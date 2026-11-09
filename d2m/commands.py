"""Handling of start/stop/restart commands received over MQTT."""
from subprocess import run

from . import docker_cli
from .config import MQTT_TOPIC_PREFIX
from .mqtt_client import mqtt_send
from .state import known_containers
from .topics import HOST_KEY
from .util import error, info, warning


def handle_command(msg):
    """Handle a message on <prefix>/<host>/<container>/commands."""
    if not msg.topic.startswith(f"{MQTT_TOPIC_PREFIX}/{HOST_KEY}/"):
        return

    # Expected command topic: <prefix>/<host>/<container key>/commands
    topic_parts = msg.topic.split("/")
    if len(topic_parts) != 4 or topic_parts[3] != "commands":
        return

    key = topic_parts[2]
    command = msg.payload.decode()

    if command == "---":
        return

    if key not in known_containers:
        warning(f"Cannot find container for key {key}", tag="Command")
        return

    container_id = known_containers[key]["id"]
    container_status = docker_cli.get_container_ps(container_id)
    if container_status is None:
        warning(f"Container {container_id} no longer exists", tag="Command")
        return

    container_running = container_status['State'] == "running"

    try:
        if command == "start":
            if not container_running:
                run(["docker", "start", container_id], check=True)
            else:
                info("Can't start an already running container", tag="Command")
        elif command == "stop":
            if container_running:
                run(["docker", "stop", container_id], check=True)
            else:
                info("Can't stop an already stopped container", tag="Command")
        elif command == "restart":
            run(["docker", "restart", container_id], check=True)
        else:
            warning(f"Unknown command: {command}", tag="Command")
    except Exception as e:
        error(f"Failed to {command} container {container_id}: {e}", tag="Command")

    mqtt_send(msg.topic, '---')
