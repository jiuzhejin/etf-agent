#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
PYTHON_BIN="${PYTHON:-python3}"

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "未找到 Python 解释器: ${PYTHON_BIN}"
  echo "可通过 PYTHON=/path/to/python3 ./init.sh 指定解释器"
  exit 1
fi

echo "==> 使用 Python: ${PYTHON_BIN}"

if [[ ! -d "${VENV_DIR}" ]]; then
  echo "==> 创建虚拟环境 ${VENV_DIR}"
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

echo "==> 升级 pip"
"${VENV_DIR}/bin/python" -m pip install --upgrade pip

echo "==> 安装依赖 requirements.txt"
"${VENV_DIR}/bin/pip" install -r "${ROOT_DIR}/requirements.txt"

if [[ ! -f "${ROOT_DIR}/.env" && -f "${ROOT_DIR}/.env.example" ]]; then
  echo "==> 生成 .env（来自 .env.example）"
  cp "${ROOT_DIR}/.env.example" "${ROOT_DIR}/.env"
  echo "请检查 ${ROOT_DIR}/.env 里的 API Key / 模型配置"
fi

echo "==> 初始化完成"
echo "启动命令: ${ROOT_DIR}/run.sh"
