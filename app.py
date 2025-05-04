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
    return "<h1>Игра (Прототип)</h1><p><a href='/admin'>Перейти в админку</a></p>"

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

        # Определяем, кого показывать как "Ведущий" на этой странице
        # (может быть предыдущий лидер, если перешли с open_cards)
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
        return render_template("admin.html", users=[], images=[], subfolders=['koloda1', 'koloda2'],
                               active_subfolder='', guess_counts_by_user={}, all_guesses={},
                               show_card_info=False, leader_to_display=None,
                               free_image_count=0, image_owners={}) # Передаем пустые значения

    # --- Обработка POST запросов ---
    if request.method == "POST":
        action_handled = False # Флаг, что действие POST было обработано
        try:
            if "name" in request.form:
                # --- Создание пользователя ---
                name = request.form.get("name", "").strip()
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

                        db.commit() # Коммитим создание пользователя и назначение карт
                        action_handled = True

            elif "active_subfolder" in request.form:
                # --- Смена активной колоды ---
                selected = request.form.get("active_subfolder")
                if set_setting('active_subfolder', selected): # Сохраняем настройку (set_setting делает commit)
                    try:
                        # Помечаем свободные карты в НЕАКТИВНЫХ папках как Занято:Админ
                        updated_inactive = c.execute("UPDATE images SET status = 'Занято:Админ' WHERE subfolder != ? AND status = 'Свободно'", (selected,)).rowcount
                        # !!! ЯВНО КОММИТИМ ИЗМЕНЕНИЯ СТАТУСОВ КАРТ !!!
                        db.commit()

                        flash_message = f"Выбрана активная колода: {selected}."
                        if updated_inactive > 0:
                            flash_message += f" Карты в других колодах ({updated_inactive} шт.) помечены как неактивные."
                        flash(flash_message, "success")
                        # Обновляем current_active_subfolder для текущего запроса, если понадобится дальше
                        current_active_subfolder = selected

                    except sqlite3.Error as e:
                        db.rollback() # Откатываем изменения статусов при ошибке
                        flash(f"Ошибка обновления статусов карт: {e}", "danger")
                else:
                    flash("Ошибка сохранения настройки активной колоды.", "danger")
                action_handled = True # Помечаем, что действие обработано для редиректа

            elif "delete_user_id" in request.form:
                # --- Удаление пользователя ---
                user_id_to_delete = int(request.form.get("delete_user_id"))
                # Проверяем, был ли удаляемый пользователь ведущим ДО удаления
                # Используем current_actual_leader_id, который был прочитан в начале функции
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

                    # === Логика переназначения Ведущего (если удалили ведущего) ===
                    new_leader_id_after_delete = None # Определим нового лидера
                    if was_leader:
                        # Получаем список ОСТАВШИХСЯ пользователей, упорядоченный по ID
                        c.execute("SELECT id FROM users ORDER BY id")
                        remaining_users = c.fetchall()

                        if remaining_users:
                            # Назначаем первого из оставшихся новым ведущим (простая стратегия)
                            new_leader_id_after_delete = remaining_users[0]['id']
                            set_leading_user_id(new_leader_id_after_delete) # Сохраняем в БД
                            new_leader_name = get_user_name(new_leader_id_after_delete) or f"ID {new_leader_id_after_delete}"
                            flash(f"Удаленный пользователь был Ведущим. Новый Ведущий: {new_leader_name}.", "info")
                        else:
                            # Пользователей не осталось
                            set_leading_user_id(None) # Сбрасываем ведущего в БД
                            flash("Удаленный пользователь был Ведущим. Пользователей не осталось.", "warning")

                        # Обновляем переменные для текущего запроса (на случай, если они нужны до редиректа)
                        current_actual_leader_id = new_leader_id_after_delete
                        # Если отображался удаленный лидер, обновляем и его
                        if leader_to_display == user_id_to_delete:
                             leader_to_display = new_leader_id_after_delete

                    db.commit() # Коммитим удаление и возможное изменение ведущего
                else:
                    flash(f"Пользователь с ID {user_id_to_delete} не найден.", "danger")
                action_handled = True # Помечаем для редиректа

            # Если действие POST было успешно обработано, делаем редирект на GET
            if action_handled:
                # Обновляем leader_to_display перед редиректом, чтобы он был актуальным
                # Если was_leader и он изменился, leader_to_display уже обновлен выше
                # Если удалили НЕ лидера, leader_to_display остается прежним (если он был из URL) или current_actual_leader_id
                if not was_leader: # Если удалили НЕ лидера
                    if displayed_leader_id_from_url_str: # Если лидер пришел из URL
                         leader_to_display = int(displayed_leader_id_from_url_str) # Оставляем его
                    else: # Иначе показываем текущего актуального
                         leader_to_display = current_actual_leader_id

                # Передаем ID лидера, которого нужно показать ПОСЛЕ редиректа
                # Это может быть предыдущий (если перешли с open_cards) или текущий/новый
                return redirect(url_for('admin', displayed_leader_id=leader_to_display))

        except sqlite3.IntegrityError as e:
             # Обработка ошибки уникальности имени уже есть в блоке создания пользователя
             if "UNIQUE constraint failed" not in str(e): # Показываем другие ошибки целостности
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
        c.execute("SELECT id, name, code, rating FROM users ORDER BY name ASC")
        users = c.fetchall()

        c.execute("SELECT id, subfolder, image, status, owner_id, guesses FROM images ORDER BY subfolder, id")
        images_rows = c.fetchall()
        images = []
        all_guesses = {}
        for img_row in images_rows:
            guesses_json_str = img_row['guesses'] or '{}'
            try: guesses_dict = json.loads(guesses_json_str)
            except json.JSONDecodeError: guesses_dict = {}

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

        # Подсчет сделанных предположений каждым пользователем
        guess_counts_by_user = {user['id']: 0 for user in users}
        for img_id, guesses_for_image in all_guesses.items():
            for guesser_id_str in guesses_for_image:
                 try:
                     if int(guesser_id_str) in guess_counts_by_user:
                         guess_counts_by_user[int(guesser_id_str)] += 1
                 except (ValueError, TypeError): pass

        # Получение списка папок
        c.execute("SELECT DISTINCT subfolder FROM images ORDER BY subfolder") # Добавил сортировку
        subfolders = [row['subfolder'] for row in c.fetchall()] or ['koloda1', 'koloda2'] # Запасной вариант

    except sqlite3.Error as e:
        flash(f"Ошибка чтения данных для отображения: {e}", "danger")
        # Сбрасываем все данные при ошибке
        users, images, subfolders, guess_counts_by_user, all_guesses = [], [], [], {}, {}
        free_image_count = 0
        image_owners = {}

    # Рендеринг шаблона с полученными данными
    # Соединение закроется автоматически через teardown_appcontext
    return render_template("admin.html", users=users, images=images,
                           subfolders=subfolders, active_subfolder=current_active_subfolder,
                           guess_counts_by_user=guess_counts_by_user, all_guesses=all_guesses,
                           show_card_info=show_card_info,
                           leader_to_display=leader_to_display,
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
    if g.game_over: # Проверка на конец игры
        flash("Игра окончена. Подсчет очков невозможен.", "warning")
        return redirect(url_for('admin'))

    db = get_db()
    c = db.cursor()
    leader_just_finished = get_leading_user_id() # ID ведущего, чей раунд закончился

    try:
        # Показываем информацию о картах
        if not set_setting("show_card_info", "true"):
             flash("Не удалось обновить настройку видимости карт.", "warning")
             # Продолжаем выполнение, но видимость может не обновиться

        # --- Проверка и установка лидера, если не был установлен ---
        if leader_just_finished is None:
             c.execute("SELECT id FROM users ORDER BY id LIMIT 1")
             first_user = c.fetchone()
             if first_user:
                 leader_just_finished = first_user['id']
                 set_leading_user_id(leader_just_finished)
                 flash(f"Ведущий не был установлен. Назначен: {get_user_name(leader_just_finished)}.", "info")
             else:
                 flash("Нет пользователей для подсчета очков.", "warning")
                 return redirect(url_for("admin"))

        # --- Подсчет очков ---
        c.execute("SELECT id, owner_id, guesses FROM images WHERE owner_id IS NOT NULL")
        table_images = c.fetchall()
        c.execute("SELECT id FROM users")
        all_user_ids = [user['id'] for user in c.fetchall()]
        num_all_users = len(all_user_ids)
        user_points = {user_id: 0 for user_id in all_user_ids} # Очки за текущий раунд

        if leader_just_finished not in user_points and leader_just_finished is not None:
            flash(f"Ведущий (ID: {leader_just_finished}) не найден среди текущих пользователей. Подсчет очков может быть неполным.", "warning")

        print("--- Начисление очков ---") # Отладка
        for image_data in table_images:
            owner_id = image_data['owner_id']
            image_id = image_data['id'] # Получаем ID карты для отладки
            guesses_json_str = image_data['guesses'] or '{}'
            try: guesses = json.loads(guesses_json_str)
            except json.JSONDecodeError: guesses = {}

            # Пропускаем карты удаленных или неактивных владельцев
            if owner_id not in user_points: continue

            correct_guesses_count = 0
            # Сначала начисляем +3 всем правильно угадавшим (кроме владельца)
            for guesser_id_str, guessed_user_id in guesses.items():
                try:
                    guesser_id = int(guesser_id_str)
                    # Учитываем только угадывания от текущих пользователей, не являющихся владельцем
                    if guesser_id in user_points and guesser_id != owner_id:
                        if guessed_user_id == owner_id: # Правильное угадывание
                            correct_guesses_count += 1
                            user_points[guesser_id] += 3 # +3 за правильное угадывание
                            print(f"  Карта {image_id} (Владелец {owner_id}): Игрок {guesser_id} угадал верно (+3)")
                except (ValueError, TypeError): continue

            # Затем начисляем очки владельцу карты (и возможно другим по правилам ведущего)
            num_potential_guessers = num_all_users - 1 if num_all_users > 1 else 0 # Кол-во других игроков

            # --->>> ИЗМЕНЕННАЯ ЛОГИКА ПОДСЧЕТА ДЛЯ КАРТЫ ВЕДУЩЕГО <<<---
            if owner_id == leader_just_finished:
                print(f"  Обработка карты Ведущего {owner_id} (Карта ID {image_id}):")
                print(f"    Правильных угадываний: {correct_guesses_count}, Потенциальных угадывающих: {num_potential_guessers}")
                if num_potential_guessers > 0:
                    if correct_guesses_count == num_potential_guessers:
                        # Все угадали карту Ведущего
                        user_points[owner_id] -= 3 # Ведущий получает -3
                        print(f"    Все ({correct_guesses_count}) угадали. Ведущий {owner_id} получает -3.")
                        # Остальные не получают доп. очков по этому правилу (но +3 за угадывание остается)
                    elif correct_guesses_count == 0:
                        # Никто не угадал карту Ведущего
                        user_points[owner_id] -= 2 # Ведущий получает -2
                        print(f"    Никто не угадал. Ведущий {owner_id} получает -2.")
                        # Остальные не получают доп. очков по этому правилу
                    else: # 0 < correct_guesses_count < num_potential_guessers
                        # Некоторые (но не все) угадали карту Ведущего
                        user_points[owner_id] += 3 # Ведущий получает +3
                        print(f"    {correct_guesses_count} угадали (не все). Ведущий {owner_id} получает +3.")
                        # Правильно угадавшие уже получили +3
                else: # Если не было потенциальных угадывающих (напр. игра 1 на 1)
                    user_points[owner_id] -= 2 # Считаем как "никто не угадал"
                    print(f"    Нет потенциальных угадывающих. Ведущий {owner_id} получает -2.")
            # --->>> КОНЕЦ ИЗМЕНЕННОЙ ЛОГИКИ ДЛЯ ВЕДУЩЕГО <<<---
            else: # Если это карта НЕ Ведущего
                 # Владелец получает +1 за каждого, кто угадал его карту
                 user_points[owner_id] += correct_guesses_count
                 if correct_guesses_count > 0:
                     print(f"  Карта {image_id} (НЕ Ведущий {owner_id}): Владелец получает +{correct_guesses_count}.")
                 # Правильно угадавшие уже получили +3

        # --- Обновление рейтинга ---
        points_summary = []
        print("--- Обновление рейтинга ---")
        for user_id, points in user_points.items():
            if points != 0:
                 try:
                     c.execute("UPDATE users SET rating = rating + ? WHERE id = ?", (points, user_id))
                     user_name = get_user_name(user_id) or f"ID {user_id}"
                     points_summary.append(f"{user_name}: {points:+}") # Форматируем с + или -
                     print(f"  Пользователь {user_id} ({user_name}): {points:+}")
                 except sqlite3.Error as e:
                     print(f"Error updating rating for user {user_id}: {e}")
                     flash(f"Ошибка обновления рейтинга для пользователя ID {user_id}", "danger")
                     db.rollback() # Откатываем только если ошибка при обновлении рейтинга
                     # Решаем, стоит ли прерывать весь процесс или продолжить
                     return redirect(url_for("admin")) # Прерываем в случае ошибки

        # --- Определение и сохранение СЛЕДУЮЩЕГО ведущего ---
        next_leading_user_id = None
        if leader_just_finished is not None and all_user_ids:
            try:
                c.execute("SELECT id FROM users ORDER BY id") # Получаем ID в порядке возрастания
                user_ids_ordered = [row['id'] for row in c.fetchall()]
                if user_ids_ordered: # Убедимся, что пользователи есть
                    try:
                        current_index = user_ids_ordered.index(leader_just_finished)
                        next_index = (current_index + 1) % len(user_ids_ordered)
                        next_leading_user_id = user_ids_ordered[next_index]
                    except ValueError: # Если старый лидер удален
                        next_leading_user_id = user_ids_ordered[0] # Берем первого из оставшихся
            except sqlite3.Error as e:
                 print(f"Error getting user IDs for next leader selection: {e}")
                 flash("Ошибка при определении следующего ведущего.", "warning")

        elif all_user_ids: # Если лидера не было, но есть пользователи
            next_leading_user_id = all_user_ids[0]

        # Сохраняем нового лидера
        if next_leading_user_id is not None:
             if set_leading_user_id(next_leading_user_id):
                 next_leader_name = get_user_name(next_leading_user_id) or f"ID {next_leading_user_id}"
                 flash(f"Подсчет очков завершен. Следующий ведущий: {next_leader_name}.", "success")
             else:
                  flash("Ошибка сохранения нового ведущего.", "danger")
                  db.rollback()
                  return redirect(url_for("admin")) # Прерываем, если не удалось сохранить лидера
        else:
            flash("Не удалось определить следующего ведущего (возможно, нет пользователей).", "warning")
            set_leading_user_id(None) # Устанавливаем None, если не смогли определить

        # Показываем сводку очков
        if points_summary: flash(f"Изменение очков: {'; '.join(points_summary)}", "info")
        else: flash("В этом раунде очки не изменились.", "info")

        # Коммитим все изменения (рейтинги, настройки лидера)
        db.commit()
        print("--- Подсчет очков и обновление завершены успешно ---")

    except sqlite3.Error as e:
        db.rollback() # Откат при ошибке БД в процессе подсчета
        flash(f"Ошибка базы данных при подсчете очков: {e}", "danger")
    except Exception as e:
        db.rollback() # Откат при других ошибках
        flash(f"Непредвиденная ошибка при подсчете очков: {e}", "danger")
        print(f"Unexpected error in open_cards: {e}") # Логируем ошибку

    # Перенаправляем на админку, передавая ID ведущего, чей раунд ТОЛЬКО ЧТО ЗАКОНЧИЛСЯ
    # leader_just_finished содержит ID лидера до смены
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
