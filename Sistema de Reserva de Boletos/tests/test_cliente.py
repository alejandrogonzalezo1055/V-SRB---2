import json
from datetime import date

import pytest

from capa_consumidores.cliente import Cliente
from capa_consumidores import carga_clientes, consumers_main, casos
from tests.conftest import P_CLI

GT = f"127.0.0.1:{P_CLI}"


def _cliente(timeout_ms=1000):
    c = Cliente(1, "prueba", host="127.0.0.1", puerto=P_CLI, timeout_ms=timeout_ms)
    c.conectar_con_gt()
    return c


def test_consultar_evento_noviembre_exito_y_acepta_mayusculas(cadena):
    c = _cliente()
    try:
        r = c.consultar_evento("Noviembre")
        assert r["estado"] == "EXITO" and len(r["datos"]["eventos"]) == 70
    finally:
        c.cerrar()


def test_reservar_evento_y_formatear(cadena):
    c = _cliente()
    try:
        r = c.reservar_evento("E25", date(2026, 11, 12), 3)
        assert r["estado"] == "EXITO"
        assert "Reserva #" in c.formatear(r)
    finally:
        c.cerrar()


def test_formatear_sin_cupo_y_error():
    c = Cliente(1)
    sin_cupo = {"estado": "FALLO", "motivo": "SIN_CUPO", "peticion_realizada": "RESERVA",
                "datos": {"razon": "hay 2 puestos libres"}, "uuid": "x"}
    assert "Rechazada" in c.formatear(sin_cupo) and "SIN_CUPO" in c.formatear(sin_cupo)
    error = {"estado": "ERROR", "motivo": "PETICION_INVALIDA", "peticion_realizada": "NULL",
             "datos": {"razon": "sin respuesta del GT"}, "uuid": "x"}
    assert "sin respuesta del GT" in c.formatear(error)


def test_gt_apagado_devuelve_error_y_el_socket_se_recrea(cadena):
    c = _cliente(timeout_ms=500)
    try:
        cadena["rmc"].detener()
        cadena["gt"].detener()
        import time; time.sleep(0.6)
        r = c.consultar_evento("noviembre")
        assert r["estado"] == "ERROR"
        # el REQ quedo inservible tras el timeout: debe haberse recreado (no lanza zmq.ZMQError)
        r2 = c.consultar_evento("noviembre")
        assert r2["estado"] == "ERROR"
    finally:
        c.cerrar()


def test_carga_automatica_no_genera_no_existe_ni_dato_invalido(cadena):
    c = _cliente()
    try:
        rs = carga_clientes.carga_automatica(c, sol_minuto=600, porcen_cons=50, duracion_minutos=0.02)
        assert len(rs) == 12
        assert all(r["estado"] != "ERROR" for r in rs)
        assert not any(r["motivo"] in ("NO_EXISTE", "DATO_INVALIDO") for r in rs)
        assert all("_ms" in r for r in rs)
    finally:
        c.cerrar()


def test_importar_carga_no_ejecuta_nada():
    import importlib
    importlib.reload(carga_clientes)  # si ejecutara main_carga() al importar, intentaria conectarse


def test_cli_consultar_tabla_y_json(cadena, capsys):
    assert consumers_main.main(["--gt", GT, "consultar", "--mes", "noviembre"]) == 0
    assert "E1" in capsys.readouterr().out
    assert consumers_main.main(["--gt", GT, "consultar", "--mes", "noviembre", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["estado"] == "EXITO"


def test_cli_reservar_y_rechazo(cadena, capsys):
    assert consumers_main.main(["--gt", GT, "reservar", "--evento", "E25", "--fecha", "2026/11/12",
                                "--asientos", "3", "--cliente", "2"]) == 0
    assert "Reserva #" in capsys.readouterr().out
    assert consumers_main.main(["--gt", GT, "reservar", "--evento", "E99", "--fecha", "2026/11/12",
                                "--asientos", "3"]) == 1
    assert "NO_EXISTE" in capsys.readouterr().out


def test_cli_caso_basico_y_caso_desconocido(cadena, capsys):
    assert consumers_main.main(["--gt", GT, "caso", "basico"]) == 0
    assert "Reserva #" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        consumers_main.main(["--gt", GT, "caso", "no_existe"])


def test_cli_modo_interactivo_sin_argumentos(cadena, monkeypatch, capsys):
    entradas = iter(["abc", "1", "noviembre", "2", "E27", "2026-11-12", "x", "2", "0"])
    monkeypatch.setattr("builtins.input", lambda *_: next(entradas))
    assert consumers_main.main(["--gt", GT]) == 0
    out = capsys.readouterr().out
    assert "E1" in out and "Reserva #" in out   # entradas invalidas ("abc", "x") se reintentan


def test_cli_cliente_global_y_de_subcomando_se_respetan(cadena, engine, capsys):
    from sqlalchemy import text
    assert consumers_main.main(["--gt", GT, "--cliente", "5", "reservar", "--evento", "E25",
                                "--fecha", "2026/11/12", "--asientos", "1"]) == 0
    assert consumers_main.main(["--gt", GT, "reservar", "--evento", "E25", "--fecha", "2026/11/12",
                                "--asientos", "1", "--cliente", "6"]) == 0
    with engine.connect() as c:
        ids = [f[0] for f in c.execute(text("select id_cliente from reserva order by id"))]
    assert ids == [5, 6]


def test_cli_cliente_cero_o_negativo_es_rechazado(cadena):
    for valor in ("0", "-3"):
        with pytest.raises(SystemExit):
            consumers_main.main(["--gt", GT, "--cliente", valor, "consultar", "--mes", "noviembre"])
        with pytest.raises(SystemExit):
            consumers_main.main(["--gt", GT, "consultar", "--mes", "noviembre", "--cliente", valor])


# ---- etapa 2: modificar, cancelar, receptor y reintentos ----------------------------------
import threading

import zmq

from comun.mensajes import codificar, decodificar


def test_modificar_y_formatear(cadena):
    c = _cliente()
    try:
        rid = c.reservar_evento("E1", date(2026, 11, 5), 3)["datos"]["id_reserva"]
        r = c.modificar_reserva(rid, n_asientos=5)
        assert r["estado"] == "EXITO" and "modificada" in c.formatear(r)
        r = c.modificar_reserva(rid, fecha=date(2026, 11, 19))
        assert r["estado"] == "EXITO" and "2026/11/19" in c.formatear(r)
        assert c.modificar_reserva(rid, n_asientos=1, fecha=date(2026, 11, 5))["estado"] == "FALLO"
    finally:
        c.cerrar()


def test_cancelar_requiere_receptor_y_notifica(cadena):
    c = _cliente()
    try:
        rid = c.reservar_evento("E1", date(2026, 11, 5), 3)["datos"]["id_reserva"]
        with pytest.raises(RuntimeError):
            c.cancelar_reserva(rid)               # sin receptor abierto no hay donde notificar
        c.abrir_receptor(16101, host="127.0.0.1")
        ack = c.cancelar_reserva(rid)
        assert ack["estado"] == "ACK" and "recibida" in c.formatear(ack)
        notif = c.esperar_notificacion(4000)
        assert notif["datos"]["resultado"] == "EXITO"
        assert "cancelada" in c.formatear(notif)
        assert c.esperar_notificacion(200) is None   # no hay mas
    finally:
        c.cerrar()


def test_el_cliente_reintenta_con_el_mismo_uuid():
    visto = []

    def servidor():
        ctx = zmq.Context()
        s = ctx.socket(zmq.ROUTER)
        s.setsockopt(zmq.LINGER, 0)
        s.bind("tcp://127.0.0.1:17000")
        s.RCVTIMEO = 5000
        try:
            while len(visto) < 3:
                frames = s.recv_multipart()
                p = decodificar(frames[-1].decode())
                visto.append(p["uuid"])
                if len(visto) == 3:   # las dos primeras "se pierden": no se responde
                    rta = {"uuid": p["uuid"], "estado": "EXITO", "peticion_realizada": "CONSULTA",
                           "motivo": "NULL", "datos": {"mes": "noviembre", "eventos": []}}
                    s.send_multipart(frames[:2] + [codificar(rta).encode()])
        finally:
            s.close(); ctx.term()

    t = threading.Thread(target=servidor, daemon=True); t.start()
    import time; time.sleep(0.3)
    c = Cliente(1, host="127.0.0.1", puerto="17000", timeout_ms=300, reintentos=2)
    c.conectar_con_gt()
    try:
        assert c.consultar_evento("noviembre")["estado"] == "EXITO"
    finally:
        c.cerrar(); t.join(timeout=3)
    assert len(visto) == 3 and len(set(visto)) == 1


def test_cli_modificar_y_cancelar(cadena, capsys):
    assert consumers_main.main(["--gt", GT, "reservar", "--evento", "E1", "--fecha", "2026/11/05", "--asientos", "2"]) == 0
    assert "Reserva #1 " in capsys.readouterr().out
    assert consumers_main.main(["--gt", GT, "modificar", "--reserva", "1", "--asientos", "4"]) == 0
    assert "modificada" in capsys.readouterr().out
    assert consumers_main.main(["--gt", GT, "modificar", "--reserva", "1", "--fecha", "2026/11/19"]) == 0
    assert consumers_main.main(["--gt", GT, "cancelar", "--reserva", "1", "--puerto-notif", "16102",
                                "--host-notif", "127.0.0.1", "--espera", "4"]) == 0
    out = capsys.readouterr().out
    assert "recibida" in out and "cancelada" in out
    with pytest.raises(SystemExit):   # modificar necesita exactamente una de las dos
        consumers_main.main(["--gt", GT, "modificar", "--reserva", "1"])


def test_carga_con_modificaciones_y_csv(cadena, tmp_path):
    c = _cliente()
    try:
        rs = carga_clientes.carga_automatica(c, sol_minuto=600, porcen_cons=20, duracion_minutos=0.05,
                                             porcen_modif=40)
        assert len(rs) == 30
        ops = {r["_op"] for r in rs}
        assert "CONSULTA" in ops and "RESERVA" in ops
        assert not any(r["motivo"] in ("DATO_INVALIDO", "NO_AUTORIZADO") for r in rs)
        ruta = tmp_path / "ops.csv"
        carga_clientes.escribir_csv(rs, str(ruta), cliente=1)
        filas = ruta.read_text().strip().splitlines()
        assert filas[0] == "cliente,operacion,estado,motivo,ms" and len(filas) == 31
    finally:
        c.cerrar()


def test_cli_caso_cancelacion_termina_con_exito(cadena, capsys):
    assert consumers_main.main(["--gt", GT, "caso", "cancelacion", "--puerto-notif", "16103",
                                "--host-notif", "127.0.0.1"]) == 0
    out = capsys.readouterr().out
    assert "recibida" in out and "fue cancelada" in out


def test_carga_por_tiempo_se_detiene_a_los_segundos_pedidos(cadena):
    c = _cliente()
    try:
        t0 = __import__("time").time()
        rs = carga_clientes.carga_automatica(c, sol_minuto=600, porcen_cons=100, segundos=1.0)
        assert 0.9 <= __import__("time").time() - t0 <= 2.0
        assert 5 <= len(rs) <= 12
    finally:
        c.cerrar()
