# 拾影 AI 接口 · 0.5.0 Windows / macOS

0.5.0 新增录屏与截图接口。先查 `capabilities`，以正在运行的程序返回的动作清单为准；0.4.2 下载包及 0.4.3 本地版本没有这些新增动作。平台包与更新范围见 [0.5.0 发布页](https://github.com/NOXEVYR/video-catch/releases/tag/v0.5.0)。

让有 HTTP / 命令行工具能力的 AI 助手控制拾影；不需要在拾影中填写模型服务商密钥。视频不会因启用此接口被上传到模型服务。

## 启动与配对

1. 完整解压所用平台的包。Windows 运行 `VideoCatch.exe`；macOS 14+ 运行 `VideoCatch.app`，并让 `VideoCatchAI`、文档和 `extension` 保持在 `.app` 同一目录。Apple Silicon 选 arm64 包，Intel 选 x86_64 包，以发布页实际提供的架构为准。
2. 点击顶部「开启协作并复制」。自动保存本次本机配对设置、开启接口，并复制协作指引。
3. 粘贴给能在**同一台电脑**执行命令的 AI，再描述视频来源、录制范围或截取时间。指引包含客户端绝对路径、Windows PowerShell 或 macOS zsh/bash 命令、参数和完整流程，不包含配对密钥。
4. AI 先调用 `capabilities` 和 `state`，再按任务下载或裁剪。纯网页聊天或远端云环境不能仅靠粘贴文字访问本机程序。

重启拾影、切换安装目录后，再点一次「开启协作并复制」。取消「允许 AI 协作」会拒绝新请求；已提交任务在列表中单独取消。复制失败会提示原因，不会新开启接口。

兼容旧流程：勾选协作开关 →「复制配对码」→ Windows 执行 `VideoCatchAI.exe --pair`，Mac 执行 `./VideoCatchAI --pair`。源码客户端为 `python videocatch_client.py`（Mac 通常用 `python3`），仅依赖标准库，需保留同目录 `collaboration.py` 和 `runtime_paths.py`。

配对设置在 Windows 保存于 `%LOCALAPPDATA%/VideoCatch/ai-client.json`，Mac 保存于 `~/Library/Application Support/VideoCatch/ai-client.json`，只在本机使用，不应分享或打包。写入采用原子替换；拾影重启后旧配对码失效。客户端仍优先使用 `VIDEOCATCH_TOKEN` 环境变量（固定默认端口）；如以前设置过该变量，需更新或清除它才能使用自动配对设置。

macOS 包没有 Developer ID 签名或 Apple 公证，首次打开可能需要按「系统设置 → 隐私与安全性」提示允许。屏幕录制、摄像头和麦克风需分别取得系统授权；这些权限交互及硬件采集尚未经过人工验证。CI 构建和合成媒体检查不代表实际录屏、收音已验收。

`capabilities` 还返回参数说明、轮询间隔和终态；`state` 包含默认保存目录、暂停状态及裁剪任务的来源与起止秒数。客户端错误带 `code`，区分 `pairing_required`、`pairing_expired`、`collaboration_disabled`、`connection_failed`、`invalid_input` 和服务端拒绝，便于按原因恢复。

## AI 可以做什么

所有请求均为 `POST http://127.0.0.1:18796/api/v1/<action>`，JSON 请求体，需 `Authorization: Bearer <本次配对码>`。返回 JSON；客户端绕过系统代理访问回环接口。

| action | 请求参数 | 返回与行为 |
| --- | --- | --- |
| `capabilities` | `{}` | 能力清单、时间单位、并发限制 |
| `state` | `{}` | 浏览器标签页 `tabs`、视频及任务 `items`；包含 `id/status/progress/path/error` |
| `watch` | `keys` 字符串数组、`enabled` 布尔值 | 开始或停止指定标签页监听；重复请求不会反向切换 |
| `import` | `url` | 添加 HTTP/HTTPS 直链或支持的网页链接，返回 `id`；列表满时 `full: true` |
| `download` | `id`，可选 `folder/proxy` | 提交已有视频下载；正在执行或已保存的记录不重复下载 |
| `clip` | `source` 本地文件路径、`start/end` 数值秒，可选 `folder` | 创建裁剪任务，返回独立 `id` |
| `cancel` | `id` | 取消下载 / 裁剪；录制任务转为停止并保存，截图需等待完成 |
| `capture_sources` | 可选 `refresh` 布尔值 | 返回 `desktop`、`monitors`、`windows` 和异步枚举的 `devices` |
| `record_start` | 见下方录制参数，可选 `folder` | 开始单个录制，返回录制 `id` 与当前状态 |
| `record_pause` / `record_resume` / `record_stop` | `id`，须为当前录制 | 暂停 / 继续 / 停止并保存；不会删除录制 |
| `record_state` | `{}` | 当前或最近一次录制的 `id/status/elapsed/options/path/error` |
| `screenshot` | `mode` 为 `screen/region/window`；区域传 `region`，窗口传 `hwnd`，可选 `folder` | 提交 PNG 截图，返回任务 `id`；通过 `state.items` 查询完成状态 |

不传 `folder` 时使用主窗口保存目录。`proxy` 沿用现有下载连接设置，可显式传空字符串直连。HTTP 200 表示请求受理，不代表视频已下载/裁剪完成；轮询 `state`，直到任务状态为「已保存」「失败」或「已取消」。最多同时执行两个下载/裁剪任务，所有任务都显示在主界面。

请求错误：400 参数无效、401 未配对、403 接口关闭或来源不允许、404 记录/接口不存在、409 操作冲突（如已有录制或尚未完成选区；部分列表容量限制也使用此状态）、429 请求过多、503 界面忙碌。遇到超时先查询状态，避免重复创建裁剪、录制或截图任务。

## 本地录制与截图

仅在用户明确要求时开启屏幕、声音或摄像头采集。接口不要求浏览器扩展；调用前确认要录制的范围、音源及摄像头需求。API 的 `record_start` 直接提交录制，不使用面板倒计时或最小化选项；录制期间会显示浮动停止工具条。

先调用 `capture_sources`，直接使用返回的显示器、窗口坐标，多屏布局可能含负数。Windows 坐标为物理像素，`screen` 覆盖整个虚拟桌面；macOS 坐标为 Quartz 全局逻辑点，Retina 输出像素由后端换算，`screen` 仅录主显示器，`region` 和窗口必须完整位于同一显示器，不能跨屏。

窗口从 `windows` 选择整数 `hwnd`，不要使用标题或显示器编号；这一参数名在 Mac 上也沿用。Windows 采集窗口客户区；**Mac 窗口录制是屏幕矩形裁剪，其他窗口的遮挡会入镜，目标移动或调整大小后会停止并保存**。目标须保持打开、可见且未最小化。

`devices.loading` 为 `true` 时等待约 1 秒再调用；完成后从 `cameras` 取 `name`，从 `systems` / `microphones` 取 `index`。需要重新枚举时传 `{"refresh":true}`；轮询时不传 refresh，避免反复启动枚举。**Mac 的 `audio: system` 或 `both` 需要已经配置好路由的 BlackHole、Loopback 等虚拟回环输入，并显式传入其 `system_index`**；程序不安装驱动或改动系统声音输出。没有这类设备时选择 `none` 或 `microphone`。

`record_start` 参数：

| 参数 | 用法 |
| --- | --- |
| `mode` | `screen`（默认）、`region`、`window` 或 `camera` |
| `region` | 区域模式必填：整数 `x/y/width/height`；宽高至少 2，单位沿用平台返回坐标，Mac 须在单一显示器内 |
| `hwnd` | 窗口模式必填：`capture_sources.windows` 返回的整数句柄 |
| `camera` | 设备名称；camera 模式必填，其他模式传此项即启用右下角摄像头画中画；省略表示不开摄像头 |
| `audio` | `none`（默认）、`system`、`microphone` 或 `both` |
| `system_index` / `microphone_index` | 对应音源的非负设备索引；建议显式传入已枚举的设备 |
| `fps` | 10 / 15 / 24 / 30（默认）/ 60 |
| `quality` | `high`、`balanced`（默认）或 `small` |
| `cursor` | 是否录入鼠标指针，默认 `true` |
| `duration` | 有限的非负秒数，0（默认）不限时；暂停时间不计入录制时长 |
| `folder` | 可选保存目录；省略使用主窗口目录 |

例如无声区域录制的 `record-request.json`（坐标应先按实际桌面确认）：

```json
{"mode":"region","region":{"x":0,"y":0,"width":1280,"height":720},"audio":"none","fps":30,"quality":"balanced","duration":60}
```

```powershell
.\VideoCatchAI.exe capture_sources
.\VideoCatchAI.exe record_start --input .\record-request.json
.\VideoCatchAI.exe record_state
```

以上为 Windows PowerShell 示例。源码运行可将 `.\VideoCatchAI.exe` 替换为 `python .\videocatch_client.py`。Mac 在包含 `VideoCatchAI` 的目录打开 zsh/bash，使用下面的对应命令，不添加 PowerShell 的 `&`：

```sh
./VideoCatchAI capabilities
./VideoCatchAI capture_sources
./VideoCatchAI record_start --input './record-request.json'
./VideoCatchAI record_state
```

Mac 源码调用可用 `python3 ./videocatch_client.py`；JSON 内的路径使用真实 Mac 绝对路径，如 `/Users/your-name/Movies/clips`，不要照抄 Windows 的 `D:/Videos`。将返回的录制 id 写入 `record-control.json`，如 `{"id":"record_start 返回的 id"}`，再按需要调用：

```powershell
.\VideoCatchAI.exe record_pause --input .\record-control.json
.\VideoCatchAI.exe record_resume --input .\record-control.json
.\VideoCatchAI.exe record_stop --input .\record-control.json
.\VideoCatchAI.exe record_state
```

macOS 对应为 `./VideoCatchAI record_pause --input './record-control.json'`，将动作改成 `record_resume` 或 `record_stop` 即可继续或停止，随后执行 `./VideoCatchAI record_state` 查询保存结果。

同一时间只能有一个进行中或正在保存的录制。暂停后保留已录片段，继续时重新准备采集；暂停期间不写入最终视频。停止返回「正在保存」时继续每 1～2 秒查询，直到「已保存」并取得 `path` 才交付 MP4；若为「失败」，读取 `error`，不能交付临时文件冒充成片。完成后可把 MP4 的 `path` 作为 `clip.source` 精确裁剪。

截图示例 `screenshot-request.json`：

```json
{"mode":"screen"}
```

```powershell
.\VideoCatchAI.exe screenshot --input .\screenshot-request.json
.\VideoCatchAI.exe state
```

macOS zsh/bash 使用 `./VideoCatchAI screenshot --input './screenshot-request.json'`，然后 `./VideoCatchAI state`。

按返回的截图 id 匹配 `state.items`，等「已保存」后读取 PNG 路径。截图不使用摄像头或声音，也不支持 `mode: camera`。截图与录制均使用唯一文件名，不覆盖已有输出。桌面标注由界面画笔控制，API 没有画笔动作；笔迹仅进入全屏或区域画面，窗口 / 摄像头录制或窗口截图前需退出标注。

## 网页抓取流程

安装随包 `extension` 文件夹内的 Chrome / Edge 扩展并用同一配对码连接。AI 调用 `state` 取得标签页 `key`，提交 `watch`，在对应网页刷新并播放视频；随后从 `state.items` 取得视频 `id` 并提交 `download`。此接口本身不控制浏览器导航、点击或播放；这些动作由用户或 AI 的浏览器工具完成。

已有分享链接时可跳过扩展，先 `import` 再 `download`。站点兼容性、登录和 DRM 限制与原版一致；没有额外抓取 Cookie 或绕过访问限制的能力。测试使用本机合成素材，不能据此保证任意网站下载成功。

## 命令行示例

把请求参数保存为 UTF-8 JSON 文件，可以避免 Windows shell 引号转义。例如 `clip-request.json`：

```json
{"source":"D:/Videos/example.mp4","start":10,"end":25,"folder":"D:/Videos/clips"}
```

```powershell
.\VideoCatchAI.exe clip --input .\clip-request.json
.\VideoCatchAI.exe state
```

让 AI 截取网页视频：先下载成功，再把返回的 `path` 作为 `clip.source`。裁剪输出为唯一名称 `clip-<id>.mp4`，不覆盖源文件或已有输出。按时间精确裁剪会重新编码 H.264/AAC，并非无损剪辑；时长精度受源视频帧率影响。开始时间须非负，结束时间须大于开始且不超过视频时长。支持普通本地视频容器，禁止裁剪器读取网络协议或远程播放清单。

手动使用可点击「裁剪本地视频」，填写起止秒数。失败或取消的裁剪需重新提交；「保存所选视频」仅用于网页下载。

## 接口边界

只监听 IPv4 回环地址。沿用随机配对码，检查 Host，不开放 CORS，浏览器扩展来源也不能调用 AI 文件操作接口。媒体请求头和签名视频直链不出现在 AI 状态列表中，任务数据仅驻留本次进程内存。获得配对码且获准协作的本机客户端可以读取标签页信息、裁剪本机视频，以及调用录屏、音频、摄像头和截图能力，请只配对可信客户端。取消「允许 AI 协作」会拒绝后续 API 请求；已经开始的录制仍需通过界面停止并保存。
