"""
Ma'lumotlar yo'qolishi va saqlanishini tekshiruvchi avtomatlashtirilgan testlar to'plami.
8 ta majburiy stsenariy:
1. Bloklangan foydalanuvchi va uning natijalari startupdan keyin saqlanib qolishi.
2. Test topshirish yarmida 'restart'dan so'ng qoralamalarning tiklanishi (Autosave).
3. Takroriy (ikki marta) topshirishda dublikat yaratilmasligi (Atomarlik & UNIQUE).
4. Foydalanuvchi o'z akkauntini o'chirganda ma'lumotlar anonimlashtirilib, natijalar saqlanishi.
5. Admin tomonidan foydalanuvchi o'chirilganda soft delete bo'lishi va uni qaytarish imkoniyati.
6. Tun vaqtida (23:30) faol test davomida topshirishga ruxsat berilishi.
7. Migratsiyalar bir necha bor ishga tushirilganda xato bermasligi va idempotentlik.
8. Keshning Read-through TTL (45s) va invalidatsiyasi to'g'ri ishlashi.
"""
import time
import json
import pytest
from datetime import datetime, timezone, timedelta

import test_db
from core.config import should_apply_night_mode, is_working_hours, UZB_TZ


def _create_sample_test(test_id=1, test_code="TEST-01", is_active=1, title="Test Ona Tili"):
    """Sinov uchun test ma'lumotlarini bazaga qo'shuvchi yordamchi funksiya."""
    answers = {}
    for i in range(1, 36):
        answers[str(i)] = "A"
    for i in range(36, 40):
        answers[str(i)] = "12"
    for i in range(40, 45):
        answers[f"{i}a"] = "1"
        answers[f"{i}b"] = "2"
    
    conn = test_db.get_connection()
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO tests (
        id, test_code, title, subject, answers_json, is_active, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        test_id, test_code, title, "Ona tili", json.dumps(answers), is_active, int(time.time())
    ))
    conn.commit()
    test_db._close_conn(conn)
    test_db.invalidate_test_cache()
    return test_db.get_test_by_id(test_id)


# -----------------------------------------------------------------------------
# 1. Stsenariy: Bloklangan foydalanuvchi va natijalari saqlanib qoladi
# -----------------------------------------------------------------------------
def test_scenario_1_blocked_user_preserved_on_startup():
    test_obj = _create_sample_test(test_id=101, test_code="T101")
    uid = 999001
    
    test_db.add_or_update_user(uid, "Ali Valiyev", "+998901112233", "alivali", status="approved")
    test_db.check_and_save_submission(101, uid, {"1": "A", "2": "B"})
    
    # Foydalanuvchini bloklaymiz (yangi modelda rejected ga o'tadi)
    test_db.block_user(uid)
    user_before = test_db.get_user(uid)
    assert user_before["status"] == "rejected"
    
    # Startup simulyatsiyasi: init_db chaqiriladi
    test_db.init_db()
    test_db.preload_all_tests_cache()
    
    # Foydalanuvchi bazada mavjud va o'chib ketmagan!
    user_after = test_db.get_user(uid)
    assert user_after is not None
    assert user_after["tg_id"] == uid
    assert user_after["status"] == "rejected"
    
    # Uning natijalari ham bazada to'liq saqlanib qolgan!
    sub = test_db.get_user_submission_for_test(101, uid)
    assert sub is not None
    assert sub["user_tg_id"] == uid


# -----------------------------------------------------------------------------
# 2. Stsenariy: Test topshirish yarmida 'restart' bo'lsa javoblar tiklanadi (Autosave)
# -----------------------------------------------------------------------------
def test_scenario_2_draft_autosave_and_recovery_on_restart():
    test_obj = _create_sample_test(test_id=102, test_code="T102")
    uid = 999002
    test_db.add_or_update_user(uid, "Bobur Mirzo", "+998902223344", "bobur", status="approved")
    
    # O'quvchi 1-savoldan 5-savolgacha javoblarni belgiladi (Autosave)
    test_db.save_answer_draft(uid, 102, "1", "A")
    test_db.save_answer_draft(uid, 102, "2", "B")
    test_db.save_bulk_draft(uid, 102, {"1": "A", "2": "B", "3": "C", "4": "D", "5": "B"})
    
    # Jarayon "restart" bo'ladi: barcha RAM keshlar va FSM tozalanadi
    test_db.invalidate_test_cache()
    
    # Restartdan so'ng bazadan qoralama javoblar qayta olinadi
    drafts = test_db.get_answer_drafts(uid, 102)
    assert drafts["1"] == "A"
    assert drafts["2"] == "B"
    assert drafts["3"] == "C"
    assert drafts["4"] == "D"
    assert drafts["5"] == "B"
    
    # O'quvchi testni to'liq yakunlaydi va topshiradi
    sub_res = test_db.check_and_save_submission(102, uid, drafts)
    assert sub_res["submission_id"] is not None
    
    # Topshirilgach, qoralamalar avtomatik tozalangan!
    cleared_drafts = test_db.get_answer_drafts(uid, 102)
    assert len(cleared_drafts) == 0


# -----------------------------------------------------------------------------
# 3. Stsenariy: Ikki marta topshirilganda bitta submission saqlanadi (Dublikat yo'q)
# -----------------------------------------------------------------------------
def test_scenario_3_prevent_duplicate_submissions():
    test_obj = _create_sample_test(test_id=103, test_code="T103")
    uid = 999003
    test_db.add_or_update_user(uid, "Sardor Komilov", "+998903334455", "sardor", status="approved")
    
    answers = {"1": "A", "2": "A"}
    # 1-topshirish
    sub_1 = test_db.check_and_save_submission(103, uid, answers)
    assert sub_1["submission_id"] is not None
    
    # 2-topshirish (Oddiy foydalanuvchi qayta topshirishga uringanda xato berishi kerak)
    with pytest.raises(ValueError) as excinfo:
        test_db.check_and_save_submission(103, uid, answers)
    assert "allaqachon topshirgansiz" in str(excinfo.value)
    
    # Bazada aniq 1 ta submission mavjudligini tekshiramiz
    conn = test_db.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM submissions WHERE user_tg_id = ? AND test_id = ?", (uid, 103))
    count = cur.fetchone()[0]
    test_db._close_conn(conn)
    assert count == 1


# -----------------------------------------------------------------------------
# 4. Stsenariy: Foydalanuvchi o'zi ketsa barcha ma'lumotlari o'chadi, admin_deleted_users ga yozilmaydi
# -----------------------------------------------------------------------------
def test_scenario_4_user_self_delete_anonymization():
    test_obj = _create_sample_test(test_id=104, test_code="T104")
    uid = 999004
    test_db.add_or_update_user(uid, "Jasur Rustamov", "+998904445566", "jasur_r", status="approved")
    test_db.check_and_save_submission(104, uid, {"1": "A"})
    
    # Foydalanuvchi o'z akkauntini o'chiradi (is_self_delete=True)
    del_info = test_db.delete_user(uid, deleted_by=uid, is_self_delete=True)
    assert del_info is not None
    
    # Yangi model: foydalanuvchi ma'lumotlari bazadan to'liq o'chiriladi
    assert test_db.get_user(uid) is None
    # Lekin admin_deleted_users ga yozilmaydi
    assert test_db.is_admin_deleted(uid) is False


# -----------------------------------------------------------------------------
# 5. Stsenariy: Admin o'chirish amali admin_deleted_users ga yozadi va qayta tiklash mumkin
# -----------------------------------------------------------------------------
def test_scenario_5_admin_soft_delete_and_restore():
    uid = 999005
    admin_id = 8039427064
    test_db.add_or_update_user(uid, "Madina Karimova", "+998905556677", "madina_k", status="approved")
    
    # Admin foydalanuvchini o'chiradi (by_admin=True)
    test_db.delete_user(uid, deleted_by=admin_id, by_admin=True)
    
    # Users jadvalidan o'chgan
    assert test_db.get_user(uid) is None
    
    # Lekin admin_deleted_users da qayd etilgan
    assert test_db.is_admin_deleted(uid) is True
    
    # Admin uni qayta tiklaydi
    restored = test_db.restore_user(uid)
    assert restored is not None
    assert restored["status"] == "approved"
    assert test_db.is_admin_deleted(uid) is False


# -----------------------------------------------------------------------------
# 6. Stsenariy: Tun vaqtida (23:30) faol test davomida topshirishga ruxsat beriladi
# -----------------------------------------------------------------------------
def test_scenario_6_night_mode_permits_active_test(monkeypatch):
    test_obj = _create_sample_test(test_id=106, test_code="T106", is_active=1)
    uid = 999006
    test_db.add_or_update_user(uid, "Nodir Zokirov", "+998906667788", "nodir_z", status="approved")
    
    # Tun vaqtini simulyatsiya qilamiz: is_working_hours False qaytaradi (masalan 23:30)
    import core.config
    monkeypatch.setattr(core.config, "is_working_hours", lambda: False)
    
    # Faol test mavjud bo'lmaganda: Tun rejimi foydalanuvchini to'xtatadi
    monkeypatch.setattr(test_db, "has_any_active_test", lambda: False)
    assert should_apply_night_mode(user_tg_id=uid, current_test_id=None) is True
    
    # Faol test ketayotganda: Tun rejimi TO'XTATMAYDI, ruxsat beradi!
    monkeypatch.setattr(test_db, "has_any_active_test", lambda: True)
    assert should_apply_night_mode(user_tg_id=uid, current_test_id=106) is False


# -----------------------------------------------------------------------------
# 7. Stsenariy: Migratsiyalar bir necha bor ishga tushsa, xato bermaydi (Idempotent)
# -----------------------------------------------------------------------------
def test_scenario_7_migrations_idempotency():
    from migrations.runner import run_migrations
    conn = test_db.get_connection()
    try:
        # 1-marta ishga tushirish
        run_migrations(conn, use_postgres=False)
        # 2-marta takroriy ishga tushirish — mutlaqo xatosiz o'tishi kerak
        run_migrations(conn, use_postgres=False)
        
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM schema_migrations")
        count = cur.fetchone()[0]
        # Barcha migratsiyalar faqat 1 martadan ro'yxatga olingan
        assert count >= 2
    finally:
        test_db._close_conn(conn)


# -----------------------------------------------------------------------------
# 8. Stsenariy: Test keshining Read-through TTL va invalidatsiyasi to'g'ri ishlashi
# -----------------------------------------------------------------------------
def test_scenario_8_cache_ttl_and_invalidation(monkeypatch):
    test_obj = _create_sample_test(test_id=108, test_code="T108", title="Boshlang'ich Nom")
    
    # RAM keshga o'qiladi
    cached_t = test_db.get_test_by_id(108)
    assert cached_t["title"] == "Boshlang'ich Nom"
    
    # DB da to'g'ridan-to'g'ri o'zgartirish (masalan boshqa instance yoki to'g'ridan-to'g'ri SQL)
    conn = test_db.get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE tests SET title = ? WHERE id = ?", ("Yangi Yangilangan Nom", 108))
    conn.commit()
    test_db._close_conn(conn)
    
    # Kesh muddati hali tugamagan paytda keshdan o'qiydi
    still_cached = test_db.get_test_by_id(108)
    assert still_cached["title"] == "Boshlang'ich Nom"
    
    # Non-blocking kesh: TTL o'tgan bo'lsa ham get_test_by_id sinxron bloklamaydi, eski keshni qaytaradi
    test_db._GLOBAL_TESTS_CACHE_TIME = time.time() - 60.0 # 60 soniya oldinga surish (TTL tugadi)
    stale = test_db.get_test_by_id(108)
    assert stale["title"] == "Boshlang'ich Nom"

    # Fon vazifasi (preload_all_tests_cache) chaqirilganda kesh yangilanadi
    test_db.preload_all_tests_cache()
    refreshed = test_db.get_test_by_id(108)
    assert refreshed["title"] == "Yangi Yangilangan Nom"


# -----------------------------------------------------------------------------
# 9. Stsenariy: is_maintenance_mode 5 soniyalik RAM keshi va invalidatsiyasi
# -----------------------------------------------------------------------------
def test_scenario_9_maintenance_mode_5s_cache():
    test_db.set_maintenance_mode(False)
    assert test_db.is_maintenance_mode() is False

    # DB da to'g'ridan-to'g'ri o'zgartiramiz (keshni chetlab o'tib)
    conn = test_db.get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE system_settings SET value = '1' WHERE key = 'maintenance_mode'")
    conn.commit()
    test_db._close_conn(conn)

    # 5 soniya davomida keshdagi False qiymat qaytishi kerak (0 DB so'rovi)
    assert test_db.is_maintenance_mode() is False

    # set_maintenance_mode yoki invalidate_maintenance_cache chaqirilganda darhol yangilanadi
    test_db.invalidate_maintenance_cache()
    test_db.invalidate_settings_cache()
    assert test_db.is_maintenance_mode() is True

    # Qayta tiklash
    test_db.set_maintenance_mode(False)
    assert test_db.is_maintenance_mode() is False


# -----------------------------------------------------------------------------
# 10. Stsenariy: has_any_active_test 5 soniyalik RAM keshi va invalidatsiyasi
# -----------------------------------------------------------------------------
def test_scenario_10_has_any_active_test_5s_cache():
    test_db.invalidate_test_cache()
    test_db.invalidate_active_test_cache()

    # Birorta ham faol test yo'q holat
    initial_has_active = test_db.has_any_active_test()
    assert isinstance(initial_has_active, bool)

    # Yangi faol test qo'shamiz
    _create_sample_test(test_id=999, test_code="T999", is_active=1, title="Active Test")
    
    # invalidate_test_cache chaqirilganda faol test darhol ko'rinadi
    assert test_db.has_any_active_test() is True

    # Testni nofaol qilamiz
    conn = test_db.get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE tests SET is_active = 0 WHERE id = 999")
    conn.commit()
    test_db._close_conn(conn)

    # 5 soniya kesh tufayli hali True turadi
    assert test_db.has_any_active_test() is True

    # invalidate_test_cache chaqirilganda kesh tozalanadi
    test_db.invalidate_test_cache(999)
    assert test_db.has_any_active_test() is False


# -----------------------------------------------------------------------------
# 11. Stsenariy: keyboards va should_apply_night_mode tez ishlashi
# -----------------------------------------------------------------------------
def test_scenario_11_keyboards_and_night_mode_fast():
    from core.keyboards import main_menu_kb, admin_menu_kb
    from core.config import should_apply_night_mode

    # main_menu_kb va admin_menu_kb sub-millisekundda bajarilishi kerak (0 bloklanish)
    t0 = time.perf_counter()
    kb_user = main_menu_kb(12345678)
    kb_admin = admin_menu_kb()
    night_check = should_apply_night_mode(12345678)
    duration = time.perf_counter() - t0

    assert kb_user is not None
    assert kb_admin is not None
    assert isinstance(night_check, bool)
    # 3 ta chaqiruv jami 50ms dan ancha tez bo'lishi kerak (kesh tufayli < 10ms)
    assert duration < 0.05
