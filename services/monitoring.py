import asyncio
import time
import logging

import test_db

log = logging.getLogger("OnaTiliRasch")

# Og'ir vazifalar (PDF, Rasch hisobi, rasm/Gemini) uchun bir vaqtda ko'pi bilan 2 ta parallel jarayon
HEAVY_TASKS_SEMAPHORE = asyncio.Semaphore(2)

async def supabase_keepalive_task():
    """
    Supabase bepul tarifi (free tier) 7 kun davomida so'rov tushmasa avtomatik pauza (pause) bo'ladi.
    Ushbu fon vazifasi har 6 soatda bir marta yengil SELECT 1 so'rovini yuborib bazani doimo faol ushlab turadi.
    """
    while True:
        try:
            # 6 soat kutish (21600 soniya)
            await asyncio.sleep(21600)
            success = await test_db.db(test_db.ping_db)
            if success:
                log.info("🏓 Supabase Keep-Alive ping muvaffaqiyatli bajarildi (SELECT 1).")
            else:
                log.warning("⚠️ Supabase Keep-Alive ping javob bermadi.")
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.warning(f"⚠️ Supabase Keep-Alive xatosi: {e}")


async def event_loop_lag_monitor():
    """
    Bitta umumiy event loop da bot, webhook va Mini App API ishlaydi.
    Ushbu monitor har 5 soniyada 1.0 soniyalik uyquning haqiqiy kechikishini o'lchaydi.
    Agar kechikish (lag) 0.5 soniyadan oshsa, event loop to'silganligi haqida darhol ogohlantirish beradi.
    """
    while True:
        try:
            t0 = time.monotonic()
            await asyncio.sleep(1.0)
            dt = time.monotonic() - t0
            lag = dt - 1.0
            if lag > 0.5:
                log.warning(f"⚠️ Event loop kechikishi yuqori (lag: {lag:.2f}s)! Qaysidir sinxron chaqiruv loopni to'sib qo'ydi.")
            await asyncio.sleep(4.0)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.warning(f"Lag monitor xatolik: {e}")
            await asyncio.sleep(5.0)


async def memory_rss_monitor():
    """
    Render FREE muhiti (~512 MB RAM) uchun xotira sarfini (RSS) har 5 daqiqada nazorat qilish.
    Agar xotira hajmi 400 MB dan oshsa, ogohlantirish (warning) yoziladi.
    """
    while True:
        try:
            await asyncio.sleep(300) # 5 daqiqa
            rss_mb = 0.0
            try:
                import psutil
                process = psutil.Process()
                rss_mb = process.memory_info().rss / (1024 * 1024)
            except Exception:
                try:
                    import resource
                    import sys
                    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                    # macOS da ru_maxrss baytlarda, Linux da kilobaytlarda
                    rss_mb = usage / (1024 * 1024) if sys.platform == "darwin" else usage / 1024
                except Exception:
                    pass

            if rss_mb > 0:
                if rss_mb > 400.0:
                    log.warning(f"⚠️ Xotira (RSS) kritik darajada yuqori: {rss_mb:.1f} MB (> 400 MB)! Render Free 512 MB xavfi.")
                else:
                    log.info(f"📊 Server xotirasi (RSS): {rss_mb:.1f} MB / 512 MB")
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.warning(f"RSS monitor xatolik: {e}")
            await asyncio.sleep(60)
