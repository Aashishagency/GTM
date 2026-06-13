import smtplib
import os
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime
from jinja2 import Template


TRACKING_PIXEL_TEMPLATE = '<img src="{base_url}/track/open/{tracking_id}" width="1" height="1" style="display:none" />'
TRACK_LINK_PATTERN = re.compile(r'href=["\'](?!mailto:|#)(https?://[^"\']+)["\']', re.IGNORECASE)


def personalize(template_str: str, lead: dict) -> str:
    """Replace {{variables}} in templates with lead data.
    Falls back to a neutral greeting token when the name is missing so we never
    mail a raw '{{first_name}}' to a recipient."""
    full_name = (lead.get("name") or "").strip()
    name_parts = full_name.split()
    first_name = name_parts[0] if name_parts else "there"
    try:
        tmpl = Template(template_str)
        return tmpl.render(
            name=full_name or "there",
            first_name=first_name,
            company=lead.get("company", "") or "your company",
            title=lead.get("title", ""),
            city=lead.get("city", ""),
            industry=lead.get("industry", "") or "your industry",
            sender_name=os.getenv("FROM_NAME", ""),
        )
    except Exception:
        # Last-resort: strip the variable braces so the recipient never sees them.
        import re as _re
        return _re.sub(r"\{\{.*?\}\}", "", template_str)


def inject_tracking(html: str, tracking_id: str, base_url: str) -> str:
    """Inject open-tracking pixel and wrap links for click tracking."""
    pixel = TRACKING_PIXEL_TEMPLATE.format(base_url=base_url, tracking_id=tracking_id)

    def replace_link(m):
        original = m.group(1)
        encoded = requests_urlencode(original)
        tracked = f'{base_url}/track/click/{tracking_id}?url={encoded}'
        return f'href="{tracked}"'

    html = TRACK_LINK_PATTERN.sub(replace_link, html)
    html = html + pixel
    return html


def requests_urlencode(url: str) -> str:
    from urllib.parse import quote
    return quote(url, safe="")


def smtp_configured() -> bool:
    """True when real SMTP credentials are present (not blank/placeholder)."""
    user = os.getenv("SMTP_USER", "")
    pw = os.getenv("SMTP_PASS", "")
    placeholders = ("your@gmail.com", "your_app_password_here")
    return bool(user and pw and user not in placeholders and pw not in placeholders)


def test_smtp_login() -> tuple[bool, str]:
    """Verify SMTP connection + login WITHOUT sending anything."""
    if not smtp_configured():
        return False, "SMTP credentials not configured — add your Google App Password in Settings."
    try:
        with smtplib.SMTP(os.getenv("SMTP_HOST", "smtp.gmail.com"),
                          int(os.getenv("SMTP_PORT", 587)), timeout=20) as server:
            server.ehlo()
            server.starttls()
            server.login(os.getenv("SMTP_USER", ""), os.getenv("SMTP_PASS", ""))
        return True, f"SMTP login OK as {os.getenv('SMTP_USER')} — ready to send campaigns."
    except smtplib.SMTPAuthenticationError as e:
        return False, ("Login rejected by Gmail. Use a 16-character App Password "
                       "(myaccount.google.com → Security → 2-Step Verification → App passwords), "
                       f"not the normal account password. ({e.smtp_code})")
    except Exception as e:
        return False, f"SMTP connection failed: {e}"


def send_email(
    to_email: str,
    to_name: str,
    subject: str,
    body_html: str,
    body_text: str = None,
    tracking_id: str = None,
    base_url: str = None,
) -> tuple[bool, str, str]:
    """Send a single email. Returns (success, error_message, message_id).
    message_id is stored per-contact so the IMAP poller can auto-detect replies."""
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", 587))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    from_name = os.getenv("FROM_NAME", smtp_user)

    if not smtp_configured():
        return False, "SMTP credentials not configured — set the App Password in Settings.", ""

    try:
        from email.utils import make_msgid, formatdate
        domain = smtp_user.split("@")[-1] if "@" in smtp_user else None
        message_id = make_msgid(domain=domain)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{from_name} <{smtp_user}>"
        msg["To"] = f"{to_name} <{to_email}>"
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = message_id
        msg["X-Tracking-ID"] = tracking_id or ""

        if tracking_id and base_url:
            body_html = inject_tracking(body_html, tracking_id, base_url)

        if body_text:
            msg.attach(MIMEText(body_text, "plain"))
        msg.attach(MIMEText(body_html, "html"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, to_email, msg.as_string())

        return True, "", message_id
    except Exception as e:
        return False, str(e), ""


def build_plain_text(html: str) -> str:
    """Strip HTML tags for plain-text fallback."""
    clean = re.sub(r"<[^>]+>", " ", html)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean
