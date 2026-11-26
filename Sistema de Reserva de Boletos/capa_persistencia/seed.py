"""Carga de datos iniciales.

    python -m capa_persistencia.seed [--eventos 50] [--dobles 20] [--capacidad 100]
                                     [--mes 11] [--anio 2026] [--reset] [--verbose]

Determinista: E1..E{dobles} tienen dos ocurrencias (dias 5 y 19); el resto una (dia 12).
"""
import argparse
import sys
from datetime import date

from sqlalchemy import create_engine, func, insert, select

from comun.config import DB_URL
from capa_persistencia.modelos import evento, metadata, ocurrencia


def sembrar(engine, eventos, dobles, capacidad, mes, anio):
    if not 0 <= dobles <= eventos:
        raise ValueError("dobles debe estar entre 0 y eventos")
    filas_evento, filas_oc = [], []
    for i in range(1, eventos + 1):
        filas_evento.append({"id": f"E{i}", "nombre": f"Evento {i}",
                             "tipo_evento": "cultural" if i % 2 else "deportivo"})
        dias = (5, 19) if i <= dobles else (12,)
        for d in dias:
            filas_oc.append({"evento_id": f"E{i}", "fecha": date(anio, mes, d),
                             "capacidad": capacidad, "puestos_ocupados": 0})
    with engine.begin() as conn:
        conn.execute(insert(evento), filas_evento)
        conn.execute(insert(ocurrencia), filas_oc)


def sembrar_si_vacio(engine, eventos, dobles, capacidad, mes, anio):
    with engine.connect() as conn:
        if conn.execute(select(func.count()).select_from(evento)).scalar_one() > 0:
            raise RuntimeError("la BD ya tiene datos; use --reset para borrarlos y recargar")
    sembrar(engine, eventos, dobles, capacidad, mes, anio)


def main(argv=None):
    p = argparse.ArgumentParser(description="Carga los datos iniciales del SRB")
    p.add_argument("--eventos", type=int, default=50)
    p.add_argument("--dobles", type=int, default=20, help="eventos con dos ocurrencias")
    p.add_argument("--capacidad", type=int, default=100)
    p.add_argument("--mes", type=int, default=11)
    p.add_argument("--anio", type=int, default=2026)
    p.add_argument("--reset", action="store_true", help="borra todo y recarga")
    p.add_argument("--verbose", action="store_true", help="muestra el SQL")
    a = p.parse_args(argv)
    engine = create_engine(DB_URL, echo=a.verbose)
    if a.reset:
        metadata.drop_all(engine)
    metadata.create_all(engine)
    try:
        sembrar_si_vacio(engine, a.eventos, a.dobles, a.capacidad, a.mes, a.anio)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(f"Datos cargados: {a.eventos} eventos, {a.eventos + a.dobles} ocurrencias, "
          f"capacidad {a.capacidad}, mes {a.mes}/{a.anio}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
