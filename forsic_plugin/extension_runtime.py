"""Trusted declarative pack adapters registered through the native plugin API."""
import hashlib
import json
from pathlib import Path
import shlex
from tools.tool_schema_context import current_session_id, tool_schema_scope as session_scope

from . import extension_hub as hub


_registrars = {}


def remember_registration(callback, home=None):
    key = str(hub.home_path(home))
    _registrars[key] = callback
    return lambda: _registrars.pop(key, None) if _registrars.get(key) is callback else None


def refresh_extensions(home=None):
    """Register newly imported versions at the caller-owned native idle boundary."""
    callback = _registrars.get(str(hub.home_path(home)))
    if callback is None:
        return {'registered': False, 'reason': 'Plugin will discover extensions on its next load.'}
    callback()
    from model_tools import _clear_tool_defs_cache
    _clear_tool_defs_cache()
    return {'registered': True}


def runtime_name(pack, item):
    # Bounded native names include the complete descriptor identity via content hash.
    identity = hashlib.sha256((pack['sha256'] + '\0' + item['name']).encode()).hexdigest()[:32]
    return 'forsic_ext_' + item['name'].replace('-', '_')[:18] + '_' + identity


def selected(intake, session_id='', home=None):
    sid = session_id or current_session_id()
    case = intake.case(sid) if sid else None
    if case is None:
        # Unbound menus expose profile defaults; dispatch still requires a case.
        return hub.selection_snapshot(home)['selection']
    from .environments import EnvironmentManager
    return EnvironmentManager().extension_selection(case) or {'packs': {}}


def _enabled(pack, item, field, selection):
    choice = selection.get('packs', {}).get(pack['manifest']['id'], {})
    return (choice.get('version') == pack['manifest']['version'] and choice.get('sha256') == pack['sha256']
            and choice.get('enabled') is True and choice.get(field, {}).get(item['name'], True))


def dependency_status(pack, *, case=None, environment=None, tool=None):
    """Compare declared pins with measured inventory, never mere package presence."""
    dependencies = pack['manifest'].get('dependencies', [])
    if not dependencies:
        return {'available': True, 'missing': []}
    from .environments import EnvironmentManager
    try:
        if environment is None:
            manager = EnvironmentManager()
            if case is not None:
                environment = manager.extension_environment(case)
            else:
                environment_id = manager.catalog().get('default_environment_id')
                environment = manager.manifest(environment_id, verify=False) if environment_id else {}
        installed = environment.get('plan', {}).get('installed', {})
    except (OSError, ValueError, KeyError):
        installed = {}
    missing = []
    import re
    normalize = lambda name: re.sub(r'[-_.]+', '-', name).lower()
    base_python = {normalize(name): version for name, version in installed.get('python', {}).items()}
    isolated = {normalize(name): version for name, version in installed.get('isolated_python', {}).items()}
    for item in dependencies:
        if item['method'] == 'apt':
            actual = installed.get('apt', {}).get(item['name'])
            reason = 'Exact apt version is not installed in the selected environment.'
        else:
            name = normalize(item['name'])
            isolated_interpreter = tool is not None and tool.get('argv', [''])[0].startswith('/opt/forsic-deps/bin/')
            actual = isolated.get(name) if isolated_interpreter else base_python.get(name)
            reason = 'Exact Python version is unavailable to this descriptor executable.'
        if actual != item['version']:
            missing.append({'dependency': item, 'actual': actual, 'reason': reason})
    return {'available': not missing, 'missing': missing}


def available(pack, item, field, intake, capabilities, session_id='', home=None):
    try:
        try:
            from .repository_builder import is_builder_session
            if is_builder_session(session_id or current_session_id()): return False
        except ImportError:
            pass
        selection = selected(intake, session_id, home)
        if not _enabled(pack, item, field, selection): return False
        case = intake.case(session_id or current_session_id()) if session_id or current_session_id() else None
        if field == 'tools': return dependency_status(pack, case=case, tool=item)['available']
        tools = {tool['name']: tool for tool in pack['manifest'].get('tools', [])}
        for name in item.get('requires_tools', []):
            if name in tools:
                if not _enabled(pack, tools[name], 'tools', selection) or not dependency_status(pack, case=case, tool=tools[name])['available']: return False
            elif not capabilities().tool_enabled(name): return False
        return True
    except (OSError, ValueError, KeyError):
        return False


def argv_for(tool, arguments):
    import jsonschema
    jsonschema.Draft202012Validator(tool['parameters']).validate(arguments)
    result = []
    for item in tool['argv']:
        if isinstance(item, str): result.append(item)
        else:
            if item['argument'] not in arguments:
                raise ValueError('Mapped argument is missing: ' + item['argument'])
            value = arguments[item['argument']]
            result.append(str(value).lower() if type(value) is bool else str(value))
    return result


def register_extensions(ctx, intake, capabilities, native_goal_binding, projection_options):
    from agent.skill_utils import parse_frontmatter
    from tools.registry import no_cache_check_fn
    packs = hub.list_packs()
    skill_entries = []
    for pack in packs:
        if pack.get('error'):
            continue
        for tool in pack['manifest'].get('tools', []):
            name = runtime_name(pack, tool)
            schema = {'name': name, 'description': tool['description'], 'parameters': tool['parameters']}
            def handler(args, session_id='', task_id='', tool_call_id='', turn_id='', api_request_id='',
                        _pack=pack, _tool=tool, **kwargs):
                sid = session_id or task_id
                try:
                    if not available(_pack, _tool, 'tools', intake, capabilities, sid):
                        if _enabled(_pack, _tool, 'tools', selected(intake, sid)):
                            missing = dependency_status(_pack, case=intake.case(sid), tool=_tool)
                            if not missing['available']:
                                return json.dumps({'error': 'Extension dependencies are unavailable in this case environment.',
                                    'error_code': 'extension_dependencies_unavailable', 'dependencies': missing})
                        return json.dumps({'error': 'This pack/tool is disabled or a different version is selected.', 'error_code': 'capability_disabled'})
                    pinned = hub.load_pack(_pack['manifest']['id'], _pack['manifest']['version'], expected_sha256=_pack['sha256'])
                    case = intake.case(sid)
                    if case is None: raise ValueError('Bind case evidence before using an extension tool.')
                    argv = argv_for(_tool, args)
                    from .execution_contract import NativeExecution
                    native = NativeExecution(sid, turn_id, tool_call_id, api_request_id) if sid and tool_call_id else None
                    identity = {'id': _pack['manifest']['id'], 'version': _pack['manifest']['version'],
                                'sha256': _pack['sha256'], 'tool': _tool['name'], 'native_tool': runtime_name(_pack, _tool),
                                'schema_sha256': hashlib.sha256(hub._json(_tool['parameters'])).hexdigest()}
                    context = native_goal_binding(sid, **projection_options)
                    context['extension_dispatch'] = {'identity': identity, 'argv': argv, 'pack_root': pinned['path'],
                        'timeout_seconds': _tool.get('timeout_seconds', 60), 'max_output_bytes': _tool.get('max_output_bytes', 12000),
                        'output': _tool.get('output', {'format':'text'})}
                    return case.invoke('forsic_shell', {'action': 'run', 'command': shlex.join(argv), 'cwd': '/work',
                        'timeout_seconds': _tool.get('timeout_seconds', 60)}, sid, request_context=context, native_execution=native)
                except (ValueError, OSError, KeyError) as exc:
                    return json.dumps({'error': str(exc), 'error_code': 'extension_dispatch_rejected'})
                except Exception as exc:
                    import jsonschema
                    if isinstance(exc, jsonschema.ValidationError):
                        return json.dumps({'error': exc.message, 'error_code': 'extension_input_invalid'})
                    raise
            ctx.register_tool(name=name, toolset='forsic', schema=schema, handler=handler,
                description=tool['description'], pin_config_sensitive=True,
                check_fn=no_cache_check_fn(lambda p=pack, t=tool: available(p, t, 'tools', intake, capabilities)))
        for skill in pack['manifest'].get('skills', []):
            name = runtime_name(pack, skill)
            path = Path(pack['path']) / skill['path']
            metadata, _ = parse_frontmatter(path.read_text())
            ctx.register_skill(name=name, path=path, description=metadata['description'], frontmatter=metadata,
                check_fn=no_cache_check_fn(lambda p=pack, s=skill: available(p, s, 'skills', intake, capabilities)))
            references = sorted(p.relative_to(path.parent).as_posix() for p in (path.parent / 'references').rglob('*')
                                if p.is_file() and not p.is_symlink() and p.suffix == '.md')
            skill_entries.append({'name': name, 'logical_name': skill['name'], 'description': metadata['description'],
                                  'pack': pack, 'descriptor': skill, 'references': references})
    return skill_entries


def skill_catalog(entries, intake, capabilities, session_id=''):
    return [{'name': entry['name'], 'description': entry['description'], 'logical_name': entry['logical_name'],
             'pack_id': entry['pack']['manifest']['id'], 'version': entry['pack']['manifest']['version']}
            for entry in entries if available(entry['pack'], entry['descriptor'], 'skills', intake, capabilities, session_id)]


def read_skill(entries, args, intake, capabilities, session_id='', task_id=''):
    name = args.get('name')
    entry = next((e for e in entries if e['name'] == name), None)
    # Logical names are accepted only when the case resolves exactly one version.
    if entry is None:
        matches = [e for e in entries if e['logical_name'] == name and available(e['pack'], e['descriptor'], 'skills', intake, capabilities, session_id)]
        if len(matches) == 1: entry = matches[0]
    if entry is None: return None
    if not available(entry['pack'], entry['descriptor'], 'skills', intake, capabilities, session_id):
        return {'error': 'This skill or one of its required tools is disabled.', 'error_code': 'capability_disabled'}
    hub.load_pack(entry['pack']['manifest']['id'], entry['pack']['manifest']['version'], expected_sha256=entry['pack']['sha256'])
    reference = args.get('file_path')
    if 'file_path' in args and reference not in entry['references']:
        return {'error': 'Choose a listed reference.', 'references': entry['references']}
    from tools.skills_tool import skill_view
    result = json.loads(skill_view('forsic:' + entry['name'], file_path=reference, task_id=task_id, preprocess=False))
    if result.get('success') and reference is None: result['linked_files'] = {'references': entry['references']}
    result['extension'] = {k: entry['pack']['manifest'][k] for k in ('id', 'version')}
    result['extension']['sha256'] = entry['pack']['sha256']
    if result.get('success') and reference is None:
        result['procedure_ref'] = {'pack_id': entry['pack']['manifest']['id'],
            'version': entry['pack']['manifest']['version'], 'sha256': entry['pack']['sha256'],
            'skill_name': entry['logical_name']}
    return result


def parse_output(result, output):
    """Optional JSON parsing of actual retained stdout, never inferred evidence fields."""
    if output.get('format', 'text') != 'json' or result.get('error'):
        return result
    try:
        import base64
        raw = result.get('raw_output') or {}
        if raw.get('stdout_truncated') or raw.get('truncated') or result.get('preview_truncated', {}).get('stdout') and 'stdout_base64' not in raw:
            raise ValueError('Captured JSON output is incomplete.')
        value = base64.b64decode(raw['stdout_base64']) if 'stdout_base64' in raw else result.get('stdout', '').encode()
        def reject_constant(value):
            raise ValueError('Non-finite JSON constant: ' + value)
        result['parsed_output'] = json.loads(value, parse_constant=reject_constant)
        result['output_parse_status'] = 'parsed_actual_stdout'
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        result.update(success=False, error='Extension JSON output could not be parsed: ' + str(exc),
                      error_code='extension_output_invalid', output_parse_status='failed')
    return result
