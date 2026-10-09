(function () {
  'use strict';
  var view = document.getElementById('view'), titleEl = document.getElementById('title'),
      backBtn = document.getElementById('back'), cfg = { allow_input: false },
      timers = [], gen = 0;

  // ---- helpers
  function el(tag, props) {
    var n = document.createElement(tag);
    if (props) Object.keys(props).forEach(function (k) {
      if (k === 'class') n.className = props[k];
      else if (k === 'text') n.textContent = props[k];
      else if (k.slice(0, 2) === 'on') n.addEventListener(k.slice(2), props[k]);
      else n.setAttribute(k, props[k]);
    });
    for (var i = 2; i < arguments.length; i++) {
      var c = arguments[i];
      if (c == null) continue;
      n.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    }
    return n;
  }
  function api(path, opts) {
    opts = opts || {};
    var o = { method: opts.method || 'GET', credentials: 'same-origin', headers: {} };
    if (opts.body !== undefined) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(opts.body); }
    return fetch(path, o).then(function (r) {
      if (r.status === 401) { location.replace('/login'); throw new Error('auth'); }
      return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || r.status); return j; });
    });
  }
  function every(fn, ms) { var g = gen; var t = setInterval(function () { if (g === gen && !document.hidden) fn(); }, ms); timers.push(t); }
  function reset() { timers.forEach(clearInterval); timers = []; gen++; view.textContent = ''; }
  function setTheme(t) {
    document.documentElement.dataset.theme = t;
    try { localStorage.setItem('hw-theme', t); } catch (e) {}
  }
  try { var st = localStorage.getItem('hw-theme'); if (st) document.documentElement.dataset.theme = st; } catch (e) {}
  document.getElementById('theme').addEventListener('click', function () {
    var cur = document.documentElement.dataset.theme ||
      (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    setTheme(cur === 'dark' ? 'light' : 'dark');
  });
  document.getElementById('logout').addEventListener('click', function () {
    api('/api/logout', { method: 'POST', body: {} }).then(function () { location.replace('/login'); }, function () {});
  });
  backBtn.addEventListener('click', function () { location.hash = ''; });

  // ---- ANSI
  var P16 = ['#000', '#c91b00', '#00c200', '#c7c400', '#2225c4', '#c930c7', '#00c5c7', '#c7c7c7',
             '#686868', '#ff6e67', '#5ffa68', '#fffc67', '#6871ff', '#ff77ff', '#60fdff', '#fff'];
  function c256(n) {
    if (n < 16) return P16[n];
    if (n >= 232) { var g = 8 + (n - 232) * 10; return 'rgb(' + g + ',' + g + ',' + g + ')'; }
    n -= 16; var r = Math.floor(n / 36), g2 = Math.floor(n / 6) % 6, b = n % 6, f = function (v) { return v ? 55 + v * 40 : 0; };
    return 'rgb(' + f(r) + ',' + f(g2) + ',' + f(b) + ')';
  }
  function renderAnsi(text, into) {
    var st = {}, frag = document.createDocumentFragment();
    var re = /\x1b\[([0-9;:]*)m|\x1b\[[0-9;?]*[A-Za-ln-zA-Z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Z0-9]/g;
    var last = 0, m;
    function emit(s) {
      if (!s) return;
      s = s.replace(/\r/g, '');
      var fg = st.fg, bg = st.bg;
      if (st.inv) { var t = fg; fg = bg || 'var(--term)'; bg = t || 'var(--termfg)'; }
      if (!fg && !bg && !st.b && !st.d && !st.i && !st.u) { frag.appendChild(document.createTextNode(s)); return; }
      var sp = document.createElement('span');
      if (fg) sp.style.color = fg;
      if (bg) sp.style.backgroundColor = bg;
      if (st.b) sp.style.fontWeight = '700';
      if (st.d) sp.style.opacity = '.65';
      if (st.i) sp.style.fontStyle = 'italic';
      if (st.u) sp.style.textDecoration = 'underline';
      sp.textContent = s; frag.appendChild(sp);
    }
    function sgr(codes) {
      var c = codes.length ? codes.map(function (x) { return parseInt(x || '0', 10); }) : [0];
      for (var i = 0; i < c.length; i++) {
        var v = c[i];
        if (v === 0) st = {};
        else if (v === 1) st.b = 1; else if (v === 2) st.d = 1; else if (v === 3) st.i = 1;
        else if (v === 4) st.u = 1; else if (v === 7) st.inv = 1;
        else if (v === 22) { st.b = 0; st.d = 0; } else if (v === 23) st.i = 0; else if (v === 24) st.u = 0; else if (v === 27) st.inv = 0;
        else if (v >= 30 && v <= 37) st.fg = P16[v - 30];
        else if (v >= 90 && v <= 97) st.fg = P16[v - 90 + 8];
        else if (v >= 40 && v <= 47) st.bg = P16[v - 40];
        else if (v >= 100 && v <= 107) st.bg = P16[v - 100 + 8];
        else if (v === 39) st.fg = undefined; else if (v === 49) st.bg = undefined;
        else if (v === 38 || v === 48) {
          var key = v === 38 ? 'fg' : 'bg';
          if (c[i + 1] === 5) { st[key] = c256(c[i + 2] | 0); i += 2; }
          else if (c[i + 1] === 2) { st[key] = 'rgb(' + (c[i + 2] | 0) + ',' + (c[i + 3] | 0) + ',' + (c[i + 4] | 0) + ')'; i += 4; }
        }
      }
    }
    while ((m = re.exec(text))) {
      emit(text.slice(last, m.index)); last = re.lastIndex;
      if (m[1] !== undefined) sgr(m[1].replace(/:/g, ';').split(';').filter(function (x, i, a) { return a.length > 1 || x !== ''; }));
    }
    emit(text.slice(last));
    into.textContent = ''; into.appendChild(frag);
  }

  // ---- home
  function statusBadge(s) { return el('span', { class: 'badge ' + (s || '') , text: s || 'n/a' }); }
  function miniMap(layout) {
    var a = layout.area || { width: 1, height: 1 }, box = el('div', { class: 'map' });
    (layout.panes || []).forEach(function (p) {
      var r = p.rect, i = el('i', { class: p.focused ? 'f' : '', text: p.pane_id.split(':')[1] });
      i.style.left = (r.x / a.width * 100) + '%'; i.style.top = (r.y / a.height * 100) + '%';
      i.style.width = (r.width / a.width * 100) + '%'; i.style.height = (r.height / a.height * 100) + '%';
      box.appendChild(i);
    });
    return box;
  }
  function home() {
    reset(); titleEl.textContent = 'herdr'; backBtn.hidden = true;
    var box = el('div'); view.appendChild(box);
    var layouts = {};
    function draw(t) {
      box.textContent = '';
      t.workspaces.forEach(function (w) {
        box.appendChild(el('div', { class: 'ws', text: 'Workspace ' + w.number + ' · ' + w.label }));
        w.tabs.forEach(function (tb) {
          box.appendChild(el('div', { class: 'tab' }, 'Tab ' + tb.label + ' · ', statusBadge(tb.agent_status)));
          var first = tb.panes[0];
          if (first && layouts[tb.tab_id]) box.appendChild(miniMap(layouts[tb.tab_id]));
          tb.panes.forEach(function (p) {
            var b = el('button', { class: 'pane', onclick: function () { location.hash = '#/pane/' + p.pane_id; } },
              el('div', { class: 't' }, el('span', { class: 'dot ' + (p.agent_status || '') }),
                el('span', { class: 'txt', text: p.title || p.pane_id }), statusBadge(p.agent_status)),
              el('div', { class: 'm', text: p.pane_id + ' · ' + (p.agent || 'shell') + (p.focused ? ' · focused' : '') + ' · ' + p.cwd }));
            box.appendChild(b);
          });
        });
      });
    }
    function load() {
      api('/api/tree').then(function (t) {
        draw(t);
        t.workspaces.forEach(function (w) { w.tabs.forEach(function (tb) {
          if (!tb.panes.length || layouts[tb.tab_id + '!']) return;
          layouts[tb.tab_id + '!'] = 1;
          api('/api/pane/' + tb.panes[0].pane_id + '/layout').then(function (l) { layouts[tb.tab_id] = l; draw(t); }, function () {});
        }); });
      }, function (e) { box.textContent = ''; box.appendChild(el('p', { class: 'err', text: e.message })); });
    }
    load(); every(load, 3000);
  }

  // ---- pane
  function pane(pid) {
    reset(); backBtn.hidden = false; titleEl.textContent = pid;
    var mode = 'screen', source = 'visible', inputOn = false, info = null;
    var seg = el('div', { class: 'seg' });
    var bScreen = el('button', { text: 'Screen' }), bHist = el('button', { text: 'History' }),
        bFocus = el('button', { text: 'Focus' });
    seg.append(bScreen, bHist, bFocus);
    var body = el('div', { class: 'body' });
    body.style.display = 'flex'; body.style.flexDirection = 'column'; body.style.flex = '1'; body.style.minHeight = '0';
    var inputWrap = el('div', { class: 'inputbar' });
    view.append(seg, body, inputWrap);

    api('/api/pane/' + pid).then(function (p) { info = p; titleEl.textContent = p.title || pid; }, function () {});

    bFocus.addEventListener('click', function () {
      bFocus.disabled = true;
      api('/api/pane/' + pid + '/focus', { method: 'POST', body: {} }).then(function (r) {
        bFocus.textContent = r.focused === pid ? 'Focused ✓' : 'Focus ?';
      }, function (e) { bFocus.textContent = 'Focus ✗'; alert(e.message); })
        .then(function () { bFocus.disabled = false; setTimeout(function () { bFocus.textContent = 'Focus'; }, 2000); });
    });

    // input bar (OFF by default)
    function drawInput() {
      inputWrap.textContent = '';
      var tog = el('button', { class: inputOn ? 'on' : '', text: inputOn ? 'Input: ON' : 'Input: OFF',
        onclick: function () { inputOn = !inputOn; drawInput(); } });
      if (!cfg.allow_input) {
        tog.disabled = true; tog.textContent = 'Input disabled';
        inputWrap.append(tog, el('div', { class: 'warn', text: 'Server started without --allow-input.' }));
        return;
      }
      inputWrap.appendChild(tog);
      if (!inputOn) return;
      var ti = el('input', { type: 'text', placeholder: 'Type to ' + pid, autocomplete: 'off', autocapitalize: 'off', enterkeyhint: 'send' });
      function sendText() {
        var v = ti.value; if (!v) return;
        api('/api/pane/' + pid + '/text', { method: 'POST', body: { text: v, enter: true } })
          .then(function () { ti.value = ''; refresh(); }, function (e) { alert(e.message); });
      }
      ti.addEventListener('keydown', function (e) { if (e.key === 'Enter') sendText(); });
      var keys = el('div', { class: 'keys' });
      [['Enter', 'enter'], ['Esc', 'esc'], ['^C', 'ctrl+c'], ['Tab', 'tab'], ['↑', 'up'], ['↓', 'down'], ['←', 'left'], ['→', 'right']]
        .forEach(function (k) {
          keys.appendChild(el('button', { text: k[0], onclick: function () {
            api('/api/pane/' + pid + '/keys', { method: 'POST', body: { keys: [k[1]] } })
              .then(refresh, function (e) { alert(e.message); });
          } }));
        });
      inputWrap.append(el('div', { class: 'row' }, ti, el('button', { class: 'pri', text: 'Send', onclick: sendText })), keys);
    }
    drawInput();

    // screen view
    var pre = el('pre', { class: 'term' }), etag = '', refresh = function () {};
    function screenView() {
      mode = 'screen'; bScreen.className = 'on'; bHist.className = ''; body.textContent = '';
      var tools = el('div', { class: 'row tools' });
      var bsrc = el('button', { text: source === 'visible' ? 'Viewport' : 'Scrollback', onclick: function () {
        source = source === 'visible' ? 'recent' : 'visible'; bsrc.textContent = source === 'visible' ? 'Viewport' : 'Scrollback'; etag = ''; refresh(); } });
      tools.appendChild(bsrc);
      body.append(tools, pre);
      refresh = function () {
        if (mode !== 'screen') return;
        api('/api/pane/' + pid + '/screen?source=' + source + '&lines=300&etag=' + encodeURIComponent(etag)).then(function (r) {
          if (r.unchanged) return;
          etag = r.etag;
          var atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
          renderAnsi(r.ansi, pre);
          if (atBottom || source === 'recent') pre.scrollTop = pre.scrollHeight;
        }, function (e) { pre.textContent = e.message; });
      };
      etag = ''; refresh();
    }

    // history view
    var histBox = null, histState = null;
    function fmtTs(ts) { try { return new Date(ts).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); } catch (e) { return ''; } }
    function hl(text, q) {
      var f = document.createDocumentFragment();
      if (!q) { f.appendChild(document.createTextNode(text)); return f; }
      var low = text.toLowerCase(), ql = q.toLowerCase(), i = 0, j;
      while ((j = low.indexOf(ql, i)) >= 0) {
        f.appendChild(document.createTextNode(text.slice(i, j)));
        f.appendChild(el('mark', { text: text.slice(j, j + q.length) })); i = j + q.length;
      }
      f.appendChild(document.createTextNode(text.slice(i))); return f;
    }
    function entryEl(e, q) {
      var label = { prompt: 'You', reply: 'Claude', tool_use: 'Tool: ' + (e.tool || ''), tool_result: 'Result', system: 'System' }[e.kind] || e.kind;
      var d = el('div', { class: 'msg ' + e.kind + (e.error ? ' err' : '') },
        el('div', { class: 'h' }, el('span', { text: label + (e.side ? ' (sub-agent)' : '') }), el('span', { text: fmtTs(e.ts) })));
      var b = el('div'); b.appendChild(hl(e.text, q)); d.appendChild(b);
      return d;
    }
    function historyView() {
      mode = 'history'; bHist.className = 'on'; bScreen.className = ''; body.textContent = '';
      var q = '', first = null, last = -1, hasMore = false, loading = false, nonce = 0;
      var search = el('input', { type: 'search', placeholder: 'Search history…', autocomplete: 'off' });
      var box = el('div', { class: 'hist' }), top = el('div', { class: 'more' });
      histBox = box;
      body.append(el('div', { class: 'row tools' }, search), box);
      function fetchPage(url, cb) { var n = nonce; api(url).then(function (r) { if (n === nonce) cb(r); }, function (e) { box.textContent = e.message; }); }
      function loadInitial() {
        nonce++; box.textContent = ''; first = null; last = -1; loading = true;
        fetchPage('/api/pane/' + pid + '/history?limit=40&q=' + encodeURIComponent(q), function (r) {
          loading = false; hasMore = !!r.has_more;
          if (!r.session_id) { box.appendChild(el('div', { class: 'more', text: 'No Claude session linked to this pane.' })); return; }
          if (!r.entries.length) { box.appendChild(el('div', { class: 'more', text: q ? 'No matches.' : 'No messages.' })); return; }
          box.appendChild(top); top.textContent = hasMore ? 'Scroll up for older…' : 'Start of session';
          r.entries.forEach(function (e) { box.appendChild(entryEl(e, q)); });
          first = r.entries[0].i; last = r.entries[r.entries.length - 1].i;
          box.scrollTop = box.scrollHeight;
        });
      }
      function loadOlder() {
        if (loading || !hasMore || first === null) return;
        loading = true;
        fetchPage('/api/pane/' + pid + '/history?limit=40&before=' + first + '&q=' + encodeURIComponent(q), function (r) {
          loading = false; hasMore = !!r.has_more;
          var h = box.scrollHeight, ref = top.nextSibling;
          r.entries.forEach(function (e) { box.insertBefore(entryEl(e, q), ref); });
          if (r.entries.length) first = r.entries[0].i;
          top.textContent = hasMore ? 'Scroll up for older…' : 'Start of session';
          box.scrollTop += box.scrollHeight - h;
        });
      }
      box.addEventListener('scroll', function () { if (box.scrollTop < 60) loadOlder(); });
      var deb;
      search.addEventListener('input', function () { clearTimeout(deb); deb = setTimeout(function () { q = search.value.trim(); loadInitial(); }, 350); });
      refresh = function () {
        if (mode !== 'history' || q || loading || last < 0) return;
        fetchPage('/api/pane/' + pid + '/history?after=' + last, function (r) {
          if (!r.entries.length) return;
          var atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
          r.entries.forEach(function (e) { box.appendChild(entryEl(e, '')); });
          last = r.entries[r.entries.length - 1].i;
          if (atBottom) box.scrollTop = box.scrollHeight;
        });
      };
      loadInitial();
    }
    bScreen.addEventListener('click', screenView);
    bHist.addEventListener('click', historyView);
    screenView();
    var tick = 0;
    every(function () { tick++; if (mode === 'screen' || tick % 3 === 0) refresh(); }, 1500);
  }

  // ---- router
  function route() {
    var m = /^#\/pane\/(w\d+:p\d+)$/.exec(location.hash);
    if (m) pane(m[1]); else home();
  }
  window.addEventListener('hashchange', route);
  api('/api/config').then(function (c) { cfg = c; route(); }, function () {});
})();
