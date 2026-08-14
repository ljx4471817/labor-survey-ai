"""scripts/check_qwen_balance.py — 查询阿里云账户余额与百炼消费（RAM AccessKey 只读账单权限）。

用法：
    python scripts/check_qwen_balance.py                 # 查账户可用余额
    python scripts/check_qwen_balance.py --bill          # 查本月百炼消费明细
    python scripts/check_qwen_balance.py --bill 2026-07  # 查指定月份
凭据：.env 的 ALIYUN_AK_ID / ALIYUN_AK_SECRET（RAM 子账号，建议最小权限 AliyunBSSReadOnlyAccess）。
实现：阿里云 RPC 签名（HMAC-SHA1）调用费用中心 OpenAPI，无需额外依赖。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import requests  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env')

ENDPOINT = 'https://business.aliyuncs.com/'
BAILIAN_KEYWORDS = ('百炼', 'bailian', 'model studio', '大模型')


def _pe(s: str) -> str:
    """RFC3986 percent-encode（阿里云 RPC 签名用）。"""
    return quote(str(s), safe='~')


def _sign(ak: str, sk: str, action: str, extra: dict) -> dict:
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
    return params


def call(ak: str, sk: str, action: str, extra: dict) -> dict:
    params = _sign(ak, sk, action, extra)
    r = requests.get(ENDPOINT, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def query_balance(ak: str, sk: str) -> dict:
    data = call(ak, sk, 'QueryAccountBalance', {})['Data']
    return {
        'available_amount': data.get('AvailableAmount'),
        'cash_amount': data.get('AvailableCashAmount'),
        'currency': data.get('Currency', 'CNY'),
    }


def query_bill(ak: str, sk: str, billing_cycle: str) -> list[dict]:
    """查指定月份账单，返回与百炼相关的消费项（按量 + 订阅）。"""
    out: list[dict] = []
    page = 1
    while True:
        data = call(ak, sk, 'QueryBill', {
            'BillingCycle': billing_cycle,
            'PageNum': page,
            'PageSize': 100,
            'Type': 'PayAsYouGo',
        })
        items = (data.get('Data') or {}).get('Items') or {}
        rows = items.get('Item') or []
        for it in rows:
            name = str(it.get('ProductName') or '') + str(it.get('ProductCode') or '')
            if any(k in name.lower() for k in BAILIAN_KEYWORDS):
                out.append({
                    'product': it.get('ProductName'),
                    'product_code': it.get('ProductCode'),
                    'pretax_amount': it.get('PretaxAmount'),
                    'currency': it.get('Currency'),
                    'item': (it.get('Item') or '')[:60],
                })
        total = int((data.get('Data') or {}).get('TotalCount') or 0)
        if page * 100 >= total:
            break
        page += 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--bill', nargs='?', const=None, default=None, metavar='YYYY-MM',
                    help='查询指定月份百炼消费（默认本月）')
    args = ap.parse_args()
    ak = os.environ.get('ALIYUN_AK_ID', '')
    sk = os.environ.get('ALIYUN_AK_SECRET', '')
    if not ak or not sk:
        print('缺 ALIYUN_AK_ID / ALIYUN_AK_SECRET（请写入 .env）')
        return 1
    try:
        bal = query_balance(ak, sk)
        print(f'账户可用余额: {bal["available_amount"]} {bal["currency"]}'
              f'（现金 {bal["cash_amount"]}）')
        if args.bill is not None:
            cycle = args.bill or time.strftime('%Y-%m')
            rows = query_bill(ak, sk, cycle)
            if not rows:
                print(f'{cycle} 未查到百炼相关账单项')
            else:
                print(f'{cycle} 百炼账单项 {len(rows)} 条:')
                for r in rows:
                    print(f'  {r["product"]} | {r["pretax_amount"]} {r["currency"]} | {r["item"]}')
    except Exception as e:
        print(f'查询失败: {type(e).__name__}: {e}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
