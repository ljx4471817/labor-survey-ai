"""2026-06-21 起停用：配合 voice.py，路由在 main.py 已注释。代码完整保留供未来恢复。

原功能：讯飞实时语音转写大模型鉴权：签名 + 完整 WSS URL 拼接。

协议要点（来自官方文档 + Python demo）：
- URL 形如 wss://office-api-ast-dx.iflyaisol.com/ast/communicate/v1?{params}
- 参数除 signature 外按 key 升序排序，key/value 都做 URL 编码
- 签名 = HMAC-SHA1(accessKeySecret, baseString) → Base64
- utc 用本地时区 +0800 的 ISO8601 字符串
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import uuid
from urllib.parse import quote, urlencode

XUNFEI_ASR_HOST = "office-api-ast-dx.iflyaisol.com"
XUNFEI_ASR_PATH = "/ast/communicate/v1"

# 固定业务参数（按官方 demo）
FIXED_PARAMS: dict[str, str] = {
    "audio_encode": "pcm_s16le",
    "lang": "autodialect",
    "samplerate": "16000",
}


def _utc_with_offset0800() -> str:
    """生成讯飞要求的 UTC 字段：2025-09-04T15:38:07+0800（无冒号）。"""
    tz = datetime.timezone(datetime.timedelta(hours=8))
    return datetime.datetime.now(tz).strftime("%Y-%m-%dT%H:%M:%S%z")


def _sign(base_string: str, api_secret: str) -> str:
    digest = hmac.new(
        api_secret.encode("utf-8"),
        base_string.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def build_xunfei_asr_url(
    app_id: str,
    api_key: str,
    api_secret: str,
    pd_domain: str = "gov",
) -> str:
    """拼出带鉴权参数的完整 WSS URL。"""
    params: dict[str, str] = {
        "accessKeyId": api_key,
        "appId": app_id,
        "uuid": uuid.uuid4().hex,
        "utc": _utc_with_offset0800(),
        **FIXED_PARAMS,
    }
    if pd_domain:
        params["pd"] = pd_domain

    # 按 key 升序排序后做签名（key/value 都走 quote(safe='')），再 urlencode 拼 query
    sorted_items = sorted(
        (k, v) for k, v in params.items() if v and str(v).strip()
    )
    base_string = urlencode(sorted_items, safe="", quote_via=quote)
    params["signature"] = _sign(base_string, api_secret)

    return f"wss://{XUNFEI_ASR_HOST}{XUNFEI_ASR_PATH}?{urlencode(params)}"
