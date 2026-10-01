"""Reproducible inputs; self-contained local Windows distribution."""
import hashlib
import os
import importlib.metadata as metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

from version import VERSION
from windows_version import resource

ROOT = Path(__file__).resolve().parent
WORK = Path(os.environ.get("VIDEOCATCH_BUILD_DIR", ROOT / ".build"))
RELEASE = ROOT / "releases"
DIST = RELEASE / f"VideoCatch-v{VERSION}"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    # PyInstaller can silently omit tkinter when this check fails and still
    # report a successful build. Such a package cannot start the desktop app.
    import tkinter
    try:
        tkinter.Tcl()
    except tkinter.TclError as error:
        raise RuntimeError("Tcl/Tk 初始化失败，停止打包，避免生成无法启动的桌面包") from error
    RELEASE.mkdir(exist_ok=True)
    tools = WORK / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    if not (tools / "ffmpeg.exe").is_file():
        import imageio_ffmpeg
        shutil.copy2(imageio_ffmpeg.get_ffmpeg_exe(), tools / "ffmpeg.exe")
    for filename in ("VideoCatch.exe", "VideoCatchAI.exe"):
        (WORK / (filename + ".version.txt")).write_text(resource(filename), encoding="utf-8")
    subprocess.run([sys.executable, str(ROOT / "prepare_runtime.py")], check=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", "VideoCatch",
                    "--icon", str(ROOT / "assets" / "videocatch.ico"), "--version-file", str(WORK / "VideoCatch.exe.version.txt"), "--add-data", f"{ROOT / 'assets'};assets",
                    "--distpath", str(DIST), "--workpath", str(WORK / "pyinstaller"), "--specpath", str(WORK),
                    "--collect-all", "yt_dlp", "--collect-all", "yt_dlp_ejs", "--collect-all", "pyaudiowpatch", "--hidden-import", "_portaudiowpatch", "--exclude-module", "imageio_ffmpeg", "--add-binary", f"{tools / 'ffmpeg.exe'};tools", "--add-binary", f"{tools / 'deno.exe'};tools",
                    str(ROOT / "launcher.py")], check=True, cwd=ROOT)
    target = DIST / "VideoCatch"
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--console",
                    "--name", "VideoCatchAI", "--icon", str(ROOT / "assets" / "videocatch.ico"),
                    "--version-file", str(WORK / "VideoCatchAI.exe.version.txt"),
                    "--distpath", str(target), "--workpath", str(WORK / "client"), "--specpath", str(WORK),
                    str(ROOT / "videocatch_client.py")], check=True, cwd=ROOT)
    shutil.copytree(ROOT / "extension", target / "extension", dirs_exist_ok=True)
    shutil.copytree(ROOT / "assets", target / "assets", dirs_exist_ok=True)
    for name in ["README.md", "使用指南.html", "TEST-REPORT.md", "AI接口使用说明.md", "RELEASE-NOTES.md", "MIGRATION.md", "QUALITY-CHECKLIST.md", "LOCAL-CANDIDATE.md", "videocatch_client.py", "collaboration.py", "runtime_paths.py", "version.py", "SOURCE-PROVENANCE.json"]:
        shutil.copy2(ROOT / name, target / name)
    licenses = target / "licenses"
    licenses.mkdir(exist_ok=True)
    shutil.copytree(ROOT / "licenses", licenses, dirs_exist_ok=True)
    if (WORK / "licenses").is_dir():
        shutil.copytree(WORK / "licenses", licenses, dirs_exist_ok=True)
    packages = ["yt-dlp", "yt-dlp-ejs", "imageio-ffmpeg", "pyinstaller", "PyAudioWPatch", "certifi", "requests", "urllib3", "mutagen", "brotli", "pycryptodomex", "websockets", "charset-normalizer", "idna"]
    for package in packages:
        try:
            dist = metadata.distribution(package)
        except metadata.PackageNotFoundError:
            continue  # Offline reconstruction supplies verified original license files.
        for file in dist.files or []:
            if "license" in file.name.lower() or "copying" in file.name.lower():
                src = Path(dist.locate_file(file))
                if src.is_file():
                    shutil.copy2(src, licenses / f"{package}-{file.name}")
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.exists():
        shutil.copy2(python_license, licenses / "Python-LICENSE.txt")
    (licenses / "THIRD_PARTY.md").write_text(
        "# Components\n\nPython: https://www.python.org/ (PSF license).\n"
        "Tcl/Tk: https://www.tcl-lang.org/ (BSD-style license; runtime license files included).\n"
        "yt-dlp: https://github.com/yt-dlp/yt-dlp (Unlicense).\n"
        "PyInstaller: https://pyinstaller.org/ (GPL with bootloader distribution exception).\n"
        "PyAudioWPatch: https://github.com/s0d3s/PyAudioWPatch (PyAudio MIT, WPatch Apache-2.0; Windows WASAPI loopback audio).\n"
        "imageio-ffmpeg: https://github.com/imageio/imageio-ffmpeg (BSD-2-Clause).\n"
        "Deno 2.9.6: https://github.com/denoland/deno/tree/v2.9.6 (MIT).\n"
        "yt-dlp-ejs: https://github.com/yt-dlp/ejs (Unlicense).\n"
        "Bundled FFmpeg is supplied unmodified by imageio-ffmpeg 0.6.0 Windows wheel. "
        "FFmpeg is LGPL/GPL depending on enabled components; see the included binary license and build configuration. "
        "Build/source information: https://github.com/imageio/imageio-binaries and https://ffmpeg.org/download.html .\n",
        encoding="utf-8")
    (licenses / "FFmpeg-build.txt").write_bytes(subprocess.check_output([str(tools / "ffmpeg.exe"), "-version"]))
    (licenses / "FFmpeg-license.txt").write_bytes(subprocess.check_output([str(tools / "ffmpeg.exe"), "-L"], stderr=subprocess.STDOUT))
    files = {str(p.relative_to(target)).replace("\\", "/"): {"bytes": p.stat().st_size, "sha256": digest(p)}
             for p in target.rglob("*") if p.is_file() and p.name != "runtime-manifest.json"}
    try:
        source_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        source_dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT))
    except subprocess.CalledProcessError:
        source_commit, source_dirty = "source-archive-with-local-changes", True
    provenance = json.loads((WORK / "offline-provenance.json").read_text(encoding="utf-8")) if (WORK / "offline-provenance.json").is_file() else {}
    components = {}
    for package in packages:
        try:
            components[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            components[package] = provenance["components"][package]
    source_files = {p.relative_to(ROOT).as_posix(): digest(p) for p in sorted(ROOT.rglob('*')) if p.is_file()
                    and not set(p.relative_to(ROOT).parts) & {'releases', '.build', '__pycache__', '.git', 'tools'} and p.suffix != '.pyc'}
    manifest = {"version": VERSION, "source_commit": source_commit, "source_dirty": source_dirty,
                "source_tree_sha256": hashlib.sha256(json.dumps(source_files, sort_keys=True).encode()).hexdigest(),
                "python": sys.version.split()[0], "components": {**components, "deno": "2.9.6"}, "offline_dependency_provenance": provenance, "files": files}
    (target / "runtime-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    archive = RELEASE / f"VideoCatch-v{VERSION}-Windows-x64.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(target.rglob("*")):
            if p.is_file():
                z.write(p, str(p.relative_to(DIST)))
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        assert {p.split("/")[0] for p in z.namelist()} == {"VideoCatch"}
    info = {"file": archive.name, "bytes": archive.stat().st_size, "sha256": digest(archive), "zip_integrity": "passed", "root": "VideoCatch"}
    (RELEASE / f"checksums-v{VERSION}.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    extension_archive = RELEASE / f"VideoCatch-Extension-v{VERSION}.zip"
    with zipfile.ZipFile(extension_archive, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted((ROOT / "extension").iterdir()):
            if p.is_file(): z.write(p, "VideoCatch-extension/" + p.name)
    extension_info = {"file": extension_archive.name, "bytes": extension_archive.stat().st_size, "sha256": digest(extension_archive), "root": "VideoCatch-extension"}
    with zipfile.ZipFile(extension_archive) as z:
        assert z.testzip() is None
    (RELEASE / f"extension-checksums-v{VERSION}.json").write_text(json.dumps(extension_info, indent=2), encoding="utf-8")
    print(json.dumps(info, indent=2))
    print(json.dumps(extension_info, indent=2))


if __name__ == "__main__":
    main()
