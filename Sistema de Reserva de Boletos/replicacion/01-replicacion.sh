#!/bin/bash
# Se ejecuta UNA vez, al crear el volumen del primario: crea el usuario de replicacion y le permite conectarse.
set -e
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres \
  -c "CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD 'replicator';"
echo "host replication replicator all scram-sha-256" >> "$PGDATA/pg_hba.conf"
