# 浏览器真实点击与视觉回归报告（2026-10-20）

## 结论

使用 Playwright + Chromium 完成 23/23 项浏览器断言，生成 10 张关键截图。  
未发现阻断性视觉回归；无 console / pageerror。城市管理员直连提示文案为“初始化失败：无知识包管理权限”，语义可读，但建议后续去掉“初始化失败”前缀。

## 环境

- 本地服务：`http://127.0.0.1:8779`
- 浏览器：Playwright Chromium headless
- 视口：1440×960、390×844
- DPR：2
- 数据库：全部临时隔离
- 开关：`LSX_KB_PACK_ENABLED=1`
- 服务已停止

## 覆盖结果

| 场景 | 结果 |
|---|---|
| 登录页加载 | PASS |
| 登录页明暗切换 | PASS |
| 未授权手机号错误提示 | PASS |
| 系统管理员真实登录并进入知识包页 | PASS |
| 顶部导航显示知识包入口 | PASS |
| 知识包 / release / 绑定列表渲染 | PASS |
| 知识包预览真实点击 | PASS |
| 新建知识包真实点击 | PASS |
| 停用知识包真实点击 | PASS |
| pending release 发布点击返回后端校验提示 | PASS |
| 390px 移动端无横向溢出 | PASS |
| 暗色模式渲染 | PASS |
| 省级管理员真实点击知识包导航 | PASS |
| 省级管理员无新建表单 / 审计卡片 | PASS |
| 省级管理员列表只含全国包 + 本省包 | PASS |
| 市级管理员导航隐藏知识包入口 | PASS |
| 市级管理员直连页面被拒绝 | PASS |
| 系统 / 省级管理员页面无 console / page error | PASS |

## 视觉检查

截图均非空白，明暗模式像素分布差异明确：

- 登录页：`01-login-light.png`、`02-login-dark.png`
- 系统管理员：`04-kb-admin-system-light.png`
- 检索预览：`05-kb-admin-preview.png`
- 新建并停用：`06-kb-admin-created-disabled.png`
- 移动端：`07-kb-admin-system-mobile.png`
- 暗色模式：`08-kb-admin-dark.png`
- 省级管理员：`09-kb-admin-province.png`
- 市级管理员拒绝页：`10-kb-admin-city-denied.png`

## 发现的小问题

- 城市管理员直连 `/kb-admin` 时，提示是：
  - `初始化失败：无知识包管理权限`
  - 语义明确，但建议改成 `无知识包管理权限`。
- 停用 / 解绑 / 回滚仍无二次确认。
- 禁用后的知识包没有“启用”按钮。

以上不阻断，建议 Phase 4 灰度前处理。
