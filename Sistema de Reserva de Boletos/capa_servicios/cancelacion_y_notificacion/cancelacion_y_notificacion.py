"""Servicio de cancelacion y notificacion (canal asincrono).

Recibe del GT por PULL (los GT reparten por turnos entre las instancias), pide a persistencia
liberar los puestos y, cuando persistencia responde, notifica DIRECTAMENTE al cliente que lo pidio
(PUSH hacia el receptor PULL que el cliente indico en `direccion_notificacion`).
"""
from abc import ABC, abstractmethod

import zmq

import comun.mensajes as msjs
from comun.config import GT_PORT_CANC, HOST_SERV
from comun.log import log


class I_Servicio_cancelacion(ABC):
    @abstractmethod
    def eliminar_reserva(self, peticion):
        pass

    @abstractmethod
    def notificar_cliente(self, peticion, respuesta):
        pass


class Servicio_cancelacion(I_Servicio_cancelacion):
    def __init__(self, mensajero_persistencia):
        self.mensajero_persistencia = mensajero_persistencia
        self.ctx = zmq.Context()
        self._destinos = {}      # direccion -> socket PUSH (se reutiliza entre notificaciones)
        self._parar = False

    def eliminar_reserva(self, peticion):
        """Pide a persistencia cancelar la reserva (idempotente: repetirla no libera dos veces)."""
        return self.mensajero_persistencia.enviar(peticion)

    def notificar_cliente(self, peticion, respuesta):
        direccion = peticion["datos"]["direccion_notificacion"]
        datos = {"id_reserva": peticion["datos"]["id_reserva"], "resultado": respuesta["estado"],
                 "motivo": respuesta["motivo"], **{k: v for k, v in respuesta["datos"].items() if k != "id_reserva"}}
        notificacion = msjs.crear_req('N', peticion["id_cliente"], datos, peticion["uuid"])
        sock = self._destinos.get(direccion)
        if sock is None:
            sock = self.ctx.socket(zmq.PUSH)
            sock.setsockopt(zmq.LINGER, 2000)    # da tiempo a entregar si el cliente aun no conecto
            sock.setsockopt(zmq.SNDTIMEO, 1000)
            sock.connect(f"tcp://{direccion}")
            self._destinos[direccion] = sock
        try:
            sock.send_string(msjs.codificar(notificacion))
            log("CANC", f"cliente={peticion['id_cliente']} notificado en {direccion}: {datos['resultado']}")
        except zmq.Again:
            log("CANC", f"no se pudo notificar a {direccion} (cliente sin receptor)")

    def atender(self, peticion):
        uuid = str(peticion.get("uuid", ""))
        rta = self.eliminar_reserva(peticion)
        motivo = '' if rta['motivo'] == 'NULL' else f"/{rta['motivo']}"
        log("CANC", f"CANCELACION cliente={peticion.get('id_cliente')} uuid={uuid[:8]} -> {rta['estado']}{motivo}")
        self.notificar_cliente(peticion, rta)

    def iniciar(self, host=None, puerto=None):
        """Bloqueante: toma cancelaciones del GT (PULL conectado al PUSH del GT)."""
        sock = self.ctx.socket(zmq.PULL)
        sock.setsockopt(zmq.LINGER, 0)
        sock.connect(f"tcp://{host or HOST_SERV}:{puerto or GT_PORT_CANC}")
        log("CANC", "listo, conectado al GT")
        self._parar = False
        try:
            while not self._parar:
                if not sock.poll(200):
                    continue
                try:
                    self.atender(msjs.decodificar(sock.recv_string()))
                except Exception as e:  # noqa: BLE001  (una cancelacion mala no mata al servicio)
                    log("CANC", f"error al procesar una cancelacion: {e}")
        finally:
            sock.close()
            for s in self._destinos.values():
                s.close(linger=0)
            self.ctx.term()

    def detener(self):
        self._parar = True
