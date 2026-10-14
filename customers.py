"""Mijozlar bazasi: Google Sheets'ga Apps Script web-app orqali yozish va o'qish.

Hisob-kitob qismi (sheets.py) jadvalni faqat "Publish to web" CSV orqali O'QIYDI —
u yerga yozib bo'lmaydi. Mijozlarni yozish uchun esa jadvalga ulangan kichik
Google Apps Script web-app ishlatiladi (google-apps-script-mijozlar.gs).
Google Cloud / service account / JSON kalit KERAK EMAS.

Mijozlar filial bo'yicha ALOHIDA varaqlarga yoziladi:
    Mijozlar - Yangi bozor
    Mijozlar - Chorsu

Muhit o'zgaruvchilari (Railway):
    MIJOZ_API_URL     — Apps Script "Web app" havolasi (.../exec)
    MIJOZ_API_SECRET  — skriptdagi SECRET bilan bir xil maxfiy so'z
"""
import os

import requests

# kalit -> ko'rinadigan nom (kalitlar Apps Script'dagi bilan bir xil bo'lishi shart)
BRANCHES = {
    "yangibozor": "Yangi bozor",
    "chorsu": "Chorsu",
}

# /random va /mijozlar buyruqlarida filialni yozishning qabul qilinadigan variantlari
BRANCH_ALIASES = {
    "yangibozor": "yangibozor",
    "yangi": "yangibozor",
    "yangi_bozor": "yangibozor",
    "yangi-bozor": "yangibozor",
    "chorsu": "chorsu",
}


class CustomersError(Exception):
    """Apps Script bilan bog'lanishdagi xato."""


def _call(action: str, **payload) -> dict:
    url = os.environ.get("MIJOZ_API_URL", "").strip()
    secret = os.environ.get("MIJOZ_API_SECRET", "").strip()
    if not url or not secret:
        raise CustomersError("MIJOZ_API_URL yoki MIJOZ_API_SECRET sozlanmagan")
    resp = requests.post(
        url, json={"secret": secret, "action": action, **payload}, timeout=30
    )
    resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError as exc:
        raise CustomersError(
            "Apps Script javobi JSON emas — web-app 'Anyone' ruxsati bilan deploy qilinganini tekshiring"
        ) from exc
    if not data.get("ok"):
        raise CustomersError(data.get("error", "noma'lum xato"))
    return data


def normalize_phone(raw: str) -> str:
    """'998901234567' / '+998 90 123 45 67' -> '+998901234567'."""
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    return f"+{digits}" if digits else ""


def find_customer(tg_id: int):
    """Telegram ID bo'yicha mijozni topadi (ikkala filialdan). Topilmasa None."""
    return _call("find", tg_id=str(tg_id)).get("customer")


def register_customer(tg_id: int, username: str, name: str, phone: str, branch: str) -> dict:
    """Mijozni filial varag'iga yozadi.

    Qaytaradi: {"status": "created" | "exists", "customer": {...}}
    Telegram ID yoki telefon raqami allaqachon bazada bo'lsa — qayta yozilmaydi.
    """
    if branch not in BRANCHES:
        raise CustomersError(f"Noma'lum filial: {branch}")
    data = _call(
        "register",
        tg_id=str(tg_id),
        username=username or "",
        name=name,
        phone=normalize_phone(phone),
        branch=branch,
    )
    return {"status": data.get("status"), "customer": data.get("customer")}


def list_customers(branch: str | None = None) -> list[dict]:
    """Barcha mijozlar (yoki faqat bitta filial)."""
    return _call("list", branch=branch or "").get("customers", [])
