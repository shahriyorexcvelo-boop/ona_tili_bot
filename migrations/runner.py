"""
Idempotent Migratsiya Runner (PostgreSQL va SQLite)
Har bir migratsiya faylini xavfsiz, bir marta va tranzaksiyada bajaradi.
"""
import os
import re
import glob
import time
import logging

log = logging.getLogger("migrations")

def run_migrations(conn, use_postgres: bool = False):
    """Barcha mavjud SQL migratsiyalarni tartib bilan va xavfsiz ishga tushirish."""
    cur = conn.cursor()
    try:
        # Migratsiyalar tarixini saqlovchi jadval
        if use_postgres:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at BIGINT NOT NULL
            )
            """)
        else:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at INTEGER NOT NULL
            )
            """)
        conn.commit()

        # Qo'llanilgan migratsiyalarni olish
        cur.execute("SELECT version FROM schema_migrations")
        applied_versions = set()
        for row in cur.fetchall():
            if isinstance(row, dict):
                applied_versions.add(row.get("version"))
            elif hasattr(row, "keys"):
                applied_versions.add(row["version"])
            else:
                applied_versions.add(row[0])

        migrations_dir = os.path.dirname(os.path.abspath(__file__))
        migration_files = sorted(glob.glob(os.path.join(migrations_dir, "*.sql")))

        for m_path in migration_files:
            v_name = os.path.basename(m_path)
            if v_name in applied_versions:
                continue

            log.info(f"🚀 Yangi migratsiya qo'llanilmoqda: {v_name}")
            with open(m_path, "r", encoding="utf-8") as f:
                sql_content = f.read()

            # PostgreSQL vs SQLite dialekt moslashuvi
            statements = []
            for raw_stmt in sql_content.split(";"):
                stmt = raw_stmt.strip()
                if not stmt:
                    continue
                if not use_postgres:
                    # SQLite moslashuvlari
                    stmt = stmt.replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
                    stmt = stmt.replace("BIGINT", "INTEGER")
                    stmt = stmt.replace("REAL", "REAL")
                    if "ADD COLUMN IF NOT EXISTS" in stmt.upper():
                        stmt = re.sub(r'ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS', 'ADD COLUMN', stmt, flags=re.IGNORECASE)
                statements.append(stmt)

            for stmt in statements:
                try:
                    cur.execute(stmt)
                except Exception as ex:
                    # Agar ustun yoki indeks allaqachon mavjud bo'lsa (idempotentlik)
                    err_msg = str(ex).lower()
                    if "already exists" in err_msg or "duplicate column" in err_msg:
                        continue
                    else:
                        log.warning(f"Migratsiya statement ogohlantirish [{v_name}]: {ex}")

            # Tarixga yozish
            now_ts = int(time.time())
            if use_postgres:
                cur.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (%s, %s)", (v_name, now_ts))
            else:
                cur.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)", (v_name, now_ts))
            conn.commit()
            log.info(f"✅ Migratsiya muvaffaqiyatli yakunlandi: {v_name}")

    except Exception as e:
        log.error(f"❌ Migratsiya bajarishda xatolik: {e}", exc_info=True)
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        raise e
    finally:
        try:
            cur.close()
        except Exception:
            pass
