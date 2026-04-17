import os
import json
import csv
import io
import hashlib
import logging
import pymysql
import random
import re
import secrets
import string
import uuid
import requests
from collections import defaultdict, deque
from threading import Lock
from urllib import parse, request as urllib_request
from urllib.error import HTTPError, URLError
from datetime import datetime, timedelta
from contextvars import ContextVar
from functools import wraps
from flask import (
    Flask,
    g,
    abort,
    has_request_context,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_bcrypt import Bcrypt
from flask_mail import Mail, Message as MailMessage
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
from sqlalchemy import CheckConstraint, UniqueConstraint, create_engine, event, inspect, text
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession, aliased, load_only
from markupsafe import Markup, escape
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

try:
    from openpyxl import Workbook
except Exception:
    Workbook = None


def get_workbook_class():
    global Workbook
    if Workbook is not None:
        return Workbook
    try:
        from openpyxl import Workbook as OpenpyxlWorkbook
    except Exception:
        return None
    Workbook = OpenpyxlWorkbook
    return Workbook

pymysql.install_as_MySQLdb()
load_dotenv(override=True)


def flash(*args, **kwargs):
    return None


def _env_flag(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _normalized_database_url(raw_url):
    url = (raw_url or "").strip()
    if not url:
        return ""
    if url.startswith("mysql://"):
        return "mysql+pymysql://" + url[len("mysql://"):]
    return url


app = Flask(__name__)
app.config["APP_ENV"] = os.getenv("APP_ENV", "development").strip().lower()
app.config["IS_PRODUCTION"] = app.config["APP_ENV"] == "production"
app.config["ENABLE_DEV_ENDPOINTS"] = _env_flag("ENABLE_DEV_ENDPOINTS", default=not app.config["IS_PRODUCTION"])

database_url = _normalized_database_url(os.getenv("DATABASE_URL"))
if not database_url:
    if app.config["IS_PRODUCTION"]:
        raise RuntimeError("DATABASE_URL is required in production.")
    database_url = "sqlite:///skillswap.db"

secret_key = (os.getenv("SECRET_KEY") or "").strip()
if not secret_key:
    if app.config["IS_PRODUCTION"]:
        raise RuntimeError("SECRET_KEY is required in production.")
    secret_key = "dev-only-change-me-before-production"

app.config["SECRET_KEY"] = secret_key
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024
app.config["MAIL_SERVER"] = os.getenv("MAIL_SERVER", "smtp.gmail.com")
app.config["MAIL_PORT"] = int(os.getenv("MAIL_PORT", "587"))
app.config["MAIL_USE_TLS"] = os.getenv("MAIL_USE_TLS", "true").strip().lower() == "true"
app.config["MAIL_USE_SSL"] = os.getenv("MAIL_USE_SSL", "false").strip().lower() == "true"
app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME", "")
app.config["MAIL_PASSWORD"] = re.sub(r"\s+", "", os.getenv("MAIL_PASSWORD", "") or "")
app.config["MAIL_DEFAULT_SENDER"] = os.getenv("MAIL_DEFAULT_SENDER", app.config["MAIL_USERNAME"])
app.config["MAIL_TIMEOUT"] = int(os.getenv("MAIL_TIMEOUT", "10"))
app.config["ALLOW_NEW_REGISTRATIONS"] = os.getenv("ALLOW_NEW_REGISTRATIONS", "true").strip().lower() == "true"
app.config["ALLOW_USER_REPORTS"] = os.getenv("ALLOW_USER_REPORTS", "true").strip().lower() == "true"
app.config["ADMIN_TABLE_LIMIT"] = max(50, min(int(os.getenv("ADMIN_TABLE_LIMIT", "200")), 500))
app.config["ALLOW_SESSION_CREATION"] = os.getenv("ALLOW_SESSION_CREATION", "true").strip().lower() == "true"
app.config["AUTO_EXPIRE_INACTIVE_SESSIONS"] = os.getenv("AUTO_EXPIRE_INACTIVE_SESSIONS", "true").strip().lower() == "true"
app.config["MAX_REPORTS_PER_USER_PER_DAY"] = max(1, min(int(os.getenv("MAX_REPORTS_PER_USER_PER_DAY", "5")), 100))
app.config["ENABLE_SPAM_DETECTION"] = os.getenv("ENABLE_SPAM_DETECTION", "true").strip().lower() == "true"
app.config["ALLOW_INSTANT_EXCHANGE_START"] = os.getenv("ALLOW_INSTANT_EXCHANGE_START", "true").strip().lower() == "true"
app.config["REQUIRE_MUTUAL_ACCEPTANCE"] = os.getenv("REQUIRE_MUTUAL_ACCEPTANCE", "true").strip().lower() == "true"
app.config["ALLOW_RATING_AFTER_SESSION"] = os.getenv("ALLOW_RATING_AFTER_SESSION", "true").strip().lower() == "true"
app.config["REQUIRE_FEEDBACK_SUBMISSION"] = os.getenv("REQUIRE_FEEDBACK_SUBMISSION", "false").strip().lower() == "true"
app.config["RECAPTCHA_SITE_KEY"] = os.getenv("RECAPTCHA_SITE_KEY", "YOUR_SITE_KEY")
app.config["RECAPTCHA_SECRET_KEY"] = os.getenv("RECAPTCHA_SECRET_KEY", "YOUR_SECRET_KEY")
app.config["RECAPTCHA_ENABLED"] = _env_flag("RECAPTCHA_ENABLED", default=app.config["IS_PRODUCTION"])
app.config["RECAPTCHA_USE_GOOGLE_TEST_KEYS"] = _env_flag(
    "RECAPTCHA_USE_GOOGLE_TEST_KEYS",
    default=False,
)
app.config["RECAPTCHA_FAIL_OPEN_ON_LOCALHOST"] = _env_flag(
    "RECAPTCHA_FAIL_OPEN_ON_LOCALHOST",
    default=not app.config["IS_PRODUCTION"],
)

if app.config["RECAPTCHA_ENABLED"] and app.config["RECAPTCHA_USE_GOOGLE_TEST_KEYS"]:
    # Google-provided public test keys for localhost/dev environments.
    app.config["RECAPTCHA_SITE_KEY"] = "6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI"
    app.config["RECAPTCHA_SECRET_KEY"] = "6LeIxAcTAAAAAGG-vFI1TnRWxMZNFuojJ4WifJWe"
app.config["GROQ_API_KEY"] = (os.getenv("GROQ_API_KEY") or "").strip()

if app.config["IS_PRODUCTION"]:
    required_settings = {
        "DATABASE_URL": database_url,
        "SECRET_KEY": app.config["SECRET_KEY"],
        "SUPER_ADMIN_PASSWORD": (os.getenv("SUPER_ADMIN_PASSWORD") or "").strip(),
    }
    missing = [key for key, value in required_settings.items() if not value]
    if missing:
        raise RuntimeError(f"Missing required production settings: {', '.join(missing)}")

logging.basicConfig(level=logging.INFO)
app.logger.setLevel(logging.INFO)


def _resolve_startup_database_uri(configured_uri):
    uri = (configured_uri or "").strip()
    if app.config.get("IS_PRODUCTION"):
        return uri

    if not uri:
        return "sqlite:///skillswap.db"

    if uri.startswith("mysql+pymysql://"):
        try:
            probe_engine = create_engine(uri, pool_pre_ping=True)
            with probe_engine.connect():
                pass
            probe_engine.dispose()
        except Exception as exc:
            app.logger.warning(
                "Configured DATABASE_URL is unreachable in development; falling back to SQLite. Error: %s",
                exc,
            )
            return "sqlite:///skillswap.db"

    return uri


app.config["SQLALCHEMY_DATABASE_URI"] = _resolve_startup_database_uri(
    app.config.get("SQLALCHEMY_DATABASE_URI")
)

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "Admin@SkillSwap.com").strip().lower()
SUPER_ADMIN_DEFAULT_PASSWORD = (os.getenv("SUPER_ADMIN_PASSWORD") or "").strip()
if not SUPER_ADMIN_DEFAULT_PASSWORD:
    if app.config["IS_PRODUCTION"]:
        raise RuntimeError("SUPER_ADMIN_PASSWORD is required in production.")
    SUPER_ADMIN_DEFAULT_PASSWORD = "dev-only-change-super-admin-password"
LEGACY_SUPER_ADMIN_EMAIL = os.getenv("LEGACY_SUPER_ADMIN_EMAIL", "").strip().lower()
ALLOWED_ROLES = {"user", "admin", "super_admin"}
ROLE_CHANGE_CONTEXT_KEY = "role_change_context"
_request_role_change_context = ContextVar("request_role_change_context", default=None)
_SKILL_CATEGORIES_CORE = [
    "Programming",
    "Web Development",
    "Frontend Development",
    "Backend Development",
    "Full Stack Development",
    "Mobile App Development",
    "Desktop Application Development",
    "Software Engineering",
    "Game Development",
    "Embedded Systems Development",
    "Data Science",
    "Data Analysis",
    "Machine Learning",
    "Artificial Intelligence",
    "Deep Learning",
    "Data Engineering",
    "Big Data Technologies",
    "Business Intelligence",
    "Cloud Computing",
    "DevOps Engineering",
    "System Administration",
    "Network Engineering",
    "IT Support",
    "Cybersecurity",
    "Information Security",
    "User Interface Design",
    "User Experience Design",
    "Graphic Design",
    "Visual Design",
    "Product Design",
    "Motion Graphics",
    "Video Editing",
    "Photography",
    "Digital Marketing",
    "Search Engine Optimization",
    "Social Media Marketing",
    "Content Writing",
    "Copywriting",
    "Technical Writing",
    "Branding",
    "Public Relations",
    "Business Development",
    "Entrepreneurship",
    "Project Management",
    "Product Management",
    "Operations Management",
    "Human Resource Management",
    "Sales and Lead Generation",
    "Customer Support",
    "Accounting",
    "Financial Analysis",
    "Investment Management",
    "Bookkeeping",
    "Taxation",
    "Financial Planning",
    "Communication Skills",
    "Leadership",
    "Time Management",
    "Problem Solving",
    "Critical Thinking",
    "Negotiation Skills",
    "Public Speaking",
    "English Language",
    "Urdu Language",
    "Arabic Language",
    "Chinese Language",
    "Spanish Language",
    "French Language",
    "German Language",
    "Teaching",
    "Tutoring",
    "Curriculum Development",
    "Instructional Design",
    "Online Course Creation",
    "Research and Analysis",
    "Documentation",
    "Quality Assurance",
    "Testing and Debugging",
    "Technical Support",
]
_SKILL_CATEGORIES_EXTENDED = [
    "Blockchain Development",
    "Web3 Development",
    "Smart Contract Development",
    "Internet of Things",
    "Robotics Engineering",
    "Computer Vision",
    "Natural Language Processing",
    "Augmented Reality Development",
    "Virtual Reality Development",
    "Edge Computing",
    "High Performance Computing",
    "API Development",
    "API Integration",
    "Microservices Architecture",
    "Software Architecture",
    "Technical Architecture",
    "Solution Architecture",
    "Platform Engineering",
    "Site Reliability Engineering",
    "Release Engineering",
    "Firmware Development",
    "Hardware Engineering",
    "Digital Signal Processing",
    "Geospatial Analysis",
    "Remote Sensing",
    "Bioinformatics",
    "Computational Biology",
    "Quantitative Analysis",
    "Econometrics",
    "Data Visualization Engineering",
    "Data Governance",
    "Data Warehousing",
    "Data Modeling",
    "Feature Engineering",
    "Model Optimization",
    "AI Model Deployment",
    "MLOps Engineering",
    "Prompt Engineering",
    "Knowledge Engineering",
    "Information Retrieval",
    "Search Engineering",
    "Recommendation Systems",
    "Fraud Detection Systems",
    "Risk Modeling",
    "Algorithm Design",
    "Distributed Systems",
    "Parallel Computing",
    "Event-Driven Architecture",
    "Message Queue Systems",
    "Containerization",
    "Infrastructure as Code",
    "Cloud Architecture",
    "Multi-Cloud Strategy",
    "Hybrid Cloud Management",
    "Cloud Security",
    "Identity and Access Management",
    "Penetration Testing",
    "Threat Intelligence",
    "Security Operations",
    "Digital Forensics",
    "Vulnerability Assessment",
    "Security Compliance",
    "Privacy Engineering",
    "UI Prototyping",
    "Interaction Design",
    "Design Systems",
    "Accessibility Design",
    "Usability Testing",
    "Creative Direction",
    "Art Direction",
    "Brand Strategy",
    "Visual Storytelling",
    "3D Modeling",
    "3D Rendering",
    "3D Visualization",
    "Visual Effects",
    "Sound Design",
    "Music Production",
    "Audio Engineering",
    "Voice Over Production",
    "Podcast Production",
    "Broadcast Production",
    "Script Writing",
    "Story Development",
    "Game Design",
    "Level Design",
    "Game Mechanics Design",
    "Game Testing",
    "E-commerce Management",
    "Marketplace Management",
    "Product Strategy",
    "Go-To-Market Strategy",
    "Growth Strategy",
    "Revenue Operations",
    "Business Analytics",
    "Competitive Analysis",
    "Market Intelligence",
    "Customer Success Management",
    "Customer Experience Management",
    "Sales Operations",
    "Lead Qualification",
    "Account Management",
    "Partnership Development",
    "Vendor Management",
    "Procurement Management",
    "Supply Chain Analytics",
    "Logistics Management",
    "Inventory Management",
    "Operations Optimization",
    "Process Improvement",
    "Change Management",
    "Risk Management",
    "Compliance Management",
    "Corporate Strategy",
    "Startup Strategy",
    "Fundraising",
    "Investor Relations",
    "Pitch Deck Development",
    "Grant Writing",
    "Proposal Writing",
    "Technical Documentation",
    "Knowledge Base Management",
    "Content Strategy",
    "Editorial Planning",
    "Copy Editing",
    "Proofreading",
    "Localization",
    "Translation",
    "Transcription",
    "Speech Writing",
    "Public Communication",
    "Crisis Communication",
    "Media Planning",
    "Media Buying",
    "Performance Marketing",
    "Email Marketing",
    "Marketing Automation",
    "Conversion Rate Optimization",
    "Affiliate Marketing",
    "Influencer Marketing",
    "Community Management",
    "Social Media Strategy",
    "Brand Management",
    "Reputation Management",
    "Personal Branding",
    "No-Code Development",
    "Low-Code Development",
    "Workflow Automation",
    "Business Process Automation",
    "CRM Management",
    "ERP Systems",
    "CMS Management",
    "Spreadsheet Modeling",
    "Dashboard Development",
    "Reporting Automation",
    "Career Coaching",
    "Life Coaching",
    "Executive Coaching",
    "Interview Coaching",
    "Resume Development",
    "Professional Development",
    "Skill Assessment",
    "Mentorship",
    "Academic Advising",
    "Instructional Coaching",
    "Exam Preparation",
    "Language Coaching",
    "Workshop Facilitation",
    "Curriculum Assessment",
    "Educational Consulting",
    "Research Methodology",
    "Survey Design",
    "Qualitative Research",
    "Quantitative Research",
    "Technical Training",
    "Product Training",
    "Software Training",
    "Fitness Coaching",
    "Personal Training",
    "Nutrition Coaching",
    "Yoga Instruction",
    "Meditation Coaching",
    "Sports Coaching",
    "Cooking",
    "Baking",
    "Event Management",
    "Wedding Planning",
    "Travel Planning",
    "Interior Styling",
    "Fashion Styling",
    "Makeup Artistry",
    "Hair Styling",
    "Photography Editing",
    "Photo Retouching",
    "Drone Operation",
    "Driving Instruction",
    "First Aid Training",
    "Electrical Maintenance",
    "Plumbing",
    "Carpentry",
    "Welding",
    "Automotive Repair",
    "Bicycle Repair",
    "Appliance Repair",
    "HVAC Maintenance",
    "Construction Management",
    "Building Inspection",
    "Real Estate Consulting",
    "Property Management",
    "Legal Consulting",
    "Contract Management",
    "Intellectual Property Management",
    "Tax Consulting",
    "Audit Management",
    "Financial Reporting",
    "Cost Accounting",
    "Payroll Management",
    "Treasury Management",
    "Investment Analysis",
    "Portfolio Management",
    "Wealth Management",
    "Financial Modeling",
    "Budget Planning",
    "Decision Making",
    "Emotional Intelligence",
    "Conflict Resolution",
    "Negotiation",
    "Leadership Development",
    "Team Building",
    "Time Optimization",
    "Productivity Coaching",
    "Critical Analysis",
    "Problem Analysis",
    "Strategic Thinking",
]

_unique_skill_categories = {}
for category_name in _SKILL_CATEGORIES_CORE + _SKILL_CATEGORIES_EXTENDED:
    cleaned_name = re.sub(r"\s+", " ", (category_name or "").strip())
    if not cleaned_name or cleaned_name.lower() == "other":
        continue
    dedupe_key = cleaned_name.lower()
    if dedupe_key not in _unique_skill_categories:
        _unique_skill_categories[dedupe_key] = cleaned_name

_NON_EXCHANGEABLE_OFFLINE_CATEGORIES = {
    "appliance repair",
    "automotive repair",
    "bicycle repair",
    "building inspection",
    "carpentry",
    "construction management",
    "driving instruction",
    "electrical maintenance",
    "hair styling",
    "hvac maintenance",
    "interior styling",
    "makeup artistry",
    "plumbing",
    "welding",
}


def _category_sort_key(value):
    normalized = (value or "").strip()
    if not normalized:
        return (3, "")
    first_char = normalized[0]
    if first_char.isalpha():
        bucket = 0
    elif first_char.isdigit():
        bucket = 1
    else:
        bucket = 2
    return (bucket, normalized.lower())


_exchangeable_categories = [
    value
    for value in _unique_skill_categories.values()
    if value.lower() not in _NON_EXCHANGEABLE_OFFLINE_CATEGORIES
]

SKILL_CATEGORIES = sorted(_exchangeable_categories, key=_category_sort_key) + ["Other"]
SKILL_LEVELS = ["Beginner", "Intermediate", "Expert"]
SKILL_LEVEL_WEIGHT = {"Beginner": 1, "Intermediate": 2, "Expert": 3}
MAX_SKILLS_PER_LIST = 10
PRESENCE_ACTIVE_SECONDS = 90
AVAILABILITY_STATUSES = ["Available", "Busy", "Unavailable"]
REPORT_REASONS = ["Spam", "Abuse", "Fake", "Other"]
HOME_SUBTITLE_OPTIONS = [
    "Connect with learners and experts to share knowledge through peer-to-peer exchanges.",
    "Learn faster by trading practical skills with people who match your goals.",
    "Build real-world skills through trusted exchanges with your community.",
    "Teach what you know, learn what you need, and grow together.",
    "Swap expertise, collaborate with peers, and unlock new opportunities.",
    "Find people who can teach you while you help them grow too.",
    "Turn your skills into shared progress through meaningful exchanges.",
    "From beginner to expert, connect and exchange knowledge confidently.",
    "Discover skill partners, start conversations, and learn by doing.",
    "Join a community where skills are shared, valued, and exchanged.",
]
CHAT_MESSAGE_MAX_LENGTH = 500
CHAT_RATE_LIMIT_WINDOW_SECONDS = 60
CHAT_RATE_LIMIT_MAX_REQUESTS = 12
AI_REQUEST_TIMEOUT_SECONDS = 20
GROQ_MODEL_PRIMARY = os.getenv("GROQ_MODEL_PRIMARY", "llama-3.1-8b-instant")
GROQ_MODEL_FALLBACKS = os.getenv(
    "GROQ_MODEL_FALLBACKS",
    "llama-3.3-70b-versatile,meta-llama/llama-4-scout-17b-16e-instruct",
)
CHAT_HISTORY_SESSION_KEY = "assistant_chat_history"
CHAT_HISTORY_MAX_ITEMS = 24
REGISTER_RECAPTCHA_SESSION_KEY = "register_recaptcha_verified_at"
REGISTER_RECAPTCHA_TTL_SECONDS = 20 * 60
SKILLSWAP_USER_CHATBOT_SYSTEM_PROMPT = (
    "You are SkillSwap AI assistant.\n\n"
    "You help users understand and use SkillSwap based on provided runtime context only.\n\n"
    "IMPORTANT RULES:\n"
    "- Answer ONLY what the user asks\n"
    "- Use known platform routes/workflows/features from runtime context\n"
    "- Never invent buttons, labels, or UI controls\n"
    "- Never assume what page the user is currently on\n"
    "- Never invent features that are not listed in context\n"
    "- Never write fake completion statements for actions\n"
    "- Handle multi-part questions in one reply\n"
    "- Keep responses short and helpful\n"
    "- If context is insufficient, say exactly: 'I'm not fully sure about that. Please check the relevant section.'\n\n"
    "If user asks something unrelated, politely guide them back to SkillSwap topics."
)
_chat_rate_limit_store = defaultdict(deque)
_chat_rate_limit_lock = Lock()
DYNAMIC_BOOLEAN_SETTING_KEYS = {
    "ALLOW_NEW_REGISTRATIONS",
    "ALLOW_USER_REPORTS",
    "ALLOW_SESSION_CREATION",
    "AUTO_EXPIRE_INACTIVE_SESSIONS",
    "ALLOW_RATING_AFTER_SESSION",
    "REQUIRE_FEEDBACK_SUBMISSION",
}
ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png"}
ALLOWED_ATTACHMENT_EXTENSIONS = {
    "pdf",
    "doc",
    "docx",
    "txt",
    "jpg",
    "jpeg",
    "png",
    "gif",
    "mp3",
    "wav",
    "mp4",
    "zip",
    "rar",
}
DEFAULT_PROFILE_IMAGE = "images/default-avatar.svg"
DEFAULT_ADMIN_PROFILE_IMAGE = "images/skillswap-icon.png"
PROFILE_IMAGE_MAX_BYTES = 2 * 1024 * 1024
JITSI_BASE_URL = "https://meet.jit.si"
UPLOAD_SUBDIR = "uploads"
UPLOAD_FOLDER = os.path.join(app.static_folder, UPLOAD_SUBDIR)
REPORT_UPLOAD_SUBDIR = "report_uploads"
REPORT_UPLOAD_FOLDER = os.path.join(app.static_folder, REPORT_UPLOAD_SUBDIR)

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(REPORT_UPLOAD_FOLDER, exist_ok=True)

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
mail = Mail(app)


class User(db.Model):
    __tablename__ = "users"

    user_id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    username = db.Column(db.String(40), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="user")
    is_blocked = db.Column(db.Boolean, nullable=False, default=False)
    availability = db.Column(db.Boolean, nullable=False, default=True)
    availability_status = db.Column(db.String(20), nullable=False, default="Available")
    profile_image = db.Column(db.String(255), nullable=True)
    bio = db.Column(db.Text, nullable=True)
    location = db.Column(db.String(120), nullable=True)
    show_email_on_profile = db.Column(db.Boolean, nullable=False, default=False)
    is_verified = db.Column(db.Boolean, nullable=False, default=False)
    otp_code = db.Column(db.Text, nullable=True)
    otp_expiry = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_seen_at = db.Column(db.DateTime, nullable=True)

    offered_skills = db.relationship(
        "Skill",
        secondary="user_skills_offered",
        lazy="subquery",
        backref=db.backref("offered_by_users", lazy=True),
    )
    wanted_skills = db.relationship(
        "Skill",
        secondary="user_skills_wanted",
        lazy="subquery",
        backref=db.backref("wanted_by_users", lazy=True),
    )

    sent_requests = db.relationship(
        "Request", foreign_keys="Request.sender_id", backref="sender", lazy=True
    )
    received_requests = db.relationship(
        "Request", foreign_keys="Request.receiver_id", backref="receiver", lazy=True
    )

    def average_rating(self):
        ratings = Rating.query.filter_by(to_user=self.user_id).all()
        if not ratings:
            return None
        return round(sum(r.rating for r in ratings) / len(ratings), 2)

    @property
    def is_admin(self):
        return self.role in {"admin", "super_admin"}

    @property
    def is_super_admin(self):
        return self.role == "super_admin"

    @property
    def profile_image_path(self):
        if self.profile_image:
            return self.profile_image
        if self.is_admin:
            return DEFAULT_ADMIN_PROFILE_IMAGE
        return DEFAULT_PROFILE_IMAGE

    @property
    def availability_label(self):
        if self.availability_status:
            return self.availability_status
        return "Available" if self.availability else "Unavailable"

    @property
    def role_label(self):
        if self.is_super_admin:
            return "Super Admin"
        return "Admin" if self.is_admin else "User"

    @property
    def can_access_admin(self):
        return self.is_admin and not self.is_blocked


class Category(db.Model):
    __tablename__ = "categories"

    category_id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False, unique=True, index=True)


class Skill(db.Model):
    __tablename__ = "skills"

    skill_id = db.Column(db.Integer, primary_key=True)
    skill_name = db.Column(db.String(120), unique=True, nullable=False, index=True)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.category_id"), nullable=True, index=True)
    category = db.Column(db.String(80), nullable=True)
    description = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(20), nullable=False, default="active")

    category_ref = db.relationship("Category", foreign_keys=[category_id])

    @property
    def category_name(self):
        if self.category_ref and self.category_ref.name:
            return self.category_ref.name
        return self.category or "Uncategorized"


class UserSkill(db.Model):
    __tablename__ = "user_skills"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=False)
    skill_id = db.Column(db.Integer, db.ForeignKey("skills.skill_id"), nullable=False)

    __table_args__ = (UniqueConstraint("user_id", "skill_id", name="uq_user_skills_user_skill"),)


class UserSkillsOffered(db.Model):
    __tablename__ = "user_skills_offered"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=False)
    skill_id = db.Column(db.Integer, db.ForeignKey("skills.skill_id"), nullable=False)
    level = db.Column(db.String(20), nullable=False, default="Intermediate")

    __table_args__ = (UniqueConstraint("user_id", "skill_id", name="uq_offered"),)


class UserSkillsWanted(db.Model):
    __tablename__ = "user_skills_wanted"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=False)
    skill_id = db.Column(db.Integer, db.ForeignKey("skills.skill_id"), nullable=False)
    level = db.Column(db.String(20), nullable=False, default="Beginner")

    __table_args__ = (UniqueConstraint("user_id", "skill_id", name="uq_wanted"),)


class Request(db.Model):
    __tablename__ = "requests"

    request_id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=False)
    # Sender-proposed exchange values
    offered_skill_id = db.Column(db.Integer, db.ForeignKey("skills.skill_id"), nullable=False)
    requested_skill_id = db.Column(db.Integer, db.ForeignKey("skills.skill_id"), nullable=False)
    # Final locked values after receiver accepts/modifies
    final_offered_skill_id = db.Column(db.Integer, db.ForeignKey("skills.skill_id"), nullable=True)
    final_requested_skill_id = db.Column(db.Integer, db.ForeignKey("skills.skill_id"), nullable=True)
    status = db.Column(db.String(40), nullable=False, default="pending", index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    sender_rated = db.Column(db.Boolean, nullable=False, default=False)
    receiver_rated = db.Column(db.Boolean, nullable=False, default=False)
    is_completed_by_sender = db.Column(db.Boolean, nullable=False, default=False)
    is_completed_by_receiver = db.Column(db.Boolean, nullable=False, default=False)
    sender_confirmed = db.Column(db.Boolean, nullable=False, default=False)
    receiver_confirmed = db.Column(db.Boolean, nullable=False, default=False)
    rated_by_sender = db.Column(db.Boolean, nullable=False, default=False)
    rated_by_receiver = db.Column(db.Boolean, nullable=False, default=False)
    session_room = db.Column(db.String(120), nullable=True)
    session_link = db.Column(db.String(255), nullable=True)
    session_scheduled_for = db.Column(db.DateTime, nullable=True)
    session_proposed_by = db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=True)
    session_confirmed_at = db.Column(db.DateTime, nullable=True)
    session_started_at = db.Column(db.DateTime, nullable=True)
    session_completed_at = db.Column(db.DateTime, nullable=True)
    session_sender_last_ping_at = db.Column(db.DateTime, nullable=True)
    session_receiver_last_ping_at = db.Column(db.DateTime, nullable=True)

    offered_skill = db.relationship("Skill", foreign_keys=[offered_skill_id])
    requested_skill = db.relationship("Skill", foreign_keys=[requested_skill_id])
    final_offered_skill = db.relationship("Skill", foreign_keys=[final_offered_skill_id])
    final_requested_skill = db.relationship("Skill", foreign_keys=[final_requested_skill_id])

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'countered', 'accepted', 'rejected', 'awaiting_confirmation', 'completed', 'terminated')",
            name="ck_request_status",
        ),
    )


class Rating(db.Model):
    __tablename__ = "ratings"

    rating_id = db.Column(db.Integer, primary_key=True)
    from_user = db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=False)
    to_user = db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=False)
    exchange_request_id = db.Column(
        db.Integer, db.ForeignKey("requests.request_id"), nullable=True, index=True
    )
    rating = db.Column(db.Integer, nullable=False)
    feedback = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        CheckConstraint("rating >= 1 AND rating <= 5", name="ck_rating_value"),
        UniqueConstraint(
            "exchange_request_id", "from_user", name="uq_rating_exchange_from_user"
        ),
    )


class UserReport(db.Model):
    __tablename__ = "user_reports"

    report_id = db.Column(db.Integer, primary_key=True)
    reporter_id = db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=False)
    reported_user_id = db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=False)
    reason = db.Column(db.String(40), nullable=False)
    description = db.Column(db.Text, nullable=True)
    report_attachments = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    reporter = db.relationship("User", foreign_keys=[reporter_id])
    reported_user = db.relationship("User", foreign_keys=[reported_user_id])


class BlockedUser(db.Model):
    __tablename__ = "blocked_users"

    id = db.Column(db.Integer, primary_key=True)
    blocker_id = db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=False)
    blocked_id = db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("blocker_id", "blocked_id", name="uq_blocked_users_pair"),
        CheckConstraint("blocker_id <> blocked_id", name="ck_blocked_users_not_self"),
    )

    blocker = db.relationship("User", foreign_keys=[blocker_id])
    blocked = db.relationship("User", foreign_keys=[blocked_id])


def request_user_role(req, user_id):
    if req.sender_id == user_id:
        return "sender"
    if req.receiver_id == user_id:
        return "receiver"
    return None


def get_my_rating_for_request(req, user_id):
    return Rating.query.filter_by(exchange_request_id=req.request_id, from_user=user_id).first()


def normalize_username(raw_username):
    username = (raw_username or "").strip().lower()
    if not username:
        return None
    if " " in username:
        return None
    if not re.fullmatch(r"[a-z0-9_\.]{3,30}", username):
        return None
    return username


def normalize_skill_name(raw_skill_name):
    cleaned = re.sub(r"\s+", " ", (raw_skill_name or "").strip())
    if not cleaned:
        return None
    return cleaned.upper()


def normalize_custom_category(raw_category):
    cleaned = re.sub(r"\s+", " ", (raw_category or "").strip())
    if not cleaned:
        return None
    return string.capwords(cleaned)


def is_valid_skill_name(skill_name):
    if not skill_name:
        return False
    if len(skill_name) < 2 or len(skill_name) > 80:
        return False
    return re.fullmatch(r"[A-Z0-9\+\#\.\-\&\/\(\) ]+", skill_name) is not None


def get_or_create_category_by_name(category_name):
    clean_name = normalize_custom_category(category_name)
    if not clean_name:
        return None

    existing = Category.query.filter(db.func.lower(Category.name) == clean_name.lower()).first()
    if existing:
        return existing

    created = Category(name=clean_name)
    db.session.add(created)
    db.session.flush()
    return created


def get_skill_category_options():
    rows = Category.query.order_by(Category.name.asc()).all()
    return [row.name for row in rows]


def sync_user_skill_mapping(user_id, skill_id):
    offered_exists = UserSkillsOffered.query.filter_by(user_id=user_id, skill_id=skill_id).first()
    wanted_exists = UserSkillsWanted.query.filter_by(user_id=user_id, skill_id=skill_id).first()
    existing_link = UserSkill.query.filter_by(user_id=user_id, skill_id=skill_id).first()

    if offered_exists or wanted_exists:
        if not existing_link:
            db.session.add(UserSkill(user_id=user_id, skill_id=skill_id))
    elif existing_link:
        db.session.delete(existing_link)


class Message(db.Model):
    __tablename__ = "messages"

    message_id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=False)
    message = db.Column(db.Text, nullable=False)
    message_type = db.Column(db.String(20), nullable=False, default="user")
    attachment_url = db.Column(db.Text, nullable=True)
    attachment_type = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    read_at = db.Column(db.DateTime, nullable=True)

    sender = db.relationship("User", foreign_keys=[sender_id])
    receiver = db.relationship("User", foreign_keys=[receiver_id])


class Notification(db.Model):
    __tablename__ = "notifications"

    notification_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=False)
    message = db.Column(db.String(255), nullable=False)
    notif_type = db.Column(db.String(40), nullable=False)
    link = db.Column(db.String(255), nullable=True)
    is_read = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    user = db.relationship("User", foreign_keys=[user_id])


class UserSession(db.Model):
    __tablename__ = "user_sessions"

    session_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=False, index=True)
    session_token = db.Column(db.String(128), nullable=False, unique=True, index=True)
    ip_address = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(255), nullable=True)
    login_time = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_active = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)

    user = db.relationship("User", foreign_keys=[user_id])


class PlatformSetting(db.Model):
    __tablename__ = "platform_settings"

    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.String(20), nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


def _bool_to_setting_text(value):
    return "true" if bool(value) else "false"


def _setting_text_to_bool(value, default=False):
    if value is None:
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def load_dynamic_settings_from_db():
    try:
        table_names = inspect(db.engine).get_table_names()
    except Exception:
        return

    if "platform_settings" not in table_names:
        return

    rows = PlatformSetting.query.filter(PlatformSetting.key.in_(list(DYNAMIC_BOOLEAN_SETTING_KEYS))).all()
    for row in rows:
        if row.key not in DYNAMIC_BOOLEAN_SETTING_KEYS:
            continue
        app.config[row.key] = _setting_text_to_bool(row.value, app.config.get(row.key, False))


def persist_dynamic_setting(key, value):
    if key not in DYNAMIC_BOOLEAN_SETTING_KEYS:
        raise ValueError("Unsupported setting key.")

    normalized = bool(value)
    row = PlatformSetting.query.filter_by(key=key).first()
    if row is None:
        row = PlatformSetting(key=key, value=_bool_to_setting_text(normalized))
        db.session.add(row)
    else:
        row.value = _bool_to_setting_text(normalized)
        row.updated_at = datetime.utcnow()

    app.config[key] = normalized
    db.session.commit()
    return normalized


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if g.user is None:
            flash("Please log in first.", "error")
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped_view


def admin_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if g.user is None:
            flash("Please log in first.", "error")
            return redirect(url_for("login"))
        if g.user.is_blocked:
            session.clear()
            session["blocked_login_notice"] = (
                "Your account has been blocked by the administrator. "
                "If you believe this is a mistake, please contact support."
            )
            return redirect(url_for("login"))
        if not g.user.can_access_admin:
            flash("Admin access required.", "error")
            return redirect(url_for("dashboard"))
        return view(*args, **kwargs)

    return wrapped_view


def super_admin_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if g.user is None:
            flash("Please log in first.", "error")
            return redirect(url_for("login"))
        if g.user.is_blocked:
            session.clear()
            session["blocked_login_notice"] = (
                "Your account has been blocked by the administrator. "
                "If you believe this is a mistake, please contact support."
            )
            return redirect(url_for("login"))
        if not g.user.is_super_admin:
            flash("Super admin access required.", "error")
            return redirect(url_for("admin_users_page"))
        return view(*args, **kwargs)

    return wrapped_view


def hash_password(password):
    return bcrypt.generate_password_hash(password).decode("utf-8")


def verify_password(stored_hash, password):
    if stored_hash.startswith("$2a$") or stored_hash.startswith("$2b$"):
        return bcrypt.check_password_hash(stored_hash, password)
    return check_password_hash(stored_hash, password)


def generate_otp_code():
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(6))


def build_session_token_hash(raw_token):
    secret = app.config.get("SECRET_KEY", "")
    payload = f"{secret}:{raw_token}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def create_user_session_record(user):
    raw_token = secrets.token_urlsafe(32)
    token_hash = build_session_token_hash(raw_token)
    now = datetime.utcnow()
    session["auth_session_token"] = raw_token
    db.session.add(
        UserSession(
            user_id=user.user_id,
            session_token=token_hash,
            ip_address=request.remote_addr,
            user_agent=(request.user_agent.string or "")[:255],
            login_time=now,
            last_active=now,
            is_active=True,
        )
    )


def resolve_current_user_session(user_id):
    raw_token = session.get("auth_session_token")
    if not raw_token:
        return None
    token_hash = build_session_token_hash(raw_token)
    return UserSession.query.filter_by(user_id=user_id, session_token=token_hash).first()


def summarize_user_agent(user_agent_text):
    ua = (user_agent_text or "").strip()
    if not ua:
        return "Unknown device"
    lowered = ua.lower()

    if "edg/" in lowered:
        browser = "Edge"
    elif "chrome/" in lowered and "edg/" not in lowered:
        browser = "Chrome"
    elif "firefox/" in lowered:
        browser = "Firefox"
    elif "safari/" in lowered and "chrome/" not in lowered:
        browser = "Safari"
    else:
        browser = "Browser"

    if "windows" in lowered:
        device = "Windows"
    elif "android" in lowered:
        device = "Android"
    elif "iphone" in lowered or "ipad" in lowered or "ios" in lowered:
        device = "iOS"
    elif "mac os" in lowered or "macintosh" in lowered:
        device = "macOS"
    elif "linux" in lowered:
        device = "Linux"
    else:
        device = "Device"

    return f"{device} - {browser}"


def verify_recaptcha(recaptcha_response, remote_ip=None):
    if not app.config.get("RECAPTCHA_ENABLED", False):
        return True, None

    secret = (app.config.get("RECAPTCHA_SECRET_KEY") or "").strip()
    if not secret or secret == "YOUR_SECRET_KEY":
        return False, "Captcha verification is not configured. Please try again later."

    payload = {
        "secret": secret,
        "response": recaptcha_response,
    }
    if remote_ip:
        payload["remoteip"] = remote_ip

    try:
        encoded_payload = parse.urlencode(payload).encode("utf-8")
        req = urllib_request.Request(
            "https://www.google.com/recaptcha/api/siteverify",
            data=encoded_payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib_request.urlopen(req, timeout=8) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (URLError, ValueError, TimeoutError) as exc:
        app.logger.warning("reCAPTCHA verification failed: %s", exc)
        return False, "Captcha verification is unavailable right now. Please try again."

    if not result.get("success"):
        return False, "Captcha verification failed. Please confirm you are not a robot."

    return True, None


def build_email_html(
    title,
    username,
    body_lines,
    otp_label=None,
    otp_value=None,
    body_lines_after_otp=None,
):
    body_html = "".join(
        f'<p style="margin: 0 0 14px; color: #1f2937; font-size: 15px; line-height: 1.6;">{line}</p>'
        for line in body_lines
    )
    body_html_after_otp = ""
    if body_lines_after_otp:
        body_html_after_otp = "".join(
            f'<p style="margin: 0 0 14px; color: #1f2937; font-size: 15px; line-height: 1.6;">{line}</p>'
            for line in body_lines_after_otp
        )

    otp_block = ""
    if otp_label and otp_value:
        otp_block = (
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" '
            'style="margin: 0 0 16px;">'
            '<tr>'
            '<td style="background: #eef2ff; border-radius: 8px; padding: 16px; text-align: center;">'
            f'<div style="font-size: 13px; color: #4b5563; margin-bottom: 8px;">{otp_label}</div>'
            f'<div style="font-size: 28px; font-weight: 700; letter-spacing: 3px; color: #111827;">{otp_value}</div>'
            '</td>'
            '</tr>'
            '</table>'
        )

    return (
        '<!doctype html>'
        '<html>'
        '<head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>SkillSwap</title>'
        '</head>'
        '<body style="margin: 0; padding: 0; background: #f5f7fa; font-family: Arial, sans-serif;">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" '
        'style="background: #f5f7fa; padding: 24px 12px;">'
        '<tr>'
        '<td align="center">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" '
        'style="max-width: 600px; background: #ffffff; border-radius: 8px;">'
        '<tr>'
        '<td style="padding: 24px 24px 14px; text-align: center; border-bottom: 1px solid #e5e7eb;">'
        '<div style="font-size: 24px; font-weight: 700; color: #0f766e;">SkillSwap</div>'
        '</td>'
        '</tr>'
        '<tr>'
        '<td style="padding: 22px 24px 24px;">'
        f'<h2 style="margin: 0 0 14px; color: #111827; font-size: 18px; line-height: 1.3;">{title}</h2>'
        f'<p style="margin: 0 0 14px; color: #1f2937; font-size: 15px; line-height: 1.6;">Hello {username},</p>'
        f'{body_html}'
        f'{otp_block}'
        f'{body_html_after_otp}'
        '</td>'
        '</tr>'
        '</table>'
        '</td>'
        '</tr>'
        '</table>'
        '</body>'
        '</html>'
    )


def send_otp_email(recipient_email, otp_code, username):
    configured_username = (app.config.get("MAIL_USERNAME") or "").strip()
    configured_password = (app.config.get("MAIL_PASSWORD") or "").strip()
    if not configured_username or not configured_password:
        raise RuntimeError("Mail configuration is incomplete. Set MAIL_USERNAME and MAIL_PASSWORD.")

    msg = MailMessage(
        subject="Verify your SkillSwap account",
        sender=app.config["MAIL_DEFAULT_SENDER"],
        recipients=[recipient_email],
    )
    msg.body = (
        f"Hello {username},\n\n"
        "Thank you for signing up with SkillSwap.\n\n"
        "To complete your account verification, please use the code below:\n\n"
        f"Verification Code: {otp_code}\n\n"
        "This code will expire in 10 minutes.\n\n"
        "If you did not request this, please ignore this email."
    )
    msg.html = (
        '<!doctype html>'
        '<html>'
        '<head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>SkillSwap</title>'
        '</head>'
        '<body style="margin: 0; padding: 0; background: #f5f7fa; font-family: Arial, sans-serif;">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background: #f5f7fa; padding: 24px 12px;">'
        '<tr><td align="center">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="max-width: 600px; background: #ffffff; border-radius: 8px;">'
        '<tr><td style="padding: 24px 24px 14px; text-align: center; border-bottom: 1px solid #e5e7eb;">'
        '<div style="font-size: 24px; font-weight: 700; color: #0f766e;">SkillSwap</div>'
        '</td></tr>'
        '<tr><td style="padding: 22px 24px 24px;">'
        '<h2 style="margin: 0 0 14px; color: #111827; font-size: 18px; line-height: 1.3;">Account Verification</h2>'
        f'<p style="margin: 0 0 14px; color: #1f2937; font-size: 15px; line-height: 1.6;">Hello {username},</p>'
        '<p style="margin: 0 0 14px; color: #1f2937; font-size: 15px; line-height: 1.6;">Thank you for signing up with SkillSwap.</p>'
        '<p style="margin: 0 0 14px; color: #1f2937; font-size: 15px; line-height: 1.6;">To complete your account verification, please use the code below:</p>'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin: 0 0 16px;">'
        '<tr><td style="background: #eef2ff; border-radius: 8px; padding: 16px; text-align: center;">'
        '<div style="font-size: 13px; color: #4b5563; margin-bottom: 8px;">Verification Code</div>'
        f'<div style="font-size: 28px; font-weight: 700; letter-spacing: 3px; color: #111827;">{otp_code}</div>'
        '</td></tr>'
        '</table>'
        '<p style="margin: 0 0 14px; color: #1f2937; font-size: 15px; line-height: 1.6;">This code will expire in 10 minutes.</p>'
        '<p style="margin: 0; color: #1f2937; font-size: 15px; line-height: 1.6;">If you did not request this, please ignore this email.</p>'
        '</td></tr>'
        '</table>'
        '</td></tr>'
        '</table>'
        '</body>'
        '</html>'
    )
    app.logger.info("Sending OTP verification email")
    mail.send(msg)
    app.logger.info("OTP verification email sent")


def send_welcome_email(recipient_email, recipient_name):
    configured_username = (app.config.get("MAIL_USERNAME") or "").strip()
    configured_password = (app.config.get("MAIL_PASSWORD") or "").strip()
    if not configured_username or not configured_password:
        raise RuntimeError("Mail configuration is incomplete. Set MAIL_USERNAME and MAIL_PASSWORD.")

    msg = MailMessage(
        subject="Welcome to SkillSwap",
        sender=app.config["MAIL_DEFAULT_SENDER"],
        recipients=[recipient_email],
    )
    msg.body = (
        f"Hello {recipient_name},\n\n"
        "Welcome to SkillSwap!\n\n"
        "Your account has been successfully verified. You can now log in and start exploring "
        "skill exchanges, connect with others, and grow your expertise."
    )
    msg.html = build_email_html(
        title="Welcome",
        username=recipient_name,
        body_lines=[
            "Welcome to SkillSwap!",
            "Your account has been successfully verified. You can now log in and start exploring skill exchanges, connect with others, and grow your expertise.",
        ],
    )
    app.logger.info("Sending welcome email")
    last_error = None
    for _ in range(2):
        try:
            with mail.connect() as connection:
                connection.send(msg)
            app.logger.info("Welcome email sent")
            return
        except Exception as exc:
            last_error = exc
    raise last_error


def send_password_reset_otp_email(recipient_email, otp_code, username):
    configured_username = (app.config.get("MAIL_USERNAME") or "").strip()
    configured_password = (app.config.get("MAIL_PASSWORD") or "").strip()
    if not configured_username or not configured_password:
        raise RuntimeError("Mail configuration is incomplete. Set MAIL_USERNAME and MAIL_PASSWORD.")

    msg = MailMessage(
        subject="Reset Your SkillSwap Password",
        sender=app.config["MAIL_DEFAULT_SENDER"],
        recipients=[recipient_email],
    )
    msg.body = (
        f"Hello {username},\n\n"
        "We received a request to reset your SkillSwap password.\n\n"
        "Use the verification code below to proceed:\n\n"
        f"Reset Code: {otp_code}\n\n"
        "This code will expire in 10 minutes.\n\n"
        "If you did not request a password reset, please ignore this email or contact support."
    )
    msg.html = build_email_html(
        title="Password Reset",
        username=username,
        body_lines=[
            "We received a request to reset your SkillSwap password.",
            "Use the verification code below to proceed:",
        ],
        otp_label="Reset Code",
        otp_value=otp_code,
        body_lines_after_otp=[
            "This code will expire in 10 minutes.",
            "If you did not request a password reset, please ignore this email or contact support.",
        ],
    )
    app.logger.info("Sending password reset OTP email")
    mail.send(msg)
    app.logger.info("Password reset OTP email sent")


def send_account_deleted_email(recipient_email, username):
    configured_username = (app.config.get("MAIL_USERNAME") or "").strip()
    configured_password = (app.config.get("MAIL_PASSWORD") or "").strip()
    if not configured_username or not configured_password:
        raise RuntimeError("Mail configuration is incomplete. Set MAIL_USERNAME and MAIL_PASSWORD.")

    msg = MailMessage(
        subject="Your SkillSwap Account Has Been Deleted",
        sender=app.config["MAIL_DEFAULT_SENDER"],
        recipients=[recipient_email],
    )
    msg.body = (
        f"Hello {username},\n\n"
        "Your account has been successfully deleted from SkillSwap.\n\n"
        "Thank you for using SkillSwap."
    )
    msg.html = build_email_html(
        title="Account Deleted",
        username=username,
        body_lines=[
            "Your SkillSwap account has been successfully deleted.",
            "Thank you for using SkillSwap.",
        ],
    )
    app.logger.info("Sending account deletion confirmation email")
    mail.send(msg)
    app.logger.info("Account deletion confirmation email sent")


def sync_schema_and_admin():
    db.create_all()
    inspector = inspect(db.engine)
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    skill_columns = {column["name"] for column in inspector.get_columns("skills")}
    offered_columns = {column["name"] for column in inspector.get_columns("user_skills_offered")}
    wanted_columns = {column["name"] for column in inspector.get_columns("user_skills_wanted")}
    request_columns = {column["name"] for column in inspector.get_columns("requests")}
    rating_columns = {column["name"] for column in inspector.get_columns("ratings")}

    if "categories" not in inspector.get_table_names():
        Category.__table__.create(bind=db.engine)
        inspector = inspect(db.engine)

    if "user_skills" not in inspector.get_table_names():
        UserSkill.__table__.create(bind=db.engine)
        inspector = inspect(db.engine)

    if "role" not in user_columns:
        db.session.execute(
            text("ALTER TABLE users ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'user'")
        )
    if "is_blocked" not in user_columns:
        db.session.execute(
            text("ALTER TABLE users ADD COLUMN is_blocked BOOLEAN NOT NULL DEFAULT FALSE")
        )
    if "availability_status" not in user_columns:
        db.session.execute(
            text(
                "ALTER TABLE users ADD COLUMN availability_status VARCHAR(20) "
                "NOT NULL DEFAULT 'Available'"
            )
        )
    if "profile_image" not in user_columns:
        db.session.execute(text("ALTER TABLE users ADD COLUMN profile_image VARCHAR(255) NULL"))
    try:
        db.session.execute(text("ALTER TABLE users MODIFY COLUMN role VARCHAR(20) NOT NULL DEFAULT 'user'"))
    except Exception:
        pass
    db.session.execute(text("UPDATE users SET role = 'user' WHERE role IS NULL OR role = ''"))
    if "username" not in user_columns:
        db.session.execute(text("ALTER TABLE users ADD COLUMN username VARCHAR(40) NULL"))
    if "bio" not in user_columns:
        db.session.execute(text("ALTER TABLE users ADD COLUMN bio TEXT NULL"))
    if "location" not in user_columns:
        db.session.execute(text("ALTER TABLE users ADD COLUMN location VARCHAR(120) NULL"))
    if "show_email_on_profile" not in user_columns:
        db.session.execute(
            text("ALTER TABLE users ADD COLUMN show_email_on_profile BOOLEAN NOT NULL DEFAULT FALSE")
        )
    if "last_seen_at" not in user_columns:
        db.session.execute(text("ALTER TABLE users ADD COLUMN last_seen_at DATETIME NULL"))
    if "is_verified" not in user_columns:
        db.session.execute(
            text("ALTER TABLE users ADD COLUMN is_verified BOOLEAN NOT NULL DEFAULT FALSE")
        )
    if "otp_code" not in user_columns:
        db.session.execute(text("ALTER TABLE users ADD COLUMN otp_code TEXT NULL"))
    if "otp_expiry" not in user_columns:
        db.session.execute(text("ALTER TABLE users ADD COLUMN otp_expiry DATETIME NULL"))
    if "description" not in skill_columns:
        db.session.execute(text("ALTER TABLE skills ADD COLUMN description VARCHAR(255) NULL"))
    if "status" not in skill_columns:
        db.session.execute(
            text("ALTER TABLE skills ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'active'")
        )
    if "category_id" not in skill_columns:
        db.session.execute(text("ALTER TABLE skills ADD COLUMN category_id INTEGER NULL"))
    if "level" not in offered_columns:
        db.session.execute(
            text(
                "ALTER TABLE user_skills_offered ADD COLUMN level VARCHAR(20) "
                "NOT NULL DEFAULT 'Intermediate'"
            )
        )
    if "level" not in wanted_columns:
        db.session.execute(
            text(
                "ALTER TABLE user_skills_wanted ADD COLUMN level VARCHAR(20) "
                "NOT NULL DEFAULT 'Beginner'"
            )
        )
    if "is_completed_by_sender" not in request_columns:
        db.session.execute(
            text(
                "ALTER TABLE requests ADD COLUMN is_completed_by_sender BOOLEAN "
                "NOT NULL DEFAULT FALSE"
            )
        )
    if "is_completed_by_receiver" not in request_columns:
        db.session.execute(
            text(
                "ALTER TABLE requests ADD COLUMN is_completed_by_receiver BOOLEAN "
                "NOT NULL DEFAULT FALSE"
            )
        )
    if "rated_by_sender" not in request_columns:
        db.session.execute(
            text("ALTER TABLE requests ADD COLUMN rated_by_sender BOOLEAN NOT NULL DEFAULT FALSE")
        )
    if "rated_by_receiver" not in request_columns:
        db.session.execute(
            text("ALTER TABLE requests ADD COLUMN rated_by_receiver BOOLEAN NOT NULL DEFAULT FALSE")
        )
    if "sender_confirmed" not in request_columns:
        db.session.execute(
            text("ALTER TABLE requests ADD COLUMN sender_confirmed BOOLEAN NOT NULL DEFAULT FALSE")
        )
    if "receiver_confirmed" not in request_columns:
        db.session.execute(
            text("ALTER TABLE requests ADD COLUMN receiver_confirmed BOOLEAN NOT NULL DEFAULT FALSE")
        )
    if "final_offered_skill_id" not in request_columns:
        db.session.execute(text("ALTER TABLE requests ADD COLUMN final_offered_skill_id INTEGER NULL"))
    if "final_requested_skill_id" not in request_columns:
        db.session.execute(text("ALTER TABLE requests ADD COLUMN final_requested_skill_id INTEGER NULL"))
    if "session_room" not in request_columns:
        db.session.execute(text("ALTER TABLE requests ADD COLUMN session_room VARCHAR(120) NULL"))
    if "session_link" not in request_columns:
        db.session.execute(text("ALTER TABLE requests ADD COLUMN session_link VARCHAR(255) NULL"))
    if "session_scheduled_for" not in request_columns:
        db.session.execute(text("ALTER TABLE requests ADD COLUMN session_scheduled_for DATETIME NULL"))
    if "session_proposed_by" not in request_columns:
        db.session.execute(text("ALTER TABLE requests ADD COLUMN session_proposed_by INTEGER NULL"))
    if "session_confirmed_at" not in request_columns:
        db.session.execute(text("ALTER TABLE requests ADD COLUMN session_confirmed_at DATETIME NULL"))
    if "session_started_at" not in request_columns:
        db.session.execute(text("ALTER TABLE requests ADD COLUMN session_started_at DATETIME NULL"))
    if "session_completed_at" not in request_columns:
        db.session.execute(text("ALTER TABLE requests ADD COLUMN session_completed_at DATETIME NULL"))
    if "session_sender_last_ping_at" not in request_columns:
        db.session.execute(text("ALTER TABLE requests ADD COLUMN session_sender_last_ping_at DATETIME NULL"))
    if "session_receiver_last_ping_at" not in request_columns:
        db.session.execute(text("ALTER TABLE requests ADD COLUMN session_receiver_last_ping_at DATETIME NULL"))
    db.session.execute(
        text("ALTER TABLE requests MODIFY COLUMN status VARCHAR(40) NOT NULL DEFAULT 'pending'")
    )
    if "exchange_request_id" not in rating_columns:
        db.session.execute(text("ALTER TABLE ratings ADD COLUMN exchange_request_id INTEGER NULL"))

    # Performance-focused indexes for high-frequency lookups.
    indexes = {idx["name"] for idx in inspector.get_indexes("users")}
    if "idx_users_email" not in indexes:
        db.session.execute(text("CREATE INDEX idx_users_email ON users(email)"))
    if "idx_users_username" not in indexes:
        db.session.execute(text("CREATE INDEX idx_users_username ON users(username)"))

    indexes = {idx["name"] for idx in inspector.get_indexes("skills")}
    if "idx_skills_name" not in indexes:
        db.session.execute(text("CREATE INDEX idx_skills_name ON skills(skill_name)"))
    if "idx_skills_category_id" not in indexes:
        try:
            db.session.execute(text("CREATE INDEX idx_skills_category_id ON skills(category_id)"))
        except Exception:
            pass

    category_indexes = {idx["name"] for idx in inspector.get_indexes("categories")}
    if "idx_categories_name" not in category_indexes:
        try:
            db.session.execute(text("CREATE INDEX idx_categories_name ON categories(name)"))
        except Exception:
            pass

    user_skill_indexes = {idx["name"] for idx in inspector.get_indexes("user_skills")}
    if "idx_user_skills_user_id" not in user_skill_indexes:
        try:
            db.session.execute(text("CREATE INDEX idx_user_skills_user_id ON user_skills(user_id)"))
        except Exception:
            pass
    if "idx_user_skills_skill_id" not in user_skill_indexes:
        try:
            db.session.execute(text("CREATE INDEX idx_user_skills_skill_id ON user_skills(skill_id)"))
        except Exception:
            pass

    indexes = {idx["name"] for idx in inspector.get_indexes("requests")}
    if "idx_requests_status" not in indexes:
        db.session.execute(text("CREATE INDEX idx_requests_status ON requests(status)"))

    rating_indexes = {idx["name"] for idx in inspector.get_indexes("ratings")}
    if "uq_rating_exchange_from_user" not in rating_indexes:
        try:
            db.session.execute(
                text(
                    "CREATE UNIQUE INDEX uq_rating_exchange_from_user "
                    "ON ratings(exchange_request_id, from_user)"
                )
            )
        except Exception:
            pass

    if "messages" not in inspector.get_table_names():
        Message.__table__.create(bind=db.engine)
    else:
        message_columns = {column["name"] for column in inspector.get_columns("messages")}
        if "read_at" not in message_columns:
            db.session.execute(text("ALTER TABLE messages ADD COLUMN read_at DATETIME NULL"))
        if "message_type" not in message_columns:
            db.session.execute(
                text("ALTER TABLE messages ADD COLUMN message_type VARCHAR(20) NOT NULL DEFAULT 'user'")
            )
        if "attachment_url" not in message_columns:
            db.session.execute(text("ALTER TABLE messages ADD COLUMN attachment_url TEXT NULL"))
        if "attachment_type" not in message_columns:
            db.session.execute(text("ALTER TABLE messages ADD COLUMN attachment_type TEXT NULL"))

    if "notifications" not in inspector.get_table_names():
        Notification.__table__.create(bind=db.engine)
    if "user_reports" not in inspector.get_table_names():
        UserReport.__table__.create(bind=db.engine)
    else:
        user_report_columns = {column["name"] for column in inspector.get_columns("user_reports")}
        if "report_attachments" not in user_report_columns:
            db.session.execute(text("ALTER TABLE user_reports ADD COLUMN report_attachments TEXT NULL"))
            if "report_attachment_url" in user_report_columns:
                db.session.execute(
                    text(
                        "UPDATE user_reports "
                        "SET report_attachments = CONCAT('[\"', report_attachment_url, '\"]') "
                        "WHERE report_attachment_url IS NOT NULL AND report_attachment_url <> ''"
                    )
                )

    if "blocked_users" not in inspector.get_table_names():
        BlockedUser.__table__.create(bind=db.engine)

    if "user_sessions" not in inspector.get_table_names():
        UserSession.__table__.create(bind=db.engine)
    else:
        session_columns = {column["name"] for column in inspector.get_columns("user_sessions")}
        if "ip_address" not in session_columns:
            db.session.execute(text("ALTER TABLE user_sessions ADD COLUMN ip_address VARCHAR(64) NULL"))
        if "user_agent" not in session_columns:
            db.session.execute(text("ALTER TABLE user_sessions ADD COLUMN user_agent VARCHAR(255) NULL"))
        if "login_time" not in session_columns:
            db.session.execute(text("ALTER TABLE user_sessions ADD COLUMN login_time DATETIME NULL"))
            db.session.execute(
                text("UPDATE user_sessions SET login_time = NOW() WHERE login_time IS NULL")
            )
        if "last_active" not in session_columns:
            db.session.execute(text("ALTER TABLE user_sessions ADD COLUMN last_active DATETIME NULL"))
            db.session.execute(
                text("UPDATE user_sessions SET last_active = NOW() WHERE last_active IS NULL")
            )
        if "is_active" not in session_columns:
            db.session.execute(
                text("ALTER TABLE user_sessions ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT TRUE")
            )

    if "platform_settings" not in inspector.get_table_names():
        PlatformSetting.__table__.create(bind=db.engine)
        inspector = inspect(db.engine)

    for setting_key in sorted(DYNAMIC_BOOLEAN_SETTING_KEYS):
        existing_setting = PlatformSetting.query.filter_by(key=setting_key).first()
        if not existing_setting:
            db.session.add(
                PlatformSetting(
                    key=setting_key,
                    value=_bool_to_setting_text(app.config.get(setting_key, False)),
                )
            )

    try:
        blocked_user_indexes = {idx["name"] for idx in inspect(db.engine).get_indexes("blocked_users")}
    except Exception:
        blocked_user_indexes = set()
    if "idx_blocked_users_blocker" not in blocked_user_indexes:
        try:
            db.session.execute(text("CREATE INDEX idx_blocked_users_blocker ON blocked_users(blocker_id)"))
        except Exception:
            pass
    if "idx_blocked_users_blocked" not in blocked_user_indexes:
        try:
            db.session.execute(text("CREATE INDEX idx_blocked_users_blocked ON blocked_users(blocked_id)"))
        except Exception:
            pass

    try:
        user_session_indexes = {idx["name"] for idx in inspect(db.engine).get_indexes("user_sessions")}
    except Exception:
        user_session_indexes = set()
    if "idx_user_sessions_user_id" not in user_session_indexes:
        try:
            db.session.execute(text("CREATE INDEX idx_user_sessions_user_id ON user_sessions(user_id)"))
        except Exception:
            pass
    if "idx_user_sessions_last_active" not in user_session_indexes:
        try:
            db.session.execute(
                text("CREATE INDEX idx_user_sessions_last_active ON user_sessions(last_active)")
            )
        except Exception:
            pass

    # Keep status constraint aligned with two-sided completion flow.
    try:
        db.session.execute(text("ALTER TABLE requests DROP CHECK ck_request_status"))
    except Exception:
        pass
    try:
        db.session.execute(
            text(
                "ALTER TABLE requests ADD CONSTRAINT ck_request_status "
                "CHECK (status IN ('pending', 'countered', 'accepted', 'rejected', 'awaiting_confirmation', 'completed', 'terminated'))"
            )
        )
    except Exception:
        pass

    db.session.execute(
        text(
            "UPDATE users "
            "SET availability_status = CASE "
            "WHEN availability = TRUE THEN 'Available' ELSE 'Unavailable' END "
            "WHERE availability_status IS NULL OR availability_status = ''"
        )
    )
    db.session.execute(
        text("UPDATE skills SET status = 'active' WHERE status IS NULL OR status = ''")
    )
    db.session.execute(
        text("UPDATE skills SET status = 'blocked' WHERE LOWER(status) = 'inactive'")
    )

    existing_categories = {
        row[0].strip().lower()
        for row in db.session.query(Category.name).all()
        if row[0] and row[0].strip()
    }
    seed_categories = set(SKILL_CATEGORIES)
    dynamic_categories = {
        row[0].strip()
        for row in db.session.query(Skill.category).filter(Skill.category.isnot(None), Skill.category != "").all()
        if row[0] and row[0].strip()
    }
    for category_name in sorted(seed_categories.union(dynamic_categories), key=lambda v: v.lower()):
        key = category_name.strip().lower()
        if key in existing_categories:
            continue
        db.session.add(Category(name=category_name.strip()))
        existing_categories.add(key)
    db.session.flush()

    uncategorized = Category.query.filter(db.func.lower(Category.name) == "uncategorized").first()
    if not uncategorized:
        uncategorized = Category(name="Uncategorized")
        db.session.add(uncategorized)
        db.session.flush()

    category_lookup = {
        row.name.strip().lower(): row.category_id
        for row in Category.query.all()
        if row.name and row.name.strip()
    }
    skills_without_category_id = Skill.query.filter(
        or_(Skill.category_id.is_(None), Skill.category_id == 0)
    ).all()
    for skill in skills_without_category_id:
        category_name = (skill.category or "").strip() or "Uncategorized"
        skill.category_id = category_lookup.get(category_name.lower(), uncategorized.category_id)
        skill.category = category_name

    db.session.execute(
        text(
            "INSERT INTO user_skills (user_id, skill_id) "
            "SELECT user_id, skill_id FROM user_skills_offered "
            "UNION "
            "SELECT user_id, skill_id FROM user_skills_wanted "
            "ON DUPLICATE KEY UPDATE skill_id = VALUES(skill_id)"
        )
    )
    db.session.execute(
        text(
            "UPDATE users "
            "SET is_verified = TRUE "
            "WHERE is_verified = FALSE AND (otp_code IS NULL OR otp_code = '')"
        )
    )
    db.session.execute(
        text(
            "UPDATE users SET is_verified = TRUE, otp_code = NULL, otp_expiry = NULL "
            "WHERE LOWER(email) = :admin_email"
        ),
        {"admin_email": ADMIN_EMAIL},
    )
    super_admin_user = User.query.filter(User.role == "super_admin").first()
    if not super_admin_user:
        super_admin_user = User.query.filter(db.func.lower(User.email) == ADMIN_EMAIL).first()
        if not super_admin_user:
            db.session.info[ROLE_CHANGE_CONTEXT_KEY] = {
                "reason": "bootstrap_super_admin_creation",
                "actor_user_id": None,
                "source": "sync_schema_and_admin",
            }
            super_admin_user = User(
                name="SkillSwap Admin",
                username="skillswap_admin",
                email=ADMIN_EMAIL,
                password=hash_password(SUPER_ADMIN_DEFAULT_PASSWORD),
                role="super_admin",
                is_verified=True,
                otp_code=None,
                otp_expiry=None,
            )
            db.session.add(super_admin_user)
        else:
            db.session.info[ROLE_CHANGE_CONTEXT_KEY] = {
                "reason": "bootstrap_super_admin_promotion",
                "actor_user_id": None,
                "source": "sync_schema_and_admin",
            }
            super_admin_user.role = "super_admin"
            super_admin_user.is_verified = True
            super_admin_user.otp_code = None
            super_admin_user.otp_expiry = None

    if super_admin_user and not verify_password(
        super_admin_user.password,
        SUPER_ADMIN_DEFAULT_PASSWORD,
    ):
        super_admin_user.password = hash_password(SUPER_ADMIN_DEFAULT_PASSWORD)

    db.session.flush()
    canonical_username = "skillswap_admin"
    conflicting_user = User.query.filter(
        User.username == canonical_username,
        User.user_id != super_admin_user.user_id,
    ).first()
    if conflicting_user:
        fallback_base = f"user{conflicting_user.user_id}"
        candidate = fallback_base
        idx = 1
        while User.query.filter(User.username == candidate, User.user_id != conflicting_user.user_id).first():
            idx += 1
            candidate = f"{fallback_base}_{idx}"
        conflicting_user.username = candidate
    super_admin_user.username = canonical_username

    db.session.execute(
        text(
            "UPDATE requests SET sender_confirmed = is_completed_by_sender, "
            "receiver_confirmed = is_completed_by_receiver "
            "WHERE sender_confirmed = FALSE AND receiver_confirmed = FALSE"
        )
    )

    existing_usernames = set()
    all_users = User.query.order_by(User.user_id.asc()).all()
    for user in all_users:
        if user.username and user.username not in existing_usernames:
            existing_usernames.add(user.username)
            continue

        base = re.sub(r"[^a-z0-9_]", "", (user.name or "").lower().replace(" ", "_"))
        if not base:
            base = f"user{user.user_id}"
        candidate = base
        idx = 1
        while candidate in existing_usernames:
            idx += 1
            candidate = f"{base}{idx}"
        user.username = candidate
        existing_usernames.add(candidate)

    db.session.execute(
        text("UPDATE users SET username = CONCAT('user', user_id) WHERE username IS NULL OR username = ''")
    )
    db.session.execute(
        text(
            "UPDATE requests SET rated_by_sender = sender_rated, rated_by_receiver = receiver_rated "
            "WHERE rated_by_sender = FALSE AND rated_by_receiver = FALSE"
        )
    )
    try:
        db.session.execute(text("ALTER TABLE users MODIFY COLUMN username VARCHAR(40) NOT NULL"))
    except Exception:
        pass
    try:
        db.session.execute(text("ALTER TABLE users ADD CONSTRAINT uq_users_username UNIQUE (username)"))
    except Exception:
        pass
    db.session.commit()


def _current_role_change_context():
    ctx = _request_role_change_context.get()
    if ctx:
        return ctx
    return db.session.info.get(ROLE_CHANGE_CONTEXT_KEY)


def _set_role_change_context(reason, actor_user_id=None, source=None):
    context = {
        "reason": reason,
        "actor_user_id": actor_user_id,
        "source": source or (request.endpoint if has_request_context() else "unknown"),
    }
    _request_role_change_context.set(context)
    db.session.info[ROLE_CHANGE_CONTEXT_KEY] = context


def set_user_role(target_user, new_role, actor_user=None, reason="manual_role_change"):
    normalized_role = (new_role or "").strip().lower()
    if normalized_role not in ALLOWED_ROLES:
        raise ValueError("Invalid role value.")

    actor_user_id = actor_user.user_id if actor_user else None
    _set_role_change_context(
        reason=reason,
        actor_user_id=actor_user_id,
        source=request.endpoint if has_request_context() else "system",
    )
    target_user.role = normalized_role


@event.listens_for(OrmSession, "before_flush")
def enforce_role_change_policy(session_obj, flush_context, instances):
    ctx = session_obj.info.get(ROLE_CHANGE_CONTEXT_KEY) or _request_role_change_context.get()

    for obj in session_obj.new:
        if not isinstance(obj, User):
            continue
        role_value = (obj.role or "").strip().lower() or "user"
        if role_value not in ALLOWED_ROLES:
            raise ValueError("Invalid role value on user creation.")
        if role_value != "user" and not ctx:
            app.logger.error(
                "Unauthorized non-user role assignment on create blocked. "
                "target_user_id=%s role=%s source=%s",
                getattr(obj, "user_id", None),
                role_value,
                request.endpoint if has_request_context() else "system",
            )
            raise ValueError("Unauthorized role assignment blocked.")
        obj.role = role_value

    for obj in session_obj.dirty:
        if not isinstance(obj, User):
            continue
        role_history = inspect(obj).attrs.role.history
        if not role_history.has_changes():
            continue

        old_role = (role_history.deleted[0] if role_history.deleted else "").strip().lower()
        new_role = (role_history.added[0] if role_history.added else obj.role or "").strip().lower()

        if new_role not in ALLOWED_ROLES:
            raise ValueError("Invalid role value during update.")
        if not ctx:
            app.logger.error(
                "Unauthorized role mutation blocked. target_user_id=%s old_role=%s new_role=%s source=%s",
                obj.user_id,
                old_role or "unknown",
                new_role,
                request.endpoint if has_request_context() else "system",
            )
            raise ValueError("Unauthorized role mutation blocked.")

        app.logger.warning(
            "Role changed. target_user_id=%s old_role=%s new_role=%s actor_user_id=%s reason=%s source=%s",
            obj.user_id,
            old_role or "unknown",
            new_role,
            ctx.get("actor_user_id"),
            ctx.get("reason"),
            ctx.get("source"),
        )
        obj.role = new_role


@event.listens_for(OrmSession, "after_flush")
def clear_role_change_policy_context(session_obj, flush_context):
    session_obj.info.pop(ROLE_CHANGE_CONTEXT_KEY, None)
    _request_role_change_context.set(None)


@event.listens_for(OrmSession, "after_rollback")
def clear_role_change_policy_context_on_rollback(session_obj):
    session_obj.info.pop(ROLE_CHANGE_CONTEXT_KEY, None)
    _request_role_change_context.set(None)


def get_unread_message_counts(user_id):
    rows = (
        db.session.query(Message.sender_id, db.func.count(Message.message_id))
        .filter(Message.receiver_id == user_id, Message.read_at.is_(None))
        .group_by(Message.sender_id)
        .all()
    )
    return {sender_id: count for sender_id, count in rows}


def has_user_blocked(blocker_id, blocked_id):
    if not blocker_id or not blocked_id or blocker_id == blocked_id:
        return False
    return (
        db.session.query(BlockedUser.id)
        .filter(BlockedUser.blocker_id == blocker_id, BlockedUser.blocked_id == blocked_id)
        .first()
        is not None
    )


def is_user_blocked_between(user_a_id, user_b_id):
    if not user_a_id or not user_b_id or user_a_id == user_b_id:
        return False
    return (
        db.session.query(BlockedUser.id)
        .filter(
            ((BlockedUser.blocker_id == user_a_id) & (BlockedUser.blocked_id == user_b_id))
            | ((BlockedUser.blocker_id == user_b_id) & (BlockedUser.blocked_id == user_a_id))
        )
        .first()
        is not None
    )


def get_blocked_related_user_ids(user_id):
    rows = (
        BlockedUser.query.filter(
            (BlockedUser.blocker_id == user_id) | (BlockedUser.blocked_id == user_id)
        ).all()
    )
    related_ids = set()
    for row in rows:
        if row.blocker_id == user_id:
            related_ids.add(row.blocked_id)
        else:
            related_ids.add(row.blocker_id)
    return related_ids


def create_notification(user_id, message, notif_type, link=None):
    db.session.add(
        Notification(
            user_id=user_id,
            message=message,
            notif_type=notif_type,
            link=link,
        )
    )


def get_request_other_user_id(req, user_id):
    if user_id == req.sender_id:
        return req.receiver_id
    if user_id == req.receiver_id:
        return req.sender_id
    return None


def ensure_request_session_link(req):
    if not app.config.get("ALLOW_SESSION_CREATION", True):
        req.session_link = None
        req.session_room = None
        return None

    if not req.session_room:
        req.session_room = f"skillswap-exchange-{req.request_id}-{uuid.uuid4().hex[:8]}"

    expected_link = f"{JITSI_BASE_URL}/{req.session_room}"
    if req.session_link != expected_link:
        req.session_link = expected_link
    req.session_started_at = req.session_started_at or datetime.utcnow()
    return req.session_link


def get_direct_session_meeting_link(req):
    return ensure_request_session_link(req)


def ensure_exchange_session_record(req):
    if not app.config.get("ALLOW_SESSION_CREATION", True):
        req.session_link = None
        req.session_room = None
        return None

    # Keep one canonical session link and start timestamp for all participants/admin.
    meeting_link = ensure_request_session_link(req)
    req.session_started_at = req.session_started_at or datetime.utcnow()
    req.session_completed_at = None
    return meeting_link


def ensure_session_monitor_record(req):
    changed = False

    if not req.session_room:
        seed = f"skillswap-{req.request_id}-{req.created_at.isoformat() if req.created_at else req.request_id}"
        stable_id = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8]
        req.session_room = f"skillswap-exchange-{req.request_id}-{stable_id}"
        changed = True

    expected_link = f"{JITSI_BASE_URL}/{req.session_room}"
    if req.session_link != expected_link:
        req.session_link = expected_link
        changed = True

    if not req.session_started_at:
        req.session_started_at = (
            req.session_confirmed_at
            or req.session_scheduled_for
            or req.created_at
            or req.updated_at
            or datetime.utcnow()
        )
        changed = True

    if req.status in {"completed", "terminated"} and not req.session_completed_at:
        req.session_completed_at = req.updated_at or datetime.utcnow()
        changed = True

    return changed


def apply_auto_expire_exchange_sessions(now=None):
    if not app.config.get("AUTO_EXPIRE_INACTIVE_SESSIONS", True):
        return

    now = now or datetime.utcnow()
    cutoff = now - timedelta(days=30)
    stale_rows = Request.query.filter(
        Request.status.in_(["accepted", "awaiting_confirmation"]),
        Request.session_completed_at.is_(None),
        db.func.coalesce(
            Request.session_sender_last_ping_at,
            Request.session_receiver_last_ping_at,
            Request.session_started_at,
            Request.session_confirmed_at,
            Request.updated_at,
            Request.created_at,
        ) <= cutoff,
    ).all()

    if not stale_rows:
        return

    for req in stale_rows:
        req.status = "terminated"
        req.session_completed_at = now
        req.session_sender_last_ping_at = None
        req.session_receiver_last_ping_at = None
        post_system_session_message(req, "Session automatically terminated after 1 month of inactivity.")

    db.session.commit()


def mark_session_participant_presence(req, user_id, is_active):
    if user_id == req.sender_id:
        req.session_sender_last_ping_at = datetime.utcnow() if is_active else None
    elif user_id == req.receiver_id:
        req.session_receiver_last_ping_at = datetime.utcnow() if is_active else None


def is_session_participant_active(req, user_id, window_seconds=30):
    cutoff = datetime.utcnow() - timedelta(seconds=window_seconds)
    if user_id == req.sender_id:
        return bool(req.session_sender_last_ping_at and req.session_sender_last_ping_at >= cutoff)
    if user_id == req.receiver_id:
        return bool(req.session_receiver_last_ping_at and req.session_receiver_last_ping_at >= cutoff)
    return False


def post_session_chat_message(req, sender_id, body):
    receiver_id = get_request_other_user_id(req, sender_id)
    if receiver_id is None:
        return
    db.session.add(
        Message(sender_id=sender_id, receiver_id=receiver_id, message=body, message_type="user")
    )


def post_system_session_message(req, body):
    db.session.add(
        Message(
            sender_id=req.sender_id,
            receiver_id=req.receiver_id,
            message=f"[SYSTEM] {body}",
            message_type="system",
        )
    )


def post_system_join_message_for_actor(req, actor_user_id, meeting_link):
    receiver_id = get_request_other_user_id(req, actor_user_id)
    if receiver_id is None:
        return
    db.session.add(
        Message(
            sender_id=actor_user_id,
            receiver_id=receiver_id,
            message=f"[SYSTEM] Session is now active. Join here: {meeting_link}",
            message_type="system",
        )
    )


def build_skill_details(user_id):
    offered_rows = (
        db.session.query(UserSkillsOffered, Skill)
        .join(Skill, UserSkillsOffered.skill_id == Skill.skill_id)
        .filter(UserSkillsOffered.user_id == user_id)
        .order_by(Skill.skill_name.asc())
        .all()
    )
    wanted_rows = (
        db.session.query(UserSkillsWanted, Skill)
        .join(Skill, UserSkillsWanted.skill_id == Skill.skill_id)
        .filter(UserSkillsWanted.user_id == user_id)
        .order_by(Skill.skill_name.asc())
        .all()
    )
    offered_details = [{"skill": s, "level": row.level} for row, s in offered_rows]
    wanted_details = [{"skill": s, "level": row.level} for row, s in wanted_rows]
    return offered_details, wanted_details


def allowed_image_file(filename):
    if not filename or "." not in filename:
        return False
    extension = filename.rsplit(".", 1)[1].lower()
    return extension in ALLOWED_IMAGE_EXTENSIONS


def allowed_attachment_file(filename):
    if not filename or "." not in filename:
        return False
    extension = filename.rsplit(".", 1)[1].lower()
    return extension in ALLOWED_ATTACHMENT_EXTENSIONS


def attachment_type_for_extension(extension):
    ext = (extension or "").lower()
    if ext in {"jpg", "jpeg", "png", "gif"}:
        return "image"
    if ext in {"mp4"}:
        return "video"
    if ext in {"mp3", "wav"}:
        return "audio"
    return "file"


def save_profile_image(file_storage, user_id):
    if not file_storage or not file_storage.filename:
        return None

    if not allowed_image_file(file_storage.filename):
        raise ValueError("Only JPG, JPEG, and PNG files are allowed.")

    if not (file_storage.mimetype or "").startswith("image/"):
        raise ValueError("Uploaded file must be an image.")

    file_size = file_storage.content_length
    if file_size is None:
        try:
            current_pos = file_storage.stream.tell()
            file_storage.stream.seek(0, os.SEEK_END)
            file_size = file_storage.stream.tell()
            file_storage.stream.seek(current_pos)
        except (AttributeError, OSError):
            file_size = None

    if file_size and file_size > PROFILE_IMAGE_MAX_BYTES:
        raise ValueError("Profile image must be 2MB or smaller.")

    try:
        file_storage.stream.seek(0)
    except (AttributeError, OSError):
        pass

    safe_name = secure_filename(file_storage.filename)
    extension = safe_name.rsplit(".", 1)[1].lower()
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    unique_name = f"user_{user_id}_{timestamp}_{uuid.uuid4().hex[:8]}.{extension}"
    target_path = os.path.join(UPLOAD_FOLDER, unique_name)

    file_storage.save(target_path)
    return f"{UPLOAD_SUBDIR}/{unique_name}"


def remove_old_profile_image(path_in_static):
    if not path_in_static:
        return
    normalized = path_in_static.replace("\\", "/")
    if not normalized.startswith(f"{UPLOAD_SUBDIR}/"):
        return

    old_path = os.path.join(app.static_folder, normalized)
    if os.path.exists(old_path):
        os.remove(old_path)


def compute_profile_completion(user, offered_count, wanted_count):
    completion = 0
    if user.profile_image:
        completion += 25
    if user.bio:
        completion += 25
    if user.location:
        completion += 15
    if offered_count > 0:
        completion += 20
    if wanted_count > 0:
        completion += 15
    return completion


def compute_user_trust_metrics(user):
    total_requests = Request.query.filter_by(receiver_id=user.user_id).count()
    accepted_requests = Request.query.filter(
        Request.receiver_id == user.user_id,
        Request.status.in_(["accepted", "awaiting_confirmation", "completed"]),
    ).count()
    completed_exchanges = Request.query.filter(
        Request.receiver_id == user.user_id,
        Request.status == "completed",
    ).count()

    response_rate = int(round((accepted_requests / total_requests) * 100)) if total_requests else 100
    completion_rate = (
        int(round((completed_exchanges / accepted_requests) * 100)) if accepted_requests else None
    )

    total_participation = Request.query.filter(
        (Request.sender_id == user.user_id) | (Request.receiver_id == user.user_id)
    ).count()

    average_rating = user.average_rating() or 0
    badges = []
    if response_rate >= 80 and (completion_rate or 0) >= 60 and average_rating >= 4:
        badges.append("Trusted User")
    if completed_exchanges >= 5 or ((completion_rate or 0) >= 70 and total_participation >= 5):
        badges.append("Top Exchanger")

    return {
        "response_rate": response_rate,
        "completion_rate": completion_rate,
        "completed_total": completed_exchanges,
        "accepted_requests": accepted_requests,
        "total_requests": total_requests,
        "total_participation": total_participation,
        "badges": badges,
    }


def level_similarity_score(my_level, other_level):
    my_weight = SKILL_LEVEL_WEIGHT.get(my_level, 2)
    other_weight = SKILL_LEVEL_WEIGHT.get(other_level, 2)
    distance = abs(my_weight - other_weight)
    if distance == 0:
        return 1.0
    if distance == 1:
        return 0.65
    return 0.35


def level_compatibility_points(provider_level, desired_level):
    provider_weight = SKILL_LEVEL_WEIGHT.get(provider_level, 2)
    desired_weight = SKILL_LEVEL_WEIGHT.get(desired_level, 2)
    diff = provider_weight - desired_weight
    if diff >= 2:
        return 30
    if diff == 1:
        return 25
    if diff == 0:
        return 20
    return 10


def availability_score(user):
    status = (user.availability_label or "").lower()
    if status == "available":
        return 1.0
    if status == "busy":
        return 0.7
    return 0.35


def build_presence_label(last_seen_at):
    if not last_seen_at:
        return "Last seen unavailable"

    age = datetime.utcnow() - last_seen_at
    total_seconds = int(age.total_seconds())
    if total_seconds <= PRESENCE_ACTIVE_SECONDS:
        return "Active now"
    if total_seconds < 60:
        return "Last seen just now"

    minutes = total_seconds // 60
    if minutes < 60:
        unit = "minute" if minutes == 1 else "minutes"
        return f"Last seen {minutes} {unit} ago"

    hours = minutes // 60
    if hours < 24:
        unit = "hour" if hours == 1 else "hours"
        return f"Last seen {hours} {unit} ago"

    return f"Last seen {last_seen_at.strftime('%Y-%m-%d %H:%M')}"


def _chat_rate_key():
    if g.user:
        return f"user:{g.user.user_id}"
    return f"ip:{request.remote_addr or 'unknown'}"


def _is_chat_rate_limited():
    now = datetime.utcnow()
    window_start = now - timedelta(seconds=CHAT_RATE_LIMIT_WINDOW_SECONDS)
    key = _chat_rate_key()

    with _chat_rate_limit_lock:
        recent_calls = _chat_rate_limit_store[key]
        while recent_calls and recent_calls[0] < window_start:
            recent_calls.popleft()
        if len(recent_calls) >= CHAT_RATE_LIMIT_MAX_REQUESTS:
            return True
        recent_calls.append(now)
    return False


def _chat_scope(role):
    return "admin" if role in {"admin", "super_admin"} else "user"


def _get_chat_history(role):
    scope = _chat_scope(role)
    all_history = session.get(CHAT_HISTORY_SESSION_KEY) or {}
    history = all_history.get(scope) or []
    return history[-CHAT_HISTORY_MAX_ITEMS:]


def _append_chat_history(role, user_text, assistant_text):
    scope = _chat_scope(role)
    all_history = session.get(CHAT_HISTORY_SESSION_KEY) or {}
    scoped = all_history.get(scope) or []
    scoped.append(
        {
            "user": (user_text or "")[:1000],
            "assistant": (assistant_text or "")[:2000],
            "at": datetime.utcnow().isoformat(),
        }
    )
    all_history[scope] = scoped[-CHAT_HISTORY_MAX_ITEMS:]
    session[CHAT_HISTORY_SESSION_KEY] = all_history


def _clear_chat_history(role=None):
    if role is None:
        session.pop(CHAT_HISTORY_SESSION_KEY, None)
        return

    scope = _chat_scope(role)
    all_history = session.get(CHAT_HISTORY_SESSION_KEY) or {}
    if scope in all_history:
        all_history.pop(scope, None)
        session[CHAT_HISTORY_SESSION_KEY] = all_history


def _history_as_text(role):
    lines = []
    for item in _get_chat_history(role)[-8:]:
        user_text = (item.get("user") or "").strip()
        assistant_text = (item.get("assistant") or "").strip()
        if user_text:
            lines.append(f"User: {user_text}")
        if assistant_text:
            lines.append(f"Assistant: {assistant_text}")
    return "\n".join(lines)


def _assistant_route_context(role):
    user_routes = [
        ("Home", "index"),
        ("Dashboard", "dashboard"),
        ("Skills", "skills"),
        ("Matches", "matches"),
        ("Requests", "requests_page"),
        ("Search", "search"),
    ]
    admin_routes = [
        ("Admin Dashboard", "admin_dashboard"),
        ("Admin Users", "admin_users_page"),
        ("Admin Skills", "admin_skills_page"),
        ("Admin Exchanges", "admin_exchanges_page"),
        ("Admin Reports", "admin_reports_page"),
        ("Admin Sessions", "admin_sessions_page"),
        ("Admin Analytics", "admin_analytics_page"),
        ("Admin Activity Logs", "admin_activity_logs_page"),
        ("Admin Feedback", "admin_feedback_page"),
        ("Admin Settings", "admin_settings_page"),
    ]

    rows = []
    route_set = user_routes + (admin_routes if role in {"admin", "super_admin"} else [])
    for label, endpoint in route_set:
        try:
            rows.append(f"- {label}: {url_for(endpoint)}")
        except Exception:
            continue
    return "\n".join(rows)


def _platform_capability_context(role):
    feature_lines = [
        "- Accounts: register/login, profile edit, profile image upload, password reset OTP",
        "- Skills: offered and wanted skills linked to categories",
        "- Matching: search and match discovery",
        "- Exchanges: request lifecycle (pending, countered, accepted, awaiting_confirmation, completed, rejected, terminated)",
        "- Sessions: Jitsi meeting links for accepted exchanges when session creation is enabled",
        "- Messaging: direct user-to-user messages and notifications",
        "- Reporting: user reports with moderation statuses",
        "- Feedback: ratings after exchanges",
    ]

    if role in {"admin", "super_admin"}:
        feature_lines.extend(
            [
                "- Admin modules: users, skills, exchanges, reports, sessions, analytics, activity logs, feedback, settings",
                "- Admin actions: exports and selected setting toggles handled by backend intents",
            ]
        )

    runtime_flags = [
        f"ALLOW_NEW_REGISTRATIONS={app.config.get('ALLOW_NEW_REGISTRATIONS')}",
        f"ALLOW_USER_REPORTS={app.config.get('ALLOW_USER_REPORTS')}",
        f"ALLOW_SESSION_CREATION={app.config.get('ALLOW_SESSION_CREATION')}",
    ]

    known_limits = [
        "- Do not assume button names, layout positions, or page-specific UI controls",
        "- Do not claim features outside listed modules and routes",
        "- Do not expose private account-level details in chat",
        "- If uncertain, reply: I'm not fully sure about that. Please check the relevant section.",
    ]

    schema_lines = [
        "- users, skills, user_skills_offered, user_skills_wanted, user_skills",
        "- requests, user_reports, ratings, messages, notifications, platform_settings",
    ]

    return (
        "Platform capabilities:\n"
        + "\n".join(feature_lines)
        + "\n\nRuntime flags:\n- "
        + "\n- ".join(runtime_flags)
        + "\n\nKnown limits:\n"
        + "\n".join(known_limits)
        + "\n\nDatabase structure (high level):\n"
        + "\n".join(schema_lines)
    )


def _chat_runtime_context():
    if not g.user:
        return "User context: guest (not logged in)."

    pending_requests = Request.query.filter(
        Request.status.in_(["pending", "countered"]),
        ((Request.sender_id == g.user.user_id) | (Request.receiver_id == g.user.user_id)),
    ).count()
    active_exchanges = Request.query.filter(
        Request.status.in_(["accepted", "awaiting_confirmation"]),
        ((Request.sender_id == g.user.user_id) | (Request.receiver_id == g.user.user_id)),
    ).count()
    completed_exchanges = Request.query.filter(
        Request.status == "completed",
        ((Request.sender_id == g.user.user_id) | (Request.receiver_id == g.user.user_id)),
    ).count()

    return (
        f"User context: logged in as @{g.user.username} ({g.user.role}). "
        f"Pending requests: {pending_requests}. "
        f"Active exchanges: {active_exchanges}. "
        f"Completed exchanges: {completed_exchanges}."
    )


def _model_candidates(primary_model, fallback_models_csv):
    candidates = []

    primary = (primary_model or "").strip()
    if primary:
        candidates.append(primary)

    for raw in (fallback_models_csv or "").split(","):
        candidate = raw.strip()
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    return candidates


def _groq_chat_completion(user_message):
    api_key = app.config.get("GROQ_API_KEY", "")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not configured")

    history_text = _history_as_text("user")
    route_text = _assistant_route_context("user")
    capability_text = _platform_capability_context("user")
    dynamic_user_content = (
        f"{_chat_runtime_context()}\n\n"
        f"{capability_text}\n\n"
        f"Known routes:\n{route_text or '- Routes unavailable'}\n\n"
        f"Recent conversation:\n{history_text or 'No previous chat context.'}\n\n"
        f"User request: {user_message}"
    )

    messages = [
        {"role": "system", "content": SKILLSWAP_USER_CHATBOT_SYSTEM_PROMPT},
        {"role": "user", "content": dynamic_user_content},
    ]

    for model_name in _model_candidates(GROQ_MODEL_PRIMARY, GROQ_MODEL_FALLBACKS):
        app.logger.info("Using Groq model=%s", model_name)
        payload = {
            "model": model_name,
            "temperature": 0.25,
            "max_tokens": 500,
            "messages": messages,
        }

        try:
            http_response = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "SkillSwap/1.0",
                },
                json=payload,
                timeout=AI_REQUEST_TIMEOUT_SECONDS,
            )
            raw_body = (http_response.text or "")
            if http_response.status_code >= 400:
                app.logger.warning(
                    "Groq HTTP error. model=%s code=%s body=%s",
                    model_name,
                    http_response.status_code,
                    raw_body[:400],
                )
                continue

            result = http_response.json()
            app.logger.info("Groq response received. model=%s", model_name)
        except Exception as exc:
            app.logger.warning("Groq chat request failed for model %s: %s", model_name, exc)
            continue

        if not result or not isinstance(result, dict) or "choices" not in result:
            app.logger.warning("Groq returned invalid response shape. model=%s", model_name)
            return "Groq API returned invalid response"

        try:
            ai_text = (result["choices"][0]["message"]["content"] or "").strip()
        except Exception as exc:
            app.logger.warning("Groq parse failed for model %s: %s", model_name, exc)
            continue

        if ai_text:
            return ai_text

        app.logger.warning("Groq returned empty text response. model=%s", model_name)
        return "AI is not responding right now. Please try again."

    raise RuntimeError("Groq API failed after trying all configured models")


@app.errorhandler(RequestEntityTooLarge)
def file_too_large(_error):
    flash("File too large. Maximum upload size is 20MB.", "error")
    return redirect(request.referrer or url_for("profile_edit"))


@app.route("/upload_attachment", methods=["POST"])
@login_required
def upload_attachment():
    file_storage = request.files.get("file")
    if not file_storage or not file_storage.filename:
        return jsonify({"error": "No file provided."}), 400

    if not allowed_attachment_file(file_storage.filename):
        return jsonify({"error": "Unsupported file type."}), 400

    safe_name = secure_filename(file_storage.filename)
    if "." not in safe_name:
        return jsonify({"error": "Invalid filename."}), 400

    extension = safe_name.rsplit(".", 1)[1].lower()
    unique_name = f"att_{uuid.uuid4().hex}.{extension}"
    target_path = os.path.join(UPLOAD_FOLDER, unique_name)
    file_storage.save(target_path)

    return jsonify(
        {
            "file_url": url_for("static", filename=f"{UPLOAD_SUBDIR}/{unique_name}"),
            "file_type": attachment_type_for_extension(extension),
            "file_name": safe_name,
            "file_size": os.path.getsize(target_path),
        }
    )


@app.before_request
def load_logged_in_user():
    user_id = session.get("user_id")
    g.user = User.query.get(user_id) if user_id else None
    g.unread_notifications = 0
    g.unread_messages = 0
    g.recent_notifications = []
    if g.user and g.user.is_blocked:
        session.clear()
        session["blocked_login_notice"] = (
            "Your account has been blocked by the administrator. "
            "If you believe this is a mistake, please contact support."
        )
        g.user = None
        return redirect(url_for("login"))

    if g.user and g.user.is_admin:
        user_only_endpoints = {
            "dashboard",
            "profile",
            "profile_edit",
            "skills",
            "remove_skill",
            "matches",
            "requests_page",
            "messages_inbox",
            "messages_thread_legacy",
            "messages_thread",
            "search",
        }
        if request.endpoint in user_only_endpoints:
            return redirect(url_for("admin_dashboard"))

    if g.user:
        now = datetime.utcnow()
        apply_auto_expire_exchange_sessions(now=now)

        current_session = resolve_current_user_session(g.user.user_id)
        if not current_session:
            create_user_session_record(g.user)
            current_session = resolve_current_user_session(g.user.user_id)
            db.session.commit()
        elif not current_session.is_active:
            session.clear()
            g.user = None
            return redirect(url_for("login"))

        # Keep presence/session activity accurate on every request.
        g.user.last_seen_at = now
        if current_session:
            current_session.last_active = now
            current_session.ip_address = request.remote_addr
            current_session.user_agent = (request.user_agent.string or "")[:255]
        db.session.commit()

        g.unread_notifications = Notification.query.filter(
            Notification.user_id == g.user.user_id,
            Notification.is_read.is_(False),
            Notification.notif_type != "message",
        ).count()
        g.recent_notifications = (
            Notification.query.filter(
                Notification.user_id == g.user.user_id,
                Notification.notif_type != "message",
            )
            .order_by(Notification.created_at.desc())
            .limit(6)
            .all()
        )
        g.unread_messages = sum(get_unread_message_counts(g.user.user_id).values())


@app.route("/")
def index():
    if g.user and g.user.is_admin:
        return redirect(url_for("admin_dashboard"))

    top_skills = (
        db.session.query(Skill.skill_name, db.func.count(UserSkillsOffered.id).label("total_users"))
        .join(UserSkillsOffered, UserSkillsOffered.skill_id == Skill.skill_id)
        .filter(or_(Skill.status.is_(None), Skill.status == "active"))
        .group_by(Skill.skill_id, Skill.skill_name)
        .order_by(db.func.count(UserSkillsOffered.id).desc(), Skill.skill_name.asc())
        .limit(10)
        .all()
    )

    top_user_cards, top_users_total = get_ranked_top_user_cards(offset=0, limit=4)

    if g.user:
        start_swapping_url = url_for("skills")
        explore_matches_url = url_for("matches")
    else:
        start_swapping_url = url_for("register")
        explore_matches_url = url_for("login")

    return render_template(
        "user/index.html",
        start_swapping_url=start_swapping_url,
        explore_matches_url=explore_matches_url,
        top_skills=top_skills,
        top_user_cards=top_user_cards,
        top_users_total=top_users_total,
    )


def get_ranked_top_user_cards(offset=0, limit=4):
    offset = max(int(offset or 0), 0)
    limit = max(int(limit or 1), 1)

    ratings_map = {
        int(user_id): float(avg_rating or 0)
        for user_id, avg_rating in db.session.query(
            Rating.to_user,
            db.func.avg(Rating.rating),
        )
        .group_by(Rating.to_user)
        .all()
    }

    completed_map = {}
    for user_id, total in (
        db.session.query(Request.sender_id, db.func.count(Request.request_id))
        .filter(Request.status == "completed")
        .group_by(Request.sender_id)
        .all()
    ):
        completed_map[int(user_id)] = completed_map.get(int(user_id), 0) + int(total or 0)

    for user_id, total in (
        db.session.query(Request.receiver_id, db.func.count(Request.request_id))
        .filter(Request.status == "completed")
        .group_by(Request.receiver_id)
        .all()
    ):
        completed_map[int(user_id)] = completed_map.get(int(user_id), 0) + int(total or 0)

    offered_count_map = {
        int(user_id): int(total or 0)
        for user_id, total in db.session.query(
            UserSkillsOffered.user_id,
            db.func.count(UserSkillsOffered.id),
        )
        .group_by(UserSkillsOffered.user_id)
        .all()
    }

    wanted_count_map = {
        int(user_id): int(total or 0)
        for user_id, total in db.session.query(
            UserSkillsWanted.user_id,
            db.func.count(UserSkillsWanted.id),
        )
        .group_by(UserSkillsWanted.user_id)
        .all()
    }

    skill_names_map = {}
    for user_id, skill_name in (
        db.session.query(UserSkillsOffered.user_id, Skill.skill_name)
        .join(Skill, Skill.skill_id == UserSkillsOffered.skill_id)
        .order_by(UserSkillsOffered.user_id.asc(), Skill.skill_name.asc())
        .all()
    ):
        uid = int(user_id)
        existing = skill_names_map.setdefault(uid, [])
        if skill_name not in existing:
            existing.append(skill_name)

    candidate_users = (
        User.query.filter(User.is_blocked.is_(False), User.role.notin_(["admin", "super_admin"]))
        .order_by(User.user_id.asc())
        .all()
    )

    ranked_cards = []
    for user in candidate_users:
        offered_count = offered_count_map.get(user.user_id, 0)
        wanted_count = wanted_count_map.get(user.user_id, 0)

        # Top users must have at least one skill listed.
        if offered_count + wanted_count <= 0:
            continue

        completion_checks = [
            bool((user.bio or "").strip()),
            bool((user.location or "").strip()),
            bool(user.profile_image and user.profile_image != DEFAULT_PROFILE_IMAGE),
            offered_count > 0,
            wanted_count > 0,
            bool(user.is_verified),
        ]
        profile_completion = round(
            (sum(1 for flag in completion_checks if flag) / len(completion_checks)) * 100,
            1,
        )

        avg_rating = round(ratings_map.get(user.user_id, 0), 2)
        completed_exchanges = completed_map.get(user.user_id, 0)
        score = round(
            (avg_rating * 0.4) + (completed_exchanges * 0.3) + ((profile_completion / 100) * 0.2),
            4,
        )

        # Eligibility score is normalized to 0-100 and keeps ranking focused on
        # profile quality, trust, and exchange activity.
        rating_percent = min(100.0, (avg_rating / 5.0) * 100.0)
        activity_percent = min(100.0, completed_exchanges * 10.0)
        eligibility_score = round(
            (profile_completion * 0.5) + (rating_percent * 0.3) + (activity_percent * 0.2),
            2,
        )

        if eligibility_score < 80.0:
            continue

        ranked_cards.append(
            {
                "user": user,
                "avg_rating": avg_rating,
                "completed_exchanges": completed_exchanges,
                "profile_completion": profile_completion,
                "score": score,
                "eligibility_score": eligibility_score,
                "skills": skill_names_map.get(user.user_id, [])[:10],
            }
        )

    ranked_cards.sort(
        key=lambda card: (
            -card["eligibility_score"],
            -card["score"],
            -card["avg_rating"],
            -card["completed_exchanges"],
            card["user"].user_id,
        )
    )

    # Homepage top users intentionally caps at 10 entries max.
    ranked_cards = ranked_cards[:10]

    total = len(ranked_cards)
    return ranked_cards[offset : offset + limit], total


@app.route("/api/top-users")
def api_top_users():
    try:
        offset = int(request.args.get("offset", 0))
        limit = int(request.args.get("limit", 6))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid pagination parameters."}), 400

    offset = max(offset, 0)
    limit = max(1, min(limit, 10))

    cards, total = get_ranked_top_user_cards(offset=offset, limit=limit)
    is_authenticated = bool(session.get("user_id"))

    return jsonify(
        {
            "users": [
                {
                    "name": card["user"].name,
                    "username": card["user"].username,
                    "avg_rating": card["avg_rating"],
                    "availability_label": card["user"].availability_label,
                    "location": card["user"].location,
                    "completed_exchanges": card["completed_exchanges"],
                    "profile_completion": card["profile_completion"],
                    "score": card["score"],
                    "skills": card["skills"],
                    "profile_image_url": url_for("static", filename=card["user"].profile_image_path),
                    "profile_url": url_for("user_profile", username=card["user"].username),
                }
                for card in cards
            ],
            "has_more": (offset + len(cards)) < total,
            "next_offset": offset + len(cards),
            "is_authenticated": is_authenticated,
        }
    )


@app.route("/api/chat/user", methods=["POST"])
def api_chat_user():
    return api_chat_legacy()


@app.route("/api/chat/reset", methods=["POST"])
def api_chat_reset():
    role = g.user.role if g.user else "guest"
    _clear_chat_history(role)
    return jsonify({"ok": True})


@app.route("/api/chat", methods=["POST"])
def api_chat_legacy():
    payload = {}
    role = g.user.role if g.user else "guest"
    try:
        if _is_chat_rate_limited():
            return (
                jsonify(
                    {
                        "response": "Service is temporarily busy. Please wait a moment and try again.",
                        "action": None,
                    }
                ),
                429,
            )

        payload = request.json or {}
        user_message = (payload.get("message") or "").strip()
        role = g.user.role if g.user else "guest"

        app.logger.info("/api/chat incoming message role=%s message=%s", role, user_message[:250])

        if not user_message:
            return jsonify({"response": "Please enter a message."}), 400

        if len(user_message) > CHAT_MESSAGE_MAX_LENGTH:
            return jsonify({"response": f"Please keep your message under {CHAT_MESSAGE_MAX_LENGTH} characters."}), 400

        action_payload = None
        try:
            response_text = _groq_chat_completion(user_message)
        except Exception:
            app.logger.exception("Assistant Groq failure")
            response_text = "I'm not fully sure about that. Please check the relevant section."

        if not response_text:
            raise RuntimeError("AI returned empty response text")

        _append_chat_history(role, user_message, response_text)

        app.logger.info("/api/chat outgoing response role=%s response=%s", role, response_text[:250])
        return jsonify({"response": response_text, "action": action_payload})
    except Exception as exc:
        app.logger.exception("/api/chat failed: %s", exc)
        response_text = "Something went wrong. Please try again."
        _append_chat_history(role, (payload.get("message") if isinstance(payload, dict) else "") or "", response_text)
        return jsonify({"response": response_text, "action": None}), 500


@app.route("/test-groq", methods=["GET"])
def test_groq():
    if not app.config.get("ENABLE_DEV_ENDPOINTS", False):
        abort(404)

    try:
        response_text = _groq_chat_completion("hello")
        return jsonify({"response": response_text})
    except Exception as exc:
        app.logger.exception("/test-groq failed: %s", exc)
        return jsonify({"response": str(exc)}), 500


@app.route("/register", methods=["GET", "POST"])
def register():
    form_data = {"name": "", "username": "", "email": ""}
    recaptcha_site_key = app.config.get("RECAPTCHA_SITE_KEY", "YOUR_SITE_KEY")
    recaptcha_enabled = app.config.get("RECAPTCHA_ENABLED", False)
    allow_local_recaptcha_bypass = app.config.get("RECAPTCHA_FAIL_OPEN_ON_LOCALHOST", False)
    registrations_open = app.config.get("ALLOW_NEW_REGISTRATIONS", True)

    recaptcha_verified = False
    recaptcha_verified_at_raw = session.get(REGISTER_RECAPTCHA_SESSION_KEY)
    if recaptcha_enabled and recaptcha_verified_at_raw:
        try:
            recaptcha_verified_at = datetime.fromisoformat(recaptcha_verified_at_raw)
            recaptcha_verified = (
                (datetime.utcnow() - recaptcha_verified_at).total_seconds()
                <= REGISTER_RECAPTCHA_TTL_SECONDS
            )
        except ValueError:
            recaptcha_verified = False
        if not recaptcha_verified:
            session.pop(REGISTER_RECAPTCHA_SESSION_KEY, None)

    def render_register_page(notice=None):
        return render_template(
            "user/register.html",
            form_data=form_data,
            recaptcha_site_key=recaptcha_site_key,
            recaptcha_enabled=recaptcha_enabled,
            recaptcha_verified=recaptcha_verified,
            registrations_open=registrations_open,
            registration_notice=notice,
        )

    if request.method == "POST":
        if not registrations_open:
            flash("New registrations are temporarily disabled by admin.", "error")
            return render_register_page(
                "New registrations are temporarily disabled by admin."
            )

        name = request.form.get("name", "").strip()
        username_raw = request.form.get("username", "")
        username_input = (username_raw or "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        recaptcha_response = request.form.get("g-recaptcha-response", "").strip()
        username = normalize_username(username_raw)
        form_data = {"name": name, "username": username_input.lower(), "email": email}

        if not name or not username_input or not email or not password:
            flash("Name, username, email, and password are required.", "error")
            return render_register_page()
        host_name = (request.host or "").split(":", 1)[0].lower()
        is_local_host = host_name in {"localhost", "127.0.0.1", "::1"}
        skip_recaptcha_for_localhost = (
            recaptcha_enabled
            and allow_local_recaptcha_bypass
            and is_local_host
            and not recaptcha_response
        )

        should_verify_recaptcha = (
            recaptcha_enabled
            and not recaptcha_verified
            and not skip_recaptcha_for_localhost
        )

        if should_verify_recaptcha and not recaptcha_response:
            flash("Please complete the captcha.", "error")
            return render_register_page()

        if should_verify_recaptcha:
            recaptcha_ok, recaptcha_error = verify_recaptcha(recaptcha_response, request.remote_addr)
            if not recaptcha_ok:
                flash(recaptcha_error, "error")
                return render_register_page()
            session[REGISTER_RECAPTCHA_SESSION_KEY] = datetime.utcnow().isoformat()
            recaptcha_verified = True
        elif skip_recaptcha_for_localhost:
            session[REGISTER_RECAPTCHA_SESSION_KEY] = datetime.utcnow().isoformat()
            recaptcha_verified = True
            app.logger.warning("Bypassing reCAPTCHA for localhost registration due missing widget response")

        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return render_register_page()
        if not username:
            flash("Invalid username", "error")
            return render_register_page()

        if User.query.filter_by(email=email).first():
            flash("Email already registered.", "error")
            return render_register_page()
        if User.query.filter_by(username=username).first():
            flash("Username already taken", "error")
            return render_register_page()

        otp_code = generate_otp_code()
        otp_expiry = datetime.utcnow() + timedelta(minutes=10)

        session["pending_registration"] = {
            "name": name,
            "username": username,
            "email": email,
            "password_hash": hash_password(password),
            "role": "user",
            "otp_code": otp_code,
            "otp_expiry": otp_expiry.isoformat(),
        }

        try:
            send_otp_email(email, otp_code, name)
        except Exception as exc:
            app.logger.warning("OTP email error during registration: %s", exc)
            app.logger.exception("OTP email send failed")
            session.pop("pending_registration", None)
            flash(
                "Account created, but OTP email could not be delivered. "
                "Please check mail settings.",
                "error",
            )
            return render_register_page()

        flash("Registration successful. Enter the OTP sent to your email.", "success")
        session.pop(REGISTER_RECAPTCHA_SESSION_KEY, None)
        return redirect(url_for("verify_otp"))

    if not registrations_open:
        return render_register_page(
            "New registrations are temporarily disabled by admin."
        )

    return render_register_page()


@app.route("/login", methods=["GET", "POST"])
def login():
    blocked_notice = session.pop("blocked_login_notice", None)
    blocked_until_raw = session.get("login_blocked_until")
    if blocked_until_raw:
        try:
            blocked_until = datetime.fromisoformat(blocked_until_raw)
            if datetime.utcnow() < blocked_until:
                flash("Too many failed attempts. Please try again in a few minutes.", "error")
                return render_template("user/login.html")
            session.pop("login_blocked_until", None)
            session.pop("login_failures", None)
        except ValueError:
            session.pop("login_blocked_until", None)

    if request.method == "POST":
        email_or_username = request.form.get("email_or_username", "").strip()
        password = request.form.get("password", "")

        if not email_or_username or not password:
            flash("Email/username and password are required.", "error")
            return redirect(url_for("login"))

        identifier = email_or_username.lower()
        normalized_username = normalize_username(email_or_username)

        user = User.query.filter(
            or_(
                User.email == identifier,
                User.username == normalized_username,
            )
        ).first()
        if user is None or not verify_password(user.password, password):
            failures = int(session.get("login_failures", 0)) + 1
            session["login_failures"] = failures
            if failures >= 5:
                session["login_blocked_until"] = (datetime.utcnow() + timedelta(minutes=5)).isoformat()
            flash("Invalid email or password.", "error")
            return redirect(url_for("login"))

        if hasattr(user, "is_verified") and not user.is_verified:
            session["pending_registration"] = {
                "name": user.name,
                "username": user.username,
                "email": user.email,
                "password_hash": user.password,
                "role": user.role,
                "existing_user_id": user.user_id,
                "otp_code": generate_otp_code(),
                "otp_expiry": (datetime.utcnow() + timedelta(minutes=10)).isoformat(),
            }
            try:
                send_otp_email(user.email, session["pending_registration"]["otp_code"], user.name)
            except Exception as exc:
                app.logger.warning("OTP email error during login verification: %s", exc)
                app.logger.exception("OTP email send failed")
            flash("Please verify your email first.", "error")
            return redirect(url_for("verify_otp"))

        if user.is_blocked:
            session["blocked_login_notice"] = (
                "Your account has been blocked by the administrator. "
                "If you believe this is a mistake, please contact support."
            )
            return redirect(url_for("login"))

        session.clear()
        session["user_id"] = user.user_id
        create_user_session_record(user)
        db.session.commit()
        flash("Welcome back!", "success")
        if user.is_admin:
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("index"))

    return render_template("user/login.html", blocked_notice=blocked_notice)


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if not email:
            flash("Email is required.", "error")
            return render_template("user/forgot_password.html")

        user = User.query.filter_by(email=email).first()
        if not user:
            flash("No account found for this email.", "error")
            return render_template("user/forgot_password.html")

        user.otp_code = generate_otp_code()
        user.otp_expiry = datetime.utcnow() + timedelta(minutes=10)
        db.session.commit()

        try:
            send_password_reset_otp_email(user.email, user.otp_code, user.name)
        except Exception as exc:
            app.logger.warning("Password reset OTP email error: %s", exc)
            app.logger.exception("Password reset OTP send failed")
            flash("Could not send OTP right now. Please try again.", "error")
            return render_template("user/forgot_password.html")

        session.pop("password_reset_verified_user_id", None)
        session["password_reset_user_id"] = user.user_id
        flash("OTP sent to your email. Please verify to continue.", "success")
        return redirect(url_for("verify_reset_otp"))

    return render_template("user/forgot_password.html")


@app.route("/verify-reset-otp", methods=["GET", "POST"])
def verify_reset_otp():
    reset_user_id = session.get("password_reset_user_id")
    if not reset_user_id:
        flash("Start password reset again.", "error")
        return redirect(url_for("forgot_password"))

    user = User.query.get(reset_user_id)
    if not user:
        session.pop("password_reset_user_id", None)
        session.pop("password_reset_verified_user_id", None)
        flash("Reset session expired. Please try again.", "error")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        entered_otp = (request.form.get("otp") or "").strip().upper()
        now = datetime.utcnow()

        if not entered_otp:
            flash("OTP is required.", "error")
            return render_template("user/verify_reset_otp.html", pending_email=user.email)

        expected_otp = (user.otp_code or "").strip().upper()
        if not expected_otp or not user.otp_expiry or now > user.otp_expiry:
            flash("OTP expired. Please request a new one.", "error")
            return redirect(url_for("forgot_password"))

        if entered_otp != expected_otp:
            flash("Invalid OTP.", "error")
            return render_template("user/verify_reset_otp.html", pending_email=user.email)

        user.otp_code = None
        user.otp_expiry = None
        db.session.commit()

        session["password_reset_verified_user_id"] = user.user_id
        session.pop("password_reset_user_id", None)
        flash("OTP verified. Set your new password.", "success")
        return redirect(url_for("reset_password"))

    return render_template("user/verify_reset_otp.html", pending_email=user.email)


@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    verified_user_id = session.get("password_reset_verified_user_id")
    if not verified_user_id:
        flash("OTP verification required before password reset.", "error")
        return redirect(url_for("forgot_password"))

    user = User.query.get(verified_user_id)
    if not user:
        session.pop("password_reset_verified_user_id", None)
        flash("Reset session expired. Please try again.", "error")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not new_password or not confirm_password:
            flash("Both password fields are required.", "error")
            return render_template("user/reset_password.html")
        if len(new_password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return render_template("user/reset_password.html")
        if new_password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template("user/reset_password.html")

        user.password = hash_password(new_password)
        db.session.commit()

        session.pop("password_reset_verified_user_id", None)
        flash("Password reset successful. Please login.", "success")
        return redirect(url_for("login"))

    return render_template("user/reset_password.html")


@app.route("/verify-otp", methods=["GET", "POST"])
def verify_otp():
    pending = session.get("pending_registration")
    if not pending:
        flash("No pending verification found. Please register or login first.", "error")
        return redirect(url_for("login"))

    pending_email = pending.get("email", "")
    otp_expiry_raw = pending.get("otp_expiry", "")
    try:
        otp_expiry = datetime.fromisoformat(otp_expiry_raw)
    except ValueError:
        session.pop("pending_registration", None)
        flash("OTP session expired. Please register again.", "error")
        return redirect(url_for("register"))

    if request.method == "POST":
        entered_otp = (request.form.get("otp") or "").strip().upper()
        now = datetime.utcnow()
        existing_user_id = pending.get("existing_user_id")

        if now > otp_expiry:
            flash("OTP expired. Please click Resend OTP.", "error")
            return render_template("user/verify_otp.html", pending_email=pending_email)

        expected_otp = (pending.get("otp_code") or "").strip().upper()
        if expected_otp and expected_otp == entered_otp:
            if existing_user_id:
                existing_user = User.query.get(existing_user_id)
                if not existing_user:
                    session.pop("pending_registration", None)
                    flash("Verification session expired. Please register again.", "error")
                    return redirect(url_for("register"))

                existing_user.is_verified = True
                existing_user.otp_code = None
                existing_user.otp_expiry = None
                db.session.commit()
                session.pop("pending_registration", None)
                try:
                    send_welcome_email(existing_user.email, existing_user.name)
                except Exception as exc:
                    app.logger.warning("Welcome email error for existing user: %s", exc)
                    app.logger.exception("Welcome email send failed")
                flash("Email verified successfully. You can now login.", "success")
                return redirect(url_for("login"))

            if User.query.filter_by(email=pending.get("email")).first():
                session.pop("pending_registration", None)
                flash("Email already registered. Please login.", "error")
                return redirect(url_for("login"))
            if User.query.filter_by(username=pending.get("username")).first():
                session.pop("pending_registration", None)
                flash("Username already taken. Please register again.", "error")
                return redirect(url_for("register"))

            new_user = User(
                name=pending.get("name"),
                username=pending.get("username"),
                email=pending.get("email"),
                password=pending.get("password_hash"),
                role="user",
                is_verified=True,
                otp_code=None,
                otp_expiry=None,
            )
            db.session.add(new_user)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                session.pop("pending_registration", None)
                flash("Could not complete verification. Please register again.", "error")
                return redirect(url_for("register"))

            session.pop("pending_registration", None)
            try:
                send_welcome_email(new_user.email, new_user.name)
            except Exception as exc:
                app.logger.warning("Welcome email error for new user: %s", exc)
                app.logger.exception("Welcome email send failed")
            flash("Email verified successfully. You can now login.", "success")
            return redirect(url_for("login"))

        flash("Invalid or expired OTP. Please try again.", "error")

    return render_template("user/verify_otp.html", pending_email=pending_email)


@app.route("/resend-otp", methods=["POST"])
def resend_otp():
    pending = session.get("pending_registration")
    if not pending:
        flash("No pending verification found. Please register or login first.", "error")
        return redirect(url_for("login"))

    pending["otp_code"] = generate_otp_code()
    pending["otp_expiry"] = (datetime.utcnow() + timedelta(minutes=10)).isoformat()
    session["pending_registration"] = pending

    try:
        send_otp_email(
            pending.get("email", ""),
            pending["otp_code"],
            pending.get("name") or pending.get("username") or "User",
        )
    except Exception as exc:
        app.logger.warning("Resend OTP email error: %s", exc)
        app.logger.exception("Resend OTP email failed")
        flash("Could not resend OTP right now. Please try again.", "error")
        return redirect(url_for("verify_otp"))

    flash("A new OTP has been sent to your email.", "success")
    return redirect(url_for("verify_otp"))


@app.route("/logout")
def logout():
    user_id = session.get("user_id")
    active_session = resolve_current_user_session(user_id) if user_id else None
    if active_session and active_session.is_active:
        active_session.is_active = False
        active_session.last_active = datetime.utcnow()
        db.session.commit()
    session.clear()
    flash("Logged out successfully.", "success")
    return redirect(url_for("index"))


@app.route("/update-activity", methods=["POST"])
@login_required
def update_activity():
    now = datetime.utcnow()
    current_session = resolve_current_user_session(g.user.user_id)
    if not current_session:
        create_user_session_record(g.user)
        current_session = resolve_current_user_session(g.user.user_id)

    if current_session:
        current_session.is_active = True
        current_session.last_active = now
        current_session.ip_address = request.remote_addr
        current_session.user_agent = (request.user_agent.string or "")[:255]
    g.user.last_seen_at = now
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/dashboard")
@login_required
def dashboard():
    incoming_pending = Request.query.filter_by(
        receiver_id=g.user.user_id, status="pending"
    ).count()
    outgoing_pending = Request.query.filter_by(
        sender_id=g.user.user_id, status="pending"
    ).count()
    completed_count = Request.query.filter(
        Request.status == "completed",
        ((Request.sender_id == g.user.user_id) | (Request.receiver_id == g.user.user_id)),
    ).count()
    recent_feedback = (
        db.session.query(Rating, User.name)
        .join(User, Rating.from_user == User.user_id)
        .filter(Rating.to_user == g.user.user_id)
        .order_by(Rating.created_at.desc())
        .limit(20)
        .all()
    )

    def humanize_time_ago(dt):
        if not dt:
            return "just now"
        now_dt = datetime.utcnow()
        seconds = int((now_dt - dt).total_seconds())
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            mins = seconds // 60
            return f"{mins} minute{'s' if mins != 1 else ''} ago"
        if seconds < 86400:
            hours = seconds // 3600
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        days = seconds // 86400
        return f"{days} day{'s' if days != 1 else ''} ago"

    feedback_items = [
        {
            "from_name": from_name,
            "rating": rating,
            "time_ago": humanize_time_ago(rating.created_at),
        }
        for rating, from_name in recent_feedback
    ]
    feedback_preview = feedback_items[:3]
    feedback_has_more = len(feedback_items) > 3

    now = datetime.utcnow().date()
    trend_data = []
    max_daily = 0
    for i in range(6, -1, -1):
        day = now - timedelta(days=i)
        next_day = day + timedelta(days=1)
        requests_count = Request.query.filter(
            Request.created_at >= datetime.combine(day, datetime.min.time()),
            Request.created_at < datetime.combine(next_day, datetime.min.time()),
            ((Request.sender_id == g.user.user_id) | (Request.receiver_id == g.user.user_id)),
        ).count()
        completed_daily = Request.query.filter(
            Request.status == "completed",
            Request.updated_at >= datetime.combine(day, datetime.min.time()),
            Request.updated_at < datetime.combine(next_day, datetime.min.time()),
            ((Request.sender_id == g.user.user_id) | (Request.receiver_id == g.user.user_id)),
        ).count()
        rejected_daily = Request.query.filter(
            Request.status == "rejected",
            Request.updated_at >= datetime.combine(day, datetime.min.time()),
            Request.updated_at < datetime.combine(next_day, datetime.min.time()),
            ((Request.sender_id == g.user.user_id) | (Request.receiver_id == g.user.user_id)),
        ).count()
        max_daily = max(max_daily, requests_count, completed_daily, rejected_daily)
        trend_data.append(
            {
                "label": day.strftime("%a"),
                "requests": requests_count,
                "completed": completed_daily,
                "rejected": rejected_daily,
            }
        )

    for row in trend_data:
        row["request_pct"] = int(round((row["requests"] / max_daily) * 100)) if max_daily else 0
        row["completed_pct"] = int(round((row["completed"] / max_daily) * 100)) if max_daily else 0
        row["rejected_pct"] = int(round((row["rejected"] / max_daily) * 100)) if max_daily else 0

    involved_requests = Request.query.filter(
        (Request.sender_id == g.user.user_id) | (Request.receiver_id == g.user.user_id)
    ).all()
    skill_use_count = {}
    for req in involved_requests:
        skill_use_count[req.offered_skill_id] = skill_use_count.get(req.offered_skill_id, 0) + 1
        skill_use_count[req.requested_skill_id] = skill_use_count.get(req.requested_skill_id, 0) + 1
    most_used_skill = None
    if skill_use_count:
        top_skill_id = max(skill_use_count.items(), key=lambda x: x[1])[0]
        top_skill = Skill.query.get(top_skill_id)
        most_used_skill = top_skill.skill_name if top_skill else None

    weekly_summary = {
        "requests": sum(row["requests"] for row in trend_data),
        "completed": sum(row["completed"] for row in trend_data),
        "rejected": sum(row["rejected"] for row in trend_data),
    }

    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    recent_activity = (
        Request.query.filter(
            (Request.sender_id == g.user.user_id) | (Request.receiver_id == g.user.user_id),
            Request.updated_at >= thirty_days_ago,
        )
        .order_by(Request.updated_at.desc())
        .limit(6)
        .all()
    )

    quick_actions = [
        {"label": "Add Skills", "url": url_for("skills")},
        {"label": "Find Matches", "url": url_for("matches")},
        {"label": "Search Users", "url": url_for("search")},
        {"label": "View Requests", "url": url_for("requests_page")},
    ]

    return render_template(
        "user/dashboard.html",
        incoming_pending=incoming_pending,
        outgoing_pending=outgoing_pending,
        completed_count=completed_count,
        avg_rating=g.user.average_rating(),
        feedback_preview=feedback_preview,
        feedback_all=feedback_items,
        feedback_has_more=feedback_has_more,
        trend_data=trend_data,
        recent_activity=recent_activity,
        quick_actions=quick_actions,
        most_used_skill=most_used_skill,
        weekly_summary=weekly_summary,
        availability_statuses=AVAILABILITY_STATUSES,
    )


@app.route("/availability", methods=["POST"])
@login_required
def toggle_availability():
    selected_status = (request.form.get("availability_status") or "").strip()
    if selected_status not in AVAILABILITY_STATUSES:
        flash("Invalid availability status.", "error")
        return redirect(url_for("dashboard"))

    g.user.availability_status = selected_status
    g.user.availability = selected_status == "Available"
    db.session.commit()
    flash("Availability updated.", "success")
    return redirect(url_for("dashboard"))


@app.route("/skills", methods=["GET", "POST"])
@login_required
def skills():
    skills_notice = session.pop("skills_notice", None)
    if request.method == "POST":
        skill_name = normalize_skill_name(request.form.get("skill_name", ""))
        category_choice = request.form.get("category", "").strip()
        custom_category = normalize_custom_category(request.form.get("custom_category", ""))
        skill_level = request.form.get("skill_level", "Intermediate").strip()
        skill_type = request.form.get("skill_type", "").strip()

        if not skill_name or skill_type not in {"offered", "wanted"}:
            flash("Provide a valid skill and list type.", "error")
            return redirect(url_for("skills"))
        if not is_valid_skill_name(skill_name):
            flash("Skill name contains unsupported characters.", "error")
            return redirect(url_for("skills"))
        if not category_choice:
            flash("Please select a category", "error")
            return redirect(url_for("skills"))
        category_options = get_skill_category_options()
        if category_choice not in category_options and category_choice != "Other":
            flash("Please choose a valid category.", "error")
            return redirect(url_for("skills"))
        if category_choice == "Other":
            if not custom_category:
                flash("Please enter a custom category", "error")
                return redirect(url_for("skills"))
            category = custom_category
        else:
            category = category_choice
        if len(category) > 80:
            flash("Custom category is too long.", "error")
            return redirect(url_for("skills"))
        if skill_level not in SKILL_LEVELS:
            flash("Please choose a valid skill level.", "error")
            return redirect(url_for("skills"))

        if skill_type == "offered":
            list_count = UserSkillsOffered.query.filter_by(user_id=g.user.user_id).count()
        else:
            list_count = UserSkillsWanted.query.filter_by(user_id=g.user.user_id).count()
        if list_count >= MAX_SKILLS_PER_LIST:
            flash("Maximum limit reached (10 skills allowed)", "error")
            return redirect(url_for("skills"))

        category_row = get_or_create_category_by_name(category)
        if category_row is None:
            flash("Invalid category.", "error")
            return redirect(url_for("skills"))

        skill = Skill.query.filter(db.func.lower(Skill.skill_name) == skill_name.lower()).first()
        if skill is None:
            skill = Skill(
                skill_name=skill_name,
                category=category_row.name,
                category_id=category_row.category_id,
                status="active",
            )
            db.session.add(skill)
            db.session.flush()
        else:
            if (skill.status or "active").lower() == "blocked":
                session["skills_notice"] = "This skill is currently restricted by the administrator."
                return redirect(url_for("skills"))
            if not skill.category_id:
                skill.category_id = category_row.category_id
            if not skill.category:
                skill.category = category_row.name

        if skill_type == "offered":
            existing = UserSkillsOffered.query.filter_by(
                user_id=g.user.user_id, skill_id=skill.skill_id
            ).first()
            if existing:
                flash("Skill already exists", "error")
                db.session.rollback()
                return redirect(url_for("skills"))
            db.session.add(
                UserSkillsOffered(
                    user_id=g.user.user_id,
                    skill_id=skill.skill_id,
                    level=skill_level,
                )
            )
        else:
            existing = UserSkillsWanted.query.filter_by(
                user_id=g.user.user_id, skill_id=skill.skill_id
            ).first()
            if existing:
                flash("Skill already exists", "error")
                db.session.rollback()
                return redirect(url_for("skills"))
            db.session.add(
                UserSkillsWanted(
                    user_id=g.user.user_id,
                    skill_id=skill.skill_id,
                    level=skill_level,
                )
            )

        sync_user_skill_mapping(g.user.user_id, skill.skill_id)

        db.session.commit()
        flash("Skill saved.", "success")
        return redirect(url_for("skills"))

    offered_details, wanted_details = build_skill_details(g.user.user_id)
    category_options = get_skill_category_options()
    if "Other" not in category_options:
        category_options.append("Other")
    return render_template(
        "user/skills.html",
        offered_details=offered_details,
        wanted_details=wanted_details,
        skill_categories=category_options,
        skill_levels=SKILL_LEVELS,
        skills_notice=skills_notice,
    )


@app.route("/skills/remove/<skill_type>/<int:skill_id>", methods=["POST"])
@login_required
def remove_skill(skill_type, skill_id):
    skill = Skill.query.get_or_404(skill_id)

    if skill_type == "offered":
        deleted = UserSkillsOffered.query.filter_by(
            user_id=g.user.user_id, skill_id=skill.skill_id
        ).delete(synchronize_session=False)
    elif skill_type == "wanted":
        deleted = UserSkillsWanted.query.filter_by(
            user_id=g.user.user_id, skill_id=skill.skill_id
        ).delete(synchronize_session=False)
    else:
        deleted = 0

    if not deleted:
        flash("Skill not found in the selected list.", "error")
        return redirect(url_for("skills"))

    sync_user_skill_mapping(g.user.user_id, skill.skill_id)
    db.session.commit()
    flash("Skill removed.", "success")
    return redirect(url_for("skills"))


@app.route("/matches")
@login_required
def matches():
    me = g.user
    blocked_related_ids = get_blocked_related_user_ids(me.user_id)
    available_only = request.args.get("available_only", "0") == "1"
    skill_filter = request.args.get("skill", "").strip().lower()
    category_filter = request.args.get("category", "").strip()
    level_filter = request.args.get("level", "").strip()
    sort_by = request.args.get("sort", "best").strip().lower()

    skill_map = {s.skill_id: s for s in Skill.query.all()}

    my_offered = {s.skill_id for s in me.offered_skills}
    my_wanted = {s.skill_id for s in me.wanted_skills}
    my_offered_levels = {
        row.skill_id: row.level
        for row in UserSkillsOffered.query.filter_by(user_id=me.user_id).all()
    }
    my_offered_skill_names = {skill.skill_id: skill.skill_name for skill in me.offered_skills}
    my_wanted_levels = {
        row.skill_id: row.level
        for row in UserSkillsWanted.query.filter_by(user_id=me.user_id).all()
    }

    if not my_wanted and not my_offered:
        return render_template(
            "user/matches.html",
            matches=[],
            skill_map=skill_map,
            available_only=available_only,
            skill_filter=skill_filter,
            category_filter=category_filter,
            level_filter=level_filter,
            sort_by=sort_by,
            skill_categories=SKILL_CATEGORIES,
            skill_levels=SKILL_LEVELS,
        )

    candidate_users = User.query.filter(
        User.is_blocked.is_(False),
        User.user_id != me.user_id,
    )
    if blocked_related_ids:
        candidate_users = candidate_users.filter(~User.user_id.in_(blocked_related_ids))
    if available_only:
        candidate_users = candidate_users.filter(User.availability.is_(True))
    candidate_users = candidate_users.all()

    candidate_ids = [u.user_id for u in candidate_users]
    offered_rows = (
        UserSkillsOffered.query.filter(UserSkillsOffered.user_id.in_(candidate_ids)).all()
        if candidate_ids
        else []
    )
    wanted_rows = (
        UserSkillsWanted.query.filter(UserSkillsWanted.user_id.in_(candidate_ids)).all()
        if candidate_ids
        else []
    )
    offered_levels_by_user = {}
    wanted_levels_by_user = {}
    for row in offered_rows:
        offered_levels_by_user.setdefault(row.user_id, {})[row.skill_id] = row.level
    for row in wanted_rows:
        wanted_levels_by_user.setdefault(row.user_id, {})[row.skill_id] = row.level

    accepted_statuses = ["accepted"]
    accepted_rows = (
        Request.query.filter(
            Request.status.in_(accepted_statuses),
            (
                ((Request.sender_id == me.user_id) & (Request.receiver_id.in_(candidate_ids)))
                | ((Request.receiver_id == me.user_id) & (Request.sender_id.in_(candidate_ids)))
            ),
        ).all()
        if candidate_ids
        else []
    )
    chat_enabled_user_ids = set()
    for req in accepted_rows:
        if req.sender_id == me.user_id:
            chat_enabled_user_ids.add(req.receiver_id)
        elif req.receiver_id == me.user_id:
            chat_enabled_user_ids.add(req.sender_id)

    def _skill_name_sort_key(skill_id):
        skill_obj = skill_map.get(skill_id)
        return _category_sort_key(skill_obj.skill_name if skill_obj else "")

    results = []
    for other in candidate_users:
        other_offered_levels = offered_levels_by_user.get(other.user_id, {})
        other_wanted_levels = wanted_levels_by_user.get(other.user_id, {})
        other_offered = {s.skill_id for s in other.offered_skills}
        other_wanted = {s.skill_id for s in other.wanted_skills}

        they_can_teach_all = my_wanted.intersection(other_offered)
        i_can_teach = my_offered.intersection(other_wanted)

        filtered_requested = []
        for sid in they_can_teach_all:
            skill_obj = skill_map.get(sid)
            if skill_obj is None:
                continue
            if (skill_obj.status or "active").lower() != "active":
                continue
            if skill_filter and skill_filter not in skill_obj.skill_name.lower():
                continue
            if category_filter and (skill_obj.category or "") != category_filter:
                continue
            if level_filter and other_offered_levels.get(sid) != level_filter:
                continue
            filtered_requested.append(sid)

        requested_skill_ids = sorted(filtered_requested, key=_skill_name_sort_key)
        filtered_offered = [
            sid
            for sid in i_can_teach
            if skill_map.get(sid) is not None
            and (skill_map[sid].status or "active").lower() == "active"
        ]
        offered_skill_ids = sorted(filtered_offered, key=_skill_name_sort_key)

        # A match card is valid only when both directions have at least one skill.
        if not requested_skill_ids or not offered_skill_ids:
            continue

        # Build complete valid-pair set and expose it to the UI.
        pair_level_points = {}
        valid_requested_by_offered = {}
        valid_offered_by_requested = {}
        max_pair_points = 0

        for offered_id in offered_skill_ids:
            offered_key = str(offered_id)
            pair_level_points[offered_key] = {}
            valid_requested_by_offered[offered_key] = []

            for requested_id in requested_skill_ids:
                requested_key = str(requested_id)
                get_points = level_compatibility_points(
                    other_offered_levels.get(requested_id),
                    my_wanted_levels.get(requested_id),
                )
                give_points = level_compatibility_points(
                    my_offered_levels.get(offered_id),
                    other_wanted_levels.get(offered_id),
                )
                pair_points = int(round((get_points + give_points) / 2))

                pair_level_points[offered_key][requested_key] = pair_points
                valid_requested_by_offered[offered_key].append(requested_id)
                valid_offered_by_requested.setdefault(requested_key, []).append(offered_id)
                if pair_points > max_pair_points:
                    max_pair_points = pair_points

        # Default is alphabetical (letters first; numbers/symbols after).
        default_offered_id = offered_skill_ids[0]
        default_requested_id = requested_skill_ids[0]

        selected_level_points = pair_level_points[str(default_offered_id)][str(default_requested_id)]
        level_component = max(0, selected_level_points)
        skill_component = 70
        has_mutual_exchange = True
        match_type = "Full Match"
        trust = compute_user_trust_metrics(other)
        avg_rating = other.average_rating()

        # Displayed strength follows the currently selected pair.
        strength = max(0, min(100, int(round(skill_component + level_component))))
        best_strength = max(0, min(100, int(round(skill_component + max_pair_points))))

        results.append(
            {
                "user": other,
                "requested_skill_ids": requested_skill_ids,
                "offered_skill_ids": offered_skill_ids,
                "default_requested_skill_id": default_requested_id,
                "default_offered_skill_id": default_offered_id,
                "pair_level_points": pair_level_points,
                "valid_requested_by_offered": valid_requested_by_offered,
                "valid_offered_by_requested": valid_offered_by_requested,
                "is_full_match": has_mutual_exchange,
                "strength": strength,
                "score": best_strength,
                "created_at": other.created_at,
                "match_type": match_type,
                "avg_rating": avg_rating,
                "availability_label": other.availability_label,
                "skill_component": skill_component,
                "response_rate": trust["response_rate"],
                "completion_rate": trust["completion_rate"],
                "show_completion_rate": trust["accepted_requests"] > 0,
                "badges": trust["badges"],
                "chat_enabled": other.user_id in chat_enabled_user_ids,
            }
        )

    if sort_by == "latest":
        results.sort(key=lambda x: x["created_at"], reverse=True)
    elif sort_by == "rating":
        results.sort(key=lambda x: (x["avg_rating"] or 0, x["strength"]), reverse=True)
    elif sort_by == "availability":
        availability_rank = {"Available": 3, "Busy": 2, "Unavailable": 1}
        results.sort(
            key=lambda x: (availability_rank.get(x["availability_label"], 0), x["strength"]),
            reverse=True,
        )
    else:
        results.sort(
            key=lambda x: (x["is_full_match"], x["strength"], x["score"]),
            reverse=True,
        )

    return render_template(
        "user/matches.html",
        matches=results,
        skill_map=skill_map,
        available_only=available_only,
        skill_filter=skill_filter,
        category_filter=category_filter,
        level_filter=level_filter,
        sort_by=sort_by,
        skill_categories=SKILL_CATEGORIES,
        skill_levels=SKILL_LEVELS,
    )


def _create_exchange_request(receiver_id, offered_skill_id, requested_skill_id):
    if not receiver_id or not offered_skill_id or not requested_skill_id:
        flash("All request fields are required.", "error")
        return False

    receiver = User.query.get(receiver_id)
    if receiver is None:
        flash("Selected user does not exist.", "error")
        return False
    if receiver.is_blocked:
        flash("You cannot send requests to blocked users.", "error")
        return False
    if is_user_blocked_between(g.user.user_id, receiver.user_id):
        flash("You cannot send requests because one of you has blocked the other user.", "error")
        return False

    sender_offered_ids = {s.skill_id for s in g.user.offered_skills}
    receiver_offered_ids = {s.skill_id for s in receiver.offered_skills}

    if offered_skill_id not in sender_offered_ids:
        flash("Choose one of your offered skills.", "error")
        return False

    if requested_skill_id not in receiver_offered_ids:
        flash("Requested skill is not offered by selected user.", "error")
        return False

    involved_skills = {
        row.skill_id: (row.status or "active").lower()
        for row in Skill.query.filter(Skill.skill_id.in_([offered_skill_id, requested_skill_id])).all()
    }
    if involved_skills.get(offered_skill_id) != "active" or involved_skills.get(requested_skill_id) != "active":
        flash("Blocked skills cannot be used for exchange requests.", "error")
        return False

    duplicate_pending = Request.query.filter_by(
        sender_id=g.user.user_id,
        receiver_id=receiver_id,
        offered_skill_id=offered_skill_id,
        requested_skill_id=requested_skill_id,
        status="pending",
    ).first()

    if duplicate_pending:
        flash("A similar pending request already exists.", "error")
        return False

    active_exchange = Request.query.filter(
        ((Request.sender_id == g.user.user_id) & (Request.receiver_id == receiver_id))
        | ((Request.sender_id == receiver_id) & (Request.receiver_id == g.user.user_id)),
        Request.status.in_(["pending", "countered", "accepted", "awaiting_confirmation"]),
    ).first()
    if active_exchange:
        flash("An active exchange already exists between these users.", "error")
        return False

    new_request = Request(
        sender_id=g.user.user_id,
        receiver_id=receiver_id,
        offered_skill_id=offered_skill_id,
        requested_skill_id=requested_skill_id,
        final_offered_skill_id=None,
        final_requested_skill_id=None,
        status="pending",
    )
    db.session.add(new_request)
    create_notification(
        receiver_id,
        f"New exchange request from {g.user.name}.",
        "request",
        url_for("requests_page"),
    )
    db.session.commit()
    flash("Request sent successfully.", "success")
    return True


@app.route("/requests/send", methods=["POST"])
@login_required
def send_request():
    receiver_id = request.form.get("receiver_id", type=int)
    offered_skill_id = request.form.get("offered_skill_id", type=int)
    requested_skill_id = request.form.get("requested_skill_id", type=int)

    if _create_exchange_request(receiver_id, offered_skill_id, requested_skill_id):
        return redirect(url_for("requests_page"))
    return redirect(url_for("matches"))


@app.route("/send_request/<int:user_id>", methods=["POST"])
@login_required
def send_request_to_user(user_id):
    offered_skill_id = request.form.get("offered_skill_id", type=int)
    requested_skill_id = request.form.get("requested_skill_id", type=int)

    if _create_exchange_request(user_id, offered_skill_id, requested_skill_id):
        return redirect(url_for("requests_page"))
    return redirect(url_for("matches"))


@app.route("/accept_request/<int:request_id>", methods=["POST"])
@login_required
def accept_request(request_id):
    req = Request.query.get_or_404(request_id)
    if req.receiver_id != g.user.user_id or req.status != "pending":
        flash("You cannot accept this request.", "error")
        return redirect(url_for("requests_page"))

    req.status = "accepted"
    req.final_offered_skill_id = req.offered_skill_id
    req.final_requested_skill_id = req.requested_skill_id
    req.is_completed_by_sender = False
    req.is_completed_by_receiver = False
    req.sender_confirmed = False
    req.receiver_confirmed = False
    ensure_exchange_session_record(req)
    create_notification(
        req.sender_id,
        f"Your request to {g.user.name} was accepted.",
        "request",
        url_for("requests_page"),
    )
    db.session.commit()
    flash("Request accepted.", "success")
    return redirect(url_for("requests_page"))


@app.route("/reject_request/<int:request_id>", methods=["POST"])
@login_required
def reject_request(request_id):
    req = Request.query.get_or_404(request_id)
    if req.receiver_id != g.user.user_id or req.status != "pending":
        flash("You cannot reject this request.", "error")
        return redirect(url_for("requests_page"))

    req.status = "rejected"
    req.is_completed_by_sender = False
    req.is_completed_by_receiver = False
    req.sender_confirmed = False
    req.receiver_confirmed = False
    create_notification(
        req.sender_id,
        f"Your request to {g.user.name} was rejected.",
        "request",
        url_for("requests_page"),
    )
    db.session.commit()
    flash("Request rejected.", "success")
    return redirect(url_for("requests_page"))


@app.route("/requests")
@login_required
def requests_page():
    blocked_related_ids = get_blocked_related_user_ids(g.user.user_id)

    incoming = Request.query.filter_by(receiver_id=g.user.user_id).order_by(
        Request.created_at.desc()
    )
    outgoing = Request.query.filter_by(sender_id=g.user.user_id).order_by(
        Request.created_at.desc()
    )

    if blocked_related_ids:
        incoming = incoming.filter(~Request.sender_id.in_(blocked_related_ids))
        outgoing = outgoing.filter(~Request.receiver_id.in_(blocked_related_ids))

    active = Request.query.filter(
        Request.status.in_(["accepted", "awaiting_confirmation", "completed", "terminated"]),
        ((Request.sender_id == g.user.user_id) | (Request.receiver_id == g.user.user_id)),
    ).order_by(Request.updated_at.desc())
    if blocked_related_ids:
        active = active.filter(
            ~((Request.sender_id == g.user.user_id) & (Request.receiver_id.in_(blocked_related_ids))),
            ~((Request.receiver_id == g.user.user_id) & (Request.sender_id.in_(blocked_related_ids))),
        )

    all_request_ids = [req.request_id for req in incoming] + [req.request_id for req in outgoing]
    my_ratings = {}
    if all_request_ids:
        rows = Rating.query.filter(
            Rating.from_user == g.user.user_id,
            Rating.exchange_request_id.in_(all_request_ids),
        ).all()
        my_ratings = {row.exchange_request_id: row.rating for row in rows}

    return render_template(
        "user/requests.html",
        incoming=incoming,
        outgoing=outgoing,
        active=active,
        my_ratings=my_ratings,
        report_reasons=REPORT_REASONS,
    )


@app.route("/user/id/<int:user_id>")
@login_required
def user_profile_legacy(user_id):
    profile_user = User.query.get_or_404(user_id)
    return redirect(url_for("user_profile", username=profile_user.username))


@app.route("/user/<string:username>")
@login_required
def user_profile(username):
    profile_user = User.query.filter_by(username=username).first_or_404()
    if profile_user.is_blocked and not g.user.is_admin and g.user.user_id != profile_user.user_id:
        abort(404)

    offered_details, wanted_details = build_skill_details(profile_user.user_id)
    feedback = (
        db.session.query(Rating, User.name)
        .join(User, Rating.from_user == User.user_id)
        .filter(Rating.to_user == profile_user.user_id)
        .order_by(Rating.created_at.desc())
        .all()
    )

    total_reviews = Rating.query.filter_by(to_user=profile_user.user_id).count()
    completed_exchanges = Request.query.filter(
        Request.status == "completed",
        ((Request.sender_id == profile_user.user_id) | (Request.receiver_id == profile_user.user_id)),
    ).count()

    accepted_requests = Request.query.filter(
        ((Request.sender_id == profile_user.user_id) | (Request.receiver_id == profile_user.user_id)),
        Request.status.in_(["accepted", "awaiting_confirmation", "completed"]),
    ).count()
    rejected_requests = Request.query.filter(
        ((Request.sender_id == profile_user.user_id) | (Request.receiver_id == profile_user.user_id)),
        Request.status == "rejected",
    ).count()
    pending_requests = Request.query.filter(
        (Request.sender_id == profile_user.user_id) | (Request.receiver_id == profile_user.user_id)
    ).filter(Request.status.in_(["pending", "countered"])).count()
    total_requests = accepted_requests + rejected_requests + pending_requests

    total_received_requests = Request.query.filter(
        Request.receiver_id == profile_user.user_id,
    ).count()
    responded_received_requests = Request.query.filter(
        Request.receiver_id == profile_user.user_id,
        Request.status.in_(["accepted", "awaiting_confirmation", "completed", "rejected"]),
    ).count()
    response_rate = (
        int(round((responded_received_requests / total_received_requests) * 100))
        if total_received_requests
        else None
    )

    completion_rate = (
        int(round((completed_exchanges / accepted_requests) * 100)) if accepted_requests else None
    )

    profile_strength_checks = [
        bool(profile_user.profile_image and profile_user.profile_image != DEFAULT_PROFILE_IMAGE),
        bool((profile_user.bio or "").strip()),
        bool((profile_user.location or "").strip()),
        len(offered_details) >= 1,
        len(wanted_details) >= 1,
    ]
    profile_completion = int(round((sum(1 for ok in profile_strength_checks if ok) / 5) * 100))

    now_utc = datetime.utcnow()

    def humanize_time_ago(dt):
        if not dt:
            return "just now"
        seconds = int((now_utc - dt).total_seconds())
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            minutes = seconds // 60
            return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
        if seconds < 86400:
            hours = seconds // 3600
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        days = seconds // 86400
        return f"{days} day{'s' if days != 1 else ''} ago"

    thirty_days_ago = now_utc - timedelta(days=30)
    recent_exchange_requests = (
        Request.query.filter(
            ((Request.sender_id == profile_user.user_id) | (Request.receiver_id == profile_user.user_id)),
            Request.status == "completed",
            Request.updated_at >= thirty_days_ago,
        )
        .order_by(Request.updated_at.desc())
        .limit(12)
        .all()
    )
    recent_exchange_rows = []
    for req in recent_exchange_requests:
        partner = req.receiver if req.sender_id == profile_user.user_id else req.sender
        sender_offered = req.final_offered_skill or req.offered_skill
        sender_requested = req.final_requested_skill or req.requested_skill
        if req.sender_id == profile_user.user_id:
            my_skill = sender_offered.skill_name if sender_offered else "Skill"
            partner_skill = sender_requested.skill_name if sender_requested else "Skill"
        else:
            my_skill = sender_requested.skill_name if sender_requested else "Skill"
            partner_skill = sender_offered.skill_name if sender_offered else "Skill"
        recent_exchange_rows.append(
            {
                "partner_username": partner.username if partner else "user",
                "my_skill": my_skill,
                "partner_skill": partner_skill,
                "status": req.status,
                "status_label": req.status.replace("_", " ").title(),
                "updated_at": req.updated_at,
                "time_ago": humanize_time_ago(req.updated_at),
            }
        )


    trust_metrics = compute_user_trust_metrics(profile_user)
    extra_badges = list(trust_metrics["badges"])
    if total_requests >= 3:
        extra_badges.append("Active User")
    if (profile_user.average_rating() or 0) >= 4.5 and total_reviews >= 3:
        extra_badges.append("Top Rated")
    trust_metrics["badges"] = sorted(set(extra_badges))

    my_offered = (
        db.session.query(UserSkillsOffered, Skill)
        .join(Skill, UserSkillsOffered.skill_id == Skill.skill_id)
        .filter(UserSkillsOffered.user_id == g.user.user_id)
        .order_by(Skill.skill_name.asc())
        .all()
    )
    their_offered = (
        db.session.query(UserSkillsOffered, Skill)
        .join(Skill, UserSkillsOffered.skill_id == Skill.skill_id)
        .filter(UserSkillsOffered.user_id == profile_user.user_id)
        .order_by(Skill.skill_name.asc())
        .all()
    )

    blocked_by_me = False
    blocked_me = False
    messaging_blocked = False
    if g.user.user_id != profile_user.user_id:
        blocked_by_me = has_user_blocked(g.user.user_id, profile_user.user_id)
        blocked_me = has_user_blocked(profile_user.user_id, g.user.user_id)
        messaging_blocked = blocked_by_me or blocked_me

    return render_template(
        "user/user_profile.html",
        profile_user=profile_user,
        offered_details=offered_details,
        wanted_details=wanted_details,
        feedback=feedback,
        avg_rating=profile_user.average_rating(),
        total_reviews=total_reviews,
        completed_exchanges=completed_exchanges,
        total_requests=total_requests,
        accepted_requests=accepted_requests,
        rejected_requests=rejected_requests,
        pending_requests=pending_requests,
        response_rate=response_rate,
        completion_rate=completion_rate,
        recent_exchange_rows=recent_exchange_rows,
        profile_completion=profile_completion,
        trust_metrics=trust_metrics,
        show_email=(
            g.user.is_admin
            or g.user.user_id == profile_user.user_id
            or bool(profile_user.show_email_on_profile)
        ),
        my_offered=my_offered,
        their_offered=their_offered,
        blocked_by_me=blocked_by_me,
        blocked_me=blocked_me,
        messaging_blocked=messaging_blocked,
        report_reasons=REPORT_REASONS,
    )


@app.route("/user/<string:username>/toggle-block", methods=["POST"])
@login_required
def toggle_user_block(username):
    target_user = User.query.filter_by(username=username).first_or_404()
    next_url = request.form.get("next") or request.referrer or url_for("user_profile", username=username)

    if target_user.user_id == g.user.user_id:
        flash("You cannot block yourself.", "error")
        return redirect(next_url)

    if target_user.is_admin:
        flash("You cannot block admin accounts.", "error")
        return redirect(next_url)

    existing = BlockedUser.query.filter_by(
        blocker_id=g.user.user_id,
        blocked_id=target_user.user_id,
    ).first()

    if existing:
        db.session.delete(existing)
        db.session.commit()
        flash("User unblocked.", "success")
        return redirect(next_url)

    db.session.add(
        BlockedUser(
            blocker_id=g.user.user_id,
            blocked_id=target_user.user_id,
        )
    )

    impacted_requests = Request.query.filter(
        Request.status.in_(["pending", "countered", "accepted", "awaiting_confirmation"]),
        (
            ((Request.sender_id == g.user.user_id) & (Request.receiver_id == target_user.user_id))
            | ((Request.sender_id == target_user.user_id) & (Request.receiver_id == g.user.user_id))
        ),
    ).all()

    for req in impacted_requests:
        req.status = "rejected"
        req.is_completed_by_sender = False
        req.is_completed_by_receiver = False
        req.sender_confirmed = False
        req.receiver_confirmed = False

    db.session.commit()
    flash("User blocked.", "success")
    return redirect(next_url)


@app.route("/profile")
@login_required
def profile():
    return redirect(url_for("user_profile", username=g.user.username))


@app.route("/profile/edit", methods=["GET", "POST"])
@login_required
def profile_edit():
    if request.method == "POST":
        bio = request.form.get("bio", "").strip()
        location = request.form.get("location", "").strip()
        availability_status = request.form.get("availability_status", "Available").strip()
        show_email_on_profile = request.form.get("show_email_on_profile") == "on"
        profile_image = request.files.get("profile_image")

        if len(bio) > 300:
            flash("Bio must be 300 characters or fewer.", "error")
            return redirect(url_for("profile_edit"))
        if len(location) > 120:
            flash("Location must be 120 characters or fewer.", "error")
            return redirect(url_for("profile_edit"))
        if availability_status not in AVAILABILITY_STATUSES:
            flash("Please choose a valid availability status.", "error")
            return redirect(url_for("profile_edit"))

        old_image = g.user.profile_image
        new_image = None
        if profile_image and profile_image.filename:
            try:
                new_image = save_profile_image(profile_image, g.user.user_id)
            except ValueError as exc:
                flash(str(exc), "error")
                return redirect(url_for("profile_edit"))

        g.user.bio = bio or None
        g.user.location = location or None
        g.user.availability_status = availability_status
        g.user.availability = availability_status == "Available"
        g.user.show_email_on_profile = show_email_on_profile
        if new_image:
            g.user.profile_image = new_image

        db.session.commit()

        if new_image and old_image != new_image:
            remove_old_profile_image(old_image)

        flash("Profile updated successfully.", "success")
        return redirect(url_for("user_profile", username=g.user.username))

    return render_template(
        "user/edit_profile.html",
        availability_statuses=AVAILABILITY_STATUSES,
    )


@app.route("/profile/delete", methods=["POST"])
@login_required
def delete_account():
    password = request.form.get("password", "")
    if not password:
        flash("Password is required to delete account.", "error")
        return redirect(url_for("profile_edit"))

    if not verify_password(g.user.password, password):
        flash("Incorrect password. Account was not deleted.", "error")
        return redirect(url_for("profile_edit"))

    user_id = g.user.user_id
    username = g.user.username
    email = g.user.email
    old_image = g.user.profile_image

    try:
        db.session.execute(
            text("DELETE FROM user_skills WHERE user_id = :uid"),
            {"uid": user_id},
        )
        db.session.execute(
            text("DELETE FROM user_skills_offered WHERE user_id = :uid"),
            {"uid": user_id},
        )
        db.session.execute(
            text("DELETE FROM user_skills_wanted WHERE user_id = :uid"),
            {"uid": user_id},
        )
        db.session.execute(
            text("DELETE FROM ratings WHERE from_user = :uid OR to_user = :uid"),
            {"uid": user_id},
        )
        db.session.execute(
            text("DELETE FROM messages WHERE sender_id = :uid OR receiver_id = :uid"),
            {"uid": user_id},
        )
        db.session.execute(
            text("DELETE FROM notifications WHERE user_id = :uid"),
            {"uid": user_id},
        )
        db.session.execute(
            text("DELETE FROM blocked_users WHERE blocker_id = :uid OR blocked_id = :uid"),
            {"uid": user_id},
        )
        db.session.execute(
            text("DELETE FROM user_reports WHERE reporter_id = :uid OR reported_user_id = :uid"),
            {"uid": user_id},
        )
        db.session.execute(
            text(
                "DELETE FROM requests "
                "WHERE sender_id = :uid OR receiver_id = :uid OR session_proposed_by = :uid"
            ),
            {"uid": user_id},
        )
        db.session.execute(
            text("DELETE FROM users WHERE user_id = :uid"),
            {"uid": user_id},
        )
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        app.logger.exception("Account deletion failed")
        app.logger.warning("Account deletion error: %s", exc)
        flash("Could not delete account right now. Please try again.", "error")
        return redirect(url_for("profile_edit"))

    if old_image:
        remove_old_profile_image(old_image)

    try:
        send_account_deleted_email(email, username)
    except Exception as exc:
        app.logger.warning("Account deletion email error: %s", exc)
        app.logger.exception("Account deletion email send failed")

    session.clear()
    flash("Your account has been deleted successfully.", "success")
    return redirect(url_for("index"))


@app.route("/messages")
@login_required
def messages_inbox():
    request_contacts = (
        db.session.query(User)
        .join(
            Request,
            ((Request.sender_id == User.user_id) & (Request.receiver_id == g.user.user_id))
            | ((Request.receiver_id == User.user_id) & (Request.sender_id == g.user.user_id)),
        )
        .filter(User.user_id != g.user.user_id)
        .distinct()
    )
    message_contacts = (
        db.session.query(User)
        .join(
            Message,
            ((Message.sender_id == User.user_id) & (Message.receiver_id == g.user.user_id))
            | ((Message.receiver_id == User.user_id) & (Message.sender_id == g.user.user_id)),
        )
        .filter(User.user_id != g.user.user_id)
        .distinct()
    )

    request_contacts = request_contacts.all()
    message_contacts = message_contacts.all()

    combined = {user.user_id: user for user in request_contacts + message_contacts}
    latest_map = {}
    if combined:
        rows = (
            db.session.query(
                db.func.greatest(Message.sender_id, Message.receiver_id).label("pair_high"),
                db.func.least(Message.sender_id, Message.receiver_id).label("pair_low"),
                db.func.max(Message.created_at).label("latest"),
            )
            .filter(
                (Message.sender_id == g.user.user_id) | (Message.receiver_id == g.user.user_id)
            )
            .group_by("pair_high", "pair_low")
            .all()
        )
        for row in rows:
            other_id = row.pair_low if row.pair_high == g.user.user_id else row.pair_high
            latest_map[other_id] = row.latest

    contacts = sorted(
        combined.values(),
        key=lambda u: (latest_map.get(u.user_id) is None, latest_map.get(u.user_id) or datetime.min),
        reverse=True,
    )
    unread_map = get_unread_message_counts(g.user.user_id)
    return render_template(
        "user/messages.html",
        contacts=contacts,
        active_user=None,
        messages=[],
        unread_map=unread_map,
        messaging_blocked=False,
        blocked_by_me=False,
        blocked_me=False,
        block_notice=None,
        report_reasons=REPORT_REASONS,
    )


@app.route("/messages/id/<int:user_id>", methods=["GET"])
@login_required
def messages_thread_legacy(user_id):
    active_user = User.query.get_or_404(user_id)
    return redirect(url_for("messages_thread", username=active_user.username))


@app.route("/messages/<string:username>", methods=["GET", "POST"])
@login_required
def messages_thread(username):
    active_user = User.query.filter_by(username=username).first_or_404()
    user_id = active_user.user_id
    if active_user.user_id == g.user.user_id:
        flash("You cannot message yourself.", "error")
        return redirect(url_for("messages_inbox"))
    if active_user.is_blocked:
        flash("This user is blocked.", "error")
        return redirect(url_for("messages_inbox"))

    blocked_by_me = has_user_blocked(g.user.user_id, active_user.user_id)
    blocked_me = has_user_blocked(active_user.user_id, g.user.user_id)
    messaging_blocked = blocked_by_me or blocked_me
    if blocked_by_me:
        block_notice = "You have blocked this user"
    elif blocked_me:
        block_notice = "This user is blocked. You cannot send messages."
    else:
        block_notice = None

    if request.method == "POST":
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        if messaging_blocked:
            if is_ajax:
                return jsonify({"ok": False, "error": block_notice or "Messaging is blocked."}), 403
            flash(block_notice or "Messaging is blocked.", "error")
            return redirect(url_for("messages_thread", username=active_user.username))

        message_text = request.form.get("message", "").strip()
        attachment_url = request.form.get("attachment_url", "").strip() or None
        attachment_type = request.form.get("attachment_type", "").strip() or None

        if not message_text and not attachment_url:
            if is_ajax:
                return jsonify({"ok": False, "error": "Message cannot be empty."}), 400
            flash("Message cannot be empty.", "error")
            return redirect(url_for("messages_thread", username=active_user.username))
        if len(message_text) > 2000:
            if is_ajax:
                return jsonify({"ok": False, "error": "Message is too long."}), 400
            flash("Message is too long.", "error")
            return redirect(url_for("messages_thread", username=active_user.username))

        if attachment_url and not attachment_type:
            extension = attachment_url.rsplit(".", 1)[-1].split("?")[0].lower() if "." in attachment_url else ""
            attachment_type = attachment_type_for_extension(extension)

        new_message = Message(
            sender_id=g.user.user_id,
            receiver_id=active_user.user_id,
            message=message_text,
            message_type="user",
            attachment_url=attachment_url,
            attachment_type=attachment_type,
        )
        db.session.add(new_message)
        db.session.commit()

        if is_ajax:
            return jsonify(
                {
                    "ok": True,
                    "message": {
                        "message_id": new_message.message_id,
                        "sender_id": new_message.sender_id,
                        "text": new_message.message,
                        "message_type": new_message.message_type,
                        "attachment_url": new_message.attachment_url,
                        "attachment_type": new_message.attachment_type,
                        "created_at": new_message.created_at.strftime("%Y-%m-%d %H:%M"),
                        "read": bool(new_message.read_at),
                    },
                    "unread_map": get_unread_message_counts(g.user.user_id),
                }
            )

        return redirect(url_for("messages_thread", username=active_user.username))

    contacts_query = (
        db.session.query(User)
        .join(
            Message,
            ((Message.sender_id == User.user_id) & (Message.receiver_id == g.user.user_id))
            | ((Message.receiver_id == User.user_id) & (Message.sender_id == g.user.user_id)),
        )
        .filter(User.user_id != g.user.user_id)
        .distinct()
    )

    contacts = contacts_query.all()
    if active_user.user_id not in {u.user_id for u in contacts}:
        contacts.append(active_user)
    latest_map = {}
    rows = (
        db.session.query(
            db.func.greatest(Message.sender_id, Message.receiver_id).label("pair_high"),
            db.func.least(Message.sender_id, Message.receiver_id).label("pair_low"),
            db.func.max(Message.created_at).label("latest"),
        )
        .filter((Message.sender_id == g.user.user_id) | (Message.receiver_id == g.user.user_id))
        .group_by("pair_high", "pair_low")
        .all()
    )
    for row in rows:
        other_id = row.pair_low if row.pair_high == g.user.user_id else row.pair_high
        latest_map[other_id] = row.latest
    contacts = sorted(
        contacts,
        key=lambda u: (latest_map.get(u.user_id) is None, latest_map.get(u.user_id) or datetime.min),
        reverse=True,
    )

    messages = (
        Message.query.filter(
            ((Message.sender_id == g.user.user_id) & (Message.receiver_id == active_user.user_id))
            | ((Message.sender_id == active_user.user_id) & (Message.receiver_id == g.user.user_id))
        )
        .order_by(Message.created_at.asc())
        .all()
    )

    unread_message_ids = [
        msg.message_id
        for msg in messages
        if msg.sender_id == active_user.user_id
        and msg.receiver_id == g.user.user_id
        and msg.read_at is None
    ]
    unread_in_thread = [
        msg
        for msg in messages
        if msg.sender_id == active_user.user_id
        and msg.receiver_id == g.user.user_id
        and msg.read_at is None
    ]
    if unread_in_thread:
        now = datetime.utcnow()
        for msg in unread_in_thread:
            msg.read_at = now
        db.session.commit()

    active_presence_label = build_presence_label(active_user.last_seen_at)

    unread_map = get_unread_message_counts(g.user.user_id)
    return render_template(
        "user/messages.html",
        contacts=contacts,
        active_user=active_user,
        messages=messages,
        unread_map=unread_map,
        unread_message_ids=unread_message_ids,
        active_presence_label=active_presence_label,
        messaging_blocked=messaging_blocked,
        blocked_by_me=blocked_by_me,
        blocked_me=blocked_me,
        block_notice=block_notice,
        report_reasons=REPORT_REASONS,
    )


@app.route("/messages/<string:username>/poll")
@login_required
def messages_poll(username):
    active_user = User.query.filter_by(username=username).first_or_404()
    if active_user.user_id == g.user.user_id:
        return jsonify({"messages": []})

    if is_user_blocked_between(g.user.user_id, active_user.user_id):
        return jsonify(
            {
                "messages": [],
                "seen_ids": [],
                "presence_label": build_presence_label(active_user.last_seen_at),
                "unread_map": get_unread_message_counts(g.user.user_id),
                "blocked": True,
            }
        )

    after_id = request.args.get("after_id", type=int) or 0
    after_seen_id = request.args.get("after_seen_id", type=int) or 0
    query = Message.query.filter(
        ((Message.sender_id == g.user.user_id) & (Message.receiver_id == active_user.user_id))
        | ((Message.sender_id == active_user.user_id) & (Message.receiver_id == g.user.user_id))
    )
    if after_id:
        query = query.filter(Message.message_id > after_id)

    rows = query.order_by(Message.message_id.asc()).all()

    unread = [
        msg
        for msg in rows
        if msg.sender_id == active_user.user_id
        and msg.receiver_id == g.user.user_id
        and msg.read_at is None
    ]
    if unread:
        now = datetime.utcnow()
        for msg in unread:
            msg.read_at = now
        db.session.commit()

    seen_rows = (
        Message.query.filter(
            Message.sender_id == g.user.user_id,
            Message.receiver_id == active_user.user_id,
            Message.read_at.isnot(None),
            Message.message_id > after_seen_id,
        )
        .order_by(Message.message_id.asc())
        .all()
    )

    return jsonify(
        {
            "messages": [
                {
                    "message_id": msg.message_id,
                    "sender_id": msg.sender_id,
                    "text": msg.message,
                    "message_type": msg.message_type,
                    "attachment_url": msg.attachment_url,
                    "attachment_type": msg.attachment_type,
                    "created_at": msg.created_at.strftime("%Y-%m-%d %H:%M"),
                    "read": bool(msg.read_at),
                }
                for msg in rows
            ],
            "seen_ids": [msg.message_id for msg in seen_rows],
            "presence_label": build_presence_label(active_user.last_seen_at),
            "unread_map": get_unread_message_counts(g.user.user_id),
        }
    )


@app.route("/notifications")
@login_required
def notifications_page():
    notifications = (
        Notification.query.filter(
            Notification.user_id == g.user.user_id,
            Notification.notif_type != "message",
        )
        .order_by(Notification.created_at.desc())
        .limit(40)
        .all()
    )
    return render_template("user/notifications.html", notifications=notifications)


@app.route("/notifications/<int:notification_id>/read", methods=["POST"])
@login_required
def mark_notification_read(notification_id):
    notification = Notification.query.get_or_404(notification_id)
    if notification.user_id != g.user.user_id:
        abort(403)
    notification.is_read = True
    db.session.commit()
    return redirect(notification.link or url_for("notifications_page"))


@app.route("/notifications/mark-all-read", methods=["POST"])
@login_required
def mark_all_notifications_read():
    Notification.query.filter_by(user_id=g.user.user_id, is_read=False).update(
        {"is_read": True}, synchronize_session=False
    )
    db.session.commit()
    flash("All notifications marked as read.", "success")
    return redirect(request.referrer or url_for("notifications_page"))


@app.route("/reports/create", methods=["POST"])
@login_required
def create_report():
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    def fail(message):
        if is_ajax:
            return jsonify({"ok": False, "error": message}), 400
        flash(message, "error")
        return redirect(request.referrer or url_for("dashboard"))

    if not app.config.get("ALLOW_USER_REPORTS", True):
        return fail("Reporting is temporarily disabled by admin.")

    reported_user_id = request.form.get("reported_user_id", type=int)
    reason = request.form.get("reason", "").strip()
    description = request.form.get("description", "").strip()
    attachments = request.files.getlist("attachments")

    if not reported_user_id:
        return fail("Invalid report target.")
    if reason not in REPORT_REASONS:
        return fail("Please select a valid report reason.")
    if len(description) > 1000:
        return fail("Report description is too long.")

    target = User.query.get(reported_user_id)
    if not target:
        return fail("Reported user does not exist.")

    existing_active_report = (
        UserReport.query.filter_by(
            reporter_id=g.user.user_id,
            reported_user_id=reported_user_id,
        )
        .filter(UserReport.status.in_(["pending", "reviewing"]))
        .first()
    )
    if existing_active_report:
        return fail("This user is already reported. Please wait for admin to review it.")

    attachment_urls = []
    for attachment in attachments:
        if not attachment or not attachment.filename:
            continue
        if not allowed_attachment_file(attachment.filename):
            return fail("Unsupported attachment file type.")
        safe_name = secure_filename(attachment.filename)
        if "." not in safe_name:
            return fail("Invalid attachment filename.")
        extension = safe_name.rsplit(".", 1)[1].lower()
        unique_name = f"report_{uuid.uuid4().hex}.{extension}"
        target_path = os.path.join(REPORT_UPLOAD_FOLDER, unique_name)
        attachment.save(target_path)
        attachment_urls.append(url_for("static", filename=f"{REPORT_UPLOAD_SUBDIR}/{unique_name}"))

    db.session.add(
        UserReport(
            reporter_id=g.user.user_id,
            reported_user_id=reported_user_id,
            reason=reason,
            description=description or None,
            report_attachments=json.dumps(attachment_urls) if attachment_urls else None,
            status="pending",
        )
    )
    db.session.commit()
    if is_ajax:
        return jsonify({"ok": True, "message": "Report submitted. Admin will review it."})
    flash("Report submitted. Admin will review it.", "success")
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/admin/reports/<int:report_id>/action", methods=["POST"])
@login_required
@admin_required
def admin_report_action(report_id):
    action = request.form.get("action", "").strip().lower()
    return handle_report_action(report_id, action)


def handle_report_action(report_id, action):
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    report = UserReport.query.get_or_404(report_id)
    target_user = User.query.get(report.reported_user_id)

    current_status = (report.status or "pending").strip().lower()
    allowed_actions = {
        "pending": {"reviewing", "block"},
        "reviewing": {"resolve", "warn", "block"},
        "warned": set(),
        "resolved": set(),
        "blocked": {"unblock"},
    }

    if action not in allowed_actions.get(current_status, set()):
        message = "This action is not available for the current report status."
        if is_ajax:
            return jsonify({"ok": False, "message": message}), 400
        flash(message, "error")
        return redirect(request.referrer or url_for("admin_reports_page"))

    if action == "reviewing":
        report.status = "reviewing"
    elif action == "resolve":
        report.status = "resolved"
    elif action == "warn":
        report.status = "warned"
        if target_user:
            create_notification(
                target_user.user_id,
                "You have received a warning from the SkillSwap team due to a reported violation.",
                "system",
                url_for("user_profile", username=target_user.username),
            )
    elif action == "block":
        report.status = "blocked"
        if target_user and not target_user.is_admin:
            target_user.is_blocked = True
            create_notification(
                target_user.user_id,
                "Your account was blocked by admin due to policy violations.",
                "system",
                url_for("index"),
            )
    elif action == "unblock":
        report.status = "reviewing"
        if target_user and not target_user.is_admin:
            target_user.is_blocked = False
            create_notification(
                target_user.user_id,
                "Your account block has been removed. Please continue to follow community guidelines.",
                "system",
                url_for("index"),
            )

    db.session.commit()
    if is_ajax:
        return jsonify(
            {
                "ok": True,
                "message": "Report action applied.",
                "status": report.status,
            }
        )
    flash("Report action applied.", "success")
    return redirect(request.referrer or url_for("admin_reports_page"))


@app.route("/admin/reports/<int:report_id>/review", methods=["POST"])
@login_required
@admin_required
def admin_report_review(report_id):
    return handle_report_action(report_id, "reviewing")


@app.route("/admin/reports/<int:report_id>/warn", methods=["POST"])
@login_required
@admin_required
def admin_report_warn(report_id):
    return handle_report_action(report_id, "warn")


@app.route("/admin/reports/<int:report_id>/resolve", methods=["POST"])
@login_required
@admin_required
def admin_report_resolve(report_id):
    return handle_report_action(report_id, "resolve")


@app.route("/admin/reports/<int:report_id>/block", methods=["POST"])
@login_required
@admin_required
def admin_report_block(report_id):
    return handle_report_action(report_id, "block")


@app.route("/admin/reports/<int:report_id>/unblock", methods=["POST"])
@login_required
@admin_required
def admin_report_unblock(report_id):
    return handle_report_action(report_id, "unblock")


@app.route("/requests/<int:request_id>/action", methods=["POST"])
@login_required
def request_action(request_id):
    action = request.form.get("action", "").strip()
    req = Request.query.get_or_404(request_id)

    if is_user_blocked_between(req.sender_id, req.receiver_id):
        flash("This exchange is unavailable because one user has blocked the other.", "error")
        return redirect(url_for("requests_page"))

    if action in {"accept", "reject"}:
        if req.receiver_id != g.user.user_id or req.status != "pending":
            flash("You cannot perform that action.", "error")
            return redirect(url_for("requests_page"))

        if action == "accept":
            req.final_offered_skill_id = req.offered_skill_id
            req.final_requested_skill_id = req.requested_skill_id
            req.status = "accepted"
            ensure_exchange_session_record(req)
        else:
            req.status = "rejected"

        req.is_completed_by_sender = False
        req.is_completed_by_receiver = False
        req.sender_confirmed = False
        req.receiver_confirmed = False
        create_notification(
            req.sender_id,
            (
                f"Your request to {g.user.name} was accepted."
                if action == "accept"
                else f"Your request to {g.user.name} was rejected."
            ),
            "request",
            url_for("requests_page"),
        )
    elif action == "request_change":
        if req.receiver_id != g.user.user_id or req.status != "pending":
            flash("You cannot perform that action.", "error")
            return redirect(url_for("requests_page"))

        preferred_receive_skill_id = request.form.get("preferred_receive_skill_id", type=int)
        sender = User.query.get(req.sender_id)
        sender_offered_ids = {s.skill_id for s in sender.offered_skills} if sender else set()

        if preferred_receive_skill_id not in sender_offered_ids:
            flash("Please choose a valid skill to receive.", "error")
            return redirect(url_for("requests_page"))
        if preferred_receive_skill_id == req.offered_skill_id:
            flash("Selected skill matches original proposal. Use Accept instead.", "error")
            return redirect(url_for("requests_page"))

        req.status = "countered"
        req.final_offered_skill_id = preferred_receive_skill_id
        # Receiver cannot change what sender asked for.
        req.final_requested_skill_id = req.requested_skill_id
        req.is_completed_by_sender = False
        req.is_completed_by_receiver = False
        req.sender_confirmed = False
        req.receiver_confirmed = False

        create_notification(
            req.sender_id,
            f"{g.user.name} requested a change to your exchange proposal.",
            "request",
            url_for("requests_page"),
        )
    elif action == "accept_change":
        if req.sender_id != g.user.user_id or req.status != "countered":
            flash("You cannot perform that action.", "error")
            return redirect(url_for("requests_page"))

        if not req.final_offered_skill_id:
            flash("No suggested change found.", "error")
            return redirect(url_for("requests_page"))

        req.status = "accepted"
        req.final_requested_skill_id = req.requested_skill_id
        req.is_completed_by_sender = False
        req.is_completed_by_receiver = False
        req.sender_confirmed = False
        req.receiver_confirmed = False
        ensure_exchange_session_record(req)

        create_notification(
            req.receiver_id,
            f"{g.user.name} accepted your requested change.",
            "request",
            url_for("requests_page"),
        )
    elif action == "reject_change":
        if req.sender_id != g.user.user_id or req.status != "countered":
            flash("You cannot perform that action.", "error")
            return redirect(url_for("requests_page"))

        req.status = "rejected"
        req.final_offered_skill_id = None
        req.final_requested_skill_id = None
        req.is_completed_by_sender = False
        req.is_completed_by_receiver = False
        req.sender_confirmed = False
        req.receiver_confirmed = False

        create_notification(
            req.receiver_id,
            f"{g.user.name} rejected your requested change.",
            "request",
            url_for("requests_page"),
        )
    elif action == "complete":
        if req.status not in {"accepted", "awaiting_confirmation"}:
            flash("Only accepted exchanges can be marked completed.", "error")
            return redirect(url_for("requests_page"))
        role = request_user_role(req, g.user.user_id)
        if role is None:
            flash("You cannot perform that action.", "error")
            return redirect(url_for("requests_page"))

        if role == "sender":
            req.is_completed_by_sender = True
            req.sender_confirmed = True
        else:
            req.is_completed_by_receiver = True
            req.receiver_confirmed = True

        other_user_id = req.receiver_id if role == "sender" else req.sender_id
        if req.sender_confirmed and req.receiver_confirmed:
            req.status = "completed"
            create_notification(
                other_user_id,
                f"Exchange with {g.user.name} is now completed.",
                "completion_confirmation",
                url_for("requests_page"),
            )
            create_notification(
                g.user.user_id,
                "Exchange completed successfully by both participants.",
                "completion_confirmation",
                url_for("requests_page"),
            )
        else:
            req.status = "awaiting_confirmation"
            create_notification(
                other_user_id,
                f"{g.user.name} marked the exchange as complete. Confirm completion.",
                "completion_confirmation",
                url_for("requests_page"),
            )
    elif action == "confirm_completion":
        if req.status != "awaiting_confirmation":
            flash("This exchange is not awaiting confirmation.", "error")
            return redirect(url_for("requests_page"))
        role = request_user_role(req, g.user.user_id)
        if role is None:
            flash("You cannot perform that action.", "error")
            return redirect(url_for("requests_page"))
        if role == "sender":
            req.is_completed_by_sender = True
            req.sender_confirmed = True
        else:
            req.is_completed_by_receiver = True
            req.receiver_confirmed = True

        if req.sender_confirmed and req.receiver_confirmed:
            req.status = "completed"
            req.session_completed_at = req.session_completed_at or datetime.utcnow()
            req.session_sender_last_ping_at = None
            req.session_receiver_last_ping_at = None
            req.session_completed_at = req.session_completed_at or datetime.utcnow()
            req.session_sender_last_ping_at = None
            req.session_receiver_last_ping_at = None
            other_user_id = req.receiver_id if role == "sender" else req.sender_id
            create_notification(
                other_user_id,
                f"{g.user.name} confirmed completion. Exchange is completed.",
                "completion_confirmation",
                url_for("requests_page"),
            )
    elif action == "reject_completion":
        if req.status != "awaiting_confirmation":
            flash("This exchange is not awaiting confirmation.", "error")
            return redirect(url_for("requests_page"))
        role = request_user_role(req, g.user.user_id)
        if role is None:
            flash("You cannot perform that action.", "error")
            return redirect(url_for("requests_page"))
        req.status = "accepted"
        req.is_completed_by_sender = False
        req.is_completed_by_receiver = False
        req.sender_confirmed = False
        req.receiver_confirmed = False
        ensure_exchange_session_record(req)
        other_user_id = req.receiver_id if role == "sender" else req.sender_id
        create_notification(
            other_user_id,
            f"{g.user.name} rejected completion confirmation. Exchange moved back to accepted.",
            "completion_confirmation",
            url_for("requests_page"),
        )
    else:
        flash("Unknown action.", "error")
        return redirect(url_for("requests_page"))

    db.session.commit()
    flash("Request updated.", "success")
    return redirect(url_for("requests_page"))


@app.route("/requests/<int:request_id>/start-session", methods=["POST"])
@login_required
def start_session(request_id):
    req = Request.query.get_or_404(request_id)
    if not app.config.get("ALLOW_SESSION_CREATION", True):
        flash("Sessions are currently disabled.", "error")
        return redirect(url_for("requests_page"))
    if is_user_blocked_between(req.sender_id, req.receiver_id):
        flash("Session cannot be started because one user has blocked the other.", "error")
        return redirect(url_for("requests_page"))
    if g.user.user_id not in {req.sender_id, req.receiver_id}:
        flash("You cannot start this session.", "error")
        return redirect(url_for("requests_page"))
    if req.status not in {"accepted", "awaiting_confirmation"}:
        flash("Session can only start on an accepted exchange.", "error")
        return redirect(url_for("requests_page"))

    ensure_exchange_session_record(req)
    db.session.commit()
    return redirect(url_for("session_entry", request_id=req.request_id))


@app.route("/session/<int:request_id>/enter")
@app.route("/sessions/<int:request_id>/enter")
@login_required
def session_entry(request_id):
    req = Request.query.get_or_404(request_id)
    if not app.config.get("ALLOW_SESSION_CREATION", True):
        flash("Sessions are currently disabled.", "error")
        return redirect(url_for("requests_page"))
    if g.user.user_id not in {req.sender_id, req.receiver_id} and not g.user.is_admin:
        flash("You cannot access this session.", "error")
        return redirect(url_for("requests_page"))
    if req.status not in {"accepted", "awaiting_confirmation"} or req.session_completed_at:
        flash("This session is no longer active.", "error")
        return redirect(url_for("requests_page"))

    ensure_exchange_session_record(req)
    db.session.commit()

    return render_template(
        "user/session_entry.html",
        request_id=req.request_id,
        meeting_url=get_direct_session_meeting_link(req),
        is_participant=g.user.user_id in {req.sender_id, req.receiver_id},
    )


@app.route("/sessions/<int:request_id>/open", methods=["POST"])
@login_required
def session_open_meeting(request_id):
    req = Request.query.get_or_404(request_id)
    if not app.config.get("ALLOW_SESSION_CREATION", True):
        return jsonify({"ok": False, "message": "Sessions disabled"}), 409
    if g.user.user_id not in {req.sender_id, req.receiver_id}:
        return jsonify({"ok": False, "message": "Forbidden"}), 403
    if req.status not in {"accepted", "awaiting_confirmation"} or req.session_completed_at:
        return jsonify({"ok": False, "message": "Session inactive"}), 409

    ensure_exchange_session_record(req)
    internal_entry_link = ensure_request_session_link(req)
    direct_meeting_link = get_direct_session_meeting_link(req)
    was_active = is_session_participant_active(req, g.user.user_id)
    mark_session_participant_presence(req, g.user.user_id, True)
    if not was_active:
        post_system_join_message_for_actor(req, g.user.user_id, internal_entry_link)
    db.session.commit()

    return jsonify({"ok": True, "meeting_url": direct_meeting_link})


@app.route("/requests/<int:request_id>/session-heartbeat", methods=["POST"])
@login_required
def session_heartbeat(request_id):
    req = Request.query.get_or_404(request_id)
    if not app.config.get("ALLOW_SESSION_CREATION", True):
        return ("", 409)
    if g.user.user_id not in {req.sender_id, req.receiver_id}:
        return ("", 403)
    if req.status not in {"accepted", "awaiting_confirmation"} or req.session_completed_at:
        return ("", 409)

    mark_session_participant_presence(req, g.user.user_id, True)
    db.session.commit()
    return ("", 204)


@app.route("/requests/<int:request_id>/leave-session", methods=["POST"])
@login_required
def leave_session(request_id):
    req = Request.query.get_or_404(request_id)
    if not app.config.get("ALLOW_SESSION_CREATION", True):
        return ("", 409)
    if g.user.user_id not in {req.sender_id, req.receiver_id}:
        return ("", 403)

    mark_session_participant_presence(req, g.user.user_id, False)
    db.session.commit()
    return ("", 204)


@app.route("/requests/<int:request_id>/schedule-session", methods=["POST"])
@login_required
def schedule_session(request_id):
    req = Request.query.get_or_404(request_id)
    if not app.config.get("ALLOW_SESSION_CREATION", True):
        flash("Sessions are currently disabled.", "error")
        return redirect(url_for("requests_page"))
    if is_user_blocked_between(req.sender_id, req.receiver_id):
        flash("Session scheduling is disabled because one user has blocked the other.", "error")
        return redirect(url_for("requests_page"))
    if g.user.user_id not in {req.sender_id, req.receiver_id}:
        flash("You cannot schedule a session for this exchange.", "error")
        return redirect(url_for("requests_page"))
    if req.status not in {"pending", "countered"}:
        flash("Session scheduling is available only before exchange acceptance.", "error")
        return redirect(url_for("requests_page"))

    action = request.form.get("session_action", "propose").strip().lower()

    if action == "accept":
        if not req.session_scheduled_for or not req.session_proposed_by:
            flash("No pending session proposal to accept.", "error")
            return redirect(url_for("requests_page"))
        if req.session_proposed_by == g.user.user_id:
            flash("You cannot accept your own session proposal.", "error")
            return redirect(url_for("requests_page"))

        req.session_confirmed_at = datetime.utcnow()
        req.session_started_at = None
        req.session_completed_at = None
        meeting_link = ensure_request_session_link(req)
        post_system_session_message(
            req,
            (
                f"Session scheduled on {req.session_scheduled_for.strftime('%Y-%m-%d')} at "
                f"{req.session_scheduled_for.strftime('%H:%M')}. Join here: {meeting_link}"
            ),
        )
        db.session.commit()
        flash("Session schedule confirmed.", "success")
        return redirect(url_for("requests_page"))

    session_date = request.form.get("session_date", "").strip()
    session_time = request.form.get("session_time", "").strip()
    if not session_date or not session_time:
        flash("Please choose both date and time.", "error")
        return redirect(url_for("requests_page"))

    try:
        scheduled_for = datetime.strptime(f"{session_date} {session_time}", "%Y-%m-%d %H:%M")
    except ValueError:
        flash("Invalid date or time format.", "error")
        return redirect(url_for("requests_page"))

    if scheduled_for <= datetime.utcnow():
        flash("Please choose a future date and time.", "error")
        return redirect(url_for("requests_page"))

    is_initial_proposal = req.session_scheduled_for is None and req.session_confirmed_at is None
    if is_initial_proposal and g.user.user_id != req.sender_id:
        flash("Only the exchange sender can propose the initial session time.", "error")
        return redirect(url_for("requests_page"))

    req.session_scheduled_for = scheduled_for
    req.session_proposed_by = g.user.user_id
    req.session_confirmed_at = None
    req.session_started_at = None
    req.session_completed_at = None

    proposal_text = "Proposed session time"
    if action == "extend":
        proposal_text = "Proposed extended session time"
    post_session_chat_message(
        req,
        g.user.user_id,
        (
            f"{proposal_text}: {scheduled_for.strftime('%Y-%m-%d')} "
            f"at {scheduled_for.strftime('%H:%M')}."
        ),
    )
    db.session.commit()
    flash("Session proposal sent for approval.", "success")
    return redirect(url_for("requests_page"))


@app.route("/admin/sessions/<int:request_id>/join")
@login_required
@admin_required
def admin_join_session(request_id):
    req = Request.query.get_or_404(request_id)
    if not app.config.get("ALLOW_SESSION_CREATION", True):
        flash("Sessions are currently disabled.", "error")
        return redirect(url_for("admin_sessions_page"))
    if req.status not in {"accepted", "awaiting_confirmation"} or req.session_completed_at:
        flash("No active session for this exchange yet.", "error")
        return redirect(url_for("admin_sessions_page"))

    if not req.session_link:
        flash("Meeting link is not available for this session.", "error")
        return redirect(url_for("admin_sessions_page"))

    return redirect(req.session_link)


@app.route("/requests/<int:request_id>/complete-session", methods=["POST"])
@login_required
def complete_session(request_id):
    req = Request.query.get_or_404(request_id)
    if is_user_blocked_between(req.sender_id, req.receiver_id):
        flash("Session completion is disabled because one user has blocked the other.", "error")
        return redirect(url_for("requests_page"))
    if g.user.user_id not in {req.sender_id, req.receiver_id}:
        flash("You cannot complete this session.", "error")
        return redirect(url_for("requests_page"))
    if req.status not in {"accepted", "awaiting_confirmation"}:
        flash("This exchange cannot be completed right now.", "error")
        return redirect(url_for("requests_page"))
    if not req.session_started_at:
        flash("Session is not active yet.", "error")
        return redirect(url_for("requests_page"))

    req.session_completed_at = datetime.utcnow()
    req.status = "completed"
    req.sender_confirmed = True
    req.receiver_confirmed = True
    req.is_completed_by_sender = True
    req.is_completed_by_receiver = True
    req.session_sender_last_ping_at = None
    req.session_receiver_last_ping_at = None
    post_system_session_message(req, "Session marked as completed.")
    db.session.commit()
    flash("Session marked completed.", "success")
    return redirect(url_for("requests_page"))


@app.route("/requests/<int:request_id>/rate", methods=["GET", "POST"])
@login_required
def rate_user(request_id):
    req = Request.query.get_or_404(request_id)

    if not app.config.get("ALLOW_RATING_AFTER_SESSION", True):
        flash("Ratings are currently disabled.", "error")
        return redirect(url_for("requests_page"))

    if is_user_blocked_between(req.sender_id, req.receiver_id):
        flash("Rating is unavailable because one user has blocked the other.", "error")
        return redirect(url_for("requests_page"))

    if req.status != "completed" or g.user.user_id not in {req.sender_id, req.receiver_id}:
        flash("Rating is available only after completed exchanges.", "error")
        return redirect(url_for("requests_page"))

    is_sender = g.user.user_id == req.sender_id
    already_rated = req.rated_by_sender if is_sender else req.rated_by_receiver
    existing_rating = Rating.query.filter_by(
        exchange_request_id=req.request_id,
        from_user=g.user.user_id,
    ).first()
    if existing_rating:
        already_rated = True
    if already_rated:
        return redirect(url_for("requests_page"))

    target_user_id = req.receiver_id if is_sender else req.sender_id
    target_user = User.query.get(target_user_id)

    if request.method == "POST":
        rating_value = request.form.get("rating", type=int)
        feedback = request.form.get("feedback", "").strip() or None

        if rating_value is None or rating_value < 1 or rating_value > 5:
            flash("Rating must be between 1 and 5.", "error")
            return redirect(url_for("rate_user", request_id=request_id))

        if app.config.get("REQUIRE_FEEDBACK_SUBMISSION", False) and not feedback:
            flash("Feedback is required for rating submission.", "error")
            return redirect(url_for("rate_user", request_id=request_id))

        rating = Rating(
            from_user=g.user.user_id,
            to_user=target_user_id,
            exchange_request_id=req.request_id,
            rating=rating_value,
            feedback=feedback,
        )
        db.session.add(rating)
        if is_sender:
            req.sender_rated = True
            req.rated_by_sender = True
        else:
            req.receiver_rated = True
            req.rated_by_receiver = True

        create_notification(
            target_user_id,
            f"{g.user.name} rated your exchange.",
            "rating",
            url_for("user_profile", username=target_user.username),
        )

        db.session.commit()
        flash("Feedback submitted.", "success")
        return redirect(url_for("requests_page"))

    return render_template("user/rate.html", req=req, target_user=target_user)


@app.route("/search")
@login_required
def search():
    query_text = request.args.get("q", "").strip()
    available_only = request.args.get("available_only", "0") == "1"
    level_filter = request.args.get("level", "").strip()
    category_filter = request.args.get("category", "").strip()
    is_ajax = request.args.get("ajax", "0") == "1"

    blocked_related_ids = get_blocked_related_user_ids(g.user.user_id)

    offered_row = aliased(UserSkillsOffered)
    wanted_row = aliased(UserSkillsWanted)
    offered_skill = aliased(Skill)
    wanted_skill = aliased(Skill)

    users_query = (
        User.query.outerjoin(offered_row, User.user_id == offered_row.user_id)
        .outerjoin(offered_skill, offered_skill.skill_id == offered_row.skill_id)
        .outerjoin(wanted_row, User.user_id == wanted_row.user_id)
        .outerjoin(wanted_skill, wanted_skill.skill_id == wanted_row.skill_id)
        .filter(
            User.user_id != g.user.user_id,
            User.is_blocked.is_(False),
            User.role.notin_(["admin", "super_admin"]),
        )
    )
    if blocked_related_ids:
        users_query = users_query.filter(~User.user_id.in_(blocked_related_ids))

    if available_only:
        users_query = users_query.filter(User.availability.is_(True))
    if level_filter in SKILL_LEVELS:
        users_query = users_query.filter(
            or_(offered_row.level == level_filter, wanted_row.level == level_filter)
        )
    if category_filter in SKILL_CATEGORIES:
        users_query = users_query.filter(
            or_(offered_skill.category == category_filter, wanted_skill.category == category_filter)
        )

    if query_text:
        pattern = f"%{query_text}%"
        users_query = users_query.filter(
            or_(
                User.name.ilike(pattern),
                User.username.ilike(pattern),
                User.email.ilike(pattern),
                offered_skill.skill_name.ilike(pattern),
                offered_skill.category.ilike(pattern),
                wanted_skill.skill_name.ilike(pattern),
                wanted_skill.category.ilike(pattern),
            )
        )

    users = users_query.distinct().all()

    me = g.user
    my_offered = {s.skill_id for s in me.offered_skills}
    my_wanted = {s.skill_id for s in me.wanted_skills}
    my_offered_skill_names = {
        skill.skill_id: skill.skill_name
        for skill in me.offered_skills
    }
    my_offered_levels = {
        row.skill_id: row.level
        for row in UserSkillsOffered.query.filter_by(user_id=me.user_id).all()
    }
    my_wanted_levels = {
        row.skill_id: row.level
        for row in UserSkillsWanted.query.filter_by(user_id=me.user_id).all()
    }

    ranked_total = User.query.filter(
        User.is_blocked.is_(False),
        User.role.notin_(["admin", "super_admin"]),
    ).count()
    top_cards, _ = get_ranked_top_user_cards(offset=0, limit=max(ranked_total, 1))
    ranked_meta = {
        card["user"].user_id: {
            "rank_index": idx,
            "score": card["score"],
        }
        for idx, card in enumerate(top_cards)
    }

    result_rows = []
    search_text_lc = query_text.lower()
    for user in users:
        offered_details, wanted_details = build_skill_details(user.user_id)

        other_offered_levels = {
            item["skill"].skill_id: item["level"]
            for item in offered_details
            if item.get("skill") is not None
        }
        other_wanted_levels = {
            item["skill"].skill_id: item["level"]
            for item in wanted_details
            if item.get("skill") is not None
        }
        other_offered = set(other_offered_levels.keys())
        other_wanted = set(other_wanted_levels.keys())
        other_offered_skill_names = {
            item["skill"].skill_id: item["skill"].skill_name
            for item in offered_details
            if item.get("skill") is not None
        }

        they_can_teach_all = my_wanted.intersection(other_offered)
        i_can_teach = my_offered.intersection(other_wanted)

        user_match = bool(
            query_text
            and (
                search_text_lc in (user.name or "").lower()
                or search_text_lc in (user.username or "").lower()
                or search_text_lc in (user.email or "").lower()
            )
        )

        offered_skills = []
        offered_match_count = 0
        for item in offered_details:
            skill_name = item["skill"].skill_name
            category = item["skill"].category or "Uncategorized"
            level = item["level"]

            if level_filter in SKILL_LEVELS and level != level_filter:
                continue
            if category_filter in SKILL_CATEGORIES and category != category_filter:
                continue

            if query_text and (
                search_text_lc in skill_name.lower() or search_text_lc in category.lower()
            ):
                offered_match_count += 1

            offered_skills.append({"name": skill_name, "category": category, "level": level})

        wanted_skills = []
        wanted_match_count = 0
        for item in wanted_details:
            skill_name = item["skill"].skill_name
            category = item["skill"].category or "Uncategorized"
            level = item["level"]

            if level_filter in SKILL_LEVELS and level != level_filter:
                continue
            if category_filter in SKILL_CATEGORIES and category != category_filter:
                continue

            if query_text and (
                search_text_lc in skill_name.lower() or search_text_lc in category.lower()
            ):
                wanted_match_count += 1

            wanted_skills.append({"name": skill_name, "category": category, "level": level})

        requested_skill_ids = sorted(they_can_teach_all)
        offered_skill_ids = sorted(i_can_teach)
        best_requested_id = None
        best_offered_id = None
        best_level_points = -1

        if offered_skill_ids:
            for requested_id in requested_skill_ids:
                get_points = level_compatibility_points(
                    other_offered_levels.get(requested_id),
                    my_wanted_levels.get(requested_id),
                )
                for offered_id in offered_skill_ids:
                    give_points = level_compatibility_points(
                        my_offered_levels.get(offered_id),
                        other_wanted_levels.get(offered_id),
                    )
                    pair_points = int(round((get_points + give_points) / 2))
                    if pair_points > best_level_points:
                        best_level_points = pair_points
                        best_requested_id = requested_id
                        best_offered_id = offered_id
        else:
            for requested_id in requested_skill_ids:
                get_points = level_compatibility_points(
                    other_offered_levels.get(requested_id),
                    my_wanted_levels.get(requested_id),
                )
                if get_points > best_level_points:
                    best_level_points = get_points
                    best_requested_id = requested_id

        has_mutual_exchange = bool(requested_skill_ids and offered_skill_ids)
        skill_component = 70 if has_mutual_exchange else (40 if requested_skill_ids else 0)
        level_component = max(0, best_level_points)
        strength = max(0, min(100, int(round(skill_component + level_component))))

        rank_info = ranked_meta.get(user.user_id, {"rank_index": 10**9, "score": 0})

        query_relevance = 0
        if query_text:
            if search_text_lc in (user.username or "").lower():
                query_relevance += 7
            if search_text_lc in (user.name or "").lower():
                query_relevance += 5
            if search_text_lc in (user.email or "").lower():
                query_relevance += 2
            query_relevance += min(6, offered_match_count + wanted_match_count)

        offered_options = [
            {"id": sid, "name": my_offered_skill_names.get(sid, f"Skill #{sid}")}
            for sid in offered_skill_ids
        ]
        requested_options = [
            {"id": sid, "name": other_offered_skill_names.get(sid, f"Skill #{sid}")}
            for sid in requested_skill_ids
        ]

        result_rows.append(
            {
                "user": user,
                "offered_skills": offered_skills,
                "wanted_skills": wanted_skills,
                "avg_rating": user.average_rating(),
                "trust": compute_user_trust_metrics(user),
                "strength": strength,
                "location": user.location,
                "request_enabled": has_mutual_exchange and bool(best_requested_id and best_offered_id),
                "default_requested_skill_id": best_requested_id,
                "default_offered_skill_id": best_offered_id,
                "query_relevance": query_relevance,
                "default_score": rank_info["score"],
                "rank_index": rank_info["rank_index"],
                "offered_options": offered_options,
                "requested_options": requested_options,
            }
        )

    if query_text:
        result_rows.sort(
            key=lambda row: (
                row["query_relevance"],
                row["strength"],
                row["default_score"],
                row["avg_rating"] or 0,
            ),
            reverse=True,
        )
    else:
        result_rows.sort(key=lambda row: row["rank_index"])

    initial_rows = [
        {
            "name": row["user"].name,
            "username": row["user"].username,
            "location": row["user"].location,
            "availability_label": row["user"].availability_label,
            "profile_image": url_for("static", filename=row["user"].profile_image_path),
            "profile_url": url_for("user_profile", username=row["user"].username),
            "message_url": url_for("messages_thread", username=row["user"].username),
            "request_url": url_for("send_request_to_user", user_id=row["user"].user_id),
            "offered_skills": row["offered_skills"],
            "wanted_skills": row["wanted_skills"],
            "avg_rating": row["avg_rating"],
            "strength": row["strength"],
            "request_enabled": row["request_enabled"],
            "default_requested_skill_id": row["default_requested_skill_id"],
            "default_offered_skill_id": row["default_offered_skill_id"],
            "offered_options": row["offered_options"],
            "requested_options": row["requested_options"],
        }
        for row in result_rows
    ]

    if is_ajax:
        return jsonify(
            {
                "results": [
                    {
                        "name": row["user"].name,
                        "username": row["user"].username,
                        "location": row["user"].location,
                        "availability_label": row["user"].availability_label,
                        "profile_image": url_for("static", filename=row["user"].profile_image_path),
                        "profile_url": url_for("user_profile", username=row["user"].username),
                        "message_url": url_for("messages_thread", username=row["user"].username),
                        "request_url": url_for("send_request_to_user", user_id=row["user"].user_id),
                        "offered_skills": row["offered_skills"],
                        "wanted_skills": row["wanted_skills"],
                        "avg_rating": row["avg_rating"],
                        "strength": row["strength"],
                        "request_enabled": row["request_enabled"],
                        "default_requested_skill_id": row["default_requested_skill_id"],
                        "default_offered_skill_id": row["default_offered_skill_id"],
                        "offered_options": row["offered_options"],
                        "requested_options": row["requested_options"],
                    }
                    for row in result_rows
                ],
                "count": len(result_rows),
            }
        )

    featured_categories = SKILL_CATEGORIES[:10]
    if category_filter and category_filter in SKILL_CATEGORIES and category_filter not in featured_categories:
        featured_categories = [category_filter] + featured_categories[:9]

    return render_template(
        "user/search.html",
        result_rows=result_rows,
        query_text=query_text,
        available_only=available_only,
        level_filter=level_filter,
        category_filter=category_filter,
        skill_levels=SKILL_LEVELS,
        filter_categories=featured_categories,
        has_active_search=bool(query_text or available_only or level_filter or category_filter),
        initial_rows=initial_rows,
    )


def get_admin_user_insights():
    insight_rows = db.session.execute(
        text(
            """
            SELECT u.user_id,
                   COUNT(r.request_id) AS total_requests,
                   COALESCE(SUM(CASE WHEN r.status = 'completed' THEN 1 ELSE 0 END), 0) AS completed_requests
            FROM users u
            LEFT JOIN requests r
              ON (r.sender_id = u.user_id OR r.receiver_id = u.user_id)
            GROUP BY u.user_id
            """
        )
    ).fetchall()
    return {
        row[0]: {"total": int(row[1] or 0), "completed": int(row[2] or 0)}
        for row in insight_rows
    }


def parse_report_attachments(raw_attachments):
    if not raw_attachments:
        return []
    try:
        parsed = json.loads(raw_attachments)
        if isinstance(parsed, list):
            return [str(item) for item in parsed if item]
    except json.JSONDecodeError:
        pass
    return []


def get_admin_table_limit():
    try:
        return max(50, min(int(app.config.get("ADMIN_TABLE_LIMIT", 200)), 500))
    except (TypeError, ValueError):
        return 200


def _admin_active_user_window_seconds():
    try:
        configured = int(os.getenv("ADMIN_ACTIVE_USER_WINDOW_SECONDS", "300"))
    except (TypeError, ValueError):
        configured = 300
    return max(60, min(configured, 3600))


def _admin_ongoing_exchanges_count():
    # Prefer the dedicated exchanges table when available; otherwise use in-progress request states.
    try:
        table_names = inspect(db.engine).get_table_names()
        if "exchanges" in table_names:
            return int(
                db.session.execute(
                    text("SELECT COUNT(*) FROM exchanges WHERE status = :status"),
                    {"status": "ongoing"},
                ).scalar()
                or 0
            )
    except Exception:
        pass
    return Request.query.filter(Request.status.in_(["accepted", "awaiting_confirmation"])).count()


def get_admin_dashboard_metrics(now=None):
    now = now or datetime.utcnow()
    active_threshold = now - timedelta(seconds=_admin_active_user_window_seconds())

    total_users = User.query.count()
    active_users = (
        db.session.query(UserSession.user_id)
        .filter(
            UserSession.is_active.is_(True),
            UserSession.last_active >= active_threshold,
        )
        .distinct()
        .count()
    )
    pending_requests = Request.query.filter(Request.status.in_(["pending", "countered"])).count()
    ongoing_exchanges = _admin_ongoing_exchanges_count()
    completed_exchanges = Request.query.filter_by(status="completed").count()
    pending_reports = UserReport.query.filter_by(status="pending").count()

    return {
        "total_users": total_users,
        "active_users": active_users,
        "pending_requests": pending_requests,
        "ongoing_exchanges": ongoing_exchanges,
        "completed_exchanges": completed_exchanges,
        "pending_reports": pending_reports,
        "active_user_window_seconds": _admin_active_user_window_seconds(),
    }


def build_admin_analytics_data(range_days=14):
    now = datetime.utcnow()
    today = now.date()
    try:
        range_days = int(range_days)
    except (TypeError, ValueError):
        range_days = 14
    range_days = max(1, min(range_days, 1825))

    start_date = today - timedelta(days=range_days - 1)
    start_dt = datetime.combine(start_date, datetime.min.time())
    end_dt = datetime.combine(today + timedelta(days=1), datetime.min.time())

    def daily_count_map(model, dt_column, extra_filters=None):
        query = (
            db.session.query(db.func.date(dt_column).label("bucket"), db.func.count())
            .filter(dt_column >= start_dt, dt_column < end_dt)
        )
        if extra_filters:
            query = query.filter(*extra_filters)
        rows = query.group_by(db.func.date(dt_column)).all()
        result = {}
        for bucket, cnt in rows:
            if bucket is None:
                continue
            if hasattr(bucket, "strftime"):
                key = bucket.strftime("%Y-%m-%d")
            else:
                key = str(bucket)
            result[key] = int(cnt or 0)
        return result

    def compress_series(labels, values, mode="sum"):
        if range_days <= 60:
            return labels, values
        chunk = 7 if range_days <= 365 else 30
        new_labels = []
        new_values = []
        for idx in range(0, len(values), chunk):
            segment = values[idx:idx + chunk]
            if not segment:
                continue
            new_labels.append(labels[min(idx + len(segment) - 1, len(labels) - 1)])
            if mode == "last":
                new_values.append(segment[-1])
            else:
                new_values.append(sum(segment))
        return new_labels, new_values

    date_points = [start_date + timedelta(days=offset) for offset in range(range_days)]
    daily_labels = [day.strftime("%d %b") for day in date_points]
    date_keys = [day.strftime("%Y-%m-%d") for day in date_points]

    total_users = User.query.count()
    total_requests = Request.query.count()
    pending_requests = Request.query.filter(Request.status.in_(["pending", "countered"])).count()
    accepted_requests = Request.query.filter(Request.status.in_(["accepted", "awaiting_confirmation"])).count()
    completed_exchanges = Request.query.filter_by(status="completed").count()
    terminated_sessions = Request.query.filter_by(status="terminated").count()
    rejected_requests = Request.query.filter_by(status="rejected").count()
    total_reports = UserReport.query.count()
    total_skills_listed = UserSkillsOffered.query.count() + UserSkillsWanted.query.count()
    total_exchanges = accepted_requests + completed_exchanges + terminated_sessions

    active_window = now - timedelta(minutes=3)
    active_sessions = Request.query.filter(
        Request.status.in_(["accepted", "awaiting_confirmation"]),
        Request.session_started_at.isnot(None),
        Request.session_completed_at.is_(None),
        Request.session_link.isnot(None),
    ).count()
    active_users = (
        db.session.query(Request.sender_id)
        .filter(
            Request.session_started_at.isnot(None),
            Request.session_completed_at.is_(None),
            Request.session_sender_last_ping_at >= active_window,
        )
        .union(
            db.session.query(Request.receiver_id).filter(
                Request.session_started_at.isnot(None),
                Request.session_completed_at.is_(None),
                Request.session_receiver_last_ping_at >= active_window,
            )
        )
        .count()
    )

    requests_daily_map = daily_count_map(Request, Request.created_at)
    reports_daily_map = daily_count_map(UserReport, UserReport.created_at)
    exchanges_created_daily_map = daily_count_map(
        Request,
        Request.created_at,
        extra_filters=[Request.status.in_(["accepted", "awaiting_confirmation", "completed", "terminated"])],
    )
    exchanges_completed_daily_map = daily_count_map(
        Request,
        Request.updated_at,
        extra_filters=[Request.status == "completed"],
    )
    users_new_daily_map = daily_count_map(User, User.created_at)

    requests_daily_values = [requests_daily_map.get(key, 0) for key in date_keys]
    reports_daily_values = [reports_daily_map.get(key, 0) for key in date_keys]
    exchanges_created_daily_values = [exchanges_created_daily_map.get(key, 0) for key in date_keys]
    exchanges_completed_daily_values = [exchanges_completed_daily_map.get(key, 0) for key in date_keys]
    user_growth_daily_values = [users_new_daily_map.get(key, 0) for key in date_keys]

    users_before_range = User.query.filter(User.created_at < start_dt).count()
    cumulative_users_values = []
    running_total = users_before_range
    for value in user_growth_daily_values:
        running_total += value
        cumulative_users_values.append(running_total)

    requests_trend_labels, requests_trend_values = compress_series(daily_labels, requests_daily_values, mode="sum")
    reports_trend_labels, reports_trend_values = compress_series(daily_labels, reports_daily_values, mode="sum")
    _, exchange_created_values = compress_series(daily_labels, exchanges_created_daily_values, mode="sum")
    _, exchange_completed_values = compress_series(daily_labels, exchanges_completed_daily_values, mode="sum")
    user_growth_labels, user_growth_values = compress_series(daily_labels, user_growth_daily_values, mode="sum")
    _, user_growth_cumulative_values = compress_series(daily_labels, cumulative_users_values, mode="last")

    offered_counts = dict(
        db.session.query(Skill.skill_name, db.func.count(UserSkillsOffered.id))
        .join(UserSkillsOffered, Skill.skill_id == UserSkillsOffered.skill_id)
        .group_by(Skill.skill_id)
        .all()
    )
    wanted_counts = dict(
        db.session.query(Skill.skill_name, db.func.count(UserSkillsWanted.id))
        .join(UserSkillsWanted, Skill.skill_id == UserSkillsWanted.skill_id)
        .group_by(Skill.skill_id)
        .all()
    )
    all_skill_names = set(offered_counts.keys()) | set(wanted_counts.keys())
    sorted_skills = sorted(
        all_skill_names,
        key=lambda name: (wanted_counts.get(name, 0), offered_counts.get(name, 0)),
        reverse=True,
    )[:10]
    skill_compare_labels = sorted_skills
    skill_supply_values = [int(offered_counts.get(name, 0)) for name in sorted_skills]
    skill_demand_values = [int(wanted_counts.get(name, 0)) for name in sorted_skills]

    request_status_distribution = [
        pending_requests,
        accepted_requests,
        completed_exchanges,
        rejected_requests,
    ]

    weekday_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    weekday_activity = [0, 0, 0, 0, 0, 0, 0]
    for index, _ in enumerate(date_keys):
        weekday = date_points[index].weekday()
        weekday_activity[weekday] += requests_daily_values[index]

    metric_cards = [
        {"key": "total_users", "label": "Total Users", "value": total_users},
        {"key": "total_requests", "label": "Total Requests", "value": total_requests},
        {"key": "completed_exchanges", "label": "Completed Exchanges", "value": completed_exchanges},
        {"key": "active_users", "label": "Active Users", "value": active_users},
        {"key": "pending_requests", "label": "Pending Requests", "value": pending_requests},
        {"key": "active_sessions", "label": "Active Sessions", "value": active_sessions},
        {"key": "total_reports", "label": "Total Reports", "value": total_reports},
        {"key": "total_skills_listed", "label": "Total Skills Listed", "value": total_skills_listed},
    ]

    return {
        "range_days": range_days,
        "total_users": total_users,
        "total_requests": total_requests,
        "pending_requests": pending_requests,
        "accepted_requests": accepted_requests,
        "completed_exchanges": completed_exchanges,
        "rejected_requests": rejected_requests,
        "terminated_sessions": terminated_sessions,
        "total_exchanges": total_exchanges,
        "total_reports": total_reports,
        "total_skills_listed": total_skills_listed,
        "active_users": active_users,
        "active_sessions": active_sessions,
        "requests_trend_labels": requests_trend_labels,
        "requests_trend_values": requests_trend_values,
        "reports_trend_labels": reports_trend_labels,
        "reports_trend_values": reports_trend_values,
        "exchange_created_values": exchange_created_values,
        "exchange_completed_values": exchange_completed_values,
        "user_growth_labels": user_growth_labels,
        "user_growth_values": user_growth_values,
        "user_growth_cumulative_values": user_growth_cumulative_values,
        "skill_compare_labels": skill_compare_labels,
        "skill_supply_values": skill_supply_values,
        "skill_demand_values": skill_demand_values,
        "request_status_distribution_labels": ["Pending", "Accepted", "Completed", "Rejected"],
        "request_status_distribution_values": request_status_distribution,
        "activity_weekday_labels": weekday_labels,
        "activity_weekday_values": weekday_activity,
        "metric_cards": metric_cards,
    }


def build_admin_dashboard_data(range_days=14):
    now = datetime.utcnow()
    today = now.date()
    try:
        range_days = int(range_days)
    except (TypeError, ValueError):
        range_days = 14
    range_days = max(1, min(range_days, 365))

    metrics = get_admin_dashboard_metrics(now=now)
    total_users = metrics["total_users"]
    active_users = metrics["active_users"]
    pending_requests = metrics["pending_requests"]
    completed_exchanges = metrics["completed_exchanges"]
    pending_reports = metrics["pending_reports"]
    ongoing_exchanges = metrics["ongoing_exchanges"]

    requests_trend_labels = []
    requests_trend_values = []
    reports_trend_labels = []
    reports_trend_values = []
    for offset in range(range_days - 1, -1, -1):
        day = today - timedelta(days=offset)
        next_day = day + timedelta(days=1)
        start_dt = datetime.combine(day, datetime.min.time())
        end_dt = datetime.combine(next_day, datetime.min.time())

        req_count = Request.query.filter(Request.created_at >= start_dt, Request.created_at < end_dt).count()
        rep_count = UserReport.query.filter(
            UserReport.created_at >= start_dt,
            UserReport.created_at < end_dt,
        ).count()

        requests_trend_labels.append(day.strftime("%d %b"))
        requests_trend_values.append(req_count)
        reports_trend_labels.append(day.strftime("%d %b"))
        reports_trend_values.append(rep_count)

    recent_activity_rows, _ = build_admin_activity_logs(
        limit=8,
        offset=0,
        event_filter="all",
        severity_filter="all",
    )

    return {
        "total_users": total_users,
        "active_users": active_users,
        "pending_requests": pending_requests,
        "completed_exchanges": completed_exchanges,
        "pending_reports": pending_reports,
        "ongoing_exchanges": ongoing_exchanges,
        "requests_trend_labels": requests_trend_labels,
        "requests_trend_values": requests_trend_values,
        "reports_trend_labels": reports_trend_labels,
        "reports_trend_values": reports_trend_values,
        "recent_activity_rows": [serialize_activity_log_row(row) for row in recent_activity_rows],
        "range_days": range_days,
        "active_user_window_seconds": metrics["active_user_window_seconds"],
    }


def build_admin_activity_logs(limit=30, offset=0, event_filter="all", severity_filter="all"):
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 30
    try:
        offset = int(offset)
    except (TypeError, ValueError):
        offset = 0

    limit = max(10, min(limit, 100))
    offset = max(0, offset)
    event_filter = (event_filter or "all").strip().lower()
    severity_filter = (severity_filter or "all").strip().lower()

    target_size = offset + limit
    scan_limit = max(250, target_size * 2)
    max_scan_limit = 5000

    def include_event(event_key):
        return event_filter == "all" or event_filter == event_key

    while scan_limit <= max_scan_limit:
        logs = []

        if include_event("user_registration"):
            for u in User.query.order_by(User.created_at.desc()).limit(scan_limit).all():
                logs.append({
                    "at": u.created_at,
                    "event": "User Registration",
                    "event_key": "user_registration",
                    "actor": f"@{u.username}" if u.username else "Unknown",
                    "details": f"@{u.username} joined the platform",
                    "severity": "info",
                })

        if include_event("request_update"):
            for req in Request.query.order_by(Request.updated_at.desc()).limit(scan_limit).all():
                sender_username = req.sender.username if req.sender and req.sender.username else None
                receiver_username = req.receiver.username if req.receiver and req.receiver.username else None
                sender_actor = f"@{sender_username}" if sender_username else "Unknown"
                receiver_actor = f"@{receiver_username}" if receiver_username else "Unknown"
                logs.append({
                    "at": req.updated_at,
                    "event": "Request Update",
                    "event_key": "request_update",
                    "actor": f"{sender_actor} -> {receiver_actor}",
                    "actor_from": sender_actor,
                    "actor_to": receiver_actor,
                    "details": f"Status changed to {req.status.replace('_', ' ').title()}",
                    "severity": "success" if req.status == "completed" else "info",
                })

        if include_event("user_report"):
            for rep in UserReport.query.order_by(UserReport.created_at.desc()).limit(scan_limit).all():
                reporter_username = rep.reporter.username if rep.reporter and rep.reporter.username else None
                target_username = rep.reported_user.username if rep.reported_user and rep.reported_user.username else None
                reporter = f"@{reporter_username}" if reporter_username else "Unknown"
                target = f"@{target_username}" if target_username else "Unknown"
                logs.append({
                    "at": rep.created_at,
                    "event": "User Report",
                    "event_key": "user_report",
                    "actor": reporter,
                    "details": f"Reported {target} for {rep.reason}",
                    "severity": "warning" if rep.status != "resolved" else "info",
                })

        if include_event("feedback"):
            feedback_rows = (
                db.session.query(Rating, User.username)
                .join(User, Rating.from_user == User.user_id)
                .order_by(Rating.created_at.desc())
                .limit(scan_limit)
                .all()
            )
            for rating, username in feedback_rows:
                logs.append({
                    "at": rating.created_at,
                    "event": "Feedback",
                    "event_key": "feedback",
                    "actor": f"@{username}" if username else "Unknown",
                    "details": f"Submitted {rating.rating}/5 rating",
                    "severity": "warning" if rating.rating <= 2 else "info",
                })

        logs.sort(key=lambda item: item["at"] or datetime.min, reverse=True)

        if severity_filter != "all":
            logs = [row for row in logs if row["severity"] == severity_filter]

        if len(logs) >= target_size or scan_limit == max_scan_limit:
            chunk = logs[offset:offset + limit]
            has_more = len(logs) > offset + limit
            return chunk, has_more

        scan_limit = min(scan_limit + 500, max_scan_limit)

    return [], False


def serialize_activity_log_row(row):
    log_time = row.get("at")
    return {
        "time": log_time.strftime("%Y-%m-%d %H:%M") if log_time else "-",
        "event": row.get("event", "-"),
        "event_key": row.get("event_key", ""),
        "actor": row.get("actor", "-"),
        "actor_from": row.get("actor_from", ""),
        "actor_to": row.get("actor_to", ""),
        "details": row.get("details", "-"),
        "severity": row.get("severity", "info"),
    }


def _format_export_datetime(value):
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


def _csv_download_response(filename, headers, rows):
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)

    response = app.response_class(stream.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return response


def _json_download_response(filename, headers, rows):
    payload = [dict(zip(headers, row)) for row in rows]
    response = app.response_class(
        json.dumps(payload, ensure_ascii=False, indent=2),
        mimetype="application/json",
    )
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return response


def _xlsx_download_response(filename, headers, rows):
    workbook_class = get_workbook_class()
    if workbook_class is None:
        abort(400, description="XLSX export requires openpyxl.")

    wb = workbook_class()
    ws = wb.active
    ws.title = "Export"
    ws.append(headers)
    for row in rows:
        ws.append(row)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    response = app.response_class(
        output.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return response


def _dataset_download_response(dataset_name, headers, rows, fmt):
    export_format = (fmt or "csv").strip().lower()
    if export_format == "json":
        return _json_download_response(f"{dataset_name}.json", headers, rows)
    if export_format == "xlsx":
        return _xlsx_download_response(f"{dataset_name}.xlsx", headers, rows)
    return _csv_download_response(f"{dataset_name}.csv", headers, rows)


def _collect_activity_logs_for_export(max_rows=5000):
    logs = []
    offset = 0
    limit = 100
    while len(logs) < max_rows:
        chunk, has_more = build_admin_activity_logs(
            limit=limit,
            offset=offset,
            event_filter="all",
            severity_filter="all",
        )
        if not chunk:
            break
        logs.extend(chunk)
        offset += len(chunk)
        if not has_more:
            break
    return logs[:max_rows]


@app.route("/admin")
@app.route("/admin/dashboard")
@login_required
@admin_required
def admin_dashboard():
    requested_range = request.args.get("range", 14)
    try:
        range_days = int(requested_range)
    except (TypeError, ValueError):
        range_days = 14
    range_days = max(1, min(range_days, 365))

    dashboard_data = build_admin_dashboard_data(range_days=range_days)
    return render_template(
        "admin/dashboard.html",
        admin_page_title="Dashboard",
        **dashboard_data,
    )


@app.route("/admin/analytics")
@login_required
@admin_required
def admin_analytics_page():
    requested_range = request.args.get("range", 14)
    try:
        range_days = int(requested_range)
    except (TypeError, ValueError):
        range_days = 14
    range_days = max(1, min(range_days, 1825))

    analytics = build_admin_analytics_data(range_days=range_days)
    range_options = [
        (1, "1 Day"),
        (7, "7 Days"),
        (14, "14 Days"),
        (30, "1 Month"),
        (90, "3 Months"),
        (180, "6 Months"),
        (365, "1 Year"),
        (1095, "3 Years"),
        (1825, "5 Years"),
    ]
    return render_template(
        "admin/analytics.html",
        admin_page_title="Analytics",
        range_options=range_options,
        selected_range=range_days,
        **analytics,
    )


@app.route("/admin/activity-logs")
@login_required
@admin_required
def admin_activity_logs_page():
    event_filter = request.args.get("event", "all").strip().lower()
    severity_filter = request.args.get("severity", "all").strip().lower()

    return render_template(
        "admin/activity_logs.html",
        admin_page_title="Activity Logs",
        event_filter=event_filter,
        severity_filter=severity_filter,
    )


@app.route("/admin/activity-logs/data")
@login_required
@admin_required
def admin_activity_logs_data():
    event_filter = request.args.get("event", "all").strip().lower()
    severity_filter = request.args.get("severity", "all").strip().lower()
    offset = request.args.get("offset", 0)
    limit = request.args.get("limit", 25)

    logs, has_more = build_admin_activity_logs(
        limit=limit,
        offset=offset,
        event_filter=event_filter,
        severity_filter=severity_filter,
    )

    return jsonify(
        {
            "rows": [serialize_activity_log_row(row) for row in logs],
            "has_more": bool(has_more),
        }
    )


@app.route("/admin/activity-logs/export")
@login_required
@admin_required
def admin_activity_logs_export():
    return redirect(url_for("admin_settings_export", dataset="activity_logs", format="csv"))


@app.route("/admin/settings/exports/<string:dataset_key>")
@login_required
@admin_required
@super_admin_required
def admin_settings_export_dataset(dataset_key):
    export_format = request.args.get("format", "csv")
    dataset_name, headers, rows = _build_settings_export_payload(dataset_key)
    return _dataset_download_response(dataset_name, headers, rows, export_format)


def _build_settings_export_payload(dataset_key):
    dataset = (dataset_key or "").strip().lower()
    max_rows = 10000

    if dataset == "users":
        users = User.query.order_by(User.created_at.desc(), User.user_id.desc()).limit(max_rows).all()
        rows = [
            [
                user.user_id,
                user.name,
                user.username,
                user.email,
                user.role,
                "Blocked" if user.is_blocked else "Active",
                user.availability_label,
                _format_export_datetime(user.created_at),
            ]
            for user in users
        ]
        return (
            "users-export",
            ["User ID", "Name", "Username", "Email", "Role", "Account Status", "Availability", "Created At"],
            rows,
        )

    if dataset == "skills":
        skills = Skill.query.order_by(Skill.skill_name.asc()).limit(max_rows).all()
        rows = [
            [
                skill.skill_id,
                skill.skill_name,
                skill.category_name,
                skill.status,
                skill.description or "",
            ]
            for skill in skills
        ]
        return (
            "skills-export",
            ["Skill ID", "Skill Name", "Category", "Status", "Description"],
            rows,
        )

    if dataset == "exchanges":
        requests = Request.query.order_by(Request.updated_at.desc(), Request.request_id.desc()).limit(max_rows).all()
        rows = []
        for req in requests:
            sender_name = req.sender.name if req.sender else "Unknown"
            receiver_name = req.receiver.name if req.receiver else "Unknown"
            final_offered = req.final_offered_skill.skill_name if req.final_offered_skill else ""
            final_requested = req.final_requested_skill.skill_name if req.final_requested_skill else ""
            rows.append(
                [
                    req.request_id,
                    sender_name,
                    receiver_name,
                    req.offered_skill.skill_name if req.offered_skill else "",
                    req.requested_skill.skill_name if req.requested_skill else "",
                    final_offered,
                    final_requested,
                    req.status,
                    _format_export_datetime(req.created_at),
                    _format_export_datetime(req.updated_at),
                ]
            )
        return (
            "exchanges-export",
            [
                "Request ID",
                "Sender",
                "Receiver",
                "Offered Skill",
                "Requested Skill",
                "Final Offered Skill",
                "Final Requested Skill",
                "Status",
                "Created At",
                "Updated At",
            ],
            rows,
        )

    if dataset == "reports":
        reports = UserReport.query.order_by(UserReport.created_at.desc(), UserReport.report_id.desc()).limit(max_rows).all()
        rows = []
        for report in reports:
            reporter = report.reporter.name if report.reporter else "Unknown"
            reported = report.reported_user.name if report.reported_user else "Unknown"
            rows.append(
                [
                    report.report_id,
                    reporter,
                    reported,
                    report.reason,
                    report.status,
                    report.description or "",
                    _format_export_datetime(report.created_at),
                ]
            )
        return (
            "reports-export",
            ["Report ID", "Reporter", "Reported User", "Reason", "Status", "Description", "Created At"],
            rows,
        )

    if dataset == "feedback":
        feedback_rows = Rating.query.order_by(Rating.created_at.desc(), Rating.rating_id.desc()).limit(max_rows).all()
        user_ids = {row.from_user for row in feedback_rows} | {row.to_user for row in feedback_rows}
        user_map = {user.user_id: user.name for user in User.query.filter(User.user_id.in_(user_ids)).all()} if user_ids else {}
        rows = []
        for feedback in feedback_rows:
            rows.append(
                [
                    feedback.rating_id,
                    user_map.get(feedback.from_user, f"User #{feedback.from_user}"),
                    user_map.get(feedback.to_user, f"User #{feedback.to_user}"),
                    feedback.rating,
                    feedback.feedback or "",
                    feedback.exchange_request_id or "",
                    _format_export_datetime(feedback.created_at),
                ]
            )
        return (
            "user-feedback-export",
            ["Feedback ID", "From User", "To User", "Rating", "Feedback", "Exchange Request ID", "Created At"],
            rows,
        )

    if dataset == "sessions":
        session_requests = Request.query.filter(
            or_(
                Request.session_started_at.isnot(None),
                Request.status.in_(["completed", "terminated"]),
            )
        ).order_by(Request.updated_at.desc(), Request.request_id.desc()).limit(max_rows).all()
        rows = []
        for req in session_requests:
            sender_name = req.sender.name if req.sender else "Unknown"
            receiver_name = req.receiver.name if req.receiver else "Unknown"
            offered_skill = req.final_offered_skill or req.offered_skill
            requested_skill = req.final_requested_skill or req.requested_skill

            if req.status == "completed":
                session_status = "Completed"
            elif req.status == "terminated":
                session_status = "Terminated"
            else:
                session_status = "In Progress"

            rows.append(
                [
                    req.request_id,
                    f"{sender_name} / {receiver_name}",
                    f"{offered_skill.skill_name if offered_skill else ''} -> {requested_skill.skill_name if requested_skill else ''}",
                    _format_export_datetime(req.session_started_at),
                    _format_export_datetime(req.session_completed_at),
                    session_status,
                    req.session_link or "",
                ]
            )
        return (
            "sessions-export",
            [
                "Session ID",
                "Participants",
                "Skills Exchanged",
                "Start Time",
                "End Time",
                "Status",
                "Meeting Link",
            ],
            rows,
        )

    if dataset == "activity_logs":
        logs = _collect_activity_logs_for_export(max_rows=5000)
        rows = []
        for row in logs:
            serialized = serialize_activity_log_row(row)
            rows.append(
                [
                    serialized["time"],
                    serialized["event"],
                    serialized["actor"],
                    serialized["details"],
                    serialized["severity"].title(),
                ]
            )
        return (
            "activity-logs-export",
            ["Timestamp", "Event", "Actor", "Details", "Severity"],
            rows,
        )

    abort(404)


@app.route("/admin/settings/export")
@login_required
@admin_required
@super_admin_required
def admin_settings_export():
    dataset = request.args.get("dataset", "users").strip().lower()
    export_format = request.args.get("format", "csv").strip().lower()
    dataset_name, headers, rows = _build_settings_export_payload(dataset)
    return _dataset_download_response(dataset_name, headers, rows, export_format)


@app.route("/admin/alerts")
@login_required
@admin_required
def admin_alerts_page():
    now = datetime.utcnow()
    analytics = build_admin_analytics_data()
    pending_reports = UserReport.query.filter_by(status="pending").count()
    reviewing_reports = UserReport.query.filter_by(status="reviewing").count()
    reports_today = UserReport.query.filter(
        UserReport.created_at >= datetime.combine(now.date(), datetime.min.time())
    ).count()
    blocked_users = User.query.filter_by(is_blocked=True).count()
    low_ratings_week = Rating.query.filter(
        Rating.created_at >= (now - timedelta(days=7)),
        Rating.rating <= 2,
    ).count()

    alerts = []
    if pending_reports:
        alerts.append({
            "severity": "critical",
            "title": "Pending moderation queue",
            "message": f"{pending_reports} report(s) are waiting for admin action.",
        })
    if reviewing_reports:
        alerts.append({
            "severity": "high",
            "title": "Reports under review",
            "message": f"{reviewing_reports} report(s) are marked as reviewing.",
        })
    if reports_today >= 5:
        alerts.append({
            "severity": "high",
            "title": "Reports spike detected",
            "message": f"{reports_today} reports were submitted today.",
        })
    if low_ratings_week >= 5:
        alerts.append({
            "severity": "medium",
            "title": "Low feedback trend",
            "message": f"{low_ratings_week} low ratings were posted in the last 7 days.",
        })
    if blocked_users >= 3:
        alerts.append({
            "severity": "medium",
            "title": "Blocked users count",
            "message": f"{blocked_users} account(s) are currently blocked.",
        })
    if not alerts:
        alerts.append({
            "severity": "info",
            "title": "No critical alerts",
            "message": "All monitored indicators are currently stable.",
        })

    return render_template(
        "admin/alerts.html",
        admin_page_title="Alerts",
        alerts=alerts,
        analytics=analytics,
    )


@app.route("/admin/feedback")
@login_required
@admin_required
def admin_feedback_page():
    page = request.args.get("page", 1, type=int)
    per_page = 20
    q = request.args.get("q", "").strip()
    rating_filter = request.args.get("rating", "all").strip().lower()
    from_user = aliased(User)
    to_user = aliased(User)

    feedback_query = (
        db.session.query(
            Rating,
            from_user.username.label("from_username"),
            to_user.username.label("to_username"),
            from_user.profile_image.label("from_profile_image"),
            to_user.profile_image.label("to_profile_image"),
        )
        .join(from_user, Rating.from_user == from_user.user_id)
        .join(to_user, Rating.to_user == to_user.user_id)
    )

    if q:
        like_q = f"%{q}%"
        feedback_query = feedback_query.filter(
            or_(
                from_user.username.ilike(like_q),
                to_user.username.ilike(like_q),
                from_user.name.ilike(like_q),
                to_user.name.ilike(like_q),
                Rating.feedback.ilike(like_q),
            )
        )

    if rating_filter == "low":
        feedback_query = feedback_query.filter(Rating.rating <= 2)
    elif rating_filter == "mid":
        feedback_query = feedback_query.filter(Rating.rating == 3)
    elif rating_filter == "high":
        feedback_query = feedback_query.filter(Rating.rating >= 4)

    feedback_pagination = feedback_query.order_by(Rating.created_at.desc(), Rating.rating_id.desc()).paginate(
        page=page,
        per_page=per_page,
        error_out=False,
    )
    feedback_rows = feedback_pagination.items

    def highlight_text(value, term):
        text_value = "" if value is None else str(value)
        needle = (term or "").strip()
        if not needle:
            return escape(text_value)
        pattern = re.compile(re.escape(needle), re.IGNORECASE)
        if not pattern.search(text_value):
            return escape(text_value)

        pieces = []
        last_index = 0
        for match in pattern.finditer(text_value):
            pieces.append(escape(text_value[last_index:match.start()]))
            pieces.append(Markup("<mark class='search-hit'>") + escape(match.group(0)) + Markup("</mark>"))
            last_index = match.end()
        pieces.append(escape(text_value[last_index:]))
        return Markup("").join(pieces)

    return render_template(
        "admin/feedback.html",
        admin_page_title="User Feedbacks",
        feedback_rows=feedback_rows,
        rating_filter=rating_filter,
        q=q,
        highlight_text=highlight_text,
        pagination=feedback_pagination,
    )


@app.route("/admin/feedback/<int:rating_id>/delete", methods=["POST"])
@login_required
@admin_required
def admin_delete_feedback(rating_id):
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    rating = Rating.query.get_or_404(rating_id)
    db.session.delete(rating)
    db.session.commit()

    if is_ajax:
        return jsonify({"ok": True, "message": "Review deleted."})

    flash("Review deleted.", "success")
    return redirect(request.referrer or url_for("admin_feedback_page"))


@app.route("/admin/settings", methods=["GET", "POST"])
@login_required
@admin_required
@super_admin_required
def admin_settings_page():
    load_dynamic_settings_from_db()
    if request.method == "POST":
        return redirect(url_for("admin_settings_page"))

    return render_template(
        "admin/settings.html",
        admin_page_title="Settings",
        allow_new_registrations=app.config.get("ALLOW_NEW_REGISTRATIONS", True),
        allow_user_reports=app.config.get("ALLOW_USER_REPORTS", True),
        allow_session_creation=app.config.get("ALLOW_SESSION_CREATION", True),
        auto_expire_inactive_sessions=app.config.get("AUTO_EXPIRE_INACTIVE_SESSIONS", True),
        allow_rating_after_session=app.config.get("ALLOW_RATING_AFTER_SESSION", True),
        require_feedback_submission=app.config.get("REQUIRE_FEEDBACK_SUBMISSION", False),
    )


@app.route("/admin/settings/update", methods=["POST"])
@login_required
@admin_required
@super_admin_required
def admin_settings_update():
    payload = request.get_json(silent=True) or {}
    key = (payload.get("key") or "").strip()
    value = payload.get("value")

    if key in DYNAMIC_BOOLEAN_SETTING_KEYS:
        try:
            persisted_value = persist_dynamic_setting(key, value)
        except ValueError:
            return jsonify({"ok": False, "message": "Unsupported setting key."}), 400
        except Exception:
            db.session.rollback()
            return jsonify({"ok": False, "message": "Unable to save setting."}), 500

        return jsonify({"ok": True, "key": key, "value": persisted_value})

    return jsonify({"ok": False, "message": "Unsupported setting key."}), 400


@app.route("/admin/users")
@login_required
@admin_required
def admin_users_page():
    page = request.args.get("page", 1, type=int)
    per_page = 20
    q = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "all").strip().lower()
    role_filter = request.args.get("role", "all").strip().lower()

    users_query = User.query.options(
        load_only(
            User.user_id,
            User.name,
            User.username,
            User.email,
            User.role,
            User.is_blocked,
            User.availability_status,
            User.availability,
            User.created_at,
        )
    )
    if q:
        users_query = users_query.filter(
            (User.name.ilike(f"%{q}%"))
            | (User.username.ilike(f"%{q}%"))
            | (User.email.ilike(f"%{q}%"))
        )
    if status_filter == "active":
        users_query = users_query.filter(User.is_blocked.is_(False))
    elif status_filter == "blocked":
        users_query = users_query.filter(User.is_blocked.is_(True))
    if role_filter == "user":
        users_query = users_query.filter(User.role == "user")
    elif role_filter == "admin":
        users_query = users_query.filter(User.role == "admin")
    elif role_filter == "super_admin":
        users_query = users_query.filter(User.role == "super_admin")

    def highlight_text(value, term):
        text_value = "" if value is None else str(value)
        needle = (term or "").strip()
        if not needle:
            return escape(text_value)
        pattern = re.compile(re.escape(needle), re.IGNORECASE)
        if not pattern.search(text_value):
            return escape(text_value)

        pieces = []
        last_index = 0
        for match in pattern.finditer(text_value):
            pieces.append(escape(text_value[last_index:match.start()]))
            pieces.append(Markup("<mark class='search-hit'>") + escape(match.group(0)) + Markup("</mark>"))
            last_index = match.end()
        pieces.append(escape(text_value[last_index:]))
        return Markup("").join(pieces)

    users_pagination = users_query.order_by(User.created_at.desc(), User.user_id.desc()).paginate(
        page=page,
        per_page=per_page,
        error_out=False,
    )
    users = users_pagination.items
    return render_template(
        "admin/users.html",
        admin_page_title="Users",
        users=users,
        pagination=users_pagination,
        user_insights=get_admin_user_insights(),
        q=q,
        status_filter=status_filter,
        role_filter=role_filter,
        highlight_text=highlight_text,
        admin_email=ADMIN_EMAIL,
    )


@app.route("/admin/exchanges")
@login_required
@admin_required
def admin_exchanges_page():
    page = request.args.get("page", 1, type=int)
    per_page = 20
    q = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "all").strip().lower()

    sender_alias = aliased(User)
    receiver_alias = aliased(User)
    offered_skill_alias = aliased(Skill)
    requested_skill_alias = aliased(Skill)
    final_offered_skill_alias = aliased(Skill)
    final_requested_skill_alias = aliased(Skill)
    exchanges_query = (
        Request.query.join(sender_alias, Request.sender_id == sender_alias.user_id)
        .join(receiver_alias, Request.receiver_id == receiver_alias.user_id)
        .join(offered_skill_alias, Request.offered_skill_id == offered_skill_alias.skill_id)
        .join(requested_skill_alias, Request.requested_skill_id == requested_skill_alias.skill_id)
        .outerjoin(final_offered_skill_alias, Request.final_offered_skill_id == final_offered_skill_alias.skill_id)
        .outerjoin(final_requested_skill_alias, Request.final_requested_skill_id == final_requested_skill_alias.skill_id)
    )

    if status_filter == "pending":
        exchanges_query = exchanges_query.filter(Request.status.in_(["pending", "countered"]))
    elif status_filter == "ongoing":
        exchanges_query = exchanges_query.filter(Request.status.in_(["accepted", "awaiting_confirmation"]))
    elif status_filter == "completed":
        exchanges_query = exchanges_query.filter(Request.status == "completed")
    elif status_filter == "rejected":
        exchanges_query = exchanges_query.filter(Request.status == "rejected")

    if q:
        like_q = f"%{q}%"
        exchanges_query = exchanges_query.filter(
            or_(
                sender_alias.username.ilike(like_q),
                receiver_alias.username.ilike(like_q),
                offered_skill_alias.skill_name.ilike(like_q),
                requested_skill_alias.skill_name.ilike(like_q),
                final_offered_skill_alias.skill_name.ilike(like_q),
                final_requested_skill_alias.skill_name.ilike(like_q),
            )
        )

    def lifecycle_status_label(raw_status):
        status = (raw_status or "").strip().lower()
        if status in {"pending", "countered"}:
            return "Pending"
        if status in {"accepted", "awaiting_confirmation"}:
            return "Ongoing"
        if status == "completed":
            return "Completed"
        if status == "rejected":
            return "Rejected"
        return status.replace("_", " ").title() if status else "Unknown"

    def lifecycle_status_key(raw_status):
        status = (raw_status or "").strip().lower()
        if status in {"pending", "countered"}:
            return "pending"
        if status in {"accepted", "awaiting_confirmation"}:
            return "ongoing"
        if status in {"completed", "rejected"}:
            return status
        return "pending"

    def highlight_text(value, term):
        text_value = "" if value is None else str(value)
        needle = (term or "").strip()
        if not needle:
            return escape(text_value)
        pattern = re.compile(re.escape(needle), re.IGNORECASE)
        if not pattern.search(text_value):
            return escape(text_value)

        pieces = []
        last_index = 0
        for match in pattern.finditer(text_value):
            pieces.append(escape(text_value[last_index:match.start()]))
            pieces.append(Markup("<mark class='search-hit'>") + escape(match.group(0)) + Markup("</mark>"))
            last_index = match.end()
        pieces.append(escape(text_value[last_index:]))
        return Markup("").join(pieces)

    exchanges_pagination = exchanges_query.order_by(Request.updated_at.desc(), Request.request_id.desc()).paginate(
        page=page,
        per_page=per_page,
        error_out=False,
    )
    exchanges = exchanges_pagination.items
    return render_template(
        "admin/exchanges.html",
        admin_page_title="Exchanges",
        exchanges=exchanges,
        pagination=exchanges_pagination,
        q=q,
        status_filter=status_filter,
        lifecycle_status_label=lifecycle_status_label,
        lifecycle_status_key=lifecycle_status_key,
        highlight_text=highlight_text,
    )


@app.route("/admin/skills")
@login_required
@admin_required
def admin_skills_page():
    page = request.args.get("page", 1, type=int)
    per_page = 20
    q = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "all").strip().lower()
    category_filter = request.args.get("category", "all").strip()

    categories = Category.query.order_by(Category.name.asc()).all()
    category_options = [{"id": c.category_id, "name": c.name} for c in categories]

    skills_query = Skill.query.outerjoin(Category, Skill.category_id == Category.category_id)

    usage_subquery = (
        db.session.query(
            UserSkill.skill_id.label("skill_id"),
            db.func.count(UserSkill.user_id).label("users_count"),
        )
        .group_by(UserSkill.skill_id)
        .subquery()
    )

    skills_query = skills_query.join(
        usage_subquery,
        usage_subquery.c.skill_id == Skill.skill_id,
    )

    if q:
        like_q = f"%{q}%"
        skills_query = skills_query.filter(
            or_(
                Skill.skill_name.ilike(like_q),
                Category.name.ilike(like_q),
                Skill.category.ilike(like_q),
            )
        )

    if status_filter == "active":
        skills_query = skills_query.filter(Skill.status == "active")
    elif status_filter == "blocked":
        skills_query = skills_query.filter(Skill.status == "blocked")

    selected_category_id = None
    if category_filter and category_filter.lower() != "all":
        try:
            parsed_category_id = int(category_filter)
        except (TypeError, ValueError):
            parsed_category_id = None
        if parsed_category_id is not None:
            selected_category = Category.query.filter_by(category_id=parsed_category_id).first()
            if selected_category:
                selected_category_id = selected_category.category_id
                skills_query = skills_query.filter(Skill.category_id == selected_category_id)

    skills_pagination = skills_query.order_by(
        db.func.lower(Skill.skill_name).asc(),
        Skill.skill_name.asc(),
        Skill.skill_id.asc(),
    ).paginate(page=page, per_page=per_page, error_out=False)

    skills_rows = skills_pagination.items
    skill_ids = [row.skill_id for row in skills_rows]

    usage_count_map = {sid: 0 for sid in skill_ids}
    if skill_ids:
        usage_rows = (
            db.session.query(UserSkill.skill_id, db.func.count(UserSkill.user_id))
            .filter(UserSkill.skill_id.in_(skill_ids))
            .group_by(UserSkill.skill_id)
            .all()
        )
        usage_count_map = {sid: count for sid, count in usage_rows}

    def highlight_text(value, term):
        text_value = "" if value is None else str(value)
        needle = (term or "").strip()
        if not needle:
            return escape(text_value)
        pattern = re.compile(re.escape(needle), re.IGNORECASE)
        if not pattern.search(text_value):
            return escape(text_value)

        pieces = []
        last_index = 0
        for match in pattern.finditer(text_value):
            pieces.append(escape(text_value[last_index:match.start()]))
            pieces.append(Markup("<mark class='search-hit'>") + escape(match.group(0)) + Markup("</mark>"))
            last_index = match.end()
        pieces.append(escape(text_value[last_index:]))
        return Markup("").join(pieces)

    return render_template(
        "admin/skills.html",
        admin_page_title="Skills",
        q=q,
        status_filter=status_filter,
        category_filter=str(selected_category_id) if selected_category_id else "all",
        category_options=category_options,
        skills=skills_rows,
        users_count_map=usage_count_map,
        pagination=skills_pagination,
        highlight_text=highlight_text,
    )


@app.route("/admin/skills/<int:skill_id>/toggle-status", methods=["POST"])
@login_required
@admin_required
def admin_toggle_skill_status(skill_id):
    skill = Skill.query.get_or_404(skill_id)
    new_status = "blocked" if (skill.status or "active").lower() == "active" else "active"
    skill.status = new_status
    db.session.commit()

    app.logger.info(
        "Skill status changed by admin. skill_id=%s skill_name=%s new_status=%s actor_user_id=%s",
        skill.skill_id,
        skill.skill_name,
        new_status,
        g.user.user_id,
    )
    flash(f"Skill set to {new_status.title()}.", "success")
    return redirect(request.referrer or url_for("admin_skills_page"))


@app.route("/admin/skills/<int:skill_id>/delete", methods=["POST"])
@app.route("/admin/skills/delete/<int:skill_id>", methods=["POST"])
@login_required
@admin_required
def admin_delete_skill(skill_id):
    skill = Skill.query.get_or_404(skill_id)

    in_use_requests = Request.query.filter(
        or_(
            Request.offered_skill_id == skill_id,
            Request.requested_skill_id == skill_id,
            Request.final_offered_skill_id == skill_id,
            Request.final_requested_skill_id == skill_id,
        )
    ).count()

    UserSkill.query.filter_by(skill_id=skill_id).delete(synchronize_session=False)
    UserSkillsOffered.query.filter_by(skill_id=skill_id).delete(synchronize_session=False)
    UserSkillsWanted.query.filter_by(skill_id=skill_id).delete(synchronize_session=False)

    if in_use_requests > 0:
        skill.status = "blocked"
        db.session.commit()
        flash("Skill is referenced in exchange history. It was blocked and removed from all users.", "success")
        return redirect(request.referrer or url_for("admin_skills_page"))

    app.logger.warning(
        "Skill deleted by admin. skill_id=%s skill_name=%s actor_user_id=%s",
        skill.skill_id,
        skill.skill_name,
        g.user.user_id,
    )
    db.session.delete(skill)
    db.session.commit()
    flash("Skill deleted.", "success")
    return redirect(request.referrer or url_for("admin_skills_page"))


@app.route("/admin/sessions")
@login_required
@admin_required
def admin_sessions_page():
    q = request.args.get("q", "").strip()
    history_status = request.args.get("history_status", "all").strip().lower()

    sender_alias = aliased(User)
    receiver_alias = aliased(User)
    offered_alias = aliased(Skill)
    requested_alias = aliased(Skill)
    final_offered_alias = aliased(Skill)
    final_requested_alias = aliased(Skill)

    sessions_query = (
        Request.query
        .join(sender_alias, Request.sender_id == sender_alias.user_id)
        .join(receiver_alias, Request.receiver_id == receiver_alias.user_id)
        .outerjoin(offered_alias, Request.offered_skill_id == offered_alias.skill_id)
        .outerjoin(requested_alias, Request.requested_skill_id == requested_alias.skill_id)
        .outerjoin(final_offered_alias, Request.final_offered_skill_id == final_offered_alias.skill_id)
        .outerjoin(final_requested_alias, Request.final_requested_skill_id == final_requested_alias.skill_id)
        .filter(
            Request.status.in_(["accepted", "awaiting_confirmation"]),
            Request.session_completed_at.is_(None),
        )
    )

    if q:
        like_q = f"%{q}%"
        sessions_query = sessions_query.filter(
            or_(
                Request.session_room.ilike(like_q),
                db.cast(Request.request_id, db.String).ilike(like_q),
                sender_alias.username.ilike(like_q),
                sender_alias.name.ilike(like_q),
                receiver_alias.username.ilike(like_q),
                receiver_alias.name.ilike(like_q),
            )
        )

    exchange_sessions = (
        sessions_query.order_by(
            db.func.coalesce(Request.session_started_at, Request.updated_at).desc(),
            Request.request_id.desc(),
        )
        .all()
    )

    updated_links = False
    valid_exchange_sessions = []
    for req in exchange_sessions:
        if ensure_session_monitor_record(req):
            updated_links = True
        if req.session_room and req.session_started_at and req.session_link:
            valid_exchange_sessions.append(req)

    now = datetime.utcnow()
    active_window = timedelta(minutes=3)
    participant_activity_counts = {}
    for req in valid_exchange_sessions:
        participants_active = 0
        if req.session_sender_last_ping_at and req.session_sender_last_ping_at >= (now - active_window):
            participants_active += 1
        if req.session_receiver_last_ping_at and req.session_receiver_last_ping_at >= (now - active_window):
            participants_active += 1
        participant_activity_counts[req.request_id] = participants_active

    history_query = (
        Request.query
        .join(sender_alias, Request.sender_id == sender_alias.user_id)
        .join(receiver_alias, Request.receiver_id == receiver_alias.user_id)
        .outerjoin(offered_alias, Request.offered_skill_id == offered_alias.skill_id)
        .outerjoin(requested_alias, Request.requested_skill_id == requested_alias.skill_id)
        .outerjoin(final_offered_alias, Request.final_offered_skill_id == final_offered_alias.skill_id)
        .outerjoin(final_requested_alias, Request.final_requested_skill_id == final_requested_alias.skill_id)
        .filter(Request.status.in_(["completed", "terminated"]))
    )

    if history_status in {"completed", "terminated"}:
        history_query = history_query.filter(Request.status == history_status)

    if q:
        like_q = f"%{q}%"
        history_query = history_query.filter(
            or_(
                Request.session_room.ilike(like_q),
                db.cast(Request.request_id, db.String).ilike(like_q),
                sender_alias.username.ilike(like_q),
                sender_alias.name.ilike(like_q),
                receiver_alias.username.ilike(like_q),
                receiver_alias.name.ilike(like_q),
            )
        )

    session_history = (
        history_query.order_by(
            db.func.coalesce(Request.session_completed_at, Request.updated_at).desc(),
            Request.request_id.desc(),
        )
        .all()
    )

    valid_session_history = []
    for req in session_history:
        if ensure_session_monitor_record(req):
            updated_links = True
        if req.session_room and req.session_started_at and req.session_completed_at and req.session_link:
            valid_session_history.append(req)

    if updated_links:
        db.session.commit()

    return render_template(
        "admin/sessions.html",
        admin_page_title="Session Monitor",
        exchange_sessions=valid_exchange_sessions,
        session_history=valid_session_history,
        participant_activity_counts=participant_activity_counts,
        q=q,
        history_status=history_status,
    )


@app.route("/admin/sessions/live-state")
@login_required
@admin_required
def admin_sessions_live_state():
    ids_raw = request.args.get("ids", "").strip()
    request_ids = []
    for piece in ids_raw.split(","):
        value = piece.strip()
        if not value or not value.isdigit():
            continue
        request_ids.append(int(value))

    if not request_ids:
        return jsonify({"sessions": []})

    rows = Request.query.filter(Request.request_id.in_(request_ids)).all()
    now = datetime.utcnow()
    active_window = timedelta(minutes=3)
    payload = []

    for req in rows:
        is_live = (
            req.status in {"accepted", "awaiting_confirmation"}
            and req.session_completed_at is None
            and req.session_link
        )
        participants_active = 0
        if req.session_sender_last_ping_at and req.session_sender_last_ping_at >= (now - active_window):
            participants_active += 1
        if req.session_receiver_last_ping_at and req.session_receiver_last_ping_at >= (now - active_window):
            participants_active += 1
        payload.append(
            {
                "request_id": req.request_id,
                "is_live": bool(is_live),
                "participants_active": participants_active,
            }
        )

    return jsonify({"sessions": payload})


@app.route("/admin/sessions/<int:session_id>")
@login_required
@admin_required
def admin_session_detail_page(session_id):
    session_row = UserSession.query.get_or_404(session_id)
    return render_template(
        "admin/session_detail.html",
        admin_page_title="Session Details",
        session_row=session_row,
        summarize_user_agent=summarize_user_agent,
    )


@app.route("/admin/sessions/<int:session_id>/force-logout", methods=["POST"])
@login_required
@admin_required
def admin_force_logout_session(session_id):
    target_session = UserSession.query.get_or_404(session_id)
    target_session.is_active = False
    target_session.last_active = datetime.utcnow()
    db.session.commit()

    current_session = resolve_current_user_session(g.user.user_id)
    if current_session and current_session.session_id == target_session.session_id:
        session.clear()
        return redirect(url_for("login"))

    return redirect(request.referrer or url_for("admin_sessions_page"))


@app.route("/admin/sessions/<int:request_id>/end", methods=["POST"])
@login_required
@admin_required
def admin_end_exchange_session(request_id):
    req = Request.query.get_or_404(request_id)
    if req.status not in {"accepted", "awaiting_confirmation"} or req.session_completed_at:
        flash("Exchange session cannot be ended for this request.", "error")
        return redirect(request.referrer or url_for("admin_sessions_page"))

    req.session_completed_at = datetime.utcnow()
    req.status = "terminated"
    req.is_completed_by_sender = False
    req.is_completed_by_receiver = False
    req.sender_confirmed = False
    req.receiver_confirmed = False
    req.session_sender_last_ping_at = None
    req.session_receiver_last_ping_at = None
    post_system_session_message(req, "Session ended by admin supervision.")
    db.session.commit()

    flash("Exchange session ended.", "success")
    return redirect(request.referrer or url_for("admin_sessions_page"))


@app.route("/admin/reports")
@login_required
@admin_required
def admin_reports_page():
    page = request.args.get("page", 1, type=int)
    per_page = 20
    q = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "all").strip().lower()
    reporter_alias = aliased(User)
    reported_alias = aliased(User)
    reports_query = (
        UserReport.query
        .outerjoin(reporter_alias, UserReport.reporter_id == reporter_alias.user_id)
        .outerjoin(reported_alias, UserReport.reported_user_id == reported_alias.user_id)
    )

    if q:
        like_q = f"%{q}%"
        reports_query = reports_query.filter(
            or_(
                reporter_alias.username.ilike(like_q),
                reported_alias.username.ilike(like_q),
            )
        )

    if status_filter == "pending":
        reports_query = reports_query.filter(UserReport.status == "pending")
    elif status_filter == "reviewing":
        reports_query = reports_query.filter(UserReport.status == "reviewing")
    elif status_filter == "warned":
        reports_query = reports_query.filter(UserReport.status == "warned")
    elif status_filter == "resolved":
        reports_query = reports_query.filter(UserReport.status == "resolved")
    elif status_filter == "blocked":
        reports_query = reports_query.filter(UserReport.status == "blocked")

    def highlight_text(value, term):
        text_value = "" if value is None else str(value)
        needle = (term or "").strip()
        if not needle:
            return escape(text_value)
        pattern = re.compile(re.escape(needle), re.IGNORECASE)
        if not pattern.search(text_value):
            return escape(text_value)

        pieces = []
        last_index = 0
        for match in pattern.finditer(text_value):
            pieces.append(escape(text_value[last_index:match.start()]))
            pieces.append(Markup("<mark class='search-hit'>") + escape(match.group(0)) + Markup("</mark>"))
            last_index = match.end()
        pieces.append(escape(text_value[last_index:]))
        return Markup("").join(pieces)

    reports_pagination = reports_query.order_by(UserReport.created_at.desc(), UserReport.report_id.desc()).paginate(
        page=page,
        per_page=per_page,
        error_out=False,
    )
    reports = reports_pagination.items
    safe_image_extensions = {"jpg", "jpeg", "png", "webp"}
    safe_document_extensions = {"pdf"}

    def attachment_details(url):
        clean_url = (url or "").strip()
        if not clean_url:
            return None
        filename = os.path.basename(clean_url.split("?", 1)[0])
        if not filename:
            return None
        extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        is_image = extension in safe_image_extensions
        is_pdf = extension in safe_document_extensions
        if not (is_image or is_pdf):
            return None
        return {
            "url": clean_url,
            "filename": filename,
            "is_image": is_image,
            "is_pdf": is_pdf,
            "file_type": "image" if is_image else "pdf",
            "extension": extension,
        }

    report_attachments = {
        rep.report_id: [
            details
            for details in [attachment_details(raw_url) for raw_url in parse_report_attachments(rep.report_attachments)]
            if details
        ]
        for rep in reports
    }
    return render_template(
        "admin/reports.html",
        admin_page_title="Reports",
        reports=reports,
        pagination=reports_pagination,
        report_attachments=report_attachments,
        status_filter=status_filter,
        q=q,
        highlight_text=highlight_text,
    )


@app.route("/admin/users/<int:user_id>/toggle-block", methods=["POST"])
@login_required
@admin_required
def admin_toggle_block_user(user_id):
    target_user = User.query.get_or_404(user_id)

    if target_user.user_id == g.user.user_id:
        flash("You cannot block your own admin account.", "error")
        return redirect(request.referrer or url_for("admin_users_page"))
    if target_user.is_super_admin:
        flash("Super admin account cannot be blocked.", "error")
        return redirect(request.referrer or url_for("admin_users_page"))

    target_user.is_blocked = not target_user.is_blocked
    db.session.commit()
    flash("User status updated.", "success")
    return redirect(request.referrer or url_for("admin_users_page"))


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@login_required
@admin_required
@super_admin_required
def admin_delete_user(user_id):
    target_user = User.query.get_or_404(user_id)

    if target_user.user_id == g.user.user_id:
        flash("You cannot delete your own admin account.", "error")
        return redirect(request.referrer or url_for("admin_users_page"))
    if target_user.is_super_admin:
        flash("Super admin account cannot be deleted.", "error")
        return redirect(request.referrer or url_for("admin_users_page"))

    UserSkillsOffered.query.filter_by(user_id=target_user.user_id).delete(
        synchronize_session=False
    )
    UserSkillsWanted.query.filter_by(user_id=target_user.user_id).delete(
        synchronize_session=False
    )
    UserSkill.query.filter_by(user_id=target_user.user_id).delete(
        synchronize_session=False
    )
    Request.query.filter(
        (Request.sender_id == target_user.user_id) | (Request.receiver_id == target_user.user_id)
    ).delete(synchronize_session=False)
    Rating.query.filter(
        (Rating.from_user == target_user.user_id) | (Rating.to_user == target_user.user_id)
    ).delete(synchronize_session=False)
    Message.query.filter(
        (Message.sender_id == target_user.user_id) | (Message.receiver_id == target_user.user_id)
    ).delete(synchronize_session=False)
    Notification.query.filter_by(user_id=target_user.user_id).delete(synchronize_session=False)
    UserReport.query.filter(
        (UserReport.reporter_id == target_user.user_id)
        | (UserReport.reported_user_id == target_user.user_id)
    ).delete(synchronize_session=False)

    db.session.delete(target_user)
    db.session.commit()
    flash("User deleted successfully.", "success")
    return redirect(request.referrer or url_for("admin_users_page"))


@app.route("/admin/users/<int:user_id>/promote", methods=["POST"])
@login_required
@admin_required
@super_admin_required
def admin_promote_user(user_id):
    target_user = User.query.get_or_404(user_id)
    if target_user.user_id == g.user.user_id:
        flash("You are already an admin.", "success")
        return redirect(request.referrer or url_for("admin_users_page"))
    if target_user.is_blocked:
        flash("Blocked users cannot be promoted. Unblock first.", "error")
        return redirect(request.referrer or url_for("admin_users_page"))
    if target_user.is_super_admin:
        flash("Super admin account cannot be promoted.", "error")
        return redirect(request.referrer or url_for("admin_users_page"))
    if target_user.is_admin:
        flash("User is already an admin.", "error")
        return redirect(request.referrer or url_for("admin_users_page"))

    try:
        set_user_role(target_user, "admin", actor_user=g.user, reason="explicit_admin_promote")
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(request.referrer or url_for("admin_users_page"))
    flash("User promoted to admin.", "success")
    return redirect(request.referrer or url_for("admin_users_page"))


@app.route("/admin/users/<int:user_id>/demote", methods=["POST"])
@login_required
@admin_required
@super_admin_required
def admin_demote_user(user_id):
    target_user = User.query.get_or_404(user_id)
    if target_user.user_id == g.user.user_id:
        flash("You cannot demote your own account.", "error")
        return redirect(request.referrer or url_for("admin_users_page"))
    if target_user.is_super_admin:
        flash("Super admin privileges cannot be removed.", "error")
        return redirect(request.referrer or url_for("admin_users_page"))
    if target_user.is_blocked:
        flash("Blocked users cannot be demoted. Unblock first.", "error")
        return redirect(request.referrer or url_for("admin_users_page"))
    if not target_user.is_admin:
        flash("User is not an admin.", "error")
        return redirect(request.referrer or url_for("admin_users_page"))

    try:
        set_user_role(target_user, "user", actor_user=g.user, reason="explicit_admin_demote")
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(request.referrer or url_for("admin_users_page"))
    flash("Admin privileges removed.", "success")
    return redirect(request.referrer or url_for("admin_users_page"))


@app.cli.command("init-db")
def init_db_command():
    sync_schema_and_admin()
    app.logger.info("Database initialized and admin schema synced.")


@app.cli.command("sync-db")
def sync_db_command():
    sync_schema_and_admin()
    app.logger.info("Schema synced and admin account enforced.")


def ensure_schema_ready():
    with app.app_context():
        sync_schema_and_admin()
        load_dynamic_settings_from_db()


ensure_schema_ready()


if __name__ == "__main__":
    app.run(debug=True)

