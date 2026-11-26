"""Demostracion de tolerancia a fallos (una sola maquina, procesos reales).

    .venv/bin/python scripts/demo_fallos.py

Arranca persistencia y el supervisor (GT + 2 RMC + 1 cancelacion), envia 24 reservas seguidas y, a mitad de
camino, mata con `kill -9` el GT (dos veces) y un RMC. Al final comprueba contra la BD que TODAS las
reservas quedaron guardadas exactamente una vez (no se pierde ni se duplica ninguna).
"""
import os
import signal
import subprocess
import sys
import time

RAIZ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
VENV = os.path.join(RAIZ, ".venv", "bin", "python")
if os.path.exists(VENV) and os.path.realpath(sys.prefix) != os.path.realpath(os.path.join(RAIZ, ".venv")):
    os.execv(VENV, [VENV] + sys.argv)

ENTORNO = {**os.environ, "HOST_CAPA_CONSUMIDORES": "127.0.0.1", "HOST_CAPA_SERVICIOS": "127.0.0.1",
           "HOST_CAPA_PERSISTENCIA": "127.0.0.1", "TIMEOUT_PERSISTENCIA_MS": "500", "TIMEOUT_MS": "2500"}
os.environ.update(ENTORNO)
sys.path.insert(0, RAIZ)

from datetime import date  # noqa: E402

from sqlalchemy import create_engine, text  # noqa: E402

from capa_consumidores.cliente import Cliente  # noqa: E402
from comun.config import DB_URL  # noqa: E402

N, MATAR = 24, {6: "gt", 14: "rmc", 18: "gt"}


def pids(patron):
    r = subprocess.run(["pgrep", "-f", f"capa_servicios.service_main {patron}"], capture_output=True, text=True)
    return [int(x) for x in r.stdout.split()]


def main():
    subprocess.run([sys.executable, "-m", "capa_persistencia.seed", "--reset"], cwd=RAIZ, env=ENTORNO, check=True,
                   stdout=subprocess.DEVNULL)
    pers = subprocess.Popen([sys.executable, "-m", "capa_persistencia.persistence_main"], cwd=RAIZ, env=ENTORNO)
    sup = subprocess.Popen([sys.executable, "-m", "capa_servicios.supervisor", "--rmc", "2", "--cancelacion", "1"],
                           cwd=RAIZ, env=ENTORNO)
    time.sleep(3)
    c = Cliente(1)
    c.conectar_con_gt()
    resultados = []
    try:
        for i in range(1, N + 1):
            if i in MATAR:
                victima = pids(MATAR[i])
                if victima:
                    print(f"\n>>> kill -9 {MATAR[i].upper()} (pid {victima[0]})\n", flush=True)
                    os.kill(victima[0], signal.SIGKILL)
            evento = f"E{20 + i}"                       # un evento distinto por operacion: se puede verificar
            t0 = time.time()
            r = c.reservar_evento(evento, date(2026, 11, 12), 1)
            ms = (time.time() - t0) * 1000
            resultados.append((evento, r))
            print(f"op {i:>2} {evento:<4} -> {r['estado']:<5} {c.formatear(r)}  ({ms:.0f} ms)", flush=True)
            time.sleep(0.4)
    finally:
        c.cerrar()
        sup.send_signal(signal.SIGTERM)
        try:
            sup.wait(timeout=8)
        except subprocess.TimeoutExpired:
            sup.kill()
        pers.terminate()
        pers.wait(timeout=5)

    engine = create_engine(DB_URL)
    with engine.connect() as conn:
        filas = conn.execute(text("select count(*), coalesce(sum(n_puestos),0) from reserva")).one()
        dup = conn.execute(text("select count(*) from (select ocurrencia_id from reserva group by ocurrencia_id "
                                "having count(*) > 1) t")).scalar_one()
    exitos = sum(1 for _, r in resultados if r["estado"] == "EXITO")
    print("\n=== RESUMEN ===")
    print(f"Operaciones: {N} | exitosas: {exitos} | reservas guardadas en la BD: {filas[0]} | duplicadas: {dup}")
    ok = exitos == N and filas[0] == N and dup == 0
    print("RESULTADO:", "OK, las fallas fueron transparentes" if ok else "FALLO")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
