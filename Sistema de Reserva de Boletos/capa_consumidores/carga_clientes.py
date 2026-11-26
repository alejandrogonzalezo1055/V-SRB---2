"""Generador de carga: peticiones automaticas con tasa y mezcla parametrizables."""
import csv
import random
import time
from datetime import date

from comun.mensajes import meses

ANIO, MES = 2026, 11
EVENTOS, DOBLES = 50, 20  # coincide con el seed por defecto


def ocurrencias_seed(eventos=EVENTOS, dobles=DOBLES):
    """(id_evento, fecha) que existen en los datos iniciales."""
    return [(f"E{i}", date(ANIO, MES, d)) for i in range(1, eventos + 1)
            for d in ((5, 19) if i <= dobles else (12,))]


def _otra_fecha(evento, fecha):
    """La otra ocurrencia del evento (solo los eventos E1..E20 tienen dos), o None."""
    if int(evento[1:]) > DOBLES:
        return None
    return date(ANIO, MES, 19 if fecha.day == 5 else 5)


def carga_automatica(cliente, sol_minuto, porcen_cons, duracion_minutos=1, porcen_modif=0, porcen_cancel=0,
                     segundos=None):
    """Envia `sol_minuto * duracion_minutos` peticiones.

    `porcen_cons`% consultas, `porcen_modif`% modificaciones, `porcen_cancel`% cancelaciones (solo si el
    cliente tiene un receptor abierto) y el resto reservas. Modificar/cancelar actuan sobre reservas
    del propio cliente; si aun no tiene ninguna, se reserva. Cada respuesta lleva `_ms` (latencia) y
    `_op` (operacion enviada). Si se da `segundos`, la carga dura ese tiempo (en vez de un numero fijo de
    peticiones): es lo que se usa para medir.
    """
    pausa = 60.0 / sol_minuto
    total = float("inf") if segundos is not None else round(sol_minuto * duracion_minutos)
    fin = time.perf_counter() + segundos if segundos is not None else None
    existentes = ocurrencias_seed()
    mias = []                      # reservas propias: {"id", "evento", "fecha", "n"}
    respuestas = []
    enviadas = 0
    while enviadas < total and (fin is None or time.perf_counter() < fin):
        enviadas += 1
        inicio = time.perf_counter()
        azar = random.randint(1, 100)
        if azar <= porcen_cons:
            op, r = "CONSULTA", cliente.consultar_evento(random.choice(meses))
        elif azar <= porcen_cons + porcen_modif and mias:
            m = random.choice(mias)
            otra = _otra_fecha(m["evento"], m["fecha"])
            if otra is not None and random.random() < 0.5:
                op, r = "MODIFICACION", cliente.modificar_reserva(m["id"], fecha=otra)
                if r["estado"] == "EXITO":
                    m["fecha"] = otra
            else:
                n = random.randint(1, 4)
                op, r = "MODIFICACION", cliente.modificar_reserva(m["id"], n_asientos=n)
                if r["estado"] == "EXITO":
                    m["n"] = n
        elif (azar <= porcen_cons + porcen_modif + porcen_cancel and mias
              and cliente.direccion_notificacion is not None):
            m = mias.pop(random.randrange(len(mias)))
            op, r = "CANCELACION", cliente.cancelar_reserva(m["id"])
        else:
            evento, fecha = random.choice(existentes)
            n = random.randint(1, 4)
            op, r = "RESERVA", cliente.reservar_evento(evento, fecha, n)
            if r["estado"] == "EXITO":
                mias.append({"id": r["datos"]["id_reserva"], "evento": evento, "fecha": fecha, "n": n})
        r["_ms"] = round((time.perf_counter() - inicio) * 1000, 2)
        r["_op"] = op
        respuestas.append(r)
        restante = pausa - (time.perf_counter() - inicio)
        if restante > 0:
            time.sleep(restante)
    return respuestas


def escribir_csv(respuestas, ruta, cliente):
    """Una fila por operacion: cliente,operacion,estado,motivo,ms (entrada para las metricas)."""
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["cliente", "operacion", "estado", "motivo", "ms"])
        for r in respuestas:
            w.writerow([cliente, r["_op"], r["estado"], r["motivo"], r["_ms"]])
