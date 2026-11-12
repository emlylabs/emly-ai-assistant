#!/usr/bin/env bash

PORT="${PORT:-8080}"
HOST="${HOST:-0.0.0.0}"
# Only these proxy addresses may set X-Forwarded-For / X-Forwarded-Proto.
# Client IPs from them feed rate limits and the audit log, so trusting '*'
# would let any client spoof its IP. Comma-separated IPs or CIDRs.
FORWARDED_ALLOW_IPS="${FORWARDED_ALLOW_IPS:-127.0.0.1}"

# exec: uvicorn replaces the shell so container stop signals reach it.
exec uvicorn main:app --host $HOST --port $PORT --forwarded-allow-ips "$FORWARDED_ALLOW_IPS" --log-level info --access-log --log-config logging_config.json
