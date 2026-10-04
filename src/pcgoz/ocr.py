"""Windows'un yerlesik OCR'i (Windows.Media.Ocr) -- UIA vermeyen arayuzler icin.

Oyunlar, Qt, canvas, uzak masaustu, resim ve PDF icindeki yazi erisilebilirlik
agacinda gorunmez; tek kaynak ekranin kendisi. Hata yapar: "79/79" -> "79179",
"6/6" -> "616" -- UIA varsa once o. Kucuk yazi buyutulerek okunur (auto_scale).

winrt paketleri sadece OCR istenince yuklenir: bozulurlarsa sunucu yine
acilir, sadece bu arac hata verir.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from PIL import Image

from .textutil import fold

Rect = tuple[int, int, int, int]        # left, top, right, bottom (goruntu pikseli)


@dataclass
class Word:
    text: str
    rect: Rect


@dataclass
class Line:
    text: str
    words: list[Word]

    @property
    def rect(self) -> Rect:
        return _union(w.rect for w in self.words)


def _union(rects) -> Rect:  # noqa: ANN001
    rects = list(rects)
    return (min(r[0] for r in rects), min(r[1] for r in rects),
            max(r[2] for r in rects), max(r[3] for r in rects))


#: Buyutmenin ust siniri (buyutulmus goruntunun piksel sayisi). Olculdu: 1920x1080
#: tam ekran 1x ~100-175 ms, 2x ~200-300 ms, 3x ~450-635 ms (3x = 18.7 MP).
PIXEL_BUDGET = 20_000_000

#: Buyutme yontemi. LANCZOS DEGIL: kenarlikli kutularda (dugme, metin kutusu)
#: halka artiklari yaziyi kenarliga karistiriyor -- gercek bir Win32 penceresinde
#: dugme yazilari 2x ve 3x'te TAMAMEN kayboldu. Duz zeminli sentetik olcum bunu
#: gostermiyordu (tests/ocr_olcum.py, "Gercek pencere" tablosu).
RESAMPLE = Image.BICUBIC

#: Okuma olcegi. Olculdu (tests/ocr_olcum.py): gercek pencerede 8 bilinen
#: metinden (11/13/16 px) bulunan 1x 4/4/5, 2x 5/6/5, 3x 6/3/3 -- 3x 13-16 px
#: arayuz yazisini bozuyor; sentetik sayfada karakter hata orani 1x %31.9,
#: 2x %15.7. 1x kucuk yaziyi neredeyse hic okumuyordu ("79/79", "v0.4.0" bos).
#:
#: Denenip birakilan: iki olcekte okuyup farkli cikan satiri "belirsiz"
#: isaretlemek. Sentetik sayfada iyi gorundu (0/37 yanlis alarm), gercek
#: pencerede dogru satirlarin 11-13/37'sini isaretledi -- ucte biri yanlis alarm
#: olan bir isaret modele gurultuden baska bir sey vermez; ustelik sureyi
#: ikiye katliyordu.
SCALE = 2


def _fits(scale: int, width: int, height: int, limit: int) -> bool:
    return scale * max(width, height) <= limit and scale * scale * width * height <= PIXEL_BUDGET


def auto_scale(width: int, height: int, limit: int = 10000) -> int:
    """Okuma olcegi: SCALE sigiyorsa o, yoksa 1 (4K tam ekran piksel butcesini asar)."""
    return SCALE if _fits(SCALE, width, height, limit) else 1


def _engine():  # noqa: ANN202
    from winrt.windows.media.ocr import OcrEngine

    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        available = list(OcrEngine.available_recognizer_languages)
        if not available:
            raise RuntimeError(
                "Windows'ta kurulu OCR dili yok. Ayarlar > Zaman ve dil > Dil ve bolge'den "
                "bir dilin 'Optik karakter tanima' ozelligi kurulmali."
            )
        engine = OcrEngine.try_create_from_language(available[0])
    return engine, int(OcrEngine.max_image_dimension)


def recognize(image: Image.Image, *, scale: float | None = None) -> tuple[list[Line], str, float]:
    """Goruntudeki satirlar, kullanilan dil ve okuma olcegi. Koordinatlar goruntu pikseli.

    scale bos ise auto_scale; motorun boyut sinirini asan goruntu kucultulur.
    """
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.storage.streams import DataWriter

    engine, limit = _engine()
    factor = scale if scale is not None else auto_scale(image.width, image.height, limit)
    factor = min(float(factor), limit / max(image.width, image.height))
    if abs(factor - 1.0) > 1e-6:
        image = image.resize((max(1, round(image.width * factor)), max(1, round(image.height * factor))),
                             RESAMPLE)

    writer = DataWriter()
    writer.write_bytes(image.convert("RGBA").tobytes("raw", "BGRA"))
    bitmap = SoftwareBitmap.create_copy_from_buffer(
        writer.detach_buffer(), BitmapPixelFormat.BGRA8, image.width, image.height)

    async def run():  # noqa: ANN202
        return await engine.recognize_async(bitmap)

    # Kendi olay dongusunde: cagiran iplik (MCP'nin is parcacigi) async degil.
    result = asyncio.run(run())
    lines = []
    for line in result.lines:
        words = []
        for word in line.words:
            box = word.bounding_rect
            words.append(Word(word.text, (round(box.x / factor), round(box.y / factor),
                                          round((box.x + box.width) / factor),
                                          round((box.y + box.height) / factor))))
        if words:
            lines.append(Line(line.text, words))
    return lines, engine.recognizer_language.language_tag, factor


def find(lines: list[Line], needle: str) -> list[tuple[str, Rect]]:
    """Metni iceren en kisa kelime araliklari ve kutulari.

    Bir dugmeye tiklamak icin satirin degil, eslesen kelimelerin kutusu
    gerekir: "Dosya Duzen Kaydet" satirinda "kaydet" aranirsa sadece
    "Kaydet"in kutusu doner.
    """
    target = fold(needle).strip()
    if not target:
        return []
    hits = []
    for line in lines:
        if target not in fold(line.text) and target not in fold(" ".join(w.text for w in line.words)):
            continue
        words = line.words
        spans = []
        for start in range(len(words)):
            joined = ""
            for end in range(start, len(words)):
                joined = f"{joined} {words[end].text}".strip()
                if target in fold(joined):
                    spans.append((start, end))
                    break
        # Icinde daha kisa bir eslesme barindiran araligi at.
        spans = [s for s in spans
                 if not any(o != s and o[0] >= s[0] and o[1] <= s[1] for o in spans)]
        for start, end in spans:
            chosen = words[start:end + 1]
            hits.append((" ".join(w.text for w in chosen), _union(w.rect for w in chosen)))
    return hits
