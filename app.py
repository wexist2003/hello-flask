import json
from flask import Flask, render_template, request, redirect, url_for, g
import sqlite3
import os
import string
import random
import logging

# Настраиваем логирование
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
DB_PATH = 'database.db'
app.secret_key = "super secret"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    try:
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
                status TEXT,
                owner_id INTEGER,
                guesses TEXT
            )
        """)

        c.execute("""
            CREATE TABLE settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        image_folders = ['koloda1', 'koloda2']
        for folder in image_folders:
            folder_path = os.path.join('static', 'images', folder)
            if os.path.exists(folder_path):
                for filename in os.listdir(folder_path):
                    if filename.endswith('.jpg'):
                        c.execute("INSERT INTO images (subfolder, image, status, owner_id, guesses) VALUES (?, ?, 'Свободно', NULL, '{}')", (folder, filename))

        c.execute("UPDATE images SET status = 'Свободно'")

        # Initialize leading_user_id (e.g., set the first user as the leader)
        c.execute("SELECT id FROM users ORDER BY id ASC LIMIT 1")
        first_user = c.fetchone()
        if first_user:
            c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('leading_user_id', ?)", (first_user[0],))

        conn.commit()
    except sqlite3.Error as e:
        conn.rollback()
        logger.error(f"Ошибка при инициализации БД: {e}")
    finally:
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

def get_leading_user_id():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key = 'leading_user_id'")
    result = c.fetchone()
    conn.close()
    if result:
        return int(result[0])
    return None

def set_leading_user_id(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("REPLACE INTO settings (key, value) VALUES ('leading_user_id', ?)", (user_id,))
    conn.commit()
    conn.close()

@app.before_request
def before_request():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    code = None
    if request.view_args and 'code' in request.view_args:
        code = request.view_args.get('code')
    elif request.args and 'code' in request.args:
        code = request.args.get('code')
    if code:
        c.execute("SELECT id FROM users WHERE code = ?", (code,))
        user_id = c.fetchone()
        if user_id:
            g.user_id = user_id[0]
        else:
            g.user_id = None
    else:
        g.user_id = None

    show_card_info = get_setting("show_card_info")
    g.show_card_info = show_card_info == "true"

    conn.close()

def get_user_name(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT name FROM users WHERE id = ?", (user_id,))
    user_name = c.fetchone()
    conn.close()
    if user_name:
        return user_name[0]
    return None

app.jinja_env.globals.update(get_user_name=get_user_name, g=g, get_leading_user_id=get_leading_user_id)

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

                active_subfolder = get_setting("active_subfolder")
                if active_subfolder:
                    c.execute("""
                        SELECT id, subfolder, image
                        FROM images
                        WHERE subfolder = ?
                        AND status = 'Свободно'
                    """, (active_subfolder,))
                    available_cards = c.fetchall()

                    if len(available_cards) < num_cards:
                        message = f"Недостаточно свободных карточек в колоде {active_subfolder}."
                    else:
                        random.shuffle(available_cards)
                        selected_cards = available_cards[:num_cards]

                        for card in selected_cards:
                            c.execute("UPDATE images SET status = ? WHERE id = ?", (f"Занято:{user_id}", card[0]))

                    conn.commit()
                    message = f"Пользователь '{name}' добавлен."

            except sqlite3.IntegrityError:
                message = f"Имя '{name}' уже существует."

        elif "active_subfolder" in request.form:
            selected = request.form.get("active_subfolder")
            set_setting("active_subfolder", selected)
            c.execute("UPDATE images SET status  = 'Занято' WHERE subfolder != ?", (selected,))
            c.execute("UPDATE images SET status = 'Свободно' WHERE subfolder = ?", (selected,))
            conn.commit()
            message = f"Выбран подкаталог: {selected}"

    c.execute("SELECT id, name, code, rating FROM users ORDER BY name ASC")
    users = c.fetchall()

    c.execute("SELECT subfolder, image, status FROM images")
    images = c.fetchall()

    guess_counts_by_user = {}
    for user in users:
        user_id = user[0]
        guess_counts_by_user[user_id] = 0

    c.execute("SELECT guesses FROM images WHERE guesses != '{}'")
    images_with_guesses = c.fetchall()
    for image_guesses_row in images_with_guesses:
        guesses = json.loads(image_guesses_row[0])
        for guesser_id, guessed_user_id in guesses.items():
            if guesser_id in guess_counts_by_user:
                guess_counts_by_user[int(guesser_id)] += 1

    all_guesses = {}
    c.execute("SELECT id, guesses FROM images WHERE guesses != '{}'")
    all_guesses_data = c.fetchall()
    for image_id, guesses_str in all_guesses_data:
        all_guesses[image_id] = json.loads(guesses_str)

    subfolders = ['koloda1', 'koloda2']
    active_subfolder = get_setting("active_subfolder") or ''

    show_card_info = get_setting("show_card_info") == "true"

    conn.close()
    return render_template("admin.html", users=users, images=images, message=message,
                           subfolders=subfolders, active_subfolder=active_subfolder,
                           guess_counts_by_user=guess_counts_by_user, all_guesses=all_guesses,
                           show_card_info=show_card_info)

@app.route("/admin/delete/<int:user_id>", methods=["POST"])
def delete_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM users WHERE id = ?", (user_id,))
    c.execute("UPDATE images SET status = 'Свободно' WHERE status = ?", (f"Занято:{user_id}",))
    conn.commit()
    conn.close()
    return redirect(url_for("admin"))

@app.before_request
def before_request():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    code = None
    if request.view_args and 'code' in request.view_args:
        code = request.view_args.get('code')
    elif request.args and 'code' in request.args:
        code = request.args.get('code')
    if code:
        c.execute("SELECT id FROM users WHERE  code = ?", (code,))
        user_id = c.fetchone()
        if user_id:
            g.user_id = user_id[0]
        else:
            g.user_id = None
    else:
        g.user_id = None

    show_card_info = get_setting("show_card_info")
    g.show_card_info = show_card_info == "true"

    conn.close()

@app.route("/user/<code>/guess/<int:image_id>", methods=["POST"])
def guess_image(code, image_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    user_id = g.user_id  #  Используем g.user_id

    guessed_user_id = request.form.get("guessed_user_id")
    if not guessed_user_id:
        conn.close()
        return "No user selected", 400

    c.execute("SELECT guesses FROM images WHERE id = ?", (image_id,))
    image_data = c.fetchone()
    guesses = json.loads(image_data[0]) if image_data and image_data[0] else {}

    guesses[str(user_id)] = int(guessed_user_id)

    c.execute("UPDATE images SET guesses = ? WHERE id = ?", (json.dumps(guesses), image_id))

    conn.commit()
    conn.close()

    return redirect(url_for('user', code=code))

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

    c.execute("SELECT id, subfolder, image FROM images WHERE status = ?", (f"Занято:{user_id}",))
    cards = [{"id": r[0], "subfolder": r[1], "image": r[2]} for r in c.fetchall()]

    c.execute("SELECT id, subfolder, image, owner_id, guesses FROM images WHERE owner_id IS NOT NULL")
    table_images_data = c.fetchall()
    table_images = []
    for img in table_images_data:
        table_image = {
            "id": img[0],
            "subfolder": img[1],
            "image": img[2],
            "owner_id": img[3],
            "guesses": json.loads(img[4]) if img[4] else {},
        }
        table_images.append(table_image)

    c.execute("SELECT id, name FROM users", )
    all_users = c.fetchall()

    c.execute("SELECT 1 FROM images WHERE owner_id = ?", (user_id,))
    on_table = c.fetchone() is not None

    conn.close()

    show_card_info = get_setting("show_card_info") == "true"

    return render_template("user.html", name=name, rating=rating, cards=cards,
                           table_images=table_images, all_users=all_users,
                           code=code, on_table=on_table, g=g, show_card_info=show_card_info)
    

@app.route("/user/<code>/place/<int:image_id>", methods=["POST"])
def place_card(code, image_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    user_id = g.user_id

    c.execute("SELECT 1 FROM images WHERE owner_id = ?", (user_id,))
    if c.fetchone() is not None:
        conn.close()
        return "You already have a card on  the table", 400

    c.execute("UPDATE images SET owner_id = ?, status = 'На столе' WHERE id = ?", (user_id, image_id))
    conn.commit()
    conn.close()

    return redirect(url_for('user', code=code))

@app.route("/open_cards", methods=["POST"])
def open_cards():
    set_setting("show_card_info", "true")

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Получаем ID ведущего
    leading_user_id = get_leading_user_id()
    logger.info(f"Ведущий: {leading_user_id}")

    # Получаем все карточки на столе с предположениями
    c.execute("SELECT id, owner_id, guesses FROM images")
    table_images = c.fetchall()
    logger.info(f"Карточки: {table_images}")

    # Получаем ID всех пользователей
    c.execute("SELECT id FROM users")
    all_users = [user[0] for user in c.fetchall()]
    logger.info(f"Пользователи: {all_users}")

    # Словарь для хранения очков пользователей
    user_points = {user_id: 0 for user_id in all_users}
    logger.info(f"Начальные очки: {user_points}")

    for image in table_images:
        owner_id = image[1]
        guesses = json.loads(image[2]) if image[2] else {}
        correct_guesses = 0

        if owner_id is not None and owner_id == leading_user_id:  # Only for leading user's cards
            for guesser_id, guessed_user_id in guesses.items():
                if guessed_user_id == owner_id:
                    correct_guesses += 1

            if correct_guesses == len(all_users) - 1:
                user_points[owner_id] -= 3
            elif correct_guesses == 0:
                user_points[owner_id] -= 2
            else:
                user_points[owner_id] += 3 + correct_guesses

            for guesser_id, guessed_user_id in guesses.items():
                if guessed_user_id == owner_id:
                    user_points[int(guesser_id)] += 3

    # Обновление рейтинга пользователей в базе данных
    for user_id, points in user_points.items():
        c.execute("UPDATE users SET rating = rating + ? WHERE id = ?", (points, user_id))

    conn.commit()
    conn.close()

    #   Определяем следующего ведущего
    current_leading_user_id = get_leading_user_id()
    if current_leading_user_id is None:
        set_leading_user_id(1)
    else:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT MAX(id) FROM users")
        max_user_id = c.fetchone()[0]
        next_leading_user_id = current_leading_user_id + 1
        current_leading_user_id + 1
        if next_leading_user_id > max_user_id:
            next_leading_user_id = 1
        set_leading_user_id(next_leading_user_id)
        conn.close()

    return redirect(url_for("admin"))

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
