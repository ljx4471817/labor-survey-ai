# 对抗性测试报告（2026-10-20）

## 结论

本地隔离环境对抗性测试完成：**65/65 通过**。  
测试中发现并修复 1 个真实缺陷：`PATCH /api/admin/kb/releases/{release_id}/status` 调用 `update_release_status()` 时误传位置参数，导致已发布版本状态修改接口 500。已补充回归测试。

## 测试环境

- 后端：本地 uvicorn，端口 8777 / 8778
- 数据库：全部指向临时目录，隔离 whitelist / conversations / query_log / quiz / knowledge_admin
- 知识索引：只读复用 `nat-2026-01` 与 `gz-2026-01`
- 开关：`LSX_KB_PACK_ENABLED=1`
- 限流：主测试 1000/min；限流专项 2/min
- 未写生产库，未修改生产 release

## 测试账号

| 角色 | 用途 |
|---|---|
| 系统管理员 | 全量管理、问答、发布 / 回滚 |
| 省级业务管理员 | 只读本省知识包组合 |
| 市级业务管理员 | 验证无知识包权限 |
| 贵州调查员 | 验证省级覆盖 |
| 四川调查员 | 验证跨省隔离 |

## 覆盖结果

| 类别 | 结果 |
|---|---:|
| 鉴权与登录边界 | 6/6 |
| 聊天输入边界与注入类 payload | 8/8 |
| 省级覆盖与跨省隔离 | 4/4 |
| 知识包 RBAC 与数据范围 | 5/5 |
| 注册库写入门禁与参数攻击 | 13/13 |
| 预览接口边界与权限 | 4/4 |
| 审计 | 1/1 |
| query_log / 跨省日志字段 | 2/2 |
| 会话与反馈闭环 | 10/10 |
| release 重复 / 包归属 / 不可变状态 | 4/4 |
| 城市账号权限隔离 | 2/2 |
| 限流 | 1/1 |

## 关键验证

- 贵州账号命中 `guizhou:gz-2026-01:429`，来源标记 `override_used=true`。
- 四川同题未命中贵州覆盖，`provincial_release_id` 为空。
- 市级业务管理员访问知识包 API 返回 403。
- 前端导航对市级业务管理员隐藏知识包入口。
- 省级业务管理员只看到全国包 + 贵州包及其 release。
- 路径穿越 release ID、未知省代码、national 包绑省、pending 发布、已发布状态修改均被拒绝。
- `query_log` 记录 `pack_mode / kb_id / release_id / override_used / fallback_reason / national_release_id / provincial_release_id`。
- 多轮会话、反馈去重、负面反馈长度校验、热点接口正常。
- `LSX_RATE_LIMIT_PER_MINUTE=2` 时第 3、4 次请求返回 429。

## 发现并修复

- `backend/app/api/kb_admin.py`：`update_release_status()` 调用参数错误导致 500。
- 已新增回归测试：已发布 release 状态修改必须返回 422。

## 未覆盖 / 后续建议

- 浏览器端视觉和真实点击回归未做，本次以 HTTP API + 静态页检查为主。
- 并发压力和 PostgreSQL 双后端对抗未做。
- LLM 回答质量不在对抗性安全测试范围；现有 Eval 复跑为 114/114。
