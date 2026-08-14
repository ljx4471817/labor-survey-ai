"""Unit tests for aliyun_balance service and /api/admin/llm/balance endpoint."""
from __future__ import annotations

import pytest

from app.services import aliyun_balance


def test_pe_rfc3986():
    # RFC3986：空格 %20、中文 %XX、~ 保留、字母数字不变
    assert aliyun_balance._pe('a b~') == 'a%20b~'
    assert aliyun_balance._pe('中文') == '%E4%B8%AD%E6%96%87'
    assert aliyun_balance._pe('ABC123-._') == 'ABC123-._'


def test_credentials_missing(monkeypatch):
    monkeypatch.delenv('ALIYUN_AK_ID', raising=False)
    monkeypatch.delenv('ALIYUN_AK_SECRET', raising=False)
    with pytest.raises(RuntimeError):
        aliyun_balance._credentials()


def test_query_balance_parses(monkeypatch):
    monkeypatch.setattr(
        aliyun_balance, '_call',
        lambda action, extra, timeout=20: {
            'Data': {
                'AvailableAmount': '50.66',
                'AvailableCashAmount': '50.66',
                'Currency': 'CNY',
            }
        },
    )
    out = aliyun_balance.query_balance()
    assert out['available_amount'] == '50.66'
    assert out['cash_amount'] == '50.66'
    assert out['currency'] == 'CNY'


def test_query_bailian_usage_sums(monkeypatch):
    page1 = {
        'Data': {'TotalCount': 150, 'Items': {'Item': [
            {'ProductName': '大模型服务平台百炼', 'PretaxAmount': '0.26'},
            {'ProductName': '云服务器ECS', 'PretaxAmount': '100.00'},
        ]}},
    }
    page2 = {
        'Data': {'TotalCount': 150, 'Items': {'Item': [
            {'ProductName': 'Model Studio', 'PretaxAmount': '0.50'},
        ]}},
    }
    calls = {'n': 0}

    def fake_call(action, extra, timeout=20):
        assert action == 'QueryBill'
        assert extra['Type'] == 'PayAsYouGo'
        calls['n'] += 1
        return page1 if calls['n'] == 1 else page2

    monkeypatch.setattr(aliyun_balance, '_call', fake_call)
    assert aliyun_balance.query_bailian_usage('2026-08') == 0.76
    assert calls['n'] == 2  # 分页拉完


def test_query_bailian_usage_none(monkeypatch):
    monkeypatch.setattr(
        aliyun_balance, '_call',
        lambda action, extra, timeout=20: {'Data': {'TotalCount': 1, 'Items': {'Item': [
            {'ProductName': '云服务器ECS', 'PretaxAmount': '1.00'},
        ]}}},
    )
    assert aliyun_balance.query_bailian_usage('2026-08') is None


# ---------- admin endpoint ----------

def test_llm_balance_endpoint(monkeypatch):
    monkeypatch.setattr(
        aliyun_balance, 'query_balance',
        lambda: {'available_amount': '50.66', 'cash_amount': '50.66', 'currency': 'CNY'},
    )
    monkeypatch.setattr(aliyun_balance, 'query_bailian_usage', lambda cycle: 0.26)
    from app.api import llm_admin
    d = llm_admin.get_llm_balance(phone='13900000001')
    assert d['available_amount'] == '50.66'
    assert d['month_bailian_usage'] == 0.26
    assert d['error'] is None
    assert d['month'] is not None


def test_llm_balance_endpoint_error(monkeypatch):
    def _boom():
        raise RuntimeError('ak missing')
    monkeypatch.setattr(aliyun_balance, 'query_balance', _boom)
    from app.api import llm_admin
    d = llm_admin.get_llm_balance(phone='13900000001')
    assert d['available_amount'] is None
    assert 'ak missing' in d['error']
