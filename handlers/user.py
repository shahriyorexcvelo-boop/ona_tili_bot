"""
User-facing Telegram bot handlers and routers.
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

from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.filters.chat_member_updated import ChatMemberUpdatedFilter, KICKED, MEMBER
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove,
    WebAppInfo, FSInputFile, ChatMemberUpdated
)

import test_db
from core import (
    UZB_TZ, format_uzb_time, TG_ICONS, tg_e,
    BOT_TOKEN, ADMIN_ID, WEBAPP_URL, log, bot,
    RegistrationState, SolveTestState, EditProfileState, StudentTahlilState,
    main_menu_kb, profile_webapp_kb, results_webapp_kb,
    contact_share_kb, make_webapp_button,
    is_working_hours, get_night_message
)
from .common import (
    check_access, validate_fullname, send_test_card, send_test_card_to_user_chat,
    BLACKLIST_NAME_WORDS
)

user_router = Router(name="user_router")

@user_router.message(CommandStart())
async def start_handler(message: Message, state: FSMContext):
    user_tg_id = message.from_user.id

    # Admin uchun tun rejimi qo'llanilmaydi
    if not test_db.is_admin(user_tg_id, ADMIN_ID) and not is_working_hours():
        await message.answer(get_night_message())
        return

    user = test_db.get_user(user_tg_id)

    if not user:
        await state.set_state(RegistrationState.fullname)
        await message.answer(
            "👋 <b>Assalomu alaykum! Test Tekshirish Tizimiga xush kelibsiz!</b>\n\n"
            "Tizimdan to'liq foydalanish va ruxsat olish uchun ro'yxatdan o'ting.\n\n"
            "✍️ <b>Iltimos, Ism va Familiyangizni kiriting:</b>\n"
            "<i>(Misol: Shaxriyor Eshimbetov)</i>",
            reply_markup=ReplyKeyboardRemove()
        )
    else:
        is_adm = test_db.is_admin(user_tg_id, ADMIN_ID)
        st = user.get("status", "approved")
        if not is_adm:
            if st in ["blocked", "rejected"]:
                await message.answer(
                    "⛔️ <b>Sizning botdan foydalanish huquqingiz to'xtatilgan!</b>\n\n"
                    "Murojaat uchun: @eshmbetov"
                )
                return

        await state.clear()
        await message.answer(
            f"👋 <b>Xush kelibsiz, {user['fullname']}!</b>\n\n"
            f"{tg_e('order', '📋')} <b>Ona tili — Rasch Test Tizimi</b>\n\n"
            f"{tg_e('clock', '⏰')} Kerakli bo'limni tanlang yoki to'g'ridan-to'g'ri test kodini yuboring 👇",
            reply_markup=main_menu_kb(user_tg_id)
        )



# Ro'yxatdan o'tish: Ism kiritildi (Telefon so'ralmaydi, darhol ro'yxatdan o'tadi)
@user_router.message(RegistrationState.fullname)
async def reg_fullname(message: Message, state: FSMContext):
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

    # 1. Yangi foydalanuvchi to'g'ridan-to'g'ri faol (approved) bo'ladi
    status = "approved"
    test_db.add_or_update_user(user_tg_id, fullname, phone, username, status=status)
    try:
        test_db.log_activity(user_tg_id, "user_register", f"Yangi o'quvchi ro'yxatdan o'tdi: {fullname}", {"tg_id": user_tg_id, "fullname": fullname, "username": username})
    except Exception:
        pass
    invalidate_dashboard_cache()
    await state.clear()

    # 2. O'quvchiga darhol asosiy menyuni ochish
    await message.answer(
        f"🎉 <b>Tabriklaymiz, {fullname}! Siz muvaffaqiyatli ro'yxatdan o'tdingiz!</b>\n\n"
        f"Kerakli bo'limni tanlang yoki to'g'ridan-to'g'ri test kodini yuboring 👇",
        reply_markup=main_menu_kb(user_tg_id)
    )

    # 3. Adminga shunchaki yangi a'zo haqida ma'lumot (so'rovsiz)
    username_str = f"@{username}" if username else "Mavjud emas"
    admin_notify_text = (
        f"👤 <b>Yangi foydalanuvchi ro'yxatdan o'tdi:</b>\n\n"
        f"👤 <b>Ism-familiya:</b> {fullname}\n"
        f"🆔 <b>Telegram ID:</b> <code>{user_tg_id}</code>\n"
        f"🔗 <b>Username:</b> {username_str}\n"
        f"🕒 <b>Vaqt:</b> {format_uzb_time()}"
    )

    for adm_id in get_all_admin_ids():
        try:
            await bot.send_message(
                chat_id=adm_id,
                text=admin_notify_text
            )
        except Exception as ex:
            log.warning(f"Adminga ({adm_id}) yangi a'zo xabarini yuborishda xatolik: {ex}")

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

    status = "approved"
    test_db.add_or_update_user(user_tg_id, fullname, phone, username, status=status)
    try:
        test_db.log_activity(user_tg_id, "user_register", f"Yangi o'quvchi ro'yxatdan o'tdi: {fullname}", {"tg_id": user_tg_id, "fullname": fullname, "username": username})
    except Exception:
        pass
    invalidate_dashboard_cache()
    await state.clear()

    await message.answer(
        f"🎉 <b>Tabriklaymiz, {fullname}! Siz muvaffaqiyatli ro'yxatdan o'tdingiz!</b>\n\n"
        f"Kerakli bo'limni tanlang yoki to'g'ridan-to'g'ri test kodini yuboring 👇",
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
            await admin_panel_handler(message)
        elif text == "➕ Yangi test yaratish":
            await admin_create_test_text_handler(message)
        elif text == "📊 Test natijalari va reyting":
            await admin_leaderboard_text_handler(message)
        elif text == "📋 Testlarni boshqarish":
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
    if sched_stat["is_upcoming"] and not test_db.is_admin(call.from_user.id, ADMIN_ID):
        sstart = t.get('scheduled_start') or ''
        sdate = t.get('scheduled_date') or 'Bugun'
        await call.answer(f"⏳ Test hali boshlanmagan! Boshlanish vaqti: {sdate} {sstart} (UZB)", show_alert=True)
        return
    
    existing_sub = test_db.get_user_submission_for_test(test_id, call.from_user.id)
    if existing_sub:
        is_published = test_db.is_test_results_published(test_id)
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
    encoded_url = f"{WEBAPP_URL}?{urllib.parse.urlencode(params)}"
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
    admin_webapp_url = f"{WEBAPP_URL}/admin.html?tg_id={call.from_user.id}"
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
    subs = test_db.get_user_submissions(message.from_user.id)
    pub_subs = [s for s in subs if s.get("results_published")]

    kb_rows = []
    if pub_subs:
        for s in pub_subs[:5]:
            t_title = s.get("test_title", "Test")
            t_code = s.get("test_code", "")
            btn_title = f"📊 #{t_code} tahlili" if t_code else f"📊 {t_title[:20]} tahlili"
            kb_rows.append([InlineKeyboardButton(text=btn_title, callback_data=f"user_req_tahlil_{s['test_id']}")])

    kb_rows.append([make_webapp_button("📱 Barcha natijalar va tahlillar (Mini App)", f"{WEBAPP_URL}/app.html?tab=tests")])

    await message.answer(
        "📊 <b>Mening natijalarim va tahlillar</b>\n\n"
        "Quyidagi tugmalar orqali topshirgan testlaringiz tahlilini ko'rishingiz yoki Mini ilovani ochishingiz mumkin 👇",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
    )

# 3. 👤 Profil (Bot chatida ko'rish va tahrirlash)
@user_router.message(F.text == "👤 Profilim")
@user_router.message(Command("profile"))
async def show_profile(message: Message):
    user = test_db.get_user(message.from_user.id)
    if not user:
        await message.answer("Profil topilmadi. /start buyrug'ini bosing.")
        return

    submissions = test_db.get_user_submissions(message.from_user.id)
    tests_count = len(submissions)
    dt = format_uzb_time(user.get("registered_at"), "%d.%m.%Y") if user.get("registered_at") else "Noma'lum"

    scores = [float(s.get('score', s.get('correct_count', 0))) for s in submissions] if submissions else []
    avg_score = f"{sum(scores)/len(scores):.1f}" if scores else "0.0"
    max_score = f"{max(scores):.1f}" if scores else "0.0"

    st = (user.get("status") or "pending").lower()
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
        [make_webapp_button("📱 Shaxsiy profil (Mini App)", f"{WEBAPP_URL}/app.html?tg_id={message.from_user.id}")],
        [InlineKeyboardButton(text="🗑 Akkauntni o'chirish", callback_data="profile_delete_account")]
    ])

    await message.answer(text, reply_markup=kb)

@user_router.callback_query(F.data == "profile_show_details")
async def profile_show_details_handler(call: CallbackQuery):
    user = test_db.get_user(call.from_user.id)
    if not user:
        await call.answer("Foydalanuvchi topilmadi", show_alert=True)
        return
    submissions = test_db.get_user_submissions(call.from_user.id)
    tests_count = len(submissions)
    dt = format_uzb_time(user.get("registered_at"), "%d.%m.%Y %H:%M") if user.get("registered_at") else "Noma'lum"
    phone_str = user.get('phone') or "Biriktirilmagan"
    username_str = f"@{call.from_user.username}" if call.from_user.username else (f"@{user.get('username')}" if user.get('username') else "Mavjud emas")
    st = (user.get("status") or "pending").lower()
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
        [make_webapp_button("📱 Shaxsiy profil (Mini App)", f"{WEBAPP_URL}/app.html?tg_id={call.from_user.id}")],
        [InlineKeyboardButton(text="🗑 Akkauntni o'chirish", callback_data="profile_delete_account")]
    ])
    try:
        await call.message.edit_text(detail_text, reply_markup=kb)
    except Exception:
        pass
    await call.answer()

@user_router.callback_query(F.data == "profile_hide_details")
async def profile_hide_details_handler(call: CallbackQuery):
    user = test_db.get_user(call.from_user.id)
    if not user:
        await call.answer("Foydalanuvchi topilmadi", show_alert=True)
        return
    submissions = test_db.get_user_submissions(call.from_user.id)
    tests_count = len(submissions)
    scores = [float(s.get('score', s.get('correct_count', 0))) for s in submissions] if submissions else []
    avg_score = f"{sum(scores)/len(scores):.1f}" if scores else "0.0"
    max_score = f"{max(scores):.1f}" if scores else "0.0"
    st = (user.get("status") or "pending").lower()
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
        [make_webapp_button("📱 Shaxsiy profil (Mini App)", f"{WEBAPP_URL}/app.html?tg_id={call.from_user.id}")],
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
    
    test_db.update_user_profile(message.from_user.id, fullname=new_name)
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
    test_db.update_user_profile(message.from_user.id, phone=phone)
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

    test_db.update_user_profile(message.from_user.id, phone=cleaned)
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
    user_info = test_db.delete_user(uid)
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
            await bot.send_message(chat_id=ADMIN_ID, text=alert_text)
        except Exception as ex:
            log.warning(f"Admin alert yuborishda xatolik: {ex}")



async def user_blocked_bot_handler(event: ChatMemberUpdated):
    """
    Foydalanuvchi botni to'xtatganda (Stop bot), bloklaganda yoki chatni o'chirganda
    Telegram avtomatik ravishda botga ushbu hodisani yuboradi (foydalanuvchiga xabar bormaydi).
    """
    try:
        user_id = event.from_user.id
        u = test_db.get_user(user_id)
        
        # Botni bloklagan foydalanuvchini bazadan to'liq o'chiramiz
        test_db.delete_user(user_id)
        
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
        
        # Faqat Bosh adminga (ADMIN_ID) yuboriladi, tayinlangan yordamchi adminlarga yuborilmaydi
        try:
            await bot.send_message(chat_id=ADMIN_ID, text=alert_text)
        except Exception as ex:
            log.warning(f"Bosh adminga ({ADMIN_ID}) blok bildirishnomasi yuborishda xatolik: {ex}")
    except Exception as e:
        log.error(f"user_blocked_bot_handler error: {e}", exc_info=True)


@user_router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=MEMBER))
async def user_unblocked_bot_handler(event: ChatMemberUpdated):
    """
    Foydalanuvchi botni blokdan chiqarganda (Unblock/Restart).
    """
    try:
        user_id = event.from_user.id
        u = test_db.get_user(user_id)
        
        # Agar avval ro'yxatdan o'tgan bo'lsa va bloklangan bo'lsa, 'approved' qilamiz
        if u and u.get("status") == "blocked":
            test_db.approve_user(user_id)
            note = "<i>Bazada holati qayta «Faol» qilindi.</i>"
        else:
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
        
        # Faqat Bosh adminga (ADMIN_ID) yuboriladi, tayinlangan yordamchi adminlarga yuborilmaydi
        try:
            await bot.send_message(chat_id=ADMIN_ID, text=alert_text)
        except Exception as ex:
            log.warning(f"Bosh adminga ({ADMIN_ID}) unblock bildirishnomasi yuborishda xatolik: {ex}")
    except Exception as e:
        log.error(f"user_unblocked_bot_handler error: {e}", exc_info=True)



@user_router.message(F.text == "ℹ️ Yordam")
@user_router.message(F.text == "ℹ️ Bot haqida")
@user_router.message(Command("help"))
async def show_help(message: Message):
    contact_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Adminga murojaat (@eshmbetov)", url="https://t.me/eshmbetov")],
        [make_webapp_button("📱 Ona tili Test (Mini App)", f"{WEBAPP_URL}/app.html")]
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
    test_id = int(call.data.split("_")[3])
    test = test_db.get_test_by_id(test_id)
    if not test:
        await call.answer("Test topilmadi!", show_alert=True)
        return

    sub = test_db.get_user_submission_for_test(test_id, call.from_user.id)
    if not sub:
        await call.answer("Siz ushbu testni topshirmagansiz!", show_alert=True)
        return

    await state.set_state(StudentTahlilState.waiting_for_code)
    await state.update_data(tahlil_test_id=test_id)

    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_tahlil_code")]
    ])
    await call.message.answer(
        f"🔐 <b>«{test['title']}» (<code>#{test.get('test_code', '')}</code>) testi tahlili</b>\n\n"
        f"Savollar tahlilini ko'rish uchun test kodini yoki admin tomonidan berilgan maxsus parolni kiriting:\n\n"
        f"<i>(Masalan: <code>{test.get('test_code', '101')}</code>)</i>",
        reply_markup=cancel_kb
    )
    await call.answer()

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
            await admin_panel_handler(message)
        elif text == "➕ Yangi test yaratish":
            await admin_create_test_text_handler(message)
        elif text == "📊 Test natijalari va reyting":
            await admin_leaderboard_text_handler(message)
        elif text == "📋 Testlarni boshqarish":
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

    sub = test_db.get_user_submission_for_test(test_id, message.from_user.id)
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
    kb_rows.append([InlineKeyboardButton(text="📱 Mini ilovada to'liq ko'rish", web_app=WebAppInfo(url=f"{WEBAPP_URL}/app.html?tab=tests"))])

    await message.answer(
        tahlil_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        disable_web_page_preview=False
    )

