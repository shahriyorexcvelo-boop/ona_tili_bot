"""
Admin Telegram bot handlers and routers.
"""
from __future__ import annotations
import asyncio
import html
import json
import logging
import os
import re
import sys
import time
import urllib.parse
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, Tuple, List

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove,
    WebAppInfo, FSInputFile
)

import test_db
from core import (
    UZB_TZ, format_uzb_time, TG_ICONS, tg_e,
    BOT_TOKEN, ADMIN_ID, get_config_admin_ids, WEBAPP_URL, get_webapp_url, log, bot,
    RegistrationState, SolveTestState, UploadPostPdfState, AddAdminState,
    SetTimeLimitState, BroadcastState, ScheduleState, SetYoutubeState,
    ContactUserState, AdminPdfWatermarkState,
    main_menu_kb, profile_webapp_kb, results_webapp_kb,
    contact_share_kb, admin_menu_kb, make_webapp_button,
    get_test_schedule_status, get_test_min_submit_info
)
from services import (
    MAINT_START_TEXT, MAINT_END_TEXT,
    send_broadcast_to_users, delete_broadcast_batch_from_users,
    delete_recent_bot_messages_from_all_users,
    process_pdf_watermark_async, HEAVY_TASKS_SEMAPHORE
)
from .common import (
    get_all_admin_ids, check_access, clean_blocked_users_from_db,
    get_inactive_target_users, send_inactive_warning_messages,
    get_warn_inactive_step1_kb, build_user_contact_card,
    notify_admins_test_results_published, send_test_card,
    build_detailed_tahlil_text
)

admin_router = Router(name="admin_router")

async def admin_check_blocks_handler(message: Message):
    """
    Admin uchun: Bazadagi barcha foydalanuvchilarni tekshirib, botni bloklaganlarni bazadan to'liq o'chirish.
    """
    if not (await test_db.db(test_db.is_admin, message.from_user.id, ADMIN_ID)):
        await message.answer("⛔️ Bu buyruq faqat bot administratori uchun!")
        return

    users = await test_db.db(test_db.get_all_users)
    total = len(users)
    status_msg = await message.answer(
        f"🔍 <b>Bazadagi {total} ta foydalanuvchi tekshirilmoqda...</b>\n\n"
        f"<i>(Botni bloklagan yoki o'chirgan foydalanuvchilar aniqlansa, darhol bazadan o'chiriladi)</i>"
    )

    result = await clean_blocked_users_from_db(initiator_id=message.from_user.id)
    deleted_list = result["deleted_users"]
    active_count = result["active_count"]

    res_text = (
        f"📊 <b>Foydalanuvchilar holati tekshiruvi yakunlandi:</b>\n\n"
        f"👥 <b>Jami tekshirildi:</b> {total} ta\n"
        f"✅ <b>Faol foydalanuvchilar:</b> {active_count} ta\n"
        f"🗑 <b>Botni bloklagani uchun bazadan o'chirildi:</b> {len(deleted_list)} ta\n\n"
    )

    if deleted_list:
        res_text += "<b>Bazadan o'chirilgan foydalanuvchilar:</b>\n"
        for i, bu in enumerate(deleted_list[:30], 1):
            bun = f" ({bu['username']})" if bu.get('username') and bu['username'] != '—' else ""
            res_text += f"{i}. <b>{bu['fullname']}</b>{bun} — <code>{bu['tg_id']}</code>\n"
        if len(deleted_list) > 30:
            res_text += f"\n<i>...va yana {len(deleted_list) - 30} ta foydalanuvchi.</i>"
    else:
        res_text += "🎉 <i>Hozirda botni bloklagan foydalanuvchilar aniqlanmadi (baza toza)!</i>"

    try:
        await status_msg.edit_text(res_text)
    except Exception:
        await message.answer(res_text)


async def admin_warn_inactive_handler(message: Message):
    if not (await test_db.db(test_db.is_admin, message.from_user.id, ADMIN_ID)):
        await message.answer("⛔️ Bu buyruq faqat bot administratori uchun!")
        return
    text, kb = get_warn_inactive_step1_kb()
    await message.answer(text, reply_markup=kb)

@admin_router.callback_query(F.data == "admin_warn_inactive_menu")
async def admin_warn_inactive_menu_cb(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return
    text, kb = get_warn_inactive_step1_kb()
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except Exception:
        await call.message.answer(text, reply_markup=kb)
    await call.answer()

@admin_router.callback_query(F.data.startswith("adm_warn_cnt_"))
async def adm_warn_cnt_cb(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    cnt = call.data.replace("adm_warn_cnt_", "")
    cnt_labels = {
        "0": "Faqat 0 ta test ishlaganlar",
        "1": "Faqat 1 ta test ishlaganlar",
        "both": "0 va 1 ta test ishlaganlar"
    }
    label = cnt_labels.get(cnt, cnt)

    text = (
        f"⏱ <b>RO'YXATDAN O'TGAN VAQTI BO'YICHA SARALASH</b>\n\n"
        f"🎯 Tanlangan toifa: <b>{label}</b>\n\n"
        f"Foydalanuvchilar qaysi muddat bo'yicha saralansin?\n\n"
        f"⏳ <b>Qo'shilganiga 2+ kun bo'lganlar (tavsiya):</b>\n"
        f"<i>Yangi ro'yxatdan o'tganlarga (bugun yoki kecha kirganlarga) tegilmaydi, faqat kamida 2 kun oldin qo'shilganlarga yuboriladi.</i>\n\n"
        f"🆕 <b>Oxirgi 2 kunda qo'shilganlar:</b>\n"
        f"<i>Faqat so'nggi 48 soat ichida ro'yxatdan o'tganlarga yuboriladi.</i>\n\n"
        f"🗓 <b>Barcha vaqt bo'yicha:</b>\n"
        f"<i>Qachon qo'shilganidan qat'i nazar barcha tegishli o'quvchilarga yuboriladi.</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏳ Qo'shilganiga 2+ kun bo'lganlar", callback_data=f"adm_warn_time_{cnt}_min_2d")],
        [InlineKeyboardButton(text="🆕 Oxirgi 2 kunda qo'shilganlar", callback_data=f"adm_warn_time_{cnt}_last_2d")],
        [InlineKeyboardButton(text="🗓 Barcha vaqt bo'yicha (hammasi)", callback_data=f"adm_warn_time_{cnt}_all")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin_warn_inactive_menu")]
    ])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@admin_router.callback_query(F.data.startswith("adm_warn_time_"))
async def adm_warn_time_cb(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    parts = call.data.replace("adm_warn_time_", "").split("_", 1)
    cnt = parts[0]
    time_f = parts[1]

    cnt_labels = {
        "0": "Faqat 0 ta test ishlaganlar",
        "1": "Faqat 1 ta test ishlaganlar",
        "both": "0 va 1 ta test ishlaganlar"
    }
    time_labels = {
        "min_2d": "Qo'shilganiga 2+ kun bo'lganlar (2 kundan oshganlar)",
        "last_2d": "Oxirgi 2 kunda qo'shilganlar (so'nggi 48 soat)",
        "all": "Barcha vaqt bo'yicha (cheklovsiz)"
    }

    target_users = await test_db.db(get_inactive_target_users, target_count=cnt, time_filter=time_f)
    user_count = len(target_users)

    if user_count == 0:
        text = (
            f"ℹ️ <b>FOYDALANUVCHILAR TOPILMADI</b>\n\n"
            f"Siz tanlagan shartlar bo'yicha:\n"
            f"• Toifa: <b>{cnt_labels.get(cnt)}</b>\n"
            f"• Muddat: <b>{time_labels.get(time_f)}</b>\n\n"
            f"Hozirda hech qanday foydalanuvchi mavjud emas."
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Qayta tanlash", callback_data="admin_warn_inactive_menu")],
            [InlineKeyboardButton(text="🔙 Admin Menyuga", callback_data="admin_back_to_menu")]
        ])
        await call.message.edit_text(text, reply_markup=kb)
        await call.answer()
        return

    text = (
        f"📋 <b>OGOHLANTIRISH YUBORISHNI TASDIQLASH</b>\n\n"
        f"Siz tanlagan filtr sozlamalari:\n"
        f"🎯 <b>Toifa:</b> {cnt_labels.get(cnt)}\n"
        f"⏱ <b>Muddat:</b> {time_labels.get(time_f)}\n\n"
        f"👥 <b>Topilgan o'quvchilar soni:</b> <b>{user_count} nafar</b>\n\n"
        f"⚠️ <i>Har bir foydalanuvchiga uning testlari soniga mos rasmiy ogohlantirish xabari yetkaziladi. "
        f"Agar foydalanuvchi botni bloklagan bo'lsa, u avtomatik bazadan o'chiriladi.</i>\n\n"
        f"<b>Haqiqatan ham ushbu {user_count} nafar foydalanuvchiga xabar yuborilsinmi?</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🚀 Ha, yuborilsin ({user_count} ta)", callback_data=f"adm_warn_send_{cnt}_{time_f}")],
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin_back_to_menu")]
    ])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@admin_router.callback_query(F.data.startswith("adm_warn_send_"))
async def adm_warn_send_cb(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    parts = call.data.replace("adm_warn_send_", "").split("_", 1)
    cnt = parts[0]
    time_f = parts[1]

    await call.answer("⏳ Xabarlar yuborilmoqda...")
    status_msg = await call.message.edit_text(
        "⏳ <b>Xabarlar yuborilmoqda, iltimos kuting...</b>\n\n"
        "<i>Har bir foydalanuvchiga xabar yetkazilmoqda va bloklaganlar tekshirilmoqda...</i>"
    )

    result = await send_inactive_warning_messages(
        initiator_id=call.from_user.id,
        target_count=cnt,
        time_filter=time_f
    )

    total = result["total_targets"]
    sent = result["sent_count"]
    fail = result["fail_count"]
    deleted = result.get("deleted_count", 0)

    res_text = (
        f"✅ <b>Ogohlantirish xabarlari muvaffaqiyatli tarqatildi!</b>\n\n"
        f"🎯 <b>Rejalashtirilgan:</b> {total} nafar\n"
        f"📨 <b>Yetkazildi:</b> {sent} nafar\n"
        f"⚠️ <b>Yetkazilmadi (bloklangan):</b> {fail} nafar\n"
    )
    if deleted > 0:
        res_text += f"🗑 <b>Botni bloklagani uchun bazadan o'chirildi:</b> {deleted} nafar\n"

    res_text += "\n<i>Batafsil hisobot qabul qilindi.</i>"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Admin panelga", callback_data="admin_back_to_menu")]
    ])
    try:
        await status_msg.edit_text(res_text, reply_markup=kb)
    except Exception:
        await call.message.answer(res_text, reply_markup=kb)

@admin_router.callback_query(F.data == "admin_clean_blocked_prompt")
async def admin_clean_blocked_prompt_cb(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return

    users_count = len((await test_db.db(test_db.get_all_users)))
    text = (
        f"🧹 <b>BOTNI BLOKLAGANLARNI TOZALASH</b>\n\n"
        f"Hozirda bazada jami: <b>{users_count} nafar</b> foydalanuvchi mavjud.\n\n"
        f"Ushbu funksiya barcha foydalanuvchilarni ko'rinmas usulda (hech qanday xabar bormasdan) tekshirib chiqadi.\n\n"
        f"Agar biror foydalanuvchi botni to'xtatgan yoki bloklagan bo'lsa, u <b>bazadan to'liq o'chirib tashlanadi</b>.\n\n"
        f"<b>Tekshiruv va tozalashni boshlashni tasdiqlaysizmi?</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧹 Ha, tozalashni boshlash", callback_data="adm_clean_blocked_start")],
        [InlineKeyboardButton(text="🔙 Bekor qilish", callback_data="admin_back_to_menu")]
    ])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@admin_router.callback_query(F.data == "adm_clean_blocked_start")
async def adm_clean_blocked_start_cb(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return

    await call.answer("⏳ Tozalash boshlandi...")
    await call.message.edit_text(
        "⏳ <b>Bazadagi barcha foydalanuvchilar tekshirilmoqda...</b>\n\n"
        "<i>Bu bir necha soniya vaqt olishi mumkin, iltimos kuting...</i>"
    )

    result = await clean_blocked_users_from_db(initiator_id=call.from_user.id)
    total = result["total_checked"]
    active = result["active_count"]
    deleted = result["deleted_count"]
    deleted_list = result["deleted_users"]

    res_text = (
        f"📊 <b>Foydalanuvchilar holati tekshiruvi yakunlandi:</b>\n\n"
        f"👥 <b>Jami tekshirildi:</b> {total} ta\n"
        f"✅ <b>Faol foydalanuvchilar:</b> {active} ta\n"
        f"🗑 <b>Botni bloklagani uchun bazadan o'chirildi:</b> {deleted} ta\n\n"
    )
    if deleted_list:
        res_text += "<b>Bazadan o'chirilgan foydalanuvchilar:</b>\n"
        for i, bu in enumerate(deleted_list[:25], 1):
            bun = f" ({bu['username']})" if bu.get('username') and bu['username'] != '—' else ""
            res_text += f"{i}. <b>{bu['fullname']}</b>{bun} — <code>{bu['tg_id']}</code>\n"
        if len(deleted_list) > 25:
            res_text += f"\n<i>...va yana {len(deleted_list) - 25} ta foydalanuvchi.</i>"
    else:
        res_text += "🎉 <i>Hozirda botni bloklagan foydalanuvchilar aniqlanmadi (baza toza)!</i>"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Admin panelga", callback_data="admin_back_to_menu")]
    ])
    await call.message.edit_text(res_text, reply_markup=kb)



@admin_router.message(F.text == "➕ Yangi test yaratish")
async def admin_create_test_text_handler(message: Message):
    if not (await test_db.db(test_db.is_admin, message.from_user.id, ADMIN_ID)):
        return
    admin_webapp_url = f"{get_webapp_url()}/admin.html?tg_id={message.from_user.id}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_webapp_button("➕ Yangi test yaratish (Mini App)", admin_webapp_url)]
    ])
    await message.answer(
        "➕ <b>YANGI TEST YARATISH BO'LIMI</b>\n\n"
        "Quyidagi tugma orqali Admin Mini Appni ochib, test kodi, fani, vaqti va 55 ta savol kalitlarini kiritishingiz mumkin 👇",
        reply_markup=kb
    )

# 📋 Testlarni boshqarish (O'chirish, to'xtatish, vaqt)
@admin_router.message(F.text == "📋 Testlarni boshqarish")
async def admin_manage_tests_text_handler(message: Message):
    if not (await test_db.db(test_db.is_admin, message.from_user.id, ADMIN_ID)):
        return
    tests = await test_db.db(test_db.get_all_tests)
    if not tests:
        await message.answer("ℹ️ Hozircha bazada birorta ham test yo'q.")
        return

    text = (
        "📋 <b>Barcha testlar ro'yxati va boshqaruvi:</b>\n\n"
        "<i>Boshqarish (to'xtatish / vaqt / o'chirish) uchun kerakli testni tanlang 👇</i>\n\n"
    )
    buttons = []
    for idx, t in enumerate(tests, 1):
        status_icon = "🟢" if t["is_active"] == 1 else "🔴"
        time_str = f"{t['time_limit_min']} daqiqa" if t.get("time_limit_min", 0) > 0 else "Cheksiz"
        text += f"<b>{idx}. #{t['test_code']}</b> — {t['title']} ({status_icon}, ⏱ {time_str})\n"
        buttons.append([InlineKeyboardButton(text=f"{status_icon} #{t['test_code']} — {t['title'][:25]}", callback_data=f"adm_mng_test_{t['id']}")])

    buttons.append([InlineKeyboardButton(text="🔙 Admin Menyuga qaytish", callback_data="admin_back_to_menu")])
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

# 📊 Test natijalari va reyting
@admin_router.message(F.text == "📊 Test natijalari va reyting")
async def admin_leaderboard_text_handler(message: Message):
    if not (await test_db.db(test_db.is_admin, message.from_user.id, ADMIN_ID)):
        return
    tests = await test_db.db(test_db.get_tests_with_stats)
    if not tests:
        await message.answer("⚠️ Hozirda tizimda mavjud testlar yo'q.")
        return
    buttons = []
    msg_list = ""
    for idx, t in enumerate(tests, 1):
        sub_cnt = t.get("submissions_count", 0)
        btn_text = f"📊 #{t['test_code']} — 👥 {sub_cnt} kishi"
        buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"adm_tstat_{t['id']}")])
        creator_name = t.get("created_by_name") or ("Bosh Admin" if t.get("created_by") == ADMIN_ID else "")
        creator_info = f" (👤 {creator_name})" if creator_name else ""
        msg_list += f"<b>{idx}. #{t['test_code']}</b> — {t['title']}{creator_info}: <b>{sub_cnt} kishi</b>\n"

    buttons.append([InlineKeyboardButton(text="⬅️ Admin Panelga qaytish", callback_data="admin_panel_back")])
    msg_text = (
        "📊 <b>Mavjud Testlar va Ishtirokchilar Soni:</b>\n\n"
        f"{msg_list}\n"
        "<i>Batafsil natijalarni (Matn yoki PDF shaklida) olish uchun kerakli test kodini tanlang 👇</i>"
    )
    await message.answer(msg_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))



@admin_router.message(F.text == "⚙️ Admin Panel")
@admin_router.message(Command("admin"))
@admin_router.message(Command("dashboard"))
async def admin_panel_handler(message: Message):
    if not (await test_db.db(test_db.is_admin, message.from_user.id, ADMIN_ID)):
        await message.answer("⛔️ Bu bo'lim faqat bot administratori uchun!")
        return

    await message.answer(
        "⚙️ <b>ADMIN BOSHQARUV PANELI</b>\n\n"
        "Quyidagi bo'limlardan birini tanlang 👇",
        reply_markup=admin_menu_kb()
    )




@admin_router.message(Command("write_user"))
@admin_router.message(Command("contact"))
@admin_router.message(Command("msg"))
async def admin_contact_user_command(message: Message, state: FSMContext):
    """Admin uchun /user <id> yoki /user buyrug'i."""
    if not (await test_db.db(test_db.is_admin, message.from_user.id, ADMIN_ID)):
        await message.answer("⛔️ Bu buyruq faqat bot administratori uchun!")
        return

    parts = message.text.strip().split(maxsplit=1)
    if len(parts) > 1:
        query = parts[1].strip()
        user = await test_db.db(test_db.find_user, query)
        if user:
            await state.clear()
            try:
                clean_phone = re.sub(r"[^\d+]", "", str(user.get("phone") or ""))
                if not user.get("username") and clean_phone and clean_phone not in ["—", "-", ""]:
                    parts_name = (user.get("fullname") or "Foydalanuvchi").split(maxsplit=1)
                    fn = parts_name[0]
                    ln = parts_name[1] if len(parts_name) > 1 else ""
                    try:
                        await message.answer_contact(
                            phone_number=clean_phone,
                            first_name=fn,
                            last_name=ln
                        )
                    except Exception:
                        pass

                card_text, card_kb = build_user_contact_card(user)
                await message.answer(card_text, reply_markup=card_kb)
            except Exception as e:
                log.error(f"Xatolik foydalanuvchi kartasini chiqarishda: {e}", exc_info=True)
                uid = user.get('tg_id')
                fallback_kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✉️ Bot orqali xabar yozish", callback_data=f"adm_msg_user_{uid}")],
                    [InlineKeyboardButton(text="🔙 Admin panelga", callback_data="admin_back_to_menu")]
                ])
                await message.answer(
                    f"👤 <b>{html.escape(user.get('fullname', 'Foydalanuvchi'))}</b> (ID: <code>{uid}</code>)\n\n"
                    f"👉 <a href=\"tg://user?id={uid}\"><b>Telegram chatini ochish (ustiga bosing)</b></a>",
                    reply_markup=fallback_kb
                )
            return
        else:
            await message.answer(
                f"❌ <b>Foydalanuvchi topilmadi!</b>\n\n"
                f"Kiritilgan ma'lumot (<code>{html.escape(query)}</code>) bo'yicha foydalanuvchi bazadan topilmadi.\n\n"
                f"Iltimos, ID raqamini tekshirib qaytadan kiriting:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Bekor qilish", callback_data="admin_back_to_menu")]
                ])
            )
            await state.set_state(ContactUserState.waiting_for_user_id)
            return

    await state.set_state(ContactUserState.waiting_for_user_id)
    text = (
        "💬 <b>FOYDALANUVCHIGA YOZISH / PROFIL CHATIGA O'TISH</b>\n\n"
        "Foydalanuvchining <b>Telegram ID</b> raqamini kiriting:\n"
        "<i>(Shuningdek @username yoki telefon raqami orqali ham qidirishingiz mumkin)</i>\n\n"
        "Masalan: <code>8039427064</code> yoki <code>@foydalanuvchi</code>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Bekor qilish", callback_data="admin_back_to_menu")]
    ])
    await message.answer(text, reply_markup=kb)


@admin_router.callback_query(F.data == "admin_contact_user_prompt")
async def admin_contact_user_prompt_cb(call: CallbackQuery, state: FSMContext):
    """Admin panel orqali ID so'rash oynasi."""
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return

    await state.set_state(ContactUserState.waiting_for_user_id)
    text = (
        "💬 <b>FOYDALANUVCHIGA YOZISH / PROFIL CHATIGA O'TISH</b>\n\n"
        "Foydalanuvchining <b>Telegram ID</b> raqamini kiriting:\n"
        "<i>(Shuningdek @username yoki telefon raqami orqali ham qidirishingiz mumkin)</i>\n\n"
        "Masalan: <code>8039427064</code> yoki <code>@foydalanuvchi</code>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Bekor qilish", callback_data="admin_back_to_menu")]
    ])
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except Exception:
        await call.message.answer(text, reply_markup=kb)
    await call.answer()


@admin_router.message(ContactUserState.waiting_for_user_id)
async def admin_process_contact_user_id(message: Message, state: FSMContext):
    """Admin ID yoki Username kiritganda qidirib profil va chat havolalarini ko'rsatish."""
    if not (await test_db.db(test_db.is_admin, message.from_user.id, ADMIN_ID)):
        await state.clear()
        return

    query = message.text.strip() if message.text else ""
    if query in ["/admin", "/cancel", "/start"]:
        await state.clear()
        if query == "/admin":
            await admin_panel_handler(message)
        elif query == "/start":
            from .user import start_handler
            await start_handler(message, state)
        else:
            await message.answer("Bekor qilindi.", reply_markup=admin_menu_kb())
        return

    if not query:
        await message.answer("⚠️ Iltimos, Telegram ID yoki username matnini kiriting:")
        return

    user = await test_db.db(test_db.find_user, query)
    if not user:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Bekor qilish", callback_data="admin_back_to_menu")]
        ])
        await message.answer(
            f"❌ <b>Foydalanuvchi topilmadi!</b>\n\n"
            f"Kiritilgan ma'lumot (<code>{html.escape(query)}</code>) bo'yicha foydalanuvchi bazadan topilmadi.\n\n"
            f"Iltimos, Telegram ID yoki usernameni to'g'ri tekshirib qaytadan kiriting:",
            reply_markup=kb
        )
        return

    await state.clear()
    try:
        clean_phone = re.sub(r"[^\d+]", "", str(user.get("phone") or ""))
        if not user.get("username") and clean_phone and clean_phone not in ["—", "-", ""]:
            parts_name = (user.get("fullname") or "Foydalanuvchi").split(maxsplit=1)
            fn = parts_name[0]
            ln = parts_name[1] if len(parts_name) > 1 else ""
            try:
                await message.answer_contact(
                    phone_number=clean_phone,
                    first_name=fn,
                    last_name=ln
                )
            except Exception:
                pass

        card_text, card_kb = build_user_contact_card(user)
        await message.answer(card_text, reply_markup=card_kb)
    except Exception as e:
        log.error(f"Foydalanuvchi kartasini yuborishda xatolik: {e}", exc_info=True)
        uid = user.get('tg_id')
        fallback_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✉️ Bot orqali xabar yozish", callback_data=f"adm_msg_user_{uid}")],
            [InlineKeyboardButton(text="🔍 Boshqa foydalanuvchi qidirish", callback_data="admin_contact_user_prompt")],
            [InlineKeyboardButton(text="🔙 Admin panelga", callback_data="admin_back_to_menu")]
        ])
        await message.answer(
            f"👤 <b>{html.escape(user.get('fullname', 'Foydalanuvchi'))}</b> (ID: <code>{uid}</code>)\n\n"
            f"👉 <a href=\"tg://user?id={uid}\"><b>Telegram chatini ochish (ustiga bosing)</b></a>",
            reply_markup=fallback_kb
        )


@admin_router.callback_query(F.data.startswith("adm_view_user_"))
async def adm_view_user_cb(call: CallbackQuery, state: FSMContext):
    """Foydalanuvchi kartasini qayta ko'rsatish."""
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    await state.clear()
    target_tg_id = int(call.data.replace("adm_view_user_", ""))
    user = await test_db.db(test_db.find_user, target_tg_id)
    if not user:
        await call.answer("Foydalanuvchi topilmadi!", show_alert=True)
        return
    card_text, card_kb = build_user_contact_card(user)
    try:
        await call.message.edit_text(card_text, reply_markup=card_kb)
    except Exception:
        await call.message.answer(card_text, reply_markup=card_kb)
    await call.answer()


@admin_router.callback_query(F.data.startswith("adm_msg_user_"))
async def adm_msg_user_cb(call: CallbackQuery, state: FSMContext):
    """Foydalanuvchiga bot nomidan xabar yozish holatiga o'tish."""
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return

    target_tg_id = int(call.data.replace("adm_msg_user_", ""))
    user = await test_db.db(test_db.find_user, target_tg_id)
    fullname = user.get("fullname", f"ID: {target_tg_id}") if user else f"ID: {target_tg_id}"

    await state.set_state(ContactUserState.waiting_for_message)
    await state.update_data(target_tg_id=target_tg_id, target_fullname=fullname)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Bekor qilish", callback_data=f"adm_view_user_{target_tg_id}")]
    ])
    text = (
        f"✉️ <b>FOYDALANUVCHIGA XABAR YOZISH</b>\n\n"
        f"Kimga: <b>{fullname}</b> (ID: <code>{target_tg_id}</code>)\n\n"
        f"Foydalanuvchiga yubormoqchi bo'lgan xabaringizni yozing (matn, rasm yoki hujjat):\n"
        f"<i>Xabar foydalanuvchiga bot nomidan yetkaziladi.</i>"
    )
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except Exception:
        await call.message.answer(text, reply_markup=kb)
    await call.answer()


@admin_router.message(ContactUserState.waiting_for_message)
async def adm_send_user_message_handler(message: Message, state: FSMContext):
    """Admin yozgan xabarni foydalanuvchiga yetkazish."""
    if not (await test_db.db(test_db.is_admin, message.from_user.id, ADMIN_ID)):
        await state.clear()
        return

    text_val = message.text or message.caption or ""
    if text_val in ["/admin", "/cancel"]:
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=admin_menu_kb())
        return

    data = await state.get_data()
    target_tg_id = data.get("target_tg_id")
    target_fullname = data.get("target_fullname", "Foydalanuvchi")
    await state.clear()

    if not target_tg_id:
        await message.answer("⚠️ Ma'lumot topilmadi, iltimos qaytadan urinib ko'ring.", reply_markup=admin_menu_kb())
        return

    user_reply_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Adminga javob yozish (@eshmbetov)", url="https://t.me/eshmbetov")]
    ])

    try:
        if message.text:
            out_text = (
                f"📩 <b>ADMINISTRATSIYADAN XABAR:</b>\n\n"
                f"{message.text}\n\n"
                f"<i>Hurmat bilan, RASH TEST administratsiyasi</i>"
            )
            await bot.send_message(chat_id=target_tg_id, text=out_text, reply_markup=user_reply_kb)
        elif message.photo:
            caption = message.caption or ""
            out_caption = (
                f"📩 <b>ADMINISTRATSIYADAN XABAR:</b>\n\n"
                f"{caption}\n\n"
                f"<i>Hurmat bilan, RASH TEST administratsiyasi</i>"
            ) if caption else "📩 <b>ADMINISTRATSIYADAN XABAR:</b>\n\n<i>Hurmat bilan, RASH TEST administratsiyasi</i>"
            await bot.send_photo(chat_id=target_tg_id, photo=message.photo[-1].file_id, caption=out_caption, reply_markup=user_reply_kb)
        elif message.document:
            caption = message.caption or ""
            out_caption = (
                f"📩 <b>ADMINISTRATSIYADAN HUJJAT:</b>\n\n"
                f"{caption}\n\n"
                f"<i>Hurmat bilan, RASH TEST administratsiyasi</i>"
            ) if caption else "📩 <b>ADMINISTRATSIYADAN HUJJAT:</b>\n\n<i>Hurmat bilan, RASH TEST administratsiyasi</i>"
            await bot.send_document(chat_id=target_tg_id, document=message.document.file_id, caption=out_caption, reply_markup=user_reply_kb)
        else:
            await message.send_copy(chat_id=target_tg_id, reply_markup=user_reply_kb)

        user_info = await test_db.db(test_db.find_user, target_tg_id)
        uname = (user_info.get("username") or "").strip().lstrip("@") if user_info else ""
        buttons = []
        if uname:
            buttons.append([InlineKeyboardButton(text="💬 Shaxsiy profil chatini ochish ↗️", url=f"https://t.me/{uname}")])
        else:
            phone_val = re.sub(r"[^\d+]", "", str(user_info.get("phone") if user_info else "" or ""))
            if phone_val and phone_val not in ["—", "-", ""]:
                buttons.append([InlineKeyboardButton(text="📇 Telegram kontakt kartasini ko'rish (Chat) ↗️", callback_data=f"adm_send_contact_{target_tg_id}")])

        buttons.append([InlineKeyboardButton(text="✉️ Yana xabar yozish", callback_data=f"adm_msg_user_{target_tg_id}")])
        buttons.append([InlineKeyboardButton(text="🔍 Boshqa foydalanuvchi qidirish", callback_data="admin_contact_user_prompt")])
        buttons.append([InlineKeyboardButton(text="🔙 Admin panelga", callback_data="admin_back_to_menu")])

        await message.answer(
            f"✅ <b>Xabar muvaffaqiyatli yetkazildi!</b>\n\n"
            f"👤 Kimga: <b>{target_fullname}</b>\n"
            f"🆔 Telegram ID: <code>{target_tg_id}</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )
    except Exception as e:
        err_msg = str(e)
        log.warning(f"Error sending message to user {target_tg_id}: {err_msg}")
        user_info = await test_db.db(test_db.find_user, target_tg_id)
        uname = (user_info.get("username") or "").strip().lstrip("@") if user_info else ""
        buttons = []
        if uname:
            buttons.append([InlineKeyboardButton(text="💬 Shaxsiy profil chatini ochish ↗️", url=f"https://t.me/{uname}")])
        else:
            phone_val = re.sub(r"[^\d+]", "", str(user_info.get("phone") if user_info else "" or ""))
            if phone_val and phone_val not in ["—", "-", ""]:
                buttons.append([InlineKeyboardButton(text="📇 Telegram kontakt kartasini ko'rish (Chat) ↗️", callback_data=f"adm_send_contact_{target_tg_id}")])
        buttons.append([InlineKeyboardButton(text="🔍 Boshqa foydalanuvchi qidirish", callback_data="admin_contact_user_prompt")])
        buttons.append([InlineKeyboardButton(text="🔙 Admin panelga", callback_data="admin_back_to_menu")])

        if "forbidden" in err_msg.lower() or "blocked" in err_msg.lower():
            await message.answer(
                f"❌ <b>Xabar yetkazilmadi!</b>\n\n"
                f"Foydalanuvchi (<b>{target_fullname}</b>, ID: <code>{target_tg_id}</code>) botni bloklagan yoki to'xtatgan.\n\n"
                f"Siz Telegram ilovasining o'zida profiliga o'tishingiz mumkin.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
            )
        else:
            await message.answer(
                f"❌ <b>Xatolik yuz berdi:</b> {html.escape(err_msg)}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
            )


@admin_router.callback_query(F.data.startswith("adm_send_contact_"))
async def adm_send_contact_cb(call: CallbackQuery):
    """Admin uchun foydalanuvchining Telegram kontakt kartasini chatga yuborish."""
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        await call.answer("⛔️ Ruxsat yo'q!", show_alert=True)
        return
    parts = call.data.split("_")
    try:
        target_tg_id = int(parts[3])
    except Exception:
        await call.answer("Xatolik: ID topilmadi", show_alert=True)
        return
    user = await test_db.db(test_db.find_user, target_tg_id)
    if not user:
        await call.answer("Foydalanuvchi topilmadi!", show_alert=True)
        return
    phone = user.get("phone", "")
    fullname = user.get("fullname", "Foydalanuvchi")
    clean_phone = re.sub(r"[^\d+]", "", str(phone or ""))
    if not clean_phone or clean_phone in ["—", "-", ""]:
        await call.answer("Foydalanuvchining telefon raqami mavjud emas!", show_alert=True)
        return

    await call.answer("Kontakt kartasi yuborilmoqda...")
    try:
        parts_name = fullname.split(maxsplit=1)
        first_name = parts_name[0]
        last_name = parts_name[1] if len(parts_name) > 1 else ""

        contact_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✉️ Bot orqali xabar yozish", callback_data=f"adm_msg_user_{target_tg_id}")],
            [InlineKeyboardButton(text="🔙 Foydalanuvchi ma'lumotlariga qaytish", callback_data=f"adm_view_user_{target_tg_id}")]
        ])

        await call.message.answer_contact(
            phone_number=clean_phone,
            first_name=first_name,
            last_name=last_name,
            reply_markup=contact_kb
        )
    except Exception as e:
        log.error(f"Kontakt kartasini yuborishda xatolik: {e}")
        await call.message.answer(f"⚠️ Kontaktni yuborishda xatolik: {e}")


# 1. Yangi test yaratish (Faqat Admin Mini App orqali)
@admin_router.callback_query(F.data == "admin_add_test")
async def admin_start_add_test(call: CallbackQuery, state: FSMContext):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    await state.clear()
    from .user import admin_webapp_info_cb
    await admin_webapp_info_cb(call)


# 2. Testlarni boshqarish (O'chirish, To'xtatish/Yoqish, Vaqt)
@admin_router.callback_query(F.data == "admin_manage_tests")
async def admin_manage_tests(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return

    tests = await test_db.db(test_db.get_all_tests)
    if not tests:
        text = "ℹ️ Hozircha bazada birorta ham test yo'q."
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Admin Menyuga qaytish", callback_data="admin_back_to_menu")]
        ])
        try:
            await call.message.edit_text(text, reply_markup=kb)
        except Exception:
            await call.message.answer(text, reply_markup=kb)
        await call.answer()
        return

    text = (
        "📋 <b>Barcha testlar ro'yxati va boshqaruvi:</b>\n\n"
        "<i>Boshqarish (to'xtatish / vaqt / o'chirish) uchun kerakli testni tanlang 👇</i>\n\n"
    )
    buttons = []
    for idx, t in enumerate(tests, 1):
        status_icon = "🟢" if t["is_active"] == 1 else "🔴"
        time_str = f"{t['time_limit_min']} daqiqa" if t.get("time_limit_min", 0) > 0 else "Cheksiz"
        creator_name = t.get("created_by_name") or ("Bosh Admin" if t.get("created_by") == ADMIN_ID else "")
        creator_info = f" (👤 {creator_name})" if creator_name else ""
        text += f"<b>{idx}. #{t['test_code']}</b> — {t['title']} ({status_icon}, ⏱ {time_str}){creator_info}\n"
        buttons.append([InlineKeyboardButton(text=f"{status_icon} #{t['test_code']} — {t['title'][:22]}", callback_data=f"adm_mng_test_{t['id']}")])

    buttons.append([InlineKeyboardButton(text="🔙 Admin Menyuga qaytish", callback_data="admin_back_to_menu")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except Exception:
        await call.message.answer(text, reply_markup=kb)
    await call.answer()

@admin_router.callback_query(F.data.startswith("adm_mng_test_"))
async def admin_manage_test_card(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    test_id = int(call.data.split("_")[3])
    t = test_db.get_test_by_id(test_id)
    if not t:
        await call.answer("Test topilmadi!", show_alert=True)
        return

    status_str = "🟢 Faol (O'quvchilarga ko'rinadi)" if t["is_active"] == 1 else "🔴 To'xtatilgan (Yashiringan)"
    time_str = f"{t['time_limit_min']} daqiqa" if t.get("time_limit_min", 0) > 0 else "Cheksiz"
    toggle_btn_text = "🔴 To'xtatish" if t["is_active"] == 1 else "🟢 Faollashtirish"

    has_pdf_str = f"📄 <b>PDF:</b> {t.get('pdf_file_name') or 'Biriktirilgan ✅'}\n" if t.get("pdf_file_id") else "📄 <b>PDF:</b> ❌ Yuklanmagan\n"
    pdf_btn_text = "📄 PDF almashtirish" if t.get("pdf_file_id") else "📥 PDF yuklash"

    creator_str = t.get('created_by_name') or ('Bosh Admin' if t.get('created_by') == ADMIN_ID else 'Admin')
    card_text = (
        f"📖 <b>{t['title']}</b> (<code>#{t['test_code']}</code>)\n"
        f"👤 <b>Yaratuvchi:</b> {creator_str}\n"
        f"📌 <b>Fan:</b> {t.get('subject', 'Matematika')}\n"
        f"📊 <b>Holati:</b> {status_str}\n"
        f"⏱ <b>Vaqt chegarasi:</b> {time_str}\n"
        f"{has_pdf_str}\n"
        f"<i>Boshqarish uchun quyidagi amallardan birini tanlang:</i>"
    )

    edit_keys_url = f"{get_webapp_url()}/admin.html?edit_test_id={t['id']}"
    kb_rows = [
        [make_webapp_button("✏️ Kalitlarni tahrirlash (Mini App)", edit_keys_url)],
        [InlineKeyboardButton(text=toggle_btn_text, callback_data=f"toggle_test_{t['id']}")],
        [InlineKeyboardButton(text=pdf_btn_text, callback_data=f"ask_pdf_{t['id']}")],
        [InlineKeyboardButton(text="🗑 O'chirish", callback_data=f"del_test_confirm_{t['id']}")],
        [InlineKeyboardButton(text="⬅️ Testlar ro'yxatiga qaytish", callback_data="admin_manage_tests")]
    ]

    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)

    try:
        await call.message.edit_text(card_text, reply_markup=kb)
    except Exception:
        await call.message.answer(card_text, reply_markup=kb)
    await call.answer()

# Test holatini o'zgartirish (Toggle Active)
@admin_router.callback_query(F.data.startswith("toggle_test_"))
async def admin_toggle_test_cb(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    test_id = int(call.data.split("_")[2])
    new_status = await test_db.db(test_db.toggle_test_status, test_id)
    if new_status is not None:
        status_text = "🟢 Faol" if new_status == 1 else "🔴 To'xtatildi"
        await call.answer(f"Test holati: {status_text}", show_alert=True)
        call.data = f"adm_mng_test_{test_id}"
        await admin_manage_test_card(call)
    else:
        await call.answer("Xatolik yuz berdi!", show_alert=True)

# Test vaqtini belgilash prompt
@admin_router.callback_query(F.data.startswith("set_time_prompt_"))
async def admin_set_time_prompt(call: CallbackQuery, state: FSMContext):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    test_id = int(call.data.split("_")[3])
    await state.update_data(target_test_id=test_id)
    await state.set_state(SetTimeLimitState.time_limit)
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"adm_mng_test_{test_id}")]
    ])
    text = (
        "⏱ <b>Test uchun vaqt chegarasini daqiqalarda kiriting:</b>\n\n"
        "<i>(Masalan: 120, 180 yoki cheksiz bo'lishi uchun 0 deb yozing)</i>"
    )
    try:
        await call.message.edit_text(text, reply_markup=cancel_kb)
    except Exception:
        await call.message.answer(text, reply_markup=cancel_kb)
    await call.answer()

@admin_router.message(SetTimeLimitState.time_limit)
async def admin_save_time_limit(message: Message, state: FSMContext):
    data = await state.get_data()
    test_id = data.get("target_test_id")
    try:
        minutes = int(message.text.strip())
        await test_db.db(test_db.update_test_time_limit, test_id, minutes)
        await state.clear()
        time_text = f"{minutes} daqiqa" if minutes > 0 else "Cheksiz"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Testga qaytish", callback_data=f"adm_mng_test_{test_id}")],
            [InlineKeyboardButton(text="🔙 Admin Menyuga", callback_data="admin_back_to_menu")]
        ])
        await message.answer(f"✅ Test vaqt chegarasi <b>{time_text}</b> qilib belgilandi!", reply_markup=kb)
    except ValueError:
        await message.answer("⚠️ Iltimos, faqat butun son kiriting (masalan: 120 yoki 0):")

# Testni o'chirish
@admin_router.callback_query(F.data.startswith("del_test_confirm_"))
async def admin_delete_test_cb(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    test_id = int(call.data.split("_")[3])
    ok = await test_db.db(test_db.delete_test, test_id)
    if ok:
        await call.answer("🗑 Test muvaffaqiyatli o'chirildi!", show_alert=True)
    else:
        await call.answer("❌ Testni o'chirishda xatolik yuz berdi!", show_alert=True)
    await admin_manage_tests(call)

# ── TEZKOR FOYDALANUVCHI TASDIQLASH / RAD ETISH HANDLERLARI ──
@admin_router.callback_query(F.data.startswith("user_quick_approve_"))
async def user_quick_approve_cb(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        await call.answer("Siz admin emassiz!", show_alert=True)
        return
    uid = int(call.data.split("_")[3])
    u = await test_db.db(test_db.get_user, uid)

    # Agar allaqachon hal qilingan bo'lsa — xabar chiqar, qayta ishlamasin
    if u and u.get("status") in ["approved", "rejected"]:
        status_map = {
            "approved": "✅ Bu foydalanuvchi allaqachon boshqa admin tomonidan RUXSAT BERILGAN!",
            "rejected": "❌ Bu foydalanuvchi allaqachon RAD ETILGAN!"
        }
        await call.answer(status_map.get(u["status"], "Allaqachon hal qilingan!"), show_alert=True)
        # Ushbu admindagi tugmalarni ham o'chirib qo'y
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    uname = u["fullname"] if u else f"ID: {uid}"
    admin_name = call.from_user.full_name or "Admin"

    await test_db.db(test_db.approve_user, uid)

    # Ushbu admindagi xabarni yangilash
    try:
        await call.message.edit_text(
            f"{call.message.text}\n\n✅ <b>RUXSAT BERILDI!</b>\nTasdiqladi: <b>{admin_name}</b>",
            reply_markup=None
        )
    except Exception:
        pass

    # Boshqa BARCHA adminlarga xabar yuborish (tugmalarsiz)
    done_text = (
        f"✅ <b>ARIZA HAL QILINDI</b>\n\n"
        f"👤 <b>{uname}</b> foydalanuvchisiga\n"
        f"<b>{admin_name}</b> tomonidan ruxsat berildi.\n\n"
        f"<i>Siz hech narsa qilishingiz shart emas.</i>"
    )
    for adm_id in get_all_admin_ids():
        if adm_id == call.from_user.id:
            continue  # O'ziga yubormasin
        try:
            await bot.send_message(chat_id=adm_id, text=done_text)
        except Exception:
            pass

    # Foydalanuvchiga xabar
    try:
        await bot.send_message(
            chat_id=uid,
            text=(
                f"🎉 <b>Xushxabar, hurmatli {uname}!</b>\n\n"
                f"Admin sizga test tizimidan to'liq foydalanishga ruxsat berdi! ✅\n"
                f"Endi bemalol barcha testlarni yechishingiz, natijalarni ko'rishingiz va mini ilovadan foydalanishingiz mumkin 👇"
            ),
            reply_markup=main_menu_kb(uid)
        )
    except Exception as e:
        log.warning(f"Foydalanuvchiga ruxsat xabarini yuborishda xatolik: {e}")
    await call.answer("✅ Foydalanuvchiga ruxsat berildi!", show_alert=True)

@admin_router.callback_query(F.data.startswith("user_quick_reject_"))
async def user_quick_reject_cb(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        await call.answer("Siz admin emassiz!", show_alert=True)
        return
    uid = int(call.data.split("_")[3])
    u = await test_db.db(test_db.get_user, uid)

    # Agar allaqachon hal qilingan bo'lsa
    if u and u.get("status") in ["approved", "rejected"]:
        status_map = {
            "approved": "✅ Bu foydalanuvchi allaqachon RUXSAT BERILGAN!",
            "rejected": "❌ Bu foydalanuvchi allaqachon RAD ETILGAN!"
        }
        await call.answer(status_map.get(u["status"], "Allaqachon hal qilingan!"), show_alert=True)
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    uname = u["fullname"] if u else f"ID: {uid}"
    admin_name = call.from_user.full_name or "Admin"

    await test_db.db(test_db.reject_user, uid)

    # Ushbu admindagi xabarni yangilash
    try:
        await call.message.edit_text(
            f"{call.message.text}\n\n❌ <b>RAD ETILDI!</b>\nRad etdi: <b>{admin_name}</b>",
            reply_markup=None
        )
    except Exception:
        pass

    # Boshqa BARCHA adminlarga xabar yuborish (tugmalarsiz)
    done_text = (
        f"❌ <b>ARIZA HAL QILINDI</b>\n\n"
        f"👤 <b>{uname}</b> foydalanuvchisining arizasi\n"
        f"<b>{admin_name}</b> tomonidan rad etildi.\n\n"
        f"<i>Siz hech narsa qilishingiz shart emas.</i>"
    )
    for adm_id in get_all_admin_ids():
        if adm_id == call.from_user.id:
            continue
        try:
            await bot.send_message(chat_id=adm_id, text=done_text)
        except Exception:
            pass

    # Foydalanuvchiga xabar
    try:
        await bot.send_message(
            chat_id=uid,
            text="❌ <b>Kechirasiz, sizning botdan foydalanish arizangiz rad etildi.</b>\n\nMurojaat uchun: @eshmbetov"
        )
    except Exception:
        pass
    await call.answer("❌ Ariza rad etildi!", show_alert=True)


@admin_router.callback_query(F.data.startswith("user_req_access_"))
async def user_req_access_cb(call: CallbackQuery):
    """O'quvchi tomonidan ruxsat so'rash tugmasi bosilganda - adminga so'rov yuborish (avto-tasdiqlash EMAS)"""
    uid = int(call.data.split("_")[3])
    u = await test_db.db(test_db.get_user, uid)
    can_req, diff_h = await test_db.db(test_db.can_request_access, uid, cooldown_hours=6)
    if not can_req:
        await call.answer("⏳ So'rovingiz ko'rib chiqilmoqda. Iltimos, admin javobini kuting.", show_alert=True)
        return

    await test_db.db(test_db.touch_admin_deleted_request, uid)
    uname = u["fullname"] if u else f"ID: {uid}"
    username = f"@{u['username']}" if (u and u.get('username')) else "Mavjud emas"

    alert_text = (
        f"🔔 <b>Yangi kirish so'rovi (Ilgari o'chirilgan foydalanuvchi):</b>\n\n"
        f"👤 <b>Foydalanuvchi:</b> {html.escape(uname)}\n"
        f"🔗 <b>Username:</b> {username}\n"
        f"🆔 <b>Telegram ID:</b> <code>{uid}</code>\n"
        f"🕒 <b>Vaqt:</b> {format_uzb_time()}\n\n"
        f"Ushbu foydalanuvchiga botdan foydalanishga ruxsat berasizmi?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Ruxsat berish", callback_data=f"user_quick_approve_{uid}"),
            InlineKeyboardButton(text="❌ Rad etish", callback_data=f"user_quick_reject_{uid}")
        ]
    ])
    for adm_id in get_all_admin_ids():
        try:
            await bot.send_message(chat_id=adm_id, text=alert_text, reply_markup=kb)
        except Exception:
            pass

    try:
        await call.message.edit_text(
            "⏳ <b>So'rovingiz adminga yuborildi, ruxsat berilishini kuting.</b>\n\n"
            "Admin tasdiqlaganidan so'ng sizga xabar beriladi.",
            reply_markup=None
        )
    except Exception:
        pass
    await call.answer("⏳ So'rovingiz adminga yuborildi!", show_alert=True)

@admin_router.callback_query(F.data.startswith("ask_pdf_"))
async def ask_pdf_cb(call: CallbackQuery, state: FSMContext):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    test_id = int(call.data.split("_")[2])
    await state.update_data(test_id=test_id)
    await state.set_state(UploadPostPdfState.pdf_file)
    await call.message.edit_text(
        f"{call.message.text}\n\n✅ <i>Siz PDF yuklashni tanladingiz.</i>\n\n"
        f"📥 <b>Iltimos, test uchun PDF faylni yuboring:</b>",
        reply_markup=None
    )

@admin_router.callback_query(F.data.startswith("no_pdf_"))
async def no_pdf_cb(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    await call.message.edit_text(
        f"{call.message.text}\n\n❌ <i>PDF fayl yuklanmadi. Test asosiysiz qabul qilindi.</i>",
        reply_markup=None
    )

@admin_router.message(UploadPostPdfState.pdf_file, F.document)
async def process_post_create_pdf(message: Message, state: FSMContext):
    if not (await test_db.db(test_db.is_admin, message.from_user.id, ADMIN_ID)):
        await message.answer("Sizda ushbu amalni bajarish uchun admin ruxsati yo'q.")
        await state.clear()
        return

    data = await state.get_data()
    test_id = data.get("test_id")
    if not test_id:
        await message.answer("Xatolik! Test topilmadi.")
        await state.clear()
        return

    pdf_file_id = message.document.file_id
    pdf_file_name = message.document.file_name

    await test_db.db(test_db.update_test_pdf, test_id, pdf_file_id, pdf_file_name)
    await message.answer(f"✅ <b>PDF fayl muvaffaqiyatli biriktirildi!</b>\n📄 Fayl: {pdf_file_name}")
    await state.clear()


# ── PDF WATERMARK QAYTA ISHLASH (FAQAT ADMINLAR UCHUN) ──
@admin_router.callback_query(F.data == "admin_pdf_watermark_info")
@admin_router.message(Command("pdf"))
@admin_router.message(Command("watermark"))
async def admin_pdf_watermark_info_handler(event: Message | CallbackQuery, state: FSMContext):
    user_tg_id = event.from_user.id
    if not ((await test_db.db(test_db.is_admin, user_tg_id, ADMIN_ID)) or user_tg_id in get_config_admin_ids()):
        if isinstance(event, CallbackQuery):
            await event.answer("⛔️ Faqat administratorlar uchun!", show_alert=True)
        else:
            await event.answer("⛔️ Ushbu bo'lim faqat administratorlar uchun!")
        return

    await state.set_state(AdminPdfWatermarkState.waiting_for_pdf)

    text = (
        "📄 <b>PDF WATERMARK VA QAYTA ISHLASH XIZMATI</b>\n\n"
        "Ushbu bo'lim orqali istalgan test yoki darslik PDF faylini botga yuborishingiz mumkin.\n\n"
        "✨ <b>Tizim nimalar qiladi:</b>\n"
        "• Har bir sahifani tiniq oq fonga ko'chiradi (PyMuPDF)\n"
        "• Sahifa qoq markaziga Akbarali Boymirzayevning rasmiy shaffof logotipini tushiradi\n"
        "• Chop etish va tarqatishga to'liq tayyor PDF fayl ko'rinishida sizga yuboradi!\n\n"
        "👇 <b>Iltimos, qayta ishlanadigan PDF faylni botga yuboring (fayl / document sifatida):</b>"
    )
    back_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔙 Admin panelga qaytish", callback_data="admin_panel_back")
    ]])

    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=back_kb)
    else:
        await event.answer(text, reply_markup=back_kb)


@admin_router.message(AdminPdfWatermarkState.waiting_for_pdf, F.document)
@admin_router.message(F.document)
async def admin_pdf_watermark_handler(message: Message, state: FSMContext):
    # Agar post uchun PDF biriktirish holatida bo'lsa, o'sha handler ishlasin
    cur_state = await state.get_state()
    if cur_state == UploadPostPdfState.pdf_file:
        await process_post_create_pdf(message, state)
        return

    doc = message.document
    fname = (doc.file_name or "").lower()
    mime = (doc.mime_type or "").lower()
    is_pdf = fname.endswith(".pdf") or ("pdf" in mime)
    if not is_pdf:
        return

    user_tg_id = message.from_user.id
    is_adm = (await test_db.db(test_db.is_admin, user_tg_id, ADMIN_ID)) or (user_tg_id in get_config_admin_ids())
    if not is_adm:
        await message.answer("⛔️ Sizda ushbu funksiyadan foydalanish huquqi yo'q.")
        return

    # Admin PDF yuborgan holatda:
    status_msg = await message.answer(
        "⏳ <b>PDF qabul qilindi. Watermark qo'yish jarayoni boshlanmoqda...</b>\n"
        "<i>PyMuPDF va Pillow orqali har bir sahifa oq fonga ko'chirilib, markaziga shaffof logotip joylashtirilmoqda. Iltimos, kuting...</i>"
    )

    temp_id = uuid.uuid4().hex
    raw_filename = doc.file_name or "document.pdf"
    base_name = os.path.splitext(raw_filename)[0]
    input_path = f"/tmp/input_{temp_id}.pdf"
    output_path = f"/tmp/watermarked_{temp_id}.pdf"

    try:
        await message.bot.download(doc, destination=input_path)

        logo_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logo.png")
        if not os.path.exists(logo_file):
            logo_file = "logo.png"

        async with HEAVY_TASKS_SEMAPHORE:
            total_pages = await process_pdf_watermark_async(
                input_pdf_path=input_path,
                output_pdf_path=output_path,
                logo_path=logo_file,
                opacity=0.22
            )

        output_filename = f"{base_name}_watermarked.pdf"
        doc_to_send = FSInputFile(output_path, filename=output_filename)

        caption = (
            f"✅ <b>PDF muvaffaqiyatli tayyorlandi!</b>\n\n"
            f"📄 <b>Fayl:</b> <code>{html.escape(output_filename)}</code>\n"
            f"📑 <b>Sahifalar soni:</b> {total_pages} ta\n"
            f"🎨 <b>Watermark:</b> Akbarali Boymirzayev logotipi sahifa markaziga joylashtirildi."
        )

        await message.answer_document(
            document=doc_to_send,
            caption=caption
        )
        await state.clear()
        try:
            await status_msg.delete()
        except Exception:
            pass
    except Exception as ex:
        log.exception(f"PDF qayta ishlashda xatolik: {ex}")
        await message.answer(f"❌ <b>PDF qayta ishlashda xatolik yuz berdi:</b>\n<code>{html.escape(str(ex))}</code>")
    finally:
        for p in [input_path, output_path]:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass


# 3. Foydalanuvchilar boshqaruvi to'liq Asosiy Web Ilovaga (Main App) ko'chirildi
@admin_router.callback_query(F.data.in_(["admin_view_users", "admin_webapp_redirect_info"]))
async def admin_view_users_redirect_cb(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    text = (
        "🌐 <b>Foydalanuvchilar boshqaruvi Asosiy Web Ilovaga (Main App) ko'chirildi!</b>\n\n"
        "Foydalanuvchilar bilan bog'liq barcha amallar (ko'rish, qidirish, ruxsat berish, cheklash, bloklash, o'chirish va barchani cheklash) "
        "endi <b>Asosiy ilovaning «Admin»</b> bo'limida amalga oshiriladi.\n\n"
        "Quyidagi tugma orqali ilovani ochishingiz mumkin 👇"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_webapp_button("🚀 Foydalanuvchilarni boshqarish (Web App)", f"{get_webapp_url()}/app.html?tab=admin&tg_id={ADMIN_ID}")],
        [InlineKeyboardButton(text="🔙 Admin Menyuga qaytish", callback_data="admin_back_to_menu")]
    ])
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except Exception:
        await call.message.answer(text, reply_markup=kb)
    await call.answer()

@admin_router.callback_query(F.data.startswith("adm_user_card_") | F.data.startswith("adm_act_") | F.data.startswith("admin_restrict_all_"))
async def adm_legacy_users_redirect_cb(call: CallbackQuery):
    await call.answer("Foydalanuvchilar boshqaruvi Web Appga ko'chirilgan!", show_alert=True)

# 4. Adminlar boshqaruvi
@admin_router.callback_query(F.data == "admin_manage_admins")
async def admin_manage_admins_cb(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return

    admins = await test_db.db(test_db.get_all_admins)
    text = "👑 <b>Adminlar ro'yxati:</b>\n\n"
    for idx, a in enumerate(admins, 1):
        is_super = " (Bosh Admin)" if a["tg_id"] == ADMIN_ID else ""
        text += f"<b>{idx}. {a.get('fullname', 'Admin')}</b> — <code>{a['tg_id']}</code>{is_super}\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Yangi admin qo'shish", callback_data="admin_add_new_prompt")],
        [InlineKeyboardButton(text="🔙 Admin Menyuga qaytish", callback_data="admin_back_to_menu")]
    ])

    try:
        await call.message.edit_text(text, reply_markup=kb)
    except Exception:
        await call.message.answer(text, reply_markup=kb)
    await call.answer()

@admin_router.callback_query(F.data.in_(["admin_back_to_menu", "admin_panel_back"]))
async def admin_back_to_menu_cb(call: CallbackQuery, state: FSMContext = None):
    if state:
        await state.clear()
    text = (
        "⚙️ <b>ADMIN BOSHQARUV PANELI</b>\n\n"
        "Quyidagi bo'limlardan birini tanlang 👇"
    )
    try:
        await call.message.edit_text(text, reply_markup=admin_menu_kb())
    except Exception:
        await call.message.answer(text, reply_markup=admin_menu_kb())
    await call.answer()

@admin_router.callback_query(F.data == "admin_add_new_prompt")
async def admin_add_new_prompt_cb(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        await call.answer("Faqat Bosh Admin yangi admin tayinlashi mumkin!", show_alert=True)
        return

    await state.set_state(AddAdminState.tg_id_or_user)
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Bekor qilish", callback_data="admin_manage_admins")]
    ])
    text = (
        "👑 <b>Yangi admin qo'shish:</b>\n\n"
        "Admin qilmoqchi bo'lgan foydalanuvchining <b>Telegram ID</b> raqamini kiriting:\n"
        "<i>(Masalan: 123456789)</i>"
    )
    try:
        await call.message.edit_text(text, reply_markup=cancel_kb)
    except Exception:
        await call.message.answer(text, reply_markup=cancel_kb)
    await call.answer()

@admin_router.message(AddAdminState.tg_id_or_user)
async def admin_save_new_admin(message: Message, state: FSMContext):
    try:
        new_tg_id = int(message.text.strip())
        user_info = await test_db.db(test_db.get_user, new_tg_id)
        fullname = user_info["fullname"] if user_info else "Admin"
        username = user_info["username"] if user_info else ""

        await test_db.db(test_db.add_admin, new_tg_id, fullname, username, added_by=message.from_user.id)
        await state.clear()
        await message.answer(
            f"✅ <b>Yangi admin tayinlandi!</b>\n\n"
            f"👤 <b>Ism:</b> {fullname}\n"
            f"🆔 <b>Telegram ID:</b> <code>{new_tg_id}</code>\n\n"
            f"Endi ushbu foydalanuvchi ham Admin Panelga kira oladi.",
            reply_markup=admin_menu_kb()
        )
    except ValueError:
        await message.answer("⚠️ Iltimos, to'g'ri Telegram ID (raqam) kiriting:")

# ── TEXNIK PROFILAKTIKA VA BROADCAST (XABAR YUBORISH) BOSHQARUVI ──

# ── (Broadcast va xabar boshqaruvi funksiyalari services/broadcast.py moduliga ko'chirildi) ──


# 1. Texnik rejimni yoqish / o'chirish so'rovi
@admin_router.callback_query(F.data == "admin_toggle_maint_prompt")
async def admin_toggle_maint_prompt_cb(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        await call.answer("⛔️ Faqat Bosh Admin texnik profilaktika rejimini boshqarishi mumkin!", show_alert=True)
        return

    is_maint = await test_db.db(test_db.is_maintenance_mode)
    if not is_maint:
        text = (
            "🛠 <b>TEXNIK PROFILAKTIKA REJIMINI YOQISH</b>\n\n"
            "⚠️ <b>Eslatma:</b>\n"
            "• Ushbu rejim yoqilganda sizdan (Bosh Admin) tashqari <b>hech kim</b> — "
            "na oddiy o'quvchilar va na tayinlangan adminlar botdan foydalana olmaydi.\n"
            "• Botga yozgan har qanday foydalanuvchiga: <i>«Hozirda botda texnik profilaktika ketmoqda...»</i> xabari chiqadi.\n\n"
            "Quyidagi amallardan birini tanlang 👇"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 Yoqish va o'quvchilarga ogohlantirish yuborish", callback_data="adm_maint_set_on_notify")],
            [InlineKeyboardButton(text="🤫 Faqat Yoqish (xabarsiz)", callback_data="adm_maint_set_on_silent")],
            [InlineKeyboardButton(text="⬅️ Bekor qilish / Orqaga", callback_data="admin_back_to_menu")]
        ])
    else:
        text = (
            "✅ <b>TEXNIK PROFILAKTIKA REJIMINI O'CHIRISH</b>\n\n"
            "Tizim yana barcha o'quvchilar va adminlar uchun to'liq ochiladi va odatdagidek ishlay boshlaydi.\n\n"
            "Quyidagi amallardan birini tanlang 👇"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 O'chirish va barchaga xushxabar berish", callback_data="adm_maint_set_off_notify")],
            [InlineKeyboardButton(text="🤫 Faqat O'chirish (xabarsiz)", callback_data="adm_maint_set_off_silent")],
            [InlineKeyboardButton(text="⬅️ Bekor qilish / Orqaga", callback_data="admin_back_to_menu")]
        ])

    try:
        await call.message.edit_text(text, reply_markup=kb)
    except Exception:
        await call.message.answer(text, reply_markup=kb)
    await call.answer()

@admin_router.callback_query(F.data.in_(["adm_maint_set_on_silent", "adm_maint_set_on_notify"]))
async def adm_maint_set_on_cb(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        await call.answer("Faqat Bosh Admin!", show_alert=True)
        return
    await test_db.db(test_db.set_maintenance_mode, True)
    if call.data == "adm_maint_set_on_notify":
        await call.answer("⏳ Xabar tarqatilmoqda...")
        status_msg = await call.message.answer("⏳ O'quvchilarga texnik profilaktika boshlanganligi haqida xabar yuborilmoqda...")
        sent, fail, batch_id = await send_broadcast_to_users(message_text=MAINT_START_TEXT)
        await status_msg.edit_text(
            f"🛠 <b>Texnik rejim YOQILDI va xabar tarqatildi!</b>\n\n"
            f"📨 <b>Yetkazildi:</b> {sent} ta\n"
            f"⚠️ <b>Yetkazilmadi:</b> {fail} ta\n\n"
            f"<i>Endi botdan faqat siz (Bosh Admin) foydalana olasiz.</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗑 Ushbu xabarni barchadan o'chirish", callback_data=f"adm_bc_del_{batch_id}")],
                [InlineKeyboardButton(text="⚙️ Admin panelga", callback_data="admin_back_to_menu")]
            ])
        )
    else:
        await call.answer("🛠 Texnik rejim yoqildi (xabarsiz)!", show_alert=True)
        await call.message.edit_text(
            "⚙️ <b>ADMIN BOSHQARUV PANELI</b>\n\n"
            "🔴 <b>Texnik profilaktika rejimi YOQILGAN.</b> Faqat siz (Bosh Admin) foydalana olasiz.\n\n"
            "Quyidagi bo'limlardan birini tanlang 👇",
            reply_markup=admin_menu_kb()
        )

@admin_router.callback_query(F.data.in_(["adm_maint_set_off_silent", "adm_maint_set_off_notify"]))
async def adm_maint_set_off_cb(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        await call.answer("Faqat Bosh Admin!", show_alert=True)
        return
    await test_db.db(test_db.set_maintenance_mode, False)
    if call.data == "adm_maint_set_off_notify":
        await call.answer("⏳ Xabar tarqatilmoqda...")
        status_msg = await call.message.answer("⏳ O'quvchilarga texnik ishlar yakunlanganligi haqida xabar yuborilmoqda...")
        sent, fail, batch_id = await send_broadcast_to_users(message_text=MAINT_END_TEXT)
        await status_msg.edit_text(
            f"✅ <b>Texnik rejim O'CHIRILDI va xushxabar tarqatildi!</b>\n\n"
            f"📨 <b>Yetkazildi:</b> {sent} ta\n"
            f"⚠️ <b>Yetkazilmadi:</b> {fail} ta\n\n"
            f"<i>Bot barcha o'quvchilar va adminlar uchun yana to'liq faol.</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗑 Ushbu xabarni barchadan o'chirish", callback_data=f"adm_bc_del_{batch_id}")],
                [InlineKeyboardButton(text="⚙️ Admin panelga", callback_data="admin_back_to_menu")]
            ])
        )
    else:
        await call.answer("✅ Texnik rejim o'chirildi (xabarsiz)!", show_alert=True)
        await call.message.edit_text(
            "⚙️ <b>ADMIN BOSHQARUV PANELI</b>\n\n"
            "🟢 <b>Texnik profilaktika rejimi O'CHIRILGAN.</b> Bot barcha uchun ochiq.\n\n"
            "Quyidagi bo'limlardan birini tanlang 👇",
            reply_markup=admin_menu_kb()
        )

# ── YANGI FOYDALANUVCHILAR QABULINI BOSHQARISH (OCHISH / YOPISH) ──
@admin_router.callback_query(F.data == "admin_toggle_registration")
async def admin_toggle_registration_cb(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        await call.answer("⛔️ Faqat administrator uchun!", show_alert=True)
        return

    current = await test_db.db(test_db.is_registration_open)
    new_state = not current
    await test_db.db(test_db.set_registration_open, new_state)

    if new_state:
        alert_msg = "✅ Yangi o'quvchilar qabuli OCHILDI!\nEndi yangi o'quvchilar yana bemalol ro'yxatdan o'tishlari mumkin."
    else:
        alert_msg = "🔴 Yangi o'quvchilar qabuli YOPILDI!\nYangi foydalanuvchilarga qabul yopiqligi bildiriladi va 15 minutlik cheklov qo'yiladi."

    await call.answer(alert_msg, show_alert=True)
    try:
        await call.message.edit_reply_markup(reply_markup=admin_menu_kb())
    except Exception:
        pass

# 2. O'quvchilarga xabar yuborish menyusi
def get_broadcast_started_text() -> str:
    active_tests = test_db.get_active_tests()
    test_code = ""
    if active_tests:
        test_code = active_tests[0].get("test_code", "")
    if not test_code:
        all_tests = test_db.get_all_tests()
        if all_tests:
            test_code = all_tests[0].get("test_code", "")
    
    code_display = f"#{test_code}" if test_code and not str(test_code).startswith("#") else (str(test_code) or "#TEST_KODI")
    return (
        "🚀 <b>Test boshlandi! Barchaga omad tilaymiz.</b>\n"
        "Belgilangan vaqt ichida javoblarni topshirishni unutmang.\n\n"
        f"📌 <b>Test kodi:</b> <code>{code_display}</code>"
    )

def get_broadcast_ended_text() -> str:
    active_tests = test_db.get_active_tests()
    test_code = ""
    title = ""
    if active_tests:
        test_code = active_tests[0].get("test_code", "")
        title = active_tests[0].get("title", "")
    if not test_code:
        all_tests = test_db.get_all_tests()
        if all_tests:
            test_code = all_tests[0].get("test_code", "")
            title = all_tests[0].get("title", "")
    
    code_display = f"#{test_code}" if test_code and not str(test_code).startswith("#") else (str(test_code) or "#TEST_KODI")
    title_line = f"📖 <b>Test:</b> {title}\n" if title else ""
    return (
        "🛑 <b>Test yakunlandi!</b>\n\n"
        "Javoblarni qabul qilish to'xtatildi. Ishtirok etgan barcha o'quvchilarga minnatdorchilik bildiramiz.\n\n"
        f"{title_line}"
        f"📌 <b>Test kodi:</b> <code>{code_display}</code>\n\n"
        "📊 <i>Tez orada to'liq tahlil va rasmiy natijalar e'lon qilinadi. Mini ilovaga kirib yangiliklarni kuzatib boring!</i>"
    )

BROADCAST_TEMPLATES = {
    "30m": {
        "title": "⏳ 30 daqiqa qoldi",
        "text": "⏳ <b>Diqqat! Test boshlanishiga 30 daqiqa qoldi!</b>\n\nInternet aloqangizni tekshirib, qoralama qog'ozlarni tayyorlab oling."
    },
    "10m": {
        "title": "⚠️ 10 daqiqa qoldi",
        "text": "⚠️ <b>Test boshlanishiga 10 daqiqa qoldi!</b>\n\nMini ilovaga kirib, tayyor bo'lib turing."
    },
    "started": {
        "title": "🚀 Test boshlandi",
        "get_text": get_broadcast_started_text
    },
    "15m": {
        "title": "⏰ 15 daqiqa qoldi",
        "text": "⏰ <b>Diqqat, test yakunlanishiga 15 daqiqa qoldi!</b>\n\nQolgan javoblarni tekshirib, topshirishga shoshiling."
    },
    "ended": {
        "title": "🛑 Test yakunlandi",
        "get_text": get_broadcast_ended_text
    }
}

@admin_router.callback_query(F.data == "admin_broadcast_menu")
async def admin_broadcast_menu_cb(call: CallbackQuery, state: FSMContext):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        await call.answer("Siz admin emassiz!", show_alert=True)
        return
    await state.clear()
    users_count = len((await test_db.db(test_db.get_broadcast_users)))
    text = (
        f"📢 <b>O'QUVCHILARGA XABAR YUBORISH BO'LIMI</b>\n\n"
        f"👥 <b>Qabul qiluvchilar:</b> {users_count} nafar faol foydalanuvchi\n\n"
        f"Quyidagi tezkor tayyor shablonlardan birini tanlashingiz yoki o'zingiz erkin xabar yozishingiz mumkin:\n\n"
        f"⏳ <b>30 daqiqa qoldi:</b> Test boshlanishiga 30 daqiqa ogohlantirishi\n"
        f"⚠️ <b>10 daqiqa qoldi:</b> Test boshlanishiga 10 daqiqa ogohlantirishi\n"
        f"🚀 <b>Test boshlandi:</b> Test kodi bilan boshlanganlik xabari\n"
        f"⏰ <b>15 daqiqa qoldi:</b> Test yakunlanishiga 15 daqiqa ogohlantirishi\n"
        f"🛑 <b>Test yakunlandi:</b> Test yakunlanganlik xabari\n\n"
        f"🛠 <b>Texnik profilaktika:</b> Tizim ta'mirlash xabarlari\n"
        f"✍️ <b>Erkin xabar:</b> O'zingiz matn yoki rasm yuborishingiz mumkin"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⏳ 30 daqiqa qoldi", callback_data="adm_bc_tmpl_30m"),
            InlineKeyboardButton(text="⚠️ 10 daqiqa qoldi", callback_data="adm_bc_tmpl_10m")
        ],
        [
            InlineKeyboardButton(text="🚀 Test boshlandi", callback_data="adm_bc_tmpl_started"),
            InlineKeyboardButton(text="⏰ 15 daqiqa qoldi", callback_data="adm_bc_tmpl_15m")
        ],
        [
            InlineKeyboardButton(text="🛑 Test yakunlandi", callback_data="adm_bc_tmpl_ended")
        ],
        [
            InlineKeyboardButton(text="🛠 1. Texnik ishlar boshlandi", callback_data="adm_bc_preview_start"),
            InlineKeyboardButton(text="✅ 2. Texnik ishlar yakunlandi", callback_data="adm_bc_preview_end")
        ],
        [InlineKeyboardButton(text="✍️ 3. O'zingiz erkin xabar yozish", callback_data="adm_bc_custom_input")],
        [InlineKeyboardButton(text="🗑 4. Yuborilgan xabarlarni o'chirish (Tarix)", callback_data="adm_bc_history_menu")],
        [InlineKeyboardButton(text="🧹 5. Oldingi xabarlarni barchadan tozalash", callback_data="adm_bc_clean_past_prompt")],
        [InlineKeyboardButton(text="⬅️ Admin panelga qaytish", callback_data="admin_back_to_menu")]
    ])
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except Exception:
        await call.message.answer(text, reply_markup=kb)
    await call.answer()

@admin_router.callback_query(F.data.startswith("adm_bc_tmpl_"))
async def adm_bc_tmpl_preview_cb(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    tmpl_key = call.data.replace("adm_bc_tmpl_", "")
    tmpl = BROADCAST_TEMPLATES.get(tmpl_key)
    if not tmpl:
        await call.answer("Shablon topilmadi!", show_alert=True)
        return

    text_to_send = tmpl["get_text"]() if "get_text" in tmpl else tmpl["text"]

    preview_text = (
        f"📋 <b>XABAR KO'RINISHI (PREVIEW):</b>\n\n"
        f"────────────────────\n"
        f"{text_to_send}\n"
        f"────────────────────\n\n"
        f"<b>Ushbu tayyor shablon xabarini barcha o'quvchilarga yuborishni tasdiqlaysizmi?</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Barchaga yuborish", callback_data=f"adm_bc_send_tmpl_{tmpl_key}")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin_broadcast_menu")]
    ])
    await call.message.edit_text(preview_text, reply_markup=kb)
    await call.answer()

@admin_router.callback_query(F.data.startswith("adm_bc_send_tmpl_"))
async def adm_bc_send_tmpl_cb(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    tmpl_key = call.data.replace("adm_bc_send_tmpl_", "")
    tmpl = BROADCAST_TEMPLATES.get(tmpl_key)
    if not tmpl:
        await call.answer("Shablon topilmadi!", show_alert=True)
        return

    text_to_send = tmpl["get_text"]() if "get_text" in tmpl else tmpl["text"]

    await call.answer("⏳ Xabar tarqatilmoqda...")
    status_msg = await call.message.answer("⏳ Barcha o'quvchilarga tayyor shablon xabari yuborilmoqda...")
    sent, fail, batch_id = await send_broadcast_to_users(message_text=text_to_send, sender_tg_id=call.from_user.id)
    await status_msg.edit_text(
        f"✅ <b>Xabar muvaffaqiyatli tarqatildi!</b>\n\n"
        f"📨 <b>Yetkazildi:</b> {sent} nafar o'quvchiga\n"
        f"⚠️ <b>Yetkazilmadi (bloklangan):</b> {fail} ta\n\n"
        f"<i>Agar xabarni o'chirmoqchi bo'lsangiz, pastdagi tugmani bosing:</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Ushbu xabarni barchadan o'chirish", callback_data=f"adm_bc_del_{batch_id}")],
            [InlineKeyboardButton(text="⬅️ Xabar yuborish bo'limiga", callback_data="admin_broadcast_menu")],
            [InlineKeyboardButton(text="🔙 Admin panelga", callback_data="admin_back_to_menu")]
        ])
    )


@admin_router.callback_query(F.data == "adm_bc_preview_start")
async def adm_bc_preview_start_cb(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    text = (
        f"📋 <b>XABAR KO'RINISHI (PREVIEW):</b>\n\n"
        f"────────────────────\n"
        f"{MAINT_START_TEXT}\n"
        f"────────────────────\n\n"
        f"<b>Ushbu xabarni barcha o'quvchilarga yuborishni tasdiqlaysizmi?</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Barchaga yuborish", callback_data="adm_bc_send_start")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin_broadcast_menu")]
    ])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@admin_router.callback_query(F.data == "adm_bc_preview_end")
async def adm_bc_preview_end_cb(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    text = (
        f"📋 <b>XABAR KO'RINISHI (PREVIEW):</b>\n\n"
        f"────────────────────\n"
        f"{MAINT_END_TEXT}\n"
        f"────────────────────\n\n"
        f"<b>Ushbu xabarni barcha o'quvchilarga yuborishni tasdiqlaysizmi?</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Barchaga yuborish", callback_data="adm_bc_send_end")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin_broadcast_menu")]
    ])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@admin_router.callback_query(F.data == "adm_bc_send_start")
async def adm_bc_send_start_cb(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    await call.answer("⏳ Xabar tarqatilmoqda...")
    status_msg = await call.message.answer("⏳ Barcha o'quvchilarga xabar yuborilmoqda...")
    sent, fail, batch_id = await send_broadcast_to_users(message_text=MAINT_START_TEXT, sender_tg_id=call.from_user.id)
    await status_msg.edit_text(
        f"✅ <b>Xabar muvaffaqiyatli tarqatildi!</b>\n\n"
        f"📨 <b>Yetkazildi:</b> {sent} nafar o'quvchiga\n"
        f"⚠️ <b>Yetkazilmadi (bloklangan):</b> {fail} ta\n\n"
        f"<i>Agar xabarni o'chirmoqchi bo'lsangiz, pastdagi tugmani bosing:</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Ushbu xabarni barchadan o'chirish", callback_data=f"adm_bc_del_{batch_id}")],
            [InlineKeyboardButton(text="⬅️ Xabar yuborish bo'limiga", callback_data="admin_broadcast_menu")],
            [InlineKeyboardButton(text="🔙 Admin panelga", callback_data="admin_back_to_menu")]
        ])
    )

@admin_router.callback_query(F.data == "adm_bc_send_end")
async def adm_bc_send_end_cb(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    await call.answer("⏳ Xabar tarqatilmoqda...")
    status_msg = await call.message.answer("⏳ Barcha o'quvchilarga xabar yuborilmoqda...")
    sent, fail, batch_id = await send_broadcast_to_users(message_text=MAINT_END_TEXT, sender_tg_id=call.from_user.id)
    await status_msg.edit_text(
        f"✅ <b>Xabar muvaffaqiyatli tarqatildi!</b>\n\n"
        f"📨 <b>Yetkazildi:</b> {sent} nafar o'quvchiga\n"
        f"⚠️ <b>Yetkazilmadi (bloklangan):</b> {fail} ta\n\n"
        f"<i>Agar xabarni o'chirmoqchi bo'lsangiz, pastdagi tugmani bosing:</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Ushbu xabarni barchadan o'chirish", callback_data=f"adm_bc_del_{batch_id}")],
            [InlineKeyboardButton(text="⬅️ Xabar yuborish bo'limiga", callback_data="admin_broadcast_menu")],
            [InlineKeyboardButton(text="🔙 Admin panelga", callback_data="admin_back_to_menu")]
        ])
    )

def get_broadcast_item_summary(message: Message) -> str:
    if message.document:
        fname = message.document.file_name or "Hujjat"
        return f"📄 PDF/Fayl: {fname}"
    elif message.photo:
        cap = (message.caption or "").strip()
        cap_prev = f" ('{cap[:30]}...')" if cap else ""
        return f"🖼 Rasm{cap_prev}"
    elif message.video:
        cap = (message.caption or "").strip()
        cap_prev = f" ('{cap[:30]}...')" if cap else ""
        return f"🎬 Video{cap_prev}"
    elif message.sticker:
        emoji = message.sticker.emoji or "🎭"
        return f"🎭 Stiker ({emoji})"
    elif message.voice:
        return f"🎙 Ovozli xabar ({message.voice.duration}s)"
    elif message.audio:
        title = message.audio.title or "Audio fayl"
        return f"🎵 Audio: {title}"
    elif message.video_note:
        return f"📹 Dumaloq video ({message.video_note.duration}s)"
    elif message.animation:
        return "👾 GIF animatsiya"
    elif message.text:
        txt = message.text.strip().replace("\n", " ")
        if len(txt) > 40:
            txt = txt[:37] + "..."
        return f"📝 Matn: '{txt}'"
    else:
        return "📬 Telegram xabari"

@admin_router.callback_query(F.data == "adm_bc_custom_input")
async def adm_bc_custom_input_cb(call: CallbackQuery, state: FSMContext):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    await state.clear()
    await state.set_state(BroadcastState.waiting_for_message)
    await state.update_data(messages_list=[])

    text = (
        "✍️ <b>O'QUVCHILARGA XABAR YUBORISH (HAMMA FORMATLAR)</b>\n\n"
        "Siz istalgan turdagi xabarlarni yuborishingiz mumkin:\n"
        "• 📄 <b>PDF yoki har qanday fayl / hujjat</b>\n"
        "• 🎭 <b>Stiker (Sticker) yoki 👾 GIF animatsiya</b>\n"
        "• 🖼 <b>Rasm / Foto (matnli yoki matnsiz)</b>\n"
        "• 🎬 <b>Video yoki 📹 Dumaloq video (video-note)</b>\n"
        "• 🎙 <b>Ovozli xabar (Voice) yoki 🎵 Audio</b>\n"
        "• 📝 <b>Oddiy yoki formatlangan matn</b>\n"
        "• 🔄 <b>Kanallardan to'g'ridan-to'g'ri forward xabarlar</b>\n\n"
        "💡 <b>Bittada bir nechta xabar yuborish:</b>\n"
        "Ketma-ket 1 ta yoki bir nechta xabarlarni botga yuboring (masalan: avval yo'riqnoma matni, keyin PDF fayl, keyin stiker). "
        "Barchasi to'plamga yig'iladi va bitta tugma bilan barcha o'quvchilarga ketma-ket yetkaziladi!\n\n"
        "<i>Xabarlaringizni yuboring (yoki bekor qilish uchun /cancel yozing):</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin_broadcast_menu")]
    ])
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except Exception:
        await call.message.answer(text, reply_markup=kb)
    await call.answer()

@admin_router.message(BroadcastState.waiting_for_message)
async def adm_bc_receive_custom_msg(message: Message, state: FSMContext):
    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ Xabar yuborish bekor qilindi.", reply_markup=admin_menu_kb())
        return

    data = await state.get_data()
    messages_list = list(data.get("messages_list", []))

    summary = get_broadcast_item_summary(message)
    messages_list.append({
        "chat_id": message.chat.id,
        "message_id": message.message_id,
        "desc": summary
    })
    await state.update_data(messages_list=messages_list)

    count = len(messages_list)
    list_items = "\n".join([f"<b>{i+1}.</b> {m['desc']}" for i, m in enumerate(messages_list)])

    text = (
        f"📥 <b>{count}-xabar to'plamga qo'shildi!</b>\n\n"
        f"📋 <b>Hozirgi xabarlar ro'yxati:</b>\n"
        f"{list_items}\n\n"
        f"💡 <i>Yana xabar yuborishingiz mumkin (PDF, stiker, rasm, audio, matn...) yoki barchasini birdaniga o'quvchilarga tarqatish uchun quyidagi tugmani bosing:</i>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🚀 Barchaga yuborish ({count} ta xabar)", callback_data="adm_bc_custom_confirm")],
        [InlineKeyboardButton(text="🗑 Ro'yxatni tozalash", callback_data="adm_bc_custom_clear")],
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin_broadcast_menu")]
    ])

    await message.answer(text, reply_markup=kb)

@admin_router.callback_query(F.data == "adm_bc_custom_clear", BroadcastState.waiting_for_message)
async def adm_bc_custom_clear_cb(call: CallbackQuery, state: FSMContext):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    await state.update_data(messages_list=[])
    text = (
        "🗑 <b>Barcha tayyorlangan xabarlar ro'yxati tozalandi.</b>\n\n"
        "Endi yangidan istalgan xabarlaringizni yuborishingiz mumkin (PDF, rasm, stiker, audio, matn...):"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin_broadcast_menu")]
    ])
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except Exception:
        await call.message.answer(text, reply_markup=kb)
    await call.answer("Ro'yxat tozalandi!")

@admin_router.callback_query(F.data == "adm_bc_custom_confirm")
async def adm_bc_custom_confirm_cb(call: CallbackQuery, state: FSMContext):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return

    data = await state.get_data()
    messages_list = data.get("messages_list", [])

    if not messages_list:
        await call.answer("Hech qanday xabar kiritilmagan! Avval xabar yuboring.", show_alert=True)
        return

    await state.clear()
    await call.answer("⏳ Xabarlar tarqatilmoqda...")

    count = len(messages_list)
    status_msg = await call.message.answer(f"⏳ Barcha o'quvchilarga <b>{count} ta xabar</b> yuborilmoqda, iltimos kuting...")

    sent, fail, batch_id = await send_broadcast_to_users(
        messages_list=messages_list,
        sender_tg_id=call.from_user.id
    )

    await status_msg.edit_text(
        f"✅ <b>{count} ta xabar muvaffaqiyatli tarqatildi!</b>\n\n"
        f"📨 <b>Yetkazildi:</b> {sent} nafar o'quvchiga\n"
        f"⚠️ <b>Yetkazilmadi (bloklangan):</b> {fail} ta\n\n"
        f"<i>Agar ushbu xabarlar to'plamini o'chirmoqchi bo'lsangiz, pastdagi tugmani bosing:</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Ushbu xabarlarni barchadan o'chirish", callback_data=f"adm_bc_del_{batch_id}")],
            [InlineKeyboardButton(text="⬅️ Xabar yuborish bo'limiga", callback_data="admin_broadcast_menu")],
            [InlineKeyboardButton(text="🔙 Admin panelga", callback_data="admin_back_to_menu")]
        ])
    )


# ── YUBORILGAN XABARLARNI O'CHIRISH (RECALL & DEEP CLEAN) ──

@admin_router.callback_query(F.data.startswith("adm_bc_del_"))
async def adm_bc_del_cb(call: CallbackQuery):
    """Aniq bir batch bo'yicha yuborilgan xabarlarni barcha o'quvchilar chatidan tezkor o'chirish."""
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        await call.answer("Siz admin emassiz!", show_alert=True)
        return
    batch_id = call.data.replace("adm_bc_del_", "").strip()
    await call.answer("⏳ O'chirish boshlandi...", show_alert=False)

    try:
        await call.message.edit_text(
            "⏳ <b>Xabarni o'chirish boshlandi...</b>\n\n"
            "O'quvchilar chatidan bir zumda tozalanmoqda...\n"
            "<i>Iltimos, kuting...</i>"
        )
    except Exception:
        pass

    last_update = [0.0]
    async def on_progress(done, total, del_cnt):
        now = time.time()
        if now - last_update[0] >= 1.0:
            last_update[0] = now
            try:
                await call.message.edit_text(
                    f"⏳ <b>Xabar o'chirilmoqda...</b>\n\n"
                    f"📨 <b>Jarayon:</b> {done} / {total} o'quvchi ({del_cnt} ta o'chirildi)\n\n"
                    f"<i>Iltimos, kuting...</i>"
                )
            except Exception:
                pass

    try:
        deleted_count, fail_count = await asyncio.wait_for(
            delete_broadcast_batch_from_users(batch_id, progress_callback=on_progress),
            timeout=20.0
        )
    except asyncio.TimeoutError:
        log.warning(f"delete_broadcast_batch_from_users timeout for {batch_id}")
        deleted_count, fail_count = 0, 0

    result_text = (
        f"🗑 <b>XABAR O'CHIRILDI!</b>\n\n"
        f"✅ <b>O'chirildi:</b> {deleted_count} nafar o'quvchi chatidan\n"
        f"⚠️ <b>Yetib bormagan / allaqachon yo'q:</b> {fail_count} ta\n\n"
        f"<i>Ushbu xabar endi o'quvchilar ekranida ko'rinmaydi.</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Xabar yuborish bo'limiga", callback_data="admin_broadcast_menu")],
        [InlineKeyboardButton(text="🔙 Admin panelga", callback_data="admin_back_to_menu")]
    ])
    try:
        await call.message.edit_text(result_text, reply_markup=kb)
    except Exception:
        await call.message.answer(result_text, reply_markup=kb)


@admin_router.callback_query(F.data == "adm_bc_history_menu")
async def adm_bc_history_menu_cb(call: CallbackQuery):
    """Yaqinda yuborilgan xabarlar ro'yxati va ularni o'chirish menyusi."""
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    recent = await test_db.db(test_db.get_recent_broadcasts, 8)
    if not recent:
        text = (
            "📭 <b>Yuborilgan xabarlar tarixi bo'sh.</b>\n\n"
            "Hozircha tizimda saqlangan xabarlar mavjud emas.\n"
            "Oldingi xabarlarni tozalash uchun quyidagi tugmadan foydalanishingiz mumkin:"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🧹 Oldingi xabarlarni barchadan tozalash", callback_data="adm_bc_clean_past_prompt")],
            [InlineKeyboardButton(text="⬅️ Xabar yuborish bo'limiga", callback_data="admin_broadcast_menu")]
        ])
        await call.message.edit_text(text, reply_markup=kb)
        return

    text = "🗑 <b>YUBORILGAN XABARLAR TARIXI VA O'CHIRISH</b>\n\n"
    text += "Kerakli xabar ostidagi <b>«🗑 O'chirish»</b> tugmasini bossangiz, u barcha o'quvchilar chatidan bir zumda o'chirib tashlanadi:\n\n"

    buttons = []
    for idx, b in enumerate(recent, 1):
        dt = format_uzb_time(b.get("created_at"), "%d.%m %H:%M")
        raw_msg = (b.get("message_text") or "Rasmli xabar").replace("<br>", " ").replace("\n", " ")
        snippet = (raw_msg[:35] + "...") if len(raw_msg) > 35 else raw_msg
        is_del = (b.get("is_deleted") == 1)

        status_icon = "🗑 O'chirilgan" if is_del else f"✅ {b.get('total_sent', 0)} ta o'quvchiga"
        text += f"{idx}. <b>[{dt}]</b> {snippet}\n   <i>Holat: {status_icon}</i>\n\n"

        if not is_del:
            buttons.append([InlineKeyboardButton(
                text=f"🗑 {idx}-xabarni barchadan o'chirish",
                callback_data=f"adm_bc_del_{b['batch_id']}"
            )])

    buttons.append([InlineKeyboardButton(text="🧹 Oldingi xabarlarni barchadan tozalash", callback_data="adm_bc_clean_past_prompt")])
    buttons.append([InlineKeyboardButton(text="⬅️ Xabar yuborish bo'limiga", callback_data="admin_broadcast_menu")])

    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()


@admin_router.callback_query(F.data == "adm_bc_clean_past_prompt")
async def adm_bc_clean_past_prompt_cb(call: CallbackQuery):
    """Oldingi xabarlarni barcha o'quvchilar chatidan tozalash taklifi."""
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    text = (
        "🧹 <b>OLDINGI XABARLARNI TOZALASH (DEEP CLEAN)</b>\n\n"
        "Ushbu funksiya tarixda saqlanmagan yoki avvalroq yuborilgan xabarlarni barcha o'quvchilar chatidan o'chirish uchun mo'ljallangan.\n\n"
        "Bot barcha o'quvchilar chatidagi so'nggi xabarlarni tezkor parallel tekshirib tozalab chiqadi.\n\n"
        "<b>Nechta so'nggi bot xabarini o'chirmoqchisiz?</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🗑 1 ta so'nggi xabarni o'chirish", callback_data="adm_bc_clean_1"),
            InlineKeyboardButton(text="🗑 2 ta so'nggi xabarni o'chirish", callback_data="adm_bc_clean_2")
        ],
        [
            InlineKeyboardButton(text="🗑 3 ta so'nggi xabarni o'chirish", callback_data="adm_bc_clean_3"),
            InlineKeyboardButton(text="🗑 5 ta so'nggi xabarni o'chirish", callback_data="adm_bc_clean_5")
        ],
        [InlineKeyboardButton(text="⬅️ Bekor qilish", callback_data="admin_broadcast_menu")]
    ])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


@admin_router.callback_query(F.data.startswith("adm_bc_clean_"))
async def adm_bc_clean_action_cb(call: CallbackQuery):
    """Barcha o'quvchilardan so'nggi N ta xabarni o'chirish amali."""
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    try:
        count = int(call.data.replace("adm_bc_clean_", "").strip())
    except Exception:
        count = 1

    await call.answer(f"⏳ So'nggi {count} ta xabarni tozalash boshlandi...", show_alert=False)

    try:
        await call.message.edit_text(
            f"⏳ <b>Tozalash boshlandi...</b>\n\n"
            f"O'quvchilar chatidagi so'nggi {count} ta bot xabari o'chirilmoqda...\n"
            f"<i>Iltimos, kuting...</i>"
        )
    except Exception:
        pass

    last_update = [0.0]
    async def on_progress(done_users, total_users, del_count):
        now = time.time()
        if now - last_update[0] >= 1.0:
            last_update[0] = now
            try:
                await call.message.edit_text(
                    f"⏳ <b>Tozalanmoqda...</b>\n\n"
                    f"👥 <b>Jarayon:</b> {done_users} / {total_users} o'quvchi\n"
                    f"🗑 <b>O'chirildi:</b> {del_count} ta xabar\n\n"
                    f"<i>Iltimos, kuting...</i>"
                )
            except Exception:
                pass

    try:
        deleted_total, checked_users = await asyncio.wait_for(
            delete_recent_bot_messages_from_all_users(count=count, progress_callback=on_progress),
            timeout=25.0
        )
    except asyncio.TimeoutError:
        log.warning("delete_recent_bot_messages_from_all_users timeout reached")
        deleted_total = 0
        checked_users = len((await test_db.db(test_db.get_broadcast_users)))

    res_text = (
        f"🧹 <b>TOZALASH MUVAFFAQIYATLI YAKUNLANDI!</b>\n\n"
        f"👥 <b>Tekshirilgan o'quvchilar:</b> {checked_users} nafar\n"
        f"🗑 <b>O'chirilgan jami bot xabarlari:</b> {deleted_total} ta\n\n"
        f"<i>O'quvchilar chatidagi so'nggi bot xabarlari tozalandi.</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Xabar yuborish bo'limiga", callback_data="admin_broadcast_menu")],
        [InlineKeyboardButton(text="🔙 Admin panelga", callback_data="admin_back_to_menu")]
    ])
    try:
        await call.message.edit_text(res_text, reply_markup=kb)
    except Exception:
        await call.message.answer(res_text, reply_markup=kb)

# 5. Natijalar va hisobotlar boshqaruvi
@admin_router.callback_query(F.data == "admin_leaderboard")
async def admin_leaderboard(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return

    tests = await test_db.db(test_db.get_tests_with_stats)
    if not tests:
        text = "⚠️ Hozirda tizimda mavjud testlar yo'q."
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Admin Panelga qaytish", callback_data="admin_panel_back")]
        ])
        try:
            await call.message.edit_text(text, reply_markup=kb)
        except Exception:
            await call.message.answer(text, reply_markup=kb)
        await call.answer()
        return

    buttons = []
    msg_list = ""
    for idx, t in enumerate(tests, 1):
        sub_cnt = t.get("submissions_count", 0)
        btn_text = f"📊 #{t['test_code']} — 👥 {sub_cnt} kishi"
        buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"adm_tstat_{t['id']}")])
        msg_list += f"<b>{idx}. #{t['test_code']}</b> — {t['title']}: <b>{sub_cnt} kishi</b>\n"

    buttons.append([InlineKeyboardButton(text="⬅️ Admin Panelga qaytish", callback_data="admin_panel_back")])

    msg_text = (
        "📊 <b>Mavjud Testlar va Ishtirokchilar Soni:</b>\n\n"
        f"{msg_list}\n"
        "<i>Batafsil natijalarni (Matn yoki PDF shaklida) olish uchun kerakli test kodini tanlang 👇</i>"
    )

    try:
        await call.message.edit_text(msg_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    except Exception:
        await call.message.answer(msg_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()

@admin_router.callback_query(F.data.startswith("adm_tstat_"))
async def admin_test_stats_detail(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return

    test_id = int(call.data.split("_")[2])
    test = test_db.get_test_by_id(test_id)
    if not test:
        await call.answer("Test topilmadi!", show_alert=True)
        return

    results = await test_db.db(test_db.get_test_results_leaderboard, test_id)
    count = len(results)
    avg_score = 0
    if count > 0:
        avg_score = round(sum(r['score'] for r in results) / count, 1)

    is_active = (test.get("is_active", 1) == 1)
    is_pub = await test_db.db(test_db.is_test_results_published, test_id)

    status_badge = "🟢 Javoblar qabul qilinmoqda" if is_active else "🔴 Javoblar qabul qilish to'xtatilgan"
    pub_badge = "📢 Natijalar e'lon qilingan" if is_pub else "🔒 Yashirin (O'quvchilarga «Javoblar tekshirilmoqda» ko'rinadi)"

    # Jadval vaqt ma'lumoti
    sched_date = test.get('scheduled_date') or ''
    sched_start = test.get('scheduled_start') or ''
    sched_end = test.get('scheduled_end') or ''
    if sched_date and sched_start and sched_end:
        sched_badge = f"⏰ {sched_date} | {sched_start}–{sched_end} (UZB)"
    elif sched_start and sched_end:
        sched_badge = f"⏰ {sched_start}–{sched_end} (UZB, sana belgilanmagan)"
    else:
        sched_badge = "➖ Belgilanmagan"

    yt_url = (test.get('youtube_url') or '').strip()
    yt_badge = f'<a href="{yt_url}">Mavjud (Ko\'rish 🎬)</a>' if yt_url else "❌ Kiritilmagan"

    creator_str = test.get('created_by_name') or ('Bosh Admin' if test.get('created_by') == ADMIN_ID else 'Admin')
    text = (
        f"📊 <b>Test natijalari va tahlil bo'limi:</b>\n\n"
        f"📖 <b>Nomi:</b> {test['title']}\n"
        f"🔑 <b>Kodi:</b> <code>#{test['test_code']}</code>\n"
        f"👤 <b>Yaratuvchi:</b> {creator_str}\n"
        f"📌 <b>Fani:</b> {test.get('subject', 'Matematika')}\n"
        f"🚦 <b>Holati:</b> {status_badge}\n"
        f"📢 <b>Natijalar:</b> {pub_badge}\n"
        f"🎬 <b>Video tahlil:</b> {yt_badge}\n\n"
        f"👥 <b>Topshirganlar soni:</b> <b>{count} nafar</b>\n"
        f"📈 <b>O'rtacha ball:</b> <b>{avg_score} ball</b>\n\n"
        f"<i>Hisoblash, tahlil qilish va natijalarni e'lon qilish usulini tanlang 👇</i>"
    )

    edit_keys_url = f"{get_webapp_url()}/admin.html?edit_test_id={test_id}"
    buttons = [
        [make_webapp_button("✏️ Kalitlarni tahrirlash (Mini App)", edit_keys_url)],
        [InlineKeyboardButton(text="🧮 Rasch modeli (JMLE) bo'yicha hisoblash", callback_data=f"adm_broadcast_rasch_{test_id}")],
        [InlineKeyboardButton(text="✅ Standart hisoblash (To'g'ri javoblar)", callback_data=f"adm_broadcast_std_{test_id}")],
    ]

    if not is_pub:
        buttons.append([InlineKeyboardButton(text="📢 Natijalarni e'lon qilish va yuborish", callback_data=f"adm_eval_prompt_{test_id}")])
    else:
        buttons.append([
            InlineKeyboardButton(text="🔄 Natijalarni qayta yuborish", callback_data=f"adm_eval_prompt_{test_id}"),
            InlineKeyboardButton(text="🔒 Natijalarni yashirish", callback_data=f"adm_hide_results_{test_id}")
        ])

    buttons.append([
        InlineKeyboardButton(text="📄 Matn shaklida reyting", callback_data=f"adm_restxt_{test_id}"),
        InlineKeyboardButton(text="📑 PDF hisobot", callback_data=f"adm_respdf_{test_id}")
    ])
    yt_btn_text = "🎬 Video tahlil ✅" if yt_url else "🎬 Video tahlil ➕"
    buttons.append([
        InlineKeyboardButton(text="🧮 Rasch tahlil", callback_data=f"adm_rasch_{test_id}"),
        InlineKeyboardButton(text="🔍 Shovqin & Savollar", callback_data=f"adm_item_diag_{test_id}")
    ])
    buttons.append([
        InlineKeyboardButton(text=yt_btn_text, callback_data=f"adm_set_yt_{test_id}"),
        InlineKeyboardButton(text="⬅️ Testlar ro'yxati", callback_data="admin_leaderboard")
    ])

    try:
        await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    except Exception:
        await call.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    try:
        await call.answer()
    except Exception:
        pass

@admin_router.callback_query(F.data.startswith("adm_set_yt_"))
async def adm_set_yt_cb(call: CallbackQuery, state: FSMContext):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    await state.clear()
    test_id = int(call.data.split("_")[3])
    test = test_db.get_test_by_id(test_id)
    if not test:
        await call.answer("Test topilmadi!", show_alert=True)
        return
    yt_url = (test.get("youtube_url") or "").strip()
    status_text = f"🔗 <b>Joriy havola:</b> <code>{yt_url}</code>\n\n" if yt_url else "❌ <b>Hozircha havola kiritilmagan.</b>\n\n"
    text = (
        f"🎬 <b>YOUTUBE VIDEO TAHLIL SOZLAMALARI</b>\n\n"
        f"📖 <b>Test:</b> {test['title']} (<code>#{test['test_code']}</code>)\n"
        f"{status_text}"
        f"Quyidagi amallardan birini tanlang:"
    )
    buttons = [
        [InlineKeyboardButton(text="✍️ Havolani kiritish / Yangilash", callback_data=f"adm_input_yt_{test_id}")],
    ]
    if yt_url:
        buttons.append([InlineKeyboardButton(text="🗑 Havolani o'chirish", callback_data=f"adm_del_yt_{test_id}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Test boshqaruviga qaytish", callback_data=f"adm_tstat_{test_id}")])
    
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()

@admin_router.callback_query(F.data.startswith("adm_input_yt_"))
async def adm_input_yt_cb(call: CallbackQuery, state: FSMContext):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    test_id = int(call.data.split("_")[3])
    await state.update_data(yt_test_id=test_id)
    await state.set_state(SetYoutubeState.waiting_for_url)
    await call.message.answer(
        "🎬 <b>YouTube video tahlil havolasini (URL) yuboring:</b>\n\n"
        "<i>Masalan: https://youtu.be/... yoki https://www.youtube.com/watch?v=...</i>\n\n"
        "Bekor qilish uchun /cancel deb yozing."
    )
    await call.answer()

@admin_router.message(SetYoutubeState.waiting_for_url)
async def adm_save_yt_msg(message: Message, state: FSMContext):
    if not (await test_db.db(test_db.is_admin, message.from_user.id, ADMIN_ID)):
        return
    if message.text and message.text.strip().lower() == "/cancel":
        await state.clear()
        await message.answer("❌ Havola kiritish bekor qilindi.")
        return
    data = await state.get_data()
    test_id = data.get("yt_test_id")
    url = (message.text or "").strip()
    if not ("youtube.com" in url or "youtu.be" in url or url.startswith("http")):
        await message.answer("⚠️ Iltimos, to'g'ri YouTube havolasini (URL) yuboring (masalan: https://youtu.be/...):")
        return
    await test_db.db(test_db.set_test_youtube_url, test_id, url)
    await state.clear()
    test = test_db.get_test_by_id(test_id)
    title = test['title'] if test else f"#{test_id}"
    await message.answer(
        f"✅ <b>«{title}» testi uchun YouTube video tahlil havolasi muvaffaqiyatli saqlandi!</b>\n\n"
        f"🔗 Havola: {url}\n\n"
        f"<i>Natijalar e'lon qilinganda o'quvchilarga video tahlil tugmasi orqali ochiladi.</i>"
    )

@admin_router.callback_query(F.data.startswith("adm_del_yt_"))
async def adm_del_yt_cb(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    test_id = int(call.data.split("_")[3])
    await test_db.db(test_db.set_test_youtube_url, test_id, "")
    await call.answer("🗑 Video tahlil havolasi o'chirildi!", show_alert=True)
    call.data = f"adm_tstat_{test_id}"
    await admin_test_stats_detail(call)

# Testni o'chirishni tasdiqlash
@admin_router.callback_query(F.data.startswith("adm_del_test_prompt_"))
async def adm_del_test_prompt_cb(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    test_id = int(call.data.split("_")[4])
    test = test_db.get_test_by_id(test_id)
    if not test:
        await call.answer("Test topilmadi!", show_alert=True)
        return
    
    text = (
        f"⚠️ <b>DIQQAT! TESTNI O'CHIRISH</b>\n\n"
        f"📖 Nomi: <b>{test['title']}</b> (#{test['test_code']})\n\n"
        f"Ushbu testni va uning barcha o'quvchilar topshirgan natijalarini <b>butunlay o'chirib tashlamoqchimisiz?</b>\n"
        f"<i>Ushbu amalni ortga qaytarib bo'lmaydi!</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Ha, butunlay o'chirilsin!", callback_data=f"adm_del_test_exec_{test_id}")],
        [InlineKeyboardButton(text="🔙 Bekor qilish", callback_data=f"adm_tstat_{test_id}")]
    ])
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except Exception:
        await call.message.answer(text, reply_markup=kb)
    await call.answer()

@admin_router.callback_query(F.data.startswith("adm_del_test_exec_"))
async def adm_del_test_exec_cb(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    test_id = int(call.data.split("_")[4])
    ok = await test_db.db(test_db.delete_test, test_id)
    if ok:
        await call.answer("🗑 Test muvaffaqiyatli o'chirildi!", show_alert=True)
        call.data = "admin_leaderboard"
        await admin_leaderboard(call)
    else:
        await call.answer("O'chirishda xatolik yuz berdi!", show_alert=True)

# Javob qabul qilishni boshlash / to'xtatish
@admin_router.callback_query(F.data.startswith("toggle_test_tstat_"))
async def admin_toggle_test_tstat_cb(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    test_id = int(call.data.split("_")[3])
    new_status = await test_db.db(test_db.toggle_test_status, test_id)
    if new_status is not None:
        status_text = "🟢 Javoblar qabul qilinmoqda" if new_status == 1 else "🔴 Javoblar to'xtatildi"
        try:
            await call.answer(f"Test holati: {status_text}", show_alert=True)
        except Exception:
            pass
        call.data = f"adm_tstat_{test_id}"
        await admin_test_stats_detail(call)
    else:
        try:
            await call.answer("Xatolik yuz berdi!", show_alert=True)
        except Exception:
            pass

# Natijalarni qayta yashirish (o'quvchilarga yana "tekshirilmoqda" qilish)
@admin_router.callback_query(F.data.startswith("adm_hide_results_"))
async def admin_hide_results_cb(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    test_id = int(call.data.split("_")[3])
    await test_db.db(test_db.set_test_results_published, test_id, False)
    await test_db.db(test_db.set_test_active_status, test_id, 1)
    await call.answer("🔒 Natijalar yashirildi va test faollashtirildi! Endi o'quvchilarga «Javoblar tekshirilmoqda» ko'rinadi.", show_alert=True)
    call.data = f"adm_tstat_{test_id}"
    await admin_test_stats_detail(call)

# Natijalarni e'lon qilishdan oldin SO'ROV: Rasch modeli bo'yicha yoki Standart
@admin_router.callback_query(F.data.startswith("adm_eval_prompt_"))
@admin_router.callback_query(F.data.startswith("adm_broadcast_results_"))
async def admin_eval_prompt_cb(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return

    parts = call.data.split("_")
    test_id = int(parts[-1])
    test = test_db.get_test_by_id(test_id)
    if not test:
        await call.answer("Test topilmadi!", show_alert=True)
        return

    text = (
        f"📋 <b>«{test['title']}»</b> (<code>#{test['test_code']}</code>)\n\n"
        f"🤔 <b>Natijalarni qaysi usulda tekshirib, o'quvchilarga e'lon qilmoqchisiz?</b>\n\n"
        f"1️⃣ <b>🧮 Rasch modeli (JMLE) bo'yicha:</b>\n"
        f"• Savollarning qiyinlik darajasi (b) va o'quvchilar qobiliyati (θ) hisoblanadi.\n"
        f"• Rasmiy Milliy Sertifikat darajalari (A+, A, B+, B, C+, C) beriladi.\n\n"
        f"2️⃣ <b>✅ Standart baholash (To'g'ri javoblar soni bo'yicha):</b>\n"
        f"• Har bir to'g'ri ishlangan savol soni va standart ball hisoblanadi.\n"
        f"• Oddiy va shaffof: nechta to'g'ri, noto'g'ri va to'plangan ball ko'rsatiladi.\n\n"
        f"<i>Quyidagi usullardan birini tanlang 👇</i>"
    )

    buttons = [
        [InlineKeyboardButton(text="🧮 1. Rasch modeli (JMLE) bo'yicha e'lon qilish", callback_data=f"adm_broadcast_rasch_{test_id}")],
        [InlineKeyboardButton(text="✅ 2. Standart (To'g'ri javoblar soni) bo'yicha", callback_data=f"adm_broadcast_std_{test_id}")],
        [InlineKeyboardButton(text="⬅️ Bekor qilish / Orqaga", callback_data=f"adm_tstat_{test_id}")]
    ]

    try:
        await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    except Exception:
        await call.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()

def build_detailed_tahlil_text(sub: dict, test: dict) -> str:
    """O'quvchi uchun to'g'ri va noto'g'ri ishlangan savollar tahlili matni."""
    name = sub.get("fullname") or "O'quvchi"
    score = sub.get("score", 0.0)
    grade = sub.get("grade") or test_db.calculate_grade(score)
    corr = sub.get("correct_count", 0)
    total = sub.get("total_count", 55) or 55
    incorr = max(0, total - corr)
    code = sub.get("test_code", test.get("test_code", ""))
    test_title = test.get("title", "Matematika Testi")
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

def build_student_result_message(sub: dict, test: dict, eval_type: str = "rasch") -> tuple:
    """O'quvchi uchun natija matni va video tahlil / mini ilova tugmalarini shakllantirish."""
    name = sub.get("fullname") or "O'quvchi"
    score = sub.get("score", 0.0)
    grade = sub.get("grade") or test_db.calculate_grade(score)
    corr = sub.get("correct_count", 0)
    total = sub.get("total_count", 55) or 55
    incorr = max(0, total - corr)
    code = sub.get("test_code", test.get("test_code", ""))
    test_title = test.get("title", "Matematika Testi")
    yt_url = (test.get("youtube_url") or "").strip()

    if eval_type == "rasch":
        msg_text = (
            f"📢 <b>DIQQAT! TEST NATIJALARI E'LON QILINDI!</b>\n\n"
            f"Hurmatli <b>{name}</b>, sizning <b>«{test_title}»</b> (<code>#{code}</code>) testi bo'yicha rasmiy natijangiz:\n\n"
            f"{tg_e('star_premium', '🧮')} <b>Baholash tizimi:</b> Rasch Modeli (JMLE)\n"
            f"{tg_e('star_gold', '🎖')} <b>Milliy Sertifikat darajangiz:</b> <b>{grade}</b> ({score} ball)\n"
            f"{tg_e('success', '✅')} <b>To'g'ri ishlangan:</b> {corr} ta band\n"
            f"{tg_e('error', '❌')} <b>Noto'g'ri / belgilanmagan:</b> {incorr} ta\n"
            f"{tg_e('order', '📊')} <b>Jami savollar:</b> {total} ta\n"
            f"{tg_e('clock', '🕒')} <b>E'lon vaqti:</b> {format_uzb_time()}\n\n"
            f"💡 <i>Qaysi savollaringiz to'g'ri yoki noto'g'ri ekanligini ko'rish uchun quyidagi <b>«📊 Test tahlili»</b> tugmasini bosing:</i>\n\n"
            f"🏆 <i>Ishtirokingiz uchun tashakkur!</i>"
        )
    else:
        msg_text = (
            f"📢 <b>DIQQAT! TEST NATIJALARI E'LON QILINDI!</b>\n\n"
            f"Hurmatli <b>{name}</b>, sizning <b>«{test_title}»</b> (<code>#{code}</code>) testi bo'yicha rasmiy natijangiz:\n\n"
            f"{tg_e('star_premium', '📋')} <b>Baholash turi:</b> Standart (To'g'ri javoblar soni)\n"
            f"{tg_e('success', '✅')} <b>To'g'ri javoblar:</b> {corr} / {total} ta\n"
            f"{tg_e('error', '❌')} <b>Noto'g'ri javoblar:</b> {incorr} ta\n"
            f"{tg_e('star_gold', '🎯')} <b>To'plangan ball:</b> {score} ball\n"
            f"{tg_e('clock', '🕒')} <b>E'lon vaqti:</b> {format_uzb_time()}\n\n"
            f"💡 <i>Qaysi savollaringiz to'g'ri yoki noto'g'ri ekanligini ko'rish uchun quyidagi <b>«📊 Test tahlili»</b> tugmasini bosing:</i>\n\n"
            f"🏆 <i>Ishtirokingiz uchun tashakkur!</i>"
        )

    kb_rows = [
        [InlineKeyboardButton(text="📊 Test tahlili", callback_data=f"user_req_tahlil_{test['id']}")]
    ]
    if yt_url:
        kb_rows.append([InlineKeyboardButton(text="🎬 Video tahlilni ko'rish (YouTube)", url=yt_url)])
    else:
        kb_rows.append([InlineKeyboardButton(text="🎬 Video tahlil (mavjud emas)", callback_data="no_video_analysis")])

    kb_rows.append([InlineKeyboardButton(text="📱 Mini ilovada to'liq ko'rish", web_app=WebAppInfo(url=f"{get_webapp_url()}/app.html?tab=tests"))])

    return msg_text, InlineKeyboardMarkup(inline_keyboard=kb_rows)

@admin_router.callback_query(F.data == "no_video_analysis")
async def no_video_analysis_cb(call: CallbackQuery):
    await call.answer("⚠️ Ushbu test uchun video tahlil kiritilmagan.", show_alert=True)



@admin_router.callback_query(F.data.startswith("adm_broadcast_rasch_"))
async def admin_broadcast_rasch_cb(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return

    test_id = int(call.data.split("_")[3])
    test = test_db.get_test_by_id(test_id)
    if not test:
        await call.answer("Test topilmadi!", show_alert=True)
        return

    await call.answer("⏳ Rasch modeli hisoblanmoqda va e'lon qilinmoqda...")
    status_msg = await call.message.answer(
        f"⏳ <b>«{test['title']}»</b> testi to'xtatilmoqda, Rasch modeli (JMLE) bo'yicha yakuniy ballar kalibrlanmoqda va o'quvchilarga shaxsiy natijalar yuborilmoqda..."
    )

    submissions = await test_db.db(test_db.get_test_submissions_with_users, test_id)
    if not submissions:
        await status_msg.edit_text(
            f"⚠️ <b>«{test['title']}»</b> testini hali hech kim topshirmagan!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Test boshqaruviga qaytish", callback_data=f"adm_tstat_{test_id}")]
            ])
        )
        return

    if len(submissions) < 2:
        await status_msg.edit_text(
            f"⚠️ <b>Rasch modeli (JMLE) uchun kamida 2 nafar o'quvchi topshirgan bo'lishi kerak!</b>\n\n"
            f"Hozirda testni faqat 1 nafar o'quvchi topshirgan. O'quvchilar soni ko'payishini kuting yoki Standart baholash bo'yicha e'lon qiling.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Standart baholash bilan e'lon qilish", callback_data=f"adm_broadcast_std_{test_id}")],
                [InlineKeyboardButton(text="⬅️ Test boshqaruviga qaytish", callback_data=f"adm_tstat_{test_id}")]
            ])
        )
        return

    try:
        # 1. Testni to'xtatish (is_active = 0)
        await test_db.db(test_db.set_test_active_status, test_id, 0)

        # 2. Rasch modeli orqali yakuniy kalibrlash va bazani yangilash
        rasch_res = await test_db.db(test_db.evaluate_test_rasch, test_id, auto_update_db=True)

        # 3. Test natijalarini e'lon qilingan holatga o'tkazish
        await test_db.db(test_db.set_test_results_published, test_id, True)

        # 4. Topshirgan barcha o'quvchilarga shaxsiy Telegram xabarini yuborish
        submissions = await test_db.db(test_db.get_test_submissions_with_users, test_id)
        sent_count = 0
        fail_count = 0

        for sub in submissions:
            uid = sub.get("user_tg_id")
            if not uid:
                continue
            msg_text, reply_kb = build_student_result_message(sub, test, eval_type="rasch")
            try:
                await bot.send_message(chat_id=uid, text=msg_text, reply_markup=reply_kb)
                sent_count += 1
                await asyncio.sleep(0.05)
            except Exception as ex:
                log.warning(f"O'quvchi {uid} ga natija yuborishda xatolik: {ex}")
                fail_count += 1

        fail_text = f"⚠️ Yetkazilmadi (bot bloklangan): {fail_count} ta\n" if fail_count > 0 else ""

        items = rasch_res.get("items", []) if rasch_res else []
        noisy = [it for it in items if it.get("is_noisy")]
        if noisy:
            noisy_names = ", ".join([f"<code>{it['item_label']}</code>" for it in noisy[:10]])
            noisy_info = f"⚠️ <b>Shovqinli savollar ({len(noisy)} ta):</b> {noisy_names}\n"
        else:
            noisy_info = "✨ <b>Savollar sifati:</b> Barcha savollar sifatli (shovqin yo'q)\n"

        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔍 Savollar qiyinligi va Shovqin tahlili", callback_data=f"adm_item_diag_{test_id}")],
            [InlineKeyboardButton(text="⬅️ Test boshqaruviga qaytish", callback_data=f"adm_tstat_{test_id}")]
        ])
        await status_msg.edit_text(
            f"✅ <b>Rasch modeli bo'yicha natijalar e'lon qilindi!</b>\n\n"
            f"📨 <b>Yuborildi:</b> {sent_count} nafar o'quvchiga\n"
            f"{fail_text}"
            f"{noisy_info}\n"
            f"📌 <i>O'quvchilar botda va mini ilovada o'z ballari va to'liq tahlilni ko'ra oladilar.</i>",
            reply_markup=back_kb
        )
        # Barcha adminlarga e'lon xabari va rasmiy natijalar PDF ini yuborish
        asyncio.create_task(notify_admins_test_results_published(test_id, call.from_user.id, eval_type="rasch"))
    except Exception as e:
        log.error(f"Xatolik broadcastda: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ <b>Natijalarni e'lon qilishda xatolik yuz berdi:</b>\n<code>{e}</code>")

# 2. Standart baholash (To'g'ri javoblar soni) bo'yicha e'lon qilish
@admin_router.callback_query(F.data.startswith("adm_broadcast_std_"))
async def admin_broadcast_std_cb(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return

    test_id = int(call.data.split("_")[3])
    test = test_db.get_test_by_id(test_id)
    if not test:
        await call.answer("Test topilmadi!", show_alert=True)
        return

    await call.answer("⏳ Standart natijalar e'lon qilinmoqda...")
    status_msg = await call.message.answer(
        f"⏳ <b>«{test['title']}»</b> testi to'xtatilmoqda va standart to'g'ri javoblar soni bo'yicha natijalar yuborilmoqda..."
    )

    submissions = await test_db.db(test_db.get_test_submissions_with_users, test_id)
    if not submissions:
        await status_msg.edit_text(
            f"⚠️ <b>«{test['title']}»</b> testini hali hech kim topshirmagan!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Test boshqaruviga qaytish", callback_data=f"adm_tstat_{test_id}")]
            ])
        )
        return

    try:
        # 1. Testni to'xtatish (is_active = 0)
        await test_db.db(test_db.set_test_active_status, test_id, 0)

        # 2. Test natijalarini e'lon qilingan holatga o'tkazish
        await test_db.db(test_db.set_test_results_published, test_id, True)

        # 3. Topshirgan barcha o'quvchilarga shaxsiy Telegram xabarini yuborish
        sent_count = 0
        fail_count = 0

        for sub in submissions:
            uid = sub.get("user_tg_id")
            if not uid:
                continue
            msg_text, reply_kb = build_student_result_message(sub, test, eval_type="std")
            try:
                await bot.send_message(chat_id=uid, text=msg_text, reply_markup=reply_kb)
                sent_count += 1
                await asyncio.sleep(0.05)
            except Exception as ex:
                log.warning(f"O'quvchi {uid} ga natija yuborishda xatolik: {ex}")
                fail_count += 1

        fail_text = f"⚠️ Yetkazilmadi (bot bloklangan): {fail_count} ta\n" if fail_count > 0 else ""
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Test boshqaruviga qaytish", callback_data=f"adm_tstat_{test_id}")]
        ])
        await status_msg.edit_text(
            f"✅ <b>Standart natijalar muvaffaqiyatli e'lon qilindi!</b>\n\n"
            f"📨 <b>Yuborildi:</b> {sent_count} nafar o'quvchiga\n"
            f"{fail_text}"
            f"📌 <i>Endi barcha o'quvchilar botda va mini ilovada o'z ballari va to'liq tahlilni ko'ra oladilar.</i>",
            reply_markup=back_kb
        )
        # Barcha adminlarga e'lon xabari va rasmiy natijalar PDF ini yuborish
        asyncio.create_task(notify_admins_test_results_published(test_id, call.from_user.id, eval_type="std"))
    except Exception as e:
        log.error(f"Xatolik broadcastda: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ <b>Natijalarni e'lon qilishda xatolik yuz berdi:</b>\n<code>{e}</code>")

# ── KECH TOPSHIRILGAN NATIJANI TESTGA QO'SHISH YOKI RAD ETISH ──
@admin_router.callback_query(F.data.startswith("adm_accept_late_"))
async def admin_accept_late_cb(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    sub_id = int(call.data.split("_")[3])
    sub = await test_db.db(test_db.get_submission_by_id, sub_id)
    if not sub:
        await call.answer("Topshiriq topilmadi!", show_alert=True)
        return

    # 1. Bazada statusni yangilash: is_late = 0
    await test_db.db(test_db.set_submission_late_status, sub_id, 0)
    test_id = sub["test_id"]
    test = test_db.get_test_by_id(test_id)

    # 2. Agar test natijalari e'lon qilingan bo'lsa, Rasch va PDF ni yangilash
    if test and test.get("results_published", 0) == 1:
        try:
            await test_db.db(test_db.evaluate_test_rasch, test_id, auto_update_db=True)
        except Exception as e:
            log.warning(f"Rasch re-eval error: {e}")
        try:
            await test_db.db(test_db.generate_test_results_pdf, test_id)
        except Exception as e:
            log.warning(f"PDF regeneration error: {e}")

    # 3. O'quvchiga natijani yuborish
    uid = sub.get("user_tg_id")
    if uid:
        try:
            updated_sub = (await test_db.db(test_db.get_submission_by_id, sub_id)) or sub
            msg_text, reply_kb = build_student_result_message(updated_sub, test, eval_type="rasch")
            await bot.send_message(
                chat_id=uid,
                text=(
                    f"🎉 <b>Xushxabar!</b>\n\n"
                    f"Admin sizning kech topshirgan javoblaringizni qabul qildi va test natijalariga qo'shdi!\n\n"
                    f"{msg_text}"
                ),
                reply_markup=reply_kb
            )
        except Exception as ex:
            log.warning(f"O'quvchiga tasdiq xabarini yuborishda xatolik: {ex}")

    try:
        cur_html = call.message.html_text or call.message.text or ""
        await call.message.edit_text(
            cur_html + "\n\n✅ <b>Natija testga muvaffaqiyatli qo'shildi va o'quvchiga yuborildi!</b>",
            reply_markup=None
        )
    except Exception:
        pass
    await call.answer("Natija testga qo'shildi!", show_alert=True)


@admin_router.callback_query(F.data.startswith("adm_reject_late_"))
async def admin_reject_late_cb(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    sub_id = int(call.data.split("_")[3])
    sub = await test_db.db(test_db.get_submission_by_id, sub_id)
    if not sub:
        await call.answer("Topshiriq topilmadi!", show_alert=True)
        return

    # 2 = rad etilgan
    await test_db.db(test_db.set_submission_late_status, sub_id, 2)

    try:
        cur_html = call.message.html_text or call.message.text or ""
        await call.message.edit_text(
            cur_html + "\n\n❌ <b>Ushbu kech topshirilgan natija hisobga olinmadi (rad etildi).</b>",
            reply_markup=None
        )
    except Exception:
        pass
    await call.answer("Natija rad etildi!", show_alert=True)

@admin_router.callback_query(F.data.startswith("adm_rasch_"))
async def admin_test_rasch_eval(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return

    test_id = int(call.data.split("_")[2])
    test = test_db.get_test_by_id(test_id)
    if not test:
        await call.answer("Test topilmadi!", show_alert=True)
        return

    await call.answer("⏳ Rasch modeli hisoblanmoqda...")
    status_msg = await call.message.answer("⏳ <i>Rasch JMLE modeli bo'yicha savollar qiyinligi va o'quvchilar qobiliyati hisoblanmoqda...</i>")

    res = await test_db.db(test_db.evaluate_test_rasch, test_id, auto_update_db=True)
    if not res or not res.get("students"):
        await status_msg.edit_text(
            f"⚠️ <b>«{test['title']}»</b> testi uchun Rasch modelini hisoblashning imkoni bo'lmadi.\n\n"
            f"📌 <i>Talab: Rasch modeli ishlashi uchun testni kamida 2 nafar o'quvchi topshirgan bo'lishi kerak.</i>"
        )
        return

    meta = res.get("meta", {})
    students = res.get("students", [])
    items = res.get("items", [])

    is_active = (test.get("is_active", 1) == 1)
    is_pub = await test_db.db(test_db.is_test_results_published, test_id)

    status_note = ""
    if is_active or not is_pub:
        status_note = (
            f"⚠️ <b>Eslatma:</b> Test hozirda to'xtatilmagan yoki natijalar o'quvchilarga hali e'lon qilinmagan. "
            f"O'quvchilarga natijalar ko'rinmaydi (ularga <i>«Javoblaringiz tekshirilmoqda»</i> ko'rinadi).\n"
            f"Testni to'xtatib, barchaga natijalarni e'lon qilish uchun quyidagi tugmani bosing 👇\n\n"
        )

    noisy = [it for it in items if it.get("is_noisy")]
    if noisy:
        noisy_summary = f"⚠️ <b>Shovqinli savollar ({len(noisy)} ta):</b> " + ", ".join([f"<code>{it['item_label']}</code>" for it in noisy[:10]]) + "\n\n"
    else:
        noisy_summary = "✨ <b>Savollar sifati:</b> Barcha savollar sifatli (shovqin yo'q)\n\n"

    text = (
        f"🧮 <b>Rasch Modeli (JMLE) Baholash Natijalari</b>\n\n"
        f"📖 <b>Test:</b> {test['title']} (<code>#{test['test_code']}</code>)\n"
        f"👥 <b>Talabalar:</b> {meta.get('n_students', meta.get('num_students', len(students)))} nafar\n"
        f"❓ <b>Elementlar:</b> {meta.get('n_items', meta.get('num_items', len(items)))} ta (55 ta band)\n"
        f"🔄 <b>Iteratsiyalar:</b> {meta.get('iterations', 0)} (Konvergensiya: {meta.get('converged', True)})\n\n"
        f"{noisy_summary}"
        f"{status_note}"
        f"🏆 <b>O'quvchilar darajalari va yakuniy ballari (0-100):</b>\n"
    )

    for idx, s in enumerate(students[:25], 1):
        sid = s.get('student_id')
        u = await test_db.db(test_db.get_user, sid) if sid else None
        name = u['fullname'] if u else f"ID: {sid}"
        theta_val = s.get('theta', 0.0)
        text += f"<b>{idx}. {name}</b>: <b>{s['final_score']} ball</b> [🎖 <b>{s['grade']}</b>] (θ={theta_val:+.2f})\n"

    if len(students) > 25:
        text += f"\n<i>...va yana {len(students) - 25} nafar talaba.</i>"

    buttons = [
        [InlineKeyboardButton(text="🔍 Savollar qiyinligi va Shovqin tahlili", callback_data=f"adm_item_diag_{test_id}")],
        [InlineKeyboardButton(text="📢 Testni to'xtatish va Natijalarni e'lon qilish", callback_data=f"adm_broadcast_results_{test_id}")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data=f"adm_tstat_{test_id}")]
    ]
    await status_msg.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@admin_router.callback_query(F.data.startswith("adm_item_diag_"))
async def admin_item_diag_cb(call: CallbackQuery):
    """Admin uchun savollarning qiyinligi va shovqin (infit/outfit) tahlili."""
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    test_id = int(call.data.split("_")[3])
    test = test_db.get_test_by_id(test_id)
    if not test:
        await call.answer("Test topilmadi!", show_alert=True)
        return

    res = await test_db.db(test_db.evaluate_test_rasch, test_id)
    if not res or not res.get("items"):
        await call.answer("Tahlil uchun kamida 2 nafar o'quvchi topshirgan bo'lishi kerak!", show_alert=True)
        return

    items = res.get("items", [])
    n_students = res.get("meta", {}).get("n_students", 0)
    noisy = [it for it in items if it.get("is_noisy")]

    # Sort by difficulty
    items_sorted = sorted(items, key=lambda x: x.get("difficulty_b", 0), reverse=True)
    hardest = items_sorted[:5]
    easiest = items_sorted[-5:]
    easiest.reverse()

    text = (
        f"🔍 <b>«{test['title']}» — Savollar Sifati va Shovqin Tahlili</b>\n\n"
        f"👥 <b>Ishtirokchilar:</b> {n_students} nafar | ❓ <b>Jami elementlar:</b> 55 ta band\n\n"
    )

    if noisy:
        text += f"⚠️ <b>SHOVQINLI SAVOLLAR ({len(noisy)} TA):</b>\n"
        text += f"<i>(Infit yoki Outfit > 1.3 — kutilmagan javoblar yuqori bo'lgan):</i>\n"
        for it in noisy:
            solved = it.get('solved_count', 0)
            score_pts = it.get('item_score', 0)
            infit = it.get('infit_mnsq', 1.0)
            outfit = it.get('outfit_mnsq', 1.0)
            text += f"• <b>{it['item_label']}</b>: {solved}/{n_students} kishi yechgan | Ball: <b>{score_pts}</b> | Infit: <code>{infit}</code>, Outfit: <code>{outfit}</code>\n"
        text += f"\nℹ️ <i>Eslatma: Ushbu savollarni to'g'ri topgan o'quvchilarga ball to'liq berilgan.</i>\n\n"
    else:
        text += f"✨ <b>SHOVQINLI SAVOLLAR:</b>\nHech qanday shovqinli savol aniqlanmadi. Barcha 55 ta savol Rasch modeliga ideal mos tushgan!\n\n"

    text += f"🔥 <b>ENG QIYIN 5 TA SAVOL (Eng yuqori ball):</b>\n"
    for it in hardest:
        solved = it.get('solved_count', 0)
        score_pts = it.get('item_score', 0)
        b_val = it.get('difficulty_b', 0)
        text += f"• <b>{it['item_label']}</b>: {solved}/{n_students} kishi topgan (Qiyinlik: <code>{b_val:+.2f}</code>) ➔ <b>{score_pts} ball</b>\n"

    text += f"\n🟢 <b>ENG OSON 5 TA SAVOL (Ko'pchilik yechgan):</b>\n"
    for it in easiest:
        solved = it.get('solved_count', 0)
        score_pts = it.get('item_score', 0)
        b_val = it.get('difficulty_b', 0)
        text += f"• <b>{it['item_label']}</b>: {solved}/{n_students} kishi topgan (Qiyinlik: <code>{b_val:+.2f}</code>) ➔ <b>{score_pts} ball</b>\n"

    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧮 Rasch reytingiga qaytish", callback_data=f"adm_rasch_{test_id}")],
        [InlineKeyboardButton(text="⬅️ Test boshqaruviga qaytish", callback_data=f"adm_tstat_{test_id}")]
    ])

    try:
        await call.message.edit_text(text, reply_markup=back_kb)
    except Exception:
        await call.message.answer(text, reply_markup=back_kb)

@admin_router.callback_query(F.data.startswith("adm_restxt_"))
async def admin_test_res_text(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return

    test_id = int(call.data.split("_")[2])
    test = test_db.get_test_by_id(test_id)
    if not test:
        await call.answer("Test topilmadi!", show_alert=True)
        return

    results = await test_db.db(test_db.get_test_results_leaderboard, test_id)
    if not results:
        await call.answer("Bu testni hali hech kim topshirmagan!", show_alert=True)
        return

    text = f"🏆 <b>«{test['title']}» Natijalari (Matn ko'rinishida):</b>\n"
    text += f"👥 <b>Jami ishtirokchilar:</b> {len(results)} nafar | 🕒 {format_uzb_time()}\n\n"

    for idx, row in enumerate(results[:30], 1):
        grade = test_db.calculate_grade(row["score"])
        text += f"<b>{idx}. {row['fullname']}</b> — <b>{row['score']} ball</b> [🎖 {grade}] ({row['correct_count']} ta to'g'ri)\n"

    if len(results) > 30:
        text += f"\n<i>...va yana {len(results) - 30} nafar ishtirokchi (barchasini ko'rish uchun PDF yuklab oling).</i>"

    buttons = [
        [InlineKeyboardButton(text="📑 PDF hisobotni yuklab olish", callback_data=f"adm_respdf_{test_id}")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data=f"adm_tstat_{test_id}")]
    ]

    try:
        await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    except Exception:
        await call.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()

@admin_router.callback_query(F.data.startswith("adm_respdf_"))
async def admin_test_res_pdf(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return

    test_id = int(call.data.split("_")[2])
    test = test_db.get_test_by_id(test_id)
    if not test:
        await call.answer("Test topilmadi!", show_alert=True)
        return

    results = await test_db.db(test_db.get_test_results_leaderboard, test_id)
    if not results:
        await call.answer("Ushbu testni hali hech kim topshirmagan, PDF chiqarib bo'lmaydi.", show_alert=True)
        return

    await call.answer("⏳ PDF hisobot yaratilmoqda...")
    status_msg = await call.message.answer("⏳ <i>PDF reyting jadvali shakllantirilmoqda, iltimos kuting...</i>")

    async with HEAVY_TASKS_SEMAPHORE:
        pdf_path = await test_db.db(test_db.generate_test_results_pdf, test_id)
    if pdf_path and os.path.exists(pdf_path):
        try:
            caption = (
                f"📑 <b>«{test['title']}»</b> bo'yicha rasmiy test natijalari va reyting hisoboti.\n\n"
                f"👥 <b>Ishtirokchilar:</b> {len(results)} nafar\n"
                f"🕒 <b>Sana:</b> {format_uzb_time()}"
            )
            await bot.send_document(
                chat_id=call.from_user.id,
                document=FSInputFile(pdf_path),
                caption=caption
            )
            await status_msg.delete()
        except Exception as e:
            log.error(f"PDF yuborishda xatolik: {e}")
            await status_msg.edit_text(f"⚠️ PDF yuborishda xatolik yuz berdi: {e}")
        finally:
            try:
                if os.path.exists(pdf_path):
                    os.remove(pdf_path)
            except Exception:
                pass
    else:
        await status_msg.edit_text("⚠️ PDF hisobotini shakllantirishda xatolik yuz berdi.")


@admin_router.callback_query(F.data.startswith("adm_schedule_"))
async def adm_schedule_start(call: CallbackQuery, state: FSMContext):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    test_id = int(call.data.split("_")[2])
    test = test_db.get_test_by_id(test_id)
    if not test:
        await call.answer("Test topilmadi!", show_alert=True)
        return

    sched_date = test.get('scheduled_date') or ''
    sched_start = test.get('scheduled_start') or ''
    sched_end = test.get('scheduled_end') or ''

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏰ Yangi vaqt belgilash", callback_data=f"adm_sched_set_{test_id}")],
        [InlineKeyboardButton(text="🗑 Jadval vaqtini bekor qilish", callback_data=f"adm_sched_clear_{test_id}")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data=f"adm_tstat_{test_id}")],
    ])

    cur_sched = f"⏰ {sched_date} | {sched_start}–{sched_end}" if (sched_start and sched_end) else "➖ Belgilanmagan"
    text = (
        f"⏰ <b>Avtomatik vaqt boshqaruvi</b>\n\n"
        f"📖 <b>Test:</b> {test['title']}\n"
        f"🕐 <b>Hozirgi jadval:</b> {cur_sched}\n\n"
        f"<i>Test belgilangan vaqtda avtomatik <b>faollashadi</b> (boshlanish vaqti kelganda)\n"
        f"va belgilangan vaqtda avtomatik <b>to'xtatiladi</b> (tugash vaqti kelganda).\n"
        f"Siz hech narsa qilmasangiz ham bot o'zi boshqaradi.</i>"
    )
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except Exception:
        await call.message.answer(text, reply_markup=kb)
    await call.answer()


@admin_router.callback_query(F.data.startswith("adm_sched_clear_"))
async def adm_sched_clear(call: CallbackQuery):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    test_id = int(call.data.split("_")[3])
    await test_db.db(test_db.clear_test_schedule, test_id)
    await call.answer("✅ Jadval vaqti bekor qilindi!", show_alert=True)
    # test boshqaruv sahifasiga qaytish
    call.data = f"adm_tstat_{test_id}"
    await admin_test_stats_detail(call)


@admin_router.callback_query(F.data.startswith("adm_sched_set_"))
async def adm_sched_set_start(call: CallbackQuery, state: FSMContext):
    if not (await test_db.db(test_db.is_admin, call.from_user.id, ADMIN_ID)):
        return
    test_id = int(call.data.split("_")[3])
    await state.update_data(schedule_test_id=test_id)
    await state.set_state(ScheduleState.waiting_date)

    now_uzb = datetime.now(UZB_TZ)
    today = now_uzb.strftime("%d.%m.%Y")
    tomorrow = (now_uzb + timedelta(days=1)).strftime("%d.%m.%Y")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📅 Bugun ({today})", callback_data=f"sched_date_{today}_{test_id}")],
        [InlineKeyboardButton(text=f"📅 Ertaga ({tomorrow})", callback_data=f"sched_date_{tomorrow}_{test_id}")],
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"adm_schedule_{test_id}")],
    ])
    try:
        await call.message.edit_text(
            "📅 <b>Test o'tkaziladigan sanani tanlang yoki kiriting:</b>\n\n"
            "<i>Format: <code>26.09.2026</code></i>",
            reply_markup=kb
        )
    except Exception:
        await call.message.answer(
            "📅 <b>Sanani tanlang yoki kiriting (26.09.2026 formatida):</b>",
            reply_markup=kb
        )
    await call.answer()


@admin_router.callback_query(F.data.startswith("sched_date_"))
async def adm_sched_date_chosen(call: CallbackQuery, state: FSMContext):
    parts = call.data.split("_")
    # sched_date_DD.MM.YYYY_testid
    chosen_date = parts[2]
    test_id = int(parts[3])
    await state.update_data(schedule_date=chosen_date, schedule_test_id=test_id)
    await state.set_state(ScheduleState.waiting_start)
    await call.message.edit_text(
        f"✅ Sana: <b>{chosen_date}</b>\n\n"
        f"🕐 <b>Test boshlanish vaqtini kiriting</b> (UZB vaqti):\n"
        f"<i>Format: <code>19:30</code></i>"
    )
    await call.answer()


@admin_router.message(ScheduleState.waiting_date)
async def adm_sched_date_text(message: Message, state: FSMContext):
    import re
    text = message.text.strip()
    if not re.match(r'^\d{2}\.\d{2}\.\d{4}$', text):
        await message.answer("❌ Noto'g'ri format. Iltimos: <code>26.09.2026</code>")
        return
    data = await state.get_data()
    test_id = data.get('schedule_test_id')
    await state.update_data(schedule_date=text)
    await state.set_state(ScheduleState.waiting_start)
    await message.answer(
        f"✅ Sana: <b>{text}</b>\n\n"
        f"🕐 <b>Boshlanish vaqtini kiriting</b> (UZB):\n"
        f"<i>Format: <code>19:30</code></i>"
    )


@admin_router.message(ScheduleState.waiting_start)
async def adm_sched_start_time(message: Message, state: FSMContext):
    import re
    text = message.text.strip()
    if not re.match(r'^\d{1,2}:\d{2}$', text):
        await message.answer("❌ Noto'g'ri format. Iltimos: <code>19:30</code>")
        return
    # HH:MM formatga keltirish
    h, m = text.split(":")
    text = f"{int(h):02d}:{m}"
    await state.update_data(schedule_start=text)
    await state.set_state(ScheduleState.waiting_end)
    await message.answer(
        f"✅ Boshlanish: <b>{text}</b>\n\n"
        f"🕕 <b>Tugash vaqtini kiriting</b> (UZB):\n"
        f"<i>Format: <code>22:00</code></i>"
    )


@admin_router.message(ScheduleState.waiting_end)
async def adm_sched_end_time(message: Message, state: FSMContext):
    import re
    text = message.text.strip()
    if not re.match(r'^\d{1,2}:\d{2}$', text):
        await message.answer("❌ Noto'g'ri format. Iltimos: <code>22:00</code>")
        return
    h, m = text.split(":")
    text = f"{int(h):02d}:{m}"

    data = await state.get_data()
    test_id = data.get('schedule_test_id')
    schedule_date = data.get('schedule_date', '')
    schedule_start = data.get('schedule_start', '')

    await state.clear()

    # Saqlash
    await test_db.db(test_db.set_test_schedule, test_id, schedule_date, schedule_start, text)

    test = test_db.get_test_by_id(test_id)
    test_name = test['title'] if test else f"Test #{test_id}"

    await message.answer(
        f"✅ <b>Avtomatik jadval saqlandi!</b>\n\n"
        f"📖 <b>Test:</b> {test_name}\n"
        f"📅 <b>Sana:</b> {schedule_date}\n"
        f"🕐 <b>Boshlanadi:</b> {schedule_start} (UZB)\n"
        f"🕕 <b>Tugaydi:</b> {text} (UZB)\n\n"
        f"<i>Bot belgilangan vaqtda testni avtomatik faollashtiradi va to'xtatadi.</i>"
    )


