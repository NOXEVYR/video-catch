"""Tk-only startup view and an event-loop-friendly initialization scheduler.

Construct Splash after withdrawing the main root. Split initialization into short
StartupStages; background stages MUST NOT touch Tk. A stage may assign its result
to a closure-owned context, or callers can inspect sequence.results. Retry resumes
the failed stage, so each action must clean up its own partially acquired resources
before raising. No artificial dwell time or estimated progress is used.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import queue
import threading
import time
import tkinter as tk
from typing import Callable

BG = "#211D2C"
PANEL = "#2E283C"
TEXT = "#F5EEE9"
MUTED = "#B9AEC5"
APRICOT = "#F3B78F"
PURPLE = "#9E87BB"

# Matches scripts/build_startup_motion.cjs. Frames follow wall time, so a busy
# initialization stage never causes a backlog or delays entry to the workbench.
ICON_SIZE = 96
ICON_COLUMNS = 10
ICON_INTRO = 24
ICON_LOOP = 64
ICON_FPS = 25


def icon_frame_at(elapsed):
    frame = max(0, int(elapsed * ICON_FPS))
    if frame < ICON_INTRO:
        return frame
    return ICON_INTRO + (frame - ICON_INTRO) % ICON_LOOP


def _mix(first, second, amount):
    a = tuple(int(first[i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(second[i:i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(a, b))


class Splash:
    """One startup window per root; finish is immediate and idempotent."""

    def __new__(cls, root, *args, **kwargs):
        existing = getattr(root, "_videocatch_startup_splash", None)
        if isinstance(existing, cls):
            return existing
        instance = super().__new__(cls)
        root._videocatch_startup_splash = instance
        return instance

    def __init__(self, root, on_retry=None):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self.root = root
        self.on_retry = on_retry
        self.finished = False
        self.failed = False
        self._timer = None
        self._frame = 0
        self._started_at = time.monotonic()
        self._icon_frame = None
        self._icon_updates = 0
        self.icon_sheet = None
        root.withdraw()
        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.window.title("拾影 · 正在打开")
        self.window.configure(bg=BG)
        self.window.resizable(False, False)
        self.window.overrideredirect(True)
        self.window.bind("<Destroy>", self._on_destroy, add="+")
        width, height = 430, 302
        x = max(0, (root.winfo_screenwidth() - width) // 2)
        y = max(0, (root.winfo_screenheight() - height) // 2)
        self.window.geometry(f"{width}x{height}+{x}+{y}")
        self.canvas = tk.Canvas(self.window, width=width, height=height,
                                bg=BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.halo = self.canvas.create_line(24, 1, width - 24, 1, fill=PURPLE)
        self.image = None
        try:
            from runtime_paths import distribution_root
            assets = distribution_root() / "assets"
            try:
                sheet = tk.PhotoImage(file=str(assets / "startup-motion.png"), master=root)
                rows = math.ceil((ICON_INTRO + ICON_LOOP) / ICON_COLUMNS)
                if sheet.width() != ICON_COLUMNS * ICON_SIZE or sheet.height() != rows * ICON_SIZE:
                    raise ValueError("Invalid startup atlas dimensions")
                self.icon_sheet = sheet
                self.image = tk.PhotoImage(width=ICON_SIZE, height=ICON_SIZE, master=root)
                self._draw_icon(0)
            except (tk.TclError, OSError, ValueError):
                self.icon_sheet = None
                self.image = tk.PhotoImage(file=str(assets / "brand64.png"), master=root)
        except (tk.TclError, OSError):
            pass
        self.icon_animation_available = self.icon_sheet is not None
        if self.image is not None:
            self.canvas.create_image(215, 71, image=self.image)
        else:
            self.canvas.create_oval(192, 48, 238, 94, fill=APRICOT, outline="")
            self.canvas.create_line(185, 79, 245, 79, fill=BG, width=4)
        self.canvas.create_text(215, 148, text="拾影", fill=TEXT, font=("Microsoft YaHei UI", 25, "bold"))
        self.canvas.create_text(215, 185, text="把发现，留成作品", fill=MUTED, font=("Microsoft YaHei UI", 10))
        self.stage_item = self.canvas.create_text(215, 230, text="正在打开工作台", fill=MUTED,
                                                 font=("Microsoft YaHei UI", 10), width=382)
        self.dots = [self.canvas.create_oval(197 + i * 12, 264, 203 + i * 12, 270,
                                             fill=PURPLE, outline="") for i in range(3)]
        self.actions = tk.Frame(self.window, bg=BG)
        self.retry_button = tk.Button(self.actions, text="重试", command=self._retry,
                                     bg=APRICOT, fg=BG, activebackground=APRICOT,
                                     relief="flat", bd=0, padx=18, pady=4, cursor="hand2")
        self.retry_button.pack(side="left", padx=5)
        tk.Button(self.actions, text="退出", command=root.destroy, bg=PANEL, fg=TEXT,
                  activebackground=PANEL, activeforeground=TEXT, relief="flat", bd=0,
                  padx=18, pady=4, cursor="hand2").pack(side="left", padx=5)
        self.window.deiconify()
        self.window.lift()
        self._animate()

    def _on_destroy(self, event):
        if event.widget is self.window:
            self.finished = True
            self._stop_animation()
            self.icon_sheet = None
            self.image = None

    def _stop_animation(self):
        if self._timer is not None:
            try:
                self.root.after_cancel(self._timer)
            except tk.TclError:
                pass
            self._timer = None

    def _animate(self):
        self._timer = None
        if self.finished or self.failed:
            return
        elapsed = time.monotonic() - self._started_at
        self._frame = elapsed * ICON_FPS
        self._draw_icon(icon_frame_at(elapsed))
        pulse = (1 + math.sin(self._frame / 13)) / 2
        self.canvas.itemconfigure(self.halo, fill=_mix(PANEL, PURPLE, 0.45 + pulse * 0.25))
        for i, dot in enumerate(self.dots):
            glow = (1 + math.sin(self._frame / 8 - i * 0.9)) / 2
            self.canvas.itemconfigure(dot, fill=_mix(PANEL, APRICOT, 0.25 + glow * 0.7))
        self._timer = self.root.after(40, self._animate)

    def _draw_icon(self, frame):
        if self.icon_sheet is None or self._icon_frame == frame:
            return
        x, y = (frame % ICON_COLUMNS) * ICON_SIZE, (frame // ICON_COLUMNS) * ICON_SIZE
        self.image.blank()
        self.image.tk.call(self.image, "copy", self.icon_sheet, "-from",
                           x, y, x + ICON_SIZE, y + ICON_SIZE, "-to", 0, 0)
        self._icon_frame = frame
        self._icon_updates += 1

    def set_stage(self, text):
        if self.finished:
            return
        was_failed = self.failed
        self.failed = False
        self.actions.place_forget()
        self.canvas.itemconfigure(self.stage_item, text=str(text), fill=MUTED)
        if was_failed:
            self._started_at = time.monotonic()
            for dot in self.dots:
                self.canvas.itemconfigure(dot, state="normal")
            self._animate()

    def fail(self, message="加载未完成，请重试。", on_retry=None):
        if self.finished:
            return
        self.failed = True
        self._stop_animation()
        self._draw_icon(ICON_INTRO)  # Rest in the complete, original icon pose.
        if on_retry is not None:
            self.on_retry = on_retry
        self.canvas.itemconfigure(self.stage_item, text=str(message), fill=APRICOT)
        for dot in self.dots:
            self.canvas.itemconfigure(dot, state="hidden")
        self.retry_button.configure(state="normal" if self.on_retry else "disabled")
        self.actions.place(relx=0.5, y=259, anchor="n")

    def _retry(self):
        if self.finished or not self.failed or self.on_retry is None:
            return
        self.set_stage("正在重试")
        try:
            self.on_retry()
        except Exception:
            self.fail("重试未完成，请再次重试或退出。")

    def finish(self, show_main=True):
        if self.finished:
            return
        self.finished = True
        self._stop_animation()
        self.window.destroy()
        self.icon_sheet = None
        self.image = None
        if show_main:
            self.root.deiconify()
            self.root.lift()


@dataclass(frozen=True)
class StartupStage:
    label: str
    action: Callable
    background: bool = False


class StartupSequence:
    """Short Tk stages and optional pure-I/O worker stages, with checkpoint retry.

    start() must run on the Tk thread. on_complete also runs there, before finish.
    It can construct/show the main UI; any exception leaves the splash retryable.
    on_error receives the exception on the Tk thread for partial-stage cleanup.
    cancel() invalidates pending worker results but cannot forcibly stop I/O.
    """

    def __init__(self, root, splash, stages, on_complete=None, on_error=None):
        self.root, self.splash = root, splash
        self.stages = tuple(stages)
        self.on_complete = on_complete
        self.on_error = on_error
        self.results = []
        self.index = 0
        self.running = False
        self.cancelled = False
        self._timer = None
        self._generation = 0
        self.splash.on_retry = self.retry
        root.bind("<Destroy>", self._on_destroy, add="+")

    def _on_destroy(self, event):
        if event.widget is self.root:
            self.cancel()

    def _schedule(self, delay, callback):
        # Idle scheduling lets pending Tk redraws paint the stage before work.
        self._timer = self.root.after_idle(callback) if delay == 0 else self.root.after(delay, callback)

    def start(self):
        if self.running or self.cancelled or self.splash.finished:
            return
        self.running = True
        self._generation += 1
        self._schedule(0, self._next)

    def retry(self):
        self.start()

    def cancel(self):
        self.cancelled = True
        self.running = False
        self._generation += 1
        if self._timer is not None:
            try:
                self.root.after_cancel(self._timer)
            except tk.TclError:
                pass
            self._timer = None

    def _failed(self, error):
        self.running = False
        if self.on_error:
            try:
                self.on_error(error)
            except Exception:
                self.splash.on_retry = None
                self.splash.fail("启动清理未完成，请退出后重新打开。")
                return
        label = self.stages[self.index].label if self.index < len(self.stages) else "打开工作台"
        self.splash.fail(f"{label}未完成，请重试。", on_retry=self.retry)

    def _next(self):
        self._timer = None
        if self.cancelled or not self.running:
            return
        if self.index == len(self.stages):
            try:
                if self.on_complete:
                    self.on_complete()
            except Exception as error:
                self._failed(error)
                return
            self.running = False
            self.splash.finish()
            return
        stage = self.stages[self.index]
        self.splash.set_stage(stage.label)
        # Yield once after setting the stage so Tk can paint before its work starts.
        self._schedule(0, lambda: self._run(stage))

    def _run(self, stage):
        self._timer = None
        if self.cancelled or not self.running:
            return
        if not stage.background:
            try:
                value = stage.action()
            except Exception as error:
                self._failed(error)
                return
            self._completed(value)
            return
        result = queue.Queue(maxsize=1)
        generation = self._generation

        def worker():
            try:
                result.put((True, stage.action()))
            except Exception as error:
                result.put((False, error))

        def poll():
            self._timer = None
            if self.cancelled or generation != self._generation:
                return
            try:
                ok, value = result.get_nowait()
            except queue.Empty:
                self._schedule(25, poll)
                return
            if ok:
                self._completed(value)
            else:
                self._failed(value)

        self._worker = threading.Thread(target=worker, name="VideoCatch-startup", daemon=True)
        self._worker.start()
        poll()

    def _completed(self, value):
        if self.cancelled:
            return
        self.results.append(value)
        self.index += 1
        self._schedule(0, self._next)
