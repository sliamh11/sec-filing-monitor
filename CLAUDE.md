# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SEC Filing Monitor is a Python CLI tool that polls SEC EDGAR daily for Form 4 (insider trades) and 13F (institutional holdings) filings, filters them based on configured thresholds, and emails alerts via Gmail SMTP.

## Setup & Running

```bash
pip install -r requirements.txt

# Test email config
python sec_monitor.py --test-email

# Dry run (no email sent)
python sec_monitor.py --dry-run
python sec_monitor.py --days 3 --dry-run

# Normal run (checks yesterday by default)
python sec_monitor.py
python sec_monitor.py --date 2024-01-15
python sec_monitor.py -v  # verbose/debug logging
```

Production runs via cron (e.g., `0 19 * * 1-5 cd /path && python sec_monitor.py >> monitor.log 2>&1`).

There are no tests, no linter configuration, and no build step.

## Architecture

Three modules with a linear pipeline:

**`edgar.py`** — SEC EDGAR API client
- `EdgarClient` class handles all HTTP communication with rate limiting
- `search_form4_filings()` → `fetch_form4_xml()` → `parse_form4_xml()` → `InsiderTrade` dataclass
- `get_fund_latest_13f()` / `search_recent_13f_filings()` → `fetch_13f_holdings()` → `HoldingChange` dataclass

**`sec_monitor.py`** — Orchestrator and CLI entry point
- `StateManager` persists seen filings and previous 13F holdings to `state.json` (JSON file, capped at 10,000 Form 4 entries)
- `process_form4()` applies filters: min transaction value, insider role whitelist, optional ticker watchlist
- `process_13f()` compares current vs. stored previous quarter holdings to detect new positions, exits, and large changes

**`notifier.py`** — Gmail email sender
- `EmailNotifier` sends TLS-encrypted HTML email via `smtp.gmail.com:587`
- `build_form4_email()` and `build_13f_email()` produce HTML tables (color-coded: green=buy, red=sell)

**Data flow:**
```
SEC EDGAR APIs → edgar.py → sec_monitor.py (filter + deduplicate) → notifier.py → Gmail
                                     ↕
                                 state.json
```

## Configuration

All user settings are in `config.yaml`:
- `sec.user_agent` — Required by SEC (must include name + email)
- `sec.rate_limit_delay` — Seconds between API requests (SEC limit: 10 req/sec)
- `form4.min_transaction_value`, `form4.insider_roles`, `form4.watchlist` — Filtering rules
- `thirteenf.track_fund_ciks` — Specific fund CIKs to monitor (empty = broad search)
- `thirteenf.new_position_min_value`, `thirteenf.large_change_threshold` — Change detection thresholds
- `email.*` — Gmail SMTP credentials (requires App Password, not regular password)

## Key External APIs

- EDGAR full-text search: `https://efts.sec.gov/LATEST/search-index`
- EDGAR filing archives: `https://www.sec.gov/Archives/edgar/data/`
- EDGAR submissions API: `https://data.sec.gov/submissions/`

## XML Parsing Notes

Form 4 and 13F filings use XML with inconsistent namespace usage. `edgar.py` handles both namespaced and non-namespaced variants via the `_text()` helper which tries multiple tag formats.
