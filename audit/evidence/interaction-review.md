# 交互独立审查

日期：2026-10-01。范围：共享右键组件、素材库框选/原生拖出交互、网页批量动作及页面清理。源码归档无 Git；遵循根 AGENTS.md。实现和测试均保持只读；仅写此审查记录及先前的 context-review.md。

## 结论

当前稳定结果未发现实质问题。现有 76 项相关测试通过，另做共享菜单异常/重开/回收等补充离屏探针。测试覆盖和静态核对相互补充，不据此声称真实用户鼠标、系统焦点、物理屏幕 DPI、OLE 接收端或已安装拾影已验收。

曾发现 `LibraryDirectionalTkTests` 裸构造 fixture 缺少新增 `context_menu` 字段；已通知 root，root 添加初始化和 cleanup 后，原模块直接运行 12/12 通过。该问题已解决，不是当前残留 finding。最初 work-area Mock 保留 owner 导致的 weakref 假失败也已排除，详见 context-review.md。

## 验证命令及结果

在 source 目录，以本机 Python 3.11 通过 stdin 执行：

```python
import sys, unittest
sys.path.insert(0, 'tests')
modules = ['test_context_menu', 'test_library_selection', 'test_gallery_selection',
           'test_library_context_menu', 'test_web_batch_actions']
suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in modules)
result = unittest.TextTestRunner(verbosity=1).run(suite)
sys.exit(not result.wasSuccessful())
```

结果：76/76，34.276 秒，退出码 0。使用测试自身的 +12000+12000 Tk fixtures、生成 PNG 和临时/禁用持久化状态；无用户程序启动、真实鼠标键盘、Computer Use、剪贴板操作及私有媒体读取。

## 核对行为与位置

- `context_menu.py:193` / `:198` / `:245`：disabled 的键盘/Enter/鼠标激活受同一检查；执行回调前销毁菜单并释放自身 grab、清引用和 binding。现有测试覆盖模态 dialog handoff；补充探针覆盖异常回调和回调重开。
- `context_menu.py:208` 至 `:243`：outside click、FocusOut、owner Unmap/Destroy、popup Destroy 清理；负横坐标和 100/150/200% 小工作区滚动已有实际 Tk widget 测试。
- `library_selection.py:27` / `:59`：selection bindtag 放在 widget/class/toplevel 之前；仅 active marquee 拦截 motion/release，跨名称 Checkbutton 的 release 能结束框选并应用最后坐标，而普通名称/动作按钮保留自己的点击语义。
- `library_selection.py:184` / `:214` / `:227`：blank/card 边框/metadata/动作行空白可框选，4 方向交集一致；physical sampling 的逻辑用受控 snapshot 验证，未操纵真实指针。
- `library_selection.py:304` / `:358`：结束/取消清 timer、grab、overlay，Escape 恢复快照；重建、切页、失焦/隐藏/关闭均有离屏生命周期测试。
- `library_ui.py:292` / `:410` / `:644`：重建与关闭会关闭共享菜单；右键所选卡片保留组，右键其他卡片替换；开始菜单前清掉框选，native drag 活跃时拒绝菜单。封面继续承担原生拖出，边框/metadata 承担框选。
- `app.py:135`：show_page 关闭网页/任务菜单，离开 library 清素材菜单及手势，并取消 native drag。
- `app.py:1019` / `:1064`：批量显示名称只解释 `{name}`、`{n}`，替换内容保持字面值；全部名称验证和一次回执保存后更新内存，取消/无效名称/保存失败保留整组；单项名称的字面花括号语义保留。网页待保存发现记录不跨会话写回执是原有隐私规则，不将其误报成保存缺陷。
- `app.py:1076` / `:1091` / `:1109` / `:1127` / `:1162` 与 `ui.py:326`：选择数量/按钮状态、Ctrl/Shift、全选/取消选择、右键组保留、批量取消/删除忙任务保护，以及小宽度按钮换行均通过合成事件/实际 Tk 控件测试。

## 复现边界

既有测试并未证明真实 OS 焦点切换；相关 FocusOut/焦点恢复采用合成事件或受控替换。没有访问用户录屏、没有复制私人素材，也未操作真实 OLE 接收端。源代码行为和合成事件通过仍需与物理鼠标/已安装版本验收区分。

## 审查结束源码 SHA-256

- context_menu.py: 4636FEA6010B8DECCAB8025E8CCFBE279559A80A07230E90BBAA47850C714A4C
- library_selection.py: 6C9D4D9866331A249A026B56B05D4E12CFE3D0D093E238F6AEF7CA4E85D41D7A
- library_ui.py: C3CC75080C4EBD56C7CC67DDF666FEC41A2A9208E2DB15B956E8A3AE1FD6B082
- app.py: 25D97182A0F6E9D3E8BA58FC650C32C22C371F9E7C830AB7DAB7EC486836D119
- ui.py: B74B8E1463E7FEB49E0E83739E82B43B15C68EF6F8B52825E3CF13D85A22CA49
- tests/test_context_menu.py: 881119A105CDF6808ED7799C5D0776C74C0B4609E577C7FA77B58E89C8C796E5
- tests/test_library_selection.py: 54D015F980BEAE3BF9CAAC7B2166B98B1D3F550B603E6BEA698B2A0DF0F6C7AE
- tests/test_gallery_selection.py: E73FEAA5CD2AB77AC7A80CCD3E04C3E82BEF83A29D3B26F2A5C57034BAED084C
- tests/test_library_context_menu.py: F68277A2D06CB5066495A1336D98B5D026E90A8D398669AF2E1FE628F45C0AE6
- tests/test_web_batch_actions.py: E89824F4A9537E89CDBBF6C8C4771C46916B648D535E392B3F0B7A097D713179

## 补充复核：监听心跳覆盖显示名称（已修复）

这是先前审查后另行发现并由 root 修复的问题：`Store.add_page` 原本把 title 无条件替换为标签页标题，监听中的用户批量显示名称会在下一次 sync 心跳或格式升级时丢失。

只读复核 `core.py:154-178` 及 `tests/test_pages.py:38-54` 后，确认 `core.py:172` 现在优先使用明确的 `display_name`，没有显示名称时继续跟随网页标题。`Store.sync` 的真实受监视页面路径调用同一 `add_page`，B站格式升级也通过同一路径；新增回归覆盖重命名后 sync 保留、显式 add_page 格式升级保留，以及未改名页面标题继续更新。

独立执行：`python -m unittest discover -s tests -p test_pages.py -v`，6/6 通过，0.001 秒，退出码 0。此补充没有发现新的实质问题，未修改实现和测试；没有扩大到其他 core 行为或未稳定的 UI 高度调整。
- 补充复核 core.py: 29E57FD057EDC6FDA0DA3560151CB764EFBC4F3025CF77F6A53A4F3EEDC6B7F0
- 补充复核 tests/test_pages.py: 14A0B89DA4F8673F0873AC3885072968C9ABB6C056B57C8DB32B647A45D40D42

## 最终补充：网页高度预算与 flow 布局静态复核

本轮范围仅为已交接稳定的 `ui.py` 高度/换行改动，以及新增 `WebListHeightTests` 的检查内容。为避免干扰 root 正在进行的 WM_PRINT 三 DPI 渲染，没有创建 Tk 解释器/GUI，也未并发重跑 GUI 测试。

静态复核未发现新的实质问题：

- `ui.py:158-174` 将卡片与 pack child 的 padding、pady/ipady、reqheight 计入预算；列表额外保留表头、4 个完整 rowheight、树内部滚动条与余量。
- `ui.py:176-215` 根据真实 canvas 宽度选横排/竖排，outer chrome 和两卡片预算共同决定 canvas content/scrollregion 高度，空间不足走纵向滚动。高度计算不以旧 content 高度为增量，因此未见单调膨胀的反馈路径。
- `ui.py:33-64` 的 `flow=True` 使用各按钮实际 reqwidth 逐行 place，frame 的高度由当前宽度下的行数确定。它不使用 grid 的跨行共享列宽；也没有在 Configure 回调内执行 update/mainloop/循环调度。宽度和包装高度稳定后再次计算得到相同布局，静态没有发现 resize 死循环。
- `ui.py:201-212` 只在首次定位/朝向变化时设置初始 sash；已经竖排时只把超出两侧最低高度预算的 sash 限制在合法区间，区间内的用户位置保留。横排不会在每次 label/按钮 Configure 时反复归位。
- 单个最长动作的 reqwidth 用于判断横排最小空间，flow 各行独立累计宽度；新增 `tests/test_web_batch_actions.py:33` 以 100/150/200%、1020/720 宽度、设置展开/收起和长详情分别检查第4行 bbox 完整、outer 无横滚及按钮右边界。该测试读到的断言与所述目标相符。

worker 交接报告：own 15 + small_display 1，16 项通过。本 reviewer 本轮只读静态核对，未独立重复这 16 项，不将 worker 结果冒称独立 GUI 验证。先前报告的 76 项独立运行对应最终高度改动之前的快照；下面 SHA-256 替代旧 ui/test_web_batch_actions hash，供最终构建核对。
- 最终静态复核 ui.py: 28798D9E8B0521BFAE2F0AE451D53D12BF3A9FEFFBB38B68CB64C5CBC97B0E0E
- 最终静态复核 tests/test_web_batch_actions.py: 8CC6796CA8C12D76D41386D4D15A7E9C4E35B5B30DD44644C9602692BCF43176
