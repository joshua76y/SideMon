#!/bin/bash
while true; do
    python3 -u /Volumes/MACdata/Docs/Documents/SideMon/mac/sidemon.py --host 192.168.1.24 --port 9877 -i 2 >> /tmp/sidemon-sender.log 2>&1
    echo "[$(date)] Sender died, restarting in 3s..." >> /tmp/sidemon-sender.log
    sleep 3
done
