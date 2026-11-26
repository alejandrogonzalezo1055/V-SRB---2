"""Mediciones de rendimiento: arquitectura base vs escalable con 3, 5 y 8 clientes simultaneos.

    .venv/bin/python scripts/benchmark.py --todo --segundos 120 --por-min 600
    .venv/bin/python scripts/benchmark.py --arq escalable --clientes 5 --segundos 120

Arquitectura base:      1 GT, 1 RMC, 1 cancelacion, persistencia con 1 hilo.
Arquitectura escalable: 1 GT, 4 RMC, 2 cancelaciones, persistencia con 8 hilos (supervisor las vigila).

Escenarios (enunciado): 3 clientes = 2 solo consultan + 1 consulta y reserva; 5 = 3 solo consultan + 2
reservan y modifican; 8 = 4 solo consultan + 4 reservan y modifican.
Cada cliente es un PROCESO (`consumers_main carga --segundos ...`) que escribe un CSV por operacion.
Salida: resultados/<arq>_<n>/ (CSV de cada cliente + resumen.json) y resultados/resumen.json.
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import time

RAIZ = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
VENV = os.path.join(RAIZ, ".venv", "bin", "python")
if os.path.exists(VENV) and os.path.realpath(sys.prefix) != os.path.realpath(os.path.join(RAIZ, ".venv")):
    os.execv(VENV, [VENV] + sys.argv)

ARQS = {"base": {"rmc": 1, "canc": 1, "hilos": 1}, "escalable": {"rmc": 4, "canc": 2, "hilos": 8}}
# n clientes -> (solo consultan, % consulta de los mixtos, % modificacion de los mixtos)
ESCENARIOS = {3: (2, 50, 0), 5: (3, 0, 50), 8: (4, 0, 50)}


def stats(ms):
    return {"n": len(ms), "media_ms": round(statistics.mean(ms), 2) if ms else None,
            "desv_ms": round(statistics.stdev(ms), 2) if len(ms) > 1 else 0.0}


def correr(arq, n, segundos, por_min, salida):
    cfg = ARQS[arq]
    env = {**os.environ, "HOST_CAPA_CONSUMIDORES": "127.0.0.1", "HOST_CAPA_SERVICIOS": "127.0.0.1",
           "HOST_CAPA_PERSISTENCIA": "127.0.0.1", "HILOS_PERSISTENCIA": str(cfg["hilos"])}
    carpeta = os.path.join(salida, f"{arq}_{n}")
    os.makedirs(carpeta, exist_ok=True)
    py = sys.executable
    subprocess.run([py, "-m", "capa_persistencia.seed", "--reset", "--capacidad", "100000"], cwd=RAIZ, env=env,
                   check=True, stdout=subprocess.DEVNULL)
    quieto = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    pers = subprocess.Popen([py, "-m", "capa_persistencia.persistence_main"], cwd=RAIZ, env=env, **quieto)
    sup = subprocess.Popen([py, "-m", "capa_servicios.supervisor", "--rmc", str(cfg["rmc"]),
                            "--cancelacion", str(cfg["canc"])], cwd=RAIZ, env=env, **quieto)
    time.sleep(4)
    solo, pct_c, pct_m = ESCENARIOS[n]
    clientes = []
    t0 = time.time()
    for i in range(1, n + 1):
        consulta, modif = (100, 0) if i <= solo else (pct_c, pct_m)
        csv = os.path.join(carpeta, f"cliente_{i}.csv")
        cmd = [py, "-m", "capa_consumidores.consumers_main", "--cliente", str(i), "carga", "--por-min", str(por_min),
               "--pct-consulta", str(consulta), "--pct-modif", str(modif), "--segundos", str(segundos), "--csv", csv]
        clientes.append(subprocess.Popen(cmd, cwd=RAIZ, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    for p in clientes:
        p.wait()
    real = time.time() - t0
    from sqlalchemy import create_engine, text
    sys.path.insert(0, RAIZ)
    os.environ.update(env)
    from comun.config import DB_URL
    with create_engine(DB_URL).connect() as conn:
        db_reservas = conn.execute(text("select count(*) from reserva")).scalar_one()
    sup.terminate(); pers.terminate()
    for p in (sup, pers):
        try:
            p.wait(timeout=8)
        except subprocess.TimeoutExpired:
            p.kill()

    lat = {"CONSULTA": [], "RESERVA": [], "MODIFICACION": []}
    estados = {"CONSULTA": {}, "RESERVA": {}, "MODIFICACION": {}}
    errores = 0
    import csv as csvmod
    for i in range(1, n + 1):
        with open(os.path.join(carpeta, f"cliente_{i}.csv"), encoding="utf-8") as f:
            for fila in csvmod.DictReader(f):
                op, est = fila["operacion"], fila["estado"]
                estados[op][est] = estados[op].get(est, 0) + 1
                if est == "ERROR":
                    errores += 1
                else:
                    lat[op].append(float(fila["ms"]))
    minutos = real / 60
    res = {"arquitectura": arq, "clientes": n, "segundos_medidos": round(real, 1), "por_min_pedido": por_min,
           "errores_tecnicos": errores, "reservas_en_bd": db_reservas, "estados": estados}
    for op, k in (("CONSULTA", "consulta"), ("RESERVA", "reserva"), ("MODIFICACION", "modificacion")):
        res[k] = {**stats(lat[op]), "tpm": round(len(lat[op]) / minutos, 1)}
    res["tpm_reserva_bd"] = round(db_reservas / minutos, 1)
    json.dump(res, open(os.path.join(carpeta, "resumen.json"), "w"), indent=2)
    print(f"[{arq:<9} {n} clientes] consulta {res['consulta']['media_ms']}±{res['consulta']['desv_ms']} ms "
          f"({res['consulta']['tpm']} TPM) | reserva {res['reserva']['media_ms']}±{res['reserva']['desv_ms']} ms "
          f"({res['reserva']['tpm']} TPM) | errores {errores}", flush=True)
    return res


def entorno(salida):
    def sh(c):
        return subprocess.run(c, shell=True, capture_output=True, text=True).stdout.strip()
    import zmq
    cpu = sh("lscpu | grep 'Model name' | cut -d: -f2")
    ram = sh("free -h | awk '/Mem/ {print $2}'")
    texto = (f"CPU: {cpu}\nNucleos: {sh('nproc')}\n"
             f"RAM: {ram}\nSO: {sh('uname -sr')}\n"
             f"Python: {sys.version.split()[0]}\nPostgreSQL: {sh('docker exec srb-postgres postgres --version')}\n"
             f"libzmq: {zmq.zmq_version()}, pyzmq: {zmq.__version__}\n")
    open(os.path.join(salida, "entorno.txt"), "w", encoding="utf-8").write(texto)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arq", choices=sorted(ARQS))
    ap.add_argument("--clientes", type=int, choices=sorted(ESCENARIOS))
    ap.add_argument("--todo", action="store_true", help="las 6 combinaciones (base y escalable x 3, 5, 8)")
    ap.add_argument("--segundos", type=int, default=120)
    ap.add_argument("--por-min", type=int, default=600, help="solicitudes por minuto por cliente")
    ap.add_argument("--salida", default=os.path.join(RAIZ, "resultados"))
    a = ap.parse_args()
    os.makedirs(a.salida, exist_ok=True)
    entorno(a.salida)
    combos = [(arq, n) for n in sorted(ESCENARIOS) for arq in ("base", "escalable")] if a.todo else [(a.arq, a.clientes)]
    if not a.todo and (a.arq is None or a.clientes is None):
        ap.error("indique --arq y --clientes, o --todo")
    todos = [correr(arq, n, a.segundos, a.por_min, a.salida) for arq, n in combos]
    json.dump(todos, open(os.path.join(a.salida, "resumen.json"), "w"), indent=2)


if __name__ == "__main__":
    main()
