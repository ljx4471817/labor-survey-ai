# 20260929 · 切 PG 后新增白名单手机号登不进来（缓存失效键失灵）

**现象**：管理员 2026-09-29 14:41 在白名单里新增了桐梓县业务管理员（19922793945 邹美玲，active=1），
本人随后在手机上登录，17:12 被拒 —— `POST /api/auth/login` 401「手机号未授权」，
`journalctl` 里是 `app.api.auth:login:29 - login: 未授权手机号 199****`。

**结论：白名单数据没问题，是登录缓存的失效键在切 PG 之后失灵了。**

## 根因（一句话）

`backend/app/infra/auth.py` 里白名单是全量缓存：

```python
@functools.lru_cache(maxsize=1)
def _cached_whitelist(cache_key): return frozenset(list_active_phones())

def _whitelist_cache_key():
    # 只看 backend/data/whitelist.db / -wal / -shm 的 mtime
```

2026-09-18 白名单从 SQLite 切到 PG（`.env` 的 `LSX_DB_WHITELIST=postgresql://…`）之后，
**本地 `backend/data/whitelist.db` 再也不被写** ⇒ 三个 mtime 键恒为定值 ⇒ `lru_cache` 永不失效 ⇒
缓存内容 = **进程启动那一刻**从 PG 读到的 active 号码集合。
任何在进程启动之后新增的白名单用户，登录一律 401「手机号未授权」，**直到服务重启**。

## 判据（怎么一秒确认是这个坑）

1. 号码在 PG 里 `active=1`：
   `bash scripts/pg_query.sh 'psql -d lsx_whitelist -c "SELECT phone,name,county,active,created_at FROM whitelist WHERE phone='"'"'<手机号>'"'"'"'`
2. `backend/data/whitelist.db` 的 mtime 早于该号码的 `created_at`，且服务启动时间晚于……总之：
   `ls -la backend/data/whitelist.db*` 的 mtime 长期不动（切 PG 后再没变过），
   `systemctl show labor-survey -p ActiveEnterTimestamp` 早于号码新增时间。
3. 对照组：**进程启动前**建的号登录 200，**启动后**建的号 401 —— 这一条是铁证
   （2026-09-29 实测：13007853370 / 19985507072 → 200；19922793945 → 401）。

## 处置

### 应急（零重启、零中断，首选）

```bash
cd /opt/labor-survey-ai && touch backend/data/whitelist.db
# 立刻验证
curl -sS -X POST http://127.0.0.1:8001/api/auth/login \
  -H 'Content-Type: application/json' -d '{"phone":"<手机号>"}'
```

原理：mtime 变 → 缓存键变 → 下次请求 `list_active_phones()` 重读 PG。**只改 mtime，不动内容、不动 owner**，
不触发 lsdata ACL 事故（对比：`git checkout` 重写文件会丢 owner/ACL）。

### 根治（2026-09-29 17:16 已随重启生效）

`backend/app/infra/auth.py` 的缓存键追加了时间桶：

```python
WHITELIST_CACHE_TTL = int(os.environ.get("LSX_WHITELIST_CACHE_TTL", "10"))
keys.append(int(time.time()) // max(WHITELIST_CACHE_TTL, 1))
```

⇒ 新增/停用白名单 ≤10s 自动生效，不再依赖重启。**该改动要 `systemctl restart labor-survey` 才生效**，
重启前应急流程继续用（管理员每次新增用户后 `touch` 一下）。

隔离验证脚本（只碰 /tmp/sandbox，用显式 `LSX_DB_*` 指向沙箱库，避免 load_dotenv 注入生产 DSN）：
`/tmp/sandbox/cache_ttl_test.py` —— 断言「插入后过 TTL 即可见」「停用后过 TTL 即不可见」。

## 红线提醒

- 判断白名单是否生效，**不能只看 PG 里的 `active=1`**；还要看登录接口是否 200（缓存层在中间）。
- 别用「重启服务」当万能钥匙：先试 `touch` 热刷新，重启要按生产变更纪律要窗口。
- 别直接改 PG 数据"绕过"——`load_whitelist()` 只认 active=1，问题在缓存不在数据。
