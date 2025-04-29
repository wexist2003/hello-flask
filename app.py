import json
from flask import Flask, render_template, request, redirect, url_for, g
import sqlite3
import os
import string
import random
from collections import defaultdict

app = Flask(__name__)
DB_PATH = 'database.db'
app.secret_key = "super secret"  # Needed for flash messages

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    #   Удаляем таблицы и создаем заново
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
    """)  #

    c.execute("""
        CREATE TABLE images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subfolder TEXT NOT NULL,
            image TEXT NOT NULL,
            status TEXT,
            owner_id INTEGER,  -- New column
            guesses TEXT       -- New column
        )
    """)  #

    c.execute("""
        CREATE TABLE settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)  #

    #   Загрузка изображений из static/images
    image_folders = ['koloda1', 'koloda2']
    for folder in image_folders:
        folder_path = os.path.join('static', 'images', folder)
        if os.path.exists(folder_path):
            for filename in os.listdir(folder_path):
                if filename.endswith('.jpg'):
                    c.execute("INSERT INTO images (subfolder, image, status, owner_id, guesses) VALUES (?, ?, 'Свободно', NULL, '{}')", (folder, filename))  # Initialize new columns

    #   Удаляем статусы "Занято" (при новом запуске)
    c.execute("UPDATE images SET status = 'Свободно'")  #

    conn.commit()
    conn.close()

def generate_unique_code(length=8):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))  #

def get_setting(key):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None  #

def set_setting(key, value):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()  #

def get_leading_user_id():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key = 'leading_user_id'")
    result = c.fetchone()
    conn.close()
    if result:
        return int(result[0])
    return None  #

def set_leading_user_id(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("REPLACE INTO settings (key, value) VALUES ('leading_user_id', ?)", (user_id,))
    conn.commit()
    conn.close()  #

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

    # Get the show_card_info setting and make it available globally
    show_card_info = get_setting("show_card_info")
    g.show_card_info = show_card_info == "true"

    conn.close()  #

def get_user_name(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT name FROM users WHERE id = ?", (user_id,))
    user_name = c.fetchone()
    conn.close()
    if user_name:
        return user_name[0]
    return None  #

app.jinja_env.globals.update(get_user_name=get_user_name, g=g, get_leading_user_id=get_leading_user_id) # Make the function globally available

@app.route("/")
def index():
    return "<h1>Hello, world!</h1><p><a href='/admin'>Перейти в админку</a></p>"  #

@app.route("/admin", methods=["GET", "POST"])
def admin():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    message = ""

    if request.method == "POST":
        if "name" in request.form:
            name = request.form.get("name").strip()
            num_cards = int(request.form.get("num_cards", 3))
            code = generate_unique_code()  #

            try:
                c.execute("INSERT INTO users (name, code) VALUES (?, ?)", (name, code))
                user_id = c.lastrowid

                #   Назначаем карточки пользователю из активной колоды в случайном порядке
                active_subfolder = get_setting("active_subfolder")  #
                if active_subfolder:
                    c.execute("""
                        SELECT id, subfolder, image
                        FROM images
                        WHERE subfolder = ?
                        AND status = 'Свободно'
                    """, (active_subfolder,))  #
                    available_cards = c.fetchall()

                    if len(available_cards) < num_cards:
                        message = f"Недостаточно свободных карточек в колоде {active_subfolder}."  #
                    else:
                        random.shuffle(available_cards)  #   Перемешиваем карточки
                        selected_cards = available_cards[:num_cards]  #   Выбираем нужное количество

                        for card in selected_cards:
                            c.execute("UPDATE images SET status = ? WHERE id = ?", (f"Занято:{user_id}", card[0]))  #

                    conn.commit()
                    message = f"Пользователь '{name}' добавлен."  #

            except sqlite3.IntegrityError:
                message = f"Имя '{name}' уже существует."  #

        elif "active_subfolder" in request.form:
            selected = request.form.get("active_subfolder")
            set_setting("active_subfolder", selected)
            #   Сделать все другие изображения занятыми
            c.execute("UPDATE images SET status = 'Занято' WHERE subfolder != ?", (selected,))
            c.execute("UPDATE images SET status = 'Свободно' WHERE subfolder = ?", (selected,))
            conn.commit()
            message = f"Выбран подкаталог: {selected}"  #

    #   Получение данных
    c.execute("SELECT id, name, code, rating FROM users ORDER BY name ASC")
    users = c.fetchall()

    c.execute("SELECT subfolder, image, status FROM images")
    images = c.fetchall()

    #   Get guess counts by each user
    guess_counts_by_user = {}
    for user in users:
        user_id = user[0]
        guess_counts_by_user[user_id] = 0

    c.execute("SELECT guesses FROM images WHERE guesses != '{}'")
    images_with_guesses = c.fetchall()
    for image_guesses_row in images_with_guesses:
        guesses = json.loads(image_guesses_row[0])
        for guesser_id, guessed_user_id in guesses.items():
            guess_counts_by_user[int(guesser_id)] += 1  #

    #   Get all guesses
    all_guesses = {}
    c.execute("SELECT id, guesses FROM images WHERE guesses != '{}'")
    all_guesses_data = c.fetchall()
    for image_id, guesses_str in all_guesses_data:
        all_guesses[image_id] = json.loads(guesses_str)  #

    subfolders = ['koloda1', 'koloda2']
    active_subfolder = get_setting("active_subfolder") or ''

    show_card_info = get_setting("show_card_info") == "true"

    conn.close()
    return render_template("admin.html", users=users, images=images, message=message,
                           subfolders=subfolders, active_subfolder=active_subfolder,
                           guess_counts_by_user=guess_counts_by_user, all_guesses=all_guesses,
                           show_card_info=show_card_info)  #

@app.route("/admin/delete/<int:user_id>", methods=["POST"])
def delete_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM users WHERE id = ?", (user_id,))
    c.execute("UPDATE images SET status = 'Свободно' WHERE status = ?", (f"Занято:{user_id}",))
    conn.commit()
    conn.close()
    return redirect(url_for("admin"))  #

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

    # Get the show_card_info setting and make it available globally
    show_card_info = get_setting("show_card_info")
    g.show_card_info = show_card_info == "true"

    conn.close()  #

@app.route("/user/<code>/guess/<int:image_id>", methods=["POST"])
def guess_image(code, image_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    #   Get user ID
    c.execute("SELECT id FROM users WHERE code = ?", (code,))
    user_row = c.fetchone()
    if not user_row:
        conn.close()
        return "User not found", 404
    user_id = user_row[0]

    guessed_user_id = request.form.get("guessed_user_id")
    if not guessed_user_id:
        conn.close()
        return "No user selected", 400  #

    #   Get the image's current guesses
    c.execute("SELECT guesses FROM images WHERE id = ?", (image_id,))
    image_data = c.fetchone()
    guesses = json.loads(image_data[0]) if image_data and image_data[0] else {}

    #   Add/Update the guess
    guesses[str(user_id)] = int(guessed_user_id)  #   Store user_id as string key

    #   Update the image with the guess
    c.execute("UPDATE images SET guesses = ? WHERE id = ?", (json.dumps(guesses), image_id))

    conn.commit()
    conn.close()

    return redirect(url_for('user', code=code)) #   Redirect back to user page  #

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

    #
    #  Get user's cards
    c.execute("SELECT id, subfolder, image FROM images WHERE status = ?", (f"Занято:{user_id}",))
    cards = [{"id": r[0], "subfolder": r[1], "image": r[2]} for r in c.fetchall()]

    #   Get images on the table
    c.execute("SELECT id, subfolder, image, owner_id, guesses FROM images WHERE owner_id IS NOT NULL")
    table_images_data = c.fetchall()
    table_images = []
    for img in table_images_data:
        owner_id = img[3]
        table_image = {
            "id": img[0],
            "subfolder": img[1],
            "image": img[2],
            "owner_id": owner_id,
            "guesses": json.loads(img[4]) if img[4] else {},
        }
        table_images.append(table_image)

    #   Get all users
    # for the dropdown (excluding the current user - will handle exclusion in template)
    c.execute("SELECT id, name FROM users", )  #   Fetch all users
    all_users = c.fetchall()

    #   Check if the user has a card on the table
    c.execute("SELECT 1 FROM images WHERE owner_id = ?", (user_id,))
    on_table = c.fetchone() is not None

    conn.close()

    show_card_info = get_setting("show_card_info") == "true" # Get the setting

    return render_template("user.html", name=name, rating=rating, cards=cards,
                           table_images=table_images, all_users=all_users, #   передаем всех пользователей
                           code=code, on_table=on_table, g=g, show_card_info=show_card_info)  #

@app.route("/user/<code>/place/<int:image_id>", methods=["POST"])
def place_card(code, image_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    #   Get user ID
    c.execute("SELECT id FROM users WHERE code = ?", (code,))
    user_row = c.fetchone()
    if not user_row:
        conn.close()
        return "User not found", 404
    user_id = user_row[0]

    #   Check if the user already has a card on the table
    c.execute("SELECT 1 FROM images WHERE owner_id = ?", (user_id,))
    if c.fetchone() is not None:
        conn.close()
        return "You already have a card on the table", 400  #

    #   Update the image
    c.execute("UPDATE images SET owner_id = ?, status = 'На столе' WHERE id = ?", (user_id, image_id))
    conn.commit()
    conn.close()
    return redirect(url_for('user', code=code))  #

@app.route("/open_cards", methods=["POST"])
def open_cards():
    set_setting("show_card_info", "true")

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    #   Calculate scores
    c.execute("SELECT id, owner_id, guesses FROM images WHERE owner_id IS NOT NULL")
    table_images_data = c.fetchall()
    user_guesses = {}
    for img_id, owner_id, guesses_str in table_images_data:
        guesses = json.loads(guesses_str) if guesses_str else {}
        user_guesses[img_id] = {"owner": owner_id, "guesses": guesses}

    user_points = defaultdict(int)

    for img_data in user_guesses.values():
        owner_id = img_data["owner"]
        guesses = img_data["guesses"]
        correct_guessers = [guesser_id for guesser_id, guessed_owner_id in guesses.items() if guessed_owner_id == owner_id]
        num_correct_guessers = len(correct_guessers)

        # ведущий
        if num_correct_guessers == len(user_guesses) - 1:  # All others guessed correctly
            user_points[owner_id] -= 3
        elif num_correct_guessers == 0:  # No one guessed correctly
            user_points[owner_id] -= 2
        else:
            user_points[owner_id] += 3 + num_correct_guessers
            for guesser_id in correct_guessers:
                user_points[int(guesser_id)] += 3

        # остальные игроки
        for guesser_id, guessed_owner_id in guesses.items():
            user_points[guessed_owner_id] += 1  # +1 point for each guesser

    #   Update user ratings in the database
    for user_id, points in user_points.items():
        c.execute("UPDATE users SET rating = rating + ? WHERE id = ?", (points, user_id))

    #   Determine the next leading user
    current_leading_user_id = get_leading_user_id()
    if current_leading_user_id is None:
        set_leading_user_id(1)  # First leading user - user with ID 1
    else:
        c.execute("SELECT MAX(id) FROM users")
        max_user_id = c.fetchone()[0]
        next_leading_user_id = current_leading_user_id + 1
        if next_leading_user_id > max_user_id:
            next_leading_user_id = 1  # If it's the last one, go back to the first
        set_leading_user_id(next_leading_user_id)

    conn.commit()
    conn.close()

    return redirect(url_for("admin"))

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
