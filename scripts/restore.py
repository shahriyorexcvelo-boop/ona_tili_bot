#!/usr/bin/env python3
"""
Ona Tili — Ma'lumotlar bazasini zaxira nusxadan tiklash (Restore) skripti.
========================================================================
Ishlatish:
  python scripts/restore.py backups/backup_20261020_152800.json.gz [--yes]
  python scripts/restore.py backups/backup_20261020_152800.sql.gz [--yes]
"""
from __future__ import annotations
import os
import sys
import gzip
import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass


def parse_db_url(db_url: str):
    parsed = urlparse(db_url)
    return {
        "user": parsed.username or "postgres",
        "password": parsed.password or "",
        "host": parsed.hostname or "localhost",
        "port": str(parsed.port or 5432),
        "dbname": parsed.path.lstrip("/") or "postgres"
    }


def restore_sql_file(db_url: str, file_path: Path) -> bool:
    """SQL fayl yoki SQL.GZ faylni psql yoki psycopg2 orqali tiklash."""
    print(f"📦 SQL formatidagi zaxirani tiklash: {file_path.name}")
    
    if file_path.suffix == ".gz":
        with gzip.open(file_path, "rt", encoding="utf-8") as gz:
            sql_content = gz.read()
    else:
        with open(file_path, "r", encoding="utf-8") as f:
            sql_content = f.read()

    # Agar psql utilitasi bo'lsa
    if shutil.which("psql"):
        info = parse_db_url(db_url)
        env = os.environ.copy()
        if info["password"]:
            env["PGPASSWORD"] = info["password"]

        cmd = [
            "psql",
            "-h", info["host"],
            "-p", info["port"],
            "-U", info["user"],
            "-d", info["dbname"],
            "-v", "ON_ERROR_STOP=1"
        ]

        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            stdout, stderr = proc.communicate(input=sql_content.encode("utf-8"))
            if proc.returncode == 0:
                print("✅ psql orqali tiklash muvaffaqiyatli bajarildi!")
                return True
            else:
                print(f"⚠️ psql xatosi: {stderr.decode('utf-8', errors='ignore')}")
        except Exception as e:
            print(f"⚠️ psql chaqirishda xatolik: {e}")

    # psycopg2 orqali tiklash fallback
    print("🐍 psycopg2 orqali SQL bajarilmoqda...")
    try:
        import psycopg2
        conn = psycopg2.connect(db_url, sslmode="require" if "localhost" not in db_url else "prefer")
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(sql_content)
        cur.close()
        conn.close()
        print("✅ psycopg2 orqali SQL muvaffaqiyatli bajarildi!")
        return True
    except Exception as e:
        print(f"❌ SQL tiklashda xatolik: {e}")
        return False


def restore_json_file(db_url: str, file_path: Path) -> bool:
    """JSON yoki JSON.GZ fayldagi yozuvlarni psycopg2 orqali tiklash."""
    print(f"🐍 JSON formatidagi zaxirani tiklash: {file_path.name}")
    try:
        if file_path.suffix == ".gz":
            with gzip.open(file_path, "rt", encoding="utf-8") as gz:
                data = json.load(gz)
        else:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
    except Exception as e:
        print(f"❌ Zaxira faylini o'qib bo'lmadi: {e}")
        return False

    tables = data.get("tables", {})
    if not tables:
        print("❌ Zaxira faylida hech qanday jadval ma'lumotlari topilmadi!")
        return False

    try:
        import psycopg2
        from psycopg2.extras import execute_batch
    except ImportError:
        print("❌ psycopg2 o'rnatilmagan!")
        return False

    try:
        conn = psycopg2.connect(db_url, sslmode="require" if "localhost" not in db_url else "prefer")
        cur = conn.cursor()

        # Jadvallarni ketma-ketlikda tiklaymiz (FK xavfsizligi)
        order = ["schema_migrations", "users", "tests", "submissions", "submission_drafts", "user_test_drafts", "broadcast_messages"]
        all_tables = [t for t in order if t in tables] + [t for t in tables if t not in order]

        restored_total = 0
        for table in all_tables:
            rows = tables.get(table, [])
            if not rows:
                continue

            columns = list(rows[0].keys())
            cols_joined = ", ".join(columns)
            ph_joined = ", ".join(["%s"] * len(columns))

            # ON CONFLICT DO UPDATE yoki DO NOTHING
            conflict_clause = "ON CONFLICT DO NOTHING"
            if table == "users":
                conflict_clause = "ON CONFLICT (tg_id) DO UPDATE SET fullname = EXCLUDED.fullname, status = EXCLUDED.status, updated_at = EXCLUDED.updated_at"
            elif table == "tests":
                conflict_clause = "ON CONFLICT (id) DO UPDATE SET title = EXCLUDED.title, answers_json = EXCLUDED.answers_json, is_active = EXCLUDED.is_active"
            elif table == "submissions":
                conflict_clause = "ON CONFLICT (user_tg_id, test_id) DO UPDATE SET score = EXCLUDED.score, details_json = EXCLUDED.details_json"
            elif table == "schema_migrations":
                conflict_clause = "ON CONFLICT (version) DO NOTHING"

            query = f"INSERT INTO {table} ({cols_joined}) VALUES ({ph_joined}) {conflict_clause}"

            batch_data = []
            for row in rows:
                batch_data.append([row.get(c) for c in columns])

            execute_batch(cur, query, batch_data, page_size=200)
            restored_total += len(rows)
            print(f"  • {table}: {len(rows)} ta yozuv tiklandi/yangilandi")

        conn.commit()
        cur.close()
        conn.close()
        print(f"🎉 Jami {restored_total} ta yozuv muvaffaqiyatli tiklandi!")
        return True
    except Exception as e:
        print(f"❌ Tiklash jarayonida xatolik: {e}")
        return False


def main():
    if len(sys.argv) < 2:
        print("Foydalanish: python scripts/restore.py <zaxira_fayl_yo'li> [--yes]")
        sys.exit(1)

    backup_file = Path(sys.argv[1])
    if not backup_file.exists():
        print(f"❌ Fayl topilmadi: {backup_file}")
        sys.exit(1)

    auto_confirm = "--yes" in sys.argv or "-y" in sys.argv
    if not auto_confirm:
        confirm = input(f"⚠️ Diqqat! {backup_file.name} fayli ma'lumotlar bazasiga tiklanadi. Davom etasizmi? (ha/yoq): ")
        if confirm.strip().lower() not in ["ha", "yes", "y"]:
            print("Bekor qilindi.")
            sys.exit(0)

    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url:
        print("❌ DATABASE_URL topilmadi!")
        sys.exit(1)

    fname = backup_file.name.lower()
    if ".sql" in fname:
        ok = restore_sql_file(db_url, backup_file)
    else:
        ok = restore_json_file(db_url, backup_file)

    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
