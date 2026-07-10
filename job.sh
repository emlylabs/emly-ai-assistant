#!/bin/bash
set -e
./start.sh &
START_PID=$!
wait $START_PID
