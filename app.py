from __future__ import annotations

import multiprocessing as mp
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid
import re
import tkinter as tk
from tkinter import ttk, filedialog
from dialogs import messagebox, simpledialog

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
    def __init__(self, root, smoke=False, state_dir=None, defer_ui=False):
        self.root = root
        self.smoke = smoke
        self.store = Store()
        self.bridge = start_bridge(self.store, 0 if smoke else 18796)
        self.jobs = {}
        self.pending = []
        self.closing = False
        from local_state import LocalState
        self.local_state = LocalState(state_dir, enabled=not smoke or state_dir is not None)
        self.storage_settings = self.local_state.load_settings()
        self.session_id = uuid.uuid4().hex
        self._storage_stamp = 0
        self._logged_statuses = {}
        self.preferences_window = None
        self.ai_collaboration_window = None
        from media_library import Library
        self.library = Library(self.local_state.data_dir, enabled=self.local_state.enabled)
        self.library_window = None
        self.tray = None
        self.capture_bar = None
        self._completion_seen = set()
        self._completion_pending = []
        self.clip_windows = []
        from clip_drafts import ClipDrafts
        self.clip_drafts = ClipDrafts(self.local_state.data_dir, enabled=self.local_state.enabled)
        self.clip_after_download = set()
        self.folder = tk.StringVar(value=str(Path.home() / ("Movies" if sys.platform == "darwin" else "Videos") / "VideoCatch"))
        if self.storage_settings.get("remember_folder") and self.storage_settings.get("folder"):
            self.folder.set(self.storage_settings["folder"])
        self.remember_folder = tk.BooleanVar(value=self.storage_settings.get("remember_folder", False))
        self.diagnostics = tk.BooleanVar(value=self.storage_settings.get("diagnostics", False))
        self.ai_on_start = tk.BooleanVar(value=self.storage_settings.get("ai_on_start", True))
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
        for receipt in self.local_state.load_receipts():
            self.store.items[receipt["id"]] = receipt
            self._completion_seen.add(receipt['id'])
        if defer_ui:
            return
        self.build_ui()
        self.finish_initialization()

    def finish_initialization(self):
        if not self.smoke or self.local_state.enabled:
            self._restore_trust()
        self.local_state.log("startup")
        if self.local_state.warnings:
            self.notice.set("本地设置或回执读取异常，已保护原文件；请打开「设置与恢复」查看。")
        self.root.protocol("WM_DELETE_WINDOW", self.request_close)
        if not self.smoke and sys.platform == 'win32':
            from tray import TrayIcon
            self.tray = TrayIcon()
            self.tray.start()
        self.collect_library()
        self.open_library(activate=False)
        self.tick()

    def collect_library(self):
        with self.store.lock:
            items = [dict(item) for item in self.store.items.values()]
        return self.library.collect(items)

    def open_library(self, path=None, activate=True):
        self.collect_library()
        if self.library_window is None:
            from library_ui import LibraryWindow
            self.library_window = LibraryWindow(self, parent=getattr(self, 'library_parent', None))
        else:
            self.library_window.refresh()
        self.show_page('library')
        if path:
            if self.library_window.focus_path(path) is False:
                raise RuntimeError('文件已保存，但素材目录尚未完成收集')
        if activate:
            self.show_main()

    def show_page(self, name):
        pages = getattr(self, 'pages', {})
        if name not in pages:
            return
        for menu_name in ('media_menu', 'task_menu'):
            menu = getattr(self, menu_name, None)
            if menu is not None:
                menu.close()
        if getattr(self, 'current_page', None) != name:
            self.cancel_native_drag()
        gallery = getattr(self, 'library_window', None)
        if gallery is not None and name != 'library':
            menu = getattr(gallery, 'context_menu', None)
            if menu is not None:
                menu.close()
            gallery.cancel_gesture()
            self.cancel_native_drag()
        self._media_drag_start = None
        pages[name].tkraise()
        self.current_page = name
        self.page_title.set({'library': '素材库', 'browser': '网页采集', 'tasks': '任务与导出'}[name])
        for key, button in self.navigation_buttons.items() if hasattr(self, 'navigation_buttons') else ():
            button.configure(style='Accent.TButton' if key == name else 'TButton')

    def focus_link_entry(self):
        self.show_page('browser')
        self.url.focus_set()

    @property
    def native_drag_active(self):
        session = getattr(self, '_native_drag_session', None)
        return bool(session and session.busy)

    def start_native_drag(self, paths, feedback=None):
        from native_drag import NativeFileDrag
        if self.native_drag_active:
            return False
        session = NativeFileDrag()
        self._native_drag_session = session
        try:
            session.start(paths)
        except (OSError, ValueError, RuntimeError) as error:
            (feedback or self.notice).set(str(error))
            self._native_drag_session = None
            return False
        self._native_drag_feedback = feedback or self.notice
        self._native_drag_feedback.set('拖到桌面或文件夹复制；Esc 取消。')
        gallery = getattr(self, 'library_window', None)
        if gallery is not None:
            gallery.cancel_gesture()
            gallery.selection_changed()
        self._native_drag_timer = self.root.after(30, self.poll_native_drag)
        return True

    def cancel_native_drag(self):
        if self.native_drag_active:
            self._native_drag_session.cancel()

    def poll_native_drag(self):
        self._native_drag_timer = None
        session = getattr(self, '_native_drag_session', None)
        if session is None:
            return
        result = session.poll()
        if result is None:
            self._native_drag_timer = self.root.after(30, self.poll_native_drag)
            return
        copied, error = result
        self._native_drag_feedback.set(error or ('复制请求已交给目标文件夹。' if copied else '已取消拖出，原文件保留。'))
        self._native_drag_session = None
        gallery = getattr(self, 'library_window', None)
        if gallery is not None and not gallery.closed:
            gallery.cancel_gesture()
            gallery.selection_changed()
            gallery.refresh()
        if getattr(self, '_exit_after_drag', False):
            self._exit_after_drag = False
            self.close()

    def create_ai_menu(self):
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_checkbutton(label='允许本机 AI 协作', variable=self.ai_enabled, command=self.toggle_ai)
        menu.add_command(label='开启协作并复制使用指引', command=self.copy_ai_collaboration)
        menu.add_command(label='复制本次配对码', command=self.copy_pairing)
        menu.add_command(label='权限与启动设置', command=self.open_preferences)
        return menu

    def open_task_result(self):
        selected = self.task_tree.selection()
        if len(selected) != 1:
            self.notice.set('请选择一个任务。')
            return
        item = self.store.items.get(selected[0], {})
        if item.get('status') == '已保存' and item.get('path'):
            if not Path(item['path']).is_file():
                self.notice.set('成果文件已被移动或删除，请检查保存文件夹。')
                return
            try:
                self.open_library(item['path'])
            except (OSError, ValueError, RuntimeError) as error:
                self.notice.set(f'无法打开成果：{error}')
        else:
            self.notice.set(item.get('error') or '任务尚未产生可用文件。')

    def cancel_task_selection(self):
        selected = self.task_tree.selection()
        cancellable = []
        for ident in selected:
            if self.store.items.get(ident, {}).get('status') in BUSY:
                cancellable.append(ident)
                self.kill_job(ident)
        if not cancellable:
            self.notice.set('所选任务已经结束，无需取消。' if selected else '请先选择正在执行的任务。')
        self.persist_receipts()
        self.refresh()

    def remove_task_records(self, keys=None):
        self.remove_records(self.task_tree.selection() if keys is None else keys)

    def remove_records(self, keys):
        """Commit receipts first: failed persistence never hides visible history."""
        keys = set(keys)
        with self.store.lock:
            removed = {ident for ident in keys if ident in self.store.items and
                       self.store.items[ident].get('status') not in BUSY and ident not in self.jobs}
            skipped = len(keys & self.store.items.keys()) - len(removed)
            if not removed:
                self.notice.set('正在执行的任务不能删除记录，请先停止或等待完成。' if skipped else '请选择要删除的任务记录。')
                return False
            remaining = [item for ident, item in self.store.items.items() if ident not in removed]
            try:
                saved = self.local_state.save_receipts(remaining)
            except (OSError, ValueError, TypeError, RuntimeError):
                saved = False
            if not saved:
                self.notice.set('删除记录未能保存，原记录已保留。请检查本机设置目录后重试。')
                return False
            for ident in removed:
                self.store.items.pop(ident, None)
                self.clip_after_download.discard(ident)
                self._logged_statuses.pop(ident, None)
        self.refresh()
        self.notice.set(f'已删除 {len(removed)} 条任务记录，素材库和原文件保留。' +
                        (f' {skipped} 条执行中的任务已保留。' if skipped else ''))
        return True

    def update_task_actions(self, _event=None):
        if not hasattr(self, 'task_buttons'):
            return
        selected = [self.store.items[key] for key in self.task_tree.selection() if key in self.store.items]
        finished = [item for item in selected if item['status'] not in BUSY and item['id'] not in self.jobs]
        available = len(selected) == 1 and selected[0]['status'] == '已保存' and bool(selected[0].get('path'))
        for name, enabled in (('open', available), ('cancel', any(item['status'] in BUSY for item in selected)),
                              ('delete', bool(finished))):
            self.task_buttons[name].configure(state='normal' if enabled else 'disabled')
        self.task_selection_text.set(f'已选 {len(selected)} 项 · 可删除 {len(finished)} 条记录' if selected else '选择任务查看成果，或删除已结束的记录')

    def task_context_menu(self, event):
        ident = self.task_tree.identify_row(event.y)
        if ident and ident not in self.task_tree.selection():
            self.task_tree.selection_set(ident)
        self.update_task_actions()
        self.task_menu.show(event.x_root, event.y_root, f'任务 · 已选 {len(self.task_tree.selection())} 项', [
            dict(label=label, command=command, enabled=str(self.task_buttons[name].cget('state')) != 'disabled', danger=name == 'delete')
            for name, label, command in (('open', '查看成果', self.open_task_result),
                                         ('cancel', '停止 / 取消任务', self.cancel_task_selection),
                                         ('delete', '删除记录（保留原文件）', self.remove_task_records))])
        return 'break'

    def task_action_press(self, event):
        ident = self.task_tree.identify_row(event.y)
        self._task_delete_press = (ident, event.x, event.y) if ident and self.task_tree.identify_column(event.x) == '#5' else None

    def task_action_release(self, event):
        start = getattr(self, '_task_delete_press', None)
        self._task_delete_press = None
        if start and abs(event.x-start[1]) + abs(event.y-start[2]) < 6 and self.task_tree.identify_row(event.y) == start[0] and self.task_tree.identify_column(event.x) == '#5':
            self.remove_task_records([start[0]])
            return 'break'

    def file_action_busy(self, path):
        gallery = getattr(self, 'library_window', None)
        return bool(gallery and gallery.model.key(path) in gallery.busy_paths)

    def media_path_busy(self, path):
        if self.native_drag_active:
            target = Path(path).resolve()
            if any(Path(source) == target for source in self._native_drag_session.paths):
                return True
        if self.file_action_busy(path):
            return True
        def same(other):
            try:
                return other and os.path.normcase(str(Path(other).resolve())) == os.path.normcase(str(Path(path).resolve()))
            except OSError:
                return False
        if any(not getattr(editor, 'closed', False) and same(editor.source) for editor in self.clip_windows):
            return True
        with self.store.lock:
            return any(item.get('status') in BUSY and (same(item.get('path')) or same(item.get('source_path')))
                       for item in self.store.items.values())

    def media_recycled(self, paths):
        keys = {os.path.normcase(str(Path(path).resolve())) for path in paths}
        with self.store.lock:
            for item in self.store.items.values():
                path = item.get('path')
                if path and os.path.normcase(str(Path(path).resolve())) in keys:
                    item.update(status='已取消', progress='原文件已移入回收站', error='原文件已移入回收站')
        self.persist_receipts()
        self.refresh()

    def queue_completed_media(self, ident, path):
        if not hasattr(self, '_completion_seen') or ident in self._completion_seen or not path:
            return
        try:
            if not Path(path).is_file() or Path(path).stat().st_size == 0:
                return
        except OSError:
            return
        self._completion_seen.add(ident)
        if not self.exit_after_capture:
            self._completion_pending.append(path)

    def present_completed_media(self):
        if self.native_drag_active:
            return
        pending = getattr(self, '_completion_pending', [])
        if not pending or self.exit_after_capture or self.recorder.is_busy or self.screenshot_pending:
            return
        for control in (getattr(self, 'recording_panel', None), getattr(self, 'capture_bar', None)):
            if (getattr(control, 'selector', None) is not None or getattr(control, '_countdown', None) is not None
                    or getattr(control, '_screenshot_timer', None) is not None):
                return
        if time.monotonic() < getattr(self, '_completion_retry_at', 0):
            return
        path, count = pending[-1], len(pending)
        if getattr(self, 'capture_bar', None) is not None:
            self.capture_bar.close()
            self.capture_bar = None
        try:
            self.open_library(path)
        except Exception:
            self._completion_retry_at = time.monotonic() + 3
            self.notice.set('文件已保存，素材库展示暂未完成，正在重试。')
            return
        del pending[:count]
        self._completion_retry_at = 0
        self.notice.set('保存完成，已选中新素材，可直接查看、剪辑或删除。')

    def show_main(self):
        self.root.deiconify()
        self.root.lift()

    def request_close(self):
        self.cancel_native_drag()
        if self.tray is not None and self.tray.active:
            self.notice.set('拾影仍在系统托盘运行；右键托盘图标可以打开素材库或退出。')
            self.root.withdraw()
        else:
            self.close()

    def poll_tray(self):
        tray = getattr(self, 'tray', None)
        if tray is None:
            return
        while True:
            try:
                event = tray.events.get_nowait()
            except queue.Empty:
                return
            if event in {'show', 'unavailable'}:
                self.show_main()
            elif event == 'library':
                self.open_library()
            elif event == 'exit':
                self.show_main()
                self.close()
                return

    def _save_pairing(self):
        if self.local_state.enabled:
            save_pairing(self.bridge.token, self.bridge.server_port, self.local_state.data_dir / "ai-client.json")
        else:
            save_pairing(self.bridge.token, self.bridge.server_port)

    def _restore_trust(self):
        until = self.storage_settings.get("trust_until", 0)
        if self.storage_settings.get("ai_on_start", True) and until:
            self.storage_settings["trust_until"] = until = 0
            self.local_state.save_settings({"trust_until": 0})
        if self.storage_settings.get("ai_on_start", True) or time.time() < until <= time.time() + 86400:
            try:
                self._save_pairing()
                self.ai.enabled.set()
                self.ai_enabled.set(True)
                self.notice.set("AI 协作已开启，本次配对码已轮换；可在设置中关闭启动时自动开启。")
            except (OSError, ValueError):
                self.notice.set("自动协作未恢复：本机配对设置无法更新，请手动重新开启。")
        elif until:
            self.storage_settings["trust_until"] = 0
            self.local_state.save_settings(self.storage_settings)

    def save_preferences(self):
        candidate = dict(self.storage_settings, remember_folder=self.remember_folder.get(),
            folder=self.folder.get().strip() if self.remember_folder.get() else "", diagnostics=self.diagnostics.get())
        if hasattr(self, "ai_on_start"):
            candidate["ai_on_start"] = self.ai_on_start.get()
            if candidate["ai_on_start"]:
                candidate["trust_until"] = 0
        if not self.local_state.save_settings(candidate):
            self.notice.set("设置未能保存；请检查输入格式和设置目录权限，原设置保持不变。")
            return False
        self.storage_settings = candidate
        self.notice.set("设置已保存。诊断仅记录事件和状态，不记录链接、令牌或私人路径。")
        return True

    def grant_trust(self):
        if not messagebox.askyesno("授权本机客户端 24 小时", "授权后，未来 24 小时内启动拾影会自动开启 AI 接口并更新本机配对文件。能以当前账户读取该文件的本机程序可调用接口；这不是对某个进程身份的认证。仍仅监听回环、每次启动换码，可随时撤销。是否授权？", parent=self.root):
            return
        previous = dict(self.storage_settings)
        self.storage_settings["trust_until"] = time.time() + 86400
        self.storage_settings["ai_on_start"] = False
        try:
            self._save_pairing()
            if not self.local_state.save_settings(self.storage_settings):
                raise OSError()
        except (OSError, ValueError):
            self.storage_settings = previous
            self.notice.set("限期授权未保存，请检查设置目录权限后重试。")
            return
        self.ai_enabled.set(True)
        if hasattr(self, "ai_on_start"):
            self.ai_on_start.set(False)
        self.ai.enabled.set()
        self.local_state.log("trust_granted")
        self.notice.set("已授权本机客户端 24 小时；可在设置中撤销，或取消「允许 AI 协作」。")

    def revoke_trust(self):
        self.storage_settings["trust_until"] = 0
        self.storage_settings["ai_on_start"] = False
        if hasattr(self, "ai_on_start"):
            self.ai_on_start.set(False)
        saved = self.local_state.save_settings({"trust_until": 0, "ai_on_start": False})
        self.ai.enabled.clear()
        self.ai_enabled.set(False)
        self.local_state.log("trust_revoked")
        self.notice.set("已撤销重启恢复权限并关闭当前 AI 协作。" if saved else "当前 AI 协作已关闭，但撤销未能写入设置；请修复目录权限后再次撤销。")
        window = getattr(self, "preferences_window", None)
        if window is not None and window.winfo_exists():
            window.refresh_status()

    def persist_receipts(self):
        with self.store.lock:
            items = list(self.store.items.values())
            result = self.local_state.save_receipts(items)
        for item in items:
            if self._logged_statuses.get(item["id"]) != item["status"]:
                self.local_state.log("task", operation=item.get("operation", "download"), status=item["status"])
        self._logged_statuses = {item["id"]: item["status"] for item in items}
        if not result:
            self.notice.set("任务仍在执行，但本地回执未能保存；退出前请确认实际文件。")
        return result

    def open_ai_collaboration(self):
        from ai_collaboration_ui import show_collaboration
        return show_collaboration(self)

    def open_preferences(self, section=None):
        from preferences_ui import show_preferences
        return show_preferences(self, section=section)

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
            self.revoke_trust()

    def copy_ai_collaboration(self):
        try:
            prompt = collaboration_prompt()
            self._save_pairing()
            self.root.clipboard_clear()
            self.root.clipboard_append(prompt)
        except (OSError, ValueError, tk.TclError) as error:
            self.notice.set(f"协作指引未复制成功：{error}；请重试。")
            return
        self.ai_enabled.set(True)
        self.ai.enabled.set()
        self.notice.set("协作已开启，指引已复制。粘贴给能执行本机命令的 AI，再描述要下载或截取的视频。")

    def open_recording(self):
        self.open_capture('record')

    def open_screenshot(self):
        self.open_capture('screenshot')

    def open_capture(self, mode):
        from capture_bar import CaptureBar
        if self.capture_bar is None:
            self.capture_bar = CaptureBar(self, mode=mode)
        self.capture_bar.show(mode)

    def open_capture_settings(self):
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
        if not Path(value).expanduser().is_absolute():
            raise ValueError("保存目录须为绝对路径")
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
                elif getattr(self, "capture_bar", None) is not None:
                    self.capture_bar.start()
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
                if getattr(self, "capture_bar", None) is not None:
                    self.capture_bar.screenshot()
                elif self.recording_panel is None:
                    self.open_screenshot()
                    self.notice.set("先在截图工具条选择范围，再按截图快捷键。")
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
                if result.get("status") == "失败":
                    self._cancel_failed_capture_exit()
                self.store.update(ident, **{k: result.get(k, "") for k in ("status", "path", "error")},
                                  progress="100%" if result.get("status") == "已保存" else "截图未完成")
                self.notice.set("截图已保存：" + result["path"] if result.get("status") == "已保存" else "截图失败：" + result.get("error", "未知错误"))
                if result.get('status') == '已保存':
                    self.queue_completed_media(ident, result.get('path'))
        snapshot = self.recorder.snapshot()
        ident = snapshot.get("id")
        if ident:
            elapsed = max(0, int(snapshot.get("elapsed", 0)))
            self.store.update(ident, **{k: snapshot.get(k, "") for k in ("status", "path", "error")},
                elapsed=elapsed, progress=f"{elapsed // 3600:02}:{elapsed // 60 % 60:02}:{elapsed % 60:02}")
            if snapshot.get("status") in {"已保存", "失败", "已取消"} and self.recording_notice_id != ident:
                self.recording_notice_id = ident
                if snapshot["status"] == "失败":
                    self._cancel_failed_capture_exit()
                if snapshot["status"] == "已取消":
                    self.notice.set("已取消录制，尚未产生可保存的画面。")
                else:
                    self.notice.set("录制已保存：" + snapshot.get("path", "") if snapshot["status"] == "已保存" else "录制失败：" + snapshot.get("error", ""))
                    if snapshot['status'] == '已保存':
                        self.queue_completed_media(ident, snapshot.get('path'))
        if self.recording_panel is not None:
            self.recording_panel.refresh()
        if getattr(self, 'capture_bar', None) is not None:
            self.capture_bar.refresh()
        if self.recording_toolbar is not None:
            self.recording_toolbar.refresh()
            if self.annotations is not None and self.annotations.active:
                self._raise_recording_controls()

    def _cancel_failed_capture_exit(self):
        if self.exit_after_capture:
            self.exit_after_capture = False
            if self.ai_enabled.get():
                self.ai.enabled.set()
            self.root.deiconify()

    def clip_dialog(self):
        source = filedialog.askopenfilename(parent=self.root, title="选择要剪辑的本地视频", filetypes=[("视频", "*.mp4 *.mkv *.mov *.webm *.avi *.m4v *.flv *.ts")])
        if source:
            self.open_clip_editor(source)

    def open_clip_editor(self, source):
        if self.file_action_busy(source):
            self.notice.set("该素材正在删除或导出副本，请稍候。")
            return
        path = Path(source)
        if not path.is_file():
            self.notice.set("视频文件已移动或不存在，请重新选择本地视频。")
            return
        from clip_ui import ClipEditor
        for editor in self.clip_windows:
            if editor.source == str(path):
                editor.window.deiconify()
                editor.window.lift()
                return
        self.clip_windows.append(ClipEditor(self, path))

    def clip_selected(self):
        selected = self.media.selection()
        if len(selected) != 1:
            self.notice.set("请先选择一个视频，再点击「剪辑所选视频」。")
            return
        ident = selected[0]
        item = self.store.items.get(ident)
        if not item:
            return
        if item.get("operation") == "screenshot":
            self.notice.set("截图是图片，不能按视频时间段剪辑。")
            return
        if item.get("status") == "已保存" and item.get("path"):
            self.open_clip_editor(item["path"])
            return
        if item.get("history"):
            self.notice.set("历史任务没有可用视频文件，请重新导入或选择本地视频。")
            return
        try:
            if item.get("status") not in BUSY:
                if item.get("operation") in {"record", "clip"}:
                    raise ValueError("请先完成视频保存，再进行剪辑。")
                self.ai.dispatch("download", {"id": ident})
                self.persist_receipts()
            self.clip_after_download.add(ident)
            self.notice.set("视频保存完成后将自动打开剪辑页，可在任务列表取消下载。")
        except (ValueError, OSError, ApiError) as error:
            self.notice.set("暂时无法剪辑：" + str(error))

    def poll_clip_sources(self):
        for ident in tuple(self.clip_after_download):
            item = self.store.items.get(ident)
            if not item or item.get("status") in {"失败", "已取消", "已中断"}:
                self.clip_after_download.discard(ident)
                self.notice.set("视频尚未保存成功，剪辑页未打开；请查看任务状态后重试。")
            elif item.get("status") == "已保存":
                self.clip_after_download.discard(ident)
                self.open_clip_editor(item.get("path", ""))

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
            if not self.folder.get().strip():
                raise ValueError("请选择保存目录")
            p = Path(self.folder.get()).expanduser().resolve()
            p.mkdir(parents=True, exist_ok=True)
            open_local(p)
        except (OSError, ValueError) as error:
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
        added = 0
        with self.store.lock:
            for ident in selected:
                item = self.store.items.get(ident)
                if item and not item.get("history") and item.get("operation") not in {"clip", "record", "screenshot"} and item["status"] not in BUSY | {"已保存"}:
                    self.store.update(ident, status="排队中", progress="等待下载", error="")
                    self.pending.append((dict(item, proxy=self.proxy.get()), str(folder)))
                    added += 1
        self.notice.set(f"已将 {added} 个视频加入下载队列，最多同时下载 2 个视频。" if added else
                        "所选记录没有可下载的视频。已保存或正在执行的任务不会重复下载；本地裁剪、录制和截图请使用对应入口。")
        self.persist_receipts()

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
        self.remove_records(self.media.selection())

    def rename_selected(self):
        selected = self.media.selection()
        if not selected:
            self.notice.set("请先选择素材后修改名称。")
            return
        with self.store.lock:
            if any(ident not in self.store.items for ident in selected):
                self.notice.set('记录已不存在，请刷新后重试。')
                return
            initial = self.store.items[selected[0]]["title"] if len(selected) == 1 else '素材_{n}'
        batch = len(selected) > 1
        prompt = (f'为所选 {len(selected)} 项修改显示名称（不改动原文件）：\n'
                  '可用 {name} 保留当前名称、{n} 编号，如 素材_{n} 或 {name}_{n}。') if batch else '修改列表显示名称（不改动原文件）：'
        value = simpledialog.askstring('批量修改名称' if batch else '修改素材名称', prompt,
                                       initialvalue=initial, parent=self.root)
        if value is None:
            return
        if any(c in value for c in '\r\n\x00'):
            self.notice.set('每项名称须为 1～120 个字符，不能包含换行。全部名称均未修改。')
            return
        value = value.strip()
        with self.store.lock:
            if any(ident not in self.store.items for ident in selected):
                self.notice.set('记录已不存在，请刷新后重试。')
                return
            try:
                names = self.media_display_names(value, [self.store.items[ident]['title'] for ident in selected], batch=batch)
            except ValueError as error:
                self.notice.set(str(error))
                return
            updated = {ident: dict(self.store.items[ident], title=name, display_name=name)
                       for ident, name in zip(selected, names)}
            remaining = [updated.get(key, dict(item)) for key, item in self.store.items.items()]
            try:
                saved = self.local_state.save_receipts(remaining)
            except (OSError, ValueError, TypeError, RuntimeError):
                saved = False
            if not saved:
                self.notice.set('名称未能保存，原名称已保留。')
                return
            self.store.items.update(updated)
        self.refresh()
        self.notice.set(f'已修改 {len(selected)} 项素材显示名称，原文件名保持不变。' if batch else '素材显示名称已修改，原文件名保持不变。')

    @staticmethod
    def media_display_names(pattern, names, batch=True):
        """Substitute only closed-vocabulary tokens, never execute format fields."""
        if any(c in pattern for c in '\r\n\x00'):
            raise ValueError('每项名称须为 1～120 个字符，不能包含换行。全部名称均未修改。')
        if batch and ('{' in re.sub(r'\{(?:name|n)\}', '', pattern) or '}' in re.sub(r'\{(?:name|n)\}', '', pattern)):
            raise ValueError('命名模式只支持 {name} 和 {n}，请检查花括号与占位符。')
        result = [re.sub(r'\{(name|n)\}', lambda match: name if match[1] == 'name' else str(index), pattern)
                  if batch else pattern for index, name in enumerate(names, 1)]
        if any(not value.strip() or len(value.strip()) > 120 or any(c in value for c in '\r\n\x00') for value in result):
            raise ValueError('每项名称须为 1～120 个字符，不能包含换行。全部名称均未修改。')
        return [value.strip() for value in result]

    def select_all_media(self, _event=None):
        rows = self.media.get_children()
        self.media.selection_set(rows)
        if rows and not self.media.focus():
            self.media.focus(rows[0])
        self.update_media_actions()
        return 'break'

    def clear_media_selection(self, _event=None):
        self.media.selection_remove(self.media.selection())
        self._media_selection_anchor = None
        self._media_drag_start = None
        self.update_media_actions()
        return 'break'

    def media_action_states(self):
        selected = [self.store.items[ident] for ident in self.media.selection() if ident in self.store.items]
        return {'save': any(not item.get('history') and item.get('operation') not in {'clip', 'record', 'screenshot'} and
                            item.get('status') not in BUSY | {'已保存'} for item in selected),
                'clip': len(selected) == 1 and selected[0].get('operation') != 'screenshot',
                'rename': bool(selected), 'cancel': any(item.get('status') in BUSY for item in selected),
                'delete': any(item.get('status') not in BUSY and item['id'] not in self.jobs for item in selected),
                'all': bool(self.media.get_children()), 'none': bool(selected)}

    def update_media_actions(self, _event=None):
        self.show_detail()
        if not hasattr(self, 'media_buttons'):
            return
        states = self.media_action_states()
        for name, button in self.media_buttons.items():
            button.configure(state='normal' if states[name] else 'disabled')
        self.media_selection_text.set(f'已选 {len(self.media.selection())} 项 · Ctrl / Shift 多选')

    def media_context_menu(self, event):
        row = self.media.identify_row(event.y)
        if row and row not in self.media.selection():
            self.media.selection_set(row)
            self.media.focus(row)
            self._media_selection_anchor = row
        self.update_media_actions()
        states = self.media_action_states()
        self.media_menu.show(event.x_root, event.y_root, f'网页素材 · 已选 {len(self.media.selection())} 项', [
            dict(label=label, command=command, enabled=states[name], shortcut=shortcut, danger=name == 'delete')
            for name, label, command, shortcut in (
                ('save', '保存所选视频', self.enqueue, ''), ('clip', '剪辑视频（请选择一项）', self.clip_selected, ''),
                ('rename', '修改所选显示名称', self.rename_selected, 'F2'),
                ('cancel', '取消所选任务', self.cancel_selected, ''),
                ('delete', '移除所选记录（保留文件）', self.clear_selected, 'Delete'),
                ('all', '全选', self.select_all_media, 'Ctrl+A'), ('none', '取消选择', self.clear_media_selection, 'Esc'))])
        return 'break'

    def begin_media_drag(self, event):
        if self.native_drag_active:
            return 'break'
        row = self.media.identify_row(event.y)
        self._media_drag_start = None
        if self.media.identify_region(event.x, event.y) in {'heading', 'separator'}:
            return
        if not row:
            if not event.state & 5:
                self.clear_media_selection()
            return 'break'
        self.media.focus_set()
        selected = self.media.selection()
        anchor = getattr(self, '_media_selection_anchor', None)
        if event.state & 1:
            rows = self.media.get_children()
            anchor = anchor if anchor in rows else (self.media.focus() or row)
            start, end = sorted((rows.index(anchor), rows.index(row)))
            if event.state & 4:
                self.media.selection_add(rows[start:end+1])
            else:
                self.media.selection_set(rows[start:end+1])
            self._media_selection_anchor = anchor
        elif event.state & 4:
            (self.media.selection_remove if row in selected else self.media.selection_add)(row)
            self._media_selection_anchor = row
        else:
            if row not in selected:
                self.media.selection_set(row)
            self._media_selection_anchor = row
            self._media_drag_start = (event.x_root, event.y_root, row)
        self.media.focus(row)
        self.update_media_actions()
        return 'break'  # One selection owner avoids conflicting Tk class anchors.

    def release_media_drag(self, event):
        start = getattr(self, '_media_drag_start', None)
        self._media_drag_start = None
        if start and not self.native_drag_active and not event.state & 5 and self.media.exists(start[2]):
            self.media.selection_set(start[2])
            self.update_media_actions()
        return 'break' if start else None

    def drag_media(self, event):
        if self.native_drag_active:
            self._media_drag_start = None
            return 'break'
        start = getattr(self, "_media_drag_start", None)
        if not start or abs(event.x_root - start[0]) + abs(event.y_root - start[1]) < 12:
            return
        self._media_drag_start = None
        selected = self.media.selection()
        if start[2] not in selected:
            selected = (start[2],)
            self.media.selection_set(selected)
        items = [self.store.items.get(ident, {}) for ident in selected]
        paths = [item.get("path", "") for item in items]
        if any(item.get("status") != "已保存" or not path or not Path(path).is_file() for item, path in zip(items, paths)):
            self.notice.set("请先保存视频，再拖到桌面或文件夹复制；未完成的素材不能拖出。")
            return "break"
        try:
            self.start_native_drag(paths)
        except (OSError, ValueError, RuntimeError) as error:
            self.notice.set(str(error))
        return "break"

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
        watching = sum(t["watching"] and t.get("online", True) for t in tabs)
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
        if hasattr(self, 'task_tree'):
            self.reconcile(self.task_tree, [(i['id'], tuple(i[k] for k in ('title', 'kind', 'status', 'progress')) +
                                            ('执行中' if i['status'] in BUSY or i['id'] in self.jobs else '删除记录',))
                                           for i in items if i['status'] != '待保存'])
            self.update_task_actions()
        from ui import refresh_ui
        refresh_ui(self, tabs, items, count)
        self.update_media_actions()

    def tick(self):
        if self.closing:
            return
        try:
            self._tick_once()
        except Exception as error:
            # One failing callback must not permanently stop queue polling.
            self.notice.set("任务刷新遇到异常，正在继续重试。请检查任务详情；已完成的文件保留。")
            if hasattr(self, "local_state"):
                self.local_state.log("refresh_error", error_class=type(error).__name__)
        finally:
            if not self.closing:
                self.tick_timer = self.root.after(500, self.tick)

    def _tick_once(self):
        self.poll_tray()
        if self.closing:
            return
        until = self.storage_settings.get("trust_until", 0)
        if until and time.time() >= until:
            self.revoke_trust()
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
                        if fields.get('status') == '已保存' and self.store.items.get(ident, {}).get('operation') == 'clip':
                            self.queue_completed_media(ident, fields.get('path'))
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
        self.poll_clip_sources()
        self.present_completed_media()
        self.refresh()
        if time.monotonic() - self._storage_stamp >= 2:
            self._storage_stamp = time.monotonic()
            self.persist_receipts()
            if getattr(self, 'library', None) is not None and self.collect_library() and self.library_window is not None:
                self.library_window.refresh()
            if self.remember_folder.get() and self.storage_settings.get("folder") != self.folder.get().strip():
                self.save_preferences()

    def close(self):
        if self.native_drag_active:
            self._exit_after_drag = True
            self.cancel_native_drag()
            self.notice.set('正在结束文件拖放，随后退出拾影。')
            return
        if getattr(getattr(self, 'library_window', None), 'acting', False):
            self.show_main()
            self.notice.set('文件操作正在完成，请稍候再退出。')
            return
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
                if self.ai_enabled.get():
                    self.ai.enabled.set()
                self.notice.set("已取消退出，任务继续执行。")
                return
        decisions = []
        for editor in list(self.clip_windows):
            choice = editor.close_decision()
            if choice is None:
                self.exit_after_capture = False
                if self.ai_enabled.get():
                    self.ai.enabled.set()
                return
            decisions.append((editor, choice))
        if decisions:
            changes = [(editor.source, editor.draft_value(choice)) for editor, choice in decisions
                       if editor.timeline is not None]
            if not self.clip_drafts.save_many(changes):
                self.exit_after_capture = False
                if self.ai_enabled.get():
                    self.ai.enabled.set()
                messagebox.showerror('退出已取消', '剪辑草稿未能保存，所有编辑窗口与选区均已保留。', parent=self.root)
                return
            for editor, choice in decisions:
                editor.close(prompt=False, decision=choice, prepared=True)
        self.closing = True
        for name in ('media_menu', 'task_menu'):
            menu = getattr(self, name, None)
            if menu is not None:
                menu.close()
        for name in ('ai_collaboration_window', 'preferences_window'):
            window = getattr(self, name, None)
            if window is not None:
                window.close()
        if getattr(self, 'capture_bar', None) is not None:
            self.capture_bar.close()
        if getattr(self, 'library_window', None) is not None:
            self.library_window.close()
        if getattr(self, 'tray', None) is not None:
            self.tray.close()
        for editor in list(self.clip_windows):
            editor.close()
        self.clip_after_download.clear()
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
        self.persist_receipts()
        self.save_preferences()
        self.local_state.log("shutdown")
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
    state_dir = None
    if "--data-dir" in sys.argv:
        index = sys.argv.index("--data-dir")
        if index + 1 >= len(sys.argv):
            raise SystemExit("--data-dir 需要目录")
        state_dir = str(Path(sys.argv[index + 1]).expanduser().resolve())
        os.environ["VIDEOCATCH_DATA_DIR"] = state_dir
        del sys.argv[index:index + 2]
    enable_dpi_awareness()
    root = tk.Tk()
    try:
        captured_check = "--verify-capture-json" in sys.argv
        clip_check = "--verify-clip-json" in sys.argv
        verification = "--verify-download" in sys.argv or captured_check or clip_check
        recording_check = "--verify-recording-json" in sys.argv
        library_check = "--verify-library-json" in sys.argv
        surface_check = '--verify-surfaces-json' in sys.argv
        if surface_check:
            if state_dir is not None:
                raise ValueError('界面验证不允许使用持久目录')
            root.withdraw()
        app = App(root, smoke="--smoke-test" in sys.argv or verification or recording_check or library_check or surface_check, state_dir=state_dir)
        if surface_check:
            # Own offscreen fixture only: no permissions/clipboard/user catalogue.
            root.geometry('1020x720+12000+12000')
            root.deiconify()
            def check_surfaces():
                from dialogs import _Dialog
                from version import VERSION
                ai_before = app.ai_enabled.get()
                panel = app.open_ai_collaboration()
                settings = app.open_preferences(section='AI 协作')
                root.update_idletasks()
                reused = app.open_ai_collaboration() is panel
                confirmation = _Dialog(root, '界面检查', '生成的本机检查正文。', 'yesno')
                confirmation.cancel()
                confirmation._cleanup()
                result = {'version': VERSION, 'ai_panel': panel.title(),
                          'panel_reused': reused, 'settings': settings.title(),
                          'ai_unchanged': ai_before == app.ai_enabled.get(),
                          'cancel_result': confirmation.result,
                          'grab_released': root.grab_current() is None,
                          'private_state_disabled': not app.local_state.enabled}
                panel.close()
                settings.close()
                result['closed_references'] = app.ai_collaboration_window is None and app.preferences_window is None
                index = sys.argv.index('--verify-surfaces-json')
                Path(sys.argv[index + 1]).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
                app.close()
            root.after(100, check_surfaces)
        if library_check:
            # Isolated frozen-package check; never reads the user's catalogue.
            index = sys.argv.index('--verify-library-json')
            fixture, report = sys.argv[index + 1:index + 3]
            if state_dir is not None:
                raise ValueError('素材库验证不允许使用持久目录')
            app.library.add(fixture)
            app.open_library()
            deadline = time.monotonic() + 25
            def check_library():
                gallery = app.library_window
                if not gallery.photos and time.monotonic() < deadline:
                    root.after(100, check_library)
                    return
                from version import VERSION
                result = {'version': VERSION, 'cards': len(gallery.labels), 'thumbnails': len(gallery.photos),
                          'default_page': app.current_page, 'embedded_library': gallery.embedded,
                          'vertical_grip_hidden': 'vgrip' not in str(ttk.Style(root).layout('Vertical.Sash'))}
                if sys.platform == 'win32':
                    # Exercise the frozen paint layer in this isolated fixture,
                    # without touching the real mouse or any user catalogue.
                    from selection_overlay import _api
                    from ctypes import wintypes
                    canvas = gallery.canvas
                    x,y = canvas.winfo_rootx(), canvas.winfo_rooty()
                    native = _api()
                    native.GetForegroundWindow.restype = wintypes.HWND
                    foreground, focus = native.GetForegroundWindow(), root.focus_displayof()
                    gallery.marquee = {'start': gallery._point(x+8,y+8),
                        'pointer':(x+108,y+88), 'origin':(x+8,y+8), 'dragging':True,
                        'original':set(gallery.selected_keys()), 'add':False, 'physical':False}
                    canvas.grab_set()
                    gallery._paint_marquee()
                    root.update_idletasks()
                    overlay = gallery._selection_overlay
                    diagnostic = {'visible':overlay.visible,
                        'items':len(overlay._canvas.find_all()) if overlay._canvas is not None else 0,
                        'moving_edges':len(gallery._selection_edges),
                        'foreground_unchanged':native.GetForegroundWindow() == foreground,
                        'focus_unchanged':root.focus_displayof() is focus}
                    gallery.end_marquee()
                    root.update_idletasks()
                    diagnostic.update(hidden=not overlay.visible,
                                      capture_released=canvas.grab_current() is None)
                    result['selection_overlay'] = diagnostic
                Path(report).write_text(json.dumps(result), encoding='utf-8')
                app.close()
            root.after(100, check_library)
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
