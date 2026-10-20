#!/usr/bin/env python3
"""Bounded synthetic carving and deleted-file recovery in a restricted container.

No evidence, host devices, network, elevated capabilities, package installs or models.
Use an explicit candidate tag and a new empty local output directory.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import uuid
import zlib


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def synthetic_png():
    """A complete, deterministic 128x128 RGB PNG; no external asset or executor."""
    def chunk(kind, payload):
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload))
    pixels = b''.join(b'\0' + bytes((x * 17 + y * 31 + channel * 67) % 256
                                  for x in range(128) for channel in range(3)) for y in range(128))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 128, 128, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b''))


def inside():
    assert os.getuid() != 0, 'Synthetic smoke must run as a non-root user'
    root = Path('/work')
    commands = []
    def run(command, *, allowed=(0,), timeout=45):
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        commands.append({'argv': command, 'exit_code': result.returncode,
                         'stdout': result.stdout, 'stderr': result.stderr})
        (root / 'commands.json').write_text(json.dumps(commands, indent=2)+'\n')
        if result.returncode not in allowed:
            raise RuntimeError(f'{command[0]} exited {result.returncode}: {result.stderr}')
        return result.stdout + result.stderr

    # Inspect installed CLI help before choosing options; retain actual format lists.
    run(['photorec', '/help'])  # /cmd is documented upstream but omitted from local help.
    run(['tsk_recover'], allowed=(0, 1))
    run(['img_stat', '-i', 'list'], allowed=(0, 1))
    image_types = commands[-1]['stdout'] + commands[-1]['stderr']
    run(['fsstat', '-f', 'list'], allowed=(0, 1))
    filesystem_types = commands[-1]['stdout'] + commands[-1]['stderr']
    run(['mke2fs', '-h'], allowed=(0, 1))
    run(['debugfs', '-R', 'help'])
    versions = {'photorec': run(['photorec', '/version']).strip(),
                'sleuthkit': run(['tsk_recover', '-V']).strip()}
    raw = root / 'synthetic-raw.dd'
    png = root / 'synthetic-marker.png'
    png.write_bytes(synthetic_png())
    offset = 4096
    with raw.open('wb') as stream:
        stream.truncate(8 * 1024 * 1024)
        stream.seek(offset)
        stream.write(png.read_bytes())
    raw.chmod(0o444)
    raw_hash = sha256(raw)
    png_hash = sha256(png)

    def carve(source, prefix, *, permit_unsupported_ewf=False):
        source_hash = sha256(source)
        output = root / prefix
        run(['photorec', '/log', '/logname', str(root / (prefix+'.log')), '/d', str(output),
             '/cmd', str(source), 'partition_none,fileopt,everything,disable,png,enable,wholespace,search'],
            allowed=(0, 1) if permit_unsupported_ewf else (0,))
        if commands[-1]['exit_code'] != 0:
            assert permit_unsupported_ewf and 'ewf lib: none' in versions['photorec']
            assert sha256(source) == source_hash
            return {'status': 'unsupported', 'reason': 'Installed PhotoRec reports ewf lib: none; actual E01 open failed.',
                    'source_sha256_before': source_hash, 'source_sha256_after': sha256(source)}
        candidates = sorted(root.glob(prefix+'.*/*.png'))
        exact = [path for path in candidates if sha256(path) == png_hash]
        assert exact, f'PhotoRec did not recover the exact synthetic PNG from {source.name}'
        assert sha256(source) == source_hash, 'PhotoRec modified its input'
        return {'status': 'passed', 'source': source.name, 'source_sha256_before': source_hash,
                'source_sha256_after': sha256(source), 'expected_embedded_byte_offset': offset,
                'expected_png_sha256': png_hash,
                'recovered': [{'path': str(path.relative_to(root)), 'bytes': path.stat().st_size,
                               'sha256': sha256(path)} for path in exact],
                'scope': 'PNG only, whole synthetic image; names/paths are derived recovery output'}

    raw_result = carve(raw, 'raw-carved')
    ext = root / 'synthetic-ext2.dd'
    with ext.open('wb') as stream:
        stream.truncate(16 * 1024 * 1024)
    run(['mke2fs', '-q', '-t', 'ext2', '-F', '-b', '1024', '-E', 'lazy_itable_init=0', str(ext)])
    marker = root / 'deleted-marker.bin'
    marker.write_bytes(b'Forsic synthetic deleted-file marker; not evidence.\n' * 64)
    allocated = root / 'allocated-control.bin'
    allocated.write_bytes(b'Synthetic allocated control; must not appear in default unallocated recovery.\n' * 16)
    run(['debugfs', '-w', '-R', f'write {marker} /deleted-marker.bin', str(ext)])
    run(['debugfs', '-w', '-R', f'write {allocated} /allocated-control.bin', str(ext)])
    deleted_stat = run(['debugfs', '-R', 'stat /deleted-marker.bin', str(ext)])
    assert 'Inode:' in deleted_stat
    run(['debugfs', '-w', '-R', 'rm /deleted-marker.bin', str(ext)])
    ext.chmod(0o444)
    ext_hash = sha256(ext)
    destination = root / 'ext2-recovered'
    run(['tsk_recover', '-i', 'raw', '-f', 'ext2', str(ext), str(destination)])
    recovered = [path for path in destination.rglob('*') if path.is_file()]
    exact = [path for path in recovered if sha256(path) == sha256(marker)]
    assert exact, 'TSK did not recover the exact deleted synthetic marker'
    assert not any(sha256(path) == sha256(allocated) for path in recovered), 'Allocated control was unexpectedly exported'
    assert sha256(ext) == ext_hash, 'TSK modified its input'
    ext_result = {'source': ext.name, 'source_sha256_before': ext_hash, 'source_sha256_after': sha256(ext),
                  'deleted_inode_before_unlink': deleted_stat, 'expected_sha256': sha256(marker),
                  'allocated_control_excluded': True,
                  'recovered': [{'path': str(path.relative_to(root)), 'sha256': sha256(path)} for path in exact]}
    ewf_result = {'status': 'unavailable', 'reason': 'ewfacquire is not installed'}
    if shutil.which('ewfacquire'):
        run(['ewfacquire', '-h'])
        run(['ewfacquire', '-u', '-t', str(root / 'synthetic-ewf'), str(raw)], timeout=60)
        segments = sorted(root.glob('synthetic-ewf.E*'))
        assert segments, 'ewfacquire did not produce an EWF segment'
        for segment in segments:
            segment.chmod(0o444)
        segment_hashes = {segment.name: sha256(segment) for segment in segments}
        ewf_result = carve(segments[0], 'ewf-carved', permit_unsupported_ewf=True)
        assert {segment.name: sha256(segment) for segment in segments} == segment_hashes
        ewf_result['all_segment_hashes_preserved'] = segment_hashes
        if ewf_result['status'] == 'unsupported' and shutil.which('ewfexport'):
            run(['ewfexport', '-h'])
            run(['ewfexport', '-u', '-f', 'raw', '-t', str(root / 'ewf-export'), str(segments[0])], timeout=60)
            exported = root / 'ewf-export.raw'
            assert exported.is_file() and sha256(exported) == raw_hash, 'EWF raw export differs from synthetic acquisition input'
            exported.chmod(0o444)
            ewf_result['raw_export_path'] = carve(exported, 'ewf-export-carved')
            assert {segment.name: sha256(segment) for segment in segments} == segment_hashes
    assert sha256(raw) == raw_hash, 'Input raw image changed during EWF acquisition'
    packages = run(['dpkg-query', '-W', '-f=${Package}\t${Version}\n'])
    (root / 'packages.tsv').write_text(packages)
    notices = {}
    (root / 'licenses').mkdir()
    for package in ('sleuthkit', 'testdisk', 'ewf-tools'):
        source = Path('/usr/share/doc') / package / 'copyright'
        destination = root / 'licenses' / (package+'-copyright')
        shutil.copyfile(source, destination)
        notices[package] = {'path': str(destination.relative_to(root)), 'sha256': sha256(destination)}
    record = {'status': 'passed', 'synthetic_only': True, 'uid': os.getuid(), 'versions': versions,
              'package_copyrights': notices, 'supported_image_types': image_types, 'supported_filesystem_types': filesystem_types,
              'photorec_raw': raw_result, 'tsk_ext2_deleted': ext_result, 'photorec_ewf': ewf_result,
              'limitations': ['Small synthetic recovery only; no actual case or autonomous model investigation.',
                             'Format listing does not prove deleted-file recovery for every filesystem.',
                             'No XFS deleted-file recovery is claimed.',
                             'Carved data and recovered names are derived output, not original path or execution proof.']}
    (root / 'container-verification.json').write_text(json.dumps(record, indent=2)+'\n')
    print(json.dumps(record))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--inside', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.inside:
        inside()
        return
    if not args.image or not args.output:
        parser.error('--image and --output are required')
    if os.getuid() == 0:
        parser.error('Use the non-root analysis-shell operator')
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error('Output directory must be new or empty')
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    image = json.loads(subprocess.check_output(['docker', 'image', 'inspect', args.image], text=True, timeout=15))[0]
    name = 'forsic-recovery-smoke-'+uuid.uuid4().hex
    command = ['docker', 'run', '--rm', '--pull=never', '--name', name,
               '--init', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
               '--security-opt', 'no-new-privileges', '--user', f'{os.getuid()}:{os.getgid()}',
               '--memory', '8g', '--memory-swap', '8g', '--cpus', '2', '--pids-limit', '64',
               '--ulimit', 'fsize=268435456:268435456', '--tmpfs', '/tmp:rw,nosuid,nodev,size=256m',
               '--env', 'HOME=/work', '--env', 'TMPDIR=/tmp', '--workdir', '/work',
               '--mount', f'type=bind,src={output},dst=/work',
               '--mount', f'type=bind,src={Path(__file__).resolve()},dst=/opt/smoke_recovery_runtime.py,readonly,bind-propagation=rprivate,bind-recursive=readonly',
               image['Id'], 'python', '-B', '/opt/smoke_recovery_runtime.py', '--inside']
    receipt = {'image_tag': args.image, 'image_id': image['Id'], 'image_size_bytes': image['Size'], 'command': command}
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=240)
        receipt.update(exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr)
    except subprocess.TimeoutExpired as exc:
        receipt.update(exit_code=124, timed_out=True,
                       stdout=(exc.stdout or b'').decode(errors='replace') if isinstance(exc.stdout, bytes) else exc.stdout or '',
                       stderr=(exc.stderr or b'').decode(errors='replace') if isinstance(exc.stderr, bytes) else exc.stderr or '')
    except OSError as exc:
        receipt.update(exit_code=1, stdout='', stderr=str(exc))
    finally:
        try:
            cleanup = subprocess.run(['docker', 'rm', '-f', name], capture_output=True, text=True, timeout=15)
            receipt['cleanup'] = {'exit_code': cleanup.returncode, 'stdout': cleanup.stdout, 'stderr': cleanup.stderr}
        except (OSError, subprocess.TimeoutExpired) as exc:
            receipt['cleanup'] = {'error': str(exc)}
    (output / 'runtime-smoke.json').write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps({key: receipt[key] for key in ('image_tag', 'image_id', 'image_size_bytes', 'exit_code')}))
    if receipt['exit_code']:
        print(receipt['stderr'], file=sys.stderr)
        raise SystemExit(receipt['exit_code'])


if __name__ == '__main__':
    main()
