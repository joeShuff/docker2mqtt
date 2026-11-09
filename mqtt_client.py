"""MQTT connection handling, publishing and cleanup of stale Home Assistant discovery topics."""
import json
from time import sleep

import paho.mqtt.client

from .config import (DOCKER2MQTT_HOSTNAME, HOMEASSISTANT_PREFIX, MQTT_DEBUG, MQTT_HOST,
                     MQTT_PASSWD, MQTT_PORT, MQTT_QOS, MQTT_TIMEOUT, MQTT_TOPIC_PREFIX, MQTT_USER)
from .topics import DISCOVERY_NODE_PREFIXES, HA_DISCOVERY_CLEANUP_TOPIC, HOST_KEY, topics
from .util import debug, error, info, warning

STATUS_TOPIC = f'{MQTT_TOPIC_PREFIX}/{DOCKER2MQTT_HOSTNAME}/status'


def new_mqtt_client():
    """Create an MQTT client using the paho-mqtt 2.x callback API (falls back to 1.x)."""
    callback_api = getattr(paho.mqtt.client, 'CallbackAPIVersion', None)
    if callback_api is not None:
        return paho.mqtt.client.Client(callback_api.VERSION2)
    return paho.mqtt.client.Client()


mqtt = new_mqtt_client()
connected_to_mqtt = False
ha_discovery_cleanup_active = False

# State of the "is this host name already online?" check performed at startup.
_host_probe_active = False
_host_probe_retained_status = None

_cleared_discovery_topics = 0

# Set by the application: called with every non-cleanup message (container commands).
command_handler = None


def is_connected():
    return connected_to_mqtt


'''
DISCOVERY CLEANUP
'''
def discovery_config_belongs_to_this_host(payload):
    """Return True only if a retained discovery payload was published by THIS docker host.

    The cleanup subscription matches every docker2mqtt discovery topic on the broker,
    including ones owned by other instances. The payload's device identifier
    (`<hostname>:<name>`) and entity unique_id (`<hostname>.<name>.<entity>`) both start
    with the publishing host's DOCKER2MQTT_HOSTNAME, so use those to make sure we never
    clear another instance's entities.
    """
    try:
        config = json.loads(payload)
    except (ValueError, TypeError):
        return False

    if not isinstance(config, dict):
        return False

    device = config.get('device')
    identifiers = device.get('identifiers') if isinstance(device, dict) else None
    if isinstance(identifiers, str):
        identifiers = [identifiers]
    elif not isinstance(identifiers, (list, tuple)):
        identifiers = []

    # Identifiers look like "<hostname>:<container key>". Container keys never contain
    # ":", so everything before the last ":" is the host. Compare it exactly, otherwise
    # host "nas" would also match host "nas.local".
    host_identifiers = [str(i) for i in identifiers if ':' in str(i)]
    if host_identifiers:
        return any(i.rpartition(':')[0] == DOCKER2MQTT_HOSTNAME for i in host_identifiers)

    # No host-style identifier (very old payloads): fall back to the unique_id prefix.
    return str(config.get('unique_id', '')).startswith(f"{DOCKER2MQTT_HOSTNAME}.")


def clear_legacy_state_topics(payload):
    """Migration: clear retained state/command topics from the old host-less layout.

    Older versions published to <prefix>/<container>/<stat>. Our own old discovery
    config tells us which topics those were, so remove any that are not under the
    new <prefix>/<host>/ namespace.
    """
    try:
        config = json.loads(payload)
    except (ValueError, TypeError):
        return

    if not isinstance(config, dict):
        return

    new_namespace = f"{MQTT_TOPIC_PREFIX}/{HOST_KEY}/"
    for field in ('state_topic', 'command_topic'):
        topic = config.get(field)
        if isinstance(topic, str) and topic and not topic.startswith(new_namespace):
            mqtt_send(topic, '', retain=True)
            debug(f'Cleared legacy topic: {topic}', tag="MQTT")


def cleanup_home_assistant_discovery():
    """Clear this host's retained Home Assistant discovery entries before rebuilding them."""
    global ha_discovery_cleanup_active, _cleared_discovery_topics

    ha_discovery_cleanup_active = True
    _cleared_discovery_topics = 0
    debug(f'Cleaning Home Assistant discovery topics: {HA_DISCOVERY_CLEANUP_TOPIC}', tag="MQTT")

    try:
        mqtt.subscribe(HA_DISCOVERY_CLEANUP_TOPIC, qos=MQTT_QOS)
        # Give the MQTT loop time to receive retained discovery messages and
        # let on_mqtt_message clear them.
        sleep(2)
    finally:
        mqtt.unsubscribe(HA_DISCOVERY_CLEANUP_TOPIC)
        ha_discovery_cleanup_active = False

    info(f'Cleared {_cleared_discovery_topics} stale Home Assistant discovery topics for this host',
         tag="MQTT")


def _handle_cleanup_message(msg):
    global _cleared_discovery_topics

    parts = msg.topic.split("/")
    # Expected docker2mqtt discovery topic:
    # homeassistant/<component>/d2m-<host>-<key>/<entity>/config
    # (older releases used docker-<key>; still matched here so they get migrated)
    if (
        msg.retain
        and len(parts) == 5
        and parts[0] == HOMEASSISTANT_PREFIX
        and parts[2].startswith(DISCOVERY_NODE_PREFIXES)
        and parts[4] == "config"
        and msg.payload
        and discovery_config_belongs_to_this_host(msg.payload)
    ):
        mqtt_send(msg.topic, '', retain=True)
        _cleared_discovery_topics += 1
        debug(f'Cleared Home Assistant discovery topic: {msg.topic}', tag="MQTT")
        clear_legacy_state_topics(msg.payload)


'''
DUPLICATE HOST CHECK
'''
def _start_host_probe():
    """Start listening for a retained status message for this host name."""
    global _host_probe_active, _host_probe_retained_status

    _host_probe_active = True
    _host_probe_retained_status = None
    mqtt.subscribe(STATUS_TOPIC, qos=MQTT_QOS)


def _finish_host_probe():
    """Stop listening and warn if this host name was already marked online."""
    global _host_probe_active

    mqtt.unsubscribe(STATUS_TOPIC)
    _host_probe_active = False

    if _host_probe_retained_status == 'online':
        warning(
            f"Host name '{DOCKER2MQTT_HOSTNAME}' is already marked online on the broker. Either "
            f"another docker2mqtt instance is using the same DOCKER2MQTT_HOSTNAME (they will "
            f"overwrite each other's entities - give every instance a unique name), or a previous "
            f"instance has not timed out yet (safe to ignore after a quick restart).",
            tag="MQTT")
    else:
        debug(f"Host name '{DOCKER2MQTT_HOSTNAME}' is not currently marked online", tag="MQTT")


def _handle_status_message(msg):
    global _host_probe_retained_status

    if _host_probe_active and msg.retain:
        _host_probe_retained_status = msg.payload.decode(errors='replace')


'''
MQTT CALLBACKS
'''
def on_mqtt_connect(client, userdata, flags, rc, properties=None):
    global connected_to_mqtt

    if rc == 0:
        connected_to_mqtt = True
        info(f'Connected to MQTT server at {MQTT_HOST}:{MQTT_PORT}', tag="MQTT")
    else:
        connected_to_mqtt = False
        error(f'Failed to connect to MQTT server at {MQTT_HOST}:{MQTT_PORT} reason: {rc}', tag="MQTT")


def on_mqtt_disconnect(client, userdata, *args):
    global connected_to_mqtt

    # paho 2.x (VERSION2): (disconnect_flags, reason_code, properties)
    # paho 1.x:            (rc,)
    rc = args[1] if len(args) >= 2 else (args[0] if args else None)

    if rc == 0:
        info('Disconnected from MQTT server', tag="MQTT")
    else:
        warning(f'Disconnected from MQTT server (reason:{rc})', tag="MQTT")
    connected_to_mqtt = False


def on_mqtt_message(client, userdata, msg):
    if MQTT_DEBUG:
        debug("Message received->" + msg.topic + " > " + str(msg.payload.decode()), tag="MQTT")

    if msg.topic == STATUS_TOPIC:
        _handle_status_message(msg)
        return

    if ha_discovery_cleanup_active:
        _handle_cleanup_message(msg)
        return

    if command_handler is not None:
        command_handler(msg)


'''
MQTT MANAGEMENT
'''
def setup_mqtt():
    global mqtt

    mqtt = new_mqtt_client()
    if MQTT_USER:
        mqtt.username_pw_set(username=MQTT_USER, password=MQTT_PASSWD)
    mqtt.will_set(STATUS_TOPIC, 'offline', qos=MQTT_QOS, retain=True)
    mqtt.on_connect = on_mqtt_connect
    mqtt.on_disconnect = on_mqtt_disconnect
    mqtt.on_message = on_mqtt_message


def mqtt_connect(exit_on_fail=False, on_ready=None, check_duplicate_host=False):
    """Connect, clear this host's stale discovery topics, then call `on_ready` to republish.

    With `check_duplicate_host` a warning is logged if the broker already has a retained
    "online" status for this host name (i.e. another instance may be using it).
    """
    global connected_to_mqtt

    try:
        info(f'Attempting to connect to MQTT server at {MQTT_HOST}:{MQTT_PORT}', tag="MQTT")
        mqtt.connect(MQTT_HOST, MQTT_PORT, MQTT_TIMEOUT)
        connected_to_mqtt = True

        mqtt.subscribe(topics['commands'].format("+"))

        mqtt.loop_start()

        if check_duplicate_host:
            _start_host_probe()

        try:
            # Clear any retained Home Assistant discovery entries belonging to this host,
            # then rebuild discovery from the current containers. The wait inside also
            # gives the duplicate host check time to receive its retained message.
            cleanup_home_assistant_discovery()
        finally:
            if check_duplicate_host:
                _finish_host_probe()

        if on_ready is not None:
            on_ready()

        mqtt_send(STATUS_TOPIC, 'online', retain=True)
        return True
    except ConnectionRefusedError as e:
        error(f'Failed to connect to MQTT server at {MQTT_HOST}:{MQTT_PORT}. reason {e}', tag="MQTT")
        connected_to_mqtt = False

        if exit_on_fail:
            exit(0)

        return False


def mqtt_disconnect():
    global connected_to_mqtt

    info('Disconnecting from MQTT voluntarily', tag="MQTT")
    connected_to_mqtt = False
    mqtt.publish(STATUS_TOPIC, 'offline', qos=MQTT_QOS, retain=True)
    mqtt.disconnect()
    sleep(1)
    mqtt.loop_stop()


def mqtt_send(topic, payload, retain=False, qos=MQTT_QOS):
    """Publish a message. Returns True if it was handed to the MQTT client, else False."""
    if not connected_to_mqtt:
        return False

    try:
        if MQTT_DEBUG:
            debug(f'Sending to MQTT: {topic}: {payload}', tag="MQTT")
        mqtt.publish(topic, payload=payload, qos=qos, retain=retain)
        return True
    except Exception as e:
        error(f'MQTT Publish Failed: {e}', tag="MQTT")
        return False
