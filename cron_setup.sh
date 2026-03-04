#!/usr/bin/env bash
PROJECT_DIR="/Users/liam10play/Desktop/אישי/Coding Projects/sec-filing-monitor"
SCRIPT="$PROJECT_DIR/run_monitor.sh"
MONITOR_LOG="$PROJECT_DIR/monitor.log"
CRON_LINE="*/15 * * * 1-5 $SCRIPT >> $MONITOR_LOG 2>&1"

case "$1" in
    on)
        chmod +x "$SCRIPT"
        if crontab -l 2>/dev/null | grep -qF "$SCRIPT"; then
            echo "SEC Monitor is already active."
        else
            (crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
            echo "SEC Monitor Activated!"
        fi
        ;;
    off)
        if crontab -l 2>/dev/null | grep -qF "$SCRIPT"; then
            crontab -l 2>/dev/null | grep -vF "$SCRIPT" | crontab -
            echo "SEC Monitor Deactivated."
        else
            echo "SEC Monitor is already inactive."
        fi
        ;;
    status)
        if crontab -l 2>/dev/null | grep -qF "$SCRIPT"; then
            echo "SEC Monitor: ACTIVE (polling every 15 min, Mon-Fri ET market hours)"
        else
            echo "SEC Monitor: INACTIVE"
        fi

        # Last fetch info from monitor.log
        echo ""
        if [ -f "$MONITOR_LOG" ]; then
            LAST_RUN=$(grep "═══ SEC Filing Monitor ═══" "$MONITOR_LOG" | tail -1 | awk '{print $1, $2}')
            LAST_RESULT=$(grep -E "(Done!|No interesting filings found)" "$MONITOR_LOG" | tail -1 | sed 's/.*\] sec_monitor: //')
            if [ -n "$LAST_RUN" ]; then
                echo "Last fetch:  $LAST_RUN"
                echo "Result:      ${LAST_RESULT:-unknown}"
            else
                echo "Last fetch:  No runs recorded yet"
            fi
        else
            echo "Last fetch:  No runs recorded yet (monitor.log not found)"
        fi

        # Next fetch: next 15-min mark in ET that falls within market hours (9:25–16:05)
        echo ""
        ET_NOW=$(TZ="America/New_York" date "+%Y-%m-%d %H:%M %A")
        ET_H=$(TZ="America/New_York" date "+%H")
        ET_M=$(TZ="America/New_York" date "+%M")
        ET_MINS=$((10#$ET_H * 60 + 10#$ET_M))
        DAY=$(TZ="America/New_York" date "+%u")  # 1=Mon, 7=Sun

        # Next 15-min slot
        NEXT_MINS=$(( (ET_MINS / 15 + 1) * 15 ))

        if [ "$DAY" -ge 6 ] || [ "$NEXT_MINS" -gt 965 ]; then
            echo "Next fetch:  Monday at 9:30 AM ET (next market open)"
        elif [ "$NEXT_MINS" -lt 565 ]; then
            echo "Next fetch:  Today at 9:30 AM ET (market not yet open)"
        else
            NEXT_H=$(( NEXT_MINS / 60 ))
            NEXT_M=$(( NEXT_MINS % 60 ))
            printf "Next fetch:  Today at %02d:%02d ET\n" "$NEXT_H" "$NEXT_M"
        fi
        echo "(Current ET time: $ET_NOW)"
        ;;
    *)
        echo "Usage: $0 on|off|status"
        exit 1
        ;;
esac
