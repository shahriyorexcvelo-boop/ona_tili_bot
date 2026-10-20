-- ==============================================================
-- Migratsiya 001: Asosiy baza sxemasi (PostgreSQL & SQLite)
-- Idempotent (CREATE TABLE IF NOT EXISTS)
-- ==============================================================

-- 1. Foydalanuvchilar jadvali
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    tg_id BIGINT UNIQUE NOT NULL,
    fullname TEXT NOT NULL,
    phone TEXT NOT NULL,
    username TEXT,
    status TEXT DEFAULT 'approved',
    pin_code TEXT,
    registered_at BIGINT NOT NULL
);

-- 2. Testlar jadvali
CREATE TABLE IF NOT EXISTS tests (
    id SERIAL PRIMARY KEY,
    test_code TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    subject TEXT DEFAULT 'Ona tili',
    pdf_file_id TEXT,
    pdf_file_name TEXT,
    answers_json TEXT NOT NULL,
    total_questions INTEGER DEFAULT 45,
    time_limit_min INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    key_access_code TEXT,
    results_published INTEGER DEFAULT 0,
    created_at BIGINT NOT NULL,
    created_by BIGINT DEFAULT 0,
    created_by_name TEXT DEFAULT '',
    scheduled_date TEXT,
    scheduled_start TEXT,
    scheduled_end TEXT,
    auto_notified TEXT,
    youtube_url TEXT,
    stopped_at BIGINT
);

-- 3. Topshirilgan javoblar (Submissions)
CREATE TABLE IF NOT EXISTS submissions (
    id SERIAL PRIMARY KEY,
    test_id INTEGER NOT NULL,
    test_code TEXT NOT NULL,
    user_tg_id BIGINT NOT NULL,
    fullname TEXT NOT NULL,
    phone TEXT NOT NULL,
    answers_json TEXT NOT NULL,
    score REAL NOT NULL,
    max_score REAL DEFAULT 100.0,
    correct_count INTEGER NOT NULL,
    total_count INTEGER DEFAULT 45,
    details_json TEXT NOT NULL,
    submitted_at BIGINT NOT NULL,
    is_late INTEGER DEFAULT 0,
    status TEXT DEFAULT 'accepted',
    reject_reason TEXT DEFAULT ''
);

-- 4. Adminlar jadvali
CREATE TABLE IF NOT EXISTS admins (
    tg_id BIGINT PRIMARY KEY,
    fullname TEXT,
    username TEXT,
    added_by BIGINT,
    created_at BIGINT NOT NULL
);

-- 5. Tizim sozlamalari
CREATE TABLE IF NOT EXISTS system_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 6. Broadcast tarixi va xabarlari
CREATE TABLE IF NOT EXISTS broadcast_history (
    id SERIAL PRIMARY KEY,
    batch_id TEXT UNIQUE NOT NULL,
    sender_tg_id BIGINT DEFAULT 0,
    message_text TEXT DEFAULT '',
    photo_id TEXT DEFAULT '',
    total_sent INTEGER DEFAULT 0,
    is_deleted INTEGER DEFAULT 0,
    created_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS broadcast_messages (
    id SERIAL PRIMARY KEY,
    batch_id TEXT NOT NULL,
    chat_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    status TEXT DEFAULT 'sent',
    created_at BIGINT NOT NULL
);

-- Asosiy indekslar
CREATE INDEX IF NOT EXISTS idx_users_tg_id ON users(tg_id);
CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);
CREATE INDEX IF NOT EXISTS idx_submissions_user_tg_id ON submissions(user_tg_id);
CREATE INDEX IF NOT EXISTS idx_submissions_test_id ON submissions(test_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_submissions_user_test ON submissions(user_tg_id, test_id);
CREATE INDEX IF NOT EXISTS idx_tests_test_code ON tests(test_code);
CREATE INDEX IF NOT EXISTS idx_tests_is_active ON tests(is_active);
CREATE INDEX IF NOT EXISTS idx_bc_msg_batch ON broadcast_messages(batch_id);
CREATE INDEX IF NOT EXISTS idx_bc_msg_chat ON broadcast_messages(chat_id);
CREATE INDEX IF NOT EXISTS idx_bc_hist_created ON broadcast_history(created_at);
