from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_socketio import SocketIO, emit
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = 'c31k_cok_gizli_anahtar'
socketio = SocketIO(app)

# Basit bellek tabanlı kullanıcı veritabanı
users = {}
active_users = 0

@app.route('/')
def index():
    if 'username' not in session:
        return redirect(url_for('login'))
    return render_template('index.html', username=session['username'])

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

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('login'))

# Anlık aktif kullanıcı takibi (Socket.IO)
@socketio.on('connect')
def handle_connect():
    global active_users
    active_users += 1
    emit('update_user_count', {'count': active_users}, broadcast=True)

@socketio.on('disconnect')
def handle_disconnect():
    global active_users
    active_users = max(0, active_users - 1)
    emit('update_user_count', {'count': active_users}, broadcast=True)

if __name__ == '__main__':
    socketio.run(app, debug=True)