// ============ ZYRA SHARED JOBS STORE ============
// Single source of truth for all job listings + applications
// Uses localStorage to persist across pages

const ZyraJobs = (function () {

  const DEFAULT_JOBS = [
    { id: 'job_001', title: "Senior Frontend Engineer",   dept: "Engineering", location: "Remote",  type: "Full-time", salary: "$90,000 – $130,000", status: "Active", posted: "2 days ago",
      desc: "Build performant, accessible UI components using React and modern CSS. Work alongside product designers and backend engineers to ship world-class features.",
      skills: ["React", "TypeScript", "CSS", "REST APIs"] },
    { id: 'job_002', title: "Senior Backend Engineer",    dept: "Engineering", location: "Remote",  type: "Full-time", salary: "$95,000 – $140,000", status: "Active", posted: "3 days ago",
      desc: "Design and scale distributed backend systems. Champion API design standards and collaborate on database architecture decisions.",
      skills: ["Node.js", "Python", "PostgreSQL", "AWS"] },
    { id: 'job_003', title: "Senior Digital Engineer",   dept: "Engineering", location: "Remote",  type: "Full-time", salary: "$85,000 – $125,000", status: "Active", posted: "5 days ago",
      desc: "Lead digital transformation initiatives and integrate cloud-native services into existing infrastructure.",
      skills: ["Cloud", "Docker", "Kubernetes", "CI/CD"] },
    { id: 'job_004', title: "Senior Financial Engineer", dept: "Finance",     location: "Hybrid",  type: "Full-time", salary: "$100,000 – $150,000", status: "Active", posted: "1 week ago",
      desc: "Model complex financial instruments and build risk analysis tools. Partner with quant teams on algorithmic strategy.",
      skills: ["Python", "SQL", "Financial Modeling", "Excel"] },
    { id: 'job_005', title: "UX Designer",               dept: "Design",      location: "Remote",  type: "Full-time", salary: "$75,000 – $110,000", status: "Active", posted: "1 week ago",
      desc: "Shape end-to-end user experiences with research-driven design. Own design systems and collaborate closely with engineers.",
      skills: ["Figma", "User Research", "Prototyping", "Design Systems"] },
    { id: 'job_006', title: "Product Manager",           dept: "Product",     location: "Hybrid",  type: "Full-time", salary: "$110,000 – $155,000", status: "Active", posted: "4 days ago",
      desc: "Drive product strategy and roadmap execution. Work cross-functionally with engineering, design, and business stakeholders.",
      skills: ["Product Strategy", "Agile", "Analytics", "Stakeholder Mgmt"] },
    { id: 'job_007', title: "Data Scientist",            dept: "Data",        location: "Remote",  type: "Full-time", salary: "$95,000 – $135,000", status: "Active", posted: "6 days ago",
      desc: "Build ML models to enhance candidate scoring and HR analytics. Deploy models to production and monitor performance.",
      skills: ["Python", "ML", "TensorFlow", "SQL"] },
  ];

  function getJobs() {
    try {
      const stored = localStorage.getItem('zyra_jobs');
      return stored ? JSON.parse(stored) : DEFAULT_JOBS;
    } catch { return DEFAULT_JOBS; }
  }

  function saveJobs(jobs) {
    try { localStorage.setItem('zyra_jobs', JSON.stringify(jobs)); } catch {}
  }

  function getApplications() {
    try {
      const stored = localStorage.getItem('zyra_applications');
      return stored ? JSON.parse(stored) : [];
    } catch { return []; }
  }

  function saveApplication(app) {
    const apps = getApplications();
    apps.unshift({ ...app, id: 'app_' + Date.now(), submittedAt: new Date().toISOString() });
    try { localStorage.setItem('zyra_applications', JSON.stringify(apps)); } catch {}
    // Increment applicant count for that job
    const jobs = getJobs();
    const job = jobs.find(j => j.id === app.jobId);
    if (job) {
      job.count = (job.count || 0) + 1;
      saveJobs(jobs);
    }
    return apps[0];
  }

  function addJob(jobData) {
    const jobs = getJobs();
    const newJob = {
      id: 'job_' + Date.now(),
      title: jobData.title,
      dept: jobData.dept || 'General',
      location: jobData.location || 'Remote',
      type: jobData.type || 'Full-time',
      salary: jobData.salary || 'Competitive',
      status: 'Active',
      count: 0,
      posted: 'Just now',
      desc: jobData.desc || 'Open position at Zyra.',
      skills: jobData.skills || [],
    };
    jobs.unshift(newJob);
    saveJobs(jobs);
    return newJob;
  }

  // Initialize localStorage with defaults if empty
  if (!localStorage.getItem('zyra_jobs')) saveJobs(DEFAULT_JOBS);

  return { getJobs, saveJobs, getApplications, saveApplication, addJob };
})();
