import sqlite3
import os
import random
import string
from flask import Flask, render_template, request, redirect, url_for

app = Flask(__name__)

DB_PATH = 'database.db'
IMAGES_DIR = 'images'

# Создание базы данных (если не существует)
def create_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            link TEXT NOT NULL,
            rating INTEGER DEFAULT 0
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subfolder TEXT NOT NULL,
            image TEXT NOT NULL,
            status TEXT
        )
    ''')
    conn.commit()
    conn.close()

# Инициализация базы данных и заполнение таблицы images
def initialize_images():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Очищаем таблицу images
    c.execute("DELETE FROM images")

    # Получаем список подкаталогов
    subfolders = [f for f in os.listdir(IMAGES_DIR) if os.path.isdir(os.path.join(IMAGES_DIR, f))]
    
    for subfolder in subfolders:
        images = [f for f in os.listdir(os.path.join(IMAGES_DIR, subfolder)) if f.endswith('.jpg')]
        for image in images:
            c.execute("INSERT INTO images (subfolder, image) VALUES (?, ?)", (subfolder, image))

    conn.commit()
    conn.close()

# Главная страница
@app.route('/')
def index():
    return render_template("index.html")

# Страница администрирования пользователей
@app.route('/admin', methods=["GET", "POST"])
def admin():
    message = ""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    if request.method == "POST":
        # Создание пользователя
        name = request.form['name']
        link = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
        c.execute("INSERT INTO users (name, link) VALUES (?, ?)", (name, link))
        conn.commit()
        message = f"Пользователь {name} был создан! Ссылка: /user/{link}"

    # Получаем всех пользователей
    c.execute("SELECT * FROM users ORDER BY name ASC")
    users = c.fetchall()
    conn.close()
    
    return render_template("admin.html", users=users, message=message)

# Страница пользователя
@app.route('/user/<link>')
def user_page(link):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE link = ?", (link,))
    user = c.fetchone()
    conn.close()
    return render_template("user_page.html", user=user)

# Страница с изображениями
@app.route("/admin/images", methods=["GET", "POST"])
def admin_images():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    if request.method == "POST":
        selected_group = request.form.get("subfolder")
        
        # Обновляем статус изображений, которые не принадлежат выбранной группе
        c.execute("""
            UPDATE images
            SET status = 'Занято'
            WHERE subfolder != ?
        """, (selected_group,))
        conn.commit()

    # Получаем все изображения из базы данных
    c.execute("SELECT subfolder, image, status FROM images")
    images = c.fetchall()
    conn.close()
    return render_template("admin_images.html", images=images)

# Удаление пользователя
@app.route('/admin/delete_user/<int:user_id>')
def delete_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin'))

# Главная функция
if __name__ == '__main__':
    create_db()
    initialize_images()
    app.run(debug=True)
