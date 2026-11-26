import os
import signal
import sys
import time

from capa_servicios.supervisor import Supervisor, comandos_capa_servicios

DORMIR = [sys.executable, "-c", "import time; time.sleep(30)"]
SALIR_PRONTO = [sys.executable, "-c", "import time; time.sleep(0.2)"]


def _esperar(cond, timeout=6.0):
    fin = time.time() + timeout
    while time.time() < fin:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_relanza_un_proceso_que_muere_con_kill_9():
    sup = Supervisor({"rmc-1": DORMIR}, intervalo=0.1, espera_reinicio=0.1)
    sup.iniciar()
    try:
        pid1 = sup.pid("rmc-1")
        os.kill(pid1, signal.SIGKILL)
        assert _esperar(lambda: sup.pid("rmc-1") not in (None, pid1))
        assert sup.reinicios["rmc-1"] == 1
        os.kill(sup.pid("rmc-1"), 0)          # el nuevo proceso esta vivo
    finally:
        sup.detener()


def test_relanza_varias_veces_si_sigue_cayendo():
    sup = Supervisor({"gt": SALIR_PRONTO}, intervalo=0.1, espera_reinicio=0.05)
    sup.iniciar()
    try:
        assert _esperar(lambda: sup.reinicios["gt"] >= 3)
    finally:
        sup.detener()


def test_detener_termina_todos_los_hijos():
    sup = Supervisor({"a": DORMIR, "b": DORMIR}, intervalo=0.1)
    sup.iniciar()
    pids = [sup.pid("a"), sup.pid("b")]
    sup.detener()
    for pid in pids:
        assert _esperar(lambda: not _vivo(pid), 3.0)


def _vivo(pid):
    try:
        os.kill(pid, 0)
        os.waitpid(pid, os.WNOHANG)   # por si es un hijo zombie
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, ChildProcessError):
        return False


def test_comandos_de_la_capa_de_servicios():
    c = comandos_capa_servicios(rmc=3, cancelacion=2)
    assert sorted(c) == ["cancelacion-1", "cancelacion-2", "gt", "rmc-1", "rmc-2", "rmc-3"]
    assert c["rmc-2"][-2:] == ["capa_servicios.service_main", "rmc"] or c["rmc-2"][-1] == "rmc"
    assert "gt" not in comandos_capa_servicios(rmc=1, cancelacion=1, con_gt=False)
