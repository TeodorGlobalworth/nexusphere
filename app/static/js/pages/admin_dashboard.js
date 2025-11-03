// Admin Dashboard page script
// Initializes project progress bar widths using data-percent attribute.
(function(){
  document.addEventListener('DOMContentLoaded', function(){
    document.querySelectorAll('.project-progress').forEach(function(bar){
      var pct = parseInt(bar.getAttribute('data-percent')||'0',10);
      pct = isNaN(pct)?0:Math.min(100,Math.max(0,pct));
      var inner = bar.querySelector('.progress-bar');
      if(inner){ inner.style.width = pct + '%'; inner.setAttribute('aria-valuenow', pct); }
    });
  });
})();
