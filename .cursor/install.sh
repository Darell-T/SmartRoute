#!/usr/bin/env bash
# Idempotent Cloud Agent setup for SmartRoute.
#
# Prepares the Python backend (virtualenv + pinned requirements) and the
# Next.js frontend (locked npm dependencies). Safe to run repeatedly: an
# existing virtualenv is reused and dependency installs converge on the
# pinned lockfiles without rewriting them.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

# The default image ships Python 3.12 without the stdlib venv/ensurepip
# module, so `python3 -m venv` fails until this package is present. apt install
# is idempotent, so rerunning setup is a no-op once it is installed.
if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
  echo "[install] installing python3-venv"
  sudo apt-get update -qq
  sudo apt-get install -y --no-install-recommends python3-venv >/dev/null
fi

echo "[install] backend: virtualenv + requirements"
if [ ! -x backend/.venv/bin/python ]; then
  python3 -m venv backend/.venv
fi
backend/.venv/bin/python -m pip install --upgrade pip >/dev/null
backend/.venv/bin/python -m pip install \
  -r backend/requirements.txt \
  -r backend/requirements-dev.txt

echo "[install] frontend: npm ci"
(cd frontend && npm ci)

echo "[install] done"
