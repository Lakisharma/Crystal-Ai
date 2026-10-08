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
        spinner.setAttribute('aria-hidden', 'true');
        submitBtn.prepend(spinner);
      }
    });
  });
});

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
