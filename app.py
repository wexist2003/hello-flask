import json
from flask import Flask, render_template, request, redirect, url_for, g, flash
import sqlite3
import os
import string
import random

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-very-secret-and-complex-key-should-be-here')
if app.config['SECRET_KEY'] == 'your-very-secret-and-complex-key-should-be-here':
    print("ПРЕДУПРЕЖДЕНИЕ: Используется SECRET_KEY по умолчанию. Установите переменную окружения SECRET_KEY для безопасности!")
DB_PATH = 'database.db'
# Убедитесь, что секретный ключ установлен (важно для flash сообщений)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "a_default_super_secret_key") # Лучше использовать переменную окружения

# --- Управление соединением с БД ---

def get_db():
    """Открывает новое соединение с БД, если его еще нет для текущего контекста."""
    if 'db' not in g:
        # Используем единый таймаут
        g.db = sqlite3.connect(DB_PATH, timeout=10)
        # Устанавливаем row_factory здесь для удобства доступа к колонкам по имени
        g.db.row_factory = sqlite3.Row
    return g.db

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password_attempt = request.form.get('password')

        # --- ПРОВЕРКА ПАРОЛЯ ---
        # !!! ВАЖНО: Это САМЫЙ НЕБЕЗОПАСНЫЙ СПОСОБ - только для примера!
        # Замените на безопасное сравнение (хеширование) или проверку по БД.
        # Лучше всего хранить пароль в переменной окружения ADMIN_PASSWORD
        correct_password = os.environ.get('ADMIN_PASSWORD')

        if correct_password and password_attempt == correct_password:
            # Пароль верный - устанавливаем флаг в сессии
            session['is_admin'] = True
            flash('Авторизация успешна.', 'success')
            # Перенаправляем на админку или на страницу, с которой пришли (если есть 'next')
            next_page = request.args.get('next')
            return redirect(next_page or url_for('admin'))
        else:
            # Пароль неверный
            flash('Неверный пароль.', 'danger')
            # Снова показываем форму входа (не редирект, чтобы не терять сообщение flash)
            # return redirect(url_for('login')) # Неправильно для показа ошибки
    # Если GET запрос или пароль был неверный - показываем форму
    return render_template('login.html')

@app.route('/logout')
def logout():
    # Удаляем флаг администратора из сессии
    session.pop('is_admin', None)
    flash('Вы вышли из системы.', 'info')
    return redirect(url_for('login')) # Перенаправляем на страницу входа
    
@app.route("/")
def index():
    """Обработчик для корневого URL (стартовой страницы)."""
    # Эта функция просто возвращает базовый HTML с заголовком и ссылкой на админку.
    # В будущем здесь можно будет сделать, например, страницу входа для пользователей
    # или автоматическое перенаправление на /admin или другую страницу.
    return "<h1>Игра Имаджинариум</h1><p><a href='/admin'>Перейти в админку</a></p>"
    
@app.teardown_appcontext
def close_db(error=None): # Аргумент error стандартен для teardown_appcontext
    """Закрывает соединение с БД после завершения запроса."""
    db = g.pop('db', None)
    if db is not None:
        db.close()

# --- Инициализация БД (вызывается отдельно, если нужно) ---

def init_db():
    # Открываем соединение ОДИН РАЗ в начале
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row # Устанавливаем row_factory
    c = conn.cursor()
    print("init_db: Connection opened.") # Добавлено для отладки

    try: # Оборачиваем основные операции в try/except для отката при ошибке
        # --- Удаление и создание таблиц ---
        print("init_db: Dropping tables...")
        c.execute("DROP TABLE IF EXISTS users")
        c.execute("DROP TABLE IF EXISTS images")
        c.execute("DROP TABLE IF EXISTS settings")
        print("init_db: Creating tables...")
        c.execute("""
            CREATE TABLE users ( id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL,
                                code TEXT UNIQUE NOT NULL, rating INTEGER DEFAULT 0 )""")
        c.execute("""
            CREATE TABLE images ( id INTEGER PRIMARY KEY AUTOINCREMENT, subfolder TEXT NOT NULL,
                                 image TEXT NOT NULL, status TEXT, owner_id INTEGER,
                                 guesses TEXT DEFAULT '{}' )""")
        c.execute("""
            CREATE TABLE settings ( key TEXT PRIMARY KEY, value TEXT )""")
        # Коммитим создание таблиц перед вставкой настроек
        conn.commit()
        print("init_db: Tables created and committed.")

        # --- Сброс настройки game_over ---
        try:
             # Сначала проверим, есть ли ключ
             c.execute("SELECT 1 FROM settings WHERE key = 'game_over'")
             if c.fetchone():
                 c.execute("UPDATE settings SET value = 'false' WHERE key = 'game_over'")
                 print("init_db: 'game_over' setting updated to false.")
             else:
                 c.execute("INSERT INTO settings (key, value) VALUES ('game_over', 'false')")
                 print("init_db: 'game_over' setting inserted as false.")
             # Коммит этой настройки можно сделать здесь или в конце вместе с картинками
        except sqlite3.Error as e:
             # Не критично, если не удалось сбросить настройку, продолжаем
             print(f"Warning: Could not reset 'game_over' setting during init_db: {e}")

        # --- Загрузка изображений ---
        print("init_db: Starting image loading...")
        image_folders = ['koloda1', 'koloda2'] # Убедитесь, что эти папки есть в static/images
        images_added_count = 0
        for folder in image_folders:
            folder_path = os.path.join('static', 'images', folder)
            print(f"init_db: Checking folder: {folder_path}")
            if os.path.exists(folder_path) and os.path.isdir(folder_path): # Добавлена проверка isdir
                print(f"init_db: Processing folder: {folder}")
                for filename in os.listdir(folder_path):
                    # Проверяем расширение файла
                    if filename.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
                        try:
                            # Проверяем, нет ли уже такой картинки (соединение должно быть открыто!)
                            print(f"init_db: Checking image {folder}/{filename}...")
                            c.execute("SELECT 1 FROM images WHERE subfolder = ? AND image = ?", (folder, filename))
                            if c.fetchone() is None:
                                 # Добавляем новую картинку
                                 c.execute("INSERT INTO images (subfolder, image, status, guesses) VALUES (?, ?, 'Свободно', '{}')", (folder, filename))
                                 images_added_count += 1
                                 print(f"init_db: Added image {folder}/{filename}")
                            # else: print(f"init_db: Image {folder}/{filename} already exists.") # Раскомментировать для детальной отладки
                        except sqlite3.Error as e:
                            # Логируем ошибку и продолжаем с другими картинками
                            print(f"Warning: Could not process image {folder}/{filename}: {e}")
            else:
                 print(f"Warning: Folder not found or is not a directory: {folder_path}")

        if images_added_count > 0:
            print(f"init_db: Added {images_added_count} new images to the database.")
        else:
            print("init_db: No new images were added.")

        # --- Финальный коммит ---
        # Коммитим вставку картинок и сброс настройки (если не коммитили раньше)
        conn.commit()
        print("init_db: Final commit successful.")

    except sqlite3.Error as e:
        # Откатываем все изменения, если произошла серьезная ошибка при создании таблиц и т.д.
        print(f"CRITICAL ERROR during init_db execution: {e}")
        conn.rollback()
        print("init_db: Changes rolled back due to critical error.")
        raise # Передаем исключение дальше, чтобы приложение знало об ошибке

    finally:
        # --- Закрываем соединение ---
        # Этот блок finally относится к внешнему try и выполняется всегда
        if conn:
            conn.close() # Закрываем соединение ОДИН РАЗ в самом конце
            print("init_db: Connection closed.")

# --- Вспомогательные функции ---

def is_game_over():
    """Проверяет, установлен ли флаг конца игры."""
    return get_setting('game_over') == 'true'

def set_game_over(state=True):
    """Устанавливает флаг конца игры."""
    return set_setting('game_over', 'true' if state else 'false')
def generate_unique_code(length=8):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def get_setting(key):
    """Получает значение настройки из БД, используя соединение из g."""
    try:
        db = get_db()
        c = db.cursor()
        c.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = c.fetchone()
        return row['value'] if row else None
    except sqlite3.Error as e:
        print(f"Database error in get_setting for key '{key}': {e}")
        return None # Возвращаем None при ошибке

def set_setting(key, value):
    """Устанавливает значение настройки в БД, используя соединение из g."""
    db = get_db()
    try:
        c = db.cursor()
        c.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        db.commit()
        return True
    except sqlite3.Error as e:
        print(f"Database error in set_setting for key '{key}': {e}")
        db.rollback()
        return False

def get_leading_user_id():
    """Получает ID ведущего пользователя."""
    value = get_setting('leading_user_id')
    if value:
        try:
            return int(value)
        except (ValueError, TypeError):
            print(f"Invalid leading_user_id value found in settings: {value}")
            return None
    return None

def set_leading_user_id(user_id):
    """Устанавливает ID ведущего пользователя."""
    # Конвертируем None в пустую строку или специальное значение, если нужно хранить отсутствие лидера
    value_to_set = str(user_id) if user_id is not None else ''
    return set_setting('leading_user_id', value_to_set)

def get_user_name(user_id):
    """Получает имя пользователя по ID."""
    if user_id is None:
        return None
    try:
        user_id_int = int(user_id)
        db = get_db()
        c = db.cursor()
        c.execute("SELECT name FROM users WHERE id = ?", (user_id_int,))
        user_name_row = c.fetchone()
        return user_name_row['name'] if user_name_row else None
    except (ValueError, TypeError, sqlite3.Error) as e:
        print(f"Error in get_user_name for ID '{user_id}': {e}")
        return None

# --- Глобальные переменные и функции для Jinja ---
# Обновляем после определения функций
app.jinja_env.globals.update(
    get_user_name=get_user_name,
    get_leading_user_id=get_leading_user_id) # Передаем функцию в шаблон
    # g доступен в шаблонах по умолчанию, но можно передать явно: g=g

# --- Обработчики запросов ---

@app.before_request
def before_request():
    """Выполняется перед каждым запросом."""
    # Получаем соединение (оно будет создано, если его нет)
    db = get_db()
    c = db.cursor()
    code = None
    if request.view_args and 'code' in request.view_args:
        code = request.view_args.get('code')
    elif request.args and 'code' in request.args:
        code = request.args.get('code')

    g.user_id = None # Пользователь по умолчанию не аутентифицирован
    if code:
        try:
            c.execute("SELECT id FROM users WHERE code = ?", (code,))
            user_row = c.fetchone()
            if user_row:
                g.user_id = user_row['id']
        except sqlite3.Error as e:
            print(f"Database error in before_request checking code '{code}': {e}")
            g.user_id = None # Считаем недействительным при ошибке

    # Получаем настройку видимости информации
    # get_setting теперь использует get_db()
    show_card_info = get_setting("show_card_info")
    g.show_card_info = show_card_info == "true" # Сохраняем как boolean в g

    # --->>> ДОБАВЛЕНО: Чтение статуса конца игры <<<---
    g.game_over = is_game_over()
    # --->>> КОНЕЦ ДОБАВЛЕНИЯ <<<---

    # Соединение НЕ закрывается здесь, закроется автоматически через teardown_appcontext

@app.route("/admin", methods=["GET", "POST"])
def admin():
    # --- ПРОВЕРКА АДМИНА ---
    if not session.get('is_admin'):
        flash('Для доступа к этой странице требуется авторизация администратора.', 'warning')
        # Перенаправляем на страницу входа, запомнив, куда пользователь хотел попасть
        return redirect(url_for('login', next=request.url))
    # --- КОНЕЦ ПРОВЕРКИ АДМИНА ---
    # Используем соединение через g для потокобезопасности
    db = get_db()
    c = db.cursor()
    leader_to_display = None
    current_active_subfolder = '' # Значения по умолчанию
    show_card_info = False

    # --- Читаем начальные настройки ---
    try:
        current_actual_leader_id = get_leading_user_id() # ID текущего/следующего лидера
        current_active_subfolder = get_setting('active_subfolder') or '' # Активная папка
        show_card_info = get_setting('show_card_info') == "true" # Видимость карт

        # Определяем, кого показывать как "Ведущий" (может быть предыдущий из URL)
        displayed_leader_id_from_url_str = request.args.get('displayed_leader_id')
        if displayed_leader_id_from_url_str:
            try:
                leader_to_display = int(displayed_leader_id_from_url_str)
            except (ValueError, TypeError):
                leader_to_display = current_actual_leader_id # Fallback
        else:
            leader_to_display = current_actual_leader_id

    except Exception as e:
        print(f"CRITICAL Error reading initial settings: {e}")
        flash(f"Критическая ошибка чтения начальных настроек: {e}", "danger")
        # Не можем продолжить без настроек
        return render_template("admin.html", users=[], images=[], subfolders=['koloda1', 'koloda2'],
                               active_subfolder='', guess_counts_by_user={}, all_guesses={},
                               show_card_info=False, leader_to_display=None,
                               free_image_count=0, image_owners={}, user_has_duplicate_guesses={})

    # --- Обработка POST запросов ---
    if request.method == "POST":
        action_handled = False # Флаг, что действие POST было обработано
        # ID лидера для редиректа - по умолчанию тот, кто отображался до POST
        leader_for_redirect = leader_to_display

        try:
            if "name" in request.form:
                # --- Создание пользователя ---
                name = request.form.get("name", "").strip()
                user_created_success = False # Флаг для коммита
                if not name:
                     flash("Имя пользователя не может быть пустым.", "warning")
                else:
                    num_cards = int(request.form.get("num_cards", 3))
                    if num_cards < 1: num_cards = 1
                    code = generate_unique_code()

                    c.execute("SELECT 1 FROM users WHERE name = ?", (name,))
                    if c.fetchone():
                        flash(f"Имя пользователя '{name}' уже существует.", "danger")
                    else:
                        # Добавляем пользователя
                        c.execute("INSERT INTO users (name, code) VALUES (?, ?)", (name, code))
                        user_id = c.lastrowid
                        flash(f"Пользователь '{name}' добавлен.", "success")
                        user_created_success = True # Успех

                        # Назначаем ведущего, если его не было
                        if current_actual_leader_id is None:
                            if set_leading_user_id(user_id):
                                flash(f"Пользователь '{name}' назначен Ведущим.", "info")
                                current_actual_leader_id = user_id
                                if leader_to_display is None:
                                    leader_to_display = current_actual_leader_id
                            else:
                                flash("Ошибка назначения ведущего.", "warning")

                        # Раздаем карты
                        if current_active_subfolder:
                            c.execute("SELECT id FROM images WHERE subfolder = ? AND status = 'Свободно'", (current_active_subfolder,))
                            available_cards_ids = [row['id'] for row in c.fetchall()]
                            if len(available_cards_ids) < num_cards:
                                 flash(f"Недостаточно свободных карт ({len(available_cards_ids)}) в '{current_active_subfolder}' для {num_cards} шт.", "warning")
                                 num_cards = len(available_cards_ids)
                            if num_cards > 0:
                                selected_cards_ids = random.sample(available_cards_ids, num_cards)
                                for card_id in selected_cards_ids:
                                    c.execute("UPDATE images SET status = ? WHERE id = ?", (f"Занято:{user_id}", card_id))
                                flash(f"'{name}' назначено {num_cards} карт.", "info")
                        else:
                             flash("Активная колода не выбрана, карты не назначены.", "warning")

                if user_created_success:
                     db.commit() # Коммитим только если пользователь создан
                     action_handled = True
                     # Редирект покажет текущего лидера
                     leader_for_redirect = current_actual_leader_id

            elif "active_subfolder" in request.form:
                # --- Смена активной колоды ---
                selected = request.form.get("active_subfolder")
                if set_setting('active_subfolder', selected): # set_setting делает commit
                    try:
                        # Обновляем статусы карт в ДРУГИХ колодах
                        updated_inactive = c.execute("UPDATE images SET status = 'Занято:Админ' WHERE subfolder != ? AND status = 'Свободно'", (selected,)).rowcount
                        # Коммитим изменение статусов
                        db.commit()

                        flash_message_text = f"Выбрана активная колода: {selected}."
                        if updated_inactive > 0:
                            flash_message_text += f" Карты в других колодах ({updated_inactive} шт.) помечены как неактивные."
                        flash(flash_message_text, "success")
                        current_active_subfolder = selected # Обновляем переменную для текущего запроса
                    except sqlite3.Error as e:
                        db.rollback()
                        flash(f"Ошибка обновления статусов карт: {e}", "danger")
                else:
                    flash("Ошибка сохранения настройки активной колоды.", "danger")

                # Лидер не меняется, редирект покажет того же, кто отображался
                leader_for_redirect = leader_to_display
                action_handled = True

            elif "delete_user_id" in request.form:
                # --- Удаление пользователя ---
                user_id_to_delete = int(request.form.get("delete_user_id"))
                # Проверяем, был ли он лидером, используя актуальное значение
                was_leader = (current_actual_leader_id == user_id_to_delete)

                c.execute("SELECT name FROM users WHERE id = ?", (user_id_to_delete,))
                user_to_delete = c.fetchone()

                if user_to_delete:
                    user_name_deleted = user_to_delete['name']
                    # Выполняем удаление и очистку
                    c.execute("DELETE FROM users WHERE id = ?", (user_id_to_delete,))
                    c.execute("UPDATE images SET status = 'Свободно' WHERE status = ?", (f"Занято:{user_id_to_delete}",))
                    c.execute("UPDATE images SET status = 'Свободно', owner_id = NULL, guesses = '{}' WHERE owner_id = ?", (user_id_to_delete,))
                    flash(f"Пользователь '{user_name_deleted}' удален.", "success")

                    # Логика переназначения лидера
                    new_leader_id_after_delete = current_actual_leader_id # По умолчанию оставляем
                    if was_leader:
                        c.execute("SELECT id FROM users ORDER BY id")
                        remaining_users = c.fetchall()
                        if remaining_users:
                            new_leader_id_after_delete = remaining_users[0]['id']
                            if set_leading_user_id(new_leader_id_after_delete):
                                new_leader_name = get_user_name(new_leader_id_after_delete) or f"ID {new_leader_id_after_delete}"
                                flash(f"Удаленный пользователь был Ведущим. Новый Ведущий: {new_leader_name}.", "info")
                            else:
                                flash("Ошибка назначения нового ведущего.", "warning")
                        else:
                            new_leader_id_after_delete = None
                            set_leading_user_id(None)
                            flash("Удаленный пользователь был Ведущим. Пользователей не осталось.", "warning")
                        # Устанавливаем ID для редиректа
                        leader_for_redirect = new_leader_id_after_delete
                    else:
                        # Если удалили не лидера, редирект покажет того же лидера
                         leader_for_redirect = current_actual_leader_id

                    db.commit() # Коммитим удаление и смену лидера
                else:
                    flash(f"Пользователь с ID {user_id_to_delete} не найден.", "danger")
                    leader_for_redirect = leader_to_display # Оставляем как было

                action_handled = True

            # --- Редирект после успешного POST действия ---
            if action_handled:
                # Используем leader_for_redirect, определенный выше
                return redirect(url_for('admin', displayed_leader_id=leader_for_redirect))

        # Обработка ошибок во время POST
        except sqlite3.IntegrityError as e:
             if "UNIQUE constraint failed" not in str(e):
                 flash(f"Ошибка целостности базы данных: {e}", "danger")
             db.rollback()
        except (sqlite3.Error, ValueError, TypeError) as e:
             flash(f"Ошибка при обработке запроса: {e}", "danger")
             db.rollback()
        except Exception as e:
              print(f"!!! UNEXPECTED ERROR during admin POST: {e}") # Логируем
              flash(f"Произошла непредвиденная ошибка: {e}", "danger")
              db.rollback()
        # Если была ошибка и не было редиректа, переходим к отображению GET

    # --- Получение данных для отображения (GET request или после ошибки POST) ---
    # Переменные current_active_subfolder и leader_to_display уже установлены
    users, images, subfolders, guess_counts_by_user, all_guesses = [], [], [], {}, {}
    free_image_count = 0
    image_owners = {}
    user_has_duplicate_guesses = {} # Для подсветки дубликатов

    try:
        # Получаем пользователей
        c.execute("SELECT id, name, code, rating FROM users ORDER BY name ASC")
        users = c.fetchall()
        print(f"Admin GET: Fetched {len(users)} users.")

        # Получаем изображения
        c.execute("SELECT id, subfolder, image, status, owner_id, guesses FROM images ORDER BY subfolder, id")
        images_rows = c.fetchall()
        images = []
        all_guesses = {}
        print(f"Admin GET: Fetched {len(images_rows)} image rows. Active subfolder: '{current_active_subfolder}'")

        for img_row in images_rows:
            # Парсинг JSON guesses
            guesses_json_str = img_row['guesses'] or '{}'
            try:
                 guesses_dict = json.loads(guesses_json_str)
            except json.JSONDecodeError as json_e:
                 print(f"Warning: JSONDecodeError for image ID {img_row['id']} - guesses: '{guesses_json_str}'. Error: {json_e}. Using empty dict.")
                 guesses_dict = {}

            img_dict = dict(img_row)
            img_dict['guesses'] = guesses_dict
            images.append(img_dict)

            if img_dict['owner_id'] is not None:
                image_owners[img_dict['id']] = img_dict['owner_id']
            if img_dict['status'] == 'Свободно' and img_dict['subfolder'] == current_active_subfolder:
                free_image_count += 1
            if guesses_dict:
                 all_guesses[img_row['id']] = guesses_dict

        print(f"Admin GET: Processed images. Free count in active folder: {free_image_count}")

        # Проверка дубликатов в предположениях
        user_has_duplicate_guesses = {user['id']: False for user in users}
        if all_guesses:
            for user in users:
                user_id_str = str(user['id'])
                guesses_made_by_user = []
                for image_id, guesses_for_image in all_guesses.items():
                    if user_id_str in guesses_for_image:
                        guesses_made_by_user.append(guesses_for_image[user_id_str])
                if len(guesses_made_by_user) > len(set(guesses_made_by_user)):
                     user_has_duplicate_guesses[user['id']] = True

        # Подсчет сделанных предположений каждым пользователем
        guess_counts_by_user = {user['id']: 0 for user in users}
        for img_id, guesses_for_image in all_guesses.items():
            for guesser_id_str in guesses_for_image:
                 try:
                     if int(guesser_id_str) in guess_counts_by_user:
                         guess_counts_by_user[int(guesser_id_str)] += 1
                 except (ValueError, TypeError): pass

        print(f"Admin GET: Calculated guess counts and duplicates.")

        # Получение списка папок
        c.execute("SELECT DISTINCT subfolder FROM images ORDER BY subfolder")
        subfolders = [row['subfolder'] for row in c.fetchall()] or ['koloda1', 'koloda2']
        print(f"Admin GET: Found subfolders: {subfolders}")

    # Обработка ошибки чтения данных для GET
    except sqlite3.Error as e:
        print(f"!!! ERROR caught in admin GET data fetch: {e}")
        print(f"!!! State before reset: users length={len(users)}, images length={len(images)}, subfolders={len(subfolders)}")
        flash(f"Ошибка чтения данных для отображения: {e}", "danger")
        # Сбрасываем все данные в пустые списки/словари при ошибке
        users, images, subfolders, guess_counts_by_user, all_guesses = [], [], [], {}, {}
        free_image_count = 0
        image_owners = {}
        user_has_duplicate_guesses = {}

    except Exception as e: # Ловим другие возможные ошибки
         print(f"!!! UNEXPECTED ERROR caught in admin GET data fetch: {e}")
         flash(f"Непредвиденная ошибка при чтении данных: {e}", "danger")
         users, images, subfolders, guess_counts_by_user, all_guesses = [], [], [], {}, {}
         free_image_count = 0
         image_owners = {}
         user_has_duplicate_guesses = {}

    # Рендеринг шаблона с полученными данными
    print(f"Admin GET: Rendering template. Users count: {len(users)}")
    # Соединение закроется автоматически через teardown_appcontext
    return render_template("admin.html", users=users, images=images,
                           subfolders=subfolders, active_subfolder=current_active_subfolder,
                           guess_counts_by_user=guess_counts_by_user, all_guesses=all_guesses,
                           show_card_info=show_card_info,
                           leader_to_display=leader_to_display,
                           free_image_count=free_image_count,
                           image_owners=image_owners,
                           user_has_duplicate_guesses=user_has_duplicate_guesses) # Передаем флаги дубликатов
    

@app.route("/start_new_game", methods=["POST"])
def start_new_game():
    """Сбрасывает игру и начинает заново с выбранной колодой и раздачей карт."""
    db = get_db()
    c = db.cursor()

    selected_deck = request.form.get("new_game_subfolder")
    try:
        num_cards_per_player = int(request.form.get("new_game_num_cards", 3))
        if num_cards_per_player < 1:
            raise ValueError("Количество карт должно быть не меньше 1.")
    except (ValueError, TypeError):
        flash("Неверное количество карт для раздачи.", "danger")
        return redirect(url_for('admin'))

    if not selected_deck:
        flash("Колода для новой игры не выбрана.", "danger")
        return redirect(url_for('admin'))

    print(f"--- Начало новой игры с колодой: {selected_deck}, карт на игрока: {num_cards_per_player} ---")

    try:
        # === Полный сброс игрового состояния ===
        print("Сброс рейтингов...")
        c.execute("UPDATE users SET rating = 0")

        print("Сброс состояния карт...")
        # Сначала все карты помечаем как Занято:Админ и очищаем владельцев/угадывания
        c.execute("UPDATE images SET owner_id = NULL, guesses = '{}', status = 'Занято:Админ'")
        # Затем карты выбранной колоды делаем свободными
        c.execute("UPDATE images SET status = 'Свободно' WHERE subfolder = ?", (selected_deck,))

        print("Сброс настроек игры...")
        set_game_over(False) # Используем функцию, добавленную ранее
        set_setting("show_card_info", "false")
        set_setting("active_subfolder", selected_deck)

        # Назначаем нового ведущего (первого по ID)
        c.execute("SELECT id FROM users ORDER BY id LIMIT 1")
        first_user = c.fetchone()
        new_leader_id = None
        if first_user:
            new_leader_id = first_user['id']
            set_leading_user_id(new_leader_id)
            print(f"Назначен новый ведущий: {get_user_name(new_leader_id)} (ID: {new_leader_id})")
        else:
            set_leading_user_id(None) # Нет пользователей - нет ведущего
            print("Пользователи не найдены, ведущий не назначен.")

        db.commit() # Коммитим все сбросы перед раздачей

        # === Раздача карт существующим пользователям ===
        c.execute("SELECT id FROM users ORDER BY id")
        user_ids = [row['id'] for row in c.fetchall()]
        num_users = len(user_ids)
        num_total_dealt = 0

        if not user_ids:
            flash("Пользователи не найдены. Новая игра начата, но карты не розданы.", "warning")
        else:
            print(f"Раздача карт {num_users} пользователям...")
            c.execute("SELECT id FROM images WHERE subfolder = ? AND status = 'Свободно'", (selected_deck,))
            available_cards_ids = [row['id'] for row in c.fetchall()]
            random.shuffle(available_cards_ids)
            num_available = len(available_cards_ids)
            print(f"Доступно карт в колоде '{selected_deck}': {num_available}")

            if num_available < num_users * num_cards_per_player:
                 flash(f"Внимание: Недостаточно свободных карт ({num_available}) в колоде '{selected_deck}' для раздачи по {num_cards_per_player} шт. всем {num_users} игрокам.", "warning")
                 # Можно добавить логику частичной раздачи или остановки

            card_index = 0
            for user_id in user_ids:
                cards_dealt_to_user = 0
                for _ in range(num_cards_per_player):
                    if card_index < num_available:
                        card_id = available_cards_ids[card_index]
                        c.execute("UPDATE images SET status = ? WHERE id = ?", (f"Занято:{user_id}", card_id))
                        card_index += 1
                        cards_dealt_to_user += 1
                    else:
                        # Карты в колоде закончились
                        break # Прерываем раздачу этому игроку
                print(f"  Пользователю ID {user_id} роздано карт: {cards_dealt_to_user}")
                num_total_dealt += cards_dealt_to_user
                if card_index >= num_available:
                     break # Прерываем раздачу всем, если карты кончились

            flash(f"Новая игра начата! Колода: '{selected_deck}'. Роздано карт: {num_total_dealt}.", "success")
            if new_leader_id:
                flash(f"Ведущий назначен: {get_user_name(new_leader_id)}.", "info")

        db.commit() # Коммитим раздачу карт
        print("--- Новая игра успешно начата и карты розданы ---")

    except sqlite3.Error as e:
        db.rollback()
        flash(f"Ошибка базы данных при начале новой игры: {e}", "danger")
        print(f"Database error during start_new_game: {e}")
    except Exception as e:
        db.rollback()
        flash(f"Непредвиденная ошибка при начале новой игры: {e}", "danger")
        print(f"Unexpected error during start_new_game: {e}")

    return redirect(url_for('admin'))
    
@app.route("/user/<code>")
def user(code):
    db = get_db()
    c = db.cursor()
    user_data = None
    leader_for_display = None # ID ведущего для отображения на этой странице

    try:
        c.execute("SELECT id, name, rating FROM users WHERE code = ?", (code,))
        user_data = c.fetchone()

        if not user_data:
            flash("Пользователь с таким кодом не найден.", "warning")
            return redirect(url_for('index'))

        user_id = user_data['id']
        name = user_data['name']
        rating = user_data['rating']

        # Проверка соответствия g.user_id (уже установлено в before_request)
        if g.user_id != user_id:
            flash("Ошибка доступа или неверный код.", "danger")
            return redirect(url_for('index'))

        c.execute("SELECT id, subfolder, image FROM images WHERE status = ?", (f"Занято:{user_id}",))
        cards = c.fetchall()

        c.execute("SELECT id, subfolder, image, owner_id, guesses FROM images WHERE owner_id IS NOT NULL")
        table_images_data = c.fetchall()
        table_images = []
        for img_row in table_images_data:
            guesses_json_str = img_row['guesses'] or '{}'
            try: guesses_dict = json.loads(guesses_json_str)
            except json.JSONDecodeError: guesses_dict = {}
            img_dict = dict(img_row)
            img_dict['guesses'] = {str(k): v for k, v in guesses_dict.items()} # Ключи в JSON должны быть строками
            table_images.append(img_dict)

        c.execute("SELECT id, name FROM users ORDER BY name ASC")
        all_users = c.fetchall()

        c.execute("SELECT 1 FROM images WHERE owner_id = ?", (user_id,))
        on_table = c.fetchone() is not None

        # Определение ID ведущего для отображения (текущий или предыдущий)
        current_leader_id = get_leading_user_id()
        leader_for_display = current_leader_id

        if g.show_card_info and current_leader_id is not None:
            c.execute("SELECT id FROM users ORDER BY id")
            user_ids_ordered = [row['id'] for row in c.fetchall()]
            if user_ids_ordered:
                try:
                    current_index = user_ids_ordered.index(current_leader_id)
                    previous_index = (current_index - 1 + len(user_ids_ordered)) % len(user_ids_ordered)
                    leader_for_display = user_ids_ordered[previous_index]
                except ValueError: pass # Оставляем current_leader_id, если он не в списке

    except sqlite3.Error as e:
        flash(f"Ошибка базы данных при загрузке профиля: {e}", "danger")
        # Редирект или отображение ошибки
        return redirect(url_for('index'))

    # g уже доступен в шаблоне, передаем остальные данные
    return render_template("user.html", name=name, rating=rating, cards=cards,
                           table_images=table_images, all_users=all_users,
                           code=code, on_table=on_table,
                           leader_for_display=leader_for_display)


@app.route("/user/<code>/guess/<int:image_id>", methods=["POST"])
def guess_image(code, image_id):
    # Проверка g.user_id (установлен в before_request)
    if not g.user_id:
        flash("Доступ запрещен. Пожалуйста, используйте вашу уникальную ссылку.", "danger")
        return redirect(url_for('index'))

    # Дополнительная проверка, что код в URL совпадает с g.user_id (хотя before_request уже это делает)
    # db = get_db(); c = db.cursor(); c.execute("SELECT id FROM users WHERE code=?", (code,)); user_by_code = c.fetchone()
    # if not user_by_code or user_by_code['id'] != g.user_id: return redirect(url_for('index'))

    guessed_user_id_str = request.form.get("guessed_user_id")
    if not guessed_user_id_str:
        flash("Игрок для предположения не выбран.", "warning")
        return redirect(url_for('user', code=code))

    db = get_db()
    c = db.cursor()
    try:
        guessed_user_id = int(guessed_user_id_str)
        c.execute("SELECT 1 FROM users WHERE id = ?", (guessed_user_id,))
        if not c.fetchone():
            flash("Выбранный для предположения игрок не существует.", "danger")
            return redirect(url_for('user', code=code))

        c.execute("SELECT guesses, owner_id FROM images WHERE id = ?", (image_id,))
        image_data = c.fetchone()
        if not image_data:
            flash("Карточка не найдена.", "danger")
            return redirect(url_for('user', code=code))

        # Нельзя угадывать свою карту
        if image_data['owner_id'] == g.user_id:
             flash("Нельзя угадывать свою карточку.", "warning")
             return redirect(url_for('user', code=code))

        guesses_json_str = image_data['guesses'] or '{}'
        try: guesses = json.loads(guesses_json_str)
        except json.JSONDecodeError: guesses = {}

        # Ключ в JSON всегда строка
        guesses[str(g.user_id)] = guessed_user_id

        c.execute("UPDATE images SET guesses = ? WHERE id = ?", (json.dumps(guesses), image_id))
        db.commit()
        flash("Ваше предположение сохранено.", "success")

    except (ValueError, TypeError):
        flash("Неверный ID игрока для предположения.", "danger")
    except sqlite3.Error as e:
        db.rollback()
        flash(f"Ошибка сохранения предположения: {e}", "danger")

    return redirect(url_for('user', code=code))


@app.route("/user/<code>/place/<int:image_id>", methods=["POST"])
def place_card(code, image_id):
    if not g.user_id:
        flash("Доступ запрещен.", "danger")
        return redirect(url_for('index'))

    db = get_db()
    c = db.cursor()
    try:
        # Проверяем, есть ли уже карта пользователя на столе
        c.execute("SELECT 1 FROM images WHERE owner_id = ?", (g.user_id,))
        if c.fetchone() is not None:
            flash("У вас уже есть карта на столе.", "warning")
            return redirect(url_for('user', code=code))

        # Проверяем, принадлежит ли карта пользователю
        c.execute("SELECT status FROM images WHERE id = ?", (image_id,))
        card_status_row = c.fetchone()
        if not card_status_row or card_status_row['status'] != f"Занято:{g.user_id}":
            flash("Вы не можете выложить эту карту.", "danger")
            return redirect(url_for('user', code=code))

        # Обновляем статус карты
        c.execute("UPDATE images SET owner_id = ?, status = 'На столе', guesses = '{}' WHERE id = ?", (g.user_id, image_id)) # Сбрасываем guesses при выкладывании
        db.commit()
        flash("Ваша карта выложена на стол.", "success")

    except sqlite3.Error as e:
        db.rollback()
        flash(f"Ошибка выкладывания карты: {e}", "danger")

    return redirect(url_for('user', code=code))


@app.route("/open_cards", methods=["POST"])
def open_cards():
    if hasattr(g, 'game_over') and g.game_over:
        flash("Игра окончена. Подсчет очков невозможен.", "warning")
        return redirect(url_for('admin'))

    db = get_db()
    c = db.cursor()
    leader_just_finished = get_leading_user_id()

    stop_processing = False
    points_summary = []

    try:
        if not set_setting("show_card_info", "true"):
            flash("Не удалось обновить настройку видимости карт.", "warning")

        if leader_just_finished is None:
            c.execute("SELECT id FROM users ORDER BY id LIMIT 1")
            first_user = c.fetchone()
            if first_user:
                leader_just_finished = first_user['id']
                if not set_leading_user_id(leader_just_finished):
                    flash(f"Не удалось установить первого ведущего (ID: {leader_just_finished}).", "danger")
                    db.rollback()
                    return redirect(url_for("admin"))
                flash(f"Ведущий не был установлен. Назначен: {get_user_name(leader_just_finished)}.", "info")
            else:
                flash("Нет пользователей для подсчета очков.", "warning")
                return redirect(url_for("admin"))

        # --- Подсчет очков ---
        c.execute("SELECT id, owner_id, guesses FROM images WHERE owner_id IS NOT NULL")
        table_images = c.fetchall()
        c.execute("SELECT id FROM users")
        all_user_ids = [int(user['id']) for user in c.fetchall()]
        num_all_users = len(all_user_ids)
        user_points = {user_id: 0 for user_id in all_user_ids} # Очки ЗА РАУНД

        if leader_just_finished not in user_points and leader_just_finished is not None:
            flash(f"Ведущий (ID: {leader_just_finished}) не найден среди текущих пользователей.", "warning")

        print("--- Начисление очков ---")

        for image_data in table_images:
            if stop_processing:
                print("  Обработка карт прервана из-за выполнения Правила 1.")
                break

            owner_id = image_data['owner_id']
            image_id = image_data['id']
            guesses_json_str = image_data['guesses'] or '{}'

            try: guesses = json.loads(guesses_json_str)
            except json.JSONDecodeError:
                print(f"  ПРЕДУПРЕЖДЕНИЕ: Некорректный JSON в guesses для карты {image_id}.")
                guesses = {}

            try: owner_id = int(owner_id)
            except (ValueError, TypeError):
                print(f"  ПРЕДУПРЕЖДЕНИЕ: Некорректный owner_id ({owner_id}) для карты {image_id}.")
                continue

            if owner_id not in user_points:
                print(f"  ИНФО: Владелец {owner_id} карты {image_id} неактивен.")
                continue

            print(f"\n  Обработка карты {image_id} (Владелец: {owner_id})")
            correct_guesses_count = 0
            correct_guesser_ids = []

            for guesser_id_str, guessed_user_id in guesses.items():
                try:
                    guesser_id = int(guesser_id_str)
                    try: guessed_user_id = int(guessed_user_id)
                    except (ValueError, TypeError): continue

                    if guesser_id in user_points and guesser_id != owner_id:
                        if guessed_user_id == owner_id:
                            correct_guesses_count += 1
                            correct_guesser_ids.append(guesser_id)
                            if owner_id == leader_just_finished: # Правило 4
                                user_points[guesser_id] += 3
                                print(f"    Игрок {guesser_id} угадал ВЕДУЩЕГО --> +3 (предварительно)")
                            # else: # Правило 3 - угадывание НЕ ведущего дает +0

                except (ValueError, TypeError): continue

            num_potential_guessers = num_all_users - 1 if num_all_users > 1 else 0

            if owner_id == leader_just_finished: # --- ЛОГИКА КАРТЫ ВЕДУЩЕГО ---
                print(f"    --- Обработка очков ВЕДУЩЕГО {owner_id} ---")
                print(f"      Правильных угадываний: {correct_guesses_count}, Потенциальных угадывающих: {num_potential_guessers}")

                if num_potential_guessers > 0:
                    if correct_guesses_count == num_potential_guessers: # --- Правило 1 ---
                        print(f"      Все ({correct_guesses_count}) угадали Ведущего {owner_id}.")
                        stop_processing = True
                        # --- ИЗМЕНЕНИЕ: Прямое обновление рейтинга Ведущего с учетом порога 0 ---
                        try:
                            # Используем MAX(0, rating - 3)
                            c.execute("UPDATE users SET rating = MAX(0, rating - 3) WHERE id = ?", (owner_id,))
                            print(f"      !!! Прямое обновление рейтинга Ведущего {owner_id}: MAX(0, рейтинг - 3) !!!")
                        except sqlite3.Error as direct_update_err:
                            print(f"!!! ОШИБКА прямого обновления рейтинга Ведущего {owner_id}: {direct_update_err}")
                            flash(f"Ошибка БД при обновлении рейтинга Ведущего (ID: {owner_id}) по Правилу 1.", "danger")
                            db.rollback()
                            return redirect(url_for("admin"))
                        print("      !!! Начисление очков ОСТАНОВЛЕНО (Правило 1) !!!")
                        break # Прерываем цикл по картам немедленно

                    elif correct_guesses_count == 0: # --- Правило 2 ---
                        user_points[owner_id] -= 2
                        print(f"      Никто не угадал. Ведущий {owner_id} --> -2 (будет учтен порог 0 при обновлении).")
                    else: # --- Правило 5 --- (0 < count < all)
                        points_for_leader = 3 + correct_guesses_count
                        user_points[owner_id] += points_for_leader
                        print(f"      {correct_guesses_count} угадали (не все). Ведущий {owner_id} --> +3 + {correct_guesses_count} = +{points_for_leader}.")
                else: # Нет угадывающих -> Правило 2
                    user_points[owner_id] -= 2
                    print(f"      Нет потенциальных угадывающих. Ведущий {owner_id} --> -2 (будет учтен порог 0 при обновлении).")

            else: # --- ЛОГИКА КАРТЫ НЕ ВЕДУЩЕГО --- (Правило 3)
                if correct_guesses_count > 0:
                    user_points[owner_id] += correct_guesses_count
                    print(f"    Карта НЕ Ведущего {owner_id}: Владелец --> +{correct_guesses_count}.")

        # --- Обновление рейтинга (если не остановлено) ---
        print("\n--- Обновление рейтинга ---")
        if stop_processing:
            print("  Обновление рейтинга (кроме Ведущего по Правилу 1) пропущено.")
            flash("Подсчет очков остановлен: все угадали карту Ведущего. Только Ведущему изменено -3 очка (но не ниже 0).", "info")
        else:
            for user_id, points in user_points.items():
                if points != 0:
                    try:
                        user_name = f"ID {user_id}"
                        try: fetched_name = get_user_name(user_id)
                        except Exception: fetched_name = None
                        if fetched_name: user_name = fetched_name

                        print(f"  Обновление пользователя {user_id} ({user_name}): {points:+}")
                        # --- ИЗМЕНЕНИЕ: Обновление с учетом порога 0 ---
                        # Используем MAX(0, rating + points)
                        c.execute("UPDATE users SET rating = MAX(0, rating + ?) WHERE id = ?", (points, user_id))
                        points_summary.append(f"{user_name}: {points:+}")

                    except sqlite3.Error as e:
                        print(f"!!! ОШИБКА обновления рейтинга для {user_id}: {e}")
                        flash(f"Ошибка обновления рейтинга для пользователя ID {user_id}", "danger")
                        db.rollback()
                        print("  !!! Транзакция отменена !!!")
                        return redirect(url_for("admin"))

        # --- Определение и сохранение СЛЕДУЮЩЕГО ведущего ---
        # (Логика определения и сохранения следующего ведущего остается без изменений)
        next_leading_user_id = None
        try:
            c.execute("SELECT id FROM users ORDER BY id")
            user_ids_ordered = [int(row['id']) for row in c.fetchall()]
        except sqlite3.Error as e:
            print(f"Error getting user IDs for next leader: {e}")
            flash("Ошибка БД при определении следующего ведущего.", "danger")
            db.rollback() # Важно откатить, если ошибка здесь
            return redirect(url_for("admin"))

        if not user_ids_ordered:
             flash("Нет пользователей для определения следующего ведущего.", "warning")
             set_leading_user_id(None)
        elif leader_just_finished is not None and leader_just_finished in user_ids_ordered:
             try:
                 current_index = user_ids_ordered.index(leader_just_finished)
                 next_index = (current_index + 1) % len(user_ids_ordered)
                 next_leading_user_id = user_ids_ordered[next_index]
             except ValueError:
                 print(f"Предупреждение: ID ведущего {leader_just_finished} не найден.")
                 next_leading_user_id = user_ids_ordered[0]
        elif user_ids_ordered:
             next_leading_user_id = user_ids_ordered[0]

        if next_leading_user_id is not None:
            if set_leading_user_id(next_leading_user_id):
                next_leader_name = get_user_name(next_leading_user_id) or f"ID {next_leading_user_id}"
                if not stop_processing:
                     flash(f"Подсчет очков завершен. Следующий ведущий: {next_leader_name}.", "success")
                else:
                     flash(f"Раунд завершен (очки не сохранены, кроме -3 Ведущему с порогом 0). Следующий ведущий: {next_leader_name}.", "info")
            else:
                 flash("Критическая ошибка: не удалось сохранить нового ведущего.", "danger")
                 db.rollback()
                 return redirect(url_for("admin"))
        else:
             flash("Не удалось определить следующего ведущего.", "warning")
             set_leading_user_id(None)

        # Показываем сводку очков (только если не было остановки)
        if points_summary and not stop_processing:
            flash(f"Изменение очков: {'; '.join(points_summary)}", "info")
        elif not stop_processing and not points_summary:
             flash("В этом раунде очки не изменились.", "info")

        # --- Коммит всех изменений ---
        db.commit()
        print("--- Подсчет очков и обновление завершены успешно ---")

    except sqlite3.Error as e:
        db.rollback()
        flash(f"Ошибка базы данных во время обработки раунда: {e}", "danger")
        print(f"Database error in open_cards: {e}")
        print(traceback.format_exc())
        return redirect(url_for("admin"))
    except Exception as e:
        db.rollback()
        flash(f"Непредвиденная ошибка во время обработки раунда: {type(e).__name__}", "danger")
        print(f"Unexpected error in open_cards: {e}")
        print(traceback.format_exc())
        return redirect(url_for("admin"))

    return redirect(url_for("admin", displayed_leader_id=leader_just_finished))


@app.route("/new_round", methods=["POST"])
def new_round():
    """Начинает новый раунд: сброс стола/угадываний, раздача карт, скрытие информации."""
    
    # --->>> ДОБАВЛЕНО: Блокировка, если игра окончена <<<---
    if g.game_over:
        flash("Игра уже окончена. Начать новый раунд нельзя.", "warning")
        return redirect(url_for('admin'))
    # --->>> КОНЕЦ БЛОКИРОВКИ <<<---
    
    db = get_db() # Получаем соединение из контекста g
    c = db.cursor()
    active_subfolder = get_setting('active_subfolder') # Получаем активную колоду

    try:
        # Получаем ID текущего (уже назначенного на этот раунд) ведущего
        current_leader_id = get_leading_user_id()
        if current_leader_id:
            leader_name = get_user_name(current_leader_id) or f"ID {current_leader_id}"
            flash(f"Новый раунд начат. Ведущий: {leader_name}.", "info")
        else:
            flash("Новый раунд начат. Ведущий не определен.", "warning")

        # Сбрасываем guesses и owner_id у карт, которые были 'На столе'.
        # Статус этих карт меняем на 'Занято:Админ', чтобы они не участвовали в следующей раздаче.
        c.execute("SELECT id FROM images WHERE status = 'На столе'") # ID карт на столе
        cards_on_table_ids = [row['id'] for row in c.fetchall()]
        table_cleared_count = 0
        if cards_on_table_ids:
            new_status = 'Занято:Админ'
            placeholders = ','.join('?' * len(cards_on_table_ids))
            c.execute(f"UPDATE images SET status = ?, owner_id = NULL, guesses = '{{}}' WHERE id IN ({placeholders})",
                      [new_status] + cards_on_table_ids)
            table_cleared_count = len(cards_on_table_ids)

        # --->>> ИСПРАВЛЕНИЕ ЗДЕСЬ <<<---
        # Сбрасываем guesses у карт, которые НЕ были на столе и у которых guesses НЕ пустой ('{}')
        # Передаем только ОДИН аргумент для ОДНОГО плейсхолдера '?'
        c.execute("UPDATE images SET guesses = '{}' WHERE status != ? AND guesses != '{}'", ('На столе',))
        # --->>> КОНЕЦ ИСПРАВЛЕНИЯ <<<---
        guesses_cleared_count = c.rowcount

        # Скрываем информацию о картах
        set_setting("show_card_info", "false")
        flash("Информация о картах скрыта.", "info")
        if table_cleared_count > 0:
            flash(f"Карты со стола ({table_cleared_count} шт.) убраны (статус 'Занято:Админ').", "info")
        if guesses_cleared_count > 0:
            flash(f"Сброшены прочие предположения ({guesses_cleared_count} карт).", "info")

        # Раздаем по одной новой карте каждому пользователю из активной колоды
        c.execute("SELECT id FROM users ORDER BY id")
        user_ids = [row['id'] for row in c.fetchall()]

        if not user_ids:
            flash("Нет пользователей для раздачи карт.", "warning")
        elif not active_subfolder:
            flash("Активная колода не установлена. Новые карты не розданы.", "warning")
        else:
            c.execute("SELECT id FROM images WHERE subfolder = ? AND status = 'Свободно'", (active_subfolder,))
            available_cards_ids = [row['id'] for row in c.fetchall()]
            random.shuffle(available_cards_ids)

            num_users = len(user_ids)
            num_available = len(available_cards_ids)
            num_to_deal = min(num_users, num_available)

            if num_available < num_users:
                flash(f"Внимание: Недостаточно свободных карт ({num_available}) для всех ({num_users}). Карты получат первые {num_available}.", "warning")

            for i in range(num_to_deal):
                user_id = user_ids[i]
                card_id = available_cards_ids[i]
                c.execute("UPDATE images SET status = ? WHERE id = ?", (f"Занято:{user_id}", card_id))

            if num_to_deal > 0:
                flash(f"Роздано {num_to_deal} новых карт из '{active_subfolder}'.", "info")
            elif num_users > 0:
                flash(f"Нет свободных карт в колоде '{active_subfolder}' для раздачи.", "warning")

        # --->>> ДОБАВЛЕНО: Проверка окончания игры ПОСЛЕ раздачи <<<---
        game_over_now = False
        if user_ids: # Проверяем только если есть пользователи
            for user_id in user_ids:
                c.execute("SELECT COUNT(*) FROM images WHERE status = ?", (f"Занято:{user_id}",))
                card_count = c.fetchone()[0]
                if card_count == 0:
                    game_over_now = True
                    break # Достаточно одного игрока без карт

        if game_over_now:
            set_game_over(True) # Устанавливаем флаг в БД
            g.game_over = True # Обновляем и в контексте текущего запроса
            flash("Игра окончена! У одного из игроков закончились карты.", "danger")
        # --->>> КОНЕЦ ПРОВЕРКИ <<<---
        
        # Коммитим все изменения нового раунда
        db.commit()

    except sqlite3.Error as e:
        db.rollback()
        flash(f"Ошибка базы данных при начале нового раунда: {e}", "danger") # Ошибка будет показана здесь
    except Exception as e:
        db.rollback()
        flash(f"Непредвиденная ошибка при начале нового раунда: {e}", "danger")

    return redirect(url_for('admin'))


# --- Запуск приложения ---
if __name__ == "__main__":
    # Проверка и инициализация БД при первом запуске
    if not os.path.exists(DB_PATH):
        print("База данных не найдена. Инициализация...")
        init_db()
        print("База данных инициализирована.")
    else:
        print("База данных найдена.")
        # Опционально: проверка и установка настроек по умолчанию, если их нет
        if get_setting('active_subfolder') is None:
             # Попробуем установить первую папку как активную
             db_conn_check = sqlite3.connect(DB_PATH)
             cursor_check = db_conn_check.cursor()
             cursor_check.execute("SELECT DISTINCT subfolder FROM images ORDER BY subfolder LIMIT 1")
             first_folder = cursor_check.fetchone()
             db_conn_check.close()
             default_folder = first_folder[0] if first_folder else 'koloda1'
             set_setting('active_subfolder', default_folder) # Используем get_db() внутри
             print(f"Установлена активная колода по умолчанию: {default_folder}")

        if get_setting('show_card_info') is None:
             set_setting('show_card_info', 'false')
             print("Установлена настройка show_card_info по умолчанию: false")

    # Параметры запуска Flask
    port = int(os.environ.get("PORT", 5000))
    # debug=True удобен для разработки, но ВЫКЛЮЧИТЕ его для production
    # В режиме debug сервер обычно перезапускается при изменении кода, что может влиять на соединения
    # debug=False рекомендуется для стабильной работы с БД
    debug_mode = os.environ.get("FLASK_DEBUG", "False").lower() in ['true', '1', 't']
    print(f"Запуск Flask приложения на порту {port} с debug={debug_mode}")
    # threaded=True может усилить проблемы с блокировкой SQLite, лучше оставить False (по умолчанию)
    app.run(host="0.0.0.0", port=port, debug=debug_mode)
