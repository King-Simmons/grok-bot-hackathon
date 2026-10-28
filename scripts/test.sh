#!/usr/bin/env bash
# The one test command for this repo. Agents, the verification skill, and CI
# call this file. When the test runner changes, edit only the exec line below.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=python3
if [[ -x .venv/bin/python ]]; then
  PY=.venv/bin/python
fi
exec "$PY" -m unittest discover -s tests -v
