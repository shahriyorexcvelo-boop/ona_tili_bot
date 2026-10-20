"""
Common helper functions and utilities shared across bot and web handlers.
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import re
import time
import urllib.parse
from typing import Optional, Dict, Any, Tuple, List

from aiogram.enums import ParseMode
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, Message, FSInputFile
)
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

import test_db
from core import (
    UZB_TZ, format_uzb_time, TG_ICONS, tg_e,
    get_test_schedule_status,
    is_working_hours, get_night_message,
    ADMIN_ID, get_config_admin_ids, WEBAPP_URL, bot, log,
    make_webapp_button
)

BLACKLIST_NAME_WORDS = {
    "kimsan", "kimsanov", "kimsanova", "hechkim", "hechkimov", "hechkimova", "hech", "kim",
    "falonchi", "pismadonchi", "nomalum", "noma'lum", "noma’lum", "nomaʼlum", "kimdir", "birov",
    "test", "tester", "testov", "testova", "admin", "administrator", "moderator",
    "bot", "user", "foydalanuvchi", "anonim", "anonymous", "null", "undefined",
    "qwerty", "asdf", "asdfgh", "zxcv", "salom", "qalesan", "ism", "familiya",
    "yoq", "yo'q", "yo’q", "yoʼq", "bilmayman", "blabla", "bla", "hacker", "pro", "master",
    "super", "king", "killer", "qizaloq", "yigit", "patsan", "brat", "aka", "uka",
    "shohruh", "matematika", "susik", "susiksks", "ahshshs", "ahahaha", "hahaha", "xaxa",
    "nik", "login", "parol", "password"
}


def validate_fullname(fullname: str) -> tuple[bool, str]:
    """Ism va familiyani g'alati belgilar, buyruqlar, soxta nomlar va klaviatura terishlaridan tekshirish."""
    if not fullname or not isinstance(fullname, str):
        return False, "⚠️ Iltimos, Ism va Familiyangizni kiriting."
    
    name = fullname.strip()
    
    # 1. Bot buyruqlari tekshiruvi (/start, /help va h.k.)
    if name.startswith("/"):
        return False, "⚠️ Bot buyruqlari ism sifatida qabul qilinmaydi. Iltimos, haqiqiy Ism va Familiyangizni kiriting."
    
    # 2. Maxsus belgilar va raqamlar tekshiruvi (Faqat lotin/kirill harflari, bo'sh joy, defis, apostroflar)
    clean_chars = re.sub(r"[a-zA-Zа-яА-ЯёЁўқғҳЎҚҒҲ\s\-\'ʻʼ`’]", "", name)
    if clean_chars:
        return False, "⚠️ Ism-familiyada raqamlar yoki maxsus belgilar bo'lmasligi kerak. Faqat harflar bilan yozing."

    # 3. So'zlar soni (kamida 1 ta ism bo'lishi shart)
    words = [w for w in name.split() if w]
    if len(words) < 1:
        return False, "⚠️ Iltimos, Ism va Familiyangizni kiriting."
    if len(words) > 4:
        return False, "⚠️ Iltimos, faqat o'zingizning haqiqiy Ism va Familiyangizni kiriting (ortiqcha so'zlarsiz)."

    vowels = set("aeiouyаеёиоуыэюяў")

    for w in words:
        w_lower = w.lower().strip("'-`ʻʼ’")
        letters_only = re.sub(r"[^a-zA-Zа-яА-ЯёЁўқғҳЎҚҒҲ]", "", w_lower)
        
        # Har bir so'z kamida 2 ta harfdan iborat bo'lishi kerak
        if len(letters_only) < 2:
            return False, f"⚠️ Kiritilgan so'z juda qisqa: <b>{w}</b>. Haqiqiy ism va familiyangizni kiriting."
        
        # Qora ro'yxat (soxta, hazil yoki buyruq ma'nosidagi nomlar)
        if letters_only in BLACKLIST_NAME_WORDS:
            return False, f"⚠️ Hazil yoki soxta nomlar (<b>{w}</b>) qabul qilinmaydi! Iltimos, haqiqiy Ism va Familiyangizni kiriting."
        
        # 3 tadan ortiq ketma-ket bir xil harf (masalan: aaa, sss, zzz)
        if re.search(r"(.)\1\1", letters_only):
            return False, "⚠️ Ism-familiyada harflarni ketma-ket asossiz takrorlash mumkin emas. Haqiqiy ismingizni kiriting."
        
        # Qisqa takrorlanuvchi klaviatura bo'g'inlari (masalan: shshsh, sksks, ababab)
        if re.search(r"(.{2,3})\1\1", letters_only):
            return False, "⚠️ Tushunarsiz yoki soxta nom kiritildi. Iltimos, haqiqiy Ism va Familiyangizni kiriting."

        # Unli harf tekshiruvi (so'z 3 harfdan uzun bo'lsa va unli umuman bo'lmasa — klaviatura spam)
        if len(letters_only) >= 3 and not any(ch in vowels for ch in letters_only):
            return False, f"⚠️ Noto'g'ri so'z kiritildi: <b>{w}</b>. Iltimos, haqiqiy Ism va Familiyangizni kiriting."

        # Ketma-ket 5 ta undosh harf (o'zbek va rus tilida 5 ta undosh ketma-ket kelmaydi: Ahshshs, susiksks)
        consec_cons = 0
        for ch in letters_only:
            if ch not in vowels:
                consec_cons += 1
                if consec_cons >= 5:
                    return False, "⚠️ Tushunarsiz yoki xato yozilgan ism. Iltimos, haqiqiy Ism va Familiyangizni kiriting."
            else:
                consec_cons = 0

    return True, ""


def get_all_admin_ids() -> list:
    """Bosh admin va bazadagi barcha tayinlangan yordamchi adminlar ID ro'yxati"""
    admin_ids = set(get_config_admin_ids())
    try:
        for a in test_db.get_all_admins():
            if a.get("tg_id"):
                admin_ids.add(int(a["tg_id"]))
    except Exception as e:
        log.warning(f"Adminlar ro'yxatini olishda xatolik: {e}")
    return list(admin_ids)


async def check_access(message: Message) -> bool:
    """Foydalanuvchi admin tomonidan tasdiqlanganligini tekshiradi."""
    uid = message.from_user.id
    if test_db.is_admin(uid, ADMIN_ID):
        return True
    # Tun rejimi tekshiruvi (23:00 – 07:00)
    if not is_working_hours():
        await message.answer(get_night_message())
        return False
    u = test_db.get_user(uid)
    if not u:
        await message.answer("⚠️ Iltimos, avval /start buyrug'i orqali ro'yxatdan o'ting.")
        return False
    st = u.get("status", "approved")
    if st in ["blocked", "rejected"]:
        await message.answer(
            "⛔️ <b>Sizning botdan foydalanish huquqingiz to'xtatilgan yoki rad etilgan!</b>\n\n"
            "Murojaat uchun: @eshmbetov"
        )
        return False
    return True


async def send_test_card_to_user_chat(user_tg_id: int, test: Dict[str, Any]):
    """Test ma'lumotlari, PDF va WebApp tugmasini foydalanuvchining chatiga yuboradi. Agar foydalanuvchi allaqachon topshirgan bo'lsa qayta topshirish taqiqlanadi."""
    existing_sub = test_db.get_user_submission_for_test(test["id"], user_tg_id)
    is_admin = test_db.is_admin(user_tg_id, ADMIN_ID)
    
    if existing_sub and not is_admin:
        dt = format_uzb_time(existing_sub["submitted_at"])
        is_published = test_db.is_test_results_published(test["id"])
        if is_published:
            grade = test_db.calculate_grade(existing_sub.get("score", 0))
            score_val = existing_sub.get("score", 0)
            corr_val = existing_sub.get("correct_count", 0)
            text = (
                f"{tg_e('warning', '⛔️')} <b>Siz ushbu testni topshirgansiz!</b>\n\n"
                f"{tg_e('order', '📖')} <b>Test:</b> {test['title']}\n"
                f"{tg_e('star_gold', '🎖')} <b>Milliy Sertifikat darajangiz:</b> <b>{grade}</b> ({score_val} ball)\n"
                f"{tg_e('success', '✅')} <b>To'g'ri javoblar:</b> {corr_val} ta\n"
                f"{tg_e('clock', '🕒')} <b>Topshirilgan vaqt:</b> {dt}\n\n"
                f"💡 <i>Test tahlilini ko'rish uchun quyidagi tugmani bosing:</i>"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📊 Test tahlili", callback_data=f"user_req_tahlil_{test['id']}")]
            ])
            try:
                await bot.send_message(chat_id=user_tg_id, text=text, reply_markup=kb)
            except Exception as e:
                log.warning(f"send_message error: {e}")
        else:
            text = (
                f"{tg_e('clock', '⏳')} <b>Siz ushbu testni topshirgansiz!</b>\n\n"
                f"{tg_e('order', '📖')} <b>Test:</b> {test['title']}\n"
                f"{tg_e('clock', '📌')} <b>Holat:</b> ⏳ <b>Javoblaringiz tekshirilmoqda...</b>\n"
                f"{tg_e('clock', '🕒')} <b>Topshirilgan vaqt:</b> {dt}\n\n"
                f"ℹ️ <i>Test hozirda davom etmoqda. Admin testni to'xtatib, Rasch tahlilini e'lon qilgandan so'ng, "
                f"to'g'ri javoblar soni, yakuniy ball va Milliy sertifikat darajangiz bot orqali shaxsiy xabar qilib yuboriladi!</i>"
            )
            try:
                await bot.send_message(chat_id=user_tg_id, text=text)
            except Exception as e:
                log.warning(f"send_message error: {e}")
        return

    sched_stat = get_test_schedule_status(test)
    if sched_stat["is_upcoming"] and not is_admin:
        sdate = test.get('scheduled_date') or 'Bugun'
        sstart = test.get('scheduled_start') or ''
        try:
            await bot.send_message(
                chat_id=user_tg_id,
                text=(
                    f"{tg_e('clock', '⏳')} <b>«{test['title']}» testi hali boshlanmagan!</b>\n\n"
                    f"{tg_e('clock', '📅')} Belgilangan sana: <b>{sdate}</b>\n"
                    f"{tg_e('clock', '⏰')} Boshlanish vaqti: <b>{sstart} (UZB)</b>\n\n"
                    f"<i>Test belgilangan vaqtda avtomatik boshlanadi va test kodi hamda savollar ochiladi. Ungacha kuting!</i>"
                )
            )
        except Exception as e:
            log.warning(f"send_message error: {e}")
        return

    if (test.get("is_active", 1) == 0 or sched_stat["is_closed"]) and not is_admin:
        try:
            await bot.send_message(
                chat_id=user_tg_id,
                text=f"{tg_e('warning', '⛔️')} <b>«{test['title']}» testi to'xtatilgan!</b>\nAdmin tomonidan javoblar qabul qilish yopilgan."
            )
        except Exception as e:
            log.warning(f"send_message error: {e}")
        return

    params = {
        "test_id": test["id"],
        "test_code": test["test_code"],
        "title": test["title"],
        "subject": test.get("subject", "Ona tili"),
        "tg_id": user_tg_id
    }
    encoded_url = f"{WEBAPP_URL}?{urllib.parse.urlencode(params)}"

    inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_webapp_button("📝 Javoblarni topshirish (Mini App)", encoded_url, fallback_cb=f"solve_test_{test['id']}")]
    ])

    time_info = f"{tg_e('clock', '⏱')} <b>Vaqt chegarasi:</b> {test['time_limit_min']} daqiqa\n" if test.get("time_limit_min", 0) > 0 else ""

    caption = (
        f"{tg_e('order', '📖')} <b>{test['title']}</b>\n"
        f"{tg_e('star_premium', '📌')} <b>Fan:</b> {test.get('subject', 'Ona tili')}\n"
        f"{tg_e('chat_check', '❓')} <b>Savollar:</b> 55 ta (1-32 ABCD, 33-35 ABCDEF, 36a-45b Yozma)\n"
        f"{time_info}"
        f"{tg_e('warning', '⚠️')} <i>Eslatma: Testni faqat 1 marta topshirish mumkin! Javoblaringizni belgilab bo'lgach, «Testni yakunlash» tugmasini bosing.</i>"
    )

    sent = False
    if test.get("pdf_file_id"):
        try:
            await bot.send_document(
                chat_id=user_tg_id,
                document=test["pdf_file_id"],
                caption=caption,
                reply_markup=inline_kb
            )
            sent = True
        except Exception as e:
            log.warning(f"PDF yuborishda xatolik: {e}")

    if not sent:
        try:
            await bot.send_message(chat_id=user_tg_id, text=caption, reply_markup=inline_kb)
        except Exception as e:
            log.error(f"Xabar yuborishda xatolik: {e}")


async def send_test_card(target_message: Message, test: Dict[str, Any], user_tg_id: int):
    await send_test_card_to_user_chat(user_tg_id, test)


async def clean_blocked_users_from_db(initiator_id: int = ADMIN_ID) -> dict:
    """
    Bazada mavjud barcha foydalanuvchilarni tekshirib, botni bloklaganlarni bazadan to'liq o'chirib tashlash.
    """
    already_blocked = test_db.delete_all_blocked_users()
    deleted_users = []
    for bu in already_blocked:
        deleted_users.append({
            "id": bu.get("id"),
            "tg_id": bu.get("tg_id"),
            "fullname": bu.get("fullname") or "Noma'lum",
            "username": f"@{bu.get('username')}" if bu.get("username") else "—",
            "tests_count": bu.get("tests_count", 0),
            "reason": "Oldindan bloklangan holatda"
        })

    users = test_db.get_all_users()
    admin_ids = set(get_all_admin_ids())

    active_users = []

    for u in users:
        uid = u.get("tg_id")
        if not uid or uid <= 0 or uid in admin_ids:
            continue
        try:
            await bot.send_chat_action(chat_id=uid, action="typing")
            active_users.append(u)
        except (TelegramForbiddenError, TelegramBadRequest) as ex:
            err_text = str(ex).lower()
            if "blocked" in err_text or "deactivated" in err_text or "chat not found" in err_text:
                test_db.delete_user(uid)
                deleted_users.append({
                    "id": u.get("id"),
                    "tg_id": uid,
                    "fullname": u.get("fullname") or "Noma'lum",
                    "username": f"@{u.get('username')}" if u.get("username") else "—",
                    "tests_count": u.get("tests_count", 0),
                    "reason": err_text[:60]
                })
                log.info(f"🗑 Bloklagan foydalanuvchi {uid} ({u.get('fullname')}) bazadan o'chirildi")
            else:
                active_users.append(u)
        except Exception as e:
            active_users.append(u)
        await asyncio.sleep(0.04)

    return {
        "success": True,
        "total_users": len(users),
        "active_users": len(active_users),
        "deleted_count": len(deleted_users),
        "deleted_users": deleted_users
    }


def get_inactive_target_users(target_count: str = "both", time_filter: str = "min_2d") -> list:
    """
    Shartlar bo'yicha nishondagi foydalanuvchilar ro'yxatini hisoblash.
    target_count: "0", "1", "both"
    time_filter: "min_2d" (2+ kun oldin), "last_2d" (oxirgi 2 kun ichida), "all" (barchasi)
    """
    users = test_db.get_all_users()
    admin_ids = set(get_all_admin_ids())
    now_ts = int(time.time())
    two_days_secs = 2 * 86400

    target_users = []
    for u in users:
        uid = u.get("tg_id")
        if not uid or uid <= 0 or uid in admin_ids:
            continue
        if u.get("status") != "approved":
            continue

        t_count = u.get("tests_count", 0)
        if target_count == "0" and t_count != 0:
            continue
        elif target_count == "1" and t_count != 1:
            continue
        elif target_count == "both" and t_count not in (0, 1):
            continue

        reg_at = u.get("registered_at") or 0
        if time_filter == "min_2d":
            if reg_at > (now_ts - two_days_secs):
                continue
        elif time_filter == "last_2d":
            if reg_at < (now_ts - two_days_secs):
                continue

        target_users.append(u)

    return target_users


async def send_inactive_warning_messages(
    initiator_id: int = ADMIN_ID,
    target_count: str = "both",
    time_filter: str = "min_2d"
) -> dict:
    """
    Tanlangan parametrlar bo'yicha foydalanuvchilarga formal ogohlantirish xabari yuborish.
    """
    target_users = get_inactive_target_users(target_count=target_count, time_filter=time_filter)

    sent_users = []
    failed_users = []
    deleted_blocked_users = []

    for u in target_users:
        uid = u.get("tg_id")
        fname = u.get("fullname") or "Foydalanuvchi"
        uname = f"@{u.get('username')}" if u.get("username") else "—"
        t_count = u.get("tests_count", 0)

        if t_count == 0:
            text = (
                "⚠️ <b>RASMIY OGOHLANTIRISH</b>\n\n"
                f"Hurmatli <b>{fname}</b>!\n\n"
                "Siz tizimimizda ro'yxatdan o'tgan bo'lsangiz-da, shu kunga qadar <b>birorta ham test ishlamadingiz</b> "
                "va sizga berilgan bepul imkoniyatdan foydalanmadingiz.\n\n"
                "📌 <b>Muhim eslatma:</b> Bugungi bo'lib o'tadigan testda ham qatnashmasangiz, faoliyatsizligingiz sababli sizni "
                "<b>botdan va tizimdan chiqarib yuborishga</b> majbur bo'lamiz.\n\n"
                "<i>O'z o'rningizni saqlab qolish va bilimingizni sinash uchun bugungi testda albatta ishtirok eting!</i>"
            )
        else:
            text = (
                "⚠️ <b>RASMIY OGOHLANTIRISH</b>\n\n"
                f"Hurmatli <b>{fname}</b>!\n\n"
                "Siz shu kunga qadar faqat <b>1 ta test</b> ishladingiz va sizga taqdim etilgan bepul imkoniyatlardan "
                "to'liq foydalanmadingiz.\n\n"
                "📌 <b>Muhim eslatma:</b> Bugungi bo'lib o'tadigan testda ham qatnashmasangiz, faoliyatsizligingiz sababli sizni "
                "<b>botdan va tizimdan chiqarib yuborishga</b> majbur bo'lamiz.\n\n"
                "<i>O'z o'rningizni saqlab qolish va natijalaringizni oshirish uchun bugungi testda albatta ishtirok eting!</i>"
            )

        try:
            await bot.send_message(chat_id=uid, text=text, parse_mode=ParseMode.HTML)
            sent_users.append({
                "id": u.get("id"),
                "tg_id": uid,
                "fullname": fname,
                "username": uname,
                "tests_count": t_count,
                "status": "sent"
            })
            await asyncio.sleep(0.04)
        except Exception as e:
            err_text = str(e).lower()
            if "blocked" in err_text or "forbidden" in err_text or "deactivated" in err_text:
                test_db.delete_user(uid)
                deleted_blocked_users.append(u)
            failed_users.append({
                "id": u.get("id"),
                "tg_id": uid,
                "fullname": fname,
                "username": uname,
                "tests_count": t_count,
                "error": str(e),
                "status": "failed"
            })
            log.warning(f"Ogohlantirish yuborishda xatolik user {uid}: {e}")

    try:
        z_count = len([u for u in target_users if u.get('tests_count') == 0])
        o_count = len([u for u in target_users if u.get('tests_count') == 1])
        report_text = (
            "📊 <b>FAOLIYATSIZ FOYDALANUVCHILARGA OGOHLANTIRISH YUBORILDI</b>\n\n"
            f"🎯 <b>Jami rejalashtirilgan:</b> {len(target_users)} nafar\n"
            f"✅ <b>Yetkazildi:</b> {len(sent_users)} nafar\n"
            f"⚠️ <b>Yetkazilmadi:</b> {len(failed_users)} nafar\n"
        )
        if deleted_blocked_users:
            report_text += f"🗑 <b>Botni bloklagani uchun bazadan o'chirildi:</b> {len(deleted_blocked_users)} nafar\n"
        report_text += (
            f"\n• 0 ta test ishlaganlar: {z_count} nafar\n"
            f"• 1 ta test ishlaganlar: {o_count} nafar"
        )
        await bot.send_message(chat_id=ADMIN_ID, text=report_text)
    except Exception as e:
        log.error(f"Admin hisobotini yuborishda xato: {e}")

    return {
        "success": True,
        "total_targets": len(target_users),
        "sent_count": len(sent_users),
        "fail_count": len(failed_users),
        "deleted_count": len(deleted_blocked_users),
        "deleted_users": deleted_blocked_users,
        "zero_tests_count": len([u for u in target_users if u.get("tests_count") == 0]),
        "one_test_count": len([u for u in target_users if u.get("tests_count") == 1]),
        "sent_users": sent_users,
        "failed_users": failed_users
    }


def get_warn_inactive_step1_kb():
    text = (
        "⚠️ <b>FAOLIYATSIZ FOYDALANUVCHILARGA OGOHLANTIRISH</b>\n\n"
        "Qaysi toifadagi foydalanuvchilarga ogohlantirish yubormoqchisiz?\n\n"
        "0️⃣ <b>Faqat 0 ta test ishlaganlar:</b> Ro'yxatdan o'tib, umuman test topshirmaganlar\n"
        "1️⃣ <b>Faqat 1 ta test ishlaganlar:</b> Shu kungacha faqat bitta testda qatnashganlar\n"
        "🔢 <b>Ikkalasi ham (0 va 1 ta):</b> Barcha sust/kam faol o'quvchilar"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="0️⃣ Faqat 0 ta ishlaganlar", callback_data="adm_warn_cnt_0")],
        [InlineKeyboardButton(text="1️⃣ Faqat 1 ta ishlaganlar", callback_data="adm_warn_cnt_1")],
        [InlineKeyboardButton(text="🔢 Ikkalasi ham (0 va 1 ta)", callback_data="adm_warn_cnt_both")],
        [InlineKeyboardButton(text="🔙 Admin Menyuga", callback_data="admin_back_to_menu")]
    ])
    return text, kb


def build_user_contact_card(user: Dict[str, Any]) -> Tuple[str, InlineKeyboardMarkup]:
    """Foydalanuvchi kartasi va profil chatiga olib o'tuvchi tugmalarni shakllantiradi."""
    tg_id = user.get("tg_id")
    fullname = user.get("fullname", "Noma'lum")
    raw_uname = (user.get("username") or "").strip()
    username = raw_uname.lstrip("@").strip()
    phone = user.get("phone", "—")
    status = user.get("status", "approved")
    tests_count = user.get("tests_count", 0)
    reg_ts = user.get("registered_at")
    reg_fmt = format_uzb_time(reg_ts, "%d.%m.%Y %H:%M") if reg_ts else "Noma'lum"

    last_test_ts = user.get("last_test_at")
    last_test_fmt = format_uzb_time(last_test_ts, "%d.%m.%Y %H:%M") if last_test_ts else "Topshirmagan"

    status_icon = "🟢 Faol" if status == "approved" else "🔴 Bloklangan"
    clean_phone = re.sub(r"[^\d+]", "", str(phone or ""))

    if username:
        uname_text = f"@{username}"
        profile_mention = (
            f"👉 <b>Telegram chat:</b> <a href=\"https://t.me/{username}\">@{username} shaxsiy chatini ochish ↗️</a>"
        )
    else:
        uname_text = "<i>Mavjud emas</i>"
        profile_mention = (
            f"👉 <b>Telegram chat (Telegramning o'zida ochish):</b>\n"
            f"👉 <a href=\"tg://user?id={tg_id}\"><b>{fullname} profil chatini ochish (ustiga bosing) 💬</b></a>"
        )

    card_text = (
        f"👤 <b>FOYDALANUVCHI MA'LUMOTLARI VA PROFIL CHATI</b>\n\n"
        f"🆔 <b>Telegram ID:</b> <code>{tg_id}</code>\n"
        f"👤 <b>F.I.SH:</b> <b>{fullname}</b>\n"
        f"🔗 <b>Username:</b> {uname_text}\n"
        f"📞 <b>Telefon:</b> <code>{phone}</code>\n"
        f"📊 <b>Topshirgan testlari:</b> <b>{tests_count} ta</b>\n"
        f"🕒 <b>Oxirgi faolligi:</b> {last_test_fmt}\n"
        f"📅 <b>Ro'yxatdan o'tgan:</b> {reg_fmt}\n"
        f"🔘 <b>Holati:</b> {status_icon}\n\n"
        f"💬 <b>Shaxsiy chatga o'tish:</b>\n"
        f"{profile_mention}\n\n"
        f"<i>Quyidagi tugmalar orqali Telegramning o'zida kontaktni ochishingiz yoki bot orqali xabar yuborishingiz mumkin 👇</i>"
    )

    buttons = []
    if username:
        buttons.append([InlineKeyboardButton(text="💬 Shaxsiy profil chatini ochish ↗️", url=f"https://t.me/{username}")])
    else:
        if clean_phone and clean_phone not in ["—", "-", ""]:
            buttons.append([InlineKeyboardButton(text="📇 Telegram kontakt kartasini ko'rish (Chat) ↗️", callback_data=f"adm_send_contact_{tg_id}")])

    buttons.append([InlineKeyboardButton(text="✉️ Bot orqali xabar yozish", callback_data=f"adm_msg_user_{tg_id}")])
    buttons.append([InlineKeyboardButton(text="🔍 Boshqa foydalanuvchi qidirish", callback_data="admin_contact_user_prompt")])
    buttons.append([InlineKeyboardButton(text="🔙 Admin panelga", callback_data="admin_back_to_menu")])

    return card_text, InlineKeyboardMarkup(inline_keyboard=buttons)


async def notify_admins_test_results_published(test_id: int, publisher_tg_id: int, eval_type: str = "rasch"):
    """
    Test natijalari e'lon qilinganda:
    - Barcha adminlarga xabar va natijalar PDF fayli yuboriladi.
    """
    try:
        test = await asyncio.to_thread(test_db.get_test_by_id, test_id)
        if not test:
            return

        publisher_tg_id = int(publisher_tg_id or 0)
        if publisher_tg_id == ADMIN_ID:
            publisher_name = "Shaxriyor (Bosh Admin)"
        else:
            u = await asyncio.to_thread(test_db.get_user, publisher_tg_id)
            publisher_name = u.get("fullname", "Admin") if u else f"Admin (ID: {publisher_tg_id})"

        subs = await asyncio.to_thread(test_db.get_test_results_leaderboard, test_id)
        subs_count = len(subs)

        eval_title = "Rasch modeli (JMLE)" if eval_type == "rasch" else "Standart ballar"
        caption = (
            f"📢 <b>«{test['title']}»</b> (<code>#{test.get('test_code', '')}</code>) test natijalari "
            f"<b>{publisher_name}</b> tomonidan e'lon qilindi!\n\n"
            f"🧮 <b>Baholash usuli:</b> {eval_title}\n"
            f"👥 <b>Ishtirokchilar soni:</b> {subs_count} nafar\n"
            f"🕒 <b>E'lon vaqti:</b> {format_uzb_time()}\n\n"
            f"📑 <i>To'liq natijalar reyting jadvali (PDF) quyida ilova qilindi:</i>"
        )

        pdf_path = await asyncio.to_thread(test_db.generate_test_results_pdf, test_id)

        all_admins = await asyncio.to_thread(test_db.get_all_admins)
        admin_ids = set()
        admin_ids.add(ADMIN_ID)
        for a in (all_admins or []):
            if a.get("tg_id"):
                admin_ids.add(int(a["tg_id"]))

        for aid in admin_ids:
            try:
                if pdf_path and os.path.exists(pdf_path):
                    await bot.send_document(
                        chat_id=aid,
                        document=FSInputFile(pdf_path),
                        caption=caption
                    )
                else:
                    await bot.send_message(
                        chat_id=aid,
                        text=caption
                    )
            except Exception as e_send:
                log.warning(f"Adminga ({aid}) test natijalari va PDF yuborishda xatolik: {e_send}")

        if pdf_path and os.path.exists(pdf_path):
            try:
                os.remove(pdf_path)
            except Exception:
                pass
    except Exception as e:
        log.error(f"notify_admins_test_results_published error: {e}", exc_info=True)


def build_detailed_tahlil_text(sub: dict, test: dict) -> str:
    """O'quvchi uchun to'g'ri va noto'g'ri ishlangan savollar tahlili matni."""
    name = sub.get("fullname") or "O'quvchi"
    score = sub.get("score", 0.0)
    grade = sub.get("grade") or test_db.calculate_grade(score)
    corr = sub.get("correct_count", 0)
    total = sub.get("total_count", 55) or 55
    incorr = max(0, total - corr)
    code = sub.get("test_code", test.get("test_code", ""))
    test_title = test.get("title", "Ona tili Testi")
    yt_url = (test.get("youtube_url") or "").strip()

    details = {}
    raw_details = sub.get("details_json")
    if isinstance(raw_details, dict):
        details = raw_details
    elif raw_details:
        try:
            details = json.loads(raw_details)
        except Exception:
            details = {}

    def sort_key(k):
        m = re.match(r"^(\d+)([ab]?)$", str(k).strip())
        if m:
            return (int(m.group(1)), m.group(2))
        return (999, str(k))

    correct_keys = []
    partial_keys = []
    incorrect_keys = []
    unanswered_keys = []
    for k in sorted(details.keys(), key=sort_key):
        item = details[k]
        st = item.get("status")
        if st == "correct":
            correct_keys.append(str(k))
        elif st == "partial":
            partial_keys.append(str(k))
        elif st == "unanswered":
            unanswered_keys.append(str(k))
        else:
            incorrect_keys.append(str(k))

    corr_str = ", ".join(correct_keys) if correct_keys else "Mavjud emas"
    partial_str = ", ".join(partial_keys) if partial_keys else ""
    incorr_str = ", ".join(incorrect_keys) if incorrect_keys else "Yo'q"

    text = (
        f"📊 <b>SAVOLLAR TAHLILI — «{test_title}»</b> (<code>#{code}</code>)\n\n"
        f"👤 <b>O'quvchi:</b> {name}\n"
        f"🎖 <b>Milliy Sertifikat darajasi:</b> <b>{grade}</b> ({score} ball)\n"
        f"✅ <b>To'g'ri ishlangan:</b> {corr} / {total} ta band\n"
    )
    if partial_keys:
        text += f"⚠️ <b>Qisman to'g'ri (30% ball):</b> {len(partial_keys)} ta band\n"
    text += (
        f"❌ <b>Noto'g'ri / qoldirilgan:</b> {incorr} ta\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ <b>TO'G'RI ISHLANGAN SAVOLLAR ({len(correct_keys)} ta):</b>\n"
        f"<code>{corr_str}</code>\n"
    )
    if partial_keys:
        text += (
            f"\n⚠️ <b>QISMAN TO'G'RI (30% ball berilgan, oxirgacha hisoblanmagan) ({len(partial_keys)} ta):</b>\n"
            f"<code>{partial_str}</code>\n"
        )
    text += (
        f"\n❌ <b>NOTO'G'RI ISHLANGAN SAVOLLAR ({len(incorrect_keys)} ta):</b>\n"
        f"<code>{incorr_str}</code>"
    )
    if unanswered_keys:
        unans_str = ", ".join(unanswered_keys)
        text += f"\n\n⚪ <b>BELGILANMAGAN SAVOLLAR ({len(unanswered_keys)} ta):</b>\n<code>{unans_str}</code>"
    text += "\n━━━━━━━━━━━━━━━━━━━━"
    if yt_url:
        text += f"\n\n🎬 <b>Video tahlil:</b> <a href=\"{yt_url}\">YouTube orqali ko'rish</a>"
    return text

