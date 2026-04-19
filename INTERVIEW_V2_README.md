# Zyra Sentinel - Advanced AI Interview System V2

## Overview

This comprehensive update transforms the Zyra interview system into a state-of-the-art AI-powered assessment platform featuring:

### ✨ Key Features

1. **Real-time Question Generation (Groq LLM)**
   - Dynamic question generation based on candidate profile
   - Adaptive difficulty adjustment
   - Context-aware questioning

2. **Advanced Proctoring & Security**
   - Tab switch detection (hard violation)
   - Face detection monitoring
   - Real-time violation tracking
   - 3-violation auto-lock system

3. **Modern Interview UI (Zyra Sentinel Design)**
   - Professional two-panel layout
   - Left: AI Avatar + Question
   - Right: Live Video Feed + Transcript
   - Real-time controls and feedback

4. **Comprehensive Evaluation**
   - Semantic analysis of responses
   - Confidence scoring
   - Communication & technical assessment
   - Final hiring recommendations

## Installation & Configuration

### 1. Update Your .env File

Add these new Groq environment variables:

```env
# Groq API for Real-time Interview Question Generation
GROQ_API_KEY=your_groq_api_key_here
GROQ_MODEL=mixtral-8x7b-32768
GROQ_INTERVIEW_TEMP=0.7
GROQ_MAX_TOKENS=500

# Proctoring Settings
MAX_VIOLATIONS_ALLOWED=3
MAX_LOGIN_ATTEMPTS=3
ADAPTIVE_DIFFICULTY_ENABLED=true
RECORD_VIDEO_PROCTORING=true
```

**To get a Groq API key:**
1. Visit https://console.groq.com
2. Sign up for a free account
3. Generate API key
4. Add to your .env file

### 2. Install Dependencies

```bash
pip install -r requirements.txt
# Add 'requests' if not already included (for Groq API calls)
```

### 3. Update MongoDB Schema (Optional but Recommended)

The system will automatically create collections for:
- `interviews` - Interview sessions and responses
- `proctoring_events` - Violation tracking
- `applications` - Job applications (existing)
- `users` - Candidate profiles (existing)

## Architecture

### Backend Components

#### `ai/groq_generator.py` - Groq Integration
```python
class GroqInterviewGenerator:
    - generate_initial_questions()      # First set of questions
    - generate_adaptive_question()      # Difficulty-adjusted questions
    - evaluate_response()               # Score candidate answers
    - generate_final_evaluation()       # Comprehensive report
```

#### `api/interview_routes.py` - New API Routes
- `/api/interview/session` - Initialize interview
- `/api/interview/generate_questions` - Get initial questions
- `/api/interview/next_question` - Adaptive next question
- `/api/interview/evaluate_response` - Score responses
- `/api/interview/submit` - Submit and evaluate interview
- `/api/proctoring/record_violation` - Track violations
- `/api/interview/upload_recording` - Store video

### Frontend Components

#### `templates/candidate_interview_v2.html` - Modern Interview UI
- Two-panel layout (AI Avatar + Video Feed)
- Real-time controls
- Interview transcript
- Evaluation results display

#### `static/css/interview-v2.css` - Zyra Sentinel Styling
- Modern gradient design
- Responsive grid layout
- Animated indicators
- Professional color scheme

#### `static/js/interview-v2.js` - Main Interview Logic
- Session management
- Question display & navigation
- Response collection
- Result presentation

#### `static/js/interview-proctoring.js` - Security & Monitoring
- Tab switch detection
- Face detection monitoring
- Violation management
- Session lock triggers

#### `static/js/interview-recognition.js` - Speech-to-Text
- Real-time speech recognition
- Audio quality monitoring
- Video recording with integrity checks
- Transcript generation

## How It Works

### Interview Flow

```
1. Candidate Logs In
   ↓
2. Interview Session Created
   ├─ Check login limits
   ├─ Verify not previously locked
   └─ Generate Session ID
   ↓
3. Questions Generated (Groq)
   ├─ Analyze candidate profile
   ├─ Generate 10-12 questions
   └─ Store for session
   ↓
4. Candidate Answers Questions
   ├─ Speaks/types response
   ├─ Monitor for violations
   ├─ Record video/audio
   └─ Transcribe speech
   ↓
5. Real-time Evaluation
   ├─ Score response (0-10)
   ├─ Analyze semantic content
   ├─ Measure confidence
   └─ Determine adaptive difficulty
   ↓
6. Generate Next Question (Adaptive)
   ├─ Adjust difficulty based on performance
   ├─ Avoid repetition
   ├─ Build on previous discussion
   └─ Test new competencies
   ↓
7. Repeat Steps 4-6
   ↓
8. Interview Completion
   ├─ Submit all responses
   ├─ Generate final evaluation
   ├─ Create hiring recommendation
   └─ Display results
```

### Proctoring Rules

**Hard Violations (3 = Auto-Lock):**
- Tab switch (candidate left interview tab)
- Developer tools detected
- Copy/paste detected
- Multiple faces detected
- Face not visible for > 15 seconds

**Penalties:**
- Each critical violation counts toward 3-violation limit
- 3rd violation triggers automatic submission
- Session is permanently locked
- Candidate cannot re-attempt

### Adaptive Difficulty Algorithm

```
Performance Range    → Difficulty
< 50% correct       → Easy (foundational concepts)
50-75% correct      → Medium (scenario-based)
> 75% correct       → Hard (complex problem-solving)
```

## API Documentation

### Initialize Interview Session

**POST** `/api/interview/session`
```json
Request: (No body required, uses session token)

Response: {
  "session_id": "uuid",
  "user_name": "Candidate Name",
  "job_title": "Position Applied",
  "duration_minutes": 45
}
```

### Generate Initial Questions

**POST** `/api/interview/generate_questions`
```json
Request: {
  "session_id": "uuid",
  "count": 10
}

Response: {
  "questions": [
    {
      "question_text": "...",
      "category": "technical|behavioral|problem_solving|culture_fit",
      "difficulty": "easy|medium|hard",
      "expected_competencies": ["competency1", "competency2"],
      "follow_up_hints": ["hint1", "hint2"]
    }
  ],
  "total_count": 10
}
```

### Evaluate Response

**POST** `/api/interview/evaluate_response`
```json
Request: {
  "session_id": "uuid",
  "question_index": 0,
  "response_text": "Candidate's answer..."
}

Response: {
  "score": 7.5,
  "semantic_analysis": "Candidate demonstrated...",
  "confidence_level": "high|medium|low",
  "strengths": ["strength1", "strength2"],
  "areas_for_improvement": ["area1", "area2"],
  "follow_up_suggested": "..."
}
```

### Submit Interview

**POST** `/api/interview/submit`
```json
Request: {
  "session_id": "uuid",
  "responses": [{...}],
  "duration": 1800000,
  "violations": 0
}

Response: {
  "final_score": 8.5,
  "strengths": [...],
  "weaknesses": [...],
  "recommendation": "strong_hire|hire|maybe|reject",
  "hiring_rationale": "...",
  "communication_score": 0.78,
  "technical_score": 0.85,
  "confidence_score": 0.82
}
```

### Record Proctoring Violation

**POST** `/api/proctoring/record_violation`
```json
Request: {
  "session_id": "uuid",
  "type": "Tab Switch|Face Not Detected|...",
  "severity": "CRITICAL|HIGH|MEDIUM|LOW",
  "details": "Additional violation details"
}

Response: {
  "status": "recorded|locked",
  "message": "....",
  "violations_count": 2,
  "max_allowed": 3
}
```

## Usage Guide

### For HR / Recruiters

1. **Configure Interview Settings**
   ```python
   # In config.py
   MAX_VIOLATIONS_ALLOWED = 3
   MAX_LOGIN_ATTEMPTS = 3
   ADAPTIVE_DIFFICULTY_ENABLED = true
   ```

2. **Monitor Interview Sessions**
   - Access admin dashboard
   - View real-time interview progress
   - Track proctoring violations
   - Review intermediate scores

3. **Access Interview Reports**
   - Final evaluation scores
   - Semantic analysis breakdown
   - Candidate strengths/weaknesses
   - Hiring recommendations
   - Video recording for review

### For Candidates

1. **Started on Stage 3 of Interview**
   - Log in with provided credentials
   - Click "Start Interview"
   - Grant camera & microphone access

2. **During Interview**
   - Listen to AI Avatar ask questions
   - Speak your answer naturally
   - Click "Save & Next" when done
   - Answer 10-12 adaptive questions

3. **Interview Tips**
   - Provide detailed examples
   - Explain your thought process
   - Stay in the interview window
   - Ensure good lighting & audio
   - Face the camera

4. **View Results**
   - Final score out of 10
   - Strengths you demonstrated
   - Areas for improvement
   - Hiring recommendation
   - Download detailed report

## Security Features

### Data Protection
- All video/audio encrypted in transit
- Candidate responses stored securely
- Violation logs audit trail
- Session encryption enabled

### Cheating Prevention
- Continuous face monitoring
- Tab switch detection
- Copy/paste blocking
- Right-click disabled
- Developer tools blocked
- Screenshot prevention

### Session Integrity
- One active session per login
- Max 3 login attempts per candidate
- Auto-lock on max violations
- Cannot resume locked session
- Automatic submission on time limit

## Deployment Considerations

### Production Checklist

- [ ] Groq API key configured
- [ ] MongoDB Atlas connection verified
- [ ] Cloudinary configured for video storage
- [ ] SMTP/Email service operational
- [ ] DID Avatar API key valid (for video generation)
- [ ] SSL/TLS enabled for HTTPS
- [ ] Database backups scheduled
- [ ] Log monitoring configured
- [ ] Rate limiting enabled
- [ ] CORS settings appropriate

### Performance Optimization

```python
# Recommended settings for production
GROQ_MAX_TOKENS = 500  # Balance quality vs latency
GROQ_INTERVIEW_TEMP = 0.7  # Consistency vs creativity
INTERVIEW_TIMEOUT = 2700  # 45 minutes
VIOLATION_CHECK_INTERVAL = 5000  # 5 seconds
```

## Troubleshooting

### Groq API Errors

**Error: "Groq API Key not set in .env"**
- Solution: Add `GROQ_API_KEY` to .env file
- Get key from https://console.groq.com

**Error: "Question generation timeout"**
- Solution: Groq API might be rate-limited
- Fallback to deterministic questions enabled
- Wait and retry

### Proctoring Issues

**Error: "Face not detected"**
- Ensure good lighting
- Camera positioned at face level
- Remove obstacles (glasses, headwear may cause issues)

**Error: "Tab switch violation"**
- Stay in the interview browser window
- Don't minimize or switch tabs
- Max 1 switch tolerated before warning

**Error: "Session locked"**
- 3 critical violations triggered lock
- Cannot resume this session
- Contact HR for new attempt

### Video Issues

**Error: "Camera not accessible"**
- Grant microphone/camera permissions
- Close other apps using camera
- Restart browser
- Ensure HTTPS connection

**Error: "Video recording failed"**
- Check available disk space
- Verify network connectivity
- Ensure strong WiFi signal
- Try different browser

## Advanced Configuration

### Custom Question Templates

Add to your job profiles in MongoDB:

```python
{
  "id": "job_custom",
  "title": "Custom Role",
  "custom_question_template": {
    "technical_count": 5,
    "behavioral_count": 3,
    "problem_solving_count": 2,
    "culture_fit_count": 2,
    "required_competencies": ["competency1", "competency2"],
    "difficulty_weights": {
      "easy": 0.2,
      "medium": 0.5,
      "hard": 0.3
    }
  }
}
```

### Custom Scoring Weights

Modify evaluation in `groq_generator.py`:

```python
evaluation = {
    "score": (
        (semantic_alignment * 0.30) +
        (communication * 0.25) +
        (confidence * 0.20) +
        (problem_solving * 0.15) +
        (relevant_examples * 0.10)
    )
}
```

### Violation Policies

Customize in `interview_proctoring.js`:

```javascript
this.config = {
    faceDetectionInterval: 5000,  // milliseconds
    minFaceConfidence: 0.7,        // 0-1 scale
    maxTabSwitchTolerance: 1,      // integer count
    recordVideoFrame: true         // boolean
};
```

## Support & Debugging

### Enable Debug Logging

```bash
export FLASK_DEBUG=true
export FLASK_ENV=development
python app.py
```

### Check Groq Integration

```python
from ai.groq_generator import get_groq_generator
gen = get_groq_generator()
questions, error = gen.generate_initial_questions(
    job_title="Software Engineer",
    job_description="Build reliable systems",
    num_questions=3
)
print(questions)
print(error)
```

### Monitor Interview Sessions

```python
# In Flask shell
db.interviews.find_one({"status": "active"})
db.proctoring_events.find({"severity": "CRITICAL"}).count()
```

## What's New vs Previous Version

| Feature | Old | New |
|---------|-----|-----|
| Questions | Static Generation | Real-time Groq LLM |
| Difficulty | Fixed | Adaptive Based on Performance |
| Proctoring | Basic | Advanced with Face Detection |
| Interview UI | Simple | Modern Zyra Sentinel Design |
| Video | Not Captured | Real-time Recording |
| Evaluation | Basic Scoring | Comprehensive Semantic Analysis |
| Violations | No Tracking | Hard 3-Strike Lock System |
| Login Limits | Basic | Enforced with Session Lock |
| Recommendations | Not Provided | AI-Driven Hiring Advice |

## Future Enhancements

- 🚀 Multi-language support
- 🎥 Real-time video quality adjustment
- 📊 Advanced analytics dashboard
- 🤖 Additional LLM provider support (OpenAI, Claude, etc.)
- 🎯 Role-specific competency frameworks
- 🔐 Biometric verification
- 📱 Mobile app integration
- 🌍 International candidate support

---

**Zyra Sentinel V2** - Empowering recruitment through AI-driven assessments.
Built with Python Flask, MongoDB, Groq LLM, and modern web technologies.
