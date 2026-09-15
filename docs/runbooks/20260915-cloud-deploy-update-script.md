# 云服务器一键更新脚本

## 用途

云服务器上的常规更新不再需要分别判断“是否改了知识库”“是否改了依赖”。把代码同步到 GitHub 并合并后，在服务器执行：

```bash
cd /opt/labor-survey-ai
bash scripts/deploy_update.sh
```

脚本会按变更内容自动执行：

1. `git fetch origin main`，并要求本地 `main` 能快进合并。
2. 对比本地和远端提交，列出更新文件。
3. 如果 `backend/requirements.txt` 变了，用 `backend/venv/bin/python` 安装依赖。
4. 如果 `faq.json`、知识库 markdown、指标目录或索引构建脚本变了，先跑 `validate_faq.py`，再执行 `rebuild_all.py --incremental`。
5. 有代码、依赖或索引更新时重启 `labor-survey` systemd 服务；没有更新时不重启。
6. 等待 `/health` 返回成功。

## 常用参数

```bash
# 只看会做什么，不改文件
bash scripts/deploy_update.sh --dry-run

# 全量重建索引
bash scripts/deploy_update.sh --full-rebuild

# 没有新提交，但想强制重启
bash scripts/deploy_update.sh --force-restart

# 只更新文件和索引，不重启服务
bash scripts/deploy_update.sh --skip-restart

# systemd 服务名不同
bash scripts/deploy_update.sh --service labor-survey-prod
```

## 安全边界

- 脚本只允许 `main` 快进合并，不会 `reset --hard`，也不会覆盖本地未提交改动。
- 索引默认只增量重建；只有显式传 `--full-rebuild` 才全量重建。
- 脚本会检查 systemd 服务存在；如果服务名不同，用 `--service` 指定。
