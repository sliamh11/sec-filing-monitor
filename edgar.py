"""
SEC EDGAR API client — handles Form 4 and 13F-HR fetching and parsing.

Uses the EDGAR full-text search (EFTS) API for discovery and
fetches individual filings for detailed parsing.

API docs: https://efts.sec.gov/LATEST/search-index
Rate limit: max 10 requests/second (we use configurable delay).
"""

import time
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ── Data Models ──────────────────────────────────────────────

@dataclass
class InsiderTrade:
    """Parsed Form 4 transaction."""
    filing_url: str
    filing_date: str
    insider_name: str
    insider_title: str
    is_director: bool
    is_officer: bool
    company_name: str
    ticker: str
    transaction_type: str  # "P" = Purchase, "S" = Sale, "A" = Grant, etc.
    acquired_or_disposed: str  # "A" = Acquired, "D" = Disposed
    shares: float
    price_per_share: float
    total_value: float

    @property
    def is_purchase(self) -> bool:
        return self.acquired_or_disposed == "A" and self.transaction_type in ("P",)

    @property
    def summary(self) -> str:
        action = "BOUGHT" if self.acquired_or_disposed == "A" else "SOLD"
        return (
            f"{self.insider_name} ({self.insider_title}) {action} "
            f"${self.total_value:,.0f} of {self.ticker} ({self.company_name}) — "
            f"{self.shares:,.0f} shares @ ${self.price_per_share:,.2f}"
        )


@dataclass
class HoldingChange:
    """A detected change in 13F holdings."""
    fund_name: str
    fund_cik: str
    filing_date: str
    filing_url: str
    issuer_name: str
    ticker: str
    change_type: str  # "new_position", "large_increase", "large_decrease", "exited"
    current_value: float
    previous_value: float
    change_pct: Optional[float]

    @property
    def summary(self) -> str:
        if self.change_type == "new_position":
            return (
                f"🆕 {self.fund_name} opened NEW position in {self.issuer_name} "
                f"({self.ticker}) — ${self.current_value:,.0f}"
            )
        elif self.change_type == "exited":
            return (
                f"🚪 {self.fund_name} EXITED position in {self.issuer_name} "
                f"({self.ticker}) — was ${self.previous_value:,.0f}"
            )
        else:
            direction = "INCREASED" if self.change_type == "large_increase" else "DECREASED"
            return (
                f"📊 {self.fund_name} {direction} {self.issuer_name} ({self.ticker}) "
                f"by {self.change_pct:+.1%} — now ${self.current_value:,.0f}"
            )


# ── EDGAR API Client ────────────────────────────────────────

class EdgarClient:
    """Handles all communication with SEC EDGAR."""

    EFTS_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
    ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"
    SUBMISSIONS_URL = "https://data.sec.gov/submissions"

    def __init__(self, user_agent: str, rate_limit_delay: float = 0.15):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept": "application/json, application/xml, text/xml, */*",
        })
        self.delay = rate_limit_delay

    def _get(self, url: str, params: dict = None) -> requests.Response:
        """Rate-limited GET request."""
        time.sleep(self.delay)
        resp = self.session.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp

    # ── Form 4 ───────────────────────────────────────────────

    def search_form4_filings(self, start_date: str, end_date: str, limit: int = 100) -> list[dict]:
        """
        Search EDGAR for recent Form 4 filings within a date range.
        Returns list of filing metadata dicts.
        """
        params = {
            "q": "",
            "forms": "4",
            "dateRange": "custom",
            "startdt": start_date,
            "enddt": end_date,
            "from": 0,
            "size": min(limit, 100),
        }
        logger.info(f"Searching Form 4 filings: {start_date} to {end_date}")
        try:
            resp = self._get(self.EFTS_SEARCH_URL, params=params)
            data = resp.json()
            hits = data.get("hits", {}).get("hits", [])
            logger.info(f"Found {len(hits)} Form 4 filings")
            return hits
        except Exception as e:
            logger.error(f"Form 4 search failed: {e}")
            return []

    def fetch_form4_xml(self, filing_url: str) -> Optional[str]:
        """Fetch the XML content of a specific Form 4 filing."""
        try:
            # EDGAR filing URLs typically point to an index page.
            # We need to find the actual XML document.
            if not filing_url.startswith("http"):
                filing_url = f"https://www.sec.gov{filing_url}"

            # If URL is an index page, fetch it and find the XML link
            resp = self._get(filing_url)
            content = resp.text

            # If it's already XML, return it
            if content.strip().startswith("<?xml") or "<ownershipDocument" in content:
                return content

            # Otherwise, try to find the XML file in the index page
            # EDGAR index pages list files — look for the primary XML doc
            import re
            # Look for links to .xml files in the filing
            xml_links = re.findall(r'href="([^"]*\.xml)"', content, re.IGNORECASE)
            for link in xml_links:
                if "primary_doc" in link.lower() or link.endswith(".xml"):
                    if link.startswith("/"):
                        link = f"https://www.sec.gov{link}"
                    elif not link.startswith("http"):
                        base = filing_url.rsplit("/", 1)[0]
                        link = f"{base}/{link}"
                    xml_resp = self._get(link)
                    if "<ownershipDocument" in xml_resp.text:
                        return xml_resp.text

            logger.warning(f"No Form 4 XML found at {filing_url}")
            return None
        except Exception as e:
            logger.error(f"Failed to fetch Form 4 XML from {filing_url}: {e}")
            return None

    def parse_form4_xml(self, xml_content: str, filing_url: str, filing_date: str) -> list[InsiderTrade]:
        """Parse a Form 4 XML document into InsiderTrade objects."""
        trades = []
        try:
            root = ET.fromstring(xml_content)

            # Extract issuer info
            issuer = root.find(".//issuer")
            company_name = _text(issuer, "issuerName", "Unknown")
            ticker = _text(issuer, "issuerTradingSymbol", "???").upper()

            # Extract reporting owner info
            owner = root.find(".//reportingOwner")
            owner_id = owner.find("reportingOwnerId") if owner is not None else None
            insider_name = _text(owner_id, "rptOwnerName", "Unknown") if owner_id is not None else "Unknown"

            relationship = owner.find("reportingOwnerRelationship") if owner is not None else None
            is_director = _text(relationship, "isDirector", "0") == "1" if relationship is not None else False
            is_officer = _text(relationship, "isOfficer", "0") == "1" if relationship is not None else False
            officer_title = _text(relationship, "officerTitle", "") if relationship is not None else ""

            # Determine display title
            if officer_title:
                insider_title = officer_title
            elif is_director:
                insider_title = "Director"
            else:
                insider_title = "Other"

            # Parse non-derivative transactions
            for txn in root.findall(".//nonDerivativeTransaction"):
                coding = txn.find("transactionCoding")
                txn_code = _text(coding, "transactionCode", "") if coding is not None else ""

                amounts = txn.find("transactionAmounts")
                if amounts is None:
                    continue

                shares_elem = amounts.find("transactionShares/value")
                price_elem = amounts.find("transactionPricePerShare/value")
                acq_disp_elem = amounts.find("transactionAcquiredDisposedCode/value")

                shares = _float(shares_elem)
                price = _float(price_elem)
                acq_disp = acq_disp_elem.text.strip() if acq_disp_elem is not None and acq_disp_elem.text else "A"
                total_value = shares * price

                if shares > 0:
                    trades.append(InsiderTrade(
                        filing_url=filing_url,
                        filing_date=filing_date,
                        insider_name=insider_name,
                        insider_title=insider_title,
                        is_director=is_director,
                        is_officer=is_officer,
                        company_name=company_name,
                        ticker=ticker,
                        transaction_type=txn_code,
                        acquired_or_disposed=acq_disp,
                        shares=shares,
                        price_per_share=price,
                        total_value=total_value,
                    ))

        except ET.ParseError as e:
            logger.error(f"XML parse error: {e}")
        except Exception as e:
            logger.error(f"Error parsing Form 4: {e}")

        return trades

    # ── 13F ──────────────────────────────────────────────────

    def get_fund_latest_13f(self, cik: str) -> Optional[dict]:
        """
        Get the latest 13F-HR filing for a specific fund CIK.
        Returns filing metadata or None.
        """
        padded_cik = cik.lstrip("0").zfill(10)
        url = f"{self.SUBMISSIONS_URL}/CIK{padded_cik}.json"
        try:
            resp = self._get(url)
            data = resp.json()
            fund_name = data.get("name", f"CIK {cik}")

            recent = data.get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            accessions = recent.get("accessionNumber", [])
            dates = recent.get("filingDate", [])
            primary_docs = recent.get("primaryDocument", [])

            for i, form in enumerate(forms):
                if form in ("13F-HR", "13F-HR/A"):
                    return {
                        "fund_name": fund_name,
                        "cik": cik,
                        "accession": accessions[i],
                        "filing_date": dates[i],
                        "primary_doc": primary_docs[i] if i < len(primary_docs) else "",
                        "form_type": form,
                    }
            logger.info(f"No 13F-HR found for CIK {cik}")
            return None
        except Exception as e:
            logger.error(f"Failed to get 13F for CIK {cik}: {e}")
            return None

    def search_recent_13f_filings(self, start_date: str, end_date: str, limit: int = 50) -> list[dict]:
        """Search for recent 13F-HR filings in a date range."""
        params = {
            "q": "",
            "forms": "13F-HR",
            "dateRange": "custom",
            "startdt": start_date,
            "enddt": end_date,
            "from": 0,
            "size": min(limit, 100),
        }
        logger.info(f"Searching 13F filings: {start_date} to {end_date}")
        try:
            resp = self._get(self.EFTS_SEARCH_URL, params=params)
            data = resp.json()
            hits = data.get("hits", {}).get("hits", [])
            logger.info(f"Found {len(hits)} 13F filings")
            return hits
        except Exception as e:
            logger.error(f"13F search failed: {e}")
            return []

    def fetch_13f_holdings(self, accession: str, cik: str) -> list[dict]:
        """
        Fetch and parse 13F holdings from the information table XML.
        Returns list of holding dicts: {issuer, ticker, shares, value, ...}
        """
        clean_cik = cik.lstrip("0")
        clean_accession = accession.replace("-", "")

        # 13F holdings are in the "infotable.xml" or similar file
        # First, get the filing index to find the info table
        index_url = f"{self.ARCHIVES_BASE}/{clean_cik}/{clean_accession}/"
        try:
            resp = self._get(index_url)
            content = resp.text

            # Find the information table XML file
            import re
            xml_files = re.findall(r'href="([^"]*(?:infotable|information_table|13f)[^"]*\.xml)"',
                                   content, re.IGNORECASE)

            if not xml_files:
                # Try looking for any XML file that might be the info table
                xml_files = re.findall(r'href="([^"]*\.xml)"', content, re.IGNORECASE)
                xml_files = [f for f in xml_files if "primary" not in f.lower()]

            holdings = []
            for xml_file in xml_files:
                if xml_file.startswith("/"):
                    full_url = f"https://www.sec.gov{xml_file}"
                else:
                    full_url = f"{self.ARCHIVES_BASE}/{clean_cik}/{clean_accession}/{xml_file}"
                try:
                    xml_resp = self._get(full_url)
                    parsed = self._parse_13f_info_table(xml_resp.text)
                    if parsed:
                        holdings = parsed
                        break
                except Exception:
                    continue

            logger.info(f"Parsed {len(holdings)} holdings from 13F")
            return holdings

        except Exception as e:
            logger.error(f"Failed to fetch 13F holdings: {e}")
            return []

    def _parse_13f_info_table(self, xml_content: str) -> list[dict]:
        """Parse 13F information table XML into holding dicts."""
        holdings = []
        try:
            # 13F XML uses namespaces — handle both with and without
            # Remove namespace prefixes for easier parsing
            import re
            clean_xml = re.sub(r'xmlns[^"]*"[^"]*"', '', xml_content)
            clean_xml = re.sub(r'<(/?)(\w+):', r'<\1', clean_xml)

            root = ET.fromstring(clean_xml)

            # Find all infoTable entries
            for entry in root.iter():
                if "infotable" in entry.tag.lower():
                    issuer = _text(entry, "nameOfIssuer", "") or _text(entry, "issuer", "")
                    ticker = _text(entry, "titleOfClass", "")
                    cusip = _text(entry, "cusip", "")

                    value_elem = entry.find("value")
                    value = int(value_elem.text.strip()) * 1000 if value_elem is not None and value_elem.text else 0

                    shares_elem = entry.find(".//sshPrnamt") or entry.find(".//amount")
                    shares = int(shares_elem.text.strip()) if shares_elem is not None and shares_elem.text else 0

                    if issuer:
                        holdings.append({
                            "issuer": issuer,
                            "ticker": ticker,
                            "cusip": cusip,
                            "value": value,
                            "shares": shares,
                        })

        except ET.ParseError:
            logger.warning("Failed to parse 13F XML as standard XML, trying alternative parsing")
        except Exception as e:
            logger.warning(f"13F parse error: {e}")

        return holdings


# ── Helpers ──────────────────────────────────────────────────

def _text(parent, tag: str, default: str = "") -> str:
    """Safely extract text from an XML element."""
    if parent is None:
        return default
    elem = parent.find(tag)
    if elem is not None and elem.text:
        return elem.text.strip()
    return default


def _float(elem) -> float:
    """Safely extract float from an XML element."""
    if elem is not None and elem.text:
        try:
            return float(elem.text.strip())
        except ValueError:
            return 0.0
    return 0.0
