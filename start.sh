#!/usr/bin/env bash

PORT="${PORT:-8080}"
HOST="${HOST:-0.0.0.0}"

# exec: uvicorn replaces the shell so container stop signals reach it.
exec uvicorn main:app --host $HOST --port $PORT --forwarded-allow-ips '*' --log-level info --access-log --log-config logging_config.json
