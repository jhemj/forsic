#!/usr/bin/env python3
"""Build preparation: fetch pinned assets, then install from verified cache offline.

python tools/install_forensic_tools.py fetch --cache /tmp/forensic-cache
python tools/install_forensic_tools.py install --cache /tmp/forensic-cache --prefix /opt/forensic-tools
Add /opt/forensic-tools/bin to PATH. Never fetch inside an evidence runtime.
"""
import argparse
import hashlib
import json
import os
import platform
from pathlib import Path, PurePosixPath
import shutil
import stat
import tarfile
import tempfile
import urllib.request
import zipfile


LOCK = Path(__file__).with_name("forensic-tools.lock.json")
SENSITIVE = Path(__file__).with_name("sensitive")


def sensitive_assets(lock, arch):
    return [package["assets"].get(arch, package["assets"].get("any"))
            for package in lock.get("sensitive_python", {}).get("packages", {}).values()]


def check_trusted_sensitive(lock):
    for filename, digest in lock.get("sensitive_python", {}).get("trusted_files", {}).items():
        if hashlib.sha256((SENSITIVE / filename).read_bytes()).hexdigest() != digest:
            raise ValueError(f"Trusted sensitive configuration mismatch: {filename}")


def checked_asset(cache, asset):
    path = cache / asset["filename"]
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != asset["sha256"]:
        raise ValueError(f"SHA256 mismatch: {path}")
    return path


def unpack(archive, destination):
    """Extract only regular files/directories; reject paths outside destination."""
    def valid(name):
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name:
            raise ValueError(f"Unsafe archive member: {name}")
        return destination / path

    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as source:
            for member in source.infolist():
                path = valid(member.filename)
                if stat.S_ISLNK(member.external_attr >> 16):
                    raise ValueError(f"Archive symlink: {member.filename}")
                if member.is_dir():
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with source.open(member) as src, path.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
    else:
        with tarfile.open(archive) as source:
            for member in source:
                path = valid(member.name)
                if member.isdir():
                    path.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with source.extractfile(member) as src, path.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                else:
                    raise ValueError(f"Non-regular archive member: {member.name}")


def install(lock, cache, prefix, arch):
    if prefix.exists():
        raise ValueError(f"Install prefix already exists: {prefix}; use a new build directory")
    # Verify every asset before creating any installation files.
    selected = {name: package["assets"][arch] for name, package in lock["packages"].items()}
    wheels = sensitive_assets(lock, arch)
    for asset in [*selected.values(), *lock["licenses"], *wheels]:
        checked_asset(cache, asset)
    check_trusted_sensitive(lock)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=prefix.parent) as temporary:
        stage = Path(temporary) / "install"
        (stage / "bin").mkdir(parents=True)
        for name, asset in selected.items():
            unpack(checked_asset(cache, asset), stage / name)
        binary = next((stage / "hayabusa").glob("hayabusa-*"))
        binary.chmod(0o755)
        (stage / "yara-x/yr").chmod(0o755)
        (stage / "bin/hayabusa").symlink_to(Path("../hayabusa") / binary.name)
        (stage / "bin/yr").symlink_to("../yara-x/yr")
        if "gitleaks" in selected:
            executable = stage / "gitleaks/gitleaks"
            if hashlib.sha256(executable.read_bytes()).hexdigest() != selected["gitleaks"]["binary_sha256"]:
                raise ValueError("Gitleaks binary SHA256 mismatch")
            executable.chmod(0o755)
            (stage / "bin/gitleaks").symlink_to("../gitleaks/gitleaks")
        if wheels:
            (stage / "sensitive").mkdir()
            for filename in lock["sensitive_python"]["trusted_files"]:
                shutil.copyfile(SENSITIVE / filename, stage / "sensitive" / filename)
            # Install exact wheel payloads without resolving heavyweight optional
            # runtime dependencies. The supported loader verifies and imports only
            # the selected modules; CPython 3.12 is enforced by the analysis image.
            for asset in wheels:
                unpack(checked_asset(cache, asset), stage / "sensitive/python")
        notices = stage / "licenses"
        notices.mkdir()
        for asset in lock["licenses"]:
            shutil.copyfile(checked_asset(cache, asset), notices / asset["filename"])
        shutil.copyfile(LOCK, stage / LOCK.name)
        # Per-file source hashes allow exact rule/config snapshots to be cited offline.
        hashes = {}
        for directory in (stage / "hayabusa/rules", stage / "hayabusa/config", stage / "sensitive"):
            for path in sorted(directory.rglob("*")):
                if path.is_file():
                    with path.open("rb") as stream:
                        hashes[str(path.relative_to(stage))] = hashlib.file_digest(stream, "sha256").hexdigest()
        (stage / "rule-config-sha256.json").write_text(json.dumps(hashes, indent=2) + "\n")
        os.rename(stage, prefix)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("fetch", "install"))
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--prefix", "--dest", type=Path, default=Path("/opt/forensic-tools"),
                        help="installation directory, including a prepared Docker build context")
    parser.add_argument("--arch", choices=("amd64", "arm64"))
    args = parser.parse_args()
    arch = args.arch or {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine())
    if platform.system() != "Linux" or arch is None:
        parser.error("only Linux amd64/arm64 assets are pinned")
    lock = json.loads(LOCK.read_text())
    if args.action == "fetch":
        args.cache.mkdir(parents=True, exist_ok=True)
        assets = ([p["assets"][arch] for p in lock["packages"].values()]
                  + lock["licenses"] + sensitive_assets(lock, arch))
        for asset in assets:
            path = args.cache / asset["filename"]
            if not path.exists():
                partial = path.with_suffix(path.suffix + ".partial")
                with urllib.request.urlopen(asset["url"], timeout=120) as src, partial.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                try:
                    if hashlib.sha256(partial.read_bytes()).hexdigest() != asset["sha256"]:
                        raise ValueError(f"SHA256 mismatch: {partial}")
                    partial.rename(path)
                finally:
                    partial.unlink(missing_ok=True)
            checked_asset(args.cache, asset)
            print(f"verified {asset['filename']}")
    else:
        install(lock, args.cache, args.prefix, arch)
        print(f"installed pinned scanners at {args.prefix}; add its bin/ to PATH")


if __name__ == "__main__":
    main()
