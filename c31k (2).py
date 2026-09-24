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
from markupsafe import Markup, escape
from flask import (Flask, Response, flash, redirect, render_template, request,
                   session, stream_with_context, url_for)
from jinja2 import DictLoader
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

# ═══════════════════════════════════════════════════════════════
#  ADMIN HESAPLARI  →  Sitede kayıt olurken kullandığın isim buraya yazılacak.
#  Bu isimle giriş yapan hesap admin olur (Admin Panel yetkisi).
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
CATEGORIES = {"vpns": "VPN", "tools": "TOOLS", "apps": "APPS"}
NAME_MAX = 40
BADGES = {"moderator": "moderator", "bughunter": "bughunter"}

# ───────────────────────── diller (varsayılan: English) ─────────────────────────
STR = {
    "en": {
        "made_by": "Made by: Antalya Obbyists & Dashers",
        "search_ph": "Search: apps, tools, vpn...", "search": "Search",
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
        "admin_panel": "Admin Panel", "add_game": "Add App", "thumbnail": "Thumbnail (choose from computer)",
        "game_name": "App name", "type": "Type", "source": "Source", "src_url": "URL", "src_file": "File",
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
        "need_title": "App name is required.", "bad_category": "Invalid type.",
        "bad_url": "URL must start with http:// or https://", "need_file": "Please choose a file.",
        "bad_source": "Invalid source.", "bad_ext": "This file type is not allowed.",
        "added": "Published.", "saved": "Saved.", "deleted": "Deleted.", "lock_set": "Site disabled.",
        "lock_cleared": "Site re-opened.", "lock_bad": "Pick a time in the future.",
        "add_badge": "Add Badge", "select_badge": "Select badge", "assign_badge": "Assign badge",
        "moderator_badge": "Moderator", "bughunter_badge": "Bug Hunter", "no_badge": "No badge",
        "badge_saved": "Badge saved.", "bad_badge": "Invalid badge.", "user_not_found": "User not found.",
        "remove_badge": "Remove badge", "current_badge": "Current badge",
        "mod_panel": "Mod Panel", "ban_user": "Ban a user", "ban": "Ban",
        "banned_msg": "This account has been banned.", "user_banned": "User banned.",
        "user_unbanned": "User unbanned.", "cant_ban_admin": "You can't ban an admin.",
        "banned_users": "Banned users", "unban": "Unban",
        "profile_search": "Search profiles", "profile_search_ph": "Search users...",
        "no_users_found": "No users found.", "customize_photo": "Customize picture",
    },
    "tr": {
        "made_by": "Made by: Antalya Obbyists & Dashers",
        "search_ph": "Ara: uygulama, tool, vpn...", "search": "Ara",
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
        "admin_panel": "Admin Panel", "add_game": "Uygulama Ekle", "thumbnail": "Thumbnail (bilgisayardan seç)",
        "game_name": "Uygulama ismi", "type": "Tür", "source": "Kaynak", "src_url": "URL gir", "src_file": "Dosya seç",
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
        "need_title": "Uygulama ismi gerekli.", "bad_category": "Geçersiz tür.",
        "bad_url": "URL http:// veya https:// ile başlamalı.", "need_file": "Bir dosya seçmelisin.",
        "bad_source": "Geçersiz kaynak.", "bad_ext": "Bu dosya türüne izin verilmiyor.",
        "added": "Yayınlandı.", "saved": "Kaydedildi.", "deleted": "Silindi.", "lock_set": "Site kapatıldı.",
        "lock_cleared": "Site tekrar açıldı.", "lock_bad": "Gelecekte bir zaman seç.",
        "add_badge": "Rozet Ekle", "select_badge": "Rozet seç", "assign_badge": "Rozeti ata",
        "moderator_badge": "Moderatör", "bughunter_badge": "Bug Hunter", "no_badge": "Rozet yok",
        "badge_saved": "Rozet kaydedildi.", "bad_badge": "Geçersiz rozet.", "user_not_found": "Kullanıcı bulunamadı.",
        "remove_badge": "Rozeti kaldır", "current_badge": "Mevcut rozet",
        "mod_panel": "Mod Panel", "ban_user": "Kullanıcı banla", "ban": "Banla",
        "banned_msg": "Bu hesap banlanmış.", "user_banned": "Kullanıcı banlandı.",
        "user_unbanned": "Banı kaldırıldı.", "cant_ban_admin": "Bir admini banlayamazsın.",
        "banned_users": "Banlı kullanıcılar", "unban": "Banı kaldır",
        "profile_search": "Profil ara", "profile_search_ph": "Kullanıcı ara...",
        "no_users_found": "Kullanıcı bulunamadı.", "customize_photo": "Resmi özelleştir",
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
            avatar TEXT DEFAULT '',
            badge TEXT DEFAULT '',
            banned INTEGER DEFAULT 0
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
    # eski veritabanlarını yeni kolonlarla güncelle (varsa dokunmaz)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    if "badge" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN badge TEXT DEFAULT ''")
    if "banned" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN banned INTEGER DEFAULT 0")
    conn.execute("UPDATE content SET category='apps' WHERE category='games'")  # eski kategori adı
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


def parse_badges(raw):
    """'moderator,bughunter' -> ['moderator', 'bughunter'] (BADGES sırasına göre)."""
    have = {b.strip() for b in (raw or "").split(",")}
    return [b for b in BADGES if b in have]


def current_badges():
    if "username" not in session:
        return []
    conn = db()
    row = conn.execute("SELECT badge FROM users WHERE username=?", (session["username"],)).fetchone()
    conn.close()
    return parse_badges(row["badge"]) if row else []


def is_moderator():
    return is_admin() or "moderator" in current_badges()


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


def mod_only(fn):
    @wraps(fn)
    def wrapper(*a, **k):
        if not is_moderator():
            flash("admin_only")
            return redirect(url_for("index"))
        return fn(*a, **k)
    return wrapper


# ───────────────────────── site kilidi ─────────────────────────
@app.before_request
def gate():
    if request.endpoint in {None, "static", "favicon", "set_lang"}:
        return None
    if "username" in session and request.endpoint not in {"login", "logout"}:
        conn = db()
        row = conn.execute("SELECT banned FROM users WHERE username=?", (session["username"],)).fetchone()
        conn.close()
        if row and row["banned"]:
            session.clear()
            flash("banned_msg")
            return redirect(url_for("login"))
    until = maintenance_until()
    if until and not is_admin() and request.endpoint not in {"login", "logout"}:
        return render_template("maintenance.html", until=until), 503
    return None


def badge_icon(badge):
    if badge in BADGES:
        return url_for("badge_img", name=badge)
    return ""


def badge_html(raw):
    out = [f'<img class="badge-ic" src="{badge_icon(b)}" alt="" title="{escape(t(b + "_badge"))}">'
           for b in parse_badges(raw)]
    return Markup("".join(out))


def badge_names(raw):
    return ", ".join(t(b + "_badge") for b in parse_badges(raw))


@app.context_processor
def inject():
    conn = db()
    counts = {r[0]: r[1] for r in conn.execute("SELECT category, COUNT(*) FROM content GROUP BY category")}
    me = None
    if "username" in session:
        me = conn.execute(
            "SELECT username, display_name, avatar, badge FROM users WHERE username=?", (session["username"],)
        ).fetchone()
    conn.close()
    return {"me": me, "categories": CATEGORIES, "counts": counts,
            "media": media, "t": t, "lang": get_lang(), "is_admin": is_admin(),
            "is_moderator": is_moderator(), "badge_icon": badge_icon,
            "badge_html": badge_html, "badge_names": badge_names, "badge_list": parse_badges}


# ───────────────────────── sayfalar ─────────────────────────
@app.route("/")
@login_required
def index():
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    if category == "games":  # eski linkler
        return redirect(url_for("index", category="apps", q=q or None))
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
            if user["banned"]:
                flash("banned_msg")
            else:
                session.clear()
                session["username"] = user["username"]
                return redirect(url_for("index"))
        else:
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


def create_item_from_form():
    """Formu doğrulayıp yeni içeriği yayınlar (admin ve moderatör ortak kullanır)."""
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


@app.route("/admin", methods=["GET", "POST"])
@admin_only
def admin():
    if request.method == "POST":
        try:
            create_item_from_form()
            flash("added")
        except ValueError as exc:
            flash(str(exc))
        return redirect(url_for("admin"))
    conn = db()
    items = conn.execute("SELECT * FROM content ORDER BY id DESC").fetchall()
    badged_users = conn.execute(
        "SELECT username, display_name, badge FROM users WHERE badge != '' ORDER BY username"
    ).fetchall()
    conn.close()
    return render_template("admin.html", items=items, until=maintenance_until(), badged_users=badged_users)


@app.post("/admin/badge")
@admin_only
def admin_set_badge():
    username = request.form.get("username", "").strip()
    badge = request.form.get("badge", "")
    action = request.form.get("action", "add")
    if badge and badge not in BADGES:
        flash("bad_badge")
        return redirect(url_for("admin"))
    conn = db()
    user = conn.execute("SELECT id, badge FROM users WHERE lower(username)=lower(?)", (username,)).fetchone()
    if not user:
        flash("user_not_found")
    else:
        cur = parse_badges(user["badge"])
        if action == "remove":
            cur = [] if not badge else [b for b in cur if b != badge]
        elif badge:
            if badge not in cur:
                cur.append(badge)
        else:
            flash("bad_badge")
            conn.close()
            return redirect(url_for("admin"))
        cur = [b for b in BADGES if b in cur]  # sıralı, tekrarsız
        conn.execute("UPDATE users SET badge=? WHERE id=?", (",".join(cur), user["id"]))
        conn.commit()
        flash("badge_saved")
    conn.close()
    return redirect(url_for("admin"))


@app.route("/mod", methods=["GET", "POST"])
@mod_only
def mod_panel():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        conn = db()
        user = conn.execute(
            "SELECT id, username FROM users WHERE lower(username)=lower(?)", (username,)
        ).fetchone()
        if not user:
            flash("user_not_found")
        elif user["username"].lower() in ADMIN_USERS:
            flash("cant_ban_admin")
        else:
            conn.execute("UPDATE users SET banned=1 WHERE id=?", (user["id"],))
            conn.commit()
            flash("user_banned")
        conn.close()
        return redirect(url_for("mod_panel"))
    conn = db()
    banned = conn.execute(
        "SELECT username, display_name FROM users WHERE banned=1 ORDER BY username"
    ).fetchall()
    conn.close()
    return render_template("mod.html", banned=banned)


@app.post("/mod/add")
@mod_only
def mod_add():
    try:
        create_item_from_form()
        flash("added")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("mod_panel"))


@app.post("/mod/unban/<username>")
@mod_only
def mod_unban(username):
    conn = db()
    conn.execute("UPDATE users SET banned=0 WHERE lower(username)=lower(?)", (username,))
    conn.commit()
    conn.close()
    flash("user_unbanned")
    return redirect(url_for("mod_panel"))


@app.route("/profile-search")
@login_required
def profile_search():
    q = request.args.get("q", "").strip()
    users = []
    if q:
        conn = db()
        users = conn.execute(
            "SELECT username, display_name, avatar, badge FROM users "
            "WHERE username LIKE ? OR display_name LIKE ? ORDER BY display_name LIMIT 30",
            (f"%{q}%", f"%{q}%"),
        ).fetchall()
        conn.close()
    return render_template("profile_search.html", q=q, users=users)


@app.route("/badge/<name>.png")
def badge_img(name):
    data = BADGE_IMAGES.get(name)
    if not data:
        return Response("Not found", status=404)
    return Response(data, mimetype="image/png", headers={"Cache-Control": "public, max-age=86400"})


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
.mini{width:28px;height:28px;border-radius:50%;background:linear-gradient(135deg,var(--a),var(--b));display:grid;place-items:center;font-size:13px;font-weight:700;color:var(--on-a);overflow:hidden;flex:none}
.mini img{width:100%;height:100%;object-fit:cover}
.badge-ic{width:16px;height:16px;object-fit:contain;vertical-align:middle;flex:none}
.badge-ic+.badge-ic{margin-left:3px}

/* profil arama (üst ortada) */
.pf-search{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);display:flex;align-items:center;gap:6px;width:min(260px,26vw);padding:6px 12px;border:1px solid var(--line);border-radius:12px;background:var(--field);color:var(--muted);transition:.2s}
.pf-search:focus-within{border-color:var(--a);color:var(--text)}
.pf-search svg{flex:none}
.pf-search input{border:0!important;background:transparent!important;padding:2px 0!important;box-shadow:none!important;color:inherit}
@media(max-width:900px){.pf-search{display:none}}

/* resim kırpma diyaloğu */
.cropdlg{text-align:center}
.cropstage{display:flex;justify-content:center;margin:12px 0}
.cropstage canvas{background:#000;touch-action:none;cursor:grab;border-radius:10px}
#cropZoom{width:100%}

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
.avatar{width:96px;height:96px;margin:0 auto 14px;border-radius:50%;overflow:hidden;background:linear-gradient(135deg,var(--a),var(--b));display:grid;place-items:center;font-size:38px;font-weight:700;color:var(--on-a)}
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
</script>
<script>
(function(){
  let state=null, img=new Image(), activeShape='circle', activeCb=null, activeCancel=null, finished=false, wired=false;
  const SIZE=280;
  function els(){
    return {dlg:document.getElementById('dCrop'), cv:document.getElementById('cropCv'),
      zoom:document.getElementById('cropZoom'), done:document.getElementById('cropDone'),
      cancel:document.getElementById('cropCancel')};
  }
  function draw(){
    const {cv}=els(); if(!cv||!state) return;
    const ctx=cv.getContext('2d');
    ctx.clearRect(0,0,SIZE,SIZE);
    ctx.save();
    ctx.beginPath();
    if(activeShape==='circle'){ctx.arc(SIZE/2,SIZE/2,SIZE/2,0,Math.PI*2);}else{ctx.rect(0,0,SIZE,SIZE);}
    ctx.clip();
    const w=img.width*state.scale,h=img.height*state.scale;
    ctx.drawImage(img,state.x-w/2,state.y-h/2,w,h);
    ctx.restore();
    ctx.beginPath();
    if(activeShape==='circle'){ctx.arc(SIZE/2,SIZE/2,SIZE/2-1,0,Math.PI*2);}else{ctx.rect(1,1,SIZE-2,SIZE-2);}
    ctx.strokeStyle='rgba(255,255,255,.85)';ctx.lineWidth=2;ctx.stroke();
  }
  function wireOnce(){
    if(wired)return; wired=true;
    const {cv,zoom,done,cancel,dlg}=els();
    if(!cv)return;
    let dragging=false,lastX=0,lastY=0;
    cv.addEventListener('pointerdown',e=>{dragging=true;lastX=e.clientX;lastY=e.clientY;try{cv.setPointerCapture(e.pointerId)}catch(err){}});
    cv.addEventListener('pointermove',e=>{if(!dragging||!state)return;state.x+=e.clientX-lastX;state.y+=e.clientY-lastY;lastX=e.clientX;lastY=e.clientY;draw();});
    cv.addEventListener('pointerup',()=>dragging=false);
    cv.addEventListener('pointercancel',()=>dragging=false);
    cv.addEventListener('wheel',e=>{if(!state)return;e.preventDefault();const d=e.deltaY<0?1.06:0.94;state.scale=Math.min(state.minScale*4,Math.max(state.minScale,state.scale*d));zoom.value=state.scale;draw();},{passive:false});
    zoom.addEventListener('input',()=>{if(!state)return;state.scale=+zoom.value;draw();});
    done.addEventListener('click',()=>{
      if(!state){dlg.close();return;}
      const out=document.createElement('canvas');out.width=512;out.height=512;
      const octx=out.getContext('2d');
      const ratio=512/SIZE;
      octx.save();
      if(activeShape==='circle'){octx.beginPath();octx.arc(256,256,256,0,Math.PI*2);octx.clip();}
      const w=img.width*state.scale*ratio,h=img.height*state.scale*ratio;
      octx.drawImage(img,(state.x*ratio)-w/2,(state.y*ratio)-h/2,w,h);
      octx.restore();
      out.toBlob(blob=>{
        finished=true;
        if(blob){const f=new File([blob],'crop.png',{type:'image/png'});if(activeCb)activeCb(f);}
        dlg.close();
      },'image/png',0.92);
    });
    cancel.addEventListener('click',()=>dlg.close());
    dlg.addEventListener('click',e=>{if(e.target===dlg)dlg.close();});
    dlg.addEventListener('close',()=>{if(!finished&&activeCancel)activeCancel();});
  }
  window.C31Crop=function(file,opts,cb,onCancel){
    activeShape=(opts&&opts.shape)||'circle';activeCb=cb;activeCancel=onCancel||null;finished=false;
    const {dlg,zoom,cv}=els();
    if(!dlg||!cv)return;
    wireOnce();
    cv.width=SIZE;cv.height=SIZE;
    const reader=new FileReader();
    reader.onload=e=>{
      const im=new Image();
      im.onload=()=>{
        img=im;
        const minScale=Math.max(SIZE/im.width,SIZE/im.height);
        state={scale:minScale,minScale:minScale,x:SIZE/2,y:SIZE/2};
        zoom.min=minScale;zoom.max=minScale*4;zoom.step=((minScale*4-minScale)/200)||0.001;zoom.value=minScale;
        draw();
      };
      im.src=e.target.result;
    };
    reader.readAsDataURL(file);
    dlg.showModal();
  };
  window.wireCrop=function(inputId,previewId,shape){
    const input=document.getElementById(inputId);
    if(!input||input.dataset.cropWired)return;
    input.dataset.cropWired='1';
    input.addEventListener('change',function(e){
      const f=e.target.files&&e.target.files[0];
      if(!f)return;
      window.C31Crop(f,{shape:shape},function(cropped){
        try{const dt=new DataTransfer();dt.items.add(cropped);input.files=dt.files;}catch(err){}
        if(previewId){const p=document.getElementById(previewId);if(p){p.hidden=false;p.src=URL.createObjectURL(cropped);}}
      },function(){input.value='';if(previewId){const p=document.getElementById(previewId);if(p)p.hidden=true;}});
    });
  };
})();
</script>"""

FLASH_JS = "setTimeout(()=>document.querySelectorAll('.flash').forEach(f=>{f.style.opacity=0;f.style.transform='translateX(40px)';setTimeout(()=>f.remove(),500)}),4200);"

TEMPLATES = {
    "base.html": """<!doctype html>
<html lang="{{ lang }}"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{% block title %}C31K Prime{% endblock %}</title>
<link rel="icon" type="image/jpeg" href="{{ url_for('favicon') }}?v=2">
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
  <form class="pf-search" method="get" action="{{ url_for('profile_search') }}">
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
    <input type="text" name="q" placeholder="{{ t('profile_search_ph') }}" value="{{ request.args.get('q','') if request.endpoint == 'profile_search' else '' }}" autocomplete="off">
  </form>
  {% endif %}
  <div class="right">
    {% if me %}
    {% if is_admin %}<a class="btn admin" href="{{ url_for('admin') }}">{{ t('admin_panel') }}</a>{% endif %}
    {% if is_moderator %}<a class="btn admin" href="{{ url_for('mod_panel') }}">{{ t('mod_panel') }}</a>{% endif %}
    <div class="menu-wrap">
      <button class="profile-btn" id="pbtn" type="button" aria-expanded="false" aria-haspopup="true">
        <span class="mini">{% if me.avatar %}<img src="{{ media(me.avatar) }}" alt="">{% else %}{{ me.display_name[:1]|upper }}{% endif %}</span>
        <span>{{ t('profile') }}</span>{{ badge_html(me.badge) }}<span class="caret">▾</span>
      </button>
      <div class="menu" id="pmenu" hidden>
        <button class="item" type="button" data-dlg="dProfile">{{ t('profile') }}{{ badge_html(me.badge) }}</button>
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
  <h2 style="margin:0">{{ me.display_name }} {{ badge_html(me.badge) }}</h2>
  <p class="muted" style="margin:4px 0 0">@{{ me.username }}{% if is_admin %} · {{ t('admin_badge') }}{% endif %}{% if me.badge %} · {{ badge_names(me.badge) }}{% endif %}</p>
  <div class="acts" style="justify-content:center;margin-top:18px"><button class="btn" type="button" data-close>{{ t('close') }}</button></div>
</dialog>
<dialog id="dAvatar"><h2>{{ t('add_profile') }}</h2>
  <form method="post" action="{{ url_for('set_avatar') }}" enctype="multipart/form-data">
    <img id="avPrev" class="preview" style="width:120px;height:120px;border-radius:50%;margin:0 auto 10px;display:block" alt="" hidden>
    <input id="avIn" type="file" name="avatar" accept="image/png,image/jpeg,image/webp,image/gif" required>
    <small class="muted">{{ t('customize_photo') }}</small>
    <div class="acts"><button class="btn" type="button" data-close>{{ t('cancel') }}</button><button class="btn solid">{{ t('save') }}</button></div>
  </form></dialog>
<dialog id="dCrop" class="cropdlg">
  <h2>{{ t('customize_photo') }}</h2>
  <div class="cropstage"><canvas id="cropCv" width="280" height="280"></canvas></div>
  <input type="range" id="cropZoom" min="0" max="1" step="0.001" value="0">
  <div class="acts" style="justify-content:center"><button class="btn" type="button" id="cropCancel">{{ t('cancel') }}</button><button class="btn solid" type="button" id="cropDone">{{ t('save') }}</button></div>
</dialog>
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
if(window.wireCrop)wireCrop('avIn','avPrev','circle');
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
<link rel="icon" type="image/jpeg" href="{{ url_for('favicon') }}?v=2">
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
  <h2>{{ t('add_badge') }}</h2>
  <form method="post" action="{{ url_for('admin_set_badge') }}">
    <div class="two">
      <div><label>{{ t('select_badge') }}</label>
        <select name="badge">
          <option value="moderator">{{ t('moderator_badge') }}</option>
          <option value="bughunter">{{ t('bughunter_badge') }}</option>
        </select>
      </div>
      <div><label>{{ t('username') }}</label><input name="username" required></div>
    </div>
    <button class="btn solid">{{ t('assign_badge') }}</button>
  </form>
  {% for u in badged_users %}
  <div class="arow">
    <div><strong>{{ u.display_name }}</strong> {{ badge_html(u.badge) }}<small>@{{ u.username }} · {{ badge_names(u.badge) }}</small></div>
    <div class="acts">
    {% for b in badge_list(u.badge) %}
    <form method="post" action="{{ url_for('admin_set_badge') }}">
      <input type="hidden" name="username" value="{{ u.username }}"><input type="hidden" name="action" value="remove"><input type="hidden" name="badge" value="{{ b }}">
      <button class="btn danger sm" title="{{ t('remove_badge') }}">✕ {{ t(b + '_badge') }}</button>
    </form>
    {% endfor %}
    </div>
  </div>
  {% endfor %}
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
if(window.wireCrop)wireCrop('thIn','thPrev','square');
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
if(window.wireCrop)wireCrop('thIn','thPrev','square');
</script>
</main>{% endblock %}""",

    "mod.html": """{% extends 'base.html' %}
{% block title %}{{ t('mod_panel') }} · C31K Prime{% endblock %}
{% block content %}<main class="wrap">
<div class="panel" style="margin-top:20px">
  <span class="eyebrow">{{ t('mod_panel')|upper }}</span><h1>{{ t('add_game') }}</h1>
  <form method="post" action="{{ url_for('mod_add') }}" enctype="multipart/form-data">
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
  <h1>{{ t('ban_user') }}</h1>
  <form method="post">
    <label>{{ t('username') }}</label>
    <input name="username" required>
    <button class="btn danger" style="margin-top:6px">{{ t('ban') }}</button>
  </form>
</div>
<div class="panel">
  <h2>{{ t('banned_users') }} ({{ banned|length }})</h2>
  {% for u in banned %}
  <div class="arow">
    <div><strong>{{ u.display_name }}</strong><small>@{{ u.username }}</small></div>
    <form method="post" action="{{ url_for('mod_unban', username=u.username) }}"><button class="btn sm">{{ t('unban') }}</button></form>
  </div>
  {% else %}<p class="muted">{{ t('no_content') }}</p>{% endfor %}
</div>
<script>
const $=id=>document.getElementById(id);
function sync(){
  const k=document.querySelector('[name=source_type]:checked').value;
  $('linkIn').hidden=k!=='link';$('linkIn').required=k==='link';
  $('fileBox').hidden=k==='link';$('fileIn').required=k!=='link';
}
document.querySelectorAll('[name=source_type]').forEach(r=>r.addEventListener('change',sync));sync();
if(window.wireCrop)wireCrop('thIn','thPrev','square');
</script>
</main>{% endblock %}""",

    "profile_search.html": """{% extends 'base.html' %}
{% block title %}{{ t('profile_search') }} · C31K Prime{% endblock %}
{% block content %}<main class="wrap">
<div class="panel" style="margin-top:20px">
  <h1>{{ t('profile_search') }}</h1>
  {% if q and not users %}<p class="muted">{{ t('no_users_found') }}</p>{% endif %}
  {% for u in users %}
  <div class="arow">
    <div style="display:flex;align-items:center;gap:12px">
      <span class="mini" style="width:36px;height:36px;border-radius:50%">{% if u.avatar %}<img src="{{ media(u.avatar) }}" alt="">{% else %}{{ u.display_name[:1]|upper }}{% endif %}</span>
      <div><strong>{{ u.display_name }}</strong> {{ badge_html(u.badge) }}<br><small class="muted">@{{ u.username }}</small></div>
    </div>
  </div>
  {% endfor %}
</div>
</main>{% endblock %}""",
}

# Site ikonu / logo (favicon.jpg dosyasına gerek yok, görsel buraya gömülü)
FAVICON = base64.b64decode("""
/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAQDAwMDAgQDAwMEBAQFBgoGBgUFBgwICQcKDgwPDg4MDQ0PERYTDxAVEQ0NExoTFRcYGRkZDxIb
HRsYHRYYGRj/2wBDAQQEBAYFBgsGBgsYEA0QGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBj/wAAR
CAEAAQADASIAAhEBAxEB/8QAHQAAAgEFAQEAAAAAAAAAAAAABgcFAQIDBAgACf/EAE4QAAEDAwIEAwQHBAcFBgUFAAECAwQABREGIQcSMUET
UWEUInGBCCMyQmKCkRVScqEWJDNjorHBNENTc5JUg7LR4fAXJTWjs0STwsPT/8QAGgEAAgMBAQAAAAAAAAAAAAAAAQQAAgMFBv/EACkRAAIC
AQQBBAICAwEAAAAAAAABAgMRBBIhMUEFEyJRMmEjgRRxkbH/2gAMAwEAAhEDEQA/AMVhZ3TgYOaYDDZ8BOfKgiwfZSaO46h4CfhXo2LMqEcp
q7O2K8TVCT2FABXvXj0q2q4NQhjWnnqqY+e1ZEJ86yg4o5IajrJ5MCg69xSXFkA9KOlDOaHbs1lalY7VMkFjdGSEHyqHaaUR0omuzeELGNqi
GWsoGBUZAk042coopvac23B8qgNPo5Al1ZCUJ6qUcAUxJ2idZTtMqucXTUlcXkLgBcSl5afNLJ94+YGxPlVJ2Rhjcyyi5dI54vrQ9vV8KgHE
5OKLtQMKTcFBSCkglJBGCCDuCOx9KG3GQFnFDvkGDXit5lo2700tItbpIpeQWMykKPSmzpGKAhOOwzVo8E6DSOyoR07dqyCKck+fnU/pvTs7
UdzTBhq8JhvCpUvGfASegA6Fauw7Dc9sm154VwzBDumpLkSYkZLct1TrUjb72d0K/EnbzBpeerhXLazWFE5R3IVSoxxjYVjMXfrvUg+29GuU
i3S2HI8yMQHY7owpOeih+8k9lDY1by5OwplSTWUZNNcM0Vs8qNutBOp2CWFfOmKpr6snHagvUjJLRzVkyIUM5g+0qrXSwT2qbuEf+sqIrXbZ
8xtVMACjSrICW9u1G3gDloZ0uyMtgCjgRjyZx8KuQiCwMnJqwsjI2qTXHx2rAWTkbVCFjLdecbrbZawdxXnWxnOKhAdsSSEpxRtHSfBTQbYs
eCnzo3YH1CfhQYWXYFUKfjV4BzWVLZIoAMPIKyJA8qyhnbeqFGDUIY1YHSrQcmr1j3TWJIINQhkwDUJdUfa32xU+2nPaoq7sjlV22qEFpdW8
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
8qslJ6jKh0JqRxvtWTeXkiyf/9k=
""")

BADGE_MOD = base64.b64decode("""
iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAAUZklEQVR42u1aa4ycV3l+zu27zW137fXaIXGMjYm9uRQIl4JEvUpJ
VCKBSptxhYpQi5Aq8QPRCqSKH511VBVVRdBKNFVRKyhJKHgpBCFyJ/baSUjiOHZCvLaTOL57vV6vd3d25ruca3+cb8YGinCSDZHS
HGmk1ezO7Hnf87zv+zzP+YC31lvrrfX/eZE36v82t2+nTQATEwAwgYmJCQvAvamz3Wq1aGvHDk7Ir+edEIJWq8WBFn0zIYC0Wi0y
NjZGx8bGDCGkd8LBF778lY9SQj997NhRZFnnzvt+eNd9AAoAcM6RsbFxNjYGu23bNvd6ImN5E+AcaW7dSkc/9zkyPjbmGKPG2ot7
f/9NzdFPfOyW24ZXrvykCMJNJ8/M4OixY5B5ipDTw0Wefu/UqWMTOx74yYFLv7a5fTs7d+AAWTU15SYmttty2+6NSABptVpk6tpr
SRPA8PAwAYCxsTHHOTPW2F/dVfSRj3/mui1/8IGPvPv6zbeuHln1QcYD/tzzU3hizz57ZvqMGx4ehpSKaCWp4BR5npmiKJ5ot9v3
LrbnH967+8FfAMh+tVxuu+377NzoAQIAq6audcAERkdH3StFzHKXwMjNf/Lpaz74vvfeOHrNxg9uWHfVjWuvumL9qpWDmJvv4sFH
dmPXYz/X0zOz1FpLO+1F5FkX9UYDtcaQtcZaKQteqzdQq9cxf2EOWsljgRBPS1k8Obcwv3dm+tThw/seP/NGlQD/29bX1oowGI7i
aE0lCq6sV6vravXKhuEVg+uHBhvrrlizur5isA4AmDk/j6mDL+DZAy/oo8dPkqKQNAhCQhnDwmIbZ06dwNlTx9HtLAAOaAyuwNDw
iAvjih1oDLpao8EJIQiDEG+7YjWyLMX02ZlOt9M5lmXpUWvtEQIcdSCnOSNnR4ZHzn74/dcc27p1q1nWBDjnKCHEfu9HD965bu1V
f7ZisCHevm4N2CWfXuykmJ2dw+z5edfNctPppCTPc5LEMW006uCMYWZ2DidOT+P09AxmZs5h9twMZs6cQJZ2oIocWknwIMTVGzaj
MTgEpRQa9bqtVStu9eoRNzjQoIVUNMsVtHFwzkJpBWMstFYIg1CdO3vmu9+64yt/0Wq16LZt2+xvPdHLSQCl1ALAYKP2npGVQ+KR
3Y+bJ/cKcEpd+XuSJBEZGhwgI6tWkBVDg7xWrYASwDlgdq6N4ydOoygKKOk3bIyBswacCzDGYRgDR4AwjBCEIRqNAcydn8WpUydp
klSgrMNSJ4XR0jHKXZzErlKpOIcAi4ttAkpdFMciy7P3AcDtt99uLwvSl4cS5wBQSihJ88wJzsmqFSvp5k0bsGKojjgMfu1Dxlpk
hYFUGlmWQxsD5xyMNTBGwxgDgIBQCkopGBcghICJAEpKpFkKQgicMeBCQIgA3axAu90mSkoiAoEoisAYg5QK9WrFrl4dOwfqABDn
GyH5bQ2RX14JAAAECBIpFXnnhnVueHglVq0cghAUxlq48o8IIegRHUoJCAicLyMYa6G0gbUOjDHQ/ouDWgtCKIQIwIVAnmXI8wyM
Cwgu4JwDAUAJAWMUlBIYraG1hrUW1lmEghNKaAJAwDl5Wei+3OhXXPOhwDoXGWOQS4VOp4s8L6C1BUBAy5O8lOUZ4yCVgpQSzsH/
HoAtkQA4cC5AKQPnHEEYgosA1jrAORBCEYQBwigCJbQM1sA6C0oZwihCGATgnIMSCsooGGfhyMiIuNwm+FsT0MPPjTdsFtYYoY3B
UqdL8kJC697Ju/8zb71TN8ZCKgmlFGyZUEpoHzEiEAiCCGGUgDEOOAfKGBhj4MIHyDmHgyuRQEAJBeccYRhC8ACEEkIJASU0XLVq
Y3DZY+2ySZ4W3AHMOQdjDJRSKKREqAQo5fhVeu8cYK2FVhpKKegS+pRScMZAKAFlDEIEMFrBMYCyi0iglEIEgf97IRCGAbTRyPPc
J0RwhEEAQn0JUuoRIARnQcDYxeMjy5OAgVosnHPcH60/Oa0NjLFlYATOAYRcPH2pNLppjjTLkRdF2QANpFSwxoJRBi4EmBJQUoIQ
Asq4b4aMgzF/8rVqDWEYwTlAKQVjDMIwRBxHcM5Baw3GGJgvQ9a4Yg3D3mUqgfHxcQIAg2vqDA7MOWChvYQsy6G0htIa1rp+E+xV
g7UOUvr6L4oCeV4gywoopf2hEJ+ki03TwfYmhNYghCAIQ4RRhFqtioGBOpIkRqVSQbVaRa1aRRLHiKMQgbjYRwghXBUu8OpznCxb
CSRRg1tnmQNQSIlON0VeFIhkiEBwANx3/RICSvvT9iTF/2ydg1LK9wJj+1NWKwWtFYjRoJRBmzIBQYDBgQHU6zVUkwSCCzDGoJRG
FIUIoxDWGERSwzqLwDdEYm2XLHsPiELBnXPMubIRUQJZSORZDsE5PFHw44mW5aG0gdIKsuwXWZYjywsUeQ6tVb9eCKWw1gLOwWgN
OMA5iyiK/GjkHJVKAi4kdIk6ITiSKEIhJUTAy5KiIARsoD7Mlz0BSRwTYywx1qKbZsiyHIVUkEpDSgmQ3oz2/afTTdFuL6GbZsiL
wqPAWqCs7x5S/OjEJZ3fI4mXpx2GASpJgnq9iqJQgAOklAiCoEQewBmHgQbnDJRSYoRZfgQwY4jWGs46MMqQ5QW63W4v60jKDi8E
h3POJ6iQ5ex2SLMc3W6KbreLLEuhtYZzDtZZOOtKRsggggBBGJVNLkajUUetVkUSxZ4QWQulA1DKwCiBNgac+9EphADjHECEZU/A
klQ6L6Q11jBCPHqlUsjyHJT6Xso5hzYewr4ENIpCQkoJpTTSLEeWpijyHMYo3zythYMrR1sIzjlEEPoGGAQIgxCMUsRxjLCk03le
+DFKACkV4jiGCQQ4ZyBwTinllj0B56dn8+KqKwpnbdJNM4AAtWoFhBBwzj1p4f40rLNIswx5XqCQCrmUMNZ4XqAVlCzQIw6EMlDK
4KhDGEYIw8gngXuuX60kqNeqiKOwZJC+1m05QYyxWOp2AecRJJWWrugUy8YEx8fHHQAspu1UKp2DEL8R7ed5lhfIiwJ5npcBS8hC
QRYSsuz4xljkhUS320Xa7UAb7U/eeSO4x/8JIaAlEQqjELVqBY16DQMDDSRJgigKUUkSJEmMKAxKhAgwSpEkMSihMEZnc6fOdQGg
dIdeWwJ63H7iP59pK6UuAARxFDrnHJTyqq4oJPLCB6+kKlmi6td/X7BYix5zds752geBtRaEEIRRjCSpoFFvoFatlWONgRDSDz6K
QoSBf0VhWCJGoJLEzliLLMvnn312cmnZEOCFnKPApJZSniSEIgyEy2V5wtpDu6fxjTHQxvR8BIA4MEY9bK3pi6ser9daeznMeCmL
ORjnqFYThGGAMPSBJlGIWiVGpfd+FEIIASE4ojBAvVZz2lgURXEKgGm1WvRyvMHL8uDHx3dSAMiy/EXnHOIocgSkH4A2pk9zjbWw
xkIbjUKqPhHSxiIMo7JL+61Z51HxS/t0DqzUC0kUIY6iMhEMUcgQBQGiKEIUhggCAc5986vXKi7LC+RZ9gIA7LzM2C7zEmInAKCb
dp/LiwLVSgLGKJTW/RPXWnvlpzW08ZB3KBOkdSlh/Qn3ysqTKl8WXvkJsHL+Bz2ZSwlY2fFDQRFHAlEYIAgEgiAAJQRBIJAkMRYW
F7HUbj93yZaXZwpMTV3rAOD8+cX9Fy7MY6BeY4QQ3wMCA600NGOgVPdlrrVeNTrnx2NQmhpaqf73WmP79d9HBhystSiKAowzRGGE
OArBqZ+9jFJwwUtZrQFCkMQJOBfs3Mw5nD17Zj8ArFo15ZYNARMTTQsAh/cfmTo/N3chiiNCKXWFlJBSl1RXefGjJJRWXiiVLLHT
TdHppqUx4vrGSU9CE3qRuFlrYUp3iMJPHBACwb2Z4jkI6esISgkqldhRSsnsuZm5A08/M+X3PGGXsQSIc87RRx/97nx7qbtfCIEk
jq0xFtoYr/TyHHkhIZUP3EPf9CWzMQZ5lvmaJ74JGmtAAHAmwEUAITysBeeQSqKbppCF5xK2lNqUkr7o8gqUoFGr2DTLMTt3fj+w
uNBquctqgK8gAcD4+DgFgPmFhR2dTopGveoKKZFmGaRS/VrX2kAq5ZNREiBCACE4uBAXxx8hIIQChIBQ0u8BnvxUUK9W/byPI1Ti
EA49J8mr6d5YJQRI4tCdPj2N02emd/ryH6fLOQbLPuBranp6+oHT09OoVSuMgKCQHv5ZXiDNck97tYZWvjTSrEA3zbCw2Ea324Er
ax6lR8gY89RZSWilYYyFI0AQekRY66BK39FYh1xaFD2uURRgFCCEsqlDh92Rg4ce8FdlU8tPhScmJoxzjhAytm9006ZD69et3VSr
VuyFhQWqAtE3nhjzQclLyJDSflJcapj27HBX2llcBAjC0twgBKrUGXmeYamTIokDxKGAsbY0WnyfiQJhlbb06MtHDp06un+f3yMx
y46AsgwYMKnnLlz4YZrlWDE0YKX0IqcoBU8hVWmByf6I9Jcg/uQ9OSqTVeqA/kQo+USeF0jT3DdR1UuiRSY12m3fUAvplWa1Ettj
J07j8AuH7wGgx8bH2SuJ6ZU+jGAB4OT07F1Hjx3Xg406i6LQq7yyBLIsR9FrinlRnpR3g5xzQNnFKaV9FHgLXaOQRV9DOGehlD/p
oiggpUaWK6S5//40zUDhQClne57eaw48veduAJjcBvu6JWDbtm225Ry9585/OXjqzNlHHKEYGV5hpNLIC4W8KLwR0k19MsqpYI3p
Oz295kdK94jQsps7B6MU8iKHktKbLnmOLM/RTdOyh2Q++Mz/rlaNzeyFRezbt/eR+flTz7eco8C2V5QA/goRgKmJCQIAMzMzXz95
6swtI6uGcPL0WeSFBJxDmuYlvfVD25UWOWMchFIAF10jRxkAAmc9I/R2u0Re5Oh0uoijCIEQiKMIlUoFSmt0Oh0stpdQrcSIkwQ/
2/UIeXb/vq8BwM6xMdpD6etVApjYutW0Wi161ze/+uDLx4495Ryhq0dWmjzP0UlTLHU6WGgvYqnTLa0wDWMNHLwoooyBlJSYMlb2
BM/yCPHKUEo/Qpe6XSx1uuh2UywtddDpdNHudJBmGYaHGmZ2bpE++ujup49M7Xmw1WrRyclJ/UrjeVUPJE1NXUsA2DPnzrWOnTxN
Vg0PoVqJ0W63sbS0hHa7jTTreh5QqkX/Ku8ES/Xnb3xE+Z5/vyewlNawxqIoPJNcbLexsLiI83PzGBqoIQgj7Nz1KNm3d08LgJ2a
mnpVD3uwV5eACddsNtn2O//jxQ2b3/37K1esfGetGpsTJ0/TpfYiTOn3ERBQykrN7wWTN0Auih7KKNB3mr2rw8obIsD5yxZKEAYB
Ot0UzllsXH+1efnEGfaDie899NSu+/6u2WyyiYkJ82piedWPpI2Ojjo4R+bm577wwotHCsYE1l55hUvT1I8/XV6J9a7C+9YZhxAB
ojBCFMUIwxBBGIEL0UeAUhJ5nkMqVZqoORYX22gvLeHqK1e7difH/fffL/ftfeKvnXNkdHT0VT8wxV7tBycnJ13z2mvZxJe/OHvN
9e/VjrKbBxsNo5Si58/P9p0cf/vrx5113s8LwhBRFCMQQT/oS90n4CLX1+UtUVZIrH3bCIZXrjS7Ht/Ddz7ywLYXfrHnB1NTU+yO
O+6wv/MElBMBzeZ2dve3P//Ypuvee5Mj7O1vWzNiFhYWaaezhCAoXV4RglFPeRnj/oorKZ0dEfzSFabPhdcKPWepKCRGhoewaeN6
8+zUS/xnD/z08V0P/egzzWaTXq7qW/YS6O13dPSAA+DybP5TF+bmzi+0u/T3brjexnGCPMtK+esfauCCIwgDVCoJatUEtWrF/1yp
IEkqCKMIIhD+xrdkj4uLiwgDjhtGr7GHjpyku3ftmDt0+Pk/J4SYiYnR1/wQJXuNCfCl0Gyy/77zWwsbN40+Yxz5VBTFWLNmhMxd
mCeUsvIOX4Ay6v29OEa1kiCOQoRhAEL8IzW9ewJT2mSdThe1Soybb/qwO3lmzu3Z8xQ5+Iv9nzj83BN7m80mm5q6w77W/b/mBPSU
4pYtLf7TH3/1yNp1G08wEX5i5YoVZmhoiCy220QI0Vd3cRyhksSo1ypI4hhhIEqpS/oPVDjn0G63Ua9X8bE/utkdOT5tDh48xI8f
OfhXT+66f2LLli383nvvNcux92VJAAAcPz5pt7Ra/IF//foza658+5II449eecVqm8QRWVzqkDiKEEXe3W00aqhXKkjiCEIIcOYv
Ory77HDhwjxGhofw0Y/c5J49+JI9cuRlfn76zBcfvvf739iypcUnJ/9LL9e+ly0BAHB80ifhoTv++bHa0Kp5EUS3vvMd60kliU27
k9IoDJDEMSpJjFq1giAIwJhvQ85aZHmBCxcW8Y71V+FDH3iffXLf8/Sll16i3YXzf3PfPd/52pYtW5Y1+GVPQD8JW1p8x4Pf+LmI
Gi9YQm69bvPmYOXQgF5a6lLBGYYGG6hUEj8CASil0el2obXBje8axbq1V+ldT+xlLx4+VKRL83/5wI/v/qYPflIv936XPQH9ctjS
4rt3/NtzncI9rLX58HWjm1dds2GdyQuFQkpSq1YQBoH3CvMC9VoF77p+kwWofWjn4/z55/a/uLg4+8eT9//w3tcr+NctAReTsIU/
/fPJk/ue2n23FbXVjUbj3e9/z7tIo17TM7NzpJCSRGGAq69c4zZuWGeOnzzL/ueen9C9ex6/6+jBw7cdePaxF1/P4H8nq9ls9h8p
vuEDt/zp5790+5Edjz7jXj4+4x7e/bR66ehpdfTEOffv3/6Bu+Xjn3z5qnWbt176WbxJFtm+fXsvmIGbbm3+w9//0x3d/c+/7HY8
+oz77Oe+lG4cfc8/Ahi8JHCCN9tqNpusx/eD6ppNH9/62W/f+KE//A6A0b4WeBOd+m9EQ/MiGvoi6E176r9xtVp0+/btrAyc4q31
1nprvbXegPW/tdgOsmUxkmYAAAAASUVORK5CYII=
""")
BADGE_BUG = base64.b64decode("""
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
""")
BADGE_IMAGES = {"moderator": BADGE_MOD, "bughunter": BADGE_BUG}

app.jinja_loader = DictLoader(TEMPLATES)
init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)),
            debug=os.environ.get("FLASK_DEBUG") == "1")
