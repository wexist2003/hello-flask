import sqlite3
import random
import string
from flask import Flask, render_template, request, redirect, url_for

app = Flask(__name__)

# Подключение к базе данных SQLite
def get_db():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn

# Генерация уникального кода
def generate_unique_code(length=6):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

@app.route('/admin', methods=["GET", "POST"])
def admin():
    conn = get_db()
    c = conn.cursor()

    # Создание таблиц, если они не существуют
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    code TEXT UNIQUE NOT NULL
                )''')

    c.execute('''CREATE TABLE IF NOT EXISTS images (
                    id INTEGER PRIMARY KEY,
                    subfolder TEXT NOT NULL,
                    image TEXT NOT NULL,
                    status TEXT
                )''')

    c.execute('''CREATE TABLE IF NOT EXISTS user_images (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER,
                    image_id INTEGER,
                    FOREIGN KEY (user_id) REFERENCES users(id),
                    FOREIGN KEY (image_id) REFERENCES images(id)
                )''')
    
    # Заполнение таблицы images (например, вручную или при запуске)
    # Если данных нет, добавьте их

    # Получение всех пользователей
    c.execute("SELECT * FROM users ORDER BY name")
    users = c.fetchall()

    # Получение всех изображений
    c.execute("SELECT * FROM images")
    images = c.fetchall()

    message = ""
    
    if request.method == "POST":
        if "add_user" in request.form:
            # Добавление нового пользователя
            name = request.form.get("name")
            image_count = int(request.form.get("image_count"))
            
            if name:
                code = generate_unique_code()
                try:
                    c.execute("INSERT INTO users (name, code) VALUES (?, ?)", (name.strip(), code))
                    conn.commit()
                    message = f"Пользователь '{name}' добавлен."

                    # Получаем все изображения без статуса
                    c.execute("SELECT id, subfolder, image FROM images WHERE status IS NULL")
                    available_images = c.fetchall()
                    
                    if len(available_images) < image_count:
                        message = "Недостаточно доступных изображений."
                        return render_template("admin.html", users=users, message=message, images=images, image_count=image_count)
                    
                    chosen_images = random.sample(available_images, min(image_count, len(available_images)))

                    for image in chosen_images:
                        c.execute("UPDATE images SET status = 'Занято' WHERE id = ?", (image[0],))
                        c.execute("INSERT INTO user_images (user_id, image_id) VALUES (?, ?)", (code, image[0]))
                    conn.commit()
                    
                except sqlite3.IntegrityError as e:
                    message = f"Ошибка базы данных: {str(e)}"
                except Exception as e:
                    message = f"Произошла ошибка: {str(e)}"

        elif "select_group" in request.form:
            # Выбор группы и изменение статуса изображений
            selected_group = request.form.get("group")
            c.execute("UPDATE images SET status = 'Занято' WHERE subfolder != ?", (selected_group,))
            conn.commit()
            message = f"Все изображения из других групп помечены как занятые."

        conn.close()
        return render_template("admin.html", users=users, message=message, images=images)
    
    return render_template("admin.html", users=users, message=message, images=images)

if __name__ == "__main__":
    app.run(debug=True)
