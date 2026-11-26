from typing import TypedDict
import uuid
import json

# constantes
peticiones = {
    'CO':'CONSULTA',
    'CO_D':'CONSULTA_DISPONIBILIDAD',
    'R':'RESERVA',
    'A_R':'AGREGAR_RESERVA',
    'M':'MODIFICACION',
    'CA':'CANCELACION',
    'N':'NOTIFICACION',
    '-':'NULL'
}

estados_rta = {
    'E':'EXITO',
    'F':'FALLO',
    'ER':'ERROR',
    'A':'ACK'
}

motivos_rechazo = {
    'S_C': 'SIN_CUPO',
    'N_E': 'NO_EXISTE',
    'D_I': 'DATO_INVALIDO',
    'P_I':'PETICION_INVALIDA',
    'N_I': 'NO_IMPLEMENTADO',
    'N_A': 'NO_AUTORIZADO',
    '-':'NULL'
}

meses = ['enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre']

# peticiones
class Peticion(TypedDict):
    uuid: str
    tipo: str
    id_cliente: int
    datos: dict

class Respuesta(TypedDict):
    uuid: str
    estado: str
    peticion_realizada: str
    motivo: str
    datos: dict

# funciones
def crear_req(tipo, id_cliente, datos, uuid_peticion=None):
    """Arma una peticion. `tipo` es la abreviatura (ver `peticiones`).

    Genera un uuid nuevo solo si no se recibe uno; los servicios que reenvian
    una peticion conservan el uuid original.
    """
    if tipo not in peticiones:
        raise ValueError(f"tipo de peticion desconocido: {tipo}")
    if not isinstance(id_cliente, int) or id_cliente <= 0:
        raise ValueError("id_cliente debe ser un entero positivo")
    peticion: Peticion = {
        'uuid': uuid_peticion if uuid_peticion else str(uuid.uuid4()),
        'tipo': peticiones[tipo],
        'id_cliente': id_cliente,
        'datos': datos,
    }
    return peticion


def crear_rta(estado, peticion_realizada, motivo, datos, uuid_peticion):
    """Arma una respuesta con el MISMO uuid de la peticion que responde.

    `motivo=None` equivale a '-' (sin motivo, p. ej. en un EXITO).
    """
    if motivo is None:
        motivo = '-'
    if estado not in estados_rta:
        raise ValueError(f"estado desconocido: {estado}")
    if motivo not in motivos_rechazo:
        raise ValueError(f"motivo desconocido: {motivo}")
    if peticion_realizada not in peticiones:
        raise ValueError(f"peticion desconocida: {peticion_realizada}")
    respuesta: Respuesta = {
        'uuid': uuid_peticion,
        'estado': estados_rta[estado],
        'peticion_realizada': peticiones[peticion_realizada],
        'motivo': motivos_rechazo[motivo],
        'datos': datos if datos is not None else {},
    }
    return respuesta


def rta_error(uuid_peticion, texto):
    """Respuesta de ERROR del sistema (peticion ilegible, servicio caido, etc.)."""
    return crear_rta('ER', '-', 'P_I', {'razon': texto}, uuid_peticion)


def codificar(peticion):
    return json.dumps(peticion)


def decodificar(peticion):
    return json.loads(peticion)
