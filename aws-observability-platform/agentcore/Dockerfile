# AgentCore Runtime - LangGraph 분석 컨테이너
FROM python:3.12-slim

WORKDIR /app

# 시스템 패키지 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Python 패키지 설치
RUN pip install --no-cache-dir \
    bedrock-agentcore \
    langgraph \
    "strands-agents>=0.1.0" \
    "strands-agents-tools==0.2.21" \
    boto3 \
    langchain-aws \
    opensearch-py \
    requests-aws4auth

# Python stdout 즉시 플러시 (CloudWatch 로그 전달용)
ENV PYTHONUNBUFFERED=1
# OTEL 로그 수집 활성화
ENV OTEL_LOGS_EXPORTER=console
ENV OTEL_PYTHON_LOG_CORRELATION=true
ENV OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED=true

# 소스 코드 복사
COPY lambda_package/ .

# AgentCore Runtime 엔트리포인트
ENTRYPOINT ["python", "agent_runtime_app.py"]
