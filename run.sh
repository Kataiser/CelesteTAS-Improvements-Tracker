#!/bin/bash
cd "$(dirname "$0")"
while true; do
    source .venv/bin/activate
    python bot.py
    exit_code=$?
    if [ $exit_code -ne 111 ]; then
        break
    fi
    git pull
done
read -p "Press enter to continue..."
