import os
import asyncio
from unittest.mock import AsyncMock
import pytest
from aiohttp.test_utils import TestClient, TestServer

import test_bot
import test_db


@pytest.mark.anyio
async def test_main_local_polling_mode(monkeypatch):
    """
    1) Lokal polling rejimi:
    RENDER env yo'q, USE_WEBHOOK=0.
    UnboundLocalError yo'qligi, bot.set_my_commands va dp.start_polling chaqirilishi tekshiriladi.
    """
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    monkeypatch.delenv("RENDER_SERVICE_ID", raising=False)
    monkeypatch.setenv("USE_WEBHOOK", "0")
    monkeypatch.setenv("DATABASE_URL", "")

    # Subprocess tunnel va fon monitorlarini mock qilish
    async def mock_maintain_tunnel(port):
        await asyncio.sleep(0.01)

    monkeypatch.setattr(test_bot, "maintain_tunnel", mock_maintain_tunnel)
    monkeypatch.setattr(test_bot.bot, "set_my_commands", AsyncMock(return_value=True))
    monkeypatch.setattr(test_bot.bot, "set_chat_menu_button", AsyncMock(return_value=True))
    monkeypatch.setattr(test_bot.bot, "delete_webhook", AsyncMock(return_value=True))

    polling_started = asyncio.Event()

    async def mock_start_polling(bot, **kwargs):
        polling_started.set()
        await asyncio.sleep(0.05)

    monkeypatch.setattr(test_bot.dp, "start_polling", mock_start_polling)

    # main() ni ishga tushirish
    main_task = asyncio.create_task(test_bot.main())

    try:
        await asyncio.wait_for(polling_started.wait(), timeout=5.0)
        assert polling_started.is_set(), "dp.start_polling chaqirilmadi"
        assert test_bot.bot.set_my_commands.called, "bot.set_my_commands chaqirilmadi"
    finally:
        main_task.cancel()
        try:
            await main_task
        except (asyncio.CancelledError, Exception):
            pass


@pytest.mark.anyio
async def test_main_render_webhook_mode(monkeypatch):
    """
    2) Render Webhook rejimi:
    RENDER=true, RENDER_EXTERNAL_URL=https://example.com, USE_WEBHOOK=1, WEBHOOK_SECRET=""
    UnboundLocalError yo'qligi, WEBHOOK_SECRET bo'sh bo'lganda avtomatik xavfsiz token
    yaratilib, bot.set_webhook ga uzatilishi tekshiriladi.
    """
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://example.com")
    monkeypatch.setenv("USE_WEBHOOK", "1")
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("DATABASE_URL", "")

    async def mock_maintain_tunnel(port):
        await asyncio.sleep(0.01)

    monkeypatch.setattr(test_bot, "maintain_tunnel", mock_maintain_tunnel)
    monkeypatch.setattr(test_bot.bot, "set_my_commands", AsyncMock(return_value=True))
    monkeypatch.setattr(test_bot.bot, "set_chat_menu_button", AsyncMock(return_value=True))
    
    mock_set_webhook = AsyncMock(return_value=True)
    monkeypatch.setattr(test_bot.bot, "set_webhook", mock_set_webhook)

    main_task = asyncio.create_task(test_bot.main())

    try:
        # bot.set_webhook chaqirilishini kutish
        for _ in range(50):
            if mock_set_webhook.called:
                break
            await asyncio.sleep(0.1)

        assert mock_set_webhook.called, "bot.set_webhook chaqirilmadi"
        call_kwargs = mock_set_webhook.call_args[1]

        assert call_kwargs["url"] == "https://example.com/tg-webhook"
        assert call_kwargs["drop_pending_updates"] is False
        secret_tok = call_kwargs["secret_token"]
        assert secret_tok is not None, "secret_token None bo'lmasligi kerak"
        assert len(secret_tok) >= 32, "Avtomatik yaratilgan token kamida 32 belgili bo'lishi kerak"
    finally:
        main_task.cancel()
        try:
            await main_task
        except (asyncio.CancelledError, Exception):
            pass


@pytest.mark.anyio
async def test_unregistered_user_access_blocked(monkeypatch):
    """
    3) Botda ro'yxatdan o'tmagan foydalanuvchilar Mini App orqali testlarni
    ko'ra olmasligi va ishga tushira olmasligi (403 not_registered qaytishi).
    """
    from handlers.webapp import create_web_app

    monkeypatch.setenv("ALLOW_INSECURE_AUTH", "1")
    app = await create_web_app()
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        unreg_tg_id = 999888777

        # Bazada bunday foydalanuvchi yo'qligini tekshirish
        user = await test_db.db(test_db.get_user, unreg_tg_id)
        assert user is None

        # 1. Faol testlarni so'rash -> 403 not_registered
        resp = await client.get(f"/api/app/active-tests?tg_id={unreg_tg_id}")
        assert resp.status == 403
        data = await resp.json()
        assert data["status"] == "not_registered"
        assert "ro'yxatdan o'ting" in data["message"].lower()

        # 2. Testni yechishni boshlash (trigger-solve) -> 403 not_registered
        resp_solve = await client.get(f"/api/app/trigger-solve?tg_id={unreg_tg_id}&test_id=1")
        assert resp_solve.status == 403
        solve_data = await resp_solve.json()
        assert solve_data["status"] == "not_registered"

        # 3. Foydalanuvchi ro'yxatdan o'tgach (approved) -> 200 ruxsat beriladi
        await test_db.db(
            test_db.add_or_update_user,
            unreg_tg_id, "Ali Valiyev", "alivaliyev", "+998901234567", "approved"
        )
        resp_ok = await client.get(f"/api/app/active-tests?tg_id={unreg_tg_id}")
        assert resp_ok.status == 200
        ok_data = await resp_ok.json()
        assert ok_data["success"] is True
    finally:
        await client.close()
