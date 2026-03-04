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


def build_form4_email(trades: list) -> tuple[str, str]:
    """
    Build subject and HTML body for Form 4 insider trade alerts.
    Returns (subject, html_body).
    """
    today = date.today().isoformat()
    subject = f"🔔 SEC Insider Trades Alert — {len(trades)} trades ({today})"

    rows = ""
    for t in trades:
        action_color = "#22c55e" if t.acquired_or_disposed == "A" else "#ef4444"
        action_label = "BUY" if t.acquired_or_disposed == "A" else "SELL"
        rows += f"""
        <tr style="border-bottom: 1px solid #e5e7eb;">
            <td style="padding: 10px; font-weight: 600;">{t.ticker}</td>
            <td style="padding: 10px;">{t.company_name}</td>
            <td style="padding: 10px;">{t.insider_name}<br>
                <span style="color: #6b7280; font-size: 12px;">{t.insider_title}</span></td>
            <td style="padding: 10px; color: {action_color}; font-weight: 600;">{action_label}</td>
            <td style="padding: 10px; text-align: right;">${t.total_value:,.0f}</td>
            <td style="padding: 10px; text-align: right;">{t.shares:,.0f}</td>
            <td style="padding: 10px; text-align: right;">${t.price_per_share:,.2f}</td>
            <td style="padding: 10px;">
                <a href="{t.filing_url}" style="color: #3b82f6;">View</a></td>
        </tr>"""

    html = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; 
                 max-width: 900px; margin: 0 auto; padding: 20px; color: #1f2937;">
        <h2 style="color: #111827; border-bottom: 2px solid #3b82f6; padding-bottom: 10px;">
            📊 SEC Insider Trades — {today}
        </h2>
        <p style="color: #6b7280;">{len(trades)} trades matched your filters</p>
        
        <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
            <thead>
                <tr style="background-color: #f9fafb; border-bottom: 2px solid #e5e7eb;">
                    <th style="padding: 10px; text-align: left;">Ticker</th>
                    <th style="padding: 10px; text-align: left;">Company</th>
                    <th style="padding: 10px; text-align: left;">Insider</th>
                    <th style="padding: 10px; text-align: left;">Action</th>
                    <th style="padding: 10px; text-align: right;">Value</th>
                    <th style="padding: 10px; text-align: right;">Shares</th>
                    <th style="padding: 10px; text-align: right;">Price</th>
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
