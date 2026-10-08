/**
 * TeachLive Main Frontend Script
 * "Teach Live. Learn Live."
 * Handles vanilla JavaScript UI interactions, code copy, tooltips, and loading states.
 */

document.addEventListener('DOMContentLoaded', () => {
  // Initialize Bootstrap 5 Tooltips if available
  if (typeof bootstrap !== 'undefined' && bootstrap.Tooltip) {
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map(tooltipTriggerEl => new bootstrap.Tooltip(tooltipTriggerEl));
  }

  // Auto-dismiss alerts after 6 seconds
  const autoAlerts = document.querySelectorAll('.alert-dismissible');
  autoAlerts.forEach(alert => {
    setTimeout(() => {
      if (typeof bootstrap !== 'undefined' && bootstrap.Alert) {
        const bsAlert = bootstrap.Alert.getOrCreateInstance(alert);
        bsAlert.close();
      }
    }, 6000);
  });

  // Copy Classroom Code button functionality
  const copyButtons = document.querySelectorAll('.btn-copy-code');
  copyButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      const code = btn.getAttribute('data-code');
      if (code) {
        navigator.clipboard.writeText(code).then(() => {
          const originalHTML = btn.innerHTML;
          btn.innerHTML = '<i class="bi bi-check2"></i> Copied!';
          btn.classList.add('btn-success');
          btn.classList.remove('btn-outline-secondary', 'btn-secondary');
          setTimeout(() => {
            btn.innerHTML = originalHTML;
            btn.classList.remove('btn-success');
            btn.classList.add('btn-outline-secondary');
          }, 2000);
        }).catch(err => {
          console.error('Failed to copy code: ', err);
        });
      }
    });
  });

  // Universal Password Visibility Toggle
  document.querySelectorAll('.btn-toggle-password').forEach(toggleBtn => {
    toggleBtn.addEventListener('click', () => {
      const targetId = toggleBtn.getAttribute('data-target');
      const input = document.getElementById(targetId) || toggleBtn.previousElementSibling;
      if (input && (input.tagName === 'INPUT')) {
        const isPassword = input.type === 'password';
        input.type = isPassword ? 'text' : 'password';
        const icon = toggleBtn.querySelector('i');
        if (icon) {
          icon.className = isPassword ? 'bi bi-eye-slash' : 'bi bi-eye';
        }
      }
    });
  });

  // Form Submit Loading Feedback
  document.querySelectorAll('form[data-loading="true"]').forEach(form => {
    form.addEventListener('submit', () => {
      const submitBtn = form.querySelector('button[type="submit"]');
      if (submitBtn && !submitBtn.disabled) {
        submitBtn.disabled = true;
        const spinner = document.createElement('span');
        spinner.className = 'spinner-border spinner-border-sm me-2';
        spinner.setAttribute('role', 'status');
        submitBtn.prepend(spinner);
      }
    });
  });

  // Initialize Dark / Light Mode Toggle
  initThemeToggle();
});

/**
 * Dark / Light Theme Toggle with LocalStorage persistence and icon swapping
 */
function initThemeToggle() {
  const toggleBtn = document.getElementById('themeToggleBtn');
  if (!toggleBtn) return;
  const html = document.documentElement;

  function syncThemeUI(theme) {
    const darkIcon = toggleBtn.querySelector('.theme-icon-dark');
    const lightIcon = toggleBtn.querySelector('.theme-icon-light');
    if (theme === 'dark') {
      if (darkIcon) darkIcon.classList.add('d-none');
      if (lightIcon) lightIcon.classList.remove('d-none');
      toggleBtn.setAttribute('title', 'Switch to Light Mode');
      toggleBtn.setAttribute('aria-label', 'Switch to Light Mode');
    } else {
      if (darkIcon) darkIcon.classList.remove('d-none');
      if (lightIcon) lightIcon.classList.add('d-none');
      toggleBtn.setAttribute('title', 'Switch to Dark Mode');
      toggleBtn.setAttribute('aria-label', 'Switch to Dark Mode');
    }
  }

  // Initial sync with active attribute
  const activeTheme = html.getAttribute('data-bs-theme') || 'light';
  syncThemeUI(activeTheme);

  toggleBtn.addEventListener('click', () => {
    const currentTheme = html.getAttribute('data-bs-theme') || 'light';
    const nextTheme = currentTheme === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-bs-theme', nextTheme);
    try {
      localStorage.setItem('crystal-theme', nextTheme);
    } catch (e) {
      console.warn('LocalStorage unavailable for theme storage:', e);
    }
    syncThemeUI(nextTheme);
  });
}

/**
 * Helper to retrieve CSRF token from browser cookies for AJAX/Fetch calls.
 */
function getCookie(name) {
  let cookieValue = null;
  if (document.cookie && document.cookie !== '') {
    const cookies = document.cookie.split(';');
    for (let i = 0; i < cookies.length; i++) {
      const cookie = cookies[i].trim();
      if (cookie.substring(0, name.length + 1) === (name + '=')) {
        cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
        break;
      }
    }
  }
  return cookieValue;
}
