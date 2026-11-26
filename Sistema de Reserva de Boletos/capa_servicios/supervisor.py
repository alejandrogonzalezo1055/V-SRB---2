"""Supervisor de la capa de servicios: tolerancia a fallos y escalabilidad.

Lanza el GT y N replicas del RMC y del servicio de cancelacion, vigila que sigan vivos y, si uno
muere (modelo fail-stop: el proceso se detiene), lanza una copia que lo sustituye.

    python -m capa_servicios.supervisor --rmc 3 --cancelacion 2

Por que la falla es transparente para el cliente:
  * Los RMC y los servicios de cancelacion se CONECTAN al GT (que hace bind): al volver, se reconectan
    solos y el DEALER/PUSH del GT les reparte trabajo otra vez. Mientras haya una replica viva, el
    servicio no se interrumpe.
  * Si cae el GT, el supervisor lo relanza en el mismo puerto y los clientes (que reintentan con el
    MISMO uuid tras un timeout) continuan; persistencia es idempotente por uuid, asi que una reserva ya
    confirmada no se duplica.
"""
import argparse
import os
import signal
import subprocess
import sys
import threading
import time

from comun.log import log

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def comandos_capa_servicios(rmc=2, cancelacion=1, con_gt=True):
    base = [sys.executable, "-m", "capa_servicios.service_main"]
    cmds = {}
    if con_gt:
        cmds["gt"] = base + ["gt"]
    for i in range(1, rmc + 1):
        cmds[f"rmc-{i}"] = base + ["rmc"]
    for i in range(1, cancelacion + 1):
        cmds[f"cancelacion-{i}"] = base + ["cancelacion"]
    return cmds


class Supervisor:
    def __init__(self, comandos, intervalo=0.5, espera_reinicio=0.3):
        self.comandos = comandos
        self.intervalo = intervalo
        self.espera_reinicio = espera_reinicio
        self.procesos = {}
        self.reinicios = {nombre: 0 for nombre in comandos}
        self._parar = threading.Event()
        self._hilo = None

    def _lanzar(self, nombre):
        self.procesos[nombre] = subprocess.Popen(self.comandos[nombre], cwd=RAIZ)
        log("SUP", f"{nombre} lanzado (pid {self.procesos[nombre].pid})")

    def pid(self, nombre):
        p = self.procesos.get(nombre)
        return p.pid if p else None

    def iniciar(self):
        """Lanza todos los procesos y empieza a vigilarlos en un hilo."""
        for nombre in self.comandos:
            self._lanzar(nombre)
        self._hilo = threading.Thread(target=self._vigilar, daemon=True)
        self._hilo.start()

    def _vigilar(self):
        while not self._parar.is_set():
            for nombre, p in list(self.procesos.items()):
                codigo = p.poll()
                if codigo is not None and not self._parar.is_set():
                    log("SUP", f"{nombre} murio (pid {p.pid}, codigo {codigo}): lanzando una copia")
                    time.sleep(self.espera_reinicio)
                    self._lanzar(nombre)
                    self.reinicios[nombre] += 1
            self._parar.wait(self.intervalo)

    def detener(self):
        self._parar.set()
        if self._hilo:
            self._hilo.join(timeout=2)
        for p in self.procesos.values():
            if p.poll() is None:
                p.terminate()
        for p in self.procesos.values():
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Supervisor de la capa de servicios del SRB")
    ap.add_argument("--rmc", type=int, default=2, help="replicas del RMC")
    ap.add_argument("--cancelacion", type=int, default=1, help="replicas del servicio de cancelacion")
    ap.add_argument("--sin-gt", action="store_true", help="no lanzar el GT (otro proceso lo gestiona)")
    a = ap.parse_args(argv)
    sup = Supervisor(comandos_capa_servicios(a.rmc, a.cancelacion, not a.sin_gt))
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    sup.iniciar()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("SUP", "deteniendo todos los procesos")
    finally:
        sup.detener()
    return 0


if __name__ == "__main__":
    sys.exit(main())
