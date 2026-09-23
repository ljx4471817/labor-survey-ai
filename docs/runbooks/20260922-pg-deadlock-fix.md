# Runbook：2026-09-22 生产 PG 死锁事故与修复

## 事故

- 时间：2026-09-22 13:58 起
- 表现：`POST /api/chat`、`POST /api/quiz/submit` 返回 500，PG 日志报 `deadlock detected`
- 现场：线程甲 `save_exchange` 持 `conversation_messages` 行写锁、等 `conversations` 写锁；线程乙首次建连接重跑建表 DDL，持 `conversations` ShareLock、等 `conversation_messages` ShareLock → AB-BA 互等，PG 杀事务 → 500

## 根因

`backend/app/persistence/conversations.py` 的 `_get_conn()` 在每个新线程首次调用时于 `_WRITE_LOCK` 之外重跑整套 `CREATE TABLE / CREATE INDEX`（uvicorn 线程池不断新增线程）。`whitelist_db.py`、`query_log.py` 存在同款隐患。

## 修复（PR #22，commit c5823ca，2026-09-23 合入 main c7b4453）

- 三个持久化模块的建表 DDL 收进进程级双检锁（`_schema_lock` + `_schema_ready_for` / `if _conn is None`，对齐 `quiz_db.py` 既有写法）
- 新增回归测试 `backend/tests/test_schema_init_guard.py`（8 线程并发首调，DDL 只应执行 1 次）
- 曾附带 `db.py` 语句级死锁重试（40P01/40001 最多 2 次）；2026-09-23 复核后移除（commit 87d3163）：PG 对这两类错误会 abort **整个事务**并销毁 savepoint，`ROLLBACK TO SAVEPOINT` 后重放单条语句必然以 25P02 失败——语句级重试是无效兜底。防死锁只能靠根因修复。

## 教训：测试必须隔离生产数据库（红线）

`backend/app/core/config.py` 顶部 `load_dotenv(.env)` 会把 4 行 `LSX_DB_*`（指向生产 PG）注入 `os.environ`；持久化模块 `_db_target()` 优先读环境变量，测试里 `monkeypatch DB_PATH` 拦不住。

2026-09-22 实测后果：在生产机跑未隔离的 pytest，14:28–14:29 造成 17 个真实调查员 `POST /api/quiz/submit` 500，另有 13 条测试会话写入生产库（已清理并留 CSV 备份）。

规则：配置了 `LSX_DB_*` 的环境跑 pytest 前必须先移除这 4 个变量（`env -u LSX_DB_WHITELIST -u LSX_DB_CONVERSATIONS -u LSX_DB_QUERY_LOG -u LSX_DB_QUIZ`）。长期方案：`backend/tests/conftest.py` 加 autouse fixture 清变量并重置各模块 `_conn` / `_schema_ready_for`（待做）。

## 验证记录（生产服务器）

- 2026-09-22 15:28:30 重启后：`/health` 200；并发 30 次 `/health` 全 200；deadlock / 500 / Traceback 0 新增；PG 日志死锁止于 14:29:32
