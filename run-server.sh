#!/bin/bash
# 启动 Team Network 云端服务（默认 http://0.0.0.0:8787）
set -euo pipefail
cd "$(dirname "$0")/server"

if [ ! -d .venv ]; then
  python3 -m venv .venv
  ./.venv/bin/pip install -q -r requirements.txt
fi

PORT="${TN_PORT:-8787}"
echo "Team Network server → http://localhost:$PORT"
exec ./.venv/bin/uvicorn app:app --host 0.0.0.0 --port "$PORT"
