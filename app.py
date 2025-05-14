import json
import sys
import sqlite3
import os
import string
import random
import traceback
from flask import Flask, render_template, request, redirect, url_for, g, flash, session
from flask_socketio import SocketIO, emit, join_room, leave_room # Добавлены join_room, leave_room (хотя пока не используются активно)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your_very_secret_fallback_key_for_dev_only_123') # Используйте надежный ключ или переменную окружения
if app.config['SECRET_KEY'] == 'your_very_secret_fallback_key_for_dev_only_123':
    print("ПРЕДУПРЕЖДЕНИЕ: Используется SECRET_KEY по умолчанию для разработки. Установите переменную окружения SECRET_KEY для продакшена!", file=sys.stderr)

socketio = SocketIO(app)
DB_PATH = 'database.db' # Если не нужна персистентность, этот файл будет создаваться в текущей директории

# --- Конфигурация для Игрового Поля ---
GAME_BOARD_POLE_IMG_SUBFOLDER = "pole"
GAME_BOARD_POLE_IMAGES = [f"p{i}.jpg" for i in range(1, 8)]
DEFAULT_NUM_BOARD_CELLS = 40
_current_game_board_pole_image_config = []
_current_game_board_num_cells = 0

# --- Хранилище для сопоставления SID и user_code ---
# ВАЖНО: Это простое внутрипроцессное хранилище.
# Если вы будете использовать Gunicorn с несколькими воркерами (-w > 1),
# каждый воркер будет иметь свой экземпляр этого словаря.
# Для одного воркера (-w 1) или при использовании встроенного сервера Flask это будет работать.
# Для полноценной многопроцессорной среды с Socket.IO нужен внешний брокер сообщений (например, Redis).
connected_users_socketio = {}  # {sid: user_code}

# --- Управление соединением с БД ---
def get_db():
    if 'db' not in g:
        # print(f"get_db: CWD: {os.getcwd()}, DB_PATH resolved: {os.path.abspath(DB_PATH)}", file=sys.stderr)
        g.db = sqlite3.connect(DB_PATH, timeout=10)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(error=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

# --- Инициализация БД ---
def init_db():
    print(f"DB Init: Attempting to initialize database at {os.path.abspath(DB_PATH)}", file=sys.stderr)
    print(f"DB Init: CWD is {os.getcwd()}", file=sys.stderr)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute("DROP TABLE IF EXISTS users")
        c.execute("DROP TABLE IF EXISTS images")
        c.execute("DROP TABLE IF EXISTS settings")
        c.execute("DROP TABLE IF EXISTS deck_votes")

        c.execute("""CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, code TEXT UNIQUE NOT NULL, rating INTEGER DEFAULT 0, status TEXT DEFAULT 'pending' NOT NULL)""")
        c.execute("""CREATE TABLE images (id INTEGER PRIMARY KEY AUTOINCREMENT, subfolder TEXT NOT NULL, image TEXT NOT NULL, status TEXT, owner_id INTEGER, guesses TEXT DEFAULT '{}')""")
        c.execute("""CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)""")
        c.execute("""CREATE TABLE deck_votes (subfolder TEXT PRIMARY KEY, votes INTEGER DEFAULT 0)""")
        conn.commit()

        settings_to_init = {
            'game_over': 'false', 'game_in_progress': 'false', 'show_card_info': 'false',
            'leading_user_id': '', 'active_subfolder': 'koloda1' # Значение по умолчанию для active_subfolder
        }
        for key, value in settings_to_init.items():
            try:
                c.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))
            except sqlite3.IntegrityError:
                c.execute("UPDATE settings SET value = ? WHERE key = ?", (value, key))
        conn.commit()

        # Загрузка изображений (убедитесь, что пути и папки корректны)
        image_folders = ['koloda1', 'ariadna', 'detstvo', 'odissey', 'pandora', ' Dixit', ' Dixit 2', ' Dixit 3', ' Dixit 4', ' Dixit 5', ' Dixit 6', ' Dixit 7 ', ' Dixit 8', ' Dixit 9', ' Dixit Odyssey', ' Dixit Odyssey (2)', ' Dixit Миражи', ' Имаджинариум', ' Имаджинариум Химера', ' Имаджинариум Юбилейный']
        images_added_count = 0
        for folder in image_folders:
            folder_path = os.path.join(app.static_folder, 'images', folder.strip()) # strip() для удаления лишних пробелов
            if os.path.exists(folder_path) and os.path.isdir(folder_path):
                for filename in os.listdir(folder_path):
                    if filename.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
                        try:
                            c.execute("SELECT 1 FROM images WHERE subfolder = ? AND image = ?", (folder.strip(), filename))
                            if c.fetchone() is None:
                                c.execute("INSERT INTO images (subfolder, image, status, guesses) VALUES (?, ?, 'Свободно', '{}')", (folder.strip(), filename))
                                images_added_count += 1
                        except sqlite3.Error as e_img:
                            print(f"DB Init Warning: Could not process image {folder.strip()}/{filename}: {e_img}", file=sys.stderr)
            else:
                print(f"DB Init Warning: Folder not found or is not a directory: {folder_path}", file=sys.stderr)
        if images_added_count > 0:
            print(f"DB Init: Added {images_added_count} new images to the database.", file=sys.stderr)
        else:
            print("DB Init: No new images were added (or all existing images already in DB).", file=sys.stderr)
        conn.commit()
        print("DB Init: Database tables created/recreated and basic settings/images populated.", file=sys.stderr)
    except sqlite3.Error as e:
        print(f"CRITICAL ERROR during init_db: {e}", file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)
        conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

# Вызываем init_db() прямо здесь, при загрузке модуля.
# Это гарантирует, что БД будет создана/пересоздана перед тем, как приложение начнет обрабатывать запросы.
# Поскольку вам не нужна персистентность, это перезапишет БД при каждом старте процесса приложения.
print("DB Init: Calling init_db() on module load.", file=sys.stderr)
init_db()
print("DB Init: init_db() call completed on module load.", file=sys.stderr)


# --- Вспомогательные функции (get_setting, set_setting, etc.) ---
def get_setting(key):
    try:
        db = get_db()
        c = db.cursor()
        c.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = c.fetchone()
        return row['value'] if row else None
    except sqlite3.Error as e:
        print(f"Database error in get_setting for key '{key}': {e}", file=sys.stderr)
        # print(traceback.format_exc(), file=sys.stderr) # Раскомментируйте для более детальной ошибки
        return None # Возвращаем None, чтобы вызывающий код мог это обработать

def set_setting(key, value):
    db = get_db()
    try:
        c = db.cursor()
        c.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        db.commit()
        return True
    except sqlite3.Error as e:
        print(f"Database error in set_setting for key '{key}': {e}", file=sys.stderr)
        db.rollback()
        return False

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
        print(f"Error in get_user_name for ID '{user_id}': {e}", file=sys.stderr)
        return None

def is_game_in_progress(): return get_setting('game_in_progress') == 'true'
def set_game_in_progress(state=True): return set_setting('game_in_progress', 'true' if state else 'false')
def is_game_over(): return get_setting('game_over') == 'true'
def set_game_over(state=True): return set_setting('game_over', 'true' if state else 'false')
def generate_unique_code(length=8): return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def get_leading_user_id():
    value = get_setting('leading_user_id')
    if value and value.strip():
        try: return int(value)
        except (ValueError, TypeError):
            print(f"Invalid leading_user_id value: {value}", file=sys.stderr)
            return None
    return None

def set_leading_user_id(user_id): return set_setting('leading_user_id', str(user_id) if user_id is not None else '')

def determine_new_leader(current_leader_id):
    db = get_db()
    c = db.cursor()
    try:
        c.execute("SELECT id FROM users WHERE status = 'active' ORDER BY id ASC")
        user_rows = c.fetchall()
        if not user_rows: return None
        active_user_ids = [row['id'] for row in user_rows]
        if current_leader_id is None or current_leader_id not in active_user_ids: return active_user_ids[0]
        try:
            current_index = active_user_ids.index(current_leader_id)
            next_index = (current_index + 1) % len(active_user_ids)
            return active_user_ids[next_index]
        except ValueError: return active_user_ids[0]
    except sqlite3.Error as e: print(f"DB error in determine_new_leader: {e}", file=sys.stderr); return None
    except Exception as e_gen: print(f"Generic error in determine_new_leader: {e_gen}", file=sys.stderr); return None

def get_active_players_count(db_conn): # db_conn передается, чтобы использовать существующее соединение
    try:
        cursor = db_conn.execute("SELECT COUNT(id) FROM users WHERE status = 'active'")
        count_row = cursor.fetchone()
        return count_row[0] if count_row else 0
    except sqlite3.Error as e: print(f"DB error in get_active_players_count: {e}", file=sys.stderr); return 0


def initialize_new_game_board_visuals(num_cells_for_board=None, all_users_for_rating_check=None):
    global _current_game_board_pole_image_config, _current_game_board_num_cells
    actual_num_cells = DEFAULT_NUM_BOARD_CELLS
    if num_cells_for_board is not None: actual_num_cells = num_cells_for_board
    elif all_users_for_rating_check:
        max_rating = 0
        for user_data_item in all_users_for_rating_check:
            user_rating = user_data_item.get('rating', 0) if isinstance(user_data_item, dict) else getattr(user_data_item, 'rating', 0)
            if isinstance(user_rating, int) and user_rating > max_rating: max_rating = user_rating
        actual_num_cells = max(DEFAULT_NUM_BOARD_CELLS, max_rating + 6)
    _current_game_board_num_cells = actual_num_cells
    _current_game_board_pole_image_config = []
    pole_image_folder_path = os.path.join(app.static_folder, 'images', GAME_BOARD_POLE_IMG_SUBFOLDER)
    if GAME_BOARD_POLE_IMAGES and os.path.exists(pole_image_folder_path) and os.path.isdir(pole_image_folder_path):
        available_pole_images = [f for f in os.listdir(pole_image_folder_path) if f.lower().endswith(('.jpg', '.png', '.jpeg')) and f in GAME_BOARD_POLE_IMAGES]
        if not available_pole_images:
             print(f"Warning: No images from GAME_BOARD_POLE_IMAGES found in {pole_image_folder_path}. Using p1.jpg as fallback.", file=sys.stderr)
             available_pole_images = ["p1.jpg"] # Fallback
        for _ in range(_current_game_board_num_cells):
            random_pole_image_file = random.choice(available_pole_images)
            image_path_for_static = os.path.join('images', GAME_BOARD_POLE_IMG_SUBFOLDER, random_pole_image_file).replace("\\", "/")
            _current_game_board_pole_image_config.append(image_path_for_static)
    else:
        print(f"Warning: Pole images not found or not configured. Path: {pole_image_folder_path}", file=sys.stderr)
        default_placeholder = os.path.join('images', GAME_BOARD_POLE_IMG_SUBFOLDER, "p1.jpg").replace("\\", "/")
        _current_game_board_pole_image_config = [default_placeholder] * _current_game_board_num_cells
    # print(f"Визуалізацію ігрового поля ініціалізовано/оновлено для {_current_game_board_num_cells} клітинок.", file=sys.stderr)


def generate_game_board_data_for_display(all_users_data_for_board):
    global _current_game_board_pole_image_config, _current_game_board_num_cells
    if not _current_game_board_pole_image_config or _current_game_board_num_cells == 0:
        initialize_new_game_board_visuals(all_users_for_rating_check=all_users_data_for_board)
        if not _current_game_board_pole_image_config or _current_game_board_num_cells == 0: return []
    board_cells_data = []
    for i in range(_current_game_board_num_cells):
        cell_number = i + 1; cell_image_path = "images/default_pole_image.png"
        if _current_game_board_pole_image_config:
            try: cell_image_path = _current_game_board_pole_image_config[i % len(_current_game_board_pole_image_config)]
            except (IndexError, TypeError) as e: print(f"Error getting board cell image: {e}", file=sys.stderr)
        users_in_this_cell = []
        for user_data_item_board in all_users_data_for_board:
            user_rating_raw = user_data_item_board.get('rating') if isinstance(user_data_item_board, dict) else user_data_item_board['rating']
            user_name = user_data_item_board.get('name', "N/A") if isinstance(user_data_item_board, dict) else user_data_item_board['name']
            user_id_for_name = user_data_item_board.get('id', "N/A_ID") if isinstance(user_data_item_board, dict) else user_data_item_board['id']
            current_user_rating_int = 0
            if user_rating_raw is not None:
                try: current_user_rating_int = int(user_rating_raw)
                except (ValueError, TypeError): current_user_rating_int = 0
            if current_user_rating_int == cell_number:
                display_name = user_name if user_name and str(user_name).strip() else f"ID {user_id_for_name}"
                users_in_this_cell.append({'id': user_id_for_name, 'name': display_name, 'rating': current_user_rating_int})
        board_cells_data.append({'cell_number': cell_number, 'image_path': cell_image_path, 'users_in_cell': users_in_this_cell})
    return board_cells_data


# --- Функции для SocketIO ---
def get_full_game_state_data(user_code_for_state=None):
    db = get_db() # Используем get_db() для получения соединения в текущем контексте
    current_g_user_dict = None
    if user_code_for_state:
        user_row_for_state = db.execute("SELECT id, name, code, rating, status FROM users WHERE code = ?", (user_code_for_state,)).fetchone()
        if user_row_for_state:
            current_g_user_dict = dict(user_row_for_state)

    active_subfolder_val = get_setting('active_subfolder')
    db_current_leader_id_val = get_leading_user_id()
    num_active_players_val = get_active_players_count(db) # Передаем db

    game_state = {
        'game_in_progress': is_game_in_progress(),
        'game_over': is_game_over(),
        'show_card_info': get_setting("show_card_info") == "true",
        'active_subfolder': active_subfolder_val,
        'db_current_leader_id': db_current_leader_id_val,
        'num_active_players': num_active_players_val,
        'table_images': [], 'user_cards': [], 'all_users_for_guessing': [],
        'on_table_status': False, 'is_current_user_the_db_leader': False,
        'leader_pole_pictogram_path': None, 'leader_pictogram_rating_display': None,
        'game_board': [], 'current_num_board_cells': _current_game_board_num_cells,
        'current_user_data': current_g_user_dict,
        'num_cards_on_table': 0,
        'all_cards_placed_for_guessing_phase_to_template': False,
        'flashed_messages': [] # Пока не используем, но можно будет добавить
    }

    raw_table_cards = db.execute("""
        SELECT i.id, i.image, i.subfolder, i.owner_id, u.name as owner_name, i.guesses
        FROM images i LEFT JOIN users u ON i.owner_id = u.id
        WHERE i.subfolder = ? AND i.status LIKE 'На столе:%' AND (u.status = 'active' OR u.status IS NULL)
    """, (active_subfolder_val,)).fetchall() if active_subfolder_val else []
    game_state['num_cards_on_table'] = len(raw_table_cards)

    if game_state['game_in_progress']:
        game_state['all_cards_placed_for_guessing_phase_to_template'] = (num_active_players_val > 0 and game_state['num_cards_on_table'] >= num_active_players_val)

        for card_row in raw_table_cards:
            guesses_data = json.loads(card_row['guesses'] or '{}')
            my_guess_val = None
            if current_g_user_dict and current_g_user_dict['status'] == 'active' and \
               game_state['all_cards_placed_for_guessing_phase_to_template'] and \
               not game_state['show_card_info'] and card_row['owner_id'] != current_g_user_dict['id']:
                my_guess_val = guesses_data.get(str(current_g_user_dict['id']))

            game_state['table_images'].append({
                'id': card_row['id'], 'image': card_row['image'], 'subfolder': card_row['subfolder'],
                'owner_id': card_row['owner_id'], 'owner_name': get_user_name(card_row['owner_id']) or "Неизвестный",
                'guesses': guesses_data, 'my_guess_for_this_card_value': my_guess_val
            })

        if current_g_user_dict and current_g_user_dict['status'] == 'active' and active_subfolder_val:
            user_cards_db = db.execute("SELECT id, image, subfolder FROM images WHERE owner_id = ? AND subfolder = ? AND status LIKE 'Занято:%'",
                                       (current_g_user_dict['id'], active_subfolder_val)).fetchall()
            game_state['user_cards'] = [{'id': r['id'], 'image': r['image'], 'subfolder': r['subfolder']} for r in user_cards_db]
            if any(tc['owner_id'] == current_g_user_dict['id'] for tc in game_state['table_images']): # Проверка на основе уже собранных table_images
                game_state['on_table_status'] = True
            
            # all_users_for_guessing теперь включает ВСЕХ активных, клиент отфильтрует себя при необходимости
            all_active_users_db = db.execute("SELECT id, name FROM users WHERE status = 'active'").fetchall()
            game_state['all_users_for_guessing'] = [{'id': u['id'], 'name': u['name']} for u in all_active_users_db]


            if db_current_leader_id_val is not None:
                game_state['is_current_user_the_db_leader'] = (current_g_user_dict['id'] == db_current_leader_id_val)

            if game_state['is_current_user_the_db_leader'] and not game_state['on_table_status'] and \
               not game_state['show_card_info'] and not game_state['all_cards_placed_for_guessing_phase_to_template']:
                leader_rating = int(current_g_user_dict.get('rating', 0))
                game_state['leader_pictogram_rating_display'] = leader_rating
                if leader_rating > 0 and _current_game_board_pole_image_config and \
                   leader_rating <= _current_game_board_num_cells and \
                   (leader_rating - 1) < len(_current_game_board_pole_image_config):
                    game_state['leader_pole_pictogram_path'] = _current_game_board_pole_image_config[leader_rating - 1]

    all_active_users_for_board = db.execute("SELECT id, name, rating FROM users WHERE status = 'active'").fetchall()
    game_state['game_board'] = generate_game_board_data_for_display(all_active_users_for_board)
    game_state['current_num_board_cells'] = _current_game_board_num_cells
    return game_state

def broadcast_game_state_update(user_code_trigger=None):
    print(f"SocketIO: Broadcasting game_update. Triggered by: {user_code_trigger or 'System'}", file=sys.stderr)
    active_sids = list(connected_users_socketio.keys())
    if not active_sids:
        print("SocketIO: No identified clients to broadcast to.", file=sys.stderr)
        return

    for sid_to_update in active_sids:
        user_code_for_sid = connected_users_socketio.get(sid_to_update)
        if user_code_for_sid:
            try:
                # Создаем новый app_context для каждого SID, чтобы get_db() работала корректно
                with app.app_context():
                    state_data = get_full_game_state_data(user_code_for_state=user_code_for_sid)
                    socketio.emit('game_update', state_data, room=sid_to_update)
                # print(f"SocketIO: Sent personalized game_update to SID {sid_to_update} (user {user_code_for_sid})", file=sys.stderr) # Слишком много логов
            except Exception as e:
                print(f"SocketIO: Error sending update to SID {sid_to_update} (user {user_code_for_sid}): {e}", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
    print(f"SocketIO: Broadcast completed for {len(active_sids)} SIDs.", file=sys.stderr)


def broadcast_user_list_update(): # Используется в админке и при регистрации
    # В данный момент админка не слушает это событие напрямую.
    # Перезагрузка состояния игры обновит и список пользователей на странице /admin через HTTP.
    # Для index.html это не актуально.
    print("SocketIO: broadcast_user_list_update() called. Triggering general game state update.", file=sys.stderr)
    broadcast_game_state_update() # Общее обновление затронет и данные для админки, если она их отображает динамически


def broadcast_deck_votes_update():
    print("SocketIO: broadcast_deck_votes_update() called.", file=sys.stderr)
    try:
        with app.app_context(): # Нужен контекст для get_db()
            db = get_db()
            c = db.cursor()
            c.execute("SELECT i.subfolder, COALESCE(dv.votes, 0) as votes FROM (SELECT DISTINCT subfolder FROM images ORDER BY subfolder) as i LEFT JOIN deck_votes as dv ON i.subfolder = dv.subfolder;")
            deck_votes_data = [dict(row) for row in c.fetchall()]
            socketio.emit('deck_votes_updated', {'deck_votes': deck_votes_data})
            print(f"SocketIO: Emitted deck_votes_updated: {len(deck_votes_data)} decks.", file=sys.stderr)
    except Exception as e: # Ловим более общие исключения, если get_db() не сработает вне HTTP-контекста
        print(f"Error broadcasting deck votes (possibly context issue): {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)

# --- Глобальные переменные и функции для Jinja ---
app.jinja_env.globals.update(get_user_name=get_user_name, get_leading_user_id=get_leading_user_id)

# --- Маршруты и before_request ---
@app.before_request
def before_request_func():
    db = get_db() # Инициализация g.db, если еще нет
    # print(f"Before request: CWD: {os.getcwd()}, DB_PATH resolved: {os.path.abspath(DB_PATH)}", file=sys.stderr)
    # print(f"Before request: session is {session}", file=sys.stderr)
    code_param = request.args.get('code') or \
                 (request.view_args.get('code') if request.view_args else None) or \
                 session.get('user_code')

    g.user = None; g.user_id = None
    if code_param:
        try:
            user_row = db.execute("SELECT id, name, code, rating, status FROM users WHERE code = ?", (code_param,)).fetchone()
            if user_row:
                g.user = dict(user_row); g.user_id = user_row['id']
                session['user_id'] = user_row['id']; session['user_name'] = user_row['name']
                session['user_code'] = user_row['code']; session['user_status'] = user_row['status']
                session['user_rating'] = user_row['rating']
            else:
                if 'user_code' in session and session['user_code'] == code_param:
                    keys_to_pop = ['user_id', 'user_name', 'user_code', 'user_status', 'user_rating']
                    for key in keys_to_pop: session.pop(key, None)
        except sqlite3.Error as e: print(f"DB error in before_request for code '{code_param}': {e}", file=sys.stderr)
    g.show_card_info = get_setting("show_card_info") == "true"
    g.game_over = is_game_over()
    g.game_in_progress = is_game_in_progress()

# --- Маршруты (index, login, logout, admin, etc.) ---
@app.route('/')
def index():
    deck_votes_data = []; db = get_db(); c = db.cursor()
    try:
        c.execute("SELECT i.subfolder, COALESCE(dv.votes, 0) as votes FROM (SELECT DISTINCT subfolder FROM images ORDER BY subfolder) as i LEFT JOIN deck_votes as dv ON i.subfolder = dv.subfolder;")
        deck_votes_data = [dict(row) for row in c.fetchall()]
    except sqlite3.Error as e: print(f"Ошибка чтения голосов на index: {e}", file=sys.stderr); flash(f"Не удалось загрузить данные о колодах: {e}", "danger")
    current_vote = session.get('voted_for_deck'); active_subfolder = get_setting('active_subfolder') or "Не выбрана"
    return render_template("index.html", deck_votes=deck_votes_data, current_vote=current_vote, active_subfolder=active_subfolder)

@app.route('/init_db_route_for_dev_only_make_sure_to_secure_or_remove')
def init_db_route(): # Защитить или удалить в продакшене
    # init_db() # Уже вызывается при старте модуля
    flash("База данных была инициализирована при старте приложения.", "info")
    return redirect(url_for('index'))

@app.route("/login_player")
def login_player():
    user_code_from_session = session.get('user_code')
    if user_code_from_session: return redirect(url_for('user', code=user_code_from_session))
    return render_template('login_player.html')

@app.route("/register_or_login_player", methods=["POST"])
def register_or_login_player():
    player_name = request.form.get('name', '').strip()
    if not player_name: flash("Имя не может быть пустым.", "warning"); return redirect(url_for('login_player'))
    if len(player_name) > 50: flash("Имя слишком длинное.", "warning"); return redirect(url_for('login_player'))
    db = get_db(); c = db.cursor(); user_code = None; user_id = None; user_status = 'pending'
    try:
        existing_user = c.execute("SELECT id, code, status, rating FROM users WHERE name = ?", (player_name,)).fetchone()
        if existing_user:
            user_id = existing_user['id']; user_code = existing_user['code']; user_status = existing_user['status']; user_rating = existing_user['rating']
            flash(f"С возвращением, {player_name}!", "info")
        else:
            user_code = generate_unique_code()
            user_initial_status = 'pending' if is_game_in_progress() else 'active'
            flash_msg_status = "Вы как наблюдатель. Участие со след. игры." if user_initial_status == 'pending' else "Вы активный участник."
            try:
                c.execute("INSERT INTO users (name, code, status, rating) VALUES (?, ?, ?, ?)", (player_name, user_code, user_initial_status, 0))
                user_id = c.lastrowid; db.commit(); user_status = user_initial_status; user_rating = 0
                flash(f"Добро пожаловать, {player_name}! {flash_msg_status}", "success")
                broadcast_user_list_update()
            except sqlite3.IntegrityError: db.rollback(); flash("Это имя уже занято.", "danger"); return redirect(url_for('login_player'))
            except sqlite3.Error as e_ins: db.rollback(); flash(f"Ошибка БД: {e_ins}", "danger"); return redirect(url_for('login_player'))
        if user_code:
            session['user_id'] = user_id; session['user_name'] = player_name; session['user_code'] = user_code
            session['user_status'] = user_status; session['user_rating'] = user_rating
            session.pop('is_admin', None)
            return redirect(url_for('user', code=user_code))
        else: flash("Произошла ошибка.", "danger"); return redirect(url_for('login_player'))
    except sqlite3.Error as e: flash(f"Ошибка БД: {e}", "danger"); return redirect(url_for('login_player'))
    except Exception as e_gen: print(traceback.format_exc(), file=sys.stderr); flash(f"Ошибка: {e_gen}", "danger"); return redirect(url_for('login_player'))

@app.route('/vote_deck', methods=['POST'])
def vote_deck():
    new_deck = request.form.get('subfolder'); previous_deck = session.get('voted_for_deck')
    if not new_deck: flash("Колода не выбрана.", "warning"); return redirect(url_for('index'))
    if new_deck == previous_deck: flash(f"Вы уже голосовали за '{new_deck}'.", "info"); return redirect(url_for('index'))
    db = get_db(); c = db.cursor()
    try:
        if previous_deck: c.execute("UPDATE deck_votes SET votes = MAX(0, votes - 1) WHERE subfolder = ?", (previous_deck,))
        c.execute("INSERT OR IGNORE INTO deck_votes (subfolder, votes) VALUES (?, 0)", (new_deck,))
        c.execute("UPDATE deck_votes SET votes = votes + 1 WHERE subfolder = ?", (new_deck,))
        db.commit(); session['voted_for_deck'] = new_deck
        flash(f"Голос за '{new_deck}' учтен!", "success"); broadcast_deck_votes_update()
    except sqlite3.Error as e: db.rollback(); flash(f"Ошибка БД: {e}", "danger")
    return redirect(url_for('index'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password_attempt = request.form.get('password'); correct_password = os.environ.get('ADMIN_PASSWORD')
        if not correct_password: flash('Ошибка конфигурации сервера (пароль админа).', 'danger'); return render_template('login.html')
        if password_attempt == correct_password:
            session['is_admin'] = True; flash('Авторизация успешна.', 'success')
            return redirect(request.args.get('next') or url_for('admin'))
        else: flash('Неверный пароль.', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('is_admin', None)
    # Очистка пользовательской сессии при выходе из админки НЕ НУЖНА,
    # если админ может быть одновременно и игроком. Если нет - можно раскомментировать.
    # keys_to_pop = ['user_id', 'user_name', 'user_code', 'user_status', 'user_rating', 'voted_for_deck']
    # for key in keys_to_pop: session.pop(key, None)
    flash('Вы вышли из системы администратора.', 'info')
    return redirect(url_for('index')) # Или url_for('login')

@app.route("/admin", methods=["GET", "POST"])
def admin():
    if not session.get('is_admin'):
        flash('Требуется авторизация администратора.', 'warning'); return redirect(url_for('login', next=request.url))
    db = get_db(); c = db.cursor()
    current_leader_from_db = get_leading_user_id()
    leader_to_focus_on_id = current_leader_from_db
    # ... (остальная логика вашего маршрута admin без изменений, но с вызовами broadcast_...() в нужных местах)
    if request.method == "POST":
        action_handled = False; leader_for_redirect = leader_to_focus_on_id
        form_action = request.form.get("form_action") # Скрытое поле для определения действия
        try:
            if form_action == "add_user": # Пример, если вы так различаете формы
                name_admin_form = request.form.get("name", "").strip()
                num_cards_admin_form = int(request.form.get("num_cards", 3))
                # ... (логика добавления пользователя) ...
                db.commit(); action_handled = True; broadcast_user_list_update(); broadcast_game_state_update()
            elif form_action == "set_active_deck":
                new_active_subfolder = request.form.get("active_subfolder")
                set_setting("active_subfolder", new_active_subfolder if new_active_subfolder else "")
                flash(f"Активная колода изменена на '{new_active_subfolder or 'Не выбрана'}'.", "success" if new_active_subfolder else "info")
                db.commit(); action_handled = True; broadcast_game_state_update()
            elif form_action == "delete_user":
                user_id_to_delete = int(request.form.get("delete_user_id"))
                # ... (логика удаления пользователя) ...
                db.commit(); action_handled = True; broadcast_user_list_update(); broadcast_game_state_update()
            elif form_action == "toggle_show_card_info":
                new_show_info = not (get_setting('show_card_info') == 'true')
                set_setting('show_card_info', 'true' if new_show_info else 'false'); db.commit()
                flash(f"Отображение инфо о картах {'вкл' if new_show_info else 'выкл'}.", "info")
                action_handled = True; broadcast_game_state_update()
            elif form_action == "reset_game_board_visuals":
                c.execute("SELECT id, name, rating FROM users WHERE status = 'active'")
                initialize_new_game_board_visuals(all_users_for_rating_check=c.fetchall()) # Должно быть c.fetchall()
                flash("Визуализация игрового поля сброшена.", "success")
                action_handled = True; broadcast_game_state_update()

            # Добавьте сюда обработку других POST-запросов с формы админа по аналогии
            # Например, если у вас отдельные кнопки сабмита с разными name/value:
            if "submit_add_user" in request.form: # Если кнопка называется submit_add_user
                # ... логика добавления пользователя ...
                db.commit(); broadcast_user_list_update(); broadcast_game_state_update() # Обновляем состояние
                # ...
            # И так далее для других действий

            if action_handled:
                return redirect(url_for('admin', displayed_leader_id=leader_for_redirect if leader_for_redirect is not None else ''))

        except sqlite3.Error as e_sql_post: db.rollback(); flash(f"Ошибка БД POST: {e_sql_post}", "danger"); print(traceback.format_exc(), file=sys.stderr)
        except Exception as e_general_post: flash(f"Ошибка POST: {e_general_post}", "danger"); print(traceback.format_exc(), file=sys.stderr)
    
    # Сбор данных для шаблона admin.html (ваш существующий код)
    users_for_template = [dict(row) for row in c.execute("SELECT id, name, code, rating, status FROM users ORDER BY name ASC").fetchall()]
    active_users_for_board = [u for u in users_for_template if u['status'] == 'active']
    game_board_data_for_template = generate_game_board_data_for_display(active_users_for_board)
    # ... и т.д.
    # Убедитесь, что все необходимые переменные передаются в render_template
    current_active_subfolder = get_setting('active_subfolder') or ''
    potential_next_leader_id = determine_new_leader(current_leader_from_db)
    images_for_template = [dict(img_row, guesses=json.loads(img_row['guesses'] or '{}')) for img_row in c.execute("SELECT id, subfolder, image, status, owner_id, guesses FROM images ORDER BY subfolder, id").fetchall()]
    image_owners_for_template = {img['id']: img['owner_id'] for img in images_for_template if img.get('owner_id') is not None}
    free_image_count_for_template = sum(1 for img in images_for_template if img.get('status') == 'Свободно' and img.get('subfolder') == current_active_subfolder)
    all_guesses_for_template = {img['id']: img['guesses'] for img in images_for_template if img['guesses']}
    guess_counts_by_user_for_template = {u['id']: 0 for u in active_users_for_board} # Используйте active_users_for_board
    user_has_duplicate_guesses_for_template = {u['id']: False for u in active_users_for_board} # Используйте active_users_for_board

    if all_guesses_for_template and active_users_for_board:
        for user_item in active_users_for_board:
            user_id_str = str(user_item['id']); guesses_made = []
            for guesses_for_image in all_guesses_for_template.values():
                if user_id_str in guesses_for_image:
                    guesses_made.append(guesses_for_image[user_id_str])
                    guess_counts_by_user_for_template[user_item['id']] += 1
            if len(guesses_made) > len(set(guesses_made)): user_has_duplicate_guesses_for_template[user_item['id']] = True
    subfolders_for_template = [row['subfolder'] for row in c.execute("SELECT DISTINCT subfolder FROM images ORDER BY subfolder").fetchall()]


    return render_template("admin.html", users=users_for_template, images=images_for_template, subfolders=subfolders_for_template,
                           active_subfolder=current_active_subfolder, db_current_leader_id=current_leader_from_db,
                           admin_focus_leader_id=leader_to_focus_on_id, potential_next_leader_id=potential_next_leader_id,
                           free_image_count=free_image_count_for_template, image_owners=image_owners_for_template,
                           guess_counts_by_user=guess_counts_by_user_for_template, all_guesses=all_guesses_for_template,
                           user_has_duplicate_guesses=user_has_duplicate_guesses_for_template, game_board=game_board_data_for_template,
                           get_user_name_func=get_user_name, current_num_board_cells=_current_game_board_num_cells)


@app.route("/start_new_game", methods=["POST"])
def start_new_game():
    if not session.get('is_admin'): flash('Доступ запрещен.', 'danger'); return redirect(url_for('login'))
    db = get_db(); c = db.cursor(); selected_deck = request.form.get("new_game_subfolder")
    try: num_cards_per_player = int(request.form.get("new_game_num_cards", 3)); assert num_cards_per_player >= 1
    except (ValueError, TypeError, AssertionError): flash("Кол-во карт <1 или неверно. Уст. 3.", "warning"); num_cards_per_player = 3
    if not selected_deck: flash("Колода не выбрана.", "danger"); return redirect(url_for('admin'))
    new_leader_id_sng = None
    try:
        c.execute("UPDATE users SET status = 'active' WHERE status = 'pending'"); activated_count = c.rowcount
        if activated_count > 0: flash(f"{activated_count} ожидающих активированы.", "info")
        c.execute("UPDATE users SET rating = 0")
        c.execute("UPDATE images SET owner_id = NULL, guesses = '{}', status = 'Занято:Админ'") # Сначала помечаем все карты как "Занято:Админ"
        c.execute("UPDATE images SET status = 'Свободно' WHERE subfolder = ?", (selected_deck,)) # Затем освобождаем карты выбранной колоды
        set_game_over(False); set_setting("show_card_info", "false"); set_setting("active_subfolder", selected_deck); set_game_in_progress(False) # Сначала ставим False
        
        first_active_user = c.execute("SELECT id FROM users WHERE status = 'active' ORDER BY id LIMIT 1").fetchone()
        if first_active_user: new_leader_id_sng = first_active_user['id']; set_leading_user_id(new_leader_id_sng)
        else: set_leading_user_id(None)
        
        initialize_new_game_board_visuals(all_users_for_rating_check=c.execute("SELECT id, name, rating FROM users WHERE status = 'active'").fetchall())
        db.commit() # Коммит перед раздачей карт

        active_user_ids_sng = [row['id'] for row in c.execute("SELECT id FROM users WHERE status = 'active' ORDER BY id").fetchall()]
        num_active_users = len(active_user_ids_sng); num_total_dealt = 0
        if not active_user_ids_sng: flash("Активные пользователи не найдены. Карты не розданы.", "warning")
        else:
            available_cards_ids = [row['id'] for row in c.execute("SELECT id FROM images WHERE subfolder = ? AND status = 'Свободно'", (selected_deck,)).fetchall()]
            random.shuffle(available_cards_ids); num_available = len(available_cards_ids)
            if num_available < num_active_users * num_cards_per_player: flash(f"Внимание: Карт ({num_available}) < чем нужно для {num_active_users} игроков по {num_cards_per_player}.", "warning")
            card_index = 0
            for user_id_sng_deal in active_user_ids_sng:
                cards_dealt_to_user = 0
                for _ in range(num_cards_per_player):
                    if card_index < num_available:
                        c.execute("UPDATE images SET status = ?, owner_id = ? WHERE id = ?", (f"Занято:{user_id_sng_deal}", user_id_sng_deal, available_cards_ids[card_index]))
                        card_index += 1; cards_dealt_to_user += 1
                    else: break
                num_total_dealt += cards_dealt_to_user
                if card_index >= num_available and num_total_dealt < num_active_users * num_cards_per_player : # Если карты кончились раньше времени
                    flash(f"Карты в колоде '{selected_deck}' закончились. Роздано {num_total_dealt} карт(ы) вместо ожидаемых {num_active_users * num_cards_per_player}.", "warning")
                    break
            flash(f"Новая игра! Колода: '{selected_deck}'. Роздано: {num_total_dealt} карт.", "success")
            if new_leader_id_sng: flash(f"Ведущий: {get_user_name(new_leader_id_sng) or f'ID {new_leader_id_sng}'}.", "info")
        
        set_game_in_progress(True); # Теперь устанавливаем True
        db.commit()
        broadcast_game_state_update()
    except sqlite3.Error as e: db.rollback(); flash(f"Ошибка БД: {e}", "danger"); print(traceback.format_exc(), file=sys.stderr)
    except Exception as e_gen: db.rollback(); flash(f"Ошибка: {e_gen}", "danger"); print(traceback.format_exc(), file=sys.stderr)
    return redirect(url_for('admin', displayed_leader_id=new_leader_id_sng if new_leader_id_sng is not None else ''))


@app.route('/user/<code>')
def user(code):
    if g.user is None: # g.user устанавливается в before_request
        session.pop('user_id', None); session.pop('user_name', None); session.pop('user_code', None)
        flash("Пользователя не найдено. Пожалуйста, войдите.", "warning")
        return redirect(url_for('login_player'))
    # Сохраняем/обновляем данные в сессии Flask, если они есть в g.user
    session['user_id'] = g.user['id']; session['user_name'] = g.user['name']; session['user_code'] = g.user['code']
    session['user_status'] = g.user['status']; session['user_rating'] = g.user['rating']
    session.pop('is_admin', None) # Убираем флаг админа, если он зашел как игрок
    return render_template('user.html', user_data_for_init=dict(g.user))

@app.route("/user/<code>/place/<int:image_id>", methods=["POST"])
def place_card(code, image_id):
    if not g.user or g.user['status'] != 'active':
        flash("Только активные игроки могут выкладывать карты.", "warning")
        return redirect(url_for('user', code=code))
    db = get_db(); c = db.cursor()
    try:
        if g.game_over: flash("Игра окончена.", "warning"); return redirect(url_for('user', code=code))
        active_subfolder = get_setting('active_subfolder')
        if not active_subfolder: flash("Активная колода не определена.", "danger"); return redirect(url_for('user', code=code))

        num_on_table = c.execute("SELECT COUNT(id) FROM images WHERE subfolder = ? AND status LIKE 'На столе:%'", (active_subfolder,)).fetchone()[0]
        num_active = get_active_players_count(db)
        all_cards_placed_for_guessing = (num_active > 0 and num_on_table >= num_active)

        if g.show_card_info: flash("Карты уже открыты, менять нельзя.", "warning"); return redirect(url_for('user', code=code))
        if all_cards_placed_for_guessing: # Если все выложили карты (кроме текущего действия)
            # Проверим, не является ли текущий игрок последним, кто не выложил карту.
            # Это условие нужно, чтобы разрешить последнему игроку выложить карту, но не менять ее потом.
            # Но если он уже выложил, то менять нельзя.
            card_of_this_user_on_table_already = c.execute("SELECT 1 FROM images WHERE owner_id = ? AND subfolder = ? AND status LIKE 'На столе:%'", (g.user['id'], active_subfolder)).fetchone()
            if card_of_this_user_on_table_already:
                 flash("Все игроки уже выложили карты, менять нельзя.", "warning"); return redirect(url_for('user', code=code))


        current_card_on_table = c.execute("SELECT id FROM images WHERE owner_id = ? AND subfolder = ? AND status LIKE 'На столе:%'", (g.user['id'], active_subfolder)).fetchone()
        card_to_place_info = c.execute("SELECT status, owner_id, subfolder, image FROM images WHERE id = ?", (image_id,)).fetchone()

        if not card_to_place_info: flash(f"Карта ID {image_id} не найдена.", "danger"); return redirect(url_for('user', code=code))
        if card_to_place_info['owner_id'] != g.user['id']: flash(f"Вы не владелец карты {image_id}.", "danger"); return redirect(url_for('user', code=code))
        
        expected_status_on_hand = f"Занято:{g.user['id']}"
        if card_to_place_info['status'] != expected_status_on_hand:
            if card_to_place_info['status'].startswith("На столе:") and card_to_place_info['id'] == (current_card_on_table['id'] if current_card_on_table else None):
                 flash(f"Карта '{card_to_place_info['image']}' уже на столе.", "info"); return redirect(url_for('user', code=code))
            flash(f"Карту '{card_to_place_info['image']}' ({image_id}) нельзя выложить. Статус: '{card_to_place_info['status']}'. Ожидался: '{expected_status_on_hand}'.", "danger"); return redirect(url_for('user', code=code))

        if card_to_place_info['subfolder'] != active_subfolder: flash(f"Карта '{card_to_place_info['image']}' не из активной колоды '{active_subfolder}'.", "danger"); return redirect(url_for('user', code=code))
        if current_card_on_table and current_card_on_table['id'] == image_id: flash(f"Карта '{card_to_place_info['image']}' уже на столе.", "info"); return redirect(url_for('user', code=code))

        if current_card_on_table and current_card_on_table['id'] != image_id:
            c.execute("UPDATE images SET status = ?, guesses = '{}' WHERE id = ?", (f"Занято:{g.user['id']}", current_card_on_table['id']))
            flash(f"Предыдущая карта возвращена в руку.", "info")
        
        new_status_on_table = f"На столе:{g.user['id']}"; c.execute("UPDATE images SET status = ?, guesses = '{}' WHERE id = ?", (new_status_on_table, image_id))
        db.commit(); flash(f"Ваша карта '{card_to_place_info['image']}' выложена.", "success"); broadcast_game_state_update(user_code_trigger=code)
    except sqlite3.Error as e: db.rollback(); flash(f"Ошибка БД: {e}", "danger"); print(traceback.format_exc(), file=sys.stderr)
    except Exception as e_gen: flash(f"Ошибка: {e_gen}", "danger"); print(traceback.format_exc(), file=sys.stderr)
    return redirect(url_for('user', code=code))


@app.route("/user/<code>/guess/<int:image_id>", methods=["POST"])
def guess_image(code, image_id):
    if not g.user or g.user['status'] != 'active': flash("Только активные игроки могут делать предположения.", "warning"); return redirect(url_for('user', code=code))
    guessed_user_id_str = request.form.get("guessed_user_id")
    if not guessed_user_id_str: flash("Игрок для предположения не выбран.", "warning"); return redirect(url_for('user', code=code))
    db = get_db(); c = db.cursor()
    try:
        guessed_user_id = int(guessed_user_id_str)
        if not c.execute("SELECT 1 FROM users WHERE id = ? AND status = 'active'", (guessed_user_id,)).fetchone():
            flash("Выбранный игрок не существует/неактивен.", "danger"); return redirect(url_for('user', code=code))
        
        image_data = c.execute("SELECT i.guesses, i.owner_id FROM images i JOIN users u ON i.owner_id = u.id WHERE i.id = ? AND i.status LIKE 'На столе:%' AND u.status = 'active'", (image_id,)).fetchone()
        if not image_data: flash("Карта не найдена или принадлежит неактивному.", "danger"); return redirect(url_for('user', code=code))
        if image_data['owner_id'] == g.user['id']: flash("Нельзя угадывать свою карту.", "warning"); return redirect(url_for('user', code=code))
        if g.show_card_info: flash("Карты уже открыты.", "warning"); return redirect(url_for('user', code=code))
        
        guesses = json.loads(image_data['guesses'] or '{}'); guesses[str(g.user['id'])] = guessed_user_id
        c.execute("UPDATE images SET guesses = ? WHERE id = ?", (json.dumps(guesses), image_id)); db.commit()
        flash(f"Ваше предположение (карта '{get_user_name(guessed_user_id)}') сохранено.", "success")
        broadcast_game_state_update(user_code_trigger=code)
    except (ValueError, TypeError): flash("Неверный ID игрока.", "danger")
    except sqlite3.Error as e: db.rollback(); flash(f"Ошибка БД: {e}", "danger"); print(traceback.format_exc(), file=sys.stderr)
    except Exception as e_gen: flash(f"Ошибка: {e_gen}", "danger"); print(traceback.format_exc(), file=sys.stderr)
    return redirect(url_for('user', code=code))

@app.route("/admin/open_cards", methods=["POST"])
def open_cards():
    if not session.get('is_admin'): flash('Доступ запрещен.', 'danger'); return redirect(url_for('login'))
    if g.game_over and not g.game_in_progress: flash("Игра завершена.", "warning"); return redirect(url_for('admin'))
    if not g.game_in_progress and not g.game_over: flash("Игра не активна.", "warning"); return redirect(url_for('admin'))
    db = get_db(); c = db.cursor()
    try:
        set_setting("show_card_info", "true") # Показываем карты
        leading_user_id = get_leading_user_id()
        # ... (Ваша существующая логика подсчета очков) ...
        # Пример упрощенной логики подсчета (ВАМ НУЖНО АДАПТИРОВАТЬ ВАШУ ПОЛНУЮ ЛОГИКУ СЮДА)
        if leading_user_id is not None:
            leader_card_on_table = c.execute("SELECT id, guesses FROM images WHERE owner_id = ? AND status LIKE 'На столе:%'", (leading_user_id,)).fetchone()
            if leader_card_on_table:
                leader_guesses = json.loads(leader_card_on_table['guesses'] or '{}')
                correct_guessers_of_leader_card = 0
                other_active_players_count = get_active_players_count(db) - 1 # Минус сам ведущий

                for guesser_id_str, guessed_card_owner_id in leader_guesses.items():
                    if guessed_card_owner_id == leading_user_id: # Угадали карту ведущего
                        correct_guessers_of_leader_card += 1
                        c.execute("UPDATE users SET rating = MAX(0, rating + 3) WHERE id = ?", (int(guesser_id_str),)) # +3 угадавшему
                
                if other_active_players_count > 0:
                    if correct_guessers_of_leader_card == other_active_players_count : # Все угадали ведущего
                        c.execute("UPDATE users SET rating = MAX(0, rating - 3) WHERE id = ?", (leading_user_id,)) # Ведущий -3
                    elif correct_guessers_of_leader_card == 0: # Никто не угадал ведущего
                        c.execute("UPDATE users SET rating = MAX(0, rating - 2) WHERE id = ?", (leading_user_id,)) # Ведущий -2
                        # Другие игроки, чьи карты угадали, получают по +1 за каждого угадавшего их карту
                        # (Эту логику нужно добавить, если она есть)
                    else: # Некоторые угадали ведущего
                         c.execute("UPDATE users SET rating = MAX(0, rating + 3 + ?) WHERE id = ?", (correct_guessers_of_leader_card, leading_user_id)) # Ведущий +3 + кол-во угадавших

            # Подсчет очков для остальных игроков (кто угадал их карты)
            all_cards_on_table = c.execute("SELECT owner_id, guesses FROM images WHERE status LIKE 'На столе:%'").fetchall()
            for card_info in all_cards_on_table:
                if card_info['owner_id'] == leading_user_id: continue # Уже обработали карту ведущего
                
                owner_id = card_info['owner_id']
                guesses_for_this_card = json.loads(card_info['guesses'] or '{}')
                num_correct_guesses_for_this_owner = 0
                for guesser_id_str, guessed_card_owner_id in guesses_for_this_card.items():
                    if guessed_card_owner_id == owner_id: # Если угадали владельца этой карты
                        num_correct_guesses_for_this_owner +=1
                        # Можно угадавшему тоже дать очки, если он не владелец карты
                        if int(guesser_id_str) != owner_id:
                             c.execute("UPDATE users SET rating = MAX(0, rating + 1) WHERE id = ?", (int(guesser_id_str),)) # Например, +1

                if num_correct_guesses_for_this_owner > 0:
                    c.execute("UPDATE users SET rating = MAX(0, rating + ?) WHERE id = ?", (num_correct_guesses_for_this_owner, owner_id))


        flash("Очки подсчитаны! Карты открыты.", "success")
        # set_game_over(True) # Возможно, игра не заканчивается сразу после открытия карт, а после нового раунда, если карты кончились
        db.commit(); broadcast_game_state_update()
    except sqlite3.Error as e_sql: db.rollback(); flash(f"Ошибка БД: {e_sql}", "danger"); print(traceback.format_exc(), file=sys.stderr)
    except Exception as e_general: db.rollback(); flash(f"Ошибка: {e_general}", "danger"); print(traceback.format_exc(), file=sys.stderr)
    return redirect(url_for('admin'))


@app.route("/new_round", methods=["POST"])
def new_round():
    if not session.get('is_admin'): flash('Доступ запрещен.', 'danger'); return redirect(url_for('login'))
    if g.game_over: flash("Игра окончена. Начните новую игру.", "warning"); return redirect(url_for('admin'))
    if not is_game_in_progress(): flash("Игра не начата.", "warning"); return redirect(url_for('admin'))
    db = get_db(); c = db.cursor(); active_subfolder_new_round = get_setting('active_subfolder')
    leader_who_finished_round = get_leading_user_id(); new_actual_leader_id = None
    try:
        new_actual_leader_id = determine_new_leader(leader_who_finished_round)
        if new_actual_leader_id is not None: set_leading_user_id(new_actual_leader_id); flash(f"Новый раунд! Ведущий: {get_user_name(new_actual_leader_id) or f'ID {new_actual_leader_id}'}.", "success")
        else: set_leading_user_id(None); flash("Новый раунд, но ведущий не определен (нет активных игроков?).", "warning")
        
        c.execute("UPDATE images SET owner_id = NULL, guesses = '{}', status = 'Занято:Админ' WHERE status LIKE 'На столе:%'") # Освобождаем карты со стола
        c.execute("UPDATE images SET guesses = '{}' WHERE status NOT LIKE 'На столе:%' AND guesses != '{}'") # Сбрасываем голоса на картах в руках
        set_setting("show_card_info", "false"); flash("Информация о картах скрыта для нового раунда.", "info")
        
        active_user_ids_new_round = [row['id'] for row in c.execute("SELECT id FROM users WHERE status = 'active' ORDER BY id").fetchall()]
        game_over_now_flag = False
        if not active_user_ids_new_round: flash("Нет активных игроков для раздачи карт.", "warning")
        elif not active_subfolder_new_round: flash("Активная колода не установлена. Карты не розданы.", "warning")
        else:
            available_cards_ids = [row['id'] for row in c.execute("SELECT id FROM images WHERE subfolder = ? AND status = 'Свободно'", (active_subfolder_new_round,)).fetchall()]
            random.shuffle(available_cards_ids); num_available_new_round = len(available_cards_ids); cards_actually_dealt_total = 0
            if num_available_new_round < len(active_user_ids_new_round): # Если карт меньше, чем активных игроков
                flash(f"В колоде '{active_subfolder_new_round}' карт ({num_available_new_round}) меньше, чем активных игроков ({len(active_user_ids_new_round)}). Игра окончена.", "danger")
                game_over_now_flag = True
            else:
                for user_id_nr_deal in active_user_ids_new_round:
                    if cards_actually_dealt_total < num_available_new_round:
                        c.execute("UPDATE images SET status = ?, owner_id = ? WHERE id = ?", (f"Занято:{user_id_nr_deal}", user_id_nr_deal, available_cards_ids[cards_actually_dealt_total]))
                        cards_actually_dealt_total += 1
                    else: # Этого не должно произойти, если предыдущая проверка верна
                        flash(f"Карты в '{active_subfolder_new_round}' закончились неожиданно. Роздано {cards_actually_dealt_total}.", "warning"); break
                flash(f"Роздано {cards_actually_dealt_total} новых карт.", "info")

        # Проверка, закончилась ли игра (например, у кого-то нет карт ПОСЛЕ раздачи)
        if not game_over_now_flag and active_user_ids_new_round: # Если игра еще не помечена как оконченная из-за нехватки карт в колоде
            for user_id_check_game_over in active_user_ids_new_round:
                if c.execute("SELECT COUNT(*) FROM images WHERE owner_id = ? AND status LIKE 'Занято:%'", (user_id_check_game_over,)).fetchone()[0] == 0:
                    game_over_now_flag = True; flash(f"У игрока {get_user_name(user_id_check_game_over) or f'ID {user_id_check_game_over}'} кончились карты!", "info"); break
        
        if game_over_now_flag: set_game_over(True); set_game_in_progress(False); flash("Игра окончена!", "danger")
        
        db.commit(); broadcast_game_state_update()
    except sqlite3.Error as e: db.rollback(); flash(f"Ошибка БД: {e}", "danger"); print(traceback.format_exc(), file=sys.stderr)
    except Exception as e_gen: db.rollback(); flash(f"Ошибка: {e_gen}", "danger"); print(traceback.format_exc(), file=sys.stderr)
    return redirect(url_for('admin', displayed_leader_id=new_actual_leader_id if new_actual_leader_id is not None else leader_who_finished_round))


# --- SocketIO события ---
@socketio.on('connect')
def handle_connect():
    user_code_on_connect = session.get('user_code')
    sid = request.sid
    print(f"SocketIO: Client connected: SID={sid}, User code from Flask session: {user_code_on_connect or 'N/A'}", file=sys.stderr)
    if user_code_on_connect:
        connected_users_socketio[sid] = user_code_on_connect
    else:
        print(f"SocketIO: Warning - Client {sid} connected without user_code in Flask session. Will not receive personalized updates until identified.", file=sys.stderr)
    
    # Отправляем состояние только этому клиенту
    try:
        with app.app_context(): # Нужен контекст приложения для get_db()
            initial_state = get_full_game_state_data(user_code_for_state=user_code_on_connect)
            emit('game_update', initial_state, room=sid)
    except Exception as e:
        print(f"SocketIO: Error sending initial state to SID {sid}: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)


@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    user_code_on_disconnect = connected_users_socketio.pop(sid, None)
    print(f"SocketIO: Client disconnected: SID={sid}, User code: {user_code_on_disconnect or 'N/A'}", file=sys.stderr)


# --- Запуск приложения ---
if __name__ == "__main__":
    # Инициализация игрового поля (визуальная часть)
    # Вызывается здесь, так как _current_game_board_pole_image_config - глобальная переменная
    if not _current_game_board_pole_image_config:
        print("Первичная инициализация визуализации игрового поля при запуске приложения...", file=sys.stderr)
        all_users_at_startup = []
        if os.path.exists(DB_PATH): # Проверяем, существует ли файл БД
            try:
                conn_startup = sqlite3.connect(DB_PATH); conn_startup.row_factory = sqlite3.Row
                cursor_startup = conn_startup.cursor()
                cursor_startup.execute("SELECT id, name, rating FROM users WHERE status = 'active'")
                all_users_at_startup = cursor_startup.fetchall(); conn_startup.close()
            except sqlite3.Error as e_startup_sql:
                print(f"Ошибка чтения пользователей для поля при старте (БД может быть еще не готова или пуста): {e_startup_sql}", file=sys.stderr)
        initialize_new_game_board_visuals(all_users_for_rating_check=all_users_at_startup)

    port = int(os.environ.get("PORT", 5000))
    debug_mode = os.environ.get("FLASK_DEBUG", "False").lower() in ['true', '1', 't']
    
    print(f"Запуск Flask-SocketIO приложения (через socketio.run()) на http://0.0.0.0:{port}/ с debug={debug_mode}", file=sys.stderr)
    # Для Render.com и аналогичных платформ, если Start Command - gunicorn, эта часть не будет основной точкой входа.
    # Но она полезна для локальной разработки.
    socketio.run(app, host="0.0.0.0", port=port, debug=debug_mode, allow_unsafe_werkzeug=True)
