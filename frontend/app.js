/* ================================================================
   VioDetect AI – Dashboard Logic
================================================================ */

const API_BASE = 'http://localhost:5000';

let currentMode = 'image';   // 'image' | 'video'
let currentFile = null;

// ── Page Navigation ──────────────────────────────────────────────
function showPage(name, el) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('page-' + name).classList.add('active');
  if (el) el.classList.add('active');
  document.getElementById('page-title').textContent = {
    dashboard: 'Dashboard',
    detect:    'Phát Hiện Vi Phạm',
    videos:    'Danh Sách Video',
    violations:'Danh Sách Vi Phạm',
  }[name] || name;

  if (name === 'dashboard')  loadDashboard();
  if (name === 'videos')     loadVideos();
  if (name === 'violations') loadViolations();
  return false;
}

// ── Clock ─────────────────────────────────────────────────────────
function updateClock() {
  const now = new Date();
  document.getElementById('clock').textContent =
    now.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
setInterval(updateClock, 1000);
updateClock();

// ── Theme Toggle ──────────────────────────────────────────────────
function toggleTheme() {
  const body = document.getElementById('app-body');
  const btn  = document.getElementById('themeToggle');
  if (body.classList.contains('dark')) {
    body.classList.replace('dark', 'light');
    btn.querySelector('span').textContent = 'Dark Mode';
  } else {
    body.classList.replace('light', 'dark');
    btn.querySelector('span').textContent = 'Light Mode';
  }
  localStorage.setItem('theme', body.classList.contains('dark') ? 'dark' : 'light');
}

// Restore saved theme
(function() {
  const t = localStorage.getItem('theme');
  if (t === 'light') {
    document.getElementById('app-body').classList.replace('dark', 'light');
    const btn = document.getElementById('themeToggle');
    if (btn) btn.querySelector('span').textContent = 'Dark Mode';
  }
})();

// ── API Status Check ──────────────────────────────────────────────
async function checkApiStatus() {
  const dot  = document.querySelector('.status-dot');
  const text = document.getElementById('apiStatusText');
  try {
    const r = await fetch(API_BASE + '/', { signal: AbortSignal.timeout(3000) });
    if (r.ok) {
      dot.className  = 'status-dot online';
      text.textContent = 'API Online';
    } else throw new Error();
  } catch {
    dot.className  = 'status-dot offline';
    text.textContent = 'API Offline';
  }
}
checkApiStatus();
setInterval(checkApiStatus, 15000);

// ── Toast ─────────────────────────────────────────────────────────
function showToast(message, type = 'info', duration = 4000) {
  const container = document.getElementById('toastContainer');
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  const icons = { success: '✅', error: '❌', info: 'ℹ️' };
  toast.innerHTML = `<span>${icons[type] || '•'}</span><span>${message}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(12px)';
    toast.style.transition = 'all .3s';
    setTimeout(() => toast.remove(), 300);
  }, duration);
}

// ── Dashboard ─────────────────────────────────────────────────────
async function loadDashboard() {
  try {
    const [videosRes, violationsRes] = await Promise.all([
      fetch(API_BASE + '/videos'),
      fetch(API_BASE + '/violations'),
    ]);

    if (!videosRes.ok || !violationsRes.ok) throw new Error('API error');

    const videos     = await videosRes.json();
    const violations = await violationsRes.json();

    document.getElementById('totalVideos').textContent     = videos.length;
    document.getElementById('totalViolations').textContent = violations.length;
    document.getElementById('totalHelmet').textContent     =
      violations.filter(v => v.violation_type === 'no_helmet').length;
    document.getElementById('totalTriple').textContent     =
      violations.filter(v => v.violation_type === 'overloaded_motorcycle').length;

    // Recent violations (last 10)
    const tbody = document.getElementById('dashViolationsBody');
    const recent = [...violations].slice(0, 10);
    if (recent.length === 0) {
      tbody.innerHTML = `<tr><td colspan="6" class="loading-row">Chưa có vi phạm nào</td></tr>`;
      return;
    }
    tbody.innerHTML = recent.map(v => `
      <tr>
        <td>#${v.id}</td>
        <td>${v.video_id ?? '—'}</td>
        <td><span class="badge ${violationClass(v.violation_type)}">${violationLabel(v.violation_type)}</span></td>
        <td>${v.confidence ? (v.confidence * 100).toFixed(1) + '%' : '—'}</td>
        <td>${v.frame_number ?? '—'}</td>
        <td>${formatTime(v.created_at)}</td>
      </tr>`).join('');
  } catch (e) {
    document.getElementById('totalVideos').textContent     = 'Err';
    document.getElementById('totalViolations').textContent = 'Err';
    showToast('Không thể kết nối API. Kiểm tra Flask server đang chạy.', 'error');
  }
}

// ── Videos Page ───────────────────────────────────────────────────
async function loadVideos() {
  const tbody = document.getElementById('videosBody');
  tbody.innerHTML = `<tr><td colspan="6" class="loading-row"><div class="spinner"></div> Đang tải...</td></tr>`;
  try {
    const res    = await fetch(API_BASE + '/videos');
    const videos = await res.json();
    if (videos.length === 0) {
      tbody.innerHTML = `<tr><td colspan="6" class="loading-row">Chưa có video nào</td></tr>`;
      return;
    }
    tbody.innerHTML = videos.map(v => `
      <tr>
        <td>#${v.id}</td>
        <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${v.file_name}">${v.file_name ?? '—'}</td>
        <td><span class="badge ${statusClass(v.status)}">${statusLabel(v.status)}</span></td>
        <td>${v.violation_count ?? 0}</td>
        <td>${formatTime(v.upload_time)}</td>
        <td>
          ${v.status === 'pending' || v.status === 'error'
            ? `<button class="btn-process" onclick="processVideo(${v.id}, this)">▶ Xử lý</button>`
            : v.annotated_url
              ? `<a href="${API_BASE}${v.annotated_url.startsWith('/') ? v.annotated_url : ''}${!v.annotated_url.startsWith('http') ? '' : ''}" target="_blank" class="btn-outline btn-sm">Xem</a>`
              : '—'}
        </td>
      </tr>`).join('');
  } catch {
    showToast('Lỗi tải danh sách video', 'error');
  }
}

async function processVideo(id, btn) {
  btn.disabled = true;
  btn.textContent = '⌛ Đang xử lý...';
  showToast(`Đang xử lý video #${id}...`, 'info');
  try {
    const res  = await fetch(`${API_BASE}/process-video/${id}`, { method: 'POST' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Lỗi không xác định');
    showToast(`Video #${id} xử lý xong! Vi phạm: ${data.summary?.total_violations ?? 0}`, 'success');
    loadVideos();
    loadDashboard();
  } catch (e) {
    showToast(`Lỗi: ${e.message}`, 'error');
    btn.disabled = false;
    btn.textContent = '▶ Xử lý';
  }
}

// ── Violations Page ───────────────────────────────────────────────
async function loadViolations() {
  const tbody     = document.getElementById('violationsBody');
  const filterVal = document.getElementById('filterType').value;
  tbody.innerHTML = `<tr><td colspan="7" class="loading-row"><div class="spinner"></div> Đang tải...</td></tr>`;
  try {
    const res        = await fetch(API_BASE + '/violations');
    let violations   = await res.json();
    if (filterVal) violations = violations.filter(v => v.violation_type === filterVal);
    if (violations.length === 0) {
      tbody.innerHTML = `<tr><td colspan="7" class="loading-row">Không có vi phạm nào</td></tr>`;
      return;
    }
    tbody.innerHTML = violations.map(v => `
      <tr>
        <td>#${v.id}</td>
        <td>#${v.video_id ?? '—'}</td>
        <td><span class="badge ${violationClass(v.violation_type)}">${violationLabel(v.violation_type)}</span></td>
        <td>${v.label ?? '—'}</td>
        <td>${v.confidence ? (v.confidence * 100).toFixed(1) + '%' : '—'}</td>
        <td>${v.frame_number ?? '—'}</td>
        <td>${formatTime(v.created_at)}</td>
      </tr>`).join('');
  } catch {
    showToast('Lỗi tải danh sách vi phạm', 'error');
  }
}

// ── Detect Mode ───────────────────────────────────────────────────
function setMode(mode) {
  currentMode = mode;
  clearFile();
  document.getElementById('modeImage').classList.toggle('active', mode === 'image');
  document.getElementById('modeVideo').classList.toggle('active', mode === 'video');
  document.getElementById('dropSub').textContent = mode === 'image'
    ? 'Chấp nhận: JPG, PNG, BMP, WEBP'
    : 'Chấp nhận: MP4, AVI, MOV, MKV';
  document.getElementById('videoOptions').style.display = mode === 'video' ? 'flex' : 'none';
  document.getElementById('fileInput').accept = mode === 'image'
    ? '.jpg,.jpeg,.png,.bmp,.webp'
    : '.mp4,.avi,.mov,.mkv';
}

// ── File handling ─────────────────────────────────────────────────
function onFileSelected(event) {
  const file = event.target.files[0];
  if (file) loadFile(file);
}

function onDragOver(event) {
  event.preventDefault();
  document.getElementById('dropZone').classList.add('drag-over');
}

function onDragLeave(event) {
  document.getElementById('dropZone').classList.remove('drag-over');
}

function onDrop(event) {
  event.preventDefault();
  document.getElementById('dropZone').classList.remove('drag-over');
  const file = event.dataTransfer.files[0];
  if (file) loadFile(file);
}

function loadFile(file) {
  currentFile = file;
  const isImage = file.type.startsWith('image/');
  const isVideo = file.type.startsWith('video/');

  if (!isImage && !isVideo) {
    showToast('Định dạng file không được hỗ trợ', 'error');
    return;
  }

  // Auto-switch mode
  if (isImage) setMode('image');
  if (isVideo) setMode('video');

  // Show preview
  document.getElementById('previewArea').style.display = 'block';
  document.getElementById('previewFileName').textContent = file.name;
  document.getElementById('dropZone').style.display     = 'none';

  const url = URL.createObjectURL(file);
  if (isImage) {
    document.getElementById('previewImage').src     = url;
    document.getElementById('previewImage').style.display = 'block';
    document.getElementById('previewVideo').style.display = 'none';
  } else {
    document.getElementById('previewVideo').src     = url;
    document.getElementById('previewVideo').style.display = 'block';
    document.getElementById('previewImage').style.display = 'none';
  }

  document.getElementById('detectBtn').disabled = false;
  resetResult();
}

function clearFile() {
  currentFile = null;
  document.getElementById('fileInput').value          = '';
  document.getElementById('previewArea').style.display = 'none';
  document.getElementById('dropZone').style.display   = 'block';
  document.getElementById('detectBtn').disabled       = true;
  resetResult();
}

function resetResult() {
  document.getElementById('resultPlaceholder').style.display = 'flex';
  document.getElementById('progressWrap').style.display      = 'none';
  document.getElementById('resultImage').style.display       = 'none';
  document.getElementById('resultVideo').style.display       = 'none';
  document.getElementById('resultSummary').style.display     = 'none';
  document.getElementById('violationsList').style.display    = 'none';
  document.getElementById('resultBadge').style.display       = 'none';
}

// ── Detect ────────────────────────────────────────────────────────
async function runDetect() {
  if (!currentFile) return;

  const btn = document.getElementById('detectBtn');
  btn.disabled = true;
  btn.innerHTML = '<div class="spinner"></div> Đang phân tích...';

  document.getElementById('resultPlaceholder').style.display = 'none';
  document.getElementById('progressWrap').style.display      = 'block';
  document.getElementById('resultImage').style.display       = 'none';
  document.getElementById('resultVideo').style.display       = 'none';
  document.getElementById('resultSummary').style.display     = 'none';
  document.getElementById('violationsList').style.display    = 'none';

  // Animate progress bar
  const fill = document.getElementById('progressFill');
  const txt  = document.getElementById('progressText');
  fill.style.width = '0%';
  txt.textContent  = 'Đang tải model AI...';

  let fakeProgress = 0;
  const fakeInterval = setInterval(() => {
    if (fakeProgress < 85) {
      fakeProgress += Math.random() * 8;
      fill.style.width = Math.min(fakeProgress, 85) + '%';
      if (fakeProgress > 30) txt.textContent = 'Đang chạy YOLO detect...';
      if (fakeProgress > 60) txt.textContent = 'Đang annotate kết quả...';
    }
  }, 400);

  try {
    const formData = new FormData();
    const endpoint = currentMode === 'image' ? '/detect/image' : '/detect/video';

    if (currentMode === 'image') {
      formData.append('file', currentFile);
    } else {
      formData.append('file', currentFile);
      formData.append('frame_skip', document.getElementById('frameSkip').value);
    }

    const res  = await fetch(API_BASE + endpoint, { method: 'POST', body: formData });
    const data = await res.json();

    clearInterval(fakeInterval);
    fill.style.width = '100%';
    txt.textContent  = 'Hoàn thành!';

    if (!res.ok) throw new Error(data.error || 'Lỗi phân tích');

    setTimeout(() => showResult(data), 400);

  } catch (e) {
    clearInterval(fakeInterval);
    document.getElementById('progressWrap').style.display  = 'none';
    document.getElementById('resultPlaceholder').style.display = 'flex';
    showToast(`Lỗi: ${e.message}`, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = `
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
        <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
      </svg>
      Phân Tích AI`;
  }
}

function showResult(data) {
  document.getElementById('progressWrap').style.display = 'none';

  const summary   = data.summary || {};
  const hasViolations = summary.has_violations;
  const violations    = data.violations || [];

  // Annotated media
  if (data.annotated_url) {
    const url = API_BASE + data.annotated_url;
    if (currentMode === 'image') {
      const img = document.getElementById('resultImage');
      img.src = url + '?t=' + Date.now();
      img.style.display = 'block';
    } else {
      const vid = document.getElementById('resultVideo');
      vid.src = url + '?t=' + Date.now();
      vid.style.display = 'block';
    }
  }

  // Badge
  const badge = document.getElementById('resultBadge');
  badge.style.display = 'inline-flex';
  if (hasViolations) {
    badge.className  = 'badge badge-red';
    badge.textContent = `${summary.total_violations} vi phạm`;
  } else {
    badge.className  = 'badge badge-green';
    badge.textContent = 'Không vi phạm';
  }

  // Summary numbers
  document.getElementById('sumTotal').textContent  = summary.total_violations ?? 0;
  document.getElementById('sumHelmet').textContent = summary.no_helmet_count ?? 0;
  document.getElementById('sumTriple').textContent = summary.overloaded_motorcycle_count ?? 0;
  document.getElementById('resultSummary').style.display = 'grid';

  // Violations list
  if (violations.length > 0) {
    document.getElementById('violationsList').style.display = 'block';
    document.getElementById('violationsListBody').innerHTML = violations.map(v => `
      <div class="violation-item">
        <div class="v-icon ${v.type}">${v.type === 'no_helmet' ? '⛑️' : '🏍️'}</div>
        <div class="v-info">
          <div class="v-label">${v.label}</div>
          <div class="v-meta">${v.type === 'overloaded_motorcycle' ? `${v.people_count} người` : ''} ${v.frame !== undefined ? `· Frame ${v.frame}` : ''}</div>
        </div>
        <span class="v-conf">${v.conf ? (v.conf * 100).toFixed(1) + '%' : ''}</span>
      </div>`).join('');
  }

  if (hasViolations) {
    showToast(`Phát hiện ${summary.total_violations} vi phạm!`, 'error');
  } else {
    showToast('Không phát hiện vi phạm', 'success');
  }
}

// ── Helpers ───────────────────────────────────────────────────────
function violationLabel(type) {
  return { no_helmet: 'Không MBH', overloaded_motorcycle: 'Chở 3 người' }[type] || type;
}
function violationClass(type) {
  return { no_helmet: 'vtype-no_helmet', overloaded_motorcycle: 'vtype-overloaded_motorcycle' }[type] || 'badge-gray';
}
function statusLabel(s) {
  return { pending: 'Chờ xử lý', processing: 'Đang xử lý', processed: 'Đã xử lý', error: 'Lỗi' }[s] || s;
}
function statusClass(s) {
  return { pending: 'badge-blue', processing: 'badge-orange', processed: 'badge-green', error: 'badge-red' }[s] || 'badge-gray';
}
function formatTime(t) {
  if (!t) return '—';
  try {
    return new Date(t).toLocaleString('vi-VN', { dateStyle: 'short', timeStyle: 'short' });
  } catch { return t; }
}

// ── Init ──────────────────────────────────────────────────────────
loadDashboard();
