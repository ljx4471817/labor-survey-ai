# PG 只读查询脚本 pg_query.sh 与改密脚本 pg_touch.sh

## 背景

2026-09-20 生产事故：为了跑一次性数据查询，用 `ALTER USER` 改了 PostgreSQL 角色密码，还原时把密码写成占位符字符串，导致 PG 密码 hash 与后端 `.env` 不一致 —— 管理后台接口全线 401、后端大量报错。

根因不是"还原写错了"，而是**只读查询根本不需要改密码**：后端用的密码就在 `.env` 里，直接读来用即可。

## 两个脚本的分工

| 脚本 | 用途 | 是否改 PG 密码 |
|---|---|---|
| `scripts/pg_query.sh '<command>'` | 跑一次性只读查询 / 分析 | **绝不改** |
| `scripts/pg_touch.sh --rotate` | 密码轮换 / 凭据修复专用 | 会改（强制停服） |

### 只读查询

```bash
cd /opt/labor-survey-ai
bash scripts/pg_query.sh 'backend/venv/bin/python /tmp/my_stats.py'
bash scripts/pg_query.sh 'psql -d lsx_query_log -c "SELECT count(*) FROM query_log"'
```

- 密码来源：`$LSX_ENV_FILE`（默认 `/opt/labor-survey-ai/.env`）里的 `LSX_DB_WHITELIST` 连接串；读不到时尝试 `sudo` 读取
- 执行前先验证该密码能登入 PG：凭据不同步时**立刻暴露**（而不是让用户脚本莫名报错）
- 传给子命令的通道：`$PGPASSWORD`、`$PG_TOUCH_PWD_FILE`（600 临时文件，退出即删）、`$LSX_DB_PWD`；已预设 `PGHOST=127.0.0.1`、`PGUSER=lsx`

### 密码轮换

```bash
bash scripts/pg_touch.sh --rotate              # 自动生成新密码
bash scripts/pg_touch.sh --rotate --pwd 'xxx'  # 指定新密码
```

流程：备份 `.env` 到**仓库外**（`$LSX_BACKUP_DIR`，默认 `/root`，600）→ 停 `labor-survey` 服务（消除改密窗口期的认证失败）→ `ALTER USER` → 幂等替换 `.env` 4 行（替换数不等于 4 就中止、不写盘）→ 重启 → 校验 `/health` 200 与管理端接口 200。不传 `--rotate` 直接拒绝执行。

## 安全边界

- **不要为了跑查询去改 PG 密码**：改密窗口期内线上后端仍用旧密码，新建连接会认证失败；还原写错还会把 hash 永久改坏。
- `.env` 备份只写仓库外；`.gitignore` 已用 `.env.*` 覆盖 `.env.bak-*` 等派生名，避免密钥被 `git add -A` 带走。
- 两个脚本在仓库里以 `100755` 提交，`bash scripts/xxx.sh` 与 `./scripts/xxx.sh` 均可执行。
