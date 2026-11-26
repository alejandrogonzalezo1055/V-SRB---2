#!/usr/bin/env bash
# Deshace lo que hizo deploy.sh en ESTA maquina:
#   ./scripts/undeploy.sh <consumidores|servicios|persistencia|todo> [--si] [--simular] [--con-env] [--con-resultados] [--imagenes]
#
# Por defecto borra: los procesos del SRB en ejecucion, el entorno virtual (.venv), las cachés de Python y
# los temporales (/tmp/e2e_*). En 'persistencia' y 'todo' tambien detiene y BORRA los contenedores y los
# VOLUMENES de Docker (PostgreSQL primario, replica y pgAdmin): se pierden TODOS los datos de la BD.
#
#   --si               no pide confirmacion
#   --simular          solo muestra que haria, sin borrar nada
#   --con-env          tambien borra el .env (las IPs de esta maquina)
#   --con-resultados   tambien borra resultados/ y resultados_replica/ (mediciones)
#   --imagenes         tambien borra las imagenes de Docker descargadas (postgres, pgadmin)
# No toca el codigo fuente ni nada fuera de esta carpeta (salvo los temporales /tmp/e2e_*).
set -uo pipefail
cd "$(dirname "$0")/.."

CAPA="" SI=0 SIMULAR=0 CON_ENV=0 CON_RES=0 IMAGENES=0
for a in "$@"; do
  case "$a" in
    consumidores|servicios|persistencia|todo) CAPA="$a" ;;
    --si) SI=1 ;; --simular) SIMULAR=1 ;; --con-env) CON_ENV=1 ;;
    --con-resultados) CON_RES=1 ;; --imagenes) IMAGENES=1 ;;
    *) echo "argumento desconocido: $a" >&2; sed -n '2,15p' "$0" >&2; exit 2 ;;
  esac
done
[ -n "$CAPA" ] || { echo "uso: $0 <consumidores|servicios|persistencia|todo> [opciones]" >&2; exit 2; }

# Seguridad: solo actua si esta carpeta es realmente la del proyecto.
[ -f requirements.txt ] && [ -d comun ] && [ -d capa_servicios ] \
  || { echo "[x] esta carpeta no parece la del SRB (falta requirements.txt/comun/capa_servicios); no se borra nada" >&2; exit 1; }
RAIZ="$(pwd -P)"

DOCKER=0
case "$CAPA" in persistencia|todo) DOCKER=1 ;; esac

echo "Se hara en $RAIZ (capa: $CAPA):"
echo "  - detener los procesos del SRB que esten corriendo"
echo "  - borrar .venv, __pycache__ y .pytest_cache"
echo "  - borrar los temporales /tmp/e2e_*"
[ $DOCKER -eq 1 ]  && echo "  - detener y BORRAR contenedores y volumenes de Docker (¡se pierden los datos de la BD!)"
[ $IMAGENES -eq 1 ] && [ $DOCKER -eq 1 ] && echo "  - borrar las imagenes de Docker (postgres, pgadmin)"
[ $CON_ENV -eq 1 ] && echo "  - borrar .env"
[ $CON_RES -eq 1 ] && echo "  - borrar resultados/ y resultados_replica/"
[ $SIMULAR -eq 1 ] && echo "  (modo simulacion: no se borra nada)"

if [ $SIMULAR -eq 0 ] && [ $SI -eq 0 ]; then
  read -r -p "¿Continuar? Escriba 'si' para confirmar: " r
  [ "$r" = "si" ] || { echo "Cancelado."; exit 1; }
fi

hacer() {  # ejecuta (o solo muestra, en simulacion)
  if [ $SIMULAR -eq 1 ]; then echo "  [simular] $*"; else echo "  $*"; "$@"; fi
}

# 1. procesos. Se detectan por el NOMBRE del ejecutable (debe ser python) Y el modulo/script del SRB en su
# linea de comandos: un shell o editor que solo mencione esos textos nunca coincide, y funciona aunque la ruta
# del proyecto tenga espacios.
echo "[1] procesos"
pids_srb() {
  ps -eo pid=,comm=,args= | awk '$2 ~ /^python/ && ($0 ~ /-m capa_(consumidores|servicios|persistencia)/ || $0 ~ /scripts\/(demo_fallos|benchmark|e2e_etapa2)\.py/) {print $1}'
}
PIDS="$(pids_srb | tr '\n' ' ')"
if [ -n "${PIDS// /}" ]; then
  echo "  procesos del SRB: $PIDS"
  hacer kill $PIDS
  if [ $SIMULAR -eq 0 ]; then
    sleep 1
    RESTO="$(pids_srb | tr '\n' ' ')"
    [ -n "${RESTO// /}" ] && { echo "  siguen vivos: $RESTO -> kill -9"; kill -9 $RESTO 2>/dev/null; }
  fi
else
  echo "  (no hay procesos del SRB en ejecucion)"
fi

# 2. docker
if [ $DOCKER -eq 1 ]; then
  echo "[2] docker"
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    if [ $IMAGENES -eq 1 ]; then hacer docker compose down -v --rmi all --remove-orphans
    else hacer docker compose down -v --remove-orphans; fi
  else
    echo "  [!] Docker no esta instalado o no esta activo: no se pudo limpiar (sudo systemctl start docker y repetir)"
  fi
fi

# 3. archivos locales
echo "[3] archivos"
hacer rm -rf "$RAIZ/.venv" "$RAIZ/.pytest_cache"
if [ $SIMULAR -eq 1 ]; then echo "  [simular] borrar los __pycache__"; else
  find "$RAIZ" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null; echo "  __pycache__ borrados"; fi
hacer rm -rf /tmp/e2e_logs /tmp/e2e_cli_*.csv /tmp/e2e2*.txt
[ $CON_ENV -eq 1 ] && hacer rm -f "$RAIZ/.env"
[ $CON_RES -eq 1 ] && hacer rm -rf "$RAIZ/resultados" "$RAIZ/resultados_replica"

echo
[ $SIMULAR -eq 1 ] && echo "Simulacion terminada: no se borro nada." || echo "Listo. Para volver a empezar: ./scripts/deploy.sh $([ "$CAPA" = todo ] && echo '<capa>' || echo "$CAPA")"
