// dashboard.js — MindFlow Dashboard
const API = 'http://localhost:3000';

// ── AUTH ──────────────────────────────────────────────
const user = JSON.parse(localStorage.getItem('user') || 'null');
if (!user) { window.location.href = 'login.html'; }
else {
  const name = user.name || user.full_name || 'User';
  document.getElementById('sidebarName').textContent = name;
  document.getElementById('sidebarAvatar').textContent = name.charAt(0).toUpperCase();
}

const h = new Date().getHours();
document.getElementById('timeGreet').textContent = h < 12 ? 'morning' : h < 17 ? 'afternoon' : 'evening';
document.getElementById('todayDate').textContent = new Date().toLocaleDateString('en-IN',{weekday:'long',day:'numeric',month:'long'});

document.getElementById('logoutBtn').addEventListener('click', () => {
  localStorage.removeItem('user'); localStorage.removeItem('email');
  window.location.href = 'login.html';
});

// ── PANEL NAVIGATION ──────────────────────────────────
function showPanel(name) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.getElementById('panel-' + name)?.classList.add('active');
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  const match = [...document.querySelectorAll('.nav-item')].find(n => n.getAttribute('onclick')?.includes(`'${name}'`));
  if (match) match.classList.add('active');
  const fab = document.querySelector('.fab');
  if (fab) fab.style.display = name === 'schedule' ? 'none' : 'flex';

  if (name === 'calendar')  renderCalendar();
  if (name === 'schedule')  { if (currentCategory) { renderScheduleTable(lastAiOrder); } else { renderCategoryGrid(); } }
  if (name === 'analytics') { loadBehaviorInsights(); updateSmartScheduling(); }
  if (name === 'history')   loadRecentActivity();
}
window.showPanel = showPanel;

// ── ANALYTICS TAB SWITCHING ───────────────────────────
function switchAnalyticsTab(tab, btn) {
  ['behavior','scheduling','progress'].forEach(t => {
    document.getElementById('apanel-'+t).style.display = t === tab ? 'block' : 'none';
    document.getElementById('atab-'+t)?.classList.toggle('active', t === tab);
  });
  if (tab === 'progress') renderChart();
  if (tab === 'behavior') loadBehaviorInsights();
  if (tab === 'scheduling') updateSmartScheduling();
}
window.switchAnalyticsTab = switchAnalyticsTab;

// ── STRESS STATE ──────────────────────────────────────
let currentStressLevel = 40;
let detectionSource    = 'slider';
let lastFaceEmotion    = null;
let lastVoiceEmotion   = null;
let lastVideoUrl       = null;
let cameraStream       = null;
let combinedVoiceBlob  = null;
let combinedVideoBlob  = null;
let combinedMicStream  = null;
let combinedMediaRecorder = null;
let voiceAnimInterval  = null;
let deferredIds        = [];
let lastAiOrder        = null;
let taskFilter         = 'all';
let currentCategory    = null;

const DEFAULT_CATEGORIES = [
  { id:'habit',        label:'Quitting Bad Habit', icon:'🚭', color:'var(--red-dim)',    border:'var(--red)' },
  { id:'art',          label:'Art',                icon:'🎨', color:'var(--primary-dim)',border:'var(--primary)' },
  { id:'self-love',    label:'Self Love',          icon:'💖', color:'rgba(236,72,153,.12)',border:'#ec4899' },
  { id:'meditation',   label:'Meditation',         icon:'🧘', color:'var(--accent-dim)', border:'var(--accent)' },
  { id:'study',        label:'Study',              icon:'📚', color:'var(--primary-dim)',border:'var(--primary)' },
  { id:'sports',       label:'Sports',             icon:'⚽', color:'var(--green-dim)',  border:'var(--green)' },
  { id:'entertainment',label:'Entertainment',      icon:'🎮', color:'var(--yellow-dim)', border:'var(--yellow)' },
  { id:'social',       label:'Social',             icon:'👥', color:'var(--accent-dim)', border:'var(--accent)' },
  { id:'finance',      label:'Finance',            icon:'💰', color:'var(--green-dim)',  border:'var(--green)' },
  { id:'spirituality', label:'Spirituality',       icon:'✨', color:'rgba(167,139,250,.15)',border:'#a78bfa' },
  { id:'health',       label:'Health',             icon:'❤️', color:'var(--red-dim)',    border:'var(--red)' },
  { id:'work',         label:'Work',               icon:'💼', color:'var(--primary-dim)',border:'var(--primary)' },
  { id:'nutrition',    label:'Nutrition',          icon:'🥗', color:'var(--green-dim)',  border:'var(--green)' },
  { id:'home',         label:'Home',               icon:'🏠', color:'var(--yellow-dim)', border:'var(--yellow)' },
  { id:'outdoor',      label:'Outdoor',            icon:'🌿', color:'var(--green-dim)',  border:'var(--green)' },
  { id:'other',        label:'Other',              icon:'➕', color:'var(--card2)',       border:'var(--border2)' },
];

const CATEGORY_COLOR_POOL = [
  { border: '#38bdf8', color: 'rgba(56,189,248,0.12)' },   // Sky
  { border: '#818cf8', color: 'rgba(129,140,248,0.12)' },  // Indigo
  { border: '#f472b6', color: 'rgba(244,114,182,0.12)' },  // Pink
  { border: '#fb923c', color: 'rgba(251,146,60,0.12)' },   // Orange
  { border: '#2dd4bf', color: 'rgba(45,212,191,0.12)' },   // Teal
  { border: '#a78bfa', color: 'rgba(167,139,250,0.12)' },  // Violet
  { border: '#fb7185', color: 'rgba(251,113,133,0.12)' },  // Rose
  { border: '#4ade80', color: 'rgba(74,222,128,0.12)' },   // Emerald
];

const CATEGORY_ICON_POOL = ['📋', '🔖', '🏷️', '🌟', '🎯', '💡', '🚀', '🌈'];

let customCategories = []; // Will be loaded from DB

function getAllCategories() {
  const defaults = [...DEFAULT_CATEGORIES];
  const other = defaults.pop(); // Remove 'other'
  
  // Use a Map to ensure unique IDs, prioritizing custom overrides
  const catMap = new Map();
  
  // 1. Add defaults
  defaults.forEach(c => catMap.set(c.id, c));
  
  // 2. Add custom overrides/new categories from DB
  customCategories.forEach(c => {
    // If it's a rename of a default, it replaces the default in the map
    catMap.set(c.cat_id || c.id, {
      ...catMap.get(c.cat_id || c.id), // Keep original icon/colors if not in custom
      ...c,
      id: c.cat_id || c.id // ensure id is correct
    });
  });
  
  const result = Array.from(catMap.values());
  result.push(other); // Always keep 'Other' at the end
  return result;
}

async function loadCategories() {
  const email = user?.email || localStorage.getItem('email') || '';
  try {
    const res = await fetch(`${API}/get-categories?email=${encodeURIComponent(email)}`);
    const data = await res.json();
    customCategories = data.categories || [];
    
    // Migration from localStorage if needed
    const local = JSON.parse(localStorage.getItem('mf_custom_cats') || '[]');
    if (local.length > 0) {
      console.log(`[Persistence] Migrating ${local.length} local categories to DB...`);
      for (const c of local) {
        await fetch(`${API}/save-category`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ userEmail: email, catId: c.id, label: c.label, icon: c.icon, color: c.color, border: c.border })
        });
      }
      localStorage.removeItem('mf_custom_cats');
      // Reload after migration
      const reload = await fetch(`${API}/get-categories?email=${encodeURIComponent(email)}`);
      customCategories = (await reload.json()).categories || [];
    }
  } catch (err) { console.error('[Persistence] Error loading categories:', err); }
  renderCategoryOptions();
  renderCategoryGrid();
}

function renderCategoryOptions() {
  const sel = document.getElementById('taskCategory');
  if (!sel) return;
  const cats = getAllCategories();
  sel.innerHTML = cats.map(c => `<option value="${c.id}">${c.label}</option>`).join('');
}

function getCatById(id) {
  return getAllCategories().find(c => c.id === id) || { id, label: id, icon: '📋', color:'var(--card2)', border:'var(--border2)' };
}

const STRESS_LABELS = [
  '',
  'Very Calm','Very Calm','Very Calm','Very Calm','Very Calm','Very Calm','Very Calm','Very Calm','Very Calm','Very Calm',
  'Calm','Calm','Calm','Calm','Calm','Calm','Calm','Calm','Calm','Calm',
  'Relaxed','Relaxed','Relaxed','Relaxed','Relaxed','Relaxed','Relaxed','Relaxed','Relaxed','Relaxed',
  'Neutral','Neutral','Neutral','Neutral','Neutral','Neutral','Neutral','Neutral','Neutral','Neutral',
  'Slightly Stressed','Slightly Stressed','Slightly Stressed','Slightly Stressed','Slightly Stressed',
  'Slightly Stressed','Slightly Stressed','Slightly Stressed','Slightly Stressed','Slightly Stressed',
  'Moderately Stressed','Moderately Stressed','Moderately Stressed','Moderately Stressed','Moderately Stressed',
  'Moderately Stressed','Moderately Stressed','Moderately Stressed','Moderately Stressed','Moderately Stressed',
  'Stressed','Stressed','Stressed','Stressed','Stressed','Stressed','Stressed','Stressed','Stressed','Stressed',
  'Very Stressed','Very Stressed','Very Stressed','Very Stressed','Very Stressed',
  'Very Stressed','Very Stressed','Very Stressed','Very Stressed','Very Stressed',
  'Overwhelmed','Overwhelmed','Overwhelmed','Overwhelmed','Overwhelmed',
  'Overwhelmed','Overwhelmed','Overwhelmed','Overwhelmed','Overwhelmed',
  'Burnt Out','Burnt Out','Burnt Out','Burnt Out','Burnt Out',
  'Burnt Out','Burnt Out','Burnt Out','Burnt Out','Burnt Out',
];

// ── DETECT TAB SWITCH ─────────────────────────────────
function switchDetect(type, btn) {
  document.querySelectorAll('.detect-tab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('.detect-panel').forEach(p => p.classList.remove('active'));
  document.getElementById('detect-' + type).classList.add('active');
  if (cameraStream) { cameraStream.getTracks().forEach(t => t.stop()); cameraStream = null; }
}
window.switchDetect = switchDetect;

// ── SLIDER ────────────────────────────────────────────
function updateSlider(val) {
  currentStressLevel = parseInt(val);
  document.getElementById('sliderNum').textContent = val;
  document.getElementById('sliderLbl').textContent = STRESS_LABELS[parseInt(val)] || 'Neutral';
}
window.updateSlider = updateSlider;
updateSlider(40);

// ── COMBINED SCAN ─────────────────────────────────────
async function startCombinedScan() {
  const status = document.getElementById('scanStatus');
  const startBtn = document.getElementById('scanStartBtn');
  const captureBtn = document.getElementById('scanCaptureBtn');
  try {
    cameraStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
    document.getElementById('cameraFeed').srcObject = cameraStream;
    combinedMediaRecorder = new MediaRecorder(cameraStream);
    const chunks = [];
    combinedMediaRecorder.ondataavailable = e => { if (e.data.size > 0) chunks.push(e.data); };
    combinedMediaRecorder.onstop = () => {
      combinedVideoBlob = new Blob(chunks, { type: 'video/webm' });
      combinedVoiceBlob = combinedVideoBlob;
    };
    combinedMediaRecorder.start();
  } catch (err) {
    status.textContent = '⚠️ Camera/Mic access denied. Check browser permissions.';
    return;
  }
  const bars = document.querySelectorAll('.voice-bar');
  voiceAnimInterval = setInterval(() => bars.forEach(b => b.style.height = Math.random() * 34 + 4 + 'px'), 100);
  status.textContent = '🟢 Recording... Speak naturally, then click Capture & Analyse';
  startBtn.disabled = true;
  captureBtn.disabled = false;
  document.getElementById('scanStopBtn').style.display = 'inline-flex';
}
window.startCombinedScan = startCombinedScan;

function stopCombinedScan() {
  if (voiceAnimInterval) { clearInterval(voiceAnimInterval); voiceAnimInterval = null; }
  if (combinedMediaRecorder && combinedMediaRecorder.state !== 'inactive') combinedMediaRecorder.stop();
  if (cameraStream) { cameraStream.getTracks().forEach(t => t.stop()); cameraStream = null; }
  document.querySelectorAll('.voice-bar').forEach(b => b.style.height = '8px');
  document.getElementById('scanStatus').textContent = 'Scan stopped. Click Start Scan to try again.';
  document.getElementById('scanStartBtn').disabled = false;
  document.getElementById('scanCaptureBtn').disabled = true;
  document.getElementById('scanStopBtn').style.display = 'none';
  combinedVoiceBlob = null;
}
window.stopCombinedScan = stopCombinedScan;

async function captureCombined() {
  const status = document.getElementById('scanStatus');
  const captureBtn = document.getElementById('scanCaptureBtn');
  const startBtn = document.getElementById('scanStartBtn');
  captureBtn.disabled = true;
  status.textContent = '🧠 Analysing face & voice...';

  if (combinedMediaRecorder && combinedMediaRecorder.state !== 'inactive') {
    combinedMediaRecorder.stop();
    await new Promise(r => setTimeout(r, 600));
  }
  if (voiceAnimInterval) { clearInterval(voiceAnimInterval); voiceAnimInterval = null; }
  document.querySelectorAll('.voice-bar').forEach(b => b.style.height = '8px');

  try {
    // Upload video
    if (combinedVideoBlob) {
      const vFd = new FormData();
      vFd.append('video', combinedVideoBlob, 'stress_report.webm');
      try {
        const vRes = await fetch(`${API}/upload-video`, { method: 'POST', body: vFd });
        const vData = await vRes.json();
        lastVideoUrl = vData.videoUrl;
      } catch {}
    }

    // AI detection
    const fd = new FormData();
    if (combinedVoiceBlob) fd.append('audio', combinedVoiceBlob, 'voice.wav');
    const video = document.getElementById('cameraFeed');
    if (video && video.readyState >= 2) {
      const canvas = document.createElement('canvas');
      canvas.width = video.videoWidth || 640;
      canvas.height = video.videoHeight || 480;
      canvas.getContext('2d').drawImage(video, 0, 0);
      const blob = await new Promise(r => canvas.toBlob(r, 'image/jpeg', 0.85));
      if (blob) fd.append('frame', blob, 'frame.jpg');
    }

    const res = await fetch(`${API}/detect-combined`, { method: 'POST', body: fd });
    const data = await res.json();
    const combined = data.combined_stress ?? 40;
    currentStressLevel = combined;
    const faceTag = data.face_emotion || 'Neutral';
    const voiceTag = data.voice_emotion || 'Neutral';

    status.innerHTML = `
      <span style="color:var(--primary)">👤 Face: <b>${faceTag}</b> (${data.face_stress ?? '?'}/100)</span> &nbsp;|
      <span style="color:var(--accent)">🎤 Voice: <b>${voiceTag}</b> (${data.voice_stress ?? '?'}/100)</span><br>
      <span style="color:var(--green);font-weight:700;font-size:1.05rem;">→ Combined Stress: ${combined}/100 — ${STRESS_LABELS[Math.min(combined,100)] || ''}</span>
      ${lastVideoUrl ? '<br><small style="color:var(--green)">✓ Video report stored</small>' : ''}
    `;
    updateStressBadge(combined);
    updateStressStats(combined);
    detectionSource = 'face_voice_scan';
    lastFaceEmotion = faceTag;
    lastVoiceEmotion = voiceTag;
    document.getElementById('analyzeBtn').style.boxShadow = '0 0 0 3px rgba(74,222,128,0.4)';
  } catch {
    currentStressLevel = 40;
    status.textContent = 'Could not reach AI bridge — using offline estimate (40/100)';
  }

  if (cameraStream) { cameraStream.getTracks().forEach(t => t.stop()); cameraStream = null; }
  startBtn.disabled = false;
  document.getElementById('scanStopBtn').style.display = 'none';
  combinedVoiceBlob = null;
  combinedVideoBlob = null;
}
window.captureCombined = captureCombined;

// ── MAIN ANALYZE ──────────────────────────────────────
async function analyzeStress() {
  const btn = document.getElementById('analyzeBtn');
  btn.disabled = true;
  btn.style.boxShadow = '';
  btn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> Analysing...';
  const email = user?.email || localStorage.getItem('email') || '';
  const note = document.getElementById('stressNote').value;

  try {
    const res = await fetch(`${API}/analyze`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stressLevel: currentStressLevel, note, userEmail: email })
    });
    const data = await res.json();
    showAiSuggestion(data);
    if (data.order) {
      lastAiOrder = data.order;
      reorderTasks(data.order);
      renderScheduleTable(data.order);
    }
    if (data.defer) {
      deferredIds = data.defer;
      document.getElementById('statDeferred').textContent = deferredIds.length;
      renderTasks(lastAiOrder);
      renderScheduleTable(lastAiOrder);
    }
  } catch {
    const stressed = currentStressLevel >= 50;
    showAiSuggestion({
      title: stressed ? 'Take it gentle today' : "You're in great shape!",
      message: stressed
        ? 'Your stress is elevated. Start with lighter tasks to build momentum.'
        : "You're calm and focused — perfect for deep work. Tackle demanding tasks first."
    });
  }

  updateStressBadge(currentStressLevel);
  updateStressStats(currentStressLevel);

  // Log stress
  try {
    await fetch(`${API}/log-stress`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email, stressLevel: currentStressLevel,
        source: detectionSource,
        faceEmotion: lastFaceEmotion,
        voiceEmotion: lastVoiceEmotion,
        note, videoUrl: lastVideoUrl
      })
    });
    loadRecentActivity();
  } catch {}

  // Update smart scheduling after new scan
  updateSmartScheduling();

  detectionSource = 'slider';
  lastFaceEmotion = null;
  lastVoiceEmotion = null;
  lastVideoUrl = null;
  btn.disabled = false;
  btn.innerHTML = '<i class="fas fa-sparkles"></i> Analyze & Personalise My Day';
}
window.analyzeStress = analyzeStress;

// ── AI SUGGESTION ─────────────────────────────────────
function showAiSuggestion(data) {
  document.getElementById('aiTitle').textContent = data.title || 'Your plan';
  document.getElementById('aiMessage').textContent = data.message || '';
  document.getElementById('aiSuggestion').classList.add('visible');
}

function updateStressBadge(level) {
  const badge = document.getElementById('stressBadge');
  badge.className = 'stress-status-badge';
  if (level <= 30)      { badge.classList.add('low');      badge.innerHTML = '<i class="fas fa-circle" style="font-size:.45rem;"></i> Low Stress'; }
  else if (level <= 60) { badge.classList.add('moderate'); badge.innerHTML = '<i class="fas fa-circle" style="font-size:.45rem;"></i> Moderate'; }
  else                  { badge.classList.add('high');     badge.innerHTML = '<i class="fas fa-circle" style="font-size:.45rem;"></i> High Stress'; }
}

function updateStressStats(level) {
  document.getElementById('statStress').textContent = level + '/100';
  document.getElementById('statStressSub').textContent = STRESS_LABELS[Math.min(level, 100)] || '';
}

// ── TASKS ─────────────────────────────────────────────
let tasks = [];

async function loadTasks() {
  const email = user?.email || localStorage.getItem('email') || '';
  try {
    const res = await fetch(`${API}/get-tasks?email=${encodeURIComponent(email)}`);
    const data = await res.json();
    const serverTasks = data.tasks || data || [];
    console.log(`[Persistence] Loaded ${serverTasks.length} tasks from server`);
    
    tasks = serverTasks.map(t => {
      if (!t.category) console.warn(`[Persistence] Task ${t.id} ("${t.title}") has NO category. Defaulting to 'other'.`);
      return {
        ...t,
        category: t.category || 'other',
        done: t.status === 'done',
        deadline: t.deadline ? t.deadline.split('T')[0] : null
      };
    });
  } catch (err) { console.error('[Persistence] Error loading tasks:', err); }
  renderTasks();
  renderScheduleTable();
}

function renderTasks(orderedIds) {
  let list = [...tasks];
  if (orderedIds) {
    const reordered = orderedIds.map(id => list.find(t => t.id == id)).filter(Boolean);
    const rest = list.filter(t => !orderedIds.map(String).includes(String(t.id)));
    list = [...reordered, ...rest];
  }
  if (taskFilter === 'pending') list = list.filter(t => !t.done);
  if (taskFilter === 'done')    list = list.filter(t => t.done);

  const el = document.getElementById('taskList');
  if (!list.length) { el.innerHTML = '<div style="text-align:center;padding:2rem;color:var(--text3);font-size:.85rem;">No tasks here yet</div>'; updateTaskStats(); return; }

  el.innerHTML = list.map((t, idx) => {
    const pClass = `priority-${t.priority || 'medium'}`;
    const pLabel = (t.priority || 'medium').charAt(0).toUpperCase() + (t.priority || 'medium').slice(1);
    const isDeferred = deferredIds.map(String).includes(String(t.id));
    const isReordered = orderedIds && idx < orderedIds.length;
    return `<div class="task-item ${t.done ? 'completed' : ''}">
      <div class="task-body">
        <div class="task-title ${t.done ? 'striked' : ''}">${t.title || 'Untitled'}
          ${isReordered && !t.done ? '<span class="ai-order-badge"><i class="fas fa-sparkles"></i>AI</span>' : ''}
        </div>
        <div class="task-meta"><span><i class="fas fa-calendar" style="margin-right:3px;opacity:.6;"></i>${t.deadline || 'No deadline'}</span></div>
      </div>
      <span class="priority-badge ${pClass}">${pLabel}</span>
      ${isDeferred ? '<span class="badge badge-red" style="font-size:.68rem;">Deferred</span>' : ''}
      <div style="margin:0 10px;">
        ${t.done
          ? `<button class="btn btn-sm" style="background:var(--green-dim);color:var(--green);border:1px solid var(--green);padding:.2rem .5rem;font-size:.75rem;" onclick="toggleTask(${t.id})"><i class="fas fa-check-double"></i> Done</button>`
          : `<button class="btn btn-sm btn-outline" style="padding:.2rem .5rem;font-size:.75rem;" onclick="toggleTask(${t.id})"><i class="fas fa-check"></i> Complete</button>`}
      </div>
      <div class="task-actions">
        <button class="task-action-btn" onclick="openEditModal(${t.id})" title="Edit"><i class="fas fa-pencil"></i></button>
        <button class="task-action-btn danger" onclick="deleteTask(${t.id})" title="Delete"><i class="fas fa-trash"></i></button>
      </div>
    </div>`;
  }).join('');
  updateTaskStats();
}

function reorderTasks(ids) { renderTasks(ids); }
window.reorderTasks = reorderTasks;

function updateTaskStats() {
  const done    = tasks.filter(t => t.done).length;
  const pending = tasks.filter(t => !t.done).length;
  document.getElementById('statPending').textContent = pending;
  document.getElementById('statDone').textContent    = done;
  document.getElementById('statPct').textContent     = tasks.length ? Math.round(done / tasks.length * 100) + '% done' : '0% done';
  document.getElementById('statDeferred').textContent = deferredIds.length;
}

async function toggleTask(id) {
  const t = tasks.find(t => t.id == id); if (!t) return;
  t.done = !t.done;
  const email = user?.email || localStorage.getItem('email') || '';
  try {
    await fetch(`${API}/update-task`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ taskId: id, title: t.title, deadline: t.deadline, priority: t.priority, status: t.done ? 'done' : 'pending', user_email: email })
    });
  } catch {}
  renderTasks(lastAiOrder); renderScheduleTable(lastAiOrder); renderCalendar();
}
window.toggleTask = toggleTask;

async function deleteTask(id) {
  // Use custom modal instead of system confirm()
  openDeleteTaskModal(id);
}
window.deleteTask = deleteTask;

function openDeleteTaskModal(id) {
  document.getElementById('deleteTaskId').value = id;
  document.getElementById('deleteTaskModal').classList.add('open');
}
window.openDeleteTaskModal = openDeleteTaskModal;

function closeDeleteTaskModal() {
  document.getElementById('deleteTaskModal').classList.remove('open');
}
window.closeDeleteTaskModal = closeDeleteTaskModal;

async function confirmDeleteTask() {
  const id = document.getElementById('deleteTaskId').value;
  const email = user?.email || localStorage.getItem('email') || '';
  try {
    await fetch(`${API}/delete-task`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ taskId: id, user_email: email })
    });
    tasks = tasks.filter(t => t.id != id);
    deferredIds = deferredIds.filter(did => String(did) !== String(id));
    showToast('Task deleted');
    closeDeleteTaskModal();
    renderTasks(lastAiOrder); renderScheduleTable(lastAiOrder); renderCalendar();
    renderCategoryGrid();
  } catch { showToast('Error deleting task'); }
}
window.confirmDeleteTask = confirmDeleteTask;

function filterTasks(f, btn) {
  taskFilter = f;
  document.querySelectorAll('#panel-dashboard .badge[onclick^="filterTasks"]').forEach(b => {
    b.style.background = 'var(--card2)'; b.style.color = 'var(--text2)';
  });
  btn.style.background = 'var(--primary-dim)'; btn.style.color = 'var(--primary)';
  renderTasks(lastAiOrder);
}
window.filterTasks = filterTasks;

// ── CATEGORY GRID ────────────────────────────────────────────────────────────
function renderCategoryGrid() {
  const grid = document.getElementById('categoryGrid');
  if (!grid) return;
  const cats = getAllCategories();
  grid.innerHTML = cats.map(cat => {
    const count = tasks.filter(t => (t.category || 'other') === cat.id && !t.done).length;
    return `
      <div class="card cat-card" onclick="openCategory('${cat.id}')" style="border-color:${count>0?cat.border:'var(--border)'};background:${count>0?cat.color:'var(--card2)'}">
        <div class="cat-card-options" onclick="toggleCategoryMenu(event, '${cat.id}')">
          <i class="fas fa-ellipsis-v"></i>
        </div>
        <div class="cat-dropdown" id="dropdown-${cat.id}">
          <div class="cat-dropdown-item" onclick="openRenameModal(event, '${cat.id}')"><i class="fas fa-pen"></i> Rename</div>
          ${cat.id !== 'other' ? `<div class="cat-dropdown-item danger" onclick="openDeleteModal(event, '${cat.id}')"><i class="fas fa-trash"></i> Delete</div>` : ''}
        </div>
        ${count > 0 ? `<span class="cat-card-count">${count}</span>` : ''}
        <span class="cat-card-icon">${cat.icon}</span>
        <div class="cat-card-label">${cat.label}</div>
      </div>
    `;
  }).join('');
}
window.renderCategoryGrid = renderCategoryGrid;

function toggleCategoryMenu(e, catId) {
  e.stopPropagation();
  document.querySelectorAll('.cat-dropdown').forEach(d => { if (d.id !== `dropdown-${catId}`) d.classList.remove('show'); });
  const el = document.getElementById(`dropdown-${catId}`);
  if (el) el.classList.toggle('show');
}

// Close dropdowns on window click
window.addEventListener('click', (e) => {
  if (!e.target.closest('.cat-card-options')) {
    document.querySelectorAll('.cat-dropdown').forEach(d => d.classList.remove('show'));
  }
});

async function openRenameModal(e, catId) {
  e.stopPropagation();
  const cat = getCatById(catId);
  document.getElementById('renameCatId').value = catId;
  document.getElementById('renameCatInput').value = cat.label;
  document.getElementById('renameCatModal').classList.add('open');
  setTimeout(() => document.getElementById('renameCatInput').focus(), 100);
}
window.openRenameModal = openRenameModal;

function closeRenameModal() {
  document.getElementById('renameCatModal').classList.remove('open');
}
window.closeRenameModal = closeRenameModal;

async function confirmRenameCategory() {
  const catId = document.getElementById('renameCatId').value;
  const newName = document.getElementById('renameCatInput').value.trim();
  const cat = getCatById(catId);
  
  if (!newName || newName === cat.label) { closeRenameModal(); return; }

  const email = user?.email || localStorage.getItem('email') || '';
  
  // Update definition locally
  const custom = customCategories.find(c => c.id === catId);
  if (custom) {
    custom.label = newName;
  } else {
    // If it was a default, clone it to custom so we can track the name override
    const newCat = { ...cat, label: newName };
    customCategories.push(newCat);
  }
  
  localStorage.setItem('mf_custom_cats', JSON.stringify(customCategories)); // Legacy fallback
  
  try {
    await fetch(`${API}/save-category`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ userEmail: email, catId, label: newName, icon: cat.icon, color: cat.color, border: cat.border })
    });
    // Refresh local state
    const res = await fetch(`${API}/get-categories?email=${encodeURIComponent(email)}`);
    customCategories = (await res.json()).categories || [];
  } catch { showToast('Error saving to server'); }

  showToast('Category renamed ✓');
  closeRenameModal();
  renderCategoryGrid();
}
window.confirmRenameCategory = confirmRenameCategory;

function openDeleteModal(e, catId) {
  e.stopPropagation();
  if (catId === 'other') return showToast('Cannot delete "Other"');
  document.getElementById('deleteCatId').value = catId;
  document.getElementById('deleteCatModal').classList.add('open');
}
window.openDeleteModal = openDeleteModal;

function closeDeleteModal() {
  document.getElementById('deleteCatModal').classList.remove('open');
}
window.closeDeleteModal = closeDeleteModal;

async function confirmDeleteCategory() {
  const catId = document.getElementById('deleteCatId').value;
  const email = user?.email || localStorage.getItem('email') || '';
  
  try {
    const res = await fetch(`${API}/bulk-update-category`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ oldCategory: catId, newCategory: 'other', userEmail: email })
    });
    const data = await res.json();
    
    // Update local tasks
    tasks.forEach(t => { if (t.category === catId) t.category = 'other'; });
    
    // Refresh local categories
    const r = await fetch(`${API}/get-categories?email=${encodeURIComponent(email)}`);
    customCategories = (await r.json()).categories || [];

    showToast('Category deleted ✓');
    closeDeleteModal();
    renderCategoryGrid();
    if (currentCategory === catId) showCategoryGrid();
  } catch {
    showToast('Error syncing with server');
  }
}
window.confirmDeleteCategory = confirmDeleteCategory;

function openCategory(catId) {
  currentCategory = catId;
  document.getElementById('catGridView').style.display = 'none';
  document.getElementById('catDetailView').style.display = 'block';
  const cat = getCatById(catId);
  document.getElementById('catDetailTitle').innerHTML = `<span style="margin-right:.4rem;">${cat.icon}</span>${cat.label}`;
  
  const detailBtn = document.getElementById('catDetailBtn');
  if (detailBtn) {
    detailBtn.innerHTML = cat.id === 'other' ? '<i class="fas fa-plus"></i> Add Category' : '<i class="fas fa-plus"></i> Add Task';
  }
  
  updateScheduleNavLabel(cat.label);
  renderScheduleTable(lastAiOrder);
}
window.openCategory = openCategory;

function showCategoryGrid() {
  currentCategory = null;
  document.getElementById('catGridView').style.display = 'block';
  document.getElementById('catDetailView').style.display = 'none';
  updateScheduleNavLabel(null);
  renderCategoryGrid();
}
window.showCategoryGrid = showCategoryGrid;

function updateScheduleNavLabel(catLabel) {
  const navItem = [...document.querySelectorAll('.nav-item')].find(n => n.getAttribute('onclick')?.includes("'schedule'"));
  if (!navItem) return;
  if (catLabel) {
    navItem.innerHTML = `<i class="fas fa-list-check"></i>Schedule <span class="ai-order-badge" style="font-size:.6rem;padding:.1rem .35rem;">${catLabel}</span>`;
  } else {
    navItem.innerHTML = `<i class="fas fa-list-check"></i>View Schedule`;
  }
}
window.updateScheduleNavLabel = updateScheduleNavLabel;

// ── SCHEDULE TABLE ────────────────────────────────────
function renderScheduleTable(orderedIds) {
  const tbody = document.getElementById('scheduleBody');
  if (!tbody) return;

  let list = [...tasks];
  // Filter by current category if one is selected
  if (currentCategory) {
    list = list.filter(t => (t.category || 'other') === currentCategory);
  }
  if (orderedIds) {
    const orderedFiltered = orderedIds.filter(id => list.find(t => t.id == id));
    const reordered = orderedFiltered.map(id => list.find(t => t.id == id)).filter(Boolean);
    const rest = list.filter(t => !orderedIds.map(String).includes(String(t.id)));
    list = [...reordered, ...rest];
  }

  // Update stress info banner
  const infoEl = document.getElementById('scheduleStressInfo');
  if (infoEl && (orderedIds || deferredIds.length > 0)) {
    const label = STRESS_LABELS[Math.min(currentStressLevel, 100)] || 'Neutral';
    infoEl.innerHTML = `<i class="fas fa-brain" style="color:var(--primary);margin-right:.3rem;"></i>Ordered by AI based on your stress level: <b>${currentStressLevel}/100 — ${label}</b>${deferredIds.length > 0 ? ` · <span style="color:var(--red)">${deferredIds.length} task(s) deferred</span>` : ''}`;
  } else if (infoEl) {
    infoEl.innerHTML = '<i class="fas fa-info-circle" style="opacity:.5;margin-right:.3rem;"></i>Run Analyze to get AI-ordered schedule';
  }

  if (!list.length) { tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text3);padding:2rem;">No tasks yet</td></tr>'; return; }

  const pColor = { high: 'var(--red)', medium: 'var(--yellow)', low: 'var(--green)' };
  tbody.innerHTML = list.map((t, idx) => {
    const isDeferred = deferredIds.map(String).includes(String(t.id));
    const isAiOrdered = orderedIds && idx < orderedIds.length;
    return `<tr class="${isDeferred ? 'deferred-row' : ''}">
      <td>${t.title || 'Untitled'}
        ${isAiOrdered && !t.done ? '<span class="ai-order-badge"><i class="fas fa-sparkles"></i>AI</span>' : ''}
        ${isDeferred ? '<br><span class="badge badge-red" style="font-size:.65rem;padding:1px 4px;">AI Deferred</span>' : ''}
        ${!currentCategory ? `<br><span class="badge badge-purple" style="font-size:.62rem;padding:1px 6px;">${getCatById(t.category||'other').icon} ${getCatById(t.category||'other').label}</span>` : ''}
      </td>
      <td style="color:var(--text2)">${t.deadline || '—'}</td>
      <td><span style="font-weight:600;color:${pColor[t.priority||'medium']}">${(t.priority||'medium').charAt(0).toUpperCase()+(t.priority||'medium').slice(1)}</span></td>
      <td>${t.done
        ? `<button class="btn btn-sm" style="background:var(--green-dim);color:var(--green);border:1px solid var(--green);padding:.2rem .5rem;font-size:.75rem;" onclick="toggleTask(${t.id})"><i class="fas fa-check-double"></i> Done</button>`
        : `<button class="btn btn-sm btn-outline" style="padding:.2rem .5rem;font-size:.7rem;" onclick="toggleTask(${t.id})"><i class="fas fa-check"></i> Complete</button>`}
      </td>
      <td style="text-align:center;">
        <button class="task-action-btn" onclick="openEditModal(${t.id})" title="Edit"><i class="fas fa-pencil"></i></button>
        <button class="task-action-btn danger" onclick="deleteTask(${t.id})" title="Delete"><i class="fas fa-trash"></i></button>
      </td>
    </tr>`;
  }).join('');
}

// ── MODAL ─────────────────────────────────────────────
function openAddModal(dateStr) {
  renderCategoryOptions(); // Ensure dropdown is dynamic
  document.getElementById('modalTitle').textContent = 'Add New Task';
  document.getElementById('editTaskId').value = '';
  document.getElementById('taskTitle').value = '';
  // Enforce today as minimum date
  const todayStr = new Date().toISOString().split('T')[0];
  const dl = document.getElementById('taskDeadline');
  dl.min = todayStr;
  dl.value = dateStr && dateStr >= todayStr ? dateStr : '';
  document.getElementById('taskPriority').value = 'medium';
  document.getElementById('taskNotes').value = '';
  const catSel = document.getElementById('taskCategory');
  if (catSel) {
    // Pre-select currentCategory if available
    const optVal = currentCategory && catSel.querySelector(`option[value="${currentCategory}"]`) ? currentCategory : 'other';
    catSel.value = optVal;
    handleCategorySelect(catSel);
  }
  document.getElementById('taskModal').classList.add('open');
  setTimeout(() => document.getElementById('taskTitle').focus(), 100);
}
window.openAddModal = openAddModal;

function openEditModal(id) {
  const t = tasks.find(t => t.id == id); if (!t) return;
  document.getElementById('modalTitle').textContent = 'Edit Task';
  document.getElementById('editTaskId').value = id;
  document.getElementById('taskTitle').value = t.title || '';
  const todayStr = new Date().toISOString().split('T')[0];
  const dl = document.getElementById('taskDeadline');
  dl.min = todayStr;
  dl.value = t.deadline || '';
  document.getElementById('taskPriority').value = t.priority || 'medium';
  document.getElementById('taskNotes').value = t.notes || '';
  const catSel = document.getElementById('taskCategory');
  if (catSel) {
    const tCat = t.category || 'other';
    // If it's a custom category not in the select, add a temp option
    if (!catSel.querySelector(`option[value="${tCat}"]`)) {
      const opt = document.createElement('option');
      opt.value = tCat; opt.textContent = tCat;
      catSel.appendChild(opt);
    }
    catSel.value = tCat;
    handleCategorySelect(catSel);
  }
  document.getElementById('taskModal').classList.add('open');
}
window.openEditModal = openEditModal;

function closeModal() { document.getElementById('taskModal').classList.remove('open'); }
window.closeModal = closeModal;
document.getElementById('taskModal').addEventListener('click', e => { if (e.target === document.getElementById('taskModal')) closeModal(); });

async function submitTask() {
  const title = document.getElementById('taskTitle').value.trim();
  if (!title) { showToast('Task title is required'); return; }
  const deadline = document.getElementById('taskDeadline').value;
  // Date validation: reject past dates
  if (deadline) {
    const todayStr = new Date().toISOString().split('T')[0];
    if (deadline < todayStr) { showToast('Please select today or a future date'); return; }
  }
  const priority = document.getElementById('taskPriority').value;
  const editId   = document.getElementById('editTaskId').value;
  const email    = user?.email || localStorage.getItem('email') || '';
  let category = document.getElementById('taskCategory')?.value || currentCategory || 'other';
  
  // Auto-add custom category if "other" is selected and name is typed
  if (category === 'other') {
    const customName = document.getElementById('newCatInput')?.value.trim();
    if (customName) {
      addCustomCategory(); // This internal function adds to list and sets dropdown value
      category = document.getElementById('taskCategory').value;
    }
  }

  if (editId) {
    try {
      await fetch(`${API}/update-task`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ taskId: parseInt(editId), title, deadline, priority, status: 'pending', user_email: email, category })
      });
      const t = tasks.find(t => t.id == editId);
      if (t) {
        t.title = title;
        t.deadline = deadline;
        t.priority = priority;
        t.category = category;
        t.notes = document.getElementById('taskNotes').value;
      }
      showToast('Task updated ✓');
    } catch { showToast('Error updating task'); }
  } else {
    try {
      console.log(`[Persistence] Adding task to category: ${category}`);
      const res = await fetch(`${API}/add-task`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, deadline, priority, user_email: email, category })
      });
      const data = await res.json();
      if (data.success) {
        const notes = document.getElementById('taskNotes').value;
        const newTask = {
          id: data.id || Date.now(),
          title,
          deadline,
          priority,
          done: false,
          category,
          notes,
          status: 'pending',
          created_at: new Date().toISOString()
        };
        tasks.push(newTask);
        showToast('Task added ✓');
      } else { showToast(data.error || 'Error adding task'); }
    } catch { showToast('Server error'); }
  }
  closeModal();
  renderTasks(lastAiOrder); renderScheduleTable(lastAiOrder); renderCalendar();
  if (document.getElementById('catGridView')?.style.display !== 'none') renderCategoryGrid();
}
window.submitTask = submitTask;

// ── CALENDAR ──────────────────────────────────────────
let calY, calM;
const taskedDates = new Set();

function initCalendar() {
  const n = new Date(); calY = n.getFullYear(); calM = n.getMonth();
}

function renderCalendar() {
  const days = ['Su','Mo','Tu','We','Th','Fr','Sa'];
  const today = new Date();
  const fd  = new Date(calY, calM, 1).getDay();
  const dim = new Date(calY, calM + 1, 0).getDate();
  const picker = document.getElementById('calMonthPicker');
  if (picker) picker.value = `${calY}-${String(calM+1).padStart(2,'0')}`;
  let html = '';
  days.forEach(d => html += `<div class="cal-head">${d}</div>`);
  for (let i = 0; i < fd; i++) html += `<div class="cal-day empty"></div>`;
  for (let d = 1; d <= dim; d++) {
    const ds = `${calY}-${String(calM+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
    const isToday = d === today.getDate() && calM === today.getMonth() && calY === today.getFullYear();
    const dayTasks = tasks.filter(t => t.deadline && t.deadline.split('T')[0] === ds);
    let taskHtml = dayTasks.length > 0
      ? '<div class="cal-tasks-list">' + dayTasks.map(t => `<div class="cal-task-item priority-${t.priority||'medium'} ${t.done?'done':''}" title="${t.title}">${t.title}</div>`).join('') + '</div>'
      : '';
    html += `<div class="cal-day${isToday?' today':''}${dayTasks.length>0?' has-task':''}" data-date="${ds}">
               <div class="cal-day-num">${d}</div>${taskHtml}
             </div>`;
  }
  document.getElementById('calGrid').innerHTML = html;
  document.querySelectorAll('.cal-day[data-date]').forEach(el => {
    el.addEventListener('click', function(e) {
      if (e.target.classList.contains('cal-task-item')) return;
      const lbl = new Date(this.dataset.date + 'T00:00:00').toLocaleDateString('en-IN',{weekday:'long',day:'numeric',month:'long'});
      document.getElementById('calSelected').textContent = `Selected: ${lbl}`;
      openAddModal(this.dataset.date);
    });
  });
}

function handleMonthPicker(val) {
  if (!val) return;
  const parts = val.split('-');
  calY = parseInt(parts[0]); calM = parseInt(parts[1]) - 1;
  renderCalendar();
  document.getElementById('calSelected').textContent = '';
}
window.handleMonthPicker = handleMonthPicker;

document.getElementById('calPrev').addEventListener('click', () => { calM--; if(calM<0){calM=11;calY--;} renderCalendar(); document.getElementById('calSelected').textContent=''; });
document.getElementById('calNext').addEventListener('click', () => { calM++; if(calM>11){calM=0;calY++;} renderCalendar(); document.getElementById('calSelected').textContent=''; });

// ── RECENT ACTIVITY + HISTORY ─────────────────────────
async function loadRecentActivity() {
  const email = user?.email || localStorage.getItem('email') || '';
  const list  = document.getElementById('recentActivityList');
  const historyList = document.getElementById('historyList');
  if (!email) return;

  try {
    const res  = await fetch(`${API}/stress-logs?email=${encodeURIComponent(email)}&days=30`);
    const data = await res.json();
    const logs = data.logs || [];
    const avgStress = logs.length > 0 ? logs.reduce((s,l) => s + l.stress_level, 0) / logs.length : 40;

    const doneTasks = tasks.filter(t => t.done).map(t => ({
      type: 'task', title: t.title,
      logged_at: t.updated_at || t.created_at || new Date().toISOString(), id: t.id
    }));

    let items = [...logs.map(l => ({...l, type:'stress'})), ...doneTasks];
    items.sort((a,b) => new Date(b.logged_at) - new Date(a.logged_at));

    if (items.length === 0) {
      const empty = '<div class="activity-item"><div class="activity-icon"><i class="fas fa-heartbeat"></i></div><div><div class="activity-title">No recent activity yet</div><div class="activity-time">Complete a scan to see it here</div></div></div>';
      if (list) list.innerHTML = empty;
      if (historyList) historyList.innerHTML = empty;
      return;
    }

    const html = items.slice(0, 10).map((item, idx) => {
      const d = new Date(item.logged_at);
      const timeStr = d.toLocaleString('en-IN', { hour:'2-digit', minute:'2-digit', hour12:true, day:'numeric', month:'short' });

      if (item.type === 'task') {
        return `<div class="activity-item">
          <div class="activity-icon" style="background:var(--green-dim);color:var(--green);"><i class="fas fa-check-circle"></i></div>
          <div style="flex:1">
            <div class="activity-title">Task Done: <b>${item.title}</b></div>
            <div class="activity-time">${timeStr}</div>
          </div>
        </div>`;
      }

      let pattern = 'Stable';
      if (item.stress_level > avgStress + 15) pattern = '↑ Peak';
      else if (item.stress_level < avgStress - 15) pattern = '↓ Recovery';
      const next = items.slice(idx+1).find(it => it.type === 'stress');
      if (next) {
        if (item.stress_level > next.stress_level + 10) pattern = '↑ Rising';
        else if (item.stress_level < next.stress_level - 10) pattern = '↓ Declining';
      }

      const icon  = item.source === 'face_voice_scan' ? 'fa-camera' : 'fa-sliders-h';
      const label = item.source === 'face_voice_scan'
        ? `Scan — Stress ${item.stress_level}/100 (${item.face_emotion || ''})`
        : `Check-in — Stress ${item.stress_level}/100`;
      const videoBtn = item.video_url
        ? `<button class="btn btn-sm btn-outline" style="margin-top:4px;padding:2px 8px;font-size:.7rem;" onclick="playStressVideo('${item.video_url}')"><i class="fas fa-play"></i> Watch</button>`
        : (item.source === 'face_voice_scan' ? '<div style="font-size:.65rem;color:var(--text3);margin-top:3px;">Video removed (7-day policy)</div>' : '');

      const pColor = pattern.includes('↑') ? 'var(--red)' : pattern.includes('↓') ? 'var(--green)' : 'var(--text3)';
      return `<div class="activity-item">
        <div class="activity-icon"><i class="fas ${icon}"></i></div>
        <div style="flex:1">
          <div class="activity-title">${label} <span style="font-size:.65rem;color:${pColor};font-weight:600;">${pattern}</span></div>
          <div class="activity-time">${timeStr}</div>
          ${videoBtn}
        </div>
      </div>`;
    }).join('');

    if (list) list.innerHTML = html;
    if (historyList) historyList.innerHTML = html;
  } catch(err) { console.error('Activity error:', err); }
}

function playStressVideo(url) {
  const modal = document.getElementById('videoModal');
  const video = document.getElementById('reportVideoPlayer');
  if (!modal || !video) return;
  video.src = API + url;
  modal.classList.add('open');
}
window.playStressVideo = playStressVideo;

function closeVideoModal() {
  const modal = document.getElementById('videoModal');
  const video = document.getElementById('reportVideoPlayer');
  if (modal) modal.classList.remove('open');
  if (video) { video.pause(); video.src = ''; }
}
window.closeVideoModal = closeVideoModal;

// ── BEHAVIORAL ANALYSIS ───────────────────────────────
async function loadBehaviorInsights() {
  const email = user?.email || localStorage.getItem('email') || '';
  const days  = parseInt(document.getElementById('behaviorDays')?.value || 7);
  if (!email) return;

  try {
    const res  = await fetch(`${API}/stress-logs?email=${encodeURIComponent(email)}&days=${days}`);
    const data = await res.json();
    const logs = data.logs || [];

    const avgStress   = logs.length > 0 ? Math.round(logs.reduce((s,l) => s+l.stress_level,0)/logs.length) : 0;
    const tasksDone   = tasks.filter(t => t.done).length;
    const scans       = logs.filter(l => l.source === 'face_voice_scan').length;

    // KPI tiles
    const stressColor = avgStress <= 35 ? 'var(--green)' : avgStress <= 60 ? 'var(--yellow)' : 'var(--red)';
    document.getElementById('bkpi-avgStress').innerHTML = `<span style="color:${stressColor}">${avgStress || '—'}</span>`;
    document.getElementById('bkpi-avgStressTrend').innerHTML = avgStress > 60
      ? '<span style="color:var(--red)">⚠️ High — consider lighter days</span>'
      : avgStress > 35
      ? '<span style="color:var(--yellow)">→ Moderate range</span>'
      : '<span style="color:var(--green)">✓ Healthy range</span>';

    document.getElementById('bkpi-tasksDone').textContent = tasksDone;
    document.getElementById('bkpi-tasksDoneTrend').innerHTML = tasksDone > 5
      ? '<span style="color:var(--green)">↑ Great productivity</span>'
      : tasksDone > 0
      ? '<span style="color:var(--yellow)">→ Building momentum</span>'
      : '<span style="color:var(--text3)">Start completing tasks</span>';

    document.getElementById('bkpi-scans').textContent = scans;
    document.getElementById('bkpi-scansTrend').innerHTML = scans >= 3
      ? '<span style="color:var(--primary)">↑ Consistent tracking</span>'
      : '<span style="color:var(--text3)">Scan daily for better insights</span>';

    // Generate patterns
    const patternsEl = document.getElementById('behaviorPatterns');
    if (logs.length === 0) {
      patternsEl.innerHTML = '<div style="text-align:center;padding:2rem;color:var(--text3);font-size:.85rem;">Run a stress scan to start building your behavioral profile.</div>';
      return;
    }

    const patterns = [];

    // Peak stress time pattern
    const highStressLogs = logs.filter(l => l.stress_level > 65);
    if (highStressLogs.length > 0) {
      const hours = highStressLogs.map(l => new Date(l.logged_at).getHours());
      const avgHour = Math.round(hours.reduce((a,b)=>a+b,0)/hours.length);
      const timeLabel = avgHour < 12 ? 'mornings' : avgHour < 17 ? 'afternoons' : 'evenings';
      patterns.push({
        icon: '⏰', bg: 'var(--red-dim)', color: 'var(--red)',
        title: `Stress peaks in your ${timeLabel}`,
        desc: `Your highest stress readings tend to occur during ${timeLabel}. Consider scheduling demanding tasks at other times and protecting this window with lighter work or breaks.`
      });
    }

    // Completion vs stress correlation
    if (logs.length >= 3 && tasksDone > 0) {
      const recentAvg = logs.slice(0,3).reduce((s,l)=>s+l.stress_level,0)/3;
      patterns.push({
        icon: '📈', bg: 'var(--primary-dim)', color: 'var(--primary)',
        title: recentAvg < 50 ? 'Low stress days boost your output' : 'Stress is affecting your output',
        desc: recentAvg < 50
          ? `On calmer days your task completion improves significantly. You've completed ${tasksDone} tasks — your best performance aligns with low-stress periods.`
          : `Recent stress levels are elevated (avg ${Math.round(recentAvg)}/100). This typically reduces focus and completion rates. Prioritise recovery activities.`
      });
    }

    // Scan frequency
    if (scans >= 5) {
      patterns.push({
        icon: '🎯', bg: 'var(--green-dim)', color: 'var(--green)',
        title: 'Excellent self-awareness habit',
        desc: `You've completed ${scans} stress scans — this level of self-monitoring is associated with better emotional regulation and more intentional decision-making.`
      });
    } else {
      patterns.push({
        icon: '💡', bg: 'var(--yellow-dim)', color: 'var(--yellow)',
        title: 'Build your awareness baseline',
        desc: 'Scanning at least once daily helps MindFlow detect stress patterns earlier and give you more accurate scheduling recommendations. Try scanning at the same time each day.'
      });
    }

    // Recovery pattern
    const recentTrend = logs.length >= 4
      ? logs.slice(0,2).reduce((s,l)=>s+l.stress_level,0)/2 - logs.slice(-2).reduce((s,l)=>s+l.stress_level,0)/2
      : 0;
    if (Math.abs(recentTrend) > 10) {
      patterns.push({
        icon: recentTrend < 0 ? '📉' : '📈',
        bg: recentTrend < 0 ? 'var(--green-dim)' : 'var(--red-dim)',
        color: recentTrend < 0 ? 'var(--green)' : 'var(--red)',
        title: recentTrend < 0 ? 'Stress is rising — act now' : 'Stress is improving — keep it up',
        desc: recentTrend < 0
          ? 'Your stress has increased recently. Consider reducing your workload, taking short walks, and deferring non-urgent tasks until you recover.'
          : 'Your stress levels have improved over this period. Your current habits are working — maintain your routine and build on this momentum.'
      });
    }

    patternsEl.innerHTML = patterns.map(p => `
      <div class="pattern-card">
        <div class="pattern-icon" style="background:${p.bg};color:${p.color};">${p.icon}</div>
        <div class="pattern-text">
          <h4 style="color:${p.color};">${p.title}</h4>
          <p>${p.desc}</p>
        </div>
      </div>`).join('');

  } catch(err) { console.error('Behavior insights error:', err); }
}
window.loadBehaviorInsights = loadBehaviorInsights;

// ── SMART SCHEDULING ──────────────────────────────────
function updateSmartScheduling() {
  const stress = currentStressLevel;
  const label  = STRESS_LABELS[Math.min(stress, 100)] || 'Neutral';
  const pill   = document.getElementById('schedStressPill');
  const tipsEl = document.getElementById('schedTips');
  if (!pill || !tipsEl) return;

  // Update stress pill
  const pillColor = stress <= 35 ? 'var(--green)' : stress <= 60 ? 'var(--yellow)' : 'var(--red)';
  const pillBg    = stress <= 35 ? 'var(--green-dim)' : stress <= 60 ? 'var(--yellow-dim)' : 'var(--red-dim)';
  pill.style.background = pillBg;
  pill.style.color = pillColor;
  pill.style.border = `1px solid ${pillColor}`;
  pill.textContent = `Stress: ${stress}/100 — ${label}`;

  // Time blocks
  let morning, afternoon, evening;
  if (stress <= 30) {
    morning = 'Deep work & complex tasks'; afternoon = 'Meetings & collaboration'; evening = 'Review & planning';
  } else if (stress <= 50) {
    morning = 'Moderate tasks & emails'; afternoon = 'Creative or social work'; evening = 'Light tasks & wind down';
  } else if (stress <= 70) {
    morning = 'Easiest tasks first'; afternoon = 'Short focused sessions'; evening = 'Rest — avoid screens';
  } else {
    morning = 'Rest & gentle movement'; afternoon = 'Only critical tasks'; evening = 'Complete rest, no work';
  }
  document.getElementById('sched-morning').textContent   = morning;
  document.getElementById('sched-afternoon').textContent = afternoon;
  document.getElementById('sched-evening').textContent   = evening;

  // Tips
  const pending = tasks.filter(t => !t.done);
  const highPri = pending.filter(t => t.priority === 'high').length;
  const lowPri  = pending.filter(t => t.priority === 'low').length;

  const tips = [];
  if (stress <= 35) {
    tips.push({ icon: 'fa-rocket', text: "You're in peak state. This is your optimal window for creative thinking, problem-solving, and any task that requires sustained focus." });
    tips.push({ icon: 'fa-list-check', text: highPri > 0 ? `You have ${highPri} high-priority task(s) — tackle them now while your energy is high.` : 'All high-priority tasks are cleared. Use this time to get ahead on upcoming work.' });
    tips.push({ icon: 'fa-clock', text: 'Use 90-minute deep work sessions with 15-minute breaks (Ultradian rhythm). Avoid checking messages until after your first session.' });
  } else if (stress <= 50) {
    tips.push({ icon: 'fa-balance-scale', text: "You're in a balanced state — neither too calm nor too stressed. Productive for a wide range of tasks." });
    tips.push({ icon: 'fa-forward', text: 'Start with a quick win (5-10 min task) to build momentum before tackling larger items.' });
    tips.push({ icon: 'fa-mug-hot', text: 'Hydrate and take a 5-minute stretch break every hour. Your focus will last longer.' });
  } else if (stress <= 70) {
    tips.push({ icon: 'fa-shield-halved', text: "Stress is noticeable. Protect your energy — focus on one task at a time and close unnecessary browser tabs." });
    tips.push({ icon: 'fa-arrow-down', text: lowPri > 0 ? `Start with your ${lowPri} low-priority task(s) to build confidence before heavier work.` : 'Consider breaking one large task into 3 smaller micro-tasks to make progress feel achievable.' });
    tips.push({ icon: 'fa-tree', text: 'A 10-minute walk outside reduces cortisol by up to 15%. Even standing up and stretching helps.' });
    if (deferredIds.length > 0) tips.push({ icon: 'fa-calendar-plus', text: `${deferredIds.length} high-priority task(s) have been deferred by AI. Revisit them tomorrow when you feel better.` });
  } else {
    tips.push({ icon: 'fa-heart-pulse', text: "High stress detected. Your cognitive capacity is reduced — this is normal and temporary. Be kind to yourself." });
    tips.push({ icon: 'fa-minimize', text: 'Do only what is absolutely necessary today. Everything else can wait — seriously.' });
    tips.push({ icon: 'fa-wind', text: 'Try box breathing: inhale 4s, hold 4s, exhale 4s, hold 4s. Repeat 4 times. It activates your parasympathetic nervous system.' });
    tips.push({ icon: 'fa-moon', text: 'Prioritise sleep tonight. Even one extra hour of sleep can reduce stress by 25% the following day.' });
  }

  tipsEl.innerHTML = tips.map(t => `
    <div class="tip-card">
      <i class="fas ${t.icon}"></i>
      <p>${t.text}</p>
    </div>`).join('');
}
window.updateSmartScheduling = updateSmartScheduling;

// ── TOAST ─────────────────────────────────────────────
function showToast(msg) {
  const t = document.getElementById('toast');
  if (!t) return;
  t.textContent = msg; t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 3000);
}
window.showToast = showToast;

// ── CATEGORY MODAL HELPERS ───────────────────────────────────────────────────
function handleCategorySelect(sel) {
  const grp = document.getElementById('customCatGroup');
  if (!grp) return;
  grp.style.display = sel.value === 'other' ? 'block' : 'none';
}
window.handleCategorySelect = handleCategorySelect;

async function addCustomCategory() {
  const input = document.getElementById('newCatInput');
  const raw = input?.value.trim();
  if (!raw) { showToast('Enter a category name'); return; }
  const id = raw.toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9-]/g, '');
  if (getAllCategories().find(c => c.id === id)) { showToast('Category already exists'); return; }
  
  // Pick colors and icon from pools
  const colorIdx = (customCategories.length) % CATEGORY_COLOR_POOL.length;
  const iconIdx  = (customCategories.length) % CATEGORY_ICON_POOL.length;
  const colorSet = CATEGORY_COLOR_POOL[colorIdx];
  const iconSet  = CATEGORY_ICON_POOL[iconIdx];
  
  const email = user?.email || localStorage.getItem('email') || '';
  
  try {
    await fetch(`${API}/save-category`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ userEmail: email, catId: id, label: raw, icon: iconSet, color: colorSet.color, border: colorSet.border })
    });
    // Refresh local state
    const res = await fetch(`${API}/get-categories?email=${encodeURIComponent(email)}`);
    customCategories = (await res.json()).categories || [];
  } catch { showToast('Error saving to server'); }

  renderCategoryOptions(); // Centralized rendering
  const sel = document.getElementById('taskCategory');
  if (sel) {
    sel.value = id;
    document.getElementById('customCatGroup').style.display = 'none';
  }
  if (input) input.value = '';
  showToast(`Category "${raw}" added!`);
  renderCategoryGrid(); // Refresh grid too
}
window.addCustomCategory = addCustomCategory;

// ── INIT ──────────────────────────────────────────────
loadTasks().then(() => { 
  loadCategories(); // Integrated category loading
  initCalendar(); 
  loadRecentActivity(); 
  renderCategoryGrid(); 
});
updateSlider(40);
updateSmartScheduling();

// ── CHART.JS PROGRESS TRACKING ────────────────────────
let barChartInstance = null;
let donutChartInstance = null;
let polarChartInstance = null;

async function renderChart() {
  if (barChartInstance)   { barChartInstance.destroy();   barChartInstance = null; }
  if (donutChartInstance) { donutChartInstance.destroy(); donutChartInstance = null; }
  if (polarChartInstance) { polarChartInstance.destroy(); polarChartInstance = null; }

  const scale = document.getElementById('chartTimeScale')?.value || 'week';
  const days  = scale === 'week' ? 7 : 30;
  const today = new Date();
  const email = user?.email || localStorage.getItem('email') || '';

  let stressLogs = [];
  try {
    const r = await fetch(`${API}/stress-logs?email=${encodeURIComponent(email)}&days=${days}`);
    const d = await r.json();
    stressLogs = d.logs || [];
  } catch {}

  const labels = [], completedData = [], stressData = [];
  let totalDone = 0, totalPending = 0, highP = 0, medP = 0, lowP = 0;
  let bestStress = 100, streakDays = 0, lastActive = false;

  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(d.getDate() - i);
    const ds = `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
    labels.push(d.toLocaleDateString('en-IN', { month: 'short', day: 'numeric' }));

    const dayTasks    = tasks.filter(t => t.deadline && t.deadline.split('T')[0] === ds);
    const dayDone     = dayTasks.filter(t => t.done).length;
    totalDone    += dayDone;
    totalPending += dayTasks.filter(t => !t.done).length;
    dayTasks.forEach(t => { if(t.priority==='high') highP++; else if(t.priority==='low') lowP++; else medP++; });
    completedData.push(dayDone);

    const dayLogs = stressLogs.filter(l => {
      const ld = new Date(l.logged_at);
      return `${ld.getFullYear()}-${String(ld.getMonth()+1).padStart(2,'0')}-${String(ld.getDate()).padStart(2,'0')}` === ds;
    });
    if (dayLogs.length > 0) {
      const avg = dayLogs.reduce((s,l) => s + l.stress_level, 0) / dayLogs.length;
      stressData.push(parseFloat(avg.toFixed(1)));
      if (avg < bestStress) bestStress = Math.round(avg);
      if (i <= 6) { if (dayLogs.length > 0) { if (lastActive || i === 6) streakDays++; lastActive = true; } else lastActive = false; }
    } else {
      stressData.push(null);
    }
  }

  // KPI updates
  const completionRate = (totalDone + totalPending) > 0 ? Math.round(totalDone / (totalDone + totalPending) * 100) : 0;
  const recentStress   = stressLogs.slice(0, Math.ceil(stressLogs.length/2));
  const olderStress    = stressLogs.slice(Math.ceil(stressLogs.length/2));
  const recentAvg  = recentStress.length > 0 ? recentStress.reduce((s,l)=>s+l.stress_level,0)/recentStress.length : 0;
  const olderAvg   = olderStress.length > 0  ? olderStress.reduce((s,l)=>s+l.stress_level,0)/olderStress.length  : recentAvg;
  const improvement = olderAvg > 0 ? Math.round(((olderAvg - recentAvg) / olderAvg) * 100) : 0;

  document.getElementById('pkpi-streak').textContent      = streakDays + ' days';
  document.getElementById('pkpi-best').textContent        = bestStress < 100 ? bestStress + '/100' : '—';
  document.getElementById('pkpi-completion').textContent  = completionRate + '%';
  document.getElementById('pkpi-improvement').textContent = improvement > 0 ? '+' + improvement + '%' : improvement + '%';
  document.getElementById('pkpi-improvement').style.color = improvement > 0 ? 'var(--green)' : improvement < 0 ? 'var(--red)' : 'var(--text2)';

  Chart.defaults.color = '#8b98a5';
  Chart.defaults.font.family = "'Plus Jakarta Sans', sans-serif";

  const ctxBar   = document.getElementById('activityBarChart');
  const ctxDonut = document.getElementById('activityDonutChart');
  const ctxPolar = document.getElementById('priorityPolarChart');
  if (!ctxBar) return;

  const gradRed = ctxBar.getContext('2d').createLinearGradient(0,0,0,300);
  gradRed.addColorStop(0,'rgba(248,113,113,0.8)'); gradRed.addColorStop(1,'rgba(248,113,113,0.05)');
  const gradBlue = ctxBar.getContext('2d').createLinearGradient(0,0,0,300);
  gradBlue.addColorStop(0,'rgba(102,126,234,0.85)'); gradBlue.addColorStop(1,'rgba(118,75,162,0.2)');

  barChartInstance = new Chart(ctxBar, {
    type: 'bar',
    data: {
      labels,
      datasets: [
        { label: 'Tasks Done', data: completedData, backgroundColor: gradBlue, borderColor: '#667eea', borderWidth: 1, borderRadius: 5, yAxisID: 'y' },
        { label: 'Stress Level', data: stressData, type: 'line', borderColor: '#f87171', backgroundColor: 'rgba(248,113,113,0.08)', borderWidth: 2, pointBackgroundColor: '#f87171', pointRadius: 3, tension: 0.4, yAxisID: 'y1', spanGaps: true }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      scales: {
        x: { grid: { color: 'rgba(255,255,255,0.03)' } },
        y: { type: 'linear', position: 'left', beginAtZero: true, ticks: { stepSize: 1 }, grid: { color: 'rgba(255,255,255,0.03)' } },
        y1: { type: 'linear', position: 'right', min: 0, max: 100, grid: { drawOnChartArea: false } }
      },
      plugins: { legend: { labels: { usePointStyle: true, boxWidth: 8 } } }
    }
  });

  if (ctxDonut) {
    const d1 = totalDone || 0, d2 = totalPending || 0;
    const pct = (d1+d2) > 0 ? Math.round(d1/(d1+d2)*100) : 0;
    const centerText = {
      id: 'center', beforeDraw(chart) {
        const {width, height, ctx} = chart;
        ctx.restore();
        ctx.font = `600 ${(height/110).toFixed(2)}em 'Plus Jakarta Sans',sans-serif`;
        ctx.fillStyle = '#f8fafc'; ctx.textBaseline = 'middle';
        const tx = Math.round((width - ctx.measureText(pct+'%').width)/2);
        const ty = chart.chartArea.top + (chart.chartArea.bottom - chart.chartArea.top)/2;
        ctx.fillText(pct+'%', tx, ty - height*0.02);
        ctx.font = `500 ${(height/320).toFixed(2)}em 'Plus Jakarta Sans',sans-serif`;
        ctx.fillStyle = '#64748b';
        const sx = Math.round((width - ctx.measureText('DONE').width)/2);
        ctx.fillText('DONE', sx, ty + height*0.12);
        ctx.save();
      }
    };
    donutChartInstance = new Chart(ctxDonut, {
      type: 'doughnut',
      data: { labels: ['Done','Pending'], datasets: [{ data: [d1||0, d2||1], backgroundColor: ['#667eea','#334155'], borderWidth: 0, hoverOffset: 4 }] },
      plugins: [centerText],
      options: { responsive: true, maintainAspectRatio: false, cutout: '76%', plugins: { legend: { position:'bottom', labels:{ usePointStyle:true, padding:12 } } } }
    });
  }

  if (ctxPolar) {
    if(highP===0&&medP===0&&lowP===0) medP=1;
    polarChartInstance = new Chart(ctxPolar, {
      type: 'polarArea',
      data: {
        labels: ['High','Medium','Low'],
        datasets: [{ data: [highP, medP, lowP], backgroundColor: ['rgba(248,113,113,.75)','rgba(251,191,36,.75)','rgba(74,222,128,.75)'], borderWidth: 1, borderColor: '#1e293b' }]
      },
      options: { responsive: true, maintainAspectRatio: false, plugins: { legend:{ position:'bottom', labels:{ usePointStyle:true } } }, scales:{ r:{ grid:{ color:'rgba(255,255,255,.03)' }, ticks:{ display:false } } } }
    });
  }

  // Weekly recommendations
  renderWeeklyRecommendations(stressLogs, completionRate, improvement);
}
window.renderChart = renderChart;

function renderWeeklyRecommendations(stressLogs, completionRate, improvement) {
  const el = document.getElementById('weeklyRecs');
  if (!el) return;

  const avgStress = stressLogs.length > 0 ? Math.round(stressLogs.reduce((s,l)=>s+l.stress_level,0)/stressLogs.length) : 40;
  const recs = [];

  if (avgStress > 60) {
    recs.push('Your average stress this period is high. Try scheduling at least one full rest day per week — even 24 hours of lighter workload significantly speeds recovery.');
  } else if (avgStress > 35) {
    recs.push('Your stress is in a moderate range. Experiment with a consistent morning routine — even 10 minutes of quiet time before screens can lower baseline stress throughout the day.');
  } else {
    recs.push("Your stress levels are healthy. This is a great time to take on a stretch goal or tackle work you've been putting off.");
  }

  if (completionRate < 40) {
    recs.push('Task completion is below 40%. Try the 2-minute rule: if a task takes less than 2 minutes, do it immediately. This clears mental clutter and builds momentum.');
  } else if (completionRate < 70) {
    recs.push("You're completing about half your tasks. Consider time-blocking — assign specific 60-90 min slots to your top 3 tasks each morning.");
  } else {
    recs.push("Excellent task completion rate! You're highly productive. Make sure you're also taking recovery time — sustainability matters more than short bursts.");
  }

  if (improvement > 10) {
    recs.push('Your stress has improved significantly this period. Identify what changed and double down on it — whether it\'s sleep, exercise, or workload management.');
  } else if (improvement < -10) {
    recs.push('Stress is trending upward. Audit your commitments — are you taking on more than you can handle? Saying no is a productivity strategy too.');
  } else {
    recs.push('Your stress has been fairly consistent. To improve, try one small change: a 15-minute daily walk, earlier bedtime, or a 5-minute end-of-day reflection.');
  }

  if (stressLogs.length < 3) {
    recs.push('You have limited data this period. Scan your stress at least once daily for 7 days — the patterns MindFlow detects become much more accurate and personal.');
  }

  el.innerHTML = recs.map((r, i) => `
    <div class="weekly-rec-card">
      <div class="rec-num">${i+1}</div>
      <p>${r}</p>
    </div>`).join('');
}