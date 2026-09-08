import argparse
import os
from pathlib import Path

from waitress import serve
from vault import create_app
from vault.access import normalize_public_origin, waitress_options


def main():
    parser = argparse.ArgumentParser(description="密匣 · 本地账号密码管理")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--public-origin", help="启用同机 HTTPS 反向代理，例如 https://203.0.113.10")
    args = parser.parse_args()
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        parser.error("请使用普通用户运行，不允许 root 启动。")
    if not 1024 <= args.port <= 65535:
        parser.error("端口须在 1024–65535 之间")
    try:
        public_origin = normalize_public_origin(args.public_origin)
        app = create_app(args.data_dir, args.port, public_origin)
    except ValueError as error:
        parser.error(str(error))
    if public_origin:
        print(f"密匣公网入口：{public_origin}；后端仅监听 127.0.0.1:{args.port}，请配置同机 HTTPS 代理。", flush=True)
    else:
        print(f"密匣已启动：http://127.0.0.1:{args.port} （Ctrl+C 停止）", flush=True)
    serve(app, port=args.port, **waitress_options(public_origin))


if __name__ == "__main__":
    main()
