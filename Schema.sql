-- ══════════════════════════════════════════════════════════════════════════
-- MINDFLOW DATABASE SCHEMA
-- Clean setup for MindFlow AI Stress-Aware Task Manager
-- ══════════════════════════════════════════════════════════════════════════
    create database calm_desk_db;
-- 1. USERS TABLE
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    full_name VARCHAR(100) NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    password VARCHAR(255) NOT NULL,
    dob DATE,
    gender VARCHAR(20),
    occupation VARCHAR(50),
    verified BOOLEAN DEFAULT false,
    otp TEXT,
    otp_expiry TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. STRESS LOGS TABLE (Stores AI Scan results & Video links)
CREATE TABLE IF NOT EXISTS stress_logs (
    id SERIAL PRIMARY KEY,
    user_email TEXT NOT NULL,
    stress_level INTEGER NOT NULL,
    source TEXT DEFAULT 'slider', -- 'slider' or 'face_voice_scan'
    face_emotion TEXT,             -- Detected face emotion
    voice_emotion TEXT,            -- Detected voice emotion
    note TEXT,                     -- User's manual note
    video_url TEXT,                -- Path to the stored video report
    logged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3. TASKS TABLE
CREATE TABLE IF NOT EXISTS tasks (
    id SERIAL PRIMARY KEY,
    user_email TEXT NOT NULL,
    title VARCHAR(255) NOT NULL,
    deadline TIMESTAMP,
    priority VARCHAR(50) DEFAULT 'medium',
    status VARCHAR(50) DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 4. TASKS LOGS (For time tracking)
CREATE TABLE IF NOT EXISTS tasks_logs (
    id SERIAL PRIMARY KEY,
    task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
    start_time TIMESTAMP NOT NULL,
    end_time TIMESTAMP,
    duration_minutes INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 5. USER PERSONALIZATION (Weights & Models)
CREATE TABLE IF NOT EXISTS user_personalization (
    user_email VARCHAR(100) PRIMARY KEY REFERENCES users(email) ON DELETE CASCADE,
    weights JSONB NOT NULL,
    ml_model BYTEA, -- Pickled SGD model
    last_ai_order JSONB,
    last_deferred_ids JSONB,
    last_ai_title TEXT,
    last_ai_message TEXT,
    last_stress_used REAL,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 6. USER SCAN STATS (Personalization History)
CREATE TABLE IF NOT EXISTS user_scan_stats (
    id SERIAL PRIMARY KEY,
    user_email VARCHAR(100) REFERENCES users(email) ON DELETE CASCADE,
    timestamp DOUBLE PRECISION,
    hour INTEGER,
    day_of_week INTEGER,
    raw_stress REAL,
    adjusted_stress REAL,
    face_stress REAL,
    voice_stress REAL,
    face_conf REAL,
    voice_conf REAL,
    task_type TEXT,
    task_priority TEXT,
    outcome_rating REAL DEFAULT -1
);

-- ══════════════════════════════════════════════════════════════════════════
-- Migrations / Fixes for existing databases:
-- ══════════════════════════════════════════════════════════════════════════

-- Ensure video_url exists in stress_logs
DO $$ 
BEGIN 
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='stress_logs' AND column_name='video_url') THEN
        ALTER TABLE stress_logs ADD COLUMN video_url TEXT;
    END IF;
END $$;

-- Ensure updated_at exists in tasks
DO $$ 
BEGIN 
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='tasks' AND column_name='updated_at') THEN
        ALTER TABLE tasks ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
    END IF;
END $$;