"""query_log 按自然日聚合适用于使用频率折线图。"""
from __future__ import annotations

from datetime import datetime, time, timedelta

from app.persistence.query_log import UTC8, _get_conn

_REGION_SCOPE_COLUMNS = ("province", "city", "county")


def daily_usage_counts(
    *,
    days: int,
    now: datetime | None = None,
    scope: dict[str, str] | None = None,
) -> dict:
    """按 UTC+8 自然日统计 query_log；返回完整日期序列并补零。"""
    if days not in (7, 30):
        raise ValueError("days 只支持 7 或 30")

    reference_time = now or datetime.now(UTC8)
    if reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=UTC8)
    end_date = reference_time.date()
    start_date = end_date - timedelta(days=days - 1)
    start_ts = datetime.combine(
        start_date, time.min, tzinfo=UTC8
    ).isoformat(timespec="seconds")

    where_parts = ["ts >= ?"]
    params: list[str] = [start_ts]
    for column in _REGION_SCOPE_COLUMNS:
        value = (scope or {}).get(column, "")
        if value:
            where_parts.append(f"{column} = ?")
            params.append(value)

    sql = f"""
        SELECT substr(ts, 1, 10) AS usage_date, COUNT(*) AS count
        FROM query_log
        WHERE {' AND '.join(where_parts)}
        GROUP BY substr(ts, 1, 10)
    """
    counts = {
        row["usage_date"]: row["count"]
        for row in _get_conn().execute(sql, params).fetchall()
    }
    series = []
    current_date = start_date
    for _ in range(days):
        usage_date = current_date.isoformat()
        series.append({"date": usage_date, "count": counts.get(usage_date, 0)})
        current_date += timedelta(days=1)

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "total": sum(item["count"] for item in series),
        "series": series,
    }
