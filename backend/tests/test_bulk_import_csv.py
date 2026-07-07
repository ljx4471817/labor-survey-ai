"""bulk_import_csv 白名单批量导入测试。
使用 tmp 临时 SQLite（monkeypatch DB_PATH + 重置 _conn），
避免污染真实白名单数据。
CSV 表头：phone, name, province, city, admin_level, county, township, community, remark
返回：{"inserted": int, "updated": int, "errors": [{"line", "error"}]}
"""
import csv as csv_mod
import sqlite3

import pytest

import app.persistence.whitelist_db as wl


@pytest.fixture
def tmp_db(monkeypatch, tmp_path):
    """临时 SQLite + monkeypatch 全局 _conn / DB_PATH。"""
    db_file = tmp_path / "whitelist.db"
    monkeypatch.setattr(wl, "DB_PATH", db_file)
    monkeypatch.setattr(wl, "_conn", None)
    yield db_file
    monkeypatch.setattr(wl, "_conn", None)


def _csv(rows: list[dict]) -> str:
    """构造 CSV 文本（用 stdlib csv 避免手写转义）。"""
    import io as iomod
    buf = iomod.StringIO()
    fieldnames = ["phone", "name", "province", "city", "admin_level",
                  "county", "township", "community", "remark"]
    writer = csv_mod.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return buf.getvalue()


def test_bulk_insert_new(tmp_db):
    csv_text = _csv([{
        "phone": "13900000001",
        "name": "\u6d4b\u8bd5\u53f7",
        "province": "\u8d35\u5dde\u7701",
        "city": "\u8d35\u9633\u5e02",
        "admin_level": "\u8c03\u67e5\u5458",
        "county": "\u4e91\u5ca9\u533a",
        "township": "",
        "community": "\u6d4b\u8bd5\u793e\u533a",
        "remark": "",
    }])
    result = wl.bulk_import_csv(csv_text)
    assert result["inserted"] == 1
    assert result["updated"] == 0
    assert result["errors"] == []
    assert wl.is_whitelisted("13900000001")


def test_bulk_update_existing(tmp_db):
    # 第一次 insert
    csv_text1 = _csv([{
        "phone": "13900000002",
        "name": "\u6d4b\u8bd5\u4e59",
        "province": "\u8d35\u5dde\u7701",
        "city": "\u8d35\u9633\u5e02",
        "admin_level": "\u8c03\u67e5\u5458",
        "county": "\u5357\u660e\u533a",
        "township": "",
        "community": "\u793e\u533aA",
        "remark": "",
    }])
    wl.bulk_import_csv(csv_text1)
    # 第二次 update（同 phone, 不同社区）
    csv_text2 = _csv([{
        "phone": "13900000002",
        "name": "\u6d4b\u8bd5\u4e59",
        "province": "\u8d35\u5dde\u7701",
        "city": "\u8d35\u9633\u5e02",
        "admin_level": "\u533a\u53bf",
        "county": "\u5357\u660e\u533a",
        "township": "",
        "community": "\u793e\u533aB\u66f4\u65b0",
        "remark": "",
    }])
    result = wl.bulk_import_csv(csv_text2)
    assert result["inserted"] == 0
    assert result["updated"] == 1
    user = wl.get_user("13900000002")
    assert user["community"] == "\u793e\u533aB\u66f4\u65b0"
    assert user["admin_level"] == "\u533a\u53bf"


def test_bulk_mixed_insert_update(tmp_db):
    # 提前插入一个
    csv_pre = _csv([{
        "phone": "13900000010",
        "name": "\u65e7\u540d",
        "province": "\u8d35\u5dde\u7701",
        "city": "\u8d35\u9633\u5e02",
        "admin_level": "\u8c03\u67e5\u5458",
        "county": "\u4e91\u5ca9\u533a",
        "township": "",
        "community": "\u793e\u533aX",
        "remark": "",
    }])
    wl.bulk_import_csv(csv_pre)
    # 混合：13900000010 更新 + 13900000011 新增
    csv_mixed = _csv([
        {"phone": "13900000010", "name": "\u65b0\u540d", "province": "\u8d35\u5dde\u7701",
         "city": "\u8d35\u9633\u5e02", "admin_level": "\u8c03\u67e5\u5458", "county": "\u4e91\u5ca9\u533a",
         "township": "", "community": "\u793e\u533aX", "remark": ""},
        {"phone": "13900000011", "name": "\u65b0\u5458", "province": "\u8d35\u5dde\u7701",
         "city": "\u8d35\u9633\u5e02", "admin_level": "\u5e02\u7ea7", "county": "",
         "township": "", "community": "", "remark": "\u5e02\u7ba1\u7406\u5458"},
    ])
    result = wl.bulk_import_csv(csv_mixed)
    assert result["inserted"] == 1
    assert result["updated"] == 1
    user = wl.get_user("13900000011")
    assert user["admin_level"] == "\u5e02\u7ea7"
    assert user["remark"] == "\u5e02\u7ba1\u7406\u5458"


def test_bulk_missing_required_columns_raises(tmp_db):
    # 缺 admin_level 列
    csv_text = "phone,name,province,city,county,township,community\n13900000099,\u8d35\u5dde\u7701,\u8d35\u9633\u5e02,\u4e91\u5ca9\u533a,\u793e\u533aZ\n"
    with pytest.raises(ValueError, match="CSV 缺少必要列"):
        wl.bulk_import_csv(csv_text)


def test_bulk_row_error_collected_not_raised(tmp_db):
    csv_text = _csv([{
        "phone": "13900000020",
        "name": "\u6b63\u5e38",
        "province": "\u8d35\u5dde\u7701",
        "city": "\u8d35\u9633\u5e02",
        "admin_level": "\u8c03\u67e5\u5458",
        "county": "\u4e91\u5ca9\u533a",
        "township": "",
        "community": "\u793e\u533aN",
        "remark": "",
    }])
    result = wl.bulk_import_csv(csv_text)
    assert result["errors"] == []
    assert result["inserted"] == 1
    # 第二行人为制造空 name 触发 NOT NULL 约束
    csv_text2 = "phone,name,province,city,admin_level,county,township,community,remark\n13900000030,,贵州省,贵阳市,调查员,云岩区,,社区M,\n"
    result2 = wl.bulk_import_csv(csv_text2)
    # sqlite NOT NULL 触发 → errors 应收 line=2
    assert len(result2["errors"]) == 1
    assert result2["errors"][0]["line"] == 2


def test_bulk_strips_whitespace_in_fields(tmp_db):
    csv_text = _csv([{
        "phone": "  13900000040  ",  # 应被 strip
        "name": "  \u5e26\u7a7a\u683c  ",
        "province": "\u8d35\u5dde\u7701",
        "city": "\u8d35\u9633\u5e02",
        "admin_level": "\u8c03\u67e5\u5458",
        "county": "\u4e91\u5ca9\u533a",
        "township": "",
        "community": "\u793e\u533aS",
        "remark": "",
    }])
    result = wl.bulk_import_csv(csv_text)
    assert result["inserted"] == 1
    user = wl.get_user("13900000040".strip())
    assert user is not None
    assert user["name"] == "\u5e26\u7a7a\u683c"
