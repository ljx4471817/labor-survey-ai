# 白名单生产端增量对齐说明（2026-09-01）

目标：只把本地已对齐的 211 条白名单数据增量同步到生产环境，保留生产环境审计记录 `whitelist_audit`。生产端多出来的记录一律不删除。

注意：只把这份 `md` 文件给 Hermes 是不够的，还必须先把本地源库文件上传到生产服务器。

执行原则：

- 不做整库覆盖。
- 不执行 `DELETE FROM whitelist`。
- 本地 211 条记录按 `phone` 做 `UPSERT`：存在则更新，不存在则新增。
- 生产端存在但本地不存在的记录保持原样。

## 修改前后差异

| 指标 | 修改前 | 修改后 |
| --- | ---: | ---: |
| 总记录 | 212 | 211 |
| 启用记录 | 210 | 210 |
| 区县管理员 | 25 | 25 |
| 市级管理员 | 7 | 7 |
| 调查员 | 180 | 179 |
| 业务管理员 | 31 | 31 |
| 普通用户 | 180 | 179 |
| 系统管理员 | 1 | 1 |
| 调查员四级区域精确匹配 | 0 | 172 |

本地数据本身的主要变更：

- 删除 1 条停用测试号。
- 补齐 172 条调查员的乡镇信息。
- 规范化 48 条社区名称。
- 修正 4 条县名。
- 调查员四级精确匹配从 0 条提升到 172 条。
- 剩余 7 条仍需人工确认，包括 3 条测试号、3 条贵安新区待确认社区、1 条务川县待确认记录。

## 执行准备

本地源库文件：

```text
backend/data/whitelist-20260901.db
SHA256: 66CF0B6FB3256135BC1DD2EA509BD18BBF7B65886CBD41CD6436E048EB88E2B9
```

请先把该文件上传到生产服务器：

```text
/tmp/whitelist-20260901.db
```

上传后可校验：

```bash
sha256sum /tmp/whitelist-20260901.db
```

期望值：

```text
66CF0B6FB3256135BC1DD2EA509BD18BBF7B65886CBD41CD6436E048EB88E2B9
```

## 生产执行步骤

在生产服务器上按顺序执行：

```bash
sudo systemctl stop labor-survey
```

备份当前生产库：

```bash
cd /opt/labor-survey-ai/backend/data

ts=$(date +%Y%m%d-%H%M%S)
mkdir -p backups/whitelist-replace-$ts
cp -a whitelist.db backups/whitelist-replace-$ts/
[ -f whitelist.db-wal ] && cp -a whitelist.db-wal backups/whitelist-replace-$ts/
[ -f whitelist.db-shm ] && cp -a whitelist.db-shm backups/whitelist-replace-$ts/
```

按 `phone` 增量对齐，只处理本地源库中的 211 条记录：

```bash
python3 - <<'PY'
import sqlite3

DB = '/opt/labor-survey-ai/backend/data/whitelist.db'
SRC = '/tmp/whitelist-20260901.db'

conn = sqlite3.connect(DB)
conn.execute('ATTACH DATABASE ? AS new', (SRC,))
conn.execute('BEGIN IMMEDIATE')
conn.execute('''
    INSERT INTO whitelist (
        phone, name, province, city, county, township, community,
        admin_level, sys_role, remark, active, created_at, updated_at
    )
    SELECT
        phone, name, province, city, county, township, community,
        admin_level, sys_role, remark, active, created_at, updated_at
    FROM new.whitelist
    WHERE true
    ON CONFLICT(phone) DO UPDATE SET
        name = excluded.name,
        province = excluded.province,
        city = excluded.city,
        county = excluded.county,
        township = excluded.township,
        community = excluded.community,
        admin_level = excluded.admin_level,
        sys_role = excluded.sys_role,
        remark = excluded.remark,
        active = excluded.active,
        updated_at = excluded.updated_at
''')
conn.execute('COMMIT')
conn.execute('DETACH DATABASE new')
conn.close()
PY
```

清理残留 WAL/SHM 后启动服务：

```bash
rm -f whitelist.db-wal whitelist.db-shm
sudo systemctl start labor-survey
sudo journalctl -u labor-survey -n 50 --no-pager
```

## 验证

```bash
curl -s http://127.0.0.1:8001/health
```

检查本地 211 条都已存在：

```bash
python3 - <<'PY'
import sqlite3

DB = '/opt/labor-survey-ai/backend/data/whitelist.db'
SRC = '/tmp/whitelist-20260901.db'

conn = sqlite3.connect(DB)
conn.execute('ATTACH DATABASE ? AS new', (SRC,))

missing = conn.execute('''
    SELECT COUNT(*) FROM new.whitelist s
    LEFT JOIN whitelist w ON w.phone = s.phone
    WHERE w.phone IS NULL
''').fetchone()[0]

mismatched = conn.execute('''
    SELECT COUNT(*) FROM new.whitelist s
    JOIN whitelist w ON w.phone = s.phone
    WHERE w.name != s.name
       OR w.province != s.province
       OR w.city != s.city
       OR w.county != s.county
       OR w.township != s.township
       OR w.community != s.community
       OR w.admin_level != s.admin_level
       OR w.sys_role != s.sys_role
       OR w.remark != s.remark
       OR w.active != s.active
''').fetchone()[0]

print(f'local_missing_in_prod={missing}')
print(f'local_mismatch_in_prod={mismatched}')
conn.close()
PY
```

期望结果：

```text
local_missing_in_prod=0
local_mismatch_in_prod=0
```

生产端多出的记录不会被删除，可以用执行前的备份目录核对数量。

用已知白名单手机号登录后台，确认权限、区域信息和区域下拉框均正常。

## 回滚

如果服务异常，先停服务，然后把上一步的备份目录里的 `whitelist.db` 复制回来，清掉 WAL/SHM 后再启动服务。
