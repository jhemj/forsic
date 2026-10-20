"""Versioned, local extension archives. Import never executes imported code."""
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import unicodedata
import uuid
import zipfile

FORMAT_VERSION = 1
MAX_FILES = 512
MAX_BYTES = 32 * 1024 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024
_ID = re.compile(r'[a-z][a-z0-9_-]{0,31}\.[a-z][a-z0-9_-]{0,31}\Z')
_NAME = re.compile(r'[a-z][a-z0-9_-]{0,47}\Z')
_VERSION = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}\Z')


def home_path(home=None):
    if home is None:
        from hermes_constants import get_hermes_home
        home = get_hermes_home()
    return Path(home).resolve()


def root_path(home=None):
    return home_path(home) / 'forsic' / 'extensions'


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def _relative(name):
    if (not isinstance(name, str) or not name or '\\' in name or '\x00' in name
            or any(part in ('', '.', '..') for part in name.split('/'))
            or PurePosixPath(name).is_absolute() or ':' in name):
        raise ValueError('Invalid archive path: ' + repr(name))
    return name


def _identity(pack_id, version):
    if not isinstance(pack_id, str) or not _ID.fullmatch(pack_id):
        raise ValueError('Pack ID must be namespaced, e.g. local.text-tools.')
    if not isinstance(version, str) or not _VERSION.fullmatch(version) or version in ('.', '..'):
        raise ValueError('Invalid pack version.')


def validate_dependencies(dependencies):
    """Use the environment manager's exact apt/hash-locked Python plan contract."""
    if not isinstance(dependencies, list) or len(dependencies) > 128:
        raise ValueError('Dependencies must be a bounded list.')
    plan = {'base_environment_id': 'validation-only', 'apt': {}, 'python': [], 'checks': []}
    seen = set()
    for item in dependencies:
        if not isinstance(item, dict) or item.get('method') not in ('apt', 'python'):
            raise ValueError('Supported dependencies are exact apt or hash-locked Python packages.')
        fields = {'method', 'name', 'version'} | ({'sha256'} if item['method'] == 'python' else set())
        if set(item) != fields or not isinstance(item.get('name'), str):
            raise ValueError('Dependency descriptor fields do not match its method.')
        identity = (item['method'], re.sub(r'[-_.]+', '-', item['name']).lower())
        if identity in seen:
            raise ValueError('Duplicate extension dependency.')
        seen.add(identity)
        if item['method'] == 'apt':
            plan['apt'][item['name']] = item['version']
        else:
            plan['python'].append({key: item[key] for key in ('name', 'version', 'sha256')})
    from .environments import _validate_plan
    _validate_plan(plan, require_checks=False)
    return dependencies


def _manifest(manifest, files):
    from .capability_policy import TOOLS, SKILLS
    if not isinstance(manifest, dict) or manifest.get('format_version') != FORMAT_VERSION:
        raise ValueError('Unsupported extension format_version.')
    _identity(manifest.get('id'), manifest.get('version'))
    for field in ('display_name', 'description'):
        if not isinstance(manifest.get(field), str) or not manifest[field] or len(manifest[field]) > 4000:
            raise ValueError('Missing or oversized ' + field)
    if manifest.get('kind') not in ('skill', 'tool', 'mixed'):
        raise ValueError('kind must be skill, tool or mixed.')
    compatibility = manifest.get('compatibility', {})
    if not isinstance(compatibility, dict) or compatibility.get('extension_api') != 1:
        raise ValueError('Unsupported extension API.')
    if not isinstance(manifest.get('source'), dict) or not manifest['source'].get('type'):
        raise ValueError('Declare source.type, including local-authored when applicable.')
    validate_dependencies(manifest.get('dependencies', []))
    declarations = manifest.get('files')
    if not isinstance(declarations, dict) or set(declarations) != set(files):
        raise ValueError('files must declare every payload file exactly once.')
    for name, data in files.items():
        if declarations[name] != _hash(data):
            raise ValueError('Payload checksum mismatch: ' + name)
    platforms = compatibility.get('platforms', [])
    if not isinstance(platforms, list) or any(item not in ('linux/amd64',) for item in platforms):
        raise ValueError('Unsupported extension platform; currently qualified: linux/amd64.')
    tools, skills = manifest.get('tools', []), manifest.get('skills', [])
    if not isinstance(tools, list) or not isinstance(skills, list) or not tools and not skills:
        raise ValueError('A pack must supply tools or skills.')
    if manifest['kind'] == 'skill' and tools or manifest['kind'] == 'tool' and skills:
        raise ValueError('Pack kind disagrees with provided entries (use mixed).')
    seen_tools, seen_skills = set(), set()
    import jsonschema
    if tools and not platforms:
        raise ValueError('Tool packs require a supported platform declaration.')
    for tool in tools:
        if not isinstance(tool, dict): raise ValueError('Tool descriptors must be objects.')
        if set(tool) & {'mounts', 'network', 'docker_options', 'host_module', 'hooks'}:
            raise ValueError('Host hooks and runtime overrides are unsupported.')
        name = tool.get('name')
        if not isinstance(name, str) or not _NAME.fullmatch(name) or name in seen_tools or name in TOOLS or 'forsic_' + name in TOOLS:
            raise ValueError('Duplicate, invalid or builtin tool name.')
        seen_tools.add(name)
        if not isinstance(tool.get('description'), str) or not tool['description'] or len(tool['description']) > 4000:
            raise ValueError('Tool description is required and bounded.')
        schema = tool.get('parameters')
        if (not isinstance(schema, dict) or schema.get('type') != 'object'
                or schema.get('additionalProperties') is not False):
            raise ValueError('Tool parameters must be a closed object schema.')
        jsonschema.Draft202012Validator.check_schema(schema)
        # Local schemas only: validation cannot fetch a remote $ref.
        def check_refs(value):
            if isinstance(value, dict):
                if '$ref' in value and not str(value['$ref']).startswith('#/'):
                    raise ValueError('Remote schema references are unsupported.')
                for item in value.values(): check_refs(item)
            elif isinstance(value, list):
                for item in value: check_refs(item)
        check_refs(schema)
        argv = tool.get('argv')
        if not isinstance(argv, list) or not 1 <= len(argv) <= 128 or not isinstance(argv[0], str) or not argv[0]:
            raise ValueError('argv must start with a literal executable.')
        for item in argv:
            if isinstance(item, str):
                if '\x00' in item or len(item) > 16384: raise ValueError('Invalid argv literal.')
            elif (not isinstance(item, dict) or set(item) != {'argument'}
                  or item['argument'] not in schema.get('properties', {})
                  or schema['properties'][item['argument']].get('type') not in ('string', 'integer', 'number', 'boolean')):
                raise ValueError('argv mappings must reference scalar schema arguments.')
        for field, default, maximum in (('timeout_seconds', 60, 900), ('max_output_bytes', 12000, 1024 * 1024)):
            value = tool.get(field, default)
            if type(value) is not int or not 1 <= value <= maximum: raise ValueError('Invalid ' + field)
        if not isinstance(tool.get('output', {}), dict) or tool.get('output', {}).get('format', 'text') not in ('text', 'json'):
            raise ValueError('Output format must be text or json.')
        for fixture in tool.get('fixtures', []):
            if not isinstance(fixture, dict): raise ValueError('Fixture descriptors must be objects.')
            if fixture.get('path') not in files or fixture.get('sha256') != declarations.get(fixture['path']):
                raise ValueError('Fixture must bind a declared payload hash.')
    for skill in skills:
        if not isinstance(skill, dict): raise ValueError('Skill descriptors must be objects.')
        name, path = skill.get('name'), skill.get('path')
        if not isinstance(name, str) or not _NAME.fullmatch(name) or name in seen_skills or name in SKILLS:
            raise ValueError('Duplicate, invalid or builtin skill name.')
        seen_skills.add(name)
        if path not in files or not path.endswith('/SKILL.md'):
            raise ValueError('Skill path must point to a declared SKILL.md.')
        from agent.skill_utils import parse_frontmatter
        metadata, _ = parse_frontmatter(files[path].decode('utf-8'))
        if metadata.get('name') != name or not metadata.get('description'):
            raise ValueError('SKILL.md name/description must match its descriptor.')
        if not isinstance(skill.get('requires_tools', []), list) or any(name not in seen_tools and name not in TOOLS for name in skill.get('requires_tools', [])):
            raise ValueError('Skill declares unknown required tools.')
    return manifest


def inspect_pack(archive):
    """Bounded ZIP validation; return manifest/content identity without extraction."""
    files, manifest_data, normalized = {}, None, set()
    archive = Path(archive)
    if archive.stat().st_size > MAX_BYTES: raise ValueError('Archive is oversized.')
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        if len(entries) > MAX_FILES: raise ValueError('Too many archive entries.')
        expanded = 0
        for entry in entries:
            name = _relative(entry.filename.rstrip('/') if entry.is_dir() else entry.filename)
            key = unicodedata.normalize('NFC', name).casefold()
            if key in normalized: raise ValueError('Duplicate or normalized archive name.')
            normalized.add(key)
            mode = entry.external_attr >> 16
            if mode and stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ValueError('Archive links and special files are forbidden.')
            if entry.flag_bits & 1: raise ValueError('Encrypted archives are unsupported.')
            expanded += entry.file_size
            if entry.file_size > MAX_FILE_BYTES or expanded > MAX_BYTES or entry.file_size > max(1024 * 1024, entry.compress_size * 200):
                raise ValueError('Archive expansion limit exceeded.')
            if entry.is_dir(): continue
            data = bundle.read(entry)
            if name == 'manifest.json': manifest_data = data
            else: files[name] = data
    file_keys = {unicodedata.normalize('NFC', name).casefold() for name in [*files, 'manifest.json']}
    for name in file_keys:
        parts = name.split('/')
        if any('/'.join(parts[:index]) in file_keys for index in range(1, len(parts))):
            raise ValueError('Archive file/directory path collision.')
    if manifest_data is None: raise ValueError('manifest.json is required at archive root.')
    manifest = _manifest(json.loads(manifest_data), files)
    # ZIP timestamps/order/compression never change content identity.
    digest = _hash(_json(manifest))
    return {'manifest': manifest, 'sha256': digest, 'files': files, 'validation_status': 'structure_verified',
            'functional_status': 'not_verified'}


def preview_pack(archive):
    return {k: v for k, v in inspect_pack(archive).items() if k != 'files'}


@contextmanager
def _lock(home=None):
    root = root_path(home)
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield root


def _write(path, value):
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    try:
        with temporary.open('xb') as stream:
            stream.write(_json(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def pack_path(pack_id, version, home=None):
    _identity(pack_id, version)
    return root_path(home) / 'packs' / pack_id / version


def load_pack(pack_id, version, home=None, expected_sha256=None):
    directory = pack_path(pack_id, version, home)
    manifest = json.loads((directory / 'manifest.json').read_text())
    files = {}
    for name in manifest.get('files', {}):
        path = directory / _relative(name)
        if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError('Installed payload escaped its immutable directory.')
        files[name] = path.read_bytes()
    _manifest(manifest, files)
    digest = _hash(_json(manifest))
    if expected_sha256 is not None and digest != expected_sha256: raise ValueError('Pinned extension hash mismatch.')
    return {'manifest': manifest, 'sha256': digest, 'path': str(directory)}


def import_pack(archive, home=None, expected_sha256=None):
    checked = inspect_pack(archive)
    if expected_sha256 is not None and checked['sha256'] != expected_sha256:
        raise ValueError('Pack changed after preview.')
    manifest = checked['manifest']
    with _lock(home) as root:
        destination = pack_path(manifest['id'], manifest['version'], home)
        if destination.exists():
            old = load_pack(manifest['id'], manifest['version'], home)
            if old['sha256'] != checked['sha256']: raise ValueError('ID/version already exists with different content; choose a new version.')
            return {**old, 'idempotent': True}
        stage = Path(tempfile.mkdtemp(prefix='.import-', dir=root))
        try:
            for name, data in checked['files'].items():
                path = stage / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            (stage / 'manifest.json').write_bytes(_json(manifest))
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.rename(stage, destination)
        finally:
            if stage.exists(): shutil.rmtree(stage)
        return {**load_pack(manifest['id'], manifest['version'], home), 'idempotent': False}


def list_packs(home=None):
    base = root_path(home) / 'packs'
    result = []
    for path in sorted(base.glob('*/*')):
        if not path.is_dir() or path.is_symlink(): continue
        try:
            result.append(load_pack(path.parent.name, path.name, home))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result.append({'manifest': {'id': path.parent.name, 'version': path.name,
                'display_name': path.parent.name, 'description': 'Installed content is unavailable.',
                'kind': 'unavailable', 'tools': [], 'skills': [], 'dependencies': []},
                'sha256': None, 'validation_status': 'invalid', 'error': str(exc)})
    return result


def export_pack(pack_id, version, destination, home=None):
    value = load_pack(pack_id, version, home)
    root = Path(value['path'])
    destination = Path(destination)
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr('manifest.json', _json(value['manifest']))
        for name in sorted(value['manifest']['files']): bundle.write(root / name, name)
    return {'path': str(destination), 'sha256': value['sha256']}


def normalize_selection(selection, home=None):
    if not isinstance(selection, dict) or set(selection) != {'packs'} or not isinstance(selection['packs'], dict):
        raise ValueError('Selection must contain a packs map.')
    result = deepcopy(selection)
    for pack_id, choice in result['packs'].items():
        if not isinstance(choice, dict) or set(choice) - {'version', 'sha256', 'enabled', 'tools', 'skills'}:
            raise ValueError('Invalid pack selection.')
        loaded = load_pack(pack_id, choice.get('version'), home, choice.get('sha256'))
        if choice.get('sha256') != loaded['sha256'] or type(choice.get('enabled')) is not bool:
            raise ValueError('Selection requires exact content hash and enabled boolean.')
        for field in ('tools', 'skills'):
            overrides = choice.setdefault(field, {})
            names = {item['name'] for item in loaded['manifest'].get(field, [])}
            if not isinstance(overrides, dict) or set(overrides) - names or any(type(value) is not bool for value in overrides.values()):
                raise ValueError('Unknown or invalid entry toggle.')
    return result


def selection_snapshot(home=None):
    path = root_path(home) / 'defaults.json'
    if path.exists(): return json.loads(path.read_text())
    selection = {'packs': {}}
    return {'revision': _hash(_json(selection)), 'selection': selection, 'operations': {}}


def save_selection(selection, expected_revision, operation_id, home=None):
    selection = normalize_selection(selection, home)
    if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 128: raise ValueError('operation_id is required.')
    digest = _hash(_json(selection))
    with _lock(home) as root:
        current = selection_snapshot(home)
        previous = current.get('operations', {}).get(operation_id)
        if previous:
            if previous != digest: raise ValueError('operation_id was already used with different content.')
            return current
        if expected_revision != current['revision']: raise ValueError('Extension defaults revision conflict.')
        current.update(revision=digest, selection=selection)
        current.setdefault('operations', {})[operation_id] = digest
        _write(root / 'defaults.json', current)
        return current


def delete_pack(pack_id, version, home=None, references=()):
    """Caller supplies case references after inspecting the existing case registry."""
    with _lock(home):
        for selection in [selection_snapshot(home)['selection'], *references]:
            if selection.get('packs', {}).get(pack_id, {}).get('version') == version:
                raise ValueError('Pack version is referenced; retain it for rollback.')
        destination = pack_path(pack_id, version, home)
        if destination.exists(): shutil.rmtree(destination)
        return {'deleted': True, 'id': pack_id, 'version': version}
