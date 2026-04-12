// server.js — MindFlow Backend

const express   = require('express');
const cors      = require('cors');
const bodyParser= require('body-parser');
const bcrypt    = require('bcrypt');
const axios     = require('axios');
const multer    = require('multer');
const path      = require('path');
const fs        = require('fs');
const FormData  = require('form-data');
require('dotenv').config();

const app    = express();
const PORT   = 3000;

// Disk storage for logs/videos
const storage = multer.diskStorage({
    destination: (req, file, cb) => cb(null, 'uploads/'),
    filename: (req, file, cb) => {
        const ext = path.extname(file.originalname) || '.webm';
        cb(null, `stress_${Date.now()}_${Math.round(Math.random() * 1e9)}${ext}`);
    }
});
const uploadDisk = multer({ storage });
const uploadMem  = multer({ storage: multer.memoryStorage() });

const verifiedForDeletion = new Set();

const { Pool } = require('pg');
const pool = new Pool({
    user:     process.env.DB_USER,
    host:     process.env.DB_HOST,
    database: process.env.DB_NAME,
    password: process.env.DB_PASSWORD,
    port:     process.env.DB_PORT,
});

const PYTHON_URL = 'http://127.0.0.1:5000';

// ─── AUTO-CREATE TABLES (PostgreSQL) ───────────────────────────────────────────
(async () => {
    try {
        // Ensure stress_logs exists
        await pool.query(`
            CREATE TABLE IF NOT EXISTS stress_logs (
                id           SERIAL PRIMARY KEY,
                user_email   TEXT NOT NULL,
                stress_level INTEGER NOT NULL,
                source       TEXT DEFAULT 'slider',
                face_emotion TEXT,
                voice_emotion TEXT,
                note         TEXT,
                video_url    TEXT,
                logged_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        `);
        // Ensure video_url exists if table already existed
        await pool.query(`ALTER TABLE stress_logs ADD COLUMN IF NOT EXISTS video_url TEXT`);
        // Ensure users table has required columns for OTP/Verification
        await pool.query(`ALTER TABLE users ADD COLUMN IF NOT EXISTS otp TEXT`);
        await pool.query(`ALTER TABLE users ADD COLUMN IF NOT EXISTS verified BOOLEAN DEFAULT false`);
        await pool.query(`ALTER TABLE tasks ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP`);
        await pool.query(`ALTER TABLE tasks ADD COLUMN IF NOT EXISTS category TEXT DEFAULT 'other'`);
        
        // Ensure categories table exists
        await pool.query(`
            CREATE TABLE IF NOT EXISTS categories (
                id          SERIAL PRIMARY KEY,
                user_email  TEXT NOT NULL,
                cat_id      VARCHAR(100) NOT NULL,
                label       VARCHAR(100) NOT NULL,
                icon        TEXT,
                color       TEXT,
                border      TEXT,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_email, cat_id)
            )
        `);
        
        console.log('[DB] Tables and columns ready');

        // ─── AUTO-CLEANUP VIDEOS (Older than 7 days) ──────────────────────────
        setInterval(async () => {
            console.log('[Cleanup] Checking for old videos...');
            const cutoff = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000);
            try {
                const oldLogs = await pool.query(
                    'SELECT video_url FROM stress_logs WHERE logged_at < $1 AND video_url IS NOT NULL',
                    [cutoff]
                );
                for (const row of oldLogs.rows) {
                    const filePath = path.join(process.cwd(), row.video_url);
                    if (fs.existsSync(filePath)) {
                        fs.unlinkSync(filePath);
                        console.log(`[Cleanup] Deleted old video: ${row.video_url}`);
                    }
                }
                // Clear the URLs from DB as well if files are gone
                await pool.query('UPDATE stress_logs SET video_url = NULL WHERE logged_at < $1', [cutoff]);
            } catch (err) { console.error('[Cleanup] Error:', err.message); }
        }, 24 * 60 * 60 * 1000); // Once a day

    } catch(e) {
        console.error('[DB] Table init error:', e.message);
    }
})();

app.use(cors());
app.use(bodyParser.json());
app.use(express.static(process.cwd()));
app.use('/uploads', express.static(path.join(process.cwd(), 'uploads'))); // Static folder for videos
app.get('/', (_req, res) => res.sendFile(path.join(process.cwd(), 'dashboard.html')));

// ─── HEALTH ─────────────────────────────────────────────────────────────────
app.get('/health', (_req, res) => res.json({ ok: true }));



// AUTH ROUTES — keep all your existing signup/login/otp/verify routes here


// ─── SIGNUP ─────────────────────────────────────────────
app.post('/signup', async (req, res) => {
    const { full_name, email, password, dob, gender, occupation } = req.body;

    if (!full_name || !email || !password || !dob || !gender || !occupation) {
        return res.status(400).json({ error: 'All fields required' });
    }

    try {
        const existing = await pool.query('SELECT * FROM users WHERE email=$1', [email]);

        if (existing.rows.length > 0 && existing.rows[0].verified) {
            return res.status(400).json({ error: 'User already exists' });
        }

        const hashed = await bcrypt.hash(password, 10);

        const otp = Math.floor(100000 + Math.random() * 900000).toString();

        await pool.query(
            `INSERT INTO users (full_name,email,password,dob,gender,occupation,otp,verified)
             VALUES ($1,$2,$3,$4,$5,$6,$7,false)
             ON CONFLICT (email) DO UPDATE SET otp=$7`,
            [full_name,email,hashed,dob,gender,occupation,otp]
        );

        res.json({ success: true, otp });

    } catch (err) {
        console.error("Signup error:", err);
        res.status(500).json({ error: 'Signup failed: ' + err.message });
    }
});


// ─── LOGIN ─────────────────────────────────────────────
app.post('/login', async (req, res) => {
    const { email, password } = req.body;

    try {
        const result = await pool.query('SELECT * FROM users WHERE email=$1', [email]);

        if (result.rows.length === 0) {
            return res.json({ success: false, error: 'User not found' });
        }

        const user = result.rows[0];

        const match = await bcrypt.compare(password, user.password);

        if (!match) {
            return res.json({ success: false, error: 'Wrong password' });
        }

        if (!user.verified) {
            return res.json({ success: false, error: 'Verify email first' });
        }

        res.json({
            success: true,
            user: {
                email: user.email,
                full_name: user.full_name
            }
        });

    } catch (err) {
        console.error("Login error:", err);
        res.json({ success: false, error: 'Login failed: ' + err.message });
    }
});

// send OTP to email 
app.post('/send-otp', async (req, res) => {
    const { email } = req.body;

    const otp = Math.floor(100000 + Math.random() * 900000).toString();
    const expiry = new Date(Date.now() + 5 * 60 * 1000);

    console.log("OTP:", otp);
    console.log("Expiry:", expiry);

    try {
        const result = await pool.query(
            'UPDATE users SET otp=$1 WHERE email=$2 RETURNING otp',
            [otp, email]
        );

        console.log("DB RESULT:", result.rows);

        res.json({ success: true, otp });

    } catch (err) {
        console.error(err);
        res.status(500).json({ error: 'Failed to send OTP' });
    }
});


// ─── VERIFY OTP ────────────────────────────────────────
app.post('/verify-otp', async (req, res) => {
    const { email, otp, type } = req.body;

    if (!email || !otp) {
        return res.status(400).json({ error: 'Email and OTP required' });
    }

    try {
        const result = await pool.query(
            'SELECT otp FROM users WHERE email=$1',
            [email]
        );

        if (result.rows.length === 0) {
            return res.json({ success: false, error: 'User not found' });
        }

        const user = result.rows[0];

        // ❌ Check OTP match
        if (user.otp !== otp) {
            return res.json({ success: false, error: 'Invalid OTP' });
        }

        // ✅ Clear OTP after successful verification
        await pool.query(
            'UPDATE users SET otp=NULL WHERE email=$1',
            [email]
        );

        // ✅ Signup verification
        if (type === 'signup') {
            await pool.query(
                'UPDATE users SET verified=true WHERE email=$1',
                [email]
            );
        }

        // ✅ Delete verification
        if (type === 'delete-verify') {
            verifiedForDeletion.add(email);
            return res.json({ success: true });
        }

        res.json({ success: true });

    } catch (err) {
        console.error('Verify OTP error:', err);
        res.status(500).json({ error: 'Server error' });
    }
});

// ─── GET TASKS ──────────────────────────────────────────────────────────────
// NEW: frontend calls GET /get-tasks?email=xxx to load tasks on dashboard
app.get('/get-tasks', async (req, res) => {
    const { email } = req.query;
    if (!email) return res.status(400).json({ error: 'Email required' });
    try {
        const result = await pool.query(
            'SELECT * FROM tasks WHERE user_email = $1 ORDER BY created_at DESC',
            [email]
        );
        res.json({ tasks: result.rows });
    } catch (err) {
        console.error('Get tasks error:', err);
        res.status(500).json({ error: 'Failed to fetch tasks' });
    }
});

// ─── ADD TASK ───────────────────────────────────────────────────────────────
app.post('/add-task', async (req, res) => {
    const { title, deadline, priority, user_email, category } = req.body;
    if (!title || !user_email) return res.status(400).json({ error: 'Title and user required' });
    try {
        const result = await pool.query(
            `INSERT INTO tasks (user_email, title, deadline, priority, status, category)
             VALUES ($1, $2, $3, $4, 'pending', $5) RETURNING id`,
            [user_email, title, deadline, priority || 'medium', category || 'other']
        );
        res.json({ success: true, id: result.rows[0].id, message: 'Task added' });
    } catch (err) {
        console.error('[Persistence] Add task error:', err.message || err);
        if (err.message.includes('column "category" does not exist')) {
            console.error('[Persistence] CRITICAL: Database schema is out of sync. Category column missing.');
        }
        res.status(500).json({ error: 'Failed to add task' });
    }
});

// ─── UPDATE TASK ────────────────────────────────────────────────────────────
app.post('/update-task', async (req, res) => {
    const { taskId, title, deadline, priority, status, user_email, category } = req.body;
    if (!taskId || !title || !user_email) return res.status(400).json({ error: 'Missing fields' });
    try {
        const result = await pool.query(
            `UPDATE tasks SET title=$1, deadline=$2, priority=$3, status=$4, category=$5
             WHERE id=$6 AND user_email=$7 RETURNING id`,
            [title, deadline, priority, status || 'pending', category || 'other', taskId, user_email]
        );
        if (result.rowCount === 0) return res.status(404).json({ error: 'Task not found' });
        res.json({ success: true });
    } catch (err) {
        console.error('Update task error:', err);
        res.status(500).json({ error: 'Failed to update task' });
    }
});

// ─── DELETE TASK ────────────────────────────────────────────────────────────
app.post('/delete-task', async (req, res) => {
    const { taskId, user_email } = req.body;
    if (!taskId) return res.status(400).json({ error: 'TaskId required' });
    try {
        const result = await pool.query(
            'DELETE FROM tasks WHERE id=$1 AND user_email=$2 RETURNING id',
            [taskId, user_email]
        );
        if (result.rowCount === 0) return res.status(404).json({ error: 'Task not found' });
        res.json({ success: true });
    } catch (err) {
        console.error('Delete task error:', err);
        res.status(500).json({ error: 'Failed to delete task' });
    }
});


// ─── CATEGORY PERSISTENCE ──────────────────────────────────────────────────
app.get('/get-categories', async (req, res) => {
    const { email } = req.query;
    if (!email) return res.status(400).json({ error: 'Email required' });
    try {
        const result = await pool.query('SELECT * FROM categories WHERE user_email=$1', [email]);
        res.json({ categories: result.rows });
    } catch (err) {
        console.error('Get categories error:', err);
        res.status(500).json({ error: 'Failed to fetch categories' });
    }
});

app.post('/save-category', async (req, res) => {
    const { userEmail, catId, label, icon, color, border } = req.body;
    if (!userEmail || !catId || !label) return res.status(400).json({ error: 'Missing fields' });
    try {
        await pool.query(
            `INSERT INTO categories (user_email, cat_id, label, icon, color, border)
             VALUES ($1, $2, $3, $4, $5, $6)
             ON CONFLICT (user_email, cat_id) 
             DO UPDATE SET label=$3, icon=$4, color=$5, border=$6`,
            [userEmail, catId, label, icon, color, border]
        );
        res.json({ success: true, message: 'Category saved' });
    } catch (err) {
        console.error('Save category error:', err);
        res.status(500).json({ error: 'Failed to save category' });
    }
});

app.post('/delete-category', async (req, res) => {
    const { userEmail, catId } = req.body;
    if (!userEmail || !catId) return res.status(400).json({ error: 'Missing fields' });
    try {
        await pool.query('DELETE FROM categories WHERE user_email=$1 AND cat_id=$2', [userEmail, catId]);
        res.json({ success: true, message: 'Category removed' });
    } catch (err) {
        console.error('Delete category error:', err);
        res.status(500).json({ error: 'Failed to delete category' });
    }
});

// ─── BULK UPDATE CATEGORY ──────────────────────────────────────────────────
app.post('/bulk-update-category', async (req, res) => {
    const { oldCategory, newCategory, userEmail } = req.body;
    if (!oldCategory || !newCategory || !userEmail) return res.status(400).json({ error: 'Missing fields' });
    try {
        const result = await pool.query(
            'UPDATE tasks SET category=$1 WHERE category=$2 AND user_email=$3',
            [newCategory, oldCategory, userEmail]
        );
        // Also remove the old category definition if it exists in DB
        await pool.query('DELETE FROM categories WHERE user_email=$1 AND cat_id=$2', [userEmail, oldCategory]);
        res.json({ success: true, count: result.rowCount });
    } catch (err) {
        console.error('Bulk update error:', err);
        res.status(500).json({ error: 'Failed to update tasks' });
    }
});

// ═══════════════════════════════════════════════════════════════════════════
// AI ROUTES — these are NEW, connect to your Python ai_bridge.py
// ═══════════════════════════════════════════════════════════════════════════

// ─── ANALYZE STRESS + SCHEDULE TASKS ────────────────────────────────────────
app.post('/analyze', async (req, res) => {
    const { stressLevel, note, userEmail } = req.body;

    // Fetch user's real tasks from DB
    let userTasks = [];
    try {
        const result = await pool.query(
            "SELECT * FROM tasks WHERE user_email=$1 AND status!='done'",
            [userEmail]
        );
        userTasks = result.rows;
    } catch (err) {
        console.error('Task fetch error in /analyze:', err);
    }

    // Call Python AI bridge
    try {
        const pyRes = await axios.post(`${PYTHON_URL}/predict`, {
            stress_level: stressLevel,
            note: note || '',
            tasks: userTasks
        }, { timeout: 8000 });
        return res.json(pyRes.data);
    } catch (pyErr) {
        console.log('[AI] Python bridge unavailable, using fallback:', pyErr.message);
    }

    // Rule-based fallback (no Python needed)
    const isStressed = stressLevel >= 50;
    const priorityW  = { high: 2, medium: 1, low: 0 };
    const sorted = [...userTasks].sort((a, b) =>
        isStressed
            ? priorityW[a.priority||'medium'] - priorityW[b.priority||'medium']
            : priorityW[b.priority||'medium'] - priorityW[a.priority||'medium']
    );
    return res.json({
        title:   isStressed ? 'Take it gentle today' : 'You\'re in great shape!',
        message: isStressed
            ? 'Your stress is elevated. Start with lighter tasks to build momentum and protect your energy. Consider deferring anything high-pressure until tomorrow.'
            : 'You\'re calm and focused — an ideal state for deep work. Tackle your most demanding tasks first while your energy is high.',
        order:  sorted.map(t => t.id),
        defer:  isStressed ? userTasks.filter(t => t.priority==='high').map(t => t.id) : [],
        model:  'fallback'
    });
});

// ─── LOG STRESS READING ───────────────────────────────────────────────────
app.post('/log-stress', async (req, res) => {
    const { email, stressLevel, source, faceEmotion, voiceEmotion, note, videoUrl } = req.body;
    if (!email || stressLevel === undefined) return res.status(400).json({ error: 'email and stressLevel required' });
    try {
        await pool.query(
            `INSERT INTO stress_logs (user_email, stress_level, source, face_emotion, voice_emotion, note, video_url)
             VALUES ($1, $2, $3, $4, $5, $6, $7)`,
            [email, stressLevel, source || 'slider', faceEmotion || null, voiceEmotion || null, note || null, videoUrl || null]
        );
        res.json({ success: true });
    } catch(err) {
        console.error('Log stress error:', err.message);
        res.status(500).json({ error: 'Failed to log stress' });
    }
});

// ─── GET STRESS LOGS ─────────────────────────────────────────────────────
app.get('/stress-logs', async (req, res) => {
    const { email, days } = req.query;
    if (!email) return res.status(400).json({ error: 'email required' });
    const limit = parseInt(days) || 30;
    try {
        const result = await pool.query(
            `SELECT stress_level, source, face_emotion, voice_emotion, note, video_url, logged_at
             FROM stress_logs
             WHERE user_email = $1
               AND logged_at >= NOW() - INTERVAL '1 day' * $2
             ORDER BY logged_at ASC`,
            [email, limit]
        );
        res.json({ logs: result.rows });
    } catch(err) {
        console.error('Get stress logs error:', err.message);
        res.status(500).json({ error: 'Failed to fetch stress logs' });
    }
});



// ─── FACE EMOTION DETECTION ─────────────────────────────────────────────────
// Proxies to Python /detect-face which uses your face_detection.py model
app.post('/detect-face-emotion', async (req, res) => {
    try {
        const pyRes = await axios.post(`${PYTHON_URL}/detect-face`, {}, { timeout: 10000 });
        return res.json(pyRes.data);
    } catch (err) {
        console.log('[Face] Python bridge error:', err.message);
        return res.json({ emotion: 'Neutral', stress_level: 5, source: 'fallback' });
    }
});


// ─── VOICE EMOTION DETECTION ────────────────────────────────────────────────
// Receives audio from browser, forwards to Python /detect-voice
// which uses your voice.py MFCC + model
app.post('/detect-voice-emotion', uploadMem.single('audio'), async (req, res) => {
    if (!req.file) return res.status(400).json({ error: 'No audio file' });
    try {
        const form = new FormData();
        form.append('audio', req.file.buffer, {
            filename:    'voice.wav',
            contentType: req.file.mimetype || 'audio/wav'
        });
        const pyRes = await axios.post(`${PYTHON_URL}/detect-voice`, form, {
            headers: form.getHeaders(),
            timeout: 15000
        });
        return res.json(pyRes.data);
    } catch (err) {
        console.log('[Voice] Python bridge error:', err.message);
        return res.json({ emotion: 'Neutral', stress_level: 5, source: 'fallback' });
    }
});

// ─── COMBINED FACE + VOICE DETECTION ────────────────────────────────────────────
// Receives optional audio and webcam frame from browser
// forwards both to Python /detect-combined
app.post('/detect-combined', uploadMem.fields([{ name: 'audio', maxCount: 1 }, { name: 'frame', maxCount: 1 }]), async (req, res) => {
    try {
        const form = new FormData();
        
        // Attach audio if provided
        if (req.files && req.files['audio']) {
            const audioFile = req.files['audio'][0];
            form.append('audio', audioFile.buffer, {
                filename:    'voice.wav',
                contentType: audioFile.mimetype || 'audio/wav'
            });
        }
        
        // Attach frame if provided
        if (req.files && req.files['frame']) {
            const frameFile = req.files['frame'][0];
            form.append('frame', frameFile.buffer, {
                filename:    'frame.jpg',
                contentType: frameFile.mimetype || 'image/jpeg'
            });
        }

        const pyRes = await axios.post(`${PYTHON_URL}/detect-combined`, form, {
            headers: form.getHeaders(),
            timeout: 60000
        });
        return res.json(pyRes.data);
    } catch (err) {
        console.log('[Combined] Realtime bridge error:', err.message);
        // Smart fallback: return plausible offline estimate
        return res.json({
            face_emotion:    'Neutral',
            face_stress:     40,
            voice_emotion:   'Neutral',
            voice_stress:    40,
            combined_stress: 40,
            source:          'fallback'
        });
    }
});

// ─── UPLOAD STRESS VIDEO ────────────────────────────────────────────────────
app.post('/upload-video', uploadDisk.single('video'), (req, res) => {
    if (!req.file) return res.status(400).json({ error: 'No video file' });
    const videoUrl = `/uploads/${req.file.filename}`;
    res.json({ success: true, videoUrl });
});

// ─── PROFILE ────────────────────────────────────────────────────────────────
app.get('/profile', async (req, res) => {
    const { email } = req.query;
    if (!email) return res.status(400).json({ error: 'Email required' });
    try {
        const result = await pool.query(
            'SELECT full_name, email, dob, gender, occupation FROM users WHERE email=$1 AND verified=true',
            [email]
        );
        if (result.rows.length === 0) return res.status(404).json({ error: 'User not found' });
        res.json({ success: true, user: result.rows[0] });
    } catch (err) {
        res.status(500).json({ error: 'Failed to fetch profile' });
    }
});

// ─── DELETE ACCOUNT ─────────────────────────────────────────────────────────
app.post('/delete-account', async (req, res) => {
    const { email } = req.body;

    if (!email) {
        return res.status(400).json({ error: 'Email required' });
    }

    // 🔐 CHECK OTP VERIFIED
    if (!verifiedForDeletion.has(email)) {
        return res.status(403).json({ error: 'OTP verification required' });
    }

    try {
        // 🗑️ DELETE ASSOCIATED DATA FIRST (Avoids 500 errors from constraints)
        await pool.query('DELETE FROM tasks WHERE user_email=$1', [email]);
        await pool.query('DELETE FROM stress_logs WHERE user_email=$1', [email]);

        const result = await pool.query(
            'DELETE FROM users WHERE email=$1 RETURNING email',
            [email]
        );

        if (result.rowCount === 0) {
            return res.status(404).json({ error: 'User not found' });
        }

        // cleanup
        verifiedForDeletion.delete(email);

        res.json({ success: true });

    } catch (err) {
        console.error("Delete error:", err);
        res.status(500).json({ error: 'Failed to delete account (DB constraint error)' });
    }
});


app.listen(PORT, () => {
    console.log(`\nMindFlow server running at http://localhost:${PORT}`);
    console.log('Python AI bridge should run at http://localhost:5000\n');
});