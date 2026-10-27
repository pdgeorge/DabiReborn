#!/usr/bin/env bash
# Shortcut for ptt.py with this folder's venv: `./ptt.sh` starts the client,
# `./ptt.sh toggle|start|stop|cancel` pokes the running one.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$DIR/venv/bin/python" "$DIR/ptt.py" "${@:-run}"
