import requests
import os

# Correct, current Apollo base path is /api/v1 (NOT /v1).
APOLLO_BASE = "https://api.apollo.io/api/v1"

# Placeholder Apollo returns when an email exists but isn't unlocked on your plan.
LOCKED_EMAIL_MARKERS = ("email_not_unlocked", "domain.com")

# Sentinel values that mean "no real key configured".
_PLACEHOLDER_KEYS = {"", "your_apollo_api_key_here", "your_apollo_key", "changeme"}


class ApolloError(RuntimeError):
    """Raised with a human-readable message the UI can show directly."""


def get_api_key() -> str | None:
    key = (os.getenv("APOLLO_API_KEY") or "").strip()
    if key.lower() in _PLACEHOLDER_KEYS:
        return None
    return key


def _headers():
    return {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "accept": "application/json",
        "X-Api-Key": get_api_key() or "",
    }


def _require_key():
    if not get_api_key():
        raise ApolloError(
            "Apollo API key not configured. Add APOLLO_API_KEY=<your master key> to the "
            ".env file in the gtm_workflow folder, then restart the app. "
            "Get a master key at apollo.io → Settings → Integrations → API."
        )


def _raise_for_apollo(resp: requests.Response, action: str):
    """Turn Apollo's HTTP errors into clear, actionable messages (including the
    response body, which is where Apollo explains *why* a 422/403 happened)."""
    if resp.ok:
        return
    body = ""
    try:
        data = resp.json()
        body = data.get("error") or data.get("message") or str(data)
    except Exception:
        body = (resp.text or "")[:300]

    code = resp.status_code
    if code in (401, 403):
        hint = ("Invalid API key, or your key is not a *master* key. The People Search "
                "endpoint requires a master API key (apollo.io → Settings → Integrations → "
                "API → create master key).")
        raise ApolloError(f"{action} failed ({code}): {body or 'unauthorized'}. {hint}")
    if code == 422:
        raise ApolloError(
            f"{action} failed (422): {body or 'unprocessable'}. "
            "This usually means the API key is missing/invalid or a filter value was rejected.")
    if code == 429:
        raise ApolloError(f"{action} failed (429): rate limit / out of credits. {body}")
    raise ApolloError(f"{action} failed ({code}): {body}")


def _post(path: str, payload: dict, params: dict = None, action: str = "Apollo request"):
    _require_key()
    resp = requests.post(f"{APOLLO_BASE}/{path}", json=payload, params=params,
                         headers=_headers(), timeout=40)
    _raise_for_apollo(resp, action)
    return resp.json()


def _get(path: str, params: dict, action: str = "Apollo request"):
    _require_key()
    resp = requests.get(f"{APOLLO_BASE}/{path}", params=params, headers=_headers(), timeout=40)
    _raise_for_apollo(resp, action)
    return resp.json()


def _normalize_size_ranges(sizes: list[str]) -> list[str]:
    """Apollo expects ranges like '11,50' (comma-separated), not '11-50'.
    Also maps an open-ended '10001+' to '10001,1000000'."""
    out = []
    for s in sizes or []:
        s = (s or "").strip().replace(" ", "")
        if not s:
            continue
        if s.endswith("+"):
            out.append(f"{s[:-1]},1000000")
        elif "-" in s:
            out.append(s.replace("-", ","))
        else:
            out.append(s)
    return out


def search_people(
    job_titles: list[str],
    country: str = "India",
    industries: list[str] = None,
    company_sizes: list[str] = None,
    seniorities: list[str] = None,
    page: int = 1,
    per_page: int = 25,
    keywords: str = None,
    locations: list[str] = None,
) -> dict:
    """People Search via the credit-free api_search endpoint.
    NOTE: returns NO emails/phones — use enrich_person() to unlock them.

    `locations` (city/state/country strings like 'Mumbai' or 'Maharashtra, India')
    takes precedence over `country`, so the demographic search can target a place."""
    person_locations = locations if locations else ([country] if country else None)
    payload = {
        "page": page,
        "per_page": min(per_page, 100),
        "person_titles": job_titles or None,
        "person_locations": person_locations,
    }
    if industries:
        payload["q_organization_keyword_tags"] = industries
    if company_sizes:
        ranges = _normalize_size_ranges(company_sizes)
        if ranges:
            payload["organization_num_employees_ranges"] = ranges
    if seniorities:
        payload["person_seniorities"] = seniorities
    if keywords:
        payload["q_keywords"] = keywords
    payload = {k: v for k, v in payload.items() if v is not None}

    return _post("mixed_people/api_search", payload, action="Lead search")


def search_companies(
    company_name: str = None,
    industry: str = None,
    country: str = "India",
    page: int = 1,
    per_page: int = 10,
) -> dict:
    payload = {
        "page": page,
        "per_page": min(per_page, 100),
    }
    if country:
        payload["organization_locations"] = [country]
    if company_name:
        payload["q_organization_name"] = company_name
    if industry:
        payload["q_organization_keyword_tags"] = [industry]

    return _post("mixed_companies/search", payload, action="Company search")


def get_people_in_company(
    company_name: str,
    job_titles: list[str] = None,
    country: str = None,
    page: int = 1,
    per_page: int = 10,
) -> dict:
    """Find people inside a named company via people api_search + keyword."""
    payload = {
        "page": page,
        "per_page": min(per_page, 100),
        "q_keywords": company_name,
    }
    if job_titles:
        payload["person_titles"] = job_titles
    if country:
        payload["person_locations"] = [country]

    return _post("mixed_people/api_search", payload, action="Company people search")


def enrich_person(
    apollo_id: str = None,
    email: str = None,
    name: str = None,
    first_name: str = None,
    last_name: str = None,
    company: str = None,
    domain: str = None,
    linkedin_url: str = None,
    reveal_personal_emails: bool = True,
    reveal_phone_number: bool = False,
    webhook_url: str = None,
) -> dict:
    """People Enrichment / Match. COSTS 1 CREDIT per matched person (0 if no match).
    Returns the work email (+ personal emails if revealed) SYNCHRONOUSLY.

    Phone reveal is ASYNC: Apollo rejects reveal_phone_number unless a webhook_url
    is supplied, and then delivers the number to that URL later (not in this
    response). So we only request phone reveal when a webhook_url is given."""
    payload = {}
    if apollo_id:
        payload["id"] = apollo_id
    if email:
        payload["email"] = email
    if name:
        payload["name"] = name
    if first_name:
        payload["first_name"] = first_name
    if last_name:
        payload["last_name"] = last_name
    if company:
        payload["organization_name"] = company
    if domain:
        payload["domain"] = domain
    if linkedin_url:
        payload["linkedin_url"] = linkedin_url

    params = {}
    if reveal_personal_emails:
        params["reveal_personal_emails"] = "true"
    # Apollo 400s on reveal_phone_number without a webhook_url, so only ask for the
    # phone when we have a public endpoint for Apollo to deliver it to.
    if reveal_phone_number and webhook_url:
        params["reveal_phone_number"] = "true"
        params["webhook_url"] = webhook_url

    return _post("people/match", payload, params=params, action="Lead enrichment")


def enrich_company(domain: str = None, name: str = None) -> dict:
    params = {}
    if domain:
        params["domain"] = domain
    if name:
        params["name"] = name
    return _get("organizations/enrich", params, action="Company enrichment")


def test_api_key() -> tuple[bool, str]:
    """Validate the configured key and report exactly what it can do.
    Distinguishes (a) no key, (b) invalid key, (c) valid key but plan lacks
    search-API access (free/Basic), (d) fully working."""
    if not get_api_key():
        return False, "No API key configured. Add APOLLO_API_KEY to .env and restart."

    # 1) Confirm the key itself is valid using an endpoint available on all plans.
    key_valid = False
    try:
        enrich_company(domain="apollo.io")
        key_valid = True
    except ApolloError as e:
        msg = str(e)
        if "401" in msg or "Invalid access" in msg or "credentials" in msg:
            return False, "API key is INVALID. Check it at apollo.io > Settings > Integrations > API."
        # Some other error on the validation call; fall through to the search probe.

    # 2) Probe whether lead-search/enrichment is unlocked on this plan.
    try:
        search_people(job_titles=["Manager"], country="India", per_page=1)
        return True, "API key is valid and lead search is ENABLED. You're fully set up."
    except ApolloError as e:
        msg = str(e)
        if "free plan" in msg or "not accessible" in msg or "upgrade" in msg.lower():
            return False, ("API key is VALID, but your Apollo plan does NOT include the search/"
                           "enrichment API. Upgrade to the Professional plan ($79/mo) to enable "
                           "lead discovery and email/phone enrichment. (Company enrichment works "
                           "on your current plan.)")
        if key_valid:
            return False, f"Key is valid but search failed: {msg}"
        return False, msg
    except Exception as e:
        return False, f"Unexpected error: {e}"


# Apollo credit buckets, in the order we want to show them. (key, label, what it pays for)
_CREDIT_TYPES = [
    ("lead_credit",          "Email / People credits", "Revealing emails (people search & enrichment)"),
    ("direct_dial_credit",   "Mobile number credits",  "Revealing mobile / direct-dial numbers"),
    ("export_credit",        "Export credits",         "Exporting contacts out of Apollo"),
    ("conversation_credit",  "Conversation credits",   "Conversations / sequences"),
    ("dialer",               "Dialer minutes",         "Apollo dialer usage"),
    ("ai_credit",            "AI credits",             "Apollo AI features"),
    ("inbound_website_visitor_credit", "Website visitor credits", "Website visitor identification"),
]


def get_credit_usage() -> dict:
    """Raw Apollo credit balances. POST /usage_stats/credit_usage_stats (spends nothing)."""
    data = _post("usage_stats/credit_usage_stats", {}, action="Apollo credit usage")
    return data.get("credit_usage_stats") or {}


def credit_summary() -> dict:
    """Friendly, UI-ready view of the account's credit balances. Always includes the
    email + mobile buckets; other buckets only when the plan grants them (limit > 0)."""
    raw = get_credit_usage()
    items = []
    for key, label, desc in _CREDIT_TYPES:
        v = raw.get(key)
        if not isinstance(v, dict):
            continue
        limit = int(v.get("limit") or 0)
        consumed = int(v.get("consumed") or 0)
        left = v.get("left_over")
        left = int(left) if left is not None else max(0, limit - consumed)
        if key not in ("lead_credit", "direct_dial_credit") and limit <= 0:
            continue  # hide buckets the plan doesn't include
        pct_used = round(consumed / limit * 100) if limit else (100 if consumed else 0)
        items.append({"key": key, "label": label, "desc": desc, "limit": limit,
                      "consumed": consumed, "left": left, "pct_used": pct_used,
                      "low": limit > 0 and left <= max(1, round(limit * 0.1))})
    return {"items": items}


def is_email_locked(email: str) -> bool:
    if not email:
        return True
    low = email.lower()
    return any(m in low for m in LOCKED_EMAIL_MARKERS)


# Apollo tags each phone with a type; we treat these as a mobile/cell number.
_MOBILE_TYPES = ("mobile", "cell")


def _number_of(p: dict) -> str:
    return (p.get("sanitized_number") or p.get("raw_number") or "").strip()


def extract_mobile(phone_numbers: list) -> str:
    """Return a MOBILE/cell number from an Apollo phone_numbers list, or '' if none.
    Only numbers Apollo already has on file are present (the async 'fresh dial'
    flow returns a request_id instead, which we intentionally ignore — the user
    wants mobiles already updated in Apollo's DB)."""
    for p in phone_numbers or []:
        ptype = (p.get("type") or p.get("type_cd") or "").lower()
        if any(t in ptype for t in _MOBILE_TYPES) and _number_of(p):
            return _number_of(p)
    return ""


def _best_phone(phone_numbers: list) -> str:
    """Mobile if available, otherwise the first phone of any type."""
    mobile = extract_mobile(phone_numbers)
    if mobile:
        return mobile
    for p in phone_numbers or []:
        if _number_of(p):
            return _number_of(p)
    return ""


def parse_person(raw: dict) -> dict:
    """Normalize an Apollo person record into our lead schema.

    NOTE: api_search does NOT return real emails/phones — they come back null or
    as 'email_not_unlocked@domain.com'. We flag those so the app can enrich on demand.
    """
    org = raw.get("organization") or {}
    phones = raw.get("phone_numbers") or []
    mobile = extract_mobile(phones)
    phone = _best_phone(phones)

    raw_email = raw.get("email") or ""
    locked = is_email_locked(raw_email)
    email = "" if locked else raw_email

    name = raw.get("name") or ""
    if not name:
        name = " ".join(p for p in [raw.get("first_name"), raw.get("last_name")] if p).strip()

    # Search results don't expose actual phone DIGITS — Apollo only states whether
    # it HAS a direct/mobile number on file via has_direct_phone ('Yes'/'No'/'Maybe').
    # We use this (credit-free) to keep only contacts with a mobile updated in Apollo.
    phone_hint = str(raw.get("has_direct_phone") or "").strip().lower()
    has_direct_phone = phone_hint == "yes"

    return {
        "apollo_id": raw.get("id"),
        "name": name,
        "title": raw.get("title") or "",
        "company": org.get("name") or raw.get("organization_name") or "",
        "email": email,
        "phone": phone,
        "mobile": mobile,
        "has_mobile": bool(mobile),
        "has_direct_phone": has_direct_phone,
        "has_phone_hint": phone_hint,
        "linkedin_url": raw.get("linkedin_url") or "",
        "city": raw.get("city") or "",
        "state": raw.get("state") or "",
        "country": raw.get("country") or "India",
        "industry": org.get("industry") or "",
        "company_size": org.get("estimated_num_employees") or "",
        "website": org.get("website_url") or org.get("primary_domain") or "",
        "email_status": "locked" if locked else (raw.get("email_status") or "unknown"),
        "enriched": not locked,
    }


def parse_enriched(raw: dict) -> dict:
    """Parse the `person` object returned by People Match into updatable fields."""
    person = raw.get("person") or raw
    org = person.get("organization") or {}
    phones = person.get("phone_numbers") or []
    mobile = extract_mobile(phones)
    phone = _best_phone(phones)

    email = person.get("email") or ""
    if is_email_locked(email):
        personal = person.get("personal_emails") or []
        email = personal[0] if personal else ""

    return {
        "apollo_id": person.get("id"),
        "email": email if not is_email_locked(email) else "",
        "phone": phone,
        "mobile": mobile,
        "has_mobile": bool(mobile),
        "linkedin_url": person.get("linkedin_url") or "",
        "website": org.get("website_url") or "",
        "email_status": person.get("email_status") or "unknown",
        "enriched": bool(email and not is_email_locked(email)),
    }
