import os
from flask import Flask, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-change-this-secret-key")

# Demo in-memory storage. Note: data resets when the Render service restarts.
users = {"admin": generate_password_hash("1234")}

games = [
    {
        "id": 1,
        "title": "Örnek Oyun 1",
        "category": "indirilebilir",
        "thumbnail": "https://via.placeholder.com/250x140",
        "url": "#",
    },
    {
        "id": 2,
        "title": "Psiphon VPN",
        "category": "tools",
        "thumbnail": "https://via.placeholder.com/250x140",
        "url": "#",
    },
]


@app.route("/")
def index():
    if "username" not in session:
        return redirect(url_for("login"))

    search_query = request.args.get("q", "").strip().lower()
    filtered_games = (
        [g for g in games if search_query in g["title"].lower()]
        if search_query
        else games
    )

    return render_template(
        "index.html",
        username=session["username"],
        games=filtered_games,
        search_query=search_query,
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if username in users and check_password_hash(users[username], password):
            session["username"] = username
            return redirect(url_for("index"))

        flash("Geçersiz kullanıcı adı veya şifre!")

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            flash("Kullanıcı adı ve şifre gerekli!")
        elif username in users:
            flash("Bu kullanıcı adı zaten alınmış!")
        else:
            users[username] = generate_password_hash(password)
            flash("Kayıt başarılı! Giriş yapabilirsiniz.")
            return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/admin", methods=["GET", "POST"])
def admin():
    if session.get("username") != "admin":
        flash("Bu sayfaya erişim yetkiniz yok!")
        return redirect(url_for("index"))

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        category = request.form.get("category", "").strip()
        thumbnail = request.form.get("thumbnail", "").strip()
        url = request.form.get("url", "").strip()

        if not all([title, category, thumbnail, url]):
            flash("Tüm alanları doldurun!")
            return redirect(url_for("admin"))

        new_id = max((g["id"] for g in games), default=0) + 1
        games.append({
            "id": new_id,
            "title": title,
            "category": category,
            "thumbnail": thumbnail,
            "url": url,
        })
        flash("İçerik başarıyla eklendi!")
        return redirect(url_for("admin"))

    return render_template("admin.html", games=games)


@app.route("/admin/delete/<int:game_id>")
def delete_game(game_id):
    if session.get("username") != "admin":
        return redirect(url_for("index"))

    global games
    games = [g for g in games if g["id"] != game_id]
    flash("İçerik silindi!")
    return redirect(url_for("admin"))


@app.route("/logout")
def logout():
    session.pop("username", None)
    return redirect(url_for("login"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
