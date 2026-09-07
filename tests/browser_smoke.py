"""Real HTTP/Chrome smoke test; uses a temporary vault, never the user's database.

Run with a Python environment containing playwright; the app uses .venv/bin/python.
"""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "test-results"
RESULTS.mkdir(exist_ok=True)
MASTER = "browser test master 123!"


def wait_ready(port, process):
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError("Server exited before becoming ready")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=.5) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(.1)
    raise RuntimeError("Server did not become ready")


def stop(process):
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        process.wait()


def recovery_flow(page):
    new_master = "browser recovered master 987!"
    page.locator("#manage-recovery").click()
    page.locator("#recovery-master").fill("wrong master password")
    page.locator("#prepare-recovery").click()
    expect(page.locator("#prepare-error")).to_contain_text("不正确")
    page.locator("#recovery-master").fill(MASTER)
    page.locator("#prepare-recovery").click()
    expect(page.locator("#recovery-confirm-form")).to_be_visible()
    expect(page.locator("#recovery-master")).to_have_value("")
    page.locator("#close-recovery-settings").click()
    expect(page.locator("#new-recovery-key")).to_have_value("")
    expect(page.locator("#recovery-status")).to_contain_text("备用钥匙")
    page.locator("#manage-recovery").click()
    page.locator("#recovery-master").fill(MASTER)
    page.locator("#prepare-recovery").click()
    expect(page.locator("#recovery-confirm-form")).to_be_visible()
    key = page.locator("#new-recovery-key").input_value()
    assert key.startswith("RK1-") and len(key) == 75
    page.locator("#copy-recovery-key").click()
    expect(page.locator("#toast")).to_contain_text("已复制")
    assert page.evaluate("navigator.clipboard.readText()") == key
    page.set_viewport_size({"width": 375, "height": 812})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(RESULTS / "recovery-settings-mobile.png"), full_page=True, mask=[page.locator("#new-recovery-key")])
    page.locator("#confirm-recovery").click()
    expect(page.locator("#recovery-settings")).to_be_visible()  # Saving acknowledgement is required.
    page.locator("#recovery-saved").check()
    page.locator("#confirm-recovery").click()
    expect(page.locator("#recovery-settings")).not_to_be_visible()
    expect(page.locator("#recovery-status")).to_have_text("恢复密钥已启用")
    expect(page.locator("#new-recovery-key")).to_have_value("")
    page.locator("#lock").click()
    page.locator("#forgot-master").click()
    page.locator("#recovery-key-input").fill(key)
    page.locator("#new-master").fill(new_master)
    page.locator("#new-master-confirm").fill("mismatching password")
    page.locator("#submit-recover").click()
    expect(page.locator("#recover-error")).to_contain_text("不一致")
    page.locator("#new-master-confirm").fill(new_master)
    wrong_key = key[:-1] + ("0" if key[-1] != "0" else "1")
    page.locator("#recovery-key-input").fill(wrong_key)
    page.locator("#submit-recover").click()
    expect(page.locator("#recover-error")).to_contain_text("不正确")
    page.locator("#recovery-key-input").fill(key)
    page.locator("#submit-recover").click()
    expect(page.locator("#workspace")).to_be_visible()
    expect(page.locator(".entry-row")).to_have_count(1)
    expect(page.locator("#recovery-status")).to_contain_text("备用钥匙")
    expect(page.locator("#recovery-key-input")).to_have_value("")
    expect(page.locator("#new-master")).to_have_value("")
    page.locator("#lock").click()
    page.locator("#forgot-master").click()
    page.locator("#recovery-key-input").fill(key)
    page.locator("#new-master").fill(new_master)
    page.locator("#new-master-confirm").fill(new_master)
    page.locator("#submit-recover").click()
    expect(page.locator("#recover-error")).to_contain_text("已使用")
    page.locator("#close-recover").click()
    page.locator("#master").fill(MASTER)
    page.locator("#unlock-button").click()
    expect(page.locator("#gate-error")).to_contain_text("不正确")
    page.locator("#master").fill(new_master)
    page.locator("#unlock-button").click()
    expect(page.locator(".entry-row")).to_have_count(1)
    return new_master


def main():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="vault-browser-") as data:
        command = [str(ROOT / ".venv/bin/python"), "-m", "vault", "--port", str(port), "--data-dir", data]
        process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.DEVNULL)
        try:
            wait_ready(port, process)
            with sync_playwright() as pw:
                chrome = os.environ.get("CHROME_PATH") or shutil.which("google-chrome")
                browser = pw.chromium.launch(executable_path=chrome, headless=True)
                context = browser.new_context(viewport={"width": 1440, "height": 1000}, permissions=["clipboard-read", "clipboard-write"])
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{port}")
                expect(page.locator("#gate-title")).to_have_text("创建你的保险库")
                page.screenshot(path=str(RESULTS / "setup-desktop.png"), full_page=True)
                page.locator("#master").fill(MASTER)
                page.locator("#master-confirm").fill("mismatching password")
                page.locator("#unlock-button").click()
                expect(page.locator("#gate-error")).to_contain_text("不一致")
                page.locator("#master-confirm").fill(MASTER)
                page.locator("#unlock-button").click()
                expect(page.locator("#workspace")).to_be_visible()
                expect(page.locator("#empty")).to_be_visible()
                page.locator("#add").click()
                page.locator("#title").fill("工作邮箱")
                page.locator("#username").fill("demo@example.com")
                page.locator("#generate").click()
                expect(page.locator("#password")).not_to_have_value("")
                password = page.locator("#password").input_value()
                assert len(password) == 20
                page.locator("#url").fill("https://example.com")
                page.locator("#category").select_option("工作")
                page.locator("#notes").fill("仅用于自动化测试的虚构账号")
                page.locator("#favorite").check()
                page.locator("#save").click()
                expect(page.locator(".entry-row")).to_have_count(1)
                page.get_by_role("button", name="复制密码", exact=True).click()
                expect(page.locator("#toast")).to_contain_text("已复制")
                assert page.evaluate("navigator.clipboard.readText()") == password
                assert page.evaluate("window.isSecureContext") is True
                page.get_by_role("button", name="查看 工作邮箱").click()
                expect(page.locator("#detail-body code")).to_have_text("••••••••••••")
                page.locator("#detail-body").get_by_role("button", name="显示", exact=True).click()
                expect(page.locator("#detail-body code")).to_have_text(password)
                page.locator("#detail-edit").click()
                page.locator("#title").fill("团队邮箱")
                page.locator("#password").fill("updated-password-987!")
                page.locator("#save").click()
                expect(page.locator(".entry-title")).to_have_text("团队邮箱")
                page.locator("#search").fill("不存在")
                expect(page.locator(".entry-row")).to_have_count(0)
                page.locator("#search").fill("demo@example")
                expect(page.locator(".entry-row")).to_have_count(1)
                page.locator("#search").fill("")
                page.locator('[data-filter="个人"]').click()
                expect(page.locator(".entry-row")).to_have_count(0)
                page.locator('[data-filter="favorites"]').click()
                expect(page.locator(".entry-row")).to_have_count(1)
                page.locator('[data-filter="all"]').click()
                # Add a second record and verify text is never interpreted as markup.
                page.locator("#add").click()
                page.locator("#title").fill('<img src=x onerror="alert(1)">')
                page.locator("#username").fill("fictional-user")
                page.locator("#password").fill("fictional-password")
                page.locator("#save").click()
                expect(page.locator(".entry-row")).to_have_count(2)
                assert page.locator("#entry-list img").count() == 0
                page.locator("#sort").select_option("name")
                # Rename malicious-looking text for a clean, fictional UI screenshot.
                row = page.locator(".entry-row").filter(has_text="fictional-user")
                row.get_by_role("button", name="编辑账号").click()
                page.locator("#title").fill("个人笔记")
                page.locator("#save").click()
                expect(page.locator("#editor")).not_to_be_visible()
                page.screenshot(path=str(RESULTS / "vault-desktop.png"), full_page=True)
                for width, height in [(375, 812), (768, 1024), (1024, 768), (812, 375)]:
                    page.set_viewport_size({"width": width, "height": height})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (width, height)
                page.set_viewport_size({"width": 375, "height": 812})
                page.emulate_media(reduced_motion="reduce")
                page.screenshot(path=str(RESULTS / "vault-mobile.png"), full_page=True)
                page.locator("#add").click()
                expect(page.locator("#editor")).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.keyboard.press("Escape")
                expect(page.locator("#editor")).not_to_be_visible()
                page.set_viewport_size({"width": 1440, "height": 1000})
                second_tab = context.new_page()
                second_tab.goto(f"http://127.0.0.1:{port}")
                expect(second_tab.locator("#workspace")).to_be_visible()
                page.locator("#lock").click()
                expect(page.locator("#gate")).to_be_visible()
                expect(second_tab.locator("#gate")).to_be_visible()
                second_tab.close()
                assert page.locator("#entry-list").inner_text() == ""
                assert page.locator("#password").input_value() == ""
                page.locator("#master").fill("incorrect password")
                page.locator("#unlock-button").click()
                expect(page.locator("#gate-error")).to_contain_text("不正确")
                stop(process)
                process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.DEVNULL)
                wait_ready(port, process)
                page.reload()
                page.locator("#master").fill(MASTER)
                page.locator("#unlock-button").click()
                expect(page.locator(".entry-row")).to_have_count(2)
                row = page.locator(".entry-row").filter(has_text="demo@example.com")
                row.get_by_role("button", name="复制密码", exact=True).click()
                expect(page.locator("#toast")).to_contain_text("已复制")
                assert page.evaluate("navigator.clipboard.readText()") == "updated-password-987!"
                row.get_by_role("button", name="删除账号").click()
                page.locator("#cancel-delete").click()
                expect(page.locator(".entry-row")).to_have_count(2)
                row.get_by_role("button", name="删除账号").click()
                page.locator("#confirm-delete").click()
                expect(page.locator(".entry-row")).to_have_count(1)
                assert page.evaluate("localStorage.length") == 0
                assert page.evaluate("sessionStorage.length") == 0
                new_master = recovery_flow(page)
                stop(process)
                process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.DEVNULL)
                wait_ready(port, process)
                page.reload()
                page.locator("#master").fill(new_master)
                page.locator("#unlock-button").click()
                expect(page.locator(".entry-row")).to_have_count(1)
                page.get_by_role("button", name="复制密码", exact=True).click()
                expect(page.locator("#toast")).to_contain_text("已复制")
                assert page.evaluate("navigator.clipboard.readText()") == "fictional-password"
                assert page.evaluate("localStorage.length + sessionStorage.length") == 0
                assert not errors, errors
                print(f"Browser smoke passed: CRUD, restart persistence, clipboard, XSS, lock, responsive layouts, recovery enrollment/reset/consumption and new-password restart; Chrome {browser.version}")
                browser.close()
        finally:
            stop(process)


if __name__ == "__main__":
    main()
