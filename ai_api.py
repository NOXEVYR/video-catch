"""Authenticated requests are dispatched on Tk's thread, never on HTTP threads."""
import math
from pathlib import Path
import queue
import threading
import uuid

from core import MAX_ITEMS
from network import proxy_value

BUSY = {"排队中", "解析中", "下载中", "整理文件", "裁剪中", "准备录制", "录制中", "录制暂停", "正在保存", "截图中"}


class ApiError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class Api:
    def __init__(self, app):
        self.app = app
        self.enabled = threading.Event()
        self.requests = queue.Queue(maxsize=32)

    def request(self, action, data):
        if not self.enabled.is_set():
            raise ApiError(403, "请先在拾影中启用 AI 接口")
        response = queue.Queue(maxsize=1)
        cancelled = threading.Event()
        try:
            self.requests.put_nowait((action, data, response, cancelled))
        except queue.Full:
            raise ApiError(429, "请求队列已满") from None
        try:
            status, result = response.get(timeout=4)
        except queue.Empty:
            cancelled.set()
            raise ApiError(503, "界面忙碌；请先查询状态再重试操作") from None
        if status != 200:
            raise ApiError(status, result["error"])
        return result

    def pump(self):
        for _ in range(32):
            try:
                action, data, response, cancelled = self.requests.get_nowait()
            except queue.Empty:
                break
            if cancelled.is_set():
                continue
            try:
                if not self.enabled.is_set():
                    raise ApiError(403, "AI 接口已关闭")
                result = self.dispatch(action, data)
                response.put((200, result))
            except ApiError as exc:
                response.put((exc.status, {"error": exc.message}))
            except RuntimeError as exc:
                response.put((409, {"error": str(exc)}))
            except (ValueError, TypeError, KeyError, OSError):
                response.put((400, {"error": "参数无效，请检查路径、链接和时间范围"}))

    def dispatch(self, action, data):
        app = self.app
        if action == "capabilities":
            return {"ok": True, "version": "1", "actions": ["capabilities", "state", "watch", "import", "download", "clip", "cancel",
                    "capture_sources", "record_start", "record_pause", "record_resume", "record_stop", "record_state", "screenshot"],
                    "time_unit": "seconds", "clip_mode": "accurate_h264_aac", "max_concurrent_jobs": 2,
                    "terminal_statuses": ["已保存", "失败", "已取消"], "poll_interval_seconds": 1,
                    "parameters": {"watch": {"keys": "string[]", "enabled": "boolean"},
                                   "import": {"url": "http(s) URL"},
                                   "download": {"id": "string", "folder?": "absolute path", "proxy?": "string"},
                                   "clip": {"source": "absolute local video path", "start": "seconds >= 0", "end": "seconds > start", "folder?": "absolute path"},
                                   "cancel": {"id": "string"},
                                   "capture_sources": {"refresh?": "boolean; async device enumeration"},
                                   "record_start": {"mode": "screen|region|window|camera", "region?": {"x": "int", "y": "int", "width": "int", "height": "int"},
                                       "hwnd?": "int from capture_sources.windows", "camera?": "device name", "audio?": "none|system|microphone|both",
                                       "microphone_index?": "int", "system_index?": "int", "fps?": "10|15|24|30|60", "quality?": "high|balanced|small",
                                       "cursor?": "boolean", "duration?": "seconds; 0 unlimited", "folder?": "absolute path"},
                                   "record_pause": {"id": "recording id"}, "record_resume": {"id": "recording id"},
                                   "record_stop": {"id": "recording id; finalize MP4"}, "record_state": {},
                                   "screenshot": {"mode": "screen|region|window", "region?": "rectangle", "hwnd?": "window handle", "folder?": "absolute path"}},
                    "capture_notes": ["录屏、麦克风和摄像头只在用户明确要求时调用", "录制显示浮动停止工具条", "停止后仍需等待已保存", "截图任务通过 state 查询"]}
        if action == "state":
            tabs, items, clients = app.store.snapshot()
            # Request headers and signed media URLs stay in the application.
            fields = ("id", "title", "kind", "status", "progress", "path", "error", "operation", "source_path", "start", "end", "elapsed")
            return {"ok": True, "tabs": tabs, "items": [{k: i.get(k, "") for k in fields} for i in items],
                    "clients": clients, "paused": app.store.paused, "default_folder": app.folder.get(), "recording": app.recorder.snapshot()}
        if action == "capture_sources":
            refresh = data.get("refresh", False)
            if type(refresh) is not bool:
                raise ValueError()
            return app.capture_sources(refresh)
        if action == "record_state":
            return dict(app.recorder.snapshot(), ok=True)
        if action == "record_start":
            return app.start_recording(data)
        if action == "screenshot":
            return app.capture_screenshot(data)
        if action in {"record_pause", "record_resume", "record_stop"}:
            ident = data.get("id")
            if not isinstance(ident, str) or not ident or ident != app.recorder.snapshot().get("id"):
                raise ApiError(404, "录制任务不存在，请先查询 record_state")
            method = {"record_pause": app.pause_recording, "record_resume": app.resume_recording, "record_stop": app.stop_recording}[action]
            return method()
        if action == "watch":
            keys, enabled = data.get("keys"), data.get("enabled")
            if not isinstance(keys, list) or not keys or not all(isinstance(k, str) for k in keys) or type(enabled) is not bool:
                raise ValueError()
            with app.store.lock:
                if any(k not in app.store.tabs for k in keys):
                    raise ApiError(404, "标签页不存在，请重新查询 state")
                if enabled:
                    app.store.paused = False
                app.store.set_watching(keys, enabled)
            app.pause_button.configure(text="暂停发现" if not app.store.paused else "继续发现")
            return {"ok": True, "keys": keys, "enabled": enabled}
        if action == "import":
            return app.store.add({"url": data.get("url", "")}, manual=True)
        if action == "download":
            proxy = data.get("proxy", app.proxy.get())
            proxy_value(proxy)
            folder = self.folder(data)
            with app.store.lock:
                item = app.store.items.get(data.get("id"))
                if not item:
                    raise ApiError(404, "视频记录不存在")
                if item.get("operation") in {"clip", "record", "screenshot"}:
                    raise ValueError()
                if item["status"] in BUSY | {"已保存"}:
                    return {"ok": True, "id": item["id"], "status": item["status"], "queued": False}
                app.store.update(item["id"], status="排队中", progress="等待下载", error="")
                app.pending.append((dict(item, proxy=proxy), folder))
                return {"ok": True, "id": item["id"], "status": "排队中", "queued": True}
        if action == "clip":
            return self.enqueue_clip(data)
        if action == "cancel":
            ident = data.get("id")
            if not isinstance(ident, str) or ident not in app.store.items:
                raise ApiError(404, "任务不存在")
            if app.store.items[ident]["status"] in BUSY:
                app.kill_job(ident)
            return {"ok": True, "id": ident, "status": app.store.items[ident]["status"]}
        raise ApiError(404, "未知 AI 接口")

    def folder(self, data):
        value = data.get("folder", self.app.folder.get())
        if not isinstance(value, str) or not value.strip():
            raise ValueError()
        folder = Path(value).expanduser().resolve()
        folder.mkdir(parents=True, exist_ok=True)
        return str(folder)

    def enqueue_clip(self, data):
        source = data.get("source")
        if not isinstance(source, str) or not source.strip():
            raise ValueError()
        path = Path(source).expanduser().resolve()
        if not path.is_file() or path.suffix.lower() not in {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".flv", ".ts"}:
            raise ValueError()
        start, end = data.get("start"), data.get("end")
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, end)) or not 0 <= start < end:
            raise ValueError()
        folder = self.folder(data)
        ident = uuid.uuid4().hex[:20]
        item = {"id": ident, "operation": "clip", "source_path": str(path), "start": start, "end": end,
                "title": path.name, "kind": "本地视频裁剪", "host": "本机", "source": "本机", "status": "排队中",
                "progress": "等待裁剪", "path": "", "error": "", "url": "", "headers": {}}
        with self.app.store.lock:
            if len(self.app.store.items) >= MAX_ITEMS:
                raise ApiError(409, "列表已满，请先清理记录")
            self.app.store.items[ident] = item
            self.app.pending.append((dict(item), folder))
        return {"ok": True, "id": ident, "status": "排队中"}
