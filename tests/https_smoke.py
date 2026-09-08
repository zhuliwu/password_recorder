"""Real Nginx + TLS + Waitress test on a non-loopback IP with a temporary vault.

Run: python3 tests/https_smoke.py --nginx /path/to/nginx
Only the ephemeral test certificate's SPKI is allowed in Chrome. Python separately
verifies the certificate chain and IP SAN against the ephemeral CA (no TLS bypass).
"""
import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from playwright.sync_api import expect, sync_playwright

from browser_smoke import MASTER, ROOT, recovery_flow, stop


def certificates(directory, ip):
    now = datetime.now(timezone.utc)
    root_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    root_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Ephemeral vault test CA")])
    ca = (x509.CertificateBuilder().subject_name(root_name).issuer_name(root_name)
          .public_key(root_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .sign(root_key, hashes.SHA256()))
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, ip)]))
            .issuer_name(root_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(ip))]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(root_key, hashes.SHA256()))
    ca_path, cert_path, key_path = directory / "ca.pem", directory / "server.pem", directory / "server.key"
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM) + ca.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    key_path.chmod(0o600)
    spki = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return ca_path, cert_path, key_path, base64.b64encode(hashlib.sha256(spki).digest()).decode()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--nginx", default=shutil.which("nginx"))
    args = parser.parse_args()
    if not args.nginx:
        parser.error("请通过 --nginx 指定可运行的 Nginx")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
        route.connect(("192.0.2.1", 9))  # Route lookup only; UDP payload is never sent.
        ip = route.getsockname()[0]
    assert not ipaddress.ip_address(ip).is_loopback
    ports = []
    sockets = []
    for _ in range(3):
        sock = socket.socket()
        sock.bind(("0.0.0.0", 0))
        ports.append(sock.getsockname()[1])
        sockets.append(sock)
    for sock in sockets:
        sock.close()
    backend_port, http_port, tls_port = ports
    origin = f"https://{ip}:{tls_port}"
    python = str(ROOT / ".venv/bin/python")
    with tempfile.TemporaryDirectory(prefix="vault-https-") as temp:
        directory = Path(temp)
        ca, cert, key, spki = certificates(directory, ip)
        data = directory / "data"
        # Initialize privately before enabling public mode; no public bootstrap endpoint.
        init_code = """
import sys
from vault import create_app
c=create_app(sys.argv[1]).test_client()
r=c.post('/api/setup',base_url='http://127.0.0.1:8765',json={'password':sys.stdin.read()},headers={'Origin':'http://127.0.0.1:8765','X-Vault-Request':'1'})
assert r.status_code==200
"""
        subprocess.run([python, "-c", init_code, str(data)], input=MASTER, text=True, cwd=ROOT, check=True)
        acme = directory / "acme"
        challenge = acme / ".well-known/acme-challenge/probe"
        challenge.parent.mkdir(parents=True)
        challenge.write_text("acme-test-response")
        server_conf = directory / "server.conf"
        subprocess.run([python, "-m", "vault.proxy_config", "--public-origin", origin,
                        "--certificate", str(cert), "--private-key", str(key), "--backend-port", str(backend_port),
                        "--http-port", str(http_port), "--acme-webroot", str(acme), "--output", str(server_conf)],
                       cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
        nginx_conf = directory / "nginx.conf"
        nginx_conf.write_text(f"""daemon off;
master_process off;
pid {directory}/nginx.pid;
error_log {directory}/error.log warn;
events {{ worker_connections 64; }}
http {{
  client_body_temp_path {directory}/body;
  proxy_temp_path {directory}/proxy;
  fastcgi_temp_path {directory}/fastcgi;
  uwsgi_temp_path {directory}/uwsgi;
  scgi_temp_path {directory}/scgi;
  include {server_conf};
}}
""")
        subprocess.run([args.nginx, "-t", "-p", str(directory), "-c", str(nginx_conf)], check=True, capture_output=True)
        command = [python, "-m", "vault", "--port", str(backend_port), "--data-dir", str(data), "--public-origin", origin]
        app = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.DEVNULL)
        nginx = subprocess.Popen([args.nginx, "-p", str(directory), "-c", str(nginx_conf)], stderr=subprocess.DEVNULL)
        context = ssl.create_default_context(cafile=str(ca))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context), NoRedirect())

        def request(url, method="GET", headers=None, payload=None):
            req = urllib.request.Request(url, method=method, headers=headers or {},
                                         data=json.dumps(payload).encode() if payload is not None else None)
            try:
                return opener.open(req, timeout=5)
            except urllib.error.HTTPError as error:
                return error

        try:
            deadline = time.monotonic() + 15
            while True:
                assert app.poll() is None and nginx.poll() is None, "Service exited"
                try:
                    with request(origin + "/api/status") as response:
                        if response.status == 200:
                            break
                except OSError:
                    pass
                if time.monotonic() > deadline:
                    raise AssertionError("HTTPS proxy readiness timeout")
                time.sleep(.1)
            http_origin = f"http://{ip}:{http_port}"
            with request(http_origin + "/") as response:
                assert response.status == 308 and response.headers["Location"] == origin + "/"
            with request(http_origin + "/api/unlock", "POST", payload={"password": MASTER}) as response:
                assert response.status == 403
            with request(http_origin + "/.well-known/acme-challenge/probe") as response:
                assert response.status == 200 and response.read() == b"acme-test-response"
            with request(origin + "/api/status", headers={"Host": "evil.example"}) as response:
                assert response.status == 403
            with request(origin + "/api/status", headers={"X-Forwarded-Proto": "http", "Forwarded": "proto=http;host=evil.example"}) as response:
                assert response.status == 200
            with request(origin + "/api/unlock", "POST", headers={"Origin": "https://evil.example", "Content-Type": "application/json", "X-Vault-Request": "1"}, payload={"password": MASTER}) as response:
                assert response.status == 403
            with request(f"http://127.0.0.1:{backend_port}/api/status", headers={"Host": f"{ip}:{tls_port}"}) as response:
                assert response.status == 403
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=shutil.which("google-chrome"),
                                              args=[f"--ignore-certificate-errors-spki-list={spki}", "--no-proxy-server"], headless=True)
                browser_context = browser.new_context(permissions=["clipboard-read", "clipboard-write"], ignore_https_errors=False,
                                                       viewport={"width": 1440, "height": 1000})
                page = browser_context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin)
                assert page.evaluate("window.isSecureContext") is True
                page.locator("#master").fill(MASTER)
                page.locator("#unlock-button").click()
                expect(page.locator("#workspace")).to_be_visible()
                cookie = next(c for c in browser_context.cookies() if c["name"] == "__Host-vault_session")
                assert cookie["secure"] and cookie["httpOnly"] and cookie["sameSite"] == "Strict"
                assert "服务端设备" in page.locator(".local-card").inner_text()
                page.locator("#add").click()
                page.locator("#title").fill("远程测试账号")
                page.locator("#username").fill("fictional-user")
                page.locator("#password").fill("fictional-password")
                page.locator("#save").click()
                expect(page.locator(".entry-row")).to_have_count(1)
                page.get_by_role("button", name="复制密码", exact=True).click()
                expect(page.locator("#toast")).to_contain_text("已复制")
                assert page.evaluate("navigator.clipboard.readText()") == "fictional-password"
                page.get_by_role("button", name="编辑账号", exact=True).click()
                page.locator("#title").fill("远程编辑成功")
                page.locator("#save").click()
                expect(page.locator(".entry-title")).to_have_text("远程编辑成功")
                # Let application error handling consume a real proxy-style non-JSON response.
                page.route("**/api/recovery/prepare", lambda route: route.fulfill(status=429, content_type="text/html", body="<h1>Too many requests</h1>"))
                page.locator("#manage-recovery").click()
                page.locator("#recovery-master").fill(MASTER)
                page.locator("#prepare-recovery").click()
                expect(page.locator("#prepare-error")).to_contain_text("请求过于频繁")
                page.locator("#close-recovery-settings").click()
                page.unroute("**/api/recovery/prepare")
                # Test the complete recovery flow without Nginx auth rate limits hiding functional results.
                # Rate limiting itself is tested below; temporarily increase its test-only budget.
                server_conf.write_text(server_conf.read_text().replace("rate=10r/m", "rate=600r/m").replace("burst=5", "burst=100"))
                stop(nginx)
                nginx = subprocess.Popen([args.nginx, "-p", str(directory), "-c", str(nginx_conf)], stderr=subprocess.DEVNULL)
                for _ in range(50):
                    try:
                        with request(origin + "/api/status") as response:
                            if response.status == 200:
                                break
                    except OSError:
                        time.sleep(.1)
                def lose_recovery_response(route):
                    # Use the CA-verifying Python client; Chrome's SPKI exception does
                    # not extend to Playwright's separate HTTP request client.
                    headers = {name: value for name, value in route.request.headers.items()
                               if name.lower() not in {"content-length", "connection"}}
                    with request(route.request.url, method="POST", headers=headers,
                                 payload=route.request.post_data_json) as response:
                        assert response.status == 200
                    route.fulfill(status=502, content_type="text/html", body="<h1>Bad Gateway</h1>")

                new_master = recovery_flow(page, lost_response=lose_recovery_response)
                stop(app)
                app = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.DEVNULL)
                for _ in range(50):
                    with request(origin + "/api/status") as response:
                        if response.status == 200:
                            break
                    time.sleep(.1)
                page.reload()
                page.locator("#master").fill(new_master)
                page.locator("#unlock-button").click()
                expect(page.locator(".entry-row")).to_have_count(1)
                page.get_by_role("button", name="删除账号", exact=True).click()
                page.locator("#confirm-delete").click()
                expect(page.locator(".entry-row")).to_have_count(0)
                assert not errors, errors
                browser.close()
            # Use a rejected Origin to avoid changing app authentication counters during proxy rate checks.
            server_conf.write_text(server_conf.read_text().replace("rate=600r/m", "rate=10r/m").replace("burst=100", "burst=5"))
            stop(nginx)
            nginx = subprocess.Popen([args.nginx, "-p", str(directory), "-c", str(nginx_conf)], stderr=subprocess.DEVNULL)
            for _ in range(50):
                try:
                    with request(origin + "/api/status") as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(.1)
            statuses = []
            for _ in range(8):
                with request(origin + "/api/unlock", "POST", payload={}, headers={"Origin": "https://evil.example"}) as response:
                    statuses.append(response.status)
            assert 429 in statuses
            print(f"HTTPS smoke passed on non-loopback {ip}: Nginx TLS/IP SAN, redirect, ACME, proxy trust, Secure cookies, CRUD, clipboard, recovery, restart, rate limit. Public IP/CA deployment is not covered.")
        finally:
            stop(nginx)
            stop(app)


if __name__ == "__main__":
    main()
