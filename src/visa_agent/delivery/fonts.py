"""Portable embedded Unicode font selection; never silently emit missing glyphs."""

from functools import lru_cache
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

UNICODE_FONT = "VisaNotoSansSC"
FONT_PATH = Path(__file__).resolve().parents[1] / "assets" / "fonts" / "NotoSansSC-Regular.ttf"


@lru_cache(maxsize=1)
def _unicode_codepoints() -> frozenset[int]:
    font = TTFont(UNICODE_FONT, str(FONT_PATH))
    pdfmetrics.registerFont(font)
    # One regular face is sufficient for data rows. Bold table headings remain
    # Helvetica; inline emphasis must not fall back to an unrelated Latin font.
    pdfmetrics.registerFontFamily(UNICODE_FONT, normal=UNICODE_FONT, bold=UNICODE_FONT,
                                  italic=UNICODE_FONT, boldItalic=UNICODE_FONT)
    return frozenset(font.face.charToGlyph)


def font_for_text(text: str, base_font: str = "Helvetica") -> str:
    controls = {ord(char) for char in text if ord(char) < 32 and char not in "\n\r\t"}
    if controls:
        raise ValueError("PDF text contains unsupported control characters")
    try:
        text.encode("cp1252")
        return base_font
    except UnicodeEncodeError:
        pass
    missing = {ord(char) for char in text if char not in "\n\r\t"} - _unicode_codepoints()
    if missing:
        codes = ", ".join(f"U+{point:04X}" for point in sorted(missing))
        raise ValueError(f"Bundled PDF font cannot render these code points: {codes}")
    return UNICODE_FONT
