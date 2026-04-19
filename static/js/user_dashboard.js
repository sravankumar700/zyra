(function () {
  function setButtonLocked(button, label) {
    if (!button) return;
    button.textContent = label;
    button.classList.add('coming-soon');
    button.setAttribute('aria-disabled', 'true');
    button.dataset.locked = 'true';
  }

  function setButtonUnlocked(button, label) {
    if (!button) return;
    button.textContent = label;
    button.classList.remove('coming-soon');
    button.removeAttribute('aria-disabled');
    button.dataset.locked = 'false';
  }

  function applyState(state) {
    const mcqCard = document.getElementById('mcq-module-card');
    const mcqBtn = document.getElementById('mcq-module-btn');
    const avatarCard = document.getElementById('avatar-module-card');
    const avatarBtn = document.getElementById('avatar-module-btn');

    if (!state?.logged_in && !state?.interview_taken && !state?.virtual_round_enabled) {
      setButtonLocked(mcqBtn, 'Login Required');
      setButtonLocked(avatarBtn, 'Login Required');
      mcqCard?.classList.add('disabled');
      avatarCard?.classList.add('disabled');
      return;
    }

    if (state.interview_taken) {
      mcqCard?.classList.add('disabled');
      setButtonLocked(mcqBtn, 'MCQ Completed');
    } else {
      mcqCard?.classList.remove('disabled');
      setButtonUnlocked(mcqBtn, 'MCQ Test');
    }

    if (state.virtual_round_enabled && !state.virtual_taken) {
      avatarCard?.classList.remove('disabled');
      setButtonUnlocked(avatarBtn, 'Start Interview');
    } else if (state.virtual_taken) {
      avatarCard?.classList.add('disabled');
      setButtonLocked(avatarBtn, 'Interview Completed');
    } else {
      avatarCard?.classList.add('disabled');
      setButtonLocked(avatarBtn, 'Locked Until MCQ Pass');
    }
  }

  async function logoutCandidate() {
    localStorage.removeItem('zyra_candidate_state');
    localStorage.removeItem('zyra_avatar_answers');
    try {
      await fetch('/api/logout', { method: 'GET' });
    } catch {}
    window.location.href = 'user_login.html';
  }

  document.addEventListener('DOMContentLoaded', async () => {
    document.querySelectorAll('.module-btn').forEach(button => {
      button.addEventListener('click', event => {
        if (button.dataset.locked === 'true') {
          event.preventDefault();
        }
      });
    });

    document.querySelectorAll('[data-candidate-logout]').forEach(button => {
      button.addEventListener('click', logoutCandidate);
    });

    const cached = JSON.parse(localStorage.getItem('zyra_candidate_state') || '{}');
    applyState(cached);

    try {
      const response = await fetch('/api/session/status');
      const data = await response.json();
      const state = {
        logged_in: data.logged_in,
        role: data.role,
        ...(data.candidate_state || {})
      };
      localStorage.setItem('zyra_candidate_state', JSON.stringify(state));
      applyState(state);
    } catch {
      applyState(cached);
    }
  });
})();
