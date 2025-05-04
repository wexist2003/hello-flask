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

    return render_template("user.html",
                           code=code,
                           user=user_data,          # <--- Ключевой момент
                           user_cards=user_cards,
                           table_images=table_images,
                           all_users=all_users,
                           show_card_info=show_card_info,
                           leading_user_id=leading_user_id
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
    set_setting("show_card_info", "true") # Показываем информацию о картах

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Получаем ID ведущего, который ЗАВЕРШИЛ раунд
    leader_just_finished = get_leading_user_id() # Запоминаем ID для отображения

    # --- Проверка и установка ведущего, если его не было (оставляем как есть) ---
    if leader_just_finished is None:
         c.execute("SELECT id FROM users ORDER BY id LIMIT 1")
         first_user = c.fetchone()
         if first_user:
             leader_just_finished = first_user[0] # Используем первого как "завершившего"
             set_leading_user_id(leader_just_finished) # И устанавливаем его текущим
         else:
             conn.close()
             return redirect(url_for("admin")) # Нет пользователей - нечего делать

    # --- Подсчет очков (основной код остается без изменений) ---
    c.execute("SELECT id, owner_id, guesses FROM images WHERE owner_id IS NOT NULL")
    table_images = c.fetchall()
    c.execute("SELECT id FROM users")
    all_users = [user[0] for user in c.fetchall()]
    num_all_users = len(all_users)
    user_points = {user_id: 0 for user_id in all_users}

    for image_data in table_images:
        image_id = image_data[0]
        owner_id = image_data[1]
        guesses = json.loads(image_data[2]) if image_data[2] else {}
        correct_guesses_count = 0

        for guesser_id_str, guessed_user_id in guesses.items():
            guesser_id = int(guesser_id_str)
            # Используем leader_just_finished для правил подсчета
            if guessed_user_id == owner_id:
                correct_guesses_count += 1
                if owner_id == leader_just_finished: # Правило 4
                    user_points[guesser_id] += 3
                elif owner_id != leader_just_finished: # Правило 5
                    user_points[owner_id] += 1

        if owner_id == leader_just_finished: # Правило 3
            if num_all_users > 1 and correct_guesses_count == num_all_users - 1:
                user_points[owner_id] -= 3
            elif correct_guesses_count == 0:
                user_points[owner_id] -= 2
            else:
                if num_all_users > 1:
                     user_points[owner_id] += 3 + correct_guesses_count

    # --- Обновление рейтинга ---
    for user_id, points in user_points.items():
        if points != 0:
            c.execute("UPDATE users SET rating = rating + ? WHERE id = ?", (points, user_id))

    conn.commit() # Сохраняем ИЗМЕНЕНИЯ РЕЙТИНГА

    # --- ОПРЕДЕЛЯЕМ И СОХРАНЯЕМ СЛЕДУЮЩЕГО ВЕДУЩЕГО ---
    # Используем leader_just_finished как отправную точку
    next_leading_user_id = None # Инициализация
    if leader_just_finished is not None:
        c.execute("SELECT id FROM users ORDER BY id")
        user_ids_ordered = [user[0] for user in c.fetchall()]

        if user_ids_ordered:
             try:
                 current_index = user_ids_ordered.index(leader_just_finished)
                 next_index = (current_index + 1) % len(user_ids_ordered)
                 next_leading_user_id = user_ids_ordered[next_index]
             except ValueError:
                 # Если текущего ведущего нет в списке, назначаем первого
                 next_leading_user_id = user_ids_ordered[0] if user_ids_ordered else None

             if next_leading_user_id is not None:
                  # СОХРАНЯЕМ ID СЛЕДУЮЩЕГО ВЕДУЩЕГО В БАЗУ ДАННЫХ
                  set_leading_user_id(next_leading_user_id)
                  # Важно: Мы сохранили ID следующего ведущего, но будем перенаправлять
                  # с ID того, кто только что закончил раунд.

    conn.close()

    # --- ПЕРЕНАПРАВЛЕНИЕ С ID ЗАВЕРШИВШЕГО ВЕДУЩЕГО ---
    # Передаем ID ведущего, чей раунд только что закончился, как параметр
    return redirect(url_for("admin", displayed_leader_id=leader_just_finished))

@app.route("/new_round", methods=["POST"])
def new_round():
    """Обрабатывает начало нового раунда: сброс стола/догадок, раздача карт, скрытие инфо."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row # Используем Row factory
    c = conn.cursor()

    try:
        # === Шаг 1: Определение ведущего НЕ ТРЕБУЕТСЯ ===

        # === Шаг 2: Сбрасываем все предыдущие предположения ===
        c.execute("UPDATE images SET guesses = '{}' WHERE guesses IS NOT NULL AND guesses != '{}'")
        guesses_cleared_count = c.rowcount # Запоминаем количество

        # === Шаг 3: Карты со стола получают статус "Занято" ===
        c.execute("UPDATE images SET status = 'Занято', owner_id = NULL WHERE status = 'На столе'")
        table_cleared_count = c.rowcount # Запоминаем количество

        # === Шаг 4: Скрываем информацию о картах (сбрасываем флаг) ===
        # Это позволит пользователям снова угадывать и скроет владельцев карт на столе
        c.execute("REPLACE INTO settings (key, value) VALUES ('show_card_info', 'false')")
        # или можно использовать set_setting("show_card_info", "false"), если она работает с текущим соединением conn

        # Выводим сообщение о начале раунда и результатах очистки
        flash("Новый раунд начат.", "info")
        if guesses_cleared_count > 0:
             flash(f"Сброшены предыдущие предположения ({guesses_cleared_count} карт).", "info")
        if table_cleared_count > 0:
            flash(f"Карты со стола ({table_cleared_count} шт.) получили статус 'Занято'.", "info")
        flash("Информация о картах скрыта, можно делать новые предположения.", "info") # Новое сообщение


        # === Шаг 5: Раздаем по одной новой карте каждому пользователю ===
        c.execute("SELECT id FROM users ORDER BY id") # Получаем актуальный список user ID
        user_rows = c.fetchall()
        user_ids_ordered = [row['id'] for row in user_rows]

        if not user_ids_ordered:
            flash("Нет пользователей для раздачи карт.", "warning")
        else:
            # Получаем активную колоду (используя прямой запрос вместо get_setting)
            c.execute("SELECT value FROM settings WHERE key = 'active_subfolder'")
            subfolder_row = c.fetchone()
            active_subfolder = subfolder_row['value'] if subfolder_row else None

            if not active_subfolder:
                flash("Активная колода не установлена. Новые карты не розданы.", "warning")
            else:
                # Получаем список ID свободных карт из активной колоды
                c.execute("SELECT id FROM images WHERE subfolder = ? AND status = 'Свободно'", (active_subfolder,))
                available_cards_rows = c.fetchall()
                available_cards_ids = [row['id'] for row in available_cards_rows]
                random.shuffle(available_cards_ids) # Перемешиваем свободные карты

                num_users = len(user_ids_ordered)
                num_available = len(available_cards_ids)

                if num_available < num_users:
                    flash(f"Внимание: Недостаточно свободных карт ({num_available}) в колоде '{active_subfolder}' для раздачи всем пользователям ({num_users}). Карты получат не все.", "warning")

                num_dealt = 0
                # Раздаем по одной карте каждому пользователю, пока есть карты
                for i, user_id in enumerate(user_ids_ordered):
                    if i < num_available:
                        card_id_to_deal = available_cards_ids[i]
                        # Назначаем карту пользователю
                        c.execute("UPDATE images SET status = ? WHERE id = ?", (f"Занято:{user_id}", card_id_to_deal))
                        num_dealt += 1
                    else:
                        break # Карты закончились

                if num_dealt > 0:
                    flash(f"Роздано {num_dealt} новых карт.", "info")
                elif num_users > 0: # Если есть юзеры, но ничего не роздано
                    flash(f"Свободных карт в колоде '{active_subfolder}' нет. Новые карты не розданы.", "warning")

        # === Фиксируем все изменения ===
        conn.commit()

    except sqlite3.Error as e:
        conn.rollback() # Откатываем изменения в случае ошибки БД
        flash(f"Ошибка базы данных при начале нового раунда: {e}", "danger")
    except Exception as e:
        conn.rollback() # Откатываем изменения в случае другой ошибки
        flash(f"Непредвиденная ошибка при начале нового раунда: {e}", "danger")
    finally:
        # Закрываем соединение в любом случае
        if conn:
            conn.close()

    # Перенаправляем обратно на страницу администратора
    return redirect(url_for('admin'))

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
