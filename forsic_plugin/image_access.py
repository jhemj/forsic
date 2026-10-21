"""Container CLI for read-only image access and bounded derived-file export."""
import argparse
from contextlib import contextmanager, ExitStack
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

if __package__:
    from .image_reader import archive_origin, image_file_identity, image_segments, inspect, open_image
else:
    from image_reader import archive_origin, image_file_identity, image_segments, inspect, open_image


EXPORT_LIMIT = 256 * 1024 * 1024


class ExportError(ValueError):
    def __init__(self, message, details):
        super().__init__(message)
        self.details = details


def evidence_image(image, root=Path('/evidence')):
    """CLI-only boundary; the host image parser retains its existing paths."""
    path = Path(image)
    if not path.is_absolute() or path.is_symlink():
        raise ValueError('Image must be a regular E01 path under /evidence; symlinks are unsupported')
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root.resolve(strict=True)) or not resolved.is_file():
        raise ValueError('Image must be a regular E01 path under /evidence')
    return resolved


def destination_path(destination, work_root=Path('/work')):
    """Anchor relative exports to /work without resolving through symlinks."""
    raw = os.fspath(destination)
    if not isinstance(raw, str) or not raw.strip() or '\x00' in raw:
        raise ValueError('Destination must name a new file under /work')
    path, root = Path(raw), Path(work_root)
    if '..' in path.parts or not root.is_absolute():
        raise ValueError('Destination must be a new path under /work; parent traversal is unsupported')
    if not path.is_absolute():
        path = root / path
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise ValueError('Destination must be a new path under /work') from None
    if not relative.parts:
        raise ValueError('Destination must name a new file under /work')
    return path


@contextmanager
def new_destination(destination, work_root=Path('/work')):
    """Create exclusively through pinned directories without following symlinks.

    Parents must already exist. On failure unlink only our inode in its pinned
    parent; a preexisting or replacement file must never be removed.
    """
    path, root = destination_path(destination, work_root), Path(work_root)
    relative = path.relative_to(root)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with ExitStack() as stack:
        parent = os.open(root, flags)
        stack.callback(os.close, parent)
        for part in relative.parts[:-1]:
            parent = os.open(part, flags, dir_fd=parent)
            stack.callback(os.close, parent)
        leaf = relative.parts[-1]
        fd = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        identity = os.fstat(fd)
        try:
            with os.fdopen(fd, 'wb', buffering=0) as output:
                yield output
        except BaseException:
            try:
                current = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
                if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                    os.unlink(leaf, dir_fd=parent)
            except FileNotFoundError:
                pass
            raise


def export_file(image, args, *, work_root=Path('/work')):
    """Stream one allocated regular file; exported bytes remain derived output."""
    file_path = args.get('file_path')
    if not isinstance(file_path, str) or not file_path:
        raise ValueError('export requires an explicit --file-path inside the image')
    destination = args.get('destination')
    if not isinstance(destination, str) or not destination:
        raise ValueError('export requires --destination NEW_FILE or /work/NEW_FILE')
    limit = args.get('max_bytes', EXPORT_LIMIT)
    if type(limit) is not int or not 0 < limit <= EXPORT_LIMIT:
        raise ValueError(f'export max_bytes must be an integer from 1 to {EXPORT_LIMIT}')
    digest, count = hashlib.sha256(), 0
    try:
        destination = str(destination_path(destination, work_root))
    except ValueError as exc:
        raise ExportError(str(exc), {'destination': destination, 'derived': True, 'bytes': 0,
                                    'sha256': digest.hexdigest(), 'hash_scope': 'exported_bytes',
                                    'complete': False, 'complete_file': False}) from exc
    with ExitStack() as image_stack:
        opened = image_stack.enter_context(open_image(image))
        volume, fs = opened.filesystem(args.get('volume_offset'))
        node = fs.get(file_path)
        origin = archive_origin(opened.parts, volume.offset)
        before = image_file_identity(node.lstat())
        result = {'source': {'image': str(opened.path), 'volume_offset': volume.offset, 'file_path': file_path},
                  'image': str(opened.path), 'volume_offset': volume.offset, 'file_path': file_path,
                  'destination': destination, 'derived': True, 'bytes': 0,
                  'sha256': digest.hexdigest(), 'hash_scope': 'exported_bytes',
                  'complete': False, 'complete_file': False}
        try:
            if not stat.S_ISREG(before['st_mode']):
                raise ValueError('export requires an allocated regular file; directories and symlinks are unsupported')
            if before['st_size'] > limit:
                raise ValueError(f'File exceeds export max_bytes ({limit}); no file was exported')
            with new_destination(destination, work_root) as output:
                with node.open() as stream:
                    while block := stream.read(min(1024 * 1024, limit - count + 1)):
                        if count + len(block) > limit:
                            raise ValueError('File exceeded export max_bytes while reading')
                        pending = memoryview(block)
                        while pending:
                            written = output.write(pending)
                            if not written:
                                raise OSError('Export destination stopped accepting bytes')
                            digest.update(pending[:written])
                            count += written
                            pending = pending[written:]
                if count != before['st_size'] or before != image_file_identity(node.lstat()):
                    raise ValueError('Source file changed or ended early while exporting')
                if origin != archive_origin(image_segments(opened.path), volume.offset):
                    raise ValueError('Image segments changed while exporting')
                # Complete source cleanup while destination rollback is still armed.
                image_stack.close()
            return {**result, 'bytes': count, 'sha256': digest.hexdigest(), 'complete': True, 'complete_file': True}
        except Exception as exc:
            raise ExportError(str(exc), {**result, 'bytes': count, 'sha256': digest.hexdigest()}) from exc


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def parser():
    result = Parser(description='E01 읽기 전용 접근 / read-only E01 access. Export creates one derived file under /work.',
                    epilog='Example: image_access.py /evidence/disk.e01 volumes. '
                    'list requires a directory; stat/read_bytes inspect a selected file. '
                    'Exit 0: JSON result (check completeness); 1: JSON error; --help reads no evidence.')
    result.add_argument('image', help='/evidence/.../disk.e01 (원본 읽기 전용)')
    result.add_argument('action', choices=('volumes', 'list', 'stat', 'read', 'read_bytes', 'search', 'hash', 'export'))
    result.add_argument('--file-path', help='exact path INSIDE the image / 이미지 내부 경로')
    result.add_argument('--volume-offset', type=int, help='select discovered volume offset; use volumes first')
    result.add_argument('--start-line', type=int, help='read: first line (default 1)')
    result.add_argument('--offset', type=int, help='read_bytes/list/search: byte or entry offset')
    result.add_argument('--length', type=int, help='read_bytes: bytes requested (at most 12000)')
    result.add_argument('--text', help='search: nonempty single-line literal')
    result.add_argument('--recursive', action='store_true', default=None, help='search: include descendant directories')
    result.add_argument('--cursor', type=json.loads, help='search: JSON next_cursor from previous result')
    result.add_argument('--max-files', type=int, help='search: file scan limit')
    result.add_argument('--max-bytes', type=int, help='search/export byte limit; export default and maximum 256 MiB')
    result.add_argument('--max-matches', type=int, help='search: match limit')
    result.add_argument('--max-seconds', type=float, help='search: time limit')
    result.add_argument('--destination', help='export: NEW file relative to /work or absolute /work/...; '
                        'parent must exist; no overwrite, parent traversal, or symlinks')
    return result


def main(argv=None):
    try:
        args = {key: value for key, value in vars(parser().parse_args(argv)).items() if value is not None}
        image = evidence_image(args.pop('image'))
        args['path'] = str(image)
        if args['action'] == 'export':
            result = export_file(image, args)
        else:
            result = inspect(image, args)
        code = 0
    except Exception as exc:
        result = {**getattr(exc, 'details', {}), 'error': str(exc), 'error_type': type(exc).__name__}
        if result.get('observed_kind') == 'regular_file' and result.get('recovery_arguments'):
            recovery = result['recovery_arguments']
            result['recovery_argv'] = ['python', '/opt/forsic/image_access.py', recovery['path'],
                'read_bytes', '--volume-offset', str(recovery['volume_offset']),
                '--file-path', recovery['file_path'], '--offset', '0', '--length', '4096']
        code = 1
    encoded = json.dumps(result, ensure_ascii=False)
    # Keep the completed receipt in native stderr when a caller discards stdout.
    # This is still a derived command declaration, not verified image provenance.
    if code == 0 and args['action'] == 'export' and result.get('complete_file') is True:
        print(encoded, file=sys.stderr)
    print(encoded)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
