"""Google Sheets bilan ishlash: 'Publish to web' orqali chop etilgan CSV havolalardan o'qish.

Google Cloud / service account / JSON kalit KERAK EMAS.

MUHIM: bu — ishchi (obidxon) haqiqatda foydalanayotgan ESKI/ASOSIY jadval
tuzilmasi (bitta "Tranzaksiyalar" varag'i emas!). Jadvalda 4 ta alohida
varaq bor, va Bo'lim/Turi ustun orqali emas, balki QAYSI VARAQ ekanidan
aniqlanadi:

    Kirim kiritish   -> Klub, Kirim
    Chiqim kiritish  -> Klub, Chiqim
    Bar - Kirim      -> Bar,  Kirim
    Bar - Chiqim     -> Bar,  Chiqim

Google'ning CSV eksporti bir vaqtda faqat BITTA varaqni chiqaradi, shuning
uchun har bir varaq ALOHIDA "Publish to web" havolasiga ega bo'lishi va
alohida env varga yozilishi kerak (qarang: SHEET_SOURCES).

Har bir varaqdagi ustunlar: Sana | Tavsif (nima uchun) | Summasi (so'm) |
To'lov usuli | Kim kiritdi | Yuborildi. "Yuborildi" ustuni botga aloqasi
yo'q (eski, ishlatilmaydigan ustun) — bot uni e'tiborsiz qoldiradi.

Eslatma: Google'ning chop etilgan (published) versiyasi har safar darhol emas,
odatda bir necha daqiqada yangilanadi — shuning uchun jadvalga endigina kiritilgan
qator botda 2-5 daqiqagacha kechikib ko'rinishi mumkin.
"""
import os
import csv
import io
from datetime import date, datetime

import requests

# Har bir manba: (env var nomi, Bo'lim, Turi)
SHEET_SOURCES = [
    ("CSV_URL_KLUB_KIRIM", "Klub", "Kirim"),
    ("CSV_URL_KLUB_CHIQIM", "Klub", "Chiqim"),
    ("CSV_URL_BAR_KIRIM", "Bar", "Kirim"),
    ("CSV_URL_BAR_CHIQIM", "Bar", "Chiqim"),
]

# Har bir varaqdagi ustunlar tartibi
COL_SANA = 0
COL_TAVSIF = 1
COL_SUMMASI = 2
COL_TOLOV_USULI = 3
COL_KIM = 4
COL_YUBORILDI = 5

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


def _fetch_one_sheet(env_var_nomi, bolim, turi):
    """Bitta varaqning CSV havolasini o'qib, shu varaqqa tegishli qatorlarni qaytaradi."""
    url = os.environ[env_var_nomi]
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    reader = csv.reader(io.StringIO(resp.text))
    all_rows = list(reader)

    rows = []
    # row_no = CSV'dagi qator raqami (1-dan boshlab). Sarlavha/tavsif/bo'sh
    # qatorlar sananing aniqlanmasligi tufayli avtomatik o'tkazib yuboriladi,
    # shuning uchun bu yerda qat'iy qator raqamini oldindan bilish shart emas.
    # env_var_nomi bilan birga noyob identifikator hosil qiladi — 4 ta varaqdagi
    # bir xil raqamli qatorlar bir-biriga aralashib ketmasligi uchun (tracker.py).
    for row_no, raw in enumerate(all_rows, start=1):
        if not raw:
            continue
        raw = raw + [""] * (6 - len(raw))
        d = _parse_date(raw[COL_SANA])
        if d is None:
            continue  # sanasi yo'q/tushunarsiz qator (sarlavha, instruksiya, bo'sh qator) - o'tkazib yuboramiz
        summasi = _parse_amount(raw[COL_SUMMASI])
        if summasi == 0:
            continue  # summasi yo'q - real tranzaksiya emas
        tavsif = str(raw[COL_TAVSIF]).strip() or "Boshqa"
        kim = str(raw[COL_KIM]).strip()
        rows.append({
            "row_no": f"{env_var_nomi}:{row_no}",
            "sana": d,
            "bolim": bolim,
            "kategoriya": tavsif,
            "kirim": summasi if turi == "Kirim" else 0.0,
            "chiqim": summasi if turi == "Chiqim" else 0.0,
            "izoh": "",
            "kim": kim,
        })
    return rows


def fetch_rows():
    """4 ta varaqning CSV havolalarini o'qib, bitta umumiy ro'yxatga birlashtiradi."""
    rows = []
    for env_var_nomi, bolim, turi in SHEET_SOURCES:
        rows.extend(_fetch_one_sheet(env_var_nomi, bolim, turi))
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
