/* ================================================================
   VioDetect AI – Dashboard & RBAC Client Logic
   Hỗ trợ: Phân quyền CSGT/Admin vs Viewer, ALPR biển số xe, Vượt đèn đỏ
================================================================ */

const API_BASE = (window.location.port === '5000' || (window.location.protocol === 'http:' && !window.location.port))
  ? window.location.origin
  : 'http://localhost:5000';

let currentMode = 'image';   // 'image' | 'video'
let currentFile = null;
let currentUser = null;      // { username, role: 'admin'|'viewer', name, badge_id }
let editingViolationId = null;
let activeRecoveryTokens = null;
let videoPageOffset = 0;
let violationPageOffset = 0;
let imageHistoryOffset = 0;
let auditPageOffset = 0;
const TABLE_PAGE_SIZE = 50;
let videoSearchTimer = null;
let violationSearchTimer = null;
let auditSearchTimer = null;
const pollingVideoIds = new Set();
let resultObjectUrl = null;

// ── Auth Token Helper ─────────────────────────────────────────────
function getAuthToken() {
  return localStorage.getItem('viodetect_token');
}

function setAuthToken(token) {
  if (token) localStorage.setItem('viodetect_token', token);
  else localStorage.removeItem('viodetect_token');
}

function getAuthHeaders() {
  const token = getAuthToken();
  const headers = {};
  if (token) headers['Authorization'] = `Bearer ${token}`;
  return headers;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
  })[char]);
}

async function readApiJson(response, operation) {
  const body = await response.text();
  try {
    return JSON.parse(body);
  } catch {
    const contentType = response.headers.get('content-type') || 'không có Content-Type';
    const endpoint = new URL(response.url).pathname;
    const preview = escapeHtml(body.replace(/\s+/g, ' ').trim().slice(0, 240));
    throw new Error(
      `${operation}: API trả về dữ liệu không phải JSON ` +
      `(HTTP ${response.status}, ${contentType}, ${endpoint}).` +
      `${preview ? ` Phản hồi: ${preview}` : ''}`
    );
  }
}

// ── Page Navigation ──────────────────────────────────────────────
function showPage(name, el) {
  if (name === 'audit' && currentUser?.role !== 'admin') {
    showToast('Chỉ quản trị viên được xem nhật ký hoạt động.', 'error');
    return false;
  }
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));

  const pageEl = document.getElementById('page-' + name);
  if (pageEl) pageEl.classList.add('active');
  if (el) el.classList.add('active');

  const titles = {
    dashboard: 'Dashboard Giám Sát',
    detect:    'Phân tích vi phạm bằng AI',
    videos:    'Quản Lý Video Giám Sát',
    violations:'Xử Lý Vi Phạm & Phạt Nguội',
    'image-history': 'Lịch Sử Phân Tích Ảnh',
    audit: 'Nhật Ký Hoạt Động',
  };
  document.getElementById('page-title').textContent = titles[name] || name;

  if (name === 'dashboard')  loadDashboard();
  if (name === 'videos')     loadVideos();
  if (name === 'violations') loadViolations();
  if (name === 'image-history') loadImageHistory();
  if (name === 'audit' && currentUser?.role === 'admin') loadAuditLogs();
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

// ── Toast Notification ────────────────────────────────────────────
function showToast(message, type = 'info', duration = 3500) {
  const container = document.getElementById('toastContainer');
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  const icons = { success: '✅', error: '❌', info: 'ℹ️', warning: '⚠️' };
  toast.innerHTML = `<span>${icons[type] || '•'}</span><span>${message}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(12px)';
    toast.style.transition = 'all .3s';
    setTimeout(() => toast.remove(), 300);
  }, duration);
}

// ── API Status Check ──────────────────────────────────────────────
async function checkApiStatus() {
  const dot  = document.querySelector('.status-dot');
  const text = document.getElementById('apiStatusText');
  try {
    const r = await fetch(API_BASE + '/health', { signal: AbortSignal.timeout(3000) });
    const health = await r.json();
    if (health.api === 'ok' && health.error_code === 'SUPABASE_WORKER_SCHEMA_MISSING') {
      dot.className = 'status-dot degraded';
      text.textContent = 'Thiếu bảng heartbeat worker';
      text.title = 'Chạy migration supabase_schema.sql trong Supabase SQL Editor để tạo bảng worker_heartbeats.';
      return;
    }
    if (health.api === 'ok' && ['stale', 'not_running'].includes(health.video_worker)) {
      dot.className = 'status-dot degraded';
      text.textContent = 'Worker video không hoạt động';
      text.title = 'Khởi động backend/process_pending_videos.py để xử lý video trong hàng đợi.';
      return;
    }
    if (health.api === 'ok' && health.video_worker === 'error') {
      dot.className = 'status-dot degraded';
      text.textContent = 'Không kiểm tra được worker video';
      text.title = 'Kiểm tra quyền truy cập bảng worker_heartbeats và nhật ký backend.';
      return;
    }
    if (!r.ok) {
      if (health.api === 'ok' && health.error_code === 'SUPABASE_INVALID_API_KEY') {
        dot.className = 'status-dot degraded';
        text.textContent = 'Supabase API key không hợp lệ';
        text.title = 'Kiểm tra SUPABASE_URL và các API key thuộc cùng một project; sau đó khởi động lại backend và worker.';
        return;
      }
      throw new Error(health.error_code || 'Health check failed');
    }
    if (r.ok) {
      if (health.service_role_configured === false) {
        dot.className = 'status-dot degraded';
        text.textContent = 'Thiếu SUPABASE_SERVICE_ROLE_KEY';
        text.title = 'Cấu hình khóa service role trong môi trường backend rồi khởi động lại API và worker.';
      } else {
        dot.className = 'status-dot online';
        text.textContent = 'API Online';
        text.title = '';
      }
    }
  } catch {
    dot.className  = 'status-dot offline';
    text.textContent = 'API Offline';
  }
}
checkApiStatus();
setInterval(checkApiStatus, 15000);

// ── Authentication & RBAC ─────────────────────────────────────────
async function initAuth() {
  if (openRecoveryLink()) return;
  const token = getAuthToken();
  if (token) {
    try {
      const res = await fetch(`${API_BASE}/api/auth/me`, { headers: getAuthHeaders() });
      const data = await res.json();
      if (data.authenticated && data.user) {
        setCurrentUser(data.user);
        loadDashboard();
        if (document.getElementById('page-violations').classList.contains('active')) loadViolations();
        if (document.getElementById('page-videos').classList.contains('active')) loadVideos();
        return;
      }
    } catch (e) {
      console.warn("Không thể xác thực token:", e);
    }
  }
  setAuthToken(null);
  setCurrentUser(null);
  openLoginModal();
}

function setCurrentUser(user) {
  currentUser = user;
  const avatarEl = document.getElementById('userAvatar');
  const nameEl   = document.getElementById('userName');
  const badgeEl  = document.getElementById('userRoleBadge');
  const noticeEl = document.getElementById('roleNotice');
  const addAdminBtn = document.getElementById('btnAddAdmin');
  const auditNav = document.querySelector('.admin-only-nav');

  if (!user) {
    avatarEl.textContent = '👤';
    nameEl.textContent = 'Chưa đăng nhập';
    badgeEl.textContent = 'KHÁCH';
    badgeEl.className = 'user-role-badge';
    if (noticeEl) noticeEl.textContent = 'Đăng nhập để xem quyền truy cập của tài khoản.';
    if (addAdminBtn) addAdminBtn.style.display = 'none';
    if (auditNav) auditNav.style.display = 'none';
    if (document.getElementById('page-audit').classList.contains('active')) {
      document.getElementById('page-audit').classList.remove('active');
      document.getElementById('page-dashboard').classList.add('active');
    }
    return;
  }

  if (user.role === 'admin') {
    avatarEl.textContent = '👮';
    nameEl.textContent   = user.name || 'CSGT / Quản trị viên';
    badgeEl.textContent  = 'CSGT / ADMIN';
    badgeEl.className    = 'user-role-badge role-admin';
    if (noticeEl) noticeEl.textContent = '🟢 Quyền CSGT: Toàn quyền xem, duyệt xử phạt nguội, sửa biển số, xóa vi phạm.';
    if (addAdminBtn) addAdminBtn.style.display = 'inline-flex';
    if (auditNav) auditNav.style.display = 'flex';
  } else {
    avatarEl.textContent = '👁️';
    nameEl.textContent   = user.name || 'Viewer (Người xem)';
    badgeEl.textContent  = 'VIEWER';
    badgeEl.className    = 'user-role-badge role-viewer';
    if (noticeEl) noticeEl.textContent = '🟡 Quyền Viewer: Chỉ xem thống kê, tra cứu lịch sử, gửi ảnh/video phân tích AI.';
    if (addAdminBtn) addAdminBtn.style.display = 'none';
    if (auditNav) auditNav.style.display = 'none';
  }
  if (user && document.getElementById('cameraId')) loadCameraConfig();
}

function openLoginModal() {
  switchAuthTab('login');
  document.getElementById('loginModal').style.display = 'flex';
}

function closeLoginModal() {
  document.getElementById('loginModal').style.display = 'none';
}

function switchAuthTab(tab) {
  const isLogin = (tab === 'login');
  document.getElementById('tabLoginBtn').classList.toggle('active', isLogin);
  document.getElementById('tabRegisterBtn').classList.toggle('active', !isLogin);
  document.getElementById('authLoginForm').style.display = isLogin ? 'block' : 'none';
  document.getElementById('authRegisterForm').style.display = !isLogin ? 'block' : 'none';
  document.getElementById('authRecoveryForm').style.display = 'none';
}

function openRecoveryLink() {
  const params = new URLSearchParams(window.location.hash.slice(1));
  if (params.get('type') !== 'recovery') return false;
  activeRecoveryTokens = {
    access_token: params.get('access_token'),
    refresh_token: params.get('refresh_token')
  };
  if (!activeRecoveryTokens.access_token || !activeRecoveryTokens.refresh_token) {
    showToast('Liên kết đặt lại mật khẩu không hợp lệ hoặc đã hết hạn.', 'error');
    return false;
  }
  setAuthToken(null);
  setCurrentUser(null);
  document.getElementById('loginModal').style.display = 'flex';
  document.getElementById('authLoginForm').style.display = 'none';
  document.getElementById('authRegisterForm').style.display = 'none';
  document.getElementById('authRecoveryForm').style.display = 'block';
  return true;
}

async function requestPasswordReset() {
  const email = document.getElementById('loginUser').value.trim();
  if (!email) {
    showToast('Nhập email trước khi yêu cầu đặt lại mật khẩu.', 'warning');
    return;
  }
  try {
    const res = await fetch(`${API_BASE}/api/auth/password-reset`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Không gửi được email đặt lại mật khẩu.');
    showToast(data.message, 'info', 6000);
  } catch (err) {
    showToast(err.message, 'error');
  }
}

async function resendConfirmation() {
  const email = document.getElementById('loginUser').value.trim();
  if (!email) {
    showToast('Nhập email trước khi yêu cầu xác minh lại.', 'warning');
    return;
  }
  try {
    const res = await fetch(`${API_BASE}/api/auth/resend-confirmation`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Không gửi được email xác minh.');
    showToast(data.message, 'info', 6000);
  } catch (err) {
    showToast(err.message, 'error');
  }
}

async function completePasswordReset() {
  const password = document.getElementById('recoveryPassword').value;
  const confirm = document.getElementById('recoveryPasswordConfirm').value;
  if (password.length < 6) {
    showToast('Mật khẩu cần tối thiểu 6 ký tự.', 'warning');
    return;
  }
  if (password !== confirm) {
    showToast('Mật khẩu xác nhận không khớp.', 'error');
    return;
  }
  try {
    const res = await fetch(`${API_BASE}/api/auth/password-reset/complete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...activeRecoveryTokens, password })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Không cập nhật được mật khẩu.');
    activeRecoveryTokens = null;
    window.history.replaceState(null, '', window.location.pathname + window.location.search);
    switchAuthTab('login');
    showToast(data.message, 'success');
  } catch (err) {
    showToast(err.message, 'error');
  }
}

async function submitRegister() {
  const fullName = document.getElementById('regFullName').value.trim();
  const username = document.getElementById('regUsername').value.trim();
  const email    = document.getElementById('regEmail').value.trim();
  const pass     = document.getElementById('regPass').value;
  const passConf = document.getElementById('regPassConfirm').value;

  if (!fullName || !username || !email || !pass) {
    showToast('Vui lòng điền họ tên, tên đăng nhập, email và mật khẩu', 'warning');
    return;
  }
  if (pass.length < 6) {
    showToast('Mật khẩu cần tối thiểu 6 ký tự', 'warning');
    return;
  }
  if (pass !== passConf) {
    showToast('Mật khẩu xác nhận không khớp', 'error');
    return;
  }

  showToast('Đang tạo tài khoản trong Supabase...', 'info');
  try {
    const res = await fetch(`${API_BASE}/api/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ full_name: fullName, username, email, password: pass })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Đăng ký thất bại');

    if (data.token) {
      setAuthToken(data.token);
      setCurrentUser(data.user);
      closeLoginModal();
      loadDashboard();
      loadViolations();
      loadVideos();
      showToast('Đăng ký tài khoản Người xem thành công!', 'success');
    } else {
      switchAuthTab('login');
      document.getElementById('loginUser').value = email;
      showToast('Tài khoản đã được tạo. Hãy xác minh email trước khi đăng nhập.', 'info', 6000);
    }
  } catch (err) {
    showToast(err.message, 'error');
  }
}

// ── Admin Thêm Quản Trị Viên Mới ──────────────────────────────────
function openAddAdminModal() {
  document.getElementById('addAdminModal').style.display = 'flex';
}

function closeAddAdminModal() {
  document.getElementById('addAdminModal').style.display = 'none';
}

async function submitCreateAdmin() {
  const fullName = document.getElementById('adminFullName').value.trim();
  const username = document.getElementById('adminUsername').value.trim();
  const email    = document.getElementById('adminEmail').value.trim();
  const badgeId  = document.getElementById('adminBadge').value.trim();
  const unit     = document.getElementById('adminUnit').value.trim();
  const pass     = document.getElementById('adminPass').value;

  if (!fullName || !username || !email || !pass) {
    showToast('Vui lòng nhập đầy đủ họ tên, tên đăng nhập, email và mật khẩu', 'warning');
    return;
  }
  if (pass.length < 6) {
    showToast('Mật khẩu cần ít nhất 6 ký tự', 'warning');
    return;
  }

  try {
    const res = await fetch(`${API_BASE}/api/auth/create-admin`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...getAuthHeaders()
      },
      body: JSON.stringify({
        full_name: fullName,
        username,
        email,
        badge_id: badgeId,
        unit,
        password: pass
      })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Lỗi khi tạo quản trị viên');

    showToast(`Đã thêm Quản trị viên mới: ${fullName}`, 'success');
    closeAddAdminModal();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

async function doLogin(email, password, showNotify = true) {
  try {
    const res = await fetch(`${API_BASE}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Đăng nhập thất bại');

    setAuthToken(data.token);
    setCurrentUser(data.user);
    closeLoginModal();
    if (showNotify) showToast(`Chào mừng ${data.user.name}!`, 'success');

    // Tải lại dữ liệu trang hiện tại
    loadDashboard();
    loadViolations();
    loadVideos();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

function submitLogin() {
  const u = document.getElementById('loginUser').value.trim();
  const p = document.getElementById('loginPass').value;
  if (!u || !p) {
    showToast('Vui lòng nhập email và mật khẩu', 'warning');
    return;
  }
  doLogin(u, p);
}

async function handleLogout(e) {
  e.stopPropagation();
  try {
    await fetch(`${API_BASE}/api/auth/logout`, { method: 'POST', headers: getAuthHeaders() });
  } catch (err) {
    console.warn('Không thể thông báo đăng xuất tới máy chủ:', err);
  }
  setAuthToken(null);
  setCurrentUser(null);
  showToast('Đã đăng xuất', 'info');
  openLoginModal();
}

// ── Dashboard ─────────────────────────────────────────────────────
async function loadDashboard() {
  try {
    const res = await fetch(`${API_BASE}/api/dashboard`, { headers: getAuthHeaders() });
    const data = await res.json();
    if (res.status === 401) {
      setAuthToken(null);
      setCurrentUser(null);
      openLoginModal();
      return;
    }
    if (!res.ok) throw new Error(data.error || 'Không tải được dashboard.');

    document.getElementById('totalVideos').textContent = data.video_count;
    document.getElementById('totalViolations').textContent = data.violations_count;
    document.getElementById('totalHelmet').textContent = data.type_counts.no_helmet;
    document.getElementById('totalTriple').textContent = data.type_counts.overloaded_motorcycle;
    document.getElementById('totalRedLight').textContent = data.type_counts.red_light;
    loadViolationTrends();

    const tbody = document.getElementById('dashViolationsBody');
    const recent = data.recent_violations || [];
    if (recent.length === 0) {
      tbody.innerHTML = `<tr><td colspan="7" class="loading-row">Chưa có hồ sơ vi phạm được ghi nhận. Xem các lượt phân tích ảnh bên dưới; ảnh không phát hiện vi phạm sẽ không tạo hồ sơ xử lý.</td></tr>`;
    } else {
      tbody.innerHTML = recent.map(v => `
        <tr>
          <td>#${v.id}</td>
          <td>${v.video_id ? `#${v.video_id}` : 'Trực tiếp'}</td>
          <td><span class="plate-badge">${escapeHtml(v.license_plate || 'Chưa rõ')}</span></td>
          <td><span class="badge ${violationClass(v.violation_type)}">${escapeHtml(violationLabel(v.violation_type))}</span></td>
          <td>${v.confidence ? (v.confidence * 100).toFixed(1) + '%' : '—'}</td>
          <td><span class="badge ${reviewClass(v.review_status)}">${reviewLabel(v.review_status)}</span></td>
          <td>${formatTime(v.created_at)}</td>
        </tr>`).join('');
    }
    loadRecentImageAnalyses('dashboardRecentAnalysesBody', 4);
  } catch (e) {
    document.getElementById('totalVideos').textContent     = 'Err';
    document.getElementById('totalViolations').textContent = 'Err';
    showToast('Không thể tải dashboard. Hãy kiểm tra API và cấu hình cơ sở dữ liệu.', 'error');
  }
}

async function loadRecentImageAnalyses(tbodyId, limit = 5) {
  const tbody = document.getElementById(tbodyId);
  if (!tbody) return;
  tbody.innerHTML = `<tr><td colspan="5" class="loading-row">Đang tải...</td></tr>`;
  try {
    const params = new URLSearchParams({ limit: String(limit), offset: '0' });
    const res = await fetch(`${API_BASE}/image-analyses?${params}`, {
      headers: getAuthHeaders()
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Không tải được lịch sử phân tích ảnh.');
    const items = data.items || [];
    if (!items.length) {
      tbody.innerHTML = `<tr><td colspan="5" class="loading-row">Chưa có lượt phân tích ảnh. Hãy tải ảnh lên và chạy phân tích AI trước.</td></tr>`;
      return;
    }
    tbody.innerHTML = items.map(item => `
      <tr>
        <td>#${item.id}</td>
        <td>${escapeHtml(item.file_name || '—')}</td>
        <td>${Number(item.summary?.total_violations) || 0}</td>
        <td>${formatTime(item.created_at)}</td>
        <td><button class="btn-outline btn-sm" onclick="viewImageAnalysis(${Number(item.id)})">Xem bằng chứng</button></td>
      </tr>`).join('');
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="5" class="loading-row">${escapeHtml(err.message)}</td></tr>`;
  }
}

const TREND_TYPES = ['no_helmet', 'overloaded_motorcycle', 'red_light'];
const TREND_COLORS = {
  no_helmet: '#f85149',
  overloaded_motorcycle: '#f0883e',
  red_light: '#a371f7'
};

function initializeTrendDates() {
  const end = new Date();
  const start = new Date(end);
  start.setDate(start.getDate() - 29);
  document.getElementById('trendEndDate').value = toLocalDateInput(end);
  document.getElementById('trendStartDate').value = toLocalDateInput(start);
}

function toLocalDateInput(date) {
  const offset = date.getTimezoneOffset();
  return new Date(date.getTime() - offset * 60000).toISOString().slice(0, 10);
}

async function loadViolationTrends() {
  const startDate = document.getElementById('trendStartDate').value;
  const endDate = document.getElementById('trendEndDate').value;
  const bucket = document.getElementById('trendBucket').value;
  if (!startDate || !endDate || startDate > endDate) {
    showToast('Chọn khoảng ngày hợp lệ cho biểu đồ.', 'warning');
    return;
  }
  try {
    const params = new URLSearchParams({
      start_date: startDate,
      end_date: endDate,
      bucket
    });
    const res = await fetch(`${API_BASE}/api/dashboard/trends?${params}`, {
      headers: getAuthHeaders()
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Không tải được xu hướng vi phạm.');
    const analysesParams = new URLSearchParams({
      limit: '1',
      offset: '0',
      start_date: startDate,
      end_date: endDate,
    });
    const analysesRes = await fetch(`${API_BASE}/image-analyses?${analysesParams}`, {
      headers: getAuthHeaders()
    });
    const analysesData = await analysesRes.json();
    if (!analysesRes.ok) throw new Error(analysesData.error || 'Không tải được số lượt phân tích ảnh.');
    renderViolationTrends(data.items || [], analysesData.total || 0);
  } catch (err) {
    document.getElementById('trendChart').textContent = err.message;
    showToast(err.message, 'error');
  }
}

function renderViolationTrends(items, analysisCount = 0) {
  const chart = document.getElementById('trendChart');
  const groups = new Map();
  items.forEach(item => {
    const date = item.bucket_start;
    if (!groups.has(date)) groups.set(date, Object.fromEntries(TREND_TYPES.map(type => [type, 0])));
    groups.get(date)[item.violation_type] = Number(item.violation_count) || 0;
  });
  const dates = [...groups.keys()];
  const violationCount = items.reduce((sum, item) => sum + (Number(item.violation_count) || 0), 0);
  if (!dates.length || violationCount === 0) {
    chart.innerHTML = `<div class="trend-empty-state">Chưa ghi nhận vi phạm trong khoảng thời gian đã chọn.<br><strong>${Number(analysisCount)} lượt phân tích ảnh</strong> đã được thực hiện trong khoảng này. Ảnh không phát hiện vi phạm không được tính là hồ sơ vi phạm.</div>`;
    document.getElementById('trendLegend').innerHTML = '';
    return;
  }

  const plotHeight = 220;
  const top = 18;
  const bottom = 52;
  const slotWidth = 32;
  const width = Math.max(720, dates.length * slotWidth + 60);
  const height = plotHeight + top + bottom;
  const maxValue = Math.max(1, ...dates.map(date =>
    TREND_TYPES.reduce((sum, type) => sum + groups.get(date)[type], 0)
  ));
  const scaleMax = Math.ceil(maxValue / 5) * 5 || 1;
  const barWidth = Math.max(8, Math.min(22, slotWidth * 0.62));
  const plotBottom = top + plotHeight;
  const grid = [];
  for (let tick = 0; tick <= 4; tick++) {
    const value = Math.round(scaleMax * tick / 4);
    const y = plotBottom - (value / scaleMax) * plotHeight;
    grid.push(`<line x1="42" y1="${y}" x2="${width - 8}" y2="${y}" class="trend-grid"/>`);
    grid.push(`<text x="34" y="${y + 4}" text-anchor="end" class="trend-axis-label">${value}</text>`);
  }

  const bars = dates.map((date, index) => {
    const x = 52 + index * slotWidth;
    let y = plotBottom;
    const segments = TREND_TYPES.map(type => {
      const count = groups.get(date)[type];
      const segmentHeight = count / scaleMax * plotHeight;
      y -= segmentHeight;
      return `<rect x="${x}" y="${y}" width="${barWidth}" height="${Math.max(0, segmentHeight)}" fill="${TREND_COLORS[type]}" rx="2"><title>${date}: ${violationLabel(type)} — ${count}</title></rect>`;
    }).join('');
    const showLabel = dates.length <= 14 || index % Math.ceil(dates.length / 14) === 0;
    const label = showLabel
      ? `<text x="${x + barWidth / 2}" y="${plotBottom + 20}" text-anchor="middle" class="trend-axis-label">${escapeHtml(date.slice(5))}</text>`
      : '';
    return segments + label;
  }).join('');

  chart.innerHTML = `<div class="trend-chart-scroll"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Biểu đồ cột xếp chồng số vi phạm">${grid.join('')}${bars}</svg></div>`;
  document.getElementById('trendLegend').innerHTML = TREND_TYPES.map(type =>
    `<span><i style="background:${TREND_COLORS[type]}"></i>${escapeHtml(violationLabel(type))}</span>`
  ).join('');
}

// ── Videos Page ───────────────────────────────────────────────────
async function loadVideos() {
  const tbody = document.getElementById('videosBody');
  tbody.innerHTML = `<tr><td colspan="6" class="loading-row"><div class="spinner"></div> Đang tải...</td></tr>`;
  try {
    const params = new URLSearchParams({
      limit: String(TABLE_PAGE_SIZE),
      offset: String(videoPageOffset),
      search: document.getElementById('videoSearch').value.trim(),
      status: document.getElementById('videoStatusFilter').value
    });
    const res = await fetch(`${API_BASE}/videos?${params}`, { headers: getAuthHeaders() });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Lỗi tải danh sách video.');
    const videos = data.items || [];
    const total = data.total || 0;
    document.getElementById('videosPageInfo').textContent =
      total ? `${videoPageOffset + 1}-${Math.min(videoPageOffset + videos.length, total)} / ${total}` : '0 video';
    if (videos.length === 0) {
      tbody.innerHTML = `<tr><td colspan="6" class="loading-row">Chưa có video nào</td></tr>`;
      return;
    }

    const isAdmin = currentUser && currentUser.role === 'admin';

    tbody.innerHTML = videos.map(v => `
      <tr>
        <td>#${v.id}</td>
        <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escapeHtml(v.file_name)}">${escapeHtml(v.file_name || '—')}</td>
        <td>
          <span class="badge ${statusClass(v.status)}">${statusLabel(v.status)}</span>
          ${v.status === 'processing' ? `<small class="job-progress">${v.processing_progress || 0}%</small>` : ''}
          ${v.processing_error ? `<small class="job-error" title="${escapeHtml(v.processing_error)}">${escapeHtml(v.processing_error)}</small>` : ''}
        </td>
        <td><strong class="count-tag">${v.violation_count ?? 0}</strong></td>
        <td>${formatTime(v.upload_time)}</td>
        <td>
          <div class="action-btn-group">
            ${v.status === 'pending' || v.status === 'error'
              ? `<button class="btn-process" onclick="processVideo(${v.id}, this)">▶ ${v.status === 'error' ? 'Thử lại' : 'Xếp hàng'}</button>`
              : v.has_annotated_video
                ? `<button type="button" onclick="openAnnotatedVideo(${v.id})" class="btn-outline btn-sm">Xem video</button>`
                : '—'}
            ${isAdmin ? `<button class="btn-del-sm" onclick="deleteVideo(${v.id})" title="Xóa video">🗑️</button>` : ''}
          </div>
        </td>
      </tr>`).join('');
    videos.filter(v => v.status === 'processing').forEach(v => monitorVideoJob(v.id));
  } catch {
    showToast('Lỗi tải danh sách video', 'error');
  }
}

async function processVideo(id, btn) {
  btn.disabled = true;
  btn.textContent = '⌛ Đang xếp hàng...';
  try {
    const res = await fetch(`${API_BASE}/process-video/${id}`, {
      method: 'POST',
      headers: getAuthHeaders()
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Lỗi không xác định');
    showToast(`Video #${id} đã được đưa vào hàng đợi.`, 'success');
    loadVideos();
    monitorVideoJob(id);
  } catch (e) {
    showToast(`Lỗi: ${e.message}`, 'error');
    btn.disabled = false;
    btn.textContent = '▶ Xử lý AI';
  }
}

async function openAnnotatedVideo(id) {
  const tab = window.open('about:blank', '_blank');
  if (!tab) {
    showToast('Trình duyệt đã chặn cửa sổ xem video.', 'error');
    return;
  }
  try {
    const res = await fetch(`${API_BASE}/videos/${id}/annotated-url`, {
      headers: getAuthHeaders()
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Không mở được video kết quả.');
    tab.opener = null;
    tab.location = data.url;
  } catch (err) {
    tab.close();
    showToast(err.message, 'error');
  }
}

async function monitorVideoJob(id) {
  if (pollingVideoIds.has(id)) return;
  pollingVideoIds.add(id);
  try {
    while (pollingVideoIds.has(id)) {
      const res = await fetch(`${API_BASE}/videos/${id}`, { headers: getAuthHeaders() });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `Không thể đọc trạng thái video #${id}.`);
      if (data.status === 'processed') {
          showToast(`Video #${id} xử lý xong; phát hiện ${data.violation_count || 0} vi phạm.`, 'success');
        loadVideos();
        loadDashboard();
        loadViolations();
        break;
      }
      if (data.status === 'error') {
        showToast(`Video #${id} lỗi: ${data.processing_error || 'Không rõ nguyên nhân'}`, 'error', 7000);
        loadVideos();
        break;
      }
      loadVideos();
      await new Promise(resolve => setTimeout(resolve, 3000));
    }
  } catch (err) {
    showToast(err.message, 'error');
  } finally {
    pollingVideoIds.delete(id);
  }
}

async function deleteVideo(id) {
  if (!confirm(`Bạn có chắc chắn muốn xóa video #${id} và các vi phạm liên quan?`)) return;
  try {
    const res = await fetch(`${API_BASE}/videos/${id}`, {
      method: 'DELETE',
      headers: getAuthHeaders()
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Không thể xóa');
    showToast(`Đã xóa video #${id}`, 'success');
    loadVideos();
    loadDashboard();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

async function loadImageHistory() {
  const tbody = document.getElementById('imageHistoryBody');
  tbody.innerHTML = '<tr><td colspan="5" class="loading-row">Đang tải...</td></tr>';
  const params = new URLSearchParams({
    limit: String(TABLE_PAGE_SIZE),
    offset: String(imageHistoryOffset)
  });
  const startDate = document.getElementById('imageHistoryStart').value;
  const endDate = document.getElementById('imageHistoryEnd').value;
  if (startDate) params.set('start_date', startDate);
  if (endDate) params.set('end_date', endDate);
  try {
    const res = await fetch(`${API_BASE}/image-analyses?${params}`, {
      headers: getAuthHeaders()
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Không tải được lịch sử ảnh.');
    const items = data.items || [];
    const total = data.total || 0;
    document.getElementById('imageHistoryPageInfo').textContent =
      total ? `${imageHistoryOffset + 1}-${Math.min(imageHistoryOffset + items.length, total)} / ${total}` : '0 ảnh';
    if (!items.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="loading-row">Chưa có lịch sử phân tích ảnh</td></tr>';
      return;
    }
    tbody.innerHTML = items.map(item => `
      <tr>
        <td>#${item.id}</td>
        <td>${escapeHtml(item.file_name)}</td>
        <td>${Number(item.summary?.total_violations) || 0}</td>
        <td>${formatTime(item.created_at)}</td>
        <td><button class="btn-outline btn-sm" onclick="viewImageAnalysis(${item.id})">Xem bằng chứng</button></td>
      </tr>`).join('');
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="5" class="loading-row">${escapeHtml(err.message)}</td></tr>`;
    showToast(err.message, 'error');
  }
}

function resetImageHistoryPage() {
  imageHistoryOffset = 0;
  loadImageHistory();
}

function changeImageHistoryPage(direction) {
  imageHistoryOffset = Math.max(0, imageHistoryOffset + direction * TABLE_PAGE_SIZE);
  loadImageHistory();
}

async function viewImageAnalysis(id) {
  try {
    const res = await fetch(`${API_BASE}/image-analyses/${id}`, {
      headers: getAuthHeaders()
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Không tải được chi tiết ảnh.');
    document.getElementById('violationReportTitle').textContent =
      `Phân Tích Ảnh #${data.id} — ${data.file_name}`;
    document.getElementById('violationReportBody').innerHTML = `
      <p><strong>Thời gian:</strong> ${escapeHtml(formatTime(data.created_at))}</p>
      <p><strong>Vạch dừng:</strong> ${data.stop_line_enabled ? 'Bật' : 'Tắt'} — ${Math.round(data.stop_line_ratio * 100)}%, góc ${Number(data.stop_line_angle || 0)}°</p>
      <div class="report-evidence-grid">
        <figure><img src="${escapeHtml(data.original_url)}" alt="Ảnh gốc" /><figcaption>Ảnh gốc</figcaption></figure>
        <figure><img src="${escapeHtml(data.annotated_url)}" alt="Ảnh đã phân tích" /><figcaption>Ảnh kết quả có đánh dấu</figcaption></figure>
      </div>
      <h4>Vi phạm ghi nhận (${data.violations.length})</h4>
      ${data.violations.length ? `<ul>${data.violations.map(item =>
        `<li>${escapeHtml(violationLabel(item.violation_type))}: ${escapeHtml(item.label)} — ${(Number(item.confidence) * 100).toFixed(1)}%</li>`
      ).join('')}</ul>` : '<p>Không phát hiện vi phạm trong ảnh.</p>'}`;
    document.getElementById('violationReportModal').style.display = 'flex';
  } catch (err) {
    showToast(err.message, 'error');
  }
}

// ── Violations Page & CSGT Review ─────────────────────────────────
async function loadViolations() {
  const tbody        = document.getElementById('violationsBody');
  const filterVal    = document.getElementById('filterType').value;
  const filterReview = document.getElementById('filterReview').value;
  tbody.innerHTML    = `<tr><td colspan="8" class="loading-row"><div class="spinner"></div> Đang tải...</td></tr>`;
  loadRecentImageAnalyses('violationRecentAnalysesBody', 4);

  try {
    const params = new URLSearchParams({
      limit: String(TABLE_PAGE_SIZE),
      offset: String(violationPageOffset),
      type: filterVal,
      review_status: filterReview,
      search: document.getElementById('violationSearch').value.trim()
    });
    const res = await fetch(`${API_BASE}/violations?${params}`, { headers: getAuthHeaders() });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Lỗi tải danh sách vi phạm.');
    const violations = data.items || [];
    const total = data.total || 0;
    document.getElementById('violationsPageInfo').textContent =
      total ? `${violationPageOffset + 1}-${Math.min(violationPageOffset + violations.length, total)} / ${total}` : '0 vi phạm';

    if (violations.length === 0) {
      const hasFilters = Boolean(filterVal || filterReview || document.getElementById('violationSearch').value.trim());
      const emptyMessage = hasFilters
        ? 'Không có hồ sơ vi phạm phù hợp với bộ lọc.'
        : 'Chưa có hồ sơ vi phạm được ghi nhận. Các lượt phân tích ảnh không phát hiện vi phạm được liệt kê bên dưới, không tạo thành hồ sơ xử lý.';
      tbody.innerHTML = `<tr><td colspan="8" class="loading-row">${emptyMessage}</td></tr>`;
      return;
    }

    const isAdmin = currentUser && currentUser.role === 'admin';

    tbody.innerHTML = violations.map(v => {
      const plate = v.license_plate || 'Chưa rõ';
      const notes = v.notes ? `<div class="note-text">📝 ${escapeHtml(v.notes)}</div>` : '';
      const reviewer = v.reviewed_by ? `<small class="reviewer-tag">Bởi: ${escapeHtml(v.reviewed_by)}</small>` : '';

      let actionsHtml = '';
      if (isAdmin) {
        actionsHtml = `
          <div class="action-btn-group">
            <button class="btn-outline btn-sm" onclick="openViolationReport(${v.id})">Báo cáo</button>
            <button class="btn-action btn-approve" onclick="reviewViolation(${v.id}, 'approved')" title="Duyệt phạt nguội">✔ Phạt</button>
            <button class="btn-action btn-reject" onclick="reviewViolation(${v.id}, 'rejected')" title="Bác bỏ vi phạm">✖ Hủy</button>
            <button class="btn-action btn-edit" onclick="openEditModal(${v.id}, this)" data-plate="${escapeHtml(plate)}" data-notes="${escapeHtml(v.notes || '')}" title="Sửa biển số / ghi chú">✏️</button>
            <button class="btn-action btn-del" onclick="deleteViolation(${v.id})" title="Xóa">🗑️</button>
          </div>
        `;
      } else {
        actionsHtml = `
          <div class="action-btn-group">
            <button class="btn-outline btn-sm" onclick="openViolationReport(${v.id})">Báo cáo</button>
            <span class="viewer-only-tag">👁️ Quyền xem</span>
          </div>`;
      }

      return `
        <tr>
          <td>#${v.id}</td>
          <td>${v.video_id ? `Video #${v.video_id}` : v.image_analysis_id ? `Ảnh #${v.image_analysis_id}` : 'Trực tiếp'}</td>
          <td><span class="plate-badge">${escapeHtml(plate)}</span></td>
          <td><span class="badge ${violationClass(v.violation_type)}">${violationLabel(v.violation_type)}</span></td>
          <td>
            <div>${escapeHtml(v.label || '—')}</div>
            ${notes}
            ${reviewer}
          </td>
          <td>${v.confidence ? (v.confidence * 100).toFixed(1) + '%' : '—'}</td>
          <td><span class="badge ${reviewClass(v.review_status)}">${reviewLabel(v.review_status)}</span></td>
          <td>${actionsHtml}</td>
        </tr>`;
    }).join('');
  } catch (err) {
    showToast(err.message || 'Lỗi tải danh sách vi phạm', 'error');
  }
}

async function openViolationReport(id) {
  try {
    const res = await fetch(`${API_BASE}/violations/${id}/report`, {
      headers: getAuthHeaders()
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Không tải được báo cáo vi phạm.');
    const v = data.violation;
    const evidence = data.evidence || {};
    document.getElementById('violationReportTitle').textContent = `Báo Cáo Hồ Sơ Vi Phạm #${v.id}`;
    document.getElementById('violationReportBody').innerHTML = `
      <div class="report-fields">
        <p><strong>Loại vi phạm:</strong> ${escapeHtml(violationLabel(v.violation_type))}</p>
        <p><strong>Mô tả:</strong> ${escapeHtml(v.label || '—')}</p>
        <p><strong>Biển số:</strong> ${escapeHtml(v.license_plate || 'Chưa rõ')}</p>
        <p><strong>Độ tin cậy:</strong> ${(Number(v.confidence) * 100).toFixed(1)}%</p>
        <p><strong>Trạng thái:</strong> ${escapeHtml(reviewLabel(v.review_status))}</p>
        <p><strong>Nguồn:</strong> ${escapeHtml(v.source_file_name || (v.video_id ? `Video #${v.video_id}` : `Ảnh #${v.image_analysis_id}`))}</p>
        ${v.video_id ? `<p><strong>Khung hình:</strong> ${v.first_frame ?? v.frame_number ?? '—'}${v.last_frame && v.last_frame !== v.first_frame ? `–${v.last_frame}` : ''} (${v.occurrence_count || 1} lần quan sát gộp)</p>` : ''}
        <p><strong>Thời gian:</strong> ${escapeHtml(formatTime(v.created_at))}</p>
        <p><strong>Người duyệt:</strong> ${escapeHtml(v.reviewed_by || 'Chưa duyệt')}</p>
        <p><strong>Ghi chú:</strong> ${escapeHtml(v.notes || '—')}</p>
      </div>
      ${evidence.image_url ? `<div class="report-evidence-grid">${evidence.original_image_url ? `<figure><img src="${escapeHtml(evidence.original_image_url)}" alt="Ảnh gốc" /><figcaption>Ảnh gốc</figcaption></figure>` : ''}<figure><img src="${escapeHtml(evidence.image_url)}" alt="Bằng chứng đã đánh dấu" /><figcaption>Bằng chứng đã đánh dấu</figcaption></figure></div>` : ''}
      ${evidence.video_url ? `<div class="report-video"><video controls preload="metadata" src="${escapeHtml(evidence.video_url)}"></video><p>Video bằng chứng${v.first_frame !== null && v.first_frame !== undefined ? ` — khung hình ${v.first_frame}` : ''}</p></div>` : ''}
      ${!evidence.image_url && !evidence.video_url ? '<p>Hồ sơ chưa có tệp bằng chứng.</p>' : ''}`;
    document.getElementById('violationReportModal').style.display = 'flex';
  } catch (err) {
    showToast(err.message, 'error');
  }
}

function closeViolationReport() {
  const modal = document.getElementById('violationReportModal');
  modal.style.display = 'none';
  modal.querySelectorAll('video').forEach(video => video.pause());
  document.getElementById('violationReportBody').innerHTML = '';
}

document.addEventListener('keydown', event => {
  const modal = document.getElementById('violationReportModal');
  if (event.key === 'Escape' && modal?.style.display === 'flex') {
    closeViolationReport();
  }
});

function changeVideoPage(direction) {
  videoPageOffset = Math.max(0, videoPageOffset + direction * TABLE_PAGE_SIZE);
  loadVideos();
}

function changeViolationPage(direction) {
  violationPageOffset = Math.max(0, violationPageOffset + direction * TABLE_PAGE_SIZE);
  loadViolations();
}

async function loadAuditLogs() {
  const tbody = document.getElementById('auditLogsBody');
  tbody.innerHTML = '<tr><td colspan="5" class="loading-row">Đang tải nhật ký...</td></tr>';
  const params = new URLSearchParams({
    limit: String(TABLE_PAGE_SIZE),
    offset: String(auditPageOffset),
    search: document.getElementById('auditSearch').value.trim()
  });
  const startDate = document.getElementById('auditStartDate').value;
  const endDate = document.getElementById('auditEndDate').value;
  if (startDate) params.set('start_date', startDate);
  if (endDate) params.set('end_date', endDate);
  try {
    const res = await fetch(`${API_BASE}/api/audit-logs?${params}`, {
      headers: getAuthHeaders()
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Không tải được audit log.');
    const items = data.items || [];
    const total = data.total || 0;
    document.getElementById('auditPageInfo').textContent =
      total ? `${auditPageOffset + 1}-${Math.min(auditPageOffset + items.length, total)} / ${total}` : '0 sự kiện';
    tbody.innerHTML = items.length ? items.map(item => `
      <tr>
        <td>${formatTime(item.created_at)}</td>
        <td>${escapeHtml(item.actor_email || item.actor_id || 'Hệ thống')}</td>
        <td><span class="badge badge-blue">${escapeHtml(auditActionLabel(item.action))}</span></td>
        <td>${escapeHtml(auditResourceLabel(item.resource_type))}${item.resource_id ? ` #${escapeHtml(item.resource_id)}` : ''}</td>
        <td><div class="audit-details">${formatAuditDetails(item.details || {})}</div></td>
      </tr>`).join('') : '<tr><td colspan="5" class="loading-row">Chưa có nhật ký phù hợp</td></tr>';
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="5" class="loading-row">${escapeHtml(err.message)}</td></tr>`;
    showToast(err.message, 'error');
  }
}

const AUDIT_ACTION_LABELS = {
  admin_created: 'Tạo tài khoản quản trị viên',
  camera_config_updated: 'Cập nhật cấu hình camera',
  image_analysis_completed: 'Hoàn tất phân tích ảnh',
  video_queued: 'Đưa video vào hàng đợi',
  video_requeued: 'Đưa video xử lý lại',
  video_deleted: 'Xóa video',
  violation_reviewed: 'Cập nhật duyệt vi phạm',
  violation_deleted: 'Xóa hồ sơ vi phạm',
};

const AUDIT_RESOURCE_LABELS = {
  user: 'Tài khoản',
  camera: 'Camera',
  image_analysis: 'Lần phân tích ảnh',
  video: 'Video',
  violation: 'Hồ sơ vi phạm',
};

const AUDIT_DETAIL_LABELS = {
  email: 'Email',
  file_name: 'Tên ảnh',
  filename: 'Tên tệp',
  violations: 'Số vi phạm phát hiện',
  enabled: 'Kiểm tra vạch dừng',
  stop_line_ratio: 'Vị trí vạch dừng',
  camera_id: 'Mã camera',
  status: 'Trạng thái',
  review_status: 'Trạng thái duyệt',
  license_plate: 'Biển số',
};

function auditActionLabel(action) {
  return AUDIT_ACTION_LABELS[action] || String(action || 'Hoạt động khác').replaceAll('_', ' ');
}

function auditResourceLabel(resourceType) {
  return AUDIT_RESOURCE_LABELS[resourceType] || String(resourceType || 'Đối tượng');
}

function auditDetailValue(key, value) {
  if (key === 'enabled') return value ? 'Bật' : 'Tắt';
  if (key === 'stop_line_ratio' && Number.isFinite(Number(value))) {
    return `${Math.round(Number(value) * 100)}%`;
  }
  if (key === 'review_status') return reviewLabel(value);
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function formatAuditDetails(details) {
  const entries = Object.entries(details || {});
  if (!entries.length) return '<span class="audit-detail-empty">Không có thông tin bổ sung</span>';
  return `<ul>${entries.map(([key, value]) => `
    <li><strong>${escapeHtml(AUDIT_DETAIL_LABELS[key] || key.replaceAll('_', ' '))}:</strong> ${escapeHtml(auditDetailValue(key, value))}</li>`
  ).join('')}</ul>`;
}

function changeAuditPage(direction) {
  auditPageOffset = Math.max(0, auditPageOffset + direction * TABLE_PAGE_SIZE);
  loadAuditLogs();
}

function resetAuditPage() {
  auditPageOffset = 0;
  loadAuditLogs();
}

function debouncedLoadAuditLogs() {
  clearTimeout(auditSearchTimer);
  auditSearchTimer = setTimeout(resetAuditPage, 250);
}

function resetVideoPage() {
  videoPageOffset = 0;
  loadVideos();
}

function resetViolationPage() {
  violationPageOffset = 0;
  loadViolations();
}

function debouncedLoadVideos() {
  clearTimeout(videoSearchTimer);
  videoSearchTimer = setTimeout(resetVideoPage, 250);
}

function debouncedLoadViolations() {
  clearTimeout(violationSearchTimer);
  violationSearchTimer = setTimeout(resetViolationPage, 250);
}

async function exportViolationsCsv() {
  const params = new URLSearchParams({
    type: document.getElementById('filterType').value,
    review_status: document.getElementById('filterReview').value
  });
  try {
    const res = await fetch(`${API_BASE}/violations/export.csv?${params}`, {
      headers: getAuthHeaders()
    });
    if (!res.ok) {
      const data = await res.json();
      throw new Error(data.error || 'Không xuất được báo cáo.');
    }
    const url = URL.createObjectURL(await res.blob());
    const link = document.createElement('a');
    link.href = url;
    link.download = 'traffic-violations.csv';
    link.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    showToast(err.message, 'error');
  }
}

async function reviewViolation(id, status) {
  try {
    const res = await fetch(`${API_BASE}/violations/${id}`, {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
        ...getAuthHeaders()
      },
      body: JSON.stringify({ review_status: status })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Lỗi khi cập nhật trạng thái');

    const statusText = status === 'approved' ? 'Đã duyệt phạt nguội' : 'Đã bác bỏ';
    showToast(`Hồ sơ #${id}: ${statusText}`, 'success');
    loadViolations();
    loadDashboard();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

async function deleteViolation(id) {
  if (!confirm(`Bạn có chắc chắn muốn xóa vĩnh viễn vi phạm #${id}?`)) return;
  try {
    const res = await fetch(`${API_BASE}/violations/${id}`, {
      method: 'DELETE',
      headers: getAuthHeaders()
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Lỗi khi xóa vi phạm');
    showToast(`Đã xóa vi phạm #${id}`, 'success');
    loadViolations();
    loadDashboard();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

function openEditModal(id, button) {
  const plate = button.dataset.plate;
  const notes = button.dataset.notes;
  editingViolationId = id;
  document.getElementById('editId').textContent = id;
  document.getElementById('editPlate').value = plate === 'Chưa rõ' ? '' : plate;
  document.getElementById('editNotes').value = notes || '';
  document.getElementById('editModal').style.display = 'flex';
}

function closeEditModal() {
  document.getElementById('editModal').style.display = 'none';
  editingViolationId = null;
}

async function saveEditViolation() {
  if (!editingViolationId) return;
  const plate = document.getElementById('editPlate').value.trim();
  const notes = document.getElementById('editNotes').value.trim();

  try {
    const res = await fetch(`${API_BASE}/violations/${editingViolationId}`, {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
        ...getAuthHeaders()
      },
      body: JSON.stringify({ license_plate: plate, notes: notes })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Lỗi khi cập nhật hồ sơ');

    showToast(`Cập nhật hồ sơ #${editingViolationId} thành công!`, 'success');
    closeEditModal();
    loadViolations();
    loadDashboard();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

// ── Stop Line UI Guide ────────────────────────────────────────────
function updateStopLine(val) {
  document.getElementById('stopLineVal').textContent = val + '%';
  syncStopLineGuide();
}

function updateStopLineAngle(val) {
  document.getElementById('stopLineAngleVal').textContent = `${val}°`;
  syncStopLineGuide();
}

function stopLinePreviewGeometry() {
  const guide = document.getElementById('stopLineGuide');
  const wrap = document.querySelector('.preview-media-wrap');
  if (!guide || !wrap) return null;
  const media = document.getElementById('previewVideo').style.display !== 'none'
    ? document.getElementById('previewVideo')
    : document.getElementById('previewImage');
  const sourceWidth = media.videoWidth || media.naturalWidth;
  const sourceHeight = media.videoHeight || media.naturalHeight;
  const width = wrap.clientWidth;
  const height = wrap.clientHeight;
  if (!width || !height || !sourceWidth || !sourceHeight) {
    return { left: 0, top: 0, width, height };
  }
  const aspect = sourceWidth / sourceHeight;
  const contentWidth = Math.min(width, height * aspect);
  const contentHeight = contentWidth / aspect;
  return {
    left: (width - contentWidth) / 2,
    top: (height - contentHeight) / 2,
    width: contentWidth,
    height: contentHeight
  };
}

function syncStopLineGuide() {
  const guide = document.getElementById('stopLineGuide');
  const geometry = stopLinePreviewGeometry();
  if (!guide || !geometry) return;
  const ratio = Number(document.getElementById('stopLineRange').value) / 100;
  const angle = Number(document.getElementById('stopLineAngleRange').value);
  guide.style.left = `${geometry.left}px`;
  guide.style.right = 'auto';
  guide.style.width = `${geometry.width}px`;
  guide.style.top = `${geometry.top + geometry.height * ratio}px`;
  guide.style.setProperty('--stop-line-angle', `${angle}deg`);
  guide.style.transform = `translateY(-50%) rotate(${angle}deg)`;
}

function setStopLineFromPointer(event) {
  const wrap = document.querySelector('.preview-media-wrap');
  if (!wrap || !wrap.clientHeight) return;
  const bounds = wrap.getBoundingClientRect();
  const geometry = stopLinePreviewGeometry();
  if (!geometry || !geometry.height) return;
  const angle = Number(document.getElementById('stopLineAngleRange').value);
  const x = Math.max(0, Math.min(geometry.width, event.clientX - bounds.left - geometry.left));
  const centerY = event.clientY - bounds.top - geometry.top -
    (x - geometry.width / 2) * Math.tan(angle * Math.PI / 180);
  const percent = Math.max(30, Math.min(95, Math.round((centerY / geometry.height) * 100)));
  const range = document.getElementById('stopLineRange');
  range.value = percent;
  updateStopLine(String(percent));
}

function initializeStopLineDrag() {
  const guide = document.getElementById('stopLineGuide');
  if (!guide) return;
  guide.addEventListener('pointerdown', event => {
    event.preventDefault();
    guide.setPointerCapture(event.pointerId);
    setStopLineFromPointer(event);
  });
  guide.addEventListener('pointermove', event => {
    if (guide.hasPointerCapture(event.pointerId)) setStopLineFromPointer(event);
  });
  guide.addEventListener('pointerup', event => {
    if (guide.hasPointerCapture(event.pointerId)) guide.releasePointerCapture(event.pointerId);
  });
  guide.addEventListener('pointercancel', event => {
    if (guide.hasPointerCapture(event.pointerId)) guide.releasePointerCapture(event.pointerId);
  });
  document.getElementById('previewVideo').addEventListener('loadedmetadata', syncStopLineGuide);
  document.getElementById('previewImage').addEventListener('load', syncStopLineGuide);
  window.addEventListener('resize', syncStopLineGuide);
}

async function loadCameraConfig() {
  if (!currentUser) return;
  const cameraId = document.getElementById('cameraId').value.trim();
  if (!cameraId) return;
  try {
    const res = await fetch(`${API_BASE}/api/cameras/${encodeURIComponent(cameraId)}`, {
      headers: getAuthHeaders()
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Không tải được cấu hình camera.');
    document.getElementById('toggleStopLine').checked = Boolean(data.enabled);
    document.getElementById('stopLineRange').value = Math.round(data.stop_line_ratio * 100);
    document.getElementById('stopLineAngleRange').value = Number(data.stop_line_angle) || 0;
    updateStopLine(document.getElementById('stopLineRange').value);
    updateStopLineAngle(document.getElementById('stopLineAngleRange').value);
    handleStopLineToggle(Boolean(data.enabled));
  } catch (err) {
    showToast(err.message, 'error');
  }
}

async function saveCameraConfig() {
  if (!currentUser) {
    showToast('Vui lòng đăng nhập để lưu cấu hình camera.', 'warning');
    return;
  }
  const cameraId = document.getElementById('cameraId').value.trim();
  if (!cameraId) {
    showToast('Nhập mã camera trước khi lưu.', 'warning');
    return;
  }
  try {
    const res = await fetch(`${API_BASE}/api/cameras/${encodeURIComponent(cameraId)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      body: JSON.stringify({
        enabled: document.getElementById('toggleStopLine').checked,
        stop_line_ratio: Number(document.getElementById('stopLineRange').value) / 100,
        stop_line_angle: Number(document.getElementById('stopLineAngleRange').value)
      })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Không lưu được cấu hình camera.');
    showToast(`Đã lưu cấu hình camera "${cameraId}".`, 'success');
  } catch (err) {
    showToast(err.message, 'error');
  }
}

function handleStopLineToggle(checked) {
  const group = document.getElementById('stopLineSettingGroup');
  const angleGroup = document.getElementById('stopLineAngleSettingGroup');
  const guide = document.getElementById('stopLineGuide');
  if (group) group.style.display = checked ? 'flex' : 'none';
  if (angleGroup) angleGroup.style.display = checked ? 'flex' : 'none';
  if (guide) guide.style.display = checked ? 'flex' : 'none';
}

// ── Detect Mode ───────────────────────────────────────────────────
function setMode(mode, fromUserClick = false) {
  currentMode = mode;
  if (fromUserClick) {
    clearFile();
  }
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

function onFileSelected(event) {
  const file = event.target.files && event.target.files[0];
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
  const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
  if (file) loadFile(file);
}

function loadFile(file) {
  if (!file) return;

  const fileName = (file.name || '').toLowerCase();
  const fileType = (file.type || '').toLowerCase();

  const isImage = fileType.startsWith('image/') || /\.(jpe?g|png|bmp|webp)$/i.test(fileName);
  const isVideo = fileType.startsWith('video/') || /\.(mp4|avi|mov|mkv)$/i.test(fileName);

  if (!isImage && !isVideo) {
    showToast('Định dạng file không được hỗ trợ. Hãy chọn file ảnh hoặc video.', 'error');
    return;
  }

  // Tự động chuyển mode hiển thị theo loại file
  const newMode = isImage ? 'image' : 'video';
  currentMode = newMode;
  document.getElementById('modeImage').classList.toggle('active', newMode === 'image');
  document.getElementById('modeVideo').classList.toggle('active', newMode === 'video');
  document.getElementById('videoOptions').style.display = newMode === 'video' ? 'flex' : 'none';

  // Lưu file VÀO BIẾN currentFile
  currentFile = file;

  // Hiển thị preview
  document.getElementById('previewArea').style.display = 'block';
  document.getElementById('previewFileName').textContent = `${file.name} (${(file.size / 1024 / 1024).toFixed(2)} MB)`;
  document.getElementById('dropZone').style.display     = 'none';

  // Cập nhật lại vị trí vạch dừng stop-line guide
  const curStopVal = document.getElementById('stopLineRange').value || 72;
  updateStopLine(curStopVal);
  updateStopLineAngle(document.getElementById('stopLineAngleRange').value || 0);

  const url = URL.createObjectURL(file);
  if (isImage) {
    const pImg = document.getElementById('previewImage');
    pImg.src = url;
    pImg.style.display = 'block';
    document.getElementById('previewVideo').style.display = 'none';
  } else {
    const pVid = document.getElementById('previewVideo');
    pVid.src = url;
    pVid.style.display = 'block';
    document.getElementById('previewImage').style.display = 'none';
  }

  const btn = document.getElementById('detectBtn');
  btn.disabled = false;
  btn.innerHTML = `
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
      <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
    </svg>
    Phân tích vi phạm bằng AI`;

  resetResult();
  showToast(`Đã chọn: ${file.name}`, 'info');
}

function clearFile() {
  currentFile = null;
  document.getElementById('fileInput').value           = '';
  document.getElementById('previewArea').style.display = 'none';
  document.getElementById('dropZone').style.display    = 'block';
  document.getElementById('detectBtn').disabled        = true;
  resetResult();
}

function resetResult() {
  releaseResultObjectUrl();
  document.getElementById('resultPlaceholder').style.display = 'flex';
  document.getElementById('progressWrap').style.display      = 'none';
  document.getElementById('resultImage').style.display       = 'none';
  document.getElementById('resultVideo').style.display       = 'none';
  document.getElementById('resultSummary').style.display     = 'none';
  document.getElementById('platesFoundBox').style.display    = 'none';
  document.getElementById('violationsList').style.display    = 'none';
  document.getElementById('resultBadge').style.display       = 'none';
}

// ── Run AI Detect ─────────────────────────────────────────────────
async function runDetect() {
  if (!currentFile) {
    showToast('Vui lòng chọn ảnh hoặc video trước khi bấm phân tích!', 'warning');
    return;
  }

  const btn = document.getElementById('detectBtn');
  btn.disabled = true;
  btn.innerHTML = '<div class="spinner"></div> Đang phân tích AI & ALPR...';

  resetResult();
  document.getElementById('resultPlaceholder').style.display = 'none';
  document.getElementById('progressWrap').style.display      = 'block';

  const fill = document.getElementById('progressFill');
  const txt  = document.getElementById('progressText');
  fill.style.width = '0%';
  txt.textContent = currentMode === 'video'
    ? 'Kiểm tra worker xử lý video...'
    : 'Đang tải ảnh lên máy chủ...';

  try {
    if (currentMode === 'video') {
      await ensureVideoWorkerAvailable();
    }

    const stopRatio = (parseFloat(document.getElementById('stopLineRange').value) || 72) / 100.0;
    let res;
    let data;
    if (currentMode === 'video') {
      const settings = {
        file_name: currentFile.name,
        file_size: currentFile.size,
        frame_skip: Number(document.getElementById('frameSkip').value),
        stop_line_ratio: stopRatio,
        stop_line_angle: Number(document.getElementById('stopLineAngleRange').value),
        enable_stop_line: document.getElementById('toggleStopLine').checked,
        camera_id: document.getElementById('cameraId').value.trim() || 'default'
      };
      txt.textContent = 'Đang chuẩn bị upload resumable...';
      const signedResponse = await fetch(`${API_BASE}/api/videos/upload-url`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify(settings)
      });
      const uploadInfo = await readApiJson(signedResponse, 'Khởi tạo upload video');
      if (!signedResponse.ok) {
        throw new Error(uploadInfo.error || 'Không thể khởi tạo upload video.');
      }

      txt.textContent = 'Đang tải video trực tiếp lên Supabase...';
      await uploadVideoResumable(uploadInfo, currentFile, percent => {
        fill.style.width = `${percent * 0.35}%`;
        txt.textContent = `Đang tải video trực tiếp lên Supabase... ${percent}%`;
      });
      fill.style.width = '35%';
      txt.textContent = 'Video đã tải lên; đang xác minh và xếp hàng xử lý...';
      res = await fetch(`${API_BASE}/api/videos/queue`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({
          ...settings,
          storage_path: uploadInfo.storage_path
        })
      });
      data = await readApiJson(res, 'Đưa video vào hàng đợi');
    } else {
      const formData = new FormData();
      formData.append('stop_line_ratio', stopRatio);
      formData.append('stop_line_angle', document.getElementById('stopLineAngleRange').value);
      formData.append('enable_stop_line', document.getElementById('toggleStopLine').checked ? 'true' : 'false');
      formData.append('camera_id', document.getElementById('cameraId').value.trim() || 'default');
      formData.append('file', currentFile);
      txt.textContent = 'Đang tải ảnh lên và phân tích...';
      res = await fetch(`${API_BASE}/detect/image`, {
        method: 'POST',
        headers: getAuthHeaders(),
        body: formData
      });
      data = await readApiJson(res, 'Phân tích ảnh');
    }
    if (!res.ok) throw new Error(data.error || 'Lỗi phân tích từ server');

    if (currentMode === 'video' && res.status === 202) {
      const videoId = Number(data?.video_id ?? data?.id);
      if (!Number.isSafeInteger(videoId) || videoId <= 0) {
        const fields = data && typeof data === 'object' ? Object.keys(data).join(', ') : 'không phải object';
        throw new Error(
          `Video đã được gửi vào hàng đợi nhưng phản hồi không có ID hợp lệ ` +
          `(các trường nhận được: ${fields || 'không có'}). ` +
          'Hãy kiểm tra video trong mục Videos Giám Sát trước khi tải lại để tránh tạo bản trùng.'
        );
      }
      fill.style.width = '35%';
      txt.textContent = `Đã tải video #${videoId}; đang chờ xử lý...`;
      let completed = false;
      let nextHealthCheck = Date.now() + 15000;
      while (!completed) {
        await new Promise(resolve => setTimeout(resolve, 2500));
        res = await fetch(`${API_BASE}/videos/${videoId}`, { headers: getAuthHeaders() });
        data = await readApiJson(res, 'Đọc trạng thái video');
        if (!res.ok) throw new Error(data.error || 'Không thể đọc trạng thái video.');
        if (data.status === 'error') {
          throw new Error(data.processing_error || 'Worker không xử lý được video.');
        }
        completed = data.status === 'processed';
        const processingProgress = Number(data.processing_progress) || 0;
        fill.style.width = `${35 + processingProgress * 0.65}%`;
        txt.textContent = completed
          ? 'Hoàn tất phân tích!'
          : data.status === 'pending'
            ? `Video #${data.id} đã xếp hàng; chờ worker...`
            : `Đang xử lý video #${data.id}: ${processingProgress}%`;

        if (!completed && Date.now() >= nextHealthCheck) {
          nextHealthCheck = Date.now() + 15000;
          const healthResponse = await fetch(`${API_BASE}/health`, {
            signal: AbortSignal.timeout(8000)
          });
          const health = await readApiJson(healthResponse, 'Kiểm tra worker video');
          if (health.video_worker !== 'ok') {
            throw new Error(
              `Worker video không hoạt động (trạng thái: ${health.video_worker}). ` +
              `Video #${data.id} đã được tải lên, trạng thái hiện tại là "${data.status}". ` +
              'Kiểm tra lại video trong Quản Lý Video sau khi worker được khởi động.'
            );
          }
        }
      }
      const violations = data.violations || [];
      const count = type => violations.filter(v => v.violation_type === type).length;
      data.summary = {
        has_violations: violations.length > 0,
        total_violations: violations.length,
        no_helmet_count: count('no_helmet'),
        overloaded_motorcycle_count: count('overloaded_motorcycle'),
        red_light_count: count('red_light'),
        detected_plates: [...new Set(violations.map(v => v.license_plate).filter(p => p && p !== 'Chưa rõ'))]
      };
    } else {
      fill.style.width = '100%';
      txt.textContent = 'Hoàn tất phân tích!';
    }

    setTimeout(() => showResult(data), 350);
  } catch (e) {
    document.getElementById('progressWrap').style.display      = 'none';
    document.getElementById('resultPlaceholder').style.display = 'flex';
    showToast(`Lỗi: ${e.message}`, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = `
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
        <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
      </svg>
      Phân tích vi phạm bằng AI`;
  }
}

async function ensureVideoWorkerAvailable() {
  const response = await fetch(`${API_BASE}/health`, {
    signal: AbortSignal.timeout(8000)
  });
  const health = await readApiJson(response, 'Kiểm tra worker video');
  if (!response.ok) {
    throw new Error(
      health.error_code === 'SUPABASE_INVALID_API_KEY'
        ? 'Supabase từ chối API key; kiểm tra cấu hình backend trước khi tải video.'
        : 'Không kiểm tra được trạng thái worker video; chưa tải video lên.'
    );
  }
  if (health.video_worker === 'ok') return;

  const messages = {
    stale: 'Worker video không gửi heartbeat gần đây.',
    not_running: 'Worker video chưa được khởi động.',
    schema_missing: 'Supabase thiếu bảng heartbeat worker; cần cập nhật schema.',
    unconfigured: 'Backend chưa cấu hình service-role key cho worker video.',
    error: 'Backend không đọc được trạng thái worker video.'
  };
  throw new Error(
    `${messages[health.video_worker] || 'Worker video chưa sẵn sàng'} ` +
    'Video chưa được tải lên. Hãy khởi động backend\\process_pending_videos.py rồi thử lại.'
  );
}

function tusRequest(method, url, headers, body = null, onProgress = null, timeout = 120000) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open(method, url);
    xhr.timeout = timeout;
    Object.entries(headers).forEach(([name, value]) => {
      xhr.setRequestHeader(name, value);
    });
    xhr.addEventListener('load', () => {
      resolve({
        status: xhr.status,
        location: xhr.getResponseHeader('Location'),
        offset: xhr.getResponseHeader('Upload-Offset'),
        body: xhr.responseText
      });
    });
    xhr.addEventListener('error', () => reject(new Error('Mất kết nối khi tải video lên Supabase.')));
    xhr.addEventListener('abort', () => reject(new Error('Đã hủy tải video lên Supabase.')));
    xhr.addEventListener('timeout', () => reject(new Error('Supabase không phản hồi khi tải video lên.')));
    if (onProgress) {
      xhr.upload.addEventListener('progress', event => {
        if (event.lengthComputable) onProgress(event.loaded);
      });
    }
    xhr.send(body);
  });
}

function tusMetadata(name, value) {
  return `${name} ${btoa(value)}`;
}

function tusFailureMessage(response, file, stage) {
  if (response.status === 413) {
    const sizeMb = (file.size / 1024 / 1024).toFixed(2);
    return `Supabase Storage từ chối video ${sizeMb} MB vì vượt giới hạn kích thước tệp. ` +
      'Trong Supabase Dashboard, tăng File size limit cho bucket "videos" lên ít nhất 200 MB ' +
      'và kiểm tra giới hạn chung của project/gói dịch vụ. Chưa tạo tác vụ phân tích; ' +
      'sau khi đổi giới hạn, hãy tải lại video.';
  }

  let detail = '';
  try {
    const payload = JSON.parse(response.body);
    detail = payload.message || payload.error || '';
  } catch {
    detail = response.body.trim().slice(0, 240);
  }
  return `Supabase lỗi khi ${stage} (HTTP ${response.status})` +
    `${detail ? `: ${detail}` : '.'}`;
}

async function uploadVideoResumable(uploadInfo, file, onProgress) {
  const baseHeaders = {
    'Tus-Resumable': '1.0.0',
    'x-signature': uploadInfo.upload_token
  };
  const metadata = [
    tusMetadata('bucketName', 'videos'),
    tusMetadata('objectName', uploadInfo.storage_path),
    tusMetadata('contentType', uploadInfo.content_type),
    tusMetadata('cacheControl', '3600')
  ].join(',');
  const created = await tusRequest('POST', uploadInfo.tus_endpoint, {
    ...baseHeaders,
    'Upload-Length': String(file.size),
    'Upload-Metadata': metadata
  });
  if (created.status !== 201 || !created.location) {
    throw new Error(tusFailureMessage(created, file, 'khởi tạo upload'));
  }

  const uploadUrl = new URL(created.location, uploadInfo.tus_endpoint).toString();
  const chunkSize = 6 * 1024 * 1024;
  let offset = 0;
  let retries = 0;
  while (offset < file.size) {
    const chunk = file.slice(offset, Math.min(offset + chunkSize, file.size));
    try {
      const response = await tusRequest('PATCH', uploadUrl, {
        ...baseHeaders,
        'Upload-Offset': String(offset),
        'Content-Type': 'application/offset+octet-stream'
      }, chunk, loaded => onProgress(Math.min(
        100,
        Math.floor(((offset + loaded) / file.size) * 100)
      )));
      if (response.status !== 204) {
        const error = new Error(tusFailureMessage(response, file, 'nhận một phần video'));
        error.retryable = response.status === 409 || response.status >= 500;
        throw error;
      }
      offset = Number(response.offset);
      if (!Number.isFinite(offset) || offset <= 0) {
        throw new Error('Supabase trả về vị trí upload không hợp lệ.');
      }
      retries = 0;
    } catch (error) {
      if (error.retryable === false) throw error;
      if (++retries > 4) throw error;
      await new Promise(resolve => setTimeout(resolve, retries * 1500));
      const head = await tusRequest('HEAD', uploadUrl, baseHeaders);
      if (head.status !== 200) {
        throw new Error(`Không thể tiếp tục upload resumable (HTTP ${head.status}).`);
      }
      const serverOffset = Number(head.offset);
      if (!Number.isFinite(serverOffset) || serverOffset < 0 || serverOffset > file.size) {
        throw new Error('Supabase trả về vị trí tiếp tục upload không hợp lệ.');
      }
      offset = serverOffset;
      onProgress(Math.floor((offset / file.size) * 100));
    }
  }
  onProgress(100);
}

function releaseResultObjectUrl() {
  if (resultObjectUrl) URL.revokeObjectURL(resultObjectUrl);
  resultObjectUrl = null;
}

async function showResult(data) {
  document.getElementById('progressWrap').style.display      = 'none';
  document.getElementById('resultPlaceholder').style.display = 'none';

  const image = document.getElementById('resultImage');
  const video = document.getElementById('resultVideo');
  image.onerror = null;
  video.onerror = null;
  image.style.display = 'none';
  image.removeAttribute('src');
  video.pause();
  video.style.display = 'none';
  video.removeAttribute('src');
  video.load();
  releaseResultObjectUrl();

  const summary       = data.summary || {};
  const hasViolations = summary.has_violations;
  const violations    = data.violations || [];

  // Annotated media
  if (data.annotated_url) {
    const url = /^https?:\/\//i.test(data.annotated_url)
      ? data.annotated_url
      : API_BASE + data.annotated_url;
    try {
      let mediaUrl = url;
      if (!/^https?:\/\//i.test(data.annotated_url)) {
        const res = await fetch(url, { headers: getAuthHeaders() });
        if (!res.ok) throw new Error(`Không tải được tệp kết quả (HTTP ${res.status}).`);
        resultObjectUrl = URL.createObjectURL(await res.blob());
        mediaUrl = resultObjectUrl;
      }
      if (currentMode === 'image') {
        image.onerror = () => {
          image.style.display = 'none';
          showToast('Không tải được ảnh kết quả. Hãy mở lại bằng chứng từ Lịch sử phân tích ảnh.', 'error');
        };
        image.src = mediaUrl;
        image.style.display = 'block';
      } else {
        video.onerror = () => {
          showToast(
            'Trình duyệt không thể phát video kết quả. Hãy tải lại trang; nếu vẫn lỗi, kiểm tra video đã được mã hóa H.264.',
            'error',
            7000
          );
        };
        video.src = mediaUrl;
        video.style.display = 'block';
      }
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  // Badge kết quả
  const badge = document.getElementById('resultBadge');
  badge.style.display = 'inline-flex';
  if (hasViolations) {
    badge.className  = 'badge badge-red';
    badge.textContent = `${summary.total_violations} vi phạm`;
  } else {
    badge.className  = 'badge badge-green';
    badge.textContent = 'Hợp lệ – Không vi phạm';
  }

  // Summary numbers
  document.getElementById('sumTotal').textContent    = summary.total_violations ?? 0;
  document.getElementById('sumHelmet').textContent   = summary.no_helmet_count ?? 0;
  document.getElementById('sumTriple').textContent   = summary.overloaded_motorcycle_count ?? 0;
  document.getElementById('sumRedLight').textContent = summary.red_light_count ?? 0;
  document.getElementById('resultSummary').style.display = 'grid';

  // Biển số xe tìm thấy
  const plates = summary.detected_plates || [];
  const box = document.getElementById('platesFoundBox');
  const list = document.getElementById('detectedPlatesList');
  if (plates.length > 0) {
    box.style.display = 'block';
    list.innerHTML = plates.map(p => `<span class="plate-badge">${p}</span>`).join('');
  } else {
    box.style.display = 'none';
  }

  // Violations list
  if (violations.length > 0) {
    document.getElementById('violationsList').style.display = 'block';
    document.getElementById('violationsListBody').innerHTML = violations.map(v => {
      let icon = '⛑️';
      if (v.type === 'overloaded_motorcycle') icon = '🏍️';
      if (v.type === 'red_light') icon = '🚦';

      const plateBadge = v.license_plate && v.license_plate !== 'Chưa rõ'
        ? `<span class="plate-badge">${v.license_plate}</span>`
        : '';

      return `
        <div class="violation-item">
          <div class="v-icon ${v.type}">${icon}</div>
          <div class="v-info">
            <div class="v-label">${v.label} ${plateBadge}</div>
            <div class="v-meta">
              ${v.people_count ? `${v.people_count} người · ` : ''}
              ${v.frame !== undefined ? `Frame ${v.frame} · ` : ''}
              Loại: ${violationLabel(v.type)}
            </div>
          </div>
          <span class="v-conf">${v.conf ? (v.conf * 100).toFixed(1) + '%' : ''}</span>
        </div>`;
    }).join('');
  } else {
    document.getElementById('violationsList').style.display = 'none';
  }

  if (hasViolations) {
    showToast(`Phát hiện ${summary.total_violations} lỗi vi phạm giao thông!`, 'error');
  } else {
    showToast('Tất cả phương tiện đều chấp hành tốt luật giao thông', 'success');
  }

  // Cuộn nhẹ xuống vùng kết quả để người dùng thấy rõ
  const resCard = document.querySelector('.result-card');
  if (resCard) {
    resCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
}

// ── UI Format Helpers ─────────────────────────────────────────────
function violationLabel(type) {
  return {
    no_helmet:             'Không MBH',
    overloaded_motorcycle: 'Chở quá số người',
    red_light:             'Vượt đèn đỏ'
  }[type] || type;
}

function violationClass(type) {
  return {
    no_helmet:             'vtype-no_helmet',
    overloaded_motorcycle: 'vtype-overloaded_motorcycle',
    red_light:             'vtype-red_light'
  }[type] || 'badge-gray';
}

function reviewLabel(status) {
  return {
    approved: 'Đã duyệt (Phạt nguội)',
    rejected: 'Bác bỏ (Không phạt)',
    pending:  'Chờ CSGT duyệt'
  }[status] || 'Chờ duyệt';
}

function reviewClass(status) {
  return {
    approved: 'badge-green',
    rejected: 'badge-gray',
    pending:  'badge-orange'
  }[status] || 'badge-orange';
}

function statusLabel(s) {
  return {
    pending:    'Chờ xử lý',
    processing: 'Đang xử lý',
    processed:  'Đã xử lý',
    error:      'Lỗi'
  }[s] || s;
}

function statusClass(s) {
  return {
    pending:    'badge-blue',
    processing: 'badge-orange',
    processed:  'badge-green',
    error:      'badge-red'
  }[s] || 'badge-gray';
}

function formatTime(t) {
  if (!t) return '—';
  try {
    return new Date(t).toLocaleString('vi-VN', { dateStyle: 'short', timeStyle: 'short' });
  } catch { return t; }
}

// ── Khởi động ứng dụng ────────────────────────────────────────────
initializeTrendDates();
initializeStopLineDrag();
initAuth();
