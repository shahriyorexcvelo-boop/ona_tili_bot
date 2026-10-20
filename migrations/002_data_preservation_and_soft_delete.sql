-- ==============================================================
-- Migratsiya 002: Ma'lumotlarni saqlash va Soft Delete (Idempotent)
-- ==============================================================

-- 1. Foydalanuvchilar jadvali uchun Soft Delete ustunlari
ALTER TABLE users ADD COLUMN IF NOT EXISTS deleted_at BIGINT DEFAULT NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS deleted_by BIGINT DEFAULT NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS previous_status TEXT DEFAULT NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_anonymized INTEGER DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_users_deleted_at ON users(deleted_at);

-- 2. Submissions jadvali uchun arxivlash (is_archived)
ALTER TABLE submissions ADD COLUMN IF NOT EXISTS is_archived INTEGER DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_submissions_is_archived ON submissions(is_archived);

-- 3. Test topshirish jarayonidagi javoblarni avtomatik saqlash (Autosave Drafts)
CREATE TABLE IF NOT EXISTS submission_drafts (
    id SERIAL PRIMARY KEY,
    user_tg_id BIGINT NOT NULL,
    test_id INTEGER NOT NULL,
    question_key TEXT NOT NULL,
    answer_val TEXT NOT NULL,
    updated_at BIGINT NOT NULL,
    UNIQUE (user_tg_id, test_id, question_key)
);

CREATE INDEX IF NOT EXISTS idx_sub_drafts_user_test ON submission_drafts(user_tg_id, test_id);

-- 4. To'liq yechilayotgan test holati qoralamasi (Aggregate Test Draft)
CREATE TABLE IF NOT EXISTS user_test_drafts (
    user_tg_id BIGINT NOT NULL,
    test_id INTEGER NOT NULL,
    answers_json TEXT NOT NULL DEFAULT '{}',
    updated_at BIGINT NOT NULL,
    PRIMARY KEY (user_tg_id, test_id)
);

CREATE INDEX IF NOT EXISTS idx_user_test_drafts_user ON user_test_drafts(user_tg_id);
