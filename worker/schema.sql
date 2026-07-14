-- Team Network D1 schema（与 server/app.py 的 SQLite schema 对应，密码字段为 hex 文本）
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  pw_salt TEXT NOT NULL,
  pw_hash TEXT NOT NULL,
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
