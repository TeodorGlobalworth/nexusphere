(() => {
  const parseJsonPayload = (elementId) => {
    try {
      const el = document.getElementById(elementId);
      if (!el) {
        return {};
      }
      const raw = el.textContent || el.innerText || '';
      return raw ? JSON.parse(raw) : {};
    } catch (error) {
      console.warn(`[admin-projects] Failed to parse JSON payload for ${elementId}`, error);
      return {};
    }
  };

  const i18n = parseJsonPayload('i18n-projects');
  const mfaFlag = parseJsonPayload('mfa-flag');

  const t = (key, fallback) => {
    if (Object.prototype.hasOwnProperty.call(i18n, key)) {
      return i18n[key];
    }
    return fallback;
  };

  const ensureOrgReportModal = () => {
    if (document.getElementById('orgReportModal')) {
      return;
    }
    const wrapper = document.createElement('div');
    wrapper.className = 'modal fade';
    wrapper.id = 'orgReportModal';
    wrapper.tabIndex = -1;
    const markup = `
    <div class="modal-dialog modal-sm">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title"><i class="material-icons me-1">date_range</i> ${t('reportRange', 'Zakres raportu')}</h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="${t('close', 'Zamknij')}"></button>
        </div>
        <div class="modal-body">
          <form id="orgReportForm" onsubmit="return false;">
            <div class="mb-2">
              <label class="form-label mb-0">${t('from', 'Od')}</label>
              <input type="date" class="form-control" id="orgReportFrom">
            </div>
            <div class="mb-2">
              <label class="form-label mb-0">${t('to', 'Do')}</label>
              <input type="date" class="form-control" id="orgReportTo">
            </div>
            <div class="mb-2">
              <label class="form-label mb-1">${t('quickRanges', 'Szybkie zakresy')}</label>
              <div class="d-flex flex-wrap gap-1">
                <button type="button" class="btn btn-outline-secondary btn-sm" data-preset="prev-month">${t('prevMonth', 'Poprzedni miesiąc')}</button>
                <button type="button" class="btn btn-outline-secondary btn-sm" data-preset="prev-3-months">${t('prev3Months', 'Poprzednie 3 miesiące')}</button>
              </div>
            </div>
            <div class="form-text">${t('allHint', 'Pozostaw puste aby pobrać wszystkie dane.')}</div>
          </form>
        </div>
        <div class="modal-footer d-flex justify-content-between">
          <button type="button" class="btn btn-outline-secondary btn-sm" id="orgReportClear">${t('clear', 'Wyczyść')}</button>
          <div>
            <button type="button" class="btn btn-secondary btn-sm" data-bs-dismiss="modal">${t('cancel', 'Anuluj')}</button>
            <button type="button" class="btn btn-primary btn-sm" id="orgReportGenerate"><i class="material-icons me-1 fs-6">download</i>${t('generate', 'Generuj')}</button>
          </div>
        </div>
      </div>
    </div>`;

    if (typeof safeReplaceHtml === 'function') {
      safeReplaceHtml(wrapper, markup);
    } else {
      wrapper.innerHTML = markup;
    }

    document.body.appendChild(wrapper);
  };

  const initOrgReportTrigger = () => {
    const trigger = document.getElementById('orgReportTrigger');
    if (!trigger) {
      return;
    }

    trigger.addEventListener('click', () => {
      const baseUrl = trigger.getAttribute('data-base-url');
      if (!baseUrl) {
        showToast(t('noReportUrl', 'Brak adresu raportu'), 'danger');
        return;
      }
      ensureOrgReportModal();
      const modalEl = document.getElementById('orgReportModal');
      modalEl.dataset.baseUrl = baseUrl;

      const fromInput = document.getElementById('orgReportFrom');
      const toInput = document.getElementById('orgReportTo');
      const now = new Date();
      if (fromInput && !fromInput.value) {
        fromInput.value = new Date(now.getFullYear(), now.getMonth(), 1).toISOString().slice(0, 10);
      }
      if (toInput && !toInput.value) {
        toInput.value = now.toISOString().slice(0, 10);
      }

      const instance = new bootstrap.Modal(modalEl);
      instance.show();
    });

    document.addEventListener('click', (event) => {
      const target = event.target;
      if (!target) {
        return;
      }

      if (target.matches('#orgReportModal [data-preset]')) {
        const preset = target.getAttribute('data-preset');
        const fromInput = document.getElementById('orgReportFrom');
        const toInput = document.getElementById('orgReportTo');
        if (!fromInput || !toInput) {
          return;
        }
        const now = new Date();
        let start;
        let end;
        if (preset === 'prev-month') {
          start = new Date(now.getFullYear(), now.getMonth() - 1, 1);
          end = new Date(now.getFullYear(), now.getMonth(), 0);
        } else if (preset === 'prev-3-months') {
          start = new Date(now.getFullYear(), now.getMonth() - 3, 1);
          end = new Date(now.getFullYear(), now.getMonth(), 0);
        }
        if (start && end) {
          fromInput.value = start.toISOString().slice(0, 10);
          toInput.value = end.toISOString().slice(0, 10);
        }
        return;
      }

      if (target.id === 'orgReportClear') {
        const fromInput = document.getElementById('orgReportFrom');
        const toInput = document.getElementById('orgReportTo');
        if (fromInput) {
          fromInput.value = '';
        }
        if (toInput) {
          toInput.value = '';
        }
        return;
      }

      if (target.id === 'orgReportGenerate') {
        const modalEl = document.getElementById('orgReportModal');
        if (!modalEl) {
          return;
        }
        const baseUrl = modalEl.dataset.baseUrl;
        if (!baseUrl) {
          showToast(t('noReportUrl', 'Brak adresu raportu'), 'danger');
          return;
        }
        const fromInput = document.getElementById('orgReportFrom');
        const toInput = document.getElementById('orgReportTo');
        const params = new URLSearchParams();
        const fromValue = fromInput?.value;
        const toValue = toInput?.value;
        if (fromValue) {
          params.append('from', `${fromValue}T00:00:00`);
        }
        if (toValue) {
          params.append('to', `${toValue}T23:59:59`);
        }
        const url = params.toString() ? `${baseUrl}?${params.toString()}` : baseUrl;
        window.open(url, '_blank');
        const modalInstance = bootstrap.Modal.getInstance(modalEl);
        if (modalInstance) {
          modalInstance.hide();
        }
      }
    });
  };

  const initProgressBars = () => {
    document.querySelectorAll('.project-progress').forEach((bar) => {
      const percentRaw = bar.getAttribute('data-percent');
      const percent = Math.max(0, Math.min(100, parseInt(percentRaw || '0', 10)));
      const inner = bar.querySelector('.progress-bar');
      if (inner) {
        inner.style.width = `${percent}%`;
        inner.setAttribute('aria-valuenow', `${percent}`);
      }
    });
  };

  const initProjectFilter = () => {
    const input = document.getElementById('projectFilterInput');
    if (!input) {
      return;
    }
    const cards = Array.from(document.querySelectorAll('.project-card'));
    input.addEventListener('input', () => {
      const query = input.value.trim().toLowerCase();
      cards.forEach((card) => {
        const name = card.dataset.projectName || '';
        card.style.display = !query || name.includes(query) ? '' : 'none';
      });
    });
  };

  const requestMfaCode = () => {
    if (mfaFlag.mfa !== 1) {
      return null;
    }
    const code = prompt(t('mfaPrompt', 'Podaj 6-cyfrowy kod MFA, aby potwierdzić usunięcie projektu:'));
    if (code === null) {
      showToast(t('mfaCancelled', 'Operacja anulowana'), 'info');
      return undefined;
    }
    const trimmed = (code || '').trim();
    if (!/^[0-9]{6}$/.test(trimmed)) {
      showToast(t('mfaInvalid', 'Niepoprawny kod MFA'), 'warning');
      return undefined;
    }
    return trimmed;
  };

  const handleDeleteClick = (button) => {
    const projectId = button.getAttribute('data-project-id');
    if (!projectId) {
      showToast(t('deleteFailed', 'Nie udało się usunąć projektu'), 'danger');
      return;
    }

    if (!confirm(t('confirmDelete', 'Czy na pewno chcesz usunąć ten projekt? Ta operacja jest nieodwracalna.'))) {
      return;
    }

    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content');
    if (!csrfToken) {
      showToast(`${t('error', 'Błąd')}: CSRF token missing`, 'danger');
      return;
    }

    let mfaCode = null;
    if (mfaFlag.mfa === 1) {
      mfaCode = requestMfaCode();
      if (mfaCode === undefined) {
        return;
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

    const originalHtml = button.innerHTML;
    button.disabled = true;
    button.innerHTML = `
      <span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>
      <span>${t('deletingProject', 'Usuwanie projektu...')}</span>
    `;

    fetch(`/admin/projects/${projectId}`, {
      method: 'DELETE',
      headers,
      credentials: 'same-origin'
    })
      .then((resp) => resp.json().catch(() => ({})).then((payload) => ({ resp, payload })))
      .then(({ resp, payload }) => {
        if (resp.ok && payload && payload.status === 'ok') {
          const card = button.closest('.project-card');
          if (card) {
            card.remove();
          }
          showToast(t('projectDeleted', 'Projekt został usunięty'), 'success');
          return;
        }
        if (payload && payload.require_mfa_setup) {
          showToast(payload.error || t('requireMfaFirst', 'Aby usunąć projekt musisz najpierw aktywować MFA na swoim koncie.'), 'warning');
          return;
        }
        if (payload && payload.invalid_mfa) {
          showToast(payload.error || t('mfaInvalid', 'Niepoprawny kod MFA'), 'warning');
          return;
        }
        if (payload && payload.error) {
          showToast(`${t('error', 'Błąd')}: ${payload.error}`, 'danger');
        } else {
          showToast(t('deleteFailed', 'Nie udało się usunąć projektu'), 'danger');
        }
      })
      .catch((error) => {
        console.error('[admin-projects] Project deletion failed', error);
        showToast(t('networkErrorDelete', 'Błąd sieci podczas usuwania projektu'), 'danger');
      })
      .finally(() => {
        button.disabled = false;
        button.innerHTML = originalHtml;
      });
  };

  const initDeleteButtons = () => {
    document.querySelectorAll('[data-action="delete-project"]').forEach((button) => {
      button.addEventListener('click', () => handleDeleteClick(button));
    });
  };

  document.addEventListener('DOMContentLoaded', () => {
    initProgressBars();
    initProjectFilter();
    initOrgReportTrigger();
    initDeleteButtons();
  });
})();
