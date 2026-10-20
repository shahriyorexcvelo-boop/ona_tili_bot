import time
import pytest
from core.telegram_utils import is_user_gone_error
import test_db


class FakeTelegramError(Exception):
    pass


class FakeRetryAfter(Exception):
    def __init__(self, retry_after=5):
        super().__init__(f"Flood control exceeded. Retry in {retry_after} seconds")
        self.retry_after = retry_after


def test_is_user_gone_error():
    # 1. User gone errors (bot blocked, kicked, user deactivated, chat not found)
    assert is_user_gone_error(FakeTelegramError("Forbidden: bot was blocked by the user")) is True
    assert is_user_gone_error(FakeTelegramError("Forbidden: user is deactivated")) is True
    assert is_user_gone_error(FakeTelegramError("Bad Request: chat not found")) is True
    assert is_user_gone_error(FakeTelegramError("Forbidden: bot was kicked from the group")) is True
    assert is_user_gone_error(FakeTelegramError("bot was blocked by user")) is True

    # 2. Transient / network / retry errors MUST return False
    assert is_user_gone_error(FakeRetryAfter(30)) is False
    assert is_user_gone_error(FakeTelegramError("Request timed out")) is False
    assert is_user_gone_error(FakeTelegramError("Network is unreachable")) is False
    assert is_user_gone_error(FakeTelegramError("Connection reset by peer")) is False
    assert is_user_gone_error(FakeTelegramError("Telegram server error 502")) is False
    assert is_user_gone_error(Exception("Unknown database error")) is False


def test_admin_delete_purges_user_and_records_admin_deleted():
    uid = 999101
    admin_id = 123456789

    # Foydalanuvchi va uning ma'lumotlarini yaratish
    test_db.add_or_update_user(uid, "Ali Valiyev", "+998901234567", "ali_valiyev", status="approved")
    u = test_db.get_user(uid)
    assert u is not None
    assert u["fullname"] == "Ali Valiyev"

    # Admin tomonidan o'chirish
    res = test_db.delete_user(uid, by_admin=True, deleted_by=admin_id)
    assert res is not None

    # Foydalanuvchi users jadvalidan to'liq o'chgan bo'lishi kerak
    assert test_db.get_user(uid) is None

    # Faqat tg_id admin_deleted_users jadvalida qayd etilgan bo'lishi kerak
    assert test_db.is_admin_deleted(uid) is True
    info = test_db.get_admin_deleted_info(uid)
    assert info is not None
    assert int(info["tg_id"]) == uid
    assert int(info["deleted_by"]) == admin_id


def test_self_delete_does_not_record_admin_deleted():
    uid = 999102

    # Foydalanuvchi ro'yxatdan o'tdi
    test_db.add_or_update_user(uid, "Madina Rahimova", "+998907654321", "madina_r", status="approved")
    assert test_db.get_user(uid) is not None

    # Foydalanuvchi botni blokladi yoki o'z akkauntini o'chirdi (is_self_delete=True)
    test_db.delete_user(uid, is_self_delete=True)

    # Foydalanuvchi bazadan o'chgan
    assert test_db.get_user(uid) is None

    # LEKIN admin_deleted_users jadvaliga TUSHMAYDI!
    assert test_db.is_admin_deleted(uid) is False


def test_admin_deleted_user_approval_flow():
    uid = 999103
    admin_id = 123456789

    # 1. Avval admin uni o'chirgan
    test_db.add_or_update_user(uid, "O'tkir Hoshimov", "", "otkir", status="approved")
    test_db.delete_user(uid, by_admin=True, deleted_by=admin_id)
    assert test_db.is_admin_deleted(uid) is True

    # 2. Foydalanuvchi qaytib keldi va ro'yxatdan o'tdi -> status 'pending' bo'ladi
    test_db.add_or_update_user(uid, "O'tkir Hoshimov", "", "otkir", status="pending")
    u = test_db.get_user(uid)
    assert u["status"] == "pending"
    assert test_db.is_user_approved(uid) is False

    # 3. add_or_update_user qayta chaqirilsa ham pending/rejected holatini o'zgartirmasligi kerak
    test_db.add_or_update_user(uid, "O'tkir Hoshimov", "", "otkir", status="approved")
    u_after = test_db.get_user(uid)
    assert u_after["status"] == "pending"  # Himoyalangan

    # 4. Admin uni tasdiqladi (approve_user)
    test_db.approve_user(uid)
    u_approved = test_db.get_user(uid)
    assert u_approved["status"] == "approved"
    assert test_db.is_user_approved(uid) is True

    # admin_deleted_users jadvalidan tozalangan bo'lishi kerak
    assert test_db.is_admin_deleted(uid) is False


def test_admin_deleted_user_reject_flow():
    uid = 999104
    admin_id = 123456789

    test_db.mark_admin_deleted(uid, admin_id=admin_id)
    test_db.add_or_update_user(uid, "Salim Bozorov", "", "salim", status="pending")

    # Admin rad etdi
    test_db.reject_user(uid)
    u = test_db.get_user(uid)
    assert u["status"] == "rejected"
    assert test_db.is_user_approved(uid) is False


def test_cooldown_spam_protection():
    uid = 999105

    test_db.mark_admin_deleted(uid)
    # Hali so'rov yuborilmagan: can_request_access -> True
    can_req, diff_h = test_db.can_request_access(uid, cooldown_hours=6)
    assert can_req is True

    # So'rov yuborildi
    test_db.touch_admin_deleted_request(uid)
    can_req2, diff_h2 = test_db.can_request_access(uid, cooldown_hours=6)
    assert can_req2 is False
    assert diff_h2 < 0.1


def test_blocked_status_eliminated():
    uid = 999106

    test_db.add_or_update_user(uid, "Nodir Xoliqov", "", "nodir", status="approved")
    # block_user chaqirilganda 'rejected' ga yo'naltiriladi
    test_db.block_user(uid)
    u = test_db.get_user(uid)
    assert u["status"] == "rejected"
    assert u["status"] != "blocked"

    # delete_all_blocked_users bo'sh ro'yxat qaytaradi
    assert test_db.delete_all_blocked_users() == []

    # get_users_count da blocked har doim 0 bo'ladi
    counts = test_db.get_users_count()
    assert counts.get("blocked") == 0


def test_national_certificate_75_scale_grading():
    # 75 ballik shkala tekshiruvlari
    assert test_db.calculate_grade(75.0) == "A+"
    assert test_db.calculate_grade(70.0) == "A+"
    assert test_db.calculate_grade(74.5) == "A+"
    # Rasch ta'sirida 75 dan oshsa ham A+ bo'lib qoladi
    assert test_db.calculate_grade(78.5) == "A+"

    assert test_db.calculate_grade(69.9) == "A"
    assert test_db.calculate_grade(65.0) == "A"

    assert test_db.calculate_grade(64.5) == "B+"
    assert test_db.calculate_grade(60.0) == "B+"

    assert test_db.calculate_grade(59.9) == "B"
    assert test_db.calculate_grade(55.0) == "B"

    assert test_db.calculate_grade(54.5) == "C+"
    assert test_db.calculate_grade(50.0) == "C+"

    assert test_db.calculate_grade(49.5) == "C"
    assert test_db.calculate_grade(46.0) == "C"

    assert test_db.calculate_grade(45.9) == "—"
    assert test_db.calculate_grade(30.0) == "—"
    assert test_db.calculate_grade(0.0) == "—"


def test_submission_with_essay_calculation():
    uid = 999107
    test_db.add_or_update_user(uid, "Shoira Azizova", "", "shoira", status="approved")

    # 44 ta savoldan iborat Ona tili testi yaratish
    answers = {}
    for i in range(1, 36):
        answers[str(i)] = "A"
    for i in range(36, 40):
        answers[str(i)] = "kelishik"
    for i in range(40, 45):
        answers[f"{i}a"] = "ega"
        answers[f"{i}b"] = "kesim"

    test_code = "TEST75"
    created = test_db.create_test(
        test_code=test_code,
        title="Ona tili Milliy Sertifikat Testi",
        subject="Ona tili",
        answers=answers,
        time_limit_min=120,
        created_by=123
    )
    assert created is True

    t_obj = test_db.get_test_by_code(test_code)
    assert t_obj is not None
    test_id = t_obj["id"]

    # O'quvchi javoblari:
    # Barcha test savollariga to'g'ri javob berdi -> Test bali = 75.0 ball
    user_ans = dict(answers)
    # 45-savol sifatida o'zining halol tahminiy esse balini kiritdi: 65.0 ball
    user_ans["45"] = "65"

    sub = test_db.check_and_save_submission(test_id, uid, user_ans)
    assert sub is not None

    # Test bali = 75.0, Esse bali = 65.0
    # O'rtacha arifmetik = round((75.0 + 65.0) / 2.0, 1) = 70.0 ball
    assert sub["score"] == 70.0
    assert sub["max_score"] == 75.0
    # 70.0 ball -> A+
    assert sub["grade"] == "A+"

    details = sub["details"]
    assert "45" in details
    assert details["45"]["type"] == "essay"
    assert details["45"]["score"] == 65.0
