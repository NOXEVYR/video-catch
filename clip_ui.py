"""Frame-aware local trimming; all image/widget work stays on Tk's thread."""
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk
from dialogs import messagebox
from surface_ui import setup_window, header, ScrollableBody

from clip_preview import Decoder
from ui import BG, PANEL, FG, ACCENT, wrap_controls


class ClipEditor:
    def __init__(self, app, source):
        self.app, self.source = app, str(source)
        from clipper import source_stamp
        self.source_stamp = source_stamp(source)
        self.exported = False
        self.decoder = Decoder(source)
        self.results, self.requests = queue.Queue(), queue.Queue(maxsize=1)
        self.closed = False
        self.timeline = None
        self.cursor = self.first = self.last = 0
        self.revision = 0
        self.playing = False
        self.play_timer = None
        self.window = tk.Toplevel(app.root)
        setup_window(self.window, app, "剪辑片段", 820, 780, minwidth=640, minheight=620)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        heading = header(self.window, "剪辑片段", Path(source).name, app=app)
        # Export remains pinned; the time controls scroll before preview vanishes.
        footer = ttk.Frame(self.window, padding=(16, 8, 16, 16))
        footer.pack(side='bottom', fill='x')
        self.controls_body = ScrollableBody(self.window)
        self.controls_body.pack(side='bottom', fill='x', expand=False, padx=16, pady=(0, 8))
        bottom = ttk.Frame(self.controls_body.frame, padding=(0, 8))
        bottom.pack(fill='x')
        self.status = tk.StringVar(value="正在读取实际帧时间，请稍候；长视频首次读取需要更多时间。")
        status_label = ttk.Label(bottom, textvariable=self.status, wraplength=700)
        status_label.pack(fill="x", pady=(0, 6))
        status_label.bind('<Configure>', lambda event: status_label.configure(wraplength=max(1, event.width)), add='+')
        self.scale = ttk.Scale(bottom, from_=0, to=1, command=self.seek)
        self.scale.pack(fill="x", pady=6)
        self.range_canvas = tk.Canvas(bottom, height=42, bg=PANEL, highlightthickness=0)
        self.range_canvas.pack(fill='x', pady=(0, 8))
        self.range_canvas.bind('<Configure>', lambda _: self.draw_range())
        self.range_canvas.bind('<Button-1>', self.range_press)
        self.range_canvas.bind('<B1-Motion>', self.range_drag)
        self.target = tk.StringVar(value="cursor")
        targets = ttk.Frame(bottom); targets.pack(fill="x")
        ttk.Label(targets, text="方向键微调：").pack(side="left")
        for label, value in (("预览位置", "cursor"), ("开始帧", "first"), ("结束帧", "last")):
            ttk.Radiobutton(targets, text=label, variable=self.target, value=value, command=self.select_target).pack(side="left", padx=5)
        wrap_controls(targets)
        marks = ttk.Frame(bottom); marks.pack(fill="x", pady=8)
        self.start_text, self.end_text = tk.StringVar(), tk.StringVar()
        for column, (label, var) in enumerate((("开始（秒）", self.start_text), ("结束（秒）", self.end_text))):
            ttk.Label(marks, text=label).grid(row=column, column=0, padx=4, sticky="w")
            entry = ttk.Entry(marks, textvariable=var, width=12)
            entry.grid(row=column, column=1, padx=4, pady=3, sticky="ew")
            entry.bind("<Return>", lambda event: self.apply_times())
        ttk.Button(marks, text="应用时间", command=self.apply_times).grid(row=0, column=2, rowspan=2, padx=6)
        marks.columnconfigure(1, weight=1)
        controls = ttk.Frame(bottom); controls.pack(fill="x")
        for index, (text, command) in enumerate((("◀ 前一帧", lambda: self.step(-1)), ("播放 / 暂停", self.play),
                              ("后一帧 ▶", lambda: self.step(1)), ("设为开始 I", self.mark_start), ("设为结束 O", self.mark_end))):
            ttk.Button(controls, text=text, command=command).grid(row=index // 3, column=index % 3, sticky="ew", padx=2, pady=2)
        for column in range(3):
            controls.columnconfigure(column, weight=1)
        ttk.Label(bottom, text="← / → 逐帧微调 · I / O 标记起止 · 结束时刻不包含在片段内\n画面预览无声，导出保留原音轨", style="Muted.TLabel", wraplength=570).pack(anchor="w", pady=8)
        ttk.Label(footer, text="另存 MP4，原视频保留", foreground=ACCENT).pack(side="left")
        self.export_button = ttk.Button(footer, text="导出所选片段", command=self.export, state="disabled", style='Accent.TButton')
        self.export_button.pack(side="right")
        ttk.Button(footer, text="保存草稿并关闭", command=lambda: self.close(prompt=False)).pack(side="right", padx=8)
        wrap_controls(footer)
        self.canvas = tk.Canvas(self.window, bg=PANEL, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=16, pady=6)
        self.canvas.bind("<Configure>", lambda _: self.draw())
        self.canvas.bind("<Button-1>", lambda _: self.canvas.focus_set())
        self.window.bind("<Left>", lambda event: self.key_step(event, -1))
        self.window.bind("<Right>", lambda event: self.key_step(event, 1))
        for key, method in (("i", self.mark_start), ("o", self.mark_end), ("space", self.play)):
            self.window.bind("<" + key + ">", lambda event, action=method: self.shortcut(event, action))
        self.window.bind("<Escape>", lambda _: self.close())
        def fit_controls(event=None):
            if self.closed or (event is not None and event.widget is not self.window):
                return
            scale = max(1, self.window.winfo_fpixels('1i') / 96)
            available = self.window.winfo_height() - heading.winfo_reqheight() - footer.winfo_reqheight() - round(120 * scale) - 20
            self.controls_body.canvas.configure(height=min(bottom.winfo_reqheight(), max(80, available)))
        self.window.bind('<Configure>', fit_controls, add='+')
        for frame in (heading, footer, bottom):
            frame.bind('<Configure>', lambda _event: fit_controls(), add='+')
        self.photo = None
        threading.Thread(target=self.worker, args=(self.decoder, self.results, self.requests), daemon=True).start()
        self.timer = self.window.after(40, self.poll)

    @staticmethod
    def worker(decoder, results, requests):
        try:
            timeline = decoder.timeline(lambda n: results.put(("progress", n)))
            results.put(("timeline", timeline))
            while not decoder.closed.is_set():
                try:
                    revision, seconds = requests.get(timeout=.2)
                except queue.Empty:
                    continue
                try:
                    results.put(("frame", revision, decoder.frame(seconds)))
                except Exception:
                    results.put(("preview_error", revision))
        except Exception as error:
            results.put(("error", str(error)))

    def poll(self):
        if self.closed:
            return
        try:
            while True:
                result = self.results.get_nowait()
                if result[0] == "timeline":
                    if not self.source_unchanged():
                        self.status.set('源视频已变化，请关闭并重新打开后剪辑。')
                        continue
                    self.timeline = result[1]
                    self.last = len(self.timeline.times) - 1
                    self.scale.configure(to=self.last)
                    self.export_button.configure(state="normal")
                    draft = self.app.clip_drafts.get(self.source)
                    if draft and draft[0] == self.source_stamp:
                        self.first = max(0, min(draft[1], self.last))
                        self.last = max(self.first, min(draft[2], self.last))
                        self.show(draft[3])
                    else:
                        self.show(0)
                elif result[0] == "progress":
                    self.status.set(f"正在建立帧索引：已读取 {result[1]:,} 帧…")
                elif result[0] == "frame" and result[1] == self.revision:
                    self.photo = tk.PhotoImage(master=self.window, data=result[2])
                    self.draw()
                    if self.playing:
                        if self.cursor >= self.last:
                            self.playing = False
                        else:
                            delay = max(1, int(1000 * (self.timeline.times[self.cursor + 1] - self.timeline.times[self.cursor])))
                            if self.play_timer is not None:
                                self.window.after_cancel(self.play_timer)
                            self.play_timer = self.window.after(delay, self.advance)
                elif result[0] == "preview_error" and result[1] == self.revision:
                    self.playing = False
                    self.status.set("当前帧无法预览，请换一个时间位置后重试。")
                elif result[0] == "error":
                    self.status.set(result[1])
        except queue.Empty:
            pass
        self.timer = self.window.after(40, self.poll)

    def draw(self):
        self.canvas.delete("all")
        if self.photo:
            factor = max(1, (self.photo.width() + max(1, self.canvas.winfo_width()) - 1) // max(1, self.canvas.winfo_width()),
                         (self.photo.height() + max(1, self.canvas.winfo_height()) - 1) // max(1, self.canvas.winfo_height()))
            self.display_photo = self.photo.subsample(factor)
            self.canvas.create_image(self.canvas.winfo_width()/2, self.canvas.winfo_height()/2, image=self.display_photo)

    def show(self, index):
        if not self.timeline or self.closed:
            return
        self.cursor = max(0, min(len(self.timeline.times) - 1, index))
        self.revision += 1
        self.scale.set(self.cursor)
        start, end = self.timeline.range(self.first, self.last)
        self.start_text.set(f"{start:.6f}"); self.end_text.set(f"{end:.6f}")
        self.status.set(f"第 {self.cursor+1:,} / {len(self.timeline.times):,} 帧 · {self.timeline.times[self.cursor]:.6f} 秒   |   所选 {self.last-self.first+1:,} 帧 · {end-start:.3f} 秒")
        try: self.requests.get_nowait()
        except queue.Empty: pass
        self.requests.put_nowait((self.revision, self.timeline.times[self.cursor]))
        self.draw_range()

    def draw_range(self):
        if not self.timeline:
            return
        canvas = self.range_canvas
        width = max(1, canvas.winfo_width() - 24)
        frames = max(1, len(self.timeline.times) - 1)
        x1, x2 = 12 + width * self.first / frames, 12 + width * self.last / frames
        canvas.delete('all')
        canvas.create_rectangle(12, 12, width + 12, 30, fill='#302b40', outline='')
        canvas.create_rectangle(x1, 12, max(x1 + 2, x2), 30, fill='#776087', outline='')
        for x in (x1, x2):
            canvas.create_rectangle(x-4, 6, x+4, 36, fill=ACCENT, outline='')
        x = 12 + width * self.cursor / frames
        canvas.create_line(x, 8, x, 34, fill=FG, width=2)

    def range_press(self, event):
        if not self.timeline:
            return
        width = max(1, self.range_canvas.winfo_width()-24)
        frames = max(1, len(self.timeline.times)-1)
        value = max(0, min(frames, round((event.x-12)/width*frames)))
        self.target.set('first' if abs(value-self.first) <= abs(value-self.last) else 'last')
        self.range_canvas.focus_set()
        self.range_drag(event)

    def range_drag(self, event):
        if not self.timeline:
            return
        width = max(1, self.range_canvas.winfo_width()-24)
        value = max(0, min(len(self.timeline.times)-1, round((event.x-12)/width*(len(self.timeline.times)-1))))
        target = self.target.get()
        if target == 'first':
            self.first = min(value, self.last)
        elif target == 'last':
            self.last = max(value, self.first)
        self.playing = False
        self.show(getattr(self, target))

    def seek(self, value):
        index = round(float(value))
        if index != self.cursor:
            self.playing = False
            self.show(index)

    def select_target(self):
        self.playing = False
        self.show(getattr(self, self.target.get()))
        self.canvas.focus_set()

    def step(self, delta):
        if not self.timeline:
            return
        self.playing = False
        target = self.target.get()
        index = max(0, min(len(self.timeline.times)-1, getattr(self, target) + delta))
        if target == "first": index = min(index, self.last)
        if target == "last": index = max(index, self.first)
        setattr(self, target, index)
        self.show(index)

    def key_step(self, event, delta):
        return self.shortcut(event, lambda: self.step(delta))

    def shortcut(self, event, action):
        if isinstance(event.widget, (ttk.Entry, tk.Entry)):
            return
        action()
        return "break"

    def mark_start(self):
        if self.timeline:
            self.first = min(self.cursor, self.last)
            self.show(self.first)

    def mark_end(self):
        if self.timeline:
            self.last = max(self.cursor, self.first)
            self.show(self.last)

    def apply_times(self):
        if not self.timeline:
            return
        try:
            import math
            from bisect import bisect_left
            start, end = float(self.start_text.get()), float(self.end_text.get())
            if not all(map(math.isfinite, (start, end))) or not self.timeline.times[0] <= start < end <= self.timeline.end + .000001:
                raise ValueError()
            first = self.timeline.nearest(start)
            last = max(0, bisect_left(self.timeline.times, end - .000001) - 1)
            if first > last: raise ValueError()
            self.first, self.last = first, last
            self.playing = False
            self.show(first)
            return True
        except ValueError:
            messagebox.showerror("时间范围无效", "请输入视频范围内的起止秒数，结束须晚于开始。", parent=self.window)
            return False

    def play(self):
        if not self.timeline: return
        if self.play_timer is not None:
            self.window.after_cancel(self.play_timer)
            self.play_timer = None
        self.playing = not self.playing
        if self.playing:
            self.show(self.first if self.cursor >= self.last or self.cursor < self.first else self.cursor)

    def advance(self):
        self.play_timer = None
        if not self.closed and self.playing:
            self.show(min(self.cursor + 1, self.last))

    def source_unchanged(self):
        from clipper import source_stamp
        try:
            return source_stamp(self.source) == self.source_stamp
        except OSError:
            return False

    def export(self):
        if not self.timeline: return
        if not self.source_unchanged():
            messagebox.showerror('源视频已变化', '请关闭并重新打开素材，确认新的画面和时间范围后再导出。', parent=self.window)
            return
        start, end = self.timeline.range(self.first, self.last)
        if (self.start_text.get(), self.end_text.get()) != (f"{start:.6f}", f"{end:.6f}") and not self.apply_times():
            return
        try:
            start, end = self.timeline.range(self.first, self.last)
            self.app.ai.enqueue_clip({"source": self.source, "start": start, "end": end})
            self.app.persist_receipts()
            self.app.notice.set("剪辑已加入队列，完成后自动展示新素材；原视频保留。")
            self.exported = True
            self.close()
        except Exception as error:
            messagebox.showerror("无法导出", str(error), parent=self.window)

    def close_decision(self):
        changed = self.timeline and (self.first != 0 or self.last != len(self.timeline.times)-1)
        if changed and not self.exported:
            answer = messagebox.askyesnocancel('保留剪辑选区',
                '是否保留当前选区，稍后继续剪辑？\n是：保存草稿；否：放弃选区；取消：继续编辑。', parent=self.window)
            return None if answer is None else 'keep' if answer else 'discard'
        return 'keep'

    def draft_value(self, choice):
        return ((self.source_stamp, self.first, self.last, self.cursor)
                if self.timeline and not self.exported and choice == 'keep' else None)

    def close(self, prompt=True, decision=None, prepared=False):
        if self.closed:
            return True
        choice = decision if decision is not None else self.close_decision() if prompt else 'keep'
        if choice is None:
            return False
        value = self.draft_value(choice)
        if prepared or self.timeline is None:
            pass  # Cancelling index/decoder loading must retain the previous valid draft.
        elif value is None and self.app.clip_drafts.get(self.source) is None:
            pass
        elif not self.app.clip_drafts.save(self.source, value):
            if not self.exported:
                messagebox.showerror('草稿未保存', '无法更新本机剪辑草稿，窗口已保留。请检查目录权限后重试。', parent=self.window)
                return False
            self.app.notice.set('窗口已关闭，但本机旧草稿未能清理。')
        elif value and not getattr(self.app, 'closing', False):
            self.app.notice.set('剪辑草稿已保存，重新打开该素材可继续。' if self.app.clip_drafts.enabled
                                else '剪辑选择已暂存到当前验证会话。')
        self.closed = True
        self.playing = False
        self.decoder.close()
        self.window.after_cancel(self.timer)
        if self.play_timer is not None:
            self.window.after_cancel(self.play_timer)
            self.play_timer = None
        self.controls_body.close()
        self.window.destroy()
        if self in self.app.clip_windows:
            self.app.clip_windows.remove(self)
        return True
