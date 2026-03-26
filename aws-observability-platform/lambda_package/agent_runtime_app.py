"""
AgentCore Runtime App - LangGraph 분석을 AgentCore Runtime에서 실행
Lambda timeout 제약 없이 최대 8시간 실행 가능
"""
import os
import sys
import asyncio
import logging

# Add current directory to path (for imports)
sys.path.insert(0, os.path.dirname(__file__))

from cw_logger import cw_log

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
logger = logging.getLogger(__name__)

from bedrock_agentcore import BedrockAgentCoreApp
from bedrock_agent_runtime_handler import _run_analysis_lambda

app = BedrockAgentCoreApp()


@app.entrypoint
async def agent_invocation(request):
    """AgentCore Runtime entrypoint - LangGraph 분석 실행"""
    alert_name = request.get('alert_name', 'Unknown')
    cw_log(f"[AgentCore Runtime] 분석 시작: {alert_name}")

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _run_analysis_lambda, request)

    cw_log(f"[AgentCore Runtime] 분석 완료: {alert_name}")
    yield {'status': 'done', 'alert_name': alert_name}


if __name__ == '__main__':
    app.run()
