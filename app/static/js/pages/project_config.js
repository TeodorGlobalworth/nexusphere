(() => {
  const parseJsonPayload = (id) => {
    try {
      const el = document.getElementById(id);
      if (!el) {
        return {};
      }
      const text = el.textContent || el.innerText || '';
      return text ? JSON.parse(text) : {};
    } catch (error) {
      console.warn(`[project-config] Failed to parse JSON payload for ${id}`, error);
      return {};
    }
  };

  const i18n = parseJsonPayload('i18n-project-config');
  const mfaFlag = parseJsonPayload('project-mfa-flag');

  const getI18n = (key, fallback) => {
    if (Object.prototype.hasOwnProperty.call(i18n, key)) {
      return i18n[key];
    }
    return fallback;
  };

  const withButtonState = async (button, pendingHtml, task) => {
    if (!button) {
      return;
    }
    const originalHtml = button.innerHTML;
    button.disabled = true;
    if (pendingHtml) {
      button.innerHTML = pendingHtml;
    }
    try {
      await task();
    } finally {
      button.disabled = false;
      button.innerHTML = originalHtml;
    }
  };

  const promptForMfa = () => {
    if (mfaFlag.mfa !== 1) {
      return null;
    }
    const input = prompt(getI18n('mfaPrompt', 'Enter the 6-digit MFA code to confirm project deletion:'));
    if (input === null) {
      showToast(getI18n('mfaCancelled', 'Operation cancelled.'), 'info');
      return undefined;
    }
    const trimmed = (input || '').trim();
    if (!/^[0-9]{6}$/.test(trimmed)) {
      showToast(getI18n('mfaInvalid', 'Invalid MFA code.'), 'warning');
      return undefined;
    }
    return trimmed;
  };

  const deleteProject = async (button) => {
    const projectPublicId = button.getAttribute('data-project-public-id');
    if (!projectPublicId) {
      showToast('Missing project identifier.', 'danger');
      return;
    }

    if (!confirm(getI18n('confirmDelete', 'Are you sure you want to permanently delete this project? This action cannot be undone.'))) {
      return;
    }

    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
    if (!csrfToken) {
      showToast(getI18n('errorPrefix', 'Error:') + ' CSRF token missing.', 'danger');
      return;
    }

    let mfaCode = null;
    if (mfaFlag.mfa === 1) {
      mfaCode = promptForMfa();
      if (mfaCode === undefined) {
        return; // User cancelled or provided invalid input (toast already shown)
      }
    }

    const headers = {
      'X-CSRFToken': csrfToken,
      'X-CSRF-Token': csrfToken,
      'Accept': 'application/json'
    };
    if (mfaCode) {
      headers['X-MFA-Code'] = mfaCode;
    }

    const pendingHtml = `
      <span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>
      <span>${getI18n('deletingProject', 'Deleting project...')}</span>
    `;

    await withButtonState(button, pendingHtml, async () => {
      try {
        const response = await fetch(`/admin/projects/${projectPublicId}`, {
          method: 'DELETE',
          headers,
          credentials: 'same-origin'
        });
        const payload = await response.json().catch(() => ({}));

        if (response.ok && payload && payload.status === 'ok') {
          showToast(getI18n('projectDeleted', 'Project deleted.'), 'success');
          const redirectUrl = button.getAttribute('data-redirect-url') || '/admin/projects';
          setTimeout(() => {
            window.location.href = redirectUrl;
          }, 800);
          return;
        }

        if (payload && payload.require_mfa_setup) {
          showToast(payload.error || getI18n('requireMfaFirst', 'Enable MFA before deleting the project.'), 'warning');
          return;
        }
        if (payload && payload.invalid_mfa) {
          showToast(payload.error || getI18n('mfaInvalid', 'Invalid MFA code.'), 'warning');
          return;
        }

        const baseError = getI18n('deleteFailed', 'Failed to delete the project.');
        if (payload && payload.error) {
          showToast(`${getI18n('errorPrefix', 'Error:')} ${payload.error}`, 'danger');
        } else {
          showToast(baseError, 'danger');
        }
      } catch (error) {
        console.error('[project-config] deleteProject failed', error);
        showToast(getI18n('networkError', 'Network error while deleting the project.'), 'danger');
      }
    });
  };

  document.addEventListener('DOMContentLoaded', () => {
    const deleteButton = document.querySelector('[data-project-delete]');
    if (!deleteButton) {
      return;
    }
    deleteButton.addEventListener('click', () => deleteProject(deleteButton));
  });
})();
