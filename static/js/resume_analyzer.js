// Resume Analyzer Module
// Utility functions for text processing

function splitCsv(value) {
    return String(value || "")
        .split(",")
        .map(item => item.trim())
        .filter(Boolean);
}

function normalizeText(value) {
    return String(value || "").toLowerCase().replace(/[^a-z0-9+#.\s]/g, " ");
}

function tokenize(value) {
    return Array.from(new Set(normalizeText(value).split(/\s+/).filter(Boolean)));
}

function getResumeAnalysisSource(application) {
    return [
        application.resume_analysis_text,
        application.resume_text,
        application.skills,
        application.job_role,
        application.resume_name
    ]
        .filter(Boolean)
        .join(" ");
}

// Main resume analysis function
function analyzeResumeAgainstJob(application, job) {
    const skills = splitCsv(application.skills).map(s => s.toLowerCase());
    const resumeSource = getResumeAnalysisSource(application);
    const resumeText = normalizeText(resumeSource);
    const tokens = tokenize(`${application.skills} ${resumeSource} ${application.job_role}`);
    const required = Array.isArray(job.required_skills) ? job.required_skills : [];
    const preferred = Array.isArray(job.preferred_skills) ? job.preferred_skills : [];

    const requiredMatches = required.filter(skill => tokens.some(token => skill.toLowerCase().includes(token) || token.includes(skill.toLowerCase())));
    const preferredMatches = preferred.filter(skill => tokens.some(token => skill.toLowerCase().includes(token) || token.includes(skill.toLowerCase())));
    const requiredCoverage = required.length ? requiredMatches.length / required.length : 0;
    const preferredCoverage = preferred.length ? preferredMatches.length / preferred.length : 0;
    const roleAlignment = resumeText.includes(job.title.toLowerCase()) || normalizeText(application.job_role).includes(job.title.toLowerCase()) ? 1 : 0.65;
    const experienceHint = /\b([1-9])\+?\s*(year|yr)/.test(resumeText) ? 1 : 0.6;
    const evidenceSignals = ["project", "certification", "achievement", "led", "built", "improved", "deployed", "optimized", "implemented"];
    const evidenceCount = evidenceSignals.filter(signal => resumeText.includes(signal)).length;
    const evidenceScore = Math.min(1, evidenceCount / 4);

    const score = Math.round(
        (requiredCoverage * 45) +
        (preferredCoverage * 15) +
        (roleAlignment * 15) +
        (experienceHint * 10) +
        (Math.min(1, skills.length / Math.max(3, required.length || 3)) * 10) +
        (evidenceScore * 5)
    );

    let decision = "review";
    if (score >= Number(job.threshold || 72)) {
        decision = "shortlisted";
    } else if (score < Math.max(45, Number(job.threshold || 72) - 20)) {
        decision = "rejected";
    }

    return {
        score,
        decision,
        breakdown: {
            required_skill_match: `${requiredMatches.length}/${required.length || 0}`,
            preferred_skill_match: `${preferredMatches.length}/${preferred.length || 0}`,
            matched_keywords: requiredMatches.concat(preferredMatches).slice(0, 8),
            experience_signal: experienceHint === 1 ? "Strong" : "Limited",
            role_alignment: roleAlignment === 1 ? "Direct" : "Adjacent"
        },
        summary: decision === "shortlisted"
            ? `ATS shortlisted this profile with ${score}% match for ${job.title}.`
            : decision === "review"
                ? `ATS suggests recruiter review with ${score}% match for ${job.title}.`
                : `ATS rejected this profile with ${score}% match for ${job.title}.`
    };
}
