import json  # Import json for handling guesses
from flask import Flask, render_template, request, redirect, url_for, g
import sqlite3
import os
import string
import random

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
    """)

    c.execute("""
        CREATE TABLE images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subfolder TEXT NOT NULL,
            image TEXT NOT NULL,
            status TEXT,
            owner_id INTEGER,  -- New column
            guesses TEXT       -- New column
        )
    """)

    c.execute("""
        CREATE TABLE settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    #   Загрузка изображений из static/images
    image_folders = ['koloda1', 'koloda2']
    for folder in image_folders:
        folder_path = os.path.join('static', 'images', folder)
        if os.path.exists(folder_path):
            for filename in os.listdir(folder_path):
                if filename.endswith('.jpg'):
                    c.execute("INSERT INTO images (subfolder, image, status, owner_id, guesses) VALUES (?, ?, 'Свободно', NULL, '{}')", (folder, filename))  # Initialize new columns

    #   Удаляем статусы "Занято" (при новом запуске)
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

    # Get the show_card_info setting and make it available globally
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

app.jinja_env.globals.update(get_user_name=get_user_name, g=g, get_leading_user_id=get_leading_user_id) # Make the function globally available


@app.route("/")
def index():
    return "<h1>Hello, world!</h1><p><a href='/admin'>Перейти в админку</a></p>"

@app.route("/admin", methods=["GET", "POST"])
def admin():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    message = ""
    # ДОБАВЛЕНО: Получаем ID текущего ведущего
    leading_user_id = get_leading_user_id()

    if request.method == "POST":
        if "name" in request.form:
            name = request.form.get("name").strip()
            num_cards = int(request.form.get("num_cards", 3))
            code = generate_unique_code() # 

            try:
                c.execute("INSERT INTO users (name, code) VALUES (?, ?)", (name, code))
                user_id = c.lastrowid

                #   Назначаем карточки пользователю из активной колоды в случайном порядке
                active_subfolder = get_setting("active_subfolder") 
                if active_subfolder:
                    c.execute("""
                        SELECT id, subfolder, image
                        FROM images
                        WHERE subfolder = ? AND status = 'Свободно' 
                    """, (active_subfolder,))
                    available_cards = c.fetchall()

                    if len(available_cards) < num_cards:
                        message = f"Недостаточно свободных карточек в колоде {active_subfolder}." # 
                    else:
                        random.shuffle(available_cards)  #   Перемешиваем карточки
                        selected_cards = available_cards[:num_cards]  #   Выбираем нужное количество

                        for card in selected_cards: # 
                            c.execute("UPDATE images SET status = ? WHERE id = ?", (f"Занято:{user_id}", card[0]))

                    conn.commit()
                    message = f"Пользователь '{name}' добавлен."

            except sqlite3.IntegrityError: # 
                message = f"Имя '{name}' уже существует."

        elif "active_subfolder" in request.form:
            selected = request.form.get("active_subfolder")
            set_setting("active_subfolder", selected)
            #   Сделать все другие изображения занятыми
            c.execute("UPDATE images SET status = 'Занято' WHERE subfolder != ?", (selected,)) # 
            c.execute("UPDATE images SET status = 'Свободно' WHERE subfolder = ?", (selected,)) # 
            conn.commit()
            message = f"Выбран подкаталог: {selected}"

    #   Получение данных
    c.execute("SELECT id, name, code, rating FROM users ORDER BY name ASC")
    users = c.fetchall()

    c.execute("SELECT subfolder, image, status FROM images")
    images = c.fetchall()

    # Get guess counts by each user # 
    guess_counts_by_user = {}
    for user in users:
        user_id = user[0]
        guess_counts_by_user[user_id] = 0

    c.execute("SELECT guesses FROM images WHERE guesses != '{}'")
    images_with_guesses = c.fetchall()
    for image_guesses_row in images_with_guesses:
        guesses = json.loads(image_guesses_row[0])
        for guesser_id, guessed_user_id in guesses.items():
            # Ensure guesser_id is an integer and exists in the dictionary # 
            guesser_id_int = int(guesser_id)
            if guesser_id_int in guess_counts_by_user:
                guess_counts_by_user[guesser_id_int] += 1

    #   Get all guesses
    all_guesses = {}
    c.execute("SELECT id, guesses FROM images WHERE guesses != '{}'")
    all_guesses_data = c.fetchall()
    for image_id, guesses_str in all_guesses_data:
        all_guesses[image_id] = json.loads(guesses_str) # 

    subfolders = ['koloda1', 'koloda2']
    active_subfolder = get_setting("active_subfolder") or ''

    show_card_info = get_setting("show_card_info") == "true"

    conn.close()
    return render_template("admin.html", users=users, images=images, message=message,
                           subfolders=subfolders, active_subfolder=active_subfolder,
                           guess_counts_by_user=guess_counts_by_user, all_guesses=all_guesses,
                           show_card_info=show_card_info,
                           leading_user_id=leading_user_id) # <--- ДОБАВЛЕНО: Передача ID ведущего в шаблон #
    

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

    conn.close()

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
        return "No user selected", 400

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

    return redirect(url_for('user', code=code)) #   Redirect back to user page

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
                           code=code, on_table=on_table, g=g, show_card_info=show_card_info)
    

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
        return "You already have a card on the table", 400

    #   Update the image
    c.execute("UPDATE images SET owner_id = ?, status = 'На столе' WHERE id = ?", (user_id, image_id))
    conn.commit()
    conn.close()

    return redirect(url_for('user', code=code))



# ЗАМЕНИТЕ ВАШУ ТЕКУЩУЮ ФУНКЦИЮ open_cards НА ЭТУ:
@app.route("/open_cards", methods=["POST"])
def open_cards():
    set_setting("show_card_info", "true") # Показываем информацию о картах

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Получаем ID ведущего
    leading_user_id = get_leading_user_id()
    if leading_user_id is None:
         # Обработка случая, если ведущий не назначен (например, установить первого или вернуть ошибку)
         # Для примера установим первого пользователя, если ведущего нет
         c.execute("SELECT id FROM users ORDER BY id LIMIT 1")
         first_user = c.fetchone()
         if first_user:
             leading_user_id = first_user[0]
             set_leading_user_id(leading_user_id)
         else:
             # Обработка случая, если пользователей нет совсем
             conn.close()
             # Можно вернуть сообщение об ошибке или редирект
             return redirect(url_for("admin")) # Пример

    # Получаем все карточки на столе с предположениями
    c.execute("SELECT id, owner_id, guesses FROM images WHERE owner_id IS NOT NULL")
    table_images = c.fetchall()

    # Получаем ID всех пользователей
    c.execute("SELECT id FROM users")
    all_users = [user[0] for user in c.fetchall()]
    num_all_users = len(all_users) # Количество всех пользователей

    # Словарь для хранения очков пользователей (инициализируем нулями)
    user_points = {user_id: 0 for user_id in all_users}

    # --- ОСНОВНОЙ ЦИКЛ ОБРАБОТКИ КАРТ НА СТОЛЕ ---
    for image_data in table_images: # Используем другое имя переменной, чтобы не конфликтовать ниже
        image_id = image_data[0]
        owner_id = image_data[1] # Владелец ТЕКУЩЕЙ карты
        guesses = json.loads(image_data[2]) if image_data[2] else {} # Угадывания для ТЕКУЩЕЙ карты
        correct_guesses_count = 0 # Счетчик правильных угадываний для ТЕКУЩЕЙ карты

        # --- ВНУТРЕННИЙ ЦИКЛ ОБРАБОТКИ УГАДЫВАНИЙ ДЛЯ ТЕКУЩЕЙ КАРТЫ ---
        for guesser_id_str, guessed_user_id in guesses.items():
            guesser_id = int(guesser_id_str) # ID того, кто угадывал

            # Проверяем, правильно ли угадали владельца ТЕКУЩЕЙ карты
            if guessed_user_id == owner_id:
                correct_guesses_count += 1 # Увеличиваем счетчик для Правила 3

                # Применяем Правило 4 (Модифицированное):
                # Если ТЕКУЩАЯ карта принадлежит Ведущему, угадавший получает +3
                if owner_id == leading_user_id:
                    user_points[guesser_id] += 3

                # Применяем Правило 5:
                # Если ТЕКУЩАЯ карта НЕ принадлежит Ведущему, ее ВЛАДЕЛЕЦ получает +1
                elif owner_id != leading_user_id:
                    user_points[owner_id] += 1 # +1 очко ВЛАДЕЛЬЦУ карты

        # --- ПРИМЕНЯЕМ ПРАВИЛО 3 (Очки для Ведущего) ---
        # Делаем это после подсчета ВСЕХ угадываний для карты Ведущего
        if owner_id == leading_user_id:
            # Проверяем, все ли остальные угадали
            if num_all_users > 1 and correct_guesses_count == num_all_users - 1:
                user_points[owner_id] -= 3 # Штраф, если все угадали
            elif correct_guesses_count == 0: # Никто не угадал
                user_points[owner_id] -= 2 # Штраф, если никто не угадал
            else: # Кто-то угадал (но не все) или только 1 игрок
                # Проверяем, были ли вообще другие игроки, чтобы угадывать
                if num_all_users > 1:
                     user_points[owner_id] += 3 + correct_guesses_count # Бонус + очки за угадавших
                # Если игрок один, очки ему не меняются по этому правилу

    # --- ОБНОВЛЕНИЕ РЕЙТИНГА В БАЗЕ ДАННЫХ ---
    for user_id, points in user_points.items():
        if points != 0: # Обновляем только если очки изменились
            c.execute("UPDATE users SET rating = rating + ? WHERE id = ?", (points, user_id))

    conn.commit() # Сохраняем изменения в БД

    # --- ОПРЕДЕЛЯЕМ СЛЕДУЮЩЕГО ВЕДУЩЕГО ---
    # (Этот блок кода у вас был корректным, оставляем его)
    current_leading_user_id = get_leading_user_id() # Получаем текущего еще раз (мог быть установлен выше, если был None)
    if current_leading_user_id is not None: # Проверяем, что ведущий есть
        c.execute("SELECT id FROM users ORDER BY id") # Получаем ID всех юзеров по порядку
        user_ids_ordered = [user[0] for user in c.fetchall()]

        if user_ids_ordered: # Если есть пользователи
             try:
                 current_index = user_ids_ordered.index(current_leading_user_id)
                 next_index = (current_index + 1) % len(user_ids_ordered) # Циклический переход
                 next_leading_user_id = user_ids_ordered[next_index]
             except ValueError:
                 # Если текущего ведущего нет в списке (удалили?), назначаем первого
                 next_leading_user_id = user_ids_ordered[0]

             set_leading_user_id(next_leading_user_id)

    conn.close() # Закрываем соединение с БД

    return redirect(url_for("admin"))

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
