"""Local preferences and recovery, with revocable collaboration consent."""
from datetime import datetime
import tkinter as tk
from tkinter import ttk

from surface_ui import ScrollableBody, card, header, setup_window
from ui import BG, ACCENT


def show_preferences(app, section=None):
    """Reuse settings; an explicit section also selects its page."""
    previous = getattr(app, 'preferences_window', None)
    if previous is not None and previous.winfo_exists():
        previous.refresh_status()
        if section is not None:
            previous.select_section(section)
        previous.deiconify()
        previous.lift()
        return previous

    window = app.preferences_window = tk.Toplevel(app.root)
    setup_window(window, app, '拾影 · 设置与恢复', 760, 650,
                 minwidth=560, minheight=480)
    header(window, '设置与恢复', '管理采集、AI 协作与本机记录。', app=app)
    # Feedback and actions remain reachable when the page needs to scroll.
    footer = ttk.Frame(window, padding=(20, 12, 20, 16))
    footer.pack(side='bottom', fill='x')
    feedback_row = ttk.Frame(footer)
    feedback_row.pack(fill='x', pady=(0, 10))
    feedback_row.columnconfigure(0, weight=1)
    feedback = tk.Text(feedback_row, height=2, width=1, wrap='word',
                       state='disabled', takefocus=True, background=BG,
                       foreground=ACCENT, borderwidth=0, highlightthickness=0,
                       font=ttk.Style(window).lookup('.', 'font') or ('Microsoft YaHei UI', 9))
    feedback.grid(row=0, column=0, sticky='ew')
    feedback_scroll = ttk.Scrollbar(feedback_row, orient='vertical', command=feedback.yview)
    feedback_scroll.grid(row=0, column=1, sticky='ns')
    feedback.configure(yscrollcommand=feedback_scroll.set)
    footer_actions = ttk.Frame(footer)
    footer_actions.pack(fill='x')
    tabs = ttk.Notebook(window)
    tabs.pack(fill='both', expand=True, padx=20, pady=(0, 4))
    views = {}
    pages = {}
    bindings = []
    traces = []
    closed = False

    def settings_tab(title):
        page = ttk.Frame(tabs)
        tabs.add(page, text=title)
        body = ScrollableBody(page)
        body.pack(fill='both', expand=True)
        views[str(page)] = body
        pages[title] = page
        return body.frame

    def paragraph(parent, text=None, variable=None, status=False):
        label = ttk.Label(parent, text=text, textvariable=variable,
                          style='SurfaceStatus.TLabel' if status else 'SurfaceMuted.TLabel',
                          wraplength=500)
        label.pack(fill='x', anchor='w', pady=(6, 10))
        label.bind('<Configure>', lambda event: label.configure(
            wraplength=max(1, event.width)), add='+')
        return label

    def section_card(parent, title, description=None):
        panel = card(parent, padding=16)
        panel.pack(fill='x', padx=2, pady=(2, 12))
        ttk.Label(panel, text=title, style='Section.TLabel').pack(anchor='w', fill='x')
        if description:
            paragraph(panel, description)
        return panel

    capture = settings_tab('采集与保存')
    ai_body = settings_tab('AI 协作')
    recovery = settings_tab('诊断与恢复')
    capture_card = section_card(capture, '录屏与截图',
        '快捷工具栏负责框选与开始采集；画质、帧率、摄像头、声音设备和倒计时在采集参数中调整。')
    ttk.Button(capture_card, text='打开采集参数设置',
               command=app.open_capture_settings, style='Quiet.TButton').pack(anchor='w')
    folder_card = section_card(capture, '素材保存位置',
        '采集与下载使用主窗口的保存目录；可以选择在下次启动时继续使用。')
    folder_row = ttk.Frame(folder_card, style='Surface.TFrame')
    folder_row.pack(fill='x', pady=(0, 8))
    ttk.Entry(folder_row, textvariable=app.folder).pack(side='left', fill='x', expand=True)
    ttk.Button(folder_row, text='更改', command=app.choose_folder,
               style='Quiet.TButton').pack(side='right', padx=(8, 0))
    ttk.Checkbutton(folder_card, text='记住保存目录', style='Surface.TCheckbutton',
                    variable=app.remember_folder).pack(anchor='w', pady=(0, 8))

    def reset_folder():
        from pathlib import Path
        import sys
        app.remember_folder.set(False)
        app.folder.set(str(Path.home() / ('Movies' if sys.platform == 'darwin' else 'Videos') / 'VideoCatch'))
        app.save_preferences()
        refresh_status()

    ttk.Button(folder_card, text='恢复默认保存目录', command=reset_folder,
               style='Quiet.TButton').pack(anchor='w')
    startup_card = section_card(ai_body, '启动与当前协作',
        '仅允许本机鉴权访问。修改启动行为后，请点击下方「保存设置」。')
    ttk.Checkbutton(startup_card, text='启动时自动开启 AI 协作', style='Surface.TCheckbutton',
                    variable=app.ai_on_start).pack(anchor='w', pady=(0, 8))
    current = tk.StringVar(window)
    paragraph(startup_card, variable=current, status=True)
    ttk.Button(startup_card, text='打开当前 AI 协作', command=app.open_ai_collaboration,
               style='Quiet.TButton').pack(anchor='w')
    consent_card = section_card(ai_body, '限期授权与撤销')
    grant = tk.StringVar(window)
    paragraph(consent_card, variable=grant, status=True)
    paragraph(consent_card,
        '可显式授予本机客户端 24 小时重启恢复权限。每次启动仍轮换配对码，只允许回环鉴权调用。授权范围是当前本机账户可读取配对文件的程序，不是单独应用身份认证。CLI 不会启动或开启服务。')

    def perform(method):
        method()
        refresh_status()

    ttk.Button(consent_card, text='授权 24 小时', command=lambda: perform(app.grant_trust),
               style='Quiet.TButton').pack(anchor='w', pady=(0, 8))
    ttk.Button(consent_card, text='撤销并关闭协作', command=lambda: perform(app.revoke_trust),
               style='Danger.TButton').pack(anchor='w')
    diagnostics_card = section_card(recovery, '限量诊断日志')
    ttk.Checkbutton(diagnostics_card, text='启用诊断日志（默认关闭）', style='Surface.TCheckbutton',
                    variable=app.diagnostics).pack(anchor='w')
    paragraph(diagnostics_card,
        '日志只记录事件、状态和错误类型，不记录链接、请求头、令牌或私人路径。最多保留约 1 MiB；修改后请保存设置。')
    history_card = section_card(recovery, '任务记录与恢复',
        '最近 200 条任务回执保存在本机。重启时未完成的任务标为「已中断」，不会自动重试；历史「已保存」仍须核对文件。')
    paragraph(history_card,
        '在「任务与导出」选择已结束的任务，再清理记录。清理记录保留素材库和原文件；原始视频与录制恢复文件不会自动清理。')

    def open_tasks():
        app.show_main()
        app.show_page('tasks')

    ttk.Button(history_card, text='查看任务与清理记录', command=open_tasks,
               style='Quiet.TButton').pack(anchor='w')
    health_card = section_card(recovery, '本地状态')
    health = tk.StringVar(window)
    paragraph(health_card, variable=health, status=True)

    def refresh_status():
        if closed:
            return
        until = app.storage_settings.get('trust_until', 0)
        grant.set('重启恢复授权至：' + datetime.fromtimestamp(until).strftime('%m-%d %H:%M:%S') if until else
                  ('启动时自动开启 AI 协作。' if app.storage_settings.get('ai_on_start', True) else
                   '自动开启已关闭，重启后需手动开启协作。'))
        current.set('当前 AI 协作：' + ('已开启' if app.ai_enabled.get() else '已关闭'))
        health.set('\n'.join(app.local_state.warnings[-3:]) or '本地状态读取正常。')

    def select_section(title):
        if not closed and title in pages:
            tabs.select(pages[title])

    def save():
        result = app.save_preferences()
        refresh_status()
        return result

    def refresh_feedback(*_args):
        if closed:
            return
        feedback.configure(state='normal')
        feedback.delete('1.0', 'end')
        feedback.insert('1.0', app.notice.get())
        feedback.configure(state='disabled')
        feedback.yview_moveto(0)

    def release():
        nonlocal closed
        if closed:
            return
        closed = True
        for variable, ident in traces:
            variable.trace_remove('write', ident)
        traces.clear()
        for sequence, ident in bindings:
            window.unbind(sequence, ident)
        bindings.clear()
        for body in views.values():
            body.close()
        views.clear()
        pages.clear()
        if app.preferences_window is window:
            app.preferences_window = None
        window.refresh_status = lambda: None
        window.select_section = lambda _title: None
        window.close = lambda: None

    def close():
        if closed:
            return
        release()
        window.destroy()

    def destroyed(event):
        if event.widget is window:
            release()

    window.refresh_status = refresh_status
    window.select_section = select_section
    window.close = close
    window.protocol('WM_DELETE_WINDOW', close)
    for sequence, method in (('<Escape>', lambda _event: close()), ('<Destroy>', destroyed)):
        bindings.append((sequence, window.bind(sequence, method, add='+')))
    traces.append((app.ai_enabled, app.ai_enabled.trace_add('write', lambda *_args: refresh_status())))
    traces.append((app.notice, app.notice.trace_add('write', refresh_feedback)))
    ttk.Button(footer_actions, text='保存设置', command=save,
               style='Accent.TButton').pack(side='left')
    ttk.Button(footer_actions, text='关闭', command=close,
               style='Quiet.TButton').pack(side='right')
    refresh_status()
    refresh_feedback()
    if section is not None:
        select_section(section)
    return window
