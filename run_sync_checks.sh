#!/bin/bash
cd "$(dirname "$0")"
source .venv/bin/activate
while true; do
    python game_sync.py
    sleep 1800
done
read -p "Press enter to continue..."
