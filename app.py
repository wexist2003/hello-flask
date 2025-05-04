import json  # Import json for handling guesses
from flask import Flask, render_template, request, redirect, url_for, g, flash
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
def load_user():
    # --- Загрузка пользователя в g.user ---
    code = request.view_args.get('code') if request.view_args else None
    g.user = None # Сброс по умолчанию
    db_conn = None # Инициализация переменной соединения
    try:
        db_conn = sqlite3.connect(DB_PATH)
        db_conn.row_factory = sqlite3.Row # Используем Row Factory для доступа по именам колонок
        c = db_conn.cursor()
        if code:
            c.execute("SELECT * FROM users WHERE code = ?", (code,))
            g.user = c.fetchone() # Загружаем пользователя (или None, если не найден)

        # --- Чтение настройки show_card_info и установка g.show_card_info / g.leader_of_revealed_round ---
        g.show_card_info = False # Значение по умолчанию
        g.leader_of_revealed_round = None # Значение по умолчанию

        c.execute("SELECT value FROM settings WHERE key = 'show_card_info'")
        setting_row = c.fetchone()

        if setting_row and setting_row['value']:
             setting_value = setting_row['value']
             if setting_value.lower() != 'false':
                 try:
                     # Пытаемся получить ID ведущего из значения настройки
                     g.leader_of_revealed_round = int(setting_value)
                     g.show_card_info = True # Карты открыты, если значение - это ID
                 except (ValueError, TypeError):
                     # Если значение не 'false' и не ID, считаем карты скрытыми
                     g.show_card_info = False
    except sqlite3.Error as e:
        print(f"DATABASE ERROR in load_user: {e}")
        # В случае ошибки БД, лучше сбросить значения g
        g.user = None
        g.show_card_info = False
        g.leader_of_revealed_round = None
    finally:
        # Закрываем соединение, если оно было открыто
        if db_conn:
            db_conn.close()

# --- Убедитесь, что у вас есть функция get_user_name, доступная в шаблонах ---
# Либо через app.context_processor, либо через app.jinja_env.globals.update
def get_user_name(user_id):
    # Ваша реализация получения имени по ID (с обработкой ошибок и None)
    if user_id is None: return None
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT name FROM users WHERE id = ?", (int(user_id),))
        user_name_row = c.fetchone()
        conn.close()
        return user_name_row[0] if user_name_row else f"ID {user_id}?"
    except (ValueError, TypeError, sqlite3.Error) as e:
        print(f"Error in get_user_name for ID {user_id}: {e}")
        if conn: conn.close()
        return f"ID {user_id}?"

app.jinja_env.globals.update(get_user_name=get_user_name)
# --- Также убедитесь, что get_leading_user_id доступна или замените вызов в шаблоне ---
# def get_leading_user_id(): ...
# app.jinja_env.globals.update(get_leading_user_id=get_leading_user_id)

app.jinja_env.globals.update(get_user_name=get_user_name, g=g, get_leading_user_id=get_leading_user_id) # Make the function globally available


@app.route("/")
def index():
    return "<h1>Hello, world!</h1><p><a href='/admin'>Перейти в админку</a></p>"


@app.route("/admin", methods=["GET", "POST"])
def admin():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row # Удобно для доступа к колонкам по имени
    c = conn.cursor()
    message = "" # Используем flash для сообщений
    leader_to_display = None # ID ведущего для отображения на этой странице

    # --- Читаем начальные настройки через основной курсор ---
    try:
        # Получаем ID ТЕКУЩЕГО (следующего) ведущего из БД
        c.execute("SELECT value FROM settings WHERE key = 'leading_user_id'")
        leading_user_row = c.fetchone()
        current_actual_leader_id = int(leading_user_row['value']) if leading_user_row and leading_user_row['value'] else None

        # --- ОПРЕДЕЛЯЕМ КОГО ПОКАЗЫВАТЬ ---
        displayed_leader_id_from_url_str = request.args.get('displayed_leader_id')
        if displayed_leader_id_from_url_str:
            try:
                leader_to_display = int(displayed_leader_id_from_url_str)
            except (ValueError, TypeError):
                leader_to_display = current_actual_leader_id # Фоллбэк на текущего, если параметр некорректен
        else:
            leader_to_display = current_actual_leader_id # Показываем текущего при обычном заходе

        # Получаем остальные настройки
        c.execute("SELECT value FROM settings WHERE key = 'active_subfolder'")
        active_subfolder_row = c.fetchone()
        current_active_subfolder = active_subfolder_row['value'] if active_subfolder_row else ''

        c.execute("SELECT value FROM settings WHERE key = 'show_card_info'")
        show_card_info_row = c.fetchone()
        show_card_info = show_card_info_row['value'] == "true" if show_card_info_row else False

    except sqlite3.Error as e:
        flash(f"Ошибка чтения начальных настроек: {e}", "danger")
        conn.close()
        return render_template("admin.html", users=[], images=[], subfolders=['koloda1', 'koloda2'], active_subfolder='', guess_counts_by_user={}, all_guesses={}, show_card_info=False, leader_to_display=None)

    # --- Обработка POST-запросов ---
    if request.method == "POST":
        try:
            if "name" in request.form:
                # --- Создание пользователя ---
                name = request.form.get("name", "").strip()
                if not name:
                     flash("Имя пользователя не может быть пустым.", "warning")
                     # Прерываем обработку этого POST и переходим к отображению GET
                else:
                    num_cards = int(request.form.get("num_cards", 3))
                    code = generate_unique_code()

                    c.execute("INSERT INTO users (name, code) VALUES (?, ?)", (name, code))
                    user_id = c.lastrowid
                    flash(f"Пользователь '{name}' добавлен.", "success")

                    # --- Проверка и установка ведущего (при СОЗДАНИИ первого пользователя) ---
                    if current_actual_leader_id is None:
                        c.execute("REPLACE INTO settings (key, value) VALUES ('leading_user_id', ?)", (user_id,))
                        flash(f"Пользователь '{name}' назначен Ведущим.", "info")
                        current_actual_leader_id = user_id
                        leader_to_display = current_actual_leader_id # Обновляем и для отображения

                    # --- Назначение карточек ---
                    if current_active_subfolder:
                        c.execute("SELECT id FROM images WHERE subfolder = ? AND status = 'Свободно'", (current_active_subfolder,))
                        available_cards_rows = c.fetchall()
                        available_cards_ids = [row['id'] for row in available_cards_rows]

                        if len(available_cards_ids) < num_cards:
                            flash(f"Недостаточно свободных карточек ({len(available_cards_ids)} шт.) в колоде {current_active_subfolder} для назначения {num_cards} шт.", "warning")
                        else:
                            random.shuffle(available_cards_ids)
                            selected_cards_ids = available_cards_ids[:num_cards]
                            for card_id in selected_cards_ids:
                                c.execute("UPDATE images SET status = ? WHERE id = ?", (f"Занято:{user_id}", card_id))
                            flash(f"Пользователю '{name}' назначено {num_cards} карт.", "info")
                    else:
                         flash("Активная колода не выбрана, карточки не назначены.", "warning")

                    conn.commit() # Фиксируем создание пользователя, возможно ведущего, назначение карт
                    # После успешного POST лучше сделать редирект на GET, чтобы избежать повторной отправки формы
                    return redirect(url_for('admin'))


            elif "active_subfolder" in request.form:
                # --- Смена активной колоды ---
                selected = request.form.get("active_subfolder")
                c.execute("REPLACE INTO settings (key, value) VALUES ('active_subfolder', ?)", (selected,))
                c.execute("UPDATE images SET status = 'Занято' WHERE subfolder != ?", (selected,))
                c.execute("UPDATE images SET status = 'Свободно' WHERE subfolder = ? AND owner_id IS NULL AND status != 'На столе'", (selected,)) # Не трогаем карты на столе или уже назначенные из этой колоды
                conn.commit()
                flash(f"Выбрана активная колода: {selected}", "success")
                current_active_subfolder = selected # Обновляем локальную переменную
                return redirect(url_for('admin')) # Редирект после смены колоды

        except sqlite3.IntegrityError:
             flash(f"Имя пользователя '{name}' уже существует.", "danger")
             conn.rollback()
        except sqlite3.OperationalError as e:
             flash(f"Ошибка базы данных: {e}", "danger")
             conn.rollback()
        except Exception as e:
             flash(f"Произошла непредвиденная ошибка: {e}", "danger")
             conn.rollback()

    # --- Получение данных для отображения (для GET или после неудачного POST без редиректа) ---
    try:
        c.execute("SELECT id, name, code, rating FROM users ORDER BY name ASC")
        users = c.fetchall() # Теперь это список объектов Row

        # Запрашиваем все данные из images
        c.execute("SELECT id, subfolder, image, status, owner_id, guesses FROM images ORDER BY subfolder, id")
        images_rows = c.fetchall()
        images = [] # Список словарей для шаблона
        all_guesses = {} # Словарь всех предположений {image_id: {guesser_id_str: guessed_owner_id}}
        for img_row in images_rows:
            guesses_dict = json.loads(img_row['guesses']) if img_row['guesses'] and img_row['guesses'] != '{}' else {}
            img_dict = {
                "id": img_row['id'],
                "subfolder": img_row['subfolder'],
                "image": img_row['image'],
                "status": img_row['status'],
                "owner_id": img_row['owner_id'],
                "guesses": guesses_dict
            }
            images.append(img_dict)
            if guesses_dict:
                 all_guesses[img_row['id']] = guesses_dict

        # Считаем количество предположений КАЖДОГО пользователя
        guess_counts_by_user = {user['id']: 0 for user in users}
        for img_id, guesses_for_image in all_guesses.items():
            for guesser_id_str in guesses_for_image:
                 try:
                     guesser_id_int = int(guesser_id_str)
                     if guesser_id_int in guess_counts_by_user:
                          guess_counts_by_user[guesser_id_int] += 1
                 except (ValueError, TypeError):
                     pass # Игнорируем невалидные ID угадывающих

        subfolders = ['koloda1', 'koloda2'] # Можно получать динамически, если нужно

    except sqlite3.Error as e:
        flash(f"Ошибка чтения данных для отображения: {e}", "danger")
        users, images, guess_counts_by_user, all_guesses = [], [], {}, {}

    finally:
        # Закрываем соединение в любом случае (кроме случая раннего выхода из-за ошибки чтения настроек)
        if conn:
            conn.close()

    # Отображаем шаблон с подготовленными данными
    return render_template("admin.html", users=users, images=images, # message больше не передаем, используем flash
                           subfolders=subfolders, active_subfolder=current_active_subfolder,
                           guess_counts_by_user=guess_counts_by_user, all_guesses=all_guesses,
                           show_card_info=show_card_info,
                           leader_to_display=leader_to_display) # Передаем ID для отображения значка
    

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

@app.route("/user/<code>", methods=["GET", "POST"]) # Добавим методы, т.к. есть формы
def user(code):
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Используем g.user, как договорились
    if not g.user or g.user['code'] != code: # Проверка, что g.user тот, кто нужен
        # Если нет, или код не совпадает, возможно, стоит перезагрузить?
        # Но пока оставим так, предполагая, что load_user отработал для нужного кода
         return "Ошибка: Пользователь не загружен или код не совпадает.", 403

    # --- Определяем статус показа карт и ID ведущего завершенного раунда ---
    cards_are_revealed = False
    leader_of_revealed_round = None
    c.execute("SELECT value FROM settings WHERE key = 'show_card_info'")
    show_card_info_setting = c.fetchone()
    if show_card_info_setting and show_card_info_setting['value']:
        setting_value = show_card_info_setting['value']
        if setting_value.lower() != 'false': # Если не 'false'
             try:
                 # Пытаемся преобразовать значение в ID ведущего
                 leader_of_revealed_round = int(setting_value)
                 cards_are_revealed = True # Считаем, что карты открыты
             except (ValueError, TypeError):
                 # Не удалось преобразовать в число, считаем, что карты скрыты
                 cards_are_revealed = False


    # --- Получаем остальные данные ---
    # Карты пользователя
    c.execute("SELECT id, subfolder, image, status FROM images WHERE status = ?", (f"Занято:{g.user['id']}",))
    user_cards_rows = c.fetchall()
    user_cards = [dict(row) for row in user_cards_rows] # Преобразуем в словари

    # Карты на столе
    c.execute("SELECT id, subfolder, image, status, owner_id, guesses FROM images WHERE status = 'На столе'")
    table_images_rows = c.fetchall()
    table_images = [] # Список словарей
    for img_row in table_images_rows:
         guesses_dict = json.loads(img_row['guesses']) if img_row['guesses'] and img_row['guesses'] != '{}' else {}
         img_dict = dict(img_row) # Копируем Row в dict
         img_dict['guesses'] = guesses_dict
         table_images.append(img_dict)

    # Все пользователи для списка угадывания
    c.execute("SELECT id, name FROM users")
    all_users = c.fetchall() # Список Row

    # ID текущего (следующего) ведущего - может понадобиться где-то еще
    c.execute("SELECT value FROM settings WHERE key = 'leading_user_id'")
    leading_user_row = c.fetchone()
    current_leading_user_id = int(leading_user_row['value']) if leading_user_row and leading_user_row['value'] else None

    conn.close()

    # --- Передаем переменные в шаблон ---
    return render_template("user.html",
                           code=code,                 # Код текущего пользователя
                           # user=g.user,           # Передаем g.user как user
                           # Теперь используем g.user НАПРЯМУЮ в шаблоне, как было в оригинале user.html
                           user_cards=user_cards,     # Карты пользователя
                           table_images=table_images,   # Карты на столе
                           all_users=all_users,         # Все пользователи
                           cards_are_revealed=cards_are_revealed, # Флаг показа карт (True/False)
                           leader_of_revealed_round=leader_of_revealed_round # ID ведущего для показа метки (или None)
                           # current_leading_user_id=current_leading_user_id # Если нужен ID следующего ведущего
                           )
    

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
    conn = None # Инициализация
    leader_just_finished = None # Инициализация
    try:
        # Получаем ID ведущего, который ЗАВЕРШИЛ раунд
        # Используем вашу функцию или прямой запрос
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        c.execute("SELECT value FROM settings WHERE key = 'leading_user_id'")
        leader_row = c.fetchone()
        leader_just_finished = int(leader_row['value']) if leader_row and leader_row['value'] else None

        # --- Если ведущего не было, устанавливаем первого (пример) ---
        if leader_just_finished is None:
            c.execute("SELECT id FROM users ORDER BY id LIMIT 1")
            first_user = c.fetchone()
            if first_user:
                leader_just_finished = first_user['id']
                # Устанавливаем его как текущего ведущего
                c.execute("REPLACE INTO settings (key, value) VALUES ('leading_user_id', ?)", (leader_just_finished,))
                conn.commit() # Фиксируем установку первого ведущего
            else:
                 flash("Нет пользователей для игры.", "warning")
                 if conn: conn.close()
                 return redirect(url_for('admin')) # Выход, если нет юзеров

        # --- Подсчет очков (ваш код, использующий leader_just_finished) ---
        c.execute("SELECT id, owner_id, guesses FROM images WHERE owner_id IS NOT NULL")
        table_images_rows = c.fetchall()
        c.execute("SELECT id FROM users")
        all_user_rows = c.fetchall()
        all_users_ids = [row['id'] for row in all_user_rows]
        num_all_users = len(all_users_ids)
        user_points = {user_id: 0 for user_id in all_users_ids}

        for image_data in table_images_rows:
            owner_id = image_data['owner_id']
            guesses = json.loads(image_data['guesses']) if image_data['guesses'] else {}
            correct_guesses_count = 0

            for guesser_id_str, guessed_user_id in guesses.items():
                guesser_id = int(guesser_id_str)
                if guessed_user_id == owner_id:
                    correct_guesses_count += 1
                    if owner_id == leader_just_finished: # Правило 4
                         if guesser_id in user_points: user_points[guesser_id] += 3
                    elif owner_id != leader_just_finished: # Правило 5
                        if owner_id in user_points: user_points[owner_id] += 1
                        if guesser_id in user_points: user_points[guesser_id] += 1 # Добавлено правило 5 для угадавшего


            if owner_id == leader_just_finished: # Правило 3
                # Если есть хотя бы 2 игрока
                if num_all_users > 1:
                    # Если ВСЕ угадали правильно (кроме самого себя)
                    if correct_guesses_count == num_all_users - 1:
                         if owner_id in user_points: user_points[owner_id] += 0 # или -= penalty, если надо
                    # Если НИКТО не угадал правильно
                    elif correct_guesses_count == 0:
                         if owner_id in user_points: user_points[owner_id] += 0 # или -= penalty
                    else: # Если угадали некоторые, но не все
                         if owner_id in user_points: user_points[owner_id] += correct_guesses_count # Очки за каждого угадавшего
                # Если игрок всего один, очков он не получает как ведущий? Уточнить логику.


        # --- Обновление рейтинга ---
        for user_id, points in user_points.items():
            if points != 0:
                c.execute("UPDATE users SET rating = rating + ? WHERE id = ?", (points, user_id))
        flash("Очки подсчитаны и рейтинг обновлен.", "info")
        conn.commit() # Сохраняем ИЗМЕНЕНИЯ РЕЙТИНГА

        # --- УСТАНОВКА ФЛАГА ПОКАЗА ИНФО С ID ВЕДУЩЕГО ---
        if leader_just_finished is not None:
            c.execute("REPLACE INTO settings (key, value) VALUES ('show_card_info', ?)", (str(leader_just_finished),))
        else:
            c.execute("REPLACE INTO settings (key, value) VALUES ('show_card_info', 'false')")
        conn.commit() # Сохраняем настройку показа

        # --- ОПРЕДЕЛЯЕМ И СОХРАНЯЕМ СЛЕДУЮЩЕГО ВЕДУЩЕГО ---
        next_leading_user_id = None
        if leader_just_finished is not None and all_users_ids:
             try:
                 current_index = all_users_ids.index(leader_just_finished)
                 next_index = (current_index + 1) % len(all_users_ids)
                 next_leading_user_id = all_users_ids[next_index]
             except ValueError:
                 # Если текущего нет в списке, назначаем первого
                 next_leading_user_id = all_users_ids[0]

        if next_leading_user_id is not None:
              # Сохраняем ID следующего ведущего
              c.execute("REPLACE INTO settings (key, value) VALUES ('leading_user_id', ?)", (next_leading_user_id,))
              conn.commit() # Сохраняем нового ведущего
              # flash(f"Следующий ведущий: {get_user_name(next_leading_user_id)}", "info") # Можно добавить сообщение
        else:
             flash("Не удалось определить следующего ведущего.", "warning")

    except sqlite3.Error as e:
        flash(f"Ошибка базы данных при открытии карт: {e}", "danger")
        if conn: conn.rollback() # Откат изменений при ошибке
    except Exception as e:
        flash(f"Непредвиденная ошибка при открытии карт: {e}", "danger")
        if conn: conn.rollback()
    finally:
        if conn:
            conn.close()

    # --- Перенаправление на админку с ID ЗАВЕРШИВШЕГО ведущего ---
    return redirect(url_for("admin", displayed_leader_id=leader_just_finished))

@app.route("/new_round", methods=["POST"])
@app.route("/new_round", methods=["POST"])
def new_round():
    """Обрабатывает начало нового раунда: сброс стола/догадок, раздача карт, скрытие инфо."""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        # conn.row_factory = sqlite3.Row # Убрал, т.к. оригинальный шаблон user.html может ждать кортежи/списки
        c = conn.cursor()

        # === Шаг 1: Определение ведущего НЕ ТРЕБУЕТСЯ ===

        # === Шаг 2: Сбрасываем все предыдущие предположения ===
        c.execute("UPDATE images SET guesses = '{}' WHERE guesses IS NOT NULL AND guesses != '{}'")
        guesses_cleared_count = c.rowcount

        # === Шаг 3: Карты со стола получают статус "Занято" ===
        c.execute("UPDATE images SET status = 'Занято', owner_id = NULL WHERE status = 'На столе'")
        table_cleared_count = c.rowcount

        # === Шаг 4: Скрываем информацию о картах (сбрасываем флаг) ===
        c.execute("REPLACE INTO settings (key, value) VALUES ('show_card_info', 'false')")

        # Выводим сообщение о начале раунда и результатах очистки
        flash("Новый раунд начат.", "info")
        if guesses_cleared_count > 0:
             flash(f"Сброшены предыдущие предположения ({guesses_cleared_count} карт).", "info")
        if table_cleared_count > 0:
            flash(f"Карты со стола ({table_cleared_count} шт.) получили статус 'Занято'.", "info")
        # flash("Информация о картах скрыта, можно делать новые предположения.", "info") # Это сообщение можно убрать, т.к. оно следует из "Новый раунд начат"


        # === Шаг 5: Раздаем по одной новой карте каждому пользователю ===
        c.execute("SELECT id FROM users ORDER BY id")
        user_rows = c.fetchall()
        # Преобразуем результат в список ID
        user_ids_ordered = [row[0] for row in user_rows]


        if not user_ids_ordered:
            flash("Нет пользователей для раздачи карт.", "warning")
        else:
            # Получаем активную колоду
            c.execute("SELECT value FROM settings WHERE key = 'active_subfolder'")
            subfolder_row = c.fetchone()
            active_subfolder = subfolder_row[0] if subfolder_row else None

            if not active_subfolder:
                flash("Активная колода не установлена. Новые карты не розданы.", "warning")
            else:
                # Получаем список ID свободных карт из активной колоды
                c.execute("SELECT id FROM images WHERE subfolder = ? AND status = 'Свободно'", (active_subfolder,))
                available_cards_rows = c.fetchall()
                available_cards_ids = [row[0] for row in available_cards_rows]
                random.shuffle(available_cards_ids) # Перемешиваем

                num_users = len(user_ids_ordered)
                num_available = len(available_cards_ids)

                if num_available < num_users:
                    flash(f"Внимание: Недостаточно свободных карт ({num_available}) в колоде '{active_subfolder}' для раздачи всем ({num_users}). Карты получат не все.", "warning")

                num_dealt = 0
                for i, user_id in enumerate(user_ids_ordered):
                    if i < num_available:
                        card_id_to_deal = available_cards_ids[i]
                        c.execute("UPDATE images SET status = ? WHERE id = ?", (f"Занято:{user_id}", card_id_to_deal))
                        num_dealt += 1
                    else:
                        break # Карты закончились

                if num_dealt > 0:
                    flash(f"Роздано {num_dealt} новых карт.", "info")
                elif num_users > 0:
                    flash(f"Свободных карт в колоде '{active_subfolder}' нет. Новые карты не розданы.", "warning")

        # === Фиксируем все изменения ===
        conn.commit()

    except sqlite3.Error as e:
        if conn: conn.rollback()
        flash(f"Ошибка базы данных при начале нового раунда: {e}", "danger")
    except Exception as e:
        if conn: conn.rollback()
        flash(f"Непредвиденная ошибка при начале нового раунда: {e}", "danger")
    finally:
        if conn:
            conn.close()

    return redirect(url_for('admin'))

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
