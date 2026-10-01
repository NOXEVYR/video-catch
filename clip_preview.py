"""Local frame timeline and cancellable FFmpeg preview. No Tk or network I/O."""
from bisect import bisect_left
from fractions import Fraction
from pathlib import Path
import re
import subprocess
import threading

from engine import ffmpeg_path


class Timeline:
    def __init__(self, times, end):
        if not times or any(b <= a for a, b in zip(times, times[1:])) or end <= times[-1]:
            raise ValueError("视频帧时间不连续，无法安全逐帧剪辑")
        self.times, self.end = times, end

    def nearest(self, seconds):
        index = bisect_left(self.times, seconds)
        if index == len(self.times):
            return index - 1
        if index and seconds - self.times[index - 1] <= self.times[index] - seconds:
            return index - 1
        return index

    def range(self, first, last):
        if not 0 <= first <= last < len(self.times):
            raise ValueError("结束帧不能早于开始帧")
        return self.times[first], self.times[last + 1] if last + 1 < len(self.times) else self.end


class Decoder:
    def __init__(self, source):
        self.source = str(Path(source).resolve())
        self.closed = threading.Event()
        self.lock = threading.Lock()
        self.process = None

    def launch(self, arguments):
        binary = ffmpeg_path()
        if not binary:
            raise RuntimeError("未找到 FFmpeg，请使用完整程序包")
        with self.lock:
            if self.closed.is_set():
                raise RuntimeError("剪辑预览已关闭")
            self.process = subprocess.Popen([binary, "-hide_banner", "-nostdin", "-protocol_whitelist", "file,pipe", *arguments],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return self.process

    def close(self):
        self.closed.set()
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                self.process.kill()

    def timeline(self, progress=lambda count: None):
        process = self.launch(["-i", self.source, "-map", "0:v:0", "-vf", "showinfo", "-an", "-sn", "-f", "null", "-"])
        times, timebase, duration = [], None, 0
        try:
            for raw in process.stderr:
                line = raw.decode("utf-8", errors="replace")
                config = re.search(r"config in time_base: (\d+/\d+)", line)
                if config:
                    timebase = Fraction(config[1])
                frame = re.search(r"\bn:\s*\d+\s+pts:\s*(-?\d+).*?duration:\s*(\d+)", line)
                if frame and timebase:
                    times.append(float(int(frame[1]) * timebase))
                    duration = float(int(frame[2]) * timebase)
                    if len(times) > 2_000_000:
                        raise ValueError("视频超过 200 万帧，请先截取较短片段后再逐帧剪辑")
                    if len(times) % 300 == 0:
                        progress(len(times))
            if process.wait() or self.closed.is_set():
                raise ValueError("视频无法完整解码，或预览已取消")
            if not times:
                raise ValueError("未找到可预览的视频帧")
            if duration <= 0:
                duration = times[-1] - times[-2] if len(times) > 1 else 1 / 25
            return Timeline(times, times[-1] + duration)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()
            process.stderr.close()

    def frame(self, seconds):
        # A sub-microsecond bias avoids rounding a timestamp past its frame.
        process = self.launch(["-v", "error", "-ss", f"{max(0, seconds - 0.0000005):.9f}", "-i", self.source,
            "-map", "0:v:0", "-frames:v", "1", "-vf", "scale=640:360:force_original_aspect_ratio=decrease",
            "-an", "-sn", "-f", "image2pipe", "-vcodec", "png", "pipe:1"])
        try:
            data, _ = process.communicate(timeout=20)
            if process.returncode or not data.startswith(b"\x89PNG"):
                raise ValueError("当前帧预览失败，请重新选择时间位置")
            return data
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

