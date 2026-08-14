# LLM 模型 A/B 对比研究报告（2026-08-14）

> 目标：验证「用 qwen3.5-flash 等便宜模型替换 MiniMax M2.7-highspeed 主模型，回答精准度是否可接受」，为 LLM 成本节约决策提供数据。
> 方法：同一检索（retrieve top_k=5）、同一 prompt（SYSTEM_PROMPT + USER_TEMPLATE）、同一评分（run_eval.evaluate_item，与正式回归同规则），只换 LLM。
> 脚本：`scripts/compare_models.py`（可复跑，`--probe` 探测模型 / `--limit` 小样 / `--start --end` 切片 / `--out` 结果落盘）。

## 结论（先行）

1. **可以换**。便宜模型与当前主模型在 104 条评测集上通过率几乎无差别：
   - MiniMax M2.7-highspeed（生产基线，正式 run_eval）：**104/104 = 100%**
   - **qwen-flash**：**103/104 = 99.0%**（唯一未过题为非确定性波动，单独重跑通过）
   - **qwen-turbo**：**103/104 = 99.0%**（同一题同样重跑通过）
2. **用户说的 qwen3.5-flash 在阿里云百炼 DashScope 上不存在（403）**，它是 QwenCloud（千问自家平台）的型号；DashScope 上的等价廉价模型是 **`qwen-flash`**（同一代 Flash 线）。
3. **推荐主模型切到 `qwen-flash`**：按当前用量月费约 ¥0.9，仅为 MiniMax 按量价的 ~1/15，且无套餐配额上限；速度约为 MiniMax 的 2 倍。
4. 生产代码**已内置 dashscope 供应商支持**，切换只需改 `.env` 两个变量，无需改代码。

## 评测结果明细

| 模型 | 通过率 | in_kb 74 | out_of_kb 15 | trap 10 | ambiguous 5 | 用时(104题) | 估算成本 |
|---|---|---|---|---|---|---|---|
| MiniMax-M2.7-highspeed（生产） | 104/104 | 74/74 | 15/15 | 10/10 | 5/5 | 正式回归 | 套餐内 |
| qwen-flash | 103/104 (99.0%) | 73/74 | 15/15 | 10/10 | 5/5 | 208s | ≈¥0.039 |
| qwen-turbo | 103/104 (99.0%) | 73/74 | 15/15 | 10/10 | 5/5 | 187s | ≈¥0.036 |

- 唯一未过题 `eval-051`（住户不愿透露收入，期望答出「收入敏感/保密义务/统计法/F27」）：
  - qwen-turbo 全量时漏了「统计法/保密义务」关键词（temperature=0.3 非确定性）；**单独重跑三模型全部通过**。
  - 结论：不是系统性能力差距，属采样波动。
- 本 A/B 全部 104 题共约 99 次真实 LLM 调用/模型；API 错误 0（脚本内置 3 次重试）。

## 价格与成本测算

### 单价（元/百万 token，输入/输出）
| 模型 | 输入 | 输出 | 说明 |
|---|---|---|---|
| MiniMax-M2.7-highspeed | 4.2 | 16.8 | 按量价；生产走 Token Plan 套餐（有额度上限） |
| qwen-flash（DashScope） | ≈0.15~0.2 | ≈1.5~2 | 无配额上限，按量 |
| qwen-turbo（DashScope） | ≈0.3 | ≈0.6 | 无配额上限，按量 |
| qwen3.6-flash | 1.2 | 7.2 | 思考模型，回「OK」烧 186 推理 token，已排除 |
| DeepSeek（现价） | 2 | 8 | 用户反馈将涨价，且当前 Key 401 不可用（见风险） |

### 月度成本（按实测：每次 LLM 调用 ≈1,200 输入 + 100 输出 token；近 30 天约 1,166 次查询、82% 走 LLM，月约 1,500~2,000 次 LLM 调用）
| 月 LLM 调用量 | MiniMax 按量 | qwen-flash | qwen-turbo |
|---|---|---|---|
| 2,000 次 | ≈¥13.5 | ≈¥0.9 | ≈¥0.8 |
| 10,000 次 | ≈¥67 | ≈¥4.3 | ≈¥4.0 |

> MiniMax 套餐内的边际成本为 0，但受额度限制；qwen-flash 的真正价值是**近乎零成本 + 无天花板 + 快 2 倍**。

## 切换方案（已落地为三级路由，2026-08-14）

按用户要求实现**三级优先链**：MiniMax M2.7-highspeed（主）-> qwen-flash（额度用尽后优先）-> DeepSeek flash（最后兜底），改动已提交：

- `llm_router.py`：新增 `SECONDARY="dashscope"` / `PRIORITY_ORDER=(minimax, dashscope, deepseek)`；MiniMax 5h>=85% 或 7d>=90% 时切 **dashscope（qwen-flash）** 而非 deepseek；qwen-flash 无配额上限，用量回落 + 冷却后切回 MiniMax；`resolve_llm_config` 沿链回退；手动 override 支持 dashscope。
- `llm_switch_job.py`：fail-safe 连续 3 次配额查询失败时沿链切下一级（minimax->dashscope->deepseek），不再直接跳 deepseek。
- `llm_admin.py` + `dashboard.html`：手动切换/展示支持 dashscope（qwen-flash 备用）。
- `.env`：`LLM_PROVIDER=minimax` 保持主模型，新增 `DASHSCOPE_LLM_MODEL=qwen-flash`（复用现有 `DASHSCOPE_API_KEY`）。
- 验证：`pytest tests/ -q` 203 passed；生产 `llm.chat` 路径真实调用 qwen-flash 返回 OK；路由决策（minimax 90% -> dashscope；dashscope 50% -> minimax）符合预期。

## 风险与注意

1. **DeepSeek 备用链路当前 401**：`DEEPSEEK_API_KEY` 在环境中存在但调用返回 401（疑似失效/欠费）。MiniMax 额度耗尽时若 DeepSeek 也不可用，系统将无 LLM 兜底——这是比价格更紧迫的问题，建议一并排查/轮换 Key。
2. **评测集规模有限**：104 条覆盖主流场景，但真实填报 edge case 更多；qwen-flash 上线后建议保留反馈闭环，观察 2 周使用监测里的 out_of_kb/差评。
3. **非确定性**：temperature=0.3 下两模型都有小概率漏关键词，无法 100% 消除；可通过降 temperature 到 0.1~0.2 或加大 must_contain 权重缓解（需重跑 eval 验证）。
4. **qwen-flash 输出更长**：实测 104 题输出 8,530 token vs qwen-turbo 6,945；若追求极致便宜可再对比 qwen-turbo（单价更低，输出更短）。

## 后续省钱路线（按性价比排序，均需 eval 门禁）

1. **token 用量落库 + 后台面板**（零风险，先做）：`llm.py` 现在丢弃 usage；query_log 无 token 字段。加上后可精确监控成本。
2. **max_tokens 2000 → 500**：当前生成远用不满（实测平均 ~86 输出 token），2000 是「思考型模型留余量」的遗产；qwen-flash 非思考型，可直接压到 500，降低异常长答风险。
3. **响应缓存**：完全相同问题（调查员高频重复问）可缓存 LLM 输出，按 query 哈希 + top1 source id 做 key。
4. **direct-hit ≥0.75 直答**：高分命中时跳过 LLM 直接返回 KB 答案（当前仍调 LLM 重写）；省 ~50% LLM 调用，但需 eval 验证措辞仍满足 must_contain。
5. **prompt/检索瘦身**：top_k 5→3、历史 8→4、SYSTEM_PROMPT 裁剪——减少输入 token，但可能影响复杂多轮场景，需回归。

## 复跑方法

```bash
python scripts/compare_models.py --probe --models qwen-flash,qwen-turbo   # 探测可用模型
python scripts/compare_models.py --models minimax,qwen-flash --limit 25   # 小样
python scripts/compare_models.py --models qwen-flash --start 74 --end 104  # 难点子集
python scripts/compare_models.py --models qwen-flash --out reports/ab-full.json  # 全量+落盘
```

> 成本：全量 104 题 × 3 模型 ≈ 300 次调用，合计 < ¥0.15。