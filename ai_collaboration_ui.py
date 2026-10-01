"""A nonmodal collaboration surface; authorization remains in App callbacks."""
import tkinter as tk
from tkinter import ttk

from surface_ui import ScrollableBody, card, header, setup_window


def local_api_address(app):
    """Display only the known loopback endpoint, never arbitrary bridge values."""
    port = getattr(getattr(app, "bridge", None), "server_port", 18796)
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        port = 18796
    return f"http://127.0.0.1:{port}/api/v1/"


class CollaborationWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self._closed = False
        self._status_trace = None
        self.body = None
        self.status = tk.StringVar(self)
        self.status_detail = tk.StringVar(self)
        setup_window(self, app, "AI 协作", 720, 720, minwidth=650, minheight=560)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._escape_binding = self.bind("<Escape>", self._on_escape, add="+")
        self.bind("<Destroy>", self._on_destroy, add="+")

        header(self, "AI 协作", "连接本机 AI，让下载与剪辑接续你的创作。", app=app)
        self.body = ScrollableBody(self)
        frame = self.body.frame

        connection = card(frame, padding=20)
        connection.pack(fill="x", pady=(0, 14))
        self._label(connection, "本机协作", style="Section.TLabel")
        self._label(connection, textvariable=self.status, style="SurfaceStatus.TLabel", pady=(8, 4))
        self._label(connection, textvariable=self.status_detail, muted=True)
        self._label(connection, local_api_address(app), muted=True, pady=(8, 12))
        self.toggle_button = ttk.Button(connection, command=self.toggle, style="Quiet.TButton")
        self.toggle_button.pack(fill="x")

        abilities = card(frame, padding=20)
        abilities.pack(fill="x", pady=(0, 14))
        self._label(abilities, "AI 可以帮你做什么", style="Section.TLabel")
        for title, detail in (
            ("获取网页视频", "读取已捕获的媒体、导入视频链接并安排下载。"),
            ("裁剪本地素材", "按起止时间生成新片段，保留原始视频。"),
            ("跟进任务", "查看下载与剪辑状态，按需取消任务。"),
        ):
            self._label(abilities, title, pady=(12, 3))
            self._label(abilities, detail, muted=True)

        steps = card(frame, padding=20)
        steps.pack(fill="x", pady=(0, 14))
        self._label(steps, "开始协作", style="Section.TLabel")
        for text in (
            "01  复制使用指引，粘贴给能执行本机命令的 AI 客户端。",
            "02  描述要下载的视频，或给出本地素材与裁剪时间。",
            "03  在工作台确认任务状态与生成的文件。",
        ):
            self._label(steps, text, muted=True, pady=(10, 0))
        self.copy_guide_button = ttk.Button(
            steps, text="开启协作并复制使用指引", style="Accent.TButton", command=self.copy_guide)
        self.copy_guide_button.pack(fill="x", pady=(16, 8))
        self.copy_pairing_button = ttk.Button(
            steps, text="复制本次配对码", style="Quiet.TButton", command=self.copy_pairing)
        self.copy_pairing_button.pack(fill="x", pady=(0, 8))
        self.preferences_button = ttk.Button(
            steps, text="权限与启动设置", style="Quiet.TButton", command=self.open_preferences)
        self.preferences_button.pack(fill="x")
        self._label(steps, "复制结果与操作提示会显示在工作台提示栏。", muted=True, pady=(10, 0))

        limits = card(frame, padding=20)
        limits.pack(fill="x", pady=(0, 6))
        self._label(limits, "协作边界", style="Section.TLabel")
        self._label(limits, "接口仅在本机开放，调用须鉴权；配对码每次启动轮换。关闭协作会撤销当前授权与重启恢复权限。",
                    muted=True, pady=(8, 0))
        self._label(limits, "网页采集仍需浏览器扩展配对，并由你在浏览器中刷新或播放；协作不能绕过登录、DRM 或网站限制。",
                    muted=True, pady=(8, 0))
        self._status_trace = app.ai_enabled.trace_add("write", self.refresh_status)
        self.refresh_status()

    @staticmethod
    def _label(parent, text=None, *, textvariable=None, style=None, muted=False, pady=0):
        options = {"style": style or ("SurfaceMuted.TLabel" if muted else "Surface.TLabel"),
                   "wraplength": 540, "justify": "left", "anchor": "w"}
        if textvariable is not None:
            options["textvariable"] = textvariable
        else:
            options["text"] = text
        label = ttk.Label(parent, **options)
        label.pack(fill="x", pady=pady)
        # Measure the actual card interior so small windows and large fonts wrap.
        parent.bind("<Configure>", lambda event: label.configure(wraplength=max(1, event.width - 40)), add="+")
        return label

    def refresh_status(self, *_args):
        if self._closed:
            return
        enabled = bool(self.app.ai_enabled.get())
        self.status.set("协作已开启" if enabled else "协作已关闭")
        self.status_detail.set("本机 AI 客户端可使用本次配对码连接。" if enabled else "开启后，本机 AI 客户端可请求下载与剪辑。")
        self.toggle_button.configure(text="关闭协作并撤销授权" if enabled else "开启本机协作",
                                     style="Danger.TButton" if enabled else "Quiet.TButton")

    def toggle(self):
        self.app.ai_enabled.set(not self.app.ai_enabled.get())
        self.app.toggle_ai()
        self.refresh_status()

    def copy_guide(self):
        self.app.copy_ai_collaboration()
        self.refresh_status()

    def copy_pairing(self):
        self.app.copy_pairing()

    def open_preferences(self):
        self.app.open_preferences(section="AI 协作")

    def _on_escape(self, _event=None):
        self.close()
        return "break"

    def _on_destroy(self, event):
        if event.widget is self:
            self.close(destroy=False)

    def close(self, destroy=True):
        if self._closed:
            return
        self._closed = True
        app = self.app
        if self._status_trace is not None:
            try:
                app.ai_enabled.trace_remove("write", self._status_trace)
            except tk.TclError:
                pass
            self._status_trace = None
        if self.body is not None:
            try:
                self.body.close()
            except tk.TclError:
                # Owner teardown may have already destroyed the canvas.
                pass
            self.body = None
        if getattr(app, "ai_collaboration_window", None) is self:
            app.ai_collaboration_window = None
        self.app = None
        if destroy:
            try:
                self.destroy()
            except tk.TclError:
                pass


def show_collaboration(app):
    window = getattr(app, "ai_collaboration_window", None)
    if window is not None and window.winfo_exists():
        window.refresh_status()
        window.deiconify()
        window.lift()
        return window
    window = CollaborationWindow(app)
    app.ai_collaboration_window = window
    return window
