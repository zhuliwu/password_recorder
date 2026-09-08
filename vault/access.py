"""Explicit external origins; proxy headers are interpreted by Waitress, not Flask."""
import ipaddress
import re
from urllib.parse import urlsplit


def normalize_public_origin(value):
    if value is None:
        return None
    if not isinstance(value, str) or not value or any(c.isspace() for c in value):
        raise ValueError("公网地址必须是完整 HTTPS 地址，例如 https://203.0.113.10")
    try:
        url = urlsplit(value)
        port = url.port
        host = url.hostname
    except ValueError as error:
        raise ValueError("公网地址或端口格式不正确") from error
    if (url.scheme != "https" or not host or url.username is not None or url.password is not None
            or url.path not in ("", "/") or url.query or url.fragment or "%" in host
            or url.netloc.endswith(":") or "?" in value or "#" in value):
        raise ValueError("公网地址只允许 HTTPS、主机和可选端口，不能包含路径、账号、查询或片段")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("公网端口须在 1–65535 之间")
    try:
        address = ipaddress.ip_address(host)
        host = f"[{address.compressed}]" if address.version == 6 else str(address)
    except ValueError:
        try:
            host = host.encode("idna").decode("ascii").lower()
        except UnicodeError as error:
            raise ValueError("公网主机名格式不正确") from error
        labels = host.split(".")
        if (len(host) > 253 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                                  for label in labels) or re.fullmatch(r"[0-9.]+", host)):
            raise ValueError("公网 IP 或主机名格式不正确")
    return f"https://{host}" + (f":{port}" if port not in (None, 443) else "")


def waitress_options(public_origin=None):
    options = dict(host="127.0.0.1", threads=4, max_request_body_size=65536,
                   clear_untrusted_proxy_headers=True)
    if public_origin:
        options.update(trusted_proxy="127.0.0.1", trusted_proxy_count=1,
                       trusted_proxy_headers={"x-forwarded-proto"})
    return options
