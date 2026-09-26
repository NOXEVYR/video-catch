"""Native macOS bundle build. Run on an arm64 or x86_64 Mac matching --arch."""
import argparse
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import uuid
import zipfile

from prepare_runtime_macos import architecture, digest, prepare

ROOT = Path(__file__).resolve().parent
VERSION = "0.5.0"
PACKAGES = ["yt-dlp", "yt-dlp-ejs", "imageio-ffmpeg", "pyinstaller", "pyobjc-core",
            "pyobjc-framework-Cocoa", "pyobjc-framework-Quartz", "certifi", "requests",
            "urllib3", "mutagen", "brotli", "pycryptodomex", "websockets", "charset-normalizer", "idna"]
DOCUMENTS = ["README.md", "使用指南.html", "AI接口使用说明.md", "TEST-REPORT.md",
             "RELEASE-NOTES.md", "SOURCE-PROVENANCE.json", "videocatch_client.py", "collaboration.py", "runtime_paths.py"]


def run(command, **kwargs):
    return subprocess.run([str(x) for x in command], check=True, **kwargs)


def require_arch(path, arch):
    found = subprocess.check_output(["lipo", "-archs", str(path)], text=True).split()
    if arch not in found:
        raise RuntimeError(f"{path.name} does not contain {arch}: {found}")


def make_icon(work):
    iconset = work / "VideoCatch.iconset"
    iconset.mkdir()
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
            run(["sips", "-z", size * scale, size * scale, ROOT / "assets/videocatch.png", "--out", iconset / name], stdout=subprocess.DEVNULL)
    icon = work / "VideoCatch.icns"
    run(["iconutil", "-c", "icns", "-o", icon, iconset])
    return icon


def file_manifest(target):
    files = {}
    for path in sorted(target.rglob("*")):
        name = path.relative_to(target).as_posix()
        if name == "runtime-manifest.json":
            continue
        if path.is_symlink():
            if not path.resolve(strict=True).is_relative_to(target.resolve()):
                raise RuntimeError(f"Bundle link escapes package: {name}")
            files[name] = {"type": "symlink", "target": os.readlink(path)}
        elif path.is_file():
            files[name] = {"type": "file", "bytes": path.stat().st_size, "sha256": digest(path), "mode": stat.S_IMODE(path.stat().st_mode)}
    return files


def write_archive(target, archive):
    """Preserve .app symlinks and Unix executable modes (ZipFile.write follows links)."""
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for path in sorted(target.rglob("*")):
            name = "VideoCatch/" + path.relative_to(target).as_posix()
            if path.is_symlink():
                item = zipfile.ZipInfo(name)
                item.create_system = 3
                item.external_attr = (stat.S_IFLNK | 0o777) << 16
                output.writestr(item, os.readlink(path).encode("utf-8"))
            elif path.is_file():
                output.write(path, name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", choices=("arm64", "x86_64", "x64"))
    args = parser.parse_args()
    arch = architecture(args.arch)
    if sys.platform != "darwin" or architecture() != arch:
        raise SystemExit("Build on a Mac/Python installation matching the target architecture")
    import imageio_ffmpeg
    work = Path(os.environ.get("VIDEOCATCH_BUILD_DIR", ROOT / ".build")).resolve()
    stage = work / (f"macos-{arch}-" + uuid.uuid4().hex[:10])
    stage.mkdir(parents=True)
    deno = prepare(arch, work)
    tools = stage / "tools"
    tools.mkdir()
    ffmpeg = tools / "ffmpeg"
    shutil.copy2(imageio_ffmpeg.get_ffmpeg_exe(), ffmpeg)
    ffmpeg.chmod(0o755)
    for binary in (ffmpeg, deno):
        require_arch(binary, arch)
    icon = make_icon(stage)
    target = stage / "package" / "VideoCatch"
    target.mkdir(parents=True)
    info = {"CFBundleDisplayName": "拾影 VideoCatch", "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION, "LSMinimumSystemVersion": "14.0", "NSHighResolutionCapable": True,
            "NSCameraUsageDescription": "拾影仅在您选择摄像头录制或画中画时使用摄像头。",
            "NSMicrophoneUsageDescription": "拾影仅在您选择麦克风音源时录制声音。",
            "NSScreenCaptureUsageDescription": "拾影仅在您开始录屏或截图时采集所选屏幕画面。"}
    spec = stage / "VideoCatch-macos.spec"
    spec.write_text(f'''from PyInstaller.utils.hooks import collect_all
datas = [({str(ROOT / 'assets')!r}, 'assets')]
binaries = [({str(ffmpeg)!r}, 'tools'), ({str(deno)!r}, 'tools')]
hiddenimports = ['objc', 'AppKit', 'Quartz', 'Foundation']
for package in ('yt_dlp', 'yt_dlp_ejs'):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h
a = Analysis([{str(ROOT / 'app.py')!r}], pathex=[{str(ROOT)!r}], binaries=binaries,
             datas=datas, hiddenimports=hiddenimports, excludes=['imageio_ffmpeg', 'pyaudiowpatch'])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='VideoCatch', console=False,
          target_arch={arch!r}, codesign_identity=None, entitlements_file=None)
coll = COLLECT(exe, a.binaries, a.datas, name='VideoCatch')
app = BUNDLE(coll, name='VideoCatch.app', icon={str(icon)!r},
             bundle_identifier='io.github.noxevyr.videocatch', info_plist={info!r})
''', encoding="utf-8")
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", stage / "dist",
         "--workpath", stage / "pyinstaller", spec], cwd=ROOT)
    shutil.copytree(stage / "dist/VideoCatch.app", target / "VideoCatch.app", symlinks=True)
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--console",
         "--name", "VideoCatchAI", "--target-architecture", arch, "--distpath", target,
         "--workpath", stage / "client", "--specpath", stage, ROOT / "videocatch_client.py"], cwd=ROOT)
    for name in DOCUMENTS:
        shutil.copy2(ROOT / name, target / name)
    shutil.copytree(ROOT / "extension", target / "extension")
    licenses = target / "licenses"
    shutil.copytree(ROOT / "licenses", licenses)
    for package in PACKAGES:
        distribution = metadata.distribution(package)
        for file in distribution.files or ():
            if "license" in file.name.lower() or "copying" in file.name.lower():
                source = Path(distribution.locate_file(file))
                if source.is_file():
                    shutil.copy2(source, licenses / f"{package}-{file.name}")
    for location in (Path(sys.base_prefix) / "LICENSE.txt", Path(sys.base_prefix) / "Resources/English.lproj/License.rtf"):
        if location.is_file():
            shutil.copy2(location, licenses / ("Python-" + location.name))
    (licenses / "FFmpeg-build.txt").write_bytes(subprocess.check_output([str(ffmpeg), "-version"]))
    (licenses / "FFmpeg-license.txt").write_bytes(subprocess.check_output([str(ffmpeg), "-L"], stderr=subprocess.STDOUT))
    (licenses / "THIRD_PARTY-macOS.md").write_text(
        "# macOS components\n\nPython (PSF), Tcl/Tk (BSD), PyObjC (MIT), yt-dlp and yt-dlp-ejs (Unlicense), "
        "PyInstaller (GPL with bootloader exception), Deno 2.9.6 (MIT), imageio-ffmpeg (BSD-2-Clause).\n\n"
        "FFmpeg is redistributed from the unmodified imageio-ffmpeg wheel; its enabled components and license are included. "
        "Build/source: https://github.com/imageio/imageio-binaries and https://ffmpeg.org/download.html .\n",
        encoding="utf-8")
    (target / "macOS-开始使用.txt").write_text(
        "拾影 VideoCatch 0.5.0 · macOS 14 或更新版本\n\n完整解压并保留 VideoCatch 文件夹。双击 VideoCatch.app。"
        "VideoCatchAI 与文档、extension 须留在 app 旁边；请勿只移动 app。\n"
        "本包未进行 Developer ID 签名或 Apple 公证。首次启动若被系统阻止，请核实 GitHub 下载来源，"
        "在系统设置的隐私与安全性中按系统提示允许打开。\n"
        "录屏、摄像头和麦克风需要分别按 macOS 提示授权。CI 合成媒体检查不代表实际权限、"
        "摄像头、麦克风或屏幕录制已人工验收。系统声音可能需要另行选择可用的音频回环设备。\n",
        encoding="utf-8")
    source_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    manifest = {"version": VERSION, "platform": "macOS", "minimum_macos": "14.0", "architecture": arch, "python": sys.version.split()[0],
                "source_commit": source_commit, "source_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)),
                "components": {**{p: metadata.version(p) for p in PACKAGES}, "deno": "2.9.6"},
                "codesigning": "ad-hoc; no Developer ID or notarization", "files": file_manifest(target)}
    (target / "runtime-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    release = ROOT / "releases"
    release.mkdir(exist_ok=True)
    archive = release / f"VideoCatch-v{VERSION}-macOS-{arch}.zip"
    write_archive(target, archive)
    with zipfile.ZipFile(archive) as zipped:
        if zipped.testzip() is not None:
            raise RuntimeError("Final ZIP CRC failed")
    checksum = {"file": archive.name, "bytes": archive.stat().st_size, "sha256": digest(archive),
                "root": "VideoCatch", "version": VERSION, "architecture": arch, "zip_integrity": "passed", "source_commit": source_commit}
    (release / f"checksums-v{VERSION}-macOS-{arch}.json").write_text(json.dumps(checksum, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(checksum, indent=2))


if __name__ == "__main__":
    main()
