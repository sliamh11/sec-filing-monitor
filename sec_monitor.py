#!/usr/bin/env python3
"""
SEC Filing Monitor — Main entry point.

Fetches Form 4 insider trades and 13F institutional holdings from SEC EDGAR,
applies your filters, and sends email alerts for interesting findings.

Usage:
    python sec_monitor.py              # Normal run (check yesterday's filings)
    python sec_monitor.py --date 2024-01-15   # Check a specific date
    python sec_monitor.py --days 3     # Check last N days
    python sec_monitor.py --test-email # Send a test email to verify config
    python sec_monitor.py --dry-run    # Find filings but don't send emails
"""

import argparse
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

from edgar import EdgarClient, InsiderTrade, HoldingChange
from notifier import EmailNotifier, build_form4_email, build_13f_email

# ── Logging ──────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("sec_monitor")

# ── State Management ─────────────────────────────────────────

class StateManager:
    """Tracks seen filings to avoid duplicate notifications."""

    def __init__(self, path: str):
        self.path = Path(path)
        self.data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except (json.JSONDecodeError, IOError):
                logger.warning(f"Corrupted state file, starting fresh")
        return {"seen_form4": [], "seen_13f": {}, "last_13f_holdings": {}}

    def save(self):
        self.path.write_text(json.dumps(self.data, indent=2))

    def is_form4_seen(self, filing_id: str) -> bool:
        return filing_id in self.data["seen_form4"]

    def mark_form4_seen(self, filing_id: str):
        self.data["seen_form4"].append(filing_id)
        # Keep only last 10,000 entries to prevent unbounded growth
        if len(self.data["seen_form4"]) > 10000:
            self.data["seen_form4"] = self.data["seen_form4"][-5000:]

    def is_13f_seen(self, fund_cik: str, accession: str) -> bool:
        return self.data.get("seen_13f", {}).get(fund_cik) == accession

    def mark_13f_seen(self, fund_cik: str, accession: str):
        self.data.setdefault("seen_13f", {})[fund_cik] = accession

    def get_previous_holdings(self, fund_cik: str) -> dict:
        """Get previous 13F holdings for comparison. Returns {cusip: {issuer, value, shares}}."""
        return self.data.get("last_13f_holdings", {}).get(fund_cik, {})

    def store_holdings(self, fund_cik: str, holdings: list[dict]):
        """Store current holdings for future comparison."""
        self.data.setdefault("last_13f_holdings", {})[fund_cik] = {
            h["cusip"]: h for h in holdings if h.get("cusip")
        }


# ── Form 4 Processing ───────────────────────────────────────

def process_form4(client: EdgarClient, config: dict, state: StateManager,
                  start_date: str, end_date: str) -> list[InsiderTrade]:
    """Fetch, filter, and return interesting Form 4 trades."""
    form4_cfg = config["form4"]
    if not form4_cfg.get("enabled", True):
        logger.info("Form 4 monitoring disabled, skipping")
        return []

    min_value = form4_cfg.get("min_transaction_value", 100000)
    roles = [r.lower() for r in form4_cfg.get("insider_roles", [])]
    watchlist = [t.upper() for t in form4_cfg.get("watchlist", [])]
    scan_limit = form4_cfg.get("scan_limit", 100)

    # Search for recent Form 4 filings
    hits = client.search_form4_filings(start_date, end_date, limit=scan_limit)

    interesting_trades = []
    for hit in hits:
        source = hit.get("_source", {})
        filing_id = hit.get("_id", "")

        # Skip if already seen
        if state.is_form4_seen(filing_id):
            continue

        file_date = source.get("file_date", "")
        file_url = source.get("file_url", "")

        if not file_url:
            # EFTS API uses 'adsh' (accession) and 'ciks' (list)
            ciks = source.get("ciks", [])
            accession = source.get("adsh", "")
            # Last CIK is typically the issuer/company
            cik = ciks[-1].lstrip("0") if ciks else ""
            if cik and accession:
                clean_accession = accession.replace("-", "")
                file_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{clean_accession}/"

        if not file_url:
            continue

        # Fetch and parse the actual Form 4 XML
        xml_content = client.fetch_form4_xml(file_url)
        if not xml_content:
            state.mark_form4_seen(filing_id)
            continue

        trades = client.parse_form4_xml(xml_content, file_url, file_date)

        for trade in trades:
            # Apply filters
            passes_value = trade.total_value >= min_value
            passes_role = (
                not roles
                or any(role in trade.insider_title.lower() for role in roles)
                or (trade.is_director and "director" in roles)
            )
            passes_watchlist = not watchlist or trade.ticker in watchlist

            if passes_value and passes_role and passes_watchlist:
                interesting_trades.append(trade)
                logger.info(f"  ✓ {trade.summary}")
            else:
                reasons = []
                if not passes_value:
                    reasons.append(f"value ${trade.total_value:,.0f} < ${min_value:,.0f}")
                if not passes_role:
                    reasons.append(f"role '{trade.insider_title}' not in filter")
                if not passes_watchlist:
                    reasons.append(f"ticker {trade.ticker} not in watchlist")
                logger.debug(f"  ✗ Filtered out: {', '.join(reasons)}")

        state.mark_form4_seen(filing_id)

    logger.info(f"Form 4: {len(interesting_trades)} trades passed filters (from {len(hits)} filings)")
    return interesting_trades


# ── 13F Processing ───────────────────────────────────────────

def process_13f(client: EdgarClient, config: dict, state: StateManager,
                start_date: str, end_date: str) -> list[HoldingChange]:
    """Fetch, compare, and return interesting 13F holding changes."""
    cfg = config["thirteenf"]
    if not cfg.get("enabled", True):
        logger.info("13F monitoring disabled, skipping")
        return []

    fund_ciks = cfg.get("track_fund_ciks", [])
    new_pos_min = cfg.get("new_position_min_value", 1000000)
    change_threshold = cfg.get("large_change_threshold", 0.25)

    all_changes = []

    if fund_ciks:
        # Track specific funds
        for cik in fund_ciks:
            filing = client.get_fund_latest_13f(cik)
            if not filing:
                continue

            accession = filing["accession"]
            if state.is_13f_seen(cik, accession):
                logger.info(f"13F for {filing['fund_name']} already processed, skipping")
                continue

            logger.info(f"Processing 13F for {filing['fund_name']} ({filing['filing_date']})")

            # Fetch current holdings
            current_holdings = client.fetch_13f_holdings(accession, cik)
            if not current_holdings:
                logger.warning(f"No holdings found in 13F for {filing['fund_name']}")
                continue

            # Compare with previous holdings
            previous = state.get_previous_holdings(cik)
            changes = _compare_holdings(
                fund_name=filing["fund_name"],
                fund_cik=cik,
                filing_date=filing["filing_date"],
                filing_url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=13F-HR",
                current=current_holdings,
                previous=previous,
                new_pos_min=new_pos_min,
                change_threshold=change_threshold,
            )

            all_changes.extend(changes)

            # Store current holdings for next comparison
            state.store_holdings(cik, current_holdings)
            state.mark_13f_seen(cik, accession)

    else:
        # Search for recent 13F filings from any filer
        logger.info("No specific fund CIKs configured — searching recent 13F filings")
        hits = client.search_recent_13f_filings(start_date, end_date, limit=cfg.get("scan_limit", 50))
        logger.info(f"Found {len(hits)} recent 13F filings (add specific fund CIKs to config for detailed tracking)")

    logger.info(f"13F: {len(all_changes)} significant changes detected")
    return all_changes


def _compare_holdings(fund_name, fund_cik, filing_date, filing_url,
                      current, previous, new_pos_min, change_threshold):
    """Compare current vs previous 13F holdings and return significant changes."""
    changes = []
    current_by_cusip = {h["cusip"]: h for h in current if h.get("cusip")}
    prev_cusips = set(previous.keys())
    curr_cusips = set(current_by_cusip.keys())

    # New positions
    for cusip in curr_cusips - prev_cusips:
        h = current_by_cusip[cusip]
        if h["value"] >= new_pos_min:
            changes.append(HoldingChange(
                fund_name=fund_name, fund_cik=fund_cik,
                filing_date=filing_date, filing_url=filing_url,
                issuer_name=h["issuer"], ticker=h.get("ticker", ""),
                change_type="new_position",
                current_value=h["value"], previous_value=0, change_pct=None,
            ))

    # Exited positions
    for cusip in prev_cusips - curr_cusips:
        h = previous[cusip]
        changes.append(HoldingChange(
            fund_name=fund_name, fund_cik=fund_cik,
            filing_date=filing_date, filing_url=filing_url,
            issuer_name=h.get("issuer", "Unknown"), ticker=h.get("ticker", ""),
            change_type="exited",
            current_value=0, previous_value=h.get("value", 0), change_pct=-1.0,
        ))

    # Changed positions
    for cusip in curr_cusips & prev_cusips:
        curr_h = current_by_cusip[cusip]
        prev_h = previous[cusip]
        curr_val = curr_h["value"]
        prev_val = prev_h.get("value", 0)

        if prev_val > 0:
            pct_change = (curr_val - prev_val) / prev_val
            if abs(pct_change) >= change_threshold:
                change_type = "large_increase" if pct_change > 0 else "large_decrease"
                changes.append(HoldingChange(
                    fund_name=fund_name, fund_cik=fund_cik,
                    filing_date=filing_date, filing_url=filing_url,
                    issuer_name=curr_h["issuer"], ticker=curr_h.get("ticker", ""),
                    change_type=change_type,
                    current_value=curr_val, previous_value=prev_val, change_pct=pct_change,
                ))

    return changes


# ── Main ─────────────────────────────────────────────────────

def load_config(path: str = "config.yaml") -> dict:
    """Load and validate configuration."""
    config_path = Path(path)
    if not config_path.exists():
        logger.error(f"Config file not found: {path}")
        logger.error("Copy config.yaml.example to config.yaml and fill in your details")
        sys.exit(1)

    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Basic validation
    if config["sec"]["user_agent"] == "YourName your.email@example.com":
        logger.error("Please update 'sec.user_agent' in config.yaml with your name and email")
        sys.exit(1)

    if config["email"]["app_password"] == "xxxx xxxx xxxx xxxx":
        logger.warning("Email not configured — will run in dry-run mode")

    return config


def send_test_email(config: dict):
    """Send a test email to verify configuration."""
    notifier = EmailNotifier(config["email"])
    test_html = """
    <html><body style="font-family: sans-serif; padding: 20px;">
        <h2>✅ SEC Filing Monitor — Test Email</h2>
        <p>If you're reading this, your email configuration is working correctly!</p>
        <p>The monitor will send alerts in this format when it finds interesting filings.</p>
    </body></html>"""

    success = notifier.send("✅ SEC Monitor — Test Email", test_html)
    if success:
        logger.info("Test email sent successfully! Check your inbox.")
    else:
        logger.error("Test email failed. Check your SMTP settings and app password.")
    return success


def main():
    parser = argparse.ArgumentParser(description="SEC Filing Monitor")
    parser.add_argument("--date", help="Check filings for a specific date (YYYY-MM-DD)")
    parser.add_argument("--days", type=int, default=1, help="Check last N days (default: 1)")
    parser.add_argument("--test-email", action="store_true", help="Send a test email")
    parser.add_argument("--dry-run", action="store_true", help="Find filings but don't send emails")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Load config
    config = load_config(args.config)

    # Test email mode
    if args.test_email:
        send_test_email(config)
        return

    # Determine date range
    if args.date:
        start_date = end_date = args.date
    else:
        end_date = date.today().isoformat()
        start_date = (date.today() - timedelta(days=args.days)).isoformat()

    logger.info(f"═══ SEC Filing Monitor ═══")
    logger.info(f"Checking filings: {start_date} to {end_date}")

    # Initialize components
    client = EdgarClient(
        user_agent=config["sec"]["user_agent"],
        rate_limit_delay=config["sec"].get("rate_limit_delay", 0.15),
    )
    state = StateManager(config.get("state_file", "state.json"))
    notifier = EmailNotifier(config["email"]) if not args.dry_run else None

    # Process Form 4
    trades = process_form4(client, config, state, start_date, end_date)

    if trades and notifier:
        subject, html = build_form4_email(trades)
        notifier.send(subject, html)
    elif trades:
        logger.info(f"[DRY RUN] Would send email with {len(trades)} trades:")
        for t in trades:
            logger.info(f"  {t.summary}")

    # Process 13F
    changes = process_13f(client, config, state, start_date, end_date)

    if changes and notifier:
        subject, html = build_13f_email(changes)
        notifier.send(subject, html)
    elif changes:
        logger.info(f"[DRY RUN] Would send email with {len(changes)} holding changes:")
        for c in changes:
            logger.info(f"  {c.summary}")

    # Save state
    state.save()

    # Summary
    total = len(trades) + len(changes)
    if total == 0:
        logger.info("No interesting filings found today. 💤")
    else:
        logger.info(f"Done! {len(trades)} insider trades + {len(changes)} holding changes")


if __name__ == "__main__":
    main()
