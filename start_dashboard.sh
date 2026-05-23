#!/usr/bin/env bash
# Start the Whale Mirror dashboard server.
# Usage: ./start_dashboard.sh [--port 8000] [--mode paper|live]

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PORT="${DASHBOARD_PORT:-8000}"
MODE="${1:-paper}"

echo "╔══════════════════════════════════════════╗"
echo "║   Whale Mirror — Dashboard Server        ║"
echo "║   http://localhost:${PORT}                  ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "  Mode    : $MODE"
echo "  Port    : $PORT"
echo "  Ctrl+C  to stop"
echo ""

# Create config from examples if not present
if [ ! -f "config/settings.yaml" ] && [ -f "config/settings.example.yaml" ]; then
  cp config/settings.example.yaml config/settings.yaml
  echo "  Created config/settings.yaml from example."
fi
if [ ! -f "config/wallets.yaml" ] && [ -f "config/wallets.example.yaml" ]; then
  cp config/wallets.example.yaml config/wallets.yaml
  echo "  Created config/wallets.yaml from example."
fi

# Patch mode into settings.yaml if arg given
if [ "$MODE" = "live" ]; then
  sed -i.bak 's/^mode:.*/mode: live/' config/settings.yaml
else
  sed -i.bak 's/^mode:.*/mode: paper/' config/settings.yaml
fi

DASHBOARD_PORT=$PORT python server.py
