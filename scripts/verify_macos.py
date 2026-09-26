"""Verify a final macOS ZIP; --gui additionally runs frozen synthetic download/clip checks.

No mode records the screen or opens a camera/microphone. GUI checks require a Mac
desktop session and are not a substitute for real macOS permission/hardware QA.
"""
import argparse
import functools
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import posixpath
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import zipfile


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_extract(archive, destination):
    """Validate paths/links before extraction; materialize symlinks last."""
    destination = Path(destination).resolve()
    links = {}
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        names = [item.filename.rstrip("/") for item in entries]
        if len(names) != len(set(names)) or source.testzip() is not None:
            raise ValueError("Duplicate entries or failed ZIP CRC")
        for item in entries:
            name = item.filename.rstrip("/")
            path = PurePosixPath(name)
            if not name or "\\" in name or ":" in name or path.is_absolute() or ".." in path.parts or path.parts[0] != "VideoCatch":
                raise ValueError("ZIP path is outside the single VideoCatch root")
            mode = item.external_attr >> 16
            if stat.S_ISLNK(mode):
                target = source.read(item).decode("utf-8")
                resolved = posixpath.normpath(posixpath.join(str(path.parent), target))
                if not target or "\\" in target or ":" in target or target.startswith("/") or not resolved.startswith("VideoCatch/"):
                    raise ValueError(f"ZIP symlink escapes package: {name}")
                links[name] = target
            elif stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ValueError("ZIP contains a special device entry")
        for name in names:
            if any(str(parent) in links for parent in PurePosixPath(name).parents):
                raise ValueError("ZIP writes through a symlink")
        for item in entries:
            name = item.filename.rstrip("/")
            path = destination / name
            if name in links:
                continue
            if item.is_dir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with source.open(item) as original, path.open("wb") as output:
                    import shutil
                    shutil.copyfileobj(original, output)
                path.chmod((item.external_attr >> 16) & 0o777 or 0o644)
        for name, target in links.items():
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(target)
    package = destination / "VideoCatch"
    for name in links:
        if not (destination / name).resolve(strict=True).is_relative_to(package):
            raise ValueError("Extracted symlink escapes package")
    return package


def verify_manifest(package):
    manifest = json.loads((package / "runtime-manifest.json").read_text(encoding="utf-8"))
    actual = {p.relative_to(package).as_posix() for p in package.rglob("*") if p.is_symlink() or p.is_file()}
    if actual != set(manifest["files"]) | {"runtime-manifest.json"}:
        raise ValueError("Package and manifest file sets differ")
    for name, expected in manifest["files"].items():
        path = package / name
        if expected["type"] == "symlink":
            if not path.is_symlink() or os.readlink(path) != expected["target"]:
                raise ValueError(f"Symlink manifest mismatch: {name}")
        elif path.is_symlink() or path.stat().st_size != expected["bytes"] or digest(path) != expected["sha256"] or stat.S_IMODE(path.stat().st_mode) != expected["mode"]:
            raise ValueError(f"File manifest mismatch: {name}")
    return manifest


def command(args, timeout=90, **kwargs):
    return subprocess.run([str(arg) for arg in args], check=True, capture_output=True, timeout=timeout, **kwargs)


def _log_tail(path, limit=16000):
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - limit))
        return stream.read(limit).decode("utf-8", errors="replace")


def _stop_owned_process(process):
    if process.poll() is not None:
        return
    # The probe owns this new session; terminate only its process group.
    for sig in (signal.SIGTERM, signal.SIGKILL) if os.name == "posix" else (None, None):
        try:
            if os.name == "posix":
                os.killpg(process.pid, sig)
            elif sig is None:
                process.kill()
            process.wait(timeout=2)
            return
        except ProcessLookupError:
            return
        except subprocess.TimeoutExpired:
            continue


def diagnosed_command(args, label, diagnostics, *, env, timeout=30, sample_after=12, cwd=None):
    """Keep the original deadline; sample this probe before terminating it.

    File-backed output avoids pipe-buffer deadlocks and survives fixture cleanup.
    Samples inspect only the verifier's own child; no screen/device capture occurs.
    """
    diagnostics.mkdir(parents=True, exist_ok=True)
    args = [str(arg) for arg in args]
    stdout_path, stderr_path = diagnostics / f"{label}.stdout.log", diagnostics / f"{label}.stderr.log"
    details = dict(command=args, timeout_seconds=timeout, status="starting", sample="not_needed")
    started = time.monotonic()
    deadline = started + timeout
    process = None
    try:
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            process = subprocess.Popen(args, stdout=stdout, stderr=stderr, env=env, cwd=cwd,
                                       start_new_session=os.name == "posix")
            details["pid"] = process.pid
            try:
                process.wait(timeout=min(sample_after, timeout))
            except subprocess.TimeoutExpired:
                if sys.platform == "darwin" and process.poll() is None and deadline - time.monotonic() > 4:
                    sample_file = diagnostics / f"{label}.sample.txt"
                    with (diagnostics / f"{label}.sample-command.log").open("wb") as sample_log:
                        try:
                            sampled = subprocess.run(["/usr/bin/sample", str(process.pid), "3", "-file", str(sample_file)],
                                                     stdout=sample_log, stderr=subprocess.STDOUT,
                                                     timeout=min(7, max(.1, deadline - time.monotonic())))
                            details["sample"] = "saved" if sampled.returncode == 0 else f"exit_{sampled.returncode}"
                        except (OSError, subprocess.TimeoutExpired) as error:
                            details["sample"] = str(error)
                else:
                    details["sample"] = "not_available"
                process.wait(timeout=max(.01, deadline - time.monotonic()))
            details["returncode"] = process.returncode
            details["status"] = "passed" if process.returncode == 0 else "failed"
            if process.returncode:
                raise subprocess.CalledProcessError(process.returncode, args)
    except subprocess.TimeoutExpired:
        details["status"] = "timed_out"
        raise
    except Exception as error:
        details.update(status="failed", error=str(error))
        raise
    finally:
        if process is not None:
            _stop_owned_process(process)
            details["returncode"] = process.returncode
        details["elapsed_seconds"] = round(time.monotonic() - started, 3)
        for name, path in (("stdout", stdout_path), ("stderr", stderr_path)):
            details[f"{name}_file"] = path.name
            if path.exists():
                details[f"{name}_tail"] = _log_tail(path)
        (diagnostics / f"{label}.json").write_text(json.dumps(details, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(dict(probe=label, **details), ensure_ascii=False), flush=True)


def diagnose_gui_failure(diagnostics, env):
    """Bounded source controls run only after frozen smoke has already failed."""
    source_root = Path(__file__).resolve().parents[1]
    preamble = (
        "import faulthandler, json, platform, sys\n"
        "faulthandler.enable()\n"
        "faulthandler.dump_traceback_later(10, repeat=True)\n"
        "print(json.dumps({'python': sys.version, 'executable': sys.executable, 'platform': platform.platform(), "
        "'machine': platform.machine()}), flush=True)\n"
    )
    probes = {
        "minimal-tk": preamble + (
            "print('stage: before tkinter import', flush=True)\n"
            "import tkinter as tk\n"
            "print('stage: before Tk()', flush=True)\n"
            "root = tk.Tk()\n"
            "print('stage: Tk created; patchlevel=' + root.tk.call('info', 'patchlevel'), flush=True)\n"
            "root.title('VideoCatch CI minimal Tk diagnostic')\n"
            "root.after(1200, root.destroy)\n"
            "print('stage: before mainloop', flush=True)\n"
            "root.mainloop()\n"
            "faulthandler.cancel_dump_traceback_later()\n"
            "print('stage: minimal Tk exited', flush=True)\n"
        ),
        "source-smoke": preamble + (
            "import runpy\n"
            f"sys.path.insert(0, {str(source_root)!r})\n"
            f"sys.argv = [{str(source_root / 'app.py')!r}, '--smoke-test']\n"
            "print('stage: before source app', flush=True)\n"
            "runpy.run_path(sys.argv[0], run_name='__main__')\n"
            "faulthandler.cancel_dump_traceback_later()\n"
            "print('stage: source smoke exited', flush=True)\n"
        ),
    }
    outcomes = {}
    for label, source in probes.items():
        script = diagnostics / f"{label}.py"
        script.write_text(source, encoding="utf-8")
        try:
            diagnosed_command([sys.executable, "-u", "-X", "faulthandler", script], label, diagnostics,
                               env=env, timeout=30, cwd=source_root)
            outcomes[label] = "passed"
        except Exception as error:
            outcomes[label] = f"failed: {error}"
    return outcomes


def native_checks(package, manifest, fixture, result, gui, diagnostics):
    app = package / "VideoCatch.app"
    executable = app / "Contents/MacOS/VideoCatch"
    client = package / "VideoCatchAI"
    ffmpeg = app / "Contents/Frameworks/tools/ffmpeg"
    deno = app / "Contents/Frameworks/tools/deno"
    for binary in (executable, client, ffmpeg, deno):
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise ValueError(f"Missing executable component: {binary.name}")
        found = command(["lipo", "-archs", binary]).stdout.decode().split()
        if manifest["architecture"] not in found:
            raise ValueError(f"Wrong architecture: {binary.name}")
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    if info["CFBundleShortVersionString"] != manifest["version"] or not all(info.get(key) for key in ("NSCameraUsageDescription", "NSMicrophoneUsageDescription")):
        raise ValueError("Bundle version or usage descriptions are missing")
    command(["codesign", "--verify", "--deep", "--strict", app])
    result["ad_hoc_signature"] = "passed; not Developer ID signed or notarized"
    if "deno 2.9.6" not in command([deno, "--version"]).stdout.decode():
        raise ValueError("Unexpected bundled Deno version")
    command([client, "--help"])
    result["executable_components"] = "passed"
    media = fixture / "source.mp4"
    command([ffmpeg, "-v", "error", "-n", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=20",
             "-f", "lavfi", "-i", "sine=frequency=440", "-t", "6", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", media])
    command([ffmpeg, "-v", "error", "-i", media, "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"])
    result["ffmpeg_synthetic_audio_video"] = "passed"
    if not gui:
        return
    # Give all developer checks an isolated home; real pairing/settings are not used.
    isolated = fixture / "home"
    isolated.mkdir()
    env = dict(os.environ, HOME=str(isolated), LOCALAPPDATA=str(isolated / "local"))
    env.pop("VIDEOCATCH_TOKEN", None)
    result["diagnostics_directory"] = diagnostics.name
    try:
        diagnosed_command([executable, "--smoke-test"], "frozen-smoke", diagnostics, env=env, timeout=30)
    except Exception:
        result["frozen_gui_smoke"] = "failed"
        try:
            result["gui_failure_controls"] = diagnose_gui_failure(diagnostics, env)
        except Exception as diagnostic_error:
            result["gui_failure_controls"] = {"diagnostic_error": str(diagnostic_error)}
        raise  # A successful control never makes the failed frozen artifact pass.
    result["frozen_gui_smoke"] = "passed"

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(fixture)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    download_report = fixture / "download.json"
    try:
        command([executable, "--verify-download", f"http://127.0.0.1:{server.server_port}/source.mp4",
                 fixture / "download", download_report], env=env)
    finally:
        server.shutdown()
        server.server_close()
    downloaded = json.loads(download_report.read_text(encoding="utf-8"))
    if downloaded["status"] != "已保存" or digest(downloaded["path"]) != digest(media):
        raise ValueError(f"Frozen synthetic download failed: {downloaded}")
    result["frozen_download_sha256"] = "passed"
    request = fixture / "clip.json"
    request.write_text(json.dumps({"source": str(media), "start": 1, "end": 3.25, "folder": str(fixture / "clipped")}), encoding="utf-8")
    report = fixture / "clip-report.json"
    command([executable, "--verify-clip-json", request, fixture / "clipped", report], env=env)
    clipped = json.loads(report.read_text(encoding="utf-8"))
    if clipped["status"] != "已保存":
        raise ValueError(f"Frozen synthetic clip failed: {clipped}")
    decoded = command([ffmpeg, "-v", "error", "-i", clipped["path"], "-map", "0:v:0", "-map", "0:a:0", "-progress", "pipe:1", "-f", "null", "-"])
    times = [int(line.split("=")[1]) / 1e6 for line in decoded.stdout.decode().splitlines() if line.startswith("out_time_us=")]
    if not times or abs(times[-1] - 2.25) > .12:
        raise ValueError(f"Frozen clip duration mismatch: {times}")
    result.update(frozen_clip_audio_video_decode="passed", clip_duration_seconds=times[-1])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--gui", action="store_true", help="Require frozen Tk startup and synthetic download/clip on a Mac desktop session")
    parser.add_argument("--structure-only", action="store_true", help="CRC, safe extraction and manifest only; no Mach-O execution")
    parser.add_argument("--sha256", help="Expected final ZIP SHA-256 from release checksums")
    parser.add_argument("--diagnostics", type=Path, help="Persistent directory for bounded GUI process diagnostics")
    args = parser.parse_args()
    diagnostics = (args.diagnostics or args.report.parent / (args.report.stem + "-diagnostics")).resolve()
    if args.gui and args.structure_only:
        parser.error("--gui and --structure-only cannot be combined")
    result = {"archive": args.archive.name, "bytes": args.archive.stat().st_size, "sha256": digest(args.archive),
              "frozen_gui_smoke": "not_run", "frozen_download_sha256": "not_run", "frozen_clip_audio_video_decode": "not_run",
              "screen_camera_microphone_permissions": "not_tested", "native_hardware_capture": "not_tested",
              "third_party_sites": "not_tested", "manual_visual_acceptance": "not_tested"}
    try:
        if args.sha256 and result["sha256"] != args.sha256:
            raise ValueError("Final archive SHA-256 mismatch")
        with tempfile.TemporaryDirectory(prefix="videocatch-macos-check-", dir=os.environ.get("RUNNER_TEMP")) as temporary:
            work = Path(temporary)
            package = safe_extract(args.archive, work / "extracted")
            manifest = verify_manifest(package)
            result.update(archive_crc="passed", archive_paths_links="passed", manifest_files=len(manifest["files"]),
                          version=manifest["version"], architecture=manifest["architecture"],
                          source_commit=manifest["source_commit"])
            if not args.structure_only:
                if sys.platform != "darwin":
                    raise ValueError("Native package verification requires macOS; use --structure-only elsewhere")
                fixture = work / "fixtures"
                fixture.mkdir()
                native_checks(package, manifest, fixture, result, args.gui, diagnostics)
            result["status"] = "passed"
    except Exception as error:
        result.update(status="failed", error=str(error))
        raise
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
