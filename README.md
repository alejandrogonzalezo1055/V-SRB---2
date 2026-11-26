# Sistema de Reserva de Boletos

Proyecto de **Introducción a los Sistemas Distribuidos** (periodo 2026-30), Departamento de Ingeniería de Sistemas, Pontificia Universidad Javeriana.

Sistema distribuido de tres capas para reservar boletos de eventos universitarios. La comunicación entre procesos usa [ZeroMQ](https://zeromq.org/); la persistencia usa PostgreSQL con SQLAlchemy Core.

**Autores:** Solon Losada Cadena, Juan Pablo Peña Bernal, Alejandro González Ochoa
**Docente:** Ricardo Hjalmar González García

> **Estado:** entrega 2 implementada: consulta, reserva, modificación y cancelación asíncrona con notificación; réplicas de RMC y de cancelación; tolerancia a fallos con supervisor. Incluye réplica de lectura de PostgreSQL. Ver [Estado](#estado).

---

## Arquitectura

| Capa | Máquina | Procesos |
|---|---|---|
| Consumidores | 1 | Cliente (interactivo, por casos o generador de carga) |
| Servicios de negocio | 2 | Gestor de Transacciones (GT), servicio RMC (réplicas), servicio de cancelación y notificación (réplicas), supervisor |
| Persistencia | 3 | Servicio de persistencia, PostgreSQL primario + réplica en streaming y pgAdmin (en Docker) |

```
Cliente (REQ) ─► GT (ROUTER :5555) ─► GT (DEALER :5556) ─► RMC (REP)
                                                              │ REQ
                                                              ▼
                       PostgreSQL ◄── Persistencia (ROUTER :5558 + hilos REP)
```

- **Cliente ⇄ GT:** `REQ/ROUTER`. Cada petición lleva un `uuid` y la respuesta vuelve con el mismo.
- **GT ⇄ RMC:** `ROUTER/DEALER`. El RMC se conecta al GT, así que se pueden agregar réplicas sin tocar el GT.
- **RMC ⇄ Persistencia:** `REQ/ROUTER`. Persistencia atiende varias peticiones en paralelo con un pool de hilos.
- **Cancelación (asíncrona):** el cliente envía `CANCELACION` con la dirección de su receptor (`ip:puerto`). El GT responde `ACK` al instante y manda el trabajo por `PUSH/PULL` (round-robin entre las réplicas del servicio de cancelación). Ese servicio pide a persistencia marcar la reserva como cancelada y liberar los puestos y, **después**, notifica directamente al cliente (`PUSH` → `PULL` del cliente).
- **Modificación (síncrona):** `MODIFICACION` cambia la cantidad **o** la fecha (otra ocurrencia del mismo evento y mes, misma cantidad) en **una sola transacción**; si no hay cupo, se rechaza y la reserva queda como estaba. Solo el dueño de la reserva puede modificar o cancelar (`NO_AUTORIZADO`).
- **Replicación de la base de datos:** PostgreSQL primario (`:5432`) + réplica en streaming asíncrona de solo lectura (`:5433`), ambas en Docker en la máquina 3. Persistencia lee las **consultas de eventos** de la réplica (si la réplica no responde, del primario). Las **reservas, modificaciones, cancelaciones y la comprobación de cupo** van siempre al primario, así que la réplica nunca puede causar una sobreventa. Como es asíncrona, una consulta puede mostrar datos con milisegundos de retraso. La promoción de la réplica a primario es manual y no está automatizada.
- **Escalabilidad:** varias réplicas de RMC y de cancelación se conectan al GT (que hace `bind`); el `DEALER`/`PUSH` del GT reparte el trabajo entre las que estén vivas. Persistencia atiende con `HILOS_PERSISTENCIA` hilos.
- **Tolerancia a fallos (modelo fail-stop):** el `supervisor` lanza GT y réplicas, detecta que un proceso murió y lanza una copia. Las réplicas se reconectan solas al GT. El cliente, tras un timeout, **reintenta con el mismo `uuid`** y persistencia es **idempotente por `uuid`** (una reserva ya confirmada no se duplica; repetir una modificación o cancelación no hace nada nuevo). Así la caída es transparente: la operación continúa donde iba.
- **Reserva sin sobreventa:** persistencia ejecuta `UPDATE … WHERE puestos_ocupados + n <= capacidad` y revisa cuántas filas cambió; además la BD tiene un `CHECK` que rechaza cualquier sobreventa.

### Protocolo de mensajes (JSON, `comun/mensajes.py`)

```json
{ "uuid": "…", "tipo": "RESERVA", "id_cliente": 1,
  "datos": { "id_evento": "E25", "fecha": "2026/11/12", "n_asientos": 3 } }
```
```json
{ "uuid": "…", "estado": "EXITO", "peticion_realizada": "RESERVA", "motivo": "NULL",
  "datos": { "id_reserva": 17 } }
```

| Campo | Valores |
|---|---|
| `tipo` | `CONSULTA`, `CONSULTA_DISPONIBILIDAD`, `RESERVA`, `AGREGAR_RESERVA`, `MODIFICACION`, `CANCELACION`, `NOTIFICACION` |
| `datos` de `MODIFICACION` | `{id_reserva, n_asientos}` o `{id_reserva, fecha}` (exactamente uno) |
| `datos` de `CANCELACION` | `{id_reserva, direccion_notificacion: "ip:puerto"}` |
| `estado` | `EXITO`, `FALLO` (la operación no se pudo hacer), `ERROR` (falla del sistema), `ACK` |
| `motivo` | `SIN_CUPO`, `NO_EXISTE`, `DATO_INVALIDO`, `NO_AUTORIZADO`, `PETICION_INVALIDA`, `NO_IMPLEMENTADO`, `NULL` |

Fechas siempre como texto `AAAA/MM/DD`. El mes se envía en español y minúscula (`noviembre`).

---

## Datos iniciales

50 eventos (`E1`…`E50`) en **noviembre de 2026**, 100 asientos por ocurrencia. `E1`…`E20` tienen **dos** ocurrencias (días 5 y 19); `E21`…`E50` tienen **una** (día 12). En total 70 ocurrencias. Los tipos alternan cultural/deportivo.

Para reiniciar la BD (o cargar otros números que dicte el profesor):

```bash
.venv/bin/python -m capa_persistencia.seed --reset                          # 50 eventos, 20 dobles, 100 asientos
.venv/bin/python -m capa_persistencia.seed --reset --eventos 10 --dobles 4 --capacidad 50
```

---

## Despliegue en tres máquinas

**Requisitos por máquina:** Python 3.10 o superior (`python3 --version`), `python3-venv` (`sudo apt install python3-venv` en Ubuntu) y, solo en la máquina 3, Docker.

Las IPs están en `.env` (copia de `.env.example`). Valores actuales: consumidores `10.43.99.230`, servicios `10.43.99.217`, persistencia `10.43.99.253`.

**En cada máquina**, desde la carpeta `Sistema de Reserva de Boletos/` del repositorio, con el mismo commit:

```bash
git pull
./scripts/deploy.sh persistencia    # máquina 3
./scripts/deploy.sh servicios       # máquina 2
./scripts/deploy.sh consumidores    # máquina 1
```

`deploy.sh` crea el entorno virtual `.venv`, instala `requirements.txt` (versiones fijas, iguales en las tres máquinas), valida que `.env` tenga las tres IPs y que la IP de la capa pertenezca a esa máquina. En la máquina de persistencia también levanta Docker (PostgreSQL + pgAdmin). Docker debe estar instalado y activo (`sudo systemctl start docker`).

### Borrar todo en una máquina (`undeploy.sh`)

Es el inverso de `deploy.sh`. Útil para dejar una máquina limpia antes de volver a desplegar:

```bash
./scripts/undeploy.sh persistencia --simular     # primero ver qué haría, sin borrar nada
./scripts/undeploy.sh persistencia               # pide confirmación (escribir 'si')
./scripts/undeploy.sh todo --si --con-env --con-resultados --imagenes    # borra TODO sin preguntar
```

Qué borra: los procesos del SRB en ejecución, `.venv`, las cachés de Python y los temporales de las pruebas. En `persistencia` y `todo` también detiene y **borra los contenedores y los volúmenes de Docker (se pierden todos los datos de la BD)**. Opciones: `--con-env` (borra `.env`), `--con-resultados` (borra las mediciones), `--imagenes` (borra las imágenes de Docker), `--si` (no pregunta), `--simular` (solo muestra). No toca el código fuente ni nada fuera de la carpeta del proyecto. Después: `./scripts/deploy.sh <capa>`.

### Orden de arranque (cada proceso en su terminal)

```bash
# Máquina 3: persistencia
.venv/bin/python -m capa_persistencia.seed --reset
.venv/bin/python -m capa_persistencia.persistence_main

# Máquina 2: servicios. El supervisor lanza el GT, 3 réplicas de RMC y 2 de cancelación,
# y relanza cualquiera que muera (modelo fail-stop)
.venv/bin/python -m capa_servicios.supervisor --rmc 3 --cancelacion 2
# (alternativa a mano, un proceso por terminal, sin tolerancia a fallos:
#  .venv/bin/python -m capa_servicios.service_main gt | rmc | cancelacion)

# Verificar que las capas se ven entre sí (desde cualquier máquina)
.venv/bin/python scripts/verificar_red.py

# Máquina 1: cliente
.venv/bin/python -m capa_consumidores.consumers_main
```

Cada proceso imprime una línea por operación (hora, proceso, cliente, tipo, `uuid`, resultado).

Timeouts (en `.env`): `TIMEOUT_PERSISTENCIA_MS=2000` (RMC → persistencia) y `TIMEOUT_MS=6000` (cliente → GT). El del cliente debe ser mayor que dos veces el de persistencia, porque una reserva hace dos llamadas; así, si persistencia cae, el cliente recibe `Error: persistencia no responde` y no un timeout genérico.

### Si algo falla

| Síntoma | Causa probable |
|---|---|
| `verificar_red.py` marca `Connection refused` | El proceso de esa capa no está corriendo, o el firewall bloquea el puerto (5555, 5556, 5558). |
| El cliente dice `Error: sin respuesta del GT` | El GT no está arriba o `HOST_CAPA_SERVICIOS` en `.env` es incorrecto. |
| El cliente dice `persistencia no responde` | La capa 3 no está arriba, o `HOST_CAPA_PERSISTENCIA` es incorrecta. |
| `Address already in use` | Quedó un proceso viejo: `pkill -f capa_` y reintentar. |
| `column reserva.uuid does not exist` | La BD es de la entrega 1: `python -m capa_persistencia.seed --reset` (recrea las tablas con la columna nueva). |
| La cancelación hace ACK pero no llega la notificación | La máquina 2 no alcanza `IP-cliente:puerto-notif` (firewall o `HOST_CAPA_CONSUMIDORES` incorrecta). |
| `password authentication failed` | Cambió la contraseña en `compose.yaml` con un volumen viejo: `docker compose down -v` y volver a levantar. |
| `deploy.sh` dice "no es una IP de esta maquina" | El `.env` de esa máquina tiene una IP de su capa que no le pertenece (copiado de otra red). Corríjalo; las tres IPs deben ser iguales en las tres máquinas. `SRB_SIN_CHECK_IP=1` omite la verificación solo para pruebas locales. |
| `.env` distinto entre máquinas | `.env` no se versiona: ajústelo en cada máquina. Se parte de `.env.example`. |

---

## Uso del cliente

Sin argumentos: modo interactivo, que pregunta los datos.

```bash
.venv/bin/python -m capa_consumidores.consumers_main
```

Con subcomandos (una operación con datos específicos):

```bash
# consultar los eventos de un mes
.venv/bin/python -m capa_consumidores.consumers_main consultar --mes noviembre

# reservar con datos específicos (E25 tiene una sola ocurrencia: el 12; E5 tiene el 5 y el 19)
.venv/bin/python -m capa_consumidores.consumers_main reservar --evento E25 --fecha 2026/11/12 --asientos 3 --cliente 2

# modificar: cantidad O fecha (otra ocurrencia del mismo evento), una sola cosa por petición
.venv/bin/python -m capa_consumidores.consumers_main modificar --reserva 1 --asientos 5
.venv/bin/python -m capa_consumidores.consumers_main modificar --reserva 1 --fecha 2026/11/19

# cancelar: responde ACK al instante y luego imprime la notificación (espera hasta --espera segundos).
# --host-notif es la IP de la MÁQUINA 1 vista desde la máquina 2 (por defecto, HOST_CAPA_CONSUMIDORES del .env)
.venv/bin/python -m capa_consumidores.consumers_main cancelar --reserva 1 --puerto-notif 6001

# casos predefinidos: basico, sin_cupo, inexistente, modificar, cancelacion
.venv/bin/python -m capa_consumidores.consumers_main caso basico

# generador de carga (mínimo 20 solicitudes por minuto); el resto de porcentajes son reservas.
# --segundos mide por tiempo; --csv escribe una fila por operación
.venv/bin/python -m capa_consumidores.consumers_main carga --por-min 20 --pct-consulta 50 --pct-modif 20 --pct-cancel 10 --minutos 1 --csv ops.csv
```

Opciones comunes: `--cliente N` (id del cliente, por defecto 1), `--json` (imprime la respuesta cruda), `--gt HOST:PUERTO` (apunta a otro GT sin editar `.env`).

El programa termina con código 0 si la operación tuvo éxito y 1 si fue rechazada o falló.

---

## Prueba de la etapa 2 con procesos reales

```bash
.venv/bin/python scripts/e2e_etapa2.py      # 19 comprobaciones contra la BD real, ~1 min
```

Levanta persistencia y el supervisor y ejecuta clientes como procesos independientes: reservar, modificar (cantidad y fecha), rechazos, cancelación con notificación, caída de una y de todas las réplicas de cancelación, y 4 clientes concurrentes. Debe terminar con `19/19 comprobaciones OK`. Si falla de inmediato diciendo que hay puertos en uso, quedó algo corriendo: `pkill -f capa_`.

## Tolerancia a fallos y escalabilidad (demostración)

```bash
.venv/bin/python scripts/demo_fallos.py
```

Arranca persistencia y el supervisor, envía 24 reservas y, a mitad de camino, mata con `kill -9` el GT (dos veces) y un RMC. Al final comprueba en la BD que las 24 reservas se guardaron **una sola vez**. Las operaciones afectadas tardan ~2.5 s (timeout + reintento); el resto ~20 ms.

En vivo, con el sistema corriendo: `pkill -9 -f "service_main rmc"` (o `... gt`) y seguir enviando operaciones; el supervisor imprime `murio ... lanzando una copia`.

## Mediciones de rendimiento

```bash
.venv/bin/python scripts/benchmark.py --todo --segundos 120 --por-min 6000      # las 6 combinaciones
.venv/bin/python scripts/benchmark.py --arq escalable --clientes 5 --segundos 120
```

*Base:* 1 GT, 1 RMC, 1 cancelación, persistencia con 1 hilo. *Escalable:* 1 GT, 4 RMC, 2 cancelaciones, persistencia con 8 hilos. Escenarios del enunciado: 3 clientes (2 solo consultan + 1 consulta y reserva), 5 (3 + 2 reservan y modifican) y 8 (4 + 4). Cada cliente es un proceso. Resultados en `Sistema de Reserva de Boletos/resultados/` (un CSV por cliente y `resumen.json`).

---

## Replicación de PostgreSQL

Requisito (una sola vez, máquina 3): el volumen del primario debe crearse con el usuario de replicación. Si venían de una versión anterior, recréenlo (**borra los datos**; luego `seed --reset`):

```bash
docker compose down -v && docker compose up -d
docker exec srb-postgres createdb -U srb srb_test       # solo para las pruebas automáticas
```

Y en el `.env` de la **máquina 3** debe estar `DB_REPLICA_URL=postgresql+psycopg://srb:srb@localhost:5433/srb` (viene en `.env.example`; si el `.env` es anterior, agréguenla). Al arrancar, `persistence_main` imprime `Replica de lectura: ...`; si no lo imprime, no usa la réplica.

Cómo comprobar que funciona:

```bash
# el primario ve a la réplica conectada en streaming
docker exec srb-postgres psql -U srb -d srb -c "select client_addr, state, sync_state from pg_stat_replication"
# la réplica está en modo solo lectura (debe imprimir t)
docker exec srb-postgres-replica psql -U srb -d srb -tc "select pg_is_in_recovery()"
# hacer una consulta y ver que la réplica la sirvió: seq_scan de evento/ocurrencia sube en la réplica
docker exec srb-postgres-replica psql -U srb -d srb -c "select relname, seq_scan from pg_stat_user_tables"
# tolerancia a la caída de la réplica: detenerla y consultar; persistencia imprime "replica no disponible, se lee del primario"
docker stop srb-postgres-replica      # ... consumers_main consultar --mes noviembre  (sigue funcionando) ...
docker start srb-postgres-replica
```

---

## Ver la base de datos (antes y después de cada petición)

**pgAdmin (interfaz gráfica):** en la máquina 3 abra <http://localhost:5050>. El servidor `SRB (PostgreSQL)` ya está configurado; la primera vez pide la contraseña de la BD: `srb` (marque "Save password"). Navegue a `srb` → `Schemas` → `public` → `Tables` y use *View/Edit Data → All Rows* sobre `ocurrencia` y `reserva`, o abra *Tools → Query Tool* con consultas como:

```sql
-- Ocupación de un evento
select evento_id, fecha, capacidad, puestos_ocupados from ocurrencia where evento_id = 'E25' order by fecha;
-- Reservas hechas
select r.id, r.id_cliente, o.evento_id, o.fecha, r.n_puestos, r.estado from reserva r join ocurrencia o on o.id = r.ocurrencia_id order by r.id;
-- Control de integridad: ocupados nunca supera la capacidad
select count(*) from ocurrencia where puestos_ocupados > capacidad;   -- debe ser 0
```

**Terminal (equivalente):** `docker exec -it srb-postgres psql -U srb -d srb` y las mismas consultas.

pgAdmin y PostgreSQL solo escuchan en `127.0.0.1` de la máquina 3: ábralos desde esa máquina.

---

## Prueba local (una sola máquina)

```bash
cd "Sistema de Reserva de Boletos"
./scripts/deploy.sh persistencia      # una vez
./scripts/e2e_local.sh                # BD + persistencia + GT + RMC + cliente, muestra la BD antes y después
.venv/bin/pytest -q                   # 90 pruebas (necesita la réplica en :5433 y la BD de pruebas: ver abajo)
```

Las pruebas usan la base `srb_test` en el mismo PostgreSQL. Se crea una vez con `docker exec srb-postgres createdb -U srb srb_test`.

---

## Estructura

```
.
├── CONTEXTO.md                      # estado del proyecto y decisiones de diseño
├── CORRECCION_DIAGRAMAS.md          # cambios pendientes en los diagramas del informe
├── EnunciadoProyecto_sdp_Reserva de boletas.pdf
├── Informe proyecto/                # informe LaTeX y diagramas
├── docs/superpowers/plans/          # plan de implementación de la etapa 1
├── Guias/  ZeroMQ examples/
└── Sistema de Reserva de Boletos/
    ├── .env.example  requirements.txt  compose.yaml  pgadmin/servers.json
    ├── comun/                       # mensajes.py, config.py, log.py
    ├── capa_consumidores/           # cliente.py, casos.py, carga_clientes.py, consumers_main.py
    ├── capa_servicios/              # service_main.py, gestor_transacciones/, servicio_RMC/
    ├── capa_persistencia/           # modelos.py, repositorio.py, seed.py, servicio_persistencia.py, persistence_main.py
    ├── scripts/                     # deploy.sh, verificar_red.py, e2e_local.sh
    └── tests/
```

---

## Estado

| Componente | Estado |
|---|---|
| Cliente: consulta, reserva, modo interactivo, casos, `--json` | Implementado y probado |
| Generador de carga (tasa y mezcla parametrizables) | Implementado y probado |
| GT (canal síncrono ROUTER/DEALER) | Implementado y probado |
| RMC: consulta y reserva | Implementado y probado |
| Persistencia (ROUTER + hilos, SQLAlchemy Core, PostgreSQL) y seed | Implementado y probado |
| pgAdmin, scripts de despliegue y verificación de red | Implementado |
| Prueba en una máquina | Hecha (`e2e_local.sh`, 90 pruebas, `e2e_etapa2.py` con procesos reales) |
| Prueba en las tres máquinas | **Pendiente: hacerla con el procedimiento de arriba** |
| Modificación de reserva (cantidad o fecha, atómica, solo el dueño) | Implementado y probado |
| Cancelación asíncrona (ACK + PUSH/PULL) y notificación al cliente | Implementado y probado |
| Réplicas de RMC y de cancelación, supervisor, reintento con `uuid` e idempotencia | Implementado y probado (`demo_fallos.py`) |
| Réplica de lectura de PostgreSQL (streaming asíncrono) con vuelta al primario | Implementado y probado (6 pruebas + e2e con la réplica activa) |
| Promoción automática de la réplica (failover de BD) | **No implementado** (la promoción es manual y no se probó) |
| Mediciones (`benchmark.py`) e informe de rendimiento | Hechas en una sola máquina; ver `Informe proyecto/Informe Rendimiento.tex` |
