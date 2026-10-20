#!/usr/bin/env python3
"""
Ona Tili — Ma'lumotlar bazasini avtomatlashtirilgan zaxiralash (Backup) skripti.
=============================================================================
DATABASE_URL dan o'qiydi.
1. pg_dump mavjud bo'lsa: to'liq SQL dump olib, gzip bilan siqadi.
2. pg_dump bo'lmasa (masalan engil containerlarda): psycopg2 orqali barcha
   jadvallarni (users, tests, submissions, drafts, broadcast_messages)
   eksport qilib, gzip bilan siqilgan JSON dump yaratadi.
3. Render Cron Job sifatida ishlatish uchun to'liq moslashtirilgan.
"""
from __future__ import annotations
import os
import sys
import gzip
import json
import time
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

# Loyiha ildiz papkasini sys.path ga qo'shish
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

BACKUP_DIR = BASE_DIR / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def parse_db_url(db_url: str):
    parsed = urlparse(db_url)
    return {
        "user": parsed.username or "postgres",
        "password": parsed.password or "",
        "host": parsed.hostname or "localhost",
        "port": str(parsed.port or 5432),
        "dbname": parsed.path.lstrip("/") or "postgres"
    }


def run_pg_dump(db_url: str, output_path: Path) -> bool:
    """Tizimda pg_dump utilitasi mavjud bo'lsa undan foydalanish."""
    if not shutil.which("pg_dump"):
        return False

    print("📦 'pg_dump' utilitasi aniqlandi. To'liq SQL dump olinmoqda...")
    info = parse_db_url(db_url)
    env = os.environ.copy()
    if info["password"]:
        env["PGPASSWORD"] = info["password"]

    cmd = [
        "pg_dump",
        "-h", info["host"],
        "-p", info["port"],
        "-U", info["user"],
        "-d", info["dbname"],
        "--no-owner",
        "--no-privileges",
        "--clean",
        "--if-exists"
    ]

    try:
        with gzip.open(output_path, "wb") as gz_file:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env
            )
            stdout, stderr = process.communicate()
            if process.returncode != 0:
                print(f"⚠️ pg_dump xatosi: {stderr.decode('utf-8', errors='ignore')}")
                return False
            gz_file.write(stdout)
        return True
    except Exception as e:
        print(f"⚠️ pg_dump bajarishda xatolik: {e}")
        return False


def run_python_export(db_url: str, output_path: Path) -> bool:
    """pg_dump bo'lmagan holatda psycopg2 orqali jadvallarni JSON-gzip formatida eksport qilish."""
    print("🐍 Python ichki drayveri (psycopg2) orqali jadvallar eksport qilinmoqda...")
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
    except ImportError:
        print("❌ psycopg2 kutubxonasi o'rnatilmagan! pip install psycopg2-binary buyrug'ini bering.")
        return False

    tables = [
        "users",
        "tests",
        "submissions",
        "submission_drafts",
        "user_test_drafts",
        "broadcast_messages",
        "schema_migrations"
    ]

    dump_data = {
        "created_at": datetime.utcnow().isoformat() + "Z",
        "timestamp": int(time.time()),
        "tables": {}
    }

    try:
        conn = psycopg2.connect(db_url, sslmode="require" if "localhost" not in db_url else "prefer")
        cur = conn.cursor(cursor_factory=RealDictCursor)

        total_rows = 0
        for table in tables:
            try:
                cur.execute(f"SELECT * FROM {table}")
                rows = cur.fetchall()
                # Serializatsiya uchun datetime va maxsus tiplarni stringga o'tkazamiz
                clean_rows = []
                for row in rows:
                    clean_row = {}
                    for k, v in dict(row).items():
                        if isinstance(v, (datetime, time.struct_time)):
                            clean_row[k] = str(v)
                        else:
                            clean_row[k] = v
                    clean_rows.append(clean_row)

                dump_data["tables"][table] = clean_rows
                total_rows += len(clean_rows)
                print(f"  • {table}: {len(clean_rows)} ta yozuv olindi")
            except Exception as te:
                conn.rollback()
                print(f"  • {table}: jadval mavjud emas yoki o'qib bo'lmadi ({te})")

        cur.close()
        conn.close()

        with gzip.open(output_path, "wt", encoding="utf-8") as gz:
            json.dump(dump_data, gz, ensure_ascii=False, indent=2)

        print(f"✅ Jami {total_rows} ta yozuv muvaffaqiyatli saqlandi.")
        return True
    except Exception as e:
        print(f"❌ Python eksportida xatolik: {e}")
        return False


def prune_old_backups(keep_count: int = 30):
    """Eski zaxiralarni tozalash (faqat oxirgi 30 tasini saqlaydi)."""
    backups = sorted(BACKUP_DIR.glob("backup_*.gz"), key=lambda p: p.stat().st_mtime)
    if len(backups) > keep_count:
        to_delete = backups[:-keep_count]
        for f in to_delete:
            try:
                f.unlink()
                print(f"🧹 Eski zaxira o'chirildi: {f.name}")
            except Exception:
                pass


def main():
    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url:
        print("❌ DATABASE_URL topilmadi! Iltimos, .env faylida yoki muhitda DATABASE_URL ni belgilang.")
        sys.exit(1)

    now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    sql_gz_path = BACKUP_DIR / f"backup_{now_str}.sql.gz"
    json_gz_path = BACKUP_DIR / f"backup_{now_str}.json.gz"

    print(f"🚀 Ona Tili DB Zaxira jarayoni boshlandi: {now_str}")

    success = run_pg_dump(db_url, sql_gz_path)
    final_path = sql_gz_path

    if not success:
        if sql_gz_path.exists():
            sql_gz_path.unlink()
        success = run_python_export(db_url, json_gz_path)
        final_path = json_gz_path

    if success and final_path.exists():
        size_kb = round(final_path.stat().st_size / 1024, 2)
        print(f"🎉 Zaxira muvaffaqiyatli yakunlandi!")
        print(f"📁 Fayl: {final_path}")
        print(f"📊 Hajmi: {size_kb} KB")
        prune_old_backups(keep_count=30)
    else:
        print("❌ Zaxira nusxasi yaratilmadi!")
        sys.exit(1)


if __name__ == "__main__":
    main()
