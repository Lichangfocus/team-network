# 运维手册 — 中心化托管（主推模式）

模式：**我们运维一个多租户服务，用户只需注册**。所有 team/space 共用一套服务和一个数据库，靠成员关系做隔离（代码已实现）。用户侧完整流程只有三步：`curl <域名>/install.sh | bash` → 网页注册/邀请链接入团 → 接入命令扔给 agent。

## 服务器怎么选（按国内团队访问质量排序）

| 方案 | 成本 | 上手 | 说明 |
|---|---|---|---|
| **香港轻量 VPS**（腾讯云/阿里云 HK）★推荐 | ~30-50 元/月 | 需自己 docker | 国内直连快、无需备案、数据自己攥着 |
| Fly.io（hkg 区） | 免费额度起步 | `fly launch` | 有 fly.toml；国内访问一般但可用 |
| Zeabur（HK 区） | 免费额度起步 | 连 GitHub 即部署 | 华人团队产品，国内访问友好 |
| Render | 免费起步 | 连 GitHub 即部署 | 有 render.yaml；free 无持久盘 |

**VPS 部署（推荐路径）**：

```bash
# 服务器上（装好 docker 后）
git clone https://github.com/Lichangfocus/team-network && cd team-network
docker build -t team-network .
docker run -d --name tn --restart unless-stopped -p 80:8787 -v tn-data:/data team-network
# 绑域名 + HTTPS：最简单是套 Cloudflare（DNS 代理即免费 HTTPS），或 caddy reverse_proxy
```

升级：`git pull && docker build -t team-network . && docker rm -f tn && docker run ...`（数据在 volume，无损）。

## 域名与 HTTPS

- 买一个域名（如 `tn.example.com`）指到服务器；Cloudflare 免费计划开代理即有 HTTPS 和基础防护
- install.sh / 接入命令 / 邀请链接全部从请求头自动识别域名（`x-forwarded-proto/host`），换域名零改动

## 数据与备份

- 全部状态在一个 SQLite 文件（`TN_DB`，默认 `/data/tn.db`）
- `scripts/backup.sh`：热备份 + 保留 14 份，加进 cron 每日一跑；备份目录建议再同步到对象存储
- 恢复：停服务 → 用备份文件替换 tn.db → 起服务
- 更强的方案：litestream 实时复制到 S3（单二进制，配一次不用管）

## 监控

- `GET /api/health` 健康检查（会真实探一次数据库），接 UptimeRobot/Betterstack 免费档即可
- 日志：uvicorn stdout，`docker logs tn`

## 已内置的公开服务防护

- 限流（按 IP）：注册 20/小时、登录 30/10分钟、邀请接受 30/小时、邀请预览 60/10分钟
- 配额：实体 ≤100KB、空间 ≤5000 实体、team ≤20 空间、用户 ≤50 team、邀请码 20 次
- 密码 PBKDF2-SHA256(20万轮)存储，token 仅存哈希，凭据文件 0600

## 扩容路径（按用户量）

1. **现在～几千用户**：单机 SQLite 完全够（读多写少、单实体 KB 级）
2. **再往后**：SQLite → Postgres（schema 简单，迁移脚本半天活），限流挪到网关层，多实例
3. **搜索**：全文打分 → SQLite FTS / pg_trgm → 向量检索（实体格式不用变）

## 尚未做（对外开放前建议补）

- [ ] 邮箱验证 / 密码找回（需接一个邮件服务，如 Resend，免费档够用）
- [ ] token 过期与吊销列表（当前长期有效，泄露需手工删 tokens 表记录）
- [ ] 服务条款/隐私页（公开注册的合规基础）
- [ ] 管理面板（看注册量/空间量/封禁滥用账号，现阶段直接 sqlite3 查）
