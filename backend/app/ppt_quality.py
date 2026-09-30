"""Structural and rendered-page gate for generated PPTX files."""

import io
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageChops
from pptx import Presentation


class PptQualityError(ValueError):
    def __init__(self, message: str, page_index: int | None = None):
        super().__init__(message)
        self.page_index = page_index


def inspect_pptx(content: bytes, expected_pages: int, *, render: bool = True) -> None:
    deck = Presentation(io.BytesIO(content))
    if len(deck.slides) != expected_pages:
        raise ValueError("PPT page count changed after rendering")
    for index, slide in enumerate(deck.slides):
        if not any(shape.has_text_frame and shape.text.strip() for shape in slide.shapes):
            raise PptQualityError("PPT contains a blank page", index)
        for shape in slide.shapes:
            if shape.left < 0 or shape.top < 0 or shape.left + shape.width > deck.slide_width or shape.top + shape.height > deck.slide_height:
                raise PptQualityError("PPT contains an out-of-bounds shape", index)
    if not render:
        return
    with tempfile.TemporaryDirectory(prefix="ppt-review-") as directory:
        root = Path(directory)
        source = root / "deck.pptx"
        source.write_bytes(content)
        subprocess.run(
            ["soffice", "-env:UserInstallation=file://" + (root / "profile").as_posix(),
             "--headless", "--convert-to", "pdf", "--outdir", str(root), str(source)],
            check=True, timeout=45, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        pdf = root / "deck.pdf"
        if not pdf.is_file():
            raise ValueError("PPT preview could not be rendered")
        subprocess.run(
            ["pdftoppm", "-f", "1", "-l", str(expected_pages), "-scale-to", "960", "-png", str(pdf), str(root / "page")],
            check=True, timeout=45, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        pages = sorted(root.glob("page-*.png"))
        if len(pages) != expected_pages:
            raise ValueError("PPT preview page count is incorrect")
        for index, page in enumerate(pages):
            with Image.open(page) as image:
                rgb = image.convert("RGB")
                background = Image.new("RGB", rgb.size, rgb.getpixel((0, 0)))
                if ImageChops.difference(rgb, background).getbbox() is None:
                    raise PptQualityError("PPT preview contains a blank page", index)
