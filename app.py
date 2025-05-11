import json
from flask import Flask, render_template, request, redirect, url_for, g, flash, session
import sqlite3
import os
import string
import random
import traceback # Додано для детального логування помилок

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')
# Проверка при запуске, что ключ установлен (важно!)
if not app.config['SECRET_KEY']:
    raise ValueError("Не установлена переменная окружения SECRET_KEY!")
    
DB_PATH = 'database.db'

# --- ДОДАНО: Конфігурація для Ігрового Поля ---
GAME_BOARD_POLE_IMG_SUBFOLDER = "pole" 
GAME_BOARD_POLE_IMAGES = [f"p{i}.jpg" for i in range(1, 8)] 
DEFAULT_NUM_BOARD_CELLS = 40 

_current_game_board_pole_image_config = []
_current_game_board_num_cells = 0
# --- Кінець Конфігурації ---

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

# --- Маршрути автентифікації та головна сторінка ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password_attempt = request.form.get('password')
        correct_password = os.environ.get('ADMIN_PASSWORD')
        if not correct_password:
             print("ПРЕДУПРЕЖДЕНИЕ: Не установлена переменная окружения ADMIN_PASSWORD!")
             flash('Ошибка конфигурации сервера.', 'danger')
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
    
# --- Инициализация БД ---
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
        image_folders = ['koloda1', 'ariadna', 'detstvo', 'odissey', 'pandora'] 
        images_added_count = 0
        for folder in image_folders:
            folder_path = os.path.join('static', 'images', folder)
            print(f"init_db: Checking folder: {folder_path}")
            if os.path.exists(folder_path) and os.path.isdir(folder_path): 
                print(f"init_db: Processing folder: {folder}")
                for filename in os.listdir(folder_path):
                    if filename.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
                        try:
                            print(f"init_db: Checking image {folder}/{filename}...")
                            c.execute("SELECT 1 FROM images WHERE subfolder = ? AND image = ?", (folder, filename))
                            if c.fetchone() is None:
                                 c.execute("INSERT INTO images (subfolder, image, status, guesses) VALUES (?, ?, 'Свободно', '{}')", (folder, filename))
                                 images_added_count += 1
                                 print(f"init_db: Added image {folder}/{filename}")
                        except sqlite3.Error as e:
                            print(f"Warning: Could not process image {folder}/{filename}: {e}")
            else:
                 print(f"Warning: Folder not found or is not a directory: {folder_path}")
        if images_added_count > 0:
            print(f"init_db: Added {images_added_count} new images to the database.")
        else:
            print("init_db: No new images were added.")
        conn.commit()
        print("init_db: Final commit successful.")
    except sqlite3.Error as e:
        print(f"CRITICAL ERROR during init_db execution: {e}")
        conn.rollback()
        print("init_db: Changes rolled back due to critical error.")
        raise 
    finally:
        if conn:
            conn.close() 
            print("init_db: Connection closed.")

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
    if value and value.strip():
        try:
            return int(value)
        except (ValueError, TypeError):
            print(f"Invalid leading_user_id value found in settings: {value}")
            return None
    return None

def set_leading_user_id(user_id):
    value_to_set = str(user_id) if user_id is not None else ''
    return set_setting('leading_user_id', value_to_set)

def get_user_name(user_id):
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

# --- ДОДАНО: Функції для ігрового поля ---
def initialize_new_game_board_visuals(num_cells_for_board=None, all_users_for_rating_check=None):
    global _current_game_board_pole_image_config, _current_game_board_num_cells
    actual_num_cells = DEFAULT_NUM_BOARD_CELLS
    if num_cells_for_board is not None:
        actual_num_cells = num_cells_for_board
    elif all_users_for_rating_check:
        max_rating = 0
        for user_data_item in all_users_for_rating_check:
            user_rating = 0
            if isinstance(user_data_item, dict):
                user_rating = user_data_item.get('rating', 0)
            elif hasattr(user_data_item, 'rating'):
                user_rating = getattr(user_data_item, 'rating', 0)
            if isinstance(user_rating, int) and user_rating > max_rating:
                max_rating = user_rating
        actual_num_cells = max(DEFAULT_NUM_BOARD_CELLS, max_rating + 6) # +6 для запасу
    _current_game_board_num_cells = actual_num_cells
    _current_game_board_pole_image_config = []
    pole_image_folder_path = os.path.join('static', 'images', GAME_BOARD_POLE_IMG_SUBFOLDER)
    if GAME_BOARD_POLE_IMAGES and os.path.exists(pole_image_folder_path) and os.path.isdir(pole_image_folder_path):
        for _ in range(_current_game_board_num_cells):
            random_pole_image_file = random.choice(GAME_BOARD_POLE_IMAGES)
            # Шлях відносно папки static
            image_path = os.path.join('images', GAME_BOARD_POLE_IMG_SUBFOLDER, random_pole_image_file).replace("\\", "/")
            _current_game_board_pole_image_config.append(image_path)
    else:
        if not GAME_BOARD_POLE_IMAGES: print(f"ПОПЕРЕДЖЕННЯ: Список GAME_BOARD_POLE_IMAGES порожній.")
        if not os.path.exists(pole_image_folder_path): print(f"ПОПЕРЕДЖЕННЯ: Папка для зображень поля '{pole_image_folder_path}' не знайдена.")
        elif not os.path.isdir(pole_image_folder_path): print(f"ПОПЕРЕДЖЕННЯ: '{pole_image_folder_path}' не є папкою.")
        
        default_placeholder = os.path.join('images', GAME_BOARD_POLE_IMG_SUBFOLDER, "p1.jpg").replace("\\", "/") 
        _current_game_board_pole_image_config = [default_placeholder] * _current_game_board_num_cells
        print(f"Використовуються placeholder'и для ігрового поля: {default_placeholder}")
    print(f"Візуалізацію ігрового поля ініціалізовано/оновлено для {_current_game_board_num_cells} клітинок.")

def generate_game_board_data_for_display(all_users_data_for_board):
    global _current_game_board_pole_image_config, _current_game_board_num_cells
    
    # Ініціалізація візуалізації поля, якщо потрібно (як було раніше)
    if not _current_game_board_pole_image_config or _current_game_board_num_cells == 0:
        print("DEBUG_GB: Board visuals not initialized or num_cells is 0. Attempting to initialize...")
        # Передаємо all_users_data_for_board, оскільки він може містити актуальні рейтинги
        initialize_new_game_board_visuals(all_users_for_rating_check=all_users_data_for_board)
        if not _current_game_board_pole_image_config or _current_game_board_num_cells == 0:
            print("DEBUG_GB: ERROR - Failed to initialize game board visuals even after auto-attempt.")
            return [] # Повертаємо порожній список, якщо ініціалізація не вдалася

    print(f"DEBUG_GB: generate_game_board_data_for_display called.")
    print(f"DEBUG_GB: Received {len(all_users_data_for_board)} users for board generation.")
    # Виведемо дані про перших кількох гравців для перевірки
    for i, u_debug in enumerate(all_users_data_for_board):
        if i < 3: # Логуємо тільки перших 3 для короткості
            try:
                user_id_debug = u_debug.get('id') if isinstance(u_debug, dict) else u_debug['id']
                user_name_debug = u_debug.get('name') if isinstance(u_debug, dict) else u_debug['name']
                user_rating_debug = u_debug.get('rating') if isinstance(u_debug, dict) else u_debug['rating']
                print(f"DEBUG_GB: User sample: id={user_id_debug}, name='{user_name_debug}', rating={user_rating_debug} (type: {type(user_rating_debug)})")
            except Exception as e_debug_user:
                print(f"DEBUG_GB: Error accessing user sample data: {e_debug_user}, user_data: {u_debug}")
        else:
            break

    board_cells_data = []
    print(f"DEBUG_GB: Board is configured for {_current_game_board_num_cells} cells.")

    if not _current_game_board_pole_image_config:
        print("DEBUG_GB: ERROR - _current_game_board_pole_image_config is empty! Cannot generate board.")
        # Спробуємо ще раз ініціалізувати, хоча це вже мало б статися вище
        initialize_new_game_board_visuals(all_users_for_rating_check=all_users_data_for_board)
        if not _current_game_board_pole_image_config:
             print("DEBUG_GB: CRITICAL - _current_game_board_pole_image_config still empty after re-init. Returning empty board.")
             return []


    for i in range(_current_game_board_num_cells):
        cell_number = i + 1 
        
        # Захист від порожнього _current_game_board_pole_image_config або виходу за межі індексу
        cell_image_path = "static/images/default_pole_image.png" # Шлях до запасного зображення за замовчуванням
        if _current_game_board_pole_image_config: # Перевірка, що список не порожній
            try:
                cell_image_path_idx = i % len(_current_game_board_pole_image_config)
                cell_image_path = _current_game_board_pole_image_config[cell_image_path_idx]
            except IndexError:
                 print(f"DEBUG_GB: IndexError for pole image config. Index: {i}, Len: {len(_current_game_board_pole_image_config)}. Using default.")
            except TypeError: # Якщо _current_game_board_pole_image_config несподівано None
                 print(f"DEBUG_GB: TypeError - _current_game_board_pole_image_config is None. Using default image.")
        # else: # Цей випадок вже оброблено вище, але для безпеки
        #      print(f"DEBUG_GB: _current_game_board_pole_image_config is empty for cell {cell_number}. Using default image.")


        users_in_this_cell = []
        # print(f"DEBUG_GB: Processing cell {cell_number}") # Можна розкоментувати для дуже детального логу

        for user_data_item_board in all_users_data_for_board:
            user_rating_raw = None
            user_name = "Unknown"
            user_id_for_name = "UnknownID"

            if isinstance(user_data_item_board, sqlite3.Row):
                try:
                    user_rating_raw = user_data_item_board['rating']
                    user_name = user_data_item_board['name']
                    user_id_for_name = user_data_item_board['id']
                except IndexError as e_sqlite_row: # Якщо ключ 'rating', 'name' або 'id' відсутній
                    print(f"DEBUG_GB: KeyError accessing sqlite3.Row: {e_sqlite_row}. Row keys: {user_data_item_board.keys()}")
                    continue 
            elif isinstance(user_data_item_board, dict):
                user_rating_raw = user_data_item_board.get('rating') # .get() безпечніший, поверне None якщо немає
                user_name = user_data_item_board.get('name', "N/A")
                user_id_for_name = user_data_item_board.get('id', "N/A_ID")
            else:
                print(f"DEBUG_GB: Unexpected user_data_item_board type: {type(user_data_item_board)}. Skipping this user item.")
                continue
            
            # print(f"DEBUG_GB:   User {user_id_for_name} ('{user_name}') has raw rating '{user_rating_raw}' (type: {type(user_rating_raw)}). Comparing with cell_number {cell_number} (type: {type(cell_number)})")

            current_user_rating_int = 0 # За замовчуванням
            if user_rating_raw is not None:
                try:
                    current_user_rating_int = int(user_rating_raw)
                except (ValueError, TypeError) as e_conv:
                    print(f"DEBUG_GB: Could not convert rating '{user_rating_raw}' to int for user {user_id_for_name} ('{user_name}'). Error: {e_conv}. Defaulting to 0.")
                    current_user_rating_int = 0 # Явно присвоюємо 0 у випадку помилки конвертації
            # else: # Якщо user_rating_raw is None, current_user_rating_int залишиться 0
            #    print(f"DEBUG_GB: User {user_id_for_name} ('{user_name}') has None rating. Defaulting to 0.")


            if current_user_rating_int == cell_number:
                display_name = user_name if user_name and str(user_name).strip() else f"ID {user_id_for_name}"
                users_in_this_cell.append({'id': user_id_for_name, 'name': display_name, 'rating': current_user_rating_int})
                print(f"DEBUG_GB: ---> ADDED User '{display_name}' (Actual Rating for check: {current_user_rating_int}) to cell {cell_number}")
            
        if not users_in_this_cell and cell_number <= _current_game_board_num_cells : # Логуємо для всіх порожніх клітин
             # Це повідомлення з'явиться в консолі Flask, а не на веб-сторінці
             # На веб-сторінці вже є (No players in cell X) з шаблону
             if cell_number <= 5 or cell_number == _current_game_board_num_cells: # Обмежимо логування, щоб не спамити
                print(f"DEBUG_GB: Server-side: Cell {cell_number} determined to be empty.")


        board_cells_data.append({
            'cell_number': cell_number,
            'image_path': cell_image_path,
            'users_in_cell': users_in_this_cell
        })
    
    print(f"DEBUG_GB: Finished generating board data. Total cells processed: {len(board_cells_data)}")
    return board_cells_data

# --- Глобальные переменные и функции для Jinja ---
app.jinja_env.globals.update(
    get_user_name=get_user_name,
    get_leading_user_id=get_leading_user_id) 

# --- Обработчики запросов ---
@app.before_request
def before_request():
    db = get_db()
    c = db.cursor()
    code_param_before_req = None # Змінено code на code_param_before_req
    if request.view_args and 'code' in request.view_args:
        code_param_before_req = request.view_args.get('code')
    elif request.args and 'code' in request.args: 
        code_param_before_req = request.args.get('code')
    g.user_id = None 
    if code_param_before_req:
        try:
            c.execute("SELECT id FROM users WHERE code = ?", (code_param_before_req,))
            user_row = c.fetchone()
            if user_row:
                g.user_id = user_row['id']
        except sqlite3.Error as e:
            print(f"Database error in before_request checking code '{code_param_before_req}': {e}")
            g.user_id = None 
    show_card_info_setting = get_setting("show_card_info")
    g.show_card_info = show_card_info_setting == "true" 
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
    show_card_info_admin_page = False # Змінено show_card_info на show_card_info_admin_page

    try:
        current_actual_leader_id = get_leading_user_id() 
        current_active_subfolder = get_setting('active_subfolder') or '' 
        show_card_info_admin_page = get_setting('show_card_info') == "true" 
        displayed_leader_id_from_url_str = request.args.get('displayed_leader_id')
        if displayed_leader_id_from_url_str:
            try:
                leader_to_display = int(displayed_leader_id_from_url_str)
            except (ValueError, TypeError):
                leader_to_display = current_actual_leader_id 
        else:
            leader_to_display = current_actual_leader_id
    except Exception as e:
        print(f"CRITICAL Error reading initial settings in admin: {e}")
        flash(f"Критическая ошибка чтения начальных настроек: {e}", "danger")
        return render_template("admin.html", users=[], images=[], subfolders=['koloda1', 'koloda2'],
                               active_subfolder='', guess_counts_by_user={}, all_guesses={},
                               show_card_info=False, leader_to_display=None,
                               free_image_count=0, image_owners={}, user_has_duplicate_guesses={},
                               game_board=[] ) # Додано порожній game_board для помилки

    if request.method == "POST":
        action_handled = False 
        leader_for_redirect = leader_to_display
        try:
            if "name" in request.form:
                name_admin_form = request.form.get("name", "").strip() # Змінено name
                user_created_success = False 
                if not name_admin_form:
                     flash("Имя пользователя не может быть пустым.", "warning")
                else:
                    num_cards = int(request.form.get("num_cards", 3))
                    if num_cards < 1: num_cards = 1
                    code_admin_form = generate_unique_code() # Змінено code
                    c.execute("SELECT 1 FROM users WHERE name = ?", (name_admin_form,))
                    if c.fetchone():
                        flash(f"Имя пользователя '{name_admin_form}' уже существует.", "danger")
                    else:
                        c.execute("INSERT INTO users (name, code) VALUES (?, ?)", (name_admin_form, code_admin_form))
                        user_id_admin_form = c.lastrowid # Змінено user_id
                        flash(f"Пользователь '{name_admin_form}' добавлен.", "success")
                        user_created_success = True 
                        if current_actual_leader_id is None:
                            if set_leading_user_id(user_id_admin_form):
                                flash(f"Пользователь '{name_admin_form}' назначен Ведущим.", "info")
                                current_actual_leader_id = user_id_admin_form
                                if leader_to_display is None:
                                    leader_to_display = current_actual_leader_id
                            else:
                                flash("Ошибка назначения ведущего.", "warning")
                        if current_active_subfolder:
                            c.execute("SELECT id FROM images WHERE subfolder = ? AND status = 'Свободно'", (current_active_subfolder,))
                            available_cards_ids = [row['id'] for row in c.fetchall()]
                            if len(available_cards_ids) < num_cards:
                                 flash(f"Недостаточно свободных карт ({len(available_cards_ids)}) в '{current_active_subfolder}' для {num_cards} шт.", "warning")
                                 num_cards = len(available_cards_ids)
                            if num_cards > 0:
                                selected_cards_ids = random.sample(available_cards_ids, num_cards)
                                for card_id_admin_form in selected_cards_ids: # Змінено card_id
                                    c.execute("UPDATE images SET status = ? WHERE id = ?", (f"Занято:{user_id_admin_form}", card_id_admin_form))
                                flash(f"'{name_admin_form}' назначено {num_cards} карт.", "info")
                        else:
                             flash("Активная колода не выбрана, карты не назначены.", "warning")
                if user_created_success:
                     db.commit() 
                     action_handled = True
                     leader_for_redirect = current_actual_leader_id
            elif "active_subfolder" in request.form:
                selected = request.form.get("active_subfolder")
                if set_setting('active_subfolder', selected): 
                    try:
                        updated_inactive = c.execute("UPDATE images SET status = 'Занято:Админ' WHERE subfolder != ? AND status = 'Свободно'", (selected,)).rowcount
                        db.commit()
                        flash_message_text = f"Выбрана активная колода: {selected}."
                        if updated_inactive > 0:
                            flash_message_text += f" Карты в других колодах ({updated_inactive} шт.) помечены как неактивные."
                        flash(flash_message_text, "success")
                        current_active_subfolder = selected 
                    except sqlite3.Error as e:
                        db.rollback()
                        flash(f"Ошибка обновления статусов карт: {e}", "danger")
                else:
                    flash("Ошибка сохранения настройки активной колоды.", "danger")
                leader_for_redirect = leader_to_display
                action_handled = True
            elif "delete_user_id" in request.form:
                user_id_to_delete = int(request.form.get("delete_user_id"))
                was_leader = (current_actual_leader_id == user_id_to_delete)
                c.execute("SELECT name FROM users WHERE id = ?", (user_id_to_delete,))
                user_to_delete = c.fetchone()
                if user_to_delete:
                    user_name_deleted = user_to_delete['name']
                    c.execute("DELETE FROM users WHERE id = ?", (user_id_to_delete,))
                    c.execute("UPDATE images SET status = 'Свободно' WHERE status = ?", (f"Занято:{user_id_to_delete}",))
                    c.execute("UPDATE images SET status = 'Свободно', owner_id = NULL, guesses = '{}' WHERE owner_id = ?", (user_id_to_delete,))
                    flash(f"Пользователь '{user_name_deleted}' удален.", "success")
                    new_leader_id_after_delete = current_actual_leader_id 
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
                        leader_for_redirect = new_leader_id_after_delete
                    else:
                         leader_for_redirect = current_actual_leader_id
                    db.commit() 
                else:
                    flash(f"Пользователь с ID {user_id_to_delete} не найден.", "danger")
                    leader_for_redirect = leader_to_display 
                action_handled = True
            if action_handled:
                return redirect(url_for('admin', displayed_leader_id=leader_for_redirect))
        except sqlite3.IntegrityError as e:
             if "UNIQUE constraint failed" not in str(e):
                 flash(f"Ошибка целостности базы данных: {e}", "danger")
             db.rollback()
        except (sqlite3.Error, ValueError, TypeError) as e:
             flash(f"Ошибка при обработке запроса: {e}", "danger")
             db.rollback()
        except Exception as e:
              print(f"!!! UNEXPECTED ERROR during admin POST: {e}") 
              flash(f"Произошла непредвиденная ошибка: {e}", "danger")
              db.rollback()
    
    users_admin_page, images_admin_page, subfolders_admin_page = [], [], [] # Змінено users, images, subfolders
    guess_counts_by_user_admin_page, all_guesses_admin_page = {}, {} # Змінено
    free_image_count_admin_page = 0 # Змінено
    image_owners_admin_page = {} # Змінено
    user_has_duplicate_guesses_admin_page = {} # Змінено
    game_board_admin_page = [] # ДОДАНО: для ігрового поля

    try:
        c.execute("SELECT id, name, code, rating FROM users ORDER BY name ASC")
        users_admin_page = c.fetchall()
        print(f"Admin GET: Fetched {len(users_admin_page)} users.")

        game_board_admin_page = generate_game_board_data_for_display(users_admin_page) # ДОДАНО

        c.execute("SELECT id, subfolder, image, status, owner_id, guesses FROM images ORDER BY subfolder, id")
        images_rows = c.fetchall()
        images_admin_page = []
        all_guesses_admin_page = {}
        print(f"Admin GET: Fetched {len(images_rows)} image rows. Active subfolder: '{current_active_subfolder}'")

        for img_row in images_rows:
            guesses_json_str = img_row['guesses'] or '{}'
            try:
                 guesses_dict = json.loads(guesses_json_str)
            except json.JSONDecodeError as json_e:
                 print(f"Warning: JSONDecodeError for image ID {img_row['id']} - guesses: '{guesses_json_str}'. Error: {json_e}. Using empty dict.")
                 guesses_dict = {}
            img_dict = dict(img_row)
            img_dict['guesses'] = guesses_dict
            images_admin_page.append(img_dict)
            if img_dict['owner_id'] is not None:
                image_owners_admin_page[img_dict['id']] = img_dict['owner_id']
            if img_dict['status'] == 'Свободно' and img_dict['subfolder'] == current_active_subfolder:
                free_image_count_admin_page += 1
            if guesses_dict:
                 all_guesses_admin_page[img_row['id']] = guesses_dict
        print(f"Admin GET: Processed images. Free count in active folder: {free_image_count_admin_page}")
        user_has_duplicate_guesses_admin_page = {user_item_admin['id']: False for user_item_admin in users_admin_page} # Змінено user
        if all_guesses_admin_page:
            for user_item_admin in users_admin_page: # Змінено user
                user_id_str = str(user_item_admin['id'])
                guesses_made_by_user = []
                for image_id_admin_page, guesses_for_image in all_guesses_admin_page.items(): # Змінено image_id
                    if user_id_str in guesses_for_image:
                        guesses_made_by_user.append(guesses_for_image[user_id_str])
                if len(guesses_made_by_user) > len(set(guesses_made_by_user)):
                     user_has_duplicate_guesses_admin_page[user_item_admin['id']] = True
        guess_counts_by_user_admin_page = {user_item_admin['id']: 0 for user_item_admin in users_admin_page} # Змінено user
        for img_id_admin_page, guesses_for_image in all_guesses_admin_page.items(): # Змінено img_id
            for guesser_id_str in guesses_for_image:
                 try:
                     if int(guesser_id_str) in guess_counts_by_user_admin_page:
                         guess_counts_by_user_admin_page[int(guesser_id_str)] += 1
                 except (ValueError, TypeError): pass
        print(f"Admin GET: Calculated guess counts and duplicates.")
        c.execute("SELECT DISTINCT subfolder FROM images ORDER BY subfolder")
        subfolders_admin_page = [row['subfolder'] for row in c.fetchall()] or ['koloda1', 'koloda2']
        print(f"Admin GET: Found subfolders: {subfolders_admin_page}")
    except sqlite3.Error as e:
        print(f"!!! ERROR caught in admin GET data fetch: {e}")
        flash(f"Ошибка чтения данных для отображения: {e}", "danger")
        users_admin_page, images_admin_page, subfolders_admin_page, guess_counts_by_user_admin_page, all_guesses_admin_page = [], [], [], {}, {}
        free_image_count_admin_page = 0
        image_owners_admin_page = {}
        user_has_duplicate_guesses_admin_page = {}
        game_board_admin_page = [] 
    except Exception as e: 
         print(f"!!! UNEXPECTED ERROR caught in admin GET data fetch: {e}")
         flash(f"Непредвиденная ошибка при чтении данных: {e}", "danger")
         users_admin_page, images_admin_page, subfolders_admin_page, guess_counts_by_user_admin_page, all_guesses_admin_page = [], [], [], {}, {}
         free_image_count_admin_page = 0
         image_owners_admin_page = {}
         user_has_duplicate_guesses_admin_page = {}
         game_board_admin_page = [] 

    # --- Начало изменений для игрового поля ---
    # 1. Получаем всех пользователей (необходимо для generate_game_board_data_for_display)
    c.execute("SELECT id, name, rating FROM users ORDER BY rating DESC, name") # или другой порядок
    all_users_data = c.fetchall() # Это будет список объектов sqlite3.Row

    # 2. Генерируем данные игрового поля
    # Ваша функция generate_game_board_data_for_display может вызывать initialize_new_game_board_visuals,
    # если _current_game_board_pole_image_config пуст.
    game_board_data = generate_game_board_data_for_display(all_users_data)
    # --- Конец изменений для игрового поля ---
    
    print(f"Admin GET: Rendering template. Users count: {len(users_admin_page)}")
    return render_template("admin.html", users=users_admin_page, images=images_admin_page,
                           subfolders=subfolders_admin_page, active_subfolder=current_active_subfolder,
                           guess_counts_by_user=guess_counts_by_user_admin_page, all_guesses=all_guesses_admin_page,
                           show_card_info=show_card_info_admin_page, 
                           leader_to_display=leader_to_display,
                           free_image_count=free_image_count_admin_page,
                           image_owners=image_owners_admin_page,
                           game_board=game_board_data,
                           get_user_name_func=get_user_name,
                           user_has_duplicate_guesses=user_has_duplicate_guesses_admin_page) # ДОДАНО
    
@app.route("/start_new_game", methods=["POST"])
def start_new_game():
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
    new_leader_id_sng = None # Змінено new_leader_id
    try:
        print("Сброс рейтингов...")
        c.execute("UPDATE users SET rating = 0")
        print("Сброс состояния карт...")
        c.execute("UPDATE images SET owner_id = NULL, guesses = '{}', status = 'Занято:Админ'")
        c.execute("UPDATE images SET status = 'Свободно' WHERE subfolder = ?", (selected_deck,))
        print("Сброс настроек игры...")
        set_game_over(False) 
        set_setting("show_card_info", "false")
        set_setting("active_subfolder", selected_deck)
        c.execute("SELECT id FROM users ORDER BY id LIMIT 1")
        first_user = c.fetchone()
        if first_user:
            new_leader_id_sng = first_user['id']
            set_leading_user_id(new_leader_id_sng)
            print(f"Назначен новый ведущий: {get_user_name(new_leader_id_sng)} (ID: {new_leader_id_sng})")
        else:
            set_leading_user_id(None) 
            print("Пользователи не найдены, ведущий не назначен.")
        
        # --- ДОДАНО: Ініціалізація ігрового поля ---
        c.execute("SELECT id, name, rating FROM users") 
        all_users_for_board_init_sng = c.fetchall() # Змінено all_users_for_board_init
        initialize_new_game_board_visuals(all_users_for_rating_check=all_users_for_board_init_sng)
        # --- Кінець додавання ---

        db.commit() 
        c.execute("SELECT id FROM users ORDER BY id")
        user_ids_sng = [row['id'] for row in c.fetchall()] # Змінено user_ids
        num_users = len(user_ids_sng)
        num_total_dealt = 0
        if not user_ids_sng:
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
            
            card_index = 0
            for user_id_sng_deal in user_ids_sng: # Змінено user_id
                cards_dealt_to_user = 0
                for _ in range(num_cards_per_player):
                    if card_index < num_available:
                        card_id_sng_deal = available_cards_ids[card_index] # Змінено card_id
                        c.execute("UPDATE images SET status = ? WHERE id = ?", (f"Занято:{user_id_sng_deal}", card_id_sng_deal))
                        card_index += 1
                        cards_dealt_to_user += 1
                    else:
                        break 
                # ВИПРАВЛЕНО ВІДСТУПИ ДЛЯ ЦИХ РЯДКІВ:
                print(f"  Пользователю ID {user_id_sng_deal} роздано карт: {cards_dealt_to_user}")
                num_total_dealt += cards_dealt_to_user
                if card_index >= num_available:
                    break 
            
            flash(f"Новая игра начата! Колода: '{selected_deck}'. Роздано карт: {num_total_dealt}.", "success")
            if new_leader_id_sng:
                flash(f"Ведущий назначен: {get_user_name(new_leader_id_sng)}.", "info")
        db.commit() 
        print("--- Новая игра успешно начата и карты розданы ---")
    # ВИПРАВЛЕНО ВІДСТУПИ ДЛЯ БЛОКІВ EXCEPT ТА RETURN:
    except sqlite3.Error as e:
        db.rollback()
        flash(f"Ошибка базы данных при начале новой игры: {e}", "danger")
        print(f"Database error during start_new_game: {e}")
    except Exception as e:
        db.rollback()
        flash(f"Непредвиденная ошибка при начале новой игры: {e}", "danger")
        print(f"Unexpected error during start_new_game: {e}")
    return redirect(url_for('admin', displayed_leader_id=new_leader_id_sng))
    
@app.route("/user/<code>")
def user(code): # Параметр названо code, як у вашому файлі
    db = get_db()
    c = db.cursor()
    user_data_page = None # Змінено user_data
    name_user_page, rating_user_page = None, None # Змінено
    cards_user_page, table_images_user_page, all_users_for_template = [], [], [] # Змінено
    on_table_user_page = False # Змінено
    leader_for_display_user_page = None # Змінено
    game_board_user_page = [] # ДОДАНО

    try:
        # Використовуємо g.user_id, встановлений в before_request, для безпеки
        if not g.user_id:
            flash("Неверный код доступа или сессия истекла.", "danger")
            return redirect(url_for('index'))

        # Отримуємо дані поточного користувача за g.user_id
        c.execute("SELECT id, name, rating, code FROM users WHERE id = ?", (g.user_id,))
        user_data_page = c.fetchone()

        if not user_data_page:
            flash("Пользователь не найден. Возможно, ваш код устарел.", "danger")
            session.pop('_flashes', None) 
            return redirect(url_for('index'))

        name_user_page = user_data_page['name']
        rating_user_page = user_data_page['rating']
        # code_user_page = user_data_page['code'] # Не потрібен, оскільки code є параметром функції

        c.execute("SELECT id, subfolder, image, status FROM images WHERE status = ?", (f"Занято:{g.user_id}",))
        cards_user_page = c.fetchall()

        c.execute("SELECT id, subfolder, image, owner_id, guesses FROM images WHERE status LIKE 'На столе:%' ORDER BY id")
        raw_table_images = c.fetchall()
        for img_row in raw_table_images:
            guesses_json_str = img_row['guesses'] or '{}'
            try: guesses_dict = json.loads(guesses_json_str)
            except json.JSONDecodeError: guesses_dict = {}
            img_dict = dict(img_row)
            # Ключі в JSON повинні бути рядками, це вже забезпечується при збереженні
            img_dict['guesses'] = guesses_dict 
            table_images_user_page.append(img_dict)

        c.execute("SELECT id, name, rating FROM users ORDER BY name ASC") # Додано rating
        all_users_for_template = c.fetchall()

        # --- ДОДАНО: Генерація даних ігрового поля ---
        game_board_user_page = generate_game_board_data_for_display(all_users_for_template)
        # --- Кінець додавання ---
        
        on_table_user_page = False
        for img_on_table in table_images_user_page:
            if img_on_table['owner_id'] == g.user_id:
                on_table_user_page = True
                break
        
        leader_for_display_user_page = get_leading_user_id()
        # Ваша логіка для визначення leader_for_display_user_page, якщо g.show_card_info is True
        if g.show_card_info and leader_for_display_user_page is not None:
            # Отримуємо всіх користувачів, відсортованих за ID, щоб знайти попереднього
            c.execute("SELECT id FROM users ORDER BY id") 
            user_ids_ordered = [row['id'] for row in c.fetchall()]
            if user_ids_ordered:
                try:
                    current_leader_idx = user_ids_ordered.index(leader_for_display_user_page)
                    # Попередній індекс по колу
                    previous_leader_idx = (current_leader_idx - 1 + len(user_ids_ordered)) % len(user_ids_ordered)
                    leader_for_display_user_page = user_ids_ordered[previous_leader_idx]
                except ValueError: # Якщо поточний ведучий не знайдений у списку (малоймовірно)
                    pass # Залишаємо поточного ведучого

    except sqlite3.Error as e:
        flash(f"Ошибка базы данных при загрузке профиля: {e}", "danger")
        return redirect(url_for('index'))
    except Exception as e_user_route_main: # Змінено e_user_route
        flash(f"Неочікувана помилка на сторінці користувача: {e_user_route_main}", "danger")
        print(f"Unexpected error in /user/{code} route: {e_user_route_main}")
        print(traceback.format_exc())
        return redirect(url_for('index'))

    return render_template("user.html", name=name_user_page, rating=rating_user_page, cards=cards_user_page,
                           table_images=table_images_user_page, all_users=all_users_for_template,
                           code=code, on_table=on_table_user_page, # Використовуємо code, переданий у функцію
                           leader_for_display=leader_for_display_user_page,
                           game_board=game_board_user_page) # ДОДАНО


# Маршрути для дій користувача (guess_image, place_card, open_cards, new_round) - ЗАЛИШАЮТЬСЯ ЯК У ВАШОМУ ФАЙЛІ
# Я не буду їх дублювати тут, оскільки вони вже є у вашому файлі app.py,
# і ви просили внести правки саме по ігровому полю.
# Переконайтеся, що їхні визначення є УНІКАЛЬНИМИ у вашому кінцевому файлі.
# Їхня логіка не змінювалася в цій ітерації.

# Приклад того, як вони виглядають у вашому файлі (не копіюйте це, якщо вони вже є):
@app.route("/user/<code>/guess/<int:image_id>", methods=["POST"])
def guess_image(code, image_id):
    # ... (ваша логіка з файлу) ...
    if not g.user_id:
        flash("Доступ запрещен. Пожалуйста, используйте вашу уникальную ссылку.", "danger")
        return redirect(url_for('index'))
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
        c.execute("SELECT guesses, owner_id FROM images WHERE id = ? AND status LIKE 'На столе:%'", (image_id,))
        image_data = c.fetchone()
        if not image_data:
            flash("Карточка не найдена на столе.", "danger")
            return redirect(url_for('user', code=code))
        if image_data['owner_id'] == g.user_id:
             flash("Нельзя угадывать свою карточку.", "warning")
             return redirect(url_for('user', code=code))
        if g.show_card_info: # Додано перевірку
            flash("Карты уже открыты, делать предположения поздно.", "warning")
            return redirect(url_for('user', code=code))
        guesses_json_str = image_data['guesses'] or '{}'
        try: guesses = json.loads(guesses_json_str)
        except json.JSONDecodeError: guesses = {}
        guesses[str(g.user_id)] = guessed_user_id
        c.execute("UPDATE images SET guesses = ? WHERE id = ?", (json.dumps(guesses), image_id))
        db.commit()
        guessed_user_name_display = get_user_name(guessed_user_id) or f"ID {guessed_user_id}"
        flash(f"Ваше предположение (что карта принадлежит '{guessed_user_name_display}') сохранено.", "success")
    except (ValueError, TypeError):
        flash("Неверный ID игрока для предположения.", "danger")
    except sqlite3.Error as e:
        db.rollback()
        flash(f"Ошибка сохранения предположения: {e}", "danger")
    return redirect(url_for('user', code=code))

@app.route("/user/<code>/place/<int:image_id>", methods=["POST"])
def place_card(code, image_id):
    # ... (ваша логіка з файлу) ...
    if not g.user_id:
        flash("Доступ запрещен.", "danger")
        return redirect(url_for('index'))
    db = get_db()
    c = db.cursor()
    try:
        if g.game_over: # Додано перевірку
            flash("Игра окончена, выкладывать карты нельзя.", "warning")
            return redirect(url_for('user', code=code))
        c.execute("SELECT 1 FROM images WHERE owner_id = ? AND status LIKE 'На столе:%'", (g.user_id,)) # Додано LIKE
        if c.fetchone() is not None:
            flash("У вас уже есть карта на столе в этом раунде.", "warning")
            return redirect(url_for('user', code=code))
        c.execute("SELECT status FROM images WHERE id = ?", (image_id,))
        card_status_row = c.fetchone()
        if not card_status_row or card_status_row['status'] != f"Занято:{g.user_id}":
            flash("Вы не можете выложить эту карту (она не ваша или уже использована).", "danger")
            return redirect(url_for('user', code=code))
        # При викладанні карти статус стає "На столе:ID_ГРАВЦЯ"
        c.execute("UPDATE images SET owner_id = ?, status = ?, guesses = '{}' WHERE id = ?", 
                  (g.user_id, f"На столе:{g.user_id}", image_id))
        db.commit()
        flash("Ваша карта выложена на стол.", "success")
    except sqlite3.Error as e:
        db.rollback()
        flash(f"Ошибка выкладывания карты: {e}", "danger")
    return redirect(url_for('user', code=code))

@app.route("/open_cards", methods=["POST"])
def open_cards():
    # ... (ваша детальна логіка open_cards з файлу) ...
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
                    flash(f"Не удалось установить первого ведущего (ID: {leader_just_finished}). Подсчет остановлен.", "danger")
                    db.rollback()
                    return redirect(url_for("admin"))
                flash(f"Ведущий не был установлен. Назначен: {get_user_name(leader_just_finished)}.", "info")
            else: 
                flash("Нет пользователей для подсчета очков.", "warning")
                return redirect(url_for("admin"))
        c.execute("SELECT id, owner_id, guesses FROM images WHERE status LIKE 'На столе:%'") 
        table_images = c.fetchall()
        if not table_images:
            flash("Нет карт на столе для подсчета очков. Карты открыты.", "info")
        else:
            c.execute("SELECT id FROM users") 
            all_user_ids = [int(user['id']) for user in c.fetchall()]
            num_all_users = len(all_user_ids)
            user_points = {user_id_calc: 0 for user_id_calc in all_user_ids}
            if leader_just_finished not in user_points and leader_just_finished is not None:
                flash(f"Ведущий (ID: {leader_just_finished}), который завершил ход, не найден среди текущих пользователей. Очки могут быть подсчитаны некорректно.", "warning")
            print("--- Начисление очков ---")
            for image_data in table_images:
                if stop_processing:
                    print("  Обработка карт прервана из-за выполнения Правила 1.")
                    break
                owner_id = image_data['owner_id']
                image_id_calc = image_data['id'] 
                guesses_json_str = image_data['guesses'] or '{}'
                try: guesses = json.loads(guesses_json_str)
                except json.JSONDecodeError:
                    print(f"  ПРЕДУПРЕЖДЕНИЕ: Некорректный JSON в guesses для карты {image_id_calc}.")
                    guesses = {}
                try: owner_id = int(owner_id) 
                except (ValueError, TypeError):
                    print(f"  ПРЕДУПРЕЖДЕНИЕ: Некорректный owner_id ({owner_id}) для карты {image_id_calc}.")
                    continue 
                if owner_id not in user_points:
                    print(f"  ИНФО: Владелец {owner_id} карты {image_id_calc} неактивен или не существует. Карта пропускается.")
                    continue
                print(f"\n  Обработка карты {image_id_calc} (Владелец: {owner_id})")
                correct_guesses_count = 0
                for guesser_id_str, guessed_user_id_str_calc in guesses.items(): 
                    try:
                        guesser_id = int(guesser_id_str)
                        guessed_user_id_calc = int(guessed_user_id_str_calc) 
                        if guesser_id in user_points and guesser_id != owner_id:
                            if guessed_user_id_calc == owner_id: 
                                correct_guesses_count += 1
                                if owner_id == leader_just_finished: 
                                    user_points[guesser_id] += 3
                                    print(f"    Игрок {guesser_id} угадал ВЕДУЩЕГО {owner_id} --> +3")
                    except (ValueError, TypeError): 
                        print(f"    Помилка конвертації ID в припущенні: guesser='{guesser_id_str}', guessed='{guessed_user_id_str_calc}'")
                        continue 
                num_potential_guessers = num_all_users - 1 if num_all_users > 0 else 0
                if owner_id == leader_just_finished: 
                    print(f"    --- Обработка очков ВЕДУЩЕГО {owner_id} ---")
                    print(f"      Правильных угадываний карты ведущего: {correct_guesses_count}, Потенциальных угадывающих: {num_potential_guessers}")
                    if num_potential_guessers > 0: 
                        if correct_guesses_count == num_potential_guessers: 
                            print(f"      Все ({correct_guesses_count}) угадали Ведущего {owner_id}.")
                            stop_processing = True 
                            try:
                                c.execute("UPDATE users SET rating = MAX(0, rating - 3) WHERE id = ?", (owner_id,))
                                print(f"      !!! Ведущий {owner_id}: рейтинг = MAX(0, рейтинг - 3) (Правило 1) !!!")
                            except sqlite3.Error as direct_update_err:
                                print(f"!!! ОШИБКА обновления рейтинга Ведущего {owner_id} по Правилу 1: {direct_update_err}")
                                flash(f"Ошибка БД при обновлении рейтинга Ведущего (ID: {owner_id}) по Правилу 1.", "danger")
                                db.rollback()
                                return redirect(url_for("admin"))
                            print("      !!! Начисление очков ОСТАНОВЛЕНО (Правило 1) !!!")
                            break 
                        elif correct_guesses_count == 0: 
                            points_to_add = -2 
                            user_points[owner_id] += points_to_add
                            print(f"      Никто не угадал. Ведущий {owner_id} --> {points_to_add} (будет учтен порог 0 при обновлении).")
                        else: 
                            points_for_leader = 3 + correct_guesses_count
                            user_points[owner_id] += points_for_leader
                            print(f"      {correct_guesses_count} угадали (не все). Ведущий {owner_id} --> +3 + {correct_guesses_count} = +{points_for_leader}.")
                    else: 
                        user_points[owner_id] += -2
                        print(f"      Нет потенциальных угадывающих. Ведущий {owner_id} --> -2 (будет учтен порог 0).")
                else: 
                    if correct_guesses_count > 0: 
                        user_points[owner_id] += correct_guesses_count
                        print(f"    Карта НЕ Ведущего {owner_id}: Владелец --> +{correct_guesses_count} (за каждое угадывание его карты).")
        print("\n--- Обновление рейтинга ---")
        if stop_processing: 
            flash_msg_rule1 = f"Подсчет очков остановлен (Правило 1: все угадали карту Ведущего ID {leader_just_finished}). "
            flash_msg_rule1 += f"Ведущему {get_user_name(leader_just_finished) or leader_just_finished} изменено -3 очка (но не ниже 0)."
            flash(flash_msg_rule1, "info")
        else: 
            for user_id_update_rating, points_to_update in user_points.items(): 
                if points_to_update != 0: 
                    try:
                        user_name_update_rating = get_user_name(user_id_update_rating) or f"ID {user_id_update_rating}" 
                        print(f"  Обновление пользователя {user_id_update_rating} ({user_name_update_rating}): {points_to_update:+}")
                        c.execute("UPDATE users SET rating = MAX(0, rating + ?) WHERE id = ?", (points_to_update, user_id_update_rating))
                        points_summary.append(f"{user_name_update_rating}: {points_to_update:+}")
                    except sqlite3.Error as e_update_rating: 
                        print(f"!!! ОШИБКА обновления рейтинга для {user_id_update_rating}: {e_update_rating}")
                        flash(f"Ошибка обновления рейтинга для пользователя ID {user_id_update_rating}", "danger")
                        db.rollback()
                        print("  !!! Транзакция отменена !!!")
                        return redirect(url_for("admin"))
        next_leading_user_id = None
        try:
            c.execute("SELECT id FROM users ORDER BY id") 
            user_ids_ordered = [int(row['id']) for row in c.fetchall()]
        except sqlite3.Error as e_get_users: 
            print(f"Error getting user IDs for next leader: {e_get_users}")
            flash("Ошибка БД при определении следующего ведущего.", "danger")
            if not stop_processing: db.rollback() 
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
                 print(f"Предупреждение: ID ведущего {leader_just_finished} не найден в списке активных игроков.")
                 next_leading_user_id = user_ids_ordered[0] if user_ids_ordered else None
        elif user_ids_ordered: 
             next_leading_user_id = user_ids_ordered[0]
        if next_leading_user_id is not None:
            if set_leading_user_id(next_leading_user_id):
                next_leader_name_display = get_user_name(next_leading_user_id) or f"ID {next_leading_user_id}" 
                if not stop_processing:
                     flash(f"Подсчет очков завершен. Следующий ведущий: {next_leader_name_display}.", "success")
            else:
                 flash("Критическая ошибка: не удалось сохранить нового ведущего.", "danger")
                 if not stop_processing: db.rollback() 
                 return redirect(url_for("admin"))
        else: 
             flash("Не удалось определить следующего ведущего (нет активных игроков).", "warning")
             set_leading_user_id(None)
        if points_summary and not stop_processing:
            flash(f"Изменение очков: {'; '.join(points_summary)}", "info")
        elif not stop_processing and not points_summary and table_images : 
             flash("В этом раунде очки не изменились (кроме возможного штрафа Ведущему по Правилу 1).", "info")
        db.commit() 
        print("--- Подсчет очков и обновление завершены успешно ---")
    except sqlite3.Error as e_main_try_sqlite: 
        db.rollback()
        flash(f"Ошибка базы данных во время обработки раунда: {e_main_try_sqlite}", "danger")
        print(f"Database error in open_cards: {e_main_try_sqlite}")
        print(traceback.format_exc())
        return redirect(url_for("admin", displayed_leader_id=leader_just_finished)) 
    except Exception as e_main_try_exception: 
        db.rollback()
        flash(f"Непредвиденная ошибка во время обработки раунда: {type(e_main_try_exception).__name__} - {e_main_try_exception}", "danger")
        print(f"Unexpected error in open_cards: {e_main_try_exception}")
        print(traceback.format_exc())
        return redirect(url_for("admin", displayed_leader_id=leader_just_finished))
    return redirect(url_for("admin", displayed_leader_id=leader_just_finished))

@app.route("/new_round", methods=["POST"])
def new_round():
    if g.game_over: # Перевірка, чи гра вже не закінчена
        flash("Игра уже окончена. Начать новый раунд нельзя.", "warning")
        return redirect(url_for('admin'))
    
    db = get_db() 
    c = db.cursor()
    active_subfolder_new_round = get_setting('active_subfolder') 
    current_leader_id_new_round = get_leading_user_id() 

    try:
        if current_leader_id_new_round:
            leader_name_new_round = get_user_name(current_leader_id_new_round) or f"ID {current_leader_id_new_round}" 
            flash(f"Новый раунд начат. Ведущий: {leader_name_new_round}.", "info")
        else:
            flash("Новый раунд начат. Ведущий не определен (возможно, это первый раунд после старта игры без игроков).", "warning") 

        # Скидання карт зі столу: owner_id = NULL, guesses = '{}', status = 'Занято:Админ'
        c.execute("UPDATE images SET owner_id = NULL, guesses = '{}', status = 'Занято:Админ' WHERE status LIKE 'На столе:%'")
        table_cleared_count = c.rowcount
        
        # Скидання guesses у карт, що НЕ були на столі (в руках гравців)
        c.execute("UPDATE images SET guesses = '{}' WHERE status NOT LIKE 'На столе:%' AND guesses != '{}'")
        guesses_cleared_count = c.rowcount

        set_setting("show_card_info", "false")
        flash("Информация о картах скрыта.", "info")
        if table_cleared_count > 0:
            flash(f"Карты со стола ({table_cleared_count} шт.) убраны и очищены (статус 'Занято:Админ').", "info")
        if guesses_cleared_count > 0:
            flash(f"Сброшены прочие предположения ({guesses_cleared_count} карт).", "info")

        c.execute("SELECT id FROM users ORDER BY id")
        user_ids_new_round = [row['id'] for row in c.fetchall()] 

        if not user_ids_new_round:
            flash("Нет пользователей для раздачи карт.", "warning")
        elif not active_subfolder_new_round:
            flash("Активная колода не установлена. Новые карты не розданы.", "warning")
        else:
            # ЗМІНЕНО: Вибираємо для роздачі тільки карти зі статусом 'Свободно'
            c.execute("SELECT id FROM images WHERE subfolder = ? AND status = 'Свободно'", 
                      (active_subfolder_new_round,))
            available_cards_ids = [row['id'] for row in c.fetchall()]
            random.shuffle(available_cards_ids)

            num_users_new_round = len(user_ids_new_round) 
            num_available_new_round = len(available_cards_ids) 
            # Роздаємо по одній карті кожному гравцю, якщо є карти і гравці
            num_to_deal_per_player = 1 
            
            cards_actually_dealt_total = 0
            if num_available_new_round == 0:
                 # ЗМІНЕНО: Оновлене повідомлення
                flash(f"Нет доступных карт (статус 'Свободно') в колоде '{active_subfolder_new_round}' для раздачи.", "warning")
            else:
                for user_id_nr_deal in user_ids_new_round: # Змінено user_id_new_round_deal на user_id_nr_deal
                    if cards_actually_dealt_total < num_available_new_round : # Перевірка, чи є ще карти для роздачі
                        card_id_nr_deal = available_cards_ids[cards_actually_dealt_total] # Беремо наступну доступну карту, Змінено card_id_new_round_deal
                        c.execute("UPDATE images SET status = ? WHERE id = ?", 
                                  (f"Занято:{user_id_nr_deal}", card_id_nr_deal))
                        cards_actually_dealt_total +=1
                    else:
                        # Карти скінчились раніше, ніж всі гравці отримали
                        flash(f"Внимание: Свободные карты в колоде '{active_subfolder_new_round}' закончились. Роздано {cards_actually_dealt_total} карт.", "warning")
                        break 
            
            if cards_actually_dealt_total > 0: 
                flash(f"Роздано {cards_actually_dealt_total} новых карт из '{active_subfolder_new_round}'.", "info")
            # elif num_users_new_round > 0 and num_available_new_round > 0 (ця умова вже не потрібна через перевірку вище)

        # Перевірка завершення гри (якщо у когось закінчились карти)
        game_over_now = False
        if user_ids_new_round: 
            for user_id_check_game_over in user_ids_new_round: 
                c.execute("SELECT COUNT(*) FROM images WHERE status = ?", (f"Занято:{user_id_check_game_over}",))
                card_count = c.fetchone()[0]
                if card_count == 0:
                    game_over_now = True
                    flash(f"У игрока {get_user_name(user_id_check_game_over) or ('ID '+str(user_id_check_game_over))} закончились карты!", "info")
                    break 
        if game_over_now:
            set_game_over(True) 
            g.game_over = True 
            flash("Игра окончена! У одного из игроков закончились карты.", "danger")
        
        db.commit()
    except sqlite3.Error as e_new_round_sqlite: 
        db.rollback()
        flash(f"Ошибка базы данных при начале нового раунда: {e_new_round_sqlite}", "danger") 
    except Exception as e_new_round_exception: 
        db.rollback()
        flash(f"Непредвиденная ошибка при начале нового раунда: {e_new_round_exception}", "danger")
        print(f"Unexpected error in new_round: {e_new_round_exception}")
        print(traceback.format_exc()) # Додано для кращої діагностики
        
    return redirect(url_for('admin', displayed_leader_id=current_leader_id_new_round))
# --- Запуск приложения ---
if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        print("База данных не найдена. Инициализация...")
        init_db()
        print("База данных инициализирована.")
    else:
        print("База данных найдена.")
        if get_setting('active_subfolder') is None:
             db_conn_check = sqlite3.connect(DB_PATH)
             cursor_check = db_conn_check.cursor()
             cursor_check.execute("SELECT DISTINCT subfolder FROM images ORDER BY subfolder LIMIT 1")
             first_folder = cursor_check.fetchone()
             db_conn_check.close()
             default_folder = first_folder[0] if first_folder else 'koloda1'
             set_setting('active_subfolder', default_folder) 
             print(f"Установлена активная колода по умолчанию: {default_folder}")
        if get_setting('show_card_info') is None:
             set_setting('show_card_info', 'false')
             print("Установлена настройка show_card_info по умолчанию: false")
        if get_setting('game_over') is None: 
             set_setting('game_over', 'false')
             print("Установлена настройка game_over по умолчанию: false")

    # --- ДОДАНО: Ініціалізація ігрового поля при першому запуску ---
    if not _current_game_board_pole_image_config: 
         print("Первичная инициализация визуализации игрового поля при запуске приложения...")
         all_users_at_startup = []
         if os.path.exists(DB_PATH): # Тільки якщо БД існує, намагаємося отримати користувачів
             try:
                 # Використовуємо get_db для отримання з'єднання в контексті g, якщо це можливо,
                 # але оскільки це поза контекстом запиту, краще створити нове тимчасове з'єднання.
                 conn_startup = sqlite3.connect(DB_PATH)
                 conn_startup.row_factory = sqlite3.Row
                 cursor_startup = conn_startup.cursor()
                 cursor_startup.execute("SELECT id, name, rating FROM users")
                 all_users_at_startup = cursor_startup.fetchall()
                 conn_startup.close()
             except sqlite3.Error as e_startup_sql:
                 print(f"Помилка читання користувачів для ініціалізації поля при старті: {e_startup_sql}")
         
         initialize_new_game_board_visuals(all_users_for_rating_check=all_users_at_startup if all_users_at_startup else None)
    # --- Кінець додавання ---

    port = int(os.environ.get("PORT", 5000))
    debug_mode = os.environ.get("FLASK_DEBUG", "False").lower() in ['true', '1', 't']
    print(f"Запуск Flask приложения на порту {port} с debug={debug_mode}")
    app.run(host="0.0.0.0", port=port, debug=debug_mode)
