from flask import Flask, render_template, request, redirect, url_for
 import sqlite3
 import os
 import string
 import sqlite3
 import random
 import string
 from flask import Flask, render_template, request, redirect, url_for, g
 
 app = Flask(__name__)
 DB_PATH = 'database.db'
 DATABASE = "database.db"
 
 # ──────── БАЗА ДАННЫХ ────────
 
 def get_db():
     db = getattr(g, "_database", None)
     if db is None:
         db = g._database = sqlite3.connect(DATABASE)
         db.row_factory = sqlite3.Row
     return db
 
 @app.teardown_appcontext
 def close_connection(exception):
     db = getattr(g, "_database", None)
     if db is not None:
         db.close()
 
 # ──────── ИНИЦИАЛИЗАЦИЯ ────────
 
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
     with app.app_context():
         db = get_db()
         db.execute('''CREATE TABLE IF NOT EXISTS users (
                           id INTEGER PRIMARY KEY AUTOINCREMENT,
                           name TEXT NOT NULL,
                           code TEXT NOT NULL,
                           rating INTEGER DEFAULT 0
                       )''')
         db.execute('''CREATE TABLE IF NOT EXISTS cards (
                           id INTEGER PRIMARY KEY AUTOINCREMENT,
                           user_id INTEGER,
                           subfolder TEXT,
                           image TEXT,
                           status TEXT,
                           FOREIGN KEY(user_id) REFERENCES users(id)
                       )''')
         db.commit()
 
 init_db()
 
 # ──────── ГЛАВНАЯ ────────
 
 @app.route("/")
 def index():
     return "<h1>Hello, world!</h1><p><a href='/admin'>Перейти в админку</a></p>"
     visits = int(request.cookies.get("visits", 0)) + 1
     response = render_template("index.html", visits=visits)
     resp = app.make_response(response)
     resp.set_cookie("visits", str(visits))
     return resp
 
 # ──────── АДМИНКА ────────
 
 @app.route("/admin", methods=["GET", "POST"])
 def admin():
     conn = sqlite3.connect(DB_PATH)
     c = conn.cursor()
     db = get_db()
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
     # Активная колода
     subfolders = sorted(os.listdir("static/images"))
     active_subfolder = request.form.get("active_subfolder") or request.cookies.get("active_subfolder") or subfolders[0]
 
     # Добавление пользователя
     if request.method == "POST" and "name" in request.form:
         name = request.form["name"]
         num_cards = int(request.form.get("num_cards", 3))
         code = "".join(random.choices(string.ascii_letters + string.digits, k=8))
 
         db.execute("INSERT INTO users (name, code) VALUES (?, ?)", (name, code))
         user_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
 
     if not row:
         conn.close()
         return "<h1>Пользователь не найден</h1>", 404
         # Выбор случайных изображений
         images = os.listdir(f"static/images/{active_subfolder}")
         selected_images = random.sample(images, min(num_cards, len(images)))
 
     user_id, name, rating = row
         for img in selected_images:
             db.execute("INSERT INTO cards (user_id, subfolder, image, status) VALUES (?, ?, ?, NULL)",
                        (user_id, active_subfolder, img))
         db.commit()
         message = f"Пользователь {name} добавлен."
 
     c.execute("SELECT subfolder, image FROM images WHERE status = ?", (f"Занято:{user_id}",))
     cards = [{"subfolder": r[0], "image": r[1]} for r in c.fetchall()]
     conn.close()
     users = db.execute("SELECT * FROM users").fetchall()
     images = db.execute("SELECT subfolder, image, status FROM cards").fetchall()
 
     return render_template("user.html", name=name, rating=rating, cards=cards)
     resp = app.make_response(render_template("admin.html", users=users, images=images,
                                              subfolders=subfolders, active_subfolder=active_subfolder, message=message))
     resp.set_cookie("active_subfolder", active_subfolder)
     return resp
 
 @app.route("/admin/images")
 def admin_images():
     db = get_db()
     images = db.execute("SELECT subfolder, image, status FROM cards").fetchall()
     return render_template("admin_images.html", images=images)
 
 @app.route("/delete_user/<int:user_id>", methods=["POST"])
 def delete_user(user_id):
     db = get_db()
     db.execute("DELETE FROM users WHERE id = ?", (user_id,))
     db.execute("DELETE FROM cards WHERE user_id = ?", (user_id,))
     db.commit()
     return redirect(url_for("admin"))
 
 # ──────── ПОЛЬЗОВАТЕЛЬ ────────
 
 @app.route("/user/<code>")
 def user(code):
     db = get_db()
     user_row = db.execute("SELECT * FROM users WHERE code = ?", (code,)).fetchone()
     if user_row is None:
         return "Пользователь не найден", 404
 
     user_id = user_row["id"]
     name = user_row["name"]
     rating = user_row["rating"]
 
     # Карты пользователя, которые не на общем столе
     cards = db.execute("SELECT * FROM cards WHERE user_id = ? AND (status IS NULL OR status != 'Общий')", (user_id,)).fetchall()
 
     # Общий стол: только по одной карте от каждого пользователя
     common_cards = db.execute("""
         SELECT c.*
         FROM cards c
         INNER JOIN (
             SELECT user_id, MIN(id) AS min_id
             FROM cards
             WHERE status = 'Общий'
             GROUP BY user_id
         ) t ON c.id = t.min_id
     """).fetchall()
 
     return render_template("user.html", name=name, rating=rating, cards=cards, common_cards=common_cards)
 
 @app.route("/select_card/<int:card_id>", methods=["POST"])
 def select_card(card_id):
     db = get_db()
 
     card = db.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
     if not card:
         return "Карточка не найдена", 404
 
     # Проверка: уже есть карта от этого пользователя в общем столе?
     existing = db.execute("SELECT * FROM cards WHERE status = 'Общий' AND user_id = ?", (card["user_id"],)).fetchone()
     if existing:
         return redirect(url_for("user", code=get_user_code(card["user_id"])))
 
     db.execute("UPDATE cards SET status = 'Общий' WHERE id = ?", (card_id,))
     db.commit()
     return redirect(url_for("user", code=get_user_code(card["user_id"])))
 
 def get_user_code(user_id):
     db = get_db()
     row = db.execute("SELECT code FROM users WHERE id = ?", (user_id,)).fetchone()
     return row["code"] if row else None
 
 # ──────── ЗАПУСК ────────
 
 if __name__ == "__main__":
     init_db()
     port = int(os.environ.get("PORT", 5000))
     app.run(host="0.0.0.0", port=port)
     app.run(debug=True)
