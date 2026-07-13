#!/usr/bin/env python3
"""tn — Team Network CLI

一个 workspace 绑定一个团队共享空间，空间里存储 wiki 化的实体（markdown + frontmatter）。
两种后端：
  api  — Team Network 云端服务（网页后台建 team/拉人/建空间）★ 推荐
  git  — 一个共享 git 仓库（无服务器的降级方案）

命令:
  tn connect <url>/s/<id> --token TOK  一条命令完成登录+绑定（网页空间页可复制）
  tn login <server> [--token TOK]      登录云端服务（token 可在网页后台生成）
  tn init <目标>                        绑定当前 workspace 到共享空间
                                        目标 = http(s)://server/s/<空间id>（网页空间页可复制）
                                             或 git 远端地址
  tn pull                              同步云端最新实体到本地
  tn push [-m MSG]                     回流本地新增/修改/删除的实体
  tn search <关键词...>                检索实体（api 后端走服务端，始终最新）
  tn show <name> [name...]            查看实体全文（含出链列表）
  tn new <name> --type TYPE --title T  创建实体骨架
  tn ls                                列出空间内全部实体
  tn status                            查看待推送的本地改动
  tn where                             打印空间本地目录（可直接编辑其中文件）

零依赖，Python 3.9+。
"""

import argparse
import datetime
import getpass
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CONFIG_DIR = ".team-network"
CONFIG_FILE = "config.json"
TN_HOME = Path.home() / ".team-network"
SPACES_ROOT = TN_HOME / "spaces"
CREDS_FILE = TN_HOME / "credentials.json"
ENTITY_DIR = "entities"
INDEX_FILE = "SPACE.md"
STATE_FILE = ".tn-state.json"
ENTITY_TYPES = ["entity", "decision", "fact", "task-log", "question"]
CONFLICT_MARK = "<<<<<<<"


def die(msg, code=1):
    print(f"tn: {msg}", file=sys.stderr)
    sys.exit(code)


def today():
    return datetime.date.today().isoformat()


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


# ---------- frontmatter ----------

def parse_text(text):
    """解析 markdown frontmatter，返回 (meta:dict, body:str)。"""
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.DOTALL)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue
        kv = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if not kv:
            continue
        key, val = kv.group(1), kv.group(2).strip()
        if val.startswith("[") and val.endswith("]"):
            meta[key] = [v.strip().strip("'\"") for v in val[1:-1].split(",") if v.strip()]
        else:
            meta[key] = val.strip("'\"")
    return meta, m.group(2)


def dump_text(meta, body):
    lines = ["---"]
    for k, v in meta.items():
        if isinstance(v, list):
            lines.append(f"{k}: [{', '.join(v)}]")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines) + "\n\n" + body.lstrip("\n")


def extract_links(body):
    return re.findall(r"\[\[([\w./-]+)\]\]", body)


def stamp(text, email):
    """给实体盖 updated_at/updated_by 戳，返回新文本。"""
    meta, body = parse_text(text)
    if not meta:
        return text
    meta["updated_at"] = today()
    meta["updated_by"] = email
    meta.setdefault("created_by", email)
    return dump_text(meta, body)


# ---------- workspace 配置 ----------

def find_workspace_root(start=None):
    cur = Path(start or os.getcwd()).resolve()
    for p in [cur, *cur.parents]:
        if (p / CONFIG_DIR / CONFIG_FILE).is_file():
            return p
    return None


def load_config():
    root = find_workspace_root()
    if root is None:
        die("当前 workspace 未绑定共享空间。先运行: tn init <目标>\n"
            "  目标 = http(s)://服务器/s/<空间id>（网页后台空间页可复制）或 git 远端地址")
    cfg = json.loads((root / CONFIG_DIR / CONFIG_FILE).read_text())
    cfg.setdefault("backend", "git")
    space = Path(cfg["space_path"])
    if not space.is_dir():
        die(f"空间本地目录不存在: {space}\n重新运行 tn init 修复")
    return root, cfg, space


def load_creds():
    if CREDS_FILE.is_file():
        return json.loads(CREDS_FILE.read_text())
    return {}


def save_creds(creds):
    TN_HOME.mkdir(parents=True, exist_ok=True)
    CREDS_FILE.write_text(json.dumps(creds, indent=2, ensure_ascii=False) + "\n")
    os.chmod(CREDS_FILE, 0o600)


def get_cred(server):
    c = load_creds().get(server)
    if not c:
        die(f"尚未登录 {server}。先运行: tn login {server}")
    return c


# ---------- HTTP ----------

def http(method, url, token=None, data=None):
    req = urllib.request.Request(url, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    payload = json.dumps(data).encode() if data is not None else None
    try:
        with urllib.request.urlopen(req, payload, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {"detail": f"HTTP {e.code}"}
    except urllib.error.URLError as e:
        die(f"无法连接服务器: {e.reason}")


def api(method, cfg, path, data=None):
    cred = get_cred(cfg["server"])
    status, r = http(method, cfg["server"] + path, cred["token"], data)
    if status == 401:
        die(f"登录已失效，重新运行: tn login {cfg['server']}")
    return status, r


# ---------- git 工具 ----------

def run_git(space, *args, check=True):
    return subprocess.run(["git", "-C", str(space)] + list(args),
                          check=check, capture_output=True, text=True)


def git_author():
    try:
        r = subprocess.run(["git", "config", "user.email"], capture_output=True, text=True)
        if r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return os.environ.get("USER", "unknown")


def is_empty_remote_error(stderr):
    return ("couldn't find remote ref" in stderr
            or "no such ref was fetched" in stderr
            or "no such ref" in stderr)


# ---------- 实体读取（本地目录，两种后端通用） ----------

def load_all_entities(space):
    ents = {}
    edir = space / ENTITY_DIR
    if not edir.is_dir():
        return ents
    for p in sorted(edir.glob("*.md")):
        meta, body = parse_text(p.read_text(errors="replace"))
        ents[p.stem] = {"path": p, "meta": meta, "body": body}
    return ents


def score_entity(name, e, terms):
    meta, body = e["meta"], e["body"]
    tags = meta.get("tags", [])
    hay_high = " ".join([name, str(meta.get("title", "")),
                         " ".join(tags if isinstance(tags, list) else [tags])]).lower()
    hay_body = body.lower()
    return sum(hay_high.count(t.lower()) * 5 + hay_body.count(t.lower()) for t in terms)


def regenerate_index(space):
    ents = load_all_entities(space)
    lines = ["# Shared Space Index", "",
             f"共 {len(ents)} 个实体。本文件由 `tn push` 自动生成，请勿手工编辑。", ""]
    by_type = {}
    for name, e in ents.items():
        by_type.setdefault(e["meta"].get("type", "entity"), []).append((name, e))
    for t in ENTITY_TYPES + sorted(set(by_type) - set(ENTITY_TYPES)):
        if t not in by_type:
            continue
        lines.append(f"## {t}")
        lines.append("")
        for name, e in sorted(by_type[t]):
            meta = e["meta"]
            lines.append(f"- [{meta.get('title', name)}]({ENTITY_DIR}/{e['path'].name}) — `{name}`"
                         f" — {meta.get('updated_at', '?')} by {meta.get('updated_by', '?')}")
        lines.append("")
    (space / INDEX_FILE).write_text("\n".join(lines))


# ---------- api 后端：本地镜像状态 ----------

def load_state(space):
    p = space / STATE_FILE
    if p.is_file():
        return json.loads(p.read_text())
    return {"last_rev": 0, "entities": {}}


def save_state(space, state):
    (space / STATE_FILE).write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")


def conflict_text(local_text, server):
    return (f"{CONFLICT_MARK} local（本地修改）\n{local_text.rstrip()}\n"
            f"=======\n{server['content'].rstrip()}\n"
            f">>>>>>> server v{server['version']} by {server['updated_by']}\n")


def report_conflicts(space, conflicts):
    print("⚠ 以下实体与云端冲突，已在文件中写入冲突标记，需要语义合并：", file=sys.stderr)
    for nm in conflicts:
        print(f"  ✗ {space / ENTITY_DIR / (nm + '.md')}", file=sys.stderr)
    print("处理方式：编辑上述文件，保留双方有效信息、删除冲突标记，然后重新 tn push。", file=sys.stderr)
    sys.exit(2)


# ---------- 命令: login / init ----------

def cmd_login(args):
    server = args.server.rstrip("/")
    if not re.match(r"^https?://", server):
        server = "http://" + server
    if args.token:
        token = args.token
    else:
        print(f"登录 {server}（账号在网页后台注册；也可在网页后台生成 token 后用 --token 传入）")
        email = input("邮箱: ").strip()
        password = getpass.getpass("密码: ")
        status, r = http("POST", server + "/api/login", None,
                         {"email": email, "password": password, "kind": "cli"})
        if status != 200:
            die(r.get("detail", "登录失败"))
        token = r["token"]
    status, me = http("GET", server + "/api/me", token)
    if status != 200:
        die("token 无效")
    creds = load_creds()
    creds[server] = {"token": token, "email": me["email"]}
    save_creds(creds)
    print(f"✓ 已登录 {server}（{me['email']}）")


def cmd_init(args):
    target = args.target.rstrip("/")
    m = re.match(r"^(https?://[^/]+)/s/(\d+)$", target)
    if m:
        init_api(m.group(1), int(m.group(2)))
    else:
        init_git(args.target, args.name)


def cmd_connect(args):
    """一条命令完成 登录 + 绑定 + 首次同步（token 来自网页空间页的「接入命令」）。"""
    m = re.match(r"^(https?://[^/]+)/s/(\d+)$", args.target.rstrip("/"))
    if not m:
        die("目标格式应为 http(s)://服务器/s/<空间id>（网页空间页可复制完整接入命令）")
    server, sid = m.group(1), int(m.group(2))
    status, me = http("GET", server + "/api/me", args.token)
    if status != 200:
        die("token 无效或已失效，回到网页空间页重新生成接入命令")
    creds = load_creds()
    creds[server] = {"token": args.token, "email": me["email"]}
    save_creds(creds)
    print(f"✓ 已登录 {server}（{me['email']}）")
    init_api(server, sid)


def write_ws_config(cfg):
    ws = Path(os.getcwd())
    cfgdir = ws / CONFIG_DIR
    cfgdir.mkdir(exist_ok=True)
    (cfgdir / CONFIG_FILE).write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    return cfgdir / CONFIG_FILE


def init_api(server, sid):
    cred = get_cred(server)
    status, sp = http("GET", f"{server}/api/spaces/{sid}", cred["token"])
    if status == 401:
        die(f"登录已失效，重新运行: tn login {server}")
    if status != 200:
        die(sp.get("detail", f"无法访问空间 {sid}"))
    host = re.sub(r"[^\w.-]", "-", server.split("://")[1])
    space = SPACES_ROOT / f"api-{host}-s{sid}"
    (space / ENTITY_DIR).mkdir(parents=True, exist_ok=True)
    if not (space / STATE_FILE).is_file():
        save_state(space, {"last_rev": 0, "entities": {}})
    cfg = {"backend": "api", "server": server, "space_id": sid,
           "space_name": sp["name"], "team_name": sp.get("team_name", ""),
           "space_path": str(space), "bound_at": today()}
    path = write_ws_config(cfg)
    print(f"✓ workspace 已绑定云端空间 '{sp['name']}'（team: {sp.get('team_name','')}，{sp['entities']} 个实体）")
    print(f"  配置: {path}")
    api_pull(cfg, space)


def init_git(remote, name):
    slug = name or re.sub(r"[^\w-]", "-", Path(remote.rstrip("/")).stem) or "space"
    digest = hashlib.sha1(remote.encode()).hexdigest()[:8]
    space = SPACES_ROOT / f"{slug}-{digest}"
    SPACES_ROOT.mkdir(parents=True, exist_ok=True)
    if space.is_dir():
        print(f"复用已存在的空间克隆: {space}")
    else:
        print(f"克隆共享空间 {remote} -> {space}")
        r = subprocess.run(["git", "clone", remote, str(space)], capture_output=True, text=True)
        if r.returncode != 0:
            die(f"克隆失败:\n{r.stderr.strip()}")
    (space / ENTITY_DIR).mkdir(exist_ok=True)
    cfg = {"backend": "git", "remote": remote, "space_name": slug,
           "space_path": str(space), "bound_at": today()}
    path = write_ws_config(cfg)
    print(f"✓ workspace 已绑定共享空间 '{slug}'（{len(load_all_entities(space))} 个实体）")
    print(f"  配置: {path}")


# ---------- 命令: pull ----------

def cmd_pull(args):
    _, cfg, space = load_config()
    if cfg["backend"] == "api":
        api_pull(cfg, space)
    else:
        git_pull(cfg, space)


def api_pull(cfg, space):
    state = load_state(space)
    status, r = api("GET", cfg, f"/api/spaces/{cfg['space_id']}/entities?since={state['last_rev']}")
    if status != 200:
        die(r.get("detail", "pull 失败"))
    edir = space / ENTITY_DIR
    edir.mkdir(exist_ok=True)
    updated, conflicts = [], []
    for e in r["entities"]:
        nm = e["name"]
        p = edir / f"{nm}.md"
        st = state["entities"].get(nm)
        local = p.read_text() if p.is_file() else None
        local_clean = local is not None and st is not None and st["sha"] == sha(local)
        if e["deleted"]:
            if local is None or local_clean:
                if p.is_file():
                    p.unlink()
                if state["entities"].pop(nm, None) or local is not None:
                    updated.append(f"✗ {nm}（云端已删除）")
            else:
                state["entities"].pop(nm, None)
                print(f"⚠ {nm} 云端已删除但本地有修改，保留本地版本（下次 push 将重新创建）")
            continue
        if local is None or local_clean:
            if local != e["content"]:
                p.write_text(e["content"])
                updated.append(f"↓ {nm} v{e['version']}")
            state["entities"][nm] = {"version": e["version"], "sha": sha(e["content"])}
        elif local == e["content"]:
            state["entities"][nm] = {"version": e["version"], "sha": sha(local)}
        else:
            p.write_text(conflict_text(local, e))
            state["entities"][nm] = {"version": e["version"], "sha": None}
            conflicts.append(nm)
    state["last_rev"] = r["rev"]
    save_state(space, state)
    n = len([1 for _ in edir.glob("*.md")])
    print(f"✓ 已同步空间 '{cfg['space_name']}'，当前 {n} 个实体（rev {r['rev']}）")
    for u in updated:
        print(f"  {u}")
    if conflicts:
        report_conflicts(space, conflicts)


def git_pull(cfg, space):
    r = run_git(space, "pull", "--rebase", check=False)
    if r.returncode != 0:
        if is_empty_remote_error(r.stderr):
            print("空间尚为空（远端无提交），无可拉取内容。")
            return
        die(f"pull 失败:\n{r.stderr.strip()}")
    print(f"✓ 已同步空间 '{cfg['space_name']}'，当前 {len(load_all_entities(space))} 个实体")


# ---------- 命令: push ----------

def cmd_push(args):
    _, cfg, space = load_config()
    if cfg["backend"] == "api":
        api_push(cfg, space)
    else:
        git_push(cfg, space, args.message)


def api_push(cfg, space):
    email = get_cred(cfg["server"])["email"]
    state = load_state(space)
    edir = space / ENTITY_DIR
    edir.mkdir(exist_ok=True)

    for p in edir.glob("*.md"):
        if CONFLICT_MARK in p.read_text(errors="replace"):
            die(f"{p} 还有未解决的冲突标记。先做语义合并（保留双方有效信息、删除标记），再 tn push")

    pushed, conflicts, deleted = [], [], []
    for p in sorted(edir.glob("*.md")):
        nm = p.stem
        st = state["entities"].get(nm)
        text = p.read_text(errors="replace")
        if st and st["sha"] == sha(text):
            continue
        text = stamp(text, email)
        p.write_text(text)
        base = st["version"] if st else 0
        status, r = api("PUT", cfg, f"/api/spaces/{cfg['space_id']}/entities/{nm}",
                        {"content": text, "base_version": base})
        if status == 200:
            state["entities"][nm] = {"version": r["version"], "sha": sha(text)}
            pushed.append(f"↑ {nm} v{r['version']}")
        elif status == 409 and r.get("current"):
            p.write_text(conflict_text(text, r["current"]))
            state["entities"][nm] = {"version": r["current"]["version"], "sha": None}
            conflicts.append(nm)
        else:
            die(r.get("detail", f"push {nm} 失败"))

    for nm in list(state["entities"]):
        if not (edir / f"{nm}.md").is_file():
            st = state["entities"][nm]
            status, r = api("DELETE", cfg, f"/api/spaces/{cfg['space_id']}/entities/{nm}",
                            {"base_version": st["version"]})
            if status in (200, 404):
                state["entities"].pop(nm)
                deleted.append(f"✗ {nm}（已删除）")
            elif status == 409 and r.get("current"):
                (edir / f"{nm}.md").write_text(r["current"]["content"])
                state["entities"][nm] = {"version": r["current"]["version"],
                                         "sha": sha(r["current"]["content"])}
                print(f"⚠ {nm} 本地已删但云端有更新，已恢复云端版本；确认要删除请再次删除文件并 tn push")
            else:
                die(r.get("detail", f"删除 {nm} 失败"))

    save_state(space, state)
    if not pushed and not deleted and not conflicts:
        print("没有需要推送的改动。")
        return
    if pushed or deleted:
        print(f"✓ 已回流到云端空间 '{cfg['space_name']}'")
        for line in pushed + deleted:
            print(f"  {line}")
    if conflicts:
        report_conflicts(space, conflicts)


def git_push(cfg, space, message):
    me = git_author()
    r = run_git(space, "status", "--porcelain")
    changed = [ln[3:].strip().strip('"') for ln in r.stdout.splitlines()
               if ln[3:].strip().strip('"').startswith(f"{ENTITY_DIR}/")
               and ln[3:].strip().strip('"').endswith(".md")
               and not ln.startswith(" D") and not ln.startswith("D ")]
    for f in changed:
        p = space / f
        p.write_text(stamp(p.read_text(errors="replace"), me))

    regenerate_index(space)
    run_git(space, "add", "-A")
    r = run_git(space, "status", "--porcelain")
    msg = message or f"sync: {len(changed)} entities updated by {me}"
    if r.stdout.strip():
        run_git(space, "commit", "-m", msg)
    else:
        ahead = run_git(space, "rev-list", "--count", "@{u}..HEAD", check=False)
        if ahead.returncode == 0 and int(ahead.stdout.strip() or 0) == 0:
            print("没有需要推送的改动。")
            return
        if ahead.returncode != 0 and run_git(space, "rev-parse", "HEAD", check=False).returncode != 0:
            print("没有需要推送的改动。")
            return

    r = run_git(space, "pull", "--rebase", check=False)
    if r.returncode != 0 and not is_empty_remote_error(r.stderr):
        if "CONFLICT" in r.stdout + r.stderr or (space / ".git" / "rebase-merge").exists():
            conflicts = run_git(space, "diff", "--name-only", "--diff-filter=U").stdout.strip()
            print("⚠ 与云端产生冲突，需要语义合并。冲突文件：", file=sys.stderr)
            print(conflicts, file=sys.stderr)
            print(f"\n处理方式：编辑上述文件解决冲突（保留双方有效信息），然后执行：", file=sys.stderr)
            print(f"  git -C {space} add -A && git -C {space} rebase --continue && tn push", file=sys.stderr)
            sys.exit(2)
        die(f"pull --rebase 失败:\n{r.stderr.strip()}")

    r = run_git(space, "push", "-u", "origin", "HEAD", check=False)
    if r.returncode != 0:
        die(f"push 失败:\n{r.stderr.strip()}")
    print(f"✓ 已回流到共享空间 '{cfg['space_name']}': {msg}")
    for f in changed:
        print(f"  ↑ {f}")


# ---------- 命令: search / show / new / ls / status / where ----------

def cmd_search(args):
    _, cfg, space = load_config()
    if cfg["backend"] == "api":
        q = " ".join(args.terms)
        status, r = api("GET", cfg,
                        f"/api/spaces/{cfg['space_id']}/search?q={urllib.parse.quote(q)}&limit={args.limit}")
        if status != 200:
            die(r.get("detail", "检索失败"))
        results = r["results"]
        if not results:
            print(f"没有匹配 {q} 的实体。可用 tn ls 浏览全部。")
            return
        print(f"匹配 {len(results)} 个实体（按相关度排序）：\n")
        for x in results:
            print(f"● {x['name']}  [{x['type']}]  {x['title']}")
            print(f"  {x['snippet']}")
            if x["links"]:
                print(f"  links: {', '.join(x['links'])}")
            print(f"  updated: {x['updated_at']} by {x['updated_by']}\n")
        print("用 tn show <name> 查看全文（show 前先 tn pull 保证本地最新）。")
        return

    ents = load_all_entities(space)
    if not ents:
        print("空间为空，没有可检索的实体。")
        return
    scored = sorted([(score_entity(n, e, args.terms), n, e) for n, e in ents.items()
                     if score_entity(n, e, args.terms) > 0], key=lambda x: -x[0])[: args.limit]
    if not scored:
        print(f"没有匹配 {' '.join(args.terms)} 的实体。可用 tn ls 浏览全部索引。")
        return
    print(f"匹配 {len(scored)} 个实体（按相关度排序）：\n")
    for _, name, e in scored:
        meta, body = e["meta"], e["body"]
        first = next((l.strip() for l in body.splitlines() if l.strip() and not l.startswith("#")), "")
        print(f"● {name}  [{meta.get('type', 'entity')}]  {meta.get('title', '')}")
        print(f"  {first[:100]}")
        links = extract_links(body)
        if links:
            print(f"  links: {', '.join(links[:8])}")
        print(f"  updated: {meta.get('updated_at', '?')} by {meta.get('updated_by', '?')}\n")
    print("用 tn show <name> 查看全文。")


def cmd_show(args):
    _, _, space = load_config()
    ents = load_all_entities(space)
    for name in args.names:
        e = ents.get(name)
        if not e:
            print(f"（未找到实体 {name}，先 tn pull 试试）\n", file=sys.stderr)
            continue
        print(f"════ {name} ({e['path'].name}) ════")
        print(e["path"].read_text())
        links = [l for l in extract_links(e["body"]) if l in ents]
        if links:
            print(f"—— 出链（可用 tn show 继续查看）: {', '.join(links)}")
        print()


def cmd_new(args):
    _, cfg, space = load_config()
    name = re.sub(r"[^\w.-]", "-", args.name).strip("-").lower()
    p = space / ENTITY_DIR / f"{name}.md"
    if p.exists():
        die(f"实体 {name} 已存在（{p}）。直接编辑该文件后 tn push 即可。")
    email = get_cred(cfg["server"])["email"] if cfg["backend"] == "api" else git_author()
    meta = {"name": name, "type": args.type, "title": args.title or name,
            "tags": args.tags.split(",") if args.tags else [],
            "created_by": email, "updated_by": email, "updated_at": today()}
    p.parent.mkdir(exist_ok=True)
    p.write_text(dump_text(meta, args.body or "（待补充。用 [[其他实体名]] 建立关联。）\n"))
    print(f"✓ 已创建实体: {p}")
    print("编辑补充内容后运行 tn push 回流。")


def cmd_ls(args):
    _, cfg, space = load_config()
    ents = load_all_entities(space)
    print(f"共享空间 '{cfg['space_name']}'（{len(ents)} 个实体，本地视图，最新内容先 tn pull）\n")
    for name, e in sorted(ents.items(), key=lambda x: str(x[1]["meta"].get("updated_at", "")), reverse=True):
        meta = e["meta"]
        print(f"  {str(meta.get('updated_at', '?')):10}  [{meta.get('type', 'entity'):8}]  {name}  — {meta.get('title', '')}")


def cmd_status(args):
    _, cfg, space = load_config()
    if cfg["backend"] == "git":
        r = run_git(space, "status", "--porcelain")
        if not r.stdout.strip():
            print(f"空间 '{cfg['space_name']}' 无本地待推送改动。")
            return
        print(f"空间 '{cfg['space_name']}' 待推送改动（tn push 回流，推送前确认无敏感信息）：")
        print(r.stdout.rstrip())
        return
    state = load_state(space)
    edir = space / ENTITY_DIR
    changes = []
    for p in sorted(edir.glob("*.md")) if edir.is_dir() else []:
        nm = p.stem
        st = state["entities"].get(nm)
        text = p.read_text(errors="replace")
        if CONFLICT_MARK in text:
            changes.append(f"  ✗ 冲突待合并  {nm}")
        elif st is None:
            changes.append(f"  + 新增        {nm}")
        elif st["sha"] != sha(text):
            changes.append(f"  ~ 修改        {nm}")
    for nm in state["entities"]:
        if not (edir / f"{nm}.md").is_file():
            changes.append(f"  - 删除        {nm}")
    if not changes:
        print(f"空间 '{cfg['space_name']}' 无本地待推送改动。")
        return
    print(f"空间 '{cfg['space_name']}' 待推送改动（tn push 回流，推送前确认无敏感信息）：")
    print("\n".join(changes))


def cmd_where(args):
    _, _, space = load_config()
    print(space)


def main():
    ap = argparse.ArgumentParser(prog="tn", description="Team Network — 团队共享上下文空间 CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("login", help="登录云端服务")
    p.add_argument("server", help="服务器地址，如 https://tn.example.com")
    p.add_argument("--token", help="直接使用网页后台生成的 CLI token（免输密码）")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("connect", help="一条命令完成登录+绑定（网页空间页可复制完整命令）")
    p.add_argument("target", help="http(s)://服务器/s/<空间id>")
    p.add_argument("--token", required=True, help="网页空间页生成的接入 token")
    p.set_defaults(func=cmd_connect)

    p = sub.add_parser("init", help="绑定当前 workspace 到共享空间")
    p.add_argument("target", help="http(s)://服务器/s/<空间id> 或 git 远端地址")
    p.add_argument("--name", help="空间名（仅 git 后端，默认取自远端地址）")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("pull", help="同步云端实体")
    p.set_defaults(func=cmd_pull)

    p = sub.add_parser("push", help="回流本地实体改动")
    p.add_argument("-m", "--message", help="提交说明（仅 git 后端使用）")
    p.set_defaults(func=cmd_push)

    p = sub.add_parser("search", help="检索实体")
    p.add_argument("terms", nargs="+", help="关键词（空格分隔，中英文均可）")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("show", help="查看实体全文")
    p.add_argument("names", nargs="+")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("new", help="创建实体骨架")
    p.add_argument("name")
    p.add_argument("--type", choices=ENTITY_TYPES, default="entity")
    p.add_argument("--title", default="")
    p.add_argument("--tags", default="", help="逗号分隔")
    p.add_argument("--body", default="", help="实体正文（可选，也可创建后编辑文件）")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("ls", help="列出全部实体")
    p.set_defaults(func=cmd_ls)

    p = sub.add_parser("status", help="查看待推送改动")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("where", help="打印空间本地路径")
    p.set_defaults(func=cmd_where)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
