(function(root){
  "use strict";

  function sanitizeHtmlFragment(html){
    if(!html){
      return "";
    }
    let safe = String(html);
  // Remove script and style blocks entirely
    safe = safe.replace(/<script[^>]*>[\s\S]*?<\/script>/gi, '');
    safe = safe.replace(/<style[^>]*>[\s\S]*?<\/style>/gi, '');
  safe = safe.replace(/<iframe[^>]*>[\s\S]*?<\/iframe>/gi, '');
    // Neutralize event handler attributes (onclick, onerror, etc.)
    safe = safe.replace(/\son[a-z]+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi, '');
    // Neutralize javascript: or data:text/html URIs
  safe = safe.replace(/\s(href|src)\s*=\s*(['"])\s*(?:javascript:|data:text\/html)[^'"]*\2/gi, ' $1="#"');
  safe = safe.replace(/\s(href|src)\s*=\s*(?:javascript:|data:text\/html)[^\s>]+/gi, ' $1="#"');
    // Remove meta refresh directives
    safe = safe.replace(/<meta[^>]+http-equiv\s*=\s*("|')(?:refresh|Refresh)("|')[^>]*>/gi, '');
    return safe;
  }

  function safeReplaceHtml(target, html){
    if(!target){
      return;
    }
    target.innerHTML = sanitizeHtmlFragment(html);
  }

  const exportRoot = root || (typeof window !== 'undefined' ? window : globalThis);
  exportRoot.sanitizeHtmlFragment = sanitizeHtmlFragment;
  exportRoot.safeReplaceHtml = safeReplaceHtml;
})(typeof window !== 'undefined' ? window : this);
