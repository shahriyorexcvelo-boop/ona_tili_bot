-- ==============================================================
-- Migratsiya 003: admin_deleted_users jadvali va blocked holatini tozalash
-- ==============================================================

-- 1. Admin tomonidan o'chirilgan foydalanuvchilar jadvali
CREATE TABLE IF NOT EXISTS admin_deleted_users (
    tg_id BIGINT PRIMARY KEY,
    deleted_at BIGINT NOT NULL,
    deleted_by BIGINT,
    last_request_at BIGINT
);

CREATE INDEX IF NOT EXISTS idx_admin_del_users_req ON admin_deleted_users(last_request_at);

-- 2. Eski 'blocked' holatidagi foydalanuvchilarni 'pending' ga o'tkazish (bloklash butunlay yo'qoladi)
UPDATE users SET status = 'pending' WHERE status = 'blocked';
