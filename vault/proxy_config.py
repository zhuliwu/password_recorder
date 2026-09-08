"""Generate a reviewed Nginx server block; never install or reload system config."""
import argparse
import ipaddress
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

from .access import normalize_public_origin


def render_nginx(public_origin, certificate, private_key, backend_port=8765, http_port=80,
                 acme_webroot="/var/lib/password-recorder-acme"):
    origin = normalize_public_origin(public_origin)
    if not origin:
        raise ValueError("必须指定公网 HTTPS 地址")
    url = urlsplit(origin)
    for value in (certificate, private_key, acme_webroot):
        if not re.fullmatch(r"/[A-Za-z0-9_./-]+", str(value)):
            raise ValueError("证书路径须为绝对路径，且只能含字母、数字、下划线、点、横线和斜线")
    tls_port = url.port or 443
    if not 1024 <= backend_port <= 65535 or not 1 <= http_port <= 65535:
        raise ValueError("后端端口须为 1024–65535；HTTP 端口须为 1–65535")
    if len({backend_port, http_port, tls_port}) != 3:
        raise ValueError("HTTP、HTTPS 和后端端口不能重复")
    try:
        address = ipaddress.ip_address(url.hostname)
        default_server = " default_server"  # Browsers commonly omit TLS SNI for IP literals.
        nginx_host = f"[{address}]" if address.version == 6 else str(address)
    except ValueError:
        default_server = ""
        nginx_host = url.hostname
    # Preserve the exact authority instead of accepting arbitrary forwarded hosts.
    return f"""# Generated for {origin}. Include inside Nginx's http {{}} context.
# This file contains no private key material. Nginx must run on the app server.
limit_req_zone $binary_remote_addr zone=password_recorder_auth:1m rate=10r/m;

server {{
    listen {http_port};
    server_name {nginx_host};
    access_log off;
    if ($host != \"{nginx_host}\") {{ return 403; }}
    # Never redirect passwords submitted over plaintext HTTP.
    if ($request_method !~ ^(GET|HEAD)$) {{ return 403; }}
    location ^~ /.well-known/acme-challenge/ {{
        root {acme_webroot};
        default_type text/plain;
        try_files $uri =404;
    }}
    location / {{ return 308 {origin}$request_uri; }}
}}

server {{
    listen {tls_port} ssl{default_server};
    server_name {nginx_host};
    ssl_certificate {certificate};
    ssl_certificate_key {private_key};
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_tickets off;
    server_tokens off;
    client_max_body_size 64k;
    if ($host != \"{nginx_host}\") {{ return 403; }}

    # No response caches or disk buffering of vault contents.
    proxy_cache off;
    proxy_buffering off;
    proxy_request_buffering off;
    proxy_max_temp_file_size 0;
    proxy_http_version 1.1;
    proxy_set_header Connection \"\";
    proxy_set_header Host \"{url.netloc}\";
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-For \"\";
    proxy_set_header X-Forwarded-Host \"\";
    proxy_set_header X-Forwarded-Port \"\";
    proxy_set_header Forwarded \"\";
    proxy_connect_timeout 5s;
    proxy_read_timeout 120s;
    # Avoid storing account identifiers or client-supplied query strings in access logs.
    access_log off;

    location ~ ^/api/(unlock|recover|recovery/prepare)$ {{
        limit_req zone=password_recorder_auth burst=5 nodelay;
        limit_req_status 429;
        proxy_pass http://127.0.0.1:{backend_port};
    }}
    location / {{
        proxy_pass http://127.0.0.1:{backend_port};
    }}
}}
"""


def main():
    parser = argparse.ArgumentParser(description="生成密匣 HTTPS 反向代理配置（不修改系统配置）")
    parser.add_argument("--public-origin", required=True)
    parser.add_argument("--certificate", required=True, type=Path)
    parser.add_argument("--private-key", required=True, type=Path)
    parser.add_argument("--backend-port", type=int, default=8765)
    parser.add_argument("--http-port", type=int, default=80)
    parser.add_argument("--acme-webroot", type=Path, default=Path("/var/lib/password-recorder-acme"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        content = render_nginx(args.public_origin, args.certificate, args.private_key,
                               args.backend_port, args.http_port, args.acme_webroot)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Atomic replacement keeps a failed generation from truncating an existing file.
        with tempfile.NamedTemporaryFile("w", dir=args.output.parent, delete=False, encoding="utf-8") as file:
            temporary = Path(file.name)
            file.write(content)
        try:
            os.replace(temporary, args.output)
        finally:
            temporary.unlink(missing_ok=True)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(f"配置已生成：{args.output}；安装前请核对证书路径，并执行 nginx -t。")


if __name__ == "__main__":
    main()
