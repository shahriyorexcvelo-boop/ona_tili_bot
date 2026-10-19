"""
PDF Watermark and Image Processing Service.
PyMuPDF (fitz) va Pillow (PIL) orqali PDF sahifalarini oq fonga ko'chirib,
markaziga shaffof logotip (watermark) qo'yish moduli.
"""
from __future__ import annotations
import asyncio
import io
import logging
import os
from typing import Optional

try:
    import pymupdf as fitz
except ImportError:
    import fitz
from PIL import Image, ImageEnhance

log = logging.getLogger("OnaTiliRasch.PDFWatermark")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_LOGO_PATH = os.path.join(BASE_DIR, "logo.png")


def process_pdf_watermark_sync(
    input_pdf_path: str,
    output_pdf_path: str,
    logo_path: Optional[str] = None,
    opacity: float = 0.22,
    scale_ratio: float = 0.55,
    render_zoom: float = 2.0
) -> int:
    """
    Sinxron ravishda PDF faylning har bir sahifasini oq fonga ko'chiradi
    va markaziga shaffof logotip (watermark) joylashtirib yangi PDF yaratadi.
    
    :param input_pdf_path: Asl PDF fayl yo'li
    :param output_pdf_path: Chiqish PDF fayl yo'li
    :param logo_path: Logotip rasm yo'li (logo.png)
    :param opacity: Logotipning shaffoflik darajasi (0.0 dan 1.0 gacha, odatda 0.20-0.25)
    :param scale_ratio: Logotip sahifa kengligining qancha qismini egallashi (0.50-0.60)
    :param render_zoom: Sahifani rasmga aylantirish aniqligi (2.0 = 144 DPI, sifatli va ixcham)
    :return: Qayta ishlangan sahifalar soni
    """
    if not logo_path or not os.path.exists(logo_path):
        logo_path = DEFAULT_LOGO_PATH

    if not os.path.exists(logo_path):
        raise FileNotFoundError(f"Logotip fayli topilmadi: {logo_path}")

    # 1. Logotipni tayyorlash va shaffoflikni sozlash
    base_logo = Image.open(logo_path).convert("RGBA")
    r, g, b, alpha = base_logo.split()
    alpha = ImageEnhance.Brightness(alpha).enhance(opacity)
    base_logo.putalpha(alpha)
    logo_orig_w, logo_orig_h = base_logo.size
    logo_aspect = logo_orig_h / max(1, logo_orig_w)

    input_doc = fitz.open(input_pdf_path)
    output_doc = fitz.open()

    total_pages = len(input_doc)
    log.info(f"📄 PDF qayta ishlash boshlandi: {total_pages} ta sahifa, logo={logo_path}")

    try:
        for page_idx in range(total_pages):
            page = input_doc[page_idx]

            # 2. Sahifani yuqori sifatda rasmga o'tkazish
            mat = fitz.Matrix(render_zoom, render_zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)

            # 3. Pillow orqali oq fonli toza varaq hosil qilish
            page_img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples).convert("RGBA")
            white_canvas = Image.new("RGBA", page_img.size, (255, 255, 255, 255))
            canvas = Image.alpha_composite(white_canvas, page_img)

            # 4. Logotipni sahifa o'lchamiga moslab masshtablash
            target_w = max(50, int(canvas.width * scale_ratio))
            target_h = max(50, int(target_w * logo_aspect))
            resized_logo = base_logo.resize((target_w, target_h), Image.Resampling.LANCZOS)

            # 5. Logotipni sahifa markaziga joylashtirish
            pos_x = (canvas.width - target_w) // 2
            pos_y = (canvas.height - target_h) // 2

            watermark_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            watermark_layer.paste(resized_logo, (pos_x, pos_y))

            # 6. Qatlamlarni birlashtirish
            final_page_img = Image.alpha_composite(canvas, watermark_layer).convert("RGB")

            # 7. Sifatli va siqilgan JPEG sifatida PyMuPDF varag'iga joylash
            img_buffer = io.BytesIO()
            final_page_img.save(img_buffer, format="JPEG", quality=90, optimize=True)
            img_bytes = img_buffer.getvalue()

            new_page = output_doc.new_page(width=page.rect.width, height=page.rect.height)
            new_page.insert_image(new_page.rect, stream=img_bytes)

        # 8. Yakuniy PDF faylni saqlash
        output_doc.save(output_pdf_path, deflate=True)
        log.info(f"✅ PDF muvaffaqiyatli saqlandi: {output_pdf_path}")
        return total_pages
    finally:
        output_doc.close()
        input_doc.close()


async def process_pdf_watermark_async(
    input_pdf_path: str,
    output_pdf_path: str,
    logo_path: Optional[str] = None,
    opacity: float = 0.22
) -> int:
    """
    Botni qotirib qo'ymaslik uchun og'ir hisob-kitoblarni alohida thread executor'da bajaradi.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        process_pdf_watermark_sync,
        input_pdf_path,
        output_pdf_path,
        logo_path,
        opacity
    )
