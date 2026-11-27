"""Office Hub - Streamlit app
Pages: Scan & Camera (-> PDF/JPG), File Converter (PDF/JPG/PNG/DOCX),
Shared Files. Protected by a passcode.
"""
import hmac
import io
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st
from PIL import Image, ImageEnhance, ImageOps

import converters as cv

st.set_page_config(page_title="Office Hub", page_icon="🗂️", layout="wide")

# ----------------------------------------------------------------- config
try:
    PASSCODE = str(st.secrets["PASSCODE"])
except Exception:
    PASSCODE = "tvm044"  # fallback; better: set PASSCODE in Streamlit secrets

TZ = ZoneInfo("Asia/Kolkata")
BASE = Path(__file__).parent / "storage"
SHARED_DIR = BASE / "shared"
DB_PATH = BASE / "hub.db"
SHARED_DIR.mkdir(parents=True, exist_ok=True)

UPLOAD_TYPES = ["pdf", "docx", "jpg", "jpeg", "png", "webp", "bmp", "heic"]


# --------------------------------------------------------------- database
def db(query, args=(), fetch=False):
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        cur = conn.execute(query, args)
        rows = cur.fetchall() if fetch else None
        conn.commit()
        return rows
    finally:
        conn.close()


@st.cache_resource
def init_db():
    db(
        """CREATE TABLE IF NOT EXISTS files (
        id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT, path TEXT,
        uploader TEXT, size INTEGER, created TEXT)"""
    )
    return True


init_db()


def now():
    return datetime.now(TZ).strftime("%d %b %Y, %H:%M")


def human(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def save_shared(name: str, data: bytes):
    path = SHARED_DIR / f"{uuid.uuid4().hex}{Path(name).suffix.lower()}"
    path.write_bytes(data)
    db(
        "INSERT INTO files (filename, path, uploader, size, created) VALUES (?,?,?,?,?)",
        (name, str(path), (st.session_state.get("user_name") or "").strip() or "Unknown",
         len(data), now()),
    )


@st.cache_data(show_spinner=False, max_entries=8)
def decode(name: str, data: bytes):
    return cv.decode_pages(name, data)


# ------------------------------------------------------------------- auth
def gate() -> bool:
    if st.session_state.get("auth"):
        return True
    st.title("🔒 Office Hub")
    with st.form("login"):
        code = st.text_input("Passcode", type="password")
        if st.form_submit_button("Enter", type="primary"):
            if hmac.compare_digest(code.encode(), PASSCODE.encode()):
                st.session_state.auth = True
                st.rerun()
            else:
                st.error("Wrong passcode")
    return False


# ------------------------------------------------------- page: scan/camera
def apply_filter(im, mode, rotate):
    if rotate:
        im = im.rotate(-rotate, expand=True)
    if mode == "Auto-enhance":
        im = ImageOps.autocontrast(im, cutoff=1)
        im = ImageEnhance.Sharpness(im).enhance(1.6)
    elif mode == "Grayscale":
        im = ImageOps.grayscale(im).convert("RGB")
    elif mode == "Black & white (document)":
        g = ImageOps.autocontrast(ImageOps.grayscale(im))
        im = g.point(lambda p: 255 if p > 140 else 0).convert("RGB")
    return im


def scanner_page():
    st.title("📷 Scan & Camera")
    st.caption("Take photos or upload images/PDFs, clean them up, and export as one PDF or JPGs.")

    st.session_state.setdefault("scans", [])
    st.session_state.setdefault("cam_key", 0)

    tab_cam, tab_up = st.tabs(["📸 Take photo", "📁 Upload files"])
    with tab_up:
        uploads = st.file_uploader(
            "Images or PDFs (from phone or laptop)",
            type=["pdf", "jpg", "jpeg", "png", "webp", "bmp", "heic"],
            accept_multiple_files=True,
            key="scan_uploads",
        )
    with tab_cam:
        shot = st.camera_input("Camera", key=f"cam_{st.session_state.cam_key}")
        c1, c2 = st.columns(2)
        if shot and c1.button("➕ Add this photo to batch", use_container_width=True):
            st.session_state.scans.append(shot.getvalue())
            st.session_state.cam_key += 1
            st.rerun()
        if st.session_state.scans and c2.button("🗑️ Clear camera photos", use_container_width=True):
            st.session_state.scans = []
            st.rerun()
        if st.session_state.scans:
            st.info(f"{len(st.session_state.scans)} camera photo(s) in batch")

    pages = []
    for f in uploads or []:
        try:
            pages += decode(f.name, f.getvalue())
        except Exception as e:
            st.error(f"Could not read {f.name}: {e}")
    for i, b in enumerate(st.session_state.scans):
        pages += decode(f"camera_{i}.jpg", b)

    if not pages:
        st.info("Add some files or photos to get started.")
        return

    st.subheader(f"{len(pages)} page(s)")
    o1, o2, o3 = st.columns(3)
    mode = o1.selectbox("Filter", ["Original", "Auto-enhance", "Grayscale", "Black & white (document)"])
    rotate = o2.selectbox("Rotate", [0, 90, 180, 270], format_func=lambda x: f"{x}°")
    fmt = o3.radio("Convert to", ["PDF", "JPG"], horizontal=True)

    n1, n2 = st.columns(2)
    base = n1.text_input("File name", value=f"scan_{datetime.now(TZ):%Y%m%d_%H%M}")
    quality = n2.slider("JPG quality", 50, 100, 85)

    processed = [apply_filter(p, mode, rotate) for p in pages]
    cols = st.columns(min(len(processed), 4))
    for i, im in enumerate(processed):
        cols[i % len(cols)].image(im, caption=f"Page {i + 1}", use_container_width=True)

    if st.button("⚙️ Convert", type="primary"):
        base = base.strip() or "scan"
        if fmt == "PDF":
            results = [(f"{base}.pdf", cv.images_to_pdf(processed), cv.MIME["pdf"])]
        else:
            results = cv.images_to_files(processed, base, "JPG", quality)
        if len(results) > 1:
            results = [(f"{base}_jpgs.zip", cv.zip_results(results), cv.MIME["zip"])]
        st.session_state.scan_result = results[0]

    res = st.session_state.get("scan_result")
    if res:
        name, data, mime = res
        st.success(f"Ready: {name} ({human(len(data))})")
        d1, d2 = st.columns(2)
        d1.download_button("⬇️ Download", data, file_name=name, mime=mime, use_container_width=True)
        if d2.button("📤 Save to Shared Files", use_container_width=True, key="scan_share"):
            save_shared(name, data)
            st.success("Saved to Shared Files")


# ------------------------------------------------------- page: converter
def converter_page():
    st.title("🔄 File Converter")
    st.caption("Convert between PDF, JPG, PNG and DOCX.")

    files = st.file_uploader(
        "Upload files (PDF, DOCX, JPG, PNG, WEBP, HEIC...)",
        type=UPLOAD_TYPES,
        accept_multiple_files=True,
        key="conv_files",
    )
    c1, c2 = st.columns(2)
    target = c1.radio("Convert to", cv.TARGETS, horizontal=True, key="conv_target")
    quality = c2.slider("JPG quality", 50, 100, 85, key="conv_quality")

    merge, base = False, "merged"
    if target in ("PDF", "DOCX") and files and sum(cv.is_image(f.name) for f in files) > 1:
        merge = st.checkbox(f"Merge all images into one {target} file", value=True)
        if merge:
            base = st.text_input("Merged file name", value="merged")

    if not files:
        st.info("Upload one or more files to begin.")
        return

    if st.button("⚙️ Convert", type="primary", key="conv_go"):
        results, errors = [], []
        with st.spinner("Converting..."):
            todo = list(files)
            if merge:
                imgs = [(f.name, f.getvalue()) for f in todo if cv.is_image(f.name)]
                todo = [f for f in todo if not cv.is_image(f.name)]
                try:
                    results += cv.merge_images(imgs, base.strip() or "merged", target)
                except Exception as e:
                    errors.append(f"Merging images: {e}")
            for f in todo:
                try:
                    results += cv.convert_one(f.name, f.getvalue(), target, quality)
                except Exception as e:
                    errors.append(f"{f.name}: {e}")
        st.session_state.conv_results = results
        st.session_state.conv_errors = errors

    for err in st.session_state.get("conv_errors", []):
        st.error(err)

    results = st.session_state.get("conv_results", [])
    if results:
        st.success(f"{len(results)} file(s) ready")
        for i, (name, data, mime) in enumerate(results):
            a, b, c = st.columns([5, 2, 2])
            a.markdown(f"**{name}** · {human(len(data))}")
            b.download_button("⬇️ Download", data, file_name=name, mime=mime, key=f"cdl{i}")
            if c.button("📤 Share", key=f"csh{i}", help="Save to Shared Files"):
                save_shared(name, data)
                st.toast(f"{name} saved to Shared Files")
        if len(results) > 1:
            st.download_button(
                "📦 Download all (ZIP)",
                cv.zip_results(results),
                file_name="converted_files.zip",
                mime=cv.MIME["zip"],
            )


# ------------------------------------------------------- page: shared files
def files_page():
    st.title("📂 Shared Files")
    st.caption("Drop files here so anyone using this app can download them.")

    st.session_state.setdefault("drop_key", 0)
    ups = st.file_uploader(
        "Drop files here", accept_multiple_files=True, key=f"drop_{st.session_state.drop_key}"
    )
    if ups and st.button("📤 Upload", type="primary"):
        for f in ups:
            save_shared(f.name, f.getvalue())
        st.session_state.drop_key += 1
        st.rerun()

    st.divider()
    q = st.text_input("🔍 Search files")
    rows = db("SELECT id,filename,path,uploader,size,created FROM files ORDER BY id DESC", fetch=True)
    rows = [r for r in rows if q.lower() in r[1].lower()]
    if not rows:
        st.caption("No files yet.")
    for fid, fname, path, up, size, created in rows:
        c1, c2, c3 = st.columns([5, 2, 1])
        c1.markdown(f"**{fname}**  \n{human(size)} · {up} · {created}")
        if Path(path).exists():
            c2.download_button("⬇️ Download", Path(path).read_bytes(), file_name=fname, key=f"dl{fid}")
        if c3.button("🗑️", key=f"del{fid}", help="Delete"):
            Path(path).unlink(missing_ok=True)
            db("DELETE FROM files WHERE id=?", (fid,))
            st.rerun()


# ------------------------------------------------------------------ main
if not gate():
    st.stop()

with st.sidebar:
    st.text_input("Your name", key="user_name", placeholder="Shown on files you share")
    if st.button("Log out"):
        st.session_state.clear()
        st.rerun()

nav = st.navigation(
    [
        st.Page(scanner_page, title="Scan & Camera", icon="📷", default=True),
        st.Page(converter_page, title="File Converter", icon="🔄"),
        st.Page(files_page, title="Shared Files", icon="📂"),
    ]
)
nav.run()
