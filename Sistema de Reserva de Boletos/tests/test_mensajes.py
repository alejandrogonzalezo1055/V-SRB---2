import pytest
from comun.mensajes import crear_req, crear_rta, rta_error, codificar, decodificar


def test_crear_req_genera_uuid_distinto_cada_vez():
    a = crear_req('R', 1, {})
    b = crear_req('R', 1, {})
    assert a['uuid'] and b['uuid'] and a['uuid'] != b['uuid']


def test_crear_req_conserva_uuid_recibido():
    assert crear_req('R', 1, {}, uuid_peticion='abc')['uuid'] == 'abc'


def test_crear_req_usa_valor_completo_del_tipo():
    assert crear_req('CO_D', 2, {'x': 1})['tipo'] == 'CONSULTA_DISPONIBILIDAD'


def test_crear_req_tipo_desconocido_lanza_valueerror():
    with pytest.raises(ValueError):
        crear_req('X', 1, {})


def test_crear_req_cliente_no_positivo_lanza_valueerror():
    with pytest.raises(ValueError):
        crear_req('R', 0, {})


def test_crear_rta_exito_sin_motivo_y_mismo_uuid():
    r = crear_rta('E', 'R', None, {}, 'abc')
    assert r['motivo'] == 'NULL'
    assert r['uuid'] == 'abc'
    assert r['estado'] == 'EXITO'
    assert r['peticion_realizada'] == 'RESERVA'


def test_crear_rta_sin_cupo_es_fallo():
    r = crear_rta('F', 'R', 'S_C', {}, 'abc')
    assert r['estado'] == 'FALLO'
    assert r['motivo'] == 'SIN_CUPO'


def test_crear_rta_codigo_desconocido_lanza_valueerror():
    with pytest.raises(ValueError):
        crear_rta('Z', 'R', None, {}, 'abc')


def test_rta_error_es_error_peticion_invalida():
    r = rta_error('abc', 'algo salio mal')
    assert r['estado'] == 'ERROR'
    assert r['motivo'] == 'PETICION_INVALIDA'
    assert r['uuid'] == 'abc'
    assert r['datos']['razon'] == 'algo salio mal'


def test_codificar_decodificar_round_trip():
    p = crear_req('CO', 1, {'mes': 'noviembre'})
    assert decodificar(codificar(p)) == p
