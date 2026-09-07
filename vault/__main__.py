import argparse
import os
from pathlib import Path

from waitress import serve
from vault import create_app


def main():
    parser = argparse.ArgumentParser(description="密匣 · 本地账号密码管理")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args()
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        parser.error("请使用普通用户运行，不允许 root 启动。")
    if not 1024 <= args.port <= 65535:
        parser.error("端口须在 1024–65535 之间")
    app = create_app(args.data_dir, args.port)
    print(f"密匣已启动：http://127.0.0.1:{args.port} （Ctrl+C 停止）", flush=True)
    serve(app, host="127.0.0.1", port=args.port, threads=4, max_request_body_size=65536)


if __name__ == "__main__":
    main()
