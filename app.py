import os
import sqlite3
import uuid
from flask import Flask, render_template, request, redirect, url_for

app = Flask(__name__)

IMAGE_FOLDER = 'static/images'

def init_db():
    conn = sqlite3.connect('game.db')
    c = conn.cursor()

    # Таблица пользователей
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            code TEXT UNIQUE NOT NULL,
            rating INTEGER DEFAULT 0,
            selected_card_id INTEGER,
            FOREIGN KEY (selected_card_id) REFERENCES images(rowid)
        )
    ''')

    # Таблица изображений
    c.execute('''
        CREATE TABLE IF NOT EXISTS images (
            subfolder TEXT,
            image TEXT,
            status TEXT
        )
    ''')

    conn.commit()
    conn.close()

def load_images():
    conn = sqlite3.connect('game.db')
    c = conn.cursor()

    for subfolder in os.listdir(IMAGE_FOLDER):
        subfolder_path = os.path.join(IMAGE_FOLDER, subfolder)
        if os.path.isdir(subfolder_path):
            for filename in os.listdir(subfolder_path):
                c.execute('SELECT * FROM images WHERE subfolder = ? AND image = ?', (subfolder, filename))
                if not c.fetchone():
                    c.execute('INSERT INTO images (subfolder, image, status) VALUES (?, ?, ?)',
                              (subfolder, filename, None))

    conn.commit()
    conn.close()

@app.route('/')
def index():
    visits = int(request.cookies.get('visits', 0)) + 1
    resp = app.make_response(render_template("index.html", visits=visits))
    resp.set_cookie('visits', str(visits))
    return resp

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    conn = sqlite3.connect('game.db')
    c = conn.cursor()

    message = None
    if request.method == 'POST':
        name = request.form.get('name')
        num_cards = int(request.form.get('num_cards', 3))
        active_subfolder = request.form.get('active_subfolder')

        if name:
            code = str(uuid.uuid4())[:8]
            c.execute('INSERT INTO users (name, code) VALUES (?, ?)', (name, code))
            user_id = c.lastrowid

            # Назначаем картинки
            c.execute('SELECT rowid FROM images WHERE status IS NULL AND subfolder = ? LIMIT ?', (active_subfolder, num_cards))
            for (img_id,) in c.fetchall():
                c.execute('UPDATE images SET status = ? WHERE rowid = ?', (f'Занято:{user_id}', img_id))

            conn.commit()
            message = f'Пользователь {name} добавлен.'

        elif active_subfolder:
            message = f'Активен подкаталог: {active_subfolder}'

    c.execute('SELECT id, name, code, rating FROM users')
    users = c.fetchall()

    c.execute('SELECT subfolder, image, status FROM images')
    images = c.fetchall()

    subfolders = [f for f in os.listdir(IMAGE_FOLDER) if os.path.isdir(os.path.join(IMAGE_FOLDER, f))]
    active_subfolder = request.form.get('active_subfolder') or subfolders[0]

    conn.close()
    return render_template('admin.html', users=users, images=images, subfolders=subfolders,
                           active_subfolder=active_subfolder, message=message)

@app.route('/delete_user/<int:user_id>', methods=['POST'])
def delete_user(user_id):
    conn = sqlite3.connect('game.db')
    c = conn.cursor()

    c.execute('DELETE FROM users WHERE id = ?', (user_id,))
    c.execute('UPDATE images SET status = NULL WHERE status LIKE ?', (f'Занято:{user_id}%',))

    conn.commit()
    conn.close()
    return redirect(url_for('admin'))

@app.route('/user/<code>')
def user(code):
    conn = sqlite3.connect('game.db')
    c = conn.cursor()

    c.execute('SELECT id, name, rating, selected_card_id FROM users WHERE code = ?', (code,))
    user = c.fetchone()
    if not user:
        return "Пользователь не найден", 404

    user_id, name, rating, selected_card_id = user

    c.execute('SELECT rowid, subfolder, image FROM images WHERE status = ?', (f'Занято:{user_id}',))
    cards = c.fetchall()

    selected_card = None
    if selected_card_id:
        c.execute('SELECT subfolder, image FROM images WHERE rowid = ?', (selected_card_id,))
        selected_card = c.fetchone()

    conn.close()
    return render_template('user.html', name=name, rating=rating, cards=cards, code=code, selected_card=selected_card)

@app.route('/select_card/<code>/<int:card_id>', methods=['POST'])
def select_card(code, card_id):
    conn = sqlite3.connect('game.db')
    c = conn.cursor()

    c.execute('SELECT id, selected_card_id FROM users WHERE code = ?', (code,))
    user = c.fetchone()
    if not user:
        conn.close()
        return "Пользователь не найден", 404

    user_id, current_selection = user

    if current_selection:
        c.execute('UPDATE images SET status = ? WHERE rowid = ?', (f'Занято:{user_id}', current_selection))

    c.execute('UPDATE users SET selected_card_id = ? WHERE id = ?', (card_id, user_id))
    c.execute('UPDATE images SET status = ? WHERE rowid = ?', (f'Выбрано:{user_id}', card_id))

    conn.commit()
    conn.close()
    return redirect(url_for('user', code=code))

if __name__ == '__main__':
    init_db()
    load_images()
    app.run(debug=True)
