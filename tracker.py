"""Jadvaldagi tranzaksiyalarga kiritilgan TAHRIRLASH va O'CHIRISHlarni aniqlash.

Bot har CHECK_INTERVAL_MINUTES daqiqada jadvalni qayta o'qiydi va uni oldingi
holat (snapshot) bilan qator raqami (row_no) bo'yicha solishtiradi:

  - Agar mavjud qatorning sanasi/bo'limi/kategoriyasi/kirim yoki chiqim summasi
    o'zgargan bo'lsa -> "tahrirlandi" (edited).
  - Agar mavjud qator butunlay yo'qolgan bo'lsa (o'chirilgan yoki bo'sh
    qilib tashlangan) -> "o'chirildi" (deleted).
  - Yangi qo'shilgan oddiy qatorlar (row_no ilgari umuman ko'rilmagan) —
    bu ODATIY holat, o'zgartirish deb hisoblanmaydi va xabar yubormaydi.

Izoh (COL_IZOH) va "kim kiritdi" (COL_KIM) ustunlaridagi o'zgarish yolg'iz
o'zi hisobga olinmaydi — bular hisob-kitobga ta'sir qilmaydi.
"""

TRACKED_FIELDS = ("sana", "bolim", "kategoriya", "kirim", "chiqim")


def snapshot_from_rows(rows: list) -> dict:
    """rows -> {row_no: row_dict}. Bir xil row_no takrorlansa oxirgisi qoladi."""
    return {r["row_no"]: r for r in rows}


def diff_snapshots(old_snapshot: dict, new_snapshot: dict) -> list:
    """Ikki snapshot orasidagi hisob-kitobga daxldor o'zgarishlarni topadi.

    Qaytaradi (row_no bo'yicha tartiblangan):
      [{"type": "edited", "row_no": int, "old": {...}, "new": {...}},
       {"type": "deleted", "row_no": int, "old": {...}, "new": None}, ...]
    """
    changes = []
    for row_no, old_row in old_snapshot.items():
        new_row = new_snapshot.get(row_no)
        if new_row is None:
            changes.append({"type": "deleted", "row_no": row_no, "old": old_row, "new": None})
            continue
        if any(old_row[f] != new_row[f] for f in TRACKED_FIELDS):
            changes.append({"type": "edited", "row_no": row_no, "old": old_row, "new": new_row})
    changes.sort(key=lambda c: c["row_no"])
    return changes
