import json
from flask import Flask, render_template, request, redirect, url_for, g, flash, session
import sqlite3
import os
import string
import random
import traceback

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')
if not app.config['SECRET_KEY']:
    raise ValueError("Не установлена переменная окружения SECRET_KEY!")

DB_PATH = 'database.db'

# --- Управление соединением с БД ---
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=10)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(error=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

# --- Вспомогательные функции ---
def is_game_over():
    return get_setting('game_over') == 'true'

def set_game_over(state=True):
    return set_setting('game_over', 'true' if state else 'false')

def generate_unique_code(length=8):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def get_setting(key):
    try:
        db = get_db()
        c = db.cursor()
        c.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = c.fetchone()
        return row['value'] if row else None
    except sqlite3.Error as e:
        print(f"Database error in get_setting for key '{key}': {e}")
        return None

def set_setting(key, value):
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
    value = get_setting('leading_user_id')
    if value:
        try: return int(value)
        except (ValueError, TypeError): return None
    return None

def set_leading_user_id(user_id):
    value_to_set = str(user_id) if user_id is not None else ''
    return set_setting('leading_user_id', value_to_set)

def get_user_name(user_id):
    if user_id is None: return None
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

# --->>> ИЗМЕНЕННАЯ ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ДЛЯ ИГРОВОГО ПОЛЯ <<<---
def get_game_board_data(c):
    """
    Подготавливает данные для отображения игрового поля.
    Рейтинги > 40 зацикливаются на поле.
    Возвращает список из 40 элементов, где каждый элемент - список имен пользователей
    в соответствующей ячейке.
    """
    board_cells = [[] for _ in range(40)] # Инициализация 40 пустых ячеек
    try:
        # ИЗМЕНЕНИЕ SQL: Получаем всех пользователей с рейтингом > 0
        c.execute("SELECT id, name, rating FROM users WHERE rating > 0 ORDER BY name") # Сортировка по имени для консистентности при одинаковых позициях
        users_for_board = c.fetchall()

        if users_for_board:
            for user_data_row in users_for_board:
                try:
                    rating = int(user_data_row['rating'])
                    username = user_data_row['name'] # Используем 'name'

                    # ИЗМЕНЕНИЕ ЛОГИКИ: Зацикливание рейтинга с помощью оператора %
                    # (rating - 1) т.к. рейтинг 1 должен быть на индексе 0
                    # % 40 т.к. у нас 40 ячеек (0-39)
                    cell_index = (rating - 1) % 40

                    board_cells[cell_index].append(username)

                except (ValueError, TypeError, KeyError) as e:
                    user_id_for_log = user_data_row.get('id', 'Неизвестный ID') if isinstance(user_data_row, sqlite3.Row) else 'Неизвестный ID'
                    print(f"Предупреждение (get_game_board_data): Некорректные данные для пользователя ID {user_id_for_log}: {e}")
                    continue
        print("get_game_board_data: Данные для игрового поля подготовлены (с зацикливанием).")
    except sqlite3.Error as e:
        print(f"Ошибка БД в get_game_board_data: {e}")
        flash("Ошибка загрузки данных для игрового поля.", "warning")
        # В случае ошибки БД, board_cells останется списком пустых списков
    return board_cells
# --->>> КОНЕЦ ИЗМЕНЕННОЙ ФУНКЦИИ <<<---


app.jinja_env.globals.update(
    get_user_name=get_user_name,
    get_leading_user_id=get_leading_user_id)

# --- Инициализация БД ---
# (init_db остается без изменений)
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    print("init_db: Connection opened.")
    try:
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
        conn.commit()
        print("init_db: Tables created and committed.")
        try:
            c.execute("SELECT 1 FROM settings WHERE key = 'game_over'")
            if c.fetchone():
                c.execute("UPDATE settings SET value = 'false' WHERE key = 'game_over'")
                print("init_db: 'game_over' setting updated to false.")
            else:
                c.execute("INSERT INTO settings (key, value) VALUES ('game_over', 'false')")
                print("init_db: 'game_over' setting inserted as false.")
        except sqlite3.Error as e:
            print(f"Warning: Could not reset 'game_over' setting during init_db: {e}")
        print("init_db: Starting image loading...")
        image_folders = ['koloda1', 'ariadna', 'odissey', 'pandora']
        images_added_count = 0
        for folder in image_folders:
            folder_path = os.path.join('static', 'images', folder)
            if os.path.exists(folder_path) and os.path.isdir(folder_path):
                for filename in os.listdir(folder_path):
                    if filename.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
                        try:
                            c.execute("SELECT 1 FROM images WHERE subfolder = ? AND image = ?", (folder, filename))
                            if c.fetchone() is None:
                                c.execute("INSERT INTO images (subfolder, image, status, guesses) VALUES (?, ?, 'Свободно', '{}')", (folder, filename))
                                images_added_count += 1
                        except sqlite3.Error as e:
                            print(f"Warning: Could not process image {folder}/{filename}: {e}")
            else:
                print(f"Warning: Folder not found or is not a directory: {folder_path}")
        if images_added_count > 0: print(f"init_db: Added {images_added_count} new images.")
        else: print("init_db: No new images were added.")
        conn.commit()
        print("init_db: Final commit successful.")
    except sqlite3.Error as e:
        print(f"CRITICAL ERROR during init_db: {e}")
        conn.rollback()
        raise
    finally:
        if conn: conn.close(); print("init_db: Connection closed.")


# --- Маршруты ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password_attempt = request.form.get('password')
        correct_password = os.environ.get('ADMIN_PASSWORD')
        if not correct_password:
            flash('Ошибка конфигурации сервера (пароль администратора не установлен).', 'danger')
            return render_template('login.html')
        if password_attempt == correct_password:
            session['is_admin'] = True
            flash('Авторизация успешна.', 'success')
            next_page = request.args.get('next')
            return redirect(next_page or url_for('admin'))
        else:
            flash('Неверный пароль.', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('is_admin', None)
    flash('Вы вышли из системы.', 'info')
    return redirect(url_for('login'))

@app.route("/")
def index():
    return render_template("index.html")

@app.before_request
def before_request():
    db = get_db()
    c = db.cursor()
    code = None
    if request.view_args and 'code' in request.view_args:
        code = request.view_args.get('code')
    elif request.args and 'code' in request.args:
        code = request.args.get('code')

    g.user_id = None
    if code:
        try:
            c.execute("SELECT id FROM users WHERE code = ?", (code,))
            user_row = c.fetchone()
            if user_row: g.user_id = user_row['id']
        except sqlite3.Error as e: print(f"DB error in before_request: {e}")

    g.show_card_info = get_setting("show_card_info") == "true"
    g.game_over = is_game_over()


@app.route("/admin", methods=["GET", "POST"])
def admin():
    if not session.get('is_admin'):
        flash('Для доступа к этой странице требуется авторизация администратора.', 'warning')
        return redirect(url_for('login', next=request.url))

    db = get_db()
    c = db.cursor()
    leader_to_display = None
    current_active_subfolder = ''
    show_card_info = False

    try:
        current_actual_leader_id = get_leading_user_id()
        current_active_subfolder = get_setting('active_subfolder') or ''
        show_card_info = get_setting('show_card_info') == "true"

        displayed_leader_id_from_url_str = request.args.get('displayed_leader_id')
        if displayed_leader_id_from_url_str:
            try: leader_to_display = int(displayed_leader_id_from_url_str)
            except (ValueError, TypeError): leader_to_display = current_actual_leader_id
        else: leader_to_display = current_actual_leader_id

    except Exception as e:
        print(f"CRITICAL Error reading initial settings for admin page: {e}")
        flash(f"Критическая ошибка чтения начальных настроек: {e}", "danger")
        return render_template("admin.html", users=[], images=[], subfolders=['koloda1'],
                               active_subfolder='', guess_counts_by_user={}, all_guesses={},
                               show_card_info=False, leader_to_display=None,
                               free_image_count=0, image_owners={}, user_has_duplicate_guesses={},
                               board_cells=[[] for _ in range(40)])

    if request.method == "POST":
        # (Весь ваш код обработки POST-запросов в admin остается здесь без изменений)
        action_handled = False
        leader_for_redirect = leader_to_display
        try:
            if "name" in request.form:
                name = request.form.get("name", "").strip()
                user_created_success = False
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
                        c.execute("INSERT INTO users (name, code) VALUES (?, ?)", (name, code))
                        user_id = c.lastrowid
                        flash(f"Пользователь '{name}' добавлен.", "success")
                        user_created_success = True
                        if current_actual_leader_id is None:
                            if set_leading_user_id(user_id):
                                flash(f"Пользователь '{name}' назначен Ведущим.", "info")
                                current_actual_leader_id = user_id
                                if leader_to_display is None: leader_to_display = current_actual_leader_id
                            else: flash("Ошибка назначения ведущего.", "warning")
                        if current_active_subfolder:
                            c.execute("SELECT id FROM images WHERE subfolder = ? AND status = 'Свободно'", (current_active_subfolder,))
                            available_cards_ids = [row['id'] for row in c.fetchall()]
                            if len(available_cards_ids) < num_cards:
                                flash(f"Недостаточно карт ({len(available_cards_ids)}) для {num_cards} шт.", "warning")
                                num_cards = len(available_cards_ids)
                            if num_cards > 0:
                                selected_cards_ids = random.sample(available_cards_ids, num_cards)
                                for card_id in selected_cards_ids:
                                    c.execute("UPDATE images SET status = ? WHERE id = ?", (f"Занято:{user_id}", card_id))
                                flash(f"'{name}' назначено {num_cards} карт.", "info")
                        else: flash("Активная колода не выбрана, карты не назначены.", "warning")
                if user_created_success:
                    db.commit(); action_handled = True; leader_for_redirect = current_actual_leader_id
            elif "active_subfolder" in request.form:
                selected = request.form.get("active_subfolder")
                if set_setting('active_subfolder', selected):
                    try:
                        updated_inactive = c.execute("UPDATE images SET status = 'Занято:Админ' WHERE subfolder != ? AND status = 'Свободно'", (selected,)).rowcount
                        db.commit()
                        flash(f"Выбрана колода: {selected}. Другие карты ({updated_inactive}) неактивны.", "success")
                        current_active_subfolder = selected
                    except sqlite3.Error as e: db.rollback(); flash(f"Ошибка обновления карт: {e}", "danger")
                else: flash("Ошибка сохр. активной колоды.", "danger")
                leader_for_redirect = leader_to_display; action_handled = True
            elif "delete_user_id" in request.form:
                user_id_to_delete = int(request.form.get("delete_user_id"))
                was_leader = (current_actual_leader_id == user_id_to_delete)
                c.execute("SELECT name FROM users WHERE id = ?", (user_id_to_delete,)); user_to_delete = c.fetchone()
                if user_to_delete:
                    user_name_deleted = user_to_delete['name']
                    c.execute("DELETE FROM users WHERE id = ?", (user_id_to_delete,))
                    c.execute("UPDATE images SET status = 'Свободно' WHERE status = ?", (f"Занято:{user_id_to_delete}",))
                    c.execute("UPDATE images SET status = 'Свободно', owner_id = NULL, guesses = '{}' WHERE owner_id = ?", (user_id_to_delete,))
                    flash(f"Пользователь '{user_name_deleted}' удален.", "success")
                    new_leader_id_after_delete = current_actual_leader_id
                    if was_leader:
                        c.execute("SELECT id FROM users ORDER BY id"); remaining_users = c.fetchall()
                        if remaining_users:
                            new_leader_id_after_delete = remaining_users[0]['id']
                            if set_leading_user_id(new_leader_id_after_delete):
                                flash(f"Новый Ведущий: {get_user_name(new_leader_id_after_delete) or f'ID {new_leader_id_after_delete}'}.", "info")
                            else: flash("Ошибка назначения нового ведущего.", "warning")
                        else:
                            new_leader_id_after_delete = None; set_leading_user_id(None)
                            flash("Удален Ведущий. Пользователей не осталось.", "warning")
                        leader_for_redirect = new_leader_id_after_delete
                    else: leader_for_redirect = current_actual_leader_id
                    db.commit()
                else: flash(f"Пользователь с ID {user_id_to_delete} не найден.", "danger"); leader_for_redirect = leader_to_display
                action_handled = True
            if action_handled: return redirect(url_for('admin', displayed_leader_id=leader_for_redirect))
        except sqlite3.IntegrityError as e:
            if "UNIQUE constraint failed" not in str(e): flash(f"Ошибка целостности БД: {e}", "danger")
            db.rollback()
        except (sqlite3.Error, ValueError, TypeError) as e: flash(f"Ошибка обработки запроса: {e}", "danger"); db.rollback()
        except Exception as e: print(f"!!! UNEXPECTED POST ERROR: {e}"); flash(f"Непредвиденная ошибка: {e}", "danger"); db.rollback()

    # --- Получение данных для отображения (GET) ---
    users, images, subfolders, guess_counts_by_user, all_guesses = [], [], [], {}, {}
    free_image_count = 0; image_owners = {}; user_has_duplicate_guesses = {}
    board_cells = get_game_board_data(c) # <--- ВЫЗОВ ФУНКЦИИ ДЛЯ ПОЛЯ

    try:
        c.execute("SELECT id, name, code, rating FROM users ORDER BY name ASC"); users = c.fetchall()
        c.execute("SELECT id, subfolder, image, status, owner_id, guesses FROM images ORDER BY subfolder, id"); images_rows = c.fetchall()
        images = []; all_guesses = {}
        for img_row in images_rows:
            guesses_json_str = img_row['guesses'] or '{}'
            try: guesses_dict = json.loads(guesses_json_str)
            except json.JSONDecodeError: guesses_dict = {}
            img_dict = dict(img_row); img_dict['guesses'] = guesses_dict; images.append(img_dict)
            if img_dict['owner_id'] is not None: image_owners[img_dict['id']] = img_dict['owner_id']
            if img_dict['status'] == 'Свободно' and img_dict['subfolder'] == current_active_subfolder: free_image_count += 1
            if guesses_dict: all_guesses[img_row['id']] = guesses_dict
        user_has_duplicate_guesses = {user['id']: False for user in users}
        if all_guesses:
            for user_data_row in users:
                user_id_str = str(user_data_row['id']); guesses_made_by_user = []
                for image_id, guesses_for_image in all_guesses.items():
                    if user_id_str in guesses_for_image: guesses_made_by_user.append(guesses_for_image[user_id_str])
                if len(guesses_made_by_user) > len(set(guesses_made_by_user)): user_has_duplicate_guesses[user_data_row['id']] = True
        guess_counts_by_user = {user['id']: 0 for user in users}
        for img_id, guesses_for_image in all_guesses.items():
            for guesser_id_str in guesses_for_image:
                try:
                    if int(guesser_id_str) in guess_counts_by_user: guess_counts_by_user[int(guesser_id_str)] += 1
                except (ValueError, TypeError): pass
        c.execute("SELECT DISTINCT subfolder FROM images ORDER BY subfolder")
        subfolders = [row['subfolder'] for row in c.fetchall()] or ['koloda1']
    except sqlite3.Error as e:
        flash(f"Ошибка чтения данных: {e}", "danger")
        users, images, subfolders, guess_counts_by_user, all_guesses = [], [], [], {}, {}
        # board_cells уже содержит результат от get_game_board_data (возможно, пустой)
    except Exception as e:
        flash(f"Непредвиденная ошибка: {e}", "danger")
        users, images, subfolders, guess_counts_by_user, all_guesses = [], [], [], {}, {}
        # board_cells уже содержит результат от get_game_board_data

    return render_template("admin.html", users=users, images=images,
                           subfolders=subfolders, active_subfolder=current_active_subfolder,
                           guess_counts_by_user=guess_counts_by_user, all_guesses=all_guesses,
                           show_card_info=show_card_info, leader_to_display=leader_to_display,
                           free_image_count=free_image_count, image_owners=image_owners,
                           user_has_duplicate_guesses=user_has_duplicate_guesses,
                           board_cells=board_cells)

# --- Маршруты start_new_game и open_cards остаются такими, какими мы их финализировали ---
@app.route("/start_new_game", methods=["POST"])
def start_new_game():
    db = get_db(); c = db.cursor()
    selected_deck = request.form.get("new_game_subfolder")
    try:
        num_cards_per_player = int(request.form.get("new_game_num_cards", 3))
        if num_cards_per_player < 1: raise ValueError("Кол-во карт < 1.")
    except (ValueError, TypeError): flash("Неверное кол-во карт.", "danger"); return redirect(url_for('admin'))
    if not selected_deck: flash("Колода не выбрана.", "danger"); return redirect(url_for('admin'))
    try:
        c.execute("UPDATE users SET rating = 0")
        c.execute("UPDATE images SET owner_id = NULL, guesses = '{}', status = 'Занято:Админ'")
        c.execute("UPDATE images SET status = 'Свободно' WHERE subfolder = ?", (selected_deck,))
        set_game_over(False); set_setting("show_card_info", "false"); set_setting("active_subfolder", selected_deck)
        c.execute("SELECT id FROM users ORDER BY id LIMIT 1"); first_user = c.fetchone()
        new_leader_id = first_user['id'] if first_user else None
        set_leading_user_id(new_leader_id); db.commit()
        if not first_user: flash("Пользователи не найдены, ведущий не назначен.", "warning")
        c.execute("SELECT id FROM users ORDER BY id"); user_ids = [row['id'] for row in c.fetchall()]
        num_total_dealt = 0
        if user_ids:
            c.execute("SELECT id FROM images WHERE subfolder = ? AND status = 'Свободно'", (selected_deck,)); available_cards_ids = [row['id'] for row in c.fetchall()]
            random.shuffle(available_cards_ids); num_available = len(available_cards_ids)
            if num_available < len(user_ids) * num_cards_per_player: flash(f"Недостаточно карт ({num_available}) для раздачи по {num_cards_per_player} шт.", "warning")
            card_idx = 0
            for user_id in user_ids:
                for _ in range(num_cards_per_player):
                    if card_idx < num_available:
                        c.execute("UPDATE images SET status = ? WHERE id = ?", (f"Занято:{user_id}", available_cards_ids[card_idx]))
                        card_idx +=1; num_total_dealt +=1
                    else: break
            db.commit()
        flash(f"Новая игра начата с '{selected_deck}'. Роздано карт: {num_total_dealt}.", "success")
    except Exception as e: db.rollback(); flash(f"Ошибка при старте игры: {e}", "danger"); print(traceback.format_exc())
    return redirect(url_for('admin'))

@app.route("/open_cards", methods=["POST"])
def open_cards():
    if hasattr(g, 'game_over') and g.game_over:
        flash("Игра окончена. Подсчет очков невозможен.", "warning"); return redirect(url_for('admin'))
    db = get_db(); c = db.cursor(); leader_just_finished = get_leading_user_id()
    stop_processing = False; points_summary = []
    try:
        if not set_setting("show_card_info", "true"): flash("Не удалось обновить видимость карт.", "warning")
        if leader_just_finished is None:
            c.execute("SELECT id FROM users ORDER BY id LIMIT 1"); first_user = c.fetchone()
            if first_user:
                leader_just_finished = first_user['id']
                if not set_leading_user_id(leader_just_finished):
                    flash(f"Не удалось установить первого ведущего (ID: {leader_just_finished}).", "danger"); db.rollback(); return redirect(url_for("admin"))
                flash(f"Ведущий не был установлен. Назначен: {get_user_name(leader_just_finished)}.", "info")
            else: flash("Нет пользователей для подсчета очков.", "warning"); return redirect(url_for("admin"))
        c.execute("SELECT id, owner_id, guesses FROM images WHERE owner_id IS NOT NULL"); table_images = c.fetchall()
        c.execute("SELECT id FROM users"); all_user_ids = [int(user['id']) for user in c.fetchall()]
        num_all_users = len(all_user_ids); user_points = {user_id: 0 for user_id in all_user_ids}
        if leader_just_finished not in user_points and leader_just_finished is not None:
            flash(f"Ведущий (ID: {leader_just_finished}) не найден среди текущих пользователей.", "warning")
        for image_data in table_images:
            if stop_processing: break
            owner_id = image_data['owner_id']; image_id = image_data['id']; guesses_json_str = image_data['guesses'] or '{}'
            try: guesses = json.loads(guesses_json_str)
            except json.JSONDecodeError: guesses = {}
            try: owner_id = int(owner_id)
            except (ValueError, TypeError): continue
            if owner_id not in user_points: continue
            correct_guesses_count = 0; correct_guesser_ids = []
            for guesser_id_str, guessed_user_id in guesses.items():
                try:
                    guesser_id = int(guesser_id_str); guessed_user_id_int = int(guessed_user_id)
                    if guesser_id in user_points and guesser_id != owner_id and guessed_user_id_int == owner_id:
                        correct_guesses_count += 1; correct_guesser_ids.append(guesser_id)
                        if owner_id == leader_just_finished: user_points[guesser_id] += 3
                except (ValueError, TypeError): continue
            num_potential_guessers = num_all_users - 1 if num_all_users > 1 else 0
            if owner_id == leader_just_finished:
                if num_potential_guessers > 0:
                    if correct_guesses_count == num_potential_guessers:
                        stop_processing = True
                        try: c.execute("UPDATE users SET rating = MAX(0, rating - 3) WHERE id = ?", (owner_id,))
                        except sqlite3.Error as direct_update_err: flash(f"Ошибка БД (П1) для Ведущего {owner_id}: {direct_update_err}.", "danger"); db.rollback(); return redirect(url_for("admin"))
                        break # Прерываем цикл по картам
                    elif correct_guesses_count == 0: user_points[owner_id] -= 2
                    else: user_points[owner_id] += (3 + correct_guesses_count)
                else: user_points[owner_id] -= 2
            else:
                if correct_guesses_count > 0: user_points[owner_id] += correct_guesses_count
        if stop_processing: flash("Подсчет остановлен (П1): Ведущему -3 (не ниже 0).", "info")
        else:
            for user_id, points in user_points.items():
                if points != 0:
                    try:
                        user_name = get_user_name(user_id) or f"ID {user_id}"
                        c.execute("UPDATE users SET rating = MAX(0, rating + ?) WHERE id = ?", (points, user_id))
                        points_summary.append(f"{user_name}: {points:+}")
                    except sqlite3.Error as e: flash(f"Ошибка обновления рейтинга для {user_id}: {e}", "danger"); db.rollback(); return redirect(url_for("admin"))
        next_leading_user_id = None
        try: c.execute("SELECT id FROM users ORDER BY id"); user_ids_ordered = [int(row['id']) for row in c.fetchall()]
        except sqlite3.Error as e: flash("Ошибка БД при опр. след. ведущего.", "danger"); db.rollback(); return redirect(url_for("admin"))
        if not user_ids_ordered: set_leading_user_id(None)
        elif leader_just_finished is not None and leader_just_finished in user_ids_ordered:
            try: next_leading_user_id = user_ids_ordered[(user_ids_ordered.index(leader_just_finished) + 1) % len(user_ids_ordered)]
            except ValueError: next_leading_user_id = user_ids_ordered[0]
        elif user_ids_ordered: next_leading_user_id = user_ids_ordered[0]
        if next_leading_user_id is not None:
            if not set_leading_user_id(next_leading_user_id):
                flash("Крит. ошибка: не удалось сохр. нового ведущего.", "danger"); db.rollback(); return redirect(url_for("admin"))
            next_leader_name = get_user_name(next_leading_user_id) or f"ID {next_leading_user_id}"
            flash_msg = f"Раунд завершен (очки не сохр., кроме -3 Ведущему). След. ведущий: {next_leader_name}." if stop_processing else f"Подсчет завершен. След. ведущий: {next_leader_name}."
            flash(flash_msg, "info" if stop_processing else "success")
        else: set_leading_user_id(None); flash("Не удалось определить следующего ведущего.", "warning")
        if points_summary and not stop_processing: flash(f"Изменение очков: {'; '.join(points_summary)}", "info")
        elif not stop_processing and not points_summary: flash("В этом раунде очки не изменились.", "info")
        db.commit()
    except Exception as e:
        db.rollback(); flash(f"Ошибка обработки раунда: {type(e).__name__} - {e}", "danger"); print(traceback.format_exc())
        return redirect(url_for("admin"))
    return redirect(url_for("admin", displayed_leader_id=leader_just_finished))

# --- Запуск приложения ---
if __name__ == "__main__":
    # init_db() # Раскомментируйте для инициализации/пересоздания БД при запуске
    app.run(debug=True) # debug=True только для разработки!
