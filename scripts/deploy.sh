#!/bin/bash
# deploy.sh - Deploy Shiva Sniper Bot to VPS (Ubuntu)
# Usage: bash scripts/deploy.sh user@your-vps-ip

set -e
REMOTE=$1
if [ -z "$REMOTE" ]; then
  echo "Usage: bash scripts/deploy.sh user@ip"
  exit 1
fi

echo "Deploying to $REMOTE..."
rsync -avz --exclude '__pycache__' --exclude '*.pyc' \
      --exclude 'phase*/data/*.csv' --exclude '.git' \
      ./ $REMOTE:/home/ubuntu/shiva_sniper_bot/

echo "Installing dependencies..."
ssh $REMOTE "cd /home/ubuntu/shiva_sniper_bot && \
  python3 -m venv venv && \
  venv/bin/pip install -r requirements.txt -q"

echo "Installing systemd service..."
ssh $REMOTE "sudo cp /home/ubuntu/shiva_sniper_bot/scripts/shiva_sniper.service \
  /etc/systemd/system/ && \
  sudo systemctl daemon-reload && \
  sudo systemctl enable shiva_sniper"

echo "Done. Start with: ssh $REMOTE 'sudo systemctl start shiva_sniper'"
