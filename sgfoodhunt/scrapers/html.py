"""Shared HTML parsing helpers for static pages and listicle articles."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, fields
from datetime import datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from sgfoodhunt.models import extract_phone, extract_postal_code

ADDRESS_RE = re.compile(
    r"(?:Address\s*[:：]\s*)?((?:\d{1,4}[A-Z]?\s+)?[#\w][\w\s,'.\-#/()]{4,120}?Singapore\s*\d{6})",
    re.IGNORECASE,
)
HOURS_RE = re.compile(r"(?:Opening\s+Hours?|Hours|Open)\s*[:：]\s*(.+)", re.IGNORECASE)
HOURS_STOP_RE = re.compile(
    r"\s*(?:\||Tel\b|Phone\b|Contact\b|Website\b|Address\b|Google Maps)", re.IGNORECASE
)
PRICE_RE = re.compile(r"(\${1,4})(?![\d\w])")
LEADING_NUMBER_RE = re.compile(r"^\s*(?:#?\d{1,3}[.):\-–—]?\s*)")
TRAILING_SEP_RE = re.compile(r"\s*[|–—-]\s*(?:best|top|the|a|an|for|our|where)\b.*$", re.IGNORECASE)
CJK_RE = re.compile(r"[一-鿿]{2,}")
PAREN_ZH_RE = re.compile(r"[(（]\s*([一-鿿][一-鿿\s]*)\s*[)）]")

SKIP_HEADINGS = {
    "conclusion",
    "final thoughts",
    "summary",
    "faq",
    "faqs",
    "related articles",
    "read more",
    "share this",
    "comments",
    "also read",
    "table of contents",
    "contents",
    "overview",
    "verdict",
    "the verdict",
    "photos",
    "location",
    "how to get there",
    "about the author",
}


def soup_of(html: str | bytes) -> BeautifulSoup:
    """Parse HTML. Bytes are decoded as UTF-8 when valid; otherwise bs4 sniffs the encoding."""
    if isinstance(html, bytes):
        try:
            html = html.decode("utf-8")
        except UnicodeDecodeError:
            return BeautifulSoup(html, "lxml")
    return BeautifulSoup(html, "lxml")


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def absolute_links(soup: BeautifulSoup | Tag, base_url: str, pattern: re.Pattern[str]) -> list[str]:
    """Unique absolute hrefs whose path matches ``pattern``, in document order."""
    seen: set[str] = set()
    out: list[str] = []
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, str(a["href"])).split("#")[0]
        if href in seen:
            continue
        if pattern.search(urlsplit(href).path):
            seen.add(href)
            out.append(href)
    return out


def json_ld_blocks(soup: BeautifulSoup | Tag) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "")
        except (ValueError, TypeError):
            continue
        if isinstance(data, list):
            blocks.extend(d for d in data if isinstance(d, dict))
        elif isinstance(data, dict):
            graph = data.get("@graph")
            if isinstance(graph, list):
                blocks.extend(d for d in graph if isinstance(d, dict))
            blocks.append(data)
    return blocks


def article_published_at(soup: BeautifulSoup) -> str | None:
    """ISO date from JSON-LD, meta tags or a <time> element, if present."""
    for block in json_ld_blocks(soup):
        for key in ("datePublished", "dateCreated", "dateModified"):
            value = block.get(key)
            if isinstance(value, str) and value:
                return _normalise_date(value)
    for attrs in (
        {"property": "article:published_time"},
        {"name": "article:published_time"},
        {"property": "og:updated_time"},
        {"name": "date"},
        {"itemprop": "datePublished"},
    ):
        meta = soup.find("meta", attrs=attrs)
        if isinstance(meta, Tag) and meta.get("content"):
            return _normalise_date(str(meta["content"]))
    time_tag = soup.find("time", attrs={"datetime": True})
    if isinstance(time_tag, Tag):
        return _normalise_date(str(time_tag["datetime"]))
    return None


def _normalise_date(value: str) -> str | None:
    value = value.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d %B %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(value[:25] if "T" in value else value, fmt).date().isoformat()
        except ValueError:
            continue
    match = re.match(r"(\d{4}-\d{2}-\d{2})", value)
    return match.group(1) if match else None


def page_title(soup: BeautifulSoup) -> str | None:
    og = soup.find("meta", attrs={"property": "og:title"})
    if isinstance(og, Tag) and og.get("content"):
        return str(og["content"]).strip()
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(" ", strip=True)
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    return None


def clean_venue_heading(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = LEADING_NUMBER_RE.sub("", text)
    text = TRAILING_SEP_RE.sub("", text)
    text = re.sub(r"\s*[\[(]\s*(?:closed|permanently closed|new)\s*[\])]\s*$", "", text, flags=re.I)
    return text.strip(" -–—:|")


def split_chinese_name(name: str) -> tuple[str, str | None]:
    """Split "Keng Eng Kee (琼荣记)" into ("Keng Eng Kee", "琼荣记")."""
    match = PAREN_ZH_RE.search(name)
    if match:
        zh = match.group(1).strip()
        return name[: match.start()].strip() + name[match.end() :].strip(), zh
    cjk = CJK_RE.search(name)
    if cjk and not name.strip().startswith(cjk.group(0)):
        return name[: cjk.start()].strip(" -–|"), cjk.group(0)
    return name, None


@dataclass(slots=True)
class ListicleEntry:
    name: str
    text: str
    links: list[str] = field(default_factory=list)
    address: str | None = None
    postal_code: str | None = None
    phone: str | None = None
    hours: str | None = None
    price_text: str | None = None
    name_zh: str | None = None

    def snippet(self, limit: int = 300) -> str:
        return self.text[:limit]


def _block_text(node: Tag) -> str:
    return node.get_text(" ", strip=True)


def extract_listicle_entries(
    article: Tag,
    heading_tags: tuple[str, ...] = ("h2", "h3"),
    min_text_chars: int = 20,
) -> list[ListicleEntry]:
    """Split an article into (heading, following text) entries and pull out venue details.

    Works for the typical Singapore food listicle: one h2/h3 per venue followed by paragraphs
    that include an address line with a six digit postal code.
    """
    entries: list[ListicleEntry] = []
    headings = [h for h in article.find_all(list(heading_tags)) if _block_text(h)]
    for heading in headings:
        raw = _block_text(heading)
        info = ""
        if ":" in raw and extract_postal_code(raw):
            # single-venue review info line: "Name: address, Singapore 650643 | Tel | Hours"
            raw, info = (part.strip() for part in raw.split(":", 1))
        if raw.lower().strip(" :") in SKIP_HEADINGS or len(raw) > 140:
            continue
        chunks: list[str] = []
        links: list[str] = []
        for sib in heading.next_siblings:
            if isinstance(sib, Tag):
                if sib.name in heading_tags:
                    break
                nested = sib.find(list(heading_tags))
                if nested is not None:
                    break
                chunks.append(_block_text(sib))
                links.extend(str(a["href"]) for a in sib.find_all("a", href=True))
        text = re.sub(r"\s+", " ", " ".join(c for c in [info, *chunks] if c)).strip()
        if len(text) < min_text_chars:
            continue
        name, name_zh = split_chinese_name(clean_venue_heading(raw))
        if not name:
            continue
        addr_match = ADDRESS_RE.search(text)
        address = addr_match.group(1).strip() if addr_match else None
        hours_match = HOURS_RE.search(text)
        hours = None
        if hours_match:
            hours = HOURS_STOP_RE.split(hours_match.group(1), maxsplit=1)[0][:120].strip(" |")
        price_match = PRICE_RE.search(text)
        entries.append(
            ListicleEntry(
                name=name,
                text=text,
                links=links,
                address=address,
                postal_code=extract_postal_code(address or text),
                phone=extract_phone(text),
                hours=hours or None,
                price_text=price_match.group(1) if price_match else None,
                name_zh=name_zh,
            )
        )
    return entries


def looks_like_venue_entry(entry: ListicleEntry) -> bool:
    """Heuristic: an entry is a venue if it has an address, phone, hours, or a maps link."""
    if entry.postal_code or entry.phone or entry.hours:
        return True
    return any("maps" in link or "goo.gl" in link for link in entry.links)


# ---------------------------------------------------------------------------------------------
# Result "cards" on search pages (booking platforms, aggregators, review sites)
# ---------------------------------------------------------------------------------------------
@dataclass(slots=True)
class CardSelectors:
    card: str
    name: str
    link: str | None = None
    address: str | None = None
    rating: str | None = None
    review_count: str | None = None
    price: str | None = None
    cuisine: str | None = None

    @classmethod
    def from_options(cls, options: dict[str, Any], defaults: CardSelectors) -> CardSelectors:
        raw = options.get("selectors") or {}
        base = {f.name: getattr(defaults, f.name) for f in fields(defaults)}
        return cls(**{**base, **{k: v for k, v in raw.items() if k in base}})


@dataclass(slots=True)
class Card:
    name: str
    url: str | None = None
    address: str | None = None
    rating: float | None = None
    review_count: int | None = None
    price_text: str | None = None
    cuisine: list[str] = field(default_factory=list)
    text: str = ""


def _first_text(node: Tag, selector: str | None) -> str | None:
    if not selector:
        return None
    found = node.select_one(selector)
    return found.get_text(" ", strip=True) if found else None


def parse_float(text: str | None) -> float | None:
    if not text:
        return None
    match = re.search(r"\d+(?:\.\d+)?", text.replace(",", ""))
    return float(match.group(0)) if match else None


def parse_int(text: str | None) -> int | None:
    if not text:
        return None
    match = re.search(r"\d[\d,]*", text)
    return int(match.group(0).replace(",", "")) if match else None


def parse_cards(soup: BeautifulSoup | Tag, base_url: str, sel: CardSelectors) -> list[Card]:
    cards: list[Card] = []
    for node in soup.select(sel.card):
        name = _first_text(node, sel.name)
        if not name:
            continue
        link_node = node.select_one(sel.link) if sel.link else node.find("a", href=True)
        url = (
            urljoin(base_url, str(link_node["href"]))
            if isinstance(link_node, Tag) and link_node.get("href")
            else None
        )
        cuisine_text = _first_text(node, sel.cuisine)
        cards.append(
            Card(
                name=clean_venue_heading(name),
                url=url,
                address=_first_text(node, sel.address),
                rating=parse_float(_first_text(node, sel.rating)),
                review_count=parse_int(_first_text(node, sel.review_count)),
                price_text=_first_text(node, sel.price),
                cuisine=[c.strip() for c in re.split(r"[,/·•|]", cuisine_text) if c.strip()]
                if cuisine_text
                else [],
                text=node.get_text(" ", strip=True),
            )
        )
    return cards


def parse_json_ld_restaurants(soup: BeautifulSoup | Tag, base_url: str) -> list[Card]:
    """Restaurant / FoodEstablishment / LocalBusiness entries from JSON-LD, including ItemLists."""
    found: list[Card] = []

    def visit(block: dict[str, Any]) -> None:
        kind = block.get("@type")
        kinds = {kind} if isinstance(kind, str) else set(kind or [])
        if kinds & {
            "Restaurant",
            "FoodEstablishment",
            "LocalBusiness",
            "CafeOrCoffeeShop",
            "BarOrPub",
        }:
            name = block.get("name")
            if not isinstance(name, str) or not name.strip():
                return
            address = block.get("address")
            addr_text: str | None = None
            if isinstance(address, dict):
                parts = [
                    address.get("streetAddress"),
                    address.get("addressLocality"),
                    address.get("postalCode"),
                ]
                addr_text = ", ".join(str(p) for p in parts if p)
            elif isinstance(address, str):
                addr_text = address
            agg = block.get("aggregateRating") or {}
            cuisine = block.get("servesCuisine")
            found.append(
                Card(
                    name=name.strip(),
                    url=urljoin(base_url, str(block["url"])) if block.get("url") else None,
                    address=addr_text,
                    rating=parse_float(str(agg.get("ratingValue")))
                    if isinstance(agg, dict) and agg.get("ratingValue") is not None
                    else None,
                    review_count=parse_int(str(agg.get("reviewCount") or agg.get("ratingCount")))
                    if isinstance(agg, dict)
                    else None,
                    price_text=str(block["priceRange"]) if block.get("priceRange") else None,
                    cuisine=[cuisine]
                    if isinstance(cuisine, str)
                    else [str(c) for c in cuisine or []],
                    text=str(block.get("description") or ""),
                )
            )
        for item in block.get("itemListElement") or []:
            if isinstance(item, dict):
                inner = item.get("item") if isinstance(item.get("item"), dict) else item
                if isinstance(inner, dict):
                    visit(inner)

    for block in json_ld_blocks(soup):
        visit(block)
    return found
