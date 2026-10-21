# Ona Tili — Test Tekshirish Telegram Boti va Mini App Tizimi

Ushbu tizim O'zbekiston Milliy Sertifikat standarti va Rasch psixometrik modeli asosida testlarni tekshirish, natijalarni hisoblash hamda tahlil qilish uchun mo'ljallangan yuqori unumdorlikka ega Telegram Bot va Mini App ilovasidir.

---

## 🔒 MA'LUMOTLARNI SAQLASH VA XAVFSIZLIK (DATA INTEGRITY)

Tizimda ma'lumotlar yo'qolishining (data loss) oldini olish bo'yicha quyidagi qat'iy arxitektura tamoyillari amal qiladi:
1. **Foydalanuvchilar (Users):** Foydalanuvchilar hech qachon bazadan butunlay o'chirilmaydi.
   - Admin o'chirganda: `deleted_at` va `deleted_by` qayd etiladi (Soft Delete). Istalgan paytda `restore_user` orqali tiklash mumkin.
   - Foydalanuvchi o'z akkauntini o'chirganda (`/api/app/delete-my-account`): Shaxsiy ma'lumotlar (`fullname`, `phone`, `username`, `pin_code`) anonimlashtiriladi, lekin barcha topshirgan testlari va natijalari saqlanadi.
2. **Testlar va Kalitlar (Tests):** Bazadagi barcha testlar va ularning kalitlari daxlsiz saqlanadi. O'qish tezligini oshirish uchun 45 soniyalik Read-Through RAM kesh qo'llaniladi.
3. **Javoblar va Topshirishlar (Submissions):**
   - **Autosave Drafts:** Foydalanuvchi test ishlayotganida har bir savol javobi darhol `submission_drafts` va `user_test_drafts` jadvallariga qoralama sifatida avtomatik yoziladi. Sahifa yangilanganda yoki telefon o'chib yonganda javoblar avtomatik tiklanadi.
   - **Atomar tranzaksiya:** Yakuniy topshirish bitta ACID tranzaksiyasida amalga oshiriladi va qoralamalar avtomatik tozalanadi.
   - **Dublikatdan himoya:** `UNIQUE(user_tg_id, test_id)` orqali bitta testni qayta topshirish yoki bir vaqtda ikki marta bosish to'liq bartaraf etilgan.
4. **Tun Rejimi (Night Mode):** Soat 23:00 dan 07:00 gacha bo'lgan vaqtda ham faol yoki rejalashtirilgan test ketayotgan bo'lsa, o'quvchilar bemalol test topshira oladi.
5. **Bir nechta instance to'qnashuvini bartaraf etish:** Webhook rejimi (`USE_WEBHOOK=1`) aiohttp serveri orqali xavfsiz token bilan ishlaydi.

---

## ⚠️ SIRLARNI (SECRETS) ROTATSIYA QILISH BO'YICHA ESLATMA

> [!WARNING]
> Avvalgi render.yaml va .env.example fayllarida Telegram Bot Tokeni va Supabase ma'lumotlar bazasi paroli ochiq ko'rinishda bo'lgan.
> Xavfsizlikni to'liq ta'minlash uchun quyidagilarni amalga oshirish qat'iy tavsiya etiladi:
> 1. **Telegram Bot Token:** @BotFather ga kirib, `/revoke` buyrug'i orqali yangi token oling.
> 2. **Supabase Paroli:** Supabase Dashboard -> Project Settings -> Database bo'limidan bazaning parolini yangilang (Reset Database Password).
> 3. Yangi parollarni Render Dashboard dagi **Environment Variables** ga qo'lda kiriting (`sync: false` qilib sozlangan).

---

## 🚀 ISHGA TUSHIRISH VA SOZLASh

### 1. Muhit parametrlarini o'rnatish (.env)
`.env.example` nusxasidan `.env` faylini yarating va qiymatlarni to'ldiring:
```bash
cp .env.example .env
```

### 2. Bog'liqliklarni o'rnatish
```bash
pip install -r requirements.txt
```

### 3. Migratsiyalarni qo'llash
Baza ishga tushganda avtomatik ravishda barcha idempotent SQL migratsiyalari tekshiriladi va qo'llaniladi. Qo'lda ishga tushirish:
```bash
python -m migrations.runner
```

### 4. Bot va Serverni ishga tushirish
```bash
python test_bot.py
```

> [!IMPORTANT]
> **Lokal sinovlar uchun alohida `BOT_TOKEN` ishlating:**
> Render production serveri Webhook rejimida 24/7 ishlaydi. Agar lokal kompyuteringizda production `BOT_TOKEN` orqali bot ishga tushirilsa, Telegram bot ziddiyatiga (Conflict error) olib kelishi mumkin. Shuning uchun lokal sinov va testlar uchun @BotFather orqali yaratilgan alohida test boti tokenini (.env da) ishlatish tavsiya etiladi. Lokal ishga tushirilganda production webhooki o'chirilmaydi.


---

## 🧪 AVTOMATLASHGAN TESTLARNI ISHGA TUSHIRISH

Loyihada ma'lumotlar saqlanishi va barcha xavfsizlik talablarini tekshiruvchi 8 ta to'liq avtomatlashtirilgan test mavjud:
```bash
pytest tests/test_data_preservation.py -v
```

---

## 📦 BAZANI ZAXIRALASH (BACKUP) VA TIKLASH (RESTORE)

### 1. Zaxira nusxa yaratish
```bash
python scripts/backup.py
```
Zaxira fayli sana va vaqt bilan siqilgan (`.sql.gz` yoki `.json.gz`) holda `backups/` papkasiga saqlanadi.

### 2. Render da Cron Job sifatida sozlash
Render boshqaruv panelida:
1. **New +** -> **Cron Job** ni tanlang.
2. Nomi: `ona-tili-db-backup`
3. Command: `python scripts/backup.py`
4. Schedule: `0 3 * * *` (Har kuni tunda 03:00 da)
5. Environment Variables: `DATABASE_URL` ni ulang.

### 3. Zaxiradan tiklash (Restore)
```bash
python scripts/restore.py backups/backup_YYYYMMDD_HHMMSS.json.gz --yes
```

---

## ⚡ RENDER BEPUL TARIF (FREE PLAN) VA UYG'OQ SAQLASH

`render.yaml` faylida `plan: free` sozlangan. Render bepul tarifidagi Web Service 15 daqiqa davomida kiruvchi so'rov bo'lmasa, avtomatik ravishda uyqu (spin-down / idle) rejimiga o'tadi. Natijada keyingi so'rov kelganda bot 30–50 soniya "uyg'onish" (cold start) kechikishiga uchrashi mumkin.

**Yechimlar:**
1. **Bepul tashqi ping (tavsiya etiladi):** Bepul monitoring xizmatlari ([UptimeRobot](https://uptimerobot.com/), [cron-job.org](https://cron-job.org/) yoki [Better Stack](https://betterstack.com/)) orqali botingizning `/healthz` endpointini har 10 daqiqada bir marta chaqirib turing (masalan: `GET https://ona-tili-bot.onrender.com/healthz`). Bu botning uxlamasdan doimiy faol turishini ta'minlaydi.
2. **Pullik tarifga o'tish:** Render boshqaruv panelida Web Service ni `Starter` ($7/oy) tarifiga o'tkazsangiz, bot 24/7 rejimida hech qachon uxlamaydi va yuqori tezlikda uzluksiz ishlaydi.
