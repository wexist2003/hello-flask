from flask import Flask, render_template, request, redirect, url_for
import sqlite3
import os
import random
import string

app = Flask(__name__)
DB_PATH = 'database.db'
IMAGE_DIR = 'static/images'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Создаем таблицы
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            code TEXT UNIQUE NOT NULL,
            rating INTEGER DEFAULT 0
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subfolder TEXT NOT NULL,
            image TEXT NOT NULL,
            status TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS user_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            image_id INTEGER,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (image_id) REFERENCES images(id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS chosen_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_id INTEGER,
            FOREIGN KEY (image_id) REFERENCES images(id)
        )
    """)

    # Чтение изображений
    image_folders = ['koloda1', 'koloda2']
    for folder in image_folders:
        folder_path = os.path.join('static', 'images', folder)
        if os.path.exists(folder_path):
            for filename in os.listdir(folder_path):
                if filename.endswith('.jpg'):
                    c.execute("INSERT INTO images (subfolder, image, status) VALUES (?, ?, 'Свободно')", (folder, filename))

    conn.commit()
    conn.close()

# Инициализация базы, если не существует
if not os.path.exists(DB_PATH):
    init_db()

def generate_code(length=8):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

@app.route("/", methods=["GET", "POST"])
def admin():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    message = None

    # Получение всех подкаталогов
    subfolders = [name for name in os.listdir(IMAGE_DIR) if os.path.isdir(os.path.join(IMAGE_DIR, name))]

    # Получение текущей активной колоды
    c.execute("SELECT subfolder FROM images WHERE status = 'Свободно' LIMIT 1")
    row = c.fetchone()
    active_subfolder = row[0] if row else None

    if request.method == "POST":
        if 'name' in request.form:
            name = request.form['name']
            num_cards = int(request.form.get('num_cards', 3))
            code = generate_code()
            c.execute("INSERT INTO users (name, code) VALUES (?, ?)", (name, code))
            user_id = c.lastrowid

            # Выбор случайных свободных изображений
            c.execute("SELECT id FROM images WHERE status = 'Свободно' AND subfolder = ?", (active_subfolder,))
            available_images = c.fetchall()
            selected_images = random.sample(available_images, min(num_cards, len(available_images)))
            for image_id_tuple in selected_images:
                image_id = image_id_tuple[0]
                c.execute("INSERT INTO user_images (user_id, image_id) VALUES (?, ?)", (user_id, image_id))
                c.execute("UPDATE images SET status = 'Занято' WHERE id = ?", (image_id,))

            conn.commit()
            message = f"Пользователь {name} добавлен."

        elif 'active_subfolder' in request.form:
            active_subfolder = request.form['active_subfolder']
            c.execute("UPDATE images SET status = 'Занято'")
            c.execute("UPDATE images SET status = 'Свободно' WHERE subfolder = ?", (active_subfolder,))
            message = f"Выбрана колода: {active_subfolder}"

        elif 'reset_all' in request.form:
            c.execute("DELETE FROM user_images")
            c.execute("DELETE FROM chosen_cards")
            c.execute("DELETE FROM users")
            c.execute("UPDATE images SET status = 'Свободно'")
            conn.commit()
            message = "Все пользователи удалены, статусы изображений сброшены."

    c.execute("SELECT * FROM users")
    users = c.fetchall()

    c.execute("SELECT subfolder, image, status FROM images")
    images = c.fetchall()

    conn.close()
    return render_template("admin.html", users=users, subfolders=subfolders,
                           active_subfolder=active_subfolder, images=images, message=message)

@app.route("/user/<code>")
def user(code):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("SELECT id, name, rating FROM users WHERE code = ?", (code,))
    row = c.fetchone()
    if not row:
        return "<h1>Пользователь не найден</h1>", 404

    user_id, name, rating = row

    c.execute("""
        SELECT images.id, images.subfolder, images.image
        FROM user_images
        JOIN images ON user_images.image_id = images.id
        WHERE user_images.user_id = ?
    """, (user_id,))
    my_cards = c.fetchall()

    c.execute("""
        SELECT images.subfolder, images.image
        FROM chosen_cards
        JOIN images ON chosen_cards.image_id = images.id
    """)
    table_cards = c.fetchall()

    conn.close()
    return render_template("user.html", name=name, rating=rating, my_cards=my_cards, table_cards=table_cards, code=code)

@app.route("/choose_card/<code>/<int:image_id>", methods=["POST"])
def choose_card(code, image_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
        DELETE FROM user_images
        WHERE image_id = ? AND user_id = (
            SELECT id FROM users WHERE code = ?
        )
    """, (image_id, code))

    c.execute("INSERT INTO chosen_cards (image_id) VALUES (?)", (image_id,))

    conn.commit()
    conn.close()

    return redirect(url_for('user', code=code))

@app.route("/delete_user/<int:user_id>", methods=["POST"])
def delete_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM user_images WHERE user_id = ?", (user_id,))
    c.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("admin"))

if __name__ == "__main__":
    app.run(debug=True)
