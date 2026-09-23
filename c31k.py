"""C31K Prime - tek dosyalık Flask sitesi (backend + şablonlar + CSS hepsi burada)."""
import base64
import os
import re
import sqlite3
import time
import uuid
from functools import wraps
from pathlib import Path

from flask import (Flask, Response, flash, redirect, render_template, request,
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
    return Response(FAVICON, mimetype="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


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

# Site ikonu / logo (favicon.jpg dosyasına gerek yok, görsel buraya gömülü)
FAVICON = base64.b64decode("""
/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAQDAwMDAgQDAwMEBAQFBgoGBgUFBgwICQcKDgwPDg4MDQ0PERYTDxAVEQ0NExoTFRcY
GRkZDxIbHRsYHRYYGRj/2wBDAQQEBAYFBgsGBgsYEA0QGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgY
GBgYGBgYGBj/wAARCAEAAQADASIAAhEBAxEB/8QAHQAAAQQDAQEAAAAAAAAAAAAABwQFBggBAgMACf/EAEsQAAIBAwIDBQQGBwUH
AwMFAAECAwQFEQAGEiExBxNBUWEUInGBCBUyQoKRIyRSYnKhwRYlM6KxFzRDU3OS0WOywoPS8Bg2VJPh/8QAGgEAAgMBAQAAAAAA
AAAAAAAAAgMAAQQFBv/EACsRAAICAQQCAQIGAwEAAAAAAAABAhEDBBIhMRNBIjJRBSNhgZGhcbHR8P/aAAwDAQACEQMRAD8AmNDS
KLZHg88a0joZGqM5wM6XW8D6ujz5aUrybIGvR2ZRvultJtUjcXQaAF5pWN9f0P8AXVkqo8drlVjj3ToD3iBTfJAMH3v66nZaJ9ZI
iNkuP3NCy9RMKuo5eOjLZID/AGPfC/c/poVXuH9aqCfPUZBBteMG4EHzGjpZYlFsUZ0FNsIv1i2PMaN1oQigXnqeiMXDCjHTXs63
CZ6a2WPHhqijiIg5yw1yrKZHpHUfs6WPhcctYZOOFsDw1ZAPbloxHxgfHXLa9Nw3VevPGnvdUQ71weWdFTse7JPaKSDdO6ICKaRQ
9LQuOcw8Hk8l8l8ep5cirNnjijuYzHjc3SBF2hQh6RSOeVBHLqPMeY9dCmWBu8IK6+h++bDtK/bOkot2QxJQrhI5x7r07E8KmMgZ
BzjkBg+II1VO59he6TuOaPaxo7/bGUvTVsVTHGZAOqMjEFZAOZXy5jlnGTFrYT+rhjp6WUeVyhJ2b0Ld0CV8Bot/2Zuv1EL39Wzt
QH/jqAfd/aK9eD97GPlz1Hti7TrdtV5h3jbJ6SCKNZDTFk7yqYk4jXDHhHLLMcYXGM8Q0dtmbqrb/cblTVsdJCsKRSU8NOpHAh4l
IJP2sFV54HXpoc+vUGow5GYtFKcXOXCAwaWNuY555g50136k/ul+R6H/AE0Z91bAiPFc9vU4RhlpaGMYDebRDwP7vQ+GD1GV5gV7
Q5XmCp1rwaiOZWjNkxSxumV3uNFi4uMeOpjsajwHHDnrpqusBFwbI8c6l2xYOefPOnih6ko8HJTWsFP+l6afqiH3SeHSaCEF8Y1Z
BqqKfOQRpDDBwXWFv3tSCohAzkaa3ULXQn97UIGjabEQQ5PlovUn+5L8NB7aXOCD5aMFJ/uS/DXL1PY6I3XVm7ogaE+9WxBIT5aK
90IWM50J99urUrBR4HTNL2VMrdu6QNO2Dps2s395geulW7GAqWGfE6QbXybopHTOt/sWGuhf9TRT5aWppJSREU6nHhpWIyeQ1TKO
dc4+rZBnw0FLmn97yEDx/ro1V1M/sEmCeh0IblDw3WTPM50SIE2x4GzXz/y/6aEt9TNVUkdNGWy0vFs5seKf00JdwU5inqfjnQFj
RtlCLkf4ho5WiMfVyeugttqP+8eLw4ho52SMSUCEeWi9EYsjiCnONdGjyvIaUmA9dC3d3b3sHa8slHR1Mt9ro8qYbfgxq3k0p938
uLS3JLlkoIDxE412ijXuyOXTVVb39IvfV0lZbPT22ywk8u7i7+THqz8vyUaOv0atrdpvaU39sd67lqU2lGzRw0whiR7m4OGAIUFY
lPIsObH3R0J0meqhFWEsbbCPsnsyiv14i3Pfog9sibipqVxkVTA8nb/0wfD7xHl1NbHGlZplijEatHGigBVHIADoAPDTTeqo0Fv4
6eSJ6qZ1gpkPMGRuhPoBlj6KdcXLllllcjp4oxgqQNO0Jty7o3JHt/bNA9TS0HOplLNCnfsOX6XoCinpgnLnly0ltWwtz0V0omvF
4o6aCSThnqaGR0mIGOBc4C8ZYgBgOXMgDpolFqOxWDJnLw0ydcEvISev8TMfzbUbvN2lbeG1bPNwLNLUNVzohyB3cUhAHnhvH0zp
LXs2wm9u1Dbufs6udZXz11hvLNLUkd59ZTM7R4GP0b8JwDjoRyPTPIBt2tbN07V3vbZb1bXFPPxUM9VTsJYRxjKEkHIHGqDLAfa8
M6KYkTkCzfIa5VkVNW2+ekleVFlQpxgDKnwYfA4Py1KVk809u30L+IgaF+9rTT3ie8TWSGRbjQH9douAgzqUDiaP9o8LDOOuCDhh
zItor4K+0Ry1DOlSuYqhQvJZVOHA9MjI9CNb09JSSbrAM7frFOHjYcmjliY+8PUpIQR4hdOwycJXFmPNTjyikt4gQ1mVOc886k+y
4wnXl1xpq+mBsy8bN3HRbpsVzkobTdHMNRRU7d2I6nBbvExzKOATw59xgfBhqp09XXTvxz11XI3XieZif9ddpalNdHL2F+HHukMP
z1wiQKWI89VT7Oe2O97QrY6C9T1N0sjth45XLy0/70bHmR5oeR8MHVp7ZcLfeLVBdLVVxVdHUKJIpojlWH9D5jqNOhkUugWqNKgE
k6aZ0/XYc8ve0+TLyOmes5VkH8WmlBf2muKenOfLRepv9wX4aDu03ZqenA9NGejj/u9Af2dcvU9joDFeG/QnQn3kQaZx6aLF4Q8L
ADQl3meCnfPlpumKmVl3mStY/odI9qPm4qfXSre0gNVJ489NW15G+tFA89bn2LLIRQAUy/DXSKEswwNapxCnXHlpVRuVJ4l1RRis
p/1Fsjw0HL1Tqt6b4/10aq6T9Rc+mgzd3Ml7k9DqRLClaF4dpMoH3P6aEO6oyKioI0aLKsZ2eS3hH/TQb3SytU1IXz0MXyWxn2xE
0lS4HXI0cNvQvFblL50GdpL+vMPUaOdnTNuTx1cihwYrwcwMEdDqk3bXsKi2H2iLHamxbrlE1ZBB4we+VZPVQeY9Djw1dWoKohZ2
Cqo4mZjgAeZPgNVs7QbOnbf272jb2xq6OtjoKNo7lcUHFT0imXJbi6ORnAA6nAHiRm1FKFsZjTcqRD+wnshqe1PfA9sWWDbtAyvc
KlORfxECH9tvE/dXJ8tfRm2tTWmz0tqtcEVHRUsaxQwQKFSNFGAoHkBqEbL2tZNibNpNtbfpu4o6YfaPN5XP2pHPizHmT8ugGpEt
Ry664mTI5P8AQ7OPTqEeex+9p4jzYk6YTUmv3DLVls09GDT0+OfFIf8AFf8AkEHwfz0kut0mpqAR0bAVlQwgp/3WOcv8FALH+H10
z2i5RybirbPRO3sdpgigbnnimfLHJ8SFVc+rnQWNUB/qqg1l6go+Id1SgVUo83ORGPkQzfhXUYuQk/267fmK5iNBU4PkQrD/AOWl
9JcIKf64udXII4hVmMueeFjVIx/m4vz0ulpoJLtT1zj9NTCREPkHABH8tUw4qh89o5aTT3ehpqyGkqKuGKecExRyNwmTHXGevUct
IamsFPRTVPCziKNpCq9TgE4H5aH3aMlPubZNmvNqqu8QVSPC6dJElQgqfLmF+BHpqi0ldMIxqvq7cAl5inuBWJ/3JwMI34lHAfVU
89La6tlpoUr4MtJSOJwo+8o+2vzQsPy1BNuLcq7aLUVZWe3Uc0PFSVxOJUPgki9Q6MBz5810/WO+fW+3qW4shSSRB30bDnHIOTqR
6Nn5Y1al7QueNXtZIt12fbu+dq1Fg3JboLpaqpQTFKPmrqw5qw6hgQRqjHbJ9F/cGxfaL/s01N/26uXeMLxVdGP31UfpEH7ajI8Q
Ouro2OpMUEtrkY5pD+iz1MLZKflhk/CPPTuJ1AzkgjxGulB2rRxpxcZOLPllsnap3pvii27HcYKL2nibv5BxclHEQo+8xAOBy1cn
am0rVs7bEFjtEbLTx5Znc5eVz9p2PmdOG9OyDs/vPaXR7qsFdSbb3ZS1C1rU0g7mCvAOCXjOME9O8jzzIyDpTPJU22daa90M1tkc
4jabDQy/9OZfcb4ZB9Brbp699iZnOSMYOmO4xH2iEqOh0/y/ZOmevbhmiPrrWhYUdnKRS0rEeWjXTf7lH/DoO7SQNa6VgPAc9F+j
bNujP7uuXqux0BruXDwsWxoP76AamkI9dFS9yMImA0JN6yE0Lj003SrkqRVzezcNew9eekG1SPrRCfPSjerH6zb46R7Wf+80Hrra
+xfotMkIECnHLGu0cY4emt4QGoo8+WlEcSmPONCWN9cAKCQn9k6DVxw14lP/AOddGm5xj6vkHodBC5Ei+SqD0P8AXRLooL9l57Nc
/wDp/wBNBrcRAmqvPOi9aHxs5wCfsf00HtyMDNUnxzoYlvozstA9wf4jXfdH0i7Ns6sq7Db9u3GvulK5ikFSRTQqw9ebMOhBAGR4
6T7IINa54sEMMa07WOx+v37d7JX7WhhN4lf2WsDtwoIACVmc+AQgrnqeIAZI0vPNxjaChHc6BZPuvtR7dt5U+2KepIjqWyLfR5hp
YUH2pJTnLKo6lifIDJA1czsy7OLJ2Y7LjsloHfTuRJWVzrh6mXGOIjwUdFXwHqSdNvZZ2Z7d7L7I1mtq+1XOWJZa+5OoDznJAUfs
oMNhfmcnU6qauOlanSQEmeZYEA/aIJ/IBSflrhZs7yM7mm0yxq32K+MjWQ5zgc865g5Gm+6zSmKK30shjqasmNXXrGg/xHHwU4H7
zLpCNTQ3XK+U1tt1y3bWyRiloonhpONsCTBHEw/icBR6J66H3Znue+vte+3Wg2zU3N6i4M6VMtTHAk8nBGoUA5fHE3Xh8eWdSLtH
swrbParFaYVSsuFbFQ0xbLCFQD9keAAwTjrjnqSbe21Q2K8rtW18S01LOtSGIBJCU8WGI8zIVPxGiStgzmoxr2Qesq950/ZG9sve
3pTdJLkY6x6OqjmU4qWllYYwQoEfl00WmfMjNnOST/PUNvUF3pK0W2KWauljuU808nB70kLwFjlR6TY+WpNQyO1EqTAiaLMMoPg6
HhP+mfnq2DB2KJippZOM+7wNn4YOgtsC/XazWeHbl0t6iaguNFKZKiQLDEHIOWxlip90+6D9vRcu0pjsNa6/a7h1UebMOEfzI1Cd
17Sr6rt8t9ttiqsVVbIp55G+yopsxcR+fdapL2VOST2v2KqK1booriK207vtD2+4O7tS09K3c0tQAGKpxZbDKCcMeqk+Oke0LluG
3XW4U9xSgrqP6wloXai4o5Yp1ZirMj+6VkQoBwnqo89EiLalHb9tVFJRF2qyFlEznmZE95eXgM8seROo1S2qiqLMl3ijMcd3nnSZ
4zzz3rvBJ/EOEgH4eWicWLhkTdDrU1BpJae9KknDACJkxhjC2OPl5rgP+Ejx0/VVTDS0UlVUSBYUALSDmACQM/Dn18tMFurHqaVo
qpVFVA3dVCAci2PtAfssMMPQ48DrtQxLXWKv2rUsw4YGgibPN4JFKofivNPwg+OtGlyV8TPrsPU0Z3Vt1NwWY06vDDWwEvSzyx94
sb9CGX7yN0YDw5jmBoNU72LcJuu1b1bY6S5UhMFzs0kjDhyOTDhIEkTAgq4GCDzweWitt3ctYdt0E+4oYxxUyM9fS5MRbhAYunNk
5g8/eX1Gg79KHaVQdvW7tg2hVtTXOzhYqmro3GZaVmwr8Q5MEc4PUcLnPTW/FnX+TnZMUo9oeLPty27cpGpLR7XFS4wtNLUyTRx/
wBySvwBx6a5XTk8fhz1DOxntA3Dv60XAX23QqaBkjFfAOBZ2YElSnQMAATjlzHIam96AVYiBzzrfBpq0IYZNnRqdtUj/ALgOifRH
+7lHpoZ7PBG0qNjy/RjRHo2/UUIP3dczUdjY9DTekzTsfTQd3q36q4z4aMl5cLSv8NBnexxTSH0On6UGZVzehH1k2T97STawxdEI
89dd7n+8HOfHSXaTlq9M+etcuwEW7pow1FGD5a7ABF4RrlSn9VQemlPAOp0LLG65HFvkz5HQQuQVr1M/r/XRxu4At8jHpwnQKuUy
i6z48/66OJTCpZf/ANoSc/uf00Gt1yCNqkk494aMNifOz5Djnwf00E968ZlkjRWZnkVEVQSXYnkABzJ9NCEd9h1lNLdXp56iWlQY
ZpxTPMT6IqjDN8SAPXpo+UNyjpLMsO3LTIjTypClRXyASTSMeEMVGScDJ54AAPIDQh7NtibzrGaePatzWIsMSVEPs6H5ycOi7DbK
22QV1RVPAZKFGpqWOBiyioccDHiOOIqWVBgAAl+vXWDWbdu5v9jbopS3qMV+5I7ORURVFyzxCqmJjbH/AAl9xPzClvxa5XRwdyWK
Ek85p5MfwwN/92nKmgjpKSKliA4IUWNfgox/TUfuNS0/aPYIoBxQwJW9/IPsq5iQKnqftE+XLPXXGO2ySj0GSdNtsBq66pu7g8Mh
7inB8IVJ978bZb4BdbXmd47elPC5WeslSliIPNS55t8lDt8tI9oS052LaxSOXgig7lGPPIjYoP8A26ssg/a32qbQ7MrxaLpfq6oe
400FTPQWyjVXmqJWCxL9oFUX3n95vLlk8tVg/wD1G/SC29vaS83KaKKqrqdXSmulrRVNOT7hVQFIHudeRODq29q7JqC9du57Qbut
PdKSKOSgpoKqJS9HIrBzKj/e4mZ05jKhVwcaif0h+wmn3Dul9ybduVKb9V0cMVPapWKFkg4g7gnPImRAT4H461Y4Lac6W7Nn2XQN
uzr6UN33F2wUDb2200dVOi0a1O3mkjKBjwd7IjsV4AWXiOeQxq2d0pnt9zFczsYKspHMWXh4JgOFWI6DiAVT+8B56r72I9lw2x2x
2Y3Okie8UlkrJ7n3Z4kiE7Rxwo38XDLjPUIx1aJ6enkozSSQo0BTuzGwypXGMY8tVOK6LnGWDJtbuiMCP2u8W+hwGVphPKP3IsP/
AO/ux89bb7vFs2pZH3bX0V2rZYUWjjgtKcVRP3sq4jXmPtOF8R0+OnS12Vbbcqmr9qkqDJGsMQkHOKMEkrn7xJPU+Cr5ZLo0UE/C
lREkiBgcOuRoMarhgZsm92imW/PpWdoVm3LcNt7T2bZtvS0cvdSyVb/WE3ecIJyVITIyAftc8jJ04bX3R24y9lNo3jbNwUu79vle
8qLKKGOhqqeSOTMio6KRJwMG9zILAjXHte7BXru2O5/2W3PQz1M9TJcrxT1IEMlLFUOZFZckAoFyvGOQxzwRjRx+j/t5rT2Q3Kaa
OJrfdrvU19viIwjUxWOKN8eAk7oyD0YHx1pUY+0VkxOGGOVPlv8A9/AukuMbSxXaGGWJ0i4pYmGe/pjz40I5PwZDDHPBYEDOnSeV
qaemvNN+kalPGwQ572E44wPPkAw9VHnpr2zZG29bWtNTWPcK2gY0zVkq4Z05OmB0UcLKMDy55OmTaG7Vk3rfNoVSrE9DWSigZehi
Bz3fxXPL93+HWG9srXo6G1ZIU+mh22nXRf2bt0SuHjmlqkhmQgqwSeThwfVBkfA6gnalsvdFRfaKt2fuV7Ja7or26/UnCslPNGyt
iQQMChkYEpnAOShJ5amu7KD6q7M6v+z8MdJJbE9spEiUBYzG3eEAeRHGMePEdctsbmtW/tpSRvwrMUCVMCtzTydD5ZAIPgR6aOGX
bLcBPBvx7GRnbe3bTtTbVNY7HTdzSU4wATxM58XdvvMepOkt9npleJHnhVs/ZLgH8tFbbO0du1lpH1lTy19bA3d1IqZiVL4zxBFw
vCwww5eOPDTtfa/bmyNvNXi2UUTD9HT08EKI00mMhAQPmT4AE67kdTF1sR5+WJxbUjTZdTS1WzqU01RFMEQK3duG4W8jjofTRDoz
w0C/DQ62dVR1200uiTQTy1btNNNBjheTOGxjwHDwjyCgaIVCc25fhrLm7LiNV6y1M2hBvc4o5Phou3huGBtB3fDfq7n0OtGmKkVa
3sMXB8+ekW1XC1i8+YOle+uVax9dNW2H/Xl+OtT7AXRcGCO4RxheA8h5aU/r3Tgb8tajd0BOBDrz7sjUDEWdJthCOvpbpVQNEqMA
RjpoTbl2xX2moepnTKN1OjF/a5f/AOOTpi3PcFvNqeP2YZx1Pho4yfRVCPbtLNXWugokrI6SKpnWCWaSIycPEpC8uIff4RnPjrtd
fo71t1lczbwpoVJBBSgcsMf/AFOWvW2lMezpYpeJMx5DL1B8CPUHBHw0VtmbmTdO0YbjxoatMwVcYP2Jl+18A3Jh6NpWaUo8oKKQ
z2qjv2y9spZKzd9VuKtlHDST1dOqyU0Y5M7MCS6jIxxHOcDOM4aXnpqi4Wqz0QleGOQ1LShC0bCL7vedGcyOhIBJ5E6mMW1qKW4T
V92mmuc8rBis5AiQDoqxrgcI8OLi8SeZOo3umjuFVfZIZCaOmmC0FIkJAcwqOOaQY+wuW4fAkhOg5nmZ4OVyb6Ojo8sYNQS5Zl55
K6UwUkjJTqeGWpQ4JI5FEPn5t4dBz6JquCGnvdhSFFjiR541ReQ5wk/0OnWOOOKFYokWONAFVF5BQOgHpqHbi3bbabtC25t2KQPW
SV696R0hV4pEUH1JYcvLWA7G023PfFp7tckjyWs1net4vBZpy0UY+IVHP4tIexu4ip7O/ZJGy1DWSwtxH7rESg/k5/LQ8rtxi62b
tAuqOStddKWkhPgYkD8IB8sJn56TdmW5TaId60M8hCNbHroTno0cRDY+TZ/Dq/YSj8LLL7Kq6ev7PrXX0zFlqYjMxIweNnZmB9Qx
I+Ws7p21at3W6CivEEFQsEnfws4dZIXxjjSRHR0OCQcNzBwc6hvY5d5JLNXWKdoCsLLV0jQsSHhdQD18nGeWR740TOBCxbhGdOjJ
ro5WWFSdjDtPZ9m2fR1NNao1VqqXvqiTh96WTGOJ2Ys7nHLLMcAYGNSBg5UiMqrkHhLdAfDPpryqoJIHMnJ1nmThRk+mr3Ni6GPZ
31qNj21b2kq3ARstQJftcQkYZ+YAI9CNPmT00knu1rpJAlXc6GncnAWaoRCT8CdLASULAEgeIGpZNpHtx7S2/uypo5r1arVWy0gI
ilq6CKokQZzgM4OFzz4cEHy0+QRNDAsTVEs2Ohkxn4AAAAegGs8CcXHwji88a1mngpqeSoqJFihiUvJIxwEUDJJ+AB1e9vgigl0Q
6ru1JN2p3OyQkmeK30tRL5cTNIoHx4eDPxGgBZ7xA3blWTzTOkVRd54HkBw0ZMpCMPUEIR/408bF3kbx253fcNQj08ddHWuY5ORR
YWUoD8Ei0PKG0VVXZbtuyGZy0NRC08XkrJxGQeqswz6fDSdrk3R1MUliit3X/S1dJIa6jmoa9FE6ZgqUUYUgjHEP3WByPmPA6FPZ
9tq01ts/uuolsm47NK9JPLTc0mCsVDSRH3WDcODjGcddTu33U121rXuuPJPsy+1qPvRn7fzRgWHoGHjoST32bZXb1eapAWpXrXM8
a/filCycvUFsj4euhGqPNBvgvNwsUy3Kut8sndR8NU1ApmWWMcyQn2wynLAYPiMnOtu0OwT7ltVvvO3rZ9dTvH+hdqoCKGJgGEiR
MQrs2R9o4GASDgDXdKha20iqt86us0XHBKOYJK5U/njSnZV8doae1VsEtIammWro45lwcEcUiKRyYAniGDyBYcuHWzSZWnRyvxDC
vr9nTajw2LZlFZ57TX0DU0QTuRROVXzwUBU889D46mVNui1Q2vLCtbgUswFHKMAdSSygAfE65jkcDqemNaUtJ9czFnPFb4W94g5E
7g/Y9VBHveZwPPWuTT5Zy0jlNXNc7HFXtSy0onXvEimxxqp+zxAdCRg48M6FG9+VMw0Xb05EZ8cnroO74YrAxJ1p04Eyse/lKVBJ
HVtMe2f9/Hx0+dok4aXAGMNpj2u4aqX460P6gV0XFFDTj3hEv5awaeAH/CX8tZM56eGte8DHOqolsyIYeX6JPy1uYYXUq0SMp5EE
AgjWg948tZdkghMsrrGg6s5CgfM6hBuhtdmtVG9JJHV0dtYcIe3v71MP+k2VdPQAMPUdHvs+2SLLf5twbb3tBdrPWJwVNMKYe8R9
huJX911JPVehII8uMVluu4aVoqCjZIHGPbKoGOLHmv3n+Qx66edp9llh2vcGuwqq2rujjD1HetCg9BGhAI/iLHWfLJdJhpMnQzkA
eOoNJWi7XqquitxQZ9npPLulPNh/G4LfAJp93VWyU9hNJTyFKmub2aNl6qCCXcfwoGPxxqGyx3utpUt+0KOB3P6M1s5xS0ajlkkf
bYeCLnpzwNczO26hH2dPQxjG8s3whi39v2HbFEKCgKzXioAWKLHF3eeQYjxPkvUnURsHY9uq58F+v1dLabjUTd5RRTjineYe+HmB
+wvu/Z+1z+7ozbR7NrDtiua7yu93vshLS3WsALhj17tekY+HP11i932CLfdDG8mIKJsysOfvEc/yGBq8OmV3ILP+INrbj4QLO3mC
lt81Fa6GFIIIo6dY4YwFUKqSgAAaCVztV5t+2BuKjk4FuMc1LEAOoVSDn+NTKB/CNEztfra2+7shMKYmre6jp1/ZPE8YHy41J+en
zcdgSs7OZbLQxgtTQKaRQOrxgFR88EfiOp4LlJjI6tQxY4/z/I0dkrS22mh3DR1NVI1NVzxPRFhwSQsFJRcjKnmGHPHEBqx9HXU9
dRQ1tLIJIZkDo2MZB9PA+nhqsnZVVCWwXSMZ4Vq1cZH7USf/AGnRMs+5JduTSSSFpbe2ZJ4cjKeciZ8cdR0PoepeG8alHsz5crWW
SfVk/wB2Xm4WLYd5vdpthuldQ0ctTBRAkd+yKWC5HPw8OflquNrqu1DtDs8V13huWa301UolWhogYIwjcwBGp6YI5uSTqydnvNrv
9kprzZLhTXC31Kd5BVUzh45B5gj+Y6jodQbcO3ZbNI9VSRPLbSSw7tSxps/dYDnweTeA5HoCcGfco/E6f4Z4fLWX9gXnstsPd8qy
s7w9WIQ5/wAuk1LtrfVP21bImot03aqpaetigBNTIEWkUFpInTPDw8Cn4/lqeqXkCmNHk4scIjUuWz0wB1zqe7W209uP1pcFxWuh
RIs5ECHBIz4scDJ8AMDxJz4HNy/Q634l4ceJp9volXGfDUG7Ra6Sstg2zRVSQS1BR6uQrx8EIYEJjI5uR/2qfMa77n7QbHYb5Ftm
K5UL7iqYu+ht7yjjWP8A5jLnOPJerfAE6gN0rjR2a43aokeSRIZamSR+bOwUnJ/IfDkByGuthxbuX0eTyTceF2Amluc9sqHkpxLV
VNUlZToVHvv3scnE+B+7xMfhoh9lxhqKK+0kiJLA5hyh6MjIykfAgaSdl+2wY4tx1ecxA09MhAIYcPDI/Pzb3R/CfA6c9m28WDtI
v9iAIiNNFU0xPjFxvj8uLh/DqYMLi4zfuzVqtTGcZYl6r+uwi9nNCbds17HNIZvZKqWAB/GJjxJn4ow/nqF2vs8Tf+2LjLTXBBuq
2JToxc8K1UXdER8Y+6xWPAccsgg8uk0s9SKLd1LxHEVdinf/AKi5aM/Mca/MaadkVU9stkV2pf0NVVUvcSOp+0qySFPy4ifnqR06
8kovoGeqn4ozi+Rj7M911djvEmydxxy0siSlIFqF4Whl6mNs9Aeo8M+hGjRYRR3KwS2qaNvabPVN3BQAyRY9+KRM+PC/D68weR1A
u0Oz2nfdroLnIPYr1ADA1RCMNgc1yPvJnPI8x4Eab9pXzcln3GPrmPjnMASSrhyY6lUPCGJ8H4WXkeeUzz1eLTyxz/QHU6mGfFb4
kgo3Ctor5TzQUovFV3eaeojpp3oUR8e8rtlSG59BnT1ZrfvGmoolguduhokAC01SrVLBB90Oqx45fH56hNJuekqmVb53sNSq8C3G
l918eAcdCPiCPTU1tm4Kv2QJRT228RAcmWXuJceoAZT+S61STSpHMFN3JIIby0It+AGncjRCN/qbxWTolklgpIxj241Uckcj+KoF
5tjxbp4cznQ534xFK/w1owKmDIq7v4g1Gc/e0zbWf9fUeunDfb5q8fvHTbtgAVqk6c38ivRcTvB01gtjnpL3mD11v36lMDro6Bs7
pMwblrtQN9XXo3iCkpaqdscUdYpkUY8UJyYm9VGD4jx0kgkC5LaViRO74gc6GST4LRMF7Q7NFSma8xVltZRl2aJp4/iHjB/mBpsm
7WdtVAIslTT3A+Ld+qAfLr+YGofdpW+q5yDgcB550MLDseTdG+bVPdbVLLZKmoYmUoCawRrxtFFnmwPIFvsgE5OkPDFchbmWQs9G
N4U9PuG/UaPHwuKGlDN3YhbGZGGcMX4QRnkFA5czpTdt12212ploSssiju4AiYiJ/dPQqvjjlnl8GHfNZHDttKO9sgStcQx26mYr
GsYwX4iMFzjCjkFy4ABPPULmvc+4XF1nV074e5GycHdqCQFC+AGOQ0hQvkZfoktr3pVUFKI2LPwRyseIZ72Z2yGY+Q66jzzvPO00
jlndizE+JJ5nSRuuNeBx56OiLgT3Cz01ffbTdJXZZLbJJKijo/HGU5/A4Yeo07CQFeFTpCZVjjaSRwqr4nXrdVUcm9KC23umqKKm
WVmm+saZoYpW4CI48uAG4mYEeB4COvLVUW3Q02mwCybovNRSELRXAx1Aj/5co4g4Hochh8SPLQ3+krcLrRdipW3VDQwVFbFT1hTq
8TBvdz4Asq58+mjxue0QWW8Uy0SrHSVaOVh/5Tpwkhf3SGzjwIOOR5Bntuis69kt7gr7jR0hq4S0cFTJw99MuGRoxzPHlR0GD446
6GSSi0iXbsE/0Xu0iu2fuC62pb5FS09Qsc0VFWzYp5nBIYBSRhiCOa4PLx6avXtfe1r3LGIoiaS4BeJqSRgWI/ajb76+o5jxA18k
GGPtgD+LT/Y907ytkkUO3r9ead+Id0lHNISG8OADOD8Oeua427NkMyUdrR9c+ONAzgqvLLNyHLxJPloVb+7ZLPYrfKtBdqSjhUES
XSoYKg9IQftt+9gjyDapbfN8/SU3rtqOiutZvCoty4LCC3vTiXHQyNHGpf5nGhTd4b1HWsb6leKgdTXB+P8Az89Tx8FrUJPqx67Q
b/HuTtavG4LLVVc3tVSslPUOzCZ34VUNkniB4hy8emrvVVNU3PZtLtyrrJDWVEMFPXTJguo4A0rHwBIVh8WGqUdmUe2F7QKGv3Xd
Z6KjpZUniFNTGoM0ob3VOAeEZwc4J6Y1fSghhhoo1po2jRhx4cEMSeeWzz4vPPPW3BHgyTk27FFNDT0lJFS00SxQxKEjjUclAHIa
5PQwve6e6DAmhhkgzjmyOVOM+QZAfz1Itv7at952xU3K4BJJKnvYaZ3OFpgCyB1Pg5YFuLqOQHjmGWe+0tzoe9gqO/7nEVVLDGzR
Qy54WRpAOAHiyMZ8taFyLscriKlrXO9G3DVRL31OfKRPeT/MAPnrSxtnbVvGCM00bEEYwSoJ/mddZaqOCSJJW4TK/doccuLBOPTO
DrqD+WqpXYVuqPVVZDRUb1U/EIoxxOyjPCvix9B1PoDraeTggMixPMeoSPBLfDJA1qxUrhgCDyIIznXXbO36WpSaz0VwmpK2MNLS
R1DGWCeLOShz7yMhOAVP2SvunhOrugWa2e3XrcdniuNBQw01NJnhetnCuMEggogYgggggkdNEbbPZfZoqdK6+TyXWo+0IiO6p1/A
Dl/xEj01CrFdZdsbmqbBeqaemFaDURoEMgSRR77Ar1RlAbiHLKtnBOjTaJo5rJDNFIsiMmVdDkMPMEddDkk0uCl2NV4VUi4UUKqj
AVRgADwA0G9+N+pyDRmu4DQtz56C+/Tijk07TgyKrb3Oa0j1037WYfWCgnx0s3u2a4/HSHbA/XgdG/qKXRbTvPDSiFAeZ0m4fTWV
nMfLPLTShU+FOBrZJVijZ5WCooLMx6AaQrU8UnXOuN1qe7s9RLGMtEomA8+Bg/8A8dQhObLsxbjClduGJjTtho7c/IMPAzDx/wCn
0/ayeQmwSJXQrGgKLwKQoHCvLkPIchy9Brqzq5Locq3vAjxB5jXI8XMLgHHIkZ1glJy5YzrogG94qGiqWq3kaqv9ZEYKeR29230+
TxSRp0VuZAb7TMeuFwIaWp6OjXiKQwooVckBQByA56ID9nUNZc5666blu9XJO3FJwiKLPkAQmQoHIAdB8zp7tu09uWiRamjtcRqF
5ionJmlB8wzkkfLGi3JIiA2KmpkvhpRRzwwJTrMZZ4Wj7wuSE4M4JGEck48VxrC1U730UiQMKdacyNMyEK7cfCFQ9DjDcXllRqVJ
bp93dql6jMkiU1PV8FTMpwyxxgRpGD4M5Vz6Ak+WlHaBBFTbrsVBQUoXioJYIKaBebYlTCqPmf5k+J1L5osZtv0P1lvK30pAaGnY
1sy4zkIQEHzkZT+E6m9TR0+5pVkm7wUdM7Clkifhd5CCjSq3gBkhfPmTkY1B6R6vbl9r4rsYhSVM1NRzz06ScSKA/HHGwBEh711V
yOEgMSMgZE7lmiuZjtttfFMFHtUkYKd1H0EQ/ZZsYx1VQfEjV1zZUnYNq+wXe27cnqrRRy19HQTSLbQK9ijRHAk4kcHgUlfdZCQM
dOEjQs3X2NWbffarT33ckt3tlLLRxgRKy8NYRnnFJlgAFxkLzyc4HPVqqyoht9u4zDxqMRRU8YAMjHksaj16egyeg1ALrtW4UFqo
KCa+V01tnnSnljjCEUUkjkrImR7yB2VRxZIABB5nSs0Xkg4xdMPFJRknJWiNWzsV7I7Nt2oW37LtckhgdTU1ae0S81Izxvkg/DGg
L2RGGTtqsMCxxqsdQZAFXH2UY/00f9xXSfa9HdrXcaqJp4aFqlWjyBJGVYBwDzHNSCOeCOvMaq/2U3NKTti2/O78INRwEk/tKR/X
XlLzLyLJ2j1MceD8t4+pM+jO0rhM0TUbSPgDjX3j89It2UVJU17vWU0U4ZR/ioHB5eukO06pHroGDj3uWPlpw32ywWhassFUAhm8
sc8/lnWnDlll0VrtGDUYYYdbt9MEV92X2a1NYtTPs6zGsjcSJURUqRyIwOQwZACCDzzpiN9qY6Cs9kjiuFRBN3FNxVCRmsdv8NEX
JZ3J908KkZVjkDOH61WyPcPZ9U7hqKeSa6Osix26ol4YqZzIUTjVfte6VfLEgg8hjGpfNtmz2LacMdvt1N3lqKVcLiFVYtF1IwOW
U41wPBsa6n4fp8uJbsku/Rh1ubFN1ij17GLamzG79aXcftnDBTrNFbZ6vv0DSuzyMSuF5PlQqjAH3mzqURWehSO87ZgpoaelrYjU
RxRRhEUyL3bkAADk6K34hrtd6+htdVbrzU1dPT0ZZqaSeeRY1EcgBQliQMB1T4cWmqou9Zfb5SttSNXSJZIp7jMcRLG4U+4MEu2V
Ugch1J5delyzBwQhYvrWxpDUs0bzRgOy8mjkB5sPVXGflpw2BR2zcV9lhvtJJHXz0SsGhlaJleJjxcJU4KsH4hnP2SOoOtKm1XC0
VNdUzpLNbpKphHXcuBpMDvMjJK5k48E8ic4PhqS7GoaS5Yd2MVfZa9poJE6tBOhLRt5qWMvLwIBHjmS6LFtb2dShybZfm4fCOupx
J/nQqfzB00S7I3fDKj0/1a0kbB45oKxomRh0YcUfX5+Y0UOg1gtz6aBSZGM8dvnulvtlVuGjp4bpRTCeOSlk4hHIMrxI2OQZSQy9
MEjngHTjE09tkkntiIVkPFNSseFJD+0p+4/r0Pj5jrnlrxIB5jUIJq2vhq6Q1FOzAZKsjjhZG8VYeB//ADpoQ7/bNE5z1zoiX+RY
NwW1YeUlQk4mx0dEVSufUMwwfUjQx35Nmndc9NacCFyKubzJ9ubPnpFtuThrFHrpXvNga1seemqwNisU55auX1ERb81FMBzmGuMs
lK3/AB8aCa9tW18Z43/Ma2/2z7WLf4j/AJjTdy+4NMNKJT/aE/PWXjgZSGl4lIIYeYPUaDsPbLtXjA76T8xp9pu0bb1ZCJEqHGrV
Ppllldk3E1+xqEPJxz0qmjmY9S0fu5+a8J+epBnnoFdj3aHZa3e9ZteGsUNXRe0Qq7faljGGA9SmD/8ATOjmPLOsU47XQxOzYn11
4dMEcta+OvBvTQkOdBbqO3mqNLCIzVVD1Ux8XkbGWP5AegA11enp5K2OqeniaeNWRJSoLIrY4gD1AOBnzxrPFxeGsA89A0y7GWSN
aa/1lE6KYKxTVRgjIY8llUjx58LfjPlplqNqQwVj3CwutFVsFBBZuBgucAEHK9TywV810q7QrjVWzblHU22AS3E10SU2U4wCQxfI
4lyDGrjqOo8tJ9u7rpb0xo6iM0lyj+3TOCOL1XPP5Hn8Rz0UckU9t8kcG1urgbor5c6K+yTbytK0EEHuUtVDL30HDw+/McL7pOSM
HmqjyJOnC711PdYo7Paq6iqKmpAmJEodUhR1ZmPCc8zwqMeLZ8NSFm8jjQ37Yqvc+3eyyo3HsWKljuFonWueI04dZIQrLL7gxnAf
j/CdE5JK2XGLbpFePpD9pp/2j3PbNJDFVTU9IaGsq0YoFlbDGOMc/dQMQc8yzMfADQHs98a17goriqyKaedJcjB6MD/ppLdLhWXW
7VVzr52qKuqleeaZuruzFmY/Ek6T08E1XWRUtMhlmlYIiL1Zj0GuJkl5JOT9nfhgUIxS7RbHb30mdp2S6Us8rXSqijbLRx02Dj4s
QNcu0P6X1Rf7NJa9rbeShDEEVVbJ3zjHkgwv550F17LI46Ay1F4kEqxlmVIhwhsZ6nnjOhwh5A+OM6zYIxxwcIdGjMlmyLJkXJcv
sK30/aNsZtq3RqZtxW51WKveIFxRqQyuQCOIqwEQ8PeU+HM5TWrcdXUsZ9zrBExOVo6NVJBJ5e+WHTlzzquv0Q9lVMMN37QKuMpD
UJ9W0QI5uAwaVx6ZVF+Iby1aYdRjXf00pPGmzz2rjGOVqPQxUGzLdTd29wmmuzxBQhrArKAowvugYOMch0HgBp2uE00FEsFEVSpn
cU9Py5K7fex5KAzn+HTXfd5WWwxus83tFSvI09OQWUnpxHonz5nwB1GtsXLcO4+1q3XKpkWnpKWmqW9hUEiON1VQW5/bLcIBIzgM
PdyRq5ZopqLfIpYpNOSXAU46OkjtS27uVkphH3XdyAMHXGPeB5HPj8dMNi2pDt7dVdW0Ez+xVdNHGKdznuWR2PInmVIc4B6YxzBG
JGTkYGtTkDUQJ0z56wzAeGueTjWOI51aRZvx5PIaxIcsNaltc6iohpqSWqqW4IYUMjnyUDJ/kNEUR+tb2ndFVN1Sjp0plP77nvH/
AMoi0Lt+sRDKSdE6mingsveVa8NVUs1VOv7LueLh/COFfw6F2/ifZ5Djw1rwi5FXd3uTWv8AHTXZGPtK+GnDdjE3BvjpvtTBZgR1
0EvqIugdmnXwGukNMrNjGts+uu1O4V86zJIIz7EEdeIcs6NOzduU1XYhI2c8hoNTT8TqOmj/ANn7j+y6EnqNacFWDIiV9jqtq7io
7zZ6lqauoplqKeZOqOpyD6jzHiCRq7PZd2i23tN7PKfcNF3cVWp7mvo1OTTTgc18+E/aU+IPmDqod12vfd67xg29t6jaqrZ/DOEj
Xxd2+6g8T8hk8tWL2H2e7c7FaK3UlJVvX7hudTDDcKkZHfRs3CQsecLGpbK594kEk8yBWZfLguIZA3PW3L11zOFQu5CqBkknAA1E
L12k2K2OYaTjuMw69ycID6sevyzpKTfQRMwdZzjQmHa5X97k2ak7vwXvWz+f/wDmnqk7WLNKo9rt9ZA3jwcMg/1B1cscvsRMft70
k1XsmpkpojLUUbJWxIOrGM8RUepXjHz1Xm8drOzJLnFbq25qJ0x3dZT5JgPUe+BgeeOnw0e4e0bbM9VFTU8lZNVSf4dNHSs8j/hH
P59NUX7Qtnz2jeN1raC1VtJZZq6b2M1EYUxqXP6N8E8DKcrwnnyGubrdPbU26Zv0mfanCrRbra/aBOZ6S33+WKphqSqUt2hI4JCf
srIPAnwPj68zohOEdWjlRXRgVZHGQwPIgjyPTXz92vvev2/QSWeteWW3cayxgH3qZ1YMGX065Hrkauztbfm3Ny7Ho9wRXugKPAHq
CZlUxuBh+IHmOYJ0zSZZTTjPloDVY4xalDplOt8dhNbau0a72qyXm0pHHUGWkoqubu5e4b3ozzBDDBK5/dOlW3uy/dVnvaXd7Xbp
JZI+7mippkEanP24yccJx1XGOuDpv+kluyg3l2qUlfQRhYKei9miZhh3USMQ58slmwPADUO2JNd63d4cxtU0nBwTlyRHEuORUDkG
5Dl489c7VY3GcknwdXTTc8cW+wyXnam7rjt6to6C2pTTyxGNZJ6lFVc8jzBJ6Z0Na7sevNDSUrVl3sFCRwRSZqi8jOz4yFC88Ajl
5KdS+8bfo7raJqQcdPJIvuTRs3Eh8D15/Dx0MZbjao+0W0fV57ymo40o+/dcF295S3P1brpOmjuko3xY7NJxi2lzR9FtsWS17a2b
a7BZOH6uoaZIKdlIIdQPt5HUscsT5k6h/arvKLbsVBb5LgtFFUh5KiUyFMIOQBKkNjk5wCCeEDPXQJsXaNu3bdrNHbb3LDSpkiKV
VkWPz4eIHhHw0LNybpv2/Ny1F8vVxqKsLiGDvSAEjXOMAYA5kn5nXd1P5eOk6PP4PnNNqyWbr7WaiecUu1Gkp4o84rZUCuPPu4x7
sY9ccR8Tq0/YDZbzQdk1LetyzyzXa7gVRMww8dP/AMFD8iXP8fpqpO2uzK73OFbnX0stLbh74Z4i0lTgcXBFF1ctjHTGM6uPPdO0
KhsFFW09to7hFNSxSuaKHJiLIDwiPiyVGeWM8h01m0WKLba7HarJLhegh9TpHX3a12yIvcLhTUwHhJIAfy66BtfvbcV0Mkcl2lUA
4eGH9Fw+hUYI+emGR2kJZ2LN5k5P566awfdmLcHin33tOqqe4jvMKNnAMqsgPwJGNSAENGHQhlYZBByCPPVYmUkddS7Zm96vb1Sl
FXPJPa2OChOTD6r6eY1csP2JuDcTpBVRi6XGK0AcUKFamsPgEU5SM+rsBy/ZVvMa1ud0FNtuS62+A3BjGGp4YSMzs3JQPjn+R8db
7faNLAssdSKl5yZZagDBlc8iSPDpjh+6AB4aVT7LbE16bAYg6Dm+3zQSYPnos3p/cY6DO95S0cic+h1rxLgWys+7GIuD/HTVa2Jm
HPx057v92ufJ8dMtpkxMPjpcvqLIT7wJydbq5Ua5lh565tJy66z2WKVl4pBz0fNkVax7ZjUtjpqvMb/pRz8dGnasxXbi8zp+mfLK
mXE7KrFS2vshivdkiilvF2p/aZZ5efE4LBY/RV5jHnknrqKQXioot+e33dJp6ylm7yaFm4ZA2OQIPQcx6Y6aiXYB2prbN0js8vUx
9muEpe2St0inPNoj+6+MjybP7WrIXbbtj3BGi3i2wVRj/wAORgVdP4XUhh8M41Tltk7LStAj3Rvi6X5DTKpo6Lr3KH7f8TePw6ah
wmheYRRyLJKeQjj99j+FcnR+p+z/AGVTMGj2zb3YfenQzH/OTp8pKKit8fBQUdNSL5U8Sxj/ACganmSVJF7QDW3ZO7LqFansc1PE
3/Gr29nX/tOXP/bqZ2rshgXhkvt5lnPjBQr3KfDjOWPy4dE0HmSdNFw3NZLbcUts9fC1xkH6KgjYNPKcZAC+Hnk4GOfTQvLJl7Ti
lDaNqWo09gtVLDUT+5FEg5zP5u32io6sSTgepGo7cdg2muo5SjdzWzcTVNR3YZKx2OWaaI+6+ST5EDkDgakUEUzStWVpRquQYPBz
WJevdp6DxPiefkAo0qUVJVIKLcXaKt74+j4zmSe2U09tfOTLQIaymceOYv8AEj+WQPXUJg2fWbeoFouO3pTw5LTz1qoBzyS3GAw+
GNXXY4bPjoK/Sj3PFt76O1zhd1NRd5orbGDgsVZuOTGf3Eb89JWF4rljdf2O8qnSmike5Kp6ndtwkNdFWoJmSOoiBCOgOFKg/dx/
58dP+2d3Qbd2RWqojkrWqf0MLHrlR7x/dGND72ylZs8ZQ/MayHSQ+5Pn0BB1zJQcvqOxCcKSiFPam/hJQT2661RjrpWlkiq52/RZ
IyAf2QPAdND2neOOsieoT2iNJFaRFcr3gDcwGHMZwefrpv5+Mhx8BrQ1VMOsxb4E6qOOnwFLKl2Wx27sH+3+yKe+2Km3AlquCugU
Rw1DoVYo68XGCCCCPeB/nog7N7Fae0SRNTbb7qVDn26+SLKyc85SJCRnn5L8dQP6GW81qKTcmyJZGxCUulIjHorfo5QPn3Z+Z1bN
G/LXQjg8kU5ts5E82yT2JDbZduUNmc1KGSqrmGGrJ8F8eSgckX0HzJ04Wib2CuaySYETAzUR8ODq8XxQ8x+6w/ZOuoOuFbT+104R
ZTDKjCSGYDJicdG9fIjxBI8daoxUVSM0pNu2KLztqwX9MXi109S4GFmI4ZV+Ei4YfnqCXTshU8UlhvkkZ6iC4J3i/KRcMPmG1P7X
cfrO2968YhqY3MNRCDnupV+0PUcwQfFWB0tU+9jOjjJrpgNABuWzN22rLVdinmiXrNQsKlfyX3h810wCWNpTGHUSDrG3usPip5j8
tWfzz66SV9qtV2j7u622jrV8qmFZP5kaYs79ojiAaj3NuCgs0Vuo6hVhiq4qlBLHx8HA4Ygeh8tE/a13uF+t1Rc6O3imq1IEiEkU
9a2PA9Vflji5+Gcjo4js82Px8Q2xQD90BuH8s409zyUtmsks6RJDT0kLOsaDAAUZAA+WNVLIn0iKJGbjWRV1qhrYOIRzxiRQ3UAj
OD66De+JeFJWJ540VJYpKHbdFRy/4kNOiP8AxcPP+edBzfMxKSDzzrRj6Fvsrtu2UyVrk+emW2PicDTjuhx7U/Px0021h7QOfjpE
nyEQ0sda8WTz1OZLLbccgPy1yNktp/Z/LSvEwtxD4wO8X46K+3KoJYlXOo4lgoSQw5DT3SpFTU/dI3LTcUXF2U3YW/o8WCO/dv8A
Fcp4w8VmpJK4ZGR3hIjjPyLsR/Dq5hkSNCzMFAGSScADVafoqUkEEO8L1K6oo9lpy7nAVQJJDz+Y1MN57zlvtS9BQO0dtQ45cjOf
M+nkPz1TjukROgpLuvbjzmEX2h4wcEd6B/PppYl1tsgBS4UbDzEy/wDnVcXp62W2y1dJCggRu7NRMSI+P9hcc3bzC9PEjTdYts3r
d+5oqCmvlTT0qlmqKiJFQcCtwsUA9fdBJOWz4Kc1KEY9sJW+g+7z3hFZrMaa1zJLdathBT8GHWAsD+lfwAUBmA8SB4Z0w9nlip6W
1tuOQyS1dx4nWSZuJlh4vd5nmWfAdmPM5A6ADUZ37R0G17NQ0NtjaKmo7fW1Qyxd3cKi8bMebPgt7x89E63QJTWejpoRiOKnjjUe
gQAf6azxd5HFdKv7Gtbcaf3F3Fy1qzcuuuRY4xk604+v9dOoVZs0hGdUa+mFvuO+dptDs2in46awxFqnhPL2qUAkfFUCD4sdWz7T
d+UPZx2Y3TdtZwu9NHwU0BP+PUNyjj+bcz6Bj4a+YtzuVbd7vVXW41DVFZVzPPPM/WSRiWZvmSdKzSpUFFXyI2Oldpoa65XumoLb
Sy1VVM/BHDEMs5x0A+GdIyc6kGxL7Tba7RbVfazj7illLOY14mAKlcgePXWSd7XRox1uVsfqns739BTuZNmXwe6cEUjEfy1ASGRy
rAqynBBGCDq1jfSM2ssIH1pVOP2BSvk6q1cKlay8VdWq8KzzySgHwDMT/XWXSznK98aNmujjW3ZKwidgu8P7Fdv23btNL3dHPP7B
VnOB3U/uEn0DFG/Dr6WhmHusOYONfIkHyJHqPDVyuxzt9uy2C3x7qnludEyiKSdvengZfdJz98eODz58j4a6uB3aOXNUWwWQj010
4s+Om6huFHcbfDXUNTHUU0yh45YzlWB8RpUCCeR01oGyN7quVftW6Ue6aR2e3d4tLdqUAYkiY4SYeTxnl6qxB6DE8SRHUOjKyMMq
ynIIPQjUD386DY8yyANx1NMgB8czpy/IHQnu107SNv7VtF0G7Wm2/PAiRccPdvScIICOyEFlKrkMeeAc5xzDct21h7fjuLLOxCng
ALY5AnAJ1FrjvmnsVTHDuK011u7w8Mc44ZoZD5K6nr6EA+mg1au1HdNpKS11XI0DYIkkf2mBh58XJlB8zy9dGPbW6LVvO1S0dVTQ
99wYqKKUB0dfMA/aX/TTHDbyLuzq3aFtVaNp0uXeMBkQiNg5PlgjUFq+06vu277LYZKKmgt9fXIk/MsyxrzXJ6c5O6Xp9466bu7O
ns8Ul02+ks1CBxS0hJd4B+0h6sg8QeY8MjpEbTZJ79WPTUxIeKF6kSL1UxjjXH4lXTFGLVlfoFW+NmM89AjfswjSQfHRpudYtVa4
qxSMTxLMPgyhv66AXaFUHikXPgdPh0A+wCbmmDVTfHTVbpgtQulW4ZAattNlAf1gDWVv5B+hH/a5W5FRrw3VHxdNQnvBjrr3eay+
eQe0IKbugAxw413j3ZTHrjQ6V9LKGlqa+409DRRGWpqJVhhjHV3YhVHzJGiWeRNqL6/R42zcL99HmurYKoUi3W6tLHxAlZY4kWPn
jnjjD/logUHZfUJVmp3FcYI7dEpeSOmduOQDngtgcC4645npy66luwNqwbF7M7Fs+mIZbZRpTs4+/IBmRvm5Y/PTP2o380Fjis1P
Jias96THhGD0+Z5fI60Jy6BpAm7S97U9HtyuuscaUdut1O0dDSxLwrHn3UwB4liuiR2V2+Cj2nPURgcTzezqfKOFQij5sXb4tqpH
0hL+8G27bYYXw9XMaiQZ58EY93/Mw/7dWa7EtwxXbZKxh/0k0UNxRfErLGob8nQ/9w0nLJeaMf0f8joL8uT/AMDt2pUbS0tuqz/g
t31BMfBRMo4SfxJj8Q1K9tXMXXZ1trwfeenRZB+y6jgcfJlbUc3pvTYtttdTatzXWBhMnC9HCTJN5ggLzUggEE4wQDqvf+327bIn
rKazWVrjbpJGZJ6tuEA9BJwLzViAOJSeEkZyNC4OGTf6ZFJSht9otrUVEcELTTSpHEgyzuwVVHmSeQ0Kt29uu2rMJKawj65rByDo
eGnU+r/e/D+eqybk7Y7tu5zJe7tWzR9VpUj4IU9AqnHzOdDjc+9JTRGnpUaFJAVBzkv58xyA9Bp3kxxV2K2sVdrva1uTtK3UIrlc
3lttC57imi9yEP0LhR+QJycePPQ64hjSeM5JY8yT110yNYnJydsZHg2PTWNYzrwPLVFsx489bA601nUIjpqe9ml3EVfVWiR8LIO/
iH7w5MPywflqABtKbdXm2XWKrHECuRxIcMvqNHCeyW4kuUXD7Mu06o2Zc1oa93msdQ/6WPqadj/xEH+o8fjq01NV09TSRVVLMk0M
qB45I2yrqRkEHy183qDeU5pI5J4VnjcZWXnET/LB0V+znt6u1loTtcRFKGRiYKor7RJSE9Qqjlwk8/eyFOTjnrXLNBqxcYO6LF9p
N2lqqy37btwElU0qzMuekjZSBT82Zz6ID46lF4sVG/ZtU2ERiSGGgMUWR96NMo3xyoOo7sna8kMke5L1IJ62UGSBe9E3BxDnIzjk
8hHLIJCgkAnPKTbkuUdt2fdK2RsCOlk4ceLFSqgepYgfPSoRb3Tmqv8A0Mm1xCPNf7PnpbN73Ds67Sqy1x1Ms23lqislE5LLHE2G
4kB+yVDeHIgYI1ZSxXio25daWvt9SopGIenlBysZboh/9NgeXlnHiNU735Ux1PaPe3iwUWqaIEdDwAJn/Lo+9im4E3F2WR2ytIll
t7GjkV+fFHjKZ9OE4/DotJktbWBljUmXksV5p77Yae50xwsg95M80YcmU/A660FktFurJqiit8FO9QR3pjXHEPL0HoNB7sputbYb
5BYbjOZaW4xd5TSt98glR+IYKN5nhPjo28WRo5pxfAK5AvVzVVPt2KlAJanDUx9O7dk/+OhRumzXK5h+CFmJ56N1e1LSbgvFBVYV
1rpJkB8UlxIp/NmHyOtIIbdIxOFOtcZXEXXJSvcGyr0s7uaVwvw1GYbNX09WA8ZGD5avZerLZZ6Ni6R9NVy39T2m2V7mBlBB8NKl
jXZZU7l5azgeWsYOdZOVHXXJGmck6sD9EjYrbu7e6a9VMHHbtux/WEpYZUzH3YF+PFl/war8g89fST6LGwP7D9gFDWVUHBc78wud
TxDDKjDEKH4Jhvi507FG5EYbJJY4IHmmcJHGpZnPQADJOq77n3A993HU3JyeB24YlP3YxyUflz+JOid2qXw27aS26GThmr24Gx17
tebfmcD89AerrEpqWSokOEiUyMfQDJ/010IL2KK1dtF8+s+1WqRXJjoY0pFwfEe83+Zj+WuO2u0nc1JaaS0U95qKaOjVo4o4n7sv
G3VCw5kc8Yz5eWoVda2S5XKruEpJepmeZs+bMT/XTZkggg4OuTKe6W40248LoPdJdobhF3kMgLNzZCfeB9f/ADro0hJ5nQTpr7WQ
4DFZMdCeTD5jS5t215i4VecfGdsa1R1fHKFOC9MJVwayUiGethpQ3UZQFj8tCzcV4a9XlplURwRju4Yx4L/5Okc9dVVjEyv7p6ge
Px89J8fpNKyZt/CVBKFKzdfdGtwfPWmthjGgRDbXtalgBrQy6hdnQHprbrpP3pz010WQHqdVZLN/HWJOaZ1kEHxzr3XlqyBB7Obk
01rqLRKwIibvIwefI/aH58/nqbLwxoQoA9By01dm3ZbJuPsM3Rvm2z1b3i11iw09FHjgnjEXeScx7wfBHDjlyxjnqExb8qY48GVn
8u9iDH8wRrRi1EYra/QEsb7DTYt97p2zhbNfauliXn3Ifii+aNlf5aUbt+k1da3bzWqupKad4sulRTe4Jph/h8S5xwqfeOOpA8tV
9uO8a6tjMfE3AfufZX5gdfmdR6aeSeXvJX4j/poc2dTW1FwW12KJZ2kkeaVy7uSzMfEnmT+eiT2D317d2kPa2fEFygZMH/mICyn8
uIfPQqDad9t3F7Rui33WNippqmOXI8gwz/LOkwntkpF05cH0f2bQf2l2waGCVYrjaa2Ouo5WP2VYgSIfRgp+fCfDRpyMn46AXZXc
xT77hiV/0VXE8Q8jy4l/00egx10ZrkWgUdtFrlp46DdVMWUIRQ1XD+yxLRMfgxZfxjQ/tN9YIczN8zqxN6tNJftvVtmrhmnrIWhc
/s5HJh6g4I9Rqrz0tNa6+e3XNhDV00rQToTjDqcEj0PUehGmYpcUC+xZuXc5SkYCY4x56rvvG6vU1DksTk6Olyg21VQBJamP/v1E
bjtHak36RZ1ct5P00U7a4IipWWz01nhY+Gsh+et1cZ1yKHEw7KNlvv7tl29tMqe5raxRUkfdgX35T/2K356+rcaxxxCOJBHGoCoi
8goHIAfActUn+hJtH2zd+4d8zxnu6CnW3UxPTvJfekPxCIB+PV0ayqjoLbU1sv2IImlP4Rn+mtWGPFgSYCe0+7m47/qYQ36KjApk
HqObH8yfy0H+0e4m29l96qA/C7U5hQjrlyE/0J1L62qkq62aplOXlcyMT5k5P+uhV24VZg7PKemB51Naikeiqzf641pyvbjZUFck
V5IyMDppO8ZB8xpSTrQ5J6a4yZrlGzgqk+GugQA89ba9qNgqCR7Wj8sHx10x564OTxEaiLnwjqpyNYZwo1yBI6axo7EmxYk51rnX
te1RD2s51jXtQhsGIORrqsvnrhr2rshaP6Nfa1sfZvZ1uKw7suVRR1MldHX0yQ05lM47ru2VccgwIB545HTbu3bHZp2jWncG7drW
utsVVEXeLilQJVyheI5iHJSTywpzk6rirFGDAkYOeRxqabd7QZ9t0s60NshnnkYOktSxbuzjBwB8vEaRlhJ/KHZrwZYL4ZFwQ14p
EYqykMORB6g65fHThX11RdLrUXGq4DPUSNLJwLwjiJycAdNJiAfDTb+4hwXo4gEnGlMS+4V+WtQBrdeWqbCjGi6HZTuBjtva1848
skcDOc+KkK3+h1cAEHmpyp6H01QTsRrhU9l0dOWyaWplh+AJDD/3HV3tnXMXXYdrreLicwCNz+8vun/TXVT3QjIzS4bQ/qeeq89v
e2RRbwpNyxLiG5x9xNjwnjHI/ijx/wD1nVgwdRXtM22+6+y+526nQNWRJ7XSf9aP3gPxDiX8Wri6dlMp7UUY5sD89IXQITjw0smq
+KlDoSVYAjPrpmmqG5+OnsE//9k=
""")

app.jinja_loader = DictLoader(TEMPLATES)
init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)),
            debug=os.environ.get("FLASK_DEBUG") == "1")
