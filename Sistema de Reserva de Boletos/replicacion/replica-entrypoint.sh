#!/bin/bash
# Replica en streaming: la primera vez copia el primario (pg_basebackup -R deja el nodo como standby)
# y despues arranca PostgreSQL en modo hot standby (solo lectura).
set -e
if [ ! -s "$PGDATA/PG_VERSION" ]; then
  until pg_isready -h db -U srb >/dev/null 2>&1; do echo "esperando al primario..."; sleep 1; done
  PGPASSWORD=replicator pg_basebackup -h db -U replicator -D "$PGDATA" -R -X stream
  chmod 700 "$PGDATA"
fi
exec postgres
