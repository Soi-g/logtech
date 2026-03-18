#!/bin/bash
# ============================================================
# EC2 #1 Frontend — Flask + Thymeleaf + loadgenerator
# OTel Collector Agent runs as systemd service (Datadog-style)
#
# Terraform templatefile variables:
#   ${gateway_endpoint}   — platform EC2 collector host:port (direct, port 14317)
#   ${backend_private_ip} — Spring Boot EC2 private IP
#   ${environment}        — deployment.environment label
# ============================================================
set -euxo pipefail

# ─── System packages ─────────────────────────────────────────
apt-get update -y || apt-get update -y --fix-missing || true
apt-get install -y docker.io wget curl ca-certificates

# Docker Compose v2 plugin
mkdir -p /usr/local/lib/docker/cli-plugins
curl -SL "https://github.com/docker/compose/releases/download/v2.27.0/docker-compose-linux-x86_64" \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

systemctl enable docker
systemctl start docker

# Set EC2 hostname so syslog and host.name reflect the instance name
hostnamectl set-hostname "${instance_name}"

# ─── OTel Collector — binary install ─────────────────────────
OTEL_VERSION="0.147.0"

cd /tmp
wget -O otelcol-contrib.tar.gz \
  "https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/v$${OTEL_VERSION}/otelcol-contrib_$${OTEL_VERSION}_linux_amd64.tar.gz"

mkdir -p /opt/otelcol
tar -xzf otelcol-contrib.tar.gz -C /opt/otelcol
ln -sf /opt/otelcol/otelcol-contrib /usr/local/bin/otelcol

# Create dedicated user; add to docker group for docker_stats receiver
useradd --system --no-create-home --shell /usr/sbin/nologin otelcol || true
usermod -aG docker,adm otelcol

mkdir -p /etc/otelcol /var/lib/otelcol/file_storage
chown -R otelcol:otelcol /etc/otelcol /var/lib/otelcol

# ─── OTel Collector config ────────────────────────────────────
# ${gateway_endpoint} is substituted by Terraform templatefile before shell runs.
# The single-quoted heredoc (<<'OTELEOF') prevents shell variable expansion,
# so the already-substituted literal value is written directly into the YAML.
cat > /etc/otelcol/config.yaml << 'OTELEOF'
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317

  docker_stats:
    endpoint: unix:///var/run/docker.sock
    excluded_containers:
      - loadgenerator

  hostmetrics:
    scrapers:
      cpu:
      memory:
      disk:
      network:
      filesystem:
        exclude_fs_types:
          match_type: strict
          fs_types:
            - tmpfs
            - devtmpfs
            - overlay
            - squashfs
            - proc
            - sysfs
            - cgroup
            - cgroup2
            - nsfs
        exclude_mount_points:
          match_type: regexp
          mount_points:
            - ^/proc.*
            - ^/sys.*
            - ^/dev.*
            - ^/run.*
            - ^/var/lib/docker/.*

  filelog:
    include:
      - /var/log/syslog
      - /var/log/auth.log
    start_at: end
    storage: file_storage
    fingerprint_size: 1kb
    operators:
      - type: regex_parser
        regex: '^(?P<ts>[A-Z][a-z]{2}\s+\d+\s+\d+:\d+:\d+)\s+(?P<hostname>\S+)\s+(?P<proc>[^\[:]+)(\[(?P<pid>\d+)\])?:\s+(?P<msg>.*)$'
      - type: move
        from: attributes.msg
        to: body
        if: 'attributes.msg != nil'
      - type: move
        from: attributes.hostname
        to: resource["host.name"]
        if: 'attributes.hostname != nil'

extensions:
  file_storage:
    directory: /var/lib/otelcol/file_storage
    create_directory: true

exporters:
  debug:
    verbosity: basic

  otlp/gateway:
    endpoint: "${gateway_endpoint}"
    tls:
      insecure: true
    timeout: 30s
    sending_queue:
      enabled: true
      num_consumers: 4
      queue_size: 2048
    retry_on_failure:
      enabled: true
      initial_interval: 5s
      max_interval: 30s

processors:
  memory_limiter:
    check_interval: 1s
    limit_mib: 400
    spike_limit_mib: 100

  batch:
    timeout: 5s
    send_batch_size: 1024

  resource/app:
    attributes:
      - key: service.namespace
        value: "todolist"
        action: upsert
      - key: deployment.environment
        value: "${environment}"
        action: upsert
      - key: source
        value: "app"
        action: upsert

  resource/container:
    attributes:
      - key: service.namespace
        value: "todolist"
        action: upsert
      - key: deployment.environment
        value: "${environment}"
        action: upsert
      - key: source
        value: "container"
        action: upsert

  resource/host:
    attributes:
      - key: service.namespace
        value: "todolist"
        action: upsert
      - key: deployment.environment
        value: "${environment}"
        action: upsert
      - key: source
        value: "host"
        action: upsert

  resource/syslog:
    attributes:
      - key: service.namespace
        value: "todolist"
        action: upsert
      - key: deployment.environment
        value: "${environment}"
        action: upsert
      - key: source
        value: "sys"
        action: upsert
      - key: log.source
        value: "syslog"
        action: upsert
      - key: service.name
        value: "host-logs"
        action: insert
      - key: host.name
        value: "${instance_name}"
        action: upsert

service:
  extensions: [file_storage]

  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, resource/app, batch]
      exporters: [otlp/gateway]

    metrics/app:
      receivers: [otlp]
      processors: [memory_limiter, resource/app, batch]
      exporters: [otlp/gateway]

    metrics/container:
      receivers: [docker_stats]
      processors: [memory_limiter, resource/container, batch]
      exporters: [otlp/gateway]

    metrics/host:
      receivers: [hostmetrics]
      processors: [memory_limiter, resource/host, batch]
      exporters: [otlp/gateway]

    logs/app:
      receivers: [otlp]
      processors: [memory_limiter, resource/app, batch]
      exporters: [otlp/gateway]

    logs/host:
      receivers: [filelog]
      processors: [memory_limiter, resource/syslog, batch]
      exporters: [otlp/gateway]
OTELEOF

# ─── OTel Collector systemd service ──────────────────────────
cat > /etc/systemd/system/otelcol.service << 'SVCEOF'
[Unit]
Description=OpenTelemetry Collector Agent
After=network.target docker.service
Requires=docker.service

[Service]
User=otelcol
Group=docker
ExecStart=/usr/local/bin/otelcol --config=/etc/otelcol/config.yaml
Restart=always
RestartSec=5
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable otelcol
systemctl start otelcol

# ─── Build Flask image from source (로그 브릿지 + Python 3.12 패치 포함) ──────
mkdir -p /opt/flask-src/templates

cat > /opt/flask-src/Dockerfile << 'DOCKEREOF'
FROM docker.io/python:3.12-slim
WORKDIR /app
COPY . /app
RUN pip install -r requirements.txt
EXPOSE 5000
ENV OTEL_LOGS_EXPORTER="otlp"
ENV OTEL_METRICS_EXPORTER="otlp"
ENV OTEL_TRACES_EXPORTER="otlp"
ENV OTEL_EXPORTER_OTLP_ENDPOINT="localhost:4317"
CMD ["opentelemetry-instrument","python","app.py"]
DOCKEREOF

cat > /opt/flask-src/requirements.txt << 'REQEOF'
setuptools<80
Faker==28.4.1
Flask==3.0.3
opentelemetry-api==1.26.0
opentelemetry-distro==0.47b0
opentelemetry-sdk==1.26.0
opentelemetry-exporter-otlp-proto-grpc==1.26.0
opentelemetry-instrumentation-flask==0.47b0
opentelemetry-instrumentation-requests==0.47b0
opentelemetry-instrumentation-logging==0.47b0
requests==2.32.3
REQEOF

cat > /opt/flask-src/app.py << 'APPEOF'
from flask import Flask, render_template, request, jsonify, redirect, url_for

import logging
import requests
import os

from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter

# OTel 로깅 브릿지 — LoggingHandler를 root logger에 직접 추가
_lp = LoggerProvider()
_lp.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
logging.getLogger().addHandler(LoggingHandler(level=logging.NOTSET, logger_provider=_lp))

app = Flask(__name__)
logging.getLogger(__name__)
logging.basicConfig(format='%(levelname)s:%(name)s:%(module)s:%(message)s', level=logging.INFO)

app.config['BACKEND_URL'] = 'http://localhost:8080/todos/'
app.config['BACKEND_URL'] = os.getenv('BACKEND_URL', app.config['BACKEND_URL'])

@app.route('/')
def index():
    backend_url = app.config['BACKEND_URL']
    response = requests.get(backend_url)
    logging.info("GET %s/todos/",backend_url)
    if response.status_code == 200:
        logging.info("Response: %s", response.text)
        todos = response.json()
    return render_template('index.html', todos=todos)

@app.route('/add', methods=['POST'])
def add():
    if request.method == 'POST':
        new_todo = request.form['todo']
        logging.info("POST  %s/todos/%s",app.config['BACKEND_URL'],new_todo)
        response = requests.post(app.config['BACKEND_URL']+new_todo)
    return redirect(url_for('index'))

@app.route('/delete', methods=['POST'])
def delete():
    if request.method == 'POST':
        delete_todo = request.form['todo']
        logging.info("POST  %s/todos/%s",app.config['BACKEND_URL'],delete_todo)
        print(delete_todo)
    response = requests.delete(app.config['BACKEND_URL']+delete_todo)
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(host='0.0.0.0')
APPEOF

cat > /opt/flask-src/templates/index.html << 'HTMLEOF'

<!DOCTYPE HTML>
<html>
<head>
    <title>Most beautiful of all Python Todo Lists</title>
    <meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />
</head>
<link rel="stylesheet" href="https://www.w3schools.com/w3css/4/w3.css"/>
<body>

<div class="w3-row-padding w3-section" style="margin: 0px auto; width: 800px;" >

    <header class="w3-container w3-teal">
        <h1>Most beautiful of all Python Todo Lists</h1>
    </header>

    <h2>Add a ToDo</h2>

    <div>
        <table class="w3-table-all w3-hoverable">
            <tr class="w3-teal">
                <th width="600px">Item</th>
                <th>Action</th>
            </tr>
            <tr>
                <form action="/add" method="post">
                    <td>
                        <label for="description">Description:</label>
                        <input type="text" id="todo" name="todo" required>
                    </td>
                    <td>
                        <input class="w3-button w3-black" type="submit" value="Submit!"/>
                    </td>
                </form>

            </tr>
        </table>
    </div>

    {% if todos %}

    <table class="w3-table-all w3-hoverable">
        <tr class="w3-teal">
            <th width="600px">Item</th>
            <th>Action</th>
        </tr>
        {% for todo in todos %}
        <tr>
            <td>{{ todo }}</td>
            <td>
                <form action="/delete" method="post">
                    <input type="hidden" id="todo" name="todo" value="{{ todo }}" />
                    <input class="w3-button w3-black" type="submit" value="Done!"/>
                </form>
            </td>
        </tr>
        {% endfor %}
    </table>


    {% else %}

    <table class="w3-table-all w3-hoverable">
        <tr class="w3-teal">
            <th width="600px">Item</th>
            <th>Action</th>
        </tr>
        <tr>
            <td>You have no ToDos :-)</td>
            <td>None</td>
        </tr>
    </table>

    {% endif %}

</div>
HTMLEOF

docker build -t todoui-flask:local /opt/flask-src/

# ─── Docker Compose — Flask + Thymeleaf + loadgenerator ──────
# Apps reach the host-side otelcol via host.docker.internal:4317
# backend_private_ip is substituted by Terraform templatefile

mkdir -p /opt/app
cat > /opt/app/docker-compose.yaml << 'COMPOSEEOF'
services:

  todoui-flask:
    image: todoui-flask:local
    restart: unless-stopped
    ports:
      - "5001:5000"
    extra_hosts:
      - "host.docker.internal:host-gateway"
    environment:
      - BACKEND_URL=http://${backend_private_ip}:8080/todos/
      - OTEL_EXPORTER_OTLP_ENDPOINT=http://host.docker.internal:4317
      - OTEL_EXPORTER_OTLP_PROTOCOL=grpc
      - OTEL_METRICS_EXPORTER=otlp
      - OTEL_LOGS_EXPORTER=otlp
      - OTEL_SERVICE_NAME=flask
      - OTEL_RESOURCE_ATTRIBUTES=service.namespace=todolist,deployment.environment=${environment},service.name=flask

  todoui-thymeleaf:
    image: ghcr.io/lftraining/lfs148-code-todoui-thymeleaf:v2404
    restart: unless-stopped
    ports:
      - "8090:8090"
    extra_hosts:
      - "host.docker.internal:host-gateway"
    environment:
      - BACKEND_URL=http://${backend_private_ip}:8080/
      - OTEL_EXPORTER_OTLP_ENDPOINT=http://host.docker.internal:4317
      - OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=http://host.docker.internal:4317
      - OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=grpc
      - OTEL_EXPORTER_OTLP_METRICS_PROTOCOL=grpc
      - OTEL_EXPORTER_OTLP_LOGS_PROTOCOL=grpc
      - OTEL_METRICS_EXPORTER=otlp,logging-otlp
      - OTEL_LOGS_EXPORTER=otlp,logging-otlp
      - OTEL_RESOURCE_ATTRIBUTES=service.namespace=todolist,deployment.environment=${environment},service.name=thymeleaf

  loadgenerator:
    image: ghcr.io/lftraining/lfs148-code-simple-generator:v2404
    restart: unless-stopped
    depends_on:
      - todoui-flask
      - todoui-thymeleaf
COMPOSEEOF

# Start app containers
docker compose -f /opt/app/docker-compose.yaml up -d
