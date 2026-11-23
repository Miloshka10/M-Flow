#!/usr/bin/env bash
set -eu

export M_FLOW_CLOUD_DEMO=1
export M_FLOW_AI_PROVIDER=groq
export M_FLOW_DATABASE_BACKEND=sqlite
export M_FLOW_DATABASE=/tmp/mflow-cloud-demo.db
export M_FLOW_SECRET_KEY="${M_FLOW_SECRET_KEY:-$(python -c 'import secrets; print(secrets.token_urlsafe(48))')}"

if [ -n "${M_FLOW_CLOUD_DEMO_PASSWORD:-}" ]; then
  python seed_cloud_demo.py
fi

exec gunicorn --workers 1 --threads 4 --timeout 60 --bind "0.0.0.0:${PORT:-10000}" app:app
