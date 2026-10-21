"""
Lokal test serveri (Faqat sinov va load_test uchun).
DATABASE_URL="" (Lokal SQLite) va ALLOW_INSECURE_AUTH=1 bilan ishlaydi.
Production bazaga yoki tashqi tarmoqqa HECH QACHON ulanmaydi.
"""
from __future__ import annotations
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

os.environ["DATABASE_URL"] = ""
os.environ["ALLOW_INSECURE_AUTH"] = "1"
os.environ["USE_WEBHOOK"] = "0"
os.environ["PORT"] = "8089"

import json
from aiohttp import web
import test_db
from handlers.webapp import create_web_app

async def make_app():
    test_db.init_db()
    test_db.preload_all_tests_cache()

    # Baza ichida faol test 1 mavjudligini ta'minlash
    conn = test_db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM tests WHERE id = 1")
        if not cur.fetchone():
            ans = {str(i): "A" for i in range(1, 45)}
            ans["45"] = "50.0"
            cur.execute(
                f"INSERT INTO tests (id, test_code, title, subject, answers_json, is_active, created_at, created_by) "
                f"VALUES (1, 'T101', 'Lokal Sinov Testi', 'Ona tili', {test_db._ph()}, 1, 1000, 8039427064)",
                (json.dumps(ans),)
            )
            conn.commit()
    finally:
        test_db._close_conn(conn)

    test_db.preload_all_tests_cache()
    app = await create_web_app()
    return app

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8089"))
    print(f"🚀 Lokal test serveri ishga tushmoqda: http://127.0.0.1:{port}")
    web.run_app(make_app(), host="127.0.0.1", port=port)
