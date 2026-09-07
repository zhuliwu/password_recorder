import json
import os
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import closing, contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidTag
from flask import Flask, g, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from .crypto import decrypt, derive_key, encrypt
from .recovery import RECOVERY_CONTEXT, generate_recovery_key, parse_recovery_key

CATEGORIES = ("工作", "个人", "财务", "社交", "其他")
IDLE_SECONDS = 15 * 60


class APIError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def validate_entry(data):
    limits = {"title": 120, "username": 320, "password": 2048,
              "url": 2048, "notes": 10000, "category": 20}
    if set(data) - (set(limits) | {"favorite"}):
        raise APIError("包含不支持的字段")
    result = {}
    for field, limit in limits.items():
        value = data.get(field, "其他" if field == "category" else "")
        if not isinstance(value, str) or len(value) > limit:
            raise APIError(f"字段 {field} 类型或长度不正确")
        result[field] = value if field == "password" else value.strip()
    if not all(result[f] for f in ("title", "username", "password")):
        raise APIError("名称、账号和密码不能为空")
    if result["category"] not in CATEGORIES:
        raise APIError("分类不正确")
    if result["url"]:
        try:
            url = urlsplit(result["url"])
            valid = (url.scheme in ("https", "http") and url.hostname and not url.username and not url.password
                     and not any(c.isspace() or ord(c) < 32 or c == "\\" for c in result["url"]))
            _ = url.port  # Reject malformed or out-of-range ports.
        except ValueError:
            valid = False
        if not valid:
            raise APIError("网址须为有效的 http:// 或 https:// 地址，且不能包含登录凭据")
    if not isinstance(data.get("favorite", False), bool):
        raise APIError("收藏状态不正确")
    result["favorite"] = data.get("favorite", False)
    return result


def create_app(data_dir=None, port=8765):
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=65536, IDLE_SECONDS=IDLE_SECONDS)
    directory = Path(data_dir or "data").resolve()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    db_path = directory / "vault.db"
    # Create with private permissions before SQLite opens the file.
    fd = os.open(db_path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    os.chmod(db_path, 0o600)
    with closing(sqlite3.connect(db_path)) as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (id INTEGER PRIMARY KEY CHECK(id=1), salt BLOB NOT NULL, verifier BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS entries (id TEXT PRIMARY KEY, payload BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS recovery (id INTEGER PRIMARY KEY CHECK(id=1), wrapped_key BLOB NOT NULL);
        """)
    sessions = {}
    guard = threading.RLock()
    expiry_timer = [None]
    attempts = {"count": 0, "until": 0}
    app.extensions["vault_sessions"] = sessions

    @contextmanager
    def connect():
        db = sqlite3.connect(db_path)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA secure_delete=ON")
            with db:
                yield db
        finally:
            db.close()

    def metadata():
        with connect() as db:
            return db.execute("SELECT salt, verifier FROM metadata WHERE id=1").fetchone()

    def session():
        now = time.monotonic()
        with guard:
            for sid in list(sessions):
                if now - sessions[sid]["last"] >= app.config["IDLE_SECONDS"]:
                    del sessions[sid]
            return sessions.get(request.cookies.get("vault_session"))

    @app.before_request
    def protect():
        if request.host not in (f"localhost:{port}", f"127.0.0.1:{port}"):
            raise APIError("不允许的 Host", 403)
        # Serialize local API work so logout cannot race an in-flight write.
        if request.path.startswith("/api/"):
            guard.acquire()
            g.vault_guard_held = True
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get("Origin") != f"http://{request.host}":
                raise APIError("请求来源验证失败，请刷新页面", 403)
            if request.mimetype != "application/json":
                raise APIError("仅接受 JSON 请求", 415)
            if request.headers.get("X-Vault-Request") != "1":
                raise APIError("请求校验失败", 403)
            if not isinstance(request.get_json(silent=True), dict):
                raise APIError("JSON 请求格式不正确")
        public = {"/api/status", "/api/setup", "/api/unlock", "/api/recover"}
        if request.path.startswith("/api/") and request.path not in public:
            g.vault_session = session()
            if not g.vault_session:
                raise APIError("保险库已锁定，请重新解锁", 401)
            g.vault_session["last"] = time.monotonic()

    @app.teardown_request
    def release_guard(error):
        if g.pop("vault_guard_held", False):
            guard.release()

    @app.after_request
    def headers(response):
        response.headers.update({
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        })
        return response

    @app.errorhandler(APIError)
    def api_error(error):
        return jsonify(error=error.message), error.status

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error="请求无效" if error.code != 413 else "请求内容过大"), error.code

    @app.errorhandler(sqlite3.Error)
    def db_error(error):
        return jsonify(error="本地数据库读写失败，请检查磁盘空间和数据目录权限"), 503

    @app.errorhandler(InvalidTag)
    def corruption(error):
        return jsonify(error="数据完整性校验失败，请停止修改并检查备份"), 500

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/status")
    def status():
        active = session()
        return jsonify(initialized=metadata() is not None, unlocked=active is not None,
                       idle_seconds=app.config["IDLE_SECONDS"])

    def password_input(field="password"):
        value = request.get_json().get(field)
        if not isinstance(value, str) or not 12 <= len(value) <= 1024:
            raise APIError("主密码须为 12–1024 个字符")
        return value

    def check_attempts():
        if time.monotonic() < attempts["until"]:
            raise APIError("尝试过于频繁，请 30 秒后重试", 429)

    def failed_attempt(message, status=401):
        attempts["count"] += 1
        if attempts["count"] >= 5:
            attempts.update(count=0, until=time.monotonic() + 30)
        raise APIError(message, status)

    def recovery_input():
        try:
            return parse_recovery_key(request.get_json().get("recovery_key"))
        except ValueError as error:
            raise APIError(str(error))

    def login(key):
        with guard:
            if expiry_timer[0]:
                expiry_timer[0].cancel()
            sessions.clear()  # A single local vault has one active browser session.
            token = secrets.token_urlsafe(32)
            sessions[token] = {"key": key, "last": time.monotonic()}

            def expire():
                with guard:
                    active = sessions.get(token)
                    if not active:
                        return
                    remaining = app.config["IDLE_SECONDS"] - (time.monotonic() - active["last"])
                    if remaining <= 0:
                        sessions.pop(token, None)
                    else:
                        expiry_timer[0] = threading.Timer(remaining, expire)
                        expiry_timer[0].daemon = True
                        expiry_timer[0].start()

            expiry_timer[0] = threading.Timer(app.config["IDLE_SECONDS"], expire)
            expiry_timer[0].daemon = True
            expiry_timer[0].start()
        response = jsonify(ok=True)
        response.set_cookie("vault_session", token, httponly=True, samesite="Strict", path="/")
        return response

    @app.post("/api/setup")
    def setup():
        password = password_input()
        with guard:
            if metadata():
                raise APIError("保险库已创建，请解锁", 409)
            salt = os.urandom(16)
            key = derive_key(password, salt)
            with connect() as db:
                db.execute("INSERT INTO metadata VALUES (1, ?, ?)",
                           (salt, encrypt(key, b"vault-v1", b"master-verifier-v1")))
            return login(key)

    @app.post("/api/unlock")
    def unlock():
        password = password_input()
        with guard:
            check_attempts()
            meta = metadata()
            if not meta:
                raise APIError("请先创建保险库", 409)
            key = derive_key(password, meta["salt"])
            try:
                decrypt(key, meta["verifier"], b"master-verifier-v1")
            except InvalidTag:
                failed_attempt("主密码不正确，或保险库校验数据已损坏")
            attempts.update(count=0, until=0)
            return login(key)

    @app.get("/api/recovery")
    def recovery_status():
        with connect() as db:
            enabled = db.execute("SELECT 1 FROM recovery WHERE id=1").fetchone() is not None
        return jsonify(enabled=enabled)

    @app.post("/api/recovery/prepare")
    def recovery_prepare():
        check_attempts()
        password = password_input()
        meta = metadata()
        key = derive_key(password, meta["salt"])
        try:
            decrypt(key, meta["verifier"], b"master-verifier-v1")
        except InvalidTag:
            failed_attempt("主密码不正确，无法生成恢复密钥", 403)
        attempts.update(count=0, until=0)
        recovery_key, formatted = generate_recovery_key()
        # Preparing a replacement never invalidates the user's active recovery key.
        g.vault_session["pending_recovery"] = {
            "wrapped_key": encrypt(recovery_key, key, RECOVERY_CONTEXT),
            "expires": time.monotonic() + 300,
        }
        return jsonify(recovery_key=formatted, expires_in=300)

    @app.post("/api/recovery/confirm")
    def recovery_confirm():
        recovery_key = recovery_input()
        if request.get_json().get("saved") is not True:
            raise APIError("请先确认已在保险库以外妥善保存恢复密钥")
        pending = g.vault_session.get("pending_recovery")
        if not pending or time.monotonic() >= pending["expires"]:
            g.vault_session.pop("pending_recovery", None)
            raise APIError("待确认的恢复密钥已失效，请重新生成", 409)
        try:
            key = decrypt(recovery_key, pending["wrapped_key"], RECOVERY_CONTEXT)
        except InvalidTag:
            raise APIError("密钥与本次生成的恢复密钥不一致")
        if not secrets.compare_digest(key, g.vault_session["key"]):
            raise APIError("会话已变更，请重新生成", 409)
        with connect() as db:
            db.execute("INSERT OR REPLACE INTO recovery VALUES (1, ?)", (pending["wrapped_key"],))
        # Keep the encrypted pending value briefly so a lost response can be retried.
        return jsonify(ok=True, enabled=True)

    @app.post("/api/recovery/cancel")
    def recovery_cancel():
        g.vault_session.pop("pending_recovery", None)
        return jsonify(ok=True)

    @app.post("/api/recover")
    def recover():
        check_attempts()
        recovery_key = recovery_input()
        new_password = password_input("new_password")
        with connect() as db:
            row = db.execute("SELECT wrapped_key FROM recovery WHERE id=1").fetchone()
            if not row:
                raise APIError("当前保险库未启用恢复密钥，或密钥已使用。请使用主密码解锁。", 409)
            try:
                old_key = decrypt(recovery_key, row["wrapped_key"], RECOVERY_CONTEXT)
            except InvalidTag:
                failed_attempt("恢复密钥不正确，或恢复数据已损坏")
            meta = db.execute("SELECT salt, verifier FROM metadata WHERE id=1").fetchone()
            decrypt(old_key, meta["verifier"], b"master-verifier-v1")
            # Every record, master verifier, and consumed recovery key change in one transaction.
            salt = os.urandom(16)
            new_key = derive_key(new_password, salt)
            for record in db.execute("SELECT id, payload FROM entries").fetchall():
                context = record["id"].encode()
                plaintext = decrypt(old_key, record["payload"], context)
                db.execute("UPDATE entries SET payload=? WHERE id=?",
                           (encrypt(new_key, plaintext, context), record["id"]))
            db.execute("UPDATE metadata SET salt=?, verifier=? WHERE id=1",
                       (salt, encrypt(new_key, b"vault-v1", b"master-verifier-v1")))
            db.execute("DELETE FROM recovery WHERE id=1")
        attempts.update(count=0, until=0)
        return login(new_key)

    @app.post("/api/lock")
    def lock():
        with guard:
            sessions.clear()
            if expiry_timer[0]:
                expiry_timer[0].cancel()
        response = jsonify(ok=True)
        response.delete_cookie("vault_session")
        return response

    @app.post("/api/touch")
    def touch():
        return jsonify(ok=True)

    def decode(row):
        return json.loads(decrypt(g.vault_session["key"], row["payload"], row["id"].encode()))

    def encode(entry):
        return encrypt(g.vault_session["key"], json.dumps(entry, ensure_ascii=False).encode(), entry["id"].encode())

    @app.get("/api/entries")
    def entries():
        with connect() as db:
            records = [decode(row) for row in db.execute("SELECT * FROM entries")]
        for record in records:
            record.pop("password")
        records.sort(key=lambda r: r["updated_at"], reverse=True)
        return jsonify(entries=records)

    @app.post("/api/entries")
    def create():
        entry = validate_entry(request.get_json())
        entry.update(id=uuid.uuid4().hex, created_at=int(time.time()), updated_at=int(time.time()))
        with connect() as db:
            db.execute("INSERT INTO entries VALUES (?, ?)", (entry["id"], encode(entry)))
        return jsonify(id=entry["id"]), 201

    def get_entry(entry_id):
        with connect() as db:
            row = db.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        if not row:
            raise APIError("记录不存在，可能已被删除", 404)
        return decode(row)

    @app.get("/api/entries/<entry_id>")
    def detail(entry_id):
        return jsonify(get_entry(entry_id))

    @app.put("/api/entries/<entry_id>")
    def update(entry_id):
        existing = get_entry(entry_id)
        entry = validate_entry(request.get_json())
        entry.update(id=entry_id, created_at=existing["created_at"], updated_at=int(time.time()))
        with connect() as db:
            db.execute("UPDATE entries SET payload=? WHERE id=?", (encode(entry), entry_id))
        return jsonify(ok=True)

    @app.delete("/api/entries/<entry_id>")
    def delete(entry_id):
        with connect() as db:
            if db.execute("DELETE FROM entries WHERE id=?", (entry_id,)).rowcount == 0:
                raise APIError("记录不存在，可能已被删除", 404)
        return jsonify(ok=True)

    @app.get("/api/generate")
    def generate():
        alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%&*?"
        while True:
            password = "".join(secrets.choice(alphabet) for _ in range(20))
            if all(any(c in group for c in password) for group in
                   ("abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "0123456789", "!@#$%&*?")):
                return jsonify(password=password)

    return app
