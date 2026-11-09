"""Configuration, read once from environment variables."""
from os import environ
from socket import gethostname

LOG_LEVELS = {'DEBUG': 10, 'INFO': 20, 'WARNING': 30, 'ERROR': 40}


def _log_level():
    requested = environ.get('LOG_LEVEL', '').strip().upper()
    if requested == 'WARN':
        requested = 'WARNING'
    if requested in LOG_LEVELS:
        return requested
    if requested:
        print(f"[WARNING] CONFIG: unknown LOG_LEVEL '{requested}', using INFO "
              f"(valid: {', '.join(LOG_LEVELS)})", flush=True)
    # Legacy switch: DEBUG=1 used to turn logging on.
    if environ.get('DEBUG', '0') == '1':
        return 'DEBUG'
    return 'INFO'


def _csv(name):
    return [part.strip().lower() for part in environ.get(name, '').split(',') if part.strip()]


LOG_LEVEL = _log_level()
# Also log every MQTT message sent/received (very noisy, needs LOG_LEVEL=DEBUG).
MQTT_DEBUG = environ.get('MQTT_DEBUG', '0') == '1'

HOMEASSISTANT_PREFIX = environ.get('HOMEASSISTANT_PREFIX', 'homeassistant')
DOCKER2MQTT_HOSTNAME = environ.get('DOCKER2MQTT_HOSTNAME', gethostname())

MQTT_CLIENT_ID = environ.get('MQTT_CLIENT_ID', 'docker2mqtt')
MQTT_USER = environ.get('MQTT_USER', '')
MQTT_PASSWD = environ.get('MQTT_PASSWD', '')
MQTT_HOST = environ.get('MQTT_HOST', 'localhost')
MQTT_PORT = int(environ.get('MQTT_PORT', '1883'))
MQTT_TIMEOUT = int(environ.get('MQTT_TIMEOUT', '30'))
MQTT_TOPIC_PREFIX = environ.get('MQTT_TOPIC_PREFIX', 'docker')
MQTT_QOS = int(environ.get('MQTT_QOS', 1))

STATS_DELAY_SECONDS = int(environ.get('STATS_DELAY', '5'))

# Values are only published when they change, plus a refresh at least this often so
# Home Assistant does not mark entities unavailable (see EXPIRE_AFTER_SECONDS).
PUBLISH_REFRESH_SECONDS = int(environ.get('PUBLISH_REFRESH', '120'))
# Always at least 3x the refresh interval so a missed refresh never expires an entity.
EXPIRE_AFTER_SECONDS = max(STATS_DELAY_SECONDS * 60, PUBLISH_REFRESH_SECONDS * 3)

# Comma separated container name patterns (* and ? wildcards, case-insensitive).
# If INCLUDE is set only matching containers are published; EXCLUDE always wins.
CONTAINER_INCLUDE = _csv('DOCKER2MQTT_INCLUDE')
CONTAINER_EXCLUDE = _csv('DOCKER2MQTT_EXCLUDE')

# Unit the writable layer size is published in (B, KB, MB or GB). Home Assistant stores
# long-term statistics in this unit, so changing it later triggers a unit-change repair.
_SIZE_UNITS = {
    # name: (Home Assistant unit, bytes per unit, display precision)
    'B': ('B', 1, 0),
    'KB': ('kB', 1000, 1),
    'MB': ('MB', 1000 ** 2, 1),
    'GB': ('GB', 1000 ** 3, 2),
}


def _size_unit():
    requested = environ.get('SIZE_UNIT', '').strip().upper() or 'MB'
    if requested not in _SIZE_UNITS:
        print(f"[WARNING] CONFIG: unknown SIZE_UNIT '{requested}', using MB "
              f"(valid: {', '.join(_SIZE_UNITS)})", flush=True)
        requested = 'MB'
    return requested


SIZE_UNIT = _size_unit()
SIZE_HA_UNIT, SIZE_BYTES_PER_UNIT, SIZE_DISPLAY_PRECISION = _SIZE_UNITS[SIZE_UNIT]
