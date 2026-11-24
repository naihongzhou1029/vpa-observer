#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
RUN_DIR="${ROOT_DIR}/.run"
PID_FILE="${RUN_DIR}/vpa-view.pid"
LOG_FILE="${RUN_DIR}/vpa-view.log"
HOST="${VPA_HOST:-127.0.0.1}"
PORT="${VPA_PORT:-8050}"

usage() {
  cat <<EOF
VPA-View DevOps 控制中心

用法:
  ./devops.sh setup               建立 Python 虛擬環境、安裝依賴套件並初始化 SQLite
  ./devops.sh start [--daemon]    啟動後端服務 (預設前景；加上 --daemon 背景常駐)
  ./devops.sh stop                停止背景服務
  ./devops.sh restart             重啟背景服務
  ./devops.sh redeploy            重新部署：重啟背景後端服務並以預設瀏覽器開啟前端頁面
  ./devops.sh status              查看服務運行狀態與資料庫摘要
  ./devops.sh logs                追蹤背景服務日誌
  ./devops.sh sync [SYMBOL]       同步全部觀察清單或指定標的的所有週期資料
EOF
}

ensure_venv() {
  if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    echo "找不到虛擬環境，請先執行: ./devops.sh setup"
    exit 1
  fi
}

cmd_setup() {
  cd "${ROOT_DIR}"
  mkdir -p "${ROOT_DIR}/data" "${RUN_DIR}"

  if command -v uv >/dev/null 2>&1; then
    echo "[1/3] 使用 uv 建立虛擬環境..."
    # Prefer Python 3.12 if system Python is 3.15+ to ensure prebuilt wheels for pandas/numpy
    local py_ver
    py_ver="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "3.12")"
    if [[ "${py_ver}" == "3.15" || "${py_ver}" == "3.14" ]]; then
      uv venv --python 3.12 "${VENV_DIR}" --allow-existing
    else
      uv venv "${VENV_DIR}" --allow-existing
    fi
    echo "[2/3] 安裝 Python 依賴套件..."
    uv pip install --python "${VENV_DIR}/bin/python" -r "${ROOT_DIR}/requirements.txt"
  else
    echo "[1/3] 使用 python3 -m venv 建立虛擬環境..."
    python3 -m venv "${VENV_DIR}"
    echo "[2/3] 安裝 Python 依賴套件..."
    "${VENV_DIR}/bin/pip" install --upgrade pip
    "${VENV_DIR}/bin/pip" install -r "${ROOT_DIR}/requirements.txt"
  fi

  echo "[3/3] 初始化 SQLite 資料庫與預設觀察清單..."
  "${VENV_DIR}/bin/python" -c "from backend import db; db.init_db(); print('DB ready:', db.DB_PATH)"
  echo "環境建置完成。"
}

is_running() {
  if [[ -f "${PID_FILE}" ]]; then
    local pid
    pid="$(cat "${PID_FILE}")"
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      return 0
    fi
  fi
  return 1
}

cmd_start() {
  ensure_venv
  mkdir -p "${RUN_DIR}"
  local daemon_mode="false"
  if [[ "${1:-}" == "--daemon" ]]; then
    daemon_mode="true"
  fi

  if is_running; then
    echo "VPA-View 已在運行中 (PID: $(cat "${PID_FILE}"), URL: http://${HOST}:${PORT})"
    return 0
  fi

  cd "${ROOT_DIR}"
  if [[ "${daemon_mode}" == "true" ]]; then
    nohup "${VENV_DIR}/bin/uvicorn" backend.main:app --host "${HOST}" --port "${PORT}" > "${LOG_FILE}" 2>&1 &
    local pid=$!
    echo "${pid}" > "${PID_FILE}"
    sleep 2
    if kill -0 "${pid}" 2>/dev/null; then
      echo "VPA-View 已於背景啟動 (PID: ${pid}) -> http://${HOST}:${PORT}"
    else
      echo "啟動失敗，請查看日誌: ${LOG_FILE}"
      tail -n 30 "${LOG_FILE}" || true
      exit 1
    fi
  else
    echo "VPA-View 啟動中 -> http://${HOST}:${PORT}"
    exec "${VENV_DIR}/bin/uvicorn" backend.main:app --host "${HOST}" --port "${PORT}"
  fi
}

cmd_stop() {
  if ! is_running; then
    rm -f "${PID_FILE}"
    echo "VPA-View 目前未運行。"
    return 0
  fi

  local pid
  pid="$(cat "${PID_FILE}")"
  echo "停止 VPA-View (PID: ${pid})..."
  kill "${pid}" 2>/dev/null || true
  for _ in {1..20}; do
    if ! kill -0 "${pid}" 2>/dev/null; then
      break
    fi
    sleep 0.25
  done
  if kill -0 "${pid}" 2>/dev/null; then
    kill -9 "${pid}" 2>/dev/null || true
  fi
  rm -f "${PID_FILE}"
  echo "已停止。"
}

cmd_restart() {
  cmd_stop
  cmd_start --daemon
}

open_browser() {
  local url="http://${HOST}:${PORT}"
  if command -v open >/dev/null 2>&1; then
    open "${url}"
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "${url}" >/dev/null 2>&1 &
  fi
  echo "已於預設瀏覽器開啟 -> ${url}"
}

cmd_redeploy() {
  cmd_restart
  open_browser
}

cmd_status() {
  if is_running; then
    echo "狀態: 運行中 (PID: $(cat "${PID_FILE}"), URL: http://${HOST}:${PORT})"
    if command -v curl >/dev/null 2>&1; then
      curl -fsS "http://${HOST}:${PORT}/api/status" 2>/dev/null || true
      echo ""
    fi
  else
    echo "狀態: 未運行"
    if [[ -x "${VENV_DIR}/bin/python" ]]; then
      cd "${ROOT_DIR}"
      "${VENV_DIR}/bin/python" -c "from backend import db; db.init_db(); print('DB:', db.get_db_stats())"
    fi
  fi
}

cmd_logs() {
  if [[ ! -f "${LOG_FILE}" ]]; then
    echo "尚無日誌檔案: ${LOG_FILE}"
    exit 0
  fi
  tail -f -n 80 "${LOG_FILE}"
}

cmd_sync() {
  ensure_venv
  cd "${ROOT_DIR}"
  local target="${1:-}"
  "${VENV_DIR}/bin/python" - "${target}" <<'PY'
import json
import sys
from backend import db, fetcher, scheduler

db.init_db()
raw_target = sys.argv[1].strip() if len(sys.argv) > 1 else ""
if raw_target:
    canonical, market = fetcher.normalize_symbol(raw_target)
    if not db.get_watchlist_item(canonical):
        db.upsert_watchlist_item(canonical, market)
    res = scheduler.sync_symbols(symbols=[canonical], full=False)
else:
    res = scheduler.sync_symbols(full=False)

print(json.dumps(res, ensure_ascii=False, indent=2))
PY
}

main() {
  local cmd="${1:-}"
  shift || true
  case "${cmd}" in
    setup)   cmd_setup "$@" ;;
    start)   cmd_start "$@" ;;
    stop)    cmd_stop "$@" ;;
    restart)         cmd_restart "$@" ;;
    redeploy|deploy) cmd_redeploy "$@" ;;
    status)          cmd_status "$@" ;;
    logs)    cmd_logs "$@" ;;
    sync)    cmd_sync "$@" ;;
    *)       usage; exit 1 ;;
  esac
}

main "$@"
