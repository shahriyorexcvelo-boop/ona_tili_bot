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
    assert details["45"]["status"] == "essay"
    assert details["45"]["score"] == 65.0
    assert sub["correct_count"] == 49
    assert sub["total_count"] == 49
    assert sub["test_score"] == 75.0
    assert sub["essay_score"] == 65.0


def test_rasch_ali_vs_vali_and_zero_one_two_rules():
    from rasch_engine import evaluate_rasch_scores, N_ITEMS
    import numpy as np

    assert N_ITEMS == 49
    # 30 ta talaba va 49 ta savol matritsasi:
    # 1..20 savollar juda oson (deyarli hamma yechgan)
    # 21..49 savollar juda qiyin (kamchilik yechgan)
    rng = np.random.default_rng(42)
    N = 30
    M = np.zeros((N, 49), dtype=int)
    for n in range(N):
        # Oson savollar (0..19): 85% ehtimol bilan yechiladi
        M[n, :20] = (rng.random(20) < 0.85).astype(int)
        # Qiyin savollar (20..48): 15% ehtimol bilan yechiladi
        M[n, 20:] = (rng.random(29) < 0.15).astype(int)

    # Maxsus o'quvchilar:
    # Student 0 (Zero): 0 ta to'g'ri
    M[0, :] = 0
    # Student 1 (One): 1 ta to'g'ri
    M[1, :] = 0
    M[1, 0] = 1
    # Student 2 (Two): 2 ta to'g'ri
    M[2, :] = 0
    M[2, :2] = 1
    # Student 3 (Ali): 20 ta oson savolni topgan
    M[3, :] = 0
    M[3, :20] = 1
    # Student 4 (Vali): 20 ta qiyin savolni topgan
    M[4, :] = 0
    M[4, 29:49] = 1
    # Student 5 (All): 49 ta to'g'ri
    M[5, :] = 1

    student_ids = [1000 + i for i in range(N)]
    essay_scores = {sid: 50.0 for sid in student_ids}
    essay_scores[1000] = 60.0  # 0 ta to'g'ri ishlagan bo'lsa ham 0.0 chiqishi shart
    essay_scores[1005] = 75.0  # 49 ta to'g'ri + 75 esse -> 75.0 (A+)

    responses_matrix = [
        {
            "student_id": student_ids[i],
            "responses": M[i].tolist(),
            "essay_score": essay_scores[student_ids[i]],
        }
        for i in range(N)
    ]

    res = evaluate_rasch_scores(responses_matrix)
    st_map = {s["student_id"]: s for s in res["students"]}

    # 1) 0 ta to'g'ri -> 0.0 ball va "—"
    s_zero = st_map[1000]
    assert s_zero["raw_score"] == 0
    assert s_zero["test_score"] == 0.0
    assert s_zero["final_score"] == 0.0
    assert s_zero["grade"] == "—"

    # 2) 1 va 2 ta to'g'ri -> sertifikat berilmaydi ("—") va silliq past ball
    s_one = st_map[1001]
    s_two = st_map[1002]
    assert s_one["grade"] == "—"
    assert s_two["grade"] == "—"
    assert 0.0 < s_one["test_score"] < s_two["test_score"] < 25.0

    # 3) Ali (20 ta oson) vs Vali (20 ta qiyin)
    s_ali = st_map[1003]
    s_vali = st_map[1004]
    assert s_ali["raw_score"] == 20
    assert s_vali["raw_score"] == 20
    assert s_vali["test_score"] > s_ali["test_score"] + 7.0  # Vali Alidan ancha yuqori ball oladi (~59 vs ~48)
    assert 46.0 <= s_ali["test_score"] <= 51.0
    assert 56.0 <= s_vali["test_score"] <= 63.0

    # 4) 49 ta to'g'ri (ko'p odam qatnashgan qiyin testda) -> 75.0 balldan oshadi, lekin daraja A+ dan oshmaydi!
    s_all = st_map[1005]
    assert s_all["test_score"] > 75.0
    assert s_all["final_score"] > 75.0
    assert s_all["grade"] == "A+"

