const professionRoles = {
    technology: {
        label: "Technology & IT",
        roles: {
            "Software Developer": [
                "Tell me about yourself and your software development background.",
                "Which programming project best reflects your problem-solving ability?",
                "How do you ensure code quality before delivery?"
            ],
            "Data Analyst": [
                "Tell me about yourself and your data analysis experience.",
                "How do you turn raw data into useful business insights?",
                "Which tools do you use to validate data accuracy?"
            ],
            "Cybersecurity Analyst": [
                "Tell me about your cybersecurity background.",
                "How do you respond when you detect a security incident?",
                "What steps do you take to reduce system vulnerabilities?"
            ]
        }
    },
    education: {
        label: "Education & Research",
        roles: {
            "Professor": [
                "Tell me about yourself and your academic teaching experience.",
                "How do you make complex subjects easier for students to understand?",
                "How do you balance teaching, research, and student mentoring?"
            ],
            "Lecturer": [
                "Tell me about your classroom teaching experience.",
                "How do you keep students engaged during lectures?",
                "How do you assess whether students are truly learning?"
            ],
            "Research Associate": [
                "Tell me about your research background.",
                "How do you structure a strong research study or project?",
                "How do you communicate findings to academic or non-academic audiences?"
            ]
        }
    },
    healthcare: {
        label: "Healthcare & Medical",
        roles: {
            "Doctor": [
                "Tell me about your clinical background.",
                "How do you handle high-pressure patient care decisions?",
                "How do you maintain empathy while managing a busy workload?"
            ],
            "Nurse": [
                "Tell me about your nursing experience.",
                "How do you prioritize patient care during a demanding shift?",
                "How do you communicate with patients and families during difficult situations?"
            ],
            "Pharmacist": [
                "Tell me about your pharmacy experience.",
                "How do you ensure medication safety and accuracy?",
                "How do you counsel patients about proper medication use?"
            ]
        }
    },
    business: {
        label: "Business & Management",
        roles: {
            "HR Manager": [
                "Tell me about your HR leadership experience.",
                "How do you approach hiring and retaining strong talent?",
                "How do you resolve workplace conflicts professionally?"
            ],
            "Marketing Executive": [
                "Tell me about your marketing background.",
                "How do you measure campaign success?",
                "How do you adapt your strategy when a campaign underperforms?"
            ],
            "Operations Manager": [
                "Tell me about your operations experience.",
                "How do you improve efficiency across teams or processes?",
                "How do you manage deadlines, people, and quality together?"
            ]
        }
    },
    engineering: {
        label: "Engineering & Technical",
        roles: {
            "Mechanical Engineer": [
                "Tell me about your mechanical engineering background.",
                "How do you approach diagnosing a design or production issue?",
                "How do you ensure safety and reliability in your work?"
            ],
            "Civil Engineer": [
                "Tell me about your civil engineering experience.",
                "How do you manage quality and compliance on a project?",
                "How do you handle unexpected site challenges?"
            ],
            "Electrical Engineer": [
                "Tell me about your electrical engineering experience.",
                "How do you troubleshoot technical failures systematically?",
                "How do you balance performance, safety, and cost?"
            ]
        }
    }
};

let questions = [];

let currentQuestion = 0;
let interviewActive = false;

initializeProfessionOptions();

async function startInterview() {
    const professionSelect = document.getElementById("professionSelect");
    const jobRoleSelect = document.getElementById("jobRoleSelect");
    const professionKey = professionSelect.value;
    const jobRole = jobRoleSelect.value;

    if (!professionKey || !jobRole) {
        alert("Please select both a profession and a job role before starting the interview.");
        return;
    }

    questions = [...professionRoles[professionKey].roles[jobRole]];
    currentQuestion = 0;
    interviewActive = true;
    document.getElementById("selected-role").innerText = `Interview Role: ${jobRole}`;

    // Start Camera
    const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
    document.getElementById("candidateVideo").srcObject = stream;

    askQuestion();
}

function askQuestion() {
    if (!interviewActive) {
        return;
    }

    if (currentQuestion >= questions.length) {
        speak("Thank you for attending the interview. Goodbye.");
        interviewActive = false;
        return;
    }

    let question = questions[currentQuestion];
    document.getElementById("ai-text").innerText = question;

    speak(question);
    currentQuestion++;
}

function speak(text) {
    let speech = new SpeechSynthesisUtterance(text);
    speech.lang = "en-US";
    speech.onend = () => {
        startListening();
    };
    window.speechSynthesis.speak(speech);
}

function startListening() {
    if (!interviewActive) {
        return;
    }

    const recognition = new (window.SpeechRecognition || window.webkitSpeechRecognition)();
    recognition.lang = "en-US";

    recognition.onresult = function(event) {
        let answer = event.results[0][0].transcript;
        console.log("Candidate Answer:", answer);

        setTimeout(() => {
            askQuestion();
        }, 2000);
    };

    recognition.start();
}

function initializeProfessionOptions() {
    const professionSelect = document.getElementById("professionSelect");

    Object.entries(professionRoles).forEach(([key, profession]) => {
        const option = document.createElement("option");
        option.value = key;
        option.textContent = profession.label;
        professionSelect.appendChild(option);
    });
}

function updateJobRoles() {
    const professionKey = document.getElementById("professionSelect").value;
    const jobRoleSelect = document.getElementById("jobRoleSelect");
    const selectedRoleLabel = document.getElementById("selected-role");

    jobRoleSelect.innerHTML = '<option value="">Select job role</option>';
    selectedRoleLabel.innerText = "No role selected";

    if (!professionKey) {
        return;
    }

    const roleNames = Object.keys(professionRoles[professionKey].roles);
    roleNames.forEach((roleName) => {
        const option = document.createElement("option");
        option.value = roleName;
        option.textContent = roleName;
        jobRoleSelect.appendChild(option);
    });
}
