"""
SEC EDGAR API client — handles Form 4, 13F-HR, Form 8-K, and Schedule 13D/13G
fetching and parsing.

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


@dataclass
class Form8K:
    """Parsed Form 8-K material event filing."""
    filing_url: str
    filing_date: str
    company_name: str
    ticker: str
    cik: str
    item_codes: list  # e.g. ["1.01", "5.02"]
    event_description: str

    @property
    def summary(self) -> str:
        items = ", ".join(self.item_codes)
        return (
            f"{self.company_name} ({self.ticker}) — Items: {items} — "
            f"{self.event_description[:100]}"
        )


@dataclass
class Schedule13DG:
    """Parsed Schedule 13D or 13G filing (5%+ ownership crossing)."""
    filing_url: str
    filing_date: str
    filer_name: str
    target_company: str
    target_ticker: str
    target_cik: str
    form_type: str  # "SC 13D" or "SC 13G"
    shares_pct: float
    intent: str

    @property
    def summary(self) -> str:
        return (
            f"{self.form_type}: {self.filer_name} owns {self.shares_pct:.1f}% of "
            f"{self.target_company} ({self.target_ticker}) — {self.intent[:80]}"
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

    # ── Form 8-K ─────────────────────────────────────────────

    def search_form8k_filings(self, start_date: str, end_date: str, limit: int = 100) -> list[dict]:
        """Search EDGAR for Form 8-K filings within a date range."""
        params = {
            "q": "",
            "forms": "8-K",
            "dateRange": "custom",
            "startdt": start_date,
            "enddt": end_date,
            "from": 0,
            "size": min(limit, 100),
        }
        logger.info(f"Searching Form 8-K filings: {start_date} to {end_date}")
        try:
            resp = self._get(self.EFTS_SEARCH_URL, params=params)
            data = resp.json()
            hits = data.get("hits", {}).get("hits", [])
            logger.info(f"Found {len(hits)} Form 8-K filings")
            return hits
        except Exception as e:
            logger.error(f"Form 8-K search failed: {e}")
            return []

    def fetch_8k_content(self, filing_url: str, company_name: str, ticker: str,
                         cik: str, filing_date: str) -> Optional[Form8K]:
        """
        Fetch Form 8-K filing index, find the primary HTML document, and
        extract item codes (e.g. ["1.01", "5.02"]) via regex.
        Returns a Form8K or None if no items found.
        """
        import re
        try:
            if not filing_url.startswith("http"):
                filing_url = f"https://www.sec.gov{filing_url}"

            resp = self._get(filing_url)
            index_content = resp.text

            # Find .htm links from the EDGAR index page
            htm_links = re.findall(r'href="(/Archives/edgar/data/[^"]*\.htm[l]?)"',
                                   index_content, re.IGNORECASE)

            doc_content = ""
            primary_url = filing_url

            for link in htm_links[:5]:
                full_url = f"https://www.sec.gov{link}"
                try:
                    doc_resp = self._get(full_url)
                    if re.search(r'[Ii]tem\s+\d+\.\d+', doc_resp.text):
                        doc_content = doc_resp.text
                        primary_url = full_url
                        break
                except Exception:
                    continue

            if not doc_content:
                logger.debug(f"No 8-K item content found at {filing_url}")
                return None

            # Extract item codes (deduplicated, order-preserving)
            raw_codes = re.findall(r'[Ii]tem\s+(\d+\.\d+)', doc_content)
            seen_codes: set = set()
            item_codes = []
            for code in raw_codes:
                if code not in seen_codes:
                    seen_codes.add(code)
                    item_codes.append(code)

            if not item_codes:
                return None

            # Strip HTML and grab text after the first item header for description
            plain_text = re.sub(r'<[^>]+>', ' ', doc_content)
            plain_text = re.sub(r'\s+', ' ', plain_text)
            desc_match = re.search(r'[Ii]tem\s+\d+\.\d+[^\w]*(.{20,300})', plain_text)
            event_description = desc_match.group(1).strip()[:200] if desc_match else ""

            return Form8K(
                filing_url=primary_url,
                filing_date=filing_date,
                company_name=company_name,
                ticker=ticker,
                cik=cik,
                item_codes=item_codes,
                event_description=event_description,
            )
        except Exception as e:
            logger.error(f"Failed to fetch 8-K content from {filing_url}: {e}")
            return None

    # ── Schedule 13D / 13G ───────────────────────────────────

    def search_13dg_filings(self, start_date: str, end_date: str, limit: int = 50) -> list[dict]:
        """Search EDGAR for Schedule 13D and 13G filings within a date range."""
        params = {
            "q": "",
            "forms": "SC 13D,SC 13G",
            "dateRange": "custom",
            "startdt": start_date,
            "enddt": end_date,
            "from": 0,
            "size": min(limit, 100),
        }
        logger.info(f"Searching Schedule 13D/13G filings: {start_date} to {end_date}")
        try:
            resp = self._get(self.EFTS_SEARCH_URL, params=params)
            data = resp.json()
            hits = data.get("hits", {}).get("hits", [])
            logger.info(f"Found {len(hits)} Schedule 13D/13G filings")
            return hits
        except Exception as e:
            logger.error(f"Schedule 13D/13G search failed: {e}")
            return []

    def fetch_13dg_content(self, filing_url: str, accession: str, cik: str,
                           form_type: str, filing_date: str) -> Optional[Schedule13DG]:
        """
        Fetch Schedule 13D/13G filing index, find the primary document, and
        extract filer name, target company, ownership percentage, and intent.
        Tries XML parsing first, falls back to regex on plain text/HTML.
        Returns a Schedule13DG or None on failure.
        """
        import re
        try:
            clean_cik = cik.lstrip("0")
            clean_accession = accession.replace("-", "")
            index_url = f"{self.ARCHIVES_BASE}/{clean_cik}/{clean_accession}/"

            resp = self._get(index_url)
            index_content = resp.text

            # Find candidate documents (xml, htm, txt)
            doc_links = re.findall(
                r'href="(/Archives/edgar/data/[^"]*\.(?:xml|htm[l]?|txt))"',
                index_content, re.IGNORECASE
            )

            doc_content = ""
            for link in doc_links[:6]:
                full_url = f"https://www.sec.gov{link}"
                try:
                    doc_resp = self._get(full_url)
                    text = doc_resp.text
                    # Look for 13D/G-specific keywords
                    if any(kw in text for kw in [
                        "nameOfIssuer", "NAME OF ISSUER", "PERCENT OF CLASS",
                        "percentOfClass", "PURPOSE OF TRANSACTION",
                    ]):
                        doc_content = text
                        break
                except Exception:
                    continue

            if not doc_content:
                logger.debug(f"No 13D/G cover page content found at {index_url}")
                return None

            filer_name = ""
            target_company = ""
            target_ticker = ""
            shares_pct = 0.0
            intent = ""

            # Try XML parsing
            try:
                clean_xml = re.sub(r'xmlns[^"]*"[^"]*"', '', doc_content)
                clean_xml = re.sub(r'<(/?)(\w+):', r'<\1', clean_xml)
                root = ET.fromstring(clean_xml)
                target_company = (
                    _text(root, ".//nameOfIssuer") or
                    _text(root, ".//issuerName") or ""
                )
                filer_name = (
                    _text(root, ".//nameOfFiler") or
                    _text(root, ".//reportingPersonName") or ""
                )
                pct_raw = (
                    _text(root, ".//percentOfClass") or
                    _text(root, ".//percentageOfClass") or ""
                )
                if pct_raw:
                    m = re.search(r'[\d.]+', pct_raw)
                    if m:
                        shares_pct = float(m.group())
                intent = (
                    _text(root, ".//purposeOfTransaction") or
                    _text(root, ".//purpose") or ""
                )
            except Exception:
                pass

            # Regex fallbacks for fields not found via XML
            plain = re.sub(r'<[^>]+>', ' ', doc_content)
            plain = re.sub(r'\s+', ' ', plain)

            if not target_company:
                m = re.search(
                    r'(?:NAME OF ISSUER|nameOfIssuer)\s*[:\-]?\s*([^\n<]{2,80})',
                    doc_content, re.IGNORECASE
                )
                target_company = m.group(1).strip() if m else ""

            if not filer_name:
                m = re.search(
                    r'(?:NAME OF REPORTING PERSON|nameOfFiler)\s*[:\-]?\s*([^\n<]{2,80})',
                    doc_content, re.IGNORECASE
                )
                filer_name = m.group(1).strip() if m else "Unknown"

            if not shares_pct:
                m = re.search(
                    r'(?:PERCENT OF CLASS|percentOfClass|percentageOfClass)[^:\d]*([0-9]+\.?[0-9]*)\s*%?',
                    doc_content, re.IGNORECASE
                )
                if m:
                    try:
                        shares_pct = float(m.group(1))
                    except ValueError:
                        pass

            if not intent:
                m = re.search(
                    r'(?:PURPOSE OF TRANSACTION|purposeOfTransaction)\s*[:\-]?\s*(.{10,400}?)(?:\n\n|\Z)',
                    plain, re.IGNORECASE | re.DOTALL
                )
                if m:
                    intent = m.group(1).strip()[:200]

            return Schedule13DG(
                filing_url=filing_url,
                filing_date=filing_date,
                filer_name=filer_name or "Unknown",
                target_company=target_company or "Unknown",
                target_ticker=target_ticker,
                target_cik=cik,
                form_type=form_type,
                shares_pct=shares_pct,
                intent=intent,
            )
        except Exception as e:
            logger.error(f"Failed to fetch 13D/G content from {filing_url}: {e}")
            return None

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
