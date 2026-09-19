#!/bin/sh
# Runs inside the `backup` service (see docker-compose.yml). Periodically dumps
# the MariaDB database to a gzip-compressed SQL file and prunes old backups.
#
# BACKUP_INTERVAL_HOURS=0 (default) disables this entirely - the container
# just idles, so `docker compose up` doesn't need a separate profile/flag to
# skip backups when they're not wanted.
set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
DB_HOST="${DB_HOST:-db}"
DB_NAME="${DB_NAME:?DB_NAME must be set}"
DB_USER="${DB_USER:?DB_USER must be set}"
DB_PASSWORD="${DB_PASSWORD:?DB_PASSWORD must be set}"
BACKUP_INTERVAL_HOURS="${BACKUP_INTERVAL_HOURS:-0}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"

case "$BACKUP_INTERVAL_HOURS" in
  ''|*[!0-9]*)
    echo "[backup] invalid BACKUP_INTERVAL_HOURS='$BACKUP_INTERVAL_HOURS' - treating as 0 (disabled)" >&2
    BACKUP_INTERVAL_HOURS=0
    ;;
esac

if [ "$BACKUP_INTERVAL_HOURS" -eq 0 ]; then
  echo "[backup] BACKUP_INTERVAL_HOURS=0 - automatic backups disabled, idling"
  exec sleep infinity
fi

case "$BACKUP_RETENTION_DAYS" in
  ''|*[!0-9]*)
    echo "[backup] invalid BACKUP_RETENTION_DAYS='$BACKUP_RETENTION_DAYS' - disabling pruning" >&2
    BACKUP_RETENTION_DAYS=0
    ;;
esac

mkdir -p "$BACKUP_DIR"
echo "[backup] enabled: dumping every ${BACKUP_INTERVAL_HOURS}h, retention ${BACKUP_RETENTION_DAYS} days (0 = keep forever), writing to $BACKUP_DIR"

while true; do
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  dest="$BACKUP_DIR/makerspaceapi_${timestamp}.sql.gz"
  tmp="${dest}.part"

  echo "[backup] $(date -u +%FT%TZ) starting dump -> $dest"
  if MYSQL_PWD="$DB_PASSWORD" mariadb-dump \
        --host="$DB_HOST" --user="$DB_USER" \
        --single-transaction --routines --triggers --skip-lock-tables \
        "$DB_NAME" | gzip > "$tmp"
  then
    mv "$tmp" "$dest"
    echo "[backup] $(date -u +%FT%TZ) done: $(du -h "$dest" | cut -f1)"
  else
    echo "[backup] $(date -u +%FT%TZ) FAILED - see mariadb-dump output above" >&2
    rm -f "$tmp"
  fi

  if [ "$BACKUP_RETENTION_DAYS" -gt 0 ]; then
    find "$BACKUP_DIR" -maxdepth 1 -name 'makerspaceapi_*.sql.gz' -mtime "+${BACKUP_RETENTION_DAYS}" -print -delete
  fi

  sleep "$((BACKUP_INTERVAL_HOURS * 3600))"
done
