import os
import uuid
import smtplib
import re
import json
import random
import requests
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from urllib.parse import unquote

from flask import Flask, render_template, request, jsonify, session, redirect, send_file, abort
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError, PyMongoError
from gridfs import GridFS
from bson.objectid import ObjectId
from config import Config
from ai.candidate_reporting import build_candidate_report
from ai.groq_generator import get_groq_generator
from api.interview_routes import register_interview_routes
import cloudinary
import cloudinary.uploader
import certifi

try:
    from pypdf import PdfReader
except Exception:
    try:
        from PyPDF2 import PdfReader
    except Exception:
        PdfReader = None


class UnavailableCollection:
    def __init__(self, name, error):
        self.name = name
        self.error = error

    def _raise(self):
        raise ServerSelectionTimeoutError(
            f"MongoDB is unavailable for collection '{self.name}': {self.error}"
        )

    def find(self, *args, **kwargs):
        self._raise()

    def find_one(self, *args, **kwargs):
        self._raise()

    def insert_one(self, *args, **kwargs):
        self._raise()

    def update_one(self, *args, **kwargs):
        self._raise()

    def update_many(self, *args, **kwargs):
        self._raise()

    def delete_one(self, *args, **kwargs):
        self._raise()

    def delete_many(self, *args, **kwargs):
        self._raise()

    def count_documents(self, *args, **kwargs):
        self._raise()


class UnavailableDatabase:
    def __init__(self, error):
        self.error = error

    def __getattr__(self, name):
        return UnavailableCollection(name, self.error)

    def __getitem__(self, name):
        return UnavailableCollection(name, self.error)


class UnavailableGridFS:
    def __init__(self, error):
        self.error = error

    def put(self, *args, **kwargs):
        raise ServerSelectionTimeoutError(f"MongoDB GridFS is unavailable: {self.error}")

    def delete(self, *args, **kwargs):
        raise ServerSelectionTimeoutError(f"MongoDB GridFS is unavailable: {self.error}")


app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "super_secret_key")
app.config["TEMPLATES_AUTO_RELOAD"] = True
CORS(app)
cloudinary.config(
    cloud_name=Config.CLOUDINARY_CLOUD_NAME,
    api_key=Config.CLOUDINARY_API_KEY,
    api_secret=Config.CLOUDINARY_API_SECRET,
    secure=True
)

# -------------------------------
# MongoDB Atlas Connection
# -------------------------------
mongo_kwargs = {
    "serverSelectionTimeoutMS": int(os.getenv("MONGO_SERVER_SELECTION_TIMEOUT_MS", "5000")),
    "connectTimeoutMS": int(os.getenv("MONGO_CONNECT_TIMEOUT_MS", "5000")),
    "socketTimeoutMS": int(os.getenv("MONGO_SOCKET_TIMEOUT_MS", "10000"))
}
if str(Config.MONGO_URI or "").startswith("mongodb+srv://"):
    mongo_kwargs["tls"] = True
    mongo_kwargs["tlsCAFile"] = certifi.where()

mongo_startup_error = None
try:
    client = MongoClient(Config.MONGO_URI, **mongo_kwargs)
    db = client[Config.MONGO_DB]
    fs = GridFS(db)
    try:
        client.admin.command("ping")
    except Exception as e:
        print("MongoDB ping failed on startup:", str(e))
        mongo_startup_error = e
except Exception as e:
    print("MongoDB client initialization failed:", str(e))
    mongo_startup_error = e
    client = None
    db = UnavailableDatabase(e)
    fs = UnavailableGridFS(e)

applications = db.applications
users = db.users
tests = db.tests
coding_tests = db.coding_tests
jobs = db.jobs
MCQ_QUESTION_COUNT = max(20, int(os.getenv("MCQ_QUESTION_COUNT", "20")))
VIRTUAL_QUESTION_COUNT = min(20, max(15, int(os.getenv("VIRTUAL_QUESTION_COUNT", "18"))))
MCQ_PROMOTION_THRESHOLD_PERCENT = float(os.getenv("MCQ_PROMOTION_THRESHOLD_PERCENT", "60"))
RESUME_AUTO_CREDENTIAL_THRESHOLD_PERCENT = float(os.getenv("RESUME_AUTO_CREDENTIAL_THRESHOLD_PERCENT", "60"))
MCQ_TEST_DURATION_SECONDS = max(300, int(os.getenv("MCQ_TEST_DURATION_SECONDS", "3600")))
VIRTUAL_TEST_DURATION_SECONDS = max(300, int(os.getenv("VIRTUAL_TEST_DURATION_SECONDS", "3600")))


def mongo_is_available():
    return mongo_startup_error is None


def mongo_unavailable_payload():
    return {
        "error": "Database connection failed",
        "details": str(mongo_startup_error or "MongoDB is not available")
    }


def utc_now():
    return datetime.now(timezone.utc)


def elapsed_seconds_since(started_at):
    if not isinstance(started_at, datetime):
        return 0
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    return max(0, int((utc_now() - started_at).total_seconds()))


def get_staff_role():
    if not session.get("admin"):
        return None
    role = str(session.get("staff_role", "")).strip().lower()
    return role or "admin"


def is_staff_authorized(*allowed_roles):
    if not session.get("admin"):
        return False
    if not allowed_roles:
        return True
    return get_staff_role() in {str(role).strip().lower() for role in allowed_roles}


def serialize_admin_value(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [serialize_admin_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): serialize_admin_value(item) for key, item in value.items()}
    return value


def public_application_document(application):
    application = application or {}
    return serialize_admin_value({
        "_id": application.get("_id"),
        "source": "application",
        "first_name": application.get("first_name"),
        "last_name": application.get("last_name"),
        "email": application.get("email"),
        "phone": application.get("phone"),
        "skills": application.get("skills"),
        "job_role": application.get("job_role"),
        "status": application.get("status"),
        "ats_score": application.get("ats_score"),
        "ats_decision": application.get("ats_decision"),
        "ats_summary": application.get("ats_summary"),
        "ats_breakdown": application.get("ats_breakdown"),
        "resume_name": application.get("resume_name"),
        "resume": application.get("resume"),
        "created_at": application.get("created_at"),
        "updated_at": application.get("updated_at")
    })


def public_candidate_document(user, include_report=False):
    user = user or {}
    document = {
        "_id": user.get("_id"),
        "source": "candidate",
        "application_id": user.get("application_id"),
        "first_name": user.get("first_name"),
        "last_name": user.get("last_name"),
        "email": user.get("email"),
        "phone": user.get("phone"),
        "skills": user.get("skills"),
        "job_role": user.get("job_role"),
        "status": user.get("status"),
        "assessment_track": user.get("assessment_track"),
        "stage_count": user.get("stage_count"),
        "ats_score": user.get("ats_score"),
        "ats_decision": user.get("ats_decision"),
        "ats_summary": user.get("ats_summary"),
        "ats_breakdown": user.get("ats_breakdown"),
        "score": user.get("score"),
        "mcq_score_percent": user.get("mcq_score_percent"),
        "mcq_raw_score": user.get("mcq_raw_score"),
        "mcq_total_questions": user.get("mcq_total_questions"),
        "mcq_completed_at": user.get("mcq_completed_at"),
        "mcq_proctoring_violations": user.get("mcq_proctoring_violations"),
        "coding_taken": user.get("coding_taken"),
        "coding_score": user.get("coding_score"),
        "coding_feedback": user.get("coding_feedback"),
        "virtual_round_enabled": user.get("virtual_round_enabled"),
        "virtual_taken": user.get("virtual_taken"),
        "virtual_score": user.get("virtual_score"),
        "virtual_feedback": user.get("virtual_feedback"),
        "virtual_answered_count": user.get("virtual_answered_count"),
        "virtual_duration_seconds": user.get("virtual_duration_seconds"),
        "virtual_completed_at": user.get("virtual_completed_at"),
        "virtual_proctoring_violations": user.get("virtual_proctoring_violations"),
        "virtual_report": user.get("virtual_report"),
        "candidate_report": user.get("candidate_report"),
        "bias_review_required": user.get("bias_review_required"),
        "updated_at": user.get("updated_at")
    }
    if include_report:
        report = build_candidate_report(user, interview_evaluation=user.get("virtual_report") or {})
        document["candidate_report"] = report
        document["virtual_questions"] = user.get("virtual_questions", [])
        document["virtual_answers"] = user.get("virtual_answers", [])
        document["questions_data"] = user.get("questions_data", [])
        document["candidate_answers"] = user.get("candidate_answers", [])
    return serialize_admin_value(document)

PROFESSION_CATEGORIES = {
    "technology_it": {
        "label": "Technology & IT",
        "keywords": ["technology", "it", "software", "developer", "engineer", "data", "analyst", "cyber", "cloud", "devops", "product", "qa", "testing", "ai", "ml"]
    },
    "healthcare_medical": {
        "label": "Healthcare & Medical",
        "keywords": ["healthcare", "medical", "nurse", "doctor", "physician", "pharmacist", "clinical", "hospital", "therapist", "lab", "radiology"]
    },
    "engineering_technical": {
        "label": "Engineering & Technical",
        "keywords": ["mechanical", "electrical", "civil", "manufacturing", "technical", "maintenance", "automation", "industrial", "quality engineer"]
    },
    "education_research": {
        "label": "Education & Research",
        "keywords": ["teacher", "faculty", "professor", "education", "research", "lecturer", "trainer", "curriculum", "academic"]
    },
    "business_management": {
        "label": "Business & Management",
        "keywords": ["business", "management", "manager", "operations", "sales", "marketing", "finance", "account", "consultant", "executive", "hr"]
    },
    "law_legal": {
        "label": "Law & Legal",
        "keywords": ["law", "legal", "advocate", "attorney", "compliance", "contract", "litigation", "paralegal", "counsel"]
    },
    "arts_creative": {
        "label": "Arts & Creative Fields",
        "keywords": ["design", "creative", "artist", "writer", "content", "video", "editor", "ui", "ux", "brand", "media"]
    },
    "government_public_services": {
        "label": "Government & Public Services",
        "keywords": ["government", "public", "policy", "administration", "civic", "municipal", "public service", "bureau"]
    },
    "skilled_trades_labor": {
        "label": "Skilled Trades & Labor",
        "keywords": ["technician", "welder", "electrician", "plumber", "operator", "carpenter", "fabricator", "trade", "labor"]
    },
    "agriculture_environment": {
        "label": "Agriculture & Environment",
        "keywords": ["agriculture", "farm", "environment", "sustainability", "soil", "crop", "ecology", "forestry", "agronomy"]
    },
    "transportation_logistics": {
        "label": "Transportation & Logistics",
        "keywords": ["logistics", "supply chain", "transport", "warehouse", "procurement", "fleet", "dispatch", "shipping", "delivery"]
    },
    "hospitality_service": {
        "label": "Hospitality & Service",
        "keywords": ["hospitality", "hotel", "restaurant", "chef", "kitchen", "service", "front office", "guest", "housekeeping", "travel"]
    }
}

LEGACY_DEFAULT_JOB_IDS = {
    "job_data_analyst",
    "job_mechanical_engineer",
    "job_school_teacher",
    "job_operations_manager",
    "job_legal_associate",
    "job_graphic_designer",
    "job_public_admin_officer",
    "job_electrician",
    "job_agri_officer",
    "job_logistics_coordinator",
    "job_python_dev"
}

DEFAULT_JOBS = [
    {
        "id": "job_frontend_developer",
        "profession": "Software Engineering",
        "title": "Frontend Developer",
        "industry": "Software Product",
        "department": "Product Engineering",
        "employment_type": "Full-time",
        "location": "Bengaluru",
        "experience": "1-3 years",
        "salary_range": "INR 6,00,000 - 10,00,000 per year",
        "priority_level": "High",
        "urgency_tag": "Immediate",
        "required_skills": ["html", "css", "javascript", "react", "responsive design"],
        "preferred_skills": ["typescript", "accessibility", "api integration"],
        "threshold": 60,
        "description": "Build responsive web interfaces with strong usability, accessibility, and performance fundamentals.",
        "created_by_role": "system",
        "seed_source": "zyra_top_professions_v1",
        "created_at": utc_now()
    },
    {
        "id": "job_backend_developer",
        "profession": "Software Engineering",
        "title": "Backend Developer",
        "industry": "Software Product",
        "department": "Platform Engineering",
        "employment_type": "Full-time",
        "location": "Hyderabad",
        "experience": "3-5 years",
        "salary_range": "INR 10,00,000 - 16,00,000 per year",
        "priority_level": "High",
        "urgency_tag": "Urgent",
        "required_skills": ["python", "api development", "sql", "system design", "testing"],
        "preferred_skills": ["aws", "docker", "microservices"],
        "threshold": 60,
        "description": "Design and maintain reliable backend services with a focus on scalability, security, and clean architecture.",
        "created_by_role": "system",
        "seed_source": "zyra_top_professions_v1",
        "created_at": utc_now()
    },
    {
        "id": "job_junior_data_scientist",
        "profession": "Data Science / AI",
        "title": "Junior Data Scientist",
        "industry": "Artificial Intelligence Solutions",
        "department": "Data Science",
        "employment_type": "Full-time",
        "location": "Singapore",
        "experience": "1-2 years",
        "salary_range": "USD 45,000 - 60,000 per year",
        "priority_level": "Medium",
        "urgency_tag": "Standard",
        "required_skills": ["python", "sql", "statistics", "data analysis", "pandas"],
        "preferred_skills": ["scikit-learn", "visualization", "experiment design"],
        "threshold": 60,
        "description": "Analyze datasets, build baseline models, and communicate insights that support product and business decisions.",
        "created_by_role": "system",
        "seed_source": "zyra_top_professions_v1",
        "created_at": utc_now()
    },
    {
        "id": "job_ml_engineer",
        "profession": "Data Science / AI",
        "title": "Machine Learning Engineer",
        "industry": "Artificial Intelligence Solutions",
        "department": "AI Engineering",
        "employment_type": "Full-time",
        "location": "Toronto",
        "experience": "3-5 years",
        "salary_range": "USD 75,000 - 105,000 per year",
        "priority_level": "High",
        "urgency_tag": "Urgent",
        "required_skills": ["python", "pytorch", "mlops", "feature engineering", "cloud deployment"],
        "preferred_skills": ["kubernetes", "model monitoring", "docker"],
        "threshold": 60,
        "description": "Deploy and optimize machine learning systems for production-grade predictions and AI-powered features.",
        "created_by_role": "system",
        "seed_source": "zyra_top_professions_v1",
        "created_at": utc_now()
    },
    {
        "id": "job_registered_nurse",
        "profession": "Healthcare",
        "title": "Registered Nurse",
        "industry": "Hospital & Healthcare",
        "department": "Nursing Services",
        "employment_type": "Full-time",
        "location": "Dubai",
        "experience": "1-3 years",
        "salary_range": "USD 32,000 - 45,000 per year",
        "priority_level": "Critical",
        "urgency_tag": "Immediate",
        "required_skills": ["patient care", "medication administration", "clinical documentation", "vital signs", "infection control"],
        "preferred_skills": ["emr", "triage", "bcls"],
        "threshold": 60,
        "description": "Deliver safe, compassionate patient care while supporting physicians and maintaining accurate clinical records.",
        "created_by_role": "system",
        "seed_source": "zyra_top_professions_v1",
        "created_at": utc_now()
    },
    {
        "id": "job_general_physician",
        "profession": "Healthcare",
        "title": "General Physician",
        "industry": "Hospital & Healthcare",
        "department": "Clinical Services",
        "employment_type": "Full-time",
        "location": "Riyadh",
        "experience": "3-6 years",
        "salary_range": "USD 60,000 - 90,000 per year",
        "priority_level": "Critical",
        "urgency_tag": "Urgent",
        "required_skills": ["patient assessment", "clinical diagnosis", "treatment planning", "medical records", "care coordination"],
        "preferred_skills": ["primary care", "outpatient practice", "clinical governance"],
        "threshold": 60,
        "description": "Provide primary medical care, diagnose common conditions, and guide treatment plans with strong patient responsibility.",
        "created_by_role": "system",
        "seed_source": "zyra_top_professions_v1",
        "created_at": utc_now()
    },
    {
        "id": "job_business_analyst",
        "profession": "Business Analysis",
        "title": "Business Analyst",
        "industry": "Management Consulting",
        "department": "Business Transformation",
        "employment_type": "Full-time",
        "location": "Kuala Lumpur",
        "experience": "1-3 years",
        "salary_range": "USD 35,000 - 50,000 per year",
        "priority_level": "Medium",
        "urgency_tag": "Standard",
        "required_skills": ["requirements gathering", "process mapping", "stakeholder communication", "excel", "sql"],
        "preferred_skills": ["agile", "bpmn", "user stories"],
        "threshold": 60,
        "description": "Gather requirements, map processes, and support solution design that improves operational efficiency.",
        "created_by_role": "system",
        "seed_source": "zyra_top_professions_v1",
        "created_at": utc_now()
    },
    {
        "id": "job_senior_business_analyst",
        "profession": "Business Analysis",
        "title": "Senior Business Analyst",
        "industry": "Management Consulting",
        "department": "Strategy & Process Excellence",
        "employment_type": "Full-time",
        "location": "London",
        "experience": "4-6 years",
        "salary_range": "USD 70,000 - 90,000 per year",
        "priority_level": "High",
        "urgency_tag": "Urgent",
        "required_skills": ["stakeholder management", "business case development", "process analysis", "workshop facilitation", "data interpretation"],
        "preferred_skills": ["cbap", "transformation programs", "change management"],
        "threshold": 60,
        "description": "Lead discovery, define business cases, and align stakeholders on high-impact transformation initiatives.",
        "created_by_role": "system",
        "seed_source": "zyra_top_professions_v1",
        "created_at": utc_now()
    },
    {
        "id": "job_project_coordinator",
        "profession": "Project Management",
        "title": "Project Coordinator",
        "industry": "Information Technology Services",
        "department": "Project Management Office",
        "employment_type": "Full-time",
        "location": "Manila",
        "experience": "1-3 years",
        "salary_range": "USD 20,000 - 32,000 per year",
        "priority_level": "Medium",
        "urgency_tag": "Standard",
        "required_skills": ["project documentation", "scheduling", "jira", "communication", "risk tracking"],
        "preferred_skills": ["ms project", "status reporting", "agile support"],
        "threshold": 60,
        "description": "Support project planning, reporting, and team coordination to keep timelines and risks on track.",
        "created_by_role": "system",
        "seed_source": "zyra_top_professions_v1",
        "created_at": utc_now()
    },
    {
        "id": "job_project_manager",
        "profession": "Project Management",
        "title": "Project Manager",
        "industry": "Information Technology Services",
        "department": "Enterprise Delivery",
        "employment_type": "Full-time",
        "location": "Sydney",
        "experience": "4-7 years",
        "salary_range": "USD 80,000 - 110,000 per year",
        "priority_level": "High",
        "urgency_tag": "Urgent",
        "required_skills": ["project planning", "stakeholder management", "budget tracking", "risk management", "delivery governance"],
        "preferred_skills": ["pmp", "agile delivery", "vendor management"],
        "threshold": 60,
        "description": "Own end-to-end project delivery and lead cross-functional teams across scope, timeline, and budget decisions.",
        "created_by_role": "system",
        "seed_source": "zyra_top_professions_v1",
        "created_at": utc_now()
    },
    {
        "id": "job_financial_analyst",
        "profession": "Finance",
        "title": "Financial Analyst",
        "industry": "Banking & Financial Services",
        "department": "Corporate Finance",
        "employment_type": "Full-time",
        "location": "Mumbai",
        "experience": "1-3 years",
        "salary_range": "INR 7,00,000 - 12,00,000 per year",
        "priority_level": "Medium",
        "urgency_tag": "Standard",
        "required_skills": ["financial modeling", "advanced excel", "budgeting", "data analysis", "forecasting"],
        "preferred_skills": ["power bi", "cfa", "presentation skills"],
        "threshold": 60,
        "description": "Prepare financial models, analyze trends, and support planning decisions through accurate reporting.",
        "created_by_role": "system",
        "seed_source": "zyra_top_professions_v1",
        "created_at": utc_now()
    },
    {
        "id": "job_finance_manager",
        "profession": "Finance",
        "title": "Finance Manager",
        "industry": "Banking & Financial Services",
        "department": "Finance Planning & Control",
        "employment_type": "Full-time",
        "location": "New York",
        "experience": "5-7 years",
        "salary_range": "USD 95,000 - 130,000 per year",
        "priority_level": "High",
        "urgency_tag": "Urgent",
        "required_skills": ["budgeting", "financial planning", "compliance awareness", "erp systems", "team leadership"],
        "preferred_skills": ["cpa", "audit management", "business partnering"],
        "threshold": 60,
        "description": "Lead budgeting, compliance, and performance reporting while partnering with business leaders on strategic decisions.",
        "created_by_role": "system",
        "seed_source": "zyra_top_professions_v1",
        "created_at": utc_now()
    },
    {
        "id": "job_primary_school_teacher",
        "profession": "Education",
        "title": "Primary School Teacher",
        "industry": "K-12 Education",
        "department": "Academic Operations",
        "employment_type": "Full-time",
        "location": "Abu Dhabi",
        "experience": "1-3 years",
        "salary_range": "USD 28,000 - 40,000 per year",
        "priority_level": "High",
        "urgency_tag": "Immediate",
        "required_skills": ["lesson planning", "classroom management", "student assessment", "communication", "child safeguarding"],
        "preferred_skills": ["teaching license", "phonics", "inclusive education"],
        "threshold": 60,
        "description": "Deliver engaging classroom instruction, monitor student progress, and create a safe learning environment.",
        "created_by_role": "system",
        "seed_source": "zyra_top_professions_v1",
        "created_at": utc_now()
    },
    {
        "id": "job_academic_coordinator",
        "profession": "Education",
        "title": "Academic Coordinator",
        "industry": "Education Management",
        "department": "Curriculum & Faculty Management",
        "employment_type": "Full-time",
        "location": "Doha",
        "experience": "4-6 years",
        "salary_range": "USD 45,000 - 65,000 per year",
        "priority_level": "High",
        "urgency_tag": "Urgent",
        "required_skills": ["curriculum planning", "faculty coordination", "academic reporting", "stakeholder communication", "quality assurance"],
        "preferred_skills": ["teacher coaching", "school accreditation", "assessment data analysis"],
        "threshold": 60,
        "description": "Coordinate curriculum delivery, mentor teachers, and ensure academic quality across grade levels.",
        "created_by_role": "system",
        "seed_source": "zyra_top_professions_v1",
        "created_at": utc_now()
    },
    {
        "id": "job_front_office_executive",
        "profession": "Hospitality",
        "title": "Front Office Executive",
        "industry": "Hotels & Resorts",
        "department": "Guest Services",
        "employment_type": "Full-time",
        "location": "Bangkok",
        "experience": "0-2 years",
        "salary_range": "USD 18,000 - 24,000 per year",
        "priority_level": "High",
        "urgency_tag": "Immediate",
        "required_skills": ["guest relations", "reservation systems", "communication", "problem resolution", "upselling"],
        "preferred_skills": ["pms tools", "multilingual support", "service recovery"],
        "threshold": 60,
        "description": "Create a welcoming guest experience through efficient check-in, inquiry handling, and service recovery.",
        "created_by_role": "system",
        "seed_source": "zyra_top_professions_v1",
        "created_at": utc_now()
    },
    {
        "id": "job_hotel_manager",
        "profession": "Hospitality",
        "title": "Hotel Manager",
        "industry": "Hotels & Resorts",
        "department": "Hotel Operations",
        "employment_type": "Full-time",
        "location": "Dubai",
        "experience": "5-8 years",
        "salary_range": "USD 70,000 - 100,000 per year",
        "priority_level": "Critical",
        "urgency_tag": "Urgent",
        "required_skills": ["hotel operations", "guest experience", "revenue awareness", "team leadership", "crisis handling"],
        "preferred_skills": ["brand standards", "f&b coordination", "service recovery"],
        "threshold": 60,
        "description": "Lead hotel operations, service standards, and team performance to deliver strong guest satisfaction.",
        "created_by_role": "system",
        "seed_source": "zyra_top_professions_v1",
        "created_at": utc_now()
    }
]

CURATED_DEFAULT_JOB_IDS = {
    "job_frontend_developer",
    "job_backend_developer",
    "job_primary_school_teacher",
    "job_finance_manager",
    "job_front_office_executive",
}

DEFAULT_JOBS = [job for job in DEFAULT_JOBS if job.get("id") in CURATED_DEFAULT_JOB_IDS]

TECHNICAL_PROFESSION_KEYS = {"technology_it", "engineering_technical"}
TECHNICAL_ROLE_HINTS = (
    "developer", "engineer", "software", "frontend", "backend", "full stack",
    "fullstack", "data scientist", "machine learning", "devops", "qa", "automation",
    "cloud", "cyber", "analyst", "programmer"
)


def resolve_assessment_track(role_value="", skills_value="", job_data=None):
    job_data = job_data or {}
    explicit_track = str(job_data.get("assessment_track", "")).strip().lower()
    if explicit_track in {"technical", "non_technical"}:
        return explicit_track

    haystack = normalize_text(
        f"{role_value} {skills_value} {job_data.get('title', '')} {job_data.get('profession', '')} "
        f"{' '.join(job_data.get('required_skills') or [])}"
    )
    if any(normalize_text(keyword).strip() in haystack for keyword in TECHNICAL_ROLE_HINTS):
        return "technical"

    category_key = infer_profession_category(role_value, skills_value)
    return "technical" if category_key in TECHNICAL_PROFESSION_KEYS else "non_technical"


def resolve_stage_count(role_value="", skills_value="", job_data=None):
    return 3 if resolve_assessment_track(role_value, skills_value, job_data) == "technical" else 2


def parse_object_id(value):
    try:
        return ObjectId(value)
    except Exception:
        return None


def upload_resume_to_cloudinary(resume_file):
    if not (Config.CLOUDINARY_CLOUD_NAME and Config.CLOUDINARY_API_KEY and Config.CLOUDINARY_API_SECRET):
        return None, "Cloudinary credentials are not configured"

    original = secure_filename(resume_file.filename or "resume.pdf")
    base_name = os.path.splitext(original)[0] or "resume"
    public_id = f"{uuid.uuid4().hex}_{base_name}"

    try:
        result = cloudinary.uploader.upload(
            resume_file,
            resource_type="raw",
            folder=Config.CLOUDINARY_FOLDER,
            public_id=public_id,
            overwrite=False
        )
    except Exception as e:
        return None, f"Cloudinary upload failed: {str(e)}"

    resume_url = result.get("secure_url") or result.get("url")
    if not resume_url:
        return None, "Cloudinary response missing file URL"
    return resume_url, None


def upload_proctoring_video_to_cloudinary(video_file, assessment_type):
    if not (Config.CLOUDINARY_CLOUD_NAME and Config.CLOUDINARY_API_KEY and Config.CLOUDINARY_API_SECRET):
        return None, "Cloudinary credentials are not configured"

    safe_type = re.sub(r"[^a-z0-9_-]", "_", str(assessment_type or "assessment").lower())
    public_id = f"{uuid.uuid4().hex}_{safe_type}_proctoring"
    folder = f"{Config.CLOUDINARY_FOLDER}/proctoring".strip("/")

    try:
        result = cloudinary.uploader.upload(
            video_file,
            resource_type="video",
            folder=folder,
            public_id=public_id,
            overwrite=False
        )
    except Exception as e:
        return None, f"Cloudinary video upload failed: {str(e)}"

    video_url = result.get("secure_url") or result.get("url")
    if not video_url:
        return None, "Cloudinary response missing video URL"
    return video_url, None


def store_proctoring_video_in_mongodb(video_file, assessment_type):
    try:
        video_file.stream.seek(0)
    except Exception:
        pass

    safe_type = re.sub(r"[^a-z0-9_-]", "_", str(assessment_type or "assessment").lower())
    filename = secure_filename(video_file.filename or f"{safe_type}-proctoring.webm")
    content_type = video_file.mimetype or "video/webm"
    try:
        file_id = fs.put(
            video_file.stream,
            filename=filename,
            content_type=content_type,
            assessment_type=safe_type,
            created_at=utc_now()
        )
    except Exception as e:
        return None, f"MongoDB video storage failed: {str(e)}"
    return str(file_id), None


def send_email(to_email, subject, body):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = Config.SMTP_USER
    msg["To"] = to_email
    msg.set_content(body)

    try:
        with smtplib.SMTP(Config.SMTP_SERVER, Config.SMTP_PORT) as server:
            server.starttls()
            server.login(Config.SMTP_USER, Config.SMTP_PASS)
            server.send_message(msg)
        return True, None
    except Exception as e:
        return False, str(e)


def split_csv(value):
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def normalize_text(value):
    return re.sub(r"[^a-z0-9+#.\s]", " ", str(value or "").lower())


def tokenize(value):
    return list(dict.fromkeys(token for token in normalize_text(value).split() if token))


def infer_profession_category(role_value="", skills_value=""):
    haystack = normalize_text(f"{role_value} {skills_value}")
    best_key = "business_management"
    best_score = 0
    for category_key, category in PROFESSION_CATEGORIES.items():
        score = sum(1 for keyword in category["keywords"] if normalize_text(keyword).strip() and normalize_text(keyword).strip() in haystack)
        if score > best_score:
            best_key = category_key
            best_score = score
    return best_key


for job in DEFAULT_JOBS:
    track = resolve_assessment_track(job.get("title"), ",".join(job.get("required_skills") or []), job)
    job["assessment_track"] = track
    job["stage_count"] = 3 if track == "technical" else 2
    job["seed_source"] = "zyra_curated_stage_roles_v2"


def get_recent_mcq_question_texts(limit=80):
    recent_questions = []
    try:
        recent_tests = tests.find(
            {"questions": {"$exists": True}},
            {"questions.question": 1}
        ).sort("_id", -1).limit(max(1, int(limit / 4)))
        for test in recent_tests:
            for question in test.get("questions", []):
                text = re.sub(r"\s+", " ", str(question.get("question", "")).strip()).lower()
                if text and text not in recent_questions:
                    recent_questions.append(text)
                if len(recent_questions) >= limit:
                    return recent_questions
    except Exception:
        return recent_questions
    return recent_questions


def get_recent_virtual_question_texts(limit=120):
    recent_questions = []
    try:
        recent_users = users.find(
            {"virtual_questions": {"$exists": True, "$type": "array"}},
            {"virtual_questions": 1}
        ).sort("virtual_completed_at", -1).limit(max(20, limit))
        for user_doc in recent_users:
            for question in user_doc.get("virtual_questions", []):
                text = re.sub(r"\s+", " ", str(question or "").strip()).lower()
                if text and text not in recent_questions:
                    recent_questions.append(text)
                if len(recent_questions) >= limit:
                    return recent_questions
    except Exception:
        return recent_questions
    return recent_questions


def question_similarity_key(text):
    return re.sub(r"[^a-z0-9\s]", " ", str(text or "").lower())


def question_tokens(text):
    stop_words = {
        "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with", "your",
        "you", "how", "what", "why", "tell", "me", "about", "describe", "share", "give",
        "example", "role", "work", "time", "would", "could", "did", "do", "as"
    }
    return {
        token for token in question_similarity_key(text).split()
        if len(token) > 2 and token not in stop_words
    }


def is_similar_question(candidate, existing_questions, threshold=0.62):
    candidate_text = re.sub(r"\s+", " ", str(candidate or "").strip()).lower()
    if not candidate_text:
        return True
    candidate_tokens = question_tokens(candidate_text)
    for existing in existing_questions or []:
        existing_text = re.sub(r"\s+", " ", str(existing or "").strip()).lower()
        if not existing_text:
            continue
        if candidate_text == existing_text:
            return True
        existing_tokens = question_tokens(existing_text)
        if candidate_tokens and existing_tokens:
            overlap = len(candidate_tokens & existing_tokens) / max(1, min(len(candidate_tokens), len(existing_tokens)))
            if overlap >= threshold:
                return True
    return False


def extract_text_from_pdf_file(file_storage):
    if PdfReader is None:
        return "", "PDF reader dependency is not installed"

    try:
        file_storage.stream.seek(0)
        reader = PdfReader(file_storage.stream)
        pages = []
        for page in getattr(reader, "pages", []):
            try:
                pages.append(page.extract_text() or "")
            except Exception:
                continue
        file_storage.stream.seek(0)
        text = "\n".join(part.strip() for part in pages if part and part.strip()).strip()
        if not text:
            return "", "No readable text found in PDF"
        return text, None
    except Exception as e:
        try:
            file_storage.stream.seek(0)
        except Exception:
            pass
        return "", str(e)


def analyze_resume_payload(application, job=None):
    job = job or {}
    skills = [item.lower() for item in split_csv(application.get("skills"))]
    job_title = str((job.get("title") or application.get("job_role") or "")).strip()
    required = [str(item).strip() for item in (job.get("required_skills") or []) if str(item).strip()]
    preferred = [str(item).strip() for item in (job.get("preferred_skills") or []) if str(item).strip()]
    resume_text = normalize_text(application.get("resume_analysis_text"))
    tokens = tokenize(
        f"{application.get('skills', '')} "
        f"{application.get('resume_analysis_text', '')} "
        f"{application.get('job_role', '')} "
        f"{application.get('resume_name', '')}"
    )

    def matched(job_skills):
        return [
            skill for skill in job_skills
            if any(token == normalize_text(skill).strip() or token in normalize_text(skill) or normalize_text(skill).strip() in token for token in tokens)
        ]

    required_matches = matched(required)
    preferred_matches = matched(preferred)

    required_coverage = (len(required_matches) / len(required)) if required else min(1.0, len(skills) / 4) if skills else 0
    preferred_coverage = (len(preferred_matches) / len(preferred)) if preferred else min(1.0, len(skills) / 6) if skills else 0
    role_alignment = 1.0 if job_title and (job_title.lower() in resume_text or normalize_text(application.get("job_role")) == job_title.lower()) else 0.68
    experience_hint = 1.0 if re.search(r"\b([1-9][0-9]?)\+?\s*(year|years|yr|yrs)\b", resume_text) else 0.62
    evidence_signals = [
        "project", "projects", "experience", "certification", "achievement", "led", "built",
        "improved", "deployed", "optimized", "implemented", "patient", "teaching", "classroom",
        "compliance", "guest", "shipment", "maintenance", "field", "research", "contract"
    ]
    evidence_count = sum(1 for signal in evidence_signals if signal in resume_text)
    evidence_score = min(1.0, evidence_count / 5)
    profile_score = min(1.0, len(skills) / max(3, len(required) or 3)) if skills else 0.35

    score = round(
        (required_coverage * 45) +
        (preferred_coverage * 15) +
        (role_alignment * 15) +
        (experience_hint * 10) +
        (profile_score * 10) +
        (evidence_score * 5)
    )
    score = max(0, min(100, score))

    threshold = int(job.get("threshold") or 72)
    decision = "review"
    if score >= threshold:
        decision = "shortlisted"
    elif score < max(45, threshold - 20):
        decision = "rejected"

    return {
        "score": score,
        "decision": decision,
        "breakdown": {
            "required_skill_match": f"{len(required_matches)}/{len(required)}",
            "preferred_skill_match": f"{len(preferred_matches)}/{len(preferred)}",
            "matched_keywords": (required_matches + preferred_matches)[:8],
            "experience_signal": "Strong" if experience_hint == 1.0 else "Limited",
            "role_alignment": "Direct" if role_alignment == 1.0 else "Adjacent"
        },
        "summary": (
            f"ATS shortlisted this profile with {score}% match for {job_title or 'the selected role'}."
            if decision == "shortlisted"
            else f"ATS suggests recruiter review with {score}% match for {job_title or 'the selected role'}."
            if decision == "review"
            else f"ATS rejected this profile with {score}% match for {job_title or 'the selected role'}."
        )
    }


def ensure_default_jobs():
    try:
        default_job_ids = [job["id"] for job in DEFAULT_JOBS]
        if LEGACY_DEFAULT_JOB_IDS:
            jobs.delete_many({"id": {"$in": list(LEGACY_DEFAULT_JOB_IDS)}})
        jobs.delete_many({
            "created_by_role": "system",
            "id": {"$nin": default_job_ids}
        })
        for job in DEFAULT_JOBS:
            jobs.update_one({"id": job["id"]}, {"$set": job}, upsert=True)
    except Exception as e:
        print("Job seeding skipped:", str(e))


def create_candidate_account_from_application(app_data):
    username = f"{str(app_data.get('first_name', 'candidate')).lower()}.{uuid.uuid4().hex[:4]}"
    raw_password = uuid.uuid4().hex[:8]
    assessment_track = resolve_assessment_track(app_data.get("job_role"), app_data.get("skills"), app_data)
    stage_count = 3 if assessment_track == "technical" else 2

    user_document = {
        "application_id": str(app_data["_id"]),
        "first_name": app_data["first_name"],
        "last_name": app_data["last_name"],
        "email": app_data["email"],
        "phone": app_data["phone"],
        "skills": app_data["skills"],
        "job_role": app_data["job_role"],
        "resume": app_data["resume"],
        "job_description": app_data.get("job_description"),
        "assessment_track": assessment_track,
        "stage_count": stage_count,
        "username": username.lower(),
        "credential_username": username.lower(),
        "credential_plaintext": raw_password,
        "password": generate_password_hash(raw_password),
        "credential_login_limit": max(1, int(Config.MAX_LOGIN_ATTEMPTS)),
        "credential_login_count": 0,
        "interview_taken": False,
        "score": None,
        "status": "selected",
        "ats_score": app_data.get("ats_score"),
        "ats_decision": app_data.get("ats_decision"),
        "ats_summary": app_data.get("ats_summary"),
        "ats_breakdown": app_data.get("ats_breakdown"),
        "ats_shortlist_reason": app_data.get("ats_shortlist_reason", "auto_shortlisted"),
        "virtual_round_enabled": False,
        "virtual_taken": False,
        "virtual_score": None,
        "virtual_questions": [],
        "virtual_answers": [],
        "coding_round_enabled": False,
        "coding_taken": False,
        "coding_score": None,
        "coding_feedback": None,
        "coding_questions": [],
        "coding_answers": [],
        "coding_duration_seconds": None,
        "virtual_decision": "pending",
        "virtual_feedback": None,
        "virtual_duration_seconds": None,
        "virtual_report": None,
        "bias_review_required": False,
        "interview_status": "not_started",
        "interview_score": None,
        "interview_recommendation": None,
        "candidate_report": None,
        "mcq_completed_at": None,
        "updated_at": utc_now()
    }
    user_document["candidate_report"] = build_candidate_report(user_document)
    users.insert_one(user_document)
    return username.lower(), raw_password


def get_candidate_login_credentials(user):
    username = str(user.get("credential_username") or user.get("username") or "").strip().lower()
    raw_password = str(user.get("credential_plaintext") or "").strip()
    if username and raw_password:
        return username, raw_password
    return username, ""


def reset_candidate_login_usage(user_id, login_limit=None):
    updates = {
        "credential_login_count": 0,
        "updated_at": utc_now()
    }
    if login_limit is not None:
        updates["credential_login_limit"] = max(1, int(login_limit))
    users.update_one({"_id": user_id}, {"$set": updates})


def is_demo_candidate(user):
    if not user:
        return False
    username = str(user.get("username") or user.get("credential_username") or "").strip().lower()
    email = str(user.get("email") or "").strip().lower()
    return bool(user.get("demo_user")) or username == Config.DEMO_CANDIDATE_USERNAME or email == Config.DEMO_CANDIDATE_EMAIL


def cleanup_demo_candidate_artifacts(user_id):
    user_id_string = str(user_id)
    tests.delete_many({"user_id": user_id_string})
    coding_tests.delete_many({"user_id": user_id_string})

    try:
        db.interviews.delete_many({"$or": [{"user_id": user_id}, {"user_id": user_id_string}]})
    except Exception:
        pass

    try:
        records = list(db.proctoring_recordings.find({"user_id": user_id_string}, {"mongo_file_id": 1}))
        for record in records:
            mongo_file_id = record.get("mongo_file_id")
            if mongo_file_id:
                try:
                    fs.delete(ObjectId(mongo_file_id))
                except Exception:
                    pass
        db.proctoring_recordings.delete_many({"user_id": user_id_string})
    except Exception:
        pass

    try:
        db.proctoring_events.delete_many({"$or": [{"user_id": user_id}, {"user_id": user_id_string}]})
    except Exception:
        pass


def reset_demo_candidate_workflow(user_id):
    demo_user = users.find_one({"_id": user_id})
    if not demo_user:
        return None

    reset_payload = {
        "status": "selected",
        "interview_taken": False,
        "score": None,
        "mcq_score_percent": None,
        "mcq_raw_score": None,
        "mcq_total_questions": None,
        "mcq_duration_seconds": None,
        "mcq_time_expired": False,
        "mcq_auto_submitted": False,
        "mcq_proctoring_violations": 0,
        "candidate_answers": [],
        "questions_data": [],
        "mcq_completed_at": None,
        "coding_round_enabled": False,
        "coding_taken": False,
        "coding_score": None,
        "coding_feedback": None,
        "coding_questions": [],
        "coding_answers": [],
        "coding_duration_seconds": None,
        "coding_proctoring_violations": 0,
        "virtual_round_enabled": False,
        "virtual_taken": False,
        "virtual_score": None,
        "virtual_questions": [],
        "virtual_answers": [],
        "virtual_feedback": None,
        "virtual_duration_seconds": None,
        "virtual_started_at": None,
        "virtual_expires_at": None,
        "virtual_time_expired": False,
        "virtual_auto_submitted": False,
        "virtual_report": None,
        "virtual_decision": "pending",
        "virtual_completed_at": None,
        "bias_review_required": False,
        "interview_status": "not_started",
        "interview_score": None,
        "interview_recommendation": None,
        "interview_locked": False,
        "interview_login_attempts": 0,
        "last_interview_session": None,
        "credential_login_count": 0,
        "credential_login_limit": max(1, int(Config.DEMO_LOGIN_LIMIT)),
        "workflow_demo_override": bool(Config.DEMO_ALWAYS_PROMOTE),
        "demo_user": True,
        "updated_at": utc_now()
    }
    reset_snapshot = dict(demo_user)
    reset_snapshot.update(reset_payload)
    reset_payload["candidate_report"] = build_candidate_report(reset_snapshot)

    users.update_one({"_id": user_id}, {"$set": reset_payload})
    cleanup_demo_candidate_artifacts(user_id)

    return users.find_one({"_id": user_id})


def send_candidate_credentials_email(user, subject, intro_lines):
    username, raw_password = get_candidate_login_credentials(user)
    if not username or not raw_password:
        raw_password = uuid.uuid4().hex[:8]
        username = username or str(user.get("username") or "").strip().lower()
        users.update_one(
            {"_id": user["_id"]},
            {"$set": {
                "username": username,
                "credential_username": username,
                "credential_plaintext": raw_password,
                "password": generate_password_hash(raw_password),
                "updated_at": utc_now()
            }}
        )

    body = f"""
Hello {user['first_name']},

{chr(10).join([str(line).strip() for line in intro_lines if str(line).strip()])}

Username: {username}
Password: {raw_password}

Login at: https://zyra-avatar.vercel.app/

Regards,
HR Harsh
"""
    return send_email(user["email"], subject, body)


def ensure_demo_candidate():
    demo_username = Config.DEMO_CANDIDATE_USERNAME
    demo_email = Config.DEMO_CANDIDATE_EMAIL
    demo_password = Config.DEMO_CANDIDATE_PASSWORD
    demo_track = "technical"

    existing = users.find_one({
        "$or": [
            {"username": demo_username},
            {"credential_username": demo_username},
            {"email": demo_email}
        ]
    })
    demo_report = build_candidate_report({
        "ats_score": 82,
        "interview_taken": False,
        "mcq_score_percent": 0,
        "virtual_taken": False,
        "virtual_score": 0,
        "ats_breakdown": {
            "matched_keywords": ["python", "flask", "api design"],
            "missing_keywords": ["production monitoring"]
        }
    })
    demo_document = {
        "application_id": "demo-candidate",
        "first_name": "Demo",
        "last_name": "Candidate",
        "email": demo_email,
        "phone": "+91 90000 00000",
        "skills": "Python, Flask, JavaScript, APIs, Communication",
        "job_role": "Backend Developer",
        "resume": "Demo profile for end-to-end workflow testing.",
        "resume_summary": "Backend-focused candidate with Python, Flask, APIs, and practical project delivery experience.",
        "job_description": "Build backend services, APIs, and integrations with solid communication and debugging skills.",
        "assessment_track": demo_track,
        "stage_count": 3,
        "username": demo_username,
        "credential_username": demo_username,
        "credential_plaintext": demo_password,
        "password": generate_password_hash(demo_password),
        "credential_login_limit": max(1, int(Config.DEMO_LOGIN_LIMIT)),
        "credential_login_count": 0,
        "interview_taken": False,
        "score": None,
        "mcq_score_percent": None,
        "status": "selected",
        "ats_score": 82,
        "ats_decision": "shortlisted",
        "ats_summary": "Demo user was shortlisted because the resume aligns well with backend engineering expectations.",
        "ats_breakdown": {
            "required_skill_match": "Strong",
            "preferred_skill_match": "Moderate",
            "matched_keywords": ["python", "flask", "api design"],
            "missing_keywords": ["production monitoring"]
        },
        "ats_shortlist_reason": "demo_seed_shortlisted",
        "virtual_round_enabled": False,
        "virtual_taken": False,
        "virtual_score": None,
        "virtual_questions": [],
        "virtual_answers": [],
        "coding_round_enabled": False,
        "coding_taken": False,
        "coding_score": None,
        "coding_feedback": None,
        "coding_questions": [],
        "coding_answers": [],
        "coding_duration_seconds": None,
        "virtual_decision": "pending",
        "virtual_feedback": None,
        "virtual_duration_seconds": None,
        "virtual_report": None,
        "interview_status": "not_started",
        "interview_score": None,
        "interview_recommendation": None,
        "candidate_report": demo_report,
        "bias_review_required": False,
        "workflow_demo_override": bool(Config.DEMO_ALWAYS_PROMOTE),
        "demo_user": True,
        "updated_at": utc_now()
    }

    if existing:
        users.update_one(
            {"_id": existing["_id"]},
            {"$set": {
                "job_role": demo_document["job_role"],
                "resume_summary": demo_document["resume_summary"],
                "job_description": demo_document["job_description"],
                "assessment_track": demo_document["assessment_track"],
                "stage_count": demo_document["stage_count"],
                "username": demo_username,
                "credential_username": demo_username,
                "credential_plaintext": demo_password,
                "password": generate_password_hash(demo_password),
                "credential_login_limit": max(1, int(Config.DEMO_LOGIN_LIMIT)),
                "credential_login_count": 0,
                "email": demo_email,
                "status": "selected",
                "ats_score": demo_document["ats_score"],
                "ats_decision": demo_document["ats_decision"],
                "ats_summary": demo_document["ats_summary"],
                "ats_breakdown": demo_document["ats_breakdown"],
                "coding_round_enabled": False,
                "coding_taken": False,
                "coding_score": None,
                "coding_feedback": None,
                "coding_questions": [],
                "coding_answers": [],
                "coding_duration_seconds": None,
                "candidate_report": demo_report,
                "bias_review_required": False,
                "workflow_demo_override": bool(Config.DEMO_ALWAYS_PROMOTE),
                "demo_user": True,
                "updated_at": utc_now()
            }}
        )
        return

    users.insert_one(demo_document)


def safe_ensure_default_jobs():
    if not mongo_is_available():
        print("Default job seed skipped:", mongo_unavailable_payload()["details"])
        return False
    try:
        ensure_default_jobs()
        return True
    except Exception as e:
        print("Default job seed skipped:", str(e))
        return False


def safe_ensure_demo_candidate():
    if not mongo_is_available():
        print("Demo candidate seed skipped:", mongo_unavailable_payload()["details"])
        return False
    try:
        ensure_demo_candidate()
        return True
    except Exception as e:
        print("Demo candidate seed skipped:", str(e))
        return False


def extract_json_block(text):
    if not text:
        return None
    cleaned = text.strip().replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    obj_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if obj_match:
        try:
            return json.loads(obj_match.group())
        except Exception:
            pass
    return None


def normalize_mcq_questions(raw_questions):
    normalized = []
    if not isinstance(raw_questions, list):
        return normalized

    for q in raw_questions:
        if not isinstance(q, dict):
            continue
        question_text = str(q.get("question", "")).strip()
        options = q.get("options")
        answer = q.get("answer")
        if isinstance(answer, str) and answer.isdigit():
            answer = int(answer)

        if not question_text:
            continue
        if not isinstance(options, list) or len(options) != 4:
            continue
        if not isinstance(answer, int) or answer < 0 or answer > 3:
            continue
        cleaned_options = [str(opt).strip() for opt in options]
        normalized_options = [re.sub(r"\s+", " ", opt).strip().lower() for opt in cleaned_options]
        if any(not opt for opt in cleaned_options):
            continue
        if len(set(normalized_options)) != 4:
            continue

        normalized.append({
            "question": question_text,
            "options": cleaned_options,
            "answer": answer
        })

    return normalized


def extract_mcq_context_label(question_text, fallback_label="the role"):
    text = str(question_text or "").strip()
    patterns = [
        r"\bIn ([^,?.]+)",
        r"\bFor ([^,?.]+)",
        r"\bin ([^,?.]+) work",
        r"\bin ([^,?.]+) operations"
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            label = re.sub(r"\s+", " ", match.group(1)).strip(" .")
            if label:
                return label
    return str(fallback_label or "the role").strip() or "the role"


def ensure_mcq_question_quality(questions, user=None):
    prepared = []
    used_questions = set()
    used_option_signatures = set()
    fallback_label = str((user or {}).get("job_role", "")).strip() or "the role"

    for idx, item in enumerate(questions or [], start=1):
        if not isinstance(item, dict):
            continue

        question_text = str(item.get("question", "")).strip()
        options = [str(opt or "").strip() for opt in (item.get("options") or [])]
        answer = item.get("answer")

        if not question_text or len(options) != 4 or not isinstance(answer, int) or answer < 0 or answer > 3:
            continue

        question_key = re.sub(r"\s+", " ", question_text).strip().lower()
        if question_key in used_questions:
            continue

        option_signature = tuple(re.sub(r"\s+", " ", opt).strip().lower() for opt in options)
        if len(set(option_signature)) != 4:
            continue

        if option_signature in used_option_signatures:
            context_label = extract_mcq_context_label(question_text, fallback_label)
            options = [f"{opt} for {context_label}" for opt in options]
            option_signature = tuple(re.sub(r"\s+", " ", opt).strip().lower() for opt in options)
            if option_signature in used_option_signatures:
                options = [f"{opt} [Q{idx}]" for opt in options]
                option_signature = tuple(re.sub(r"\s+", " ", opt).strip().lower() for opt in options)

        used_questions.add(question_key)
        used_option_signatures.add(option_signature)
        prepared.append({
            "question": question_text,
            "options": options,
            "answer": answer
        })

    return prepared


def query_groq_text(prompt_text, system_message="You are a helpful assistant.", model_name=None, max_tokens=800, temperature=0.2):
    if not Config.GROQ_API_KEY:
        return None, {"provider": "groq", "error": "GROQ_API_KEY not configured"}

    payload = {
        "model": model_name or Config.GROQ_TEXT_MODEL,
        "messages": [
            {"role": "system", "content": system_message},
            {"role": "user", "content": prompt_text}
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "top_p": 0.95
    }
    headers = {
        "Authorization": f"Bearer {Config.GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=45
        )
    except Exception as e:
        return None, {"provider": "groq", "error": f"Request failed: {str(e)}"}

    if response.status_code != 200:
        return None, {"provider": "groq", "status_code": response.status_code, "details": response.text}

    result = response.json()
    try:
        content = result["choices"][0]["message"]["content"]
    except Exception:
        return None, {"provider": "groq", "error": "Invalid response format", "raw": result}
    return str(content or "").strip(), None


def generate_mcq_with_groq(prompt_text, num_questions):
    groq_prompt = f"""
{prompt_text}

Generate exactly {num_questions} multiple choice interview questions.
Return ONLY valid JSON in this exact format:
{{
  "questions": [
    {{
      "question": "Question text",
      "options": ["Option A", "Option B", "Option C", "Option D"],
      "answer": 0
    }}
  ]
}}
"""
    content, err = query_groq_text(
        groq_prompt,
        system_message="You generate professional, role-specific MCQ interview questions and return valid JSON only.",
        model_name=Config.GROQ_MCQ_MODEL,
        max_tokens=1600,
        temperature=0.2
    )
    if not content:
        return None, err

    parsed = extract_json_block(content)
    if isinstance(parsed, dict):
        questions = parsed.get("questions")
    elif isinstance(parsed, list):
        questions = parsed
    else:
        questions = None

    normalized = normalize_mcq_questions(questions)
    if len(normalized) < num_questions:
        return None, {"provider": "groq", "error": "Groq returned insufficient valid questions", "count": len(normalized)}
    return {"questions": normalized[:num_questions]}, None


def generate_mcq_with_ollama(prompt_text, num_questions):
    ollama_prompt = f"""
{prompt_text}

Generate exactly {num_questions} multiple choice interview questions.
Return ONLY valid JSON in this exact format:
{{
  "questions": [
    {{
      "question": "Question text",
      "options": ["Option A", "Option B", "Option C", "Option D"],
      "answer": 0
    }}
  ]
}}
"""
    content, err = query_ollama(ollama_prompt, model_name=Config.MCQ_OLLAMA_MODEL)
    if not content:
        return None, err

    parsed = extract_json_block(content)
    if isinstance(parsed, dict):
        questions = parsed.get("questions")
    elif isinstance(parsed, list):
        questions = parsed
    else:
        questions = None

    normalized = normalize_mcq_questions(questions)
    if len(normalized) < num_questions:
        return None, {"provider": "ollama", "error": "Ollama returned insufficient valid questions", "count": len(normalized)}
    return {"questions": normalized[:num_questions]}, None


def generate_deterministic_mcq(user, total_count, session_seed=None, excluded_questions=None):
    skills_raw = str(user.get("skills", "")).strip()
    role_raw = str(user.get("job_role", "")).strip() or "professional role"
    topics = [s.strip() for s in skills_raw.split(",") if s.strip()]
    if not topics:
        topics = [role_raw, "communication", "problem solving", "quality standards", "team coordination"]

    category_key = infer_profession_category(role_raw, skills_raw)
    patterns_by_category = {
        "technology_it": [
            {"question": "In {topic}, which practice most improves maintainability?", "options": ["Consistent standards and modular work", "Only quick fixes", "No documentation", "Skipping reviews"], "answer": 0},
            {"question": "For {topic}, what is the best first step when an issue appears in production?", "options": ["Restart everything immediately", "Review logs, alerts, and recent changes", "Ignore it until more users report it", "Rewrite the module"], "answer": 1},
            {"question": "Which approach best supports reliable delivery in {topic}?", "options": ["Manual changes in live systems", "No testing", "Automated testing and monitoring", "Undocumented deployments"], "answer": 2},
            {"question": "Why are peer reviews valuable in {topic} work?", "options": ["They remove all future bugs", "They add confusion", "They always delay work", "They improve quality and shared understanding"], "answer": 3}
        ],
        "healthcare_medical": [
            {"question": "In {topic}, what should be prioritized first during patient-facing work?", "options": ["Patient safety and accurate assessment", "Speed over safety", "Skipping documentation", "Working without confirmation"], "answer": 0},
            {"question": "If a patient condition changes unexpectedly, what is the best immediate response?", "options": ["Wait until shift end", "Assess, escalate, and document promptly", "Ignore if vitals were stable earlier", "Change medication without authorization"], "answer": 1},
            {"question": "Which habit best supports quality care in {topic}?", "options": ["Incomplete handovers", "Assumptions in charting", "Clear documentation and protocol compliance", "Avoiding team communication"], "answer": 2},
            {"question": "Why is interdisciplinary coordination important in {topic}?", "options": ["It reduces accountability", "It replaces all individual judgment", "It is only for audits", "It improves continuity and patient outcomes"], "answer": 3}
        ],
        "engineering_technical": [
            {"question": "In {topic}, what best reduces repeat technical failures?", "options": ["Root cause analysis and preventive action", "Temporary workarounds only", "Ignoring inspection records", "Delaying maintenance"], "answer": 0},
            {"question": "When equipment performance drops, what should be done first?", "options": ["Replace everything immediately", "Inspect data, symptoms, and safety conditions", "Keep running without checks", "Wait for total failure"], "answer": 1},
            {"question": "Which practice best supports reliable engineering output in {topic}?", "options": ["No testing", "Verbal-only handover", "Standard procedures and quality checks", "Untracked design changes"], "answer": 2},
            {"question": "Why is documentation important in {topic} operations?", "options": ["It has no value after installation", "It only helps auditors", "It slows repairs", "It preserves traceability and supports safer decisions"], "answer": 3}
        ],
        "education_research": [
            {"question": "In {topic}, what most improves learner outcomes?", "options": ["Clear planning with feedback", "Reading slides only", "Avoiding assessment", "Ignoring student differences"], "answer": 0},
            {"question": "If many learners are struggling, what is the best next step?", "options": ["Blame the learners", "Review evidence and adjust instruction", "Move ahead without changes", "Remove all evaluation"], "answer": 1},
            {"question": "Which habit best strengthens quality in {topic} work?", "options": ["No records", "No peer review", "Structured assessment and reflection", "Teaching without objectives"], "answer": 2},
            {"question": "Why is research or progress documentation valuable in {topic}?", "options": ["It only exists for formalities", "It prevents collaboration", "It replaces teaching", "It supports continuity, evidence, and improvement"], "answer": 3}
        ],
        "business_management": [
            {"question": "In {topic}, what best improves team execution?", "options": ["Clear priorities and accountability", "Frequent goal changes", "No ownership", "Untracked tasks"], "answer": 0},
            {"question": "When results fall behind target, what is the best first response?", "options": ["Hide the issue", "Review data, blockers, and action plan", "Blame the team publicly", "Change every process at once"], "answer": 1},
            {"question": "Which approach best supports scalable operations in {topic}?", "options": ["No KPIs", "Verbal-only approvals", "Documented processes and regular reviews", "Reactive planning only"], "answer": 2},
            {"question": "Why is stakeholder communication important in {topic}?", "options": ["It removes the need for decisions", "It is only needed at project end", "It reduces clarity", "It keeps alignment strong and risks visible"], "answer": 3}
        ],
        "law_legal": [
            {"question": "In {topic}, what should be prioritized first?", "options": ["Accuracy, compliance, and evidence", "Speed without review", "Skipping precedents", "Verbal agreements only"], "answer": 0},
            {"question": "When reviewing a contract or case file, what is the best first step?", "options": ["Sign off quickly", "Check facts, obligations, and risks carefully", "Ignore unclear clauses", "Rely on memory alone"], "answer": 1},
            {"question": "Which habit best supports strong legal work in {topic}?", "options": ["No version control", "Minimal recordkeeping", "Structured review and precise documentation", "Untracked changes"], "answer": 2},
            {"question": "Why is legal research important in {topic}?", "options": ["It replaces client discussion", "It is only for court filings", "It delays resolution", "It improves defensibility and decision quality"], "answer": 3}
        ],
        "arts_creative": [
            {"question": "In {topic}, what best improves creative output?", "options": ["Strong brief alignment and iteration", "No feedback", "Random asset choices", "Skipping audience needs"], "answer": 0},
            {"question": "If a concept is not landing with stakeholders, what should you do first?", "options": ["Abandon the project", "Clarify the brief and revise with purpose", "Ignore feedback", "Deliver the same version again"], "answer": 1},
            {"question": "Which practice best strengthens consistency in {topic} work?", "options": ["No design system", "Changing style every time", "Brand standards and review checkpoints", "Unlabeled files"], "answer": 2},
            {"question": "Why is presenting rationale important in {topic}?", "options": ["It removes the need for visuals", "It limits creativity", "It only matters for managers", "It shows how creative choices support goals"], "answer": 3}
        ],
        "government_public_services": [
            {"question": "In {topic}, what should come first?", "options": ["Public interest and procedural compliance", "Personal preference", "Skipping records", "Informal decisions only"], "answer": 0},
            {"question": "If a public request is delayed, what is the best next step?", "options": ["Ignore follow-up", "Check status, resolve the bottleneck, and communicate", "Delete the request", "Shift responsibility without review"], "answer": 1},
            {"question": "Which habit best supports trustworthy service delivery in {topic}?", "options": ["No documentation", "Inconsistent criteria", "Clear records and transparent process", "Unofficial verbal approvals"], "answer": 2},
            {"question": "Why are service standards important in {topic}?", "options": ["They reduce fairness", "They only help internal reports", "They are optional in urgent work", "They improve accountability and citizen trust"], "answer": 3}
        ],
        "skilled_trades_labor": [
            {"question": "In {topic}, what should always be prioritized first?", "options": ["Safety and correct procedure", "Speed without checks", "Skipping PPE", "Working from memory only"], "answer": 0},
            {"question": "When a fault appears on-site, what is the best first response?", "options": ["Keep operating normally", "Inspect safely and isolate the issue", "Ignore the warning", "Disassemble everything immediately"], "answer": 1},
            {"question": "Which approach best supports reliable trade work in {topic}?", "options": ["No checklist", "No maintenance records", "Preventive checks and standard methods", "Guess-based repairs"], "answer": 2},
            {"question": "Why does proper handover matter in {topic}?", "options": ["It is only for paperwork", "It slows the next shift", "It reduces ownership", "It prevents repeat issues and improves safety"], "answer": 3}
        ],
        "agriculture_environment": [
            {"question": "In {topic}, what leads to better field outcomes?", "options": ["Observation-backed decisions", "One solution for every case", "Ignoring local conditions", "No record of results"], "answer": 0},
            {"question": "If field performance drops, what is the best first step?", "options": ["Blame weather only", "Assess conditions, evidence, and likely causes", "Apply every treatment at once", "Stop monitoring"], "answer": 1},
            {"question": "Which habit best supports strong work in {topic}?", "options": ["No tracking", "No follow-up", "Data-backed monitoring and sustainable practice", "Random interventions"], "answer": 2},
            {"question": "Why is stakeholder education important in {topic}?", "options": ["It replaces fieldwork", "It is only needed after failure", "It reduces adoption", "It improves implementation and long-term results"], "answer": 3}
        ],
        "transportation_logistics": [
            {"question": "In {topic}, what best improves delivery reliability?", "options": ["Clear tracking and contingency planning", "No status updates", "Manual memory-based dispatch", "Ignoring delays"], "answer": 0},
            {"question": "If a shipment is delayed, what should happen first?", "options": ["Close the ticket", "Check the cause, update stakeholders, and replan", "Wait without communication", "Change every route immediately"], "answer": 1},
            {"question": "Which approach best supports scalable logistics work in {topic}?", "options": ["No documentation", "No vendor follow-up", "Standard processes and visibility dashboards", "Reactive-only coordination"], "answer": 2},
            {"question": "Why is coordination important in {topic}?", "options": ["It reduces accountability", "It only matters in audits", "It slows dispatch", "It keeps supply flow predictable and issues controlled"], "answer": 3}
        ],
        "hospitality_service": [
            {"question": "In {topic}, what most improves customer experience?", "options": ["Consistent service standards and empathy", "Avoiding guest interaction", "Delaying issue handling", "Ignoring feedback"], "answer": 0},
            {"question": "If a guest raises a complaint, what is the best first response?", "options": ["Argue with the guest", "Acknowledge, assess, and resolve promptly", "Ignore it until checkout", "Transfer without context"], "answer": 1},
            {"question": "Which habit best supports strong hospitality performance in {topic}?", "options": ["No shift notes", "No service checks", "Clear handover and service quality tracking", "Reactive-only service"], "answer": 2},
            {"question": "Why is teamwork important in {topic}?", "options": ["It removes accountability", "It is only useful in peak season", "It lowers service consistency", "It helps deliver smooth and reliable guest experiences"], "answer": 3}
        ]
    }
    patterns = list(patterns_by_category.get(category_key, patterns_by_category["business_management"]))
    excluded = set(excluded_questions or [])
    rng = random.Random(str(session_seed or uuid.uuid4().hex))
    rng.shuffle(patterns)
    rng.shuffle(topics)

    generated = []
    pattern_idx = 0
    topic_idx = 0
    max_attempts = max(total_count * 6, 24)
    attempts = 0
    while len(generated) < total_count and attempts < max_attempts:
        attempts += 1
        topic = topics[topic_idx % len(topics)]
        template = patterns[pattern_idx % len(patterns)]
        question_text = template["question"].format(topic=topic)
        dedupe_key = re.sub(r"\s+", " ", question_text).strip().lower()
        if dedupe_key in excluded:
            pattern_idx += 1
            topic_idx += 1
            continue
        correct_text = template["options"][template["answer"]]
        shuffled_opts = list(template["options"])
        rng.shuffle(shuffled_opts)
        new_answer = shuffled_opts.index(correct_text)
        generated.append({
            "id": len(generated) + 1,
            "question": f"{question_text} (Scenario {len(generated) + 1})",
            "options": shuffled_opts,
            "answer": new_answer
        })
        excluded.add(dedupe_key)
        pattern_idx += 1
        topic_idx += 1

    if len(generated) < total_count:
        fallback_topics = [role_raw, "communication", "decision making", "quality", "stakeholder coordination"]
        fallback_option_sets = [
            ["Follow clear standards and verify facts before acting", "Act without checking requirements", "Delay communication until the issue grows", "Assume someone else will handle it"],
            ["Verify requirements and communicate clearly", "Skip the review step", "Proceed without documentation", "Wait for someone else to act"],
            ["Apply structured thinking and check facts first", "React without analysis", "Ignore the stakeholder input", "Defer indefinitely"],
            ["Use evidence and consult relevant parties", "Make assumptions without checking", "Avoid the issue until escalated", "Delegate without context"],
        ]
        while len(generated) < total_count:
            topic = fallback_topics[len(generated) % len(fallback_topics)]
            question_text = f"For {topic}, which response best demonstrates sound professional judgment?"
            base_opts = list(fallback_option_sets[len(generated) % len(fallback_option_sets)])
            correct_text = base_opts[0]
            rng.shuffle(base_opts)
            generated.append({
                "id": len(generated) + 1,
                "question": f"{question_text} (Scenario {len(generated) + 1})",
                "options": base_opts,
                "answer": base_opts.index(correct_text)
            })
    return generated


def generate_deterministic_virtual_questions(user, total_count):
    role = str(user.get("job_role", "")).strip() or "professional role"
    skills_raw = str(user.get("skills", "")).strip()
    skills = [s.strip() for s in skills_raw.split(",") if s.strip()]
    primary_skill = skills[0] if skills else role
    category_key = infer_profession_category(role, skills_raw)
    templates_by_category = {
        "technology_it": [
            "Tell me about a recent project where you used {skill}. What was your exact contribution?",
            "You are assigned a high-priority issue in a {role} workflow. How would you investigate and resolve it?",
            "Describe a time when you had conflicting technical requirements. How did you handle it?",
            "How do you ensure quality before releasing work in a {role} team?",
            "Explain a performance or reliability issue you solved using {skill}."
        ],
        "healthcare_medical": [
            "Tell me about a situation where you handled a sensitive patient-care responsibility. What did you do?",
            "Describe a time when a patient condition changed unexpectedly. How did you respond?",
            "How do you maintain accuracy in documentation and handovers during busy shifts?",
            "Share an example of working with a multidisciplinary team to improve care outcomes.",
            "What steps do you take to stay calm and safe under clinical pressure?"
        ],
        "engineering_technical": [
            "Tell me about a technical problem you solved in a recent {role} assignment.",
            "Describe a time you identified the root cause of a recurring equipment or process issue.",
            "How do you balance safety, quality, and delivery deadlines in your work?",
            "Share an example of using {skill} to improve efficiency or reduce failures.",
            "How do you communicate technical constraints to operations or management?"
        ],
        "education_research": [
            "Tell me about a lesson, training session, or research task that had a strong outcome.",
            "Describe how you handled learners or stakeholders with different needs or abilities.",
            "How do you measure whether your teaching or research approach is effective?",
            "Share an example of feedback that changed your approach for the better.",
            "How do you keep learners or collaborators engaged over time?"
        ],
        "business_management": [
            "Tell me about a time you improved a process or business outcome in your team.",
            "Describe how you handled competing priorities across stakeholders.",
            "How do you monitor performance and decide when action is needed?",
            "Share an example of leading a team through a challenging period or change.",
            "How do you communicate updates or risks to leadership clearly?"
        ],
        "law_legal": [
            "Tell me about a matter where careful research or drafting changed the outcome.",
            "Describe how you review contracts or case materials for risk and accuracy.",
            "How do you manage deadlines when multiple legal tasks become urgent?",
            "Share an example of explaining a complex legal point to a non-legal stakeholder.",
            "How do you ensure your work stays compliant and well documented?"
        ],
        "arts_creative": [
            "Tell me about a creative project where your concept made a measurable difference.",
            "Describe how you handle feedback that conflicts with your initial creative direction.",
            "How do you balance originality with brand or client constraints?",
            "Share an example of a project where storytelling or design choices solved a problem.",
            "How do you organize your process so quality stays high under deadlines?"
        ],
        "government_public_services": [
            "Tell me about a time you improved a public-facing process or service outcome.",
            "Describe how you handle sensitive records or compliance-heavy work.",
            "How do you manage citizen or stakeholder expectations during delays?",
            "Share an example of coordinating across departments to solve a service issue.",
            "How do you maintain fairness and consistency in day-to-day decisions?"
        ],
        "skilled_trades_labor": [
            "Tell me about a difficult on-site issue you solved safely and effectively.",
            "Describe how you diagnose faults before taking corrective action.",
            "How do you make sure work quality remains high under time pressure?",
            "Share an example where preventive maintenance or inspection avoided a bigger issue.",
            "How do you communicate handover details to the next shift or team?"
        ],
        "agriculture_environment": [
            "Tell me about a field or sustainability challenge you helped solve.",
            "Describe how you use observations or data to make decisions in changing conditions.",
            "How do you work with farmers, communities, or partners to improve adoption?",
            "Share an example of balancing productivity with environmental responsibility.",
            "How do you evaluate whether an intervention was successful?"
        ],
        "transportation_logistics": [
            "Tell me about a time you recovered quickly from a delivery or supply disruption.",
            "Describe how you keep logistics operations visible and under control.",
            "How do you prioritize when several shipments or requests become urgent together?",
            "Share an example of improving coordination across vendors or internal teams.",
            "How do you prevent repeat delays or documentation issues?"
        ],
        "hospitality_service": [
            "Tell me about a guest or customer issue you turned into a positive outcome.",
            "Describe how you maintain service quality during peak demand.",
            "How do you coordinate with other teams to deliver a smooth customer experience?",
            "Share an example of handling difficult feedback professionally.",
            "How do you make sure standards stay consistent across shifts?"
        ]
    }
    templates = templates_by_category.get(category_key, templates_by_category["business_management"])

    questions = []
    idx = 0
    while len(questions) < total_count:
        t = templates[idx % len(templates)]
        questions.append(
            t.format(skill=primary_skill, role=role) + f" (Round Question {len(questions) + 1})"
        )
        idx += 1
    return questions


def generate_mcq_questions_with_fallback(user, total_count, session_seed=None, excluded_questions=None):
    excluded_questions = [re.sub(r"\s+", " ", str(item).strip()).lower() for item in (excluded_questions or []) if str(item).strip()]
    variation_hint = str(session_seed or uuid.uuid4().hex[:8])
    base_prompt = f"""
Generate high-quality, competitive, role-specific professional MCQ interview questions.

Candidate profile:
- Job role: {user.get('job_role')}
- Skills: {user.get('skills')}
- Variation seed: {variation_hint}

Each question must:
- Be relevant to the job role and skills
- Have exactly 4 options
- Have one clearly correct answer index (0-3)
- Include a mix of fundamentals, practical scenarios, judgment, and problem-solving
- Avoid repetition and trivial questions
- Use fresh wording and fresh scenarios instead of repeating common interview templates
"""

    collected = []
    seen = set()
    last_error = None
    attempts = 0
    max_attempts = max(8, total_count)

    provider_order = ["groq", "ollama"] if Config.MCQ_USE_OLLAMA else ["groq"]

    while len(collected) < total_count and attempts < max_attempts:
        attempts += 1
        remaining = total_count - len(collected)
        batch_size = min(8, remaining)
        recent_questions = "\n".join([f"- {q['question']}" for q in collected[-8:]]) or "- None yet"
        external_exclusions = "\n".join([f"- {q}" for q in excluded_questions[:24]]) or "- None recorded"

        prompt = f"""
{base_prompt}

Generate exactly {batch_size} questions in this batch.
Do not repeat questions that are semantically similar to:
{recent_questions}
Also avoid wording or scenarios similar to these recently used questions across other candidates:
{external_exclusions}
"""

        result = None
        batch_errors = []

        for provider in provider_order:
            if provider == "groq":
                candidate_result, err = generate_mcq_with_groq(prompt, batch_size)
                if candidate_result:
                    result = candidate_result
                    break
                batch_errors.append({"provider": "groq", "details": err})
            elif provider == "ollama":
                candidate_result, err = generate_mcq_with_ollama(prompt, batch_size)
                if candidate_result:
                    result = candidate_result
                    break
                batch_errors.append({"provider": "ollama", "details": err})

        if not result:
            last_error = {"error": "All MCQ providers failed for batch", "details": batch_errors}
            continue

        parsed_batch = normalize_mcq_questions(result.get("questions"))
        if not parsed_batch:
            last_error = {"error": "No valid questions in generated batch"}
            continue

        for item in parsed_batch:
            dedupe_key = re.sub(r"\s+", " ", item["question"]).strip().lower()
            if dedupe_key in seen or dedupe_key in excluded_questions:
                continue
            seen.add(dedupe_key)
            collected.append(item)
            if len(collected) >= total_count:
                break

    collected = ensure_mcq_question_quality(collected, user)

    if len(collected) < total_count:
        fallback_needed = total_count - len(collected)
        deterministic = generate_deterministic_mcq(user, fallback_needed, session_seed=session_seed, excluded_questions=excluded_questions + list(seen))
        for item in deterministic:
            dedupe_key = re.sub(r"\s+", " ", item["question"]).strip().lower()
            if dedupe_key in seen or dedupe_key in excluded_questions:
                continue
            seen.add(dedupe_key)
            collected.append({
                "question": item["question"],
                "options": item["options"],
                "answer": item["answer"]
            })
            if len(collected) >= total_count:
                break

    collected = ensure_mcq_question_quality(collected, user)

    if len(collected) < total_count:
        return None, last_error or {"error": "Insufficient valid questions from AI and fallback"}

    final_questions = []
    for idx, q in enumerate(collected[:total_count], start=1):
        final_questions.append({
            "id": idx,
            "question": q["question"],
            "options": q["options"],
            "answer": q["answer"]
        })
    return final_questions, None


def query_ollama(prompt_text, model_name=None):
    payload = {
        "model": model_name or Config.OLLAMA_MODEL,
        "prompt": prompt_text,
        "stream": False
    }
    try:
        response = requests.post(Config.OLLAMA_URL, json=payload, timeout=120)
    except Exception as e:
        return None, {"provider": "ollama", "error": f"Request failed: {str(e)}"}

    if response.status_code != 200:
        return None, {"provider": "ollama", "status_code": response.status_code, "details": response.text}

    body = response.json()
    content = body.get("response")
    if not content:
        return None, {"provider": "ollama", "error": "Missing response field"}
    return content, None


def parse_virtual_question_candidates(content):
    parsed = extract_json_block(content)
    if isinstance(parsed, dict) and isinstance(parsed.get("questions"), list):
        return [str(q).strip() for q in parsed["questions"] if str(q).strip()]
    if isinstance(parsed, list):
        return [str(q).strip() for q in parsed if str(q).strip()]

    lines = []
    for line in str(content or "").splitlines():
        cleaned = re.sub(r"^\s*(\d+[\).\-\s]+|[-*]\s+)", "", line).strip()
        if cleaned:
            lines.append(cleaned)
    return lines


def normalize_virtual_questions(raw_questions, total_count, excluded_questions=None):
    normalized = []
    seen = []
    excluded = [str(item or "").strip() for item in (excluded_questions or []) if str(item or "").strip()]
    for q in raw_questions or []:
        text = re.sub(r"\s+", " ", str(q or "").strip())
        if not text:
            continue
        if len(text) < 18:
            continue
        if is_similar_question(text, seen + excluded):
            continue
        seen.append(text)
        normalized.append(text)
        if len(normalized) >= total_count:
            break
    return normalized


def enforce_virtual_question_mix(questions, user, total_count, excluded_questions=None):
    role = str(user.get("job_role") or "this role").strip() or "this role"
    skills = str(user.get("skills") or "the required skills").strip() or "the required skills"
    skill_list = split_csv(skills)
    primary_skill = skill_list[0] if skill_list else skills
    excluded = [str(item or "").strip() for item in (excluded_questions or []) if str(item or "").strip()]
    generated = normalize_virtual_questions(questions, total_count, excluded)
    fallback_professional = [
        f"Walk me through the professional experiences that best prepared you for the {role} role.",
        "Tell me about a work situation where you had to learn quickly and still deliver a good outcome.",
        "Describe how you prefer to collaborate when a project requires input from different people.",
        f"What professional achievement are you most proud of, and how does it connect to {role} work?",
        "Share a moment when feedback changed how you approached your work.",
        f"What motivates you to keep improving in {role} work?",
        "Describe a professional decision that taught you something important about your work style."
    ]
    professional_questions = normalize_virtual_questions(generated[:3] + fallback_professional, 3, excluded)

    role_source = generated[3:] + generate_deterministic_virtual_questions(user, max(total_count * 3, total_count - len(professional_questions)))
    role_questions = normalize_virtual_questions(role_source, max(0, total_count - len(professional_questions)), excluded + professional_questions)
    mixed = professional_questions + role_questions
    fallback_role_templates = [
        f"How would you use {primary_skill} to handle a measurable delivery challenge in the {role} role?",
        f"What would be your first three steps when joining a new {role} project with unclear requirements?",
        f"Describe how you would explain a difficult {role} tradeoff to a non-technical stakeholder.",
        f"If a project using {primary_skill} started falling behind, how would you diagnose the cause and recover?",
        f"What quality checks would you put in place before handing over important {role} work?",
        f"How would you prioritize two urgent {role} tasks when both have business impact?",
        f"Tell me how you would turn incomplete requirements into an actionable {role} plan.",
        f"What signals would you track to know whether your {role} solution is working well?",
        f"How would you handle a disagreement about priorities during a {role} assignment?",
        f"Describe the information you would gather before recommending a {role} solution.",
        f"What would you do if a handover for important {role} work was incomplete?",
        f"How would you keep stakeholders informed while solving a {role} delivery issue?",
        f"Tell me how you would identify risks before starting a complex {role} task.",
        f"How would you decide whether to escalate a blocked {role} problem?",
        f"What steps would you take to improve a repeated weakness in a {role} workflow?",
        f"How would you validate that your {role} decision helped the team or customer?",
        f"Describe how you would mentor a newer teammate on a task involving {primary_skill}.",
        f"What would you do if quality expectations changed halfway through a {role} project?",
        f"How would you balance speed and accuracy when delivering urgent {role} work?",
        f"Tell me how you would document a difficult decision made during {role} delivery."
    ]
    for filler in fallback_role_templates:
        if len(mixed) >= total_count:
            break
        if not is_similar_question(filler, excluded + mixed):
            mixed.append(filler)

    variant_index = 1
    while len(mixed) < total_count and variant_index <= total_count * 2:
        filler = (
            f"For a fresh {role} scenario involving {primary_skill}, a deadline risk, "
            f"and stakeholder alignment challenge {variant_index}, what action plan would you follow?"
        )
        if not is_similar_question(filler, excluded + mixed, threshold=0.78):
            mixed.append(filler)
        variant_index += 1
    return mixed[:total_count]


def guarantee_virtual_question_count(questions, user, total_count, excluded_questions=None):
    strict_questions = enforce_virtual_question_mix(
        questions or [],
        user,
        total_count,
        excluded_questions=excluded_questions
    )
    if len(strict_questions) >= total_count:
        return strict_questions[:total_count], "strict"

    relaxed_seed = []
    relaxed_seed.extend(strict_questions)
    relaxed_seed.extend(questions or [])
    relaxed_seed.extend(generate_deterministic_virtual_questions(user, total_count * 4))
    relaxed_questions = enforce_virtual_question_mix(
        relaxed_seed,
        user,
        total_count,
        excluded_questions=[]
    )
    if len(relaxed_questions) >= total_count:
        return relaxed_questions[:total_count], "relaxed_history"

    role = str(user.get("job_role") or "this role").strip() or "this role"
    skills = split_csv(user.get("skills"))
    primary_skill = skills[0] if skills else role
    emergency_templates = [
        f"In a {role} situation where customer expectations changed suddenly, how would you reset priorities and communicate the plan?",
        f"When using {primary_skill}, how would you check whether the final outcome is reliable enough to hand over?",
        f"If you noticed a small issue that could become a larger {role} risk, what would you do first?",
        f"Describe how you would compare two possible solutions for a difficult {role} problem.",
        f"How would you recover trust after a delay or mistake in important {role} work?",
        f"What would you ask a manager or client before starting an unfamiliar {role} assignment?",
        f"How would you organize your day when learning a new tool while still delivering {role} responsibilities?",
        f"Describe how you would use feedback from one {role} project to improve the next one.",
        f"If data, instructions, or requirements conflicted in a {role} task, how would you resolve the conflict?",
        f"How would you show ownership when a shared {role} task has no clear owner?",
        f"What would you do if your first solution for a {role} challenge did not work as expected?",
        f"How would you make a complex {role} update easy for a non-specialist to understand?",
        f"Describe how you would protect quality when several {role} deadlines arrive together.",
        f"What would you measure after completing a {role} improvement to prove it created value?",
        f"How would you prepare for a review meeting about your {role} performance and recent outcomes?",
        f"If a teammate disagreed with your {role} approach, how would you test which approach is better?",
        f"How would you adapt your communication style for a senior stakeholder during {role} work?",
        f"Describe a practical plan for reducing repeated errors in a {role} process."
    ]
    for filler in emergency_templates:
        if len(relaxed_questions) >= total_count:
            break
        if not is_similar_question(filler, relaxed_questions, threshold=0.86):
            relaxed_questions.append(filler)

    variant_index = 1
    scenario_focus = [
        "ambiguous acceptance criteria",
        "limited time for testing",
        "multiple teams waiting for a decision",
        "unexpected feedback from a stakeholder",
        "missing information in the request",
        "a process that keeps producing rework",
        "a handover with unclear ownership",
        "pressure to deliver before quality checks finish"
    ]
    while len(relaxed_questions) < total_count and variant_index <= total_count * 4:
        focus = scenario_focus[(variant_index - 1) % len(scenario_focus)]
        filler = (
            f"For {role} scenario {variant_index} involving {focus}, "
            f"what practical steps would you take using {primary_skill} and how would you judge success?"
        )
        if not is_similar_question(filler, relaxed_questions, threshold=0.92):
            relaxed_questions.append(filler)
        variant_index += 1

    return relaxed_questions[:total_count], "emergency_fill"


def generate_virtual_questions_with_fallback(user, total_count, excluded_questions=None):
    excluded_questions = [re.sub(r"\s+", " ", str(item or "").strip()).lower() for item in (excluded_questions or []) if str(item or "").strip()]
    role_count = max(0, total_count - 3)
    exclusion_text = "\n".join([f"- {item}" for item in excluded_questions[:40]]) or "- None"
    prompt = f"""
Generate exactly {total_count} high-quality virtual interview questions for this candidate.
Candidate skills: {user.get('skills')}
Candidate role: {user.get('job_role')}

Question quality rules:
- The first 3 questions must be about personal professional life: background, strengths, work style, career goals, or a meaningful professional challenge.
- The remaining {role_count} questions must be role-based for the candidate's job role and skills.
- Include practical, scenario-based and behavioral questions.
- Test depth, communication, and problem-solving.
- Avoid duplicate, generic, or commonly repeated interview questions.
- Do not repeat or closely paraphrase any question from any earlier stage or candidate history.
- Avoid these previously used questions and similar wording:
{exclusion_text}
- Keep each question concise and interview-ready.

Return ONLY valid JSON:
{{
  "questions": ["Question 1", "Question 2"]
}}
"""

    providers = ["groq", "ollama"] if Config.MCQ_USE_OLLAMA else ["groq"]

    errors = []
    best_ai_questions = []

    for provider in providers:
        if provider == "groq":
            content, err = query_groq_text(
                prompt,
                system_message="You generate role-specific interview questions and return valid JSON only.",
                model_name=Config.GROQ_TEXT_MODEL,
                max_tokens=900,
                temperature=0.35
            )
            if not content:
                errors.append({"provider": "groq", "details": err})
                continue

            questions = normalize_virtual_questions(parse_virtual_question_candidates(content), total_count, excluded_questions)

            if len(questions) >= total_count:
                return enforce_virtual_question_mix(questions[:total_count], user, total_count, excluded_questions), None
            if len(questions) > len(best_ai_questions):
                best_ai_questions = questions
            errors.append({"provider": "groq", "error": "Insufficient virtual questions", "count": len(questions)})
        elif provider == "ollama":
            content, err = query_ollama(prompt, model_name=Config.OLLAMA_MODEL)
            if not content:
                errors.append({"provider": "ollama", "details": err})
                continue

            questions = normalize_virtual_questions(parse_virtual_question_candidates(content), total_count, excluded_questions)

            if len(questions) >= total_count:
                return enforce_virtual_question_mix(questions[:total_count], user, total_count, excluded_questions), None
            if len(questions) > len(best_ai_questions):
                best_ai_questions = questions
            errors.append({"provider": "ollama", "error": "Insufficient virtual questions", "count": len(questions)})

    if best_ai_questions:
        deterministic_fill = generate_deterministic_virtual_questions(user, total_count * 3)
        combined = normalize_virtual_questions(best_ai_questions + deterministic_fill, total_count, excluded_questions)
        mixed, fallback_mode = guarantee_virtual_question_count(combined, user, total_count, excluded_questions)
        if len(mixed) >= total_count:
            return mixed, {"fallback": f"partial_ai_with_{fallback_mode}", "errors": errors}

    deterministic = generate_deterministic_virtual_questions(user, total_count * 3)
    deterministic = normalize_virtual_questions(deterministic, total_count, excluded_questions)
    mixed, fallback_mode = guarantee_virtual_question_count(deterministic, user, total_count, excluded_questions)
    if mixed and len(mixed) >= total_count:
        return mixed, {"fallback": f"deterministic_{fallback_mode}", "errors": errors}
    return None, {"errors": errors}


def evaluate_virtual_submission_with_fallback(evaluation_prompt):
    errors = []
    providers = ["groq", "ollama"] if Config.MCQ_USE_OLLAMA else ["groq"]
    for provider in providers:
        if provider == "groq":
            content, err = query_groq_text(
                evaluation_prompt,
                system_message="You are an interview evaluator. Return valid JSON only with score and feedback.",
                model_name=Config.GROQ_EVAL_MODEL,
                max_tokens=700,
                temperature=0.2
            )
        else:
            content, err = query_ollama(evaluation_prompt, model_name=Config.OLLAMA_MODEL)
        if not content:
            errors.append({"provider": provider, "error": err})
            continue

        parsed = extract_json_block(content)
        if isinstance(parsed, dict):
            return parsed, {"source": provider}

        number_match = re.search(r"\d+(\.\d+)?", content or "")
        if number_match:
            try:
                score_val = float(number_match.group())
            except Exception:
                score_val = 5.0
            return {
                "score": score_val,
                "feedback": "Virtual interview completed. Detailed structured feedback unavailable."
            }, {"source": provider, "format": "text_fallback"}

        errors.append({
            "provider": provider,
            "error": "Invalid evaluator response format",
            "raw_output": str(content)[:600]
        })

    return None, {"source": "local", "errors": errors}


def local_virtual_scoring(questions, answers):
    cleaned_answers = [str(a or "").strip() for a in answers]
    answered = [a for a in cleaned_answers if a]
    total = max(1, len(questions) if isinstance(questions, list) and questions else len(cleaned_answers))
    answered_count = len(answered)

    if answered_count == 0:
        return 0.0, "Interview was submitted without valid answers. Score reflects unanswered responses."

    avg_words = sum(len(a.split()) for a in answered) / answered_count
    completeness = answered_count / total
    depth = min(1.0, avg_words / 55.0)
    score = round((completeness * 6.0) + (depth * 4.0), 1)
    score = max(0.0, min(10.0, score))

    feedback = (
        f"You answered {answered_count} out of {total} questions. "
        f"Provide more detailed, structured examples to improve interview score."
    )
    return score, feedback


def clamp_score(value, minimum=0.0, maximum=10.0):
    return max(minimum, min(maximum, round(float(value), 1)))


def analyze_virtual_answer_metrics(question, answer, user=None):
    question_text = str(question or "").strip()
    answer_text = str(answer or "").strip()
    role = str((user or {}).get("job_role", "")).strip()
    skills_text = str((user or {}).get("skills", "")).strip()

    if not answer_text:
        return {
            "semantic_score": 0.0,
            "nlp_score": 0.0,
            "confidence_score": 0.0,
            "problem_solving_score": 0.0,
            "communication_score": 0.0,
            "overall_score": 0.0,
            "difficulty": "foundation",
            "summary": "Answer was empty.",
            "signals": {"word_count": 0, "sentence_count": 0, "keyword_overlap": 0}
        }

    answer_tokens = tokenize(answer_text)
    question_tokens = tokenize(question_text)
    profile_tokens = tokenize(f"{role} {skills_text}")
    word_count = len(answer_text.split())
    sentence_count = max(1, len([part for part in re.split(r"[.!?]+", answer_text) if part.strip()]))
    unique_ratio = (len(set(answer_tokens)) / max(1, len(answer_tokens)))
    overlap_count = len(set(answer_tokens) & (set(question_tokens) | set(profile_tokens)))
    semantic_score = clamp_score(((overlap_count / max(3, len(set(question_tokens[:12])) or 3)) * 6.5) + min(3.5, word_count / 28.0))

    filler_words = {"maybe", "probably", "kind of", "sort of", "i think", "not sure", "guess"}
    confident_words = {"i led", "i improved", "i resolved", "i delivered", "i designed", "i handled", "i coordinated", "i analyzed", "i implemented"}
    filler_hits = sum(1 for phrase in filler_words if phrase in answer_text.lower())
    confidence_hits = sum(1 for phrase in confident_words if phrase in answer_text.lower())
    confidence_score = clamp_score((min(4.5, word_count / 22.0)) + (confidence_hits * 1.3) - (filler_hits * 1.2) + (unique_ratio * 2.2))

    problem_solving_terms = {"first", "then", "because", "result", "impact", "approach", "resolved", "analyzed", "improved", "measured", "steps", "issue", "solution"}
    problem_solving_hits = sum(1 for token in answer_tokens if token in problem_solving_terms)
    problem_solving_score = clamp_score((problem_solving_hits * 1.2) + min(3.0, sentence_count * 0.9) + min(2.0, word_count / 30.0))

    nlp_score = clamp_score((unique_ratio * 4.0) + min(3.0, word_count / 25.0) + min(3.0, sentence_count * 0.8))
    communication_score = clamp_score((min(4.0, word_count / 24.0)) + (min(3.0, sentence_count * 0.7)) + (unique_ratio * 3.0))
    overall_score = clamp_score(
        (semantic_score * 0.28) +
        (nlp_score * 0.18) +
        (confidence_score * 0.18) +
        (problem_solving_score * 0.22) +
        (communication_score * 0.14)
    )

    difficulty = "foundation"
    if overall_score >= 7.5 and confidence_score >= 7.0:
        difficulty = "advanced"
    elif overall_score >= 5.5:
        difficulty = "intermediate"

    summary = (
        f"Response showed {difficulty} readiness with semantic relevance {semantic_score}/10, "
        f"confidence {confidence_score}/10, and problem solving {problem_solving_score}/10."
    )

    return {
        "semantic_score": semantic_score,
        "nlp_score": nlp_score,
        "confidence_score": confidence_score,
        "problem_solving_score": problem_solving_score,
        "communication_score": communication_score,
        "overall_score": overall_score,
        "difficulty": difficulty,
        "summary": summary,
        "signals": {
            "word_count": word_count,
            "sentence_count": sentence_count,
            "keyword_overlap": overlap_count
        }
    }


def build_virtual_report_locally(user, questions, answers):
    pairs = list(zip(questions or [], answers or []))
    metrics = [analyze_virtual_answer_metrics(question, answer, user) for question, answer in pairs if str(answer or "").strip()]
    if not metrics:
        return {
            "semantic_analysis": 0.0,
            "nlp_evaluation": 0.0,
            "confidence": 0.0,
            "problem_solving": 0.0,
            "communication": 0.0,
            "overall_performance": 0.0,
            "difficulty_progression": "foundation",
            "performance_summary": "Interview was submitted without usable spoken or typed answers.",
            "strengths": ["No measurable strengths captured"],
            "improvements": ["Provide complete answers with examples, actions, and outcomes"],
            "recommendation": "Needs review"
        }

    semantic = clamp_score(sum(item["semantic_score"] for item in metrics) / len(metrics))
    nlp = clamp_score(sum(item["nlp_score"] for item in metrics) / len(metrics))
    confidence = clamp_score(sum(item["confidence_score"] for item in metrics) / len(metrics))
    problem_solving = clamp_score(sum(item["problem_solving_score"] for item in metrics) / len(metrics))
    communication = clamp_score(sum(item["communication_score"] for item in metrics) / len(metrics))
    overall = clamp_score(
        (semantic * 0.24) +
        (nlp * 0.16) +
        (confidence * 0.18) +
        (problem_solving * 0.24) +
        (communication * 0.18)
    )
    max_difficulty = "foundation"
    if any(item["difficulty"] == "advanced" for item in metrics):
        max_difficulty = "advanced"
    elif any(item["difficulty"] == "intermediate" for item in metrics):
        max_difficulty = "intermediate"

    strengths = []
    improvements = []
    if semantic >= 7:
        strengths.append("Answers stayed relevant to the interview questions and target role")
    if confidence >= 7:
        strengths.append("Responses reflected confident ownership and direct contribution")
    if problem_solving >= 7:
        strengths.append("Candidate explained approach, actions, and outcomes clearly")
    if communication >= 7:
        strengths.append("Communication was clear and reasonably well structured")
    if not strengths:
        strengths.append("Candidate attempted to respond across the interview flow")

    if semantic < 6:
        improvements.append("Improve semantic relevance by answering closer to the asked scenario")
    if confidence < 6:
        improvements.append("Use more decisive language and concrete ownership statements")
    if problem_solving < 6:
        improvements.append("Describe problem, action, and result more explicitly")
    if communication < 6:
        improvements.append("Use clearer sentence structure and measurable examples")
    if nlp < 6:
        improvements.append("Add more detail and role-specific vocabulary to strengthen answer quality")
    if not improvements:
        improvements.append("Maintain the same answer depth consistently across all questions")

    recommendation = "Strong fit"
    if overall < 5.5:
        recommendation = "Needs review"
    elif overall < 7.5:
        recommendation = "Moderate fit"

    return {
        "semantic_analysis": semantic,
        "nlp_evaluation": nlp,
        "confidence": confidence,
        "problem_solving": problem_solving,
        "communication": communication,
        "overall_performance": overall,
        "difficulty_progression": max_difficulty,
        "performance_summary": (
            f"Candidate showed {recommendation.lower()} performance with strongest signals in "
            f"{'problem solving' if problem_solving >= max(semantic, confidence, communication, nlp) else 'overall communication'}."
        ),
        "strengths": strengths[:3],
        "improvements": improvements[:3],
        "recommendation": recommendation
    }


def evaluate_virtual_report_with_ai(user, questions, answers):
    prompt = f"""
Evaluate this candidate's virtual interview performance for the role below.

Role: {user.get('job_role')}
Skills: {user.get('skills')}
Questions: {questions}
Answers: {answers}

Return ONLY valid JSON:
{{
  "semantic_analysis": 0,
  "nlp_evaluation": 0,
  "confidence": 0,
  "problem_solving": 0,
  "communication": 0,
  "overall_performance": 0,
  "difficulty_progression": "foundation|intermediate|advanced",
  "performance_summary": "short paragraph",
  "strengths": ["one", "two", "three"],
  "improvements": ["one", "two", "three"],
  "recommendation": "Strong fit|Moderate fit|Needs review"
}}

Rules:
- Scores must be out of 10
- Base the analysis only on the candidate answers
- Be strict, professional, and concise
"""
    parsed, meta = evaluate_virtual_submission_with_fallback(prompt)
    if not isinstance(parsed, dict):
        return None, meta
    return parsed, meta


def generate_adaptive_virtual_question(user, previous_question, answer, target_difficulty, question_number, used_questions=None):
    used_questions = [str(item or "").strip() for item in (used_questions or []) if str(item or "").strip()]
    used_question_text = "\n".join([f"- {item}" for item in used_questions[:35]]) or "- None"
    prompt = f"""
Create one role-specific virtual interview question.

Role: {user.get('job_role')}
Skills: {user.get('skills')}
Previous question: {previous_question}
Candidate answer: {answer}
Target difficulty: {target_difficulty}
Question number: {question_number}

Instructions:
- Ask exactly one new question
- Do not repeat or closely paraphrase the previous question
- Do not repeat or closely paraphrase any question already used in the MCQ stage, avatar stage, or candidate history
- Avoid these used questions and similar wording:
{used_question_text}
- Make the question {target_difficulty} level
- If the candidate did well, increase depth and scenario complexity
- If the candidate struggled, keep it practical and more guided
- Keep it concise and interview-ready

Return ONLY valid JSON:
{{ "question": "..." }}
"""
    providers = ["groq", "ollama"] if Config.MCQ_USE_OLLAMA else ["groq"]
    for provider in providers:
        if provider == "groq":
            content, err = query_groq_text(
                prompt,
                system_message="You generate one concise interview question in valid JSON.",
                model_name=Config.GROQ_TEXT_MODEL,
                max_tokens=220,
                temperature=0.35
            )
        elif provider == "ollama":
            content, err = query_ollama(prompt, model_name=Config.OLLAMA_MODEL)
        else:
            content, err = None, {"provider": provider, "error": "Unsupported provider"}
        if not content:
            continue
        parsed = extract_json_block(content)
        candidate_question = ""
        if isinstance(parsed, dict):
            candidate_question = str(parsed.get("question", "")).strip()
        if not candidate_question:
            lines = [line.strip("-* \t") for line in str(content).splitlines() if line.strip()]
            candidate_question = lines[0] if lines else ""
        normalized = re.sub(r"\s+", " ", candidate_question).strip()
        if normalized and not is_similar_question(normalized, used_questions):
            return normalized

    role = str(user.get("job_role", "")).strip() or "this role"
    skill = split_csv(user.get("skills"))[0] if split_csv(user.get("skills")) else role
    fallback_by_difficulty = {
        "foundation": [
            f"Walk me through a practical situation where you used {skill} and what result you achieved.",
            f"How do you usually prepare before starting a new {role} task that involves {skill}?"
        ],
        "intermediate": [
            f"Describe a challenging situation in {role} where you had to make a decision with limited time or information.",
            f"If a {role} project had unclear requirements and changing priorities, how would you move it forward?"
        ],
        "advanced": [
            f"In {role}, tell me about a high-stakes problem you solved, how you evaluated options, and why you chose your final approach.",
            f"How would you design a long-term improvement plan for a complex {role} workflow involving {skill}?"
        ]
    }
    for fallback in fallback_by_difficulty.get(target_difficulty, fallback_by_difficulty["intermediate"]):
        if not is_similar_question(fallback, used_questions):
            return fallback
    return f"For {role}, explain a fresh example of ownership, decision making, and measurable impact using {skill}."


def normalize_coding_questions(raw_questions):
    normalized = []
    if not isinstance(raw_questions, list):
        return normalized

    for idx, item in enumerate(raw_questions, start=1):
        if not isinstance(item, dict):
            continue
        prompt = str(item.get("prompt", "")).strip()
        title = str(item.get("title", "")).strip() or f"Scenario {idx}"
        starter_code = str(item.get("starter_code", "")).rstrip()
        sample_input = str(item.get("sample_input", "")).strip()
        sample_output = str(item.get("sample_output", "")).strip()
        languages = item.get("languages") or ["Python", "JavaScript"]
        if not prompt:
            continue
        cleaned_languages = [str(language).strip() for language in languages if str(language).strip()]
        normalized.append({
            "id": idx,
            "title": title,
            "prompt": prompt,
            "starter_code": starter_code,
            "sample_input": sample_input,
            "sample_output": sample_output,
            "languages": cleaned_languages[:3] or ["Python", "JavaScript"],
        })
    return normalized


def generate_deterministic_coding_questions(user, total_count=2):
    role = str(user.get("job_role", "")).strip() or "Software Engineer"
    role_key = normalize_text(role)
    templates = [
        {
            "title": "API Reliability Scenario",
            "prompt": f"Build a function for the {role} role that validates incoming records, skips malformed rows safely, and returns a clean summary with processed count, rejected count, and error reasons.",
            "starter_code": "def summarize_records(records):\n    # handle empty and malformed input safely\n    summary = {\n        'processed': 0,\n        'rejected': 0,\n        'errors': []\n    }\n    return summary",
            "sample_input": "[{'id': 1, 'status': 'ok'}, {'id': None, 'status': 'bad'}]",
            "sample_output": "{'processed': 1, 'rejected': 1, 'errors': ['missing id']}",
            "languages": ["Python", "JavaScript"],
        },
        {
            "title": "Role-Based Scenario",
            "prompt": f"Create a scenario-based solution for {role} that transforms raw input into interview-ready insights. Focus on readable structure, edge-case handling, and one clear helper function.",
            "starter_code": "def solve_case(items):\n    if not items:\n        return []\n    result = []\n    return result",
            "sample_input": "Input list of role-specific records",
            "sample_output": "Processed list or summary object",
            "languages": ["Python", "JavaScript"],
        },
    ]

    if "frontend" in role_key or "ui" in role_key:
        templates[0]["title"] = "Component State Scenario"
        templates[0]["prompt"] = "Write a function that receives a list of UI filter actions and returns the final visible item ids without mutating the original data."
        templates[0]["starter_code"] = "function applyFilters(items, actions) {\n  if (!Array.isArray(items)) return [];\n  return items;\n}"
        templates[0]["sample_input"] = "items=[{id:1, tag:'react'}], actions=['tag:react']"
        templates[0]["sample_output"] = "[1]"
        templates[0]["languages"] = ["JavaScript", "TypeScript", "Python"]
    elif "backend" in role_key or "api" in role_key:
        templates[1]["title"] = "Service Aggregation Scenario"
        templates[1]["prompt"] = "Implement a function that aggregates service health records and returns uptime percentage, degraded services, and unresolved incident ids."
        templates[1]["starter_code"] = "def build_health_summary(records):\n    return {\n        'uptime_percent': 0,\n        'degraded_services': [],\n        'incident_ids': []\n    }"
        templates[1]["sample_input"] = "[{'service':'auth','status':'up'},{'service':'mail','status':'degraded','incident_id':'INC-12'}]"
        templates[1]["sample_output"] = "{'uptime_percent': 50, 'degraded_services': ['mail'], 'incident_ids': ['INC-12']}"

    return templates[:max(1, int(total_count or 1))]


def generate_coding_questions_with_fallback(user, total_count=2):
    prompt = f"""
Generate exactly {total_count} scenario-based coding assessment questions for this candidate.

Role: {user.get('job_role')}
Skills: {user.get('skills')}
Assessment rules:
- Questions must be specific to the role
- Each item must be practical and interview-ready
- Include starter_code, sample_input, sample_output, and 2 or 3 language options
- Keep the prompt concise but detailed enough to implement

Return ONLY valid JSON:
{{
  "questions": [
    {{
      "title": "Short scenario title",
      "prompt": "Problem statement",
      "starter_code": "starter code",
      "sample_input": "sample input",
      "sample_output": "sample output",
      "languages": ["Python", "JavaScript"]
    }}
  ]
}}
"""

    errors = []
    providers = ["groq", "ollama"] if Config.MCQ_USE_OLLAMA else ["groq"]
    for provider in providers:
        if provider == "groq":
            content, err = query_groq_text(
                prompt,
                system_message="You generate scenario-based coding interview tasks and return valid JSON only.",
                model_name=Config.GROQ_MCQ_MODEL,
                max_tokens=1400,
                temperature=0.3
            )
        else:
            content, err = query_ollama(prompt, model_name=Config.OLLAMA_MODEL)

        if not content:
            errors.append({"provider": provider, "details": err})
            continue

        parsed = extract_json_block(content)
        questions = parsed.get("questions") if isinstance(parsed, dict) else parsed
        normalized = normalize_coding_questions(questions)
        if len(normalized) >= total_count:
            return normalized[:total_count], None
        errors.append({"provider": provider, "error": "Insufficient coding questions", "count": len(normalized)})

    deterministic = normalize_coding_questions(generate_deterministic_coding_questions(user, total_count))
    if deterministic:
        return deterministic[:total_count], {"fallback": "deterministic", "errors": errors}
    return None, {"errors": errors}


def evaluate_coding_submission_locally(questions, answers):
    normalized_answers = answers if isinstance(answers, list) else []
    non_empty = [item for item in normalized_answers if str((item or {}).get("code", "")).strip()]
    if not non_empty:
        return {
            "score": 0.0,
            "feedback": "No code was submitted for the technical round.",
            "strengths": [],
            "improvements": ["Submit at least one working solution with clear logic."],
        }

    score_signals = []
    for item in non_empty:
        code = str(item.get("code", "")).strip()
        local_score = 4.0
        if "return" in code:
            local_score += 1.5
        if any(token in code for token in ["for ", "while ", ".map(", ".filter("]):
            local_score += 1.0
        if any(token in code for token in ["if ", "try:", "catch", "except"]):
            local_score += 1.0
        if len(code.splitlines()) >= 6:
            local_score += 1.0
        if len(code) >= 180:
            local_score += 1.0
        score_signals.append(min(10.0, local_score))

    average_score = round(sum(score_signals) / max(1, len(score_signals)), 1)
    return {
        "score": average_score,
        "feedback": "Technical round submitted. Stronger edge-case handling and clearer decomposition would improve the solution quality.",
        "strengths": ["Submitted working code for the coding round.", "Included implementation structure instead of leaving the prompt empty."],
        "improvements": ["Add more defensive checks and sample-case coverage.", "Explain naming and structure through cleaner code organization."],
    }


def evaluate_coding_submission_with_fallback(user, questions, answers):
    prompt = f"""
Evaluate this coding assessment submission for the role below.

Role: {user.get('job_role')}
Skills: {user.get('skills')}
Questions: {questions}
Answers: {answers}

Return ONLY valid JSON:
{{
  "score": 0,
  "feedback": "short paragraph",
  "strengths": ["one", "two"],
  "improvements": ["one", "two"]
}}

Rules:
- score must be out of 10
- be strict but fair
- focus on problem solving, code structure, and likely correctness
"""

    providers = ["groq", "ollama"] if Config.MCQ_USE_OLLAMA else ["groq"]
    for provider in providers:
        if provider == "groq":
            content, err = query_groq_text(
                prompt,
                system_message="You are a senior coding evaluator. Return valid JSON only.",
                model_name=Config.GROQ_EVAL_MODEL,
                max_tokens=650,
                temperature=0.2
            )
        else:
            content, err = query_ollama(prompt, model_name=Config.OLLAMA_MODEL)

        if not content:
            continue

        parsed = extract_json_block(content)
        if isinstance(parsed, dict):
            return parsed, {"source": provider}

    return evaluate_coding_submission_locally(questions, answers), {"source": "local"}


def _did_auth_header_value():
    if not Config.DID_API_KEY:
        return None
    token = Config.DID_API_KEY.strip()
    if token.lower().startswith("basic "):
        return token
    return f"Basic {token}"


def generate_did_talk_video(question_text):
    auth_value = _did_auth_header_value()
    if not auth_value:
        return None, {"error": "DID_API_KEY not configured"}

    headers = {
        "Authorization": auth_value,
        "Content-Type": "application/json"
    }

    create_payload = {
        "source_url": Config.DID_AVATAR_SOURCE_URL,
        "script": {
            "type": "text",
            "input": question_text,
            "provider": {
                "type": Config.DID_VOICE_PROVIDER,
                "voice_id": Config.DID_VOICE_ID
            }
        },
        "config": {
            "fluent": True
        }
    }

    talks_url = f"{Config.DID_BASE_URL.rstrip('/')}/talks"
    try:
        created = requests.post(talks_url, headers=headers, json=create_payload, timeout=60)
    except Exception as e:
        return None, {"error": f"D-ID create request failed: {str(e)}"}

    if created.status_code not in (200, 201):
        return None, {
            "error": "D-ID create talk failed",
            "status_code": created.status_code,
            "details": created.text
        }

    created_body = created.json()
    talk_id = created_body.get("id")
    result_url = created_body.get("result_url")
    if result_url:
        return result_url, None
    if not talk_id:
        return None, {"error": "D-ID create response missing talk id", "raw": created_body}

    # Poll until the talk video is ready.
    status_url = f"{talks_url}/{talk_id}"
    timeout_seconds = max(10, int(Config.DID_TALK_TIMEOUT_SECONDS))
    elapsed = 0
    while elapsed < timeout_seconds:
        try:
            status_resp = requests.get(status_url, headers=headers, timeout=30)
        except Exception as e:
            return None, {"error": f"D-ID poll request failed: {str(e)}"}

        if status_resp.status_code != 200:
            return None, {
                "error": "D-ID poll failed",
                "status_code": status_resp.status_code,
                "details": status_resp.text
            }

        body = status_resp.json()
        status = str(body.get("status", "")).lower()
        if status == "done" and body.get("result_url"):
            return body["result_url"], None
        if status in ("error", "failed"):
            return None, {"error": "D-ID talk generation failed", "raw": body}

        # Wait and retry.
        import time
        time.sleep(2)
        elapsed += 2

    return None, {"error": "D-ID talk timed out", "talk_id": talk_id}

# -------------------------------
# MAIN PAGE
# -------------------------------
FRONTEND_PAGES = {
    "index",
    "apply",
    "user_login",
    "user_dashboard",
    "hr_login",
    "admin_dashboard",
    "mcq_test",
    "avatar_interview",
    "report",
}


@app.route("/")
def home():
    safe_ensure_default_jobs()
    safe_ensure_demo_candidate()
    return render_template("index.html")


@app.route("/favicon.ico")
def favicon():
    return "", 204


@app.route("/<page_name>.html")
def frontend_page(page_name):
    if page_name not in FRONTEND_PAGES:
        abort(404)
    if page_name == "admin_dashboard" and not session.get("admin"):
        return redirect("/admin/login")
    if page_name == "report":
        return redirect("/admin/dashboard" if session.get("admin") else "/admin/login")
    if page_name in {"user_dashboard", "mcq_test", "avatar_interview"}:
        if not session.get("candidate_id"):
            return redirect("/candidate/login")
        if not mongo_is_available():
            return render_template("user_login.html"), 503
        user = users.find_one({"_id": ObjectId(session["candidate_id"])})
        if not user:
            session.clear()
            return redirect("/candidate/login")
        if page_name == "mcq_test" and user.get("interview_taken"):
            return redirect("/user_dashboard.html")
        if page_name == "avatar_interview" and (
            not user.get("interview_taken")
            or not user.get("virtual_round_enabled")
            or user.get("virtual_taken")
        ):
            return redirect("/user_dashboard.html")
    return render_template(f"{page_name}.html")


@app.route("/candidate/login")
def candidate_login_page():
    safe_ensure_default_jobs()
    safe_ensure_demo_candidate()
    return render_template("user_login.html")


@app.route("/register")
def register_page():
    safe_ensure_default_jobs()
    return render_template("user_login.html")


@app.route("/resume-template")
def resume_template():
    return redirect("/admin/dashboard" if session.get("admin") else "/admin/login")


@app.route("/admin")
@app.route("/admin/dashboard")
def admin_dashboard():
    """Render the HR dashboard for viewing and managing applications"""
    if not session.get("admin"):
        return redirect("/admin/login")
    safe_ensure_demo_candidate()
    return render_template("admin_dashboard.html")


@app.route("/candidate/interview-v2")
def candidate_interview_v2():
    if not session.get("candidate_id"):
        return redirect("/")
    if not mongo_is_available():
        return redirect("/candidate/login")

    user = users.find_one({"_id": ObjectId(session["candidate_id"])})
    if not user:
        session.clear()
        return redirect("/")
    if user.get("status") == "rejected":
        return redirect("/")
    if not user.get("interview_taken"):
        return redirect("/")
    if user.get("virtual_taken"):
        return redirect("/")
    if not user.get("virtual_round_enabled"):
        return redirect("/")

    return render_template("avatar_interview.html")


@app.route("/media/interview-avatar")
def interview_avatar_media():
    media_path = os.path.join(app.root_path, "Professional_Video_For_Interview.mp4")
    if not os.path.exists(media_path):
        return jsonify({"error": "Interview avatar video not found"}), 404
    return send_file(media_path, mimetype="video/mp4", conditional=True)


@app.route("/admin/login-page")
@app.route("/admin/login")
def admin_login_page():
    """Render admin login page"""
    return render_template("hr_login.html")


@app.route("/admin/logout")
def admin_logout():
    """Logout admin user"""
    session.clear()
    return redirect("/")


# -------------------------------
# JOBS
# -------------------------------
@app.route("/api/jobs", methods=["GET"])
def get_jobs():
    if not mongo_is_available():
        fallback_jobs = [serialize_admin_value(dict(job)) for job in DEFAULT_JOBS]
        return jsonify({
            "jobs": fallback_jobs,
            "warning": "Database unavailable. Showing bundled job list.",
            "details": mongo_unavailable_payload()["details"]
        }), 200

    safe_ensure_default_jobs()
    try:
        items = []
        for job in jobs.find().sort("created_at", -1):
            job["_id"] = str(job["_id"])
            items.append(job)
        return jsonify({"jobs": items})
    except (ServerSelectionTimeoutError, PyMongoError) as e:
        fallback_jobs = [serialize_admin_value(dict(job)) for job in DEFAULT_JOBS]
        return jsonify({
            "jobs": fallback_jobs,
            "warning": "Database unavailable. Showing bundled job list.",
            "details": str(e)
        }), 200


@app.route("/api/jobs", methods=["POST"])
def create_job():
    if not is_staff_authorized("admin", "hr", "recruiter"):
        return jsonify({"error": "Unauthorized"}), 403
    if not mongo_is_available():
        return jsonify(mongo_unavailable_payload()), 503

    data = request.get_json() or {}
    required_fields = ["title", "department", "location", "experience", "required_skills", "threshold", "description"]
    for field in required_fields:
        if not str(data.get(field, "")).strip():
            return jsonify({"error": f"{field.replace('_', ' ').title()} is required"}), 400

    document = {
        "id": f"job_{uuid.uuid4().hex[:10]}",
        "title": str(data.get("title", "")).strip(),
        "department": str(data.get("department", "")).strip(),
        "location": str(data.get("location", "")).strip(),
        "experience": str(data.get("experience", "")).strip(),
        "required_skills": split_csv(data.get("required_skills")),
        "preferred_skills": split_csv(data.get("preferred_skills")),
        "threshold": max(40, min(95, int(data.get("threshold", 72)))),
        "description": str(data.get("description", "")).strip(),
        "created_by_role": get_staff_role(),
        "created_at": utc_now()
    }
    document["assessment_track"] = resolve_assessment_track(document.get("title"), ",".join(document.get("required_skills") or []), document)
    document["stage_count"] = 3 if document["assessment_track"] == "technical" else 2
    jobs.insert_one(document)
    response_job = dict(document)
    response_job["_id"] = str(response_job.get("_id", ""))
    return jsonify({"message": "Job created successfully", "job": response_job})


# -------------------------------
# APPLY
# -------------------------------
@app.route("/api/apply", methods=["POST"])
def apply():
    data = request.form
    resume = request.files.get("resume")

    if not resume or not resume.filename:
        return jsonify({"error": "Resume file is required"}), 400
    if not str(resume.filename).lower().endswith(".pdf"):
        return jsonify({"error": "Resume must be PDF"}), 400

    required_fields = ["first_name", "last_name", "email", "phone", "skills"]
    for field in required_fields:
        if not str(data.get(field, "")).strip():
            return jsonify({"error": f"{field.replace('_', ' ').title()} is required"}), 400
    if not mongo_is_available():
        return jsonify(mongo_unavailable_payload()), 503

    safe_ensure_default_jobs()
    selected_job = None
    job_id = str(data.get("job_id", "")).strip()
    if job_id:
        try:
            selected_job = jobs.find_one({"id": job_id})
        except (ServerSelectionTimeoutError, PyMongoError) as e:
            return jsonify({
                "error": "Database connection failed",
                "details": str(e)
            }), 503
        if not selected_job:
            return jsonify({"error": "Selected job was not found"}), 400

    job_role = str(data.get("job_role", "")).strip() or str((selected_job or {}).get("title", "")).strip()
    if not job_role:
        return jsonify({"error": "Job role is required"}), 400
    assessment_track = resolve_assessment_track(job_role, data.get("skills"), selected_job)
    stage_count = resolve_stage_count(job_role, data.get("skills"), selected_job)

    resume_text, resume_text_error = extract_text_from_pdf_file(resume)
    resume_name = secure_filename(resume.filename or "resume.pdf")

    resume_url, upload_error = upload_resume_to_cloudinary(resume)
    if not resume_url:
        return jsonify({"error": "Resume upload failed", "details": upload_error}), 500

    analysis_payload = {
        "skills": data.get("skills"),
        "job_role": job_role,
        "resume_analysis_text": resume_text,
        "resume_name": resume_name
    }
    ats = analyze_resume_payload(analysis_payload, selected_job)

    # Determine shortlist reason: auto-shortlisted if resume score meets threshold, else needs HR review.
    ats_score = ats["score"]
    auto_shortlisted = ats_score >= RESUME_AUTO_CREDENTIAL_THRESHOLD_PERCENT
    shortlist_reason = "auto_shortlisted" if auto_shortlisted else "needs_hr_review"
    
    # Status logic: auto-shortlist at threshold, reject if ATS decision is rejected, else pending for HR review.
    if auto_shortlisted:
        status = "selected"
    elif ats["decision"] == "rejected":
        status = "rejected"
    else:
        status = "pending"

    application_document = {
            "first_name": data.get("first_name"),
            "last_name": data.get("last_name"),
            "email": data.get("email"),
            "phone": data.get("phone"),
            "skills": data.get("skills"),
            "job_id": job_id or None,
            "job_role": job_role,
            "job_description": str((selected_job or {}).get("description", "")).strip(),
            "assessment_track": assessment_track,
            "stage_count": stage_count,
            "resume": resume_url,
            "resume_name": resume_name,
            "resume_analysis_text": resume_text,
            "resume_analysis_error": resume_text_error,
            "ats_score": ats["score"],
            "ats_decision": ats["decision"],
            "ats_summary": ats["summary"],
            "ats_breakdown": ats["breakdown"],
            "ats_shortlist_reason": shortlist_reason,
            "status": status,
            "created_at": utc_now()
        }

    try:
        insert_result = applications.insert_one(application_document)
        application_document["_id"] = insert_result.inserted_id
    except ServerSelectionTimeoutError as e:
        return jsonify({
            "error": "Database connection failed",
            "details": str(e)
        }), 503
    except PyMongoError as e:
        return jsonify({
            "error": "Database write failed",
            "details": str(e)
        }), 500

    response_message = "Application submitted and ATS analyzed automatically"
    email_error = None

    if auto_shortlisted:
        try:
            username, raw_password = create_candidate_account_from_application(application_document)
            sent, email_error = send_email(
                application_document["email"],
                "zyra Interview Credentials",
                f"""
Hello {application_document['first_name']},

Congratulations! Your profile has been automatically shortlisted with a resume score of {ats_score}% for the {application_document['job_role']} role.

Username: {username}
Password: {raw_password}

Login at: https://zyra-avatar.vercel.app/

Regards,
HR Harsh
"""
            )
            response_message = "Application submitted and credentials generated automatically"
            if not sent:
                response_message = "Application shortlisted and credentials generated, but email failed"
        except PyMongoError as e:
            applications.update_one(
                {"_id": application_document["_id"]},
                {"$set": {"status": "pending", "auto_shortlist_error": str(e), "updated_at": utc_now()}}
            )
            return jsonify({
                "error": "ATS shortlisted the candidate, but account creation failed",
                "details": str(e)
            }), 500
    elif ats["decision"] == "rejected":
        sent, email_error = send_email(
            application_document["email"],
            "zyra Application Update",
            f"""
Hello {application_document['first_name']},

Thank you for applying to zyra for the {application_document['job_role']} role.
After automated screening, we will not be moving forward with this application.

We appreciate your time and interest in zyra.

Regards,
zyra HR
"""
        )
        response_message = "Application submitted and rejected automatically"
        if not sent:
            response_message = "Application rejected automatically, but email failed"

    return jsonify({
        "message": response_message,
        "application_id": str(application_document["_id"]),
        "ats_score": ats["score"],
        "ats_decision": ats["decision"],
        "ats_summary": ats["summary"],
        "auto_credential_threshold_percent": RESUME_AUTO_CREDENTIAL_THRESHOLD_PERCENT,
        "credentials_generated": bool(auto_shortlisted),
        "credentials_email_sent": bool(auto_shortlisted and not email_error),
        "resume_analysis_warning": resume_text_error,
        "email_error": email_error
    })

# -------------------------------
# HR LOGIN
# -------------------------------
@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    data = request.get_json() or {}
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", "")).strip()
    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400

    configured_username = str(Config.ADMIN_USER or "").strip().lower()
    typed_username = username.lower()
    allowed_usernames = {configured_username}
    if "@" in configured_username:
        allowed_usernames.add(configured_username.split("@", 1)[0])
    allowed_usernames.update({"admin", "hr", "recruiter"})

    role_map = {
        "admin": "admin",
        "hr": "hr",
        "recruiter": "recruiter"
    }
    resolved_role = role_map.get(typed_username, "admin")

    if typed_username in allowed_usernames and password == Config.ADMIN_PASS:
        session.clear()
        session["admin"] = True
        session["staff_role"] = resolved_role
        return jsonify({"message": "Login success", "role": resolved_role})
    return jsonify({"error": "Invalid credentials"}), 401

# -------------------------------
# GET APPLICATIONS BY STATUS
# -------------------------------
@app.route("/api/admin/applications")
def get_applications():
    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"}), 403
    if not mongo_is_available():
        return jsonify(mongo_unavailable_payload()), 503

    safe_ensure_demo_candidate()

    try:
        pending = [
            public_application_document(app_doc)
            for app_doc in applications.find({"status": "pending"}).sort("created_at", -1)
        ]
        selected = [
            public_candidate_document(user_doc)
            for user_doc in users.find({
                "status": {"$ne": "rejected"},
                "demo_user": {"$ne": True}
            }).sort("updated_at", -1)
        ]
        rejected = [
            public_application_document(app_doc)
            for app_doc in applications.find({"status": "rejected"}).sort("updated_at", -1)
        ] + [
            public_candidate_document(user_doc)
            for user_doc in users.find({
                "status": "rejected",
                "demo_user": {"$ne": True}
            }).sort("updated_at", -1)
        ]
        reports = [
            public_candidate_document(user_doc, include_report=True)
            for user_doc in users.find({
                "interview_taken": True,
                "virtual_taken": True,
                "demo_user": {"$ne": True}
            }).sort("virtual_completed_at", -1)
        ]
    except (ServerSelectionTimeoutError, PyMongoError) as e:
        return jsonify({
            "error": "Database connection failed",
            "details": str(e)
        }), 503

    return jsonify({
        "pending": pending,
        "rejected": rejected,
        "selected": selected,
        "reports": reports
    })

# -------------------------------
# ACCEPT APPLICATION
# -------------------------------
@app.route("/api/admin/accept/<id>", methods=["POST"])
def accept_candidate(id):
    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"}), 403

    object_id = parse_object_id(id)
    if not object_id:
        return jsonify({"error": "Invalid candidate id"}), 400

    app_data = applications.find_one({"_id": object_id})
    if not app_data:
        return jsonify({"error": "Application not found"}), 404

    existing_user = users.find_one({"application_id": str(app_data["_id"])})
    if existing_user:
        return jsonify({"message": "Candidate is already shortlisted"}), 200

    username, raw_password = create_candidate_account_from_application(app_data)



    applications.update_one(
        {"_id": object_id},
        {"$set": {"status": "selected"}}
    )

    sent, email_error = send_email(
        app_data["email"],
        "zyra Interview Credentials",
        f"""
Hello {app_data['first_name']},

Congratulations! You are selected for zyra interview.

Username: {username}
Password: {raw_password}

Login at: https://zyra-avatar.vercel.app/

Regards,
HR Harsh
"""
    )

    if sent:
        return jsonify({"message": "Candidate accepted and credentials sent"})
    return jsonify({"message": "Candidate accepted but email failed", "email_error": email_error}), 200

@app.route("/resume/<path:resume_ref>")
def get_resume(resume_ref):
    decoded = unquote(resume_ref or "").strip()
    if decoded.startswith("http://") or decoded.startswith("https://"):
        return redirect(decoded)
    return jsonify({"error": "Resume link is invalid"}), 404

# -------------------------------
# REJECT
# -------------------------------
@app.route("/api/admin/reject/<id>", methods=["POST"])
def reject_candidate(id):
    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"}), 403

    object_id = parse_object_id(id)
    if not object_id:
        return jsonify({"error": "Invalid candidate id"}), 400

    app_data = applications.find_one({"_id": object_id})
    if not app_data:
        return jsonify({"error": "Application not found"}), 404

    applications.update_one(
        {"_id": object_id},
        {"$set": {"status": "rejected", "updated_at": utc_now()}}
    )

    sent, email_error = send_email(
        app_data["email"],
        "zyra Application Update",
        f"""
Hello {app_data['first_name']},

Thank you for applying to zyra for the {app_data.get('job_role', 'selected')} role.
After reviewing your profile, we will not be moving forward with this application.

We appreciate your time and interest in zyra.

Regards,
zyra HR
"""
    )

    if sent:
        return jsonify({"message": "Candidate rejected and email sent"})
    return jsonify({"message": "Candidate rejected, but email failed", "email_error": email_error}), 200




@app.route("/api/admin/resend_credentials/<id>", methods=["POST"])
def resend_credentials(id):
    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"}), 403

    object_id = parse_object_id(id)
    if not object_id:
        return jsonify({"error": "Invalid candidate id"}), 400

    user = users.find_one({"_id": object_id})
    if not user:
        return jsonify({"error": "User not found"}), 404

    reset_candidate_login_usage(object_id, login_limit=Config.MAX_LOGIN_ATTEMPTS)
    user = users.find_one({"_id": object_id})

    sent, email_error = send_candidate_credentials_email(
        user,
        "zyra — Login Credentials Reminder",
        [
            "This is a reminder from the zyra HR team.",
            "Your login credentials for the zyra assessment portal are below.",
            "Please log in and complete your pending assessment."
        ]
    )

    if sent:
        return jsonify({"message": "Credentials resent successfully"})
    return jsonify({"message": "Failed to send email", "email_error": email_error}), 200


@app.route("/api/admin/enable_virtual/<id>", methods=["POST"])
def enable_virtual(id):
    # Backward-compatible alias for promote API.
    return promote_virtual(id)


@app.route("/api/admin/virtual/promote/<id>", methods=["POST"])
def promote_virtual(id):

    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"}), 403

    object_id = parse_object_id(id)
    if not object_id:
        return jsonify({"error": "Invalid candidate id"}), 400

    user = users.find_one({"_id": object_id})
    if not user:
        return jsonify({"error": "User not found"}), 404

    if not user.get("interview_taken"):
        return jsonify({"error": "Candidate has not completed MCQ interview yet"}), 400
    assessment_track = user.get("assessment_track", resolve_assessment_track(user.get("job_role"), user.get("skills"), user))
    enable_coding = assessment_track == "technical" and not user.get("coding_taken")
    update_fields = {
        "status": "selected",
        "credential_login_count": 0,
        "credential_login_limit": max(1, int(Config.MAX_LOGIN_ATTEMPTS)),
        "bias_review_required": False,
        "updated_at": utc_now()
    }
    if enable_coding:
        update_fields.update({
            "coding_round_enabled": True,
            "virtual_round_enabled": False,
            "virtual_decision": "pending_coding",
        })
    else:
        update_fields.update({
            "virtual_round_enabled": True,
            "virtual_decision": "promoted",
        })

    users.update_one({"_id": object_id}, {"$set": update_fields})

    updated_user = users.find_one({"_id": object_id})
    sent, email_error = send_candidate_credentials_email(
        updated_user,
        "zyra Next Assessment Round",
        [
            "Congratulations! You have been manually promoted by the recruiting team.",
            f"Please login to your dashboard and complete your {'Coding Assessment' if enable_coding else 'AI Avatar Virtual Interview'} next.",
            "Your login credentials are below:"
        ]
    )

    if sent:
        return jsonify({"message": f"Candidate promoted to {'coding round' if enable_coding else 'virtual round'} and email sent"})
    return jsonify({"message": "Candidate promoted, but email failed", "email_error": email_error}), 200


@app.route("/api/admin/virtual/reject/<id>", methods=["POST"])
def reject_after_mcq(id):

    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"}), 403

    object_id = parse_object_id(id)
    if not object_id:
        return jsonify({"error": "Invalid candidate id"}), 400

    user = users.find_one({"_id": object_id})
    if not user:
        return jsonify({"error": "User not found"}), 404

    users.update_one(
        {"_id": object_id},
        {"$set": {
            "virtual_round_enabled": False,
            "virtual_decision": "rejected",
            "status": "rejected",
            "updated_at": utc_now()
        }}
    )

    sent, email_error = send_email(
        user["email"],
        "zyra Interview Update",
        f"""
Hello {user['first_name']},

Thank you for completing the interview process.
We will get back to you.

Regards,
zyra HR
"""
    )

    if sent:
        return jsonify({"message": "Candidate removed from process and email sent"})
    return jsonify({"message": "Candidate removed, but email failed", "email_error": email_error}), 200

@app.route("/api/virtual/questions", methods=["POST"])
def generate_virtual_questions():

    if not session.get("candidate_id"):
        return jsonify({"error": "Unauthorized"}), 403

    user = users.find_one({"_id": ObjectId(session["candidate_id"])})
    if not user:
        return jsonify({"error": "Candidate not found"}), 404
    if not user.get("interview_taken"):
        return jsonify({"error": "Complete MCQ interview first"}), 400
    if not user.get("virtual_round_enabled"):
        return jsonify({"error": "Virtual round is not enabled by HR"}), 400
    if user.get("virtual_taken"):
        return jsonify({"error": "Virtual interview already completed"}), 400

    excluded_questions = []
    excluded_questions.extend(get_recent_virtual_question_texts(limit=160))
    excluded_questions.extend(get_recent_mcq_question_texts(limit=80))
    excluded_questions.extend([str(q.get("question", "")).strip() for q in user.get("questions_data", []) if isinstance(q, dict)])
    excluded_questions.extend([str(q or "").strip() for q in user.get("virtual_questions", []) if str(q or "").strip()])

    try:
        questions, last_error = generate_virtual_questions_with_fallback(
            user,
            VIRTUAL_QUESTION_COUNT,
            excluded_questions=excluded_questions
        )
    except Exception as e:
        questions, fallback_mode = guarantee_virtual_question_count(
            generate_deterministic_virtual_questions(user, VIRTUAL_QUESTION_COUNT * 2),
            user,
            VIRTUAL_QUESTION_COUNT,
            excluded_questions=excluded_questions
        )
        last_error = {"error": "Virtual question generation exception", "details": str(e), "fallback": fallback_mode}

    if not questions:
        questions, fallback_mode = guarantee_virtual_question_count(
            generate_deterministic_virtual_questions(user, VIRTUAL_QUESTION_COUNT * 2),
            user,
            VIRTUAL_QUESTION_COUNT,
            excluded_questions=excluded_questions
        )
        last_error = {"error": "Virtual question generation failed", "details": last_error, "fallback": fallback_mode}

    if not questions:
        return jsonify({"error": "Failed to generate virtual interview questions", "details": last_error}), 500

    if len(questions) < VIRTUAL_QUESTION_COUNT:
        questions, fallback_mode = guarantee_virtual_question_count(
            questions + generate_deterministic_virtual_questions(user, VIRTUAL_QUESTION_COUNT * 3),
            user,
            VIRTUAL_QUESTION_COUNT,
            excluded_questions=excluded_questions
        )
        last_error = {"error": "Virtual question fill used", "details": last_error, "fallback": fallback_mode}

    if len(questions) < VIRTUAL_QUESTION_COUNT:
        return jsonify({"error": "Failed to prepare enough unique virtual interview questions", "details": last_error}), 500

    started_at = utc_now()
    expires_at = started_at + timedelta(seconds=VIRTUAL_TEST_DURATION_SECONDS)

    users.update_one(
        {"_id": ObjectId(session["candidate_id"])},
        {"$set": {
            "virtual_questions": questions,
            "virtual_started_at": started_at,
            "virtual_expires_at": expires_at,
            "virtual_test_duration_seconds": VIRTUAL_TEST_DURATION_SECONDS,
            "updated_at": utc_now()
        }}
    )

    return jsonify({
        "questions": questions,
        "total_questions": len(questions),
        "duration_seconds": VIRTUAL_TEST_DURATION_SECONDS,
        "expires_at": expires_at.isoformat(),
        "generation_info": last_error
    })


@app.route("/api/virtual/avatar_question", methods=["POST"])
def generate_virtual_avatar_question():

    if not session.get("candidate_id"):
        return jsonify({"error": "Unauthorized"}), 403

    user = users.find_one({"_id": ObjectId(session["candidate_id"])})
    if not user:
        return jsonify({"error": "Candidate not found"}), 404
    if not user.get("interview_taken"):
        return jsonify({"error": "Complete MCQ interview first"}), 400
    if not user.get("virtual_round_enabled"):
        return jsonify({"error": "Virtual round is not enabled by HR"}), 400
    if user.get("virtual_taken"):
        return jsonify({"error": "Virtual interview already completed"}), 400

    data = request.get_json() or {}
    question = str(data.get("question", "")).strip()
    if not question:
        return jsonify({"error": "Question text is required"}), 400
    allowed_questions = [str(q).strip() for q in user.get("virtual_questions", [])]
    if allowed_questions and question not in allowed_questions:
        return jsonify({"error": "Question is not part of this interview session"}), 400

    video_url, err = generate_did_talk_video(question)
    if not video_url:
        return jsonify({"error": "Failed to generate avatar video", "details": err}), 500

    return jsonify({"video_url": video_url})


@app.route("/api/virtual/respond", methods=["POST"])
def virtual_interviewer_response():

    if not session.get("candidate_id"):
        return jsonify({"error": "Unauthorized"}), 403

    user = users.find_one({"_id": ObjectId(session["candidate_id"])})
    if not user:
        return jsonify({"error": "Candidate not found"}), 404
    demo_user = is_demo_candidate(user)
    demo_private_mode = demo_user and not Config.DEMO_PERSIST_TEST_DATA
    if not user.get("virtual_round_enabled"):
        return jsonify({"error": "Virtual round is not enabled"}), 400
    if user.get("virtual_taken"):
        return jsonify({"error": "Virtual interview already submitted"}), 400

    data = request.get_json() or {}
    question = str(data.get("question", "")).strip()
    answer = str(data.get("answer", "")).strip()
    if not question or not answer:
        return jsonify({"error": "Question and answer are required"}), 400

    prompt = f"""
You are an HR interviewer in a virtual interview.
Question asked: {question}
Candidate answer: {answer}

Return a short spoken response in 1-2 sentences:
- acknowledge the answer
- give brief constructive feedback
- keep it professional and concise
Do not use markdown.
"""

    answer_metrics = analyze_virtual_answer_metrics(question, answer, user)
    next_index = int(data.get("next_index", -1))
    adaptive_next_question = None
    adaptive_difficulty = answer_metrics.get("difficulty", "foundation")
    current_questions = [str(q or "").strip() for q in user.get("virtual_questions", []) if str(q or "").strip()]
    used_question_history = current_questions[:]
    used_question_history.extend(get_recent_virtual_question_texts(limit=80))
    used_question_history.extend(get_recent_mcq_question_texts(limit=60))
    used_question_history.extend([str(q.get("question", "")).strip() for q in user.get("questions_data", []) if isinstance(q, dict)])

    preferred_provider = "groq"
    response_text = None
    last_error = None
    for _ in range(2):
        if preferred_provider == "groq":
            response_text, err = query_groq_text(
                prompt,
                system_message="You are a professional HR interviewer.",
                model_name=Config.GROQ_TEXT_MODEL,
                max_tokens=200,
                temperature=0.4
            )
            if not response_text and Config.MCQ_USE_OLLAMA:
                fallback_text, fallback_err = query_ollama(prompt, model_name=Config.OLLAMA_MODEL)
                if fallback_text:
                    response_text = fallback_text
                    err = None
                else:
                    err = {"preferred_error": err, "fallback_error": fallback_err}
        elif preferred_provider == "ollama":
            response_text, err = query_ollama(prompt)
        if response_text:
            break
        last_error = err

    if not response_text:
        return jsonify({"error": "Failed to generate interviewer response", "details": last_error}), 500

    if current_questions and 0 <= next_index < len(current_questions):
        adaptive_next_question = generate_adaptive_virtual_question(
            user,
            question,
            answer,
            adaptive_difficulty,
            next_index + 1,
            used_questions=used_question_history
        )
        if adaptive_next_question:
            current_questions[next_index] = adaptive_next_question
            users.update_one(
                {"_id": user["_id"]},
                {"$set": {
                    "virtual_questions": current_questions,
                    "virtual_last_answer_metrics": answer_metrics,
                    "updated_at": utc_now()
                }}
            )

    cleaned = str(response_text).replace("```", "").strip()
    return jsonify({
        "response_text": cleaned[:500],
        "answer_metrics": answer_metrics,
        "next_question": adaptive_next_question,
        "next_difficulty": adaptive_difficulty
    })


@app.route("/api/virtual/submit", methods=["POST"])
def submit_virtual():

    if not session.get("candidate_id"):
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json() or {}
    answers = data.get("answers", [])
    duration_seconds = int(data.get("duration_seconds", 0))
    proctoring_violations = int(data.get("proctoring_violations", 0) or 0)
    auto_submitted = bool(data.get("auto_submitted", False))

    user = users.find_one({"_id": ObjectId(session["candidate_id"])})
    if not user:
        return jsonify({"error": "Candidate not found"}), 404
    demo_user = is_demo_candidate(user)
    demo_private_mode = demo_user and not Config.DEMO_PERSIST_TEST_DATA
    if not user.get("virtual_round_enabled"):
        return jsonify({"error": "Virtual round is not enabled"}), 400
    if user.get("virtual_taken"):
        return jsonify({"error": "Virtual interview already submitted"}), 400

    if not isinstance(answers, list):
        return jsonify({"error": "Virtual answers format is invalid"}), 400

    server_duration_seconds = elapsed_seconds_since(user.get("virtual_started_at"))
    if server_duration_seconds > 0:
        duration_seconds = server_duration_seconds
    virtual_time_expired = duration_seconds > int(user.get("virtual_test_duration_seconds", VIRTUAL_TEST_DURATION_SECONDS))
    if virtual_time_expired:
        auto_submitted = True

    questions = [str(q or "").strip() for q in user.get("virtual_questions", []) if str(q or "").strip()]
    normalized_answers = [str(a or "").strip() for a in answers]
    if questions and len(normalized_answers) < len(questions):
        normalized_answers.extend([""] * (len(questions) - len(normalized_answers)))
    if not normalized_answers:
        normalized_answers = [""] * (len(questions) if questions else 1)
    answered_count = sum(1 for a in normalized_answers if a)

    evaluation_prompt = f"""
Evaluate the candidate's answers for the following interview questions.
Provide:
1) Overall score out of 10
2) One short feedback paragraph

Questions: {questions}
Answers: {normalized_answers}

Important:
- Evaluate based on answered responses only.
- Ignore unanswered/empty responses while scoring.
- Keep scoring strict and interview-grade.

Return ONLY valid JSON:
{{
  "score": 0,
  "feedback": "short feedback text"
}}
"""

    score = 0.0
    feedback = "Virtual interview completed."
    evaluation_meta = {"source": "local"}
    evaluator_error = None
    local_report = build_virtual_report_locally(user, questions, normalized_answers)
    report = dict(local_report)
    report_meta = {"source": "local"}

    if answered_count > 0:
        parsed, meta = evaluate_virtual_submission_with_fallback(evaluation_prompt)
        if isinstance(parsed, dict):
            raw_score = parsed.get("score", 5)
            try:
                score = float(raw_score)
            except Exception:
                score = 5.0
            feedback = str(parsed.get("feedback", feedback)).strip() or feedback
            evaluation_meta = meta or evaluation_meta
        else:
            score, feedback = local_virtual_scoring(questions, normalized_answers)
            evaluation_meta = meta or evaluation_meta
            evaluator_error = meta.get("errors") if isinstance(meta, dict) else None

        ai_report, ai_report_meta = evaluate_virtual_report_with_ai(user, questions, normalized_answers)
        if isinstance(ai_report, dict):
            report.update({
                "semantic_analysis": clamp_score(ai_report.get("semantic_analysis", report["semantic_analysis"])),
                "nlp_evaluation": clamp_score(ai_report.get("nlp_evaluation", report["nlp_evaluation"])),
                "confidence": clamp_score(ai_report.get("confidence", report["confidence"])),
                "problem_solving": clamp_score(ai_report.get("problem_solving", report["problem_solving"])),
                "communication": clamp_score(ai_report.get("communication", report["communication"])),
                "overall_performance": clamp_score(ai_report.get("overall_performance", report["overall_performance"])),
                "difficulty_progression": str(ai_report.get("difficulty_progression", report["difficulty_progression"])).strip() or report["difficulty_progression"],
                "performance_summary": str(ai_report.get("performance_summary", report["performance_summary"])).strip() or report["performance_summary"],
                "strengths": [str(item).strip() for item in (ai_report.get("strengths") or report["strengths"]) if str(item).strip()][:3],
                "improvements": [str(item).strip() for item in (ai_report.get("improvements") or report["improvements"]) if str(item).strip()][:3],
                "recommendation": str(ai_report.get("recommendation", report["recommendation"])).strip() or report["recommendation"]
            })
            report_meta = ai_report_meta or report_meta
    else:
        score, feedback = local_virtual_scoring(questions, normalized_answers)
        if auto_submitted:
            feedback = "Virtual interview was auto-submitted due to proctoring policy. " + feedback

    score = max(0.0, min(10.0, round(score, 1)))
    report["overall_performance"] = clamp_score(report.get("overall_performance", score))
    report["final_score"] = score
    report["feedback"] = feedback
    report["answered_count"] = answered_count
    report["total_questions"] = len(questions) if questions else len(normalized_answers)
    if auto_submitted:
        report["performance_summary"] = "Interview was auto-submitted by proctoring policy. " + report["performance_summary"]

    if demo_private_mode:
        reset_demo_candidate_workflow(user["_id"])
        sent = False
        completion_email_error = None
    else:
        virtual_update = {
            "virtual_taken": True,
            "virtual_score": score,
            "virtual_answers": normalized_answers,
            "virtual_feedback": feedback,
            "virtual_duration_seconds": max(0, duration_seconds),
            "virtual_time_expired": bool(virtual_time_expired),
            "virtual_auto_submitted": bool(auto_submitted),
            "virtual_proctoring_violations": max(0, proctoring_violations),
            "virtual_answered_count": answered_count,
            "virtual_evaluation_source": evaluation_meta.get("source"),
            "virtual_evaluation_model": evaluation_meta.get("model"),
            "virtual_evaluation_error": evaluator_error,
            "virtual_report": report,
            "virtual_report_source": report_meta.get("source"),
            "virtual_report_model": report_meta.get("model"),
            "virtual_completed_at": utc_now(),
            "updated_at": utc_now()
        }
        report_snapshot = dict(user)
        report_snapshot.update(virtual_update)
        virtual_update["candidate_report"] = build_candidate_report(
            report_snapshot,
            interview_evaluation=report,
            proctoring_summary={
                "violation_count": max(0, proctoring_violations),
                "critical_flags": ["Violation limit reached"] if max(0, proctoring_violations) >= 3 else []
            }
        )
        users.update_one(
            {"_id": ObjectId(session["candidate_id"])},
            {"$set": virtual_update}
        )

        completion_email_error = None
        sent, completion_email_error = send_email(
            user["email"],
            "zyra Interview Completion Update",
            f"""
Hello {user['first_name']},

Thank you for applying to zyra and completing your AI avatar interview for the {user.get('job_role', 'selected')} role.

We have received your responses successfully. Our team will review the results and get back to you soon.

Regards,
zyra HR
"""
        )

    return jsonify({
        "message": "Virtual interview submitted",
        "duration_seconds": max(0, duration_seconds),
        "time_expired": bool(virtual_time_expired),
        "auto_submitted": bool(auto_submitted),
        "email_error": completion_email_error if not sent else None
    })


# -------------------------------
# CANDIDATE LOGIN
# -------------------------------
@app.route("/api/candidate/login", methods=["POST"])
def candidate_login():
    data = request.get_json() or {}
    if not mongo_is_available():
        return jsonify(mongo_unavailable_payload()), 503

    safe_ensure_demo_candidate()

    username = str(data.get("username", "")).strip().lower()
    password = str(data.get("password", "")).strip()
    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400

    try:
        user = users.find_one({"username": username})
    except (ServerSelectionTimeoutError, PyMongoError) as e:
        return jsonify({
            "error": "Database connection failed",
            "details": str(e)
        }), 503

    if not user:
        return jsonify({"error": "Invalid username"}), 401

    if not check_password_hash(user["password"], password):
        return jsonify({"error": "Invalid password"}), 401
    demo_user = is_demo_candidate(user)
    demo_needs_reset = (
        demo_user and (
            user.get("virtual_taken")
            or str(user.get("status") or "").strip().lower() != "selected"
            or (user.get("interview_taken") and not user.get("virtual_round_enabled"))
        )
    )
    if demo_needs_reset:
        user = reset_demo_candidate_workflow(user["_id"]) or user
    elif demo_user and not Config.DEMO_PERSIST_TEST_DATA and not user.get("interview_taken"):
        cleanup_demo_candidate_artifacts(user["_id"])
    if user.get("virtual_taken"):
        return jsonify({
            "error": "All stages were already submitted. Further candidate logins are disabled."
        }), 403

    login_limit = max(1, int(Config.DEMO_LOGIN_LIMIT if demo_user else (user.get("credential_login_limit", Config.MAX_LOGIN_ATTEMPTS) or Config.MAX_LOGIN_ATTEMPTS)))
    login_count = max(0, int(user.get("credential_login_count", 0) or 0))
    if not demo_user and login_count >= login_limit:
        return jsonify({
            "error": "Login limit exceeded. Please contact customer care."
        }), 403

    login_count = 0 if demo_user else login_count + 1
    users.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "credential_login_limit": login_limit,
                "credential_login_count": login_count,
                "workflow_demo_override": bool(Config.DEMO_ALWAYS_PROMOTE) if demo_user else bool(user.get("workflow_demo_override", False)),
                "demo_user": True if demo_user else bool(user.get("demo_user", False)),
                "updated_at": utc_now()
            }
        }
    )

    session.clear()
    session["candidate_id"] = str(user["_id"])

    return jsonify({
        "message": "Login successful",
        "job_role": user.get("job_role"),
        "assessment_track": user.get("assessment_track", resolve_assessment_track(user.get("job_role"), user.get("skills"), user)),
        "stage_count": int(user.get("stage_count") or resolve_stage_count(user.get("job_role"), user.get("skills"), user)),
        "remaining_login_uses": max(0, login_limit - login_count),
        "interview_taken": user.get("interview_taken", False),
        "score": user.get("score"),
        "mcq_total_questions": user.get("mcq_total_questions", MCQ_QUESTION_COUNT),
        "coding_round_enabled": user.get("coding_round_enabled", False),
        "coding_taken": user.get("coding_taken", False),
        "coding_score": user.get("coding_score"),
        "coding_feedback": user.get("coding_feedback"),
        "status": user.get("status"),
        "virtual_round_enabled": user.get("virtual_round_enabled", False),
        "virtual_taken": user.get("virtual_taken", False),
        "virtual_decision": user.get("virtual_decision", "pending"),
        "bias_review_required": user.get("bias_review_required", False),
        "workflow_demo_override": user.get("workflow_demo_override", False),
        "virtual_question_count": len(user.get("virtual_questions", [])) if isinstance(user.get("virtual_questions"), list) else VIRTUAL_QUESTION_COUNT
    })


# -------------------------------
# START TEST
# -------------------------------
@app.route("/api/start_test", methods=["POST"])
def start_test():

    if not session.get("candidate_id"):
        return jsonify({"error": "Unauthorized"}), 403

    user = users.find_one({"_id": ObjectId(session["candidate_id"])})
    if not user:
        return jsonify({"error": "Candidate not found"}), 404
    if user.get("status") == "rejected":
        return jsonify({"error": "Your candidature is currently on hold. We will get back to you."}), 403

    if user.get("interview_taken"):
        return jsonify({"error": "Interview already taken"}), 400

    session_seed = uuid.uuid4().hex[:12]
    recent_question_bank = get_recent_mcq_question_texts(limit=120)

    try:
        questions_data, last_error = generate_mcq_questions_with_fallback(
            user,
            MCQ_QUESTION_COUNT,
            session_seed=session_seed,
            excluded_questions=recent_question_bank
        )
    except Exception as e:
        print("START TEST ERROR:", str(e))
        questions_data = None
        last_error = {"error": "MCQ generation exception", "details": str(e)}

    if not questions_data:
        print("MCQ GENERATION ERROR:", last_error)
        # Hard fallback to guarantee interview continuity.
        deterministic = generate_deterministic_mcq(
            user,
            MCQ_QUESTION_COUNT,
            session_seed=session_seed,
            excluded_questions=recent_question_bank
        )
        questions_data = [
            {
                "id": idx + 1,
                "question": q["question"],
                "options": q["options"],
                "answer": q["answer"]
            }
            for idx, q in enumerate(deterministic[:MCQ_QUESTION_COUNT])
        ]

    test_id = str(uuid.uuid4())
    started_at = utc_now()
    expires_at = started_at + timedelta(seconds=MCQ_TEST_DURATION_SECONDS)

    tests.insert_one({
        "test_id": test_id,
        "user_id": session["candidate_id"],
        "variation_seed": session_seed,
        "created_at": started_at,
        "expires_at": expires_at,
        "duration_seconds": MCQ_TEST_DURATION_SECONDS,
        "questions": questions_data
    })

    questions = [
        {
            "id": q["id"],
            "question": q["question"],
            "options": q["options"]
        }
        for q in questions_data
    ]

    return jsonify({
        "test_id": test_id,
        "questions": questions,
        "total_questions": len(questions),
        "duration_seconds": MCQ_TEST_DURATION_SECONDS,
        "expires_at": expires_at.isoformat()
    })


# -------------------------------
# SUBMIT TEST
# -------------------------------
@app.route("/api/submit_test", methods=["POST"])
def submit_test():

    if not session.get("candidate_id"):
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json() or {}
    test_id = str(data.get("test_id", "")).strip()
    answers = data.get("answers", [])
    auto_submitted = bool(data.get("auto_submitted", False))
    if not test_id:
        return jsonify({"error": "test_id is required"}), 400
    if not isinstance(answers, list):
        return jsonify({"error": "answers must be a list"}), 400

    test = tests.find_one({"test_id": test_id})
    if not test:
        return jsonify({"error": "Invalid test session"}), 400

    score_raw = 0
    total_questions = len(test.get("questions", []))
    proctoring_violations = int(data.get("proctoring_violations", 0) or 0)
    elapsed_seconds = elapsed_seconds_since(test.get("created_at"))
    test_duration_seconds = int(test.get("duration_seconds", MCQ_TEST_DURATION_SECONDS) or MCQ_TEST_DURATION_SECONDS)
    mcq_time_expired = elapsed_seconds > test_duration_seconds
    if mcq_time_expired:
        auto_submitted = True

    for q in test["questions"]:
        for ans in answers:
            if ans["id"] == q["id"] and ans["answer"] == q["answer"]:
                score_raw += 1

    score = round((score_raw / total_questions) * 10, 1) if total_questions else 0.0
    score_percent = round((score_raw / total_questions) * 100, 1) if total_questions else 0.0
    user = users.find_one({"_id": ObjectId(session["candidate_id"])})
    if not user:
        return jsonify({"error": "Candidate not found"}), 404
    demo_user = is_demo_candidate(user)
    demo_private_mode = demo_user and not Config.DEMO_PERSIST_TEST_DATA
    auto_qualified = score_percent >= MCQ_PROMOTION_THRESHOLD_PERCENT or (demo_user and Config.DEMO_ALWAYS_PROMOTE) or bool(user.get("workflow_demo_override"))
    mcq_update = {
        "interview_taken": True,
        "score": score,
        "mcq_score_percent": score_percent,
        "mcq_raw_score": score_raw,
        "mcq_total_questions": total_questions,
        "mcq_duration_seconds": elapsed_seconds,
        "mcq_time_expired": bool(mcq_time_expired),
        "mcq_auto_submitted": bool(auto_submitted),
        "mcq_proctoring_violations": max(0, proctoring_violations),
        "candidate_answers": [] if demo_private_mode else answers,
        "questions_data": [] if demo_private_mode else test["questions"],
        "mcq_completed_at": utc_now(),
        "coding_round_enabled": False,
        "coding_taken": False,
        "coding_score": None,
        "coding_feedback": None,
        "coding_questions": [],
        "coding_answers": [],
        "coding_duration_seconds": None,
        "virtual_round_enabled": bool(auto_qualified),
        "virtual_taken": False,
        "virtual_score": None,
        "virtual_questions": [],
        "virtual_answers": [],
        "virtual_feedback": None,
        "virtual_duration_seconds": None,
        "virtual_decision": "promoted" if auto_qualified else "pending",
        "bias_review_required": not auto_qualified,
        "updated_at": utc_now()
    }
    if auto_qualified:
        mcq_update["credential_login_count"] = 0
        mcq_update["credential_login_limit"] = max(1, int(Config.MAX_LOGIN_ATTEMPTS))
    mcq_snapshot = dict(user)
    mcq_snapshot.update(mcq_update)
    mcq_update["candidate_report"] = build_candidate_report(mcq_snapshot)
    users.update_one({"_id": ObjectId(session["candidate_id"])}, {"$set": mcq_update})
    tests.update_one(
        {"test_id": test_id},
        {"$set": {
            "submitted_at": utc_now(),
            "elapsed_seconds": elapsed_seconds,
            "time_expired": bool(mcq_time_expired),
            "auto_submitted": bool(auto_submitted),
            "score_percent": score_percent,
            "raw_score": score_raw
        }}
    )
    if demo_private_mode:
        tests.delete_many({"user_id": str(user["_id"])})

    email_error = None
    if auto_qualified and not demo_user:
        next_round_label = "AI Avatar Virtual Interview"
        sent, email_error = send_email(
            user["email"],
            f"zyra {next_round_label}",
            f"""
Hello {user['first_name']},

Congratulations! You scored {score_percent}% in the MCQ round, which meets the {MCQ_PROMOTION_THRESHOLD_PERCENT:.0f}% promotion criteria.

Your profile has been promoted automatically to the next round. Please log in and complete your {next_round_label}.

Regards,
zyra HR
"""
        )
        if not sent:
            email_error = email_error or "Failed to send promotion email"

    return jsonify({
        "score": score,
        "score_percent": score_percent,
        "raw_score": score_raw,
        "total_questions": total_questions,
        "promoted_to_virtual": bool(auto_qualified),
        "promoted_to_coding": False,
        "next_stage": "virtual" if auto_qualified else "bias_review",
        "promotion_threshold_percent": MCQ_PROMOTION_THRESHOLD_PERCENT,
        "duration_seconds": elapsed_seconds,
        "time_expired": bool(mcq_time_expired),
        "auto_submitted": bool(auto_submitted),
        "email_error": email_error
    })


@app.route("/api/coding/start", methods=["POST"])
def start_coding_round():
    if not session.get("candidate_id"):
        return jsonify({"error": "Unauthorized"}), 403

    user = users.find_one({"_id": ObjectId(session["candidate_id"])})
    if not user:
        return jsonify({"error": "Candidate not found"}), 404
    if user.get("assessment_track") != "technical":
        return jsonify({"error": "Coding round is only enabled for technical roles"}), 400
    if not user.get("interview_taken"):
        return jsonify({"error": "Complete the MCQ round first"}), 400
    if user.get("coding_taken"):
        return jsonify({"error": "Coding round already submitted"}), 400
    if not user.get("coding_round_enabled"):
        return jsonify({"error": "Coding round is not enabled yet"}), 400

    existing = coding_tests.find_one({"user_id": session["candidate_id"], "submitted": False})
    if existing:
        return jsonify({
            "test_id": existing["test_id"],
            "questions": existing.get("questions", []),
            "total_questions": len(existing.get("questions", []))
        })

    question_count = 2
    questions, _ = generate_coding_questions_with_fallback(user, question_count)
    if not questions:
        questions = generate_deterministic_coding_questions(user, question_count)
    questions = normalize_coding_questions(questions)

    test_id = str(uuid.uuid4())
    coding_tests.insert_one({
        "test_id": test_id,
        "user_id": session["candidate_id"],
        "created_at": utc_now(),
        "submitted": False,
        "questions": questions,
    })

    users.update_one(
        {"_id": user["_id"]},
        {"$set": {"coding_questions": questions, "updated_at": utc_now()}}
    )

    return jsonify({
        "test_id": test_id,
        "questions": questions,
        "total_questions": len(questions)
    })


@app.route("/api/coding/submit", methods=["POST"])
def submit_coding_round():
    if not session.get("candidate_id"):
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json() or {}
    test_id = str(data.get("test_id", "")).strip()
    answers = data.get("answers", [])
    duration_seconds = int(data.get("duration_seconds", 0) or 0)
    proctoring_violations = int(data.get("proctoring_violations", 0) or 0)

    if not test_id:
        return jsonify({"error": "test_id is required"}), 400
    if not isinstance(answers, list):
        return jsonify({"error": "answers must be a list"}), 400

    user = users.find_one({"_id": ObjectId(session["candidate_id"])})
    if not user:
        return jsonify({"error": "Candidate not found"}), 404

    coding_test = coding_tests.find_one({"test_id": test_id, "user_id": session["candidate_id"]})
    if not coding_test:
        return jsonify({"error": "Coding session not found"}), 404
    if coding_test.get("submitted"):
        return jsonify({"error": "Coding round already submitted"}), 400

    evaluation, meta = evaluate_coding_submission_with_fallback(user, coding_test.get("questions", []), answers)
    score = max(0.0, min(10.0, round(float(evaluation.get("score", 0) or 0), 1)))
    feedback = str(evaluation.get("feedback", "")).strip() or "Coding round submitted successfully."

    coding_update = {
        "coding_taken": True,
        "coding_round_enabled": False,
        "coding_score": score,
        "coding_feedback": feedback,
        "coding_answers": answers,
        "coding_duration_seconds": max(0, duration_seconds),
        "coding_proctoring_violations": max(0, proctoring_violations),
        "virtual_round_enabled": True,
        "virtual_decision": "promoted",
        "updated_at": utc_now(),
    }
    coding_snapshot = dict(user)
    coding_snapshot.update(coding_update)
    coding_update["candidate_report"] = build_candidate_report(coding_snapshot)

    users.update_one({"_id": user["_id"]}, {"$set": coding_update})
    coding_tests.update_one(
        {"_id": coding_test["_id"]},
        {"$set": {"submitted": True, "answers": answers, "score": score, "feedback": feedback, "updated_at": utc_now()}}
    )

    return jsonify({
        "score": score,
        "feedback": feedback,
        "strengths": evaluation.get("strengths", []),
        "improvements": evaluation.get("improvements", []),
        "next_stage": "virtual",
        "provider": meta.get("source"),
    })


@app.route("/api/proctoring/upload", methods=["POST"])
def upload_proctoring_recording():
    if not session.get("candidate_id"):
        return jsonify({"error": "Unauthorized"}), 403

    user = users.find_one({"_id": ObjectId(session["candidate_id"])})
    if is_demo_candidate(user) and not Config.DEMO_PERSIST_TEST_DATA:
        return jsonify({
            "message": "Demo proctoring recording skipped",
            "storage_provider": "demo_skip",
            "video_url": None,
            "mongo_file_id": None
        })

    video = request.files.get("video")
    if not video or not video.filename:
        return jsonify({"error": "Proctoring video is required"}), 400

    assessment_type = str(request.form.get("assessment_type", "assessment")).strip().lower()
    violations = int(request.form.get("violations", 0) or 0)
    multiple_user_events = int(request.form.get("multiple_user_events", 0) or 0)
    metadata = request.form.get("metadata", "{}")

    video_url, upload_error = upload_proctoring_video_to_cloudinary(video, assessment_type)
    storage_provider = "cloudinary"
    mongo_file_id = None
    if not video_url:
        mongo_file_id, mongo_error = store_proctoring_video_in_mongodb(video, assessment_type)
        storage_provider = "mongodb"
        if not mongo_file_id:
            return jsonify({
                "error": "Proctoring upload failed",
                "details": upload_error,
                "mongodb_details": mongo_error
            }), 500

    document = {
        "user_id": session["candidate_id"],
        "assessment_type": assessment_type,
        "storage_provider": storage_provider,
        "video_url": video_url,
        "mongo_file_id": mongo_file_id,
        "violations": max(0, violations),
        "multiple_user_events": max(0, multiple_user_events),
        "metadata": metadata,
        "created_at": utc_now()
    }
    db.proctoring_recordings.insert_one(document)
    return jsonify({
        "message": "Proctoring recording uploaded",
        "storage_provider": storage_provider,
        "video_url": video_url,
        "mongo_file_id": mongo_file_id
    })

@app.route("/api/logout")
def logout():
    session.clear()
    return jsonify({"message":"Logged out"})


@app.route("/api/session/status")
def session_status():
    is_admin = bool(session.get("admin"))
    candidate_id = session.get("candidate_id")
    role = None
    candidate_state = {}

    if is_admin:
        role = get_staff_role() or "admin"
    elif candidate_id:
        role = "candidate"
        try:
            user = users.find_one({"_id": ObjectId(candidate_id)})
        except Exception:
            user = None
        if user:
            candidate_state = {
                "interview_taken": bool(user.get("interview_taken", False)),
                "coding_round_enabled": bool(user.get("coding_round_enabled", False)),
                "coding_taken": bool(user.get("coding_taken", False)),
                "virtual_round_enabled": bool(user.get("virtual_round_enabled", False)),
                "virtual_taken": bool(user.get("virtual_taken", False))
            }

    return jsonify({
        "logged_in": bool(role),
        "role": role,
        "candidate_state": candidate_state
    })

# ================================
# REGISTER INTERVIEW V2 ROUTES
# ================================
try:
    register_interview_routes(app, db)
except Exception as e:
    print("⚠ Warning: Interview V2 routes registration failed:", str(e))

try:
    safe_ensure_demo_candidate()
except Exception as e:
    print("Warning: demo candidate seed failed:", str(e))

if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG", "false").lower() == "true")
