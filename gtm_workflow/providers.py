"""Unified data-provider layer. Selects Apollo or Hunter based on the
DATA_PROVIDER env var (default 'hunter' — the free, working option).

Every function returns lead dicts in the SAME schema so the app and DB don't
care which provider produced them.
"""
import os
import apollo_client as apollo
import hunter_client as hunter


def active_provider() -> str:
    p = (os.getenv("DATA_PROVIDER") or "hunter").strip().lower()
    return p if p in ("apollo", "hunter") else "hunter"


def _resolve(provider: str = None) -> str:
    """Resolve the provider for a single request: explicit choice wins, else the
    .env default. Anything unrecognized falls back to Hunter (the working free one)."""
    p = (provider or active_provider() or "hunter").strip().lower()
    return p if p in ("apollo", "hunter") else "hunter"


def provider_status(provider: str = None) -> dict:
    p = _resolve(provider)
    configured = bool(apollo.get_api_key()) if p == "apollo" else bool(hunter.get_api_key())
    return {"provider": p, "configured": configured}


def test_key(provider: str = None) -> tuple[bool, str]:
    return apollo.test_api_key() if _resolve(provider) == "apollo" else hunter.test_api_key()


# ─── Company -> people (the "Search Company" feature) ──────────────────────────

# How many mobile-having search hits to enrich (1 Apollo credit each) per company
# search, to reveal their email (and request their phone via webhook). Bounded so a
# single search stays responsive (~1s/contact) and doesn't burn a pile of credits.
MAX_MOBILE_ENRICH = 10


def apollo_phone_webhook_url() -> str | None:
    """The public URL Apollo should deliver revealed phone numbers to, or None when
    the app is on localhost (Apollo can't reach it, so phone reveal is skipped)."""
    base = (os.getenv("APP_BASE_URL") or "").strip().rstrip("/")
    if not base or "localhost" in base or "127.0.0.1" in base:
        return None
    return base + "/apollo/phone-webhook"


def company_people(company_name: str, role_titles: list[str], country: str = "India",
                   provider: str = None, require_mobile: bool = True) -> list[dict]:
    p = _resolve(provider)
    if p == "apollo":
        result = apollo.get_people_in_company(company_name=company_name,
                                              job_titles=role_titles, per_page=100)
        people = result.get("people") or result.get("contacts") or []
        parsed = [apollo.parse_person(x) for x in people if x.get("name") or x.get("first_name")]
        if not require_mobile:
            return parsed

        # Apollo states (credit-free) whether it holds a direct/mobile number for
        # each person via has_direct_phone. Keep ONLY those — i.e. contacts whose
        # mobile is already updated in Apollo's DB.
        mobile_contacts = [l for l in parsed if l.get("has_direct_phone")][:MAX_MOBILE_ENRICH]

        # Reveal the EMAIL only here (1 credit each). We deliberately DON'T auto-reveal
        # phone numbers in bulk: Apollo charges ~8 credits per mobile, so revealing the
        # whole result set would burn ~80+ credits per search. The mobile is revealed
        # on demand per lead (the lead "Reveal mobile" action), keeping that spend
        # intentional. The "mobile on file" badge tells the user a number is available.
        for lead in mobile_contacts:
            try:
                raw = apollo.enrich_person(
                    apollo_id=lead.get("apollo_id"), name=lead.get("name"),
                    company=company_name, linkedin_url=lead.get("linkedin_url") or None,
                    reveal_personal_emails=True, reveal_phone_number=False)
                fields = apollo.parse_enriched(raw)
                if fields.get("email"):
                    lead["email"] = fields["email"]
                    lead["email_status"] = fields.get("email_status", lead.get("email_status"))
                lead["enriched"] = True
            except apollo.ApolloError as e:
                msg = str(e).lower()
                # Plan/auth/quota problems → surface; per-contact misses → skip.
                if any(t in msg for t in ("403", "401", "free plan", "not accessible",
                                          "upgrade", "credit", "rate limit", "invalid")):
                    raise
                continue
        return mobile_contacts

    # Hunter: domain-search by company name; role keywords RANK (don't discard).
    # Accepts a domain too, so "Search Company" works with either a name or a domain.
    # (Hunter rarely has phone data, so the mobile-only filter is Apollo-specific.)
    hunter._require_key()
    looks_like_domain = ("." in company_name and " " not in company_name)
    result = hunter.domain_search(domain=company_name if looks_like_domain else None,
                                  company=None if looks_like_domain else company_name, limit=10)
    return hunter.parse_domain_search(result, role_keywords=role_titles, country=country)


# ─── Unified "Find Leads" finder (Apollo-only, simple UI) ─────────────────────

# Default buying-committee titles, used only when the user gives no position so a
# demographic search still returns relevant decision-makers rather than everyone.
_FIND_DEFAULT_TITLES = ["Loyalty Manager", "Head of Loyalty", "CRM Manager",
                        "Customer Retention Manager", "Marketing Manager",
                        "Sales Manager", "Founder", "CEO", "Director"]


def _reveal_emails(parsed: list[dict], max_reveal: int) -> None:
    """Unlock the work email (≈1 Apollo credit each) for up to `max_reveal` of the
    parsed people so they're immediately mailer-ready. Mutates in place. Plan/auth/
    quota errors are raised; per-contact misses are skipped."""
    revealed = 0
    for lead in parsed:
        if revealed >= max_reveal:
            break
        if lead.get("email"):
            continue
        try:
            raw = apollo.enrich_person(
                apollo_id=lead.get("apollo_id"), name=lead.get("name"),
                company=lead.get("company") or None,
                linkedin_url=lead.get("linkedin_url") or None,
                reveal_personal_emails=True, reveal_phone_number=False)
            fields = apollo.parse_enriched(raw)
            if fields.get("email"):
                lead["email"] = fields["email"]
                lead["email_status"] = fields.get("email_status", lead.get("email_status"))
            if fields.get("phone") and not lead.get("phone"):
                lead["phone"] = fields["phone"]
            if fields.get("linkedin_url") and not lead.get("linkedin_url"):
                lead["linkedin_url"] = fields["linkedin_url"]
            lead["enriched"] = True
            revealed += 1
        except apollo.ApolloError as e:
            msg = str(e).lower()
            if any(t in msg for t in ("403", "401", "free plan", "not accessible",
                                      "upgrade", "credit", "rate limit", "invalid")):
                raise
            continue


def find_people(mode: str, company_name: str = None, locations: list[str] = None,
                titles: list[str] = None, sizes: list[str] = None,
                seniorities: list[str] = None, country: str = "India",
                per_page: int = 25, reveal_emails: bool = True,
                max_reveal: int = 10, require_mobile: bool = False) -> list[dict]:
    """Apollo-powered people finder backing the simple Find Leads page.

    mode='company'     → everyone (optionally filtered by position) inside a named company.
    mode='demographic' → people matching location / position / company size / seniority.

    Returns lead dicts with company, name, mobile-on-file flag, email (revealed for up
    to `max_reveal`), LinkedIn, etc. — the fields the user asked to see and save."""
    apollo._require_key()

    if mode == "company":
        if not (company_name or "").strip():
            raise apollo.ApolloError("Enter a company name (or domain) to search.")
        result = apollo.get_people_in_company(
            company_name=company_name, job_titles=titles or None,
            country=country or None, per_page=per_page)
    else:
        result = apollo.search_people(
            job_titles=titles or _FIND_DEFAULT_TITLES,
            country=country or "India", locations=locations or None,
            company_sizes=sizes or None, seniorities=seniorities or None,
            per_page=per_page)

    people = result.get("people") or result.get("contacts") or []
    parsed = [apollo.parse_person(x) for x in people if x.get("name") or x.get("first_name")]

    # "Only contacts with a mobile on file" — Apollo states this credit-free via
    # has_direct_phone; the actual digits are revealed on demand per lead later.
    if require_mobile:
        parsed = [l for l in parsed if l.get("has_direct_phone")]

    if reveal_emails and max_reveal > 0:
        _reveal_emails(parsed, max_reveal)

    return parsed


# ─── Discovery ────────────────────────────────────────────────────────────────

def discover(titles: list[str], industries: list[str], sizes: list[str],
             country: str, per_page: int, companies: list[str] = None,
             max_companies: int = 10, provider: str = None,
             strict: bool = False) -> list[dict]:
    """Both providers are driven by INDUSTRY + SIZE (no manual company names).
    Apollo: native firmographic people search.
    Hunter: resolve matching companies from the India directory, then sweep them.
    Role keywords RANK results (decision-makers first); set strict=True to keep
    only exact title matches."""
    p = _resolve(provider)
    if p == "apollo":
        result = apollo.search_people(job_titles=titles, country=country,
                                      industries=industries or None,
                                      company_sizes=sizes or None, per_page=per_page)
        people = result.get("people") or result.get("contacts") or []
        return [apollo.parse_person(x) for x in people if x.get("name") or x.get("first_name")]

    # Hunter: industry/size -> companies (directory) -> people sweep.
    hunter._require_key()
    targets = list(companies or [])
    if not targets:
        from india_companies import find_companies
        matched = find_companies(industries, sizes, limit=max_companies)
        targets = [c["domain"] for c in matched]
    if not targets:
        raise hunter.HunterError(
            "No companies matched those industries/sizes. Select at least one industry "
            "(and optionally widen the size bands).")

    leads = []
    for c in targets:
        c = (c or "").strip()
        if not c:
            continue
        try:
            looks_like_domain = ("." in c and " " not in c)
            result = hunter.domain_search(domain=c if looks_like_domain else None,
                                          company=None if looks_like_domain else c, limit=10)
            leads.extend(hunter.parse_domain_search(result, role_keywords=titles,
                                                    country=country, strict=strict))
        except hunter.HunterError as e:
            msg = str(e).lower()
            if any(t in msg for t in ("key", "401", "403", "invalid", "credit", "rate limit")):
                raise  # config / auth / quota — surface to the user
            continue  # per-company failure (e.g. not found) — keep sweeping

    # Rank globally: function matches (loyalty/CRM/sales) first, then other
    # decision-makers, then the rest — stable so each company's people stay grouped.
    leads.sort(key=lambda l: l.get("rank", 2))
    return leads


def resolve_companies(industries: list[str], sizes: list[str], limit: int = 10) -> list[dict]:
    """Preview which directory companies a given industry/size selection will sweep
    (used by the UI to show count + credit cost before spending)."""
    from india_companies import find_companies
    return find_companies(industries, sizes, limit=limit)


# ─── Single-lead enrichment (unlock email/phone) ──────────────────────────────

def enrich_lead_fields(lead, provider: str = None, reveal_phone: bool = False) -> dict:
    """Return updated {email, phone, linkedin_url, email_status, enriched} for a lead.
    Apollo: People Match (1 credit for email; +~8 credits & async webhook if
    reveal_phone). Hunter: Email Finder (1 credit)."""
    p = _resolve(provider)
    if p == "apollo":
        # Phone reveal costs ~8 Apollo credits and is async (webhook), so it's opt-in
        # via reveal_phone; the default enrich only reveals the email (1 credit).
        webhook = apollo_phone_webhook_url() if reveal_phone else None
        raw = apollo.enrich_person(apollo_id=lead.apollo_id, name=lead.name,
                                   company=lead.company, linkedin_url=lead.linkedin_url or None,
                                   reveal_personal_emails=True,
                                   reveal_phone_number=bool(webhook), webhook_url=webhook)
        return apollo.parse_enriched(raw)
    # Hunter
    result = hunter.email_finder(company=lead.company, full_name=lead.name)
    parsed = hunter.parse_email_finder(result, fallback_name=lead.name, company=lead.company)
    if not parsed:
        return {"email": "", "phone": "", "linkedin_url": lead.linkedin_url or "",
                "email_status": "unknown", "enriched": False}
    return {"email": parsed["email"], "phone": parsed["phone"],
            "linkedin_url": parsed["linkedin_url"] or (lead.linkedin_url or ""),
            "email_status": parsed["email_status"], "enriched": parsed["enriched"]}


# Re-export so callers can catch a single error type.
ApolloError = apollo.ApolloError
HunterError = hunter.HunterError
ProviderError = (apollo.ApolloError, hunter.HunterError)
