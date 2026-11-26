"""Estres de consistencia: muchos clientes mezclando reservar, modificar y cancelar a la vez."""
import random
import threading

from sqlalchemy import text

from capa_persistencia import repositorio as repo

EVENTOS = [("E1", ("2026/11/05", "2026/11/19")), ("E2", ("2026/11/05", "2026/11/19"))]


def _trabajador(engine, id_cliente, ops, errores, resumen):
    rnd = random.Random(id_cliente)
    mias = []   # (id_reserva, evento, fecha_actual)
    for i in range(ops):
        uuid = f"{id_cliente}-{i}"
        try:
            accion = rnd.choice(["reservar", "reservar", "cantidad", "fecha", "cancelar"])
            if accion == "reservar" or not mias:
                ev, fechas = rnd.choice(EVENTOS)
                f = rnd.choice(fechas)
                r = repo.agregar_reserva(engine, uuid, id_cliente, {"id_evento": ev, "fecha": f, "n_asientos": rnd.randint(1, 12)})
                if r["estado"] == "EXITO":
                    mias.append([r["datos"]["id_reserva"], ev, f])
            elif accion == "cantidad":
                m = rnd.choice(mias)
                r = repo.modificar_reserva(engine, uuid, id_cliente, {"id_reserva": m[0], "n_asientos": rnd.randint(1, 12)})
            elif accion == "fecha":
                m = rnd.choice(mias)
                otra = [f for f in dict(EVENTOS)[m[1]] if f != m[2]][0]
                r = repo.modificar_reserva(engine, uuid, id_cliente, {"id_reserva": m[0], "fecha": otra})
                if r["estado"] == "EXITO":
                    m[2] = otra
            else:
                m = mias.pop(rnd.randrange(len(mias)))
                r = repo.cancelar_reserva(engine, uuid, id_cliente, {"id_reserva": m[0]})
            resumen[r["estado"] + "/" + r["motivo"]] = resumen.get(r["estado"] + "/" + r["motivo"], 0) + 1
        except Exception as e:  # noqa: BLE001  (un interbloqueo o error de BD debe hacer fallar la prueba)
            errores.append(repr(e))


def test_ocupacion_coincide_con_las_reservas_activas_tras_un_estres_concurrente(engine):
    errores, resumen = [], {}
    hilos = [threading.Thread(target=_trabajador, args=(engine, c, 40, errores, resumen)) for c in range(1, 17)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert errores == [], errores[:3]
    assert resumen.get("EXITO/NULL", 0) > 100              # hubo trabajo real, no todo rechazado
    with engine.connect() as c:
        desajustes = c.execute(text("""
            select o.id, o.puestos_ocupados, coalesce(sum(r.n_puestos) filter (where r.estado), 0) as reservados
            from ocurrencia o left join reserva r on r.ocurrencia_id = o.id
            group by o.id, o.puestos_ocupados
            having o.puestos_ocupados <> coalesce(sum(r.n_puestos) filter (where r.estado), 0)""")).all()
        fuera = c.execute(text("select count(*) from ocurrencia where puestos_ocupados < 0 or puestos_ocupados > capacidad")).scalar_one()
    assert desajustes == [], desajustes
    assert fuera == 0
