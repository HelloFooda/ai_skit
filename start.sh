#!/usr/bin/env bash
# 一键启动 ai-skit 前后端；按 Ctrl-C 两个服务一起停。
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
FRONTEND_URL="http://127.0.0.1:5174"
BACKEND_URL="http://127.0.0.1:8001"

BACKEND_PID=""
FRONTEND_PID=""
STOPPING=0

log() { printf '%s\n' "$*"; }

port_in_use() {
  command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

cleanup() {
  STOPPING=1
  trap - INT TERM EXIT
  log ""
  log "正在停止前后端服务…"
  if [ -n "$BACKEND_PID" ]; then
    kill "$BACKEND_PID" 2>/dev/null || true
  fi
  if [ -n "$FRONTEND_PID" ]; then
    kill "$FRONTEND_PID" 2>/dev/null || true
  fi
  if [ -n "$BACKEND_PID" ]; then
    wait "$BACKEND_PID" 2>/dev/null || true
  fi
  if [ -n "$FRONTEND_PID" ]; then
    wait "$FRONTEND_PID" 2>/dev/null || true
  fi
  log "已停止。"
}
trap cleanup INT TERM EXIT

log "=== ai-skit 一键启动 ==="

if [ ! -x "$BACKEND/.venv/bin/uvicorn" ]; then
  log "后端虚拟环境缺失：$BACKEND/.venv"
  log "请先执行：cd backend && uv sync"
  exit 1
fi
if [ ! -d "$FRONTEND/node_modules" ]; then
  log "前端依赖缺失：$FRONTEND/node_modules"
  log "请先执行：cd frontend && npm install"
  exit 1
fi
for port in 8001 5174; do
  if port_in_use "$port"; then
    log "端口 $port 已被占用，请先关闭占用它的进程再启动。"
    exit 1
  fi
done

log "启动后端 → $BACKEND_URL"
(
  cd "$BACKEND" || exit 1
  # --reload：改代码后自动重载。少了它，改完后端会出现「前端已是新代码、
  # 后端还是旧进程」，表现为新接口一律 404（2026-11-22 踩过）。
  exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
) &
BACKEND_PID=$!

log "启动前端 → $FRONTEND_URL"
(
  cd "$FRONTEND" || exit 1
  exec npm run dev -- --host 127.0.0.1
) &
FRONTEND_PID=$!

log ""
log "两个服务已启动，按 Ctrl-C 同时停止。"
log "  前端：$FRONTEND_URL"
log "  后端：${BACKEND_URL}（接口文档 ${BACKEND_URL}/docs）"
log ""

# 任一服务退出就收掉另一个，避免留下半套环境。
while kill -0 "$BACKEND_PID" 2>/dev/null && kill -0 "$FRONTEND_PID" 2>/dev/null; do
  sleep 1
done

if [ "$STOPPING" -eq 0 ]; then
  log "检测到某个服务退出，准备清理…"
fi
