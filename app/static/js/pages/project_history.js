(function(){
  const I18N = JSON.parse(document.getElementById('i18n-project-history')?.textContent || '{}');
  const projectPublicId = document.body.getAttribute('data-project-public-id');
  const pageRoot = document.querySelector('.project-history-page');
  if(!projectPublicId || !pageRoot){ return; }

  const state = {
    page: 1,
    perPage: 25,
    totalPages: 1,
    loading: false,
  };
  const entryCache = new Map();

  const tableBody = document.querySelector('#historyTable tbody');
  const emptyState = document.getElementById('historyEmptyState');
  const paginationEl = document.getElementById('historyPagination');
  const filtersForm = document.getElementById('historyFiltersForm');
  const dateFromInput = document.getElementById('historyDateFrom');
  const dateToInput = document.getElementById('historyDateTo');
  const sourceSelect = document.getElementById('historySource');
  const spamSelect = document.getElementById('historySpam');
  const searchInput = document.getElementById('historySearch');
  const refreshBtn = document.getElementById('historyRefreshBtn');
  const resetBtn = document.getElementById('historyResetBtn');

  const defaultFromDate = pageRoot.getAttribute('data-default-from-date') || '';
  const defaultToDate = pageRoot.getAttribute('data-default-to-date') || '';

  document.addEventListener('DOMContentLoaded', () => {
    applyDefaultFilters();
    filtersForm?.addEventListener('submit', onFiltersSubmit);
    refreshBtn?.addEventListener('click', () => reloadCurrentPage(true));
    resetBtn?.addEventListener('click', onResetFilters);
    if(searchInput){
      let debounceTimer = null;
      searchInput.addEventListener('input', () => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => reloadCurrentPage(false), 350);
      });
    }
    document.body.addEventListener('click', onDelegatedClicks);
    loadPage(1);
  });

  function applyDefaultFilters(){
    if(dateFromInput && !dateFromInput.value && defaultFromDate){
      dateFromInput.value = defaultFromDate;
    }
    if(dateToInput && !dateToInput.value && defaultToDate){
      dateToInput.value = defaultToDate;
    }
  }

  function onFiltersSubmit(event){
    event.preventDefault();
    reloadCurrentPage(false);
  }

  function reloadCurrentPage(showToast){
    entryCache.clear();
    loadPage(1, { showToast });
  }

  function onResetFilters(){
    if(searchInput){ searchInput.value = ''; }
    if(sourceSelect){ sourceSelect.value = ''; }
    if(spamSelect){ spamSelect.value = ''; }
    if(dateFromInput){ dateFromInput.value = defaultFromDate || ''; }
    if(dateToInput){ dateToInput.value = defaultToDate || ''; }
    entryCache.clear();
    showToast(I18N.filtersReset || 'Przywrócono domyślne filtry', 'info');
    loadPage(1);
  }

  function gatherFilters(){
    const filters = {};
    const searchTerm = searchInput?.value?.trim();
    if(searchTerm){ filters.search = searchTerm; }
    const sourceValue = sourceSelect?.value;
    if(sourceValue){ filters.source_kind = sourceValue; }
    const spamValue = spamSelect?.value;
    if(spamValue){ filters.spam = spamValue; }
    const fromVal = dateFromInput?.value;
    const toVal = dateToInput?.value;
    if(fromVal && toVal && fromVal > toVal){
      showToast(I18N.dateError || 'Niewłaściwy zakres dat (Od > Do)', 'warning');
      return null;
    }
    if(fromVal){ filters.from = fromVal + 'T00:00:00'; }
    if(toVal){ filters.to = toVal + 'T23:59:59'; }
    return filters;
  }

  async function loadPage(page, { showToast: showToastOnSuccess = false } = {}){
    if(state.loading){ return; }
    const filters = gatherFilters();
    if(filters === null){ return; }
    state.loading = true;
    renderLoading();
    try {
      const params = new URLSearchParams();
      params.set('page', String(page));
      params.set('per_page', String(state.perPage));
      Object.entries(filters).forEach(([key, value]) => {
        if(value !== undefined && value !== null && value !== ''){
          params.append(key, value);
        }
      });
      const response = await fetch(`/project/${projectPublicId}/history/data?${params.toString()}`, {
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        credentials: 'same-origin',
      });
      if(!response.ok){
        throw new Error('HTTP ' + response.status);
      }
      const data = await response.json();
      const items = Array.isArray(data.items) ? data.items : [];
      renderTable(items);
      renderPagination(data.page || 1, data.pages || 1);
      state.page = data.page || 1;
      state.totalPages = data.pages || 1;
      if(showToastOnSuccess){
        showToast(I18N.refreshSuccess || 'Odświeżono historię', 'success');
      }
    } catch(error){
      renderErrorRow(error);
      showToast(I18N.loadFailed || 'Nie udało się pobrać historii.', 'danger');
    } finally {
      state.loading = false;
    }
  }

  function renderLoading(){
    if(tableBody){
      tableBody.innerHTML = `
        <tr>
          <td colspan="7" class="text-center py-4">
            <div class="spinner-border text-primary" role="status"><span class="visually-hidden">${escapeHtml(I18N.loading || 'Wczytywanie...')}</span></div>
            <div class="small text-muted mt-2">${escapeHtml(I18N.loading || 'Wczytywanie...')}</div>
          </td>
        </tr>`;
    }
    hideEmptyState();
    if(paginationEl){ paginationEl.classList.add('d-none'); paginationEl.innerHTML = ''; }
  }

  function renderErrorRow(error){
    if(tableBody){
      tableBody.innerHTML = `<tr><td colspan="7" class="text-center text-danger py-3 small">${escapeHtml((I18N.loadFailed || 'Nie udało się pobrać historii.') + ' ' + (error?.message || ''))}</td></tr>`;
    }
    hideEmptyState();
    if(paginationEl){ paginationEl.classList.add('d-none'); paginationEl.innerHTML = ''; }
  }

  function renderTable(items){
    if(!tableBody){ return; }
    if(!items.length){
      tableBody.innerHTML = '<tr><td colspan="7" class="py-4"></td></tr>';
      showEmptyState();
      if(paginationEl){ paginationEl.classList.add('d-none'); paginationEl.innerHTML=''; }
      return;
    }
    hideEmptyState();
    const rows = items.map(buildRowHtml).join('');
    tableBody.innerHTML = rows;
  }

  function buildRowHtml(item){
    const created = formatDateTime(item.created_at_human || item.created_at);
    const title = item.title ? escapeHtml(item.title) : '&mdash;';
    const snippet = item.snippet ? escapeHtml(item.snippet) : '';
    const sourceLabel = item.source_label ? escapeHtml(item.source_label) : escapeHtml(item.source_kind || '');
    const address = item.source_address ? escapeHtml(item.source_address) : '';
    const userLabel = item.source_user ? escapeHtml(item.source_user) : '';
    const tokens = typeof item.total_tokens === 'number' ? formatNumber(item.total_tokens) : '0';
    const chunkBadge = Number(item.chunk_count) > 0
      ? `<span class="badge bg-info text-dark">${item.chunk_count}</span>`
      : '<span class="text-muted">0</span>';
    const metaLines = [userLabel, address].filter(Boolean).join(' • ');
    const subtitle = snippet ? `<div class="small text-muted line-clamp-2">${snippet}</div>` : '';
    return `
      <tr data-entry-id="${item.id}">
        <td class="text-nowrap"><span>${escapeHtml(created)}</span></td>
        <td>
          <div class="fw-semibold">${title}</div>
          ${subtitle}
        </td>
        <td>
          <div>${sourceLabel}</div>
          ${metaLines ? `<div class="small text-muted">${metaLines}</div>` : ''}
        </td>
        <td class="text-end">
          <span class="badge bg-secondary">${tokens}</span>
        </td>
        <td>
          ${address ? `<div class="small">${escapeHtml(address)}</div>` : ''}
          ${userLabel ? `<div class="small text-muted">${escapeHtml(userLabel)}</div>` : ''}
        </td>
        <td class="text-center">${chunkBadge}</td>
        <td class="text-end">
          <button type="button" class="btn btn-outline-primary btn-sm" data-action="view-entry" data-entry-id="${item.id}">
            <i class="material-icons me-1 fs-6">visibility</i>${escapeHtml(I18N.viewResponse || 'Podgląd')}
          </button>
        </td>
      </tr>`;
  }

  function renderPagination(page, totalPages){
    if(!paginationEl){ return; }
    if(totalPages <= 1){
      paginationEl.classList.add('d-none');
      paginationEl.innerHTML = '';
      return;
    }
    const prevDisabled = page <= 1 ? ' disabled' : '';
    const nextDisabled = page >= totalPages ? ' disabled' : '';
    let html = '<ul class="pagination pagination-sm justify-content-end mb-0">';
    html += `<li class="page-item${prevDisabled}"><a class="page-link" href="#" data-action="paginate" data-page="${page-1}">&laquo;</a></li>`;
    html += `<li class="page-item active"><span class="page-link">${page} / ${totalPages}</span></li>`;
    html += `<li class="page-item${nextDisabled}"><a class="page-link" href="#" data-action="paginate" data-page="${page+1}">&raquo;</a></li>`;
    html += '</ul>';
    paginationEl.innerHTML = html;
    paginationEl.classList.remove('d-none');
  }

  function onDelegatedClicks(event){
    const actionEl = event.target.closest('[data-action]');
    if(!actionEl){ return; }
    const action = actionEl.getAttribute('data-action');
    if(action === 'paginate'){
      event.preventDefault();
      const page = Number(actionEl.getAttribute('data-page'));
      if(page >= 1 && page <= state.totalPages){
        loadPage(page);
      }
      return;
    }
    if(action === 'view-entry'){
      const entryId = Number(actionEl.getAttribute('data-entry-id'));
      if(entryId){ openEntryModal(entryId); }
      return;
    }
    if(action === 'chunk-preview'){
      const entryId = Number(actionEl.getAttribute('data-entry-id'));
      const refIndex = Number(actionEl.getAttribute('data-ref-index'));
      if(entryId >= 0 && refIndex >= 0){
        openChunkModal(entryId, refIndex);
      }
      return;
    }
  }

  async function openEntryModal(entryId){
    try {
      const entry = await getEntry(entryId);
      renderEntryModal(entry);
    } catch(error){
      showToast(I18N.entryLoadFailed || 'Nie udało się pobrać szczegółów.', 'danger');
    }
  }

  async function getEntry(entryId){
    if(entryCache.has(entryId)){
      return entryCache.get(entryId);
    }
    const response = await fetch(`/project/${projectPublicId}/history/${entryId}`, {
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
      credentials: 'same-origin',
    });
    if(!response.ok){
      throw new Error('HTTP ' + response.status);
    }
    const data = await response.json();
    entryCache.set(entryId, data);
    return data;
  }

  function renderEntryModal(entry){
    const modalEl = document.getElementById('historyEntryModal');
    if(!modalEl){ return; }
    modalEl.setAttribute('data-entry-id', String(entry.id));

    const metaEl = document.getElementById('historyEntryMeta');
    if(metaEl){
      metaEl.innerHTML = buildEntryMeta(entry);
    }

    const bodyEl = document.getElementById('historyEntryBody');
    if(bodyEl){
      bodyEl.textContent = entry.response_body || '';
    }

    const chunksEl = document.getElementById('historyEntryChunks');
    if(chunksEl){
      const refs = Array.isArray(entry.chunk_refs) ? entry.chunk_refs : [];
      if(refs.length === 0){
        chunksEl.innerHTML = `<div class="list-group-item text-muted">${escapeHtml(I18N.noChunks || 'Brak powiązanych fragmentów.')}</div>`;
      } else {
        chunksEl.innerHTML = refs.map((ref, idx) => buildChunkListItem(entry.id, ref, idx)).join('');
      }
    }

    bootstrap.Modal.getOrCreateInstance(modalEl).show();
  }

  function buildEntryMeta(entry){
    const rows = [];
  if(entry.created_at_human){ rows.push(metaRow(I18N.labelDate || 'Data', entry.created_at_human)); }
  else if(entry.created_at){ rows.push(metaRow(I18N.labelDate || 'Data', formatDateTime(entry.created_at))); }
  rows.push(metaRow(I18N.labelSource || 'Źródło', entry.source_label || entry.source_kind || ''));
    if(entry.source_address){ rows.push(metaRow(I18N.sourceAddress || 'Adres', entry.source_address)); }
    if(entry.source_user){ rows.push(metaRow(I18N.sourceUser || 'Użytkownik', entry.source_user)); }
  rows.push(metaRow(I18N.labelTokens || 'Tokeny', formatNumber(entry.total_tokens || 0) + ' ' + (I18N.tokens || 'tokenów')));
    if(entry.spam_flag !== null && entry.spam_flag !== undefined){
      rows.push(metaRow(I18N.labelSpam || 'Spam', entry.spam_flag ? (I18N.spamFlagTrue || 'Oznaczone jako spam') : (I18N.spamFlagFalse || 'Wiarygodne')));
    }
    if(entry.importance_level){ rows.push(metaRow(I18N.importance || 'Ważność', entry.importance_level)); }
    const filenames = Array.isArray(entry.filenames) ? entry.filenames.filter(Boolean) : [];
    if(filenames.length){ rows.push(metaRow(I18N.labelFiles || 'Pliki', filenames.join(', '))); }
    return rows.join('');
  }

  function metaRow(label, value){
    if(!value){ return ''; }
    return `<dt class="col-sm-3">${escapeHtml(label)}</dt><dd class="col-sm-9">${escapeHtml(String(value))}</dd>`;
  }

  function buildChunkListItem(entryId, ref, idx){
    const name = ref?.file_name ? String(ref.file_name) : `${I18N.fragment || 'Fragment'} #${idx + 1}`;
    const parts = [];
  if(ref?.chunk_id !== undefined && ref.chunk_id !== null){ parts.push(`${I18N.fragment || 'Fragment'} #${ref.chunk_id}`); }
    return `
      <div class="list-group-item d-flex justify-content-between align-items-center gap-3">
        <div>
          <div class="fw-semibold">${escapeHtml(name)}</div>
          ${parts.length ? `<div class="small text-muted">${escapeHtml(parts.join(' • '))}</div>` : ''}
        </div>
        <button type="button" class="btn btn-outline-primary btn-sm" data-action="chunk-preview" data-entry-id="${entryId}" data-ref-index="${idx}">
          <i class="material-icons me-1 fs-6">visibility</i>${escapeHtml(I18N.preview || 'Podgląd')}
        </button>
      </div>`;
  }

  async function openChunkModal(entryId, refIndex){
    try {
      const entry = await getEntry(entryId);
      const ref = Array.isArray(entry.chunk_refs) ? entry.chunk_refs[refIndex] : null;
      const modalEl = document.getElementById('historyChunkModal');
      if(!modalEl){ return; }
      renderChunkModalPlaceholder(modalEl, ref);
      bootstrap.Modal.getOrCreateInstance(modalEl).show();
      const response = await fetch(`/project/${projectPublicId}/history/${entryId}/chunks/${refIndex}`, {
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        credentials: 'same-origin',
      });
      if(!response.ok){ throw new Error('HTTP ' + response.status); }
      const data = await response.json();
      renderChunkModalContent(modalEl, ref, data.chunk);
    } catch(error){
      showToast(I18N.chunkLoadFailed || 'Nie udało się pobrać fragmentu.', 'danger');
    }
  }

  function renderChunkModalPlaceholder(modalEl, reference){
    const metaEl = document.getElementById('historyChunkMeta');
    if(metaEl){
      metaEl.innerHTML = reference ? buildChunkMeta(reference) : '';
    }
    const bodyEl = document.getElementById('historyChunkBody');
    if(bodyEl){
      bodyEl.textContent = (I18N.loading || 'Wczytywanie...');
    }
  }

  function renderChunkModalContent(modalEl, reference, chunk){
    const metaEl = document.getElementById('historyChunkMeta');
    if(metaEl){
      metaEl.innerHTML = buildChunkMeta(chunk || reference || {});
    }
    const bodyEl = document.getElementById('historyChunkBody');
    if(bodyEl){
      bodyEl.textContent = chunk?.content || '';
    }
  }

  function buildChunkMeta(data){
    const rows = [];
  if(data.file_name){ rows.push(metaRow(I18N.labelFile || 'Plik', data.file_name)); }
  if(data.point_id){ rows.push(metaRow(I18N.pointId || 'Point ID', data.point_id)); }
  if(data.chunk_id !== undefined && data.chunk_id !== null){ rows.push(metaRow(I18N.fragment || 'Fragment', String(data.chunk_id))); }
    if(data.rerank_score !== undefined && data.rerank_score !== null){ rows.push(metaRow(I18N.rerankScore || 'Wynik rerankera', formatRerankScore(data.rerank_score))); }
    return rows.join('');
  }

  function showEmptyState(){ emptyState?.classList.remove('d-none'); }
  function hideEmptyState(){ emptyState?.classList.add('d-none'); }

  function escapeHtml(value){
    return String(value ?? '').replace(/[&<>"']/g, ch => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    }[ch] || ch));
  }

  function formatRerankScore(value){
    const numeric = Number(value);
    if(Number.isFinite(numeric)){
      const percent = numeric * 100;
      return `${percent.toFixed(2)}%`;
    }
    return String(value);
  }

  function formatNumber(num){
    try {
      return Number(num || 0).toLocaleString();
    } catch(_){
      return String(num || 0);
    }
  }

  function formatDateTime(value){
    if(!value){ return ''; }
    try {
      const dt = value.includes('T') ? new Date(value) : value;
      if(dt instanceof Date && !isNaN(dt)){ return dt.toLocaleString(); }
      return String(value);
    } catch(_){
      return String(value);
    }
  }
})();
