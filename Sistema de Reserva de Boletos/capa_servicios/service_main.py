"""Punto de entrada de la capa de servicios.

    python -m capa_servicios.service_main gt          # Gestor de Transacciones
    python -m capa_servicios.service_main rmc         # Servicio RMC
    python -m capa_servicios.service_main cancelacion # cancelacion y notificacion
"""
import argparse
import sys

from comun.log import log


def main(argv=None):
    p = argparse.ArgumentParser(description="Arranca un proceso de la capa de servicios del SRB")
    p.add_argument("proceso", choices=["gt", "rmc", "cancelacion"])
    a = p.parse_args(argv)
    try:
        if a.proceso == "gt":
            from capa_servicios.gestor_transacciones.gestor_de_transacciones import Gestor_transacciones
            gt = Gestor_transacciones()
            gt.iniciar_sockets()
            gt.iniciar_poller()
            gt.iniciar_gestor()
        elif a.proceso == "rmc":
            from capa_servicios.servicio_RMC.servicio_rmc import Cliente_persistencia, Servicio_RMC
            Servicio_RMC(Cliente_persistencia()).iniciar()
        else:
            from capa_servicios.servicio_RMC.servicio_rmc import Cliente_persistencia
            from capa_servicios.cancelacion_y_notificacion.cancelacion_y_notificacion import Servicio_cancelacion
            Servicio_cancelacion(Cliente_persistencia()).iniciar()
    except KeyboardInterrupt:
        log(a.proceso.upper(), "detenido")
    return 0


if __name__ == "__main__":
    sys.exit(main())
