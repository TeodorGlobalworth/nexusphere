// Centralized toast utility. Provides showToast(message, variant?, opts?)
// Variant maps to Bootstrap contextual backgrounds: primary, success, danger, warning, info, secondary, light, dark.
// Options: { delay: ms (default 5000), containerId: customId, dedupeKey: string to suppress duplicates within ttl, dedupeTtl: ms for dedupe window }
(function(){
  const DEFAULT_DELAY = 5000;
  const DEDUPE_MAP = new Map();

  function ensureContainer(id){
    let el = document.getElementById(id);
    if(!el){
      el = document.createElement('div');
      el.id = id;
      el.className = 'toast-container position-fixed bottom-0 end-0 p-3';
      document.body.appendChild(el);
    }
    return el;
  }

  function showToast(message, variant='primary', opts={}){
    try {
      const delay = typeof opts.delay === 'number' ? opts.delay : DEFAULT_DELAY;
      const containerId = opts.containerId || 'globalToastContainer';
      const container = ensureContainer(containerId);
      const dedupeKey = opts.dedupeKey;
      const dedupeTtl = typeof opts.dedupeTtl === 'number' ? opts.dedupeTtl : 4000;
      if(dedupeKey){
        const now = Date.now();
        const prev = DEDUPE_MAP.get(dedupeKey);
        if(prev && (now - prev) < dedupeTtl){
          return; // suppress duplicate toast
        }
        DEDUPE_MAP.set(dedupeKey, now);
      }
      const toastEl = document.createElement('div');
      toastEl.className = `toast text-bg-${variant} border-0`; // rely on bootstrap styles
      toastEl.setAttribute('role','alert');
      toastEl.setAttribute('aria-live','assertive');
      toastEl.setAttribute('aria-atomic','true');
      toastEl.innerHTML = `<div class="d-flex"><div class="toast-body">${escapeHtml(String(message))}</div><button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button></div>`;
      container.appendChild(toastEl);
      const bsToast = bootstrap.Toast.getOrCreateInstance(toastEl, { delay, autohide: true });
      bsToast.show();
      toastEl.addEventListener('hidden.bs.toast', () => { toastEl.remove(); });
    } catch(e){
      try { console.error('Toast error', e); } catch(_) {}
    }
  }

  function escapeHtml(str){
    return str.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;','\'':'&#39;'}[c]));
  }

  // Expose globally
  window.showToast = showToast;
})();
