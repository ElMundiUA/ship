#!/usr/bin/env bash
# backup-postgres.sh — durable Postgres snapshot for the Ship cloud platform.
#
# Designed to be safe to run from cron on the same host that owns the
# `postgres` docker-compose service. Three guarantees:
#
#   1. Atomic write: dump goes to a temp file in the destination directory
#      and is renamed only after pg_dump exits 0. A failed dump never
#      replaces the previous good snapshot.
#   2. Rotation: keeps the most recent ${BACKUP_KEEP:-14} dumps and deletes
#      the rest, so disk usage is bounded without an extra cron job.
#   3. Optional off-host copy: if BACKUP_UPLOAD_CMD is set, the dump path
#      is appended to it and the command is run after the rename. Use this
#      to ship to S3 / Bunny Storage / rsync.net without baking those
#      credentials into this script.
#
# Required env (read from the host shell or `.env` next to this script):
#   POSTGRES_USER         (default: ship)
#   POSTGRES_DB           (default: ship)
#
# Optional env:
#   BACKUP_DIR            target dir (default: ./backups)
#   BACKUP_KEEP           snapshots to retain (default: 14)
#   BACKUP_COMPOSE_FILE   compose file (default: docker-compose.yml)
#   BACKUP_DB_SVC         compose service (default: postgres)
#   BACKUP_UPLOAD_CMD     command run as: $cmd <path-to-dump>
#   BACKUP_PRUNE_ONLY     if "1", just prune old snapshots and exit
#
# Usage:
#   scripts/backup-postgres.sh
#   BACKUP_DIR=/srv/backups BACKUP_KEEP=30 scripts/backup-postgres.sh
#
# Suggested cron (root crontab on the VPS):
#   17 4 * * * cd /opt/ship && BACKUP_DIR=/srv/ship-backups ./scripts/backup-postgres.sh >> /var/log/ship-backup.log 2>&1

set -euo pipefail

PG_USER="${POSTGRES_USER:-ship}"
PG_DB="${POSTGRES_DB:-ship}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
BACKUP_KEEP="${BACKUP_KEEP:-14}"
COMPOSE_FILE="${BACKUP_COMPOSE_FILE:-docker-compose.yml}"
DB_SVC="${BACKUP_DB_SVC:-postgres}"

# Pick the compose binary the operator has — `docker compose` (plugin, modern)
# is preferred but the older `docker-compose` keeps working on legacy hosts.
if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose -f "$COMPOSE_FILE")
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose -f "$COMPOSE_FILE")
else
  echo "FATAL: neither 'docker compose' nor 'docker-compose' is installed" >&2
  exit 1
fi

prune() {
  # ls newest-first, skip the first $BACKUP_KEEP, delete the rest. We list
  # only the standard `ship-*.sql.gz` filenames so a manually placed file
  # in BACKUP_DIR (e.g. README) is never touched.
  local kept=0
  shopt -s nullglob
  while IFS= read -r -d '' file; do
    kept=$((kept + 1))
    if [ "$kept" -gt "$BACKUP_KEEP" ]; then
      rm -f -- "$file"
      echo "[prune] removed $file"
    fi
  done < <(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'ship-*.sql.gz' -print0 \
            | xargs -0 -r ls -t1 -- 2>/dev/null \
            | tr '\n' '\0')
}

mkdir -p "$BACKUP_DIR"

if [ "${BACKUP_PRUNE_ONLY:-0}" = "1" ]; then
  prune
  exit 0
fi

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="${BACKUP_DIR%/}/ship-${stamp}.sql.gz"
tmp="${target}.partial"

cleanup() {
  rm -f -- "$tmp" 2>/dev/null || true
}
trap cleanup EXIT

echo "[backup] dumping db=${PG_DB} user=${PG_USER} -> ${target}"

# pg_dump runs *inside* the postgres container so we don't need libpq on
# the host. `-T` keeps stdin attached so docker doesn't allocate a TTY
# (which would corrupt the binary stream).
"${COMPOSE[@]}" exec -T "$DB_SVC" pg_dump -U "$PG_USER" "$PG_DB" \
  | gzip --rsyncable -c > "$tmp"

# Sanity-check size: an empty dump is almost certainly a misconfigured
# user/db pair, not a healthy state.
size=$(stat -f%z "$tmp" 2>/dev/null || stat -c%s "$tmp")
if [ "$size" -lt 1024 ]; then
  echo "FATAL: dump suspiciously small (${size} bytes); leaving as ${tmp}" >&2
  trap - EXIT
  exit 2
fi

mv -f -- "$tmp" "$target"
trap - EXIT
echo "[backup] wrote ${target} ($(du -h "$target" | cut -f1))"

if [ -n "${BACKUP_UPLOAD_CMD:-}" ]; then
  echo "[upload] ${BACKUP_UPLOAD_CMD} ${target}"
  # `eval` so operators can use shell expansions inside BACKUP_UPLOAD_CMD,
  # e.g. `aws s3 cp --storage-class STANDARD_IA`.
  eval "${BACKUP_UPLOAD_CMD} \"${target}\""
fi

prune
echo "[backup] done"
