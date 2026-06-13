"""Hunter.io data provider — free tier returns REAL emails (50 credits/month).

Strengths: company/domain -> people + verified emails (Domain Search), and
name+company -> email (Email Finder). Weaknesses: no broad title-only prospecting
and rarely phone numbers. We implement discovery as a sweep over a list of target
companies, filtering returned positions by role keywords.

Docs: https://hunter.io/api-documentation
"""
import requests
import os

HUNTER_BASE = "https://api.hunter.io/v2"
_PLACEHOLDER_KEYS = {"", "your_hunter_api_key_here", "changeme"}


class HunterError(RuntimeError):
    """Human-readable message the UI can show directly."""


def get_api_key() -> str | None:
    key = (os.getenv("HUNTER_API_KEY") or "").strip()
    if key.lower() in _PLACEHOLDER_KEYS:
        return None
    return key


def _require_key():
    if not get_api_key():
        raise HunterError(
            "Hunter API key not configured. Add HUNTER_API_KEY=<your key> to the .env file "
            "in the gtm_workflow folder, then restart the app. Get a free key (50 credits/mo) "
            "at hunter.io -> API."
        )


def _get(path: str, params: dict, action: str):
    _require_key()
    params = dict(params or {})
    params["api_key"] = get_api_key()
    resp = requests.get(f"{HUNTER_BASE}/{path}", params=params, timeout=40)
    if resp.ok:
        return resp.json()

    # Hunter error shape: {"errors":[{"id","code","details"}]}
    detail = ""
    try:
        data = resp.json()
        errs = data.get("errors") or []
        if errs:
            detail = errs[0].get("details") or errs[0].get("id") or ""
    except Exception:
        detail = (resp.text or "")[:200]

    code = resp.status_code
    if code in (401, 403):
        raise HunterError(f"{action} failed ({code}): {detail or 'invalid API key'}. "
                          "Check your key at hunter.io -> API.")
    if code == 429:
        raise HunterError(f"{action} failed (429): rate limit or out of monthly credits. {detail}")
    if code == 400:
        raise HunterError(f"{action} failed (400): {detail or 'bad request'}.")
    raise HunterError(f"{action} failed ({code}): {detail}")


# ─── Endpoints ────────────────────────────────────────────────────────────────

def domain_search(domain: str = None, company: str = None, limit: int = 10,
                  seniority: str = None, department: str = None) -> dict:
    """Find people + emails at a company. 1 credit per call (up to ~10 emails)."""
    params = {"limit": min(limit, 100)}
    if domain:
        params["domain"] = domain
    if company:
        params["company"] = company
    if seniority:
        params["seniority"] = seniority      # junior | senior | executive
    if department:
        params["department"] = department    # executive, management, sales, marketing, hr, it...
    return _get("domain-search", params, action="Company search")


def email_finder(domain: str = None, company: str = None,
                 first_name: str = None, last_name: str = None, full_name: str = None) -> dict:
    """Find a specific person's email. 1 credit per call."""
    params = {}
    if domain:
        params["domain"] = domain
    if company:
        params["company"] = company
    if full_name:
        params["full_name"] = full_name
    if first_name:
        params["first_name"] = first_name
    if last_name:
        params["last_name"] = last_name
    return _get("email-finder", params, action="Email finder")


def verify_email(email: str) -> dict:
    return _get("email-verifier", {"email": email}, action="Email verify")


def account() -> dict:
    return _get("account", {}, action="Account check")


def test_api_key() -> tuple[bool, str]:
    if not get_api_key():
        return False, "No Hunter API key configured. Add HUNTER_API_KEY to .env and restart."
    try:
        data = account().get("data", {})
        plan = data.get("plan_name", "?")
        reqs = (data.get("requests") or {}).get("searches") or {}
        used, avail = reqs.get("used", "?"), reqs.get("available", "?")
        return True, f"Hunter key is valid. Plan: {plan}. Searches used {used}/{avail} this month."
    except HunterError as e:
        return False, str(e)
    except Exception as e:
        return False, f"Unexpected error: {e}"


# ─── Parsing into our lead schema ─────────────────────────────────────────────

_GENERIC_PREFIXES = ("info", "contact", "hello", "support", "admin",
                     "office", "team", "careers", "jobs", "help")

# Generic words stripped from role keywords before matching, so "manager"/"head"
# alone don't tag every contact as relevant (seniority is ranked via _DECISION_TERMS).
_ROLE_STOPWORDS = {"manager", "head", "of", "the", "and", "for", "senior", "lead",
                   "director", "executive", "officer", "chief", "vice", "president",
                   "general", "assistant", "associate", "specialist", "&"}

# Functions/seniority that make someone a relevant target for a loyalty/CRM pitch,
# used to rank "decision-maker" contacts above operational staff.
_DECISION_TERMS = (
    "loyalty", "crm", "customer", "retention", "dealer", "channel", "membership",
    "rewards", "engagement", "marketing", "brand", "sales", "growth", "commercial",
    "ecommerce", "e-commerce", "digital", "experience", "cx",
    "head", "chief", "director", "vice president", "vp", "general manager",
    "gm", "manager", "lead", "president", "founder", "owner", "partner",
)


def _is_generic(email: str) -> bool:
    local = (email or "").split("@")[0].lower()
    return any(local == p or local.startswith(p + ".") for p in _GENERIC_PREFIXES)


def parse_domain_search(result: dict, role_keywords: list[str] = None,
                        country: str = "India", strict: bool = False) -> list[dict]:
    """Turn a Domain Search response into lead dicts.

    Role keywords are used to RANK (relevant titles first), not to discard —
    Hunter's free tier returns whatever ~10 contacts it has indexed for a domain,
    so a hard title filter usually leaves zero. Decision-maker titles are always
    ranked above junior/operational ones. Pass strict=True to keep ONLY
    keyword-matched positions (may legitimately return nothing)."""
    data = result.get("data") or {}
    org = data.get("organization") or ""
    domain = data.get("domain") or ""
    emails = data.get("emails") or []

    # Tokenize role keywords into meaningful FUNCTION words (drop generic words like
    # "manager"/"head" so they don't make every title look relevant — seniority is
    # handled separately by _DECISION_TERMS).
    tokens = set()
    for k in (role_keywords or []):
        for w in (k or "").lower().replace(",", " ").split():
            if len(w) > 2 and w not in _ROLE_STOPWORDS:
                tokens.add(w)

    matched, related, others = [], [], []
    for e in emails:
        position = (e.get("position") or "").strip()
        value = e.get("value") or ""
        if not value or _is_generic(value):
            continue

        first = e.get("first_name") or ""
        last = e.get("last_name") or ""
        name = (first + " " + last).strip() or value.split("@")[0]
        phones = e.get("phone_number") or ""
        confidence = e.get("confidence")
        pos_l = position.lower()

        is_match = bool(tokens and pos_l and any(t in pos_l for t in tokens))
        # Loyalty buying-committee functions + seniority signals, so even when the
        # exact role keyword isn't present we still surface the right people first.
        is_related = bool(pos_l and any(t in pos_l for t in _DECISION_TERMS))

        lead = {
            "apollo_id": None,
            "name": name,
            "title": position,
            "company": org,
            "email": value,
            "phone": phones or "",
            "linkedin_url": e.get("linkedin") or "",
            "city": "", "state": "", "country": country,
            "industry": "",
            "company_size": "",
            "website": domain,
            "email_status": "verified" if (confidence or 0) >= 80 else "unverified",
            "enriched": True,
            "source": "hunter",
            "title_match": is_match,
            # 0 = function match (loyalty/CRM/sales…), 1 = other decision-maker,
            # 2 = everyone else. Used to rank results globally across companies.
            "rank": 0 if is_match else (1 if is_related else 2),
        }
        if is_match:
            matched.append(lead)
        elif is_related:
            related.append(lead)
        else:
            others.append(lead)

    if strict and tokens:
        return matched
    # Soft: function matches, then related decision-makers, then everyone else.
    return matched + related + others


def parse_email_finder(result: dict, fallback_name: str = "", company: str = "",
                       country: str = "India") -> dict | None:
    data = result.get("data") or {}
    email = data.get("email")
    if not email:
        return None
    first = data.get("first_name") or ""
    last = data.get("last_name") or ""
    name = (first + " " + last).strip() or fallback_name
    return {
        "apollo_id": None,
        "name": name,
        "title": data.get("position") or "",
        "company": data.get("company") or company,
        "email": email,
        "phone": data.get("phone_number") or "",
        "linkedin_url": data.get("linkedin_url") or "",
        "city": "", "state": "", "country": country,
        "industry": "",
        "company_size": "",
        "website": data.get("domain") or "",
        "email_status": "verified" if (data.get("score") or 0) >= 80 else "unverified",
        "enriched": True,
        "source": "hunter",
    }
