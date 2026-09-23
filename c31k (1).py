"""C31K Prime - tek dosyalık Flask sitesi (backend + şablonlar + CSS hepsi burada)."""
import os
import re
import sqlite3
import time
import uuid
from functools import wraps
from pathlib import Path

from flask import (Flask, flash, redirect, render_template, request, send_file,
                   send_from_directory, session, url_for)
from jinja2 import DictLoader
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
# Render'da kalıcı disk kullanırsan DATA_DIR'i o diskin yoluna ayarla (örn. /var/data)
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "c31k.db"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")
app.config.update(MAX_CONTENT_LENGTH=250 * 1024 * 1024, SESSION_COOKIE_SAMESITE="Lax")

IMG = {"png", "jpg", "jpeg", "webp", "gif"}
ARCHIVES = {"zip", "rar", "7z"}
FILES = IMG | ARCHIVES | {"exe", "msi", "iso", "apk", "pdf", "txt"}
CATEGORIES = {"tools": "Tools", "games": "Games", "vpns": "VPN'S"}
ACTIVE_WINDOW = 60  # saniye: bu süre içinde hareket eden kullanıcı "online" sayılır
NEW_WINDOW = 3 * 24 * 3600  # 3 gün içinde eklenenlere "NEW" rozeti


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
        CREATE TABLE IF NOT EXISTS active_users (
            token TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            last_seen REAL NOT NULL
        );
    """)
    if not conn.execute("SELECT 1 FROM users WHERE username='admin'").fetchone():
        conn.execute(
            "INSERT INTO users(username, password_hash, display_name) VALUES (?,?,?)",
            ("admin", generate_password_hash(os.environ.get("ADMIN_PASSWORD", "1234")), "C31K Admin"),
        )
    conn.commit()
    conn.close()


# ───────────────────────── yardımcılar ─────────────────────────
def save_upload(f, folder, allowed):
    """Dosyayı uploads/<folder>/<uuid>/<orijinal_ad> olarak kaydeder, göreli yolu döner."""
    if not f or not f.filename:
        return ""
    name = secure_filename(f.filename) or "dosya"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in allowed:
        raise ValueError("Bu dosya türüne izin verilmiyor.")
    target = UPLOAD_DIR / folder / uuid.uuid4().hex
    target.mkdir(parents=True, exist_ok=True)
    f.save(target / name)
    return f"{folder}/{target.name}/{name}"


def remove_upload(rel):
    if rel and not rel.startswith("http"):
        try:
            p = UPLOAD_DIR / rel
            p.unlink(missing_ok=True)
            p.parent.rmdir()
        except OSError:
            pass


def media(path):
    """Yüklenmiş dosya ise /uploads/... adresine, dış link ise olduğu gibi çevirir."""
    if not path:
        return ""
    return path if path.startswith(("http://", "https://")) else url_for("uploads", filename=path)


def is_http(url):
    return url.startswith(("http://", "https://"))


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
        if session.get("username") != "admin":
            flash("Bu sayfa sadece admin için.")
            return redirect(url_for("index"))
        return fn(*a, **k)
    return wrapper


# ───────────────────────── anlık kullanıcı sayacı ─────────────────────────
def touch_active():
    if "username" not in session:
        return
    token = session.setdefault("active_token", uuid.uuid4().hex)
    now = time.time()
    conn = db()
    conn.execute(
        "INSERT INTO active_users(token, username, last_seen) VALUES (?,?,?) "
        "ON CONFLICT(token) DO UPDATE SET username=excluded.username, last_seen=excluded.last_seen",
        (token, session["username"], now),
    )
    conn.execute("DELETE FROM active_users WHERE last_seen < ?", (now - ACTIVE_WINDOW,))
    conn.commit()
    conn.close()


@app.before_request
def track():
    if request.endpoint not in {None, "static", "uploads", "favicon"}:
        touch_active()


@app.context_processor
def inject():
    conn = db()
    online = conn.execute(
        "SELECT COUNT(*) FROM active_users WHERE last_seen >= ?", (time.time() - ACTIVE_WINDOW,)
    ).fetchone()[0]
    counts = {r[0]: r[1] for r in conn.execute("SELECT category, COUNT(*) FROM content GROUP BY category")}
    me = None
    if "username" in session:
        me = conn.execute(
            "SELECT username, display_name, avatar FROM users WHERE username=?", (session["username"],)
        ).fetchone()
    conn.close()
    return {"active_count": online, "me": me, "categories": CATEGORIES, "counts": counts, "media": media}


# ───────────────────────── sayfalar ─────────────────────────
@app.route("/")
@login_required
def index():
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    sql, params = "SELECT * FROM content WHERE 1=1", []
    if q:
        sql += " AND (title LIKE ? OR description LIKE ? OR category LIKE ?)"
        params += [f"%{q}%"] * 3
    if category in CATEGORIES:
        sql += " AND category = ?"
        params.append(category)
    conn = db()
    items = conn.execute(sql + " ORDER BY id DESC", params).fetchall()
    conn.close()
    return render_template("index.html", items=items, q=q, active_category=category,
                           now=time.time(), new_window=NEW_WINDOW)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        conn = db()
        user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        conn.close()
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            session.clear()
            session["username"] = username
            session["active_token"] = uuid.uuid4().hex
            return redirect(url_for("index"))
        flash("Kullanıcı adı veya şifre yanlış.")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not re.fullmatch(r"[\w.-]{3,20}", username):
            flash("Kullanıcı adı 3-20 karakter olmalı (harf, rakam, . _ -).")
        elif len(password) < 6:
            flash("Şifre en az 6 karakter olmalı.")
        else:
            try:
                conn = db()
                conn.execute(
                    "INSERT INTO users(username, password_hash, display_name) VALUES (?,?,?)",
                    (username, generate_password_hash(password), username),
                )
                conn.commit()
                conn.close()
                flash("Hesap oluşturuldu. Giriş yapabilirsin.")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("Bu kullanıcı adı zaten alınmış.")
    return render_template("register.html")


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    conn = db()
    user = conn.execute("SELECT * FROM users WHERE username=?", (session["username"],)).fetchone()
    if request.method == "POST":
        name = request.form.get("display_name", "").strip()[:40] or user["username"]
        avatar = user["avatar"]
        try:
            new = save_upload(request.files.get("avatar"), "avatars", IMG)
            if new:
                remove_upload(avatar)
                avatar = new
            conn.execute("UPDATE users SET display_name=?, avatar=? WHERE username=?",
                         (name, avatar, session["username"]))
            conn.commit()
            flash("Profil güncellendi.")
        except ValueError as exc:
            flash(str(exc))
        conn.close()
        return redirect(url_for("profile"))
    conn.close()
    return render_template("profile.html", user=user)


@app.route("/admin", methods=["GET", "POST"])
@admin_only
def admin():
    if request.method == "POST":
        f = request.form
        title, category = f.get("title", "").strip(), f.get("category", "")
        kind = f.get("source_type", "link")
        try:
            if not title:
                raise ValueError("Başlık gerekli.")
            if category not in CATEGORIES:
                raise ValueError("Geçersiz kategori.")
            if kind == "link":
                source = f.get("source_url", "").strip()
                if not is_http(source):
                    raise ValueError("Link http:// veya https:// ile başlamalı.")
            elif kind in {"zip", "file"}:
                source = save_upload(request.files.get("source_file"), "content",
                                     ARCHIVES if kind == "zip" else FILES)
                if not source:
                    raise ValueError("Dosya seçmelisin.")
            else:
                raise ValueError("Geçersiz aktarım türü.")
            thumb = save_upload(request.files.get("thumbnail_file"), "thumbs", IMG)
            if not thumb:
                thumb = f.get("thumbnail_url", "").strip()
                if thumb and not is_http(thumb):
                    raise ValueError("Thumbnail linki http:// veya https:// ile başlamalı.")
            conn = db()
            conn.execute(
                "INSERT INTO content(title, category, description, thumbnail, source_type, source, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (title, category, f.get("description", "").strip(), thumb, kind, source, time.time()),
            )
            conn.commit()
            conn.close()
            flash("İçerik eklendi.")
        except ValueError as exc:
            flash(str(exc))
        return redirect(url_for("admin"))
    conn = db()
    items = conn.execute("SELECT * FROM content ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("admin.html", items=items)


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
        flash("İçerik silindi.")
    conn.close()
    return redirect(url_for("admin"))


@app.route("/uploads/<path:filename>")
def uploads(filename):
    is_image = filename.rsplit(".", 1)[-1].lower() in IMG
    return send_from_directory(UPLOAD_DIR, filename, as_attachment=not is_image)


@app.route("/favicon.jpg")
def favicon():
    return send_file(BASE_DIR / "favicon.jpg", mimetype="image/jpeg")


@app.post("/heartbeat")
def heartbeat():
    conn = db()
    n = conn.execute(
        "SELECT COUNT(*) FROM active_users WHERE last_seen >= ?", (time.time() - ACTIVE_WINDOW,)
    ).fetchone()[0]
    conn.close()
    return {"count": n}


@app.route("/logout")
def logout():
    token = session.get("active_token")
    if token:
        conn = db()
        conn.execute("DELETE FROM active_users WHERE token=?", (token,))
        conn.commit()
        conn.close()
    session.clear()
    return redirect(url_for("login"))


# ───────────────────────── tasarım + şablonlar ─────────────────────────
CSS = """
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&display=swap');
:root{color-scheme:dark;--bg:#05070d;--panel:rgba(14,19,32,.66);--solid:#0d1422;--line:rgba(120,160,255,.16);--grid:rgba(120,160,255,.055);--text:#e9efff;--muted:#8592ae;--a:#22e3ff;--b:#8b5cff;--danger:#ff5d7a;--field:#080c15;--nav:rgba(5,7,13,.7);--thumb:#0b1120;--h1:#fff;--on-a:#04060c;--shadow:rgba(0,0,0,.45);--glow:rgba(34,227,255,.14)}
html[data-theme=light]{color-scheme:light;--bg:#eef2fa;--panel:rgba(255,255,255,.78);--solid:#fff;--line:rgba(40,70,140,.16);--grid:rgba(40,70,140,.07);--text:#0c1322;--muted:#5a6684;--a:#0a84ff;--b:#7c3aed;--danger:#e11d48;--field:#f6f8fd;--nav:rgba(238,242,250,.75);--thumb:#e2e8f6;--h1:#0c1322;--on-a:#fff;--shadow:rgba(30,50,110,.18);--glow:rgba(10,132,255,.12)}
*{box-sizing:border-box}[hidden]{display:none!important}
html{scroll-behavior:smooth}
body{margin:0;min-height:100vh;color:var(--text);background:var(--bg);font:15px/1.55 "Space Grotesk",system-ui,sans-serif;overflow-x:hidden;transition:background .3s,color .3s}
body::before{content:"";position:fixed;inset:0;z-index:-2;background:linear-gradient(var(--grid) 1px,transparent 1px) 0 0/48px 48px,linear-gradient(90deg,var(--grid) 1px,transparent 1px) 0 0/48px 48px;-webkit-mask-image:radial-gradient(ellipse at 50% 0,#000 15%,transparent 75%);mask-image:radial-gradient(ellipse at 50% 0,#000 15%,transparent 75%)}
.aurora{position:fixed;inset:0;z-index:-1;pointer-events:none;overflow:hidden}
.aurora i{position:absolute;width:520px;height:520px;border-radius:50%;filter:blur(110px);opacity:.3;animation:float 18s ease-in-out infinite}
.aurora i:nth-child(1){background:var(--b);top:-200px;right:-120px}
.aurora i:nth-child(2){background:var(--a);top:25%;left:-220px;opacity:.18;animation-delay:-7s}
@keyframes float{50%{transform:translate(40px,60px) scale(1.12)}}
a{color:inherit;text-decoration:none}button,input,select,textarea{font:inherit}
.muted{color:var(--muted)}

/* üst bar */
.top{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:20px;padding:12px 4%;background:var(--nav);backdrop-filter:blur(16px);border-bottom:1px solid var(--line)}
.brand{display:flex;align-items:center;gap:10px;font-weight:700;letter-spacing:.4px;font-size:17px}
.brand img{width:36px;height:36px;border-radius:11px;object-fit:cover;box-shadow:0 0 0 1px var(--line),0 0 22px rgba(139,92,255,.55);transition:transform .3s}
.brand:hover img{transform:rotate(-8deg) scale(1.08)}
.nav{display:flex;gap:4px;flex:1}
.nav a,.link{position:relative;padding:8px 14px;border-radius:10px;color:var(--muted);transition:color .2s,background .2s,transform .2s}
.nav a sup{margin-left:5px;font-size:10px;padding:1px 6px;border-radius:99px;background:rgba(139,92,255,.18);color:var(--a)}
.nav a::after{content:"";position:absolute;left:50%;bottom:1px;width:0;height:2px;border-radius:2px;background:linear-gradient(90deg,var(--a),var(--b));transition:.25s;transform:translateX(-50%)}
.nav a:hover,.link:hover{color:var(--text);background:rgba(128,150,200,.12);transform:translateY(-2px)}
.nav a:hover::after,.nav a.on::after{width:60%}
.nav a.on{color:var(--text)}
.right{display:flex;align-items:center;gap:6px;margin-left:auto}
.pill{display:inline-flex;align-items:center;gap:7px;padding:6px 12px;border:1px solid var(--line);border-radius:99px;font-size:12px;color:var(--muted)}
.pill i{width:7px;height:7px;border-radius:50%;background:#2be48a;box-shadow:0 0 10px #2be48a;animation:pulse 1.8s infinite}
.pill b{color:var(--text)}
@keyframes pulse{50%{opacity:.3}}
.me{display:flex;align-items:center;gap:8px}
.mini{width:28px;height:28px;border-radius:9px;background:linear-gradient(135deg,var(--a),var(--b));display:grid;place-items:center;font-size:13px;font-weight:700;color:var(--on-a);overflow:hidden}
.mini img{width:100%;height:100%;object-fit:cover}
.icon{width:36px;height:36px;border:1px solid var(--line);border-radius:10px;background:transparent;color:var(--muted);cursor:pointer;transition:.25s;font-size:15px}
.icon:hover{color:var(--text);transform:rotate(20deg) translateY(-2px);border-color:var(--a)}

/* buton */
.btn{position:relative;overflow:hidden;display:inline-flex;align-items:center;justify-content:center;gap:8px;padding:10px 18px;border:1px solid var(--a);border-radius:11px;background:transparent;color:var(--a);font-weight:600;cursor:pointer;transition:transform .2s,box-shadow .25s,background .25s,color .25s}
.btn::before{content:"";position:absolute;inset:0;background:linear-gradient(110deg,transparent 30%,rgba(255,255,255,.45) 50%,transparent 70%);transform:translateX(-120%);transition:transform .55s}
.btn:hover{transform:translateY(-2px);background:linear-gradient(135deg,var(--a),var(--b));border-color:transparent;color:var(--on-a);box-shadow:0 10px 30px rgba(34,227,255,.3)}
.btn:hover::before{transform:translateX(120%)}
.btn:active{transform:scale(.97)}
.btn.full{width:100%}
.danger{color:var(--danger);border-color:var(--danger);padding:6px 14px}
.danger:hover{background:var(--danger);color:#fff;box-shadow:0 8px 24px rgba(255,93,122,.3)}

/* ana sayfa */
.wrap{max-width:1150px;margin:auto;padding:30px 22px 70px}
.hero{text-align:center;padding:56px 0 30px}
.eyebrow{display:inline-block;font-size:11px;letter-spacing:4px;color:var(--a);font-weight:700}
.hero h1{font-size:clamp(44px,9vw,88px);line-height:1;margin:14px 0 12px;letter-spacing:-2px;background:linear-gradient(90deg,var(--h1) 10%,var(--a) 55%,var(--b));background-size:200% auto;-webkit-background-clip:text;background-clip:text;color:transparent;animation:shine 8s linear infinite}
@keyframes shine{to{background-position:200% center}}
.hero p{margin:0}
.search{position:relative;display:flex;align-items:center;gap:8px;max-width:640px;margin:32px auto 0;padding:7px;border:1px solid var(--line);border-radius:18px;background:var(--panel);backdrop-filter:blur(12px);box-shadow:0 20px 60px var(--shadow);transition:border-color .25s,box-shadow .25s,transform .25s}
.search:focus-within{border-color:var(--a);transform:translateY(-2px);box-shadow:0 0 0 4px var(--glow),0 20px 70px var(--shadow)}
.search svg{margin-left:12px;flex:none;color:var(--muted)}
form.search input[name=q]{flex:1;min-width:0;border:0;border-radius:0;box-shadow:none;background:transparent;color:var(--text);padding:12px 6px;font-size:16px}
kbd{padding:2px 8px;border:1px solid var(--line);border-radius:6px;font-size:12px;color:var(--muted)}
.meta{display:flex;justify-content:center;gap:10px;align-items:center;margin-top:16px;font-size:13px;color:var(--muted)}
.chip{padding:3px 12px;border:1px solid var(--a);border-radius:99px;color:var(--a);transition:.2s}
.chip:hover{background:var(--a);color:var(--on-a)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:18px;margin-top:26px}
.card{position:relative;border:1px solid var(--line);border-radius:18px;background:var(--panel);backdrop-filter:blur(8px);overflow:hidden;animation:rise .55s cubic-bezier(.2,.7,.2,1) both;animation-delay:calc(min(var(--i,0),12)*50ms);transition:transform .3s,border-color .3s,box-shadow .3s}
@keyframes rise{from{opacity:0;transform:translateY(18px)}}
.card::before{content:"";position:absolute;inset:0;z-index:2;pointer-events:none;opacity:0;transition:opacity .3s;background:radial-gradient(300px circle at var(--mx,50%) var(--my,0%),var(--glow),transparent 65%)}
.card:hover{transform:translateY(-7px);border-color:var(--a);box-shadow:0 24px 60px var(--shadow)}
.card:hover::before{opacity:1}
.thumb{position:relative;height:158px;background:var(--thumb);display:grid;place-items:center;color:var(--line);font-weight:700;font-size:26px;letter-spacing:8px;overflow:hidden}
.thumb img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;transition:transform .5s}
.card:hover .thumb img{transform:scale(1.07)}
.thumb::after{content:"";position:absolute;inset:auto 0 0;height:50%;background:linear-gradient(transparent,rgba(0,0,0,.35));pointer-events:none}
.tag{position:absolute;z-index:1;left:10px;top:10px;padding:3px 10px;border-radius:8px;background:rgba(5,7,13,.72);backdrop-filter:blur(6px);border:1px solid rgba(255,255,255,.12);font-size:10px;font-style:normal;letter-spacing:1.2px;color:#dfe7fb;text-transform:uppercase}
.tag.new{left:auto;right:10px;background:linear-gradient(135deg,var(--a),var(--b));color:#04060c;border:0;font-weight:700}
.body{padding:16px 16px 18px}.body h3{margin:0 0 6px;font-size:18px;letter-spacing:-.2px}
.body p{margin:0 0 16px;min-height:22px;font-size:13px;color:var(--muted);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.empty{margin-top:30px;padding:60px 20px;text-align:center;border:1px dashed var(--line);border-radius:20px;color:var(--muted)}
.empty b{display:block;font-size:40px;margin-bottom:6px;opacity:.6}
.foot{text-align:center;padding:30px;font-size:12px;color:var(--muted)}.foot b{color:var(--text)}

/* panel / form */
.auth{min-height:calc(100vh - 70px);display:grid;place-items:center;padding:24px}
.panel{width:100%;max-width:640px;margin:auto;padding:30px;border:1px solid var(--line);border-radius:22px;background:var(--panel);backdrop-filter:blur(14px);box-shadow:0 30px 90px var(--shadow);animation:rise .5s both}
.panel.sm{max-width:390px;text-align:center}.panel.sm form,.panel.sm p{text-align:left}
.panel+.panel{margin-top:18px}
.logo{width:68px;height:68px;border-radius:20px;object-fit:cover;box-shadow:0 0 0 1px var(--line),0 0 40px rgba(139,92,255,.6);animation:float 6s ease-in-out infinite}
.panel h1{font-size:30px;margin:8px 0 22px;letter-spacing:-.5px}.panel h2{margin:0 0 4px;font-size:18px}
form{display:grid;gap:10px}label{font-size:12px;color:var(--muted);margin-top:4px}
input:not([type=radio]):not([type=file]),select,textarea{width:100%;padding:12px 14px;border:1px solid var(--line);border-radius:12px;background:var(--field);color:var(--text);outline:0;transition:border-color .2s,box-shadow .2s}
input:focus,select:focus,textarea:focus{border-color:var(--a);box-shadow:0 0 0 4px var(--glow)}
textarea{min-height:80px;resize:vertical}
input[type=file]{width:100%;color:var(--muted);font-size:13px}
input[type=file]::file-selector-button{margin-right:12px;padding:9px 14px;border:1px solid var(--a);border-radius:10px;background:transparent;color:var(--a);cursor:pointer;transition:.2s}
input[type=file]::file-selector-button:hover{background:var(--a);color:var(--on-a)}
.two{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.seg{display:flex;gap:6px}.seg label{flex:1;margin:0;cursor:pointer}.seg input{display:none}
.seg span{display:block;text-align:center;padding:10px;border:1px solid var(--line);border-radius:12px;color:var(--muted);transition:.2s}
.seg span:hover{color:var(--text);transform:translateY(-2px);border-color:var(--a)}
.seg input:checked+span{color:var(--on-a);background:linear-gradient(135deg,var(--a),var(--b));border-color:transparent;font-weight:600}
.preview{width:100%;max-height:150px;object-fit:cover;border-radius:12px;border:1px solid var(--line)}
.avatar{width:104px;height:104px;margin:0 0 16px;border-radius:30px;overflow:hidden;background:linear-gradient(135deg,var(--a),var(--b));display:grid;place-items:center;font-size:40px;font-weight:700;color:var(--on-a);box-shadow:0 0 40px rgba(139,92,255,.4)}
.avatar img{width:100%;height:100%;object-fit:cover}
.row{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:12px 0;border-bottom:1px solid var(--line)}
.row:last-child{border:0}.row small{display:block;color:var(--muted)}.row form{display:block}
.flash-wrap{position:fixed;top:80px;right:18px;z-index:50;display:grid;gap:8px}
.flash{padding:12px 18px;border:1px solid var(--line);border-left:3px solid var(--a);border-radius:12px;background:var(--solid);box-shadow:0 15px 40px var(--shadow);animation:slide .4s cubic-bezier(.2,.7,.2,1) both;transition:opacity .5s,transform .5s}
@keyframes slide{from{opacity:0;transform:translateX(40px)}}
@media(max-width:760px){.top{flex-wrap:wrap;gap:8px}.nav{order:3;width:100%;overflow-x:auto}.pill,.me span:last-child,kbd{display:none}.two{grid-template-columns:1fr}.hero{padding-top:36px}}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important}}
"""

TEMPLATES = {
    "base.html": """<!doctype html>
<html lang="tr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{% block title %}C31K Prime{% endblock %}</title>
<link rel="icon" type="image/jpeg" href="{{ url_for('favicon') }}">
<script>try{document.documentElement.dataset.theme=localStorage.getItem('theme')||'dark'}catch(e){}</script>
<style>""" + CSS + """</style></head><body>
<div class="aurora"><i></i><i></i></div>
<header class="top">
  <a class="brand" href="{{ url_for('index') }}"><img src="{{ url_for('favicon') }}" alt=""><span>C31K Prime</span></a>
  {% if me %}
  {% set cat = request.args.get('category','') if request.endpoint == 'index' else '' %}
  <nav class="nav">
    {% for key, name in categories.items() %}<a class="{{ 'on' if cat == key }}" href="{{ url_for('index', category=key) }}">{{ name }}{% if counts.get(key) %}<sup>{{ counts[key] }}</sup>{% endif %}</a>{% endfor %}
  </nav>
  {% endif %}
  <div class="right">
    {% if me %}
    <span class="pill"><i></i><b data-online>{{ active_count }}</b>&nbsp;online</span>
    <a class="link me" href="{{ url_for('profile') }}"><span class="mini">{% if me.avatar %}<img src="{{ media(me.avatar) }}" alt="">{% else %}{{ me.username[0]|upper }}{% endif %}</span><span>{{ me.display_name }}</span></a>
    {% if me.username == 'admin' %}<a class="link" href="{{ url_for('admin') }}">Admin</a>{% endif %}
    <a class="link" href="{{ url_for('logout') }}">Çıkış</a>
    {% endif %}
    <button class="icon" id="theme" type="button" title="Tema">☀</button>
  </div>
</header>
{% with msgs = get_flashed_messages() %}{% if msgs %}<div class="flash-wrap">{% for m in msgs %}<div class="flash">{{ m }}</div>{% endfor %}</div>{% endif %}{% endwith %}
{% block content %}{% endblock %}
{% if me %}<footer class="foot">C31K Prime · şu an <b data-online>{{ active_count }}</b> kişi online</footer>{% endif %}
<script>
const th=document.getElementById('theme');
function setTheme(t){document.documentElement.dataset.theme=t;th.textContent=t==='light'?'☾':'☀';try{localStorage.setItem('theme',t)}catch(e){}}
th.textContent=document.documentElement.dataset.theme==='light'?'☾':'☀';
th.onclick=()=>setTheme(document.documentElement.dataset.theme==='light'?'dark':'light');
setTimeout(()=>document.querySelectorAll('.flash').forEach(f=>{f.style.opacity=0;f.style.transform='translateX(40px)';setTimeout(()=>f.remove(),500)}),4200);
document.querySelectorAll('.card').forEach(c=>c.addEventListener('pointermove',e=>{const r=c.getBoundingClientRect();c.style.setProperty('--mx',e.clientX-r.left+'px');c.style.setProperty('--my',e.clientY-r.top+'px')}));
addEventListener('keydown',e=>{if(e.key==='/'&&!/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)){const s=document.querySelector('[name=q]');if(s){e.preventDefault();s.focus()}}});
{% if me %}setInterval(()=>fetch("{{ url_for('heartbeat') }}",{method:"POST"}).then(r=>r.json()).then(d=>document.querySelectorAll("[data-online]").forEach(e=>e.textContent=d.count)).catch(()=>{}),20000);{% endif %}
</script>
</body></html>""",

    "index.html": """{% extends 'base.html' %}
{% block content %}<main class="wrap">
  <section class="hero">
    <span class="eyebrow">// ARCHIVE</span>
    <h1>C31K Prime</h1>
    <p class="muted">Ne aratırsan, sitedeki her şey tek aramada.</p>
    <form class="search" method="get" action="{{ url_for('index') }}">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
      <input name="q" value="{{ q }}" placeholder="Ara: oyun, tool, vpn..." autocomplete="off">
      <kbd>/</kbd>
      {% if active_category %}<input type="hidden" name="category" value="{{ active_category }}">{% endif %}
      <button class="btn">Ara</button>
    </form>
    <div class="meta">
      <span>{{ items|length }} içerik</span>
      {% if active_category %}<a class="chip" href="{{ url_for('index', q=q) if q else url_for('index') }}">{{ categories[active_category] }} ✕</a>{% endif %}
    </div>
  </section>
  {% if not items %}<div class="empty"><b>∅</b>{{ 'Aramana uygun içerik bulunamadı.' if q or active_category else 'Henüz içerik yok.' }}</div>{% endif %}
  <section class="grid">
  {% for item in items %}
    <article class="card" style="--i:{{ loop.index0 }}">
      <div class="thumb">C31K{% if item.thumbnail %}<img src="{{ media(item.thumbnail) }}" alt="" loading="lazy">{% endif %}<em class="tag">{{ categories.get(item.category, item.category) }}</em>{% if now - item.created_at < new_window %}<em class="tag new">NEW</em>{% endif %}</div>
      <div class="body">
        <h3>{{ item.title }}</h3><p>{{ item.description }}</p>
        {% if item.source_type == 'link' %}<a class="btn" href="{{ item.source }}" target="_blank" rel="noopener noreferrer">Aç ↗</a>
        {% else %}<a class="btn" href="{{ media(item.source) }}">{{ 'ZIP indir' if item.source_type == 'zip' else 'İndir' }} ↓</a>{% endif %}
      </div>
    </article>
  {% endfor %}
  </section>
</main>{% endblock %}""",

    "login.html": """{% extends 'base.html' %}
{% block title %}Giriş · C31K Prime{% endblock %}
{% block content %}<main class="auth"><div class="panel sm">
  <img class="logo" src="{{ url_for('favicon') }}" alt="">
  <h1>Giriş yap</h1>
  <form method="post"><input name="username" placeholder="Kullanıcı adı" required autofocus><input name="password" type="password" placeholder="Şifre" required><button class="btn full">Devam et</button></form>
  <p class="muted">Hesabın yok mu? <a href="{{ url_for('register') }}" style="color:var(--a)">Kayıt ol</a></p>
</div></main>{% endblock %}""",

    "register.html": """{% extends 'base.html' %}
{% block title %}Kayıt · C31K Prime{% endblock %}
{% block content %}<main class="auth"><div class="panel sm">
  <img class="logo" src="{{ url_for('favicon') }}" alt="">
  <h1>Hesap oluştur</h1>
  <form method="post"><input name="username" placeholder="Kullanıcı adı" required autofocus><input name="password" type="password" placeholder="Şifre (en az 6)" required><button class="btn full">Kayıt ol</button></form>
  <p class="muted">Zaten hesabın var mı? <a href="{{ url_for('login') }}" style="color:var(--a)">Giriş yap</a></p>
</div></main>{% endblock %}""",

    "profile.html": """{% extends 'base.html' %}
{% block title %}Profil · C31K Prime{% endblock %}
{% block content %}<main class="wrap"><div class="panel">
  <span class="eyebrow">PROFİL</span><h1>@{{ user.username }}</h1>
  <div class="avatar" id="av">{% if user.avatar %}<img src="{{ media(user.avatar) }}" alt="">{% else %}{{ user.username[0]|upper }}{% endif %}</div>
  <form method="post" enctype="multipart/form-data">
    <label>Görünen ad</label><input name="display_name" value="{{ user.display_name }}" maxlength="40">
    <label>Profil görseli</label><input id="avIn" type="file" name="avatar" accept="image/png,image/jpeg,image/webp,image/gif">
    <button class="btn">Kaydet</button>
  </form>
</div>
<script>document.getElementById('avIn').onchange=e=>{const f=e.target.files[0];if(f)document.getElementById('av').innerHTML='<img src="'+URL.createObjectURL(f)+'" alt="">'}</script>
</main>{% endblock %}""",

    "admin.html": """{% extends 'base.html' %}
{% block title %}Admin · C31K Prime{% endblock %}
{% block content %}<main class="wrap">
<div class="panel">
  <span class="eyebrow">ADMIN</span><h1>İçerik ekle</h1>
  <form method="post" enctype="multipart/form-data">
    <div class="two">
      <div><label>Başlık</label><input name="title" required placeholder="Örn. CS Setup"></div>
      <div><label>Kategori</label><select name="category">{% for k, n in categories.items() %}<option value="{{ k }}">{{ n }}</option>{% endfor %}</select></div>
    </div>
    <label>Açıklama</label><textarea name="description" placeholder="Kısa açıklama"></textarea>

    <label>Thumbnail — dosyadan seç (ya da link yapıştır)</label>
    <input id="thIn" type="file" name="thumbnail_file" accept="image/png,image/jpeg,image/webp,image/gif">
    <img id="thPrev" class="preview" alt="" hidden>
    <input name="thumbnail_url" placeholder="veya görsel linki: https://...">

    <label>İçerik türü</label>
    <div class="seg">
      <label><input type="radio" name="source_type" value="link" checked><span>Link</span></label>
      <label><input type="radio" name="source_type" value="zip"><span>ZIP</span></label>
      <label><input type="radio" name="source_type" value="file"><span>Dosya</span></label>
    </div>
    <input id="linkIn" name="source_url" placeholder="https://..." required>
    <div id="fileBox" hidden>
      <input id="fileIn" type="file" name="source_file">
      <small class="muted" id="fileHint"></small>
    </div>
    <button class="btn">İçeriği ekle</button>
  </form>
</div>
<div class="panel">
  <h2>İçerikler ({{ items|length }})</h2>
  {% for item in items %}
  <div class="row">
    <div><strong>{{ item.title }}</strong><small>{{ categories.get(item.category, item.category) }} · {{ item.source_type }}</small></div>
    <form method="post" action="{{ url_for('delete_item', item_id=item.id) }}" onsubmit="return confirm('Silinsin mi?')"><button class="btn danger">Sil</button></form>
  </div>
  {% else %}<p class="muted">Henüz içerik yok.</p>{% endfor %}
</div>
<script>
const $=id=>document.getElementById(id);
function sync(){
  const t=document.querySelector('[name=source_type]:checked').value;
  $('linkIn').hidden=t!=='link';$('linkIn').required=t==='link';
  $('fileBox').hidden=t==='link';$('fileIn').required=t!=='link';
  $('fileIn').accept=t==='zip'?'.zip,.rar,.7z':'.exe,.msi,.iso,.apk,.pdf,.txt,.zip,.rar,.7z,.png,.jpg,.jpeg,.webp';
  $('fileHint').textContent=t==='zip'?'zip / rar / 7z':'cssetup.exe, .msi, .iso, .apk, .pdf ...';
}
document.querySelectorAll('[name=source_type]').forEach(r=>r.addEventListener('change',sync));sync();
$('thIn').onchange=e=>{const f=e.target.files[0];$('thPrev').hidden=!f;if(f)$('thPrev').src=URL.createObjectURL(f)};
</script>
</main>{% endblock %}""",
}

app.jinja_loader = DictLoader(TEMPLATES)
init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)),
            debug=os.environ.get("FLASK_DEBUG") == "1")
