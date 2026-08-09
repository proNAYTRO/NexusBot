"""Rules-based extraction of Clash of Clans base layouts from seller PDFs.

Base sellers (Atrasis, PINNACLE, RHBB, ...) ship a PDF with one base per page:
a **screenshot** of the base, a small **caption** (name / builder / recommended
clan-castle troops), and — crucially — a real **`action=OpenLayout` deep-link**
embedded as a clickable annotation. That link is the whole point: it's what lets
a player copy the layout in-game, and it's the only thing here that can't be
reconstructed from a picture.

This module pulls each base out of a PDF with PyMuPDF (`fitz`) — cog-free and
sync so it stays testable offline (`python -m utils.base_pdf <file.pdf>`), mirroring
`utils/import_clashperk.py`. `cogs/clash/bases.py` wraps it in the `/nexbasepost`
review-and-post command.

Confirmed against three real sellers (2026-07): all embed the layout links as
plaintext URI annotations, use JPEG/PNG screenshots, and carry ToUnicode text so
captions decode cleanly. Two use emoji-labeled fields (`👷Builder:` /
`💎Base Style:` / `🔰Clan Castle:`); Atrasis uses `Base:N` + `Cc:`/`Recommended CC`.
Some sellers attach the same link to BOTH the image and a button, so annotations
come ~2× the base count — deduped here by the `id=` value.
"""

from __future__ import annotations

import re
import sys
import urllib.parse
from dataclasses import dataclass, field
from typing import Optional

import fitz  # PyMuPDF

# A drawn image covering this fraction of the page is a background/template, not
# a base screenshot (PINNACLE lays every base over a full-page background).
_BG_COVERAGE = 0.80
# Images drawn smaller than this (points) on either side are logos / separators.
_MIN_IMG_SIDE = 40.0

# Lines that are plumbing, not caption content — links, socials, the URL text
# echo, and the bare id continuation token that a wrapped link leaves behind.
_SKIP_SUBSTR = (
    "http", "discord", "twitter", "youtube", "instagram", "patreon", "supr.cl",
    "base link", "@", "/ x", "t.me", "tiktok",
)

# Bare anchor words a seller draws under the base (Atrasis prints "Link"); on
# their own they're clickable-link labels, not caption text.
_SKIP_EXACT = {"link", "links", "copy base", "copy layout", "copy", "download", "click here", "open"}


@dataclass
class BaseCandidate:
    """One base pulled from a PDF, ready to review and post."""
    link: str                       # full OpenLayout URL, verbatim (the button target)
    layout_id: str                  # the id= value, for dedupe
    image_bytes: Optional[bytes]    # base screenshot bytes (None if unrecoverable)
    image_ext: str = "png"          # 'png' / 'jpeg'
    name: str = ""
    author: str = ""
    cc: str = ""
    raw_lines: list = field(default_factory=list)  # caption lines as extracted, for the review UI
    page: int = 0

    def composed_title(self) -> str:
        """The ClashPerk-style title line: `Name | Author | CC`, blanks dropped."""
        return " | ".join(p for p in (self.name.strip(), self.author.strip(), self.cc.strip()) if p.strip())


# ── text cleaning ────────────────────────────────────────────────────────────

def _clean(s: str) -> str:
    """Normalise a text fragment: kill zero-width / nbsp, collapse whitespace,
    trim trailing separators left over from labels."""
    s = s.replace("​", "").replace("﻿", "").replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s.strip(" :;-|·•").strip()


def _strip_lead(s: str) -> str:
    """Drop leading non-alphanumeric characters (emoji, symbols, spaces) so a
    label like `👷Builder:` matches on `Builder:`."""
    i = 0
    while i < len(s) and not s[i].isalnum():
        i += 1
    return s[i:]


def _is_skip_line(low: str) -> bool:
    if low in _SKIP_EXACT:
        return True
    if any(k in low for k in _SKIP_SUBSTR):
        return True
    # A wrapped OpenLayout link leaves a bare token like "AAAAKYo2Jvya8w-cNF6..."
    if re.fullmatch(r"[A-Za-z0-9%_\-]{15,}", low.replace(" ", "")):
        return True
    return False


# ── caption field parsing ────────────────────────────────────────────────────

# label -> field. Order matters: check CC before NAME so "Clan Castle" doesn't
# get swallowed by a looser name rule. Matched after _strip_lead, case-insensitive.
_AUTHOR_RE = re.compile(r"^(?:builder|author|built\s*by|created\s*by|creator|by)\s*[:\-]\s*(.+)$", re.I)
_STYLE_RE  = re.compile(r"^(?:base\s*style|style|name|layout)\s*[:\-]\s*(.+)$", re.I)
_CC_RE     = re.compile(r"^(?:clan\s*castle|recommended\s*cc|cc\s*troops?|cc|castle)\s*[:\-]\s*(.+)$", re.I)
# A top-of-page title line: "Base 1 - TH18", "Base:1", "Base 12".
_TITLE_RE  = re.compile(r"^base\s*[:#]?\s*\d+", re.I)


def _parse_caption(lines: list) -> tuple:
    """Slot cleaned caption lines into (name, author, cc). Labeled fields win;
    a `Base N` top-title fills the name when there's no `Base Style`. CC absorbs
    following unlabeled lines (sellers wrap the troop list across two lines)."""
    name = author = cc = ""
    style = ""          # value of a "Base Style:" label, weaker than a top title
    top_title = ""
    current = None      # which field a trailing continuation line extends ("cc" only)

    for raw in lines:
        low = raw.lower()
        if _is_skip_line(low):
            current = None
            continue
        body = _strip_lead(raw)

        m = _AUTHOR_RE.match(body)
        if m:
            author = _clean(m.group(1)); current = None; continue
        m = _CC_RE.match(body)
        if m:
            cc = _clean(m.group(1)); current = "cc"; continue
        m = _STYLE_RE.match(body)
        if m:
            style = _clean(m.group(1)); current = None; continue
        if _TITLE_RE.match(body):
            top_title = _clean(body); current = None; continue

        # Unlabeled line: treat as a continuation of a wrapping CC list.
        if current == "cc":
            extra = _clean(body)
            if extra:
                cc = f"{cc} {extra}".strip()

    name = top_title or style
    return name, author, cc


# ── image selection ──────────────────────────────────────────────────────────

def _bbox_area(b) -> float:
    return max(0.0, (b[2] - b[0])) * max(0.0, (b[3] - b[1]))


def _pick_base_image(doc, page, link_rect):
    """Return (image_bytes, ext) for the base screenshot on this page.

    The base is the dominant content image: exclude full-page backgrounds and
    small logos/separators, then take the largest by drawn area (ties broken by
    proximity to the link). Falls back to rasterising the region if the embedded
    image can't be extracted by xref."""
    page_area = _bbox_area(page.rect)
    best = None
    best_score = -1.0
    for im in page.get_image_info(xrefs=True):
        b = im["bbox"]
        w, h = b[2] - b[0], b[3] - b[1]
        if w < _MIN_IMG_SIDE or h < _MIN_IMG_SIDE:
            continue
        area = _bbox_area(b)
        if page_area and area / page_area > _BG_COVERAGE:
            continue  # background / template
        if area > best_score:
            best_score = area
            best = im
    if best is None:
        return None, "png"

    xref = best.get("xref", 0)
    if xref:
        try:
            info = doc.extract_image(xref)
            if info and info.get("image"):
                return info["image"], info.get("ext", "png")
        except Exception:
            pass
    # Fallback: rasterise the drawn region (handles inline / masked images).
    try:
        pix = page.get_pixmap(clip=fitz.Rect(best["bbox"]), dpi=200)
        return pix.tobytes("png"), "png"
    except Exception:
        return None, "png"


# ── link helpers ─────────────────────────────────────────────────────────────

def _layout_id(uri: str) -> str:
    """The `id=` query value (identifies a unique layout, so duplicate image+button
    annotations for the same base collapse to one)."""
    try:
        q = urllib.parse.urlparse(uri).query
        vals = urllib.parse.parse_qs(q)
        if "id" in vals and vals["id"]:
            return vals["id"][0]
    except Exception:
        pass
    return uri  # fall back to the whole URL


def _page_lines(page) -> list:
    """All text lines on the page as cleaned strings, in reading order."""
    out = []
    d = page.get_text("dict")
    items = []
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            txt = "".join(sp.get("text", "") for sp in line.get("spans", [])).strip()
            if txt:
                bb = line["bbox"]
                items.append((round(bb[1], 1), round(bb[0], 1), txt))
    items.sort()
    for _, _, txt in items:
        c = _clean(txt)
        if c:
            out.append(c)
    return out


# ── public API ───────────────────────────────────────────────────────────────

def extract_bases(pdf_bytes: bytes) -> list:
    """Extract every base (one per unique layout link) from a seller PDF.

    Returns a list of BaseCandidate in document order. Pages with no OpenLayout
    link (covers, indexes) are skipped. When a page holds exactly one base
    (the common case for every sample seller) all its caption text and its
    largest content image belong to that base; a rare multi-base page falls back
    to associating each link with the nearest content image."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    candidates: list = []
    seen_ids: set = set()
    try:
        for pno in range(doc.page_count):
            page = doc[pno]
            links = [
                l for l in page.get_links()
                if l.get("uri") and "openlayout" in l["uri"].lower()
            ]
            if not links:
                continue

            # Dedupe this page's links by layout id, keeping the annotation with
            # the largest rect (the image, not the small button) as the anchor.
            by_id: dict = {}
            for l in links:
                lid = _layout_id(l["uri"])
                r = l["from"]
                area = (r.x1 - r.x0) * (r.y1 - r.y0)
                if lid not in by_id or area > by_id[lid][1]:
                    by_id[lid] = (l, area)

            lines = _page_lines(page)
            for lid, (link, _area) in by_id.items():
                if lid in seen_ids:
                    continue
                seen_ids.add(lid)
                img_bytes, ext = _pick_base_image(doc, page, link["from"])
                name, author, cc = _parse_caption(lines)
                candidates.append(BaseCandidate(
                    link=link["uri"],
                    layout_id=lid,
                    image_bytes=img_bytes,
                    image_ext=ext,
                    name=name,
                    author=author,
                    cc=cc,
                    raw_lines=lines,
                    page=pno,
                ))
    finally:
        doc.close()
    return candidates


# ── offline probe ────────────────────────────────────────────────────────────

def _probe(paths: list, dump_dir: Optional[str] = None) -> None:
    import os
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    for p in paths:
        with open(p, "rb") as f:
            data = f.read()
        bases = extract_bases(data)
        print(f"\n########## {os.path.basename(p)} — {len(bases)} bases ##########")
        for i, b in enumerate(bases, 1):
            img = f"{len(b.image_bytes)}B {b.image_ext}" if b.image_bytes else "NO IMAGE"
            print(f"\n[{i}] page {b.page}  img={img}")
            print(f"    TITLE : {b.composed_title()!r}")
            print(f"    name={b.name!r}  author={b.author!r}  cc={b.cc!r}")
            print(f"    link  : {b.link[:75]}...")
            if dump_dir and b.image_bytes:
                os.makedirs(dump_dir, exist_ok=True)
                fn = os.path.join(dump_dir, f"{os.path.splitext(os.path.basename(p))[0]}_{i}.{b.image_ext}")
                with open(fn, "wb") as out:
                    out.write(b.image_bytes)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--dump=")]
    dd = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--dump=")), None)
    if not args:
        print("usage: python -m utils.base_pdf <file.pdf> [more.pdf ...] [--dump=DIR]")
        sys.exit(1)
    _probe(args, dd)
