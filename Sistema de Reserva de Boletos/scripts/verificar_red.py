"""Comprueba desde ESTA maquina que se alcanzan los puertos de las otras capas.

    .venv/bin/python scripts/verificar_red.py

Sale con codigo 0 si todo responde. Cada puerto solo responde si el proceso
correspondiente esta corriendo: arranque primero persistencia, GT y RMC.
"""
import os
import socket
import sys

RAIZ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
VENV_PY = os.path.join(RAIZ, ".venv", "bin", "python")

# Las dependencias estan en .venv: si se ejecuto con el Python del sistema, se relanza con el del .venv.
try:
    import dotenv  # noqa: F401
except ModuleNotFoundError:
    if os.path.exists(VENV_PY) and os.path.realpath(sys.prefix) != os.path.realpath(os.path.join(RAIZ, ".venv")):
        os.execv(VENV_PY, [VENV_PY] + sys.argv)
    sys.exit("Faltan dependencias. Ejecute ./scripts/deploy.sh <capa> y luego:  .venv/bin/python scripts/verificar_red.py")

sys.path.insert(0, RAIZ)
from comun import config  # noqa: E402

OBJETIVOS = [
    ("Cliente -> GT (REQ/ROUTER)", config.HOST_SERV, config.GT_PORT_CLI),
    ("RMC -> GT (REP/DEALER)", config.HOST_SERV, config.GT_PORT_RMC),
    ("Cancelacion -> GT (PULL/PUSH)", config.HOST_SERV, config.GT_PORT_CANC),
    ("RMC -> Persistencia (REQ/ROUTER)", config.HOST_PERS, config.PS_PORT),
]


def alcanzable(host, puerto, timeout=2.0):
    try:
        with socket.create_connection((host, int(puerto)), timeout=timeout):
            return True, ""
    except OSError as e:
        return False, e.strerror or str(e)


def main():
    todo_ok = True
    print(f"{'Conexion':<36}{'Destino':<24}Estado")
    for nombre, host, puerto in OBJETIVOS:
        ok, motivo = alcanzable(host, puerto)
        todo_ok &= ok
        print(f"{nombre:<36}{f'{host}:{puerto}':<24}{'OK' if ok else 'FALLA (' + motivo + ')'}")
    return 0 if todo_ok else 1


if __name__ == "__main__":
    sys.exit(main())
