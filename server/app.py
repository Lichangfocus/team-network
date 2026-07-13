"""Team Network 云端服务

用户 / Team / 邀请 / 共享空间 / 实体（带版本历史 + 乐观锁）/ 检索。
存储: SQLite。启动: ./run-server.sh 或 uvicorn app:app --port 8787
"""

import datetime
import hashlib
import os
import re
import secrets
import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

BASE = Path(__file__).parent
DB_PATH = Path(os.environ.get("TN_DB", BASE / "data" / "tn.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  pw_salt BLOB NOT NULL,
  pw_hash BLOB NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tokens(
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  kind TEXT NOT NULL DEFAULT 'web',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS teams(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS team_members(
  team_id INTEGER NOT NULL REFERENCES teams(id),
  user_id INTEGER NOT NULL REFERENCES users(id),
  role TEXT NOT NULL DEFAULT 'member',
  joined_at TEXT NOT NULL,
  PRIMARY KEY(team_id, user_id)
);
CREATE TABLE IF NOT EXISTS invites(
  code TEXT PRIMARY KEY,
  team_id INTEGER NOT NULL REFERENCES teams(id),
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL,
  uses_left INTEGER NOT NULL DEFAULT 20
);
CREATE TABLE IF NOT EXISTS spaces(
  id INTEGER PRIMARY KEY,
  team_id INTEGER NOT NULL REFERENCES teams(id),
  name TEXT NOT NULL,
  rev INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  UNIQUE(team_id, name)
);
CREATE TABLE IF NOT EXISTS entities(
  id INTEGER PRIMARY KEY,
  space_id INTEGER NOT NULL REFERENCES spaces(id),
  name TEXT NOT NULL,
  content TEXT NOT NULL,
  version INTEGER NOT NULL,
  rev INTEGER NOT NULL,
  updated_by TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted INTEGER NOT NULL DEFAULT 0,
  UNIQUE(space_id, name)
);
CREATE TABLE IF NOT EXISTS entity_versions(
  entity_id INTEGER NOT NULL REFERENCES entities(id),
  version INTEGER NOT NULL,
  content TEXT NOT NULL,
  updated_by TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(entity_id, version)
);
CREATE INDEX IF NOT EXISTS idx_entities_space_rev ON entities(space_id, rev);
"""


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


with db() as _c:
    _c.executescript(SCHEMA)


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hash_pw(pw: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200_000)


def token_hash(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def err(code, msg):
    raise HTTPException(status_code=code, detail=msg)


app = FastAPI(title="Team Network", docs_url=None, redoc_url=None)


# ---------- 鉴权 / 权限 ----------

def auth_user(conn, request: Request):
    h = request.headers.get("authorization", "")
    if not h.startswith("Bearer "):
        err(401, "需要登录（Authorization: Bearer <token>）")
    row = conn.execute(
        "SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token_hash=?",
        (token_hash(h[7:]),),
    ).fetchone()
    if not row:
        err(401, "token 无效或已失效")
    return row


def require_member(conn, user_id, team_id):
    row = conn.execute(
        "SELECT role FROM team_members WHERE team_id=? AND user_id=?", (team_id, user_id)
    ).fetchone()
    if not row:
        err(403, "不是该 team 的成员")
    return row["role"]


def get_space(conn, user_id, space_id):
    sp = conn.execute("SELECT * FROM spaces WHERE id=?", (space_id,)).fetchone()
    if not sp:
        err(404, "空间不存在")
    require_member(conn, user_id, sp["team_id"])
    return sp


def issue_token(conn, user_id, kind):
    t = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO tokens(token_hash, user_id, kind, created_at) VALUES(?,?,?,?)",
        (token_hash(t), user_id, kind, now()),
    )
    return t


async def body(request: Request):
    try:
        d = await request.json()
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def user_json(u):
    return {"id": u["id"], "email": u["email"], "name": u["name"]}


# ---------- 用户 ----------

@app.post("/api/register")
async def register(request: Request):
    d = await body(request)
    email = (d.get("email") or "").strip().lower()
    name = (d.get("name") or "").strip() or email.split("@")[0]
    pw = d.get("password") or ""
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        err(400, "邮箱格式不正确")
    if len(pw) < 8:
        err(400, "密码至少 8 位")
    salt = secrets.token_bytes(16)
    with db() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO users(email,name,pw_salt,pw_hash,created_at) VALUES(?,?,?,?,?)",
                (email, name, salt, hash_pw(pw, salt), now()),
            )
        except sqlite3.IntegrityError:
            err(409, "该邮箱已注册")
        t = issue_token(conn, cur.lastrowid, "web")
        u = conn.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    return {"token": t, "user": user_json(u)}


@app.post("/api/login")
async def login(request: Request):
    d = await body(request)
    email = (d.get("email") or "").strip().lower()
    kind = d.get("kind") or "web"
    with db() as conn:
        u = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not u or not secrets.compare_digest(hash_pw(d.get("password") or "", u["pw_salt"]), u["pw_hash"]):
            err(401, "邮箱或密码错误")
        t = issue_token(conn, u["id"], kind)
    return {"token": t, "user": user_json(u)}


@app.get("/api/me")
async def me(request: Request):
    with db() as conn:
        u = auth_user(conn, request)
    return user_json(u)


@app.post("/api/cli-token")
async def cli_token(request: Request):
    """在网页后台生成给 CLI 用的 token，避免在终端输入密码。"""
    with db() as conn:
        u = auth_user(conn, request)
        t = issue_token(conn, u["id"], "cli")
    return {"token": t}


# ---------- Team ----------

@app.get("/api/teams")
async def my_teams(request: Request):
    with db() as conn:
        u = auth_user(conn, request)
        rows = conn.execute(
            """SELECT t.id, t.name, m.role,
                      (SELECT COUNT(*) FROM team_members WHERE team_id=t.id) AS members,
                      (SELECT COUNT(*) FROM spaces WHERE team_id=t.id) AS spaces
               FROM teams t JOIN team_members m ON m.team_id=t.id WHERE m.user_id=?
               ORDER BY t.id""",
            (u["id"],),
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/teams")
async def create_team(request: Request):
    d = await body(request)
    name = (d.get("name") or "").strip()
    if not name:
        err(400, "team 名称不能为空")
    with db() as conn:
        u = auth_user(conn, request)
        cur = conn.execute(
            "INSERT INTO teams(name,created_by,created_at) VALUES(?,?,?)", (name, u["id"], now())
        )
        conn.execute(
            "INSERT INTO team_members(team_id,user_id,role,joined_at) VALUES(?,?,?,?)",
            (cur.lastrowid, u["id"], "owner", now()),
        )
    return {"id": cur.lastrowid, "name": name}


@app.get("/api/teams/{tid}")
async def team_detail(tid: int, request: Request):
    with db() as conn:
        u = auth_user(conn, request)
        role = require_member(conn, u["id"], tid)
        t = conn.execute("SELECT * FROM teams WHERE id=?", (tid,)).fetchone()
        members = conn.execute(
            """SELECT u.id, u.email, u.name, m.role, m.joined_at
               FROM team_members m JOIN users u ON u.id=m.user_id WHERE m.team_id=? ORDER BY m.joined_at""",
            (tid,),
        ).fetchall()
        spaces = conn.execute(
            """SELECT s.id, s.name, s.rev, s.created_at,
                      (SELECT COUNT(*) FROM entities e WHERE e.space_id=s.id AND e.deleted=0) AS entities
               FROM spaces s WHERE s.team_id=? ORDER BY s.id""",
            (tid,),
        ).fetchall()
    return {
        "id": t["id"], "name": t["name"], "my_role": role,
        "members": [dict(m) for m in members],
        "spaces": [dict(s) for s in spaces],
    }


@app.post("/api/teams/{tid}/invites")
async def create_invite(tid: int, request: Request):
    with db() as conn:
        u = auth_user(conn, request)
        require_member(conn, u["id"], tid)
        code = secrets.token_urlsafe(8)
        conn.execute(
            "INSERT INTO invites(code,team_id,created_by,created_at,uses_left) VALUES(?,?,?,?,20)",
            (code, tid, u["id"], now()),
        )
    return {"code": code}


@app.post("/api/invites/{code}/accept")
async def accept_invite(code: str, request: Request):
    with db() as conn:
        u = auth_user(conn, request)
        inv = conn.execute("SELECT * FROM invites WHERE code=?", (code,)).fetchone()
        if not inv or inv["uses_left"] <= 0:
            err(404, "邀请码无效或已用完")
        existing = conn.execute(
            "SELECT 1 FROM team_members WHERE team_id=? AND user_id=?", (inv["team_id"], u["id"])
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO team_members(team_id,user_id,role,joined_at) VALUES(?,?,?,?)",
                (inv["team_id"], u["id"], "member", now()),
            )
            conn.execute("UPDATE invites SET uses_left=uses_left-1 WHERE code=?", (code,))
        t = conn.execute("SELECT * FROM teams WHERE id=?", (inv["team_id"],)).fetchone()
    return {"team_id": t["id"], "team_name": t["name"]}


# ---------- 空间 ----------

@app.post("/api/teams/{tid}/spaces")
async def create_space(tid: int, request: Request):
    d = await body(request)
    name = (d.get("name") or "").strip()
    if not name:
        err(400, "空间名称不能为空")
    with db() as conn:
        u = auth_user(conn, request)
        require_member(conn, u["id"], tid)
        try:
            cur = conn.execute(
                "INSERT INTO spaces(team_id,name,created_at) VALUES(?,?,?)", (tid, name, now())
            )
        except sqlite3.IntegrityError:
            err(409, "同名空间已存在")
    return {"id": cur.lastrowid, "name": name}


@app.get("/api/spaces/{sid}")
async def space_detail(sid: int, request: Request):
    with db() as conn:
        u = auth_user(conn, request)
        sp = get_space(conn, u["id"], sid)
        t = conn.execute("SELECT name FROM teams WHERE id=?", (sp["team_id"],)).fetchone()
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM entities WHERE space_id=? AND deleted=0", (sid,)
        ).fetchone()["c"]
    return {"id": sp["id"], "name": sp["name"], "team_id": sp["team_id"],
            "team_name": t["name"], "rev": sp["rev"], "entities": n}


# ---------- 实体 ----------

def entity_json(e, with_content=True):
    d = {
        "name": e["name"], "version": e["version"], "rev": e["rev"],
        "updated_by": e["updated_by"], "updated_at": e["updated_at"], "deleted": bool(e["deleted"]),
    }
    if with_content:
        d["content"] = e["content"]
    return d


@app.get("/api/spaces/{sid}/entities")
async def list_entities(sid: int, request: Request, since: int = 0, meta_only: int = 0):
    with db() as conn:
        u = auth_user(conn, request)
        sp = get_space(conn, u["id"], sid)
        rows = conn.execute(
            "SELECT * FROM entities WHERE space_id=? AND rev>? ORDER BY rev", (sid, since)
        ).fetchall()
    return {"rev": sp["rev"], "entities": [entity_json(r, not meta_only) for r in rows]}


@app.get("/api/spaces/{sid}/entities/{name}")
async def get_entity(sid: int, name: str, request: Request):
    with db() as conn:
        u = auth_user(conn, request)
        get_space(conn, u["id"], sid)
        e = conn.execute(
            "SELECT * FROM entities WHERE space_id=? AND name=? AND deleted=0", (sid, name)
        ).fetchone()
        if not e:
            err(404, "实体不存在")
    return entity_json(e)


@app.get("/api/spaces/{sid}/entities/{name}/history")
async def entity_history(sid: int, name: str, request: Request):
    with db() as conn:
        u = auth_user(conn, request)
        get_space(conn, u["id"], sid)
        e = conn.execute("SELECT * FROM entities WHERE space_id=? AND name=?", (sid, name)).fetchone()
        if not e:
            err(404, "实体不存在")
        rows = conn.execute(
            "SELECT version, content, updated_by, updated_at, deleted FROM entity_versions "
            "WHERE entity_id=? ORDER BY version DESC", (e["id"],),
        ).fetchall()
    return [dict(r) for r in rows]


@app.put("/api/spaces/{sid}/entities/{name}")
async def put_entity(sid: int, name: str, request: Request):
    d = await body(request)
    content = d.get("content")
    base_version = int(d.get("base_version") or 0)
    if not isinstance(content, str) or not content.strip():
        err(400, "content 不能为空")
    if not re.match(r"^[\w.-]+$", name):
        err(400, "实体名只能包含字母数字、点、横线、下划线")
    with db() as conn:
        u = auth_user(conn, request)
        sp = get_space(conn, u["id"], sid)
        e = conn.execute("SELECT * FROM entities WHERE space_id=? AND name=?", (sid, name)).fetchone()
        current_version = 0 if (e is None or e["deleted"]) else e["version"]
        if base_version != current_version:
            return JSONResponse(status_code=409, content={
                "detail": "版本冲突：云端已被他人更新，请做语义合并后重试",
                "current": entity_json(e) if e else None,
            })
        new_rev = sp["rev"] + 1
        conn.execute("UPDATE spaces SET rev=? WHERE id=?", (new_rev, sid))
        ts = now()
        if e is None:
            cur = conn.execute(
                "INSERT INTO entities(space_id,name,content,version,rev,updated_by,updated_at,deleted) "
                "VALUES(?,?,?,?,?,?,?,0)", (sid, name, content, 1, new_rev, u["email"], ts),
            )
            eid, new_version = cur.lastrowid, 1
        else:
            new_version = e["version"] + 1
            conn.execute(
                "UPDATE entities SET content=?, version=?, rev=?, updated_by=?, updated_at=?, deleted=0 "
                "WHERE id=?", (content, new_version, new_rev, u["email"], ts, e["id"]),
            )
            eid = e["id"]
        conn.execute(
            "INSERT INTO entity_versions(entity_id,version,content,updated_by,updated_at,deleted) "
            "VALUES(?,?,?,?,?,0)", (eid, new_version, content, u["email"], ts),
        )
    return {"name": name, "version": new_version, "rev": new_rev, "updated_at": ts}


@app.delete("/api/spaces/{sid}/entities/{name}")
async def delete_entity(sid: int, name: str, request: Request):
    d = await body(request)
    base_version = int(d.get("base_version") or 0)
    with db() as conn:
        u = auth_user(conn, request)
        sp = get_space(conn, u["id"], sid)
        e = conn.execute(
            "SELECT * FROM entities WHERE space_id=? AND name=? AND deleted=0", (sid, name)
        ).fetchone()
        if not e:
            err(404, "实体不存在")
        if base_version != e["version"]:
            return JSONResponse(status_code=409, content={
                "detail": "版本冲突：云端已被他人更新", "current": entity_json(e),
            })
        new_rev = sp["rev"] + 1
        ts = now()
        conn.execute("UPDATE spaces SET rev=? WHERE id=?", (new_rev, sid))
        conn.execute(
            "UPDATE entities SET version=?, rev=?, updated_by=?, updated_at=?, deleted=1 WHERE id=?",
            (e["version"] + 1, new_rev, u["email"], ts, e["id"]),
        )
        conn.execute(
            "INSERT INTO entity_versions(entity_id,version,content,updated_by,updated_at,deleted) "
            "VALUES(?,?,?,?,?,1)", (e["id"], e["version"] + 1, e["content"], u["email"], ts),
        )
    return {"name": name, "version": e["version"] + 1, "rev": new_rev}


# ---------- 检索（与 CLI 同一套打分逻辑）----------

def parse_frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.DOTALL)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        kv = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line.rstrip())
        if not kv:
            continue
        key, val = kv.group(1), kv.group(2).strip()
        if val.startswith("[") and val.endswith("]"):
            meta[key] = [v.strip().strip("'\"") for v in val[1:-1].split(",") if v.strip()]
        else:
            meta[key] = val.strip("'\"")
    return meta, m.group(2)


@app.get("/api/spaces/{sid}/search")
async def search(sid: int, request: Request, q: str = "", limit: int = 10):
    terms = [t for t in q.split() if t]
    if not terms:
        err(400, "缺少检索词 q")
    with db() as conn:
        u = auth_user(conn, request)
        get_space(conn, u["id"], sid)
        rows = conn.execute(
            "SELECT * FROM entities WHERE space_id=? AND deleted=0", (sid,)
        ).fetchall()
    results = []
    for e in rows:
        meta, mbody = parse_frontmatter(e["content"])
        hay_high = " ".join([e["name"], str(meta.get("title", "")),
                             " ".join(meta.get("tags", []) if isinstance(meta.get("tags"), list) else [])]).lower()
        hay_body = mbody.lower()
        score = sum(hay_high.count(t.lower()) * 5 + hay_body.count(t.lower()) for t in terms)
        if score > 0:
            first = next((l.strip() for l in mbody.splitlines() if l.strip() and not l.startswith("#")), "")
            results.append({
                "score": score, "name": e["name"], "type": meta.get("type", "entity"),
                "title": meta.get("title", ""), "snippet": first[:120],
                "links": re.findall(r"\[\[([\w./-]+)\]\]", mbody)[:8],
                "updated_by": e["updated_by"], "updated_at": e["updated_at"], "version": e["version"],
            })
    results.sort(key=lambda x: -x["score"])
    return {"results": results[:limit]}


# ---------- 一键安装分发（服务自己就是发行渠道）----------

REPO_ROOT = BASE.parent


def origin_of(request: Request):
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
    return f"{proto}://{host}"


@app.get("/install.sh")
async def install_sh(request: Request):
    """curl -fsSL <server>/install.sh | bash — 一条命令装好 tn CLI + skill"""
    from fastapi.responses import PlainTextResponse
    o = origin_of(request)
    script = f"""#!/bin/bash
# Team Network 一键安装（来自 {o}）
set -euo pipefail
SERVER="{o}"
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
echo "下一步：打开 $SERVER 注册并加入/创建 team，在共享空间页复制「接入命令」发给你的 agent 即可。"
"""
    return PlainTextResponse(script, media_type="text/x-shellscript")


@app.get("/cli/tn.py")
async def dist_cli():
    from fastapi.responses import FileResponse
    p = REPO_ROOT / "cli" / "tn.py"
    if not p.is_file():
        err(404, "CLI 文件缺失")
    return FileResponse(p, media_type="text/x-python")


@app.get("/skill/SKILL.md")
async def dist_skill():
    from fastapi.responses import FileResponse
    p = REPO_ROOT / "skill" / "team-network" / "SKILL.md"
    if not p.is_file():
        err(404, "skill 文件缺失")
    return FileResponse(p, media_type="text/markdown")


# ---------- 静态页面（网页后台）----------

@app.get("/s/{sid}")
async def space_shortlink(sid: int):
    """tn init 绑定命令里的短链接，浏览器打开时跳到空间页。"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(f"/#/space/{sid}")


app.mount("/", StaticFiles(directory=BASE / "static", html=True), name="static")
