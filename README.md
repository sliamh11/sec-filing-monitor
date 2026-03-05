# SEC Filing Monitor

Automatically fetches SEC filings daily and emails you when insider trades or institutional holding changes match your criteria.

**What it monitors:**
- **Form 4** — Insider trades (CEO/CFO/Director buys/sells above your threshold)
- **13F-HR** — Institutional holdings (new positions, large changes by funds you track)

## Quick Start (5 minutes)

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Create a Gmail App Password

1. Go to https://myaccount.google.com/apppasswords
2. Select "Mail" → "Other" → name it "SEC Monitor"
3. Copy the 16-character password

> **Note:** You need 2-Factor Authentication enabled on your Google account first.

### 3. Configure

Edit `config.yaml`:

```yaml
sec:
  user_agent: "Liam sliamh11@gmail.com"   # Your name + email (required by SEC)

email:
  sender: "sliamh11@gmail.com"
  app_password: "ptrv rqzs fnwf wxce"
  recipient: "sliamh11@gmail.com"
```

Adjust filters as needed (min transaction value, insider roles, fund CIKs, etc.)

### 4. Test it

```bash
# Verify email works
python sec_monitor.py --test-email

# Check yesterday's filings (dry run, no email)
python sec_monitor.py --dry-run

# Check last 3 days
python sec_monitor.py --days 3 --dry-run

# Real run
python sec_monitor.py
```

### 5. Set up the scheduler

The monitor polls every 15 minutes during US market hours (9:25 AM–4:05 PM ET, Mon–Fri) via macOS launchd.

**From anywhere in your terminal (global command):**

```bash
sec-monitor-cron on      # activate
sec-monitor-cron off     # disable
sec-monitor-cron status  # check if active or inactive
```

**Or from the project directory:**

```bash
./cron_setup.sh on
./cron_setup.sh off
./cron_setup.sh status
```

**Logs:**
- `monitor.log` — normal output from every run
- `errors.log` — one timestamped line per failed run (exit code + first 200 chars of stderr)

## CLI Options

| Flag | Description |
|------|-------------|
| `--date YYYY-MM-DD` | Check filings for a specific date |
| `--days N` | Check last N days (default: 1) |
| `--test-email` | Send a test email to verify config |
| `--dry-run` | Find filings but don't send emails |
| `--config PATH` | Use a different config file |
| `-v, --verbose` | Debug-level logging |

## How it Works

```
┌──────────────┐    ┌────────────────┐    ┌──────────────┐    ┌───────────┐
│  SEC EDGAR   │───>│  edgar.py      │───>│ sec_monitor  │───>│   Gmail   │
│  EFTS API    │    │  fetch + parse │    │ filter logic │    │   alert   │
└──────────────┘    └────────────────┘    └──────────────┘    └───────────┘
                                                │
                                          ┌─────┴─────┐
                                          │ state.json │
                                          │ (dedup)    │
                                          └───────────┘
```

1. **Search** EDGAR for recent Form 4 / 13F filings
2. **Fetch** individual XML filings and parse transaction data
3. **Filter** by your criteria (value, role, tickers, etc.)
4. **Deduplicate** against previously seen filings (state.json)
5. **Email** a formatted HTML alert with matching results

## 13F Fund Tracking

To track specific institutional investors, find their CIK number:
1. Go to https://www.sec.gov/cgi-bin/browse-edgar
2. Search by company name (e.g., "Berkshire Hathaway")
3. Copy the CIK number

Add to config:
```yaml
thirteenf:
  track_fund_ciks:
    - "0001067983"  # Berkshire Hathaway
    - "0001350694"  # Bridgewater Associates
```

> 13F filings are quarterly (45 days after quarter end), so the monitor will detect new filings when they appear and compare against the previous quarter's holdings.

## Troubleshooting

**"Email send failed"** — Check that you're using a Gmail App Password (not regular password) and 2FA is enabled.

**No results** — Try `--days 7` to search a wider date range. Weekends/holidays have no filings.

**Rate limited by EDGAR** — Increase `rate_limit_delay` in config (SEC allows max 10 req/sec).

**XML parse errors** — Some filings have non-standard XML. The parser handles most cases gracefully and logs warnings for edge cases.
