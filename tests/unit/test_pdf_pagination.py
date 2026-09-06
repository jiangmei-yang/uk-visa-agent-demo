import pytest
from pypdf import PdfReader

from visa_agent.delivery.pack import _pdf


@pytest.mark.parametrize("count", [24, 25, 26, 50])
def test_table_does_not_emit_a_footer_only_trailing_page(tmp_path, count):
    target = tmp_path / "pagination.pdf"
    rows = [f"Fictional row {number:03d}" for number in range(count)]
    _pdf(target, "Sponsor output states", rows, "FICTIONAL QA - NOT A CUSTOMER PACK", compact=True)
    pages = PdfReader(target).pages
    texts = [page.extract_text() for page in pages]
    assert all("Fictional row" in text for text in texts)
    assert all(sum(row in text for text in texts) == 1 for row in rows)
    if count == 25:
        assert len(pages) == 1
