import os
import threading
import time

import pytest
from sqlalchemy import create_engine

from capa_persistencia.modelos import metadata
from capa_persistencia.seed import sembrar

TEST_DB_URL = os.getenv("TEST_DB_URL", "postgresql+psycopg://srb:srb@localhost:5432/srb_test")


@pytest.fixture
def engine():
    eng = create_engine(TEST_DB_URL, pool_size=25, max_overflow=10)
    metadata.drop_all(eng)
    metadata.create_all(eng)
    sembrar(eng, eventos=50, dobles=20, capacidad=100, mes=11, anio=2026)
    yield eng
    eng.dispose()


# ---- cadena completa (persistencia + GT + RMC) en hilos, con puertos de prueba ----------------
P_CLI, P_RMC, P_CANC, P_PERS = "15555", "15556", "15557", "15558"


def _hilo(fn):
    t = threading.Thread(target=fn, daemon=True)
    t.start()
    return t


@pytest.fixture
def cadena(engine):
    from capa_persistencia.servicio_persistencia import Servicio_Persistencia
    from capa_servicios.gestor_transacciones.gestor_de_transacciones import Gestor_transacciones
    from capa_servicios.servicio_RMC.servicio_rmc import Cliente_persistencia, Servicio_RMC
    from capa_servicios.cancelacion_y_notificacion.cancelacion_y_notificacion import Servicio_cancelacion

    pers = Servicio_Persistencia(engine, puerto=P_PERS, hilos=4)
    gt = Gestor_transacciones(puerto_clientes=P_CLI, puerto_rmc=P_RMC, puerto_cancelacion=P_CANC)
    canc = Servicio_cancelacion(Cliente_persistencia("127.0.0.1", P_PERS, timeout_ms=800))
    rmc = Servicio_RMC(Cliente_persistencia("127.0.0.1", P_PERS, timeout_ms=800))
    hilos = [_hilo(pers.iniciar),
             _hilo(lambda: (gt.iniciar_sockets(), gt.iniciar_poller(), gt.iniciar_gestor())),
             _hilo(lambda: rmc.iniciar("127.0.0.1", P_RMC)),
             _hilo(lambda: canc.iniciar("127.0.0.1", P_CANC))]
    time.sleep(0.5)
    yield {"pers": pers, "gt": gt, "rmc": rmc, "canc": canc}
    canc.detener(); rmc.detener(); gt.detener(); pers.detener()
    for h in hilos:
        h.join(timeout=3)
