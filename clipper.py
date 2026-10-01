"""Accurate local clipping in a cancellable worker process; never overwrite."""
from pathlib import Path
import os
import re
import subprocess
import tempfile

from engine import ffmpeg_path, clean_error


def source_stamp(path):
    stat = Path(path).stat()
    return (stat.st_size, stat.st_mtime_ns, stat.st_ino)


def clip_worker(item, folder, events):
    def send(**fields):
        events.put((item["id"], fields))
    try:
        ffmpeg = ffmpeg_path()
        if not ffmpeg:
            raise RuntimeError("未找到 FFmpeg")
        source = str(Path(item["source_path"]).resolve())
        stamp = source_stamp(source)
        if item.get('source_stamp') is not None and tuple(item['source_stamp']) != stamp:
            raise ValueError('源视频在排队后发生变化，请重新打开并选择片段')
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        base = [ffmpeg, "-hide_banner", "-nostdin", "-protocol_whitelist", "file,pipe"]
        probe = subprocess.run(base + ["-i", source], capture_output=True, timeout=30, creationflags=flags)
        info = probe.stderr.decode("utf-8", errors="replace")
        match = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", info)
        if not match or "Video:" not in info:
            raise ValueError("无法读取本地视频时长或视频轨道")
        duration = int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])
        start, end = item["start"], item["end"]
        if start >= duration or end > duration + 0.05:
            raise ValueError(f"裁剪范围超出视频时长（{duration:.2f} 秒）")
        target = Path(folder) / f"clip-{item['id']}.mp4"
        if target.exists():
            raise FileExistsError("输出文件已存在，请重新创建裁剪任务")
        send(status="裁剪中", progress="精确裁剪并编码为 MP4")
        with tempfile.TemporaryDirectory(prefix=".videocatch-clip-", dir=folder) as temporary:
            partial = Path(temporary) / "clip.mp4"
            command = base + ["-progress", "pipe:1", "-v", "error", "-n", "-ss", str(start), "-i", source, "-t", str(end - start),
                              "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "fast",
                              "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(partial)]
            if source_stamp(source) != stamp:
                raise ValueError('源视频发生变化，已取消导出')
            result = subprocess.run(command, capture_output=True, creationflags=flags)
            if source_stamp(source) != stamp:
                raise ValueError('源视频发生变化，已取消导出')
            if result.returncode or not partial.is_file() or not partial.stat().st_size:
                raise RuntimeError("裁剪失败，源文件未更改；请检查视频格式和保存空间")
            frames = [int(line[6:].strip()) for line in result.stdout.splitlines()
                      if line.startswith(b"frame=") and line[6:].strip().isdigit()]
            if not frames or frames[-1] < 1:
                raise ValueError("所选时间范围未生成视频帧，请扩大裁剪区间")
            # Windows rename is atomic and refuses an existing destination, including a race.
            if os.name == "nt":
                os.rename(partial, target)
            else:
                os.link(partial, target)
        send(status="已保存", progress="100%", path=str(target.resolve()), error="")
    except Exception as exc:
        send(status="失败", progress="裁剪未完成", error=clean_error(exc))
