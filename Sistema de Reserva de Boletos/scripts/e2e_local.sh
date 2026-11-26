#!/usr/bin/env bash
# Prueba de punta a punta en UNA maquina: BD real + persistencia + GT + RMC + cliente.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
export HOST_CAPA_CONSUMIDORES=127.0.0.1 HOST_CAPA_SERVICIOS=127.0.0.1 HOST_CAPA_PERSISTENCIA=127.0.0.1

sql() { docker exec srb-postgres psql -U srb -d srb -c "$1"; }

docker compose up -d db >/dev/null
until docker exec srb-postgres pg_isready -U srb -d srb >/dev/null 2>&1; do sleep 1; done
$PY -m capa_persistencia.seed --reset

PIDS=()
cleanup() { kill "${PIDS[@]}" 2>/dev/null || true; wait 2>/dev/null || true; }
trap cleanup EXIT
$PY -m capa_persistencia.persistence_main & PIDS+=($!)
$PY -m capa_servicios.service_main gt & PIDS+=($!)
$PY -m capa_servicios.service_main rmc & PIDS+=($!)
sleep 2

echo; echo "=== ANTES ==="
sql "select evento_id, fecha, capacidad, puestos_ocupados from ocurrencia where evento_id='E1' order by fecha"

echo; echo "=== CASO basico ==="
$PY -m capa_consumidores.consumers_main caso basico | tail -n 4

echo; echo "=== DESPUES ==="
sql "select evento_id, fecha, capacidad, puestos_ocupados from ocurrencia where evento_id='E1' order by fecha"
sql "select id, id_cliente, ocurrencia_id, n_puestos, estado from reserva"
