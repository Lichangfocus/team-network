# Team Network

**把单人 agent 的 memory，变成团队共享、双向同步的 memory。**

每个人的 agent 在自己的 workspace 里干活，攒下的上下文——做过的决策、踩过的坑、对系统的认知——以 wiki 化实体回流到团队的云端共享空间；团队里任何人的 agent 开工前，都会先来这里看一眼背景再动手。

在线服务：**https://tn.lichangfocus.com**（注册即用，托管在 Cloudflare Workers + D1 上）

## 30 秒接入：把这段话发给你的 agent

```text
请帮我接入团队共享上下文（team-network）：
1. 如果没有 tn 命令，先运行: curl -fsSL https://tn.lichangfocus.com/install.sh | bash
2. 在我的项目目录运行: tn connect https://tn.lichangfocus.com
3. 把它输出的授权链接发给我，我在浏览器点击授权
4. 我说完成后，运行 tn connect --finish 完成绑定，并告诉我结果
```

剩下的由 agent 引导完成：它装好工具，甩回来一个**授权链接**；你点开——没账号就注册，没 team 页面上直接创建，选中共享空间点「授权」，回来说一声"好了"。全程不碰终端、不碰 token（设备授权流，凭据只在服务端与 agent 之间一次性交接）。

已经在用的团队邀请新人更简单：team 页点「生成邀请」，产出一段完整的话（注册链接 + 上面这样的接入指令），**整段转发给同事即可**。

## 为什么需要它

- 同事上周踩过的坑，你的 agent 今天原样再踩一遍
- "为什么选 JWT 不选 session"这类决策散落在每个人的会话历史里，会话一关就没了
- agent 的 memory 是单人的：个人积累越多，团队成员之间的信息差反而越大

Team Network 用最薄的一层解决它：**一个共享空间 + 一个 skill**，不改变任何人的工作方式。

## 它怎么工作

```
 你的 agent                      云端共享空间                     同事的 agent
（workspace A）              （team 的 wiki 实体库）             （workspace B）
      │                              │                              │
      │── 任务开始: pull + search ──►│◄── 任务开始: pull + search ──│
      │◄──────── 相关背景实体 ───────│───────── 相关背景实体 ──────►│
      │                              │                              │
      │── 任务结束: push 新实体 ────►│◄──── 任务结束: push 新实体 ──│
```

绑定后全自动：**任务开始前**，agent 用任务关键词检索团队背景（下行）；**任务结束后**，把本次值得团队知道的内容写成实体回流（上行）。什么值得同步、什么不许同步（敏感信息），由 skill 里的质量标准约束。

空间里存的不是文件，是五类**知识卡片**（markdown + frontmatter，用 `[[链接]]` 互相关联，agent 和网页端都能沿链接扩展浏览）：

| 类型 | 存什么 | 例子 |
|---|---|---|
| `decision` | 决策 + 理由 + 排除了什么 | "鉴权选 JWT 而非 session，因为……" |
| `fact` | 客观约束、踩过的坑 | "支付回调 15 秒超时，重试无幂等保护" |
| `entity` | 模块/服务/概念的 wiki 页 | "订单服务：职责、依赖、负责人" |
| `task-log` | 任务结果摘要 | "2026-07-13 重构订单模块，改了 X/Y" |
| `question` | 待确认的疑问 | "回调重试策略待与支付组确认" |

## 协作细节

- **网页 wiki**：人也能用——浏览/检索空间、沿 `[[链接]]` 跳转、查看每个实体的完整版本历史（谁、何时、改了什么）
- **冲突**：乐观锁（push 带 `base_version`），撞车时 agent 拿到双方内容做**语义合并**——保留双方有效信息而不是选边，这是 agent 相比传统 wiki 的天然优势
- **增量同步**：按空间全局 rev 游标只传变更；本地有完整镜像，离线可读写
- **组织模型**：一个 team 可建多个共享空间（按项目/领域分）；一个 workspace 绑定一个空间
- **防护**：接口限流、实体/空间/team 配额、密码 PBKDF2、token 仅存哈希

## 架构与自托管

官方实例跑在 Cloudflare Workers + D1 上，零服务器。服务本身就是发行渠道：`/install.sh` 分发 CLI 与 skill，`/start` 是给 agent 读的接入说明书。

```
worker/                 # 云端服务（Cloudflare Workers + D1），生产主推
server/                 # 同一套 API 的 Python 自托管版（FastAPI + SQLite）
cli/tn.py               # tn CLI（零依赖 Python）：connect/pull/push/search/show/new/…
skill/team-network/     # agent skill：任务前 pull 背景、任务后回流上下文的行为约定
docs/OPS.md             # 运维手册：部署选型、备份、监控、扩容路径
```

自托管三选一（同一套 CLI/skill 对后端透明）：

```bash
# ① 自己的 Cloudflare 账号：cd worker && ./build.sh && npx wrangler deploy（详见 worker/README.md）
# ② 任意服务器跑 Python 版：./run-server.sh 或 docker build（详见 docs/OPS.md）
# ③ 没有服务器也不想托管：tn init <一个共享 git 仓库> 当降级后端
```

## Roadmap

- 语义检索（当前为服务端全文打分，中英文可用）
- 质量闸门：云端定时 agent 做实体合并、去重、过时标记
- 邮箱验证 / 密码找回、token 过期策略
- 细粒度权限（空间只读成员）、实体变更通知

## License

[MIT](LICENSE)
