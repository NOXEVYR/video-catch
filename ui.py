"""VideoCatch's lightweight, native video workbench."""
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk
from version import VERSION

BG = "#181722"
PANEL = "#242132"
FIELD = "#302b40"
FG = "#f6eff4"
MUTED = "#b1a7bd"
ACCENT = "#ffc18e"
LINE = "#494056"
SECONDARY = "#c5a0d8"


class ResponsivePane(tk.PanedWindow):
    """Classic Tk permits orientation changes and a plain, draggable sash."""
    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg=BG, bd=0, sashwidth=8, sashrelief='flat', showhandle=False, opaqueresize=True, **kwargs)

    def add(self, child, weight=1):
        super().add(child, stretch='always', minsize=120)

    def sashpos(self, index, value=None):
        vertical = str(self.cget('orient')) == 'vertical'
        if value is not None:
            self.sash_place(index, 0 if vertical else value, value if vertical else 0)
        return self.sash_coord(index)[1 if vertical else 0]


def wrap_controls(frame, gap=6, reverse=False, flow=False):
    """Wrap control rows using measured widget widths, including DPI fonts."""
    children = frame.winfo_children()
    if reverse:
        children.reverse()
    for child in children:
        child.pack_forget()
    def fit(event):
        if flow:
            # Grid shares column widths between rows. Different button widths
            # can therefore overflow even when each row's measured sum fits.
            x = y = row_height = 0
            width = max(1, event.width)
            for child in children:
                needed, height = child.winfo_reqwidth(), child.winfo_reqheight()
                if x and x + needed > width:
                    x, y, row_height = 0, y + row_height + gap, 0
                child.place(x=x, y=y, width=needed, height=height)
                x += needed + gap
                row_height = max(row_height, height)
            frame.configure(height=y + row_height)
            return
        row = col = used = 0
        width = max(1, event.width - 24)
        for child in children:
            needed = child.winfo_reqwidth() + gap
            if col and used + needed > width:
                row, col, used = row + 1, 0, 0
            child.grid(row=row, column=col, sticky='w', padx=(0, gap), pady=3)
            col += 1
            used += needed
    frame.bind('<Configure>', fit, add='+')
    fit(type('Size', (), {'width': frame.winfo_width()})())


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


def _build_browser_ui(app, parent=None):
    root = app.root
    assets = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "assets"
    if sys.platform != "darwin":
        root.iconbitmap(str(assets / "videocatch.ico"))
    root.title("拾影 VideoCatch · 视频工作台")
    scale = max(1, root.winfo_fpixels("1i") / 96)
    aw, ah = root.winfo_screenwidth() - 60, root.winfo_screenheight() - 90
    root.geometry(f"{min(round(1180 * scale), aw)}x{min(round(790 * scale), ah)}")
    root.minsize(min(round(1020 * scale), aw), min(round(720 * scale), ah))
    root.configure(bg=BG)
    # ttk's default font comes from the style. A global *Font resource would
    # override section/title style fonts on every popup widget.
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", font=("Microsoft YaHei UI", 9), background=BG, foreground=FG)
    style.configure("TFrame", background=BG)
    style.configure("TNotebook", background=BG, borderwidth=0, bordercolor=LINE, lightcolor=LINE, darkcolor=LINE)
    style.configure("TNotebook.Tab", background=FIELD, foreground=FG, padding=(16, 10), bordercolor=LINE, lightcolor=LINE, darkcolor=LINE)
    style.map("TNotebook.Tab", background=[("selected", PANEL)], foreground=[("selected", ACCENT)])
    style.configure("TRadiobutton", background=BG, foreground=FG)
    style.map("TRadiobutton", indicatorbackground=[("selected", ACCENT), ("!selected", FIELD)], background=[("active", BG)])
    style.configure("TScale", background=SECONDARY, troughcolor=FIELD, bordercolor=FIELD, lightcolor=LINE, darkcolor=LINE)
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
    style.map("TCombobox", fieldbackground=[("disabled", PANEL), ("readonly", FIELD)],
              foreground=[("disabled", "#82758f"), ("readonly", FG)],
              selectbackground=[("readonly", FIELD)], selectforeground=[("readonly", FG)])
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
    style.layout("Horizontal.Sash", [("Sash.hsash", {"sticky": "nswe"})])
    style.configure("Horizontal.Sash", sashthickness=6, background=BG)
    style.layout("Vertical.Sash", [("Sash.vsash", {"sticky": "nswe"})])
    style.configure("Vertical.Sash", sashthickness=6, background=BG)
    from surface_ui import register_styles
    register_styles(root)

    # Keep lists and actions usable when the display cannot fit the workbench.
    viewport = ttk.Frame(parent if parent is not None else root)
    viewport.pack(fill="both", expand=True)
    viewport.rowconfigure(0, weight=1)
    viewport.columnconfigure(0, weight=1)
    canvas = tk.Canvas(viewport, bg=BG, highlightthickness=0)
    canvas.grid(row=0, column=0, sticky="nsew")
    vertical = ttk.Scrollbar(viewport, orient="vertical", command=canvas.yview)
    horizontal = ttk.Scrollbar(viewport, orient="horizontal", command=canvas.xview)
    vertical.grid(row=0, column=1, sticky="ns")
    horizontal.grid(row=1, column=0, sticky="ew")
    canvas.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    outer = ttk.Frame(canvas, padding=16)
    content = canvas.create_window(0, 0, window=outer, anchor="nw")
    app.workbench_canvas = canvas

    def vertical_padding(value):
        parts = [root.winfo_pixels(str(part)) for part in root.tk.splitlist(value)]
        return (2 * parts[0] if len(parts) == 1 else 2 * parts[1] if len(parts) == 2
                else parts[1] + parts[3] if len(parts) == 4 else sum(parts))

    def packed_height(parent, excluded):
        return sum(child.winfo_reqheight() + vertical_padding(child.pack_info().get('pady', 0)) +
                   2 * int(child.pack_info().get('ipady', 0))
                   for child in parent.pack_slaves() if child not in excluded)

    def list_budget(tree):
        row_height = int(style.lookup('Treeview', 'rowheight'))
        header = max(20, tree.winfo_reqheight() - int(tree.cget('height')) * row_height)
        # Reserve the horizontal tree scrollbar as well as four complete rows.
        scrollbar = max((child.winfo_reqheight() for child in tree.master.winfo_children()
                         if child.winfo_class() == 'TScrollbar'), default=12)
        return header + 4 * row_height + scrollbar + 8

    def fit_workbench(_event=None):
        width = max(1, canvas.winfo_width())
        # Use real available page width, including the navigation rail and DPI.
        # Stack the two panels when their controls cannot fit side by side.
        if 'split' not in locals_for_layout:
            return
        minimum_left = max(260, left_actions.winfo_reqwidth() + 32, tools.winfo_reqwidth() + 32)
        minimum_right = max(390, max((button.winfo_reqwidth() for button in app.media_buttons.values()), default=0) + 32)
        stacked = width - 40 < minimum_left + minimum_right
        orientation = 'vertical' if stacked else 'horizontal'
        changed = str(split.cget('orient')) != orientation
        if changed:
            split.configure(orient=orientation)
        left_height = (vertical_padding(left.cget('padding')) + packed_height(left, {source_area}) +
                       list_budget(app.tabs))
        right_height = (vertical_padding(right.cget('padding')) + packed_height(right, {media_area}) +
                        list_budget(app.media))
        split.paneconfigure(left, minsize=left_height if stacked else minimum_left)
        split.paneconfigure(right, minsize=right_height if stacked else minimum_right)
        panels_height = left_height + right_height + int(split.cget('sashwidth')) + 4 if stacked else max(left_height, right_height) + 4
        outside_height = vertical_padding(outer.cget('padding')) + packed_height(outer, {split})
        height = max(canvas.winfo_height(), outside_height + panels_height,
                     round((1000 if stacked else 650) * scale))
        canvas.itemconfigure(content, width=width, height=height)
        canvas.configure(scrollregion=(0, 0, width, height))
        canvas.xview_moveto(0)
        if changed or not locals_for_layout.get('positioned'):
            split.sashpos(0, left_height if stacked else minimum_left)
            locals_for_layout['positioned'] = True
        elif stacked:
            # A width change can wrap the lower panel without changing the
            # orientation. Reclaim excess upper-panel space before it crowds
            # out the lower list, while retaining any usable sash adjustment.
            maximum = height - outside_height - right_height - int(split.cget('sashwidth')) - 4
            position = max(left_height, min(split.sashpos(0), maximum))
            if split.sashpos(0) != position:
                split.sashpos(0, position)
        if height > canvas.winfo_height():
            vertical.grid()
        else:
            vertical.grid_remove()
        horizontal.grid_remove()

    locals_for_layout = {}
    canvas.bind("<Configure>", fit_workbench)

    def scroll_workbench(event):
        if event.widget.winfo_toplevel() != root:
            return
        if not str(event.widget).startswith(str(viewport)):
            return
        # Trees and dropdowns retain their own scrolling.
        if event.widget.winfo_class() in {"Treeview", "TCombobox", "TScrollbar"}:
            return
        delta = -1 if getattr(event, "num", None) == 4 else 1 if getattr(event, "num", None) == 5 else (-1 if event.delta > 0 else 1)
        if event.state & 1:
            canvas.xview_scroll(delta * 3, "units")
        else:
            canvas.yview_scroll(delta * 3, "units")

    root.bind("<MouseWheel>", scroll_workbench, add=True)
    root.bind("<Button-4>", scroll_workbench, add=True)
    root.bind("<Button-5>", scroll_workbench, add=True)

    def reveal_focus(event):
        widget = event.widget
        if widget.winfo_toplevel() != root or widget == root:
            return
        parent = widget
        while parent is not None and parent != outer:
            parent = getattr(parent, "master", None)
        if parent != outer:
            return
        x = widget.winfo_rootx() - outer.winfo_rootx()
        y = widget.winfo_rooty() - outer.winfo_rooty()
        for position, size, origin, viewport_size, total, move in (
                (x, widget.winfo_width(), canvas.canvasx(0), canvas.winfo_width(), outer.winfo_width(), canvas.xview_moveto),
                (y, widget.winfo_height(), canvas.canvasy(0), canvas.winfo_height(), outer.winfo_height(), canvas.yview_moveto)):
            if position < origin:
                move(max(0, position - 8) / max(1, total))
            elif position + size > origin + viewport_size:
                move(max(0, position + size + 8 - viewport_size) / max(1, total))

    root.bind("<FocusIn>", reveal_focus, add=True)
    # Brand and primary actions live in the shell; retain images for child views.
    app.brand_image = tk.PhotoImage(file=str(assets / "brand64.png"))
    app.ui_icons = {name: tk.PhotoImage(file=str(assets / f"ui-{name}.png")) for name in ("link", "cut", "browser", "pair", "folder", "videos")}
    app.empty_images = {name: tk.PhotoImage(file=str(assets / f"empty-{name}.png")) for name in ("browser", "media")}
    collaboration = ttk.Frame(outer)
    collaboration.pack(fill="x", pady=(0, 12))
    ttk.Button(collaboration, text="连接浏览器", command=app.open_guide).pack(side="left")
    ttk.Button(collaboration, text="复制配对码", command=app.copy_pairing).pack(side="left", padx=10)
    connection = ttk.Label(outer, textvariable=app.connection, style="Muted.TLabel")
    connection.pack(fill='x', pady=(0, 8))
    outer.bind('<Configure>', lambda e: connection.configure(wraplength=max(180, e.width-40)), add='+')

    # A direct link is an independent entry point; never hidden behind extension setup.
    entry_card = rounded_card(outer, padding=(17, 13))
    entry_card.pack(fill="x", pady=(0, 14))
    ttk.Label(entry_card, text="  粘贴视频链接", image=app.ui_icons["link"], compound="left", style="Card.TLabel", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor='w', pady=(0, 8))
    ttk.Button(entry_card, text="添加  +", style="Accent.TButton", command=app.import_url).pack(side="right", padx=(12, 0))
    app.url = ttk.Entry(entry_card)
    app.url.pack(side="left", fill="x", expand=True)
    app.url.bind("<Return>", lambda _: app.import_url())

    # Reserve the controls before giving the lists the remaining space.
    footer = ttk.Frame(outer)
    footer.pack(side="bottom", fill="x", pady=(12, 0))
    settings = rounded_card(footer, padding=(17, 12))
    def toggle_download_settings():
        opened = not locals_for_layout.get('settings_open', False)
        locals_for_layout['settings_open'] = opened
        if opened:
            settings.pack(fill='x', pady=(8, 0))
        else:
            settings.pack_forget()
        settings_toggle.configure(text='收起保存与连接设置  ▴' if opened else '保存位置与下载连接  ▾')
        fit_workbench()
    settings_toggle = ttk.Button(footer, text='保存位置与下载连接  ▾', command=toggle_download_settings)
    settings_toggle.pack(anchor='w')
    app.toggle_download_settings = toggle_download_settings
    settings.columnconfigure(1, weight=1)
    ttk.Label(settings, text="保存位置", style="CardMuted.TLabel").grid(row=0, column=0, padx=(0, 14), sticky="w")
    ttk.Entry(settings, textvariable=app.folder).grid(row=0, column=1, sticky="ew")
    ttk.Button(settings, text="更改", command=app.choose_folder).grid(row=0, column=2, padx=10)
    ttk.Button(settings, text="打开文件夹", command=app.open_folder).grid(row=2, column=1, sticky='w', pady=(9, 0))
    ttk.Label(settings, text="下载连接", style="CardMuted.TLabel").grid(row=1, column=0, padx=(0, 14), pady=(9, 0), sticky="w")
    network = ttk.Frame(settings, style="Card.TFrame")
    network.grid(row=1, column=1, columnspan=3, sticky="ew", pady=(9, 0))
    from network import SYSTEM, DIRECT
    app.proxy_box = ttk.Combobox(network, textvariable=app.proxy, values=(SYSTEM, DIRECT), width=25)
    app.proxy_box.pack(side="left")
    app.detect_button = ttk.Button(network, text="检测本机代理", command=app.detect_proxy)
    app.detect_button.pack(side="left", padx=10)
    app.proxy_box.configure(width=18)
    bottom = ttk.Frame(footer)
    # The shell already owns the live notice and version; avoid duplicate bars.
    ttk.Label(bottom, text=VERSION + "  ·  本地视频工作台", style="Muted.TLabel", font=("Microsoft YaHei UI", 8)).pack(side="right", padx=12)
    notice = ttk.Label(bottom, textvariable=app.notice, foreground=ACCENT, wraplength=700, font=("Microsoft YaHei UI", 8))
    notice.pack(side="left", fill="x", expand=True)
    bottom.bind("<Configure>", lambda e: notice.configure(wraplength=max(230, e.width - 210)))

    guide = ttk.Frame(outer)
    guide.pack(fill="x", pady=(1, 12))
    hint = ttk.Label(guide, textvariable=app.step, style="Muted.TLabel", wraplength=1000)
    hint.pack(anchor="w")
    guide.bind("<Configure>", lambda e: hint.configure(wraplength=max(300, e.width)))

    split = ResponsivePane(outer, orient="horizontal")
    app.source_split = split
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
    heading(right, "网页发现与保存", app.media_count, "videos")
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
    app.media_selection_text = tk.StringVar(value='已选 0 项 · Ctrl / Shift 多选')
    ttk.Label(actions, textvariable=app.media_selection_text, style='CardMuted.TLabel').pack(anchor='w', pady=(0, 3))
    main_actions = ttk.Frame(actions, style="Card.TFrame")
    main_actions.pack(fill="x")
    app.media_buttons = {}
    for name, text, command in (('save', '保存所选视频', app.enqueue), ('clip', '剪辑视频（单项）', app.clip_selected),
                                ('rename', '修改显示名称', app.rename_selected), ('cancel', '取消任务', app.cancel_selected),
                                ('delete', '移除所选记录（保留文件）', app.clear_selected), ('all', '全选', app.select_all_media),
                                ('none', '取消选择', app.clear_media_selection)):
        button = ttk.Button(main_actions, text=text, command=command,
                            style='Accent.TButton' if name == 'save' else 'TButton')
        button.pack(side='left')
        app.media_buttons[name] = button
    app.pause_button = ttk.Button(main_actions, text="暂停发现", command=app.pause)
    app.pause_button.pack(side='left')
    wrap_controls(main_actions, flow=True)
    detail = ttk.Label(right, textvariable=app.detail, style="CardMuted.TLabel", wraplength=640, font=("Microsoft YaHei UI", 8))
    detail.pack(side="bottom", fill="x", pady=(10, 0))
    right.bind("<Configure>", lambda e: detail.configure(wraplength=max(200, e.width - 40)))
    media_area = ttk.Frame(right, style="Card.TFrame")
    media_area.pack(fill="both", expand=True)
    app.media = app.tree(media_area, [("title", "视频 / 文件", 250), ("kind", "类型", 120), ("host", "来源", 90), ("status", "状态", 80), ("progress", "进度", 145)])
    app.media.configure(displaycolumns=("title", "kind", "status", "progress"))
    app.media.bind("<<TreeviewSelect>>", app.update_media_actions)
    app.media.bind("<Double-1>", lambda _: app.clip_selected())
    from context_menu import ContextMenu
    app.media_menu = ContextMenu(root)
    app.media.bind("<Button-3>", app.media_context_menu)
    app.media.bind('<Control-a>', app.select_all_media)
    app.media.bind('<Control-A>', app.select_all_media)
    app.media.bind('<Escape>', app.clear_media_selection)
    app.media.bind("<Delete>", lambda _: app.clear_selected())
    app.media.bind("<F2>", lambda _: app.rename_selected())
    app.media.bind("<ButtonPress-1>", app.begin_media_drag)
    app.media.bind("<B1-Motion>", app.drag_media)
    app.media.bind("<ButtonRelease-1>", app.release_media_drag)
    for tree in (app.tabs, app.media):
        tree.tag_configure("done", foreground="#9dd4ae")
        tree.tag_configure("busy", foreground=ACCENT)
        tree.tag_configure("error", foreground="#f59c98")

    def empty(parent, title, description, mode):
        box = tk.Frame(parent, bg=PANEL)
        illustration = ttk.Label(box, image=app.empty_images["browser" if mode == "browser" else "media"], style="Card.TLabel")
        illustration.pack(pady=(0, 14))
        title_label = tk.Label(box, text=title, bg=PANEL, fg=FG, font=("Microsoft YaHei UI", 11, "bold"))
        title_label.pack()
        description_label = tk.Label(box, text=description, bg=PANEL, fg=MUTED, justify="center", font=("Microsoft YaHei UI", 9))
        description_label.pack(pady=(8, 0))

        def fit_empty(event):
            description_label.configure(wraplength=max(100, event.width - 16))
            needed = illustration.winfo_reqheight() + title_label.winfo_reqheight() + description_label.winfo_reqheight() + 65
            if event.height >= needed:
                illustration.pack(before=title_label, pady=(0, 14))
            else:
                illustration.pack_forget()

        parent.bind("<Configure>", fit_empty, add=True)
        return box

    app.empty_tabs = empty(source_area, "连接你的浏览器", "点击上方「连接浏览器」\n安装扩展并填写配对码", "browser")
    app.empty_media = empty(media_area, "好片段，从这里开始", "粘贴视频链接，或监听正在播放的网页\n已有视频？在素材库导入后即可剪辑", "video")
    # Let the native geometry manager measure fonts before assigning pane proportions.
    locals_for_layout['split'] = split
    # Wrapped controls and detail text can change height without resizing the
    # canvas. Re-budget after those measurements settle as well as on resize.
    for widget in (outer, main_actions, detail, connection, hint, footer):
        widget.bind('<Configure>', fit_workbench, add='+')
    app.fit_browser_layout = fit_workbench
    app.layout_timer = root.after_idle(fit_workbench)


def build_ui(app):
    """One results-first workbench, with capture and preferences as secondary UI."""
    root = app.root
    shell = ttk.Frame(root)
    shell.pack(fill='both', expand=True)
    navigation = ttk.Frame(shell, padding=(12, 24))
    navigation.pack(side='left', fill='y')
    body = ttk.Frame(shell)
    body.pack(side='left', fill='both', expand=True)
    header = ttk.Frame(body, padding=(20, 20, 20, 12))
    header.pack(fill='x')
    status = ttk.Frame(body, padding=(20, 8))
    status.pack(side='bottom', fill='x')
    status_notice = ttk.Label(status, textvariable=app.notice, style='Muted.TLabel', wraplength=900)
    status_notice.pack(side='left', fill='x', expand=True)
    status.bind('<Configure>', lambda e: status_notice.configure(wraplength=max(180, e.width-160)))
    ttk.Label(status, text=VERSION, style='Muted.TLabel').pack(side='right', padx=8)
    content = ttk.Frame(body)
    content.pack(fill='both', expand=True)
    content.rowconfigure(0, weight=1)
    content.columnconfigure(0, weight=1)
    app.pages = {name: ttk.Frame(content) for name in ('library', 'browser', 'tasks')}
    for page in app.pages.values():
        page.grid(row=0, column=0, sticky='nsew')
    app.library_parent = app.pages['library']
    _build_browser_ui(app, app.pages['browser'])
    root.geometry(f"{min(1280, root.winfo_screenwidth()-50)}x{min(850, root.winfo_screenheight()-80)}")
    root.minsize(min(1000, root.winfo_screenwidth()-50), min(650, root.winfo_screenheight()-80))
    ttk.Label(navigation, image=app.brand_image).pack(anchor='w', padx=10)
    ttk.Label(navigation, text='拾影', font=('Microsoft YaHei UI', 19, 'bold')).pack(anchor='w', padx=12, pady=(6, 28))
    app.navigation_buttons = {}
    for name, label in [('library', '素材库'), ('browser', '网页采集'), ('tasks', '任务与导出')]:
        button = ttk.Button(navigation, text=label, width=12, command=lambda n=name: app.show_page(n))
        button.pack(fill='x', pady=4)
        app.navigation_buttons[name] = button
    ttk.Button(navigation, text='设置与恢复', command=app.open_preferences).pack(side='bottom', fill='x', pady=6)
    ttk.Button(navigation, text='退出拾影', command=app.close).pack(side='bottom', fill='x', pady=6)
    app.page_title = tk.StringVar(value='素材库')
    page_heading = ttk.Label(header, textvariable=app.page_title, font=('Microsoft YaHei UI', 19, 'bold'))
    page_heading.grid(row=0, column=0, sticky='w')
    primary_actions = ttk.Frame(header)
    primary_actions.grid(row=0, column=1, sticky='e')
    header.columnconfigure(1, weight=1)
    ttk.Button(primary_actions, text='添加链接', command=app.focus_link_entry).pack(side='right', padx=(8, 0))
    ttk.Button(primary_actions, text='截图', command=app.open_screenshot).pack(side='right', padx=(8, 0))
    ttk.Button(primary_actions, text='录屏', style='Accent.TButton', command=app.open_recording).pack(side='right', padx=(8, 0))
    ttk.Button(primary_actions, text='AI 协作', command=app.open_ai_collaboration).pack(side='right', padx=(8, 0))
    wrap_controls(primary_actions, reverse=True)
    def fit_header(event):
        needed = sum(child.winfo_reqwidth() + 6 for child in primary_actions.winfo_children())
        narrow = event.width < page_heading.winfo_reqwidth() + needed + 60
        primary_actions.grid(row=1 if narrow else 0, column=0 if narrow else 1,
                             columnspan=2 if narrow else 1, sticky='ew', pady=(10,0) if narrow else 0,
                             padx=0 if narrow else (24,0))
    header.bind('<Configure>', fit_header)
    task_page = app.pages['tasks']
    task_body = ttk.Frame(task_page, padding=20)
    task_body.pack(fill='both', expand=True)
    task_hint = ttk.Label(task_body, text='下载、录制保存和剪辑导出统一在这里查看。删除记录保留素材库和原文件。', style='Muted.TLabel', wraplength=720)
    task_hint.pack(fill='x', pady=(0, 12))
    task_body.bind('<Configure>', lambda e: task_hint.configure(wraplength=max(180,e.width-40)))
    app.task_selection_text = tk.StringVar(value='选择任务查看成果，或删除已结束的记录')
    ttk.Label(task_body, textvariable=app.task_selection_text, style='Muted.TLabel').pack(anchor='w', pady=(0, 8))
    actions = ttk.Frame(task_body)
    actions.pack(side='bottom', fill='x', pady=12)
    app.task_buttons = {}
    for name, title, command in (('open', '查看成果', app.open_task_result),
                                  ('cancel', '停止 / 取消任务', app.cancel_task_selection),
                                  ('delete', '删除所选记录', app.remove_task_records)):
        button = ttk.Button(actions, text=title, command=command, state='disabled')
        button.pack(side='left', padx=(0, 8))
        app.task_buttons[name] = button
    ttk.Button(actions, text='打开保存文件夹', command=app.open_folder).pack(side='right')
    wrap_controls(actions)
    app.task_tree = app.tree(task_body, [('title', '任务', 300), ('kind', '类型', 130), ('status', '状态', 100), ('progress', '进度', 140), ('action', '操作', 95)])
    app.task_tree.column('action', stretch=False, width=110, anchor='center')
    app.task_tree.bind('<Double-1>', lambda e: app.open_task_result() if app.task_tree.identify_column(e.x) != '#5' else 'break')
    app.task_tree.bind('<<TreeviewSelect>>', app.update_task_actions)
    app.task_tree.bind('<Delete>', lambda _: app.remove_task_records())
    app.task_tree.bind('<ButtonPress-1>', app.task_action_press)
    app.task_tree.bind('<ButtonRelease-1>', app.task_action_release)
    from context_menu import ContextMenu
    app.task_menu = ContextMenu(root)
    app.task_tree.bind('<Button-3>', app.task_context_menu)
    app.show_page('library')


def refresh_ui(app, tabs, items, clients):
    watching = sum(t["watching"] and t.get("online", True) for t in tabs)
    app.source_count.set(f"{clients} 个浏览器 · {watching} 监听" if clients else "尚未连接")
    from ai_api import BUSY
    busy = sum(i["status"] in BUSY for i in items)
    done = sum(i["status"] == "已保存" for i in items)
    app.media_count.set(f"{len(items)} 个视频 · {done} 已保存" + (f" · {busy} 处理中" if busy else ""))
    for box, rows in ((app.empty_tabs, tabs), (app.empty_media, items)):
        if rows:
            box.place_forget()
        else:
            box.place(relx=.5, rely=.5, y=18, anchor="center")
    for item in items:
        tag = "done" if item["status"] == "已保存" else "error" if item["status"] == "失败" else "busy" if item["status"] in BUSY else ""
        app.media.item(item["id"], tags=(tag,))
