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
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from sheets import fetch_rows, summarize_by_unit, UNITS
from formatting import format_report, format_change_alert, format_history
from tracker import snapshot_from_rows, diff_snapshots

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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        "🔎 Kimdir jadvalda avval kiritilgan summani/sanani o'zgartirsa — ZUDLIK BILAN "
        "xabar keladi (Google Sheets skripti orqali). Butun qator o'chirilsa esa "
        f"eng ko'pi bilan {CHECK_INTERVAL_MINUTES} daqiqa ichida xabar beradi."
    )


async def yordam(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_chat.id):
        await start(update, context)
        return
    await update.message.reply_text(
        "📖 *Buyruqlar*\n\n"
        "/bugun [klub|bar] — bugungi hisobot\n"
        "/sana YYYY-MM-DD [klub|bar] — masalan: /sana 2026-08-15 bar\n"
        "/oraliq YYYY-MM-DD YYYY-MM-DD [klub|bar] — masalan: /oraliq 2026-08-01 2026-08-15\n"
        "/tarix — jadvalda so'nggi qilingan tahrir/o'chirishlar ro'yxati\n\n"
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
        await start(update, context)
        return
    unit, _rest = _extract_unit(context.args)
    today = datetime.now(TZ).date()
    await _reply_report(update, today, today, unit, "Bugungi hisobot")


async def sana(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_chat.id):
        await start(update, context)
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
        await start(update, context)
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
        await start(update, context)
        return
    history = context.bot_data.get("history", [])
    await update.message.reply_text(format_history(history), parse_mode=ParseMode.MARKDOWN)


def main() -> None:
    token = os.environ["BOT_TOKEN"]
    application = Application.builder().token(token).build()

    application.add_handler(CommandHandler("start", start))
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
