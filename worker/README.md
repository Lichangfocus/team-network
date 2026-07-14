# Cloudflare Workers + D1 部署（主推：零服务器托管）

整个服务跑在 Cloudflare 上：Worker 提供 API，D1（云端 SQLite）存数据，Workers Assets 托管网页后台并分发 CLI/skill。免费额度（100k 请求/天、D1 5GB）足够小团队长期使用。

## 首次部署

```bash
cd worker
npx wrangler login                 # 浏览器授权一次（账号所有者操作）
./build.sh                         # 收集静态资源
npx wrangler d1 create team-network
#   ↑ 把输出的 database_id 填进 wrangler.toml 的 PLACEHOLDER
npx wrangler d1 execute team-network --remote --file schema.sql
npx wrangler deploy                # 得到 https://team-network.<你>.workers.dev
```

## 绑定自有域名（国内访问建议必做）

workers.dev 域名在国内访问不稳定。Dashboard → Workers & Pages → team-network → Settings → Domains & Routes → 添加你托管在 Cloudflare 的域名（如 `tn.example.com`）。install.sh、接入命令、邀请链接都从请求域名自动生成，换域名零改动。

## 升级

```bash
cd worker && ./build.sh && npx wrangler deploy
```

schema 变更：写迁移 SQL 后 `npx wrangler d1 execute team-network --remote --file <迁移文件>`。

## 备份

```bash
npx wrangler d1 export team-network --remote --output backup-$(date +%Y%m%d).sql
```

（D1 自身有 30 天时间点恢复，Time Travel。）

## 本地开发 / 测试（无需登录 Cloudflare）

```bash
cd worker && ./build.sh
npx wrangler d1 execute team-network --local --file schema.sql
npx wrangler dev --port 8788       # 本地模拟 Worker + D1
```

## 限制说明

- 密码 PBKDF2 为 10 万轮（Workers crypto 上限），仅注册/登录时计算；token 校验走 SHA-256，不受影响
- 限流是每个边缘节点内存级的，跨节点不共享——对滥用防护足够，精确限流需 Durable Objects（暂无必要）
- 单实体 100KB、D1 单行上限 1MB，天然匹配"知识卡片"的定位
