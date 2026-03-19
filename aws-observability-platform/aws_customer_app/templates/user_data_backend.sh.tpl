#!/bin/bash
# ============================================================
# EC2 #2 Backend — Spring Boot only
# OTel Collector Agent runs as systemd service (Datadog-style)
#
# Terraform templatefile variables:
#   ${gateway_endpoint} — platform EC2 collector host:port (direct, port 14317)
#   ${rds_endpoint}     — RDS Postgres hostname
#   ${environment}      — deployment.environment label
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

  transform/container_hostname:
    metric_statements:
      - context: resource
        statements:
          - set(attributes["host.name"], attributes["container.hostname"]) where attributes["container.hostname"] != nil

  resourcedetection:
    detectors: [system]
    system:
      hostname_sources: [os]

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
      processors: [memory_limiter, resource/container, transform/container_hostname, batch]
      exporters: [otlp/gateway]

    metrics/host:
      receivers: [hostmetrics]
      processors: [memory_limiter, resourcedetection, resource/host, batch]
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

# ─── Docker Compose — Spring Boot backend ────────────────────
# POSTGRES_HOST → RDS endpoint (Terraform-injected)
# COLLECTOR_HOST → host.docker.internal (systemd otelcol on same EC2)

mkdir -p /opt/app
cat > /opt/app/docker-compose.yaml << 'COMPOSEEOF'
services:

  todobackend-springboot:
    image: ghcr.io/lftraining/lfs148-code-todobackend-springboot:v2404
    restart: unless-stopped
    ports:
      - "8080:8080"
    extra_hosts:
      - "host.docker.internal:host-gateway"
    environment:
      - SPRING_PROFILES_ACTIVE=prod
      - POSTGRES_HOST=${rds_endpoint}
      - OTEL_EXPORTER_OTLP_ENDPOINT=http://host.docker.internal:4317
      - OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=http://host.docker.internal:4317
      - OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=grpc
      - OTEL_EXPORTER_OTLP_METRICS_PROTOCOL=grpc
      - OTEL_EXPORTER_OTLP_LOGS_PROTOCOL=grpc
      - OTEL_METRICS_EXPORTER=otlp,logging-otlp
      - OTEL_LOGS_EXPORTER=otlp,logging-otlp
      - OTEL_RESOURCE_ATTRIBUTES=service.namespace=todolist,deployment.environment=${environment},service.name=springboot
COMPOSEEOF

# Start app containers
docker compose -f /opt/app/docker-compose.yaml up -d
