"""IMAP inbox poller for AUTOMATIC reply & bounce detection.

Logs into the sender mailbox (info@aashishagency.com via imap.gmail.com — same
Google App Password as SMTP) and scans recent mail:

  - REPLY:  an incoming message whose In-Reply-To / References headers contain
            a Message-ID we sent (stored per CampaignContact). Fallback: sender
            address matches a contacted lead and the subject starts with "Re:".
  - BOUNCE: a mailer-daemon delivery-failure notice; the failed recipient is
            read from the X-Failed-Recipients header or extracted from the body.

Returns structured events; the app maps them onto CampaignContact rows.
"""
import imaplib
import email as email_lib
import os
import re
from datetime import datetime, timedelta
from email.header import decode_header
from email.utils import parseaddr

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
BOUNCE_SENDERS = ("mailer-daemon", "postmaster", "mail delivery subsystem")


def imap_configured() -> bool:
    user = os.getenv("SMTP_USER", "")
    pw = os.getenv("SMTP_PASS", "")
    return bool(user and pw and user != "your@gmail.com" and pw != "your_app_password_here")


def _decode(value) -> str:
    if not value:
        return ""
    parts = []
    for chunk, enc in decode_header(value):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(enc or "utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return "".join(parts)


def _body_text(msg) -> str:
    """Plain-text body (first 8 KB) — enough to find a bounced recipient address."""
    try:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() in ("text/plain", "message/delivery-status"):
                    payload = part.get_payload(decode=True)
                    if payload:
                        return payload.decode("utf-8", errors="replace")[:8192]
            return ""
        payload = msg.get_payload(decode=True)
        return payload.decode("utf-8", errors="replace")[:8192] if payload else ""
    except Exception:
        return ""


def fetch_inbox_events(since_days: int = 3) -> list[dict]:
    """Scan the inbox for replies and bounces. Returns a list of:
      {"type": "reply",  "from_email": ..., "subject": ..., "refs": set-of-message-ids}
      {"type": "bounce", "failed_emails": [...]}
    Raises on connection/auth failure so the caller can log it.
    """
    user = os.getenv("SMTP_USER", "")
    pw = os.getenv("SMTP_PASS", "")
    host = os.getenv("IMAP_HOST", "imap.gmail.com")
    port = int(os.getenv("IMAP_PORT", 993))

    since = (datetime.utcnow() - timedelta(days=since_days)).strftime("%d-%b-%Y")
    events = []

    with imaplib.IMAP4_SSL(host, port) as imap:
        imap.login(user, pw)
        imap.select("INBOX", readonly=True)
        status, data = imap.search(None, f'(SINCE "{since}")')
        if status != "OK" or not data or not data[0]:
            return events

        ids = data[0].split()
        # Cap per poll so a huge inbox can't stall the scheduler tick.
        for num in ids[-200:]:
            status, msg_data = imap.fetch(num, "(BODY.PEEK[])")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email_lib.message_from_bytes(msg_data[0][1])

            from_name, from_email = parseaddr(_decode(msg.get("From", "")))
            from_lower = (from_email or from_name or "").lower()
            subject = _decode(msg.get("Subject", ""))

            if any(b in from_lower for b in BOUNCE_SENDERS):
                failed = []
                xfr = msg.get("X-Failed-Recipients", "")
                if xfr:
                    failed = EMAIL_RE.findall(xfr)
                if not failed:
                    failed = [e for e in EMAIL_RE.findall(_body_text(msg))
                              if "mailer-daemon" not in e.lower() and e.lower() != user.lower()]
                if failed:
                    events.append({"type": "bounce", "failed_emails": list(dict.fromkeys(failed))})
                continue

            if from_lower == user.lower():
                continue  # our own sent mail synced into INBOX

            refs = set()
            for header in ("In-Reply-To", "References"):
                for mid in re.findall(r"<[^>]+>", msg.get(header, "") or ""):
                    refs.add(mid.strip())
            events.append({"type": "reply", "from_email": (from_email or "").lower(),
                           "subject": subject, "refs": refs})
    return events
