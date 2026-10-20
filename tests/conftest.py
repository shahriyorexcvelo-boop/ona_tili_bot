import os
import sys
import tempfile
import pytest
from pathlib import Path

# Loyiha ildiz papkasini import yo'liga qo'shish
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
os.environ["DATABASE_URL"] = ""
import test_db

@pytest.fixture(autouse=True)
def isolated_sqlite_db(monkeypatch, tmp_path):
    """
    Har bir test uchun alohida, toza va izolyatsiyalangan SQLite bazasini tayyorlash.
    Haqiqiy production bazasiga mutlaqo tegilmaydi.
    """
    db_file = str(tmp_path / "test_isolated.db")
    
    monkeypatch.setattr(test_db, "USE_POSTGRES", False)
    monkeypatch.setattr(test_db, "DATABASE_URL", "")
    monkeypatch.setattr(test_db, "DB_FILE", db_file)
    monkeypatch.setattr(test_db, "_pg_pool", None)
    
    test_db.init_db()
    test_db.invalidate_test_cache()
    
    yield db_file
    
    test_db.invalidate_test_cache()
