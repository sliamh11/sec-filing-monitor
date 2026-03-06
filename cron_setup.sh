#!/usr/bin/env bash
PROJECT_DIR="/Users/liam10play/Dev/sec-filing-monitor"
SCRIPT="$PROJECT_DIR/run_monitor.sh"
MONITOR_LOG="$PROJECT_DIR/monitor.log"
PLIST_LABEL="com.sec-monitor"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"

case "$1" in
    on)
        chmod +x "$SCRIPT"

        # Remove legacy crontab entry if present
        crontab -l 2>/dev/null | grep -vF "$SCRIPT" | crontab - 2>/dev/null

        # Write launchd plist
        cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PLIST_LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$SCRIPT</string>
    </array>
    <key>StartInterval</key>
    <integer>900</integer>
    <key>StandardOutPath</key>
    <string>$MONITOR_LOG</string>
    <key>StandardErrorPath</key>
    <string>$MONITOR_LOG</string>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
EOF

        # Pre-flight: verify bash can access the project directory
        if ! /bin/bash -c "test -r '$PROJECT_DIR/run_monitor.sh'" 2>/dev/null; then
            echo ""
            echo "⚠️  WARNING: macOS is blocking /bin/bash from accessing this directory."
            echo "   Scheduled runs will fail silently with 'Operation not permitted'."
            echo ""
            echo "   Fix (one-time):"
            echo "   1. Open System Settings → Privacy & Security → Full Disk Access"
            echo "   2. Click '+', press Cmd+Shift+G, type /bin/bash, click Open"
            echo "   3. Enable the toggle for /bin/bash"
            echo "   4. Run: ./cron_setup.sh on"
            echo ""
        fi

        launchctl unload "$PLIST_PATH" 2>/dev/null
        launchctl load "$PLIST_PATH"
        echo "SEC Monitor Activated!"
        ;;

    off)
        if [ -f "$PLIST_PATH" ]; then
            launchctl unload "$PLIST_PATH" 2>/dev/null
            rm -f "$PLIST_PATH"
            echo "SEC Monitor Deactivated."
        else
            echo "SEC Monitor is already inactive."
        fi
        ;;

    status)
        if launchctl list | grep -q "$PLIST_LABEL"; then
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

        # File access permission check
        echo ""
        if /bin/bash -c "test -r '$PROJECT_DIR/run_monitor.sh'" 2>/dev/null; then
            echo "  File access: ✅ OK"
        else
            echo "  File access: ❌ BLOCKED (grant Full Disk Access to /bin/bash in System Settings)"
        fi
        ;;

    *)
        echo "Usage: $0 on|off|status"
        exit 1
        ;;
esac
