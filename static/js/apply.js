// ============ APPLICATION FORM LOGIC ============
(function () {

  let selectedJob = null;
  let currentStep = 1;
  const totalSteps = 3;

  // ---- JOB DROPDOWN ----
  function initDropdown() {
    const trigger = document.getElementById('job-dropdown-trigger');
    const list = document.getElementById('job-dropdown-list');
    const selectedText = document.getElementById('selected-job-text');
    if (!trigger || !list) return;

    const jobs = ZyraJobs.getJobs().filter(j => j.status === 'Active');

    // Populate list
    list.innerHTML = '';
    jobs.forEach(job => {
      const item = document.createElement('div');
      item.className = 'dropdown-item';
      item.dataset.id = job.id;
      item.innerHTML = `
        <div class="dropdown-item-title">${job.title}</div>
        <div class="dropdown-item-meta">
          <span class="dropdown-tag">${job.dept}</span>
          <span class="dropdown-tag">${job.location}</span>
          <span class="dropdown-tag">${job.type}</span>
        </div>
      `;
      item.addEventListener('click', () => {
        selectJob(job);
        closeDropdown();
      });
      list.appendChild(item);
    });

    // Toggle
    trigger.addEventListener('click', (e) => {
      e.stopPropagation();
      const isOpen = list.classList.contains('open');
      if (isOpen) closeDropdown(); else openDropdown();
    });

    document.addEventListener('click', closeDropdown);
    list.addEventListener('click', e => e.stopPropagation());

    // Pre-select if URL param
    const params = new URLSearchParams(window.location.search);
    const preJobId = params.get('job');
    if (preJobId) {
      const job = jobs.find(j => j.id === preJobId);
      if (job) selectJob(job);
    }
  }

  function openDropdown() {
    document.getElementById('job-dropdown-trigger').classList.add('open');
    document.getElementById('job-dropdown-list').classList.add('open');
  }

  function closeDropdown() {
    document.getElementById('job-dropdown-trigger')?.classList.remove('open');
    document.getElementById('job-dropdown-list')?.classList.remove('open');
  }

  function selectJob(job) {
    selectedJob = job;
    const text = document.getElementById('selected-job-text');
    if (text) text.textContent = job.title;

    // Highlight in list
    document.querySelectorAll('.dropdown-item').forEach(el => {
      el.classList.toggle('selected', el.dataset.id === job.id);
    });

    // Update preview
    renderJobPreview(job);

    // Update form header
    const formJobName = document.getElementById('form-job-name');
    if (formJobName) formJobName.textContent = job.title;

    // Enable next btn
    updateNavButtons();
  }

  function renderJobPreview(job) {
    const preview = document.getElementById('job-preview');
    if (!preview) return;
    preview.innerHTML = `
      <div class="job-preview-dept">${job.dept} · ${job.type}</div>
      <div class="job-preview-title">${job.title}</div>
      <div class="job-preview-tags">
        <div class="job-preview-tag"><span class="tag-icon">📍</span>${job.location}</div>
        <div class="job-preview-tag"><span class="tag-icon">💰</span>${job.salary}</div>
        <div class="job-preview-tag"><span class="tag-icon">🕒</span>Posted ${job.posted}</div>
      </div>
      <div class="job-preview-divider"></div>
      <p class="job-preview-desc">${job.desc}</p>
      <div class="job-skills-title">Required Skills</div>
      <div class="job-skills-wrap">
        ${job.skills.map(s => `<span class="skill-pill">${s}</span>`).join('')}
      </div>
    `;
    preview.style.display = 'block';
    document.getElementById('no-job-selected')?.setAttribute('style', 'display:none');
  }

  // ---- STEP NAVIGATION ----
  function goToStep(step) {
    if (step < 1 || step > totalSteps) return;

    // Validate before advancing
    if (step > currentStep && !validateCurrentStep()) return;

    currentStep = step;

    // Update step UI
    document.querySelectorAll('.form-step').forEach((el, i) => {
      const s = i + 1;
      el.classList.toggle('active', s === currentStep);
      el.classList.toggle('done', s < currentStep);
    });

    document.querySelectorAll('.step-circle').forEach((el, i) => {
      const s = i + 1;
      el.classList.remove('active', 'done');
      if (s === currentStep) el.classList.add('active');
      else if (s < currentStep) { el.classList.add('done'); el.textContent = '✓'; }
      else el.textContent = s;
    });

    document.querySelectorAll('.step-line').forEach((el, i) => {
      el.classList.toggle('done', i + 1 < currentStep);
    });

    // Show correct page
    document.querySelectorAll('.form-step-page').forEach((el, i) => {
      el.classList.toggle('active', i + 1 === currentStep);
    });

    updateNavButtons();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function updateNavButtons() {
    const prevBtn = document.getElementById('btn-prev');
    const nextBtn = document.getElementById('btn-next');
    const submitBtn = document.getElementById('btn-submit');

    if (prevBtn) prevBtn.style.display = currentStep > 1 ? 'flex' : 'none';
    if (nextBtn) {
      nextBtn.style.display = currentStep < totalSteps ? 'flex' : 'none';
      nextBtn.disabled = !selectedJob;
    }
    if (submitBtn) submitBtn.style.display = currentStep === totalSteps ? 'flex' : 'none';
  }

  // ---- VALIDATION ----
  function validateCurrentStep() {
    if (currentStep === 1) {
      if (!selectedJob) {
        showToast('Please select a job position first.', 'error');
        return false;
      }
      const required = ['fname', 'lname', 'email', 'phone'];
      let valid = true;
      required.forEach(id => {
        const el = document.getElementById(id);
        if (el && !el.value.trim()) {
          el.classList.add('error');
          showFieldError(id);
          valid = false;
        }
      });
      if (!valid) { showToast('Please fill in all required fields.', 'error'); return false; }
      // Email format
      const email = document.getElementById('email');
      if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.value)) {
        email.classList.add('error');
        showToast('Please enter a valid email address.', 'error');
        return false;
      }
    }

    if (currentStep === 2) {
      const exp = document.getElementById('experience');
      if (exp && !exp.value) {
        exp.classList.add('error');
        showToast('Please select your years of experience.', 'error');
        return false;
      }
      const cover = document.getElementById('cover_letter');
      if (cover && cover.value.trim().length < 50) {
        cover.classList.add('error');
        showToast('Please write a cover letter (at least 50 characters).', 'error');
        return false;
      }
    }

    return true;
  }

  function showFieldError(id) {
    const errEl = document.getElementById(id + '-error');
    if (errEl) errEl.classList.add('show');
    const input = document.getElementById(id);
    if (input) {
      input.addEventListener('input', () => {
        input.classList.remove('error');
        errEl?.classList.remove('show');
      }, { once: true });
    }
  }

  // ---- SUBMIT ----
  async function handleSubmit() {
    if (!validateCurrentStep()) return;
    if (!selectedJob) { showToast('Please select a job first.', 'error'); return; }

    const btn = document.getElementById('btn-submit');
    if (btn) { btn.textContent = 'Submitting...'; btn.disabled = true; }

    const resumeInput = document.getElementById('resume-input');
    const resumeFile = resumeInput?.files?.[0];
    if (!resumeFile) {
      showToast('Please upload your resume as a PDF.', 'error');
      if (btn) { btn.textContent = 'Submit Application'; btn.disabled = false; }
      return;
    }
    if (!String(resumeFile.name || '').toLowerCase().endsWith('.pdf')) {
      showToast('Backend screening currently accepts PDF resumes only.', 'error');
      if (btn) { btn.textContent = 'Submit Application'; btn.disabled = false; }
      return;
    }

    const selectedSkills = [...document.querySelectorAll('.skill-toggle.selected')].map(el => el.dataset.skill);
    const skills = selectedSkills.length ? selectedSkills : (selectedJob.skills || []);

    const applicationData = {
      jobId: selectedJob.id,
      jobTitle: selectedJob.title,
      dept: selectedJob.dept,
      firstName: document.getElementById('fname')?.value || '',
      lastName: document.getElementById('lname')?.value || '',
      email: document.getElementById('email')?.value || '',
      phone: document.getElementById('phone')?.value || '',
      location: document.getElementById('location')?.value || '',
      linkedin: document.getElementById('linkedin')?.value || '',
      portfolio: document.getElementById('portfolio')?.value || '',
      experience: document.getElementById('experience')?.value || '',
      currentRole: document.getElementById('current_role')?.value || '',
      education: document.getElementById('education')?.value || '',
      coverLetter: document.getElementById('cover_letter')?.value || '',
      availability: document.getElementById('availability')?.value || '',
      salary: document.getElementById('expected_salary')?.value || '',
      resume: document.getElementById('resume-name')?.textContent || resumeFile.name,
      skills,
    };

    const payload = new FormData();
    payload.append('first_name', applicationData.firstName);
    payload.append('last_name', applicationData.lastName);
    payload.append('email', applicationData.email);
    payload.append('phone', applicationData.phone);
    payload.append('skills', skills.join(', '));
    payload.append('job_role', selectedJob.title);
    payload.append('resume', resumeFile);

    try {
      const response = await fetch('/api/apply', {
        method: 'POST',
        body: payload,
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(result.error || result.message || 'Application submission failed.');
      }

      const saved = ZyraJobs.saveApplication({
        ...applicationData,
        backendApplicationId: result.application_id,
        atsScore: result.ats_score,
        atsDecision: result.ats_decision,
        credentialsGenerated: result.credentials_generated,
        credentialsEmailSent: result.credentials_email_sent,
      });

      showSuccessScreen(result.application_id || saved.id, applicationData);
      if (result.credentials_email_sent) {
        showToast(`Resume score ${result.ats_score}%. Credentials sent to ${applicationData.email}.`);
      } else if (result.credentials_generated) {
        showToast(`Resume score ${result.ats_score}%. Credentials generated, but email failed.`, 'error');
      } else {
        showToast(`Resume score ${result.ats_score}%. Application sent for HR review.`);
      }
    } catch (error) {
      showToast(error.message || 'Application submission failed.', 'error');
      if (btn) { btn.textContent = 'Submit Application'; btn.disabled = false; }
    }
  }

  function showSuccessScreen(appId, data) {
    document.getElementById('form-container').style.display = 'none';
    const success = document.getElementById('success-screen');
    if (!success) return;
    success.classList.add('show');

    const refEl = document.getElementById('app-reference');
    if (refEl) refEl.textContent = 'Application Reference: ' + appId.toUpperCase();

    const nameEl = document.getElementById('success-name');
    if (nameEl) nameEl.textContent = data.firstName + ' ' + data.lastName;

    const jobEl = document.getElementById('success-job');
    if (jobEl) jobEl.textContent = data.jobTitle;
  }

  // ---- SKILL TOGGLES ----
  function initSkillToggles() {
    document.querySelectorAll('.skill-toggle').forEach(btn => {
      btn.addEventListener('click', () => btn.classList.toggle('selected'));
    });
  }

  // ---- STAR RATINGS ----
  function initStarRatings() {
    document.querySelectorAll('.star-rating').forEach(group => {
      const stars = group.querySelectorAll('.star-btn');
      stars.forEach((star, i) => {
        star.addEventListener('click', () => {
          stars.forEach((s, j) => s.classList.toggle('lit', j <= i));
          group.dataset.rating = i + 1;
        });
        star.addEventListener('mouseover', () => {
          stars.forEach((s, j) => s.style.color = j <= i ? '#f59e0b' : '');
        });
        star.addEventListener('mouseout', () => {
          const rating = parseInt(group.dataset.rating || 0);
          stars.forEach((s, j) => s.style.color = j < rating ? '#f59e0b' : '');
        });
      });
    });
  }

  // ---- FILE UPLOAD ----
  function initFileUpload() {
    const zone = document.getElementById('resume-zone');
    const input = document.getElementById('resume-input');
    const nameDisplay = document.getElementById('resume-name-display');
    const nameSpan = document.getElementById('resume-name');
    const removeBtn = document.getElementById('resume-remove');

    if (!zone || !input) return;

    zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('dragover'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
    zone.addEventListener('drop', e => {
      e.preventDefault();
      zone.classList.remove('dragover');
      const file = e.dataTransfer.files[0];
      if (file) handleFile(file);
    });

    input.addEventListener('change', () => {
      if (input.files[0]) handleFile(input.files[0]);
    });

    function handleFile(file) {
      if (file.size > 5 * 1024 * 1024) { showToast('File too large. Max 5MB.', 'error'); return; }
      if (!String(file.name || '').toLowerCase().endsWith('.pdf')) {
        showToast('Please upload a PDF resume.', 'error');
        return;
      }
      zone.classList.add('has-file');
      nameDisplay.classList.add('show');
      nameSpan.textContent = file.name;
      document.querySelector('.file-upload-title').textContent = 'Resume uploaded!';
    }

    removeBtn?.addEventListener('click', () => {
      input.value = '';
      zone.classList.remove('has-file');
      nameDisplay.classList.remove('show');
      document.querySelector('.file-upload-title').textContent = 'Upload your Resume / CV';
    });
  }

  // ---- CHARACTER COUNTER ----
  function initCharCounters() {
    document.querySelectorAll('[data-maxlen]').forEach(el => {
      const maxLen = parseInt(el.dataset.maxlen);
      const counterId = el.dataset.counter;
      const counter = document.getElementById(counterId);
      el.addEventListener('input', () => {
        const len = el.value.length;
        if (counter) {
          counter.textContent = len + ' / ' + maxLen;
          counter.className = 'field-counter' + (len > maxLen * 0.9 ? ' warn' : '') + (len > maxLen ? ' over' : '');
        }
        if (len > maxLen) el.value = el.value.substring(0, maxLen);
      });
    });
  }

  // ---- TOAST ----
  function showToast(msg, type = 'info') {
    let toast = document.getElementById('zyra-toast');
    if (!toast) {
      toast = document.createElement('div');
      toast.id = 'zyra-toast';
      toast.style.cssText = `
        position:fixed;bottom:24px;right:24px;z-index:9999;
        padding:13px 20px;border-radius:12px;font-family:var(--font-head);
        font-size:13px;font-weight:600;color:white;
        box-shadow:0 8px 30px rgba(0,0,0,0.2);
        transform:translateY(20px);opacity:0;
        transition:all 0.3s cubic-bezier(0.4,0,0.2,1);
        max-width:320px;line-height:1.4;
      `;
      document.body.appendChild(toast);
    }
    toast.textContent = msg;
    toast.style.background = type === 'error' ? 'linear-gradient(135deg,#ef4444,#dc2626)' : 'linear-gradient(135deg,#1e3a5f,#0f1d31)';
    requestAnimationFrame(() => {
      toast.style.opacity = '1';
      toast.style.transform = 'translateY(0)';
    });
    clearTimeout(toast._timeout);
    toast._timeout = setTimeout(() => {
      toast.style.opacity = '0'; toast.style.transform = 'translateY(10px)';
    }, 3000);
  }

  // ---- INIT ----
  document.addEventListener('DOMContentLoaded', () => {
    initDropdown();
    initSkillToggles();
    initStarRatings();
    initFileUpload();
    initCharCounters();

    document.getElementById('btn-prev')?.addEventListener('click', () => goToStep(currentStep - 1));
    document.getElementById('btn-next')?.addEventListener('click', () => goToStep(currentStep + 1));
    document.getElementById('btn-submit')?.addEventListener('click', handleSubmit);

    // Step indicators clickable
    document.querySelectorAll('.step-circle').forEach((el, i) => {
      el.addEventListener('click', () => { if (i + 1 < currentStep) goToStep(i + 1); });
    });

    // Remove error on input
    document.querySelectorAll('.field-input').forEach(el => {
      el.addEventListener('input', () => el.classList.remove('error'));
    });

    goToStep(1);
    updateNavButtons();
  });

})();
