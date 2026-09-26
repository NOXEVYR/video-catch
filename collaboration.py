"""Local pairing and a shareable, credential-free handoff to desktop agents."""
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import shlex
from runtime_paths import distribution_root


def settings_path():
    if sys.platform == "darwin" and "LOCALAPPDATA" not in os.environ:
        return Path.home() / "Library" / "Application Support" / "VideoCatch" / "ai-client.json"
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "VideoCatch" / "ai-client.json"


def save_pairing(token, port=18796):
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32}", token):
        raise ValueError("无效的配对码")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("无效的本机端口")
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".pair-", suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            json.dump({"token": token, "port": port}, stream)
        os.replace(temporary, path)
    finally:
        if temporary and Path(temporary).exists():
            Path(temporary).unlink()


def powershell_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def collaboration_prompt():
    frozen = getattr(sys, "frozen", False)
    base = distribution_root()
    mac = sys.platform == "darwin"
    client = base / (("VideoCatchAI" if mac else "VideoCatchAI.exe") if frozen else "videocatch_client.py")
    if not client.is_file():
        raise FileNotFoundError("缺少 AI 客户端，请完整解压拾影程序包后重试。")
    command = "& " + powershell_quote(client) if frozen else "& " + powershell_quote(sys.executable) + " " + powershell_quote(client)
    if mac:
        command = shlex.quote(str(client)) if frozen else shlex.quote(sys.executable) + " " + shlex.quote(str(client))
    shell = "macOS 终端（zsh/bash，保留引号）" if mac else "PowerShell（路径含空格/中文，请保留 & 和单引号）"
    return f"""请通过这台 {'macOS' if mac else 'Windows'} 电脑上的「拾影 VideoCatch」协助我下载、录制、截图或截取视频。
拾影已开启本次 AI 协作并完成本机配对。你需要能执行本机命令；如果只有云端/网页聊天工具，请说明无法访问这台电脑，不要假装已连接。

无需寻找安装目录、重新安装工具或读取配对密钥。使用 {shell} 执行以下命令：
{command} capabilities
{command} state

操作格式：
{command} <action> --input '请求参数文件的绝对路径.json'
把参数写成 UTF-8 JSON 文件，再传给 --input，避免命令行引号转义。客户端自动读取本机配对设置，输出 JSON；不要读取或展示密钥。

可用操作及参数：
- state / capabilities：无需参数。state 返回标签页 key、任务 id/status/progress/path/error。
- import：{{"url":"HTTP/HTTPS 视频直链或网页分享链接"}}
- watch：{{"keys":["state 返回的标签页 key"],"enabled":true}}
- download：{{"id":"视频 id"}}，可选 folder（绝对保存目录）、proxy。
- clip：{{"source":"本地视频绝对路径","start":10,"end":25}}，时间为秒，可选 folder。
- cancel：{{"id":"任务 id"}}
- capture_sources：查询显示器、窗口和音视频设备；devices.loading 为 true 时稍后重查。
- record_start：{{"mode":"region","region":{{"x":0,"y":0,"width":1280,"height":720}},"audio":"system","fps":30,"duration":60}}。mode 可为 screen/region/window/camera；窗口须传 hwnd；摄像头须传 camera 名称；音频可为 none/system/microphone/both。完整参数先查 capabilities。
- record_pause / record_resume / record_stop：{{"id":"录制 id"}}。record_state 查询当前录制；record_stop 会封装保存 MP4，不是删除录制。
- screenshot：{{"mode":"screen"}}；也支持 region 或 window，异步任务通过 state 查询 PNG 路径。

执行流程：
1. 先查 capabilities 和 state，再按我的具体任务操作；没有视频来源或时间范围时先问清楚。
2. 已有本地视频直接 clip；网页链接先 import → download → 轮询 state 直到「已保存」→ 用返回的 path 裁剪。
3. 浏览器抓取先从 state.tabs 选页面 → watch → 由用户或你的浏览器工具刷新并播放 → 从 state.items 选择主视频下载。拾影接口不能导航网页或点击播放。
4. 请求受理不等于完成。每 1～2 秒查询 state，直到「已保存」「失败」或「已取消」；成功后交付实际文件路径。遇到超时先查状态，不要盲目重复 clip。
5. 默认保存到拾影主窗口目录；精确裁剪另存 H.264/AAC MP4，会重新编码，源文件不变。无法绕过 DRM 或登录访问限制。
6. 仅按我的明确要求开始屏幕、声音或摄像头录制，不要擅自打开麦克风或摄像头。录制时保留浮动停止工具条；暂停/停止使用返回的 id，停止后轮询直到「已保存」。桌面画笔适用于全屏/区域录制，不能烧录到独立窗口或摄像头画面。

若未启动、接口关闭或配对过期，请提示我打开拾影并再次点击「开启协作并复制」。重新打开拾影后需再次点击；取消「允许 AI 协作」可关闭访问，已提交任务需单独取消。
详细本地接口文档：{base / 'AI接口使用说明.md'}
"""
