#!/usr/bin/env bash
PROJECT_DIR="/Users/liam10play/Dev/sec-filing-monitor"
VENV_PYTHON="$PROJECT_DIR/venv/bin/python"
ERRORS_LOG="$PROJECT_DIR/errors.log"

# Market hours guard (ET): 9:25 AM (565 min) to 4:05 PM (965 min)
ET_MINUTES=$(TZ="America/New_York" date +"%H%M" | awk '{h=substr($0,1,2); m=substr($0,3,2); print h*60+m}')
if [ "$ET_MINUTES" -lt 565 ] || [ "$ET_MINUTES" -gt 965 ]; then
    exit 0
fi

cd "$PROJECT_DIR" || exit 1

# Verify we can access the venv before attempting to run Python
if ! test -r "$VENV_PYTHON"; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') [ERROR] sec_monitor: Cannot access venv — check Full Disk Access in System Settings (Privacy & Security)" >&2
    exit 1
fi

# Detect end-of-day: trigger from 3:45 PM ET onwards (945 min)
# Wide window handles launchd timing drift — reset_today_results prevents duplicate sends
EOD_FLAG=""
if [ "$ET_MINUTES" -ge 945 ]; then
    EOD_FLAG="--end-of-day"
fi

# Run monitor: capture stderr to temp file so we can inspect it,
# then forward it to stdout (→ monitor.log via launchd StandardOutPath)
STDERR_FILE=$(mktemp)
"$VENV_PYTHON" "$PROJECT_DIR/sec_monitor.py" $EOD_FLAG 2>"$STDERR_FILE"
EXIT_CODE=$?

cat "$STDERR_FILE"  # forward Python log output to stdout → monitor.log

if [ $EXIT_CODE -ne 0 ]; then
    TIMESTAMP=$(date "+%Y-%m-%d %H:%M:%S")
    ERROR_MSG=$(head -c 200 "$STDERR_FILE" | tr '\n' ' ')
    echo "$TIMESTAMP [ERROR] Exit code: $EXIT_CODE | $ERROR_MSG" >> "$ERRORS_LOG"
fi

rm -f "$STDERR_FILE"
