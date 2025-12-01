#!/bin/bash

# Set PATH to include standard locations
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# Get script directory (works regardless of where it's installed)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Ensure instance directory (passed from app.py as INSTANCE_DIR) and current directory are in PYTHONPATH
# INSTANCE_DIR is required for sitecustomize.py to load in multi-tenant mode
if [ -n "$INSTANCE_DIR" ]; then
    export PYTHONPATH="$INSTANCE_DIR:$SCRIPT_DIR:$PYTHONPATH"
else
    export PYTHONPATH="$SCRIPT_DIR:$PYTHONPATH"
fi

# Load environment variables from .env
if [ -f "$SCRIPT_DIR/.env" ]; then
    export $(cat "$SCRIPT_DIR/.env" | grep -v '^#' | xargs)
fi

# Use environment variables with defaults
VENV_DIR="${VENV_DIR:-$SCRIPT_DIR/venv}"
LOCK_FILE="${LOCK_FILE:-$SCRIPT_DIR/scan.lock}"
LOG_DIR="${LOG_DIR:-$SCRIPT_DIR/logs}"
LOG_FILE="$LOG_DIR/scan.log"
TZ="${TZ:-UTC}"

# Export timezone
export TZ

# Create logs directory if it doesn't exist
mkdir -p "$LOG_DIR"

# Check if already running
if [ -f "$LOCK_FILE" ]; then
    # Check if the process is actually running
    if ps -p $(cat "$LOCK_FILE") > /dev/null 2>&1; then
        echo "$(date): Scan already running (PID: $(cat $LOCK_FILE)). Skipping." >> "$LOG_FILE"
        exit 0
    else
        # Stale lock file, remove it
        rm "$LOCK_FILE"
    fi
fi

# Create lock file with current PID
echo $$ > "$LOCK_FILE"

# Log start
echo "================================" >> "$LOG_FILE"
echo "$(date): Starting scan and process" >> "$LOG_FILE"
echo "================================" >> "$LOG_FILE"

# Activate virtual environment and run script
cd "$SCRIPT_DIR"
source "$VENV_DIR/bin/activate"
python3 process_memes.py --scan --process >> "$LOG_FILE" 2>&1

# Log completion
echo "$(date): Scan and process completed" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"

# Remove lock file
rm "$LOCK_FILE"
