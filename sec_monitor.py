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
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

from edgar import EdgarClient, InsiderTrade, HoldingChange, Form8K, Schedule13DG
from notifier import EmailNotifier, build_form4_email, build_13f_email, build_8k_email, build_13dg_email, build_daily_summary_email

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
        return {"seen_form4": [], "seen_13f": {}, "last_13f_holdings": {}, "seen_8k": [], "seen_13dg": []}

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

    def is_8k_seen(self, filing_id: str) -> bool:
        return filing_id in self.data.get("seen_8k", [])

    def mark_8k_seen(self, filing_id: str):
        self.data.setdefault("seen_8k", []).append(filing_id)
        if len(self.data["seen_8k"]) > 10000:
            self.data["seen_8k"] = self.data["seen_8k"][-5000:]

    def is_13dg_seen(self, filing_id: str) -> bool:
        return filing_id in self.data.get("seen_13dg", [])

    def mark_13dg_seen(self, filing_id: str):
        self.data.setdefault("seen_13dg", []).append(filing_id)
        if len(self.data["seen_13dg"]) > 10000:
            self.data["seen_13dg"] = self.data["seen_13dg"][-5000:]

    def get_last_scan_time(self) -> datetime | None:
        raw = self.data.get("last_scan_time")
        if raw:
            try:
                return datetime.fromisoformat(raw)
            except ValueError:
                pass
        return None

    def set_last_scan_time(self, dt: datetime):
        self.data["last_scan_time"] = dt.isoformat(timespec="seconds")

    def get_today_results(self, today: str) -> dict:
        stored = self.data.get("today_results", {})
        if stored.get("date") != today:
            return {"date": today, "form4": [], "thirteenf": [], "form8k": [], "schedule13dg": []}
        return stored

    def set_today_results(self, results: dict):
        self.data["today_results"] = results

    def reset_today_results(self, today: str):
        self.data["today_results"] = {
            "date": today, "form4": [], "thirteenf": [], "form8k": [], "schedule13dg": []
        }


# ── Serialization helpers (dataclass → dict for today_results) ───────────────

def _trade_to_dict(t) -> dict:
    return {
        "filing_date": t.filing_date, "company_name": t.company_name,
        "ticker": t.ticker, "acquired_or_disposed": t.acquired_or_disposed,
        "insider_name": t.insider_name, "insider_title": t.insider_title,
        "shares": t.shares, "price_per_share": t.price_per_share,
        "total_value": t.total_value, "filing_url": t.filing_url,
    }

def _change_to_dict(c) -> dict:
    return {
        "fund_name": c.fund_name, "fund_cik": c.fund_cik,
        "filing_date": c.filing_date, "filing_url": c.filing_url,
        "issuer_name": c.issuer_name, "ticker": c.ticker,
        "change_type": c.change_type, "current_value": c.current_value,
        "previous_value": c.previous_value, "change_pct": c.change_pct,
    }

def _8k_to_dict(f) -> dict:
    return {
        "filing_date": f.filing_date, "company_name": f.company_name,
        "ticker": f.ticker, "cik": f.cik,
        "item_codes": f.item_codes, "event_description": f.event_description,
        "filing_url": f.filing_url,
    }

def _13dg_to_dict(f) -> dict:
    return {
        "filing_date": f.filing_date, "filer_name": f.filer_name,
        "target_company": f.target_company, "target_ticker": f.target_ticker,
        "target_cik": f.target_cik, "form_type": f.form_type,
        "shares_pct": f.shares_pct, "intent": f.intent,
        "filing_url": f.filing_url,
    }


# ── Trade Aggregation ────────────────────────────────────────

def aggregate_trades(trades: list[InsiderTrade]) -> list[InsiderTrade]:
    """Collapse same-day multi-tranche trades into one row per (insider, ticker, direction, date).

    Groups by (insider_name, ticker, acquired_or_disposed, filing_date). For groups with
    multiple rows: sums shares, computes weighted-average price, sums total_value, and
    records the price range. Single-row groups are returned unchanged.
    """
    from collections import defaultdict
    groups: dict[tuple, list[InsiderTrade]] = defaultdict(list)
    for t in trades:
        key = (t.insider_name, t.ticker, t.acquired_or_disposed, t.filing_date)
        groups[key].append(t)

    result = []
    for group in groups.values():
        if len(group) == 1:
            result.append(group[0])
            continue

        total_shares = sum(t.shares for t in group)
        total_value = sum(t.total_value for t in group)
        weighted_avg = total_value / total_shares if total_shares else 0.0
        min_price = min(t.price_per_share for t in group)
        max_price = max(t.price_per_share for t in group)

        first = group[0]
        result.append(InsiderTrade(
            filing_url=first.filing_url,
            filing_date=first.filing_date,
            insider_name=first.insider_name,
            insider_title=first.insider_title,
            is_director=first.is_director,
            is_officer=first.is_officer,
            company_name=first.company_name,
            ticker=first.ticker,
            transaction_type=first.transaction_type,
            acquired_or_disposed=first.acquired_or_disposed,
            shares=total_shares,
            price_per_share=weighted_avg,
            total_value=total_value,
            price_range=f"${min_price:,.2f}–${max_price:,.2f}",
        ))

    return result


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


# ── Form 8-K Processing ──────────────────────────────────────

def process_8k(client: EdgarClient, config: dict, state: StateManager,
               start_date: str, end_date: str) -> list[Form8K]:
    """Fetch, filter, and return interesting Form 8-K filings."""
    cfg = config.get("form8k", {})
    if not cfg.get("enabled", True):
        logger.info("Form 8-K monitoring disabled, skipping")
        return []

    enabled_items = cfg.get("item_codes", ["1.01", "1.03", "1.05", "2.02", "2.06", "5.02"])
    watchlist = [t.upper() for t in cfg.get("watchlist", [])]
    scan_limit = cfg.get("scan_limit", 100)

    hits = client.search_form8k_filings(start_date, end_date, limit=scan_limit)

    results = []
    for hit in hits:
        source = hit.get("_source", {})
        filing_id = hit.get("_id", "")

        if state.is_8k_seen(filing_id):
            continue

        file_date = source.get("file_date", "")
        file_url = source.get("file_url", "")
        accession = source.get("adsh", "")
        ciks = source.get("ciks", [])
        cik = ciks[0].lstrip("0") if ciks else ""

        # Extract company name from EFTS metadata
        display_names = source.get("display_names", [])
        if display_names and isinstance(display_names[0], dict):
            company_name = display_names[0].get("name", "")
        elif display_names:
            company_name = str(display_names[0])
        else:
            company_name = source.get("entity_name", "Unknown")

        # Extract ticker (may be absent)
        tickers = source.get("tickers", [])
        ticker = tickers[0].upper() if tickers else ""

        # Apply watchlist filter early to skip unnecessary HTTP fetches
        if watchlist and ticker and ticker not in watchlist:
            state.mark_8k_seen(filing_id)
            continue

        if not file_url and cik and accession:
            clean_accession = accession.replace("-", "")
            file_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{clean_accession}/"

        if not file_url:
            state.mark_8k_seen(filing_id)
            continue

        filing = client.fetch_8k_content(file_url, company_name, ticker, cik, file_date)
        state.mark_8k_seen(filing_id)

        if not filing:
            continue

        # Filter by enabled item codes
        if enabled_items and not any(code in filing.item_codes for code in enabled_items):
            logger.debug(f"  ✗ 8-K filtered: no matching items {filing.item_codes}")
            continue

        results.append(filing)
        logger.info(f"  ✓ 8-K: {filing.company_name} items={filing.item_codes}")

    logger.info(f"Form 8-K: {len(results)} filings passed filters (from {len(hits)} filings)")
    return results


# ── Schedule 13D/13G Processing ──────────────────────────────

def process_13dg(client: EdgarClient, config: dict, state: StateManager,
                 start_date: str, end_date: str) -> list[Schedule13DG]:
    """Fetch, filter, and return Schedule 13D/13G filings."""
    cfg = config.get("schedule13dg", {})
    if not cfg.get("enabled", True):
        logger.info("Schedule 13D/13G monitoring disabled, skipping")
        return []

    min_pct = cfg.get("min_percentage", 5.0)
    watchlist = [t.upper() for t in cfg.get("watchlist", [])]
    scan_limit = cfg.get("scan_limit", 50)

    hits = client.search_13dg_filings(start_date, end_date, limit=scan_limit)

    results = []
    for hit in hits:
        source = hit.get("_source", {})
        filing_id = hit.get("_id", "")

        if state.is_13dg_seen(filing_id):
            continue

        file_date = source.get("file_date", "")
        file_url = source.get("file_url", "")
        accession = source.get("adsh", "")
        form_type = source.get("form_type", "SC 13D")
        ciks = source.get("ciks", [])
        cik = ciks[0].lstrip("0") if ciks else ""

        if not file_url and cik and accession:
            clean_accession = accession.replace("-", "")
            file_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{clean_accession}/"

        if not file_url or not accession:
            state.mark_13dg_seen(filing_id)
            continue

        filing = client.fetch_13dg_content(file_url, accession, cik, form_type, file_date)
        state.mark_13dg_seen(filing_id)

        if not filing:
            continue

        # Filter by ownership threshold
        if filing.shares_pct < min_pct:
            logger.debug(f"  ✗ 13D/G filtered: {filing.shares_pct:.1f}% < {min_pct:.1f}%")
            continue

        # Filter by watchlist
        if watchlist and filing.target_ticker and filing.target_ticker.upper() not in watchlist:
            logger.debug(f"  ✗ 13D/G filtered: {filing.target_ticker} not in watchlist")
            continue

        results.append(filing)
        logger.info(f"  ✓ {filing.form_type}: {filing.filer_name} owns {filing.shares_pct:.1f}% of {filing.target_company}")

    logger.info(f"Schedule 13D/13G: {len(results)} filings passed filters (from {len(hits)} filings)")
    return results


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
    parser.add_argument("--days", type=int, default=None, help="Check last N days")
    parser.add_argument("--end-of-day", action="store_true", help="Send daily summary and reset today's results")
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

    # Initialize state early (needed to read last_scan_time for auto date range)
    today_str = date.today().isoformat()
    state = StateManager(config.get("state_file", "state.json"))

    # Determine date range
    if args.date:
        start_date = end_date = args.date
    elif args.days is not None:
        end_date = today_str
        start_date = (date.today() - timedelta(days=args.days)).isoformat()
    else:
        # Auto mode: determine range from last_scan_time
        end_date = today_str
        last_scan = state.get_last_scan_time()
        if last_scan is None:
            # Never run — safe default: yesterday + today
            start_date = (date.today() - timedelta(days=1)).isoformat()
        elif last_scan.date() < date.today():
            # First scan of the day — cover the full gap (handles weekends too)
            gap_days = (date.today() - last_scan.date()).days
            start_date = (date.today() - timedelta(days=gap_days)).isoformat()
            logger.info(f"First scan of day (last: {last_scan.date()}) — gap: {gap_days}d")
        else:
            # Mid-day scan — today only (dedup handles re-sends)
            start_date = today_str

    logger.info(f"═══ SEC Filing Monitor ═══")
    logger.info(f"Checking filings: {start_date} to {end_date}")

    # Initialize client
    client = EdgarClient(
        user_agent=config["sec"]["user_agent"],
        rate_limit_delay=config["sec"].get("rate_limit_delay", 0.15),
    )
    notifier = EmailNotifier(config["email"]) if not args.dry_run else None

    # Process Form 4
    trades = aggregate_trades(process_form4(client, config, state, start_date, end_date))

    if trades and notifier:
        subject, html = build_form4_email(trades, config.get("form4", {}))
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

    # Process Form 8-K
    eightk_filings = process_8k(client, config, state, start_date, end_date)

    if eightk_filings and notifier:
        subject, html = build_8k_email(eightk_filings)
        notifier.send(subject, html)
    elif eightk_filings:
        logger.info(f"[DRY RUN] Would send email with {len(eightk_filings)} Form 8-K filings:")
        for f in eightk_filings:
            logger.info(f"  {f.summary}")

    # Process Schedule 13D/13G
    dg_filings = process_13dg(client, config, state, start_date, end_date)

    if dg_filings and notifier:
        subject, html = build_13dg_email(dg_filings)
        notifier.send(subject, html)
    elif dg_filings:
        logger.info(f"[DRY RUN] Would send email with {len(dg_filings)} Schedule 13D/13G filings:")
        for f in dg_filings:
            logger.info(f"  {f.summary}")

    # Accumulate into today_results (auto mode only — not manual --date/--days overrides)
    is_auto_mode = not args.date and args.days is None
    if is_auto_mode:
        today_results = state.get_today_results(today_str)
        today_results["form4"].extend(_trade_to_dict(t) for t in trades)
        today_results["thirteenf"].extend(_change_to_dict(c) for c in changes)
        today_results["form8k"].extend(_8k_to_dict(f) for f in eightk_filings)
        today_results["schedule13dg"].extend(_13dg_to_dict(f) for f in dg_filings)
        state.set_today_results(today_results)

    # Update last_scan_time and save
    state.set_last_scan_time(datetime.now().replace(microsecond=0))
    state.save()

    # End-of-day summary
    if args.end_of_day:
        today_results = state.get_today_results(today_str)
        total_eod = sum(len(today_results.get(k, [])) for k in ("form4", "thirteenf", "form8k", "schedule13dg"))
        logger.info(f"End-of-day summary: {total_eod} total findings today")
        if total_eod > 0 and notifier:
            subject, html = build_daily_summary_email(today_results, config)
            notifier.send(subject, html)
        elif total_eod > 0 and args.dry_run:
            logger.info(f"[DRY RUN] Would send end-of-day summary with {total_eod} findings")
        else:
            logger.info("No findings today — skipping summary email")
        if not args.dry_run:
            state.reset_today_results(today_str)
            state.save()

    # Log scan summary
    total = len(trades) + len(changes) + len(eightk_filings) + len(dg_filings)
    if total == 0:
        logger.info("No interesting filings found in this scan. 💤")
    else:
        logger.info(f"Done! {len(trades)} trades + {len(changes)} 13F + "
                    f"{len(eightk_filings)} 8-K + {len(dg_filings)} 13D/G")


if __name__ == "__main__":
    main()
