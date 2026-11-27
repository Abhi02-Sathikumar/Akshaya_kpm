"""Office Hub - Streamlit app
Pages: Scan & Convert (upload/camera -> PDF/JPG), Work Records (per staff),
Shared Files, Team Chat. Protected by a passcode.
"""
import hmac
import io
import sqlite3
import uuid
import zipfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import fitz  # PyMuPDF
import streamlit as st
from PIL import Image, ImageEnhance, ImageOps

try:  # optional: iPhone HEIC photos
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:
    pass

st.set_page_config(page_title="Office Hub", page_icon="🗂️", layout="wide")

# ----------------------------------------------------------------- config
try:
    PASSCODE = str(st.secrets["PASSCODE"])
except Exception:
    PASSCODE = "tvm044"  # fallback; better: set PASSCODE in Streamlit secrets

TZ = ZoneInfo("Asia/Kolkata")
BASE = Path(__file__).parent / "storage"
SHARED_DIR = BASE / "shared"
WORK_DIR = BASE / "work"
DB_PATH = BASE / "hub.db"
for d in (SHARED_DIR, WORK_DIR):
    d.mkdir(parents=True, exist_ok=True)

IMG_TYPES = ["jpg", "jpeg", "png", "webp", "bmp", "heic"]


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
    db("CREATE TABLE IF NOT EXISTS staff (name TEXT PRIMARY KEY)")
    db(
        """CREATE TABLE IF NOT EXISTS work (
        id INTEGER PRIMARY KEY AUTOINCREMENT, staff TEXT, title TEXT,
        description TEXT, status TEXT, work_date TEXT,
        file_path TEXT, file_name TEXT, created TEXT)"""
    )
    db(
        """CREATE TABLE IF NOT EXISTS files (
        id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT, path TEXT,
        uploader TEXT, size INTEGER, created TEXT)"""
    )
    db(
        """CREATE TABLE IF NOT EXISTS chat (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT, msg TEXT, created TEXT)"""
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


def store_file(folder: Path, name: str, data: bytes) -> str:
    path = folder / f"{uuid.uuid4().hex}{Path(name).suffix.lower()}"
    path.write_bytes(data)
    return str(path)


def current_user():
    return (st.session_state.get("user_name") or "").strip()


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


# ------------------------------------------------------- page: scan/convert
@st.cache_data(show_spinner=False, max_entries=8)
def decode(name: str, data: bytes):
    """Return a list of RGB PIL images (one per image file / PDF page)."""
    if name.lower().endswith(".pdf"):
        doc = fitz.open(stream=data, filetype="pdf")
        out = []
        for page in doc:
            pix = page.get_pixmap(dpi=150)
            out.append(Image.frombytes("RGB", [pix.width, pix.height], pix.samples))
        return out
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    im.thumbnail((2400, 2400))
    return [im]


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
    st.title("📷 Scan & Convert")
    st.caption("Upload images/PDFs or take photos, then export as one PDF or as JPG.")

    st.session_state.setdefault("scans", [])
    st.session_state.setdefault("cam_key", 0)

    tab_up, tab_cam = st.tabs(["📁 Upload files", "📸 Take photo"])
    with tab_up:
        uploads = st.file_uploader(
            "Images or PDFs (from phone or laptop)",
            type=IMG_TYPES + ["pdf"],
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
            buf = io.BytesIO()
            first, *rest = processed
            first.save(buf, "PDF", save_all=True, append_images=rest, resolution=150.0)
            result = (f"{base}.pdf", buf.getvalue(), "application/pdf")
        elif len(processed) == 1:
            buf = io.BytesIO()
            processed[0].save(buf, "JPEG", quality=quality)
            result = (f"{base}.jpg", buf.getvalue(), "image/jpeg")
        else:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                for i, im in enumerate(processed, 1):
                    b = io.BytesIO()
                    im.save(b, "JPEG", quality=quality)
                    z.writestr(f"{base}_{i}.jpg", b.getvalue())
            result = (f"{base}_jpgs.zip", buf.getvalue(), "application/zip")
        st.session_state.scan_result = result

    res = st.session_state.get("scan_result")
    if res:
        name, data, mime = res
        st.success(f"Ready: {name} ({human(len(data))})")
        d1, d2 = st.columns(2)
        d1.download_button("⬇️ Download", data, file_name=name, mime=mime, use_container_width=True)
        if d2.button("📤 Save to Shared Files", use_container_width=True):
            path = store_file(SHARED_DIR, name, data)
            db(
                "INSERT INTO files (filename, path, uploader, size, created) VALUES (?,?,?,?,?)",
                (name, path, current_user() or "Unknown", len(data), now()),
            )
            st.success("Saved to Shared Files")


# ------------------------------------------------------- page: work records
def work_page():
    st.title("🧑‍💼 Work Records")
    staff = [r[0] for r in db("SELECT name FROM staff ORDER BY name", fetch=True)]

    with st.expander("➕ Add a staff member"):
        with st.form("add_staff", clear_on_submit=True):
            new = st.text_input("Staff name")
            if st.form_submit_button("Add") and new.strip():
                db("INSERT OR IGNORE INTO staff (name) VALUES (?)", (new.strip(),))
                st.rerun()

    tab_staff, tab_all = st.tabs(["Staff page", "Everyone (summary)"])

    with tab_staff:
        if not staff:
            st.info("No staff yet. Add one above.")
        else:
            person = st.selectbox("Select staff", staff)
            st.subheader(f"Work done by {person}")

            with st.form(f"work_form", clear_on_submit=True):
                title = st.text_input("Work / application title")
                desc = st.text_area("Details")
                c1, c2 = st.columns(2)
                status = c1.selectbox("Status", ["Done", "In progress", "Pending"])
                wdate = c2.date_input("Date", value=date.today())
                att = st.file_uploader("Attach a file (optional)")
                if st.form_submit_button("Save entry", type="primary"):
                    if not title.strip():
                        st.error("Title is required")
                    else:
                        path = name = None
                        if att is not None:
                            path, name = store_file(WORK_DIR, att.name, att.getvalue()), att.name
                        db(
                            """INSERT INTO work (staff,title,description,status,work_date,
                               file_path,file_name,created) VALUES (?,?,?,?,?,?,?,?)""",
                            (person, title.strip(), desc, status, str(wdate), path, name, now()),
                        )
                        st.success("Saved")
                        st.rerun()

            rows = db(
                """SELECT id,title,description,status,work_date,file_path,file_name
                   FROM work WHERE staff=? ORDER BY work_date DESC, id DESC""",
                (person,),
                fetch=True,
            )
            if not rows:
                st.caption("No entries yet.")
            for wid, title, desc, status, wdate, fpath, fname in rows:
                icon = {"Done": "✅", "In progress": "🛠️", "Pending": "⏳"}.get(status, "")
                with st.expander(f"{icon} {title} · {wdate}"):
                    st.write(desc or "_No details_")
                    st.caption(f"Status: {status}")
                    if fpath and Path(fpath).exists():
                        st.download_button(
                            f"⬇️ {fname}", Path(fpath).read_bytes(), file_name=fname, key=f"wd{wid}"
                        )
                    if st.button("Delete entry", key=f"wdel{wid}"):
                        if fpath:
                            Path(fpath).unlink(missing_ok=True)
                        db("DELETE FROM work WHERE id=?", (wid,))
                        st.rerun()

    with tab_all:
        rows = db(
            "SELECT staff,title,status,work_date FROM work ORDER BY work_date DESC", fetch=True
        )
        if rows:
            st.dataframe(
                [{"Staff": r[0], "Work": r[1], "Status": r[2], "Date": r[3]} for r in rows],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.caption("Nothing recorded yet.")


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
            data = f.getvalue()
            path = store_file(SHARED_DIR, f.name, data)
            db(
                "INSERT INTO files (filename, path, uploader, size, created) VALUES (?,?,?,?,?)",
                (f.name, path, current_user() or "Unknown", len(data), now()),
            )
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


# ----------------------------------------------------------- page: chat
@st.fragment(run_every=4)
def chat_messages():
    me = current_user()
    rows = db("SELECT user,msg,created FROM chat ORDER BY id DESC LIMIT 100", fetch=True)[::-1]
    box = st.container(height=480)
    if not rows:
        box.caption("No messages yet. Say hi 👋")
    for user, msg, created in rows:
        with box.chat_message("user" if user == me else "assistant"):
            st.caption(f"{user} · {created}")
            st.write(msg)


def chat_page():
    st.title("💬 Team Chat")
    if not current_user():
        st.warning("Enter your name in the sidebar to chat.")
    chat_messages()
    msg = st.chat_input("Type a message", disabled=not current_user())
    if msg:
        db(
            "INSERT INTO chat (user,msg,created) VALUES (?,?,?)",
            (current_user(), msg, now()),
        )
        st.rerun()


# ------------------------------------------------------------------ main
if not gate():
    st.stop()

with st.sidebar:
    st.text_input("Your name", key="user_name", placeholder="e.g. Abhiram")
    if st.button("Log out"):
        st.session_state.clear()
        st.rerun()

nav = st.navigation(
    [
        st.Page(scanner_page, title="Scan & Convert", icon="📷", default=True),
        st.Page(work_page, title="Work Records", icon="🧑‍💼"),
        st.Page(files_page, title="Shared Files", icon="📂"),
        st.Page(chat_page, title="Team Chat", icon="💬"),
    ]
)
nav.run()
