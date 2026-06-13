from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import uuid

db = SQLAlchemy()

def generate_uuid():
    return str(uuid.uuid4())

class Lead(db.Model):
    __tablename__ = "leads"
    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    apollo_id = db.Column(db.String(100), unique=True, nullable=True)
    name = db.Column(db.String(200))
    title = db.Column(db.String(200))
    company = db.Column(db.String(200))
    email = db.Column(db.String(200))
    phone = db.Column(db.String(50))
    linkedin_url = db.Column(db.String(500))
    city = db.Column(db.String(100))
    state = db.Column(db.String(100))
    country = db.Column(db.String(100), default="India")
    industry = db.Column(db.String(200))
    company_size = db.Column(db.String(50))
    website = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    source = db.Column(db.String(50), default="apollo")
    tags = db.Column(db.String(500))
    notes = db.Column(db.Text)
    status = db.Column(db.String(50), default="new")  # new, contacted, replied, qualified, disqualified
    email_status = db.Column(db.String(50), default="unknown")  # locked, verified, unverified, unknown
    enriched = db.Column(db.Boolean, default=False)  # True once email/phone unlocked via Apollo Match
    has_mobile_on_file = db.Column(db.Boolean, default=False)  # Apollo holds a mobile (has_direct_phone) — reveal on demand

    campaign_contacts = db.relationship("CampaignContact", backref="lead", lazy=True)


class Campaign(db.Model):
    __tablename__ = "campaigns"
    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    name = db.Column(db.String(200), nullable=False)
    subject = db.Column(db.String(500), nullable=False)
    body_html = db.Column(db.Text, nullable=False)
    body_text = db.Column(db.Text)
    status = db.Column(db.String(50), default="draft")  # draft, active, paused, completed
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    scheduled_at = db.Column(db.DateTime, nullable=True)
    sent_count = db.Column(db.Integer, default=0)
    from_name = db.Column(db.String(200))
    from_email = db.Column(db.String(200))

    contacts = db.relationship("CampaignContact", backref="campaign", lazy=True)


class CampaignContact(db.Model):
    __tablename__ = "campaign_contacts"
    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    campaign_id = db.Column(db.String(36), db.ForeignKey("campaigns.id"), nullable=False)
    lead_id = db.Column(db.String(36), db.ForeignKey("leads.id"), nullable=False)
    tracking_id = db.Column(db.String(36), unique=True, default=generate_uuid)
    message_id = db.Column(db.String(300), nullable=True)  # SMTP Message-ID; matched against In-Reply-To/References for auto reply detection
    status = db.Column(db.String(50), default="pending")  # pending, sent, delivered, opened, clicked, replied, bounced, failed
    sent_at = db.Column(db.DateTime, nullable=True)
    opened_at = db.Column(db.DateTime, nullable=True)
    open_count = db.Column(db.Integer, default=0)
    clicked_at = db.Column(db.DateTime, nullable=True)
    click_count = db.Column(db.Integer, default=0)
    replied_at = db.Column(db.DateTime, nullable=True)
    bounced_at = db.Column(db.DateTime, nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    personalized_subject = db.Column(db.String(500))
    personalized_body = db.Column(db.Text)


class AutoDiscoveryConfig(db.Model):
    __tablename__ = "auto_discovery_config"
    id = db.Column(db.Integer, primary_key=True)
    enabled = db.Column(db.Boolean, default=False)
    job_titles = db.Column(db.Text, default="Loyalty Manager,Head of Loyalty,CRM Manager,Dealer Loyalty Manager,Customer Retention Manager")
    industries = db.Column(db.Text, default="Automotive,Consumer Goods,Retail,Financial Services,Telecom")
    company_sizes = db.Column(db.Text, default="51-200,201-500,501-1000,1001-5000,5001-10000,10001+")
    country = db.Column(db.String(50), default="India")
    run_interval_hours = db.Column(db.Integer, default=24)
    last_run_at = db.Column(db.DateTime, nullable=True)
    leads_per_run = db.Column(db.Integer, default=25)
    total_leads_fetched = db.Column(db.Integer, default=0)
