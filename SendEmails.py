#!/usr/bin/env python3
"""
Secret Santa Emailer

Usage: python SendEmails.py [path/to/sendercredentials.txt]

- Finds the most recent YYYY.txt in assignments_dir (default: this script's folder)
- Reads giver->receiver pairs
- Loads emails from emails_file (default: emails.txt next to this script)
- Loads SMTP + behavior from sendercredentials.txt (same folder by default)
- Defaults to DRY-RUN; set `send = true` in sendercredentials.txt to actually send

sendercredentials.txt keys (case-insensitive):
  smtp_server    = smtp.gmail.com
  smtp_port      = 587
  username       = youraddress@gmail.com
  password       = <16-char app password>
  from           = "Secret Santa <youraddress@gmail.com>"   # optional; defaults to username
  subject        = Secret Santa {year}: your assignment 🎁   # optional; {year} will be formatted
  wishlist_url   = https://example.com/wishlist              # optional; appended to email body
  note           = Any extra sentence to include             # optional; appended to email body
  use_starttls   = true                                      # optional; default true if port 587 else false
  send           = true                                     # optional; default false (dry-run)
  assignments_dir= /path/to/folder                           # optional; default this script's folder
  emails_file    = /path/to/emails.txt                       # optional; default emails.txt next to script
  wishlist_doc   = Gift Exchange.docx                        # optional; Word doc with each person's address + list links
  wishlist_aliases = Brian=Dad, Jennifer=Mom                 # optional; doc heading name = name used in assignments

Relative paths are resolved from the credentials file's folder.

wishlist_doc format: a heading paragraph with the person's name (e.g. "LEXI"), then
"Where to send gift: <address>" (may continue on following lines), then link lines such as
"Gift list link: ...", "Amazon: ...", "Other: ...". Hyperlinks are expanded to their URLs.
Headings also match a receiver's first name (doc "NATE" matches "Nate Puffer").
"""

from __future__ import annotations
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from email.message import EmailMessage
import smtplib
import ssl

# ----------------- Utilities & Parsing -----------------

ASSIGNMENT_LINE_RE = re.compile(r"\s*(?:->|:|,|-)\s*")

def canon(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()

def script_dir() -> Path:
    try:
        return Path(__file__).resolve().parent
    except NameError:
        return Path(".").resolve()

def _parse_bool(s: str | None, default: bool) -> bool:
    if s is None:
        return default
    return s.strip().lower() in {"1", "true", "yes", "on"}

def load_credentials(path: Path) -> dict:
    """
    Load key=value pairs from sendercredentials.txt.
    Required: smtp_server, smtp_port, username
    Optional: password, from, subject, use_starttls, send, assignments_dir, emails_file, wishlist_url, note
    """
    if not path.exists():
        sys.exit(f"[ERROR] Credentials file not found: {path}")

    cfg: dict[str, str] = {}
    with path.open("r", encoding="utf-8") as f:
        for ln in f:
            s = ln.strip()
            if not s or s.startswith("#"):
                continue
            if "=" not in s:
                continue
            k, v = s.split("=", 1)
            k = k.strip().lower()
            v = v.strip().strip('"').strip("'")
            cfg[k] = v

    server = cfg.get("smtp_server")
    port_s = cfg.get("smtp_port")
    username = cfg.get("username")

    if not server or not port_s or not username:
        sys.exit("[ERROR] sendercredentials.txt must include smtp_server, smtp_port, and username.")

    try:
        port = int(port_s)
    except ValueError:
        sys.exit("[ERROR] smtp_port must be an integer.")

    use_starttls = _parse_bool(cfg.get("use_starttls"), default=(port == 587))
    from_addr = cfg.get("from") or username
    subject_tmpl = cfg.get("subject") or "Secret Santa {year}: your assignment 🎁"
    send_flag = _parse_bool(cfg.get("send"), default=False)

    # Optional paths / content
    sdir = cfg.get("assignments_dir")
    emails_file = cfg.get("emails_file")
    wishlist_url = cfg.get("wishlist_url")
    note = cfg.get("note") or cfg.get("message_note") or cfg.get("extra_note")

    return {
        "server": server,
        "port": port,
        "username": username,
        "password": cfg.get("password"),  # may be None in dry-run
        "use_starttls": use_starttls,
        "from_addr": from_addr,
        "subject_tmpl": subject_tmpl,
        "send": send_flag,
        "assignments_dir": sdir,
        "emails_file": emails_file,
        "wishlist_url": wishlist_url,
        "note": note,
        "wishlist_doc": cfg.get("wishlist_doc"),
        "wishlist_aliases": cfg.get("wishlist_aliases"),
    }

def find_latest_year_file(directory: Path) -> Tuple[int, Path]:
    candidates: list[Tuple[int, Path]] = []
    for p in directory.glob("*.txt"):
        m = re.fullmatch(r"(\d{4})\.txt", p.name)
        if m:
            candidates.append((int(m.group(1)), p))
    if not candidates:
        sys.exit(f"[ERROR] No year files like '2025.txt' found in {directory}")
    year, path = max(candidates, key=lambda t: t[0])
    return year, path

def load_assignments(path: Path) -> Dict[str, str]:
    """Returns mapping giver(display)->receiver(display)"""
    mapping: Dict[str, str] = {}
    with path.open("r", encoding="utf-8") as f:
        for ln in f:
            s = ln.strip()
            if not s or s.startswith("#"):
                continue
            parts = ASSIGNMENT_LINE_RE.split(s)
            if len(parts) != 2:
                continue
            giver, receiver = parts[0].strip(), parts[1].strip()
            mapping[giver] = receiver
    if not mapping:
        sys.exit(f"[ERROR] No assignments parsed from {path}")
    return mapping

def load_emails(path: Path) -> Dict[str, Tuple[str, str]]:
    """
    Returns dict: canonical_name -> (display_name, email)
    Accepts: 'Name, email' or pipe/tab; ignores comments/blank lines.
    """
    if not path.exists():
        sys.exit(f"[ERROR] Emails file not found: {path}")
    book: Dict[str, Tuple[str, str]] = {}
    with path.open("r", encoding="utf-8") as f:
        for ln in f:
            s = ln.strip()
            if not s or s.startswith("#"):
                continue
            parts = re.split(r"[,\|\t]", s)
            parts = [p.strip() for p in parts if p.strip()]
            if len(parts) < 2:
                print(f"[WARN] Skipping line (need 'Name, Email'): {s}")
                continue
            name, email = parts[0], parts[1]
            book[canon(name)] = (name, email)
    if not book:
        sys.exit(f"[ERROR] No emails parsed from {path}")
    return book

def build_giver_email_map(assignments: Dict[str, str], email_book: Dict[str, Tuple[str, str]]) -> Dict[str, Tuple[str, str, str]]:
    """
    Returns: giver_display -> (giver_email, giver_display, receiver_display)
    Warns for missing emails.
    """
    out: Dict[str, Tuple[str, str, str]] = {}
    missing = []
    for giver_display, receiver_display in assignments.items():
        key = canon(giver_display)
        rec = email_book.get(key)
        if not rec:
            missing.append(giver_display)
            continue
        _, giver_email = rec
        out[giver_display] = (giver_email, giver_display, receiver_display)
    if missing:
        print("[WARN] No email for the following givers (will be skipped):")
        for m in missing:
            print(f"  - {m}")
    return out

# ----------------- Wish list doc (.docx) -----------------

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_ADDRESS_RE = re.compile(r"^\s*where to send( the)? gift\s*:?\s*", re.I)
_LINK_RE = re.compile(r"^\s*([A-Za-z][\w ]{0,30}?)\s*:\s*(.*)$")

WishInfo = Dict[str, object]  # {"name": str, "address": str|None, "links": [str]}

def _docx_paragraphs(path: Path) -> List[Tuple[str, List[Tuple[str, str]]]]:
    """Returns [(paragraph_text, [(link_text, url), ...]), ...] for non-empty paragraphs."""
    with zipfile.ZipFile(path) as z:
        rels_root = ET.fromstring(z.read("word/_rels/document.xml.rels"))
        rels = {r.get("Id"): r.get("Target") for r in rels_root}
        root = ET.fromstring(z.read("word/document.xml"))

    out = []
    for p in root.iter(_W + "p"):
        text_parts: List[str] = []
        links: List[Tuple[str, str]] = []
        for el in p:
            if el.tag == _W + "hyperlink":
                t = "".join(x.text or "" for x in el.iter(_W + "t"))
                url = rels.get(el.get(_R + "id"))
                text_parts.append(t)
                if url:
                    links.append((t.strip(), url))
            else:
                for x in el.iter():
                    if x.tag == _W + "t":
                        text_parts.append(x.text or "")
                    elif x.tag in (_W + "br", _W + "tab"):
                        text_parts.append(" ")
        text = re.sub(r"\s+", " ", "".join(text_parts)).strip()
        if text:
            out.append((text, links))
    return out

def _format_link(line: str, links: List[Tuple[str, str]]) -> str:
    """'Amazon: <Clip's Amazon List>' -> "Amazon (Clip's Amazon List): https://..."."""
    m = _LINK_RE.match(line)
    label, rest = (m.group(1).strip(), m.group(2).strip()) if m else ("", line)
    if label.lower() in {"gift list link", "gift list", "list", "link"}:
        label = ""
    if not links:
        return f"{label}: {rest}" if label and rest else rest
    parts = []
    for text, url in links:
        name = text if text and text != url else ""
        title = " – ".join(x for x in (label, name) if x)
        parts.append(f"{title}: {url}" if title else url)
    return "\n  - ".join(parts)

def load_wishlist_doc(path: Path) -> Dict[str, WishInfo]:
    """Returns canon(heading name) -> {"name", "address", "links"}."""
    if not path.exists():
        sys.exit(f"[ERROR] Wish list doc not found: {path}")
    paras = _docx_paragraphs(path)
    people: Dict[str, WishInfo] = {}
    current: Optional[WishInfo] = None
    in_address = False
    for i, (text, links) in enumerate(paras):
        nxt = paras[i + 1][0] if i + 1 < len(paras) else ""
        if _ADDRESS_RE.match(nxt) and not _ADDRESS_RE.match(text):
            # A heading line: the person's name
            current = {"name": text.title(), "address": None, "links": []}
            people[canon(text)] = current
            in_address = False
            continue
        if current is None:
            continue
        if _ADDRESS_RE.match(text):
            addr = _ADDRESS_RE.sub("", text).strip()
            current["address"] = addr or None
            in_address = True
            continue
        if links or _LINK_RE.match(text):
            in_address = False
            entry = _format_link(text, links)
            if entry:
                current["links"].append(entry)
            continue
        if in_address:
            current["address"] = f"{current['address']}, {text}" if current["address"] else text
    return people

def build_wish_lookup(
    doc: Dict[str, WishInfo], aliases: Optional[str]
) -> Dict[str, WishInfo]:
    """Adds alias keys (e.g. 'Brian=Dad' makes 'dad' point at the BRIAN entry)."""
    lookup = dict(doc)
    for pair in (aliases or "").split(","):
        if "=" not in pair:
            continue
        doc_name, our_name = (canon(x) for x in pair.split("=", 1))
        if doc_name in doc:
            lookup[our_name] = doc[doc_name]
        else:
            print(f"[WARN] wishlist_aliases: '{doc_name}' not found in wish list doc")
    return lookup

def find_wish_info(lookup: Dict[str, WishInfo], name: str) -> Optional[WishInfo]:
    key = canon(name)
    if key in lookup:
        return lookup[key]
    first = key.split(" ")[0]
    return lookup.get(first)

def make_message(
    from_addr: str,
    to_addr: str,
    giver_name: str,
    receiver_name: str,
    year: int,
    subject: str,
    wishlist_url: str | None = None,
    note: str | None = None,
    wish: Optional[WishInfo] = None,
) -> EmailMessage:
    lines = [
        f"Hey {giver_name},",
        "",
        f"Welcome to the Secret Santa gift exchange for {year}!",
        f"This year you have been assigned to gift to {receiver_name}.",
    ]
    if wish is not None:
        lines += ["", f"Where to send {receiver_name}'s gift:"]
        lines += [f"  {wish['address']}" if wish["address"] else "  (No address listed yet. Check the gift exchange doc.)"]
        lines += ["", f"{receiver_name}'s wish list:"]
        if wish["links"]:
            lines += [f"  - {link}" for link in wish["links"]]
        else:
            lines += ["  (No wish list added yet. Check the gift exchange doc.)"]
    if wishlist_url:
        lines += ["", f"Wish list link: {wishlist_url}"]
    if note:
        lines += ["", note]
    lines += ["", "Please keep this a secret 🤫"]
    body = "\n".join(lines)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(body)
    return msg

def send_all(messages: list[EmailMessage], server: str, port: int, username: str, password: str, use_starttls: bool = True) -> None:
    if use_starttls:
        with smtplib.SMTP(server, port) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(username, password)
            for m in messages:
                smtp.send_message(m)
    else:
        with smtplib.SMTP_SSL(server, port, context=ssl.create_default_context()) as smtp:
            smtp.login(username, password)
            for m in messages:
                smtp.send_message(m)

# ----------------- Main (optional arg: credentials file) -----------------

def main():
    here = script_dir()

    # Optional: path to an alternate credentials file, e.g. kids/sendercredentials.txt
    creds_path = Path(sys.argv[1]).expanduser().resolve() if len(sys.argv) > 1 else here / "sendercredentials.txt"
    creds = load_credentials(creds_path)
    base = creds_path.parent  # relative paths in the config resolve from its own folder

    # Resolve paths
    assignments_dir = (base / Path(creds["assignments_dir"]).expanduser()).resolve() if creds["assignments_dir"] else base
    emails_path = (base / Path(creds["emails_file"]).expanduser()).resolve() if creds["emails_file"] else (base / "emails.txt")

    year, ypath = find_latest_year_file(assignments_dir)
    assignments = load_assignments(ypath)
    email_book = load_emails(emails_path)
    roster = build_giver_email_map(assignments, email_book)
    if not roster:
        sys.exit("[ERROR] No mailable givers found. Check emails.txt names vs assignment names.")

    subject = creds["subject_tmpl"].format(year=year)
    from_addr = creds["from_addr"]

    # Optional: per-receiver address + wish list from a Word doc
    wish_lookup: Optional[Dict[str, WishInfo]] = None
    wish_path: Optional[Path] = None
    if creds["wishlist_doc"]:
        wish_path = (base / Path(creds["wishlist_doc"]).expanduser()).resolve()
        wish_lookup = build_wish_lookup(load_wishlist_doc(wish_path), creds["wishlist_aliases"])

    # Build messages
    messages: list[EmailMessage] = []
    wish_gaps: list[str] = []
    for giver_display, (giver_email, giver_name, receiver_name) in roster.items():
        wish = None
        if wish_lookup is not None:
            wish = find_wish_info(wish_lookup, receiver_name)
            if wish is None:
                wish_gaps.append(f"{receiver_name}: not found in wish list doc (add a wishlist_aliases entry?)")
                wish = {"name": receiver_name, "address": None, "links": []}
            else:
                if not wish["address"]:
                    wish_gaps.append(f"{receiver_name}: no address in doc")
                if not wish["links"]:
                    wish_gaps.append(f"{receiver_name}: no wish list in doc")
        messages.append(
            make_message(
                from_addr=from_addr,
                to_addr=giver_email,
                giver_name=giver_name,
                receiver_name=receiver_name,
                year=year,
                subject=subject,
                wishlist_url=creds["wishlist_url"],
                note=creds["note"],
                wish=wish,
            )
        )

    if wish_gaps:
        print("[WARN] Missing wish list info (emails will say to check the doc):")
        for g in wish_gaps:
            print(f"  - {g}")

    if not creds["send"]:
        print(f"[DRY-RUN] Would send {len(messages)} emails for year {year} from '{from_addr}':")
        print(f"Assignments file: {ypath}")
        print(f"Emails file:      {emails_path}")
        if wish_path:
            print(f"Wish list doc:    {wish_path}")
        print("-" * 60)
        for m in messages:
            print(f"TO:   {m['To']}")
            print(f"SUBJ: {m['Subject']}")
            print()
            print(m.get_content())
            print("-" * 60)
        print("Set `send = true` in sendercredentials.txt to actually send.")
        return

    if not creds["password"]:
        sys.exit("[ERROR] `send = true` but no password found in sendercredentials.txt.")

    print(f"[INFO] Sending {len(messages)} emails for year {year} via {creds['server']}:{creds['port']} ...")
    send_all(
        messages,
        server=creds["server"],
        port=creds["port"],
        username=creds["username"],
        password=creds["password"],
        use_starttls=creds["use_starttls"],
    )
    print("[OK] All emails sent.")

if __name__ == "__main__":
    main()
