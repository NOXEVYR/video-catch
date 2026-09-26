"""Native recording controls; device discovery never runs on Tk's thread."""
from __future__ import annotations

import math
import queue
import threading
import tkinter as tk
from tkinter import ttk

from ui import BG, PANEL, MUTED, ACCENT

MODES = {"全屏录制": "screen", "区域录制": "region", "窗口录制": "window", "仅摄像头": "camera"}
AUDIO = {"不录声音": "none", "系统声音": "system", "麦克风": "microphone", "系统 + 麦克风": "both"}
QUALITY = {"清晰优先": "high", "均衡": "balanced", "节省空间": "small"}
PROFILES = {"日常演示": ("30", "均衡"), "流畅动态": ("60", "清晰优先"), "轻量记录": ("15", "节省空间")}


def elapsed_text(seconds):
    seconds = max(0, int(seconds or 0))
    return f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}"


def is_busy(recorder):
    value = recorder.is_busy
    return bool(value() if callable(value) else value)


def recording_options(values, inventory, region=None, windows=()):
    """Validate UI selections without touching hardware or Tk."""
    mode = MODES.get(values["mode"], values["mode"])
    audio = AUDIO.get(values["audio"], values["audio"])
    quality = QUALITY.get(values["quality"], values["quality"])
    if mode not in MODES.values() or audio not in AUDIO.values() or quality not in QUALITY.values():
        raise ValueError("录制选项无效，请重新选择。")
    try:
        fps = int(values["fps"])
        duration = float(values["duration"])
    except (ValueError, TypeError):
        raise ValueError("帧率和定时秒数须填写数字；0 表示不限时。") from None
    if fps not in (10, 15, 24, 30, 60):
        raise ValueError("请选择 10、15、24、30 或 60 帧。")
    if not math.isfinite(duration) or duration < 0:
        raise ValueError("定时秒数须为不小于 0 的有限数字。")
    options = dict(mode=mode, audio=audio, fps=fps, quality=quality, cursor=bool(values["cursor"]), duration=duration)
    if mode == "region":
        if not region or region.get("width", 0) < 2 or region.get("height", 0) < 2:
            raise ValueError("请先框选要录制的屏幕区域。")
        options["region"] = dict(region)
    if mode == "window":
        window = next((w for w in windows if window_label(w) == values.get("window")), None)
        if not window:
            raise ValueError("请刷新并选择要录制的窗口。")
        options["hwnd"] = window["hwnd"]
    if mode == "camera" or values.get("overlay"):
        camera = next((c for c in inventory.get("cameras", ()) if c["name"] == values.get("camera")), None)
        if not camera:
            raise ValueError("没有可用的摄像头。请连接设备后点击「刷新设备」。")
        options["camera"] = camera["name"]
    for needed, key, selection in ((audio in ("system", "both"), "systems", "system"),
                                    (audio in ("microphone", "both"), "microphones", "microphone")):
        if needed:
            device = next((d for d in inventory.get(key, ()) if device_label(d) == values.get(selection)), None)
            if not device:
                name = "系统声音" if key == "systems" else "麦克风"
                raise ValueError(f"没有可用的{name}设备，请刷新设备或选择不录声音。")
            options[selection + "_index"] = device["index"]
    return options


def device_label(device):
    return f"{device['name']}  [{device['index']}]"


def window_label(window):
    return f"{window['title']}  [{window['hwnd']}]"


def widget_exists(widget):
    try:
        return bool(widget and widget.winfo_exists())
    except tk.TclError:
        return False


def cancel_timer(root, timer):
    if timer is not None:
        try:
            root.after_cancel(timer)
        except tk.TclError:
            pass


def panel_size(screen_width, screen_height, scale, content_height):
    """Keep transport visible on small displays and fit content on larger ones."""
    scale = max(1.0, min(2.0, scale))
    available_width = max(320, screen_width - round(40 * scale))
    available_height = max(240, screen_height - round(90 * scale))
    width = min(960, round(780 * scale), available_width)
    height = min(1100, max(round(760 * scale), content_height), available_height)
    return width, height, min(width, round(660 * scale)), min(height, round(480 * scale))


class RecordingPanel:
    def __init__(self, app):
        self.app = app
        self.window = None
        self.selector = None
        self.region = None
        self.windows = []
        self.inventory = {"cameras": [], "microphones": [], "systems": []}
        self._results = queue.Queue()
        self._discovering = False
        self._countdown = None
        self._pending_options = None
        self._timer = None
        self._screenshot_timer = None
        self._screenshot_restore = False
        self._generation = 0
        self.vars = {name: tk.StringVar(app.root, value=value) for name, value in
                     dict(mode="全屏录制", audio="不录声音", quality="均衡", fps="30", duration="0",
                          camera="", microphone="", system="", window="", countdown="3 秒", profile="日常演示").items()}
        self.vars["cursor"] = tk.BooleanVar(app.root, value=True)
        self.vars["overlay"] = tk.BooleanVar(app.root, value=False)
        self.vars["minimize"] = tk.BooleanVar(app.root, value=True)
        self.status = tk.StringVar(app.root, value="准备就绪")
        self.device_status = tk.StringVar(app.root, value="打开面板后检测设备")
        self.region_status = tk.StringVar(app.root, value="尚未框选区域")
        self.detail = tk.StringVar(app.root, value="录制与截图保存到主窗口所选文件夹。")
        self._settings = []

    @property
    def visible(self):
        return bool(widget_exists(self.window) and self.window.state() != "withdrawn")

    @property
    def is_visible(self):
        return self.visible

    def show(self):
        if self._screenshot_restore and getattr(self.app, "screenshot_pending", False):
            self.app.notice.set("正在截图，完成后会恢复录制面板。")
            return
        if widget_exists(self.window):
            self.window.deiconify()
            self.window.lift()
            self.refresh()
            return
        self._build()
        self._apply_devices()
        self.refresh_windows()
        if not self._discovering:
            self.refresh_devices()
        self._tick()

    def _build(self):
        self._generation += 1
        win = self.window = tk.Toplevel(self.app.root)
        win.withdraw()
        win.title("拾影 · 录屏与截图")
        win.configure(bg=BG)
        win.protocol("WM_DELETE_WINDOW", self.close)
        # Scroll the controls on smaller displays; actions remain pinned below.
        footer = ttk.Frame(win, padding=(20, 12))
        footer.pack(side="bottom", fill="x")
        self.start_button = ttk.Button(footer, text="开始录制", style="Accent.TButton", command=self.start)
        self.start_button.pack(side="left")
        self.pause_button = ttk.Button(footer, text="暂停", command=self._pause_resume)
        self.pause_button.pack(side="left", padx=8)
        self.stop_button = ttk.Button(footer, text="停止并保存", command=self._stop)
        self.stop_button.pack(side="left")
        self.screenshot_button = ttk.Button(footer, text="截取图片", command=self._screenshot)
        self.screenshot_button.pack(side="right")
        canvas = self.canvas = tk.Canvas(win, bg=BG, highlightthickness=0)
        scroll = ttk.Scrollbar(win, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        body = self.body = ttk.Frame(canvas, padding=20)
        child = canvas.create_window(0, 0, window=body, anchor="nw")
        body.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(child, width=event.width))
        # Toplevel bindings see wheel events from child fields too. Do not bind
        # globally, which would leave callbacks behind when this panel closes.
        def scroll_content(event):
            if event.delta:
                canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
            return "break"

        win.bind("<MouseWheel>", scroll_content)
        body.columnconfigure(1, weight=1)
        ttk.Label(body, text="录下眼前，留下灵感", font=("Microsoft YaHei UI", 19, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(body, textvariable=self.status, foreground=ACCENT, font=("Microsoft YaHei UI", 11, "bold")).grid(row=1, column=0, columnspan=3, sticky="w", pady=(5, 15))
        self._settings = []

        def combo(row, title, key, choices):
            ttk.Label(body, text=title).grid(row=row, column=0, sticky="w", padx=(0, 16), pady=5)
            widget = ttk.Combobox(body, textvariable=self.vars[key], values=tuple(choices), state="readonly")
            widget.grid(row=row, column=1, columnspan=2, sticky="ew", pady=5)
            self._settings.append((widget, "readonly"))
            return widget

        self.mode_box = combo(2, "录制范围", "mode", MODES)
        self.mode_box.bind("<<ComboboxSelected>>", lambda _: self.refresh())
        self.window_box = combo(3, "目标窗口", "window", ())
        area = ttk.Frame(body)
        area.grid(row=4, column=0, columnspan=3, sticky="ew", pady=5)
        self.region_button = ttk.Button(area, text="框选区域", command=self._select_region)
        self.region_button.pack(side="left")
        self.windows_button = ttk.Button(area, text="刷新窗口", command=self.refresh_windows)
        self.windows_button.pack(side="left", padx=8)
        ttk.Label(area, textvariable=self.region_status, style="Muted.TLabel").pack(side="left", padx=5)
        self._settings.extend(((self.region_button, "normal"), (self.windows_button, "normal")))
        self.camera_box = combo(5, "摄像头", "camera", ())
        self.overlay_check = ttk.Checkbutton(body, text="在屏幕画面叠加摄像头（右下角）", variable=self.vars["overlay"], command=self.refresh)
        self.overlay_check.grid(row=6, column=1, columnspan=2, sticky="w")
        self._settings.append((self.overlay_check, "normal"))
        self.audio_box = combo(7, "声音来源", "audio", AUDIO)
        self.audio_box.bind("<<ComboboxSelected>>", lambda _: self.refresh())
        self.system_box = combo(8, "系统音频设备", "system", ())
        self.microphone_box = combo(9, "麦克风设备", "microphone", ())
        devices = ttk.Frame(body)
        devices.grid(row=10, column=0, columnspan=3, sticky="ew", pady=(3, 12))
        self.devices_button = ttk.Button(devices, text="刷新设备", command=self.refresh_devices)
        self.devices_button.pack(side="left")
        self._settings.append((self.devices_button, "normal"))
        ttk.Label(devices, textvariable=self.device_status, style="Muted.TLabel", wraplength=500).pack(side="left", padx=12)
        profile = combo(11, "快速预设", "profile", PROFILES)
        profile.bind("<<ComboboxSelected>>", lambda _: self._profile())
        ttk.Label(body, text="画质 / 帧率").grid(row=12, column=0, sticky="w", pady=5)
        quality_row = ttk.Frame(body)
        quality_row.grid(row=12, column=1, columnspan=2, sticky="ew", pady=5)
        quality_row.columnconfigure(0, weight=1)
        quality = ttk.Combobox(quality_row, textvariable=self.vars["quality"], values=tuple(QUALITY), state="readonly")
        quality.grid(row=0, column=0, sticky="ew")
        fps = ttk.Combobox(quality_row, textvariable=self.vars["fps"], values=(10, 15, 24, 30, 60), state="readonly", width=7)
        fps.grid(row=0, column=1, padx=(12, 6))
        ttk.Label(quality_row, text="帧 / 秒", style="Muted.TLabel").grid(row=0, column=2)
        self._settings.extend(((quality, "readonly"), (fps, "readonly")))
        ttk.Label(body, text="定时停止（秒）").grid(row=14, column=0, sticky="w", pady=5)
        duration = ttk.Entry(body, textvariable=self.vars["duration"], width=15)
        duration.grid(row=14, column=1, sticky="ew", pady=5)
        ttk.Label(body, text="0 = 不限时", style="Muted.TLabel").grid(row=14, column=2, padx=12)
        self._settings.append((duration, "normal"))
        combo(15, "开始倒计时", "countdown", ("3 秒", "立即开始"))
        recording_flags = ttk.Frame(body)
        recording_flags.grid(row=16, column=0, columnspan=3, sticky="w")
        cursor = ttk.Checkbutton(recording_flags, text="录制鼠标指针", variable=self.vars["cursor"])
        cursor.pack(side="left")
        minimize = ttk.Checkbutton(recording_flags, text="录制时最小化主窗口", variable=self.vars["minimize"])
        minimize.pack(side="left", padx=12)
        self._settings.append((cursor, "normal"))
        self._settings.append((minimize, "normal"))
        tools = ttk.Frame(body)
        tools.grid(row=17, column=0, columnspan=3, sticky="ew", pady=(12, 4))
        ttk.Button(tools, text="屏幕批注 / 画笔", command=self.app.toggle_annotations).pack(side="left")
        ttk.Checkbutton(tools, text="启用全局快捷键", variable=self.app.record_hotkeys, command=self.app.toggle_record_hotkeys).pack(side="left", padx=12)
        wrapped = []
        for row, variable in ((18, self.app.record_hotkey_status), (19, self.detail), (20, self.app.folder)):
            label = ttk.Label(body, textvariable=variable, foreground=MUTED, wraplength=880)
            label.grid(row=row, column=0, columnspan=3, sticky="w", pady=4)
            wrapped.append(label)

        def resize_content(event):
            canvas.itemconfigure(child, width=event.width)
            for label in wrapped:
                label.configure(wraplength=max(200, event.width - 40))

        canvas.bind("<Configure>", resize_content)
        pending = [body]
        while pending:
            widget = pending.pop()
            # Prevent a closed combobox from changing its value when scrolling.
            widget.bind("<MouseWheel>", scroll_content)
            pending.extend(widget.winfo_children())
        win.update_idletasks()
        scale = win.winfo_fpixels("1i") / 96
        width, height, min_width, min_height = panel_size(win.winfo_screenwidth(), win.winfo_screenheight(), scale,
                                                        body.winfo_reqheight() + footer.winfo_reqheight() + 12)
        win.minsize(min_width, min_height)
        win.geometry(f"{width}x{height}+{max(0, (win.winfo_screenwidth() - width) // 2)}+{max(0, (win.winfo_screenheight() - height) // 2 - 30)}")
        win.deiconify()

    def _profile(self):
        fps, quality = PROFILES[self.vars["profile"].get()]
        self.vars["fps"].set(fps)
        self.vars["quality"].set(quality)

    def refresh_devices(self):
        if self._discovering or is_busy(self.app.recorder) or self._pending_options is not None:
            return
        self._discovering = True
        self.device_status.set("正在检测摄像头和音频设备…")
        results = self._results

        def discover():
            try:
                from recorder import device_inventory
                result = device_inventory()
            except Exception:
                result = {"cameras": [], "microphones": [], "systems": [], "diagnostics": ["设备检测失败，请检查设备连接后重试。"]}
            results.put(result)

        threading.Thread(target=discover, name="recording-device-list", daemon=True).start()
        self.refresh()

    def _read_devices(self):
        try:
            inventory = self._results.get_nowait()
        except queue.Empty:
            return
        self.inventory = inventory
        self._discovering = False
        self._apply_devices()

    def _apply_devices(self):
        if not widget_exists(self.window):
            return
        inventory = self.inventory
        for key, field, box, labels in (("cameras", "camera", self.camera_box, lambda d: d["name"]),
                                         ("systems", "system", self.system_box, device_label),
                                         ("microphones", "microphone", self.microphone_box, device_label)):
            choices = [labels(d) for d in inventory.get(key, ())]
            box.configure(values=choices)
            if self.vars[field].get() not in choices:
                default = next((labels(d) for d in inventory.get(key, ()) if d.get("default")), None)
                self.vars[field].set(default or (choices[0] if choices else ""))
        counts = f"摄像头 {len(inventory.get('cameras', []))} · 系统声音 {len(inventory.get('systems', []))} · 麦克风 {len(inventory.get('microphones', []))}"
        diagnostics = inventory.get("diagnostics") or []
        if isinstance(diagnostics, str):
            diagnostics = [diagnostics]
        self.device_status.set(counts + ("\n" + "；".join(str(d) for d in diagnostics) if diagnostics else ""))

    def refresh_windows(self):
        if is_busy(self.app.recorder) or self._pending_options is not None:
            return
        try:
            from capture_native import list_windows
            self.windows = list_windows()
            choices = [window_label(w) for w in self.windows]
            self.window_box.configure(values=choices)
            if self.vars["window"].get() not in choices:
                self.vars["window"].set(choices[0] if choices else "")
        except (OSError, RuntimeError, ValueError):
            self._error("无法读取窗口列表，请稍后刷新。")

    def _select_region(self):
        if is_busy(self.app.recorder) or self._pending_options is not None or (self.selector and self.selector.active):
            return
        from capture_overlays import select_region
        self.window.withdraw()
        generation = self._generation

        def selected(region):
            if generation != self._generation:
                return
            self.selector = None
            if region:
                self.region = dict(region)
                self.region_status.set(f"{region['width']} × {region['height']} · ({region['x']}, {region['y']})")
                self.vars["mode"].set("区域录制")
            if widget_exists(self.window):
                self.window.deiconify()
                self.refresh()

        try:
            self.selector = select_region(self.app.root, selected)
        except (OSError, RuntimeError, ValueError) as error:
            selected(None)
            self._error(str(error))

    def get_options(self):
        return recording_options({key: var.get() for key, var in self.vars.items()}, self.inventory, self.region, self.windows)

    def get_screenshot_options(self):
        if self.vars["mode"].get() == "仅摄像头":
            raise ValueError("截图支持全屏、区域或窗口，请先切换录制范围。")
        values = {key: var.get() for key, var in self.vars.items()}
        values.update(audio="none", overlay=False, duration="0", fps="30", quality="balanced")
        return recording_options(values, self.inventory, self.region, self.windows)

    def start(self):
        if is_busy(self.app.recorder) or self._pending_options is not None or (self.selector and self.selector.active):
            return
        try:
            self._pending_options = self.get_options()
        except (ValueError, RuntimeError, OSError) as error:
            self._error(str(error))
            return
        self._count_down(3 if self.vars["countdown"].get() == "3 秒" else 0)

    def _count_down(self, remaining):
        self._countdown = None
        if self._pending_options is None:
            return
        if remaining:
            self.status.set(f"{remaining} 秒后开始 · 点击「取消倒计时」可取消")
            self._countdown = self.app.root.after(1000, lambda: self._count_down(remaining - 1))
            self.refresh()
            return
        options = self._pending_options
        self._pending_options = None
        # A global/API request could have started while this countdown ran.
        if is_busy(self.app.recorder):
            self.refresh()
            return
        minimized = False
        try:
            if self.visible:
                self.window.withdraw()
            if self.vars["minimize"].get():
                try:
                    if self.app.root.state() == "normal" and self.app.root.winfo_ismapped():
                        self.app.root.iconify()
                        minimized = True
                except tk.TclError:
                    pass
            self.app.start_recording(options)
        except (ValueError, RuntimeError, OSError) as error:
            if minimized:
                self.app.root.deiconify()
            if widget_exists(self.window):
                self.window.deiconify()
            self._error(str(error))
        self.refresh()

    def cancel_countdown(self):
        cancel_timer(self.app.root, self._countdown)
        self._countdown = None
        self._pending_options = None

    def _pause_resume(self):
        try:
            if self.app.recorder.snapshot().get("status") == "录制暂停":
                self.app.resume_recording()
            else:
                self.app.pause_recording()
        except (ValueError, RuntimeError, OSError) as error:
            self._error(str(error))
        self.refresh()

    def _stop(self):
        if self._pending_options is not None:
            self.cancel_countdown()
        else:
            try:
                self.app.stop_recording()
            except (ValueError, RuntimeError, OSError) as error:
                self._error(str(error))
        self.refresh()

    def _screenshot(self):
        if (self._screenshot_timer is not None or getattr(self.app, "screenshot_pending", False)
                or self._pending_options is not None or (self.selector and self.selector.active)):
            return
        try:
            options = self.get_screenshot_options()
            restore = self.visible
            if restore:
                self.window.withdraw()
            # Let the compositor remove this panel before sampling the screen.
            generation = self._generation
            self._screenshot_timer = self.app.root.after(180, lambda: self._finish_screenshot(options, restore)
                                                        if generation == self._generation else None)
        except (ValueError, RuntimeError, OSError) as error:
            self._error(str(error))

    def screenshot(self):
        self._screenshot()

    def _finish_screenshot(self, options, restore=True):
        self._screenshot_timer = None
        self._screenshot_restore = restore
        try:
            self.app.capture_screenshot(options)
        except (ValueError, RuntimeError, OSError) as error:
            self._error(str(error))
            self._restore_after_screenshot(force=True)
        else:
            self._restore_after_screenshot()

    def _restore_after_screenshot(self, force=False):
        if self._screenshot_restore and (force or not getattr(self.app, "screenshot_pending", False)):
            self._screenshot_restore = False
            if widget_exists(self.window):
                self.window.deiconify()

    def _error(self, message):
        self.detail.set(message)
        self.app.notice.set(message)

    def refresh(self):
        self._read_devices()
        self._restore_after_screenshot()
        if not self.visible:
            return
        snapshot = self.app.recorder.snapshot() or {}
        status = snapshot.get("status") or "准备就绪"
        busy = is_busy(self.app.recorder)
        countdown = self._pending_options is not None
        locked = busy or countdown
        if not countdown:
            self.status.set(status + ("  ·  " + elapsed_text(snapshot.get("elapsed")) if busy or snapshot.get("path") else ""))
        if snapshot.get("error"):
            self.detail.set(snapshot["error"])
        elif status == "已保存" and snapshot.get("path"):
            self.detail.set("已保存：" + str(snapshot["path"]))
        for widget, normal in self._settings:
            widget.configure(state="disabled" if locked else normal)
        if not locked:
            self.devices_button.configure(state="disabled" if self._discovering else "normal")
            mode = self.vars["mode"].get()
            self.window_box.configure(state="readonly" if mode == "窗口录制" else "disabled")
            self.overlay_check.configure(state="disabled" if mode == "仅摄像头" else "normal")
            self.camera_box.configure(state="readonly" if mode == "仅摄像头" or self.vars["overlay"].get() else "disabled")
            audio = AUDIO[self.vars["audio"].get()]
            self.system_box.configure(state="readonly" if audio in ("system", "both") else "disabled")
            self.microphone_box.configure(state="readonly" if audio in ("microphone", "both") else "disabled")
        self.start_button.configure(state="disabled" if locked else "normal")
        self.pause_button.configure(text="继续" if status == "录制暂停" else "暂停", state="normal" if status in ("录制中", "录制暂停") else "disabled")
        self.stop_button.configure(text="取消倒计时" if countdown else "停止并保存", state="normal" if countdown or status in ("准备录制", "录制中", "录制暂停") and busy else "disabled")
        self.screenshot_button.configure(state="disabled" if countdown else "normal")

    def _tick(self):
        self._timer = None
        if widget_exists(self.window):
            self.refresh()
            self._timer = self.app.root.after(250, self._tick)

    def close(self):
        self._generation += 1
        self.cancel_countdown()
        self._screenshot_restore = False
        cancel_timer(self.app.root, self._screenshot_timer)
        self._screenshot_timer = None
        cancel_timer(self.app.root, self._timer)
        self._timer = None
        win, self.window = self.window, None
        if self.selector:
            try:
                self.selector.close()
            except tk.TclError:
                pass
            self.selector = None
        if widget_exists(win):
            win.destroy()


class RecordingToolbar:
    """Small always-on-top transport; never steals focus during status updates."""
    def __init__(self, app):
        self.app = app
        self.window = None
        self.status = tk.StringVar(app.root, value="")

    def _build(self):
        self.window = tk.Toplevel(self.app.root)
        self.window.title("拾影 · 录制控制")
        self.window.configure(bg=PANEL)
        self.window.resizable(False, False)
        self.window.attributes("-topmost", True)
        self.window.protocol("WM_DELETE_WINDOW", lambda: self.app.notice.set("录制仍在进行，请点击「停止并保存」结束录制。"))
        box = ttk.Frame(self.window, padding=12, style="Card.TFrame")
        box.pack(fill="both", expand=True)
        ttk.Label(box, textvariable=self.status, style="Card.TLabel", foreground=ACCENT, width=24).pack(side="left", padx=(0, 10))
        self.pause_button = ttk.Button(box, text="暂停", command=self._pause_resume)
        self.pause_button.pack(side="left")
        self.stop_button = ttk.Button(box, text="停止并保存", style="Accent.TButton", command=self._stop)
        self.stop_button.pack(side="left", padx=(8, 0))
        ttk.Button(box, text="批注", command=self.app.toggle_annotations).pack(side="left", padx=(8, 0))
        self.window.update_idletasks()
        width = self.window.winfo_reqwidth()
        self.window.geometry(f"+{max(0, (self.window.winfo_screenwidth() - width) // 2)}+24")
        try:
            from capture_native import exclude_from_capture
            exclude_from_capture(self.window)
        except (OSError, RuntimeError, ValueError):
            pass

    def _pause_resume(self):
        try:
            if self.app.recorder.snapshot().get("status") == "录制暂停":
                self.app.resume_recording()
            else:
                self.app.pause_recording()
        except (ValueError, RuntimeError, OSError) as error:
            self.app.notice.set(str(error))
        self.refresh()

    def _stop(self):
        try:
            self.app.stop_recording()
        except (ValueError, RuntimeError, OSError) as error:
            self.app.notice.set(str(error))
        self.refresh()

    def refresh(self):
        if not widget_exists(self.app.root):
            self.close()
            return
        if not is_busy(self.app.recorder):
            self.close()
            return
        if not widget_exists(self.window):
            self._build()
        snapshot = self.app.recorder.snapshot() or {}
        status = snapshot.get("status", "准备录制")
        self.status.set(status + " · " + elapsed_text(snapshot.get("elapsed")))
        self.pause_button.configure(text="继续" if status == "录制暂停" else "暂停", state="normal" if status in ("录制中", "录制暂停") else "disabled")
        self.stop_button.configure(state="disabled" if status == "正在保存" else "normal")

    def close(self):
        if widget_exists(self.window):
            self.window.destroy()
        self.window = None
