"""VideoCatch's lightweight, native video workbench."""
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk

BG = "#181722"
PANEL = "#242132"
FIELD = "#302b40"
FG = "#f6eff4"
MUTED = "#b1a7bd"
ACCENT = "#ffc18e"
LINE = "#494056"
SECONDARY = "#c5a0d8"


def rounded_card(parent, padding):
    frame = ttk.Frame(parent, style="Card.TFrame", padding=padding)
    radius = 14
    px, py = (padding, padding) if isinstance(padding, int) else padding
    for right, bottom in ((False, False), (True, False), (False, True), (True, True)):
        corner = tk.Canvas(frame, bg=BG, highlightthickness=0, width=radius, height=radius)
        x, y = (-radius if right else 0), (-radius if bottom else 0)
        corner.create_oval(x, y, x + 2 * radius, y + 2 * radius, fill=PANEL, outline=PANEL)
        corner.place(relx=1 if right else 0, rely=1 if bottom else 0,
                     x=x + (px if right else -px), y=y + (py if bottom else -py))
    return frame


def build_ui(app):
    root = app.root
    assets = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "assets"
    root.iconbitmap(str(assets / "videocatch.ico"))
    root.title("拾影 VideoCatch · 视频工作台")
    scale = max(1, root.winfo_fpixels("1i") / 96)
    aw, ah = root.winfo_screenwidth() - 60, root.winfo_screenheight() - 90
    root.geometry(f"{min(round(1180 * scale), aw)}x{min(round(790 * scale), ah)}")
    root.minsize(min(round(1020 * scale), aw), min(round(720 * scale), ah))
    root.configure(bg=BG)
    root.option_add("*Font", ("Microsoft YaHei UI", 9))
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", font=("Microsoft YaHei UI", 9), background=BG, foreground=FG)
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=PANEL)
    style.configure("TLabel", background=BG, foreground=FG)
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Card.TLabel", background=PANEL, foreground=FG)
    style.configure("CardMuted.TLabel", background=PANEL, foreground=MUTED)
    style.configure("Heading.TLabel", background=PANEL, font=("Microsoft YaHei UI", 12, "bold"))
    style.configure("TButton", background=FIELD, foreground=FG, borderwidth=0, padding=(14, 9), focusthickness=1, focuscolor=ACCENT)
    style.map("TButton", background=[("active", "#493b57"), ("disabled", PANEL)], foreground=[("disabled", "#82758f")])
    style.configure("Accent.TButton", background=ACCENT, foreground="#241b15", font=("Microsoft YaHei UI", 9, "bold"))
    style.map("Accent.TButton", background=[("active", "#ffc698"), ("disabled", "#5b493b")], foreground=[("disabled", "#b3a18f")])
    style.configure("TCheckbutton", background=BG, foreground=FG, focuscolor=ACCENT, padding=5)
    style.map("TCheckbutton", background=[("active", BG)], indicatorbackground=[("selected", ACCENT), ("!selected", FIELD)])
    style.configure("TEntry", fieldbackground=FIELD, foreground=FG, bordercolor=LINE, lightcolor=LINE, darkcolor=LINE, insertcolor=FG, padding=9)
    style.map("TEntry", bordercolor=[("focus", ACCENT)])
    style.configure("TCombobox", fieldbackground=FIELD, foreground=FG, background=FIELD, arrowcolor=MUTED, padding=7, bordercolor=LINE, lightcolor=LINE, darkcolor=LINE)
    root.option_add("*TCombobox*Listbox.background", FIELD)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", "#554261")
    style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=FG, rowheight=round(38 * scale), borderwidth=0)
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    style.map("Treeview", background=[("selected", "#51405d")], foreground=[("selected", "#fff3eb")])
    style.configure("Treeview.Heading", background=FIELD, foreground=MUTED, padding=(9, 10), relief="flat", font=("Microsoft YaHei UI", 9))
    style.map("Treeview.Heading", background=[("active", "#44384f")])
    style.configure("TScrollbar", background=LINE, troughcolor=PANEL, borderwidth=0, arrowcolor=MUTED, width=10)
    for direction, sticky in (("Vertical", "ns"), ("Horizontal", "we")):
        style.layout(f"{direction}.TScrollbar", [(f"{direction}.Scrollbar.trough", {"sticky": "nswe", "children": [(f"{direction}.Scrollbar.thumb", {"sticky": sticky, "expand": "1"})]})])
        style.configure(f"{direction}.TScrollbar", background=LINE, troughcolor=PANEL, bordercolor=PANEL, lightcolor=LINE, darkcolor=LINE, width=8, arrowsize=8)
    style.configure("TPanedwindow", background=BG)

    outer = ttk.Frame(root, padding=24)
    outer.pack(fill="both", expand=True)
    header = ttk.Frame(outer)
    header.pack(fill="x", pady=(0, 18))
    app.brand_image = tk.PhotoImage(file=str(assets / "brand64.png"))
    app.ui_icons = {name: tk.PhotoImage(file=str(assets / f"ui-{name}.png")) for name in ("link", "cut", "browser", "pair", "folder", "videos")}
    app.empty_images = {name: tk.PhotoImage(file=str(assets / f"empty-{name}.png")) for name in ("browser", "media")}
    ttk.Label(header, image=app.brand_image).pack(side="left", padx=(0, 13))
    brand = ttk.Frame(header)
    brand.pack(side="left")
    ttk.Label(brand, text="拾影", font=("Microsoft YaHei UI", 23, "bold")).pack(anchor="w")
    ttk.Label(brand, text="VIDEOCATCH  /  捕捉光影，留住片段", foreground=SECONDARY, font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(2, 0))
    ttk.Button(header, text=" 裁剪本地视频", image=app.ui_icons["cut"], compound="left", command=app.clip_dialog).pack(side="right", padx=(12, 0))
    ttk.Button(header, text=" 复制配对码", image=app.ui_icons["pair"], compound="left", command=app.copy_pairing).pack(side="right", padx=(12, 0))
    ttk.Button(header, text=" 连接浏览器", image=app.ui_icons["browser"], compound="left", command=app.open_guide).pack(side="right")

    # A direct link is an independent entry point; never hidden behind extension setup.
    entry_card = rounded_card(outer, padding=(17, 13))
    entry_card.pack(fill="x", pady=(0, 14))
    ttk.Label(entry_card, text="  添加视频链接", image=app.ui_icons["link"], compound="left", style="Card.TLabel", font=("Microsoft YaHei UI", 10, "bold")).pack(side="left", padx=(0, 16))
    ttk.Button(entry_card, text="添加到列表  +", style="Accent.TButton", command=app.import_url).pack(side="right", padx=(12, 0))
    app.url = ttk.Entry(entry_card)
    app.url.pack(side="left", fill="x", expand=True)
    app.url.bind("<Return>", lambda _: app.import_url())

    # Reserve the controls before giving the lists the remaining space.
    footer = ttk.Frame(outer)
    footer.pack(side="bottom", fill="x", pady=(12, 0))
    settings = rounded_card(footer, padding=(17, 12))
    settings.pack(fill="x")
    settings.columnconfigure(1, weight=1)
    ttk.Label(settings, text="保存位置", style="CardMuted.TLabel").grid(row=0, column=0, padx=(0, 14), sticky="w")
    ttk.Entry(settings, textvariable=app.folder).grid(row=0, column=1, sticky="ew")
    ttk.Button(settings, text="更改", command=app.choose_folder).grid(row=0, column=2, padx=10)
    ttk.Button(settings, text=" 打开文件夹", image=app.ui_icons["folder"], compound="left", command=app.open_folder).grid(row=0, column=3)
    ttk.Label(settings, text="下载连接", style="CardMuted.TLabel").grid(row=1, column=0, padx=(0, 14), pady=(9, 0), sticky="w")
    network = ttk.Frame(settings, style="Card.TFrame")
    network.grid(row=1, column=1, columnspan=3, sticky="ew", pady=(9, 0))
    from network import SYSTEM, DIRECT
    app.proxy_box = ttk.Combobox(network, textvariable=app.proxy, values=(SYSTEM, DIRECT), width=25)
    app.proxy_box.pack(side="left")
    app.detect_button = ttk.Button(network, text="检测本机代理", command=app.detect_proxy)
    app.detect_button.pack(side="left", padx=10)
    ttk.Label(network, text="下载超时时可检测连接", style="CardMuted.TLabel").pack(side="left")
    bottom = ttk.Frame(footer)
    bottom.pack(fill="x", pady=(9, 0))
    ttk.Checkbutton(bottom, text="启用 AI 接口", variable=app.ai_enabled, command=app.toggle_ai).pack(side="right")
    ttk.Label(bottom, text="0.4.2  ·  本地视频工作台", style="Muted.TLabel", font=("Microsoft YaHei UI", 8)).pack(side="right", padx=12)
    notice = ttk.Label(bottom, textvariable=app.notice, foreground=ACCENT, wraplength=700, font=("Microsoft YaHei UI", 8))
    notice.pack(side="left", fill="x", expand=True)
    bottom.bind("<Configure>", lambda e: notice.configure(wraplength=max(230, e.width - 380)))

    guide = ttk.Frame(outer)
    guide.pack(fill="x", pady=(1, 12))
    hint = ttk.Label(guide, textvariable=app.step, style="Muted.TLabel", wraplength=1000)
    hint.pack(anchor="w")
    guide.bind("<Configure>", lambda e: hint.configure(wraplength=max(300, e.width)))

    split = ttk.Panedwindow(outer, orient="horizontal")
    split.pack(fill="both", expand=True)
    left = rounded_card(split, padding=16)
    right = rounded_card(split, padding=16)
    split.add(left, weight=1)
    split.add(right, weight=3)
    app.source_count = tk.StringVar(value="尚未连接")
    app.media_count = tk.StringVar(value="0 个视频")

    def heading(parent, title, count, icon):
        row = ttk.Frame(parent, style="Card.TFrame")
        row.pack(fill="x", pady=(0, 12))
        ttk.Label(row, text="  " + title, image=app.ui_icons[icon], compound="left", style="Heading.TLabel").pack(side="left")
        ttk.Label(row, textvariable=count, style="CardMuted.TLabel", font=("Microsoft YaHei UI", 8)).pack(side="right", padx=(8, 0))

    heading(left, "浏览器来源", app.source_count, "browser")
    heading(right, "视频列表", app.media_count, "videos")
    left_actions = ttk.Frame(left, style="Card.TFrame")
    left_actions.pack(side="bottom", fill="x", pady=(12, 0))
    ttk.Button(left_actions, text="开始监听", style="Accent.TButton", command=app.start_tabs).pack(side="left", fill="x", expand=True, padx=(0, 6))
    ttk.Button(left_actions, text="停止监听", command=app.stop_tabs).pack(side="left", fill="x", expand=True)
    tools = ttk.Frame(left, style="Card.TFrame")
    tools.pack(side="bottom", fill="x", pady=(10, 0))
    ttk.Button(tools, text="添加所选页面", command=app.add_tab_pages).pack(side="left", fill="x", expand=True, padx=(0, 6))
    ttk.Button(tools, text="全部停止", command=app.unwatch).pack(side="left", fill="x", expand=True)
    source_area = ttk.Frame(left, style="Card.TFrame")
    source_area.pack(fill="both", expand=True)
    app.tabs = app.tree(source_area, [("watch", "监听", 48), ("title", "页面", 165), ("browser", "浏览器", 75)])
    app.tabs.bind("<Button-1>", app.click_checkbox)
    app.tabs.bind("<Double-1>", lambda e: app.toggle_tabs() if app.tabs.identify_column(e.x) != "#1" else None)
    app.tabs.bind("<space>", lambda _: app.toggle_tabs())

    actions = ttk.Frame(right, style="Card.TFrame")
    actions.pack(side="bottom", fill="x", pady=(12, 0))
    ttk.Button(actions, text="保存所选视频", style="Accent.TButton", command=app.enqueue).pack(side="left")
    ttk.Button(actions, text="取消任务", command=app.cancel_selected).pack(side="left", padx=8)
    ttk.Button(actions, text="清除记录", command=app.clear_selected).pack(side="right")
    app.pause_button = ttk.Button(actions, text="暂停发现", command=app.pause)
    app.pause_button.pack(side="right", padx=8)
    detail = ttk.Label(right, textvariable=app.detail, style="CardMuted.TLabel", wraplength=640, font=("Microsoft YaHei UI", 8))
    detail.pack(side="bottom", fill="x", pady=(10, 0))
    right.bind("<Configure>", lambda e: detail.configure(wraplength=max(200, e.width - 40)))
    media_area = ttk.Frame(right, style="Card.TFrame")
    media_area.pack(fill="both", expand=True)
    app.media = app.tree(media_area, [("title", "视频 / 文件", 250), ("kind", "类型", 120), ("host", "来源", 90), ("status", "状态", 80), ("progress", "进度", 145)])
    app.media.configure(displaycolumns=("title", "kind", "status", "progress"))
    app.media.bind("<<TreeviewSelect>>", lambda _: app.show_detail())
    for tree in (app.tabs, app.media):
        tree.tag_configure("done", foreground="#9dd4ae")
        tree.tag_configure("busy", foreground=ACCENT)
        tree.tag_configure("error", foreground="#f59c98")

    def empty(parent, title, description, mode):
        box = tk.Frame(parent, bg=PANEL)
        ttk.Label(box, image=app.empty_images["browser" if mode == "browser" else "media"], style="Card.TLabel").pack(pady=(0, 14))
        tk.Label(box, text=title, bg=PANEL, fg=FG, font=("Microsoft YaHei UI", 11, "bold")).pack()
        tk.Label(box, text=description, bg=PANEL, fg=MUTED, justify="center", font=("Microsoft YaHei UI", 9)).pack(pady=(8, 0))
        return box

    app.empty_tabs = empty(source_area, "连接你的浏览器", "点击上方「连接浏览器」\n安装扩展并填写配对码", "browser")
    app.empty_media = empty(media_area, "好片段，从这里开始", "粘贴视频链接，或监听正在播放的网页\n已有视频？试试右上角的本地裁剪", "video")
    # Let the native geometry manager measure fonts before assigning pane proportions.
    app.layout_timer = root.after_idle(lambda: split.sashpos(0, max(290, round(split.winfo_width() * .31))) if split.winfo_exists() else None)


def refresh_ui(app, tabs, items, clients):
    watching = sum(t["watching"] for t in tabs)
    app.source_count.set(f"{clients} 个浏览器 · {watching} 监听" if clients else "尚未连接")
    busy = sum(i["status"] in {"排队中", "解析中", "下载中", "整理文件", "裁剪中"} for i in items)
    done = sum(i["status"] == "已保存" for i in items)
    app.media_count.set(f"{len(items)} 个视频 · {done} 已保存" + (f" · {busy} 处理中" if busy else ""))
    for box, rows in ((app.empty_tabs, tabs), (app.empty_media, items)):
        if rows:
            box.place_forget()
        else:
            box.place(relx=.5, rely=.53, anchor="center")
    for item in items:
        tag = "done" if item["status"] == "已保存" else "error" if item["status"] == "失败" else "busy" if item["status"] in {"排队中", "解析中", "下载中", "整理文件", "裁剪中"} else ""
        app.media.item(item["id"], tags=(tag,))
