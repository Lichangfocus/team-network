#!/bin/bash
# SQLite 在线热备份，保留最近 14 份
# cron 示例（每天 04:00）: 0 4 * * * /path/to/scripts/backup.sh
set -euo pipefail
DB="${TN_DB:-$(cd "$(dirname "$0")/.." && pwd)/server/data/tn.db}"
DEST="${TN_BACKUP_DIR:-$(dirname "$DB")/backups}"
mkdir -p "$DEST"
sqlite3 "$DB" ".backup '$DEST/tn-$(date +%Y%m%d-%H%M%S).db'"
ls -t "$DEST"/tn-*.db 2>/dev/null | tail -n +15 | xargs rm -f
echo "✓ 备份完成: $(ls -t "$DEST"/tn-*.db | head -1)"
