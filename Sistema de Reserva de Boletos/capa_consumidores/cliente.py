"""Cliente del SRB: socket REQ hacia el GT, con timeout y reconexion."""
import zmq

import comun.mensajes as msjs
from comun.config import GT_PORT_CLI, HOST_CLI, HOST_SERV, REINTENTOS, TIMEOUT_MS


class Cliente:
    def __init__(self, id, nombre="cliente", host=None, puerto=None, timeout_ms=None, reintentos=None):
        self.id = id
        self.nombre = nombre
        self.host = host or HOST_SERV
        self.puerto = str(puerto or GT_PORT_CLI)
        self.timeout_ms = timeout_ms or TIMEOUT_MS
        self.reintentos = REINTENTOS if reintentos is None else reintentos
        self.contexto = zmq.Context()
        self.req_socket = None
        self.pull_socket = None            # receptor de notificaciones de cancelacion
        self.direccion_notificacion = None  # "ip:puerto" que se envia en la CANCELACION

    # ---- conexion ---------------------------------------------------------
    def conectar_con_gt(self):
        self.req_socket = self.contexto.socket(zmq.REQ)
        self.req_socket.setsockopt(zmq.RCVTIMEO, self.timeout_ms)
        self.req_socket.setsockopt(zmq.SNDTIMEO, self.timeout_ms)
        self.req_socket.setsockopt(zmq.LINGER, 0)
        self.req_socket.connect(f"tcp://{self.host}:{self.puerto}")

    def reconectar(self):
        """Tras un timeout el REQ queda inservible (ZeroMQ): hay que cerrarlo y crear otro."""
        if self.req_socket is not None:
            self.req_socket.close(linger=0)
        self.conectar_con_gt()

    def cerrar(self):
        if self.req_socket is not None:
            self.req_socket.close(linger=0)
            self.req_socket = None
        if self.pull_socket is not None:
            self.pull_socket.close(linger=0)
            self.pull_socket = None
        self.contexto.term()

    # ---- notificaciones (canal asincrono) -----------------------------------
    def abrir_receptor(self, puerto, host=None):
        """PULL en `puerto`: aqui llega la notificacion cuando una cancelacion se hace efectiva.

        `host` es la IP de ESTA maquina (la que ve el servicio de cancelacion); por defecto la de
        HOST_CAPA_CONSUMIDORES del .env.
        """
        self.pull_socket = self.contexto.socket(zmq.PULL)
        self.pull_socket.setsockopt(zmq.LINGER, 0)
        self.pull_socket.bind(f"tcp://*:{puerto}")
        self.direccion_notificacion = f"{host or HOST_CLI}:{puerto}"

    def esperar_notificacion(self, timeout_ms=10000):
        """Devuelve la notificacion (dict) o None si no llega a tiempo."""
        if self.pull_socket is None or not self.pull_socket.poll(timeout_ms):
            return None
        return msjs.decodificar(self.pull_socket.recv_string())

    # ---- operaciones ------------------------------------------------------
    def enviar_peticion(self, peticion):
        """Devuelve siempre un dict-respuesta.

        Si el GT no contesta se recrea el socket y se REINTENTA con el MISMO uuid: asi, si el GT o el
        RMC cayeron y fueron sustituidos, la operacion continua sin que el usuario haga nada, y como
        persistencia es idempotente por uuid no se duplica una reserva ya confirmada.
        """
        for _ in range(self.reintentos + 1):
            try:
                self.req_socket.send_string(msjs.codificar(peticion))
                return msjs.decodificar(self.req_socket.recv_string())
            except zmq.Again:
                self.reconectar()
        return msjs.rta_error(peticion["uuid"], "sin respuesta del GT")

    def consultar_evento(self, mes):
        peticion = msjs.crear_req('CO', self.id, {'mes': str(mes).strip().lower()})
        return self.enviar_peticion(peticion)

    def reservar_evento(self, id_evento, fecha, n_asientos):
        datos = {'id_evento': id_evento, 'fecha': fecha.strftime('%Y/%m/%d'), 'n_asientos': n_asientos}
        return self.enviar_peticion(msjs.crear_req('R', self.id, datos))

    def modificar_reserva(self, id_reserva, n_asientos=None, fecha=None):
        """Cambia la cantidad O la fecha (otra ocurrencia del mismo evento) de una reserva propia."""
        datos = {'id_reserva': id_reserva}
        if n_asientos is not None:
            datos['n_asientos'] = n_asientos
        if fecha is not None:
            datos['fecha'] = fecha.strftime('%Y/%m/%d')
        return self.enviar_peticion(msjs.crear_req('M', self.id, datos))

    def cancelar_reserva(self, id_reserva):
        """Responde ACK de inmediato; el resultado llega despues por `esperar_notificacion`."""
        if self.direccion_notificacion is None:
            raise RuntimeError("abra un receptor (abrir_receptor) antes de cancelar: ahi llega la notificacion")
        datos = {'id_reserva': id_reserva, 'direccion_notificacion': self.direccion_notificacion}
        return self.enviar_peticion(msjs.crear_req('CA', self.id, datos))

    # ---- presentacion -----------------------------------------------------
    @staticmethod
    def formatear(respuesta, detalle=""):
        datos = respuesta.get("datos", {})
        if respuesta.get("tipo") == "NOTIFICACION":
            if datos.get("resultado") == "EXITO":
                return f"Notificacion: la reserva #{datos.get('id_reserva')} fue cancelada ({datos.get('puestos_liberados', 0)} puestos liberados)"
            return f"Notificacion: no se pudo cancelar la reserva #{datos.get('id_reserva')} ({datos.get('motivo')}): {datos.get('razon', '')}"
        estado, motivo = respuesta.get("estado"), respuesta.get("motivo")
        razon = datos.get("razon", "")
        if estado == "ACK":
            return f"Cancelacion recibida: {razon}"
        if estado == "ERROR":
            return f"Error: {razon or 'fallo del sistema'}"
        if estado == "FALLO":
            return f"Rechazada ({motivo}): {razon}"
        if respuesta.get("peticion_realizada") == "CONSULTA":
            eventos = datos.get("eventos", [])
            if not eventos:
                return f"No hay eventos en {datos.get('mes', '')}."
            filas = [f"{'Evento':<7}{'Nombre':<12}{'Tipo':<11}{'Fecha':<12}{'Libres':>7}{'Ocupados':>9}"]
            for e in eventos:
                libres = e["capacidad"] - e["puestos_ocupados"]
                filas.append(f"{e['id_evento']:<7}{e['nombre']:<12}{e['tipo_evento']:<11}{e['fecha']:<12}"
                             f"{libres:>7}{e['puestos_ocupados']:>9}")
            return "\n".join(filas)
        if respuesta.get("peticion_realizada") == "MODIFICACION":
            return (f"Reserva #{datos.get('id_reserva')} modificada: {datos.get('n_asientos')} puestos "
                    f"el {datos.get('fecha')}")
        if respuesta.get("peticion_realizada") == "RESERVA":
            extra = f": {detalle}" if detalle else ""
            return f"Reserva #{datos.get('id_reserva')} confirmada{extra}"
        return f"{estado}: {datos}"
