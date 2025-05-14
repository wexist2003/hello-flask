import json
import sys # Додано для детального логування помилок
from flask import Flask, render_template, request, redirect, url_for, g, flash, session
import sqlite3
import os
import string
import random
import traceback # Додано для детального логування помилок
from flask_socketio import SocketIO, emit # <--- ДОБАВЛЕНО

app = Flask(__name__)
# Важно: Для продакшена установите SECRET_KEY через переменную окружения!
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your_very_secret_fallback_key_for_dev_only')
if app.config['SECRET_KEY'] == 'your_very_secret_fallback_key_for_dev_only':
    print("ПРЕДУПРЕЖДЕНИЕ: Используется SECRET_KEY по умолчанию для разработки. Установите переменную окружения SECRET_KEY для продакшена!", file=sys.stderr)
elif not app.config['SECRET_KEY']: # Эта проверка была в вашем коде, но теперь она избыточна, если есть fallback
    raise ValueError("Не установлена переменная окружения SECRET_KEY!")

socketio = SocketIO(app) # <--- ДОБАВЛЕНО: Инициализация SocketIO

DB_PATH = 'database.db'

# --- Инициализация БД ---
def init_db():
    print(f"DB Init: Attempting to initialize database at {os.path.abspath(DB_PATH)}", file=sys.stderr)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    print("init_db: Connection opened.", file=sys.stderr)
    try:
        print("init_db: Dropping tables...", file=sys.stderr)
        c.execute("DROP TABLE IF EXISTS users")
        c.execute("DROP TABLE IF EXISTS images")
        c.execute("DROP TABLE IF EXISTS settings")
        c.execute("DROP TABLE IF EXISTS deck_votes")
        print("init_db: Creating tables...", file=sys.stderr)
        c.execute("""CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, code TEXT UNIQUE NOT NULL, rating INTEGER DEFAULT 0, status TEXT DEFAULT 'pending' NOT NULL)""")
        c.execute("""CREATE TABLE images (id INTEGER PRIMARY KEY AUTOINCREMENT, subfolder TEXT NOT NULL, image TEXT NOT NULL, status TEXT, owner_id INTEGER, guesses TEXT DEFAULT '{}')""")
        c.execute("""CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)""")
        c.execute("""CREATE TABLE deck_votes (subfolder TEXT PRIMARY KEY, votes INTEGER DEFAULT 0)""")
        conn.commit()
        print("init_db: Tables created and committed.", file=sys.stderr)
        settings_to_init = {'game_over': 'false', 'game_in_progress': 'false', 'show_card_info': 'false'}
        for key, value in settings_to_init.items():
            try:
                c.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))
            except sqlite3.IntegrityError:
                c.execute("UPDATE settings SET value = ? WHERE key = ?", (value, key))
        conn.commit()
        print("init_db: Basic settings initialized/updated.", file=sys.stderr)
        image_folders = ['koloda1', 'ariadna', 'detstvo', 'odissey', 'pandora'] # Используем ваш список
        images_added_count = 0
        for folder in image_folders:
            folder_path = os.path.join(app.static_folder, 'images', folder) # Используем app.static_folder
            if os.path.exists(folder_path) and os.path.isdir(folder_path):
                for filename in os.listdir(folder_path):
                    if filename.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
                        try:
                            c.execute("SELECT 1 FROM images WHERE subfolder = ? AND image = ?", (folder, filename))
                            if c.fetchone() is None:
                                 c.execute("INSERT INTO images (subfolder, image, status, guesses) VALUES (?, ?, 'Свободно', '{}')", (folder, filename))
                                 images_added_count += 1
                        except sqlite3.Error as e:
                            print(f"Warning: Could not process image {folder}/{filename}: {e}", file=sys.stderr)
            else: print(f"Warning: Folder not found or is not a directory: {folder_path}", file=sys.stderr)
        if images_added_count > 0: print(f"init_db: Added {images_added_count} new images to the database.", file=sys.stderr)
        else: print("init_db: No new images were added.", file=sys.stderr)
        conn.commit()
    except sqlite3.Error as e: print(f"CRITICAL ERROR during init_db: {e}", file=sys.stderr); conn.rollback(); raise
    finally:
        if conn: conn.close(); print("init_db: Connection closed.", file=sys.stderr)
    print("DB Init: Database tables created/recreated.", file=sys.stderr)


# --- Конфигурация для Игрового Поля ---
GAME_BOARD_POLE_IMG_SUBFOLDER = "pole"
GAME_BOARD_POLE_IMAGES = [f"p{i}.jpg" for i in range(1, 8)]
DEFAULT_NUM_BOARD_CELLS = 40

_current_game_board_pole_image_config = []
_current_game_board_num_cells = 0
# --- Кінець Конфігурації ---

# Вызываем init_db() прямо здесь, при загрузке модуля.
# Это гарантирует, что БД будет создана/пересоздана перед тем, как приложение начнет обрабатывать запросы.
# Поскольку вам не нужна персистентность, это перезапишет БД при каждом старте процесса приложения.
print("DB Init: Calling init_db() on module load.", file=sys.stderr)
init_db()
print("DB Init: init_db() call completed on module load.", file=sys.stderr)

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
def get_setting(key):
    try:
        db = get_db()
        c = db.cursor()
        c.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = c.fetchone()
        return row['value'] if row else None
    except sqlite3.Error as e:
        print(f"Database error in get_setting for key '{key}': {e}", file=sys.stderr)
        return None

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
    if user_id is None:
        return None
    try:
        user_id_int = int(user_id) # Убедимся, что это число
        db = get_db()
        c = db.cursor()
        c.execute("SELECT name FROM users WHERE id = ?", (user_id_int,))
        user_name_row = c.fetchone()
        return user_name_row['name'] if user_name_row else None
    except (ValueError, TypeError, sqlite3.Error) as e:
        print(f"Error in get_user_name for ID '{user_id}': {e}", file=sys.stderr)
        return None

def is_game_in_progress():
    return get_setting('game_in_progress') == 'true'

def set_game_in_progress(state=True):
    return set_setting('game_in_progress', 'true' if state else 'false')

def is_game_over():
    return get_setting('game_over') == 'true'

def set_game_over(state=True):
    return set_setting('game_over', 'true' if state else 'false')

def generate_unique_code(length=8):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def get_leading_user_id():
    value = get_setting('leading_user_id')
    if value and value.strip():
        try:
            return int(value)
        except (ValueError, TypeError):
            print(f"Invalid leading_user_id value found in settings: {value}", file=sys.stderr)
            return None
    return None

def set_leading_user_id(user_id):
    value_to_set = str(user_id) if user_id is not None else ''
    return set_setting('leading_user_id', value_to_set)

def determine_new_leader(current_leader_id):
    db_local = get_db()
    c_local = db_local.cursor()
    try:
        c_local.execute("SELECT id FROM users WHERE status = 'active' ORDER BY id ASC")
        user_rows = c_local.fetchall()
        if not user_rows:
            print("determine_new_leader: Нет АКТИВНЫХ пользователей для выбора ведущего.", file=sys.stderr)
            return None
        active_user_ids = [row['id'] for row in user_rows]
        if current_leader_id is None or current_leader_id not in active_user_ids:
            print(f"determine_new_leader: Текущий ведущий не определен или не активен. Выбираем первого активного: {active_user_ids[0]}", file=sys.stderr)
            return active_user_ids[0]
        try:
            current_index = active_user_ids.index(current_leader_id)
            next_index = (current_index + 1) % len(active_user_ids)
            print(f"determine_new_leader: Текущий активный: {current_leader_id} (индекс {current_index}). Следующий активный: {active_user_ids[next_index]} (индекс {next_index})", file=sys.stderr)
            return active_user_ids[next_index]
        except ValueError:
            print(f"determine_new_leader: Ошибка - ID текущего ведущего {current_leader_id} не найден в списке активных {active_user_ids}. Выбираем первого активного.", file=sys.stderr)
            return active_user_ids[0]
    except sqlite3.Error as e:
        print(f"Database error in determine_new_leader: {e}", file=sys.stderr)
        return None
    except Exception as e_gen:
        print(f"Unexpected error in determine_new_leader: {e_gen}", file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)
        return None

def get_active_players_count(db_conn):
    try:
        cursor = db_conn.execute("SELECT COUNT(id) FROM users WHERE status = 'active'")
        count_row = cursor.fetchone()
        active_count = count_row[0] if count_row else 0
        print(f"[get_active_players_count] Active players found: {active_count}", flush=True, file=sys.stderr)
        return active_count
    except sqlite3.Error as e:
        print(f"Ошибка БД в get_active_players_count: {e}", flush=True, file=sys.stderr)
        return 0

def initialize_new_game_board_visuals(num_cells_for_board=None, all_users_for_rating_check=None):
    global _current_game_board_pole_image_config, _current_game_board_num_cells
    actual_num_cells = DEFAULT_NUM_BOARD_CELLS
    if num_cells_for_board is not None:
        actual_num_cells = num_cells_for_board
    elif all_users_for_rating_check:
        max_rating = 0
        for user_data_item in all_users_for_rating_check:
            user_rating = user_data_item.get('rating', 0) if isinstance(user_data_item, dict) else getattr(user_data_item, 'rating', 0)
            if isinstance(user_rating, int) and user_rating > max_rating:
                max_rating = user_rating
        actual_num_cells = max(DEFAULT_NUM_BOARD_CELLS, max_rating + 6)
    _current_game_board_num_cells = actual_num_cells
    _current_game_board_pole_image_config = []
    # Используем app.static_folder для корректного пути
    pole_image_folder_path = os.path.join(app.static_folder, 'images', GAME_BOARD_POLE_IMG_SUBFOLDER)

    if GAME_BOARD_POLE_IMAGES and os.path.exists(pole_image_folder_path) and os.path.isdir(pole_image_folder_path):
        for _ in range(_current_game_board_num_cells):
            random_pole_image_file = random.choice(GAME_BOARD_POLE_IMAGES)
            image_path_for_static = os.path.join('images', GAME_BOARD_POLE_IMG_SUBFOLDER, random_pole_image_file).replace("\\", "/")
            _current_game_board_pole_image_config.append(image_path_for_static)
    else:
        if not GAME_BOARD_POLE_IMAGES: print(f"ПОПЕРЕДЖЕННЯ: Список GAME_BOARD_POLE_IMAGES порожній.", file=sys.stderr)
        if not os.path.exists(pole_image_folder_path): print(f"ПОПЕРЕДЖЕННЯ: Папка для зображень поля '{pole_image_folder_path}' не знайдена.", file=sys.stderr)
        elif not os.path.isdir(pole_image_folder_path): print(f"ПОПЕРЕДЖЕННЯ: '{pole_image_folder_path}' не є папкою.", file=sys.stderr)
        default_placeholder = os.path.join('images', GAME_BOARD_POLE_IMG_SUBFOLDER, "p1.jpg").replace("\\", "/")
        _current_game_board_pole_image_config = [default_placeholder] * _current_game_board_num_cells
        print(f"Використовуються placeholder'и для ігрового поля: {default_placeholder}", file=sys.stderr)
    print(f"Візуалізацію ігрового поля ініціалізовано/оновлено для {_current_game_board_num_cells} клітинок.", file=sys.stderr)

def generate_game_board_data_for_display(all_users_data_for_board):
    global _current_game_board_pole_image_config, _current_game_board_num_cells
    if not _current_game_board_pole_image_config or _current_game_board_num_cells == 0:
        initialize_new_game_board_visuals(all_users_for_rating_check=all_users_data_for_board)
        if not _current_game_board_pole_image_config or _current_game_board_num_cells == 0:
            return []
    board_cells_data = []
    for i in range(_current_game_board_num_cells):
        cell_number = i + 1
        cell_image_path = "images/default_pole_image.png" # Относительно static
        if _current_game_board_pole_image_config:
            try:
                cell_image_path = _current_game_board_pole_image_config[i % len(_current_game_board_pole_image_config)]
            except (IndexError, TypeError) as e:
                print(f"Error getting board cell image for cell {cell_number}: {e}", file=sys.stderr)
        users_in_this_cell = []
        for user_data_item_board in all_users_data_for_board:
            user_rating_raw = user_data_item_board.get('rating') if isinstance(user_data_item_board, dict) else user_data_item_board['rating']
            user_name = user_data_item_board.get('name', "N/A") if isinstance(user_data_item_board, dict) else user_data_item_board['name']
            user_id_for_name = user_data_item_board.get('id', "N/A_ID") if isinstance(user_data_item_board, dict) else user_data_item_board['id']
            current_user_rating_int = 0
            if user_rating_raw is not None:
                try:
                    current_user_rating_int = int(user_rating_raw)
                except (ValueError, TypeError):
                    current_user_rating_int = 0
            if current_user_rating_int == cell_number:
                display_name = user_name if user_name and str(user_name).strip() else f"ID {user_id_for_name}"
                users_in_this_cell.append({'id': user_id_for_name, 'name': display_name, 'rating': current_user_rating_int})
        board_cells_data.append({
            'cell_number': cell_number,
            'image_path': cell_image_path, # Это относительный путь для static
            'users_in_cell': users_in_this_cell
        })
    return board_cells_data

# --- Функции для SocketIO ---
def get_full_game_state_data(user_code_for_state=None):
    db = get_db()
    current_g_user = None
    if user_code_for_state: # Если передан код, пытаемся найти пользователя
        user_row_for_state = db.execute("SELECT id, name, code, rating, status FROM users WHERE code = ?", (user_code_for_state,)).fetchone()
        if user_row_for_state:
            current_g_user = dict(user_row_for_state)

    active_subfolder = get_setting('active_subfolder')
    db_current_leader_id = get_leading_user_id()
    num_active_players_val = get_active_players_count(db)

    game_state = {
        'game_in_progress': is_game_in_progress(),
        'game_over': is_game_over(),
        'show_card_info': get_setting("show_card_info") == "true",
        'active_subfolder': active_subfolder,
        'db_current_leader_id': db_current_leader_id,
        'potential_next_leader_id': determine_new_leader(db_current_leader_id),
        'num_active_players': num_active_players_val,
        'table_images': [],
        'user_cards': [],
        'all_users_for_guessing': [],
        'on_table_status': False,
        'is_current_user_the_db_leader': False,
        'leader_pole_pictogram_path': None, # Будет заполнен относительным путем
        'leader_pictogram_rating_display': None,
        'game_board': [],
        'current_num_board_cells': _current_game_board_num_cells,
        'current_user_data': current_g_user, # Данные ТЕКУЩЕГО пользователя (для которого обновляется интерфейс)
        'num_cards_on_table': 0, # Будет обновлено ниже
        # Дополнительные переменные для шаблона, которые могут быть вычислены здесь
        'all_cards_placed_for_guessing_phase_to_template': False, # Будет обновлено
        'flashed_messages': [] # Для передачи сообщений через SocketIO
    }

    num_cards_on_table_val = 0
    if game_state['game_in_progress'] and active_subfolder:
        raw_table_cards = db.execute("""
            SELECT i.id, i.image, i.subfolder, i.owner_id, u.name as owner_name, i.guesses
            FROM images i LEFT JOIN users u ON i.owner_id = u.id
            WHERE i.subfolder = ? AND i.status LIKE 'На столе:%' AND (u.status = 'active' OR u.status IS NULL)
        """, (active_subfolder,)).fetchall()
        num_cards_on_table_val = len(raw_table_cards)
        game_state['num_cards_on_table'] = num_cards_on_table_val

        all_cards_placed = (num_active_players_val > 0 and num_cards_on_table_val >= num_active_players_val)
        game_state['all_cards_placed_for_guessing_phase_to_template'] = all_cards_placed

        for card_row in raw_table_cards:
            guesses_data = json.loads(card_row['guesses'] or '{}')
            my_guess_val = None
            if current_g_user and current_g_user['status'] == 'active' and all_cards_placed and not game_state['show_card_info'] and card_row['owner_id'] != current_g_user['id']:
                my_guess_val = guesses_data.get(str(current_g_user['id']))

            game_state['table_images'].append({
                'id': card_row['id'], 'image': card_row['image'], 'subfolder': card_row['subfolder'],
                'owner_id': card_row['owner_id'], 'owner_name': get_user_name(card_row['owner_id']) or "Неизвестный",
                'guesses': guesses_data, 'my_guess_for_this_card_value': my_guess_val
            })

        if current_g_user and current_g_user['status'] == 'active':
            user_cards_db = db.execute("SELECT id, image, subfolder FROM images WHERE owner_id = ? AND subfolder = ? AND status LIKE 'Занято:%'",
                                       (current_g_user['id'], active_subfolder)).fetchall()
            game_state['user_cards'] = [{'id': r['id'], 'image': r['image'], 'subfolder': r['subfolder']} for r in user_cards_db]

            game_state['on_table_status'] = any(tc['owner_id'] == current_g_user['id'] for tc in game_state['table_images'])
            
            # Исключаем текущего пользователя из списка для угадывания
            other_active_users_db = db.execute("SELECT id, name FROM users WHERE status = 'active' AND id != ?", (current_g_user['id'],)).fetchall()
            game_state['all_users_for_guessing'] = [{'id': u['id'], 'name': u['name']} for u in other_active_users_db]


            if db_current_leader_id is not None:
                game_state['is_current_user_the_db_leader'] = (current_g_user['id'] == db_current_leader_id)

            if game_state['is_current_user_the_db_leader'] and not game_state['on_table_status'] and not game_state['show_card_info'] and not all_cards_placed:
                leader_rating = int(current_g_user.get('rating', 0))
                game_state['leader_pictogram_rating_display'] = leader_rating
                if leader_rating > 0 and _current_game_board_pole_image_config and \
                   leader_rating <= _current_game_board_num_cells and \
                   (leader_rating - 1) < len(_current_game_board_pole_image_config):
                    game_state['leader_pole_pictogram_path'] = _current_game_board_pole_image_config[leader_rating - 1] # Относительный путь

    all_active_users_for_board = db.execute("SELECT id, name, rating FROM users WHERE status = 'active'").fetchall()
    game_state['game_board'] = generate_game_board_data_for_display(all_active_users_for_board)
    game_state['current_num_board_cells'] = _current_game_board_num_cells # Убедимся, что это актуально
    
    # Собираем flash сообщения для отправки через SocketIO (опционально)
    # Это сложнее, так как flash сообщения обычно привязаны к HTTP сессии.
    # Можно создать свой механизм или просто не передавать их через SocketIO,
    # а полагаться на JavaScript для отображения уведомлений.
    # game_state['flashed_messages'] = [{'message': msg, 'category': cat} for cat, msg in get_flashed_messages(with_categories=True)]

    return game_state

def broadcast_game_state_update(user_code_trigger=None, specific_sid=None):
    """Отправляет обновленное состояние игры. Если specific_sid указан, то только ему."""
    if specific_sid:
        # user_code_trigger должен быть кодом пользователя для этого SID
        state_data = get_full_game_state_data(user_code_for_state=user_code_trigger)
        socketio.emit('game_update', state_data, room=specific_sid)
        print(f"SocketIO: Emitted specific game_update to SID {specific_sid} (triggered by user {user_code_trigger or 'N/A'})", file=sys.stderr)
    else:
        # Отправка всем: для каждого подключенного SID получаем его user_code из сессии SocketIO
        # и отправляем персонализированное состояние.
        # Это более сложная логика, которая требует хранения user_code в сессии SocketIO.
        # Простой вариант: отправить общее состояние или состояние для триггера всем.
        # Более правильный: для каждого клиента генерировать свое состояние.
        # Пока что, если user_code_trigger есть, отправим его "перспективу" всем.
        # Если нет, то общее состояние.
        state_data_to_broadcast = get_full_game_state_data(user_code_for_state=user_code_trigger)
        socketio.emit('game_update', state_data_to_broadcast)
        print(f"SocketIO: Emitted broadcast game_update (perspective of user {user_code_trigger or 'general'})", file=sys.stderr)

def broadcast_user_list_update():
    """Отправляет обновление списка пользователей (например, для админки)."""
    # Эта функция должна собирать данные о пользователях и отправлять их.
    # Пока что это заглушка.
    # users_data = ... собрать данные о пользователях ...
    # socketio.emit('user_list_updated', users_data)
    print("SocketIO: Placeholder for broadcast_user_list_update() called.", file=sys.stderr)
    # Временное решение: можно просто триггерить общее обновление состояния, если админка его слушает
    broadcast_game_state_update()


def broadcast_deck_votes_update():
    """Отправляет обновление голосов за колоды."""
    # Эта функция должна собирать данные о голосах и отправлять их.
    # Пока что это заглушка.
    # deck_votes_data = ... собрать данные о голосах ...
    # socketio.emit('deck_votes_updated', deck_votes_data)
    print("SocketIO: Placeholder for broadcast_deck_votes_update() called.", file=sys.stderr)
    # Временное решение: можно просто триггерить общее обновление состояния, если index.html его слушает
    # или создать специальное событие для index.html
    db = get_db()
    c = db.cursor()
    try:
        c.execute("SELECT i.subfolder, COALESCE(dv.votes, 0) as votes FROM (SELECT DISTINCT subfolder FROM images ORDER BY subfolder) as i LEFT JOIN deck_votes as dv ON i.subfolder = dv.subfolder;")
        deck_votes_data = [dict(row) for row in c.fetchall()]
        socketio.emit('deck_votes_updated', {'deck_votes': deck_votes_data})
        print(f"SocketIO: Emitted deck_votes_updated: {len(deck_votes_data)} decks.", file=sys.stderr)
    except sqlite3.Error as e:
        print(f"Error broadcasting deck votes: {e}", file=sys.stderr)


# --- Глобальные переменные и функции для Jinja ---
app.jinja_env.globals.update(
    get_user_name=get_user_name,
    get_leading_user_id=get_leading_user_id
)

# --- Маршруты и before_request ---
@app.before_request
def before_request_func(): # Переименовал, чтобы не было конфликта имен
    db = get_db()
    c = db.cursor()
    code_param = request.args.get('code') or \
                 (request.view_args.get('code') if request.view_args else None) or \
                 session.get('user_code') # Пытаемся взять из сессии Flask

    g.user = None
    g.user_id = None

    if code_param:
        try:
            user_row = c.execute("SELECT id, name, code, rating, status FROM users WHERE code = ?", (code_param,)).fetchone()
            if user_row:
                g.user = dict(user_row)
                g.user_id = user_row['id']
                # Сохраняем в сессию Flask для доступа в обработчиках SocketIO и между HTTP запросами
                session['user_id'] = user_row['id']
                session['user_name'] = user_row['name']
                session['user_code'] = user_row['code']
                session['user_status'] = user_row['status']
                session['user_rating'] = user_row['rating']
            else: # Если код был передан, но невалиден, очищаем сессию, если код в ней совпадает
                if 'user_code' in session and session['user_code'] == code_param:
                    keys_to_pop = ['user_id', 'user_name', 'user_code', 'user_status', 'user_rating']
                    for key in keys_to_pop:
                        session.pop(key, None)
        except sqlite3.Error as e:
            print(f"Database error in before_request_func checking code '{code_param}': {e}", file=sys.stderr)

    g.show_card_info = get_setting("show_card_info") == "true"
    g.game_over = is_game_over()
    g.game_in_progress = is_game_in_progress()

# --- Маршруты автентифікації та головна сторінка ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    # (Ваш код без изменений)
    if request.method == 'POST':
        password_attempt = request.form.get('password')
        correct_password = os.environ.get('ADMIN_PASSWORD')
        if not correct_password:
             print("ПРЕДУПРЕЖДЕНИЕ: Не установлена переменная окружения ADMIN_PASSWORD!", file=sys.stderr)
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
    # (Ваш код без изменений)
    session.pop('is_admin', None)
    # Также очищаем данные пользователя при выходе из админки, если это нужно
    # keys_to_pop = ['user_id', 'user_name', 'user_code', 'user_status', 'user_rating']
    # for key in keys_to_pop:
    #     session.pop(key, None)
    flash('Вы вышли из системы.', 'info')
    return redirect(url_for('login')) # Или на главную, если есть разница

@app.route("/")
def index():
    # (Ваш код index, но теперь он может получать обновления через SocketIO для голосов)
    deck_votes_data = []
    db = get_db()
    c = db.cursor()
    try:
        c.execute("SELECT i.subfolder, COALESCE(dv.votes, 0) as votes FROM (SELECT DISTINCT subfolder FROM images ORDER BY subfolder) as i LEFT JOIN deck_votes as dv ON i.subfolder = dv.subfolder;")
        deck_votes_data = [dict(row) for row in c.fetchall()]
    except sqlite3.Error as e:
        print(f"Ошибка чтения данных для голосования на стартовой странице: {e}", file=sys.stderr)
        flash(f"Не удалось загрузить данные о колодах: {e}", "danger")
    current_vote = session.get('voted_for_deck')
    active_subfolder = get_setting('active_subfolder') or "Не выбрана"
    return render_template("index.html",
                           deck_votes=deck_votes_data,
                           current_vote=current_vote,
                           active_subfolder=active_subfolder)


@app.route('/init_db_route_for_dev_only_make_sure_to_secure_or_remove')
def init_db_route():
    # (Ваш код без изменений)
    # init_db()
    flash("База данных инициализирована (если функция init_db активна).", "info")
    return redirect(url_for('index'))

@app.route("/login_player") # GET запрос для отображения формы
def login_player():
    # (Ваш код без изменений, но он теперь использует session)
    user_code_from_session = session.get('user_code')
    if user_code_from_session:
        print(f"Player session found (code: {user_code_from_session}). Redirecting to user page.", file=sys.stderr)
        return redirect(url_for('user', code=user_code_from_session))
    else:
        print("No active player session found. Showing login_player.html form.", file=sys.stderr)
        return render_template('login_player.html')

@app.route("/register_or_login_player", methods=["POST"])
def register_or_login_player():
    # (Ваш код с добавлением broadcast_user_list_update)
    player_name = request.form.get('name', '').strip()
    if not player_name: flash("Имя не может быть пустым.", "warning"); return redirect(url_for('login_player'))
    if len(player_name) > 50: flash("Имя слишком длинное (максимум 50 символов).", "warning"); return redirect(url_for('login_player'))
    db = get_db()
    c = db.cursor()
    user_code = None; user_id = None; user_status = 'pending'
    try:
        c.execute("SELECT id, code, status FROM users WHERE name = ?", (player_name,))
        existing_user = c.fetchone()
        if existing_user:
            user_id = existing_user['id']; user_code = existing_user['code']; user_status = existing_user['status']
            flash(f"С возвращением, {player_name}!", "info")
        else:
            user_code = generate_unique_code()
            user_initial_status = 'pending' if is_game_in_progress() else 'active'
            flash_msg_status = "Вы присоединились как наблюдатель. Ваше участие начнется со следующей НОВОЙ игры." if user_initial_status == 'pending' else "Вы успешно зарегистрированы и являетесь активным участником."
            try:
                c.execute("INSERT INTO users (name, code, status) VALUES (?, ?, ?)", (player_name, user_code, user_initial_status))
                user_id = c.lastrowid; db.commit(); user_status = user_initial_status
                flash(f"Добро пожаловать, {player_name}! {flash_msg_status}", "success")
                broadcast_user_list_update() # <--- Обновляем список пользователей
            except sqlite3.IntegrityError: db.rollback(); flash("Это имя уже занято.", "danger"); return redirect(url_for('login_player'))
            except sqlite3.Error as e_insert: db.rollback(); flash(f"Ошибка базы данных: {e_insert}", "danger"); return redirect(url_for('login_player'))
        if user_code:
            session['user_id'] = user_id; session['user_name'] = player_name; session['user_code'] = user_code
            session['user_status'] = user_status; session['user_rating'] = db.execute("SELECT rating FROM users WHERE id = ?", (user_id,)).fetchone()['rating']
            session.pop('is_admin', None)
            return redirect(url_for('user', code=user_code))
        else: flash("Произошла ошибка.", "danger"); return redirect(url_for('login_player'))
    except sqlite3.Error as e: flash(f"Ошибка базы данных: {e}", "danger"); return redirect(url_for('login_player'))
    except Exception as e_general: print(traceback.format_exc(), file=sys.stderr); flash(f"Ошибка: {e_general}", "danger"); return redirect(url_for('login_player'))

@app.route('/vote_deck', methods=['POST'])
def vote_deck():
    # (Ваш код с добавлением broadcast_deck_votes_update)
    new_deck = request.form.get('subfolder')
    previous_deck = session.get('voted_for_deck')
    if not new_deck: flash("Ошибка: Колода для голосования не была выбрана.", "warning"); return redirect(url_for('index'))
    if new_deck == previous_deck: flash(f"Вы уже голосовали за колоду '{new_deck}'.", "info"); return redirect(url_for('index'))
    db = get_db(); c = db.cursor()
    try:
        if previous_deck: c.execute("UPDATE deck_votes SET votes = MAX(0, votes - 1) WHERE subfolder = ?", (previous_deck,))
        c.execute("INSERT OR IGNORE INTO deck_votes (subfolder, votes) VALUES (?, 0)", (new_deck,))
        c.execute("UPDATE deck_votes SET votes = votes + 1 WHERE subfolder = ?", (new_deck,))
        db.commit(); session['voted_for_deck'] = new_deck
        flash(f"Ваш голос за колоду '{new_deck}' учтен!", "success")
        broadcast_deck_votes_update() # <--- Обновляем голоса
    except sqlite3.Error as e: db.rollback(); flash(f"Не удалось учесть голос: {e}", "danger")
    except Exception as e_general: flash("Произошла ошибка при голосовании.", "danger")
    return redirect(url_for('index'))

@app.route("/admin", methods=["GET", "POST"])
def admin():
    # (Ваш код маршрута admin)
    # ВАЖНО: После действий, изменяющих состояние игры или пользователей (создание, удаление, старт игры и т.д.),
    # нужно будет вызывать broadcast_game_state_update() или broadcast_user_list_update().
    # Это будет сделано на следующих этапах. Пока оставляем вашу логику.
    if not session.get('is_admin'):
        flash('Для доступа к этой странице требуется авторизация администратора.', 'warning')
        return redirect(url_for('login', next=request.url))
    db = get_db(); c = db.cursor()
    current_leader_from_db = get_leading_user_id()
    potential_next_leader_id = determine_new_leader(current_leader_from_db)
    leader_to_focus_on_id = current_leader_from_db
    if request.method == "GET":
        displayed_leader_id_from_url_str = request.args.get('displayed_leader_id')
        if displayed_leader_id_from_url_str:
            try:
                c.execute("SELECT 1 FROM users WHERE id = ? AND status = 'active'", (int(displayed_leader_id_from_url_str),))
                if c.fetchone(): leader_to_focus_on_id = int(displayed_leader_id_from_url_str)
                else: flash(f"Попытка сфокусироваться на неактивном ID: {displayed_leader_id_from_url_str}.", "warning")
            except (ValueError, TypeError): pass
    current_active_subfolder = get_setting('active_subfolder') or ''
    if request.method == "POST":
        action_handled = False; leader_for_redirect = leader_to_focus_on_id
        try:
            if "name" in request.form and "num_cards" in request.form and not any(key in request.form for key in ["delete_user_id", "active_subfolder", "toggle_show_card_info", "reset_game_board_visuals"]):
                name_admin_form = request.form.get("name", "").strip()
                try: num_cards_admin_form = int(request.form.get("num_cards", 3)); assert num_cards_admin_form >= 0
                except (ValueError, AssertionError): flash("Некорректное кол-во карт.", "warning"); num_cards_admin_form = 0
                if not name_admin_form: flash("Имя не может быть пустым.", "warning")
                else:
                    try:
                        user_code = generate_unique_code(); initial_status_admin_create = 'pending' if is_game_in_progress() else 'active'
                        c.execute("INSERT INTO users (name, code, rating, status) VALUES (?, ?, 0, ?)", (name_admin_form, user_code, initial_status_admin_create))
                        user_id_admin_form = c.lastrowid; flash(f"Пользователь '{name_admin_form}' (код: {user_code}, статус: {initial_status_admin_create}) добавлен.", "success")
                        if get_leading_user_id() is None and initial_status_admin_create == 'active':
                            set_leading_user_id(user_id_admin_form); leader_for_redirect = user_id_admin_form; flash(f"'{name_admin_form}' назначен ведущим.", "info")
                        if initial_status_admin_create == 'active' and current_active_subfolder and num_cards_admin_form > 0:
                            c.execute("SELECT id FROM images WHERE subfolder = ? AND status = 'Свободно' ORDER BY RANDOM() LIMIT ?", (current_active_subfolder, num_cards_admin_form))
                            cards_to_deal = c.fetchall(); num_dealt = len(cards_to_deal)
                            if num_dealt < num_cards_admin_form: flash(f"Внимание: Недостаточно карт. Роздано {num_dealt}.", "warning")
                            for card_admin_deal in cards_to_deal: c.execute("UPDATE images SET status = ?, owner_id = ? WHERE id = ?", (f"Занято:{user_id_admin_form}", user_id_admin_form, card_admin_deal['id']))
                            flash(f"Активному '{name_admin_form}' роздано {num_dealt} карт(ы).", "info")
                        elif num_cards_admin_form > 0: flash(f"Пользователь '{name_admin_form}' добавлен как ожидающий, карты не розданы.", "info")
                        db.commit(); action_handled = True; broadcast_user_list_update() # <---
                    except sqlite3.IntegrityError: db.rollback(); flash("Пользователь с таким именем или кодом уже существует.", "danger")
                    except sqlite3.Error as e_sql_user_add: db.rollback(); flash(f"Ошибка БД: {e_sql_user_add}", "danger")
            elif "active_subfolder" in request.form:
                new_active_subfolder = request.form.get("active_subfolder")
                set_setting("active_subfolder", new_active_subfolder if new_active_subfolder else "")
                flash(f"Активная колода изменена на '{new_active_subfolder or 'Не выбрана'}'.", "success" if new_active_subfolder else "info")
                db.commit(); action_handled = True; broadcast_game_state_update() # <---
            elif "delete_user_id" in request.form:
                user_id_to_delete_str = request.form.get("delete_user_id")
                try:
                    user_id_to_delete = int(user_id_to_delete_str); user_to_delete_name = get_user_name(user_id_to_delete) or f"ID {user_id_to_delete}"
                    current_leader_before_delete = get_leading_user_id()
                    c.execute("UPDATE images SET status = 'Свободно', owner_id = NULL, guesses = '{}' WHERE owner_id = ?", (user_id_to_delete,))
                    c.execute("DELETE FROM users WHERE id = ?", (user_id_to_delete,)); deleted_count = c.rowcount
                    if deleted_count > 0:
                        flash(f"Пользователь '{user_to_delete_name}' удален.", "success")
                        if current_leader_before_delete == user_id_to_delete:
                            flash(f"Удаленный '{user_to_delete_name}' был ведущим.", "info")
                            new_leader_after_delete = determine_new_leader(user_id_to_delete)
                            set_leading_user_id(new_leader_after_delete); leader_for_redirect = new_leader_after_delete
                            flash(f"Новым ведущим назначен '{get_user_name(new_leader_after_delete) or f'ID {new_leader_after_delete}' if new_leader_after_delete else 'Никто'}'.", "info")
                    else: flash(f"Пользователь с ID {user_id_to_delete} не найден.", "warning")
                    db.commit(); action_handled = True; broadcast_user_list_update(); broadcast_game_state_update() # <---
                except ValueError: flash("Некорректный ID для удаления.", "danger")
                except sqlite3.Error as e_sql_user_delete: db.rollback(); flash(f"Ошибка БД: {e_sql_user_delete}", "danger")
            elif 'toggle_show_card_info' in request.form:
                new_show_info = not (get_setting('show_card_info') == 'true')
                set_setting('show_card_info', 'true' if new_show_info else 'false'); db.commit()
                flash(f"Отображение информации о картах {'включено' if new_show_info else 'выключено'}.", "info")
                action_handled = True; broadcast_game_state_update() # <---
            elif 'reset_game_board_visuals' in request.form:
                c.execute("SELECT id, name, rating FROM users WHERE status = 'active'")
                initialize_new_game_board_visuals(all_users_for_rating_check=c.fetchall())
                flash("Визуализация игрового поля сброшена (на основе активных игроков).", "success")
                action_handled = True; broadcast_game_state_update() # <---
            if action_handled: return redirect(url_for('admin', displayed_leader_id=leader_for_redirect if leader_for_redirect is not None else ''))
        except sqlite3.Error as e_sql_post: db.rollback(); flash(f"Ошибка БД POST: {e_sql_post}", "danger"); print(traceback.format_exc(), file=sys.stderr)
        except Exception as e_general_post: flash(f"Ошибка POST: {e_general_post}", "danger"); print(traceback.format_exc(), file=sys.stderr)
    users_for_template = [dict(row) for row in c.execute("SELECT id, name, code, rating, status FROM users ORDER BY name ASC").fetchall()]
    active_users_for_board = [u for u in users_for_template if u['status'] == 'active']
    game_board_data_for_template = generate_game_board_data_for_display(active_users_for_board)
    images_for_template = [dict(img_row, guesses=json.loads(img_row['guesses'] or '{}')) for img_row in c.execute("SELECT id, subfolder, image, status, owner_id, guesses FROM images ORDER BY subfolder, id").fetchall()]
    image_owners_for_template = {img['id']: img['owner_id'] for img in images_for_template if img.get('owner_id') is not None}
    free_image_count_for_template = sum(1 for img in images_for_template if img.get('status') == 'Свободно' and img.get('subfolder') == current_active_subfolder)
    all_guesses_for_template = {img['id']: img['guesses'] for img in images_for_template if img['guesses']}
    # ... (остальная логика сбора данных для admin.html)
    guess_counts_by_user_for_template = {u['id']: 0 for u in active_users_for_board}
    user_has_duplicate_guesses_for_template = {u['id']: False for u in active_users_for_board}
    if all_guesses_for_template and active_users_for_board:
        for user_item in active_users_for_board:
            user_id_str = str(user_item['id']); guesses_made = []
            for guesses_for_image in all_guesses_for_template.values():
                if user_id_str in guesses_for_image:
                    guesses_made.append(guesses_for_image[user_id_str])
                    guess_counts_by_user_for_template[user_item['id']] += 1
            if len(guesses_made) > len(set(guesses_made)): user_has_duplicate_guesses_for_template[user_item['id']] = True
    subfolders_for_template = [row['subfolder'] for row in c.execute("SELECT DISTINCT subfolder FROM images ORDER BY subfolder").fetchall()]
    # (Продолжение вашей логики для admin.html)
    return render_template("admin.html", users=users_for_template, images=images_for_template, subfolders=subfolders_for_template,
                           active_subfolder=current_active_subfolder, db_current_leader_id=current_leader_from_db,
                           admin_focus_leader_id=leader_to_focus_on_id, potential_next_leader_id=potential_next_leader_id,
                           free_image_count=free_image_count_for_template, image_owners=image_owners_for_template,
                           guess_counts_by_user=guess_counts_by_user_for_template, all_guesses=all_guesses_for_template,
                           user_has_duplicate_guesses=user_has_duplicate_guesses_for_template, game_board=game_board_data_for_template,
                           get_user_name_func=get_user_name, current_num_board_cells=_current_game_board_num_cells)


@app.route("/start_new_game", methods=["POST"])
def start_new_game():
    # (Ваш код с добавлением broadcast_game_state_update)
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
        c.execute("UPDATE images SET owner_id = NULL, guesses = '{}', status = 'Занято:Админ'")
        c.execute("UPDATE images SET status = 'Свободно' WHERE subfolder = ?", (selected_deck,))
        set_game_over(False); set_setting("show_card_info", "false"); set_setting("active_subfolder", selected_deck); set_game_in_progress(False)
        first_active_user = c.execute("SELECT id FROM users WHERE status = 'active' ORDER BY id LIMIT 1").fetchone()
        if first_active_user: new_leader_id_sng = first_active_user['id']; set_leading_user_id(new_leader_id_sng)
        else: set_leading_user_id(None)
        initialize_new_game_board_visuals(all_users_for_rating_check=c.execute("SELECT id, name, rating FROM users WHERE status = 'active'").fetchall())
        db.commit()
        active_user_ids_sng = [row['id'] for row in c.execute("SELECT id FROM users WHERE status = 'active' ORDER BY id").fetchall()]
        num_active_users = len(active_user_ids_sng); num_total_dealt = 0
        if not active_user_ids_sng: flash("Активные пользователи не найдены.", "warning")
        else:
            available_cards_ids = [row['id'] for row in c.execute("SELECT id FROM images WHERE subfolder = ? AND status = 'Свободно'", (selected_deck,)).fetchall()]
            random.shuffle(available_cards_ids); num_available = len(available_cards_ids)
            if num_available < num_active_users * num_cards_per_player: flash(f"Внимание: Карт ({num_available}) < чем нужно.", "warning")
            card_index = 0
            for user_id_sng_deal in active_user_ids_sng:
                cards_dealt_to_user = 0
                for _ in range(num_cards_per_player):
                    if card_index < num_available:
                        c.execute("UPDATE images SET status = ?, owner_id = ? WHERE id = ?", (f"Занято:{user_id_sng_deal}", user_id_sng_deal, available_cards_ids[card_index]))
                        card_index += 1; cards_dealt_to_user += 1
                    else: break
                num_total_dealt += cards_dealt_to_user
                if card_index >= num_available: break
            flash(f"Новая игра! Колода: '{selected_deck}'. Роздано: {num_total_dealt}.", "success")
            if new_leader_id_sng: flash(f"Ведущий: {get_user_name(new_leader_id_sng)}.", "info")
        set_game_in_progress(True); db.commit()
        broadcast_game_state_update() # <--- Обновляем всех клиентов
    except sqlite3.Error as e: db.rollback(); flash(f"Ошибка БД: {e}", "danger")
    except Exception as e_gen: db.rollback(); flash(f"Ошибка: {e_gen}", "danger"); print(traceback.format_exc(), file=sys.stderr)
    return redirect(url_for('admin', displayed_leader_id=new_leader_id_sng if new_leader_id_sng is not None else ''))

@app.route('/user/<code>')
def user(code):
    # (Ваш код маршрута user)
    # ВАЖНО: Этот маршрут теперь будет отдавать базовый HTML, а JS на клиенте
    # будет запрашивать и отображать данные через SocketIO.
    # Поэтому, многие переменные, передаваемые в render_template, станут не нужны здесь.
    # (Ваш существующий код user() без изменений для этого шага, кроме broadcast)
    if g.user is None:
        session.pop('user_id', None); session.pop('user_name', None); session.pop('user_code', None)
        flash("Пользователя не найдено. Пожалуйста, войдите.", "warning")
        return redirect(url_for('login_player'))
    db = get_db()
    session['user_id'] = g.user['id']; session['user_name'] = g.user['name']; session['user_code'] = g.user['code']
    session.pop('is_admin', None)
    is_pending_player = g.user['status'] == 'pending'
    active_subfolder = get_setting('active_subfolder')
    user_cards = []
    if not is_pending_player and active_subfolder and g.game_in_progress:
        user_cards = db.execute("SELECT id, image, subfolder FROM images WHERE owner_id = ? AND subfolder = ? AND status LIKE 'Занято:%'", (g.user['id'], active_subfolder)).fetchall()
    num_active_players = get_active_players_count(db)
    num_cards_on_table = 0
    if g.game_in_progress and active_subfolder:
        num_cards_on_table = db.execute("SELECT COUNT(id) FROM images WHERE subfolder = ? AND status LIKE 'На столе:%'", (active_subfolder,)).fetchone()[0]
    all_cards_placed_for_guessing_phase = (g.game_in_progress and num_active_players > 0 and num_cards_on_table >= num_active_players)
    table_cards_raw = []
    if g.game_in_progress and active_subfolder:
        table_cards_raw = db.execute("SELECT i.id, i.image, i.subfolder, i.owner_id, u.name as owner_name, i.guesses FROM images i LEFT JOIN users u ON i.owner_id = u.id WHERE i.subfolder = ? AND i.status LIKE 'На столе:%' AND (u.status = 'active' OR u.status IS NULL)", (active_subfolder,)).fetchall()
    table_cards_for_template = []; on_table_status = False
    for card_row in table_cards_raw:
        if not is_pending_player and card_row['owner_id'] == g.user['id']: on_table_status = True
        guesses_data = json.loads(card_row['guesses'] or '{}'); my_guess_val = None
        if not is_pending_player and g.game_in_progress and all_cards_placed_for_guessing_phase and not g.show_card_info and card_row['owner_id'] != g.user['id']:
            my_guess_val = guesses_data.get(str(g.user['id']))
        table_cards_for_template.append({'id': card_row['id'], 'image': card_row['image'], 'subfolder': card_row['subfolder'], 'owner_id': card_row['owner_id'], 'owner_name': get_user_name(card_row['owner_id']) or "Неизвестный", 'guesses': guesses_data, 'my_guess_for_this_card_value': my_guess_val, 'has_guessed': my_guess_val is not None})
    other_active_users_for_template = []
    if not is_pending_player and g.game_in_progress:
        other_active_users_for_template = db.execute("SELECT id, name FROM users WHERE status = 'active' AND id != ?", (g.user['id'],)).fetchall()
    leader_id_from_db = get_leading_user_id(); is_current_user_the_db_leader = False
    if not is_pending_player and leader_id_from_db is not None and g.game_in_progress: is_current_user_the_db_leader = (g.user['id'] == leader_id_from_db)
    leader_pole_pictogram_path = None; leader_pictogram_rating_display = None
    if is_current_user_the_db_leader and not on_table_status and g.game_in_progress and not g.show_card_info and not all_cards_placed_for_guessing_phase:
        current_leader_actual_rating = int(g.user.get('rating', 0)) if g.user else 0
        leader_pictogram_rating_display = current_leader_actual_rating
        if current_leader_actual_rating > 0 and _current_game_board_pole_image_config and current_leader_actual_rating <= _current_game_board_num_cells and (current_leader_actual_rating - 1) < len(_current_game_board_pole_image_config):
            leader_pole_pictogram_path = url_for('static', filename=_current_game_board_pole_image_config[current_leader_actual_rating - 1])
    potential_next_leader_id_for_user_page = determine_new_leader(leader_id_from_db) if leader_id_from_db and g.game_in_progress else None
    game_board_data = generate_game_board_data_for_display(db.execute("SELECT id, name, rating FROM users WHERE status = 'active'").fetchall())

    # Для SocketIO, мы не будем передавать все эти данные через render_template.
    # Клиент запросит их или получит при подключении.
    # Но для первоначальной отрисовки оставим user_data.
    return render_template('user.html',
                           user_data_for_init=dict(g.user) if g.user else None
                           # Остальные данные будут загружены через JS и SocketIO
                           # Мы можем передать некоторые НЕдинамические данные или данные для первоначальной структуры, если это нужно JS
                           # Например, user_code для JS, чтобы он мог себя идентифицировать при запросе состояния
                          )


@app.route("/user/<code>/guess/<int:image_id>", methods=["POST"])
def guess_image(code, image_id):
    # (Ваш код с добавлением broadcast_game_state_update)
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
        broadcast_game_state_update(user_code_trigger=code) # <--- Обновляем всех
    except (ValueError, TypeError): flash("Неверный ID игрока.", "danger")
    except sqlite3.Error as e: db.rollback(); flash(f"Ошибка БД: {e}", "danger"); print(traceback.format_exc(), file=sys.stderr)
    except Exception as e_gen: flash(f"Ошибка: {e_gen}", "danger"); print(traceback.format_exc(), file=sys.stderr)
    return redirect(url_for('user', code=code)) # Остается redirect, но JS на клиенте обновит страницу по событию

@app.route("/user/<code>/place/<int:image_id>", methods=["POST"])
def place_card(code, image_id):
    # (Ваш код с добавлением broadcast_game_state_update)
    if not g.user or g.user['status'] != 'active': flash("Только активные игроки могут выкладывать карты.", "warning"); return redirect(url_for('user', code=code))
    db = get_db()
    try:
        if g.game_over: flash("Игра окончена.", "warning"); return redirect(url_for('user', code=code))
        active_subfolder = get_setting('active_subfolder')
        if not active_subfolder: flash("Активная колода не определена.", "danger"); return redirect(url_for('user', code=code))
        if db.execute("SELECT 1 FROM images WHERE owner_id = ? AND subfolder = ? AND status LIKE 'На столе:%'", (g.user['id'], active_subfolder)).fetchone():
            flash("У вас уже есть карта на столе.", "warning"); return redirect(url_for('user', code=code))
        card_info = db.execute("SELECT status, owner_id, subfolder, image FROM images WHERE id = ?", (image_id,)).fetchone()
        if not card_info: flash(f"Карта ID {image_id} не найдена.", "danger"); return redirect(url_for('user', code=code))
        if card_info['owner_id'] != g.user['id']: flash(f"Вы не владелец карты {image_id}.", "danger"); return redirect(url_for('user', code=code))
        expected_status = f"Занято:{g.user['id']}"
        if card_info['status'] != expected_status: flash(f"Карту {image_id} нельзя выложить. Статус: '{card_info['status']}'.", "danger"); return redirect(url_for('user', code=code))
        if card_info['subfolder'] != active_subfolder: flash(f"Карта '{card_info['image']}' не из активной колоды '{active_subfolder}'.", "danger"); return redirect(url_for('user', code=code))
        new_status_on_table = f"На столе:{g.user['id']}"
        db.execute("UPDATE images SET status = ?, guesses = '{}' WHERE id = ?", (new_status_on_table, image_id)); db.commit()
        flash(f"Ваша карта '{card_info['image']}' выложена.", "success")
        broadcast_game_state_update(user_code_trigger=code) # <--- Обновляем всех
    except sqlite3.Error as e: db.rollback(); flash(f"Ошибка БД: {e}", "danger"); print(traceback.format_exc(), file=sys.stderr)
    except Exception as e_gen: flash(f"Ошибка: {e_gen}", "danger"); print(traceback.format_exc(), file=sys.stderr)
    return redirect(url_for('user', code=code)) # Остается redirect

@app.route("/admin/open_cards", methods=["POST"])
def open_cards():
    # (Ваш код с добавлением broadcast_game_state_update)
    if not session.get('is_admin'): flash('Доступ запрещен.', 'danger'); return redirect(url_for('login'))
    if g.game_over and not g.game_in_progress: flash("Игра завершена.", "warning"); return redirect(url_for('admin'))
    if not g.game_in_progress and not g.game_over: flash("Игра не активна.", "warning"); return redirect(url_for('admin'))
    db = get_db(); c = db.cursor()
    try:
        leading_user_id = get_leading_user_id()
        if leading_user_id is None: flash("Ведущий не определен.", "warning"); return redirect(url_for('admin'))
        leader_status_row = c.execute("SELECT status FROM users WHERE id = ?", (leading_user_id,)).fetchone()
        if not leader_status_row or leader_status_row['status'] != 'active':
            flash(f"Ведущий (ID: {leading_user_id}) неактивен.", "danger"); set_setting("show_card_info", "true"); db.commit(); broadcast_game_state_update(); return redirect(url_for('admin'))
        set_setting("show_card_info", "true")
        active_player_ids_set = {row['id'] for row in c.execute("SELECT id FROM users WHERE status = 'active'").fetchall()}
        if not active_player_ids_set: flash("Нет активных игроков. Карты открыты.", "info"); db.commit(); broadcast_game_state_update(); return redirect(url_for('admin'))
        if leading_user_id not in active_player_ids_set: flash(f"Ведущий ID {leading_user_id} не активен. Ошибка подсчета.", "danger"); db.commit(); broadcast_game_state_update(); return redirect(url_for('admin'))
        other_active_player_ids = active_player_ids_set - {leading_user_id}; num_other_active_players = len(other_active_player_ids)
        cards_on_table = c.execute("SELECT i.id, i.owner_id, i.guesses FROM images i JOIN users u ON i.owner_id = u.id WHERE i.status LIKE 'На столе:%' AND u.status = 'active'").fetchall()
        leader_card = next((card for card in cards_on_table if card['owner_id'] == leading_user_id), None)
        leader_card_correct_guessers = set(); correct_guessers_per_player_card = {}
        if leader_card:
            for card_data_on_table in cards_on_table:
                card_owner_id = card_data_on_table['owner_id']
                if card_owner_id not in active_player_ids_set: continue
                guesses_dict = json.loads(card_data_on_table['guesses'] or '{}')
                for guesser_id_str, guessed_user_id in guesses_dict.items():
                    try:
                        guesser_id = int(guesser_id_str)
                        if guesser_id not in active_player_ids_set or guesser_id == card_owner_id: continue
                        if guessed_user_id == card_owner_id:
                            if card_owner_id == leading_user_id: leader_card_correct_guessers.add(guesser_id)
                            else: correct_guessers_per_player_card.setdefault(card_owner_id, set()).add(guesser_id)
                    except ValueError: continue
        scores = {player_id: 0 for player_id in active_player_ids_set}
        if leader_card:
            num_leader_correct_guessers = len(leader_card_correct_guessers)
            if num_other_active_players > 0 and num_leader_correct_guessers == num_other_active_players: scores[leading_user_id] -= 3
            elif num_leader_correct_guessers == 0:
                scores[leading_user_id] -= 2
                for owner_id, guesser_set in correct_guessers_per_player_card.items():
                    if owner_id != leading_user_id and owner_id in active_player_ids_set: scores[owner_id] += len(guesser_set)
            else:
                scores[leading_user_id] += 3 + num_leader_correct_guessers
                for guesser_id in leader_card_correct_guessers: scores[guesser_id] += 3
                for owner_id, guesser_set in correct_guessers_per_player_card.items():
                    if owner_id != leading_user_id and owner_id in active_player_ids_set: scores[owner_id] += len(guesser_set)
        if scores:
            points_changed = False
            for user_id_score, points in scores.items():
                if points != 0:
                    points_changed = True
                    if c.execute("SELECT 1 FROM users WHERE id = ? AND status = 'active'", (user_id_score,)).fetchone():
                        c.execute("UPDATE users SET rating = MAX(0, rating + ?) WHERE id = ?", (points, user_id_score))
            flash("Очки подсчитаны! Карты открыты." if points_changed else "Карты открыты. Очков нет.", "success" if points_changed else "info")
        else: flash("Карты открыты. Очки не начислялись.", "info")
        if g.game_over: set_game_in_progress(False)
        db.commit(); broadcast_game_state_update() # <--- Обновляем всех
    except sqlite3.Error as e_sql: db.rollback(); flash(f"Ошибка БД: {e_sql}", "danger"); print(traceback.format_exc(), file=sys.stderr)
    except Exception as e_general: db.rollback(); flash(f"Ошибка: {e_general}", "danger"); print(traceback.format_exc(), file=sys.stderr)
    return redirect(url_for('admin'))

@app.route("/new_round", methods=["POST"])
def new_round():
    # (Ваш код с добавлением broadcast_game_state_update)
    if not session.get('is_admin'): flash('Доступ запрещен.', 'danger'); return redirect(url_for('login'))
    if g.game_over: flash("Игра окончена.", "warning"); return redirect(url_for('admin'))
    if not is_game_in_progress(): flash("Игра не начата.", "warning"); return redirect(url_for('admin'))
    db = get_db(); c = db.cursor(); active_subfolder_new_round = get_setting('active_subfolder')
    leader_who_finished_round = get_leading_user_id(); new_actual_leader_id = None
    try:
        new_actual_leader_id = determine_new_leader(leader_who_finished_round)
        if new_actual_leader_id is not None:
            set_leading_user_id(new_actual_leader_id)
            flash(f"Новый раунд! Ведущий: {get_user_name(new_actual_leader_id) or f'ID {new_actual_leader_id}'}.", "success")
        else: flash("Новый раунд, но ведущий не определен.", "warning"); set_leading_user_id(None)
        c.execute("UPDATE images SET owner_id = NULL, guesses = '{}', status = 'Занято:Админ' WHERE status LIKE 'На столе:%'")
        c.execute("UPDATE images SET guesses = '{}' WHERE status NOT LIKE 'На столе:%' AND guesses != '{}'")
        set_setting("show_card_info", "false"); flash("Информация о картах скрыта.", "info")
        active_user_ids_new_round = [row['id'] for row in c.execute("SELECT id FROM users WHERE status = 'active' ORDER BY id").fetchall()]
        if not active_user_ids_new_round: flash("Нет активных для раздачи.", "warning")
        elif not active_subfolder_new_round: flash("Активная колода не установлена. Карты не розданы.", "warning")
        else:
            available_cards_ids = [row['id'] for row in c.execute("SELECT id FROM images WHERE subfolder = ? AND status = 'Свободно'", (active_subfolder_new_round,)).fetchall()]
            random.shuffle(available_cards_ids); num_available_new_round = len(available_cards_ids); cards_actually_dealt_total = 0
            if num_available_new_round == 0: flash(f"Нет доступных карт в '{active_subfolder_new_round}'.", "warning")
            else:
                for user_id_nr_deal in active_user_ids_new_round:
                    if cards_actually_dealt_total < num_available_new_round:
                        c.execute("UPDATE images SET status = ?, owner_id = ? WHERE id = ?", (f"Занято:{user_id_nr_deal}", user_id_nr_deal, available_cards_ids[cards_actually_dealt_total]))
                        cards_actually_dealt_total += 1
                    else: flash(f"Карты в '{active_subfolder_new_round}' закончились. Роздано {cards_actually_dealt_total}.", "warning"); break
            if cards_actually_dealt_total > 0: flash(f"Роздано {cards_actually_dealt_total} новых карт.", "info")
        game_over_now = False
        if active_user_ids_new_round:
            for user_id_check_game_over in active_user_ids_new_round:
                if c.execute("SELECT COUNT(*) FROM images WHERE owner_id = ? AND status LIKE 'Занято:%'", (user_id_check_game_over,)).fetchone()[0] == 0:
                    game_over_now = True; flash(f"У игрока {get_user_name(user_id_check_game_over) or f'ID {user_id_check_game_over}'} кончились карты!", "info"); break
        if game_over_now: set_game_over(True); set_game_in_progress(False); flash("Игра окончена!", "danger")
        db.commit(); broadcast_game_state_update() # <--- Обновляем всех
    except sqlite3.Error as e: db.rollback(); flash(f"Ошибка БД: {e}", "danger"); print(traceback.format_exc(), file=sys.stderr)
    except Exception as e_gen: db.rollback(); flash(f"Ошибка: {e_gen}", "danger"); print(traceback.format_exc(), file=sys.stderr)
    return redirect(url_for('admin', displayed_leader_id=new_actual_leader_id if new_actual_leader_id is not None else leader_who_finished_round))

# --- SocketIO события ---
@socketio.on('connect')
def handle_connect():
    user_code_on_connect = session.get('user_code') # Пытаемся получить код из сессии Flask
    print(f"SocketIO: Client connected: SID={request.sid}, User code from session: {user_code_on_connect or 'N/A'}", file=sys.stderr)
    # Отправляем текущее состояние игры новому подключенному клиенту
    # Передаем user_code, чтобы get_full_game_state_data мог подготовить персонализированное состояние
    initial_state = get_full_game_state_data(user_code_for_state=user_code_on_connect)
    emit('game_update', initial_state, room=request.sid) # Отправляем только этому клиенту

@socketio.on('disconnect')
def handle_disconnect():
    print(f"SocketIO: Client disconnected: SID={request.sid}", file=sys.stderr)

# --- Запуск приложения ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug_mode = os.environ.get("FLASK_DEBUG", "False").lower() in ['true', '1', 't']
    print(f"Запуск Flask-SocketIO приложения на http://0.0.0.0:{port}/ с debug={debug_mode}", file=sys.stderr)
    socketio.run(app, host="0.0.0.0", port=port, debug=debug_mode, allow_unsafe_werkzeug=True if debug_mode else False)
    
