"""Registering, updating and removing containers (HA discovery + MQTT state)."""
import json
from time import monotonic

from . import docker_cli
from .config import PUBLISH_REFRESH_SECONDS
from .discovery import build_discovery_messages
from .mqtt_client import mqtt_send
from .state import container_ids, known_container_stats, known_containers, new_container_stats, published
from .topics import clear_container_topics, container_topic_keys, topics
from .util import bytes_to_size_unit, container_key, debug, is_monitored, warning


def _publish_state(topic, value, force=False):
    """Publish a state value, skipping it if unchanged and recently published.

    Unchanged values are re-sent every PUBLISH_REFRESH_SECONDS so Home Assistant's
    expire_after never marks the entity unavailable.
    """
    payload = str(value)
    now = monotonic()
    last = published.get(topic)

    if not force and last is not None:
        last_payload, last_time = last
        if last_payload == payload and now - last_time < PUBLISH_REFRESH_SECONDS:
            return

    if mqtt_send(topic, payload):
        published[topic] = (payload, now)


def post_info_for_container(key, force=False):
    """Publish the state and stats of one known container (only what changed unless forced)."""
    if key not in known_containers:
        warning(f"Cannot find container for ID {key}")
        return

    if key not in known_container_stats:
        warning(f"Cannot find stats for ID {key}")
        return

    container = known_containers[key]
    stats = known_container_stats[key]

    values = {
        'state': container['state'],
        'status': container['status'],
        'image': container['image'],
        'cpu': stats['cpu'],
        '1cpu': stats['1_cpu'],
        'memory': stats['memory'],
        'memory_usage': stats['memory_usage'],
        'net_io': stats['net_io'],
        'pids': stats['pids'],
        'block_io': stats['block_io'],
        'size': bytes_to_size_unit(stats['size']),
    }

    for topic_key, value in values.items():
        _publish_state(topics[topic_key].format(key), value, force)


def register_container(container_entry):
    container_name = container_entry['name']

    if not is_monitored(container_name):
        debug(f"Ignoring container {container_name} (excluded by DOCKER2MQTT_INCLUDE/EXCLUDE)")
        return

    key = container_key(container_name)
    container_id = container_entry['id']
    container_image = container_entry['image']
    container_entry = container_entry | {"key": key}

    for topic, config in build_discovery_messages(container_name, container_image, key):
        mqtt_send(topic, json.dumps(config), retain=True)

    mqtt_send(topics['commands'].format(key), '---')

    known_containers[key] = container_entry
    if key not in known_container_stats:
        known_container_stats[key] = new_container_stats()
    container_ids[container_id] = key
    post_info_for_container(key, force=True)


def register_all_containers():
    for container_status in docker_cli.list_containers():
        register_container({
            'id': container_status['ID'],
            'name': container_status['Names'],
            'image': container_status['Image'],
            'status': container_status['Status'],
            'state': container_status['State'],
        })


def clear_container(key):
    """Remove every retained/discovery topic of a container and forget what we published."""
    clear_container_topics(mqtt_send, key)

    for topic_key in container_topic_keys():
        published.pop(topics[topic_key].format(key), None)


def unregister_container(container_id):
    key = container_ids.get(container_id)
    if key is None:
        debug(f"Not unregistering unknown container ID {container_id}", tag="Event")
        return

    # A same-name replacement may already exist. Do not let the old destroy
    # event delete the new container's HA device.
    current = known_containers.get(key)
    if current and current["id"] != container_id:
        container_ids.pop(container_id, None)
        return

    clear_container(key)

    known_containers.pop(key, None)
    known_container_stats.pop(key, None)
    container_ids.pop(container_id, None)
