from flask import Flask, render_template, request, redirect, url_for
from flask import send_from_directory
import sqlite3
import os
import string
import random

app = Flask(__name__)
DB_PATH = 'database.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Создаем таблицу пользователей
    c.execute("DROP TABLE IF EXISTS users")
    c.execute(""" 
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            code TEXT UNIQUE NOT NULL,
            rating INTEGER DEFAULT 0,
            cards_count INTEGER DEFAULT 0
        )
    """)

    # Создаем таблицу изображений
    c.execute("DROP TABLE IF EXISTS images")
    c.execute(""" 
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subfolder TEXT NOT NULL,
            image TEXT NOT NULL,
            status TEXT DEFAULT 'Свободно'
        )
    """)

    # Создаем таблицу для назначения карт пользователям
    c.execute("DROP TABLE IF EXISTS user_images")
    c.execute(""" 
        CREATE TABLE IF NOT EXISTS user_images (
            user_id INTEGER,
            image_id INTEGER,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (image_id) REFERENCES images (id)
        )
    """)

    # Читаем изображения из папки
    image_folders = ['koloda1', 'koloda2']
    for folder in image_folders:
        folder_path = os.path.join('images', folder)
        if os.path.exists(folder_path):
            for filename in os.listdir(folder_path):
                if filename.endswith('.jpg'):
                    # Добавляем информацию о картинке в таблицу
                    image_name = filename
                    c.execute("INSERT INTO images (subfolder, image) VALUES (?, ?)", (folder, image_name))
    
    # Сбросить все статусы на "Свободно" при запуске
    c.execute("UPDATE images SET status = 'Свободно'")
    
    conn.commit()
    conn.close()


@app.route('/images/<path:filename>')
def serve_image(filename):
    return send_from_directory(os.path.join(app.root_path, 'images'), filename)


def generate_unique_code(length=8):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

@app.route("/admin/images")
def admin_images():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT subfolder, image, status FROM images")
    images = c.fetchall()
    conn.close()
    return render_template("admin_images.html", images=images)


@app.route("/")
def index():
    return "<h1>Hello, world!</h1><p><a href='/admin'>Перейти в админку</a></p>"

import random

@app.route("/admin", methods=["GET", "POST"])
def admin():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    message = ""

    # Получаем список подкаталогов
    c.execute("SELECT DISTINCT subfolder FROM images")
    subfolders = c.fetchall()

    if request.method == "POST":
        name = request.form.get("name")
        cards_count = request.form.get("cards_count", 0)
        selected_subfolder = request.form.get("subfolder")  # Выбранный подкаталог
        
        if name:
            code = generate_unique_code()
            try:
                # Добавляем нового пользователя в базу данных
                c.execute("INSERT INTO users (name, code, cards_count) VALUES (?, ?, ?)", (name.strip(), code, int(cards_count)))
                user_id = c.lastrowid
                conn.commit()
                
                # Выбираем случайные изображения для пользователя
                c.execute("SELECT id FROM images WHERE status = 'Свободно' LIMIT ?", (cards_count,))
                available_images = c.fetchall()

                if len(available_images) < int(cards_count):
                    message = "Недостаточно доступных изображений."
                else:
                    # Присваиваем изображения пользователю
                    for image in available_images:
                        c.execute("UPDATE images SET status = 'Занято' WHERE id = ?", (image[0],))
                        c.execute("INSERT INTO user_images (user_id, image_id) VALUES (?, ?)", (user_id, image[0]))
                    
                    conn.commit()
                    message = f"Пользователь '{name}' добавлен с {cards_count} картами."
            except sqlite3.IntegrityError:
                message = f"Имя '{name}' уже существует."

        # Обновляем статус изображений
        if selected_subfolder:
            c.execute("UPDATE images SET status = 'Занято' WHERE subfolder != ?", (selected_subfolder,))
            c.execute("UPDATE images SET status = 'Свободно' WHERE subfolder = ?", (selected_subfolder,))
            conn.commit()
            message = f"Изображения из подкаталога '{selected_subfolder}' теперь доступны."

    # Сортируем пользователей по имени
    c.execute("SELECT id, name, code, rating, cards_count FROM users ORDER BY name ASC")
    users = c.fetchall()

    # Получаем список изображений
    c.execute("SELECT subfolder, image, status FROM images")
    images = c.fetchall()

    conn.close()

    return render_template("admin.html", users=users, images=images, subfolders=subfolders, message=message)


@app.route("/admin/delete/<int:user_id>", methods=["POST"])
def delete_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("admin"))

@app.route("/user/<code>")
def user(code):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT name, rating FROM users WHERE code = ?", (code,))
    row = c.fetchone()

    if row:
        name, rating = row
        c.execute("""
            SELECT images.image, images.subfolder FROM user_images
            JOIN images ON user_images.image_id = images.id
            WHERE user_images.user_id = (SELECT id FROM users WHERE code = ?)
        """, (code,))
        cards = c.fetchall()
        conn.close()
        return render_template("user.html", name=name, rating=rating, cards=cards)
    else:
        conn.close()
        return "<h1>Пользователь не найден</h1>", 404


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
