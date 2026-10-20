"""
Ona tili — Test Tekshirish Telegram Boti va Mini App Serveri
Aiogram 3.x + aiohttp WebApp Server
============================================================
Clean Architecture: core, services, handlers modullari bilan integratsiya qilingan.
Barcha muhim sozlamalar environment variable orqali o'rnatiladi.
"""
from __future__ import annotations
import asyncio
import os
import sys

from aiohttp import web
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo

import test_db
from core import (
    BOT_TOKEN, ADMIN_ID, PORT, WEBAPP_URL, log, bot, dp
)
from services import maintain_tunnel, schedule_checker
from handlers import user_router, admin_router, create_web_app

# ── ROUTERLARNI REGISTRATSIYA QILISH ──
dp.include_router(admin_router)
dp.include_router(user_router)


# ── ASOSIY ISHGA TUSHIRISH (MAIN) ─────────────────────
async def main():
    global WEBAPP_URL

    # 1. Baza jadvallarini serverdan oldin yaratish/tekshirish va Global Memory Cache ni to'ldirish
    try:
        test_db.init_db()
        log.info("✅ Ma'lumotlar bazasi jadvallari muvaffaqiyatli ishga tushirildi.")
        # Zero-DB-Read: barcha testlarni bir marta server RAM xotirasiga yuklash
        loaded_count = test_db.preload_all_tests_cache()
        log.info(f"🚀 Zero-DB-Read: {loaded_count} ta test RAM xotirasiga muvaffaqiyatli keshlandi!")
    except Exception as dbe:
        log.error(f"❌ DB init / preload xatolik: {dbe}")


    # 2. Webhook va Polling rejimini aniqlash (M7: Bir nechta instance to'qnashuvini oldini olish)
    USE_WEBHOOK = os.getenv("USE_WEBHOOK", "1").strip().lower() in ("1", "true", "yes")
    WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "onatili_sec_token_2026").strip()
    WEBHOOK_PATH = "/tg-webhook"

    is_valid_https = bool(WEBAPP_URL and WEBAPP_URL.startswith("https://") and "localhost" not in WEBAPP_URL)
    use_webhook_mode = USE_WEBHOOK and is_valid_https

    app = await create_web_app()

    if use_webhook_mode:
        from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
        SimpleRequestHandler(
            dispatcher=dp,
            bot=bot,
            secret_token=WEBHOOK_SECRET
        ).register(app, path=WEBHOOK_PATH)
        setup_application(app, dp, bot=bot)
        log.info(f"🔗 Webhook endpoint ro'yxatdan o'tkazildi: {WEBHOOK_PATH}")

    runner = web.AppRunner(app)
    await runner.setup()
    
    current_port = PORT
    site = None
    for attempt in range(10):
        try:
            site = web.TCPSite(runner, '0.0.0.0', current_port)
            await site.start()
            log.info(f"🌐 Mini App Server ishga tushdi: http://localhost:{current_port}")
            break
        except OSError as e:
            if e.errno == 48: # Address already in use
                current_port += 1
            else:
                raise e

    # 3. M1: Startup'da bloklangan foydalanuvchilarni o'chirish butunlay bekor qilindi (Data Preservation)

    # 4. Fon rejimida HTTPS Tunnelni boshlash
    _tunnel_task = asyncio.create_task(maintain_tunnel(current_port))

    # 5. Avtomatik jadval tekshiruvchisini ishga tushirish
    _sched_task = asyncio.create_task(schedule_checker())

    # 6. Telegram Botni ishga tushirish (Webhook yoki Polling)
    try:
        # Telegram rasmiy menyu buyruqlarini ro'yxatdan o'tkazish
        try:
            await bot.set_my_commands([
                BotCommand(command="start", description="🚀 Botni ishga tushirish"),
                BotCommand(command="app", description="📱 Test topshirish (Mini App)"),
                BotCommand(command="results", description="📊 Mening natijalarim"),
                BotCommand(command="profile", description="👤 Shaxsiy profilim"),
                BotCommand(command="help", description="ℹ️ Qo'llanma va yordam"),
            ])
            log.info("✅ Telegram Bot rasmiy buyruqlar menyusi o'rnatildi (/start, /app, ...)")

            # Telegram pastki chat menyu tugmasini doimiy ravishda "Profil 👤" qilib o'rnatish
            try:
                app_url = WEBAPP_URL
                if not app_url and os.path.exists("tunnel_url.txt"):
                    try:
                        with open("tunnel_url.txt", "r") as f:
                            app_url = f.read().strip()
                    except Exception:
                        pass
                if not app_url:
                    app_url = os.getenv("WEBAPP_URL") or "https://rash-vmrm.onrender.com"
                if "/dashboard" in app_url:
                    app_url = app_url.split("/dashboard")[0]
                menu_btn = MenuButtonWebApp(text="Profil 👤", web_app=WebAppInfo(url=f"{app_url.rstrip('/')}/app.html"))
                await bot.set_chat_menu_button(menu_button=menu_btn)
                log.info("✅ Telegram Bot pastki menyu tugmasi 'Profil 👤' qilib sozlandi!")
            except Exception as me:
                log.warning(f"Bot chat menyu tugmasini o'rnatishda ogohlantirish: {me}")
        except Exception as ce:
            log.warning(f"Bot buyruqlarini o'rnatishda ogohlantirish: {ce}")

        if use_webhook_mode:
            webhook_url = f"{WEBAPP_URL.rstrip('/')}{WEBHOOK_PATH}"
            log.info(f"🚀 Telegram Bot Webhook rejimida ishlamoqda: {webhook_url}")
            try:
                await bot.set_webhook(
                    url=webhook_url,
                    secret_token=WEBHOOK_SECRET,
                    allowed_updates=dp.resolve_used_update_types(),
                    drop_pending_updates=True
                )
                log.info("✅ Telegram Webhook muvaffaqiyatli o'rnatildi!")
            except Exception as we:
                log.error(f"❌ Telegram Webhook o'rnatishda xatolik: {we}")
            # Webhook rejimida aiohttp serverini to'xtovsiz ushlab turamiz
            stop_event = asyncio.Event()
            await stop_event.wait()
        else:
            log.info("🤖 Telegram Bot Polling rejimida ishga tushmoqda...")
            await bot.delete_webhook(drop_pending_updates=True)
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        if use_webhook_mode:
            try:
                await bot.delete_webhook()
            except Exception:
                pass
        await bot.session.close()
        if runner:
            await runner.cleanup()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Bot to'xtatildi.")
