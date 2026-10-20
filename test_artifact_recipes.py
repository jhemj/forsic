"""Opt-in offline parser smoke checks; no evidence mounts or network.

Run FORSIC_ARTIFACT_RUNTIME_TEST=1 python3 -m unittest test_artifact_recipes -v.
The existing image is required; this test never pulls/builds it.
"""
import os
import subprocess
import unittest


RUNTIME_CHECK = r'''
import importlib.metadata as metadata
import io
import json
import pathlib
import subprocess
import tempfile
from datetime import datetime, timezone
from dissect.target.target import Target
from dissect.target.filesystem import VirtualFilesystem
from dissect.target.helpers.regutil import VirtualHive, VirtualKey, HiveCollection, RegfHive
from dissect.target.plugins.os.windows.amcache import AmcachePlugin
from dissect.target.plugins.os.windows.prefetch import Prefetch, c_prefetch
from dissect.target.plugins.os.unix.log.utmp import UtmpFile, c_utmp

assert metadata.version("dissect.target") == "3.25.1"
for command in (["target-query", "--help"], ["target-reg", "--help"],
                ["target-query", "-f", "prefetch", "--help"],
                ["target-query", "-f", "amcache.application_files", "--help"],
                ["target-query", "-f", "authlog", "--help"],
                ["target-query", "-f", "utmp", "--help"]):
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout

# Real Prefetch binary parser, uncompressed version 30 and no linked metrics.
header = c_prefetch.PREFETCH_HEADER()
header.version = 30
header.signature = b"SCCA"
header.name = "SYNTHETIC.EXE".encode("utf-16-le").ljust(60, b"\0")
info = c_prefetch.FILE_INFORMATION_26()
info.last_run_time = 133485408000000000  # 2024-01-01 UTC FILETIME
info.run_count = 3
binary = header.dumps() + info.dumps()
pf = Prefetch(io.BytesIO(binary))
assert pf.version == 30 and pf.fn.run_count == 3
assert pf.latest_timestamp == datetime(2024, 1, 1, tzinfo=timezone.utc)
assert pf.previous_timestamps == [] and pf.metrics == []
try:
    Prefetch(io.BytesIO(b"invalid"))
except Exception:
    pass
else:
    raise AssertionError("corrupt PF was accepted")

# Amcache parsing over in-memory Registry structures; not a binary hive test.
hive = VirtualHive()
key = VirtualKey(hive, r"Root\InventoryApplicationFile\synthetic")
key.add_value("Name", "synthetic.exe")
key.add_value("LowerCaseLongPath", r"c:\synthetic.exe")
key.add_value("FileId", "0000" + "a" * 40)
key.add_value("ProgramId", "synthetic-program")
hive.map_key(key.path, key)
target = Target()
target.filesystems.add(VirtualFilesystem())
target.apply()
plugin = AmcachePlugin(target)
plugin.amcache = HiveCollection([hive])
records = list(plugin.application_files())
assert len(records) == 1 and records[0].name == "synthetic.exe"
assert records[0].program_id == "synthetic-program"
assert key.value("Name").value == "synthetic.exe"

with tempfile.TemporaryDirectory() as temporary:
    root = pathlib.Path(temporary)
    (root / "etc").mkdir()
    (root / "var/log").mkdir(parents=True)
    (root / "etc/os-release").write_text('NAME="Synthetic Linux"\nID=debian\n')
    (root / "etc/passwd").write_text('root:x:0:0:root:/root:/bin/sh\n')
    (root / "etc/timezone").write_text('UTC\n')
    auth = root / "var/log/auth.log"
    auth.write_text('2024-01-01T00:00:00.000000+00:00 synthetic sshd[42]: Accepted password for testuser from 192.0.2.1 port 4242 ssh2\n')
    result = subprocess.run(["target-query", str(root), "-f", "authlog", "-j", "--no-cache"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    lines = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    lines = [line for line in lines if line.get("_type") == "record"]
    assert len(lines) == 1, (result.stdout, result.stderr)
    assert "testuser" in lines[0]["message"]
    assert "auth.log" in lines[0]["source"]
    assert lines[0]["ts"].startswith("2024-01-01T00:00:00")
    auth.write_text(auth.read_text().replace(".000000+00:00", "+00:00"))
    result = subprocess.run(["target-query", str(root), "-f", "authlog", "-j", "--no-cache"],
                            capture_output=True, text=True)
    assert result.returncode == 0
    assert not any(json.loads(line).get("_type") == "record" for line in result.stdout.splitlines())
    assert "No timestamp found" in result.stderr
    result = subprocess.run(["target-reg", str(root), "-k", r"HKLM\SOFTWARE"],
                            capture_output=True, text=True)
    assert result.returncode == 0 and "Target has no Windows Registry" in result.stderr

    entry = c_utmp.entry()
    entry.ut_type = 7
    entry.ut_pid = 42
    entry.ut_user = b"testuser"
    entry.ut_line = b"pts/0"
    entry.ut_host = b"192.0.2.1"
    entry.ut_tv.tv_sec = 1704067200
    entry.ut_tv.tv_usec = 123456
    wtmp = root / "var/log/wtmp"
    wtmp.write_bytes(entry.dumps())
    rows = list(UtmpFile(wtmp))
    assert len(rows) == 1 and rows[0].ut_user == "testuser"
    assert rows[0].ut_type == "USER_PROCESS"
    assert rows[0].ts.microsecond == 0  # parser drops tv_usec
    result = subprocess.run(["target-query", str(root), "-f", "utmp", "-j", "--no-cache"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert len([s for s in result.stdout.splitlines() if s.strip() and json.loads(s).get("_type") == "record"]) == 1
    wtmp.write_bytes(entry.dumps() + b"incomplete")
    assert wtmp.stat().st_size % c_utmp.entry.size != 0
    assert len(list(UtmpFile(wtmp))) == 1  # truncated tail silently EOFs

    invalid_hive = root / "invalid.hve"
    invalid_hive.write_bytes(b"invalid")
    try:
        with invalid_hive.open("rb") as fh:
            RegfHive(invalid_hive, fh=fh)
    except Exception:
        pass
    else:
        raise AssertionError("invalid hive was accepted")

print("PASS: pinned CLI help; binary PF; virtual Amcache/Registry; authlog+utmp JSON CLI; utmp tail/precision; corrupt PF/hive rejection")
'''


@unittest.skipUnless(os.environ.get("FORSIC_ARTIFACT_RUNTIME_TEST") == "1",
                     "set FORSIC_ARTIFACT_RUNTIME_TEST=1 for offline Docker checks")
class ArtifactRuntimeChecks(unittest.TestCase):
    def test_pinned_parser_entrypoints_on_synthetic_inputs(self):
        result = subprocess.run(
            ["docker", "run", "--pull=never", "--rm", "-i", "--network", "none",
             "--read-only", "--tmpfs", "/tmp",
             os.environ.get("FORSIC_ARTIFACT_TEST_IMAGE", "forsic-dissect:3.25.1"), "python", "-"],
            input=RUNTIME_CHECK, capture_output=True, text=True, timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS:", result.stdout)


if __name__ == "__main__":
    unittest.main()
