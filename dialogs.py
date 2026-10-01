"""Themed, locally modal dialogs with tkinter-compatible public facades."""
import tkinter as tk
from tkinter import messagebox as _native_messagebox
from tkinter import simpledialog as _native_simpledialog
from tkinter import ttk


def _alive(widget):
    try:
        return widget is not None and bool(widget.winfo_exists())
    except tk.TclError:
        return False


class _Dialog:
    def __init__(self, parent, title, message, kind, **options):
        self.owner = parent.winfo_toplevel()
        self.kind = kind
        self.result = False if kind == "yesno" else ("ok" if kind in ("error", "info") else None)
        self.window = None
        self.previous_grab = None
        self.previous_focus = None
        self.acquired_grab = False
        self.owner_binding = None
        self.buttons = {}
        try:
            self._build(title, message, options)
        except Exception:
            self._cleanup()
            raise

    def _build(self, title, message, options):
        from surface_ui import setup_window, header, card
        from ui import PANEL, FG

        window = self.window = tk.Toplevel(self.owner)
        window.withdraw()
        setup_window(window, None, title or "拾影 VideoCatch", 560, 400,
                     minwidth=320, minheight=240)
        window.withdraw()
        if self.owner.winfo_viewable():
            window.transient(self.owner)
        else:
            window.transient("")
        window.protocol("WM_DELETE_WINDOW", self.cancel)
        window.bind("<Escape>", self.cancel)
        window.bind("<Return>", self._enter)
        window.bind("<Destroy>", self._on_destroy, add="+")
        self.owner_binding = self.owner.bind("<Destroy>", self._owner_destroyed, add="+")

        heading = header(window, title or "拾影 VideoCatch", {
            "yesno": "请确认本次操作", "yesnocancel": "请选择如何继续",
            "string": "修改素材显示名称", "error": "操作未能完成", "info": "拾影提示",
        }[self.kind])
        body = card(window, padding=16)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        text = str(message or "")
        if options.get("detail"):
            text += "\n\n" + str(options["detail"])
        self.message = tk.Text(body, wrap="word", height=5, width=20,
                               bg=PANEL, fg=FG, insertbackground=FG,
                               selectbackground="#51405d", selectforeground=FG,
                               relief="flat", borderwidth=0, highlightthickness=0,
                               font=("Microsoft YaHei UI", 10), padx=2, pady=2,
                               spacing1=3, spacing3=5, takefocus=True)
        self.message.insert("1.0", text)
        self.message.configure(state="disabled")
        self.message.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=self.message.yview)
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        self.message.configure(yscrollcommand=scrollbar.set)
        self.entry = None
        if self.kind == "string":
            self.entry = ttk.Entry(body, show=options.get("show", ""))
            self.entry.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(14, 0))
            value = options.get("initialvalue")
            if value is not None:
                self.entry.insert(0, str(value))
            self.entry.selection_range(0, "end")

        footer = ttk.Frame(window, style="Surface.TFrame", padding=(16, 0, 16, 16))
        footer.pack(side='bottom', fill="x")
        body.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        if self.kind == "yesno":
            specs = [("no", "取消", False, "Quiet.TButton"),
                     ("yes", "确认", True, "Accent.TButton")]
        elif self.kind == "yesnocancel":
            specs = [("cancel", "取消", None, "Quiet.TButton"),
                     ("no", "否", False, "Quiet.TButton"),
                     ("yes", "是", True, "Accent.TButton")]
        elif self.kind == "string":
            specs = [("cancel", "取消", None, "Quiet.TButton"),
                     ("ok", "保存名称", None, "Accent.TButton")]
        else:
            specs = [("ok", "知道了", "ok", "Accent.TButton")]
        for key, label, value, style in specs:
            callback = self._save_string if self.kind == "string" and key == "ok" else (
                lambda answer=value: self.finish(answer))
            self.buttons[key] = ttk.Button(footer, text=label, style=style, command=callback)

        footer_height = 0
        def fit_minimum(_event=None):
            scale = max(1, window.winfo_fpixels('1i') / 96)
            required = heading.winfo_reqheight() + footer_height + round(96 * scale) + 12
            if self.entry is not None:
                required += self.entry.winfo_reqheight() + 14
            min_width, _ = window.minsize()
            window.minsize(min_width, min(required, window.winfo_screenheight() - 80))

        def fit_buttons(event=None):
            nonlocal footer_height
            width = event.width if event else footer.winfo_width()
            needed = sum(button.winfo_reqwidth() + 8 for button in self.buttons.values()) + 32
            stacked = width < needed
            for index, button in enumerate(self.buttons.values()):
                button.grid(row=index if stacked else 0,
                            column=0 if stacked else index, sticky="ew", padx=4, pady=3)
            for index in range(len(self.buttons)):
                footer.columnconfigure(index, weight=1 if index == 0 or not stacked else 0)
            heights = [button.winfo_reqheight() + 6 for button in self.buttons.values()]
            footer_height = (sum(heights) if stacked else max(heights)) + 16
            fit_minimum()
        footer.bind("<Configure>", fit_buttons)
        heading.bind('<Configure>', fit_minimum, add='+')
        fit_buttons()
        self.default_button = self.buttons.get("cancel", self.buttons.get("no", self.buttons.get("ok")))
        window.update_idletasks()
        self._place()

    def _place(self):
        from surface_ui import owner_work_area
        bounds = owner_work_area(self.owner)
        if bounds is None:
            return
        left, top, right, bottom = bounds
        width, height = self.window.winfo_width(), self.window.winfo_height()
        x = max(left, min(self.window.winfo_rootx(), right - width))
        y = max(top, min(self.window.winfo_rooty(), bottom - height - 40))
        self.window.geometry(f'{width}x{height}+{x}+{y}')

    def _enter(self, _event=None):
        focused = self.window.focus_get()
        if focused in self.buttons.values():
            focused.invoke()
        elif self.kind == "string" and focused == self.entry:
            self._save_string()
        else:
            self.default_button.invoke()
        return "break"

    def _save_string(self):
        self.finish(self.entry.get())

    def finish(self, result):
        self.result = result
        if _alive(self.window):
            self.window.destroy()

    def cancel(self, _event=None):
        self.finish(False if self.kind == "yesno" else
                    ("ok" if self.kind in ("error", "info") else None))
        return "break"

    def _on_destroy(self, event):
        if event.widget == self.window:
            self._restore_grab()

    def _owner_destroyed(self, event):
        if event.widget == self.owner:
            self.cancel()

    def _restore_grab(self):
        if not self.acquired_grab:
            return
        self.acquired_grab = False
        try:
            current = self.owner.grab_current() if _alive(self.owner) else None
            if current == self.window:
                self.window.grab_release()
            elif current is not None:
                return
            if _alive(self.previous_grab):
                self.previous_grab.grab_set()
        except tk.TclError:
            pass

    def _cleanup(self):
        self._restore_grab()
        if self.owner_binding and _alive(self.owner):
            self.owner.unbind("<Destroy>", self.owner_binding)
        self.owner_binding = None
        if _alive(self.window):
            self.window.destroy()

    def show(self):
        try:
            if not _alive(self.owner) or not _alive(self.window):
                return self.result
            self.previous_grab = self.owner.grab_current()
            self.previous_focus = self.owner.focus_get()
            self.window.deiconify()
            self.window.wait_visibility()
            if not _alive(self.window) or not _alive(self.owner):
                return self.result
            self.window.grab_set()
            self.acquired_grab = True
            self.default_button.focus_set()
            self.window.wait_window()
        except tk.TclError:
            self.cancel()
        finally:
            self._cleanup()
            if _alive(self.previous_focus):
                try:
                    self.previous_focus.focus_set()
                except tk.TclError:
                    pass
        return self.result


def _show(kind, title, message, options):
    options = dict(options)
    parent = options.get("parent") or tk._default_root
    empty_result = False if kind == "yesno" else ("ok" if kind in ("error", "info") else None)
    if parent is not None and not _alive(parent):
        return empty_result
    if parent is not None:
        try:
            dialog_options = {key: value for key, value in options.items() if key != "parent"}
            dialog = _Dialog(parent, title, message, kind, **dialog_options)
        except (tk.TclError, ImportError, OSError):
            if not _alive(parent):
                return empty_result
        else:
            return dialog.show()
    methods = {"yesno": "askyesno", "yesnocancel": "askyesnocancel",
               "error": "showerror", "info": "showinfo", "string": "askstring"}
    if kind == "string":
        return _native_simpledialog.askstring(title, message, **options)
    if kind in ("yesno", "yesnocancel"):
        options["default"] = "no" if kind == "yesno" else "cancel"
    return getattr(_native_messagebox, methods[kind])(title, message, **options)


class _MessageBox:
    @staticmethod
    def askyesno(title=None, message=None, **options):
        return _show("yesno", title, message, options)

    @staticmethod
    def askyesnocancel(title=None, message=None, **options):
        return _show("yesnocancel", title, message, options)

    @staticmethod
    def showerror(title=None, message=None, **options):
        return _show("error", title, message, options)

    @staticmethod
    def showinfo(title=None, message=None, **options):
        return _show("info", title, message, options)


class _SimpleDialog:
    @staticmethod
    def askstring(title, prompt, **options):
        return _show("string", title, prompt, options)


messagebox = _MessageBox()
simpledialog = _SimpleDialog()
