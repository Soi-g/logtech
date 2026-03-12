#!/bin/bash
# Lambda Layer를 Linux 환경에서 빌드

set -e

echo "🐳 Docker로 Lambda Layer 빌드 중..."

# requirements.txt 생성
cat > requirements_layer.txt <<EOF
langgraph==1.0.10
langchain-aws==1.3.1
strands-agents==1.28.0
opensearch-py==3.1.0
requests-aws4auth==1.3.1
slack-bolt==1.27.0
boto3==1.42.60
EOF

# Docker로 Linux용 패키지 설치
docker run --rm \
  -v "$(pwd):/workspace" \
  -w /workspace \
  public.ecr.aws/lambda/python:3.12 \
  bash -c "
    pip install -r requirements_layer.txt -t lambda_layer/python/ --upgrade
    find lambda_layer/python -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
    find lambda_layer/python -type d -name '*.dist-info' -exec rm -rf {} + 2>/dev/null || true
    find lambda_layer/python -type f -name '*.pyc' -delete
  "

echo "📦 Lambda Layer zip 생성 중..."
cd lambda_layer
zip -r ../lambda_layer.zip python/ -q
cd ..

echo "✅ Lambda Layer 빌드 완료!"
echo "파일: lambda_layer.zip ($(du -h lambda_layer.zip | cut -f1))"
