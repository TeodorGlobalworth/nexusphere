(function(){
  const dataEl = document.getElementById('experimental-data');
  const payload = dataEl ? JSON.parse(dataEl.textContent || '{}') : {};
  const form = document.getElementById('experimental-form');
  const orgSelect = document.getElementById('exp-org');
  const projectSelect = document.getElementById('exp-project');
  const styleSelect = document.getElementById('exp-style');
  const emailInput = document.getElementById('exp-email');
  const resultPanel = document.getElementById('experimental-result');
  const modeLabel = document.getElementById('exp-mode-label');
  const tokensBadge = document.getElementById('exp-tokens-badge');
  const alertBox = document.getElementById('exp-alert');
  const responseWrapper = document.getElementById('exp-response-wrapper');
  const responseDiv = document.getElementById('exp-response');
  const contextTableBody = document.querySelector('#exp-context-table tbody');
  const contextCount = document.getElementById('exp-context-count');
  const multiQueryUsed = document.getElementById('exp-multiquery-used');
  const multiQueryVariants = document.getElementById('exp-multiquery-variants');
  const rerankQueryDisplay = document.getElementById('exp-rerank-query');
  const searchTokens = document.getElementById('exp-search-tokens');
  const rerankUsage = document.getElementById('exp-rerank-usage');
  const embeddingUsage = document.getElementById('exp-embedding-usage');
  const multiQueryModeDisplay = document.getElementById('exp-multiquery-mode-display');
  const multiQueryModelDisplay = document.getElementById('exp-multiquery-model-display');
  const multiQueryAggregateDisplay = document.getElementById('exp-multiquery-aggregate-display');
  const contextLimitDisplay = document.getElementById('exp-context-limit');
  const retrievalTopKDisplay = document.getElementById('exp-retrieval-topk');
  const retrievalThresholdDisplay = document.getElementById('exp-retrieval-threshold-display');
  const prefetchLimitDisplay = document.getElementById('exp-prefetch-limit-display');
  const colbertCandidatesDisplay = document.getElementById('exp-colbert-candidates-display');
  const rrfKDisplay = document.getElementById('exp-rrf-k-display');
  const rrfWeightsDisplay = document.getElementById('exp-rrf-weights-display');
  const rerankProviderDisplay = document.getElementById('exp-rerank-provider-display');
  const rerankModelDisplay = document.getElementById('exp-rerank-model-display');
  const rerankTopKDisplay = document.getElementById('exp-rerank-topk-display');
  const rerankThresholdDisplay = document.getElementById('exp-rerank-threshold-display');
  const responseModelDisplay = document.getElementById('exp-response-model-active');
  const resolvedStyleDisplay = document.getElementById('exp-resolved-style');
  const usagePre = document.getElementById('exp-usage-json');
  const rawPre = document.getElementById('exp-raw-json');
  const clearBtn = document.getElementById('exp-clear');
  const runButtons = Array.from(document.querySelectorAll('#experimental-form button[data-mode]'));
  const csrfMeta = document.querySelector('meta[name="csrf-token"]');
  const csrfToken = csrfMeta ? (csrfMeta.getAttribute('content') || '') : '';

  const responseModelSelect = document.getElementById('exp-response-model-select');
  const responseModelCustom = document.getElementById('exp-response-model-custom');
  const contextLimitInput = document.getElementById('exp-context-limit-input');
  const vectorTopKInput = document.getElementById('exp-vector-topk');
  const retrievalThresholdInput = document.getElementById('exp-retrieval-threshold');
  const prefetchLimitInput = document.getElementById('exp-prefetch-limit');
  const colbertCandidatesInput = document.getElementById('exp-colbert-candidates');
  const rrfKInput = document.getElementById('exp-hybrid-rrf-k');
  const rrfWeightDenseInput = document.getElementById('exp-rrf-weight-dense');
  const rrfWeightLexicalInput = document.getElementById('exp-rrf-weight-lexical');
  const rrfWeightColbertInput = document.getElementById('exp-rrf-weight-colbert');
  const multiQueryModeSelect = document.getElementById('exp-multiquery-mode');
  const multiQueryModelSelect = document.getElementById('exp-multiquery-model-select');
  const multiQueryModelCustom = document.getElementById('exp-multiquery-model-custom');
  const multiQueryVariantsInput = document.getElementById('exp-multiquery-variants');
  const multiQueryAggregateInput = document.getElementById('exp-multiquery-aggregate-limit');
  const includeNeighborChunksCheckbox = document.getElementById('exp-include-neighbor-chunks');
  const rerankProviderSelect = document.getElementById('exp-rerank-provider-select');
  const rerankProviderCustom = document.getElementById('exp-rerank-provider-custom');
  const rerankModelSelect = document.getElementById('exp-rerank-model-select');
  const rerankModelCustom = document.getElementById('exp-rerank-model-custom');
  const rerankTopKInput = document.getElementById('exp-rerank-topk');
  const rerankThresholdInput = document.getElementById('exp-rerank-threshold');
  const vectorLogsWrapper = document.getElementById('exp-vector-logs-wrapper');
  const vectorLogsPre = document.getElementById('exp-vector-logs');
  const vectorLogsPlaceholder = document.getElementById('exp-vector-logs-placeholder');
  const chunkPreviewModalEl = document.getElementById('chunkPreviewModal');
  const chunkPreviewMeta = document.getElementById('chunkPreviewMeta');
  const chunkPreviewContent = document.getElementById('chunkPreviewContent');
  const searchStepsTableBody = document.querySelector('#exp-search-steps-table tbody');
  const searchStepsCount = document.getElementById('exp-search-steps-count');

  let chunkPreviewModalInstance = null;
  let currentContextDocs = [];
  let currentSearchSteps = [];

  const projects = Array.isArray(payload.projects) ? payload.projects : [];
  const activeOrgId = payload.active_org_id != null ? String(payload.active_org_id) : '';

  if(!form || !orgSelect || !projectSelect || !styleSelect || !emailInput || !resultPanel){
    return;
  }

  const projectsByOrg = new Map();
  const projectIndex = new Map();
  projects.forEach((proj) => {
    const orgId = ''; // organization removed
    if(!projectsByOrg.has(orgId)){
      projectsByOrg.set(orgId, []);
    }
    projectsByOrg.get(orgId).push(proj);
    projectIndex.set(String(proj.id), proj);
  });

  function escapeHtml(str){
    return String(str == null ? '' : str).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c] || c));
  }

  function formatScore(value){
    if(value == null || value === ''){ return '–'; }
    const num = Number(value);
    if(Number.isNaN(num)){ return escapeHtml(value); }
    return num.toFixed(4);
  }

  function formatSearchSource(value){
    if(value == null){ return '—'; }
    const normalized = String(value).trim().toLowerCase();
    if(normalized === 'dense'){ return 'Dense'; }
    if(normalized === 'colbert'){ return 'ColBERT'; }
    if(normalized === 'sparse' || normalized === 'lexical'){ return 'Sparse'; }
    if(normalized === 'hybrid'){ return 'Hybrid'; }
    return String(value);
  }

  function sumUsageTotals(breakdown){
    if(!breakdown || typeof breakdown !== 'object'){ return 0; }
    let total = 0;
    Object.values(breakdown).forEach((entry) => {
      if(entry && typeof entry === 'object'){
        const raw = firstDefined(entry.total_tokens, entry.total, 0);
        const val = Number(raw);
        if(!Number.isNaN(val)){ total += val; }
      }
    });
    return total;
  }

  function sumSpecificUsage(breakdown, keys){
    if(!breakdown || typeof breakdown !== 'object' || !Array.isArray(keys)){ return 0; }
    return keys.reduce((acc, key) => {
      const entry = breakdown[key];
      if(entry && typeof entry === 'object'){
        const raw = firstDefined(entry.total_tokens, entry.total, 0);
        const val = Number(raw);
        if(!Number.isNaN(val)){ acc += val; }
      }
      return acc;
    }, 0);
  }

  function describeUsage(entry, includeCompletion = true){
    if(!entry || typeof entry !== 'object'){
      return includeCompletion ? '0 (prompt: 0, completion: 0)' : '0 (prompt: 0)';
    }
    const prompt = Number(firstDefined(entry.prompt_tokens, entry.input_tokens, 0));
    const completion = Number(firstDefined(entry.completion_tokens, entry.output_tokens, 0));
    const total = Number(firstDefined(entry.total_tokens, prompt + completion));
    const parts = [`prompt: ${prompt}`];
    if(includeCompletion || completion > 0){
      parts.push(`completion: ${completion}`);
    }
    return `${total} (${parts.join(', ')})`;
  }

  function buildRerankUsage(usage){
    return describeUsage(usage, true);
  }

  function formatInteger(value){
    if(value === null || value === undefined || value === ''){ return '—'; }
    const num = Number(value);
    if(Number.isNaN(num)){ return String(value); }
    return Math.round(num).toString();
  }

  function formatText(value){
    if(value === null || value === undefined || value === ''){ return '—'; }
    return String(value);
  }

  function formatRrfWeights(weights){
    if(!weights || typeof weights !== 'object'){
      return '—';
    }
    const parts = [];
    const channels = [
      ['dense', 'dense'],
      ['lexical', 'lexical'],
      ['colbert', 'colbert']
    ];
    channels.forEach(([key, label]) => {
      if(weights[key] === undefined || weights[key] === null){
        return;
      }
      const value = Number(weights[key]);
      if(Number.isNaN(value)){
        parts.push(`${label}: ${weights[key]}`);
      } else {
        parts.push(`${label}: ${value.toFixed(2)}`);
      }
    });
    return parts.length ? parts.join(', ') : '—';
  }

  function firstDefined(){
    for(let i = 0; i < arguments.length; i += 1){
      const value = arguments[i];
      if(value !== undefined && value !== null){
        return value;
      }
    }
    return undefined;
  }

  function humanizeMultiQueryMode(mode){
    if(!mode){ return '—'; }
    const lookup = {
      forced_on: 'Wymuszone włączenie',
      forced_off: 'Wymuszone wyłączenie',
      org_on: 'Domyślnie: włączone',
      org_off: 'Domyślnie: wyłączone',
      forced_true: 'Wymuszone włączenie',
      forced_false: 'Wymuszone wyłączenie'
    };
    const normalized = String(mode).toLowerCase();
    return lookup[normalized] || mode;
  }

  function humanizeRerankProvider(provider){
    if(!provider){ return '—'; }
    const normalized = String(provider).toLowerCase();
    const lookup = {
      zeroentropy: 'ZeroEntropy',
      zerorank: 'ZeroEntropy',
      novita: 'NovitaAI',
      novitaai: 'NovitaAI',
      none: 'Wyłączony',
      disabled: 'Wyłączony',
      inherit: 'Domyślne'
    };
    return lookup[normalized] || provider;
  }

  function resetMetadataDisplays(){
    multiQueryModeDisplay.textContent = '—';
    multiQueryModelDisplay.textContent = '—';
    multiQueryAggregateDisplay.textContent = '—';
    if(rerankQueryDisplay){ rerankQueryDisplay.textContent = '—'; }
    contextLimitDisplay.textContent = '—';
    retrievalTopKDisplay.textContent = '—';
    retrievalThresholdDisplay.textContent = '—';
    if(prefetchLimitDisplay){ prefetchLimitDisplay.textContent = '—'; }
    if(colbertCandidatesDisplay){ colbertCandidatesDisplay.textContent = '—'; }
    if(rrfKDisplay){ rrfKDisplay.textContent = '—'; }
    if(rrfWeightsDisplay){ rrfWeightsDisplay.textContent = '—'; }
    rerankProviderDisplay.textContent = '—';
    rerankModelDisplay.textContent = '—';
    rerankTopKDisplay.textContent = '—';
    rerankThresholdDisplay.textContent = '—';
    responseModelDisplay.textContent = '—';
    if(embeddingUsage){ embeddingUsage.textContent = '—'; }
    if(resolvedStyleDisplay){ resolvedStyleDisplay.textContent = '—'; }
  }

  function readInt(input){
    if(!input){ return null; }
    const raw = input.value != null ? input.value.trim() : '';
    if(raw === ''){ return null; }
    const parsed = Number.parseInt(raw, 10);
    return Number.isNaN(parsed) ? null : parsed;
  }

  function readFloat(input){
    if(!input){ return null; }
    const raw = input.value != null ? input.value.trim() : '';
    if(raw === ''){ return null; }
    const parsed = Number.parseFloat(raw);
    return Number.isNaN(parsed) ? null : parsed;
  }

  function readStr(input){
    if(!input){ return null; }
    const raw = input.value != null ? input.value.trim() : '';
    return raw || null;
  }

  function readCombo(selectEl, customInput, skipValues = []){
    const normalizedSkip = Array.isArray(skipValues) ? skipValues.map((v) => String(v)) : [];
    if(!selectEl){
      return readStr(customInput);
    }
    const value = selectEl.value != null ? String(selectEl.value) : '';
    if(value === '__custom__'){
      return readStr(customInput);
    }
    if(value === '' || normalizedSkip.includes(value)){
      return null;
    }
    return value;
  }

  function setupCombo(selectEl, customInput){
    if(!selectEl || !customInput){ return; }
    const updateVisibility = () => {
      const isCustom = selectEl.value === '__custom__';
      customInput.classList.toggle('d-none', !isCustom);
      customInput.toggleAttribute('disabled', !isCustom);
      if(isCustom){
        customInput.focus();
      }
    };
    selectEl.addEventListener('change', updateVisibility);
    updateVisibility();
  }

  function collectOptions(){
    const opts = {};
    const responseModel = readCombo(responseModelSelect, responseModelCustom);
    if(responseModel){ opts.response_model = responseModel; }

    const contextLimit = readInt(contextLimitInput);
    if(contextLimit !== null){ opts.context_limit = contextLimit; }

    const vectorTopK = readInt(vectorTopKInput);
    if(vectorTopK !== null){ opts.vector_top_k = vectorTopK; }

    const retrievalThreshold = readFloat(retrievalThresholdInput);
    if(retrievalThreshold !== null){ opts.retrieval_threshold = retrievalThreshold; }

    const prefetchLimit = readInt(prefetchLimitInput);
    if(prefetchLimit !== null){
      opts.prefetch_limit = prefetchLimit;
      opts.hybrid_per_vector_limit = prefetchLimit;
    }

    const colbertCandidates = readInt(colbertCandidatesInput);
    if(colbertCandidates !== null){ opts.colbert_candidates = colbertCandidates; }

    const rrfK = readInt(rrfKInput);
    if(rrfK !== null){
      opts.rrf_k = rrfK;
      opts.hybrid_rrf_k = rrfK;
    }

    const rrfDense = readFloat(rrfWeightDenseInput);
    const rrfLexical = readFloat(rrfWeightLexicalInput);
    const rrfColbert = readFloat(rrfWeightColbertInput);
    if(rrfDense !== null || rrfLexical !== null || rrfColbert !== null){
      const weights = {};
      if(rrfDense !== null){ weights.dense = rrfDense; }
      if(rrfLexical !== null){ weights.lexical = rrfLexical; }
      if(rrfColbert !== null){ weights.colbert = rrfColbert; }
      opts.rrf_weights = weights;
    }

    const mqMode = multiQueryModeSelect ? multiQueryModeSelect.value : null;
    if(mqMode && mqMode !== 'inherit'){ opts.multiquery_mode = mqMode; }

    const mqModel = readCombo(multiQueryModelSelect, multiQueryModelCustom);
    if(mqModel){ opts.multiquery_model = mqModel; }

    const mqVariants = readInt(multiQueryVariantsInput);
    if(mqVariants !== null){ opts.multiquery_variants = mqVariants; }


    const mqAggregate = readInt(multiQueryAggregateInput);
    if(mqAggregate !== null){ opts.multiquery_aggregate_top_k = mqAggregate; }

    // Add neighbor chunks option
    if (includeNeighborChunksCheckbox && includeNeighborChunksCheckbox.checked) {
      opts.include_neighbor_chunks = true;
    }

    const rerankProvider = readCombo(rerankProviderSelect, rerankProviderCustom, ['inherit']);
    if(rerankProvider){ opts.rerank_provider = rerankProvider; }

    const rerankModel = readCombo(rerankModelSelect, rerankModelCustom);
    if(rerankModel){ opts.rerank_model = rerankModel; }

    const rerankTopK = readInt(rerankTopKInput);
    if(rerankTopK !== null){ opts.rerank_top_k = rerankTopK; }

    const rerankThreshold = readFloat(rerankThresholdInput);
    if(rerankThreshold !== null){ opts.rerank_threshold = rerankThreshold; }

    return opts;
  }

  function resetResult(){
    resultPanel.classList.add('d-none');
    responseWrapper.classList.add('d-none');
    responseDiv.innerHTML = '';
    alertBox.className = '';
    alertBox.textContent = '';
    contextTableBody.innerHTML = '';
    contextCount.textContent = '';
    multiQueryUsed.textContent = '';
    multiQueryVariants.textContent = '';
    searchTokens.textContent = '';
    rerankUsage.textContent = '';
    currentContextDocs = [];
    currentSearchSteps = [];
    if(chunkPreviewMeta){ chunkPreviewMeta.textContent = ''; }
    if(chunkPreviewContent){
      chunkPreviewContent.textContent = '';
      chunkPreviewContent.classList.remove('text-muted');
    }
    if(searchStepsTableBody){ searchStepsTableBody.innerHTML = ''; }
    if(searchStepsCount){ searchStepsCount.textContent = '0'; }
    resetMetadataDisplays();
    renderVectorLogs([]);
    usagePre.textContent = '';
    rawPre.textContent = '';
    tokensBadge.textContent = '';
    renderSearchSteps([]);
  }

  function highlightAlert(message, variant){
    if(resultPanel){
      resultPanel.classList.remove('d-none');
    }
    alertBox.className = `alert alert-${variant}`;
    alertBox.innerHTML = escapeHtml(message);
  }

  function renderContextDocs(docs){
    contextTableBody.innerHTML = '';
    currentContextDocs = Array.isArray(docs) ? docs.slice() : [];
    if(!Array.isArray(docs) || docs.length === 0){
      currentContextDocs = [];
      const row = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = 6;
      cell.className = 'text-muted text-center';
      cell.textContent = 'Brak wyników';
      row.appendChild(cell);
      contextTableBody.appendChild(row);
      return;
    }
    docs.forEach((doc, index) => {
      const metadata = (doc && typeof doc.metadata === 'object') ? doc.metadata : {};
    const title = metadata.title || metadata.name || metadata.filename || metadata.file_name || metadata.fileId || '–';
    const vectorScore = firstDefined(metadata.vector_score, doc.score, metadata.score);
    const rerankScore = firstDefined(metadata.rerank_score, metadata.rerankScore);
  const matchedQueryRaw = metadata.matched_query || metadata.matchedQuery || '';
  const matchedQuery = Array.isArray(matchedQueryRaw) ? matchedQueryRaw.join(', ') : matchedQueryRaw;
  const matchedQueryDisplay = matchedQuery ? escapeHtml(matchedQuery) : '—';
      const row = document.createElement('tr');
      row.innerHTML = `
        <th scope="row">${index + 1}</th>
        <td>${escapeHtml(title)}</td>
        <td>${formatScore(vectorScore)}</td>
        <td>${formatScore(rerankScore)}</td>
        <td>${matchedQueryDisplay}</td>
        <td class="text-center">
          <button type="button" class="btn btn-sm btn-outline-primary context-preview-btn" data-index="${index}" aria-label="Podgląd fragmentu">
            <i class="material-icons align-middle">visibility</i>
          </button>
        </td>
      `;
      contextTableBody.appendChild(row);
    });
  }

  function renderSearchSteps(steps){
    if(searchStepsTableBody){ searchStepsTableBody.innerHTML = ''; }
    const rows = Array.isArray(steps) ? steps : [];
    currentSearchSteps = rows;
    if(searchStepsCount){ searchStepsCount.textContent = rows.length.toString(); }
    if(!searchStepsTableBody){
      return;
    }
    // Update table header to add the new column if not already present
    const table = searchStepsTableBody.closest('table');
    if(table){
      const thead = table.querySelector('thead');
      if(thead){
        const headerRow = thead.querySelector('tr');
        if(headerRow && !headerRow.querySelector('.exp-variant-preview-th')){
          const th = document.createElement('th');
          th.textContent = 'Wariant zapytania';
          th.className = 'exp-variant-preview-th';
          // Insert after 'Kategoria' (4th column, index 3)
          if(headerRow.children.length >= 4){
            headerRow.insertBefore(th, headerRow.children[4]);
          } else {
            headerRow.appendChild(th);
          }
        }
      }
    }
    if(rows.length === 0){
      const row = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = 8;
      cell.className = 'text-muted text-center';
      cell.textContent = 'Brak danych diagnostycznych';
      row.appendChild(cell);
      searchStepsTableBody.appendChild(row);
      return;
    }
    rows.forEach((entry, index) => {
      const metadata = entry && typeof entry.metadata === 'object' ? entry.metadata : {};
      const fileName = entry && entry.file_name ? entry.file_name : (metadata.filename || metadata.file_name || metadata.name || '—');
      const chunkId = entry && entry.chunk_id ? entry.chunk_id : (metadata.chunk_id || metadata.chunk_index || metadata.chunkId || '—');
      const categoryLabel = entry && entry.category ? entry.category : '—';
      const sourceLabel = formatSearchSource(entry && entry.search_source);
      const scoreLabel = formatScore(entry && entry.score);
      const variantPreview = entry && entry.variant_preview ? entry.variant_preview : '';
      const row = document.createElement('tr');
      row.innerHTML = `
        <th scope="row">${index + 1}</th>
        <td>${escapeHtml(fileName || '—')}</td>
        <td>${escapeHtml(String(chunkId || '—'))}</td>
        <td>${escapeHtml(categoryLabel)}</td>
        <td>${escapeHtml(variantPreview)}</td>
        <td>${escapeHtml(sourceLabel)}</td>
        <td>${scoreLabel}</td>
        <td class="text-center">
          <button type="button" class="btn btn-sm btn-outline-primary search-step-preview-btn" data-index="${index}" aria-label="Podgląd fragmentu">
            <i class="material-icons align-middle">visibility</i>
          </button>
        </td>
      `;
      searchStepsTableBody.appendChild(row);
    });
  }

  function ensureChunkPreviewModal(){
    if(!chunkPreviewModalEl){
      return null;
    }
    if(chunkPreviewModalInstance){
      return chunkPreviewModalInstance;
    }
    if(typeof bootstrap === 'undefined' || !bootstrap.Modal){
      console.warn('Bootstrap modal not available for chunk preview.');
      return null;
    }
    chunkPreviewModalInstance = bootstrap.Modal.getOrCreateInstance(chunkPreviewModalEl);
    return chunkPreviewModalInstance;
  }

  function getChunkPreviewText(doc){
    if(!doc || typeof doc !== 'object'){
      return '';
    }
    const metadata = doc.metadata && typeof doc.metadata === 'object' ? doc.metadata : {};
    const raw = firstDefined(
      doc.content,
      doc.text,
      metadata.chunk,
      metadata.chunk_text,
      metadata.content,
      metadata.body,
      metadata.text,
      metadata.snippet
    );
    if(raw === undefined || raw === null){
      return '';
    }
    if(typeof raw === 'string'){
      return raw;
    }
    try{
      return JSON.stringify(raw, null, 2);
    } catch(err){
      return String(raw);
    }
  }

  function openChunkPreview(index){
    if(!Array.isArray(currentContextDocs) || index < 0 || index >= currentContextDocs.length){
      return;
    }
    const doc = currentContextDocs[index];
    const metadata = doc && typeof doc.metadata === 'object' ? doc.metadata : {};
    const title = metadata.title || metadata.name || metadata.filename || metadata.file_name || metadata.fileId || `Fragment ${index + 1}`;
    const vectorScore = formatScore(firstDefined(metadata.vector_score, doc.score, metadata.score));
    const rerankScore = formatScore(firstDefined(metadata.rerank_score, metadata.rerankScore));
  const matchedQueryRaw = metadata.matched_query || metadata.matchedQuery || '';
  const matchedQuery = Array.isArray(matchedQueryRaw) ? matchedQueryRaw.join(', ') : matchedQueryRaw;
    const metaParts = [];
    if(title){ metaParts.push(title); }
    if(vectorScore && vectorScore !== '–'){ metaParts.push(`Vector: ${vectorScore}`); }
    if(rerankScore && rerankScore !== '–'){ metaParts.push(`Rerank: ${rerankScore}`); }
    if(metadata.file_page !== undefined && metadata.file_page !== null){ metaParts.push(`Strona: ${metadata.file_page}`); }
    if(metadata.chunk_index !== undefined && metadata.chunk_index !== null){ metaParts.push(`Fragment #${metadata.chunk_index}`); }
    const sourceLabel = metadata.source || metadata.source_name || metadata.collection || metadata.dataset;
    if(sourceLabel){ metaParts.push(`Źródło: ${sourceLabel}`); }
    if(matchedQuery){ metaParts.push(`Wariant: ${matchedQuery}`); }
    if(chunkPreviewMeta){
      chunkPreviewMeta.textContent = metaParts.length ? metaParts.join(' • ') : 'Brak dodatkowych metadanych.';
    }

    if(chunkPreviewContent){
      const chunkText = getChunkPreviewText(doc);
      if(chunkText){
        chunkPreviewContent.textContent = chunkText;
        chunkPreviewContent.classList.remove('text-muted');
      } else {
        chunkPreviewContent.textContent = 'Brak treści do wyświetlenia.';
        chunkPreviewContent.classList.add('text-muted');
      }
    }

    const modalInstance = ensureChunkPreviewModal();
    if(modalInstance){
      modalInstance.show();
    }
  }

  function openSearchStepPreview(index){
    if(!Array.isArray(currentSearchSteps) || index < 0 || index >= currentSearchSteps.length){
      return;
    }
    const entry = currentSearchSteps[index] || {};
    const metadata = entry && typeof entry.metadata === 'object' ? entry.metadata : {};
    const metaParts = [];
    const fileName = entry.file_name || metadata.filename || metadata.file_name || metadata.name;
    if(fileName){ metaParts.push(fileName); }
    if(entry.category){ metaParts.push(`Kategoria: ${entry.category}`); }
    const sourceLabel = formatSearchSource(entry.search_source);
    if(sourceLabel && sourceLabel !== '—'){ metaParts.push(`Źródło: ${sourceLabel}`); }
    if(entry.score !== undefined && entry.score !== null){ metaParts.push(`Wynik: ${formatScore(entry.score)}`); }
    if(metadata.file_page !== undefined && metadata.file_page !== null){ metaParts.push(`Strona: ${metadata.file_page}`); }
    const chunkId = entry.chunk_id || metadata.chunk_id || metadata.chunk_index || metadata.chunkId;
    if(chunkId !== undefined && chunkId !== null && chunkId !== ''){ metaParts.push(`Fragment #${chunkId}`); }
    const variantLabel = entry.query_variant || metadata.matched_query || metadata.matchedQuery;
    if(variantLabel){ metaParts.push(`Zapytanie: ${variantLabel}`); }
    if(chunkPreviewMeta){
      chunkPreviewMeta.textContent = metaParts.length ? metaParts.join(' • ') : 'Brak dodatkowych metadanych.';
    }

    if(chunkPreviewContent){
      const chunkText = getChunkPreviewText(entry);
      if(chunkText){
        chunkPreviewContent.textContent = chunkText;
        chunkPreviewContent.classList.remove('text-muted');
      } else {
        chunkPreviewContent.textContent = 'Brak treści do wyświetlenia.';
        chunkPreviewContent.classList.add('text-muted');
      }
    }

    const modalInstance = ensureChunkPreviewModal();
    if(modalInstance){
      modalInstance.show();
    }
  }

  function renderVectorLogs(logs){
    if(vectorLogsPre){
      vectorLogsPre.textContent = '';
    }
    if(vectorLogsWrapper){
      vectorLogsWrapper.classList.add('d-none');
    }
    if(vectorLogsPlaceholder){
      vectorLogsPlaceholder.classList.remove('d-none');
    }
    if(!Array.isArray(logs) || logs.length === 0){
      return;
    }
    const blocks = logs.map((entry, index) => {
      if(!entry || typeof entry !== 'object'){
        return '';
      }
      const label = entry.label ? String(entry.label).trim() : '';
      let thresholdText = '';
      if(entry.threshold !== null && entry.threshold !== undefined && entry.threshold !== ''){
        const formatted = formatScore(entry.threshold);
        if(formatted && formatted !== '–'){
          thresholdText = `Próg: ${formatted}`;
        }
      }
      const files = Array.isArray(entry.files) && entry.files.length ? `Pliki: ${entry.files.join(', ')}` : '';
      const headerParts = [`Log #${index + 1}`];
      if(label){ headerParts.push(label); }
      if(thresholdText){ headerParts.push(thresholdText); }
      if(files){ headerParts.push(files); }
      const header = headerParts.join(' | ');
      let tableText = '';
      if(typeof entry.table === 'string' && entry.table.trim()){
        tableText = entry.table;
      } else if(entry.table){
        try{
          tableText = JSON.stringify(entry.table, null, 2);
        } catch(err){
          tableText = String(entry.table);
        }
      }
      const payload = [header, tableText].filter(Boolean).join('\n');
      return payload.trim();
    }).filter(Boolean);
    if(blocks.length && vectorLogsPre && vectorLogsWrapper){
      vectorLogsWrapper.classList.remove('d-none');
      vectorLogsPre.textContent = blocks.join('\n\n');
      if(vectorLogsPlaceholder){
        vectorLogsPlaceholder.classList.add('d-none');
      }
    }
  }

  function setButtonsDisabled(disabled, activeBtn){
    runButtons.forEach((btn) => {
      if(disabled){
        btn.disabled = true;
        if(activeBtn && btn === activeBtn){
          if(!btn.dataset.originalHtml){
            btn.dataset.originalHtml = btn.innerHTML;
            btn.dataset.originalLabel = btn.textContent.trim();
          }
          const label = btn.dataset.loadingText || btn.dataset.originalLabel || btn.textContent.trim();
          btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>' + escapeHtml(label);
        }
      } else {
        if(btn.dataset.originalHtml){
          btn.innerHTML = btn.dataset.originalHtml;
          delete btn.dataset.originalHtml;
          delete btn.dataset.originalLabel;
        }
        btn.disabled = false;
      }
    });
  }

  function renderResult(data, mode){
    const success = Boolean(data && data.success);
    resultPanel.classList.remove('d-none');
    modeLabel.textContent = mode === 'search_only' ? 'Tryb: wyszukiwanie + reranking' : 'Tryb: pełna generacja';
    const vectorLogs = data && Array.isArray(data.vector_debug_logs) ? data.vector_debug_logs : [];
    renderVectorLogs(vectorLogs);
  const breakdown = data && data.token_usage_breakdown ? data.token_usage_breakdown : {};
  const fallbackTokens = sumUsageTotals(breakdown);
  const totalTokens = mode === 'search_only' ? fallbackTokens : Number(firstDefined(data ? data.tokens_used : undefined, fallbackTokens));
    tokensBadge.textContent = `${totalTokens} tokenów`; 
    tokensBadge.className = 'badge bg-secondary';

    if(success){
      highlightAlert('Pipeline zakończony pomyślnie.', 'success');
    } else {
      resetResult();
      resultPanel.classList.remove('d-none');
  highlightAlert((data && data.error) || 'Błąd wykonania pipeline.', 'danger');
  renderVectorLogs(vectorLogs);
  rawPre.textContent = JSON.stringify(data && typeof data === 'object' ? data : {}, null, 2);
      return;
    }

    const responseText = typeof data.response === 'string' ? data.response.trim() : '';
    if(mode === 'full' && responseText){
      responseWrapper.classList.remove('d-none');
      responseDiv.textContent = responseText;
    } else {
      responseWrapper.classList.add('d-none');
      responseDiv.textContent = '';
    }

    const docs = Array.isArray(data.context_docs) ? data.context_docs : [];
    renderContextDocs(docs);
  renderSearchSteps(data && Array.isArray(data.search_steps) ? data.search_steps : []);
    contextCount.textContent = `${docs.length} ${docs.length === 1 ? 'dokument' : 'dokumenty'}`;

    const breakdownClean = breakdown && typeof breakdown === 'object' ? breakdown : {};
    usagePre.textContent = JSON.stringify(breakdownClean, null, 2);

    const mqUsed = Boolean(data.multi_query_used);
    multiQueryUsed.textContent = mqUsed ? 'Tak' : 'Nie';
    const variants = Array.isArray(data.multi_query_variants) ? data.multi_query_variants : [];
    multiQueryVariants.textContent = variants.length ? variants.map((v) => `• ${v}`).join('\n') : '—';
    multiQueryModeDisplay.textContent = humanizeMultiQueryMode(data.multi_query_mode);
    multiQueryModelDisplay.textContent = formatText(data.multi_query_model);
  if(rerankQueryDisplay){ rerankQueryDisplay.textContent = formatText(data.rerank_query); }
    multiQueryAggregateDisplay.textContent = formatInteger(data.multi_query_aggregate_limit);
    const retrievalTokenTotal = sumSpecificUsage(breakdownClean, ['embedding', 'multi_query', 'rerank']);
    searchTokens.textContent = `${retrievalTokenTotal} tokenów`;
    if(embeddingUsage){ embeddingUsage.textContent = describeUsage(breakdownClean.embedding, false); }
    rerankUsage.textContent = buildRerankUsage(data.rerank_usage);
    contextLimitDisplay.textContent = formatInteger(data.context_limit);
    retrievalTopKDisplay.textContent = formatInteger(data.retrieval_top_k);
    retrievalThresholdDisplay.textContent = formatScore(data.retrieval_threshold);
    if(prefetchLimitDisplay){
      const effectivePrefetch = firstDefined(data.prefetch_limit, data.hybrid_per_vector_limit);
      prefetchLimitDisplay.textContent = formatInteger(effectivePrefetch);
    }
    if(colbertCandidatesDisplay){ colbertCandidatesDisplay.textContent = formatInteger(data.colbert_candidates); }
    if(rrfKDisplay){
      const effectiveRrfK = firstDefined(data.rrf_k, data.hybrid_rrf_k);
      rrfKDisplay.textContent = formatInteger(effectiveRrfK);
    }
    if(rrfWeightsDisplay){ rrfWeightsDisplay.textContent = formatRrfWeights(data.rrf_weights); }
    rerankProviderDisplay.textContent = humanizeRerankProvider(data.rerank_provider);
    rerankModelDisplay.textContent = formatText(data.rerank_model);
    rerankTopKDisplay.textContent = formatInteger(data.rerank_top_k);
    rerankThresholdDisplay.textContent = formatScore(data.rerank_threshold);
    responseModelDisplay.textContent = formatText(data.response_model);
    if(resolvedStyleDisplay){ resolvedStyleDisplay.textContent = formatText(data.resolved_style || data.style); }

    const rawPayload = { ...data };
    if(!Array.isArray(rawPayload.multi_query_variants)){
      rawPayload.multi_query_variants = variants;
    }
    if(!rawPayload.primary_query && typeof rawPayload.rerank_query === 'string'){
      rawPayload.primary_query = rawPayload.rerank_query;
    }
    rawPre.textContent = JSON.stringify(rawPayload, null, 2);
  }

  function handleSubmit(mode, trigger){
    const projectId = projectSelect.value;
    const project = projectIndex.get(projectId);
    const text = emailInput.value.trim();

    if(!project){
      highlightAlert('Wybierz projekt zanim uruchomisz pipeline.', 'warning');
      return;
    }
    if(!text){
      highlightAlert('Wklej treść wejściową (email / zapytanie).', 'warning');
      return;
    }

  resetResult();
  setButtonsDisabled(true, trigger);

    const headers = { 'Content-Type': 'application/json', 'Accept': 'application/json' };
    if(csrfToken){
      headers['X-CSRFToken'] = csrfToken;
      headers['X-CSRF-Token'] = csrfToken;
    }

    const optionsPayload = collectOptions();
    const requestBody = {
      email_content: text,
      temperature: styleSelect.value,
      mode
    };
    if(Object.keys(optionsPayload).length > 0){
      requestBody.options = optionsPayload;
    }

    fetch(`/api/projects/${project.public_id}/generate-response`, {
      method: 'POST',
      headers,
      credentials: 'same-origin',
      body: JSON.stringify(requestBody)
    })
      .then(async (resp) => {
        const ct = resp.headers.get('Content-Type') || '';
        if(!ct.includes('application/json')){
          const rawText = await resp.text();
          throw new Error(`Niepoprawny typ odpowiedzi (${resp.status}). Fragment: ${rawText.slice(0, 200)}`);
        }
        return resp.json();
      })
      .then((data) => {
        renderResult(data, mode);
      })
      .catch((err) => {
        highlightAlert(err && err.message ? err.message : 'Nieoczekiwany błąd podczas komunikacji z API.', 'danger');
      })
      .finally(() => {
        setButtonsDisabled(false);
      });
  }

  function populateProjects(){
    const orgId = orgSelect.value || '';
    const options = projectsByOrg.get(orgId) || [];
    projectSelect.innerHTML = '';
    if(options.length === 0){
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = orgId ? 'Brak projektów dla organizacji' : 'Najpierw wybierz organizację';
      projectSelect.appendChild(opt);
      return;
    }
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = 'Wybierz projekt';
    projectSelect.appendChild(placeholder);
    options.forEach((proj) => {
      const opt = document.createElement('option');
      opt.value = String(proj.id);
      opt.textContent = `${proj.name} (${proj.public_id})`;
      projectSelect.appendChild(opt);
    });
  }

  function initialize(){
    if(activeOrgId && !orgSelect.value){
      orgSelect.value = activeOrgId;
    }
    populateProjects();
    if(!projectSelect.value){
      const options = projectSelect.querySelectorAll('option');
      if(options.length > 1){
        projectSelect.selectedIndex = 1;
      }
    }
  }

  if(orgSelect){
    orgSelect.addEventListener('change', () => {
      populateProjects();
      resetResult();
    });
  }

  runButtons.forEach((btn) => {
    btn.addEventListener('click', (event) => {
      event.preventDefault();
      const mode = btn.getAttribute('data-mode') === 'search_only' ? 'search_only' : 'full';
      handleSubmit(mode, btn);
    });
  });

  if(clearBtn){
    clearBtn.addEventListener('click', () => {
      emailInput.value = '';
      resetResult();
    });
  }

  if(contextTableBody){
    contextTableBody.addEventListener('click', (event) => {
      const previewBtn = event.target.closest('.context-preview-btn');
      if(!previewBtn){
        return;
      }
      event.preventDefault();
      const index = Number.parseInt(previewBtn.getAttribute('data-index'), 10);
      if(Number.isNaN(index)){
        return;
      }
      openChunkPreview(index);
    });
  }

  if(searchStepsTableBody){
    searchStepsTableBody.addEventListener('click', (event) => {
      const previewBtn = event.target.closest('.search-step-preview-btn');
      if(!previewBtn){
        return;
      }
      event.preventDefault();
      const index = Number.parseInt(previewBtn.getAttribute('data-index'), 10);
      if(Number.isNaN(index)){
        return;
      }
      openSearchStepPreview(index);
    });
  }

  if(chunkPreviewModalEl){
    chunkPreviewModalEl.addEventListener('hidden.bs.modal', () => {
      if(chunkPreviewMeta){ chunkPreviewMeta.textContent = ''; }
      if(chunkPreviewContent){
        chunkPreviewContent.textContent = '';
        chunkPreviewContent.classList.remove('text-muted');
      }
    });
  }

  setupCombo(responseModelSelect, responseModelCustom);
  setupCombo(rerankProviderSelect, rerankProviderCustom);
  setupCombo(rerankModelSelect, rerankModelCustom);
  setupCombo(multiQueryModelSelect, multiQueryModelCustom);

  initialize();
})();
