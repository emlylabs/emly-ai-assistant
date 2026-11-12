#!/bin/bash
set -e
# exec so start.sh (and, through its own exec, uvicorn) replaces this shell
# and receives SIGTERM/SIGINT directly for a clean shutdown.
exec ./start.sh
