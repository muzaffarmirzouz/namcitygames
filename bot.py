"""
Namcity Games — Kirim-Chiqim Telegram bot

Bu bot Google Sheets'dagi "Tranzaksiyalar" varag'ini o'qiydi va:
  - har kuni belgilangan vaqtda avtomatik kunlik hisobot yuboradi (Klub va Bar ALOHIDA)
  - /bugun, /sana, /oraliq komandalar orqali istalgan sana/oraliq bo'yicha
    kirim-chiqim hisobotini chiqarib beradi

Ishchi ma'lumotlarni to'g'ridan-to'g'ri Google Sheets'ga kiritadi — bot faqat o'qiydi.
Klub va Bar hisoblari hech qachon qo'shib yuborilmaydi (ular ikki xil egaga tegishli) —
lekin ikkalasi ham xohlasangiz bitta xabarda, alohida bloklarda ko'rsatiladi.
"""
import asyncio
import html
import logging
import os
import random
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from sheets import fetch_rows, summarize_by_unit, UNITS
from formatting import format_report, format_change_alert, format_history
from tracker import snapshot_from_rows, diff_snapshots
from customers import (
    BRANCHES,
    BRANCH_ALIASES,
    CustomersError,
    find_customer,
    list_customers,
    register_customer,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

TZ = ZoneInfo(os.environ.get("TIMEZONE", "Asia/Tashkent"))
REPORT_HOUR = int(os.environ.get("REPORT_HOUR", "22"))
REPORT_MINUTE = int(os.environ.get("REPORT_MINUTE", "0"))
CHECK_INTERVAL_MINUTES = int(os.environ.get("CHECK_INTERVAL_MINUTES", "10"))
HISTORY_LIMIT = 200  # /tarix uchun xotirada saqlanadigan eng ko'p yozuvlar soni


def _allowed_ids() -> set[int]:
    raw = os.environ.get("OWNER_CHAT_ID", "").strip()
    if not raw:
        return set()
    ids = set()
    for part in raw.split(","):
        part = part.strip()
        if part:
            try:
                ids.add(int(part))
            except ValueError:
                pass
    return ids


def _is_authorized(chat_id: int) -> bool:
    allowed = _allowed_ids()
    if not allowed:
        return False
    return chat_id in allowed


def _extract_unit(args: list[str]):
    """Agar oxirgi so'z 'klub' yoki 'bar' bo'lsa, uni ajratib qaytaradi.

    Qaytaradi: (unit_yoki_None, qolgan_argumentlar)
    unit=None -> Klub VA Bar ikkalasi ham ko'rsatiladi.
    """
    if args and args[-1].strip().lower() in ("klub", "bar"):
        word = args[-1].strip().lower()
        unit = "Klub" if word == "klub" else "Bar"
        return unit, args[:-1]
    return None, args


async def admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    allowed = _allowed_ids()
    if not allowed:
        await update.message.reply_text(
            "Salom! Bot hali to'liq sozlanmagan.\n\n"
            f"Sizning chat ID'ingiz: `{chat_id}`\n\n"
            "Buni Railway (yoki boshqa hosting)dagi OWNER_CHAT_ID muhit o'zgaruvchisiga "
            "kiritib, botni qayta ishga tushiring.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    if not _is_authorized(chat_id):
        await update.message.reply_text(
            f"Salom! Bu bot faqat ruxsat berilgan foydalanuvchilar uchun.\n"
            f"Sizning chat ID'ingiz: `{chat_id}` — administratorga yuboring.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    await update.message.reply_text(
        "Salom! Namcity Games kirim-chiqim boti ishga tushdi. ✅\n\n"
        "Buyruqlar (Klub va Bar hisoblari doim alohida ko'rsatiladi):\n"
        "/bugun — bugungi kirim-chiqim (Klub + Bar)\n"
        "/bugun klub — faqat Klub\n"
        "/bugun bar — faqat Bar\n"
        "/sana 2026-08-29 — shu kundagi hisobot\n"
        "/sana 2026-08-29 bar — shu kundagi Bar hisoboti\n"
        "/oraliq 2026-08-01 2026-08-29 — oraliq bo'yicha hisobot\n"
        "/oraliq 2026-08-01 2026-08-29 klub — oraliq, faqat Klub\n"
        "/tarix — jadvalda qilingan oxirgi o'zgartirishlar\n"
        "/yordam — yordam\n\n"
        "👥 Mijozlar bazasi:\n"
        "/mijozlar — mijozlar soni (filiallar bo'yicha)\n"
        "/random — barcha mijozlar orasidan 1 ta g'olib\n"
        "/random chorsu 3 — Chorsu mijozlari orasidan 3 ta g'olib\n"
        "/random yangibozor — Yangi bozor mijozlari orasidan\n\n"
        "🔎 Kimdir jadvalda avval kiritilgan summani/sanani o'zgartirsa — ZUDLIK BILAN "
        "xabar keladi (Google Sheets skripti orqali). Butun qator o'chirilsa esa "
        f"eng ko'pi bilan {CHECK_INTERVAL_MINUTES} daqiqa ichida xabar beradi."
    )


async def yordam(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_chat.id):
        await _deny(update, context)
        return
    await update.message.reply_text(
        "📖 *Buyruqlar*\n\n"
        "/bugun [klub|bar] — bugungi hisobot\n"
        "/sana YYYY-MM-DD [klub|bar] — masalan: /sana 2026-08-15 bar\n"
        "/oraliq YYYY-MM-DD YYYY-MM-DD [klub|bar] — masalan: /oraliq 2026-08-01 2026-08-15\n"
        "/tarix — jadvalda so'nggi qilingan tahrir/o'chirishlar ro'yxati\n"
        "/mijozlar — mijozlar soni (Yangi bozor / Chorsu)\n"
        "/random [chorsu|yangibozor] [soni] — sovrinli o'yin g'olib(lar)ini tanlash\n\n"
        "`klub`/`bar` qo'shilmasa — ikkalasi ham alohida-alohida ko'rsatiladi.\n\n"
        f"Har kuni soat {REPORT_HOUR:02d}:{REPORT_MINUTE:02d} da avtomatik kunlik hisobot keladi.\n\n"
        "🔎 Kimdir allaqachon kiritilgan summani/sanani/bo'limni/kategoriyani o'zgartirsa — "
        "Google Sheets'ga bog'langan skript orqali ZUDLIK BILAN (bir necha soniyada) xabar "
        f"keladi. Butun qator o'chirilsa — bot buni eng ko'pi bilan {CHECK_INTERVAL_MINUTES} "
        "daqiqa ichida payqab, xabar beradi (bu holatni zudkor skript ushlay olmaydi). "
        "Hisobotlar (/bugun, /sana, /oraliq) esa har doim jadvalning ANIQ joriy holatidan "
        "hisoblanadi, shuning uchun adashib yozilgan summa tuzatilsa, keyingi hisobotda "
        "avtomatik to'g'ri chiqadi — eski xato hech qachon hisobga 'yopishib' qolmaydi.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def _reply_error(update: Update, text: str) -> None:
    await update.message.reply_text(f"⚠️ {text}")


async def _reply_report(update: Update, start_d, end_d, unit, title: str) -> None:
    try:
        rows = fetch_rows()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Sheets error")
        await _reply_error(update, f"Google Sheets'dan ma'lumot o'qib bo'lmadi: {exc}")
        return
    units = [unit] if unit else list(UNITS)
    summaries = summarize_by_unit(rows, start_d, end_d, units=units)
    await update.message.reply_text(
        format_report(summaries, title, start_d, end_d), parse_mode=ParseMode.MARKDOWN
    )


async def bugun(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_chat.id):
        await _deny(update, context)
        return
    unit, _rest = _extract_unit(context.args)
    today = datetime.now(TZ).date()
    await _reply_report(update, today, today, unit, "Bugungi hisobot")


async def sana(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_chat.id):
        await _deny(update, context)
        return
    if not context.args:
        await _reply_error(update, "Sanani kiriting. Masalan: /sana 2026-08-15")
        return
    unit, rest = _extract_unit(context.args)
    if not rest:
        await _reply_error(update, "Sanani kiriting. Masalan: /sana 2026-08-15")
        return
    try:
        d = datetime.strptime(rest[0], "%Y-%m-%d").date()
    except ValueError:
        await _reply_error(update, "Sana formati noto'g'ri. Masalan: /sana 2026-08-15")
        return
    await _reply_report(update, d, d, unit, "Sana bo'yicha hisobot")


async def oraliq(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_chat.id):
        await _deny(update, context)
        return
    unit, rest = _extract_unit(context.args)
    if len(rest) < 2:
        await _reply_error(
            update, "Ikkita sana kiriting. Masalan: /oraliq 2026-08-01 2026-08-15"
        )
        return
    try:
        d1 = datetime.strptime(rest[0], "%Y-%m-%d").date()
        d2 = datetime.strptime(rest[1], "%Y-%m-%d").date()
    except ValueError:
        await _reply_error(
            update, "Sana formati noto'g'ri. Masalan: /oraliq 2026-08-01 2026-08-15"
        )
        return
    if d1 > d2:
        d1, d2 = d2, d1
    await _reply_report(update, d1, d2, unit, "Oraliq bo'yicha hisobot")


async def daily_report_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    allowed = _allowed_ids()
    if not allowed:
        logger.warning("OWNER_CHAT_ID sozlanmagan — kunlik hisobot yuborilmadi")
        return
    try:
        rows = fetch_rows()
    except Exception:  # noqa: BLE001
        logger.exception("Kunlik hisobot uchun Sheets xatosi")
        return
    today = datetime.now(TZ).date()
    summaries = summarize_by_unit(rows, today, today)
    text = format_report(summaries, "📊 Kunlik hisobot", today, today)
    for chat_id in allowed:
        try:
            await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.MARKDOWN)
        except Exception:  # noqa: BLE001
            logger.exception("Chat %s ga xabar yuborib bo'lmadi", chat_id)


async def check_changes_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Jadvalni avvalgi holati bilan solishtirib, tahrir/o'chirishlarni aniqlaydi.

    Birinchi ishga tushishda (bot yangi ishga tushgan/qayta ishga tushirilgan
    bo'lsa) boshlang'ich holat sifatida saqlanadi va HECH QANDAY xabar
    yuborilmaydi — solishtirish faqat KEYINGI tekshiruvlardan boshlab ishlaydi.

    ESLATMA: Summa/sana/bo'lim/kategoriya TAHRIRLANISHI haqida ZUDLIK BILAN
    xabar endi Google Sheets'ga bog'langan Apps Script orqali yuboriladi
    (google-apps-script.gs), chunki bu yerdan (CSV orqali) chinakam zudkor
    xabar berish imkonsiz — "Publish to web" havolasi 1-5 daqiqa kechikadi.
    Shu sababli bu joyda faqat QATOR BUTUNLAY O'CHIRILGAN holatlar uchun
    xabar yuboriladi (buni Apps Script ishonchli tuta olmaydi) — tahrirlash
    voqealari esa ikkalanib xabar bermasin deb faqat /tarix tarixiga
    yoziladi, alohida Telegram xabari YUBORILMAYDI.
    """
    try:
        rows = fetch_rows()
    except Exception:  # noqa: BLE001
        logger.exception("O'zgarishlarni tekshirishda Sheets xatosi")
        return

    new_snapshot = snapshot_from_rows(rows)
    old_snapshot = context.bot_data.get("snapshot")
    context.bot_data["snapshot"] = new_snapshot

    if old_snapshot is None:
        logger.info("Boshlang'ich holat saqlandi (%d qator) — kuzatish boshlandi", len(new_snapshot))
        return

    changes = diff_snapshots(old_snapshot, new_snapshot)
    if not changes:
        return

    history = context.bot_data.setdefault("history", [])
    now = datetime.now(TZ)
    for change in changes:
        history.insert(0, {"detected_at": now, "change": change})
    del history[HISTORY_LIMIT:]

    # Faqat o'chirilgan qatorlar uchun Telegram xabari yuboramiz — tahrirlash
    # haqida zudkor xabarni endi Apps Script beradi (dublikat bo'lmasligi uchun).
    alert_changes = [c for c in changes if c["type"] == "deleted"]
    if not alert_changes:
        return

    allowed = _allowed_ids()
    if not allowed:
        return
    text = format_change_alert(alert_changes)
    for chat_id in allowed:
        try:
            await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.MARKDOWN)
        except Exception:  # noqa: BLE001
            logger.exception("Chat %s ga o'zgartirish xabarini yuborib bo'lmadi", chat_id)


async def tarix(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_chat.id):
        await _deny(update, context)
        return
    history = context.bot_data.get("history", [])
    await update.message.reply_text(format_history(history), parse_mode=ParseMode.MARKDOWN)


# ---------------------------------------------------------------------------
# MIJOZLAR BAZASI — QR-kod orqali kelgan oddiy foydalanuvchilar uchun
# ---------------------------------------------------------------------------
# Admin (OWNER_CHAT_ID) /start bosganda — avvalgidek hisob-kitob menyusi.
# Boshqa har kim /start bosganda — mijoz sifatida ro'yxatdan o'tadi:
#   filial (Yangi bozor / Chorsu) -> ism -> "📱 Raqamni yuborish" tugmasi
# Ma'lumot Google Sheets'dagi filialga mos varaqqa yoziladi (customers.py).
# Hisob-kitob buyruqlari mijozlarga ishlamaydi.

ASK_BRANCH, ASK_NAME, ASK_PHONE = range(3)

CONSENT_TEXT = (
    "Raqamingizni yuborish orqali Namcity Games'dan aksiyalar, sovrinli o'yinlar "
    "va takliflar haqida xabar (Telegram/SMS) olishga rozilik bildirasiz."
)

TECH_ERROR_TEXT = (
    "⚠️ Hozir texnik nosozlik bor, iltimos birozdan so'ng /start ni qayta bosing."
)


async def _deny(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin buyrug'ini oddiy foydalanuvchi yozsa."""
    if not _allowed_ids():
        await admin_start(update, context)
        return
    await update.message.reply_text(
        "Bu buyruq faqat administrator uchun.\n"
        "Namcity Games mijozi sifatida ro'yxatdan o'tish uchun /start ni bosing."
    )


async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/id — chat ID'ni ko'rsatadi (yangi admin qo'shish uchun)."""
    await update.message.reply_text(
        f"Sizning chat ID'ingiz: `{update.effective_chat.id}`", parse_mode=ParseMode.MARKDOWN
    )


def _branch_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(f"📍 {name}", callback_data=f"branch:{key}")]
         for key, name in BRANCHES.items()]
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_chat.type != "private":
        return ConversationHandler.END
    chat_id = update.effective_chat.id
    if not _allowed_ids() or _is_authorized(chat_id):
        await admin_start(update, context)
        return ConversationHandler.END

    try:
        existing = await asyncio.to_thread(find_customer, update.effective_user.id)
    except Exception:  # noqa: BLE001
        logger.exception("Mijozni tekshirishda xato")
        await update.message.reply_text(TECH_ERROR_TEXT)
        return ConversationHandler.END

    if existing:
        await update.message.reply_text(
            f"Siz allaqachon ro'yxatdan o'tgansiz ✅\n"
            f"Filial: {existing.get('branch', '')}\n\n"
            "Aksiya va sovrinli o'yinlar haqida shu bot orqali xabar beramiz. 🎮",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END

    context.user_data.pop("reg_branch", None)
    context.user_data.pop("reg_name", None)
    await update.message.reply_text(
        "Assalomu alaykum! 🎮 Namcity Games'ga xush kelibsiz!\n\n"
        "Doimiy mijozlarimiz bazasiga qo'shiling — aksiyalar, chegirmalar va "
        "sovrinli o'yinlarda qatnashasiz.\n\n"
        "Qaysi filialimizdasiz?",
        reply_markup=_branch_keyboard(),
    )
    return ASK_BRANCH


async def branch_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    key = (query.data or "").split(":", 1)[-1]
    if key not in BRANCHES:
        return ASK_BRANCH
    context.user_data["reg_branch"] = key
    await query.edit_message_text(f"📍 Filial: {BRANCHES[key]} ✅")
    await query.message.reply_text("Ismingizni yozing:")
    return ASK_NAME


async def branch_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Iltimos, filialni pastdagi tugmalardan tanlang:", reply_markup=_branch_keyboard()
    )
    return ASK_BRANCH


async def name_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = " ".join((update.message.text or "").split())
    if len(name) < 2 or len(name) > 50:
        await update.message.reply_text("Ismingizni to'g'ri yozing (2–50 ta harf):")
        return ASK_NAME
    context.user_data["reg_name"] = name
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("📱 Raqamni yuborish", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await update.message.reply_text(
        f"Rahmat, {name}! Endi pastdagi «📱 Raqamni yuborish» tugmasini bosing.\n\n"
        f"ℹ️ {CONSENT_TEXT}",
        reply_markup=keyboard,
    )
    return ASK_PHONE


async def phone_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Raqamni qo'lda yozmang — pastdagi «📱 Raqamni yuborish» tugmasini bosing. 👇"
    )
    return ASK_PHONE


async def contact_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    contact = update.message.contact
    user = update.effective_user
    if contact.user_id != user.id:
        await update.message.reply_text(
            "Iltimos, o'zingizning raqamingizni «📱 Raqamni yuborish» tugmasi orqali yuboring."
        )
        return ASK_PHONE

    branch = context.user_data.get("reg_branch")
    name = context.user_data.get("reg_name")
    if branch not in BRANCHES or not name:
        await update.message.reply_text(
            "Nimadir chalkashib ketdi, /start ni qayta bosing.", reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END

    try:
        result = await asyncio.to_thread(
            register_customer, user.id, user.username or "", name, contact.phone_number, branch
        )
    except Exception:  # noqa: BLE001
        logger.exception("Mijozni yozishda xato")
        await update.message.reply_text(TECH_ERROR_TEXT, reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    context.user_data.pop("reg_branch", None)
    context.user_data.pop("reg_name", None)

    if result.get("status") == "exists":
        await update.message.reply_text(
            "Bu raqam bazamizda allaqachon bor ✅ Qayta ro'yxatdan o'tish shart emas.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END

    await update.message.reply_text(
        f"🎉 Tabriklaymiz, {name}! Siz Namcity Games ({BRANCHES[branch]}) doimiy "
        "mijozlari bazasiga qo'shildingiz.\n\n"
        "Aksiyalar va sovrinli o'yinlar haqida shu yerda xabar beramiz. Ko'rishguncha! 🎮",
        reply_markup=ReplyKeyboardRemove(),
    )

    customer = result.get("customer") or {}
    alert = (
        "🆕 <b>Yangi mijoz</b>\n"
        f"👤 {html.escape(name)}\n"
        f"📞 {html.escape(customer.get('phone', ''))}\n"
        f"📍 {html.escape(BRANCHES[branch])}"
        + (f"\n💬 @{html.escape(user.username)}" if user.username else "")
    )
    for admin_id in _allowed_ids():
        try:
            await context.bot.send_message(chat_id=admin_id, text=alert, parse_mode=ParseMode.HTML)
        except Exception:  # noqa: BLE001
            logger.exception("Adminga yangi mijoz xabarini yuborib bo'lmadi")
    return ConversationHandler.END


async def cancel_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("reg_branch", None)
    context.user_data.pop("reg_name", None)
    await update.message.reply_text(
        "Ro'yxatdan o'tish bekor qilindi. Qayta boshlash uchun /start ni bosing.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


def _parse_branch_and_count(args: list[str]):
    """'/random chorsu 3' -> ('chorsu', 3). Xato bo'lsa ValueError."""
    branch, count = None, 1
    for arg in args:
        a = arg.strip().lower()
        if a in BRANCH_ALIASES:
            branch = BRANCH_ALIASES[a]
        elif a.isdigit():
            count = int(a)
        else:
            raise ValueError(a)
    return branch, count


async def mijozlar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_chat.id):
        await _deny(update, context)
        return
    try:
        customers = await asyncio.to_thread(list_customers)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Mijozlar ro'yxatini o'qishda xato")
        await _reply_error(update, f"Mijozlar bazasini o'qib bo'lmadi: {exc}")
        return
    today = datetime.now(TZ).date().isoformat()
    lines = ["👥 <b>Mijozlar bazasi</b>", ""]
    for key, name in BRANCHES.items():
        branch_list = [c for c in customers if c.get("branch_key") == key]
        new_today = sum(1 for c in branch_list if str(c.get("registered", "")).startswith(today))
        lines.append(f"📍 {name}: <b>{len(branch_list)}</b> ta (bugun +{new_today})")
    lines += ["", f"Jami: <b>{len(customers)}</b> ta mijoz"]
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def random_winner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_chat.id):
        await _deny(update, context)
        return
    try:
        branch, count = _parse_branch_and_count(context.args)
    except ValueError:
        await _reply_error(
            update, "Format: /random [chorsu|yangibozor] [soni]\nMasalan: /random chorsu 3"
        )
        return
    if not 1 <= count <= 50:
        await _reply_error(update, "G'oliblar soni 1 dan 50 gacha bo'lishi kerak.")
        return
    try:
        pool = await asyncio.to_thread(list_customers, branch)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Sovrinli o'yin uchun bazani o'qishda xato")
        await _reply_error(update, f"Mijozlar bazasini o'qib bo'lmadi: {exc}")
        return
    if not pool:
        await _reply_error(update, "Bazada hali mijoz yo'q.")
        return
    count = min(count, len(pool))
    winners = random.SystemRandom().sample(pool, count)
    where = BRANCHES[branch] if branch else "barcha filiallar"
    lines = [
        "🎁 <b>Sovrinli o'yin natijasi</b>",
        f"📍 {html.escape(where)} — {len(pool)} ta ishtirokchi orasidan",
        f"🕒 {datetime.now(TZ).strftime('%Y-%m-%d %H:%M')}",
        "",
    ]
    for i, w in enumerate(winners, start=1):
        extra = f" ({html.escape(w.get('branch', ''))})" if not branch else ""
        lines.append(
            f"{i}. 🏆 <b>{html.escape(w.get('name', ''))}</b> — "
            f"{html.escape(w.get('phone', ''))}{extra}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


def main() -> None:
    token = os.environ["BOT_TOKEN"]
    application = Application.builder().token(token).build()

    registration = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_BRANCH: [
                CallbackQueryHandler(branch_chosen, pattern=r"^branch:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, branch_reminder),
            ],
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, name_received)],
            ASK_PHONE: [
                MessageHandler(filters.CONTACT, contact_received),
                MessageHandler(filters.TEXT & ~filters.COMMAND, phone_reminder),
            ],
        },
        fallbacks=[CommandHandler("bekor", cancel_registration)],
        allow_reentry=True,
    )
    application.add_handler(registration)
    application.add_handler(CommandHandler("id", my_id))
    application.add_handler(CommandHandler("mijozlar", mijozlar))
    application.add_handler(CommandHandler("random", random_winner))
    application.add_handler(CommandHandler("yordam", yordam))
    application.add_handler(CommandHandler("help", yordam))
    application.add_handler(CommandHandler("bugun", bugun))
    application.add_handler(CommandHandler("sana", sana))
    application.add_handler(CommandHandler("oraliq", oraliq))
    application.add_handler(CommandHandler("tarix", tarix))

    if application.job_queue is not None:
        report_time = datetime.now(TZ).replace(
            hour=REPORT_HOUR, minute=REPORT_MINUTE, second=0, microsecond=0
        ).timetz()
        application.job_queue.run_daily(daily_report_job, time=report_time, name="daily_report")
        logger.info("Kunlik hisobot vaqti: %02d:%02d (%s)", REPORT_HOUR, REPORT_MINUTE, TZ)

        application.job_queue.run_repeating(
            check_changes_job,
            interval=CHECK_INTERVAL_MINUTES * 60,
            first=30,
            name="check_changes",
        )
        logger.info("O'zgarishlarni tekshirish oralig'i: %d daqiqa", CHECK_INTERVAL_MINUTES)
    else:
        logger.warning(
            "JobQueue mavjud emas — 'pip install \"python-telegram-bot[job-queue]\"' o'rnating"
        )

    logger.info("Bot ishga tushdi (polling)")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
