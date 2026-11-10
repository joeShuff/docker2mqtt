"""Home Assistant MQTT discovery payloads for one container.

Each entity is described once in the tables below; build_discovery_messages()
turns them into (discovery topic, config) pairs.
"""
import re

from .config import (DOCKER2MQTT_HOSTNAME, EXPIRE_AFTER_SECONDS, MQTT_QOS, MQTT_TOPIC_PREFIX,
                     SIZE_DISPLAY_PRECISION, SIZE_HA_UNIT)
from .topics import HOST_KEY, payload_commands, topics

DIAGNOSTIC = {"entity_category": "diagnostic"}
DISABLED = {"enabled_by_default": False}

# (topic key, label, unique id suffix, extra HA attributes). Order is publish order.
SENSORS = [
    ("status", "Status", "status", {"icon": "mdi:chart-line-variant"}),
    ("image", "Image", "image", {**DIAGNOSTIC, "icon": "mdi:docker"}),
    ("cpu", "CPU Usage", "cpu",
     {"unit_of_measurement": "%", **DIAGNOSTIC, "icon": "mdi:cpu-64-bit"}),
    ("1cpu", "Overall CPU Usage", "1_cpu",
     {"unit_of_measurement": "%", **DIAGNOSTIC, "icon": "mdi:cpu-64-bit"}),
    ("memory", "Memory", "memory",
     {"unit_of_measurement": "%", **DIAGNOSTIC, "icon": "mdi:memory"}),
    ("memory_usage", "Memory Usage", "memory_usage",
     {**DIAGNOSTIC, **DISABLED, "icon": "mdi:memory"}),
    ("net_io", "Network IO", "net_io",
     {**DIAGNOSTIC, **DISABLED, "icon": "mdi:lan-connect"}),
    ("pids", "PIDs", "pids",
     {**DIAGNOSTIC, **DISABLED, "icon": "mdi:memory"}),
    ("block_io", "Block IO", "block_io",
     {**DIAGNOSTIC, **DISABLED, "icon": "mdi:tape-drive"}),
    ("size", "Writable Layer Size", "size",
     {"unit_of_measurement": SIZE_HA_UNIT, "suggested_display_precision": SIZE_DISPLAY_PRECISION,
      "device_class": "data_size", "state_class": "measurement",
      **DIAGNOSTIC, "enabled_by_default": True, "icon": "mdi:harddisk"}),
]

# (topic key, HA discovery key, label, unique id suffix, extra HA attributes)
# The state topic carries Docker's raw state ("running", "exited", "paused", ...);
# the template turns it into ON/OFF.
BINARY_SENSORS = [
    ("state", "running", "Running", "running",
     {"device_class": "running", "value_template": "{{ 'ON' if value == 'running' else 'OFF' }}"}),
]

# (command, label, icon)
BUTTONS = [
    ("stop", "Stop", "mdi:stop"),
    ("start", "Start", "mdi:play"),
    ("restart", "Restart", "mdi:restart"),
]


def _default_entity_id(component, key, label):
    """Host-prefixed entity id, e.g. sensor.teddycctv_netdata_cpu_usage.

    Home Assistant builds entity ids from the device and entity *names*, which do not
    contain the host. Two hosts running a container with the same name would then fight
    over the same id (the second one becomes ..._2). Suggesting the id explicitly keeps
    them apart. It only applies when an entity is first created: existing entities keep
    their current id.
    """
    parts = (HOST_KEY, key, label)
    slug = '_'.join(re.sub(r'[^a-z0-9]+', '_', part.lower()).strip('_') for part in parts)
    return f"{component}.{slug}"


def _base_config(container_name, container_image, key):
    return {
        'availability_topic': f'{MQTT_TOPIC_PREFIX}/{DOCKER2MQTT_HOSTNAME}/status',
        'expire_after': EXPIRE_AFTER_SECONDS,
        'device': {
            "name": container_name,
            "manufacturer": "Docker",
            "model": container_image,
            "identifiers": f"{DOCKER2MQTT_HOSTNAME}:{key}",
            "sw_version": container_image,
            "via_device": "docker2mqtt",
        },
    }


def build_discovery_messages(container_name, container_image, key):
    """Return [(discovery_topic, config_dict), ...] for every entity of a container."""
    base = _base_config(container_name, container_image, key)
    messages = []

    for topic_key, label, unique_suffix, extra in SENSORS:
        config = base | {
            "qos": MQTT_QOS,
            "state_topic": topics[topic_key].format(key),
            "name": f"{container_name} {label}",
            "unique_id": f"{DOCKER2MQTT_HOSTNAME}.{key}.{unique_suffix}",
            "default_entity_id": _default_entity_id("sensor", key, label),
            **extra,
        }
        messages.append((topics['home_assistant'][topic_key].format(key), config))

    for topic_key, ha_key, label, unique_suffix, extra in BINARY_SENSORS:
        config = base | {
            "qos": MQTT_QOS,
            "state_topic": topics[topic_key].format(key),
            "name": f"{container_name} {label}",
            "unique_id": f"{DOCKER2MQTT_HOSTNAME}.{key}.{unique_suffix}",
            "default_entity_id": _default_entity_id("binary_sensor", key, label),
            **extra,
        }
        messages.append((topics['home_assistant'][ha_key].format(key), config))

    for command, label, icon in BUTTONS:
        config = base | {
            "qos": MQTT_QOS,
            "command_topic": topics['commands'].format(key),
            "name": f"{container_name} {label}",
            "unique_id": f"{DOCKER2MQTT_HOSTNAME}.{key}.{command}",
            "default_entity_id": _default_entity_id("button", key, label),
            "icon": icon,
            "payload_press": payload_commands[command],
        }
        messages.append((topics['home_assistant'][command].format(key), config))

    return messages