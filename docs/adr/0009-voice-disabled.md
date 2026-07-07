# 0009 — 语音识别停用与代码保留

## 状态
已采纳 · 2026-07-05

## 背景
2026-06-21 起，H5 调查员端的"按住说话"语音输入按钮停用。原因是：现代手机输入法的语音转写功能（讯飞/百度/搜狗输入法自带）已足够准确，再做一遍后端 ASR 流式识别是重复造轮子，且增加部署成本与延迟。

## 决策
1. **前端隐藏语音按钮**（H5 index.html 不渲染 mic button）
2. **后端路由停用**：`/api/voice/stream` WebSocket 不再注册
3. **代码完整保留**：`api/voice.py`、`api/_xunfei_auth.py`、`api/_xunfei_auth` 依赖（`XUNFEI_*` 环境变量）整段保留
4. **决策记录**：本 ADR 取代散落在 `main.py` 和 `core/config.py` 中的 `# DISABLED(voice) 2026-06-21` 注释

## 理由
- 投入产出：手机自带语音已经够用，2-5 人在同一会话内反复测都不会触发后端 ASR 价值
- 维护成本：讯飞 token 缓存、WebSocket 连接管理、partial 结果去重都要继续维护，但无业务收益
- 部署成本：`.env` 的 `XUNFEI_*` 仍然占位置，新人 onboarding 会困惑

## 代码考古指南
- **看到了 `# DISABLED(voice)` 注释？** 读本 ADR
- **想恢复 ASR？** 步骤：
  1. `main.py` 取消注释 `from app.api.voice import router as voice_router` 和 `app.include_router(voice_router, tags=["voice"])`
  2. `core/config.py` 取消注释 4 个 `xunfei_*` 字段及其 `_load()` 里的赋值
  3. `.env` 填入 `XUNFEI_APP_ID` / `XUNFEI_API_KEY` / `XUNFEI_API_SECRET` / `XUNFEI_ASR_DOMAIN`
  4. H5 `index.html` 恢复 mic button 渲染（参见 ADR 0001 决策反转前的历史）
  5. 写一条新 ADR 取代本条

## 清理标准（什么时候真删）
满足以下**全部**才考虑清理：
1. 连续 12 个月无任何"恢复语音"需求（自本 ADR 起算）
2. KB / 制度文档未引用语音能力
3. 至少 3 个版本（每个版本 ≥ 6 个月）证明不需要

否则继续保留（成本约 4 个文件、~10KB 代码、零运行时开销）。
