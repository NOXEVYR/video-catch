# 共享右键组件独立审查

日期：2026-10-01。审查范围：`context_menu.py`、`tests/test_context_menu.py`。源码归档，无 Git；未修改实现与测试。遵循源码根 `AGENTS.md`。未读取尚在修改的 app/ui/library 源码。

## 结论

未发现实质问题。以下证据仅来自生成的离屏 Tk own fixtures，不代表安装版、真实鼠标键盘或物理显示器验收。

## 实测

- Python 3.11；通过 stdin 载入 `tests/test_context_menu.py`，以普通函数替换 `context_menu.owner_work_area` 返回 `(12000,12000,13920,13080)`，确保弹窗保留离屏。现有 14 项测试全部通过。
- 现有测试覆盖关闭与 grab 清理、模态对话框前清理、disabled 跳过与键盘绕回、outside click、模拟 FocusOut、恢复此前焦点与 local grab、保留另一活动 grab、owner hide/destroy、popup destroy、show 异常清理、负横坐标、100/150/200% 的 640×480 菜单滚动与行边界。
- 补充生成的 own fixtures：回调抛 `RuntimeError` 时 popup/grab/bindings 已清；Tk 回调异常接收器只记录预期异常。回调重开菜单后，新菜单获得 local grab，旧 popup 弱引用可回收。
- 全 disabled 菜单上下与 Enter 不执行回调；作为 owner 的子 Frame 的 Unmap/Destroy 均清 popup/bindings。
- 200 行菜单逐行键盘访问后绕回第 0 行；20 次 show/close 后 owner 的 Destroy/Unmap 绑定无残留，controller 弱引用可回收。
- 初次使用 `Mock(return_value=...)` 替换 work-area 时，Mock 调用记录保留 owner，产生一次 weakref 断言假失败。改用无调用记录的普通函数后 14/14 通过；不归因于组件。

## 静态核对位置

- `context_menu.py:193-205`：Enter 走 `_activate`；再次检查 disabled，先 close 后调用用户回调。
- `context_menu.py:208-243`：outside click / FocusOut / owner Unmap/Destroy / popup Destroy。
- `context_menu.py:245-286`：先清引用、释放自己的 grab、恢复前一 local grab、解绑、销毁及有条件恢复焦点。
- `context_menu.py:95-109,179-190`：工作区域限位、负坐标绝对定位与滚动可达。

## 范围限制

未启动用户拾影实例，未访问私人目录、剪贴板，未使用 Computer Use、真实鼠标键盘，也未读取/审查当前 app/ui/library 集成代码。FocusOut/焦点恢复部分由受控替换与合成事件核对，没有声称真实系统焦点切换已验收。
