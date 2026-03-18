#!/bin/bash
# 챗봇 코드를 S3에 업로드하고 EC2에 배포하는 스크립트
# 사용법: bash deploy.sh [EC2_PUBLIC_IP] [PEM_KEY_PATH]
#
# destroy/apply 이후 재배포할 때도 이 스크립트 하나로 해결됩니다.

set -e

EC2_IP="${1:-52.79.160.26}"
PEM_KEY="${2:-../log-platform-key-v5.pem}"
S3_BUCKET="log-platform-dev-runbooks-347751175815"
AWS_REGION="ap-northeast-2"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

echo "=== 챗봇 배포 시작 ==="

# 1. 코드 패키지 생성 (chatbot/ + lambda_package/ 필요 파일)
echo "[1/4] 코드 패키징..."
cd "$REPO_ROOT"
tar -czf /tmp/chatbot.tar.gz \
  chatbot/app.py \
  chatbot/chat_agent.py \
  chatbot/database.py \
  chatbot/requirements.txt \
  chatbot/templates/ \
  lambda_package/agents_aws.py \
  lambda_package/agentcore_memory.py

# 2. S3 업로드 (EC2 user_data가 부팅 시 이걸 받아감)
echo "[2/4] S3 업로드..."
aws s3 cp /tmp/chatbot.tar.gz "s3://$S3_BUCKET/chatbot-deploy/chatbot.tar.gz" --region "$AWS_REGION"

# 3. EC2에 직접 배포 (SSH)
echo "[3/4] EC2 배포..."
scp -o StrictHostKeyChecking=no -i "$PEM_KEY" /tmp/chatbot.tar.gz ubuntu@$EC2_IP:/tmp/

ssh -o StrictHostKeyChecking=no -i "$PEM_KEY" ubuntu@$EC2_IP << 'ENDSSH'
set -e
cd /home/ubuntu
tar -xzf /tmp/chatbot.tar.gz
chown -R ubuntu:ubuntu chatbot lambda_package

cd chatbot
if [ ! -d venv ]; then
  python3 -m venv venv
fi
source venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt
pip install -q python-dotenv
deactivate

sudo systemctl restart chatbot
sleep 2
sudo systemctl status chatbot --no-pager | head -10
ENDSSH

# 4. 접속 확인
echo "[4/4] 접속 확인..."
sleep 2
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://$EC2_IP:8000/" || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
  echo ""
  echo "배포 완료! http://$EC2_IP:8000"
else
  echo "경고: HTTP $HTTP_CODE (서버 시작 중일 수 있음)"
  echo "잠시 후 http://$EC2_IP:8000 접속 시도해보세요"
fi
