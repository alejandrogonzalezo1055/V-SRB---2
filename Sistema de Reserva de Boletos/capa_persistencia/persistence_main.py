"""Punto de entrada de la capa de persistencia:  python -m capa_persistencia.persistence_main"""
import argparse

from sqlalchemy import create_engine

from comun.config import DB_REPLICA_URL, DB_URL
from capa_persistencia.servicio_persistencia import Servicio_Persistencia


def main():
    p = argparse.ArgumentParser(description="Servicio de persistencia del SRB")
    p.add_argument("--verbose", action="store_true", help="muestra el SQL de SQLAlchemy")
    a = p.parse_args()
    engine = create_engine(DB_URL, echo=a.verbose, pool_size=10, max_overflow=10, pool_pre_ping=True)
    lectura = None
    if DB_REPLICA_URL:
        lectura = create_engine(DB_REPLICA_URL, echo=a.verbose, pool_size=10, max_overflow=10, pool_pre_ping=True,
                                connect_args={"connect_timeout": 2})
        print(f"Replica de lectura: {DB_REPLICA_URL.split('@')[-1]} (las consultas de eventos se leen de ahi)")
    else:
        print("Sin replica (DB_REPLICA_URL vacio): todas las operaciones van al primario")
    try:
        Servicio_Persistencia(engine, engine_lectura=lectura).iniciar()
    except KeyboardInterrupt:
        print("\npersistencia detenida")


if __name__ == "__main__":
    main()
