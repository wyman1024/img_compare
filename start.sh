#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$APP_DIR/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "$APP_DIR"

if [[ -f "$APP_DIR/config.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$APP_DIR/config.env"
    set +a
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "未找到 $PYTHON_BIN，请先安装 Python 3.10 或更高版本。" >&2
    exit 1
fi

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    echo "正在创建 Python 虚拟环境……"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

if ! "$VENV_DIR/bin/python" - <<'PY' >/dev/null 2>&1
import sys
from importlib.metadata import version


def major_minor(package: str) -> tuple[int, int]:
    return tuple(int(part) for part in version(package).split(".")[:2])


valid = (
    sys.version_info >= (3, 10)
    and (3, 1) <= major_minor("Flask") < (4, 0)
    and (11, 0) <= major_minor("Pillow") < (13, 0)
    and (3, 0) <= major_minor("waitress") < (4, 0)
    and (1, 7) <= major_minor("lark-oapi") < (2, 0)
)
raise SystemExit(0 if valid else 1)
PY
then
    echo "正在安装运行依赖……"
    "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check -r requirements.txt
fi

if [[ "${1:-}" == "--prepare" ]]; then
    echo "运行环境已准备完成。"
    exit 0
fi

export OPEN_BROWSER="${OPEN_BROWSER:-0}"
export PYTHONDONTWRITEBYTECODE="${PYTHONDONTWRITEBYTECODE:-1}"
export PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"

exec "$VENV_DIR/bin/python" server.py
