// Dashboard behaviour. Served as a static file (not inline) so the
// Content-Security-Policy can use script-src 'self' with no 'unsafe-inline'.
(function () {
  const input = document.getElementById('label');
  const suffix = document.getElementById('preview-suffix');
  function slug(raw) {
    return raw.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '');
  }
  input.addEventListener('input', function () {
    const s = slug(input.value);
    suffix.textContent = s ? '-' + s : '';
  });

  // Delete confirmation via data-attribute (no template interpolation into JS).
  document.querySelectorAll('button[data-confirm]').forEach(function (btn) {
    btn.addEventListener('click', function (e) {
      if (!confirm(btn.dataset.confirm)) e.preventDefault();
    });
  });

  // Copy-to-clipboard. Uses document.execCommand('copy') for compatibility:
  // navigator.clipboard.writeText() is the modern API but has a Firefox Android
  // quirk where it resolves silently without writing on http://localhost.
  // execCommand works on http+localhost, https, mobile, and desktop alike
  // (officially deprecated but no browser has removed it).
  function flashButton(btn, text, ms) {
    var orig = btn.textContent;
    btn.textContent = text;
    btn.setAttribute('disabled', '');
    setTimeout(function () {
      btn.textContent = orig;
      btn.removeAttribute('disabled');
    }, ms);
  }
  function copyViaExecCommand(text) {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.top = '-9999px';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try {
      ta.setSelectionRange(0, ta.value.length);
    } catch (e) {
      console.warn('setSelectionRange unavailable:', e);
    }
    var ok = false;
    try {
      ok = document.execCommand('copy');
    } catch (e) {
      console.warn('execCommand copy failed:', e);
      ok = false;
    }
    ta.remove();
    return ok;
  }
  document.querySelectorAll('button[data-copy]').forEach(function (btn) {
    btn.addEventListener('click', function (e) {
      e.preventDefault();
      if (copyViaExecCommand(btn.dataset.copy)) {
        flashButton(btn, 'copied', 1500);
      } else {
        flashButton(btn, 'copy failed', 2500);
      }
    });
  });
})();
