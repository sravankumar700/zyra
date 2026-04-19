"""
Interview V2 routes for adaptive interviewing, proctoring, and reporting.
"""

from datetime import datetime, timezone
import uuid

import cloudinary.uploader
from bson.objectid import ObjectId
from flask import jsonify, request, session

from ai.candidate_reporting import build_candidate_report, clamp_score_10, normalize_list
from ai.groq_generator import get_groq_generator
from config import Config


def register_interview_routes(app, db):
    """Register the Interview V2 routes on the Flask app."""

    interviews = db.interviews
    proctoring_events = db.proctoring_events
    users_db = db.users

    def utc_now():
        return datetime.now(timezone.utc)

    def _candidate_user():
        candidate_id = session.get("candidate_id")
        if not candidate_id:
            return None
        try:
            return users_db.find_one({"_id": ObjectId(candidate_id)})
        except Exception:
            return None

    def _serialize_interview_session(interview):
        return {
            "session_id": interview.get("session_id"),
            "status": interview.get("status", "active"),
            "questions_asked": len(interview.get("questions", [])),
            "responses_received": len(interview.get("responses", [])),
            "violations": len(interview.get("violations", [])),
            "paused": bool(interview.get("paused", False)),
            "pause_reason": interview.get("pause_reason"),
            "planned_question_count": int(interview.get("planned_question_count", 0) or 0),
            "created_at": interview.get("created_at").isoformat() if interview.get("created_at") else None,
        }

    def _build_proctoring_summary(interview, inbound_summary=None):
        interview = interview or {}
        inbound_summary = inbound_summary or {}
        violations = interview.get("violations", [])
        critical_flags = normalize_list(
            inbound_summary.get("critical_flags")
            or [
                event.get("type")
                for event in violations
                if str(event.get("severity", "")).upper() in {"CRITICAL", "HIGH"}
            ],
            limit=5,
        )
        pause_reasons = normalize_list(
            [
                event.get("details") or event.get("type")
                for event in violations
                if event.get("pause_interview")
            ],
            limit=5,
        )
        return {
            "violation_count": len(violations),
            "critical_flags": critical_flags,
            "pause_count": int(inbound_summary.get("pause_count") or len(pause_reasons)),
            "pause_reasons": pause_reasons,
            "face_warning_count": int(inbound_summary.get("face_warning_count") or 0),
            "voice_warning_count": int(inbound_summary.get("voice_warning_count") or 0),
            "last_face_count": int(inbound_summary.get("last_face_count") or 0),
            "last_voice_count": int(inbound_summary.get("last_voice_count") or 0),
            "status": "review_required" if critical_flags else "clear",
        }

    def _build_virtual_report_payload(evaluation, candidate_report, proctoring_summary):
        final_score = clamp_score_10(evaluation.get("final_score"), default=0.0)
        communication = clamp_score_10(evaluation.get("communication_score"), default=round(final_score * 0.95, 1))
        technical = clamp_score_10(evaluation.get("technical_score"), default=final_score)
        confidence = clamp_score_10(evaluation.get("confidence_score"), default=round(final_score * 0.9, 1))
        return {
            "final_score": final_score,
            "overall_score": candidate_report.get("overall_score", 0),
            "score_out_of": 100,
            "semantic_analysis": clamp_score_10(evaluation.get("semantic_score"), default=round(final_score * 0.92, 1)),
            "nlp_evaluation": clamp_score_10(evaluation.get("nlp_score"), default=round(final_score * 0.9, 1)),
            "confidence": confidence,
            "problem_solving": clamp_score_10(evaluation.get("problem_solving_score"), default=technical),
            "communication": communication,
            "overall_performance": final_score,
            "difficulty_progression": str(evaluation.get("difficulty_progression") or "Adaptive based on candidate performance.").strip(),
            "performance_summary": str(evaluation.get("performance_summary") or evaluation.get("hiring_rationale") or candidate_report.get("hiring_rationale") or "").strip(),
            "strengths": normalize_list(evaluation.get("strengths") or candidate_report.get("strengths") or [], limit=5),
            "improvements": normalize_list(evaluation.get("weaknesses") or candidate_report.get("weaknesses") or [], limit=5),
            "recommendation": str(evaluation.get("recommendation") or candidate_report.get("final_recommendation") or "").strip(),
            "shortlist_reason": candidate_report.get("shortlist_reason"),
            "shortlist_decision": candidate_report.get("shortlist_decision"),
            "stage_scores": candidate_report.get("stage_scores"),
            "proctoring_summary": proctoring_summary,
        }

    @app.route("/api/interview/session", methods=["GET"])
    def get_interview_session():
        if not session.get("candidate_id"):
            return jsonify({"error": "Unauthorized"}), 403

        user = _candidate_user()
        if not user:
            return jsonify({"error": "User not found"}), 404

        if user.get("interview_locked"):
            return jsonify({"error": "Interview session is locked due to violations."}), 403

        last_session_id = str(user.get("last_interview_session") or "").strip()
        if last_session_id:
            existing = interviews.find_one({
                "session_id": last_session_id,
                "user_id": user["_id"],
                "status": {"$in": ["active", "paused"]},
            })
            if existing:
                return jsonify({
                    "session_id": existing["session_id"],
                    "user_name": f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or user.get("name", ""),
                    "job_title": user.get("job_role") or user.get("applied_position", "Candidate"),
                    "duration_minutes": 45,
                    "status": existing.get("status", "active"),
                    "paused": bool(existing.get("paused", False)),
                    "pause_reason": existing.get("pause_reason"),
                    "questions_asked": len(existing.get("questions", [])),
                    "responses_received": len(existing.get("responses", [])),
                    "planned_question_count": int(existing.get("planned_question_count", 10) or 10),
                })

        login_attempts = int(user.get("interview_login_attempts", 0) or 0)
        if login_attempts >= Config.MAX_LOGIN_ATTEMPTS and user.get("virtual_taken"):
            return jsonify({"error": "Interview already completed. No more attempts allowed."}), 403

        session_id = str(uuid.uuid4())
        interview_session = {
            "session_id": session_id,
            "user_id": user["_id"],
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "questions": [],
            "responses": [],
            "violations": [],
            "transcript_entries": [],
            "status": "active",
            "paused": False,
            "pause_reason": "",
            "planned_question_count": 12,
            "start_time": utc_now(),
        }
        interviews.insert_one(interview_session)

        users_db.update_one(
            {"_id": user["_id"]},
            {
                "$inc": {"interview_login_attempts": 1},
                "$set": {
                    "last_interview_session": session_id,
                    "interview_status": "in_progress",
                    "updated_at": utc_now(),
                },
            },
        )

        return jsonify({
            "session_id": session_id,
            "user_name": f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or user.get("name", ""),
            "job_title": user.get("job_role") or user.get("applied_position", "Candidate"),
            "duration_minutes": 45,
            "status": "active",
            "paused": False,
            "planned_question_count": 12,
        })

    @app.route("/api/interview/generate_questions", methods=["POST"])
    def generate_questions():
        if not session.get("candidate_id"):
            return jsonify({"error": "Unauthorized"}), 403

        data = request.get_json() or {}
        session_id = str(data.get("session_id") or "").strip()
        requested_count = max(10, min(12, int(data.get("count", 12) or 12)))
        initial_count = min(3, requested_count)

        if not session_id:
            return jsonify({"error": "session_id required"}), 400

        user = _candidate_user()
        if not user:
            return jsonify({"error": "User not found"}), 404

        interview = interviews.find_one({"session_id": session_id, "user_id": user["_id"]})
        if not interview:
            return jsonify({"error": "Interview session not found"}), 404

        if interview.get("questions"):
            return jsonify({
                "questions": interview.get("questions", []),
                "total_count": len(interview.get("questions", [])),
                "planned_question_count": int(interview.get("planned_question_count", requested_count) or requested_count),
            })

        try:
            groq = get_groq_generator()
            questions, error = groq.generate_initial_questions(
                job_title=user.get("job_role") or user.get("applied_position", "Software Engineer"),
                job_description=user.get("job_description") or f"Interview for {user.get('job_role') or 'the selected role'} position",
                candidate_resume=user.get("resume_summary") or user.get("resume") or "",
                num_questions=initial_count,
            )
            if error or not questions:
                return jsonify({"error": f"Question generation failed: {error or 'unknown error'}"}), 500

            interviews.update_one(
                {"session_id": session_id},
                {"$set": {
                    "questions": questions,
                    "planned_question_count": requested_count,
                    "updated_at": utc_now(),
                }},
            )

            return jsonify({
                "questions": questions,
                "total_count": len(questions),
                "planned_question_count": requested_count,
            })
        except Exception as exc:
            return jsonify({"error": f"Exception: {str(exc)}"}), 500

    @app.route("/api/interview/next_question", methods=["POST"])
    def get_next_question():
        if not session.get("candidate_id"):
            return jsonify({"error": "Unauthorized"}), 403

        data = request.get_json() or {}
        session_id = str(data.get("session_id") or "").strip()
        if not session_id:
            return jsonify({"error": "session_id required"}), 400

        user = _candidate_user()
        if not user:
            return jsonify({"error": "User not found"}), 404

        interview = interviews.find_one({"session_id": session_id, "user_id": user["_id"]})
        if not interview:
            return jsonify({"error": "Interview session not found"}), 404

        if interview.get("paused"):
            return jsonify({"error": "Interview is paused. Resume before continuing."}), 409

        planned_question_count = int(interview.get("planned_question_count", 8) or 8)
        current_question_count = len(interview.get("questions", []))
        if current_question_count >= planned_question_count:
            return jsonify({"done": True, "question": None})

        try:
            response_scores = [
                float((item.get("evaluation") or {}).get("score", 5))
                for item in interview.get("responses", [])
            ]
            avg_score = sum(response_scores) / len(response_scores) if response_scores else 5.0
            difficulty = "medium"
            if avg_score >= 7.5:
                difficulty = "hard"
            elif avg_score <= 4.5:
                difficulty = "easy"

            groq = get_groq_generator()
            question, error = groq.generate_adaptive_question(
                previous_questions=interview.get("questions", []),
                previous_responses=interview.get("responses", []),
                difficulty_level=difficulty,
                job_context=user.get("job_role") or user.get("applied_position", "Position"),
            )
            if error or not question:
                return jsonify({"error": f"Question generation failed: {error or 'unknown error'}"}), 500

            interviews.update_one(
                {"session_id": session_id},
                {"$push": {"questions": question}, "$set": {"updated_at": utc_now()}},
            )

            return jsonify({"question": question, "done": False})
        except Exception as exc:
            return jsonify({"error": f"Exception: {str(exc)}"}), 500

    @app.route("/api/interview/evaluate_response", methods=["POST"])
    def evaluate_response():
        if not session.get("candidate_id"):
            return jsonify({"error": "Unauthorized"}), 403

        data = request.get_json() or {}
        session_id = str(data.get("session_id") or "").strip()
        question_index = int(data.get("question_index", 0) or 0)
        response_text = str(data.get("response_text") or "").strip()
        transcript_entries = data.get("transcript_entries") or []
        input_mode = str(data.get("input_mode") or "voice").strip().lower()

        user = _candidate_user()
        if not user:
            return jsonify({"error": "User not found"}), 404

        interview = interviews.find_one({"session_id": session_id, "user_id": user["_id"]})
        if not interview:
            return jsonify({"error": "Interview session not found"}), 404
        if interview.get("paused"):
            return jsonify({"error": "Interview is paused. Resolve the warning and resume to continue."}), 409
        if question_index >= len(interview.get("questions", [])):
            return jsonify({"error": "Invalid question index"}), 400

        try:
            groq = get_groq_generator()
            question = interview["questions"][question_index]
            evaluation, error = groq.evaluate_response(
                question=question,
                response_text=response_text,
                job_context=user.get("job_role") or user.get("applied_position", "Position"),
            )

            if error:
                evaluation = {
                    "score": 5,
                    "semantic_analysis": "Response captured successfully, but evaluation fallback was used.",
                    "confidence_level": "medium",
                    "strengths": ["Participated in the interview round."],
                    "areas_for_improvement": ["Provide more detailed examples and clearer structure."],
                    "follow_up_suggested": "",
                }

            response_doc = {
                "question_index": question_index,
                "question": question,
                "response_text": response_text,
                "input_mode": input_mode,
                "evaluation": evaluation,
                "transcript_entries": transcript_entries[:20] if isinstance(transcript_entries, list) else [],
                "timestamp": utc_now(),
            }

            interviews.update_one(
                {"session_id": session_id},
                {
                    "$push": {"responses": response_doc},
                    "$set": {"updated_at": utc_now()},
                },
            )

            return jsonify(evaluation)
        except Exception as exc:
            return jsonify({"error": f"Exception: {str(exc)}"}), 500

    @app.route("/api/interview/pause", methods=["POST"])
    def pause_interview():
        if not session.get("candidate_id"):
            return jsonify({"error": "Unauthorized"}), 403

        data = request.get_json() or {}
        session_id = str(data.get("session_id") or "").strip()
        reason = str(data.get("reason") or "Interview paused by proctoring policy.").strip()
        user = _candidate_user()
        if not user:
            return jsonify({"error": "User not found"}), 404

        interviews.update_one(
            {"session_id": session_id, "user_id": user["_id"]},
            {"$set": {"paused": True, "status": "paused", "pause_reason": reason, "updated_at": utc_now()}},
        )
        return jsonify({"status": "paused", "reason": reason})

    @app.route("/api/interview/resume", methods=["POST"])
    def resume_interview():
        if not session.get("candidate_id"):
            return jsonify({"error": "Unauthorized"}), 403

        data = request.get_json() or {}
        session_id = str(data.get("session_id") or "").strip()
        user = _candidate_user()
        if not user:
            return jsonify({"error": "User not found"}), 404

        interviews.update_one(
            {"session_id": session_id, "user_id": user["_id"]},
            {"$set": {"paused": False, "status": "active", "pause_reason": "", "updated_at": utc_now()}},
        )
        return jsonify({"status": "active"})

    @app.route("/api/proctoring/record_violation", methods=["POST"])
    def record_violation():
        if not session.get("candidate_id"):
            return jsonify({"error": "Unauthorized"}), 403

        data = request.get_json() or {}
        session_id = str(data.get("session_id") or "").strip()
        violation_type = str(data.get("type") or "unknown").strip()
        severity = str(data.get("severity") or "LOW").strip().upper()
        details = str(data.get("details") or "").strip()
        face_count = int(data.get("face_count") or 0)
        voice_count = int(data.get("voice_count") or 0)
        should_pause = bool(data.get("pause_interview")) or face_count > 1 or voice_count > 1

        user = _candidate_user()
        if not user:
            return jsonify({"error": "User not found"}), 404

        interview = interviews.find_one({"session_id": session_id, "user_id": user["_id"]})
        if not interview:
            return jsonify({"status": "ignored", "message": "Session not found"}), 200

        violation_doc = {
            "interview_session_id": session_id,
            "user_id": interview.get("user_id"),
            "type": violation_type,
            "severity": severity,
            "timestamp": utc_now(),
            "details": details,
            "face_count": face_count,
            "voice_count": voice_count,
            "pause_interview": should_pause,
        }
        proctoring_events.insert_one(violation_doc)

        critical_count = proctoring_events.count_documents({
            "interview_session_id": session_id,
            "severity": "CRITICAL",
        })

        update_fields = {"updated_at": utc_now()}
        if should_pause:
            update_fields.update({
                "paused": True,
                "status": "paused",
                "pause_reason": details or violation_type,
            })

        interviews.update_one(
            {"session_id": session_id},
            {"$push": {"violations": violation_doc}, "$set": update_fields},
        )

        if critical_count >= Config.MAX_VIOLATIONS_ALLOWED:
            interviews.update_one(
                {"session_id": session_id},
                {"$set": {"status": "locked", "locked_reason": "Max violations reached", "updated_at": utc_now()}},
            )
            users_db.update_one(
                {"_id": interview.get("user_id")},
                {"$set": {"interview_locked": True, "updated_at": utc_now()}},
            )
            return jsonify({
                "status": "locked",
                "message": "Session locked due to maximum violations",
                "pause_interview": True,
            }), 403

        return jsonify({
            "status": "recorded",
            "pause_interview": should_pause,
            "message": details or violation_type,
        })

    @app.route("/api/interview/submit", methods=["POST"])
    def submit_interview():
        if not session.get("candidate_id"):
            return jsonify({"error": "Unauthorized"}), 403

        data = request.get_json() or {}
        session_id = str(data.get("session_id") or "").strip()
        duration_ms = int(data.get("duration", 0) or 0)
        transcript_entries = data.get("transcript_entries") or []
        proctoring_summary_in = data.get("proctoring_summary") or {}
        auto_submitted = bool(data.get("auto_submitted", False))

        user = _candidate_user()
        if not user:
            return jsonify({"error": "User not found"}), 404

        interview = interviews.find_one({"session_id": session_id, "user_id": user["_id"]})
        if not interview:
            return jsonify({"error": "Interview session not found"}), 404

        try:
            groq = get_groq_generator()
            evaluation, error = groq.generate_final_evaluation(
                interview_session=interview,
                job_context=user.get("job_role") or user.get("applied_position", "Position"),
            )

            if error:
                fallback_score = 0.0
                if interview.get("responses"):
                    response_scores = [
                        float((item.get("evaluation") or {}).get("score", 0))
                        for item in interview.get("responses", [])
                    ]
                    fallback_score = round(sum(response_scores) / len(response_scores), 1) if response_scores else 0.0
                evaluation = {
                    "final_score": fallback_score,
                    "strengths": ["Candidate completed the live interview round."],
                    "weaknesses": ["Detailed AI synthesis was unavailable, so a fallback summary was used."],
                    "recommendation": "maybe",
                    "hiring_rationale": "Interview completed successfully, but the final AI evaluator fell back to a local summary.",
                    "performance_summary": "Interview completed with fallback evaluation.",
                }

            proctoring_summary = _build_proctoring_summary(interview, proctoring_summary_in)
            merged_user = dict(user)
            merged_user.update({
                "virtual_taken": True,
                "virtual_score": evaluation.get("final_score"),
                "interview_status": "completed",
                "interview_score": evaluation.get("final_score"),
                "interview_recommendation": evaluation.get("recommendation"),
            })
            candidate_report = build_candidate_report(
                merged_user,
                interview_evaluation=evaluation,
                proctoring_summary=proctoring_summary,
            )
            virtual_report = _build_virtual_report_payload(evaluation, candidate_report, proctoring_summary)

            interviews.update_one(
                {"session_id": session_id},
                {"$set": {
                    "status": "completed",
                    "paused": False,
                    "pause_reason": "",
                    "final_evaluation": evaluation,
                    "candidate_report": candidate_report,
                    "proctoring_summary": proctoring_summary,
                    "duration_ms": duration_ms,
                    "auto_submitted": auto_submitted,
                    "transcript_entries": transcript_entries[:100] if isinstance(transcript_entries, list) else [],
                    "end_time": utc_now(),
                    "updated_at": utc_now(),
                }},
            )

            users_db.update_one(
                {"_id": user["_id"]},
                {"$set": {
                    "interview_status": "completed",
                    "interview_score": evaluation.get("final_score", 0.0),
                    "interview_recommendation": evaluation.get("recommendation", "maybe"),
                    "interview_completed_at": utc_now(),
                    "virtual_round_enabled": True,
                    "virtual_taken": True,
                    "virtual_score": evaluation.get("final_score", 0.0),
                    "virtual_feedback": evaluation.get("hiring_rationale") or candidate_report.get("shortlist_reason"),
                    "virtual_report": virtual_report,
                    "virtual_duration_seconds": round(duration_ms / 1000) if duration_ms else 0,
                    "virtual_proctoring_violations": proctoring_summary.get("violation_count", 0),
                    "virtual_decision": candidate_report.get("shortlist_decision", "pending"),
                    "candidate_report": candidate_report,
                    "updated_at": utc_now(),
                }},
            )

            response_payload = dict(evaluation)
            response_payload.update(candidate_report)
            response_payload["proctoring_summary"] = proctoring_summary
            response_payload["virtual_report"] = virtual_report
            return jsonify(response_payload)
        except Exception as exc:
            return jsonify({"error": f"Exception: {str(exc)}"}), 500

    @app.route("/api/interview/upload_recording", methods=["POST"])
    def upload_recording():
        if not session.get("candidate_id"):
            return jsonify({"error": "Unauthorized"}), 403

        if "video" not in request.files:
            return jsonify({"error": "No video file provided"}), 400

        video_file = request.files["video"]
        session_id = request.form.get("session_id", "")

        try:
            result = cloudinary.uploader.upload(
                video_file,
                resource_type="video",
                folder="zyra/interview_recordings",
                public_id=f"interview-{session_id}-{uuid.uuid4().hex[:8]}",
            )
            interviews.update_one(
                {"session_id": session_id},
                {"$set": {"recording_url": result.get("secure_url", ""), "updated_at": utc_now()}},
            )
            return jsonify({"status": "uploaded", "recording_id": result.get("public_id", "")})
        except Exception as exc:
            return jsonify({"error": f"Upload failed: {str(exc)}"}), 500

    @app.route("/api/interview/status/<session_id>", methods=["GET"])
    def interview_status(session_id):
        if not session.get("candidate_id"):
            return jsonify({"error": "Unauthorized"}), 403

        user = _candidate_user()
        if not user:
            return jsonify({"error": "User not found"}), 404

        interview = interviews.find_one({"session_id": session_id, "user_id": user["_id"]})
        if not interview:
            return jsonify({"error": "Session not found"}), 404

        payload = _serialize_interview_session(interview)
        payload["proctoring_summary"] = interview.get("proctoring_summary", {})
        return jsonify(payload)

    @app.route("/api/interview/history", methods=["GET"])
    def interview_history():
        if not session.get("candidate_id"):
            return jsonify({"error": "Unauthorized"}), 403

        user = _candidate_user()
        if not user:
            return jsonify({"error": "Candidate not found"}), 404

        history = list(
            interviews.find(
                {"user_id": user["_id"]},
                {"responses.response_text": 0},
            ).sort("created_at", -1).limit(10)
        )

        serialized_history = []
        for interview in history:
            serialized = _serialize_interview_session(interview)
            serialized["final_evaluation"] = interview.get("final_evaluation", {})
            serialized["candidate_report"] = interview.get("candidate_report", {})
            serialized_history.append(serialized)

        return jsonify({"history": serialized_history})

    print("Interview V2 routes registered successfully")
