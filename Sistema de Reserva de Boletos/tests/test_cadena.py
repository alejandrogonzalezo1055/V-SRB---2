import time
from concurrent.futures import ThreadPoolExecutor

import zmq

from comun.mensajes import codificar, crear_req, decodificar

from tests.conftest import P_CLI


def _pedir(peticion_o_texto, timeout=4000):
    texto = peticion_o_texto if isinstance(peticion_o_texto, str) else codificar(peticion_o_texto)
    sock = zmq.Context.instance().socket(zmq.REQ)
    sock.setsockopt(zmq.RCVTIMEO, timeout)
    sock.setsockopt(zmq.LINGER, 0)
    sock.connect(f"tcp://127.0.0.1:{P_CLI}")
    try:
        sock.send_string(texto)
        return decodificar(sock.recv_string())
    finally:
        sock.close()


RES = {"id_evento": "E1", "fecha": "2026/11/05", "n_asientos": 3}


def test_consulta_noviembre_de_punta_a_punta(cadena):
    req = crear_req('CO', 1, {"mes": "noviembre"})
    r = _pedir(req)
    assert r["estado"] == "EXITO" and r["peticion_realizada"] == "CONSULTA" and r["uuid"] == req["uuid"]
    assert len(r["datos"]["eventos"]) == 70


def test_reserva_exitosa_y_la_consulta_la_refleja(cadena):
    req = crear_req('R', 1, RES)
    r = _pedir(req)
    assert r["estado"] == "EXITO" and r["peticion_realizada"] == "RESERVA" and r["uuid"] == req["uuid"]
    assert isinstance(r["datos"]["id_reserva"], int)
    eventos = _pedir(crear_req('CO', 1, {"mes": "noviembre"}))["datos"]["eventos"]
    e1 = [e for e in eventos if e["id_evento"] == "E1" and e["fecha"] == "2026/11/05"][0]
    assert e1["puestos_ocupados"] == 3


def test_reserva_mas_de_100_es_dato_invalido(cadena):
    r = _pedir(crear_req('R', 1, {**RES, "n_asientos": 101}))
    assert (r["estado"], r["motivo"]) == ("FALLO", "DATO_INVALIDO")


def test_reserva_evento_inexistente_es_no_existe(cadena):
    r = _pedir(crear_req('R', 1, {**RES, "id_evento": "E99"}))
    assert (r["estado"], r["motivo"]) == ("FALLO", "NO_EXISTE")


def test_reserva_sin_cupo_es_fallo_no_error(cadena):
    assert _pedir(crear_req('R', 1, {**RES, "n_asientos": 98}))["estado"] == "EXITO"
    r = _pedir(crear_req('R', 2, {**RES, "n_asientos": 5}))
    assert (r["estado"], r["motivo"]) == ("FALLO", "SIN_CUPO")


def test_modificacion_de_reserva_inexistente_es_no_existe(cadena):
    r = _pedir(crear_req('M', 1, {"id_reserva": 12345, "n_asientos": 2}))
    assert (r["estado"], r["motivo"]) == ("FALLO", "NO_EXISTE")


def test_modificar_cantidad_y_fecha_de_punta_a_punta(cadena):
    rid = _pedir(crear_req('R', 1, {"id_evento": "E1", "fecha": "2026/11/05", "n_asientos": 3}))["datos"]["id_reserva"]
    r = _pedir(crear_req('M', 1, {"id_reserva": rid, "n_asientos": 6}))
    assert r["estado"] == "EXITO" and r["peticion_realizada"] == "MODIFICACION" and r["datos"]["n_asientos"] == 6
    r = _pedir(crear_req('M', 1, {"id_reserva": rid, "fecha": "2026/11/19"}))
    assert r["estado"] == "EXITO" and r["datos"]["fecha"] == "2026/11/19"
    ev = {(e["id_evento"], e["fecha"]): e["puestos_ocupados"]
          for e in _pedir(crear_req('CO', 1, {"mes": "noviembre"}))["datos"]["eventos"]}
    assert ev[("E1", "2026/11/05")] == 0 and ev[("E1", "2026/11/19")] == 6
    ajeno = _pedir(crear_req('M', 2, {"id_reserva": rid, "n_asientos": 1}))
    assert (ajeno["estado"], ajeno["motivo"]) == ("FALLO", "NO_AUTORIZADO")


def test_json_malformado_en_el_gt_responde_error_y_el_gt_sigue_vivo(cadena):
    assert _pedir("{no es json")["estado"] == "ERROR"
    assert _pedir(codificar({"uuid": "x", "tipo": "VOLAR", "id_cliente": 1, "datos": {}}))["estado"] == "ERROR"
    assert _pedir(crear_req('CO', 1, {"mes": "noviembre"}))["estado"] == "EXITO"


def test_persistencia_caida_el_cliente_recibe_error_sin_colgarse(cadena):
    cadena["pers"].detener()
    time.sleep(0.5)
    t0 = time.time()
    r = _pedir(crear_req('CO', 1, {"mes": "noviembre"}), timeout=6000)
    assert r["estado"] == "ERROR"
    assert time.time() - t0 < 3


def test_clientes_simultaneos_reciben_su_propio_uuid(cadena):
    reqs = [crear_req('CO', i + 1, {"mes": "noviembre"}) for i in range(8)]
    with ThreadPoolExecutor(8) as ex:
        rs = list(ex.map(_pedir, reqs))
    assert [r["uuid"] for r in rs] == [q["uuid"] for q in reqs]
    assert all(r["estado"] == "EXITO" for r in rs)


def test_mensaje_multipart_en_el_gt_responde_error_y_el_rmc_sigue_vivo(cadena):
    sock = zmq.Context.instance().socket(zmq.REQ)
    sock.setsockopt(zmq.RCVTIMEO, 4000)
    sock.setsockopt(zmq.LINGER, 0)
    sock.connect(f"tcp://127.0.0.1:{P_CLI}")
    try:
        for _ in range(3):
            sock.send_multipart([b"x", codificar(crear_req('CO', 1, {"mes": "noviembre"})).encode()])
            assert decodificar(sock.recv_string())["estado"] == "ERROR"
    finally:
        sock.close()
    assert _pedir(crear_req('CO', 1, {"mes": "noviembre"}))["estado"] == "EXITO"


def test_consulta_con_id_cliente_invalido_es_dato_invalido(cadena):
    for id_cliente in (0, -3, "abc"):
        r = _pedir({"uuid": "x", "tipo": "CONSULTA", "id_cliente": id_cliente, "datos": {"mes": "noviembre"}})
        assert (r["estado"], r["motivo"]) == ("FALLO", "DATO_INVALIDO"), id_cliente


# ---- cancelacion asincrona + notificacion --------------------------------------------------
def _receptor(puerto):
    s = zmq.Context.instance().socket(zmq.PULL)
    s.setsockopt(zmq.RCVTIMEO, 4000)
    s.setsockopt(zmq.LINGER, 0)
    s.bind(f"tcp://127.0.0.1:{puerto}")
    return s


def _reservar(cliente=1, n=3):
    return _pedir(crear_req('R', cliente, {"id_evento": "E1", "fecha": "2026/11/05", "n_asientos": n}))["datos"]["id_reserva"]


def _ocup():
    ev = _pedir(crear_req('CO', 1, {"mes": "noviembre"}))["datos"]["eventos"]
    return [e["puestos_ocupados"] for e in ev if e["id_evento"] == "E1" and e["fecha"] == "2026/11/05"][0]


def test_cancelacion_responde_ack_y_luego_notifica_al_cliente(cadena):
    rec = _receptor(16001)
    try:
        rid = _reservar()
        req = crear_req('CA', 1, {"id_reserva": rid, "direccion_notificacion": "127.0.0.1:16001"})
        ack = _pedir(req)
        assert ack["estado"] == "ACK" and ack["uuid"] == req["uuid"]
        notif = decodificar(rec.recv_string())
        assert notif["tipo"] == "NOTIFICACION" and notif["id_cliente"] == 1 and notif["uuid"] == req["uuid"]
        assert notif["datos"]["resultado"] == "EXITO" and notif["datos"]["id_reserva"] == rid
        assert _ocup() == 0
    finally:
        rec.close()


def test_cada_cliente_recibe_solo_su_notificacion(cadena):
    r1, r2 = _receptor(16001), _receptor(16002)
    try:
        a, b = _reservar(1, 2), _reservar(2, 4)
        _pedir(crear_req('CA', 1, {"id_reserva": a, "direccion_notificacion": "127.0.0.1:16001"}))
        _pedir(crear_req('CA', 2, {"id_reserva": b, "direccion_notificacion": "127.0.0.1:16002"}))
        n1, n2 = decodificar(r1.recv_string()), decodificar(r2.recv_string())
        assert (n1["id_cliente"], n1["datos"]["id_reserva"]) == (1, a)
        assert (n2["id_cliente"], n2["datos"]["id_reserva"]) == (2, b)
        assert r1.poll(300) == 0 and r2.poll(300) == 0   # nadie recibe la ajena
    finally:
        r1.close(); r2.close()


def test_cancelar_reserva_ajena_notifica_fallo_y_no_libera(cadena):
    rec = _receptor(16001)
    try:
        rid = _reservar(1, 3)
        _pedir(crear_req('CA', 2, {"id_reserva": rid, "direccion_notificacion": "127.0.0.1:16001"}))
        notif = decodificar(rec.recv_string())
        assert (notif["datos"]["resultado"], notif["datos"]["motivo"]) == ("FALLO", "NO_AUTORIZADO")
        assert _ocup() == 3
    finally:
        rec.close()


def test_cancelacion_con_datos_invalidos_responde_fallo_sin_ack(cadena):
    for datos in ({"id_reserva": "x", "direccion_notificacion": "127.0.0.1:16001"},
                  {"id_reserva": 1}, {"id_reserva": 1, "direccion_notificacion": "sin-puerto"}):
        r = _pedir(crear_req('CA', 1, datos))
        assert (r["estado"], r["motivo"]) == ("FALLO", "DATO_INVALIDO"), datos


def test_cancelacion_sin_servicio_de_cancelacion_responde_error(cadena):
    cadena["canc"].detener()
    time.sleep(0.6)
    r = _pedir(crear_req('CA', 1, {"id_reserva": 1, "direccion_notificacion": "127.0.0.1:16001"}))
    assert r["estado"] == "ERROR"
