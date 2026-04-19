// MCQ Test Logic - backed by /api/start_test.
(function () {
  let questions = [];
  let currentQ = 1;
  let answers = {};
  let testId = null;
  let timerInterval;
  let seconds = 60 * 60;
  let proctor = null;
  let submitting = false;
  let pendingAutoSubmit = false;

  function lockForSubmit(message) {
    document.querySelectorAll('button, input').forEach(el => {
      el.disabled = true;
    });
    const qText = document.getElementById('q-text');
    if (qText) qText.textContent = message || 'Submitting test...';
  }

  function renderTimer() {
    const el = document.getElementById('timer');
    if (!el) return;
    const m = Math.floor(seconds / 60).toString().padStart(2, '0');
    const s = (seconds % 60).toString().padStart(2, '0');
    el.textContent = m + ':' + s;
    if (seconds < 300) el.style.color = '#ef4444';
  }

  function startTimer() {
    clearInterval(timerInterval);
    renderTimer();
    timerInterval = setInterval(() => {
      if (seconds > 0) {
        seconds--;
        renderTimer();
      } else {
        clearInterval(timerInterval);
        submitTest(true);
      }
    }, 1000);
  }

  function renderNav() {
    const nav = document.getElementById('q-nav');
    if (!nav) return;
    nav.innerHTML = '';
    questions.forEach((question, index) => {
      const qNumber = index + 1;
      const btn = document.createElement('button');
      btn.className = 'q-num' + (qNumber === currentQ ? ' active' : '') + (answers[question.id] !== undefined ? ' answered' : '');
      btn.textContent = qNumber;
      btn.addEventListener('click', () => goToQ(qNumber));
      nav.appendChild(btn);
    });
  }

  function goToQ(n) {
    if (submitting) return;
    currentQ = Math.max(1, Math.min(n, questions.length));
    loadQuestion(currentQ);
    renderNav();
  }

  function loadQuestion(n) {
    const q = questions[n - 1];
    const qText = document.getElementById('q-text');
    const optsCont = document.getElementById('options-container');
    const qNum = document.getElementById('q-num-label');
    if (!q) return;

    if (qText) {
      qText.style.opacity = '0';
      setTimeout(() => {
        qText.textContent = q.question;
        qText.style.opacity = '1';
        qText.style.transition = 'opacity 0.3s';
      }, 100);
    }

    if (qNum) qNum.textContent = n + '/' + questions.length;
    const progress = document.getElementById('mcq-progress');
    if (progress) progress.textContent = `Question ${n} of ${questions.length}`;

    if (optsCont) {
      optsCont.innerHTML = '';
      (q.options || []).forEach((opt, i) => {
        const btn = document.createElement('button');
        btn.className = 'option-btn' + (answers[q.id] === i ? ' selected' : '');
        btn.innerHTML = `<span class="option-radio">${answers[q.id] === i ? '&#10003;' : ''}</span>${escapeHtml(opt)}`;
        btn.addEventListener('click', () => selectOption(q.id, i, btn));
        optsCont.appendChild(btn);
      });
    }
  }

  function escapeHtml(value) {
    return String(value || '').replace(/[&<>"']/g, char => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#039;'
    }[char]));
  }

  function selectOption(questionId, optIdx, clickedBtn) {
    if (submitting) return;
    answers[questionId] = optIdx;
    document.querySelectorAll('.option-btn').forEach(b => {
      b.classList.remove('selected');
      const radio = b.querySelector('.option-radio');
      if (radio) radio.textContent = '';
    });
    clickedBtn.classList.add('selected');
    const radio = clickedBtn.querySelector('.option-radio');
    if (radio) radio.innerHTML = '&#10003;';
    renderNav();

    setTimeout(() => {
      if (currentQ < questions.length) goToQ(currentQ + 1);
    }, 350);
  }

  async function startTest() {
    const qText = document.getElementById('q-text');
    if (qText) qText.textContent = 'Generating your 20 assessment questions...';

    try {
      const response = await fetch('/api/start_test', { method: 'POST' });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.error || 'Unable to start MCQ test.');
      }
      testId = data.test_id;
      questions = Array.isArray(data.questions) ? data.questions : [];
      if (!questions.length) throw new Error('No MCQ questions were generated.');
      seconds = Number(data.duration_seconds) || seconds;
      currentQ = 1;
      answers = {};
      loadQuestion(1);
      renderNav();
      if (pendingAutoSubmit) {
        submitTest(true);
      } else {
        startTimer();
      }
    } catch (error) {
      if (qText) qText.textContent = error.message || 'Unable to load MCQ questions.';
      if (String(error.message || '').toLowerCase().includes('unauthorized')) {
        setTimeout(() => { window.location.href = 'user_login.html'; }, 1200);
      }
    }
  }

  async function submitTest(autoTriggered = false) {
    if (!testId) {
      if (autoTriggered) {
        pendingAutoSubmit = true;
        lockForSubmit('Violation limit reached. Auto-submitting as soon as the test is ready...');
      }
      return;
    }
    if (submitting) return;
    const answered = Object.keys(answers).length;
    if (!autoTriggered && !confirm(`Submit test?\n\nAnswered: ${answered}/${questions.length}\n\nProceed?`)) {
      return;
    }
    submitting = true;
    lockForSubmit(autoTriggered ? 'Violation limit reached. Auto-submitting MCQ test...' : 'Submitting test...');

    const submitBtn = document.getElementById('submit-test-btn');
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = 'Submitting...';
    }

    const payload = {
      test_id: testId,
      answers: questions.map(q => ({
        id: q.id,
        answer: answers[q.id] ?? null
      })),
      proctoring_violations: proctor?.violations || 0,
      auto_submitted: Boolean(autoTriggered)
    };

    try {
      await proctor?.stopAndUpload({ test_id: testId, auto_submitted: Boolean(autoTriggered) });
      const response = await fetch('/api/submit_test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.error || 'Unable to submit MCQ test.');
      }
      localStorage.setItem('zyra_candidate_state', JSON.stringify({
        interview_taken: true,
        virtual_round_enabled: Boolean(data.promoted_to_virtual),
        virtual_taken: false,
        mcq_total_questions: data.total_questions,
        score: data.score
      }));
      alert(`MCQ submitted.\nScore: ${data.score_percent}%\n${data.promoted_to_virtual ? 'AI Avatar Interview is now unlocked.' : 'AI Avatar Interview remains locked pending review.'}`);
      window.location.href = 'user_dashboard.html';
    } catch (error) {
      alert(error.message || 'Unable to submit MCQ test.');
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Submit Test';
      }
      submitting = false;
    }
  }

  document.addEventListener('DOMContentLoaded', async () => {
    try {
      proctor = new window.ZyraProctor({
        assessmentType: 'mcq',
        videoElement: document.getElementById('proctor-video'),
        onAutoSubmit: () => submitTest(true)
      });
      await proctor.start();
      await startTest();
    } catch (error) {
      const qText = document.getElementById('q-text');
      if (qText) qText.textContent = error.message || 'Camera and microphone access is required to start the test.';
    }

    const submitBtn = document.getElementById('submit-test-btn');
    if (submitBtn) submitBtn.addEventListener('click', () => submitTest(false));

    const reviewBtn = document.getElementById('review-later-btn');
    if (reviewBtn) reviewBtn.addEventListener('click', () => {
      if (currentQ < questions.length) goToQ(currentQ + 1);
    });
  });
})();
