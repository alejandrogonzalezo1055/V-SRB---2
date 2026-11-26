from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import text

from capa_persistencia import repositorio as repo


def _ocupados(engine, evento, fecha):
    with engine.connect() as c:
        return c.execute(text(
            "select puestos_ocupados from ocurrencia where evento_id=:e and fecha=:f"),
            {"e": evento, "f": fecha}).scalar_one()


def test_seed_crea_50_eventos_y_70_ocurrencias(engine):
    with engine.connect() as c:
        assert c.execute(text("select count(*) from evento")).scalar_one() == 50
        assert c.execute(text("select count(*) from ocurrencia")).scalar_one() == 70


def test_seed_sin_reset_no_duplica(engine):
    import pytest
    from capa_persistencia.seed import sembrar_si_vacio
    with pytest.raises(RuntimeError):
        sembrar_si_vacio(engine, 50, 20, 100, 11, 2026)


def test_consulta_noviembre_devuelve_70_ocurrencias(engine):
    r = repo.consultar_eventos(engine, "u1", {"mes": "noviembre"})
    assert r["estado"] == "EXITO" and r["uuid"] == "u1"
    assert len(r["datos"]["eventos"]) == 70
    e = r["datos"]["eventos"][0]
    assert set(e) == {"id_evento", "nombre", "tipo_evento", "fecha", "capacidad", "puestos_ocupados"}
    assert e["fecha"] == "2026/11/05"


def test_consulta_mes_sin_eventos_es_exito_con_lista_vacia(engine):
    r = repo.consultar_eventos(engine, "u1", {"mes": "octubre"})
    assert r["estado"] == "EXITO" and r["datos"]["eventos"] == []


def test_consulta_mes_invalido_es_dato_invalido(engine):
    for mes in ("foo", "Noviembre", "", None, 5):
        r = repo.consultar_eventos(engine, "u1", {"mes": mes})
        assert (r["estado"], r["motivo"]) == ("FALLO", "DATO_INVALIDO"), mes


def test_disponibilidad_inicial(engine):
    r = repo.consultar_disponibilidad(engine, "u1", {"id_evento": "E1", "fecha": "2026/11/05"})
    assert r["estado"] == "EXITO"
    assert r["datos"] == {"capacidad_total": 100, "ocupacion": 0}


def test_disponibilidad_evento_o_fecha_inexistente(engine):
    for datos in ({"id_evento": "E99", "fecha": "2026/11/05"},
                  {"id_evento": "E30", "fecha": "2026/11/05"},   # E30 solo tiene dia 12
                  {"id_evento": "E1", "fecha": "2026/12/05"}):
        r = repo.consultar_disponibilidad(engine, "u1", datos)
        assert (r["estado"], r["motivo"]) == ("FALLO", "NO_EXISTE"), datos


def test_reserva_exitosa_suma_ocupacion(engine):
    r = repo.agregar_reserva(engine, "u1", 7, {"id_evento": "E1", "fecha": "2026/11/05", "n_asientos": 3})
    assert r["estado"] == "EXITO" and isinstance(r["datos"]["id_reserva"], int)
    assert _ocupados(engine, "E1", "2026-11-05") == 3


def test_mismo_cliente_puede_reservar_varias_veces(engine):
    d = {"id_evento": "E1", "fecha": "2026/11/05", "n_asientos": 2}
    assert repo.agregar_reserva(engine, "a", 7, d)["estado"] == "EXITO"
    assert repo.agregar_reserva(engine, "b", 7, d)["estado"] == "EXITO"
    assert _ocupados(engine, "E1", "2026-11-05") == 4


def test_reserva_datos_invalidos(engine):
    base = {"id_evento": "E1", "fecha": "2026/11/05"}
    for n in (0, -1, 101, "3", None):
        r = repo.agregar_reserva(engine, "u", 7, {**base, "n_asientos": n})
        assert (r["estado"], r["motivo"]) == ("FALLO", "DATO_INVALIDO"), n
    r = repo.agregar_reserva(engine, "u", 7, {"id_evento": "E1", "fecha": "05-11-2026", "n_asientos": 1})
    assert (r["estado"], r["motivo"]) == ("FALLO", "DATO_INVALIDO")
    assert _ocupados(engine, "E1", "2026-11-05") == 0


def test_reserva_sin_cupo_no_cambia_ocupacion(engine):
    d = {"id_evento": "E1", "fecha": "2026/11/05"}
    assert repo.agregar_reserva(engine, "a", 7, {**d, "n_asientos": 98})["estado"] == "EXITO"
    r = repo.agregar_reserva(engine, "b", 7, {**d, "n_asientos": 3})
    assert (r["estado"], r["motivo"]) == ("FALLO", "SIN_CUPO")
    assert _ocupados(engine, "E1", "2026-11-05") == 98


def test_reserva_cupo_exacto(engine):
    d = {"id_evento": "E1", "fecha": "2026/11/05", "n_asientos": 100}
    assert repo.agregar_reserva(engine, "a", 7, d)["estado"] == "EXITO"
    assert _ocupados(engine, "E1", "2026-11-05") == 100


def test_reserva_evento_inexistente(engine):
    r = repo.agregar_reserva(engine, "u", 7, {"id_evento": "E99", "fecha": "2026/11/05", "n_asientos": 1})
    assert (r["estado"], r["motivo"]) == ("FALLO", "NO_EXISTE")


def test_concurrencia_20_clientes_10_asientos_sin_sobreventa(engine):
    d = {"id_evento": "E1", "fecha": "2026/11/05", "n_asientos": 1}
    assert repo.agregar_reserva(engine, "pre", 99, {**d, "n_asientos": 90})["estado"] == "EXITO"
    with ThreadPoolExecutor(20) as ex:
        rs = list(ex.map(lambda i: repo.agregar_reserva(engine, f"u{i}", i + 1, d), range(20)))
    assert sum(r["estado"] == "EXITO" for r in rs) == 10
    assert sum(r["motivo"] == "SIN_CUPO" for r in rs) == 10
    assert _ocupados(engine, "E1", "2026-11-05") == 100
