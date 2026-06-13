import os, json, threading, time
from datetime import datetime
from flask import Flask, request, jsonify, render_template, redirect, url_for, send_file, Response, session
from dotenv import load_dotenv
from sqlalchemy import text, inspect
from database import db, Lead, Campaign, CampaignContact, AutoDiscoveryConfig
import providers
from email_client import send_email, personalize, build_plain_text, smtp_configured, test_smtp_login
import reply_tracker
import io, csv

# Anchor .env and the SQLite DB to THIS file's folder, so the app behaves the same
# no matter which working directory it's launched from (a common cause of "missing
# API keys" or "empty database" when started a different way).
BASEDIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASEDIR, ".env"))


def _resolve_db_uri() -> str:
    uri = os.getenv("DATABASE_URL", "").strip()
    if not uri:
        return "sqlite:///" + os.path.join(BASEDIR, "gtm.db").replace("\\", "/")
    # Managed Postgres (Render/Heroku/Railway) hands out 'postgres://', which
    # SQLAlchemy 2.x no longer accepts — normalize to 'postgresql://'.
    if uri.startswith("postgres://"):
        uri = "postgresql://" + uri[len("postgres://"):]
    # Anchor a *relative* sqlite path to BASEDIR (absolute paths/other DBs untouched).
    if uri.startswith("sqlite:///") and not uri.startswith("sqlite:////"):
        rel = uri[len("sqlite:///"):]
        if not os.path.isabs(rel) and ":" not in rel.split("/")[0]:
            return "sqlite:///" + os.path.join(BASEDIR, rel).replace("\\", "/")
    return uri


app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = _resolve_db_uri()
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret")
db.init_app(app)


def _base_url() -> str:
    # Read at call time so Settings-page changes apply without a restart.
    return os.getenv("APP_BASE_URL", "http://localhost:5000")


# ─── ACCESS CONTROL ───────────────────────────────────────────────────────────
# Protects the app UI once it's on a public URL. Two modes, picked automatically:
#   1. "Sign in with Google" restricted to ONE address (ALLOWED_GOOGLE_EMAIL,
#      default info@aashishagency.com) — active when GOOGLE_CLIENT_ID/SECRET are set.
#   2. Otherwise, if APP_USERNAME/APP_PASSWORD are set, HTTP basic-auth fallback.
#   3. Otherwise, no login (local dev).
# The Apollo phone webhook and email open/click tracking endpoints MUST stay public
# (Apollo's servers and recipients' mail clients call them with no credentials).
import secrets as _secrets

_PUBLIC_PREFIXES = ("/track/open/", "/track/click/", "/apollo/phone-webhook", "/healthz")
_LOGIN_PREFIXES = ("/login", "/auth/", "/static/", "/logout")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


def _google_login_enabled() -> bool:
    return bool(os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"))


def _basic_login_enabled() -> bool:
    return bool(os.getenv("APP_USERNAME") and os.getenv("APP_PASSWORD"))


def allowed_login_emails() -> set:
    """The Google accounts authorized to use the app. Anyone may attempt to sign in
    with Google, but only these addresses are let through."""
    raw = (os.getenv("ALLOWED_GOOGLE_EMAILS") or os.getenv("ALLOWED_GOOGLE_EMAIL")
           or "info@aashishagency.com,sujit@aashishagency.com")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


@app.before_request
def _access_control():
    path = request.path
    if any(path.startswith(p) for p in _PUBLIC_PREFIXES):
        return

    # Mode 1: Google sign-in (restricted to the one allowed address).
    if _google_login_enabled():
        if session.get("user_email"):
            return
        if any(path.startswith(p) for p in _LOGIN_PREFIXES):
            return
        return redirect(url_for("login", next=path))

    # Mode 2: basic-auth fallback.
    if _basic_login_enabled():
        auth = request.authorization
        if (not auth or auth.username != os.getenv("APP_USERNAME")
                or not _secrets.compare_digest(auth.password or "", os.getenv("APP_PASSWORD", ""))):
            return Response("Login required.", 401,
                            {"WWW-Authenticate": 'Basic realm="GTM Workflow"'})
        return
    # Mode 3: open (local dev).


@app.route("/login")
def login():
    if not _google_login_enabled():
        return redirect("/")
    if session.get("user_email"):
        return redirect("/")
    return render_template("login.html", allowed=", ".join(sorted(allowed_login_emails())),
                           error=request.args.get("error"))


@app.route("/auth/google")
def auth_google():
    if not _google_login_enabled():
        return redirect("/")
    from urllib.parse import urlencode
    state = _secrets.token_urlsafe(24)
    session["oauth_state"] = state
    nxt = request.args.get("next", "/")
    session["oauth_next"] = nxt if nxt.startswith("/") else "/"
    redirect_uri = _base_url().rstrip("/") + "/auth/google/callback"
    params = {
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
        # Open to any Google account (login "for all"); the allowlist is enforced
        # server-side in the callback, so unauthorized accounts are rejected there.
    }
    return redirect(GOOGLE_AUTH_URL + "?" + urlencode(params))


@app.route("/auth/google/callback")
def auth_google_callback():
    import requests as _r
    if request.args.get("error"):
        return redirect(url_for("login", error="Google sign-in was cancelled."))
    if not request.args.get("state") or request.args.get("state") != session.get("oauth_state"):
        return redirect(url_for("login", error="Sign-in expired, please try again."))
    code = request.args.get("code")
    if not code:
        return redirect(url_for("login", error="No authorization code returned."))
    redirect_uri = _base_url().rstrip("/") + "/auth/google/callback"
    try:
        tok = _r.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": os.getenv("GOOGLE_CLIENT_ID"),
            "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }, timeout=20).json()
        access_token = tok.get("access_token")
        if not access_token:
            return redirect(url_for("login", error="Google rejected the sign-in (check OAuth setup)."))
        info = _r.get(GOOGLE_USERINFO_URL,
                      headers={"Authorization": f"Bearer {access_token}"}, timeout=20).json()
    except Exception:
        return redirect(url_for("login", error="Could not reach Google. Try again."))

    email = (info.get("email") or "").strip().lower()
    verified = str(info.get("email_verified", "")).lower() in ("true", "1") or info.get("email_verified") is True
    if not email or not verified or email not in allowed_login_emails():
        allowed_str = " or ".join(sorted(allowed_login_emails()))
        return redirect(url_for("login",
                                error=f"Access is limited to {allowed_str}. "
                                      f"You signed in as {email or 'an unknown account'}."))
    session["user_email"] = email
    return redirect(session.pop("oauth_next", "/"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login" if _google_login_enabled() else "/")


def _ensure_columns():
    """Lightweight migration: add columns introduced after a DB already exists.
    SQLite only; avoids forcing a destructive db rebuild on upgrade."""
    insp = inspect(db.engine)
    if "leads" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("leads")}
    add = []
    if "email_status" not in existing:
        add.append("ALTER TABLE leads ADD COLUMN email_status VARCHAR(50) DEFAULT 'unknown'")
    if "enriched" not in existing:
        add.append("ALTER TABLE leads ADD COLUMN enriched BOOLEAN DEFAULT 0")
    if "has_mobile_on_file" not in existing:
        add.append("ALTER TABLE leads ADD COLUMN has_mobile_on_file BOOLEAN DEFAULT 0")
    if "campaign_contacts" in insp.get_table_names():
        cc_cols = {c["name"] for c in insp.get_columns("campaign_contacts")}
        if "message_id" not in cc_cols:
            add.append("ALTER TABLE campaign_contacts ADD COLUMN message_id VARCHAR(300)")
    for stmt in add:
        db.session.execute(text(stmt))
    if add:
        db.session.commit()


with app.app_context():
    db.create_all()
    _ensure_columns()
    if not AutoDiscoveryConfig.query.first():
        db.session.add(AutoDiscoveryConfig())
        db.session.commit()


# ─── DASHBOARD ───────────────────────────────────────────────────────────────

def _campaign_stats(campaign_id) -> dict:
    """Sent/delivered/opened/clicked/replied/bounced counts for one campaign.
    delivered = accepted by SMTP and no bounce notice came back."""
    rows = CampaignContact.query.filter_by(campaign_id=campaign_id).all()
    sent = sum(1 for cc in rows if cc.sent_at is not None)
    bounced = sum(1 for cc in rows if cc.bounced_at is not None or cc.status == "bounced")
    return {
        "total": len(rows), "sent": sent, "bounced": bounced,
        "delivered": max(0, sent - bounced),
        "opened": sum(1 for cc in rows if cc.open_count > 0),
        "clicked": sum(1 for cc in rows if cc.click_count > 0),
        "replied": sum(1 for cc in rows if cc.replied_at is not None),
    }


@app.route("/")
def dashboard():
    total_leads = Lead.query.count()
    total_campaigns = Campaign.query.count()
    # "Sent" = actually dispatched (has a sent_at), not merely non-pending — so
    # failed/bounced rows don't inflate the denominator for open/click/reply rates.
    total_sent = CampaignContact.query.filter(CampaignContact.sent_at.isnot(None)).count()
    total_bounced = CampaignContact.query.filter(
        db.or_(CampaignContact.bounced_at.isnot(None), CampaignContact.status == "bounced")).count()
    total_delivered = max(0, total_sent - total_bounced)
    total_opened = CampaignContact.query.filter(CampaignContact.open_count > 0).count()
    total_clicked = CampaignContact.query.filter(CampaignContact.click_count > 0).count()
    total_replied = CampaignContact.query.filter(CampaignContact.replied_at.isnot(None)).count()
    recent_leads = Lead.query.order_by(Lead.created_at.desc()).limit(10).all()
    campaigns = Campaign.query.order_by(Campaign.created_at.desc()).limit(5).all()
    campaign_rows = [{"c": c, "s": _campaign_stats(c.id)} for c in campaigns]
    config = AutoDiscoveryConfig.query.first()

    delivery_rate = round((total_delivered / total_sent * 100) if total_sent else 0, 1)
    open_rate = round((total_opened / total_sent * 100) if total_sent else 0, 1)
    click_rate = round((total_clicked / total_sent * 100) if total_sent else 0, 1)
    reply_rate = round((total_replied / total_sent * 100) if total_sent else 0, 1)

    return render_template("dashboard.html",
        total_leads=total_leads, total_campaigns=total_campaigns,
        total_sent=total_sent, total_delivered=total_delivered, total_opened=total_opened,
        total_clicked=total_clicked, total_replied=total_replied, total_bounced=total_bounced,
        delivery_rate=delivery_rate, open_rate=open_rate, click_rate=click_rate, reply_rate=reply_rate,
        recent_leads=recent_leads, campaigns=campaigns, campaign_rows=campaign_rows, config=config,
        smtp_ok=smtp_configured(), smtp_user=os.getenv("SMTP_USER", ""),
    )


# ─── LEAD DISCOVERY ──────────────────────────────────────────────────────────

@app.route("/leads")
def leads_list():
    status_filter = request.args.get("status", "")
    q = request.args.get("q", "")
    page = int(request.args.get("page", 1))
    per_page = 20

    query = Lead.query
    if status_filter:
        query = query.filter(Lead.status == status_filter)
    if q:
        query = query.filter(
            db.or_(Lead.name.ilike(f"%{q}%"), Lead.company.ilike(f"%{q}%"),
                   Lead.email.ilike(f"%{q}%"), Lead.phone.ilike(f"%{q}%"))
        )
    pagination = query.order_by(Lead.created_at.desc()).paginate(page=page, per_page=per_page)
    return render_template("leads.html", pagination=pagination, q=q, status_filter=status_filter)


def _save_leads(parsed_leads):
    """De-dup and persist a list of parsed lead dicts. Dedup by apollo_id when
    present (Apollo), otherwise by email (Hunter). Returns (saved, skipped, objs)."""
    saved, skipped = 0, 0
    new_leads = []
    for parsed in parsed_leads:
        if not parsed.get("name"):
            skipped += 1
            continue
        existing = None
        if parsed.get("apollo_id"):
            existing = Lead.query.filter_by(apollo_id=parsed["apollo_id"]).first()
        elif parsed.get("email"):
            existing = Lead.query.filter_by(email=parsed["email"]).first()
        if existing:
            skipped += 1
            continue
        # Only keep fields that exist on the model.
        fields = {k: v for k, v in parsed.items()
                  if k in ("apollo_id", "name", "title", "company", "email", "phone",
                           "linkedin_url", "city", "state", "country", "industry",
                           "company_size", "website", "email_status", "enriched", "source")}
        fields["company_size"] = str(fields.get("company_size") or "")
        fields["has_mobile_on_file"] = bool(parsed.get("has_direct_phone") or parsed.get("has_mobile"))
        lead = Lead(**fields)
        db.session.add(lead)
        new_leads.append(lead)
        saved += 1
    db.session.commit()
    return saved, skipped, new_leads


# Default role keywords = the loyalty/CRM buying committee. Used to RANK Hunter
# results (decision-makers first) and as Apollo person_titles.
DEFAULT_TITLES = ["Loyalty Manager", "Dealer Loyalty Manager", "Head of Loyalty",
                  "CRM Manager", "Customer Retention Manager", "Customer Experience",
                  "Marketing Manager", "Sales Manager", "Channel Manager", "Dealer"]


def _discover_and_save(titles, industries, sizes, country, per_page,
                       auto_enrich=False, companies=None, max_companies=10,
                       provider=None, strict=False):
    """Shared discovery routine (manual route, auto 'run now', scheduler).
    Returns (saved, skipped, new_lead_objs). Provider-agnostic."""
    parsed_leads = providers.discover(
        titles=titles or DEFAULT_TITLES,
        industries=industries, sizes=sizes,
        country=country or "India", per_page=per_page,
        companies=companies, max_companies=max_companies,
        provider=provider, strict=strict)

    saved, skipped, new_leads = _save_leads(parsed_leads)

    if auto_enrich:
        for lead in new_leads:
            if lead.enriched:
                continue
            try:
                _enrich_lead_obj(lead, provider=provider)
            except Exception:
                pass  # enrichment failures shouldn't lose the discovered lead
        db.session.commit()

    return saved, skipped, new_leads


def _enrich_lead_obj(lead, provider=None, reveal_phone=False) -> bool:
    """Unlock email (and optionally mobile) for one lead via the chosen provider.
    Email ≈1 credit; Apollo mobile reveal ≈8 credits and arrives async via webhook.
    Mutates the lead in place; caller commits."""
    fields = providers.enrich_lead_fields(lead, provider=provider, reveal_phone=reveal_phone)
    if fields.get("email"):
        lead.email = fields["email"]
    if fields.get("phone"):
        lead.phone = fields["phone"]
    if fields.get("linkedin_url") and not lead.linkedin_url:
        lead.linkedin_url = fields["linkedin_url"]
    lead.email_status = fields.get("email_status", lead.email_status)
    lead.enriched = bool(fields.get("enriched"))
    return lead.enriched


@app.route("/leads/discover", methods=["GET", "POST"])
def discover_leads():
    if request.method == "GET":
        return render_template("discover.html")

    data = request.form
    titles = [t.strip() for t in data.get("titles", "").split(",") if t.strip()]
    # Industries & sizes drive discovery (checkboxes -> comma list).
    industries = [i.strip() for i in data.get("industries", "").split(",") if i.strip()]
    sizes = [s.strip() for s in data.get("sizes", "").split(",") if s.strip()]
    country = data.get("country", "India")
    per_page = int(data.get("per_page", 25))
    max_companies = int(data.get("max_companies", 10))
    auto_enrich = data.get("auto_enrich") in ("on", "true", "1")
    provider = data.get("provider") or None
    strict = data.get("strict") in ("on", "true", "1")

    try:
        saved, skipped, new_leads = _discover_and_save(
            titles, industries, sizes, country, per_page,
            auto_enrich=auto_enrich, max_companies=max_companies,
            provider=provider, strict=strict)
        used = providers._resolve(provider)
        # Helpful, specific message when nothing came back — so "no results" is
        # never a silent dead end.
        note = ""
        if saved == 0 and skipped == 0:
            if strict:
                note = ("No exact title matches found. Untick “Strict title match” to see all "
                        "decision-makers at these companies (ranked by relevance).")
            elif used == "hunter":
                note = ("No contacts returned for the matched companies. Try more/other industries, "
                        "widen the size bands, or increase “Max companies per run”.")
            else:
                note = "No people matched those filters."
        elif saved == 0 and skipped > 0:
            note = f"All {skipped} matching contacts are already in your leads."
        return jsonify({"success": True, "saved": saved, "skipped": skipped,
                        "enriched": auto_enrich, "provider": used, "note": note,
                        "leads": [_lead_to_dict(l) for l in new_leads]})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/companies/preview")
def companies_preview():
    """Preview which directory companies an industry/size selection will sweep,
    so the UI can show the count + Hunter credit cost before spending."""
    industries = [i.strip() for i in request.args.get("industries", "").split(",") if i.strip()]
    sizes = [s.strip() for s in request.args.get("sizes", "").split(",") if s.strip()]
    limit = int(request.args.get("limit", 10))
    matched = providers.resolve_companies(industries, sizes, limit=limit)
    return jsonify({"count": len(matched),
                    "companies": [{"name": c["name"], "industry": c["industry"], "size": c["size"]}
                                  for c in matched]})


@app.route("/api/industries")
def api_industries():
    from india_companies import all_industries
    return jsonify({"industries": all_industries()})


@app.route("/leads/<lead_id>/enrich", methods=["POST"])
def enrich_lead(lead_id):
    """Reveal a single lead's EMAIL (consumes ~1 Apollo credit)."""
    lead = Lead.query.get_or_404(lead_id)
    try:
        ok = _enrich_lead_obj(lead)
        db.session.commit()
        return jsonify({"success": True, "enriched": ok, "email": lead.email,
                        "phone": lead.phone, "email_status": lead.email_status})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/leads/<lead_id>/reveal-phone", methods=["POST"])
def reveal_phone(lead_id):
    """Request the lead's MOBILE from Apollo (~8 credits). Apollo delivers the
    number ASYNC to /apollo/phone-webhook, which writes it onto the lead a few
    seconds later — so the number isn't in this immediate response."""
    lead = Lead.query.get_or_404(lead_id)
    if not providers.apollo_phone_webhook_url():
        return jsonify({"success": False,
                        "error": "Mobile reveal needs a public APP_BASE_URL so Apollo can "
                                 "deliver the number. Set it in Settings (or deploy)."}), 400
    try:
        _enrich_lead_obj(lead, reveal_phone=True)  # triggers async webhook delivery
        db.session.commit()
        return jsonify({"success": True, "pending": not bool(lead.phone),
                        "phone": lead.phone, "email": lead.email})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/leads/enrich-bulk", methods=["POST"])
def enrich_bulk():
    """Bulk-unlock selected leads. Consumes up to 1 credit per lead."""
    lead_ids = (request.json or {}).get("lead_ids", [])
    enriched, failed = 0, 0
    for lid in lead_ids:
        lead = Lead.query.get(lid)
        if not lead or lead.enriched:
            continue
        try:
            if _enrich_lead_obj(lead):
                enriched += 1
            db.session.commit()
            time.sleep(0.3)
        except Exception:
            failed += 1
            db.session.rollback()
    return jsonify({"success": True, "enriched": enriched, "failed": failed})


@app.route("/leads/search-company", methods=["GET", "POST"])
def search_company():
    if request.method == "GET":
        return render_template("search_company.html")

    data = request.json or request.form
    company_name = data.get("company_name", "")
    titles = data.get("titles", "")
    if isinstance(titles, str):
        titles = [t.strip() for t in titles.split(",") if t.strip()]
    provider = data.get("provider") or None
    used = providers._resolve(provider)
    # Apollo searches return ONLY contacts with a mobile number on file (default);
    # can be turned off from the UI. Hunter ignores this (it has no phone data).
    rm = data.get("require_mobile")
    require_mobile = True if rm is None else (rm in (True, "true", "on", "1", 1))

    try:
        leads = providers.company_people(
            company_name=company_name,
            role_titles=titles or ["Loyalty", "CRM", "Customer Retention", "Dealer", "Manager"],
            country=data.get("country", "India"),
            provider=provider, require_mobile=require_mobile,
        )
        leads = [l for l in leads if l.get("name")]
        note = ""
        if used == "apollo" and require_mobile:
            note = (f"{len(leads)} contact(s) with a mobile number on file in Apollo. "
                    "Contacts without a stored mobile were excluded.")
        return jsonify({"success": True, "people": leads, "total": len(leads),
                        "provider": used, "require_mobile": require_mobile, "note": note})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/apollo/phone-webhook", methods=["POST"])
def apollo_phone_webhook():
    """Receives Apollo's ASYNC phone-reveal callback and writes the mobile number
    onto the matching saved lead (by apollo_id). Apollo posts either a single
    person or a list, each with id + phone_numbers."""
    import apollo_client as apollo
    # Capture Apollo's raw callback once so we can confirm/adjust the payload shape.
    try:
        raw_body = request.get_data(as_text=True)
        with open(os.path.join(BASEDIR, "apollo_webhook_last.json"), "w", encoding="utf-8") as _f:
            _f.write(raw_body or "")
        app.logger.info(f"[apollo-webhook] raw payload: {(raw_body or '')[:1500]}")
    except Exception:
        pass
    data = request.get_json(silent=True) or {}
    # Apollo may wrap the people list under various keys depending on the call.
    people = (data.get("people") or data.get("contacts") or data.get("matches")
              or ([data.get("person")] if data.get("person") else None)
              or ([data] if data.get("id") or data.get("phone_numbers") else []))
    updated = 0
    for person in people:
        if not isinstance(person, dict):
            continue
        aid = person.get("id") or person.get("person_id")
        mobile = apollo.extract_mobile(person.get("phone_numbers") or [])
        if not (aid and mobile):
            continue
        lead = Lead.query.filter_by(apollo_id=aid).first()
        if lead:
            lead.phone = mobile
            lead.enriched = True
            updated += 1
    if updated:
        db.session.commit()
    return jsonify({"received": True, "updated": updated})


@app.route("/leads/add-from-search", methods=["POST"])
def add_lead_from_search():
    data = request.json
    existing = None
    if data.get("apollo_id"):
        existing = Lead.query.filter_by(apollo_id=data.get("apollo_id")).first()
    elif data.get("email"):
        existing = Lead.query.filter_by(email=data.get("email")).first()
    if existing:
        return jsonify({"success": False, "error": "Lead already exists", "id": existing.id})
    lead = Lead(
        apollo_id=data.get("apollo_id"),
        name=data.get("name", ""),
        title=data.get("title", ""),
        company=data.get("company", ""),
        email=data.get("email", ""),
        phone=data.get("phone", ""),
        linkedin_url=data.get("linkedin_url", ""),
        city=data.get("city", ""),
        state=data.get("state", ""),
        country=data.get("country", "India"),
        industry=data.get("industry", ""),
        company_size=str(data.get("company_size", "")),
        website=data.get("website", ""),
        email_status=data.get("email_status", "unknown"),
        enriched=bool(data.get("enriched")),
        has_mobile_on_file=bool(data.get("has_direct_phone") or data.get("has_mobile")),
        source=data.get("source", "apollo"),
    )
    db.session.add(lead)
    db.session.commit()
    return jsonify({"success": True, "id": lead.id})


@app.route("/leads/<lead_id>")
def lead_detail(lead_id):
    lead = Lead.query.get_or_404(lead_id)
    contacts = CampaignContact.query.filter_by(lead_id=lead_id).all()
    return render_template("lead_detail.html", lead=lead, contacts=contacts)


@app.route("/leads/<lead_id>/update", methods=["POST"])
def update_lead(lead_id):
    lead = Lead.query.get_or_404(lead_id)
    for field in ["name", "title", "company", "email", "phone", "linkedin_url", "status", "notes", "tags"]:
        if field in request.form:
            setattr(lead, field, request.form[field])
    db.session.commit()
    return redirect(url_for("lead_detail", lead_id=lead_id))


@app.route("/leads/<lead_id>/delete", methods=["POST"])
def delete_lead(lead_id):
    lead = Lead.query.get_or_404(lead_id)
    db.session.delete(lead)
    db.session.commit()
    return jsonify({"success": True})


@app.route("/leads/export")
def export_leads():
    leads = Lead.query.order_by(Lead.created_at.desc()).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Name", "Title", "Company", "Email", "Phone", "LinkedIn", "City", "State", "Industry", "Company Size", "Website", "Status", "Tags", "Created At"])
    for l in leads:
        writer.writerow([l.name, l.title, l.company, l.email, l.phone, l.linkedin_url,
                         l.city, l.state, l.industry, l.company_size, l.website,
                         l.status, l.tags, l.created_at.strftime("%Y-%m-%d %H:%M")])
    output.seek(0)
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment;filename=leads_export.csv"})


# ─── AUTO DISCOVERY CONFIG ────────────────────────────────────────────────────

@app.route("/auto-discovery", methods=["GET", "POST"])
def auto_discovery():
    config = AutoDiscoveryConfig.query.first()
    if request.method == "POST":
        config.enabled = request.form.get("enabled") == "on"
        config.job_titles = request.form.get("job_titles", config.job_titles)
        config.industries = request.form.get("industries", config.industries)
        config.company_sizes = request.form.get("company_sizes", config.company_sizes)
        config.country = request.form.get("country", config.country)
        config.run_interval_hours = int(request.form.get("run_interval_hours", 24))
        config.leads_per_run = int(request.form.get("leads_per_run", 25))
        db.session.commit()
        return jsonify({"success": True})
    return render_template("auto_discovery.html", config=config)


def _run_config_discovery(config):
    """Run one discovery pass from a saved AutoDiscoveryConfig. Returns (saved, skipped)."""
    titles = [t.strip() for t in (config.job_titles or "").split(",") if t.strip()]
    industries = [i.strip() for i in (config.industries or "").split(",") if i.strip()]
    sizes = [s.strip() for s in (config.company_sizes or "").split(",") if s.strip()]
    saved, skipped, _ = _discover_and_save(
        titles, industries, sizes, config.country, config.leads_per_run,
        auto_enrich=False, max_companies=config.leads_per_run)
    config.last_run_at = datetime.utcnow()
    config.total_leads_fetched = (config.total_leads_fetched or 0) + saved
    db.session.commit()
    return saved, skipped


@app.route("/auto-discovery/run-now", methods=["POST"])
def run_auto_discovery_now():
    config = AutoDiscoveryConfig.query.first()
    try:
        saved, skipped = _run_config_discovery(config)
        return jsonify({"success": True, "saved": saved, "skipped": skipped})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─── BACKGROUND SCHEDULER (continuous discovery) ──────────────────────────────

def _scheduled_discovery_tick():
    """Fires hourly; runs discovery only if enabled and the configured interval
    has elapsed since the last run. This is what makes discovery 'continuous'."""
    with app.app_context():
        config = AutoDiscoveryConfig.query.first()
        if not config or not config.enabled:
            return
        interval = max(1, config.run_interval_hours or 24)
        if config.last_run_at:
            elapsed_h = (datetime.utcnow() - config.last_run_at).total_seconds() / 3600
            if elapsed_h < interval:
                return
        try:
            saved, skipped = _run_config_discovery(config)
            app.logger.info(f"[scheduler] auto-discovery saved={saved} skipped={skipped}")
        except Exception as e:
            app.logger.error(f"[scheduler] auto-discovery failed: {e}")


def _apply_inbox_events(events) -> dict:
    """Map IMAP reply/bounce events onto CampaignContact rows. Returns counts."""
    replied, bounced = 0, 0
    sent_ccs = CampaignContact.query.filter(CampaignContact.sent_at.isnot(None)).all()
    by_message_id = {cc.message_id: cc for cc in sent_ccs if cc.message_id}
    by_lead_email = {}
    for cc in sent_ccs:
        lead = Lead.query.get(cc.lead_id)
        if lead and lead.email:
            by_lead_email.setdefault(lead.email.lower(), []).append(cc)

    for ev in events:
        if ev["type"] == "bounce":
            for addr in ev["failed_emails"]:
                for cc in by_lead_email.get(addr.lower(), []):
                    if not cc.bounced_at:
                        cc.bounced_at = datetime.utcnow()
                        cc.status = "bounced"
                        bounced += 1
            continue
        # Reply: exact threading match first, then sender-address + "Re:" fallback.
        matches = [by_message_id[m] for m in ev["refs"] if m in by_message_id]
        if not matches and ev["from_email"] in by_lead_email and \
                ev["subject"].lower().startswith(("re:", "re :")):
            matches = by_lead_email[ev["from_email"]]
        for cc in matches:
            if not cc.replied_at:
                cc.replied_at = datetime.utcnow()
                cc.status = "replied"
                lead = Lead.query.get(cc.lead_id)
                if lead:
                    lead.status = "replied"
                replied += 1
    if replied or bounced:
        db.session.commit()
    return {"replied": replied, "bounced": bounced, "scanned": len(events)}


def _reply_tracking_tick():
    """Every 10 min: scan the sender inbox for replies & bounces (skips silently
    until SMTP/IMAP credentials are configured)."""
    if not reply_tracker.imap_configured():
        return
    with app.app_context():
        try:
            events = reply_tracker.fetch_inbox_events(since_days=3)
            counts = _apply_inbox_events(events)
            if counts["replied"] or counts["bounced"]:
                app.logger.info(f"[reply-tracker] {counts}")
        except Exception as e:
            app.logger.error(f"[reply-tracker] failed: {e}")


def start_scheduler():
    """Start APScheduler once (guard against Flask reloader double-start)."""
    from apscheduler.schedulers.background import BackgroundScheduler
    sched = BackgroundScheduler(daemon=True, timezone="UTC")
    # Check every hour; the tick itself respects each config's interval.
    sched.add_job(_scheduled_discovery_tick, "interval", hours=1,
                  id="auto_discovery", next_run_time=datetime.utcnow())
    # Auto reply/bounce detection from the sender inbox.
    sched.add_job(_reply_tracking_tick, "interval", minutes=10, id="reply_tracking")
    sched.start()
    return sched


# ─── CAMPAIGNS ───────────────────────────────────────────────────────────────

@app.route("/campaigns")
def campaigns_list():
    campaigns = Campaign.query.order_by(Campaign.created_at.desc()).all()
    return render_template("campaigns.html", campaigns=campaigns)


@app.route("/campaigns/new", methods=["GET", "POST"])
def new_campaign():
    if request.method == "GET":
        leads = Lead.query.filter(Lead.email.isnot(None), Lead.email != "").order_by(Lead.created_at.desc()).all()
        return render_template("campaign_editor.html", campaign=None, leads=leads,
                               default_from_name=os.getenv("FROM_NAME", ""),
                               smtp_user=os.getenv("SMTP_USER", ""), smtp_ok=smtp_configured())

    data = request.form
    campaign = Campaign(
        name=data["name"],
        subject=data["subject"],
        body_html=data["body_html"],
        body_text=data.get("body_text", ""),
        from_name=data.get("from_name", os.getenv("FROM_NAME", "")),
        from_email=data.get("from_email", os.getenv("SMTP_USER", "")),
    )
    db.session.add(campaign)
    db.session.flush()

    lead_ids = request.form.getlist("lead_ids")
    for lid in lead_ids:
        cc = CampaignContact(campaign_id=campaign.id, lead_id=lid)
        db.session.add(cc)

    db.session.commit()
    return jsonify({"success": True, "id": campaign.id})


@app.route("/campaigns/<campaign_id>")
def campaign_detail(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)
    contacts = (db.session.query(CampaignContact, Lead)
                .join(Lead, CampaignContact.lead_id == Lead.id)
                .filter(CampaignContact.campaign_id == campaign_id)
                .all())
    total = len(contacts)
    sent = sum(1 for cc, _ in contacts if cc.sent_at is not None)
    opened = sum(1 for cc, _ in contacts if cc.open_count > 0)
    clicked = sum(1 for cc, _ in contacts if cc.click_count > 0)
    replied = sum(1 for cc, _ in contacts if cc.replied_at)
    bounced = sum(1 for cc, _ in contacts if cc.bounced_at is not None or cc.status == "bounced")
    delivered = max(0, sent - bounced)
    sendable = sum(1 for cc, _ in contacts if cc.status in ("pending", "failed"))
    return render_template("campaign_detail.html", campaign=campaign, contacts=contacts,
                           total=total, sent=sent, delivered=delivered, opened=opened,
                           clicked=clicked, replied=replied, bounced=bounced,
                           sendable=sendable, smtp_ok=smtp_configured())


@app.route("/campaigns/<campaign_id>/edit", methods=["GET", "POST"])
def edit_campaign(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)
    if request.method == "GET":
        leads = Lead.query.filter(Lead.email.isnot(None), Lead.email != "").order_by(Lead.created_at.desc()).all()
        added_ids = {cc.lead_id for cc in campaign.contacts}
        return render_template("campaign_editor.html", campaign=campaign, leads=leads, added_ids=added_ids,
                               default_from_name=os.getenv("FROM_NAME", ""),
                               smtp_user=os.getenv("SMTP_USER", ""), smtp_ok=smtp_configured())

    data = request.form
    campaign.name = data.get("name", campaign.name)
    campaign.subject = data.get("subject", campaign.subject)
    campaign.body_html = data.get("body_html", campaign.body_html)
    campaign.body_text = data.get("body_text", campaign.body_text)
    db.session.commit()
    return jsonify({"success": True})


@app.route("/campaigns/<campaign_id>/send", methods=["POST"])
def send_campaign(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)
    if campaign.status == "active":
        return jsonify({"success": False, "error": "Campaign is already sending"})
    if not smtp_configured():
        return jsonify({"success": False,
                        "error": "Can't send: SMTP App Password not configured. "
                                 "Add it in Settings, then try again."})

    # Include previously-failed contacts so a campaign can be retried after a
    # config fix instead of dead-ending.
    pending_ids = [cc.id for cc in
                   CampaignContact.query.filter(
                       CampaignContact.campaign_id == campaign_id,
                       CampaignContact.status.in_(("pending", "failed"))).all()]
    if not pending_ids:
        return jsonify({"success": False, "error": "No unsent recipients left in this campaign"})
    campaign.status = "active"
    db.session.commit()

    # Pass only IDs into the thread; re-query inside the thread's own session to
    # avoid DetachedInstanceError / cross-session writes that silently don't persist.
    def _send_all(cid, contact_ids):
        with app.app_context():
            camp = Campaign.query.get(cid)
            for ccid in contact_ids:
                cc = CampaignContact.query.get(ccid)
                if not cc:
                    continue
                lead = Lead.query.get(cc.lead_id)
                if not lead or not lead.email:
                    cc.status = "failed"
                    cc.error_message = "No email address (lead not enriched?)"
                    db.session.commit()
                    continue
                ctx = {"name": lead.name, "company": lead.company, "title": lead.title,
                       "city": lead.city, "industry": lead.industry}
                subj = personalize(camp.subject, ctx)
                body = personalize(camp.body_html, ctx)
                cc.personalized_subject = subj
                cc.personalized_body = body
                ok, err, message_id = send_email(
                    to_email=lead.email, to_name=lead.name,
                    subject=subj, body_html=body,
                    body_text=build_plain_text(body),
                    tracking_id=cc.tracking_id, base_url=_base_url(),
                )
                if ok:
                    cc.status = "sent"
                    cc.sent_at = datetime.utcnow()
                    cc.message_id = message_id
                    camp.sent_count = (camp.sent_count or 0) + 1
                    lead.status = "contacted"
                else:
                    cc.status = "failed"
                    cc.error_message = err
                db.session.commit()
                time.sleep(1.5)  # rate-limit: avoid spam filters
            camp.status = "completed"
            db.session.commit()

    thread = threading.Thread(target=_send_all, args=(campaign_id, pending_ids), daemon=True)
    thread.start()
    return jsonify({"success": True, "message": f"Sending to {len(pending_ids)} contacts in background"})


@app.route("/campaigns/<campaign_id>/send-test", methods=["POST"])
def send_test_email(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)
    test_email = request.json.get("email", "")
    if not test_email:
        return jsonify({"success": False, "error": "No test email provided"})
    sample = {"name": "Test User", "company": "Test Company", "title": "Loyalty Manager",
              "city": "Mumbai", "industry": "Automotive"}
    subj = personalize(campaign.subject, sample)
    body = personalize(campaign.body_html, sample)
    ok, err, _ = send_email(to_email=test_email, to_name="Test User", subject=f"[TEST] {subj}",
                            body_html=body, body_text=build_plain_text(body))
    return jsonify({"success": ok, "error": err})


@app.route("/campaigns/<campaign_id>/mark-replied", methods=["POST"])
def mark_replied(campaign_id):
    tracking_id = request.json.get("tracking_id")
    cc = CampaignContact.query.filter_by(tracking_id=tracking_id, campaign_id=campaign_id).first()
    if cc and not cc.replied_at:
        cc.replied_at = datetime.utcnow()
        cc.status = "replied"
        db.session.commit()
    return jsonify({"success": True})


@app.route("/campaigns/<campaign_id>/add-leads", methods=["POST"])
def add_leads_to_campaign(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)
    lead_ids = request.json.get("lead_ids", [])
    added = 0
    for lid in lead_ids:
        existing = CampaignContact.query.filter_by(campaign_id=campaign_id, lead_id=lid).first()
        if not existing:
            db.session.add(CampaignContact(campaign_id=campaign_id, lead_id=lid))
            added += 1
    db.session.commit()
    return jsonify({"success": True, "added": added})


# ─── HEALTH CHECK (public; used by the host's uptime probe) ───────────────────

@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


# ─── EMAIL TRACKING ──────────────────────────────────────────────────────────

@app.route("/track/open/<tracking_id>")
def track_open(tracking_id):
    cc = CampaignContact.query.filter_by(tracking_id=tracking_id).first()
    if cc:
        if not cc.opened_at:
            cc.opened_at = datetime.utcnow()
            cc.status = "opened"
        cc.open_count += 1
        db.session.commit()
    # Return 1x1 transparent GIF
    pixel = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00"
             b"!\xf9\x04\x00\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01"
             b"\x00\x00\x02\x02D\x01\x00;")
    return Response(pixel, mimetype="image/gif", headers={"Cache-Control": "no-cache"})


@app.route("/track/click/<tracking_id>")
def track_click(tracking_id):
    from urllib.parse import unquote
    url = unquote(request.args.get("url", "/"))
    cc = CampaignContact.query.filter_by(tracking_id=tracking_id).first()
    if cc:
        if not cc.clicked_at:
            cc.clicked_at = datetime.utcnow()
            cc.status = "clicked"
        cc.click_count += 1
        db.session.commit()
    return redirect(url)


# ─── SETTINGS (sender identity / SMTP) ────────────────────────────────────────

_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
_SETTINGS_KEYS = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS",
                  "FROM_NAME", "APP_BASE_URL", "IMAP_HOST", "IMAP_PORT")


def _update_env_file(updates: dict):
    """Persist key=value pairs into .env (replace in place or append) and apply
    them to the running process so no restart is needed."""
    lines = []
    if os.path.exists(_ENV_PATH):
        with open(_ENV_PATH, encoding="utf-8") as f:
            lines = f.read().splitlines()
    remaining = dict(updates)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in remaining:
            lines[i] = f"{key}={remaining.pop(key)}"
    for key, val in remaining.items():
        lines.append(f"{key}={val}")
    with open(_ENV_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    for key, val in updates.items():
        os.environ[key] = str(val)


@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        updates = {}
        for key in _SETTINGS_KEYS:
            field = key.lower()
            if field in request.form:
                val = request.form[field].strip()
                if key == "SMTP_PASS" and not val:
                    continue  # blank password field = keep the stored one
                updates[key] = val
        if updates:
            _update_env_file(updates)
        return jsonify({"success": True})

    st = providers.provider_status()
    return render_template("settings.html",
        smtp_host=os.getenv("SMTP_HOST", "smtp.gmail.com"),
        smtp_port=os.getenv("SMTP_PORT", "587"),
        smtp_user=os.getenv("SMTP_USER", ""),
        smtp_pass_set=bool(os.getenv("SMTP_PASS", "")),
        from_name=os.getenv("FROM_NAME", ""),
        app_base_url=_base_url(),
        smtp_ok=smtp_configured(),
        provider=st["provider"], provider_configured=st["configured"],
    )


@app.route("/api/smtp/test", methods=["POST"])
def smtp_test():
    """Verify SMTP login without sending an email."""
    ok, msg = test_smtp_login()
    return jsonify({"success": ok, "message": msg})


@app.route("/api/replies/check", methods=["POST"])
def check_replies_now():
    """Manually trigger one inbox scan for replies & bounces."""
    if not reply_tracker.imap_configured():
        return jsonify({"success": False,
                        "error": "Email credentials not configured — set the App Password in Settings first."})
    try:
        events = reply_tracker.fetch_inbox_events(since_days=7)
        counts = _apply_inbox_events(events)
        return jsonify({"success": True, **counts})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─── API (for AJAX) ───────────────────────────────────────────────────────────

@app.route("/api/leads")
def api_leads():
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 25))
    leads = Lead.query.order_by(Lead.created_at.desc()).paginate(page=page, per_page=per_page)
    return jsonify({
        "leads": [_lead_to_dict(l) for l in leads.items],
        "total": leads.total, "pages": leads.pages, "page": leads.page,
    })


@app.route("/api/apollo/status")
@app.route("/api/provider/status")
def provider_status():
    """Status of one provider (?provider=hunter|apollo) or the active default.
    Spends nothing."""
    prov = request.args.get("provider") or None
    st = providers.provider_status(prov)
    return jsonify({"configured": st["configured"], "provider": st["provider"],
                    "default": providers.active_provider()})


@app.route("/api/apollo/test", methods=["POST"])
@app.route("/api/provider/test", methods=["POST"])
def provider_test():
    """Validate a provider's key with a tiny live call (?provider= or JSON provider)."""
    prov = request.args.get("provider") or (request.json or {}).get("provider") or None
    ok, msg = providers.test_key(prov)
    return jsonify({"success": ok, "message": msg, "provider": providers._resolve(prov)})


@app.route("/api/campaigns")
def api_campaigns():
    campaigns = Campaign.query.order_by(Campaign.created_at.desc()).all()
    return jsonify({"campaigns": [{"id": c.id, "name": c.name, "status": c.status} for c in campaigns]})


@app.route("/api/stats")
def api_stats():
    total_leads = Lead.query.count()
    sent = CampaignContact.query.filter(CampaignContact.sent_at.isnot(None)).count()
    bounced = CampaignContact.query.filter(
        db.or_(CampaignContact.bounced_at.isnot(None), CampaignContact.status == "bounced")).count()
    opened = CampaignContact.query.filter(CampaignContact.open_count > 0).count()
    clicked = CampaignContact.query.filter(CampaignContact.click_count > 0).count()
    replied = CampaignContact.query.filter(CampaignContact.replied_at.isnot(None)).count()
    return jsonify({"total_leads": total_leads, "sent": sent,
                    "delivered": max(0, sent - bounced), "bounced": bounced,
                    "opened": opened, "clicked": clicked, "replied": replied})


def _lead_to_dict(l):
    return {"id": l.id, "name": l.name, "title": l.title, "company": l.company,
            "email": l.email, "phone": l.phone, "linkedin_url": l.linkedin_url,
            "city": l.city, "state": l.state, "industry": l.industry,
            "company_size": str(l.company_size), "website": l.website, "status": l.status,
            "email_status": getattr(l, "email_status", "unknown"),
            "enriched": bool(getattr(l, "enriched", False)),
            "has_mobile_on_file": bool(getattr(l, "has_mobile_on_file", False))}


# Start the background scheduler exactly once.
#  • Local `python app.py` (reloader on): only the reloader child sets
#    WERKZEUG_RUN_MAIN=true, so the job isn't registered twice.
#  • Production (gunicorn): no reloader, so the start command sets RUN_SCHEDULER=1.
#    Run with a SINGLE worker (--workers 1) so the schedule isn't duplicated.
if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or os.getenv("RUN_SCHEDULER") == "1":
    try:
        start_scheduler()
    except Exception as _e:
        app.logger.error(f"scheduler start failed: {_e}")


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "1") == "1"
    app.run(debug=debug, host="0.0.0.0", port=port, use_reloader=debug)
