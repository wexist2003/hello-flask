from flask import Flask, render_template, request, redirect, url_for
import sqlite3
import os
import string
import random

app = Flask(__name__)
DB_PATH = 'database.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Удаляем таблицы и создаем заново
    c.execute("DROP TABLE IF EXISTS users")
    c.execute("DROP TABLE IF EXISTS images")
    c.execute("DROP TABLE IF EXISTS settings")

    c.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            code TEXT UNIQUE NOT NULL,
            rating INTEGER DEFAULT 0
        )
    """)

    c.execute("""
        CREATE TABLE images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subfolder TEXT NOT NULL,
            image TEXT NOT NULL,
            status TEXT
        )
    """)

    c.execute("""
        CREATE TABLE settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # Загрузка изображений из static/images
    image_folders = ['koloda1', 'koloda2']
    for folder in image_folders:
        folder_path = os.path.join('static', 'images', folder)
        if os.path.exists(folder_path):
            for filename in os.listdir(folder_path):
                if filename.endswith('.jpg'):
                    c.execute("INSERT INTO images (subfolder, image, status) VALUES (?, ?, 'Свободно')", (folder, filename))

    # Удаляем статусы "Занято" (при новом запуске)
    c.execute("UPDATE images SET status = 'Свободно'")

    conn.commit()
    conn.close()

def generate_unique_code(length=8):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def get_setting(key):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def set_setting(key, value):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

@app.route("/")
def index():
    return "<h1>Hello, world!</h1><p><a href='/admin'>Перейти в админку</a></p>"

@app.route("/admin", methods=["GET", "POST"])
def admin():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    message = ""
    
    if request.method == "POST":
        if "name" in request.form:
            name = request.form.get("name").strip()
            num_cards = int(request.form.get("num_cards", 3))
            code = generate_unique_code()

            try:
                c.execute("INSERT INTO users (name, code) VALUES (?, ?)", (name, code))
                user_id = c.lastrowid

                # Назначаем карточки пользователю из активной колоды
                active_subfolder = get_setting("active_subfolder")
                if active_subfolder:
                    c.execute("""
                        SELECT id, subfolder, image FROM images
                        WHERE subfolder = ? AND status = 'Свободно'
                        LIMIT ?
                    """, (active_subfolder, num_cards))
                    cards = c.fetchall()

                    for card in cards:
                        c.execute("UPDATE images SET status = ? WHERE id = ?", (f"Занято:{user_id}", card[0]))

                conn.commit()
                message = f"Пользователь '{name}' добавлен."

            except sqlite3.IntegrityError:
                message = f"Имя '{name}' уже существует."

        elif "active_subfolder" in request.form:
            selected = request.form.get("active_subfolder")
            set_setting("active_subfolder", selected)
            # Сделать все другие изображения занятыми
            c.execute("UPDATE images SET status = 'Занято' WHERE subfolder != ?", (selected,))
            c.execute("UPDATE images SET status = 'Свободно' WHERE subfolder = ?", (selected,))
            conn.commit()
            message = f"Выбран подкаталог: {selected}"

    # Получение данных
    c.execute("SELECT id, name, code, rating FROM users ORDER BY name ASC")
    users = c.fetchall()

    c.execute("SELECT subfolder, image, status FROM images")
    images = c.fetchall()

    subfolders = ['koloda1', 'koloda2']
    active_subfolder = get_setting("active_subfolder") or ''

    conn.close()
    return render_template("admin.html", users=users, images=images, message=message,
                           subfolders=subfolders, active_subfolder=active_subfolder)

@app.route("/admin/delete/<int:user_id>", methods=["POST"])
def delete_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM users WHERE id = ?", (user_id,))
    c.execute("UPDATE images SET status = 'Свободно' WHERE status = ?", (f"Занято:{user_id}",))
    conn.commit()
    conn.close()
    return redirect(url_for("admin"))

@app.route("/user/<code>")
def user(code):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, name, rating FROM users WHERE code = ?", (code,))
    row = c.fetchone()

    if not row:
        conn.close()
        return "<h1>Пользователь не найден</h1>", 404

    user_id, name, rating = row

    c.execute("SELECT subfolder, image FROM images WHERE status = ?", (f"Занято:{user_id}",))
    cards = [{"subfolder": r[0], "image": r[1]} for r in c.fetchall()]
    conn.close()

    return render_template("user.html", name=name, rating=rating, cards=cards)

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
