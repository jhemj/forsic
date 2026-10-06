"""One-shot Dissect adapter. The caller runs this with a read-only filesystem and no network."""
from contextlib import ExitStack
import json
from pathlib import Path
import re
import stat
import sys
import hashlib
import base64

if __package__:
    from .literal_search import file_identity, search_entries
else:  # The isolated Docker reader mounts only these two modules, not the plugin.
    from literal_search import file_identity, search_entries


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


def inspect(image, args):
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
        raise ValueError('Supported actions are volumes, list, read, read_bytes, search, stat and hash')


if __name__ == '__main__':
    try:
        result = inspect(sys.argv[1], json.loads(sys.argv[2]))
    except Exception as exc:
        result = {'error': str(exc), 'error_type': type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False))
