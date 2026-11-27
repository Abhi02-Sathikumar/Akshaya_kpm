"""File conversion helpers: PDF <-> JPG/PNG <-> DOCX. No Streamlit imports."""
import io
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image, ImageOps

try:  # optional: iPhone HEIC photos
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:
    pass

IMAGE_EXTS = {"jpg", "png", "webp", "bmp", "heic"}
SOURCE_EXTS = IMAGE_EXTS | {"pdf", "docx"}
TARGETS = ["PDF", "JPG", "PNG", "DOCX"]
MIME = {
    "pdf": "application/pdf",
    "jpg": "image/jpeg",
    "png": "image/png",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "zip": "application/zip",
}


def ext_of(name: str) -> str:
    e = Path(name).suffix.lower().lstrip(".")
    return "jpg" if e == "jpeg" else e


def is_image(name: str) -> bool:
    return ext_of(name) in IMAGE_EXTS


# ---------------------------------------------------------------- decoding
def decode_pages(name: str, data: bytes):
    """List of RGB PIL images: one per image file or per PDF page."""
    if ext_of(name) == "pdf":
        doc = fitz.open(stream=data, filetype="pdf")
        out = []
        for page in doc:
            pix = page.get_pixmap(dpi=150)
            out.append(Image.frombytes("RGB", [pix.width, pix.height], pix.samples))
        return out
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    im.thumbnail((2400, 2400))
    return [im]


# ------------------------------------------------------------ DOCX <-> PDF
def docx_to_pdf(data: bytes) -> bytes:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise RuntimeError(
            "DOCX conversion needs LibreOffice. On Streamlit Cloud add a "
            "packages.txt file containing: libreoffice-writer"
        )
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "input.docx"
        src.write_bytes(data)
        subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf", "--outdir", tmp, str(src)],
            check=True,
            timeout=180,
            capture_output=True,
            env={**os.environ, "HOME": tmp},
        )
        out = Path(tmp) / "input.pdf"
        if not out.exists():
            raise RuntimeError("LibreOffice could not convert this DOCX file")
        return out.read_bytes()


def pdf_to_docx(data: bytes) -> bytes:
    from pdf2docx import Converter  # imported lazily

    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "input.pdf"
        out = Path(tmp) / "output.docx"
        pdf.write_bytes(data)
        cv = Converter(str(pdf))
        try:
            cv.convert(str(out))
        finally:
            cv.close()
        return out.read_bytes()


# ---------------------------------------------------------- images <-> docs
def images_to_pdf(images) -> bytes:
    buf = io.BytesIO()
    first, *rest = images
    first.save(buf, "PDF", save_all=True, append_images=rest, resolution=150.0)
    return buf.getvalue()


def images_to_docx(images) -> bytes:
    from docx import Document
    from docx.shared import Inches

    doc = Document()
    for i, im in enumerate(images):
        b = io.BytesIO()
        im.save(b, "JPEG", quality=90)
        b.seek(0)
        w = 6.5
        h = w * im.height / im.width
        if h > 9:  # keep tall pages on one sheet
            doc.add_picture(b, height=Inches(9))
        else:
            doc.add_picture(b, width=Inches(w))
        if i < len(images) - 1:
            doc.add_page_break()
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def images_to_files(images, stem: str, target: str, quality: int = 85):
    """One JPG/PNG per page. Returns [(filename, bytes, mime)]."""
    t = target.lower()
    results = []
    for i, im in enumerate(images, 1):
        b = io.BytesIO()
        if t == "jpg":
            im.convert("RGB").save(b, "JPEG", quality=quality)
        else:
            im.save(b, "PNG")
        name = f"{stem}.{t}" if len(images) == 1 else f"{stem}_{i}.{t}"
        results.append((name, b.getvalue(), MIME[t]))
    return results


# -------------------------------------------------------------- main entry
def convert_one(name: str, data: bytes, target: str, quality: int = 85):
    """Convert one file to `target`. Returns [(filename, bytes, mime)]."""
    stem, src, t = Path(name).stem, ext_of(name), target.lower()
    if src not in SOURCE_EXTS:
        raise ValueError(f"Unsupported file type: .{src}")
    if src == t:
        raise ValueError(f"Already a {target} file")

    if src == "docx":
        pdf = docx_to_pdf(data)
        if t == "pdf":
            return [(f"{stem}.pdf", pdf, MIME["pdf"])]
        return images_to_files(decode_pages(f"{stem}.pdf", pdf), stem, t, quality)

    if src == "pdf" and t == "docx":
        return [(f"{stem}.docx", pdf_to_docx(data), MIME["docx"])]

    pages = decode_pages(name, data)
    if t == "pdf":
        return [(f"{stem}.pdf", images_to_pdf(pages), MIME["pdf"])]
    if t == "docx":
        return [(f"{stem}.docx", images_to_docx(pages), MIME["docx"])]
    return images_to_files(pages, stem, t, quality)


def merge_images(named_files, base: str, target: str):
    """Merge several image files into ONE pdf/docx."""
    pages = []
    for name, data in named_files:
        pages += decode_pages(name, data)
    t = target.lower()
    if t == "pdf":
        return [(f"{base}.pdf", images_to_pdf(pages), MIME["pdf"])]
    return [(f"{base}.docx", images_to_docx(pages), MIME["docx"])]


def zip_results(results) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data, _ in results:
            z.writestr(name, data)
    return buf.getvalue()
