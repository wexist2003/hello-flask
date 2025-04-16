import os
import sqlite3
import random
import string
from flask import Flask, render_template, request, redirect, url_for, g

app = Flask(__name__)
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
    visits = int(request.cookies.get("visits", 0)) + 1
    response = render_template("index.html", visits=visits)
    resp = app.make_response(response)
    resp.set_cookie("visits", str(visits))
    return resp

# ──────── АДМИНКА ────────

@app.route("/admin", methods=["GET", "POST"])
def admin():
    db = get_db()
    message = ""

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

        # Выбор случайных изображений
        images = os.listdir(f"static/images/{active_subfolder}")
        selected_images = random.sample(images, min(num_cards, len(images)))

        for img in selected_images:
            db.execute("INSERT INTO cards (user_id, subfolder, image, status) VALUES (?, ?, ?, NULL)",
                       (user_id, active_subfolder, img))
        db.commit()
        message = f"Пользователь {name} добавлен."

    users = db.execute("SELECT * FROM users").fetchall()
    images = db.execute("SELECT subfolder, image, status FROM cards").fetchall()

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
    app.run(debug=True)
