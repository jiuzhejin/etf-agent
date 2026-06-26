#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="${ROOT_DIR}/.venv/bin/python"

if [[ ! -x "${VENV_PYTHON}" ]]; then
  echo "未检测到 ${ROOT_DIR}/.venv，先执行初始化..."
  "${ROOT_DIR}/init.sh"
fi

exec "${VENV_PYTHON}" "${ROOT_DIR}/main.py" "$@"
