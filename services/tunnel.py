import asyncio
import os
import re
from aiogram.types import MenuButtonWebApp, WebAppInfo

from core.config import log, WEBAPP_URL
from core.bot import bot

async def maintain_tunnel(local_port: int):
    """Telegram Mini App uchun tunnel yoki Railway doimiy HTTPS manzilini sozlaydi."""
    global WEBAPP_URL

    # Agar Render.com yoki Railway yoki boshqa doimiy domenda ishlayotgan bo'lsa
    render_domain = os.getenv("RENDER_EXTERNAL_URL")
    is_render = os.getenv("RENDER") == "true" or bool(os.getenv("RENDER_SERVICE_ID")) or bool(os.getenv("RENDER_INSTANCE_ID"))
    if render_domain or is_render:
        if not render_domain:
            render_domain = os.getenv("WEBAPP_URL") or "https://ona-tili-bot.onrender.com"
        WEBAPP_URL = (render_domain if render_domain.startswith("http") else f"https://{render_domain}").rstrip("/")
        log.info(f"🚀 Render.com Production muhiti aniqlandi: {WEBAPP_URL}")
        try:
            with open("tunnel_url.txt", "w") as f:
                f.write(WEBAPP_URL)
            menu_btn = MenuButtonWebApp(text="Profil 👤", web_app=WebAppInfo(url=f"{WEBAPP_URL}/app.html"))
            await bot.set_chat_menu_button(menu_button=menu_btn)
            log.info("✅ Bot menyu tugmasi 'Profil 👤' Render.com URL ga ulandi!")
        except Exception as e:
            log.error(f"Menu tugmasini yangilashda xatolik: {e}")
        return

    railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN") or os.getenv("RAILWAY_STATIC_URL")
    if railway_domain:
        WEBAPP_URL = f"https://{railway_domain}"
        log.info(f"🚂 Railway Production muhiti aniqlandi: {WEBAPP_URL}")
        try:
            with open("tunnel_url.txt", "w") as f:
                f.write(WEBAPP_URL)
            menu_btn = MenuButtonWebApp(text="Profil 👤", web_app=WebAppInfo(url=f"{WEBAPP_URL}/app.html"))
            await bot.set_chat_menu_button(menu_button=menu_btn)
            log.info("✅ Bot menyu tugmasi 'Profil 👤' Railway URL ga ulandi!")
        except Exception as e:
            log.error(f"Menu tugmasini yangilashda xatolik: {e}")
        return

    env_url = os.getenv("WEBAPP_URL", "")
    if env_url and not any(k in env_url for k in [".lhr.life", ".trycloudflare.com", ".serveo.net", "serveousercontent.com", "localhost", "127.0.0.1"]):
        WEBAPP_URL = env_url.rstrip("/")
        log.info(f"🌐 Doimiy WEBAPP_URL sozlamasi aniqlandi: {WEBAPP_URL}")
        try:
            with open("tunnel_url.txt", "w") as f:
                f.write(WEBAPP_URL)
            menu_btn = MenuButtonWebApp(text="Profil 👤", web_app=WebAppInfo(url=f"{WEBAPP_URL}/app.html"))
            await bot.set_chat_menu_button(menu_button=menu_btn)
            log.info("✅ Bot menyu tugmasi 'Profil 👤' env URL ga ulandi!")
        except Exception as e:
            log.error(f"Menu tugmasini yangilashda xatolik: {e}")
        return

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
                        new_url = match.group(0)
                        WEBAPP_URL = new_url
                        log.info(f"✨ JONLI HTTPS MINI APP URL ({name}): {WEBAPP_URL}")
                        try:
                            with open("tunnel_url.txt", "w") as f:
                                f.write(WEBAPP_URL)
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
