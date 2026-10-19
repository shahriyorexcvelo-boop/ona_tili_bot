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

    # 1. aiohttp serverini DARHOL ishga tushirish (Render /healthz tekshiruvidan darhol o'tishi uchun)
    app = await create_web_app()
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

    # 2. Orqa fonda bazani tozalash (Server javob berishini to'xtatib qo'ymaslik uchun)
    async def run_startup_db_tasks():
        try:
            await asyncio.to_thread(test_db.init_db)
            cleaned = await asyncio.to_thread(test_db.delete_all_blocked_users)
            if cleaned:
                log.info(f"🧹 Bot ishga tushganda {len(cleaned)} ta oldindan bloklangan foydalanuvchi bazadan o'chirildi")
        except Exception as ex:
            log.warning(f"Startup clean blocked xatolik: {ex}")

    asyncio.create_task(run_startup_db_tasks())

    # 3. Fon rejimida HTTPS Tunnelni boshlash
    asyncio.create_task(maintain_tunnel(current_port))

    # 4. Avtomatik jadval tekshiruvchisini ishga tushirish
    asyncio.create_task(schedule_checker())

    # 5. Telegram Botni ishga tushirish
    log.info("🤖 Telegram Bot Polling rejimida ishga tushmoqda...")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
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

        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        if runner:
            await runner.cleanup()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Bot to'xtatildi.")
