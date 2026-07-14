#!/bin/bash
# 把网页后台 + CLI + skill 收集进 public/ 供 Workers Assets 分发
set -euo pipefail
cd "$(dirname "$0")"
rm -rf public
mkdir -p public/cli public/skill
cp ../server/static/* public/
cp ../cli/tn.py public/cli/tn.py
cp ../skill/team-network/SKILL.md public/skill/SKILL.md
echo "✓ public/ 就绪"
