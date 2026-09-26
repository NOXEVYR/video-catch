"""AVFoundation device selection and capture planning; no hardware on import.

System sound requires a user-configured virtual loopback input. This module
never installs a driver or changes the macOS sound output configuration.
"""
import re
import subprocess


PLATFORM_NOTES = [
    "macOS 全屏录制默认使用主显示器；区域和窗口需完整位于同一显示器。",
    "macOS 窗口录制裁剪屏幕矩形，遮挡内容会入镜；窗口移动或缩放后结束录制。",
    "macOS 系统声音需要已安装并配置的 BlackHole、Loopback、Soundflower 或 VB-Cable 虚拟回环输入，并手动选择该设备。",
    "请在 macOS 隐私与安全性中允许屏幕录制、摄像头和麦克风访问。",
]
LOOPBACK_NAME = re.compile(r"blackhole|loopback|soundflower|vb[- _]?cable|vb[- _]?audio|cable output", re.I)


def parse_devices(text):
    result = {"cameras": [], "microphones": [], "systems": [], "screens": [],
              "diagnostics": list(PLATFORM_NOTES)}
    section = None
    for line in text.splitlines():
        if "AVFoundation video devices:" in line:
            section = "video"
            continue
        if "AVFoundation audio devices:" in line:
            section = "audio"
            continue
        match = re.search(r"\[(\d+)\]\s+(.+?)\s*$", line)
        if not match or section is None:
            continue
        index, name = int(match[1]), match[2].strip()
        if section == "video":
            screen = re.fullmatch(r"Capture screen (\d+)", name)
            if screen:
                result["screens"].append({"index": index, "name": name, "display_index": int(screen[1])})
            else:
                result["cameras"].append({"index": index, "name": name})
        else:
            entry = {"index": index, "name": name, "default": False}
            if LOOPBACK_NAME.search(name):
                result["systems"].append({**entry, "requires_routing": True})
            else:
                result["microphones"].append(entry)
    return result


def device_inventory(executable):
    completed = subprocess.run([executable, "-hide_banner", "-list_devices", "true", "-f", "avfoundation",
                                "-i", ""], stdin=subprocess.DEVNULL, capture_output=True, timeout=15)
    text = completed.stderr.decode("utf-8", errors="replace")
    if "AVFoundation video devices:" not in text and "AVFoundation audio devices:" not in text:
        raise RuntimeError("无法枚举 AVFoundation 设备，请检查 FFmpeg 和 macOS 权限：" + text[-900:])
    return parse_devices(text)


def _contains(monitor, rect):
    return (rect["x"] >= monitor["x"] and rect["y"] >= monitor["y"] and
            rect["x"] + rect["width"] <= monitor["x"] + monitor["width"] and
            rect["y"] + rect["height"] <= monitor["y"] + monitor["height"])


def capture_geometry(options, monitors, window=None):
    if options["mode"] == "camera":
        return {"x": 0, "y": 0, "width": 1280, "height": 720}
    if not monitors:
        raise ValueError("未找到可录制的显示器")
    if options["mode"] == "screen":
        monitor = next((m for m in monitors if m.get("primary")), monitors[0])
        rect = {key: monitor[key] for key in ("x", "y", "width", "height")}
    else:
        if options["mode"] == "window":
            rect = {key: window.get("client_" + key, window[key]) for key in ("x", "y", "width", "height")}
        else:
            rect = options["region"]
        monitor = next((m for m in monitors if _contains(m, rect)), None)
        if monitor is None:
            raise ValueError("macOS 录制区域或窗口必须完整位于同一显示器，请重新选择")
    scale_x = monitor.get("scale_x", monitor.get("pixel_width", monitor["width"]) / monitor["width"])
    scale_y = monitor.get("scale_y", monitor.get("pixel_height", monitor["height"]) / monitor["height"])
    crop_x = round((rect["x"] - monitor["x"]) * scale_x)
    crop_y = round((rect["y"] - monitor["y"]) * scale_y)
    width = round((rect["x"] + rect["width"] - monitor["x"]) * scale_x) - crop_x
    height = round((rect["y"] + rect["height"] - monitor["y"]) * scale_y) - crop_y
    if width < 2 or height < 2:
        raise ValueError("录制区域过小")
    return {"x": rect["x"], "y": rect["y"], "width": width, "height": height,
            "logical_rect": dict(rect), "display_index": monitor["display_index"],
            "display_id": monitor.get("id"), "crop": {"x": crop_x, "y": crop_y, "width": width, "height": height}}


def _audio_device(devices, requested, system=False):
    label = "虚拟系统声音输入" if system else "麦克风"
    if system and requested is None:
        raise ValueError("macOS 系统声音需要手动选择已配置的虚拟回环输入，例如 BlackHole；不会自动安装或更改声音输出")
    if requested is None:
        if not devices:
            raise ValueError("未找到可用的麦克风，请检查 macOS 麦克风权限")
        return "default"
    selected = next((d for d in devices if d["index"] == requested), None)
    if selected is None:
        raise ValueError(f"所选{label}不可用，请刷新设备列表")
    return selected["index"]


def capture_plan(options, geometry, inventory):
    """Return input arguments and audio stream labels for one FFmpeg session."""
    audio_devices = []
    if options["audio"] in {"system", "both"}:
        audio_devices.append(_audio_device(inventory["systems"], options.get("system_index"), system=True))
    if options["audio"] in {"microphone", "both"}:
        audio_devices.append(_audio_device(inventory["microphones"], options.get("microphone_index")))
    camera = None
    if options["camera"]:
        camera = next((c["index"] for c in inventory["cameras"] if c["name"] == options["camera"]), None)
        if camera is None:
            raise ValueError("所选摄像头不可用，请刷新设备列表并检查 macOS 摄像头权限")
    if options["mode"] == "camera":
        video = camera
    else:
        video = next((s["index"] for s in inventory["screens"] if s["display_index"] == geometry["display_index"]), None)
        if video is None:
            raise ValueError("所选显示器的 AVFoundation 屏幕设备不可用，请检查屏幕录制权限")
    first_audio = str(audio_devices[0]) if audio_devices else "none"
    args = ["-thread_queue_size", "512", "-f", "avfoundation", "-framerate",
            str(options["fps"] if options["mode"] != "camera" else 30),
            "-capture_cursor", "1" if options["cursor"] else "0", "-i", f"{video}:{first_audio}"]
    labels = ["0:a"] if audio_devices else []
    next_index = 1
    if camera is not None and options["mode"] != "camera":
        args += ["-thread_queue_size", "512", "-f", "avfoundation", "-framerate", "30", "-i", f"{camera}:none"]
        next_index += 1
    for device in audio_devices[1:]:
        args += ["-thread_queue_size", "512", "-f", "avfoundation", "-i", f"none:{device}"]
        labels.append(f"{next_index}:a")
        next_index += 1
    return {"inputs": args, "audio_labels": labels}


def audio_filter(labels):
    filters = [f"[{label}]aresample=48000:async=1:first_pts=0[mac_a{index}]" for index, label in enumerate(labels)]
    streams = "".join(f"[mac_a{index}]" for index in range(len(labels)))
    filters.append(streams + f"amix=inputs={len(labels)}:duration=longest:normalize=0,alimiter=limit=0.95[record_audio]")
    return ";".join(filters)
