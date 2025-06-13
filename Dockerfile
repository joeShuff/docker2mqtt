FROM python:3.10-slim-buster

COPY requirements.txt requirements.txt

RUN pip3 install -r requirements.txt

# Copy files into place
COPY . .

# Set the entrypoint
CMD ["python3", "docker2mqtt.py"]
