from __future__ import annotations
"""
Test Tekshirish Tizimi — PostgreSQL Database moduli (psycopg2)
Neon.tech PostgreSQL bilan ishlash uchun to'liq refaktoring qilingan.
SQLite fallback lokal sinov uchun saqlab qolindi.
"""
import json
import os
import re
import time
import math
import inspect
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any, Tuple
import sqlite3

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

UZB_TZ = timezone(timedelta(hours=5))
ADMIN_ID = int(os.getenv("ADMIN_ID", "8039427064"))

if os.getenv("LOCAL_SAFE_MODE") == "1":
    os.environ["DATABASE_URL"] = ""

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# Neon.tech uchun: agar URL da '-pooler' bo'lmasa, uni avtomatik '-pooler' (PgBouncer) rejimiga o'tkazish
# Bu 'Max connections' (ulanuvchilar soni chegarasi) xatoligini to'liq bartaraf qiladi.
if DATABASE_URL and "neon.tech" in DATABASE_URL and "-pooler" not in DATABASE_URL:
    DATABASE_URL = re.sub(r'(@[a-zA-Z0-9_\-]+)(?<!\-pooler)(\.[a-zA-Z0-9_\.\-]*neon\.tech)', r'\1-pooler\2', DATABASE_URL)
    os.environ["DATABASE_URL"] = DATABASE_URL
    print("ℹ️ DATABASE_URL avtomatik Neon PgBouncer (-pooler) rejimiga ulandi.")

# PostgreSQL yoki SQLite ni avtomatik aniqlash
USE_POSTGRES = bool(DATABASE_URL and ("postgresql" in DATABASE_URL or "postgres" in DATABASE_URL))

class DatabasePoolBusyError(Exception):
    """PostgreSQL connection pool to'liq va 2 soniyada ulanish ololmaganda ko'tariladi."""
    pass

# ── PostgreSQL Connection Pool (Yuqori yuklama uchun optimallashtirilgan) ──
_pg_pool = None
_pool_connections = set()
_conn_last_used = {}
PG_POOL_MINCONN = int(os.getenv("PG_POOL_MINCONN", "1"))
PG_POOL_MAXCONN = int(os.getenv("PG_POOL_MAXCONN", "5"))

# ── ASYNC DB HELPER (Non-blocking ThreadPoolExecutor) ──
import asyncio
import functools
from concurrent.futures import ThreadPoolExecutor

# Chegaralangan ThreadPoolExecutor: thread soni pool hajmidan oshmasligi shart (executor <= pool - 1)
_DB_DEFAULT_WORKERS = min(4, max(1, PG_POOL_MAXCONN - 1))
_DB_EXECUTOR_WORKERS = int(os.getenv("DB_EXECUTOR_WORKERS", str(_DB_DEFAULT_WORKERS)))
_DB_EXECUTOR_WORKERS = min(_DB_EXECUTOR_WORKERS, max(1, PG_POOL_MAXCONN - 1))
_db_executor = ThreadPoolExecutor(max_workers=_DB_EXECUTOR_WORKERS, thread_name_prefix="db_pool_worker")

async def db(func, *args, **kwargs):
    """
    Sinxron test_db funksiyalarini alohida thread pool da ishga tushirish uchun yagona async yordamchi.
    Aiogram / Aiohttp bitta umumiy event loop da ishlayotganda event loop ni bloklashdan to'liq himoya qiladi.
    """
    if inspect.iscoroutinefunction(func):
        return await func(*args, **kwargs)
    loop = asyncio.get_running_loop()
    if kwargs:
        pfunc = functools.partial(func, *args, **kwargs)
        return await loop.run_in_executor(_db_executor, pfunc)
    return await loop.run_in_executor(_db_executor, func, *args)

# ── ZERO-DB-READ ARXITEKTURASI (Global Memory Cache) ──
# 1,000+ o'quvchi bir vaqtda kirganda bazaga yuklama bermaslik uchun
# barcha testlar, variantlar va kalitlar server xotirasida (RAM) saqlanadi.
import threading
_RAM_CACHE_LOCK = threading.Lock()

_GLOBAL_TESTS_CACHE: Dict[int, Dict[str, Any]] = {}
_GLOBAL_TESTS_BY_CODE: Dict[str, int] = {}
_GLOBAL_ALL_TESTS_LIST: List[Dict[str, Any]] = []
_GLOBAL_ACTIVE_TESTS_LIST: List[Dict[str, Any]] = []
_GLOBAL_TESTS_PRELOADED: bool = False
_GLOBAL_TESTS_CACHE_TIME: float = 0.0
_CACHE_TEST_BOUNDS: Dict[str, Any] = {}
_CACHE_TEST_BOUNDS_TIME: float = 0.0
_USER_SUBMISSIONS_MAP_CACHE: Dict[int, Any] = {}
_USER_SUBMISSIONS_CACHE: Dict[int, Tuple[float, List[Dict[str, Any]]]] = {}
_USER_SUBMITTED_IDS_CACHE: Dict[Any, Any] = {}
_CACHE_USERS: Dict[int, Any] = {}
_CACHE_SETTINGS: Dict[str, Any] = {}
_ADMIN_DELETED_CACHE: Dict[int, Any] = {}
_ALL_ADMINS_CACHE: Tuple[float, List[Dict[str, Any]]] = (0.0, [])

# 5 soniyalik tezkor xotira keshlari (CPU/Event loop optimallashtirish)
_MAINTENANCE_CACHE: Tuple[float, bool] = (0.0, False)
_ACTIVE_TEST_CACHE: Tuple[float, bool] = (0.0, False)
CACHE_TTL_5S = 5.0

def invalidate_maintenance_cache():
    """Texnik profilaktika keshini bekor qilish."""
    global _MAINTENANCE_CACHE
    with _RAM_CACHE_LOCK:
        _MAINTENANCE_CACHE = (0.0, False)

def invalidate_active_test_cache():
    """Faol testlar mavjudligi keshini bekor qilish."""
    global _ACTIVE_TEST_CACHE
    with _RAM_CACHE_LOCK:
        _ACTIVE_TEST_CACHE = (0.0, False)

def _ensure_tests_cache_loaded():
    """
    Kesh umuman yuklanmagan bo'lsagina bir marta sinxron yuklaydi.
    Kesh eskirgan bo'lsa ham eski keshni qaytaradi (test_bot dagi fon vazifasi yangilab turadi).
    """
    with _RAM_CACHE_LOCK:
        loaded = _GLOBAL_TESTS_PRELOADED
    if not loaded:
        preload_all_tests_cache()

# Kesh siyosati (M3): DB — yagona haqiqat manbai. 
# 30 soniyalik Read-Through TTL bir nechta instance larda kesh nomutanosibligining oldini oladi.
# Local yozish amallari darhol invalidate qiladi.
CACHE_TTL_TESTS = 30.0       # Read-through RAM kesh TTL: 30 soniya (Mini App talabi)
CACHE_TTL_USER_PROFILE = 5.0 # Foydalanuvchi profili va natijalari TTL: 5 soniya
CACHE_TTL_USERS = 5.0       # 5 soniya
CACHE_TTL_SETTINGS = 300.0  # 5 daqiqa
CACHE_TTL_ADMINS = 300.0    # 5 daqiqa
CACHE_TTL_ADMIN_DELETED = 300.0 # 5 daqiqa

def is_tests_cache_valid() -> bool:
    """RAM kesh yuklangan va TTL (45 soniya) muddati o'tmaganligini tekshirish."""
    with _RAM_CACHE_LOCK:
        return _GLOBAL_TESTS_PRELOADED and (time.time() - _GLOBAL_TESTS_CACHE_TIME) < CACHE_TTL_TESTS

def preload_all_tests_cache() -> int:
    """
    Zero-DB-Read: Server ishga tushganda va TTL muddati tugaganda barcha testlar
    bazadan o'qilib, serverning RAM xotirasiga joylanadi.
    DB yagona haqiqat manbai (Source of Truth) hisoblanadi.
    """
    global _GLOBAL_TESTS_PRELOADED, _GLOBAL_ALL_TESTS_LIST, _GLOBAL_ACTIVE_TESTS_LIST, _GLOBAL_TESTS_CACHE_TIME
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM tests ORDER BY id DESC")
        rows = cur.fetchall()
        all_tests = [_row_to_dict(r) for r in rows if r]

        with _RAM_CACHE_LOCK:
            _GLOBAL_TESTS_CACHE.clear()
            _GLOBAL_TESTS_BY_CODE.clear()
            _GLOBAL_ALL_TESTS_LIST = all_tests
            _GLOBAL_ACTIVE_TESTS_LIST = [t for t in all_tests if t.get("is_active") == 1]

            for t in all_tests:
                tid = t["id"]
                _GLOBAL_TESTS_CACHE[tid] = t
                if t.get("test_code"):
                    _GLOBAL_TESTS_BY_CODE[str(t["test_code"]).upper().strip().replace("#", "")] = tid

            _GLOBAL_TESTS_PRELOADED = True
            _GLOBAL_TESTS_CACHE_TIME = time.time()

        print(f"🚀 [Read-Through Kesh] Global RAM kesh yangilandi: {len(all_tests)} ta test xotirada tayyor (TTL: {CACHE_TTL_TESTS}s)")
        return len(all_tests)
    except Exception as e:
        print(f"❌ preload_all_tests_cache xatolik: {e}")
        return 0
    finally:
        _close_conn(conn)

def invalidate_test_cache(test_id: Optional[int] = None):
    """Test ma'lumotlari yangilanganda yoki o'chirilganda RAM keshni tozalash va qayta yuklash"""
    global _CACHE_TEST_BOUNDS_TIME, _GLOBAL_ALL_TESTS_LIST, _GLOBAL_ACTIVE_TESTS_LIST, _GLOBAL_TESTS_CACHE_TIME
    invalidate_active_test_cache()
    with _RAM_CACHE_LOCK:
        _GLOBAL_TESTS_CACHE_TIME = 0.0
        _CACHE_TEST_BOUNDS.clear()
        _CACHE_TEST_BOUNDS_TIME = 0.0
        _USER_SUBMISSIONS_MAP_CACHE.clear()
        _USER_SUBMISSIONS_CACHE.clear()
        _USER_SUBMITTED_IDS_CACHE.clear()
        if test_id is not None:
            tid = int(test_id)
            _GLOBAL_TESTS_CACHE.pop(tid, None)
            codes_to_del = [c for c, id_ in list(_GLOBAL_TESTS_BY_CODE.items()) if id_ == tid]
            for c in codes_to_del:
                _GLOBAL_TESTS_BY_CODE.pop(c, None)
            _GLOBAL_ALL_TESTS_LIST = [t for t in _GLOBAL_ALL_TESTS_LIST if t.get("id") != tid]
            _GLOBAL_ACTIVE_TESTS_LIST = [t for t in _GLOBAL_ACTIVE_TESTS_LIST if t.get("id") != tid]
        else:
            _GLOBAL_TESTS_CACHE.clear()
            _GLOBAL_TESTS_BY_CODE.clear()
            _GLOBAL_ALL_TESTS_LIST = []
            _GLOBAL_ACTIVE_TESTS_LIST = []
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(db(preload_all_tests_cache))
    except RuntimeError:
        try:
            preload_all_tests_cache()
        except Exception as e:
            print(f"Error reloading tests cache in invalidate_test_cache: {e}")

def invalidate_user_cache(tg_id: Optional[int] = None):
    """Foydalanuvchi ma'lumotlari yangilanganda RAM keshni tozalash"""
    with _RAM_CACHE_LOCK:
        if tg_id is not None:
            _CACHE_USERS.pop(int(tg_id), None)
            _USER_SUBMISSIONS_MAP_CACHE.pop(int(tg_id), None)
            _USER_SUBMISSIONS_CACHE.pop(int(tg_id), None)
            # Ushbu userning topshirganlik keshini tozalash
            to_del = [k for k in _USER_SUBMITTED_IDS_CACHE.keys() if k[0] == int(tg_id)]
            for k in to_del:
                _USER_SUBMITTED_IDS_CACHE.pop(k, None)
        else:
            _CACHE_USERS.clear()
            _USER_SUBMISSIONS_MAP_CACHE.clear()
            _USER_SUBMISSIONS_CACHE.clear()
            _USER_SUBMITTED_IDS_CACHE.clear()

def invalidate_settings_cache():
    with _RAM_CACHE_LOCK:
        _CACHE_SETTINGS.clear()
    invalidate_maintenance_cache()


_on_schedule_changed_callbacks = []

def register_schedule_changed_callback(cb):
    """Jadval o'zgarganda xabardor qiluvchi callbacklarni ro'yxatga olish."""
    if cb not in _on_schedule_changed_callbacks:
        _on_schedule_changed_callbacks.append(cb)

def _notify_schedule_changed_internal():
    for cb in _on_schedule_changed_callbacks:
        try:
            cb()
        except Exception:
            pass

def _get_pg_pool():
    """PostgreSQL connection pool — bir marta yaratiladi, qayta ishlatiladi.
    minconn=1, maxconn=5: Supabase transaction pooler (port 6543) uchun optimallashtirilgan.
    """
    global _pg_pool
    if _pg_pool is None and USE_POSTGRES:
        try:
            import psycopg2.pool
            _pg_pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=PG_POOL_MINCONN,
                maxconn=PG_POOL_MAXCONN,
                dsn=DATABASE_URL,
                connect_timeout=10,
                keepalives=1,
                keepalives_idle=60,
                keepalives_interval=30,
                keepalives_count=3,
                options="-c statement_timeout=8000"
            )
        except Exception as e:
            print(f"Connection pool xatolik: {e}")
            _pg_pool = None
    return _pg_pool

def _close_conn(conn):
    """Ulanishni pool ga qaytarish yoki yopish (Connection Leak va Abort tranzaksiyalarning oldini oladi)."""
    if conn is None:
        return
    conn_id = id(conn)
    if USE_POSTGRES and conn_id in _pool_connections:
        _pool_connections.discard(conn_id)
        _conn_last_used[conn_id] = time.time()
        pool = _get_pg_pool()
        if pool:
            try:
                # Agar tranzaksiya ochiq yoki xatolik bilan to'xtab qolgan bo'lsa, rollback qilib tozalash
                if hasattr(conn, "closed") and not conn.closed:
                    if not getattr(conn, "autocommit", False):
                        try:
                            conn.rollback()
                        except Exception:
                            pass
                pool.putconn(conn)
                return
            except Exception:
                pass
    try:
        if hasattr(conn, "closed"):
            if not conn.closed:
                conn.close()
        else:
            conn.close()
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

data_dir = os.getenv("DATA_DIR")
if data_dir:
    try:
        os.makedirs(data_dir, exist_ok=True)
    except Exception:
        pass
    DB_FILE = os.path.join(data_dir, "test_system.db")
else:
    db_path_env = os.getenv("DB_PATH", "").strip()
    if db_path_env:
        DB_FILE = db_path_env if os.path.isabs(db_path_env) else os.path.join(BASE_DIR, db_path_env)
    else:
        DB_FILE = os.path.join(BASE_DIR, "test_system.db")

_db_initialized = False

def ensure_db_initialized():
    """Baza jadvallari yaratilganligini tekshirish va lozim bo'lsa darhol yaratish."""
    global _db_initialized
    if _db_initialized:
        return
    try:
        init_db()
    except Exception as e:
        print(f"Baza initializatsiya xatosi: {e}")


def format_uzb_time(timestamp: Optional[float] = None, fmt: str = "%d.%m.%Y %H:%M") -> str:
    """O'zbekiston (Toshkent, UTC+5) vaqti bo'yicha formatlash"""
    if timestamp is None:
        dt = datetime.now(UZB_TZ)
    else:
        dt = datetime.fromtimestamp(timestamp, tz=UZB_TZ)
    return dt.strftime(fmt)


# ──────────────────────────────────────────────────────────
# ULANISH MENEJERI
# ──────────────────────────────────────────────────────────

def get_connection():
    """PostgreSQL yoki SQLite ulanishini qaytaradi (pool orqali)."""
    if USE_POSTGRES:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        pool = _get_pg_pool()
        if pool:
            # Yuqori yuklamada pool to'lib qolsa, thread ichida qisqa kutish bilan qayta urinish.
            # Umumiy kutish vaqti 2.0 soniyadan oshmaydi va ulanish olinmasa DatabasePoolBusyError ko'tariladi.
            start_wait = time.time()
            sleep_time = 0.04
            while True:
                try:
                    conn = pool.getconn()
                    # O'lik / uzilgan ulanishni aniqlab yangilash
                    if hasattr(conn, "closed") and conn.closed:
                        try:
                            pool.putconn(conn, close=True)
                        except Exception:
                            pass
                        conn = pool.getconn()

                    conn_id = id(conn)
                    _conn_last_used[conn_id] = time.time()
                    conn.cursor_factory = RealDictCursor
                    conn.autocommit = False
                    _pool_connections.add(conn_id)
                    return conn
                except Exception as pe:
                    import psycopg2.pool
                    if isinstance(pe, psycopg2.pool.PoolError):
                        elapsed = time.time() - start_wait
                        if elapsed + sleep_time <= 2.0:
                            time.sleep(sleep_time)
                            sleep_time = min(sleep_time * 1.5, 0.2)
                            continue
                        raise DatabasePoolBusyError(f"Server band: connection pool to'liq (2 soniya kutildi): {pe}") from pe
                    raise
        else:
            raise DatabasePoolBusyError("Server band: PostgreSQL connection pool mavjud emas yoki ulanib bo'lmadi")
    else:
        conn = sqlite3.connect(DB_FILE, timeout=20.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=20000;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        global _db_initialized
        if not _db_initialized:
            try:
                cur_c = conn.cursor()
                cur_c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'")
                if not cur_c.fetchone():
                    _db_initialized = True
                    _init_sqlite_tables_on_conn(conn)
                else:
                    _db_initialized = True
            except Exception:
                pass
        return conn


def _row_to_dict(row) -> Optional[Dict[str, Any]]:
    """psycopg2 RealDictRow yoki sqlite3.Row ni dict ga o'tkazish."""
    if row is None:
        return None
    return dict(row)


def _commit_and_close(conn):
    """Commit qilib, ulanishni pool ga qaytarish (yoki yopish)."""
    try:
        conn.commit()
    finally:
        _close_conn(conn)


def ping_db() -> bool:
    """Supabase/PostgreSQL ulanishini tirik saqlash (Keep-Alive) uchun SELECT 1 tekshiruvi."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1")
        row = cur.fetchone()
        return bool(row)
    except Exception as e:
        print(f"ping_db xatolik: {e}")
        return False
    finally:
        _close_conn(conn)


def _placeholder(n: int = 1) -> str:
    """PostgreSQL uchun %s, SQLite uchun ? placeholder qaytaradi."""
    ph = "%s" if USE_POSTGRES else "?"
    if n == 1:
        return ph
    return ", ".join([ph] * n)


def _ph() -> str:
    """Bitta placeholder."""
    return "%s" if USE_POSTGRES else "?"


# ──────────────────────────────────────────────────────────
# JADVALLARNI YARATISH
# ──────────────────────────────────────────────────────────

def _init_sqlite_tables_on_conn(conn):
    """SQLite uchun barcha jadvallar va indekslarni xavfsiz yaratish hamda yangilash."""
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_id INTEGER UNIQUE NOT NULL,
        fullname TEXT NOT NULL,
        phone TEXT NOT NULL,
        username TEXT,
        status TEXT DEFAULT 'approved',
        pin_code TEXT,
        registered_at INTEGER NOT NULL,
        deleted_at INTEGER DEFAULT NULL,
        deleted_by INTEGER DEFAULT NULL,
        previous_status TEXT DEFAULT NULL,
        is_anonymized INTEGER DEFAULT 0
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS tests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
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
        created_at INTEGER NOT NULL,
        created_by INTEGER DEFAULT 0,
        created_by_name TEXT DEFAULT '',
        scheduled_date TEXT,
        scheduled_start TEXT,
        scheduled_end TEXT,
        auto_notified TEXT,
        youtube_url TEXT,
        stopped_at INTEGER
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        test_id INTEGER NOT NULL,
        test_code TEXT NOT NULL,
        user_tg_id INTEGER NOT NULL,
        fullname TEXT NOT NULL,
        phone TEXT NOT NULL,
        answers_json TEXT NOT NULL,
        score REAL NOT NULL,
        max_score REAL DEFAULT 100.0,
        correct_count INTEGER NOT NULL,
        total_count INTEGER DEFAULT 45,
        details_json TEXT NOT NULL,
        submitted_at INTEGER NOT NULL,
        is_late INTEGER DEFAULT 0,
        status TEXT DEFAULT 'accepted',
        reject_reason TEXT DEFAULT '',
        is_archived INTEGER DEFAULT 0,
        FOREIGN KEY(test_id) REFERENCES tests(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS submission_drafts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_tg_id INTEGER NOT NULL,
        test_id INTEGER NOT NULL,
        question_key TEXT NOT NULL,
        answer_val TEXT NOT NULL,
        updated_at INTEGER NOT NULL,
        UNIQUE (user_tg_id, test_id, question_key)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_test_drafts (
        user_tg_id INTEGER NOT NULL,
        test_id INTEGER NOT NULL,
        answers_json TEXT NOT NULL DEFAULT '{}',
        updated_at INTEGER NOT NULL,
        PRIMARY KEY (user_tg_id, test_id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS admins (
        tg_id INTEGER PRIMARY KEY,
        fullname TEXT,
        username TEXT,
        added_by INTEGER,
        created_at INTEGER NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS system_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS broadcast_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id TEXT UNIQUE NOT NULL,
        sender_tg_id INTEGER DEFAULT 0,
        message_text TEXT DEFAULT '',
        photo_id TEXT DEFAULT '',
        total_sent INTEGER DEFAULT 0,
        is_deleted INTEGER DEFAULT 0,
        status TEXT DEFAULT 'running',
        created_at INTEGER NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS broadcast_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id TEXT NOT NULL,
        chat_id INTEGER NOT NULL,
        message_id INTEGER NOT NULL,
        status TEXT DEFAULT 'sent',
        created_at INTEGER NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS admin_deleted_users (
        tg_id INTEGER PRIMARY KEY,
        deleted_at INTEGER NOT NULL,
        deleted_by INTEGER,
        last_request_at INTEGER
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_admin_del_users_req ON admin_deleted_users(last_request_at)")

    cur.execute("""
    INSERT OR IGNORE INTO admins (tg_id, fullname, username, added_by, created_at)
    VALUES (8039427064, 'Bosh Admin', 'admin', 0, 1789300000)
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_users_tg_id ON users(tg_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_users_status ON users(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_submissions_user_tg_id ON submissions(user_tg_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_submissions_test_id ON submissions(test_id)")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_submissions_user_test ON submissions(user_tg_id, test_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tests_test_code ON tests(test_code)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tests_is_active ON tests(is_active)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bc_msg_batch ON broadcast_messages(batch_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bc_msg_chat ON broadcast_messages(chat_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bc_hist_created ON broadcast_history(created_at)")

    for col, coltype in [
        ("scheduled_date", "TEXT"),
        ("scheduled_start", "TEXT"),
        ("scheduled_end", "TEXT"),
        ("created_by", "INTEGER"),
        ("created_by_name", "TEXT"),
        ("auto_notified", "TEXT"),
        ("youtube_url", "TEXT"),
        ("stopped_at", "INTEGER"),
    ]:
        try:
            cur.execute(f"ALTER TABLE tests ADD COLUMN {col} {coltype} DEFAULT NULL")
            conn.commit()
        except Exception:
            pass

    for col, coltype in [
        ("is_late", "INTEGER DEFAULT 0"),
        ("status", "TEXT DEFAULT 'accepted'"),
        ("reject_reason", "TEXT DEFAULT ''"),
    ]:
        try:
            cur.execute(f"ALTER TABLE submissions ADD COLUMN {col} {coltype}")
            conn.commit()
        except Exception:
            pass

    try:
        cur.execute("ALTER TABLE broadcast_history ADD COLUMN status TEXT DEFAULT 'running'")
        conn.commit()
    except Exception:
        pass

    conn.commit()


def init_db():
    global _db_initialized
    _db_initialized = True
    conn = get_connection()
    try:
        # 1. Versiyalangan xavfsiz va idempotent migratsiyalarni ishga tushirish
        try:
            from migrations.runner import run_migrations
            run_migrations(conn, use_postgres=USE_POSTGRES)
        except Exception as mig_err:
            print(f"Migratsiya xatoligi: {mig_err}")

        cur = conn.cursor()

        if USE_POSTGRES:
            # PostgreSQL jadvallar (asosiy sxema)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                tg_id BIGINT UNIQUE NOT NULL,
                fullname TEXT NOT NULL,
                phone TEXT NOT NULL,
                username TEXT,
                status TEXT DEFAULT 'approved',
                pin_code TEXT,
                registered_at BIGINT NOT NULL,
                deleted_at BIGINT DEFAULT NULL,
                deleted_by BIGINT DEFAULT NULL,
                previous_status TEXT DEFAULT NULL,
                is_anonymized INTEGER DEFAULT 0
            )
            """)

            cur.execute("""
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
            )
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                id SERIAL PRIMARY KEY,
                test_id INTEGER NOT NULL REFERENCES tests(id),
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
                reject_reason TEXT DEFAULT '',
                is_archived INTEGER DEFAULT 0
            )
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                tg_id BIGINT PRIMARY KEY,
                fullname TEXT,
                username TEXT,
                added_by BIGINT,
                created_at BIGINT NOT NULL
            )
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS system_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS broadcast_history (
                id SERIAL PRIMARY KEY,
                batch_id TEXT UNIQUE NOT NULL,
                sender_tg_id BIGINT DEFAULT 0,
                message_text TEXT DEFAULT '',
                photo_id TEXT DEFAULT '',
                total_sent INTEGER DEFAULT 0,
                is_deleted INTEGER DEFAULT 0,
                status TEXT DEFAULT 'running',
                created_at BIGINT NOT NULL
            )
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS broadcast_messages (
                id SERIAL PRIMARY KEY,
                batch_id TEXT NOT NULL,
                chat_id BIGINT NOT NULL,
                message_id BIGINT NOT NULL,
                status TEXT DEFAULT 'sent',
                created_at BIGINT NOT NULL
            )
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS submission_drafts (
                id SERIAL PRIMARY KEY,
                user_tg_id BIGINT NOT NULL,
                test_id INTEGER NOT NULL,
                question_key TEXT NOT NULL,
                answer_val TEXT NOT NULL,
                updated_at BIGINT NOT NULL,
                UNIQUE (user_tg_id, test_id, question_key)
            )
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS user_test_drafts (
                user_tg_id BIGINT NOT NULL,
                test_id INTEGER NOT NULL,
                answers_json TEXT NOT NULL DEFAULT '{}',
                updated_at BIGINT NOT NULL,
                PRIMARY KEY (user_tg_id, test_id)
            )
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS admin_deleted_users (
                tg_id BIGINT PRIMARY KEY,
                deleted_at BIGINT NOT NULL,
                deleted_by BIGINT,
                last_request_at BIGINT
            )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_admin_del_users_req ON admin_deleted_users(last_request_at)")

            # Bosh adminni qo'shish (ON CONFLICT — PostgreSQL)
            cur.execute("""
            INSERT INTO admins (tg_id, fullname, username, added_by, created_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (tg_id) DO NOTHING
            """, (8039427064, 'Bosh Admin', 'admin', 0, 1789300000))

            # PostgreSQL indekslar (Tezkor qidiruv va yuklamaga chidamlilik)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_users_tg_id ON users(tg_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_users_status ON users(status)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_users_deleted_at ON users(deleted_at)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_submissions_user_tg_id ON submissions(user_tg_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_submissions_test_id ON submissions(test_id)")
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_submissions_user_test ON submissions(user_tg_id, test_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_tests_test_code ON tests(test_code)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_tests_is_active ON tests(is_active)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_sub_drafts_user_test ON submission_drafts(user_tg_id, test_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_bc_msg_batch ON broadcast_messages(batch_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_bc_msg_chat ON broadcast_messages(chat_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_bc_hist_created ON broadcast_history(created_at)")

            conn.commit()
        else:
            _init_sqlite_tables_on_conn(conn)
    finally:
        _close_conn(conn)


# ──────────────────────────────────────────────────────────
# TEST JADVAL VAQT BOSHQARUVI (SCHEDULER)
# ──────────────────────────────────────────────────────────

def set_test_schedule(test_id: int, scheduled_date: str, start_time: str, end_time: str) -> bool:
    """Test uchun avtomatik boshlanish/tugash vaqtini o'rnatish.
    scheduled_date: "26.09.2026", start_time: "19:30", end_time: "22:00" (UZB vaqt)
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE tests SET scheduled_date={_ph()}, scheduled_start={_ph()}, scheduled_end={_ph()}, auto_notified='' WHERE id={_ph()}",
            (scheduled_date, start_time, end_time, test_id)
        )
        conn.commit()
        invalidate_test_cache(test_id)
        _notify_schedule_changed_internal()
        try:
            from core import invalidate_dashboard_cache
            invalidate_dashboard_cache()
        except Exception:
            pass
        return True
    except Exception as e:
        print(f"Error set_test_schedule: {e}")
        return False
    finally:
        _close_conn(conn)


def mark_test_auto_notified(test_id: int, stage: str) -> bool:
    """Belgilangan bosqich (30m, 10m, started, 15m) xabari yuborilganini belgilash."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT auto_notified FROM tests WHERE id = {_ph()}", (test_id,))
        row = cur.fetchone()
        cur_val = (row[0] if isinstance(row, (list, tuple)) else row.get('auto_notified')) if row else ""
        cur_val = cur_val or ""
        stages = set(s for s in cur_val.split(",") if s)
        stages.add(stage)
        new_val = ",".join(stages)
        cur.execute(f"UPDATE tests SET auto_notified = {_ph()} WHERE id = {_ph()}", (new_val, test_id))
        conn.commit()
        return True
    except Exception as e:
        print(f"Error mark_test_auto_notified: {e}")
        return False
    finally:
        _close_conn(conn)


def clear_test_schedule(test_id: int) -> bool:
    """Test jadvalini tozalash (avtomatik boshlanish bekor qilish)."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE tests SET scheduled_date=NULL, scheduled_start=NULL, scheduled_end=NULL WHERE id={_ph()}",
            (test_id,)
        )
        conn.commit()
        invalidate_test_cache(test_id)
        _notify_schedule_changed_internal()
        try:
            from core import invalidate_dashboard_cache
            invalidate_dashboard_cache()
        except Exception:
            pass
        return True
    except Exception as e:
        print(f"Error clear_test_schedule: {e}")
        return False
    finally:
        _close_conn(conn)


def set_test_youtube_url(test_id: int, url: str) -> bool:
    """Test uchun YouTube video tahlil havolasini saqlash yoki o'chirish."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        val = url.strip() if url and url.strip() else None
        cur.execute(
            f"UPDATE tests SET youtube_url = {_ph()} WHERE id = {_ph()}",
            (val, test_id)
        )
        conn.commit()
        invalidate_test_cache(test_id)
        try:
            from core import invalidate_dashboard_cache
            invalidate_dashboard_cache()
        except Exception:
            pass
        return True
    except Exception as e:
        print(f"Error set_test_youtube_url: {e}")
        return False
    finally:
        _close_conn(conn)


def get_test_youtube_url(test_id: int) -> Optional[str]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT youtube_url FROM tests WHERE id = {_ph()}", (test_id,))
        row = cur.fetchone()
        if row:
            d = _row_to_dict(row)
            return d.get("youtube_url")
        return None
    except Exception as e:
        print(f"Error get_test_youtube_url: {e}")
        return None
    finally:
        _close_conn(conn)


def get_next_test_code() -> str:
    """Mavjud testlar ketma-ketligiga qarab keyingi unikal test kodini avtomatik aniqlash."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT test_code FROM tests ORDER BY id ASC")
        rows = cur.fetchall()
        if not rows:
            return "1"

        codes = [str(r[0] if isinstance(r, (list, tuple)) else r['test_code']).strip() for r in rows if r]
        int_codes = [int(c) for c in codes if c.isdigit()]
        if int_codes:
            return str(max(int_codes) + 1)

        import re
        latest_code = codes[-1] if codes else ""
        match = re.match(r'^(.*?)(\d+)$', latest_code)
        if match:
            prefix, num_str = match.groups()
            next_num = int(num_str) + 1
            return f"{prefix}{next_num:0{len(num_str)}d}"

        return str(len(codes) + 1)
    finally:
        _close_conn(conn)


def get_scheduled_tests() -> List[Dict[str, Any]]:
    """Jadval vaqti belgilangan barcha testlarni qaytaradi (Zero-DB RAM Cache)."""
    try:
        all_tests = get_all_tests()
        return [
            t for t in all_tests
            if t.get("scheduled_start") is not None and t.get("scheduled_end") is not None
        ]
    except Exception:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM tests WHERE scheduled_start IS NOT NULL AND scheduled_end IS NOT NULL"
            )
            rows = cur.fetchall()
            return [_row_to_dict(r) for r in rows if r]
        finally:
            _close_conn(conn)


# ──────────────────────────────────────────────────────────
# TIZIM SOZLAMALARI VA TEXNIK REJIM (MAINTENANCE MODE)
# ──────────────────────────────────────────────────────────

def get_setting(key: str, default: str = "") -> str:
    """Tizim sozlamasini olish."""
    with _RAM_CACHE_LOCK:
        if key in _CACHE_SETTINGS:
            ts, val = _CACHE_SETTINGS[key]
            if time.time() - ts < CACHE_TTL_SETTINGS:
                return val

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT value FROM system_settings WHERE key = {_ph()}", (key,))
        row = cur.fetchone()
        if row:
            val = str(row["value"] if isinstance(row, dict) else row[0])
            with _RAM_CACHE_LOCK:
                _CACHE_SETTINGS[key] = (time.time(), val)
            return val
        return default
    finally:
        _close_conn(conn)


def set_setting(key: str, value: str):
    """Tizim sozlamasini saqlash yoki yangilash."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute("""
            INSERT INTO system_settings (key, value)
            VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """, (key, str(value)))
        else:
            cur.execute("""
            INSERT INTO system_settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """, (key, str(value)))
        conn.commit()
        invalidate_settings_cache()
    finally:
        _close_conn(conn)


def is_maintenance_mode() -> bool:
    """Texnik profilaktika rejimi yoqilganmi? (5 soniyalik xotira keshi)"""
    global _MAINTENANCE_CACHE
    now = time.time()
    with _RAM_CACHE_LOCK:
        ts, val = _MAINTENANCE_CACHE
        if now - ts < CACHE_TTL_5S:
            return val

    val = (get_setting("maintenance_mode", "0") == "1")
    with _RAM_CACHE_LOCK:
        _MAINTENANCE_CACHE = (time.time(), val)
    return val


def set_maintenance_mode(enabled: bool):
    """Texnik profilaktika rejimini yoqish yoki o'chirish."""
    set_setting("maintenance_mode", "1" if enabled else "0")
    invalidate_maintenance_cache()


def is_registration_open() -> bool:
    """Yangi foydalanuvchilar qabuli ochiqmi? (default: True)"""
    return get_setting("registration_open", "1") == "1"


def set_registration_open(enabled: bool):
    """Yangi foydalanuvchilar qabulini ochish yoki yopish."""
    set_setting("registration_open", "1" if enabled else "0")


def get_broadcast_users() -> List[Dict[str, Any]]:
    """Xabar tarqatish uchun faol foydalanuvchilar ro'yxati (faqat tasdiqlanganlar, blocked_bot larsiz)."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT tg_id, fullname, status FROM users WHERE status = 'approved' AND status != 'blocked_bot'")
        rows = cur.fetchall()
        return [_row_to_dict(r) for r in rows if r]
    finally:
        _close_conn(conn)


def mark_user_blocked_bot(tg_id: int) -> bool:
    """Foydalanuvchi botni bloklaganda ma'lumotlarini o'chirmasdan status='blocked_bot' qilib belgilash."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        sql = f"UPDATE users SET status = 'blocked_bot' WHERE tg_id = {_ph()}"
        cur.execute(sql, (tg_id,))
        conn.commit()
        return True
    except Exception as e:
        print(f"Error mark_user_blocked_bot: {e}")
        return False
    finally:
        _close_conn(conn)


def create_broadcast_batch(batch_id: str, sender_tg_id: int = 0, message_text: str = "", photo_id: str = "") -> bool:
    """Yangi broadcast partiyasini yaratish (boshlang'ich holati 'running')."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        now = int(time.time())
        sql = f"""
        INSERT INTO broadcast_history (batch_id, sender_tg_id, message_text, photo_id, total_sent, is_deleted, status, created_at)
        VALUES ({_ph()}, {_ph()}, {_ph()}, {_ph()}, 0, 0, 'running', {_ph()})
        """
        cur.execute(sql, (batch_id, sender_tg_id, str(message_text)[:1000], str(photo_id or ''), now))
        conn.commit()
        return True
    except Exception as e:
        # Agar status ustuni hali bo'lmasa, status siz fallback qilib yozish
        try:
            sql_fallback = f"""
            INSERT INTO broadcast_history (batch_id, sender_tg_id, message_text, photo_id, total_sent, is_deleted, created_at)
            VALUES ({_ph()}, {_ph()}, {_ph()}, {_ph()}, 0, 0, {_ph()})
            """
            cur.execute(sql_fallback, (batch_id, sender_tg_id, str(message_text)[:1000], str(photo_id or ''), now))
            conn.commit()
            return True
        except Exception as e2:
            print(f"Error create_broadcast_batch: {e2}")
            return False
    finally:
        _close_conn(conn)


def update_broadcast_status(batch_id: str, status: str) -> bool:
    """Partiya holatini yangilash ('running', 'done', 'failed', 'stopped')."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        sql = f"UPDATE broadcast_history SET status = {_ph()} WHERE batch_id = {_ph()}"
        cur.execute(sql, (status, batch_id))
        conn.commit()
        return True
    except Exception as e:
        print(f"Error update_broadcast_status: {e}")
        return False
    finally:
        _close_conn(conn)


def get_broadcast_sent_chat_ids(batch_id: str) -> set[int]:
    """Partiya bo'yicha allaqachon xabar yetkazilgan chat_id lar to'plami."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        sql = f"SELECT chat_id FROM broadcast_messages WHERE batch_id = {_ph()}"
        cur.execute(sql, (batch_id,))
        rows = cur.fetchall()
        result = set()
        for r in rows:
            if isinstance(r, dict):
                result.add(int(r.get("chat_id")))
            elif hasattr(r, "keys"):
                result.add(int(r["chat_id"]))
            else:
                result.add(int(r[0]))
        return result
    finally:
        _close_conn(conn)


def record_broadcast_message(batch_id: str, chat_id: int, message_id: int) -> bool:
    """Har bir yuborilgan xabar ID sini saqlash (keyinchalik o'chirish uchun)."""
    return record_broadcast_messages_batch([(batch_id, chat_id, message_id)])


def record_broadcast_messages_batch(records: List[tuple[str, int, int]]) -> bool:
    """
    To'plangan xabarlar ID sini bitta tranzaksiyada ommaviy yozish (executemany).
    records: [(batch_id, chat_id, message_id), ...]
    """
    if not records:
        return True
    conn = get_connection()
    try:
        cur = conn.cursor()
        now = int(time.time())
        sql = f"""
        INSERT INTO broadcast_messages (batch_id, chat_id, message_id, status, created_at)
        VALUES ({_ph()}, {_ph()}, {_ph()}, 'sent', {_ph()})
        """
        rows = [(r[0], r[1], r[2], now) for r in records]
        cur.executemany(sql, rows)
        conn.commit()
        return True
    except Exception as e:
        print(f"Error record_broadcast_messages_batch: {e}")
        return False
    finally:
        _close_conn(conn)


def update_broadcast_sent_count(batch_id: str, total_sent: int) -> bool:
    """Yuborilgan jami xabarlar sonini yangilash."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        sql = f"UPDATE broadcast_history SET total_sent = {_ph()} WHERE batch_id = {_ph()}"
        cur.execute(sql, (total_sent, batch_id))
        conn.commit()
        return True
    except Exception as e:
        print(f"Error update_broadcast_sent_count: {e}")
        return False
    finally:
        _close_conn(conn)


def get_broadcast_messages(batch_id: str) -> List[Dict[str, Any]]:
    """Partiyaga tegishli barcha yuborilgan xabar ID lari ro'yxati."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        sql = f"SELECT chat_id, message_id FROM broadcast_messages WHERE batch_id = {_ph()} AND status = 'sent'"
        cur.execute(sql, (batch_id,))
        rows = cur.fetchall()
        return [_row_to_dict(r) for r in rows if r]
    finally:
        _close_conn(conn)


def mark_broadcast_deleted(batch_id: str) -> bool:
    """Xabar barcha o'quvchilardan o'chirilganini belgilash."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"UPDATE broadcast_history SET is_deleted = 1 WHERE batch_id = {_ph()}", (batch_id,))
        cur.execute(f"UPDATE broadcast_messages SET status = 'deleted' WHERE batch_id = {_ph()}", (batch_id,))
        conn.commit()
        return True
    except Exception as e:
        print(f"Error mark_broadcast_deleted: {e}")
        return False
    finally:
        _close_conn(conn)


def get_recent_broadcasts(limit: int = 10) -> List[Dict[str, Any]]:
    """Yaqinda yuborilgan broadcast xabarlari ro'yxati."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        sql = f"SELECT * FROM broadcast_history ORDER BY id DESC LIMIT {_ph()}"
        cur.execute(sql, (limit,))
        rows = cur.fetchall()
        return [_row_to_dict(r) for r in rows if r]
    finally:
        _close_conn(conn)


def get_broadcast_by_batch(batch_id: str) -> Optional[Dict[str, Any]]:
    """Partiyani kodi orqali olish."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        sql = f"SELECT * FROM broadcast_history WHERE batch_id = {_ph()} LIMIT 1"
        cur.execute(sql, (batch_id,))
        row = cur.fetchone()
        return _row_to_dict(row) if row else None
    finally:
        _close_conn(conn)


# ──────────────────────────────────────────────────────────
# USERS & ACCESS
# ──────────────────────────────────────────────────────────

def add_or_update_user(tg_id: int, fullname: str, phone: str,
                       username: Optional[str] = None, status: str = "approved") -> bool:
    conn = get_connection()
    try:
        cur = conn.cursor()
        now = int(time.time())
        if USE_POSTGRES:
            cur.execute("""
            INSERT INTO users (tg_id, fullname, phone, username, status, registered_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (tg_id) DO UPDATE SET
                fullname = EXCLUDED.fullname,
                phone = EXCLUDED.phone,
                username = EXCLUDED.username,
                status = CASE WHEN users.status IN ('pending', 'rejected') THEN users.status ELSE EXCLUDED.status END,
                deleted_at = NULL,
                deleted_by = NULL,
                is_anonymized = 0
            """, (tg_id, fullname, phone, username, status, now))
        else:
            cur.execute("""
            INSERT INTO users (tg_id, fullname, phone, username, status, registered_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(tg_id) DO UPDATE SET
                fullname=excluded.fullname,
                phone=excluded.phone,
                username=excluded.username,
                status=CASE WHEN users.status IN ('pending', 'rejected') THEN users.status ELSE excluded.status END,
                deleted_at = NULL,
                deleted_by = NULL,
                is_anonymized = 0
            """, (tg_id, fullname, phone, username, status, now))
        conn.commit()
        invalidate_user_cache(tg_id)
        return True
    except Exception as e:
        print(f"Error saving user: {e}")
        return False
    finally:
        _close_conn(conn)


def update_user_profile(tg_id: int, fullname: Optional[str] = None, phone: Optional[str] = None) -> bool:
    conn = get_connection()
    try:
        cur = conn.cursor()
        updates = []
        params = []
        if fullname is not None:
            updates.append(f"fullname = {_ph()}")
            params.append(fullname.strip())
        if phone is not None:
            updates.append(f"phone = {_ph()}")
            params.append(phone.strip())
        if not updates:
            return True
        params.append(tg_id)
        sql = f"UPDATE users SET {', '.join(updates)} WHERE tg_id = {_ph()}"
        cur.execute(sql, tuple(params))
        conn.commit()
        return True
    except Exception as e:
        print(f"Error updating user profile: {e}")
        return False
    finally:
        _close_conn(conn)


def get_user(tg_id: int, include_deleted: bool = False) -> Optional[Dict[str, Any]]:
    if not tg_id or int(tg_id) <= 0:
        return None
    now = time.time()
    with _RAM_CACHE_LOCK:
        if int(tg_id) in _CACHE_USERS:
            ts, val = _CACHE_USERS[int(tg_id)]
            ttl = CACHE_TTL_USERS if val is not None else 10.0
            if now - ts < ttl:
                if val is not None:
                    if not include_deleted and val.get("deleted_at"):
                        return None
                    return dict(val)
                return None

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM users WHERE tg_id = {_ph()}", (tg_id,))
        row = cur.fetchone()
        res = _row_to_dict(row)
        with _RAM_CACHE_LOCK:
            _CACHE_USERS[int(tg_id)] = (now, dict(res) if res else None)
        if res and not include_deleted and res.get("deleted_at"):
            return None
        return res
    finally:
        _close_conn(conn)


def find_user(query: Any) -> Optional[Dict[str, Any]]:
    """
    Foydalanuvchini turli parametrlar bo'yicha qidiradi:
    - tg_id (masalan: 8039427064)
    - username (masalan: @username yoki username yoki https://t.me/username)
    - telefon raqami (masalan: +998901234567 yoki 998901234567)
    - id (baza ichki id si)
    - fullname (ism-familiya bo'yicha)
    Natija sifatida foydalanuvchi ma'lumotlari, testlar soni va oxirgi topshirgan vaqti qaytariladi.
    """
    if not query:
        return None
    raw_q = str(query).strip()
    if not raw_q:
        return None

    clean_q = raw_q
    for prefix in ["https://t.me/", "http://t.me/", "t.me/"]:
        if clean_q.lower().startswith(prefix):
            clean_q = clean_q[len(prefix):].split("/")[0].split("?")[0]
            break

    clean_uname = clean_q.lstrip("@").strip()
    digits_only = re.sub(r"\D", "", raw_q)

    conn = get_connection()
    try:
        cur = conn.cursor()
        ph = _ph()

        # 1. Telegram ID (aniq moslik)
        if digits_only and len(digits_only) >= 5:
            num_val = int(digits_only)
            cur.execute(f"""
                SELECT u.*, COUNT(s.id) as tests_count, MAX(s.submitted_at) as last_test_at
                FROM users u
                LEFT JOIN submissions s ON u.tg_id = s.user_tg_id
                WHERE u.tg_id = {ph}
                GROUP BY u.id, u.tg_id, u.fullname, u.phone, u.username, u.status, u.pin_code, u.registered_at
            """, (num_val,))
            row = cur.fetchone()
            if row:
                return _row_to_dict(row)

        # 2. Username bo'yicha
        if clean_uname:
            cur.execute(f"""
                SELECT u.*, COUNT(s.id) as tests_count, MAX(s.submitted_at) as last_test_at
                FROM users u
                LEFT JOIN submissions s ON u.tg_id = s.user_tg_id
                WHERE LOWER(u.username) = LOWER({ph})
                GROUP BY u.id, u.tg_id, u.fullname, u.phone, u.username, u.status, u.pin_code, u.registered_at
            """, (clean_uname,))
            row = cur.fetchone()
            if row:
                return _row_to_dict(row)

        # 3. Telefon raqami bo'yicha
        if digits_only and len(digits_only) >= 7:
            cur.execute(f"""
                SELECT u.*, COUNT(s.id) as tests_count, MAX(s.submitted_at) as last_test_at
                FROM users u
                LEFT JOIN submissions s ON u.tg_id = s.user_tg_id
                WHERE u.phone LIKE {ph}
                GROUP BY u.id, u.tg_id, u.fullname, u.phone, u.username, u.status, u.pin_code, u.registered_at
            """, (f"%{digits_only[-9:]}%",))
            row = cur.fetchone()
            if row:
                return _row_to_dict(row)

        # 4. Ichki baza ID raqami bo'yicha (users.id)
        if digits_only:
            try:
                db_id = int(digits_only)
                cur.execute(f"""
                    SELECT u.*, COUNT(s.id) as tests_count, MAX(s.submitted_at) as last_test_at
                    FROM users u
                    LEFT JOIN submissions s ON u.tg_id = s.user_tg_id
                    WHERE u.id = {ph}
                    GROUP BY u.id, u.tg_id, u.fullname, u.phone, u.username, u.status, u.pin_code, u.registered_at
                """, (db_id,))
                row = cur.fetchone()
                if row:
                    return _row_to_dict(row)
            except Exception:
                pass

        # 5. Ism bo'yicha (LOWER LIKE)
        if len(raw_q) >= 3:
            cur.execute(f"""
                SELECT u.*, COUNT(s.id) as tests_count, MAX(s.submitted_at) as last_test_at
                FROM users u
                LEFT JOIN submissions s ON u.tg_id = s.user_tg_id
                WHERE LOWER(u.fullname) LIKE LOWER({ph})
                GROUP BY u.id, u.tg_id, u.fullname, u.phone, u.username, u.status, u.pin_code, u.registered_at
                LIMIT 1
            """, (f"%{raw_q}%",))
            row = cur.fetchone()
            if row:
                return _row_to_dict(row)

        return None
    except Exception as e:
        print(f"Error in find_user: {e}")
        return None
    finally:
        _close_conn(conn)


def is_user_approved(tg_id: int, admin_id: int = 8039427064) -> bool:
    if is_admin(tg_id, admin_id):
        return True
    user = get_user(tg_id)
    if not user:
        return False
    return user.get("status") == "approved"


def approve_user(tg_id: int) -> bool:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"UPDATE users SET status = 'approved' WHERE tg_id = {_ph()}", (tg_id,))
        cur.execute(f"DELETE FROM admin_deleted_users WHERE tg_id = {_ph()}", (tg_id,))
        conn.commit()
        invalidate_user_cache(tg_id)
        invalidate_admin_deleted_cache(tg_id)
        try:
            from core import invalidate_dashboard_cache
            invalidate_dashboard_cache()
        except Exception:
            pass
        return True
    except Exception as e:
        print(f"Error approving user: {e}")
        return False
    finally:
        _close_conn(conn)


def reject_user(tg_id: int) -> bool:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"UPDATE users SET status = 'rejected' WHERE tg_id = {_ph()}", (tg_id,))
        conn.commit()
        invalidate_user_cache(tg_id)
        try:
            from core import invalidate_dashboard_cache
            invalidate_dashboard_cache()
        except Exception:
            pass
        return True
    except Exception as e:
        print(f"Error rejecting user: {e}")
        return False
    finally:
        _close_conn(conn)


def block_user(tg_id: int) -> bool:
    """Eski tizim bilan moslik: yangi modelda bloklash o'rniga rad etish (reject) bajariladi."""
    return reject_user(tg_id)


def invalidate_admin_deleted_cache(tg_id: Optional[int] = None):
    with _RAM_CACHE_LOCK:
        if tg_id is not None:
            _ADMIN_DELETED_CACHE.pop(int(tg_id), None)
        else:
            _ADMIN_DELETED_CACHE.clear()


def mark_admin_deleted(tg_id: int, admin_id: Optional[int] = None) -> bool:
    """Admin tomonidan o'chirilgan foydalanuvchini admin_deleted_users jadvaliga kiritish."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        now_ts = int(time.time())
        if USE_POSTGRES:
            cur.execute("""
            INSERT INTO admin_deleted_users (tg_id, deleted_at, deleted_by, last_request_at)
            VALUES (%s, %s, %s, NULL)
            ON CONFLICT (tg_id) DO UPDATE SET
                deleted_at = EXCLUDED.deleted_at,
                deleted_by = EXCLUDED.deleted_by
            """, (tg_id, now_ts, admin_id))
        else:
            cur.execute("""
            INSERT INTO admin_deleted_users (tg_id, deleted_at, deleted_by, last_request_at)
            VALUES (?, ?, ?, NULL)
            ON CONFLICT(tg_id) DO UPDATE SET
                deleted_at = excluded.deleted_at,
                deleted_by = excluded.deleted_by
            """, (tg_id, now_ts, admin_id))
        conn.commit()
        invalidate_admin_deleted_cache(tg_id)
        return True
    except Exception as e:
        print(f"Error mark_admin_deleted: {e}")
        return False
    finally:
        _close_conn(conn)


def is_admin_deleted(tg_id: int) -> bool:
    """Foydalanuvchi admin tomonidan o'chirilganlar ro'yxatida bormi? (Zero-DB RAM Cache)"""
    return get_admin_deleted_info(tg_id) is not None


def clear_admin_deleted(tg_id: int) -> bool:
    """Admin ruxsat berganda ushbu foydalanuvchini admin_deleted_users ro'yxatidan tozalash."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"DELETE FROM admin_deleted_users WHERE tg_id = {_ph()}", (tg_id,))
        conn.commit()
        invalidate_admin_deleted_cache(tg_id)
        return True
    except Exception as e:
        print(f"Error clear_admin_deleted: {e}")
        return False
    finally:
        _close_conn(conn)


def touch_admin_deleted_request(tg_id: int) -> bool:
    """Foydalanuvchi /start yoki ruxsat so'raganda oxirgi so'rov vaqtini yangilash."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        now_ts = int(time.time())
        cur.execute(f"UPDATE admin_deleted_users SET last_request_at = {_ph()} WHERE tg_id = {_ph()}", (now_ts, tg_id))
        conn.commit()
        invalidate_admin_deleted_cache(tg_id)
        return True
    except Exception as e:
        print(f"Error touch_admin_deleted_request: {e}")
        return False
    finally:
        _close_conn(conn)


def get_admin_deleted_info(tg_id: int) -> Optional[Dict[str, Any]]:
    """Admin tomonidan o'chirilgan foydalanuvchi ma'lumotlarini olish (Zero-DB RAM Cache)."""
    now = time.time()
    with _RAM_CACHE_LOCK:
        if tg_id in _ADMIN_DELETED_CACHE:
            ts, val = _ADMIN_DELETED_CACHE[tg_id]
            if now - ts < CACHE_TTL_ADMIN_DELETED:
                return dict(val) if val else None

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM admin_deleted_users WHERE tg_id = {_ph()}", (tg_id,))
        row = cur.fetchone()
        res = _row_to_dict(row) if row else None
        with _RAM_CACHE_LOCK:
            _ADMIN_DELETED_CACHE[tg_id] = (time.time(), res)
        return res
    except Exception as e:
        print(f"Error get_admin_deleted_info: {e}")
        return None
    finally:
        _close_conn(conn)


def get_admin_deleted_request_time(tg_id: int) -> Optional[int]:
    """Admin tomonidan o'chirilgan foydalanuvchining oxirgi so'rov yuborgan vaqtini olish."""
    info = get_admin_deleted_info(tg_id)
    if info and info.get("last_request_at"):
        try:
            return int(info["last_request_at"])
        except (ValueError, TypeError):
            return None
    return None


def can_request_access(tg_id: int, cooldown_hours: int = 6) -> Tuple[bool, float]:
    """
    Foydalanuvchi adminga yangi ruxsat so'rovi yuborishi mumkinligini tekshirish (kamida cooldown_hours soat).
    Qaytaradi: (mumkin_mi: bool, o'tgan_soat: float)
    """
    req_time = get_admin_deleted_request_time(tg_id)
    if not req_time:
        return True, 0.0
    diff_sec = time.time() - req_time
    diff_hours = diff_sec / 3600.0
    return (diff_hours >= cooldown_hours), diff_hours


def set_user_pending(tg_id: int) -> bool:
    """Foydalanuvchi maqomini 'pending' (kutilmoqda) ga o'tkazadi."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"UPDATE users SET status = 'pending' WHERE tg_id = {_ph()}", (tg_id,))
        conn.commit()
        invalidate_user_cache(tg_id)
        return True
    except Exception as e:
        print(f"Error setting user pending: {e}")
        return False
    finally:
        _close_conn(conn)


def restrict_all_users(super_admin_id: int = 8039427064) -> int:
    """
    Barcha oddiy foydalanuvchilarning maqomini 'pending' (kutilmoqda) ga o'tkazadi.
    Adminlar daxlsiz qoladi.
    Qaytaradi: cheklangan foydalanuvchilar soni.
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT tg_id FROM admins")
        admin_rows = cur.fetchall()
        admin_ids = set()
        for r in admin_rows:
            d = _row_to_dict(r)
            if d and d.get("tg_id"):
                admin_ids.add(int(d["tg_id"]))
        admin_ids.add(int(super_admin_id))
        admin_list = list(admin_ids)

        if USE_POSTGRES:
            cur.execute("""
                UPDATE users 
                SET previous_status = status, status = 'pending' 
                WHERE NOT (tg_id = ANY(%s)) AND status != 'pending' AND deleted_at IS NULL
            """, (admin_list,))
        else:
            placeholders = ",".join("?" for _ in admin_list)
            cur.execute(f"""
                UPDATE users 
                SET previous_status = status, status = 'pending' 
                WHERE tg_id NOT IN ({placeholders}) AND status != 'pending' AND deleted_at IS NULL
            """, admin_list)
        count = cur.rowcount
        conn.commit()
        invalidate_user_cache()
        return count
    except Exception as e:
        print(f"Error restricting all users: {e}")
        return 0
    finally:
        _close_conn(conn)


def unrestrict_all_users(super_admin_id: int = 8039427064) -> int:
    """Barcha cheklangan foydalanuvchilarni avvalgi statusiga qaytarish."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE users
            SET status = COALESCE(previous_status, 'approved'), previous_status = NULL
            WHERE status = 'pending' AND deleted_at IS NULL
        """)
        count = cur.rowcount
        conn.commit()
        invalidate_user_cache()
        return count
    except Exception as e:
        print(f"Error unrestricting all users: {e}")
        return 0
    finally:
        _close_conn(conn)


def delete_user(tg_id: int, deleted_by: Optional[int] = None, is_self_delete: bool = False, by_admin: bool = False) -> Optional[Dict[str, Any]]:
    """
    Foydalanuvchi ma'lumotlarini to'liq tozalash (Atomic Purge):
    1. users, submissions, submission_drafts, user_test_drafts, broadcast_messages jadvallaridan o'chiriladi.
    2. Agar ADMIN o'chirsa (by_admin=True yoki deleted_by kiritilgan va is_self_delete=False):
       Faqat tg_id admin_deleted_users jadvaliga yoziladi (bot keyin tanib olishi uchun).
    3. Agar o'zi ketsa (botni bloklasa yoki akkauntini o'chirsa):
       admin_deleted_users ga TUSHMAYDI!
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM users WHERE tg_id = {_ph()} OR id = {_ph()}", (tg_id, tg_id))
        user_row = _row_to_dict(cur.fetchone())
        actual_tg_id = (user_row.get("tg_id") if user_row else None) or tg_id

        # 1. Barcha jadvallardan to'liq tozalash
        cur.execute(f"DELETE FROM users WHERE tg_id = {_ph()} OR id = {_ph()}", (actual_tg_id, tg_id))
        cur.execute(f"DELETE FROM submissions WHERE user_tg_id = {_ph()}", (actual_tg_id,))
        cur.execute(f"DELETE FROM submission_drafts WHERE user_tg_id = {_ph()}", (actual_tg_id,))
        cur.execute(f"DELETE FROM user_test_drafts WHERE user_tg_id = {_ph()}", (actual_tg_id,))
        cur.execute(f"DELETE FROM broadcast_messages WHERE chat_id = {_ph()}", (actual_tg_id,))

        # 2. Admin o'chirgan holatni qayd etish
        is_admin_action = by_admin or (deleted_by is not None and int(deleted_by) != int(actual_tg_id) and not is_self_delete)
        if is_admin_action:
            now_ts = int(time.time())
            adm_id = deleted_by if (deleted_by and int(deleted_by) != int(actual_tg_id)) else ADMIN_ID
            if USE_POSTGRES:
                cur.execute("""
                INSERT INTO admin_deleted_users (tg_id, deleted_at, deleted_by, last_request_at)
                VALUES (%s, %s, %s, NULL)
                ON CONFLICT (tg_id) DO UPDATE SET
                    deleted_at = EXCLUDED.deleted_at,
                    deleted_by = EXCLUDED.deleted_by
                """, (actual_tg_id, now_ts, adm_id))
            else:
                cur.execute("""
                INSERT INTO admin_deleted_users (tg_id, deleted_at, deleted_by, last_request_at)
                VALUES (?, ?, ?, NULL)
                ON CONFLICT(tg_id) DO UPDATE SET
                    deleted_at = excluded.deleted_at,
                    deleted_by = excluded.deleted_by
                """, (actual_tg_id, now_ts, adm_id))

        conn.commit()
        invalidate_user_cache(actual_tg_id)
        invalidate_admin_deleted_cache(actual_tg_id)
        try:
            from core import invalidate_dashboard_cache
            invalidate_dashboard_cache()
        except Exception:
            pass
        return user_row or {"tg_id": actual_tg_id}
    except Exception as e:
        print(f"Error deleting user {tg_id}: {e}")
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        return None
    finally:
        _close_conn(conn)


def restore_user(tg_id: int) -> Optional[Dict[str, Any]]:
    """Admin tomonidan o'chirilgan foydalanuvchini qayta tiklash (admin_deleted_users dan tozalash va approved qilish)."""
    clear_admin_deleted(tg_id)
    u = get_user(tg_id)
    if not u:
        add_or_update_user(tg_id, "Foydalanuvchi", "", None, status="approved")
    else:
        approve_user(tg_id)
    return get_user(tg_id)


def delete_all_blocked_users() -> List[Dict[str, Any]]:
    """Eski tizim bilan moslik uchun; yangi modelda 'blocked' statusi yo'q."""
    return []


# ──────────────────────────────────────────────────────────
# JAVOBLARNI AVTOMATIK SAQLASH (AUTOSAVE DRAFTS)
# ──────────────────────────────────────────────────────────

def save_answer_draft(user_tg_id: int, test_id: int, question_key: str, answer_val: str) -> bool:
    """
    O'quvchi test ishlayotgan paytda har bir savolga bergan javobini zudlik bilan bazaga autosave qilish.
    Server restart yoki mini ilova tasodifan yopilib qolsa ham javoblar yo'qolmaydi!
    """
    if not user_tg_id or not test_id or not question_key:
        return False
    conn = get_connection()
    try:
        cur = conn.cursor()
        now_ts = int(time.time())
        qkey = str(question_key).strip().lower()
        ans = str(answer_val).strip()

        if USE_POSTGRES:
            cur.execute("""
                INSERT INTO submission_drafts (user_tg_id, test_id, question_key, answer_val, updated_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (user_tg_id, test_id, question_key) DO UPDATE SET
                    answer_val = EXCLUDED.answer_val,
                    updated_at = EXCLUDED.updated_at
            """, (user_tg_id, test_id, qkey, ans, now_ts))
        else:
            cur.execute("""
                INSERT INTO submission_drafts (user_tg_id, test_id, question_key, answer_val, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (user_tg_id, test_id, question_key) DO UPDATE SET
                    answer_val = excluded.answer_val,
                    updated_at = excluded.updated_at
            """, (user_tg_id, test_id, qkey, ans, now_ts))

        conn.commit()
        return True
    except Exception as e:
        print(f"Error autosaving answer draft: {e}")
        return False
    finally:
        _close_conn(conn)


def save_bulk_draft(user_tg_id: int, test_id: int, answers: Dict[str, str]) -> bool:
    """Ko'p javoblarni birdaniga autosave qilish."""
    if not user_tg_id or not test_id or not answers:
        return False
    conn = get_connection()
    try:
        cur = conn.cursor()
        now_ts = int(time.time())
        ans_json = json.dumps(answers, ensure_ascii=False)

        if USE_POSTGRES:
            cur.execute("""
                INSERT INTO user_test_drafts (user_tg_id, test_id, answers_json, updated_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_tg_id, test_id) DO UPDATE SET
                    answers_json = EXCLUDED.answers_json,
                    updated_at = EXCLUDED.updated_at
            """, (user_tg_id, test_id, ans_json, now_ts))
        else:
            cur.execute("""
                INSERT INTO user_test_drafts (user_tg_id, test_id, answers_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (user_tg_id, test_id) DO UPDATE SET
                    answers_json = excluded.answers_json,
                    updated_at = excluded.updated_at
            """, (user_tg_id, test_id, ans_json, now_ts))

        for qkey, val in answers.items():
            qkey_clean = str(qkey).strip().lower()
            val_clean = str(val).strip()
            if USE_POSTGRES:
                cur.execute("""
                    INSERT INTO submission_drafts (user_tg_id, test_id, question_key, answer_val, updated_at)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (user_tg_id, test_id, question_key) DO UPDATE SET
                        answer_val = EXCLUDED.answer_val,
                        updated_at = EXCLUDED.updated_at
                """, (user_tg_id, test_id, qkey_clean, val_clean, now_ts))
            else:
                cur.execute("""
                    INSERT INTO submission_drafts (user_tg_id, test_id, question_key, answer_val, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT (user_tg_id, test_id, question_key) DO UPDATE SET
                        answer_val = excluded.answer_val,
                        updated_at = excluded.updated_at
                """, (user_tg_id, test_id, qkey_clean, val_clean, now_ts))

        conn.commit()
        return True
    except Exception as e:
        print(f"Error saving bulk draft: {e}")
        return False
    finally:
        _close_conn(conn)


def get_answer_drafts(user_tg_id: int, test_id: int) -> Dict[str, str]:
    """Foydalanuvchining ushbu test uchun saqlangan barcha qoralama javoblarini olish."""
    if not user_tg_id or not test_id:
        return {}
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT answers_json FROM user_test_drafts WHERE user_tg_id = {_ph()} AND test_id = {_ph()}", (user_tg_id, test_id))
        row = cur.fetchone()
        if row and row[0]:
            try:
                res = json.loads(row[0])
                if isinstance(res, dict) and res:
                    return res
            except Exception:
                pass

        cur.execute(f"SELECT question_key, answer_val FROM submission_drafts WHERE user_tg_id = {_ph()} AND test_id = {_ph()}", (user_tg_id, test_id))
        rows = cur.fetchall()
        out = {}
        for r in rows:
            if r and r[0]:
                out[str(r[0])] = str(r[1] or "")
        return out
    except Exception as e:
        print(f"Error fetching answer drafts: {e}")
        return {}
    finally:
        _close_conn(conn)


def clear_answer_drafts(user_tg_id: int, test_id: int) -> bool:
    """Test topshirilgandan so'ng qoralamalarni tozalash."""
    if not user_tg_id or not test_id:
        return False
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"DELETE FROM submission_drafts WHERE user_tg_id = {_ph()} AND test_id = {_ph()}", (user_tg_id, test_id))
        cur.execute(f"DELETE FROM user_test_drafts WHERE user_tg_id = {_ph()} AND test_id = {_ph()}", (user_tg_id, test_id))
        conn.commit()
        return True
    except Exception as e:
        print(f"Error clearing answer drafts: {e}")
        return False
    finally:
        _close_conn(conn)


def has_any_active_test() -> bool:
    """Ayni paytda birorta ham test faolmi yoki jadval bo'yicha ketayotganmi? (5 soniyalik xotira keshi)"""
    global _ACTIVE_TEST_CACHE
    now = time.time()
    with _RAM_CACHE_LOCK:
        ts, val = _ACTIVE_TEST_CACHE
        if now - ts < CACHE_TTL_5S:
            return val

    res = False
    tests = get_active_tests()
    if tests:
        res = True
    else:
        try:
            from core.config import get_test_schedule_status
            all_t = get_all_tests()
            for t in all_t:
                st = get_test_schedule_status(t)
                if st.get("is_active"):
                    res = True
                    break
        except Exception:
            pass

    with _RAM_CACHE_LOCK:
        _ACTIVE_TEST_CACHE = (time.time(), res)
    return res


def get_users_count() -> Dict[str, int]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) as total FROM users")
        total = (_row_to_dict(cur.fetchone()) or {}).get("total", 0)
        cur.execute("SELECT COUNT(*) as approved FROM users WHERE status = 'approved'")
        approved = (_row_to_dict(cur.fetchone()) or {}).get("approved", 0)
        cur.execute("SELECT COUNT(*) as pending FROM users WHERE status = 'pending'")
        pending = (_row_to_dict(cur.fetchone()) or {}).get("pending", 0)
        cur.execute("SELECT COUNT(*) as rejected FROM users WHERE status = 'rejected'")
        rejected = (_row_to_dict(cur.fetchone()) or {}).get("rejected", 0)
        return {
            "total": total,
            "approved": approved,
            "pending": pending,
            "rejected": rejected,
            "blocked": 0
        }
    finally:
        _close_conn(conn)


_SYSTEM_STATS_CACHE: Dict[str, Any] = {"ts": 0.0, "data": None}
_SYSTEM_STATS_LOCK = threading.Lock()

def invalidate_system_stats_cache():
    with _SYSTEM_STATS_LOCK:
        _SYSTEM_STATS_CACHE["ts"] = 0.0
        _SYSTEM_STATS_CACHE["data"] = None

def get_system_stats() -> Dict[str, int]:
    """
    Admin /stats uchun tizim statistikasi:
    - tasdiqlangan (approved)
    - kutilmoqda (pending)
    - bloklangan (blocked / blocked_bot / rejected)
    - jami testlar
    - bugungi topshirishlar
    Bitta COUNT so'rovi orqali va 10 soniyalik xotira keshi bilan ishlaydi.
    """
    now_ts = time.time()
    with _SYSTEM_STATS_LOCK:
        if _SYSTEM_STATS_CACHE["data"] is not None and (now_ts - _SYSTEM_STATS_CACHE["ts"] < 10.0):
            return dict(_SYSTEM_STATS_CACHE["data"])

    now_dt = datetime.now(UZB_TZ)
    today_start_dt = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    today_start_ts = int(today_start_dt.timestamp())

    ph = "%s" if USE_POSTGRES else "?"
    query = f"""
    SELECT
        (SELECT COUNT(*) FROM users WHERE status = 'approved') AS approved_users,
        (SELECT COUNT(*) FROM users WHERE status = 'pending') AS pending_users,
        (SELECT COUNT(*) FROM users WHERE status IN ('blocked', 'blocked_bot', 'rejected')) AS blocked_users,
        (SELECT COUNT(*) FROM tests) AS total_tests,
        (SELECT COUNT(*) FROM submissions WHERE submitted_at >= {ph}) AS today_submissions
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(query, (today_start_ts,))
        row = cur.fetchone()
        row_dict = _row_to_dict(row) or {}
        res = {
            "approved": int(row_dict.get("approved_users") or 0),
            "pending": int(row_dict.get("pending_users") or 0),
            "blocked": int(row_dict.get("blocked_users") or 0),
            "tests": int(row_dict.get("total_tests") or 0),
            "today_submissions": int(row_dict.get("today_submissions") or 0),
        }
        with _SYSTEM_STATS_LOCK:
            _SYSTEM_STATS_CACHE["ts"] = time.time()
            _SYSTEM_STATS_CACHE["data"] = res
        return res
    finally:
        _close_conn(conn)


# ──────────────────────────────────────────────────────────
# TESTS
# ──────────────────────────────────────────────────────────

def create_test(test_code: str, title: str, subject: str, answers: Dict[str, Any],
                pdf_file_id: Optional[str] = None, pdf_file_name: Optional[str] = None,
                time_limit_min: int = 0, key_access_code: str = "",
                created_by: int = 0, created_by_name: str = "",
                youtube_url: str = "") -> bool:
    conn = get_connection()
    try:
        cur = conn.cursor()
        now = int(time.time())
        if USE_POSTGRES:
            cur.execute("""
            INSERT INTO tests (test_code, title, subject, pdf_file_id, pdf_file_name,
                               answers_json, total_questions, time_limit_min, is_active,
                               key_access_code, results_published, created_at, created_by, created_by_name, youtube_url)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, %s, 0, %s, %s, %s, %s)
            ON CONFLICT (test_code) DO UPDATE SET
                title = EXCLUDED.title,
                subject = EXCLUDED.subject,
                pdf_file_id = COALESCE(EXCLUDED.pdf_file_id, tests.pdf_file_id),
                pdf_file_name = COALESCE(EXCLUDED.pdf_file_name, tests.pdf_file_name),
                answers_json = EXCLUDED.answers_json,
                time_limit_min = EXCLUDED.time_limit_min,
                key_access_code = EXCLUDED.key_access_code,
                is_active = 1,
                results_published = 0,
                created_by = COALESCE(EXCLUDED.created_by, tests.created_by),
                created_by_name = COALESCE(EXCLUDED.created_by_name, tests.created_by_name),
                youtube_url = COALESCE(NULLIF(EXCLUDED.youtube_url, ''), tests.youtube_url)
            """, (test_code, title, subject, pdf_file_id, pdf_file_name,
                  json.dumps(answers, ensure_ascii=False), 45, time_limit_min,
                  key_access_code, now, created_by, created_by_name, youtube_url or None))
        else:
            cur.execute("""
            INSERT INTO tests (test_code, title, subject, pdf_file_id, pdf_file_name,
                               answers_json, total_questions, time_limit_min, is_active,
                               key_access_code, results_published, created_at, created_by, created_by_name, youtube_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 0, ?, ?, ?, ?)
            ON CONFLICT(test_code) DO UPDATE SET
                title=excluded.title,
                subject=excluded.subject,
                pdf_file_id=COALESCE(excluded.pdf_file_id, tests.pdf_file_id),
                pdf_file_name=COALESCE(excluded.pdf_file_name, tests.pdf_file_name),
                answers_json=excluded.answers_json,
                time_limit_min=excluded.time_limit_min,
                key_access_code=excluded.key_access_code,
                is_active=1,
                results_published=0,
                created_by=COALESCE(excluded.created_by, tests.created_by),
                created_by_name=COALESCE(excluded.created_by_name, tests.created_by_name),
                youtube_url=COALESCE(NULLIF(excluded.youtube_url, ''), tests.youtube_url)
            """, (test_code, title, subject, pdf_file_id, pdf_file_name,
                  json.dumps(answers, ensure_ascii=False), 45, time_limit_min,
                  key_access_code, now, created_by, created_by_name, youtube_url or None))
        conn.commit()
        invalidate_test_cache()
        return True
    except Exception as e:
        print(f"Error creating/updating test: {e}")
        return False
    finally:
        _close_conn(conn)


def update_test_pdf(test_id: int, pdf_file_id: str, pdf_file_name: str) -> bool:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE tests SET pdf_file_id = {_ph()}, pdf_file_name = {_ph()} WHERE id = {_ph()}",
            (pdf_file_id, pdf_file_name, test_id)
        )
        conn.commit()
        invalidate_test_cache(test_id)
        return True
    except Exception as e:
        print(f"Error updating test pdf: {e}")
        return False
    finally:
        _close_conn(conn)


def get_active_tests() -> List[Dict[str, Any]]:
    """Zero-DB-Read: Faol testlarni to'g'ridan-to'g'ri server RAM xotirasidan sub-millisekundda qaytarish."""
    _ensure_tests_cache_loaded()
    with _RAM_CACHE_LOCK:
        return [dict(t) for t in _GLOBAL_ACTIVE_TESTS_LIST]


def get_test_by_code(test_code: str) -> Optional[Dict[str, Any]]:
    """Zero-DB-Read: Test kodi bo'yicha server RAM xotirasidan darhol olish (0 DB SELECT)."""
    if not test_code:
        return None
    clean_code = str(test_code).upper().strip().replace("#", "")

    _ensure_tests_cache_loaded()

    with _RAM_CACHE_LOCK:
        if clean_code in _GLOBAL_TESTS_BY_CODE:
            cached_id = _GLOBAL_TESTS_BY_CODE[clean_code]
            if cached_id in _GLOBAL_TESTS_CACHE:
                return dict(_GLOBAL_TESTS_CACHE[cached_id])
        return None


def get_test_by_id(test_id: int) -> Optional[Dict[str, Any]]:
    """Zero-DB-Read: Test ID bo'yicha server RAM xotirasidan darhol olish (0 DB SELECT)."""
    if not test_id or int(test_id) <= 0:
        return None
    tid = int(test_id)

    _ensure_tests_cache_loaded()

    with _RAM_CACHE_LOCK:
        if tid in _GLOBAL_TESTS_CACHE:
            return dict(_GLOBAL_TESTS_CACHE[tid])
        return None


get_test = get_test_by_id


def get_all_tests() -> List[Dict[str, Any]]:
    """Zero-DB-Read: Barcha testlar ro'yxatini to'g'ridan-to'g'ri server RAM xotirasidan qaytarish."""
    _ensure_tests_cache_loaded()
    with _RAM_CACHE_LOCK:
        return [dict(t) for t in _GLOBAL_ALL_TESTS_LIST]



def toggle_test_status(test_id: int) -> Optional[int]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT is_active FROM tests WHERE id = {_ph()}", (test_id,))
        row = cur.fetchone()
        if not row:
            return None
        d = _row_to_dict(row)
        new_status = 0 if d["is_active"] == 1 else 1
        cur.execute(f"UPDATE tests SET is_active = {_ph()} WHERE id = {_ph()}", (new_status, test_id))
        conn.commit()
        invalidate_test_cache(test_id)
        return new_status
    except Exception as e:
        print(f"Error toggling test: {e}")
        return None
    finally:
        _close_conn(conn)


def set_test_active_status(test_id: int, status: int) -> bool:
    conn = get_connection()
    try:
        cur = conn.cursor()
        now_ts = int(time.time())
        if not status:
            cur.execute(
                f"UPDATE tests SET is_active = 0, stopped_at = {_ph()} WHERE id = {_ph()}",
                (now_ts, test_id)
            )
        else:
            cur.execute(
                f"UPDATE tests SET is_active = 1 WHERE id = {_ph()}",
                (test_id,)
            )
        conn.commit()
        invalidate_test_cache(test_id)
        return True
    except Exception as e:
        print(f"Error setting test active status: {e}")
        return False
    finally:
        _close_conn(conn)


def get_test_submission_bounds(test_id: int) -> Dict[str, Any]:
    """Test uchun birinchi va oxirgi topshirilgan vaqtlarni qaytaradi."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT MIN(submitted_at), MAX(submitted_at), COUNT(*) FROM submissions WHERE test_id = {_ph()}",
            (test_id,)
        )
        row = cur.fetchone()
        if row:
            if isinstance(row, dict):
                return {
                    "first_submitted_at": row.get("min") or row.get("MIN(submitted_at)"),
                    "last_submitted_at": row.get("max") or row.get("MAX(submitted_at)"),
                    "count": row.get("count") or 0
                }
            min_ts = row[0]
            max_ts = row[1]
            count = row[2]
            return {
                "first_submitted_at": min_ts,
                "last_submitted_at": max_ts,
                "count": count or 0
            }
        return {"first_submitted_at": None, "last_submitted_at": None, "count": 0}
    except Exception as e:
        print(f"Error getting submission bounds: {e}")
        return {"first_submitted_at": None, "last_submitted_at": None, "count": 0}
    finally:
        _close_conn(conn)


def get_all_tests_submission_bounds() -> Dict[int, Dict[str, Any]]:
    """Zero-DB-Read: Barcha testlar uchun birinchi va oxirgi topshirilgan vaqtlarni RAM keshdan darhol qaytarish."""
    global _CACHE_TEST_BOUNDS, _CACHE_TEST_BOUNDS_TIME
    now = time.time()
    with _RAM_CACHE_LOCK:
        if _CACHE_TEST_BOUNDS and (now - _CACHE_TEST_BOUNDS_TIME < 30.0):
            return dict(_CACHE_TEST_BOUNDS)

    conn = get_connection()
    res = {}
    try:
        cur = conn.cursor()
        cur.execute("SELECT test_id, MIN(submitted_at) as min_ts, MAX(submitted_at) as max_ts, COUNT(*) as cnt FROM submissions GROUP BY test_id")
        rows = cur.fetchall()
        for row in rows:
            if isinstance(row, dict):
                tid = row.get("test_id")
                if tid:
                    res[int(tid)] = {
                        "first_submitted_at": row.get("min_ts") or row.get("min"),
                        "last_submitted_at": row.get("max_ts") or row.get("max"),
                        "count": row.get("cnt") or row.get("count") or 0
                    }
            else:
                tid = row[0]
                if tid:
                    res[int(tid)] = {
                        "first_submitted_at": row[1],
                        "last_submitted_at": row[2],
                        "count": row[3] or 0
                    }
        with _RAM_CACHE_LOCK:
            _CACHE_TEST_BOUNDS = res
            _CACHE_TEST_BOUNDS_TIME = now
        return res
    except Exception as e:
        print(f"Error getting all submission bounds: {e}")
        return res
    finally:
        _close_conn(conn)



def update_test_time_limit(test_id: int, time_limit_min: int) -> bool:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE tests SET time_limit_min = {_ph()} WHERE id = {_ph()}",
            (time_limit_min, test_id)
        )
        conn.commit()
        invalidate_test_cache(test_id)
        try:
            from core import invalidate_dashboard_cache
            invalidate_dashboard_cache()
        except Exception:
            pass
        return True
    except Exception as e:
        print(f"Error updating test time limit: {e}")
        return False
    finally:
        _close_conn(conn)


def set_test_results_published(test_id: int, published: bool = True) -> bool:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE tests SET results_published = {_ph()} WHERE id = {_ph()}",
            (1 if published else 0, test_id)
        )
        conn.commit()
        invalidate_test_cache(test_id)
        return True
    except Exception as e:
        print(f"Error publishing results: {e}")
        return False
    finally:
        _close_conn(conn)


def is_test_results_published(test_id: int) -> bool:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT results_published FROM tests WHERE id = {_ph()}", (test_id,))
        row = cur.fetchone()
        if row:
            d = _row_to_dict(row)
            return bool(d.get("results_published", 0))
        return False
    except Exception:
        return False
    finally:
        _close_conn(conn)


def get_test_submissions_with_users(test_id: int, include_late: bool = False) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        late_cond = "" if include_late else "AND (s.is_late = 0 OR s.is_late IS NULL)"
        cur.execute(f"""
        SELECT s.*, t.title as test_title, t.test_code, t.results_published, t.youtube_url
        FROM submissions s
        JOIN tests t ON s.test_id = t.id
        WHERE s.test_id = {_ph()} {late_cond}
        ORDER BY s.score DESC, s.submitted_at ASC
        """, (test_id,))
        rows = cur.fetchall()
        out = []
        for r in rows:
            d = _row_to_dict(r)
            if d:
                d["grade"] = calculate_grade(d.get("score", 0.0))
                out.append(d)
        return out
    finally:
        _close_conn(conn)


def delete_test(test_id: int) -> bool:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"DELETE FROM submissions WHERE test_id = {_ph()}", (test_id,))
        cur.execute(f"DELETE FROM tests WHERE id = {_ph()}", (test_id,))
        conn.commit()
        invalidate_test_cache(test_id)
        try:
            from core import invalidate_dashboard_cache
            invalidate_dashboard_cache()
        except Exception:
            pass
        print(f"🗑 Test #{test_id} bazadan va RAM keshdan muvaffaqiyatli o'chirildi.")
        return True
    except Exception as e:
        print(f"❌ Test #{test_id} o'chirishda xatolik: {e}")
        return False
    finally:
        _close_conn(conn)


# ──────────────────────────────────────────────────────────
# ADMINS
# ──────────────────────────────────────────────────────────

_admin_cache = {}

def invalidate_admins_cache():
    global _ALL_ADMINS_CACHE, _admin_cache
    with _RAM_CACHE_LOCK:
        _ALL_ADMINS_CACHE = (0.0, [])
        _admin_cache.clear()

def is_admin(tg_id: int, super_admin_id: int = 8039427064) -> bool:
    try:
        from core.config import get_config_admin_ids
        cfg_ids = set(get_config_admin_ids())
    except Exception:
        cfg_ids = {8039427064, 1181227767}
    if tg_id == super_admin_id or tg_id in cfg_ids or tg_id in {8039427064, 1181227767}:
        return True
    now = time.time()
    cached = _admin_cache.get(tg_id)
    if cached and now < cached[1]:
        return cached[0]

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT tg_id FROM admins WHERE tg_id = {_ph()}", (tg_id,))
        row = cur.fetchone()
        is_adm = bool(row)
        _admin_cache[tg_id] = (is_adm, now + CACHE_TTL_ADMINS)
        return is_adm
    finally:
        _close_conn(conn)


def add_admin(tg_id: int, fullname: str = "Admin", username: Optional[str] = None, added_by: int = 0) -> bool:
    conn = get_connection()
    try:
        cur = conn.cursor()
        now = int(time.time())
        if USE_POSTGRES:
            cur.execute("""
            INSERT INTO admins (tg_id, fullname, username, added_by, created_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (tg_id) DO UPDATE SET
                fullname = EXCLUDED.fullname,
                username = EXCLUDED.username
            """, (tg_id, fullname, username, added_by, now))
        else:
            cur.execute("""
            INSERT INTO admins (tg_id, fullname, username, added_by, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(tg_id) DO UPDATE SET
                fullname=excluded.fullname,
                username=excluded.username
            """, (tg_id, fullname, username, added_by, now))
        conn.commit()
        invalidate_admins_cache()
        return True
    except Exception as e:
        print(f"Error adding admin: {e}")
        return False
    finally:
        _close_conn(conn)


def remove_admin(tg_id: int) -> bool:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"DELETE FROM admins WHERE tg_id = {_ph()}", (tg_id,))
        conn.commit()
        invalidate_admins_cache()
        return True
    except Exception:
        return False
    finally:
        _close_conn(conn)


def get_all_admins() -> List[Dict[str, Any]]:
    global _ALL_ADMINS_CACHE
    now = time.time()
    with _RAM_CACHE_LOCK:
        ts, cached = _ALL_ADMINS_CACHE
        if (now - ts) < CACHE_TTL_ADMINS and cached:
            return [dict(r) for r in cached]

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM admins ORDER BY created_at ASC")
        rows = cur.fetchall()
        admins = [_row_to_dict(r) for r in rows if r]
        with _RAM_CACHE_LOCK:
            _ALL_ADMINS_CACHE = (time.time(), admins)
        return admins
    finally:
        _close_conn(conn)


def get_all_users(include_deleted: bool = False) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        del_cond = "" if include_deleted else "WHERE u.deleted_at IS NULL"
        cur.execute(f"""
        SELECT u.*, COUNT(s.id) as tests_count, MAX(s.submitted_at) as last_test_at
        FROM users u
        LEFT JOIN submissions s ON u.tg_id = s.user_tg_id
        {del_cond}
        GROUP BY u.id, u.tg_id, u.fullname, u.phone, u.username, u.status, u.pin_code, u.registered_at, u.deleted_at, u.deleted_by, u.previous_status, u.is_anonymized
        ORDER BY u.registered_at DESC
        """)
        rows = cur.fetchall()
        return [_row_to_dict(r) for r in rows if r]
    finally:
        _close_conn(conn)


def get_recent_users(limit: int = 15) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"""
        SELECT id, tg_id, fullname, phone, username, status, registered_at
        FROM users
        ORDER BY id DESC
        LIMIT {_ph()}
        """, (limit,))
        rows = cur.fetchall()
        return [_row_to_dict(r) for r in rows if r]
    finally:
        _close_conn(conn)



# ──────────────────────────────────────────────────────────
# SUBMISSIONS / RESULTS
# ──────────────────────────────────────────────────────────

def parse_answers_json(raw: Any) -> Dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


DASHES_PATTERN = r"[\u2212\u2013\u2014\u2012\u2015\uFE63\uFF0D\u00ad]"
MULT_SYMS_PATTERN = r"[\u00d7\u00b7\u2022\u2219\u22c5\u2715\u2716]"


def normalize_answer(ans: Any) -> str:
    if ans is None:
        return ""
    s = str(ans).strip()

    # 1. Bo'shliqlar, ko'rinmas belgilar va dollar belgilarini olib tashlash
    s = re.sub(r"[\s\u200b\u200c\u200d\u00a0\uFEFF\$]", "", s)

    # 2. Barcha klaviaturalardagi minus/chiziqchalarni bitta standart '-' belgisiga keltirish
    s = re.sub(DASHES_PATTERN, "-", s)

    # 3. Barcha ko'paytirish belgilarini '*' ga keltirish
    s = re.sub(MULT_SYMS_PATTERN, "*", s)
    s = re.sub(r"\\+(?:cdot|times)\b", "*", s)

    # 4. Bo'lish belgilarini '/' ga keltirish
    s = re.sub(r"[\u00f7]", "/", s)

    # 5. O'nlik kasrlardagi vergul: 2,5 -> 2.5
    s = re.sub(r"(\d+),(\d+)", r"\1.\2", s)

    # 6. Plus-minus belgisi
    s = re.sub(r"(\+\/\-|\+\s*\-|\+\-)", "±", s)

    # 7. Pi soni: \pi, pi, PI -> π
    s = re.sub(r"(^|[^a-zA-Z])\\*pi(?![a-zA-Z])", r"\g<1>π", s, flags=re.IGNORECASE)

    # 7b. Cheksizlik (Infinity): \infty, infty, infinity, inf, cheksiz, cheksizlik -> ∞
    s = s.replace(r"\\infty", "∞").replace(r"\infty", "∞").replace(r"\inf", "∞")
    s = re.sub(r"(?:infinity|infty|inf|cheksiz(?:lik)?)\b", "∞", s, flags=re.IGNORECASE)
    # Musbat cheksizlik: +∞ -> ∞
    s = re.sub(r"(?<![0-9a-zA-Z])\+∞", "∞", s)

    # Oraliqlardagi vergul: [0, ∞) -> [0;∞), (-∞, 5) -> (-∞;5)
    s = re.sub(r"([0-9a-zA-Z∞\.\-\+]+),([0-9a-zA-Z∞\.\-\+]+)", r"\1;\2", s)

    # 8. Darajalarni standart ^ shakliga keltirish
    for sup, norm in [("⁰", "^0"), ("¹", "^1"), ("²", "^2"), ("³", "^3"), ("⁴", "^4"),
                      ("⁵", "^5"), ("⁶", "^6"), ("⁷", "^7"), ("⁸", "^8"), ("⁹", "^9"), ("ⁿ", "^n")]:
        s = s.replace(sup, norm)

    # 9. LaTeX residuallari: \frac, \sqrt, \sqrt[n]
    while re.search(r"\\+sqrt\[([^\]]+)\]\{([^{}]+)\}", s):
        s = re.sub(r"\\+sqrt\[([^\]]+)\]\{([^{}]+)\}", r"\1√\2", s)
    while re.search(r"\\+d?frac\{([^{}]+)\}\{([^{}]+)\}", s):
        s = re.sub(r"\\+d?frac\{([^{}]+)\}\{([^{}]+)\}", r"\1/\2", s)
    while "sqrt{" in s:
        s = re.sub(r"\\+sqrt\{([^{}]+)\}", r"√\1", s)
    s = re.sub(r"\\+sqrt([0-9a-zA-Z]+)", r"√\1", s)
    s = re.sub(r"sqrt\(", "√(", s)
    s = s.replace("sqrt", "√")

    # Ildizlar va darajalar
    s = s.replace("cbrt", "∛")

    # Ildiz qavslari: "√(29)" -> "√29", "5√(32)" -> "5√32"
    while re.search(r"([0-9a-zA-Z]*√)\(([0-9a-zA-Z]+)\)", s):
        s = re.sub(r"([0-9a-zA-Z]*√)\(([0-9a-zA-Z]+)\)", r"\1\2", s)
    while re.search(r"^\(([0-9a-zA-Z]*√[0-9a-zA-Z]+)\)$", s):
        s = re.sub(r"^\(([0-9a-zA-Z]*√[0-9a-zA-Z]+)\)$", r"\1", s)

    # Raqam va qavsli ildiz: "8(√58)" -> "8√58" (lekin 32(√2+1) emas!)
    s = re.sub(r"(\d)\((√[0-9a-zA-Z]+)\)", r"\1\2", s)

    # Tashqi ortiqcha qavslar: (-3π/2) -> -3π/2
    while s.startswith("(") and s.endswith(")") and s.count("(") == 1:
        s = s[1:-1]

    # "x = ", "x1 = ", "javob:" kabi prefikslarni tozalash
    s = re.sub(r"^(?:[a-zA-Z]|x\d*|y\d*|k\d*)\s*=\s*", "", s)
    s = re.sub(r"^(?:javob|ans)\s*:\s*", "", s, flags=re.IGNORECASE)

    # Ortiqcha figurali qavslar va sleshlar
    s = re.sub(r"\{([^{}]+)\}", r"\1", s)
    s = s.replace("\\", "")

    return s.strip().lower()


def eval_numeric_val(expr: str) -> Optional[float]:
    """Matematik ifodaning sonli qiymatini xavfsiz hisoblash (π, √, kasrlar va darajalar bilan)."""
    s = normalize_answer(expr)
    if not s or "±" in s:
        return None

    # Agar x, y, a kabi algebraik noma'lumlar bo'lsa, sonli hisoblab bo'lmaydi
    clean_for_vars = re.sub(r"(math|sqrt|pi|abs|exp)", "", s)
    if re.search(r"[a-df-oq-z]", clean_for_vars):
        return None

    # 1. LaTeX va maxsus n-darajali ildizlar
    s = re.sub(r"\\+sqrt\[([^\]]+)\]\{([^{}]+)\}", r"((\2)**(1/(\1)))", s)
    s = re.sub(r"∛\(([^()]+)\)", r"((\1)**(1/3))", s)
    s = re.sub(r"∛(\d+(?:\.\d+)?)", r"((\1)**(1/3))", s)
    s = re.sub(r"∜\(([^()]+)\)", r"((\1)**(1/4))", s)
    s = re.sub(r"∜(\d+(?:\.\d+)?)", r"((\1)**(1/4))", s)

    # 2. Kvadrat ildiz: avval √(...) va √son larni math.sqrt(...) ga aylantiramiz
    s = re.sub(r"√\(([^()]+)\)", r"math.sqrt(\1)", s)
    s = re.sub(r"√(\d+(?:\.\d+)?)", r"math.sqrt(\1)", s)
    s = s.replace("√", "math.sqrt")

    # 3. math.sqrt oldidagi ko'paytirish (masalan: 32math.sqrt(2) -> 32*math.sqrt(2))
    s = re.sub(r"(\d)math\.sqrt", r"\1*math.sqrt", s)
    s = re.sub(r"(\))math\.sqrt", r"\1*math.sqrt", s)

    # 4. Pi va ko'paytirish
    s = re.sub(r"(\d)π", r"\1*math.pi", s)
    s = re.sub(r"(\))π", r"\1*math.pi", s)
    s = re.sub(r"π(\d)", r"math.pi*\1", s)
    s = re.sub(r"(\))\s*\(", r"\1*(", s)
    s = re.sub(r"(\d)\s*\(", r"\1*(", s)
    s = re.sub(r"(\))\s*(\d)", r"\1*\2", s)
    s = s.replace("π", "math.pi")
    s = s.replace("^", "**")

    allowed = set("0123456789.+-*/()math.sqrtpi ")
    if not set(s).issubset(allowed):
        return None
    try:
        val = eval(s, {"__builtins__": None, "math": math})
        return float(val)
    except Exception:
        return None


def count_binary_plus_minus(s: str) -> int:
    """
    Ifodadagi binar qo'shish va ayirish amallarini sanash.
    Masalan:
      '133+5/13' -> 1 ('+')
      '140-5/13' -> 1 ('-')
      '-133-5/13' -> 1 (birinchi '-' unar, ikkinchi '-' binar)
      '1734/13'  -> 0
      '-1734/13' -> 0
      '85/√13'   -> 0
      '(85√13)/13' -> 0
    """
    if not s:
        return 0
    cnt = 0
    for i, ch in enumerate(s):
        if ch == "+":
            cnt += 1
        elif ch == "-":
            if i > 0 and s[i - 1] not in ("(", "*", "/", "^", "±", "[", "{"):
                cnt += 1
    return cnt


def normalize_uzbek_text(s: Any) -> str:
    """Ona tili matnlarini me'yorga keltirish (tutuq belgilari, defislar, probellar, registri)."""
    if s is None:
        return ""
    text = str(s).strip()
    # 1. Barcha apostrof va tutuq belgilarini standart ' belgisiga keltirish
    text = re.sub(r"[\u2018\u2019\u02BB\u02BC\u00B4`ʼʻ\‘\’\']", "'", text)
    # 2. Barcha defis va tirelarni '-' belgisiga keltirish
    text = re.sub(r"[\u2212\u2013\u2014\u2012\u2015\uFE63\uFF0D\u00ad]", "-", text)
    # 3. Qo'shtirnoqlarni tozalash
    text = re.sub(r'["“”«»]', '"', text)
    # 4. So'ngidagi keraksiz nuqtani tozalash (masalan: "tiniq." -> "tiniq")
    text = text.strip()
    if text.endswith(".") and not text.endswith("..") and not re.search(r"\d\.\d$", text):
        text = text[:-1].strip()
    # 5. Ketma-ket kelgan bo'shliqlarni bittaga keltirish
    text = re.sub(r"\s+", " ", text)
    return text.lower().strip()


def extract_uzbek_alternatives(c_str: str) -> List[str]:
    """
    Ona tili kalitlaridagi alternativ variantlar va o'qituvchi izohlarini ajratish:
    1. Izohlarni tozalash: "(28 talik katakka sig'dirish kerak)", "(: xato) (- to'g'ri)"
    2. Qo'shimcha variantlar: "tengdoshlariga (dan)" -> ["tengdoshlariga", "tengdoshlaridan"]
    3. Sleshli variantlar: "yor og'zi/labi" -> ["yor og'zi", "yor labi", "yor og'zi/labi", "og'zi", "labi"]
    4. Belgilar: ": -" -> [": -", ":-", "-", ":"]
    5. Bo'luvchilar: ";", "|", "yoki", "or"
    """
    raw = str(c_str).strip()
    norm = normalize_uzbek_text(raw)
    results = set()
    results.add(norm)

    # 1. Agar tinish belgilari bo'lsa (masalan: ": -")
    if ": -" in norm or ":-" in norm:
        results.update([": -", ":-", "-", ":", "- :"])

    # 2. O'qituvchi izohlarini tozalash: masalan "(28 talik katakka sig'dirish kerak)", "(: xato)", "(- to'g'ri)"
    cleaned = re.sub(r"\s*\([^)]*(?:katak|sig'dir|kerak|xato|to'g'ri|izoh)[^)]*\)", "", norm).strip()
    if cleaned and cleaned != norm:
        results.add(cleaned)

    # 3. Qo'shimchalar varianti: masalan "tengdoshlariga (dan)" yoki "so'zga(dan)"
    m_suf = re.search(r"(\b\w+)(?:ga|ka|qa|da|ni|ning)?\s*\((dan|ga|ka|qa|da|ni|ning|lar|i|si)\)", norm)
    if m_suf:
        base_word = m_suf.group(1)
        suffix = m_suf.group(2)
        without_paren = re.sub(r"\s*\([^)]*\)", "", norm).strip()
        results.add(without_paren)
        if without_paren.endswith("ga") or without_paren.endswith("da"):
            results.add(without_paren[:-2] + suffix)
        elif without_paren.endswith("ka") or without_paren.endswith("qa"):
            results.add(without_paren[:-2] + suffix)
        else:
            results.add(base_word + suffix)

    # 4. Sleshli variantlar: masalan "yor og'zi/labi" yoki "katta/kichik"
    if "/" in norm:
        parts = [p.strip() for p in norm.split("/") if p.strip()]
        if len(parts) >= 2:
            p1, p2 = parts[0], parts[1]
            results.add(p1)
            results.add(p2)
            words1 = p1.split()
            if len(words1) > 1:
                results.add(" ".join(words1[:-1]) + " " + p2)
            for p in parts:
                for w in p.split():
                    if len(w) > 2:
                        results.add(w)

    # 5. Bo'luvchilar (;, |, yoki, or)
    for sep in [";", "|", " yoki ", " or "]:
        if sep in norm:
            for piece in norm.split(sep):
                if piece.strip():
                    results.add(piece.strip())

    return [r for r in results if r]


def _is_minor_typo(s1: str, s2: str) -> bool:
    """Kichik imlo xatosi (1 ta harf farqi) mavjudligini aniqlash"""
    if not s1 or not s2:
        return False
    if len(s1) < 4 or len(s2) < 4:
        return False
    if abs(len(s1) - len(s2)) > 1:
        return False
    if s1[0] != s2[0]:
        return False
    diff = 0
    i, j = 0, 0
    while i < len(s1) and j < len(s2):
        if s1[i] != s2[j]:
            diff += 1
            if diff > 1:
                return False
            if len(s1) > len(s2):
                i += 1
            elif len(s2) > len(s1):
                j += 1
            else:
                i += 1
                j += 1
        else:
            i += 1
            j += 1
def is_punctuation_answer(s: Any) -> bool:
    """Tekshirilayotgan javob tinish belgilari (qo'shtirnoq, tire, vergul, ikki nuqta, nuqta va h.k.) dan iboratligini aniqlash"""
    if not s:
        return False
    cleaned = str(s).strip()
    if not cleaned:
        return False
    non_punct = re.sub(r'[\s"“”«»\u2018\u2019\u02BB\u02BC\u00B4`ʼʻ\‘\’\'\u2212\u2013\u2014\u2012\u2015\uFE63\uFF0D\u00ad\-_.,:;!?( )\[\]{}/\\|]', '', cleaned)
    return len(non_punct) == 0


def normalize_punctuation_seq(s: str) -> str:
    """Tinish belgilari ketma-ketligini standartlashtirish (qo'shtirnoq, tire, apostrof, probellar)."""
    if not s:
        return ""
    t = re.sub(r'["“”«»]', '"', str(s))
    t = re.sub(r"[\u2018\u2019\u02BB\u02BC\u00B4`ʼʻ\‘\’\']", "'", t)
    t = re.sub(r"[\u2212\u2013\u2014\u2012\u2015\uFE63\uFF0D\u00ad]", "-", t)
    t = re.sub(r"\s+", "", t)
    return t


def check_punctuation_match(u_str: str, c_str: str) -> bool:
    """
    Ona tili 37-savol kabi tinish belgilari savollarini to'liq va moslashuvchan tekshirish:
    Masalan: '“ , — , — : , ” .' kalitiga nisbatan o'quvchi tomonidan yozilgan
    turli formatdagi javoblarni (probelli, probelsiz, tire va qo'shtirnoq turlari, vergulli yoki vergulsiz)
    100% to'g'ri deb qabul qiladi.
    """
    u_clean = normalize_punctuation_seq(u_str)
    c_clean = normalize_punctuation_seq(c_str)
    if not u_clean or not c_clean:
        return False
    if u_clean == c_clean:
        return True

    # 1. Oxiridagi nuqtasiz moslik (nuqta qo'yilgan yoki tushirib qoldirilgan)
    u_no_dot = u_clean[:-1] if u_clean.endswith(".") else u_clean
    c_no_dot = c_clean[:-1] if c_clean.endswith(".") else c_clean
    if u_no_dot == c_no_dot:
        return True

    # 2. Ketma-ket vergullarni birlashtirish
    u_no_sep = re.sub(r",+", ",", u_clean).strip(",")
    c_no_sep = re.sub(r",+", ",", c_clean).strip(",")
    if u_no_sep == c_no_sep:
        return True

    # 3. Tinish belgilari orasidagi qo'shimcha vergullarni normallashtirish (masalan: "-:" vs "-,:")
    u_collapse = re.sub(r"(?<=[^\w\s,]),(?=[^\w\s,])", "", u_clean)
    c_collapse = re.sub(r"(?<=[^\w\s,]),(?=[^\w\s,])", "", c_clean)
    if u_collapse == c_collapse:
        return True
    if u_collapse.rstrip(".") == c_collapse.rstrip("."):
        return True

    # 4. Vergulsiz strukturaviy belgilar ketma-ketligi (masalan: ", -, -, :, " vs ", -, - :, ")
    u_no_comma = u_clean.replace(",", "")
    c_no_comma = c_clean.replace(",", "")
    if u_no_comma and u_no_comma == c_no_comma:
        return True
    if u_no_comma and u_no_comma.rstrip(".") == c_no_comma.rstrip("."):
        return True

    # 5. Tire va ikki nuqta o'rin almashuvi yoki variantlari (masalan: ": -" vs ":-" vs "- :" vs "-:")
    if set(c_no_comma) == {":", "-"} and set(u_no_comma) == {":", "-"}:
        return True

    # 6. Belgilar to'plami va uzunligi bir xil bo'lsa
    if set(c_no_comma) == set(u_no_comma) and len(c_no_comma) == len(u_no_comma):
        return True

    return False


def check_answer_match(user_ans: Any, correct_ans: Any) -> Tuple[bool, float, str]:
    """
    Foydalanuvchi javobini to'g'ri kalitga solishtirish va moslik koeffitsientini hisoblash:
    Returns:
        (is_matched, ratio, status)
        - is_matched: bool (True agar qabul qilinsa)
        - ratio: float (1.0 = 100% to'liq to'g'ri, 0.3 = 30% qisman, 0.0 = noto'g'ri)
        - status: str ("correct", "partial", "incorrect")
    """
    if user_ans is None or correct_ans is None:
        return (False, 0.0, "incorrect")
    u_str = str(user_ans).strip()
    c_str = str(correct_ans).strip()
    if not u_str or not c_str:
        return (False, 0.0, "incorrect")

    # 0. Tinish belgilari savoli tekshiruvi (masalan, 37-savol: "“ , — , — : , ” ." yoki ": -")
    if is_punctuation_answer(c_str) or is_punctuation_answer(u_str):
        if check_punctuation_match(u_str, c_str):
            return (True, 1.0, "correct")

    # 1. Ona tili matnli solishtirish (tutuq belgilari, defislar, tinish belgilari)
    u_uz = normalize_uzbek_text(u_str)
    c_uz = normalize_uzbek_text(c_str)

    # 1a. Aniq matnli moslik
    if u_uz == c_uz:
        return (True, 1.0, "correct")

    # 1b. Probellarsiz moslik (masalan: "sabab - oqibat" vs "sabab-oqibat", ": -" vs ":-")
    if u_uz.replace(" ", "") == c_uz.replace(" ", ""):
        return (True, 1.0, "correct")

    # 1b-1. Tutuq belgisi (') va probellar farqi (masalan: "talim" vs "ta'lim", "marifat" vs "ma'rifat")
    u_no_tutuq = re.sub(r"['\s\-_.,:;\"`ʼʻ\(\)]", "", u_uz)
    c_no_tutuq = re.sub(r"['\s\-_.,:;\"`ʼʻ\(\)]", "", c_uz)
    if u_no_tutuq and u_no_tutuq == c_no_tutuq:
        return (True, 1.0, "correct")

    # 1b-2. Kichik imlo xatosi (1 ta harf farqi, masalan: x/h yoki harf adashishi)
    if _is_minor_typo(u_no_tutuq, c_no_tutuq):
        return (True, 1.0, "correct")

    # 1c. Ona tili alternativ variantlari (qo'shimchalar, sleshlar, izohlar)
    c_alts = extract_uzbek_alternatives(c_str)
    for alt in c_alts:
        alt_no = re.sub(r"['\s\-_.,:;\"`ʼʻ\(\)]", "", alt)
        if u_uz == alt or u_uz.replace(" ", "") == alt.replace(" ", "") or (u_no_tutuq and u_no_tutuq == alt_no) or _is_minor_typo(u_no_tutuq, alt_no):
            return (True, 1.0, "correct")

    # 1d. O'quvchi javobida ham ajratuvchi bo'lsa (masalan: "yor og'zi yoki yor labi")
    for sep in [";", "/", "|", " yoki ", " or "]:
        if sep in u_uz:
            for u_part in u_uz.split(sep):
                u_p = u_part.strip()
                if u_p in c_alts or any(u_p.replace(" ", "") == a.replace(" ", "") for a in c_alts):
                    return (True, 1.0, "correct")

    # 2. Kalitda bir nechta to'g'ri variant berilgan bo'lsa
    if any(sep in c_str for sep in [";", "|", "yoki", "or"]):
        parts = [p.strip() for p in re.split(r";|\||\byoki\b|\bor\b", c_str) if p.strip()]
        best_res = (False, 0.0, "incorrect")
        for p in parts:
            res = check_answer_match(user_ans, p)
            if res[1] > best_res[1]:
                best_res = res
            if best_res[1] >= 1.0:
                return best_res
        if best_res[0]:
            return best_res

    # 3. Matematik formulalar / ifodalar (agar mavjud bo'lsa)
    u = normalize_answer(u_str)
    c = normalize_answer(c_str)

    if u and c:
        if u == c:
            return (True, 1.0, "correct")
        if u.replace("*", "") == c.replace("*", ""):
            return (True, 1.0, "correct")
        if "+" in u and "+" in c:
            u_terms = sorted([t.strip().replace("*", "") for t in u.split("+") if t.strip()])
            c_terms = sorted([t.strip().replace("*", "") for t in c.split("+") if t.strip()])
            if u_terms == c_terms:
                return (True, 1.0, "correct")

        num_u = eval_numeric_val(u)
        num_c = eval_numeric_val(c)
        if num_u is not None and num_c is not None:
            if (num_u > 1e-6 and num_c < -1e-6) or (num_u < -1e-6 and num_c > 1e-6):
                return (False, 0.0, "incorrect")
            if abs(num_u - num_c) < 1e-4:
                if count_binary_plus_minus(u) > count_binary_plus_minus(c):
                    return (True, 0.3, "partial")
                return (True, 1.0, "correct")

    # 4. Ona tili Milliy sertifikat ochiq savollari uchun Gemini AI tekshiruvi (fallback)
    if len(u_str) >= 2 and len(c_str) >= 2 and not u_str.isdigit():
        if ai_evaluate_open_answer(u_str, c_str):
            return (True, 1.0, "correct")

    return (False, 0.0, "incorrect")


def ai_evaluate_open_answer(student_answer: str, correct_answer: str) -> bool:
    """
    O'zbek tili fanidan Milliy sertifikat ochiq savollarini Gemini AI orqali tekshirish:
    1. Imlo va tinish belgilari: Kichik imlo xatolari (masalan, tutuq belgisi (') tushib qolishi yoki probellar)
       ballni kamaytirmasin, asosiy ma'no to'g'ri bo'lsa qabul qilinsin.
    2. A va B qismli savollar: Har bir qism alohida tekshirilsin.
    3. Qat'iy javob: Faqat "True" (to'g'ri) yoki "False" (xato) natijasi qaytariladi.
    """
    try:
        import os, requests
        gemini_key = (
            os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_API_KEY")
            or get_setting("gemini_api_key", "").strip()
        )
        if not gemini_key:
            return False

        prompt = (
            "Sen o'zbek tili fanidan Milliy sertifikat imtihonlarini tekshiruvchi aqlli yordamchisan.\n"
            "Senga o'quvchining yozma javobi va rasmiy kalit beriladi. O'quvchining javobini baholashda quyidagi qoidalarga amal qil:\n\n"
            "1. **Imlo va tinish belgilari:** Kichik imlo xatolari (masalan, tutuq belgisi (') tushib qolishi yoki probellar) ballni kamaytirmasin, asosiy ma'no to'g'ri bo'lsa qabul qilinsin.\n"
            "2. **A va B qismli savollar:** Agar savol a) va b) qismlardan iborat bo'lsa, har bir qism alohida tekshirilsin.\n"
            "3. **Qat'iy javob:** Faqat \"True\" (to'g'ri) yoki \"False\" (xato) natijasini qaytar.\n\n"
            f"- Rasmiy kalit: {correct_answer}\n"
            f"- O'quvchining javobi: {student_answer}"
        )

        models = ["gemini-2.5-flash-lite", "gemini-1.5-flash", "gemini-2.0-flash"]
        for m in models:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={gemini_key}"
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.0, "maxOutputTokens": 15}
                }
                res = requests.post(url, json=payload, timeout=2.5)
                if res.status_code == 200:
                    cand = res.json().get("candidates", [])
                    if cand:
                        text = cand[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip().lower()
                        return "true" in text
            except Exception:
                continue
    except Exception:
        pass
    return False


def is_answer_matching(user_ans: Any, correct_ans: Any) -> bool:
    """Oldingi kodlar bilan to'liq moslik uchun yordamchi funksiya."""
    matched, _, _ = check_answer_match(user_ans, correct_ans)
    return matched


def get_user_submission_for_test(test_id: int, user_tg_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT * FROM submissions WHERE test_id = {_ph()} AND user_tg_id = {_ph()}",
            (test_id, user_tg_id)
        )
        row = cur.fetchone()
        return _row_to_dict(row)
    finally:
        _close_conn(conn)


def get_user_submissions_map(user_tg_id: int) -> Dict[int, Dict[str, Any]]:
    """Foydalanuvchining barcha topshirgan testlarini {test_id: submission_dict} qilib RAM keshdan qaytaradi."""
    if not user_tg_id:
        return {}
    uid = int(user_tg_id)
    now = time.time()

    with _RAM_CACHE_LOCK:
        if uid in _USER_SUBMISSIONS_MAP_CACHE:
            ts, cached_map = _USER_SUBMISSIONS_MAP_CACHE[uid]
            if now - ts < 60.0:
                return dict(cached_map)

    conn = get_connection()
    res = {}
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT * FROM submissions WHERE user_tg_id = {_ph()}",
            (uid,)
        )
        rows = cur.fetchall()
        for r in rows:
            d = _row_to_dict(r)
            if d and d.get('test_id'):
                res[int(d['test_id'])] = d
        with _RAM_CACHE_LOCK:
            _USER_SUBMISSIONS_MAP_CACHE[uid] = (now, res)
            for tid in res.keys():
                _USER_SUBMITTED_IDS_CACHE[(uid, tid)] = (now, True)
        return res
    except Exception as e:
        print(f"Error get_user_submissions_map: {e}")
        return res
    finally:
        _close_conn(conn)


def has_user_submitted(test_id: int, user_tg_id: int) -> bool:
    """Zero-DB-Read: Foydalanuvchi ushbu testni topshirganligini tezkor RAM keshdan tekshirish."""
    if not user_tg_id or not test_id:
        return False
    uid = int(user_tg_id)
    tid = int(test_id)
    now = time.time()
    key = (uid, tid)

    with _RAM_CACHE_LOCK:
        if key in _USER_SUBMITTED_IDS_CACHE:
            ts, has_sub = _USER_SUBMITTED_IDS_CACHE[key]
            if now - ts < 120.0:
                return has_sub
        if uid in _USER_SUBMISSIONS_MAP_CACHE:
            ts, subs_map = _USER_SUBMISSIONS_MAP_CACHE[uid]
            if now - ts < 120.0:
                has_sub = tid in subs_map
                _USER_SUBMITTED_IDS_CACHE[key] = (now, has_sub)
                return has_sub

    # Keshda bo'lmasa, DB dan bitta yengil indexed so'rov bilan tekshiramiz
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT 1 FROM submissions WHERE user_tg_id = {_ph()} AND test_id = {_ph()} LIMIT 1",
            (uid, tid)
        )
        row = cur.fetchone()
        has_sub = bool(row)
        with _RAM_CACHE_LOCK:
            _USER_SUBMITTED_IDS_CACHE[key] = (now, has_sub)
        return has_sub
    finally:
        _close_conn(conn)



def get_submission_by_id(submission_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM submissions WHERE id = {_ph()}", (submission_id,))
        row = cur.fetchone()
        return _row_to_dict(row)
    finally:
        _close_conn(conn)


def set_submission_late_status(submission_id: int, is_late: int) -> bool:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE submissions SET is_late = {_ph()} WHERE id = {_ph()}",
            (is_late, submission_id)
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"Error updating late status: {e}")
        return False
    finally:
        _close_conn(conn)


def reject_submission(submission_id: int, reason: str = "") -> bool:
    """O'quvchining topshirgan javoblarini rad etadi / bekor qiladi (qabul qilmaydi)."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE submissions SET status = 'rejected', reject_reason = {_ph()} WHERE id = {_ph()}",
            (reason, submission_id)
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"Error rejecting submission: {e}")
        return False
    finally:
        _close_conn(conn)


def delete_submission(submission_id: int) -> bool:
    """O'quvchining topshirgan javoblarini o'chiradi (testni qayta topshirish imkonini beradi)."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"DELETE FROM submissions WHERE id = {_ph()}", (submission_id,))
        conn.commit()
        return True
    except Exception as e:
        print(f"Error deleting submission: {e}")
        return False
    finally:
        _close_conn(conn)


def get_key_and_score(q_data: Any, default_score: float) -> tuple:
    if isinstance(q_data, dict):
        ans = str(q_data.get("ans", q_data.get("answer", "")))
        try:
            score = float(q_data.get("score", q_data.get("ball", default_score)))
        except (ValueError, TypeError):
            score = default_score
        return ans, score
    else:
        return str(q_data or ""), default_score


def calculate_grade(score: float, correct_count: Optional[int] = None) -> str:
    if correct_count is not None and correct_count == 0 and score <= 0.0:
        return "—"
    if score <= 0.0:
        return "—"
    if score >= 70.0:
        return "A+"
    elif score >= 65.0:
        return "A"
    elif score >= 60.0:
        return "B+"
    elif score >= 55.0:
        return "B"
    elif score >= 50.0:
        return "C+"
    elif score >= 46.0:
        return "C"
    else:
        return "—"


def check_and_save_submission(test_id: int, user_tg_id: int, user_answers: Dict[str, str], is_late: int = 0) -> Dict[str, Any]:
    test = get_test_by_id(test_id)
    if not test:
        raise ValueError("Test topilmadi!")

    if not user_tg_id or int(user_tg_id) <= 0:
        raise ValueError("⚠️ Web orqali ishlash mumkin emas! Testni faqat rasmiy Telegram botimiz (@bm_rashtest_bot) va Mini ilova orqali topshirish mumkin.")

    user = get_user(user_tg_id) if user_tg_id else None
    fullname = user.get("fullname", "Foydalanuvchi") if isinstance(user, dict) else "Foydalanuvchi"
    phone = user.get("phone", "-") if isinstance(user, dict) else "-"

    existing = get_user_submission_for_test(test_id, user_tg_id)
    if existing:
        if is_admin(user_tg_id, ADMIN_ID):
            conn_del = get_connection()
            try:
                cur_del = conn_del.cursor()
                cur_del.execute(
                    f"DELETE FROM submissions WHERE id = {_ph()}", (existing["id"],)
                )
                conn_del.commit()
            finally:
                _close_conn(conn_del)
        else:
            raise ValueError("Siz ushbu testni allaqachon topshirgansiz! Qayta topshirish taqiqlanadi.")

    correct_answers_raw = json.loads(test["answers_json"])

    total_questions = 55
    correct_count = 0
    incorrect_count = 0
    unanswered_count = 0
    details = {}
    earned_score = 0.0
    total_possible_score = 0.0

    # 1-35 savollar (Variantli yopiq savollar, default 2.0 ball)
    for q in range(1, 36):
        key = str(q)
        q_raw = correct_answers_raw.get(key, "A")
        correct_ans, q_score = get_key_and_score(q_raw, default_score=2.0)
        total_possible_score += q_score
        user_val = user_answers.get(key, "")
        is_corr = False
        if not user_val or not str(user_val).strip():
            status = "unanswered"
            unanswered_count += 1
        elif is_answer_matching(user_val, correct_ans):
            is_corr = True
            status = "correct"
            correct_count += 1
            earned_score += q_score
        else:
            status = "incorrect"
            incorrect_count += 1
        details[key] = {
            "num": f"{q}-savol", "type": "choice_4",
            "user": user_val, "correct": correct_ans,
            "status": status,
            "score": q_score if is_corr else 0.0,
            "max_score": q_score
        }

    # 36, 37, 38, 39 savollar (1 ta variantli yopiq/ochiq savollar)
    for q in range(36, 40):
        key = str(q)
        q_raw = correct_answers_raw.get(key, "")
        if not q_raw and f"{key}a" in correct_answers_raw:
            q_raw = correct_answers_raw.get(f"{key}a", "")
        correct_ans, q_score = get_key_and_score(q_raw, default_score=1.5)
        total_possible_score += q_score
        user_val = user_answers.get(key, "")
        if not user_val and f"{key}a" in user_answers:
            user_val = user_answers.get(f"{key}a", "")
        is_matched, ratio, match_status = check_answer_match(user_val, correct_ans)
        if not user_val or not str(user_val).strip():
            status = "unanswered"
            unanswered_count += 1
            item_score = 0.0
        elif is_matched:
            status = "correct"
            correct_count += 1
            item_score = q_score
            earned_score += q_score
        else:
            status = "incorrect"
            incorrect_count += 1
            item_score = 0.0

        details[key] = {
            "num": f"{key}-savol", "type": "open",
            "user": user_val, "correct": correct_ans,
            "status": status,
            "score": item_score,
            "max_score": q_score,
            "ratio": ratio
        }

    # 40 dan 44 gacha 2 qismli (a va b) yopiq savollar (45-savol esse bo'lgani uchun 45a, 45b yo'q!)
    end_q = 44
    for q in range(40, end_q + 1):
        for sub in ["a", "b"]:
            key = f"{q}{sub}"
            q_raw = correct_answers_raw.get(key, "")
            correct_ans, q_score = get_key_and_score(q_raw, default_score=1.5)
            total_possible_score += q_score
            user_val = user_answers.get(key, "")
            is_matched, ratio, match_status = check_answer_match(user_val, correct_ans)
            if not user_val or not str(user_val).strip():
                status = "unanswered"
                unanswered_count += 1
                item_score = 0.0
            elif is_matched:
                status = "correct"
                correct_count += 1
                item_score = q_score
                earned_score += q_score
            else:
                status = "incorrect"
                incorrect_count += 1
                item_score = 0.0

            details[key] = {
                "num": f"{key}-savol", "type": "open",
                "user": user_val, "correct": correct_ans,
                "status": status,
                "score": item_score,
                "max_score": q_score,
                "ratio": ratio
            }

    # Test qismi (1-44) bali — maksimal 75 ball shkalasiga o'tkaziladi
    if correct_count == 0 or total_possible_score <= 0.0:
        test_score = 0.0
    else:
        test_score = round((earned_score / total_possible_score) * 75.0, 1)

    # 45-savol: O'quvchi kiritgan halol tahminiy ESSE bali (0 dan 75 gacha)
    raw_essay = user_answers.get("45", user_answers.get("essay_score", ""))
    essay_score = 0.0
    try:
        if raw_essay is not None and str(raw_essay).strip() != "":
            clean_essay = str(raw_essay).replace(",", ".").strip()
            essay_score = round(min(75.0, max(0.0, float(clean_essay))), 1)
    except Exception:
        essay_score = 0.0

    details["45"] = {
        "num": "45-savol (Esse)", "type": "essay",
        "user": str(essay_score), "correct": "Halol baholash (0 - 75)",
        "status": "correct" if essay_score > 0 else "unanswered",
        "score": essay_score,
        "max_score": 75.0
    }

    # Ona tili yakuniy bali: Test (max 75) va Esse (max 75) ning o'rta arifmetigi!
    final_score = round((test_score + essay_score) / 2.0, 1)
    max_total_score = 75.0
    total_questions = len(details)

    if correct_count == 0 and essay_score <= 0.0:
        grade = "—"
    else:
        grade = calculate_grade(final_score, correct_count=correct_count)

    percentage = round((final_score / max_total_score) * 100.0, 1)
    rasch_theta = 0.0

    now = int(time.time())
    submission_id = None
    last_err = None

    # ACID Tranzaksiya va 3 marta avtomatik qayta urinish (Atomic Writes & Retry Logic)
    for attempt in range(1, 4):
        conn = get_connection()
        try:
            if hasattr(conn, "autocommit"):
                conn.autocommit = False
            cur = conn.cursor()

            if USE_POSTGRES:
                cur.execute("""
                INSERT INTO submissions (
                    test_id, test_code, user_tg_id, fullname, phone,
                    answers_json, score, max_score, correct_count, total_count,
                    details_json, submitted_at, is_late
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_tg_id, test_id) DO NOTHING
                RETURNING id
                """, (
                    test["id"], test["test_code"], user_tg_id, fullname, phone,
                    json.dumps(user_answers, ensure_ascii=False), final_score, max_total_score,
                    correct_count, total_questions,
                    json.dumps(details, ensure_ascii=False), now, is_late
                ))
                sub_row = cur.fetchone()
                if sub_row:
                    submission_id = _row_to_dict(sub_row).get("id")
                else:
                    cur.execute(
                        "SELECT id FROM submissions WHERE user_tg_id = %s AND test_id = %s",
                        (user_tg_id, test["id"])
                    )
                    ex_row = cur.fetchone()
                    submission_id = _row_to_dict(ex_row).get("id") if ex_row else None
            else:
                cur.execute("""
                INSERT OR IGNORE INTO submissions (
                    test_id, test_code, user_tg_id, fullname, phone,
                    answers_json, score, max_score, correct_count, total_count,
                    details_json, submitted_at, is_late
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    test["id"], test["test_code"], user_tg_id, fullname, phone,
                    json.dumps(user_answers, ensure_ascii=False), final_score, max_total_score,
                    correct_count, total_questions,
                    json.dumps(details, ensure_ascii=False), now, is_late
                ))
                submission_id = cur.lastrowid
                if not submission_id or submission_id == 0:
                    cur.execute(
                        "SELECT id FROM submissions WHERE user_tg_id = ? AND test_id = ?",
                        (user_tg_id, test["id"])
                    )
                    ex_row = cur.fetchone()
                    submission_id = _row_to_dict(ex_row).get("id") if ex_row else None

            # Qoralamalarni bitta atomar tranzaksiya ichida tozalash (Autosave drafts purge)
            try:
                cur.execute(
                    f"DELETE FROM submission_drafts WHERE user_tg_id = {_ph()} AND test_id = {_ph()}",
                    (user_tg_id, test["id"])
                )
                cur.execute(
                    f"DELETE FROM user_test_drafts WHERE user_tg_id = {_ph()} AND test_id = {_ph()}",
                    (user_tg_id, test["id"])
                )
            except Exception:
                pass

            conn.commit()
            break
        except Exception as exc:
            last_err = exc
            try:
                conn.rollback()
            except Exception:
                pass
            if attempt == 3:
                raise last_err
            time.sleep(0.05 * (2 ** attempt))
        finally:
            _close_conn(conn)

    return {
        "submission_id": submission_id,
        "is_late": is_late,
        "submitted_at": now,
        "test_title": test["title"],
        "test_code": test["test_code"],
        "fullname": fullname,
        "score": final_score,
        "test_score": test_score,
        "essay_score": essay_score,
        "max_score": max_total_score,
        "percentage": percentage,
        "grade": grade,
        "rasch_theta": rasch_theta,
        "correct_count": correct_count,
        "incorrect_count": incorrect_count,
        "unanswered_count": unanswered_count,
        "details": details,
        "submitted_at": now
    }


def get_user_submissions(user_tg_id: int) -> List[Dict[str, Any]]:
    uid = int(user_tg_id)
    now = time.time()
    with _RAM_CACHE_LOCK:
        if uid in _USER_SUBMISSIONS_CACHE:
            ts, cached_list = _USER_SUBMISSIONS_CACHE[uid]
            if now - ts < CACHE_TTL_5S:
                return [dict(x) for x in cached_list]

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"""
        SELECT s.*, t.title as test_title, t.results_published, t.is_active
        FROM submissions s
        JOIN tests t ON s.test_id = t.id
        WHERE s.user_tg_id = {_ph()} ORDER BY s.id DESC
        """, (uid,))
        rows = cur.fetchall()
    finally:
        _close_conn(conn)
    results = []
    for r in rows:
        d = _row_to_dict(r)
        if not d:
            continue
        is_pub = bool(d.get("results_published", 0))
        d["results_published"] = is_pub
        is_rejected = (str(d.get("status", "")).strip().lower() == "rejected")
        d["is_rejected"] = is_rejected
        if is_rejected:
            d["grade"] = "Bekor qilingan"
            d["score"] = 0
            d["correct_count"] = 0
            d["incorrect_count"] = 0
            d["status_text"] = "Bekor qilingan"
        elif is_pub:
            d["grade"] = calculate_grade(d.get("score", 0))
        else:
            d["grade"] = "Kutilmoqda"
            d["score"] = None
            d["correct_count"] = None
            d["total_count"] = None
            d["details_json"] = "{}"
        results.append(d)

    with _RAM_CACHE_LOCK:
        _USER_SUBMISSIONS_CACHE[uid] = (now, [dict(x) for x in results])

    return results


def get_test_results_leaderboard(test_id: int) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"""
        SELECT fullname, phone, score, correct_count, submitted_at
        FROM submissions
        WHERE test_id = {_ph()} AND (is_late = 0 OR is_late IS NULL)
        ORDER BY score DESC, submitted_at ASC
        """, (test_id,))
        rows = cur.fetchall()
        return [_row_to_dict(r) for r in rows if r]
    finally:
        _close_conn(conn)


def get_tests_with_stats() -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
        SELECT t.*, COUNT(s.id) as submissions_count,
               COALESCE(AVG(s.score), 0) as avg_score,
               COALESCE(MAX(s.score), 0) as max_score_achieved
        FROM tests t
        LEFT JOIN submissions s ON t.id = s.test_id AND (s.is_late = 0 OR s.is_late IS NULL)
        GROUP BY t.id, t.test_code, t.title, t.subject, t.pdf_file_id, t.pdf_file_name,
                 t.answers_json, t.total_questions, t.time_limit_min, t.is_active,
                 t.key_access_code, t.results_published, t.created_at, t.created_by, t.created_by_name
        ORDER BY t.id DESC
        """)
        rows = cur.fetchall()
        return [_row_to_dict(r) for r in rows if r]
    finally:
        _close_conn(conn)


# ──────────────────────────────────────────────────────────
# RASCH MODEL INTEGRATION
# ──────────────────────────────────────────────────────────

def get_test_submissions_for_rasch(test_id: int) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"""
        SELECT user_tg_id, fullname, details_json, score, correct_count, submitted_at
        FROM submissions
        WHERE test_id = {_ph()} AND details_json IS NOT NULL AND details_json != ''
          AND (is_late = 0 OR is_late IS NULL)
        ORDER BY submitted_at ASC
        """, (test_id,))
        rows = cur.fetchall()
        return [_row_to_dict(r) for r in rows if r]
    finally:
        _close_conn(conn)


def evaluate_test_rasch(test_id: int, auto_update_db: bool = False) -> Optional[Dict[str, Any]]:
    try:
        from rasch_engine import evaluate_single_test
        res = evaluate_single_test(test_id, get_test_submissions_for_rasch)
        if res and auto_update_db and res.get("students"):
            item_score_map = {
                it["item_label"].replace("Q", ""): it.get("item_score", 1.0)
                for it in res.get("items", [])
            }
            conn = get_connection()
            try:
                cur = conn.cursor()
                for s in res["students"]:
                    student_id = s.get("student_id")
                    final_score = s.get("final_score", 0.0)
                    cur.execute(f"""
                        SELECT id, details_json FROM submissions
                        WHERE test_id = {_ph()} AND (user_tg_id = {_ph()} OR id = {_ph()})
                    """, (test_id, student_id, student_id))
                    row = cur.fetchone()
                    if row:
                        d = _row_to_dict(row)
                        sub_id = d["id"]
                        det_raw = d["details_json"]
                        try:
                            det = json.loads(det_raw) if det_raw else {}
                            for k, v in det.items():
                                if k in item_score_map:
                                    is_c = (v.get("status") == "correct")
                                    sc = item_score_map[k]
                                    v["score"] = sc if is_c else 0.0
                                    v["max_score"] = sc
                            new_det = json.dumps(det, ensure_ascii=False)
                            cur.execute(f"""
                                UPDATE submissions
                                SET score = {_ph()}, details_json = {_ph()}
                                WHERE id = {_ph()}
                            """, (final_score, new_det, sub_id))
                        except Exception:
                            cur.execute(
                                f"UPDATE submissions SET score = {_ph()} WHERE id = {_ph()}",
                                (final_score, sub_id)
                            )
                conn.commit()
            finally:
                _close_conn(conn)
        return res
    except ImportError:
        print("[rasch] rasch_engine.py topilmadi — Rasch baholash o'tkazib yuborildi.")
        return None
    except Exception as e:
        print(f"[rasch] Xatolik: {e}")
        return None


# ──────────────────────────────────────────────────────────
# PDF GENERATION HELPERS
# ──────────────────────────────────────────────────────────

CYRILLIC_TO_LATIN = {
    'А': 'A', 'а': 'a', 'Б': 'B', 'б': 'b', 'В': 'V', 'в': 'v',
    'Г': 'G', 'г': 'g', 'Д': 'D', 'д': 'd', 'Е': 'E', 'е': 'e',
    'Ё': 'Yo', 'ё': 'yo', 'Ж': 'J', 'ж': 'j', 'З': 'Z', 'з': 'z',
    'И': 'I', 'и': 'i', 'Й': 'Y', 'й': 'y', 'К': 'K', 'к': 'k',
    'Л': 'L', 'л': 'l', 'М': 'M', 'м': 'm', 'Н': 'N', 'н': 'n',
    'О': 'O', 'о': 'o', 'П': 'P', 'п': 'p', 'Р': 'R', 'р': 'r',
    'С': 'S', 'с': 's', 'Т': 'T', 'т': 't', 'У': 'U', 'у': 'u',
    'Ф': 'F', 'ф': 'f', 'Х': 'X', 'х': 'x', 'Ц': 'Ts', 'ц': 'ts',
    'Ч': 'Ch', 'ч': 'ch', 'Ш': 'Sh', 'ш': 'sh', 'Щ': 'Sh', 'щ': 'sh',
    'Ъ': "'", 'ъ': "'", 'Ь': '', 'ь': '', 'Э': 'E', 'э': 'e',
    'Ю': 'Yu', 'ю': 'yu', 'Я': 'Ya', 'я': 'ya',
    'Ў': "O'", 'ў': "o'", 'Қ': 'Q', 'қ': 'q', 'Ғ': "G'", 'ғ': "g'",
    'Ҳ': 'H', 'ҳ': 'h'
}


def transliterate_cyrillic(text: str) -> str:
    if not text:
        return ""
    return "".join(CYRILLIC_TO_LATIN.get(ch, ch) for ch in text)


def _clean_pdf_text(text: Any) -> str:
    if text is None:
        return ""
    s = str(text).strip()
    s = re.sub(r'[\U00010000-\U0010ffff]', '', s)
    s = re.sub(r'[\u200B-\u200D\uFEFF]', '', s)
    s = s.replace("—", "-").replace("–", "-").replace("−", "-")
    for ch in ["ʻ", "ʼ", "'", "'", "′", "`", "´", "ʹ", "ʽ"]:
        s = s.replace(ch, "'")
    for q in [""", """, "„", "«", "»"]:
        s = s.replace(q, '"')
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return s.strip()


def generate_test_results_pdf(test_id: int) -> Optional[str]:
    """Test natijalari bo'yicha rasmiy PDF reyting jadvali generatsiya qiladi."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib import colors
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        test = get_test_by_id(test_id)
        if not test:
            return None

        results = get_test_results_leaderboard(test_id)

        pdf_filename = f"test_result_{test_id}_{int(time.time())}.pdf"
        pdf_path = os.path.join(os.path.dirname(__file__), pdf_filename)

        doc = SimpleDocTemplate(
            pdf_path,
            pagesize=A4,
            leftMargin=30, rightMargin=30,
            topMargin=30, bottomMargin=30
        )

        font_name = 'Helvetica'
        font_bold = 'Helvetica-Bold'

        font_dir = os.path.join(os.path.dirname(__file__), 'fonts')
        possible_regular = [
            os.path.join(os.path.dirname(__file__), 'Arial.ttf'),
            os.path.join(font_dir, 'Arial.ttf'),
            '/System/Library/Fonts/Supplemental/Arial.ttf',
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
            '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
            '/Library/Fonts/Arial.ttf'
        ]
        possible_bold = [
            os.path.join(os.path.dirname(__file__), 'Arial-Bold.ttf'),
            os.path.join(font_dir, 'Arial-Bold.ttf'),
            '/System/Library/Fonts/Supplemental/Arial Bold.ttf',
            '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
            '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
            '/Library/Fonts/Arial Bold.ttf'
        ]

        reg_path = next((p for p in possible_regular if os.path.exists(p)), None)
        bld_path = next((p for p in possible_bold if os.path.exists(p)), None)

        if reg_path:
            try:
                pdfmetrics.registerFont(TTFont('UnicodeSans', reg_path))
                font_name = 'UnicodeSans'
            except Exception as fe:
                print(f"Font regular error: {fe}")

        if bld_path:
            try:
                pdfmetrics.registerFont(TTFont('UnicodeSansBold', bld_path))
                font_bold = 'UnicodeSansBold'
            except Exception as fe:
                print(f"Font bold error: {fe}")
        elif font_name == 'UnicodeSans':
            font_bold = 'UnicodeSans'

        styles = getSampleStyleSheet()

        title_style = ParagraphStyle('TitleStyle', parent=styles['Normal'],
                                     fontName=font_bold, fontSize=14, leading=18,
                                     textColor=colors.HexColor("#1E3A8A"), alignment=1)
        subtitle_style = ParagraphStyle('SubTitleStyle', parent=styles['Normal'],
                                        fontName=font_name, fontSize=9.5, leading=13,
                                        textColor=colors.HexColor("#475569"), alignment=1)
        header_cell_style = ParagraphStyle('HeaderCellStyle', parent=styles['Normal'],
                                           fontName=font_bold, fontSize=8.5, leading=11,
                                           textColor=colors.HexColor("#1E3A8A"), alignment=1)
        cell_style = ParagraphStyle('CellStyle', parent=styles['Normal'],
                                    fontName=font_name, fontSize=8.5, leading=11, alignment=1)
        cell_name_style = ParagraphStyle('CellNameStyle', parent=styles['Normal'],
                                         fontName=font_name, fontSize=8.5, leading=11, alignment=0)
        cell_bold = ParagraphStyle('CellBold', parent=styles['Normal'],
                                   fontName=font_bold, fontSize=8.5, leading=11, alignment=1)

        elements = []
        subj_header = str(test.get('subject') or 'ONA TILI VA ADABIYOT').upper()
        elements.append(Paragraph(f"{subj_header} — RASCH BAHOLASH TIZIMI", title_style))
        elements.append(Spacer(1, 4))

        test_title_clean = _clean_pdf_text(test['title'])
        test_subject_clean = _clean_pdf_text(test.get('subject', 'Ona tili'))
        if font_name == 'Helvetica':
            test_title_clean = transliterate_cyrillic(test_title_clean)
            test_subject_clean = transliterate_cyrillic(test_subject_clean)

        elements.append(Paragraph(f"Test: <b>{test_title_clean}</b> (Kod: <b>#{test['test_code']}</b>) | Fan: {test_subject_clean}", subtitle_style))
        elements.append(Paragraph(f"Jami ishtirokchilar: <b>{len(results)} nafar</b> | Rasmiy hisobot sanasi: {format_uzb_time()}", subtitle_style))
        elements.append(Spacer(1, 10))

        # Rasch Modeli & Statistika Qisqacha Kartasi
        if results:
            avg_sc = round(sum(r['score'] for r in results) / len(results), 1)
            max_sc = max(r['score'] for r in results)
            min_sc = min(r['score'] for r in results)
            
            stats_data = [
                [
                    Paragraph(f"<b>Ishtirokchilar:</b> {len(results)} nafar", cell_style),
                    Paragraph(f"<b>O'rtacha Ball:</b> {avg_sc} ball", cell_style),
                    Paragraph(f"<b>Eng Yuqori:</b> {max_sc} ball", cell_style),
                    Paragraph(f"<b>Eng Quyi:</b> {min_sc} ball", cell_style),
                ]
            ]
            stats_table = Table(stats_data, colWidths=[130, 130, 135, 140])
            stats_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#F1F5F9")),
                ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                ('TOPPADDING', (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ]))
            elements.append(stats_table)
            elements.append(Spacer(1, 10))

        table_data = [[
            Paragraph("<b>O'rin</b>", header_cell_style),
            Paragraph("<b>Ism va Familiya</b>", header_cell_style),
            Paragraph("<b>To'plangan Ball</b>", header_cell_style),
            Paragraph("<b>Daraja</b>", header_cell_style),
            Paragraph("<b>To'g'ri</b>", header_cell_style),
            Paragraph("<b>Topshirilgan Vaqt</b>", header_cell_style)
        ]]

        for rank, r in enumerate(results, 1):
            dt = format_uzb_time(r["submitted_at"], "%d.%m %H:%M")
            grade = calculate_grade(r["score"])
            fullname_clean = _clean_pdf_text(r["fullname"] or "Foydalanuvchi")
            if font_name == 'Helvetica':
                fullname_clean = transliterate_cyrillic(fullname_clean)
            grade_clean = _clean_pdf_text(grade)

            table_data.append([
                Paragraph(f"<b>#{rank}</b>", cell_bold),
                Paragraph(f"&nbsp;{fullname_clean}", cell_name_style),
                Paragraph(f"<b>{r['score']} ball</b>", cell_bold),
                Paragraph(f"<b>{grade_clean}</b>", cell_style),
                Paragraph(f"{r['correct_count']} ta", cell_style),
                Paragraph(dt, cell_style)
            ])

        col_widths = [45, 215, 80, 70, 55, 70]
        t = Table(table_data, colWidths=col_widths, repeatRows=1)
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#EEF2FF")),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor("#1E3A8A")),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))

        elements.append(t)
        doc.build(elements)
        return pdf_path
    except Exception as e:
        import traceback
        print(f"PDF Generation Error: {e}")
        traceback.print_exc()
        return None



def update_test_keys(test_id: int, new_answers: Dict[str, Any],
                     title: Optional[str] = None,
                     time_limit_min: Optional[int] = None,
                     key_access_code: Optional[str] = None,
                     scheduled_date: Optional[str] = None,
                     scheduled_start: Optional[str] = None,
                     scheduled_end: Optional[str] = None,
                     youtube_url: Optional[str] = None) -> Dict[str, Any]:
    """
    Test kalitlarini yangilaydi va shu testga topshirilgan barcha o'quvchilar javoblarini
    yangi kalitlar bo'yicha qayta tekshirib, Rasch modelini va PDF reytingni avtomatik yangilaydi.
    """
    test = get_test_by_id(test_id)
    if not test:
        raise ValueError(f"ID #{test_id} ga ega test topilmadi!")

    answers_json = json.dumps(new_answers, ensure_ascii=False)

    conn = get_connection()
    try:
        cur = conn.cursor()

        # 1. Testning yangilangan ma'lumotlarini saqlash
        update_fields = [f"answers_json = {_ph()}"]
        params: List[Any] = [answers_json]

        if title is not None and str(title).strip():
            update_fields.append(f"title = {_ph()}")
            params.append(str(title).strip())
        if time_limit_min is not None:
            update_fields.append(f"time_limit_min = {_ph()}")
            params.append(int(time_limit_min))
        if key_access_code is not None:
            update_fields.append(f"key_access_code = {_ph()}")
            params.append(str(key_access_code).strip())
        if scheduled_date is not None:
            update_fields.append(f"scheduled_date = {_ph()}")
            params.append(str(scheduled_date).strip())
        if scheduled_start is not None:
            update_fields.append(f"scheduled_start = {_ph()}")
            params.append(str(scheduled_start).strip())
        if scheduled_end is not None:
            update_fields.append(f"scheduled_end = {_ph()}")
            params.append(str(scheduled_end).strip())
        if youtube_url is not None:
            update_fields.append(f"youtube_url = {_ph()}")
            params.append(str(youtube_url).strip())

        params.append(test_id)
        sql = f"UPDATE tests SET {', '.join(update_fields)} WHERE id = {_ph()}"
        cur.execute(sql, tuple(params))

        # 2. Barcha topshirilgan javoblarni (submissions) yangi kalitlar bo'yicha qayta tekshirish
        cur.execute(f"SELECT id, answers_json, user_tg_id FROM submissions WHERE test_id = {_ph()}", (test_id,))
        subs = [_row_to_dict(r) for r in cur.fetchall()]

        updated_count = 0
        correct_answers_raw = new_answers

        for sub in subs:
            sub_id = sub["id"]
            ans_raw = sub.get("answers_json")
            if not ans_raw:
                continue
            try:
                user_answers = json.loads(ans_raw)
            except Exception:
                user_answers = {}

            correct_count = 0
            incorrect_count = 0
            unanswered_count = 0
            details = {}
            earned_score = 0.0
            total_possible_score = 0.0

            # 1-35 savollar (Variantli yopiq savollar, default 2.0 ball)
            for q in range(1, 36):
                key = str(q)
                q_raw = correct_answers_raw.get(key, "A")
                correct_ans, q_score = get_key_and_score(q_raw, default_score=2.0)
                total_possible_score += q_score
                user_val = user_answers.get(key, "")
                is_corr = False
                if not user_val or not str(user_val).strip():
                    status = "unanswered"
                    unanswered_count += 1
                elif is_answer_matching(user_val, correct_ans):
                    is_corr = True
                    status = "correct"
                    correct_count += 1
                    earned_score += q_score
                else:
                    status = "incorrect"
                    incorrect_count += 1
                details[key] = {
                    "num": f"{q}-savol", "type": "choice_4",
                    "user": user_val, "correct": correct_ans,
                    "status": status,
                    "score": q_score if is_corr else 0.0,
                    "max_score": q_score
                }

            # 36, 37, 38, 39 savollar (1 ta variantli yopiq/ochiq savollar)
            for q in range(36, 40):
                key = str(q)
                q_raw = correct_answers_raw.get(key, "")
                if not q_raw and f"{key}a" in correct_answers_raw:
                    q_raw = correct_answers_raw.get(f"{key}a", "")
                correct_ans, q_score = get_key_and_score(q_raw, default_score=1.5)
                total_possible_score += q_score
                user_val = user_answers.get(key, "")
                if not user_val and f"{key}a" in user_answers:
                    user_val = user_answers.get(f"{key}a", "")
                is_matched, ratio, match_status = check_answer_match(user_val, correct_ans)
                if not user_val or not str(user_val).strip():
                    status = "unanswered"
                    unanswered_count += 1
                    item_score = 0.0
                elif is_matched:
                    status = "correct"
                    correct_count += 1
                    item_score = q_score
                    earned_score += q_score
                else:
                    status = "incorrect"
                    incorrect_count += 1
                    item_score = 0.0

                details[key] = {
                    "num": f"{key}-savol", "type": "open",
                    "user": user_val, "correct": correct_ans,
                    "status": status,
                    "score": item_score,
                    "max_score": q_score,
                    "ratio": ratio
                }

            # 40 dan boshlab 2 qismli (a va b) yopiq savollar
            end_q = 45 if any(f"45{s}" in correct_answers_raw or "45" in correct_answers_raw for s in ["a", "b"]) else 44
            for q in range(40, end_q + 1):
                for sub_part in ["a", "b"]:
                    key = f"{q}{sub_part}"
                    q_raw = correct_answers_raw.get(key, "")
                    correct_ans, q_score = get_key_and_score(q_raw, default_score=1.5)
                    total_possible_score += q_score
                    user_val = user_answers.get(key, "")
                    is_matched, ratio, match_status = check_answer_match(user_val, correct_ans)
                    if not user_val or not str(user_val).strip():
                        status = "unanswered"
                        unanswered_count += 1
                        item_score = 0.0
                    elif is_matched:
                        status = "correct"
                        correct_count += 1
                        item_score = q_score
                        earned_score += q_score
                    else:
                        status = "incorrect"
                        incorrect_count += 1
                        item_score = 0.0

                    details[key] = {
                        "num": f"{key}-savol", "type": "open",
                        "user": user_val, "correct": correct_ans,
                        "status": status,
                        "score": item_score,
                        "max_score": q_score,
                        "ratio": ratio
                    }

            earned_score = round(earned_score, 1)
            cur.execute(f"""
                UPDATE submissions
                SET score = {_ph()}, correct_count = {_ph()}, details_json = {_ph()}
                WHERE id = {_ph()}
            """, (earned_score, correct_count, json.dumps(details, ensure_ascii=False), sub_id))
            updated_count += 1

        conn.commit()
        invalidate_test_cache(test_id)
    finally:
        _close_conn(conn)

    # 3. Rasch modelini yangilangan kalitlar va javoblar bo'yicha qayta hisoblash
    rasch_res = None
    try:
        rasch_res = evaluate_test_rasch(test_id, auto_update_db=True)
    except Exception as e:
        print(f"Rasch re-eval error: {e}")

    # 4. Yangilangan natijalar bo'yicha PDF reytingni qayta generatsiya qilish
    try:
        generate_test_results_pdf(test_id)
    except Exception as e:
        print(f"PDF regeneration error: {e}")

    return {
        "success": True,
        "test_id": test_id,
        "recalculated_count": updated_count,
        "rasch_updated": bool(rasch_res)
    }


# ──────────────────────────────────────────────────────────
# MACBOOK DASHBOARD / ADMIN APIS
# ──────────────────────────────────────────────────────────

def _fetch_scalar(row, key: str = "cnt") -> Any:
    if row is None:
        return 0
    if isinstance(row, dict):
        return row.get(key, list(row.values())[0]) if row else 0
    return row[0]


def get_dashboard_summary() -> Dict[str, Any]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        now_ts = int(time.time())
        today_midnight = now_ts - (now_ts % 86400) - (5 * 3600)
        if today_midnight > now_ts:
            today_midnight -= 86400

        cur.execute(f"""
        SELECT 
            (SELECT COUNT(*) FROM users) as total_users,
            (SELECT COUNT(*) FROM users WHERE status = 'approved') as approved_users,
            (SELECT COUNT(*) FROM users WHERE status = 'pending') as pending_users,
            0 as blocked_users,
            (SELECT COUNT(*) FROM tests) as total_tests,
            (SELECT COUNT(*) FROM tests WHERE is_active = 1) as active_tests,
            (SELECT COUNT(*) FROM submissions) as total_submissions,
            (SELECT COUNT(*) FROM submissions WHERE is_late = 1) as late_submissions,
            (SELECT COUNT(*) FROM submissions WHERE submitted_at >= {_ph()}) as today_submissions,
            (SELECT COALESCE(AVG(score), 0) FROM submissions WHERE score IS NOT NULL) as avg_score,
            (SELECT COALESCE(AVG(correct_count), 0) FROM submissions WHERE correct_count IS NOT NULL) as avg_correct
        """, (today_midnight,))
        row = cur.fetchone()
        d = _row_to_dict(row) or {}

        return {
            "total_users": int(d.get("total_users") or 0),
            "approved_users": int(d.get("approved_users") or 0),
            "pending_users": int(d.get("pending_users") or 0),
            "blocked_users": int(d.get("blocked_users") or 0),
            "total_tests": int(d.get("total_tests") or 0),
            "active_tests": int(d.get("active_tests") or 0),
            "total_submissions": int(d.get("total_submissions") or 0),
            "today_submissions": int(d.get("today_submissions") or 0),
            "late_submissions": int(d.get("late_submissions") or 0),
            "avg_score": round(float(d.get("avg_score") or 0.0), 1),
            "avg_correct": round(float(d.get("avg_correct") or 0.0), 1),
            "db_type": "PostgreSQL (Neon Cloud)" if USE_POSTGRES else "SQLite (Local)"
        }
    finally:
        _close_conn(conn)


def get_all_submissions_for_admin(limit: int = 1000) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"""
        SELECT s.id, s.test_id, s.test_code, s.user_tg_id, s.fullname, s.phone,
               s.score, s.max_score, s.correct_count, s.total_count,
               s.submitted_at, s.is_late, s.status, s.reject_reason,
               u.username, u.status as user_status,
               t.title as test_title, t.subject as test_subject,
               t.results_published, t.is_active as test_is_active
        FROM submissions s
        LEFT JOIN users u ON s.user_tg_id = u.tg_id
        LEFT JOIN tests t ON s.test_id = t.id
        ORDER BY s.submitted_at DESC
        LIMIT {int(limit)}
        """)
        rows = cur.fetchall()
        results = []
        for r in rows:
            d = _row_to_dict(r)
            if not d:
                continue
            d["grade"] = calculate_grade(d.get("score", 0), correct_count=d.get("correct_count", 0))
            results.append(d)
        return results
    finally:
        _close_conn(conn)


def get_user_submissions_for_admin(user_tg_id: int) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"""
        SELECT s.id, s.test_id, s.test_code, s.user_tg_id, s.fullname, s.phone,
               s.score, s.max_score, s.correct_count, s.total_count,
               s.submitted_at, s.is_late, s.status, s.reject_reason,
               u.username, u.status as user_status,
               t.title as test_title, t.subject as test_subject,
               t.results_published, t.is_active as test_is_active
        FROM submissions s
        LEFT JOIN users u ON s.user_tg_id = u.tg_id
        LEFT JOIN tests t ON s.test_id = t.id
        WHERE s.user_tg_id = {_ph()}
        ORDER BY s.submitted_at DESC
        """, (user_tg_id,))
        rows = cur.fetchall()
        results = []
        for r in rows:
            d = _row_to_dict(r)
            if not d:
                continue
            d["grade"] = calculate_grade(d.get("score", 0), correct_count=d.get("correct_count", 0))
            results.append(d)
        return results
    finally:
        _close_conn(conn)


get_user_results = get_user_submissions_for_admin


def get_submission_details_for_admin(sub_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"""
        SELECT s.*, u.username, u.status as user_status,
               t.title as test_title, t.subject as test_subject, t.answers_json as test_keys_json
        FROM submissions s
        LEFT JOIN users u ON s.user_tg_id = u.tg_id
        LEFT JOIN tests t ON s.test_id = t.id
        WHERE s.id = {_ph()}
        """, (sub_id,))
        row = cur.fetchone()
        if not row:
            return None
        d = _row_to_dict(row)
        d["grade"] = calculate_grade(d.get("score", 0), correct_count=d.get("correct_count", 0))
        return d
    finally:
        _close_conn(conn)


def get_activity_logs(limit: int = 60) -> List[Dict[str, Any]]:
    """Tizimdagi so'nggi jarayonlar va voqealar xronologiyasi."""
    events = []
    conn = get_connection()
    try:
        cur = conn.cursor()
        # 1. So'nggi topshirilgan testlar
        cur.execute(f"""
        SELECT s.id, s.user_tg_id, s.fullname, s.submitted_at, s.score, s.correct_count, s.is_late,
               s.test_code, t.title as test_title, u.username
        FROM submissions s
        LEFT JOIN tests t ON s.test_id = t.id
        LEFT JOIN users u ON s.user_tg_id = u.tg_id
        ORDER BY s.submitted_at DESC
        LIMIT {int(limit)}
        """)
        for r in cur.fetchall():
            d = _row_to_dict(r)
            if d:
                events.append({
                    "type": "late_submission" if d.get("is_late") else "submission",
                    "time": d.get("submitted_at"),
                    "title": f"Test topshirildi: {d.get('test_title') or '#' + str(d.get('test_code'))}",
                    "user_name": d.get("fullname"),
                    "username": d.get("username"),
                    "user_id": d.get("user_tg_id"),
                    "badge": f"{d.get('correct_count', 0)} to'g'ri • {d.get('score', 0)} ball" + (" (Kech)" if d.get("is_late") else ""),
                    "badge_color": "warning" if d.get("is_late") else "success",
                    "data_id": d.get("id")
                })
        
        # 2. So'nggi ro'yxatdan o'tgan foydalanuvchilar
        cur.execute(f"""
        SELECT id, tg_id, fullname, username, phone, status, registered_at
        FROM users
        ORDER BY registered_at DESC
        LIMIT {int(limit // 2)}
        """)
        for r in cur.fetchall():
            d = _row_to_dict(r)
            if d:
                events.append({
                    "type": "registration",
                    "time": d.get("registered_at"),
                    "title": "Yangi foydalanuvchi qo'shildi",
                    "user_name": d.get("fullname"),
                    "username": d.get("username"),
                    "user_id": d.get("tg_id"),
                    "badge": d.get("status", "approved").capitalize(),
                    "badge_color": "danger" if d.get("status") == "blocked" else ("warning" if d.get("status") == "pending" else "info"),
                    "data_id": d.get("id")
                })

        # Vaqt bo'yicha kamayish tartibida saralash
        events.sort(key=lambda x: x.get("time") or 0, reverse=True)
        return events[:limit]
    finally:
        _close_conn(conn)


def execute_admin_safe_query(query: str, limit: int = 100) -> Dict[str, Any]:
    """Admin uchun xavfsiz SELECT so'rovlarini bajarish."""
    q = (query or "").strip()
    if not q.lower().startswith("select"):
        return {"success": False, "error": "Faqat SELECT so'rovlarini bajarish mumkin!"}
    
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(q)
        rows = cur.fetchmany(limit)
        results = [_row_to_dict(r) for r in rows if r]
        return {
            "success": True,
            "count": len(results),
            "rows": results
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        _close_conn(conn)


def recalculate_all_submissions_globally() -> int:
    """Barcha testlarning topshirilgan javoblarini yangi qoidalar (masalan, uncomputed +/- ga 30% ball) bo'yicha qayta hisoblash."""
    conn = get_connection()
    total_recalculated = 0
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, answers_json FROM tests")
        tests = [_row_to_dict(r) for r in cur.fetchall()]
        for t in tests:
            test_id = t["id"]
            raw_answers = t.get("answers_json")
            if not raw_answers:
                continue
            try:
                answers_dict = json.loads(raw_answers)
            except Exception:
                continue
            cur.execute(f"SELECT id, answers_json FROM submissions WHERE test_id = {_ph()}", (test_id,))
            subs = [_row_to_dict(r) for r in cur.fetchall()]
            for sub in subs:
                sub_id = sub["id"]
                sub_ans_raw = sub.get("answers_json")
                if not sub_ans_raw:
                    continue
                try:
                    user_answers = json.loads(sub_ans_raw)
                except Exception:
                    continue

                correct_count = 0
                incorrect_count = 0
                unanswered_count = 0
                details = {}
                earned_score = 0.0

                for q in range(1, 36):
                    key = str(q)
                    correct_ans, q_score = get_key_and_score(answers_dict.get(key, "A"), default_score=2.0)
                    user_val = user_answers.get(key, "")
                    is_matched, ratio, _ = check_answer_match(user_val, correct_ans)
                    if not user_val or not str(user_val).strip():
                        status = "unanswered"
                        unanswered_count += 1
                        score = 0.0
                    elif is_matched and ratio >= 1.0:
                        status = "correct"
                        correct_count += 1
                        score = q_score
                        earned_score += q_score
                    else:
                        status = "incorrect"
                        incorrect_count += 1
                        score = 0.0
                    details[key] = {
                        "num": f"{q}-savol", "type": "choice_4",
                        "user": user_val, "correct": correct_ans,
                        "status": status, "score": score, "max_score": q_score
                    }

                # 36, 37, 38, 39 savollar (1 ta variantli yopiq/ochiq savollar)
                for q in range(36, 40):
                    key = str(q)
                    q_raw = answers_dict.get(key, "")
                    if not q_raw and f"{key}a" in answers_dict:
                        q_raw = answers_dict.get(f"{key}a", "")
                    correct_ans, q_score = get_key_and_score(q_raw, default_score=1.5)
                    user_val = user_answers.get(key, "")
                    if not user_val and f"{key}a" in user_answers:
                        user_val = user_answers.get(f"{key}a", "")
                    is_matched, ratio, _ = check_answer_match(user_val, correct_ans)
                    if not user_val or not str(user_val).strip():
                        status = "unanswered"
                        unanswered_count += 1
                        score = 0.0
                    elif is_matched:
                        status = "correct"
                        correct_count += 1
                        score = q_score
                        earned_score += q_score
                    else:
                        status = "incorrect"
                        incorrect_count += 1
                        score = 0.0
                    details[key] = {
                        "num": f"{key}-savol", "type": "open",
                        "user": user_val, "correct": correct_ans,
                        "status": status, "score": score, "max_score": q_score, "ratio": ratio
                    }

                # 40 dan boshlab 2 qismli (a va b) yopiq savollar
                end_q = 45 if any(f"45{s}" in answers_dict or "45" in answers_dict for s in ["a", "b"]) else 44
                for q in range(40, end_q + 1):
                    for sub_part in ["a", "b"]:
                        key = f"{q}{sub_part}"
                        q_raw = answers_dict.get(key, "")
                        correct_ans, q_score = get_key_and_score(q_raw, default_score=1.5)
                        user_val = user_answers.get(key, "")
                        is_matched, ratio, _ = check_answer_match(user_val, correct_ans)
                        if not user_val or not str(user_val).strip():
                            status = "unanswered"
                            unanswered_count += 1
                            score = 0.0
                        elif is_matched:
                            status = "correct"
                            correct_count += 1
                            score = q_score
                            earned_score += q_score
                        else:
                            status = "incorrect"
                            incorrect_count += 1
                            score = 0.0
                        details[key] = {
                            "num": f"{key}-savol", "type": "open",
                            "user": user_val, "correct": correct_ans,
                            "status": status, "score": score, "max_score": q_score, "ratio": ratio
                        }

                earned_score = round(earned_score, 1)
                cur.execute(f"""
                    UPDATE submissions
                    SET score = {_ph()}, correct_count = {_ph()}, details_json = {_ph()}
                    WHERE id = {_ph()}
                """, (earned_score, correct_count, json.dumps(details, ensure_ascii=False), sub_id))
                total_recalculated += 1
        conn.commit()
        return total_recalculated
    except Exception as e:
        print(f"Global recalculate submissions xatolik: {e}")
        return total_recalculated
    finally:
        _close_conn(conn)


# Baza inicializatsiyasi
try:
    ensure_db_initialized()
except Exception:
    pass

if __name__ == '__main__':
    init_db()


