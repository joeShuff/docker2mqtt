# docker2mqtt - Deliver docker status information over MQTT.

This program uses `docker events` to watch for changes in your docker containers, and delivers current status to MQTT. It will also publish Home Assistant MQTT Discovery messages so that container sensors and controls automatically show up in Home Assistant.

## Features

- Stable Home Assistant devices and entity IDs based on the Docker container name rather than the container ID.
- Recreating a container with the same name updates the existing Home Assistant device instead of creating a duplicate.
- Start, stop and restart buttons control the current container ID behind the stable MQTT topic.
- Removes retained Home Assistant discovery and state topics created by older ID-based versions during migration.
- Reports the container's writable-layer size, making it easy to spot containers that are unexpectedly consuming disk space.

# Running

Use docker to launch this. Please note that you must give it access to your docker socket, which is typically located at `/var/run/docker.sock`. A typical invocation is:

    docker run --network mqtt -e MQTT_HOST=mosquitto -v /var/run/docker.sock:/var/run/docker.sock ghcr.io/denizenn/docker2mqtt

You can also use docker compose:
```yaml
version: '3'
services:
  docker2mqtt:
    container_name: docker2mqtt
    image: ghcr.io/denizenn/docker2mqtt
    environment:
    - DEBUG=0
    - MQTT_DEBUG=0
    - DOCKER2MQTT_HOSTNAME=my_docker_host
    - HOMEASSISTANT_PREFIX=homeassistant
    - MQTT_CLIENT_ID=docker2mqtt
    - MQTT_HOST=mosquitto
    - MQTT_PORT=1883
    - MQTT_USER=username
    - MQTT_PASSWD=password
    - MQTT_TIMEOUT=30
    - MQTT_TOPIC_PREFIX=docker
    - MQTT_QOS=1
    - STATS_DELAY=5
    restart: always
    volumes:
    - type: volume
      source: /var/run/docker.sock
      target: /var/run/docker.sock
```

# Configuration

You can use environment variables to control the behavior.

| Variable                  | Default            | Description                                                                                                           |
|---------------------------|--------------------|-----------------------------------------------------------------------------------------------------------------------|
| `DEBUG`                   | 0                  | Set to `1` to enable additional debug logging.                                                                        |
| `MQTT_DEBUG`              | 0                  | Set to `1` to enable MQTT message logging.                                                                            |
| `DOCKER2MQTT_HOSTNAME`    | Container Hostname | The hostname of your docker host. This will be the container's hostname by default, you probably want to override it. |
| `HOMEASSISTANT_PREFIX`    | `homeassistant`    | The prefix for Home Assistant discovery. Must be the same as `discovery_prefix` in your Home Assistant configuration. |
| `MQTT_CLIENT_ID`          | `docker2mqtt`      | The client id to send to the MQTT broker.                                                                             |
| `MQTT_HOST`               | `localhost`        | The MQTT broker to connect to.                                                                                        |
| `MQTT_PORT`               | `1883`             | The port on the broker to connect to.                                                                                 |
| `MQTT_USER`               | ``                 | The user to send to the MQTT broker. Leave unset to disable authentication.                                           |
| `MQTT_PASSWD`             | ``                 | The password to send to the MQTT broker. Leave unset to disable authentication.                                       |
| `MQTT_TIMEOUT`            | `30`               | The timeout for the MQTT connection.                                                                                  |
| `MQTT_TOPIC_PREFIX`       | `ping`             | The MQTT topic prefix. With the default data will be published to `ping/<hostname>`.                                  |
| `MQTT_QOS`                | `1`                | The MQTT QOS level                                                                                                    |
| `STATS_DELAY`             | `5`                | Seconds between the `docker stats` command being ran and reported via MQTT.                                           |
 

# Consuming The Data

Data is published to various topics of the style `docker/<CONTAINER_NAME>/<container_stat>`. The container name is normalized for use in MQTT topics. The container name is deliberately used instead of the Docker container ID because Docker IDs change when a container is recreated.

The `size` topic reports the writable layer size in bytes. This is the space written by the container itself and does not include the image's shared read-only layers.

![Screenshot showing example mqtt topics](art/mqtt_topics.png)

# Home Assistant

After you start the service, devices should show up in Home Assistant immediately. Look for devices with Manufacturer `Docker`.
All the metadata, stats and controls for each container are entities in the device.

### Container recreation

The Home Assistant device identifier and entity `unique_id` are derived from the Docker host and container name. If Docker Compose recreates a container, the new Docker ID is associated with the existing identity, so Home Assistant does not create `_2`, `_3`, etc. entities.

The application also subscribes to the old ID-based discovery topics on startup and clears retained messages from older releases. This is a one-time migration cleanup; old Docker IDs are not used for new discovery topics.

![Screenshot of Home Assistant sensor showing status and attributes.](art/ha_screenshot.png)
