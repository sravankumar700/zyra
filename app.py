import os
import uuid
import smtplib
import re
import json
import random
import requests
from datetime import datetime, timezone
from email.message import EmailMessage
from urllib.parse import unquote

from flask import Flask, render_template, request, jsonify, session, redirect
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError, PyMongoError
from bson.objectid import ObjectId
from config import Config
from ai.hf_generator import generate_mcq
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


app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "super_secret_key")
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
    "serverSelectionTimeoutMS": int(os.getenv("MONGO_SERVER_SELECTION_TIMEOUT_MS", "15000")),
    "connectTimeoutMS": int(os.getenv("MONGO_CONNECT_TIMEOUT_MS", "15000")),
    "socketTimeoutMS": int(os.getenv("MONGO_SOCKET_TIMEOUT_MS", "20000"))
}
if Config.MONGO_URI.startswith("mongodb+srv://"):
    mongo_kwargs["tls"] = True
    mongo_kwargs["tlsCAFile"] = certifi.where()

client = MongoClient(Config.MONGO_URI, **mongo_kwargs)
db = client[Config.MONGO_DB]

try:
    client.admin.command("ping")
except Exception as e:
    print("MongoDB ping failed on startup:", str(e))

applications = db.applications
users = db.users
tests = db.tests
jobs = db.jobs
MCQ_QUESTION_COUNT = max(10, int(os.getenv("MCQ_QUESTION_COUNT", "12")))
VIRTUAL_QUESTION_COUNT = max(5, int(os.getenv("VIRTUAL_QUESTION_COUNT", "7")))
MCQ_PROMOTION_THRESHOLD_PERCENT = float(os.getenv("MCQ_PROMOTION_THRESHOLD_PERCENT", "60"))


def utc_now():
    return datetime.now(timezone.utc)


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
        "threshold": 72,
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
        "threshold": 76,
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
        "threshold": 73,
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
        "threshold": 78,
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
        "threshold": 75,
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
        "threshold": 78,
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
        "threshold": 72,
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
        "threshold": 76,
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
        "threshold": 71,
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
        "threshold": 77,
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
        "threshold": 73,
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
        "threshold": 77,
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
        "threshold": 72,
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
        "threshold": 75,
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
        "threshold": 70,
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
        "threshold": 76,
        "description": "Lead hotel operations, service standards, and team performance to deliver strong guest satisfaction.",
        "created_by_role": "system",
        "seed_source": "zyra_top_professions_v1",
        "created_at": utc_now()
    }
]


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
        if LEGACY_DEFAULT_JOB_IDS:
            jobs.delete_many({"id": {"$in": list(LEGACY_DEFAULT_JOB_IDS)}})
        for job in DEFAULT_JOBS:
            jobs.update_one({"id": job["id"]}, {"$set": job}, upsert=True)
    except Exception as e:
        print("Job seeding skipped:", str(e))


def create_candidate_account_from_application(app_data):
    username = f"{str(app_data.get('first_name', 'candidate')).lower()}.{uuid.uuid4().hex[:4]}"
    raw_password = uuid.uuid4().hex[:8]

    user_document = {
        "application_id": str(app_data["_id"]),
        "first_name": app_data["first_name"],
        "last_name": app_data["last_name"],
        "email": app_data["email"],
        "phone": app_data["phone"],
        "skills": app_data["skills"],
        "job_role": app_data["job_role"],
        "resume": app_data["resume"],
        "username": username.lower(),
        "credential_username": username.lower(),
        "credential_plaintext": raw_password,
        "password": generate_password_hash(raw_password),
        "credential_login_limit": 1,
        "credential_login_count": 0,
        "interview_taken": False,
        "score": None,
        "status": "selected",
        "virtual_round_enabled": False,
        "virtual_taken": False,
        "virtual_score": None,
        "virtual_questions": [],
        "virtual_answers": [],
        "virtual_decision": "pending",
        "virtual_feedback": None,
        "virtual_duration_seconds": None,
        "mcq_completed_at": None,
        "updated_at": utc_now()
    }
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

Login at: http://127.0.0.1:5000

Regards,
zyra HR
"""
    return send_email(user["email"], subject, body)


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

    provider_order = ["ollama", "hf"] if Config.MCQ_USE_OLLAMA else ["hf", "ollama"]

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
            if provider == "ollama":
                candidate_result, err = generate_mcq_with_ollama(prompt, batch_size)
                if candidate_result:
                    result = candidate_result
                    break
                batch_errors.append({"provider": "ollama", "details": err})
            else:
                candidate_result = generate_mcq(prompt, batch_size)
                if candidate_result and not (isinstance(candidate_result, dict) and candidate_result.get("error")):
                    result = candidate_result
                    break
                batch_errors.append({"provider": "hf", "details": candidate_result})

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


def normalize_virtual_questions(raw_questions, total_count):
    normalized = []
    seen = set()
    for q in raw_questions or []:
        text = re.sub(r"\s+", " ", str(q or "").strip())
        if not text:
            continue
        if len(text) < 18:
            continue
        dedupe_key = text.lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        normalized.append(text)
        if len(normalized) >= total_count:
            break
    return normalized


def generate_virtual_questions_with_fallback(user, total_count):
    prompt = f"""
Generate exactly {total_count} high-quality virtual interview questions for this candidate.
Candidate skills: {user.get('skills')}
Candidate role: {user.get('job_role')}

Question quality rules:
- Include practical, scenario-based and behavioral questions.
- Test depth, communication, and problem-solving.
- Avoid duplicate or generic questions.
- Keep each question concise and interview-ready.

Return ONLY valid JSON:
{{
  "questions": ["Question 1", "Question 2"]
}}
"""

    providers = ["ollama", "hf"] if Config.USE_LOCAL_VIRTUAL_MODEL else ["hf", "ollama"]
    hf_models = []
    for m in [Config.VIRTUAL_HF_MODEL, Config.MODEL, Config.MCQ_SECONDARY_MODEL, Config.MCQ_TERTIARY_MODEL]:
        if m and m not in hf_models:
            hf_models.append(m)
    max_hf_models = max(1, int(os.getenv("VIRTUAL_HF_MAX_MODELS", "2")))
    hf_models = hf_models[:max_hf_models]

    errors = []
    best_ai_questions = []

    for provider in providers:
        if provider == "ollama":
            content, err = query_ollama(prompt, model_name=Config.OLLAMA_MODEL)
            if not content:
                errors.append({"provider": "ollama", "details": err})
                continue

            questions = normalize_virtual_questions(parse_virtual_question_candidates(content), total_count)

            if len(questions) >= total_count:
                return questions[:total_count], None
            if len(questions) > len(best_ai_questions):
                best_ai_questions = questions
            errors.append({"provider": "ollama", "error": "Insufficient virtual questions", "count": len(questions)})
        else:
            for model in hf_models:
                content, err = query_hf_chat(prompt, model, max_tokens=800, request_timeout=25)
                if not content:
                    errors.append({"provider": "hf", "model": model, "details": err})
                    continue

                questions = normalize_virtual_questions(parse_virtual_question_candidates(content), total_count)

                if len(questions) >= total_count:
                    return questions[:total_count], None
                if len(questions) > len(best_ai_questions):
                    best_ai_questions = questions
                errors.append({"provider": "hf", "model": model, "error": "Insufficient virtual questions", "count": len(questions)})

    if best_ai_questions:
        deterministic_fill = generate_deterministic_virtual_questions(user, total_count)
        combined = normalize_virtual_questions(best_ai_questions + deterministic_fill, total_count)
        if len(combined) >= total_count:
            return combined[:total_count], {"fallback": "partial_ai_with_deterministic_fill", "errors": errors}

    deterministic = generate_deterministic_virtual_questions(user, total_count)
    deterministic = normalize_virtual_questions(deterministic, total_count)
    if deterministic and len(deterministic) >= total_count:
        return deterministic[:total_count], {"fallback": "deterministic", "errors": errors}
    return None, {"errors": errors}


def query_hf_chat(prompt_text, model_name, max_tokens=800, request_timeout=60):
    if not Config.HF_TOKEN:
        return None, {"provider": "hf", "error": "HF_TOKEN not configured"}
    headers = {
        "Authorization": f"Bearer {Config.HF_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": "You are an interview question generator. Output JSON only."},
            {"role": "user", "content": prompt_text}
        ],
        "temperature": 0.2,
        "max_tokens": max_tokens
    }
    try:
        response = requests.post(Config.HF_API_URL, headers=headers, json=payload, timeout=request_timeout)
    except Exception as e:
        return None, {"provider": "hf", "error": f"Request failed: {str(e)}"}

    if response.status_code != 200:
        return None, {"provider": "hf", "status_code": response.status_code, "details": response.text}

    result = response.json()
    try:
        content = result["choices"][0]["message"]["content"]
    except Exception:
        return None, {"provider": "hf", "error": "Invalid response format", "raw": result}
    return content, None


def query_hf_text(prompt_text, model_name, system_message="You are a helpful assistant.", max_tokens=250):
    if not Config.HF_TOKEN:
        return None, {"provider": "hf", "error": "HF_TOKEN not configured"}
    headers = {
        "Authorization": f"Bearer {Config.HF_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_message},
            {"role": "user", "content": prompt_text}
        ],
        "temperature": 0.3,
        "max_tokens": max_tokens
    }
    try:
        response = requests.post(Config.HF_API_URL, headers=headers, json=payload, timeout=60)
    except Exception as e:
        return None, {"provider": "hf", "error": f"Request failed: {str(e)}"}

    if response.status_code != 200:
        return None, {"provider": "hf", "status_code": response.status_code, "details": response.text}

    result = response.json()
    try:
        content = result["choices"][0]["message"]["content"]
    except Exception:
        return None, {"provider": "hf", "error": "Invalid response format", "raw": result}
    return str(content or "").strip(), None


def evaluate_virtual_submission_with_fallback(evaluation_prompt):
    models = []
    for model in [
        Config.VIRTUAL_HF_MODEL,
        Config.MODEL,
        getattr(Config, "MCQ_SECONDARY_MODEL", None),
        getattr(Config, "MCQ_TERTIARY_MODEL", None)
    ]:
        if model and model not in models:
            models.append(model)

    errors = []
    for model in models:
        content, err = query_hf_text(
            evaluation_prompt,
            model,
            system_message="You are an interview evaluator. Return valid JSON only with score and feedback.",
            max_tokens=500
        )
        if not content:
            errors.append({"model": model, "error": err})
            continue

        parsed = extract_json_block(content)
        if isinstance(parsed, dict):
            return parsed, {"source": "hf", "model": model}

        number_match = re.search(r"\d+(\.\d+)?", content or "")
        if number_match:
            try:
                score_val = float(number_match.group())
            except Exception:
                score_val = 5.0
            return {
                "score": score_val,
                "feedback": "Virtual interview completed. Detailed structured feedback unavailable."
            }, {"source": "hf", "model": model, "format": "text_fallback"}

        errors.append({
            "model": model,
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
    used_questions = [str(item or "").strip().lower() for item in (used_questions or []) if str(item or "").strip()]
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
- Make the question {target_difficulty} level
- If the candidate did well, increase depth and scenario complexity
- If the candidate struggled, keep it practical and more guided
- Keep it concise and interview-ready

Return ONLY valid JSON:
{{ "question": "..." }}
"""
    providers = ["ollama", "hf"] if Config.USE_LOCAL_VIRTUAL_MODEL else ["hf", "ollama"]
    for provider in providers:
        if provider == "ollama":
            content, err = query_ollama(prompt, model_name=Config.OLLAMA_MODEL)
        else:
            content, err = query_hf_text(
                prompt,
                Config.VIRTUAL_HF_MODEL,
                system_message="You generate one concise interview question in valid JSON.",
                max_tokens=180
            )
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
        if normalized and normalized.lower() not in used_questions:
            return normalized

    role = str(user.get("job_role", "")).strip() or "this role"
    skill = split_csv(user.get("skills"))[0] if split_csv(user.get("skills")) else role
    fallback_by_difficulty = {
        "foundation": f"Walk me through a practical situation where you used {skill} and what result you achieved.",
        "intermediate": f"Describe a challenging situation in {role} where you had to make a decision with limited time or information.",
        "advanced": f"In {role}, tell me about a high-stakes problem you solved, how you evaluated options, and why you chose your final approach."
    }
    fallback = fallback_by_difficulty.get(target_difficulty, fallback_by_difficulty["intermediate"])
    if fallback.lower() in used_questions:
        fallback = f"For {role}, explain a recent example of ownership, decision making, and measurable impact."
    return fallback


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
@app.route("/")
def home():
    ensure_default_jobs()
    return render_template("main.html")


@app.route("/register")
def register_page():
    ensure_default_jobs()
    return render_template("register.html")


@app.route("/resume-template")
def resume_template():
    return render_template("resume_template.html")

# -------------------------------
# JOBS
# -------------------------------
@app.route("/api/jobs", methods=["GET"])
def get_jobs():
    ensure_default_jobs()
    items = []
    for job in jobs.find().sort("created_at", -1):
        job["_id"] = str(job["_id"])
        items.append(job)
    return jsonify({"jobs": items})


@app.route("/api/jobs", methods=["POST"])
def create_job():
    if not is_staff_authorized("admin", "hr", "recruiter"):
        return jsonify({"error": "Unauthorized"}), 403

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

    ensure_default_jobs()
    selected_job = None
    job_id = str(data.get("job_id", "")).strip()
    if job_id:
        selected_job = jobs.find_one({"id": job_id})
        if not selected_job:
            return jsonify({"error": "Selected job was not found"}), 400

    job_role = str(data.get("job_role", "")).strip() or str((selected_job or {}).get("title", "")).strip()
    if not job_role:
        return jsonify({"error": "Job role is required"}), 400

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

    application_document = {
            "first_name": data.get("first_name"),
            "last_name": data.get("last_name"),
            "email": data.get("email"),
            "phone": data.get("phone"),
            "skills": data.get("skills"),
            "job_id": job_id or None,
            "job_role": job_role,
            "resume": resume_url,
            "resume_name": resume_name,
            "resume_analysis_text": resume_text,
            "resume_analysis_error": resume_text_error,
            "ats_score": ats["score"],
            "ats_decision": ats["decision"],
            "ats_summary": ats["summary"],
            "ats_breakdown": ats["breakdown"],
            "status": "selected" if ats["decision"] == "shortlisted" else "rejected" if ats["decision"] == "rejected" else "pending",
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

    if ats["decision"] == "shortlisted":
        try:
            username, raw_password = create_candidate_account_from_application(application_document)
            sent, email_error = send_email(
                application_document["email"],
                "zyra Interview Credentials",
                f"""
Hello {application_document['first_name']},

Congratulations! Your profile has been shortlisted automatically for the {application_document['job_role']} role.

Username: {username}
Password: {raw_password}

Login at: http://127.0.0.1:5000

Regards,
zyra HR
"""
            )
            response_message = "Application submitted and shortlisted automatically"
            if not sent:
                response_message = "Application shortlisted automatically, but email failed"
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
        "ats_score": ats["score"],
        "ats_decision": ats["decision"],
        "ats_summary": ats["summary"],
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

    pending = list(applications.find({"status": "pending"}))
    rejected = list(applications.find({"status": "rejected"}))
    selected = list(users.find())

    for c in pending:
        c["_id"] = str(c["_id"])
    for c in rejected:
        c["_id"] = str(c["_id"])
    for c in selected:
        c["_id"] = str(c["_id"])

    return jsonify({
        "pending": pending,
        "rejected": rejected,
        "selected": selected
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

Login at: http://127.0.0.1:5000

Regards,
zyra HR
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

    reset_candidate_login_usage(object_id, login_limit=1)
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

    users.update_one(
        {"_id": object_id},
        {"$set": {
            "virtual_round_enabled": True,
            "virtual_decision": "promoted",
            "status": "selected",
            "credential_login_count": 0,
            "credential_login_limit": 1,
            "updated_at": utc_now()
        }}
    )

    updated_user = users.find_one({"_id": object_id})
    sent, email_error = send_candidate_credentials_email(
        updated_user,
        "zyra Virtual Interview Round",
        [
            "Congratulations! You are shortlisted for the AI Avatar Virtual Interview Round.",
            "Please login to your dashboard and complete your 3-5 minute virtual interview.",
            "Your login credentials are below:"
        ]
    )

    if sent:
        return jsonify({"message": "Candidate promoted to virtual round and email sent"})
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

    try:
        questions, last_error = generate_virtual_questions_with_fallback(user, VIRTUAL_QUESTION_COUNT)
    except Exception as e:
        questions = generate_deterministic_virtual_questions(user, VIRTUAL_QUESTION_COUNT)
        last_error = {"error": "Virtual question generation exception", "details": str(e), "fallback": "deterministic"}

    if not questions:
        questions = generate_deterministic_virtual_questions(user, VIRTUAL_QUESTION_COUNT)
        last_error = {"error": "Virtual question generation failed", "details": last_error, "fallback": "deterministic"}

    if not questions:
        return jsonify({"error": "Failed to generate virtual interview questions", "details": last_error}), 500

    users.update_one(
        {"_id": ObjectId(session["candidate_id"])},
        {"$set": {"virtual_questions": questions, "updated_at": utc_now()}}
    )

    return jsonify({
        "questions": questions,
        "total_questions": len(questions),
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

    preferred_provider = "ollama" if Config.USE_LOCAL_VIRTUAL_MODEL else "hf"
    response_text = None
    last_error = None
    for _ in range(2):
        if preferred_provider == "ollama":
            response_text, err = query_ollama(prompt)
            if not response_text:
                fallback_text, fallback_err = query_hf_text(
                    prompt,
                    Config.VIRTUAL_HF_MODEL,
                    system_message="You are a professional HR interviewer.",
                    max_tokens=200
                )
                if fallback_text:
                    response_text = fallback_text
                    err = None
                else:
                    err = {"preferred_error": err, "fallback_error": fallback_err}
        else:
            response_text, err = query_hf_text(
                prompt,
                Config.VIRTUAL_HF_MODEL,
                system_message="You are a professional HR interviewer.",
                max_tokens=200
            )
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
            used_questions=current_questions
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
    if not user.get("virtual_round_enabled"):
        return jsonify({"error": "Virtual round is not enabled"}), 400
    if user.get("virtual_taken"):
        return jsonify({"error": "Virtual interview already submitted"}), 400

    if not isinstance(answers, list):
        return jsonify({"error": "Virtual answers format is invalid"}), 400

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

    users.update_one(
        {"_id": ObjectId(session["candidate_id"])},
        {"$set": {
            "virtual_taken": True,
            "virtual_score": score,
            "virtual_answers": normalized_answers,
            "virtual_feedback": feedback,
            "virtual_duration_seconds": max(0, duration_seconds),
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
        }}
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
        "score": score,
        "feedback": feedback,
        "report": report,
        "email_error": completion_email_error if not sent else None
    })


# -------------------------------
# CANDIDATE LOGIN
# -------------------------------
@app.route("/api/candidate/login", methods=["POST"])
def candidate_login():
    data = request.get_json() or {}

    username = str(data.get("username", "")).strip().lower()
    password = str(data.get("password", "")).strip()
    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400

    user = users.find_one({"username": username})

    if not user:
        return jsonify({"error": "Invalid username"}), 401

    if not check_password_hash(user["password"], password):
        return jsonify({"error": "Invalid password"}), 401

    login_limit = max(1, int(user.get("credential_login_limit", 2) or 2))
    login_count = max(0, int(user.get("credential_login_count", 0) or 0))
    if login_count >= login_limit:
        return jsonify({
            "error": "Login limit exceeded. Please contact customer care."
        }), 403

    login_count += 1
    users.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "credential_login_limit": login_limit,
                "credential_login_count": login_count,
                "updated_at": utc_now()
            }
        }
    )

    session["candidate_id"] = str(user["_id"])

    return jsonify({
        "message": "Login successful",
        "remaining_login_uses": max(0, login_limit - login_count),
        "interview_taken": user.get("interview_taken", False),
        "score": user.get("score"),
        "mcq_total_questions": user.get("mcq_total_questions", MCQ_QUESTION_COUNT),
        "status": user.get("status"),
        "virtual_round_enabled": user.get("virtual_round_enabled", False),
        "virtual_taken": user.get("virtual_taken", False),
        "virtual_decision": user.get("virtual_decision", "pending"),
        "virtual_score": user.get("virtual_score"),
        "virtual_feedback": user.get("virtual_feedback"),
        "virtual_report": user.get("virtual_report"),
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

    tests.insert_one({
        "test_id": test_id,
        "user_id": session["candidate_id"],
        "variation_seed": session_seed,
        "created_at": utc_now(),
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
        "total_questions": len(questions)
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

    for q in test["questions"]:
        for ans in answers:
            if ans["id"] == q["id"] and ans["answer"] == q["answer"]:
                score_raw += 1

    score = round((score_raw / total_questions) * 10, 1) if total_questions else 0.0
    score_percent = round((score_raw / total_questions) * 100, 1) if total_questions else 0.0
    promoted_to_virtual = score_percent >= MCQ_PROMOTION_THRESHOLD_PERCENT

    user = users.find_one({"_id": ObjectId(session["candidate_id"])})
    if not user:
        return jsonify({"error": "Candidate not found"}), 404

    mcq_update = {
        "interview_taken": True,
        "score": score,
        "mcq_score_percent": score_percent,
        "mcq_raw_score": score_raw,
        "mcq_total_questions": total_questions,
        "mcq_proctoring_violations": max(0, proctoring_violations),
        "candidate_answers": answers,
        "questions_data": test["questions"],
        "mcq_completed_at": utc_now(),
        "virtual_round_enabled": promoted_to_virtual,
        "virtual_taken": False,
        "virtual_score": None,
        "virtual_questions": [],
        "virtual_answers": [],
        "virtual_feedback": None,
        "virtual_duration_seconds": None,
        "virtual_decision": "promoted" if promoted_to_virtual else "pending",
        "updated_at": utc_now()
    }
    if promoted_to_virtual:
        mcq_update["credential_login_count"] = 0
        mcq_update["credential_login_limit"] = 1
    users.update_one({"_id": ObjectId(session["candidate_id"])}, {"$set": mcq_update})

    email_error = None
    if promoted_to_virtual:
        sent, email_error = send_email(
            user["email"],
            "zyra Virtual Interview Round",
            f"""
Hello {user['first_name']},

Congratulations! You scored {score_percent}% in the MCQ round, which meets the {MCQ_PROMOTION_THRESHOLD_PERCENT:.0f}% promotion criteria.

Your profile has been promoted automatically to the next round. Please log in and complete your AI Avatar Virtual Interview.

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
        "promoted_to_virtual": promoted_to_virtual,
        "promotion_threshold_percent": MCQ_PROMOTION_THRESHOLD_PERCENT,
        "email_error": email_error
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
                "virtual_round_enabled": bool(user.get("virtual_round_enabled", False)),
                "virtual_taken": bool(user.get("virtual_taken", False))
            }

    return jsonify({
        "logged_in": bool(role),
        "role": role,
        "candidate_state": candidate_state
    })

if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG", "false").lower() == "true")
