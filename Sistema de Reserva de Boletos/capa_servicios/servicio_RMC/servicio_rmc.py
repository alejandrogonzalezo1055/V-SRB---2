"""Servicio RMC (Reserva, Modificacion y Consulta).

Recibe del GT (socket REP conectado al DEALER del GT) y consulta a persistencia
(socket REQ hacia el ROUTER de persistencia). Un REP conectado a un DEALER maneja solo
el sobre de ruteo: basta con recv_string / send_string.
"""
from abc import ABC, abstractmethod

import zmq

import comun.mensajes as msjs
from comun.config import GT_PORT_RMC, HOST_PERS, HOST_SERV, PS_PORT, TIMEOUT_PERSISTENCIA_MS
from comun.log import log

MAX_PUESTOS = 100


class I_Servicio_RMC(ABC):
    @abstractmethod
    def crear_reserva(self, peticion):
        pass

    @abstractmethod
    def modificar_reserva(self, peticion):
        pass

    @abstractmethod
    def consultar_eventos(self, peticion):
        pass


class Cliente_persistencia:
    """REQ hacia persistencia con timeout; tras un timeout el socket se recrea (regla de ZeroMQ)."""

    def __init__(self, host=None, puerto=None, timeout_ms=None):
        self.host = host or HOST_PERS
        self.puerto = str(puerto or PS_PORT)
        self.timeout_ms = timeout_ms or TIMEOUT_PERSISTENCIA_MS
        self.ctx = zmq.Context.instance()
        self.socket = None
        self._conectar()

    def _conectar(self):
        self.socket = self.ctx.socket(zmq.REQ)
        self.socket.setsockopt(zmq.RCVTIMEO, self.timeout_ms)
        self.socket.setsockopt(zmq.SNDTIMEO, self.timeout_ms)
        self.socket.setsockopt(zmq.LINGER, 0)
        self.socket.connect(f"tcp://{self.host}:{self.puerto}")

    def enviar(self, peticion):
        try:
            self.socket.send_string(msjs.codificar(peticion))
            return msjs.decodificar(self.socket.recv_string())
        except zmq.Again:
            self.socket.close()
            self._conectar()
            log("RMC", f"persistencia no responde (uuid={peticion.get('uuid', '')[:8]})")
            return msjs.rta_error(peticion.get("uuid", ""), "persistencia no responde")


def _como(rta, abreviatura):
    """Devuelve la respuesta de persistencia presentada como respuesta a la peticion del cliente."""
    rta["peticion_realizada"] = msjs.peticiones[abreviatura]
    return rta


class Servicio_RMC(I_Servicio_RMC):
    def __init__(self, mensajero_persistencia):
        self.mensajero_persistencia = mensajero_persistencia
        self._parar = False

    def consultar_eventos(self, peticion):
        return self.mensajero_persistencia.enviar(peticion)

    def crear_reserva(self, peticion):
        uuid = peticion.get("uuid", "")
        id_cliente = peticion.get("id_cliente")
        datos = peticion.get("datos") or {}
        n = datos.get("n_asientos")
        if (not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= MAX_PUESTOS
                or not isinstance(id_cliente, int) or id_cliente <= 0
                or not isinstance(datos.get("id_evento"), str) or not isinstance(datos.get("fecha"), str)):
            return msjs.crear_rta('F', 'R', 'D_I', {'razon': 'id_cliente, id_evento, fecha o n_asientos invalidos'}, uuid)

        disp = self.mensajero_persistencia.enviar(msjs.crear_req(
            'CO_D', id_cliente, {'id_evento': datos['id_evento'], 'fecha': datos['fecha']}, uuid))
        if disp['estado'] != 'EXITO':
            return _como(disp, 'R')

        libres = disp['datos']['capacidad_total'] - disp['datos']['ocupacion']
        if libres < n:
            return msjs.crear_rta('F', 'R', 'S_C', {'razon': f'hay {libres} puestos libres y se pidieron {n}'}, uuid)

        agregar = msjs.crear_req('A_R', id_cliente, {
            'id_evento': datos['id_evento'], 'fecha': datos['fecha'], 'n_asientos': n}, uuid)
        return _como(self.mensajero_persistencia.enviar(agregar), 'R')

    def modificar_reserva(self, peticion):
        """La comprobacion de cupo y el cambio se hacen en UNA transaccion en persistencia (sin carreras)."""
        id_cliente = peticion.get("id_cliente")
        if not isinstance(id_cliente, int) or isinstance(id_cliente, bool) or id_cliente <= 0:
            return msjs.crear_rta('F', 'M', 'D_I', {'razon': 'id_cliente invalido'}, peticion.get('uuid', ''))
        return self.mensajero_persistencia.enviar(peticion)

    def atender(self, peticion):
        if not isinstance(peticion, dict):
            return msjs.rta_error("", "peticion mal formada")
        tipo = peticion.get("tipo")
        uuid = str(peticion.get("uuid", ""))
        if tipo == "CONSULTA":
            id_cliente = peticion.get("id_cliente")
            if not isinstance(id_cliente, int) or isinstance(id_cliente, bool) or id_cliente <= 0:
                return msjs.crear_rta('F', 'CO', 'D_I', {'razon': 'id_cliente invalido'}, uuid)
            rta = self.consultar_eventos(peticion)
        elif tipo == "RESERVA":
            rta = self.crear_reserva(peticion)
        elif tipo == "MODIFICACION":
            rta = self.modificar_reserva(peticion)
        else:
            return msjs.rta_error(uuid, f"tipo no soportado por el RMC: {tipo}")
        motivo = '' if rta['motivo'] == 'NULL' else f"/{rta['motivo']}"
        log("RMC", f"{tipo} cliente={peticion.get('id_cliente')} uuid={uuid[:8]} -> {rta['estado']}{motivo}")
        return rta

    def iniciar(self, host=None, puerto=None):
        """Bloqueante: atiende lo que el GT le reparte por su DEALER."""
        sock = zmq.Context.instance().socket(zmq.REP)
        sock.setsockopt(zmq.LINGER, 0)
        sock.connect(f"tcp://{host or HOST_SERV}:{puerto or GT_PORT_RMC}")
        log("RMC", "listo, conectado al GT")
        self._parar = False
        try:
            while not self._parar:
                if not sock.poll(200):
                    continue
                partes = sock.recv_multipart()   # se lee TODO el mensaje: el REP debe poder responder siempre
                try:
                    if len(partes) != 1:
                        raise ValueError("mensaje multipart no soportado")
                    rta = self.atender(msjs.decodificar(partes[0].decode("utf-8")))
                except ValueError:   # incluye JSON invalido y UnicodeDecodeError
                    rta = msjs.rta_error("", "peticion ilegible")
                except Exception as e:  # noqa: BLE001  (el RMC no debe morir por una peticion)
                    log("RMC", f"error interno: {e}")
                    rta = msjs.rta_error("", "error interno del RMC")
                sock.send_string(msjs.codificar(rta))
        finally:
            sock.close()

    def detener(self):
        self._parar = True
