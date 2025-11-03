// Project Dashboard page script
// Extracted from inline script in project/dashboard.html for CSP compliance.
(function(){
  const D_I18N = JSON.parse(document.getElementById('i18n-dashboard')?.textContent || '{}');
  function formatTokensK(n){ if(n==null||isNaN(n)) return '0'; return String(Math.round(Number(n)/1000)); }
  function escapeHtml(str){
    return String(str).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }

  document.addEventListener('DOMContentLoaded', function(){
    const form = document.getElementById('responseForm');
    const generateBtn = document.getElementById('generateBtn');
    const searchBtn = document.getElementById('searchFragmentsBtn');
    const responseResult = document.getElementById('responseResult');
    const responseDiv = document.getElementById('generatedResponse');
    const tokensDiv = document.getElementById('tokensUsed');
    const fragmentsSection = document.getElementById('fragmentsResult');
    const fragmentsTableBody = document.querySelector('#fragmentsTable tbody');
    const fragmentsCount = document.getElementById('fragmentsCount');
    const fragmentModalEl = document.getElementById('fragmentPreviewModal');
    const fragmentMeta = document.getElementById('fragmentPreviewMeta');
    const fragmentContent = document.getElementById('fragmentPreviewContent');

    let fragmentModalInstance = null;
    let currentFragments = [];

    // Token usage display removed from UI
    initUsageWebSocket();
    setupProjectReport();

    if(form){
      form.addEventListener('submit', (event) => {
        event.preventDefault();
        handlePipeline('full', generateBtn);
      });
    }

    if(searchBtn){
      searchBtn.addEventListener('click', (event) => {
        event.preventDefault();
        handlePipeline('search_only', searchBtn);
      });
    }

    if(fragmentsTableBody){
      fragmentsTableBody.addEventListener('click', (event) => {
        const trigger = event.target.closest('button[data-fragment-index]');
        if(!trigger){ return; }
        const idx = Number.parseInt(trigger.getAttribute('data-fragment-index'), 10);
        if(Number.isNaN(idx) || idx < 0 || idx >= currentFragments.length){ return; }
        openFragmentPreview(currentFragments[idx]);
      });
    }

    if(fragmentModalEl){
      fragmentModalEl.addEventListener('hidden.bs.modal', () => {
        if(fragmentMeta){ fragmentMeta.textContent = ''; }
        if(fragmentContent){ fragmentContent.textContent = ''; fragmentContent.classList.remove('text-muted'); }
      });
    }

    function handlePipeline(mode, triggerBtn){
      const emailContent = document.getElementById('emailContent')?.value || '';
      const temperature = document.getElementById('temperature')?.value || '';
      const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content');

      if(!emailContent.trim()){
        showToast(D_I18N.enterEmailContent || 'Enter email content', 'danger');
        return;
      }

      const headers = { 'Content-Type': 'application/json', 'Accept': 'application/json' };
      if(csrfToken){ headers['X-CSRFToken'] = csrfToken; headers['X-CSRF-Token'] = csrfToken; }
      const projectPublicId = document.body.getAttribute('data-project-public-id');
      if(!projectPublicId){ return; }

      setButtonsDisabled(true, triggerBtn);

      if(responseResult){ responseResult.classList.remove('d-none'); }
      if(responseDiv){
        responseDiv.innerHTML = '<div class="d-flex align-items-center"><div class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></div>' + escapeHtml(D_I18N.generatingResponse || 'Generating response...') + '</div>';
      }
      if(tokensDiv){ tokensDiv.textContent = ''; }
      if(fragmentsSection){ fragmentsSection.classList.add('d-none'); }
      if(fragmentsTableBody){ fragmentsTableBody.innerHTML = ''; }
      currentFragments = [];

      const payload = { email_content: emailContent, temperature, mode };

      fetch(`/api/projects/${projectPublicId}/generate-response`, {
        method: 'POST',
        headers,
        credentials: 'same-origin',
        body: JSON.stringify(payload),
      })
        .then(async (resp) => {
          const contentType = resp.headers.get('Content-Type') || '';
          if(!contentType.includes('application/json')){
            const raw = await resp.text();
            throw new Error((D_I18N.invalidResponseType || 'Invalid response type (expected JSON).') + ' Status=' + resp.status + '. Body fragment: ' + raw.slice(0, 200));
          }
          return resp.json();
        })
        .then((data) => {
          processPipelineResult(data, mode);
        })
        .catch((error) => {
          if(responseDiv){
            const msg = (D_I18N.connectionError || 'Connection error:') + ' ' + (error && error.message ? error.message : error);
            responseDiv.innerHTML = '<span class="text-danger">' + escapeHtml(msg) + '</span>';
          }
          if(fragmentsSection){ fragmentsSection.classList.add('d-none'); }
        })
        .finally(() => setButtonsDisabled(false));
    }

    function processPipelineResult(data, mode){
      if(!data || !data.success){
        if(responseDiv){
          const message = data && data.error ? data.error : (D_I18N.errorPrefix || 'Błąd:');
          responseDiv.innerHTML = '<span class="text-danger">' + escapeHtml(message) + '</span>';
        }
        if(fragmentsSection){ fragmentsSection.classList.add('d-none'); }
        return;
      }

      const body = resolveResponseBody(data);
      if(responseDiv){
        if(mode === 'full'){
          if(body){
            responseDiv.innerHTML = escapeHtml(body).replace(/\n/g, '<br>');
          } else {
            responseDiv.innerHTML = '<span class="text-warning">' + escapeHtml(D_I18N.noStructuredResponse || 'No structured response (response_json.email.body).') + '</span>';
          }
        } else {
          responseDiv.innerHTML = '';
        }
      }

      const tokensValueRaw = Number(data.tokens_used != null ? data.tokens_used : data.token_usage_breakdown_total);
      const tokensValue = Number.isFinite(tokensValueRaw) ? tokensValueRaw : 0;
      if(tokensDiv){
        tokensDiv.innerHTML = '<i class="material-icons tiny">memory</i> ' + escapeHtml(D_I18N.tokensUsed || 'Used') + ' ' + tokensValue + ' ' + escapeHtml(D_I18N.tokensWord || 'tokens');
      }

      if(Array.isArray(data.context_docs)){
        currentFragments = data.context_docs.slice();
        renderFragments(currentFragments);
      } else {
        currentFragments = [];
        if(fragmentsSection){ fragmentsSection.classList.add('d-none'); }
      }

      refreshProjectUsage();
    }

    function resolveResponseBody(data){
      let structured = data.response_json;
      if(typeof structured === 'string'){
        try { structured = JSON.parse(structured); } catch(_){ structured = null; }
      }
      if((!structured || typeof structured !== 'object') && typeof data.response === 'string' && data.response.trim().startsWith('{')){
        try { structured = JSON.parse(data.response.trim()); } catch(_){ structured = null; }
      }
      const body = structured && structured.email && typeof structured.email.body === 'string' ? structured.email.body : '';
      if(body){ return body; }
      if(typeof data.response === 'string'){ return data.response; }
      return '';
    }

    function renderFragments(docs){
      if(!fragmentsTableBody || !fragmentsSection || !fragmentsCount){ return; }
      if(!Array.isArray(docs) || docs.length === 0){
        fragmentsSection.classList.add('d-none');
        fragmentsTableBody.innerHTML = '';
        fragmentsCount.textContent = '0';
        return;
      }

      fragmentsSection.classList.remove('d-none');
      fragmentsCount.textContent = String(docs.length);
      fragmentsTableBody.innerHTML = '';

      docs.forEach((doc, index) => {
        const metadata = doc && typeof doc.metadata === 'object' ? doc.metadata : {};
        const fileName = metadata.filename || metadata.file_name || metadata.name || metadata.title || metadata.fileId || '—';
        const chunkId = metadata.chunk_id ?? metadata.chunkId ?? metadata.chunk_index ?? metadata.chunkIndex ?? '—';
        const rerankScore = metadata.rerank_score ?? metadata.rerankScore ?? doc.score;
        const row = document.createElement('tr');
        row.innerHTML = '<th scope="row">' + (index + 1) + '</th>' +
          '<td>' + escapeHtml(String(fileName || '—')) + '</td>' +
          '<td class="text-nowrap">' + escapeHtml(String(chunkId || '—')) + '</td>' +
          '<td class="text-nowrap">' + formatScore(rerankScore) + '</td>' +
          '<td class="text-center"><button type="button" class="btn btn-sm btn-outline-primary" data-fragment-index="' + index + '" aria-label="' + escapeHtml(D_I18N.preview || 'Preview') + '"><i class="material-icons align-middle">visibility</i></button></td>';
        fragmentsTableBody.appendChild(row);
      });
    }

    function formatScore(value){
      const numeric = Number(value);
      if(Number.isNaN(numeric)){ return '—'; }
      return numeric.toFixed(4);
    }

    function openFragmentPreview(doc){
      if(!fragmentModalEl){ return; }
      const metadata = doc && typeof doc.metadata === 'object' ? doc.metadata : {};
      const parts = [];
      const fileName = metadata.filename || metadata.file_name || metadata.name || metadata.title;
      if(fileName){ parts.push(String(fileName)); }
      const chunk = metadata.chunk_id ?? metadata.chunkId ?? metadata.chunk_index ?? metadata.chunkIndex;
      if(chunk != null){ parts.push((D_I18N.fragmentId || 'Fragment ID') + ': ' + chunk); }
      const rerankScore = metadata.rerank_score ?? metadata.rerankScore ?? doc.score;
      if(rerankScore != null){ parts.push((D_I18N.rerankScore || 'Rerank score') + ': ' + formatScore(rerankScore)); }
      const source = metadata.source || metadata.source_name || metadata.collection;
      if(source){ parts.push(String(source)); }

      if(fragmentMeta){
        fragmentMeta.textContent = parts.length ? parts.join(' • ') : (D_I18N.noMetadata || 'Brak dodatkowych metadanych.');
      }

      const text = getChunkPreviewText(doc);
      if(fragmentContent){
        if(text){
          fragmentContent.textContent = text;
          fragmentContent.classList.remove('text-muted');
        } else {
          fragmentContent.textContent = D_I18N.modalNoContent || 'Brak treści do wyświetlenia.';
          fragmentContent.classList.add('text-muted');
        }
      }

      if(typeof bootstrap !== 'undefined' && bootstrap.Modal){
        fragmentModalInstance = bootstrap.Modal.getOrCreateInstance(fragmentModalEl);
        fragmentModalInstance.show();
      }
    }

    function getChunkPreviewText(doc){
      if(!doc || typeof doc !== 'object'){ return ''; }
      const metadata = doc.metadata && typeof doc.metadata === 'object' ? doc.metadata : {};
      const raw = doc.content ?? doc.text ?? metadata.chunk ?? metadata.chunk_text ?? metadata.content ?? metadata.body ?? metadata.text ?? metadata.snippet;
      if(raw == null){ return ''; }
      if(typeof raw === 'string'){ return raw; }
      try { return JSON.stringify(raw, null, 2); } catch(_){ return String(raw); }
    }

    function setButtonsDisabled(disabled, activeBtn){
      [generateBtn, searchBtn].forEach((btn) => {
        if(!btn){ return; }
        if(disabled){
          if(btn.dataset.originalHtml === undefined){
            btn.dataset.originalHtml = btn.innerHTML;
          }
          btn.disabled = true;
          if(btn === activeBtn){
            btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>' + escapeHtml(btn.textContent.trim());
          }
        } else {
          btn.disabled = false;
          if(btn.dataset.originalHtml !== undefined){
            btn.innerHTML = btn.dataset.originalHtml;
            delete btn.dataset.originalHtml;
          }
        }
      });
    }

    function refreshProjectUsage(){
      const projectPublicId = document.body.getAttribute('data-project-public-id');
      if(!projectPublicId){ return; }
      fetch(`/api/projects/${projectPublicId}/status`, { headers: { 'Accept': 'application/json' }, credentials: 'same-origin' })
        .then((resp) => (resp.ok ? resp.json() : null))
        .then((data) => {
          if(data && data.project){
            applyMonthlyUsage(data.project.tokens_used, data.project.tokens_limit);
          }
        })
        .catch(() => {});
    }
  });

  // Token usage display removed - logging still happens in backend

  function initUsageWebSocket(){
    try {
      const proto = (location.protocol==='https:') ? 'wss':'ws';
      const projectId = Number(document.body.getAttribute('data-project-id'));
      const projectPublicId = document.body.getAttribute('data-project-public-id');
      const ws = new WebSocket(`${proto}://${location.host}/ws/knowledge/${projectPublicId}`);
      let limitToastShown=false; let hbInt=setInterval(()=>{ if(ws.readyState===WebSocket.OPEN){ try{ ws.send(JSON.stringify({type:'ping'})); }catch(e){} } else { clearInterval(hbInt);} }, 30000);
      ws.onmessage = evt => {
        try { const data = JSON.parse(evt.data); if(data.type==='project_usage' && Number(data.project_id)===projectId){
          // Usage updates removed from UI - backend logging still active
        } } catch(e){ console.warn('WS parse error', e); }
      };
    } catch(e){ console.warn('WS init failed', e); }
  }

  function setupProjectReport(){
    const btn = document.getElementById('projectReportTrigger');
    if(!btn) return;
    btn.addEventListener('click', () => { const base=btn.getAttribute('data-base-url'); if(!base){ showToast(D_I18N.reportMissingUrl||'Report URL missing','danger'); return; } ensureProjectReportModal(); const modalEl=document.getElementById('projectReportModal'); modalEl.dataset.base=base; const fromEl=document.getElementById('projReportFrom'); const toEl=document.getElementById('projReportTo'); if(fromEl&&!fromEl.value){ const now=new Date(); fromEl.value=new Date(now.getFullYear(), now.getMonth(),1).toISOString().slice(0,10);} if(toEl&&!toEl.value){ const now=new Date(); toEl.value=now.toISOString().slice(0,10);} (new bootstrap.Modal(modalEl)).show(); });
    document.addEventListener('click', onProjectReportDelegatedClicks);
  }
  function ensureProjectReportModal(){ if(document.getElementById('projectReportModal')) return; const wrap=document.createElement('div'); wrap.className='modal fade'; wrap.id='projectReportModal'; wrap.tabIndex=-1; wrap.innerHTML = `\n<div class="modal-dialog modal-sm"><div class="modal-content"><div class="modal-header"><h5 class="modal-title"><i class="material-icons me-1">date_range</i> ${(D_I18N.reportDialogTitle||'Project report')}</h5><button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Zamknij"></button></div><div class="modal-body"><form id="projectReportForm" onsubmit="return false;"><div class="mb-2"><label class="form-label mb-0">${D_I18N.dateFrom||'From'}</label><input type="date" class="form-control" id="projReportFrom"></div><div class="mb-2"><label class="form-label mb-0">${D_I18N.dateTo||'To'}</label><input type="date" class="form-control" id="projReportTo"></div><div class="mb-2"><label class="form-label mb-1">${D_I18N.quickRanges||'Quick ranges'}</label><div class="d-flex flex-wrap gap-1"><button type="button" class="btn btn-outline-secondary btn-sm" data-preset="prev-month">${D_I18N.prevMonth||'Previous month'}</button><button type="button" class="btn btn-outline-secondary btn-sm" data-preset="prev-3-months">${D_I18N.prev3Months||'Previous 3 months'}</button></div></div><div class="form-text">${D_I18N.downloadAllInfo||'Leave empty to download all data.'}</div></form></div><div class="modal-footer d-flex justify-content-between"><button type="button" class="btn btn-outline-secondary btn-sm" id="projReportClear">${D_I18N.clear||'Clear'}</button><div><button type="button" class="btn btn-secondary btn-sm" data-bs-dismiss="modal">${D_I18N.cancel||'Cancel'}</button><button type="button" class="btn btn-primary btn-sm" id="projReportGenerate"><i class="material-icons me-1 fs-6">download</i>${D_I18N.generate||'Generate'}</button></div></div></div></div>`; document.body.appendChild(wrap); }
  function onProjectReportDelegatedClicks(ev){
    if(ev.target.matches('#projectReportModal [data-preset]')){ const preset=ev.target.getAttribute('data-preset'); const fromInput=document.getElementById('projReportFrom'); const toInput=document.getElementById('projReportTo'); const now=new Date(); let start,end; if(preset==='prev-month'){ start=new Date(now.getFullYear(), now.getMonth()-1,1); end=new Date(now.getFullYear(), now.getMonth(),0);} else if(preset==='prev-3-months'){ start=new Date(now.getFullYear(), now.getMonth()-3,1); end=new Date(now.getFullYear(), now.getMonth(),0);} if(start&&end){ fromInput.value=start.toISOString().slice(0,10); toInput.value=end.toISOString().slice(0,10);} return; }
    if(ev.target.id==='projReportClear'){ const f=document.getElementById('projReportFrom'); const t=document.getElementById('projReportTo'); if(f) f.value=''; if(t) t.value=''; return; }
    if(ev.target.id==='projReportGenerate'){ const m=document.getElementById('projectReportModal'); const base=m?.dataset.base||''; if(!base) return; const f=document.getElementById('projReportFrom').value; const t=document.getElementById('projReportTo').value; if(f && t && f>t){ showToast(D_I18N.dateRangeInvalid||'From date cannot be later than To date','danger'); return; } const qs=new URLSearchParams(); if(f) qs.append('from', f + 'T00:00:00'); if(t) qs.append('to', t + 'T23:59:59'); const url = base + (qs.toString()?('?'+qs.toString()):''); window.open(url,'_blank'); const inst=bootstrap.Modal.getInstance(m); if(inst) inst.hide(); return; }
  }

  // showToast provided globally by ui/toast.js
})();
