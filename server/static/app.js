/* Team Network 网页后台 — 无构建原生 JS SPA（hash 路由） */

const $ = (sel) => document.querySelector(sel);
const app = $("#app");

// ---------- API ----------
const API = {
  token: localStorage.getItem("tn_token") || "",
  async call(method, path, data) {
    const opts = { method, headers: {} };
    if (this.token) opts.headers["Authorization"] = "Bearer " + this.token;
    if (data !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(data);
    }
    const res = await fetch(path, opts);
    let body = null;
    try { body = await res.json(); } catch {}
    if (res.status === 401 && location.hash !== "#/login") {
      localStorage.removeItem("tn_token");
      location.hash = "#/login";
      throw new Error("请先登录");
    }
    if (!res.ok) throw new Error((body && body.detail) || `请求失败 (${res.status})`);
    return body;
  },
  get: (p) => API.call("GET", p),
  post: (p, d) => API.call("POST", p, d ?? {}),
  put: (p, d) => API.call("PUT", p, d),
  del: (p, d) => API.call("DELETE", p, d),
};

// ---------- 工具 ----------
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function h(html) { const d = document.createElement("div"); d.innerHTML = html; return d; }
function fmtDate(s) { return (s || "").replace("T", " ").replace("Z", " UTC"); }

// 极简 markdown 渲染（标题/粗体/行内代码/代码块/列表/wiki链接）
function renderMd(md, spaceId) {
  let text = esc(md);
  text = text.replace(/```([\s\S]*?)```/g, (_, c) => `<pre>${c}</pre>`);
  text = text.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  text = text.replace(/^### (.*)$/gm, "<h3>$1</h3>");
  text = text.replace(/^## (.*)$/gm, "<h2>$1</h2>");
  text = text.replace(/^# (.*)$/gm, "<h1>$1</h1>");
  text = text.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  text = text.replace(/\[\[([\w./-]+)\]\]/g,
    `<a class="wikilink" href="#/space/${spaceId}/entity/$1">$1</a>`);
  text = text.replace(/^- (.*)$/gm, "<li>$1</li>").replace(/(<li>[\s\S]*?<\/li>)(?!\s*<li>)/g, "<ul>$1</ul>");
  return text.split(/\n{2,}/).map((p) =>
    /^<(h\d|pre|ul)/.test(p.trim()) ? p : `<p>${p.replace(/\n/g, "<br>")}</p>`).join("\n");
}

function parseFrontmatter(content) {
  const m = content.match(/^---\n([\s\S]*?)\n---\n?([\s\S]*)$/);
  if (!m) return [{}, content];
  const meta = {};
  for (const line of m[1].split("\n")) {
    const kv = line.match(/^([A-Za-z_][\w-]*):\s*(.*)$/);
    if (kv) meta[kv[1]] = kv[2].trim();
  }
  return [meta, m[2]];
}

function setUserbox(user) {
  $("#userbox").innerHTML = user
    ? `<span>${esc(user.name)} &lt;${esc(user.email)}&gt;</span>
       <button class="sm ghost" id="btn-cli-token">CLI token</button>
       <button class="sm ghost" id="btn-logout">退出</button>`
    : "";
  if (user) {
    $("#btn-logout").onclick = () => { localStorage.removeItem("tn_token"); API.token = ""; location.hash = "#/login"; };
    $("#btn-cli-token").onclick = async () => {
      const r = await API.post("/api/cli-token");
      prompt("复制此 token，在终端运行 tn login <服务器地址> --token <token>：", r.token);
    };
  }
}

// ---------- 页面 ----------
async function pageLogin() {
  setUserbox(null);
  let mode = localStorage.getItem("tn_pending_invite") ? "register" : "login";
  let inviteBanner = "";
  const pending = localStorage.getItem("tn_pending_invite");
  if (pending) {
    try {
      const inv = await API.get(`/api/invites/${pending}`);
      inviteBanner = `<p class="ok">受邀加入 team「${esc(inv.team_name)}」——注册或登录后自动加入。</p>`;
    } catch { localStorage.removeItem("tn_pending_invite"); }
  }
  const render = () => {
    app.innerHTML = `
    <div class="card center">
      <h1>${mode === "login" ? "登录" : "注册"}</h1>
      <p class="sub">Team Network — 团队共享上下文空间</p>
      ${inviteBanner}
      ${mode === "register" ? `<input id="f-name" placeholder="名字">` : ""}
      <input id="f-email" type="email" placeholder="邮箱">
      <input id="f-pw" type="password" placeholder="密码（至少 8 位）">
      <div class="err" id="f-err"></div>
      <div class="row">
        <button id="f-go">${mode === "login" ? "登录" : "注册"}</button>
        <a href="javascript:void 0" id="f-switch">${mode === "login" ? "没有账号？注册" : "已有账号？登录"}</a>
      </div>
    </div>`;
    $("#f-switch").onclick = () => { mode = mode === "login" ? "register" : "login"; render(); };
    $("#f-go").onclick = async () => {
      try {
        const payload = { email: $("#f-email").value, password: $("#f-pw").value };
        if (mode === "register") payload.name = $("#f-name").value;
        const r = await API.post(`/api/${mode}`, payload);
        API.token = r.token;
        localStorage.setItem("tn_token", r.token);
        const code = localStorage.getItem("tn_pending_invite");
        if (code) {
          localStorage.removeItem("tn_pending_invite");
          try {
            const j = await API.post(`/api/invites/${code}/accept`);
            location.hash = `#/team/${j.team_id}`;
            return;
          } catch {}
        }
        location.hash = "#/teams";
      } catch (e) { $("#f-err").textContent = e.message; }
    };
    $("#f-pw").addEventListener("keydown", (e) => e.key === "Enter" && $("#f-go").click());
  };
  render();
}

async function pageTeams() {
  const me = await API.get("/api/me");
  setUserbox(me);
  const teams = await API.get("/api/teams");
  app.innerHTML = `
    <div class="card">
      <h1>我的 Team</h1>
      <p class="sub">一个 team 可以创建多个共享空间；每个本地 workspace 绑定一个空间。</p>
      <div id="team-list">${teams.map((t) => `
        <div class="list-item">
          <div><a href="#/team/${t.id}"><strong>${esc(t.name)}</strong></a>
            <span class="badge">${t.role}</span></div>
          <div class="meta">${t.members} 成员 · ${t.spaces} 空间</div>
        </div>`).join("") || `<p class="meta">还没有 team。</p>`}
      </div>
    </div>
    <div class="card">
      <h2>创建 team</h2>
      <div class="row"><input id="t-name" placeholder="team 名称"><button id="t-create">创建</button></div>
      <h2 style="margin-top:20px">用邀请码加入 team</h2>
      <div class="row"><input id="t-code" placeholder="邀请码"><button id="t-join" class="ghost">加入</button></div>
      <div class="err" id="t-err"></div>
    </div>`;
  $("#t-create").onclick = async () => {
    try { const r = await API.post("/api/teams", { name: $("#t-name").value }); location.hash = `#/team/${r.id}`; }
    catch (e) { $("#t-err").textContent = e.message; }
  };
  $("#t-join").onclick = async () => {
    try { const r = await API.post(`/api/invites/${$("#t-code").value.trim()}/accept`); location.hash = `#/team/${r.team_id}`; }
    catch (e) { $("#t-err").textContent = e.message; }
  };
}

async function pageTeam(tid) {
  const me = await API.get("/api/me");
  setUserbox(me);
  const t = await API.get(`/api/teams/${tid}`);
  app.innerHTML = `
    <p><a href="#/teams">← 全部 team</a></p>
    <div class="card">
      <h1>${esc(t.name)}</h1>
      <p class="sub">你的角色：${t.my_role}</p>
      <h2>共享空间</h2>
      ${t.spaces.map((s) => `
        <div class="list-item">
          <a href="#/space/${s.id}"><strong>${esc(s.name)}</strong></a>
          <span class="meta">${s.entities} 实体 · rev ${s.rev}</span>
        </div>`).join("") || `<p class="meta">还没有空间。</p>`}
      <div class="row" style="margin-top:12px">
        <input id="s-name" placeholder="新空间名称"><button id="s-create">创建空间</button>
      </div>
      <div class="err" id="s-err"></div>
    </div>
    <div class="card">
      <h2>成员（${t.members.length}）</h2>
      ${t.members.map((m) => `
        <div class="list-item">
          <div>${esc(m.name)} <span class="meta">&lt;${esc(m.email)}&gt;</span></div>
          <span class="badge">${m.role}</span>
        </div>`).join("")}
      <div class="row" style="margin-top:12px">
        <button id="inv-create" class="ghost">生成邀请码</button>
        <span class="meta" id="inv-out"></span>
      </div>
    </div>`;
  $("#s-create").onclick = async () => {
    try { const r = await API.post(`/api/teams/${tid}/spaces`, { name: $("#s-name").value }); location.hash = `#/space/${r.id}`; }
    catch (e) { $("#s-err").textContent = e.message; }
  };
  $("#inv-create").onclick = async () => {
    const r = await API.post(`/api/teams/${tid}/invites`);
    const url = `${location.origin}/join/${r.code}`;
    $("#inv-out").innerHTML = `邀请链接（可用 20 次，直接发给同事）：<br><span class="cmd"><code>${esc(url)}</code></span>`;
  };
}

async function pageSpace(sid, entityName) {
  const me = await API.get("/api/me");
  setUserbox(me);
  const sp = await API.get(`/api/spaces/${sid}`);
  if (entityName) return pageEntity(sp, entityName);
  const data = await API.get(`/api/spaces/${sid}/entities`);
  const ents = data.entities.filter((e) => !e.deleted)
    .map((e) => ({ ...e, fm: parseFrontmatter(e.content)[0] }))
    .sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));
  app.innerHTML = `
    <p><a href="#/team/${sp.team_id}">← ${esc(sp.team_name)}</a></p>
    <div class="card">
      <h1>${esc(sp.name)}</h1>
      <p class="sub">${ents.length} 实体 · rev ${sp.rev}</p>
      <h2>接入（两步）</h2>
      <p class="sub">1. 每台电脑装一次（终端执行）：</p>
      <div class="cmd"><code>curl -fsSL ${esc(location.origin)}/install.sh | bash</code></div>
      <p class="sub" style="margin-top:10px">2. 在要共享上下文的项目目录里，把接入命令直接发给你的 agent（或终端执行）：</p>
      <div class="row">
        <button id="gen-connect" class="sm">生成我的接入命令</button>
        <span class="meta">命令含个人 token，别发给他人</span>
      </div>
      <div id="connect-out" style="margin-top:8px"></div>
    </div>
    <div class="card">
      <div class="row" style="margin-bottom:12px">
        <input id="q" placeholder="检索实体（空格分隔关键词）"><button id="q-go" class="ghost">检索</button>
      </div>
      <div id="ent-list">${entListHtml(ents, sid)}</div>
    </div>`;
  $("#gen-connect").onclick = async () => {
    const r = await API.post("/api/cli-token");
    const cmd = `tn connect ${location.origin}/s/${sid} --token ${r.token}`;
    $("#connect-out").innerHTML = `<div class="cmd"><code>${esc(cmd)}</code></div>
      <p class="meta">点选即全选，复制后发给 agent。绑定完成后，该目录下的 agent 会自动在任务前拉取、任务后回流团队上下文。</p>`;
  };
  const doSearch = async () => {
    const q = $("#q").value.trim();
    if (!q) { $("#ent-list").innerHTML = entListHtml(ents, sid); return; }
    const r = await API.get(`/api/spaces/${sid}/search?q=${encodeURIComponent(q)}`);
    $("#ent-list").innerHTML = r.results.map((x) => `
      <div class="list-item">
        <div><a href="#/space/${sid}/entity/${x.name}"><strong>${esc(x.title || x.name)}</strong></a>
          <span class="badge ${x.type}">${x.type}</span>
          <div class="meta">${esc(x.snippet)}</div></div>
        <div class="meta">${esc(x.updated_by)}</div>
      </div>`).join("") || `<p class="meta">无匹配结果。</p>`;
  };
  $("#q-go").onclick = doSearch;
  $("#q").addEventListener("keydown", (e) => e.key === "Enter" && doSearch());
}

function entListHtml(ents, sid) {
  return ents.map((e) => `
    <div class="list-item">
      <div><a href="#/space/${sid}/entity/${e.name}"><strong>${esc(e.fm.title || e.name)}</strong></a>
        <span class="badge ${e.fm.type || "entity"}">${e.fm.type || "entity"}</span></div>
      <div class="meta">${fmtDate(e.updated_at)} · ${esc(e.updated_by)} · v${e.version}</div>
    </div>`).join("") || `<p class="meta">空间还是空的。用 CLI 推送第一个实体，或等 agent 回流。</p>`;
}

async function pageEntity(sp, name) {
  const sid = sp.id;
  let e;
  try { e = await API.get(`/api/spaces/${sid}/entities/${name}`); }
  catch {
    app.innerHTML = `<p><a href="#/space/${sid}">← ${esc(sp.name)}</a></p>
      <div class="card"><h1>${esc(name)}</h1><p class="meta">该实体尚不存在（可能是一个待建链接）。</p></div>`;
    return;
  }
  const [fm, body] = parseFrontmatter(e.content);
  app.innerHTML = `
    <p><a href="#/space/${sid}">← ${esc(sp.name)}</a></p>
    <div class="card">
      <h1>${esc(fm.title || name)} <span class="badge ${fm.type || "entity"}">${fm.type || "entity"}</span></h1>
      <table class="fm-table"><tr>
        <td>name: <code>${esc(name)}</code></td>
        <td>v${e.version}</td>
        <td>${fmtDate(e.updated_at)}</td>
        <td>by ${esc(e.updated_by)}</td>
        ${fm.tags ? `<td>tags: ${esc(fm.tags)}</td>` : ""}
      </tr></table>
      <div class="entity-body">${renderMd(body, sid)}</div>
      <div class="tabs" style="margin-top:16px">
        <button id="btn-history" class="sm">历史版本</button>
        <button id="btn-raw" class="sm">原始 markdown</button>
      </div>
      <div id="extra"></div>
    </div>`;
  $("#btn-raw").onclick = () => { $("#extra").innerHTML = `<pre>${esc(e.content)}</pre>`; };
  $("#btn-history").onclick = async () => {
    const hist = await API.get(`/api/spaces/${sid}/entities/${name}/history`);
    $("#extra").innerHTML = hist.map((v) => `
      <div class="list-item">
        <span>v${v.version} ${v.deleted ? "（删除）" : ""}</span>
        <span class="meta">${fmtDate(v.updated_at)} · ${esc(v.updated_by)}</span>
      </div>`).join("");
  };
}

// ---------- 路由 ----------
async function route() {
  const hash = location.hash || "#/teams";
  let m;
  if ((m = hash.match(/^#\/join\/([\w-]+)$/))) {
    localStorage.setItem("tn_pending_invite", m[1]);
    if (!API.token) { location.hash = "#/login"; return; }
    const code = m[1];
    localStorage.removeItem("tn_pending_invite");
    try {
      const j = await API.post(`/api/invites/${code}/accept`);
      location.hash = `#/team/${j.team_id}`;
    } catch (e) {
      app.innerHTML = `<div class="card"><div class="err">${esc(e.message)}</div></div>`;
    }
    return;
  }
  if (!API.token && hash !== "#/login") { location.hash = "#/login"; return; }
  try {
    if (hash === "#/login") return pageLogin();
    if (hash === "#/teams") return await pageTeams();
    if ((m = hash.match(/^#\/team\/(\d+)$/))) return await pageTeam(m[1]);
    if ((m = hash.match(/^#\/space\/(\d+)$/))) return await pageSpace(m[1], null);
    if ((m = hash.match(/^#\/space\/(\d+)\/entity\/([\w./-]+)$/))) return await pageSpace(m[1], m[2]);
    location.hash = "#/teams";
  } catch (e) {
    if (e.message !== "请先登录") app.innerHTML = `<div class="card"><div class="err">${esc(e.message)}</div></div>`;
  }
}
window.addEventListener("hashchange", route);
route();
