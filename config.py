# config.py
import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    USE_LOCAL_VIRTUAL_MODEL = os.getenv("USE_LOCAL_VIRTUAL_MODEL", "false").lower() == "true"
    MCQ_USE_OLLAMA = os.getenv("MCQ_USE_OLLAMA", "true").lower() == "true"
    OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/generate")
    OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "phi3:mini")
    MCQ_OLLAMA_MODEL = os.getenv("MCQ_OLLAMA_MODEL", OLLAMA_MODEL)
    DID_API_KEY = os.getenv("DID_API_KEY")
    DID_BASE_URL = os.getenv("DID_BASE_URL", "https://api.d-id.com")
    DID_AVATAR_SOURCE_URL = os.getenv(
        "DID_AVATAR_SOURCE_URL",
        ""
    )
    DID_VOICE_PROVIDER = os.getenv("DID_VOICE_PROVIDER", "microsoft")
    DID_VOICE_ID = os.getenv("DID_VOICE_ID", "en-US-JennyNeural")
    DID_TALK_TIMEOUT_SECONDS = int(os.getenv("DID_TALK_TIMEOUT_SECONDS", 60))

    # Groq API for Real-time Interview
    GROQ_API_KEY = os.getenv("GROQ_API_KEY")
    GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    GROQ_MCQ_MODEL = os.getenv("GROQ_MCQ_MODEL", GROQ_MODEL)
    GROQ_TEXT_MODEL = os.getenv("GROQ_TEXT_MODEL", GROQ_MODEL)
    GROQ_EVAL_MODEL = os.getenv("GROQ_EVAL_MODEL", GROQ_MODEL)
    GROQ_INTERVIEW_TEMP = float(os.getenv("GROQ_INTERVIEW_TEMP", "0.7"))
    GROQ_MAX_TOKENS = int(os.getenv("GROQ_MAX_TOKENS", "500"))

    # Proctoring Settings
    MAX_VIOLATIONS_ALLOWED = int(os.getenv("MAX_VIOLATIONS_ALLOWED", "3"))
    MAX_LOGIN_ATTEMPTS = int(os.getenv("MAX_LOGIN_ATTEMPTS", "3"))
    ADAPTIVE_DIFFICULTY_ENABLED = os.getenv("ADAPTIVE_DIFFICULTY_ENABLED", "true").lower() == "true"
    RECORD_VIDEO_PROCTORING = os.getenv("RECORD_VIDEO_PROCTORING", "true").lower() == "true"
    DEMO_CANDIDATE_USERNAME = os.getenv("DEMO_CANDIDATE_USERNAME", "demo.candidate").strip().lower()
    DEMO_CANDIDATE_PASSWORD = os.getenv("DEMO_CANDIDATE_PASSWORD", "Demo@123")
    DEMO_CANDIDATE_EMAIL = os.getenv("DEMO_CANDIDATE_EMAIL", "demo.candidate@zyra.local").strip().lower()
    DEMO_ALWAYS_PROMOTE = os.getenv("DEMO_ALWAYS_PROMOTE", "true").lower() == "true"
    DEMO_PERSIST_TEST_DATA = os.getenv("DEMO_PERSIST_TEST_DATA", "false").lower() == "true"
    DEMO_LOGIN_LIMIT = int(os.getenv("DEMO_LOGIN_LIMIT", "999999"))
    MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/zyra_db")
    MONGO_DB = os.getenv("MONGO_DB", "zyra_db")

    # Cloudinary (resume storage)
    CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME")
    CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY")
    CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET")
    CLOUDINARY_FOLDER = os.getenv("CLOUDINARY_FOLDER", "zyra/resumes")

    # SMTP for sending candidate credentials (Gmail example)
    SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
    SMTP_USER = os.getenv("SMTP_USER")    # your email (set in .env)
    SMTP_PASS = os.getenv("SMTP_PASS")    # app password or smtp password

    # Admin credentials (set in .env). Default should be changed in production.
    ADMIN_USER = os.getenv("ADMIN_USER", "hr@zyra.com")
    ADMIN_PASS = os.getenv("ADMIN_PASS", "admin123")  # change this in .env

    @classmethod
    def validate(cls):
        if not cls.SMTP_USER or not cls.SMTP_PASS:
            print("Warning: SMTP_USER or SMTP_PASS not set. Emailing will fail.")
        if not cls.GROQ_API_KEY and not cls.MCQ_USE_OLLAMA:
            print("Warning: neither GROQ_API_KEY nor Ollama is configured. AI generation will use deterministic fallbacks.")
