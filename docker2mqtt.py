#!/usr/bin/env python3
"""Listens to `docker system events` and sents container stop/start events to mqtt.
"""
import json
import queue
import re
from os import environ
from socket import gethostname
from subprocess import run, Popen, PIPE, call
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
WATCHED_EVENTS = ('create', 'destroy', 'die', 'pause', 'rename', 'start', 'stop', 'unpause')
STATS_DELAY_SECONDS = environ.get('STATS_DELAY', 5)

known_containers = {}
docker_events_cmd = ['docker', 'events', '-f', 'type=container', '--format', '{{json .}}']
docker_stats_cmd = ['docker', 'stats', '-a', '--format', '{{json .}}', '--no-stream']
docker_ps_cmd = ['docker', 'ps', '-a', '--format', '{{json .}}']
invalid_ha_topic_chars = re.compile(r'[^a-zA-Z0-9_-]')

mqtt = paho.mqtt.client.Client()

mqtt_cleaned = False
cleaned_topics = []

connected_to_mqtt = False

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
    "block_io": "0B / 0B"
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

        register_all_containers()
    else:
        connected_to_mqtt = False
        log(tag="MQTT", message=f'Failed to connect to MQTT server at {MQTT_HOST}:{MQTT_PORT} reason: {rc}')


def on_mqtt_message(client, userdata, msg):
    if MQTT_DEBUG:
        log(tag="MQTT", message="Message received->" + msg.topic + " > " + str(msg.payload.decode()))

    command = msg.payload.decode()
    container_id = msg.topic.split("/")[1]
    container_status = get_container_ps(container_id)

    '''
    This controls deleting the HA config for no longer existing containers.
    Cleans up old stuff ideally.
    '''
    if HOMEASSISTANT_PREFIX in msg.topic and msg.topic not in cleaned_topics:
        try:
            cleaned_topics.append(msg.topic)
            container_id = msg.topic.split("docker-")[1].split("/")[0]

            if container_id not in known_containers.keys():
                log(tag="Event", message=f"Clearing container {container_id} topic {msg.topic}")
                mqtt_send(msg.topic, "", retain=True, qos=0)

            return
        except Exception as e:
            log(tag="Error", message=f"Error deciding whether to delete this topic {e}")

    if container_status is None:
        return

    if command == "---":
        return

    container_running = container_status['State'] == "running"

    if command == "start" and not container_running:
        call(f'docker start {container_id}')
    elif command == "start":
        log(tag="Command", message="Can't start an already running container")

    if command == "stop" and container_running:
        call(f'docker stop {container_id}')
    elif command == "stop":
        log(tag="Command", message="Can't stop an already stopped container")

    if command == "restart":
        call(f'docker restart {container_id}')

    mqtt_send(msg.topic, "---")


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


def register_container(container_entry):
    container_name = container_entry['name']
    safe_container_name = container_name.lower().replace(" ", "_")
    container_id = container_entry['id']
    container_image = container_entry['image']

    base_config = {
        'availability_topic': f'{MQTT_TOPIC_PREFIX}/{DOCKER2MQTT_HOSTNAME}/status',
        'expire_after': STATS_DELAY_SECONDS * 60,
        'device': {
            "name": container_name,
            "manufacturer": "Docker",
            "model": container_image,
            "identifiers": container_id,
            "sw_version": container_image,
            "via_device": "docker2mqtt",
        },
    }

    state_entity_config = base_config | {
        "qos": MQTT_QOS,
        "state_topic": topics['state'].format(container_id),
        "name": f"{container_name} State",
        "unique_id": f"{container_id}.state",
        "entity_category": "config",
        "icon": "mdi:chart-line-variant"
    }
    state_discovery = topics['home_assistant']['state'].format(container_id)
    mqtt_send(state_discovery, json.dumps(state_entity_config), retain=True)

    status_entity_config = base_config | {
        "qos": MQTT_QOS,
        "state_topic": topics['status'].format(container_id),
        "name": f"{container_name} Status",
        "unique_id": f"{container_id}.status",
        "entity_category": "config",
        "icon": "mdi:chart-line-variant"
    }
    status_discovery = topics['home_assistant']['status'].format(container_id)
    mqtt_send(status_discovery, json.dumps(status_entity_config), retain=True)

    image_entity_config = base_config | {
        "qos": MQTT_QOS,
        "state_topic": topics['image'].format(container_id),
        "name": f"{container_name} Image",
        "unique_id": f"{container_id}.image",
        "entity_category": "config",
        "icon": "mdi:docker"
    }
    image_discovery = topics['home_assistant']['image'].format(container_id)
    mqtt_send(image_discovery, json.dumps(image_entity_config), retain=True)

    cpu_entity_config = base_config | {
        "qos": MQTT_QOS,
        "state_topic": topics['cpu'].format(container_id),
        "name": f"{container_name} CPU Usage",
        "unique_id": f"{container_id}.cpu",
        "unit_of_measurement": "%",
        "entity_category": "diagnostic",
        "icon": "mdi:cpu-64-bit"
    }
    cpu_discovery = topics['home_assistant']['cpu'].format(container_id)
    mqtt_send(cpu_discovery, json.dumps(cpu_entity_config), retain=True)

    overall_cpu_entity_config = base_config | {
        "qos": MQTT_QOS,
        "state_topic": topics['1cpu'].format(container_id),
        "name": f"{container_name} Overall CPU Usage",
        "unique_id": f"{container_id}.1_cpu",
        "unit_of_measurement": "%",
        "entity_category": "diagnostic",
        "icon": "mdi:cpu-64-bit"
    }
    overall_cpu_discovery = topics['home_assistant']['1cpu'].format(container_id)
    mqtt_send(overall_cpu_discovery, json.dumps(overall_cpu_entity_config), retain=True)

    memory_entity_config = base_config | {
        "qos": MQTT_QOS,
        "state_topic": topics['memory'].format(container_id),
        "name": f"{container_name} Memory",
        "unique_id": f"{container_id}.memory",
        "unit_of_measurement": "%",
        "entity_category": "diagnostic",
        "icon": "mdi:memory"
    }
    memory_discovery = topics['home_assistant']['memory'].format(container_id)
    mqtt_send(memory_discovery, json.dumps(memory_entity_config), retain=True)

    memory_usage_entity_config = base_config | {
        "qos": MQTT_QOS,
        "state_topic": topics['memory_usage'].format(container_id),
        "name": f"{container_name} Memory Usage",
        "unique_id": f"{container_id}.memory_usage",
        "entity_category": "diagnostic",
        "enabled_by_default": False,
        "icon": "mdi:memory"
    }
    memory_usage_discovery = topics['home_assistant']['memory_usage'].format(container_id)
    mqtt_send(memory_usage_discovery, json.dumps(memory_usage_entity_config), retain=True)

    net_io_entity_config = base_config | {
        "qos": MQTT_QOS,
        "state_topic": topics['net_io'].format(container_id),
        "name": f"{container_name} Network IO",
        "unique_id": f"{container_id}.net_io",
        "entity_category": "diagnostic",
        "enabled_by_default": False,
        "icon": "mdi:lan-connect"
    }
    net_io_usage_discovery = topics['home_assistant']['net_io'].format(container_id)
    mqtt_send(net_io_usage_discovery, json.dumps(net_io_entity_config), retain=True)

    pids_entity_config = base_config | {
        "qos": MQTT_QOS,
        "state_topic": topics['pids'].format(container_id),
        "name": f"{container_name} PIDs",
        "unique_id": f"{container_id}.pids",
        "entity_category": "diagnostic",
        "enabled_by_default": False,
        "icon": "mid:memory",
    }
    pids_discovery = topics['home_assistant']['pids'].format(container_id)
    mqtt_send(pids_discovery, json.dumps(pids_entity_config), retain=True)

    block_io_entity_config = base_config | {
        "qos": MQTT_QOS,
        "state_topic": topics['block_io'].format(container_id),
        "name": f"{container_name} Block IO",
        "unique_id": f"{container_id}.block_io",
        "entity_category": "diagnostic",
        "enabled_by_default": False,
        "icon": "mdi:tape-drive"
    }
    block_io_discovery = topics['home_assistant']['block_io'].format(container_id)
    mqtt_send(block_io_discovery, json.dumps(block_io_entity_config), retain=True)

    stop_entity_config = base_config | {
        "qos": MQTT_QOS,
        "command_topic": topics['commands'].format(container_id),
        "name": f"{container_name} Stop",
        "unique_id": f"{container_id}.stop",
        "icon": "mdi:stop",
        "payload_press": payload_commands['stop']
    }
    stop_discovery = topics['home_assistant']['stop'].format(container_id)
    mqtt_send(stop_discovery, json.dumps(stop_entity_config), retain=True)

    start_entity_config = base_config | {
        "qos": MQTT_QOS,
        "command_topic": topics['commands'].format(container_id),
        "name": f"{container_name} Start",
        "unique_id": f"{container_id}.start",
        "icon": "mdi:play",
        "payload_press": payload_commands['start']
    }
    start_discovery = topics['home_assistant']['start'].format(container_id)
    mqtt_send(start_discovery, json.dumps(start_entity_config), retain=True)

    restart_entity_config = base_config | {
        "qos": MQTT_QOS,
        "command_topic": topics['commands'].format(container_id),
        "name": f"{container_name} Restart",
        "unique_id": f"{container_id}.restart",
        "icon": "mdi:restart",
        "payload_press": payload_commands['restart']
    }
    restart_discovery = topics['home_assistant']['restart'].format(container_id)
    mqtt_send(restart_discovery, json.dumps(restart_entity_config), retain=True)

    mqtt_send(topics['commands'].format(container_id), '---')

    known_containers[container_id] = container_entry
    known_container_stats[container_id] = empty_container_stats.copy()

    post_info_for_container(container_id)


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

    subscribe_to_clean_mqtt()


def subscribe_to_clean_mqtt():
    global mqtt_cleaned

    if not mqtt_cleaned:
        mqtt_cleaned = True
        for entity_topic in topics['home_assistant'].keys():
            formatted = topics['home_assistant'][entity_topic].format("+").replace("docker-", "")
            mqtt.subscribe(topic=formatted)


def unregister_container(short_id):
    if short_id not in known_containers.keys():
        log(tag="Error", message="Not unregistering unknown container")
        return

    container = known_containers[short_id]

    for entity_topic in topics['home_assistant'].keys():
        formatted = topics['home_assistant'][entity_topic].format(short_id)
        mqtt_send(formatted, '', retain=True)

    for state_topic in topics.keys():
        if isinstance(topics[state_topic], str):
            mqtt_send(topics[state_topic].format(short_id), '', retain=True)

    del(known_containers[short_id])


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

                if container_id in known_container_stats.keys():
                    cpu = float(stats['CPUPerc'].replace("%", ""))
                    cpu_count = docker_system_stats['NCPU']

                    known_container_stats[container_id]['cpu'] = float(stats['CPUPerc'].replace("%", ""))

                    if cpu_count is not None and cpu_count > 0:
                        known_container_stats[container_id]['1_cpu'] = cpu / cpu_count

                    known_container_stats[container_id]['memory'] = float(stats['MemPerc'].replace("%", ""))
                    known_container_stats[container_id]['memory_usage'] = stats['MemUsage']
                    known_container_stats[container_id]['net_io'] = stats['NetIO']
                    known_container_stats[container_id]['pids'] = stats['PIDs']
                    known_container_stats[container_id]['block_io'] = stats['BlockIO']

            docker_ps = run(docker_ps_cmd, stdout=PIPE, text=True)
            for line in docker_ps.stdout.splitlines():
                container_status = json.loads(line)
                container_id = container_status['ID']

                if container_id in known_container_stats.keys():
                    container_details = known_containers[container_id]

                    container_details['name'] = container_status['Names']
                    container_details['image'] = container_status['Image']
                    container_details['status'] = container_status['Status']
                    container_details['state'] = container_status['State']

            for container_id in known_containers.keys():
                post_info_for_container(container_id)

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
    global docker_events, known_containers

    # Collect and process an event from `docker events`
    try:
        line = docker_events.get(timeout=1)
    except queue.Empty:
        # No data right now, just move along.
        return

    event = json.loads(line)
    if event['status'] not in WATCHED_EVENTS:
        return

    container_name = event['Actor']['Attributes']['name']
    container_id = event['id']
    short_container_id = container_id[:12]
    container_status = get_container_ps(short_container_id)

    if event['status'] == 'create':
        # Cancel any previous pending destroys and add this to known_containers.
        log(tag="Event", message=f'Container {container_name} has been created.')

        register_container({
            'name': container_name,
            'image': event['from'],
            'status': 'created',
            'state': 'off',
            'id': short_container_id
        })
    elif event['status'] == 'destroy':
        # Add this container to pending_destroy_operations.
        log(tag="Event", message=f'Container {container_name} has been destroyed.')
        unregister_container(short_container_id)
    elif event['status'] == "rename":
        old_name = event['Actor']['Attributes']['oldName']
        log(tag="Event", message=f"Container {old_name} renamed to {container_name}")
        register_container({
            'name': container_name,
            'image': event['from'],
            'status': 'created',
            'state': 'off',
            'id': short_container_id
        })
    else:
        if short_container_id in known_containers.keys():
            known_containers[short_container_id]['status'] = container_status['Status']
            known_containers[short_container_id]['state'] = container_status['State']
            known_containers[short_container_id]['image'] = container_status['Image']
            known_containers[short_container_id]['name'] = container_status['Names']

    post_info_for_container(short_container_id)


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