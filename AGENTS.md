# 项目开发规则

## 架构与范围

- Python 3.10+、Flask 3、Waitress 单进程、SQLite、cryptography；原生 HTML/CSS/JS。
- 单人密码库；后端始终只监听 127.0.0.1。默认本地 HTTP；用户已授权可选同机 Nginx HTTPS 反向代理，必须显式配置精确的公网来源，不接受任意 Host、Origin 或跨主机代理。
- 密钥仅存在会话内存中；记录全部加密。日志、测试截图、Git 中不得出现真实用户凭据。
- 不引入云资源、外部字体、CDN、明文数据库字段或 localStorage 凭据缓存。

## 开发与验证

- 每次代码需求先按用户级规则明确分支策略。本轮公网需求已获准从干净的 `main`（`c6054b3`）创建并使用 `feat/public-https-access`；用户选择仅交付代码与部署步骤，不操作目标服务器。
- 运行：`.venv/bin/python -m vault`。
- 单元/API：`.venv/bin/python -m pytest -q`；语法：`node --check vault/static/app.js`。
- 打包：`.venv/bin/python -m build --no-isolation`。
- 浏览器：安装 Playwright 后运行 `python tests/browser_smoke.py`，使用临时数据目录，不能操作用户实际库。
- HTTPS：`python tests/https_smoke.py --nginx /absolute/path/to/nginx`，使用非 loopback IP、真实 Nginx、临时证书和测试库；测试证书信任例外不得描述为正式 CA 或目标公网验收。
- 公网模式禁止初始化保险库，要求 Secure Cookie、精确 Host/Origin、JSON 与请求头保护；Waitress 只信任 127.0.0.1 的 X-Forwarded-Proto。修改代理信任时覆盖伪造头、错误来源、非 HTTPS 和未初始化库拒绝路径。
- Nginx 配置由项目生成器输出，安装前验证、备份及原子替换；正式 IP 证书必须有续期与重载验证。证书/私钥/生成配置不得提交 Git。
- 代理可能返回非 JSON 的 429/413/502；前端须提供对应操作提示，不能只显示 JSON 解析失败。恢复请求响应丢失时标为结果未确认，提示先尝试新密码；故障注入须覆盖后端已成功但浏览器收到 502。
- 后端访问保护、加密、会话和持久化变更必须覆盖正反向路径。SQLite 连接须显式关闭，事务上下文不负责关闭连接。
- 恢复密钥采用生成/确认两阶段；确认前不得覆盖旧恢复凭据。重置主密码必须在单一事务内更新全部记录、验证数据及恢复凭据，并验证失败回滚、旧密码/旧会话失效和旧库兼容。
- setuptools 必须显式限定 `vault` 包，避免运行后生成的 `data/` 被当作顶层命名空间包；构建应在已有本地数据目录时验证，产物不得包含数据库。
- CSS Grid 中包含横向滚动导航时，要给 grid item 设置 `min-width:0`，并在 375px 真浏览器中检查横向溢出。
- UI 显示错误不得把失败当作空库或零条记录。复制能力只能按实际协议/主机验证。
- 更新 README 与 handoff，分别记载单元、构建、集成和真实运行态。只记录本轮已重新验证的状态。
- 审查所有未跟踪文件；不提交 `.venv`、数据库、缓存、构建产物或测试截图。
