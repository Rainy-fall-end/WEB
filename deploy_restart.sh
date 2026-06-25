#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-/root/WEB}"
VENV_DIR="${VENV_DIR:-$PROJECT_DIR/.venv}"
SERVICE_NAME="${SERVICE_NAME:-search-console}"
PYTHON_BIN="${VENV_DIR}/bin/python"

log() {
  printf '\n[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

if [ ! -d "$PROJECT_DIR" ]; then
  echo "Project directory not found: $PROJECT_DIR" >&2
  exit 1
fi

cd "$PROJECT_DIR"

log "Project: $PROJECT_DIR"
log "Service: $SERVICE_NAME"

if [ -d ".git" ]; then
  log "Pulling latest code"
  git pull
else
  log "Skipping git pull because $PROJECT_DIR is not a git repository"
fi

if [ ! -x "$PYTHON_BIN" ]; then
  log "Creating virtual environment: $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

log "Installing dependencies"
"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install -r requirements.txt

log "Running database migrations"
"$PYTHON_BIN" manage.py migrate

log "Collecting static files"
"$PYTHON_BIN" manage.py collectstatic --noinput

log "Restarting service"
sudo systemctl restart "$SERVICE_NAME"

log "Service status"
sudo systemctl status "$SERVICE_NAME" --no-pager

