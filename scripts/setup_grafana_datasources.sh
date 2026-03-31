#!/bin/bash
# Grafana 데이터 소스 자동 설정

set -e

# Terraform output에서 값 가져오기
AMP_ENDPOINT=$(terraform output -raw amp_endpoint 2>/dev/null || echo "")
OPENSEARCH_ENDPOINT=$(terraform output -raw opensearch_endpoint 2>/dev/null || echo "")

if [ -z "$AMP_ENDPOINT" ] || [ -z "$OPENSEARCH_ENDPOINT" ]; then
    echo "❌ Terraform output을 가져올 수 없습니다."
    echo "현재 디렉토리에서 실행하거나 수동으로 엔드포인트를 입력하세요."
    exit 1
fi

echo "📊 Grafana 데이터 소스 설정 중..."
echo "AMP: $AMP_ENDPOINT"
echo "OpenSearch: $OPENSEARCH_ENDPOINT"

# Grafana 데이터 소스 설정 파일 생성
cat > /tmp/grafana_datasources.yaml <<EOF
apiVersion: 1

datasources:
  - name: Prometheus (AMP)
    type: prometheus
    access: proxy
    url: ${AMP_ENDPOINT}
    isDefault: true
    jsonData:
      httpMethod: POST
      sigV4Auth: true
      sigV4AuthType: default
      sigV4Region: ap-northeast-2
    editable: true

  - name: OpenSearch
    type: opensearch
    access: proxy
    url: ${OPENSEARCH_ENDPOINT}
    database: "logs-*"
    basicAuth: true
    basicAuthUser: admin
    jsonData:
      timeField: "@timestamp"
      esVersion: "2.11.0"
      logMessageField: message
      logLevelField: log.level
    secureJsonData:
      basicAuthPassword: "Admin123!@#"
    editable: true
EOF

echo "📤 EC2로 파일 전송 중..."
scp -i log-platform-key-v4.pem -o StrictHostKeyChecking=no \
    /tmp/grafana_datasources.yaml \
    ubuntu@3.37.234.5:/tmp/

echo "⚙️ Grafana 설정 적용 중..."
ssh -i log-platform-key-v4.pem -o StrictHostKeyChecking=no ubuntu@3.37.234.5 << 'ENDSSH'
sudo mkdir -p /etc/grafana/provisioning/datasources
sudo mv /tmp/grafana_datasources.yaml /etc/grafana/provisioning/datasources/
sudo chown grafana:grafana /etc/grafana/provisioning/datasources/grafana_datasources.yaml
sudo systemctl restart grafana-server
echo "✅ Grafana 재시작 완료"
ENDSSH

echo ""
echo "✅ 설정 완료!"
echo ""
echo "다음 단계:"
echo "1. 브라우저에서 Grafana 새로고침 (Ctrl+F5)"
echo "2. Configuration → Data sources에서 'Prometheus (AMP)' 확인"
echo "3. Explore에서 'up' 쿼리 다시 실행"
