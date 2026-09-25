# 拾影 AI 接口 · 0.4.2

让有 HTTP / 命令行工具能力的 AI 助手控制拾影；不需要在拾影中填写模型服务商密钥。视频不会因启用此接口被上传到模型服务。

## 启动与配对

1. 完整解压 Windows 包，运行 `VideoCatch.exe`。
2. 勾选「启用 AI 接口」。默认关闭，每次启动需重新开启。
3. 点击「复制配对码」。在本机运行 `VideoCatchAI.exe --pair`（源码方式为 `python videocatch_client.py --pair`）。配对码直接从剪贴板读入本机用户设置，不需要粘贴到 AI 对话。
4. AI 工具调用 `VideoCatchAI.exe capabilities` 或 `VideoCatchAI.exe state` 即可检查接口。源码客户端仅依赖 Python 标准库。

配对设置保存在 `%LOCALAPPDATA%/VideoCatch/ai-client.json`，只在本机使用，不应分享或打包。拾影重启后旧配对码失效，重复步骤 2～3。客户端还支持从 `VIDEOCATCH_TOKEN` 环境变量读取配对信息。关闭接口会拒绝新请求，已经开始的任务可在界面取消。

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
| `cancel` | `id` | 取消排队或执行中的任务；临时文件保留 |

不传 `folder` 时使用主窗口保存目录。`proxy` 沿用现有下载连接设置，可显式传空字符串直连。HTTP 200 表示请求受理，不代表视频已下载/裁剪完成；轮询 `state`，直到任务状态为「已保存」「失败」或「已取消」。最多同时执行两个下载/裁剪任务，所有任务都显示在主界面。

请求错误：400 参数无效、401 未配对、403 接口关闭或来源不允许、404 记录/接口不存在、409 列表已满、429 请求过多、503 界面忙碌。遇到超时先查询状态，避免重复创建裁剪任务。

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

只监听 IPv4 回环地址。沿用随机配对码，检查 Host，不开放 CORS，浏览器扩展来源也不能调用 AI 文件操作接口。媒体请求头和签名视频直链不出现在 AI 状态列表中，任务数据仅驻留本次进程内存。获得配对码的本机客户端可以读取标签页信息和裁剪本机视频，请只配对可信客户端。
