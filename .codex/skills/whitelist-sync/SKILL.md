---
name: whitelist-sync
description: 把 docs/权限表.xlsx 同步到 backend/data/whitelist.db。当用户说「同步权限表到数据库」「上传白名单」「whitelist sync」「把 xlsx 入库」时触发。流程：先 dry-run 预览新增/更新/软删除数量，用户确认后再真实写入。脚本已处理：调查员+管理人员双 sheet 解析、姓名内部空格合并、手机号去重、不在 xlsx 的号码软删除（保护 13985000001-4 测试号）。
---

# 白名单同步（权限表 xlsx → whitelist.db）

把 `docs/权限表.xlsx` 的「调查员」和「管理人员」两个 sheet 解析后 upsert 到 `backend/data/whitelist.db`，并软删除 xlsx 中已不存在的号码。

## 触发词

- "同步权限表到数据库" / "上传白名单" / "whitelist sync" / "把 xlsx 入库"
- 用户提到 `权限表.xlsx` 与 `whitelist.db` 之间的数据同步

## 执行步骤

1. 在项目根目录跑 dry-run，**只读不写**，把新增/更新/软删除数量告诉用户：
   ```bash
   python scripts/sync_whitelist_xlsx.py --dry-run
   ```
2. 等用户确认后跑真实同步：
   ```bash
   python scripts/sync_whitelist_xlsx.py
   ```
3. 打印脚本输出的 `inserted` / `updated` / `deleted` 三计数，作为最终结果。

## 关键约定

- 默认 xlsx 路径：`docs/权限表.xlsx`（脚本内 `DEFAULT_XLSX`）。如用户给了别的路径，用 `--xlsx <path>`。
- 受保护号码（`PROTECTED_PHONES`）：`13985000001`–`13985000004`，永不软删除。
- 同一手机号在两个 sheet 都出现时，先读到的（调查员优先）生效。
- 姓名内部多空格会被合并为单个空格；手机号非 11 位数字的行会被跳过。
- 真实写入是**幂等 upsert**：已存在则更新姓名/区域/层级/备注，不存在则插入；xlsx 中不存在的号码软删除（`active=0`）。

## 字段映射

| xlsx 列 | whitelist.db 列 |
|--------|----------------|
| 省 | province |
| 市 | city |
| 县 | county |
| 调查小区（仅调查员 sheet） | community |
| 姓名 | name |
| 联系电话 | phone (PK) |
| 管理员层级 | admin_level |
| 备注 | remark |

## 失败处理

- 脚本报错 → 直接展示错误，不要自己重写同步逻辑。
- xlsx 被 Excel 占用 → 提示用户关闭 Excel 后重跑。
- 真实写入后 → 用 `whitelist.list_all(active_only=True)` 抽查几条确认。
