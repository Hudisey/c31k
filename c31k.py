from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = 'c31k_cok_gizli_anahtar'

users = {}
users['admin'] = generate_password_hash('1234')

games = [
    {
        "id": 1,
        "title": "Örnek Oyun 1",
        "category": "indirilebilir",
        "thumbnail": "https://via.placeholder.com/250x140",
        "url": "#"
    },
    {
        "id": 2,
        "title": "Psiphon VPN",
        "category": "tools",
        "thumbnail": "https://via.placeholder.com/250x140",
        "url": "#"
    }
]

@app.route('/')
def index():
    if 'username' not in session:
        return redirect(url_for('login'))
    
    search_query = request.args.get('q', '').lower()
    filtered_games = [g for g in games if search_query in g['title'].lower()] if search_query else games
    
    return render_template('index.html', username=session['username'], games=filtered_games, search_query=search_query)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        if username in users and check_password_hash(users[username], password):
            session['username'] = username
            return redirect(url_for('index'))
        flash('Geçersiz kullanıcı adı veya şifre!')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        if username in users:
            flash('Bu kullanıcı adı zaten alınmış!')
        else:
            users[username] = generate_password_hash(password)
            flash('Kayıt başarılı! Giriş yapabilirsiniz.')
            return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if 'username' not in session or session['username'] != 'admin':
        flash('Bu sayfaya erişim yetkiniz yok!')
        return redirect(url_for('index'))
        
    if request.method == 'POST':
        title = request.form['title']
        category = request.form['category']
        thumbnail = request.form['thumbnail']
        url = request.form['url']
        
        new_game = {
            "id": len(games) + 1,
            "title": title,
            "category": category,
            "thumbnail": thumbnail,
            "url": url
        }
        games.append(new_game)
        flash('İçerik başarıyla eklendi!')
        return redirect(url_for('admin'))
        
    return render_template('admin.html', games=games)

@app.route('/admin/delete/<int:game_id>')
def delete_game(game_id):
    if 'username' not in session or session['username'] != 'admin':
        return redirect(url_for('index'))
    global games
    games = [g for g in games if g['id'] != game_id]
    flash('İçerik silindi!')
    return redirect(url_for('admin'))

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True)
