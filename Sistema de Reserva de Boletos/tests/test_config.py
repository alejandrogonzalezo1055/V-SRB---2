from comun import config
from capa_servicios.servicio_RMC.servicio_rmc import Cliente_persistencia


def test_timeout_del_cliente_cubre_dos_llamadas_a_persistencia_con_margen():
    # una RESERVA hace dos llamadas a persistencia: el cliente debe poder recibir el ERROR del RMC
    assert config.TIMEOUT_MS >= 2 * config.TIMEOUT_PERSISTENCIA_MS + 1000


def test_el_rmc_usa_el_timeout_de_persistencia_por_defecto():
    assert Cliente_persistencia("127.0.0.1", "1").timeout_ms == config.TIMEOUT_PERSISTENCIA_MS
