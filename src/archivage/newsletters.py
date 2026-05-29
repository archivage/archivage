"""
Newsletter / email archiving: pull messages from Gmail via IMAP,
extract content via trafilatura, save as markdown with frontmatter.
"""

import email
import email.policy
import email.utils
import hashlib
import imaplib
import json
import re
import time
import unicodedata
from datetime import datetime
from email.header import decode_header, make_header
from email.message import Message
from pathlib import Path

import trafilatura

from .log import logger
from .web import normalizeHtml, xmlToMarkdown


GMAIL_IMAP_HOST = "imap.gmail.com"
GMAIL_IMAP_PORT = 993
ALL_MAIL_MAILBOX = '"[Gmail]/All Mail"'


# ---------- helpers ----------

def slugify(text: str, max_len: int = 60) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    if len(text) > max_len:
        text = text[:max_len].rsplit("-", 1)[0]
    return text or "untitled"


def decodeHeader(value: str | None) -> str:
    """Decode RFC 2047 encoded headers."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def cleanSubject(subject: str) -> str:
    """Strip Re:/Fwd: prefixes and normalize whitespace."""
    subject = re.sub(r"^\s*(re|fwd?|tr|sv)\s*:\s*", "", subject, flags=re.IGNORECASE)
    subject = re.sub(r"\s+", " ", subject).strip()
    return subject


def parseFromHeader(value: str) -> tuple[str, str]:
    """Return (display_name, email)."""
    name, addr = email.utils.parseaddr(value or "")
    return decodeHeader(name), addr.lower()


def parseDateHeader(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(value)
        return dt
    except (TypeError, ValueError):
        return None


def messageIdShort(message_id: str | None) -> str:
    """Short stable hash of message-id, used to disambiguate filenames."""
    if not message_id:
        return "noid"
    h = hashlib.sha1(message_id.encode("utf-8", errors="replace")).hexdigest()
    return h[:8]


# ---------- body extraction ----------

def pickBody(msg: Message) -> tuple[str, str]:
    """Return (body, kind) where kind in {"html", "text"}.

    Prefers the richest text/html part. Falls back to text/plain.
    """
    html_parts: list[str] = []
    text_parts: list[str] = []

    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = (part.get_content_type() or "").lower()
        disp = (part.get_content_disposition() or "").lower()
        if disp == "attachment":
            continue
        try:
            payload = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True)
            if isinstance(payload, bytes):
                charset = part.get_content_charset() or "utf-8"
                try:
                    payload = payload.decode(charset, errors="replace")
                except LookupError:
                    payload = payload.decode("utf-8", errors="replace")
            elif payload is None:
                payload = ""
        if not isinstance(payload, str):
            continue

        if ctype == "text/html":
            html_parts.append(payload)
        elif ctype == "text/plain":
            text_parts.append(payload)

    if html_parts:
        return max(html_parts, key=len), "html"
    if text_parts:
        return max(text_parts, key=len), "text"
    return "", "text"


def htmlToMarkdown(html: str) -> str:
    """Run a newsletter HTML body through trafilatura → markdown."""
    if not html.strip():
        return ""

    normalized = normalizeHtml(html)
    xml_output = trafilatura.extract(
        normalized,
        output_format="xml",
        include_links=True,
        include_formatting=True,
        favor_recall=True,
        no_fallback=False,
    )
    if not xml_output:
        return ""
    return xmlToMarkdown(xml_output)


def textToMarkdown(text: str) -> str:
    """Mild cleanup of a text/plain body."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------- filenames & frontmatter ----------

def messageOutputPath(archive_dir: Path, sender_email: str,
                      sent_at: datetime | None, subject: str,
                      message_id: str | None) -> Path:
    """Compute archive path: newsletters/<sender>/<YYYY-MM-DD>-<slug>-<msgid8>.md"""
    iso = (sent_at or datetime.utcnow()).strftime("%Y-%m-%d")
    slug = slugify(cleanSubject(subject))
    short = messageIdShort(message_id)
    sender_dir = sender_email.replace("/", "_") or "_unknown"
    return archive_dir / "newsletters" / sender_dir / f"{iso}-{slug}-{short}.md"


def formatMarkdown(headers: dict, body: str) -> str:
    """YAML frontmatter + body."""
    fm = ["---"]
    if headers.get("from_name"):
        fm.append(f'from: "{headers["from_name"]} <{headers["from_email"]}>"')
    else:
        fm.append(f'from: "{headers["from_email"]}"')
    fm.append(f'from_email: "{headers["from_email"]}"')
    if headers.get("to"):
        to = headers["to"].replace('"', '\\"')
        fm.append(f'to: "{to}"')
    if headers.get("subject"):
        subj = headers["subject"].replace('"', '\\"')
        fm.append(f'subject: "{subj}"')
    if headers.get("date"):
        fm.append(f'date: "{headers["date"]}"')
    if headers.get("message_id"):
        mid = headers["message_id"].replace('"', '\\"')
        fm.append(f'message_id: "{mid}"')
    if headers.get("list_id"):
        lid = headers["list_id"].replace('"', '\\"')
        fm.append(f'list_id: "{lid}"')
    if headers.get("list_unsubscribe"):
        lu = headers["list_unsubscribe"].replace('"', '\\"')
        fm.append(f'list_unsubscribe: "{lu}"')
    fm.append(f"archived: {time.strftime('%Y-%m-%d')}")
    fm.append("---")
    fm.append("")
    if headers.get("subject"):
        fm.append(f"# {headers['subject']}")
        fm.append("")
    fm.append(body.strip())
    fm.append("")
    return "\n".join(fm)


# ---------- message → file ----------

def parseMessage(raw_bytes: bytes) -> dict:
    """Parse raw RFC822 bytes into a dict of headers + markdown body."""
    msg = email.message_from_bytes(raw_bytes, policy=email.policy.default)

    from_name, from_email = parseFromHeader(msg.get("From", ""))
    subject = cleanSubject(decodeHeader(msg.get("Subject", "")))
    sent_at = parseDateHeader(msg.get("Date"))
    message_id = (msg.get("Message-ID") or "").strip()

    body_raw, kind = pickBody(msg)
    if kind == "html":
        body_md = htmlToMarkdown(body_raw)
        if not body_md:
            body_md = textToMarkdown(body_raw)
    else:
        body_md = textToMarkdown(body_raw)

    headers = {
        "from_name": from_name,
        "from_email": from_email,
        "to": decodeHeader(msg.get("To", "")),
        "subject": subject,
        "date": sent_at.strftime("%Y-%m-%dT%H:%M:%S%z") if sent_at else None,
        "sent_at": sent_at,
        "message_id": message_id,
        "list_id": decodeHeader(msg.get("List-ID", "")),
        "list_unsubscribe": decodeHeader(msg.get("List-Unsubscribe", "")),
    }
    return {"headers": headers, "body": body_md}


def saveParsed(parsed: dict, archive_dir: Path, force: bool = False) -> tuple[Path, str]:
    headers = parsed["headers"]
    out_path = messageOutputPath(
        archive_dir,
        headers["from_email"],
        headers["sent_at"],
        headers["subject"] or "(no subject)",
        headers["message_id"],
    )
    if out_path.exists() and not force:
        return out_path, "skipped"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(formatMarkdown(headers, parsed["body"]), encoding="utf-8")
    return out_path, "saved"


def importEml(eml_path: Path, archive_dir: Path, force: bool = False) -> tuple[Path, str]:
    """Save a single .eml file to the newsletters archive."""
    raw = eml_path.read_bytes()
    parsed = parseMessage(raw)
    return saveParsed(parsed, archive_dir, force=force)


# ---------- IMAP ----------

class ImapError(RuntimeError):
    pass


def connect(user: str, password: str,
            host: str = GMAIL_IMAP_HOST,
            port: int = GMAIL_IMAP_PORT) -> imaplib.IMAP4_SSL:
    imap = imaplib.IMAP4_SSL(host, port)
    try:
        imap.login(user, password)
    except imaplib.IMAP4.error as e:
        raise ImapError(f"Login failed for {user}: {e}") from e
    return imap


def selectAllMail(imap: imaplib.IMAP4_SSL) -> int:
    """Select [Gmail]/All Mail read-only and return UIDVALIDITY."""
    typ, data = imap.select(ALL_MAIL_MAILBOX, readonly=True)
    if typ != "OK":
        # Localized Gmail accounts may not have [Gmail]/All Mail; fall back.
        typ, data = imap.select("INBOX", readonly=True)
        if typ != "OK":
            raise ImapError("Cannot select [Gmail]/All Mail or INBOX")
    typ, data = imap.response("UIDVALIDITY")
    try:
        return int(data[0])
    except (TypeError, ValueError, IndexError):
        return 0


def searchSenderUids(imap: imaplib.IMAP4_SSL, sender: str,
                     since_uid: int = 0) -> list[int]:
    """Return UIDs of messages from sender, with UID > since_uid."""
    # Gmail's X-GM-RAW supports the full Gmail search syntax.
    raw_query = f'from:{sender}'.encode("utf-8")
    typ, data = imap.uid("SEARCH", "CHARSET", "UTF-8", "X-GM-RAW", raw_query)
    if typ != "OK":
        raise ImapError(f"SEARCH failed: {data!r}")
    if not data or not data[0]:
        return []
    uids = [int(x) for x in data[0].split()]
    return sorted(u for u in uids if u > since_uid)


def fetchRaw(imap: imaplib.IMAP4_SSL, uid: int) -> bytes:
    typ, data = imap.uid("FETCH", str(uid), "(RFC822)")
    if typ != "OK" or not data or not data[0]:
        raise ImapError(f"FETCH UID {uid} failed: {data!r}")
    # data is e.g. [(b'1 (RFC822 {1234}', b'<raw>'), b')']
    for item in data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
            return bytes(item[1])
    raise ImapError(f"No body returned for UID {uid}")


# ---------- senders config ----------

def sendersFile(archive_dir: Path) -> Path:
    return archive_dir / "newsletters" / ".config" / "senders.txt"


def loadSenders(archive_dir: Path) -> list[str]:
    path = sendersFile(archive_dir)
    if not path.exists():
        return []
    senders = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        senders.append(line.lower())
    return senders


def addSender(archive_dir: Path, sender: str) -> bool:
    """Append sender if not already present. Returns True if added."""
    sender = sender.strip().lower()
    if not sender:
        return False
    existing = loadSenders(archive_dir)
    if sender in existing:
        return False
    path = sendersFile(archive_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        if path.stat().st_size > 0 and not path.read_text().endswith("\n"):
            f.write("\n")
        f.write(sender + "\n")
    return True


# ---------- state ----------

def stateFile(archive_dir: Path) -> Path:
    return archive_dir / "newsletters" / ".state" / "state.json"


def loadState(archive_dir: Path) -> dict:
    path = stateFile(archive_dir)
    if not path.exists():
        return {"senders": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"senders": {}}


def saveState(archive_dir: Path, state: dict) -> None:
    path = stateFile(archive_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def getSenderState(archive_dir: Path, sender: str) -> dict:
    return loadState(archive_dir).get("senders", {}).get(sender, {})


def setSenderState(archive_dir: Path, sender: str, **fields) -> None:
    state = loadState(archive_dir)
    state.setdefault("senders", {}).setdefault(sender, {}).update(fields)
    saveState(archive_dir, state)


# ---------- sync ----------

def syncSender(imap: imaplib.IMAP4_SSL, sender: str, archive_dir: Path,
               uidvalidity: int, force: bool = False,
               on_progress=None) -> dict:
    """Fetch all new messages from sender, save as markdown.

    Returns {"saved": n, "skipped": n, "failed": n, "max_uid": int}.
    """
    sender = sender.lower()
    prev = getSenderState(archive_dir, sender)

    # If UIDVALIDITY changed, reset (re-fetch all; saveParsed is idempotent).
    if prev.get("uidvalidity") and prev["uidvalidity"] != uidvalidity:
        logger.warning(f"UIDVALIDITY changed for {sender}, full re-scan")
        since_uid = 0
    else:
        since_uid = 0 if force else int(prev.get("last_uid", 0))

    uids = searchSenderUids(imap, sender, since_uid=since_uid)

    saved = skipped = failed = 0
    max_uid = since_uid

    for i, uid in enumerate(uids):
        try:
            raw = fetchRaw(imap, uid)
            parsed = parseMessage(raw)
            path, status = saveParsed(parsed, archive_dir, force=force)
            if status == "saved":
                saved += 1
            else:
                skipped += 1
            if on_progress:
                on_progress(i + 1, len(uids), path.name, status)
            max_uid = max(max_uid, uid)
        except Exception as e:
            failed += 1
            logger.warning(f"Failed UID {uid} from {sender}: {e}")
            if on_progress:
                on_progress(i + 1, len(uids), f"uid={uid}", f"error: {e}")

    # Persist state
    state_count = int(prev.get("count", 0)) + saved
    setSenderState(
        archive_dir, sender,
        uidvalidity=uidvalidity,
        last_uid=max_uid,
        count=state_count,
        last_sync=datetime.now().isoformat(timespec="seconds"),
    )

    return {"saved": saved, "skipped": skipped, "failed": failed,
            "max_uid": max_uid, "total": len(uids)}


# ---------- stats ----------

def archiveStats(archive_dir: Path) -> dict:
    """Count archived newsletters by sender."""
    nl_dir = archive_dir / "newsletters"
    if not nl_dir.exists():
        return {"total": 0, "senders": {}}
    senders = {}
    total = 0
    for entry in sorted(nl_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        count = sum(1 for _ in entry.glob("*.md"))
        if count:
            senders[entry.name] = count
            total += count
    return {"total": total, "senders": senders}
