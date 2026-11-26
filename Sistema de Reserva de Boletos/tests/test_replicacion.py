"""Replicacion: las CONSULTAS se leen de la replica (streaming); lo demas, del primario."""
import os
import time

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DBAPIError

from comun.mensajes import crear_req
from capa_persistencia.servicio_persistencia import Servicio_Persistencia

REPLICA_URL = os.getenv("TEST_REPLICA_URL", "postgresql+psycopg://srb:srb@localhost:5433/srb_test")


@pytest.fixture
def replica():
    eng = create_engine(REPLICA_URL, connect_args={"connect_timeout": 2})
    try:
        with eng.connect() as c:
            c.execute(text("select 1"))
    except Exception:
        pytest.skip("no hay replica en :5433 (docker compose up -d)")
    yield eng
    eng.dispose()


def _contar(engine, contador):
    @event.listens_for(engine, "before_cursor_execute")
    def _(conn, cursor, statement, *a):
        if "FROM ocurrencia" in statement or "from ocurrencia" in statement:
            contador.append(statement)


def _ocurrencias(replica):
    """Filas de ocurrencia en la replica (0 si la tabla aun no llego). Cierra SIEMPRE la conexion: una
    consulta abierta en la replica bloquea la aplicacion de un DROP del primario hasta 30 s."""
    try:
        with replica.connect() as c:
            return c.execute(text("select count(*) from ocurrencia")).scalar_one()
    except DBAPIError:
        return 0


def _esperar(cond, segundos=5.0):
    fin = time.time() + segundos
    while time.time() < fin:
        if cond():
            return True
        time.sleep(0.05)
    return False


def _eventos(rta, ev, fecha):
    return [e for e in rta["datos"]["eventos"] if e["id_evento"] == ev and e["fecha"] == fecha][0]


def test_la_replica_es_una_copia_de_solo_lectura(engine, replica):
    with replica.connect() as c:
        assert c.execute(text("select pg_is_in_recovery()")).scalar_one() is True
    assert _esperar(lambda: _ocurrencias(replica) == 70)
    with pytest.raises(Exception):
        with replica.begin() as c:
            c.execute(text("insert into evento(id,nombre,tipo_evento) values ('X','x','x')"))


def test_la_consulta_se_lee_de_la_replica_y_no_del_primario(engine, replica):
    assert _esperar(lambda: _ocurrencias(replica) == 70)
    en_primario, en_replica = [], []
    _contar(engine, en_primario)
    _contar(replica, en_replica)
    s = Servicio_Persistencia(engine, engine_lectura=replica)
    r = s.procesar(crear_req('CO', 1, {"mes": "noviembre"}))
    assert r["estado"] == "EXITO" and len(r["datos"]["eventos"]) == 70
    assert len(en_replica) >= 1 and en_primario == []


def test_disponibilidad_y_reservas_van_al_primario(engine, replica):
    en_primario, en_replica = [], []
    _contar(engine, en_primario)
    _contar(replica, en_replica)
    s = Servicio_Persistencia(engine, engine_lectura=replica)
    d = {"id_evento": "E1", "fecha": "2026/11/05"}
    assert s.procesar(crear_req('CO_D', 1, d))["estado"] == "EXITO"
    assert s.procesar(crear_req('A_R', 1, {**d, "n_asientos": 2}))["estado"] == "EXITO"
    assert len(en_primario) >= 2 and en_replica == []


def test_lo_escrito_en_el_primario_aparece_en_la_replica(engine, replica):
    assert _esperar(lambda: _ocurrencias(replica) == 70)
    s = Servicio_Persistencia(engine, engine_lectura=replica)
    assert s.procesar(crear_req('A_R', 1, {"id_evento": "E1", "fecha": "2026/11/05", "n_asientos": 4}))["estado"] == "EXITO"
    consulta = lambda: _eventos(s.procesar(crear_req('CO', 1, {"mes": "noviembre"})), "E1", "2026/11/05")["puestos_ocupados"]  # noqa: E731
    assert _esperar(lambda: consulta() == 4, 5.0), "la replica no alcanzo al primario en 5 s"


def test_si_la_replica_cae_la_consulta_se_lee_del_primario(engine):
    caida = create_engine("postgresql+psycopg://srb:srb@localhost:5999/srb_test", connect_args={"connect_timeout": 1})
    en_primario = []
    _contar(engine, en_primario)
    s = Servicio_Persistencia(engine, engine_lectura=caida)
    r = s.procesar(crear_req('CO', 1, {"mes": "noviembre"}))
    assert r["estado"] == "EXITO" and len(r["datos"]["eventos"]) == 70
    assert len(en_primario) >= 1


def test_sin_replica_configurada_todo_va_al_primario(engine):
    en_primario = []
    _contar(engine, en_primario)
    s = Servicio_Persistencia(engine)
    assert s.procesar(crear_req('CO', 1, {"mes": "noviembre"}))["estado"] == "EXITO"
    assert len(en_primario) >= 1
