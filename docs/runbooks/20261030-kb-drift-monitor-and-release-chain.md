# 知识包漂移监控与自动发布链路（2026-10-30）

## 背景

知识库内容更新的既有流程是：开发机改内容 → push → 生产机 pull → **再手工做一串**
"构建 release → 影子 Eval → 标 Eval 通过 → 发布 → （新增省份）绑省"。

问题：
1. 这串步骤容易被忘（尤其只说"拉下最新代码"、没提知识库的时候）；
2. 对使用方是黑箱——不知道推上去的内容到底上线没有、走到哪一步。

2026-10-30 落地三层机制解决（本仓库只承载其中两层，另一层在生产机 Hermes 侧）：

| 层 | 载体 | 位置 |
|---|---|---|
| 检测 | `scripts/kb_pack_drift.py` | 本仓库（新增） |
| 固定流程 | Hermes skill `labor-survey-kb-release`（全文见附录） | 生产机 `~/.hermes/skills/`，**不入仓** |
| 定时监控 | Hermes cron 每 2 小时跑检测脚本（monitor 模式：输出无变化=静默不消耗；变化=自动触发链路并汇报） | 生产机 Hermes 侧，**不入仓** |

## 本仓库改动清单

- **新增 `scripts/kb_pack_drift.py`**：按包目录内容哈希对比"源内容 vs 已发布 release"。
- **新增本文档**：`docs/runbooks/20261030-kb-drift-monitor-and-release-chain.md`。
- 其余零改动：后端代码、.env、索引、注册库均未动。

## 脚本用法

```bash
python3 scripts/kb_pack_drift.py            # 打印每个包的状态
python3 scripts/kb_pack_drift.py --record <pack_id> <release_id>   # 发布链路收尾，记录基线
python3 scripts/kb_pack_drift.py --init     # 首次建立基线
```

输出语义（按行、稳定排序、无时间戳——为 monitor 哈希比对设计）：

| 行 | 含义 |
|---|---|
| `OK <包> <版本>` | 源内容与已发布版本一致，无需动作 |
| `DRIFT <包>` | 内容已变更，尚未构建/发布 |
| `NEW <包>` | 内容源未纳入基线（新增省份首次接入） |
| `PENDING <包> <版本>` | 已构建未发布（链路半途残留，可续跑） |
| `CHECK <包>` | 基线与注册库状态不一致，需人工核查 |
| `ORPHAN <包>` | 注册库有包但 packs 目录缺失 |

环境变量：`LSX_REPO_ROOT` 覆盖仓库根（默认 `/opt/labor-survey-ai`）。
基线状态存在**每台机器的** `~/.hermes/kb_pack_state.json`（属运维状态，不入仓；
注册库 `backend/data/knowledge_admin.db` 本就不入仓，两者共同构成"线上视角"）。

## 生产机发布链路（七步，2026-10-30 沙箱全流程演练通过）

演练记录：gz-test-1 —— 构建+登记 19s → 影子实例（独立库/独立索引根，不碰生产）跑
真 Eval 8/8 → 发布门禁（产物 hash + 状态机）→ 核验生产注册库 md5 一致、生产
query_log 零污染、服务未重启。

1. 同步四条硬命令，HEAD == origin/main 才继续；
2. 定新 release-id（`gz-2026-02` 式，绝不复用）；
3. `build_release_index.py --register`（省级包带 `--based-on <全国release>`）；
4. 影子 Eval：备份注册库 → 起 8765 影子后端（4 个 `LSX_DB_*` 全覆盖到 /tmp）→
   沙箱内标过+发布使影子解析到候选 → 跑本包评测集 + 全量 114 题；
5. 两轮全绿才在**生产**注册库 `eval_status=passed` + 发布（门禁强制
   build=success、eval=passed、content_hash 匹配，不可绕过）；任一题失败即停；
6. 仅新增省份绑省（省码查 `knowledge-base/shared/province_catalog.json`）；
7. `--record` 记录基线 → 复检全 OK → 汇报版本号/Eval 数字/绑省与否/零影响核验。

红线：影子写操作只落 `/tmp/sandbox`；不重启 labor-survey 服务；不裸跑 pytest；
Eval 全绿前绝不写生产注册库。

## ADR 0028 注脚（2026-10-30）

ADR 0028 原文："eval_status 永远只能由人工在 Eval 实际通过后更新"。
现按用户确认调整为：**agent 实测两轮 Eval 全绿后自动翻**，审计 actor 仍为
18275117145，"真跑 Eval、全绿才过、发布前必 Eval" 的前提未变。回退方式：把
skill 第 4 步改回"跑完汇报、等用户一句话确认再执行"。

## 无 Hermes 环境下的人工等价流程

见附录 skill 的 7 步（命令均为可直接执行的 bash/python），或沿用
`docs/knowledge-packs.md` 的既有流程（/kb-admin 后台发布/绑定，ADR 0028 分工）。

---

## 附录：Hermes skill `labor-survey-kb-release` 全文（生产机自动化执行体）

```yaml
# 触发：任何对本仓库的 git pull 之后必跑检测；DRIFT/NEW/PENDING/CHECK/ORPHAN → 走七步链路；
# 全 OK → 一句话收工。链路不重启 labor-survey 服务。
```

（完整七步与失败分支即本文"生产机发布链路"一节所列；skill 存放于
生产机 `~/.hermes/skills/devops/labor-survey-kb-release/SKILL.md`，
内容与本节一致，含逐条可执行命令。生产机上的 cron 任务：
每 2 小时执行 `scripts/kb_pack_drift.py`，输出无变化静默，变化才触发并投递结果。）
