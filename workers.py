"""Background threads (docker events, docker stats) and docker event processing."""
import json
import queue
from subprocess import PIPE, Popen
from threading import Thread
from time import sleep

from . import docker_cli
from .config import STATS_DELAY_SECONDS
from .containers import clear_container, post_info_for_container, register_container, unregister_container
from .state import container_ids, docker_events, docker_system_stats, known_container_stats, known_containers
from .util import container_key, docker_size_to_bytes, error, info

WATCHED_EVENTS = ('create', 'destroy', 'die', 'pause', 'rename', 'start', 'stop', 'unpause')


'''
STATS
'''
def _update_resource_stats():
    for stats in docker_cli.get_container_stats():
        key = container_ids.get(stats['Container'])
        if key not in known_container_stats:
            continue

        container_stats = known_container_stats[key]
        cpu = float(stats['CPUPerc'].replace("%", ""))
        cpu_count = docker_system_stats['NCPU']

        container_stats['cpu'] = cpu
        if cpu_count is not None and cpu_count > 0:
            container_stats['1_cpu'] = round(cpu / cpu_count, 2)

        container_stats['memory'] = float(stats['MemPerc'].replace("%", ""))
        container_stats['memory_usage'] = stats['MemUsage']
        container_stats['net_io'] = stats['NetIO']
        container_stats['pids'] = stats['PIDs']
        container_stats['block_io'] = stats['BlockIO']


def _update_container_details():
    for container_status in docker_cli.list_containers():
        key = container_ids.get(container_status['ID'])
        if key in known_container_stats:
            details = known_containers[key]
            details['name'] = container_status['Names']
            details['image'] = container_status['Image']
            details['status'] = container_status['Status']
            details['state'] = container_status['State']


def _update_container_sizes():
    for container_status in docker_cli.list_containers_with_size():
        key = container_ids.get(container_status['ID'])
        if key in known_container_stats:
            size = container_status.get('Size', '0B')
            # Docker reports this as e.g. "1.23GB (virtual 2.34GB)".
            known_container_stats[key]['size'] = docker_size_to_bytes(size.split(' ', 1)[0])


def readline_stats_thread():
    """Periodically refresh container stats and publish them."""
    while True:
        try:
            _update_resource_stats()
            _update_container_details()
            _update_container_sizes()

            for key in list(known_containers.keys()):
                post_info_for_container(key)
        except Exception as e:
            error(f"{e}", tag="Stats")

        sleep(STATS_DELAY_SECONDS)


'''
EVENTS
'''
def readline_thread():
    """Run docker events and continually read lines from it."""
    with Popen(docker_cli.DOCKER_EVENTS_CMD, stdout=PIPE, text=True) as proc:
        while True:
            docker_events.put(proc.stdout.readline())


def start_threads():
    Thread(target=readline_stats_thread, daemon=True).start()
    Thread(target=readline_thread, daemon=True).start()


def _handle_rename(event, container_name, container_id):
    old_name = event['Actor']['Attributes'].get('oldName', '').lstrip("/")
    old_key = container_key(old_name)
    new_key = container_key(container_name)

    if old_key != new_key and old_key in known_containers:
        clear_container(old_key)
        known_containers.pop(old_key, None)
        known_container_stats.pop(old_key, None)

    info(f"Container {old_name} renamed to {container_name}", tag="Event")
    register_container({
        'name': container_name,
        'image': event['from'],
        'status': 'created',
        'state': 'off',
        'id': container_id,
    })


def process_events():
    """Process one queued docker event (waits up to one second for one)."""
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
        info(f'Container {container_name} has been destroyed.', tag="Event")
        unregister_container(container_id)
        return

    container_status = docker_cli.get_container_ps(container_id)

    if event['status'] == 'create':
        info(f'Container {container_name} has been created.', tag="Event")
        register_container({
            'name': container_name,
            'image': event['from'],
            'status': 'created',
            'state': 'off',
            'id': container_id,
        })
    elif event['status'] == 'rename':
        _handle_rename(event, container_name, container_id)
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
