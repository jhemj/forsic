"""Launch the pinned, Forsic-adapted Hermes dashboard and native plugin."""
import os
from pathlib import Path
import sys
import subprocess

root = Path(__file__).resolve().parent
upstream = root / 'upstream/hermes'
os.environ['HERMES_HOME'] = str(root / 'state/hermes')
os.environ['HERMES_RUNTIME_DIR'] = str(root / 'state/tools')
os.environ['HERMES_TUI_TOOLSETS'] = 'forsic'
# Forsic procedures are registered by the plugin and read on demand. Keep the
# native sync mechanism, but do not seed a second, generic skill library.
os.environ['HERMES_BUNDLED_SKILLS'] = str(root / 'config/bundled-skills')
os.environ['HERMES_OPTIONAL_SKILLS'] = str(root / 'config/bundled-skills')
os.environ['PYTHONPATH'] = os.pathsep.join((str(root), str(upstream)))
sys.path.insert(0, str(upstream))
import pm
from pm.environments import project_python

python = str(project_python(upstream))
env = pm.env_for('node')
os.chdir(root)
command = [python, '-m', 'hermes_cli.main', 'dashboard', '--host', '127.0.0.1', '--port', '8860', '--no-open', '--skip-build']
if len(sys.argv) > 1:
    command = [python, *sys.argv[1:]]
else:
    from deploy_forsic import frontend_preflight
    if not frontend_preflight(root)['ready']:
        raise SystemExit('Forsic web/chat build is missing or stale. Complete the native frontend build in DEPLOYMENT.txt, then run frontend-preflight before restarting.')
    # An explicit dashboard launch applies staged LLM settings, never a live turn.
    subprocess.run([python, '-c', 'from forsic_plugin.connections import apply_pending_llm; from forsic_plugin.setup import apply_pending_setup, validate_pending_setup; validate_pending_setup(); apply_pending_llm(); apply_pending_setup()'], env=env, check=True)
    # Register existing profiles/paths in place before serving. List/resolve APIs
    # remain read-only; old bookmarks retain their original case/session IDs.
    subprocess.run([python, '-c', 'from forsic_plugin.tenants import migrate_workspace; migrate_workspace()'],
                   env=env, check=True)
os.execve(python, command, env)
