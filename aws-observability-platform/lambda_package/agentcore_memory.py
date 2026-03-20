"""
AgentCore Memory - AWS Bedrock AgentCore 기반 장기 기억 관리

Phase 1: AOSS와 병렬 운영 (검증용)
- AOSS는 계속 primary로 동작
- AgentCore에도 동일 데이터를 저장하여 API 동작 검증

API:
- bedrock-agentcore-control : 메모리 스토어 관리 (create/delete)
- bedrock-agentcore         : 레코드 저장 및 의미 기반 검색

Namespace 구조:
  /incidents/{alert_name}/  →  알람별 장애 이력

환경변수:
  AGENTCORE_MEMORY_ID  : 사전 생성된 Memory Store ID (없으면 모든 호출 스킵)
  AWS_REGION_NAME      : AWS 리전 (기본 ap-northeast-2)
"""

import os
import re
import json
import boto3
from datetime import datetime

AWS_REGION = os.environ.get('AWS_REGION_NAME', 'ap-northeast-2')
AGENTCORE_MEMORY_ID = os.environ.get('AGENTCORE_MEMORY_ID', '')


class AgentCoreMemory:
    """
    Bedrock AgentCore Memory 기반 장애 이력 관리.

    Phase 1에서는 AOSS와 병렬로 동작하며, 모든 예외는 조용히 처리하여
    기존 incident_memory.py(AOSS) 흐름에 영향을 주지 않는다.
    """

    def __init__(self):
        self.memory_id = AGENTCORE_MEMORY_ID
        self._data_client = None

    @staticmethod
    def _safe_namespace(alert_name: str) -> str:
        """alert_name을 namespace 허용 패턴으로 변환 (공백/특수문자 → -)"""
        safe = re.sub(r'[^a-zA-Z0-9\-_]', '-', alert_name)
        safe = re.sub(r'-+', '-', safe).strip('-')  # 연속 하이픈 정리
        return f"/incidents/{safe}/"

    # ------------------------------------------------------------------ #
    # 클라이언트                                                            #
    # ------------------------------------------------------------------ #

    def _get_data_client(self):
        if self._data_client is None:
            self._data_client = boto3.client(
                'bedrock-agentcore',
                region_name=AWS_REGION
            )
        return self._data_client

    # ------------------------------------------------------------------ #
    # Public API (incident_memory.IncidentMemory 과 동일한 시그니처)        #
    # ------------------------------------------------------------------ #

    def save_incident(self, incident_data: dict) -> str | None:
        """장애 이력을 AgentCore Long-term Memory에 저장. 성공 시 memoryRecordId 반환."""
        if not self.memory_id:
            print("⚠️ [AgentCore] AGENTCORE_MEMORY_ID 미설정 → 저장 스킵")
            return None

        try:
            client = self._get_data_client()
            alert_name = incident_data.get('alert_name', 'unknown')

            content_text = self._format_incident_content(incident_data)
            namespace = self._safe_namespace(alert_name)

            request_id = f"incident_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"

            response = client.batch_create_memory_records(
                memoryId=self.memory_id,
                records=[{
                    'requestIdentifier': request_id,
                    'namespaces': [namespace],
                    'content': {'text': content_text},
                    'timestamp': datetime.utcnow(),
                }]
            )

            ok_records = response.get('successfulRecords', [])
            ng = len(response.get('failedRecords', []))
            print(f"✅ [AgentCore] 장애 저장 완료: {len(ok_records)}건 성공 / {ng}건 실패")

            if ok_records:
                record_id = ok_records[0].get('memoryRecordId', '')
                print(f"   [AgentCore] record_id: {record_id}")
                return record_id
            return None

        except Exception as e:
            print(f"⚠️ [AgentCore] 장애 저장 실패 (AOSS에는 정상 저장됨): {e}")
            return None

    def get_recent_ongoing_incident(self, alert_name: str) -> dict | None:
        """가장 최근 ongoing 인시던트 조회 (Phase 3)."""
        if not self.memory_id:
            return None

        try:
            client = self._get_data_client()
            namespace = self._safe_namespace(alert_name)

            # 1차: 저장 포맷에 맞는 쿼리로 ongoing 레코드 타겟 조회
            response = client.retrieve_memory_records(
                memoryId=self.memory_id,
                namespace=namespace,
                searchCriteria={
                    'searchQuery': f"Alert: {alert_name} Status: ongoing",
                    'topK': 100,
                }
            )

            ongoing = self._extract_ongoing(response, alert_name)

            # 2차: 1차에서 못 찾으면 alert_name만으로 전체 조회 후 Python 필터
            if not ongoing:
                print(f"   [AgentCore] 1차 조회 미스 → 전체 조회 후 필터")
                response2 = client.retrieve_memory_records(
                    memoryId=self.memory_id,
                    namespace=namespace,
                    searchCriteria={
                        'searchQuery': f"Alert: {alert_name}",
                        'topK': 100,
                    }
                )
                ongoing = self._extract_ongoing(response2, alert_name)

            if not ongoing:
                print(f"⚠️ [AgentCore] ongoing 인시던트 없음: {alert_name}")
                return None

            ongoing.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
            result = ongoing[0]
            print(f"✅ [AgentCore] ongoing 인시던트 조회: {result['incident_id']}")
            return result

        except Exception as e:
            print(f"⚠️ [AgentCore] ongoing 조회 실패: {e}")
            return None

    def _extract_ongoing(self, response: dict, alert_name: str) -> list:
        """API 응답에서 status=ongoing 레코드만 추출."""
        ongoing = []
        for record in response.get('memoryRecordSummaries', []):
            content = record.get('content', {}).get('text', '')
            parsed = self._parse_incident_content(content)
            if parsed.get('status') == 'ongoing':
                ongoing.append({
                    'incident_id': record.get('memoryRecordId', ''),
                    'timestamp':   parsed.get('timestamp', ''),
                    'severity':    parsed.get('severity', 'high'),
                    'root_cause':  parsed.get('root_cause', ''),
                    'resolution':  parsed.get('resolution', ''),
                    'source':      'agentcore',
                })
        return ongoing

    def update_incident_resolution(self, incident_id: str, resolution: str, resolution_time_minutes: float):
        """장애 해결 정보를 새 레코드로 저장 (AgentCore는 update 미지원, 신규 resolved 레코드 추가)."""
        if not self.memory_id:
            return

        try:
            client = self._get_data_client()
            # incident_id에서 alert_name 추출 (형식: memoryRecordId or alert_name_timestamp)
            # namespace는 incident_id로 직접 조회해서 알아내거나, resolved 전용 네임스페이스 사용
            # 여기서는 incident_id를 alert_name으로 사용 (최선 노력)
            namespace = f"/incidents/resolved/"

            content_text = (
                f"Alert: resolved | "
                f"Incident ID: {incident_id} | "
                f"Resolution: {resolution} | "
                f"Resolution Time: {resolution_time_minutes} minutes | "
                f"Status: resolved | "
                f"Timestamp: {datetime.utcnow().isoformat()}"
            )

            request_id = f"resolved_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
            response = client.batch_create_memory_records(
                memoryId=self.memory_id,
                records=[{
                    'requestIdentifier': request_id,
                    'namespaces': [namespace],
                    'content': {'text': content_text},
                    'timestamp': datetime.utcnow(),
                }]
            )

            ok = len(response.get('successfulRecords', []))
            print(f"✅ [AgentCore] 해결 이력 저장: {ok}건 ({resolution_time_minutes:.1f}분)")

        except Exception as e:
            print(f"⚠️ [AgentCore] 해결 이력 저장 실패: {e}")

    def search_similar_incidents(self, alert_name: str, error_message: str = "", limit: int = 3) -> list:
        """의미 기반 유사 장애 검색."""
        if not self.memory_id:
            return []

        try:
            client = self._get_data_client()
            namespace = self._safe_namespace(alert_name)
            query = error_message if error_message else f"{alert_name} incident root cause resolution"

            response = client.retrieve_memory_records(
                memoryId=self.memory_id,
                namespace=namespace,
                searchCriteria={
                    'searchQuery': query,
                    'topK': limit,
                }
            )

            incidents = []
            for record in response.get('memoryRecordSummaries', []):
                content = record.get('content', {}).get('text', '')
                parsed  = self._parse_incident_content(content)
                incidents.append({
                    'incident_id':            record.get('memoryRecordId', ''),
                    'timestamp':              parsed.get('timestamp', ''),
                    'root_cause':             parsed.get('root_cause', ''),
                    'resolution':             parsed.get('resolution', ''),
                    'resolution_time_minutes': parsed.get('resolution_time_minutes', 0),
                    'similarity_score':       record.get('score', 0),
                    'source':                 'agentcore',
                })

            print(f"✅ [AgentCore] 유사 장애 {len(incidents)}건 검색")
            return incidents

        except Exception as e:
            print(f"⚠️ [AgentCore] 유사 장애 검색 실패: {e}")
            return []

    def get_stats(self, alert_name: str) -> dict:
        """알람 통계 (레코드 목록에서 Python으로 집계)."""
        if not self.memory_id:
            return {'count': 0, 'is_new': True}

        try:
            client = self._get_data_client()
            namespace = self._safe_namespace(alert_name)

            response = client.retrieve_memory_records(
                memoryId=self.memory_id,
                namespace=namespace,
                searchCriteria={
                    'searchQuery': f"{alert_name} incident",
                    'topK': 100,
                }
            )

            records = response.get('memoryRecordSummaries', [])
            count   = len(records)

            if count == 0:
                return {'count': 0, 'is_new': True}

            resolution_times = []
            causes: dict = {}
            for record in records:
                parsed = self._parse_incident_content(
                    record.get('content', {}).get('text', '')
                )
                rt = parsed.get('resolution_time_minutes', 0)
                if rt > 0:
                    resolution_times.append(rt)
                cause = parsed.get('root_cause', '').strip()
                if cause:
                    causes[cause] = causes.get(cause, 0) + 1

            avg_resolution = (
                sum(resolution_times) / len(resolution_times)
                if resolution_times else 0
            )
            most_common = max(causes, key=causes.get) if causes else 'Unknown'

            stats = {
                'count':             count,
                'is_new':            False,
                'avg_resolution_time': avg_resolution,
                'most_common_cause': most_common,
                'source':            'agentcore',
            }
            print(f"✅ [AgentCore] 통계: {count}건, 평균 {avg_resolution:.1f}분")
            return stats

        except Exception as e:
            print(f"⚠️ [AgentCore] 통계 조회 실패: {e}")
            return {'count': 0, 'is_new': True}

    # ------------------------------------------------------------------ #
    # 내부 유틸                                                            #
    # ------------------------------------------------------------------ #

    def _format_incident_content(self, incident_data: dict) -> str:
        """장애 데이터를 의미 검색에 최적화된 텍스트로 변환."""
        errors = ' '.join(incident_data.get('error_messages', []))[:300]
        return (
            f"Alert: {incident_data.get('alert_name', '')} | "
            f"Severity: {incident_data.get('severity', '')} | "
            f"Root Cause: {incident_data.get('root_cause', '')} | "
            f"Resolution: {incident_data.get('resolution', '')} | "
            f"Resolution Time: {incident_data.get('resolution_time', 0)} minutes | "
            f"Status: {incident_data.get('status', 'ongoing')} | "
            f"Timestamp: {incident_data.get('state_change_time', datetime.utcnow().isoformat())} | "
            f"Errors: {errors}"
        )

    def _parse_incident_content(self, content: str) -> dict:
        """저장된 텍스트에서 장애 필드 파싱."""
        result: dict = {}
        for part in content.split(' | '):
            if ': ' not in part:
                continue
            key, value = part.split(': ', 1)
            if key == 'Root Cause':
                result['root_cause'] = value
            elif key == 'Resolution':
                result['resolution'] = value
            elif key == 'Timestamp':
                result['timestamp'] = value
            elif key == 'Status':
                result['status'] = value
            elif key == 'Severity':
                result['severity'] = value
            elif key == 'Resolution Time':
                try:
                    result['resolution_time_minutes'] = float(
                        value.replace(' minutes', '')
                    )
                except ValueError:
                    result['resolution_time_minutes'] = 0
        return result


# ------------------------------------------------------------------ #
# Memory Store 생성 헬퍼 (Terraform 대신 직접 생성 시 사용)             #
# ------------------------------------------------------------------ #

def create_memory_store(name: str, description: str = "", retention_days: int = 90) -> str:
    """
    AgentCore Memory Store를 생성하고 ID를 반환.

    일반적으로 Terraform 또는 AWS CLI로 한 번만 실행하며,
    반환된 ID를 AGENTCORE_MEMORY_ID 환경변수에 설정한다.

    CLI 대응:
        aws bedrock-agentcore-control create-memory \\
            --name <name> \\
            --event-expiry-duration <days> \\
            --memory-strategies '[{"semanticMemoryStrategy":{"name":"incidents"}}]' \\
            --region ap-northeast-2
    """
    try:
        control_client = boto3.client(
            'bedrock-agentcore-control',
            region_name=AWS_REGION
        )
        response = control_client.create_memory(
            name=name,
            description=description or f"Observability incident history - {name}",
            eventExpiryDuration=retention_days,
            memoryStrategies=[{
                'semanticMemoryStrategy': {
                    'name': 'incidents',
                    'namespaces': ['/incidents/']
                }
            }]
        )
        memory = response.get('memory', {})
        memory_id = memory.get('id', '')
        print(f"✅ AgentCore Memory Store 생성: id={memory_id}")
        return memory_id

    except Exception as e:
        print(f"❌ AgentCore Memory Store 생성 실패: {e}")
        return ''


if __name__ == '__main__':
    # 간단 동작 확인 (AGENTCORE_MEMORY_ID 필요)
    mem = AgentCoreMemory()
    test_incident = {
        'alert_name': 'HighJvmMemory',
        'severity': 'critical',
        'root_cause': 'Old Gen 메모리 누수 - 캐시 eviction 정책 오류',
        'resolution': 'JVM 재시작 후 캐시 TTL 단축',
        'resolution_time': 35,
        'status': 'resolved',
        'error_messages': ['OutOfMemoryError: Java heap space'],
        'state_change_time': datetime.utcnow().isoformat(),
    }
    mem.save_incident(test_incident)
    stats = mem.get_stats('HighJvmMemory')
    print(f"Stats: {stats}")
    similar = mem.search_similar_incidents('HighJvmMemory', 'heap memory leak')
    print(f"Similar: {len(similar)}건")
