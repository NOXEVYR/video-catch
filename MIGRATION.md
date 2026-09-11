# 仓库迁移说明

拾影 VideoCatch 现在使用独立仓库 `turnsolesama/video-catch`。源码、浏览器扩展和文档在本仓库维护；[portfolio](https://github.com/turnsolesama/portfolio) 保留工具总入口与原有历史。

迁移来源是 `portfolio` 的 [`d3b47d7fc4320f627e6b5fc8f653fcbd35007670`](https://github.com/turnsolesama/portfolio/commit/d3b47d7fc4320f627e6b5fc8f653fcbd35007670) 提交中的 [`video-catch/` 目录](https://github.com/turnsolesama/portfolio/tree/d3b47d7fc4320f627e6b5fc8f653fcbd35007670/video-catch)。该目录内容提升为本仓库根目录；[开发命令](README.md#开发和验证)直接在根目录执行。

- 原有提交历史保留在 `portfolio`。这次迁移不代表重新发布 0.3.0，也不把旧运行包加入新 Git 历史。
- Windows 程序与 Chrome / Edge 扩展继续从 [`portfolio` 的 `videocatch-v0.3.0` Release](https://github.com/turnsolesama/portfolio/releases/tag/videocatch-v0.3.0) 下载，首页保留其原始资产 URL。校验记录见[原发布说明](https://github.com/turnsolesama/portfolio/blob/d3b47d7fc4320f627e6b5fc8f653fcbd35007670/video-catch/releases/README.md)。
- 此次只迁移公开源码与文档，不包含用户下载的视频、捕获地址、Cookie、配对码或本机设置。
- 已安装程序与浏览器扩展不因仓库迁移自动更新。升级时仍按[旧版更新说明](README.md#已经安装过旧版)一起更新程序和扩展。

[返回拾影首页](README.md) · [工具总入口](https://github.com/turnsolesama/portfolio)
