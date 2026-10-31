/* TV Installer — 前端逻辑（无框架，手机/电脑同一套） */
const $ = (id) => document.getElementById(id);
const S = {
  serial: '', device: null, settings: {}, pin: '', lanUrl: '',
  queue: [], apps: [], path: '/sdcard', installedAt: 0, jobId: null,
};

/* ---------------------------------------------------------------- 基础 */
async function api(path, opt) {
  try {
    const r = await fetch(path, opt);
    let j;
    try { j = await r.json(); } catch (e) { j = { ok: false, msg: 'HTTP ' + r.status }; }
    if (r.status === 401) { location.href = '/'; }
    return j;
  } catch (e) { return { ok: false, msg: '网络错误：' + e.message }; }
}
const post = (p, b) => api(p, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b || {}) });

let toastTimer;
function toast(msg) {
  const t = $('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 2200);
}
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function fmtSize(n) {
  if (!n && n !== 0) return '';
  if (n < 1024) return n + ' B';
  const u = ['KB', 'MB', 'GB', 'TB']; let i = -1, v = n;
  do { v /= 1024; i++; } while (v >= 1024 && i < u.length - 1);
  return v.toFixed(v < 10 ? 1 : 0) + ' ' + u[i];
}

/* ---------------------------------------------------------------- 弹层 */
function sheet(title, options) {
  const wrap = document.createElement('div');
  wrap.style.cssText = 'position:fixed;inset:0;background:rgba(16,24,40,.35);z-index:70;display:flex;align-items:flex-end;justify-content:center';
  wrap.innerHTML = `<div style="background:#fff;width:100%;max-width:520px;border-radius:16px 16px 0 0;padding:8px 0 12px;box-shadow:0 -8px 30px rgba(0,0,0,.15)">
    <div style="padding:10px 16px 6px;font-weight:600;font-size:13px;color:#6b7280">${esc(title)}</div>
    ${options.map((o, i) => `<button data-i="${i}" style="display:block;width:100%;text-align:left;border:0;border-radius:0;padding:13px 18px;font-size:14.5px;${o.danger ? 'color:#b91c1c' : ''}">${esc(o.label)}</button>`).join('')}
    <button data-i="-1" style="display:block;width:calc(100% - 28px);margin:8px 14px 0;padding:12px;border:0;border-radius:10px;background:#f3f4f6">取消</button>
  </div>`;
  wrap.addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) { if (e.target === wrap) wrap.remove(); return; }
    const i = Number(b.dataset.i);
    wrap.remove();
    if (i >= 0 && options[i]) options[i].fn();
  });
  document.body.appendChild(wrap);
}

function askText(title, value) {
  return new Promise((resolve) => {
    const wrap = document.createElement('div');
    wrap.style.cssText = 'position:fixed;inset:0;background:rgba(16,24,40,.35);z-index:70;display:flex;align-items:center;justify-content:center;padding:20px';
    wrap.innerHTML = `<div style="background:#fff;border-radius:14px;padding:16px;width:100%;max-width:380px">
      <div style="font-weight:600;margin-bottom:10px">${esc(title)}</div>
      <input id="_askv" style="width:100%;padding:10px 12px;border:1px solid #e6e8ec;border-radius:10px;font-size:14px" value="${esc(value || '')}">
      <div style="display:flex;gap:8px;margin-top:14px">
        <button id="_askc" style="flex:1">取消</button>
        <button id="_askok" style="flex:1;background:#2563eb;border-color:#2563eb;color:#fff">确定</button>
      </div></div>`;
    document.body.appendChild(wrap);
    const input = wrap.querySelector('#_askv');
    input.focus(); input.select();
    const done = (v) => { wrap.remove(); resolve(v); };
    wrap.querySelector('#_askc').onclick = () => done(null);
    wrap.querySelector('#_askok').onclick = () => done(input.value.trim() || null);
    input.onkeydown = (e) => { if (e.key === 'Enter') done(input.value.trim() || null); };
  });
}

function confirmBox(text) {
  return new Promise((resolve) => {
    const wrap = document.createElement('div');
    wrap.style.cssText = 'position:fixed;inset:0;background:rgba(16,24,40,.35);z-index:70;display:flex;align-items:center;justify-content:center;padding:20px';
    wrap.innerHTML = `<div style="background:#fff;border-radius:14px;padding:16px;width:100%;max-width:360px">
      <div style="font-size:14.5px;line-height:1.6;word-break:break-all">${esc(text)}</div>
      <div style="display:flex;gap:8px;margin-top:16px">
        <button id="_cfc" style="flex:1">取消</button>
        <button id="_cfok" style="flex:1;background:#dc2626;border-color:#dc2626;color:#fff">确定</button>
      </div></div>`;
    document.body.appendChild(wrap);
    const done = (v) => { wrap.remove(); resolve(v); };
    wrap.querySelector('#_cfc').onclick = () => done(false);
    wrap.querySelector('#_cfok').onclick = () => done(true);
    wrap.onclick = (e) => { if (e.target === wrap) done(false); };
  });
}

/* ---------------------------------------------------------------- tab */
function setTab(name) {
  document.querySelectorAll('.page').forEach((p) => p.classList.toggle('active', p.id === 'page-' + name));
  document.querySelectorAll('nav.tabs button, nav.bottom button')
    .forEach((b) => b.classList.toggle('active', b.dataset.tab === name));
  if (name === 'apps' && !S.apps.length) loadApps();
  if (name === 'files' && !$('files').dataset.loaded) loadFiles(S.path);
  window.scrollTo({ top: 0 });
}
document.querySelectorAll('nav.tabs button, nav.bottom button')
  .forEach((b) => b.onclick = () => setTab(b.dataset.tab));

/* ---------------------------------------------------------------- 设备 */
async function loadState() {
  const st = await api('/api/state');
  if (st.error) toast(st.error);
  S.settings = st.settings || {};
  S.pin = st.pin || '';
  S.lanUrl = st.lan_url || '';

  $('lanUrl').textContent = S.lanUrl || '（已关闭局域网访问）';
  $('pinCode').textContent = S.pin || '—';
  $('setHint').textContent = 'adb：' + (st.adb || '未找到') + '　·　aapt：' + (st.aapt || '未找到');

  const dev = st.device;
  S.device = dev;
  S.serial = dev ? dev.serial : '';
  $('devDot').className = 'dot' + (dev ? ' on' : '');
  $('devName').textContent = dev ? '已连接' : '连接';
  $('devLine').textContent = dev
    ? (dev.model + ' · Android ' + dev.android + (dev.size ? ' · ' + dev.size : ''))
    : '未连接电视';
  $('devMeta').textContent = dev
    ? `${dev.serial} · ${dev.device} · Android ${dev.android} (SDK ${dev.sdk}) · ${dev.abi}`
    : '电视需开启 ADB 调试（网络调试），并和电脑在同一 Wi-Fi';

  if (!$('ip').value) $('ip').value = S.settings.last_ip || '';
  $('port').value = S.settings.last_port || 5555;

  // 设置页回填
  $('setIp').value = S.settings.last_ip || '';
  $('setPort').value = S.settings.last_port || 5555;
  $('setChannel').value = S.settings.channel || 'auto';
  $('setLan').checked = !!S.settings.lan;
  $('setAuto').checked = !!S.settings.auto_accept;
  $('setHome').checked = !!S.settings.allow_home;
  $('setGrant').checked = !!S.settings.grant;
  $('setAdb').value = S.settings.adb || '';

  // 安装页选项跟随默认设置
  $('channel').value = S.settings.channel || 'auto';
  $('optAuto').checked = !!S.settings.auto_accept;
  $('optHome').checked = !!S.settings.allow_home;
  $('optGrant').checked = !!S.settings.grant;
  updateInstallBtn();
}

async function connect() {
  const ip = $('ip').value.trim();
  const port = $('port').value.trim() || '5555';
  if (!ip) { toast('先填电视 IP'); return; }
  $('btnConnect').disabled = true;
  $('btnConnect').textContent = '连接中';
  const r = await post('/api/connect', { ip, port: Number(port) });
  $('btnConnect').disabled = false;
  $('btnConnect').textContent = '连接';
  toast(r.ok ? '已连接' : ('连接失败：' + (r.msg || '')));
  await loadState();
}

/* ---------------------------------------------------------------- 队列 */
function updateInstallBtn() {
  $('btnInstall').disabled = !S.serial || !S.queue.some((q) => q.status !== 'done' && q.status !== 'failed');
  $('queueCount').textContent = S.queue.length ? S.queue.length + ' 个' : '';
}

function actionChip(meta) {
  if (!meta || !meta.action) return '';
  const map = { update: ['更新', 'brand'], downgrade: ['降级', 'warn'], reinstall: ['重装', ''], install: ['新装', 'ok'] };
  const [label, cls] = map[meta.action] || ['', ''];
  return label ? `<span class="chip ${cls}">${label}</span>` : '';
}

function statusChip(it) {
  const map = {
    uploading: ['上传中', 'run'], pending: ['待安装', ''], waiting: ['排队中', 'run'], running: ['安装中', 'run'],
    done: ['已安装', 'ok'], failed: ['失败', 'err'], canceled: ['已取消', ''],
  };
  const [label, cls] = map[it.status] || ['', ''];
  return `<span class="chip ${cls}">${label}</span>`;
}

function renderQueue() {
  const box = $('queue');
  box.innerHTML = '';
  S.queue.forEach((it, i) => {
    const m = it.meta || {};
    const el = document.createElement('div');
    el.className = 'item';
    el.innerHTML = `<div class="body">
        <div class="n">${esc(m.label || it.name)}</div>
        <div class="m mono">${esc(m.package || '—')}${m.versionName ? ' · v' + esc(m.versionName) + '(' + esc(m.versionCode || '?') + ')' : ''}${m.size ? ' · ' + fmtSize(m.size) : ''}</div>
        <div style="margin-top:4px">${statusChip(it)}${actionChip(m)}${m.home ? '<span class="chip warn">声明 HOME</span>' : ''}
          ${it.msg ? '<span class="small muted"> ' + esc(it.msg) + '</span>' : ''}</div>
      </div>
      <div class="acts"><button class="ghost sm" data-x="${i}">移除</button></div>`;
    el.querySelector('[data-x]').onclick = () => { S.queue.splice(i, 1); renderQueue(); updateInstallBtn(); };
    box.appendChild(el);
  });
  updateInstallBtn();
}

async function uploadFiles(files) {
  const apks = [...files].filter((f) => /\.apk$/i.test(f.name));
  if (!apks.length) { toast('没有 .apk 文件'); return; }
  for (const f of apks) {
    const item = { name: f.name, status: 'uploading', meta: { size: f.size }, path: '' };
    S.queue.push(item);
    renderQueue();
    const q = `name=${encodeURIComponent(f.name)}${S.serial ? '&serial=' + encodeURIComponent(S.serial) : ''}`;
    const r = await api('/api/upload?' + q, {
      method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: f,
    });
    if (r.ok) { item.path = r.path; item.meta = r.meta || {}; item.status = 'pending'; }
    else { item.status = 'failed'; item.msg = r.msg || '上传失败'; }
    renderQueue();
  }
  updateInstallBtn();
}

/* ---------------------------------------------------------------- 安装 */
async function startInstall() {
  const apks = S.queue.filter((q) => q.status === 'pending' && q.path).map((q) => q.path);
  if (!S.serial) { toast('先连接电视'); return; }
  if (!apks.length) { toast('没有待安装的 APK'); return; }
  $('btnInstall').disabled = true;
  const r = await post('/api/install', {
    serial: S.serial, apks,
    channel: $('channel').value,
    allow_home: $('optHome').checked,
    downgrade: $('optDowngrade').checked,
    grant: $('optGrant').checked,
    auto_accept: $('optAuto').checked,
  });
  if (!r.ok) { toast(r.msg || '启动失败'); updateInstallBtn(); return; }
  S.jobId = r.job.id;
  S.queue.forEach((q) => { if (q.status === 'pending') { q.status = 'waiting'; } });
  openDrawer(true);
}

/* ---------------------------------------------------------------- 应用 */
async function loadApps() {
  if (!S.serial) { toast('先连接电视'); return; }
  $('apps').innerHTML = '<div class="empty">读取中…</div>';
  const r = await api('/api/apps?serial=' + encodeURIComponent(S.serial));
  S.apps = r.apps || [];
  $('appsCount').textContent = S.apps.length ? '共 ' + S.apps.length + ' 个' : '';
  renderApps();
}

function renderApps() {
  const kw = ($('appSearch').value || '').trim().toLowerCase();
  const list = S.apps.filter((a) => !kw || a.package.toLowerCase().includes(kw));
  const box = $('apps');
  if (!list.length) { box.innerHTML = '<div class="empty">没有匹配的应用</div>'; return; }
  box.innerHTML = '';
  list.forEach((a) => {
    const el = document.createElement('div');
    el.className = 'item';
    el.innerHTML = `<div class="body">
        <div class="n mono">${esc(a.package)}</div>
        <div class="m">${a.versionName ? 'v' + esc(a.versionName) : ''}${a.versionCode ? ' (' + esc(a.versionCode) + ')' : ''}${a.lastUpdate ? ' · ' + esc(a.lastUpdate.split('.')[0]) : ''}${a.protected ? ' · 受保护' : ''}</div>
      </div>
      <div class="acts"><button class="sm" data-act="more">操作</button></div>`;
    el.querySelector('[data-act]').onclick = () => {
      sheet(a.package, [
        { label: '启动', fn: () => appAction('launch', a.package) },
        { label: '强制停止', fn: () => appAction('stop', a.package) },
        { label: '导出 APK 到电脑', fn: () => { location.href = `/api/app/export?serial=${encodeURIComponent(S.serial)}&package=${encodeURIComponent(a.package)}`; } },
        { label: '清除数据', danger: true, fn: () => appAction('clear', a.package) },
        { label: '卸载', danger: true, fn: async () => { if (await confirmBox('卸载 ' + a.package + '？')) appAction('uninstall', a.package); } },
      ]);
    };
    box.appendChild(el);
  });
}

async function appAction(act, pkg) {
  const r = await post('/api/app', { serial: S.serial, package: pkg, action: act });
  toast(r.ok ? '已执行：' + act : ('失败：' + (r.msg || '')));
  if (act === 'uninstall' && r.ok) setTimeout(loadApps, 600);
}

/* ---------------------------------------------------------------- 文件 */
async function loadFiles(path) {
  if (!S.serial) { toast('先连接电视'); return; }
  const box = $('files');
  box.innerHTML = '<div class="empty">读取中…</div>';
  const r = await api(`/api/files?path=${encodeURIComponent(path)}&serial=${encodeURIComponent(S.serial)}`);
  if (!r.items) {
    box.innerHTML = `<div class="empty">${esc(r.msg || '读取失败')}</div>`;
    return;
  }
  S.path = r.path;
  box.dataset.loaded = '1';
  renderCrumbs(S.path);
  box.innerHTML = '';
  if (!r.items.length) box.innerHTML = '<div class="empty">空目录</div>';
  r.items.forEach((f) => {
    const el = document.createElement('div');
    el.className = 'frow';
    el.innerHTML = `<div class="fico ${f.isDir ? 'dir' : ''}">${f.isDir ? '▸' : '·'}</div>
      <div class="fname">${esc(f.name)}</div>
      <div class="fsize">${f.isDir ? '' : fmtSize(f.size)}</div>`;
    el.onclick = () => {
      if (f.isDir) { loadFiles(f.path); return; }
      const opts = [
        { label: '下载到电脑', fn: () => { location.href = `/api/files/download?path=${encodeURIComponent(f.path)}&serial=${encodeURIComponent(S.serial)}`; } },
        { label: '重命名', fn: async () => {
          const nn = await askText('重命名', f.name);
          if (!nn || nn === f.name) return;
          const rr = await post('/api/files/rename', { serial: S.serial, path: f.path, newName: nn });
          toast(rr.ok ? '已重命名' : (rr.msg || '失败')); loadFiles(S.path);
        } },
        { label: '删除', danger: true, fn: async () => {
          if (!(await confirmBox('删除 ' + f.name + '？'))) return;
          const rr = await post('/api/files/delete', { serial: S.serial, paths: [f.path] });
          toast(rr.ok ? '已删除' : (rr.msg || '失败')); loadFiles(S.path);
        } },
      ];
      if (/\.apk$/i.test(f.name)) {
        opts.unshift({ label: '直接安装到电视', fn: async () => {
          const rr = await post('/api/install-device', { serial: S.serial, paths: [f.path], channel: 'pm',
            allow_home: $('optHome').checked, grant: $('optGrant').checked });
          toast(rr.ok ? '开始安装' : (rr.msg || '失败'));
          if (rr.ok) openDrawer(true);
        } });
      }
      sheet(f.name, opts);
    };
    box.appendChild(el);
  });
}

function renderCrumbs(path) {
  const parts = path.split('/').filter(Boolean);
  let acc = '';
  const html = ['<a href="#" data-p="/">根</a>'].concat(parts.map((p) => {
    acc += '/' + p;
    return `<a href="#" data-p="${esc(acc)}">${esc(p)}</a>`;
  }));
  const c = $('crumbs');
  c.innerHTML = html.join('<span class="muted"> / </span>');
  c.querySelectorAll('a').forEach((a) => a.onclick = (e) => { e.preventDefault(); loadFiles(a.dataset.p); });
}

async function uploadToTv(files) {
  if (!S.serial) { toast('先连接电视'); return; }
  for (const f of files) {
    toast('上传 ' + f.name + ' …');
    const r = await api(`/api/files/upload?path=${encodeURIComponent(S.path)}&name=${encodeURIComponent(f.name)}&serial=${encodeURIComponent(S.serial)}`,
      { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: f });
    if (!r.ok) { toast('上传失败：' + (r.msg || '')); break; }
  }
  toast('上传完成');
  loadFiles(S.path);
}

/* ---------------------------------------------------------------- 日志 */
const logEl = () => $('log');
function addLog(text, level) {
  const el = logEl();
  const d = document.createElement('div');
  d.className = 'l-' + (level || 'info');
  d.textContent = text;
  const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 24;
  el.appendChild(d);
  while (el.childNodes.length > 600) el.removeChild(el.firstChild);
  if (atBottom) el.scrollTop = el.scrollHeight;
}
function openDrawer(open) {
  $('drawer').classList.toggle('open', open);
  if (open) setTimeout(() => { logEl().scrollTop = logEl().scrollHeight; }, 60);
}

function connectSSE() {
  const es = new EventSource('/api/events');
  es.onmessage = (e) => {
    let ev; try { ev = JSON.parse(e.data); } catch (_) { return; }
    if (ev.type === 'log') addLog(ev.text, ev.level);
    if (ev.type === 'job') {
      const done = ev.job.status !== 'running';
      ev.job.items.forEach((ji) => {
        const q = S.queue.find((x) => x.path === ji.path && (x.status !== 'done' || ji.status === 'done'));
        if (q) { q.status = ji.status; q.msg = ji.msg || ''; q.meta = ji.meta || q.meta; }
      });
      // 安装完成后把队列里已完成的项从待装集合排除
      renderQueue();
      if (done) {
        toast('安装任务结束');
        loadState();
        if (S.apps.length) loadApps();
      }
    }
  };
  es.onerror = () => { es.close(); setTimeout(connectSSE, 2500); };
}

/* ---------------------------------------------------------------- 设置 */
async function saveSettings() {
  const r = await post('/api/settings', {
    last_ip: $('setIp').value.trim(),
    last_port: Number($('setPort').value.trim() || 5555),
    channel: $('setChannel').value,
    auto_accept: $('setAuto').checked,
    allow_home: $('setHome').checked,
    grant: $('setGrant').checked,
    lan: $('setLan').checked,
    adb: $('setAdb').value.trim(),
  });
  toast(r.ok ? '已保存（端口/局域网改动需重启程序生效）' : (r.msg || '保存失败'));
  if (r.ok) loadState();
}

/* ---------------------------------------------------------------- 绑定 */
$('btnDevice').onclick = () => { setTab('install'); setTimeout(() => $('ip').focus(), 200); };
$('btnConnect').onclick = connect;
$('btnRefreshDev').onclick = () => { loadState(); toast('已刷新'); };
$('ip').onkeydown = (e) => { if (e.key === 'Enter') connect(); };
$('port').onkeydown = (e) => { if (e.key === 'Enter') connect(); };

$('drop').onclick = () => $('file').click();
$('file').onchange = () => { uploadFiles($('file').files); $('file').value = ''; };
['dragenter', 'dragover'].forEach((ev) => $('drop').addEventListener(ev, (e) => { e.preventDefault(); $('drop').classList.add('hot'); }));
['dragleave', 'drop'].forEach((ev) => $('drop').addEventListener(ev, (e) => { e.preventDefault(); $('drop').classList.remove('hot'); }));
$('drop').addEventListener('drop', (e) => uploadFiles(e.dataTransfer.files));

$('btnInstall').onclick = startInstall;
$('btnCancel').onclick = async () => { await post('/api/cancel'); toast('已请求取消'); };

$('btnLog').onclick = () => openDrawer(!$('drawer').classList.contains('open'));
$('btnCloseLog').onclick = () => openDrawer(false);
$('btnClearLog').onclick = () => { logEl().innerHTML = ''; };

$('btnLoadApps').onclick = loadApps;
$('appSearch').oninput = renderApps;

$('btnFileRefresh').onclick = () => loadFiles(S.path);
$('btnUp').onclick = () => { const up = S.path.replace(/\/[^/]+\/?$/, '') || '/'; loadFiles(up); };
$('btnMkdir').onclick = async () => {
  const name = await askText('新建文件夹名', '');
  if (!name) return;
  const r = await post('/api/files/mkdir', { serial: S.serial, path: S.path.replace(/\/$/, '') + '/' + name });
  toast(r.ok ? '已创建' : (r.msg || '失败'));
  loadFiles(S.path);
};
$('btnUpload').onclick = () => $('fileUpload').click();
$('fileUpload').onchange = () => { uploadToTv($('fileUpload').files); $('fileUpload').value = ''; };

$('btnSave').onclick = saveSettings;
$('btnCopyUrl').onclick = async () => {
  const text = S.lanUrl + (S.pin ? '?pin=' + S.pin : '');
  try { await navigator.clipboard.writeText(text); toast('已复制：' + text); }
  catch (e) { toast(text); }
};

/* ---------------------------------------------------------------- 启动 */
(async function init() {
  await loadState();
  connectSSE();
  if (S.serial) { $('devName').textContent = '已连接'; }
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  }
})();
