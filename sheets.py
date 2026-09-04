"""Google Sheets bilan ishlash: 'Publish to web' orqali chop etilgan CSV havoladan o'qish.

Google Cloud / service account / JSON kalit KERAK EMAS — jadval "Publish to web"
orqali CSV sifatida chop etiladi, bot esa shunchaki shu havoladan o'qiydi.

Jadval ustunlari: Sana | Bo'lim | Kategoriya | Kirim (so'm) | Chiqim (so'm) | Izoh | Kim kiritdi
- Bo'lim: "Klub" yoki "Bar" (ikkalasi mustaqil, hech qachon bir-biriga qo'shilmaydi)
- Har bir qatorda faqat bitta ustun (Kirim YOKI Chiqim) to'ldiriladi.

Eslatma: Google'ning chop etilgan (published) versiyasi har safar darhol emas,
odatda bir necha daqiqada yangilanadi — shuning uchun jadvalga endigina kiritilgan
qator botda 2-5 daqiqagacha kechikib ko'rinishi mumkin.
"""
import os
import csv
import io
from datetime import date, datetime

import requests

# Tranzaksiyalar varag'idagi ustunlar tartibi (shablon fayldagi bilan bir xil)
COL_SANA = 0
COL_BOLIM = 1
COL_KATEGORIYA = 2
COL_KIRIM = 3
COL_CHIQIM = 4
COL_IZOH = 5
COL_KIM = 6

KIRIM_LABEL = "Kirim"
CHIQIM_LABEL = "Chiqim"
UNITS = ("Klub", "Bar")  # ko'rsatiladigan tartib


def _parse_date(value):
    """Turli formatdagi sana matnini date obyektiga aylantiradi."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _parse_amount(value):
    if value is None:
        return 0.0
    s = str(value).strip().replace(" ", "").replace(",", "").replace("so'm", "").replace("сум", "")
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _parse_bolim(value):
    s = str(value).strip().lower()
    if s == "bar":
        return "Bar"
    return "Klub"  # bo'sh yoki noma'lum qiymat - standart bo'yicha Klub


def fetch_rows():
    """Nashr qilingan (Publish to web) CSV havoladan barcha tranzaksiya qatorlarini o'qiydi."""
    url = os.environ["CSV_URL"]
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    reader = csv.reader(io.StringIO(resp.text))
    all_rows = list(reader)

    rows = []
    # row_no = jadvaldagi haqiqiy qator raqami (1-qator sarlavha, shuning uchun
    # ma'lumotlar 2-qatordan boshlanadi). Bu raqam har bir tranzaksiyaning
    # "shaxsini" bildiradi va keyinchalik o'sha qator o'zgartirilgan/o'chirilganini
    # aniqlash uchun ishlatiladi (qarang: tracker.py).
    for row_no, raw in enumerate(all_rows[1:], start=2):
        if not raw:
            continue
        raw = raw + [""] * (7 - len(raw))
        d = _parse_date(raw[COL_SANA])
        if d is None:
            continue  # sanasi yo'q/tushunarsiz qator (bo'sh qator yoki hisobot bloki) - o'tkazib yuboramiz
        bolim = _parse_bolim(raw[COL_BOLIM])
        kategoriya = str(raw[COL_KATEGORIYA]).strip() or "Boshqa"
        kirim = _parse_amount(raw[COL_KIRIM])
        chiqim = _parse_amount(raw[COL_CHIQIM])
        if kirim == 0 and chiqim == 0:
            continue  # ikkalasi ham bo'sh - real tranzaksiya emas
        izoh = str(raw[COL_IZOH]).strip()
        kim = str(raw[COL_KIM]).strip()
        rows.append({
            "row_no": row_no,
            "sana": d,
            "bolim": bolim,
            "kategoriya": kategoriya,
            "kirim": kirim,
            "chiqim": chiqim,
            "izoh": izoh,
            "kim": kim,
        })
    return rows


def _aggregate(rows, start_date, end_date):
    kirim_total = 0.0
    chiqim_total = 0.0
    by_category = {}
    matched = []
    for r in rows:
        if r["sana"] < start_date or r["sana"] > end_date:
            continue
        matched.append(r)
        if r["kirim"]:
            by_category[(KIRIM_LABEL, r["kategoriya"])] = (
                by_category.get((KIRIM_LABEL, r["kategoriya"]), 0.0) + r["kirim"]
            )
            kirim_total += r["kirim"]
        if r["chiqim"]:
            by_category[(CHIQIM_LABEL, r["kategoriya"])] = (
                by_category.get((CHIQIM_LABEL, r["kategoriya"]), 0.0) + r["chiqim"]
            )
            chiqim_total += r["chiqim"]
    return {
        "start_date": start_date,
        "end_date": end_date,
        "kirim_total": kirim_total,
        "chiqim_total": chiqim_total,
        "net": kirim_total - chiqim_total,
        "by_category": by_category,
        "rows": matched,
    }


def summarize_by_unit(rows, start_date, end_date, units=None):
    """Klub va Bar uchun ALOHIDA-ALOHIDA jamlanma qaytaradi (hech qachon qo'shib yuborilmaydi).

    units berilmasa, UNITS dagi barcha bo'limlar uchun hisoblanadi.
    Natija: {"Klub": {...}, "Bar": {...}}  (faqat so'ralgan bo'limlar bilan)
    """
    target_units = units if units else UNITS
    result = {}
    for unit in target_units:
        unit_rows = [r for r in rows if r["bolim"] == unit]
        result[unit] = _aggregate(unit_rows, start_date, end_date)
    return result
