from __future__ import annotations

import multiprocessing as mp
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import uuid
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from core import Store, start_bridge, safe_headers, MAX_ITEMS
from engine import download_worker
from clipper import clip_worker
from ai_api import Api, ApiError, BUSY
from collaboration import collaboration_prompt, save_pairing
from runtime_paths import distribution_root, open_local
from pages import bili_page, video_page, observed_formats
from network import SYSTEM, DIRECT, proxy_value, discover_local_proxies

from ui import BG, PANEL, FG, MUTED, ACCENT


def enable_dpi_awareness():
    from capture_native import enable_dpi_awareness as enable_capture_dpi
    enable_capture_dpi()


class App:
    def __init__(self, root, smoke=False):
        self.root = root
        self.smoke = smoke
        self.store = Store()
        self.bridge = start_bridge(self.store, 0 if smoke else 18796)
        self.jobs = {}
        self.pending = []
        self.closing = False
        self.folder = tk.StringVar(value=str(Path.home() / ("Movies" if sys.platform == "darwin" else "Videos") / "VideoCatch"))
        self.notice = tk.StringVar(value="准备就绪 · 添加链接，或连接浏览器开始发现视频。")
        self.connection = tk.StringVar(value="等待浏览器连接")
        self.detail = tk.StringVar(value="选择视频查看详情 · 按 Ctrl / Shift 可多选")
        self.step = tk.StringVar(value="① 先连接浏览器扩展：复制配对码，在扩展弹窗中粘贴并连接。")
        self.proxy = tk.StringVar(value=SYSTEM)
        self.network_results = queue.Queue()
        self.ai = Api(self)
        self.bridge.ai = self.ai
        self.ai_enabled = tk.BooleanVar(value=False)
        from recorder import Recorder
        self.recorder = Recorder()
        self.recording_panel = None
        self.recording_toolbar = None
        self.annotations = None
        self.global_hotkeys = None
        self.record_hotkeys = tk.BooleanVar(value=False)
        self.record_hotkey_status = tk.StringVar(value="快捷键未启用")
        self.capture_events = queue.Queue()
        self.screenshot_pending = False
        self.capture_inventory = {"cameras": [], "microphones": [], "systems": [], "loading": False, "loaded": False}
        self.last_capture_options = {"mode": "screen", "audio": "none", "fps": 30,
                                     "quality": "balanced", "cursor": True, "duration": 0}
        self.exit_after_capture = False
        self.recording_notice_id = None
        self.build_ui()
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.tick()

    def build_ui(self):
        from ui import build_ui
        build_ui(self)

    def tree(self, parent, columns):
        box = ttk.Frame(parent, style="Card.TFrame")
        box.pack(fill="both", expand=True)
        tree = ttk.Treeview(box, columns=[c[0] for c in columns], show="headings", selectmode="extended", height=5)
        for key, title, width in columns:
            tree.heading(key, text=title, anchor="w")
            tree.column(key, width=width, minwidth=min(width, 65), stretch=True)
        scroll = ttk.Scrollbar(box, orient="vertical", command=tree.yview)
        hscroll = ttk.Scrollbar(box, orient="horizontal", command=tree.xview)
        def show_scroll(bar, first, last):
            bar.set(first, last)
            if float(first) <= 0 and float(last) >= 1:
                bar.grid_remove()
            else:
                bar.grid()
        tree.configure(yscrollcommand=lambda a, b: show_scroll(scroll, a, b),
                       xscrollcommand=lambda a, b: show_scroll(hscroll, a, b))
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)
        tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        hscroll.grid(row=1, column=0, sticky="ew")
        return tree

    def copy_pairing(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.bridge.token)
        self.notice.set("已复制本次配对码，可连接浏览器扩展或运行 AI 客户端 --pair；程序重启后需重新配对。")

    def toggle_ai(self):
        if self.ai_enabled.get():
            self.ai.enabled.set()
            self.notice.set("AI 接口已启用：127.0.0.1:18796/api/v1/；客户端需使用本次配对码。")
        else:
            self.ai.enabled.clear()
            self.notice.set("AI 接口已关闭；已提交的任务仍可在视频列表中取消。")

    def copy_ai_collaboration(self):
        try:
            prompt = collaboration_prompt()
            save_pairing(self.bridge.token, self.bridge.server_port)
            self.root.clipboard_clear()
            self.root.clipboard_append(prompt)
        except (OSError, ValueError, tk.TclError) as error:
            self.notice.set(f"协作指引未复制成功：{error}；请重试。")
            return
        self.ai_enabled.set(True)
        self.ai.enabled.set()
        self.notice.set("协作已开启，指引已复制。粘贴给能执行本机命令的 AI，再描述要下载或截取的视频。")

    def open_recording(self):
        from recording_ui import RecordingPanel
        if self.recording_panel is None:
            self.recording_panel = RecordingPanel(self)
        self.recording_panel.show()

    def _capture_ready(self):
        if self.exit_after_capture:
            raise RuntimeError("拾影正在结束录制并退出")
        selector = getattr(self.recording_panel, "selector", None)
        if selector is not None and getattr(selector, "active", False):
            raise RuntimeError("请先完成或取消选区")
        with self.store.lock:
            if len(self.store.items) >= MAX_ITEMS:
                raise ValueError("列表已满，请先清除记录")

    def _capture_folder(self, options):
        value = options.get("folder", self.folder.get())
        if not isinstance(value, str) or not value.strip():
            raise ValueError("请选择保存目录")
        path = Path(value).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def _capture_item(self, ident, operation, title, status):
        with self.store.lock:
            self.store.items[ident] = {"id": ident, "operation": operation, "title": title,
                "kind": "屏幕录制" if operation == "record" else "屏幕截图", "source": "本机", "host": "本机",
                "url": "", "headers": {}, "size": "", "status": status, "progress": "", "path": "", "error": ""}

    def start_recording(self, options):
        from recorder import normalize_options
        self._capture_ready()
        if self.recorder.is_busy:
            raise RuntimeError("已有录制任务，请先停止并保存")
        options = dict(options)
        folder = self._capture_folder(options)
        options.pop("folder", None)
        normalized = normalize_options(options)
        if self.annotations and self.annotations.active and normalized["mode"] in {"window", "camera"}:
            raise ValueError("窗口或摄像头录制不能包含桌面画笔，请先关闭标注")
        snapshot = self.recorder.start(normalized, folder)
        self.last_capture_options = dict(normalized)
        self._capture_item(snapshot["id"], "record", "屏幕录制", snapshot["status"])
        self.recording_notice_id = None
        from recording_ui import RecordingToolbar
        if self.recording_toolbar is None:
            self.recording_toolbar = RecordingToolbar(self)
        self.notice.set("录制已启动，使用浮动工具条停止并保存。")
        return dict(snapshot, ok=True)

    def pause_recording(self):
        return dict(self.recorder.pause(), ok=True)

    def resume_recording(self):
        return dict(self.recorder.resume(), ok=True)

    def stop_recording(self):
        return dict(self.recorder.stop(), ok=True)

    def capture_screenshot(self, options):
        from recorder import normalize_options, screenshot
        self._capture_ready()
        if self.screenshot_pending:
            raise RuntimeError("正在截图，请等待完成")
        options = dict(options)
        folder = self._capture_folder(options)
        options.pop("folder", None)
        if options.get("mode", "screen") == "camera":
            raise ValueError("截图请使用全屏、区域或窗口模式")
        if self.annotations and self.annotations.active and options.get("mode") == "window":
            raise ValueError("窗口截图不能包含桌面画笔，请先退出标注或改用区域截图")
        options.update(audio="none", camera="", duration=0)
        normalized = normalize_options(options)
        ident = uuid.uuid4().hex[:20]
        self._capture_item(ident, "screenshot", "屏幕截图", "截图中")
        self.screenshot_pending = True
        events = self.capture_events
        def capture():
            try:
                result = screenshot(normalized, folder)
            except Exception as error:
                result = {"status": "失败", "error": str(error), "path": ""}
            events.put(("screenshot", ident, result))
        threading.Thread(target=capture, daemon=True).start()
        return {"ok": True, "id": ident, "status": "截图中"}

    def capture_sources(self, refresh=False):
        from capture_native import desktop_bounds, list_monitors, list_windows
        if (refresh or not self.capture_inventory["loaded"]) and not self.capture_inventory["loading"]:
            self.capture_inventory["loading"] = True
            events = self.capture_events
            def load_devices():
                from recorder import device_inventory
                try:
                    result = device_inventory()
                except Exception as error:
                    result = {"cameras": [], "microphones": [], "systems": [], "error": str(error)}
                events.put(("devices", "", result))
            threading.Thread(target=load_devices, daemon=True).start()
        return {"ok": True, "desktop": desktop_bounds(), "monitors": list_monitors(),
                "windows": list_windows(), "devices": dict(self.capture_inventory)}

    def toggle_annotations(self):
        if self.recorder.is_busy and self.recorder.snapshot().get("options", {}).get("mode") in {"window", "camera"}:
            self.notice.set("桌面画笔适用于全屏或区域录制；窗口／摄像头录制不会包含桌面标注。")
            return
        from capture_overlays import AnnotationOverlay
        if self.annotations is None:
            self.annotations = AnnotationOverlay(self.root, on_input_ready=self._raise_recording_controls)
        self.annotations.toggle()

    def _raise_recording_controls(self):
        window = getattr(self.recording_toolbar, "window", None)
        if window is not None and window.winfo_exists():
            annotation = getattr(self.annotations, "toolbar", None)
            if annotation is not None and annotation.winfo_exists():
                from capture_native import _tk_hwnd, window_info, place_window
                window.update_idletasks()
                annotation.update_idletasks()
                try:
                    controls_bounds = window_info(_tk_hwnd(window))
                    annotation_bounds = window_info(_tk_hwnd(annotation))
                except (OSError, ValueError, tk.TclError):
                    # A minimized or closing control window must not stop the UI tick.
                    return
                minimum_top = controls_bounds["y"] + controls_bounds["height"] + 12
                if annotation_bounds["y"] < minimum_top:
                    place_window(annotation, dict(x=annotation_bounds["x"], y=minimum_top,
                                                  width=annotation_bounds["width"],
                                                  height=annotation_bounds["height"]))
            window.lift()

    def _hotkey_action(self, action):
        try:
            if self.exit_after_capture:
                return
            state = self.recorder.snapshot().get("status")
            if action == "start_stop":
                if self.recorder.is_busy:
                    self.stop_recording()
                elif self.recording_panel is not None:
                    self.recording_panel.start()
                else:
                    self.open_recording()
            elif action == "pause_resume":
                if state == "录制暂停":
                    self.resume_recording()
                elif state == "录制中":
                    self.pause_recording()
            elif action == "screenshot":
                if self.recording_panel is None:
                    self.open_recording()
                    self.notice.set("先在录制面板选择截图范围，再按截图快捷键。")
                else:
                    self.recording_panel.screenshot()
            elif action == "annotate":
                self.toggle_annotations()
        except (OSError, ValueError, RuntimeError) as error:
            self.notice.set(str(error))

    def toggle_record_hotkeys(self):
        if self.global_hotkeys is not None:
            self.global_hotkeys.close()
            self.global_hotkeys = None
        if self.record_hotkeys.get():
            from capture_native import HotkeyManager
            self.global_hotkeys = HotkeyManager(self.root, {name: lambda n=name: self._hotkey_action(n)
                for name in ("start_stop", "pause_resume", "screenshot", "annotate")})
            result = self.global_hotkeys.start()
            failed = [item.get("hotkey", name) for name, item in result.items() if not item.get("registered")]
            self.record_hotkey_status.set("快捷键占用：" + "、".join(failed) if failed else "Ctrl+Alt+F9 录制/停止 · F10 暂停 · F11 截图 · F12 画笔")
        else:
            self.record_hotkey_status.set("快捷键未启用")

    def _refresh_capture(self):
        while True:
            try:
                action, ident, result = self.capture_events.get_nowait()
            except queue.Empty:
                break
            if action == "devices":
                self.capture_inventory = dict(result, loading=False, loaded=True)
            elif action == "screenshot":
                self.screenshot_pending = False
                self.store.update(ident, **{k: result.get(k, "") for k in ("status", "path", "error")},
                                  progress="100%" if result.get("status") == "已保存" else "截图未完成")
                self.notice.set("截图已保存：" + result["path"] if result.get("status") == "已保存" else "截图失败：" + result.get("error", "未知错误"))
        snapshot = self.recorder.snapshot()
        ident = snapshot.get("id")
        if ident:
            elapsed = max(0, int(snapshot.get("elapsed", 0)))
            self.store.update(ident, **{k: snapshot.get(k, "") for k in ("status", "path", "error")},
                elapsed=elapsed, progress=f"{elapsed // 3600:02}:{elapsed // 60 % 60:02}:{elapsed % 60:02}")
            if snapshot.get("status") in {"已保存", "失败"} and self.recording_notice_id != ident:
                self.recording_notice_id = ident
                self.notice.set("录制已保存：" + snapshot.get("path", "") if snapshot["status"] == "已保存" else "录制失败：" + snapshot.get("error", ""))
        if self.recording_panel is not None:
            self.recording_panel.refresh()
        if self.recording_toolbar is not None:
            self.recording_toolbar.refresh()
            if self.annotations is not None and self.annotations.active:
                self._raise_recording_controls()

    def clip_dialog(self):
        source = filedialog.askopenfilename(parent=self.root, title="选择要裁剪的本地视频", filetypes=[("视频", "*.mp4 *.mkv *.mov *.webm *.avi *.m4v *.flv *.ts")])
        if not source:
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("裁剪视频 · 另存 MP4")
        dialog.configure(bg=BG)
        dialog.transient(self.root)
        dialog.resizable(False, False)
        box = ttk.Frame(dialog, padding=20)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text=Path(source).name, wraplength=420).grid(row=0, columnspan=2, sticky="w", pady=8)
        values = []
        for row, label, default in [(1, "开始时间（秒）", "0"), (2, "结束时间（秒）", "10")]:
            ttk.Label(box, text=label).grid(row=row, column=0, padx=8, pady=8)
            entry = ttk.Entry(box)
            entry.insert(0, default)
            entry.grid(row=row, column=1)
            values.append(entry)
        ttk.Label(box, text="精确裁剪并重新编码为 MP4，保存到主窗口所选目录。", wraplength=420).grid(row=3, columnspan=2, pady=8)
        def submit():
            try:
                self.ai.enqueue_clip({"source": source, "start": float(values[0].get()), "end": float(values[1].get())})
                self.notice.set("裁剪任务已加入队列，源文件保持不变。")
                dialog.destroy()
            except (ValueError, OSError, ApiError):
                messagebox.showerror("无法裁剪", "请检查视频文件、保存目录和起止秒数（结束须大于开始）。", parent=dialog)
        ttk.Button(box, text="开始裁剪", command=submit).grid(row=4, columnspan=2, pady=8)

    def open_guide(self):
        base = distribution_root()
        open_local(base / "使用指南.html")
        if sys.platform == "darwin":
            open_local(base / "extension")
            return
        import ctypes
        desktop = ctypes.create_unicode_buffer(260)
        ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, desktop)
        standalone = Path(desktop.value) / "拾影浏览器扩展"
        open_local(standalone if (standalone / "manifest.json").is_file() else base / "extension")

    def pause(self):
        with self.store.lock:
            self.store.paused = not self.store.paused
        self.pause_button.configure(text="继续发现" if self.store.paused else "暂停发现")
        self.notice.set("已暂停发现新视频。已有下载继续。" if self.store.paused else "已继续发现视频。")

    def unwatch(self):
        with self.store.lock:
            self.store.watching.clear()

    def toggle_tabs(self):
        self.store.toggle(self.tabs.selection())
        self.notice.set("监听选择已更新。请播放或刷新对应页面，让浏览器重新请求视频。")

    def start_tabs(self):
        keys = self.tabs.selection()
        if not keys:
            self.notice.set("请先单击左侧的视频页面。列表为空时，先连接浏览器扩展。")
            return
        with self.store.lock:
            self.store.paused = False
            self.store.set_watching(keys, True)
        self.pause_button.configure(text="暂停发现")
        self.refresh()
        self.notice.set(f"已开始监听 {len(keys)} 个页面。请回到网页刷新并播放；B站等待「音画合并」，YouTube 可直接选条目保存。")

    def stop_tabs(self):
        self.store.set_watching(self.tabs.selection(), False)
        self.refresh()
        self.notice.set("已停止所选页面的监听。已经开始的下载继续运行。")

    def detect_proxy(self):
        self.detect_button.configure(state="disabled")
        self.notice.set("正在检测常见本机代理端口及 YouTube 连通性，约需 5 秒…")
        def detect():
            try:
                self.network_results.put(discover_local_proxies())
            except Exception:
                self.network_results.put([])
        threading.Thread(target=detect, daemon=True).start()

    def add_tab_pages(self):
        for key in self.tabs.selection():
            tab = self.store.tabs.get(key)
            if tab:
                self.store.add({"client": tab["client"], "tabId": tab["tabId"], "url": video_page(tab["url"]) or tab["url"], "title": tab["title"], "headers": {"Referer": tab["url"]}}, manual=True)
        self.refresh()
        self.notice.set("网页已加入列表。B 站建议先勾选监听并刷新，等待「B站音画合并」后保存。")

    def click_checkbox(self, event):
        row = self.tabs.identify_row(event.y)
        if row and self.tabs.identify_column(event.x) == "#1":
            self.store.toggle([row])
            self.refresh()
            return "break"

    def import_url(self):
        try:
            result = self.store.add({"url": self.url.get().strip()}, manual=True)
            if result.get("full"):
                self.notice.set("列表已达到 1000 条，请先清理。")
                return
            self.url.delete(0, "end")
            self.notice.set("链接已添加。选中后点击「保存所选视频」。")
            self.refresh()
            if result.get("id"):
                self.media.selection_set(result["id"])
                self.media.see(result["id"])
        except ValueError as error:
            messagebox.showerror("无法添加", str(error), parent=self.root)

    def choose_folder(self):
        folder = filedialog.askdirectory(parent=self.root, initialdir=str(Path.home()), title="选择视频保存目录")
        if folder:
            self.folder.set(folder)

    def open_folder(self):
        try:
            p = Path(self.folder.get()).expanduser().resolve()
            p.mkdir(parents=True, exist_ok=True)
            open_local(p)
        except OSError as error:
            messagebox.showerror("无法打开目录", str(error), parent=self.root)

    def enqueue(self):
        selected = self.media.selection()
        if not selected:
            self.notice.set("请先选中一个或多个视频。按 Ctrl / Shift 可以多选。")
            return
        try:
            proxy_value(self.proxy.get())
            if not self.folder.get().strip():
                raise ValueError("请选择保存目录")
            folder = Path(self.folder.get()).expanduser().resolve()
            folder.mkdir(parents=True, exist_ok=True)
        except (OSError, ValueError) as error:
            messagebox.showerror("无法开始下载", str(error), parent=self.root)
            return
        with self.store.lock:
            for ident in selected:
                item = self.store.items.get(ident)
                if item and item.get("operation") not in {"clip", "record", "screenshot"} and item["status"] not in BUSY | {"已保存"}:
                    self.store.update(ident, status="排队中", progress="等待下载", error="")
                    self.pending.append((dict(item, proxy=self.proxy.get()), str(folder)))
        self.notice.set("已加入下载队列，最多同时下载 2 个视频。")

    def kill_job(self, ident):
        if self.store.items.get(ident, {}).get("operation") == "record":
            if self.recorder.snapshot().get("id") == ident and self.recorder.is_busy:
                self.stop_recording()
            return
        if self.store.items.get(ident, {}).get("operation") == "screenshot":
            self.notice.set("截图正在保存，请等待完成。")
            return
        job = self.jobs.pop(ident, None)
        if job:
            process, events = job
            if process.is_alive():
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], creationflags=subprocess.CREATE_NO_WINDOW,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
                else:
                    process.terminate()
            process.join(timeout=2)
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
            events.close()
        self.pending = [(item, folder) for item, folder in self.pending if item["id"] != ident]
        self.store.update(ident, status="已取消", progress="临时文件保留，可重试")

    def cancel_selected(self):
        for ident in self.media.selection():
            if self.store.items.get(ident, {}).get("status") in BUSY:
                self.kill_job(ident)

    def clear_selected(self):
        with self.store.lock:
            for ident in self.media.selection():
                if self.store.items.get(ident, {}).get("status") not in BUSY:
                    self.store.items.pop(ident, None)
        self.notice.set("已清除所选记录，磁盘上的视频保留。正在下载的记录请先取消。")

    def show_detail(self):
        selection = self.media.selection()
        item = self.store.items.get(selection[0]) if selection else None
        if item:
            self.detail.set((item["error"] or item["path"] or f"{item['source']} · {item['host']} · {item['kind']}；同页可能包含广告或不同清晰度，请核对后保存。")[:500])
        else:
            self.detail.set("选择视频查看详情 · 按 Ctrl / Shift 可多选")

    @staticmethod
    def reconcile(tree, rows):
        old = set(tree.get_children())
        for ident, values in rows:
            if ident in old:
                if tuple(str(v) for v in tree.item(ident, "values")) != tuple(str(v) for v in values):
                    tree.item(ident, values=values)
                old.remove(ident)
            else:
                tree.insert("", "end", iid=ident, values=values)
        for ident in old:
            tree.delete(ident)

    def refresh(self):
        tabs, items, count = self.store.snapshot()
        watching = sum(t["watching"] for t in tabs)
        self.connection.set(f"{'已暂停发现 · ' if self.store.paused else ''}{count} 个浏览器已连接  /  {watching} 个标签页监听中  /  {len(items)} 条视频")
        if self.store.paused:
            self.step.set("已暂停发现。点击「继续发现」，然后回网页播放；已有下载继续。")
        elif not count:
            self.step.set("网页抓取：连接浏览器 → 选择页面 → 开始监听 → 播放视频")
        elif not watching:
            self.step.set("浏览器已连接。选中左侧页面，点击「开始监听」，然后回网页播放。")
        elif not items:
            self.step.set("正在等待视频请求。请回到监听的网页刷新并播放。")
        else:
            self.step.set("选中需要的视频并保存；B 站推荐选择「音画合并」条目。")
        self.reconcile(self.tabs, [(t["key"], ("☑" if t["watching"] else "☐", t["title"], t["browser"])) for t in tabs])
        self.reconcile(self.media, [(i["id"], tuple(i[k] for k in ("title", "kind", "host", "status", "progress"))) for i in items])
        from ui import refresh_ui
        refresh_ui(self, tabs, items, count)
        self.show_detail()

    def tick(self):
        if self.closing:
            return
        self.ai.pump()
        self._refresh_capture()
        if self.exit_after_capture and not self.recorder.is_busy and not self.screenshot_pending:
            self.close()
            return
        try:
            proxies = self.network_results.get_nowait()
            self.detect_button.configure(state="normal")
            self.proxy_box.configure(values=(SYSTEM, DIRECT, *proxies))
            if proxies:
                self.proxy.set(proxies[0])
                self.notice.set(f"已选择可访问 YouTube 的本机代理 {proxies[0]}。现在可以重新保存失败的视频。")
            else:
                self.notice.set("没有找到可访问 YouTube 的常见本机代理。请启动你的代理客户端，或在「下载连接」中填写其 HTTP / SOCKS5 地址。")
        except queue.Empty:
            pass
        for ident, (process, events) in list(self.jobs.items()):
            def drain():
                try:
                    while True:
                        _ident, fields = events.get_nowait()
                        self.store.update(ident, **fields)
                except queue.Empty:
                    pass
            drain()
            if not process.is_alive():
                process.join(timeout=0)
                drain()
                if self.store.items.get(ident, {}).get("status") in BUSY:
                    self.store.update(ident, status="失败", progress="任务进程已退出", error="任务未完成，请检查输入和保存空间后重试。")
                del self.jobs[ident]
                events.close()
        while self.pending and len(self.jobs) < 2:
            item, folder = self.pending.pop(0)
            events = mp.get_context("spawn").Queue()
            worker = clip_worker if item.get("operation") == "clip" else download_worker
            process = mp.get_context("spawn").Process(target=worker, args=(item, folder, events))
            try:
                process.start()
                self.jobs[item["id"]] = (process, events)
            except OSError as error:
                events.close()
                self.store.update(item["id"], status="失败", error=str(error), progress="无法启动下载进程")
        self.refresh()
        self.tick_timer = self.root.after(500, self.tick)

    def close(self):
        if self.recorder.is_busy or self.screenshot_pending:
            if not self.exit_after_capture:
                if not messagebox.askyesno("结束录制并退出", "正在录制或保存截图。先停止并保存，再退出拾影？", parent=self.root):
                    return
                self.exit_after_capture = True
                self.ai.enabled.clear()
                if self.recorder.is_busy:
                    self.recorder.stop()
                self.notice.set("正在保存录制，请等待完成后自动退出。")
            return
        if self.jobs or self.pending:
            if not messagebox.askyesno("退出拾影", "还有视频任务正在执行或排队。退出将停止任务，是否退出？", parent=self.root):
                self.exit_after_capture = False
                return
        self.closing = True
        self.ai.enabled.clear()
        if self.global_hotkeys is not None:
            self.global_hotkeys.close()
        if self.annotations is not None:
            self.annotations.close()
        if self.recording_panel is not None:
            self.recording_panel.close()
        if self.recording_toolbar is not None:
            self.recording_toolbar.close()
        if getattr(self, "tick_timer", None):
            self.root.after_cancel(self.tick_timer)
        if getattr(self, "layout_timer", None):
            self.root.after_cancel(self.layout_timer)
        for ident in list(self.jobs):
            self.kill_job(ident)
        self.pending.clear()
        self.bridge.shutdown()
        self.bridge.server_close()
        # Break the controller cycle on Tk's thread: otherwise a later HTTP thread's
        # garbage collection can finalize the closed interpreter and its images.
        self.bridge.ai = None
        self.ai.app = None
        self.root.update_idletasks()
        self.root.destroy()
        self.recording_panel = self.recording_toolbar = self.annotations = self.global_hotkeys = None


def main():
    enable_dpi_awareness()
    root = tk.Tk()
    try:
        captured_check = "--verify-capture-json" in sys.argv
        clip_check = "--verify-clip-json" in sys.argv
        verification = "--verify-download" in sys.argv or captured_check or clip_check
        recording_check = "--verify-recording-json" in sys.argv
        app = App(root, smoke="--smoke-test" in sys.argv or verification or recording_check)
        if "--smoke-test" in sys.argv:
            root.after(1200, app.close)
        if recording_check:
            # Developer check: options explicitly identify an isolated test source.
            options_file, folder, report = sys.argv[2:5]
            options = json.loads(Path(options_file).read_text(encoding="utf-8"))
            if not 0 < options.get("duration", 0) <= 10 or options.get("audio", "none") != "none":
                raise ValueError("录制验证需要 0～10 秒时长和关闭声音")
            root.withdraw()
            app.folder.set(folder)
            app.start_recording(options)
            def check_recording():
                if app.recorder.is_busy:
                    root.after(250, check_recording)
                    return
                Path(report).write_text(json.dumps(app.recorder.snapshot(), ensure_ascii=False), encoding="utf-8")
                app.close()
            root.after(250, check_recording)
        if verification:
            # Developer integration check; uses the same UI queue and frozen worker path.
            url, folder, report = sys.argv[2:5]
            captured = json.loads(Path(url).read_text(encoding="utf-8")) if captured_check else None
            if captured:
                url = captured["url"]
            root.withdraw()
            app.folder.set(folder)
            result = app.ai.enqueue_clip(json.loads(Path(url).read_text(encoding="utf-8"))) if clip_check else app.store.add({"url": url}, manual=True)
            ident = result["id"]
            if captured:
                app.store.update(ident, formats=observed_formats(captured.get("formats", [])),
                                 title=str(captured.get("title", "test"))[:300],
                                 headers=safe_headers(captured.get("headers", {})))
                app.proxy.set(captured.get("proxy", SYSTEM))
            app.refresh()
            app.media.selection_set(ident)
            if not clip_check:
                app.enqueue()
            attempts = [0]
            def check():
                attempts[0] += 1
                item = app.store.items[ident]
                if (item["status"] in {"失败", "已保存"} and not app.jobs) or attempts[0] > 120:
                    Path(report).write_text(json.dumps({k: item[k] for k in ["status", "path", "error"]}, ensure_ascii=False), encoding="utf-8")
                    for key in list(app.jobs):
                        app.kill_job(key)
                    app.pending.clear()
                    app.close()
                else:
                    root.after(500, check)
            root.after(500, check)
    except OSError as error:
        messagebox.showerror("拾影无法启动", f"本机连接端口不可用。请检查是否已经打开拾影。\n{error}", parent=root)
        root.destroy()
        raise SystemExit(1)
    root.mainloop()


if __name__ == "__main__":
    mp.freeze_support()
    main()
