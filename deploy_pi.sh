#!/bin/bash
# SideMon Pi deploy script - run this on your Mac terminal
set -e
PI_IP="192.168.1.37"
PI_USER="pi"
PI_PASS="qwe123"
echo "=== Deploying SideMon to Pi at $PI_IP ==="
export SSHPASS="$PI_PASS"

echo "[1/4] Testing connection..."
sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 ${PI_USER}@${PI_IP} "echo OK"

echo "[2/4] Stopping service..."
sshpass -e ssh -o StrictHostKeyChecking=no ${PI_USER}@${PI_IP} "sudo systemctl stop sidemon-pil"

echo "[3/4] Uploading new code..."
sshpass -e scp -o StrictHostKeyChecking=no pirecv/sidemon-pil.py ${PI_USER}@${PI_IP}:/home/pi/sidemon-pil.py

echo "[4/4] Starting service..."
sshpass -e ssh -o StrictHostKeyChecking=no ${PI_USER}@${PI_IP} "sudo systemctl start sidemon-pil"

echo ""
echo "=== Deploy done! Checking logs ==="
sleep 2
sshpass -e ssh -o StrictHostKeyChecking=no ${PI_USER}@${PI_IP} "sudo journalctl -u sidemon-pil --no-pager -n 10"

echo ""
echo "=== Pi should now display SideMon pages ==="
