"""Offline installer contracts; scanner execution is validated in an isolated image."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("forensic_installer", ROOT / "tools/install_forensic_tools.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class ForensicPackagesTest(unittest.TestCase):
    def test_cached_asset_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            (cache / "asset").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                installer.checked_asset(cache, {"filename": "asset", "sha256": "0" * 64})

    def test_zip_escape_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as stream:
                stream.writestr("../escape", "bad")
            with self.assertRaisesRegex(ValueError, "Unsafe archive"):
                installer.unpack(archive, root / "destination")
            self.assertFalse((root / "escape").exists())

    def test_tar_link_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "bad.tar.gz"
            with tarfile.open(archive, "w:gz") as stream:
                member = tarfile.TarInfo("yr")
                member.type = tarfile.SYMTYPE
                member.linkname = "/etc/passwd"
                stream.addfile(member)
            with self.assertRaisesRegex(ValueError, "Non-regular archive"):
                installer.unpack(archive, root / "destination")

    def test_install_uses_cache_only_and_preserves_rule_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "hayabusa.zip"
            with zipfile.ZipFile(archive, "w") as stream:
                for name, data in {"hayabusa-test": "fixture", "rules/test.yml": "rule source", "config/test.yaml": "config"}.items():
                    stream.writestr(name, data)
            yr = root / "yara.tar.gz"
            with tarfile.open(yr, "w:gz") as stream:
                member = tarfile.TarInfo("yr")
                member.size = 7
                stream.addfile(member, io.BytesIO(b"fixture"))
            def asset(path):
                return {"filename": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            lock = {"packages": {name: {"assets": {"amd64": asset(path)}} for name, path in [("hayabusa", archive), ("yara-x", yr)]}, "licenses": []}
            with patch.object(installer.urllib.request, "urlopen", side_effect=AssertionError("network forbidden")):
                installer.install(lock, root, root / "installed", "amd64")
            prefix = root / "installed"
            self.assertTrue((prefix / "bin/hayabusa").is_file())
            self.assertTrue((prefix / "bin/yr").is_file())
            hashes = json.loads((prefix / "rule-config-sha256.json").read_text())
            self.assertEqual(hashes["hayabusa/rules/test.yml"], hashlib.sha256(b"rule source").hexdigest())
            with self.assertRaisesRegex(ValueError, "already exists"):
                installer.install(lock, root, prefix, "amd64")


if __name__ == "__main__":
    unittest.main()
