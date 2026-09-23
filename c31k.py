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
    count = conn.execute(
        "SELECT COUNT(*) FROM active_users WHERE last_seen >= ?", (time.time() - ACTIVE_WINDOW,)
    ).fetchone()[0]
    me = None
    if "username" in session:
        me = conn.execute(
            "SELECT username, display_name, avatar FROM users WHERE username=?", (session["username"],)
        ).fetchone()
    conn.close()
    return {"active_count": count, "me": me, "categories": CATEGORIES, "media": media}


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
    return render_template("index.html", items=items, q=q, active_category=category)


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
:root{--bg:#04060c;--panel:rgba(13,18,30,.72);--line:rgba(110,150,255,.16);--text:#e9efff;--muted:#7f8ba6;--a:#22e3ff;--b:#8b5cff;--danger:#ff5d7a}
*{box-sizing:border-box}[hidden]{display:none!important}
html{scroll-behavior:smooth}
body{margin:0;min-height:100vh;color:var(--text);font:15px/1.5 "Space Grotesk",system-ui,sans-serif;background:
 radial-gradient(800px 420px at 88% -10%,rgba(139,92,255,.22),transparent 60%),
 radial-gradient(700px 420px at 0% 0%,rgba(34,227,255,.12),transparent 60%),
 linear-gradient(rgba(110,150,255,.06) 1px,transparent 1px) 0 0/48px 48px,
 linear-gradient(90deg,rgba(110,150,255,.06) 1px,transparent 1px) 0 0/48px 48px,var(--bg);background-attachment:fixed}
a{color:inherit;text-decoration:none}button,input,select,textarea{font:inherit}
.muted{color:var(--muted)}

/* üst bar */
.top{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:22px;padding:12px 4%;background:rgba(4,6,12,.75);backdrop-filter:blur(14px);border-bottom:1px solid var(--line)}
.brand{display:flex;align-items:center;gap:10px;font-weight:700;letter-spacing:.5px}
.brand img{width:34px;height:34px;border-radius:10px;object-fit:cover;box-shadow:0 0 18px rgba(139,92,255,.55)}
.nav{display:flex;gap:4px;flex:1}
.nav a,.link{position:relative;padding:8px 14px;border-radius:9px;color:var(--muted);transition:color .2s,background .2s,transform .2s}
.nav a::after{content:"";position:absolute;left:50%;bottom:2px;width:0;height:2px;background:linear-gradient(90deg,var(--a),var(--b));transition:.25s;transform:translateX(-50%)}
.nav a:hover,.link:hover{color:#fff;background:rgba(255,255,255,.05);transform:translateY(-2px)}
.nav a:hover::after,.nav a.on::after{width:60%}
.nav a.on{color:#fff}
.right{display:flex;align-items:center;gap:6px}
.pill{display:inline-flex;align-items:center;gap:7px;padding:6px 12px;border:1px solid var(--line);border-radius:99px;font-size:12px;color:#b6c2dc}
.pill i{width:7px;height:7px;border-radius:50%;background:#3dff9a;box-shadow:0 0 10px #3dff9a;animation:pulse 1.8s infinite}
@keyframes pulse{50%{opacity:.35}}
.me{display:flex;align-items:center;gap:8px}
.mini{width:28px;height:28px;border-radius:8px;object-fit:cover;background:#151c2c;display:grid;place-items:center;font-size:13px;font-weight:700;overflow:hidden}
.mini img{width:100%;height:100%;object-fit:cover}

/* buton */
.btn{position:relative;overflow:hidden;display:inline-flex;align-items:center;justify-content:center;gap:8px;padding:10px 18px;border:1px solid rgba(34,227,255,.5);border-radius:10px;background:rgba(34,227,255,.08);color:var(--a);font-weight:600;cursor:pointer;transition:transform .2s,box-shadow .25s,background .25s,color .25s}
.btn::before{content:"";position:absolute;inset:0;background:linear-gradient(110deg,transparent 30%,rgba(255,255,255,.4) 50%,transparent 70%);transform:translateX(-120%);transition:transform .55s}
.btn:hover{transform:translateY(-2px);background:linear-gradient(135deg,var(--a),var(--b));color:#04060c;box-shadow:0 8px 28px rgba(34,227,255,.35)}
.btn:hover::before{transform:translateX(120%)}
.btn:active{transform:scale(.97)}
.danger{color:var(--danger);border-color:rgba(255,93,122,.4);background:rgba(255,93,122,.08);padding:6px 12px}
.danger:hover{background:var(--danger);color:#fff;box-shadow:0 8px 24px rgba(255,93,122,.3)}

/* düzen */
.wrap{max-width:1150px;margin:auto;padding:40px 22px 80px}
.hero{text-align:center;padding:40px 0 34px}
.eyebrow{font-size:11px;letter-spacing:4px;color:var(--a);font-weight:700}
.hero h1{font-size:clamp(38px,7vw,68px);line-height:1.05;margin:10px 0 8px;letter-spacing:-1px;background:linear-gradient(90deg,#fff,var(--a) 55%,var(--b));-webkit-background-clip:text;background-clip:text;color:transparent}
.search{display:flex;max-width:620px;margin:26px auto 0;padding:6px;border:1px solid var(--line);border-radius:16px;background:var(--panel);backdrop-filter:blur(10px);transition:border-color .25s,box-shadow .25s}
.search:focus-within{border-color:var(--a);box-shadow:0 0 0 4px rgba(34,227,255,.1),0 0 40px rgba(34,227,255,.12)}
.search input{flex:1;border:0;outline:0;background:transparent;color:#fff;padding:12px 14px;font-size:16px}
.chip{display:inline-block;margin-top:14px;padding:4px 12px;border:1px solid var(--line);border-radius:99px;font-size:12px;color:var(--muted)}
.chip:hover{color:#fff;border-color:var(--a)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:16px;margin-top:20px}
.card{border:1px solid var(--line);border-radius:16px;background:var(--panel);overflow:hidden;transition:transform .25s,border-color .25s,box-shadow .25s}
.card:hover{transform:translateY(-6px);border-color:rgba(34,227,255,.5);box-shadow:0 18px 50px rgba(0,0,0,.4),0 0 30px rgba(34,227,255,.08)}
.thumb{position:relative;height:150px;background:#0c1220;display:grid;place-items:center;color:#2b3550;font-weight:700;letter-spacing:6px;overflow:hidden}
.thumb img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;transition:transform .4s}
.card:hover .thumb img{transform:scale(1.06)}
.tag{position:absolute;left:10px;top:10px;padding:3px 9px;border-radius:7px;background:rgba(4,6,12,.8);border:1px solid var(--line);font-size:10px;font-style:normal;letter-spacing:1px;color:#cfd8ee;text-transform:uppercase}
.body{padding:16px}.body h3{margin:0 0 6px;font-size:17px}.body p{margin:0 0 14px;min-height:20px;font-size:13px;color:var(--muted)}
.empty{padding:50px 20px;text-align:center;border:1px dashed var(--line);border-radius:16px;color:var(--muted);margin-top:20px}

/* panel / form */
.auth{min-height:calc(100vh - 70px);display:grid;place-items:center;padding:24px}
.panel{width:100%;max-width:640px;margin:auto;padding:28px;border:1px solid var(--line);border-radius:18px;background:var(--panel);backdrop-filter:blur(12px);box-shadow:0 25px 80px rgba(0,0,0,.35)}
.panel.sm{max-width:380px}.panel+.panel{margin-top:18px}
.panel h1{font-size:30px;margin:6px 0 20px}.panel h2{margin:0 0 4px;font-size:18px}
form{display:grid;gap:10px}label{font-size:12px;color:var(--muted);margin-top:4px}
input:not([type=radio]):not([type=file]),select,textarea{width:100%;padding:12px;border:1px solid var(--line);border-radius:10px;background:#080c15;color:#fff;outline:0;transition:border-color .2s,box-shadow .2s}
input:focus,select:focus,textarea:focus{border-color:var(--a);box-shadow:0 0 0 3px rgba(34,227,255,.1)}
textarea{min-height:80px;resize:vertical}
input[type=file]{width:100%;color:var(--muted);font-size:13px}
input[type=file]::file-selector-button{margin-right:12px;padding:9px 14px;border:1px solid rgba(34,227,255,.5);border-radius:9px;background:rgba(34,227,255,.08);color:var(--a);cursor:pointer;transition:.2s}
input[type=file]::file-selector-button:hover{background:var(--a);color:#04060c}
.two{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.seg{display:flex;gap:6px}.seg label{flex:1;margin:0;cursor:pointer}.seg input{display:none}
.seg span{display:block;text-align:center;padding:10px;border:1px solid var(--line);border-radius:10px;color:var(--muted);transition:.2s}
.seg span:hover{color:#fff;transform:translateY(-2px);border-color:rgba(34,227,255,.4)}
.seg input:checked+span{color:#04060c;background:linear-gradient(135deg,var(--a),var(--b));border-color:transparent;font-weight:600}
.avatar{width:96px;height:96px;margin:0 0 14px;border-radius:26px;overflow:hidden;background:#131a29;display:grid;place-items:center;font-size:36px;font-weight:700;border:1px solid var(--line)}
.avatar img{width:100%;height:100%;object-fit:cover}
.row{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:12px 0;border-bottom:1px solid var(--line)}
.row small{display:block;color:var(--muted)}.row form{display:block}
.flash-wrap{position:fixed;top:78px;right:18px;z-index:50;display:grid;gap:8px}
.flash{padding:11px 16px;border:1px solid var(--line);border-left:3px solid var(--a);border-radius:10px;background:#0d1422;box-shadow:0 15px 40px #000a;transition:opacity .5s}
@media(max-width:760px){.top{flex-wrap:wrap;gap:8px}.nav{order:3;width:100%}.pill{display:none}.two{grid-template-columns:1fr}.me span{display:none}}
"""

TEMPLATES = {
    "base.html": """<!doctype html>
<html lang="tr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{% block title %}C31K Prime{% endblock %}</title>
<link rel="icon" type="image/jpeg" href="{{ url_for('favicon') }}">
<style>""" + CSS + """</style></head><body>
<header class="top">
  <a class="brand" href="{{ url_for('index') }}"><img src="{{ url_for('favicon') }}" alt=""><span>C31K Prime</span></a>
  {% if me %}
  {% set cat = request.args.get('category','') if request.endpoint == 'index' else '' %}
  <nav class="nav">
    {% for key, name in categories.items() %}<a class="{{ 'on' if cat == key }}" href="{{ url_for('index', category=key) }}">{{ name }}</a>{% endfor %}
  </nav>
  <div class="right">
    <span class="pill"><i></i><b data-online>{{ active_count }}</b>&nbsp;online</span>
    <a class="link me" href="{{ url_for('profile') }}"><span class="mini">{% if me.avatar %}<img src="{{ media(me.avatar) }}" alt="">{% else %}{{ me.username[0]|upper }}{% endif %}</span><span>{{ me.display_name }}</span></a>
    {% if me.username == 'admin' %}<a class="link" href="{{ url_for('admin') }}">Admin</a>{% endif %}
    <a class="link" href="{{ url_for('logout') }}">Çıkış</a>
  </div>
  {% endif %}
</header>
{% with msgs = get_flashed_messages() %}{% if msgs %}<div class="flash-wrap">{% for m in msgs %}<div class="flash">{{ m }}</div>{% endfor %}</div>{% endif %}{% endwith %}
{% block content %}{% endblock %}
<script>
setTimeout(()=>document.querySelectorAll('.flash').forEach(f=>{f.style.opacity=0;setTimeout(()=>f.remove(),500)}),4000);
{% if me %}setInterval(()=>fetch("{{ url_for('heartbeat') }}",{method:"POST"}).then(r=>r.json()).then(d=>document.querySelectorAll("[data-online]").forEach(e=>e.textContent=d.count)).catch(()=>{}),20000);{% endif %}
</script>
</body></html>""",

    "index.html": """{% extends 'base.html' %}
{% block content %}<main class="wrap">
  <section class="hero">
    <p class="eyebrow">// ARCHIVE</p>
    <h1>C31K Prime</h1>
    <p class="muted">Ne aratırsan, sitedeki her şey tek aramada.</p>
    <form class="search" method="get" action="{{ url_for('index') }}">
      <input name="q" value="{{ q }}" placeholder="Ara: oyun, tool, vpn..." autocomplete="off" autofocus>
      {% if active_category %}<input type="hidden" name="category" value="{{ active_category }}">{% endif %}
      <button class="btn">Ara</button>
    </form>
    {% if active_category %}<a class="chip" href="{{ url_for('index', q=q) if q else url_for('index') }}">{{ categories[active_category] }} ✕</a>{% endif %}
  </section>
  {% if not items %}<div class="empty">{{ 'Aramana uygun içerik bulunamadı.' if q or active_category else 'Henüz içerik yok.' }}</div>{% endif %}
  <section class="grid">
  {% for item in items %}
    <article class="card">
      <div class="thumb">C31K{% if item.thumbnail %}<img src="{{ media(item.thumbnail) }}" alt="" loading="lazy">{% endif %}<em class="tag">{{ categories.get(item.category, item.category) }}</em></div>
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
  <p class="eyebrow">C31K PRIME</p><h1>Giriş yap</h1>
  <form method="post"><input name="username" placeholder="Kullanıcı adı" required autofocus><input name="password" type="password" placeholder="Şifre" required><button class="btn">Devam et</button></form>
  <p class="muted">Hesabın yok mu? <a href="{{ url_for('register') }}" style="color:var(--a)">Kayıt ol</a></p>
</div></main>{% endblock %}""",

    "register.html": """{% extends 'base.html' %}
{% block title %}Kayıt · C31K Prime{% endblock %}
{% block content %}<main class="auth"><div class="panel sm">
  <p class="eyebrow">C31K PRIME</p><h1>Hesap oluştur</h1>
  <form method="post"><input name="username" placeholder="Kullanıcı adı" required autofocus><input name="password" type="password" placeholder="Şifre (en az 6)" required><button class="btn">Kayıt ol</button></form>
  <p class="muted">Zaten hesabın var mı? <a href="{{ url_for('login') }}" style="color:var(--a)">Giriş yap</a></p>
</div></main>{% endblock %}""",

    "profile.html": """{% extends 'base.html' %}
{% block title %}Profil · C31K Prime{% endblock %}
{% block content %}<main class="wrap"><div class="panel">
  <p class="eyebrow">PROFİL</p><h1>@{{ user.username }}</h1>
  <div class="avatar">{% if user.avatar %}<img src="{{ media(user.avatar) }}" alt="">{% else %}{{ user.username[0]|upper }}{% endif %}</div>
  <form method="post" enctype="multipart/form-data">
    <label>Görünen ad</label><input name="display_name" value="{{ user.display_name }}" maxlength="40">
    <label>Profil görseli</label><input type="file" name="avatar" accept="image/png,image/jpeg,image/webp,image/gif">
    <button class="btn">Kaydet</button>
  </form>
</div></main>{% endblock %}""",

    "admin.html": """{% extends 'base.html' %}
{% block title %}Admin · C31K Prime{% endblock %}
{% block content %}<main class="wrap">
<div class="panel">
  <p class="eyebrow">ADMIN</p><h1>İçerik ekle</h1>
  <form method="post" enctype="multipart/form-data">
    <div class="two">
      <div><label>Başlık</label><input name="title" required placeholder="Örn. CS Setup"></div>
      <div><label>Kategori</label><select name="category">{% for k, n in categories.items() %}<option value="{{ k }}">{{ n }}</option>{% endfor %}</select></div>
    </div>
    <label>Açıklama</label><textarea name="description" placeholder="Kısa açıklama"></textarea>

    <label>Thumbnail — dosyadan seç (ya da link yapıştır)</label>
    <input type="file" name="thumbnail_file" accept="image/png,image/jpeg,image/webp,image/gif">
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
  {% endfor %}
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
</script>
</main>{% endblock %}""",
}

app.jinja_loader = DictLoader(TEMPLATES)
init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)),
            debug=os.environ.get("FLASK_DEBUG") == "1")
