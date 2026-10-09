"""Small, offline Linux deployment helper. Never installs, calls a model or restarts.

Use the upstream PM for dependencies. Init is for an EMPTY state directory only;
source bundles contain neither operational configuration nor investigation data.
"""
import argparse
from contextlib import closing
import hashlib
import gzip
import importlib.metadata
import io
import ipaddress
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
from urllib.parse import urlsplit

PIN = 'bafb42b431fca313471958fdf77f9c30e455f86b'
DEPENDENCIES = {'python-docx': '1.2.0', 'lxml': '6.1.3', 'python-telegram-bot': '22.8'}
ROOT_FILES = ('AGENTS.md', '.hermes.md', 'run.py', 'telegram_mirror.py', 'telegram_mentions.py', 'telegram_reports.py',
              'telegram_actions.py', 'deploy_forsic.py',
              'smoke.py', 'library_smoke.py', 'requirements-forsic.txt', 'Dockerfile.tools',
              'UPSTREAM.txt', 'README.txt', 'DEPLOYMENT.txt', 'DESIGN.txt', 'HARNESS_REVIEW.txt', 'test_deployment.py',
              'test_evidence.py', 'test_workflow.py', 'test_case_library.py', 'test_intake.py',
              'test_improvements.py', 'test_investigations.py', 'test_telegram_mirror.py',
              'test_telegram_mentions.py', 'test_telegram_reports.py', 'test_telegram_actions.py',
              'test_report_driven.py', 'test_timeline_recovery.py',
              'test_connections.py', 'test_note_revision_recovery.py', 'test_harness_guidance.py', 'test_harness_context.py', 'test_note_model_view.py',
              'test_report_review_projection.py', 'test_reporting_pages.py', 'test_dashboard.cjs',
              'test_investigation_state.py', 'test_investigation_context.py', 'test_mission_native_tools.py',
              'test_native_goal_evaluation.py', 'test_current_judgment.py',
              'test_indicators.py', 'test_search_resume.py', 'test_log_timeline.py', 'test_indicator_integration.py')
PUBLIC_FILES = ('README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.txt', 'SECURITY.md',
                'NOTICE', 'NOTICE.txt', '.gitignore', '.dockerignore')
REQUIRED_PUBLIC = ('README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.txt', '.gitignore', '.dockerignore')
CONFIG_FILES = ('SOUL.md', 'AGENTS.md', 'skin.yaml', 'forsic-theme.yaml', 'config.example.yaml')
FIXTURES = ('notes.txt', 'cron.log', 'change.txt')
UPSTREAM_ADDITIONS = (
    'tests/hermes_cli/test_custom_banner_branding.py', 'tests/tui_gateway/test_goal_display.py',
    'ui-tui/src/__tests__/sessionGreeting.test.ts', 'ui-tui/src/app/sessionGreeting.ts',
    'web/public/assets/forsic-lens.svg', 'web/src/plugins/slots.test.tsx',
    'tests/hermes_cli/test_terminal_theme.py', 'web/src/i18n/context.test.ts',
    'web/src/forsic-shell.css', 'web/src/App.test.tsx', 'web/src/lib/analysis-title.ts',
    'tests/agent/test_plugin_compaction_projection.py',
)


def private_url(value):
    url = urlsplit(value)
    if (url.scheme not in ('http', 'https') or not url.hostname or url.username or
            url.password or url.query or url.fragment or url.path.rstrip('/') != '/v1'):
        raise ValueError('Use an http(s) Ollama /v1 URL without credentials, query or fragment')
    try:
        address = ipaddress.ip_address(url.hostname)
        trusted = (address.is_private or address.is_loopback) and not (address.is_unspecified or address.is_multicast)
    except ValueError:
        trusted = url.hostname == 'localhost'
    if not trusted:
        raise ValueError('This template supports localhost or an explicitly chosen private IP')
    return value.rstrip('/')


def initial_config(base_url, model):
    if not model.strip() or any(char in model for char in '\r\n\0'):
        raise ValueError('Provide a model name without control characters')
    local = {'provider': 'custom', 'model': model, 'base_url': private_url(base_url), 'api_key': 'ollama'}
    return {
        '_config_version': 49,
        'model': {'provider': 'custom', 'default': model, 'base_url': local['base_url'],
                  'context_length': 65536, 'ollama_num_ctx': 65536},
        'fallback_model': {},
        'providers': {'custom': {'request_timeout_seconds': 900, 'stale_timeout_seconds': 900}},
        'tools': {'tool_search': {'enabled': 'off'}}, 'reasoning': {'enabled': False},
        'auxiliary': {'report_review': {'timeout': 900},
                      'goal_judge': {**local, 'timeout': 120, 'max_tokens': 1024},
                      'title': dict(local), 'compression': dict(local)},
        'platform_toolsets': {name: ['forsic'] for name in ('cli', 'api', 'web', 'desktop', 'telegram')},
        'agent': {'max_turns': 20, 'api_max_retries': 1, 'auto_recovery_cycles': 0,
                  'run_budget_seconds': 3600, 'local_stream_stale_timeout': 900},
        'memory': {'memory_enabled': False, 'user_profile_enabled': False, 'nudge_interval': 0},
        'curator': {'enabled': False}, 'background_review': {'enabled': False},
        'security': {'allow_lazy_installs': False}, 'updates': {'check': False},
        'dashboard': {'theme': 'forsic'},
        'display': {'skin': 'forsic', 'language': 'ko', 'tui_compact': True,
                    'new_session_prompt': '포렌식 조사 동료 포식이로서 자연스럽게 짧게 인사하고 분석할 파일 또는 폴더의 전체 경로를 물어봐 주세요. 아직 조사한 것은 없어요. 도구를 호출하지 말고 두 문장 이내로 말하세요.'},
        'plugins': {'enabled': ['forsic'], 'entries': {'forsic': {'enabled': True, 'settings': {}}}},
    }


def write_new(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('xb') as stream:
        os.chmod(path, 0o600)
        stream.write(data.encode() if isinstance(data, str) else data)


def unit_text(root, python):
    def quote(value):
        if any(char in str(value) for char in '\n\r\0$%'):
            raise ValueError('Service paths must not contain control characters, $ or %')
        return json.dumps(str(value))
    return ('[Unit]\nDescription=Forsic local investigation workspace\nAfter=network-online.target\n\n'
            '[Service]\nType=simple\nWorkingDirectory=' + quote(root) + '\n'
            'Environment=PYTHONDONTWRITEBYTECODE=1\nExecStart=' + quote(python) + ' ' + quote(root / 'run.py') + '\n'
            'UMask=0077\nRestart=no\n\n[Install]\nWantedBy=default.target\n')


def initialize(root, base_url, model, python):
    root = root.resolve()
    state = root / 'state'
    if state.exists() or state.is_symlink():
        raise ValueError('Existing state is never overwritten. Upgrade in place only after safe backup.')
    required = [root / 'run.py', root / 'forsic_plugin/plugin.yaml', root / 'upstream/hermes/pm/lock.json']
    required += [root / 'config' / name for name in CONFIG_FILES]
    required += [root / 'cases/smoke/evidence' / name for name in FIXTURES]
    if not all(path.is_file() for path in required):
        raise ValueError('Incomplete source tree; inspect the source bundle manifest first')
    config = initial_config(base_url, model)
    service = unit_text(root, python)  # Validate before writing anything.
    manifest = root / 'state/synthetic/case.json'
    config['plugins']['entries']['forsic']['settings'] = {
        'case_file': str(manifest), 'synthetic_roots': [str(root / 'cases/smoke/evidence')],
    }
    state.mkdir(mode=0o700)
    home = root / 'state/hermes'
    write_new(home / 'config.yaml', json.dumps(config, ensure_ascii=False, indent=2) + '\n')
    write_new(manifest, json.dumps({'case_id': 'forsic-synthetic-smoke', 'synthetic': True,
              'label': '합성 예약 작업 검증', 'evidence_kind': 'exported-text-fixture',
              'evidence_root': str(root / 'cases/smoke/evidence'),
              'output_root': str(root / 'state/synthetic/results'),
              'question': '예약 작업 시도·성공·승인을 구별한다.', 'scope': '무해 합성 3파일. 실제 사건 아님.'},
              ensure_ascii=False, indent=2) + '\n')
    for destination, source in [('plugins/forsic', 'forsic_plugin'), ('SOUL.md', 'config/SOUL.md'),
                                ('skins/forsic.yaml', 'config/skin.yaml'),
                                ('dashboard-themes/forsic.yaml', 'config/forsic-theme.yaml')]:
        target = home / destination
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.symlink_to(os.path.relpath(root / source, target.parent))
    write_new(root / 'state/deployment/forsic-web.service', service)
    return {'initialized': True, 'model_requests': 0, 'service_started': False,
            'home': str(home), 'next': 'Follow DEPLOYMENT.txt: prepare PM dependencies, build, doctor, then launch.'}


def idle_state(root):
    """Conservative observation, NOT a lock or permission to kill an active service."""
    result = {'active_goals': 0, 'turn_leases': 0, 'verification_jobs': 0, 'unknown': []}
    dbpath = root / 'state/hermes/state.db'
    if dbpath.exists():
        try:
            with closing(sqlite3.connect(dbpath.resolve().as_uri() + '?mode=ro', uri=True)) as db:
                db.execute('PRAGMA query_only=ON')
                result['turn_leases'] = db.execute('SELECT COUNT(*) FROM session_turn_leases').fetchone()[0]
                rows = db.execute("SELECT value FROM state_meta WHERE key LIKE 'goal:%'").fetchall()
                result['active_goals'] = sum(json.loads(row[0]).get('status') == 'active' for row in rows)
        except (sqlite3.Error, ValueError, TypeError, AttributeError):
            result['unknown'].append('native_state_unreadable_or_unsupported')
    for path in (root / 'state/intake/cases').glob('*/results/verify-*.json'):
        try:
            if json.loads(path.read_text()).get('status') in ('running', 'queued'):
                result['verification_jobs'] += 1
        except (OSError, ValueError):
            result['unknown'].append('verification_receipt_unreadable')
    result['observed_idle'] = not any(result.values())
    result['meaning'] = 'Read-only snapshot; pause goals and prevent new input before rechecking/stopping. No automatic stop or recovery.'
    return result


def doctor(root):
    """Metadata/file checks only. No model, Telegram, network or Docker daemon calls."""
    checks = []
    def add(name, ok, detail):
        checks.append({'check': name, 'ok': bool(ok), 'detail': detail})
    for name in ('run.py', 'forsic_plugin/plugin.yaml', 'upstream/hermes/pm/lock.json',
                 'upstream/hermes/uv.lock', 'state/hermes/config.yaml',
                 'upstream/hermes/ui-tui/dist/entry.js', 'upstream/hermes/hermes_cli/web_dist/index.html'):
        add(name, (root / name).is_file(), 'present' if (root / name).is_file() else 'missing')
    for name, version in DEPENDENCIES.items():
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            actual = 'missing'
        add('dependency:' + name, actual == version, actual + ' (expected ' + version + ')')
    for executable in ('docker', 'ewfinfo', 'ewfverify'):
        add('executable:' + executable, shutil.which(executable), 'available' if shutil.which(executable) else 'missing')
    for name in ('state/hermes/config.yaml', 'state/hermes/.env'):
        path = root / name
        if path.exists():
            add('private_mode:' + name, not path.stat().st_mode & 0o077, 'owner-only required')
    home = root / 'state/hermes'
    for name, expected in [('plugins/forsic', root / 'forsic_plugin'), ('SOUL.md', root / 'config/SOUL.md')]:
        add('link:' + name, (home / name).exists() and (home / name).resolve() == expected.resolve(), 'local source binding')
    try:
        text = (home / 'config.yaml').read_text()
        try:
            config = json.loads(text)
        except ValueError:
            from ruamel.yaml import YAML
            config = YAML(typ='safe').load(text)
        model = config.get('model', {})
        private_url(model.get('base_url', ''))
        add('private_model_config', model.get('provider') == 'custom' and bool(model.get('default')),
            'private endpoint; no request made')
        add('fallback_disabled', not config.get('fallback_model'), 'no automatic external fallback')
        add('forsic_only_tools', all(config.get('platform_toolsets', {}).get(name) == ['forsic']
            for name in ('cli', 'web', 'telegram')), 'CLI/web/Telegram use only Forsic toolset')
        for name, role in config.get('auxiliary', {}).items():
            if isinstance(role, dict) and role.get('base_url'):
                add('auxiliary_local:' + name, role.get('base_url') == model.get('base_url') and
                    role.get('provider') == 'custom', 'same private connection')
    except Exception:
        # YAML/parser diagnostics can echo secret-bearing source lines. Report only
        # that configuration could not be checked, never its exception text.
        add('private_model_config', False, 'cannot verify configuration with this Python; no values printed')
    return {'ready_for_manual_preflight': all(row['ok'] for row in checks), 'checks': checks,
            'idle': idle_state(root), 'python': sys.executable, 'network_requests': 0, 'model_requests': 0,
            'not_verified': ['Ollama connectivity/model identity', 'Docker permission/parser image',
                             'PM selected-environment freshness', 'frontend freshness/browser behavior',
                             'Telegram membership/mentions', 'forensic semantic accuracy/full E2E']}


def checked_source(root, rel):
    if rel.is_absolute() or '..' in rel.parts or not rel.parts:
        raise ValueError('Invalid source path: ' + str(rel))
    if any(part in ('state', 'node_modules', '__pycache__', '.git', '.env') for part in rel.parts):
        raise ValueError('Unexpected operational path in source allowlist: ' + str(rel))
    path = root / rel
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Missing source or source escapes checkout: ' + str(rel))
    return path


def upstream_omission(name, changed):
    """Remove distribution-only trees, not the web/TUI's shared dependency graph."""
    path = Path(name)
    if name in changed or path.name in ('LICENSE', 'LICENSE.md', 'NOTICE', 'NOTICE.txt'):
        return ''
    if name.startswith('website/') and name != 'website/static/api/model-catalog.json':
        return 'Docusaurus documentation/site assets; runtime model catalog retained'
    if path.parts[0] in ('evals', 'contributors', '.github'):
        return 'Upstream benchmarks, contributor media or upstream-only CI; not a Forsic runtime input'
    if len(path.parts) > 2 and path.parts[0] == 'plugins' and path.parts[2] == 'docs':
        return 'Plugin documentation/screenshots; plugin runtime and manifests retained'
    if name.startswith('apps/') and not name.startswith('apps/shared/') and path.name != 'package.json':
        return 'Standalone desktop/installer product; shared sources and npm workspace manifests retained'
    if name.startswith('tests/'):
        support = name.startswith('tests/_fixtures/') or path.name == 'conftest.py'
        support |= name in ('tests/__init__.py', 'tests/git_safety.py', 'tests/home_io_guard.py')
        if not support:
            return 'Unmodified upstream regression; run the complete suite in the pinned upstream checkout'
    return ''


def release_plan(root):
    """A clean export can reproduce itself without inheriting any Git repository."""
    upstream = root / 'upstream/hermes'
    prior = root / 'source-manifest.json'
    if not (upstream / '.git').exists() and prior.is_file():
        manifest = json.loads(prior.read_text())
        if manifest.get('schema') != 'forsic.source-release.v2' or manifest.get('upstream_commit') != PIN:
            raise ValueError('Unsupported source manifest or upstream pin')
        for name, expected in manifest['files'].items():
            path = checked_source(root, Path(name))
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError('Exported source changed: ' + name)
            if manifest['modes'][name] not in (0o644, 0o755):
                raise ValueError('Invalid source mode: ' + name)
        return manifest

    files = {Path(name) for name in (*ROOT_FILES, *REQUIRED_PUBLIC)}
    files.update(Path(name) for name in PUBLIC_FILES if (root / name).is_file())
    files.update(Path('config') / name for name in CONFIG_FILES)
    files.update(Path('cases/smoke/evidence') / name for name in FIXTURES)
    files.update(path.relative_to(root) for path in (root / 'forsic_plugin').rglob('*')
                 if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc')
    commit = subprocess.check_output(['git', '-C', str(upstream), 'rev-parse', 'HEAD'], text=True).strip()
    if commit != PIN:
        raise ValueError('Upstream commit differs from the reviewed pin')
    tracked = subprocess.check_output(['git', '-C', str(upstream), 'ls-files', '-z']).decode().split('\0')
    changed = set(subprocess.check_output(
        ['git', '-C', str(upstream), 'diff', '--name-only', '-z', PIN, '--'],
    ).decode().split('\0')) - {''}
    additions = {name for name in UPSTREAM_ADDITIONS if (upstream / name).is_file()}
    changed.update(additions)
    omitted, deleted = {}, []
    for name in sorted((set(tracked) | additions) - {''}):
        reason = upstream_omission(name, changed)
        if reason:
            omitted[name] = reason
        elif not (upstream / name).is_file() and name in changed:
            deleted.append(name)
        else:
            files.add(Path('upstream/hermes') / name)
    manifest = {'schema': 'forsic.source-release.v2', 'upstream_commit': PIN,
                'profile': 'forsic-linux-web-source', 'files': {}, 'modes': {},
                'contains': 'source + pinned upstream/local deltas + synthetic fixtures; NOT a ready runtime',
                'excluded': ['state', 'case evidence', 'secrets', 'operational config/service', 'Git history', 'installed dependencies'],
                'upstream_omitted': omitted, 'upstream_deleted_by_fork': deleted,
                'upstream_changed_files': {},
                'upstream_test_policy': 'Changed product regressions and shared fixtures retained; complete upstream suite requires the original pinned checkout.'}
    for rel in sorted(files):
        path = checked_source(root, rel)
        name = rel.as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest['files'][name] = digest
        manifest['modes'][name] = 0o755 if path.stat().st_mode & 0o111 else 0o644
        if rel.parts[:2] == ('upstream', 'hermes') and '/'.join(rel.parts[2:]) in changed:
            manifest['upstream_changed_files']['/'.join(rel.parts[2:])] = digest
    return manifest


def source_files(root):
    return [Path(name) for name in release_plan(root)['files']]


def release_items(root, manifest):
    for name, expected in manifest['files'].items():
        data = checked_source(root, Path(name)).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('Source changed while exporting: ' + name)
        yield name, data, manifest['modes'][name]
    yield 'source-manifest.json', (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode(), 0o644


def export_source(root, output):
    if output.exists() or output.is_symlink():
        raise ValueError('Output exists; choose a new export directory')
    if output.resolve().is_relative_to(root.resolve()):
        raise ValueError('Export outside the operational checkout')
    manifest = release_plan(root)
    output.mkdir(parents=True, mode=0o700)
    for name, data, mode in release_items(root, manifest):
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(data)
        target.chmod(mode)
    return {'directory': str(output), 'files': len(manifest['files']),
            'upstream_omitted_files': len(manifest['upstream_omitted']),
            'manifest_sha256': hashlib.sha256((output / 'source-manifest.json').read_bytes()).hexdigest(),
            'uploaded': False, 'service_started': False}


def bundle(root, output):
    if output.exists():
        raise ValueError('Output exists; choose a new release filename')
    manifest = release_plan(root)
    with output.open('xb') as raw:
        os.chmod(output, 0o600)
        with gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w') as archive:
                for name, data, mode in release_items(root, manifest):
                    entry = tarfile.TarInfo('forsic/' + name)
                    entry.size, entry.mode, entry.mtime = len(data), mode, 0
                    archive.addfile(entry, io.BytesIO(data))
    return {'file': str(output), 'files': len(manifest['files']), 'bytes': output.stat().st_size,
            'sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'uploaded': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('doctor')
    sub.add_parser('idle')
    init = sub.add_parser('init')
    init.add_argument('--ollama-url', default='http://127.0.0.1:11434/v1')
    init.add_argument('--model', default='qwen3.8:latest')
    init.add_argument('--python', type=Path, default=Path(sys.executable))
    release = sub.add_parser('bundle')
    release.add_argument('output', type=Path)
    public = sub.add_parser('export')
    public.add_argument('output', type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        if args.action == 'init':
            result = initialize(root, args.ollama_url, args.model, args.python.resolve())
        elif args.action == 'bundle':
            result = bundle(root, args.output.resolve())
        elif args.action == 'export':
            result = export_source(root, args.output.resolve())
        elif args.action == 'idle':
            result = idle_state(root)
        else:
            result = doctor(root)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(2, type(exc).__name__ + ': ' + str(exc) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int((args.action == 'doctor' and not result['ready_for_manual_preflight']) or
               (args.action == 'idle' and not result['observed_idle']))


if __name__ == '__main__':
    raise SystemExit(main())
