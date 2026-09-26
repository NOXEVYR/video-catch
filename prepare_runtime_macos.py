"""Prepare Deno from the official, hash-pinned macOS archive; never execute it."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parent


def architecture(value=None):
    value = value or platform.machine()
    aliases = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x86_64", "AMD64": "x86_64", "x64": "x86_64"}
    if value not in aliases:
        raise ValueError("Supported macOS architectures: arm64, x86_64")
    return aliases[value]


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def prepare(arch=None, work=None):
    arch = architecture(arch)
    entry = json.loads((ROOT / "runtime-macos.lock.json").read_text(encoding="utf-8"))["deno"]
    asset = entry["architectures"][arch]
    work = Path(work or os.environ.get("VIDEOCATCH_BUILD_DIR", ROOT / ".build")).resolve()
    cache = work / "deno-download" / f"deno-{entry['version']}-macos-{arch}.zip"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.is_file() or cache.stat().st_size != asset["bytes"] or digest(cache) != asset["sha256"]:
        request = Request(asset["url"], headers={"User-Agent": "VideoCatch-build"})
        with urlopen(request, timeout=90) as response:
            data = response.read(asset["bytes"] + 1)
        if len(data) != asset["bytes"] or hashlib.sha256(data).hexdigest() != asset["sha256"]:
            raise RuntimeError("Official Deno archive size/SHA-256 mismatch; refusing to prepare it")
        cache.write_bytes(data)
    binary = work / f"tools-macos-{arch}" / "deno"
    binary.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(cache) as archive:
        if archive.testzip() is not None or archive.namelist() != ["deno"]:
            raise RuntimeError("Unexpected or corrupt Deno archive")
        # Extract only the known executable, never arbitrary archive destinations.
        binary.write_bytes(archive.read("deno"))
    binary.chmod(0o755)
    print(f"Deno {entry['version']} macOS {arch}: official archive SHA-256 verified")
    return binary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", choices=("arm64", "x86_64", "x64"))
    args = parser.parse_args()
    prepare(args.arch)


if __name__ == "__main__":
    main()
