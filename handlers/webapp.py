"""
AIOHTTP Web Application Server and Handlers for Mini App & Dashboard.
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, Tuple, List
import html

from aiohttp import web
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, FSInputFile

import test_db
import rasch_engine
from core import (
    UZB_TZ, format_uzb_time, TG_ICONS, tg_e,
    _dashboard_overview_cache, _dashboard_users_cache, _dashboard_tests_cache, invalidate_dashboard_cache,
    get_test_schedule_status, get_test_min_submit_info,
    BOT_TOKEN, ADMIN_ID, PORT, WEBAPP_URL, CACHED_BOT_USERNAME, log, bot,
    GLOBAL_RATE_LIMITER, SUBMIT_CONCURRENCY_SEMAPHORE, COMPARE_CONCURRENCY_SEMAPHORE
)
from services import (
    MAINT_START_TEXT, MAINT_END_TEXT,
    send_broadcast_to_users, delete_broadcast_batch_from_users
)
from .common import (
    get_all_admin_ids, send_test_card_to_user_chat,
    clean_blocked_users_from_db, get_inactive_target_users,
    send_inactive_warning_messages, notify_admins_test_results_published
)

async def handle_submit_test_api(request):
    """Mini App dan kelgan javoblarni tekshirish va bot orqali faqat foydalanuvchiga natijani xabar qilish"""
    try:
        data = await request.json()
        test_id = data.get("test_id", 1)
        user_tg_id = data.get("user_tg_id")
        if not user_tg_id:
            init_data = data.get("init_data") or request.headers.get("X-Telegram-Init-Data", "")
            if init_data:
                import urllib.parse
                try:
                    parsed = dict(urllib.parse.parse_qsl(init_data))
                    if "user" in parsed:
                        u_dict = json.loads(parsed["user"])
                        if u_dict and u_dict.get("id"):
                            user_tg_id = int(u_dict["id"])
                except Exception:
                    pass
            if not user_tg_id and request.rel_url.query.get("tg_id"):
                try:
                    user_tg_id = int(request.rel_url.query.get("tg_id", 0))
                except Exception:
                    pass
        user_answers = data.get("answers", {})

        if not user_tg_id or int(user_tg_id) <= 0:
            return web.json_response({
                "success": False,
                "message": f"⚠️ Web orqali ishlash mumkin emas! Testni faqat rasmiy Telegram botimiz (@{CACHED_BOT_USERNAME}) va Mini ilova orqali topshirish mumkin."
            }, status=403)

        user_obj = await asyncio.to_thread(test_db.get_user, user_tg_id)
        if not test_db.is_admin(user_tg_id or 0, ADMIN_ID):
            if not user_obj:
                return web.json_response({
                    "success": False,
                    "message": "⚠️ Iltimos, avval Telegram botimizda ro'yxatdan o'ting!"
                }, status=403)
            if user_obj.get("status") == "pending":
                return web.json_response({
                    "success": False,
                    "message": "⏳ Arizangiz hali administrator tomonidan tasdiqlanmagan. Ruxsat berilgach test topshirishingiz mumkin."
                }, status=403)
            if user_obj.get("status") in ["blocked", "rejected"]:
                return web.json_response({
                    "success": False,
                    "message": "⛔️ Sizning profilingizga ruxsat berilmagan yoki to'xtatilgan."
                }, status=403)

        # ── RATE LIMITING (1-3 soniya cheklovi) ─────────────────
        rate_key = f"submit_{user_tg_id or request.remote or 'unknown'}"
        is_allowed, retry_sec = await GLOBAL_RATE_LIMITER.check(rate_key, max_requests=1, window_seconds=2.0)
        if not is_allowed and not test_db.is_admin(user_tg_id or 0, ADMIN_ID):
            return web.json_response({
                "success": False,
                "retry_after": retry_sec,
                "message": f"Iltimos, {retry_sec} soniya kuting! So'rovingiz navbatda qayta ishlanmoqda..."
            }, status=429)

        async with SUBMIT_CONCURRENCY_SEMAPHORE:
            test_obj = await asyncio.to_thread(test_db.get_test_by_id, test_id)
            if not test_obj:
                return web.json_response({
                    "success": False,
                    "message": "Test topilmadi!"
                }, status=404)

            # Bo'sh (0 ta belgilangan) testni topshirishni bloklash (tasodifiy bosilib ketishdan himoya)
            has_any_answer = False
            if isinstance(user_answers, dict):
                for k, v in user_answers.items():
                    if v is not None and str(v).strip():
                        has_any_answer = True
                        break

            if not has_any_answer and not test_db.is_admin(user_tg_id, ADMIN_ID):
                return web.json_response({
                    "success": False,
                    "message": "⚠️ Testda birorta ham savolga javob belgilanmagan! Bo'sh testni topshirib bo'lmaydi. Iltimos, kamida bitta javobni belgilang."
                }, status=400)

        sched_stat = get_test_schedule_status(test_obj)
        if sched_stat["is_upcoming"] and not test_db.is_admin(user_tg_id, ADMIN_ID):
            sdate = test_obj.get('scheduled_date') or 'Bugun'
            sstart = test_obj.get('scheduled_start') or ''
            return web.json_response({
                "success": False,
                "message": f"Test hali boshlanmagan! Boshlanish vaqti: {sdate} {sstart} (UZB)"
            }, status=400)

        # Dastlabki 45 daqiqa davomida javob topshirishni cheklash (Milliy sertifikat qoidasi)
        min_submit_info = get_test_min_submit_info(test_obj)
        if not min_submit_info["can_submit"] and not test_db.is_admin(user_tg_id, ADMIN_ID):
            rem_sec = min_submit_info["remaining_seconds"]
            rem_min = rem_sec // 60
            rem_s = rem_sec % 60
            unlock_t = min_submit_info["unlock_time_str"]
            return web.json_response({
                "success": False,
                "error_code": "EARLY_SUBMISSION_BLOCKED",
                "remaining_seconds": rem_sec,
                "unlock_time": unlock_t,
                "message": (
                    f"⚠️ Test boshlanganidan so'ng dastlabki 45 daqiqa davomida javob topshirish mumkin emas!\n\n"
                    f"⏱ Qolgan vaqt: {rem_min} daqiqa {rem_s} soniya\n"
                    f"🕒 Topshirish ochiladigan vaqt: {unlock_t}\n\n"
                    f"Sababi: Milliy sertifikat qoidalari va imtihon shaffofligini ta'minlash, "
                    f"shoshmashosharlik hamda tasodifiy (tavakkal) belgilashlarning oldini olish maqsadida "
                    f"test boshlanganidan so'ng dastlabki 45 daqiqa davomida javoblarni topshirish taqiqlanadi. "
                    f"Iltimos, ajratilgan vaqtdan unumli foydalanib savollarni qayta tekshirib chiqing!"
                )
            }, status=400)

        # Kech topshirilgan holatni aniqlash
        is_late_submission = False
        if not test_db.is_admin(user_tg_id, ADMIN_ID):
            if sched_stat.get("is_closed") or test_obj.get("is_active", 1) == 0 or test_obj.get("results_published", 0) == 1:
                is_late_submission = True

        # Bazada tekshirish va saqlash (Asinxron navbat / Semaphore bilan)
        async with SUBMIT_CONCURRENCY_SEMAPHORE:
            result = await asyncio.to_thread(
                test_db.check_and_save_submission,
                test_id, user_tg_id, user_answers,
                is_late=1 if is_late_submission else 0
            )
        invalidate_dashboard_cache()

        now_uzb = datetime.now(UZB_TZ)
        time_str_sec = now_uzb.strftime("%H:%M:%S")
        datetime_str_sec = now_uzb.strftime("%d.%m.%Y %H:%M:%S")

        if is_late_submission:
            # 1. Foydalanuvchiga Telegram xabari: kech topshirdingiz, hisobga olinmaydi
            if user_tg_id:
                msg_user = (
                    f"⚠️ <b>Hurmatli {result['fullname']}, siz testni belgilangan vaqtdan kech topshirdingiz!</b>\n\n"
                    f"📚 <b>Test:</b> {result['test_title']} (<code>#{result['test_code']}</code>)\n"
                    f"🕒 <b>Topshirilgan vaqt:</b> <b>{time_str_sec}</b> ({datetime_str_sec})\n"
                    f"🚫 <b>Holat:</b> <b>Natijangiz umumiy hisobga olinmaydi!</b>\n\n"
                    f"ℹ️ Test javoblaringiz qabul qilindi va ko'rib chiqish uchun <b>adminga yuborildi</b>. "
                    f"Agar admin ruxsat bersa, natijangiz umumiy testga qo'shiladi va bu haqda sizga xabar beriladi."
                )
                try:
                    await bot.send_message(chat_id=user_tg_id, text=msg_user)
                except Exception as ex:
                    log.warning(f"Foydalanuvchiga kechikish xabari yuborishda xatolik: {ex}")

            # 2. Adminga bildirishnoma: nechta ishlagan, vaqti soniyasigacha va qaror tugmalari
            sub_id = result.get("submission_id")
            corr_cnt = result.get("correct_count", 0)
            score_val = result.get("score", 0.0)
            u_info = test_db.get_user(user_tg_id)
            username_str = f" (@{u_info.get('username')})" if (u_info and u_info.get('username')) else ""
            phone_str = f"\n📞 <b>Telefon:</b> {result.get('phone', '—')}" if result.get('phone') else ""

            admin_text = (
                f"⏰ <b>DIQQAT: Kech topshirilgan test javobi keldi!</b>\n\n"
                f"👤 <b>O'quvchi:</b> {result['fullname']}{username_str}\n"
                f"🆔 <b>Telegram ID:</b> <code>{user_tg_id}</code>{phone_str}\n"
                f"📚 <b>Test:</b> {result['test_title']} (<code>#{result['test_code']}</code>)\n"
                f"🕒 <b>Topshirilgan vaqt:</b> <b>{datetime_str_sec}</b>\n\n"
                f"📊 <b>Ishlagan natijasi:</b> <b>{corr_cnt} ta to'g'ri</b> (55 tadan) — <b>{score_val} ball</b>\n\n"
                f"❓ <b>Ushbu o'quvchining natijasini testga qo'shamizmi?</b>"
            )

            admin_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Testga natijasini qo'shish", callback_data=f"adm_accept_late_{sub_id}")],
                [InlineKeyboardButton(text="❌ Hisobga olmaslik (Rad etish)", callback_data=f"adm_reject_late_{sub_id}")]
            ])

            target_admin = ADMIN_ID
            if test_obj.get("created_by") and test_db.is_admin(test_obj.get("created_by"), ADMIN_ID):
                target_admin = test_obj.get("created_by")

            try:
                await bot.send_message(chat_id=ADMIN_ID, text=admin_text, reply_markup=admin_kb)
            except Exception as ex_a:
                log.warning(f"Admin alert error: {ex_a}")

            if target_admin != ADMIN_ID:
                try:
                    await bot.send_message(chat_id=target_admin, text=admin_text, reply_markup=admin_kb)
                except Exception:
                    pass

            client_data = dict(result)
            client_data["is_published"] = False
            client_data["is_late"] = True
            client_data["score"] = None
            client_data["grade"] = "Kech topshirildi"
            client_data["correct_count"] = None
            client_data["incorrect_count"] = None
            client_data["unanswered_count"] = None
            client_data["rasch_theta"] = None
            client_data["details"] = None

            return web.json_response({
                "success": True,
                "is_late": True,
                "message": f"⚠️ Siz testni belgilangan vaqtdan kech topshirdingiz (Soat: {time_str_sec})! Natijangiz hisobga olinmaydi va adminga ko'rib chiqish uchun yuborildi.",
                "data": client_data
            })
        else:
            # Oddiy, vaqtida topshirilgan test
            if user_tg_id:
                msg_user = (
                    f"✅ <b>Hurmatli {result['fullname']}, javoblaringiz qabul qilindi!</b>\n\n"
                    f"📚 <b>Test:</b> {result['test_title']} (<code>#{result['test_code']}</code>)\n"
                    f"📌 <b>Holat:</b> ⏳ <b>Javoblaringiz qabul qilindi (Jarayonda)...</b>\n"
                    f"🕒 <b>Topshirilgan vaqt:</b> {format_uzb_time()}\n\n"
                    f"ℹ️ <b>Eslatma:</b> Test hozirda barcha o'quvchilar uchun davom etmoqda. "
                    f"Admin testni yakunlab, <b>Rasch modeli (JMLE)</b> bo'yicha tahlil o'tkazgach, "
                    f"to'g'ri ishlangan savollar soni, yakuniy ballingiz va Milliy sertifikat darajangiz botingizga shaxsiy xabar qilib yuboriladi!\n\n"
                    f"🏆 <i>Javoblaringiz tizimda muvaffaqiyatli saqlandi.</i>"
                )
                try:
                    await bot.send_message(chat_id=user_tg_id, text=msg_user)
                except Exception as ex:
                    log.warning(f"Foydalanuvchiga xabar yuborishda xatolik: {ex}")

            client_data = dict(result)
            client_data["is_published"] = False
            client_data["is_late"] = False
            client_data["score"] = None
            client_data["grade"] = "Kutilmoqda"
            client_data["correct_count"] = None
            client_data["incorrect_count"] = None
            client_data["unanswered_count"] = None
            client_data["rasch_theta"] = None
            client_data["details"] = None

            return web.json_response({"success": True, "data": client_data})

    except Exception as e:
        log.error(f"Submit API Error: {e}", exc_info=True)
        return web.json_response({"success": False, "message": str(e)}, status=400)

async def handle_create_test_api(request):
    """Admin Mini App dan yuborilgan yangi test, kalitlar va ballarni saqlash"""
    try:
        data = await request.json()
        title = data.get("title", "").strip() or "Ona tili Milliy Sertifikat Testi"
        test_code = data.get("test_code", "").strip().upper()
        if not test_code:
            test_code = test_db.get_next_test_code()
        
        subject = data.get("subject", "Ona tili").strip() or "Ona tili"
        answers = data.get("answers", {})
        time_limit_min = int(data.get("time_limit_min", 0))
        key_access_code = data.get("key_access_code", "").strip()
        youtube_url = (data.get("youtube_url") or "").strip()

        creator_id = int(data.get("creator_tg_id", 0) or data.get("tg_id", 0))
        if not creator_id:
            init_data = data.get("init_data", "") or request.headers.get("X-Telegram-Init-Data", "")
            if init_data:
                try:
                    import urllib.parse
                    parsed = dict(urllib.parse.parse_qsl(init_data))
                    if 'user' in parsed:
                        u_dict = json.loads(parsed['user'])
                        if u_dict and u_dict.get('id'):
                            creator_id = int(u_dict['id'])
                except Exception:
                    pass

        creator_name = ""
        if creator_id:
            u_info = test_db.get_user(creator_id)
            if u_info:
                creator_name = u_info.get("fullname", "")
            if not creator_name and creator_id == ADMIN_ID:
                creator_name = "Bosh Admin"

        if not creator_name:
            creator_name = "Admin"

        success = test_db.create_test(
            test_code=test_code,
            title=title,
            subject=subject,
            answers=answers,
            time_limit_min=time_limit_min,
            key_access_code=key_access_code,
            created_by=creator_id,
            created_by_name=creator_name,
            youtube_url=youtube_url
        )

        sched_date = data.get("scheduled_date", "").strip()
        sched_start = data.get("scheduled_start", "").strip()
        sched_end = data.get("scheduled_end", "").strip()

        if success:
            try:
                t_obj = test_db.get_test_by_code(test_code)
                test_id = t_obj['id'] if t_obj else 0
                
                # Jadval vaqtini sozlash
                if sched_start and sched_end and test_id:
                    test_db.set_test_schedule(test_id, sched_date, sched_start, sched_end)

                if youtube_url and test_id:
                    test_db.set_test_youtube_url(test_id, youtube_url)

                time_info = f"⏱ <b>Vaqt chegarasi:</b> {time_limit_min} daqiqa\n" if time_limit_min > 0 else ""
                sched_info = f"⏰ <b>O'tkazilish vaqti:</b> {sched_date} {sched_start}–{sched_end} (UZB)\n" if (sched_start and sched_end) else ""
                yt_info = f"🎬 <b>Video tahlil:</b> {youtube_url}\n" if youtube_url else ""
                
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📥 Ha, PDF yuklayman", callback_data=f"ask_pdf_{test_id}")],
                    [InlineKeyboardButton(text="❌ Yo'q, kerak emas", callback_data=f"no_pdf_{test_id}")]
                ])

                # Testni yaratgan adminga (bosh admin yoki tayinlangan admin) PDF so'rovini yuborish
                creator_id = int(data.get("creator_tg_id", 0) or data.get("tg_id", 0))
                if not creator_id:
                    init_data = data.get("init_data", "") or request.headers.get("X-Telegram-Init-Data", "")
                    if init_data:
                        try:
                            import urllib.parse
                            parsed = dict(urllib.parse.parse_qsl(init_data))
                            if 'user' in parsed:
                                u_dict = json.loads(parsed['user'])
                                if u_dict and u_dict.get('id'):
                                    creator_id = int(u_dict['id'])
                        except Exception:
                            pass

                target_chat_id = creator_id if (creator_id and test_db.is_admin(creator_id, ADMIN_ID)) else ADMIN_ID

                # Testni yaratgan adminga (bosh admin yoki tayinlangan admin) PDF so'rovini yuborish
                await bot.send_message(
                    chat_id=target_chat_id,
                    text=(
                        f"✅ <b>Yangi test yaratildi va saqlandi!</b>\n\n"
                        f"📖 <b>Nomi:</b> {title} (<code>#{test_code}</code>)\n"
                        f"📌 <b>Fani:</b> {subject}\n"
                        f"{sched_info}"
                        f"{time_info}"
                        f"🎯 <i>Barcha 55 ta savol kalitlari muvaffaqiyatli saqlandi!</i>\n\n"
                        f"📥 <b>Ushbu test uchun PDF fayl yuklaysizmi?</b>"
                    ),
                    reply_markup=kb
                )

                # Agar testni tayinlangan admin yaratgan bo'lsa -> Bosh Adminga PDF so'rovsiz, faqat 1 ta toza bildirishnoma:
                if target_chat_id != ADMIN_ID:
                    try:
                        creator_u = test_db.get_user(target_chat_id)
                        creator_name = creator_u.get("fullname", "Admin") if creator_u else f"Admin (ID: {target_chat_id})"
                        await bot.send_message(
                            chat_id=ADMIN_ID,
                            text=(
                                f"📢 <b>Yangi test yaratildi!</b>\n\n"
                                f"📖 <b>Nomi:</b> {title} (<code>#{test_code}</code>)\n"
                                f"📌 <b>Fani:</b> {subject}\n"
                                f"{sched_info}"
                                f"{time_info}"
                                f"👤 <b>Yaratuvchi:</b> <b>{creator_name}</b> tomonidan yaratildi."
                            )
                        )
                    except Exception as e_adm:
                        log.warning(f"Bosh adminga xabar yuborishda xatolik: {e_adm}")
                else:
                    # Agar Bosh Admin yaratgan bo'lsa -> tayinlangan adminlarga ham PDF so'rovsiz 1 ta toza bildirishnoma:
                    try:
                        all_adms = test_db.get_all_admins()
                        for adm in (all_adms or []):
                            aid = adm.get("tg_id")
                            if aid and aid != ADMIN_ID:
                                await bot.send_message(
                                    chat_id=aid,
                                    text=(
                                        f"📢 <b>Yangi test yaratildi!</b>\n\n"
                                        f"📖 <b>Nomi:</b> {title} (<code>#{test_code}</code>)\n"
                                        f"📌 <b>Fani:</b> {subject}\n"
                                        f"{sched_info}"
                                        f"{time_info}"
                                        f"👤 <b>Yaratuvchi:</b> <b>Shaxriyor (Bosh Admin)</b> tomonidan yaratildi."
                                    )
                                )
                    except Exception as e_adm2:
                        log.warning(f"Tayinlangan adminga xabar yuborishda xatolik: {e_adm2}")
            except Exception as ex:
                log.warning(f"Adminga xabar yuborishda xatolik: {ex}")

            return web.json_response({"success": True, "message": "Test muvaffaqiyatli saqlandi!"})
        else:
            return web.json_response({"success": False, "message": "Ma'lumotlar bazasiga yozishda xatolik yuz berdi"}, status=400)

    except Exception as e:
        log.error(f"Create Test API Error: {e}", exc_info=True)
        return web.json_response({"success": False, "message": str(e)}, status=400)

async def handle_admin_get_test_keys(request):
    """Admin Mini App uchun test tafsilotlari va mavjud kalitlarini qaytaradi."""
    try:
        test_id_str = request.query.get("test_id", "")
        if not test_id_str:
            return web.json_response({"success": False, "message": "Test ID ko'rsatilmadi"}, status=400)
        try:
            test_id = int(test_id_str)
        except ValueError:
            return web.json_response({"success": False, "message": "Noto'g'ri test ID"}, status=400)

        test = test_db.get_test_by_id(test_id)
        if not test:
            return web.json_response({"success": False, "message": "Test topilmadi"}, status=404)

        answers = {}
        if test.get("answers_json"):
            try:
                answers = json.loads(test["answers_json"])
            except Exception:
                answers = {}

        return web.json_response({
            "success": True,
            "test": {
                "id": test["id"],
                "test_code": test.get("test_code", ""),
                "title": test.get("title", ""),
                "subject": test.get("subject", "Ona tili"),
                "time_limit_min": test.get("time_limit_min", 0),
                "key_access_code": test.get("key_access_code", "") or "",
                "scheduled_date": test.get("scheduled_date", "") or "",
                "scheduled_start": test.get("scheduled_start", "") or "",
                "scheduled_end": test.get("scheduled_end", "") or "",
                "youtube_url": test.get("youtube_url", "") or "",
                "answers": answers
            }
        })
    except Exception as e:
        log.error(f"Get Test Keys API Error: {e}", exc_info=True)
        return web.json_response({"success": False, "message": str(e)}, status=500)


async def handle_admin_update_test_keys(request):
    """Admin Mini App dan yuborilgan yangi kalitlarni saqlash va natijalarni avtomatik qayta hisoblash."""
    try:
        data = await request.json()
        test_id = int(data.get("test_id", 0))
        if not test_id:
            return web.json_response({"success": False, "message": "Test ID ko'rsatilmadi"}, status=400)

        answers = data.get("answers", {})
        if not answers:
            return web.json_response({"success": False, "message": "Kalitlar kiritilmadi"}, status=400)

        title = data.get("title")
        time_limit_min = data.get("time_limit_min")
        key_access_code = data.get("key_access_code")
        scheduled_date = data.get("scheduled_date")
        scheduled_start = data.get("scheduled_start")
        scheduled_end = data.get("scheduled_end")
        youtube_url = data.get("youtube_url")

        res = test_db.update_test_keys(
            test_id=test_id,
            new_answers=answers,
            title=title,
            time_limit_min=time_limit_min,
            key_access_code=key_access_code,
            scheduled_date=scheduled_date,
            scheduled_start=scheduled_start,
            scheduled_end=scheduled_end,
            youtube_url=youtube_url
        )

        # Adminga Telegram orqali ham bildirishnoma yuborish
        try:
            t_obj = test_db.get_test_by_id(test_id)
            code_str = t_obj.get("test_code", str(test_id)) if t_obj else str(test_id)
            rec_cnt = res.get("recalculated_count", 0)
            await bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    f"✏️ <b>Test #{code_str} kalitlari Mini App orqali yangilandi!</b>\n\n"
                    f"👥 <b>Qayta tekshirilgan o'quvchilar:</b> {rec_cnt} nafar\n"
                    f"🧮 <b>Rasch modeli va reyting:</b> Qayta kalibrlandi ✅\n"
                    f"📑 <b>PDF hisobot:</b> Yangilandi ✅"
                )
            )
        except Exception as e_notify:
            log.warning(f"Admin notify error on key update: {e_notify}")

        return web.json_response(res)
    except Exception as e:
        log.error(f"Update Test Keys API Error: {e}", exc_info=True)
        return web.json_response({"success": False, "message": str(e)}, status=500)

def sanitize_math_expression(val: str) -> str:
    """LaTeX formatidagi matematik ifodalarni toza Unicode formatiga o'tkazish."""
    if not isinstance(val, str):
        val = str(val)
    s = val.strip()

    # Dollar belgilarini olib tashlash ($...$ yoki $$...$$)
    s = s.replace("$", "").strip()

    # Standart LaTeX almashtirishlari
    s = re.sub(r"\\+(?:cdot|times)\b", "*", s)
    s = re.sub(r"\\+pm\b", "±", s)
    s = re.sub(r"\\+pi\b", "π", s)
    s = re.sub(r"\\+(?:degree|\^\s*\\+circ)\b", "°", s)
    s = re.sub(r"\\+(?:left|right)", "", s)
    s = re.sub(r"\\+(?:text|mathrm|mathbf)\{([^}]+)\}", r"\1", s)

    # Ildizlar: \sqrt[3]{...} -> ∛...
    s = re.sub(r"\\+sqrt\[3\]\{([^{}]+)\}", r"∛\1", s)
    s = re.sub(r"\\+sqrt\[3\]([0-9a-zA-Z]+)", r"∛\1", s)

    # Ildizlar: \sqrt{...} -> √...
    while "sqrt{" in s:
        s = re.sub(r"\\+sqrt\{([^{}]+)\}", r"√\1", s)
    s = re.sub(r"\\+sqrt([0-9a-zA-Z]+)", r"√\1", s)

    # Kasrlar: \frac{a}{b} yoki \dfrac{a}{b} -> a/b
    while re.search(r"\\+d?frac\{([^{}]+)\}\{([^{}]+)\}", s):
        s = re.sub(r"\\+d?frac\{([^{}]+)\}\{([^{}]+)\}", r"\1/\2", s)

    # Qavslar va figurali qavslarni tozalash
    s = re.sub(r"\{([^{}]+)\}", r"\1", s)

    # Ortiqcha teskari slesh (\) larni tozalash
    s = s.replace("\\", "")

    # Belgilar atrofidagi bo'shliqlarni me'yorga keltirish
    s = re.sub(r"\s*\+\s*", " + ", s)
    s = re.sub(r"\s*\-\s*", " - ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _sync_extract_keys_gemini(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
    import base64
    import re
    import requests

    gemini_key = (
        os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or test_db.get_setting("gemini_api_key", "").strip()
    )
    if not gemini_key:
        raise ValueError("GEMINI_API_KEY o'rnatilmagan")

    prompt = (
        "Ushbu rasmda Ona tili fanidan Milliy sertifikat test javoblari/kalitlari jadvali berilgan (masalan, Smart books Milliy sertifikat javoblar jadvali).\n"
        "Iltimos, rasmdagi har bir savol javobini diqqat bilan o'qib, faqat to'g'ri JSON formatida qaytar.\n\n"
        "TEST TUZILISHI VA QAT'IY QOIDALAR:\n"
        "1. 1 dan 35 gacha: Variantli savollar. Kalitlar: '1' dan '35' gacha. Qiymati faqat bitta katta harf (A, B, C, D, E, F).\n"
        "2. 36, 37, 38, 39: BITTALIK (1 ta variantli) yopiq/ochiq yozma savollar! DIQQAT: Ularda 'a' va 'b' bandlari BO'LMAYDI. Kalitlar aniq '36', '37', '38', '39' bo'lishi shart!\n"
        "   - Masalan: '36': 'tiniq'\n"
        "   - '37': ': -' (tinish belgilari)\n"
        "   - '38': 'tengdoshlariga (dan)'\n"
        "   - '39': \"o'shanda muharrir edi\"\n"
        "3. 40 va undan keyingilar (40, 41, 42, 43, 44...): 2 QISMLI yozma savollar (har bir savolning a va b bandlari bor)!\n"
        "   Kalitlar: '40a', '40b', '41a', '41b', '42a', '42b', '43a', '43b', '44a', '44b', ...\n"
        "   - Masalan:\n"
        "     '40a': 'kesim', '40b': 'kechishini'\n"
        "     '41a': 'chunki', '41b': 'sabab-oqibat'\n"
        "     '42a': \"ta'did\", '42b': \"yor og'zi/labi\"\n"
        "     '43a': 'sh', '43b': 'mutlaq'\n"
        "     '44a': 'qanoat', '44b': 'isrof'\n\n"
        "MUHIM KO'RSATMALAR:\n"
        "- Jadvaldagi o'qituvchi texnik izohlarini (masalan, '(28 talik katakka sig\\'dirish kerak)', '(: xato)', '(- to\\'g\\'ri)') kalit matnidan tozalab tashla yoki asosiy kalitni qoldir.\n"
        "- Tutuq belgilarini (apostroflarni) to'g'ri saqla (masalan: o'shanda, ta'did, og'zi).\n"
        "- Defisli so'zlarni defisi bilan saqla (masalan: sabab-oqibat).\n"
        "- Alternativ variantlarni slesh bilan saqla (masalan: yor og'zi/labi).\n\n"
        "Qaytadigan javob aynan toza JSON obyekti bo'lsin:\n"
        "{\n"
        '  "1": "B",\n'
        '  "2": "B",\n'
        '  "35": "D",\n'
        '  "36": "tiniq",\n'
        '  "37": ": -",\n'
        '  "38": "tengdoshlariga (dan)",\n'
        '  "39": "o\'shanda muharrir edi",\n'
        '  "40a": "kesim",\n'
        '  "40b": "kechishini",\n'
        '  "41a": "chunki",\n'
        '  "41b": "sabab-oqibat",\n'
        '  "42a": "ta\'did",\n'
        '  "42b": "yor og\'zi/labi",\n'
        '  "43a": "sh",\n'
        '  "43b": "mutlaq",\n'
        '  "44a": "qanoat",\n'
        '  "44b": "isrof"\n'
        "}\n\n"
        "DIQQAT: Faqat toza JSON matnini qaytar, hech qanday qo'shimcha so'z yoki sharh yozma!"
    )

    models_to_try = [
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
        "gemini-3.8-flash",
    ]

    b64_data = base64.b64encode(image_bytes).decode("utf-8")
    payload = {
        "contents": [{
            "parts": [
                {"inlineData": {"mimeType": mime_type, "data": b64_data}},
                {"text": prompt}
            ]
        }]
    }

    last_err = None
    for model_name in models_to_try:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={gemini_key}"
            resp = requests.post(url, json=payload, timeout=60)
            if resp.status_code != 200:
                raise ValueError(f"HTTP {resp.status_code}: {resp.text[:200]}")

            res_json = resp.json()
            candidates = res_json.get("candidates", [])
            if not candidates:
                raise ValueError("Bo'sh javob qaytdi (candidates yo'q)")

            raw_text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()
            match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', raw_text)
            if match:
                raw_text = match.group(1).strip()
            parsed = json.loads(raw_text)
            if isinstance(parsed, dict):
                cleaned = {}
                for k, v in parsed.items():
                    k_clean = str(k).strip().lower().replace("q", "").replace("-savol", "").replace("savol", "").strip()
                    v_str = str(v).strip()
                    # Apostroflar va tirelarni me'yorga keltirish
                    v_str = re.sub(r"[\u2018\u2019\u02BB\u02BC\u00B4`ʼʻ\‘\’\']", "'", v_str)
                    v_str = re.sub(r"[\u2212\u2013\u2014\u2012\u2015\uFE63\uFF0D\u00ad]", "-", v_str)
                    if k_clean.isdigit() and int(k_clean) <= 35:
                        v_str = v_str.upper()
                    else:
                        # Ochiq savollar uchun izohlarni tozalash
                        if k_clean == "37" and (": -" in v_str or ":-" in v_str):
                            v_str = ": -"
                        elif "katakka" in v_str or "sig'dirish" in v_str:
                            v_str = re.sub(r"\s*\([^)]*katak[^)]*\)", "", v_str).strip()
                        v_str = re.sub(r"\s+", " ", v_str).strip()
                    cleaned[k_clean] = v_str
                if cleaned:
                    return cleaned
        except Exception as ex:
            log.warning(f"Gemini {model_name} xatosi: {ex}")
            last_err = ex
            continue

    return {}

async def handle_scan_keys_api(request):
    """Admin panel uchun javoblar varaqasi rasmidan kalitlarni OCR qilish."""
    try:
        image_bytes = None
        mime_type = "image/jpeg"

        if request.content_type.startswith("multipart/"):
            reader = await request.multipart()
            while True:
                part = await reader.next()
                if part is None:
                    break
                if part.name in ["image", "file", "photo"]:
                    image_bytes = await part.read()
                    mime_type = part.headers.get("Content-Type", "image/jpeg")
                    break
        else:
            data = await request.json()
            raw_b64 = data.get("image", "")
            if "," in raw_b64:
                header, raw_b64 = raw_b64.split(",", 1)
                if "image/png" in header:
                    mime_type = "image/png"
                elif "image/webp" in header:
                    mime_type = "image/webp"
            import base64
            image_bytes = base64.b64decode(raw_b64)

        if not image_bytes:
            return web.json_response({"success": False, "message": "Rasm topilmadi yoki yuklanmadi"}, status=400)

        loop = asyncio.get_running_loop()
        keys_dict = await loop.run_in_executor(None, _sync_extract_keys_gemini, image_bytes, mime_type)

        if not keys_dict:
            return web.json_response({
                "success": False,
                "message": "Rasmdan kalitlarni ajratib bo'lmadi. Iltimos, aniqroq yoki sifatliroq rasm yuklang."
            }, status=200)

        return web.json_response({
            "success": True,
            "data": keys_dict,
            "count": len(keys_dict),
            "message": f"Muvaffaqiyatli! {len(keys_dict)} ta kalit aniqlandi."
        })

    except Exception as e:
        log.error(f"Scan keys API Error: {e}", exc_info=True)
        is_key_err = "GEMINI_API_KEY" in str(e) or "API_KEY" in str(e) or "API key not valid" in str(e)
        return web.json_response({
            "success": False,
            "need_api_key": is_key_err,
            "message": f"OCR tahlilida xatolik: {str(e)}"
        }, status=500)

async def handle_set_gemini_key_api(request):
    """Admin uchun Gemini API kalitini bazaga saqlash."""
    try:
        data = await request.json()
        key = (data.get("key") or "").strip()
        if not key:
            return web.json_response({"success": False, "message": "API kalit kiritilmadi"}, status=400)
        test_db.set_setting("gemini_api_key", key)
        return web.json_response({"success": True, "message": "Gemini API kaliti saqlandi!"})
    except Exception as e:
        log.error(f"Set Gemini key API error: {e}", exc_info=True)
        return web.json_response({"success": False, "message": str(e)}, status=500)


async def handle_user_chat_redirect(request):
    """
    Foydalanuvchi ID si bo'yicha Telegram chatiga xavfsiz yo'naltiruvchi sahifa.
    Telegram Bot API dagi BUTTON_URL_INVALID cheklovini to'liq aylanib o'tadi va
    foydalanuvchining shaxsiy Telegram ilovasida to'g'ridan-to'g'ri profil/chatni ochadi.
    """
    tg_id = request.match_info.get('id', '')
    html_page = f"""<!DOCTYPE html>
<html lang="uz">
<head>
  <meta charset="utf-8">
  <title>Telegram Chatni Ochish</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background: #0f172a;
      color: #f8fafc;
      display: flex;
      align-items: center;
      justify-content: center;
      height: 100vh;
      margin: 0;
      padding: 16px;
      box-sizing: border-box;
    }}
    .box {{
      background: #1e293b;
      border: 1px solid rgba(255,255,255,0.1);
      padding: 28px 24px;
      border-radius: 18px;
      text-align: center;
      max-width: 360px;
      width: 100%;
      box-shadow: 0 20px 40px rgba(0,0,0,0.5);
    }}
    .icon {{ font-size: 48px; margin-bottom: 12px; }}
    h2 {{ margin: 0 0 8px 0; font-size: 19px; font-weight: 800; }}
    p {{ font-size: 13px; color: #94a3b8; margin: 0 0 20px 0; line-height: 1.5; }}
    .btn {{
      display: block;
      background: linear-gradient(135deg, #2563eb, #1d4ed8);
      color: #ffffff;
      text-decoration: none;
      padding: 13px 20px;
      border-radius: 12px;
      font-weight: 700;
      font-size: 14px;
      box-shadow: 0 4px 14px rgba(37,99,235,0.4);
    }}
  </style>
  <script>
    window.location.href = "tg://user?id={tg_id}";
    setTimeout(function() {{
      window.location.href = "tg://openmessage?user_id={tg_id}";
    }}, 400);
  </script>
</head>
<body>
  <div class="box">
    <div class="icon">💬</div>
    <h2>Telegram Chat Ochilmoqda</h2>
    <p>Agar Telegram ilovasi avtomatik ochilmasa, pastdagi tugmani bosing:</p>
    <a href="tg://user?id={tg_id}" class="btn">Telegramda Chatni Ochish ↗️</a>
  </div>
</body>
</html>"""
    return web.Response(text=html_page, content_type='text/html', charset='utf-8')


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, 'test_webapp')

# Agar test_webapp eskidan qolgan bo'lsa, joriy git fayllari xalaqitsiz ishlashi uchun tozalash
try:
    if os.path.exists(WEB_DIR):
        import shutil
        shutil.rmtree(WEB_DIR, ignore_errors=True)
except Exception:
    pass

async def find_web_file(filename: str) -> str:
    filename_clean = filename.lstrip('/')

    # Qaysi papkada ekanligini yo'l prefixi bo'yicha aniqlaymiz
    if filename_clean.startswith('js/'):
        priority_subdirs = ['js']
    elif filename_clean.startswith('css/'):
        priority_subdirs = ['css']
    elif filename_clean.startswith('img/'):
        priority_subdirs = ['img']
    else:
        priority_subdirs = []

    candidates = []

    # 1. Prioritet: to'g'ri papkada to'liq yo'l bilan qidirish
    for base in [BASE_DIR, os.getcwd()]:
        candidates.append(os.path.join(base, filename_clean))

    # 2. Prioritet subdirlar (js/, css/, img/) faqat agar yo'l prefixsiz berilgan bo'lsa
    if priority_subdirs:
        pass  # allaqachon filename_clean ichida bor
    else:
        # Oddiy fayl nomi — barcha subdirlarda qidirish
        for base in [BASE_DIR, os.getcwd()]:
            for sub in ['js', 'css', 'img']:
                candidates.append(os.path.join(base, sub, filename_clean))

    candidates.append(os.path.join(WEB_DIR, filename_clean))

    for c in candidates:
        if os.path.exists(c) and os.path.isfile(c):
            return c

    # Fallback: basename bo'yicha rekursiv qidirish (faqat to'g'ri papkada)
    target = os.path.basename(filename_clean)
    search_roots = [BASE_DIR, os.getcwd()]
    for root_dir in search_roots:
        if not os.path.exists(root_dir):
            continue
        for root, dirs, files in os.walk(root_dir):
            # .git papkasini o'tkazib yuboramiz
            dirs[:] = [d for d in dirs if d not in {'.git', '__pycache__', 'node_modules'}]
            if target in files:
                found = os.path.join(root, target)
                # js/ prefixli so'rov uchun faqat js/ papkasidagi natijani qabul qilamiz
                if filename_clean.startswith('js/') and '/js/' not in found and not found.endswith('/js/' + target):
                    continue
                if filename_clean.startswith('css/') and '/css/' not in found:
                    continue
                log.info(f"🔍 Topildi (recursive search): {found}")
                return found

    return os.path.join(BASE_DIR, filename_clean)


def set_no_cache_headers(resp):
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp

async def handle_index(request):
    fpath = await find_web_file('index.html')
    if os.path.exists(fpath) and os.path.isfile(fpath):
        return set_no_cache_headers(web.FileResponse(fpath))
    try:
        import web_assets_fallback
        data, mime = web_assets_fallback.get_asset_bytes('index.html')
        if data:
            return set_no_cache_headers(web.Response(body=data, content_type=mime or 'text/html', charset='utf-8'))
    except Exception as e:
        log.error(f"index.html yuklashda xatolik: {e}")
    return web.Response(status=404, text="index.html topilmadi")

async def handle_admin(request):
    fpath = await find_web_file('admin.html')
    if os.path.exists(fpath) and os.path.isfile(fpath):
        return set_no_cache_headers(web.FileResponse(fpath))
    try:
        import web_assets_fallback
        data, mime = web_assets_fallback.get_asset_bytes('admin.html')
        if data:
            return set_no_cache_headers(web.Response(body=data, content_type=mime or 'text/html', charset='utf-8'))
    except Exception as e:
        log.error(f"admin.html yuklashda xatolik: {e}")
    return web.Response(status=404, text="admin.html topilmadi")

async def handle_app(request):
    fpath = await find_web_file('app.html')
    if os.path.exists(fpath) and os.path.isfile(fpath):
        resp = web.FileResponse(fpath)
    else:
        # Fallback ishlatilmaydi — eski app.html dan eski kod qaytib chiqmasligi uchun
        log.warning("app.html topilmadi — fallback o'chirilgan, real fayl talab etiladi")
        resp = web.Response(status=404, text="app.html topilmadi")
    return set_no_cache_headers(resp)


async def handle_static_file(request):
    path_name = request.match_info.get('path', '')
    if '..' in path_name:
        return web.Response(status=403, text="Ruxsat berilmagan yo'l")
    fpath = await find_web_file(path_name)
    if os.path.exists(fpath) and os.path.isfile(fpath):
        resp = web.FileResponse(fpath)
        if any(path_name.endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.svg', '.webp', '.ico']):
            resp.headers['Cache-Control'] = 'public, max-age=86400'
        else:
            set_no_cache_headers(resp)
        return resp

    # Fallback to embedded in-memory asset
    try:
        import web_assets_fallback
        if hasattr(web_assets_fallback, 'get_asset_bytes'):
            # Eski app.js va app.html fallbackdan qaytarilmasin —
            # ular endi js/app.js va real app.html dan to'g'ri yuklanadi
            _basename = os.path.basename(path_name)
            _STALE_SKIP = {'app.js', 'app.html'}
            if _basename not in _STALE_SKIP:
                data, mime = web_assets_fallback.get_asset_bytes(path_name)
                if data:
                    resp = web.Response(body=data, content_type=mime or 'application/octet-stream')
                    if any(path_name.endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.svg', '.webp', '.ico']):
                        resp.headers['Cache-Control'] = 'public, max-age=86400'
                    else:
                        set_no_cache_headers(resp)
                    return resp
    except Exception:
        pass


    if 'apple-touch-icon' in path_name or path_name.endswith('.ico'):
        return web.Response(status=204)

    return web.Response(status=404, text="Fayl topilmadi")



# ── ASOSIY MINI APP API ENDPOINTLARI ────────────────────────────────────────

def _fetch_profile_data_sync(tg_id: int):
    user = test_db.get_user(tg_id)
    is_admin = test_db.is_admin(tg_id, ADMIN_ID)
    submissions = test_db.get_user_submissions(tg_id)
    pending_users = 0
    if is_admin:
        try:
            counts = test_db.get_users_count()
            pending_users = counts.get('pending', 0)
        except Exception:
            pass
    return user, is_admin, submissions, pending_users

async def handle_app_profile(request):
    """Foydalanuvchi profili va statistikasi (Asosiy Mini App uchun)."""
    try:
        bot_user = CACHED_BOT_USERNAME

        tg_id = int(request.rel_url.query.get('tg_id', 0))
        if not tg_id or tg_id == 0:
            return web.json_response({
                "success": True,
                "user": {
                    "tg_id": 0,
                    "fullname": "Mehmon",
                    "phone": "—",
                    "status": "not_registered",
                    "is_registered": False,
                    "registered_at": int(time.time()),
                    "tests_count": 0,
                    "avg_score": 0,
                    "max_score": 0,
                    "has_pin": False
                },
                "is_admin": False,
                "pending_users": 0,
                "bot_username": bot_user
            })

        user, is_admin, submissions, pending_users = await asyncio.to_thread(_fetch_profile_data_sync, tg_id)

        # Foydalanuvchi statistikasi
        avg_score = 0.0
        max_score_val = 0
        if submissions:
            scores = [float(s.get('score') or s.get('correct_count') or 0) for s in submissions]
            avg_score = sum(scores) / len(scores) if scores else 0
            max_score_val = max(scores) if scores else 0

        if not user:
            user_data = {
                'tg_id': tg_id,
                'fullname': 'Foydalanuvchi',
                'phone': '—',
                'status': 'not_registered',
                'is_registered': False,
                'registered_at': int(time.time()),
                'tests_count': 0,
                'avg_score': 0,
                'max_score': 0,
                'has_pin': False
            }
        else:
            user_data = dict(user)
            user_data['is_registered'] = True
            user_data['tests_count'] = len(submissions)
            user_data['avg_score'] = round(avg_score, 1)
            user_data['max_score'] = max_score_val
            user_data['has_pin'] = bool(user and user.get('pin_code'))

        return web.json_response({
            "success": True,
            "user": user_data,
            "is_admin": is_admin,
            "pending_users": pending_users,
            "bot_username": bot_user
        })
    except Exception as e:
        log.error(f"App Profile API Error: {e}", exc_info=True)
        return web.json_response({"success": False, "message": str(e)}, status=400)

async def handle_app_set_pin(request):
    """Foydalanuvchi PIN kodini bazada saqlash."""
    try:
        data = await request.json()
        tg_id = int(data.get('tg_id', 0))
        pin = str(data.get('pin', '')).strip()
        if not tg_id or len(pin) != 4:
            return web.json_response({"success": False, "message": "4 xonali PIN kerak"}, status=400)
        await asyncio.to_thread(test_db.set_user_pin, tg_id, pin)
        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"success": False, "message": str(e)}, status=400)

async def handle_app_status(request):
    """Bot va server holatini (online/active) tekshirish."""
    import time
    return web.json_response({
        "success": True,
        "status": "online",
        "bot_active": True,
        "bot_username": CACHED_BOT_USERNAME,
        "server_time": int(time.time()),
        "uptime": int(time.time())
    }, headers={"Access-Control-Allow-Origin": "*"})

async def handle_app_verify_pin(request):
    """Foydalanuvchi PIN kodini bazadan tekshirish."""
    try:
        data = await request.json()
        tg_id = int(data.get('tg_id', 0))
        pin = str(data.get('pin', '')).strip()
        stored = await asyncio.to_thread(test_db.get_user_pin, tg_id)
        if stored and stored == pin:
            return web.json_response({"success": True, "valid": True})
        return web.json_response({"success": True, "valid": False})
    except Exception as e:
        return web.json_response({"success": False, "message": str(e)}, status=400)

async def handle_app_compare_keys(request):
    """Parol orqali test kalitlarini va o'quvchi javoblarini solishtirish."""
    try:
        data = await request.json()
        tg_id = int(data.get('tg_id', 0))
        test_id = int(data.get('test_id', 0))
        code = str(data.get('code', '')).strip()

        if not tg_id or not test_id:
            return web.json_response({"success": False, "message": "Noto'g'ri so'rov"}, status=400)

        # ── RATE LIMITING (1-3 soniya cheklovi) ─────────────────
        rate_key = f"compare_{tg_id or request.remote or 'unknown'}"
        is_allowed, retry_sec = await GLOBAL_RATE_LIMITER.check(rate_key, max_requests=1, window_seconds=2.0)
        if not is_allowed and not test_db.is_admin(tg_id or 0, ADMIN_ID):
            return web.json_response({
                "success": False,
                "retry_after": retry_sec,
                "message": f"Iltimos, {retry_sec} soniya kuting! So'rovingiz navbatda qayta ishlanmoqda..."
            }, status=429)

        async with COMPARE_CONCURRENCY_SEMAPHORE:
            test = await asyncio.to_thread(test_db.get_test_by_id, test_id)
            if not test:
                return web.json_response({"success": False, "message": "Test topilmadi"}, status=404)

            is_published = await asyncio.to_thread(test_db.is_test_results_published, test_id)
            if not is_published:
                return web.json_response({
                    "success": False, 
                    "message": "Natijalar va kalitlar admin tomonidan test yakunlanib, rasmiy e'lon qilingach ochiladi."
                }, status=403)

            expected_code = str(test.get('key_access_code', '')).strip()
            if expected_code and expected_code != code:
                return web.json_response({"success": False, "message": "Parol noto'g'ri!"}, status=403)

            sub = await asyncio.to_thread(test_db.get_user_submission_for_test, test_id, tg_id)
            if not sub:
                return web.json_response({"success": False, "message": "Siz ushbu testni topshirmagansiz"}, status=400)

            correct_answers = test_db.parse_answers_json(test.get('answers_json', '{}'))
            user_answers = test_db.parse_answers_json(sub.get('answers_json', '{}'))

            results = {}
            total_correct_closed = 0
            total_correct_open = 0

            # 1-bosqich: 1-35 yopiq savollar
            for i in range(1, 36):
                k = str(i)
                c_val = correct_answers.get(k)
                u_val = user_answers.get(k)
                is_matched, ratio, status = test_db.check_answer_match(u_val, c_val)
                if status == "correct":
                    total_correct_closed += 1
                results[k] = {
                    "status": status,
                    "user": str(u_val or "").strip(),
                    "ratio": ratio
                }

            # 2-bosqich: 36-45 ochiq savollar (36a–45b, jami 20 ta band)
            for q in range(36, 46):
                for sub_letter in ('a', 'b'):
                    k = f"{q}{sub_letter}"
                    c_val = correct_answers.get(k)
                    u_val = user_answers.get(k)
                    is_matched, ratio, status = test_db.check_answer_match(u_val, c_val)
                    if status == "correct":
                        total_correct_open += 1
                    results[k] = {
                        "status": status,
                        "user": str(u_val or "").strip(),
                        "ratio": ratio
                    }

            total_correct = total_correct_closed + total_correct_open

            # Xavfsizlik: To'g'ri kalitlar klientga yuborilmaydi, faqat tekshiruv natijasi qaytariladi
            return web.json_response({
                "success": True,
                "total_correct": total_correct,
                "total_correct_closed": total_correct_closed,
                "total_correct_open": total_correct_open,
                "total_questions": 55,
                "results": results
            })
    except Exception as e:
        log.error(f"App Compare Keys API Error: {e}", exc_info=True)
        return web.json_response({"success": False, "message": str(e)}, status=400)

def _fetch_active_tests_payload_sync(tg_id: int):
    all_tests = test_db.get_all_tests()
    all_bounds = test_db.get_all_tests_submission_bounds()
    user_subs = test_db.get_user_submissions_map(tg_id) if tg_id else {}
    is_user_admin = bool(tg_id and test_db.is_admin(tg_id, ADMIN_ID))

    result = []
    for t in all_tests:
        td = dict(t)
        td.pop('answers_json', None)  # Javoblarni yashirish

        # Foydalanuvchi allaqachon topshirganmi?
        if tg_id:
            existing = user_subs.get(t['id'])
            td['already_submitted'] = bool(existing)
            is_pub = bool(t.get('results_published', 0))
            if existing:
                is_rej = (str(existing.get('status', '')).strip().lower() == 'rejected')
                td['is_rejected'] = is_rej
                td['submission_status'] = existing.get('status', 'valid')
                td['reject_reason'] = existing.get('reject_reason', '')
                if is_rej:
                    td['user_score'] = 0
                    td['user_correct'] = 0
                    td['user_incorrect'] = 0
                    td['user_total'] = 0
                    td['user_grade'] = "Bekor qilingan"
                elif is_pub:
                    td['user_score'] = existing.get('score')
                    td['user_correct'] = existing.get('correct_count')
                    td['user_incorrect'] = existing.get('incorrect_count')
                    td['user_total'] = existing.get('total_count')
                    td['user_grade'] = existing.get('grade')
                else:
                    td['user_score'] = None
                    td['user_correct'] = None
                    td['user_incorrect'] = None
                    td['user_total'] = None
                    td['user_grade'] = "Kutilmoqda"
                td['submitted_at'] = existing.get('submitted_at')
            else:
                td['is_rejected'] = False
        else:
            td['already_submitted'] = False
            td['is_rejected'] = False

        sched_stat = get_test_schedule_status(t)
        is_upcoming = sched_stat['is_upcoming']
        is_act = sched_stat['is_active']
        is_closed = sched_stat['is_closed']

        td['is_active'] = is_act
        td['is_upcoming'] = is_upcoming
        td['is_planned'] = is_upcoming
        td['is_closed'] = is_closed

        # Haqiqiy topshirilish vaqtlari (har bir testning o'ziga xos vaqt chegaralari)
        bounds = all_bounds.get(t['id'], {})
        if bounds.get('first_submitted_at'):
            td['first_submission_at'] = bounds['first_submitted_at']
        if bounds.get('last_submitted_at'):
            td['last_submission_at'] = bounds['last_submitted_at']
        td['submissions_count'] = bounds.get('count', 0)

        # Agar test boshlanish vaqti kelmagan bo'lsa (is_upcoming):
        if is_upcoming:
            td['code_hidden'] = True
            td['test_code'] = '🔒 Boshlanganda ochiladi'
            if td.get('title'):
                td['title'] = re.sub(r'\s*#[\w\d]+\s*$', '', td['title']).strip()
        else:
            td['code_hidden'] = False

        td['min_submit_info'] = get_test_min_submit_info(t)
        result.append(td)
    now_uzb = datetime.now(UZB_TZ)
    return result, is_user_admin, now_uzb

async def handle_app_active_tests(request):
    """Faol testlar ro'yxati (user uchun topshirilgan-topshirilmaganligini ham qaytaradi)."""
    try:
        tg_id = int(request.rel_url.query.get('tg_id', 0))
        if not tg_id:
            init_data = request.rel_url.query.get('init_data', '') or request.headers.get('X-Telegram-Init-Data', '')
            if init_data:
                import urllib.parse, json
                try:
                    parsed = dict(urllib.parse.parse_qsl(init_data))
                    if 'user' in parsed:
                        u_dict = json.loads(parsed['user'])
                        if u_dict and u_dict.get('id'):
                            tg_id = int(u_dict['id'])
                except Exception:
                    pass

        result, is_user_admin, now_uzb = await asyncio.to_thread(_fetch_active_tests_payload_sync, tg_id)
        return web.json_response({
            "success": True, 
            "tests": result,
            "is_admin": is_user_admin,
            "server_time": int(time.time()),
            "server_uzb": now_uzb.strftime('%Y-%m-%d %H:%M:%S')
        })
    except Exception as e:
        log.error(f"App Active Tests API Error: {e}", exc_info=True)
        return web.json_response({"success": False, "message": str(e)}, status=400)

async def handle_app_my_results(request):
    """Foydalanuvchining o'z test natijalari tarixi."""
    try:
        tg_id = int(request.rel_url.query.get('tg_id', 0))
        if not tg_id:
            return web.json_response({"success": True, "results": []})
        submissions = await asyncio.to_thread(test_db.get_user_submissions, tg_id)
        return web.json_response({"success": True, "results": submissions})
    except Exception as e:
        log.error(f"App My Results API Error: {e}", exc_info=True)
        return web.json_response({"success": False, "message": str(e)}, status=400)

async def handle_app_users(request):
    """Admin uchun barcha foydalanuvchilar ro'yxati va statistika."""
    try:
        tg_id = int(request.rel_url.query.get('tg_id', 0))
        if not tg_id or not test_db.is_admin(tg_id, ADMIN_ID):
            init_data = request.rel_url.query.get('init_data', '') or request.headers.get('X-Telegram-Init-Data', '')
            import urllib.parse, json
            try:
                parsed = dict(urllib.parse.parse_qsl(init_data))
                if 'user' in parsed:
                    u_dict = json.loads(parsed['user'])
                    if u_dict and u_dict.get('id'):
                        tg_id = int(u_dict['id'])
            except Exception:
                pass

        if not test_db.is_admin(tg_id, ADMIN_ID):
            return web.json_response({"success": False, "message": "Ruxsat yo'q"}, status=403)

        users = test_db.get_all_users()
        counts = test_db.get_users_count()
        return web.json_response({
            "success": True,
            "users": users,
            "stats": counts
        })
    except Exception as e:
        log.error(f"App Users API Error: {e}", exc_info=True)
        return web.json_response({"success": False, "message": str(e)}, status=400)

async def handle_app_update_user_status(request):
    """Admin tomonidan foydalanuvchi holatini (approved / pending / blocked / delete) o'zgartirish."""
    try:
        data = await request.json()
        admin_id = int(data.get('admin_id', 0))
        target_uid = int(data.get('target_uid', 0))
        action = str(data.get('status', '')).strip().lower()

        if not admin_id or not test_db.is_admin(admin_id, ADMIN_ID):
            init_data = data.get('init_data', '') or request.headers.get('X-Telegram-Init-Data', '')
            import urllib.parse, json
            try:
                parsed = dict(urllib.parse.parse_qsl(init_data))
                if 'user' in parsed:
                    u_dict = json.loads(parsed['user'])
                    if u_dict and u_dict.get('id'):
                        admin_id = int(u_dict['id'])
            except Exception:
                pass

        if not test_db.is_admin(admin_id, ADMIN_ID):
            return web.json_response({"success": False, "message": "Ruxsat yo'q"}, status=403)

        if not target_uid:
            return web.json_response({"success": False, "message": "Foydalanuvchi ID ko'rsatilmadi"}, status=400)

        if action == 'delete':
            # Foydalanuvchini bazadan butunlay o'chirish
            deleted_user = test_db.delete_user(target_uid)
            return web.json_response({"success": bool(deleted_user), "status": "deleted"})

        if action not in ['approved', 'rejected', 'blocked', 'pending']:
            return web.json_response({"success": False, "message": "Noto'g'ri amal"}, status=400)

        if action == 'approved':
            test_db.approve_user(target_uid)
            try:
                await bot.send_message(
                    chat_id=target_uid,
                    text=(
                        "🎉 <b>Xushxabar! Sizga test tizimidan to'liq foydalanishga ruxsat berildi!</b>\n\n"
                        "Endi botdagi barcha imkoniyatlar va Mini ilovadan to'siqsiz foydalanishingiz mumkin.\n"
                        "Test topshirish uchun bot menyusidan foydalaning!"
                    ),
                    reply_markup=main_menu_kb(target_uid)
                )
            except Exception:
                pass
        elif action in ['rejected', 'pending']:
            test_db.set_user_pending(target_uid)
            try:
                req_kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔔 Admindan ruxsat so'rash", callback_data=f"user_req_access_{target_uid}")]
                ])
                await bot.send_message(
                    chat_id=target_uid,
                    text=(
                        "⏳ <b>Sizning botdan foydalanish huquqingiz admin tomonidan to'xtatildi!</b>\n\n"
                        "Qayta foydalanish uchun quyidagi tugma orqali adminga so'rov yuborishingiz mumkin."
                    ),
                    reply_markup=req_kb
                )
            except Exception:
                pass
        elif action == 'blocked':
            test_db.block_user(target_uid)
            try:
                await bot.send_message(
                    chat_id=target_uid,
                    text="⛔️ <b>Sizning botdan foydalanish huquqingiz admin tomonidan bekor qilindi va bloklandingiz!</b>",
                    reply_markup=ReplyKeyboardRemove()
                )
            except Exception:
                pass

        return web.json_response({"success": True, "status": action})
    except Exception as e:
        log.error(f"App Update User Status Error: {e}", exc_info=True)
        return web.json_response({"success": False, "message": str(e)}, status=400)

async def handle_app_update_profile(request):
    """Foydalanuvchi o'z ism va telefon raqamini tahrirlashi uchun API."""
    try:
        data = await request.json()
        tg_id = int(data.get('tg_id', 0))
        fullname = str(data.get('fullname', '')).strip()
        phone = str(data.get('phone', '')).strip()

        if not tg_id:
            init_data = data.get('init_data', '') or request.headers.get('X-Telegram-Init-Data', '')
            import urllib.parse, json
            try:
                parsed = dict(urllib.parse.parse_qsl(init_data))
                if 'user' in parsed:
                    u_dict = json.loads(parsed['user'])
                    if u_dict and u_dict.get('id'):
                        tg_id = int(u_dict['id'])
            except Exception:
                pass

        if not tg_id:
            return web.json_response({"success": False, "message": "Foydalanuvchi aniqlanmadi"}, status=400)

        if not fullname:
            return web.json_response({"success": False, "message": "Ism kiritilmadi"}, status=400)

        is_valid, err_msg = validate_fullname(fullname)
        if not is_valid:
            clean_err = re.sub(r'<[^>]+>', '', err_msg)
            return web.json_response({"success": False, "message": clean_err}, status=400)

        # Chiroyli bosh harflar bilan formatlash
        fullname = " ".join(w.capitalize() for w in fullname.split())

        ok = test_db.update_user_profile(tg_id, fullname=fullname, phone=phone if phone else None)
        return web.json_response({"success": ok})
    except Exception as e:
        log.error(f"App Update Profile Error: {e}", exc_info=True)
        return web.json_response({"success": False, "message": str(e)}, status=400)

async def handle_app_delete_my_account(request):
    """Foydalanuvchi o'z akkauntini o'chirish uchun API."""
    try:
        data = await request.json()
        tg_id = int(data.get('tg_id', 0))

        if not tg_id:
            init_data = data.get('init_data', '') or request.headers.get('X-Telegram-Init-Data', '')
            import urllib.parse, json
            try:
                parsed = dict(urllib.parse.parse_qsl(init_data))
                if 'user' in parsed:
                    u_dict = json.loads(parsed['user'])
                    if u_dict and u_dict.get('id'):
                        tg_id = int(u_dict['id'])
            except Exception:
                pass

        if not tg_id:
            return web.json_response({"success": False, "message": "Foydalanuvchi aniqlanmadi"}, status=400)

        user_info = test_db.delete_user(tg_id)
        if ADMIN_ID and user_info:
            try:
                fn = user_info.get("fullname", "Noma'lum")
                ph = user_info.get("phone", "—")
                un = f"@{user_info.get('username')}" if user_info.get("username") else "Mavjud emas"
                alert_text = (
                    f"🗑 <b>OGOHLANTIRISH: Foydalanuvchi akkauntini o'chirdi (Mini App)!</b>\n\n"
                    f"👤 <b>Ism:</b> {fn}\n"
                    f"📞 <b>Telefon:</b> <code>{ph}</code>\n"
                    f"🔗 <b>Username:</b> {un}\n"
                    f"🆔 <b>Telegram ID:</b> <code>{tg_id}</code>\n"
                    f"🕒 <b>Vaqt:</b> {format_uzb_time()}\n\n"
                    f"<i>Foydalanuvchi Mini ilovadagi Profil bo'limidan akkauntini o'chirdi.</i>"
                )
                await bot.send_message(chat_id=ADMIN_ID, text=alert_text)
            except Exception as ex:
                log.warning(f"Admin alert yuborishda xatolik: {ex}")
        return web.json_response({"success": bool(user_info)})
    except Exception as e:
        log.error(f"App Delete My Account Error: {e}", exc_info=True)
        return web.json_response({"success": False, "message": str(e)}, status=400)

async def handle_app_restrict_all_users(request):
    """Admin tomonidan barcha oddiy foydalanuvchilarni kutilmoqda (pending) holatiga o'tkazish."""
    try:
        data = await request.json()
        admin_id = int(data.get('admin_id', 0))
        if not test_db.is_admin(admin_id, ADMIN_ID):
            return web.json_response({"success": False, "message": "Ruxsat yo'q"}, status=403)

        count = test_db.restrict_all_users(ADMIN_ID)
        return web.json_response({"success": True, "count": count})
    except Exception as e:
        log.error(f"App Restrict All Users Error: {e}", exc_info=True)
        return web.json_response({"success": False, "message": str(e)}, status=400)

async def handle_app_broadcast(request):
    """Admin tomonidan barcha o'quvchilarga xabar yuborish (Mini App orqali)."""
    try:
        data = await request.json()
        admin_id = int(data.get('admin_id', 0))
        message_text = str(data.get('message', '')).strip()

        if not admin_id or not test_db.is_admin(admin_id, ADMIN_ID):
            init_data = data.get('init_data', '') or request.headers.get('X-Telegram-Init-Data', '')
            import urllib.parse, json
            try:
                parsed = dict(urllib.parse.parse_qsl(init_data))
                if 'user' in parsed:
                    u_dict = json.loads(parsed['user'])
                    if u_dict and u_dict.get('id'):
                        admin_id = int(u_dict['id'])
            except Exception:
                pass

        if not test_db.is_admin(admin_id, ADMIN_ID):
            return web.json_response({"success": False, "message": "Ruxsat yo'q"}, status=403)

        if not message_text:
            return web.json_response({"success": False, "message": "Xabar matni bo'sh bo'lishi mumkin emas!"}, status=400)

        # Barcha faol o'quvchilarga xabar tarqatish
        sent, fail, batch_id = await send_broadcast_to_users(message_text=message_text, sender_tg_id=admin_id)
        return web.json_response({
            "success": True,
            "sent": sent,
            "fail": fail,
            "batch_id": batch_id,
            "message": f"Xabar {sent} nafar o'quvchiga muvaffaqiyatli yetkazildi!"
        })
    except Exception as e:
        log.error(f"App Broadcast Error: {e}", exc_info=True)
        return web.json_response({"success": False, "message": str(e)}, status=400)

async def handle_app_broadcast_delete(request):
    """Admin tomonidan yuborilgan xabarni o'chirish yoki so'nggi xabarlarni tozalash (Mini App API)."""
    try:
        data = await request.json()
        admin_id = int(data.get('admin_id', 0))
        batch_id = str(data.get('batch_id', '')).strip()
        clean_count = int(data.get('clean_count', 0))

        if not admin_id or not test_db.is_admin(admin_id, ADMIN_ID):
            init_data = data.get('init_data', '') or request.headers.get('X-Telegram-Init-Data', '')
            import urllib.parse, json
            try:
                parsed = dict(urllib.parse.parse_qsl(init_data))
                if 'user' in parsed:
                    u_dict = json.loads(parsed['user'])
                    if u_dict and u_dict.get('id'):
                        admin_id = int(u_dict['id'])
            except Exception:
                pass

        if not test_db.is_admin(admin_id, ADMIN_ID):
            return web.json_response({"success": False, "message": "Ruxsat yo'q"}, status=403)

        if batch_id:
            deleted, fail = await delete_broadcast_batch_from_users(batch_id)
            return web.json_response({
                "success": True,
                "deleted": deleted,
                "fail": fail,
                "message": f"Xabar {deleted} nafar o'quvchi chatidan o'chirildi!"
            })
        elif clean_count > 0:
            deleted, users = await delete_recent_bot_messages_from_all_users(count=clean_count)
            return web.json_response({
                "success": True,
                "deleted": deleted,
                "users": users,
                "message": f"{users} nafar o'quvchidan {deleted} ta so'nggi bot xabari tozalandi!"
            })
        else:
            return web.json_response({"success": False, "message": "O'chirish parametri ko'rsatilmadi"}, status=400)
    except Exception as e:
        log.error(f"App Broadcast Delete Error: {e}", exc_info=True)
        return web.json_response({"success": False, "message": str(e)}, status=400)

async def handle_next_test_code_api(request):
    """Keyingi navbatdagi ketma-ket unikal test kodini qaytarish."""
    try:
        next_code = test_db.get_next_test_code()
        return web.json_response({"success": True, "next_code": next_code})
    except Exception as e:
        return web.json_response({"success": False, "next_code": "1", "message": str(e)})

async def handle_app_test_submissions(request):
    """Admin uchun test topshirgan barcha foydalanuvchilar va ularning tafsilotlari."""
    try:
        tg_id = int(request.rel_url.query.get('tg_id', 0))
        if not test_db.is_admin(tg_id, ADMIN_ID):
            return web.json_response({"success": False, "message": "Ruxsat yo'q"}, status=403)
        test_id = int(request.match_info.get('test_id', 0))
        subs = test_db.get_test_submissions_with_users(test_id)
        return web.json_response({"success": True, "submissions": subs})
    except Exception as e:
        log.error(f"App Test Submissions Error: {e}", exc_info=True)
        return web.json_response({"success": False, "message": str(e)}, status=500)

async def handle_rasch_evaluate_api(request):
    try:
        test_id = int(request.match_info.get('test_id', 0))
        tg_id = int(request.rel_url.query.get('tg_id', 0))
        is_adm = test_db.is_admin(tg_id, ADMIN_ID) if tg_id else False
        is_pub = test_db.is_test_results_published(test_id)

        if not is_adm and not is_pub:
            return web.json_response({
                "success": False,
                "message": "Ushbu test natijalari hali e'lon qilinmagan yoki ruxsat yo'q"
            }, status=403)

        res = test_db.evaluate_test_rasch(test_id)
        if not res:
            return web.json_response({"success": False, "message": "Kamida 2 ta talaba topshirgan bo'lishi kerak yoki test topilmadi"}, status=400)
        return web.json_response({"success": True, "data": res})
    except Exception as e:
        return web.json_response({"success": False, "message": str(e)}, status=500)

async def handle_app_trigger_solve(request):
    """Mini ilovadan 'Testni yechish' bosilganda Telegram chatga test kartasini yuborish."""
    try:
        tg_id = 0
        test_code = ""
        test_id_str = ""

        if request.query.get("tg_id"):
            try:
                tg_id = int(request.query.get("tg_id", 0))
            except Exception:
                pass
        test_code = request.query.get("test_code", "").strip()
        test_id_str = request.query.get("test_id", "").strip()

        if not tg_id and request.method == "POST":
            try:
                if request.content_type == "application/json":
                    body = await request.json()
                else:
                    body = await request.post()
                if body.get("tg_id"):
                    tg_id = int(body.get("tg_id"))
                if not test_code:
                    test_code = str(body.get("test_code", "")).strip()
                if not test_id_str:
                    test_id_str = str(body.get("test_id", "")).strip()
            except Exception:
                pass

        if not tg_id:
            return web.json_response({"ok": False, "error": "Foydalanuvchi ID si topilmadi"}, status=400)

        test = None
        if test_code:
            code_clean = test_code.upper().replace("#", "")
            test = test_db.get_test_by_code(code_clean)
        if not test and test_id_str and test_id_str.isdigit():
            test = test_db.get_test_by_id(int(test_id_str))

        if not test:
            return web.json_response({"ok": False, "error": "Test topilmadi"}, status=404)

        sched_stat = get_test_schedule_status(test)
        if sched_stat["is_upcoming"] and not test_db.is_admin(tg_id, ADMIN_ID):
            sdate = test.get('scheduled_date') or 'Bugun'
            sstart = test.get('scheduled_start') or ''
            return web.json_response({
                "ok": False, 
                "error": f"Test hali boshlanmagan! Boshlanish vaqti: {sdate} {sstart} (UZB)"
            }, status=400)

        # Telegram chatga testni taqdim etish (asinxron)
        asyncio.create_task(send_test_card_to_user_chat(tg_id, test))
        return web.json_response({"ok": True, "message": "Test chatga yuborildi"})
    except Exception as e:
        log.error(f"handle_app_trigger_solve xatolik: {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=500)

# ──────────────────────────────────────────────────────────
# MACBOOK DASHBOARD HANDLERS
# ──────────────────────────────────────────────────────────

async def handle_dashboard(request):
    fpath = await find_web_file('dashboard.html')
    if os.path.exists(fpath) and os.path.isfile(fpath):
        return set_no_cache_headers(web.FileResponse(fpath))
    try:
        import web_assets_fallback
        data, mime = web_assets_fallback.get_asset_bytes('dashboard.html')
        if data:
            return set_no_cache_headers(web.Response(body=data, content_type=mime or 'text/html', charset='utf-8'))
    except Exception as e:
        log.error(f"dashboard.html yuklashda xatolik: {e}")
    return web.Response(status=404, text="dashboard.html topilmadi")

async def handle_dashboard_overview(request):
    global _dashboard_overview_cache
    now = time.time()
    refresh = request.rel_url.query.get('refresh') == 'true' or request.rel_url.query.get('live') == 'true'
    if not refresh and _dashboard_overview_cache["data"] and (now - _dashboard_overview_cache["ts"] < 3.0):
        return web.json_response(_dashboard_overview_cache["data"])

    try:
        summary, recent_subs, logs, tests, recent_users = await asyncio.gather(
            asyncio.to_thread(test_db.get_dashboard_summary),
            asyncio.to_thread(test_db.get_all_submissions_for_admin, 15),
            asyncio.to_thread(test_db.get_activity_logs, 80),
            asyncio.to_thread(test_db.get_tests_with_stats),
            asyncio.to_thread(test_db.get_recent_users, 15)
        )
        for s in recent_subs:
            s['submitted_at_fmt'] = format_uzb_time(s.get('submitted_at'), fmt="%d.%m.%Y %H:%M:%S")
        for u in recent_users:
            u['registered_at_fmt'] = format_uzb_time(u.get('registered_at'), fmt="%d.%m.%Y %H:%M:%S")
        for l in logs:
            l['time_fmt'] = format_uzb_time(l.get('time'), fmt="%d.%m.%Y %H:%M:%S")
            
        for t in tests:
            if t.get('created_at'):
                t['created_at_fmt'] = format_uzb_time(t.get('created_at'), fmt="%d.%m.%Y %H:%M")
        resp_data = {
            "success": True,
            "summary": summary,
            "recent_submissions": recent_subs,
            "recent_users": recent_users,
            "activity_logs": logs,
            "tests": tests,
            "server_time": format_uzb_time(fmt="%d.%m.%Y %H:%M:%S")
        }
        _dashboard_overview_cache["data"] = resp_data
        _dashboard_overview_cache["ts"] = now
        return web.json_response(resp_data)
    except Exception as e:
        log.error(f"Dashboard overview error: {e}", exc_info=True)
        if _dashboard_overview_cache["data"]:
            return web.json_response(_dashboard_overview_cache["data"])
        return web.json_response({"success": False, "error": str(e)}, status=500)

async def handle_dashboard_submissions(request):
    try:
        limit = int(request.rel_url.query.get('limit', 1000))
        subs = await asyncio.to_thread(test_db.get_all_submissions_for_admin, limit)
        for s in subs:
            s['submitted_at_fmt'] = format_uzb_time(s.get('submitted_at'), fmt="%d.%m.%Y %H:%M:%S")
        return web.json_response({
            "success": True,
            "total": len(subs),
            "submissions": subs
        })
    except Exception as e:
        log.error(f"Dashboard submissions error: {e}", exc_info=True)
        return web.json_response({"success": False, "error": str(e)}, status=500)

async def handle_dashboard_submission_detail(request):
    try:
        sub_id = int(request.match_info.get('id', 0))
        detail = await asyncio.to_thread(test_db.get_submission_details_for_admin, sub_id)
        if not detail:
            return web.json_response({"success": False, "error": "Natija topilmadi"}, status=404)
        
        detail['submitted_at_fmt'] = format_uzb_time(detail.get('submitted_at'), fmt="%d.%m.%Y %H:%M:%S")
        try:
            if isinstance(detail.get('details_json'), str):
                detail['details'] = json.loads(detail['details_json'])
            else:
                detail['details'] = detail.get('details_json') or {}
        except Exception:
            detail['details'] = {}
            
        try:
            if isinstance(detail.get('answers_json'), str):
                detail['answers'] = json.loads(detail['answers_json'])
            else:
                detail['answers'] = detail.get('answers_json') or {}
        except Exception:
            detail['answers'] = {}

        return web.json_response({
            "success": True,
            "submission": detail
        })
    except Exception as e:
        log.error(f"Dashboard submission detail error: {e}", exc_info=True)
        return web.json_response({"success": False, "error": str(e)}, status=500)

async def handle_dashboard_user_submissions(request):
    try:
        user_tg_id = int(request.match_info.get('tg_id', 0))
        subs = await asyncio.to_thread(test_db.get_user_results, user_tg_id)
        for s in subs:
            s['submitted_at_fmt'] = format_uzb_time(s.get('submitted_at'), fmt="%d.%m.%Y %H:%M:%S")
        return web.json_response({
            "success": True,
            "submissions": subs
        })
    except Exception as e:
        log.error(f"Dashboard user submissions error: {e}", exc_info=True)
        return web.json_response({"success": False, "error": str(e)}, status=500)

async def handle_dashboard_users(request):
    global _dashboard_users_cache
    now = time.time()
    if _dashboard_users_cache["data"] and (now - _dashboard_users_cache["ts"] < 15.0):
        return web.json_response(_dashboard_users_cache["data"])
    try:
        users, counts = await asyncio.gather(
            asyncio.to_thread(test_db.get_all_users),
            asyncio.to_thread(test_db.get_users_count)
        )
        for u in users:
            u['registered_at_fmt'] = format_uzb_time(u.get('registered_at'), fmt="%d.%m.%Y %H:%M") if u.get('registered_at') else "—"
            u['last_test_at_fmt'] = format_uzb_time(u.get('last_test_at'), fmt="%d.%m.%Y %H:%M") if u.get('last_test_at') else "—"
        resp_data = {
            "success": True,
            "users": users,
            "stats": counts
        }
        _dashboard_users_cache["data"] = resp_data
        _dashboard_users_cache["ts"] = now
        return web.json_response(resp_data)
    except Exception as e:
        log.error(f"Dashboard users error: {e}", exc_info=True)
        if _dashboard_users_cache["data"]:
            return web.json_response(_dashboard_users_cache["data"])
        return web.json_response({"success": False, "error": str(e)}, status=500)

async def handle_dashboard_tests(request):
    global _dashboard_tests_cache
    now = time.time()
    if _dashboard_tests_cache["data"] and (now - _dashboard_tests_cache["ts"] < 15.0):
        return web.json_response(_dashboard_tests_cache["data"])
    try:
        tests = await asyncio.to_thread(test_db.get_tests_with_stats)
        for t in tests:
            if t.get('created_at'):
                t['created_at_fmt'] = format_uzb_time(t.get('created_at'), fmt="%d.%m.%Y %H:%M")
        resp_data = {
            "success": True,
            "tests": tests
        }
        _dashboard_tests_cache["data"] = resp_data
        _dashboard_tests_cache["ts"] = now
        return web.json_response(resp_data)
    except Exception as e:
        log.error(f"Dashboard tests error: {e}", exc_info=True)
        if _dashboard_tests_cache["data"]:
            return web.json_response(_dashboard_tests_cache["data"])
        return web.json_response({"success": False, "error": str(e)}, status=500)

async def handle_dashboard_logs(request):
    try:
        limit = int(request.rel_url.query.get('limit', 100))
        logs = await asyncio.to_thread(test_db.get_activity_logs, limit)
        for l in logs:
            l['time_fmt'] = format_uzb_time(l.get('time'), fmt="%d.%m.%Y %H:%M:%S")
        return web.json_response({
            "success": True,
            "logs": logs
        })
    except Exception as e:
        log.error(f"Dashboard logs error: {e}", exc_info=True)
        return web.json_response({"success": False, "error": str(e)}, status=500)

async def handle_dashboard_user_action(request):
    try:
        data = await request.json()
        target_uid = int(data.get('user_id', 0))
        action = str(data.get('action', '')).strip().lower()
        if not target_uid:
            return web.json_response({"success": False, "error": "Foydalanuvchi ID ko'rsatilmadi"}, status=400)
            
        if action == 'approve':
            test_db.approve_user(target_uid)
            msg = "Foydalanuvchi faollashtirildi"
        elif action == 'block':
            test_db.block_user(target_uid)
            msg = "Foydalanuvchi bloklandi"
        elif action == 'pending':
            test_db.set_user_pending(target_uid)
            msg = "Foydalanuvchi kutilmoqda holatiga o'tkazildi"
        elif action == 'delete':
            test_db.delete_user(target_uid)
            msg = "Foydalanuvchi bazadan o'chirildi"
        else:
            return web.json_response({"success": False, "error": f"Noma'lum amal: {action}"}, status=400)
            
        return web.json_response({"success": True, "message": msg})
    except Exception as e:
        log.error(f"Dashboard user action error: {e}", exc_info=True)
        return web.json_response({"success": False, "error": str(e)}, status=500)

async def handle_dashboard_late_action(request):
    try:
        data = await request.json()
        sub_id = int(data.get('submission_id', 0))
        action = str(data.get('action', '')).strip().lower()
        if not sub_id:
            return web.json_response({"success": False, "error": "Submission ID ko'rsatilmadi"}, status=400)
            
        if action == 'accept':
            test_db.set_submission_late_status(sub_id, 0)
            msg = "Kech topshirilgan natija testga qabul qilindi!"
        elif action == 'reject':
            test_db.set_submission_late_status(sub_id, 1)
            msg = "Natija hisobga olinmaydigan (kechikkan) deb belgilandi."
        else:
            return web.json_response({"success": False, "error": "Noma'lum amal"}, status=400)
            
        return web.json_response({"success": True, "message": msg})
    except Exception as e:
        log.error(f"Dashboard late action error: {e}", exc_info=True)
        return web.json_response({"success": False, "error": str(e)}, status=500)

async def handle_dashboard_cancel_submission(request):
    """
    O'quvchining test javobini bekor qilish amallari:
    1. action == 'reject': Javobni qabul qilmaslik (rad etish, qayta topshira olmaydi)
    2. action == 'allow_retake': Qayta topshirish (avvalgi javob o'chiriladi, qayta topshira oladi)
    Ikkala holatda ham o'quvchining Telegram chatiga xabar boradi.
    """
    try:
        data = await request.json()
        sub_id = int(data.get('submission_id', 0))
        action = str(data.get('action', '')).strip().lower()
        reason = str(data.get('reason', '')).strip()

        if not sub_id:
            return web.json_response({"success": False, "error": "Submission ID ko'rsatilmadi"}, status=400)
        if action not in ['reject', 'allow_retake']:
            return web.json_response({"success": False, "error": "Noto'g'ri amal turi (reject yoki allow_retake)"}, status=400)

        sub = test_db.get_submission_by_id(sub_id)
        if not sub:
            return web.json_response({"success": False, "error": "Topshirilgan natija topilmadi"}, status=404)

        user_tg_id = sub.get('user_tg_id')
        fullname = sub.get('fullname', 'Foydalanuvchi')
        test_id = sub.get('test_id')
        test_code = sub.get('test_code', '')
        test_obj = test_db.get_test_by_id(test_id) if test_id else None
        test_title = test_obj.get('title', f"Test #{test_code}") if test_obj else f"Test #{test_code}"

        if action == 'reject':
            # 1. Javobni qabul qilmaslik (rad etish)
            success = test_db.reject_submission(sub_id, reason=reason)
            if not success:
                return web.json_response({"success": False, "error": "Natijani rad etishda xatolik yuz berdi"}, status=500)

            user_msg = (
                f"⛔️ <b>DIQQAT: TEST JAVOBLARINGIZ QABUL QILINMADI!</b>\n\n"
                f"Hurmatli <b>{fullname}</b>!\n\n"
                f"Sizning <b>«{test_title}»</b> (Kod: <code>#{test_code}</code>) testi bo'yicha topshirgan javoblaringiz ma'muriyat tomonidan bekor qilindi va <b>qabul qilinmadi</b>.\n\n"
                f"ℹ️ <i>Izoh: Ushbu test natijangiz hisobga olinmaydi. Qayta topshirishga ruxsat berilmagan.</i>\n\n"
                f"Savollaringiz bo'lsa administrator bilan bog'lanishingiz mumkin."
            )
            admin_msg = f"{fullname}ning javoblari qabul qilinmadi (rad etildi)."

        elif action == 'allow_retake':
            # 2. Qayta topshirish (avvalgi javob o'chiriladi, yangidan topshira oladi)
            success = test_db.delete_submission(sub_id)
            if not success:
                return web.json_response({"success": False, "error": "Natijani o'chirishda xatolik yuz berdi"}, status=500)

            user_msg = (
                f"🔄 <b>DIQQAT: TESTNI QAYTA TOPSHIRISHINGIZ MUMKIN!</b>\n\n"
                f"Hurmatli <b>{fullname}</b>!\n\n"
                f"Sizning <b>«{test_title}»</b> (Kod: <code>#{test_code}</code>) testi bo'yicha topshirgan avvalgi javoblaringiz ma'muriyat tomonidan bekor qilindi va sizga testni <b>QAYTA TOPSHIRISHGA RUXSAT BERILDI!</b> ✅\n\n"
                f"Endi botimiz (@bm_rashtest_bot) va Mini ilova orqali testga qaytadan kirib, barcha savollarni boshidan ishlab topshirishingiz mumkin.\n\n"
                f"Omad tilaymiz! 🚀"
            )
            admin_msg = f"{fullname}ning avvalgi javoblari o'chirildi va unga qayta topshirishga ruxsat berildi."

        # Foydalanuvchining shaxsiy Telegram chatiga xabar yuborish
        sent_to_user = False
        try:
            if user_tg_id and int(user_tg_id) > 0:
                await bot.send_message(chat_id=int(user_tg_id), text=user_msg)
                sent_to_user = True
        except Exception as e:
            log.warning(f"Foydalanuvchiga ({user_tg_id}) xabar jo'natishda xatolik: {e}")

        # Audit jurnaliga yozish
        try:
            test_db.log_activity(
                action=f"submission_{action}",
                details=f"Test #{test_code} (Sub ID: {sub_id}) - {fullname} (ID: {user_tg_id}) amali: {action}. Userga xabar: {'yetkazildi' if sent_to_user else 'yetkazilmadi'}",
                actor="admin"
            )
        except Exception:
            pass

        return web.json_response({
            "success": True,
            "message": admin_msg,
            "sent_to_user": sent_to_user,
            "action": action
        })
    except Exception as e:
        log.error(f"Dashboard cancel submission error: {e}", exc_info=True)
        return web.json_response({"success": False, "error": str(e)}, status=500)

async def handle_dashboard_test_toggle(request):
    try:
        data = await request.json()
        test_id = int(data.get('test_id', 0))
        new_status = test_db.toggle_test_status(test_id)
        return web.json_response({
            "success": True,
            "is_active": new_status,
            "message": "Test faollashtirildi" if new_status == 1 else "Test to'xtatildi"
        })
    except Exception as e:
        log.error(f"Dashboard test toggle error: {e}", exc_info=True)
        return web.json_response({"success": False, "error": str(e)}, status=500)

async def handle_dashboard_test_publish(request):
    try:
        data = await request.json()
        test_id = int(data.get('test_id', 0))
        t = test_db.get_test_by_id(test_id)
        if not t:
            return web.json_response({"success": False, "error": "Test topilmadi"}, status=404)
        cur_pub = t.get('results_published', 0)
        new_pub = 0 if cur_pub == 1 else 1
        test_db.set_test_results_published(test_id, new_pub)
        if new_pub == 1:
            pub_id = int(data.get("admin_id") or data.get("tg_id") or ADMIN_ID)
            asyncio.create_task(notify_admins_test_results_published(test_id, pub_id, eval_type="rasch"))
        return web.json_response({
            "success": True,
            "results_published": new_pub,
            "message": "Natijalar e'lon qilindi!" if new_pub == 1 else "Natijalar e'loni yashirildi"
        })
    except Exception as e:
        log.error(f"Dashboard test publish error: {e}", exc_info=True)
        return web.json_response({"success": False, "error": str(e)}, status=500)

async def handle_dashboard_query(request):
    try:
        data = await request.json()
        query = str(data.get('query', '')).strip()
        res = test_db.execute_admin_safe_query(query, limit=100)
        return web.json_response(res)
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)


async def handle_dashboard_warn_user(request):
    """Bitta foydalanuvchiga Telegram orqali rasmiy ogohlantirish xabari yuborish."""
    try:
        data = await request.json()
        tg_id = int(data.get('tg_id', 0))
        tests_count = int(data.get('tests_count', 0))
        if not tg_id:
            return web.json_response({"success": False, "error": "tg_id ko'rsatilmadi"}, status=400)

        user = await asyncio.to_thread(test_db.get_user, tg_id)
        fname = user.get("fullname") if user else "Foydalanuvchi"

        if tests_count == 0:
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
                f"Siz shu kunga qadar faqat <b>{tests_count} ta test</b> ishladingiz va sizga taqdim etilgan bepul imkoniyatlardan "
                "to'liq foydalanmadingiz.\n\n"
                "📌 <b>Muhim eslatma:</b> Bugungi bo'lib o'tadigan testda ham qatnashmasangiz, faoliyatsizligingiz sababli sizni "
                "<b>botdan va tizimdan chiqarib yuborishga</b> majbur bo'lamiz.\n\n"
                "<i>O'z o'rningizni saqlab qolish va natijalaringizni oshirish uchun bugungi testda albatta ishtirok eting!</i>"
            )

        try:
            await bot.send_message(chat_id=tg_id, text=text, parse_mode=ParseMode.HTML)
            return web.json_response({
                "success": True,
                "message": f"✅ {fname} ({tg_id}) ga rasmiy ogohlantirish xabari yetkazildi!"
            })
        except Exception as e:
            err_text = str(e).lower()
            if "blocked" in err_text or "forbidden" in err_text or "deactivated" in err_text:
                await asyncio.to_thread(test_db.delete_user, tg_id)
                return web.json_response({
                    "success": False,
                    "blocked": True,
                    "message": f"🚫 Foydalanuvchi botni bloklagani aniqlandi va bazadan o'chirildi!"
                })
            return web.json_response({
                "success": False,
                "error": f"Telegram xatolik: {e}"
            }, status=500)
    except Exception as e:
        log.error(f"Dashboard warn user error: {e}", exc_info=True)
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def handle_dashboard_warn_users_batch(request):
    """Filtrdagi barcha foydalanuvchilarga rasmiy ogohlantirish xabari yuborish."""
    try:
        data = await request.json()
        users_list = data.get('users', [])
        if not users_list:
            return web.json_response({"success": False, "error": "Foydalanuvchilar ro'yxati bo'sh"}, status=400)

        sent_count = 0
        failed_count = 0
        deleted_count = 0

        for item in users_list:
            tg_id = int(item.get('tg_id', 0))
            if not tg_id:
                continue
            tests_count = int(item.get('tests_count', 0))
            fname = item.get('fullname') or "Foydalanuvchi"

            if tests_count == 0:
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
                    f"Siz shu kunga qadar faqat <b>{tests_count} ta test</b> ishladingiz va sizga taqdim etilgan bepul imkoniyatlardan "
                    "to'liq foydalanmadingiz.\n\n"
                    "📌 <b>Muhim eslatma:</b> Bugungi bo'lib o'tadigan testda ham qatnashmasangiz, faoliyatsizligingiz sababli sizni "
                    "<b>botdan va tizimdan chiqarib yuborishga</b> majbur bo'lamiz.\n\n"
                    "<i>O'z o'rningizni saqlab qolish va natijalaringizni oshirish uchun bugungi testda albatta ishtirok eting!</i>"
                )

            try:
                await bot.send_message(chat_id=tg_id, text=text, parse_mode=ParseMode.HTML)
                sent_count += 1
                await asyncio.sleep(0.04)
            except Exception as e:
                err_text = str(e).lower()
                if "blocked" in err_text or "forbidden" in err_text or "deactivated" in err_text:
                    await asyncio.to_thread(test_db.delete_user, tg_id)
                    deleted_count += 1
                else:
                    failed_count += 1

        del_msg = f" ({deleted_count} ta bloklagan akkaunt o'chirildi)" if deleted_count else ""
        return web.json_response({
            "success": True,
            "sent_count": sent_count,
            "failed_count": failed_count,
            "deleted_count": deleted_count,
            "message": f"✅ {sent_count} nafar foydalanuvchiga ogohlantirish muvaffaqiyatli yetkazildi!{del_msg}"
        })
    except Exception as e:
        log.error(f"Dashboard warn users batch error: {e}", exc_info=True)
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def handle_dashboard_broadcast(request):
    """Dashboard orqali barcha yoki maqsadli o'quvchilarga Telegram xabarnoma yuborish."""
    try:
        data = await request.json()
        target = str(data.get('target', 'all')).strip().lower()
        test_id = int(data.get('test_id', 0) or 0)
        message_text = str(data.get('message', '')).strip()
        photo_url = str(data.get('photo_url', '')).strip()
        btn_text = str(data.get('button_text', '')).strip()
        btn_url = str(data.get('button_url', '')).strip()

        if not message_text and not photo_url:
            return web.json_response({"success": False, "message": "Xabar matni yoki rasm havolasi kiritilishi shart!"}, status=400)

        # 1. Auditoriyani aniqlash
        all_users = await asyncio.to_thread(test_db.get_all_users)
        target_users = []
        if target == 'all':
            target_users = all_users
        elif target == 'active':
            target_users = [u for u in all_users if int(u.get('tests_count', 0) or 0) > 0]
        elif target == 'inactive':
            target_users = [u for u in all_users if int(u.get('tests_count', 0) or 0) == 0]
        elif target == 'test' and test_id > 0:
            subs = await asyncio.to_thread(test_db.get_test_submissions_with_users, test_id)
            test_tg_ids = {s.get('user_tg_id') for s in subs if s.get('user_tg_id')}
            target_users = [u for u in all_users if u.get('tg_id') in test_tg_ids]
        else:
            target_users = all_users

        if not target_users:
            return web.json_response({"success": False, "message": "Tanlangan filtr bo'yicha hech qanday o'quvchi topilmadi!"}, status=400)

        # 2. Batch yaratish
        batch_id = f"BC-{datetime.now(UZB_TZ).strftime('%y%m%d%H%M%S')}-{uuid.uuid4().hex[:4].upper()}"
        summary_text = (f"[{target.upper()}] " + (message_text or "Rasm xabari"))[:400]
        await asyncio.to_thread(
            test_db.create_broadcast_batch,
            batch_id=batch_id,
            sender_tg_id=ADMIN_ID,
            message_text=summary_text,
            photo_id=photo_url[:200]
        )

        # 3. Inline klaviatura (agar tugma kiritilgan bo'lsa)
        reply_markup = None
        if btn_text and btn_url:
            reply_markup = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=btn_text, url=btn_url)
            ]])

        sent_count = 0
        fail_count = 0
        deleted_count = 0

        for u in target_users:
            uid = u.get('tg_id')
            if not uid:
                continue
            fname = u.get('fullname') or "Foydalanuvchi"
            # {fullname} shablonini o'quvchi ismi bilan almashtirish
            user_msg = message_text.replace("{fullname}", fname).replace("{ism}", fname)

            try:
                sent_msg = None
                if photo_url:
                    sent_msg = await bot.send_photo(
                        chat_id=uid,
                        photo=photo_url,
                        caption=user_msg,
                        parse_mode=ParseMode.HTML,
                        reply_markup=reply_markup
                    )
                else:
                    sent_msg = await bot.send_message(
                        chat_id=uid,
                        text=user_msg,
                        parse_mode=ParseMode.HTML,
                        reply_markup=reply_markup,
                        disable_web_page_preview=False
                    )

                if sent_msg and hasattr(sent_msg, 'message_id'):
                    await asyncio.to_thread(test_db.record_broadcast_message, batch_id, uid, sent_msg.message_id)
                sent_count += 1
                await asyncio.sleep(0.04)
            except Exception as e:
                err_text = str(e).lower()
                if "blocked" in err_text or "forbidden" in err_text or "deactivated" in err_text:
                    await asyncio.to_thread(test_db.delete_user, uid)
                    deleted_count += 1
                else:
                    fail_count += 1

        await asyncio.to_thread(test_db.update_broadcast_sent_count, batch_id, sent_count)

        del_note = f" ({deleted_count} ta botni bloklagan akkaunt tozalandi)" if deleted_count else ""
        return web.json_response({
            "success": True,
            "batch_id": batch_id,
            "sent_count": sent_count,
            "fail_count": fail_count,
            "deleted_count": deleted_count,
            "target": target,
            "message": f"Xabarnoma {sent_count} nafar o'quvchiga muvaffaqiyatli yetkazildi!{del_note}"
        })
    except Exception as e:
        log.error(f"Dashboard broadcast error: {e}", exc_info=True)
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def handle_dashboard_broadcast_history(request):
    """Dashboard uchun so'nggi xabarnomalar tarixi."""
    try:
        limit = int(request.rel_url.query.get('limit', 25))
        history = await asyncio.to_thread(test_db.get_recent_broadcasts, limit)
        for h in history:
            h['created_at_fmt'] = format_uzb_time(h.get('created_at'), fmt="%d.%m.%Y %H:%M")
        return web.json_response({"success": True, "history": history})
    except Exception as e:
        log.error(f"Dashboard broadcast history error: {e}", exc_info=True)
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def handle_dashboard_broadcast_delete(request):
    """Yuborilgan xabarnomani barcha o'quvchilar chatidan o'chirish."""
    try:
        data = await request.json()
        batch_id = str(data.get('batch_id', '')).strip()
        if not batch_id:
            return web.json_response({"success": False, "error": "batch_id ko'rsatilmadi"}, status=400)

        deleted, fail = await delete_broadcast_batch_from_users(batch_id)
        await asyncio.to_thread(test_db.mark_broadcast_deleted, batch_id)
        return web.json_response({
            "success": True,
            "deleted": deleted,
            "fail": fail,
            "message": f"Xabarnoma {deleted} nafar o'quvchi chatidan o'chirildi!"
        })
    except Exception as e:
        log.error(f"Dashboard broadcast delete error: {e}", exc_info=True)
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def handle_notify_inactive_users(request):
    try:
        data = {}
        try:
            data = await request.json()
        except Exception:
            pass
        admin_id = int(data.get('admin_id', 0) or request.rel_url.query.get('admin_id', 0))
        secret_key = str(data.get('secret_key', '') or request.rel_url.query.get('secret_key', '')).strip()
        target_count = str(data.get('target_count', '') or request.rel_url.query.get('target_count', 'both')).strip()
        time_filter = str(data.get('time_filter', '') or request.rel_url.query.get('time_filter', 'min_2d')).strip()

        is_auth = (admin_id and test_db.is_admin(admin_id, ADMIN_ID)) or (secret_key == "rash_admin_secret_2026") or (admin_id == ADMIN_ID)
        if not is_auth:
            return web.json_response({"success": False, "message": "Ruxsat yo'q"}, status=403)

        result = await send_inactive_warning_messages(
            initiator_id=admin_id or ADMIN_ID,
            target_count=target_count,
            time_filter=time_filter
        )
        return web.json_response(result)
    except Exception as e:
        log.error(f"Notify inactive users error: {e}", exc_info=True)
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def handle_clean_blocked_users(request):
    try:
        data = {}
        try:
            data = await request.json()
        except Exception:
            pass
        admin_id = int(data.get('admin_id', 0) or request.rel_url.query.get('admin_id', 0))
        secret_key = str(data.get('secret_key', '') or request.rel_url.query.get('secret_key', '')).strip()

        is_auth = (admin_id and test_db.is_admin(admin_id, ADMIN_ID)) or (secret_key == "rash_admin_secret_2026") or (admin_id == ADMIN_ID)
        if not is_auth:
            return web.json_response({"success": False, "message": "Ruxsat yo'q"}, status=403)

        result = await clean_blocked_users_from_db(initiator_id=admin_id or ADMIN_ID)
        return web.json_response(result)
    except Exception as e:
        log.error(f"Clean blocked users error: {e}", exc_info=True)
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def create_web_app():
    app = web.Application()
    app.router.add_post('/api/admin/clean-blocked-users', handle_clean_blocked_users)
    app.router.add_get('/api/admin/clean-blocked-users', handle_clean_blocked_users)
    app.router.add_post('/api/admin/notify-inactive-users', handle_notify_inactive_users)
    app.router.add_get('/api/admin/notify-inactive-users', handle_notify_inactive_users)
    app.router.add_get('/', handle_index)
    app.router.add_get('/index.html', handle_index)
    app.router.add_get('/admin.html', handle_admin)
    app.router.add_get('/app.html', handle_app)
    app.router.add_get('/user-chat/{id}', handle_user_chat_redirect)
    
    # MacBook Desktop Dashboard
    app.router.add_get('/dashboard', handle_dashboard)
    app.router.add_get('/dashboard.html', handle_dashboard)
    app.router.add_get('/api/dashboard/overview', handle_dashboard_overview)
    app.router.add_get('/api/dashboard/submissions', handle_dashboard_submissions)
    app.router.add_get('/api/dashboard/submission/{id}', handle_dashboard_submission_detail)
    app.router.add_get('/api/dashboard/users', handle_dashboard_users)
    app.router.add_get('/api/dashboard/user-submissions/{tg_id}', handle_dashboard_user_submissions)
    app.router.add_get('/api/dashboard/tests', handle_dashboard_tests)
    app.router.add_get('/api/dashboard/logs', handle_dashboard_logs)
    app.router.add_post('/api/dashboard/user-action', handle_dashboard_user_action)
    app.router.add_post('/api/dashboard/late-action', handle_dashboard_late_action)
    app.router.add_post('/api/dashboard/submissions/cancel', handle_dashboard_cancel_submission)
    app.router.add_post('/api/dashboard/test-toggle', handle_dashboard_test_toggle)
    app.router.add_post('/api/dashboard/test-publish', handle_dashboard_test_publish)
    app.router.add_post('/api/dashboard/query', handle_dashboard_query)
    app.router.add_post('/api/dashboard/warn-user', handle_dashboard_warn_user)
    app.router.add_post('/api/dashboard/warn-users-batch', handle_dashboard_warn_users_batch)
    app.router.add_post('/api/dashboard/broadcast', handle_dashboard_broadcast)
    app.router.add_get('/api/dashboard/broadcast/history', handle_dashboard_broadcast_history)
    app.router.add_post('/api/dashboard/broadcast/delete', handle_dashboard_broadcast_delete)

    app.router.add_get('/api/rasch/{test_id}', handle_rasch_evaluate_api)
    app.router.add_post('/api/submit-test', handle_submit_test_api)
    app.router.add_post('/api/create-test', handle_create_test_api)
    app.router.add_get('/api/admin/get-test-keys', handle_admin_get_test_keys)
    app.router.add_post('/api/admin/update-test-keys', handle_admin_update_test_keys)
    app.router.add_post('/api/scan-keys', handle_scan_keys_api)
    app.router.add_post('/api/set-gemini-key', handle_set_gemini_key_api)
    # Asosiy Mini App API
    app.router.add_get('/api/app/profile', handle_app_profile)
    app.router.add_post('/api/app/update-profile', handle_app_update_profile)
    app.router.add_post('/api/app/delete-my-account', handle_app_delete_my_account)
    app.router.add_get('/api/app/active-tests', handle_app_active_tests)
    app.router.add_get('/api/app/test-submissions/{test_id}', handle_app_test_submissions)
    app.router.add_get('/api/app/my-results', handle_app_my_results)
    app.router.add_get('/api/app/trigger-solve', handle_app_trigger_solve)
    app.router.add_post('/api/app/trigger-solve', handle_app_trigger_solve)
    app.router.add_get('/api/app/users', handle_app_users)
    app.router.add_post('/api/app/update-user-status', handle_app_update_user_status)
    app.router.add_post('/api/app/restrict-all-users', handle_app_restrict_all_users)
    app.router.add_post('/api/app/broadcast', handle_app_broadcast)
    app.router.add_post('/api/app/broadcast/delete', handle_app_broadcast_delete)
    app.router.add_get('/api/next-test-code', handle_next_test_code_api)
    app.router.add_get('/api/app/status', handle_app_status)
    app.router.add_get('/healthz', handle_app_status)
    app.router.add_get('/ping', handle_app_status)
    app.router.add_post('/api/app/compare-keys', handle_app_compare_keys)
    app.router.add_post('/api/app/set-pin', handle_app_set_pin)
    app.router.add_post('/api/app/verify-pin', handle_app_verify_pin)
    # Universal Static Route
    app.router.add_get('/css/{path:.*}', handle_static_file)
    app.router.add_get('/js/{path:.*}', handle_static_file)
    app.router.add_get('/img/{path:.*}', handle_static_file)
    app.router.add_get('/{path:[^/]+\\.(?:css|js|png|jpg|jpeg|svg|ico|json)}', handle_static_file)

    return app

