#!/usr/bin/env bash

PORT="${PORT:-8080}"
HOST="${HOST:-0.0.0.0}"

uvicorn main:app --host $HOST --port $PORT --forwarded-allow-ips '*' --log-level info --access-log --log-config logging_config.json
