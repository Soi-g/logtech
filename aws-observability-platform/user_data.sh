#!/bin/bash
set -e

apt-get update -y
apt-get install -y wget curl jq docker.io apt-transport-https software-properties-common

systemctl enable docker
systemctl start docker

# OpenTelemetry Collector 설치
cd /tmp
wget https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/v0.147.0/otelcol-contrib_0.147.0_linux_amd64.tar.gz
tar -xzf otelcol-contrib_0.147.0_linux_amd64.tar.gz
mv otelcol-contrib /usr/local/bin/
rm otelcol-contrib_0.147.0_linux_amd64.tar.gz

useradd --system --no-create-home --shell /usr/sbin/nologin otelcol || true
mkdir -p /etc/otelcol
chown -R otelcol:otelcol /etc/otelcol

cat > /etc/otelcol/config.yaml <<EOF
extensions:
  sigv4auth/aps:
    region: ${aws_region}
    service: aps
  sigv4auth/es:
    region: ${aws_region}
    service: es

receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:14317
      http:
        endpoint: 0.0.0.0:14318

processors:
  memory_limiter:
    check_interval: 1s
    limit_mib: 512
    spike_limit_mib: 128

  batch:
    timeout: 10s
    send_batch_size: 2048

  resource/common:
    attributes:
      - key: service.namespace
        value: "todolist"
        action: upsert
      - key: deployment.environment
        value: "dev"
        action: upsert

  resource/app:
    attributes:
      - key: source
        value: "app"
        action: upsert

  resource/sys:
    attributes:
      - key: source
        value: "sys"
        action: upsert
      - key: log.source
        value: "syslog"
        action: upsert

  resource/host:
    attributes:
      - key: source
        value: "host"
        action: upsert

  resource/container:
    attributes:
      - key: source
        value: "container"
        action: upsert

  transform/trim_service_name:
    error_mode: ignore
    log_statements:
      - context: resource
        statements:
          - set(attributes["service.name"], Trim(attributes["service.name"]))
    metric_statements:
      - context: resource
        statements:
          - set(attributes["service.name"], Trim(attributes["service.name"]))
    trace_statements:
      - context: resource
        statements:
          - set(attributes["service.name"], Trim(attributes["service.name"]))

  transform/fix_hostlog_timestamp:
    error_mode: ignore
    log_statements:
      - context: log
        statements:
          - set(time, Now()) where time_unix_nano == 0

connectors:
  routing/metrics:
    table:
      - condition: resource.attributes["source"] == "container"
        pipelines: [metrics/container]
      - condition: resource.attributes["source"] == "host"
        pipelines: [metrics/host]
    default_pipelines: [metrics/app]

  routing/logs:
    table:
      - condition: resource.attributes["source"] == "sys"
        pipelines: [logs/host]
    default_pipelines: [logs/app]

exporters:
  debug:
    verbosity: basic

  prometheusremotewrite:
    endpoint: ${amp_remote_write_url}api/v1/remote_write
    auth:
      authenticator: sigv4auth/aps
    timeout: 30s
    remote_write_queue:
      enabled: true
    resource_to_telemetry_conversion:
      enabled: true

  opensearch/logs_app:
    http:
      endpoint: https://${opensearch_endpoint}
      auth:
        authenticator: sigv4auth/es
    logs_index: "logs-app"
    timeout: 30s
    sending_queue:
      enabled: true
      num_consumers: 4
      queue_size: 2048

  opensearch/logs_host:
    http:
      endpoint: https://${opensearch_endpoint}
      auth:
        authenticator: sigv4auth/es
    logs_index: "logs-host"
    timeout: 30s
    sending_queue:
      enabled: true
      num_consumers: 4
      queue_size: 2048

  opensearch/traces:
    http:
      endpoint: https://${opensearch_endpoint}
      auth:
        authenticator: sigv4auth/es
    traces_index: "traces-app"
    timeout: 30s
    sending_queue:
      enabled: true
      num_consumers: 4
      queue_size: 2048

  awss3/logs:
    s3uploader:
      region: ${aws_region}
      s3_bucket: ${s3_logs_bucket}
      s3_prefix: "telemetry/raw/logs"
      compression: gzip
      file_prefix: logs
    marshaler: otlp_json

  awss3/traces:
    s3uploader:
      region: ${aws_region}
      s3_bucket: ${s3_traces_bucket}
      s3_prefix: "telemetry/raw/traces"
      compression: gzip
      file_prefix: traces
    marshaler: otlp_json

service:
  extensions: [sigv4auth/aps, sigv4auth/es]

  pipelines:
    metrics/ingest:
      receivers: [otlp]
      processors: [memory_limiter, resource/common, transform/trim_service_name]
      exporters: [routing/metrics]

    logs/ingest:
      receivers: [otlp]
      processors: [memory_limiter, resource/common, transform/trim_service_name]
      exporters: [routing/logs]

    metrics/app:
      receivers: [routing/metrics]
      processors: [resource/app, batch]
      exporters: [prometheusremotewrite, debug]

    metrics/container:
      receivers: [routing/metrics]
      processors: [resource/container, batch]
      exporters: [prometheusremotewrite, debug]

    metrics/host:
      receivers: [routing/metrics]
      processors: [resource/host, batch]
      exporters: [prometheusremotewrite, debug]

    logs/app:
      receivers: [routing/logs]
      processors: [resource/app, batch]
      exporters: [opensearch/logs_app, awss3/logs, debug]

    logs/host:
      receivers: [routing/logs]
      processors: [resource/sys, transform/fix_hostlog_timestamp, batch]
      exporters: [opensearch/logs_host, awss3/logs, debug]

    traces:
      receivers: [otlp]
      processors: [memory_limiter, resource/common, resource/app, transform/trim_service_name, batch]
      exporters: [opensearch/traces, awss3/traces, debug]
EOF

chown otelcol:otelcol /etc/otelcol/config.yaml
chmod 640 /etc/otelcol/config.yaml

cat > /etc/systemd/system/otelcol.service <<OTEL_SERVICE
[Unit]
Description=OpenTelemetry Collector
After=network.target

[Service]
Type=simple
User=otelcol
Group=otelcol
ExecStart=/usr/local/bin/otelcol-contrib --config=/etc/otelcol/config.yaml
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
OTEL_SERVICE

systemctl daemon-reload
systemctl enable otelcol
systemctl start otelcol
sleep 5

# Envoy 설치 (JWT 인증 게이트웨이)
docker pull envoyproxy/envoy:v1.29-latest
mkdir -p /etc/envoy

# Self-signed TLS 인증서 생성
mkdir -p /etc/envoy/certs
openssl req -x509 -newkey rsa:4096 -keyout /etc/envoy/certs/key.pem \
  -out /etc/envoy/certs/cert.pem -days 3650 -nodes \
  -subj "/CN=otel-collector"
chmod 644 /etc/envoy/certs/key.pem
chmod 644 /etc/envoy/certs/cert.pem

COGNITO_ISSUER="https://cognito-idp.${aws_region}.amazonaws.com/${cognito_user_pool_id}"
COGNITO_JWKS_URI="https://cognito-idp.${aws_region}.amazonaws.com/${cognito_user_pool_id}/.well-known/jwks.json"
COGNITO_SNI="cognito-idp.${aws_region}.amazonaws.com"

cat > /etc/envoy/envoy.yaml <<ENVOY_EOF
static_resources:
  listeners:
    - name: listener_grpc
      address:
        socket_address:
          address: 0.0.0.0
          port_value: 4317
      filter_chains:
        - transport_socket:
            name: envoy.transport_sockets.tls
            typed_config:
              "@type": type.googleapis.com/envoy.extensions.transport_sockets.tls.v3.DownstreamTlsContext
              common_tls_context:
                alpn_protocols: ["h2"]
                tls_certificates:
                  - certificate_chain:
                      filename: /etc/envoy/certs/cert.pem
                    private_key:
                      filename: /etc/envoy/certs/key.pem
          filters:
            - name: envoy.filters.network.http_connection_manager
              typed_config:
                "@type": type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager
                stat_prefix: ingress_grpc
                codec_type: HTTP2
                http_filters:
                  - name: envoy.filters.http.jwt_authn
                    typed_config:
                      "@type": type.googleapis.com/envoy.extensions.filters.http.jwt_authn.v3.JwtAuthentication
                      providers:
                        cognito:
                          issuer: $COGNITO_ISSUER
                          forward: true
                          remote_jwks:
                            http_uri:
                              uri: $COGNITO_JWKS_URI
                              cluster: cognito_jwks
                              timeout: 5s
                            cache_duration: 300s
                          claim_to_headers:
                            - header_name: x-tenant-id
                              claim_name: client_id
                      rules:
                        - match:
                            prefix: /
                          requires:
                            provider_name: cognito
                  - name: envoy.filters.http.router
                    typed_config:
                      "@type": type.googleapis.com/envoy.extensions.filters.http.router.v3.Router
                route_config:
                  name: local_route_grpc
                  virtual_hosts:
                    - name: otelcol_grpc
                      domains: ["*"]
                      routes:
                        - match:
                            prefix: /
                          route:
                            cluster: otelcol_grpc

  clusters:
    - name: otelcol_grpc
      connect_timeout: 5s
      type: STATIC
      http2_protocol_options: {}
      load_assignment:
        cluster_name: otelcol_grpc
        endpoints:
          - lb_endpoints:
              - endpoint:
                  address:
                    socket_address:
                      address: 127.0.0.1
                      port_value: 14317

    - name: cognito_jwks
      connect_timeout: 5s
      type: LOGICAL_DNS
      dns_lookup_family: V4_ONLY
      transport_socket:
        name: envoy.transport_sockets.tls
        typed_config:
          "@type": type.googleapis.com/envoy.extensions.transport_sockets.tls.v3.UpstreamTlsContext
          sni: $COGNITO_SNI
      load_assignment:
        cluster_name: cognito_jwks
        endpoints:
          - lb_endpoints:
              - endpoint:
                  address:
                    socket_address:
                      address: $COGNITO_SNI
                      port_value: 443

admin:
  address:
    socket_address:
      address: 127.0.0.1
      port_value: 9901
ENVOY_EOF

docker run -d \
  --name envoy \
  --restart unless-stopped \
  --network host \
  --user root \
  -v /etc/envoy/envoy.yaml:/etc/envoy/envoy.yaml:ro \
  -v /etc/envoy/certs:/etc/envoy/certs:ro \
  envoyproxy/envoy:v1.29-latest \
  -c /etc/envoy/envoy.yaml

sleep 5

# SigV4 Proxy (Grafana → AMP 연동용)
docker run -d \
  --name sigv4-proxy \
  --restart unless-stopped \
  --network host \
  public.ecr.aws/aws-observability/aws-sigv4-proxy:latest \
  --name aps \
  --region ${aws_region} \
  --host aps-workspaces.${aws_region}.amazonaws.com \
  --port :8005

# Grafana 설치
wget -q -O - https://packages.grafana.com/gpg.key | apt-key add -
echo "deb https://packages.grafana.com/oss/deb stable main" | tee /etc/apt/sources.list.d/grafana.list
apt-get update -y
apt-get install -y grafana

# Grafana 데이터 소스 자동 설정
mkdir -p /etc/grafana/provisioning/datasources
cat > /etc/grafana/provisioning/datasources/datasources.yaml <<GRAFANA_DS
apiVersion: 1

datasources:
  - name: Prometheus (AMP)
    type: prometheus
    access: proxy
    url: http://localhost:8005/workspaces/${amp_workspace_id}
    isDefault: true
    jsonData:
      httpMethod: POST
      timeInterval: 30s
    editable: true

  - name: OpenSearch
    type: opensearch
    access: proxy
    url: ${opensearch_endpoint}
    database: "logs-*"
    basicAuth: true
    basicAuthUser: ${opensearch_master_user}
    jsonData:
      timeField: "@timestamp"
      esVersion: "2.11.0"
      logMessageField: message
      logLevelField: log.level
      interval: Daily
      timeInterval: 10s
      maxConcurrentShardRequests: 5
    secureJsonData:
      basicAuthPassword: "${opensearch_master_password}"
    editable: true
GRAFANA_DS

chown -R grafana:grafana /etc/grafana/provisioning

systemctl enable grafana-server
systemctl start grafana-server

echo "====================================="
echo "설치 완료!"
echo "Envoy  : 4317(gRPC) / 4318(HTTP) - JWT 인증"
echo "OTel   : 14317(gRPC) / 14318(HTTP) - 내부 전용"
echo "Grafana: 3000"
echo "  - Prometheus (AMP) via SigV4 Proxy"
echo "  - OpenSearch (로그/트레이스)"
echo "====================================="

# ============================================================
# OpenSearch rolesmapping 자동 설정
# OpenSearch가 준비될 때까지 대기 후 OTel Collector IAM role 매핑
# (OSIS 제거 → OTel Collector EC2 role이 직접 OpenSearch에 쓰기)
# ============================================================
OPENSEARCH_ENDPOINT="${opensearch_endpoint}"
OPENSEARCH_USER="${opensearch_master_user}"
OPENSEARCH_PASSWORD="${opensearch_master_password}"
OTEL_COLLECTOR_ROLE_ARN="${otel_collector_role_arn}"

echo "OpenSearch rolesmapping 설정 대기 중..."
for i in $(seq 1 40); do
  STATUS=$(curl -s -o /dev/null -w "%%{http_code}" \
    -u "$OPENSEARCH_USER:$OPENSEARCH_PASSWORD" \
    "https://$OPENSEARCH_ENDPOINT/_cluster/health" 2>/dev/null)
  if [ "$STATUS" = "200" ]; then
    echo "OpenSearch 준비 완료 ($i번째 시도)"
    break
  fi
  echo "OpenSearch 대기 중... ($i/40, status=$STATUS)"
  sleep 30
done

# OTel Collector IAM role → all_access 매핑
RESULT=$(curl -s -X PUT "https://$OPENSEARCH_ENDPOINT/_plugins/_security/api/rolesmapping/all_access" \
  -H "Content-Type: application/json" \
  -u "$OPENSEARCH_USER:$OPENSEARCH_PASSWORD" \
  -d "{\"backend_roles\":[\"$OTEL_COLLECTOR_ROLE_ARN\"],\"hosts\":[],\"users\":[\"$OPENSEARCH_USER\"]}")
echo "rolesmapping 결과: $RESULT"

# ============================================================
# ISM 정책 생성 - 30일 후 인덱스 자동 삭제
# (AWS provider에 ISM 리소스가 없어 user_data에서 처리:
#  EC2는 VPC 내부 → OpenSearch 직접 접근 가능)
# ============================================================
echo "ISM 정책 생성 중..."
curl -s -X PUT "https://$OPENSEARCH_ENDPOINT/_plugins/_ism/policies/delete-after-30-days" \
  -H "Content-Type: application/json" \
  -u "$OPENSEARCH_USER:$OPENSEARCH_PASSWORD" \
  -d '{
    "policy": {
      "description": "Delete indices after 30 days",
      "default_state": "hot",
      "states": [
        {
          "name": "hot",
          "actions": [],
          "transitions": [
            {
              "state_name": "delete",
              "conditions": {
                "min_index_age": "30d"
              }
            }
          ]
        },
        {
          "name": "delete",
          "actions": [{ "delete": {} }],
          "transitions": []
        }
      ],
      "ism_template": [
        {
          "index_patterns": ["logs-app*", "logs-host*", "traces-app*"],
          "priority": 100
        }
      ]
    }
  }' || true
echo "ISM 정책 생성 완료"

# ============================================================
# 인덱스 템플릿 생성 - 신규 인덱스에 ISM 정책 자동 적용
# ============================================================
echo "인덱스 템플릿 생성 중..."

for PATTERN in "logs-app" "logs-host" "traces-app"; do
  curl -s -X PUT "https://$OPENSEARCH_ENDPOINT/_index_template/$${PATTERN}-template" \
    -H "Content-Type: application/json" \
    -u "$OPENSEARCH_USER:$OPENSEARCH_PASSWORD" \
    -d "{
      \"index_patterns\": [\"$${PATTERN}*\"],
      \"template\": {
        \"settings\": {
          \"plugins.index_state_management.policy_id\": \"delete-after-30-days\",
          \"number_of_shards\": 1,
          \"number_of_replicas\": 0
        }
      },
      \"priority\": 100
    }" || true
  echo "  템플릿 생성: $${PATTERN}-template"
done

echo "인덱스 템플릿 생성 완료"