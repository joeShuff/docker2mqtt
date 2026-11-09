"""MQTT topic layout.

Container topics:  <prefix>/<host>/<container>/<stat>
Discovery topics:  <ha prefix>/<component>/d2m-<host>-<container>/<entity>/config

The host is part of every topic so several docker2mqtt instances can share a broker.
"""
from .config import DOCKER2MQTT_HOSTNAME, HOMEASSISTANT_PREFIX, MQTT_TOPIC_PREFIX
from .util import container_key

# Host component used in container topics and HA discovery topics.
# Sanitised because HA discovery node ids only allow [a-zA-Z0-9_-].
HOST_KEY = container_key(DOCKER2MQTT_HOSTNAME)

# Node id prefixes of discovery topics owned by docker2mqtt (current, then legacy).
DISCOVERY_NODE_PREFIXES = ('d2m-', 'docker-')

HA_DISCOVERY_CLEANUP_TOPIC = f'{HOMEASSISTANT_PREFIX}/+/+/+/config'

topics = {
    "state": f"{MQTT_TOPIC_PREFIX}/{HOST_KEY}/{{}}/state",
    "status": f"{MQTT_TOPIC_PREFIX}/{HOST_KEY}/{{}}/status",
    "image": f"{MQTT_TOPIC_PREFIX}/{HOST_KEY}/{{}}/image",
    "cpu": f"{MQTT_TOPIC_PREFIX}/{HOST_KEY}/{{}}/cpu",
    "1cpu": f"{MQTT_TOPIC_PREFIX}/{HOST_KEY}/{{}}/1cpu",
    "memory": f"{MQTT_TOPIC_PREFIX}/{HOST_KEY}/{{}}/memory",
    "memory_usage": f"{MQTT_TOPIC_PREFIX}/{HOST_KEY}/{{}}/memory_usage",
    "net_io": f"{MQTT_TOPIC_PREFIX}/{HOST_KEY}/{{}}/net_io",
    "pids": f"{MQTT_TOPIC_PREFIX}/{HOST_KEY}/{{}}/pids",
    "block_io": f"{MQTT_TOPIC_PREFIX}/{HOST_KEY}/{{}}/block_io",
    "size": f"{MQTT_TOPIC_PREFIX}/{HOST_KEY}/{{}}/size",
    "commands": f"{MQTT_TOPIC_PREFIX}/{HOST_KEY}/{{}}/commands",
    "home_assistant": {
        "running": f"{HOMEASSISTANT_PREFIX}/binary_sensor/d2m-{HOST_KEY}-{{}}/running/config",
        "status": f"{HOMEASSISTANT_PREFIX}/sensor/d2m-{HOST_KEY}-{{}}/status/config",
        "image": f"{HOMEASSISTANT_PREFIX}/sensor/d2m-{HOST_KEY}-{{}}/image/config",
        "cpu": f"{HOMEASSISTANT_PREFIX}/sensor/d2m-{HOST_KEY}-{{}}/cpu/config",
        "1cpu": f"{HOMEASSISTANT_PREFIX}/sensor/d2m-{HOST_KEY}-{{}}/1cpu/config",
        "memory": f"{HOMEASSISTANT_PREFIX}/sensor/d2m-{HOST_KEY}-{{}}/memory/config",
        "memory_usage": f"{HOMEASSISTANT_PREFIX}/sensor/d2m-{HOST_KEY}-{{}}/memory_usage/config",
        "net_io": f"{HOMEASSISTANT_PREFIX}/sensor/d2m-{HOST_KEY}-{{}}/net_io/config",
        "pids": f"{HOMEASSISTANT_PREFIX}/sensor/d2m-{HOST_KEY}-{{}}/pids/config",
        "block_io": f"{HOMEASSISTANT_PREFIX}/sensor/d2m-{HOST_KEY}-{{}}/block_io/config",
        "size": f"{HOMEASSISTANT_PREFIX}/sensor/d2m-{HOST_KEY}-{{}}/size/config",
        "stop": f"{HOMEASSISTANT_PREFIX}/button/d2m-{HOST_KEY}-{{}}/stop/config",
        "start": f"{HOMEASSISTANT_PREFIX}/button/d2m-{HOST_KEY}-{{}}/start/config",
        "restart": f"{HOMEASSISTANT_PREFIX}/button/d2m-{HOST_KEY}-{{}}/restart/config",
    },
}

# Map commands sent via MQTT to the docker command equivalent.
payload_commands = {
    "stop": "stop",
    "start": "start",
    "restart": "restart",
}


def container_topic_keys():
    """Names of the plain per-container topics (everything except the HA discovery dict)."""
    return [name for name, value in topics.items() if isinstance(value, str)]


def clear_container_topics(send, key):
    """Publish empty retained payloads to every topic belonging to one container.

    `send` is the publish function (kept as a parameter so this module has no MQTT dependency).
    """
    for entity_topic in topics['home_assistant'].values():
        send(entity_topic.format(key), '', retain=True)

    for state_topic in container_topic_keys():
        send(topics[state_topic].format(key), '', retain=True)
