#!/usr/bin/env bash
PROJECT_DIR="/Users/liam10play/Desktop/אישי/Coding Projects/sec-filing-monitor"
SCRIPT="$PROJECT_DIR/run_monitor.sh"
MONITOR_LOG="$PROJECT_DIR/monitor.log"
CRON_LINE="*/15 * * * 1-5 $SCRIPT >> $MONITOR_LOG 2>&1"

case "$1" in
    on)
        chmod +x "$SCRIPT"
        # Add only if not already present
        if crontab -l 2>/dev/null | grep -qF "$SCRIPT"; then
            echo "Cron entry already exists."
        else
            (crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
            echo "Cron entry added."
        fi
        echo ""
        echo "Current crontab:"
        crontab -l
        ;;
    off)
        crontab -l 2>/dev/null | grep -vF "$SCRIPT" | crontab -
        echo "Cron entry removed."
        echo ""
        echo "Current crontab:"
        crontab -l 2>/dev/null || echo "(empty)"
        ;;
    *)
        echo "Usage: $0 on|off"
        exit 1
        ;;
esac
