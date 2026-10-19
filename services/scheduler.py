import asyncio
import re
from datetime import datetime

import test_db
from core.config import UZB_TZ, ADMIN_ID, log, is_working_hours
from core.bot import bot
from .broadcast import send_broadcast_to_users

_schedule_trigger_event = asyncio.Event()

def notify_schedule_changed():
    """Yangi test qo'shilganda yoki jadval yangilanganda chaqiriladi (kutishni darhol uyg'otish)."""
    try:
        _schedule_trigger_event.set()
    except Exception:
        pass

# test_db dagi callback ro'yxatiga ulaymiz
try:
    test_db.register_schedule_changed_callback(notify_schedule_changed)
except Exception:
    pass

async def schedule_checker():
    """
    Aqlli va energiya/kvota tejovchi test jadvali tekshiruvchisi.
    - Test bo'lmaganda yoki yaqin vaqt oralig'i bo'lmaganda bazani tinimsiz so'rovlar bilan bezovta qilmaydi.
    - Tun rejimida (23:00–07:00) faol test bo'lmasa, bazaga 30 daqiqagacha so'rov yubormaydi.
    - Test boshlanishiga 35 daqiqa qolganida yoki test ketayotgan paytda aniqlik uchun har 60 soniyada tekshiradi.
    - Yangi test jadvali kiritilganda notify_schedule_changed() orqali darhol uyg'onadi.
    """
    log.info("⏰ Aqlli Schedule Checker ishga tushdi")
    while True:
        sleep_interval = 60
        try:
            now_uzb = datetime.now(UZB_TZ)
            today_iso = now_uzb.strftime("%Y-%m-%d")
            today_dot = now_uzb.strftime("%d.%m.%Y")
            now_minutes = now_uzb.hour * 60 + now_uzb.minute

            # Tun rejimi tekshiruvi: 23:00 dan 07:00 gacha
            working_hours = is_working_hours()

            tests = test_db.get_scheduled_tests()
            
            # 1. Agar birorta ham rejalashtirilgan test bo'lmasa:
            if not tests:
                # 15 daqiqa (yoki tunda 30 daqiqa) kutamiz, yangi test kiritilsa darhol uyg'onadi
                sleep_interval = 1800 if not working_hours else 900
            else:
                has_today_test = False
                min_wait_minutes = 9999
                has_active_running_test = False

                for t in tests:
                    sdate = str(t.get('scheduled_date') or '').strip()
                    sstart = str(t.get('scheduled_start') or '').strip()
                    send = str(t.get('scheduled_end') or '').strip()
                    test_id = t['id']
                    test_code = t.get('test_code') or str(test_id)
                    code_display = f"#{test_code}" if not str(test_code).startswith("#") else str(test_code)
                    test_title = t.get('title') or "Ona tili Testi"
                    is_active = (t.get('is_active', 1) == 1)
                    notified = str(t.get('auto_notified') or '')

                    if not sdate or not sstart or not send:
                        continue

                    # Sana tekshiruvi (YYYY-MM-DD yoki DD.MM.YYYY)
                    date_match = (sdate == today_iso or sdate == today_dot)
                    if not date_match:
                        if "-" in sdate:
                            parts = sdate.split("-")
                            if len(parts) == 3 and len(parts[0]) == 4:
                                date_match = (f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}" == today_iso)
                        elif "." in sdate:
                            parts = sdate.split(".")
                            if len(parts) == 3 and len(parts[2]) == 4:
                                date_match = (f"{int(parts[2]):04d}-{int(parts[1]):02d}-{int(parts[0]):02d}" == today_iso)

                    if not date_match:
                        continue

                    has_today_test = True

                    try:
                        sh, sm = map(int, sstart.split(":"))
                        eh, em = map(int, send.split(":"))
                        start_minutes = sh * 60 + sm
                        end_minutes = eh * 60 + em
                    except Exception:
                        continue

                    # 1. ⏳ 30 daqiqa qoldi ogohlantirishi (start - 30 daqiqadan start - 10 daqiqagacha)
                    if (start_minutes - 30) <= now_minutes < (start_minutes - 10) and "30m" not in notified:
                        test_db.mark_test_auto_notified(test_id, "30m")
                        clean_title = re.sub(r'\s*#[\w\d]+\s*$', '', test_title).strip() or test_title
                        msg_30m = (
                            "⏳ <b>Diqqat! Test boshlanishiga 30 daqiqa qoldi!</b>\n\n"
                            "Internet aloqangizni tekshirib, qoralama qog'ozlarni tayyorlab oling.\n\n"
                            f"📖 <b>Test:</b> {clean_title}\n"
                            f"🕐 <b>Boshlanish vaqti:</b> {sstart} (UZB)"
                        )
                        await send_broadcast_to_users(message_text=msg_30m)
                        log.info(f"⏰ Test #{test_id} uchun 30 daqiqa qoldi xabari tarqatildi")

                    # 2. ⚠️ 10 daqiqa qoldi ogohlantirishi (start - 10 daqiqadan start gacha)
                    if (start_minutes - 10) <= now_minutes < start_minutes and "10m" not in notified:
                        test_db.mark_test_auto_notified(test_id, "10m")
                        clean_title = re.sub(r'\s*#[\w\d]+\s*$', '', test_title).strip() or test_title
                        msg_10m = (
                            "⚠️ <b>Test boshlanishiga 10 daqiqa qoldi!</b>\n\n"
                            "Mini ilovaga kirib, tayyor bo'lib turing.\n\n"
                            f"📖 <b>Test:</b> {clean_title}\n"
                            f"🕐 <b>Boshlanish vaqti:</b> {sstart} (UZB)"
                        )
                        await send_broadcast_to_users(message_text=msg_10m)
                        log.info(f"⏰ Test #{test_id} uchun 10 daqiqa qoldi xabari tarqatildi")

                    # 3. 🚀 Boshlanish vaqti keldi (start dan end gacha)
                    if start_minutes <= now_minutes < end_minutes:
                        has_active_running_test = True
                        if not is_active:
                            test_db.set_test_active_status(test_id, 1)
                            is_active = True
                            log.info(f"⏰ Test #{test_id} avtomatik faollashtirildi ({sstart})")

                        if "started" not in notified:
                            test_db.mark_test_auto_notified(test_id, "started")
                            msg_started = (
                                "🚀 <b>Test boshlandi! Barchaga omad tilaymiz.</b>\n"
                                "Belgilangan vaqt ichida javoblarni topshirishni unutmang.\n\n"
                                f"📖 <b>Test nomi:</b> {test_title}\n"
                                f"📌 <b>Test kodi:</b> <code>{code_display}</code>\n"
                                f"⏰ <b>Test vaqti:</b> {sstart} – {send} (UZB)\n\n"
                                "<i>Mini ilovaga kirib, test topshirishingiz mumkin 👇</i>"
                            )
                            sent, fail, _ = await send_broadcast_to_users(message_text=msg_started)
                            log.info(f"⏰ Test #{test_id} boshlanganlik xabari {sent} nafar o'quvchiga avtomat tarqatildi")
                            try:
                                await bot.send_message(
                                    chat_id=ADMIN_ID,
                                    text=(
                                        f"⏰ <b>Avtomatik: Test boshlandi va o'quvchilarga e'lon qilindi!</b>\n\n"
                                        f"📖 <b>{test_title}</b>\n"
                                        f"📌 Test kodi: <code>{code_display}</code>\n"
                                        f"🕐 Vaqti: <b>{sstart}–{send}</b> (UZB)\n"
                                        f"📨 <b>O'quvchilarga tarqatildi:</b> {sent} ta\n\n"
                                        f"✅ Test faol — o'quvchilar javob topshirishi mumkin."
                                    )
                                )
                            except Exception:
                                pass

                    # 4. ⏰ 15 daqiqa qoldi ogohlantirishi
                    if (end_minutes - 15) <= now_minutes < end_minutes and "15m" not in notified and "started" in notified:
                        test_db.mark_test_auto_notified(test_id, "15m")
                        msg_15m = (
                            "⏰ <b>Diqqat, test yakunlanishiga 15 daqiqa qoldi!</b>\n\n"
                            "Qolgan javoblarni tekshirib, topshirishga shoshiling.\n\n"
                            f"📖 <b>Test:</b> {test_title}\n"
                            f"📌 <b>Test kodi:</b> <code>{code_display}</code>\n"
                            f"🕕 <b>Tugash vaqti:</b> {send} (UZB)"
                        )
                        await send_broadcast_to_users(message_text=msg_15m)
                        log.info(f"⏰ Test #{test_id} yakunlanishiga 15 daqiqa qolganligi xabari tarqatildi")

                    # 5. 🔴 Tugash vaqti keldi va test hali faol bo'lsa
                    if now_minutes >= end_minutes and is_active:
                        test_db.set_test_active_status(test_id, 0)
                        test_db.clear_test_schedule(test_id)
                        log.info(f"⏰ Test #{test_id} avtomatik to'xtatildi ({send})")

                        msg_ended = (
                            "🛑 <b>Test yakunlandi!</b>\n\n"
                            "Javoblarni qabul qilish to'xtatildi. Ishtirok etgan barcha o'quvchilarga minnatdorchilik bildiramiz.\n\n"
                            f"📖 <b>Test:</b> {test_title}\n"
                            f"📌 <b>Test kodi:</b> <code>{code_display}</code>\n"
                            f"🕕 <b>Tugash vaqti:</b> {send} (UZB)\n\n"
                            "📊 <i>Tez orada to'liq tahlil va rasmiy natijalar e'lon qilinadi! Mini ilovaga kirib yangiliklarni kuzatib boring.</i>"
                        )
                        sent, fail, _ = await send_broadcast_to_users(message_text=msg_ended)
                        log.info(f"⏰ Test #{test_id} yakunlanganlik xabari {sent} nafar o'quvchiga tarqatildi")

                        try:
                            await bot.send_message(
                                chat_id=ADMIN_ID,
                                text=(
                                    f"⏰ <b>Avtomatik: Test yakunlandi!</b>\n\n"
                                    f"📖 <b>{test_title}</b>\n"
                                    f"📌 Test kodi: <code>{code_display}</code>\n"
                                    f"🕕 Tugash vaqti: <b>{send}</b>\n\n"
                                    f"🛑 Test to'xtatildi va barcha o'quvchilarga yakunlanganlik xabari tarqatildi ({sent} ta).\n"
                                    f"📊 Natijalarni hisoblash va e'lon qilish uchun: <b>/admin</b> → Testlar"
                                )
                            )
                        except Exception:
                            pass

                    # Keyingi eng yaqin voqea vaqtini hisoblash (dinamik uyqu)
                    if now_minutes < start_minutes:
                        diff = (start_minutes - 30) - now_minutes
                        if diff > 0 and diff < min_wait_minutes:
                            min_wait_minutes = diff
                    elif now_minutes < end_minutes:
                        has_active_running_test = True

                # Agar bugun uchun test bo'lmasa:
                if not has_today_test:
                    sleep_interval = 1800 if not working_hours else 900
                elif has_active_running_test:
                    sleep_interval = 60
                elif min_wait_minutes < 9999:
                    if min_wait_minutes > 5:
                        # 30 daqiqa qoldi ogohlantirishigacha hali ancha bor — 5-10 daqiqa uxlaymiz
                        sleep_interval = min(600, (min_wait_minutes - 2) * 60)
                    else:
                        sleep_interval = 60
                else:
                    sleep_interval = 60

        except Exception as e:
            log.error(f"Schedule checker xatosi: {e}")
            sleep_interval = 60

        # Uyqu: belgilangan sleep_interval kutadi, lekin jadval o'zgarsa DARHOL uyg'onadi
        try:
            _schedule_trigger_event.clear()
            await asyncio.wait_for(_schedule_trigger_event.wait(), timeout=sleep_interval)
            log.info("⏰ Jadval o'zgarishi signali qabul qilindi, darhol qayta tekshirilmoqda...")
        except asyncio.TimeoutError:
            pass
