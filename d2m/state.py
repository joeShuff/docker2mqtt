"""Shared runtime state. Modules mutate these objects in place; never rebind them."""
import queue

# container key -> container details (id, name, image, status, state, key)
known_containers = {}

# container key -> latest stats
known_container_stats = {}

# short docker container id -> container key
container_ids = {}

# output of `docker system info`
docker_system_stats = {}

# raw lines from `docker events`
docker_events = queue.Queue()

# state topic -> (last published payload, monotonic time of that publish).
# Used to skip publishing values that have not changed.
published = {}


def new_container_stats():
    """Placeholder stats used until the first `docker stats` run completes."""
    return {
        "cpu": 0,
        "1_cpu": 0,
        "memory": 0,
        "memory_usage": "0B / 0B",
        "net_io": "0B / 0B",
        "pids": 0,
        "block_io": "0B / 0B",
        "size": 0,
    }
