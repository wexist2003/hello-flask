import json
from flask import Flask, render_template, request, redirect, url_for, g, flash
import sqlite3
import os
import string
import random

app = Flask(__name__)
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
    get_leading_user_id=get_leading_user_id # Передаем функцию в шаблон
    # g доступен в шаблонах по умолчанию, но можно передать явно: g=g
)

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

@app.route("/")
def index():
    # Простой пример, можно сделать редирект на админку или страницу входа
    return "<h1>Имаджинариум</h1><p><a href='/admin'>Перейти в админку</a></p>"

@app.route("/admin", methods=["GET", "POST"])
def admin():
    db = get_db() # Используем соединение из g
    c = db.cursor()
    leader_to_display = None
    current_active_subfolder = '' # Инициализируем defaults
    show_card_info = False
    # free_image_count и image_owners будут рассчитаны ниже для GET

    # --- Читаем начальные настройки ---
    try:
        # Получаем ID текущего (уже назначенного на следующий раунд) лидера
        current_actual_leader_id = get_leading_user_id()
        current_active_subfolder = get_setting('active_subfolder') or '' # Получаем АКТИВНУЮ папку
        show_card_info = get_setting('show_card_info') == "true"

        # Определяем, кого показывать как "Ведущий" на этой странице (для GET запроса)
        displayed_leader_id_from_url_str = request.args.get('displayed_leader_id')
        if displayed_leader_id_from_url_str:
            try:
                leader_to_display = int(displayed_leader_id_from_url_str)
            except (ValueError, TypeError):
                leader_to_display = current_actual_leader_id # Fallback
        else:
            leader_to_display = current_actual_leader_id

    except Exception as e: # Ловим более общие ошибки чтения настроек
        flash(f"Критическая ошибка чтения начальных настроек: {e}", "danger")
        # Важно: Не пытаемся дальше работать с БД, если настройки не прочитались
        # Соединение закроется автоматически через teardown_appcontext
        return render_template("admin.html", users=[], images=[], subfolders=['koloda1', 'koloda2'],
                               active_subfolder='', guess_counts_by_user={}, all_guesses={},
                               show_card_info=False, leader_to_display=None,
                               free_image_count=0, image_owners={}) # Передаем пустые значения

    # --- Обработка POST запросов ---
    if request.method == "POST":
        action_handled = False # Флаг, что действие POST было обработано
        leader_for_redirect = leader_to_display # По умолчанию редирект показывает того же, кого показывали до POST

        try:
            if "name" in request.form:
                # --- Создание пользователя ---
                name = request.form.get("name", "").strip()
                user_created_success = False # Флаг успешного создания
                if not name:
                     flash("Имя пользователя не может быть пустым.", "warning")
                else:
                    num_cards = int(request.form.get("num_cards", 3))
                    if num_cards < 1: num_cards = 1
                    code = generate_unique_code()
                    # Проверка уникальности имени перед вставкой
                    c.execute("SELECT 1 FROM users WHERE name = ?", (name,))
                    if c.fetchone():
                        flash(f"Имя пользователя '{name}' уже существует.", "danger")
                    else:
                        c.execute("INSERT INTO users (name, code) VALUES (?, ?)", (name, code))
                        user_id = c.lastrowid
                        flash(f"Пользователь '{name}' добавлен.", "success")
                        user_created_success = True # Пользователь успешно создан

                        # Назначение Ведущего, если его нет
                        if current_actual_leader_id is None:
                            set_leading_user_id(user_id)
                            flash(f"Пользователь '{name}' назначен Ведущим.", "info")
                            current_actual_leader_id = user_id # Обновляем текущее значение
                            if leader_to_display is None: # Обновляем и отображаемого, если он не был задан
                                leader_to_display = current_actual_leader_id

                        # Назначение карт новому пользователю
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
                     db.commit() # Коммитим создание пользователя и назначение карт ТОЛЬКО если успех
                     action_handled = True
                     # При добавлении пользователя лидер не меняется, редирект покажет текущего
                     leader_for_redirect = current_actual_leader_id


            elif "active_subfolder" in request.form:
                # --- Смена активной колоды ---
                selected = request.form.get("active_subfolder")
                if set_setting('active_subfolder', selected): # Сохраняем настройку (set_setting делает commit)
                    try:
                        # Помечаем свободные карты в НЕАКТИВНЫХ папках как Занято:Админ
                        updated_inactive = c.execute("UPDATE images SET status = 'Занято:Админ' WHERE subfolder != ? AND status = 'Свободно'", (selected,)).rowcount
                        # ЯВНО КОММИТИМ ИЗМЕНЕНИЯ СТАТУСОВ КАРТ
                        db.commit()

                        flash_message_text = f"Выбрана активная колода: {selected}."
                        if updated_inactive > 0:
                            flash_message_text += f" Карты в других колодах ({updated_inactive} шт.) помечены как неактивные."
                        flash(flash_message_text, "success") # Используем локальную переменную
                        # Обновляем current_active_subfolder для текущего запроса
                        current_active_subfolder = selected

                    except sqlite3.Error as e:
                        db.rollback() # Откатываем изменения статусов при ошибке
                        flash(f"Ошибка обновления статусов карт: {e}", "danger")
                else:
                    flash("Ошибка сохранения настройки активной колоды.", "danger")

                # Лидер не меняется, редирект покажет текущего (или того, кто был в URL)
                leader_for_redirect = leader_to_display # Сохраняем того, кто отображался до POST
                action_handled = True

            elif "delete_user_id" in request.form:
                # --- Удаление пользователя ---
                user_id_to_delete = int(request.form.get("delete_user_id"))
                was_leader = (current_actual_leader_id == user_id_to_delete)

                c.execute("SELECT name FROM users WHERE id = ?", (user_id_to_delete,))
                user_to_delete = c.fetchone()

                if user_to_delete:
                    user_name_deleted = user_to_delete['name']
                    # === Выполняем удаление пользователя и связанных данных ===
                    c.execute("DELETE FROM users WHERE id = ?", (user_id_to_delete,))
                    c.execute("UPDATE images SET status = 'Свободно' WHERE status = ?", (f"Занято:{user_id_to_delete}",))
                    c.execute("UPDATE images SET status = 'Свободно', owner_id = NULL, guesses = '{}' WHERE owner_id = ?", (user_id_to_delete,))
                    flash(f"Пользователь '{user_name_deleted}' удален.", "success")

                    # === Логика переназначения Ведущего ===
                    new_leader_id_after_delete = current_actual_leader_id # По умолчанию оставляем старого, если удалили НЕ лидера
                    if was_leader:
                        c.execute("SELECT id FROM users ORDER BY id")
                        remaining_users = c.fetchall()
                        if remaining_users:
                            new_leader_id_after_delete = remaining_users[0]['id']
                            set_leading_user_id(new_leader_id_after_delete)
                            new_leader_name = get_user_name(new_leader_id_after_delete) or f"ID {new_leader_id_after_delete}"
                            flash(f"Удаленный пользователь был Ведущим. Новый Ведущий: {new_leader_name}.", "info")
                        else:
                            new_leader_id_after_delete = None
                            set_leading_user_id(None)
                            flash("Удаленный пользователь был Ведущим. Пользователей не осталось.", "warning")
                        # Устанавливаем ID для редиректа
                        leader_for_redirect = new_leader_id_after_delete
                    else:
                        # Если удалили НЕ лидера, редирект покажет того же лидера, что и был
                         leader_for_redirect = current_actual_leader_id

                    db.commit() # Коммитим удаление и возможное изменение ведущего
                else:
                    flash(f"Пользователь с ID {user_id_to_delete} не найден.", "danger")
                    # Оставляем leader_for_redirect как был (обычно текущий лидер)
                    leader_for_redirect = leader_to_display

                action_handled = True

            # --->>> ИСПРАВЛЕННЫЙ РЕДИРЕКТ <<<---
            if action_handled:
                # Используем leader_for_redirect, который был определен ВНУТРИ блока обработки действия
                return redirect(url_for('admin', displayed_leader_id=leader_for_redirect))
            # --->>> КОНЕЦ ИСПРАВЛЕННОГО РЕДИРЕКТА <<<---

        # Блоки except остаются без изменений, обрабатывают ошибки и откатывают транзакции
        except sqlite3.IntegrityError as e:
             if "UNIQUE constraint failed" not in str(e):
                 flash(f"Ошибка целостности базы данных: {e}", "danger")
             db.rollback()
        except (sqlite3.Error, ValueError, TypeError) as e:
             flash(f"Ошибка при обработке запроса: {e}", "danger")
             db.rollback()
        except Exception as e:
              flash(f"Произошла непредвиденная ошибка: {e}", "danger")
              db.rollback()
        # Если была ошибка в POST и не было редиректа, продолжаем на рендеринг GET

    # --- Получение данных для отображения (GET request или после ошибки POST) ---
    # Переменные current_active_subfolder и leader_to_display уже установлены
    users, images, subfolders, guess_counts_by_user, all_guesses = [], [], [], {}, {}
    free_image_count = 0 # Инициализируем счетчик для GET
    image_owners = {}    # Инициализируем словарь владельцев
    try:
        # --->>> FETCH USERS HERE <<<---
        c.execute("SELECT id, name, code, rating FROM users ORDER BY name ASC")
        users = c.fetchall() # Assigns fetched data to 'users'
        print(f"Admin GET: Fetched {len(users)} users.") # Отладка

        # --->>> FETCH IMAGES HERE <<<---
        c.execute("SELECT id, subfolder, image, status, owner_id, guesses FROM images ORDER BY subfolder, id")
        images_rows = c.fetchall()
        images = []
        all_guesses = {}
        # current_active_subfolder был прочитан в начале функции
        print(f"Admin GET: Fetched {len(images_rows)} image rows. Active subfolder: '{current_active_subfolder}'") # Отладка

        for img_row in images_rows:
            # Парсинг JSON guesses
            guesses_json_str = img_row['guesses'] or '{}'
            try:
                 guesses_dict = json.loads(guesses_json_str)
            except json.JSONDecodeError as json_e:
                 print(f"Warning: JSONDecodeError for image ID {img_row['id']} - guesses: '{guesses_json_str}'. Error: {json_e}. Using empty dict.")
                 guesses_dict = {} # Используем пустой словарь при ошибке парсинга

            img_dict = dict(img_row) # Преобразуем Row в dict
            img_dict['guesses'] = guesses_dict
            images.append(img_dict)

            # Заполняем словарь владельцев карт, которые есть на столе
            if img_dict['owner_id'] is not None:
                image_owners[img_dict['id']] = img_dict['owner_id']

            # ИСПРАВЛЕННЫЙ ПОДСЧЕТ СВОБОДНЫХ КАРТ
            if img_dict['status'] == 'Свободно' and img_dict['subfolder'] == current_active_subfolder:
                free_image_count += 1

            if guesses_dict:
                 all_guesses[img_row['id']] = guesses_dict

        print(f"Admin GET: Processed images. Free count in active folder: {free_image_count}") # Отладка

        # Подсчет сделанных предположений каждым пользователем
        guess_counts_by_user = {user['id']: 0 for user in users}
        for img_id, guesses_for_image in all_guesses.items():
            for guesser_id_str in guesses_for_image:
                 try:
                     if int(guesser_id_str) in guess_counts_by_user:
                         guess_counts_by_user[int(guesser_id_str)] += 1
                 except (ValueError, TypeError): pass # Игнорируем невалидные ID угадывающих

        print(f"Admin GET: Calculated guess counts.") # Отладка

        # Получение списка папок
        c.execute("SELECT DISTINCT subfolder FROM images ORDER BY subfolder") # Добавил сортировку
        subfolders = [row['subfolder'] for row in c.fetchall()] or ['koloda1', 'koloda2'] # Запасной вариант
        print(f"Admin GET: Found subfolders: {subfolders}") # Отладка

    # --->>> ОБНОВЛЕННЫЙ БЛОК EXCEPT С ЛОГИРОВАНИЕМ <<<---
    except sqlite3.Error as e:
        # --- Улучшенное логирование ошибки ---
        print(f"!!! ERROR caught in admin GET data fetch: {e}")
        # Логируем состояние переменных ПЕРЕД их сбросом
        print(f"!!! State before reset: users length={len(users)}, images length={len(images)}, subfolders={len(subfolders)}")
        # Показываем flash сообщение пользователю
        flash(f"Ошибка чтения данных для отображения: {e}", "danger")
        # --- Конец улучшенного логирования ---

        # Сбрасываем все данные в пустые списки/словари при ошибке
        users, images, subfolders, guess_counts_by_user, all_guesses = [], [], [], {}, {}
        free_image_count = 0
        image_owners = {}
    # --->>> КОНЕЦ ОБНОВЛЕННОГО БЛОКА EXCEPT <<<---

    except Exception as e: # Ловим другие возможные ошибки (напр. JSONDecodeError, если не обработан выше)
         print(f"!!! UNEXPECTED ERROR caught in admin GET data fetch: {e}")
         flash(f"Непредвиденная ошибка при чтении данных: {e}", "danger")
         # Сбрасываем все данные при любой ошибке
         users, images, subfolders, guess_counts_by_user, all_guesses = [], [], [], {}, {}
         free_image_count = 0
         image_owners = {}


    # Рендеринг шаблона с полученными данными
    # Соединение закроется автоматически через teardown_appcontext
    print(f"Admin GET: Rendering template. Users count: {len(users)}") # Отладка перед рендерингом
    return render_template("admin.html", users=users, images=images,
                           subfolders=subfolders, active_subfolder=current_active_subfolder,
                           guess_counts_by_user=guess_counts_by_user, all_guesses=all_guesses,
                           show_card_info=show_card_info,
                           leader_to_display=leader_to_display, # Отображаемый лидер (может быть из URL)
                           free_image_count=free_image_count,
                           image_owners=image_owners) # Передаем словарь владельцев
    

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
    # Проверка на конец игры (сохранена из оригинала)
    if hasattr(g, 'game_over') and g.game_over: # Добавлена проверка наличия g.game_over
        flash("Игра окончена. Подсчет очков невозможен.", "warning")
        return redirect(url_for('admin')) # Предполагаем, что 'admin' - это ваш маршрут

    db = get_db()
    c = db.cursor()
    leader_just_finished = get_leading_user_id() # ID ведущего, чей раунд закончился

    # Флаг для Правила 1 (остановка подсчета).
    stop_processing = False
    # Список для сводки очков
    points_summary = []

    try:
        # Показываем информацию о картах (сохранено из оригинала)
        if not set_setting("show_card_info", "true"):
            flash("Не удалось обновить настройку видимости карт.", "warning")
            # Продолжаем выполнение

        # --- Проверка и установка лидера, если не был установлен (сохранено из оригинала) ---
        if leader_just_finished is None:
            c.execute("SELECT id FROM users ORDER BY id LIMIT 1")
            first_user = c.fetchone()
            if first_user:
                leader_just_finished = first_user['id']
                # Проверяем успешность установки нового лидера
                if not set_leading_user_id(leader_just_finished):
                     flash(f"Не удалось установить первого ведущего (ID: {leader_just_finished}).", "danger")
                     db.rollback() # Откат, т.к. не можем продолжить
                     return redirect(url_for("admin"))
                flash(f"Ведущий не был установлен. Назначен: {get_user_name(leader_just_finished)}.", "info")
            else:
                flash("Нет пользователей для подсчета очков.", "warning")
                # Нет смысла продолжать, если нет пользователей
                return redirect(url_for("admin"))

        # --- Подсчет очков (ЗАМЕНЕННЫЙ БЛОК) ---
        c.execute("SELECT id, owner_id, guesses FROM images WHERE owner_id IS NOT NULL")
        table_images = c.fetchall()

        c.execute("SELECT id FROM users")
        # Убеждаемся, что ID пользователей - целые числа
        all_user_ids = [int(user['id']) for user in c.fetchall()]
        num_all_users = len(all_user_ids)

        # Очки за текущий раунд (оригинальное название)
        user_points = {user_id: 0 for user_id in all_user_ids}

        # --- Проверка Ведущего на активность ---
        if leader_just_finished not in user_points and leader_just_finished is not None:
            flash(f"Ведущий (ID: {leader_just_finished}) не найден среди текущих пользователей. Подсчет очков может быть неполным.", "warning")
            # Тем не менее, продолжаем подсчет с доступными данными

        print("--- Начисление очков ---") # Отладка

        # --- Итерация по картам ---
        for image_data in table_images:
            if stop_processing: # Проверка флага остановки (Правило 1)
                print("  Обработка карт прервана из-за выполнения Правила 1.")
                break # Выход из цикла обработки карт

            owner_id = image_data['owner_id']
            image_id = image_data['id']
            guesses_json_str = image_data['guesses'] or '{}'

            # Безопасное преобразование JSON
            try: guesses = json.loads(guesses_json_str)
            except json.JSONDecodeError:
                print(f"  ПРЕДУПРЕЖДЕНИЕ: Некорректный JSON в guesses для карты {image_id}. Угадывания пропущены.")
                guesses = {}

            # Убедимся, что owner_id - целое число
            try: owner_id = int(owner_id)
            except (ValueError, TypeError):
                print(f"  ПРЕДУПРЕЖДЕНИЕ: Некорректный owner_id ({owner_id}) для карты {image_id}. Карта пропущена.")
                continue

            # Пропуск карты, если владелец неактивен/не найден
            if owner_id not in user_points:
                print(f"  ИНФО: Владелец {owner_id} карты {image_id} неактивен. Карта пропущена.")
                continue

            print(f"\n  Обработка карты {image_id} (Владелец: {owner_id})")
            correct_guesses_count = 0
            # Список для хранения ID угадавших эту карту (нужен для Правила 1)
            correct_guesser_ids = []

            # --- Первый проход: Обработка угадываний (Начисление +3 только за Ведущего) ---
            for guesser_id_str, guessed_user_id in guesses.items():
                try:
                    guesser_id = int(guesser_id_str)
                    try: guessed_user_id = int(guessed_user_id)
                    except (ValueError, TypeError):
                        print(f"    ПРЕДУПРЕЖДЕНИЕ: Некорректный guessed_user_id ({guessed_user_id}) от {guesser_id}. Угадывание пропущено.")
                        continue

                    if guesser_id in user_points and guesser_id != owner_id:
                        if guessed_user_id == owner_id: # Угадывание верное
                            correct_guesses_count += 1
                            correct_guesser_ids.append(guesser_id)

                            # --- ИЗМЕНЕНИЕ: +3 только за угадывание Ведущего (Правило 4) ---
                            if owner_id == leader_just_finished:
                                user_points[guesser_id] += 3
                                print(f"    Игрок {guesser_id} угадал карту ВЕДУЩЕГО ({owner_id}) --> +3")
                            else:
                                # Правило 3: За угадывание НЕ Ведущего угадавший получает +0
                                print(f"    Игрок {guesser_id} угадал карту НЕ Ведущего ({owner_id}) --> +0")

                except (ValueError, TypeError):
                    print(f"    ПРЕДУПРЕЖДЕНИЕ: Некорректный guesser_id_str '{guesser_id_str}'. Угадывание пропущено.")
                    continue

            # --- Второй проход: Очки владельцу карты (и спец. правила Ведущего) ---
            num_potential_guessers = num_all_users - 1 if num_all_users > 1 else 0

            # --->>> ЛОГИКА КАРТЫ ВЕДУЩЕГО (с изменениями) <<<---
            if owner_id == leader_just_finished:
                print(f"    --- Обработка очков ВЕДУЩЕГО {owner_id} ---")
                print(f"      Правильных угадываний: {correct_guesses_count}, Потенциальных угадывающих: {num_potential_guessers}")

                if num_potential_guessers > 0:
                    if correct_guesses_count == num_potential_guessers:
                        # --- Правило 1: Все угадали Ведущего ---
                        user_points[owner_id] -= 3
                        print(f"      Все ({correct_guesses_count}) угадали. Ведущий {owner_id} --> -3.")
                        # Отмена +3 у угадавших
                        print(f"      Отмена +3 у угадавших:")
                        for guesser_id_rule1 in correct_guesser_ids:
                            if guesser_id_rule1 in user_points:
                                user_points[guesser_id_rule1] -= 3 # Отменяем ранее начисленные +3
                                print(f"        Игрок {guesser_id_rule1}: -3 (общий итог за угадывание: 0)")
                        # Установка флага для остановки
                        stop_processing = True
                        print("      !!! Начисление очков ОСТАНОВЛЕНО (Правило 1) !!!")

                    elif correct_guesses_count == 0:
                        # --- Правило 2: Никто не угадал Ведущего ---
                        user_points[owner_id] -= 2
                        print(f"      Никто не угадал. Ведущий {owner_id} --> -2.")
                    else: # 0 < correct_guesses_count < num_potential_guessers
                        # --- Правило 5: Некоторые угадали Ведущего ---
                        points_for_leader = 3 + correct_guesses_count # База +1 за каждого
                        user_points[owner_id] += points_for_leader
                        print(f"      {correct_guesses_count} угадали (не все). Ведущий {owner_id} --> +3 + {correct_guesses_count} = +{points_for_leader}.")
                        # Угадавшие сохраняют свои +3 (полученные ранее по Правилу 4)
                else: # Нет потенциальных угадывающих
                    # Считаем как Правило 2
                    user_points[owner_id] -= 2
                    print(f"      Нет потенциальных угадывающих. Ведущий {owner_id} --> -2.")
            # --->>> КОНЕЦ ЛОГИКИ КАРТЫ ВЕДУЩЕГО <<<---
            else: # Карта принадлежит НЕ Ведущему
                # --- Правило 3: Очки владельцу карты НЕ Ведущего ---
                if correct_guesses_count > 0:
                    user_points[owner_id] += correct_guesses_count # +1 за каждого угадавшего
                    print(f"    Карта НЕ Ведущего {owner_id}: Владелец --> +{correct_guesses_count}.")
                # else: # Никто не угадал, владелец +0
                #     print(f"    Карта НЕ Ведущего {owner_id}: Никто не угадал, владелец --> +0.")
        # --- Конец цикла по картам ---

        # --- Обновление рейтинга (УЛУЧШЕННЫЙ БЛОК) ---
        print("\n--- Обновление рейтинга ---")

        if stop_processing:
            print("  Обновление рейтинга пропущено, так как подсчет был остановлен по Правилу 1.")
            flash("Подсчет очков остановлен: все угадали карту Ведущего. Очки за раунд не сохранены.", "info")
            # Очки не сохраняем, но нужно определить следующего лидера
        else:
            # Обновляем рейтинг только если обработка НЕ была остановлена
            for user_id, points in user_points.items():
                if points != 0:
                    try:
                        # Получение имени пользователя (замените на реальную функцию)
                        user_name = f"ID {user_id}" # Значение по умолчанию
                        try: fetched_name = get_user_name(user_id)
                        except NameError: fetched_name = None # get_user_name не определена
                        except Exception as name_err:
                             print(f"  (Ошибка при получении имени для {user_id}: {name_err})")
                             fetched_name = None
                        if fetched_name: user_name = fetched_name

                        print(f"  Обновление пользователя {user_id} ({user_name}): {points:+}")
                        c.execute("UPDATE users SET rating = rating + ? WHERE id = ?", (points, user_id))
                        points_summary.append(f"{user_name}: {points:+}") # Формат с + или -

                    except sqlite3.Error as e:
                        print(f"!!! ОШИБКА обновления рейтинга для пользователя {user_id}: {e}")
                        flash(f"Ошибка обновления рейтинга для пользователя ID {user_id}", "danger")
                        db.rollback() # Откатываем транзакцию
                        print("  !!! Транзакция отменена из-за ошибки обновления рейтинга !!!")
                        return redirect(url_for("admin")) # Прерываем и перенаправляем

        # --- Определение и сохранение СЛЕДУЮЩЕГО ведущего (сохранено из оригинала, с улучшенным порядком) ---
        next_leading_user_id = None
        # Получаем актуальный список ID пользователей на случай, если кто-то удалился
        try:
            c.execute("SELECT id FROM users ORDER BY id") # Получаем ID в порядке возрастания
            user_ids_ordered = [int(row['id']) for row in c.fetchall()]
        except sqlite3.Error as e:
            print(f"Error getting user IDs for next leader selection: {e}")
            flash("Ошибка при получении списка пользователей для определения следующего ведущего.", "danger")
            db.rollback()
            return redirect(url_for("admin"))

        if not user_ids_ordered:
             flash("Нет пользователей для определения следующего ведущего.", "warning")
             set_leading_user_id(None) # Устанавливаем None
        elif leader_just_finished is not None and leader_just_finished in user_ids_ordered:
             try:
                 current_index = user_ids_ordered.index(leader_just_finished)
                 next_index = (current_index + 1) % len(user_ids_ordered)
                 next_leading_user_id = user_ids_ordered[next_index]
             except ValueError: # На всякий случай, если лидер был удален между проверками
                 print(f"Предупреждение: ID ведущего {leader_just_finished} не найден в упорядоченном списке.")
                 next_leading_user_id = user_ids_ordered[0] # Берем первого
        elif user_ids_ordered: # Если лидера не было или он удален, берем первого из списка
             next_leading_user_id = user_ids_ordered[0]

        # Сохраняем нового лидера
        if next_leading_user_id is not None:
            if set_leading_user_id(next_leading_user_id):
                next_leader_name = get_user_name(next_leading_user_id) or f"ID {next_leading_user_id}"
                # Сообщение об успехе зависит от того, сохраняли ли мы очки
                if not stop_processing:
                     flash(f"Подсчет очков завершен. Следующий ведущий: {next_leader_name}.", "success")
                else:
                     flash(f"Раунд завершен (очки не сохранены). Следующий ведущий: {next_leader_name}.", "info")
            else:
                 flash("Критическая ошибка: не удалось сохранить нового ведущего.", "danger")
                 db.rollback()
                 return redirect(url_for("admin")) # Прерываем, если не удалось сохранить лидера
        else:
             # Эта ветка не должна достигаться при наличии user_ids_ordered, но на всякий случай
             flash("Не удалось определить следующего ведущего.", "warning")
             set_leading_user_id(None)

        # Показываем сводку очков (если они начислялись)
        if points_summary and not stop_processing:
            flash(f"Изменение очков: {'; '.join(points_summary)}", "info")
        elif not stop_processing:
             flash("В этом раунде очки не изменились.", "info")
        # Если stop_processing, сводку не показываем (уже было сообщение)

        # --- Коммит всех изменений ---
        # Коммитим только если не было ошибок и отката ранее
        db.commit()
        print("--- Подсчет очков и обновление завершены успешно ---")

    except sqlite3.Error as e:
        db.rollback() # Откат при ошибке БД в процессе подсчета/установки лидера
        flash(f"Ошибка базы данных при подсчете очков: {e}", "danger")
        print(f"Database error in open_cards: {e}")
        import traceback
        print(traceback.format_exc()) # Печать стека вызовов для отладки
        # Перенаправляем на админку после ошибки
        return redirect(url_for("admin"))
    except Exception as e:
        db.rollback() # Откат при других непредвиденных ошибках
        flash(f"Непредвиденная ошибка при подсчете очков: {type(e).__name__}", "danger")
        print(f"Unexpected error in open_cards: {e}")
        import traceback
        print(traceback.format_exc()) # Печать стека вызовов для отладки
         # Перенаправляем на админку после ошибки
        return redirect(url_for("admin"))

    # Перенаправляем на админку в случае успеха
    # Передаем ID ведущего, чей раунд ТОЛЬКО ЧТО ЗАКОНЧИЛСЯ (для возможного отображения)
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
