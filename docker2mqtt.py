#!/usr/bin/env python3
"""Listens to `docker events` and publishes container state and stats to MQTT / Home Assistant.

Module layout (see the d2m package):
    config       environment variables
    topics       MQTT / discovery topic layout
    state        shared runtime state
    docker_cli   wrappers around the docker command line
    mqtt_client  connection, publishing, stale discovery cleanup
    discovery    Home Assistant discovery payloads
    containers   register / update / unregister containers
    commands     start / stop / restart commands from MQTT
    workers      background threads and docker event processing
"""
from time import monotonic, sleep

from d2m import mqtt_client
from d2m.commands import handle_command
from d2m.config import CONTAINER_EXCLUDE, CONTAINER_INCLUDE
from d2m.containers import register_all_containers
from d2m.docker_cli import get_docker_system_stats
from d2m.mqtt_client import STATUS_TOPIC, mqtt_connect, mqtt_send, setup_mqtt
from d2m.util import error, info, warning
from d2m.workers import process_events, start_threads

# Re-announce "online" this often (covers the broker publishing our last-will after a
# dropped connection that paho then silently recovered from).
ONLINE_REPUBLISH_SECONDS = 30

_last_online = 0.0


def announce_online():
    global _last_online

    now = monotonic()
    if now - _last_online >= ONLINE_REPUBLISH_SECONDS:
        if mqtt_send(STATUS_TOPIC, 'online', retain=True):
            _last_online = now


def go():
    announce_online()
    process_events()


def main():
    mqtt_client.command_handler = handle_command

    if CONTAINER_INCLUDE:
        info(f"Only monitoring containers matching: {', '.join(CONTAINER_INCLUDE)}")
    if CONTAINER_EXCLUDE:
        info(f"Ignoring containers matching: {', '.join(CONTAINER_EXCLUDE)}")

    setup_mqtt()
    mqtt_connect(True, on_ready=register_all_containers, check_duplicate_host=True)

    get_docker_system_stats()

    start_threads()

    # Loop and wait for new events
    while True:
        try:
            if not mqtt_client.is_connected():
                warning("MQTT not connected: Retrying...", tag="MQTT")

                setup_mqtt()
                if not mqtt_connect(on_ready=register_all_containers):
                    sleep(10)
            else:
                go()
        except Exception as e:
            error(f"{e}")


if __name__ == '__main__':
    main()
