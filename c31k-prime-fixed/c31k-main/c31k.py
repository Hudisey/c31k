import os
import sqlite3
import time
import uuid
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, session, url_for, send_from_directory
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
UPLOAD_DIR = BASE_DIR / "static" / "uploads"
INSTANCE_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = INSTANCE_DIR / "c31k.db"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "c31k_change_this_secret_key")
app.config["MAX_CONTENT_LENGTH"] = 250 * 1024 * 1024

ALLOWED_UPLOADS = {
    "zip", "rar", "7z", "exe", "msi", "iso", "apk", "pdf", "txt", "png", "jpg", "jpeg", "webp"
}
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}
CATEGORIES = {"games": "Games", "tools": "Tools", "vpns": "VPN'S"}
ACTIVE_WINDOW = 60


def db():
    conn = sqlite3.connect(DB_PATH)
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
    admin_password = os.environ.get("ADMIN_PASSWORD", "1234")
    existing = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO users(username, password_hash, display_name) VALUES (?, ?, ?)",
            ("admin", generate_password_hash(admin_password), "C31K Admin")
        )
    count = conn.execute("SELECT COUNT(*) AS n FROM content").fetchone()["n"]
    if count == 0:
        now = time.time()
        conn.executemany(
            "INSERT INTO content(title, category, description, thumbnail, source_type, source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("Örnek Oyun", "games", "Oyun içerikleri burada listelenir.", "", "link", "#", now),
                ("Psiphon VPN", "vpns", "VPN araçları için örnek kayıt.", "", "link", "#", now),
            ],
        )
    conn.commit()
    conn.close()


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_UPLOADS


def save_upload(file_storage, folder="files"):
    if not file_storage or not file_storage.filename:
        return ""
    if not allowed_file(file_storage.filename):
        raise ValueError("Bu dosya türüne izin verilmiyor.")
    safe = secure_filename(file_storage.filename)
    unique_name = f"{uuid.uuid4().hex}_{safe}"
    target_dir = UPLOAD_DIR / folder
    target_dir.mkdir(parents=True, exist_ok=True)
    file_storage.save(target_dir / unique_name)
    return f"uploads/{folder}/{unique_name}"


def touch_active():
    if "username" not in session:
        return
    token = session.setdefault("active_token", uuid.uuid4().hex)
    now = time.time()
    conn = db()
    conn.execute(
        "INSERT INTO active_users(token, username, last_seen) VALUES (?, ?, ?) "
        "ON CONFLICT(token) DO UPDATE SET username=excluded.username, last_seen=excluded.last_seen",
        (token, session["username"], now),
    )
    conn.execute("DELETE FROM active_users WHERE last_seen < ?", (now - ACTIVE_WINDOW,))
    conn.commit()
    conn.close()


@app.context_processor
def global_data():
    active = 0
    try:
        conn = db()
        conn.execute("DELETE FROM active_users WHERE last_seen < ?", (time.time() - ACTIVE_WINDOW,))
        active = conn.execute("SELECT COUNT(*) AS n FROM active_users").fetchone()["n"]
        conn.commit()
        conn.close()
    except sqlite3.Error:
        pass
    return {"active_count": active, "categories": CATEGORIES}


@app.before_request
def before_request():
    if request.endpoint not in {"static", "login", "register"}:
        touch_active()


@app.route("/")
def index():
    if "username" not in session:
        return redirect(url_for("login"))
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    conn = db()
    sql = "SELECT * FROM content WHERE 1=1"
    params = []
    if q:
        sql += " AND (title LIKE ? OR description LIKE ? OR category LIKE ?)"
        like = f"%{q}%"
        params.extend([like, like, like])
    if category in CATEGORIES:
        sql += " AND category = ?"
        params.append(category)
    sql += " ORDER BY id DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return render_template("index.html", username=session["username"], items=rows, search_query=q, active_category=category)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        conn = db()
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        conn.close()
        if user and check_password_hash(user["password_hash"], password):
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
        if len(username) < 3 or len(password) < 4:
            flash("Kullanıcı adı en az 3, şifre en az 4 karakter olmalı.")
        else:
            try:
                conn = db()
                conn.execute(
                    "INSERT INTO users(username, password_hash, display_name) VALUES (?, ?, ?)",
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
def profile():
    if "username" not in session:
        return redirect(url_for("login"))
    conn = db()
    user = conn.execute("SELECT * FROM users WHERE username = ?", (session["username"],)).fetchone()
    if request.method == "POST":
        display_name = request.form.get("display_name", "").strip() or user["username"]
        avatar = user["avatar"]
        avatar_file = request.files.get("avatar")
        if avatar_file and avatar_file.filename:
            try:
                avatar = save_upload(avatar_file, "avatars")
            except ValueError as exc:
                flash(str(exc))
                conn.close()
                return redirect(url_for("profile"))
        conn.execute("UPDATE users SET display_name = ?, avatar = ? WHERE username = ?", (display_name, avatar, session["username"]))
        conn.commit()
        flash("Profil güncellendi.")
        user = conn.execute("SELECT * FROM users WHERE username = ?", (session["username"],)).fetchone()
    conn.close()
    return render_template("profile.html", user=user)


@app.route("/admin", methods=["GET", "POST"])
def admin():
    if session.get("username") != "admin":
        flash("Bu sayfa sadece admin için.")
        return redirect(url_for("index"))

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        category = request.form.get("category", "games")
        description = request.form.get("description", "").strip()
        source_type = request.form.get("source_type", "link")
        source = request.form.get("source_url", "").strip()
        try:
            if category not in CATEGORIES:
                raise ValueError("Geçersiz kategori.")
            if source_type not in {"link", "zip", "file"}:
                raise ValueError("Geçersiz aktarım türü.")
            source_file = request.files.get("source_file")
            if source_type in {"zip", "file"}:
                source = save_upload(source_file, "content")
                if not source:
                    raise ValueError("Dosya seçmelisin.")
            thumbnail = request.form.get("thumbnail_url", "").strip()
            thumbnail_file = request.files.get("thumbnail_file")
            if thumbnail_file and thumbnail_file.filename:
                thumbnail = save_upload(thumbnail_file, "thumbnails")
            if not title or not source:
                raise ValueError("Başlık ve içerik kaynağı gerekli.")
            conn = db()
            conn.execute(
                "INSERT INTO content(title, category, description, thumbnail, source_type, source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (title, category, description, thumbnail, source_type, source, time.time()),
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


@app.route("/admin/delete/<int:item_id>")
def delete_item(item_id):
    if session.get("username") != "admin":
        return redirect(url_for("index"))
    conn = db()
    conn.execute("DELETE FROM content WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    flash("İçerik silindi.")
    return redirect(url_for("admin"))


@app.route("/uploads/<path:filename>")
def uploads(filename):
    return send_from_directory(UPLOAD_DIR, filename, as_attachment=False)


@app.route("/heartbeat", methods=["POST"])
def heartbeat():
    touch_active()
    conn = db()
    conn.execute("DELETE FROM active_users WHERE last_seen < ?", (time.time() - ACTIVE_WINDOW,))
    count = conn.execute("SELECT COUNT(*) AS n FROM active_users").fetchone()["n"]
    conn.commit()
    conn.close()
    return {"count": count}


@app.route("/logout")
def logout():
    token = session.get("active_token")
    if token:
        conn = db()
        conn.execute("DELETE FROM active_users WHERE token = ?", (token,))
        conn.commit()
        conn.close()
    session.clear()
    return redirect(url_for("login"))


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
