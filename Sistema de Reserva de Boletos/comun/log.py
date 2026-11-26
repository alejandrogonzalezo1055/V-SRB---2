"""Salida por consola (stderr, para no mezclarse con los datos de stdout):
una linea por operacion, `[HH:MM:SS] PROCESO texto`."""
import sys
from datetime import datetime


def log(proceso, texto):
    print(f"[{datetime.now():%H:%M:%S}] {proceso:<5} {texto}", file=sys.stderr, flush=True)
