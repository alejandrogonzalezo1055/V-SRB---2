"""Punto de entrada de la capa de consumidores.

    python -m capa_consumidores.consumers_main                      # modo interactivo
    python -m capa_consumidores.consumers_main consultar --mes noviembre
    python -m capa_consumidores.consumers_main reservar --evento E25 --fecha 2026/11/12 --asientos 3
    python -m capa_consumidores.consumers_main modificar --reserva 1 --asientos 5
    python -m capa_consumidores.consumers_main modificar --reserva 1 --fecha 2026/11/19
    python -m capa_consumidores.consumers_main cancelar --reserva 1 --puerto-notif 6001
    python -m capa_consumidores.consumers_main caso basico        # basico, sin_cupo, inexistente, modificar, cancelacion
    python -m capa_consumidores.consumers_main carga --por-min 20 --pct-consulta 60 --minutos 1
"""
import argparse
import json
import sys
from datetime import date, datetime

from capa_consumidores import carga_clientes, casos
from capa_consumidores.cliente import Cliente


def parsear_fecha(texto):
    """Acepta YYYY-MM-DD o YYYY/MM/DD."""
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(texto.strip(), fmt).date()
        except ValueError:
            pass
    raise ValueError(f"fecha invalida: {texto!r} (use AAAA-MM-DD)")


def _imprimir(cliente, respuesta, como_json, detalle=""):
    print(json.dumps(respuesta, ensure_ascii=False) if como_json else cliente.formatear(respuesta, detalle))
    if respuesta.get("tipo") == "NOTIFICACION":
        return 0 if respuesta["datos"].get("resultado") == "EXITO" else 1
    return 0 if respuesta.get("estado") in ("EXITO", "ACK") else 1


def _pedir_entero(mensaje):
    while True:
        try:
            return int(input(mensaje))
        except ValueError:
            print("Debe ingresar un numero entero.")


def _pedir_fecha(mensaje):
    while True:
        try:
            return parsear_fecha(input(mensaje))
        except ValueError as e:
            print(e)


def cancelar_y_esperar(cliente, id_reserva, espera_s):
    """Envia la cancelacion (ACK inmediato) y espera la notificacion posterior. 0 = cancelada."""
    ack = cliente.cancelar_reserva(id_reserva)
    print(cliente.formatear(ack))
    if ack.get("estado") != "ACK":
        return 1
    notif = cliente.esperar_notificacion(int(espera_s * 1000))
    if notif is None:
        print(f"No llego la notificacion en {espera_s} s (la cancelacion puede haberse hecho igual; revise la BD).")
        return 1
    print(cliente.formatear(notif))
    return 0 if notif["datos"].get("resultado") == "EXITO" else 1


def menu(cliente, puerto_notif=6001, host_notif=None):
    while True:
        opcion = input("\n1. Consultar eventos\n2. Reservar evento\n3. Modificar reserva\n4. Cancelar reserva\n0. Salir\n-> ").strip()
        if opcion == "1":
            mes = input("Mes a consultar (ej. noviembre): ")
            print(cliente.formatear(cliente.consultar_evento(mes)))
        elif opcion == "2":
            evento = input("Id del evento (ej. E25): ").strip().upper()
            fecha = _pedir_fecha("Fecha del evento (AAAA-MM-DD): ")
            n = _pedir_entero("Numero de asientos: ")
            r = cliente.reservar_evento(evento, fecha, n)
            print(cliente.formatear(r, f"{evento} el {fecha:%Y/%m/%d}, {n} puestos"))
        elif opcion == "3":
            rid = _pedir_entero("Numero de la reserva: ")
            if input("Cambiar (1) cantidad o (2) fecha? ").strip() == "2":
                r = cliente.modificar_reserva(rid, fecha=_pedir_fecha("Nueva fecha (AAAA-MM-DD): "))
            else:
                r = cliente.modificar_reserva(rid, n_asientos=_pedir_entero("Nueva cantidad de asientos: "))
            print(cliente.formatear(r))
        elif opcion == "4":
            rid = _pedir_entero("Numero de la reserva: ")
            if cliente.direccion_notificacion is None:
                cliente.abrir_receptor(puerto_notif, host=host_notif)
            cancelar_y_esperar(cliente, rid, 10)
        elif opcion == "0":
            return 0
        else:
            print("Opcion invalida")


def construir_parser():
    comun = argparse.ArgumentParser(add_help=False)
    # SUPPRESS: si no se escribe en el subcomando, no pisa el --cliente global
    comun.add_argument("--cliente", type=int, default=argparse.SUPPRESS, help="id del cliente (default 1)")
    comun.add_argument("--json", action="store_true", help="imprime la respuesta cruda")
    comun.add_argument("--puerto-notif", type=int, default=argparse.SUPPRESS, help="puerto del receptor de notificaciones (default 6001)")
    comun.add_argument("--host-notif", default=argparse.SUPPRESS, help="IP de esta maquina para las notificaciones (default: .env)")
    p = argparse.ArgumentParser(description="Cliente del SRB (sin subcomando: modo interactivo)")
    p.add_argument("--gt", metavar="HOST:PUERTO", help="direccion del GT (default: la del .env)")
    p.add_argument("--cliente", type=int, default=None, help="id del cliente (default 1)")
    p.add_argument("--puerto-notif", type=int, default=6001)
    p.add_argument("--host-notif", default=None)
    sub = p.add_subparsers(dest="cmd")
    c = sub.add_parser("consultar", parents=[comun], help="eventos de un mes")
    c.add_argument("--mes", required=True)
    r = sub.add_parser("reservar", parents=[comun], help="reservar puestos")
    r.add_argument("--evento", required=True)
    r.add_argument("--fecha", required=True)
    r.add_argument("--asientos", type=int, required=True)
    m = sub.add_parser("modificar", parents=[comun], help="cambiar la cantidad o la fecha de una reserva")
    m.add_argument("--reserva", type=int, required=True)
    m.add_argument("--asientos", type=int)
    m.add_argument("--fecha")
    x = sub.add_parser("cancelar", parents=[comun], help="cancelar una reserva (asincrono + notificacion)")
    x.add_argument("--reserva", type=int, required=True)
    x.add_argument("--espera", type=float, default=10.0, help="segundos a esperar la notificacion")
    k = sub.add_parser("caso", parents=[comun], help="caso predefinido")
    k.add_argument("nombre", choices=sorted(casos.CASOS))
    g = sub.add_parser("carga", parents=[comun], help="generador de carga")
    g.add_argument("--por-min", type=int, default=20, help="solicitudes por minuto (>= 20)")
    g.add_argument("--pct-consulta", type=int, default=60, help="porcentaje de consultas")
    g.add_argument("--pct-modif", type=int, default=0, help="porcentaje de modificaciones")
    g.add_argument("--pct-cancel", type=int, default=0, help="porcentaje de cancelaciones")
    g.add_argument("--minutos", type=float, default=1.0)
    g.add_argument("--segundos", type=float, help="dura este tiempo (en vez de un numero fijo de peticiones)")
    g.add_argument("--csv", metavar="RUTA", help="escribe una fila por operacion (para las metricas)")
    return p


def main(argv=None):
    p = construir_parser()
    a = p.parse_args(argv)
    host = puerto = None
    if a.gt:
        host, _, puerto = a.gt.partition(":")
    id_cliente = 1 if a.cliente is None else a.cliente
    if id_cliente <= 0:
        p.error("--cliente debe ser un entero positivo")
    cliente = Cliente(id_cliente, host=host, puerto=puerto or None)
    cliente.conectar_con_gt()
    try:
        if a.cmd is None:
            return menu(cliente, a.puerto_notif, a.host_notif)
        if a.cmd == "consultar":
            return _imprimir(cliente, cliente.consultar_evento(a.mes), a.json)
        if a.cmd == "reservar":
            try:
                fecha = parsear_fecha(a.fecha)
            except ValueError as e:
                p.error(str(e))
            r = cliente.reservar_evento(a.evento.upper(), fecha, a.asientos)
            return _imprimir(cliente, r, a.json, f"{a.evento.upper()} el {fecha:%Y/%m/%d}, {a.asientos} puestos")
        if a.cmd == "modificar":
            if (a.asientos is None) == (a.fecha is None):
                p.error("indique exactamente uno: --asientos N o --fecha AAAA/MM/DD")
            try:
                fecha = parsear_fecha(a.fecha) if a.fecha else None
            except ValueError as e:
                p.error(str(e))
            return _imprimir(cliente, cliente.modificar_reserva(a.reserva, a.asientos, fecha), a.json)
        if a.cmd == "cancelar":
            cliente.abrir_receptor(a.puerto_notif, host=a.host_notif)
            return cancelar_y_esperar(cliente, a.reserva, a.espera)
        if a.cmd == "caso":
            estado = 0
            if a.nombre == "cancelacion":
                pasos = casos.cancelacion(cliente, a.puerto_notif, a.host_notif)
            else:
                pasos = casos.CASOS[a.nombre](cliente)
            for descripcion, r in pasos:
                print(f"-> {descripcion}")
                estado |= _imprimir(cliente, r, a.json)
            return estado
        if a.cmd == "carga":
            if a.por_min < 20:
                p.error("el enunciado exige al menos 20 solicitudes por minuto (--por-min)")
            if a.pct_cancel > 0:
                cliente.abrir_receptor(a.puerto_notif, host=a.host_notif)
            rs = carga_clientes.carga_automatica(cliente, a.por_min, a.pct_consulta, a.minutos,
                                                 a.pct_modif, a.pct_cancel, a.segundos)
            if a.csv:
                carga_clientes.escribir_csv(rs, a.csv, id_cliente)
            exitos = sum(r["estado"] == "EXITO" for r in rs)
            print(f"{len(rs)} solicitudes, {exitos} exitosas, "
                  f"latencia media {sum(r['_ms'] for r in rs) / max(len(rs), 1):.1f} ms")
            return 0
    except KeyboardInterrupt:
        print("\ninterrumpido")
        return 130
    finally:
        cliente.cerrar()


if __name__ == "__main__":
    sys.exit(main())
