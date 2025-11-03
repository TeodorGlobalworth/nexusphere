// Small initializer for progress bars populated via data-percent attributes
(function(){
  function applyProgressData(){
    const els = document.querySelectorAll('.project-progress[data-percent]');
    els.forEach(function(el){
      const pct = Number(el.getAttribute('data-percent')) || 0;
      const bar = el.querySelector('.progress-bar');
      if(bar){
        bar.style.width = pct + '%';
        bar.setAttribute('aria-valuenow', String(pct));
      }
    });
  }
  if(document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', applyProgressData);
  } else {
    applyProgressData();
  }
})();

// Local timezone date/time formatter
// Usage: add data-local-dt="<ISO-8601 UTC>" to any element. Optional: data-only-date="1" to render only date.
// It will replace the element's textContent with localized string and set a helpful title.
(function(){
  function pad(n){ return String(n).padStart(2,'0'); }
  function toLocalString(d, onlyDate){
    try{
      // Prefer browser locale; fallback to pl-PL for consistent UX
      const locale = navigator.language || 'pl-PL';
      if(onlyDate){
        return d.toLocaleDateString(locale, { year:'numeric', month:'2-digit', day:'2-digit' });
      }
      return d.toLocaleString(locale, {
        year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'
      });
    }catch(e){
      // Fallback manual formatting YYYY-MM-DD HH:mm
      return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    }
  }
  function apply(root){
    const scope = root || document;
    const nodes = scope.querySelectorAll('[data-local-dt]');
    nodes.forEach(function(el){
      const iso = el.getAttribute('data-local-dt');
      if(!iso) return;
      const dt = new Date(iso);
      if(isNaN(dt.getTime())) return;
      const onlyDate = el.hasAttribute('data-only-date');
      const txt = toLocalString(dt, onlyDate);
      el.textContent = txt;
      // Title shows original UTC and local TZ for clarity
      try{
        const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
        el.title = `Czas lokalny (${tz}) dla ${iso}`;
      }catch(e){
        el.title = `Czas lokalny dla ${iso}`;
      }
    });
  }
  // Expose for dynamic content
  window.applyLocalDates = apply;
  if(document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', function(){ apply(); });
  } else {
    apply();
  }
})();
