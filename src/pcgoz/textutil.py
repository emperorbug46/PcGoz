"""Metin yardimcilari -- her katman kullaniyor, hicbir seye bagimli degil."""

from __future__ import annotations

import re
import unicodedata

#: Gorunmez yon ve bicim isaretleri (LRM/RLM, gommeler, yalitimlar, sifir
#: genislikli bosluk, BOM). Dosya Gezgini tarih sutununu boyle veriyor:
#: "‎24.‎09.‎2026 ‏‎14:42" -- modele gereksiz karakter gidiyor, "24.09"
#: aramasi da eslesmiyordu (gercek gorev turu, 2026-09-24).
_INVISIBLE = re.compile("[​‎‏‪-‮⁦-⁩﻿]")

#: Qt, adi olmayan ogeye ad olarak nesne yolunu veriyor:
#: "QApplication.MainWindow.QWebEngineView.QObject.viewer.pages.QObject..." --
#: Bir Qt PDF goruntuleyicide ciktinin yarisi buydu. Bu bir ad degil, gurultu.
_QT_OBJECT_PATH = re.compile(r"^(?:QApplication|QObject|QWidgetWindow)(?:\.\w+)+$")


def clean(text: str) -> str:
    """UIA'dan gelen ad/deger: gorunmez isaretler atilir, Qt nesne yolu bos sayilir."""
    text = _INVISIBLE.sub("", text).strip()
    return "" if _QT_OBJECT_PATH.match(text) else text


def one_line(text: str) -> str:
    """Satir sonlarini gorunur yap (\\n). Ciktilar satir satir okunuyor: cok
    satirli bir deger agaci ya da ozeti boler, devami baska bir oge sanilir."""
    return text.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")


def fold(text: str) -> str:
    """Arama icin: aksansiz, buyuk/kucuk harf duyarsiz.

    casefold Turkcede yetmiyor: "İptal".casefold() noktali "i̇ptal" veriyor,
    yani pc_find("iptal") "İptal" dugmesini bulamiyordu; "KAYIT" de "kayıt" ile
    eslesmiyordu (olculdu, ikisi de False). OCR da aksanlari karistirabiliyor
    (s/ş, i/ı). Hepsi ayni yere indiriliyor.
    """
    text = text.replace("ı", "i").replace("İ", "i").replace("I", "i")
    stripped = "".join(c for c in unicodedata.normalize("NFKD", text)
                       if not unicodedata.combining(c))
    return stripped.casefold()
