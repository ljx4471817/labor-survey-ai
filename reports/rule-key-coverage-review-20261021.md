# 省级 rule_key 覆盖策略复核材料

- 生成日期：2026-10-21
- 目的：为「省级 direct hit 启发式 vs 严格 rule_key 覆盖」的业务决策提供事实清单（ADR 0026 / PRD 待办）。

## 当前覆盖逻辑（combination_retriever.py）

1. 省级条目 rule_key 与全国条目 rule_key 相同 → 省级确定性覆盖（PRD 主路径）。
2. 省级条目 direct hit（cosine ≥ 0.75 且不低于全国 top1）→ 也强制优先（启发式，贵州 shadow 期依赖）。

## 贵州包 rule_key 清单与全国同键情况

| rule_key | 贵州 QA | 全国同键条目 |
|---|---|---|
| `F10.contractor.affiliated_waiting_project` | 434 | 无 |
| `F10.contractor.no_active_project` | 429 | 无 |
| `F10.contractor.no_activity` | 430 | 无 |
| `F10.contractor.stopped_or_retired` | 433 | 无 |
| `F16.contractor.weekly_hours` | 431 | 无 |
| `F20_21.flexible_worker_employment_identity` | 209 | 无 |
| `F27.non_monthly_income` | 357 | 无 |
| `H3.student_vacation_registration` | 355 | 无 |

## 关键事实

- 贵州包现有 8 个 rule_key、8 条 QA；全国包**没有任何同键条目**（Phase 0 刻意不分配，见 split_knowledge_packs.py 注释）。
- 因此当前线上唯一生效的覆盖通道是 direct hit 启发式；rule_key 匹配通道实际为空转。
- 若改为「严格 rule_key 覆盖」，在补齐全国同键条目之前，所有省级口径覆盖会失效，回退为全国口径——这是行为回退，不是收紧。

## 决策选项

1. **保留现状（推荐，风险最低）**：direct hit 启发式 + rule_key 双通道并存；shadow 期数据（reports/kb-pack-shadow-20261019.md，8 条覆盖日志）继续观察。
2. **严格 rule_key（PRD 原案）**：需先由业务人员为 8 个 rule_key 逐条指定全国同键 QA（或新写全国通用条目），再移除启发式；工作量在内容侧，不在代码侧。
3. **混合**：保留启发式但加日志区分两条通道（override_used 目前不区分来源），统计一个周期后再裁剪。

## 业务复核清单

- [ ] 逐条确认 8 个 rule_key 的全国通用口径是否存在（存在→补全国条目；不存在→维持启发式）。
- [ ] 确认 direct hit 阈值 0.75 是否需要按省调整。
- [ ] 确认后更新 ADR 0026 状态与 kb_pack_resolver 文档。
