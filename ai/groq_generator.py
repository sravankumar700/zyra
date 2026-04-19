"""
Groq-powered real-time question generation for AI interviews.
Supports adaptive difficulty based on candidate responses.
"""

import json
import re
from typing import Optional, Dict, List, Tuple
from config import Config
import requests


class GroqInterviewGenerator:
    """
    Generate interview questions in real-time using Groq LLM.
    Adapts difficulty based on candidate responses.
    """

    def __init__(self):
        self.api_key = Config.GROQ_API_KEY
        self.model = Config.GROQ_MODEL
        self.base_url = "https://api.groq.com/openai/v1/chat/completions"
        self.temp = Config.GROQ_INTERVIEW_TEMP
        self.max_tokens = Config.GROQ_MAX_TOKENS

    def _get_headers(self) -> Dict[str, str]:
        """Get request headers with authentication."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    def _make_request(self, messages: List[Dict]) -> Optional[str]:
        """Make API request to Groq."""
        try:
            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": self.temp,
                "max_tokens": self.max_tokens,
                "top_p": 0.95
            }

            response = requests.post(
                self.base_url,
                headers=self._get_headers(),
                json=payload,
                timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                return data["choices"][0]["message"]["content"]
            else:
                print(f"Groq API Error: {response.status_code} - {response.text}")
                return None

        except Exception as e:
            print(f"Groq request failed: {str(e)}")
            return None

    def generate_initial_questions(
        self,
        job_title: str,
        job_description: str,
        candidate_resume: Optional[str] = None,
        num_questions: int = 3
    ) -> Tuple[List[Dict], Optional[str]]:
        """
        Generate initial set of interview questions based on job and resume.

        Args:
            job_title: Position title (e.g., "Senior Software Engineer")
            job_description: Job description/requirements
            candidate_resume: Candidate's resume content
            num_questions: Number of questions to generate

        Returns:
            Tuple of (questions_list, error_message)
            Each question has: id, question_text, category, difficulty, expected_competencies
        """

        system_prompt = """You are an expert AI recruiter interviewer. Generate thoughtful, 
probing interview questions that assess both technical and soft skills.
Format your response as a JSON array of questions."""

        user_prompt = f"""Generate {num_questions} interview questions for a {job_title} position.

Job Description: {job_description}

{f'Candidate Resume Summary: {candidate_resume}' if candidate_resume else ''}

For each question, provide:
1. question_text: The actual question (2-3 sentences)
2. category: One of [technical, behavioral, problem_solving, culture_fit]
3. difficulty: One of [easy, medium, hard]
4. expected_competencies: List of 2-3 key competencies being tested
5. follow_up_hints: 2-3 follow-up questions for deeper assessment

Return ONLY valid JSON array, no markdown or extra text."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        response = self._make_request(messages)
        if not response:
            return [], "Failed to generate questions"

        try:
            questions = self._parse_json_response(response)
            if isinstance(questions, list) and questions:
                return questions, None
            return [], "Invalid response format"
        except Exception as e:
            return [], str(e)

    def generate_adaptive_question(
        self,
        previous_questions: List[Dict],
        previous_responses: List[Dict],
        difficulty_level: str,
        job_context: str
    ) -> Tuple[Optional[Dict], Optional[str]]:
        """
        Generate next question adaptively based on previous performance.

        Args:
            previous_questions: Previously asked questions
            previous_responses: Candidate's responses with performance scores
            difficulty_level: Current difficulty (easy, medium, hard)
            job_context: Job title and key requirements

        Returns:
            Tuple of (next_question, error_message)
        """

        # Analyze performance
        avg_score = sum(
            (r.get("evaluation", {}) or {}).get("score", r.get("score", 0))
            for r in previous_responses
        ) / len(
            previous_responses
        ) if previous_responses else 0.5

        # Adjust difficulty
        if avg_score > 0.75:
            next_difficulty = "hard"
        elif avg_score > 0.5:
            next_difficulty = "medium"
        else:
            next_difficulty = "easy"

        system_prompt = """You are an adaptive interview system. Generate the next interview question
based on previous responses and performance. Adapt difficulty to challenge but not overwhelm.
Format as JSON with each question object."""

        prev_questions_text = "\n".join([
            f"- {q.get('question_text', '')[:100]}... (Difficulty: {q.get('difficulty', 'medium')})"
            for q in previous_questions[-3:]  # Last 3 questions
        ])

        prev_responses_text = "\n".join([
            f"- Response: {r.get('response_text', '')[:100]}... (Score: {(r.get('evaluation', {}) or {}).get('score', r.get('score', 0))}/10)"
            for r in previous_responses[-3:]
        ])

        user_prompt = f"""Generate the NEXT interview question for a {job_context} position.

Previous Questions (last 3):
{prev_questions_text}

Candidate's Previous Responses (last 3):
{prev_responses_text}

Average Performance: {avg_score:.1%}
Recommended Difficulty: {next_difficulty}

Generate ONE question that:
1. Builds on previous discussion topics (avoid repetition)
2. Matches the recommended difficulty level
3. Tests critical competencies not yet explored
4. Is engaging and not too similar to previous questions

Return as JSON object with:
- question_text: The question
- category: [technical/behavioral/problem_solving/culture_fit]
- difficulty: [{next_difficulty}]
- expected_competencies: List of competencies
- follow_up_hints: 2-3 follow-up questions

Return ONLY valid JSON, no markdown."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        response = self._make_request(messages)
        if not response:
            return None, "Failed to generate adaptive question"

        try:
            question = self._parse_json_response(response)
            if isinstance(question, dict):
                question["question_id"] = len(previous_questions) + 1
                return question, None
            elif isinstance(question, list) and question:
                question[0]["question_id"] = len(previous_questions) + 1
                return question[0], None
            return None, "Invalid response format"
        except Exception as e:
            return None, str(e)

    def evaluate_response(
        self,
        question: Dict,
        response_text: str,
        job_context: str
    ) -> Tuple[Dict, Optional[str]]:
        """
        Evaluate candidate's response to a question.

        Returns dict with:
        - score: 0-10
        - semantic_analysis: What the response demonstrates
        - confidence_level: high/medium/low
        - strengths: List of positive aspects
        - areas_for_improvement: List of improvement areas
        - follow_up_suggested: Suggested follow-up question
        """

        system_prompt = """You are an expert interviewer evaluating candidate responses.
Provide detailed, constructive evaluation. Be objective and fair."""

        user_prompt = f"""Evaluate this interview response:

Question Category: {question.get('category', 'general')}
Question: {question.get('question_text', '')}
Expected Competencies: {', '.join(question.get('expected_competencies', []))}

Candidate's Response:
{response_text}

Job Context: {job_context}

Provide evaluation in JSON format with:
1. score: Number 0-10 (0=no understanding, 10=excellent)
2. semantic_analysis: What this response reveals about the candidate (1-2 sentences)
3. confidence_level: "high" / "medium" / "low" (based on response articulation)
4. strengths: List of 2-3 positive aspects
5. areas_for_improvement: List of 2-3 areas to develop
6. follow_up_suggested: A specific follow-up question based on response

Be thorough but fair. Return ONLY valid JSON."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        response = self._make_request(messages)
        if not response:
            return {
                "score": 5,
                "semantic_analysis": "Unable to evaluate",
                "confidence_level": "low",
                "strengths": [],
                "areas_for_improvement": [],
                "follow_up_suggested": ""
            }, "Failed to evaluate response"

        try:
            evaluation = self._parse_json_response(response)
            if isinstance(evaluation, dict):
                return evaluation, None
            return {
                "score": 5,
                "semantic_analysis": "Invalid response format",
                "confidence_level": "low",
                "strengths": [],
                "areas_for_improvement": [],
                "follow_up_suggested": ""
            }, "Invalid evaluation format"
        except Exception as e:
            return {
                "score": 5,
                "semantic_analysis": "Evaluation error",
                "confidence_level": "low",
                "strengths": [],
                "areas_for_improvement": [],
                "follow_up_suggested": ""
            }, str(e)

    def generate_final_evaluation(
        self,
        interview_session: Dict,
        job_context: str
    ) -> Tuple[Dict, Optional[str]]:
        """
        Generate comprehensive final evaluation and hiring recommendation.

        Args:
            interview_session: Dict with all questions, responses, and evaluations
            job_context: Job title and requirements

        Returns:
            Tuple of (evaluation_report, error_message)
        """

        # Compile session data
        all_evaluations = [
            r.get("evaluation", {})
            for r in interview_session.get("responses", [])
        ]

        avg_score = sum(
            e.get("score", 5) for e in all_evaluations
        ) / len(all_evaluations) if all_evaluations else 5

        strengths_set = set()
        weaknesses_set = set()

        for eval_item in all_evaluations:
            strengths_set.update(eval_item.get("strengths", []))
            weaknesses_set.update(eval_item.get("areas_for_improvement", []))

        system_prompt = """You are an expert HR recruiter. Synthesize all interview data 
into a comprehensive hiring evaluation. Be decisive but fair."""

        user_prompt = f"""Generate final hiring evaluation:

Job Position: {job_context}
Interview Duration: {len(all_evaluations)} questions
Average Score: {avg_score:.1f}/10

Key Strengths Across Responses: {', '.join(list(strengths_set)[:5])}
Areas Needing Development: {', '.join(list(weaknesses_set)[:5])}

Individual Question Scores: {[e.get('score', 0) for e in all_evaluations]}

Based on the complete interview:
1. final_score: 0-10 overall rating
2. strengths: 3-4 key strengths
3. weaknesses: 3-4 areas for improvement
4. recommendation: "strong_hire" / "hire" / "maybe" / "reject"
5. hiring_rationale: 2-3 sentence explanation
6. onboarding_notes: Suggested areas to focus on during onboarding
7. communication_score: 0-10
8. technical_score: 0-10
9. confidence_score: 0-10
10. semantic_score: 0-10
11. nlp_score: 0-10
12. problem_solving_score: 0-10
13. difficulty_progression: one short sentence
14. performance_summary: one short paragraph

Return ONLY valid JSON."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        response = self._make_request(messages)
        if not response:
            return {
                "final_score": avg_score,
                "strengths": list(strengths_set)[:4],
                "weaknesses": list(weaknesses_set)[:4],
                "recommendation": "maybe",
                "hiring_rationale": "Unable to generate evaluation",
                "onboarding_notes": ""
            }, "Failed to generate final evaluation"

        try:
            evaluation = self._parse_json_response(response)
            if isinstance(evaluation, dict):
                return evaluation, None
            return {
                "final_score": avg_score,
                "strengths": list(strengths_set)[:4],
                "weaknesses": list(weaknesses_set)[:4],
                "recommendation": "maybe",
                "hiring_rationale": "Invalid response format",
                "onboarding_notes": ""
            }, "Invalid format"
        except Exception as e:
            return {
                "final_score": avg_score,
                "strengths": list(strengths_set)[:4],
                "weaknesses": list(weaknesses_set)[:4],
                "recommendation": "maybe",
                "hiring_rationale": str(e),
                "onboarding_notes": ""
            }, str(e)

    @staticmethod
    def _parse_json_response(text: str) -> any:
        """Safely parse JSON from LLM response."""
        text = text.strip()

        # Remove markdown code blocks
        text = re.sub(r"```json\s*", "", text)
        text = re.sub(r"```\s*", "", text).strip()

        # Try direct parsing
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try extracting first JSON object
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        # Try extracting first JSON array
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        raise ValueError("Could not extract valid JSON from response")


# Singleton instance
_groq_generator = None

def get_groq_generator() -> GroqInterviewGenerator:
    """Get or create Groq generator instance."""
    global _groq_generator
    if _groq_generator is None:
        _groq_generator = GroqInterviewGenerator()
    return _groq_generator
