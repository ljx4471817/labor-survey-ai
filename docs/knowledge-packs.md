# 多知识包与省级口径运维手册

> 本文面向需要构建、发布、回滚或灰度多知识包的开发 / 运维人员。需求与决策见
> `docs/PRD-多知识包与省级口径.md`；架构决策见 `docs/adr/0026-多知识包与省级口径覆盖.md`。

## 当前能力

- 全国基础包 + 省级覆盖包；当前有 `national-base` 与 `guizhou`。
- 每个 release 使用独立 Chroma / BM25 索引目录。
- 省级条目通过 `rule_key` 确定性覆盖全国同键内容。
- 账号按 `province` 归一为 `province_code` 后动态解析知识包。
- 管理页 `/kb-admin` 支持包、版本、省份绑定、审计与检索预览。
- 生产运行总开关 `LSX_KB_PACK_ENABLED` 默认 `0`；关闭时完全使用旧 flat 检索链路。

## 关键配置

| 变量 | 说明 |
|---|---|
| `LSX_KB_PACK_ENABLED` | `1` 启用多知识包检索；默认 `0`。 |
| `KB_RELEASES_ROOT` | release 索引根目录，默认 `backend/data/kb_releases`。相对路径基于项目根解析。 |
| `LSX_DB_KB_ADMIN` | 知识包注册库后端；PG DSN 或 SQLite 路径。默认 `backend/data/knowledge_admin.db`。PG 侧由统一迁移脚本建表并迁移数据；`artifact_manifest.json` 会记录产物 hash。 |

## 目录

```text
knowledge-base/
├── packs/
│   ├── national-base/       # QA、eval、manifest、原始 markdown
│   └── guizhou/
└── shared/
    ├── province_catalog.json
    └── indicator_catalog.json

backend/data/kb_releases/
└── <release_id>/
    ├── chroma/
    ├── bm25_index.json
    └── chunks.jsonl
```

`knowledge-base/packs/` 是内容源；`backend/data/kb_releases/` 是构建产物，不入仓。

## 常用流程

### 1. 重新拆分内容包

```bash
python scripts/split_knowledge_packs.py --dry-run
python scripts/split_knowledge_packs.py
```

拆分会保留原始 `source`，校验 QA / Eval / Chunk 数据守恒，并生成包级 manifest 与报告。

### 2. 构建 release 索引并登记（ADR 0028）

```bash
# 更新贵州包（全国包省略 --based-on）
python scripts/build_release_index.py --release-id gz-2026-02 --pack-root knowledge-base/packs/guizhou --register --actor 15519106778 --based-on nat-2026-01

# 新增省份：先扩展 split_knowledge_packs.py（目前硬编码全国/贵州），包不存在时 --register 会自动建 draft 包
```

规则：

- release 目录不可重建；失败后使用新 `release_id`，不要覆盖旧版本。
- QA / chunk Chroma 有失败条目时脚本会失败，不会继续构建 BM25。
- `--register` 自动写注册库：包不存在时按 pack manifest 建 draft 包；`content_hash` 自动取 artifact manifest 的 canonical hash（发布时强校验，产物被改会被 409 拒绝）；`build_status=success`、`eval_status=pending`。
- `eval_status` 永远只能人工更新：Eval 实际通过后调 `PATCH /api/admin/kb/releases/{id}/status` 或在后台改。
- 部分构建（`--skip-chroma` / `--skip-bm25`）不写注册库，产物不完整不可发布。

### 3. 发布与回滚（后台）

`/kb-admin`（职责见 ADR 0028：后台只做运营动作，建包 / 登记在开发机脚本完成）：

1. 确认目标 release 的构建 = success、Eval = passed（未通过直接发布会 422）。
2. 发布 release；接口校验 `artifact_manifest.json`、语义产物 hash（`bm25_index.json` / `chunks.jsonl`）和 content_hash 绑定。chroma 目录下所有文件（含 `chroma.sqlite3` 本体）客户端打开即可能被 Chroma 重写——2026-10-24 生产实测（恢复演练后字节 hash 变化、向量条数不变）——因此 chroma 文件只强制存在性、不参与字节 hash；篡改 `bm25_index.json` / `chunks.jsonl` / 删除任一必需文件仍会被门禁拦截。
3. 绑定省级代码到省级包（保存后该省账号下次提问立即生效）。
4. 回滚 = 将 `current_release_id` 切回历史 release，不删数据，可随时再切回。

权限：

- 写操作：系统管理员。
- 查看知识包状态：系统管理员或省级业务管理员。
- 市级 / 区县 / 普通用户：无知识包管理入口。

### 4. 启用与回退

启用：

```env
LSX_KB_PACK_ENABLED=1
```

回退到旧 flat 检索：

```env
LSX_KB_PACK_ENABLED=0
```

如需回退某个省的包版本，在 `/kb-admin` 将该包 `current_release_id` 切回已通过的历史 release；不要删除旧 release 目录。

## 日志与追踪

`query_log` 记录：

- `province_code`
- `pack_mode`
- `kb_id / release_id`
- `global_entry_key`
- `override_used`
- `fallback_reason`
- `national_release_id / provincial_release_id`

来源快照中的 `qa_id` 仍显示原始三位 ID；跨包唯一追踪使用 `global_entry_key`。

## 备份与恢复

长期备份范围必须包含两部分：

1. 五个数据库：`whitelist`、`conversations`、`query_log`、`quiz`、`knowledge_admin`。
2. `backend/data/kb_releases/` 文件卷，包括每个 release 的 Chroma、BM25、chunks 和 `artifact_manifest.json`。

恢复演练时至少验证：

- `kb_packages.current_release_id` 指向的 release 存在；
- 对应 `artifact_manifest.json` 存在且 hash 全部匹配；
- 省份绑定正确；
- `LSX_DB_KB_ADMIN` 指向恢复后的库；
- `LSX_KB_PACK_ENABLED=1` 后问答能解析 release。

## 质量门禁

改动后至少执行：

```bash
python scripts/validate_faq.py
python scripts/split_knowledge_packs.py --dry-run
cd backend && pytest tests/ -q
python scripts/run_eval.py --phone 13985000001
```

新增省级包还需要：

- 本省 Eval 全绿；
- 跨省隔离 Eval，确保 A 省账号不能命中 B 省内容；
- 覆盖规则人工复核，尤其是同 `rule_key` 的全国条目。

## 双轨期与历史注意事项

- **双轨期（`LSX_KB_PACK_ENABLED=0`）线上仍走旧 flat 索引**：改了 `knowledge-base/qa/faq.json` 后必须跑 `rebuild_all.py`，否则线上回答不变；packs 与 flat 内容可能漂移，以 git 与拆分脚本对齐。
- **知识包按适用范围归类，不按来源建包**（ADR 0027）：采纳的外省资料（如四川题库）并入对应包并保留原始 `source`，不生成「四川包」。
- **两条登记写路径刻意不对称（ADR 0028 决策 4 的注脚）**：CLI `--register` 验后写（canonical hash 自动计算 + 幂等重登记 + 自动建包）；API `POST /releases` 信任输入原样入库，一致性由发布时的 content_hash 强门禁兜底。若未来出现第二个脚本写入方，应先收敛到 `register_release` 语义再扩。
- **manifest 口径变更时需同步重绑 content_hash**（2026-10-23 一次性修正：chroma 派生段文件移出 hash 范围，`nat-2026-01` / `gz-2026-01` 已重绑并留审计）；正常流程无此步骤，重绑仅适用于 manifest 生成规则修正。
- **rule_key 覆盖策略现状**：目前仅贵州包有条目带 `rule_key`，全国包无同键条目，实际生效通道是省级 direct hit 启发式；切「严格 rule_key 覆盖」前必须先补全国同键条目，现状与决策选项见 `reports/rule-key-coverage-review-20261021.md`。
