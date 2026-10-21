import json
import pytest
import test_db

def test_submission_flow_and_duplicate_prevention():
    # 1. Test yaratish
    test_id = 701
    conn = test_db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"DELETE FROM submissions WHERE test_id = {test_db._ph()}", (test_id,))
        cur.execute(f"DELETE FROM tests WHERE id = {test_db._ph()}", (test_id,))
        answers = {str(i): "A" for i in range(1, 45)}
        answers["45"] = "50.0"
        cur.execute(
            f"INSERT INTO tests (id, test_code, title, subject, answers_json, is_active, created_at, created_by) "
            f"VALUES ({test_db._ph()}, {test_db._ph()}, {test_db._ph()}, {test_db._ph()}, {test_db._ph()}, 1, 1000, 8039427064)",
            (test_id, "T701", "Submission Flow Test", "Ona tili", json.dumps(answers))
        )
        conn.commit()
    finally:
        test_db._close_conn(conn)

    test_db.preload_all_tests_cache()

    # 2. Ro'yxatdan o'tmagan foydalanuvchi topshirishi (UnboundLocalError yo'qligini tekshirish)
    uid_unregistered = 900099001
    user_answers = {"1": "A", "2": "B", "45": "40"}
    res1 = test_db.check_and_save_submission(test_id, uid_unregistered, user_answers)
    assert res1["submission_id"] is not None
    assert res1["fullname"] == "Foydalanuvchi"
    assert res1["score"] > 0

    # 3. Qayta topshirish: ValueError("allaqachon topshirgansiz") chiqishi shart
    with pytest.raises(ValueError) as exc:
        test_db.check_and_save_submission(test_id, uid_unregistered, user_answers)
    assert "allaqachon topshirgansiz" in str(exc.value)

    # 4. Ro'yxatdan o'tgan foydalanuvchi topshirishi
    uid_registered = 900099002
    test_db.add_or_update_user(uid_registered, "Barno Aliyeva", "+998901234567", "barno", status="approved")
    res2 = test_db.check_and_save_submission(test_id, uid_registered, user_answers)
    assert res2["submission_id"] is not None
    assert res2["fullname"] == "Barno Aliyeva"

    # 5. Qayta topshirish taqiqlangan
    with pytest.raises(ValueError) as exc:
        test_db.check_and_save_submission(test_id, uid_registered, user_answers)
    assert "allaqachon topshirgansiz" in str(exc.value)
