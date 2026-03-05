"""
Email notification module — sends formatted HTML alerts via Gmail SMTP.
"""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import date

logger = logging.getLogger(__name__)


class EmailNotifier:
    """Sends SEC filing alerts via email."""

    def __init__(self, config: dict):
        self.smtp_server = config["smtp_server"]
        self.smtp_port = config["smtp_port"]
        self.sender = config["sender"]
        self.password = config["app_password"]
        self.recipient = config["recipient"]

    def send(self, subject: str, html_body: str) -> bool:
        """Send an HTML email. Returns True on success."""
        msg = MIMEMultipart("alternative")
        msg["From"] = self.sender
        msg["To"] = self.recipient
        msg["Subject"] = subject

        # Plain text fallback
        plain_text = html_body.replace("<br>", "\n").replace("</tr>", "\n")
        import re
        plain_text = re.sub(r"<[^>]+>", "", plain_text)

        msg.attach(MIMEText(plain_text, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        try:
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(self.sender, self.password)
                server.send_message(msg)
            logger.info(f"Email sent: {subject}")
            return True
        except Exception as e:
            logger.error(f"Email send failed: {e}")
            return False


def build_form4_email(trades: list, form4_config: dict = None) -> tuple[str, str]:
    """
    Build subject and HTML body for Form 4 insider trade alerts.
    Returns (subject, html_body).
    """
    today = date.today().isoformat()
    subject = f"🔔 SEC Insider Trades Alert — {len(trades)} trades ({today})"

    # Build filters section
    filters_html = ""
    if form4_config:
        min_val = form4_config.get("min_transaction_value", 0)
        roles = form4_config.get("insider_roles", [])
        watchlist = form4_config.get("watchlist", [])

        min_val_str = f"${min_val:,.0f}" if min_val else "$0"
        roles_str = ", ".join(roles) if roles else "All roles"
        watchlist_str = ", ".join(watchlist) if watchlist else "All tickers"

        filters_html = f"""
        <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px;
                    padding: 14px 18px; margin-bottom: 20px; font-size: 13px;">
            <div style="font-weight: 600; color: #374151; margin-bottom: 8px;">Active Filters</div>
            <table style="border-collapse: collapse;">
                <tr>
                    <td style="color: #6b7280; padding: 2px 16px 2px 0; white-space: nowrap;">Min transaction value:</td>
                    <td style="color: #111827; font-weight: 500;">{min_val_str}</td>
                </tr>
                <tr>
                    <td style="color: #6b7280; padding: 2px 16px 2px 0; white-space: nowrap;">Insider roles:</td>
                    <td style="color: #111827; font-weight: 500;">{roles_str}</td>
                </tr>
                <tr>
                    <td style="color: #6b7280; padding: 2px 16px 2px 0; white-space: nowrap;">Watchlist:</td>
                    <td style="color: #111827; font-weight: 500;">{watchlist_str}</td>
                </tr>
            </table>
        </div>"""

    rows = ""
    for t in trades:
        action_color = "#22c55e" if t.acquired_or_disposed == "A" else "#ef4444"
        action_label = "BUY" if t.acquired_or_disposed == "A" else "SELL"
        rows += f"""
        <tr style="border-bottom: 1px solid #e5e7eb;">
            <td style="padding: 10px;">{t.filing_date}</td>
            <td style="padding: 10px;">{t.company_name}</td>
            <td style="padding: 10px; font-weight: 600;">{t.ticker}</td>
            <td style="padding: 10px; color: {action_color}; font-weight: 600;">{action_label}</td>
            <td style="padding: 10px;">{t.insider_name}<br>
                <span style="color: #6b7280; font-size: 12px;">{t.insider_title}</span></td>
            <td style="padding: 10px; text-align: right;">{t.shares:,.0f}</td>
            <td style="padding: 10px; text-align: right;">${t.price_per_share:,.2f}</td>
            <td style="padding: 10px; text-align: right;">${t.total_value:,.0f}</td>
            <td style="padding: 10px;">
                <a href="{t.filing_url}" style="color: #3b82f6;">View</a></td>
        </tr>"""

    html = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                 max-width: 1000px; margin: 0 auto; padding: 20px; color: #1f2937;">
        <h2 style="color: #111827; border-bottom: 2px solid #3b82f6; padding-bottom: 10px;">
            📊 SEC Insider Trades — {today}
        </h2>
        <p style="color: #6b7280;">{len(trades)} trades matched your filters</p>
        {filters_html}
        <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
            <thead>
                <tr style="background-color: #f9fafb; border-bottom: 2px solid #e5e7eb;">
                    <th style="padding: 10px; text-align: left;">Date</th>
                    <th style="padding: 10px; text-align: left;">Company</th>
                    <th style="padding: 10px; text-align: left;">Ticker</th>
                    <th style="padding: 10px; text-align: left;">Buy / Sell</th>
                    <th style="padding: 10px; text-align: left;">Insider</th>
                    <th style="padding: 10px; text-align: right;">Shares</th>
                    <th style="padding: 10px; text-align: right;">Price / Share</th>
                    <th style="padding: 10px; text-align: right;">Total Value</th>
                    <th style="padding: 10px;">Filing</th>
                </tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>

        <p style="color: #9ca3af; font-size: 12px; margin-top: 30px; border-top: 1px solid #e5e7eb; padding-top: 10px;">
            SEC Filing Monitor • Data from EDGAR •
            <a href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=4" style="color: #9ca3af;">
                Browse all Form 4 filings
            </a>
        </p>
    </body>
    </html>"""

    return subject, html


def build_8k_email(filings: list) -> tuple[str, str]:
    """
    Build subject and HTML body for Form 8-K material event alerts.
    Returns (subject, html_body).
    """
    today = date.today().isoformat()
    subject = f"⚡ SEC Form 8-K Alert — {len(filings)} material events ({today})"

    # Item code labels for the most common monitored items
    item_labels = {
        "1.01": "Material Agreement",
        "1.02": "Termination of Agreement",
        "1.03": "Bankruptcy/Receivership",
        "1.05": "Cybersecurity Incident",
        "2.01": "Completion of Acquisition",
        "2.02": "Financial Results",
        "2.06": "Material Impairment",
        "5.02": "Executive Change",
        "8.01": "Other Events",
    }

    rows = ""
    for f in filings:
        item_badges = ""
        for code in f.item_codes:
            label = item_labels.get(code, code)
            item_badges += (
                f'<span style="display:inline-block; background:#dbeafe; color:#1d4ed8; '
                f'border-radius:4px; padding:2px 6px; font-size:11px; margin:1px;">'
                f'{code} {label}</span>'
            )
        desc = f.event_description[:120] + "…" if len(f.event_description) > 120 else f.event_description
        ticker_cell = f.ticker if f.ticker else "—"
        rows += f"""
        <tr style="border-bottom: 1px solid #e5e7eb;">
            <td style="padding: 10px;">{f.filing_date}</td>
            <td style="padding: 10px; font-weight: 600;">{f.company_name}</td>
            <td style="padding: 10px;">{ticker_cell}</td>
            <td style="padding: 10px;">{item_badges}</td>
            <td style="padding: 10px; color: #6b7280; font-size: 12px;">{desc}</td>
            <td style="padding: 10px;">
                <a href="{f.filing_url}" style="color: #3b82f6;">View</a></td>
        </tr>"""

    html = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                 max-width: 960px; margin: 0 auto; padding: 20px; color: #1f2937;">
        <h2 style="color: #111827; border-bottom: 2px solid #f59e0b; padding-bottom: 10px;">
            ⚡ Form 8-K Material Events — {today}
        </h2>
        <p style="color: #6b7280;">{len(filings)} events matched your configured item codes</p>

        <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
            <thead>
                <tr style="background-color: #f9fafb; border-bottom: 2px solid #e5e7eb;">
                    <th style="padding: 10px; text-align: left;">Date</th>
                    <th style="padding: 10px; text-align: left;">Company</th>
                    <th style="padding: 10px; text-align: left;">Ticker</th>
                    <th style="padding: 10px; text-align: left;">Items</th>
                    <th style="padding: 10px; text-align: left;">Description</th>
                    <th style="padding: 10px;">Filing</th>
                </tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>

        <p style="color: #9ca3af; font-size: 12px; margin-top: 30px; border-top: 1px solid #e5e7eb; padding-top: 10px;">
            SEC Filing Monitor • Data from EDGAR •
            <a href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=8-K" style="color: #9ca3af;">
                Browse all 8-K filings
            </a>
        </p>
    </body>
    </html>"""

    return subject, html


def build_13dg_email(filings: list) -> tuple[str, str]:
    """
    Build subject and HTML body for Schedule 13D/13G ownership stake alerts.
    Returns (subject, html_body).
    """
    today = date.today().isoformat()
    subject = f"🎯 SEC 13D/13G Alert — {len(filings)} ownership stakes ({today})"

    rows = ""
    for f in filings:
        form_color = "#ef4444" if "13D" in f.form_type else "#8b5cf6"
        pct_str = f"{f.shares_pct:.1f}%" if f.shares_pct else "—"
        intent_short = f.intent[:100] + "…" if len(f.intent) > 100 else f.intent
        ticker_cell = f.target_ticker if f.target_ticker else "—"
        rows += f"""
        <tr style="border-bottom: 1px solid #e5e7eb;">
            <td style="padding: 10px;">{f.filing_date}</td>
            <td style="padding: 10px; color: {form_color}; font-weight: 600;">{f.form_type}</td>
            <td style="padding: 10px;">{f.filer_name}</td>
            <td style="padding: 10px; font-weight: 600;">{f.target_company}</td>
            <td style="padding: 10px;">{ticker_cell}</td>
            <td style="padding: 10px; font-weight: 600; text-align: right;">{pct_str}</td>
            <td style="padding: 10px; color: #6b7280; font-size: 12px;">{intent_short}</td>
            <td style="padding: 10px;">
                <a href="{f.filing_url}" style="color: #3b82f6;">View</a></td>
        </tr>"""

    html = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                 max-width: 1000px; margin: 0 auto; padding: 20px; color: #1f2937;">
        <h2 style="color: #111827; border-bottom: 2px solid #ef4444; padding-bottom: 10px;">
            🎯 Schedule 13D/13G Ownership Stakes — {today}
        </h2>
        <p style="color: #6b7280;">{len(filings)} filings detected — 13D=activist intent, 13G=passive 5%+</p>

        <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
            <thead>
                <tr style="background-color: #f9fafb; border-bottom: 2px solid #e5e7eb;">
                    <th style="padding: 10px; text-align: left;">Date</th>
                    <th style="padding: 10px; text-align: left;">Form</th>
                    <th style="padding: 10px; text-align: left;">Filer</th>
                    <th style="padding: 10px; text-align: left;">Target</th>
                    <th style="padding: 10px; text-align: left;">Ticker</th>
                    <th style="padding: 10px; text-align: right;">% Owned</th>
                    <th style="padding: 10px; text-align: left;">Intent</th>
                    <th style="padding: 10px;">Filing</th>
                </tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>

        <p style="color: #9ca3af; font-size: 12px; margin-top: 30px; border-top: 1px solid #e5e7eb; padding-top: 10px;">
            SEC Filing Monitor • Data from EDGAR •
            <a href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=SC+13D" style="color: #9ca3af;">
                Browse all 13D/13G filings
            </a>
        </p>
    </body>
    </html>"""

    return subject, html


def build_daily_summary_email(today_results: dict, config: dict = None) -> tuple[str, str]:
    """
    Build subject and HTML body for the end-of-day summary digest.
    today_results contains plain dicts (not dataclasses).
    Returns (subject, html_body).
    """
    report_date = today_results.get("date", date.today().isoformat())
    form4_items = today_results.get("form4", [])
    thirteenf_items = today_results.get("thirteenf", [])
    form8k_items = today_results.get("form8k", [])
    schedule13dg_items = today_results.get("schedule13dg", [])
    total = len(form4_items) + len(thirteenf_items) + len(form8k_items) + len(schedule13dg_items)

    subject = f"📋 SEC Daily Summary — {total} findings ({report_date})"

    # Counts banner
    counts_html = f"""
    <div style="display:flex; gap:12px; margin-bottom:24px; flex-wrap:wrap;">
        <div style="background:#dbeafe; border-radius:8px; padding:12px 20px; text-align:center; min-width:80px;">
            <div style="font-size:22px; font-weight:700; color:#1d4ed8;">{len(form4_items)}</div>
            <div style="font-size:11px; color:#3b82f6; text-transform:uppercase; letter-spacing:.5px;">Form 4</div>
        </div>
        <div style="background:#dcfce7; border-radius:8px; padding:12px 20px; text-align:center; min-width:80px;">
            <div style="font-size:22px; font-weight:700; color:#15803d;">{len(thirteenf_items)}</div>
            <div style="font-size:11px; color:#22c55e; text-transform:uppercase; letter-spacing:.5px;">13F</div>
        </div>
        <div style="background:#fef9c3; border-radius:8px; padding:12px 20px; text-align:center; min-width:80px;">
            <div style="font-size:22px; font-weight:700; color:#a16207;">{len(form8k_items)}</div>
            <div style="font-size:11px; color:#f59e0b; text-transform:uppercase; letter-spacing:.5px;">Form 8-K</div>
        </div>
        <div style="background:#fce7f3; border-radius:8px; padding:12px 20px; text-align:center; min-width:80px;">
            <div style="font-size:22px; font-weight:700; color:#be185d;">{len(schedule13dg_items)}</div>
            <div style="font-size:11px; color:#ec4899; text-transform:uppercase; letter-spacing:.5px;">13D/G</div>
        </div>
    </div>"""

    # Form 4 section
    form4_section = ""
    if form4_items:
        rows = ""
        for t in form4_items:
            action_color = "#22c55e" if t["acquired_or_disposed"] == "A" else "#ef4444"
            action_label = "BUY" if t["acquired_or_disposed"] == "A" else "SELL"
            rows += f"""
            <tr style="border-bottom: 1px solid #e5e7eb;">
                <td style="padding: 8px;">{t["filing_date"]}</td>
                <td style="padding: 8px;">{t["company_name"]}</td>
                <td style="padding: 8px; font-weight: 600;">{t["ticker"]}</td>
                <td style="padding: 8px; color: {action_color}; font-weight: 600;">{action_label}</td>
                <td style="padding: 8px;">{t["insider_name"]}<br>
                    <span style="color:#6b7280; font-size:11px;">{t["insider_title"]}</span></td>
                <td style="padding: 8px; text-align: right;">{t["shares"]:,.0f}</td>
                <td style="padding: 8px; text-align: right;">${t["price_per_share"]:,.2f}</td>
                <td style="padding: 8px; text-align: right;">${t["total_value"]:,.0f}</td>
                <td style="padding: 8px;"><a href="{t["filing_url"]}" style="color:#3b82f6;">View</a></td>
            </tr>"""
        form4_section = f"""
        <h3 style="color:#1d4ed8; border-left:4px solid #3b82f6; padding-left:10px; margin-top:28px;">
            📊 Insider Trades ({len(form4_items)})
        </h3>
        <table style="width:100%; border-collapse:collapse; font-size:13px;">
            <thead>
                <tr style="background:#f0f9ff; border-bottom:2px solid #bfdbfe;">
                    <th style="padding:8px; text-align:left;">Date</th>
                    <th style="padding:8px; text-align:left;">Company</th>
                    <th style="padding:8px; text-align:left;">Ticker</th>
                    <th style="padding:8px; text-align:left;">Buy/Sell</th>
                    <th style="padding:8px; text-align:left;">Insider</th>
                    <th style="padding:8px; text-align:right;">Shares</th>
                    <th style="padding:8px; text-align:right;">Price</th>
                    <th style="padding:8px; text-align:right;">Value</th>
                    <th style="padding:8px;">Filing</th>
                </tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>"""

    # 13F section
    thirteenf_section = ""
    if thirteenf_items:
        type_colors = {
            "new_position": ("#22c55e", "🆕 NEW"),
            "exited": ("#ef4444", "🚪 EXIT"),
            "large_increase": ("#3b82f6", "📈 UP"),
            "large_decrease": ("#f59e0b", "📉 DOWN"),
        }
        rows = ""
        for c in thirteenf_items:
            color, label = type_colors.get(c["change_type"], ("#6b7280", c["change_type"]))
            pct = f"{c['change_pct']:+.1%}" if c["change_pct"] is not None else "—"
            rows += f"""
            <tr style="border-bottom: 1px solid #e5e7eb;">
                <td style="padding: 8px; font-weight:600;">{c["fund_name"]}</td>
                <td style="padding: 8px;">{c["issuer_name"]}</td>
                <td style="padding: 8px;">{c["ticker"]}</td>
                <td style="padding: 8px; color:{color}; font-weight:600;">{label}</td>
                <td style="padding: 8px; text-align:right;">${c["current_value"]:,.0f}</td>
                <td style="padding: 8px; text-align:right;">{pct}</td>
                <td style="padding: 8px;"><a href="{c["filing_url"]}" style="color:#3b82f6;">View</a></td>
            </tr>"""
        thirteenf_section = f"""
        <h3 style="color:#7c3aed; border-left:4px solid #8b5cf6; padding-left:10px; margin-top:28px;">
            🏦 13F Holdings Changes ({len(thirteenf_items)})
        </h3>
        <table style="width:100%; border-collapse:collapse; font-size:13px;">
            <thead>
                <tr style="background:#f5f3ff; border-bottom:2px solid #ddd6fe;">
                    <th style="padding:8px; text-align:left;">Fund</th>
                    <th style="padding:8px; text-align:left;">Issuer</th>
                    <th style="padding:8px; text-align:left;">Ticker</th>
                    <th style="padding:8px; text-align:left;">Change</th>
                    <th style="padding:8px; text-align:right;">Value</th>
                    <th style="padding:8px; text-align:right;">% Change</th>
                    <th style="padding:8px;">Filing</th>
                </tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>"""

    # 8-K section
    form8k_section = ""
    if form8k_items:
        item_labels = {
            "1.01": "Material Agreement", "1.02": "Termination",
            "1.03": "Bankruptcy", "1.05": "Cybersecurity",
            "2.01": "Acquisition", "2.02": "Financial Results",
            "2.06": "Impairment", "5.02": "Executive Change", "8.01": "Other",
        }
        rows = ""
        for f in form8k_items:
            badges = "".join(
                f'<span style="display:inline-block;background:#dbeafe;color:#1d4ed8;'
                f'border-radius:4px;padding:1px 5px;font-size:11px;margin:1px;">'
                f'{code} {item_labels.get(code, code)}</span>'
                for code in f["item_codes"]
            )
            desc = f["event_description"][:100] + "…" if len(f["event_description"]) > 100 else f["event_description"]
            rows += f"""
            <tr style="border-bottom: 1px solid #e5e7eb;">
                <td style="padding: 8px;">{f["filing_date"]}</td>
                <td style="padding: 8px; font-weight:600;">{f["company_name"]}</td>
                <td style="padding: 8px;">{f["ticker"] or "—"}</td>
                <td style="padding: 8px;">{badges}</td>
                <td style="padding: 8px; color:#6b7280; font-size:12px;">{desc}</td>
                <td style="padding: 8px;"><a href="{f["filing_url"]}" style="color:#3b82f6;">View</a></td>
            </tr>"""
        form8k_section = f"""
        <h3 style="color:#b45309; border-left:4px solid #f59e0b; padding-left:10px; margin-top:28px;">
            ⚡ Form 8-K Events ({len(form8k_items)})
        </h3>
        <table style="width:100%; border-collapse:collapse; font-size:13px;">
            <thead>
                <tr style="background:#fffbeb; border-bottom:2px solid #fde68a;">
                    <th style="padding:8px; text-align:left;">Date</th>
                    <th style="padding:8px; text-align:left;">Company</th>
                    <th style="padding:8px; text-align:left;">Ticker</th>
                    <th style="padding:8px; text-align:left;">Items</th>
                    <th style="padding:8px; text-align:left;">Description</th>
                    <th style="padding:8px;">Filing</th>
                </tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>"""

    # 13D/G section
    schedule13dg_section = ""
    if schedule13dg_items:
        rows = ""
        for f in schedule13dg_items:
            form_color = "#ef4444" if "13D" in f["form_type"] else "#8b5cf6"
            pct_str = f"{f['shares_pct']:.1f}%" if f["shares_pct"] else "—"
            intent_short = f["intent"][:80] + "…" if len(f["intent"]) > 80 else f["intent"]
            rows += f"""
            <tr style="border-bottom: 1px solid #e5e7eb;">
                <td style="padding: 8px;">{f["filing_date"]}</td>
                <td style="padding: 8px; color:{form_color}; font-weight:600;">{f["form_type"]}</td>
                <td style="padding: 8px;">{f["filer_name"]}</td>
                <td style="padding: 8px; font-weight:600;">{f["target_company"]}</td>
                <td style="padding: 8px;">{f["target_ticker"] or "—"}</td>
                <td style="padding: 8px; font-weight:600; text-align:right;">{pct_str}</td>
                <td style="padding: 8px; color:#6b7280; font-size:12px;">{intent_short}</td>
                <td style="padding: 8px;"><a href="{f["filing_url"]}" style="color:#3b82f6;">View</a></td>
            </tr>"""
        schedule13dg_section = f"""
        <h3 style="color:#be185d; border-left:4px solid #ec4899; padding-left:10px; margin-top:28px;">
            🎯 Schedule 13D/13G ({len(schedule13dg_items)})
        </h3>
        <table style="width:100%; border-collapse:collapse; font-size:13px;">
            <thead>
                <tr style="background:#fdf4ff; border-bottom:2px solid #f5d0fe;">
                    <th style="padding:8px; text-align:left;">Date</th>
                    <th style="padding:8px; text-align:left;">Form</th>
                    <th style="padding:8px; text-align:left;">Filer</th>
                    <th style="padding:8px; text-align:left;">Target</th>
                    <th style="padding:8px; text-align:left;">Ticker</th>
                    <th style="padding:8px; text-align:right;">% Owned</th>
                    <th style="padding:8px; text-align:left;">Intent</th>
                    <th style="padding:8px;">Filing</th>
                </tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>"""

    html = f"""
    <html>
    <body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
                 max-width:1100px; margin:0 auto; padding:20px; color:#1f2937;">
        <h2 style="color:#111827; border-bottom:2px solid #6366f1; padding-bottom:10px;">
            📋 SEC Daily Summary — {report_date}
        </h2>
        <p style="color:#6b7280;">{total} total findings across all filing types today</p>
        {counts_html}
        {form4_section}
        {thirteenf_section}
        {form8k_section}
        {schedule13dg_section}
        <p style="color:#9ca3af; font-size:12px; margin-top:30px; border-top:1px solid #e5e7eb; padding-top:10px;">
            End-of-Day Summary • Data from EDGAR
        </p>
    </body>
    </html>"""

    return subject, html


def build_13f_email(changes: list) -> tuple[str, str]:
    """
    Build subject and HTML body for 13F holding change alerts.
    Returns (subject, html_body).
    """
    today = date.today().isoformat()
    subject = f"🏦 SEC 13F Holdings Alert — {len(changes)} changes ({today})"

    rows = ""
    for c in changes:
        type_colors = {
            "new_position": ("#22c55e", "🆕 NEW"),
            "exited": ("#ef4444", "🚪 EXIT"),
            "large_increase": ("#3b82f6", "📈 UP"),
            "large_decrease": ("#f59e0b", "📉 DOWN"),
        }
        color, label = type_colors.get(c.change_type, ("#6b7280", c.change_type))
        pct = f"{c.change_pct:+.1%}" if c.change_pct is not None else "—"

        rows += f"""
        <tr style="border-bottom: 1px solid #e5e7eb;">
            <td style="padding: 10px; font-weight: 600;">{c.fund_name}</td>
            <td style="padding: 10px;">{c.issuer_name}</td>
            <td style="padding: 10px;">{c.ticker}</td>
            <td style="padding: 10px; color: {color}; font-weight: 600;">{label}</td>
            <td style="padding: 10px; text-align: right;">${c.current_value:,.0f}</td>
            <td style="padding: 10px; text-align: right;">{pct}</td>
            <td style="padding: 10px;">
                <a href="{c.filing_url}" style="color: #3b82f6;">View</a></td>
        </tr>"""

    html = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                 max-width: 900px; margin: 0 auto; padding: 20px; color: #1f2937;">
        <h2 style="color: #111827; border-bottom: 2px solid #8b5cf6; padding-bottom: 10px;">
            🏦 13F Holdings Changes — {today}
        </h2>
        <p style="color: #6b7280;">{len(changes)} significant changes detected</p>
        
        <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
            <thead>
                <tr style="background-color: #f9fafb; border-bottom: 2px solid #e5e7eb;">
                    <th style="padding: 10px; text-align: left;">Fund</th>
                    <th style="padding: 10px; text-align: left;">Issuer</th>
                    <th style="padding: 10px; text-align: left;">Class</th>
                    <th style="padding: 10px; text-align: left;">Change</th>
                    <th style="padding: 10px; text-align: right;">Value</th>
                    <th style="padding: 10px; text-align: right;">% Change</th>
                    <th style="padding: 10px;">Filing</th>
                </tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>
        
        <p style="color: #9ca3af; font-size: 12px; margin-top: 30px; border-top: 1px solid #e5e7eb; padding-top: 10px;">
            SEC Filing Monitor • Data from EDGAR •
            <a href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=13F-HR" style="color: #9ca3af;">
                Browse all 13F filings
            </a>
        </p>
    </body>
    </html>"""

    return subject, html
