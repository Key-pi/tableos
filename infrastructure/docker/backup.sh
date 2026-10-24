#!/bin/sh
# Production database backups (service `backup` in docker-compose.prod.yml):
# a compressed pg_dump once a day, the last BACKUP_KEEP_DAYS days are kept in
# the `backups` volume. Copy them off the server as well (DEPLOYMENT.md).
set -eu

keep_days="${BACKUP_KEEP_DAYS:-14}"
interval="${BACKUP_INTERVAL_SECONDS:-86400}"
mkdir -p /backups

while true; do
  stamp="$(date -u +%Y%m%d-%H%M%S)"
  file="/backups/botera-shop-${stamp}.sql.gz"
  if pg_dump --no-owner --clean --if-exists | gzip > "${file}.partial"; then
    mv "${file}.partial" "${file}"
    echo "backup: ${file} ($(du -h "${file}" | cut -f1))"
  else
    rm -f "${file}.partial"
    echo "backup: pg_dump FAILED" >&2
  fi
  find /backups -name 'botera-shop-*.sql.gz' -mtime "+${keep_days}" -delete
  sleep "${interval}"
done
