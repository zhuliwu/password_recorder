# 密匣 · 本地账号密码管理

单人使用的中文 Web 应用：通过浏览器管理账号，数据存储于运行程序的电脑上的 SQLite 数据库。后端为 Python / Flask / Waitress，前端为原生 HTML、CSS 和 JavaScript，无前端构建或云服务依赖。

## 功能

- 新增、列表/详情查看、编辑、删除账号，删除前确认。
- 名称、登录账号、密码、网址、分类、备注、收藏。
- 搜索名称、账号、网址及备注，按分类/收藏筛选，按更新时间或名称排序。
- 密码默认隐藏、按需显示及复制；生成 20 位随机密码。
- 首次设置主密码，后续解锁；手动锁定，15 分钟无操作后过期。
- 解锁后生成恢复密钥，确认保存后启用；忘记主密码时使用恢复密钥重置主密码并保留账号数据。
- SQLite 持久化：整条记录（包括名称与备注）使用 AES-256-GCM 加密，重启后使用原主密码解锁。
- 适配桌面和窄屏布局，使用本地静态资源。

## 启动

需要 Python 3.10+，使用普通用户运行。首次安装依赖需要联网。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m vault
```

每条命令成功后再执行下一条。在浏览器打开 **http://127.0.0.1:8765**，首次访问自行设置至少 12 个字符的主密码。开发过程不预设任何主密码或演示账号。

可选参数：

```bash
.venv/bin/python -m vault --port 8766 --data-dir /absolute/path/to/private-vault
```

默认数据库位于启动工作目录下的 `data/vault.db`；请始终在同一目录启动，或显式指定固定的绝对数据目录。数据目录必须专用于本应用，程序将其权限限制为 `0700`，数据库为 `0600`。Ctrl+C 停止，重新运行后输入原主密码即可继续使用。

## 数据与安全边界

- 主密码通过 Scrypt（N=131072、r=8、p=1、随机 16 字节盐）派生密钥，不保存主密码或明文密钥；AES-GCM 使用随机 12 字节 nonce 和记录 ID 作为关联数据。SQLite 中可见记录数量和随机 ID，记录内容为密文。启用恢复功能后，数据库额外存储由随机 256 位恢复密钥加密的保险库密钥，不保存恢复密钥本身。
- 解锁密钥仅保留在服务端进程内存，会话过期或锁定后丢弃引用。Python 无法保证内存的物理擦除；本程序不防御已控制当前操作系统用户或进程的攻击者。
- 单进程、单个活跃浏览器会话；另一浏览器解锁会使旧会话失效。不要用多进程 WSGI 方式启动。
- Cookie 使用 HttpOnly 和 SameSite=Strict。由于仅支持 loopback HTTP，不设 Secure；服务限制 Host 与端口，写操作校验同源 Origin、自定义请求头及 JSON 格式，无跨域授权。不要配置公网或局域网反向代理。
- 主密码解锁、生成恢复密钥时的主密码验证、恢复密钥验证共用限流：连续 5 次验证失败后暂停尝试 30 秒，计数仅在当前进程有效。
- 复制依赖浏览器安全上下文与剪贴板授权。拒绝、不支持、运行失败均给出手动复制路径；不会自动清理系统剪贴板或其历史。
- 主密码和已启用的恢复密钥同时丢失，无法恢复数据。尚无使用旧主密码直接修改主密码的独立界面、导入导出界面、云同步、多人协作或浏览器自动填充。
- 未进行独立安全审计，不应把本轮自动化结果视为生产安全认证。

实现参考：[Cryptography 密钥派生文档](https://cryptography.io/en/46.0.3/hazmat/primitives/key-derivation-functions/)、[AES-GCM 认证加密文档](https://cryptography.io/en/46.0.3/hazmat/primitives/aead/)及 [Flask Web 安全说明](https://flask.palletsprojects.com/en/stable/web-security/)。

## 恢复密钥

1. 正常解锁后，在账号列表上方点击「设置恢复密钥」，重新输入当前主密码。
2. 将生成的 `RK1-…` 密钥复制或抄写到本保险库以外的安全位置。密钥仅在当前窗口显示，不可再次查看。
3. 在 5 分钟内勾选「我已在保险库之外保存这份密钥」，点击「已保存，启用恢复密钥」，确认页面显示「恢复密钥已启用」。关闭窗口、超时或未确认都不会启用新密钥；替换旧密钥时，旧密钥在确认新密钥成功后才作废。
4. 忘记主密码时，在解锁页点击「忘记主密码？使用恢复密钥」，输入已启用的密钥和两次新主密码，即可恢复。账号的 ID、内容、分类、收藏和时间信息保留；加密密钥会重新派生，全部记录在一个事务中重新加密。
5. 恢复成功后旧主密码、旧会话及这份恢复密钥均失效。请使用新主密码解锁，并重新生成和保存恢复密钥。

持有恢复密钥的人可重置主密码并读取全部账号。不要仅将它存入当前保险库，不要把它发给其他人或提交到 Git。复制后仍需留意系统剪贴板历史。恢复请求如果遇到断网、页面中断或响应丢失，先尝试新主密码解锁；不要丢弃原密钥，直到确认最终状态。

旧版本数据库会在启动时新增空的 `recovery` 表，不更改已有记录内容。旧库必须先正常解锁并主动启用恢复功能；升级不能补救已经遗失、且从未设置恢复密钥的主密码。

## 备份与恢复

1. 使用 Ctrl+C 停止服务，确认进程已退出。
2. 将整个专用 `data` 目录复制到安全位置；备份需要备份时的主密码，或备份中已启用且匹配的恢复密钥。旧备份可能包含已删除记录；密钥替换和消费只影响当前库，不会追溯撤销离线旧备份中的密钥。
3. 恢复时保持服务停止，用备份恢复到一个独立目录，再以 `--data-dir` 指定该目录启动。

不要在服务运行时直接复制 SQLite 文件来代替一致性备份，也不要把数据库提交到 Git。

## 测试与构建

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
node --check vault/static/app.js
node --check vault/static/recovery.js
.venv/bin/python -m build --no-isolation
.venv/bin/python tests/browser_smoke.py
```

浏览器脚本优先使用系统 `google-chrome`，也可通过 `CHROME_PATH` 指定 Chrome 路径；没有系统 Chrome 时使用 `playwright install chromium` 安装浏览器。脚本创建临时数据库、启动真实 HTTP 服务、运行浏览器操作，结束时停止服务并删除测试库。截图保存于被 Git 忽略的 `test-results/`。

本轮验证的实际数量、环境和限制见 [handoff.md](handoff.md)。单元测试、构建、集成测试和真实环境验证分别记录。

## 项目结构

```text
vault/__init__.py       API、会话、SQLite 持久化与请求保护
vault/crypto.py         Scrypt 与 AES-GCM
vault/recovery.py       恢复密钥生成和格式解析
vault/__main__.py       本地 Waitress 启动入口
vault/templates/       中文页面
vault/static/          样式、前端交互及图标
tests/test_crypto.py    加密和字段校验单元测试
tests/test_api.py       API 与数据库集成测试
tests/test_recovery.py  恢复、兼容、凭据失效、事务回滚测试
tests/test_recovery_crypto.py  恢复密钥格式单元测试
tests/browser_smoke.py  真实 HTTP 与 Chrome 端到端测试
```
