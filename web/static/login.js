(function () {
  var f = document.getElementById('f'), msg = document.getElementById('msg');
  try { var th = localStorage.getItem('hw-theme'); if (th) document.documentElement.dataset.theme = th; } catch (e) {}
  f.addEventListener('submit', function (ev) {
    ev.preventDefault();
    msg.textContent = '';
    fetch('/api/login', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: document.getElementById('t').value })
    }).then(function (r) {
      if (r.ok) { location.replace('/'); return; }
      if (r.status === 429) msg.textContent = 'Too many attempts. Retry in ' + (r.headers.get('Retry-After') || '?') + 's.';
      else msg.textContent = 'Invalid token.';
    }).catch(function () { msg.textContent = 'Network error.'; });
  });
})();
