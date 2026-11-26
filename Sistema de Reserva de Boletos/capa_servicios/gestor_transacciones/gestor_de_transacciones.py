"""Gestor de Transacciones (GT): puerta de entrada y enrutador.

Canal sincrono: ROUTER (clientes) <-> DEALER (RMC). Los frames viajan intactos; el GT solo
mira el ultimo (el JSON) para registrar la operacion y validar que sea legible.
Canal asincrono: las CANCELACION se responden al instante con ACK y el trabajo sale por PUSH
(los servicios de cancelacion son PULL y se reparten el trabajo por turnos).
"""
import zmq

import comun.mensajes as msjs
import re

from comun.config import GT_PORT_CANC, GT_PORT_CLI, GT_PORT_RMC
from comun.log import log


class Gestor_transacciones:
    def __init__(self, puerto_clientes=None, puerto_rmc=None, puerto_cancelacion=None):
        self.puerto_clientes = str(puerto_clientes or GT_PORT_CLI)
        self.puerto_rmc = str(puerto_rmc or GT_PORT_RMC)
        self.puerto_cancelacion = str(puerto_cancelacion or GT_PORT_CANC)
        self.push_socket = None     # servicio de cancelacion
        self.contexto = zmq.Context()
        self.router_socket = None   # clientes
        self.dealer_socket = None   # RMC
        self.poller = None
        self._parar = False

    def iniciar_sockets(self):
        self.router_socket = self.contexto.socket(zmq.ROUTER)
        self.router_socket.setsockopt(zmq.LINGER, 0)
        self.router_socket.bind(f"tcp://*:{self.puerto_clientes}")
        self.dealer_socket = self.contexto.socket(zmq.DEALER)
        self.dealer_socket.setsockopt(zmq.LINGER, 0)
        self.dealer_socket.bind(f"tcp://*:{self.puerto_rmc}")
        self.push_socket = self.contexto.socket(zmq.PUSH)
        self.push_socket.setsockopt(zmq.LINGER, 0)
        self.push_socket.bind(f"tcp://*:{self.puerto_cancelacion}")

    def iniciar_poller(self):
        self.poller = zmq.Poller()
        self.poller.register(self.dealer_socket, zmq.POLLIN)
        self.poller.register(self.router_socket, zmq.POLLIN)

    @staticmethod
    def _parsear(frames):
        """Peticion (dict) del ultimo frame, o None si no es legible."""
        if len(frames) != 3 or frames[1] != b"":
            return None   # un REQ siempre envia [identidad, vacio, cuerpo]
        try:
            p = msjs.decodificar(frames[-1].decode("utf-8"))
            p["tipo"], p["id_cliente"], p["uuid"]
            return p
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            return None

    def _responder_error(self, frames, texto):
        """Contesta al cliente directamente (misma identidad) sin pasar por el RMC."""
        rta = msjs.rta_error("", texto)
        self.router_socket.send_multipart(frames[:2] + [msjs.codificar(rta).encode("utf-8")])

    def _de_cliente_a_rmc(self):
        frames = self.router_socket.recv_multipart()
        peticion = self._parsear(frames)
        if peticion is None:
            log("GT", "peticion ilegible descartada, se responde ERROR al cliente")
            self._responder_error(frames, "peticion mal formada")
            return
        tipo, cliente, uuid = peticion["tipo"], peticion["id_cliente"], str(peticion["uuid"])[:8]
        if tipo == "CANCELACION":
            self._cancelacion(frames, peticion, cliente, uuid)
            return
        try:
            self.dealer_socket.send_multipart(frames, flags=zmq.NOBLOCK)
            log("GT", f"cliente={cliente} {tipo} uuid={uuid} -> RMC")
        except zmq.Again:
            log("GT", f"no hay RMC disponible (uuid={uuid})")
            self._responder_error(frames, "RMC no disponible")

    @staticmethod
    def _datos_cancelacion_validos(peticion):
        datos = peticion.get("datos")
        if not isinstance(datos, dict):
            return False
        id_res, destino = datos.get("id_reserva"), datos.get("direccion_notificacion")
        return (isinstance(id_res, int) and not isinstance(id_res, bool)
                and isinstance(peticion["id_cliente"], int) and peticion["id_cliente"] > 0
                and isinstance(destino, str) and re.fullmatch(r"[\w.\-]+:\d{2,5}", destino) is not None)

    def _responder(self, frames, rta):
        self.router_socket.send_multipart(frames[:2] + [msjs.codificar(rta).encode("utf-8")])

    def _cancelacion(self, frames, peticion, cliente, uuid):
        """Canal asincrono: valida, encola el trabajo (PUSH) y responde ACK sin esperar el resultado."""
        uid = str(peticion["uuid"])
        if not self._datos_cancelacion_validos(peticion):
            log("GT", f"cliente={cliente} CANCELACION uuid={uuid} -> datos invalidos")
            self._responder(frames, msjs.crear_rta(
                'F', 'CA', 'D_I', {'razon': 'indique id_reserva y direccion_notificacion (ip:puerto)'}, uid))
            return
        try:
            self.push_socket.send_string(msjs.codificar(peticion), flags=zmq.NOBLOCK)
        except zmq.Again:
            log("GT", f"no hay servicio de cancelacion disponible (uuid={uuid})")
            self._responder(frames, msjs.rta_error(uid, "servicio de cancelacion no disponible"))
            return
        log("GT", f"cliente={cliente} CANCELACION uuid={uuid} -> ACK al cliente, trabajo -> cancelacion (PUSH)")
        self._responder(frames, msjs.crear_rta(
            'A', 'CA', None, {'razon': 'cancelacion recibida; se le notificara cuando se haga efectiva'}, uid))

    def _de_rmc_a_cliente(self):
        frames = self.dealer_socket.recv_multipart()
        try:
            rta = msjs.decodificar(frames[-1].decode("utf-8"))
            log("GT", f"{rta['estado']} uuid={str(rta['uuid'])[:8]} -> cliente")
        except (ValueError, KeyError, UnicodeDecodeError):
            log("GT", "respuesta ilegible del RMC reenviada")
        self.router_socket.send_multipart(frames)

    def iniciar_gestor(self):
        """Bloqueante hasta `detener()`."""
        log("GT", f"clientes en :{self.puerto_clientes}, RMC en :{self.puerto_rmc}")
        self._parar = False
        try:
            while not self._parar:
                sockets = dict(self.poller.poll(200))
                if sockets.get(self.dealer_socket) == zmq.POLLIN:
                    self._de_rmc_a_cliente()
                if sockets.get(self.router_socket) == zmq.POLLIN:
                    self._de_cliente_a_rmc()
        finally:
            self.router_socket.close(linger=0)
            self.dealer_socket.close(linger=0)
            self.push_socket.close(linger=0)
            self.contexto.term()  # espera a que los puertos queden liberados

    def detener(self):
        self._parar = True
