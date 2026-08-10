#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
RUN_DIR="$APP_DIR/.run"
LOG_DIR="$APP_DIR/logs"
PID_FILE="$RUN_DIR/server.pid"
LOCK_FILE="$RUN_DIR/server.lock"
MANAGE_LOCK_FILE="$RUN_DIR/manage.lock"
LOG_FILE="$LOG_DIR/server.log"

mkdir -p "$RUN_DIR" "$LOG_DIR"
cd "$APP_DIR"
exec 8>"$MANAGE_LOCK_FILE"
flock --exclusive 8

read_pid() {
    [[ -f "$PID_FILE" ]] && tr -cd '0-9' < "$PID_FILE"
}

is_our_process() {
    local pid="${1:-}"
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    [[ "$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)" == "$APP_DIR" ]]
}

health_url() {
    local port="8000"
    if [[ -f "$APP_DIR/config.env" ]]; then
        port="$(sed -n 's/^[[:space:]]*PORT=//p' "$APP_DIR/config.env" | tail -n 1)"
        port="${port%\"}"
        port="${port#\"}"
        port="${port:-8000}"
    fi
    printf 'http://127.0.0.1:%s/api/health' "$port"
}

start_service() {
    local pid
    pid="$(read_pid || true)"
    if is_our_process "$pid"; then
        echo "服务已在运行（PID $pid）。"
        return 0
    fi
    rm -f "$PID_FILE"

    if [[ -f "$LOG_FILE" ]] && (( $(stat -c '%s' "$LOG_FILE") > 10485760 )); then
        mv -f "$LOG_FILE" "$LOG_FILE.1"
    fi

    nohup flock --exclusive --nonblock --no-fork "$LOCK_FILE" \
        bash "$APP_DIR/start.sh" >>"$LOG_FILE" 2>&1 </dev/null 8>&- &
    pid=$!
    printf '%s\n' "$pid" > "$PID_FILE"

    for _ in {1..40}; do
        if ! is_our_process "$pid"; then
            echo "服务启动失败，最近日志如下：" >&2
            tail -n 30 "$LOG_FILE" >&2 || true
            rm -f "$PID_FILE"
            return 1
        fi
        if curl --fail --silent --show-error "$(health_url)" >/dev/null 2>&1; then
            echo "服务已启动（PID $pid，$(health_url)）。"
            return 0
        fi
        sleep 0.5
    done

    echo "服务未在预期时间内通过健康检查，最近日志如下：" >&2
    tail -n 30 "$LOG_FILE" >&2 || true
    return 1
}

stop_service() {
    local pid
    pid="$(read_pid || true)"
    if ! is_our_process "$pid"; then
        echo "服务未运行。"
        rm -f "$PID_FILE"
        return 0
    fi

    kill -TERM "$pid"
    for _ in {1..40}; do
        if ! kill -0 "$pid" 2>/dev/null; then
            rm -f "$PID_FILE"
            echo "服务已停止。"
            return 0
        fi
        sleep 0.5
    done

    echo "服务未及时退出，正在终止 PID $pid。" >&2
    kill -KILL "$pid"
    rm -f "$PID_FILE"
}

status_service() {
    local pid
    pid="$(read_pid || true)"
    if ! is_our_process "$pid"; then
        echo "服务未运行。"
        return 1
    fi
    if curl --fail --silent --show-error "$(health_url)"; then
        printf '\n服务运行正常（PID %s）。\n' "$pid"
        return 0
    fi
    echo "服务进程存在，但健康检查失败（PID $pid）。" >&2
    return 1
}

case "${1:-}" in
    start)
        start_service
        ;;
    stop)
        stop_service
        ;;
    restart)
        stop_service
        start_service
        ;;
    status)
        status_service
        ;;
    *)
        echo "用法：$0 {start|stop|restart|status}" >&2
        exit 2
        ;;
esac
