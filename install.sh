#!/bin/bash
# 安装 tn CLI 和 team-network skill（针对当前用户）
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="$HOME/.local/bin"
SKILL_DIR="$HOME/.claude/skills/team-network"

# 1. tn CLI -> ~/.local/bin/tn
mkdir -p "$BIN_DIR"
ln -sf "$HERE/cli/tn.py" "$BIN_DIR/tn"
chmod +x "$HERE/cli/tn.py"
echo "✓ tn CLI -> $BIN_DIR/tn"

if ! echo ":$PATH:" | grep -q ":$BIN_DIR:"; then
  echo "  ⚠ $BIN_DIR 不在 PATH 中，请在 ~/.zshrc 中加入:"
  echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

# 2. skill -> ~/.claude/skills/team-network（符号链接，改源码即时生效）
mkdir -p "$HOME/.claude/skills"
if [ -e "$SKILL_DIR" ] && [ ! -L "$SKILL_DIR" ]; then
  echo "  ⚠ $SKILL_DIR 已存在且不是符号链接，跳过（如需覆盖请手动删除后重跑）"
else
  ln -sfn "$HERE/skill/team-network" "$SKILL_DIR"
  echo "✓ skill -> $SKILL_DIR"
fi

echo
echo "完成。使用方式："
echo "  1. 准备一个团队共享的 git 仓库（空仓库即可）作为共享空间"
echo "  2. 在任意 workspace 里运行: tn init <git-remote>"
echo "  3. 之后该 workspace 里的 agent 会自动 pull/push 团队上下文"
