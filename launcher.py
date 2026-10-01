"""Small GUI entrypoint: paint startup first, import media engines off Tk's thread."""
import multiprocessing as mp
import os
from pathlib import Path
import sys
import time
import tkinter as tk
from tkinter import messagebox


def main():
    # Existing isolated diagnostic commands retain their original argument contract.
    if any(arg.startswith('--verify-') or arg == '--smoke-test' for arg in sys.argv[1:]):
        from app import main as application_main
        return application_main()
    from single_instance import SingleInstance
    from startup_ui import Splash, StartupSequence, StartupStage
    check = '--startup-check-json' in sys.argv
    state_dir = None
    if '--data-dir' in sys.argv:
        index = sys.argv.index('--data-dir')
        state_dir = str(Path(sys.argv[index+1]).expanduser().resolve())
        os.environ['VIDEOCATCH_DATA_DIR'] = state_dir
    owner = None if check else SingleInstance()
    if owner is not None:
        try:
            if not owner.acquire():
                delivered = owner.wake_existing()
                owner.close()
                if not delivered:
                    root = tk.Tk(); root.withdraw()
                    messagebox.showerror('拾影正在运行', '已有拾影实例，但暂时无法恢复窗口。请从系统托盘打开。', parent=root)
                    root.destroy()
                return
        except OSError:
            owner.close()
            root = tk.Tk(); root.withdraw()
            messagebox.showerror('拾影无法启动', '无法建立本机实例锁，请检查账户权限后重试。', parent=root)
            root.destroy()
            return
    if sys.platform == 'win32':
        import ctypes
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    root.withdraw()
    splash = Splash(root)
    context, elapsed = {}, {}

    def measured(label, action):
        def run():
            start = time.monotonic()
            try:
                return action()
            finally:
                elapsed[label] = round((time.monotonic()-start)*1000, 1)
        return run

    def load_engines():
        import app
        context['module'] = app

    def load_state():
        cls = context['module'].App
        instance = cls.__new__(cls)
        context['app'] = instance
        cls.__init__(instance, root, smoke=check, state_dir=None if check else state_dir, defer_ui=True)

    def build_workbench():
        # Retrying a failed layout does not retain orphan views or callbacks.
        for child in root.winfo_children():
            if child != splash.window:
                child.destroy()
        context['app'].build_ui()

    def ready():
        app = context['app']
        app.finish_initialization()
        if check:
            import json
            from version import VERSION
            index = sys.argv.index('--startup-check-json')
            report = Path(sys.argv[index+1])
            def report_ready():
                report.write_text(json.dumps({'version': VERSION, 'page': app.current_page,
                    'splash_finished': splash.finished, 'stage_ms': elapsed,
                    'icon_animation': {'available': splash.icon_animation_available,
                                       'frames_drawn': splash._icon_updates}}), encoding='utf-8')
                app.close()
            root.after(150, report_ready)
        elif owner is not None:
            def activation():
                if app.closing:
                    return
                try:
                    if owner.poll():
                        app.show_main()
                except OSError:
                    app.notice.set('窗口恢复请求暂时不可用，可从系统托盘恢复。')
                root.after(150, activation)
            activation()

    def failed(error):
        if check:
            import json
            index = sys.argv.index('--startup-check-json')
            Path(sys.argv[index+1]).write_text(json.dumps({'ok': False, 'stage': sequence.index,
                'error_type': type(error).__name__, 'stage_ms': elapsed}), encoding='utf-8')
            root.after_idle(root.destroy)
        app = context.get('app')
        if app is None:
            return
        if sequence.index == 1:
            bridge = getattr(app, 'bridge', None)
            if bridge is not None:
                bridge.shutdown(); bridge.server_close()
        timer = getattr(app, 'layout_timer', None)
        if timer:
            try: root.after_cancel(timer)
            except tk.TclError: pass
        # A failed final stage must not leave a duplicate notification icon on retry.
        tray = getattr(app, 'tray', None)
        if tray is not None:
            tray.close()
            app.tray = None

    sequence = StartupSequence(root, splash, [
        StartupStage('正在载入采集与剪辑组件', measured('engines', load_engines), background=True),
        StartupStage('正在读取本机设置与素材目录', measured('state', load_state)),
        StartupStage('正在准备素材工作台', measured('layout', build_workbench)),
        StartupStage('正在连接本机服务', measured('ready', ready)),
    ], on_error=failed)
    sequence.start()
    try:
        root.mainloop()
    finally:
        sequence.cancel()
        app = context.get('app')
        if app is not None and not getattr(app, 'closing', False):
            if getattr(app, 'tray', None) is not None:
                app.tray.close()
            bridge = getattr(app, 'bridge', None)
            if bridge is not None:
                bridge.shutdown(); bridge.server_close()
                bridge.ai = None
            if getattr(app, 'ai', None) is not None:
                app.ai.app = None
        if owner is not None:
            owner.close()


if __name__ == '__main__':
    mp.freeze_support()
    main()
