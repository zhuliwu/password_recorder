"use strict";
let recoveryView = 0, recovering = false;

function clearRecoveryForms() {
  recoveryView++;
  $("recovery-prepare-form").reset(); $("recovery-confirm-form").reset(); $("recover-form").reset();
  $("new-recovery-key").value = "";
}

async function refreshRecovery() {
  $("manage-recovery").disabled = true;
  try {
    const status = await api("/recovery");
    $("recovery-status").textContent = status.enabled ? "恢复密钥已启用" : "为保险库留一把备用钥匙";
    $("recovery-description").textContent = status.enabled ? "请妥善保管。丢失后可在这里生成并启用替代密钥。" : "忘记主密码时，提前保存的恢复密钥可以帮你保留账号数据。";
    $("manage-recovery").textContent = status.enabled ? "替换恢复密钥" : "设置恢复密钥";
  } catch (error) {
    $("recovery-status").textContent = "恢复设置状态未知";
    $("recovery-description").textContent = "读取失败，请刷新重试。";
    throw error;
  } finally { $("manage-recovery").disabled = false; }
}

$("manage-recovery").onclick = () => {
  clearRecoveryForms();
  $("prepare-error").textContent = $("confirm-recovery-error").textContent = "";
  $("recovery-prepare-form").hidden = false; $("recovery-confirm-form").hidden = true;
  $("recovery-settings").showModal(); $("recovery-master").focus();
};
$("close-recovery-settings").onclick = () => $("recovery-settings").close();
$("recovery-settings").addEventListener("close", () => {
  clearRecoveryForms();
  if (unlocked) api("/recovery/cancel", "POST", {}).catch(report);
});
$("recovery-prepare-form").onsubmit = async e => {
  e.preventDefault(); const version = recoveryView;
  $("prepare-recovery").disabled = true; $("prepare-error").textContent = "";
  try {
    const result = await api("/recovery/prepare", "POST", {password: $("recovery-master").value});
    if (version !== recoveryView || !$("recovery-settings").open) return;
    $("recovery-master").value = "";
    $("new-recovery-key").value = result.recovery_key;
    $("recovery-prepare-form").hidden = true; $("recovery-confirm-form").hidden = false;
    $("new-recovery-key").focus();
  } catch (error) { if (version === recoveryView) $("prepare-error").textContent = error.message; }
  finally { $("prepare-recovery").disabled = false; }
};
$("copy-recovery-key").onclick = () => copyText($("new-recovery-key").value).catch(report);
$("recovery-confirm-form").onsubmit = async e => {
  e.preventDefault(); $("confirm-recovery").disabled = true; $("confirm-recovery-error").textContent = "";
  try {
    await api("/recovery/confirm", "POST", {recovery_key: $("new-recovery-key").value, saved: $("recovery-saved").checked});
    await refreshRecovery(); $("recovery-settings").close(); toast("恢复密钥已启用，请妥善保管");
  } catch (error) { $("confirm-recovery-error").textContent = error.message; }
  finally { $("confirm-recovery").disabled = false; }
};
$("forgot-master").onclick = () => {
  $("recover-form").reset(); $("recover-error").textContent = "";
  $("recover-dialog").showModal(); $("recovery-key-input").focus();
};
$("close-recover").onclick = () => { if (!recovering) $("recover-dialog").close(); };
$("recover-dialog").addEventListener("cancel", e => { if (recovering) e.preventDefault(); });
$("recover-dialog").addEventListener("close", () => $("recover-form").reset());
$("recover-form").onsubmit = async e => {
  e.preventDefault(); $("recover-error").textContent = "";
  if ($("new-master").value !== $("new-master-confirm").value) { $("recover-error").textContent = "两次输入的新主密码不一致"; return; }
  recovering = true; $("submit-recover").disabled = $("close-recover").disabled = true;
  $("submit-recover").textContent = "正在恢复，请稍候…";
  try {
    await api("/recover", "POST", {recovery_key: $("recovery-key-input").value, new_password: $("new-master").value});
    lockChannel?.postMessage("locked");
    $("recover-dialog").close(); await showWorkspace();
    toast("主密码已重置，旧恢复密钥已作废，请重新设置恢复密钥");
  } catch (error) {
    const message = error.message.startsWith("无法连接") ? "恢复结果未确认，请先尝试用新主密码解锁；不要丢弃原恢复密钥。" : error.message;
    if ($("recover-dialog").open) $("recover-error").textContent = message; else report(error);
  } finally {
    recovering = false; $("submit-recover").disabled = $("close-recover").disabled = false;
    $("submit-recover").textContent = "重置主密码并解锁";
  }
};
