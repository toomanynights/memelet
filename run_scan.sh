#!/bin/bash

# Set PATH to include standard locations
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# Configuration
SCRIPT_DIR="/home/basil/memes"
VENV_DIR="$SCRIPT_DIR/venv"
LOCK_FILE="$SCRIPT_DIR/scan.lock"
LOG_FILE="$SCRIPT_DIR/logs/scan.log"

# Load environment variables
if [ -f "$SCRIPT_DIR/.env" ]; then
    export $(/bin/cat "$SCRIPT_DIR/.env" | /usr/bin/xargs)
fi

# Set timezone
export TZ="Europe/Nicosia"

# Create logs directory if it doesn't exist
/bin/mkdir -p "$SCRIPT_DIR/logs"

# Check if already running
if [ -f "$LOCK_FILE" ]; then
    # Check if the process is actually running
    if /bin/ps -p $(/bin/cat "$LOCK_FILE") > /dev/null 2>&1; then
        /bin/echo "$(/bin/date): Scan already running (PID: $(/bin/cat $LOCK_FILE)). Skipping." >> "$LOG_FILE"
        exit 0
    else
        # Stale lock file, remove it
        /bin/rm "$LOCK_FILE"
    fi
fi

# Create lock file with current PID
/bin/echo $$ > "$LOCK_FILE"

# Log start
/bin/echo "================================" >> "$LOG_FILE"
/bin/echo "$(/bin/date): Starting scan and process" >> "$LOG_FILE"
/bin/echo "================================" >> "$LOG_FILE"

# Activate virtual environment and run script
cd "$SCRIPT_DIR"
source "$VENV_DIR/bin/activate"
python3 process_memes.py --scan --process >> "$LOG_FILE" 2>&1

# Log completion
/bin/echo "$(/bin/date): Scan and process completed" >> "$LOG_FILE"
/bin/echo "" >> "$LOG_FILE"

# Remove lock file
/bin/rm "$LOCK_FILE"