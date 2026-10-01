"""Conservative Windows x64 stable-release discovery and package verification.

No GUI, implicit download, process control, or user-directory mutation lives here.
The caller runs check/stage off the Tk thread and supplies explicit consent for
archives above 50 MiB. Only complete archives are downloaded; Range is unused.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import platform
import re
import sys
import time
from urllib.request import Request, urlopen
import zipfile


REPO = "NOXEVYR/video-catch"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET_BASE = f"https://github.com/{REPO}/releases/download"
MAX_AUTOMATIC_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_UNPACKED_BYTES = 3 * 1024 * 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024
SUCCESS_INTERVAL = 24 * 3600
FAILURE_BASE = 3600
FAILURE_MAX = 24 * 3600
VERSION_RE = re.compile(r"^([0-9]+)\.([0-9]+)\.([0-9]+)(?:-rc\.([0-9]+))?$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class UpdateError(RuntimeError):
    """A refused or failed update step; safe to show to the user."""


def windows_x64() -> bool:
    return sys.platform == "win32" and platform.machine().lower() in {"amd64", "x86_64"}


def version_key(version: str) -> tuple[int, int, int, int, int]:
    match = VERSION_RE.fullmatch(version)
    if not match:
        raise UpdateError(f"无法识别的版本号：{version}")
    major, minor, patch = map(int, match.group(1, 2, 3))
    return major, minor, patch, 0 if match.group(4) is None else -1, int(match.group(4) or 0)


def _digest(value: object) -> str | None:
    if isinstance(value, str) and value.startswith("sha256:"):
        value = value[7:]
    return value.lower() if isinstance(value, str) and SHA256_RE.fullmatch(value.lower()) else None


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json_url(url: str, *, opener=urlopen) -> dict:
    if not (url == API or url.startswith(ASSET_BASE + "/")):
        raise UpdateError("更新元数据来源不受信任")
    request = Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "VideoCatch-Updater"})
    with opener(request, timeout=15) as response:
        raw = response.read(MAX_METADATA_BYTES + 1)
    if len(raw) > MAX_METADATA_BYTES:
        raise UpdateError("更新元数据过大")
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise UpdateError("更新元数据不是有效 JSON") from exc
    if not isinstance(data, dict):
        raise UpdateError("更新元数据结构无效")
    return data


@dataclass(frozen=True)
class Release:
    version: str
    tag: str
    asset_name: str
    asset_url: str
    bytes: int
    sha256: str
    digest_source: str


def select_release(metadata: dict, *, opener=urlopen) -> Release:
    """Bind platform, tag, asset URL, size and a trusted GitHub SHA-256."""
    if metadata.get("draft") or metadata.get("prerelease"):
        raise UpdateError("只接受官方稳定版")
    tag = metadata.get("tag_name")
    if not isinstance(tag, str) or not tag.startswith("v"):
        raise UpdateError("官方发布标签无效")
    version = tag[1:]
    if version_key(version)[3] != 0:
        raise UpdateError("只接受官方稳定版")
    asset_name = f"VideoCatch-v{version}-Windows-x64.zip"
    expected_url = f"{ASSET_BASE}/{tag}/{asset_name}"
    assets = metadata.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("官方发布缺少资产列表")
    matches = [asset for asset in assets if isinstance(asset, dict) and asset.get("name") == asset_name]
    if len(matches) != 1:
        raise UpdateError("官方发布缺少唯一 Windows x64 程序包")
    asset = matches[0]
    size = asset.get("size")
    if (asset.get("browser_download_url") != expected_url or type(size) is not int or
            not 0 < size <= MAX_ARCHIVE_BYTES):
        raise UpdateError("官方程序包名称、来源或大小无效")
    digest = _digest(asset.get("digest"))
    source = "GitHub asset digest"
    if not digest:
        checksum_name = f"checksums-v{version}.json"
        checksum_url = f"{ASSET_BASE}/{tag}/{checksum_name}"
        checksum_assets = [item for item in assets if isinstance(item, dict) and item.get("name") == checksum_name]
        if len(checksum_assets) != 1 or checksum_assets[0].get("browser_download_url") != checksum_url:
            raise UpdateError("发布资产没有可信 SHA-256 摘要")
        checksum_digest = _digest(checksum_assets[0].get("digest"))
        if not checksum_digest:
            raise UpdateError("校验文件没有可信 GitHub 摘要")
        # The checksum asset is tiny and independently bound by GitHub's asset digest.
        request = Request(checksum_url, headers={"User-Agent": "VideoCatch-Updater"})
        with opener(request, timeout=15) as response:
            raw = response.read(MAX_METADATA_BYTES + 1)
        if len(raw) > MAX_METADATA_BYTES or hashlib.sha256(raw).hexdigest() != checksum_digest:
            raise UpdateError("官方校验文件摘要不匹配")
        try:
            checksum = json.loads(raw)
        except ValueError as exc:
            raise UpdateError("官方校验文件格式无效") from exc
        if (not isinstance(checksum, dict) or checksum.get("file") != asset_name or
                checksum.get("bytes") != size or checksum.get("root") != "VideoCatch"):
            raise UpdateError("官方校验文件与 Windows x64 程序包不匹配")
        digest = _digest(checksum.get("sha256"))
        source = "GitHub-bound official checksums asset"
    if not digest:
        raise UpdateError("发布资产没有可信 SHA-256 摘要")
    return Release(version, tag, asset_name, expected_url, size, digest, source)


def safe_name(name: str) -> str:
    """Validate a portable manifest path before it can address the install tree."""
    if (not isinstance(name, str) or not name or len(name) > 1024 or "\\" in name or ":" in name or
            name.startswith("/") or any(ord(char) < 32 for char in name)):
        raise UpdateError("安装包包含不安全的文件路径")
    parts = name.split("/")
    if any(part in ("", ".", "..") or part.endswith((" ", ".")) for part in parts):
        raise UpdateError("安装包包含不安全的文件路径")
    if any(part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                                            *(f"LPT{i}" for i in range(1, 10))} for part in parts):
        raise UpdateError("安装包包含 Windows 保留文件名")
    if PurePosixPath(name).as_posix() != name:
        raise UpdateError("安装包路径未规范化")
    return name


def _manifest(data: object, version: str) -> dict[str, dict]:
    if not isinstance(data, dict) or data.get("version") != version or not isinstance(data.get("files"), dict):
        raise UpdateError("安装包清单版本或结构不匹配")
    files = data["files"]
    if not files or len(files) > 20000:
        raise UpdateError("安装包文件数量无效")
    folded = set()
    total = 0
    for name, entry in files.items():
        safe_name(name)
        key = name.casefold()
        if key in folded or not isinstance(entry, dict) or type(entry.get("bytes")) is not int or entry["bytes"] < 0 or not _digest(entry.get("sha256")):
            raise UpdateError("安装包文件清单无效或存在大小写冲突")
        folded.add(key)
        total += entry["bytes"]
        if total > MAX_UNPACKED_BYTES:
            raise UpdateError("安装包解压尺寸超过上限")
    for name in files:
        parts = name.split("/")
        if any("/".join(parts[:index]).casefold() in folded for index in range(1, len(parts))):
            raise UpdateError("安装包清单存在文件与目录路径冲突")
    if not {"VideoCatch.exe", "VideoCatchAI.exe", "version.py"}.issubset(files):
        raise UpdateError("安装包缺少必需的 Windows 程序文件")
    return files


@dataclass(frozen=True)
class VerifiedPackage:
    path: Path
    release: Release
    files: dict[str, dict]


def verify_package(path: Path, release: Release) -> VerifiedPackage:
    path = Path(path)
    if path.stat().st_size != release.bytes or file_sha256(path) != release.sha256:
        raise UpdateError("程序包大小或官方 SHA-256 不匹配")
    try:
        with zipfile.ZipFile(path) as archive:
            entries = {}
            folded = set()
            for info in archive.infolist():
                if info.is_dir():
                    directory = info.filename.rstrip("/")
                    if directory != "VideoCatch" and (not directory.startswith("VideoCatch/") or
                                                          safe_name(directory) != directory):
                        raise UpdateError("安装包包含不安全的目录路径")
                    continue
                name = safe_name(info.filename)
                if (not name.startswith("VideoCatch/") or name.casefold() in folded or
                        (info.external_attr >> 16) & 0o170000 == 0o120000):
                    raise UpdateError("安装包根目录、重复项或符号链接无效")
                folded.add(name.casefold())
                entries[name.removeprefix("VideoCatch/")] = info
            manifest_info = entries.pop("runtime-manifest.json", None)
            if manifest_info is None or manifest_info.file_size > MAX_METADATA_BYTES:
                raise UpdateError("安装包缺少有效的运行时清单")
            data = json.loads(archive.read(manifest_info))
            files = _manifest(data, release.version)
            if set(entries) != set(files):
                raise UpdateError("安装包文件与逐文件清单不一致")
            for name, info in entries.items():
                expected = files[name]
                if info.file_size != expected["bytes"]:
                    raise UpdateError(f"文件尺寸不匹配：{name}")
                digest = hashlib.sha256()
                with archive.open(info) as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
                if digest.hexdigest() != expected["sha256"].lower():
                    raise UpdateError(f"文件摘要不匹配：{name}")
            version_text = archive.read(entries["version.py"]).decode("utf-8")
            if not re.search(r'^VERSION\s*=\s*["\']' + re.escape(release.version) + r'["\']\s*$', version_text, re.MULTILINE):
                raise UpdateError("程序版本文件与发布标签不一致")
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError, ValueError, RuntimeError, OverflowError) as exc:
        raise UpdateError("程序包 ZIP 或运行时清单无效") from exc
    return VerifiedPackage(path, release, files)


class Updater:
    def __init__(self, state_dir: Path, current_version: str, install_root: Path, *, opener=urlopen, clock=time.time):
        self.state_dir = Path(state_dir)
        self.install_root = Path(install_root)
        self.current_version = current_version
        version_key(current_version)
        self.opener = opener
        self.clock = clock
        self.state_file = self.state_dir / "update-state.json"

    def _read_state(self) -> dict:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            if (not isinstance(data, dict) or type(data.get("next_check", 0)) not in (int, float) or
                    not math.isfinite(data.get("next_check", 0)) or type(data.get("failures", 0)) is not int or
                    data.get("failures", 0) < 0):
                return {}
            return data
        except (OSError, ValueError):
            return {}

    def _write_state(self, state: dict) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        temp = self.state_file.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as stream:
            json.dump(state, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, self.state_file)

    def check(self, *, manual: bool = False) -> dict:
        """Metadata-only check. Caller schedules it after UI readiness, off Tk."""
        if not windows_x64():
            return {"status": "unsupported", "error": "自动更新目前仅支持 Windows x64"}
        now = self.clock()
        state = self._read_state()
        if not manual and now < state.get("next_check", 0):
            return {"status": "throttled", "next_check": state["next_check"]}
        try:
            metadata = _read_json_url(API, opener=self.opener)
            release = select_release(metadata, opener=self.opener)
            state.update(failures=0, next_check=now + SUCCESS_INTERVAL, last_check=now)
            self._write_state(state)
            return {"status": "available" if version_key(release.version) > version_key(self.current_version) else "current",
                    "release": release}
        except (UpdateError, OSError, TimeoutError) as exc:
            failures = min(max(int(state.get("failures", 0)) + 1, 1), 24)
            delay = min(FAILURE_MAX, FAILURE_BASE * (2 ** (failures - 1)))
            state.update(failures=failures, next_check=now + delay, last_check=now)
            try:
                self._write_state(state)
            except OSError:
                return {"status": "error", "error": f"{exc}；更新检查状态也无法保存，请检查磁盘和权限"}
            return {"status": "error", "error": str(exc), "next_check": state["next_check"]}

    def stage(self, release: Release, *, allow_large: bool = False) -> VerifiedPackage:
        """Download a complete archive, only after size is known and consented."""
        if not windows_x64():
            raise UpdateError("自动更新目前仅支持 Windows x64")
        if (version_key(release.version) <= version_key(self.current_version) or
                version_key(release.version)[3] != 0 or release.tag != "v" + release.version or
                release.asset_name != f"VideoCatch-v{release.version}-Windows-x64.zip" or
                release.asset_url != f"{ASSET_BASE}/{release.tag}/{release.asset_name}" or
                not _digest(release.sha256) or not 0 < release.bytes <= MAX_ARCHIVE_BYTES):
            raise UpdateError("下载目标与已核实的官方 Windows x64 稳定版不匹配")
        if release.bytes > MAX_AUTOMATIC_BYTES and not allow_large:
            raise UpdateError(f"程序包 {release.bytes} 字节超过 50 MiB；需要明确选择下载")
        folder = self.state_dir / "updates"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / release.asset_name
        if target.is_file():
            try:
                return verify_package(target, release)
            except UpdateError:
                # Preserve suspect package for inspection; never silently trust it.
                raise UpdateError("已有暂存程序包校验失败，请手动移走后重试") from None
        part = folder / (release.asset_name + ".part")
        if part.exists():
            raise UpdateError("存在未完成的下载；请先检查或移走 .part 文件")
        request = Request(release.asset_url, headers={"User-Agent": "VideoCatch-Updater", "Accept": "application/octet-stream"})
        try:
            with self.opener(request, timeout=30) as response, part.open("xb") as output:
                if getattr(response, "status", 200) != 200 or response.headers.get("Content-Range"):
                    raise UpdateError("服务器未返回完整程序包；不会以整包回退重试")
                announced = response.headers.get("Content-Length")
                if announced and int(announced) != release.bytes:
                    raise UpdateError("服务器公布的下载大小与官方资产不一致")
                total = 0
                digest = hashlib.sha256()
                for block in iter(lambda: response.read(1024 * 1024), b""):
                    total += len(block)
                    if total > release.bytes:
                        raise UpdateError("下载超出官方公布大小")
                    output.write(block)
                    digest.update(block)
                output.flush()
                os.fsync(output.fileno())
            if total != release.bytes or digest.hexdigest() != release.sha256:
                raise UpdateError("下载大小或官方 SHA-256 不匹配")
            os.replace(part, target)
            return verify_package(target, release)
        except Exception:
            part.unlink(missing_ok=True)
            raise

    def prepare(self, package: VerifiedPackage):
        """Preflight owned-file differences before asking the app to exit."""
        from update_installer import prepare_update
        return prepare_update(package, self.install_root, self.state_dir)
