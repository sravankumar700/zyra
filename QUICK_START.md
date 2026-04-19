# Quick Start Guide - Zyra Sentinel Interview V2

## ✅ What's Been Completed

Your Zyra AI recruitment platform has been completely redesigned with:

1. **Real-time Question Generation** - Powered by Groq LLM
2. **Advanced Proctoring** - Tab switches, face detection, 3-strike lock
3. **Modern UI** - Two-panel Zyra Sentinel design with video feed
4. **Adaptive Difficulty** - Questions adjust to candidate performance
5. **Comprehensive Evaluation** - Semantic analysis & hiring recommendations

## 🚀 Next Steps

### Step 1: Get Groq API Key (5 minutes)
```
1. Visit https://console.groq.com
2. Create free account
3. Generate API key
4. Copy the key (looks like: gsk_...)
5. Add to your .env file:
   GROQ_API_KEY=gsk_your_api_key_here
```

### Step 2: Update Your .env File
Add these lines to your existing .env:
```env
# Groq API for Real-time Interview Question Generation
GROQ_API_KEY=gsk_your_api_key_here
GROQ_MODEL=mixtral-8x7b-32768
GROQ_INTERVIEW_TEMP=0.7
GROQ_MAX_TOKENS=500

# Proctoring Settings
MAX_VIOLATIONS_ALLOWED=3
MAX_LOGIN_ATTEMPTS=3
ADAPTIVE_DIFFICULTY_ENABLED=true
RECORD_VIDEO_PROCTORING=true
```

### Step 3: Restart Your Application
```bash
python app.py
```

### Step 4: Test the Interview System
1. Go to candidate dashboard
2. Start interview (now labeled "Stage 3 Interview")
3. Allow camera/microphone permissions
4. Answer 10-12 AI-generated questions
5. System will track proctoring violations
6. Receive comprehensive final evaluation

## 📁 New Files Created

```
ai/
  └─ groq_generator.py (500+ lines) - LLM integration

api/
  ├─ __init__.py
  └─ interview_routes.py (600+ lines) - Interview API

templates/
  └─ candidate_interview_v2.html (350+ lines) - New interview UI

static/
  ├─ css/
  │  └─ interview-v2.css (800+ lines) - Modern styling
  └─ js/
     ├─ interview-v2.js (400+ lines) - Main logic
     ├─ interview-proctoring.js (450+ lines) - Security
     └─ interview-recognition.js (450+ lines) - Speech-to-text

INTERVIEW_V2_README.md - Full documentation
QUICK_START.md - This file
```

## 🎯 Key Features

### For Candidates
- ✅ Modern, professional interview interface
- ✅ Real-time AI questions tailored to their skills
- ✅ Video feed visible for self-monitoring
- ✅ Live transcript of conversation
- ✅ Clear proctoring guidelines
- ✅ Final evaluation with hiring recommendation

### For HR/Recruiters
- ✅ Sessions automatically generated
- ✅ Real-time candidate monitoring
- ✅ Violation tracking & alerts
- ✅ Comprehensive evaluation reports
- ✅ Final hiring recommendations
- ✅ Video recording for review
- ✅ Semantic analysis of responses

### For Security
- ✅ Tab switch detection (hard violation)
- ✅ Face detection monitoring
- ✅ Copy/paste prevention
- ✅ Developer tools blocked
- ✅ Auto-lock on 3 violations
- ✅ Session encryption
- ✅ Login limits enforced

## 📊 Interview Flow

```
Candidate Logs In
          ↓
Interview Session Created
          ↓
10-12 AI-Generated Questions (Groq)
          ↓
Candidate Answers Question
          ↓
Real-time Evaluation (Score 0-10)
          ↓
Adaptive Difficulty Calculation
          ↓
Generate Next Question (Smarter)
          ↓
Repeat for All Questions
          ↓
Submit Interview
          ↓
Final Evaluation Report
          ↓
Display Results & Recommendation
```

## 🔒 Proctoring Rules

**Will Trigger Violation:**
1. ❌ Switching tab/window away from interview
2. ❌ Multiple faces detected in camera
3. ❌ Face not visible for > 15 seconds
4. ❌ Trying to copy/paste
5. ❌ Opening developer tools (F12)
6. ❌ Taking screenshots

**Result:**
- ⚠️ 1 Violation = Warning
- ⚠️ 2 Violations = Another Warning
- 🔐 3 Violations = **Session Locked & Auto-Submitted**

## 📝 API Endpoints (For Integration)

### New Interview Endpoints
- `POST /api/interview/session` - Start interview
- `POST /api/interview/generate_questions` - Get questions
- `POST /api/interview/evaluate_response` - Score answer
- `POST /api/interview/submit` - Submit interview
- `POST /api/proctoring/record_violation` - Track violations

See `INTERVIEW_V2_README.md` for full API documentation.

## 🧪 Testing Your Setup

### Test 1: Groq Connection
```python
from ai.groq_generator import get_groq_generator

gen = get_groq_generator()
questions, error = gen.generate_initial_questions(
    job_title="Software Engineer",
    job_description="Build systems",
    num_questions=3
)
print(f"Generated {len(questions)} questions")
print(f"Error: {error}")
```

### Test 2: Interview Session
1. Login as candidate
2. Click "Start Interview V2"
3. Wait for questions to load
4. Click "Start Answer" and speak
5. Click "Save & Next"
6. After 3 questions, click "Submit"
7. View final evaluation

### Test 3: Proctoring
1. During interview, click away from tab
2. Should see proctoring warning
3. Return to interview tab
4. Continue - warning recorded

## 📈 Dashboard Statistics

Your system now captures:
- **Interview Duration** - How long the candidate took
- **Response Quality** - Semantic analysis score
- **Confidence Level** - How confident the candidate sounded
- **Communication Score** - Clarity and articulation
- **Technical Score** - Depth of technical knowledge
- **Violations** - Tab switches, face detection issues
- **Final Recommendation** - Strong Hire / Hire / Maybe / Reject

## 💡 Pro Tips

1. **For Best Results:**
   - Ensure good WiFi connection
   - Good lighting for video
   - Quiet environment
   - Test camera/mic before interview

2. **For Candidates:**
   - Read questions carefully
   - Provide detailed examples
   - Explain your thought process
   - Stay focused (no tab switching!)

3. **For HR:**
   - Review video recordings
   - Check proctoring violations
   - Note semantic analysis scores
   - Use hiring recommendation as guidance

## 🆘 Troubleshooting

### "Groq API Error"
- ✅ Check GROQ_API_KEY in .env
- ✅ Key should start with "gsk_"
- ✅ No extra spaces in key

### "Questions Not Generating"
- ✅ Wait 2-5 seconds (first time slower)
- ✅ Check internet connection
- ✅ Fallback questions will load automatically

### "Camera Not Working"
- ✅ Grant camera permission in browser
- ✅ Close other apps using camera
- ✅ Restart browser
- ✅ Use HTTPS (required)

### "Violations Too Strict"
- ✅ Adjust MAX_VIOLATIONS_ALLOWED in .env
- ✅ Modify proctoring thresholds in interview-proctoring.js

## 📞 Support Resources

- **Full Documentation:** See `INTERVIEW_V2_README.md`
- **API Docs:** See `INTERVIEW_V2_README.md` API section
- **Troubleshooting:** See `INTERVIEW_V2_README.md` Troubleshooting
- **Code Examples:** Check individual .js files for implementation

## ✨ What's Different From Previous Version

| Aspect | Before | After |
|--------|--------|-------|
| Questions | Fixed set | AI-Generated (Groq) |
| Difficulty | Same for all | Adaptive |
| Proctoring | Basic | Advanced (Face, Tab) |
| UI/UX | Simple form | Modern 2-panel design |
| Video | Not captured | Recorded & stored |
| Evaluation | Basic score | Comprehensive analysis |
| Results | Score only | Full report + recommendation |
| Security | Limited | Multi-layer (3-strike lock) |

## 🎓 Next Learning Steps

1. Read full `INTERVIEW_V2_README.md`
2. Review API documentation
3. Explore Groq documentation
4. Test with sample candidates
5. Customize evaluation criteria for your roles

---

**Ready to launch?** Test with a candidate and watch your AI interview system in action!
