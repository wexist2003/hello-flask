import json
import sqlite3
import os
import string
import random
from flask import Flask, render_template, request, redirect, url_for, g, flash

app = Flask(__name__)
DB_PATH = 'database.db'
# ВАЖНО: Установите свой секретный ключ!
app.secret_key = "super secret key replace me zdfxnffhhuty"

# --- Инициализация БД ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
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
            guesses TEXT DEFAULT '{}' -- Установим default пустой JSON
        )
    """)
    c.execute("""
        CREATE TABLE settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    # Загрузка изображений
    image_folders = ['koloda1', 'koloda2'] # Укажите ваши папки
    for folder in image_folders:
        folder_path = os.path.join('static', 'images', folder)
        if os.path.exists(folder_path):
            for filename in os.listdir(folder_path):
                if filename.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')): # Проверка расширений
                     # Добавляем со статусом Свободно и пустыми guesses
                    c.execute("INSERT INTO images (subfolder, image, status, owner_id, guesses) VALUES (?, ?, 'Свободно', NULL, '{}')", (folder, filename))
    conn.commit()
    conn.close()

# --- Вспомогательные функции ---
def generate_unique_code(length=8):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    while True:
        code = ''.join(random.choices(string.ascii_letters + string.digits, k=length))
        c.execute("SELECT id FROM users WHERE code = ?", (code,))
        if not c.fetchone():
            conn.close()
            return code

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
    # Используем REPLACE для вставки или обновления значения
    c.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def get_leading_user_id():
    value = get_setting('leading_user_id')
    return int(value) if value else None

def set_leading_user_id(user_id):
    set_setting('leading_user_id', str(user_id) if user_id is not None else None)

def get_user_name(user_id):
    if not user_id: return None
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT name FROM users WHERE id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

# --- Обработчики запросов ---
@app.before_request
def before_request():
    # Устанавливаем g.user_id на основе кода в URL или параметрах
    # (Эта функция осталась неизменной из предыдущих версий)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    code = None
    g.user = None # Инициализируем g.user
    if request.view_args and 'code' in request.view_args:
        code = request.view_args.get('code')
    elif request.args and 'code' in request.args:
        code = request.args.get('code')

    if code:
        c.execute("SELECT * FROM users WHERE code = ?", (code,))
        user_row = c.fetchone()
        if user_row:
            g.user = user_row # Сохраняем всю информацию о пользователе в g
        # Убрали else g.user = None, т.к. он уже None по умолчанию

    # Получаем настройку show_card_info
    show_card_info_setting = get_setting("show_card_info")
    g.show_card_info = show_card_info_setting == "true"

    conn.close()
    # Делаем get_user_name доступным глобально в шаблонах
    app.jinja_env.globals.update(get_user_name=get_user_name, g=g)


@app.route("/")
def index():
    # Простая главная страница
    admin_url = url_for('admin')
    return f'<h1>Hello!</h1><p><a href="{admin_url}">Перейти в админку</a></p>'

@app.route("/admin", methods=["GET", "POST"])
def admin():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # --- Обработка POST-запросов ---
    if request.method == "POST":
        should_redirect = True # Флаг для редиректа

        if "name" in request.form:
            # --- Упрощенная логика добавления пользователя (ВОЗМОЖНО, РАБОЧАЯ ВЕРСИЯ) ---
            name = request.form.get("name", "").strip()
            num_cards_str = request.form.get("num_cards", "3") # Получаем как строку

            if not name or not num_cards_str.isdigit() or int(num_cards_str) <= 0:
                 flash("Введите корректное имя и положительное число карт.", "warning")
            else:
                num_cards = int(num_cards_str)
                code = generate_unique_code()
                active_subfolder = get_setting("active_subfolder")

                if not active_subfolder:
                     flash("Сначала выберите активную колоду!", "warning")
                else:
                    try:
                        # 1. Добавляем пользователя
                        c.execute("INSERT INTO users (name, code) VALUES (?, ?)", (name, code))
                        user_id = c.lastrowid

                        # 2. Пытаемся раздать карты
                        c.execute("SELECT id FROM images WHERE subfolder = ? AND status = 'Свободно'", (active_subfolder,))
                        available_cards_ids = [row['id'] for row in c.fetchall()]

                        if len(available_cards_ids) >= num_cards:
                            random.shuffle(available_cards_ids)
                            selected_card_ids = available_cards_ids[:num_cards]
                            for card_id in selected_card_ids:
                                c.execute("UPDATE images SET status = ?, owner_id = NULL, guesses = '{}' WHERE id = ?", (f"Занято:{user_id}", card_id))
                            flash(f"Пользователь '{name}' добавлен. Карты розданы.", "success")
                        else:
                            # Пользователь добавлен, но карт не хватило
                            flash(f"Пользователь '{name}' добавлен, но не хватило свободных карт в '{active_subfolder}' ({len(available_cards_ids)} из {num_cards}).", "warning")

                        # 3. Коммитим результат
                        conn.commit()

                    except sqlite3.IntegrityError:
                        conn.rollback()
                        flash(f"Имя пользователя '{name}' уже занято.", "danger")
                    except Exception as e:
                        conn.rollback()
                        flash(f"Произошла ошибка: {e}", "danger")

        elif "active_subfolder" in request.form:
             # --- Логика выбора активной колоды (исправленная) ---
            selected = request.form.get("active_subfolder")
            set_setting("active_subfolder", selected)
            if selected:
                c.execute("UPDATE images SET status = 'Неактивна' WHERE subfolder != ? AND status NOT LIKE 'Занято:%' AND status != 'На столе'", (selected,))
                c.execute("UPDATE images SET status = 'Свободно' WHERE subfolder = ? AND (status = 'Неактивна' OR status = 'Свободно' OR status IS NULL)", (selected,))
            else:
                 c.execute("UPDATE images SET status = 'Неактивна' WHERE status NOT LIKE 'Занято:%' AND status != 'На столе'")
            conn.commit()
            flash(f"Статус колод обновлен. Активная колода: {selected if selected else 'Не выбрана'}", "info")

        else:
             should_redirect = False # Неизвестный POST, не делаем редирект

        # Редирект после POST
        if should_redirect:
             conn.close()
             return redirect(url_for('admin'))

    # --- Обработка GET-запроса ---
    # Получение данных для отображения (как в последних версиях)
    c.execute("SELECT id, name, code, rating FROM users ORDER BY id ASC")
    users = c.fetchall()
    user_names = {user['id']: user['name'] for user in users}

    c.execute("SELECT id, subfolder, image, status, owner_id FROM images ORDER BY subfolder, id")
    images_raw = c.fetchall()
    images = []
    for img in images_raw:
        display_status = img['status'] if img['status'] else 'Свободно'
        # ... (обработка статусов для display_status как раньше) ...
        if img['status']:
             if img['status'].startswith('Занято:'):
                try:
                    user_id = int(img['status'].split(':')[1])
                    owner_name = user_names.get(user_id)
                    display_status = f"Занято: {owner_name}" if owner_name else "Занято: ID?"
                except: display_status = "Занято (ошибка ID)"
             elif img['status'] == 'На столе':
                 owner_name = user_names.get(img['owner_id'])
                 display_status = f"На столе ({owner_name})" if owner_name else "На столе (ID?)"
             elif img['status'] == 'Неактивна':
                 display_status = 'Неактивна'
        images.append({ "id": img['id'], "subfolder": img['subfolder'], "image": img['image'], "display_status": display_status })


    all_guesses_processed = {}
    c.execute("SELECT id, guesses FROM images WHERE guesses IS NOT NULL AND guesses != '{}'")
    guesses_raw = c.fetchall()
    for img_guess in guesses_raw:
        image_id = img_guess['id']
        try:
            guesses_dict = json.loads(img_guess['guesses'])
            all_guesses_processed[image_id] = {int(k): int(v) for k, v in guesses_dict.items()}
        except: pass # Игнорируем ошибки декодирования

    # Статические подкаталоги (или можно сделать динамическими)
    subfolders = ['koloda1', 'koloda2']
    active_subfolder = get_setting("active_subfolder") or ''
    show_card_info = get_setting("show_card_info") == "true"

    conn.close() # Закрываем соединение перед рендерингом

    return render_template("admin.html",
                           users=users,
                           images=images,
                           subfolders=subfolders,
                           active_subfolder=active_subfolder,
                           show_card_info=show_card_info,
                           all_guesses_processed=all_guesses_processed,
                           user_names=user_names
                           )


# --- Маршрут удаления пользователя ---
@app.route("/admin/delete/<int:user_id>", methods=["POST"])
def delete_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Освобождаем карты пользователя
    c.execute("UPDATE images SET status = 'Свободно', owner_id = NULL, guesses = '{}' WHERE status = ? OR owner_id = ?", (f"Занято:{user_id}", user_id))
    # Удаляем пользователя
    c.execute("DELETE FROM users WHERE id = ?", (user_id,))
    # Проверяем, был ли удаленный пользователь ведущим
    if get_leading_user_id() == user_id:
         set_leading_user_id(None) # Сбрасываем ведущего, он переназначится в open_cards
         flash("Удаленный пользователь был Ведущим. Ведущий будет переназначен.", "warning")

    conn.commit()
    conn.close()
    flash(f"Пользователь ID {user_id} и его карты удалены/освобождены.", "success")
    return redirect(url_for("admin"))


# --- Маршрут страницы пользователя ---
@app.route("/user/<code>")
def user(code):
     # Проверяем пользователя по коду (через before_request -> g.user)
    if not g.user:
        return "<h1>Пользователь не найден</h1><p>Проверьте правильность кода в адресе.</p>", 404

    user_id = g.user['id']
    name = g.user['name']
    rating = g.user['rating']

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row # Используем Row Factory
    c = conn.cursor()

    # Получаем карты пользователя
    c.execute("SELECT id, subfolder, image FROM images WHERE status = ?", (f"Занято:{user_id}",))
    user_cards = c.fetchall() # Список объектов Row

    # Получаем карты на столе
    c.execute("SELECT id, subfolder, image, owner_id, guesses FROM images WHERE status = 'На столе'")
    table_images_raw = c.fetchall()

    # Обрабатываем карты на столе, декодируем JSON guesses
    table_images = []
    for img in table_images_raw:
        guesses_dict = {}
        try:
            if img['guesses']:
                guesses_dict = json.loads(img['guesses'])
        except json.JSONDecodeError:
            print(f"Ошибка JSON в карте {img['id']} на столе") # Логгирование
        table_images.append({
            "id": img['id'],
            "subfolder": img['subfolder'],
            "image": img['image'],
            "owner_id": img['owner_id'],
            "guesses": {int(k): int(v) for k,v in guesses_dict.items()} # Конвертируем ID в int
        })

    # Получаем всех пользователей для выпадающего списка
    c.execute("SELECT id, name FROM users")
    all_users = c.fetchall() # Список объектов Row

    # Проверяем, есть ли карта пользователя на столе
    c.execute("SELECT 1 FROM images WHERE owner_id = ? AND status = 'На столе'", (user_id,))
    on_table = c.fetchone() is not None

    conn.close()

    # g.show_card_info устанавливается в before_request
    return render_template("user.html", user=g.user, cards=user_cards,
                           table_images=table_images, all_users=all_users,
                           on_table=on_table)


# --- Маршрут для угадывания карты ---
@app.route("/user/<code>/guess/<int:image_id>", methods=["POST"])
def guess_image(code, image_id):
    if not g.user: # Проверка пользователя из before_request
        return "Пользователь не найден", 404
    user_id = g.user['id']

    guessed_user_id_str = request.form.get("guessed_user_id")
    if not guessed_user_id_str or not guessed_user_id_str.isdigit():
        flash("Не выбран пользователь для угадывания.", "warning")
        return redirect(url_for('user', code=code))

    guessed_user_id = int(guessed_user_id_str)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Получаем текущие guesses для картинки
    c.execute("SELECT guesses FROM images WHERE id = ?", (image_id,))
    result = c.fetchone()
    if not result:
         conn.close()
         flash("Картинка не найдена.", "danger")
         return redirect(url_for('user', code=code))

    try:
        guesses = json.loads(result[0]) if result[0] else {}
    except json.JSONDecodeError:
        guesses = {} # Начинаем с нуля если JSON невалидный

    # Добавляем/обновляем угадывание (ключ - ID угадывающего, значение - ID угаданного)
    guesses[str(user_id)] = guessed_user_id # Храним ключ как строку в JSON

    # Обновляем guesses в БД
    try:
        c.execute("UPDATE images SET guesses = ? WHERE id = ?", (json.dumps(guesses), image_id))
        conn.commit()
        flash("Ваше предположение принято!", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Ошибка сохранения предположения: {e}", "danger")

    conn.close()
    return redirect(url_for('user', code=code))

# --- Маршрут для выкладывания карты на стол ---
@app.route("/user/<code>/place/<int:image_id>", methods=["POST"])
def place_card(code, image_id):
    if not g.user:
        return "Пользователь не найден", 404
    user_id = g.user['id']

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Проверяем, есть ли уже карта этого пользователя на столе
    c.execute("SELECT 1 FROM images WHERE owner_id = ? AND status = 'На столе'", (user_id,))
    if c.fetchone():
        conn.close()
        flash("У вас уже есть карта на столе.", "warning")
        return redirect(url_for('user', code=code))

    # Проверяем, принадлежит ли карта пользователю и не на столе ли она уже
    c.execute("SELECT status FROM images WHERE id = ? AND status = ?", (image_id, f"Занято:{user_id}"))
    card = c.fetchone()

    if not card:
         conn.close()
         flash("Нельзя выложить эту карту.", "danger")
         return redirect(url_for('user', code=code))

    # Обновляем статус карты и указываем владельца
    try:
        c.execute("UPDATE images SET status = 'На столе', owner_id = ?, guesses = '{}' WHERE id = ?", (user_id, image_id))
        conn.commit()
        flash("Ваша карта выложена на стол.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Ошибка выкладывания карты: {e}", "danger")

    conn.close()
    return redirect(url_for('user', code=code))


# --- Маршрут для подсчета очков ---
@app.route("/open_cards", methods=["POST"])
def open_cards():
    set_setting("show_card_info", "true") # Показываем информацию о картах

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row # Используем Row Factory
    c = conn.cursor()

    leading_user_id = get_leading_user_id()

    # Если ведущий не назначен, назначаем первого по ID
    if leading_user_id is None:
        c.execute("SELECT id FROM users ORDER BY id ASC LIMIT 1")
        first_user = c.fetchone()
        if first_user:
            leading_user_id = first_user['id']
            set_leading_user_id(leading_user_id)
            flash(f"Ведущий не был назначен. Назначен первый пользователь по ID: {leading_user_id}.", "info")
        else:
            flash("Нет пользователей в игре! Невозможно подсчитать очки.", "danger")
            conn.close()
            return redirect(url_for("admin"))

    # Получаем данные для подсчета
    c.execute("SELECT id, owner_id, guesses FROM images WHERE status = 'На столе'") # Только карты на столе
    table_images = c.fetchall()
    c.execute("SELECT id FROM users")
    all_users_ids = [user['id'] for user in c.fetchall()]
    num_all_users = len(all_users_ids)
    user_points = {user_id: 0 for user_id in all_users_ids} # Очки за этот раунд

    # --- Логика подсчета очков (как была исправлена) ---
    for image in table_images:
        owner_id = image['owner_id']
        if not owner_id: continue # Пропускаем если у карты на столе нет владельца (маловероятно)

        guesses = {}
        try:
             if image['guesses']: guesses = json.loads(image['guesses'])
        except: pass # Игнорируем ошибки JSON

        # Конвертируем ключи и значения в int
        int_guesses = {int(k): int(v) for k, v in guesses.items() if k.isdigit() and isinstance(v, int)}

        correct_guesses_count = 0
        for guesser_id, guessed_user_id in int_guesses.items():
            # Проверяем, что угадавший и угаданный существуют
            if guesser_id in user_points and guessed_user_id in user_points:
                 if guessed_user_id == owner_id:
                    correct_guesses_count += 1
                    # Правило 4 (Модифицированное): +3 угадавшему, если угадал карту Ведущего
                    if owner_id == leading_user_id:
                        user_points[guesser_id] += 3
                    # Правило 5: +1 владельцу (не ведущему), если его карту угадали
                    elif owner_id != leading_user_id:
                         user_points[owner_id] += 1

        # Правило 3: Очки для Ведущего
        if owner_id == leading_user_id and owner_id in user_points:
            if num_all_users > 1 and correct_guesses_count == num_all_users - 1: user_points[owner_id] -= 3
            elif correct_guesses_count == 0: user_points[owner_id] -= 2
            elif num_all_users > 1: user_points[owner_id] += 3 + correct_guesses_count

    # Обновление рейтинга в БД
    for user_id, points in user_points.items():
        if points != 0:
            c.execute("UPDATE users SET rating = rating + ? WHERE id = ?", (points, user_id))

    # Сбрасываем карты со стола обратно в колоду (делаем их 'Свободно')
    # и сбрасываем owner_id и guesses
    active_subfolder = get_setting("active_subfolder")
    if active_subfolder:
        c.execute("UPDATE images SET status='Свободно', owner_id=NULL, guesses='{}' WHERE status='На столе' AND subfolder=?", (active_subfolder,))
        # Опционально: Сделать карты неактивных колод 'Неактивна'
        c.execute("UPDATE images SET status='Неактивна' WHERE status='На столе' AND subfolder!=?", (active_subfolder,))

    conn.commit() # Сохраняем обновление рейтинга и сброс карт

    # --- Автоматическое определение следующего ведущего (как было исправлено) ---
    current_leading_user_id = get_leading_user_id() # Берем ID текущего/только что назначенного
    c.execute("SELECT id FROM users ORDER BY id ASC")
    user_ids_ordered = [user['id'] for user in c.fetchall()]

    next_leading_user_id = None
    if user_ids_ordered:
        if current_leading_user_id in user_ids_ordered:
            try:
                current_index = user_ids_ordered.index(current_leading_user_id)
                next_index = (current_index + 1) % len(user_ids_ordered)
                next_leading_user_id = user_ids_ordered[next_index]
            except ValueError: next_leading_user_id = user_ids_ordered[0]
        else: next_leading_user_id = user_ids_ordered[0]

    if next_leading_user_id is not None:
        set_leading_user_id(next_leading_user_id)
        flash(f"Очки подсчитаны. Следующий ведущий назначен (ID: {next_leading_user_id}).", "info")
    else:
        set_leading_user_id(None) # Сбрасываем, если пользователей нет
        flash("Очки подсчитаны, но не удалось назначить ведущего (нет пользователей?).", "warning")

    conn.close()
    return redirect(url_for("admin"))

# --- Запуск приложения ---
if __name__ == "__main__":
    # Проверяем и создаем БД, если ее нет
    if not os.path.exists(DB_PATH):
        print("Создание новой базы данных...")
        init_db()
        print("База данных создана.")
    # Запуск Flask dev сервера
    port = int(os.environ.get("PORT", 5000))
    # Установите debug=False для продакшена
    app.run(host="0.0.0.0", port=port, debug=True)
