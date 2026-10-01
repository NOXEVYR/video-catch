"""Render isolated generated media for local Windows UI inspection."""
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import App, enable_dpi_awareness
from engine import ffmpeg_path


def capture(window, path):
    window.attributes('-topmost', True)
    window.lift()
    window.update()
    time.sleep(.2)
    x, y, w, h = window.winfo_rootx(), window.winfo_rooty(), window.winfo_width(), window.winfo_height()
    command = f"Add-Type -AssemblyName System.Drawing; $b = New-Object System.Drawing.Bitmap({w},{h}); $g = [System.Drawing.Graphics]::FromImage($b); $g.CopyFromScreen({x},{y},0,0,$b.Size); $b.Save('{str(path).replace(chr(39), chr(39)*2)}'); $g.Dispose(); $b.Dispose()"
    subprocess.run(['powershell', '-NoProfile', '-Command', command], check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    window.attributes('-topmost', False)


def main():
    enable_dpi_awareness()
    output = Path(__file__).resolve().parents[2] / 'oct01-ui'
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        root = tk.Tk()
        app = App(root, smoke=True)
        try:
            root.geometry('1180x790+40+40')
            root.update()
            capture(root, output / 'workbench.png')
            app.open_library()
            gallery = app.library_window
            gallery.window.geometry('1000x760+60+60')
            capture(gallery.window, output / 'library-empty.png')
            for index, color in enumerate(('0x725eaa', '0xe6ae7c', '0x416e7c')):
                video = Path(temp) / (['山间暮色', '暖光片段', '湖畔纪事'][index] + '.mp4')
                subprocess.run([ffmpeg_path(), '-v', 'error', '-f', 'lavfi', '-i', f'color={color}:s=640x360:r=12', '-t', '0.5', str(video)], check=True)
                app.library.add(video)
            gallery.render()
            deadline = time.monotonic() + 12
            while len(gallery.photos) < 3 and time.monotonic() < deadline:
                root.update()
                time.sleep(.03)
            capture(gallery.window, output / 'library-populated.png')
            gallery.window.geometry('720x520+60+60')
            capture(gallery.window, output / 'library-small.png')
        finally:
            app.close()
    print(output)


if __name__ == '__main__':
    main()
