"""Hisobot xabarlarini chiroyli matn qilib formatlash.

Klub va Bar hisoblari HAR DOIM alohida-alohida ko'rsatiladi va hech qachon
qo'shib yuborilmaydi (ular ikki xil egaga tegishli).
"""

UNIT_ORDER = ["Klub", "Bar"]
UNIT_EMOJI = {"Klub": "🏢", "Bar": "🍹"}


def _fmt_money(v: float) -> str:
    return f"{v:,.0f}".replace(",", " ")


def _format_unit_block(unit_name: str, summary: dict) -> str:
    emoji = UNIT_EMOJI.get(unit_name, "•")
    lines = [f"{emoji} *{unit_name}*"]

    kirim_cats = sorted(
        [(k[1], v) for k, v in summary["by_category"].items() if k[0].lower() == "kirim"],
        key=lambda x: -x[1],
    )
    chiqim_cats = sorted(
        [(k[1], v) for k, v in summary["by_category"].items() if k[0].lower() == "chiqim"],
        key=lambda x: -x[1],
    )

    if not summary["rows"]:
        lines.append("_Bu davrda tranzaksiya yo'q._")
        return "\n".join(lines)

    if kirim_cats:
        lines.append("🟢 Kirim:")
        for cat, val in kirim_cats:
            lines.append(f"  • {cat}: {_fmt_money(val)} so'm")
    lines.append(f"🟢 Jami kirim: *{_fmt_money(summary['kirim_total'])} so'm*")

    if chiqim_cats:
        lines.append("🔴 Chiqim:")
        for cat, val in chiqim_cats:
            lines.append(f"  • {cat}: {_fmt_money(val)} so'm")
    lines.append(f"🔴 Jami chiqim: *{_fmt_money(summary['chiqim_total'])} so'm*")

    net = summary["net"]
    belgi = "📈" if net >= 0 else "📉"
    lines.append(f"{belgi} Sof foyda: *{_fmt_money(net)} so'm*")

    return "\n".join(lines)


def format_report(summaries_by_unit: dict, title: str, start_date, end_date) -> str:
    """summaries_by_unit: {"Klub": summary, "Bar": summary} (bittasi bo'lishi ham mumkin)."""
    if start_date == end_date:
        sana_qatori = f"📅 Sana: {start_date.strftime('%Y-%m-%d')}"
    else:
        sana_qatori = f"📅 Oraliq: {start_date.strftime('%Y-%m-%d')} — {end_date.strftime('%Y-%m-%d')}"

    blocks = [f"*{title}*", sana_qatori, ""]
    unit_texts = []
    for unit in UNIT_ORDER:
        if unit in summaries_by_unit:
            unit_texts.append(_format_unit_block(unit, summaries_by_unit[unit]))
    blocks.append("\n\n".join(unit_texts))

    if len(summaries_by_unit) > 1:
        blocks.append("")
        blocks.append("_Klub va Bar hisoblari ataylab alohida ko'rsatilgan — qo'shilmaydi._")

    return "\n".join(blocks)


def _fmt_row_summary(row: dict) -> str:
    emoji = UNIT_EMOJI.get(row["bolim"], "•")
    parts = [f"📅 {row['sana'].strftime('%Y-%m-%d')}", f"{emoji} {row['bolim']}", f"📁 {row['kategoriya']}"]
    if row["kirim"]:
        parts.append(f"🟢 {_fmt_money(row['kirim'])} so'm")
    if row["chiqim"]:
        parts.append(f"🔴 {_fmt_money(row['chiqim'])} so'm")
    if row.get("kim"):
        parts.append(f"👤 {row['kim']}")
    return " | ".join(parts)


def _format_single_change(change: dict) -> str:
    row_no = change["row_no"]
    if change["type"] == "deleted":
        old = change["old"]
        return f"🗑 *O'chirildi* (jadvalda {row_no}-qator)\n   {_fmt_row_summary(old)}"

    old, new = change["old"], change["new"]
    lines = [f"✏️ *Tahrirlandi* (jadvalda {row_no}-qator)"]
    if old["sana"] != new["sana"]:
        lines.append(f"   📅 Sana: {old['sana'].strftime('%Y-%m-%d')} → {new['sana'].strftime('%Y-%m-%d')}")
    if old["bolim"] != new["bolim"]:
        lines.append(f"   {UNIT_EMOJI.get(old['bolim'], '•')} Bo'lim: {old['bolim']} → {new['bolim']}")
    if old["kategoriya"] != new["kategoriya"]:
        lines.append(f"   📁 Kategoriya: {old['kategoriya']} → {new['kategoriya']}")
    if old["kirim"] != new["kirim"]:
        lines.append(f"   🟢 Kirim: {_fmt_money(old['kirim'])} → *{_fmt_money(new['kirim'])} so'm*")
    if old["chiqim"] != new["chiqim"]:
        lines.append(f"   🔴 Chiqim: {_fmt_money(old['chiqim'])} → *{_fmt_money(new['chiqim'])} so'm*")
    kim = new.get("kim") or old.get("kim")
    if kim:
        lines.append(f"   👤 {kim}")
    return "\n".join(lines)


def format_change_alert(changes: list) -> str:
    """Aniqlangan o'zgarishlar (tahrir/o'chirish) haqida Telegram xabari."""
    shown = changes[:20]
    blocks = [_format_single_change(c) for c in shown]
    text = "⚠️ *Jadvalda o'zgartirish aniqlandi!*\n\n" + "\n\n".join(blocks)
    if len(changes) > len(shown):
        text += f"\n\n… yana {len(changes) - len(shown)} ta o'zgarish."
    text += (
        "\n\n_Eslatma: /bugun, /sana, /oraliq hisobotlari doim jadvalning "
        "joriy holatidan hisoblanadi — tuzatilgan summa avtomatik to'g'ri "
        "ko'rsatiladi, eski xato hisobga qo'shilib qolmaydi._"
    )
    return text


def format_history(history: list, limit: int = 15) -> str:
    """Oxirgi aniqlangan o'zgarishlar ro'yxati (/tarix buyrug'i uchun)."""
    if not history:
        return "🗂 Hozircha hech qanday o'zgartirish qayd etilmagan."
    shown = history[:limit]
    blocks = []
    for entry in shown:
        ts = entry["detected_at"].strftime("%Y-%m-%d %H:%M")
        blocks.append(f"🕒 {ts}\n{_format_single_change(entry['change'])}")
    text = "🗂 *Oxirgi o'zgartirishlar*\n\n" + "\n\n".join(blocks)
    if len(history) > len(shown):
        text += f"\n\n… jami {len(history)} ta yozuv (oxirgi {limit} tasi ko'rsatildi)."
    return text
