# Team Network

团队共享上下文网络：每个人的 agent 在自己的 workspace 里工作并持续积累上下文，这些上下文以 **wiki 化实体** 的形式回流到云端共享空间；任何绑定了该空间的 workspace，agent 执行任务前都会默认去空间里检索背景信息。

一句话：**把单人 agent 的 memory 变成团队共享、双向同步的 memory。**

## 组成

```
server/                 # 云端服务：FastAPI + SQLite（用户/team/邀请/空间/实体版本库/检索）
server/static/          # 网页后台：注册登录、建 team、邀请码拉人、多空间、实体 wiki 浏览 + 历史
cli/tn.py               # tn CLI（零依赖 Python）：login/init/pull/push/search/show/new/ls/status/where
skill/team-network/     # Claude skill：任务前 pull 背景，任务后回流上下文
install.sh              # 安装 CLI 到 ~/.local/bin，skill 链接到 ~/.claude/skills
run-server.sh           # 本地/服务器启动（自动建 venv）
server/Dockerfile       # 容器部署（数据库在 /data volume）
```

## 快速开始

**1. 部署云端服务**（团队一人做一次）

```bash
./run-server.sh                       # 默认 http://0.0.0.0:8787，TN_PORT 可改
# 或 docker build -t team-network server/ && docker run -p 8787:8787 -v tn-data:/data team-network
```

永久托管：仓库根目录有 `render.yaml`，Render Dashboard → New → Blueprint → 选本仓库即可（free plan 重部署丢数据，starter 有持久盘）。

临时公网试用（不买服务器，先给同事玩一下）：

```bash
./run-server.sh &
npx -y localtunnel --port 8787        # 得到 https://xxx.loca.lt 公网地址
# CLI 直接用；浏览器首次访问需输入隧道密码（curl https://loca.lt/mytunnelpassword 查看）
```

**2. 网页后台初始化**：打开服务地址 → 注册 → 创建 team → 「生成邀请码」发给同事（同事注册后输码加入）→ 创建共享空间。

**3. 每个成员两条命令接入**（服务本身就是发行渠道，无需 clone 本仓库）：

```bash
# ① 每台电脑装一次（装好 tn CLI + agent skill）
curl -fsSL http://server:8787/install.sh | bash

# ② 网页空间页点「生成我的接入命令」，把它扔给 agent（或在项目目录执行）
tn connect http://server:8787/s/1 --token <你的token>
```

绑定完成后，这个 workspace 里的 agent 会自动：**任务开始** → `tn pull` + `tn search <关键词>` 读背景实体（下行）；**任务结束** → 把本次产生的决策/事实/坑写成实体 → `tn push` 回流（上行）。

## 实体模型

每个实体是空间里的一个 markdown 文件（`tn where` 查看本地镜像目录）：

```markdown
---
name: payment-callback-timeout
type: fact              # entity | decision | fact | task-log | question
title: 支付回调 15 秒超时
tags: [payments]
created_by: alice@team.com
updated_by: bob@team.com
updated_at: 2026-07-13
---

支付网关回调必须在 15 秒内响应 200……相关 [[order-service]]。
```

- `[[实体名]]` 建立实体间关联，agent 和网页端都可沿链接浏览
- 每次修改产生新版本，网页端可看完整历史（谁、何时、改了什么）
- `updated_at / updated_by` 由 push 自动盖戳

## 同步与冲突

- **乐观锁**：push 带 `base_version`，云端已被他人更新时返回 409
- CLI 把双方内容写成 `<<<<<<< local / ======= / >>>>>>> server` 冲突标记，agent 做**语义合并**（保留双方有效信息）后重推——这是 agent 相比传统 wiki 的天然优势
- 增量拉取：按空间全局 rev 游标，只传变更实体
- 本地镜像优先：离线可读可写，联网后同步

## 两种后端

| | api（推荐） | git（降级方案） |
|---|---|---|
| 云端 | 本服务（team/邀请/多空间/历史/网页浏览） | 一个共享 git 仓库 |
| 绑定 | `tn init http://server/s/<id>` | `tn init <git-remote>` |
| 冲突 | 409 + 冲突标记 | rebase + 冲突标记 |

同一套 CLI 命令和 skill 对两种后端透明。

## 下一步（第三刀）

- 语义检索（当前为服务端全文打分，中英文可用）
- 质量闸门：云端定时 agent 做实体合并、去重、过时标记（格式已预留 version/updated_at 字段）
- 细粒度权限（空间只读成员）、entity watch 通知
