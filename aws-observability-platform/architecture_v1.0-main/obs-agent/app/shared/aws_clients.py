import os
import boto3
import botocore.auth
import botocore.awsrequest
from opensearchpy import OpenSearch, RequestsHttpConnection


def _resolve_region() -> str:
    # Lambda 런타임이 기본으로 AWS_REGION을 제공하며, boto3 Session에서도 region_name을 알 수 있음
    return (
        os.environ.get("AWS_REGION")
        or boto3.Session().region_name
        or "ap-northeast-2"
    )


AWS_REGION = _resolve_region()
# 예: https://aps-workspaces.ap-northeast-2.amazonaws.com/workspaces/ws-xxxx
AMP_ENDPOINT = os.environ.get("AMP_ENDPOINT", "").rstrip("/")
# 예: vpc-logtech-dev-....ap-northeast-2.es.amazonaws.com
AOS_ENDPOINT = os.environ.get("AOS_ENDPOINT", "")


def _get_boto_session() -> boto3.Session:
    """
    Lambda 실행 역할 기반 기본 세션.
    """
    return boto3.Session()


def amp_signed_get(path: str, query: str) -> str:
    """
    AMP HTTP GET을 SigV4 로 서명해서 호출. JSON 문자열을 반환한다.

    path 예시: "/api/v1/query", "/api/v1/label/__name__/values"
    query 예시: "query=up"
    """
    import requests

    if not AMP_ENDPOINT:
        raise RuntimeError("AMP_ENDPOINT 환경변수가 설정되어 있지 않습니다.")

    url = f"{AMP_ENDPOINT}{path}?{query}"

    session = _get_boto_session()
    creds = session.get_credentials().get_frozen_credentials()

    aws_req = botocore.awsrequest.AWSRequest(method="GET", url=url)
    signer = botocore.auth.SigV4Auth(creds, "aps", AWS_REGION)
    signer.add_auth(aws_req)
    prepared = aws_req.prepare()

    resp = requests.get(url, headers=dict(prepared.headers), timeout=10)
    resp.raise_for_status()
    return resp.text


def get_opensearch_client() -> OpenSearch:
    """
    SigV4 인증이 걸린 OpenSearch 클라이언트를 반환한다.
    """
    if not AOS_ENDPOINT:
        raise RuntimeError("AOS_ENDPOINT 환경변수가 설정되어 있지 않습니다.")

    session = _get_boto_session()
    credentials = session.get_credentials().get_frozen_credentials()

    class AWSV4SignerAuth:
        def __call__(self, request):
            aws_req = botocore.awsrequest.AWSRequest(
                method=request.method,
                url=request.url,
                data=request.body,
                headers=request.headers,
            )
            signer = botocore.auth.SigV4Auth(credentials, "es", AWS_REGION)
            signer.add_auth(aws_req)
            request.headers.update(dict(aws_req.headers))
            return request

    host = AOS_ENDPOINT

    return OpenSearch(
        hosts=[{"host": host, "port": 443}],
        http_auth=AWSV4SignerAuth(),
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
    )

