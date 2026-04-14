# Zyra

Zyra is an AI-powered hiring platform built with Flask, MongoDB, and a custom frontend for HR operations and candidate screening. It supports job publishing, candidate applications, ATS-style resume checks, MCQ screening, and a virtual interview workflow.

## What This Project Includes

- HR dashboard for jobs, candidates, interviews, and analytics
- Candidate application flow with shared live job catalog
- Resume upload and resume text extraction from PDF
- ATS scoring against job requirements
- MCQ screening round with proctoring checks
- Virtual interview round with AI-assisted evaluation
- Admin/HR actions for accepting, rejecting, and promoting candidates

## Recent Changes Done

These are the main product and UI changes completed in the current Zyra version:

- Replaced the default seed jobs with a structured 16-role catalog across 8 professions:
  - Software Engineering
  - Data Science / AI
  - Healthcare
  - Business Analysis
  - Project Management
  - Finance
  - Education
  - Hospitality
- Updated the backend job seed logic in `app.py` so the new default roles load cleanly without blocking recruiter-created jobs.
- Connected the shared `/api/jobs` catalog to both the HR dashboard and applicant job-selection flow.
- Updated the separate candidate registration page so users can select roles from the live job catalog instead of typing roles manually.
- Added job preview panels for applicants so they can see description, skills, experience, salary, and location before applying.
- Redesigned the HR dashboard into a modern blue SaaS interface with:
  - left navigation
  - summary cards
  - search
  - filters
  - sort controls
  - improved listings table
- Fixed HR sidebar navigation behavior so the left menu works properly.
- Added a working notification dropdown in the HR dashboard header.
- Updated the HR identity display to show `HR` and `Logged in as Harsh`.
- Changed the HR job board behavior so clicking a job title expands that role inline and shows full role details directly below the selected listing.
- Applied the blue palette styling to the dashboard and related HR UI elements, including the MCQ interface styling pass.

## Tech Stack

- Backend: Flask
- Database: MongoDB
- AI/LLM integrations: Hugging Face, Ollama, local fallback logic
- File storage: Cloudinary
- PDF parsing: `pypdf`
- Frontend: HTML, CSS, vanilla JavaScript

## Project Structure

```text
zyra final/
+-- app.py
+-- config.py
+-- requirements.txt
+-- README.md
+-- vercel.json
+-- api.txt
+-- plan.txt
+-- .env
+-- ai/
|   +-- hf_evaluator.py
|   +-- hf_generator.py
+-- ai-avatar-interview-test/
|   +-- index.html
|   +-- script.js
|   +-- style.css
|   +-- undefined - Imgur.jpg
+-- sessions/
|   +-- *.json
+-- static/
|   +-- zyra-logo.png
|   +-- zyra-logo.svg
|   +-- zyra-mark.svg
|   +-- css/
|   |   +-- landing.css
|   |   +-- main.css
|   |   +-- style.css
|   |   +-- zyra-main.css
|   +-- images/
|   |   +-- README.txt
|   |   +-- zyra-logo.png
|   |   +-- zyra-logo.svg
|   |   +-- zyra-mark.svg
|   +-- js/
|       +-- admin.js
|       +-- apply.js
|       +-- main.js
|       +-- resume_analyzer.js
|       +-- test.js
|       +-- virtual_interview.js
+-- templates/
|   +-- admin_candidate.html
|   +-- admin_dashboard.html
|   +-- admin_login.html
|   +-- candidate_dashboard.html
|   +-- candidate_login.html
|   +-- candidate_test.html
|   +-- index.html
|   +-- landing.html
|   +-- main.html
|   +-- register.html
+-- .vscode/
|   +-- settings.json
+-- .venv/
+-- __pycache__/
```

## Important Files

- `app.py`
  Main Flask app, API routes, job seed data, candidate flow, MCQ logic, virtual interview logic, and admin actions.

- `config.py`
  Environment-variable based configuration for MongoDB, Cloudinary, SMTP, Hugging Face, Ollama, and admin credentials.

- `templates/main.html`
  Main multi-panel Zyra interface including HR dashboard, candidate flow, dashboard scripts, and shared frontend logic.

- `templates/register.html`
  Candidate registration page now connected to the shared job catalog and role preview.

- `static/css/zyra-main.css`
  Main HR dashboard and Zyra UI stylesheet, including the blue SaaS design system.

- `static/css/style.css`
  Additional shared styling, including candidate-side form and preview support.

## Setup

### 1. Create and activate virtual environment

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Windows CMD:

```bat
python -m venv .venv
.\.venv\Scripts\activate.bat
```

### 2. Install dependencies

```powershell
pip install -r requirements.txt
```

### 3. Configure environment variables

Create or update `.env` in the project root.

Recommended values:

```env
FLASK_SECRET=change_this_secret
FLASK_DEBUG=true

MONGO_URI=mongodb://localhost:27017/zyra_db
MONGO_DB=zyra_db

HF_TOKEN=your_huggingface_token
HF_API_URL=https://router.huggingface.co/v1/chat/completions
HF_MODEL=microsoft/Phi-3-mini-4k-instruct
MCQ_SECONDARY_MODEL=HuggingFaceH4/zephyr-7b-beta
MCQ_TERTIARY_MODEL=google/gemma-2-2b-it
VIRTUAL_HF_MODEL=microsoft/Phi-3-mini-4k-instruct

MCQ_USE_OLLAMA=true
OLLAMA_URL=http://127.0.0.1:11434/api/generate
OLLAMA_MODEL=phi3:mini
MCQ_OLLAMA_MODEL=phi3:mini

SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your_email@example.com
SMTP_PASS=your_app_password

CLOUDINARY_CLOUD_NAME=your_cloud_name
CLOUDINARY_API_KEY=your_api_key
CLOUDINARY_API_SECRET=your_api_secret
CLOUDINARY_FOLDER=zyra/resumes

ADMIN_USER=hr@zyra.com
ADMIN_PASS=admin123
```

Optional variables already supported by the app:

- `DID_API_KEY`
- `DID_BASE_URL`
- `DID_AVATAR_SOURCE_URL`
- `DID_VOICE_PROVIDER`
- `DID_VOICE_ID`
- `DID_TALK_TIMEOUT_SECONDS`
- `MCQ_QUESTION_COUNT`
- `VIRTUAL_QUESTION_COUNT`
- `MCQ_PROMOTION_THRESHOLD_PERCENT`

### 4. Run the app

```powershell
python app.py
```

Open:

```text
http://127.0.0.1:5000
```

## Main Routes

### Pages

- `/` - main Zyra portal
- `/register` - candidate registration page

### Core APIs

- `GET /api/jobs` - fetch job catalog
- `POST /api/jobs` - create a new job
- `POST /api/apply` - submit candidate application
- `POST /api/admin/login` - HR/admin login
- `GET /api/admin/applications` - HR applications data
- `POST /api/admin/accept/<id>` - shortlist candidate
- `POST /api/admin/reject/<id>` - reject candidate
- `POST /api/admin/virtual/promote/<id>` - promote to virtual round
- `POST /api/start_test` - start MCQ round
- `POST /api/submit_test` - submit MCQ round
- `POST /api/virtual/questions` - generate virtual questions
- `POST /api/virtual/respond` - get adaptive virtual response
- `POST /api/virtual/submit` - submit virtual interview

## Job Catalog Notes

The current built-in job library includes 16 roles across 8 professions and is used by:

- the HR dashboard job board
- the main application flow
- the register page job selector

When applicants choose a job, the interface now shows role details such as:

- description
- department
- location
- experience
- salary
- required skills
- preferred skills

In the HR dashboard, role details are hidden by default and expand inline only when the recruiter clicks a job title.

## Files Changed During Recent Updates

The current round of updates primarily touched these files:

- `app.py`
- `templates/main.html`
- `templates/register.html`
- `static/css/zyra-main.css`
- `static/css/style.css`

## Verification Performed

The recent updates were checked with:

- `python -m py_compile app.py`
- inline script syntax validation for `templates/main.html`

## Deployment Notes

- `vercel.json` is present for deployment-related setup.
- Resume files are uploaded to Cloudinary and only the hosted URL is stored.
- MongoDB is required for the full live workflow.
- If external services are not available, some frontend flows may fall back to mock/demo behavior already present in the project.

## Suggested README Sections For Future Updates

Whenever the project changes again, keep this README updated in these sections:

- Overview
- Recent Changes Done
- Tech Stack
- Project Structure
- Setup
- Environment Variables
- Main Routes
- Job Catalog Notes
- Files Changed
- Verification
- Deployment Notes

## Maintainer Note

If you change the HR dashboard UI, candidate application flow, or job schema again, update both the backend job payload in `app.py` and the frontend preview/render logic in `templates/main.html` and `templates/register.html` so the dashboard and candidate flows stay in sync.



-------------------------------------------------------------------
-----------------------------------THE END-------------------------S