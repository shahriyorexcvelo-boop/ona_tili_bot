import os
import asyncio
from unittest.mock import AsyncMock, patch
import pytest
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError
from aiohttp.test_utils import TestClient, TestServer

import test_db
import services.broadcast as bc
from core.bot import bot
from handlers.webapp import create_web_app


@pytest.mark.anyio
async def test_broadcast_retry_after(monkeypatch):
    """
    1) send_broadcast_to_users da TelegramRetryAfter ushlanib, 
    3 martagacha qayta urinilishi va muvaffaqiyatli yetkazilishi.
    """
    # 1 ta tasdiqlangan foydalanuvchi yaratish
    tg_id = 111222333
    await test_db.db(test_db.add_or_update_user, tg_id, "Test User", "testuser", "+998901112233", "approved")

    attempts = [0]

    async def mock_send_message(chat_id, text, **kwargs):
        attempts[0] += 1
        if attempts[0] < 3:
            # Dastlabki 2 urinishda Flood limit (Retry-After)
            raise TelegramRetryAfter(method=None, message="Flood", retry_after=0.01)
        # 3-urinishda muvaffaqiyat
        mock_msg = AsyncMock()
        mock_msg.message_id = 777
        return mock_msg

    monkeypatch.setattr(bot, "send_message", mock_send_message)

    sent, fail, batch_id = await bc.send_broadcast_to_users(message_text="Test xabar", sender_tg_id=8039427064)

    assert attempts[0] == 3, f"3 marta qayta urinish bo'lishi kerak edi, lekin {attempts[0]} bo'ldi"
    assert sent == 1
    assert fail == 0

    # Barcha 3 urinishda ham RetryAfter bo'lsa fail_count oshishi tekshiruvi
    attempts[0] = 0

    async def mock_always_flood(chat_id, text, **kwargs):
        attempts[0] += 1
        raise TelegramRetryAfter(method=None, message="Flood", retry_after=0.01)

    monkeypatch.setattr(bot, "send_message", mock_always_flood)

    sent_f, fail_f, batch_id_f = await bc.send_broadcast_to_users(message_text="Fail xabar", sender_tg_id=8039427064)
    assert attempts[0] == 3
    assert sent_f == 0
    assert fail_f == 1


@pytest.mark.anyio
async def test_broadcast_forbidden_and_user_gone(monkeypatch):
    """
    2) TelegramForbiddenError bo'lganda delete_user o'rniga users.status='blocked_bot'
    qo'yilishi, ma'lumot saqlanishi va keyingi broadcastlardan chiqarilishi.
    """
    tg_id = 444555666
    await test_db.db(test_db.add_or_update_user, tg_id, "Blocked User", "blocked_u", "+998904445566", "approved")

    # Bot yuborishda Forbidden xatosi berishi
    async def mock_forbidden(chat_id, text, **kwargs):
        raise TelegramForbiddenError(method=None, message="Forbidden: bot was blocked by the user")

    monkeypatch.setattr(bot, "send_message", mock_forbidden)

    sent, fail, batch_id = await bc.send_broadcast_to_users(message_text="Blocked test", sender_tg_id=8039427064)
    assert sent == 0
    assert fail == 1

    # Foydalanuvchi bazadan o'chmagan bo'lishi, lekin status='blocked_bot' bo'lishi shart!
    user = await test_db.db(test_db.get_user, tg_id)
    assert user is not None, "Foydalanuvchi bazadan o'chirilmasligi kerak (Data preservation)!"
    assert user.get("status") == "blocked_bot", "Status 'blocked_bot' qilib belgilanishi shart"

    # get_broadcast_users ushbu foydalanuvchini o'tkazib yuborishi shart
    bc_users = await test_db.db(test_db.get_broadcast_users)
    bc_uids = [u.get("tg_id") for u in bc_users]
    assert tg_id not in bc_uids, "blocked_bot foydalanuvchi xabar tarqatish ro'yxatida bo'lmasligi kerak"


@pytest.mark.anyio
async def test_broadcast_mass_1000_users_batching_and_resume(monkeypatch):
    """
    3) 1000 ta foydalanuvchiga xabar tarqatish:
    - 50 talik to'plamlar (record_broadcast_messages_batch) bilan yozilishi
    - batch status ('running' -> 'done')
    - qayta yuborilganda allaqachon borgan chat_id larga dublikat yubormaslik.
    """
    # 1000 ta foydalanuvchini SQLite ga tezkor joylash
    conn = test_db.get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM users")
    cur.execute("DELETE FROM broadcast_messages")
    cur.execute("DELETE FROM broadcast_history")

    user_rows = []
    base_id = 900000000
    for i in range(1000):
        uid = base_id + i
        user_rows.append((uid, f"User {i}", f"u{i}", "+998900000000", "approved", 1700000000))

    cur.executemany("""
    INSERT INTO users (tg_id, fullname, username, phone, status, registered_at)
    VALUES (?, ?, ?, ?, ?, ?)
    """, user_rows)
    conn.commit()
    test_db._close_conn(conn)

    send_call_count = [0]

    async def mock_send_msg(chat_id, text, **kwargs):
        send_call_count[0] += 1
        mock_msg = AsyncMock()
        mock_msg.message_id = 10000 + send_call_count[0]
        return mock_msg

    monkeypatch.setattr(bot, "send_message", mock_send_msg)

    # 1000 ta xabar yuborishda asyncio.sleep(0.04) kutmaslik uchun mock qilish
    async def fast_sleep(s):
        pass

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)

    # Birinchi marta 1000 ta foydalanuvchiga yuborish
    sent, fail, batch_id = await bc.send_broadcast_to_users(
        message_text="Ommaviy xabar",
        sender_tg_id=8039427064
    )

    assert sent == 1000
    assert fail == 0
    assert send_call_count[0] == 1000

    # broadcast_messages jadvalida 1000 ta yozuv borligini tekshirish
    all_msgs = await test_db.db(test_db.get_broadcast_messages, batch_id)
    assert len(all_msgs) == 1000

    # Partiya statusi 'done' bo'lganini tekshirish
    conn2 = test_db.get_connection()
    cur2 = conn2.cursor()
    cur2.execute("SELECT status, total_sent FROM broadcast_history WHERE batch_id = ?", (batch_id,))
    row = cur2.fetchone()
    assert row[0] == "done"
    assert row[1] == 1000
    test_db._close_conn(conn2)

    # 4) XUDDI SHU batch_id bilan qayta ishga tushirish (Resume / Qayta urinish)
    send_call_count[0] = 0
    sent_res, fail_res, _ = await bc.send_broadcast_to_users(
        message_text="Qayta urinish",
        sender_tg_id=8039427064,
        batch_id=batch_id
    )

    # Allaqachon yuborilgani sababli 0 ta yangi so'rov ketishi shart (Dublikatsiz)
    assert send_call_count[0] == 0, "Allaqachon yuborilgan foydalanuvchilarga qayta xabar yuborilmasligi shart"
    assert sent_res == 0


@pytest.mark.anyio
async def test_dashboard_broadcast_security_and_domain_whitelist(monkeypatch):
    """
    4) Dashboard Broadcast xavfsizlik tekshiruvlari:
    - button_url faqat https:// va ruxsat berilgan domenlar
    - photo_url faqat https://
    - Endpoint admin auth bilan himoyalanganligi.
    """
    monkeypatch.setenv("ALLOW_INSECURE_AUTH", "1")
    monkeypatch.setenv("ALLOWED_BUTTON_DOMAINS", "t.me,telegram.me,youtube.com")

    app = await create_web_app()
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        admin_id = 8039427064
        await test_db.db(test_db.add_or_update_user, admin_id, "Admin", "admin", "+998901234567", "approved")

        # A) Admin bo'lmagan so'rov -> 403
        resp_unauth = await client.post("/api/dashboard/broadcast", json={
            "admin_id": 999111222,
            "message": "Salom"
        })
        assert resp_unauth.status == 403

        # B) photo_url http bo'lsa -> 400 rad etiladi
        resp_http_photo = await client.post("/api/dashboard/broadcast", json={
            "admin_id": admin_id,
            "message": "Rasm",
            "photo_url": "http://insecure-site.com/image.jpg"
        })
        assert resp_http_photo.status == 400
        data_p = await resp_http_photo.json()
        assert "https://" in data_p["message"]

        # C) button_url ruxsat etilmagan domen bo'lsa -> 400 rad etiladi
        resp_bad_domain = await client.post("/api/dashboard/broadcast", json={
            "admin_id": admin_id,
            "message": "Havola",
            "button_text": "Kiring",
            "button_url": "https://phishing-site.xyz/login"
        })
        assert resp_bad_domain.status == 400
        data_b = await resp_bad_domain.json()
        assert "ruxsat etilmagan" in data_b["message"].lower()

        # D) Ruxsat etilgan domen (t.me) -> qabul qilinadi
        mock_msg = AsyncMock()
        mock_msg.message_id = 9999
        monkeypatch.setattr(bot, "send_message", AsyncMock(return_value=mock_msg))

        resp_ok = await client.post("/api/dashboard/broadcast", json={
            "admin_id": admin_id,
            "message": "Xavfsiz xabar",
            "button_text": "Kanal",
            "button_url": "https://t.me/rasmiy_kanal"
        })
        assert resp_ok.status == 200
        data_ok = await resp_ok.json()
        assert data_ok["success"] is True
    finally:
        await client.close()
