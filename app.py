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

    # Удаляем таблицу, если она существует, и создаем заново
    c.execute("DROP TABLE IF EXISTS users")
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            code TEXT UNIQUE NOT NULL,
            rating INTEGER DEFAULT 0
        )
    """)

    # Создаем таблицу для изображений
    c.execute("DROP TABLE IF EXISTS images")
    c.execute("""
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subfolder TEXT NOT NULL,
            image TEXT NOT NULL,
            status TEXT
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
    conn.commit()
    conn.close()


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

@app.route("/admin/set_images_status", methods=["POST"])
def set_images_status():
    group = request.form.get("group")  # Получаем группу, выбранную администратором
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Обновляем статус всех изображений, которые не находятся в выбранной группе
    c.execute("UPDATE images SET status = 'Занято' WHERE subfolder != ?", (group,))
    conn.commit()
    conn.close()
    
    return redirect(url_for('admin_images'))


@app.route("/")
def index():
    return "<h1>Hello, world!</h1><p><a href='/admin'>Перейти в админку</a></p>"

@app.route("/admin", methods=["GET", "POST"])
def admin():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    message = ""
    image_count = 0  # Количество изображений, которое назначаем пользователю

    if request.method == "POST":
        name = request.form.get("name")
        image_count = int(request.form.get("image_count"))  # Получаем количество изображений

        if name:
            code = generate_unique_code()
            try:
                # Создаем нового пользователя
                c.execute("INSERT INTO users (name, code) VALUES (?, ?)", (name.strip(), code))
                conn.commit()
                message = f"Пользователь '{name}' добавлен."
                
                # Получаем все изображения без статуса "Занято"
                c.execute("SELECT id, subfolder, image FROM images WHERE status IS NULL")
                available_images = c.fetchall()

                # Случайным образом выбираем необходимое количество изображений
                chosen_images = random.sample(available_images, min(image_count, len(available_images)))

                for image in chosen_images:
                    # Назначаем изображение пользователю
                    c.execute("UPDATE images SET status = 'Занято' WHERE id = ?", (image[0],))
                    c.execute("INSERT INTO user_images (user_id, image_id) VALUES (?, ?)", (code, image[0]))
                conn.commit()
            except sqlite3.IntegrityError:
                message = f"Имя '{name}' уже существует."

    c.execute("SELECT id, name, code, rating FROM users ORDER BY name ASC")
    users = c.fetchall()
    conn.close()
    return render_template("admin.html", users=users, message=message, image_count=image_count)



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
    conn.close()
    if row:
        name, rating = row
        return render_template("user.html", name=name, rating=rating)
    else:
        return "<h1>Пользователь не найден</h1>", 404

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
