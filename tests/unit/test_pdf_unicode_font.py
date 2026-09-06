"""Embedded glyphs and round-trip text, not only a PDF that opens successfully."""

from pathlib import Path

import pytest
from pypdf import PdfReader

from visa_agent.delivery.fonts import FONT_PATH, UNICODE_FONT, font_for_text
from visa_agent.delivery.pack import _document_index_pdf, _pdf


def embedded_font(reader):
    return any("/FontFile2" in font.get_object().get("/FontDescriptor", {})
               for page in reader.pages for font in page["/Resources"]["/Font"].values())


def test_ascii_keeps_original_font_and_needs_no_unicode_registration():
    assert font_for_text("Fictional Example, £135") == "Helvetica"
    assert font_for_text("Case summary", "Helvetica-Bold") == "Helvetica-Bold"
    assert FONT_PATH.is_file()
    assert (FONT_PATH.parent / "OFL-NotoSansSC.txt").is_file()


@pytest.mark.parametrize("text", ["陈示例（虚构）", "日本，2023年夏天", "香港九龍虛構示例道123號", "London 与伦敦"])
def test_chinese_name_period_and_address_survive_pdf_extraction_and_have_embedded_font(tmp_path, text):
    assert font_for_text(text) == UNICODE_FONT
    target = tmp_path / "fictional.pdf"
    _pdf(target, "Fictional record QA", [text], "FICTIONAL - NOT AN APPLICATION")
    reader = PdfReader(target)
    assert text in "".join(page.extract_text() for page in reader.pages)
    assert embedded_font(reader)


def test_unicode_document_name_is_preserved_in_document_index(tmp_path):
    target = tmp_path / "index.pdf"
    _document_index_pdf(target, ["学校在读证明.pdf - student_letter; ACCEPTED_FOR_REVIEW; SHA-256 "
                                 + "a" * 64 + "; language zh; pages 1"], "FICTIONAL")
    reader = PdfReader(target)
    assert "学校在读证明.pdf" in "".join(page.extract_text() for page in reader.pages)
    assert embedded_font(reader)


@pytest.mark.parametrize("text", ["unsupported\U0010ffff", "nul\x00value"])
def test_missing_glyph_or_control_is_rejected_instead_of_silent_black_squares(text):
    with pytest.raises(ValueError, match="code points|control characters"):
        font_for_text(text)


def test_same_unicode_pdf_is_byte_identical_on_repeated_generation(tmp_path):
    paths = [tmp_path / "one.pdf", tmp_path / "two.pdf"]
    for path in paths:
        _pdf(path, "Fictional record QA", ["陈示例（虚构）", "2023年夏天"], "FICTIONAL")
    assert Path(paths[0]).read_bytes() == Path(paths[1]).read_bytes()
