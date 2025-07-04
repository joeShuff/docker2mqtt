FROM python:3.10-slim-buster

ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8

# Install system tools needed for Docker CLI and Python builds
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    tar \
    && rm -rf /var/lib/apt/lists/*

# Set desired Docker CLI version and architecture
ENV DOCKER_CLI_VERSION=24.0.6
ENV DOCKER_CLI_ARCH=x86_64

# Download and install Docker CLI for amd64
RUN curl -fsSL https://download.docker.com/linux/static/stable/${DOCKER_CLI_ARCH}/docker-${DOCKER_CLI_VERSION}.tgz \
    | tar -xz -C /usr/local/bin --strip-components=1 docker/docker

COPY requirements.txt requirements.txt

RUN pip3 install -r requirements.txt

# Copy files into place
COPY . .

# Set the entrypoint
CMD ["python3", "docker2mqtt.py"]
