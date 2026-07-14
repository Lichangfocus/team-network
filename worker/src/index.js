/* Team Network — Cloudflare Worker 版云端服务
 * 与 server/app.py 同一套 API，存储用 D1（云端 SQLite），静态资源走 Workers Assets。
 * 部署见 worker/README.md
 */

// ---------- 配额与限流 ----------
const MAX_CONTENT_BYTES = 100_000;
const MAX_ENTITIES_PER_SPACE = 5_000;
const MAX_TEAMS_PER_USER = 50;
const MAX_SPACES_PER_TEAM = 20;
const PBKDF2_ITER = 100_000; // Workers crypto.subtle 上限

const rateBuckets = new Map(); // 每 isolate 内存限流（多 PoP 下是每点独立，够用）

function clientIp(request) {
  return request.headers.get("cf-connecting-ip") || "?";
}

function rateLimit(request, bucket, limit, windowSec) {
  const key = `${bucket}:${clientIp(request)}`;
  const now = Date.now();
  const hits = (rateBuckets.get(key) || []).filter((t) => now - t < windowSec * 1000);
  if (hits.length >= limit) throw new ApiError(429, "请求过于频繁，请稍后再试");
  hits.push(now);
  rateBuckets.set(key, hits);
  if (rateBuckets.size > 10_000) rateBuckets.clear();
}

// ---------- 工具 ----------
class ApiError extends Error {
  constructor(status, detail) { super(detail); this.status = status; this.detail = detail; }
}

const enc = new TextEncoder();

function j(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status, headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

function nowIso() {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

function hex(buf) {
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function sha256hex(s) {
  return hex(await crypto.subtle.digest("SHA-256", enc.encode(s)));
}

function randToken() {
  const b = crypto.getRandomValues(new Uint8Array(32));
  return btoa(String.fromCharCode(...b)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function randCode(n = 8) {
  const b = crypto.getRandomValues(new Uint8Array(n));
  return btoa(String.fromCharCode(...b)).replace(/[+/=]/g, "").slice(0, 11);
}

async function hashPw(pw, saltHex) {
  const salt = new Uint8Array(saltHex.match(/../g).map((h) => parseInt(h, 16)));
  const key = await crypto.subtle.importKey("raw", enc.encode(pw), "PBKDF2", false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", hash: "SHA-256", salt, iterations: PBKDF2_ITER }, key, 256);
  return hex(bits);
}

function timingSafeEq(a, b) {
  if (a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}

async function readBody(request) {
  try { const d = await request.json(); return typeof d === "object" && d ? d : {}; }
  catch { return {}; }
}

// ---------- 鉴权 / 权限 ----------
async function authUser(env, request) {
  const h = request.headers.get("authorization") || "";
  if (!h.startsWith("Bearer ")) throw new ApiError(401, "需要登录（Authorization: Bearer <token>）");
  const th = await sha256hex(h.slice(7));
  const u = await env.DB.prepare(
    "SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token_hash=?").bind(th).first();
  if (!u) throw new ApiError(401, "token 无效或已失效");
  return u;
}

async function requireMember(env, userId, teamId) {
  const r = await env.DB.prepare(
    "SELECT role FROM team_members WHERE team_id=? AND user_id=?").bind(teamId, userId).first();
  if (!r) throw new ApiError(403, "不是该 team 的成员");
  return r.role;
}

async function getSpace(env, userId, spaceId) {
  const sp = await env.DB.prepare("SELECT * FROM spaces WHERE id=?").bind(spaceId).first();
  if (!sp) throw new ApiError(404, "空间不存在");
  await requireMember(env, userId, sp.team_id);
  return sp;
}

async function issueToken(env, userId, kind) {
  const t = randToken();
  await env.DB.prepare("INSERT INTO tokens(token_hash,user_id,kind,created_at) VALUES(?,?,?,?)")
    .bind(await sha256hex(t), userId, kind, nowIso()).run();
  return t;
}

const userJson = (u) => ({ id: u.id, email: u.email, name: u.name });

// ---------- frontmatter（检索用，与 CLI 同一套打分） ----------
function parseFrontmatter(text) {
  const m = text.match(/^---\n([\s\S]*?)\n---\n?([\s\S]*)$/);
  if (!m) return [{}, text];
  const meta = {};
  for (const line of m[1].split("\n")) {
    const kv = line.match(/^([A-Za-z_][\w-]*):\s*(.*)$/);
    if (!kv) continue;
    let v = kv[2].trim();
    if (v.startsWith("[") && v.endsWith("]"))
      meta[kv[1]] = v.slice(1, -1).split(",").map((x) => x.trim().replace(/^['"]|['"]$/g, "")).filter(Boolean);
    else meta[kv[1]] = v.replace(/^['"]|['"]$/g, "");
  }
  return [meta, m[2]];
}

const entityJson = (e, withContent = true) => {
  const d = { name: e.name, version: e.version, rev: e.rev, updated_by: e.updated_by,
              updated_at: e.updated_at, deleted: !!e.deleted };
  if (withContent) d.content = e.content;
  return d;
};

// ---------- install.sh（动态识别自身域名） ----------
function installScript(origin) {
  return `#!/bin/bash
# Team Network 一键安装（来自 ${origin}）
set -euo pipefail
SERVER="${origin}"
BIN="$HOME/.local/bin"
SKILL="$HOME/.claude/skills/team-network"
mkdir -p "$BIN" "$SKILL"
curl -fsSL --retry 3 --retry-all-errors -m 60 "$SERVER/cli/tn.py" -o "$BIN/tn"
chmod +x "$BIN/tn"
curl -fsSL --retry 3 --retry-all-errors -m 60 "$SERVER/skill/SKILL.md" -o "$SKILL/SKILL.md"
echo "✓ tn CLI      -> $BIN/tn"
echo "✓ agent skill -> $SKILL/SKILL.md"
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo "⚠ $BIN 不在 PATH，请在 ~/.zshrc 加入: export PATH=\\"\\$HOME/.local/bin:\\$PATH\\"" ;;
esac
echo
echo "下一步（给 agent）：在用户的项目目录运行 tn connect ${origin} ，把输出的授权链接发给用户点击，然后运行 tn connect --finish 完成绑定。"
`;
}

// ---------- /start：AI-native 入口（agent 可读的接入说明） ----------
function startDoc(origin) {
  return `# Team Network 接入说明（给 agent 执行）

Team Network 是团队共享上下文空间：绑定后，agent 在任务前自动拉取团队背景实体，任务后把新产生的决策/事实/坑回流共享。

按顺序执行：

1. 安装（若 \`tn\` 命令已存在可跳过）：
   curl -fsSL ${origin}/install.sh | bash

2. 在用户的项目目录（workspace 根目录）运行：
   tn connect ${origin}

3. 上一步会输出一个授权链接。把链接原样发给用户，请用户在浏览器打开：
   - 未注册会先引导注册（无需邀请码；受邀用户会自动入团）
   - 没有 team/空间时页面上可直接创建
   - 用户在页面上选定要绑定的共享空间并点「授权」

4. 用户说点完了之后，运行：
   tn connect --finish
   若提示仍在等待，稍后重试该命令（链接 15 分钟内有效）。

5. 绑定成功后即完成。之后每次任务：开始前 \`tn pull\` + \`tn search <关键词>\` 读团队背景；
   结束后把值得团队知道的上下文写成实体 \`tn push\` 回流（详见已安装的 team-network skill）。
`;
}

// ---------- 路由 ----------
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const p = url.pathname;
    const m = request.method;
    try {
      let mt;
      if (p === "/api/health") {
        await env.DB.prepare("SELECT 1").first();
        return j({ ok: true });
      }
      if (p === "/install.sh")
        return new Response(installScript(url.origin), { headers: { "Content-Type": "text/x-shellscript" } });
      if (p === "/start")
        return new Response(startDoc(url.origin), { headers: { "Content-Type": "text/markdown; charset=utf-8" } });
      if ((mt = p.match(/^\/s\/(\d+)$/)))
        return Response.redirect(`${url.origin}/#/space/${mt[1]}`, 302);
      if ((mt = p.match(/^\/join\/([\w-]+)$/)))
        return Response.redirect(`${url.origin}/#/join/${mt[1]}`, 302);
      if ((mt = p.match(/^\/link\/([\w-]+)$/)))
        return Response.redirect(`${url.origin}/#/link/${mt[1]}`, 302);

      if (p === "/api/device" && m === "POST") return await deviceCreate(env, request, url);
      if ((mt = p.match(/^\/api\/device\/([\w-]+)$/)) && m === "GET") return await deviceInfo(env, request, mt[1]);
      if ((mt = p.match(/^\/api\/device\/([\w-]+)\/poll$/)) && m === "GET") return await devicePoll(env, request, mt[1]);
      if ((mt = p.match(/^\/api\/device\/([\w-]+)\/approve$/)) && m === "POST") return await deviceApprove(env, request, mt[1]);

      if (p === "/api/register" && m === "POST") return await register(env, request);
      if (p === "/api/login" && m === "POST") return await login(env, request);
      if (p === "/api/me" && m === "GET") return j(userJson(await authUser(env, request)));
      if (p === "/api/cli-token" && m === "POST") {
        const u = await authUser(env, request);
        return j({ token: await issueToken(env, u.id, "cli") });
      }
      if (p === "/api/teams" && m === "GET") return await myTeams(env, request);
      if (p === "/api/teams" && m === "POST") return await createTeam(env, request);
      if ((mt = p.match(/^\/api\/teams\/(\d+)$/)) && m === "GET") return await teamDetail(env, request, +mt[1]);
      if ((mt = p.match(/^\/api\/teams\/(\d+)\/invites$/)) && m === "POST") return await createInvite(env, request, +mt[1]);
      if ((mt = p.match(/^\/api\/invites\/([\w-]+)$/)) && m === "GET") return await invitePreview(env, request, mt[1]);
      if ((mt = p.match(/^\/api\/invites\/([\w-]+)\/accept$/)) && m === "POST") return await acceptInvite(env, request, mt[1]);
      if ((mt = p.match(/^\/api\/teams\/(\d+)\/spaces$/)) && m === "POST") return await createSpace(env, request, +mt[1]);
      if ((mt = p.match(/^\/api\/spaces\/(\d+)$/)) && m === "GET") return await spaceDetail(env, request, +mt[1]);
      if ((mt = p.match(/^\/api\/spaces\/(\d+)\/entities$/)) && m === "GET") return await listEntities(env, request, +mt[1], url);
      if ((mt = p.match(/^\/api\/spaces\/(\d+)\/search$/)) && m === "GET") return await search(env, request, +mt[1], url);
      if ((mt = p.match(/^\/api\/spaces\/(\d+)\/entities\/([\w.-]+)\/history$/)))
        return await entityHistory(env, request, +mt[1], mt[2]);
      if ((mt = p.match(/^\/api\/spaces\/(\d+)\/entities\/([\w.-]+)$/))) {
        if (m === "GET") return await getEntity(env, request, +mt[1], mt[2]);
        if (m === "PUT") return await putEntity(env, request, +mt[1], mt[2]);
        if (m === "DELETE") return await deleteEntity(env, request, +mt[1], mt[2]);
      }
      if (p.startsWith("/api/")) return j({ detail: "Not Found" }, 404);
      return env.ASSETS.fetch(request);
    } catch (e) {
      if (e instanceof ApiError) return j({ detail: e.detail, ...(e.extra || {}) }, e.status);
      return j({ detail: `服务内部错误: ${e.message}` }, 500);
    }
  },
};

// ---------- 设备授权流（AI-native：agent 发起，用户网页点一下） ----------
const DEVICE_TTL_SEC = 900;

function deviceExpired(row) {
  return Date.now() - Date.parse(row.created_at) > DEVICE_TTL_SEC * 1000;
}

async function deviceCreate(env, request, url) {
  rateLimit(request, "device", 10, 600);
  const d = await readBody(request);
  const hint = parseInt(d.space_hint || 0, 10) || null;
  const code = randCode(9);
  await env.DB.prepare("INSERT INTO device_codes(code,space_hint,created_at) VALUES(?,?,?)")
    .bind(code, hint, nowIso()).run();
  return j({ code, link: `${url.origin}/link/${code}`, interval: 3, expires_in: DEVICE_TTL_SEC });
}

async function deviceInfo(env, request, code) {
  rateLimit(request, "device-info", 60, 600);
  const r = await env.DB.prepare("SELECT * FROM device_codes WHERE code=?").bind(code).first();
  if (!r || deviceExpired(r)) throw new ApiError(404, "授权码无效或已过期");
  return j({ status: r.status, space_hint: r.space_hint });
}

async function devicePoll(env, request, code) {
  rateLimit(request, "device-poll", 60, 60);
  const r = await env.DB.prepare("SELECT * FROM device_codes WHERE code=?").bind(code).first();
  if (!r) return j({ status: "expired" });
  if (deviceExpired(r)) {
    await env.DB.prepare("DELETE FROM device_codes WHERE code=?").bind(code).run();
    return j({ status: "expired" });
  }
  if (r.status !== "approved") return j({ status: "pending" });
  await env.DB.prepare("DELETE FROM device_codes WHERE code=?").bind(code).run(); // token 一次性取走
  return j({ status: "approved", token: r.token, space_id: r.space_id });
}

async function deviceApprove(env, request, code) {
  const u = await authUser(env, request);
  const d = await readBody(request);
  const sid = parseInt(d.space_id || 0, 10);
  if (!sid) throw new ApiError(400, "缺少 space_id");
  const r = await env.DB.prepare("SELECT * FROM device_codes WHERE code=?").bind(code).first();
  if (!r || deviceExpired(r)) throw new ApiError(404, "授权码无效或已过期，请让 agent 重新运行 tn connect");
  if (r.status === "approved") throw new ApiError(409, "该授权码已被使用");
  const sp = await getSpace(env, u.id, sid); // 校验授权者确实是该空间成员
  const token = await issueToken(env, u.id, "cli");
  await env.DB.prepare("UPDATE device_codes SET status='approved', token=?, space_id=? WHERE code=?")
    .bind(token, sid, code).run();
  return j({ ok: true, space_id: sid, space_name: sp.name });
}

// ---------- 用户 ----------
async function register(env, request) {
  rateLimit(request, "register", 20, 3600);
  const d = await readBody(request);
  const email = String(d.email || "").trim().toLowerCase();
  const name = String(d.name || "").trim() || email.split("@")[0];
  const pw = String(d.password || "");
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) throw new ApiError(400, "邮箱格式不正确");
  if (pw.length < 8) throw new ApiError(400, "密码至少 8 位");
  const salt = hex(crypto.getRandomValues(new Uint8Array(16)));
  const pwh = await hashPw(pw, salt);
  let r;
  try {
    r = await env.DB.prepare(
      "INSERT INTO users(email,name,pw_salt,pw_hash,created_at) VALUES(?,?,?,?,?) RETURNING *")
      .bind(email, name, salt, pwh, nowIso()).first();
  } catch (e) {
    throw new ApiError(409, "该邮箱已注册");
  }
  return j({ token: await issueToken(env, r.id, "web"), user: userJson(r) });
}

async function login(env, request) {
  rateLimit(request, "login", 30, 600);
  const d = await readBody(request);
  const email = String(d.email || "").trim().toLowerCase();
  const u = await env.DB.prepare("SELECT * FROM users WHERE email=?").bind(email).first();
  if (!u || !timingSafeEq(await hashPw(String(d.password || ""), u.pw_salt), u.pw_hash))
    throw new ApiError(401, "邮箱或密码错误");
  return j({ token: await issueToken(env, u.id, String(d.kind || "web")), user: userJson(u) });
}

// ---------- Team ----------
async function myTeams(env, request) {
  const u = await authUser(env, request);
  const rows = await env.DB.prepare(
    `SELECT t.id, t.name, m.role,
            (SELECT COUNT(*) FROM team_members WHERE team_id=t.id) AS members,
            (SELECT COUNT(*) FROM spaces WHERE team_id=t.id) AS spaces
     FROM teams t JOIN team_members m ON m.team_id=t.id WHERE m.user_id=? ORDER BY t.id`)
    .bind(u.id).all();
  return j(rows.results);
}

async function createTeam(env, request) {
  const u = await authUser(env, request);
  const d = await readBody(request);
  const name = String(d.name || "").trim();
  if (!name) throw new ApiError(400, "team 名称不能为空");
  const n = await env.DB.prepare("SELECT COUNT(*) AS c FROM team_members WHERE user_id=?").bind(u.id).first();
  if (n.c >= MAX_TEAMS_PER_USER) throw new ApiError(429, `每个用户最多加入 ${MAX_TEAMS_PER_USER} 个 team`);
  const t = await env.DB.prepare(
    "INSERT INTO teams(name,created_by,created_at) VALUES(?,?,?) RETURNING id")
    .bind(name, u.id, nowIso()).first();
  await env.DB.prepare("INSERT INTO team_members(team_id,user_id,role,joined_at) VALUES(?,?,?,?)")
    .bind(t.id, u.id, "owner", nowIso()).run();
  return j({ id: t.id, name });
}

async function teamDetail(env, request, tid) {
  const u = await authUser(env, request);
  const role = await requireMember(env, u.id, tid);
  const t = await env.DB.prepare("SELECT * FROM teams WHERE id=?").bind(tid).first();
  const members = await env.DB.prepare(
    `SELECT u.id, u.email, u.name, m.role, m.joined_at
     FROM team_members m JOIN users u ON u.id=m.user_id WHERE m.team_id=? ORDER BY m.joined_at`)
    .bind(tid).all();
  const spaces = await env.DB.prepare(
    `SELECT s.id, s.name, s.rev, s.created_at,
            (SELECT COUNT(*) FROM entities e WHERE e.space_id=s.id AND e.deleted=0) AS entities
     FROM spaces s WHERE s.team_id=? ORDER BY s.id`).bind(tid).all();
  return j({ id: t.id, name: t.name, my_role: role, members: members.results, spaces: spaces.results });
}

async function createInvite(env, request, tid) {
  const u = await authUser(env, request);
  await requireMember(env, u.id, tid);
  const code = randCode();
  await env.DB.prepare(
    "INSERT INTO invites(code,team_id,created_by,created_at,uses_left) VALUES(?,?,?,?,20)")
    .bind(code, tid, u.id, nowIso()).run();
  return j({ code });
}

async function invitePreview(env, request, code) {
  rateLimit(request, "invite-preview", 60, 600);
  const inv = await env.DB.prepare("SELECT * FROM invites WHERE code=?").bind(code).first();
  if (!inv || inv.uses_left <= 0) throw new ApiError(404, "邀请码无效或已用完");
  const t = await env.DB.prepare("SELECT * FROM teams WHERE id=?").bind(inv.team_id).first();
  return j({ team_id: t.id, team_name: t.name });
}

async function acceptInvite(env, request, code) {
  rateLimit(request, "invite-accept", 30, 3600);
  const u = await authUser(env, request);
  const inv = await env.DB.prepare("SELECT * FROM invites WHERE code=?").bind(code).first();
  if (!inv || inv.uses_left <= 0) throw new ApiError(404, "邀请码无效或已用完");
  const existing = await env.DB.prepare(
    "SELECT 1 AS x FROM team_members WHERE team_id=? AND user_id=?").bind(inv.team_id, u.id).first();
  if (!existing) {
    await env.DB.batch([
      env.DB.prepare("INSERT INTO team_members(team_id,user_id,role,joined_at) VALUES(?,?,?,?)")
        .bind(inv.team_id, u.id, "member", nowIso()),
      env.DB.prepare("UPDATE invites SET uses_left=uses_left-1 WHERE code=?").bind(code),
    ]);
  }
  const t = await env.DB.prepare("SELECT * FROM teams WHERE id=?").bind(inv.team_id).first();
  return j({ team_id: t.id, team_name: t.name });
}

// ---------- 空间 ----------
async function createSpace(env, request, tid) {
  const u = await authUser(env, request);
  await requireMember(env, u.id, tid);
  const d = await readBody(request);
  const name = String(d.name || "").trim();
  if (!name) throw new ApiError(400, "空间名称不能为空");
  const n = await env.DB.prepare("SELECT COUNT(*) AS c FROM spaces WHERE team_id=?").bind(tid).first();
  if (n.c >= MAX_SPACES_PER_TEAM) throw new ApiError(429, `每个 team 最多 ${MAX_SPACES_PER_TEAM} 个空间`);
  try {
    const s = await env.DB.prepare(
      "INSERT INTO spaces(team_id,name,created_at) VALUES(?,?,?) RETURNING id")
      .bind(tid, name, nowIso()).first();
    return j({ id: s.id, name });
  } catch {
    throw new ApiError(409, "同名空间已存在");
  }
}

async function spaceDetail(env, request, sid) {
  const u = await authUser(env, request);
  const sp = await getSpace(env, u.id, sid);
  const t = await env.DB.prepare("SELECT name FROM teams WHERE id=?").bind(sp.team_id).first();
  const n = await env.DB.prepare(
    "SELECT COUNT(*) AS c FROM entities WHERE space_id=? AND deleted=0").bind(sid).first();
  return j({ id: sp.id, name: sp.name, team_id: sp.team_id, team_name: t.name, rev: sp.rev, entities: n.c });
}

// ---------- 实体 ----------
async function listEntities(env, request, sid, url) {
  const u = await authUser(env, request);
  const sp = await getSpace(env, u.id, sid);
  const since = parseInt(url.searchParams.get("since") || "0", 10) || 0;
  const metaOnly = url.searchParams.get("meta_only") === "1";
  const rows = await env.DB.prepare(
    "SELECT * FROM entities WHERE space_id=? AND rev>? ORDER BY rev").bind(sid, since).all();
  return j({ rev: sp.rev, entities: rows.results.map((e) => entityJson(e, !metaOnly)) });
}

async function getEntity(env, request, sid, name) {
  const u = await authUser(env, request);
  await getSpace(env, u.id, sid);
  const e = await env.DB.prepare(
    "SELECT * FROM entities WHERE space_id=? AND name=? AND deleted=0").bind(sid, name).first();
  if (!e) throw new ApiError(404, "实体不存在");
  return j(entityJson(e));
}

async function entityHistory(env, request, sid, name) {
  const u = await authUser(env, request);
  await getSpace(env, u.id, sid);
  const e = await env.DB.prepare("SELECT * FROM entities WHERE space_id=? AND name=?").bind(sid, name).first();
  if (!e) throw new ApiError(404, "实体不存在");
  const rows = await env.DB.prepare(
    "SELECT version, content, updated_by, updated_at, deleted FROM entity_versions " +
    "WHERE entity_id=? ORDER BY version DESC").bind(e.id).all();
  return j(rows.results);
}

async function putEntity(env, request, sid, name) {
  const u = await authUser(env, request);
  const sp = await getSpace(env, u.id, sid);
  const d = await readBody(request);
  const content = d.content;
  const baseVersion = parseInt(d.base_version || 0, 10) || 0;
  if (typeof content !== "string" || !content.trim()) throw new ApiError(400, "content 不能为空");
  if (enc.encode(content).length > MAX_CONTENT_BYTES)
    throw new ApiError(413, `实体过大（上限 ${MAX_CONTENT_BYTES / 1000}KB）——共享空间存知识卡片，不是文件`);
  if (!/^[\w.-]+$/.test(name) || name.length > 120)
    throw new ApiError(400, "实体名只能包含字母数字、点、横线、下划线（≤120 字符）");

  const e = await env.DB.prepare("SELECT * FROM entities WHERE space_id=? AND name=?").bind(sid, name).first();
  const currentVersion = !e || e.deleted ? 0 : e.version;
  if (baseVersion !== currentVersion) {
    const err409 = new ApiError(409, "版本冲突：云端已被他人更新，请做语义合并后重试");
    err409.extra = { current: e ? entityJson(e) : null };
    throw err409;
  }
  const ts = nowIso();
  const rev = (await env.DB.prepare(
    "UPDATE spaces SET rev=rev+1 WHERE id=? RETURNING rev").bind(sid).first()).rev;
  let eid, newVersion;
  if (!e) {
    const n = await env.DB.prepare("SELECT COUNT(*) AS c FROM entities WHERE space_id=?").bind(sid).first();
    if (n.c >= MAX_ENTITIES_PER_SPACE)
      throw new ApiError(429, `空间实体数已达上限 ${MAX_ENTITIES_PER_SPACE}，先做整理合并`);
    newVersion = 1;
    eid = (await env.DB.prepare(
      "INSERT INTO entities(space_id,name,content,version,rev,updated_by,updated_at,deleted) " +
      "VALUES(?,?,?,?,?,?,?,0) RETURNING id").bind(sid, name, content, 1, rev, u.email, ts).first()).id;
  } else {
    newVersion = e.version + 1;
    await env.DB.prepare(
      "UPDATE entities SET content=?, version=?, rev=?, updated_by=?, updated_at=?, deleted=0 WHERE id=?")
      .bind(content, newVersion, rev, u.email, ts, e.id).run();
    eid = e.id;
  }
  await env.DB.prepare(
    "INSERT INTO entity_versions(entity_id,version,content,updated_by,updated_at,deleted) VALUES(?,?,?,?,?,0)")
    .bind(eid, newVersion, content, u.email, ts).run();
  return j({ name, version: newVersion, rev, updated_at: ts });
}

async function deleteEntity(env, request, sid, name) {
  const u = await authUser(env, request);
  await getSpace(env, u.id, sid);
  const d = await readBody(request);
  const baseVersion = parseInt(d.base_version || 0, 10) || 0;
  const e = await env.DB.prepare(
    "SELECT * FROM entities WHERE space_id=? AND name=? AND deleted=0").bind(sid, name).first();
  if (!e) throw new ApiError(404, "实体不存在");
  if (baseVersion !== e.version) {
    const err409 = new ApiError(409, "版本冲突：云端已被他人更新");
    err409.extra = { current: entityJson(e) };
    throw err409;
  }
  const ts = nowIso();
  const rev = (await env.DB.prepare(
    "UPDATE spaces SET rev=rev+1 WHERE id=? RETURNING rev").bind(sid).first()).rev;
  await env.DB.batch([
    env.DB.prepare(
      "UPDATE entities SET version=?, rev=?, updated_by=?, updated_at=?, deleted=1 WHERE id=?")
      .bind(e.version + 1, rev, u.email, ts, e.id),
    env.DB.prepare(
      "INSERT INTO entity_versions(entity_id,version,content,updated_by,updated_at,deleted) VALUES(?,?,?,?,?,1)")
      .bind(e.id, e.version + 1, e.content, u.email, ts),
  ]);
  return j({ name, version: e.version + 1, rev });
}

// ---------- 检索 ----------
async function search(env, request, sid, url) {
  const u = await authUser(env, request);
  await getSpace(env, u.id, sid);
  const q = url.searchParams.get("q") || "";
  const limit = Math.min(parseInt(url.searchParams.get("limit") || "10", 10) || 10, 50);
  const terms = q.split(/\s+/).filter(Boolean);
  if (!terms.length) throw new ApiError(400, "缺少检索词 q");
  const rows = await env.DB.prepare(
    "SELECT * FROM entities WHERE space_id=? AND deleted=0").bind(sid).all();
  const results = [];
  for (const e of rows.results) {
    const [meta, body] = parseFrontmatter(e.content);
    const tags = Array.isArray(meta.tags) ? meta.tags : [];
    const hayHigh = [e.name, meta.title || "", tags.join(" ")].join(" ").toLowerCase();
    const hayBody = body.toLowerCase();
    let score = 0;
    for (const t of terms) {
      const tl = t.toLowerCase();
      score += (hayHigh.split(tl).length - 1) * 5 + (hayBody.split(tl).length - 1);
    }
    if (score > 0) {
      const first = body.split("\n").map((l) => l.trim()).find((l) => l && !l.startsWith("#")) || "";
      results.push({
        score, name: e.name, type: meta.type || "entity", title: meta.title || "",
        snippet: first.slice(0, 120),
        links: [...body.matchAll(/\[\[([\w./-]+)\]\]/g)].map((x) => x[1]).slice(0, 8),
        updated_by: e.updated_by, updated_at: e.updated_at, version: e.version,
      });
    }
  }
  results.sort((a, b) => b.score - a.score);
  return j({ results: results.slice(0, limit) });
}
