#!/usr/bin/env python3
"""Listens to `docker system events` and sents container stop/start events to mqtt.
"""
import json
import queue
import re
from os import environ
from socket import gethostname
from subprocess import run, Popen, PIPE
from threading import Thread
from time import sleep, time

import paho.mqtt.client

DEBUG = environ.get('DEBUG', '0') == '1'
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
DISCOVERY_TOPIC = f'{HOMEASSISTANT_PREFIX}/binary_sensor/{MQTT_TOPIC_PREFIX}/{DOCKER2MQTT_HOSTNAME}_{{}}/config'
HA_DISCOVERY_CLEANUP_TOPIC = f'{HOMEASSISTANT_PREFIX}/+/+/+/config'
WATCHED_EVENTS = ('create', 'destroy', 'die', 'pause', 'rename', 'start', 'stop', 'unpause')
STATS_DELAY_SECONDS = int(environ.get('STATS_DELAY', '5'))

known_containers = {}
container_ids = {}
docker_events_cmd = ['docker', 'events', '-f', 'type=container', '--format', '{{json .}}']
docker_stats_cmd = ['docker', 'stats', '-a', '--format', '{{json .}}', '--no-stream']
docker_ps_cmd = ['docker', 'ps', '-a', '--format', '{{json .}}']
docker_ps_size_cmd = ['docker', 'ps', '-a', '--size', '--format', '{{json .}}']


def container_key(container_name):
    """Return a stable MQTT/HA key based on the Docker container name."""
    key = ''.join(c if c.isalnum() or c in '_-' else '_' for c in container_name.lower())
    return key.strip('_') or 'container'

def docker_size_to_bytes(value):
    """Convert Docker's human-readable size (e.g. 1.23GB) to bytes."""
    match = re.match(r'^([0-9]+(?:\\.[0-9]+)?)\\s*([KMGTPE]?B)$', value.strip(), re.I)
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

mqtt = paho.mqtt.client.Client()


connected_to_mqtt = False
ha_discovery_cleanup_active = False

known_container_stats = {}

docker_events = queue.Queue()

docker_system_stats = {

}

empty_container_stats = {
    "cpu": 0,
    "1_cpu": 0,
    "memory": 0,
    "memory_usage": "0B / 0B",
    "net_io": "OB / 0B",
    "pids": 0,
    "block_io": "0B / 0B",
    "size": "0B"
}

topics = {
    "state": "docker/{}/state",
    "status": "docker/{}/status",
    "image": "docker/{}/image",
    "cpu": "docker/{}/cpu",
    "1cpu": "docker/{}/1cpu",
    "memory": "docker/{}/memory",
    "memory_usage": "docker/{}/memory_usage",
    "net_io": "docker/{}/net_io",
    "pids": "docker/{}/pids",
    "block_io": "docker/{}/block_io",
    "size": "docker/{}/size",
    "commands": "docker/{}/commands",
    "home_assistant":{
        "state": f"{HOMEASSISTANT_PREFIX}/sensor/docker-{{}}/state/config",
        "status": f"{HOMEASSISTANT_PREFIX}/sensor/docker-{{}}/status/config",
        "image": f"{HOMEASSISTANT_PREFIX}/sensor/docker-{{}}/image/config",
        "cpu": f"{HOMEASSISTANT_PREFIX}/sensor/docker-{{}}/cpu/config",
        "1cpu": f"{HOMEASSISTANT_PREFIX}/sensor/docker-{{}}/1cpu/config",
        "memory": f"{HOMEASSISTANT_PREFIX}/sensor/docker-{{}}/memory/config",
        "memory_usage": f"{HOMEASSISTANT_PREFIX}/sensor/docker-{{}}/memory_usage/config",
        "net_io": f"{HOMEASSISTANT_PREFIX}/sensor/docker-{{}}/net_io/config",
        "pids": f"{HOMEASSISTANT_PREFIX}/sensor/docker-{{}}/pids/config",
        "block_io": f"{HOMEASSISTANT_PREFIX}/sensor/docker-{{}}/block_io/config",
        "size": f"{HOMEASSISTANT_PREFIX}/sensor/docker-{{}}/size/config",
        "stop": f"{HOMEASSISTANT_PREFIX}/button/docker-{{}}/stop/config",
        "start": f"{HOMEASSISTANT_PREFIX}/button/docker-{{}}/start/config",
        "restart": f"{HOMEASSISTANT_PREFIX}/button/docker-{{}}/restart/config",
    }
}


'''
Map commands sent via mqtt to the docker command equivalent
'''
payload_commands = {
    "stop": "stop",
    "start": "start",
    "restart": "restart",
}

'''
LOGS
'''
def log(message, tag = "DOCKER2MQTT"):
    if DEBUG:
        print(f"{tag}: {message}")


'''
MQTT CALLBACKS
'''
def on_mqtt_connect(client, userdata, flags, rc):
    global connected_to_mqtt

    if rc == 0:
        connected_to_mqtt = True

        log(tag="MQTT", message=f'Connected to MQTT server at {MQTT_HOST}:{MQTT_PORT}')

        # Discovery cleanup is performed after the MQTT network loop has started.
        # This allows retained discovery messages to actually reach on_mqtt_message.
        pass
    else:
        connected_to_mqtt = False
        log(tag="MQTT", message=f'Failed to connect to MQTT server at {MQTT_HOST}:{MQTT_PORT} reason: {rc}')


def on_mqtt_message(client, userdata, msg):
    if MQTT_DEBUG:
        log(tag="MQTT", message="Message received->" + msg.topic + " > " + str(msg.payload.decode()))

    if ha_discovery_cleanup_active:
        parts = msg.topic.split("/")
        # Expected docker2mqtt discovery topic:
        # homeassistant/<component>/docker-<key>/<entity>/config
        if (
            msg.retain
            and len(parts) == 5
            and parts[0] == HOMEASSISTANT_PREFIX
            and parts[2].startswith("docker-")
            and parts[4] == "config"
        ):
            mqtt_send(msg.topic, '', retain=True)
            log(tag="MQTT", message=f'Cleared Home Assistant discovery topic: {msg.topic}')
        return

    if not msg.topic.startswith(f"{MQTT_TOPIC_PREFIX}/"):
        return

    topic_parts = msg.topic.split("/")
    if len(topic_parts) != 3 or topic_parts[2] != "commands":
        return

    key = topic_parts[1]
    command = msg.payload.decode()

    if command == "---":
        return

    if key not in known_containers:
        log(tag="Command", message=f"Cannot find container for key {key}")
        return

    container_id = known_containers[key]["id"]
    container_status = get_container_ps(container_id)
    if container_status is None:
        log(tag="Command", message=f"Container {container_id} no longer exists")
        return

    container_running = container_status['State'] == "running"

    try:
        if command == "start":
            if not container_running:
                run(["docker", "start", container_id], check=True)
            else:
                log(tag="Command", message="Can't start an already running container")
        elif command == "stop":
            if container_running:
                run(["docker", "stop", container_id], check=True)
            else:
                log(tag="Command", message="Can't stop an already stopped container")
        elif command == "restart":
            run(["docker", "restart", container_id], check=True)
        else:
            log(tag="Command", message=f"Unknown command: {command}")
    except Exception as e:
        log(tag="Error", message=f"Failed to {command} container {container_id}: {e}")

    mqtt_send(msg.topic, '---')

def on_mqtt_disconnect(client, userdata, rc):
    global connected_to_mqtt

    log(tag="MQTT", message=f'Disconnected from MQTT server (reason:{rc})')
    connected_to_mqtt = False


'''
MQTT MANAGEMENT
'''
def setup_mqtt():
    global mqtt

    mqtt = paho.mqtt.client.Client()
    mqtt.username_pw_set(username=MQTT_USER, password=MQTT_PASSWD)
    mqtt.will_set(f'{MQTT_TOPIC_PREFIX}/{DOCKER2MQTT_HOSTNAME}/status', 'offline', qos=MQTT_QOS, retain=True)
    mqtt.on_connect = on_mqtt_connect
    mqtt.on_disconnect = on_mqtt_disconnect
    mqtt.on_message = on_mqtt_message


def mqtt_connect(exit_on_fail=False):
    global mqtt, connected_to_mqtt

    try:
        log(tag="MQTT", message=f'Attempting to connect to MQTT server at {MQTT_HOST}:{MQTT_PORT}')
        mqtt.connect(MQTT_HOST, MQTT_PORT, MQTT_TIMEOUT)
        connected_to_mqtt = True

        mqtt.subscribe(topics['commands'].format("+"))

        mqtt.loop_start()

        # Clear any retained Home Assistant discovery entries belonging to
        # docker2mqtt, then rebuild discovery from the current containers.
        cleanup_home_assistant_discovery()
        register_all_containers()

        mqtt_send(f'{MQTT_TOPIC_PREFIX}/{DOCKER2MQTT_HOSTNAME}/status', 'online', retain=True)
        return True
    except ConnectionRefusedError as e:
        log(tag="Error", message=f'Failed to connect to MQTT server at {MQTT_HOST}:{MQTT_PORT}. reason {e}')
        connected_to_mqtt = False

        if exit_on_fail:
            exit(0)

        return False


def mqtt_disconnect():
    global connected_to_mqtt

    log(tag="MQTT", message=f'Disconnecting from MQTT voluntarily')
    connected_to_mqtt = False
    mqtt.publish(f'{MQTT_TOPIC_PREFIX}/{DOCKER2MQTT_HOSTNAME}/status', 'offline', qos=MQTT_QOS, retain=True)
    mqtt.disconnect()
    sleep(1)
    mqtt.loop_stop()


def mqtt_send(topic, payload, retain=False, qos=MQTT_QOS):
    global connected_to_mqtt

    if connected_to_mqtt:
        try:
            if MQTT_DEBUG:
                log(tag="MQTT", message=f'Sending to MQTT: {topic}: {payload}')
            mqtt.publish(topic, payload=payload, qos=qos, retain=retain)
        except Exception as e:
            log(tag="MQTT", message=f'MQTT Publish Failed: {e}')

def cleanup_home_assistant_discovery():
    """Clear retained Home Assistant discovery entries before rebuilding them."""
    global ha_discovery_cleanup_active

    ha_discovery_cleanup_active = True
    log(tag="MQTT", message=f'Cleaning Home Assistant discovery topics: {HA_DISCOVERY_CLEANUP_TOPIC}')

    try:
        mqtt.subscribe(HA_DISCOVERY_CLEANUP_TOPIC, qos=MQTT_QOS)
        # Give the MQTT loop time to receive retained discovery messages and
        # let on_mqtt_message clear them.
        sleep(2)
    finally:
        mqtt.unsubscribe(HA_DISCOVERY_CLEANUP_TOPIC)
        ha_discovery_cleanup_active = False

'''
CONTAINER MANAGEMENT
'''
def get_docker_system_stats():
    global docker_system_stats
    docker_system_stats_cmd = ['docker', 'system', 'info', '--format', '{{json .}}']
    docker_system_stats_proc = run(docker_system_stats_cmd, stdout=PIPE, text=True)
    for line in docker_system_stats_proc.stdout.splitlines():
        docker_system_stats = json.loads(line)

    return docker_system_stats


def get_container_ps(short_container_id):
    # Run a command to get the latest info about this container to get accurate data that is missing from the event
    docker_ps_cont_cmd = ['docker', 'ps', '-a', '--format', '{{json .}}', '-f', f"id={short_container_id}"]
    docker_ps_cont = run(docker_ps_cont_cmd, stdout=PIPE, text=True)
    for line in docker_ps_cont.stdout.splitlines():
        return json.loads(line)


def post_info_for_container(container_id):
    if container_id not in known_containers.keys():
        log(tag="Error", message=f"Cannot find container for ID {container_id}")
        return

    if container_id not in known_container_stats.keys():
        log(tag="Error", message=f"Cannot find stats for ID {container_id}")
        return

    container = known_containers[container_id]
    container_stats = known_container_stats[container_id]

    container_image = container['image']
    container_status = container['status']
    container_state = container['state']

    mqtt_send(topics['state'].format(container_id), container_state)
    mqtt_send(topics['status'].format(container_id), container_status)
    mqtt_send(topics['image'].format(container_id), container_image)

    mqtt_send(topics['cpu'].format(container_id), container_stats['cpu'])
    mqtt_send(topics['1cpu'].format(container_id), container_stats['1_cpu'])
    mqtt_send(topics['memory'].format(container_id), container_stats['memory'])
    mqtt_send(topics['memory_usage'].format(container_id), container_stats['memory_usage'])
    mqtt_send(topics['net_io'].format(container_id), container_stats['net_io'])
    mqtt_send(topics['pids'].format(container_id), container_stats['pids'])
    mqtt_send(topics['block_io'].format(container_id), container_stats['block_io'])
    mqtt_send(topics['size'].format(container_id), container_stats['size'])


def register_container(container_entry):
    container_name = container_entry['name']
    key = container_key(container_name)
    container_id = container_entry['id']
    container_image = container_entry['image']
    container_entry = container_entry | {"key": key}

    base_config = {
        'availability_topic': f'{MQTT_TOPIC_PREFIX}/{DOCKER2MQTT_HOSTNAME}/status',
        'expire_after': STATS_DELAY_SECONDS * 60,
        'device': {
            "name": container_name,
            "manufacturer": "Docker",
            "model": container_image,
            "identifiers": f"{DOCKER2MQTT_HOSTNAME}:{key}",
            "sw_version": container_image,
            "via_device": "docker2mqtt",
        },
    }

    state_entity_config = base_config | {
        "qos": MQTT_QOS,
        "state_topic": topics['state'].format(key),
        "name": f"{container_name} State",
        "unique_id": f"{DOCKER2MQTT_HOSTNAME}.{key}.state",
        "icon": "mdi:chart-line-variant"
    }
    state_discovery = topics['home_assistant']['state'].format(key)
    mqtt_send(state_discovery, json.dumps(state_entity_config), retain=True)

    status_entity_config = base_config | {
        "qos": MQTT_QOS,
        "state_topic": topics['status'].format(key),
        "name": f"{container_name} Status",
        "unique_id": f"{DOCKER2MQTT_HOSTNAME}.{key}.status",
        "icon": "mdi:chart-line-variant"
    }
    status_discovery = topics['home_assistant']['status'].format(key)
    mqtt_send(status_discovery, json.dumps(status_entity_config), retain=True)

    image_entity_config = base_config | {
        "qos": MQTT_QOS,
        "state_topic": topics['image'].format(key),
        "name": f"{container_name} Image",
        "unique_id": f"{DOCKER2MQTT_HOSTNAME}.{key}.image",
        "entity_category": "diagnostic",
        "icon": "mdi:docker"
    }
    image_discovery = topics['home_assistant']['image'].format(key)
    mqtt_send(image_discovery, json.dumps(image_entity_config), retain=True)

    cpu_entity_config = base_config | {
        "qos": MQTT_QOS,
        "state_topic": topics['cpu'].format(key),
        "name": f"{container_name} CPU Usage",
        "unique_id": f"{DOCKER2MQTT_HOSTNAME}.{key}.cpu",
        "unit_of_measurement": "%",
        "entity_category": "diagnostic",
        "icon": "mdi:cpu-64-bit"
    }
    cpu_discovery = topics['home_assistant']['cpu'].format(key)
    mqtt_send(cpu_discovery, json.dumps(cpu_entity_config), retain=True)

    overall_cpu_entity_config = base_config | {
        "qos": MQTT_QOS,
        "state_topic": topics['1cpu'].format(key),
        "name": f"{container_name} Overall CPU Usage",
        "unique_id": f"{DOCKER2MQTT_HOSTNAME}.{key}.1_cpu",
        "unit_of_measurement": "%",
        "entity_category": "diagnostic",
        "icon": "mdi:cpu-64-bit"
    }
    overall_cpu_discovery = topics['home_assistant']['1cpu'].format(key)
    mqtt_send(overall_cpu_discovery, json.dumps(overall_cpu_entity_config), retain=True)

    memory_entity_config = base_config | {
        "qos": MQTT_QOS,
        "state_topic": topics['memory'].format(key),
        "name": f"{container_name} Memory",
        "unique_id": f"{DOCKER2MQTT_HOSTNAME}.{key}.memory",
        "unit_of_measurement": "%",
        "entity_category": "diagnostic",
        "icon": "mdi:memory"
    }
    memory_discovery = topics['home_assistant']['memory'].format(key)
    mqtt_send(memory_discovery, json.dumps(memory_entity_config), retain=True)

    memory_usage_entity_config = base_config | {
        "qos": MQTT_QOS,
        "state_topic": topics['memory_usage'].format(key),
        "name": f"{container_name} Memory Usage",
        "unique_id": f"{DOCKER2MQTT_HOSTNAME}.{key}.memory_usage",
        "entity_category": "diagnostic",
        "enabled_by_default": False,
        "icon": "mdi:memory"
    }
    memory_usage_discovery = topics['home_assistant']['memory_usage'].format(key)
    mqtt_send(memory_usage_discovery, json.dumps(memory_usage_entity_config), retain=True)

    net_io_entity_config = base_config | {
        "qos": MQTT_QOS,
        "state_topic": topics['net_io'].format(key),
        "name": f"{container_name} Network IO",
        "unique_id": f"{DOCKER2MQTT_HOSTNAME}.{key}.net_io",
        "entity_category": "diagnostic",
        "enabled_by_default": False,
        "icon": "mdi:lan-connect"
    }
    net_io_usage_discovery = topics['home_assistant']['net_io'].format(key)
    mqtt_send(net_io_usage_discovery, json.dumps(net_io_entity_config), retain=True)

    pids_entity_config = base_config | {
        "qos": MQTT_QOS,
        "state_topic": topics['pids'].format(key),
        "name": f"{container_name} PIDs",
        "unique_id": f"{DOCKER2MQTT_HOSTNAME}.{key}.pids",
        "entity_category": "diagnostic",
        "enabled_by_default": False,
        "icon": "mid:memory",
    }
    pids_discovery = topics['home_assistant']['pids'].format(key)
    mqtt_send(pids_discovery, json.dumps(pids_entity_config), retain=True)

    block_io_entity_config = base_config | {
        "qos": MQTT_QOS,
        "state_topic": topics['block_io'].format(key),
        "name": f"{container_name} Block IO",
        "unique_id": f"{DOCKER2MQTT_HOSTNAME}.{key}.block_io",
        "entity_category": "diagnostic",
        "enabled_by_default": False,
        "icon": "mdi:tape-drive"
    }
    block_io_discovery = topics['home_assistant']['block_io'].format(key)
    mqtt_send(block_io_discovery, json.dumps(block_io_entity_config), retain=True)

    size_entity_config = base_config | {
        "qos": MQTT_QOS,
        "state_topic": topics['size'].format(key),
        "name": f"{container_name} Writable Layer Size",
        "unique_id": f"{DOCKER2MQTT_HOSTNAME}.{key}.size",
        "unit_of_measurement": "B",
        "device_class": "data_size",
        "state_class": "measurement",
        "entity_category": "diagnostic",
        "enabled_by_default": True,
        "icon": "mdi:harddisk",
    }
    size_discovery = topics['home_assistant']['size'].format(key)
    mqtt_send(size_discovery, json.dumps(size_entity_config), retain=True)

    stop_entity_config = base_config | {
        "qos": MQTT_QOS,
        "command_topic": topics['commands'].format(key),
        "name": f"{container_name} Stop",
        "unique_id": f"{DOCKER2MQTT_HOSTNAME}.{key}.stop",
        "icon": "mdi:stop",
        "payload_press": payload_commands['stop']
    }
    stop_discovery = topics['home_assistant']['stop'].format(key)
    mqtt_send(stop_discovery, json.dumps(stop_entity_config), retain=True)

    start_entity_config = base_config | {
        "qos": MQTT_QOS,
        "command_topic": topics['commands'].format(key),
        "name": f"{container_name} Start",
        "unique_id": f"{DOCKER2MQTT_HOSTNAME}.{key}.start",
        "icon": "mdi:play",
        "payload_press": payload_commands['start']
    }
    start_discovery = topics['home_assistant']['start'].format(key)
    mqtt_send(start_discovery, json.dumps(start_entity_config), retain=True)

    restart_entity_config = base_config | {
        "qos": MQTT_QOS,
        "command_topic": topics['commands'].format(key),
        "name": f"{container_name} Restart",
        "unique_id": f"{DOCKER2MQTT_HOSTNAME}.{key}.restart",
        "icon": "mdi:restart",
        "payload_press": payload_commands['restart']
    }
    restart_discovery = topics['home_assistant']['restart'].format(key)
    mqtt_send(restart_discovery, json.dumps(restart_entity_config), retain=True)

    mqtt_send(topics['commands'].format(key), '---')

    known_containers[key] = container_entry
    known_container_stats[key] = known_container_stats.get(key, empty_container_stats.copy())
    container_ids[container_id] = key
    post_info_for_container(key)


def register_all_containers():
    # Register containers with HA
    docker_ps = run(docker_ps_cmd, stdout=PIPE, text=True)
    for line in docker_ps.stdout.splitlines():
        container_status = json.loads(line)

        register_container({
            'id': container_status['ID'],
            'name': container_status['Names'],
            'image': container_status['Image'],
            'status': container_status['Status'],
            'state': container_status['State']
        })


def unregister_container(container_id):
    key = container_ids.get(container_id)
    if key is None:
        log(tag="Event", message=f"Not unregistering unknown container ID {container_id}")
        return

    # A same-name replacement may already exist. Do not let the old destroy
    # event delete the new container's HA device.
    current = known_containers.get(key)
    if current and current["id"] != container_id:
        container_ids.pop(container_id, None)
        return

    for entity_topic in topics['home_assistant'].keys():
        mqtt_send(topics['home_assistant'][entity_topic].format(key), '', retain=True)

    for state_topic in topics.keys():
        if isinstance(topics[state_topic], str):
            mqtt_send(topics[state_topic].format(key), '', retain=True)

    known_containers.pop(key, None)
    known_container_stats.pop(key, None)
    container_ids.pop(container_id, None)


'''
CONTROL THREADS
'''
def readline_thread():
    """Run docker events and continually read lines from it."""
    global docker_events

    with Popen(docker_events_cmd, stdout=PIPE, text=True) as proc:
        while True:
            docker_events.put(proc.stdout.readline())


def readline_stats_thread():
    """Run thread to read docker stats"""
    while True:
        try:
            docker_stats = run(docker_stats_cmd, stdout=PIPE, text=True)
            for line in docker_stats.stdout.splitlines():
                stats = json.loads(line)
                container_id = stats['Container']

                if container_ids.get(container_id) in known_container_stats.keys():
                    cpu = float(stats['CPUPerc'].replace("%", ""))
                    cpu_count = docker_system_stats['NCPU']

                    known_container_stats[container_ids[container_id]]['cpu'] = float(stats['CPUPerc'].replace("%", ""))

                    if cpu_count is not None and cpu_count > 0:
                        known_container_stats[container_ids[container_id]]['1_cpu'] = cpu / cpu_count

                    known_container_stats[container_ids[container_id]]['memory'] = float(stats['MemPerc'].replace("%", ""))
                    known_container_stats[container_ids[container_id]]['memory_usage'] = stats['MemUsage']
                    known_container_stats[container_ids[container_id]]['net_io'] = stats['NetIO']
                    known_container_stats[container_ids[container_id]]['pids'] = stats['PIDs']
                    known_container_stats[container_ids[container_id]]['block_io'] = stats['BlockIO']

            docker_ps = run(docker_ps_cmd, stdout=PIPE, text=True)
            for line in docker_ps.stdout.splitlines():
                container_status = json.loads(line)
                container_id = container_status['ID']

                key = container_ids.get(container_id)
                if key in known_container_stats.keys():
                    container_details = known_containers[key]

                    container_details['name'] = container_status['Names']
                    container_details['image'] = container_status['Image']
                    container_details['status'] = container_status['Status']
                    container_details['state'] = container_status['State']

            docker_ps_size = run(docker_ps_size_cmd, stdout=PIPE, text=True)
            for line in docker_ps_size.stdout.splitlines():
                container_status = json.loads(line)
                container_id = container_status['ID']
                key = container_ids.get(container_id)
                if key in known_container_stats:
                    size = container_status.get('Size', '0B')
                    # Docker reports this as e.g. "1.23GB (virtual 2.34GB)".
                    known_container_stats[key]['size'] = docker_size_to_bytes(size.split(' ', 1)[0])

            for key in list(known_containers.keys()):
                post_info_for_container(key)

            sleep(STATS_DELAY_SECONDS)
        except Exception as e:
            log(tag="Error", message=f"{e}")


def start_threads():
    global docker_events

    # Start the docker stats thread
    docker_stats_t = Thread(target=readline_stats_thread, daemon=True)
    docker_stats_t.start()

    # Start the docker events thread
    docker_events = queue.Queue()
    docker_events_t = Thread(target=readline_thread, daemon=True)
    docker_events_t.start()


def process_events():
    try:
        line = docker_events.get(timeout=1)
    except queue.Empty:
        return

    event = json.loads(line)
    if event['status'] not in WATCHED_EVENTS:
        return

    container_name = event['Actor']['Attributes']['name']
    container_id = event['id'][:12]

    if event['status'] == 'destroy':
        log(tag="Event", message=f'Container {container_name} has been destroyed.')
        unregister_container(container_id)
        return

    container_status = get_container_ps(container_id)

    if event['status'] == 'create':
        log(tag="Event", message=f'Container {container_name} has been created.')
        register_container({
            'name': container_name,
            'image': event['from'],
            'status': 'created',
            'state': 'off',
            'id': container_id
        })
    elif event['status'] == 'rename':
        old_name = event['Actor']['Attributes'].get('oldName', '').lstrip("/")
        old_key = container_key(old_name)
        new_key = container_key(container_name)

        if old_key != new_key and old_key in known_containers:
            for entity_topic in topics['home_assistant'].keys():
                mqtt_send(topics['home_assistant'][entity_topic].format(old_key), '', retain=True)
            for state_topic in topics.keys():
                if isinstance(topics[state_topic], str):
                    mqtt_send(topics[state_topic].format(old_key), '', retain=True)
            known_containers.pop(old_key, None)
            known_container_stats.pop(old_key, None)

        log(tag="Event", message=f"Container {old_name} renamed to {container_name}")
        register_container({
            'name': container_name,
            'image': event['from'],
            'status': 'created',
            'state': 'off',
            'id': container_id
        })
    else:
        key = container_ids.get(container_id)
        if key in known_containers and container_status is not None:
            known_containers[key]['status'] = container_status['Status']
            known_containers[key]['state'] = container_status['State']
            known_containers[key]['image'] = container_status['Image']
            known_containers[key]['name'] = container_status['Names']

    key = container_ids.get(container_id)
    if key in known_containers:
        post_info_for_container(key)

def go():
    mqtt_send(f'{MQTT_TOPIC_PREFIX}/{DOCKER2MQTT_HOSTNAME}/status', 'online', retain=True)
    process_events()


if __name__ == '__main__':
    setup_mqtt()

    mqtt_connect(True)

    get_docker_system_stats()

    start_threads()

    # Loop and wait for new events
    while True:
        try:
            if not connected_to_mqtt:
                log(tag="MQTT", message="MQTT not connected: Retrying...")

                setup_mqtt()
                if not mqtt_connect():
                    sleep(10)
                else:
                    connected_to_mqtt = True
            else:
                go()
        except Exception as e:
            log(tag="Error", message=f"{e}")