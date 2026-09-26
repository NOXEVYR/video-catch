"""Thread-safe screen/camera recording. No Tk calls and no device use on import."""
from copy import deepcopy
from datetime import datetime
import math
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import uuid

from engine import ffmpeg_path
from recording_audio import AudioCapture, audio_inventory


BUSY_STATES = {"准备录制", "录制中", "录制暂停", "正在保存"}
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def normalize_options(data):
    """Validate public options without opening hardware or spawning a process."""
    if not isinstance(data, dict):
        raise ValueError("录制参数必须是对象")
    allowed = {"mode", "region", "hwnd", "camera", "audio", "microphone_index",
               "system_index", "fps", "quality", "cursor", "duration"}
    unknown = set(data) - allowed
    if unknown:
        raise ValueError("未知录制参数：" + ", ".join(sorted(unknown)))
    result = {"mode": "screen", "camera": "", "audio": "none", "fps": 30,
              "quality": "balanced", "cursor": True, "duration": 0}
    result.update(deepcopy(data))
    for key, choices in (("mode", {"screen", "region", "window", "camera"}),
                         ("audio", {"none", "system", "microphone", "both"}),
                         ("quality", {"high", "balanced", "small"})):
        if not isinstance(result[key], str) or result[key] not in choices:
            raise ValueError(f"无效的 {key} 参数")
    if type(result["fps"]) is not int or result["fps"] not in {10, 15, 24, 30, 60}:
        raise ValueError("帧率请选择 10、15、24、30 或 60")
    if type(result["cursor"]) is not bool:
        raise ValueError("cursor 必须是布尔值")
    for key in ("duration",):
        value = result.get(key, 0)
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError(f"{key} 必须是有限的非负秒数")
    camera = result.get("camera") or ""
    if not isinstance(camera, str) or len(camera) > 2048 or any(c in camera for c in '\x00\r\n"'):
        raise ValueError("摄像头设备名称无效")
    result["camera"] = camera
    if result["mode"] == "camera" and not camera:
        raise ValueError("请先选择摄像头")
    for key in ("system_index", "microphone_index"):
        value = result.get(key)
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(f"{key} 必须是非负整数")
    if result["mode"] == "window":
        if type(result.get("hwnd")) is not int or not 0 < result["hwnd"] < 2**64:
            raise ValueError("请先选择要录制的窗口")
    if result["mode"] == "region":
        region = result.get("region")
        if not isinstance(region, dict) or set(region) != {"x", "y", "width", "height"}:
            raise ValueError("区域需要 x、y、width、height")
        if any(type(v) is not int for v in region.values()):
            raise ValueError("区域坐标及尺寸必须是整数")
        if not (2 <= region["width"] <= 32768 and 2 <= region["height"] <= 32768):
            raise ValueError("录制区域宽高必须在 2 到 32768 像素之间")
        if any(abs(region[k]) > 2147483647 for k in ("x", "y")):
            raise ValueError("录制区域坐标超出范围")
    return result


def _ffmpeg():
    executable = ffmpeg_path()
    if not executable:
        raise RuntimeError("未找到 FFmpeg，请重新安装完整应用")
    return executable


def _geometry(options):
    from capture_native import desktop_bounds, window_info
    mode = options["mode"]
    if sys.platform == "darwin":
        from capture_native import list_monitors
        from recording_audio_macos import capture_geometry
        if mode != "camera":
            from capture_macos import require_screen_capture_permission
            require_screen_capture_permission(request=True)
        window = window_info(options["hwnd"]) if mode == "window" else None
        return capture_geometry(options, list_monitors() if mode != "camera" else [], window)
    if mode == "camera":
        return {"x": 0, "y": 0, "width": 1280, "height": 720}
    if mode == "window":
        window = window_info(options["hwnd"])
        return {"x": window.get("client_x", window["x"]),
                "y": window.get("client_y", window["y"]),
                "width": window.get("client_width", window["width"]),
                "height": window.get("client_height", window["height"])}
    bounds = desktop_bounds()
    if mode == "screen":
        return bounds
    region = options["region"]
    if (region["x"] < bounds["x"] or region["y"] < bounds["y"] or
            region["x"] + region["width"] > bounds["x"] + bounds["width"] or
            region["y"] + region["height"] > bounds["y"] + bounds["height"]):
        raise ValueError("录制区域超出当前桌面，请重新框选")
    return region


def capture_inputs(options, geometry):
    """Argument arrays deliberately avoid shell quoting and title collisions."""
    if sys.platform == "darwin":
        from recording_audio_macos import capture_plan, device_inventory as mac_devices
        plan = geometry.get("mac_plan")
        if plan is None:
            plan = capture_plan(options, geometry, mac_devices(_ffmpeg()))
        return list(plan["inputs"])
    args = []
    if options["mode"] != "camera":
        args += ["-thread_queue_size", "512", "-f", "gdigrab", "-framerate", str(options["fps"]),
                 "-draw_mouse", "1" if options["cursor"] else "0"]
        if options["mode"] == "window":
            from capture_native import window_info
            window_info(options["hwnd"])
            args += ["-i", f'hwnd={options["hwnd"]}']
        else:
            args += ["-offset_x", str(geometry["x"]), "-offset_y", str(geometry["y"]),
                     "-video_size", f'{geometry["width"]}x{geometry["height"]}', "-i", "desktop"]
    if options["camera"]:
        args += ["-thread_queue_size", "512", "-f", "dshow", "-rtbufsize", "256M",
                 "-i", "video=" + options["camera"]]
    return args


def _filters(options, geometry):
    width = (geometry["width"] + 1) // 2 * 2
    height = (geometry["height"] + 1) // 2 * 2
    crop = geometry.get("crop")
    crop_filter = (f"crop={crop['width']}:{crop['height']}:{crop['x']}:{crop['y']}:exact=1," if crop else "")
    base = (f"[0:v]{crop_filter}scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={options['fps']}[base]")
    if options["camera"] and options["mode"] != "camera":
        overlay_w, overlay_h = max(2, width // 4 // 2 * 2), max(2, height // 4 // 2 * 2)
        base += (f";[1:v]scale={overlay_w}:{overlay_h}:force_original_aspect_ratio=decrease,"
                 "setsar=1[cam];[base][cam]overlay=main_w-overlay_w-8:main_h-overlay_h-8"
                 ":eof_action=pass[v]")
    else:
        base += ";[base]null[v]"
    return base


def _log_tail(path):
    try:
        with open(path, "rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - 4000))
            return stream.read().decode("utf-8", errors="replace")[-1600:]
    except OSError:
        return ""


def _run(command, log, timeout=120):
    with open(log, "ab") as output:
        try:
            result = subprocess.run(command, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=output,
                                    timeout=timeout, creationflags=CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("媒体处理超时；原始录制文件已保留") from exc
    if result.returncode:
        raise RuntimeError("FFmpeg 处理失败：" + _log_tail(log))


def _media_duration(executable, path):
    result = subprocess.run([executable, "-hide_banner", "-i", str(path)],
                            stdin=subprocess.DEVNULL, capture_output=True, timeout=15,
                            creationflags=CREATE_NO_WINDOW)
    text = result.stderr.decode("utf-8", errors="replace")
    match = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", text)
    if not match or "Video:" not in text:
        raise RuntimeError("录制文件缺少可读的视频时长；恢复文件已保留")
    return int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])


def _cleanup_recovery(recovery, destination, ident):
    """Remove only this session's owned files, never descend into user data."""
    root = recovery.resolve()
    if root.parent != destination.resolve() or root.name != ".videocatch-recording-" + ident:
        raise RuntimeError("恢复目录校验失败，未清理临时文件")
    if recovery.is_symlink() or (hasattr(recovery, "is_junction") and recovery.is_junction()):
        raise RuntimeError("恢复目录为链接，未清理临时文件")
    # Verify every descendant before any removal; no changed links or foreign
    # files are treated as disposable just because they live under this folder.
    files, directories = [], []
    for path in recovery.rglob("*"):
        if not path.resolve().is_relative_to(root) or path.is_symlink() or (
                hasattr(path, "is_junction") and path.is_junction()):
            raise RuntimeError("恢复目录中存在链接，保留临时文件")
        if path.is_dir():
            if path.parent != recovery or not re.fullmatch(r"segment-\d{4,}", path.name):
                raise RuntimeError("恢复目录中存在未知目录，保留临时文件")
            directories.append(path)
        else:
            permitted = (path.parent == recovery and (path.name in {"finalize.log", "segments.txt"} or
                         re.fullmatch(r"ready-\d{4,}\.mp4", path.name))) or (
                path.parent.parent == recovery and re.fullmatch(r"segment-\d{4,}", path.parent.name) and
                path.name in {"video.mkv", "capture.log", "system.wav", "microphone.wav"})
            if not permitted:
                raise RuntimeError("恢复目录中存在未知文件，保留临时文件")
            files.append(path)
    for path in files:
        path.unlink()
    for path in directories:
        path.rmdir()
    recovery.rmdir()


def device_inventory():
    result = {"cameras": [], "microphones": [], "systems": [], "diagnostics": []}
    if sys.platform == "darwin":
        from recording_audio_macos import device_inventory as mac_devices, PLATFORM_NOTES
        try:
            return mac_devices(_ffmpeg())
        except Exception as exc:
            result["diagnostics"] = list(PLATFORM_NOTES) + [str(exc)]
            return result
    try:
        completed = subprocess.run([_ffmpeg(), "-hide_banner", "-list_devices", "true",
                                    "-f", "dshow", "-i", "dummy"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=12,
                                   creationflags=CREATE_NO_WINDOW)
        for line in completed.stderr.decode("utf-8", errors="replace").splitlines():
            match = re.search(r'"(.+)"\s+\(video\)', line)
            if match:
                result["cameras"].append({"name": match.group(1)})
    except Exception as exc:
        result["diagnostics"].append("摄像头枚举：" + str(exc))
    try:
        result.update(audio_inventory())
    except Exception as exc:
        result["diagnostics"].append("音频枚举：" + str(exc))
    return result


def screenshot(options, folder):
    """One PNG capture; caller dispatches this blocking function off the UI."""
    ident = uuid.uuid4().hex[:12]
    result = {"id": ident, "status": "失败", "path": "", "error": ""}
    try:
        options = normalize_options(options)
        # Screenshots never open audio devices, even when called from a preset
        # that was previously used for recording with sound.
        options["audio"] = "none"
        geometry = _geometry(options)
        destination = Path(folder).expanduser().resolve()
        destination.mkdir(parents=True, exist_ok=True)
        path = destination / f"截图_{datetime.now():%Y%m%d_%H%M%S}_{ident}.png"
        command = [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-n"]
        command += capture_inputs(options, geometry)
        if options["camera"] and options["mode"] != "camera":
            command += ["-filter_complex", _filters(options, geometry), "-map", "[v]"]
        else:
            command += ["-map", "0:v"]
            if geometry.get("crop"):
                crop = geometry["crop"]
                command += ["-vf", f"crop={crop['width']}:{crop['height']}:{crop['x']}:{crop['y']}:exact=1"]
        command += ["-frames:v", "1", "-update", "1", str(path)]
        completed = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, timeout=20, creationflags=CREATE_NO_WINDOW)
        if completed.returncode or not path.is_file() or not path.stat().st_size:
            raise RuntimeError(completed.stderr.decode("utf-8", errors="replace")[-1600:] or "截图失败")
        result.update(status="已保存", path=str(path))
    except Exception as exc:
        result["error"] = str(exc)
    return result


class Recorder:
    def __init__(self):
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._desired = "stop"
        self._thread = None
        self._elapsed = 0.0
        self._active_since = None
        self._generation = 0
        self._state = {"id": "", "status": "", "path": "", "error": "", "elapsed": 0., "options": {}}

    @property
    def is_busy(self):
        with self._lock:
            return self._state["status"] in BUSY_STATES

    def snapshot(self):
        with self._lock:
            result = deepcopy(self._state)
            result["elapsed"] = round(self._elapsed + (time.monotonic() - self._active_since
                                                       if self._active_since else 0), 3)
            return result

    def start(self, options, folder):
        options = normalize_options(options)
        if not isinstance(folder, (str, Path)) or not str(folder).strip():
            raise ValueError("请设置录制保存目录")
        with self._lock:
            if self.is_busy or (self._thread and self._thread.is_alive()):
                raise ValueError("已有录制正在进行或保存")
            self._desired = "record"
            self._generation += 1
            self._elapsed, self._active_since = 0., None
            self._state = {"id": uuid.uuid4().hex[:12], "status": "准备录制", "path": "", "error": "",
                           "elapsed": 0., "options": options}
            self._wake.clear()
            self._thread = threading.Thread(target=self._worker, args=(str(folder),),
                                            name="VideoCatchRecorder", daemon=True)
            self._thread.start()
            return self.snapshot()

    def pause(self):
        with self._lock:
            if self._state["status"] not in {"准备录制", "录制中", "录制暂停"}:
                raise ValueError("当前没有可暂停的录制")
            self._desired = "pause"
            self._generation += 1
            self._freeze_clock()
            self._state["status"] = "录制暂停"
            self._wake.set()
            return self.snapshot()

    def resume(self):
        with self._lock:
            if self._state["status"] != "录制暂停":
                raise ValueError("只有暂停的录制可以继续")
            self._desired = "record"
            self._state["status"] = "准备录制"
            self._wake.set()
            return self.snapshot()

    def stop(self):
        with self._lock:
            if not self.is_busy:
                return self.snapshot()
            self._desired = "stop"
            self._freeze_clock()
            self._state["status"] = "正在保存"
            self._wake.set()
            return self.snapshot()

    def _freeze_clock(self):
        if self._active_since is not None:
            self._elapsed += time.monotonic() - self._active_since
            self._active_since = None

    def _requested(self):
        with self._lock:
            return self._desired

    def _worker(self, folder):
        recovery = None
        try:
            options = self.snapshot()["options"]
            executable = _ffmpeg()
            geometry = _geometry(options)
            if sys.platform == "darwin":
                from recording_audio_macos import capture_plan, device_inventory as mac_devices, PLATFORM_NOTES
                geometry["mac_plan"] = capture_plan(options, geometry, mac_devices(executable))
                with self._lock:
                    self._state["notes"] = list(PLATFORM_NOTES)
            destination = Path(folder).expanduser().resolve()
            destination.mkdir(parents=True, exist_ok=True)
            ident = self.snapshot()["id"]
            recovery = destination / (".videocatch-recording-" + ident)
            recovery.mkdir()
            with self._lock:
                self._state["recovery_path"] = str(recovery)
            segments = []
            sequence = 0
            while self._requested() != "stop":
                if self._requested() == "pause":
                    self._wake.wait(.1)
                    self._wake.clear()
                    continue
                segment = self._capture_segment(executable, options, geometry, recovery, sequence)
                sequence += 1
                if segment:
                    segments.append(segment)
            if not segments:
                raise RuntimeError("已停止，尚未产生可保存的画面")
            with self._lock:
                self._state["status"] = "正在保存"
            final = destination / f"录制_{datetime.now():%Y%m%d_%H%M%S}_{ident}.mp4"
            self._finalize(executable, segments, recovery, final)
            _media_duration(executable, final)
            _run([executable, "-v", "error", "-i", str(final), "-t", "1", "-f", "null", "-"],
                 recovery / "finalize.log", timeout=30)
            warning = self.snapshot().get("warning", "")
            try:
                _cleanup_recovery(recovery, destination, ident)
            except (OSError, RuntimeError) as exc:
                warning = "视频已保存，临时文件未完全清理：" + str(exc)
            with self._lock:
                self._state.update(status="已保存", path=str(final), error="", warning=warning,
                                   recovery_path=str(recovery) if recovery.exists() else "")
        except Exception as exc:
            with self._lock:
                self._state.update(status="失败", error=str(exc))
        finally:
            with self._lock:
                self._freeze_clock()

    def _capture_segment(self, executable, options, geometry, recovery, index):
        directory = recovery / f"segment-{index:04d}"
        directory.mkdir()
        raw, log = directory / "video.mkv", directory / "capture.log"
        audio, process, log_stream = None, None, None
        ready = threading.Event()
        frames = [0]
        with self._lock:
            generation = self._generation
        try:
            embedded_audio = geometry.get("mac_plan", {}).get("audio_labels", [])
            if options["audio"] != "none" and sys.platform != "darwin":
                audio = AudioCapture(options, directory)
                audio.start(cancelled=lambda: self._requested() == "stop")
            if self._requested() != "record" or generation != self._generation:
                return None
            command = [executable, "-hide_banner", "-loglevel", "warning", "-n",
                       "-stats_period", "0.2", "-progress", "pipe:1"]
            command += capture_inputs(options, geometry)
            filters = _filters(options, geometry)
            if embedded_audio:
                from recording_audio_macos import audio_filter
                filters += ";" + audio_filter(embedded_audio)
            command += ["-filter_complex", filters, "-map", "[v]"]
            if embedded_audio:
                command += ["-map", "[record_audio]", "-c:a", "aac", "-b:a", "160k", "-ac", "2", "-ar", "48000"]
            else:
                command += ["-an"]
            command += [
                        "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
                        "-crf", str({"high": 18, "balanced": 23, "small": 28}[options["quality"]]),
                        "-pix_fmt", "yuv420p", "-g", str(options["fps"] * 2), str(raw)]
            log_stream = open(log, "wb")
            video_started = time.monotonic()
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=log_stream, creationflags=CREATE_NO_WINDOW)

            def progress():
                try:
                    for line in iter(process.stdout.readline, b""):
                        if line.startswith(b"frame="):
                            frames[0] = int(line.split(b"=", 1)[1])
                            if frames[0] > 0:
                                ready.set()
                except (OSError, ValueError):
                    pass

            reader = threading.Thread(target=progress, name="VideoCatchProgress", daemon=True)
            reader.start()
            next_window_check = 0.
            while self._requested() == "record" and generation == self._generation:
                if process.poll() is not None:
                    raise RuntimeError("录制进程意外停止：" + _log_tail(log))
                if audio:
                    audio.check()
                if options["mode"] == "window" and time.monotonic() >= next_window_check:
                    from capture_native import window_info
                    current_window = window_info(options["hwnd"])
                    if sys.platform == "darwin":
                        current_rect = {key: current_window.get("client_" + key, current_window[key])
                                        for key in ("x", "y", "width", "height")}
                        if current_rect != geometry["logical_rect"]:
                            with self._lock:
                                self._state["warning"] = "所选窗口已移动或缩放，已结束此次窗口区域录制"
                            self.stop()
                    next_window_check = time.monotonic() + .5
                if ready.is_set():
                    with self._lock:
                        if self._desired == "record" and generation == self._generation and self._active_since is None:
                            self._active_since = time.monotonic()
                            self._state["status"] = "录制中"
                    if options["duration"] and self.snapshot()["elapsed"] >= options["duration"]:
                        self.stop()
                elif time.monotonic() - video_started > 20:
                    raise RuntimeError("20 秒内未收到画面，请检查摄像头权限或所选窗口")
                self._wake.wait(.05)
                self._wake.clear()
            self._end_process(process)
            reader.join(2)
            tracks = audio.stop() if audio else []
            if audio and audio.error:
                raise RuntimeError(audio.error)
            if process.returncode not in (0, 255):
                raise RuntimeError("录制保存分段失败：" + _log_tail(log))
            if not frames[0] or not raw.is_file() or not raw.stat().st_size:
                return None
            return {"path": raw, "tracks": tracks, "started": video_started, "embedded_audio": bool(embedded_audio)}
        finally:
            try:
                if process and process.poll() is None:
                    self._end_process(process)
            finally:
                if process:
                    if process.stdin:
                        process.stdin.close()
                    if process.stdout:
                        process.stdout.close()
                if audio:
                    audio.stop()
                if log_stream:
                    log_stream.close()
                with self._lock:
                    self._freeze_clock()

    @staticmethod
    def _end_process(process):
        if process.poll() is not None:
            return
        try:
            process.stdin.write(b"q\n")
            process.stdin.flush()
            process.wait(timeout=8)
        except (OSError, subprocess.TimeoutExpired):
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            raise RuntimeError("录制进程停止超时；恢复文件已保留")

    @staticmethod
    def _finalize(executable, segments, recovery, final):
        if final.exists():
            raise RuntimeError("目标文件已存在，未覆盖原文件")
        outputs = []
        log = recovery / "finalize.log"
        for index, segment in enumerate(segments):
            output = recovery / f"ready-{index:04d}.mp4"
            command = [executable, "-hide_banner", "-loglevel", "error", "-n", "-i", str(segment["path"])]
            tracks = segment["tracks"]
            filters = []
            for track in tracks:
                # An idle WASAPI loopback can produce a header-only WAV. Supply
                # real silence instead of letting FFmpeg reject an empty input.
                if Path(track["path"]).stat().st_size <= 44:
                    command += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
                else:
                    command += ["-i", track["path"]]
            for number, track in enumerate(tracks, 1):
                delta = segment["started"] - (track["started"] or segment["started"])
                timing = f"atrim=start={delta:.6f},asetpts=PTS-STARTPTS" if delta >= 0 else (
                    f"asetpts=PTS-STARTPTS,adelay={round(-delta * 1000)}:all=1")
                filters.append(f"[{number}:a]{timing},aresample=48000,apad[a{number}]")
            command += ["-map", "0:v", "-c:v", "copy"]
            if segment.get("embedded_audio"):
                command += ["-map", "0:a", "-c:a", "copy"]
            if tracks:
                labels = "".join(f"[a{i}]" for i in range(1, len(tracks) + 1))
                filters.append(labels + f"amix=inputs={len(tracks)}:duration=longest:normalize=0,alimiter=limit=0.95[a]")
                command += ["-filter_complex", ";".join(filters), "-map", "[a]", "-c:a", "aac",
                            "-b:a", "160k", "-ac", "2", "-ar", "48000", "-shortest"]
            command += ["-t", str(_media_duration(executable, segment["path"])),
                        "-movflags", "+faststart", str(output)]
            _run(command, log, timeout=3600)
            outputs.append(output)
        listing = recovery / "segments.txt"
        listing.write_text("".join(f"file '{path.name}'\n" for path in outputs), encoding="utf-8")
        # -n protects a concurrently-created destination even in the extremely
        # unlikely case of a name collision. Source/recovery paths are retained.
        _run([executable, "-hide_banner", "-loglevel", "error", "-n", "-f", "concat", "-safe", "1",
              "-i", str(listing), "-map", "0", "-c", "copy", "-movflags", "+faststart", str(final)],
             log, timeout=3600)
        if not final.is_file() or final.stat().st_size < 100:
            raise RuntimeError("没有生成完整录制文件；恢复文件已保留")
