(() => {
  const parseJsonPayload = (id) => {
    try {
      const el = document.getElementById(id);
      if (!el) {
        return {};
      }
      const txt = el.textContent || el.innerText || '';
      return txt ? JSON.parse(txt) : {};
    } catch (error) {
      console.warn(`[admin-users] Failed to parse payload ${id}`, error);
      return {};
    }
  };

  const i18n = parseJsonPayload('i18n-users');
  const csrfToken = () => document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';

  const escapeHtml = (value) => {
    if (value == null) {
      return '';
    }
    return String(value).replace(/[&<>"]|'/g, (ch) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    })[ch]);
  };

  const state = {
    cache: null,
    loading: null,
  };

  const fetchUsers = async () => {
    if (state.cache) {
      return state.cache;
    }
    if (state.loading) {
      return state.loading;
    }
    state.loading = fetch('/api/admin/users?scope=org', {
      credentials: 'same-origin',
      headers: {
        'Accept': 'application/json',
      },
    })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        return response.json();
      })
      .then((payload) => {
        state.cache = Array.isArray(payload?.users) ? payload.users : [];
        return state.cache;
      })
      .catch((error) => {
        console.error('[admin-users] fetchUsers failed', error);
        return [];
      })
      .finally(() => {
        state.loading = null;
      });
    return state.loading;
  };

  const detachAllHighlights = (root) => {
    if (!root) {
      return;
    }
    root.querySelectorAll('mark').forEach((mark) => {
      const parent = mark.parentNode;
      parent.replaceChild(document.createTextNode(mark.textContent), mark);
      parent.normalize();
    });
  };

  const renderRows = (users, tbody) => {
    if (!tbody) {
      return;
    }

    const rows = users.map((user) => {
      const projects = Array.isArray(user.projects) ? user.projects : [];
      const projectsPreview = projects.slice(0, 3)
        .map((project) => `<span class="badge bg-light text-dark me-1">${escapeHtml(project.name)}</span>`)
        .join('');
      const remainingCount = projects.length > 3 ? `<small class="text-muted">+${projects.length - 3} ${escapeHtml(i18n.more || 'więcej')}</small>` : '';
      const roleBadge = user.is_admin
        ? `<span class="badge bg-danger">${escapeHtml(i18n.administrator || 'Administrator')}</span>`
        : `<span class="badge bg-primary">${escapeHtml(i18n.userRole || 'Użytkownik')}</span>`;
      const lastLogin = user.last_login_iso
        ? `<small data-local-dt="${escapeHtml(user.last_login_iso)}">${escapeHtml(user.last_login_human || '')}</small>`
        : `<small class="text-muted">${escapeHtml(i18n.never || 'Nigdy')}</small>`;
      const onlineBadge = user.is_online
        ? `<span class="badge bg-success">${escapeHtml(i18n.online || 'Online')}</span>`
        : `<span class="badge bg-secondary">${escapeHtml(i18n.offline || 'Offline')}</span>`;
      const deleteBtn = user.is_self
        ? ''
        : `<button class="btn btn-sm btn-action" type="button" data-action="delete" data-user-id="${user.id}" title="${escapeHtml(i18n.delete || 'Usuń')}"><i class="material-icons text-danger">delete</i></button>`;
      return `
        <tr class="user-row" data-user-id="${user.id}">
          <td>
            <div class="d-flex align-items-center">
              <div class="rounded-circle bg-primary text-white d-flex align-items-center justify-content-center me-3 size-40">${escapeHtml(user.username?.substring(0, 1)?.toUpperCase() || '?')}</div>
              <div>
                <h6 class="mb-0">${escapeHtml(user.username)}</h6>
                <small class="text-muted">${escapeHtml(user.email)}</small>
              </div>
            </div>
          </td>
          <td>${roleBadge}</td>
          <td>
            <span class="badge bg-secondary">${projects.length}</span>
            <div class="mt-1">${projectsPreview}${remainingCount}</div>
          </td>
          <td>${lastLogin}</td>
          <td>${onlineBadge}</td>
          <td>
            <button class="btn btn-sm btn-action me-1" type="button" data-action="edit" data-user-id="${user.id}" title="${escapeHtml(i18n.edit || 'Edytuj')}"><i class="material-icons text-info">edit</i></button>
            <button class="btn btn-sm btn-action me-1" type="button" data-action="reset-mfa" data-user-id="${user.id}" title="${escapeHtml(i18n.resetMfa || 'Resetuj MFA')}"><i class="material-icons text-warning">phonelink_lock</i></button>
            <button class="btn btn-sm btn-action me-1" type="button" data-action="projects" data-user-id="${user.id}" title="${escapeHtml(i18n.userProjects || 'Projekty użytkownika')}" data-bs-toggle="modal" data-bs-target="#projectsModal"><i class="material-icons text-success">assessment</i></button>
            ${deleteBtn}
          </td>
        </tr>`;
    }).join('');

    if (typeof safeReplaceHtml === 'function') {
      safeReplaceHtml(tbody, rows);
    } else {
      tbody.innerHTML = rows;
    }

    if (typeof window.applyLocalDates === 'function') {
      window.applyLocalDates();
    }
  };

  const filterUsers = async () => {
    const tbody = document.querySelector('.users-table tbody');
    if (!tbody) {
      return;
    }
    const userQuery = (document.getElementById('userFilterInput')?.value || '').trim().toLowerCase();
    const projectQuery = (document.getElementById('projectFilterInput')?.value || '').trim().toLowerCase();

    const users = await fetchUsers();
    const filtered = users.filter((user) => {
      const username = (user.username || '').toLowerCase();
      const email = (user.email || '').toLowerCase();
      const projects = Array.isArray(user.projects) ? user.projects : [];
      const matchesUser = !userQuery || username.includes(userQuery) || email.includes(userQuery);
      const matchesProjects = !projectQuery || projects.some((project) => (project.name || '').toLowerCase().includes(projectQuery));
      return matchesUser && matchesProjects;
    });

    renderRows(filtered, tbody);
  };

  const openEditModal = async (userId) => {
    try {
      const response = await fetch(`/admin/users/${userId}/edit`, { credentials: 'same-origin' });
      if (!response.ok) {
        throw new Error(i18n.loadEditFormFailed || 'Nie można załadować formularza edycji');
      }
      const html = await response.text();
      const container = document.getElementById('editUserModalContainer');
      if (!container) {
        return;
      }
      if (typeof safeReplaceHtml === 'function') {
        safeReplaceHtml(container, html);
      } else {
        container.innerHTML = html;
      }
      const modalEl = container.querySelector('#editUserModal');
      if (!modalEl) {
        return;
      }
      const modal = new bootstrap.Modal(modalEl);
      const form = modalEl.querySelector('form');
      if (form) {
        form.addEventListener('submit', async (event) => {
          event.preventDefault();
          const formData = new FormData(form);
          const payload = {
            username: formData.get('username'),
            password: formData.get('password'),
            is_admin: !!formData.get('is_admin'),
          };
          const submitButton = form.querySelector('button[type="submit"]');
          if (submitButton) {
            submitButton.disabled = true;
          }
          try {
            const resp = await fetch(`/admin/users/${userId}/edit`, {
              method: 'POST',
              credentials: 'same-origin',
              headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken(),
                'X-CSRF-Token': csrfToken(),
              },
              body: JSON.stringify(payload),
            });
            const data = await resp.json().catch(() => ({}));
            if (resp.ok && data.status === 'ok') {
              showToast(i18n.saved || 'Zapisano', 'success');
              modal.hide();
              state.cache = null; // invalidate cache, refresh filters afterwards
              await filterUsers();
            } else {
              showToast(data.error || i18n.saveError || 'Błąd podczas zapisu', 'danger');
            }
          } catch (error) {
            console.error('[admin-users] edit submit failed', error);
            showToast(i18n.genericError || 'Błąd', 'danger');
          } finally {
            if (submitButton) {
              submitButton.disabled = false;
            }
          }
        }, { once: true });
      }
      modal.show();
    } catch (error) {
      console.error('[admin-users] openEditModal failed', error);
      showToast(error.message || i18n.genericError || 'Błąd', 'danger');
    }
  };

  const openProjectsModal = async (userId) => {
    const modalBody = document.getElementById('projectsModalBody');
    if (!modalBody) {
      return;
    }
    try {
      const response = await fetch(`/admin/users/${userId}/projects`, { credentials: 'same-origin' });
      if (!response.ok) {
        throw new Error(i18n.loadProjectsFailed || 'Nie można załadować projektów');
      }
      const html = await response.text();
      if (typeof safeReplaceHtml === 'function') {
        safeReplaceHtml(modalBody, html);
      } else {
        modalBody.innerHTML = html;
      }
  wireProjectsModal(userId);
    } catch (error) {
      console.error('[admin-users] openProjectsModal failed', error);
      const markup = `<div class="text-danger">${escapeHtml(error.message || i18n.genericError || 'Błąd')}</div>`;
      if (typeof safeReplaceHtml === 'function') {
        safeReplaceHtml(modalBody, markup);
      } else {
        modalBody.innerHTML = markup;
      }
    }
  };

  const wireProjectsModal = (userId) => {
    const modalBody = document.getElementById('projectsModalBody');
    const saveButton = document.getElementById('save-user-projects');
    const form = document.getElementById('user-projects-form');
    if (saveButton && form) {
      saveButton.onclick = async () => {
        const selectedProjects = Array.from(form.querySelectorAll('input[name="project_ids"]:checked')).map((checkbox) => checkbox.value);
        saveButton.disabled = true;
        try {
          const response = await fetch(`/admin/users/${userId}/projects`, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRFToken': csrfToken(),
              'X-CSRF-Token': csrfToken(),
            },
            body: JSON.stringify({ project_ids: selectedProjects }),
          });
          const data = await response.json().catch(() => ({}));
          if (response.ok && data.status === 'ok') {
            showToast(i18n.assignmentsUpdated || 'Przypisania zaktualizowane', 'success');
            state.cache = null;
            await filterUsers();
            const modalEl = document.getElementById('projectsModal');
            bootstrap.Modal.getInstance(modalEl)?.hide();
          } else {
            showToast(data.error || i18n.saveError || 'Błąd podczas zapisu', 'danger');
          }
        } catch (error) {
          console.error('[admin-users] save projects failed', error);
          showToast(i18n.networkError || 'Błąd sieci', 'danger');
        } finally {
          saveButton.disabled = false;
        }
      };
    }

    const orgSelect = document.getElementById('projects-org-select');
    if (orgSelect) {
      orgSelect.onchange = async () => {
        const orgId = orgSelect.value;
        try {
          const response = await fetch(`/admin/users/${userId}/projects${orgId ? `?org_id=${orgId}` : ''}`, { credentials: 'same-origin' });
          if (!response.ok) {
            throw new Error('HTTP ' + response.status);
          }
          const html = await response.text();
          if (typeof safeReplaceHtml === 'function') {
            safeReplaceHtml(modalBody, html);
          } else {
            modalBody.innerHTML = html;
          }
          wireProjectsModal(userId);
        } catch (error) {
          console.error('[admin-users] filter projects failed', error);
        }
      };
    }
  };

  const resetMfa = async (userId) => {
    if (!confirm(i18n.mfaResetConfirm || 'Czy na pewno zresetować MFA temu użytkownikowi?')) {
      return;
    }
    try {
      const response = await fetch(`/admin/users/${userId}/reset-mfa`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'X-CSRFToken': csrfToken(),
          'X-CSRF-Token': csrfToken(),
        },
      });
      const data = await response.json().catch(() => ({}));
      if (response.ok && data.status === 'ok') {
        showToast(i18n.mfaResetOk || 'MFA zresetowane', 'warning');
      } else {
        showToast(data.error || i18n.mfaResetError || 'Błąd podczas resetu MFA', 'danger');
      }
    } catch (error) {
      console.error('[admin-users] resetMfa failed', error);
      showToast(i18n.networkError || 'Błąd sieci', 'danger');
    }
  };

  const deleteUser = async (userId, button) => {
    if (!confirm(i18n.deleteConfirmUser || 'Czy na pewno chcesz usunąć tego użytkownika? Ta operacja jest nieodwracalna.')) {
      return;
    }
    const originalHtml = button.innerHTML;
    button.disabled = true;
    button.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>';
    try {
      const response = await fetch(`/admin/users/${userId}`, {
        method: 'DELETE',
        credentials: 'same-origin',
        headers: {
          'X-CSRFToken': csrfToken(),
          'X-CSRF-Token': csrfToken(),
        },
      });
      const data = await response.json().catch(() => ({}));
      if (response.ok && data.status === 'ok') {
        showToast(i18n.userDeleted || 'Użytkownik usunięty', 'success');
        state.cache = null;
        await filterUsers();
      } else {
        showToast(data.error || i18n.deleteError || 'Błąd podczas usuwania', 'danger');
      }
    } catch (error) {
      console.error('[admin-users] deleteUser failed', error);
      showToast(i18n.networkError || 'Błąd sieci', 'danger');
    } finally {
      button.disabled = false;
      button.innerHTML = originalHtml;
    }
  };

  document.addEventListener('DOMContentLoaded', () => {
    const filterInputs = [
      document.getElementById('userFilterInput'),
      document.getElementById('projectFilterInput'),
    ].filter(Boolean);

    filterInputs.forEach((input) => {
      input.addEventListener('input', () => {
        filterUsers();
      });
      input.addEventListener('focus', () => {
        filterUsers();
      }, { once: true });
    });

    const table = document.querySelector('.users-table tbody');
    if (table) {
      table.addEventListener('click', (event) => {
        const button = event.target.closest('button[data-action]');
        if (!button) {
          return;
        }
        const userId = button.getAttribute('data-user-id');
        const action = button.getAttribute('data-action');
        if (!userId || !action) {
          return;
        }
        if (action === 'edit') {
          openEditModal(userId);
        } else if (action === 'projects') {
          openProjectsModal(userId);
        } else if (action === 'reset-mfa') {
          resetMfa(userId);
        } else if (action === 'delete') {
          deleteUser(userId, button);
        }
      });
    }

    // Ensure initial server-rendered rows respond to actions.
    detachAllHighlights(document.querySelector('.users-table'));
  });
})();
