import os
import sqlite3
import random
import string
from flask import Flask, render_template, request, redirect, url_for

app = Flask(__name__)

def init_db():
    conn = sqlite3.connect('database.db')
    c = conn.cursor()

    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            code TEXT UNIQUE,
            rating INTEGER DEFAULT 0
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subfolder TEXT,
            image TEXT,
            status TEXT
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS common_table (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_id INTEGER,
            user_id INTEGER
        )
    ''')

    conn.commit()
    conn.close()

def scan_images():
    image_folder = os.path.join('static', 'images')
    conn = sqlite3.connect('database.db')
    c = conn.cursor()

    for subfolder in os.listdir(image_folder):
        subfolder_path = os.path.join(image_folder, subfolder)
        if os.path.isdir(subfolder_path):
            for image in os.listdir(subfolder_path):
                c.execute('SELECT * FROM images WHERE subfolder = ? AND image = ?', (subfolder, image))
                if not c.fetchone():
                    c.execute('INSERT INTO images (subfolder, image, status) VALUES (?, ?, NULL)', (subfolder, image))

    conn.commit()
    conn.close()

@app.route('/')
def index():
    visits = int(request.cookies.get('visits', 0)) + 1
    resp = app.make_response(render_template('index.html', visits=visits))
    resp.set_cookie('visits', str(visits))
    return resp

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    conn = sqlite3.connect('database.db')
    c = conn.cursor()

    message = ''
    active_subfolder = request.form.get('active_subfolder', '')

    if request.method == 'POST':
        name = request.form.get('name')
        num_cards = int(request.form.get('num_cards', 3))
        if name:
            code = ''.join(random.choices(string.ascii_letters + string.digits, k=6))
            c.execute("INSERT INTO users (name, code) VALUES (?, ?)", (name, code))
            user_id = c.lastrowid

            c.execute("SELECT id FROM images WHERE status IS NULL")
            available = c.fetchall()
            selected = random.sample(available, min(num_cards, len(available)))
            for img_id, in selected:
                c.execute("UPDATE images SET status = ? WHERE id = ?", (f"Занято:{user_id}", img_id))
            conn.commit()
            message = f'Пользователь {name} добавлен.'

        if 'active_subfolder' in request.form:
            active_subfolder = request.form.get('active_subfolder')

    c.execute("SELECT id, name, code, rating FROM users")
    users = c.fetchall()

    c.execute("SELECT DISTINCT subfolder FROM images")
    subfolders = [row[0] for row in c.fetchall()]

    if not active_subfolder and subfolders:
        active_subfolder = subfolders[0]

    c.execute("SELECT subfolder, image, status FROM images WHERE subfolder = ?", (active_subfolder,))
    images = c.fetchall()

    conn.close()
    return render_template('admin.html', users=users, images=images, subfolders=subfolders,
                           active_subfolder=active_subfolder, message=message)

@app.route('/delete_user/<int:user_id>', methods=['POST'])
def delete_user(user_id):
    conn = sqlite3.connect('database.db')
    c = conn.cursor()

    c.execute("DELETE FROM users WHERE id = ?", (user_id,))
    c.execute("UPDATE images SET status = NULL WHERE status = ?", (f"Занято:{user_id}",))
    c.execute("DELETE FROM common_table WHERE user_id = ?", (user_id,))

    conn.commit()
    conn.close()
    return redirect(url_for('admin'))

@app.route('/user/<code>')
def user(code):
    conn = sqlite3.connect('database.db')
    c = conn.cursor()

    c.execute("SELECT * FROM users WHERE code = ?", (code,))
    user = c.fetchone()
    if not user:
        conn.close()
        return "Пользователь не найден", 404

    c.execute("SELECT id, subfolder, image FROM images WHERE status = ?", (f"Занято:{user[0]}",))
    cards = [dict(id=row[0], subfolder=row[1], image=row[2]) for row in c.fetchall()]

    c.execute('''
        SELECT images.subfolder, images.image
        FROM common_table
        JOIN images ON common_table.image_id = images.id
    ''')
    common_cards = c.fetchall()

    conn.close()
    return render_template('user.html', name=user[1], rating=user[3], cards=cards, common_cards=common_cards)

@app.route('/select_card/<int:card_id>', methods=['POST'])
def select_card(card_id):
    conn = sqlite3.connect('database.db')
    c = conn.cursor()

    c.execute("SELECT id, status FROM images WHERE id = ?", (card_id,))
    image = c.fetchone()

    if image and image[1] and image[1].startswith("Занято:"):
        user_id = int(image[1].split(":")[1])

        c.execute("INSERT INTO common_table (image_id, user_id) VALUES (?, ?)", (card_id, user_id))
        c.execute("UPDATE images SET status = 'Общий' WHERE id = ?", (card_id,))
        conn.commit()

    conn.close()
    return redirect(request.referrer)

@app.route('/admin/images')
def admin_images():
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute("SELECT subfolder, image, status FROM images")
    images = c.fetchall()
    conn.close()
    return render_template('admin_images.html', images=images)

if __name__ == '__main__':
    init_db()
    scan_images()
    app.run(debug=True)
