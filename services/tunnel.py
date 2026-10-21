import asyncio
import os
import re
from aiogram.types import MenuButtonWebApp, WebAppInfo

from core.config import log, WEBAPP_URL, set_webapp_url, get_webapp_url
from core.bot import bot
import core.config

async def maintain_tunnel(local_port: int):
    """Telegram Mini App uchun 24/7 Render doimiy HTTPS manzilini sozlaydi (yoki agar USE_TUNNEL=true bo'lsa tunnel ochadi)."""
    global WEBAPP_URL

    use_tunnel = os.getenv("USE_TUNNEL", "0").strip().lower() in ("1", "true", "yes")

    # 1. Doimiy 24/7 Render yoki Cloud muhiti (Standart holat)
    if not use_tunnel:
        render_domain = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("WEBAPP_URL") or "https://ona-tili-bot.onrender.com"
        # Agar env'dagi manzil eski vaqtinchalik tunnel bo'lsa, Render doimiy domenini olamiz
        if any(k in render_domain for k in [".trycloudflare.com", ".lhr.life", ".serveo.net", "localhost", "127.0.0.1"]):
            render_domain = "https://ona-tili-bot.onrender.com"
            
        WEBAPP_URL = (render_domain if render_domain.startswith("http") else f"https://{render_domain}").rstrip("/")
        set_webapp_url(WEBAPP_URL)
        core.config.WEBAPP_URL = WEBAPP_URL
        log.info(f"🚀 24/7 Render.com Production URL ulandi: {WEBAPP_URL}")
        try:
            menu_btn = MenuButtonWebApp(text="Profil 👤", web_app=WebAppInfo(url=f"{WEBAPP_URL}/app.html"))
            await bot.set_chat_menu_button(menu_button=menu_btn)
            log.info(f"✅ Bot menyu tugmasi 'Profil 👤' Render 24/7 URL ga muvaffaqiyatli ulandi: {WEBAPP_URL}/app.html")
        except Exception as e:
            log.warning(f"Menu tugmasini Render URL ga ulashda ogohlantirish: {e}")
        return

    # 2. Faqat agar USE_TUNNEL=true bo'lsa vaqtinchalik tunnel provayderlarini ishlatish
    providers = [
        ("Cloudflare", ["cloudflared", "tunnel", "--url", f"http://localhost:{local_port}"], r'https://[a-zA-Z0-9\-\.]+\.trycloudflare\.com'),
        ("LocalhostRun", ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ServerAliveInterval=30", "-R", f"80:localhost:{local_port}", "nokey@localhost.run"], r'https://[a-zA-Z0-9\-\.]+\.lhr\.life')
    ]

    while True:
        for name, cmd, pattern in providers:
            try:
                log.info(f"🔌 HTTPS Tunnelga ulanmoqda ({name})...")
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT
                )
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    decoded = line.decode('utf-8', errors='ignore')
                    match = re.search(pattern, decoded)
                    if match:
                        new_url = match.group(0).rstrip("/")
                        WEBAPP_URL = new_url
                        set_webapp_url(new_url)
                        core.config.WEBAPP_URL = new_url
                        log.info(f"✨ JONLI HTTPS MINI APP URL ({name}): {WEBAPP_URL}")
                        try:
                            # Update Bot Menu Button automatically
                            menu_btn = MenuButtonWebApp(text="Profil 👤", web_app=WebAppInfo(url=f"{WEBAPP_URL}/app.html"))
                            await bot.set_chat_menu_button(menu_button=menu_btn)
                            log.info("✅ Bot menyu tugmasi 'Profil 👤' Mini App URL ga ulandi!")
                        except Exception as e:
                            log.error(f"Menu tugmasini yangilashda xatolik: {e}")
                await proc.wait()
                log.warning(f"⚠️ {name} aloqasi uzildi. Qayta ulanmoqda...")
            except Exception as e:
                log.error(f"Tunnel xatoligi ({name}): {e}")
            await asyncio.sleep(2)
        await asyncio.sleep(3)
