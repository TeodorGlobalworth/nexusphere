// New Project page logic extracted
(function(){
  document.addEventListener('DOMContentLoaded', function() {
    const ctxSel = document.getElementById('context');
    if(!ctxSel) return;
    const nameInput = document.getElementById('name');
    ctxSel.addEventListener('change', function() {
      if (this.value && nameInput && !nameInput.value) {
        nameInput.value = this.value;
        // optional future: toast using i18n-new-project
      }
    });
  });
})();
