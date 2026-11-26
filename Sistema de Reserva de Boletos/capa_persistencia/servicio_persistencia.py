"""Servicio de persistencia: unico proceso con acceso a la BD.

Patron: ROUTER (clientes: RMC y cancelacion) -> DEALER interno -> pool de hilos REP.
Cada hilo atiende una peticion a la vez, asi varias peticiones avanzan en paralelo y la
BD (UPDATE condicionado) garantiza que no haya sobreventa.
"""
import threading
from abc import ABC, abstractmethod

import zmq
from sqlalchemy.exc import DBAPIError

import capa_persistencia.repositorio as repo
from comun.config import HILOS_PERSISTENCIA, PS_PORT
from comun.log import log
from comun.mensajes import codificar, decodificar, rta_error


class I_Servicio_Persistencia(ABC):
    @abstractmethod
    def procesar(self, peticion):
        """Despacha una peticion (dict) y devuelve la respuesta (dict)."""


class Servicio_Persistencia(I_Servicio_Persistencia):
    def __init__(self, engine, puerto=None, hilos=None, engine_lectura=None):
        self.engine = engine                          # primario: escrituras y comprobaciones de cupo
        self.engine_lectura = engine_lectura          # replica (solo lectura) o None
        self.puerto = str(puerto or PS_PORT)
        self.hilos = hilos or HILOS_PERSISTENCIA
        self._ctx = zmq.Context()
        self._parar = threading.Event()

    # ---- logica ---------------------------------------------------------
    def procesar(self, peticion):
        if not isinstance(peticion, dict) or "tipo" not in peticion:
            return rta_error("", "peticion mal formada")
        uuid = str(peticion.get("uuid", ""))
        tipo = peticion["tipo"]
        datos = peticion.get("datos")
        if not isinstance(datos, dict):
            return rta_error(uuid, "falta el campo datos")
        if tipo == "CONSULTA":
            rta = self._leer(repo.consultar_eventos, uuid, datos)
        elif tipo == "CONSULTA_DISPONIBILIDAD":
            rta = repo.consultar_disponibilidad(self.engine, uuid, datos)
        elif tipo == "AGREGAR_RESERVA":
            rta = repo.agregar_reserva(self.engine, uuid, peticion.get("id_cliente"), datos)
        elif tipo == "MODIFICACION":
            rta = repo.modificar_reserva(self.engine, uuid, peticion.get("id_cliente"), datos)
        elif tipo == "CANCELACION":
            rta = repo.cancelar_reserva(self.engine, uuid, peticion.get("id_cliente"), datos)
        else:
            return rta_error(uuid, f"tipo no soportado por persistencia: {tipo}")
        log("PERS", f"{tipo} cliente={peticion.get('id_cliente')} uuid={uuid[:8]} -> {rta['estado']}"
                    f"{'/' + rta['motivo'] if rta['motivo'] != 'NULL' else ''}")
        return rta

    def _leer(self, operacion, uuid, datos):
        """Lectura que tolera cierto retraso (consulta de eventos): va a la replica; si la replica no
        responde, se lee del primario. Las comprobaciones de cupo y todas las escrituras NO pasan por aqui:
        usan siempre el primario, asi la replica nunca puede causar una sobreventa."""
        if self.engine_lectura is not None:
            try:
                return operacion(self.engine_lectura, uuid, datos)
            except DBAPIError as e:   # replica caida o inalcanzable
                log("PERS", f"replica no disponible, se lee del primario ({type(e.orig).__name__ if e.orig else 'error'})")
        return operacion(self.engine, uuid, datos)

    def _atender_texto(self, texto):
        """Siempre devuelve un texto JSON: un fallo interno nunca deja al RMC esperando."""
        try:
            peticion = decodificar(texto)
        except ValueError:
            log("PERS", "peticion ilegible (JSON invalido)")
            return codificar(rta_error("", "JSON invalido"))
        try:
            return codificar(self.procesar(peticion))
        except Exception as e:  # noqa: BLE001  (error de BD u otro: se informa, no se muere)
            uuid = peticion.get("uuid", "") if isinstance(peticion, dict) else ""
            log("PERS", f"error interno: {e}")
            return codificar(rta_error(str(uuid), "error interno de persistencia"))

    # ---- sockets --------------------------------------------------------
    def _trabajador(self):
        sock = self._ctx.socket(zmq.REP)
        sock.setsockopt(zmq.LINGER, 0)
        sock.connect(f"inproc://trabajadores-{self.puerto}")
        try:
            while not self._parar.is_set():
                if sock.poll(200):
                    partes = sock.recv_multipart()   # se lee todo: el REP siempre debe poder responder
                    try:
                        if len(partes) != 1:
                            raise ValueError("mensaje multipart")
                        respuesta = self._atender_texto(partes[0].decode("utf-8"))
                    except ValueError:   # multipart o bytes que no son UTF-8
                        log("PERS", "peticion ilegible (no es un unico frame UTF-8)")
                        respuesta = codificar(rta_error("", "peticion ilegible"))
                    sock.send_string(respuesta)
        finally:
            sock.close()

    def iniciar(self):
        """Bloqueante hasta `detener()`. ROUTER <-> DEALER interno con un bucle de poll propio."""
        frontend = self._ctx.socket(zmq.ROUTER)
        frontend.setsockopt(zmq.LINGER, 0)
        frontend.bind(f"tcp://*:{self.puerto}")
        backend = self._ctx.socket(zmq.DEALER)
        backend.setsockopt(zmq.LINGER, 0)
        backend.bind(f"inproc://trabajadores-{self.puerto}")
        poller = zmq.Poller()
        poller.register(frontend, zmq.POLLIN)
        poller.register(backend, zmq.POLLIN)
        hilos = [threading.Thread(target=self._trabajador, daemon=True) for _ in range(self.hilos)]
        for h in hilos:
            h.start()
        log("PERS", f"escuchando en tcp://*:{self.puerto} con {self.hilos} hilos")
        try:
            while not self._parar.is_set():
                listos = dict(poller.poll(200))
                if listos.get(frontend) == zmq.POLLIN:
                    backend.send_multipart(frontend.recv_multipart())
                if listos.get(backend) == zmq.POLLIN:
                    frontend.send_multipart(backend.recv_multipart())
        finally:
            self._parar.set()
            for h in hilos:
                h.join(timeout=2)
            frontend.close(linger=0)
            backend.close(linger=0)
            # destroy() espera a que el puerto quede realmente liberado (un reinicio inmediato
            # no debe fallar con "Address already in use").
            self._ctx.destroy(linger=0)

    def detener(self):
        """Idempotente y seguro entre hilos."""
        self._parar.set()
