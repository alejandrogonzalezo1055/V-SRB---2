import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import zmq

from comun.mensajes import codificar, crear_req, decodificar
from capa_persistencia.servicio_persistencia import Servicio_Persistencia

PUERTO = "15558"


@pytest.fixture
def servicio(engine):
    s = Servicio_Persistencia(engine, puerto=PUERTO, hilos=4)
    t = threading.Thread(target=s.iniciar, daemon=True)
    t.start()
    time.sleep(0.3)
    yield s
    s.detener()
    t.join(timeout=3)


def _enviar_texto(texto, ctx=None):
    ctx = ctx or zmq.Context.instance()
    sock = ctx.socket(zmq.REQ)
    sock.setsockopt(zmq.RCVTIMEO, 3000)
    sock.setsockopt(zmq.LINGER, 0)
    sock.connect(f"tcp://127.0.0.1:{PUERTO}")
    try:
        sock.send_string(texto)
        return decodificar(sock.recv_string())
    finally:
        sock.close()


def test_consulta_por_zeromq_devuelve_70_eventos(servicio):
    req = crear_req('CO', 1, {"mes": "noviembre"})
    r = _enviar_texto(codificar(req))
    assert r["estado"] == "EXITO" and r["uuid"] == req["uuid"]
    assert len(r["datos"]["eventos"]) == 70


def test_json_invalido_devuelve_error_y_el_servicio_sigue_vivo(servicio):
    r = _enviar_texto("{no es json")
    assert (r["estado"], r["motivo"]) == ("ERROR", "PETICION_INVALIDA")
    r2 = _enviar_texto(codificar(crear_req('CO', 1, {"mes": "noviembre"})))
    assert r2["estado"] == "EXITO"


def test_peticion_sin_campos_o_tipo_desconocido_devuelve_error(servicio):
    assert _enviar_texto("[]")["estado"] == "ERROR"
    assert _enviar_texto(codificar({"uuid": "x", "tipo": "VOLAR"}))["estado"] == "ERROR"
    assert _enviar_texto(codificar({"uuid": "x", "tipo": "AGREGAR_RESERVA"}))["estado"] == "ERROR"


def test_20_reservas_concurrentes_sobre_10_libres(servicio):
    datos = {"id_evento": "E1", "fecha": "2026/11/05", "n_asientos": 1}
    pre = crear_req('A_R', 99, {**datos, "n_asientos": 90})
    assert _enviar_texto(codificar(pre))["estado"] == "EXITO"

    def reservar(i):
        return _enviar_texto(codificar(crear_req('A_R', i + 1, datos)))

    with ThreadPoolExecutor(20) as ex:
        rs = list(ex.map(reservar, range(20)))
    assert sum(r["estado"] == "EXITO" for r in rs) == 10
    assert sum(r["motivo"] == "SIN_CUPO" for r in rs) == 10


def test_mensajes_no_utf8_o_multipart_no_matan_a_los_trabajadores(servicio):
    ctx = zmq.Context.instance()
    for malo in ([b"\xff\xfe"], [b"a", b"b"]):
        for _ in range(6):   # mas mensajes malos que hilos (4)
            sock = ctx.socket(zmq.REQ)
            sock.setsockopt(zmq.RCVTIMEO, 3000)
            sock.setsockopt(zmq.LINGER, 0)
            sock.connect(f"tcp://127.0.0.1:{PUERTO}")
            sock.send_multipart(malo)
            assert decodificar(sock.recv_string())["estado"] == "ERROR"
            sock.close()
    r = _enviar_texto(codificar(crear_req('CO', 1, {"mes": "noviembre"})))
    assert r["estado"] == "EXITO"
