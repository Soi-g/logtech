#!/bin/bash
# ============================================================
# EC2 #1 Frontend — Flask + Thymeleaf + loadgenerator
# OTel Collector Agent runs as systemd service (Datadog-style)
#
# Terraform templatefile variables:
#   ${gateway_endpoint}   — platform NLB host:4317 (gRPC → collector 14317)
#   ${backend_private_ip} — Spring Boot EC2 private IP
#   ${environment}        — deployment.environment label
# ============================================================
set -euxo pipefail

# ─── System packages ─────────────────────────────────────────
apt-get update -y
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
        if: 'body matches "^[A-Z][a-z]{2}\\s"'
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
      storage: file_storage
      num_consumers: 4
      queue_size: 10000
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

  transform/container_hostname:
    metric_statements:
      - context: resource
        statements:
          - set(attributes["host.name"], attributes["container.hostname"]) where attributes["container.hostname"] != nil

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
      - key: service.name
        value: "host-metrics"
        action: insert
      - key: host.name
        value: "${instance_name}"
        action: insert

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
      processors: [memory_limiter, resource/container, transform/container_hostname, batch]
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

# ─── Docker Compose — Flask (image) + Thymeleaf + loadgenerator ──────
mkdir -p /opt/app
cat > /opt/app/docker-compose.yaml << 'COMPOSEEOF'
services:

  todoui-flask:
    image: ichanee/todoui-flask:latest
    restart: unless-stopped
    ports:
      - "5001:5000"
    extra_hosts:
      - "host.docker.internal:host-gateway"
    environment:
      - BACKEND_URL=http://${backend_private_ip}:8080/todos/
      - OTEL_EXPORTER_OTLP_ENDPOINT=host.docker.internal:4317
      - OTEL_EXPORTER_OTLP_INSECURE=true
      - OTEL_PYTHON_LOG_CORRELATION=true
      - OTEL_EXPORTER_OTLP_PROTOCOL=grpc
      - OTEL_TRACES_EXPORTER=otlp
      - OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=grpc
      - OTEL_METRICS_EXPORTER=otlp
      - OTEL_LOGS_EXPORTER=otlp
      - OTEL_EXPORTER_OTLP_LOGS_PROTOCOL=grpc
      - OTEL_RESOURCE_ATTRIBUTES=service.namespace=todolist,deployment.environment=${environment},service.name=flask
      - OTEL_SEMCONV_STABILITY_OPT_IN=http

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

# Wait for Docker to be ready
until docker info >/dev/null 2>&1; do echo "Waiting for Docker..."; sleep 3; done

# Start app containers (pull flask image from registry)
cd /opt/app
for i in 1 2 3 4 5; do
  if docker compose -f docker-compose.yaml up -d; then
    echo "docker compose up succeeded"
    break
  fi
  echo "docker compose attempt $i failed, retrying in 30s..."
  sleep 30
done
docker compose -f /opt/app/docker-compose.yaml ps -a >> /var/log/app-compose.log 2>&1 || true
