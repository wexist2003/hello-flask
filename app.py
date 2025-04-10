import sqlite3
import os
import random
from flask import Flask, render_template, request, redirect, url_for

app = Flask(__name__)

DB_PATH = 'database.db'
IMAGES_DIR = 'images'


# Инициализация базы данных
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Удаляем таблицы, если они существуют
    c.execute("DROP TABLE IF EXISTS users")
    c.execute("DROP TABLE IF EXISTS user_images")
    c.execute("DROP TABLE IF EXISTS images")

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
            user_id INTEGER NOT NULL,
            image_id INTEGER NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (image_id) REFERENCES images (id)
        )
    """)

    conn.commit()
    conn.close()


# Генерация уникального кода для пользователей
def generate_unique_code():
    code = ''.join(random.choices('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', k=8))
    return code


# Загрузка изображений из папки и запись в базу данных
def load_images():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    for subfolder in os.listdir(IMAGES_DIR):
        subfolder_path = os.path.join(IMAGES_DIR, subfolder)
        if os.path.isdir(subfolder_path):
            for filename in os.listdir(subfolder_path):
                if filename.endswith('.jpg'):
                    image_name = filename
                    c.execute("INSERT INTO images (subfolder, image) VALUES (?, ?)", (subfolder, image_name))

    conn.commit()
    conn.close()


# Главная страница для админа
@app.route("/admin", methods=["GET", "POST"])
def admin():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    message = ""
    image_count = 0

    if request.method == "POST":
        name = request.form.get("name")
        image_count = int(request.form.get("image_count"))

        if name:
            code = generate_unique_code()
            try:
                c.execute("INSERT INTO users (name, code) VALUES (?, ?)", (name.strip(), code))
                conn.commit()
                message = f"Пользователь '{name}' добавлен."

                c.execute("SELECT id, subfolder, image FROM images WHERE status IS NULL")
                available_images = c.fetchall()

                chosen_images = random.sample(available_images, min(image_count, len(available_images)))

                for image in chosen_images:
                    c.execute("UPDATE images SET status = 'Занято' WHERE id = ?", (image[0],))
                    c.execute("INSERT INTO user_images (user_id, image_id) VALUES (?, ?)", (code, image[0]))
                conn.commit()
            except sqlite3.IntegrityError:
                message = f"Имя '{name}' уже существует."

    c.execute("SELECT id, name, code, rating FROM users ORDER BY name ASC")
    users = c.fetchall()
    conn.close()

    return render_template("admin.html", users=users, message=message, image_count=image_count)


# Страница для настройки изображений
@app.route("/admin/set_images_status", methods=["POST"])
def set_images_status():
    group = request.form.get("group")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("UPDATE images SET status = 'Занято' WHERE subfolder != ?", (group,))
    conn.commit()
    conn.close()

    return redirect(url_for('admin_images'))


# Страница с изображениями
@app.route("/admin/images")
def admin_images():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("SELECT id, subfolder, image, status FROM images")
    images = c.fetchall()

    conn.close()

    return render_template("admin_images.html", images=images)


# Стартовый маршрут для пользователя
@app.route("/user/<user_code>")
def user_page(user_code):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("SELECT name FROM users WHERE code = ?", (user_code,))
    user = c.fetchone()

    c.execute("""
        SELECT i.subfolder, i.image
        FROM images i
        JOIN user_images ui ON i.id = ui.image_id
        JOIN users u ON u.id = ui.user_id
        WHERE u.code = ?
    """, (user_code,))
    images = c.fetchall()

    conn.close()

    return render_template("user.html", user=user, images=images)


if __name__ == "__main__":
    init_db()
    load_images()
    app.run(debug=True)
