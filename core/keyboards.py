from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup, WebAppInfo
)
import test_db
from .config import WEBAPP_URL, ADMIN_ID, get_webapp_url

def make_webapp_button(text: str, url: str, fallback_cb: str = "open_webapp_info") -> InlineKeyboardButton:
    """Telegram WebApp faqat HTTPS talab qiladi. Agar HTTP (localhost) bo'lsa callback ishlatiladi."""
    if url.startswith("https://"):
        return InlineKeyboardButton(text=text, web_app=WebAppInfo(url=url))
    elif url.startswith("http://") and not ("localhost" in url or "127.0.0.1" in url):
        return InlineKeyboardButton(text=text, url=url)
    else:
        return InlineKeyboardButton(text=text, callback_data=fallback_cb)

def main_menu_kb(user_tg_id: int) -> ReplyKeyboardMarkup:
    is_adm = test_db.is_admin(user_tg_id, ADMIN_ID)
    base_url = get_webapp_url()
    
    app_url = f"{base_url}/app.html?tg_id={user_tg_id}&v=20261020_v1"
    admin_webapp_url = f"{base_url}/admin.html?tg_id={user_tg_id}&v=20261020_v1"

    # Agar HTTPS bo'lsa to'g'ridan-to'g'ri Telegram WebApp ochadi
    if app_url.startswith("https://"):
        results_btn = KeyboardButton(text="📊 Mening natijalarim", web_app=WebAppInfo(url=app_url))
    else:
        results_btn = KeyboardButton(text="📊 Mening natijalarim")

    if admin_webapp_url.startswith("https://"):
        create_test_btn = KeyboardButton(text="➕ Yangi test yaratish", web_app=WebAppInfo(url=admin_webapp_url))
    else:
        create_test_btn = KeyboardButton(text="➕ Yangi test yaratish")

    if is_adm:
        # Adminlar uchun faqat admin funksiyalari:
        # 1-qator: Test kodini kiritish + Yangi test yaratish (Mini App)
        # 2-qator: Test natijalari va reyting + Testlarni boshqarish
        # 3-qator: Admin Panel
        buttons = [
            [KeyboardButton(text="🔢 Test kodini kiritish"), create_test_btn],
            [KeyboardButton(text="📊 Test natijalari va reyting"), KeyboardButton(text="📋 Testlarni boshqarish")],
            [KeyboardButton(text="⚙️ Admin Panel")]
        ]
    else:
        # Oddiy foydalanuvchilar uchun menyu tartibi:
        # 1-qator: Test kodini kiritish + Mening natijalarim (Asosiy App)
        # 2-qator: Profilim + Yordam (adminga murojaat)
        buttons = [
            [KeyboardButton(text="🔢 Test kodini kiritish"), results_btn],
            [KeyboardButton(text="👤 Profilim"), KeyboardButton(text="ℹ️ Yordam")]
        ]

    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def profile_webapp_kb(user_tg_id: int) -> InlineKeyboardMarkup:
    """Shaxsiy profil mini ilovasini ochish tugmasi."""
    base_url = get_webapp_url()
    app_url = f"{base_url}/app.html?tg_id={user_tg_id}&v=20261020_v1"
    buttons = [
        [make_webapp_button("📱 Shaxsiy profilni ochish", app_url, fallback_cb="open_app_info")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def results_webapp_kb(user_tg_id: int = 0) -> InlineKeyboardMarkup:
    """Natijalarni ko'rish mini ilovasi tugmasi."""
    base_url = get_webapp_url()
    qs = f"?tg_id={user_tg_id}&v=20261020_v1" if user_tg_id else "?v=20261020_v1"
    app_url = f"{base_url}/app.html{qs}"
    buttons = [
        [make_webapp_button("📊 Asosiy ilovani ochish", app_url, fallback_cb="open_app_info")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def contact_share_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Telefon raqamni yuborish", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def admin_menu_kb() -> InlineKeyboardMarkup:
    """
    Admin Panel inline menyusi — xabarlar, texnik rejim, test vaqti va adminlar boshqaruvi.
    Foydalanuvchilar boshqaruvi to'liq Asosiy Web Ilovaga (Main App) ko'chirilgan.
    """
    maint_on = test_db.is_maintenance_mode()
    maint_icon = "🔴" if maint_on else "🟢"
    maint_status = "YOQILGAN" if maint_on else "O'CHIQ"
    maint_btn_text = f"🛠 Texnik rejim: {maint_status} {maint_icon}"

    reg_open = test_db.is_registration_open()
    reg_icon = "🟢" if reg_open else "🔴"
    reg_status = "OCHIQ" if reg_open else "YOPIQ"
    reg_btn_text = f"👥 Yangi qabul: {reg_status} {reg_icon}"

    base_url = get_webapp_url()
    buttons = [
        [InlineKeyboardButton(text="📄 PDF Watermark qo'yish / Tayyorlash", callback_data="admin_pdf_watermark_info")],
        [InlineKeyboardButton(text="💬 Foydalanuvchiga yozish / Chat", callback_data="admin_contact_user_prompt")],
        [InlineKeyboardButton(text="📢 O'quvchilarga xabar yuborish", callback_data="admin_broadcast_menu")],
        [InlineKeyboardButton(text="⚠️ Faoliyatsizlarga ogohlantirish", callback_data="admin_warn_inactive_menu")],
        [InlineKeyboardButton(text="🧹 Botni bloklaganlarni tozalash", callback_data="admin_clean_blocked_prompt")],
        [InlineKeyboardButton(text=maint_btn_text, callback_data="admin_toggle_maint_prompt")],
        [InlineKeyboardButton(text=reg_btn_text, callback_data="admin_toggle_registration")],
        [InlineKeyboardButton(text="👑 Adminlar boshqaruvi", callback_data="admin_manage_admins")],
        [InlineKeyboardButton(text="📊 Bot statistikasi (/stats)", callback_data="admin_stats_quick")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)
