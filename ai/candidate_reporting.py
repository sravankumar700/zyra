"""
Shared candidate reporting helpers for Zyra's three-stage workflow.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


STANDARD_STAGE_WEIGHTS = {
    "resume_screening": 30,
    "assessment": 30,
    "interview": 40,
}

TECHNICAL_STAGE_WEIGHTS = {
    "resume_screening": 25,
    "assessment": 25,
    "coding_round": 20,
    "interview": 30,
}


def clamp_percent(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except Exception:
        number = default
    return max(0.0, min(100.0, round(number, 1)))


def clamp_score_10(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except Exception:
        number = default
    return max(0.0, min(10.0, round(number, 1)))


def normalize_list(items: Any, limit: int = 5) -> List[str]:
    cleaned: List[str] = []
    seen = set()
    for item in items or []:
        text = str(item or "").strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
        if len(cleaned) >= limit:
            break
    return cleaned


def _build_shortlist_reason(
    overall_score: float,
    interview_recommendation: str,
    high_risk_proctoring: bool,
    stage_scores: Dict[str, Dict[str, Any]],
) -> str:
    if high_risk_proctoring:
        return "Candidate performance was notable, but proctoring interference requires manual HR review."
    if interview_recommendation in {"strong_hire", "hire"} and overall_score >= 75:
        return "Shortlisted because the candidate showed strong resume alignment, solid assessment performance, and interview readiness."
    if overall_score >= 60:
        return "Moved to bias review because the candidate showed enough potential to justify a manual recruiter decision."
    if stage_scores["resume_screening"]["score"] < 50:
        return "Not shortlisted automatically because resume-to-role alignment stayed below the expected threshold."
    return "Not shortlisted automatically because the combined assessment and interview evidence was below the current benchmark."


def _answer_evidence(user: Dict[str, Any]) -> Dict[str, Any]:
    answers = [str(item or "").strip() for item in (user.get("virtual_answers") or [])]
    questions = [str(item or "").strip() for item in (user.get("virtual_questions") or [])]
    total_questions = len(questions) or len(answers)
    answered = [answer for answer in answers if answer]
    word_counts = [len(answer.split()) for answer in answered]
    short_answer_count = sum(1 for count in word_counts if count < 25)
    blank_answer_count = max(0, total_questions - len(answered))

    return {
        "answered_count": len(answered),
        "total_questions": total_questions,
        "avg_answer_words": round(sum(word_counts) / len(word_counts), 1) if word_counts else 0.0,
        "short_answer_count": short_answer_count,
        "blank_answer_count": blank_answer_count,
    }


def _build_interview_evidence(user: Dict[str, Any], limit: int = 4) -> List[Dict[str, str]]:
    questions = [str(item or "").strip() for item in (user.get("virtual_questions") or [])]
    answers = [str(item or "").strip() for item in (user.get("virtual_answers") or [])]
    evidence: List[Dict[str, str]] = []
    for index, question in enumerate(questions):
        answer = answers[index] if index < len(answers) else ""
        if not question and not answer:
            continue
        evidence.append({
            "question": question,
            "answer": answer,
            "summary": (
                answer[:180] + "..."
                if len(answer) > 180
                else answer or "No usable answer captured."
            ),
        })
        if len(evidence) >= limit:
            break
    return evidence


def _build_hr_recommendation(
    overall_score: float,
    mcq_score: float,
    live_interview_score: float,
    recommendation: str,
    answer_evidence: Dict[str, Any],
    violation_count: int,
    high_risk_proctoring: bool,
) -> Dict[str, str]:
    answered_count = int(answer_evidence.get("answered_count") or 0)
    total_questions = max(1, int(answer_evidence.get("total_questions") or 1))
    avg_words = float(answer_evidence.get("avg_answer_words") or 0.0)
    short_count = int(answer_evidence.get("short_answer_count") or 0)
    completion_rate = answered_count / total_questions

    if high_risk_proctoring or violation_count >= 3:
        action = "Manual review required before any hiring decision"
        reason = (
            "Do not advance automatically. The candidate's test session triggered proctoring risk, "
            "so HR should review the recording, warnings, and answer quality before deciding."
        )
    elif live_interview_score >= 7.5 and overall_score >= 70 and completion_rate >= 0.8 and avg_words >= 35:
        action = "Advance to HR or final interview"
        reason = (
            "The candidate gave sufficiently complete avatar interview answers, maintained a strong final interview score, "
            "and has enough combined resume and MCQ evidence to justify moving forward."
        )
    elif live_interview_score >= 5.5 and overall_score >= 55 and completion_rate >= 0.6:
        action = "Keep on hold for focused HR review"
        reason = (
            "The candidate showed partial fit, but HR should ask follow-up questions on weaker areas before shortlisting. "
            "Review answer depth, role-specific examples, and communication clarity."
        )
    elif answered_count == 0 or completion_rate < 0.5:
        action = "Do not advance unless HR has external evidence"
        reason = (
            "The avatar interview has too few usable answers for a confident recommendation. "
            "HR should only continue if the resume or prior screening contains strong independent evidence."
        )
    else:
        action = "Reject or place in backup pool"
        reason = (
            "Based on the submitted answers and combined assessment score, the candidate does not yet show enough evidence "
            "for the next stage. HR may keep the profile as backup if the role has low candidate volume."
        )

    if short_count and answered_count:
        reason += f" {short_count} answered response(s) were short, so probe for more detailed examples if HR continues."
    if recommendation:
        reason += f" AI interview recommendation signal: {recommendation}."
    if mcq_score < 60:
        reason += " MCQ performance is below the normal promotion benchmark."

    return {
        "action": action,
        "reason": reason,
    }


def build_candidate_report(
    user: Optional[Dict[str, Any]],
    interview_evaluation: Optional[Dict[str, Any]] = None,
    proctoring_summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    user = user or {}
    interview_evaluation = interview_evaluation or {}
    proctoring_summary = proctoring_summary or {}
    assessment_track = str(user.get("assessment_track") or "").strip().lower()
    weights = TECHNICAL_STAGE_WEIGHTS if assessment_track == "technical" else STANDARD_STAGE_WEIGHTS

    ats_score = clamp_percent(user.get("ats_score"), default=0.0)
    mcq_score = clamp_percent(
        user.get("mcq_score_percent"),
        default=clamp_score_10(user.get("score"), default=0.0) * 10,
    )
    coding_score = clamp_percent(
        user.get("coding_score"),
        default=0.0,
    ) * 10 if user.get("coding_score") not in (None, "") else 0.0
    live_interview_score = clamp_percent(
        interview_evaluation.get("final_score"),
        default=clamp_score_10(
            user.get("interview_score", user.get("virtual_score")),
            default=0.0,
        ) * 10,
    )

    stage_scores = {
        "resume_screening": {
            "label": "Resume Analyzer",
            "score": ats_score,
            "weight": weights["resume_screening"],
            "status": "completed" if ats_score > 0 else "pending",
        },
        "assessment": {
            "label": "Stage 1 - MCQ Assessment",
            "score": mcq_score,
            "weight": weights["assessment"],
            "status": "completed" if user.get("interview_taken") else "pending",
        },
        "interview": {
            "label": "Stage 3 - AI Interview" if assessment_track == "technical" else "Stage 2 - AI Interview",
            "score": live_interview_score,
            "weight": weights["interview"],
            "status": "completed" if (user.get("virtual_taken") or user.get("interview_status") == "completed" or interview_evaluation) else "pending",
        },
    }
    if assessment_track == "technical":
        stage_scores["coding_round"] = {
            "label": "Stage 2 - Coding Round",
            "score": coding_score,
            "weight": weights["coding_round"],
            "status": "completed" if user.get("coding_taken") else "pending",
        }

    weighted_total = 0.0
    for stage in stage_scores.values():
        weighted_total += (stage["score"] * stage["weight"]) / 100.0
    overall_score = round(weighted_total, 1)

    matched_keywords = normalize_list(((user.get("ats_breakdown") or {}).get("matched_keywords") or []), limit=3)
    missing_keywords = normalize_list(((user.get("ats_breakdown") or {}).get("missing_keywords") or []), limit=3)
    coding_feedback = str(user.get("coding_feedback") or "").strip()
    interview_strengths = normalize_list(
        interview_evaluation.get("strengths") or (user.get("virtual_report") or {}).get("strengths") or [],
        limit=4,
    )
    interview_weaknesses = normalize_list(
        interview_evaluation.get("weaknesses")
        or interview_evaluation.get("areas_for_improvement")
        or (user.get("virtual_report") or {}).get("improvements")
        or [],
        limit=4,
    )

    strengths = normalize_list(
        matched_keywords
        + interview_strengths
        + (["Submitted a completed coding round with implementable logic."] if assessment_track == "technical" and user.get("coding_taken") else [])
        + (
            ["Performed well in the assessment round."]
            if mcq_score >= 70
            else ["Assessment performance indicates baseline capability."]
            if mcq_score >= 50
            else []
        ),
        limit=5,
    )

    weaknesses = normalize_list(
        interview_weaknesses
        + ([f"Resume missed some target keywords: {', '.join(missing_keywords)}."] if missing_keywords else [])
        + ([coding_feedback] if assessment_track == "technical" and coding_feedback else [])
        + (
            ["Assessment accuracy needs improvement."]
            if mcq_score and mcq_score < 60
            else []
        ),
        limit=5,
    )

    violation_count = int(proctoring_summary.get("violation_count") or user.get("virtual_proctoring_violations") or user.get("mcq_proctoring_violations") or 0)
    critical_flags = normalize_list(proctoring_summary.get("critical_flags") or [], limit=3)
    high_risk_proctoring = bool(critical_flags) or violation_count >= 3

    recommendation = str(
        interview_evaluation.get("recommendation")
        or user.get("interview_recommendation")
        or (user.get("virtual_report") or {}).get("recommendation")
        or ""
    ).strip().lower()

    if high_risk_proctoring:
        shortlist_decision = "bias_review"
    elif recommendation in {"strong_hire", "hire"} and overall_score >= 75:
        shortlist_decision = "shortlisted"
    elif overall_score >= 60:
        shortlist_decision = "bias_review"
    else:
        shortlist_decision = "rejected"

    shortlist_reason = _build_shortlist_reason(
        overall_score,
        recommendation,
        high_risk_proctoring,
        stage_scores,
    )
    answer_evidence = _answer_evidence(user)
    hr_recommendation = _build_hr_recommendation(
        overall_score,
        mcq_score,
        live_interview_score,
        recommendation,
        answer_evidence,
        violation_count,
        high_risk_proctoring,
    )

    return {
        "overall_score": overall_score,
        "score_out_of": 100,
        "stage_scores": stage_scores,
        "shortlist_decision": shortlist_decision,
        "shortlist_reason": shortlist_reason,
        "strengths": strengths or ["Resume matched the role sufficiently to continue evaluation."],
        "weaknesses": weaknesses or ["More role-specific evidence is needed for stronger confidence."],
        "proctoring_summary": {
            "violation_count": violation_count,
            "critical_flags": critical_flags,
            "status": "review_required" if high_risk_proctoring else "clear",
        },
        "final_recommendation": recommendation or shortlist_decision,
        "hr_recommendation": hr_recommendation,
        "answer_evidence": answer_evidence,
        "interview_evidence": _build_interview_evidence(user),
        "hiring_rationale": str(
            hr_recommendation.get("reason")
            or interview_evaluation.get("hiring_rationale")
            or (user.get("virtual_report") or {}).get("performance_summary")
            or shortlist_reason
        ).strip(),
    }
