"""Opt-in strict ownership for hosts that dedicate one native store per profile.

Older shared stores are refused, never silently treated as the selected profile.
An embedding host may provide its own bounded, row-filtered read projection.
"""
from pathlib import Path
import sqlite3


def ownership_policy(db_path):
    import hermes_yaml as yaml
    from hermes_constants import profile_name_for_home
    home = Path(db_path).resolve().parent
    config_file = home / 'config.yaml'
    if not config_file.is_file():
        return None
    config = yaml.safe_load(config_file.read_text()) or {}
    policy = config.get('sessions', {})
    if not policy.get('require_profile_ownership'):
        return None
    owner = profile_name_for_home(home)
    if not owner:
        raise PermissionError('Native session store has no verified profile owner')
    return owner, bool(policy.get('allow_legacy_unowned') and owner == 'default')


def assert_store_ownership(db_path):
    policy = ownership_policy(db_path)
    path = Path(db_path).resolve()
    if policy is None or not path.is_file() or path.stat().st_size == 0:
        return
    owner, legacy = policy
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        columns = {r[1] for r in db.execute('PRAGMA table_info(sessions)')}
        if not columns:
            return
        if 'profile_name' not in columns:
            if legacy:
                return
            raise PermissionError('Native session ownership is unavailable')
        allowed = "profile_name=?" + (" OR profile_name IS NULL OR profile_name=''" if legacy else '')
        if db.execute('SELECT 1 FROM sessions WHERE NOT (' + allowed + ') '
                      + ('' if legacy else 'OR profile_name IS NULL ')
                      + 'LIMIT 1', (owner,)).fetchone():
            raise PermissionError('Mixed-profile native store requires an explicit ownership migration')


def assert_session_write(db, *, profile_name=None, parent_session_id=None):
    policy = ownership_policy(db.db_path)
    if policy is None:
        return
    owner, legacy = policy
    if profile_name and profile_name != owner:
        raise PermissionError('Session owner does not match its native store')
    if parent_session_id:
        parent = db.get_session(parent_session_id)
        if parent is None or (parent.get('profile_name') != owner
                              and not (legacy and not parent.get('profile_name'))):
            raise PermissionError('Parent session belongs to another profile or is unavailable')
