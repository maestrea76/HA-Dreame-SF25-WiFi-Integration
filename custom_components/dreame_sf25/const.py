"""Constantes de la integracion Dreame SF25."""
from __future__ import annotations

from typing import Final

DOMAIN: Final = "dreame_sf25"

# Config entry
CONF_REGION: Final = "region"
CONF_DID: Final = "did"
CONF_AUTO_MODES: Final = "auto_modes"
CONF_SAFETY_STOP: Final = "safety_stop"
CONF_VIRTUAL_MODES: Final = "virtual_modes"

DEFAULT_REGION: Final = "eu"
REGIONS: Final = ["eu", "cn", "us", "ru", "sg", "kr"]

# Modelo objetivo
TARGET_MODEL: Final = "dreame.fwd.u2527"

# Intervalos de sondeo (segundos):
#  - SCAN_INTERVAL_POLL: sin push MQTT (o si se cae) -> sondeo frecuente.
#  - SCAN_INTERVAL_PUSH: con push vivo -> el sondeo es solo red de seguridad.
SCAN_INTERVAL_POLL: Final = 30
SCAN_INTERVAL_PUSH: Final = 300

# --- Endpoints / secretos del protocolo de la app Dreamehome ---
PORT: Final = "13267"
PWD_SALT: Final = "RAylYC%fmSKp7%Tq"
USER_AGENT: Final = "Dreame_Smarthome/2.1.9 (iPhone; iOS 18.4.1; Scale/3.00)"
BASIC_AUTH: Final = "Basic ZHJlYW1lX2FwcHYxOkFQXmR2QHpAU1FZVnhOODg="
DEFAULT_TENANT: Final = "000000"

# --- Propiedades MIoT descubiertas (siid, piid) ---
# Confirmadas por observacion en vivo:
PROP_REMAINING_TIME: Final = (2, 11)   # minutos restantes de ciclo
PROP_ENERGY_WH: Final = (3, 14)        # energia acumulada en Wh (crudos)
PROP_HUMIDITY: Final = (3, 2)          # humedad relativa % (sube a ~100 al parar el secado)
PROP_TEMPERATURE: Final = (3, 3)       # temperatura de camara C (se enfria al parar)
PROP_CARBON_FILTER_PCT: Final = (4, 3)   # % de vida restante del filtro de carbon (82)
PROP_CARBON_FILTER_DAYS: Final = (4, 4)  # dias restantes para limpiar filtro de carbon (147)

# Estado del ciclo (2.1, enum) y flag marcha/pausa (2.10). Van unidos.
PROP_STATUS: Final = (2, 1)             # estado nativo (enum), ver STATUS_MAP
PROP_RUNNING: Final = (2, 10)           # tri-estado: -1=apagado, 0=pausa, 1=marcha
PROP_PROGRAM: Final = (2, 3)            # programa activo; ver PROGRAM_MAP

# Estado nativo del aparato (PROP_STATUS = 2.1). Se ira completando.
STATUS_MAP: Final = {
    1: "working",     # triturando/limpiando activamente
    2: "standby",     # en espera (sin triturar ni limpiar)
    3: "suspended",   # modo suspension (sleep)
}

# Programa (PROP_PROGRAM = 2.3). Escribir este valor arranca/para el programa:
#   -1 = parar/inactivo, 0 = triturado (360 min), 2 = autolimpieza (90 min).
#    1 = "Secado extra": fase de ~2 h que el aparato encadena SOLO al terminar el
#        triturado. Sin mapear hasta v0.3.5, el select mostraba 'unknown' y, como
#        el programa nunca pasaba a -1, el contador de aperturas no se reiniciaba.
PROGRAM_MAP: Final = {          # valor -> opcion
    -1: "idle",
    0: "cycle",
    1: "extra",       # Secado extra: fase automatica tras el triturado (~2 h)
    2: "self_clean",
}
PROGRAM_OPTIONS: Final = {      # opcion -> valor (para escribir desde el select)
    "idle": -1,
    "cycle": 0,
    "extra": 1,
    "self_clean": 2,
}

# --- Modos virtuales -------------------------------------------------------
# El aparato solo conoce ciclo (0) y autolimpieza (2). "Remover" y "Compactar"
# son autolimpiezas acotadas en el tiempo por la integracion: se lanza 2.3=2 y
# se para al cumplirse la duracion. Al usuario se le muestran como modos propios.
PROGRAM_STIR: Final = "stir"           # Remover: autolimpieza corta
PROGRAM_COMPACT: Final = "compact"     # Compactar: autolimpieza larga

VIRTUAL_DURATIONS: Final = {
    PROGRAM_STIR: 10 * 60,             # 10 minutos
    PROGRAM_COMPACT: 60 * 60,          # 1 hora
}

# Opciones que ofrece el select (en orden de aparicion)
SELECT_OPTIONS: Final = ["idle", "cycle", "extra", "self_clean", PROGRAM_STIR, PROGRAM_COMPACT]

# Disparo automatico
LID_COUNT_THRESHOLD: Final = 3         # aperturas de tapa necesarias
# Hora por defecto de Compactar; editable desde HA (entidad "Hora de compactar")
DEFAULT_COMPACT_HOUR: Final = 15
DEFAULT_COMPACT_MINUTE: Final = 0
LID_COUNT_MAX: Final = 99              # tope de la entidad number

# Un programa se considera COMPLETADO (y reinicia el contador) si se cumple
# cualquiera de estos criterios; con uno solo la deteccion era fragil, porque
# perder contacto con el aparato cerca del final lo hacia parecer cancelado:
#   1) se llego a ver una cuenta atras de <= NATURAL_END_REMAINING minutos,
#   2) el aparato encadeno el "secado extra" (solo lo hace tras completar),
#   3) transcurrio al menos (duracion - NATURAL_END_MARGIN) minutos desde que
#      arranco, aunque no vieramos el tramo final.
NATURAL_END_REMAINING: Final = 1
NATURAL_END_MARGIN: Final = 5

# Segundos tras enviar una orden en los que ignoramos los 'fin de programa':
# la nube puede devolver todavia el estado anterior y provocar falsos finales.
COMMAND_GRACE: Final = 20

STORAGE_VERSION: Final = 1

# --- Parada de seguridad por temperatura ---
# Si la temperatura supera el limite del programa en curso, la integracion
# detiene el aparato (escribe 2.3 = -1) y dispara EVENT_SAFETY_STOP.
# NOTA: es una proteccion SECUNDARIA; depende de HA y de la nube. No sustituye
# al corte termico del propio aparato.
SAFETY_TEMP_LIMITS: Final = {
    0: 150,   # triturado; pico medido ~117 C
    1: 150,   # Secado extra (~2 h); arranca caliente y va enfriando
    2: 100,   # autolimpieza completa; funciona sobre 90 C
}
# Remover y Compactar corren COMO autolimpieza (2.3=2), pero alcanzan mas
# temperatura que un ciclo de limpieza normal: con el limite de 100 C se
# cortaban al minuto (medido: 102 C). Se les aplica su propio limite.
SAFETY_TEMP_LIMITS_VIRTUAL: Final = {
    "stir": 120,      # pico medido ~102 C
    "compact": 120,
}
EVENT_SAFETY_STOP: Final = "dreame_sf25_safety_stop"

# --- TLS del broker MQTT ---
# El broker NO presenta un certificado publicamente valido: usa un CA privado
# autofirmado que ademas se identifica como "GlobalSign Root CA" y carece de
# Authority Key Identifier, por lo que OpenSSL no puede validar la cadena (ni
# siquiera con VERIFY_X509_PARTIAL_CHAIN). Como por ese canal viaja el token de
# acceso, en vez de desactivar la verificacion se ancla (pin) la huella del
# certificado del servidor y se comprueba nada mas completar el handshake,
# antes de enviar credencial alguna.
# El certificado cubre en su SAN todas las regiones (*.mt.eu, *.mt.us, ...),
# de modo que la huella es la misma para cualquier usuario.
# Si Dreame lo renovara, el push MQTT dejaria de conectar y la integracion
# seguiria funcionando por sondeo; habria que actualizar este valor.
MQTT_CERT_SHA256: Final = "0a55ff4bbf5acbb52bfb1b7a941ea097c75f5ca58d0d5eb16464c1d255988200"

PROP_LID: Final = (6, 26)              # tapa: abierta/cerrada (binary_sensor)

# Controles (escribibles):
PROP_CHILD_LOCK: Final = (6, 10)       # bloqueo infantil (0=off, 1=on)
PROP_SILENT_MODE: Final = (6, 17)      # modo silencio (0=off, 1=on)

# Accion MIoT para DESPERTAR de suspension (confirmada): siid 2, aiid 1.
# En suspension (2.1=3) las escrituras se ignoran (code 1); esta accion sí se honra.
ACTION_WAKE: Final = (2, 1)

# Sin mapear aun (candidatos a power/estado/modo/fallo):
PROP_FW_VERSION: Final = (1, 4)
PROP_SERIAL: Final = (1, 5)
PROP_HW_REV: Final = (1, 6)

# Conjunto de propiedades a sondear en cada ciclo del coordinator.
POLL_PROPERTIES: Final = [
    PROP_REMAINING_TIME,
    PROP_ENERGY_WH,
    PROP_HUMIDITY,
    PROP_TEMPERATURE,
    (2, 1), (2, 2), (2, 3), (2, 10),
    (4, 3), (4, 4), (4, 6),
    (6, 10), (6, 17), (6, 26),
]
