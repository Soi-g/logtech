"""CloudWatch Logs 직접 전송 유틸"""
import os
import time
import threading
import boto3

_LOG_GROUP = "/agentcore/runtime/analysis"
_LOG_STREAM = f"agentcore-{int(time.time())}"
_cw = boto3.client("logs", region_name=os.environ.get("AWS_REGION_NAME", "ap-northeast-2"))
_seq_token = None
_initialized = False
_lock = threading.Lock()  # 병렬 tool 호출 시 _seq_token 동시 쓰기 방지


def _init():
    global _initialized
    if _initialized:
        return
    try:
        _cw.create_log_group(logGroupName=_LOG_GROUP)
    except Exception:
        pass
    try:
        _cw.create_log_stream(logGroupName=_LOG_GROUP, logStreamName=_LOG_STREAM)
    except Exception:
        pass
    _initialized = True


def cw_log(message: str):
    global _seq_token
    _init()
    with _lock:
        kwargs = {
            "logGroupName": _LOG_GROUP,
            "logStreamName": _LOG_STREAM,
            "logEvents": [{"timestamp": int(time.time() * 1000), "message": message}],
        }
        if _seq_token:
            kwargs["sequenceToken"] = _seq_token
        try:
            resp = _cw.put_log_events(**kwargs)
            _seq_token = resp.get("nextSequenceToken")
        except Exception as e:
            print(f"[CW log error] {e}", flush=True)
