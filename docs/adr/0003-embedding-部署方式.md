# ADR 0003：Embedding 部署方式

## 状态

已确认（2026-06-15）

## 背景

RAG 需要把文本转成向量。方案原计划本地部署 BGE 模型，需要决策是本地还是 API。

## 决策

**走 API：优先 BGE API（智源开放平台），备选 DashScope（阿里云）**。

## 考虑的备选

### 备选 1：BGE API / DashScope API（已选）
- ✅ 不占服务器资源（2C4G 不需要升级）
- ✅ 服务稳定、有 SLA
- ✅ 维护成本低（API 提供方负责模型升级）
- ❌ 数据出服务器（用户问题传到 API 提供方）
- ❌ 按调用次数计费（成本极低，可忽略）

### 备选 2：本地部署 bge-large-zh-v1.5
- ✅ 数据不出服务器
- ✅ 单次调用零边际成本
- ❌ FP16 约 1.3GB 显存，2C4G 服务器无 GPU 只能 CPU 推理，速度慢（单次 200-500ms）
- ❌ 需升级服务器到 4C8G + GPU 实例，月成本翻 5-10 倍
- ❌ 需自行处理模型升级、性能监控

### 备选 3：本地部署 bge-small-zh
- ✅ 资源占用小（~100MB 显存）
- ✅ 数据不出服务器
- ❌ 检索质量比 large 下降明显（中文领域约 -10% 召回率）
- ❌ 仍需运维

## 影响

### 当前决策
- **首选 BGE API**（智源开放平台）
- 备选 DashScope `text-embedding-v3`（阿里云，配套 Qwen 生态）
- 在 `backend/app/core/config.py` 中用环境变量切换：
  ```python
  EMBEDDING_PROVIDER=bge  # 或 dashscope
  BGE_API_KEY=xxx
  DASHSCOPE_API_KEY=xxx
  ```

### 数据合规
- 用户问题传 BGE / DashScope API 提供方
- 知识库内容也会传（首次构建时）
- **知识库是公开制度文档，无隐私问题**
- 用户问题是调查员提问（不涉及居民个人信息）
- 合规风险已评估可接受

### 切换路径
如果未来需要本地部署：
1. 启动 GPU 实例（4C8G + T4 GPU）
2. 用 FlagEmbedding 库加载 bge-large-zh-v1.5
3. 实现 `LocalEmbeddingProvider` 替代 API Provider
4. 切换环境变量 `EMBEDDING_PROVIDER=local`

## 参考

- BGE 文档：https://github.com/FlagOpen/FlagEmbedding
- 智源开放平台：https://open.bigmodel.cn/
- DashScope：https://dashscope.aliyun.com/