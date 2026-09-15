#!/usr/bin/env bash

# 云服务器一键更新：拉代码 -> 按需装依赖 -> 按需增量重建索引 -> 重启 -> 健康检查。

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

REMOTE="origin"
BRANCH="main"
SERVICE="labor-survey"
VENV_DIR="backend/venv"
HEALTH_URL="http://127.0.0.1:8001/health"
FULL_REBUILD=false
FORCE_REBUILD=false
FORCE_INSTALL=false
FORCE_RESTART=false
SKIP_RESTART=false
DRY_RUN=false
RESTART_COMMAND="${DEPLOY_RESTART_COMMAND:-}"
NEED_RESTART=false

usage() {
  cat <<'EOF'
用法:
  bash scripts/deploy_update.sh [选项]

选项:
  --branch NAME           拉取分支，默认 main
  --remote NAME           git remote，默认 origin
  --service NAME          systemd 服务名，默认 labor-survey
  --venv PATH             虚拟环境目录，默认 backend/venv
  --health-url URL        重启后的健康检查地址
  --full-rebuild          全量重建索引，默认只对变更知识库做增量重建
  --force-rebuild         即使知识库源文件没变化也重建
  --force-install         即使 requirements.txt 没变化也安装依赖
  --force-restart         即使没有新提交也重启服务
  --skip-restart          只更新文件和索引，不重启服务
  --restart-command CMD   覆盖默认 systemd 重启命令
  --dry-run               只检查差异并打印计划，不改文件
  -h, --help              显示帮助
EOF
}

log() {
  printf '\n==> %s\n' "$*"
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --branch) BRANCH="${2:?缺少分支名}"; shift 2 ;;
    --remote) REMOTE="${2:?缺少 remote 名}"; shift 2 ;;
    --service) SERVICE="${2:?缺少服务名}"; SERVICE="${SERVICE%.service}"; shift 2 ;;
    --venv) VENV_DIR="${2:?缺少 venv 目录}"; shift 2 ;;
    --health-url) HEALTH_URL="${2:?缺少健康检查 URL}"; shift 2 ;;
    --full-rebuild) FULL_REBUILD=true; shift ;;
    --force-rebuild) FORCE_REBUILD=true; shift ;;
    --force-install) FORCE_INSTALL=true; shift ;;
    --force-restart) FORCE_RESTART=true; shift ;;
    --skip-restart) SKIP_RESTART=true; shift ;;
    --restart-command) RESTART_COMMAND="${2:?缺少重启命令}"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage; die "未知参数: $1" ;;
  esac
done

command -v git >/dev/null 2>&1 || die "找不到 git"
command -v curl >/dev/null 2>&1 || die "找不到 curl，健康检查需要它"

if command -v flock >/dev/null 2>&1; then
  LOCK_FILE="${TMPDIR:-/tmp}/labor-survey-ai-deploy.lock"
  exec 9>"$LOCK_FILE"
  flock -n 9 || die "另一个部署正在运行：$LOCK_FILE"
fi

CURRENT_BRANCH="$(git branch --show-current)"
[[ -n "$CURRENT_BRANCH" ]] || die "当前不在分支上，请先 checkout $BRANCH"
[[ "$CURRENT_BRANCH" == "$BRANCH" ]] || die "当前分支是 $CURRENT_BRANCH，请先切换到 $BRANCH"

if ! git diff --quiet || ! git diff --cached --quiet; then
  die "工作区有未提交改动，请先处理后再部署"
fi

log "获取 $REMOTE/$BRANCH"
git fetch --prune "$REMOTE" "$BRANCH"
REMOTE_HEAD="$(git rev-parse FETCH_HEAD)"
LOCAL_HEAD="$(git rev-parse HEAD)"

if [[ "$LOCAL_HEAD" == "$REMOTE_HEAD" ]]; then
  CHANGED_FILES=""
  HAS_UPDATE=false
else
  if ! git merge-base --is-ancestor "$LOCAL_HEAD" "$REMOTE_HEAD"; then
    die "本地 $LOCAL_HEAD 不是 $REMOTE/$BRANCH 的祖先，禁止自动合并"
  fi
  CHANGED_FILES="$(git diff --name-only "$LOCAL_HEAD" "$REMOTE_HEAD")"
  HAS_UPDATE=true
fi

REQUIREMENTS_CHANGED=false
if [[ -n "$CHANGED_FILES" ]] && grep -Fxq "backend/requirements.txt" <<<"$CHANGED_FILES"; then
  REQUIREMENTS_CHANGED=true
fi

KB_REBUILD=false
if [[ -n "$CHANGED_FILES" ]] && grep -Eq '^(knowledge-base/qa/faq\.json|knowledge-base/indicator_catalog\.json|knowledge-base/raw/markdown/.+\.md|scripts/(rebuild_all|build_kb|build_chunks|build_bm25)\.py)$' <<<"$CHANGED_FILES"; then
  KB_REBUILD=true
fi
[[ "$FORCE_REBUILD" == true ]] && KB_REBUILD=true
[[ "$FORCE_REBUILD" == true && "$FULL_REBUILD" == true ]] && FULL_REBUILD=true

if [[ -x "$ROOT_DIR/$VENV_DIR/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/$VENV_DIR/bin/python"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi

INSTALL_DEPENDENCIES=false
[[ "$REQUIREMENTS_CHANGED" == true || "$FORCE_INSTALL" == true ]] && INSTALL_DEPENDENCIES=true

if [[ "$SKIP_RESTART" == true ]]; then
  NEED_RESTART=false
elif [[ "$FORCE_RESTART" == true ]]; then
  NEED_RESTART=true
elif [[ "$HAS_UPDATE" == true || "$INSTALL_DEPENDENCIES" == true || "$KB_REBUILD" == true ]]; then
  NEED_RESTART=true
fi

log "部署计划"
printf '分支:             %s\n' "$BRANCH"
printf '本地提交:         %s\n' "$LOCAL_HEAD"
printf '远端提交:         %s\n' "$REMOTE_HEAD"
printf '有新提交:         %s\n' "$HAS_UPDATE"
printf '安装依赖:         %s\n' "$([[ "$INSTALL_DEPENDENCIES" == true ]] && echo yes || echo no)"
printf '更新索引:         %s\n' "$([[ "$KB_REBUILD" == true ]] && { [[ "$FULL_REBUILD" == true ]] && echo "yes (full)" || echo "yes (incremental)"; } || echo no)"
printf '重启服务:         %s\n' "$([[ "$NEED_RESTART" == true ]] && echo yes || echo no)"

if [[ -n "$CHANGED_FILES" ]]; then
  printf '\n更新文件:\n'
  printf '%s\n' "$CHANGED_FILES"
fi

if [[ "$DRY_RUN" == true ]]; then
  log "dry-run 完成，未修改文件"
  exit 0
fi

if [[ "$HAS_UPDATE" == true ]]; then
  log "快进合并到 $REMOTE_HEAD"
  git merge --ff-only "$REMOTE_HEAD"
fi

if [[ "$INSTALL_DEPENDENCIES" == true ]]; then
  log "安装 Python 依赖"
  "$PYTHON_BIN" -m pip install -r backend/requirements.txt
fi

if [[ "$KB_REBUILD" == true ]]; then
  log "校验知识库"
  "$PYTHON_BIN" scripts/validate_faq.py

  if [[ "$FULL_REBUILD" == true ]]; then
    log "全量重建 Chroma + BM25"
    "$PYTHON_BIN" scripts/rebuild_all.py
  else
    log "增量重建 Chroma + BM25"
    "$PYTHON_BIN" scripts/rebuild_all.py --incremental
  fi
fi

if [[ "$NEED_RESTART" == true ]]; then
  if [[ -n "$RESTART_COMMAND" ]]; then
    log "执行自定义重启命令"
    bash -c "$RESTART_COMMAND"
  else
    UNIT="$SERVICE.service"
    if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files "$UNIT" --no-legend | grep -q .; then
      log "重启 systemd 服务 $UNIT"
      if [[ "$(id -u)" -eq 0 ]]; then
        systemctl restart "$UNIT"
      else
        sudo systemctl restart "$UNIT"
      fi
    else
      die "未找到 systemd 单元 $UNIT；请用 --restart-command 指定重启命令，或用 --skip-restart 跳过"
    fi
  fi

  log "等待健康检查 $HEALTH_URL"
  HEALTH_OK=false
  for _ in $(seq 1 30); do
    if RESPONSE="$(curl -fsS --max-time 3 "$HEALTH_URL" 2>/dev/null)"; then
      printf '%s\n' "$RESPONSE"
      HEALTH_OK=true
      break
    fi
    sleep 2
  done
  [[ "$HEALTH_OK" == true ]] || die "健康检查失败，请查看服务日志"
fi

log "部署完成：$(git rev-parse --short HEAD)"
