const token = document.getElementById("token");
const name = document.getElementById("name");
const status = document.getElementById("status");
const button = document.getElementById("connect");
const pause = document.getElementById("pause");
let paused = false;
function showSync(result) {
  status.textContent = result.paused ? "连接已暂停" : result.ok ?
    `已连接，发现 ${result.count} 个标签页。请在桌面勾选。` : result.error;
}
chrome.storage.local.get(["token", "name", "paused"]).then(v => {
  token.value = v.token || "";
  name.value = v.name || "";
  paused = Boolean(v.paused);
  pause.textContent = paused ? "恢复连接" : "暂停连接";
  status.textContent = paused ? "连接已暂停；恢复后将重新同步标签页" :
    (v.token ? "正在检查桌面程序连接…" : "未配对；请从桌面程序复制配对码");
  if (v.token && !paused) chrome.runtime.sendMessage({ type: "sync" }).then(showSync,
    () => { status.textContent = "未连接：请打开桌面程序，或重新填写配对码"; });
});
button.addEventListener("click", async () => {
  if (paused) { status.textContent = "连接已暂停，请先点击恢复连接"; return; }
  if (!token.value.trim()) { status.textContent = "请填写配对码"; return; }
  button.disabled = true;
  status.textContent = "正在连接本机程序…";
  try {
    await chrome.storage.local.set({ token: token.value.trim(), name: name.value.trim() });
    const result = await chrome.runtime.sendMessage({ type: "sync" });
    showSync(result);
  } catch { status.textContent = "连接失败，请确认桌面程序正在运行"; }
  finally { button.disabled = false; }
});
pause.addEventListener("click", async () => {
  pause.disabled = true;
  try {
    const result = await chrome.runtime.sendMessage({ type: "pause", paused: !paused });
    paused = Boolean(result.paused);
    pause.textContent = paused ? "恢复连接" : "暂停连接";
    if (!result.ok) throw new Error(result.error);
    status.textContent = paused ? "连接已暂停；恢复后将重新同步标签页" :
      `已恢复连接，发现 ${result.count} 个标签页。请在桌面确认监听。`;
  } catch (error) { status.textContent = error.message || "切换连接状态失败"; }
  finally { pause.disabled = false; }
});
