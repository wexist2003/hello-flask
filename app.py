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
    """Обработчик для корневого URL (стартовой страницы)."""
    deck_votes_data = []
    db = get_db()
    c = db.cursor()
    try:
        c.execute('''
            SELECT
                i.subfolder,
                COALESCE(dv.votes, 0) as votes
            FROM (SELECT DISTINCT subfolder FROM images ORDER BY subfolder) as i
            LEFT JOIN deck_votes as dv ON i.subfolder = dv.subfolder;
        ''')
        deck_votes_data = c.fetchall() 
        deck_votes_data = [dict(row) for row in deck_votes_data]

    except sqlite3.Error as e:
        print(f"Ошибка чтения данных для голосования на стартовой странице: {e}")
        flash(f"Не удалось загрузить данные о колодах: {e}", "danger")
        deck_votes_data = [] 

    # Получаем текущий голос пользователя из сессии
    current_vote = session.get('voted_for_deck') 

    # Рендерим HTML-шаблон, передавая данные о голосовании и текущий голос пользователя
    return render_template("index.html", 
                           deck_votes=deck_votes_data, 
                           current_vote=current_vote) # <--- Добавлено
    
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
        c.execute("DROP TABLE IF EXISTS deck_votes")        
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
        c.execute("""
            CREATE TABLE deck_votes ( subfolder TEXT PRIMARY KEY, votes INTEGER DEFAULT 0 )""")        
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

def determine_new_leader(current_leader_id):
    """Определяет следующего ведущего по порядку ID."""
    db_local = get_db() # Используем get_db для консистентности
    c_local = db_local.cursor()
    try:
        c_local.execute("SELECT id FROM users ORDER BY id ASC")
        user_rows = c_local.fetchall()
        if not user_rows:
            print("determine_new_leader: Нет пользователей для выбора ведущего.")
            return None # Нет пользователей, нет ведущего

        user_ids = [row['id'] for row in user_rows]

        if current_leader_id is None or current_leader_id not in user_ids:
            # Если текущий ведущий не определен или не найден, выбираем первого в списке
            print(f"determine_new_leader: Текущий ведущий не определен или не найден. Выбираем первого: {user_ids[0]}")
            return user_ids[0]
        
        try:
            current_index = user_ids.index(current_leader_id)
            next_index = (current_index + 1) % len(user_ids) # Переход по кругу
            print(f"determine_new_leader: Текущий: {current_leader_id} (индекс {current_index}). Следующий: {user_ids[next_index]} (индекс {next_index})")
            return user_ids[next_index]
        except ValueError:
            # Этого не должно произойти, если current_leader_id был в user_ids
            print(f"determine_new_leader: Ошибка - ID текущего ведущего {current_leader_id} не найден в списке {user_ids} после проверки. Выбираем первого.")
            return user_ids[0] # Возвращаем первого как запасной вариант
            
    except sqlite3.Error as e:
        print(f"Database error in determine_new_leader: {e}")
        return None 
    except Exception as e_gen:
        print(f"Unexpected error in determine_new_leader: {e_gen}")
        print(traceback.format_exc())
        return None
        
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

@app.route('/login_player')
def login_player():
    """
    Отображает страницу для ввода имени игрока ИЛИ, если игрок уже 
    'залогинен' в сессии, перенаправляет его на его страницу.
    """
    # Проверяем, есть ли код пользователя в текущей сессии
    user_code_from_session = session.get('user_code') 
    
    if user_code_from_session:
        # Если код есть, значит пользователь уже входил/регистрировался в этой сессии
        print(f"Player session found (code: {user_code_from_session}). Redirecting to user page.")
        # Перенаправляем сразу на его страницу
        return redirect(url_for('user', code=user_code_from_session))
    else:
        # Если кода в сессии нет, показываем форму для входа/регистрации
        print("No active player session found. Showing login/registration form.")
        return render_template('login_player.html')

@app.route('/register_or_login_player', methods=['POST'])
@app.route('/register_or_login_player', methods=['POST'])
def register_or_login_player():
    """
    Обрабатывает ввод имени игрока: регистрирует нового 
    или находит существующего.
    """
    player_name = request.form.get('name', '').strip()

    if not player_name:
        flash("Имя не может быть пустым.", "warning")
        return redirect(url_for('login_player'))

    if len(player_name) > 50:
        flash("Имя слишком длинное (максимум 50 символов).", "warning")
        return redirect(url_for('login_player'))

    db = get_db()
    c = db.cursor()
    user_code = None
    user_id = None

    try:
        # Ищем пользователя по имени
        c.execute("SELECT id, code FROM users WHERE name = ?", (player_name,))
        existing_user = c.fetchone()

        if existing_user:
            # Пользователь найден - просто впускаем
            user_id = existing_user['id']
            user_code = existing_user['code']
            print(f"PLAYER_LOGIN: Found existing user '{player_name}' with code '{user_code}'")
            flash(f"С возвращением, {player_name}!", "info")
        else:
            # Пользователь НЕ найден - РЕГИСТРИРУЕМ НОВОГО пользователя
            # УДАЛЕНА ПРОВЕРКА if session.get('has_registered_player'):
            
            print(f"PLAYER_LOGIN: Registering new user '{player_name}'")
            user_code = generate_unique_code()
            try:
                c.execute("INSERT INTO users (name, code) VALUES (?, ?)", (player_name, user_code))
                user_id = c.lastrowid
                db.commit()
                
                # УДАЛЕНА СТРОКА session['has_registered_player'] = True 

                print(f"PLAYER_LOGIN: User '{player_name}' registered with code '{user_code}' and ID {user_id}")
                flash(f"Добро пожаловать, {player_name}! Ваша уникальная ссылка создана.", "success")
            except sqlite3.IntegrityError:
                db.rollback()
                flash("Произошла ошибка при регистрации (возможно, код не уникален). Попробуйте еще раз.", "danger")
                print(f"PLAYER_LOGIN_ERROR: IntegrityError during registration for '{player_name}'")
                return redirect(url_for('login_player'))
            except sqlite3.Error as e_insert:
                 db.rollback()
                 flash(f"Ошибка базы данных при регистрации: {e_insert}", "danger")
                 print(f"PLAYER_LOGIN_ERROR: DB Error during registration for '{player_name}': {e_insert}")
                 return redirect(url_for('login_player'))

        # Если есть код пользователя (новый или существующий)
        if user_code:
            # Устанавливаем основную информацию пользователя в сессию
            session['user_id'] = user_id
            session['user_name'] = player_name
            session['user_code'] = user_code
            session.pop('is_admin', None) 

            # Перенаправляем на страницу пользователя
            return redirect(url_for('user', code=user_code))
        else:
            # Сюда не должны попасть, если логика верна
            flash("Произошла неизвестная ошибка при обработке вашего запроса.", "danger")
            return redirect(url_for('login_player'))

    except sqlite3.Error as e:
        print(f"PLAYER_LOGIN_ERROR: DB Error for name '{player_name}': {e}")
        flash(f"Ошибка базы данных: {e}", "danger")
        return redirect(url_for('login_player'))
    except Exception as e_general:
        print(f"PLAYER_LOGIN_ERROR: Unexpected error for name '{player_name}': {e_general}")
        print(traceback.format_exc()) # Для более детального лога на сервере
        flash(f"Произошла непредвиденная ошибка: {e_general}", "danger")
        return redirect(url_for('login_player'))
        
@app.route('/vote_deck', methods=['POST'])
def vote_deck():
    """Обрабатывает голос за выбранную колоду, отменяя предыдущий голос пользователя."""
    new_deck = request.form.get('subfolder')
    previous_deck = session.get('voted_for_deck') # Получаем предыдущий голос из сессии

    if not new_deck:
        flash("Ошибка: Колода для голосования не была выбрана.", "warning")
        return redirect(url_for('index'))

    # Если пользователь голосует за ту же колоду, ничего не делаем
    if new_deck == previous_deck:
        flash(f"Вы уже голосовали за колоду '{new_deck}'.", "info")
        return redirect(url_for('index'))

    db = get_db()
    c = db.cursor()
    try:
        # Начинаем транзакцию (хотя для SQLite это не так критично, как для других БД)

        # 1. Уменьшаем счетчик для предыдущей колоды, если она была
        if previous_deck:
             # Используем MAX(0, votes - 1), чтобы счетчик не стал отрицательным
             c.execute("UPDATE deck_votes SET votes = MAX(0, votes - 1) WHERE subfolder = ?", (previous_deck,))
             print(f"DEBUG_VOTE: Decremented vote for '{previous_deck}'") # Отладка

        # 2. Увеличиваем счетчик для новой колоды
        # Убедимся, что запись для новой колоды существует
        c.execute("INSERT OR IGNORE INTO deck_votes (subfolder, votes) VALUES (?, 0)", (new_deck,))
        # Увеличиваем счетчик
        c.execute("UPDATE deck_votes SET votes = votes + 1 WHERE subfolder = ?", (new_deck,))
        print(f"DEBUG_VOTE: Incremented vote for '{new_deck}'") # Отладка

        db.commit() # Фиксируем изменения

        # 3. Сохраняем новый выбор в сессию
        session['voted_for_deck'] = new_deck
        print(f"DEBUG_VOTE: Session updated. voted_for_deck = '{new_deck}'") # Отладка

        flash(f"Ваш голос за колоду '{new_deck}' учтен!", "success")

    except sqlite3.Error as e:
        db.rollback() # Откатываем изменения при ошибке
        print(f"Ошибка записи голоса (с отменой) за колоду '{new_deck}': {e}")
        flash(f"Не удалось учесть голос: {e}", "danger")
    except Exception as e_general: # Ловим другие возможные ошибки
         print(f"Неожиданная ошибка при голосовании: {e_general}")
         flash("Произошла непредвиденная ошибка при голосовании.", "danger")


    return redirect(url_for('index')) # Возвращаемся на стартовую страницу
    
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

    # Ведущий, который актуален в БД (завершил раунд или текущий для нового раунда)
    current_leader_from_db = get_leading_user_id()
    # Кто будет следующим ПОСЛЕ того, кто сейчас в БД
    potential_next_leader_id = determine_new_leader(current_leader_from_db)

    # ID для "фокуса" на странице, может быть переопределен параметром URL.
    # Это влияет на то, какой ведущий будет подсвечен или выбран в формах, если такая логика есть.
    # По умолчанию фокусируемся на том, кто сейчас ведущий в БД.
    leader_to_focus_on_id = current_leader_from_db
    if request.method == "GET":
        displayed_leader_id_from_url_str = request.args.get('displayed_leader_id')
        if displayed_leader_id_from_url_str:
            try:
                leader_to_focus_on_id = int(displayed_leader_id_from_url_str)
            except (ValueError, TypeError):
                # Если ID из URL некорректен, оставляем фокус на current_leader_from_db
                pass

    current_active_subfolder = get_setting('active_subfolder') or ''
    # g.show_card_info и g.game_over устанавливаются в @app.before_request

    if request.method == "POST":
        action_handled = False
        # leader_for_redirect будет ID, который нужно передать в URL после POST,
        # чтобы страница админа сфокусировалась на правильном ведущем.
        leader_for_redirect = leader_to_focus_on_id # Изначально

        try:
            # --- ОБРАБОТКА POST-ЗАПРОСОВ ---
            # (Здесь должна быть ваша полная логика из файла app (1).py для обработки POST)

            # Пример: Создание пользователя (из вашего файла app (1).py)
            if "name" in request.form and "num_cards" in request.form and not "delete_user_id" in request.form and not "active_subfolder" in request.form and not "toggle_show_card_info" in request.form and not "reset_game_board_visuals" in request.form:
                name_admin_form = request.form.get("name", "").strip()
                try:
                    num_cards_admin_form = int(request.form.get("num_cards", 3))
                    if num_cards_admin_form < 0:
                        flash("Количество карт для раздачи не может быть отрицательным.", "warning")
                        num_cards_admin_form = 0 # или другое значение по умолчанию
                except ValueError:
                    flash("Некорректное количество карт. Установлено значение по умолчанию (3).", "warning")
                    num_cards_admin_form = 3

                if not name_admin_form:
                    flash("Имя пользователя не может быть пустым.", "warning")
                else:
                    try:
                        user_code = generate_unique_code()
                        c.execute("INSERT INTO users (name, code, rating) VALUES (?, ?, 0)", (name_admin_form, user_code))
                        user_id_admin_form = c.lastrowid
                        flash(f"Пользователь '{name_admin_form}' (код: {user_code}) добавлен.", "success")
                        
                        if get_leading_user_id() is None:
                            set_leading_user_id(user_id_admin_form)
                            leader_for_redirect = user_id_admin_form # Обновляем для фокуса
                            flash(f"'{name_admin_form}' назначен ведущим.", "info")

                        if current_active_subfolder and num_cards_admin_form > 0:
                            c.execute("SELECT id FROM images WHERE subfolder = ? AND status = 'Свободно' ORDER BY RANDOM() LIMIT ?", (current_active_subfolder, num_cards_admin_form))
                            cards_to_deal = c.fetchall()
                            if len(cards_to_deal) < num_cards_admin_form:
                                flash(f"Внимание: Недостаточно свободных карт в колоде '{current_active_subfolder}'. Роздано {len(cards_to_deal)}.", "warning")
                            for card_admin_deal in cards_to_deal:
                                c.execute("UPDATE images SET status = ?, owner_id = ? WHERE id = ?", (f"Занято:{user_id_admin_form}", user_id_admin_form, card_admin_deal['id']))
                            flash(f"Пользователю '{name_admin_form}' роздано {len(cards_to_deal)} карт(ы) из колоды '{current_active_subfolder}'.", "info")
                        elif num_cards_admin_form > 0:
                            flash("Активная колода не выбрана, карты новому пользователю не розданы.", "warning")
                        
                        db.commit()
                        action_handled = True
                    except sqlite3.IntegrityError:
                        db.rollback()
                        flash("Пользователь с таким именем или кодом уже существует.", "danger")
                    except sqlite3.Error as e_sql_user_add:
                        db.rollback()
                        flash(f"Ошибка базы данных при добавлении пользователя: {e_sql_user_add}", "danger")

            # Пример: Смена активной колоды (из вашего файла app (1).py)
            elif "active_subfolder" in request.form:
                new_active_subfolder = request.form.get("active_subfolder")
                if new_active_subfolder:
                    set_setting("active_subfolder", new_active_subfolder)
                    db.commit()
                    flash(f"Активная колода изменена на '{new_active_subfolder}'.", "success")
                    action_handled = True
                else:
                    flash("Не выбрана колода для установки.", "warning")
            
            # Пример: Удаление пользователя (из вашего файла app (1).py)
            elif "delete_user_id" in request.form:
                user_id_to_delete_str = request.form.get("delete_user_id")
                try:
                    user_id_to_delete = int(user_id_to_delete_str)
                    user_to_delete_name = get_user_name(user_id_to_delete) or f"ID {user_id_to_delete}"
                    
                    current_leader_before_delete = get_leading_user_id()
                    
                    # Освобождаем карты пользователя
                    c.execute("UPDATE images SET status = 'Свободно', owner_id = NULL WHERE owner_id = ?", (user_id_to_delete,))
                    # Удаляем пользователя
                    c.execute("DELETE FROM users WHERE id = ?", (user_id_to_delete,))
                    
                    flash(f"Пользователь '{user_to_delete_name}' и его карты удалены/освобождены.", "success")

                    if current_leader_before_delete == user_id_to_delete:
                        flash(f"Удаленный пользователь '{user_to_delete_name}' был ведущим.", "info")
                        # Определяем нового ведущего из оставшихся
                        new_leader_after_delete = determine_new_leader(user_id_to_delete) # Передаем ID удаленного, чтобы функция нашла следующего
                        set_leading_user_id(new_leader_after_delete)
                        leader_for_redirect = new_leader_after_delete # Обновляем для фокуса
                        if new_leader_after_delete is not None:
                            flash(f"Новым ведущим назначен '{get_user_name(new_leader_after_delete) or f'ID {new_leader_after_delete'}'}.", "info")
                        else:
                            flash("Не осталось игроков для назначения ведущего.", "warning")
                    
                    db.commit()
                    action_handled = True
                except ValueError:
                    flash("Некорректный ID пользователя для удаления.", "danger")
                except sqlite3.Error as e_sql_user_delete:
                    db.rollback()
                    flash(f"Ошибка базы данных при удалении пользователя: {e_sql_user_delete}", "danger")

            # Переключение видимости информации о картах (из вашего файла)
            elif 'toggle_show_card_info' in request.form:
                current_show_info = get_setting('show_card_info') == 'true'
                new_show_info = not current_show_info
                set_setting('show_card_info', str(new_show_info))
                db.commit()
                flash(f"Отображение информации о картах {'включено' if new_show_info else 'выключено'}.", "info")
                action_handled = True
            
            # Сброс игрового поля (из вашего файла)
            elif 'reset_game_board_visuals' in request.form:
                c.execute("SELECT id, name, rating FROM users")
                all_users_for_reset = c.fetchall()
                initialize_new_game_board_visuals(all_users_for_rating_check=all_users_for_reset) # Передаем список пользователей
                flash("Визуализация игрового поля была сброшена и перестроена.", "success")
                action_handled = True # Не требует редиректа, так как это фоновое действие

            if action_handled:
                # Если leader_for_redirect остался None (например, после удаления последнего ведущего),
                # передаем пустую строку, чтобы избежать None в URL.
                return redirect(url_for('admin', displayed_leader_id=leader_for_redirect if leader_for_redirect is not None else ''))

        except sqlite3.Error as e_sql_post:
            db.rollback()
            flash(f"Ошибка базы данных при обработке POST-запроса: {e_sql_post}", "danger")
            print(traceback.format_exc())
        except Exception as e_general_post:
            db.rollback()
            flash(f"Непредвиденная ошибка при обработке POST-запроса: {e_general_post}", "danger")
            print(traceback.format_exc())
        # --- КОНЕЦ ОБРАБОТКИ POST-ЗАПРОСОВ ---

    # --- Получение данных для отображения (GET-запрос или после неудачного POST) ---
    users_for_template = []
    images_for_template = []
    subfolders_for_template = []
    all_guesses_for_template = {}
    free_image_count_for_template = 0
    image_owners_for_template = {} # Добавлено для вашего кода
    guess_counts_by_user_for_template = {} # Добавлено для вашего кода
    user_has_duplicate_guesses_for_template = {} # Добавлено для вашего кода
    game_board_data_for_template = []


    try:
        c.execute("SELECT id, name, code, rating FROM users ORDER BY name ASC")
        users_for_template_rows = c.fetchall()
        users_for_template = [dict(row) for row in users_for_template_rows]


        game_board_data_for_template = generate_game_board_data_for_display(users_for_template)

        c.execute("SELECT id, subfolder, image, status, owner_id, guesses FROM images ORDER BY subfolder, id")
        images_rows = c.fetchall()

        for img_row_dict in images_rows: # img_row уже dict благодаря row_factory
            img_row = dict(img_row_dict) # Копируем, чтобы не изменять оригинал, если он используется где-то еще
            guesses_json_str = img_row.get('guesses') or '{}'
            try:
                 guesses_dict = json.loads(guesses_json_str)
            except json.JSONDecodeError:
                 guesses_dict = {}

            img_row['guesses'] = guesses_dict # Заменяем JSON-строку на словарь
            images_for_template.append(img_row)

            if img_row.get('owner_id') is not None:
                image_owners_for_template[img_row['id']] = img_row['owner_id'] # Заполнение image_owners
            if img_row.get('status') == 'Свободно' and img_row.get('subfolder') == current_active_subfolder:
                free_image_count_for_template += 1
            if guesses_dict: # Если словарь предположений не пуст
                 all_guesses_for_template[img_row['id']] = guesses_dict # Заполнение all_guesses

        # Подсчет и проверка дубликатов предположений (как было в вашем файле app (1).py)
        if users_for_template:
            user_has_duplicate_guesses_for_template = {user_item['id']: False for user_item in users_for_template}
            guess_counts_by_user_for_template = {user_item['id']: 0 for user_item in users_for_template}

            if all_guesses_for_template:
                for user_item in users_for_template:
                    user_id_str = str(user_item['id'])
                    guesses_made_by_user = [] # Список ID пользователей, на которых гадал текущий user_item
                    for image_id_admin_get, guesses_for_image in all_guesses_for_template.items():
                        if user_id_str in guesses_for_image:
                            # guesses_for_image[user_id_str] это ID того, на кого сделано предположение
                            guesses_made_by_user.append(guesses_for_image[user_id_str])
                            # Увеличиваем счетчик предположений для user_item
                            guess_counts_by_user_for_template[user_item['id']] += 1
                    
                    # Проверка на дубликаты в списке ID, на которых гадал user_item
                    if len(guesses_made_by_user) > len(set(guesses_made_by_user)):
                        user_has_duplicate_guesses_for_template[user_item['id']] = True
        
        c.execute("SELECT DISTINCT subfolder FROM images ORDER BY subfolder")
        subfolders_from_db = c.fetchall()
        subfolders_for_template = [row['subfolder'] for row in subfolders_from_db]
        if not subfolders_for_template: # Проверка, если в БД нет колод, но есть папки
            img_path = os.path.join('static', 'images')
            if os.path.exists(img_path):
                # Проверяем наличие стандартной колоды, если из БД ничего не пришло
                if os.path.isdir(os.path.join(img_path, 'koloda1')) and 'koloda1' not in subfolders_for_template:
                     subfolders_for_template.append('koloda1')
                # Можно добавить и другие существующие папки, если это необходимо
                # for item in os.listdir(img_path):
                #    if os.path.isdir(os.path.join(img_path, item)) and item not in subfolders_for_template:
                #        subfolders_for_template.append(item)
                # subfolders_for_template.sort()


    except sqlite3.Error as e_sql_get:
        flash(f"Ошибка чтения данных для отображения: {e_sql_get}", "danger")
        print(traceback.format_exc())
    except Exception as e_general_get:
         flash(f"Непредвиденная ошибка при чтении данных: {e_general_get}", "danger")
         print(traceback.format_exc())


    return render_template("admin.html",
                           users=users_for_template,
                           images=images_for_template,
                           subfolders=subfolders_for_template,
                           active_subfolder=current_active_subfolder,
                           # g.show_card_info и g.game_over доступны глобально в шаблоне
                           db_current_leader_id=current_leader_from_db,
                           admin_focus_leader_id=leader_to_focus_on_id,
                           potential_next_leader_id=potential_next_leader_id,
                           free_image_count=free_image_count_for_template,
                           image_owners=image_owners_for_template, # Передаем image_owners
                           guess_counts_by_user=guess_counts_by_user_for_template, # Передаем guess_counts_by_user
                           all_guesses=all_guesses_for_template, # Передаем all_guesses
                           user_has_duplicate_guesses=user_has_duplicate_guesses_for_template, # Передаем user_has_duplicate_guesses
                           game_board=game_board_data_for_template, # Передаем game_board
                           get_user_name_func=get_user_name,
                           current_num_board_cells=_current_game_board_num_cells
                           )
    
    
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
    new_leader_id_sng = None 
    try:
        print("Сброс рейтингов...")
        c.execute("UPDATE users SET rating = 0")
        print("Сброс состояния карт...")
        # Устанавливаем owner_id в NULL перед тем как пометить неактивные, на всякий случай
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
        
        c.execute("SELECT id, name, rating FROM users") 
        all_users_for_board_init_sng = c.fetchall() 
        initialize_new_game_board_visuals(all_users_for_rating_check=all_users_for_board_init_sng)
        
        db.commit() # Коммит после сброса и инициализации

        c.execute("SELECT id FROM users ORDER BY id")
        user_ids_sng = [row['id'] for row in c.fetchall()] 
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
            for user_id_sng_deal in user_ids_sng: 
                cards_dealt_to_user = 0
                for _ in range(num_cards_per_player):
                    if card_index < num_available:
                        card_id_sng_deal = available_cards_ids[card_index] 
                        # --- ИСПРАВЛЕНО: Обновляем и статус, и owner_id ---
                        c.execute("UPDATE images SET status = ?, owner_id = ? WHERE id = ?", 
                                  (f"Занято:{user_id_sng_deal}", user_id_sng_deal, card_id_sng_deal))
                        # --- КОНЕЦ ИСПРАВЛЕНИЯ ---
                        card_index += 1
                        cards_dealt_to_user += 1
                    else:
                        break 
                print(f"  Пользователю ID {user_id_sng_deal} роздано карт: {cards_dealt_to_user}")
                num_total_dealt += cards_dealt_to_user
                if card_index >= num_available:
                    break 
            
            flash(f"Новая игра начата! Колода: '{selected_deck}'. Роздано карт: {num_total_dealt}.", "success")
            if new_leader_id_sng:
                flash(f"Ведущий назначен: {get_user_name(new_leader_id_sng)}.", "info")
        
        db.commit() # Коммит после раздачи карт
        print("--- Новая игра успешно начата и карты розданы ---")
    except sqlite3.Error as e:
        db.rollback()
        flash(f"Ошибка базы данных при начале новой игры: {e}", "danger")
        print(f"Database error during start_new_game: {e}")
    except Exception as e:
        db.rollback()
        flash(f"Непредвиденная ошибка при начале новой игры: {e}", "danger")
        print(f"Unexpected error during start_new_game: {e}")
        print(traceback.format_exc()) # Добавлено для отладки непредвиденных ошибок
        
    return redirect(url_for('admin', displayed_leader_id=new_leader_id_sng))


    
@app.route('/user/<code>')
def user(code):
    db = get_db()
    c = db.cursor()
    c.execute("SELECT * FROM users WHERE code = ?", (code,))
    g.user = c.fetchone()

    if g.user is None:
        session.pop('user_id', None); session.pop('user_name', None); session.pop('user_code', None)
        flash("Пользователя не найдено или сессия устарела. Пожалуйста, войдите или зарегистрируйтесь.", "warning")
        return redirect(url_for('login_player'))

    session['user_id'] = g.user['id']
    session['user_name'] = g.user['name']
    session['user_code'] = g.user['code']
    session.pop('is_admin', None)

    active_subfolder_row = c.execute("SELECT value FROM settings WHERE key = 'active_subfolder'").fetchone()
    active_subfolder = active_subfolder_row['value'] if active_subfolder_row else None
    # g.show_card_info устанавливается в @app.before_request

    user_cards = []
    if active_subfolder:
        c.execute("""
            SELECT id, image, subfolder FROM images
            WHERE owner_id = ? AND subfolder = ? AND status LIKE 'Занято:%'
        """, (g.user['id'], active_subfolder))
        user_cards = c.fetchall()

    table_cards_raw = []
    if active_subfolder:
        c.execute("""
            SELECT i.id, i.image, i.subfolder, i.owner_id, u.name as owner_name, i.guesses
            FROM images i LEFT JOIN users u ON i.owner_id = u.id 
            WHERE i.subfolder = ? AND i.status LIKE 'На столе:%' 
        """, (active_subfolder,)) # Используем LEFT JOIN на случай, если owner_id есть, а пользователя уже нет
        table_cards_raw = c.fetchall()

    table_cards_for_template = []
    on_table_status = False
    for card_row in table_cards_raw:
        if card_row['owner_id'] == g.user['id']:
            on_table_status = True
        guesses_json_str = card_row['guesses'] or '{}'
        try:
            current_card_guesses_dict = json.loads(guesses_json_str)
        except json.JSONDecodeError:
            current_card_guesses_dict = {}
        my_guess_for_this_card_value = current_card_guesses_dict.get(str(g.user['id']))

        table_cards_for_template.append({
            'id': card_row['id'],
            'image': card_row['image'],
            'subfolder': card_row['subfolder'],
            'owner_id': card_row['owner_id'],
            'owner_name': card_row['owner_name'] if card_row['owner_name'] else "Неизвестный владелец", # Обработка случая, если owner_id есть, а имени нет
            'guesses': current_card_guesses_dict,
            'has_guessed': my_guess_for_this_card_value is not None,
            'guessed_user_id': my_guess_for_this_card_value
        })

    c.execute("SELECT id, name FROM users WHERE id != ?", (g.user['id'],))
    other_users_for_template = c.fetchall()

    leader_id_from_db = get_leading_user_id()
    potential_next_leader_id = determine_new_leader(leader_id_from_db)
    is_current_user_the_db_leader = (g.user['id'] == leader_id_from_db) if leader_id_from_db is not None else False

    all_users_for_board_query = c.execute("SELECT id, name, rating FROM users").fetchall()
    game_board_data = generate_game_board_data_for_display(all_users_for_board_query)
    current_board_cells_value = _current_game_board_num_cells

    return render_template('user.html',
                           name=g.user['name'],
                           rating=g.user['rating'],
                           cards=user_cards,
                           table_images=table_cards_for_template,
                           all_users=other_users_for_template, # Для формы угадывания
                           code=code,
                           on_table=on_table_status,
                           # Передаем ID ведущего из БД и потенциального следующего
                           db_current_leader_id=leader_id_from_db,
                           potential_next_leader_id=potential_next_leader_id,
                           is_current_user_the_db_leader=is_current_user_the_db_leader,
                           game_board=game_board_data,
                           current_num_board_cells=current_board_cells_value
                           # g.show_card_info доступен глобально в шаблоне
                           )
    
        
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

@app.route("/admin/open_cards", methods=["POST"])
def open_cards():
    if not session.get('is_admin'):
        flash('Доступ запрещен.', 'danger')
        return redirect(url_for('login'))

    db = get_db()
    c = db.cursor()
    try:
        # 1. Получаем ID ведущего и настройки
        leading_user_id = get_leading_user_id() # Это ведущий, чей ход завершается
        if leading_user_id is None:
            flash("Ведущий не определен. Невозможно открыть карты.", "warning")
            return redirect(url_for('admin'))

        set_setting("show_card_info", "true") # Показываем информацию
        db.commit() # Коммит изменения настроек

        # ... (шаги 2, 3, 4, 5 - Анализ предположений - остаются без изменений) ...
        # (Код анализа карт и предположений)
        c.execute("SELECT id FROM users")
        all_player_ids = {row['id'] for row in c.fetchall()}
        other_player_ids = all_player_ids - {leading_user_id}
        num_other_players = len(other_player_ids)

        c.execute("""
            SELECT i.id, i.owner_id, i.guesses
            FROM images i
            WHERE i.status LIKE 'На столе:%'
        """)
        cards_on_table = c.fetchall()
        if not cards_on_table:
             flash("На столе нет карт для открытия. Карты просто открыты.", "info") # Изменено сообщение
             # Не выходим, просто не будет очков, если карт нет
        
        leader_card = None
        if cards_on_table: # Ищем карту ведущего только если есть карты на столе
            for card_data in cards_on_table:
                if card_data['owner_id'] == leading_user_id:
                    leader_card = card_data
                    break
        
        # Если карт на столе нет, или карта ведущего не найдена (маловероятно, если ведущий играл)
        # то بخش подсчета очков может быть пропущен или обработан соответствующим образом.
        # Текущая логика предполагает, что карта ведущего должна быть, если он играл.
        # Если ведущий не выложил карту, но раунд завершается, это особый случай,
        # который текущие правила подсчета очков могут не покрывать идеально.
        # Для простоты пока предполагаем, что карта ведущего есть, если есть карты на столе.
        # Если карт на столе нет, leader_card останется None, и блок подсчета очков не выполнится,
        # что может быть приемлемо, так как очков и так не будет.

        leader_card_correct_guessers = set()
        correct_guessers_per_player_card = {} 

        if leader_card: # Продолжаем анализ только если карта ведущего найдена
            for card_data in cards_on_table:
                card_owner_id = card_data['owner_id']
                if card_owner_id is None: continue

                guesses_json_str = card_data['guesses'] or '{}'
                try:
                    guesses_dict = json.loads(guesses_json_str)
                except json.JSONDecodeError:
                    guesses_dict = {}

                for guesser_id_str, guessed_user_id in guesses_dict.items():
                    try:
                        guesser_id = int(guesser_id_str)
                        if guesser_id == card_owner_id: continue 
                        if guesser_id not in all_player_ids: continue

                        if guessed_user_id == card_owner_id: 
                            if card_owner_id == leading_user_id:
                                leader_card_correct_guessers.add(guesser_id)
                            else:
                                if card_owner_id not in correct_guessers_per_player_card:
                                    correct_guessers_per_player_card[card_owner_id] = set()
                                correct_guessers_per_player_card[card_owner_id].add(guesser_id)
                    except ValueError:
                        continue
        
        # 6. Подсчитываем очки согласно правилам
        scores = {player_id: 0 for player_id in all_player_ids}
        if leader_card: # Подсчет очков только если карта ведущего была
            num_leader_correct_guessers = len(leader_card_correct_guessers)

            print(f"--- Подсчет очков ---")
            print(f"Ведущий (завершил ход): {leading_user_id} ({get_user_name(leading_user_id)})")
            print(f"Другие игроки ({num_other_players}): {other_player_ids}")
            print(f"Угадали карту ведущего ({num_leader_correct_guessers}): {leader_card_correct_guessers}")
            print(f"Угадали карты других игроков (владелец -> угадал): {correct_guessers_per_player_card}")

            # --- Правило 2: Карту ведущего угадали ВСЕ ---
            if num_other_players > 0 and num_leader_correct_guessers == num_other_players:
                print("Правило 2: Карту ведущего угадали все.")
                scores[leading_user_id] -= 3
            # --- Правило 3: Карту ведущего НЕ угадал НИКТО ---
            elif num_leader_correct_guessers == 0:
                print("Правило 3: Карту ведущего не угадал никто.")
                scores[leading_user_id] -= 2
                for owner_id, guesser_set in correct_guessers_per_player_card.items():
                     if owner_id != leading_user_id:
                        points_for_owner = len(guesser_set)
                        scores[owner_id] += points_for_owner
                        print(f"  Игрок {owner_id} ({get_user_name(owner_id)}) получает +{points_for_owner} (за угадавших его карту)")
            # --- Правило 1: Карту ведущего угадали НЕКОТОРЫЕ ---
            else: 
                print("Правило 1: Карту ведущего угадали некоторые.")
                scores[leading_user_id] += 3
                scores[leading_user_id] += num_leader_correct_guessers
                print(f"  Ведущий {leading_user_id} ({get_user_name(leading_user_id)}) получает +3 и +{num_leader_correct_guessers}")
                for guesser_id in leader_card_correct_guessers:
                    scores[guesser_id] += 3
                    print(f"  Игрок {guesser_id} ({get_user_name(guesser_id)}) получает +3 (угадал ведущего)")
                for owner_id, guesser_set in correct_guessers_per_player_card.items():
                     if owner_id != leading_user_id:
                        points_for_owner = len(guesser_set)
                        scores[owner_id] += points_for_owner 
                        print(f"  Игрок {owner_id} ({get_user_name(owner_id)}) получает +{points_for_owner} (за угадавших его карту)")
                        
            print(f"Итоговые очки за раунд: {scores}")

        # 7. Применяем очки
        if scores: # Проверяем, есть ли вообще очки для начисления
            points_changed = False
            for user_id_score, points in scores.items():
                if points != 0: 
                    points_changed = True
                    try:
                        c.execute("SELECT id FROM users WHERE id = ?", (user_id_score,))
                        user_exists = c.fetchone()
                        if user_exists:
                            c.execute("UPDATE users SET rating = MAX(0, rating + ?) WHERE id = ?", (points, user_id_score))
                            print(f"  Обновлен рейтинг для ID {user_id_score} ({get_user_name(user_id_score)}): {points:+}")
                        else:
                            print(f"  Warning: Пользователь ID {user_id_score} для начисления очков не найден.")
                    except sqlite3.Error as e_update:
                         print(f"  Ошибка обновления рейтинга для ID {user_id_score}: {e_update}")
            if points_changed:
                db.commit() # Коммит очков
                flash("Очки подсчитаны! Карты открыты.", "success")
            else:
                flash("Карты открыты. Изменений в очках нет.", "info")
        else: # Если scores пустой (например, не было карт на столе)
            flash("Карты открыты. Очки не начислялись.", "info")

        # --- БЛОК ПЕРЕДАЧИ ХОДА УДАЛЕН ОТСЮДА ---

    except sqlite3.Error as e_sql:
        db.rollback() 
        flash(f"Ошибка базы данных при открытии карт: {e_sql}", "danger")
        print(f"Database error in open_cards: {e_sql}")
        print(traceback.format_exc())
    except Exception as e_general:
        db.rollback() 
        flash(f"Непредвиденная ошибка при открытии карт: {e_general}", "danger")
        print(f"Unexpected error in open_cards: {e_general}")
        print(traceback.format_exc())

    return redirect(url_for('admin'))
    

@app.route("/new_round", methods=["POST"])
def new_round():
    if not session.get('is_admin'): # Проверка админа
        flash('Доступ запрещен.', 'danger')
        return redirect(url_for('login'))

    if g.game_over: 
        flash("Игра уже окончена. Начать новый раунд нельзя.", "warning")
        return redirect(url_for('admin'))
    
    db = get_db() 
    c = db.cursor()
    active_subfolder_new_round = get_setting('active_subfolder') 
    
    leader_who_finished_round = get_leading_user_id() # Ведущий завершенного раунда
    new_actual_leader_id = None # Для редиректа

    try:
        # --- ОПРЕДЕЛЕНИЕ И УСТАНОВКА НОВОГО ВЕДУЩЕГО ---
        new_actual_leader_id = determine_new_leader(leader_who_finished_round)
        if new_actual_leader_id is not None:
            set_leading_user_id(new_actual_leader_id)
            db.commit() # Коммит смены ведущего
            new_leader_name_display = get_user_name(new_actual_leader_id) or f"ID {new_actual_leader_id}"
            flash(f"Новый раунд начат! Ведущий: {new_leader_name_display}.", "success")
        else:
            flash("Новый раунд начат, но не удалось определить нового ведущего (нет игроков?).", "warning")
            set_leading_user_id(None) # Сбрасываем ведущего, если не определен
            db.commit()
        # --- КОНЕЦ БЛОКА СМЕНЫ ВЕДУЩЕГО ---

        # Скидання карт зі столу
        c.execute("UPDATE images SET owner_id = NULL, guesses = '{}', status = 'Занято:Админ' WHERE status LIKE 'На столе:%'")
        table_cleared_count = c.rowcount
        
        c.execute("UPDATE images SET guesses = '{}' WHERE status NOT LIKE 'На столе:%' AND guesses != '{}'")
        guesses_cleared_count = c.rowcount

        set_setting("show_card_info", "false")
        db.commit() # Коммит show_card_info
        flash("Информация о картах скрыта.", "info")

        # ... (остальная логика раздачи карт из вашей функции new_round остается здесь) ...
        # Включая проверку на окончание игры и db.commit() в конце этой логики.
        
        c.execute("SELECT id FROM users ORDER BY id")
        user_ids_new_round = [row['id'] for row in c.fetchall()] 

        if not user_ids_new_round:
            flash("Нет пользователей для раздачи карт.", "warning")
        elif not active_subfolder_new_round:
            flash("Активная колода не установлена. Новые карты не розданы.", "warning")
        else:
            c.execute("SELECT id FROM images WHERE subfolder = ? AND status = 'Свободно'", 
                      (active_subfolder_new_round,))
            available_cards_ids = [row['id'] for row in c.fetchall()]
            random.shuffle(available_cards_ids)

            num_available_new_round = len(available_cards_ids) 
            cards_actually_dealt_total = 0
            if num_available_new_round == 0:
                flash(f"Нет доступных карт (статус 'Свободно') в колоде '{active_subfolder_new_round}' для раздачи.", "warning")
            else:
                for user_id_nr_deal in user_ids_new_round: 
                    if cards_actually_dealt_total < num_available_new_round : 
                        card_id_nr_deal = available_cards_ids[cards_actually_dealt_total] 
                        # ВАЖНО: При раздаче карты также устанавливаем owner_id
                        c.execute("UPDATE images SET status = ?, owner_id = ? WHERE id = ?", 
                                  (f"Занято:{user_id_nr_deal}", user_id_nr_deal, card_id_nr_deal))
                        cards_actually_dealt_total +=1
                    else:
                        flash(f"Внимание: Свободные карты в колоде '{active_subfolder_new_round}' закончились. Роздано {cards_actually_dealt_total} карт.", "warning")
                        break 
            
            if cards_actually_dealt_total > 0: 
                flash(f"Роздано {cards_actually_dealt_total} новых карт из '{active_subfolder_new_round}'.", "info")
        
        game_over_now = False
        if user_ids_new_round: 
            for user_id_check_game_over in user_ids_new_round: 
                c.execute("SELECT COUNT(*) FROM images WHERE owner_id = ? AND status LIKE 'Занято:%'", (user_id_check_game_over,)) # Уточнено условие
                card_count = c.fetchone()[0]
                if card_count == 0:
                    game_over_now = True
                    flash(f"У игрока {get_user_name(user_id_check_game_over) or ('ID '+str(user_id_check_game_over))} закончились карты!", "info")
                    break 
        if game_over_now:
            set_game_over(True) 
            g.game_over = True 
            flash("Игра окончена! У одного из игроков закончились карты.", "danger")
        
        db.commit() # Финальный коммит для new_round
    except sqlite3.Error as e_new_round_sqlite: 
        db.rollback()
        flash(f"Ошибка базы данных при начале нового раунда: {e_new_round_sqlite}", "danger") 
    except Exception as e_new_round_exception: 
        db.rollback()
        flash(f"Непредвиденная ошибка при начале нового раунда: {e_new_round_exception}", "danger")
        print(f"Unexpected error in new_round: {e_new_round_exception}")
        print(traceback.format_exc())
        
    return redirect(url_for('admin', displayed_leader_id=new_actual_leader_id if new_actual_leader_id is not None else leader_who_finished_round))


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
