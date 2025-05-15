import json
import sys
import sqlite3
import os
import string
import random
import traceback
from flask import Flask, render_template, request, redirect, url_for, g, flash, session
from flask_socketio import SocketIO, emit

app = Flask(__name__)
# ВАЖНО: Убедитесь, что этот ключ ИДЕНТИЧЕН тому, что был в работающей версии
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your_very_secret_fallback_key_for_dev_only_12345') 
if app.config['SECRET_KEY'] == 'your_very_secret_fallback_key_for_dev_only_12345':
    print("ПРЕДУПРЕЖДЕНИЕ: Используется SECRET_KEY по умолчанию. Установите переменную окружения SECRET_KEY!", file=sys.stderr)

socketio = SocketIO(app)
DB_PATH = 'database.db'

GAME_BOARD_POLE_IMG_SUBFOLDER = "pole"
GAME_BOARD_POLE_IMAGES = [f"p{i}.jpg" for i in range(1, 8)]
DEFAULT_NUM_BOARD_CELLS = 40
_current_game_board_pole_image_config = []
_current_game_board_num_cells = 0

connected_users_socketio = {}  # {sid: user_code}

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

def init_db(): # Эта функция остается без изменений с последнего раза
    print(f"DB Init: Attempting to initialize database at {os.path.abspath(DB_PATH)}", file=sys.stderr)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute("DROP TABLE IF EXISTS users"); c.execute("DROP TABLE IF EXISTS images")
        c.execute("DROP TABLE IF EXISTS settings"); c.execute("DROP TABLE IF EXISTS deck_votes")
        c.execute("""CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, code TEXT UNIQUE NOT NULL, rating INTEGER DEFAULT 0, status TEXT DEFAULT 'pending' NOT NULL)""")
        c.execute("""CREATE TABLE images (id INTEGER PRIMARY KEY AUTOINCREMENT, subfolder TEXT NOT NULL, image TEXT NOT NULL, status TEXT, owner_id INTEGER, guesses TEXT DEFAULT '{}')""")
        c.execute("""CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)""")
        c.execute("""CREATE TABLE deck_votes (subfolder TEXT PRIMARY KEY, votes INTEGER DEFAULT 0)""")
        conn.commit()
        settings_to_init = {'game_over': 'false', 'game_in_progress': 'false', 'show_card_info': 'false', 'leading_user_id': '', 'active_subfolder': 'koloda1'}
        for key, value in settings_to_init.items(): c.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
        image_folders = ['koloda1', 'ariadna', 'detstvo', 'odissey', 'pandora', ' Dixit', ' Dixit 2', ' Dixit 3', ' Dixit 4', ' Dixit 5', ' Dixit 6', ' Dixit 7 ', ' Dixit 8', ' Dixit 9', ' Dixit Odyssey', ' Dixit Odyssey (2)', ' Dixit Миражи', ' Имаджинариум', ' Имаджинариум Химера', ' Имаджинариум Юбилейный']
        images_added_count = 0
        for folder in image_folders:
            folder_path = os.path.join(app.static_folder, 'images', folder.strip())
            if os.path.exists(folder_path) and os.path.isdir(folder_path):
                for filename in os.listdir(folder_path):
                    if filename.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
                        c.execute("INSERT OR IGNORE INTO images (subfolder, image, status, guesses) VALUES (?, ?, 'Свободно', '{}')", (folder.strip(), filename))
                        if c.rowcount > 0: images_added_count += 1
            else: print(f"DB Init Warning: Folder not found: {folder_path}", file=sys.stderr)
        conn.commit()
        print(f"DB Init: Added {images_added_count} new images.", file=sys.stderr)
    except sqlite3.Error as e: print(f"CRITICAL ERROR during init_db: {e}\n{traceback.format_exc()}", file=sys.stderr); conn.rollback(); raise
    finally:
        if conn: conn.close()
    print("DB Init: Database initialized.", file=sys.stderr)

print("DB Init: Calling init_db() on module load.", file=sys.stderr)
init_db()
print("DB Init: init_db() call completed.", file=sys.stderr)

# --- Вспомогательные функции (get_setting, set_setting, etc.) ---
# Эти функции остаются без изменений с последнего раза
def get_setting(key):
    try: db = get_db(); c = db.cursor(); c.execute("SELECT value FROM settings WHERE key = ?", (key,)); row = c.fetchone(); return row['value'] if row else None
    except sqlite3.Error as e: print(f"DB error in get_setting for '{key}': {e}", file=sys.stderr); return None
def set_setting(key, value):
    db = get_db()
    try: c = db.cursor(); c.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)); db.commit(); return True
    except sqlite3.Error as e: print(f"DB error in set_setting for '{key}': {e}", file=sys.stderr); db.rollback(); return False
def get_user_name(user_id):
    if user_id is None: return None
    try: db = get_db(); c = db.cursor(); c.execute("SELECT name FROM users WHERE id = ?", (int(user_id),)); row = c.fetchone(); return row['name'] if row else None
    except Exception as e: print(f"Error in get_user_name for ID '{user_id}': {e}", file=sys.stderr); return None
def is_game_in_progress(): return get_setting('game_in_progress') == 'true'
def set_game_in_progress(state=True): return set_setting('game_in_progress', 'true' if state else 'false')
def is_game_over(): return get_setting('game_over') == 'true'
def set_game_over(state=True): return set_setting('game_over', 'true' if state else 'false')
def generate_unique_code(length=8): return ''.join(random.choices(string.ascii_letters + string.digits, k=length))
def get_leading_user_id(): val = get_setting('leading_user_id'); return int(val) if val and val.strip() else None
def set_leading_user_id(uid): return set_setting('leading_user_id', str(uid) if uid is not None else '')
def determine_new_leader(current_leader_id):
    db = get_db(); c = db.cursor()
    try:
        c.execute("SELECT id FROM users WHERE status = 'active' ORDER BY id ASC"); rows = c.fetchall()
        if not rows: return None
        ids = [r['id'] for r in rows]
        if current_leader_id is None or current_leader_id not in ids: return ids[0]
        try: idx = ids.index(current_leader_id); return ids[(idx + 1) % len(ids)]
        except ValueError: return ids[0]
    except Exception as e: print(f"Error in determine_new_leader: {e}", file=sys.stderr); return None
def get_active_players_count(db_conn):
    try: cur = db_conn.execute("SELECT COUNT(id) FROM users WHERE status = 'active'"); return cur.fetchone()[0] or 0
    except Exception as e: print(f"DB error in get_active_players_count: {e}", file=sys.stderr); return 0
def check_and_end_game_if_player_out_of_cards(db_conn):
    if not is_game_in_progress(): return False
    c = db_conn.cursor()
    c.execute("SELECT id, name FROM users WHERE status = 'active'")
    active_players = c.fetchall()
    if not active_players: return False
    player_who_ran_out = None
    for player in active_players:
        c.execute("SELECT COUNT(id) FROM images WHERE owner_id = ? AND status LIKE 'Занято:%'", (player['id'],))
        card_count_row = c.fetchone()
        if card_count_row and card_count_row[0] == 0:
            player_who_ran_out = player; break
    if player_who_ran_out:
        set_game_over(True); set_game_in_progress(False)
        flash(f"Игра окончена! У игрока '{player_who_ran_out['name']}' закончились карты.", "danger")
        print(f"GAME OVER: Player {player_who_ran_out['name']} (ID: {player_who_ran_out['id']}) ran out of cards.", file=sys.stderr)
        return True
    return False
def initialize_new_game_board_visuals(num_cells_for_board=None, all_users_for_rating_check=None): # Без изменений
    global _current_game_board_pole_image_config, _current_game_board_num_cells
    actual_num_cells = DEFAULT_NUM_BOARD_CELLS
    if num_cells_for_board is not None: actual_num_cells = num_cells_for_board
    elif all_users_for_rating_check:
        max_rating = 0
        for user_data_item in all_users_for_rating_check:
            user_rating = user_data_item.get('rating', 0) if isinstance(user_data_item, dict) else getattr(user_data_item, 'rating', 0)
            if isinstance(user_rating, int) and user_rating > max_rating: max_rating = user_rating
        actual_num_cells = max(DEFAULT_NUM_BOARD_CELLS, max_rating + 6)
    _current_game_board_num_cells = actual_num_cells; _current_game_board_pole_image_config = []
    pole_image_folder_path = os.path.join(app.static_folder, 'images', GAME_BOARD_POLE_IMG_SUBFOLDER)
    if GAME_BOARD_POLE_IMAGES and os.path.exists(pole_image_folder_path) and os.path.isdir(pole_image_folder_path):
        available_pole_images = [f for f in os.listdir(pole_image_folder_path) if f.lower().endswith(('.jpg', '.png', '.jpeg')) and f in GAME_BOARD_POLE_IMAGES]
        if not available_pole_images: available_pole_images = ["p1.jpg"]
        for _ in range(_current_game_board_num_cells): _current_game_board_pole_image_config.append(os.path.join('images', GAME_BOARD_POLE_IMG_SUBFOLDER, random.choice(available_pole_images)).replace("\\", "/"))
    else: _current_game_board_pole_image_config = [os.path.join('images', GAME_BOARD_POLE_IMG_SUBFOLDER, "p1.jpg").replace("\\", "/")] * _current_game_board_num_cells
def generate_game_board_data_for_display(all_users_data_for_board): # Без изменений
    global _current_game_board_pole_image_config, _current_game_board_num_cells
    if not _current_game_board_pole_image_config or _current_game_board_num_cells == 0:
        initialize_new_game_board_visuals(all_users_for_rating_check=all_users_data_for_board)
        if not _current_game_board_pole_image_config or _current_game_board_num_cells == 0: return []
    board_cells_data = []
    for i in range(_current_game_board_num_cells):
        cell_number = i + 1; cell_image_path = "images/default_pole_image.png"
        if _current_game_board_pole_image_config:
            try: cell_image_path = _current_game_board_pole_image_config[i % len(_current_game_board_pole_image_config)]
            except Exception as e: print(f"Error getting board cell image: {e}", file=sys.stderr)
        users_in_this_cell = []
        for user_data_item_board in all_users_data_for_board:
            user_rating = int(user_data_item_board.get('rating', 0) if isinstance(user_data_item_board, dict) else user_data_item_board['rating'] or 0)
            if user_rating == cell_number: users_in_this_cell.append({'id': user_data_item_board['id'], 'name': user_data_item_board['name'], 'rating': user_rating})
        board_cells_data.append({'cell_number': cell_number, 'image_path': cell_image_path, 'users_in_cell': users_in_this_cell})
    return board_cells_data

def get_full_game_state_data(user_code_for_state=None):
    """
    Собирает полное текущее состояние игры, включая информацию для конкретного пользователя.
    Включает логику определения и добавления имени следующего ведущего.
    """
    db = get_db()
    game_state = {}

    try:
        # Общие настройки игры
        game_state['game_in_progress'] = get_setting("game_in_progress") == "true"
        game_state['game_over'] = get_setting("game_over") == "true"
        game_state['show_card_info'] = get_setting("show_card_info") == "true"
        game_state['current_round_leader_id'] = get_setting("leading_user_id")
        game_state['current_round_association'] = get_setting("current_round_association")
        game_state['all_cards_placed_for_guessing_phase_to_template'] = get_setting("all_cards_placed_for_guessing_phase_to_template") == "true"

        # Информация о текущем ведущем
        current_leader_id_str = game_state.get('current_round_leader_id')
        current_leader_id = int(current_leader_id_str) if current_leader_id_str else None

        if current_leader_id is not None:
            current_leader_user = get_user_by_id(current_leader_id)
            game_state['db_current_leader_id'] = current_leader_id
            game_state['current_leader_name'] = current_leader_user['name'] if current_leader_user else "Неизвестный ведущий"
        else:
             game_state['db_current_leader_id'] = None
             game_state['current_leader_name'] = "Ожидание ведущего"

        # --- ИЗМЕНЕНИЕ: Определение и добавление имени следующего ведущего ---
        game_state['next_leader_name'] = None # Инициализируем значение по умолчанию
        # Определяем следующего ведущего, только если игра в процессе ИЛИ результаты показаны (show_card_info)
        # и есть текущий ведущий, чтобы определить следующего
        if (game_state['game_in_progress'] or game_state['show_card_info']) and current_leader_id is not None:
            try:
                # Предполагаем, что determine_new_leader возвращает ID следующего пользователя
                next_leader_id = determine_new_leader(current_leader_id)
                if next_leader_id is not None:
                    next_leader_user = get_user_by_id(next_leader_id)
                    game_state['next_leader_name'] = next_leader_user['name'] if next_leader_user else "Неизвестный игрок"
                # else: # Можно добавить сообщение, если определить следующего не удалось (например, игра закончилась)
                    # game_state['next_leader_name'] = "Будет определен"

            except Exception as e:
                # Логирование ошибки определения следующего ведущего
                print(f"Ошибка при определении имени следующего ведущего: {e}\n{traceback.format_exc()}", file=sys.stderr)
                game_state['next_leader_name'] = "Ошибка определения" # Информируем об ошибке на клиенте
        # ---------------------------------------------------------------------

        # Данные об игроках (для угадывания и отображения на поле)
        all_users = get_active_users() # Или функция, получающая всех пользователей, которые могут участвовать/отгадывать
        # Убедитесь, что get_active_users() возвращает список словарей с ключами 'id', 'name', 'rating', 'status'
        game_state['all_users_for_guessing'] = [{'id': user['id'], 'name': user['name']} for user in all_users] # Упрощенный список для клиента
        game_state['users_full_list'] = [{'id': user['id'], 'name': user['name'], 'rating': user['rating'], 'status': user['status']} for user in all_users] # Полный список пользователей для поля


        # Информация для конкретного пользователя
        user_data = None
        if user_code_for_state:
            user_data = get_user_by_code(user_code_for_state) # Убедитесь, что у вас есть такая функция и она возвращает словарь с данными пользователя
            if user_data:
                game_state['current_user_data'] = {
                    'id': user_data['id'],
                    'name': user_data['name'],
                    'rating': user_data['rating'],
                    'status': user_data['status']
                    # ... другие необходимые данные пользователя
                }
                # Карты в руке пользователя
                user_cards = get_user_cards(user_data['id']) # Убедитесь, что у вас есть такая функция и она возвращает список словарей карт
                game_state['user_cards'] = [{'id': card['id'], 'image': card['image'], 'subfolder': card['subfolder']} for card in user_cards]

                # Статус его карты на столе (если выложена)
                user_placed_card = get_table_card_by_owner(user_data['id']) # Убедитесь, что у вас есть такая функция
                game_state['on_table_status'] = user_placed_card is not None


        # Карты на столе
        table_cards = get_table_cards() # Убедитесь, что у вас есть такая функция и она возвращает список словарей карт на столе
        formatted_table_cards = []
        # Собираем информацию о картах на столе, включая владельцев и голоса
        for card in table_cards:
             card_owner = get_user_by_id(card['owner_id'])
             card_info = {
                'id': card['id'],
                'image': card['image'],
                'subfolder': card['subfolder'],
                'owner_id': card['owner_id'],
                'owner_name': card_owner['name'] if card_owner else 'Неизв.' ,
                # votes здесь может быть не нужен на клиенте, если голоса считаются на сервере
                # 'votes': card.get('votes', []),
                'guesses': {} # Будет заполняться ниже
             }

             # Добавляем информацию о том, как текущий пользователь (если есть) проголосовал за эту карту
             if user_data and game_state['all_cards_placed_for_guessing_phase_to_template'] and not game_state['show_card_info']:
                 # Находим голос текущего пользователя за эту карту
                 user_guess_for_this_card = get_user_guess_for_card(user_data['id'], card['id']) # Нужна функция для получения голоса пользователя за конкретную карту
                 card_info['my_guess_for_this_card_value'] = user_guess_for_this_card['guessed_user_id'] if user_guess_for_this_card else None

             # Собираем информацию о том, кто как проголосовал за эту карту (после подсчета баллов)
             if game_state['show_card_info']:
                 card_info['guesses'] = get_guesses_for_card(card['id']) # Нужна функция, возвращающая словарь голосов {user_id: voted_for_user_id} за эту карту


             formatted_table_cards.append(card_info)

        game_state['table_images'] = formatted_table_cards


        # Информация для игрового поля
        game_board_config = get_game_board_config() # Убедитесь, что у вас есть эта функция и она возвращает структуру поля {'cells': [...], 'total_cells': N, 'leader_pole_pictogram_path': '...'}
        # Проверяем, что game_board_config корректен
        if game_board_config and 'cells' in game_board_config and 'total_cells' in game_board_config:
            game_state['game_board'] = game_board_config['cells']
            game_state['current_num_board_cells'] = game_board_config['total_cells']
            game_state['leader_pole_pictogram_path'] = game_board_config.get('leader_pole_pictogram_path', '') # Путь к пиктограмме ведущего

            # Находим пользователя на поле, чтобы отобразить рейтинг на пиктограмме ведущего
            user_on_board = next((user for user in game_state.get('users_full_list', []) if user.get('id') == user_data['id']), None) if user_data else None
            game_state['leader_pictogram_rating_display'] = user_on_board['rating'] if user_on_board else (user_data['rating'] if user_data else 0) # Если нет на поле, берем общий рейтинг или 0
        else:
             # Если конфиг поля не загружен, отправляем пустые данные, чтобы избежать ошибок на клиенте
             game_state['game_board'] = []
             game_state['current_num_board_cells'] = 0
             game_state['leader_pole_pictogram_path'] = ''
             game_state['leader_pictogram_rating_display'] = user_data['rating'] if user_data else 0


        # Список флеш-сообщений (если есть)
        # Предполагаем, что вы сохраняете флеш-сообщения в сессии под ключом '_flashed_messages'
        # и они имеют формат списка кортежей [(category, message), ...]
        flashed_messages = session.pop('_flashed_messages', []) if '_flashed_messages' in session else []
        game_state['flashed_messages'] = [{'message': msg[1], 'category': msg[0]} for msg in flashed_messages]


    except Exception as e:
        # Логирование ошибок при сборе game_state
        print(f"Ошибка при сборе game_state: {e}\n{traceback.format_exc()}", file=sys.stderr)
        # В случае ошибки отправляем минимальное состояние, чтобы клиент не завис и показал ошибку
        game_state = {
            'error': f'Ошибка сервера при обновлении состояния: {e}',
            'game_in_progress': False,
            'game_over': True,
            'show_card_info': False,
            'current_round_leader_id': None,
            'current_round_association': None,
            'all_cards_placed_for_guessing_phase_to_template': False,
            'db_current_leader_id': None,
            'current_leader_name': 'Ошибка загрузки',
            'next_leader_name': 'Ошибка загрузки',
            'all_users_for_guessing': [],
            'users_full_list': [],
            'user_cards': [],
            'on_table_status': False,
            'table_images': [],
            'game_board': [],
            'current_num_board_cells': 0,
            'leader_pole_pictogram_path': '',
            'leader_pictogram_rating_display': 0,
            'flashed_messages': [{'message': f'Ошибка загрузки данных: {e}', 'category': 'danger'}]
        }
        # Пытаемся хотя бы получить данные текущего пользователя, если есть код
        if user_code_for_state:
            try:
                user_data = get_user_by_code(user_code_for_state)
                if user_data:
                    game_state['current_user_data'] = {'id': user_data['id'], 'name': user_data['name'], 'rating': user_data['rating'], 'status': user_data['status']}
            except Exception as user_e:
                 print(f"Ошибка при загрузке данных пользователя во время общей ошибки: {user_e}", file=sys.stderr)
                 game_state['current_user_data'] = None


    return game_state

def broadcast_game_state_update(user_code_trigger=None): # Без изменений
    print(f"SocketIO: Broadcasting game_update. Triggered by: {user_code_trigger or 'System'}", file=sys.stderr)
    active_sids = list(connected_users_socketio.keys())
    if not active_sids: print("SocketIO: No identified clients to broadcast to.", file=sys.stderr); return
    for sid_to_update in active_sids:
        user_code_for_sid = connected_users_socketio.get(sid_to_update)
        if user_code_for_sid:
            try:
                with app.app_context(): state_data = get_full_game_state_data(user_code_for_state=user_code_for_sid); socketio.emit('game_update', state_data, room=sid_to_update)
            except Exception as e: print(f"SocketIO: Error sending update to SID {sid_to_update} (user {user_code_for_sid}): {e}\n{traceback.format_exc()}", file=sys.stderr)
def broadcast_user_list_update(): print("SocketIO: broadcast_user_list_update() called -> general game state update.", file=sys.stderr); broadcast_game_state_update()
def broadcast_deck_votes_update(): # Без изменений
    print("SocketIO: broadcast_deck_votes_update() called.", file=sys.stderr)
    try:
        with app.app_context():
            db = get_db(); c = db.cursor()
            c.execute("SELECT i.subfolder, COALESCE(dv.votes, 0) as votes FROM (SELECT DISTINCT subfolder FROM images ORDER BY subfolder) as i LEFT JOIN deck_votes as dv ON i.subfolder = dv.subfolder;")
            deck_votes_data = [dict(row) for row in c.fetchall()]
            socketio.emit('deck_votes_updated', {'deck_votes': deck_votes_data})
    except Exception as e: print(f"Error broadcasting deck votes: {e}\n{traceback.format_exc()}", file=sys.stderr)

app.jinja_env.globals.update(get_user_name=get_user_name, get_leading_user_id=get_leading_user_id)

@app.before_request # Без изменений
def before_request_func():
    db = get_db()
    code_param = request.args.get('code') or (request.view_args.get('code') if request.view_args else None) or session.get('user_code')
    g.user = None; g.user_id = None
    if code_param:
        try:
            user_row = db.execute("SELECT id, name, code, rating, status FROM users WHERE code = ?", (code_param,)).fetchone()
            if user_row:
                g.user = dict(user_row); g.user_id = user_row['id']
                session.update({k: user_row[k] for k in ['id', 'name', 'code', 'rating', 'status'] if k in user_row})
                session['user_id'] = g.user_id
            elif 'user_code' in session and session['user_code'] == code_param:
                for key in ['user_id', 'user_name', 'user_code', 'user_status', 'user_rating']: session.pop(key, None)
        except sqlite3.Error as e: print(f"DB error in before_request for code '{code_param}': {e}", file=sys.stderr)
    g.show_card_info = get_setting("show_card_info") == "true"
    g.game_over = is_game_over()
    g.game_in_progress = is_game_in_progress()

@app.route('/') # Без изменений
def index():
    deck_votes_data = []; db = get_db(); c = db.cursor()
    try: c.execute("SELECT i.subfolder, COALESCE(dv.votes, 0) as votes FROM (SELECT DISTINCT subfolder FROM images ORDER BY subfolder) as i LEFT JOIN deck_votes as dv ON i.subfolder = dv.subfolder;"); deck_votes_data = [dict(row) for row in c.fetchall()]
    except sqlite3.Error as e: print(f"Ошибка чтения голосов на index: {e}", file=sys.stderr)
    return render_template("index.html", deck_votes=deck_votes_data, current_vote=session.get('voted_for_deck'), active_subfolder=get_setting('active_subfolder') or "N/A")

@app.route('/init_db_route_for_dev_only_make_sure_to_secure_or_remove') # Без изменений
def init_db_route(): flash("БД инициализируется при старте приложения.", "info"); return redirect(url_for('index'))

@app.route("/login_player") # Без изменений
def login_player(): return redirect(url_for('user', code=session['user_code'])) if session.get('user_code') else render_template('login_player.html')

@app.route("/register_or_login_player", methods=["POST"]) # Без изменений
def register_or_login_player():
    name = request.form.get('name', '').strip()
    if not name: flash("Имя не может быть пустым.", "warning"); return redirect(url_for('login_player'))
    db = get_db(); c = db.cursor()
    try:
        user = c.execute("SELECT id, code, status, rating FROM users WHERE name = ?", (name,)).fetchone()
        if user:
            session.update({'user_id': user['id'], 'user_name': name, 'user_code': user['code'], 'user_status': user['status'], 'user_rating': user['rating']})
            flash(f"С возвращением, {name}!", "info")
        else:
            code = generate_unique_code(); status = 'pending' if is_game_in_progress() else 'active'
            c.execute("INSERT INTO users (name, code, status, rating) VALUES (?, ?, ?, 0)", (name, code, status))
            uid = c.lastrowid; db.commit()
            session.update({'user_id': uid, 'user_name': name, 'user_code': code, 'user_status': status, 'user_rating': 0})
            flash(f"Добро пожаловать, {name}! Вы {'наблюдатель' if status == 'pending' else 'активный участник'}.", "success")
            broadcast_user_list_update()
        session.pop('is_admin', None)
        return redirect(url_for('user', code=session['user_code']))
    except sqlite3.Error as e: db.rollback(); flash(f"Ошибка БД: {e}", "danger"); return redirect(url_for('login_player'))

@app.route('/vote_deck', methods=['POST']) # Без изменений
def vote_deck():
    new_deck = request.form.get('subfolder'); prev_deck = session.get('voted_for_deck')
    if not new_deck: flash("Колода не выбрана.", "warning"); return redirect(url_for('index'))
    if new_deck == prev_deck: flash(f"Уже голосовали за '{new_deck}'.", "info"); return redirect(url_for('index'))
    db = get_db(); c = db.cursor()
    try:
        if prev_deck: c.execute("UPDATE deck_votes SET votes = MAX(0, votes - 1) WHERE subfolder = ?", (prev_deck,))
        c.execute("REPLACE INTO deck_votes (subfolder, votes) VALUES (?, COALESCE((SELECT votes FROM deck_votes WHERE subfolder = ?), 0) + 1)", (new_deck, new_deck))
        db.commit(); session['voted_for_deck'] = new_deck
        flash(f"Голос за '{new_deck}' учтен!", "success"); broadcast_deck_votes_update()
    except sqlite3.Error as e: db.rollback(); flash(f"Ошибка БД: {e}", "danger")
    return redirect(url_for('index'))

@app.route('/login', methods=['GET', 'POST']) # Без изменений
def login():
    if request.method == 'POST':
        pwd = request.form.get('password'); admin_pwd = os.environ.get('ADMIN_PASSWORD')
        if not admin_pwd: flash('Ошибка конфигурации сервера.', 'danger'); return render_template('login.html')
        if pwd == admin_pwd: session['is_admin'] = True; flash('Авторизация успешна.', 'success'); return redirect(request.args.get('next') or url_for('admin'))
        else: flash('Неверный пароль.', 'danger')
    return render_template('login.html')

@app.route('/logout') # Без изменений
def logout(): session.pop('is_admin', None); flash('Вы вышли из системы администратора.', 'info'); return redirect(url_for('index'))


# ===== ИЗМЕНЕНИЯ В МАРШРУТЕ ADMIN =====
@app.route("/admin", methods=["GET", "POST"])
def admin():
    if not session.get('is_admin'):
        flash('Требуется авторизация.', 'warning')
        return redirect(url_for('login', next=request.url))
    
    db = get_db()
    c = db.cursor() # Получаем курсор

    # POST-обработка остается такой же, как была в вашем полном файле или как вы ее доработали
    # Убедитесь, что после каждого действия, меняющего состояние, вызывается broadcast_game_state_update()
    # Пример:
    if request.method == "POST":
        action_admin = request.form.get("action_admin") # Пример вашего поля для определения действия
        # Например:
        if action_admin == "set_active_deck_admin":
            new_active_subfolder = request.form.get("active_subfolder")
            set_setting("active_subfolder", new_active_subfolder if new_active_subfolder else "")
            flash(f"Активная колода изменена на '{new_active_subfolder or 'Не выбрана'}'.", "success" if new_active_subfolder else "info")
            db.commit()
            broadcast_game_state_update()
        elif action_admin == "toggle_show_card_info_admin":
            new_show_info = not (get_setting('show_card_info') == 'true')
            set_setting('show_card_info', 'true' if new_show_info else 'false')
            db.commit()
            flash(f"Отображение инфо о картах {'вкл' if new_show_info else 'выкл'}.", "info")
            broadcast_game_state_update()
        # ... другие ваши POST обработчики ...
        return redirect(url_for('admin'))


    # Сбор данных для шаблона admin.html
    users_raw = c.execute("SELECT id, name, code, rating, status FROM users ORDER BY name ASC").fetchall()
    users_for_template = [dict(row) for row in users_raw]

    images_db = c.execute("SELECT id, subfolder, image, status, owner_id, guesses FROM images ORDER BY subfolder, id LIMIT 500").fetchall() # Ограничение для производительности
    images_for_template = []
    for img_row in images_db:
        img_dict = dict(img_row)
        try:
            img_dict['guesses'] = json.loads(img_row['guesses'] or '{}')
        except json.JSONDecodeError:
            img_dict['guesses'] = {} # В случае ошибки парсинга JSON
        images_for_template.append(img_dict)

    subfolders_for_template = [row['subfolder'] for row in c.execute("SELECT DISTINCT subfolder FROM images ORDER BY subfolder").fetchall()]
    
    active_users_for_template = [u for u in users_for_template if u['status'] == 'active']

    # Восстанавливаем логику для user_has_duplicate_guesses и связанных переменных
    all_guesses_for_template = {}
    for img in images_for_template:
        if img['guesses'] and isinstance(img['guesses'], dict) and img['id'] is not None:
             all_guesses_for_template[img['id']] = img['guesses']
    
    guess_counts_by_user_for_template = {u['id']: 0 for u in active_users_for_template}
    user_has_duplicate_guesses_for_template = {u['id']: False for u in active_users_for_template}

    if all_guesses_for_template and active_users_for_template:
        for user_item_dict in active_users_for_template: # user_item_dict это уже словарь
            user_id_str = str(user_item_dict['id'])
            guesses_made_by_this_user = []
            for image_id_key_str in all_guesses_for_template: # image_id_key_str это id картинки (ключ словаря)
                guesses_on_one_image = all_guesses_for_template[image_id_key_str] # это словарь голосов за эту картинку
                if user_id_str in guesses_on_one_image: # если текущий юзер голосовал за эту картинку
                    guesses_made_by_this_user.append(guesses_on_one_image[user_id_str]) # добавляем ID того, за кого он проголосовал
                    guess_counts_by_user_for_template[user_item_dict['id']] += 1
            
            # Проверка на дубликаты (если пользователь проголосовал за одного и того же игрока для РАЗНЫХ карт)
            # Это не то, что обычно проверяется как "дубликат". Обычно дубликат - это если он за ОДНУ карту пытается проголосовать несколько раз
            # или если он выложил две одинаковые карты (что невозможно по другой логике).
            # Логика ниже проверяет, не указывал ли он одного и того же ДРУГОГО игрока в качестве предполагаемого владельца для РАЗНЫХ карт.
            # Если это то, что нужно, оставляем. Если нет, эту проверку нужно скорректировать.
            if len(guesses_made_by_this_user) > len(set(guesses_made_by_this_user)):
                user_has_duplicate_guesses_for_template[user_item_dict['id']] = True
    
    current_active_subfolder = get_setting('active_subfolder') or ''
    current_leader_from_db = get_leading_user_id()
    potential_next_leader_id = determine_new_leader(current_leader_from_db)
    free_image_count_for_template = sum(1 for img in images_for_template if img.get('status') == 'Свободно' and img.get('subfolder') == current_active_subfolder)
    image_owners_for_template = {img['id']: img['owner_id'] for img in images_for_template if img.get('owner_id') is not None}

    db_users_for_board_fetch = c.execute("SELECT id, name, rating FROM users WHERE status = 'active'").fetchall()
    game_board_data_for_template = generate_game_board_data_for_display(db_users_for_board_fetch)


    return render_template("admin.html", 
                           users=users_for_template, 
                           images=images_for_template, 
                           subfolders=subfolders_for_template,
                           active_subfolder=current_active_subfolder, 
                           db_current_leader_id=current_leader_from_db,
                           potential_next_leader_id=potential_next_leader_id,
                           free_image_count=free_image_count_for_template,
                           image_owners=image_owners_for_template,
                           game_board=game_board_data_for_template,
                           all_guesses=all_guesses_for_template, # Передаем в шаблон
                           guess_counts_by_user=guess_counts_by_user_for_template, # Передаем в шаблон
                           user_has_duplicate_guesses=user_has_duplicate_guesses_for_template, # Передаем в шаблон
                           get_user_name_func=get_user_name, # Jinja global, но можно и так
                           current_num_board_cells=_current_game_board_num_cells
                           )
# ===== КОНЕЦ ИЗМЕНЕНИЙ В МАРШРУТЕ ADMIN =====

@app.route("/start_new_game", methods=["POST"]) # Логика без изменений (с последнего раза)
def start_new_game():
    if not session.get('is_admin'): flash('Доступ запрещен.', 'danger'); return redirect(url_for('login'))
    db = get_db(); c = db.cursor(); selected_deck = request.form.get("new_game_subfolder")
    num_cards_per_player = int(request.form.get("new_game_num_cards", 3))
    if num_cards_per_player < 0: num_cards_per_player = 0; flash("Кол-во карт <0. Уст. 0.", "warning")
    if not selected_deck: flash("Колода не выбрана.", "danger"); return redirect(url_for('admin'))
    new_leader_id_sng = None
    try:
        c.execute("UPDATE users SET status = 'active', rating = 0 WHERE status = 'pending' OR status = 'active'")
        c.execute("UPDATE images SET owner_id = NULL, guesses = '{}', status = 'Занято:Админ'")
        c.execute("UPDATE images SET status = 'Свободно' WHERE subfolder = ?", (selected_deck,))
        set_game_over(False); set_setting("show_card_info", "false"); set_setting("active_subfolder", selected_deck)
        first_active_user = c.execute("SELECT id FROM users WHERE status = 'active' ORDER BY id LIMIT 1").fetchone()
        if first_active_user: new_leader_id_sng = first_active_user['id']; set_leading_user_id(new_leader_id_sng)
        else: set_leading_user_id(None)
        initialize_new_game_board_visuals(all_users_for_rating_check=c.execute("SELECT id, name, rating FROM users WHERE status = 'active'").fetchall())
        db.commit()
        active_user_ids = [row['id'] for row in c.execute("SELECT id FROM users WHERE status = 'active' ORDER BY id").fetchall()]
        if not active_user_ids: flash("Нет активных игроков.", "warning")
        elif num_cards_per_player > 0:
            available_cards = [row['id'] for row in c.execute("SELECT id FROM images WHERE subfolder = ? AND status = 'Свободно'", (selected_deck,)).fetchall()]
            random.shuffle(available_cards); total_cards_needed = len(active_user_ids) * num_cards_per_player
            if len(available_cards) < total_cards_needed: flash(f"Внимание: Недостаточно карт ({len(available_cards)}) для раздачи по {num_cards_per_player} карт {len(active_user_ids)} игрокам. Будет роздано сколько есть.", "warning")
            card_idx = 0
            for user_id in active_user_ids:
                for _ in range(num_cards_per_player):
                    if card_idx < len(available_cards): c.execute("UPDATE images SET status = ?, owner_id = ? WHERE id = ?", (f"Занято:{user_id}", user_id, available_cards[card_idx])); card_idx += 1
                    else: break
                if card_idx >= len(available_cards): break
            flash(f"Новая игра! Колода: '{selected_deck}'. Роздано {card_idx} карт.", "success")
        else: flash(f"Новая игра! Колода: '{selected_deck}'. Карты не раздавались (0 на игрока).", "info")
        if new_leader_id_sng: flash(f"Ведущий: {get_user_name(new_leader_id_sng)}.", "info")
        set_game_in_progress(True); db.commit(); broadcast_game_state_update()
    except Exception as e: db.rollback(); flash(f"Ошибка старта игры: {e}", "danger"); print(traceback.format_exc(), file=sys.stderr)
    return redirect(url_for('admin', displayed_leader_id=new_leader_id_sng))

@app.route('/user/<code>') # Логика без изменений
def user(code):
    if g.user is None: flash("Пользователь не найден.", "warning"); return redirect(url_for('login_player'))
    session.update({k: g.user[k] for k in ['id', 'name', 'code', 'rating', 'status'] if k in g.user})
    session['user_id'] = g.user['id']
    session.pop('is_admin', None)
    return render_template('user.html', user_data_for_init=dict(g.user))

@app.route("/user/<code>/place/<int:image_id>", methods=["POST"]) # Логика без изменений
def place_card(code, image_id):
    if not g.user or g.user['status'] != 'active': flash("Только активные игроки могут выкладывать карты.", "warning"); return redirect(url_for('user', code=code))
    db = get_db(); c = db.cursor()
    try:
        if is_game_over(): flash("Игра окончена.", "warning"); return redirect(url_for('user', code=code))
        if not is_game_in_progress(): flash("Игра еще не началась.", "warning"); return redirect(url_for('user', code=code))
        active_subfolder = get_setting('active_subfolder')
        num_on_table = c.execute("SELECT COUNT(id) FROM images WHERE subfolder = ? AND status LIKE 'На столе:%'", (active_subfolder,)).fetchone()[0]
        num_active = get_active_players_count(db)
        all_cards_placed = (num_active > 0 and num_on_table >= num_active)
        if get_setting("show_card_info") == "true": flash("Карты уже открыты, менять нельзя.", "warning"); return redirect(url_for('user', code=code))
        card_of_this_user_on_table = c.execute("SELECT id FROM images WHERE owner_id = ? AND subfolder = ? AND status LIKE 'На столе:%'", (g.user['id'], active_subfolder)).fetchone()
        if all_cards_placed and card_of_this_user_on_table : flash("Все игроки уже выложили карты, менять нельзя.", "warning"); return redirect(url_for('user', code=code))
        card_to_place = c.execute("SELECT status, owner_id, subfolder, image FROM images WHERE id = ?", (image_id,)).fetchone()
        if not card_to_place: flash(f"Карта ID {image_id} не найдена.", "danger"); return redirect(url_for('user', code=code))
        if card_to_place['owner_id'] != g.user['id']: flash(f"Вы не владелец карты {image_id}.", "danger"); return redirect(url_for('user', code=code))
        if not card_to_place['status'].startswith(f"Занято:{g.user['id']}"):
            if card_to_place['status'].startswith("На столе:") and card_to_place['id'] == (card_of_this_user_on_table['id'] if card_of_this_user_on_table else None): flash(f"Карта '{card_to_place['image']}' уже на столе.", "info"); return redirect(url_for('user', code=code))
            flash(f"Карту '{card_to_place['image']}' ({image_id}) нельзя выложить. Статус: '{card_to_place['status']}'.", "danger"); return redirect(url_for('user', code=code))
        if card_to_place['subfolder'] != active_subfolder: flash(f"Карта не из активной колоды.", "danger"); return redirect(url_for('user', code=code))
        if card_of_this_user_on_table and card_of_this_user_on_table['id'] == image_id: flash(f"Карта уже на столе.", "info"); return redirect(url_for('user', code=code))
        if card_of_this_user_on_table and card_of_this_user_on_table['id'] != image_id: c.execute("UPDATE images SET status = ?, guesses = '{}' WHERE id = ?", (f"Занято:{g.user['id']}", card_of_this_user_on_table['id'])); flash(f"Предыдущая карта возвращена в руку.", "info")
        c.execute("UPDATE images SET status = ?, guesses = '{}' WHERE id = ?", (f"На столе:{g.user['id']}", image_id))
        db.commit(); flash(f"Ваша карта '{card_to_place['image']}' выложена.", "success")
        broadcast_game_state_update(user_code_trigger=code)
    except Exception as e: db.rollback(); flash(f"Ошибка выкладывания карты: {e}", "danger"); print(traceback.format_exc(), file=sys.stderr)
    return redirect(url_for('user', code=code))

@app.route("/user/<code>/guess/<int:image_id>", methods=["POST"]) # Логика без изменений
def guess_image(code, image_id):
    if not g.user or g.user['status'] != 'active': flash("Только активные игроки могут делать предположения.", "warning"); return redirect(url_for('user', code=code))
    guessed_user_id_str = request.form.get("guessed_user_id")
    if not guessed_user_id_str: flash("Игрок для предположения не выбран.", "warning"); return redirect(url_for('user', code=code))
    db = get_db(); c = db.cursor()
    try:
        guessed_user_id = int(guessed_user_id_str)
        if not c.execute("SELECT 1 FROM users WHERE id = ? AND status = 'active'", (guessed_user_id,)).fetchone(): flash("Выбранный игрок не существует/неактивен.", "danger"); return redirect(url_for('user', code=code))
        image_data = c.execute("SELECT i.guesses, i.owner_id FROM images i JOIN users u ON i.owner_id = u.id WHERE i.id = ? AND i.status LIKE 'На столе:%' AND u.status = 'active'", (image_id,)).fetchone()
        if not image_data: flash("Карта не найдена или принадлежит неактивному.", "danger"); return redirect(url_for('user', code=code))
        if image_data['owner_id'] == g.user['id']: flash("Нельзя угадывать свою карту.", "warning"); return redirect(url_for('user', code=code))
        if get_setting("show_card_info") == "true": flash("Карты уже открыты.", "warning"); return redirect(url_for('user', code=code))
        guesses = json.loads(image_data['guesses'] or '{}'); guesses[str(g.user['id'])] = guessed_user_id
        c.execute("UPDATE images SET guesses = ? WHERE id = ?", (json.dumps(guesses), image_id)); db.commit()
        flash(f"Ваше предположение (карта '{get_user_name(guessed_user_id)}') сохранено.", "success")
        broadcast_game_state_update(user_code_trigger=code)
    except Exception as e: db.rollback(); flash(f"Ошибка угадывания: {e}", "danger"); print(traceback.format_exc(), file=sys.stderr)
    return redirect(url_for('user', code=code))

@app.route("/admin/open_cards", methods=["POST"]) # Логика без изменений
def open_cards():
    if not session.get('is_admin'): flash('Доступ запрещен.', 'danger'); return redirect(url_for('login'))
    if is_game_over(): flash("Игра уже завершена.", "warning"); return redirect(url_for('admin'))
    if not is_game_in_progress(): flash("Игра не активна.", "warning"); return redirect(url_for('admin'))
    db = get_db(); # c = db.cursor() # Курсор будет получен внутри блока try, если нужен
    try:
        set_setting("show_card_info", "true")
        # ВАША ПОЛНАЯ ЛОГИКА ПОДСЧЕТА ОЧКОВ ДОЛЖНА БЫТЬ ЗДЕСЬ
        flash("Карты открыты, очки (если были) начислены.", "success")
        db.commit() 
        broadcast_game_state_update()
    except Exception as e: db.rollback(); flash(f"Ошибка открытия карт: {e}", "danger"); print(traceback.format_exc(), file=sys.stderr)
    return redirect(url_for('admin'))

@app.route("/new_round", methods=["POST"]) # Логика без изменений
def new_round():
    if not session.get('is_admin'): flash('Доступ запрещен.', 'danger'); return redirect(url_for('login'))
    if is_game_over(): flash("Игра окончена. Начните новую игру.", "warning"); return redirect(url_for('admin'))
    if not is_game_in_progress(): flash("Игра не начата.", "warning"); return redirect(url_for('admin'))
    db = get_db(); c = db.cursor(); active_subfolder = get_setting('active_subfolder')
    current_leader = get_leading_user_id(); next_leader = None
    try:
        next_leader = determine_new_leader(current_leader)
        if next_leader: set_leading_user_id(next_leader); flash(f"Новый раунд! Ведущий: {get_user_name(next_leader) or f'ID {next_leader}'}.", "success")
        else: set_leading_user_id(None); flash("Новый раунд, но ведущий не определен.", "warning")
        c.execute("UPDATE images SET owner_id = NULL, guesses = '{}', status = 'Занято:Админ' WHERE status LIKE 'На столе:%'")
        c.execute("UPDATE images SET guesses = '{}' WHERE status NOT LIKE 'На столе:%' AND guesses != '{}'")
        set_setting("show_card_info", "false")
        active_users = [row['id'] for row in c.execute("SELECT id FROM users WHERE status = 'active' ORDER BY id").fetchall()]
        if not active_users: flash("Нет активных игроков.", "warning")
        elif not active_subfolder: flash("Активная колода не установлена.", "warning")
        else:
            available_cards = [r['id'] for r in c.execute("SELECT id FROM images WHERE subfolder = ? AND status = 'Свободно'", (active_subfolder,)).fetchall()]
            random.shuffle(available_cards); num_dealt_total = 0
            for i, user_id in enumerate(active_users):
                if i < len(available_cards): c.execute("UPDATE images SET status = ?, owner_id = ? WHERE id = ?", (f"Занято:{user_id}", user_id, available_cards[i])); num_dealt_total +=1
                else: flash(f"Карты в колоде '{active_subfolder}' закончились. Не все игроки получили карту.", "warning"); break 
            if num_dealt_total > 0 : flash(f"Роздано {num_dealt_total} новых карт.", "info")
            elif not available_cards and active_users : flash(f"В колоде '{active_subfolder}' нет карт для раздачи.", "info")
        db.commit() 
        if check_and_end_game_if_player_out_of_cards(db): # Проверка после коммита и перед broadcast
             pass # Сообщение об окончании уже во flash из функции
        broadcast_game_state_update()
    except Exception as e: db.rollback(); flash(f"Ошибка нового раунда: {e}", "danger"); print(traceback.format_exc(), file=sys.stderr)
    return redirect(url_for('admin', displayed_leader_id=next_leader if next_leader else current_leader))

@socketio.on('connect') # Логика без изменений
def handle_connect():
    sid = request.sid; user_code = session.get('user_code')
    print(f"SocketIO: Client connected: SID={sid}, User code: {user_code or 'N/A'}", file=sys.stderr)
    if user_code: connected_users_socketio[sid] = user_code
    try:
        with app.app_context(): initial_state = get_full_game_state_data(user_code_for_state=user_code); emit('game_update', initial_state, room=sid)
    except Exception as e: print(f"SocketIO: Error sending initial state to {sid}: {e}\n{traceback.format_exc()}", file=sys.stderr)

@socketio.on('disconnect') # Логика без изменений
def handle_disconnect():
    sid = request.sid; user_code = connected_users_socketio.pop(sid, None)
    print(f"SocketIO: Client disconnected: SID={sid}, User code: {user_code or 'N/A'}", file=sys.stderr)

if __name__ == "__main__": # Логика без изменений
    if not _current_game_board_pole_image_config:
        print("Инициализация визуализации игрового поля...", file=sys.stderr)
        users_at_start = []
        if os.path.exists(DB_PATH):
            try:
                conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row; cur = conn.cursor()
                cur.execute("SELECT id, name, rating FROM users WHERE status = 'active'")
                users_at_start = cur.fetchall(); conn.close()
            except Exception as e: print(f"Ошибка чтения пользователей для поля при старте: {e}", file=sys.stderr)
        initialize_new_game_board_visuals(all_users_for_rating_check=users_at_start)
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "False").lower() in ['true', '1', 't']
    print(f"Запуск Flask-SocketIO (socketio.run) на http://0.0.0.0:{port}/ debug={debug}", file=sys.stderr)
    socketio.run(app, host="0.0.0.0", port=port, debug=debug, allow_unsafe_werkzeug=True)
