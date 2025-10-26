#!/bin/bash
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
SCRIPT_DIR="/home/basil/memes"
VENV_DIR="$SCRIPT_DIR/venv"
LOG_FILE="$SCRIPT_DIR/logs/scan.log"

if [ -f "$SCRIPT_DIR/.env" ]; then
    export $(/bin/cat "$SCRIPT_DIR/.env" | /usr/bin/xargs)
fi

export TZ="Europe/Nicosia"

/bin/echo "================================" >> "$LOG_FILE"
/bin/echo "$(/bin/date): Starting error retry" >> "$LOG_FILE"
/bin/echo "================================" >> "$LOG_FILE"

cd "$SCRIPT_DIR"
source "$VENV_DIR/bin/activate"
python3 process_memes.py --retry-errors >> "$LOG_FILE" 2>&1

/bin/echo "$(/bin/date): Retry completed" >> "$LOG_FILE"
/bin/echo "" >> "$LOG_FILE"