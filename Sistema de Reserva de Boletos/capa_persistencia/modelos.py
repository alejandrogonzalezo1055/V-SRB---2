"""Esquema de la base de datos (SQLAlchemy Core).

evento 1---N ocurrencia 1---N reserva. Cada ocurrencia lleva su propio cupo y el
CHECK `ck_cupo` impide que la BD acepte sobreventa aunque el codigo tenga un error.
"""
from sqlalchemy import (
    Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, Integer,
    MetaData, String, Table, UniqueConstraint, func,
)

metadata = MetaData()

evento = Table(
    "evento", metadata,
    Column("id", String(10), primary_key=True),
    Column("nombre", String(120), nullable=False),
    Column("tipo_evento", String(30), nullable=False),
)

ocurrencia = Table(
    "ocurrencia", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("evento_id", String(10), ForeignKey("evento.id"), nullable=False),
    Column("fecha", Date, nullable=False),
    Column("capacidad", Integer, nullable=False),
    Column("puestos_ocupados", Integer, nullable=False, server_default="0"),
    UniqueConstraint("evento_id", "fecha", name="uq_evento_fecha"),
    CheckConstraint("puestos_ocupados >= 0 AND puestos_ocupados <= capacidad", name="ck_cupo"),
)

reserva = Table(
    "reserva", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("uuid", String(64), unique=True),   # uuid de la peticion: hace idempotente el reintento
    Column("id_cliente", Integer, nullable=False),
    Column("ocurrencia_id", Integer, ForeignKey("ocurrencia.id"), nullable=False),
    Column("n_puestos", Integer, nullable=False),
    Column("estado", Boolean, nullable=False, server_default="true"),  # false = cancelada
    Column("creada", DateTime, nullable=False, server_default=func.now()),
)
