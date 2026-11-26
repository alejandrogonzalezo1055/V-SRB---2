"""Configuracion comun: hosts, puertos y timeouts leidos del .env de la raiz del codigo."""
import os
from pathlib import Path

import zmq
from dotenv import load_dotenv

# El .env vive junto a las carpetas de capas, sin importar desde donde se ejecute.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

HOST_CLI = os.getenv("HOST_CAPA_CONSUMIDORES")
HOST_SERV = os.getenv("HOST_CAPA_SERVICIOS")
HOST_PERS = os.getenv("HOST_CAPA_PERSISTENCIA")

GT_PORT_CLI = os.getenv("GT_PUERTO_CLIENTES", "5555")
GT_PORT_RMC = os.getenv("GT_PUERTO_RMC", "5556")
GT_PORT_CANC = os.getenv("GT_PUERTO_CANCELACION", "5557")

PS_PORT = os.getenv("PUERTO_SP", "5558")
DB_URL = os.getenv("DB_URL")
DB_REPLICA_URL = os.getenv("DB_REPLICA_URL")   # replica de solo lectura (opcional); sin ella, todo va al primario

# El cliente debe esperar mas que dos llamadas del RMC a persistencia (una RESERVA hace dos),
# para poder recibir el ERROR del RMC en vez de un timeout generico.
TIMEOUT_PERSISTENCIA_MS = int(os.getenv("TIMEOUT_PERSISTENCIA_MS", "2000"))
TIMEOUT_MS = int(os.getenv("TIMEOUT_MS", "6000"))
HILOS_PERSISTENCIA = int(os.getenv("HILOS_PERSISTENCIA", "4"))
REINTENTOS = int(os.getenv("REINTENTOS", "2"))   # reintentos del cliente (mismo uuid) tras un timeout


def time_out_sockets(socket, timeout_ms=None):
    """Timeout de recepcion/envio y LINGER 0 para que un socket cerrado no cuelgue el proceso."""
    t = TIMEOUT_MS if timeout_ms is None else timeout_ms
    socket.setsockopt(zmq.RCVTIMEO, t)
    socket.setsockopt(zmq.SNDTIMEO, t)
    socket.setsockopt(zmq.LINGER, 0)
