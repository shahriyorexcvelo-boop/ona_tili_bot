"""
User-facing Telegram bot handlers and routers.
"""
from __future__ import annotations
import asyncio
import html
import json
import logging
import os
import re
import time
import urllib.parse
from typing import Optional, Dict, Any, Tuple, List

from aiogram import Router, F
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.filters.chat_member_updated import ChatMemberUpdatedFilter, KICKED, MEMBER, JOIN_TRANSITION
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove,
    WebAppInfo, FSInputFile, ChatMemberUpdated
)

import test_db
from core import (
    UZB_TZ, format_uzb_time, TG_ICONS, tg_e,
    BOT_TOKEN, ADMIN_ID, get_config_admin_ids, WEBAPP_URL, get_webapp_url, log, bot,
    RegistrationState, SolveTestState, EditProfileState, StudentTahlilState,
    main_menu_kb, profile_webapp_kb, results_webapp_kb,
    contact_share_kb, make_webapp_button,
    is_working_hours, should_apply_night_mode, get_night_message,
    get_test_schedule_status,
    set_cached_membership
)
from .common import (
    get_all_admin_ids, notify_all_admins, check_access, validate_fullname, send_test_card, send_test_card_to_user_chat,
    build_detailed_tahlil_text, BLACKLIST_NAME_WORDS
)

user_router = Router(name="user_router")

# Yangi foydalanuvchilar qabuli yopilganda takroriy /start spamdan himoya (15 minut cheklov)
REG_COOLDOWN_SECONDS = 15 * 60  # 900 soniya (15 minut)
_REG_CLOSED_NOTIFIED: Dict[int, float] = {}

def is_registration_cooldown_active(user_tg_id: int) -> bool:
    """Yangi foydalanuvchiga oxirgi 15 minut ichida qabul yopiqligi haqida xabar yuborilganmi?"""
    now = time.time()
    last_time = _REG_CLOSED_NOTIFIED.get(user_tg_id, 0.0)
    return (now - last_time) < REG_COOLDOWN_SECONDS

def mark_registration_notified(user_tg_id: int):
    """Oxirgi ogohlantirish vaqtini saqlash va kesh hajmini nazorat qilish."""
    now = time.time()
    _REG_CLOSED_NOTIFIED[user_tg_id] = now
    if len(_REG_CLOSED_NOTIFIED) > 5000:
        cutoff = now - REG_COOLDOWN_SECONDS
        to_del = [uid for uid, t in _REG_CLOSED_NOTIFIED.items() if t < cutoff]
        for uid in to_del:
            _REG_CLOSED_NOTIFIED.pop(uid, None)

async def send_registration_closed_message(message: Message, user_tg_id: int):
    """Yangi foydalanuvchiga qabul yopiqligi haqida uzr so'rab xabar yuborish (15 daqiqada faqat 1 marta)"""
    if is_registration_cooldown_active(user_tg_id):
        # 15 minut ichida takroriy /start bosilganda bot qayta xabar yubormaydi (spamdan himoya)
        return
    mark_registration_notified(user_tg_id)
    await message.answer(
        "🙏 <b>Assalomu alaykum!</b>\n\n"
        "Keltirilgan noqulaylik uchun uzr so'raymiz, hozirda botimizga yangi o'quvchilarni <b>qabul qilish vaqtincha to'xtatilgan</b>.\n\n"
        "⏳ <b>Tez orada qabul yana ochiladi.</b> Iltimos, keyinroq qayta urinib ko'ring!\n\n"
        "<i>ℹ️ Serverni ortiqcha yuklamaslik uchun takroriy so'rovlar 15 daqiqaga cheklangan.</i>",
        reply_markup=ReplyKeyboardRemove()
    )


@user_router.message(CommandStart(), StateFilter("*"))
async def start_handler(message: Message, state: FSMContext):
    await state.clear()
    user_tg_id = message.from_user.id

    # Tun rejimi tekshiruvi: faol test bo'lmaganda va admin bo'lmaganda to'xtatadi
    if should_apply_night_mode(user_tg_id):
        await message.answer(get_night_message())
        return

    user = await test_db.db(test_db.get_user, user_tg_id)

    if not user:
        # Yangi ro'yxatdan o'tmoqchi bo'lgan foydalanuvchi
        if not (await test_db.db(test_db.is_admin, user_tg_id, ADMIN_ID)):
            if not (await test_db.db(test_db.is_registration_open)):
                await send_registration_closed_message(message, user_tg_id)
                return


        await state.set_state(RegistrationState.fullname)
        await message.answer(
            "👋 <b>Assalomu alaykum! Ona tili test tizimiga xush kelibsiz!</b>\n\n"
            "Tizimdan to'liq foydalanish uchun ro'yxatdan o'ting.\n\n"
            "✍️ <b>Iltimos, Ism va Familiyangizni kiriting:</b>\n"
            "<i>(Misol: Shaxriyor Eshimbetov)</i>",
            reply_markup=ReplyKeyboardRemove()
        )
    else:
        is_adm = await test_db.db(test_db.is_admin, user_tg_id, ADMIN_ID)
        st = user.get("status", "approved")
        if not is_adm:
            if st == "pending":
                can_req, diff_hours = await test_db.db(test_db.can_request_access, user_tg_id, cooldown_hours=6)
                if can_req:
                    await test_db.db(test_db.touch_admin_deleted_request, user_tg_id)
                    safe_fn = html.escape(user.get("fullname", "O'quvchi"))
                    safe_un = html.escape(f"@{user.get('username')}") if user.get("username") else "Mavjud emas"
                    alert_text = (
                        f"🔔 <b>Yangi kirish so'rovi (Ilgari o'chirilgan foydalanuvchi):</b>\n\n"
                        f"👤 <b>Foydalanuvchi:</b> {safe_fn}\n"
                        f"🔗 <b>Username:</b> {safe_un}\n"
                        f"🆔 <b>Telegram ID:</b> <code>{user_tg_id}</code>\n"
                        f"🕒 <b>Vaqt:</b> {format_uzb_time()}\n\n"
                        f"Ushbu foydalanuvchiga botdan foydalanishga ruxsat berasizmi?"
                    )
                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [
                            InlineKeyboardButton(text="✅ Ruxsat berish", callback_data=f"user_quick_approve_{user_tg_id}"),
                            InlineKeyboardButton(text="❌ Rad etish", callback_data=f"user_quick_reject_{user_tg_id}")
                        ]
                    ])
                    await notify_all_admins(message.bot, alert_text, reply_markup=kb)
                    await message.answer(
                        "⏳ <b>So'rovingiz adminga yuborildi, ruxsat berilishini kuting.</b>\n\n"
                        "Admin ruxsat bergandan so'ng botdan to'liq foydalanishingiz mumkin bo'ladi."
                    )
                else:
                    await message.answer(
                        "⏳ <b>Sizning so'rovingiz ko'rib chiqilmoqda...</b>\n\n"
                        "Iltimos, admin javobini kuting."
                    )
                return
            elif st == "rejected":
                await message.answer(
                    "⛔️ <b>Sizning botdan foydalanish so'rovingiz rad etilgan!</b>\n\n"
                    "Murojaat uchun: @eshmbetov"
                )
                return

        await message.answer(
            f"👋 <b>Xush kelibsiz, {user['fullname']}!</b>\n\n"
            f"{tg_e('order', '📋')} <b>Ona tili — Rasch Test Tizimi</b>\n\n"
            f"{tg_e('clock', '⏰')} Kerakli bo'limni tanlang yoki to'g'ridan-to'g'ri test kodini yuboring 👇",
            reply_markup=main_menu_kb(user_tg_id)
        )



# Ro'yxatdan o'tish: Ism kiritildi
@user_router.message(RegistrationState.fullname)
async def reg_fullname(message: Message, state: FSMContext):
    user_tg_id = message.from_user.id
    is_adm = await test_db.db(test_db.is_admin, user_tg_id, ADMIN_ID)

    if not is_adm and not (await test_db.db(test_db.is_registration_open)):
        await state.clear()
        await send_registration_closed_message(message, user_tg_id)
        return

    raw_fullname = (message.text or "").strip()
    
    # Qat'iy tekshiruv: g'alati nomlar, buyruqlar va klaviatura spamlarini rad etish
    is_valid, err_msg = validate_fullname(raw_fullname)
    if not is_valid:
        await message.answer(
            f"{err_msg}\n\n"
            f"✍️ <i>Masalan: Rustam Karimov yoki Dilnoza Rahimova</i>"
        )
        return

    # Chiroyli bosh harflar bilan formatlash
    fullname = " ".join(w.capitalize() for w in raw_fullname.split())

    user_tg_id = message.from_user.id
    username = message.from_user.username
    phone = ""

    is_adm = await test_db.db(test_db.is_admin, user_tg_id, ADMIN_ID)
    was_admin_deleted = await test_db.db(test_db.is_admin_deleted, user_tg_id)

    if is_adm:
        status = "approved"
    elif was_admin_deleted:
        status = "pending"
    else:
        status = "approved"

    await test_db.db(test_db.add_or_update_user, user_tg_id, fullname, phone, username, status=status)
    try:
        await test_db.db(test_db.log_activity, user_tg_id, "user_register", f"Yangi o'quvchi ro'yxatdan o'tdi: {fullname}", {"tg_id": user_tg_id, "fullname": fullname, "username": username, "status": status})
    except Exception:
        pass
    await state.clear()

    if is_adm:
        await message.answer(
            f"🎉 <b>Tabriklaymiz, {fullname}! Siz admin sifatida tizimga ulandingiz!</b>\n\n"
            f"Kerakli bo'limni tanlang yoki to'g'ridan-to'g'ri test kodini yuboring 👇",
            reply_markup=main_menu_kb(user_tg_id)
        )
    elif status == "pending":
        await test_db.db(test_db.touch_admin_deleted_request, user_tg_id)
        safe_fullname = html.escape(fullname)
        safe_username = html.escape(f"@{username}") if username else "Mavjud emas"
        alert_text = (
            f"🔔 <b>Yangi kirish so'rovi (Ilgari o'chirilgan foydalanuvchi):</b>\n\n"
            f"👤 <b>Foydalanuvchi:</b> {safe_fullname}\n"
            f"🔗 <b>Username:</b> {safe_username}\n"
            f"🆔 <b>Telegram ID:</b> <code>{user_tg_id}</code>\n"
            f"🕒 <b>Vaqt:</b> {format_uzb_time()}\n\n"
            f"Ushbu foydalanuvchiga botdan foydalanishga ruxsat berasizmi?"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Ruxsat berish", callback_data=f"user_quick_approve_{user_tg_id}"),
                InlineKeyboardButton(text="❌ Rad etish", callback_data=f"user_quick_reject_{user_tg_id}")
            ]
        ])
        await notify_all_admins(message.bot, alert_text, reply_markup=kb)

        await message.answer(
            "⏳ <b>So'rovingiz adminga yuborildi, ruxsat berilishini kuting.</b>\n\n"
            "Admin tasdiqlaganidan so'ng sizga xabar beriladi va botdan to'liq foydalanishingiz mumkin bo'ladi."
        )
    else:
        # Foydalanuvchiga to'g'ridan-to'g'ri tasdiqlanganini bildirish
        await message.answer(
            f"🎉 <b>Tabriklaymiz, {fullname}! Ro'yxatdan muvaffaqiyatli o'tdingiz.</b>\n\n"
            f"{tg_e('order', '📋')} <b>Ona tili — Rasch Test Tizimi</b>\n\n"
            f"Endi bemalol barcha testlarni yechishingiz va natijalaringizni bilib borishingiz mumkin!\n\n"
            f"Kerakli bo'limni tanlang yoki test kodini yuboring 👇",
            reply_markup=main_menu_kb(user_tg_id)
        )

        # Adminlarga (bosh admin va barcha tayinlanganlarga) yangi o'quvchi ro'yxatdan o'tgani haqida axborot
        safe_fullname = html.escape(fullname)
        safe_username = html.escape(f"@{username}") if username else "Mavjud emas"
        admin_notify_text = (
            f"👤 <b>Yangi o'quvchi ro'yxatdan o'tdi:</b>\n\n"
            f"👤 <b>Ism-familiya:</b> {safe_fullname}\n"
            f"🆔 <b>Telegram ID:</b> <code>{user_tg_id}</code>\n"
            f"🔗 <b>Username:</b> {safe_username}\n"
            f"🕒 <b>Vaqt:</b> {format_uzb_time()}"
        )
        await notify_all_admins(message.bot, admin_notify_text)

# Ixtiyoriy: Telefon yuborilgan holat uchun zaxira handler
@user_router.message(RegistrationState.phone)
async def reg_phone(message: Message, state: FSMContext):
    phone = ""
    if message.contact:
        phone = message.contact.phone_number
    elif message.text:
        phone = message.text.strip()

    data = await state.get_data()
    fullname = data.get("fullname", "Foydalanuvchi")
    user_tg_id = message.from_user.id
    username = message.from_user.username

    is_adm = await test_db.db(test_db.is_admin, user_tg_id, ADMIN_ID)
    was_admin_deleted = await test_db.db(test_db.is_admin_deleted, user_tg_id)
    if is_adm:
        status = "approved"
    elif was_admin_deleted:
        status = "pending"
    else:
        status = "approved"

    await test_db.db(test_db.add_or_update_user, user_tg_id, fullname, phone, username, status=status)
    try:
        await test_db.db(test_db.log_activity, user_tg_id, "user_register", f"Yangi o'quvchi ro'yxatdan o'tdi: {fullname}", {"tg_id": user_tg_id, "fullname": fullname, "username": username, "status": status})
    except Exception:
        pass
    await state.clear()

    # Barcha adminlarga xabar
    admin_notify_text = (
        f"👤 <b>Yangi o'quvchi ro'yxatdan o'tdi (telefon orqali):</b>\n\n"
        f"👤 <b>Ism-familiya:</b> {html.escape(fullname)}\n"
        f"📞 <b>Telefon:</b> <code>{html.escape(phone)}</code>\n"
        f"🆔 <b>Telegram ID:</b> <code>{user_tg_id}</code>\n"
        f"🔗 <b>Username:</b> {html.escape(f'@{username}') if username else 'Mavjud emas'}\n"
        f"🕒 <b>Vaqt:</b> {format_uzb_time()}"
    )
    await notify_all_admins(message.bot, admin_notify_text)

    await message.answer(
        f"🎉 <b>Tabriklaymiz, {fullname}! Siz muvaffaqiyatli ro'yxatdan o'tdingiz!</b>\n\n"
        f"Kerakli bo'limni tanlang yoki test topshirish uchun «🔢 Test kodini kiritish» tugmasini bosing 👇",
        reply_markup=main_menu_kb(user_tg_id)
    )

# 1. 🔢 Test kodini kiritish (Prompt)
@user_router.message(F.text == "🔢 Test kodini kiritish")
@user_router.message(Command("solve"))
async def enter_test_code_prompt(message: Message, state: FSMContext):
    if not await check_access(message):
        return
    await state.set_state(SolveTestState.test_code)
    await message.answer(
        "🔢 <b>Test kodini kiriting:</b>\n\n"
        "<i>(Masalan: <code>MAT-01</code> yoki <code>101</code>)</i>\n\n"
        "Bekor qilish uchun pastdagi menyudan foydalaning."
    )

# Test kodi kiritildi (FSM)
@user_router.message(SolveTestState.test_code)
async def process_solve_test_code(message: Message, state: FSMContext):
    if not await check_access(message):
        await state.clear()
        return
    text = (message.text or "").strip()
    if not text:
        return
    menu_cmds = [
        "🔢 Test kodini kiritish", "📊 Mening natijalarim", "👤 Profilim",
        "ℹ️ Yordam", "ℹ️ Bot haqida", "⚙️ Admin Panel",
        "➕ Yangi test yaratish", "📊 Test natijalari va reyting", "📋 Testlarni boshqarish"
    ]
    if text in menu_cmds:
        await state.clear()
        if text == "📊 Mening natijalarim":
            await show_my_results(message)
        elif text == "👤 Profilim":
            await show_profile(message)
        elif text in ["ℹ️ Yordam", "ℹ️ Bot haqida"]:
            await show_help(message)
        elif text == "⚙️ Admin Panel":
            from .admin import admin_panel_handler
            await admin_panel_handler(message)
        elif text == "➕ Yangi test yaratish":
            from .admin import admin_create_test_text_handler
            await admin_create_test_text_handler(message)
        elif text == "📊 Test natijalari va reyting":
            from .admin import admin_leaderboard_text_handler
            await admin_leaderboard_text_handler(message)
        elif text == "📋 Testlarni boshqarish":
            from .admin import admin_manage_tests_text_handler
            await admin_manage_tests_text_handler(message)
        elif text == "🔢 Test kodini kiritish":
            await enter_test_code_prompt(message, state)
        return

    code = text.upper().replace("#", "")
    test = test_db.get_test_by_code(code)
    await state.clear()

    if not test:
        await message.answer(
            f"❌ <b>«{text}» kodi bo'yicha test topilmadi!</b>\n\n"
            f"Iltimos, kodni to'g'ri kiritganingizni tekshiring.",
            reply_markup=main_menu_kb(message.from_user.id)
        )
        return

    await send_test_card(message, test, message.from_user.id)


@user_router.callback_query(F.data.startswith("solve_test_"))
async def solve_test_cb(call: CallbackQuery):
    test_id = int(call.data.split("_")[2])
    t = test_db.get_test_by_id(test_id)
    if not t:
        await call.answer("Test topilmadi!", show_alert=True)
        return

    sched_stat = get_test_schedule_status(t)
    if sched_stat["is_upcoming"] and not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        sstart = t.get('scheduled_start') or ''
        sdate = t.get('scheduled_date') or 'Bugun'
        await call.answer(f"⏳ Test hali boshlanmagan! Boshlanish vaqti: {sdate} {sstart} (UZB)", show_alert=True)
        return
    
    existing_sub = await test_db.db(test_db.get_user_submission_for_test, test_id, call.from_user.id)
    if existing_sub:
        is_published = await test_db.db(test_db.is_test_results_published, test_id)
        if is_published:
            grade = test_db.calculate_grade(existing_sub.get("score", 0))
            score_val = existing_sub.get("score", 0)
            await call.answer(f"⛔️ Siz bu testni topshirgansiz! Daraja: {grade} ({score_val} ball)", show_alert=True)
        else:
            await call.answer("⏳ Siz bu testni topshirgansiz! Javoblar tekshirilmoqda. Admin natijalarni e'lon qilgach, shaxsiy xabar yuboriladi.", show_alert=True)
        return

    params = {
        "test_id": t["id"],
        "test_code": t["test_code"],
        "title": t["title"],
        "subject": t.get("subject", "Ona tili")
    }
    encoded_url = f"{get_webapp_url()}?{urllib.parse.urlencode(params)}"
    reply_kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_webapp_button("📝 Testni boshlash (Mini App)", encoded_url)]
    ])
    await call.message.answer(
        f"📝 <b>{t['title']}</b> testini yechish uchun quyidagi tugmani bosing 👇",
        reply_markup=reply_kb
    )
    await call.answer()

@user_router.callback_query(F.data == "admin_webapp_info")
async def admin_webapp_info_cb(call: CallbackQuery):
    admin_webapp_url = f"{get_webapp_url()}/admin.html?tg_id={call.from_user.id}"
    reply_kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_webapp_button("📱 Admin Panelni ochish (Mini App)", admin_webapp_url)],
        [InlineKeyboardButton(text="🔙 Admin Menyuga qaytish", callback_data="admin_back_to_menu")]
    ])
    text = (
        f"📱 <b>Admin Mini App (Kalit va ballar kiritish):</b>\n\n"
        f"Ushbu paneldan 45 ta savolning kalitlari va har biriga alohida ballarni qulay belgilashingiz mumkin!\n\n"
        f"Ochish uchun quyidagi tugmani bosing 👇"
    )
    try:
        await call.message.edit_text(text, reply_markup=reply_kb)
    except Exception:
        await call.message.answer(text, reply_markup=reply_kb)
    await call.answer()

# 2. 📊 Mening natijalarim
@user_router.message(F.text == "📊 Mening natijalarim")
@user_router.message(Command("results"))
async def show_my_results(message: Message):
    subs = await test_db.db(test_db.get_user_submissions, message.from_user.id)
    pub_subs = [s for s in subs if s.get("results_published")]

    kb_rows = []
    if pub_subs:
        for s in pub_subs[:5]:
            t_title = s.get("test_title", "Test")
            t_code = s.get("test_code", "")
            btn_title = f"📊 #{t_code} tahlili" if t_code else f"📊 {t_title[:20]} tahlili"
            kb_rows.append([InlineKeyboardButton(text=btn_title, callback_data=f"user_req_tahlil_{s['test_id']}")])

    kb_rows.append([make_webapp_button("📱 Barcha natijalar va tahlillar (Mini App)", f"{get_webapp_url()}/app.html?tab=tests&tg_id={message.from_user.id}")])

    await message.answer(
        "📊 <b>Mening natijalarim va tahlillar</b>\n\n"
        "Quyidagi tugmalar orqali topshirgan testlaringiz tahlilini ko'rishingiz yoki Mini ilovani ochishingiz mumkin 👇",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
    )

# 3. 👤 Profil (Bot chatida ko'rish va tahrirlash)
@user_router.message(F.text == "👤 Profilim")
@user_router.message(Command("profile"))
async def show_profile(message: Message):
    user = await test_db.db(test_db.get_user, message.from_user.id)
    if not user:
        await message.answer("Profil topilmadi. /start buyrug'ini bosing.")
        return

    submissions = await test_db.db(test_db.get_user_submissions, message.from_user.id)
    tests_count = len(submissions)
    dt = format_uzb_time(user.get("registered_at"), "%d.%m.%Y") if user.get("registered_at") else "Noma'lum"

    scores = [float(s.get('score', s.get('correct_count', 0))) for s in submissions] if submissions else []
    avg_score = f"{sum(scores)/len(scores):.1f}" if scores else "0.0"
    max_score = f"{max(scores):.1f}" if scores else "0.0"

    st = (user.get("status") or "approved").lower()
    st_text = "✅ Faol o'quvchi" if st == "approved" else ("⏳ Kutilmoqda" if st == "pending" else "⛔️ Bloklangan")

    phone_str = user.get('phone') or "Biriktirilmagan"
    username_str = f"@{message.from_user.username}" if message.from_user.username else (f"@{user.get('username')}" if user.get('username') else "Mavjud emas")

    text = (
        f"{tg_e('star_gold', '⭐')} <b>SHAXSIY PROFILINGIZ</b>\n\n"
        f"{tg_e('user_outline', '👤')} <b>Foydalanuvchi:</b> {user['fullname']}\n"
        f"{tg_e('success', '✅')} <b>Holat:</b> {st_text}\n\n"
        f"{tg_e('order', '📋')} <b>KO'RSATKICHLAR:</b>\n"
        f"• Ishlangan testlar: <b>{tests_count} ta</b>\n"
        f"• O'rtacha natija: <b>{avg_score} ball</b>\n"
        f"• Eng yuqori natija: <b>{max_score} ball</b>\n\n"
        f"<i>Batafsil ma'lumotlarni ko'rish yoki tahrirlash uchun quyidagi tugmalardan birini tanlang 👇</i>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Shaxsiy ma'lumotlar", callback_data="profile_show_details")],
        [
            InlineKeyboardButton(text="✏️ Ismni o'zgartirish", callback_data="profile_edit_name"),
            InlineKeyboardButton(text="📞 Raqamni o'zgartirish", callback_data="profile_edit_phone")
        ],
        [make_webapp_button("📱 Shaxsiy profil (Mini App)", f"{get_webapp_url()}/app.html?tg_id={message.from_user.id}")],
        [InlineKeyboardButton(text="🗑 Akkauntni o'chirish", callback_data="profile_delete_account")]
    ])

    await message.answer(text, reply_markup=kb)

@user_router.callback_query(F.data == "profile_show_details")
async def profile_show_details_handler(call: CallbackQuery):
    user = await test_db.db(test_db.get_user, call.from_user.id)
    if not user:
        await call.answer("Foydalanuvchi topilmadi", show_alert=True)
        return
    submissions = await test_db.db(test_db.get_user_submissions, call.from_user.id)
    tests_count = len(submissions)
    dt = format_uzb_time(user.get("registered_at"), "%d.%m.%Y %H:%M") if user.get("registered_at") else "Noma'lum"
    phone_str = user.get('phone') or "Biriktirilmagan"
    username_str = f"@{call.from_user.username}" if call.from_user.username else (f"@{user.get('username')}" if user.get('username') else "Mavjud emas")
    st = (user.get("status") or "approved").lower()
    st_text = "✅ Faol o'quvchi" if st == "approved" else ("⏳ Kutilmoqda" if st == "pending" else "⛔️ Bloklangan")

    detail_text = (
        f"{tg_e('order', '📋')} <b>SHAXSIY AKKAUNT MA'LUMOTLARI</b>\n\n"
        f"{tg_e('user_outline', '👤')} <b>Ism va familiya:</b> {user['fullname']}\n"
        f"{tg_e('chat_check', '📱')} <b>Telefon raqam:</b> {phone_str}\n"
        f"{tg_e('key', '🆔')} <b>Telegram ID:</b> <code>{call.from_user.id}</code>\n"
        f"{tg_e('user_outline', '🔗')} <b>Username:</b> {username_str}\n"
        f"{tg_e('clock', '📅')} <b>Ro'yxatdan o'tgan:</b> {dt}\n"
        f"{tg_e('success', '🔰')} <b>Holat:</b> {st_text}\n"
        f"{tg_e('star_premium', '📝')} <b>Yechilgan testlar:</b> {tests_count} ta\n\n"
        f"<i>Ma'lumotlarni tahrirlashingiz yoki menyuni yopishingiz mumkin 👇</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▲ Ma'lumotlarni yashirish", callback_data="profile_hide_details")],
        [
            InlineKeyboardButton(text="✏️ Ismni o'zgartirish", callback_data="profile_edit_name"),
            InlineKeyboardButton(text="📞 Raqamni o'zgartirish", callback_data="profile_edit_phone")
        ],
        [make_webapp_button("📱 Shaxsiy profil (Mini App)", f"{get_webapp_url()}/app.html?tg_id={call.from_user.id}")],
        [InlineKeyboardButton(text="🗑 Akkauntni o'chirish", callback_data="profile_delete_account")]
    ])
    try:
        await call.message.edit_text(detail_text, reply_markup=kb)
    except Exception:
        pass
    await call.answer()

@user_router.callback_query(F.data == "profile_hide_details")
async def profile_hide_details_handler(call: CallbackQuery):
    user = await test_db.db(test_db.get_user, call.from_user.id)
    if not user:
        await call.answer("Foydalanuvchi topilmadi", show_alert=True)
        return
    submissions = await test_db.db(test_db.get_user_submissions, call.from_user.id)
    tests_count = len(submissions)
    scores = [float(s.get('score', s.get('correct_count', 0))) for s in submissions] if submissions else []
    avg_score = f"{sum(scores)/len(scores):.1f}" if scores else "0.0"
    max_score = f"{max(scores):.1f}" if scores else "0.0"
    st = (user.get("status") or "approved").lower()
    st_text = "✅ Faol o'quvchi" if st == "approved" else ("⏳ Kutilmoqda" if st == "pending" else "⛔️ Bloklangan")

    text = (
        "👤 <b>SHAXSIY PROFILINGIZ</b>\n\n"
        f"👤 <b>Foydalanuvchi:</b> {user['fullname']}\n"
        f"🔰 <b>Holat:</b> {st_text}\n\n"
        f"📊 <b>KO'RSATKICHLAR:</b>\n"
        f"• Ishlangan testlar: <b>{tests_count} ta</b>\n"
        f"• O'rtacha natija: <b>{avg_score} ball</b>\n"
        f"• Eng yuqori natija: <b>{max_score} ball</b>\n\n"
        f"<i>Batafsil ma'lumotlarni ko'rish yoki tahrirlash uchun quyidagi tugmalardan birini tanlang 👇</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Shaxsiy ma'lumotlar", callback_data="profile_show_details")],
        [
            InlineKeyboardButton(text="✏️ Ismni o'zgartirish", callback_data="profile_edit_name"),
            InlineKeyboardButton(text="📞 Raqamni o'zgartirish", callback_data="profile_edit_phone")
        ],
        [make_webapp_button("📱 Shaxsiy profil (Mini App)", f"{get_webapp_url()}/app.html?tg_id={call.from_user.id}")],
        [InlineKeyboardButton(text="🗑 Akkauntni o'chirish", callback_data="profile_delete_account")]
    ])
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass
    await call.answer()

# ── PROFILNI TAHRIRLASH (BOT CHATIDA) ──────────────────
@user_router.callback_query(F.data == "profile_edit_name")
async def profile_edit_name_handler(call: CallbackQuery, state: FSMContext):
    await state.set_state(EditProfileState.waiting_new_name)
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Bekor qilish", callback_data="cancel_edit_profile")]
    ])
    await call.message.answer(
        "✏️ <b>Ismni o'zgartirish</b>\n\n"
        "Iltimos, yangi ism va familiyangizni yozib yuboring:\n"
        "<i>(Masalan: Ali Valiyev)</i>",
        reply_markup=cancel_kb
    )
    await call.answer()

@user_router.message(EditProfileState.waiting_new_name)
async def process_new_profile_name(message: Message, state: FSMContext):
    new_name = (message.text or "").strip()
    if not new_name or len(new_name) < 3 or len(new_name) > 60:
        await message.answer("⚠️ Iltimos, haqiqiy ism va familiyangizni to'liq kiriting (kamida 3 ta belgi):")
        return
    
    await test_db.db(test_db.update_user_profile, message.from_user.id, fullname=new_name)
    await state.clear()
    await message.answer(f"✅ Ismingiz muvaffaqiyatli yangilandi: <b>{new_name}</b>")
    await show_profile(message)

@user_router.callback_query(F.data == "profile_edit_phone")
async def profile_edit_phone_handler(call: CallbackQuery, state: FSMContext):
    await state.set_state(EditProfileState.waiting_new_phone)
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Telefon raqamni yuborish", request_contact=True)],
            [KeyboardButton(text="🔙 Bekor qilish")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await call.message.answer(
        "📞 <b>Telefon raqamni o'zgartirish</b>\n\n"
        "Iltimos, pastdagi <b>'📱 Telefon raqamni yuborish'</b> tugmasini bosing yoki yangi raqamingizni yozing (Masalan: +998901234567):",
        reply_markup=kb
    )
    await call.answer()

@user_router.message(EditProfileState.waiting_new_phone, F.contact)
async def process_new_profile_phone_contact(message: Message, state: FSMContext):
    contact = message.contact
    if not contact:
        await message.answer("Raqam aniqlanmadi.")
        return
    phone = contact.phone_number
    if not phone.startswith("+"):
        phone = "+" + phone
    await test_db.db(test_db.update_user_profile, message.from_user.id, phone=phone)
    await state.clear()
    await message.answer(
        f"✅ Telefon raqamingiz muvaffaqiyatli yangilandi: <b>{phone}</b>",
        reply_markup=main_menu_kb(message.from_user.id)
    )
    await show_profile(message)

@user_router.message(EditProfileState.waiting_new_phone)
async def process_new_profile_phone_text(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text == "🔙 Bekor qilish":
        await state.clear()
        await message.answer("Amal bekor qilindi.", reply_markup=main_menu_kb(message.from_user.id))
        await show_profile(message)
        return

    import re
    cleaned = re.sub(r'[^\d+]', '', text)
    if len(cleaned.replace("+", "")) < 9:
        await message.answer("⚠️ Noto'g'ri telefon raqam formati. Masalan: +998901234567")
        return
    if not cleaned.startswith("+"):
        cleaned = "+" + cleaned

    await test_db.db(test_db.update_user_profile, message.from_user.id, phone=cleaned)
    await state.clear()
    await message.answer(
        f"✅ Telefon raqamingiz muvaffaqiyatli yangilandi: <b>{cleaned}</b>",
        reply_markup=main_menu_kb(message.from_user.id)
    )
    await show_profile(message)

@user_router.callback_query(F.data == "cancel_edit_profile")
async def cancel_edit_profile_handler(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.delete()
    await call.answer("Tahrirlash bekor qilindi")

@user_router.callback_query(F.data == "profile_delete_account")
async def profile_delete_account_confirm(call: CallbackQuery):
    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚠️ Ha, butunlay o'chirilsin", callback_data="do_delete_my_account")],
        [InlineKeyboardButton(text="🔙 Bekor qilish", callback_data="cancel_edit_profile")]
    ])
    await call.message.answer(
        "⚠️ <b>DIQQAT! AKKAUNTNI O'CHIRISH</b>\n\n"
        "Haqiqatan ham akkauntingizni va barcha ishlangan test natijalaringizni butunlay o'chirib tashlamoqchimisiz?\n\n"
        "<i>Bu amalni ortga qaytarib bo'lmaydi!</i>",
        reply_markup=confirm_kb
    )
    await call.answer()

@user_router.callback_query(F.data == "do_delete_my_account")
async def do_delete_my_account_handler(call: CallbackQuery, state: FSMContext):
    await state.clear()
    uid = call.from_user.id
    user_info = await test_db.db(test_db.delete_user, uid, is_self_delete=True)
    await call.message.answer(
        "🗑 <b>Akkauntingiz va barcha natijalaringiz butunlay o'chirildi.</b>\n\n"
        "Qaytadan ro'yxatdan o'tish uchun /start buyrug'ini bosing.",
        reply_markup=ReplyKeyboardRemove()
    )
    await call.answer()

    # Adminga zudlik bilan bildirishnoma jo'natish
    if ADMIN_ID:
        try:
            fn = user_info.get("fullname") if user_info else (call.from_user.full_name or "Noma'lum")
            ph = user_info.get("phone", "—") if user_info else "—"
            un = f"@{user_info.get('username')}" if (user_info and user_info.get("username")) else (f"@{call.from_user.username}" if call.from_user.username else "Mavjud emas")
            alert_text = (
                f"🗑 <b>OGOHLANTIRISH: Foydalanuvchi akkauntini o'chirdi!</b>\n\n"
                f"👤 <b>Ism:</b> {fn}\n"
                f"📞 <b>Telefon:</b> <code>{ph}</code>\n"
                f"🔗 <b>Username:</b> {un}\n"
                f"🆔 <b>Telegram ID:</b> <code>{uid}</code>\n"
                f"🕒 <b>Vaqt:</b> {format_uzb_time()}\n\n"
                f"<i>Foydalanuvchi Telegram botidagi «Akkauntni o'chirish» tugmasini bosib chiqib ketdi.</i>"
            )
            await notify_all_admins(bot, alert_text)
        except Exception as ex:
            log.warning(f"Admin alert yuborishda xatolik: {ex}")



@user_router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=KICKED))
async def user_blocked_bot_handler(event: ChatMemberUpdated):
    """
    Foydalanuvchi botni to'xtatganda (Stop bot), bloklaganda yoki chatni o'chirganda
    Telegram avtomatik ravishda botga ushbu hodisani yuboradi (barcha adminlarga xabar boradi).
    """
    try:
        user_id = event.from_user.id
        u = await test_db.db(test_db.get_user, user_id)
        
        # Botni bloklagan foydalanuvchini bazadan to'liq o'chiramiz (o'zi ketgani uchun is_self_delete=True)
        await test_db.db(test_db.delete_user, user_id, is_self_delete=True)
        
        fn = (u and u.get("fullname")) or event.from_user.full_name or "Noma'lum"
        un = f"@{u.get('username')}" if (u and u.get('username')) else (f"@{event.from_user.username}" if event.from_user.username else "Mavjud emas")
        ph = (u and u.get("phone")) or "—"
        now_str = format_uzb_time(fmt="%d.%m.%Y %H:%M:%S")
        
        alert_text = (
            f"🚫 <b>OGOHLANTIRISH: Foydalanuvchi botni blokladi va bazadan o'chirildi!</b>\n\n"
            f"👤 <b>Foydalanuvchi:</b> {fn}\n"
            f"🔗 <b>Username:</b> {un}\n"
            f"📞 <b>Telefon:</b> <code>{ph}</code>\n"
            f"🆔 <b>Telegram ID:</b> <code>{user_id}</code>\n"
            f"🕒 <b>Vaqt:</b> <b>{now_str}</b>\n\n"
            f"🗑 <i>Foydalanuvchi botni to'xtatgani/bloklagani sababli uning barcha ma'lumotlari bazadan to'liq o'chirildi.</i>"
        )
        await notify_all_admins(bot, alert_text)
    except Exception as e:
        log.error(f"user_blocked_bot_handler error: {e}", exc_info=True)


@user_router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=MEMBER))
async def user_unblocked_bot_handler(event: ChatMemberUpdated):
    """
    Foydalanuvchi botni qayta ishga tushirganda (Restart) barcha adminlarga xabar boradi.
    """
    try:
        user_id = event.from_user.id
        u = await test_db.db(test_db.get_user, user_id)
        note = "<i>Botdan qayta foydalanishi mumkin.</i>"
            
        fn = (u and u.get("fullname")) or event.from_user.full_name or "Noma'lum"
        un = f"@{u.get('username')}" if (u and u.get('username')) else (f"@{event.from_user.username}" if event.from_user.username else "Mavjud emas")
        ph = (u and u.get("phone")) or "—"
        now_str = format_uzb_time(fmt="%d.%m.%Y %H:%M:%S")
        
        alert_text = (
            f"🟢 <b>Foydalanuvchi botni qayta faollashtirdi (Unblock)!</b>\n\n"
            f"👤 <b>Foydalanuvchi:</b> {fn}\n"
            f"🔗 <b>Username:</b> {un}\n"
            f"📞 <b>Telefon:</b> <code>{ph}</code>\n"
            f"🆔 <b>Telegram ID:</b> <code>{user_id}</code>\n"
            f"🕒 <b>Vaqt:</b> <b>{now_str}</b>\n\n"
            f"ℹ️ {note}"
        )
        await notify_all_admins(bot, alert_text)
    except Exception as e:
        log.error(f"user_unblocked_bot_handler error: {e}", exc_info=True)


# Guruh/chatga yangi a'zo qo'shilganda (Message orqali)
@user_router.message(F.new_chat_members)
async def new_chat_members_handler(message: Message):
    for member in message.new_chat_members:
        if member.id == message.bot.id:
            chat_title = message.chat.title or "Guruh"
            inviter = message.from_user.full_name if message.from_user else "Noma'lum"
            alert = (
                f"🤖 <b>Bot guruhga qo'shildi!</b>\n\n"
                f"👥 <b>Guruh nomi:</b> {html.escape(chat_title)}\n"
                f"🆔 <b>Guruh ID:</b> <code>{message.chat.id}</code>\n"
                f"👤 <b>Qo'shgan:</b> {html.escape(inviter)}\n"
                f"🕒 <b>Vaqt:</b> {format_uzb_time()}"
            )
            await notify_all_admins(message.bot, alert)
        else:
            set_cached_membership(member.id, message.chat.id, True)
            m_name = member.full_name or "Noma'lum"
            m_user = f"@{member.username}" if member.username else "Mavjud emas"
            chat_title = message.chat.title or "Guruh"
            alert = (
                f"👥 <b>Guruhga yangi a'zo qo'shildi!</b>\n\n"
                f"🏛 <b>Guruh:</b> {html.escape(chat_title)}\n"
                f"👤 <b>Foydalanuvchi:</b> {html.escape(m_name)}\n"
                f"🆔 <b>ID:</b> <code>{member.id}</code>\n"
                f"🔗 <b>Username:</b> {m_user}\n"
                f"🕒 <b>Vaqt:</b> {format_uzb_time()}"
            )
            await notify_all_admins(message.bot, alert)


# Havola orqali yoki to'g'ridan-to'g'ri guruh/kanalga a'zo bo'lganda (ChatMemberUpdated)
@user_router.chat_member(ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION))
async def chat_member_joined_handler(event: ChatMemberUpdated):
    member = event.new_chat_member.user
    if member.id == event.bot.id:
        return
    set_cached_membership(member.id, event.chat.id, True)
    m_name = member.full_name or "Noma'lum"
    m_user = f"@{member.username}" if member.username else "Mavjud emas"
    chat_title = event.chat.title or "Chat/Kanal"
    alert = (
        f"👥 <b>Chatga yangi a'zo qo'shildi!</b>\n\n"
        f"🏛 <b>Chat:</b> {html.escape(chat_title)}\n"
        f"👤 <b>Foydalanuvchi:</b> {html.escape(m_name)}\n"
        f"🆔 <b>ID:</b> <code>{member.id}</code>\n"
        f"🔗 <b>Username:</b> {m_user}\n"
        f"🕒 <b>Vaqt:</b> {format_uzb_time()}"
    )
    await notify_all_admins(event.bot, alert)





@user_router.message(F.text == "ℹ️ Yordam")
@user_router.message(F.text == "ℹ️ Bot haqida")
@user_router.message(Command("help"))
async def show_help(message: Message):
    contact_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Adminga murojaat (@eshmbetov)", url="https://t.me/eshmbetov")],
        [make_webapp_button("📱 Ona tili Test (Mini App)", f"{get_webapp_url()}/app.html?tg_id={message.from_user.id}")]
    ])
    await message.answer(
        "ℹ️ <b>YORDAM VA QO'LLAB-QUVVATLASH</b>\n\n"
        "🎓 <b>ONA TILI — RASH TEST TIZIMI</b>\n\n"
        "Ushbu tizim orqali siz:\n"
        "• Ona tili va adabiyot Milliy sertifikat formatidagi testlarni yechishingiz;\n"
        "• Mini ilovadan foydalanib yozma va test javoblarini kiritishingiz;\n"
        "• Rasch modeli bo'yicha darajangiz (A+, A, B+, B, ...) va to'liq tahlilni ko'rishingiz mumkin.\n\n"
        "💬 <b>Savol, taklif yoki yordam uchun to'g'ridan-to'g'ri bog'lanishingiz mumkin:</b>\n"
        "👤 <b>Aloqa:</b> @eshmbetov\n\n"
        "<i>Pastdagi tugma orqali murojaat yuborishingiz mumkin 👇</i>",
        reply_markup=contact_kb
    )

show_about = show_help



@user_router.message(Command("app"))
async def open_app_command(message: Message):
    await message.answer(
        "📱 <b>RASH TEST Mini App tizimiga kirish:</b>\n\n"
        "Quyidagi tugmani bosing 👇",
        reply_markup=results_webapp_kb(message.from_user.id)
    )



@user_router.callback_query(F.data.startswith("user_req_tahlil_"))
async def user_req_tahlil_cb(call: CallbackQuery, state: FSMContext):
    await state.clear()
    test_id = int(call.data.split("_")[3])
    test = test_db.get_test_by_id(test_id)
    if not test:
        await call.answer("Test topilmadi!", show_alert=True)
        return

    sub = await test_db.db(test_db.get_user_submission_for_test, test_id, call.from_user.id)
    if not sub:
        await call.answer("Siz ushbu testni topshirmagansiz!", show_alert=True)
        return

    await call.answer()
    tahlil_text = build_detailed_tahlil_text(sub, test)

    kb_rows = []
    yt_url = (test.get("youtube_url") or "").strip()
    if yt_url:
        kb_rows.append([InlineKeyboardButton(text="🎬 Video tahlilni ko'rish (YouTube)", url=yt_url)])
    kb_rows.append([InlineKeyboardButton(text="📱 Mini ilovada to'liq ko'rish", web_app=WebAppInfo(url=f"{get_webapp_url()}/app.html?tab=tests&tg_id={call.from_user.id}"))])

    await call.message.answer(
        tahlil_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        disable_web_page_preview=False
    )

@user_router.callback_query(F.data == "cancel_tahlil_code")
async def cancel_tahlil_code_cb(call: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await call.message.edit_text("❌ Tahlil kodini kiritish bekor qilindi.")
    except Exception:
        pass
    await call.answer()

@user_router.message(StudentTahlilState.waiting_for_code)
async def process_tahlil_code(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text:
        return

    menu_cmds = [
        "🔢 Test kodini kiritish", "📊 Mening natijalarim", "👤 Profilim",
        "ℹ️ Yordam", "ℹ️ Bot haqida", "⚙️ Admin Panel",
        "➕ Yangi test yaratish", "📊 Test natijalari va reyting", "📋 Testlarni boshqarish"
    ]
    if text in menu_cmds:
        await state.clear()
        if text == "📊 Mening natijalarim":
            await show_my_results(message)
        elif text == "👤 Profilim":
            await show_profile(message)
        elif text in ["ℹ️ Yordam", "ℹ️ Bot haqida"]:
            await show_help(message)
        elif text == "⚙️ Admin Panel":
            from .admin import admin_panel_handler
            await admin_panel_handler(message)
        elif text == "➕ Yangi test yaratish":
            from .admin import admin_create_test_text_handler
            await admin_create_test_text_handler(message)
        elif text == "📊 Test natijalari va reyting":
            from .admin import admin_leaderboard_text_handler
            await admin_leaderboard_text_handler(message)
        elif text == "📋 Testlarni boshqarish":
            from .admin import admin_manage_tests_text_handler
            await admin_manage_tests_text_handler(message)
        elif text == "🔢 Test kodini kiritish":
            await enter_test_code_prompt(message, state)
        return

    data = await state.get_data()
    test_id = data.get("tahlil_test_id")
    if not test_id:
        await state.clear()
        await message.answer("Test ma'lumotlari topilmadi. Qaytadan urinib ko'ring.")
        return

    test = test_db.get_test_by_id(test_id)
    if not test:
        await state.clear()
        await message.answer("Test topilmadi.")
        return

    sub = await test_db.db(test_db.get_user_submission_for_test, test_id, message.from_user.id)
    if not sub:
        await state.clear()
        await message.answer("Siz ushbu testni topshirmagansiz.")
        return

    entered_norm = text.upper().replace("#", "").strip()
    test_code_norm = str(test.get("test_code", "")).upper().replace("#", "").strip()
    key_code_norm = str(test.get("key_access_code", "")).upper().replace("#", "").strip()

    code_matches = False
    if key_code_norm and entered_norm == key_code_norm:
        code_matches = True
    elif test_code_norm and entered_norm == test_code_norm:
        code_matches = True

    if not code_matches:
        cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_tahlil_code")]
        ])
        await message.answer(
            f"❌ <b>Kiritilgan kod noto'g'ri!</b>\n\n"
            f"«{text}» kodi ushbu testga to'g'ri kelmadi. Iltimos, to'g'ri kodni kiriting yoki bekor qilish tugmasini bosing:",
            reply_markup=cancel_kb
        )
        return

    await state.clear()
    tahlil_text = build_detailed_tahlil_text(sub, test)

    kb_rows = []
    yt_url = (test.get("youtube_url") or "").strip()
    if yt_url:
        kb_rows.append([InlineKeyboardButton(text="🎬 Video tahlilni ko'rish (YouTube)", url=yt_url)])
    kb_rows.append([InlineKeyboardButton(text="📱 Mini ilovada to'liq ko'rish", web_app=WebAppInfo(url=f"{get_webapp_url()}/app.html?tab=tests&tg_id={message.from_user.id}"))])

    await message.answer(
        tahlil_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        disable_web_page_preview=False
    )


# Ro'yxatdan o'tmagan yoki holati bo'sh bo'lgan foydalanuvchilar uchun umumiy matn handleri
@user_router.message(F.text, StateFilter(None))
async def catch_unregistered_or_menu_text(message: Message, state: FSMContext):
    user_tg_id = message.from_user.id
    raw_text = (message.text or "").strip()
    if not raw_text:
        return

    # Buyruq bo'lsa (masalan /profile, /help), bu yerda to'xtatish (buyruq handlerlari ishlasin)
    if raw_text.startswith("/"):
        return

    user = await test_db.db(test_db.get_user, user_tg_id)
    if not user:
        if await test_db.db(test_db.is_admin, user_tg_id, ADMIN_ID):
            await test_db.db(test_db.add_or_update_user, user_tg_id, message.from_user.full_name or "Administrator", "", message.from_user.username, status="approved")
            user = await test_db.db(test_db.get_user, user_tg_id)
        else:
            if not (await test_db.db(test_db.is_registration_open)):
                await state.clear()
                await send_registration_closed_message(message, user_tg_id)
                return
            is_valid, _ = validate_fullname(raw_text)
            if is_valid:
                await reg_fullname(message, state)
                return
            else:
                await state.set_state(RegistrationState.fullname)
                await message.answer(
                    "👋 <b>Assalomu alaykum! Ona tili test tizimiga xush kelibsiz!</b>\n\n"
                    "Tizimdan to'liq foydalanish uchun ro'yxatdan o'ting.\n\n"
                    "✍️ <b>Iltimos, Ism va Familiyangizni kiriting:</b>\n"
                    "<i>(Misol: Shaxriyor Eshimbetov)</i>",
                    reply_markup=main_menu_kb(user_tg_id)
                )
                return

    # Faqat kutilayotgan (pending) yoki rad etilgan bo'lsa to'xtatamiz
    st = user.get("status", "approved")
    if not (await test_db.db(test_db.is_admin, user_tg_id, ADMIN_ID)):
        if st == "pending":
            await message.answer(
                "⏳ <b>Sizning so'rovingiz ko'rib chiqilmoqda...</b>\n\n"
                "Admin ruxsat bergandan so'ng botdan to'liq foydalanishingiz mumkin bo'ladi."
            )
            return
        elif st == "rejected":
            await message.answer(
                "⛔️ <b>Sizning botdan foydalanish so'rovingiz rad etilgan!</b>\n\n"
                "Murojaat uchun: @eshmbetov"
            )
            return

    text = raw_text

    # Asosiy menyu tugmalari
    if text == "🔢 Test kodini kiritish":
        await enter_test_code_prompt(message, state)
        return
    elif text == "📊 Mening natijalarim":
        await show_my_results(message)
        return
    elif text == "👤 Profilim":
        await show_profile(message)
        return
    elif text in ["ℹ️ Yordam", "ℹ️ Bot haqida"]:
        await show_help(message)
        return
    elif text == "⚙️ Admin Panel":
        from .admin import admin_panel_handler
        await admin_panel_handler(message)
        return
    elif text == "➕ Yangi test yaratish":
        from .admin import admin_create_test_text_handler
        await admin_create_test_text_handler(message)
        return
    elif text == "📊 Test natijalari va reyting":
        from .admin import admin_leaderboard_text_handler
        await admin_leaderboard_text_handler(message)
        return
    elif text == "📋 Testlarni boshqarish":
        from .admin import admin_manage_tests_text_handler
        await admin_manage_tests_text_handler(message)
        return

    # Noma'lum buyruq yoki xabarlar uchun yo'naltiruvchi javob
    await message.answer(
        f"👋 Salom, <b>{user['fullname']}</b>!\n\n"
        f"Test topshirish uchun «🔢 Test kodini kiritish» tugmasini bosing yoki quyidagi menyudan foydalaning 👇",
        reply_markup=main_menu_kb(user_tg_id)
    )



