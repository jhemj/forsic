"""User-owned capability preferences over native Hermes plugin settings.

This allow-list controls Forsic's menu and dispatch, not arbitrary shell commands
or an OS sandbox. Target OS describes the evidence, never the analysis host.
"""
from dataclasses import dataclass
from pathlib import Path

TARGET_OS = ('all', 'windows', 'macos', 'linux')
TOOLS = (
    'forsic_indicators', 'forsic_intel', 'forsic_intake', 'forsic_cases',
    'forsic_shell', 'forsic_list', 'forsic_read', 'forsic_search', 'forsic_hash',
    'forsic_image_info', 'forsic_image_files', 'forsic_image_read',
    'forsic_image_search', 'forsic_archive_read', 'forsic_archive_relist',
    'forsic_image_read_mission', 'forsic_reporting', 'forsic_question',
    'forsic_mission', 'forsic_assess', 'forsic_review_response',
    'forsic_read_bytes', 'forsic_verify', 'forsic_note', 'forsic_skill',
)
SKILLS = (
    'forensic-investigation', 'evidence-intake', 'source-triage',
    'inspect-documents', 'inspect-scripts', 'test-hypothesis', 'correlate-time',
    'answer-question', 'review-conclusions', 'forsic-report-driven',
    'threat-intelligence', 'executive-followup', 'organize-indicators',
    'personal-data-scope', 'initial-access', 'data-movement', 'inspect-artifacts',
    'investigation-recipes', 'query-structured-logs', 'scan-windows-events',
    'scan-file-rules', 'import-timeline', 'consult-previous-cases',
    'scan-sensitive-data',
)
# Intake, direct evidence access, current answers, retained sources, review and
# report saving must stay usable even when every optional capability is off.
CORE_TOOLS = frozenset((
    'forsic_intake', 'forsic_list', 'forsic_read', 'forsic_search',
    'forsic_read_bytes', 'forsic_image_files', 'forsic_image_read',
    'forsic_image_search', 'forsic_archive_read', 'forsic_archive_relist',
    'forsic_reporting', 'forsic_question', 'forsic_review_response',
    'forsic_note', 'forsic_skill',
))
# Current tool wrappers handle multiple evidence OSes. Only this procedure is
# Windows-specific; do not infer unsupported macOS/Linux wrappers from names.
SKILL_OS = {'scan-windows-events': ('windows',)}


def normalize(values):
    """Strict complete preference document; bool overrides take priority over OS."""
    if values is None:
        values = {}
    if not isinstance(values, dict) or set(values) - {'target_os', 'tools', 'skills'}:
        raise ValueError('Capability settings require target_os, tools and skills only.')
    target = values.get('target_os', 'all')
    if not isinstance(target, str) or target not in TARGET_OS:
        raise ValueError('target_os must be all, windows, macos or linux.')
    result = {'target_os': target}
    for field, names in (('tools', TOOLS), ('skills', SKILLS)):
        overrides = values.get(field, {})
        if (not isinstance(overrides, dict) or set(overrides) - set(names)
                or any(type(value) is not bool for value in overrides.values())):
            raise ValueError(field + ' must map known capability names to booleans.')
        if field == 'tools' and any(name in CORE_TOOLS and not enabled
                                   for name, enabled in overrides.items()):
            raise ValueError('Core evidence, question, review and save tools stay enabled.')
        result[field] = dict(overrides)
    return result


@dataclass(frozen=True)
class CapabilityPolicy:
    settings: dict

    def tool_enabled(self, name):
        return name in TOOLS and (name in CORE_TOOLS or self.settings['tools'].get(name, True))

    def skill_enabled(self, name):
        if name not in SKILLS:
            return False
        default = self.settings['target_os'] == 'all' or name not in SKILL_OS or self.settings['target_os'] in SKILL_OS[name]
        return self.settings['skills'].get(name, default)


def policy(values=None):
    return CapabilityPolicy(normalize(values))


def preferences(cfg):
    entry = cfg.get('plugins', {}).get('entries', {}).get('forsic', {})
    settings = entry.get('settings') or {}
    return settings.get('capabilities', (entry.get('config') or {}).get('capabilities'))


def policy_from_config(cfg):
    from hermes_cli.config_read_errors import FailedConfigRead
    if not isinstance(cfg, dict) or isinstance(cfg, FailedConfigRead):
        raise ValueError('Capability settings are unavailable; repair the user configuration.')
    return policy(preferences(cfg))


def unavailable_policy():
    # Preserve reading/saving/cancel affordances while refusing optional work.
    return policy({'tools': {name: False for name in TOOLS if name not in CORE_TOOLS},
                   'skills': {name: False for name in SKILLS}})


def runtime_policy(values):
    """Use context-relative settings, checking native read failure before admission."""
    from hermes_cli.config import load_config_readonly
    from hermes_cli.config_read_errors import FailedConfigRead
    try:
        cfg = load_config_readonly()
        if not isinstance(cfg, dict) or isinstance(cfg, FailedConfigRead):
            return unavailable_policy()
        return policy(values)
    except (OSError, ValueError, TypeError, AttributeError):
        return unavailable_policy()


def current_policy():
    from hermes_cli.config import load_config_readonly
    try:
        return policy_from_config(load_config_readonly())
    except (OSError, ValueError, TypeError, AttributeError):
        return unavailable_policy()


def disabled_result(kind, name):
    return {'error': 'This capability is disabled by the user. Ask the user to enable it in settings if needed.',
            'error_code': 'capability_disabled', 'capability_kind': kind,
            'capability': name, 'retryable': False}


def public(cfg=None):
    if cfg is None:
        from hermes_cli.config import require_readable_config_before_write
        cfg = require_readable_config_before_write()
    selected = policy_from_config(cfg)
    from agent.skill_utils import parse_frontmatter
    skills = []
    for name in SKILLS:
        path = Path(__file__).parent / 'skills' / name / 'SKILL.md'
        metadata, _ = parse_frontmatter(path.read_text())
        skills.append({'name': name, 'description': metadata['description'],
                       'enabled': selected.skill_enabled(name), 'required': False,
                       'default_enabled': policy({'target_os': selected.settings['target_os']}).skill_enabled(name),
                       'overridden': name in selected.settings['skills'],
                       'target_os': list(SKILL_OS.get(name, TARGET_OS[1:]))})
    return {'settings': selected.settings, 'scope': 'profile',
            'target_os_options': list(TARGET_OS),
            'tools': [{'name': name, 'enabled': selected.tool_enabled(name),
                       'required': name in CORE_TOOLS, 'default_enabled': True,
                       'target_os': list(TARGET_OS[1:]),
                       'overridden': name in selected.settings['tools']} for name in TOOLS],
            'skills': skills, 'user_controlled': True,
            'terminal_boundary': 'Capability switches control Forsic tools and procedures; they do not sandbox commands in an enabled terminal.'}


def save(values):
    normalized = normalize(values)
    from hermes_cli.plugins_state import save_plugin_setting
    save_plugin_setting('forsic', ('capabilities',), normalized, replace=True)
    return public()
