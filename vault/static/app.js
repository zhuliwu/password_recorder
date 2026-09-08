"use strict";
const $ = (id) => document.getElementById(id);
let initialized = false, unlocked = false, entries = [], filter = "all", editingId = null, currentDetail = null, deletingId = null;
let epoch = 0, idleSeconds = 900, lastActivity = Date.now(), lastTouch = 0, toastTimer;
const lockChannel = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel("vault-lock") : null;
if (lockChannel) lockChannel.onmessage = () => { if (unlocked) showGate(); };
const icons = {
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V4H4v12h4"/>',
  edit: '<path d="m14 4 6 6M4 20l5-1L21 7l-4-4L5 15z"/>',
  delete: '<path d="M3 6h18M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7M14 10v7"/>',
  star: '<path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9z"/>'
};
function toast(message, error = false) {
  clearTimeout(toastTimer); $("toast").textContent = message; $("toast").className = `toast${error ? " error-toast" : ""}`;
  $("toast").hidden = false; toastTimer = setTimeout(() => $("toast").hidden = true, error ? 7000 : 3000);
}
async function api(path, method = "GET", body) {
  const version = epoch;
  let response;
  try { response = await fetch(`/api${path}`, {method, headers: {"Content-Type": "application/json", "X-Vault-Request": "1"}, body: body === undefined ? undefined : JSON.stringify(body)}); }
  catch { throw new Error("无法连接保险库服务，请确认服务已启动或联系管理员后重试。"); }
  if (version !== epoch) throw new DOMException("会话已变更", "AbortError");
  let data;
  try { data = await response.json(); }
  catch {
    const message = response.status === 429 ? "请求过于频繁，请稍后重试。" :
      response.status === 413 ? "请求内容过大，请减少输入内容。" :
      [502, 503, 504].includes(response.status) ? "服务暂时无法响应，请稍后重试或联系服务管理员。" : "服务响应异常，请刷新页面后重试。";
    const error = new Error(message);
    error.resultUnknown = ![413, 429].includes(response.status);
    throw error;
  }
  if (!response.ok) {
    if (response.status === 401 && !["/unlock", "/setup", "/recover"].includes(path)) showGate();
    throw new Error(data.error || "操作失败，请重试");
  }
  return data;
}
function report(error) { if (error.name !== "AbortError") toast(error.message, true); }
function showGate() {
  epoch++; unlocked = false; entries = []; currentDetail = null; editingId = null; deletingId = null;
  document.querySelectorAll("dialog[open]").forEach(d => d.close());
  $("entry-form").reset(); $("detail-body").replaceChildren(); $("entry-list").replaceChildren();
  clearRecoveryForms();
  $("workspace").hidden = true; $("gate").hidden = false; $("unlock-form").reset();
  $("gate-title").textContent = initialized ? "欢迎回来" : "创建你的保险库";
  $("gate-description").textContent = initialized ? "输入主密码，解锁你的私人空间。" : "设置一个主密码，开始安全保存账号。";
  $("confirm-wrap").hidden = initialized; $("master-confirm").required = !initialized;
  $("forgot-master").hidden = !initialized;
  $("master").autocomplete = initialized ? "current-password" : "new-password";
  $("unlock-button").textContent = initialized ? "解锁保险库" : "创建保险库";
  $("unlock-button").disabled = false; $("gate-error").textContent = ""; $("master").focus();
}
async function loadEntries() {
  try { entries = (await api("/entries")).entries; render(); }
  catch (error) {
    entries = []; $("entry-list").replaceChildren(); $("empty").hidden = true;
    $("total").textContent = $("favorite-total").textContent = "未知";
    $("result-count").textContent = "读取失败，请刷新重试"; throw error;
  }
}
async function showWorkspace() {
  unlocked = true; initialized = true; lastActivity = Date.now();
  $("unlock-form").reset(); $("gate").hidden = true; $("workspace").hidden = false;
  await loadEntries();
  await refreshRecovery();
}
$("unlock-form").addEventListener("submit", async e => {
  e.preventDefault(); $("gate-error").textContent = "";
  if (!initialized && $("master").value !== $("master-confirm").value) { $("gate-error").textContent = "两次输入的主密码不一致"; return; }
  $("unlock-button").disabled = true; $("unlock-button").textContent = "正在解锁…";
  try { await api(initialized ? "/unlock" : "/setup", "POST", {password: $("master").value}); await showWorkspace(); }
  catch (error) { $("gate-error").textContent = error.message; if (unlocked) report(error); }
  finally { $("unlock-button").disabled = false; $("unlock-button").textContent = initialized ? "解锁保险库" : "创建保险库"; }
});
async function lockVault(automatic = false) {
  try { await api("/lock", "POST", {}); showGate(); lockChannel?.postMessage("locked"); toast(automatic ? "长时间未操作，保险库已自动锁定" : "保险库已锁定"); }
  catch (error) { showGate(); report(new Error(`界面已清空，服务端锁定未确认：${error.message}`)); }
}
$("lock").onclick = () => lockVault();
function element(tag, className, content) {
  const el = document.createElement(tag); if (className) el.className = className; if (content !== undefined) el.textContent = content; return el;
}
function iconButton(icon, label, action) {
  const button = element("button", "icon-button"); button.type = "button"; button.title = label; button.setAttribute("aria-label", label);
  button.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${icons[icon]}</svg>`;
  button.onclick = async () => { button.disabled = true; try { await action(); } catch(error) { report(error); } finally { button.disabled = false; } }; return button;
}
function render() {
  const query = $("search").value.trim().toLocaleLowerCase();
  const shown = entries.filter(e => (filter === "all" || (filter === "favorites" ? e.favorite : e.category === filter)) &&
    [e.title, e.username, e.url, e.notes].some(v => v.toLocaleLowerCase().includes(query)));
  if ($("sort").value === "name") shown.sort((a, b) => a.title.localeCompare(b.title, "zh-CN"));
  $("total").textContent = $("nav-total").textContent = entries.length;
  $("favorite-total").textContent = $("nav-favorites").textContent = entries.filter(e => e.favorite).length;
  $("entry-list").replaceChildren();
  for (const entry of shown) {
    const row = element("article", "entry-row");
    const name = element("button", "entry-name"); name.type = "button"; name.setAttribute("aria-label", `查看 ${entry.title}`);
    name.onclick = () => openDetail(entry.id).catch(report);
    name.append(element("span", "avatar", Array.from(entry.title)[0].toUpperCase()));
    const text = element("span", "name-text"); text.append(element("span", "entry-title", entry.title));
    let site = "未设置网址";
    if (entry.url) { try { site = new URL(entry.url).hostname; } catch { site = entry.url; } }
    text.append(element("span", "entry-site", site)); name.append(text);
    const actions = element("div", "row-actions");
    const star = iconButton("star", entry.favorite ? "取消收藏" : "收藏", async () => {
      const full = await api(`/entries/${entry.id}`); const {id, created_at, updated_at, ...fields} = full;
      await api(`/entries/${entry.id}`, "PUT", {...fields, favorite: !entry.favorite}); await loadEntries();
    }); if (entry.favorite) star.classList.add("starred");
    actions.append(star, iconButton("copy", "复制密码", () => copyPassword(entry.id)), iconButton("edit", "编辑账号", () => openEditor(entry.id)), iconButton("delete", "删除账号", () => openDelete(entry)));
    row.append(name, element("span", "entry-username", entry.username), element("span", "category-pill", entry.category), actions); $("entry-list").append(row);
  }
  $("empty").hidden = shown.length !== 0;
  const isFirst = !entries.length && !query && filter === "all";
  $("empty").querySelector("h2").textContent = isFirst ? "给重要的账号一个家" : "没有找到匹配的账号";
  $("empty").querySelector("p").textContent = isFirst ? "添加第一个账号，让每次登录都从容一点。" : "试试其他关键词，或切换分类。";
  $("empty-add").hidden = !isFirst;
  $("result-count").textContent = `显示 ${shown.length} 条，共 ${entries.length} 条账号`;
}
document.querySelectorAll("[data-filter]").forEach(button => button.onclick = () => {
  filter = button.dataset.filter; document.querySelectorAll("[data-filter]").forEach(b => b.classList.toggle("active", b === button));
  $("page-title").textContent = $("crumb").textContent = filter === "all" ? "所有账号" : filter === "favorites" ? "我的收藏" : filter; render();
});
$("search").oninput = render; $("sort").onchange = render;
async function openEditor(id = null) {
  const entry = id ? await api(`/entries/${id}`) : null;
  editingId = id; $("entry-form").reset(); $("editor-error").textContent = "";
  $("password").type = "password"; $("toggle-password").textContent = "显示";
  $("editor-title").textContent = id ? "编辑账号" : "添加账号";
  if (entry) { for (const key of ["title", "username", "password", "url", "category", "notes"]) $(key).value = entry[key]; $("favorite").checked = entry.favorite; }
  $("editor").showModal(); $("title").focus();
}
$("add").onclick = $("empty-add").onclick = () => openEditor().catch(report);
document.querySelectorAll(".close-editor").forEach(b => b.onclick = () => $("editor").close());
$("editor").addEventListener("close", () => { $("entry-form").reset(); editingId = null; });
$("toggle-password").onclick = () => { const hidden = $("password").type === "password"; $("password").type = hidden ? "text" : "password"; $("toggle-password").textContent = hidden ? "隐藏" : "显示"; };
$("generate").onclick = async () => { try { $("password").value = (await api("/generate")).password; toast("已生成 20 位随机密码"); } catch (error) { report(error); } };
$("entry-form").onsubmit = async e => {
  e.preventDefault(); $("save").disabled = true; $("editor-error").textContent = "";
  const data = Object.fromEntries(new FormData(e.target)); data.favorite = $("favorite").checked;
  try {
    await api(editingId ? `/entries/${editingId}` : "/entries", editingId ? "PUT" : "POST", data);
    $("editor").close(); toast("账号已保存"); await loadEntries();
  } catch (error) { if ($("editor").open) $("editor-error").textContent = error.message; else report(error); }
  finally { $("save").disabled = false; }
};
async function copyText(value) {
  if (!window.isSecureContext) throw new Error("当前地址不是安全上下文，请使用证书受信任的 HTTPS 地址，或在服务本机通过 localhost 打开；也可显示密码后手动复制。");
  if (!navigator.clipboard?.writeText) throw new Error("此浏览器不支持剪贴板，请显示密码后手动复制。");
  try { await navigator.clipboard.writeText(value); toast("已复制，请留意系统剪贴板历史"); }
  catch (error) { throw new Error(error.name === "NotAllowedError" ? "浏览器拒绝复制，请允许剪贴板权限，或显示后手动复制。" : "复制执行失败，请显示密码后手动复制。"); }
}
async function copyPassword(id) {
  if (!window.isSecureContext || !navigator.clipboard?.writeText) return copyText("");
  const entry = await api(`/entries/${id}`); await copyText(entry.password);
}
async function openDetail(id) {
  const entry = await api(`/entries/${id}`); currentDetail = entry; $("detail-title").textContent = entry.title; $("detail-body").replaceChildren();
  for (const [label, value] of [["登录账号", entry.username], ["分类", entry.category], ["网址", entry.url || "未设置"], ["备注", entry.notes || "无备注"], ["最后更新", new Date(entry.updated_at * 1000).toLocaleString("zh-CN")]]) {
    const field = element("dl", "detail-field"); const dd = element("dd");
    if (label === "网址" && entry.url) { const a = element("a", "", value); a.href = value; a.target = "_blank"; a.rel = "noopener noreferrer"; dd.append(a); } else dd.textContent = value;
    field.append(element("dt", "", label), dd); $("detail-body").append(field);
  }
  const field = element("dl", "detail-field"); const dd = element("dd", "detail-password"); const password = element("code", "", "••••••••••••");
  const reveal = element("button", "secondary", "显示"); reveal.onclick = () => { const show = reveal.textContent === "显示"; password.textContent = show ? entry.password : "••••••••••••"; reveal.textContent = show ? "隐藏" : "显示"; };
  dd.append(password, reveal, iconButton("copy", "复制此密码", () => copyText(entry.password))); field.append(element("dt", "", "密码"), dd); $("detail-body").append(field); $("details").showModal();
}
$("close-details").onclick = () => $("details").close();
$("details").addEventListener("close", () => { currentDetail = null; $("detail-body").replaceChildren(); });
$("detail-edit").onclick = () => { const id = currentDetail.id; $("details").close(); openEditor(id).catch(report); };
function openDelete(entry) { deletingId = entry.id; $("delete-description").textContent = `即将从保险库删除「${entry.title}」。`; $("delete-error").textContent = ""; $("delete-dialog").showModal(); $("cancel-delete").focus(); }
$("cancel-delete").onclick = () => $("delete-dialog").close();
$("confirm-delete").onclick = async () => {
  $("confirm-delete").disabled = true;
  try { await api(`/entries/${deletingId}`, "DELETE", {}); $("delete-dialog").close(); toast("账号已删除"); await loadEntries(); }
  catch (error) { if ($("delete-dialog").open) $("delete-error").textContent = error.message; else report(error); }
  finally { $("confirm-delete").disabled = false; }
};
for (const event of ["pointerdown", "keydown", "scroll"]) document.addEventListener(event, () => {
  lastActivity = Date.now();
  if (unlocked && Date.now() - lastTouch > 30000) { lastTouch = Date.now(); api("/touch", "POST", {}).catch(report); }
}, {passive: true});
setInterval(() => { if (unlocked && Date.now() - lastActivity >= idleSeconds * 1000) lockVault(true); }, 1000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && unlocked) api("/status").then(s => { if (!s.unlocked) showGate(); }).catch(error => { showGate(); report(error); });
});
window.addEventListener("pageshow", e => { if (e.persisted) location.reload(); });
document.addEventListener("DOMContentLoaded", () => {
  api("/status").then(async status => { initialized = status.initialized; idleSeconds = status.idle_seconds; if (status.unlocked) await showWorkspace(); else showGate(); }).catch(error => { $("gate-title").textContent = "连接失败"; $("gate-error").textContent = error.message; });
});
