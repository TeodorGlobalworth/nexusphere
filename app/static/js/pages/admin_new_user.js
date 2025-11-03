// New User page logic extracted for CSP compliance
(function(){
  // showToast provided globally by ui/toast.js

  document.addEventListener('DOMContentLoaded', function(){
    const I18N = (()=>{ try { return JSON.parse(document.getElementById('i18n-new-user')?.textContent||'{}'); } catch(e){ return {}; } })();

    function checkPasswordStrength(password) {
      const strengthEl = document.getElementById('passwordStrength');
      let strength = 0; const feedback = [];
      if (password.length >= 8) strength++; else feedback.push(I18N.needLen || 'min 8 znaków');
      if (/[a-z]/.test(password)) strength++; else feedback.push(I18N.needLower || 'małe litery');
      if (/[A-Z]/.test(password)) strength++; else feedback.push(I18N.needUpper || 'wielkie litery');
      if (/[0-9]/.test(password)) strength++; else feedback.push(I18N.needDigit || 'cyfry');
      if (/[^A-Za-z0-9]/.test(password)) strength++; else feedback.push(I18N.needSpecial || 'znaki specjalne');
      const levels = [I18N.veryWeak||'Bardzo słabe', I18N.weak||'Słabe', I18N.medium||'Średnie', I18N.good||'Dobre', I18N.veryGood||'Bardzo dobre'];
      const colors = ['red','orange','yellow','light-green','green'];
      const strengthLevel = strength>0? strength-1:0;
      strengthEl.innerHTML = `<div class="progress my-1 progress-5"><div class="determinate ${colors[strengthLevel]}" style="width:${(strength/5)*100}%"></div></div><small class="${colors[strengthLevel]}-text">${levels[strengthLevel]}</small>${feedback.length && strength<5? `<br><small class=\"text-muted\">${(I18N.add||'Dodaj')}: ${feedback.join(', ')}</small>`:''}`;
    }

    function validatePasswords(){
      const passwordInput = document.getElementById('password');
      const confirmInput = document.getElementById('confirm_password');
      const mismatchEl = document.getElementById('passwordMismatch');
      if(passwordInput.value && confirmInput.value && passwordInput.value !== confirmInput.value){
        confirmInput.classList.add('invalid');
        mismatchEl.setAttribute('data-error', I18N.passwordsMismatch || 'Hasła nie są identyczne');
        return false;
      } else {
        confirmInput.classList.remove('invalid');
        return true;
      }
    }

    document.getElementById('password')?.addEventListener('input', function(){ checkPasswordStrength(this.value); validatePasswords(); });
    document.getElementById('confirm_password')?.addEventListener('input', validatePasswords);

    document.getElementById('newUserForm')?.addEventListener('submit', function(e){
      if(!validatePasswords()){
        e.preventDefault();
        showToast(I18N.fixErrors || 'Proszę poprawić błędy w formularzu.', 'danger');
        return false;
      }
    });

    document.getElementById('email')?.addEventListener('blur', function(){
      const email = this.value; const usernameEl = document.getElementById('username');
      if(email && usernameEl && !usernameEl.value){
        usernameEl.value = email.split('@')[0].toLowerCase().replace(/[^a-z0-9_.-]/g,'');
      }
    });
  });
})();
