"""backend/app/services/aliyun_balance.py — 阿里云账户余额/百炼消费查询（RAM 只读账单权限）。

调用费用中心 OpenAPI（QueryAccountBalance / QueryBill），凭据来自 .env 的
ALIYUN_AK_ID / ALIYUN_AK_SECRET；不打印、不落盘凭据。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
import uuid
from urllib.parse import quote

import requests

ENDPOINT = 'https://business.aliyuncs.com/'
BAILIAN_KEYWORDS = ('百炼', 'bailian', 'model studio', '大模型')


def _pe(s: str) -> str:
    """RFC3986 percent-encode（阿里云 RPC 签名用）。"""
    return quote(str(s), safe='~')


def _credentials() -> tuple[str, str]:
    ak = os.environ.get('ALIYUN_AK_ID', '')
    sk = os.environ.get('ALIYUN_AK_SECRET', '')
    if not ak or not sk:
        raise RuntimeError('ALIYUN_AK_ID / ALIYUN_AK_SECRET not set')
    return ak, sk


def _call(action: str, extra: dict, timeout: float = 20) -> dict:
    ak, sk = _credentials()
    params = {
        'Action': action,
        'Version': '2017-12-14',
        'Format': 'JSON',
        'AccessKeyId': ak,
        'SignatureMethod': 'HMAC-SHA1',
        'SignatureVersion': '1.0',
        'SignatureNonce': uuid.uuid4().hex,
        'Timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    params.update(extra)
    qs = '&'.join(f'{_pe(k)}={_pe(params[k])}' for k in sorted(params))
    string_to_sign = 'GET&%2F&' + _pe(qs)
    sig = base64.b64encode(
        hmac.new((sk + '&').encode(), string_to_sign.encode(), hashlib.sha1).digest()
    ).decode()
    params['Signature'] = sig
    resp = requests.get(ENDPOINT, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def query_balance() -> dict:
    """返回 {available_amount, cash_amount, currency}。"""
    data = _call('QueryAccountBalance', {})['Data']
    return {
        'available_amount': data.get('AvailableAmount'),
        'cash_amount': data.get('AvailableCashAmount'),
        'currency': data.get('Currency', 'CNY'),
    }


def query_bailian_usage(billing_cycle: str | None = None, timeout: float = 20) -> float | None:
    """查指定月份百炼按量消费合计（元）；无账单项返回 None。"""
    cycle = billing_cycle or time.strftime('%Y-%m')
    total: float = 0.0
    found = False
    page = 1
    while True:
        data = _call('QueryBill', {
            'BillingCycle': cycle,
            'PageNum': page,
            'PageSize': 100,
            'Type': 'PayAsYouGo',
        }, timeout=timeout)
        payload = data.get('Data') or {}
        items = payload.get('Items') or {}
        rows = items.get('Item') or []
        for it in rows:
            name = str(it.get('ProductName') or '') + str(it.get('ProductCode') or '')
            if any(k in name.lower() for k in BAILIAN_KEYWORDS):
                found = True
                try:
                    total += float(it.get('PretaxAmount') or 0)
                except (TypeError, ValueError):
                    pass
        total_count = int(payload.get('TotalCount') or 0)
        if page * 100 >= total_count:
            break
        page += 1
    return round(total, 2) if found else None
