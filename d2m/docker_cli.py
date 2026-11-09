"""Thin wrappers around the `docker` command line."""
import json
from subprocess import PIPE, run

from .state import docker_system_stats

DOCKER_EVENTS_CMD = ['docker', 'events', '-f', 'type=container', '--format', '{{json .}}']
DOCKER_STATS_CMD = ['docker', 'stats', '-a', '--format', '{{json .}}', '--no-stream']
DOCKER_PS_CMD = ['docker', 'ps', '-a', '--format', '{{json .}}']
DOCKER_PS_SIZE_CMD = ['docker', 'ps', '-a', '--size', '--format', '{{json .}}']
DOCKER_SYSTEM_INFO_CMD = ['docker', 'system', 'info', '--format', '{{json .}}']


def _json_lines(cmd):
    proc = run(cmd, stdout=PIPE, text=True)
    return [json.loads(line) for line in proc.stdout.splitlines()]


def get_docker_system_stats():
    for info in _json_lines(DOCKER_SYSTEM_INFO_CMD):
        docker_system_stats.clear()
        docker_system_stats.update(info)
    return docker_system_stats


def get_container_ps(short_container_id):
    """Latest `docker ps` entry for one container (events lack accurate data), or None."""
    entries = _json_lines(['docker', 'ps', '-a', '--format', '{{json .}}', '-f', f"id={short_container_id}"])
    return entries[0] if entries else None


def list_containers():
    return _json_lines(DOCKER_PS_CMD)


def list_containers_with_size():
    return _json_lines(DOCKER_PS_SIZE_CMD)


def get_container_stats():
    return _json_lines(DOCKER_STATS_CMD)
