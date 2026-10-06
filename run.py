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
    # An explicit dashboard launch applies staged LLM settings, never a live turn.
    subprocess.run([python, '-c', 'from forsic_plugin.connections import apply_pending_llm; apply_pending_llm()'], env=env, check=True)
os.execve(python, command, env)
