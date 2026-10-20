"""Small, offline Linux deployment helper. Never installs, calls a model or restarts.

Use the upstream PM for dependencies. Init is for an EMPTY state directory only;
source bundles contain neither operational configuration nor investigation data.
"""
import argparse
import copy
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
ROOT_FILES = ('test_initial_triage_context.py', 'test_linux_persistence.py',
              'test_skill_proposals.py', 'test_skill_proposal_native.py', 'test_skill_proposal_api.py', 'test_task_focus.py',
              'test_telegram_clarify_native.py',
              'test_model_result_compaction.py', 'test_triage_target_contract.py',
              'docs/extensions/workflow-skills.md', 'docs/notifications.md',
              'test_tenant_memory.py', 'test_tenant_ownership.py', 'test_tenant_boundaries.py',
              'docs/tenant-architecture.md',
              'test_report_word_export.py', 'test_report_word_api.py', 'test_report_narrative.py', 'docs/report-authoring.md', 'test_reader_explanation.py', 'test_report_numbered_references.py', 'test_native_agent_options.py', 'test_repository_builder.py', 'test_management_api.py', 'test_environment_management.py', 'test_environment_support.py', 'test_extension_hub.py',
              'docs/environment-management.md', 'docs/extensions/format.md',
              'docs/extensions/repository-builder.md', 'docs/roadmap/skill-creator.md',
              'test_filename_observations.py', 'test_numeric_source_citations.py', 'test_review_numeric_grounding.py', 'test_multi_image_report.py', 'test_triage_query.py', 'test_acceptance_cases.py',
              'tests/acceptance/manifest.json', 'tests/acceptance/README.md',
              'tests/acceptance/inputs/discriminating_clue/records.jsonl',
              'tests/acceptance/inputs/discriminating_clue/question.txt',
              'tests/acceptance/inputs/benign_explanation/records.jsonl',
              'tests/acceptance/inputs/benign_explanation/question.txt',
              'tests/acceptance/inputs/partial_range/records.jsonl',
              'tests/acceptance/inputs/partial_range/question.txt',
              'tests/acceptance/truth/discriminating_clue.json',
              'tests/acceptance/truth/benign_explanation.json',
              'tests/acceptance/truth/partial_range.json',
              'test_capability_policy.py', 'test_capability_runtime.py', 'test_capability_native.py',
              'test_runtime_clock.py', 'test_investigation_close.py',
              'test_review_chat.py', 'test_evidence_collection.py', 'test_evidence_collection_sensitive.py',
              'test_collection_api.py', 'WORKBENCH_DATA.txt',
              'test_release_profiles.py', 'test_dashboard_skill_locale.py', 'RELEASE_PROFILES.txt', 'test_schema_presentation.py', 'test_image_search_entry.py', 'test_specialist_contracts.py', 'HARNESS_ACCEPTANCE.txt', 'HARNESS_ACCEPTANCE.json', 'test_projection_recovery.py', 'HARNESS_P3.txt', 'test_execution_contract.py', 'HARNESS_P2.txt', 'test_question_v2.py', 'test_question_recovery.py', 'HARNESS_P1.txt', 'test_acquisition_context.py', 'test_search_selection.py', 'test_context_result_refs.py', 'test_grounded_judgment.py', 'test_recovery_integration.py', 'AGENTS.md', '.hermes.md', 'run.py', 'telegram_mirror.py', 'telegram_mentions.py', 'telegram_reports.py',
              'test_onboarding.py', 'test_remote_evidence_access.py', 'test_remote_policy.py', 'test_executive_followup.py', 'test_investigation_view.py', 'test_investigation_display.py', 'test_investigation_enrichment.py', 'test_prompt_efficiency.py', 'test_turn_context_efficiency.py', 'test_context_read_efficiency.py', 'test_private_lan_dashboard.py',
              'test_existing_question_gap_recovery.py', 'test_inventory_scope.py', 'test_note_projection_memory.py',
              'test_question_bootstrap.py', 'test_image_reference.py', 'test_source_handles.py', 'test_question_reference_integration.py',
              'test_goal_advisory.py', 'test_independent_review.py', 'test_skill_consolidation.py', 'test_forensic_skill_loading.py',
              'verify_dashboard_skills.py', 'test_artifact_recipes.py', 'test_structured_query.py', 'test_login_records.py', 'test_delivery_fidelity.py',
              'test_forensic_tool_packages.py', 'tools/forensic-tools.lock.json', 'tools/install_forensic_tools.py', 'tools/smoke_document_runtime.py', 'tools/smoke_recovery_runtime.py', 'test_recovery_runtime_smoke.py',
              'Dockerfile.analysis', 'requirements-analysis.txt', 'ANALYSIS_TOOLS.txt',
              'requirements-sensitive.txt', 'SENSITIVE_DATA.txt', 'test_sensitive_install.py', 'test_sensitive_scan.py',
              'test_sensitive_integration.py', 'test_sensitive_reporting.py',
              'tools/sensitive/README.txt', 'tools/sensitive/gitleaks.toml',
              'tools/sensitive/empty.gitleaksignore', 'tools/sensitive/presidio-source-sha256.json',
              'tools/sensitive/presidio_local.py',
              'test_observation_scope.py', 'test_review_dispositions.py',
              'test_review_overlap.py', 'test_forensic_workbench.py', 'test_forensic_workbench_writer.py',
              'test_temporal_inference.py', 'test_temporal_report.py', 'test_workbench_views.py',
              'test_review_response_writer.py', 'test_independent_review_finalization.py',
              'HARNESS_REVIEW_RECOVERY.txt',
              'test_report_projection_memory.py', 'test_report_write_status.py',
              'telegram_actions.py', 'deploy_forsic.py', 'setup_forsic.py', 'test_setup_bootstrap.py', 'test_setup.py', 'test_hosting.py',
              'smoke.py', 'library_smoke.py', 'requirements-forsic.txt', 'Dockerfile.tools',
              'UPSTREAM.txt', 'README.txt', 'DEPLOYMENT.txt', 'DESIGN.txt', 'deployment/Caddyfile.example', 'test_deployment.py',
              'test_evidence.py', 'test_workflow.py', 'test_case_library.py', 'test_intake.py',
              'test_improvements.py', 'test_investigations.py', 'test_telegram_mirror.py',
              'test_telegram_mentions.py', 'test_telegram_reports.py', 'test_telegram_actions.py',
              'test_report_driven.py', 'test_timeline_recovery.py',
              'test_shell_workspace.py', 'test_shell_integration.py', 'test_shell_container.py', 'test_image_access.py', 'HARNESS_SHELL.txt',
              'test_connections.py', 'test_note_revision_recovery.py', 'test_harness_guidance.py', 'test_harness_context.py', 'test_note_model_view.py',
              'test_report_review_projection.py', 'test_reporting_pages.py', 'test_dashboard.cjs',
              'test_investigation_state.py', 'test_investigation_context.py', 'test_mission_native_tools.py',
              'test_native_goal_evaluation.py', 'test_native_tool_reload.py', 'test_current_judgment.py', 'test_direction_review.py', 'test_source_locator.py', 'test_relationship_api.py', 'test_execution_crash_windows.py',
              'test_archive_reader.py', 'test_archive_selection.py', 'test_archive_host_origin.py', 'test_image_admission.py', 'test_image_read_entry.py', 'test_mission_reference.py', 'test_efficiency_contracts.py', 'test_assessment_basis.py', 'test_retained_mission_contract.py', 'test_indicators.py', 'test_search_resume.py', 'test_log_timeline.py', 'test_indicator_integration.py')
PUBLIC_FILES = ('README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.txt', 'SECURITY.md',
                'NOTICE', 'NOTICE.txt', '.gitignore', '.dockerignore')
REQUIRED_PUBLIC = ('README.md', 'docs/ARCHITECTURE.md', 'docs/TOOLS_AND_SKILLS.md',
                   'LICENSE', 'THIRD_PARTY_NOTICES.txt', '.gitignore', '.dockerignore')
CONFIG_FILES = ('SOUL.md', 'AGENTS.md', 'skin.yaml', 'forsic-theme.yaml', 'config.example.yaml', 'bundled-skills/README.txt')
FIXTURES = ('notes.txt', 'cron.log', 'change.txt')
UPSTREAM_ADDITIONS = (
    'web/src/components/ForsicWorkflowSkills.tsx', 'web/src/components/forsic-workflow-skills.css',
    'web/src/components/ForsicMissionFocus.tsx', 'web/src/components/ForsicMissionFocus.test.tsx',
    'web/src/components/forsic-mission-focus.css',
    'hermes_state_profile_guard.py',
    'web/src/components/ForsicMemoryReceipts.tsx', 'web/src/components/ForsicMemoryReceipts.test.tsx',
    'web/src/components/ForsicTenantGate.tsx', 'web/src/components/ForsicTenantGate.test.tsx',
    'web/src/components/forsic-tenant.css', 'web/src/contexts/tenant-context.ts',
    'web/src/lib/forsic-memory.ts', 'web/src/lib/forsic-tenant.ts', 'web/src/lib/forsic-tenant.test.ts',
    'web/src/pages/ForsicMemoryPage.tsx', 'web/src/pages/ForsicMemoryPage.test.tsx',
    'web/src/components/ForsicEnvironmentPlan.tsx', 'web/src/components/ForsicEnvironmentPlan.test.tsx',
    'web/src/components/ForsicReports.tsx', 'web/src/components/ForsicReports.css', 'web/src/components/ForsicReports.test.tsx',
    'web/src/pages/ForsicManagementPage.tsx', 'web/src/pages/forsic-management-page.css',
    'tools/tool_schema_context.py', 'hermes_cli/plugins_agent_options.py',
    'web/src/components/ForsicManagement.tsx', 'web/src/components/ForsicManagement.css',
    'web/src/components/ForsicManagement.test.tsx', 'web/src/lib/forsic-management.ts',
    'web/src/lib/forsic-management.test.ts',
    'web/src/components/ForsicCapabilities.tsx', 'web/src/components/ForsicCapabilities.css',
    'web/src/components/ForsicCapabilities.test.tsx',
    'web/src/components/ForsicEvidenceCollection.tsx', 'web/src/components/ForsicEvidenceCollection.test.tsx',
    'web/src/components/forsic-evidence-collection.css', 'web/src/lib/forsic-evidence-collection.ts',
    'web/src/lib/forsic-review-chat.test.ts',
    'web/src/lib/forsic-skill-copy.ts', 'web/src/lib/forsic-skill-copy.test.ts',
    'web/src/components/ForsicSetup.tsx', 'web/src/components/ForsicSetup.test.tsx',
    'web/src/components/forsic-setup.css', 'web/src/components/ForsicConnectionSettings.test.tsx',
    'web/src/components/ForsicTimeline.tsx', 'web/src/components/ForsicRelationMap.tsx',
    'web/src/components/ForsicReportLink.tsx', 'web/src/components/ForsicReportLink.test.tsx',
    'web/src/components/ForsicReportLink.css', 'web/src/components/ForsicReportDownload.test.tsx', 'web/src/components/ForsicConnectionCard.test.tsx',
    'web/src/components/forsic-monitor.ts', 'web/src/lib/forsic-context.ts',
    'web/src/components/ForsicToolCall.test.tsx',
    'web/src/components/ForsicOnboarding.tsx', 'web/src/components/ForsicOnboarding.test.tsx',
    'web/src/components/forsic-onboarding.css',
    'web/src/components/ForsicEvidenceLocation.tsx', 'web/src/components/ForsicEvidenceLocation.test.tsx',
    'web/src/lib/uuid.ts', 'web/src/lib/uuid.test.ts',
    'web/src/components/ForsicEntry.tsx', 'web/src/components/ForsicEntry.test.tsx',
    'web/src/components/forsic-entry.css',
    'web/src/components/ForsicInvestigation.tsx', 'web/src/components/ForsicInvestigation.test.tsx',
    'web/src/components/forsic-investigation.css',
    'web/src/hooks/useForsicInvestigation.ts', 'web/src/hooks/useForsicInvestigation.test.tsx',
    'web/src/lib/forsic-investigation.ts',
    'hermes_cli/goals_advisory.py',
    'hermes_cli/goals_runtime.py',
    'tests/hermes_cli/test_goal_runtime_budget.py', 'tests/agent/test_goal_runtime_deadline.py',
    'tests/agent/test_tool_failure_tolerance.py',
    'tests/tui_gateway/test_inflight_user_identity.py',
    'tests/agent/test_local_endpoint_probe_preference.py',
    'tests/hermes_cli/test_chat_start_failure.py',
    'tests/test_plugin_execution_identity.py',
    'tests/tui_gateway/test_goal_guardrail_pause.py',
    'tests/tui_gateway/test_goal_terminal_failure_pause.py',
    'hermes_cli/goals_turn_context.py',
    'agent/tool_loop_comparison.py',
    'tests/hermes_cli/test_custom_banner_branding.py', 'tests/tui_gateway/test_goal_display.py',
    'ui-tui/src/__tests__/sessionGreeting.test.ts', 'ui-tui/src/app/sessionGreeting.ts',
    'web/public/assets/forsic-lens.svg', 'web/src/plugins/slots.test.tsx',
    'tests/hermes_cli/test_terminal_theme.py', 'web/src/i18n/context.test.ts',
    'web/src/forsic-shell.css', 'web/src/App.test.tsx', 'web/src/lib/analysis-title.ts',
    'tests/agent/test_plugin_compaction_projection.py',
    'tests/agent/test_plugin_prompt_sections_roundtrip.py',
    'tests/agent/test_pre_llm_hook_metadata.py',
    'tests/agent/test_long_inflight_compaction.py',
    'agent/tool_argument_diagnostics.py',
    'tests/agent/test_tool_boundary_diagnostics.py',
    'tests/agent/test_tool_failure_groups.py',
    'tests/agent/test_guardrail_notice_locale.py',
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


def initial_config(base_url, model, *, wizard=False):
    if (not model.strip() and not wizard) or any(char in model for char in '\r\n\0'):
        raise ValueError('Provide a model name without control characters')
    local = {'provider': 'custom', 'model': model, 'base_url': private_url(base_url), 'api_key': 'ollama'}
    return {
        '_config_version': 49,
        'model': {'provider': 'custom', 'default': model, 'base_url': local['base_url'],
                  'context_length': 65536, 'ollama_num_ctx': 65536},
        'compression': {'threshold_tokens': 40000, 'context_timeout_seconds': 1200,
                        'context_total_ceiling_seconds': 1800},
        'fallback_model': {},
        'providers': {'custom': {'request_timeout_seconds': 1800, 'stale_timeout_seconds': 1800}},
        'tools': {'tool_search': {'enabled': 'off'}}, 'reasoning': {'enabled': False},
        'auxiliary': {'report_review': {'timeout': 1800},
                      'goal_judge': {**local, 'timeout': 600, 'max_tokens': 1024},
                      'title': dict(local), 'compression': {**local, 'timeout': 1200},
                      'title_generation': {'enabled': False, 'model_upgrade_enabled': False}},
        'platform_toolsets': {name: ['forsic', 'clarify'] for name in ('cli', 'api', 'web', 'desktop', 'telegram')},
        'agent': {'max_turns': 64, 'api_max_retries': 1, 'auto_recovery_cycles': 0,
                  'coding_context': 'off', 'execution_guidance': False,
                  'run_budget_seconds': 43200, 'local_stream_stale_timeout': 1800},
        'goals': {'max_turns': 32},
        'tool_loop_guardrails': {'hard_stop_enabled': True,
                                'hard_stop_after': {'exact_failure': 8, 'same_tool_failure': 8}},
        'memory': {'memory_enabled': False, 'user_profile_enabled': False, 'nudge_interval': 0},
        'curator': {'enabled': False}, 'background_review': {'enabled': False},
        'security': {'allow_lazy_installs': False}, 'updates': {'check': False},
        'dashboard': {'theme': 'forsic'},
        'display': {'skin': 'forsic', 'language': 'ko', 'tui_compact': True,
                    'new_session_prompt': 'As the forensic colleague 포식이, greet the user naturally in Korean and ask for the full path of the file or folder to analyze. No investigation has occurred yet. Use at most two sentences and no tools.'},
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


def initialize(root, base_url, model, python, evidence_dir=None, *, wizard=False):
    root = root.resolve()
    state = root / 'state'
    if state.exists() or state.is_symlink():
        raise ValueError('Existing state is never overwritten. Upgrade in place only after safe backup.')
    required = [root / 'run.py', root / 'forsic_plugin/plugin.yaml', root / 'upstream/hermes/pm/lock.json']
    required += [root / 'config' / name for name in CONFIG_FILES]
    required += [root / 'cases/smoke/evidence' / name for name in FIXTURES]
    if not all(path.is_file() for path in required):
        raise ValueError('Incomplete source tree; inspect the source bundle manifest first')
    config = initial_config(base_url, '' if wizard else model, wizard=wizard)
    if wizard:
        # A new installation has no selected model until the operator chooses it.
        config['model'].pop('context_length', None)
        config['model'].pop('ollama_num_ctx', None)
    service = unit_text(root, python)  # Validate before writing anything.
    evidence_directory = None
    if evidence_dir is not None:
        from forsic_plugin.onboarding import EvidenceBrowser
        browser = EvidenceBrowser(state / 'hermes', state / 'intake')
        # The install target may differ from the helper's source checkout.
        browser.private.append(root)
        connection = browser.connection(str(evidence_dir))
        if connection['kind'] != 'directory':
            raise ValueError('Evidence directory must be an existing readable directory')
        evidence_directory = connection['path']
    manifest = root / 'state/synthetic/case.json'
    config['plugins']['entries']['forsic']['settings'] = {
        'case_file': str(manifest), 'synthetic_roots': [str(root / 'cases/smoke/evidence')],
    }
    if wizard:
        config['plugins']['entries']['forsic']['settings']['setup_completed'] = False
    if evidence_directory is not None:
        config['plugins']['entries']['forsic']['settings']['evidence_directory'] = evidence_directory
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


def frontend_preflight(root):
    """Verify native compiler receipts; never install, rebuild or call a model."""
    from hermes_cli.source_build import source_product_current
    upstream = root / 'upstream/hermes'
    products = {product: source_product_current(upstream, product, upstream / output)
                for product, output in (('tui', 'ui-tui/dist'), ('web', 'hermes_cli/web_dist'))}
    return {'ready': all(products.values()), 'products': products,
            'meaning': 'Compiler input/output receipts verified; browser behavior needs a separate check.',
            'builds': 0, 'model_requests': 0, 'network_requests': 0}


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
        from forsic_plugin.connections import endpoint
        endpoint(model.get('base_url', ''))
        add('model_connection_config', model.get('provider') == 'custom' and bool(model.get('default')),
            'explicit OpenAI-compatible connection; no request made')
        setup = config.get('plugins', {}).get('entries', {}).get('forsic', {}).get('settings', {})
        add('initial_setup_completed', setup.get('setup_completed', True) is not False,
            'complete the browser setup and restart before investigation')
        add('fallback_disabled', not config.get('fallback_model'), 'no automatic external fallback')
        add('forsic_only_tools', all(config.get('platform_toolsets', {}).get(name) in (['forsic'], ['forsic', 'clarify'])
            for name in ('cli', 'web', 'telegram')), 'Forsic execution tools and optional native user clarification only')
        for name, role in config.get('auxiliary', {}).items():
            if isinstance(role, dict) and role.get('base_url'):
                add('auxiliary_connection:' + name, role.get('base_url') == model.get('base_url') and
                    role.get('provider') == 'custom', 'same explicit connection')
    except Exception:
        # YAML/parser diagnostics can echo secret-bearing source lines. Report only
        # that configuration could not be checked, never its exception text.
        add('model_connection_config', False, 'cannot verify configuration with this Python; no values printed')
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
    if ((rel.name.startswith('.env.') and rel.name != '.env.example') or
            rel.suffix.lower() in ('.db', '.sqlite', '.sqlite3', '.pem', '.key', '.p12', '.pfx') or
            rel.name.endswith(('-wal', '-shm')) or
            any(part in ('backups', 'artifacts') for part in rel.parts)):
        raise ValueError('Private runtime or credential file is not release source: ' + str(rel))
    path = root / rel
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Missing source or source escapes checkout: ' + str(rel))
    return path


def upstream_omission(name, changed):
    """Remove distribution-only trees, not the web/TUI's shared dependency graph."""
    path = Path(name)
    if name in changed or path.name in ('LICENSE', 'LICENSE.md', 'NOTICE', 'NOTICE.txt'):
        return ''
    if path.parts[0] in ('skills', 'optional-skills'):
        return 'Forsic uses its curated plugin procedures; generic skill packages are not shipped or seeded'
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


def slim_omission(name):
    """Only explicitly development-only root material; build/runtime inputs remain."""
    path = Path(name)
    if name.startswith('tests/acceptance/'):
        return 'Synthetic behavior inputs and evaluator-only truth belong to the development profile'
    if len(path.parts) == 1 and (path.name.startswith('test_') or path.name.startswith('HARNESS_')):
        return 'Development regression/acceptance source retained in the development profile'
    if name in ('DESIGN.txt', 'docs/ARCHITECTURE.md', 'docs/TOOLS_AND_SKILLS.md'):
        return 'Development design reference retained in the development profile'
    return ''


def apply_release_profile(manifest, profile):
    if profile not in (None, 'development', 'slim'):
        raise ValueError('Unknown source release profile')
    is_slim = manifest.get('profile') == 'forsic-linux-web-source-slim'
    if profile == 'development' and is_slim:
        raise ValueError('A slim source cannot reconstruct omitted development files; use the development source')
    if profile != 'slim' or is_slim:
        return manifest
    result = copy.deepcopy(manifest)
    result['profile'] = 'forsic-linux-web-source-slim'
    result['profile_omitted'] = {}
    for name in list(result['files']):
        reason = slim_omission(name)
        if reason:
            result['profile_omitted'][name] = {'reason': reason, 'sha256': result['files'].pop(name),
                                                'mode': result['modes'].pop(name)}
    result['contains'] = 'slim source + pinned upstream/local deltas + synthetic fixtures; NOT a ready runtime'
    return result


def release_plan(root, profile=None):
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
        # Validate the entire baseline before considering any profile exclusions.
        return apply_release_profile(manifest, profile)

    files = {Path(name) for name in (*ROOT_FILES, *REQUIRED_PUBLIC)}
    files.update(Path(name) for name in PUBLIC_FILES if (root / name).is_file())
    files.update(Path('config') / name for name in CONFIG_FILES)
    files.update(path.relative_to(root) for path in (root/'examples/extensions').rglob('*')
                 if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc')
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
    return apply_release_profile(manifest, profile)


def source_files(root):
    return [Path(name) for name in release_plan(root)['files']]


def release_items(root, manifest):
    for name, expected in manifest['files'].items():
        data = checked_source(root, Path(name)).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('Source changed while exporting: ' + name)
        yield name, data, manifest['modes'][name]
    yield 'source-manifest.json', (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode(), 0o644


def export_source(root, output, profile=None):
    if output.exists() or output.is_symlink():
        raise ValueError('Output exists; choose a new export directory')
    if output.resolve().is_relative_to(root.resolve()):
        raise ValueError('Export outside the operational checkout')
    manifest = release_plan(root, profile)
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


def bundle(root, output, profile=None):
    if output.exists():
        raise ValueError('Output exists; choose a new release filename')
    manifest = release_plan(root, profile)
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
    sub.add_parser('frontend-preflight')
    init = sub.add_parser('init')
    init.add_argument('--ollama-url', default='http://127.0.0.1:11434/v1')
    init.add_argument('--model', default='qwen3.8:latest')
    init.add_argument('--wizard', action='store_true',
                      help='Start with no selected model and complete configuration in the web setup')
    init.add_argument('--python', type=Path, default=Path(sys.executable))
    init.add_argument('--evidence-dir', type=Path,
                      help='Existing absolute host evidence directory to offer in the UI; no analysis is started')
    release = sub.add_parser('bundle')
    release.add_argument('output', type=Path)
    release.add_argument('--profile', choices=('development', 'slim'), default=None)
    public = sub.add_parser('export')
    public.add_argument('output', type=Path)
    public.add_argument('--profile', choices=('development', 'slim'), default=None)
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        if args.action == 'init':
            result = initialize(root, args.ollama_url, args.model, args.python.resolve(), args.evidence_dir,
                                wizard=args.wizard)
        elif args.action == 'bundle':
            result = bundle(root, args.output.resolve(), args.profile)
        elif args.action == 'export':
            result = export_source(root, args.output.resolve(), args.profile)
        elif args.action == 'idle':
            result = idle_state(root)
        elif args.action == 'frontend-preflight':
            result = frontend_preflight(root)
        else:
            result = doctor(root)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(2, type(exc).__name__ + ': ' + str(exc) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int((args.action == 'doctor' and not result['ready_for_manual_preflight']) or
               (args.action == 'frontend-preflight' and not result['ready']) or
               (args.action == 'idle' and not result['observed_idle']))


if __name__ == '__main__':
    raise SystemExit(main())
