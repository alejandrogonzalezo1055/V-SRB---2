"""Casos predefinidos para ejecutar con `caso <nombre>`.

Cada caso recibe un Cliente conectado y devuelve [(descripcion, respuesta), ...].
Para repetirlos hay que recargar la BD: python -m capa_persistencia.seed --reset
"""
from datetime import date


def basico(cliente):
    return [
        ("Consultar eventos de noviembre", cliente.consultar_evento("noviembre")),
        ("Reservar 5 puestos de E1 el 2026/11/05", cliente.reservar_evento("E1", date(2026, 11, 5), 5)),
    ]


def sin_cupo(cliente):
    return [
        ("Reservar los 100 puestos de E2 el 2026/11/05", cliente.reservar_evento("E2", date(2026, 11, 5), 100)),
        ("Reservar 1 puesto mas (debe rechazarse por SIN_CUPO)", cliente.reservar_evento("E2", date(2026, 11, 5), 1)),
    ]


def inexistente(cliente):
    return [("Reservar en un evento que no existe (E99)", cliente.reservar_evento("E99", date(2026, 11, 5), 1))]


def modificar(cliente):
    r = cliente.reservar_evento("E1", date(2026, 11, 5), 4)
    pasos = [("Reservar 4 puestos de E1 el 2026/11/05", r)]
    if r["estado"] == "EXITO":
        rid = r["datos"]["id_reserva"]
        pasos += [
            (f"Subir la reserva #{rid} a 6 puestos", cliente.modificar_reserva(rid, n_asientos=6)),
            (f"Pasar la reserva #{rid} a la otra fecha de E1 (2026/11/19)", cliente.modificar_reserva(rid, fecha=date(2026, 11, 19))),
            (f"Pedir 101 puestos (debe rechazarse: el maximo por reserva es 100)", cliente.modificar_reserva(rid, n_asientos=101)),
        ]
    return pasos


def cancelacion(cliente, puerto_notif=6001, host_notif=None):
    if cliente.direccion_notificacion is None:
        cliente.abrir_receptor(puerto_notif, host=host_notif)
    r = cliente.reservar_evento("E3", date(2026, 11, 5), 2)
    pasos = [("Reservar 2 puestos de E3 el 2026/11/05", r)]
    if r["estado"] == "EXITO":
        rid = r["datos"]["id_reserva"]
        pasos.append((f"Cancelar la reserva #{rid} (respuesta inmediata)", cliente.cancelar_reserva(rid)))
        notif = cliente.esperar_notificacion(10000)
        pasos.append(("Notificacion posterior del servicio de cancelacion",
                      notif or {"estado": "ERROR", "motivo": "NULL", "datos": {"razon": "no llego la notificacion"}}))
    return pasos


CASOS = {"basico": basico, "sin_cupo": sin_cupo, "inexistente": inexistente, "modificar": modificar,
         "cancelacion": cancelacion}
