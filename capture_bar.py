"""Compact Tk capture entry. All methods run on the Tk thread."""
from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk

from capture_overlays import MacRegionSelector, RegionSelector, region_from_points
from recording_ui import RecordingPanel, cancel_timer, elapsed_text, is_busy, widget_exists, window_label
from ui import BG


class _ConfirmRegion:
    """A release previews the rectangle; Enter commits it, Esc cancels it."""

    def __init__(self, root, on_selected):
        self._preview = None
        super().__init__(root, on_selected)
        surfaces = getattr(self, "_surfaces", [(self.window, self.canvas, self.bounds, self._box, self._label)])
        for window, _canvas, _bounds, _box, _label in surfaces:
            window.bind("<Return>", self._confirm)
            window.bind("<KP_Enter>", self._confirm)

    def _release(self, event):
        if self._start is None:
            return
        self._preview = region_from_points(self._start, self._point(event), self.bounds)
        if self._preview:
            for _window, canvas, _bounds, _box, label in getattr(
                    self, "_surfaces", [(self.window, self.canvas, self.bounds, self._box, self._label)]):
                canvas.itemconfigure(label, text=(f"{self._preview['width']} × {self._preview['height']} · "
                                                  "Enter 确认 · 重新拖动调整 · Esc 取消"))
        self._start = None

    def _press(self, event):
        self._preview = None
        super()._press(event)

    def _confirm(self, _event=None):
        if self._preview:
            self._finish(self._preview)


class _WindowsConfirmRegion(_ConfirmRegion, RegionSelector):
    pass


class _MacConfirmRegion(_ConfirmRegion, MacRegionSelector):
    pass


def select_confirm_region(root, on_selected):
    return (_MacConfirmRegion if sys.platform == "darwin" else _WindowsConfirmRegion)(root, on_selected)


class CaptureBar:
    """Quick recording/screenshot entry; RecordingPanel remains the settings model."""

    RANGES = {"全屏": "全屏录制", "窗口": "窗口录制", "区域": "区域录制"}

    def __init__(self, app, mode="record"):
        self.app = app
        self.panel = getattr(app, "recording_panel", None)
        if self.panel is None:
            self.panel = RecordingPanel(app)
            app.recording_panel = self.panel
        self.mode = mode
        self.window = None
        self.selector = None
        self._generation = 0
        self._countdown = None
        self._pending_options = None
        self._screenshot_timer = None
        self._screenshot_id = None
        self._screenshot_inflight = False
        self._recording_restore = False
        self._restore_main = False
        self._settings_open = False
        self._layout_key = None
        self.range_var = tk.StringVar(app.root)
        self.system_var = tk.BooleanVar(app.root)
        self.mic_var = tk.BooleanVar(app.root)
        self.status = tk.StringVar(app.root, value="准备就绪")
        if mode == "screenshot":
            self.panel.vars["mode"].set(self.panel.screenshot_mode)
        self._sync_from_panel()

    @property
    def visible(self):
        return bool(widget_exists(self.window) and self.window.state() != "withdrawn")

    def show(self, mode=None):
        if mode not in (None, "record", "screenshot"):
            raise ValueError("未知采集模式")
        if self.panel.selector is not None and getattr(self.panel.selector, "active", False):
            return
        if mode:
            changed = mode != self.mode
            if changed and self.mode == "record":
                self.panel.record_mode = self.panel.vars["mode"].get()
            elif changed and self.panel.vars["mode"].get() in self.RANGES.values():
                self.panel.screenshot_mode = self.panel.vars["mode"].get()
            self.mode = mode
            if changed:
                self.panel.vars["mode"].set(self.panel.screenshot_mode if mode == "screenshot" else self.panel.record_mode)
        if self._screenshot_inflight or self._pending_options is not None:
            return
        self._sync_from_panel()
        if not widget_exists(self.window):
            self._build()
        if not self.panel._discovering and not any(self.panel.inventory.get(key) for key in ("systems", "microphones")):
            self.panel.refresh_devices()
        self.window.deiconify()
        self.window.lift()
        self.refresh()

    def _build(self):
        win = self.window = tk.Toplevel(self.app.root)
        win.withdraw()
        win.title("拾影 · 快速录屏" if self.mode == "record" else "拾影 · 快速截图")
        win.configure(bg=BG)
        brand = getattr(self.app, "brand_image", None)
        if brand is not None:
            try:
                win.iconphoto(True, brand)
            except tk.TclError:
                pass
        win.resizable(False, False)
        win.attributes("-topmost", True)
        win.protocol("WM_DELETE_WINDOW", self.close)
        outer = ttk.Frame(win, padding=(10, 7))
        outer.pack(fill="both", expand=True)
        row = ttk.Frame(outer)
        row.pack(fill="x")
        self.target_row = ttk.Frame(outer)
        ttk.Label(self.target_row, text="目标窗口").pack(side="left", padx=(0, 6))
        self.mode_label = ttk.Label(row, text="录屏")
        self.mode_label.pack(side="left", padx=(0, 9))
        self.range_box = ttk.Combobox(row, textvariable=self.range_var, values=(*self.RANGES, "高级配置"),
                                      state="readonly", width=8)
        self.range_box.pack(side="left")
        self.range_box.bind("<<ComboboxSelected>>", self._range_changed)
        self.window_box = ttk.Combobox(self.target_row, textvariable=self.panel.vars["window"], state="readonly", width=23)
        self.window_box.bind("<Button-1>", lambda _event: self.refresh_windows())
        self.window_refresh = ttk.Button(self.target_row, text="刷新", command=self.refresh_windows)
        self.region_button = ttk.Button(row, text="框选区域", command=self.select_region)
        self.system_check = ttk.Checkbutton(row, text="系统声", variable=self.system_var, command=self._audio_changed)
        self.mic_check = ttk.Checkbutton(row, text="麦克风", variable=self.mic_var, command=self._audio_changed)
        self.primary_button = ttk.Button(row, text="开始录制", style="Accent.TButton", command=self._primary)
        self.primary_button.pack(side="left", padx=(9, 5))
        self.pause_button = ttk.Button(row, text="暂停", command=self._pause_resume)
        self.stop_button = ttk.Button(row, text="停止并保存", command=self._stop)
        self.annotation_button = ttk.Button(row, text="批注", command=self.app.toggle_annotations)
        self.settings_button = ttk.Button(row, text="⚙ 设置", command=self.open_settings)
        self.settings_button.pack(side="left", padx=(0, 5))
        self.close_button = ttk.Button(row, text="关闭", command=self.close)
        self.status_label = ttk.Label(outer, textvariable=self.status, wraplength=500)
        self.status_label.pack(anchor="w", pady=(4, 0))
        self._layout()
        win.update_idletasks()
        width = win.winfo_reqwidth()
        screen_width, screen_height = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"+{max(0, (screen_width - width) // 2)}+{max(0, min(64, screen_height - win.winfo_reqheight()))}")
        try:
            from capture_native import exclude_from_capture
            exclude_from_capture(win)
        except (OSError, RuntimeError, ValueError):
            pass

    def _layout(self):
        if not widget_exists(self.window):
            return
        busy = is_busy(self.app.recorder)
        key = (self.mode, self.range_var.get(), busy, self._pending_options is not None)
        if key == self._layout_key:
            return
        self._layout_key = key
        self.window.title("拾影 · 快速录屏" if self.mode == "record" else "拾影 · 快速截图")
        self.mode_label.configure(text="录屏" if self.mode == "record" else "截图")
        for widget in (self.mode_label, self.range_box, self.window_box, self.window_refresh, self.region_button,
                       self.system_check, self.mic_check, self.pause_button, self.stop_button,
                       self.annotation_button, self.primary_button, self.settings_button, self.close_button):
            widget.pack_forget()
        self.target_row.pack_forget()
        self.mode_label.pack(side="left", padx=(0, 9))
        if not busy:
            self.range_box.pack(side="left")
        if self._pending_options is None and busy:
            self.pause_button.pack(side="left", padx=(6, 0))
            self.stop_button.pack(side="left", padx=(6, 0))
            self.annotation_button.pack(side="left", padx=(6, 0))
        elif self.range_var.get() == "窗口":
            self.target_row.pack(fill="x", pady=(5, 0), before=self.status_label)
            self.window_box.pack(side="left")
            self.window_refresh.pack(side="left", padx=(3, 0))
        elif self.range_var.get() == "区域":
            self.region_button.pack(side="left", padx=(6, 0))
        if self.mode == "record" and not busy:
            self.system_check.pack(side="left", padx=(9, 0))
            self.mic_check.pack(side="left", padx=(3, 0))
        if not busy:
            self.primary_button.pack(side="left", padx=(9, 5))
        self.settings_button.pack(side="left", padx=(0, 5))
        self.close_button.pack(side="left")

    def _sync_from_panel(self):
        inverse = {value: key for key, value in self.RANGES.items()}
        self.range_var.set(inverse.get(self.panel.vars["mode"].get(), "高级配置"))
        audio = self.panel.vars["audio"].get()
        self.system_var.set(audio in ("系统声音", "系统 + 麦克风"))
        self.mic_var.set(audio in ("麦克风", "系统 + 麦克风"))

    def _range_changed(self, _event=None):
        selection = self.range_var.get()
        if selection == "高级配置":
            self.open_settings()
            return
        self.panel.vars["mode"].set(self.RANGES[selection])
        if self.mode == "record":
            self.panel.record_mode = self.panel.vars["mode"].get()
        else:
            self.panel.screenshot_mode = self.panel.vars["mode"].get()
        if selection == "窗口":
            self.refresh_windows()
        self._layout()
        self.refresh()

    def _audio_changed(self):
        sound = (self.system_var.get(), self.mic_var.get())
        self.panel.vars["audio"].set({(False, False): "不录声音", (True, False): "系统声音",
                                      (False, True): "麦克风", (True, True): "系统 + 麦克风"}[sound])
        sources = []
        if sound[0]:
            sources.append("系统声：" + (self.panel.vars["system"].get().split("  [")[0] or "检测中"))
        if sound[1]:
            sources.append("麦克风：" + (self.panel.vars["microphone"].get().split("  [")[0] or "检测中"))
        self.status.set(" · ".join(sources) if sources else "静音录制")
        self.refresh()

    def refresh_windows(self):
        if is_busy(self.app.recorder) or self._pending_options is not None:
            return
        try:
            from capture_native import list_windows
            selected = self.panel.vars["window"].get()
            self.panel.windows = list_windows()
            choices = [window_label(w) for w in self.panel.windows]
            self.window_box.configure(values=choices)
            if selected not in choices:
                self.panel.vars["window"].set("")
            if not self.panel.windows:
                self.status.set("没有可选择的窗口，请打开目标窗口后刷新。")
        except (OSError, RuntimeError, ValueError):
            self.status.set("无法读取窗口列表，请稍后刷新。")

    def _check_window_alive(self):
        if self.panel.vars["mode"].get() != "窗口录制":
            return
        from capture_native import list_windows
        selected = self.panel.vars["window"].get()
        if not selected or selected not in {window_label(item) for item in list_windows()}:
            raise ValueError("所选窗口已关闭或不可见，请刷新并重新选择窗口。")

    def select_region(self):
        if (self._pending_options is not None or is_busy(self.app.recorder)
                or (self.panel.selector and self.panel.selector.active)):
            return
        if self.visible:
            self.window.withdraw()
        generation = self._generation

        def selected(region):
            if generation != self._generation:
                return
            self.selector = self.panel.selector = None
            if region:
                self.panel.region = dict(region)
                self.panel.region_status.set(f"{region['width']} × {region['height']} · ({region['x']}, {region['y']})")
                self.panel.vars["mode"].set("区域录制")
                self.range_var.set("区域")
                self.status.set(f"已选区域 {region['width']} × {region['height']}")
            else:
                self.status.set("已取消框选，未创建任务。")
            if widget_exists(self.window):
                self.window.deiconify()
                self.window.lift()
                self.refresh()

        try:
            self.selector = self.panel.selector = select_confirm_region(self.app.root, selected)
        except (OSError, RuntimeError, ValueError, tk.TclError) as error:
            selected(None)
            self._error(str(error))

    def _primary(self):
        if self._pending_options is not None:
            self.cancel_countdown()
        elif self.mode == "screenshot":
            self.screenshot()
        elif not is_busy(self.app.recorder):
            self.start()

    def start(self):
        if (self._pending_options is not None or is_busy(self.app.recorder)
                or (self.panel.selector and self.panel.selector.active)):
            return
        try:
            self._check_window_alive()
            self._pending_options = self.panel.get_options()
        except (ValueError, RuntimeError, OSError) as error:
            self._error(str(error))
            return
        self._count_down(3 if self.panel.vars["countdown"].get() == "3 秒" else 0)

    def _count_down(self, remaining):
        self._countdown = None
        if self._pending_options is None:
            return
        if remaining:
            self.status.set(f"{remaining} 秒后开始 · 可取消")
            self._countdown = self.app.root.after(1000, lambda: self._count_down(remaining - 1))
            self.refresh()
            return
        options, self._pending_options = self._pending_options, None
        if is_busy(self.app.recorder):
            self.refresh()
            return
        minimized = False
        try:
            if self.visible:
                self.window.withdraw()
            if widget_exists(self.panel.window):
                self.panel.window.withdraw()
            if self.panel.vars["minimize"].get() and self.app.root.state() == "normal" and self.app.root.winfo_ismapped():
                self.app.root.iconify()
                minimized = True
            self.app.start_recording(options)
            self._recording_restore = True
            self._restore_main = minimized
        except (ValueError, RuntimeError, OSError) as error:
            if minimized:
                self.app.root.deiconify()
            if widget_exists(self.window):
                self.window.deiconify()
            self._error(str(error))
        self.refresh()

    def cancel_countdown(self):
        cancel_timer(self.app.root, self._countdown)
        self._countdown = self._pending_options = None
        self.status.set("已取消倒计时，未开始录制。")
        self.refresh()

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
        try:
            self.app.stop_recording()
        except (ValueError, RuntimeError, OSError) as error:
            self._error(str(error))
        self.refresh()

    def screenshot(self):
        if self._screenshot_timer is not None or self._screenshot_inflight or getattr(self.app, "screenshot_pending", False):
            return
        if self._pending_options is not None or (self.panel.selector and self.panel.selector.active):
            return
        if self.panel.vars["mode"].get() == "区域录制" and not self.panel.region:
            self.select_region()
            return
        try:
            self._check_window_alive()
            options = self.panel.get_screenshot_options()
        except (ValueError, RuntimeError, OSError) as error:
            self._error(str(error))
            return
        if self.visible:
            self.window.withdraw()
        if widget_exists(self.panel.window):
            self.panel.window.withdraw()
        generation = self._generation
        self._screenshot_timer = self.app.root.after(180, lambda: self._finish_screenshot(options)
                                                      if generation == self._generation else None)

    def _finish_screenshot(self, options):
        self._screenshot_timer = None
        try:
            result = self.app.capture_screenshot(options)
            self._screenshot_id = result.get("id") if isinstance(result, dict) else None
            self._screenshot_inflight = True
            self.refresh()
        except (ValueError, RuntimeError, OSError) as error:
            self._error(str(error))
            if widget_exists(self.window):
                self.window.deiconify()

    def open_settings(self):
        if (is_busy(self.app.recorder) or self._screenshot_timer is not None or self._screenshot_inflight
                or getattr(self.app, "screenshot_pending", False)):
            self._error("正在采集或保存，请完成后再修改采集设置。")
            return
        if self.panel.selector is not None and getattr(self.panel.selector, "active", False):
            self._error("请先确认或取消当前选区。")
            return
        if self._pending_options is not None:
            self.cancel_countdown()
        if self.visible:
            self.window.withdraw()
        self._settings_open = True
        self.panel.show()
        if widget_exists(self.panel.window):
            self.panel.window.title("拾影 · 采集设置")
            self.panel.window.protocol("WM_DELETE_WINDOW", self._settings_closed)

    def _settings_closed(self):
        self.panel.close()
        self._settings_open = False
        self.show()

    def _error(self, message):
        self.status.set(message)
        self.app.notice.set(message)

    def refresh(self):
        self.panel._read_devices()
        if self._recording_restore and not is_busy(self.app.recorder):
            snapshot = self.app.recorder.snapshot() or {}
            if snapshot.get("status") in {"已保存", "失败", "已取消"}:
                self._recording_restore = False
                if self._restore_main and not getattr(self.app, "exit_after_capture", False):
                    self.app.root.deiconify()
                self._restore_main = False
                if snapshot.get("status") != "已保存" and widget_exists(self.window):
                    self.window.deiconify()
        if self._screenshot_inflight and not getattr(self.app, "screenshot_pending", False):
            item = getattr(getattr(self.app, "store", None), "items", {}).get(self._screenshot_id, {})
            if item.get("status") != "已保存" and widget_exists(self.window):
                self.window.deiconify()
                if item.get("error"):
                    self._error(str(item["error"]))
            self._screenshot_inflight = False
            self._screenshot_id = None
        if not widget_exists(self.window):
            return
        busy = is_busy(self.app.recorder)
        snapshot = self.app.recorder.snapshot() or {}
        status = snapshot.get("status", "准备就绪")
        if busy:
            self.status.set(status + " · " + elapsed_text(snapshot.get("elapsed")))
        elif self._pending_options is None and not self.status.get():
            self.status.set("准备就绪")
        self.primary_button.configure(text=("取消倒计时" if self._pending_options is not None else
                                            "保存图片" if self.mode == "screenshot" else "开始录制"),
                                      state="disabled" if busy or self._screenshot_inflight else "normal")
        self.range_box.configure(state="disabled" if busy or self._pending_options else "readonly")
        self.system_check.configure(state="disabled" if busy or self._pending_options else "normal")
        self.mic_check.configure(state="disabled" if busy or self._pending_options else "normal")
        self.pause_button.configure(text="继续" if status == "录制暂停" else "暂停",
                                    state="normal" if status in ("录制中", "录制暂停") else "disabled")
        self.stop_button.configure(state="disabled" if status == "正在保存" else "normal")
        self._layout()

    def close(self):
        self._generation += 1
        self.panel.save_preferences()
        self.cancel_countdown()
        cancel_timer(self.app.root, self._screenshot_timer)
        self._screenshot_timer = None
        self._screenshot_inflight = False
        if self.selector:
            selector, self.selector = self.selector, None
            self.panel.selector = None
            try:
                selector.close()
            except tk.TclError:
                pass
        win, self.window = self.window, None
        if widget_exists(win):
            win.destroy()
        if self._settings_open:
            self._settings_open = False
            self.panel.close()
