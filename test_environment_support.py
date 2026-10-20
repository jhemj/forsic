"""Offline fixture for tests whose container execution is already mocked.

Environment preservation itself is tested in test_environment_management; these
fixtures let execution-boundary tests retain their original narrow scope.
"""
from pathlib import Path
from unittest.mock import patch

from forsic_plugin import environments


def offline_environment(test, image_id='sha256:'+'a'*64):
    def snapshot(manager, case):
        return {'environment_id':'env-synthetic','case_revision':1,
            'images':{role:{'image_id':image_id,'platform':'linux/amd64'} for role in ('analysis','parser')},
            'helpers':{name:{'path':str(environments.SOURCE/'forsic_plugin'/name),
                       'sha256':environments._file_sha(environments.SOURCE/'forsic_plugin'/name)}
                       for name in environments.HELPERS},
            'extensions':{'packs':{}},'app':{},
            'declared_lock_sha256':environments._file_sha(environments.SOURCE/'tools/forensic-tools.lock.json')}
    mocked=patch.object(environments.EnvironmentManager,'execution_snapshot',snapshot)
    mocked.start();test.addCleanup(mocked.stop)

    original_manifest=environments.EnvironmentManager.manifest
    def manifest(manager,environment_id,**kwargs):
        if environment_id=='env-synthetic':return {'environment_id':environment_id,'plan':{'installed':{}}}
        return original_manifest(manager,environment_id,**kwargs)
    checked=patch.object(environments.EnvironmentManager,'manifest',manifest)
    checked.start();test.addCleanup(checked.stop)
    from contextlib import contextmanager
    @contextmanager
    def execution(manager,case,*,expected_snapshot=None):
        yield expected_snapshot or snapshot(manager,case)
    lease=patch.object(environments.EnvironmentManager,'execution',execution)
    lease.start();test.addCleanup(lease.stop)
