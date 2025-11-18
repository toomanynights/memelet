#!/bin/bash
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# Get script directory (works regardless of where it's installed)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load environment variables from .env
if [ -f "$SCRIPT_DIR/.env" ]; then
    export $(cat "$SCRIPT_DIR/.env" | grep -v '^#' | xargs)
fi

# Use environment variables with defaults
VENV_DIR="${VENV_DIR:-$SCRIPT_DIR/venv}"
LOG_DIR="${LOG_DIR:-$SCRIPT_DIR/logs}"
LOG_FILE="$LOG_DIR/scan.log"
TZ="${TZ:-UTC}"

# Export timezone
export TZ

# Create logs directory if it doesn't exist
mkdir -p "$LOG_DIR"

echo "================================" >> "$LOG_FILE"
echo "$(date): Starting error retry" >> "$LOG_FILE"
echo "================================" >> "$LOG_FILE"

cd "$SCRIPT_DIR"
source "$VENV_DIR/bin/activate"
python3 process_memes.py --retry-errors >> "$LOG_FILE" 2>&1

echo "$(date): Retry completed" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"