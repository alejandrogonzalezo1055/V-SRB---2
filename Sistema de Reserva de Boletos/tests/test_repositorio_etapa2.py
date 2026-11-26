from sqlalchemy import text

from capa_persistencia import repositorio as repo

E1_5 = {"id_evento": "E1", "fecha": "2026/11/05", "n_asientos": 3}


def _ocup(engine, evento, fecha):
    with engine.connect() as c:
        return c.execute(text("select puestos_ocupados from ocurrencia where evento_id=:e and fecha=:f"),
                         {"e": evento, "f": fecha}).scalar_one()


def _reservar(engine, uuid="r1", cliente=7, datos=E1_5):
    r = repo.agregar_reserva(engine, uuid, cliente, datos)
    assert r["estado"] == "EXITO"
    return r["datos"]["id_reserva"]


def _estado(r):
    return (r["estado"], r["motivo"])


# ---- idempotencia por uuid ----------------------------------------------------------------
def test_reserva_con_el_mismo_uuid_no_se_duplica(engine):
    a = repo.agregar_reserva(engine, "mismo", 7, E1_5)
    b = repo.agregar_reserva(engine, "mismo", 7, E1_5)
    assert a["datos"]["id_reserva"] == b["datos"]["id_reserva"]
    assert _ocup(engine, "E1", "2026-11-05") == 3


# ---- modificacion de cantidad -------------------------------------------------------------
def test_modificar_cantidad_sube_y_baja_la_ocupacion(engine):
    rid = _reservar(engine)
    r = repo.modificar_reserva(engine, "m1", 7, {"id_reserva": rid, "n_asientos": 10})
    assert r["estado"] == "EXITO" and r["peticion_realizada"] == "MODIFICACION"
    assert _ocup(engine, "E1", "2026-11-05") == 10
    assert repo.modificar_reserva(engine, "m2", 7, {"id_reserva": rid, "n_asientos": 2})["estado"] == "EXITO"
    assert _ocup(engine, "E1", "2026-11-05") == 2


def test_modificar_cantidad_sin_cupo_deja_todo_como_estaba(engine):
    rid = _reservar(engine)
    _reservar(engine, "otro", 8, {**E1_5, "n_asientos": 95})   # 98 ocupados
    r = repo.modificar_reserva(engine, "m1", 7, {"id_reserva": rid, "n_asientos": 10})
    assert _estado(r) == ("FALLO", "SIN_CUPO")
    assert _ocup(engine, "E1", "2026-11-05") == 98
    with engine.connect() as c:
        assert c.execute(text("select n_puestos from reserva where id=:i"), {"i": rid}).scalar_one() == 3


def test_modificar_es_idempotente_si_la_cantidad_ya_es_la_pedida(engine):
    rid = _reservar(engine)
    repo.modificar_reserva(engine, "m1", 7, {"id_reserva": rid, "n_asientos": 5})
    assert repo.modificar_reserva(engine, "m1", 7, {"id_reserva": rid, "n_asientos": 5})["estado"] == "EXITO"
    assert _ocup(engine, "E1", "2026-11-05") == 5


# ---- modificacion de fecha ----------------------------------------------------------------
def test_modificar_fecha_mueve_los_puestos_a_la_otra_ocurrencia(engine):
    rid = _reservar(engine)
    r = repo.modificar_reserva(engine, "m1", 7, {"id_reserva": rid, "fecha": "2026/11/19"})
    assert r["estado"] == "EXITO"
    assert _ocup(engine, "E1", "2026-11-05") == 0
    assert _ocup(engine, "E1", "2026-11-19") == 3


def test_modificar_fecha_sin_cupo_en_destino_deja_la_reserva_en_el_origen(engine):
    rid = _reservar(engine)
    _reservar(engine, "lleno", 8, {"id_evento": "E1", "fecha": "2026/11/19", "n_asientos": 99})
    r = repo.modificar_reserva(engine, "m1", 7, {"id_reserva": rid, "fecha": "2026/11/19"})
    assert _estado(r) == ("FALLO", "SIN_CUPO")
    assert _ocup(engine, "E1", "2026-11-05") == 3 and _ocup(engine, "E1", "2026-11-19") == 99


def test_evento_de_una_sola_ocurrencia_no_cambia_de_fecha(engine):
    rid = _reservar(engine, "u", 7, {"id_evento": "E25", "fecha": "2026/11/12", "n_asientos": 2})
    for fecha in ("2026/11/19", "2026/12/12"):
        r = repo.modificar_reserva(engine, "m1", 7, {"id_reserva": rid, "fecha": fecha})
        assert r["estado"] == "FALLO", fecha
    assert _ocup(engine, "E25", "2026-11-12") == 2


def test_modificar_a_la_misma_fecha_es_exito_sin_cambios(engine):
    rid = _reservar(engine)
    assert repo.modificar_reserva(engine, "m1", 7, {"id_reserva": rid, "fecha": "2026/11/05"})["estado"] == "EXITO"
    assert _ocup(engine, "E1", "2026-11-05") == 3


# ---- validaciones de modificacion ---------------------------------------------------------
def test_modificar_valida_datos_duenio_y_existencia(engine):
    rid = _reservar(engine)
    for datos in ({"id_reserva": rid}, {"id_reserva": rid, "n_asientos": 2, "fecha": "2026/11/19"},
                  {"id_reserva": rid, "n_asientos": 0}, {"id_reserva": rid, "n_asientos": 101},
                  {"id_reserva": "x", "n_asientos": 2}, {"id_reserva": rid, "fecha": "19-11-2026"}):
        assert _estado(repo.modificar_reserva(engine, "m", 7, datos)) == ("FALLO", "DATO_INVALIDO"), datos
    assert _estado(repo.modificar_reserva(engine, "m", 99, {"id_reserva": rid, "n_asientos": 2})) == ("FALLO", "NO_AUTORIZADO")
    assert _estado(repo.modificar_reserva(engine, "m", 7, {"id_reserva": 9999, "n_asientos": 2})) == ("FALLO", "NO_EXISTE")
    assert _ocup(engine, "E1", "2026-11-05") == 3


# ---- cancelacion --------------------------------------------------------------------------
def test_cancelar_libera_puestos_y_marca_la_reserva(engine):
    rid = _reservar(engine)
    r = repo.cancelar_reserva(engine, "c1", 7, {"id_reserva": rid})
    assert r["estado"] == "EXITO" and r["peticion_realizada"] == "CANCELACION"
    assert r["datos"]["puestos_liberados"] == 3
    assert _ocup(engine, "E1", "2026-11-05") == 0
    with engine.connect() as c:
        assert c.execute(text("select estado from reserva where id=:i"), {"i": rid}).scalar_one() is False


def test_cancelar_dos_veces_libera_una_sola_vez(engine):
    rid = _reservar(engine)
    _reservar(engine, "otra", 8, E1_5)
    repo.cancelar_reserva(engine, "c1", 7, {"id_reserva": rid})
    again = repo.cancelar_reserva(engine, "c1", 7, {"id_reserva": rid})
    assert again["estado"] == "EXITO"
    assert _ocup(engine, "E1", "2026-11-05") == 3


def test_cancelar_valida_duenio_y_existencia_y_no_se_modifica_lo_cancelado(engine):
    rid = _reservar(engine)
    assert _estado(repo.cancelar_reserva(engine, "c", 99, {"id_reserva": rid})) == ("FALLO", "NO_AUTORIZADO")
    assert _estado(repo.cancelar_reserva(engine, "c", 7, {"id_reserva": 9999})) == ("FALLO", "NO_EXISTE")
    assert _estado(repo.cancelar_reserva(engine, "c", 7, {"id_reserva": "x"})) == ("FALLO", "DATO_INVALIDO")
    repo.cancelar_reserva(engine, "c1", 7, {"id_reserva": rid})
    assert _estado(repo.modificar_reserva(engine, "m", 7, {"id_reserva": rid, "n_asientos": 2})) == ("FALLO", "NO_EXISTE")
    assert _ocup(engine, "E1", "2026-11-05") == 0
