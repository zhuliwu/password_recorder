# 139.224.25.251 公网 HTTPS 部署

目标链路：浏览器 → `https://139.224.25.251` → 同机 Nginx → `127.0.0.1:8765` → SQLite。

本轮交付代码与步骤，**尚未登录此服务器、申请正式证书、调整安全组或完成公网验收**。以下命令在目标服务器执行；项目运行使用普通用户，系统配置与证书管理使用必要的 sudo 权限。已有账号数据时先按 README 备份，始终使用原来的绝对数据目录。

## 1. 准备项目和网络

- 将本项目放到服务器普通用户拥有的目录；下文以 `/home/YOUR_USER/password_recorder` 为例，替换 `YOUR_USER` 和路径为实际值。不要用 root 运行本项目或测试脚本。
- `139.224.25.251` 必须是你控制且能到达该服务器的公网 IPv4；如果是 NAT，须将公网 TCP 80/443 映射到本机 Nginx。
- 云安全组和主机防火墙允许 TCP 80（证书验证）与 443（HTTPS）。不要开放后端 8765。不要关闭整个防火墙；保留现有 SSH 和其他应用规则。
- 检查 80/443 的现有监听和 Nginx 配置，避免覆盖其他站点；本项目只新增专属配置与 unit。IP 访问通常不带 TLS SNI，最终配置使用 `default_server`，如已有默认 TLS 站点，必须先协调默认站点归属或为密匣选择独立 HTTPS 端口。

在项目目录以普通用户执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

每一步成功后才继续。已有虚拟环境时直接使用，不要移动已创建的虚拟环境。

## 2. 首次主密码只能在本地模式设置

已有保险库可跳过本节。公网模式遇到未初始化数据库会拒绝启动，外网不能抢先创建主密码。

在服务器普通用户终端运行：

```bash
.venv/bin/python -m vault --data-dir /home/YOUR_USER/password_recorder/data
```

在自己电脑的终端建立 SSH 隧道（如 SSH 不是 22 端口，补充 `-p`）：

```bash
ssh -N -L 8765:127.0.0.1:8765 YOUR_USER@139.224.25.251
```

在自己电脑打开 `http://127.0.0.1:8765` 设置主密码，并保存恢复密钥。完成后停止服务器上的临时本地进程；不要删除数据目录。主密码、恢复密钥均不要写入命令行、配置文件或聊天。

## 3. 安装 Nginx 和支持 IP 证书的 Certbot

示例适用于 Ubuntu/Debian。已有的软件不升级；包管理器若报告损坏或未配置状态，应停止并由管理员修复。其他发行版采用对应安装方式。

```bash
sudo apt-get update
sudo apt-get install --no-upgrade nginx python3-venv
sudo python3 -m venv /opt/password-recorder-certbot
sudo /opt/password-recorder-certbot/bin/python -m pip install 'certbot>=5.4,<6'
sudo /opt/password-recorder-certbot/bin/certbot --version
```

该虚拟环境只用于证书管理，与应用虚拟环境分开。确认 Certbot 版本至少 5.4。已有符合要求的 Certbot 时可复用，但要同步修改后续命令及续期 unit 中的绝对路径。

Let’s Encrypt 已支持 IPv4/IPv6 证书，IP 证书采用约六天的短有效期，必须配置自动续期；Certbot 的 IP `webroot` 模式需要 5.4+，不能用旧版 `--nginx` 插件直接申请 IP 证书。[官方 IP 证书指引](https://letsencrypt.org/2026/03/11/shorter-certs-certbot)

## 4. 先提供 ACME HTTP 验证路径

```bash
sudo install -d -m 0755 /var/lib/password-recorder-acme/.well-known/acme-challenge
sudo install -m 0644 deploy/nginx-bootstrap.conf /etc/nginx/conf.d/password-recorder.conf.new
```

若 `/etc/nginx/conf.d/password-recorder.conf` 已存在，先备份到 `/var/backups/password-recorder/`，不要覆盖自己没有审查过的配置。首次部署可直接执行：

```bash
sudo mv /etc/nginx/conf.d/password-recorder.conf.new /etc/nginx/conf.d/password-recorder.conf
sudo nginx -t
```

仅当配置检查成功，才执行 `sudo systemctl reload nginx`；如果 Nginx 尚未运行，检查成功后执行 `sudo systemctl start nginx`。检查失败时恢复原专属配置并再次运行 `nginx -t`，不重载错误配置。

验证网络：在 `.well-known/acme-challenge/` 目录放一个不含敏感信息的测试文件，从服务器以外访问 `http://139.224.25.251/.well-known/acme-challenge/文件名`，确认内容一致，再删除测试文件。此阶段其他 HTTP 路径返回 403 是正常的。

## 5. 申请正式 IP 证书

```bash
sudo /opt/password-recorder-certbot/bin/certbot certonly \
  --webroot --webroot-path /var/lib/password-recorder-acme \
  --preferred-profile shortlived \
  --ip-address 139.224.25.251 \
  --cert-name password-recorder-ip
```

按 Certbot 提示完成账户及条款设置。成功后证书应位于：

- `/etc/letsencrypt/live/password-recorder-ip/fullchain.pem`
- `/etc/letsencrypt/live/password-recorder-ip/privkey.pem`

用 `sudo certbot certificates`（替换为实际 Certbot 绝对路径）及 `openssl x509 -in <证书路径> -noout -dates -ext subjectAltName` 核验有效期与 `IP Address:139.224.25.251`。签发失败通常需要分别检查公网 80 可达性、IP 归属/映射、ACME 文件路径及客户端版本，不能只归因于权限。

不要以自签名证书或 `curl -k` 的成功结果代替公信 HTTPS 验收。若调试使用了 `--staging`，所得证书不被普通浏览器信任，必须重新申请生产证书后再验收。

## 6. 生成并启用完整 HTTPS 配置

在项目目录以普通用户生成专属配置（仅写项目内文件，不自动修改系统）：

```bash
.venv/bin/python -m vault.proxy_config \
  --public-origin https://139.224.25.251 \
  --certificate /etc/letsencrypt/live/password-recorder-ip/fullchain.pem \
  --private-key /etc/letsencrypt/live/password-recorder-ip/privkey.pem \
  --output deploy/generated/password-recorder.conf
```

生成器保留 ACME 路径以便续期，重定向 HTTP GET/HEAD 到 HTTPS，拒绝 HTTP 写请求，清除客户端伪造的代理头，并限制认证接口请求频率。HTTPS 和后端端口可配置，例如公网改为 8443 时，生成器和应用都必须使用 `https://139.224.25.251:8443`。

在同一个服务器终端中备份并原子替换专属 Nginx 配置：

```bash
sudo install -d -m 0700 /var/backups/password-recorder
sudo cp -p /etc/nginx/conf.d/password-recorder.conf /var/backups/password-recorder/nginx-before-https.conf
sudo install -m 0644 deploy/generated/password-recorder.conf /etc/nginx/conf.d/password-recorder.conf.new
sudo mv /etc/nginx/conf.d/password-recorder.conf.new /etc/nginx/conf.d/password-recorder.conf
sudo nginx -t
```

检查失败时不要 reload。将备份先复制为 `.rollback`，再 `mv` 恢复专属配置，重新 `nginx -t`；原 Nginx 进程会继续使用上次有效配置。检查成功后启动应用，再重载 Nginx。

普通用户前台启动应用：

```bash
.venv/bin/python -m vault \
  --data-dir /home/YOUR_USER/password_recorder/data \
  --public-origin https://139.224.25.251
```

后端仅监听 `127.0.0.1:8765`，此时直接访问该 HTTP 地址返回拒绝是预期行为。代理必须与后端同机，并连接 `127.0.0.1`；只信任这一来源设置的 `X-Forwarded-Proto`，不支持任意来源或跨主机代理。然后执行 `sudo systemctl reload nginx` 并按第 8 节验收。

长期运行可使用 `deploy/password-recorder.service.example`：先替换 `YOUR_USER`、用户组、项目路径和数据路径，并确保该普通用户可读取源码/依赖、可写自己的专用数据目录。停止前台进程后将已检查的 unit 安装到 `/etc/systemd/system/password-recorder.service`，运行 `systemd-analyze verify`、`daemon-reload`，再 `enable` 和显式 `restart`。核实服务用户、PID、监听地址和真实 HTTPS 解锁；`enable --now` 不能证明已运行进程加载了新版。

## 7. 自动续期与重载证书

先验证续期流程（不会替换线上证书）：

```bash
sudo /opt/password-recorder-certbot/bin/certbot renew --cert-name password-recorder-ip --dry-run
```

使用已有可靠续期任务，或安装本项目提供的 `password-recorder-renew.service.example` 和 `password-recorder-renew.timer.example` 到 `/etc/systemd/system/`，去掉 `.example` 后缀；核对 Certbot/Nginx/systemctl 的绝对路径。安装后执行：

```bash
sudo systemd-analyze verify /etc/systemd/system/password-recorder-renew.service /etc/systemd/system/password-recorder-renew.timer
sudo systemctl daemon-reload
sudo systemctl enable password-recorder-renew.timer
sudo systemctl restart password-recorder-renew.timer
sudo systemctl list-timers password-recorder-renew.timer
```

定时任务每六小时检查续期，只有成功取得更新后的证书才执行 `nginx -t && systemctl reload nginx`。核验 timer 的下一次执行时间及 `journalctl -u password-recorder-renew.service` 的结果。正式续期后还要从外部 TLS 连接确认服务器实际返回新证书及有效期；一次命令“接受”不代表新证书已生效。

## 8. 公网验收与回退

在另一台设备或手机蜂窝网络上验收：

- `curl https://139.224.25.251/api/status` 不使用 `-k` 能成功；浏览器无证书警告。
- HTTP GET 跳转至 HTTPS，HTTP POST 被拒绝；错误 Host 或跨站 Origin 请求不能修改数据。
- 使用真实 HTTPS 页面解锁，临时测试账号的增删改查、密码显示/复制、锁定正常；浏览器 Cookie 为 `__Host-vault_session`，含 Secure/HttpOnly/SameSite=Strict。
- 在另行创建的测试库验证恢复密钥，不对唯一真实库做破坏性演练。重启后新主密码和原记录可读取。
- 后端 8765 不对公网开放，数据目录和备份只归应用用户访问；证书续期任务有效。

如果公网配置失败，恢复上一个已知有效的专属 Nginx 配置并验证后 reload；应用可停止后去掉 `--public-origin` 退回本地模式，通过 SSH 隧道访问。两种模式使用相同加密数据库，不能同时运行多个应用进程写同一数据目录。公网参数变更不需要重置主密码或重建数据库。

当前验证范围：本地单元/接口测试、真实 Nginx + 临时证书 + 非 loopback IP 的 HTTPS 浏览器测试；临时 CA/IP SAN 由 Python 校验，Chrome 仅对该测试证书公钥使用测试信任例外。**这不等于 139.224.25.251 的正式 CA 签发、网络防火墙或公网部署已通过。**

代理实现参考：[Waitress 代理参数](https://docs.pylonsproject.org/projects/waitress/en/stable/arguments.html)、[Nginx 反向代理文档](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)。
