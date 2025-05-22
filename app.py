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
        image_folders = ['ariadna', 'detstvo', 'imaginarium', 'odissey', 'pandora', 'persephone', 'soyuzmultfilm', 'himera']
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
    db = get_db()
    current_g_user_dict = None
    if user_code_for_state:
        user_row = db.execute("SELECT id, name, code, rating, status FROM users WHERE code = ?", (user_code_for_state,)).fetchone()
        if user_row:
            current_g_user_dict = dict(user_row)
    active_subfolder_val = get_setting('active_subfolder')

    # --- ДОБАВЛЕНО: Получаем данные обо ВСЕХ пользователях ---
    all_users_info_db = db.execute("SELECT id, name FROM users").fetchall()
    all_users_info_list = [{'id': u['id'], 'name': u['name']} for u in all_users_info_db]
    # --- КОНЕЦ ДОБАВЛЕННОГО БЛОКА ---

    game_state = {
        'game_in_progress': is_game_in_progress(), 'game_over': is_game_over(),
        'show_card_info': get_setting("show_card_info") == "true",
        'active_subfolder': active_subfolder_val, 'db_current_leader_id': get_leading_user_id(),
        'num_active_players': get_active_players_count(db),
        'table_images': [], 'user_cards': [],
        'all_users_for_guessing': [], # Этот список используется для выпадающего списка угадывания (активные игроки с картами на столе)
        'all_users_info': all_users_info_list, # --- ДОБАВЛЕНО: Новый список со всеми пользователями для поиска имен ---
        'on_table_status': False, 'is_current_user_the_db_leader': False,
        'leader_pole_pictogram_path': None, 'leader_pictogram_rating_display': None,
        'game_board': [], 'current_num_board_cells': _current_game_board_num_cells,
        'current_user_data': current_g_user_dict, 'num_cards_on_table': 0,
        'all_cards_placed_for_guessing_phase_to_template': False, 'flashed_messages': []
    }

    raw_table_cards = db.execute("SELECT i.id, i.image, i.subfolder, i.owner_id, u.name as owner_name, i.guesses FROM images i LEFT JOIN users u ON i.owner_id = u.id WHERE i.subfolder = ? AND i.status LIKE 'На столе:%' AND (u.status = 'active' OR u.status IS NULL)", (active_subfolder_val,)).fetchall() if active_subfolder_val else []
    game_state['num_cards_on_table'] = len(raw_table_cards)

    if game_state['game_in_progress'] and not game_state['game_over']:
        # This condition checks if enough cards are placed for guessing phase
        game_state['all_cards_placed_for_guessing_phase_to_template'] = (game_state['num_active_players'] > 0 and game_state['num_cards_on_table'] >= game_state['num_active_players'])

        for card_row in raw_table_cards:
            guesses_data = json.loads(card_row['guesses'] or '{}')
            my_guess_val = None
            # If current user is active, in guessing phase, and not the owner, show their guess
            if current_g_user_dict and current_g_user_dict['status'] == 'active' and \
               game_state['all_cards_placed_for_guessing_phase_to_template'] and \
               not game_state['show_card_info'] and card_row['owner_id'] != current_g_user_dict['id']:
                my_guess_val = guesses_data.get(str(current_g_user_dict['id']))

            game_state['table_images'].append({
                'id': card_row['id'],
                'image': card_row['image'],
                'subfolder': card_row['subfolder'],
                'owner_id': card_row['owner_id'],
                'owner_name': get_user_name(card_row['owner_id']) or "N/A", # Still use get_user_name for initial owner_name
                'guesses': guesses_data,
                'my_guess_for_this_card_value': my_guess_val
            })

        if current_g_user_dict and current_g_user_dict['status'] == 'active' and active_subfolder_val:
            user_cards_db = db.execute("SELECT id, image, subfolder FROM images WHERE owner_id = ? AND subfolder = ? AND status LIKE 'Занято:%'", (current_g_user_dict['id'], active_subfolder_val)).fetchall()
            game_state['user_cards'] = [{'id': r['id'], 'image': r['image'], 'subfolder': r['subfolder']} for r in user_cards_db]

            # Check if current user has a card on the table
            if any(tc['owner_id'] == current_g_user_dict['id'] for tc in game_state['table_images']):
                game_state['on_table_status'] = True

            # Populate all_users_for_guessing with active users who have cards on the table
            # --- ИЗМЕНЕНО: Этот список только для выпадающего списка, имена берутся из all_users_info ---
            all_active_users_db = db.execute("SELECT id, name FROM users WHERE status = 'active'").fetchall()
            active_users_with_card_on_table = [
                u for u in all_active_users_db
                if any(card['owner_id'] == u['id'] for card in game_state['table_images'])
            ]
            game_state['all_users_for_guessing'] = [{'id': u['id'], 'name': u['name']} for u in active_users_with_card_on_table] # Пока оставляем имена здесь, но основная логика в user.html будет брать из all_users_info
            # --- КОНЕЦ ИЗМЕНЕННОГО БЛОКА ---


            if game_state['db_current_leader_id'] is not None:
                game_state['is_current_user_the_db_leader'] = (current_g_user_dict['id'] == game_state['db_current_leader_id'])

            # Leader pictogram logic (only for the active leader, before placing card)
            if game_state['is_current_user_the_db_leader'] and not game_state['on_table_status'] and \
               not game_state['show_card_info'] and not game_state['all_cards_placed_for_guessing_phase_to_template']:
                leader_rating = int(current_g_user_dict.get('rating', 0))
                game_state['leader_pictogram_rating_display'] = leader_rating
                if leader_rating > 0 and _current_game_board_pole_image_config and leader_rating <= _current_game_board_num_cells and (leader_rating - 1) < len(_current_game_board_pole_image_config):
                    game_state['leader_pole_pictogram_path'] = _current_game_board_pole_image_config[leader_rating - 1]

    elif game_state['show_card_info']:
        # If game is not in progress but cards are shown (e.g., after scoring)
        for card_row in raw_table_cards:
             guesses_data = json.loads(card_row['guesses'] or '{}')
             game_state['table_images'].append({
                 'id': card_row['id'],
                 'image': card_row['image'],
                 'subfolder': card_row['subfolder'],
                 'owner_id': card_row['owner_id'],
                 'owner_name': get_user_name(card_row['owner_id']) or "N/A",
                 'guesses': guesses_data,
                 'my_guess_for_this_card_value': None # No active guessing possible here
             })
        # all_users_for_guessing remains empty in this case


    # Always get data for the game board based on active users
    all_active_users_for_board = db.execute("SELECT id, name, rating FROM users WHERE status = 'active'").fetchall()
    game_state['game_board'] = generate_game_board_data_for_display(all_active_users_for_board)
    game_state['current_num_board_cells'] = _current_game_board_num_cells

    # Flashed messages are handled by the template on initial render/redirect.
    # For SocketIO updates, we don't need to pass them here.

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
# ===== ИЗМЕНЕНИЯ В МАРШРУТЕ ADMIN =====
@app.route("/admin", methods=["GET", "POST"])
def admin():
    # Проверка авторизации администратора
    if not session.get('is_admin'):
        flash('Требуется авторизация.', 'warning')
        return redirect(url_for('login', next=request.url))

    db = get_db()
    c = db.cursor() # Получаем курсор

    # --- POST-обработка ---
    if request.method == "POST":
        # Определяем действие администратора по скрытому полю или значению кнопки
        action_admin = request.form.get("action_admin")

        if action_admin == "set_active_deck_admin":
            new_active_subfolder = request.form.get("active_subfolder")
            set_setting("active_subfolder", new_active_subfolder if new_active_subfolder else "")
            flash(f"Активная колода изменена на '{new_active_subfolder or 'Не выбрана'}'.", "success" if new_active_subfolder else "info")
            db.commit() # Фиксируем изменение настройки
            broadcast_game_state_update() # Сообщаем клиентам об изменении
            return redirect(url_for('admin')) # Перенаправляем обратно на страницу админки

        elif action_admin == "toggle_show_card_info_admin":
            new_show_info = not (get_setting('show_card_info') == 'true')
            set_setting('show_card_info', 'true' if new_show_info else 'false')
            db.commit() # Фиксируем изменение настройки
            flash(f"Отображение инфо о картах {'вкл' if new_show_info else 'выкл'}.", "info")
            broadcast_game_state_update() # Сообщаем клиентам об изменении
            return redirect(url_for('admin')) # Перенаправляем обратно на страницу админки

        elif action_admin == "add_user": # Действие добавления пользователя (предполагается action_admin="add_user" в форме)
             name = request.form.get('name', '').strip()
             # num_cards_to_deal_if_active = int(request.form.get('num_cards', 0)) # Поле num_cards игнорируется при добавлении
             if not name:
                 flash("Имя не может быть пустым.", "warning")
                 return redirect(url_for('admin'))
             try:
                 # Проверяем, существует ли пользователь с таким именем
                 user = c.execute("SELECT id, code, status, rating FROM users WHERE name = ?", (name,)).fetchone()
                 if user:
                     flash(f"Пользователь с именем '{name}' уже существует (ID: {user['id']}).", "warning")
                 else:
                     code = generate_unique_code()
                     # Статус: активен, если игра не идет, иначе ожидающий
                     status = 'pending' if is_game_in_progress() else 'active'
                     c.execute("INSERT INTO users (name, code, status, rating) VALUES (?, ?, ?, 0)", (name, code, status))
                     uid = c.lastrowid # Получаем ID нового пользователя
                     db.commit() # Фиксируем создание пользователя

                     flash(f"Пользователь '{name}' добавлен (Код: {code}). Статус: {'Ожидает' if status == 'pending' else 'Активен'}.", "success")
                     # Отправляем обновление, чтобы другие клиенты увидели нового игрока в списке/на поле ожидания
                     broadcast_game_state_update()
             except sqlite3.Error as e:
                 db.rollback() # Откатываем изменения в случае ошибки БД
                 flash(f"Ошибка БД при добавлении пользователя: {e}", "danger")
                 print(f"CRITICAL ERROR during add_user: {e}\n{traceback.format_exc()}", file=sys.stderr)
             except Exception as e:
                 db.rollback() # Откатываем изменения в случае любой другой ошибки
                 flash(f"Произошла ошибка при добавлении пользователя: {e}", "danger")
                 print(f"CRITICAL ERROR during add_user: {e}\n{traceback.format_exc()}", file=sys.stderr)
             return redirect(url_for('admin')) # Перенаправляем обратно

        elif request.form.get("delete_user_id"): # Действие удаления пользователя (определяется по наличию параметра delete_user_id)
            user_id_to_delete = request.form.get("delete_user_id")
            if user_id_to_delete:
                try:
                    user_id_to_delete_int = int(user_id_to_delete)

                    # Получаем информацию об удаляемом пользователе до удаления
                    deleted_user_info = c.execute("SELECT id, name FROM users WHERE id = ?", (user_id_to_delete_int,)).fetchone()
                    if not deleted_user_info:
                         flash(f"Ошибка: Пользователь с ID {user_id_to_delete_int} не найден.", "warning")
                         return redirect(url_for('admin'))

                    deleted_user_name = deleted_user_info['name'] or f'ID {user_id_to_delete_int}'

                    # Проверяем, является ли удаляемый пользователь текущим ведущим
                    current_leader_id = get_leading_user_id()
                    is_deleted_user_leader = (current_leader_id is not None and current_leader_id == user_id_to_delete_int)

                    # 1. Возвращаем все карты удаляемого пользователя в колоду и сбрасываем их предположения
                    # Это затронет как карты в руке ('Занято:...'), так и карты на столе ('На столе:...').
                    c.execute("UPDATE images SET owner_id = NULL, status = 'Свободно', guesses = '{}' WHERE owner_id = ?", (user_id_to_delete_int,))
                    print(f"Admin Delete: Вернули карты пользователя '{deleted_user_name}' (ID {user_id_to_delete_int}) в колоду и сбросили предположения на них.", file=sys.stderr)

                    # 2. Удаляем запись пользователя из таблицы users
                    c.execute("DELETE FROM users WHERE id = ?", (user_id_to_delete_int,))
                    db.commit() # Фиксируем удаление пользователя и обновление карт

                    flash(f"Пользователь '{deleted_user_name}' удален.", "success")
                    print(f"Admin Delete: Пользователь '{deleted_user_name}' (ID {user_id_to_delete_int}) успешно удален из БД.", file=sys.stderr)


                    # 3. Если удаленный пользователь был ведущим, автоматически начинаем новый раунд
                    if is_deleted_user_leader:
                         print(f"Admin Delete: Удаленный пользователь '{deleted_user_name}' был ведущим. Автоматически запускаем новый раунд...", file=sys.stderr)

                         # Логика начала нового раунда (адаптировано из new_round route)
                         # Определяем нового ведущего из оставшихся активных игроков
                         next_leader_id_ar = determine_new_leader(None) # Начинаем выбор ведущего с начала списка

                         if next_leader_id_ar:
                              set_leading_user_id(next_leader_id_ar)
                              # Сообщение о новом ведущем будет добавлено после broadcast
                         else:
                              set_leading_user_id(None)
                              # Сообщение об отсутствии ведущего будет добавлено после broadcast

                         # Перемещаем любые карты, оставшиеся на столе (от ЛЮБЫХ игроков), в статус "Занято:Админ" и сбрасываем информацию
                         c.execute("UPDATE images SET owner_id = NULL, guesses = '{}', status = 'Занято:Админ' WHERE status LIKE 'На столе:%'")
                         print("Admin Delete: Переместили все карты со стола в статус 'Занято:Админ'.", file=sys.stderr)

                         # Сбрасываем предположения на любых других картах, которые не были на столе
                         c.execute("UPDATE images SET guesses = '{}' WHERE status NOT LIKE 'На столе:%' AND guesses != '{}'")
                         print("Admin Delete: Сброшены предположения на картах вне стола.", file=sys.stderr)

                         # Сбрасываем флаг показа информации о картах
                         set_setting("show_card_info", "false")
                         print("Admin Delete: Флаг show_card_info сброшен.", file=sys.stderr)


                         # Раздаем ОДНУ новую карту каждому оставшемуся активному игроку из активной колоды
                         # При автоматическом начале раунда после удаления ведущего раздаем по 1 карте для быстрого продолжения
                         num_cards_per_player_for_new_round = 1
                         remaining_active_user_ids = [row['id'] for row in c.execute("SELECT id FROM users WHERE status = 'active' ORDER BY id").fetchall()]

                         if not remaining_active_user_ids:
                              flash("Нет активных игроков для раздачи карт в новом раунде после удаления ведущего.", "warning")
                              print("Admin Delete: Нет активных игроков для раздачи карт после удаления ведущего.", file=sys.stderr)
                         else:
                              active_subfolder = get_setting('active_subfolder') # Нужна активная колода
                              if not active_subfolder:
                                   flash("Активная колода не установлена для раздачи карт в новом раунде после удаления ведущего.", "warning")
                                   print("Admin Delete: Активная колода не установлена для раздачи карт после удаления ведущего.", file=sys.stderr)
                              else:
                                   available_cards = [r['id'] for r in c.execute("SELECT id FROM images WHERE subfolder = ? AND status = 'Свободно'", (active_subfolder,)).fetchall()]
                                   random.shuffle(available_cards)
                                   num_dealt_total = 0
                                   # Раздаем по одной карте каждому игроку
                                   for i, user_id in enumerate(remaining_active_user_ids):
                                        if num_dealt_total < len(available_cards):
                                             c.execute("UPDATE images SET status = ?, owner_id = ? WHERE id = ?", (f"Занято:{user_id}", user_id, available_cards[num_dealt_total]));
                                             num_dealt_total += 1
                                        else:
                                             flash(f"Внимание: Закончились карты в колоде '{active_subfolder}'. Не все игроки получили по 1 карте в новом раунде после удаления ведущего.", "warning");
                                             print(f"Admin Delete: Закончились карты ({active_subfolder}) при раздаче 1 карты на игрока после удаления ведущего.", file=sys.stderr)
                                             break # Недостаточно карт

                                   if num_dealt_total > 0 : flash(f"В новом раунде роздано {num_dealt_total} новых карт после удаления ведущего.", "info")
                                   elif not available_cards and remaining_active_user_ids : flash(f"В колоде '{active_subfolder}' нет карт для раздачи в новом раунде после удаления ведущего.", "info")
                                   print(f"Admin Delete: Роздано {num_dealt_total} карт в новом раунде после удаления ведущего.", file=sys.stderr)


                         # Фиксируем все изменения, связанные с новым раундом
                         db.commit()

                         # Сообщаем о новом ведущем, если он был определен
                         if next_leader_id_ar:
                              flash(f"Новый ведущий: {get_user_name(next_leader_id_ar) or f'ID {next_leader_id_ar}'}.", "info")
                         elif remaining_active_user_ids: # Предупреждение, если были игроки, но ведущий не определен
                              flash("Ведущий не определен для нового раунда (нет активных игроков для выбора?) после удаления ведущего.", "warning")

                    # Конец адаптированной логики нового раунда


                    # 4. Отправляем обновление состояния игры всем клиентам, чтобы 반영 все изменения
                    broadcast_game_state_update()

                except ValueError:
                    flash("Некорректный ID пользователя для удаления.", "danger")
                    print("Admin Delete Error: Некорректный формат ID пользователя.", file=sys.stderr)
                except sqlite3.Error as e:
                    db.rollback() # Откатываем изменения в случае ошибки БД
                    flash(f"Ошибка БД при удалении пользователя: {e}", "danger")
                    print(f"CRITICAL ERROR during user deletion DB operation: {e}\n{traceback.format_exc()}", file=sys.stderr)
                except Exception as e:
                    # Перехватываем любые другие исключения в процессе удаления
                    db.rollback() # Убеждаемся, что изменения отменены, если что-то пошло не так
                    flash(f"Произошла ошибка при удалении пользователя: {e}", "danger")
                    print(f"CRITICAL ERROR during user deletion process: {e}\n{traceback.format_exc()}", file=sys.stderr)

            # Всегда перенаправляем обратно на страницу администратора после обработки POST-запроса
            return redirect(url_for('admin'))

        elif action_admin == "reset_game_board_visuals": # Действие сброса визуализации игрового поля
            num_cells_str = request.form.get('num_cells_for_board_reset', '').strip()
            num_cells = None
            if num_cells_str:
                try:
                    num_cells = int(num_cells_str)
                    if num_cells <= 0:
                         num_cells = None # Используем значение по умолчанию/автоопределение, если число неположительное
                         flash("Кол-во ячеек должно быть положительным. Использовано автоопределение.", "warning")
                except ValueError:
                    flash("Некорректное значение для кол-ва ячеек. Использовано автоопределение.", "warning")
                    num_cells = None

            # Получаем активных пользователей для корректного автоопределения размера поля
            active_users_for_board_init = c.execute("SELECT id, name, rating FROM users WHERE status = 'active'").fetchall()
            # Инициализируем новую конфигурацию визуализации поля
            initialize_new_game_board_visuals(num_cells_for_board=num_cells, all_users_for_rating_check=active_users_for_board_init)
            # Изменения _current_game_board_pole_image_config хранятся в памяти, не требуют коммита БД
            flash("Визуализация игрового поля обновлена.", "success")
            # Сообщаем клиентам об обновлении игрового поля
            broadcast_game_state_update()
            return redirect(url_for('admin'))

        # Добавьте обработчики для других POST-действий здесь, если они есть

        # Обработка неизвестных POST-действий
        else:
            flash("Неизвестное действие администратора.", "warning")
            print(f"Admin POST: Получено неизвестное действие: {request.form}", file=sys.stderr)
            return redirect(url_for('admin'))


    # --- GET-обработка (сбор данных для шаблона admin.html) ---
    # Этот блок выполняется при обычном GET-запросе страницы админки,
    # чтобы собрать все данные для отображения текущего состояния.
    # Он остается без изменений с предыдущей версии.

    # Получаем список пользователей, сортируя по статусу (активные первыми) и имени
    users_raw = c.execute("SELECT id, name, code, rating, status FROM users ORDER BY status DESC, name ASC").fetchall()
    users_for_template = [dict(row) for row in users_raw]

    # Получаем список изображений
    images_db = c.execute("SELECT id, subfolder, image, status, owner_id, guesses FROM images ORDER BY subfolder, id LIMIT 500").fetchall() # Ограничение для производительности
    images_for_template = []
    # Собираем информацию о владельцах карт и всех предположениях для отображения в таблице
    image_owners_for_template = {}
    all_guesses_for_template = {}

    for img_row in images_db:
        img_dict = dict(img_row)
        try:
            # Парсим предположения
            img_dict['guesses'] = json.loads(img_row['guesses'] or '{}')
            # Сохраняем предположения, если они есть
            if img_dict['guesses'] and img_dict['id'] is not None:
                 all_guesses_for_template[img_dict['id']] = img_dict['guesses']
        except json.JSONDecodeError:
            img_dict['guesses'] = {} # В случае ошибки парсинга JSON делаем пустой словарь
        # Сохраняем ID владельца, если он есть
        if img_dict.get('owner_id') is not None:
             image_owners_for_template[img_dict['id']] = img_dict['owner_id']
        images_for_template.append(img_dict)

    # Получаем список всех подпапок с изображениями
    subfolders_for_template = [row['subfolder'] for row in c.execute("SELECT DISTINCT subfolder FROM images ORDER BY subfolder").fetchall()]

    # Фильтруем список активных пользователей для удобства
    active_users_for_template = [u for u in users_for_template if u['status'] == 'active']

    # Пересчитываем количество предположений для каждого активного пользователя и проверяем на дубликаты
    guess_counts_by_user_for_template = {u['id']: 0 for u in active_users_for_template}
    user_has_duplicate_guesses_for_template = {u['id']: False for u in active_users_for_template}

    if all_guesses_for_template and active_users_for_template:
        for user_item_dict in active_users_for_template: # Перебираем активных пользователей
            user_id_str = str(user_item_dict['id'])
            guesses_made_by_this_user_targets = [] # Список ID игроков, которых угадал данный пользователь
            for image_id_key_str in all_guesses_for_template: # Перебираем карты с предположениями
                guesses_on_one_image = all_guesses_for_template[image_id_key_str] # Предположения по одной карте
                if user_id_str in guesses_on_one_image: # Если текущий пользователь угадывал по этой карте
                    # Увеличиваем счетчик предположений для этого пользователя
                    guess_counts_by_user_for_template[user_item_dict['id']] += 1
                    # Сохраняем ID игрока, которого он угадал (значение в словаре предположений)
                    try:
                         guessed_target_id = int(guesses_on_one_image[user_id_str])
                         guesses_made_by_this_user_targets.append(guessed_target_id)
                    except (ValueError, TypeError):
                         print(f"Admin Template: Некорректный guessed_target_id в предположениях для карты {image_id_key_str} пользователем {user_id_str}.", file=sys.stderr)
                         pass # Пропускаем некорректное значение

            # Проверка на дубликаты: угадывал ли пользователь ОДНОГО И ТОГО ЖЕ ИГРОКА для РАЗНЫХ карт
            if len(guesses_made_by_this_user_targets) > len(set(guesses_made_by_this_user_targets)):
                user_has_duplicate_guesses_for_template[user_item_dict['id']] = True


    current_active_subfolder = get_setting('active_subfolder') or ''
    current_leader_from_db = get_leading_user_id()

    # Подсчет количества свободных изображений в активной колоде
    free_image_count_for_template = sum(1 for img in images_for_template if img.get('status') == 'Свободно' and img.get('subfolder') == current_active_subfolder)


    # Получаем данные для построения игрового поля (только активные игроки)
    db_users_for_board_fetch = c.execute("SELECT id, name, rating FROM users WHERE status = 'active'").fetchall()
    game_board_data_for_template = generate_game_board_data_for_display(db_users_for_board_fetch)


    # Отображаем шаблон admin.html, передавая все собранные данные
    return render_template("admin.html",
                           users=users_for_template,
                           images=images_for_template,
                           subfolders=subfolders_for_template,
                           active_subfolder=current_active_subfolder,
                           db_current_leader_id=current_leader_from_db,
                           # potential_next_leader_id=potential_next_leader_id, # Убрано из шаблона, менее критично для админки
                           free_image_count=free_image_count_for_template,
                           image_owners=image_owners_for_template, # Передаем владельцев карт для отображения деталей предположений
                           game_board=game_board_data_for_template,
                           all_guesses=all_guesses_for_template, # Передаем все предположения для отображения деталей
                           guess_counts_by_user=guess_counts_by_user_for_template, # Передаем кол-во предположений по каждому игроку
                           user_has_duplicate_guesses=user_has_duplicate_guesses_for_template, # Передаем флаги дубликатов
                           get_user_name_func=get_user_name, # Функция для получения имени пользователя по ID в шаблоне
                           current_num_board_cells=_current_game_board_num_cells # Передаем текущий размер игрового поля
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

@app.route("/admin/open_cards", methods=["POST"])
def open_cards():
    # Проверка авторизации администратора
    if not session.get('is_admin'):
        flash('Доступ запрещен.', 'danger')
        return redirect(url_for('login'))

    # Проверка состояния игры
    if is_game_over():
        flash("Игра уже завершена.", "warning")
        return redirect(url_for('admin'))

    if not is_game_in_progress():
        flash("Игра не активна.", "warning")
        return redirect(url_for('admin'))

    db = get_db()
    c = db.cursor()

    try:
        # 1. Устанавливаем флаг, чтобы показать информацию о картах
        set_setting("show_card_info", "true")

        # --- Логика подсчета очков ---

        # Получаем список активных игроков с их текущим рейтингом
        active_users = c.execute("SELECT id, name, rating FROM users WHERE status = 'active'").fetchall()
        active_user_ids = [user['id'] for user in active_users]
        active_users_dict = {user['id']: dict(user) for user in active_users} # Словарь для быстрого поиска по ID

        # Если нет активных игроков, просто коммитим изменение show_card_info и выходим
        if not active_users:
            flash("Нет активных игроков для подсчета очков.", "warning")
            db.commit() # Commit the setting change
            broadcast_game_state_update()
            return redirect(url_for('admin'))

        # Получаем карты, которые находятся на столе
        table_cards = c.execute("SELECT id, owner_id, guesses FROM images WHERE status LIKE 'На столе:%'").fetchall()

        # Получаем ID текущего ведущего
        current_leader_id = get_leading_user_id()
        leader_card_on_table = None # Переименовал для ясности
        
        # Находим карту, выложенную ведущим
        if current_leader_id in active_user_ids:
            for card in table_cards:
                if card['owner_id'] == current_leader_id:
                    leader_card_on_table = card
                    break
        elif current_leader_id is not None:
            print(f"Scoring Warning: Current leader ID {current_leader_id} is not in active users. Cannot find leader card on table.", file=sys.stderr)


        # Инициализируем словарь для хранения изменений рейтинга для каждого активного игрока
        # Изначально у всех 0 изменений.
        rating_changes = {user_id: 0 for user_id in active_user_ids}
        
        # Флаги для условий ведущего
        leader_was_correctly_guessed_by_all_others = False
        leader_was_guessed_by_none_others = False

        # --- Анализ предположений относительно карты *ведущего* ---
        correct_guesser_ids_for_leader = [] # Список ID игроков, правильно угадавших карту ведущего
        # Количество активных игроков, кроме ведущего.
        total_other_active_players = len(active_users_dict) - (1 if current_leader_id in active_users_dict else 0)

        if leader_card_on_table and current_leader_id in active_users_dict: # Проверяем, что ведущий активен и его карта на столе
            try:
                leader_guesses = json.loads(leader_card_on_table['guesses'] or '{}')
            except json.JSONDecodeError:
                print(f"Scoring Error: Invalid JSON in leader card {leader_card_on_table['id']} guesses: {leader_card_on_table['guesses']}", file=sys.stderr)
                leader_guesses = {}

            # Собираем ID игроков, которые правильно угадали карту ведущего (исключая самого ведущего)
            for guesser_id_str, guessed_owner_id_val in leader_guesses.items():
                try:
                    guesser_id = int(guesser_id_str)
                    guessed_owner_id_int = int(guessed_owner_id_val)
                except (ValueError, TypeError):
                    print(f"Scoring Error: Invalid guesser_id or guessed_owner_id format in leader card {leader_card_on_table['id']} guesses: {guesser_id_str} -> {guessed_owner_id_val}", file=sys.stderr)
                    continue # Пропускаем некорректное предположение

                # Учитываем только предположения от других активных игроков
                if guesser_id in active_user_ids and guesser_id != current_leader_id and guessed_owner_id_int == current_leader_id:
                    correct_guesser_ids_for_leader.append(guesser_id)

            correct_leader_guesses_count_by_others = len(correct_guesser_ids_for_leader)

            # Правило 1: Если карточку ведущего угадали все игроки...
            if total_other_active_players > 0 and correct_leader_guesses_count_by_others == total_other_active_players:
                leader_was_correctly_guessed_by_all_others = True
                leader_current_rating = active_users_dict[current_leader_id]['rating']
                # Ведущий теряет 3 балла (или идет на поле 1). Это прямое изменение рейтинга.
                new_leader_rating = max(1, leader_current_rating - 3)
                c.execute("UPDATE users SET rating = ? WHERE id = ?", (new_leader_rating, current_leader_id))
                db.commit() # Сохраняем это изменение немедленно, так как дальше не будет других начислений для ведущего

                print(f"Scoring: Ведущий ({get_user_name(current_leader_id)}) угадан ВСЕМИ ({correct_leader_guesses_count_by_others} из {total_other_active_players} других игроков). Рейтинг изменен с {leader_current_rating} на {new_leader_rating}. Дальнейший подсчет очков пропускается.", file=sys.stderr)
                flash("Карты открыты, очки начислены. Ведущий угадан всеми.", "success")
                broadcast_game_state_update()
                return redirect(url_for('admin')) # *** Выход из функции ***

            # Правило 2.1: Если карточку ведущего никто не угадал (и Правило 1 не сработало)
            elif correct_leader_guesses_count_by_others == 0 and total_other_active_players > 0:
                leader_was_guessed_by_none_others = True
                leader_current_rating = active_users_dict[current_leader_id]['rating']
                # Ведущий теряет 2 балла.
                rating_changes[current_leader_id] -= 2 # Добавляем к изменению рейтинга
                print(f"Scoring: Ведущий ({get_user_name(current_leader_id)}) не угадан НИКЕМ ({correct_leader_guesses_count_by_others} из {total_other_active_players} других игроков). Ведущий теряет 2 очка.", file=sys.stderr)
        
        elif current_leader_id in active_users_dict:
            # Ведущий активен, но других активных игроков нет или нет карты ведущего на столе.
            # Специальные правила для ведущего (1, 2.1) не применяются.
            print(f"Scoring: Ведущий ({get_user_name(current_leader_id)}) - особый случай (нет других игроков/нет карты). Специальные правила ведущего не применяются.", file=sys.stderr)
        elif current_leader_id is None:
            print("Scoring Warning: No current leader defined. Cannot apply leader scoring rules.", file=sys.stderr)
        elif leader_card_on_table is None and current_leader_id in active_users_dict:
            print(f"Scoring Warning: Leader ({get_user_name(current_leader_id)}) is active but no leader card on table. Cannot apply leader scoring rules.", file=sys.stderr)


        # --- Начисление очков по остальным правилам, если Правило 1 НЕ сработало ---

        # Правило 2.2 (часть 1): Все игроки (включая ведущего), чьи карточки угадали, получают по +1 очку за каждого угадавшего.
        # Этот блок теперь обрабатывает ВСЕХ активных игроков.
        for card in table_cards:
            card_owner_id = card['owner_id']
            if card_owner_id not in active_user_ids:
                continue # Пропускаем неактивных владельцев карт

            try:
                guesses_on_this_card = json.loads(card['guesses'] or '{}')
            except json.JSONDecodeError:
                guesses_on_this_card = {}
                print(f"Scoring Error: Invalid JSON in card {card['id']} guesses: {card['guesses']}", file=sys.stderr)

            guessed_by_this_card_count = 0
            for guesser_id_str, guessed_owner_id_val in guesses_on_this_card.items():
                try:
                    guesser_id = int(guesser_id_str)
                    guessed_owner_id_int = int(guessed_owner_id_val)
                except (ValueError, TypeError):
                    continue # Пропускаем некорректное предположение

                # Проверяем, что угадавший игрок активен, не угадывает свою карту и угадал правильно
                if guesser_id in active_user_ids and guesser_id != card_owner_id and guessed_owner_id_int == card_owner_id:
                    guessed_by_this_card_count += 1
            
            if guessed_by_this_card_count > 0:
                rating_changes[card_owner_id] += guessed_by_this_card_count
                print(f"Scoring: Игрок {get_user_name(card_owner_id)} (владелец карты {card['id']}) получил +{guessed_by_this_card_count} очков (угадано {guessed_by_this_card_count} игроками) (Rule 2.2 part 1).", file=sys.stderr)


        # Правило 2.2 (часть 2): По 3 очка получают все игроки, правильно угадавшие карточку Ведущего (не ведущий).
        # Этот блок выполняется, если Правило 1 не сработало.
        for guesser_id in correct_guesser_ids_for_leader:
            if guesser_id in rating_changes: # Убедимся, что игрок активен
                rating_changes[guesser_id] += 3
                print(f"Scoring: Игрок {get_user_name(guesser_id)} получил +3 очка за правильное угадывание карты ведущего (Rule 2.2 part 2).", file=sys.stderr)
            else:
                print(f"Scoring Warning: Guesser ID {guesser_id} for leader card not found in active users rating_changes dict (Rule 2.2 part 2).", file=sys.stderr)


        # Правило 2.3: Ведущий получает 3 очка если его карточку угадали не все игроки и не никто.
        # Это условие срабатывает, если не сработал ни случай "все угадали", ни "никто не угадал".
        # (т.е. `leader_was_correctly_guessed_by_all_others` = False И `leader_was_guessed_by_none_others` = False)
        if current_leader_id in active_users_dict and not leader_was_correctly_guessed_by_all_others and not leader_was_guessed_by_none_others:
            # Убеждаемся, что есть хотя бы один угадавший, чтобы это не было "никто"
            # и что количество угадавших меньше, чем общее количество других игроков, чтобы это не было "все"
            if correct_leader_guesses_count_by_others > 0 and correct_leader_guesses_count_by_others < total_other_active_players:
                rating_changes[current_leader_id] += 3
                print(f"Scoring: Ведущий ({get_user_name(current_leader_id)}) угадан SOME ({correct_leader_guesses_count_by_others} игроков). Получает +3 очка (Rule 2.3).", file=sys.stderr)
            # Если total_other_active_players = 0, то этот случай также не должен срабатывать, 
            # так как условие "не все и не никто" не имеет смысла.
            elif total_other_active_players == 0:
                 print(f"Scoring: Ведущий ({get_user_name(current_leader_id)}) - единственный активный игрок. Правило 2.3 не применяется.", file=sys.stderr)


        # 4. Окончательное обновление рейтинга в базе данных на основе накопленных изменений
        for user in active_users:
            user_id = user['id']
            current_rating = user['rating']
            
            calculated_rating_change = rating_changes.get(user_id, 0)
            final_rating = max(1, current_rating + calculated_rating_change) # Убеждаемся, что рейтинг не падает ниже 1

            if calculated_rating_change != 0:
                print(f"Scoring Update: Игрок ({get_user_name(user_id)}) итоговый рейтинг: {current_rating} -> {final_rating} (изменения: {calculated_rating_change}).", file=sys.stderr)
            else: 
                print(f"Scoring Update: Игрок ({get_user_name(user_id)}) рейтинг остался {current_rating}.", file=sys.stderr)

            # Выполняем обновление в базе данных
            c.execute("UPDATE users SET rating = ? WHERE id = ?", (final_rating, user_id))

        # --- Конец логики подсчета очков ---

        db.commit() # Сохраняем все изменения в базе данных

        flash("Карты открыты, очки начислены.", "success")
        # Отправляем обновление состояния игры всем подключенным клиентам
        broadcast_game_state_update()

    except Exception as e:
        # В случае любой ошибки откатываем изменения в базе данных
        db.rollback()
        flash(f"Ошибка открытия карт/подсчета очков: {e}", "danger")
        print(f"CRITICAL ERROR in open_cards: {e}\n{traceback.format_exc()}", file=sys.stderr)

    # Перенаправляем обратно на страницу администратора
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
