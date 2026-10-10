# DESIGN.md — 前端设计契约

> 参照 OpenDesign 的 `DESIGN.md` 品牌契约模式建立。**任何 Agent（Codex / Claude 等）修改 `backend/static/` 下任何页面之前，必须先读完本文件并遵守。**
> 唯一事实源约束：本文件描述的是现有代码的真实状态，与代码冲突时以代码为准，并回来更新本文件。

## 0. 技术边界（不可协商）

- 浏览器原生 API 优先，**不引入任何前端框架 / 构建工具 / CDN 依赖**（ADR 0001 及项目约定）。
- 共享工具函数放 `common.js`：`$()` 选择器、`escapeHtml()`、token 管理；白名单表单逻辑在 `whitelist-form.js`。
- 所有 HTML 文件必须 UTF-8 编码。

## 1. Token 体系（唯一来源：`theme.css`）

所有页面在 `<head>` 最先加载 `<link rel="stylesheet" href="/static/theme.css">`，页面内联 `<style>` 在其后、可覆盖。**token 只定义在 theme.css，页面里不得新增同类硬编码色值。**

两套 token 家族（历史并存，不要新建第三套）：

| 家族 | 使用页面 | 关键变量 |
|------|----------|----------|
| `--c-*` | index（对话页）、quiz | `--c-ink` 文字主色 / `--c-bg` 页面底 / `--c-bg-elev` 卡片 / `--c-line` 描边 / `--c-brand` 品牌色 / `--c-ok --c-warn --c-danger --c-info` 语义色（各配 `-soft` 浅底） |
| `--ink-*` + `--paper` | login、dashboard、whitelist、quiz_admin、quiz-stats（后台页） | `--ink-blue` 品牌蓝 / `--paper` 页面底 / `--text` / `--text-dim` / `--line` / `--good` / `--bad` |

品牌色：统计蓝 `#03499F`（light）/ `#3D8BFF`（dark）。两套家族的品牌、语义色值保持一致。

**暗色模式**：`html[data-theme="dark"]`，由 `common.js` 的 `applyTheme()` 切换。任何新样式必须同时写 light 和 dark 两份；theme.css 已对后台页做了暗色兜底（大表面回退 `--c-bg-elev` 等），但页面私有样式仍需自查。

**圆角 / 动效**：`--radius-card: 14px`、`--radius-btn: 10px`、`--radius-pill: 999px`；过渡时长 `--t-fast: 150ms` / `--t-base: 220ms`，缓动 `--ease`。

## 2. 版式

- 字体栈：`-apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif`（index/quiz 多含 `"Segoe UI", "Hiragino Sans GB"`），`font-family: inherit` 用于所有按钮/输入框。
- 正文行高 1.55；卡片标签 `0.78rem` + `var(--text-dim)`，指标数值 `1.6rem` / 600 / 品牌色；等宽编号用 `ui-monospace`。

## 3. 组件规范

### 后台统一顶部导航（4 页强制）
`dashboard` / `quiz-admin` / `whitelist-admin` / `quiz-stats` 的 header 统一挂 `admin-header` + `adminNav` + `roleTag`，页面初始化调用 `common.js` 的 `initAdminNav(current)`。测验管理入口仅系统管理员和市级/省级业务管理员可见。**新增后台页面必须沿用这套导航。**

### 卡片与表格（后台页）
- 大表面：白底卡片 + `#CBD5E1`（slate-300）描边 + 柔和投影；页面底 `#E9EEF5`。
- 模块标题条：`section h2` 负 margin 贴边 + `#F1F5F9` 底；表头同底色、`#334155` 文字。
- 表格约定：「核心列 + 详情展开」，窄屏 ≤768px 卡片化。

### 弹窗
限高 `calc(100vh - 32px)`，内容在 body 内滚动。

### 按钮
主 CTA 渐变：`linear-gradient(135deg, var(--c-brand), #2FA8E0)`；普通按钮后台暗色模式自动翻转为深色表面（theme.css 统一处理，页面无需自己写）。

## 4. Agent 硬规则

1. **token 优先**：颜色/圆角/时长一律用 CSS 变量；确需新硬编码值时，先评估能否并入 theme.css 的 token（注意：改 theme.css 属于改旧文件，需按项目红线评估），否则在本文件 §5 登记。
2. **新页面清单**：UTF-8 → 引 theme.css → 复用字体栈 → 后台页挂统一导航 + `initAdminNav` → 暗色模式自查 → ≤768px 自查。
3. **不迁移框架、不复制粘贴他处设计系统**；借鉴 OpenDesign 等项目时只抄约定，不引依赖。
4. 用户输入渲染必须过 `escapeHtml()`。

## 5. 已知缺口（登记待迁，未完成不改旧文件）

以下硬编码值已存在于 theme.css（后台统一表面），属于历史遗留、暂不动；未来若做视觉升级，优先将它们提升为 token：

- `#E9EEF5` 后台页面底（light）
- `#CBD5E1` 后台卡片/表单描边
- `#F1F5F9` / `#D7DFEA` / `#334155` 模块标题条与表头
- `#2FA8E0` 主 CTA 渐变终点色
