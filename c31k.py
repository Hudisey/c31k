"""C31K Prime - tek dosyalık Flask sitesi (backend + şablonlar + CSS hepsi burada)."""
import base64
import os
import sqlite3
import time
import uuid
from functools import wraps
from pathlib import Path

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError
from flask import (Flask, Response, flash, redirect, render_template, request,
                   session, stream_with_context, url_for)
from jinja2 import DictLoader
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

# ═══════════════════════════════════════════════════════════════
#  ADMIN HESAPLARI  →  Sitede kayıt olurken kullandığın isim buraya yazılacak.
#  Bu isimle giriş yapan hesap admin olur (Admin Panel + 🔨 rozeti).
#  Birden fazla admin için virgülle ayır:  AdminUser = "Ali, Veli"
#  (Render'da ADMIN_USER ortam değişkeni verirsen o, koddaki değerin yerine geçer.)
# ═══════════════════════════════════════════════════════════════
AdminUser = "Username"
ADMIN_USERS = {n.strip().lower() for n in (os.environ.get("ADMIN_USER") or AdminUser).split(",") if n.strip()}

BASE_DIR = Path(__file__).resolve().parent
# Veritabanı (c31k.db) yerelde tutuluyor ama her yazma işleminden sonra R2'ye
# yükleniyor, açılışta da R2'den indiriliyor — böylece Render'da persistent disk
# olmadan da (yeniden deploy / restart sonrası) veriler kaybolmuyor.
# Bkz: download_db_from_r2() / upload_db_to_r2() ve after_request hook'u.
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "c31k.db"
DB_KEY = os.environ.get("R2_DB_KEY", "db/c31k.db")  # R2'deki veritabanı yedeğinin anahtarı

# ───────────────────────── Cloudflare R2 (S3 uyumlu) ─────────────────────────
R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET = os.environ.get("R2_BUCKET", "")

r2 = boto3.client(
    "s3",
    endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
    aws_access_key_id=R2_ACCESS_KEY_ID,
    aws_secret_access_key=R2_SECRET_ACCESS_KEY,
    config=BotoConfig(signature_version="s3v4"),
    region_name="auto",
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")
# Yüklenebilecek maksimum dosya boyutu (MB). Render env değişkenlerine
# MAX_UPLOAD_MB ekleyip değiştirebilirsin, kodu değiştirmen gerekmez.
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "1024"))
app.config.update(MAX_CONTENT_LENGTH=MAX_UPLOAD_MB * 1024 * 1024, SESSION_COOKIE_SAMESITE="Lax")


# ───────────────────────── DB <-> R2 senkronizasyonu ─────────────────────────
def download_db_from_r2():
    """Açılışta: yerelde DB yoksa R2'deki son yedeği indirir (varsa)."""
    if DB_PATH.exists():
        return
    try:
        r2.download_file(R2_BUCKET, DB_KEY, str(DB_PATH))
    except ClientError:
        pass  # R2'de henüz yedek yok -> ilk çalıştırmada sıfırdan DB oluşacak


def upload_db_to_r2():
    """Yazma işleminden sonra: güncel DB dosyasını R2'ye yükler."""
    if not DB_PATH.exists():
        return
    try:
        r2.upload_file(str(DB_PATH), R2_BUCKET, DB_KEY)
    except ClientError:
        pass


download_db_from_r2()


@app.after_request
def _sync_db_to_r2(resp):
    # Sadece veri değiştirebilecek isteklerden sonra yükle (her GET'te değil).
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        upload_db_to_r2()
    return resp

IMG = {"png", "jpg", "jpeg", "webp", "gif"}
ARCHIVES = {"zip", "rar", "7z"}
FILES = IMG | ARCHIVES | {"exe", "msi", "iso", "apk", "pdf", "txt"}
CATEGORIES = {"vpns": "VPN", "tools": "TOOLS", "games": "GAMES"}
NAME_MAX = 40

# ───────────────────────── diller (varsayılan: English) ─────────────────────────
STR = {
    "en": {
        "made_by": "Made by: Antalya Obbyists & Dashers",
        "search_ph": "Search: games, tools, vpn...", "search": "Search",
        "no_results": "Nothing matches your search.", "no_items": "No items yet.",
        "open": "Open", "download": "Download",
        "login": "Log in", "username": "Name", "password": "Password", "continue": "Continue",
        "no_account": "No account yet?", "register": "Sign up", "create_account": "Create account",
        "have_account": "Already have an account?",
        "profile": "Profile", "settings": "Settings", "add_profile": "Add profile picture",
        "change_name": "Change name", "change_password": "Change password",
        "delete_account": "Delete account", "sign_out": "Sign out",
        "theme": "Theme", "dark": "Black", "light": "White", "custom": "Custom", "language": "Language",
        "c_accent": "Accent", "c_bg": "Background", "c_text": "Text", "c_dim": "Image dim", "c_reset": "Reset",
        "save": "Save", "cancel": "Cancel", "close": "Close", "back": "Back",
        "display_name": "Display name",
        "current_password": "Current password", "new_password": "New password",
        "delete_confirm_text": "This permanently deletes your account. This cannot be undone.",
        "delete_confirm": "Delete my account", "admin_badge": "Admin",
        "admin_panel": "Admin Panel", "add_game": "Add Game", "thumbnail": "Thumbnail (choose from computer)",
        "game_name": "Game name", "type": "Type", "source": "Source", "src_url": "URL", "src_file": "File",
        "url_ph": "https://...", "publish": "Publish", "published": "Published", "delete": "Delete",
        "edit": "Edit", "edit_item": "Edit item",
        "keep_thumb": "Leave empty to keep the current thumbnail.",
        "keep_file": "Leave empty to keep the current file:",
        "delete_q": "Delete this item?", "no_content": "Nothing published yet.",
        "lock_title": "Disable site", "lock_until": "Disable the site until",
        "lock_btn": "Disable site for everyone",
        "lock_open": "Re-open now", "lock_note": "Everyone except admins will see the closed page.",
        "lock_active": "Site is currently CLOSED", "lock_inactive": "Site is open",
        "closed_title": "Site is closed", "closed_text": "We'll be back in", "closed_admin": "admin login",
        "wrong_login": "Wrong name or password.", "name_empty": "Name can't be empty.",
        "name_long": "Name is too long (max 40).", "pw_empty": "Password can't be empty.",
        "name_taken": "This name is already taken.", "profile_ok": "Profile updated.",
        "wrong_password": "Current password is wrong.", "password_ok": "Password changed.",
        "account_deleted": "Account deleted.", "admin_only": "Admin only.",
        "need_title": "Game name is required.", "bad_category": "Invalid type.",
        "bad_url": "URL must start with http:// or https://", "need_file": "Please choose a file.",
        "bad_source": "Invalid source.", "bad_ext": "This file type is not allowed.",
        "added": "Published.", "saved": "Saved.", "deleted": "Deleted.", "lock_set": "Site disabled.",
        "lock_cleared": "Site re-opened.", "lock_bad": "Pick a time in the future.",
    },
    "tr": {
        "made_by": "Made by: Antalya Obbyists & Dashers",
        "search_ph": "Ara: oyun, tool, vpn...", "search": "Ara",
        "no_results": "Aramana uygun içerik bulunamadı.", "no_items": "Henüz içerik yok.",
        "open": "Aç", "download": "İndir",
        "login": "Giriş yap", "username": "İsim", "password": "Şifre", "continue": "Devam et",
        "no_account": "Hesabın yok mu?", "register": "Kayıt ol", "create_account": "Hesap oluştur",
        "have_account": "Zaten hesabın var mı?",
        "profile": "Profil", "settings": "Ayarlar", "add_profile": "Profil resmi ekle",
        "change_name": "İsim değiştir", "change_password": "Şifre değiştir",
        "delete_account": "Hesabı sil", "sign_out": "Çıkış yap",
        "theme": "Tema", "dark": "Siyah", "light": "Beyaz", "custom": "Özel", "language": "Dil",
        "c_accent": "Vurgu", "c_bg": "Arka plan", "c_text": "Yazı", "c_dim": "Resim karartma", "c_reset": "Sıfırla",
        "save": "Kaydet", "cancel": "Vazgeç", "close": "Kapat", "back": "Geri",
        "display_name": "Görünen isim",
        "current_password": "Mevcut şifre", "new_password": "Yeni şifre",
        "delete_confirm_text": "Hesabın kalıcı olarak silinir. Geri alınamaz.",
        "delete_confirm": "Hesabımı sil", "admin_badge": "Admin",
        "admin_panel": "Admin Panel", "add_game": "Oyun Ekle", "thumbnail": "Thumbnail (bilgisayardan seç)",
        "game_name": "Oyun ismi", "type": "Tür", "source": "Kaynak", "src_url": "URL gir", "src_file": "Dosya seç",
        "url_ph": "https://...", "publish": "Yayınla", "published": "Yayındakiler", "delete": "Sil",
        "edit": "Düzenle", "edit_item": "İçeriği düzenle",
        "keep_thumb": "Boş bırakırsan mevcut thumbnail kalır.",
        "keep_file": "Boş bırakırsan mevcut dosya kalır:",
        "delete_q": "Silinsin mi?", "no_content": "Henüz yayınlanmış bir şey yok.",
        "lock_title": "Siteyi devre dışı bırak", "lock_until": "Siteyi şu zamana kadar devre dışı bırak",
        "lock_btn": "Siteyi herkes için kapat",
        "lock_open": "Şimdi tekrar aç", "lock_note": "Adminler hariç herkes kapalı sayfayı görür.",
        "lock_active": "Site şu an KAPALI", "lock_inactive": "Site açık",
        "closed_title": "Site kapalı", "closed_text": "Açılmasına kalan süre", "closed_admin": "admin girişi",
        "wrong_login": "İsim veya şifre yanlış.", "name_empty": "İsim boş olamaz.",
        "name_long": "İsim çok uzun (en fazla 40).", "pw_empty": "Şifre boş olamaz.",
        "name_taken": "Bu isim zaten alınmış.", "profile_ok": "Profil güncellendi.",
        "wrong_password": "Mevcut şifre yanlış.", "password_ok": "Şifre değiştirildi.",
        "account_deleted": "Hesap silindi.", "admin_only": "Bu sayfa sadece admin için.",
        "need_title": "Oyun ismi gerekli.", "bad_category": "Geçersiz tür.",
        "bad_url": "URL http:// veya https:// ile başlamalı.", "need_file": "Bir dosya seçmelisin.",
        "bad_source": "Geçersiz kaynak.", "bad_ext": "Bu dosya türüne izin verilmiyor.",
        "added": "Yayınlandı.", "saved": "Kaydedildi.", "deleted": "Silindi.", "lock_set": "Site kapatıldı.",
        "lock_cleared": "Site tekrar açıldı.", "lock_bad": "Gelecekte bir zaman seç.",
    },
}


def get_lang():
    lang = request.cookies.get("lang", "en")
    return lang if lang in STR else "en"


def t(key):
    return STR[get_lang()].get(key) or STR["en"].get(key, key)


# ───────────────────────── veritabanı ─────────────────────────
def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT NOT NULL,
            avatar TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS content (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT DEFAULT '',
            thumbnail TEXT DEFAULT '',
            source_type TEXT NOT NULL DEFAULT 'link',
            source TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()


def get_setting(key, default=""):
    conn = db()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key, value):
    conn = db()
    conn.execute("INSERT INTO settings(key, value) VALUES (?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    conn.commit()
    conn.close()


def maintenance_until():
    """Site kapalıysa bitiş zamanını (epoch), değilse 0 döner."""
    try:
        until = float(get_setting("maintenance_until", "0") or 0)
    except ValueError:
        return 0
    return until if until > time.time() else 0


# ───────────────────────── yardımcılar ─────────────────────────
def ext_of(name):
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def check_ext(f, allowed):
    """Dosya seçilmişse uzantısını kontrol eder (kaydetmeden önce)."""
    if f and f.filename and ext_of(secure_filename(f.filename)) not in allowed:
        raise ValueError("bad_ext")


def save_upload(f, folder, allowed):
    """Dosyayı R2'de <folder>/<uuid>/<orijinal_ad> anahtarıyla saklar, göreli yolu döner."""
    if not f or not f.filename:
        return ""
    name = secure_filename(f.filename) or "file"
    if ext_of(name) not in allowed:
        raise ValueError("bad_ext")
    key = f"{folder}/{uuid.uuid4().hex}/{name}"
    r2.upload_fileobj(
        f.stream, R2_BUCKET, key,
        ExtraArgs={"ContentType": f.mimetype or "application/octet-stream"},
    )
    return key


def remove_upload(rel):
    if rel and not rel.startswith("http"):
        try:
            r2.delete_object(Bucket=R2_BUCKET, Key=rel)
        except ClientError:
            pass


def media(path):
    """Yüklenmiş dosya ise /uploads/... adresine, dış link ise olduğu gibi çevirir."""
    if not path:
        return ""
    return path if path.startswith(("http://", "https://")) else url_for("uploads", filename=path)


def is_http(url):
    return url.startswith(("http://", "https://"))


def is_admin():
    return bool(session.get("username")) and session["username"].lower() in ADMIN_USERS


def back():
    ref = request.referrer or ""
    return redirect(ref if ref.startswith(request.host_url) else url_for("index"))


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **k):
        if "username" not in session:
            return redirect(url_for("login"))
        return fn(*a, **k)
    return wrapper


def admin_only(fn):
    @wraps(fn)
    def wrapper(*a, **k):
        if not is_admin():
            flash("admin_only")
            return redirect(url_for("index"))
        return fn(*a, **k)
    return wrapper


# ───────────────────────── site kilidi ─────────────────────────
@app.before_request
def gate():
    if request.endpoint in {None, "static", "favicon", "set_lang"}:
        return None
    until = maintenance_until()
    if until and not is_admin() and request.endpoint not in {"login", "logout"}:
        return render_template("maintenance.html", until=until), 503
    return None


@app.context_processor
def inject():
    conn = db()
    counts = {r[0]: r[1] for r in conn.execute("SELECT category, COUNT(*) FROM content GROUP BY category")}
    me = None
    if "username" in session:
        me = conn.execute(
            "SELECT username, display_name, avatar FROM users WHERE username=?", (session["username"],)
        ).fetchone()
    conn.close()
    return {"me": me, "categories": CATEGORIES, "counts": counts,
            "media": media, "t": t, "lang": get_lang(), "is_admin": is_admin()}


# ───────────────────────── sayfalar ─────────────────────────
@app.route("/")
@login_required
def index():
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    sql, params = "SELECT * FROM content WHERE 1=1", []
    if q:
        sql += " AND (title LIKE ? OR category LIKE ?)"
        params += [f"%{q}%"] * 2
    if category in CATEGORIES:
        sql += " AND category = ?"
        params.append(category)
    conn = db()
    items = conn.execute(sql + " ORDER BY id DESC", params).fetchall()
    conn.close()
    return render_template("index.html", items=items, q=q, active_category=category)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        conn = db()
        user = conn.execute("SELECT * FROM users WHERE lower(username)=lower(?)", (username,)).fetchone()
        conn.close()
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            session.clear()
            session["username"] = user["username"]
            return redirect(url_for("index"))
        flash("wrong_login")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        # İsim ve şifre için uzunluk / karakter zorunluluğu yok (sadece boş olamaz)
        if not username:
            flash("name_empty")
        elif len(username) > NAME_MAX:
            flash("name_long")
        elif not password:
            flash("pw_empty")
        else:
            conn = db()
            taken = conn.execute("SELECT 1 FROM users WHERE lower(username)=lower(?)", (username,)).fetchone()
            if taken:
                conn.close()
                flash("name_taken")
            else:
                try:
                    conn.execute(
                        "INSERT INTO users(username, password_hash, display_name) VALUES (?,?,?)",
                        (username, generate_password_hash(password), username),
                    )
                    conn.commit()
                    conn.close()
                    session.clear()
                    session["username"] = username
                    return redirect(url_for("index"))
                except sqlite3.IntegrityError:
                    conn.close()
                    flash("name_taken")
    return render_template("register.html")


@app.post("/settings/avatar")
@login_required
def set_avatar():
    conn = db()
    user = conn.execute("SELECT avatar FROM users WHERE username=?", (session["username"],)).fetchone()
    try:
        new = save_upload(request.files.get("avatar"), "avatars", IMG)
        if new:
            remove_upload(user["avatar"])
            conn.execute("UPDATE users SET avatar=? WHERE username=?", (new, session["username"]))
            conn.commit()
            flash("profile_ok")
    except ValueError as exc:
        flash(str(exc))
    conn.close()
    return back()


@app.post("/settings/name")
@login_required
def set_name():
    name = request.form.get("display_name", "").strip()
    if not name:
        flash("name_empty")
    elif len(name) > NAME_MAX:
        flash("name_long")
    else:
        conn = db()
        conn.execute("UPDATE users SET display_name=? WHERE username=?", (name, session["username"]))
        conn.commit()
        conn.close()
        flash("profile_ok")
    return back()


@app.post("/settings/password")
@login_required
def set_password():
    current = request.form.get("current", "")
    new = request.form.get("new", "")
    conn = db()
    user = conn.execute("SELECT password_hash FROM users WHERE username=?", (session["username"],)).fetchone()
    if not user or not check_password_hash(user["password_hash"], current):
        flash("wrong_password")
    elif not new:
        flash("pw_empty")
    else:
        conn.execute("UPDATE users SET password_hash=? WHERE username=?",
                     (generate_password_hash(new), session["username"]))
        conn.commit()
        flash("password_ok")
    conn.close()
    return back()


@app.post("/settings/delete")
@login_required
def delete_account():
    username = session["username"]
    conn = db()
    user = conn.execute("SELECT avatar FROM users WHERE username=?", (username,)).fetchone()
    conn.execute("DELETE FROM users WHERE username=?", (username,))
    conn.commit()
    conn.close()
    if user:
        remove_upload(user["avatar"])
    session.clear()
    flash("account_deleted")
    return redirect(url_for("login"))


@app.route("/lang/<code>")
def set_lang(code):
    resp = back()
    if code in STR:
        resp.set_cookie("lang", code, max_age=365 * 24 * 3600, samesite="Lax")
    return resp


# ───────────────────────── admin ─────────────────────────
def read_item_form(old=None):
    """Ekleme/düzenleme formunu doğrular. (title, category, kind, source, yeni_dosya, yeni_thumb) döner."""
    f = request.form
    title, category = f.get("title", "").strip(), f.get("category", "")
    kind = f.get("source_type", "link")
    if not title:
        raise ValueError("need_title")
    if category not in CATEGORIES:
        raise ValueError("bad_category")
    thumb_file = request.files.get("thumbnail_file")
    check_ext(thumb_file, IMG)
    src_file = request.files.get("source_file")
    has_file = bool(src_file and src_file.filename)
    new_file = None
    if kind == "link":
        source = f.get("source_url", "").strip()
        if not is_http(source):
            raise ValueError("bad_url")
    elif kind == "file":
        if has_file:
            check_ext(src_file, FILES)
            new_file, source, kind = src_file, "", "file"
        elif old is not None and old["source_type"] != "link":
            kind, source = old["source_type"], old["source"]  # dosya değişmiyor
        else:
            raise ValueError("need_file")
    else:
        raise ValueError("bad_source")
    return title, category, kind, source, new_file, thumb_file


@app.route("/admin", methods=["GET", "POST"])
@admin_only
def admin():
    if request.method == "POST":
        try:
            title, category, kind, source, new_file, thumb_file = read_item_form()
            if new_file:
                source = save_upload(new_file, "content", FILES)
            thumb = save_upload(thumb_file, "thumbs", IMG)
            conn = db()
            conn.execute(
                "INSERT INTO content(title, category, description, thumbnail, source_type, source, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (title, category, "", thumb, kind, source, time.time()),
            )
            conn.commit()
            conn.close()
            flash("added")
        except ValueError as exc:
            flash(str(exc))
        return redirect(url_for("admin"))
    conn = db()
    items = conn.execute("SELECT * FROM content ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("admin.html", items=items, until=maintenance_until())


@app.route("/admin/edit/<int:item_id>", methods=["GET", "POST"])
@admin_only
def edit_item(item_id):
    conn = db()
    old = conn.execute("SELECT * FROM content WHERE id=?", (item_id,)).fetchone()
    if not old:
        conn.close()
        return redirect(url_for("admin"))
    if request.method == "POST":
        try:
            title, category, kind, source, new_file, thumb_file = read_item_form(old)
            if new_file:
                source = save_upload(new_file, "content", FILES)
            thumb = old["thumbnail"]
            new_thumb = save_upload(thumb_file, "thumbs", IMG)
            if new_thumb:
                thumb = new_thumb
            conn.execute(
                "UPDATE content SET title=?, category=?, thumbnail=?, source_type=?, source=? WHERE id=?",
                (title, category, thumb, kind, source, item_id),
            )
            conn.commit()
            if new_thumb:
                remove_upload(old["thumbnail"])
            if old["source_type"] != "link" and old["source"] != source:
                remove_upload(old["source"])
            conn.close()
            flash("saved")
            return redirect(url_for("admin"))
        except ValueError as exc:
            conn.close()
            flash(str(exc))
            return redirect(url_for("edit_item", item_id=item_id))
    conn.close()
    return render_template("edit.html", item=old)


@app.post("/admin/delete/<int:item_id>")
@admin_only
def delete_item(item_id):
    conn = db()
    row = conn.execute("SELECT * FROM content WHERE id=?", (item_id,)).fetchone()
    if row:
        conn.execute("DELETE FROM content WHERE id=?", (item_id,))
        conn.commit()
        remove_upload(row["thumbnail"])
        if row["source_type"] != "link":
            remove_upload(row["source"])
        flash("deleted")
    conn.close()
    return redirect(url_for("admin"))


@app.post("/admin/maintenance")
@admin_only
def admin_maintenance():
    try:
        until = float(request.form.get("until_ts", "0"))
    except ValueError:
        until = 0
    if until > time.time():
        set_setting("maintenance_until", until)
        flash("lock_set")
    else:
        flash("lock_bad")
    return redirect(url_for("admin"))


@app.post("/admin/maintenance/clear")
@admin_only
def admin_maintenance_clear():
    set_setting("maintenance_until", 0)
    flash("lock_cleared")
    return redirect(url_for("admin"))


@app.route("/uploads/<path:filename>")
def uploads(filename):
    is_image = ext_of(filename) in IMG
    try:
        obj = r2.get_object(Bucket=R2_BUCKET, Key=filename)
    except ClientError:
        return Response("Not found", status=404)
    headers = {}
    if obj.get("ContentLength") is not None:
        headers["Content-Length"] = str(obj["ContentLength"])
    if not is_image:
        headers["Content-Disposition"] = f'attachment; filename="{os.path.basename(filename)}"'
    return Response(
        stream_with_context(obj["Body"].iter_chunks(chunk_size=65536)),
        mimetype=obj.get("ContentType") or "application/octet-stream",
        headers=headers,
    )


@app.route("/favicon.jpg")
def favicon():
    return Response(FAVICON, mimetype="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ───────────────────────── tasarım + şablonlar ─────────────────────────
CSS = """
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&display=swap');
:root{color-scheme:dark;--bg:#0a0709;--veil:rgba(8,5,7,.70);--panel:rgba(20,13,16,.88);--solid:#150e11;--line:rgba(255,70,80,.22);--text:#f6eff1;--muted:#a99ca1;--a:#ff3b47;--b:#ff7a4d;--danger:#ff5d7a;--field:#0d080a;--nav:rgba(10,6,8,.85);--thumb:#1a1013;--on-a:#fff;--shadow:rgba(0,0,0,.5);--glow:rgba(255,59,71,.18)}
html[data-theme=light]{color-scheme:light;--bg:#f7f2f2;--veil:rgba(255,255,255,.80);--panel:rgba(255,255,255,.92);--solid:#fff;--line:rgba(160,20,30,.22);--text:#1c1114;--muted:#6f5f64;--a:#d1141f;--b:#f0562f;--danger:#e11d48;--field:#faf6f6;--nav:rgba(255,255,255,.88);--thumb:#eadcdc;--on-a:#fff;--shadow:rgba(60,20,25,.22);--glow:rgba(209,20,31,.12)}
*{box-sizing:border-box}[hidden]{display:none!important}
body{margin:0;min-height:100vh;color:var(--text);background:var(--bg);font:15px/1.55 "Space Grotesk",system-ui,sans-serif;overflow-x:hidden}
body::before{content:"";position:fixed;inset:0;z-index:-1;background:linear-gradient(var(--veil),var(--veil)),var(--bgimg) center/cover no-repeat}
a{color:inherit;text-decoration:none}button,input,select,textarea{font:inherit}
.muted{color:var(--muted)}

/* üst bar */
.top{position:sticky;top:0;z-index:30;display:flex;align-items:center;gap:20px;padding:10px 4%;background:var(--nav);backdrop-filter:blur(16px);border-bottom:1px solid var(--line)}
.brand{display:flex;align-items:center;gap:10px;font-weight:700;font-size:15px}
.brand img{width:38px;height:38px;border-radius:11px;object-fit:cover;box-shadow:0 0 0 1px var(--line)}
.nav{display:flex;gap:4px;flex:1}
.nav a{position:relative;padding:8px 14px;border-radius:10px;color:var(--muted);transition:color .2s,background .2s}
.nav a:hover{color:var(--text);background:var(--glow)}
.nav a.on{color:var(--text);box-shadow:inset 0 -2px 0 var(--a)}
.right{display:flex;align-items:center;gap:10px;margin-left:auto}
.mini{width:28px;height:28px;border-radius:9px;background:linear-gradient(135deg,var(--a),var(--b));display:grid;place-items:center;font-size:13px;font-weight:700;color:var(--on-a);overflow:hidden;flex:none}
.mini img{width:100%;height:100%;object-fit:cover}

/* profil menüsü */
.menu-wrap{position:relative}
.profile-btn{display:flex;align-items:center;gap:8px;padding:5px 12px 5px 6px;border:1px solid var(--line);border-radius:12px;background:transparent;color:var(--text);cursor:pointer;transition:.2s}
.profile-btn:hover,.profile-btn[aria-expanded=true]{border-color:var(--a);background:var(--glow)}
.menu{position:absolute;right:0;top:calc(100% + 10px);width:270px;max-height:calc(100vh - 90px);overflow:auto;padding:8px;border:1px solid var(--line);border-radius:16px;background:var(--solid);box-shadow:0 24px 70px var(--shadow)}
.menu .item{display:flex;align-items:center;justify-content:space-between;width:100%;padding:10px 12px;border:0;border-radius:10px;background:transparent;color:var(--text);cursor:pointer;text-align:left;transition:background .15s}
.menu .item:hover{background:var(--glow)}
.menu .sub{margin:2px 0 6px 10px;padding-left:8px;border-left:2px solid var(--line)}
.menu .sub .item{color:var(--muted);padding:8px 10px;font-size:14px}.menu .sub .item:hover{color:var(--text)}
.menu .sub .item.bad{color:var(--danger)}
.menu hr{border:0;border-top:1px solid var(--line);margin:6px 4px}
.menu .lbl{padding:6px 12px 2px;font-size:11px;letter-spacing:1.5px;text-transform:uppercase;color:var(--muted)}
.opts{display:flex;gap:6px;padding:4px 8px 8px}
.opts button,.opts a{flex:1;text-align:center;padding:8px 6px;border:1px solid var(--line);border-radius:10px;background:transparent;color:var(--muted);cursor:pointer;font-size:13px;transition:.2s}
.opts button:hover,.opts a:hover{color:var(--text);border-color:var(--a)}
.opts .on{color:var(--on-a);background:linear-gradient(135deg,var(--a),var(--b));border-color:transparent;font-weight:600}
.cust{padding:0 8px 8px}
.cust label{display:grid;grid-template-columns:1fr 96px;align-items:center;gap:8px;margin:0;padding:4px 4px;color:var(--muted);font-size:13px}
.cust .item{justify-content:center;margin-top:4px;border:1px solid var(--line);font-size:13px;padding:7px}
input[type=color]{padding:2px!important;height:32px;cursor:pointer}
input[type=range]{padding:0!important;border:0!important;background:transparent!important;box-shadow:none!important;accent-color:var(--a)}
.caret{font-size:10px;opacity:.7}

/* buton */
.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;padding:10px 18px;border:1px solid var(--a);border-radius:11px;background:transparent;color:var(--a);font-weight:600;cursor:pointer;transition:background .2s,color .2s,transform .15s}
.btn:hover{background:var(--a);color:var(--on-a)}
.btn:active{transform:scale(.97)}
.btn.full{width:100%}
.btn.solid{background:var(--a);color:var(--on-a)}.btn.solid:hover{filter:brightness(1.1)}
.btn.admin{padding:8px 14px;font-size:14px}
.btn.sm{padding:6px 14px}
.danger{color:var(--danger);border-color:var(--danger);padding:6px 14px}
.danger:hover{background:var(--danger);color:#fff}

/* ana sayfa */
.wrap{max-width:1100px;margin:auto;padding:30px 22px 70px}
.search{position:relative;display:flex;align-items:center;gap:8px;max-width:640px;margin:14vh auto 0;padding:7px;border:1px solid var(--line);border-radius:18px;background:var(--panel);backdrop-filter:blur(12px);box-shadow:0 20px 60px var(--shadow);transition:border-color .25s,box-shadow .25s}
.search:focus-within{border-color:var(--a);box-shadow:0 0 0 4px var(--glow),0 20px 70px var(--shadow)}
.search svg{margin-left:12px;flex:none;color:var(--muted)}
form.search input[name=q]{flex:1;min-width:0;border:0;border-radius:0;box-shadow:none;background:transparent;color:var(--text);padding:12px 6px;font-size:16px}
.meta{display:flex;justify-content:center;margin-top:16px}
.chip{padding:3px 12px;border:1px solid var(--a);border-radius:99px;font-size:13px;color:var(--a);transition:.2s}
.chip:hover{background:var(--a);color:var(--on-a)}

/* liste satırları */
.list{display:grid;gap:14px;margin-top:40px}
.rc{position:relative;display:flex;min-height:140px;border:1px solid var(--line);border-radius:16px;overflow:hidden;background:#150c0f;color:#fff;transition:transform .2s,border-color .2s,box-shadow .2s}
.rc:hover{transform:translateY(-3px);border-color:var(--a);box-shadow:0 18px 50px var(--shadow)}
.rc-thumb{position:relative;flex:0 0 min(300px,36%);background:var(--thumb);display:grid;place-items:center;color:rgba(255,255,255,.25);font-weight:700;font-size:44px;overflow:hidden}
.rc-thumb img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.rc-main{position:relative;flex:1;min-width:0;display:flex;align-items:center;justify-content:space-between;gap:18px;padding:18px 28px;overflow:hidden}
.rc-main .bgimg{position:absolute;inset:-8px;width:calc(100% + 16px);height:calc(100% + 16px);object-fit:cover;filter:blur(4px) saturate(1.15);opacity:.6}
.rc-main::before{content:"";position:absolute;inset:0;z-index:1;background:linear-gradient(90deg,rgba(10,4,6,.92) 0%,rgba(40,8,14,.65) 60%,rgba(80,12,22,.45) 100%)}
.rc-txt,.rc-side{position:relative;z-index:2}
.rc-txt{min-width:0}
.rc-txt h3{margin:0;font-size:clamp(22px,3.2vw,34px);line-height:1.1;letter-spacing:-.5px;overflow:hidden;text-overflow:ellipsis;text-shadow:0 2px 14px rgba(0,0,0,.6)}
.rc-txt .sub{margin-top:8px;font-size:14px;color:rgba(255,255,255,.7)}
.rc-side{flex:none;display:flex;align-items:center;gap:8px;font-weight:600;color:#ff9aa1}
.rc-side .arrow{transition:transform .2s}.rc:hover .arrow{transform:translateX(4px)}
.empty{margin-top:40px;padding:50px 20px;text-align:center;border:1px dashed var(--line);border-radius:20px;color:var(--muted);background:var(--panel)}

/* panel / form */
.eyebrow{display:inline-block;font-size:11px;letter-spacing:4px;color:var(--a);font-weight:700}
.auth{min-height:calc(100vh - 70px);display:grid;place-items:center;padding:24px}
.panel{width:100%;max-width:680px;margin:auto;padding:30px;border:1px solid var(--line);border-radius:22px;background:var(--panel);backdrop-filter:blur(14px);box-shadow:0 30px 90px var(--shadow)}
.panel.sm{max-width:390px;text-align:center}.panel.sm form,.panel.sm p{text-align:left}
.panel+.panel{margin-top:18px}
.logo{width:68px;height:68px;border-radius:20px;object-fit:cover;box-shadow:0 0 0 1px var(--line)}
.panel h1{font-size:30px;margin:8px 0 22px;letter-spacing:-.5px}.panel h2{margin:0 0 14px;font-size:19px}
form{display:grid;gap:10px}label{font-size:12px;color:var(--muted);margin-top:4px}
input:not([type=radio]):not([type=file]),select,textarea{width:100%;padding:12px 14px;border:1px solid var(--line);border-radius:12px;background:var(--field);color:var(--text);outline:0;transition:border-color .2s,box-shadow .2s}
input:focus,select:focus,textarea:focus{border-color:var(--a);box-shadow:0 0 0 4px var(--glow)}
input[type=file]{width:100%;color:var(--muted);font-size:13px}
input[type=file]::file-selector-button{margin-right:12px;padding:9px 14px;border:1px solid var(--a);border-radius:10px;background:transparent;color:var(--a);cursor:pointer;transition:.2s}
input[type=file]::file-selector-button:hover{background:var(--a);color:var(--on-a)}
.two{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.seg{display:flex;gap:6px}.seg label{flex:1;margin:0;cursor:pointer}.seg input{display:none}
.seg span{display:block;text-align:center;padding:10px;border:1px solid var(--line);border-radius:12px;color:var(--muted);transition:.2s}
.seg span:hover{color:var(--text);border-color:var(--a)}
.seg input:checked+span{color:var(--on-a);background:var(--a);border-color:transparent;font-weight:600}
.preview{width:100%;max-height:150px;object-fit:cover;border-radius:12px;border:1px solid var(--line)}
.avatar{width:96px;height:96px;margin:0 auto 14px;border-radius:28px;overflow:hidden;background:linear-gradient(135deg,var(--a),var(--b));display:grid;place-items:center;font-size:38px;font-weight:700;color:var(--on-a)}
.avatar img{width:100%;height:100%;object-fit:cover}
.arow{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:12px 0;border-bottom:1px solid var(--line)}
.arow:last-child{border:0}.arow small{display:block;color:var(--muted)}.arow form{display:block}
.arow .acts{display:flex;gap:8px;align-items:center}
.status{display:flex;align-items:center;gap:8px;margin-bottom:12px;font-size:14px}
.status i{width:9px;height:9px;border-radius:50%;background:#2be48a;box-shadow:0 0 10px #2be48a}.status.off i{background:var(--danger);box-shadow:0 0 10px var(--danger)}
dialog{width:min(92vw,400px);padding:26px;border:1px solid var(--line);border-radius:20px;background:var(--solid);color:var(--text);box-shadow:0 30px 90px var(--shadow)}
dialog::backdrop{background:rgba(0,0,0,.6);backdrop-filter:blur(3px)}
dialog h2{margin:0 0 14px;font-size:20px}
dialog.prof{text-align:center}
dialog .acts,.formacts{display:flex;gap:8px;justify-content:flex-end;margin-top:6px}
.flash-wrap{position:fixed;top:78px;right:18px;z-index:60;display:grid;gap:8px}
.flash{padding:12px 18px;border:1px solid var(--line);border-left:3px solid var(--a);border-radius:12px;background:var(--solid);box-shadow:0 15px 40px var(--shadow);transition:opacity .5s,transform .5s}
.count{font-size:clamp(40px,10vw,84px);font-weight:700;letter-spacing:2px;margin:10px 0 4px;font-variant-numeric:tabular-nums}
@media(max-width:760px){.top{flex-wrap:wrap;gap:8px}.nav{order:3;width:100%;overflow-x:auto}.two{grid-template-columns:1fr}.brand span{font-size:12px;max-width:150px}.search{margin-top:8vh}.rc{flex-direction:column}.rc-thumb{flex:0 0 130px}.rc-main{padding:16px 18px}}
"""

# Tema: Black / White / Custom. Seçim localStorage'da tutulur; sayfa açılırken hemen uygulanır.
THEME_INIT = """<script>
window.C31={
  def:{a:'#ff3b47',bg:'#0a0709',tx:'#f6eff1',dim:70},
  get(){try{return Object.assign({},this.def,JSON.parse(localStorage.getItem('custom')||'{}'))}catch(e){return Object.assign({},this.def)}},
  apply(){
    const r=document.documentElement;let th='dark';
    try{th=localStorage.getItem('theme')||'dark'}catch(e){}
    r.dataset.theme=th;
    ['--a','--b','--veil','--panel','--solid','--field','--nav','--thumb','--text','--muted','--line','--glow','--on-a','--shadow'].forEach(k=>r.style.removeProperty(k));
    r.style.removeProperty('color-scheme');
    if(th!=='custom')return;
    const c=this.get(),hx=h=>[1,3,5].map(i=>parseInt(h.substr(i,2),16)),lum=h=>{const v=hx(h);return(.299*v[0]+.587*v[1]+.114*v[2])/255},rgba=(h,al)=>{const v=hx(h);return 'rgba('+v[0]+','+v[1]+','+v[2]+','+al+')'};
    const light=lum(c.bg)>.5,edge=light?'#000':'#fff',set=(k,v)=>r.style.setProperty(k,v);
    set('color-scheme',light?'light':'dark');
    set('--a',c.a);set('--b','color-mix(in srgb,'+c.a+' 70%,#fff)');
    set('--veil',rgba(c.bg,c.dim/100));set('--panel',rgba(c.bg,.9));set('--solid',c.bg);set('--nav',rgba(c.bg,.88));
    set('--field','color-mix(in srgb,'+c.bg+' 90%,'+edge+')');set('--thumb','color-mix(in srgb,'+c.bg+' 80%,'+edge+')');
    set('--text',c.tx);set('--muted',rgba(c.tx,.62));set('--line',rgba(c.a,.28));set('--glow',rgba(c.a,.18));
    set('--on-a',lum(c.a)>.6?'#000':'#fff');set('--shadow','rgba(0,0,0,.4)');
  }
};C31.apply();
</script>"""

FLASH_JS = "setTimeout(()=>document.querySelectorAll('.flash').forEach(f=>{f.style.opacity=0;f.style.transform='translateX(40px)';setTimeout(()=>f.remove(),500)}),4200);"

TEMPLATES = {
    "base.html": """<!doctype html>
<html lang="{{ lang }}"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{% block title %}C31K Prime{% endblock %}</title>
<link rel="icon" type="image/jpeg" href="{{ url_for('favicon') }}">
""" + THEME_INIT + """
<style>:root{--bgimg:url('{{ url_for("static", filename="bg.jpg") }}')}</style>
<style>""" + CSS + """</style></head><body>
<header class="top">
  <a class="brand" href="{{ url_for('index') }}"><img src="{{ url_for('favicon') }}" alt=""><span>{{ t('made_by') }}</span></a>
  {% if me %}
  {% set cat = request.args.get('category','') if request.endpoint == 'index' else '' %}
  <nav class="nav">
    {% for key, name in categories.items() %}<a class="{{ 'on' if cat == key }}" href="{{ url_for('index', category=key) }}">{{ name }}</a>{% endfor %}
  </nav>
  {% endif %}
  <div class="right">
    {% if me %}
    {% if is_admin %}<a class="btn admin" href="{{ url_for('admin') }}">{{ t('admin_panel') }}</a>{% endif %}
    <div class="menu-wrap">
      <button class="profile-btn" id="pbtn" type="button" aria-expanded="false" aria-haspopup="true">
        <span class="mini">{% if me.avatar %}<img src="{{ media(me.avatar) }}" alt="">{% else %}{{ me.display_name[:1]|upper }}{% endif %}</span>
        <span>{{ t('profile') }}{% if is_admin %} 🔨{% endif %}</span><span class="caret">▾</span>
      </button>
      <div class="menu" id="pmenu" hidden>
        <button class="item" type="button" data-dlg="dProfile">{{ t('profile') }}{% if is_admin %}<span title="{{ t('admin_badge') }}">🔨</span>{% endif %}</button>
        <button class="item" type="button" id="setBtn" aria-expanded="false">{{ t('settings') }}<span class="caret">›</span></button>
        <div class="sub" id="setSub" hidden>
          <button class="item" type="button" data-dlg="dAvatar">{{ t('add_profile') }}</button>
          <button class="item" type="button" data-dlg="dName">{{ t('change_name') }}</button>
          <button class="item" type="button" data-dlg="dPass">{{ t('change_password') }}</button>
          <button class="item bad" type="button" data-dlg="dDelete">{{ t('delete_account') }}</button>
        </div>
        <a class="item" href="{{ url_for('logout') }}">{{ t('sign_out') }}</a>
        <hr>
        <div class="lbl">{{ t('theme') }}</div>
        <div class="opts"><button type="button" data-theme="dark">{{ t('dark') }}</button><button type="button" data-theme="light">{{ t('light') }}</button><button type="button" data-theme="custom">{{ t('custom') }}</button></div>
        <div class="cust" id="cust" hidden>
          <label>{{ t('c_accent') }}<input type="color" id="cA"></label>
          <label>{{ t('c_bg') }}<input type="color" id="cB"></label>
          <label>{{ t('c_text') }}<input type="color" id="cT"></label>
          <label>{{ t('c_dim') }}<input type="range" id="cD" min="20" max="95"></label>
          <button class="item" type="button" id="cR">{{ t('c_reset') }}</button>
        </div>
        <div class="lbl">{{ t('language') }}</div>
        <div class="opts"><a class="{{ 'on' if lang == 'en' }}" href="{{ url_for('set_lang', code='en') }}">ENG</a><a class="{{ 'on' if lang == 'tr' }}" href="{{ url_for('set_lang', code='tr') }}">TR</a></div>
      </div>
    </div>
    {% endif %}
  </div>
</header>
{% with msgs = get_flashed_messages() %}{% if msgs %}<div class="flash-wrap">{% for m in msgs %}<div class="flash">{{ t(m) }}</div>{% endfor %}</div>{% endif %}{% endwith %}
{% block content %}{% endblock %}
{% if me %}
<dialog id="dProfile" class="prof">
  <div class="avatar">{% if me.avatar %}<img src="{{ media(me.avatar) }}" alt="">{% else %}{{ me.display_name[:1]|upper }}{% endif %}</div>
  <h2 style="margin:0">{{ me.display_name }}{% if is_admin %} <span title="{{ t('admin_badge') }}">🔨</span>{% endif %}</h2>
  <p class="muted" style="margin:4px 0 0">@{{ me.username }}{% if is_admin %} · {{ t('admin_badge') }}{% endif %}</p>
  <div class="acts" style="justify-content:center;margin-top:18px"><button class="btn" type="button" data-close>{{ t('close') }}</button></div>
</dialog>
<dialog id="dAvatar"><h2>{{ t('add_profile') }}</h2>
  <form method="post" action="{{ url_for('set_avatar') }}" enctype="multipart/form-data">
    <input type="file" name="avatar" accept="image/png,image/jpeg,image/webp,image/gif" required>
    <div class="acts"><button class="btn" type="button" data-close>{{ t('cancel') }}</button><button class="btn solid">{{ t('save') }}</button></div>
  </form></dialog>
<dialog id="dName"><h2>{{ t('change_name') }}</h2>
  <form method="post" action="{{ url_for('set_name') }}">
    <input name="display_name" value="{{ me.display_name }}" maxlength="40" required>
    <div class="acts"><button class="btn" type="button" data-close>{{ t('cancel') }}</button><button class="btn solid">{{ t('save') }}</button></div>
  </form></dialog>
<dialog id="dPass"><h2>{{ t('change_password') }}</h2>
  <form method="post" action="{{ url_for('set_password') }}">
    <input name="current" type="password" placeholder="{{ t('current_password') }}" required autocomplete="current-password">
    <input name="new" type="password" placeholder="{{ t('new_password') }}" required autocomplete="new-password">
    <div class="acts"><button class="btn" type="button" data-close>{{ t('cancel') }}</button><button class="btn solid">{{ t('save') }}</button></div>
  </form></dialog>
<dialog id="dDelete"><h2>{{ t('delete_account') }}</h2>
  <p class="muted">{{ t('delete_confirm_text') }}</p>
  <form method="post" action="{{ url_for('delete_account') }}">
    <div class="acts"><button class="btn" type="button" data-close>{{ t('cancel') }}</button><button class="btn danger">{{ t('delete_confirm') }}</button></div>
  </form></dialog>
{% endif %}
<script>
function setTheme(th){
  try{localStorage.setItem('theme',th)}catch(e){}
  C31.apply();
  document.querySelectorAll('button[data-theme]').forEach(b=>b.classList.toggle('on',b.dataset.theme===th));
  const c=document.getElementById('cust');if(c)c.hidden=th!=='custom';
}
setTheme(document.documentElement.dataset.theme||'dark');
document.querySelectorAll('button[data-theme]').forEach(b=>b.onclick=()=>setTheme(b.dataset.theme));
const pb=document.getElementById('pbtn'),pm=document.getElementById('pmenu');
if(pb){
  const toggle=v=>{pm.hidden=!v;pb.setAttribute('aria-expanded',v)};
  pb.onclick=e=>{e.stopPropagation();toggle(pm.hidden)};
  document.addEventListener('click',e=>{if(!pm.hidden&&!pm.contains(e.target))toggle(false)});
  addEventListener('keydown',e=>{if(e.key==='Escape')toggle(false)});
  const sb=document.getElementById('setBtn'),ss=document.getElementById('setSub');
  sb.onclick=()=>{ss.hidden=!ss.hidden;sb.setAttribute('aria-expanded',!ss.hidden)};
  document.querySelectorAll('[data-dlg]').forEach(b=>b.onclick=()=>{toggle(false);document.getElementById(b.dataset.dlg).showModal()});
  document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>b.closest('dialog').close());
  document.querySelectorAll('dialog').forEach(d=>d.addEventListener('click',e=>{if(e.target===d)d.close()}));
  const cA=document.getElementById('cA'),cB=document.getElementById('cB'),cT=document.getElementById('cT'),cD=document.getElementById('cD');
  const fill=()=>{const c=C31.get();cA.value=c.a;cB.value=c.bg;cT.value=c.tx;cD.value=c.dim};fill();
  const save=()=>{try{localStorage.setItem('custom',JSON.stringify({a:cA.value,bg:cB.value,tx:cT.value,dim:+cD.value}))}catch(e){}setTheme('custom')};
  [cA,cB,cT,cD].forEach(i=>i.oninput=save);
  document.getElementById('cR').onclick=()=>{try{localStorage.removeItem('custom')}catch(e){}fill();setTheme('custom')};
}
""" + FLASH_JS + """
addEventListener('keydown',e=>{if(e.key==='/'&&!/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)){const s=document.querySelector('[name=q]');if(s){e.preventDefault();s.focus()}}});
</script>
</body></html>""",

    "index.html": """{% extends 'base.html' %}
{% block content %}<main class="wrap">
  <form class="search" method="get" action="{{ url_for('index') }}">
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
    <input name="q" value="{{ q }}" placeholder="{{ t('search_ph') }}" autocomplete="off" autofocus>
    {% if active_category %}<input type="hidden" name="category" value="{{ active_category }}">{% endif %}
    <button class="btn">{{ t('search') }}</button>
  </form>
  {% if active_category %}<div class="meta"><a class="chip" href="{{ url_for('index', q=q) if q else url_for('index') }}">{{ categories[active_category] }} ✕</a></div>{% endif %}
  {% if not items %}<div class="empty">{{ t('no_results') if q or active_category else t('no_items') }}</div>{% endif %}
  <section class="list">
  {% for item in items %}
    {% set link = item.source_type == 'link' %}
    <a class="rc" href="{{ item.source if link else media(item.source) }}"{% if link %} target="_blank" rel="noopener noreferrer"{% endif %}>
      <div class="rc-thumb">{{ item.title[:1]|upper }}{% if item.thumbnail %}<img src="{{ media(item.thumbnail) }}" alt="" loading="lazy">{% endif %}</div>
      <div class="rc-main">
        {% if item.thumbnail %}<img class="bgimg" src="{{ media(item.thumbnail) }}" alt="" loading="lazy">{% endif %}
        <div class="rc-txt">
          <h3>{{ item.title }}</h3>
          <div class="sub">{{ categories.get(item.category, item.category) }}</div>
        </div>
        <div class="rc-side">{{ t('open') if link else t('download') }} <span class="arrow">{{ '↗' if link else '↓' }}</span></div>
      </div>
    </a>
  {% endfor %}
  </section>
</main>{% endblock %}""",

    "login.html": """{% extends 'base.html' %}
{% block title %}{{ t('login') }} · C31K Prime{% endblock %}
{% block content %}<main class="auth"><div class="panel sm">
  <img class="logo" src="{{ url_for('favicon') }}" alt="">
  <h1>{{ t('login') }}</h1>
  <form method="post"><input name="username" placeholder="{{ t('username') }}" required autofocus autocomplete="username"><input name="password" type="password" placeholder="{{ t('password') }}" required autocomplete="current-password"><button class="btn solid full">{{ t('continue') }}</button></form>
  <p class="muted">{{ t('no_account') }} <a href="{{ url_for('register') }}" style="color:var(--a)">{{ t('register') }}</a></p>
  <div class="opts"><a class="{{ 'on' if lang == 'en' }}" href="{{ url_for('set_lang', code='en') }}">ENG</a><a class="{{ 'on' if lang == 'tr' }}" href="{{ url_for('set_lang', code='tr') }}">TR</a></div>
</div></main>{% endblock %}""",

    "register.html": """{% extends 'base.html' %}
{% block title %}{{ t('register') }} · C31K Prime{% endblock %}
{% block content %}<main class="auth"><div class="panel sm">
  <img class="logo" src="{{ url_for('favicon') }}" alt="">
  <h1>{{ t('create_account') }}</h1>
  <form method="post"><input name="username" placeholder="{{ t('username') }}" required autofocus maxlength="40" autocomplete="username"><input name="password" type="password" placeholder="{{ t('password') }}" required autocomplete="new-password"><button class="btn solid full">{{ t('register') }}</button></form>
  <p class="muted">{{ t('have_account') }} <a href="{{ url_for('login') }}" style="color:var(--a)">{{ t('login') }}</a></p>
  <div class="opts"><a class="{{ 'on' if lang == 'en' }}" href="{{ url_for('set_lang', code='en') }}">ENG</a><a class="{{ 'on' if lang == 'tr' }}" href="{{ url_for('set_lang', code='tr') }}">TR</a></div>
</div></main>{% endblock %}""",

    "maintenance.html": """<!doctype html>
<html lang="{{ lang }}"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ t('closed_title') }} · C31K Prime</title>
<link rel="icon" type="image/jpeg" href="{{ url_for('favicon') }}">
""" + THEME_INIT + """
<style>:root{--bgimg:url('{{ url_for("static", filename="bg.jpg") }}')}</style>
<style>""" + CSS + """</style></head><body>
<main class="auth"><div class="panel sm">
  <img class="logo" src="{{ url_for('favicon') }}" alt="">
  <h1>{{ t('closed_title') }}</h1>
  <p class="muted" style="text-align:center">{{ t('closed_text') }}</p>
  <div class="count" id="cd" data-until="{{ until|int }}">--:--:--</div>
  <p class="muted" style="text-align:center;font-size:13px" id="when"></p>
  <p style="text-align:center;margin-top:18px"><a class="muted" style="font-size:12px" href="{{ url_for('login') }}">{{ t('closed_admin') }}</a></p>
</div></main>
<script>
const cd=document.getElementById('cd'),until=+cd.dataset.until*1000;
document.getElementById('when').textContent=new Date(until).toLocaleString();
const p=n=>String(n).padStart(2,'0');
function tick(){const s=Math.max(0,Math.floor((until-Date.now())/1000));
  const d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60);
  cd.textContent=(d?d+'d ':'')+p(h)+':'+p(m)+':'+p(s%60);
  if(s<=0)location.reload()}
tick();setInterval(tick,1000);
</script></body></html>""",

    "admin.html": """{% extends 'base.html' %}
{% block title %}{{ t('admin_panel') }} · C31K Prime{% endblock %}
{% block content %}<main class="wrap">
<div class="panel" style="margin-top:20px">
  <span class="eyebrow">{{ t('admin_panel')|upper }}</span><h1>{{ t('add_game') }}</h1>
  <form method="post" enctype="multipart/form-data">
    <label>{{ t('thumbnail') }}</label>
    <input id="thIn" type="file" name="thumbnail_file" accept="image/png,image/jpeg,image/webp,image/gif">
    <img id="thPrev" class="preview" alt="" hidden>
    <div class="two">
      <div><label>{{ t('game_name') }}</label><input name="title" required></div>
      <div><label>{{ t('type') }}</label><select name="category">{% for k, n in categories.items() %}<option value="{{ k }}">{{ n }}</option>{% endfor %}</select></div>
    </div>
    <label>{{ t('source') }}</label>
    <div class="seg">
      <label><input type="radio" name="source_type" value="link" checked><span>{{ t('src_url') }}</span></label>
      <label><input type="radio" name="source_type" value="file"><span>{{ t('src_file') }}</span></label>
    </div>
    <input id="linkIn" name="source_url" placeholder="{{ t('url_ph') }}" required>
    <div id="fileBox" hidden><input id="fileIn" type="file" name="source_file"></div>
    <button class="btn solid">{{ t('publish') }}</button>
  </form>
</div>

<div class="panel">
  <h2>{{ t('published') }} ({{ items|length }})</h2>
  {% for item in items %}
  <div class="arow">
    <div><strong>{{ item.title }}</strong><small>{{ categories.get(item.category, item.category) }} · {{ 'URL' if item.source_type == 'link' else 'File' }}</small></div>
    <div class="acts">
      <a class="btn sm" href="{{ url_for('edit_item', item_id=item.id) }}">{{ t('edit') }}</a>
      <form method="post" action="{{ url_for('delete_item', item_id=item.id) }}" onsubmit="return confirm('{{ t('delete_q') }}')"><button class="btn danger">{{ t('delete') }}</button></form>
    </div>
  </div>
  {% else %}<p class="muted">{{ t('no_content') }}</p>{% endfor %}
</div>

<div class="panel">
  <h2>{{ t('lock_title') }}</h2>
  <div class="status {{ 'off' if until }}"><i></i><span>{{ t('lock_active') if until else t('lock_inactive') }}{% if until %} — <b id="untilTxt" data-ts="{{ until|int }}"></b>{% endif %}</span></div>
  <form method="post" action="{{ url_for('admin_maintenance') }}" id="lockForm">
    <label>{{ t('lock_until') }}</label>
    <input type="datetime-local" id="lockAt" required>
    <input type="hidden" name="until_ts" id="lockTs">
    <button class="btn danger" style="padding:10px 18px">{{ t('lock_btn') }}</button>
  </form>
  <p class="muted" style="font-size:13px">{{ t('lock_note') }}</p>
  {% if until %}<form method="post" action="{{ url_for('admin_maintenance_clear') }}"><button class="btn">{{ t('lock_open') }}</button></form>{% endif %}
</div>
<script>
const $=id=>document.getElementById(id);
function sync(){
  const k=document.querySelector('[name=source_type]:checked').value;
  $('linkIn').hidden=k!=='link';$('linkIn').required=k==='link';
  $('fileBox').hidden=k==='link';$('fileIn').required=k!=='link';
}
document.querySelectorAll('[name=source_type]').forEach(r=>r.addEventListener('change',sync));sync();
$('thIn').onchange=e=>{const f=e.target.files[0];$('thPrev').hidden=!f;if(f)$('thPrev').src=URL.createObjectURL(f)};
$('lockForm').onsubmit=()=>{const v=$('lockAt').value;if(!v)return false;$('lockTs').value=new Date(v).getTime()/1000;return true};
const ut=$('untilTxt');if(ut)ut.textContent=new Date(+ut.dataset.ts*1000).toLocaleString();
</script>
</main>{% endblock %}""",

    "edit.html": """{% extends 'base.html' %}
{% block title %}{{ t('edit_item') }} · C31K Prime{% endblock %}
{% block content %}<main class="wrap">
<div class="panel" style="margin-top:20px">
  <span class="eyebrow">{{ t('admin_panel')|upper }}</span><h1>{{ t('edit_item') }}</h1>
  {% set is_link = item.source_type == 'link' %}
  <form method="post" enctype="multipart/form-data">
    <label>{{ t('thumbnail') }}</label>
    <img id="thPrev" class="preview" alt="" {% if item.thumbnail %}src="{{ media(item.thumbnail) }}"{% else %}hidden{% endif %}>
    <input id="thIn" type="file" name="thumbnail_file" accept="image/png,image/jpeg,image/webp,image/gif">
    <small class="muted">{{ t('keep_thumb') }}</small>
    <div class="two">
      <div><label>{{ t('game_name') }}</label><input name="title" value="{{ item.title }}" required></div>
      <div><label>{{ t('type') }}</label><select name="category">{% for k, n in categories.items() %}<option value="{{ k }}" {{ 'selected' if k == item.category }}>{{ n }}</option>{% endfor %}</select></div>
    </div>
    <label>{{ t('source') }}</label>
    <div class="seg">
      <label><input type="radio" name="source_type" value="link" {{ 'checked' if is_link }}><span>{{ t('src_url') }}</span></label>
      <label><input type="radio" name="source_type" value="file" {{ 'checked' if not is_link }}><span>{{ t('src_file') }}</span></label>
    </div>
    <input id="linkIn" name="source_url" placeholder="{{ t('url_ph') }}" value="{{ item.source if is_link else '' }}">
    <div id="fileBox" hidden>
      <input id="fileIn" type="file" name="source_file">
      {% if not is_link %}<small class="muted">{{ t('keep_file') }} {{ item.source.split('/')[-1] }}</small>{% endif %}
    </div>
    <div class="formacts"><a class="btn" href="{{ url_for('admin') }}">{{ t('back') }}</a><button class="btn solid">{{ t('save') }}</button></div>
  </form>
</div>
<script>
const $=id=>document.getElementById(id);
function sync(){
  const k=document.querySelector('[name=source_type]:checked').value;
  $('linkIn').hidden=k!=='link';$('linkIn').required=k==='link';
  $('fileBox').hidden=k==='link';
}
document.querySelectorAll('[name=source_type]').forEach(r=>r.addEventListener('change',sync));sync();
$('thIn').onchange=e=>{const f=e.target.files[0];if(f){$('thPrev').hidden=false;$('thPrev').src=URL.createObjectURL(f)}};
</script>
</main>{% endblock %}""",
}

# Site ikonu / logo (favicon.jpg dosyasına gerek yok, görsel buraya gömülü)
FAVICON = base64.b64decode("""
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
+KlDoSVYAjPrpmmqG5+OnsE//9k=
""")

app.jinja_loader = DictLoader(TEMPLATES)
init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)),
            debug=os.environ.get("FLASK_DEBUG") == "1")
