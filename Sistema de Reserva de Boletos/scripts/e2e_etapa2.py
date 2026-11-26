"""Prueba de punta a punta de la etapa 2 con PROCESOS REALES (una maquina).

    .venv/bin/python scripts/e2e_etapa2.py

Persistencia + supervisor (GT, 2 RMC, 2 cancelaciones) + clientes como procesos independientes.
Comprueba modificacion, cancelacion con notificacion, rechazos, caida de replicas de cancelacion y
varios clientes concurrentes, siempre contra el estado real de la BD. Sale con 0 si todo pasa.
"""
import os
import signal
import subprocess
import sys
import time

RAIZ = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
VENV = os.path.join(RAIZ, ".venv", "bin", "python")
if os.path.exists(VENV) and os.path.realpath(sys.prefix) != os.path.realpath(os.path.join(RAIZ, ".venv")):
    os.execv(VENV, [VENV] + sys.argv)

ENV = {**os.environ, "HOST_CAPA_CONSUMIDORES": "127.0.0.1", "HOST_CAPA_SERVICIOS": "127.0.0.1",
       "HOST_CAPA_PERSISTENCIA": "127.0.0.1", "TIMEOUT_PERSISTENCIA_MS": "800", "TIMEOUT_MS": "3000"}
os.environ.update(ENV)
sys.path.insert(0, RAIZ)

from sqlalchemy import create_engine, text  # noqa: E402

from comun.config import DB_URL  # noqa: E402

PY = sys.executable
resultados = []


def cli(*args, timeout=60):
    p = subprocess.run([PY, "-m", "capa_consumidores.consumers_main", *args], cwd=RAIZ, env=ENV,
                       capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout


def check(nombre, condicion, detalle=""):
    resultados.append(bool(condicion))
    print(f"  [{'OK ' if condicion else 'FALLA'}] {nombre}" + (f"  -> {detalle}" if not condicion else ""), flush=True)


def q(engine, sql, **p):
    with engine.connect() as c:
        return c.execute(text(sql), p).all()


def ocup(engine, ev, fecha):
    return q(engine, "select puestos_ocupados from ocurrencia where evento_id=:e and fecha=:f", e=ev, f=fecha)[0][0]


def matar(patron):
    r = subprocess.run(["pgrep", "-f", f"service_main {patron}"], capture_output=True, text=True)
    pids = [int(x) for x in r.stdout.split()]
    for pid in pids:
        os.kill(pid, signal.SIGKILL)
    return pids


def puerto_ocupado(puerto):
    import socket
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", int(puerto))) == 0


def main():
    ocupados = [p for p in (5555, 5556, 5557, 5558) if puerto_ocupado(p)]
    if ocupados:
        sys.exit(f"Los puertos {ocupados} ya estan en uso (quedo algo corriendo: pkill -f capa_). Abortando.")
    os.makedirs("/tmp/e2e_logs", exist_ok=True)
    subprocess.run([PY, "-m", "capa_persistencia.seed", "--reset"], cwd=RAIZ, env=ENV, check=True, stdout=subprocess.DEVNULL)
    def log(nombre):
        f = open(f"/tmp/e2e_logs/{nombre}.log", "w")
        return {"stdout": f, "stderr": f}
    pers = subprocess.Popen([PY, "-m", "capa_persistencia.persistence_main"], cwd=RAIZ, env=ENV, **log("persistencia"))
    sup = subprocess.Popen([PY, "-m", "capa_servicios.supervisor", "--rmc", "2", "--cancelacion", "2"], cwd=RAIZ, env=ENV, **log("supervisor"))
    time.sleep(4)
    engine = create_engine(DB_URL)
    try:
        print("1. Reservar y modificar")
        rc, out = cli("reservar", "--evento", "E25", "--fecha", "2026/11/12", "--asientos", "3")
        check("reservar E25 (cliente 1)", rc == 0 and "Reserva #1 " in out, out)
        rc, out = cli("modificar", "--reserva", "1", "--asientos", "6")
        check("subir la reserva a 6 puestos", rc == 0 and "modificada" in out and ocup(engine, "E25", "2026-11-12") == 6, out)
        rc, out = cli("reservar", "--evento", "E1", "--fecha", "2026/11/05", "--asientos", "4")
        check("reservar E1 el 05/11", rc == 0 and "Reserva #2 " in out, out)
        rc, out = cli("modificar", "--reserva", "2", "--fecha", "2026/11/19")
        check("mover la reserva a la otra fecha", rc == 0 and ocup(engine, "E1", "2026-11-05") == 0 and ocup(engine, "E1", "2026-11-19") == 4, out)
        rc, out = cli("modificar", "--reserva", "1", "--fecha", "2026/11/19")
        check("evento de una sola ocurrencia no cambia de fecha", rc == 1 and "Rechazada" in out and ocup(engine, "E25", "2026-11-12") == 6, out)
        rc, out = cli("modificar", "--reserva", "1", "--asientos", "7", "--cliente", "2")
        check("otro cliente no puede modificar (NO_AUTORIZADO)", rc == 1 and "NO_AUTORIZADO" in out and ocup(engine, "E25", "2026-11-12") == 6, out)

        print("2. Cancelacion asincrona con notificacion (procesos separados)")
        rc, out = cli("cancelar", "--reserva", "1", "--host-notif", "127.0.0.1", "--puerto-notif", "6201")
        check("ACK y luego notificacion de exito", rc == 0 and "recibida" in out and "fue cancelada" in out and out.index("recibida") < out.index("fue cancelada"), out)
        check("la BD libero los puestos y marco la reserva", ocup(engine, "E25", "2026-11-12") == 0
              and q(engine, "select estado from reserva where id=1")[0][0] is False)
        rc, out = cli("cancelar", "--reserva", "2", "--cliente", "2", "--host-notif", "127.0.0.1", "--puerto-notif", "6202")
        check("cancelar la reserva de otro: ACK y notificacion de fallo", rc == 1 and "recibida" in out and "NO_AUTORIZADO" in out
              and ocup(engine, "E1", "2026-11-19") == 4, out)
        rc, out = cli("cancelar", "--reserva", "1", "--host-notif", "127.0.0.1", "--puerto-notif", "6203")
        check("cancelar dos veces no libera dos veces", rc == 0 and ocup(engine, "E25", "2026-11-12") == 0, out)

        print("3. Casos predefinidos")
        rc, out = cli("caso", "modificar")
        check("caso modificar (el ultimo paso se rechaza a proposito)", rc == 1 and out.count("modificada") == 2 and "Rechazada" in out, out)
        rc, out = cli("caso", "cancelacion", "--host-notif", "127.0.0.1", "--puerto-notif", "6204")
        check("caso cancelacion", rc == 0 and "fue cancelada" in out, out)

        print("4. Cae una replica del servicio de cancelacion")
        r = subprocess.run(["pgrep", "-f", "service_main cancelacion"], capture_output=True, text=True).stdout.split()
        os.kill(int(r[0]), signal.SIGKILL)
        res = [cli("reservar", "--evento", "E30", "--fecha", "2026/11/12", "--asientos", "1")[1] for _ in range(1)]
        rc, out = cli("cancelar", "--reserva", "5", "--host-notif", "127.0.0.1", "--puerto-notif", "6205")
        check("con una replica caida la cancelacion se completa", rc == 0 and "fue cancelada" in out, out)
        print("5. Caen TODAS las replicas de cancelacion; el supervisor las relanza")
        matar("cancelacion")
        time.sleep(4)
        rc, out = cli("reservar", "--evento", "E31", "--fecha", "2026/11/12", "--asientos", "1")
        rid = out.split("#")[1].split()[0] if "#" in out else "0"
        rc, out = cli("cancelar", "--reserva", rid, "--host-notif", "127.0.0.1", "--puerto-notif", "6206")
        check("tras relanzarse, la cancelacion vuelve a funcionar", rc == 0 and "fue cancelada" in out, out)

        print("6. Cuatro clientes concurrentes (procesos) con reservas, modificaciones y cancelaciones")
        procs = []
        for i in range(1, 5):
            procs.append(subprocess.Popen([PY, "-m", "capa_consumidores.consumers_main", "--cliente", str(10 + i),
                                           "carga", "--por-min", "600", "--pct-consulta", "20", "--pct-modif", "30",
                                           "--pct-cancel", "15", "--segundos", "8", "--puerto-notif", str(6300 + i),
                                           "--host-notif", "127.0.0.1", "--csv", f"/tmp/e2e_cli_{i}.csv"],
                                          cwd=RAIZ, env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        codigos = [p.wait() for p in procs]
        check("los 4 clientes terminaron bien", codigos == [0, 0, 0, 0], codigos)
        time.sleep(1.5)
        filas = []
        for i in range(1, 5):
            filas += open(f"/tmp/e2e_cli_{i}.csv").read().strip().splitlines()[1:]
        ops = {}
        for f in filas:
            op, est = f.split(",")[1], f.split(",")[2]
            ops[(op, est)] = ops.get((op, est), 0) + 1
        print(f"     operaciones: {dict(sorted(ops.items()))}")
        check("hubo reservas, modificaciones y cancelaciones reales",
              ops.get(("RESERVA", "EXITO"), 0) > 20 and ops.get(("MODIFICACION", "EXITO"), 0) > 5 and ops.get(("CANCELACION", "ACK"), 0) > 3)
        check("ninguna operacion fallo por error tecnico", not any(k[1] == "ERROR" for k in ops), ops)
        desajustes = q(engine, """select o.id from ocurrencia o left join reserva r on r.ocurrencia_id = o.id
                      group by o.id, o.puestos_ocupados
                      having o.puestos_ocupados <> coalesce(sum(r.n_puestos) filter (where r.estado), 0)""")
        check("ocupacion de cada ocurrencia == suma de sus reservas activas", desajustes == [], desajustes)
        check("nunca hay sobreventa", q(engine, "select count(*) from ocurrencia where puestos_ocupados > capacidad")[0][0] == 0)
    finally:
        sup.send_signal(signal.SIGTERM)
        try:
            sup.wait(timeout=8)
        except subprocess.TimeoutExpired:
            sup.kill()
        pers.terminate()
        pers.wait(timeout=5)
    print(f"\n{sum(resultados)}/{len(resultados)} comprobaciones OK")
    return 0 if all(resultados) else 1


if __name__ == "__main__":
    sys.exit(main())
