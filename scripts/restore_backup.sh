#!/bin/sh
# Restore a backup produced by scripts/backup.sh (or a manual `mariadb-dump`)
# into the `db` service of this docker-compose stack.
#
# Works the same way for a brand-new installation: bring up just `db`
#   (docker compose up -d db), wait until it reports healthy, run this
# script, THEN start `app` (docker compose up -d app) - the restored dump
# already carries its own schema (including the alembic_version table), so
# `alembic upgrade head` only has to apply whatever migrations came after the
# backup was taken.
#
# Usage: scripts/restore_backup.sh path/to/makerspaceapi_20260101T000000Z.sql.gz
set -eu

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 <backup-file.sql.gz>" >&2
  exit 1
fi

BACKUP_FILE="$1"
if [ ! -f "$BACKUP_FILE" ]; then
  echo "File not found: $BACKUP_FILE" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

DB_NAME="${DB_NAME:-makerspaceapi}"
DB_USER="${DB_USER:-makerspace}"
DB_PASSWORD="${DB_PASSWORD:?DB_PASSWORD not set - check your .env}"

echo "This will OVERWRITE every table in database '$DB_NAME' with the contents of:"
echo "  $BACKUP_FILE"
printf "Type 'yes' to continue: "
read -r CONFIRM
if [ "$CONFIRM" != "yes" ]; then
  echo "Aborted."
  exit 1
fi

echo "Restoring..."
gunzip -c "$BACKUP_FILE" | docker compose -f "$SCRIPT_DIR/docker-compose.yml" \
  exec -T -e MYSQL_PWD="$DB_PASSWORD" db mariadb -u "$DB_USER" "$DB_NAME"

echo "Restore complete."
echo "If the app is already running against this database, restart it:"
echo "  docker compose restart app"
