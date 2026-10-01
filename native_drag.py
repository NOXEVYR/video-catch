"""One cancellable STA file drag. The worker never receives a Tk object."""
import ctypes
from pathlib import Path
import queue
import sys
import threading


class NativeFileDrag:
    def __init__(self):
        self.paths = ()
        self.cancelled = threading.Event()
        self.results = queue.Queue(maxsize=1)
        self.thread = None

    @property
    def busy(self):
        return self.thread is not None

    def start(self, paths):
        if self.busy:
            raise RuntimeError('文件拖放正在进行，请先结束当前操作。')
        snapshot = tuple(str(Path(path).resolve()) for path in paths)
        if not snapshot or len(snapshot) > 200 or any(not Path(path).is_file() for path in snapshot):
            raise ValueError('请只拖动已保存且仍存在的素材（每次最多 200 个）。')
        if sys.platform != 'win32':
            raise RuntimeError('拖出复制目前支持 Windows，请使用导出副本。')
        self.paths = snapshot
        self.cancelled.clear()
        # Called by the Tk thread: hand off Windows' implicit mouse capture.
        ctypes.windll.user32.ReleaseCapture()
        self.thread = threading.Thread(target=self._run, args=(snapshot,), daemon=True,
                                       name='VideoCatch native file drag')
        self.thread.start()

    def _run(self, paths):
        copied, error = False, ''
        try:
            from file_drag import drag_files
            copied = drag_files(0, paths, cancel_event=self.cancelled, physical_buttons=True)
        except Exception as exc:
            error = str(exc) or '文件拖放未完成，请重试或导出副本。'
        self.results.put((copied, error))

    def cancel(self):
        self.cancelled.set()

    def poll(self):
        if not self.busy:
            return None
        try:
            result = self.results.get_nowait()
        except queue.Empty:
            return None
        self.thread.join(timeout=1)
        self.thread = None
        self.paths = ()
        return result
