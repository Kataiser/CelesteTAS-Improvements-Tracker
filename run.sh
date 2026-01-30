#!/bin/bash
cd "$(dirname "$0")"
while true; do
    source .venv/bin/activate
    uv run bot.py
    exit_code=$?
    if [ $exit_code -ne 666 ]; then
        break
    fi
    git pull
done
read -p "Press enter to continue..."
