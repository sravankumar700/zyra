// Zyra - Main JS

// ======== PASSWORD TOGGLE ========
document.querySelectorAll('.toggle-pw').forEach(btn => {
  btn.addEventListener('click', () => {
    const input = btn.closest('.auth-input-wrap').querySelector('input');
    const isPassword = input.type === 'password';
    input.type = isPassword ? 'text' : 'password';
    btn.textContent = isPassword ? '🙈' : '👁';
  });
});

// ======== NAVBAR ACTIVE STATE ========
document.querySelectorAll('.nav-btn').forEach(btn => {
  btn.addEventListener('click', function () {
    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
    this.classList.add('active');
  });
});

// ======== STAGGER ANIMATION ON LOAD ========
document.addEventListener('DOMContentLoaded', () => {
  const items = document.querySelectorAll('.animate-fade-up');
  items.forEach((el, i) => {
    el.style.animationDelay = (i * 0.1) + 's';
    el.style.animationFillMode = 'both';
  });
});

// ======== SMOOTH PAGE LINKS ========
document.querySelectorAll('a[href]').forEach(a => {
  const href = a.getAttribute('href');
  if (href && href.endsWith('.html') && !href.startsWith('http')) {
    a.addEventListener('click', function (e) {
      if (this.dataset.locked === 'true' || this.getAttribute('aria-disabled') === 'true') {
        e.preventDefault();
        return;
      }
      e.preventDefault();
      document.body.style.opacity = '0';
      document.body.style.transition = 'opacity 0.25s ease';
      setTimeout(() => { window.location.href = href; }, 240);
    });
  }
});

// Fade in on load
document.body.style.opacity = '0';
window.addEventListener('load', () => {
  document.body.style.transition = 'opacity 0.4s ease';
  document.body.style.opacity = '1';
});
