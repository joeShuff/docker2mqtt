"""Small helpers with no side effects (logging, filtering and name/size conversion)."""
import re
from fnmatch import fnmatchcase

from .config import CONTAINER_EXCLUDE, CONTAINER_INCLUDE, LOG_LEVEL, LOG_LEVELS, SIZE_BYTES_PER_UNIT


def log(message, tag="DOCKER2MQTT", level="INFO"):
    if LOG_LEVELS[level] >= LOG_LEVELS[LOG_LEVEL]:
        print(f"[{level}] {tag}: {message}", flush=True)


def debug(message, tag="DOCKER2MQTT"):
    log(message, tag, "DEBUG")


def info(message, tag="DOCKER2MQTT"):
    log(message, tag, "INFO")


def warning(message, tag="DOCKER2MQTT"):
    log(message, tag, "WARNING")


def error(message, tag="DOCKER2MQTT"):
    log(message, tag, "ERROR")


def container_key(container_name):
    """Return a stable MQTT/HA key based on the Docker container name."""
    key = ''.join(c if c.isalnum() or c in '_-' else '_' for c in container_name.lower())
    return key.strip('_') or 'container'


def is_monitored(container_name):
    """Apply DOCKER2MQTT_INCLUDE / DOCKER2MQTT_EXCLUDE to a container name."""
    name = container_name.lower()

    if any(fnmatchcase(name, pattern) for pattern in CONTAINER_EXCLUDE):
        return False

    if CONTAINER_INCLUDE:
        return any(fnmatchcase(name, pattern) for pattern in CONTAINER_INCLUDE)

    return True


def docker_size_to_bytes(value):
    """Convert Docker's human-readable size (e.g. 1.23GB) to bytes."""
    match = re.match(r'^([0-9]+(?:\.[0-9]+)?)\s*([KMGTPE]?B)$', value.strip(), re.I)
    if not match:
        return 0

    number = float(match.group(1))
    unit = match.group(2).upper()

    units = {
        "B": 1,
        "KB": 1000,
        "MB": 1000 ** 2,
        "GB": 1000 ** 3,
        "TB": 1000 ** 4,
        "PB": 1000 ** 5,
        "EB": 1000 ** 6,
    }

    return int(number * units[unit])


def bytes_to_size_unit(num_bytes):
    """Convert bytes to the configured SIZE_UNIT (rounded: 2 decimals, or whole bytes for B)."""
    if SIZE_BYTES_PER_UNIT == 1:
        return int(num_bytes)
    return round(num_bytes / SIZE_BYTES_PER_UNIT, 2)
