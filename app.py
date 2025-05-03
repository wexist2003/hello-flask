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
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

# --- Обработка POST-запросов ---
    if request.method == "POST":
        # Флаг, чтобы знать, нужно ли делать редирект в конце
        should_redirect = True

        if "name" in request.form:
            # --- Логика добавления пользователя ---
            name = request.form.get("name", "").strip() # Добавим default и strip
            num_cards_str = request.form.get("num_cards", "0")

            # Проверка входных данных
            if not name:
                flash("Имя пользователя не может быть пустым.", "warning")
            elif not num_cards_str.isdigit() or int(num_cards_str) <= 0:
                flash("Количество карт должно быть положительным числом.", "warning")
            else:
                num_cards = int(num_cards_str)
                code = generate_unique_code()
                active_subfolder = get_setting("active_subfolder")

                # --- Предварительные проверки ---
                if not active_subfolder:
                     flash("Сначала выберите активную колоду!", "warning")
                else:
                    # Проверяем наличие достаточного количества карт
                    # Используем новый курсор для этой проверки, чтобы не мешать возможной транзакции
                    check_conn = sqlite3.connect(DB_PATH)
                    check_conn.row_factory = sqlite3.Row
                    check_c = check_conn.cursor()
                    check_c.execute("SELECT id FROM images WHERE subfolder = ? AND status = 'Свободно'", (active_subfolder,))
                    available_cards_ids = [row['id'] for row in check_c.fetchall()]
                    check_conn.close() # Закрываем соединение для проверки

                    if len(available_cards_ids) < num_cards:
                        flash(f"Недостаточно свободных карт ({len(available_cards_ids)}) в '{active_subfolder}' для раздачи {num_cards}.", "warning")
                    else:
                        # --- Если все проверки пройдены, пытаемся добавить пользователя и раздать карты ---
                        try:
                            # Начинаем транзакцию с основным соединением 'conn'
                            c.execute("INSERT INTO users (name, code) VALUES (?, ?)", (name, code))
                            user_id = c.lastrowid # Получаем ID добавленного пользователя

                            # Раздаем карты
                            random.shuffle(available_cards_ids)
                            selected_card_ids = available_cards_ids[:num_cards]
                            for card_id in selected_card_ids:
                                c.execute("UPDATE images SET status = ?, owner_id = NULL, guesses = '{}' WHERE id = ?", (f"Занято:{user_id}", card_id))

                            # Коммитим ВСЕ изменения (пользователь + карты) только если все успешно
                            conn.commit()
                            flash(f"Пользователь '{name}' добавлен. Карты розданы.", "success")

                        except sqlite3.IntegrityError:
                            conn.rollback() # Откатываем транзакцию
                            flash(f"Имя пользователя '{name}' уже занято.", "danger")
                        except Exception as e:
                            conn.rollback() # Откатываем транзакцию
                            flash(f"Произошла ошибка при добавлении/раздаче карт: {e}", "danger")

        elif "active_subfolder" in request.form:
             # --- Логика выбора активной колоды (как была исправлена) ---
            selected = request.form.get("active_subfolder")
            set_setting("active_subfolder", selected)

            if selected:
                c.execute("UPDATE images SET status = 'Неактивна' WHERE subfolder != ? AND status NOT LIKE 'Занято:%' AND status != 'На столе'", (selected,))
                c.execute("UPDATE images SET status = 'Свободно' WHERE subfolder = ? AND (status = 'Неактивна' OR status = 'Свободно' OR status IS NULL)", (selected,))
            else:
                 c.execute("UPDATE images SET status = 'Неактивна' WHERE status NOT LIKE 'Занято:%' AND status != 'На столе'")

            conn.commit() # Коммитим изменения статусов
            flash(f"Статус колод обновлен. Активная колода: {selected if selected else 'Не выбрана'}", "info")

        else:
            # Если пришел POST-запрос, но не опознан (не 'name' и не 'active_subfolder')
            should_redirect = False # Не делаем редирект, возможно это GET-запрос

        # --->>> Выполняем редирект ПОСЛЕ обработки ЛЮБОГО POST запроса <<<---
        # Это важно для паттерна Post/Redirect/Get, чтобы избежать повторной отправки формы
        if should_redirect:
             # Закрываем соединение перед редиректом, т.к. оно больше не нужно для этого запроса
             conn.close()
             return redirect(url_for('admin')) # Перенаправляем на GET /admin

    # --- Если метод GET ---
    # --- Получение данных для отображения ---
    # (Этот блок остается как в предыдущем ответе: получение users, images, user_names и т.д.)
    # ... (весь код получения users, images, user_names, subfolders, active_subfolder, show_card_info, all_guesses_processed) ...

    # Получаем ID и имена пользователей для отображения статуса карт
    c.execute("SELECT id, name FROM users ORDER BY id ASC")
    users = c.fetchall() # users теперь содержит объекты Row
    user_names = {user['id']: user['name'] for user in users}

    # Изображения (обработка статусов как раньше)
    c.execute("SELECT id, subfolder, image, status, owner_id FROM images ORDER BY subfolder, id")
    images_raw = c.fetchall()
    images = []
    for img in images_raw:
        display_status = img['status'] if img['status'] else 'Свободно'
        owner_name = None
        if img['status']:
             if img['status'].startswith('Занято:'):
                try:
                    user_id = int(img['status'].split(':')[1])
                    owner_name = user_names.get(user_id)
                    display_status = f"Занято: {owner_name}" if owner_name else "Занято: ID?"
                except (ValueError, IndexError):
                    display_status = "Занято (ошибка ID)"
             elif img['status'] == 'На столе':
                 owner_name = user_names.get(img['owner_id'])
                 display_status = f"На столе ({owner_name})" if owner_name else "На столе (ID?)"
             elif img['status'] == 'Неактивна':
                 display_status = 'Неактивна'
        images.append({ "id": img['id'], "subfolder": img['subfolder'], "image": img['image'], "display_status": display_status })

    # Предположения
    all_guesses_processed = {}
    c.execute("SELECT id, guesses FROM images WHERE guesses IS NOT NULL AND guesses != '{}'")
    guesses_raw = c.fetchall()
    for img_guess in guesses_raw:
        image_id = img_guess['id']
        try:
            guesses_dict = json.loads(img_guess['guesses'])
            all_guesses_processed[image_id] = {int(k): int(v) for k, v in guesses_dict.items()}
        except json.JSONDecodeError:
            print(f"Ошибка декодирования JSON для guesses в image_id: {image_id}")

    # Подкаталоги и другие настройки
    subfolders = ['koloda1', 'koloda2']
    active_subfolder = get_setting("active_subfolder") or ''
    show_card_info = get_setting("show_card_info") == "true"


    conn.close() # Закрываем соединение ПЕРЕД рендерингом шаблона

    return render_template("admin.html",
                           users=users,
                           images=images,
                           subfolders=subfolders,
                           active_subfolder=active_subfolder,
                           show_card_info=show_card_info,
                           get_user_name=get_user_name,
                           all_guesses_processed=all_guesses_processed,
                           user_names=user_names
                           )
    

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

import json
from flask import Flask, render_template, request, redirect, url_for, g
import sqlite3
import os
# ... (предполагается, что остальные импорты и настройки Flask/DB на месте)
# ... (функции get_setting, set_setting, get_leading_user_id тоже существуют)

# ЗАМЕНИТЕ ВАШУ ТЕКУЩУЮ ФУНКЦИЮ open_cards НА ЭТУ:
@app.route("/open_cards", methods=["POST"])
def open_cards():
    set_setting("show_card_info", "true")

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    leading_user_id = get_leading_user_id()

    # --->>> МОДИФИКАЦИЯ: Если ведущий не назначен, назначаем первого по ID <<<---
    if leading_user_id is None:
        c.execute("SELECT id FROM users ORDER BY id ASC LIMIT 1")
        first_user = c.fetchone()
        if first_user:
            leading_user_id = first_user[0]
            set_leading_user_id(leading_user_id) # Назначаем ведущим
            flash(f"Ведущий не был назначен. Назначен первый пользователь по ID: {leading_user_id}.", "info")
        else:
            # Если пользователей нет
            flash("Нет пользователей в игре! Невозможно подсчитать очки.", "danger")
            conn.close()
            return redirect(url_for("admin"))

    # --->>> Логика подсчета очков (как была исправлена ранее) <<<---
    c.execute("SELECT id, owner_id, guesses FROM images WHERE owner_id IS NOT NULL")
    table_images = c.fetchall()
    c.execute("SELECT id FROM users")
    all_users_ids = [user[0] for user in c.fetchall()]
    num_all_users = len(all_users_ids)
    user_points = {user_id: 0 for user_id in all_users_ids}

    for image_data in table_images:
        image_id = image_data[0]
        owner_id = image_data[1]
        guesses = json.loads(image_data[2]) if image_data[2] else {}
        correct_guesses_count = 0

        for guesser_id_str, guessed_user_id in guesses.items():
            guesser_id = int(guesser_id_str)
            if guessed_user_id == owner_id:
                correct_guesses_count += 1
                if owner_id == leading_user_id:
                    user_points[guesser_id] += 3
                elif owner_id != leading_user_id:
                     # Проверяем, что владелец существует, прежде чем начислять
                     if owner_id in user_points:
                          user_points[owner_id] += 1

        # Очки для Ведущего (Правило 3)
        if owner_id == leading_user_id:
             # Проверяем, что ведущий есть в словаре (на случай удаления)
             if owner_id in user_points:
                  if num_all_users > 1 and correct_guesses_count == num_all_users - 1:
                      user_points[owner_id] -= 3
                  elif correct_guesses_count == 0:
                      user_points[owner_id] -= 2
                  else:
                      if num_all_users > 1:
                           user_points[owner_id] += 3 + correct_guesses_count

    # --->>> Обновление рейтинга <<<---
    for user_id, points in user_points.items():
        if points != 0:
            c.execute("UPDATE users SET rating = rating + ? WHERE id = ?", (points, user_id))

    conn.commit() # Сохраняем очки

    # --->>> ВОССТАНОВЛЕНО И МОДИФИЦИРОВАНО: Автоматическое определение следующего ведущего <<<---
    # Получаем текущего ведущего (он мог быть только что установлен, если был None)
    current_leading_user_id = get_leading_user_id()

    # Получаем актуальный список ID пользователей, отсортированный по возрастанию ID
    c.execute("SELECT id FROM users ORDER BY id ASC")
    user_ids_ordered = [user[0] for user in c.fetchall()]

    next_leading_user_id = None
    if user_ids_ordered: # Если список пользователей не пуст
        if current_leading_user_id in user_ids_ordered:
            try:
                current_index = user_ids_ordered.index(current_leading_user_id)
                # Определяем следующий индекс по кругу
                next_index = (current_index + 1) % len(user_ids_ordered)
                next_leading_user_id = user_ids_ordered[next_index]
            except ValueError:
                # Если по какой-то причине текущего ID нет в списке, назначаем первого
                next_leading_user_id = user_ids_ordered[0]
        else:
            # Если текущий ведущий вообще невалидный (None или удален), назначаем первого
            next_leading_user_id = user_ids_ordered[0]

    # Устанавливаем следующего ведущего, если он определен
    if next_leading_user_id is not None:
        set_leading_user_id(next_leading_user_id)
        flash(f"Очки подсчитаны. Следующий ведущий назначен (ID: {next_leading_user_id}).", "info")
    else:
        # Если пользователей нет, ведущий не может быть назначен
        # Сбрасываем настройку ведущего, чтобы при следующем запуске снова назначился первый
        set_leading_user_id(None)
        flash("Очки подсчитаны, но не удалось назначить следующего ведущего (нет пользователей).", "warning")

    conn.close() # Закрываем соединение ПОСЛЕ всех операций

    return redirect(url_for("admin"))
    
if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
