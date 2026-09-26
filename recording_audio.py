"""WASAPI audio capture isolated from the GUI and FFmpeg worker.

PyAudioWPatch exposes render endpoints as loopback input devices. Devices are
opened only by AudioCapture.start(), never while displaying the device list.
"""
import multiprocessing as mp
import math
from pathlib import Path
import queue
import time
import wave


def _module():
    try:
        import pyaudiowpatch as pa
        return pa
    except ImportError as exc:
        raise RuntimeError("缺少 PyAudioWPatch，无法录制系统声音或麦克风") from exc


def _inventory(pa):
    host = pa.get_host_api_info_by_type(_module().paWASAPI)
    host_index = host["index"]
    microphones, systems = [], []
    for index in range(pa.get_device_count()):
        device = pa.get_device_info_by_index(index)
        if device.get("hostApi") != host_index or not device.get("maxInputChannels"):
            continue
        entry = {"index": index, "name": device["name"],
                 "channels": int(device["maxInputChannels"]),
                 "sample_rate": int(device["defaultSampleRate"])}
        (systems if device.get("isLoopbackDevice") else microphones).append(entry)
    try:
        default_system = int(pa.get_default_wasapi_loopback()["index"])
    except (OSError, ValueError, LookupError):
        default_system = -1
    for entry in microphones:
        entry["default"] = entry["index"] == host.get("defaultInputDevice")
    for entry in systems:
        entry["default"] = entry["index"] == default_system
    return {"microphones": microphones, "systems": systems}


def audio_inventory():
    """Return WASAPI endpoints without opening a recording stream."""
    module = _module()
    with module.PyAudio() as pa:
        return _inventory(pa)


def _select(devices, requested, label):
    if requested is not None:
        for device in devices:
            if device["index"] == requested:
                return device
        raise ValueError(f"所选{label}已不可用，请刷新设备列表")
    if not devices:
        raise ValueError(f"未找到可用的{label}")
    return next((d for d in devices if d.get("default")), devices[0])


def _write_timed_audio(output, timing, data, count, info, now):
    """Preserve silent gaps using the PortAudio clock, anchored to monotonic."""
    rate = timing["sample_rate"]
    adc = info.get("input_buffer_adc_time", 0)
    valid_adc = isinstance(adc, (float, int)) and math.isfinite(adc) and adc > 0
    if timing["started"] is None:
        timing["started"] = now - count / rate
        timing["adc_anchor"] = adc if valid_adc else None
    if valid_adc and timing.get("adc_anchor") is not None and adc >= timing["adc_anchor"]:
        expected = round((adc - timing["adc_anchor"]) * rate)
    else:
        expected = round((now - timing["started"]) * rate) - count
    gap = expected - timing["frames"]
    if gap > rate // 10:
        while gap > 0:
            amount = min(gap, rate)
            output.writeframesraw(bytes(amount * timing["channels"] * 2))
            timing["frames"] += amount
            gap -= amount
    output.writeframesraw(data)
    timing["frames"] += count


def _audio_worker(options, folder, stop, messages):
    """Spawn target: native driver hangs are contained in this process."""
    streams, files, tracks = [], [], []
    callback_errors = queue.SimpleQueue()
    pa = None
    try:
        module = _module()
        pa = module.PyAudio()
        devices = _inventory(pa)
        kinds = {"system": ["system"], "microphone": ["microphone"],
                 "both": ["system", "microphone"]}[options["audio"]]
        for kind in kinds:
            device = _select(devices["systems" if kind == "system" else "microphones"],
                             options.get(kind + "_index"),
                             "系统声音设备" if kind == "system" else "麦克风")
            path = str(Path(folder) / (kind + ".wav"))
            target = wave.open(path, "wb")
            files.append(target)
            channels = min(device["channels"], 2)
            rate = device["sample_rate"]
            target.setparams((channels, 2, rate, 0, "NONE", "not compressed"))
            track = {"path": path, "started": None, "sample_rate": rate,
                     "channels": channels, "frames": 0}
            tracks.append(track)

            def callback(data, count, info, status, output=target, timing=track):
                try:
                    _write_timed_audio(output, timing, data, count, info, time.monotonic())
                    return (None, module.paContinue)
                except Exception as exc:
                    callback_errors.put(str(exc))
                    return (None, module.paAbort)

            stream = pa.open(format=module.paInt16, channels=channels, rate=rate,
                             input=True, input_device_index=device["index"],
                             frames_per_buffer=1024, stream_callback=callback,
                             start=False)
            streams.append(stream)
        started = time.monotonic()
        for stream in streams:
            stream.start_stream()
        # Loopback may deliver no callbacks while the render endpoint is silent.
        # Report readiness immediately; WAV padding later supplies that silence.
        messages.put({"ready": True, "started": started})
        while not stop.wait(.05):
            if not callback_errors.empty():
                raise RuntimeError(callback_errors.get())
            if any(not stream.is_active() for stream in streams):
                raise RuntimeError("音频设备已断开或录音流已停止")
    except Exception as exc:
        messages.put({"error": str(exc)})
    finally:
        for stream in streams:
            try:
                stream.stop_stream()
                stream.close()
            except Exception as exc:
                messages.put({"error": "音频流停止失败：" + str(exc)})
        for output in files:
            try:
                output.close()
            except Exception as exc:
                messages.put({"error": "音频文件未完整写入：" + str(exc)})
        if pa:
            try:
                pa.terminate()
            except Exception as exc:
                messages.put({"error": "音频设备关闭失败：" + str(exc)})
        if not callback_errors.empty():
            messages.put({"error": callback_errors.get()})
        messages.put({"tracks": tracks})


class AudioCapture:
    """Bounded process lifecycle; call only on the recording worker thread."""
    def __init__(self, options, folder):
        context = mp.get_context("spawn")
        self._stop = context.Event()
        self._messages = context.Queue()
        self._process = context.Process(target=_audio_worker,
                                        args=(options, str(folder), self._stop, self._messages),
                                        daemon=True, name="VideoCatchAudio")
        self.error = ""
        self.tracks = []
        self._stopped = False
        self._completed = False
        self._expected_tracks = 2 if options["audio"] == "both" else 1

    def start(self, cancelled=None):
        self._process.start()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if cancelled and cancelled():
                self.stop()
                raise RuntimeError("录制准备已停止")
            try:
                message = self._messages.get(timeout=.1)
            except queue.Empty:
                if not self._process.is_alive():
                    break
                continue
            if message.get("error"):
                self.error = message["error"]
                break
            if message.get("ready"):
                return
        self.stop()
        raise RuntimeError(self.error or "音频设备启动超时，请检查设备或选择不录音")

    def check(self):
        while True:
            try:
                message = self._messages.get_nowait()
            except queue.Empty:
                break
            if "error" in message:
                self.error = message["error"]
            if "tracks" in message:
                self.tracks = message["tracks"]
                self._completed = True
        if self.error:
            raise RuntimeError(self.error)
        if not self._process.is_alive() and not self._stop.is_set():
            raise RuntimeError("音频采集进程意外退出")

    def stop(self):
        if self._stopped:
            return self.tracks
        self._stop.set()
        if self._process.pid is not None:
            self._process.join(5)
            if self._process.is_alive():
                self._process.terminate()
                self._process.join(3)
                self.error = "音频驱动未及时停止；原始文件已保留"
            try:
                self.check()
            except RuntimeError:
                pass
            if not self.error and (not self._completed or len(self.tracks) != self._expected_tracks):
                self.error = "音频采集未完整结束；原始文件已保留"
        self._stopped = True
        self._messages.close()
        return self.tracks
