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
import secrets
import sys

from aiohttp import web
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo, ErrorEvent

import test_db
from core import (
    BOT_TOKEN, ADMIN_ID, PORT, WEBAPP_URL, get_webapp_url, log, bot, dp
)
from services import (
    maintain_tunnel, schedule_checker,
    supabase_keepalive_task, event_loop_lag_monitor, memory_rss_monitor
)
from handlers import user_router, admin_router, create_web_app

# ── ROUTERLARNI REGISTRATSIYA QILISH ──
dp.include_router(admin_router)
dp.include_router(user_router)


@dp.error()
async def global_error_handler(event: ErrorEvent):
    """Botda connection pool band bo'lganda foydalanuvchiga muloyim javob qaytarish."""
    if isinstance(event.exception, test_db.DatabasePoolBusyError):
        log.warning(f"⚠️ Botda Pool band ogohlantirishi: {event.exception}")
        try:
            if event.update and event.update.message:
                await event.update.message.answer("⚠️ Server band, bir necha soniyadan keyin urinib ko'ring.")
            elif event.update and event.update.callback_query:
                await event.update.callback_query.answer("⚠️ Server band, bir necha soniyadan keyin urinib ko'ring.", show_alert=True)
        except Exception:
            pass
        return True
    return False


async def tests_cache_refresher():
    """Har 30 soniyada global testlar keshini fonda yangilab turuvchi vazifa (event loop ni bloklamaydi)."""
    while True:
        try:
            await asyncio.sleep(30)
            await test_db.db(test_db.preload_all_tests_cache)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.warning(f"⚠️ Testlar keshini fonda yangilashda ogohlantirish: {e}")


# ── ASOSIY ISHGA TUSHIRISH (MAIN) ─────────────────────
async def main():
    _tunnel_task = None
    _sched_task = None
    _cache_refresher_task = None
    _supabase_keepalive_task = None
    _lag_monitor_task = None
    _rss_monitor_task = None
    runner = None
    use_webhook_mode = False
    is_render = False

    # 1. Baza jadvallarini serverdan oldin yaratish/tekshirish va Global Memory Cache ni to'ldirish
    try:
        await test_db.db(test_db.init_db)
        log.info("✅ Ma'lumotlar bazasi jadvallari muvaffaqiyatli ishga tushirildi.")
        # Zero-DB-Read: barcha testlarni bir marta server RAM xotirasiga yuklash
        loaded_count = await test_db.db(test_db.preload_all_tests_cache)
        log.info(f"🚀 Zero-DB-Read: {loaded_count} ta test RAM xotirasiga muvaffaqiyatli keshlandi!")
    except Exception as dbe:
        log.error(f"❌ DB init / preload xatolik: {dbe}")


    # 2. Webhook va Polling rejimini aniqlash (M7: Bir nechta instance to'qnashuvini oldini olish)
    # Lokal ishga tushirilganda (RENDER_EXTERNAL_URL va RENDER env yo'q bo'lsa) webhook O'RNATILMASIN va O'CHIRILMASIN
    is_render = bool(os.getenv("RENDER_EXTERNAL_URL") or os.getenv("RENDER") == "true" or os.getenv("RENDER_SERVICE_ID"))
    USE_WEBHOOK = os.getenv("USE_WEBHOOK", "1" if is_render else "0").strip().lower() in ("1", "true", "yes")
    WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
    WEBHOOK_PATH = "/tg-webhook"

    active_url = get_webapp_url()
    is_valid_https = bool(active_url and active_url.startswith("https://") and "localhost" not in active_url)
    use_webhook_mode = bool(is_render and USE_WEBHOOK and is_valid_https)

    if use_webhook_mode and not WEBHOOK_SECRET:
        WEBHOOK_SECRET = secrets.token_urlsafe(32)
        log.error("❌ XATOLIK: WEBHOOK_SECRET muhit o'zgaruvchisi (env) o'rnatilmagan! Avtomatik xavfsiz tasodifiy token yaratildi.")

    app = await create_web_app()

    if use_webhook_mode:
        from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
        SimpleRequestHandler(
            dispatcher=dp,
            bot=bot,
            secret_token=WEBHOOK_SECRET,
            handle_in_background=True
        ).register(app, path=WEBHOOK_PATH)
        setup_application(app, dp, bot=bot)
        log.info(f"🔗 Webhook endpoint ro'yxatdan o'tkazildi (handle_in_background=True): {WEBHOOK_PATH}")

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

    # 6. Global testlar keshini fonda har 30 soniyada yangilab turish (Zero-Block)
    _cache_refresher_task = asyncio.create_task(tests_cache_refresher())

    # 7. Kuzatuv va Keep-Alive fon vazifalari
    _supabase_keepalive_task = asyncio.create_task(supabase_keepalive_task())
    _lag_monitor_task = asyncio.create_task(event_loop_lag_monitor())
    _rss_monitor_task = asyncio.create_task(memory_rss_monitor())

    # 8. Telegram Botni ishga tushirish (Webhook yoki Polling)
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
                app_url = get_webapp_url()
                if "/dashboard" in app_url:
                    app_url = app_url.split("/dashboard")[0]
                menu_btn = MenuButtonWebApp(text="Profil 👤", web_app=WebAppInfo(url=f"{app_url.rstrip('/')}/app.html"))
                await bot.set_chat_menu_button(menu_button=menu_btn)
                log.info(f"✅ Telegram Bot pastki menyu tugmasi 'Profil 👤' ({app_url}) qilib sozlandi!")
            except Exception as me:
                log.warning(f"Bot chat menyu tugmasini o'rnatishda ogohlantirish: {me}")
        except Exception as ce:
            log.warning(f"Bot buyruqlarini o'rnatishda ogohlantirish: {ce}")

        if use_webhook_mode:
            webhook_url = f"{get_webapp_url().rstrip('/')}{WEBHOOK_PATH}"
            log.info(f"🚀 Telegram Bot Webhook rejimida ishlamoqda: {webhook_url}")
            try:
                await bot.set_webhook(
                    url=webhook_url,
                    secret_token=WEBHOOK_SECRET,
                    allowed_updates=dp.resolve_used_update_types(),
                    drop_pending_updates=False
                )
                log.info("✅ Telegram Webhook muvaffaqiyatli o'rnatildi (drop_pending_updates=False)!")
            except Exception as we:
                log.error(f"❌ Telegram Webhook o'rnatishda xatolik: {we}")
            # Webhook rejimida aiohttp serverini to'xtovsiz ushlab turamiz
            stop_event = asyncio.Event()
            await stop_event.wait()
        else:
            log.info("🤖 Telegram Bot Polling rejimida ishga tushmoqda...")
            if is_render:
                try:
                    await bot.delete_webhook(drop_pending_updates=False)
                except Exception:
                    pass
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        for t in (
            _cache_refresher_task, _sched_task, _tunnel_task,
            _supabase_keepalive_task, _lag_monitor_task, _rss_monitor_task
        ):
            if t and not t.done():
                t.cancel()
        # finally blokidagi bot.delete_webhook() faqat polling rejimida chaqirilsin, webhook rejimida o'chirilmasin
        if not use_webhook_mode and is_render:
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
