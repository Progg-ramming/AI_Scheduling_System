// script.js — Shared authentication & utility logic
// ─────────────────────────────────────────────────────
console.log("JS LOADED");        // temporary work
const API_BASE = 'http://localhost:3000';
function openModelProfile() {
  const user = JSON.parse(localStorage.getItem('user') || 'null');
  const email = user?.email || localStorage.getItem('email') || 'anonymous';
  window.location.href = '/model-profile?uid=' + encodeURIComponent(email);
}
window.openModelProfile = openModelProfile;

// ── UTILS ──────────────────────────────────────────────
function isValidEmail(e) { return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(e); }

function showToast(msg, duration = 3000) {
  const t = document.getElementById('toast');
  if (!t) return;
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), duration);
}

// ── OTP TIMER ──────────────────────────────────────────
let otpInterval;
function startOtpTimer(timerId, confirmBtnId, resendBtnId) {
  let timeLeft = 300;
  const timerEl  = document.getElementById(timerId);
  const confirmEl= document.getElementById(confirmBtnId);
  const resendEl = document.getElementById(resendBtnId);
  clearInterval(otpInterval);
  if(confirmEl) confirmEl.disabled = false;
  if(resendEl)  resendEl.disabled = true;

  otpInterval = setInterval(() => {
    const m = Math.floor(timeLeft / 60), s = timeLeft % 60;
    if(timerEl) timerEl.textContent = `Time remaining: ${m}:${s<10?'0':''}${s}`;
    if(timeLeft <= 0) {
      clearInterval(otpInterval);
      if(timerEl)   timerEl.textContent = 'OTP expired — please resend.';
      if(confirmEl) confirmEl.disabled = true;
      if(resendEl)  resendEl.disabled = false;
    }
    timeLeft--;
  }, 1000);
}

// ── LOGIN ──────────────────────────────────────────────
const loginForm = document.getElementById('loginForm');
if (loginForm) {
  loginForm.addEventListener('submit', async function(e) {
    e.preventDefault();
    const email    = document.getElementById('email').value.trim();
    const password = document.getElementById('password').value;
    console.log("Sending login request...");
    if (!isValidEmail(email)) { showToast('Please enter a valid email'); return; }
    try {
      const res  = await fetch(`${API_BASE}/login`, {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ email, password })
      });
      const data = await res.json();
      if (data.success) {
        localStorage.setItem('user', JSON.stringify(data.user));
        localStorage.setItem('email', data.user.email);
        window.location.href = 'dashboard.html';
      } else { showToast(data.error || 'Login failed'); }
    } catch (err) {
  console.error("LOGIN ERROR:", err);
  showToast('Server error — check console');
}
  });
}

// ── SIGNUP ─────────────────────────────────────────────
const signupForm = document.getElementById('signupForm');
if (signupForm) {
  signupForm.addEventListener('submit', async function(e) {
    e.preventDefault();
    const full_name       = document.getElementById('full_name').value.trim();
    const email           = document.getElementById('email').value.trim();
    const password        = document.getElementById('password').value;
    const confirmPassword = document.getElementById('confirmPassword').value;
    const dob             = document.getElementById('dob').value;
    const gender          = document.getElementById('gender').value;
    const occupation      = document.getElementById('occupation').value.trim();
    const emailErr        = document.getElementById('emailError');

    if (!full_name || !email || !password || !dob || !gender || !occupation) { showToast('All fields are required'); return; }
    if (!isValidEmail(email)) { if(emailErr){emailErr.textContent='Invalid email format.';emailErr.style.display='block';} return; }
    if (password !== confirmPassword) { showToast('Passwords do not match'); return; }
    if (password.length < 6) { showToast('Password must be at least 6 characters'); return; }

    try {
      const res  = await fetch(`${API_BASE}/signup`, {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ full_name, email, password, dob, gender, occupation })
      });
      const data = await res.json();
      if (!data.success) { showToast(data.error || 'Signup failed'); return; }

      // Send OTP via EmailJS
      try {
        await emailjs.send('service_f6z7842', 'template_o5df24s', { email, otp: data.otp });
      } catch (err) { 
        console.warn('EmailJS failed — OTP in console:', data.otp, err);
        showToast('Dev mode: See console for OTP');
      }

      localStorage.setItem('otpEmail', email);
      document.getElementById('signupStep1').style.display = 'none';
      document.getElementById('signupStep2').style.display = 'block';
      startOtpTimer('otpTimer', 'verifyCode', 'resendCode');
      showToast('Verification code sent!');
    } catch { showToast('Signup error — is the server running?'); }
  });
}

// ── VERIFY OTP ────────────────────────────────────────
console.log("VERIFY BUTTON CLICKED");
const verifyBtn = document.getElementById('verifyCode');
if (verifyBtn) {
  verifyBtn.addEventListener('click', async function() {
    const email = localStorage.getItem('otpEmail') || document.getElementById('email')?.value.trim();
    const otp   = document.getElementById('verificationCode').value.trim();
    if (!email || !otp) { showToast('Please enter the verification code'); return; }
    try {
      const res  = await fetch(`${API_BASE}/verify-otp`, {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ email, otp, type: 'signup' })
      });
      const data = await res.json();
      if (data.success) {
        showToast('Account created! Redirecting...');
        localStorage.removeItem('otpEmail');
        setTimeout(() => window.location.href = 'login.html', 1200);
      } else { showToast(data.error || 'Invalid OTP'); }
    } catch { showToast('Verification failed'); }
  });
}

// ── RESEND OTP ────────────────────────────────────────
const resendBtn = document.getElementById('resendCode');
if (resendBtn) {
  resendBtn.addEventListener('click', async function() {
    resendBtn.disabled = true;
    const email = localStorage.getItem('otpEmail');
    try {
      const res  = await fetch(`${API_BASE}/send-otp`, {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ email, type: 'signup' })
      });
      const data = await res.json();
      if (data.success) {
        try { await emailjs.send('service_f6z7842','template_o5df24s',{email, otp: data.otp}); } catch (err) {
            console.warn('EmailJS failed — OTP in console:', data.otp, err);
            showToast('Dev mode: See console for OTP');
        }
        showToast('New code sent!');
        startOtpTimer('otpTimer','verifyCode','resendCode');
      } else { showToast(data.error); }
    } catch { showToast('Resend failed'); }
    setTimeout(() => resendBtn.disabled = false, 30000);
  });
}

// ── LOGOUT (shared) ───────────────────────────────────
const logoutBtn = document.getElementById('logoutBtn');
if (logoutBtn) {
  logoutBtn.addEventListener('click', () => {
    localStorage.removeItem('user'); localStorage.removeItem('email');
    window.location.href = 'login.html';
  });
}

// ── STRESS DEMO (home page) ───────────────────────────
const stressSliderHome = document.getElementById('stressSlider');
if (stressSliderHome) {
  const labels = ['','Very Calm','Calm','Relaxed','Neutral','Slightly Stressed','Moderately Stressed','Stressed','Very Stressed','Overwhelmed','Burnt Out'];
  stressSliderHome.addEventListener('input', function() {
    const out = document.getElementById('stressOut');
    if(out) out.textContent = `Level ${this.value} — ${labels[this.value]||'Neutral'}`;
  });
  const predictBtn = document.getElementById('predictBtn');
  if(predictBtn) {
    predictBtn.addEventListener('click', function() {
      const v = parseInt(stressSliderHome.value);
      if(v<=3) showToast('Low stress — great time for deep work!');
      else if(v<=6) showToast('Moderate stress — pace yourself today.');
      else showToast('High stress — start small, take breaks.');
    });
  }
}