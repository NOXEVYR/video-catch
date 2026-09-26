"""Paths shared by the portable Windows folder and macOS application bundle."""
import os
from pathlib import Path
import subprocess
import sys


def distribution_root():
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parent
    executable = Path(sys.executable).resolve()
    if sys.platform == "darwin" and executable.parent.name == "MacOS" and executable.parent.parent.name == "Contents":
        return executable.parents[3]
    return executable.parent


def open_local(path):
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        os.startfile(str(path))
