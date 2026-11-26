"""Operaciones sobre la BD. No conoce ZeroMQ: recibe un engine y devuelve Respuestas."""
from datetime import datetime

from sqlalchemy import extract, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from comun.mensajes import crear_rta, meses
from capa_persistencia.modelos import evento, ocurrencia, reserva

MAX_PUESTOS = 100


def _fecha(texto):
    """'2026/11/05' -> date; None si el formato no es valido."""
    try:
        return datetime.strptime(texto, "%Y/%m/%d").date()
    except (TypeError, ValueError):
        return None


def _fallo(peticion, motivo, uuid, razon):
    return crear_rta('F', peticion, motivo, {'razon': razon}, uuid)


def consultar_eventos(engine, uuid, datos):
    mes = datos.get("mes") if isinstance(datos, dict) else None
    if not isinstance(mes, str) or mes not in meses:
        return _fallo('CO', 'D_I', uuid, f"mes invalido: {mes!r}")
    numero = meses.index(mes) + 1
    consulta = (
        select(evento.c.id, evento.c.nombre, evento.c.tipo_evento,
               ocurrencia.c.fecha, ocurrencia.c.capacidad, ocurrencia.c.puestos_ocupados)
        .join(ocurrencia, ocurrencia.c.evento_id == evento.c.id)
        .where(extract("month", ocurrencia.c.fecha) == numero)
        .order_by(ocurrencia.c.fecha, evento.c.id)
    )
    with engine.connect() as conn:
        filas = conn.execute(consulta).all()
    eventos = [{
        "id_evento": f.id, "nombre": f.nombre, "tipo_evento": f.tipo_evento,
        "fecha": f.fecha.strftime("%Y/%m/%d"),
        "capacidad": f.capacidad, "puestos_ocupados": f.puestos_ocupados,
    } for f in filas]
    return crear_rta('E', 'CO', None, {"mes": mes, "eventos": eventos}, uuid)


def consultar_disponibilidad(engine, uuid, datos):
    fecha = _fecha(datos.get("fecha"))
    id_evento = datos.get("id_evento")
    if fecha is None or not isinstance(id_evento, str):
        return _fallo('CO_D', 'D_I', uuid, "id_evento o fecha invalidos")
    with engine.connect() as conn:
        fila = conn.execute(
            select(ocurrencia.c.capacidad, ocurrencia.c.puestos_ocupados)
            .where(ocurrencia.c.evento_id == id_evento, ocurrencia.c.fecha == fecha)
        ).first()
    if fila is None:
        return _fallo('CO_D', 'N_E', uuid, "no existe esa ocurrencia del evento")
    return crear_rta('E', 'CO_D', None,
                     {"capacidad_total": fila.capacidad, "ocupacion": fila.puestos_ocupados}, uuid)


def agregar_reserva(engine, uuid, id_cliente, datos):
    """Reserva atomica: el UPDATE condicionado evita la sobreventa entre clientes concurrentes."""
    n = datos.get("n_asientos")
    fecha = _fecha(datos.get("fecha"))
    id_evento = datos.get("id_evento")
    if (not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= MAX_PUESTOS
            or fecha is None or not isinstance(id_evento, str)
            or not isinstance(id_cliente, int) or id_cliente <= 0):
        return _fallo('A_R', 'D_I', uuid, "datos de reserva invalidos")
    try:
        return _agregar_reserva(engine, uuid, id_cliente, id_evento, fecha, n)
    except IntegrityError:   # dos reintentos simultaneos con el mismo uuid: gana uno, el otro lo reutiliza
        existente = _reserva_por_uuid(engine, uuid)
        if existente is None:
            raise
        return crear_rta('E', 'A_R', None, {"id_reserva": existente, "repetida": True}, uuid)


def _reserva_por_uuid(engine, uuid):
    with engine.connect() as conn:
        return conn.execute(select(reserva.c.id).where(reserva.c.uuid == uuid)).scalar()


def _agregar_reserva(engine, uuid, id_cliente, id_evento, fecha, n):
    with engine.begin() as conn:
        if uuid:
            previa = conn.execute(select(reserva.c.id).where(reserva.c.uuid == uuid)).scalar()
            if previa is not None:   # reintento de una peticion ya confirmada: no se duplica
                return crear_rta('E', 'A_R', None, {"id_reserva": previa, "repetida": True}, uuid)
        oc_id = conn.execute(
            select(ocurrencia.c.id)
            .where(ocurrencia.c.evento_id == id_evento, ocurrencia.c.fecha == fecha)
        ).scalar()
        if oc_id is None:
            return _fallo('A_R', 'N_E', uuid, "no existe esa ocurrencia del evento")
        actualizadas = conn.execute(
            update(ocurrencia)
            .where(ocurrencia.c.id == oc_id,
                   ocurrencia.c.puestos_ocupados + n <= ocurrencia.c.capacidad)
            .values(puestos_ocupados=ocurrencia.c.puestos_ocupados + n)
        ).rowcount
        if actualizadas == 0:
            return _fallo('A_R', 'S_C', uuid, "no hay puestos suficientes")
        id_reserva = conn.execute(
            insert(reserva)
            .values(uuid=uuid or None, id_cliente=id_cliente, ocurrencia_id=oc_id, n_puestos=n)
            .returning(reserva.c.id)
        ).scalar_one()
    return crear_rta('E', 'A_R', None, {"id_reserva": id_reserva}, uuid)


def _entero(x):
    return isinstance(x, int) and not isinstance(x, bool)


def _bloquear_ocurrencias(conn, ids):
    """Bloquea las ocurrencias SIEMPRE en orden de id (evita interbloqueos entre modificaciones cruzadas)."""
    conn.execute(select(ocurrencia.c.id).where(ocurrencia.c.id.in_(ids)).order_by(ocurrencia.c.id).with_for_update()).all()


def modificar_reserva(engine, uuid, id_cliente, datos):
    """Cambia la cantidad O la fecha (otra ocurrencia del mismo evento y mes) de una reserva, atomicamente.

    Si se rechaza, la reserva queda exactamente como estaba (no se escribe nada antes de validar el cupo).
    """
    datos = datos if isinstance(datos, dict) else {}
    id_res, n, fecha_txt = datos.get("id_reserva"), datos.get("n_asientos"), datos.get("fecha")
    fecha = _fecha(fecha_txt) if fecha_txt is not None else None
    if (not _entero(id_res) or not _entero(id_cliente) or id_cliente <= 0
            or (n is None) == (fecha_txt is None)                       # exactamente una de las dos
            or (n is not None and (not _entero(n) or not 1 <= n <= MAX_PUESTOS))
            or (fecha_txt is not None and fecha is None)):
        return _fallo('M', 'D_I', uuid, "indique id_reserva y exactamente uno de n_asientos (1-100) o fecha (AAAA/MM/DD)")
    with engine.begin() as conn:
        res = conn.execute(select(reserva).where(reserva.c.id == id_res).with_for_update()).first()
        if res is None or not res.estado:
            return _fallo('M', 'N_E', uuid, "la reserva no existe o ya fue cancelada")
        if res.id_cliente != id_cliente:
            return _fallo('M', 'N_A', uuid, "la reserva pertenece a otro cliente")
        origen = conn.execute(select(ocurrencia).where(ocurrencia.c.id == res.ocurrencia_id)).first()
        if n is not None:
            delta = n - res.n_puestos
            _bloquear_ocurrencias(conn, [origen.id])
            if delta > 0:
                ok = conn.execute(update(ocurrencia).where(
                    ocurrencia.c.id == origen.id, ocurrencia.c.puestos_ocupados + delta <= ocurrencia.c.capacidad
                ).values(puestos_ocupados=ocurrencia.c.puestos_ocupados + delta)).rowcount
                if ok == 0:
                    return _fallo('M', 'S_C', uuid, f"no hay {delta} puestos mas disponibles")
            elif delta < 0:
                conn.execute(update(ocurrencia).where(ocurrencia.c.id == origen.id)
                             .values(puestos_ocupados=ocurrencia.c.puestos_ocupados + delta))
            conn.execute(update(reserva).where(reserva.c.id == res.id).values(n_puestos=n))
            destino_fecha = origen.fecha
        elif fecha == origen.fecha:
            destino_fecha = origen.fecha   # ya estaba en esa fecha: reintento idempotente
        else:
            destino = conn.execute(select(ocurrencia.c.id).where(
                ocurrencia.c.evento_id == origen.evento_id, ocurrencia.c.fecha == fecha)).first()
            total = conn.execute(select(func.count()).select_from(ocurrencia)
                                 .where(ocurrencia.c.evento_id == origen.evento_id)).scalar_one()
            if total == 1:
                return _fallo('M', 'D_I', uuid, "el evento solo tiene una ocurrencia: solo se puede cambiar la cantidad")
            if destino is None or fecha.month != origen.fecha.month:
                return _fallo('M', 'N_E', uuid, "ese evento no tiene otra ocurrencia en esa fecha dentro del mismo mes")
            _bloquear_ocurrencias(conn, [origen.id, destino.id])
            ok = conn.execute(update(ocurrencia).where(
                ocurrencia.c.id == destino.id,
                ocurrencia.c.puestos_ocupados + res.n_puestos <= ocurrencia.c.capacidad
            ).values(puestos_ocupados=ocurrencia.c.puestos_ocupados + res.n_puestos)).rowcount
            if ok == 0:
                return _fallo('M', 'S_C', uuid, "no hay cupo en la otra fecha")
            conn.execute(update(ocurrencia).where(ocurrencia.c.id == origen.id)
                         .values(puestos_ocupados=ocurrencia.c.puestos_ocupados - res.n_puestos))
            conn.execute(update(reserva).where(reserva.c.id == res.id).values(ocurrencia_id=destino.id))
            destino_fecha = fecha
        n_final = n if n is not None else res.n_puestos
    return crear_rta('E', 'M', None, {"id_reserva": id_res, "n_asientos": n_final,
                                      "fecha": destino_fecha.strftime("%Y/%m/%d")}, uuid)


def cancelar_reserva(engine, uuid, id_cliente, datos):
    """Marca la reserva como cancelada y libera sus puestos. Repetirla no libera dos veces."""
    datos = datos if isinstance(datos, dict) else {}
    id_res = datos.get("id_reserva")
    if not _entero(id_res) or not _entero(id_cliente) or id_cliente <= 0:
        return _fallo('CA', 'D_I', uuid, "indique id_reserva (entero) y un id_cliente valido")
    with engine.begin() as conn:
        res = conn.execute(select(reserva).where(reserva.c.id == id_res).with_for_update()).first()
        if res is None:
            return _fallo('CA', 'N_E', uuid, "la reserva no existe")
        if res.id_cliente != id_cliente:
            return _fallo('CA', 'N_A', uuid, "la reserva pertenece a otro cliente")
        if not res.estado:
            return crear_rta('E', 'CA', None, {"id_reserva": id_res, "puestos_liberados": 0,
                                               "ya_cancelada": True}, uuid)
        _bloquear_ocurrencias(conn, [res.ocurrencia_id])
        conn.execute(update(ocurrencia).where(ocurrencia.c.id == res.ocurrencia_id)
                     .values(puestos_ocupados=ocurrencia.c.puestos_ocupados - res.n_puestos))
        conn.execute(update(reserva).where(reserva.c.id == res.id).values(estado=False))
    return crear_rta('E', 'CA', None, {"id_reserva": id_res, "puestos_liberados": res.n_puestos}, uuid)
