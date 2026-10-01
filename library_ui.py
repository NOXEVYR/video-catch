"""Bounded thumbnail gallery. Workers never hold Tk widgets or PhotoImages."""
from pathlib import Path
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk, filedialog
from dialogs import simpledialog, messagebox

from clip_preview import Decoder
from media_library import MEDIA_EXTENSIONS
from file_actions import capture_identity, recycle_files, export_copies, FileActionError
from runtime_paths import open_local
from ui import BG, PANEL, FIELD, FG, MUTED, ACCENT, wrap_controls
from library_selection import GallerySelection
from context_menu import ContextMenu

TYPES = {'全部类型': 'all', '视频': 'video', '截图 PNG': 'image'}
SOURCES = {'全部来源': 'all', '下载': 'download', '剪辑': 'clip', '录屏': 'record',
           '截图': 'screenshot', '导入': 'import', '扫描': 'scan', '历史素材': 'legacy'}
SORTS = {'最近保存': 'recent', '名称排序': 'name'}


def _put(results, value, stop):
    while not stop.is_set():
        try:
            results.put(value, timeout=.1)
            return
        except queue.Full:
            pass


def scan_media(folder, results, stop):
    paths, error = [], ''
    try:
        for index, path in enumerate(Path(folder).iterdir()):
            if stop.is_set() or index >= 5000:
                break
            if path.suffix.lower() in MEDIA_EXTENSIONS and path.is_file():
                paths.append(str(path))
    except OSError:
        error = '保存文件夹暂时无法读取。'
    _put(results, ('scan', paths, error), stop)


def file_action_worker(kind, payload, destination, results, recorder):
    outcomes, error = [], ''
    try:
        if kind == 'recycle':
            def record(result):
                outcomes.append(result)
                recorder(result)
            recycle_files(payload, on_result=record)
        else:
            outcomes = export_copies(payload, destination)
    except (OSError, ValueError, RuntimeError) as exc:
        error = str(exc)
    results.put((kind, outcomes, error))


def thumbnails(requests, results, stop, decoders, lock):
    while not stop.is_set():
        try:
            generation, key, path = requests.get(timeout=.2)
        except queue.Empty:
            continue
        try:
            decoder = Decoder(path)
        except (OSError, RuntimeError):
            _put(results, (generation, key, None), stop)
            continue
        with lock:
            if stop.is_set():
                decoder.close()
                return
            decoders.add(decoder)
        try:
            _put(results, (generation, key, decoder.frame(0)), stop)
        except (OSError, ValueError, RuntimeError, TimeoutError, subprocess.TimeoutExpired):
            _put(results, (generation, key, None), stop)
        finally:
            try:
                decoder.close()
            except OSError:
                pass
            with lock:
                decoders.discard(decoder)


class LibraryWindow(GallerySelection):
    PAGE_SIZE = 12

    def __init__(self, app, parent=None):
        self.app, self.model = app, app.library
        self.embedded = parent is not None
        self.closed = self.scanning = self.acting = False
        self.busy_paths = set()
        self._signature = None
        self._scan_pending = None
        self.columns = 3
        self._resize_timer = None
        self._focus_timer = None
        self.cards = {}
        self._selection_traces = []
        self.window = parent.winfo_toplevel() if self.embedded else tk.Toplevel(app.root)
        if not self.embedded:
            self.window.title('拾影 · 素材库')
            self.window.geometry('1000x760')
            self.window.minsize(720, 520)
            self.window.configure(bg=BG)
            if getattr(app, 'brand_image', None):
                self.window.iconphoto(False, app.brand_image)
            self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.frame = ttk.Frame(parent if self.embedded else self.window)
        self.frame.pack(fill='both', expand=True)
        self.context_menu = ContextMenu(self.frame)
        self.frame.bind('<Destroy>', self._destroyed, add='+')
        style = ttk.Style(self.window)
        style.configure('Library.TCheckbutton', background=PANEL, foreground=FG)
        style.map('Library.TCheckbutton', background=[('active', PANEL)])
        style.configure('Library.TMenubutton', background=FIELD, foreground=FG, borderwidth=0, padding=(8, 6))
        style.configure('Danger.TButton', background='#62404C', foreground='#F2C6CE')
        style.map('Danger.TButton', background=[('active', '#77505D'), ('disabled', PANEL)],
                  foreground=[('disabled', MUTED)])
        footer = ttk.Frame(self.frame, padding=(16, 8))
        footer.pack(side='bottom', fill='x')
        self.status = tk.StringVar()
        ttk.Button(footer, text='下一页', width=6, command=lambda: self.move(1)).pack(side='right')
        ttk.Button(footer, text='上一页', width=6, command=lambda: self.move(-1)).pack(side='right', padx=6)
        status_label = ttk.Label(footer, textvariable=self.status, wraplength=450, style='Muted.TLabel')
        status_label.pack(side='left', fill='x', expand=True)
        footer.bind('<Configure>', lambda e: status_label.configure(wraplength=max(180, e.width-250)))
        self.page = self.generation = 0
        self.photos, self.labels, self.selection = {}, {}, {}
        self.requests, self.results = queue.Queue(maxsize=24), queue.Queue(maxsize=24)
        self.operations = queue.Queue(maxsize=2)
        self.stop = threading.Event()
        self.decoders, self.lock = set(), threading.Lock()
        self.workers = []
        for _ in range(2):
            worker = threading.Thread(target=thumbnails, args=(self.requests, self.results, self.stop, self.decoders, self.lock), daemon=True)
            self.workers.append(worker)
            worker.start()
        shell = ttk.Frame(self.frame, padding=(16, 10))
        shell.pack(fill='both', expand=True)
        heading = ttk.Frame(shell)
        heading.pack(fill='x')
        if not self.embedded:
            ttk.Label(heading, text='素材库', font=('Microsoft YaHei UI', 18, 'bold')).pack(side='left')
        ttk.Button(heading, text='导入素材', command=self.import_files).pack(side='right')
        ttk.Button(heading, text='收集保存文件夹', command=self.scan_folder).pack(side='right', padx=8)
        ttk.Label(heading, text='收藏每一个好片段', style='Muted.TLabel').pack(side='left')
        hint = ttk.Label(shell, text='拖动空白、卡片边框或信息文字框选 · Ctrl 点选 / Shift 连选 · 拖动封面复制文件', style='Muted.TLabel')
        hint.pack(fill='x', pady=(8, 12))
        shell.bind('<Configure>', lambda e: hint.configure(wraplength=max(180, e.width-32)))
        tools = ttk.Frame(shell)
        tools.pack(fill='x', pady=(0, 12))
        self.query = tk.StringVar()
        search = ttk.Entry(tools, textvariable=self.query, width=24)
        search.pack(side='left', fill='x', expand=True)
        search.bind('<Return>', lambda _: self.render(reset=True))
        ttk.Button(tools, text='搜索', width=4, command=lambda: self.render(reset=True)).pack(side='left', padx=6)
        ttk.Button(tools, text='全选本页', width=8, command=self.select_page).pack(side='left')
        ttk.Button(tools, text='刷新', width=4, command=self.refresh).pack(side='right', padx=6)
        filters = ttk.Frame(shell)
        filters.pack(fill='x', pady=(0, 10))
        self.media_type = tk.StringVar(master=self.window, value='全部类型')
        self.source = tk.StringVar(master=self.window, value='全部来源')
        self.sort = tk.StringVar(master=self.window, value='最近保存')
        for variable, values in ((self.media_type, TYPES), (self.source, SOURCES), (self.sort, SORTS)):
            box = ttk.Combobox(filters, textvariable=variable, values=list(values), state='readonly', width=12)
            box.pack(side='left', padx=(0, 8))
            box.bind('<<ComboboxSelected>>', lambda _: self.render(reset=True))
        batch = ttk.Frame(shell, style='Card.TFrame', padding=(10, 5))
        batch.pack(fill='x', pady=(0, 10))
        self.selection_caption = tk.StringVar(value='尚未选择素材')
        ttk.Label(batch, textvariable=self.selection_caption, style='Card.TLabel').pack(side='left', padx=(0, 12))
        self.batch_buttons = []
        for label, command, style in (('导出副本', self.export_selected, 'TButton'),
                                      ('删除所选', self.delete_selected, 'Danger.TButton'),
                                      ('取消选择', self.clear_selection, 'TButton')):
            button = ttk.Button(batch, text=label, command=command, style=style, state='disabled', width=7)
            button.pack(side='right', padx=3)
            self.batch_buttons.append(button)
        wrap_controls(filters)
        wrap_controls(batch)
        self.canvas = tk.Canvas(shell, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(shell, orient='vertical', command=self.canvas.yview)
        scrollbar.pack(side='right', fill='y')
        self.canvas.pack(fill='both', expand=True)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.grid = ttk.Frame(self.canvas)
        self.content = self.canvas.create_window(0, 0, window=self.grid, anchor='nw')
        self.canvas.bind('<Configure>', self._resize)
        self.grid.bind('<Configure>', lambda _: self.canvas.configure(scrollregion=self.canvas.bbox(self.content)))
        self._wheel_id = self.window.bind('<MouseWheel>', self._wheel, add='+')
        self.setup_selection()
        self.render()
        self.timer = self.window.after(100, self.poll)

    def refresh(self):
        if self.closed:
            return
        self.app.collect_library()
        self.render()

    def _destroyed(self, event):
        if event.widget is self.frame and not self.closed:
            self.close(destroy=False)

    def _wheel(self, event):
        if str(event.widget).startswith(str(self.frame)):
            self.canvas.yview_scroll(-3 if event.delta > 0 else 3, 'units')
            return 'break'

    def _resize(self, event):
        self.canvas.itemconfigure(self.content, width=event.width)
        scale = max(1, self.window.winfo_fpixels('1i') / 96)
        minimum = max(280, round(280 * scale))
        columns = min(3, max(1, event.width // minimum))
        if columns != self.columns:
            self.columns = columns
            if self._resize_timer is not None:
                self.window.after_cancel(self._resize_timer)
            self._resize_timer = self.window.after_idle(self._resize_render)

    def _resize_render(self):
        self._resize_timer = None
        if not self.closed:
            self.render()

    def rows(self):
        return self.model.rows(self.query.get(), TYPES[self.media_type.get()], SOURCES[self.source.get()], SORTS[self.sort.get()])

    def focus_path(self, path):
        if self.closed or self.model.readonly:
            return False
        try:
            if not Path(path).is_file():
                return False
        except OSError:
            return False
        key = self.model.key(path)
        if key not in self.model.entries or self.model.entries[key]['hidden']:
            if not self.model.add(path, restore=True):
                return False
        if not self.model.save():
            return False
        self.query.set('')
        self.media_type.set('全部类型')
        self.source.set('全部来源')
        for index, (candidate, _) in enumerate(self.rows()):
            if candidate == key:
                self.page = index // self.PAGE_SIZE
                self.render(scroll_top=True)
                for candidate, selected in self.selection.items():
                    selected.set(candidate == key)
                self.status.set('已定位保存成果 · 可预览、导出或继续剪辑')
                if key in self.labels:
                    self.labels[key].focus_set()
                if self._focus_timer is not None:
                    self.window.after_cancel(self._focus_timer)
                self._focus_timer = self.window.after_idle(lambda: self._scroll_to_card(key))
                return True
        return False

    def _scroll_to_card(self, key):
        self._focus_timer = None
        if self.closed or key not in self.cards:
            return
        self.canvas.update_idletasks()
        region = self.canvas.bbox('all')
        if region and region[3] > self.canvas.winfo_height():
            self.canvas.yview_moveto(max(0, self.cards[key].winfo_y() - 8) / region[3])

    def _clear_selection_traces(self):
        for key, token in getattr(self, '_selection_traces', ()):
            if key in self.selection:
                try:
                    self.selection[key].trace_remove('write', token)
                except tk.TclError:
                    pass
        self._selection_traces = []

    def move(self, offset):
        self.clear_selection()
        count = len(self.rows())
        self.page = max(0, min(self.page + offset, max(0, (count - 1) // self.PAGE_SIZE)))
        self.render(scroll_top=True)

    def render(self, reset=False, scroll_top=False):
        if self.closed:
            return
        if getattr(self.app, 'native_drag_active', False):
            return  # Keep the gesture's source widgets stable until native completion.
        if reset:
            self.clear_selection()
            self.page = 0
            scroll_top = True
        rows = self.rows()
        self.page = min(self.page, max(0, (len(rows) - 1) // self.PAGE_SIZE))
        signature = (self.columns, self.page, self.query.get(), self.media_type.get(), self.source.get(), self.sort.get(),
                     frozenset(self.model.unconfirmed_paths),
                     tuple((key, item['name'], item['path'], item['media_type'], item['source'], item['added']) for key, item in rows))
        if signature == self._signature:
            if self.model.error:
                self.status.set(self.model.error)
            return
        self.context_menu.close()
        self.cancel_gesture(cancel_marquee=True)
        self._signature = signature
        old_scroll = self.canvas.yview()[0]
        self.generation += 1
        self._clear_selection_traces()
        self.cards.clear()
        while not self.requests.empty():
            try:
                self.requests.get_nowait()
            except queue.Empty:
                break
        for child in self.grid.winfo_children():
            child.destroy()
        self.photos.clear()
        self.labels.clear()
        for key in list(self.selection):
            if key not in self.model.entries or self.model.entries[key]['hidden']:
                del self.selection[key]
        self.status.set(self.model.error or f'{len(rows)} 个素材 · 第 {self.page + 1} 页')
        for col in range(3):
            self.grid.columnconfigure(col, weight=1 if col < self.columns else 0,
                                      uniform='cards' if col < self.columns else '')
        if not rows:
            ttk.Label(self.grid, text='这里还没有素材\n保存视频或截图后自动出现，也可以导入已有文件。', justify='center', padding=40).grid(columnspan=self.columns, sticky='ew')
        for index, (key, item) in enumerate(rows[self.page * self.PAGE_SIZE:(self.page + 1) * self.PAGE_SIZE]):
            card = tk.Frame(self.grid, bg=PANEL, highlightbackground=FIELD, highlightthickness=2)
            self.cards[key] = card
            card.grid(row=index // self.columns, column=index % self.columns, sticky='nsew', padx=5, pady=6)
            cover = tk.Label(card, text='▶\n正在生成封面', bg='#201D2D', fg=ACCENT, height=7, cursor='hand2')
            cover.pack(fill='x')
            cover.bind('<Double-1>', lambda _, p=item['path']: self.play(p))
            cover.bind('<ButtonPress-1>', lambda e, k=key: self.begin_drag(e, k))
            cover.bind('<B1-Motion>', self.drag)
            cover.bind('<ButtonRelease-1>', self.release_cover)
            self.labels[key] = cover
            if key not in self.selection:
                self.selection[key] = tk.BooleanVar(master=self.window, value=False)
            def highlight(*args, widget=card, variable=self.selection[key]):
                selected = variable.get()
                widget.configure(highlightbackground=ACCENT if selected else FIELD,
                                 highlightthickness=2)
                self.selection_changed()
            token = self.selection[key].trace_add('write', highlight)
            self._selection_traces.append((key, token))
            highlight()
            name = ttk.Checkbutton(card, text=item['name'], variable=self.selection[key], style='Library.TCheckbutton')
            name.pack(fill='x', padx=6, pady=5)
            name.bind('<ButtonPress-1>', lambda e, k=key: self.toggle_card(k, e))
            name.bind('<ButtonRelease-1>', lambda _: 'break')
            card.bind('<ButtonPress-1>', lambda e, k=key: self.begin_card_selection(e, k))
            for widget in (card, cover, name):
                widget.bind('<Enter>', lambda _, k=key: self.hover_card(k, True))
                widget.bind('<Leave>', lambda _, k=key: self.hover_card(k, False))
            def fit_name(event, widget=name, title=item['name']):
                font = tkfont.Font(root=self.window, font=('Microsoft YaHei UI', 9))
                text = title
                available = max(20, event.width - 30)
                while text and font.measure(text + ('…' if text != title else '')) > available:
                    text = text[:-1]
                widget.configure(text=text + ('…' if text != title else ''))
            name.bind('<Configure>', fit_name)
            try:
                exists = key not in self.model.unconfirmed_paths and Path(item['path']).is_file()
            except OSError:
                exists = False
            image = item['media_type'] == 'image'
            source = next((name for name, value in SOURCES.items() if value == item['source']), '导入')
            metadata = tk.Label(card, text=('截图 PNG' if image else Path(item['path']).suffix.upper().lstrip('.')) +
                     (' · ' + source if exists else ' · 文件不可用'), bg=PANEL, fg=MUTED, anchor='w')
            metadata.pack(fill='x', padx=10)
            metadata.bind('<ButtonPress-1>', lambda e, k=key: self.begin_card_selection(e, k))
            actions = tk.Frame(card, bg=PANEL)
            actions.pack(fill='x', padx=5, pady=8)
            actions.bind('<ButtonPress-1>', lambda e, k=key: self.begin_card_selection(e, k))
            ttk.Button(actions, text='预览' if image else '播放', width=4, command=lambda p=item['path']: self.play(p), state='normal' if exists else 'disabled').pack(side='left')
            if not image:
                ttk.Button(actions, text='剪辑', width=4, command=lambda p=item['path']: self.app.open_clip_editor(p), state='normal' if exists else 'disabled').pack(side='left', padx=3)
            ttk.Button(actions, text='删除', width=4, command=lambda k=key: self.delete([k]),
                       state='normal' if exists else 'disabled', style='Danger.TButton').pack(side='left', padx=3)
            more = ttk.Button(actions, text='···', width=2)
            more.configure(command=lambda k=key, widget=more: self.show_context_menu(k, anchor=widget))
            more.pack(side='right')
            self.bind_selection_widget(card)
            self.bind_context_menu(card, key)
            if exists:
                self.requests.put_nowait((self.generation, key, item['path']))
            else:
                cover.configure(text='文件不可用')
        self.canvas.yview_moveto(0 if scroll_top else old_scroll)
        for key, variable in self.selection.items():
            if key not in self.cards:
                variable.set(False)
        self.selection_changed()

    def bind_context_menu(self, widget, key):
        widget.bind('<Button-3>', lambda event, k=key: self.show_context_menu(k, event), add='+')
        for child in widget.winfo_children():
            self.bind_context_menu(child, key)

    def show_context_menu(self, key, event=None, anchor=None):
        if self.closed or getattr(self.app, 'native_drag_active', False) or key not in self.cards:
            return 'break'
        self.select_card(key, preserve_group=True)
        keys = self.selected_keys()
        single = len(keys) == 1
        item = self.model.entries[key]
        try:
            available = key not in self.model.unconfirmed_paths and Path(item['path']).is_file()
        except OSError:
            available = False
        idle = not self.acting and not self.scanning
        x = event.x_root if event is not None else anchor.winfo_rootx()
        y = event.y_root if event is not None else anchor.winfo_rooty() + anchor.winfo_height()
        self.context_menu.show(x, y, f'素材 · 已选 {len(keys)} 项', [
            dict(label='预览素材' if item['media_type'] == 'image' else '播放视频', icon='▶',
                 command=lambda: self.play(item['path']), enabled=single and available),
            dict(label='剪辑视频', icon='✂', command=lambda: self.app.open_clip_editor(item['path']),
                 enabled=single and available and item['media_type'] == 'video'),
            dict(label='修改显示名称', command=lambda: self.rename(key), enabled=single and idle),
            dict(label='打开所在文件夹', command=lambda: self.play(str(Path(item['path']).parent)), enabled=single),
            dict(separator=True),
            dict(label='导出所选副本', command=self.export_selected, enabled=bool(keys) and idle),
            dict(label='删除所选素材（移入回收站）', command=self.delete_selected,
                 enabled=bool(keys) and idle, danger=True),
            dict(label='从素材库移除（保留文件）', command=self.remove_selected, enabled=bool(keys) and idle),
        ])
        return 'break'

    def poll(self):
        if self.closed:
            return
        while True:
            try:
                generation, key, data = self.results.get_nowait()
            except queue.Empty:
                break
            if generation != self.generation or key not in self.labels:
                continue
            try:
                if data:
                    photo = tk.PhotoImage(data=data, master=self.window)
                    target = max(220, min(360, self.cards[key].winfo_width()-4))
                    factor = max(1, (photo.width() + target-1) // target, (photo.height()+179)//180)
                    photo = photo.subsample(factor, factor)
                    self.photos[key] = photo
                    self.labels[key].configure(image=photo, text='', height=180)
                else:
                    self.labels[key].configure(text='▶\n无缩略图 · 可尝试播放')
            except tk.TclError:
                self.labels[key].configure(text='▶')
        while True:
            try:
                kind, payload, error = self.operations.get_nowait()
            except queue.Empty:
                break
            if kind == 'scan':
                self._scan_pending = (payload, 0, error)
            else:
                self.acting = False
                self.busy_paths.clear()
                self.selection_changed()
                success = [item.path for item in payload if item.ok]
                if kind == 'recycle':
                    self.model.recover_recycle()
                    if success:
                        self.app.media_recycled(success)
                    self.render()
                failures = [item.error for item in payload if not item.ok]
                self.status.set(error or self.model.error or
                    (f'{len(success)} 个素材已移入回收站' if kind == 'recycle' else f'{len(success)} 个素材已导出副本') +
                    (f' · {len(failures)} 个未完成：{failures[0]}' if failures else ''))
        if self._scan_pending is not None:
            paths, index, error = self._scan_pending
            # Limit main-thread catalogue/stat work to 50 files per poll.
            for path in paths[index:index + 50]:
                self.model.add(path, source='scan')
            index += 50
            if index >= len(paths):
                self.scanning = False
                self._scan_pending = None
                self.model.save()
                self.render()
                self.status.set(error or self.model.error or f'扫描完成，发现 {len(paths)} 个素材')
            else:
                self._scan_pending = (paths, index, error)
        self.timer = self.window.after(100, self.poll)

    def play(self, path):
        try:
            if not Path(path).exists():
                raise OSError('文件不存在')
            open_local(path)
        except OSError:
            self.status.set('无法打开文件，请确认文件仍在原位置并已安装播放器。')

    def import_files(self):
        paths = filedialog.askopenfilenames(parent=self.window, title='导入素材', filetypes=[('视频与 PNG', ' '.join('*' + e for e in sorted(MEDIA_EXTENSIONS)))])
        for path in paths[:5000]:
            self.model.add(path, restore=True, source='import')
        self.model.save()
        self.render()

    def scan_folder(self):
        if self.scanning or self.acting:
            return
        self.scanning = True
        self.status.set('正在扫描保存文件夹…')
        worker = threading.Thread(target=scan_media, args=(self.app.folder.get(), self.operations, self.stop), daemon=True)
        self.workers = [item for item in self.workers if item.is_alive()]
        self.workers.append(worker)
        worker.start()

    def select_page(self):
        for key in self.labels:
            self.selection[key].set(True)
        self.selection_changed()

    def selected_keys(self):
        return [key for key, selected in self.selection.items() if selected.get() and
                key in self.cards and key in self.model.entries and not self.model.entries[key]['hidden']]

    def remove_selected(self):
        self.remove(self.selected_keys())

    def remove(self, keys):
        if self.acting:
            return
        self.model.remove(keys)
        self.render()

    def delete_selected(self):
        self.delete(self.selected_keys())

    def delete(self, keys):
        if self.acting or self.scanning or not keys:
            self.status.set('请先选择素材。' if not keys else '文件操作正在进行，请稍候。')
            return
        if len(keys) > 200:
            self.status.set('每次最多删除 200 个素材。')
            return
        identities = []
        try:
            for key in keys:
                path = self.model.entries[key]['path']
                if self.app.media_path_busy(path):
                    raise FileActionError('文件正在录制、保存、导出或剪辑，暂不能删除。')
                identities.append(capture_identity(path))
        except (OSError, ValueError, KeyError) as error:
            self.status.set(str(error))
            return
        names = '\n'.join(self.model.entries[key]['name'] for key in keys[:5])
        if not messagebox.askyesno('删除素材', f'将 {len(keys)} 个原文件移入 Windows 回收站？\n\n{names}' +
            ('\n…' if len(keys) > 5 else '') + '\n\n可以从回收站恢复。', parent=self.window):
            return
        if any(self.app.media_path_busy(item.path) for item in identities):
            self.status.set('文件开始了新的处理任务，已取消删除。')
            return
        if not self.model.begin_recycle(keys):
            self.status.set(self.model.error)
            return
        self._file_action('recycle', identities, None)

    def export_selected(self):
        keys = self.selected_keys()
        if self.acting or not keys:
            self.status.set('请先选择素材。' if not keys else '文件操作正在进行，请稍候。')
            return
        if len(keys) > 200:
            self.status.set('每次最多导出 200 个素材。')
            return
        destination = filedialog.askdirectory(parent=self.window, title='导出副本到文件夹')
        if destination:
            if any(self.app.media_path_busy(self.model.entries[key]['path']) for key in keys):
                self.status.set('文件正在处理，暂不能导出。')
                return
            self._file_action('export', [self.model.entries[key]['path'] for key in keys], destination)

    def _file_action(self, kind, payload, destination):
        self.acting = True
        self.selection_changed()
        self.busy_paths = {self.model.key(item.path if kind == 'recycle' else item) for item in payload}
        self.status.set('正在移入回收站…' if kind == 'recycle' else '正在导出副本…')
        worker = threading.Thread(target=file_action_worker,
            args=(kind, payload, destination, self.operations, self.model.record_recycle_result), daemon=True)
        self.workers = [item for item in self.workers if item.is_alive()]
        self.workers.append(worker)
        worker.start()

    def rename(self, key):
        value = simpledialog.askstring('修改素材名称', '显示名称（保留原文件名）：', initialvalue=self.model.entries[key]['name'], parent=self.window)
        if value is not None:
            try:
                self.model.rename(key, value)
                self.render()
            except ValueError as error:
                self.status.set(str(error))

    def begin_drag(self, event, key):
        if getattr(self.app, 'native_drag_active', False):
            return 'break'
        self.cancel_gesture()
        self.select_card(key, event.state, preserve_group=True)
        if event.state & 5:
            self.drag_start = None
            return 'break'
        self.drag_start = (event.x_root, event.y_root, key)

    def drag(self, event):
        if getattr(self.app, 'native_drag_active', False):
            self.drag_start = None
            return 'break'
        if self.acting:
            self.drag_start = None
            self.status.set('文件操作正在进行，请稍候再拖出。')
            return
        start = getattr(self, 'drag_start', None)
        if not start or abs(event.x_root-start[0]) + abs(event.y_root-start[1]) < 12:
            return
        self.drag_start = None
        keys = self.selected_keys()
        if start[2] not in keys:
            keys = [start[2]]
        if not keys or any(key not in self.cards or key not in self.model.entries for key in keys):
            self.cancel_gesture()
            return 'break'
        if any(self.app.media_path_busy(self.model.entries[key]['path']) for key in keys):
            self.status.set('文件正在处理，暂不能拖出。')
            return
        try:
            self.app.start_native_drag([self.model.entries[key]['path'] for key in keys], self.status)
        except (OSError, ValueError, RuntimeError) as error:
            self.status.set(str(error))

    def close(self, destroy=True):
        if getattr(self, 'closed', False):
            return
        cancel = getattr(self.app, 'cancel_native_drag', None)
        if callable(cancel):
            cancel()
        menu = getattr(self, 'context_menu', None)
        if menu is not None:
            menu.close()
        self.teardown_selection()
        self.closed = True
        self.stop.set()
        with self.lock:
            decoders = tuple(self.decoders)
        for decoder in decoders:
            try:
                decoder.close()
            except OSError:
                pass
        for timer in (getattr(self, 'timer', None), getattr(self, '_resize_timer', None), getattr(self, '_focus_timer', None)):
            if timer is not None:
                try:
                    self.window.after_cancel(timer)
                except tk.TclError:
                    pass
        if getattr(self, '_wheel_id', None):
            self.window.unbind('<MouseWheel>', self._wheel_id)
        self.photos.clear()
        self._clear_selection_traces()
        for worker in getattr(self, 'workers', ()):
            if worker.is_alive():
                worker.join(timeout=.25)
        if destroy:
            if getattr(self, 'embedded', False):
                self.frame.destroy()
            else:
                self.window.destroy()
        if self.app.library_window is self:
            self.app.library_window = None
