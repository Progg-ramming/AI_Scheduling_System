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
    video_data BYTEA,              -- Binary video content stored in DB
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
    category TEXT DEFAULT 'other',
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

-- 5. CATEGORIES TABLE
CREATE TABLE IF NOT EXISTS categories (
    id SERIAL PRIMARY KEY,
    user_email TEXT NOT NULL,
    cat_id VARCHAR(100) NOT NULL, -- The unique identifier like 'habit', 'work'
    label VARCHAR(100) NOT NULL,
    icon TEXT,
    color TEXT,
    border TEXT,
    original_cat_id TEXT, -- Tracks if this replaced a default category
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, -- For recency sorting
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_email, cat_id)
);

-- 1. Tracking for renamed defaults
ALTER TABLE categories ADD COLUMN IF NOT EXISTS original_cat_id TEXT;

-- 2. Recency sorting (Move to Top feature)
ALTER TABLE categories ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;


-- Migrations / Fixes for existing databases:
-- ══════════════════════════════════════════════════════════════════════════

-- Ensure video_url exists in stress_logs
DO $$ 
BEGIN 
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='stress_logs' AND column_name='video_url') THEN
        ALTER TABLE stress_logs ADD COLUMN video_url TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='stress_logs' AND column_name='video_data') THEN
        ALTER TABLE stress_logs ADD COLUMN video_data BYTEA;
    END IF;
END $$;

-- Ensure updated_at exists in tasks
DO $$ 
BEGIN 
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='tasks' AND column_name='updated_at') THEN
        ALTER TABLE tasks ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
    END IF;
END $$;

-- Ensure category exists in tasks
DO $$ 
BEGIN 
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='tasks' AND column_name='category') THEN
        ALTER TABLE tasks ADD COLUMN category TEXT DEFAULT 'other';
    END IF;
END $$;