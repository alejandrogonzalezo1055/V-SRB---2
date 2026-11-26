#!/usr/bin/env bash
# Prepara UNA maquina para una capa:  ./scripts/deploy.sh <consumidores|servicios|persistencia>
# Misma preparacion en las tres maquinas => mismo codigo, mismas dependencias.
set -euo pipefail
cd "$(dirname "$0")/.."

CAPA="${1:-}"
case "$CAPA" in consumidores|servicios|persistencia) ;; *)
  echo "uso: $0 <consumidores|servicios|persistencia>" >&2; exit 2 ;;
esac

# Las dependencias fijadas necesitan Python 3.10 o superior.
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
  echo "[x] Se necesita Python 3.10 o superior; esta maquina tiene: $(python3 --version 2>&1)" >&2
  exit 1
fi

ENV_FILE="${ENV_FILE:-.env}"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
echo "[ok] entorno Python listo (.venv)"

if [ ! -f "$ENV_FILE" ]; then
  cp .env.example "$ENV_FILE"
  echo "[!] se creo .env desde .env.example: REVISE las 3 IPs (HOST_CAPA_*) antes de arrancar"
fi
for v in HOST_CAPA_CONSUMIDORES HOST_CAPA_SERVICIOS HOST_CAPA_PERSISTENCIA; do
  grep -qE "^$v=[0-9a-zA-Z.]+" "$ENV_FILE" || { echo "[x] falta $v en $ENV_FILE" >&2; exit 1; }
done
echo "[ok] .env con las 3 IPs:"; grep -E '^HOST_CAPA_' "$ENV_FILE" | sed 's/^/     /'

# La IP de ESTA capa debe pertenecer a ESTA maquina (si no, el .env es de otra red o se copio mal).
case "$CAPA" in
  persistencia) VAR=HOST_CAPA_PERSISTENCIA ;;
  servicios) VAR=HOST_CAPA_SERVICIOS ;;
  consumidores) VAR=HOST_CAPA_CONSUMIDORES ;;
esac
IP_CAPA="$(grep -E "^$VAR=" "$ENV_FILE" | cut -d= -f2 | tr -d '[:space:]')"
IPS_PROPIAS="$(ip -4 -o addr show 2>/dev/null | awk '{print $4}' | cut -d/ -f1)"
if [ "${SRB_SIN_CHECK_IP:-0}" != "1" ] && [ "$IP_CAPA" != "localhost" ] \
   && ! printf '%s\n' "$IPS_PROPIAS" | grep -qx "$IP_CAPA"; then
  echo "[x] $VAR=$IP_CAPA no es una IP de esta maquina. IPs de esta maquina:" >&2
  printf '%s\n' "$IPS_PROPIAS" | sed 's/^/     /' >&2
  echo "    Corrija $ENV_FILE (o use SRB_SIN_CHECK_IP=1 solo para pruebas locales)." >&2
  exit 1
fi
if [ "${SRB_SIN_CHECK_IP:-0}" = "1" ]; then
  echo "[!] verificacion de IP omitida (SRB_SIN_CHECK_IP=1): $VAR=$IP_CAPA"
else
  echo "[ok] $VAR=$IP_CAPA pertenece a esta maquina"
fi

case "$CAPA" in
persistencia)
  docker compose up -d
  echo "[ok] PostgreSQL y pgAdmin arriba (pgAdmin: http://localhost:5050)"
  echo "Siguiente:"
  echo "  .venv/bin/python -m capa_persistencia.seed --reset     # carga los datos iniciales"
  echo "  .venv/bin/python -m capa_persistencia.persistence_main"
  ;;
servicios)
  echo "Siguiente (cada uno en su terminal):"
  echo "  .venv/bin/python -m capa_servicios.service_main gt"
  echo "  .venv/bin/python -m capa_servicios.service_main rmc"
  ;;
consumidores)
  echo "Siguiente:"
  echo "  .venv/bin/python -m capa_consumidores.consumers_main            # interactivo"
  echo "  .venv/bin/python -m capa_consumidores.consumers_main caso basico"
  ;;
esac
echo "Verifique la red entre las 3 maquinas con:  .venv/bin/python scripts/verificar_red.py"
