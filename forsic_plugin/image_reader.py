"""One-shot Dissect adapter. The caller runs this with a read-only filesystem and no network."""
from contextlib import ExitStack
import json
from pathlib import Path
import re
import stat
import sys
import hashlib
import base64
import gzip
import tarfile
import time

if __package__:
    from .literal_search import file_identity, search_entries
else:  # The isolated Docker reader mounts only these two modules, not the plugin.
    from literal_search import file_identity, search_entries


IMAGE_ACTION_REQUIRED = {
    'volumes': (), 'list': (), 'read': ('file_path',), 'read_bytes': ('file_path',),
    'stat': ('file_path',), 'search': ('text',), 'hash': ('file_path',),
    'archive_list': ('file_path',), 'archive_read': ('file_path', 'member_index'),
}


def image_action_contract(action=None):
    """Small shared contract; optional range defaults and I/O checks stay in the reader."""
    return {'type':'object', 'required':['path','action',*IMAGE_ACTION_REQUIRED.get(action, ())],
        'properties':{
            'path':{'type':'string','minLength':1,'description':'Evidence E01 segment path from forsic_list, e.g. disk.e01. This is NOT a path inside the image.'},
            'action':{'type':'string','enum':list(IMAGE_ACTION_REQUIRED),'description':'Additional required fields: '+
                '; '.join(name+'='+','.join(fields) for name,fields in IMAGE_ACTION_REQUIRED.items() if fields)+'. list/search default to the root directory; volumes needs no internal path.'},
            'file_path':{'type':'string','minLength':1,'description':'Exact allocated path INSIDE the image. Required for '+
                ', '.join(name for name,fields in IMAGE_ACTION_REQUIRED.items() if 'file_path' in fields)+'. Explicit stat("/") is valid. Keep path set to the E01.'},
            'member_index':{'type':'integer','minimum':0,'description':'archive_read only: exact ordinal from archive_list. Duplicate member names are not interchangeable.'},
            'text':{'type':'string','minLength':1,'maxLength':512,'description':'search only: nonempty single-line literal, at most 512 characters.'}}}


def image_read_entry_schema():
    """Direct image content reads expose their shared required fields at the root."""
    from copy import deepcopy
    common=image_action_contract()['properties']
    return {'type':'object','required':['path','action','file_path'],
        'properties':{
            'path':deepcopy(common['path']),
            'action':{'type':'string','enum':['read','read_bytes']},
            'file_path':deepcopy(common['file_path']),
            'volume_offset':{'type':'integer'}, 'start_line':{'type':'integer'},
            'offset':{'type':'integer'}, 'length':{'type':'integer'},
            'reason':{'type':'string'},
            'mission_id':{'type':'string','description':'Optional exact planned mission ID. Omit both linkage fields for an unplanned read.'},
            'mission_version':{'type':'string','description':'Required with mission_id: exact current saved version. Changed ranges require a matching plan.'}},
        'additionalProperties':False}


class InvalidArguments(ValueError):
    """Admission failure, not an observation about evidence existence or contents."""
    def __init__(self, problems, contract):
        self.details={'error_code':'invalid_arguments','problems':problems,
            'missing_fields':sorted({k for p in problems for k in p.get('missing_fields',[])}),
            'schema_fingerprint':hashlib.sha256(json.dumps(contract,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
        super().__init__('Invalid tool arguments: '+json.dumps(problems,ensure_ascii=False))


def image_action_problems(args, location='arguments'):
    if not isinstance(args,dict):return [{'path':location,'expected_type':'object'}]
    action=args.get('action')
    contract=image_action_contract(action if isinstance(action,str) else None)
    problems=[]
    missing=[k for k in contract['required'] if k not in args]
    if missing:problems.append({'path':location,'missing_fields':missing})
    for key in ('path','action','file_path'):
        if key in args and (not isinstance(args[key],str) or not args[key]):
            problems.append({'path':location+'.'+key,'expected_type':'nonempty_string'})
    if isinstance(action,str) and action not in IMAGE_ACTION_REQUIRED:
        problems.append({'path':location+'.action','allowed_values':list(IMAGE_ACTION_REQUIRED)})
    if action=='archive_read' and 'member_index' in args:
        index=args['member_index']
        if type(index) is not int or index<0:
            problems.append({'path':location+'.member_index','expected_type':'integer','minimum':0})
    if action=='search' and 'text' in args:
        text=args['text']
        if not isinstance(text,str) or not text or len(text)>512 or '\n' in text or '\r' in text:
            problems.append({'path':location+'.text','expected_type':'nonempty_single_line_literal','max_length':512})
    return problems


def validate_image_action(args, location='arguments'):
    problems=image_action_problems(args,location)
    if problems:
        action=args.get('action') if isinstance(args,dict) else None
        raise InvalidArguments(problems,image_action_contract(action if isinstance(action,str) else None))


def search_node(node, args, source_identity=None):
    recursive = bool(args.get('recursive', False))

    def entries():
        queue, visited = [node], set()
        while queue:
            entry = queue.pop()
            try:
                info = entry.lstat()
            except (OSError, ValueError) as exc:
                yield str(entry.path), None, None, 'stat_' + type(exc).__name__
                continue
            if stat.S_ISDIR(info.st_mode):
                identity = (getattr(info, 'st_dev', None), info.st_ino)
                if identity in visited:
                    yield str(entry.path), None, None, 'directory_cycle'
                    continue
                visited.add(identity)
                yield str(entry.path), entry.lstat, None, 'directory'
                children = []
                try:
                    for index, child in enumerate(entry.scandir()):
                        if index >= 10000:
                            yield str(entry.path), None, None, 'directory_entry_limit; narrow the path'
                            break
                        try:
                            item = child.get()
                            if recursive or not stat.S_ISDIR(item.lstat().st_mode):
                                children.append(item)
                        except (OSError, ValueError) as exc:
                            yield str(getattr(child, 'path', entry.path)), None, None, 'entry_' + type(exc).__name__
                except (OSError, ValueError) as exc:
                    yield str(entry.path), None, None, 'directory_' + type(exc).__name__
                queue.extend(reversed(sorted(children, key=lambda item: str(item.path))))
            else:
                yield str(entry.path), entry.lstat, entry.open, None

    scope = {'path': str(node.path), 'recursive': recursive,
             'identity': file_identity(node.lstat()), 'source': source_identity}
    return {**search_entries(entries(), args, scope, 'file_path'),
            'search_path': str(node.path), 'recursive': recursive}


def hash_node(node):
    """Hash the complete allocated regular file, never a read_bytes preview."""
    before = file_identity(node.lstat())
    if not stat.S_ISREG(before['st_mode']):
        raise ValueError('Full image hash requires an allocated regular file, not a directory or symlink')
    digest, count = hashlib.sha256(), 0
    with node.open() as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
            count += len(block)
    if before != file_identity(node.lstat()) or count != before['st_size']:
        raise ValueError('File changed or ended early while hashing; no complete hash is available')
    return {'sha256': digest.hexdigest(), 'bytes': count, 'complete_file': True,
            'hash_scope': 'whole_file',
            'meaning': 'SHA-256 of this complete allocated file, not acquisition provenance or deletion history.'}


def select_volume(volumes, offset=None):
    """An omitted selector is unambiguous only when discovery found one volume."""
    if not volumes:
        raise ValueError('No volumes were discovered in this image')
    if offset is None and len(volumes) == 1:
        return volumes[0]
    volume = next((v for v in volumes if v.offset == offset), None)
    if volume is None:
        available = ', '.join(str(v.offset) for v in volumes)
        raise ValueError(f'Select volume_offset from these discovered offsets: {available}')
    return volume


class ArchiveLimit(ValueError):
    pass


class ArchiveBudget:
    """Count decompressed TAR bytes, including skipped payloads and headers."""
    def __init__(self, stream, limit, deadline):
        self.stream, self.limit, self.deadline, self.used = stream, limit, deadline, 0

    def read(self, size):
        if time.monotonic() > self.deadline or size > self.limit-self.used:
            raise ArchiveLimit('Archive scan budget reached; narrow scope or increase archive_scan_bytes within the advertised limit')
        data = self.stream.read(size)
        self.used += len(data)
        return data


def inspect_archive(node, args):
    """Inspect TAR/TAR.GZ members in memory. Never extract or follow links."""
    before = file_identity(node.lstat())
    if not stat.S_ISREG(before['st_mode']):
        raise ValueError('Archive inspection requires an allocated regular file, not a symlink')
    action = args['action']
    index = args.get('member_index')
    if action == 'archive_read' and (type(index) is not int or index < 0):
        raise ValueError('archive_read needs member_index returned by archive_list; names may be duplicated')
    offset = max(0, int(args.get('offset', 0)))
    limit = min(1024**3, max(10240, int(args.get('archive_scan_bytes', 256*1024**2))))
    result = {'archive_path': str(node.path), 'measurement': 'TAR member headers or selected regular member bytes',
              'scope_note': 'Archived records only; no extraction, execution, symlink following, deleted-file recovery or proof of activity on the host.',
              'entries': [], 'coverage_complete': False, 'scan_exhausted': False}
    with node.open() as raw:
        magic = raw.read(2); raw.seek(0)
        with ExitStack() as stack:
            source = stack.enter_context(gzip.GzipFile(fileobj=raw)) if magic == b'\x1f\x8b' else raw
            budget = ArchiveBudget(source, limit, time.monotonic()+60)
            try:
                with tarfile.open(fileobj=budget, mode='r|') as archive:
                    for number, member in enumerate(archive):
                        # TarFile caches headers; the ordinal and returned page suffice.
                        archive.members.clear()
                        kind = 'file' if member.isfile() else 'directory' if member.isdir() else 'link' if member.issym() or member.islnk() else 'special'
                        header = {'member_index': number, 'name': member.name, 'kind': kind,
                                  'bytes': member.size, 'mtime_epoch': member.mtime}
                        if action == 'archive_list':
                            if number < offset: continue
                            if len(result['entries']) == 100:
                                result['next_offset'] = number
                                break
                            result['entries'].append(header)
                        elif number == index:
                            if not member.isfile() or member.issparse():
                                raise ValueError('Only regular, non-sparse archive members can be read; links and devices are not followed')
                            if offset > member.size:
                                raise ValueError('Member byte offset exceeds its declared size')
                            with archive.extractfile(member) as stream:
                                # Streaming skip is also charged to the expanded-byte budget.
                                remaining = min(offset, member.size)
                                while remaining:
                                    block = stream.read(min(65536, remaining))
                                    if not block: raise ValueError('Archive member ended before its declared size')
                                    remaining -= len(block)
                                data = stream.read(min(12000, max(1, int(args.get('length', 4096)))))
                            end = offset+len(data)
                            result.update(member=header, byte_start=offset, byte_end=end,
                                          text=data.decode('utf-8', errors='replace'), encoding='UTF-8 replacement; exact bytes in base64',
                                          base64=base64.b64encode(data).decode(), sha256_slice=hashlib.sha256(data).hexdigest(),
                                          next_offset=end if end < member.size else None,
                                          complete_member=offset==0 and end==member.size)
                            break
                    else:
                        result.update(next_offset=None, coverage_complete=offset==0, scan_exhausted=True)
                        if action == 'archive_read':
                            raise ValueError('member_index was not present in this TAR; select a returned index')
            except ArchiveLimit as exc:
                result.update(stop_reason='scan_budget', limitation=str(exc), next_offset=None)
            result['decompressed_bytes_scanned'] = budget.used
            result['archive_scan_bytes'] = limit
    if before != file_identity(node.lstat()):
        raise ValueError('Archive changed during inspection; discard this result')
    return result


def inspect(image, args):
    validate_image_action(args)
    from dissect.target import Target
    from dissect.target.containers.ewf import EwfContainer
    from dissect.target.filesystem import open as open_filesystem

    path = Path(image)
    if path.suffix.lower() != '.e01':
        raise ValueError('This adapter currently supports classic E01 segments only')
    parts = sorted(p for p in path.parent.glob(path.stem + '.*') if re.fullmatch(r'\.E\d{2}', p.suffix, re.I))
    with ExitStack() as stack:
        handles = []
        for index, part in enumerate(parts, 1):
            if part.is_symlink():
                raise ValueError('Image segment symlinks are not supported')
            handle = stack.enter_context(part.open('rb'))
            header = handle.read(13)
            if header[:8] != b'EVF\x09\x0d\x0a\xff\x00' or int.from_bytes(header[9:11], 'little') != index:
                raise ValueError('Missing, reordered or unsupported EWF segment')
            handle.seek(0)
            handles.append(handle)
        disk = EwfContainer(handles)
        stack.callback(disk.close)
        target = Target(path)
        target.disks.add(disk)
        target.disks.apply()
        volumes = list(target.volumes)
        if args['action'] == 'volumes':
            return {'volumes': [{'offset': v.offset, 'bytes': v.size, 'name': v.name} for v in volumes], 'segments': len(parts)}
        volume = select_volume(volumes, args.get('volume_offset'))
        selected = {'volume_offset': volume.offset,
                    'volume_selection': 'only_discovered_volume' if args.get('volume_offset') is None else 'explicit'}
        fs = open_filesystem(volume)
        node = fs.get(args.get('file_path', '/'))
        if args['action'] in ('archive_list', 'archive_read'):
            source = [(str(p), file_identity(p.stat())) for p in parts]
            result = inspect_archive(node, args)
            if source != [(str(p), file_identity(p.stat())) for p in parts]:
                raise ValueError('Image segments changed during archive inspection')
            return {**selected, **result}
        if args['action'] == 'search':
            source = {'segments': [(str(p), file_identity(p.stat())) for p in parts],
                      'volume_offset': volume.offset}
            result = search_node(node, args, source)
            if source['segments'] != [(str(p), file_identity(p.stat())) for p in parts]:
                raise ValueError('Image segments changed during search')
            return {**selected, **result}
        if args['action'] == 'hash':
            source = [(str(p), file_identity(p.stat())) for p in parts]
            result = hash_node(node)
            if source != [(str(p), file_identity(p.stat())) for p in parts]:
                raise ValueError('Image segments changed during hashing; no complete hash is available')
            return {**selected, **result}
        if args['action'] == 'stat':
            info = node.lstat()
            return {**selected, 'inode': info.st_ino, 'bytes': info.st_size, 'mode': stat.filemode(info.st_mode),
                    'times': {'mtime': info.st_mtime, 'ctime': info.st_ctime, 'atime': info.st_atime},
                    'time_basis': 'filesystem epoch; ctime is not necessarily creation'}
        if args['action'] == 'read_bytes':
            offset = max(0, int(args.get('offset', 0)))
            with node.open() as stream:
                stream.seek(offset)
                raw = stream.read(min(12000, max(1, int(args.get('length', 4096)))))
            end = offset + len(raw)
            return {**selected, 'byte_start': offset, 'byte_end': end, 'base64': base64.b64encode(raw).decode(),
                    'text': raw.decode('utf-8', errors='replace'), 'sha256_slice': hashlib.sha256(raw).hexdigest(),
                    'next_offset': end if end < node.stat().st_size else None}
        if args['action'] == 'list':
            offset = max(0, int(args.get('offset', 0)))
            rows = []
            for i, entry in enumerate(node.scandir()):
                if i < offset:
                    continue
                if len(rows) == 100:
                    return {**selected, 'entries': rows, 'next_offset': offset + 100, 'complete': False}
                info = entry.get().lstat()
                rows.append({'path': entry.path, 'bytes': info.st_size, 'inode': info.st_ino, 'mode': stat.filemode(info.st_mode), 'mtime_epoch': info.st_mtime})
            return {**selected, 'entries': rows, 'next_offset': None, 'complete': True}
        if args['action'] == 'read':
            start = max(1, int(args.get('start_line', 1)))
            rows, size = [], 0
            with node.open() as stream:
                for number in range(1, start + 80):
                    raw = stream.readline(16385)
                    if not raw:
                        return {**selected, 'lines': rows, 'next_line': None, 'complete_file': start == 1, 'encoding': 'UTF-8 replacement for undecodable bytes'}
                    if b'\x00' in raw or len(raw) > 16384:
                        raise ValueError('Binary or long record requires a format-specific parser')
                    if number < start:
                        continue
                    if size + len(raw) > 24000:
                        return {**selected, 'lines': rows, 'next_line': number, 'complete_file': False}
                    rows.append({'line': number, 'text': raw.decode('utf-8', errors='replace').rstrip('\r\n')})
                    size += len(raw)
            return {**selected, 'lines': rows, 'next_line': start + len(rows), 'complete_file': False}
        raise ValueError('Supported actions are volumes, list, read, read_bytes, search, stat, hash, archive_list and archive_read')


if __name__ == '__main__':
    try:
        result = inspect(sys.argv[1], json.loads(sys.argv[2]))
    except Exception as exc:
        result = {'error': str(exc), 'error_type': type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False))
