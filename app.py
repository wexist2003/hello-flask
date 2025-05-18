import json
import sys
import sqlite3
import os
import string
import random
import traceback
from flask import Flask, render_template, request, redirect, url_for, g, flash, session, jsonify
from flask_socketio import SocketIO, emit
from functools import wraps

app = Flask(__name__)
# ВАЖНО: Убедитесь, что этот ключ ИДЕНТИЧЕН тому, что был в работающей версии
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your_very_secret_fallback_key_for_dev_only_12345')
if app.config['SECRET_KEY'] == 'your_very_secret_fallback_key_for_dev_only_12345':
    print("ПРЕДУПРЕЖДЕНИЕ: Используется SECRET_KEY по умолчанию. Установите переменную окружения SECRET_KEY!", file=sys.stderr)

socketio = SocketIO(app)
DB_PATH = 'database.db'

GAME_BOARD_POLE_IMG_SUBFOLDER = "pole"
GAME_BOARD_POLE_IMAGES = [f"p{i}.jpg" for i in range(1, 8)] # Пример названий файлов пиктограмм
DEFAULT_NUM_BOARD_CELLS = 40
_current_game_board_pole_image_config = []
_current_game_board_num_cells = 0

connected_users_socketio = {}  # {sid: user_code}

# --- Декоратор для проверки администратора ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Проверяем наличие сессии и флага администратора в ней
        if not session.get('is_admin'):
            flash("Необходимо войти как администратор.", "danger")
            return redirect(url_for('admin_login'))
        # Можно также добавить проверку по IP или другому критерию, если необходимо
        return f(*args, **kwargs)
    return decorated_function
# --- Конец декоратора ---


# --- Вспомогательные функции ---
def get_db():
    if 'db' not in g:
        # Увеличиваем таймаут при необходимости
        g.db = sqlite3.connect(DB_PATH, timeout=20)
        g.db.row_factory = sqlite3.Row
        # Позволяем использовать внешний API для транзакций (например, begin, commit, rollback)
        g.db.isolation_level = None # Важно для ручного управления транзакциями в try/except
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        # Убедимся, что любые незавершенные транзакции откатываются при закрытии
        if db.in_transaction:
            db.rollback()
        db.close()

def init_db():
    """Инициализирует базу данных. Создает таблицы, если их нет."""
    print("Attempting to initialize database...", file=sys.stderr)
    if not os.path.exists(DB_PATH):
        print(f"Database file not found at {DB_PATH}, will attempt to create.", file=sys.stderr)
    with app.app_context():
        db = get_db()
        try:
            with app.open_resource('schema.sql', mode='r') as f:
                db.cursor().executescript(f.read())
            db.commit()
            print("Database schema created/verified.", file=sys.stderr)

            # Initial settings if database is new
            if db.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0:
                 print("Adding initial settings...", file=sys.stderr)
                 db.execute("INSERT INTO settings (key, value) VALUES ('game_in_progress', 'false')")
                 db.execute("INSERT INTO settings (key, value) VALUES ('game_over', 'false')")
                 db.execute("INSERT INTO settings (key, value) VALUES ('show_card_info', 'false')")
                 db.execute("INSERT INTO settings (key, value) VALUES ('active_subfolder', '')")
                 db.execute("INSERT INTO settings (key, value) VALUES ('current_leader_id', NULL)")
                 db.execute("INSERT INTO settings (key, value) VALUES ('game_board_config', ?)", (json.dumps({"pole_images": GAME_BOARD_POLE_IMAGES, "num_cells": DEFAULT_NUM_BOARD_CELLS}),)) # Save default board config
                 # Добавляем администратора по умолчанию, если нет пользователей (только для первой инициализации)
                 if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
                     admin_code = generate_user_code() # Генерируем код и для админа
                     db.execute("INSERT INTO users (name, code, status, rating, is_admin) VALUES (?, ?, ?, ?, ?)", ('Admin', admin_code, 'active', 0, 1))
                     print(f"Default admin user created with code: {admin_code}", file=sys.stderr)
                 db.commit()
                 print("Initial settings added and committed.", file=sys.stderr)
            else:
                 print("Settings table already populated.", file=sys.stderr)

        except sqlite3.OperationalError as e:
            print(f"SQLite OperationalError during init_db: {e}. This might happen if the database is being accessed by another process or if schema.sql has errors.", file=sys.stderr)
            traceback.print_exc()
            db.rollback()
        except Exception as e:
            print(f"An unexpected error occurred during init_db: {e}", file=sys.stderr)
            traceback.print_exc()
            db.rollback() # Откатываем в случае ошибки


def get_setting(key):
    db = get_db()
    setting = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return setting['value'] if setting else None

def set_setting(key, value):
    db = get_db()
    db.execute("UPDATE settings SET value = ? WHERE key = ?", (value, key))
    # db.commit() # Не делаем commit здесь, commit должен быть в вызывающей функции

def is_game_in_progress():
    return get_setting('game_in_progress') == 'true'

def is_game_over():
     return get_setting('game_over') == 'true'

def get_leading_user_id():
    leader_id_val = get_setting('current_leader_id')
    return int(leader_id_val) if leader_id_val is not None and leader_id_val != 'None' else None # Проверяем также строковое значение 'None'

def get_active_players_count(db):
    count = db.execute("SELECT COUNT(*) FROM users WHERE status = 'active'").fetchone()[0]
    return count

def get_user_name(user_id):
    db = get_db()
    user = db.execute("SELECT name FROM users WHERE id = ?", (user_id,)).fetchone()
    return user['name'] if user else "Unknown User"

def generate_user_code(length=6):
    characters = string.ascii_letters + string.digits
    db = get_db()
    while True:
        code = ''.join(random.choice(characters) for i in range(length))
        existing_user = db.execute("SELECT id FROM users WHERE code = ?", (code,)).fetchone()
        if not existing_user:
            return code

def generate_game_board_data_for_display(all_active_users_for_board):
    """
    Generates data structure for rendering the game board on the client.
    Takes a list of active users with id, name, and rating.
    """
    board_config_json = get_setting('game_board_config')
    board_config = json.loads(board_config_json) if board_config_json else {"pole_images": GAME_BOARD_POLE_IMAGES, "num_cells": DEFAULT_NUM_BOARD_CELLS}

    pole_images = board_config.get("pole_images", GAME_BOARD_POLE_IMAGES)
    num_cells = board_config.get("num_cells", DEFAULT_NUM_BOARD_CELLS)

    # Update the global variable for the number of cells
    global _current_game_board_num_cells
    _current_game_board_num_cells = num_cells
    global _current_game_board_pole_image_config
    # Убедимся, что путь формируется корректно для статических файлов
    _current_game_board_pole_image_config = [f"{GAME_BOARD_POLE_IMG_SUBFOLDER}/{img}" for img in pole_images]


    board_data = []
    # Create cells with numbers and pictogram paths
    for i in range(1, num_cells + 1):
        image_path = ""
        # Assign pictogram based on cell number (simple example)
        if i > 0 and i <= len(_current_game_board_pole_image_config):
             image_path = _current_game_board_pole_image_config[i-1]
        # else: # Optionally assign a default image for other cells
        #     image_path = os.path.join(GAME_BOARD_POLE_IMG_SUBFOLDER, "default_cell.jpg")


        board_data.append({
            'cell_number': i,
            'image_path': image_path,
            'users_in_cell': [] # List of users currently in this cell
        })

    # Place active users on the board based on their rating
    for user in all_active_users_for_board:
        # Assuming rating directly corresponds to cell number (1-based)
        # Ensure rating is within valid cell range
        cell_number = max(1, min(user['rating'], num_cells)) # Clamp rating to board range

        # Find the corresponding cell in board_data and add the user
        # Note: in-memory representation, doesn't modify DB
        for cell in board_data:
            if cell['cell_number'] == cell_number:
                # Add a simplified user object to the cell
                cell['users_in_cell'].append({'id': user['id'], 'name': user['name'], 'rating': user['rating']})
                break # Found the cell, move to next user

    return board_data

def initialize_new_game_board_visuals(users_for_board_config):
    """
    Initializes the game board visuals configuration based on settings.
    Called on app startup to load config and potentially for game state update.
    Takes list of users to generate board data immediately.
    """
    db = get_db()
    board_config_json = get_setting('game_board_config')
    board_config = json.loads(board_config_json) if board_config_json else {"pole_images": GAME_BOARD_POLE_IMAGES, "num_cells": DEFAULT_NUM_BOARD_CELLS}

    global _current_game_board_pole_image_config
    _current_game_board_pole_image_config = [f"{GAME_BOARD_POLE_IMG_SUBFOLDER}/{img}" for img in board_config.get("pole_images", GAME_BOARD_POLE_IMAGES)]

    global _current_game_board_num_cells
    _current_game_board_num_cells = board_config.get("num_cells", DEFAULT_NUM_BOARD_CELLS)

    print(f"Game board visuals initialized. Cells: {_current_game_board_num_cells}, Pictograms: {len(_current_game_board_pole_image_config)}", file=sys.stderr)
    # No DB commit needed here, just updating in-memory config

def get_full_game_state_data(user_code_for_state=None):
    db = get_db()
    current_g_user_dict = None
    if user_code_for_state:
        user_row = db.execute("SELECT id, name, code, rating, status FROM users WHERE code = ?", (user_code_for_state,)).fetchone()
        if user_row:
            current_g_user_dict = dict(user_row)

    active_subfolder_val = get_setting('active_subfolder')

    # --- ДОБАВЛЕНО: Получаем данные обо ВСЕХ пользователях для поиска имен ---
    all_users_info_db = db.execute("SELECT id, name FROM users").fetchall()
    all_users_info_list = [{'id': u['id'], 'name': u['name']} for u in all_users_info_db]
    # --- КОНЕЦ ДОБАВЛЕННОГО БЛОКА ---

    game_state = {
        'game_in_progress': is_game_in_progress(), 'game_over': is_game_over(),
        'show_card_info': get_setting("show_card_info") == "true",
        'active_subfolder': active_subfolder_val, 'db_current_leader_id': get_leading_user_id(),
        'num_active_players': get_active_players_count(db),
        'table_images': [], 'user_cards': [],
        'all_users_for_guessing': [], # Этот список для выпадающего списка угадывания (активные игроки с картами на столе)
        'all_users_info': all_users_info_list, # Список со всеми пользователями для поиска имен
        'on_table_status': False, 'is_current_user_the_db_leader': False,
        'leader_pole_pictogram_path': None, 'leader_pictogram_rating_display': None,
        'game_board': [], 'current_num_board_cells': _current_game_board_num_cells,
        'current_user_data': current_g_user_dict, 'num_cards_on_table': 0,
        'all_cards_placed_for_guessing_phase_to_template': False,
        'flashed_messages': [] # Flash сообщения не передаются через SocketIO таким образом
    }

    # Проверяем, есть ли активная подпапка перед запросом к images
    raw_table_cards = []
    if active_subfolder_val:
        raw_table_cards = db.execute("SELECT i.id, i.image, i.subfolder, i.owner_id, u.name as owner_name, i.guesses FROM images i LEFT JOIN users u ON i.owner_id = u.id WHERE i.subfolder = ? AND i.status LIKE 'На столе:%'", (active_subfolder_val,)).fetchall()

    game_state['num_cards_on_table'] = len(raw_table_cards)

    if game_state['game_in_progress'] and not game_state['game_over']:
        # Проверяем, все ли карты выложены для фазы угадывания
        # Количество активных игроков, которые должны были выложить карту (все активные, кроме, возможно, ведущего, если он первый и еще не выложил)
        # Более точная проверка: количество карт на столе == количество активных игроков
        game_state['all_cards_placed_for_guessing_phase_to_template'] = (game_state['num_active_players'] > 1 and game_state['num_cards_on_table'] == game_state['num_active_players'])


        for card_row in raw_table_cards:
            guesses_data = json.loads(card_row['guesses'] or '{}')
            my_guess_val = None
            # Если текущий пользователь активен, в фазе угадывания, и не владелец карточки - показываем его выбор
            if current_g_user_dict and current_g_user_dict['status'] == 'active' and \
               game_state['all_cards_placed_for_guessing_phase_to_template'] and \
               not game_state['show_card_info'] and card_row['owner_id'] != current_g_user_dict['id']:
                my_guess_val = guesses_data.get(str(current_g_user_dict['id']))

            game_state['table_images'].append({
                'id': card_row['id'],
                'image': card_row['image'],
                'subfolder': card_row['subfolder'],
                'owner_id': card_row['owner_id'],
                'owner_name': get_user_name(card_row['owner_id']) or "N/A", # Используем функцию для получения имени
                'guesses': guesses_data,
                'my_guess_for_this_card_value': my_guess_val
            })

        # Получаем карты на руках текущего активного пользователя
        if current_g_user_dict and current_g_user_dict['status'] == 'active' and active_subfolder_val:
            user_cards_db = db.execute("SELECT id, image, subfolder FROM images WHERE owner_id = ? AND subfolder = ? AND status LIKE 'Занято:%'", (current_g_user_dict['id'], active_subfolder_val)).fetchall()
            game_state['user_cards'] = [{'id': r['id'], 'image': r['image'], 'subfolder': r['subfolder']} for r in user_cards_db]

            # Проверяем, выложил ли текущий пользователь карточку на стол
            if any(tc['owner_id'] == current_g_user_dict['id'] for tc in game_state['table_images']):
                game_state['on_table_status'] = True

            # Наполняем список all_users_for_guessing активными игроками, у которых есть карточка на столе
            # Этот список используется для выпадающего списка угадывания
            all_active_users_db = db.execute("SELECT id, name FROM users WHERE status = 'active'").fetchall()
            active_users_with_card_on_table = [
                u for u in all_active_users_db
                if any(card['owner_id'] == u['id'] for card in game_state['table_images'])
            ]
            game_state['all_users_for_guessing'] = [{'id': u['id'], 'name': u['name']} for u in active_users_with_card_on_table]

            if game_state['db_current_leader_id'] is not None:
                game_state['is_current_user_the_db_leader'] = (current_g_user_dict['id'] == game_state['db_current_leader_id'])

            # Логика пиктограммы ведущего (только для активного ведущего, до выкладывания карты)
            if game_state['is_current_user_the_db_leader'] and not game_state['on_table_status'] and \
               not game_state['show_card_info'] and not game_state['all_cards_placed_for_guessing_phase_to_template']:
                leader_rating = int(current_g_user_dict.get('rating', 0))
                game_state['leader_pictogram_rating_display'] = leader_rating
                # Убедимся, что индекс не выходит за пределы списка пиктограмм
                if leader_rating > 0 and _current_game_board_pole_image_config and (leader_rating - 1) < len(_current_game_board_pole_image_config):
                    game_state['leader_pole_pictogram_path'] = _current_game_board_pole_image_config[leader_rating - 1]


    elif game_state['show_card_info']:
        # Если игра не в процессе или окончена, но карты показаны (фаза подсчета/просмотра)
        for card_row in raw_table_cards:
             guesses_data = json.loads(card_row['guesses'] or '{}')
             game_state['table_images'].append({
                 'id': card_row['id'],
                 'image': card_row['image'],
                 'subfolder': card_row['subfolder'],
                 'owner_id': card_row['owner_id'],
                 'owner_name': get_user_name(card_row['owner_id']) or "N/A", # Используем функцию для получения имени
                 'guesses': guesses_data,
                 'my_guess_for_this_card_value': None # В этой фазе угадывание неактивно для текущего пользователя
             })
        # Список all_users_for_guessing не нужен в этой фазе

    # Всегда получаем данные для игрового поля на основе активных пользователей
    all_active_users_for_board = db.execute("SELECT id, name, rating FROM users WHERE status = 'active'").fetchall()
    game_board_data = generate_game_board_data_for_display(all_active_users_for_board)
    game_state['current_num_board_cells'] = _current_game_board_num_cells

    return game_state


# --- ДОБАВЛЕНО: Новая функция для выполнения подсчета очков и обновления поля ---
def perform_round_scoring(db):
    """
    Выполняет подсчет очков за раунд, обновляет положение игроков на игровом поле
    и сбрасывает состояние раунда.
    Принимает объект подключения к базе данных (db).
    ВНИМАНИЕ: Эта функция должна выполняться внутри транзакции!
    """
    print("Executing perform_round_scoring...", file=sys.stderr)

    # --- СЮДА ПЕРЕНЕСЕНА ЛОГИКА ПОДСЧЕТА ОЧКОВ И ОБНОВЛЕНИЯ ПОЛЯ ИЗ admin_open_cards ---
    # Основано на правилах, которые вы предоставили.
    active_subfolder = get_setting('active_subfolder')
    all_table_images = db.execute("SELECT id, owner_id, guesses FROM images WHERE status LIKE 'На столе:%' AND subfolder = ?", (active_subfolder,)).fetchall()
    active_users = db.execute("SELECT id, name, rating FROM users WHERE status = 'active'").fetchall()
    current_leader_id = get_leading_user_id()

    print("Calculating scores based on rules...", file=sys.stderr)
    scores = {user['id']: 0 for user in active_users} # Словарь для хранения набранных очков в этом раунде

    leader_card = next((card for card in all_table_images if card['owner_id'] == current_leader_id), None)

    if leader_card:
        leader_card_guesses = json.loads(leader_card['guesses'] or '{}')
        # Игроки, которые должны были угадывать (активные, не ведущий, выложили карту)
        guesser_ids_who_should_guess = {u['id'] for u in active_users if u['id'] != current_leader_id and any(card['owner_id'] == u['id'] for card in all_table_images)}

        # Игроки, которые правильно угадали карточку ведущего
        guesser_ids_who_guessed_leader = {int(guesser_id_str) for guesser_id_str, guessed_target_id in leader_card_guesses.items()
                                           if int(guessed_target_id) == current_leader_id and int(guesser_id_str) in guesser_ids_who_should_guess}

        num_who_guessed_leader_card = len(guesser_ids_who_guessed_leader)
        num_players_who_should_guess = len(guesser_ids_who_should_guess)


        # Логика для Ведущего
        if current_leader_id in scores: # Убедимся, что ведущий активен
             if num_players_who_should_guess > 0: # Учитываем только если есть игроки, которые могли угадывать
                 if num_who_guessed_leader_card == num_players_who_should_guess: # Если ведущего угадали ВСЕ, КТО ДОЛЖЕН БЫЛ УГАДАТЬ
                     # Правило: Ведущий идет назад на 3 хода (или на поле 1)
                     print(f"Rule 1: Leader ({get_user_name(current_leader_id)}) guessed by all who should guess. Moving back.", file=sys.stderr)
                     current_leader_rating = next((user['rating'] for user in active_users if user['id'] == current_leader_id), 0)
                     new_rating = max(1, current_leader_rating - 3)
                     db.execute("UPDATE users SET rating = ? WHERE id = ?", (new_rating, current_leader_id))
                     print(f"Leader new rating: {new_rating}", file=sys.stderr)
                     # Остальные игроки стоят на месте - их рейтинги не меняются в этом условии.

                 elif num_who_guessed_leader_card == 0: # Если ведущего НЕ угадал НИКТО, КТО ДОЛЖЕН БЫЛ УГАДЫВАТЬ
                     # Правило: Ведущий идет назад на 2 хода
                     print(f"Rule 2: Leader ({get_user_name(current_leader_id)}) not guessed by anyone who should guess. Moving back.", file=sys.stderr)
                     current_leader_rating = next((user['rating'] for user in active_users if user['id'] == current_leader_id), 0)
                     new_rating = max(1, current_leader_rating - 2)
                     db.execute("UPDATE users SET rating = ? WHERE id = ?", (new_rating, current_leader_id))
                     print(f"Leader new rating: {new_rating}", file=sys.stderr)

                     # Плюс, очки получают игроки, чьи карточки угадали, по одному очку за каждого угадавшего их карту.
                     for card in all_table_images:
                         if card['owner_id'] != current_leader_id: # Рассматриваем только карточки игроков
                             card_guesses = json.loads(card['guesses'] or '{}')
                             num_who_guessed_this_card_correctly = 0
                             # Кто угадал именно владельца этой карточки (среди тех, кто должен был угадывать)?
                             for guesser_id_str, guessed_target_id in card_guesses.items():
                                 guesser_id = int(guesser_id_str)
                                 if guesser_id in guesser_ids_who_should_guess and int(guessed_target_id) == card['owner_id']:
                                      num_who_guessed_this_card_correctly += 1

                             # Очки игроку, чью карточку угадали (получает по 1 очку за каждого, кто угадал его карту)
                             if card['owner_id'] in scores and num_who_guessed_this_card_correctly > 0:
                                  scores[card['owner_id']] += num_who_guessed_this_card_correctly
                                  print(f"Rule 2: Card of {get_user_name(card['owner_id'])} guessed by {num_who_guessed_this_card_correctly} players. Owner gains {num_who_guessed_this_card_correctly} points.", file=sys.stderr)
                     # Обновляем рейтинги игроков (которые не ведущий), если они набрали очки
                     for user_id, round_score in scores.items():
                          if user_id != current_leader_id and round_score > 0:
                              current_rating_db_row = db.execute("SELECT rating FROM users WHERE id = ?", (user_id,)).fetchone()
                              if current_rating_db_row:
                                  current_rating_db = current_rating_db_row['rating']
                                  new_rating = current_rating_db + round_score
                                  new_rating = max(1, new_rating)
                                  db.execute("UPDATE users SET rating = ? WHERE id = ?", (new_rating, user_id))
                                  print(f"User {get_user_name(user_id)} gained {round_score} points. New rating: {new_rating}", file=sys.stderr)


                 else: # В любом другом случае (угадал кто-то, но не все и не никто из тех, кто должен был угадывать)
                     print("Rule 3: Mixed guesses for leader. Calculating points...", file=sys.stderr)
                     # а) по 3 очка получают все игроки, правильно угадавшие карточку Ведущего.
                     for guesser_id in guesser_ids_who_guessed_leader:
                          if guesser_id in scores:
                              scores[guesser_id] += 3
                              print(f"Rule 3a: User {get_user_name(guesser_id)} correctly guessed leader. Gains 3 points.", file=sys.stderr)

                     # Ведущий получает 3 очка плюс по очку за каждого угадавшего его карточку игрока.
                     if current_leader_id in scores:
                          leader_points = 3 + num_who_guessed_leader_card
                          scores[current_leader_id] += leader_points
                          print(f"Rule 3a: Leader ({get_user_name(current_leader_id)}) gains {leader_points} points (3 + {num_who_guessed_leader_card}).", file=sys.stderr)

                     # б) Все игроки получают по одному очку за каждого игрока, который угадал их карточку.
                     for card in all_table_images:
                         if card['owner_id'] != current_leader_id: # Рассматриваем только карточки игроков
                             card_guesses = json.loads(card['guesses'] or '{}')
                             num_who_guessed_this_card_correctly = 0
                             # Кто угадал именно владельца этой карточки (среди тех, кто должен был угадывать)?
                             for guesser_id_str, guessed_target_id in card_guesses.items():
                                  guesser_id = int(guesser_id_str)
                                  if guesser_id in guesser_ids_who_should_guess and int(guessed_target_id) == card['owner_id']:
                                      num_who_guessed_this_card_correctly += 1

                             # Очки игроку, чью карточку угадали (получает по 1 очку за каждого, кто угадал его карту)
                             if card['owner_id'] in scores and num_who_guessed_this_card_correctly > 0:
                                  scores[card['owner_id']] += num_who_guessed_this_card_correctly
                                  print(f"Rule 3b: Card of {get_user_name(card['owner_id'])} guessed by {num_who_guessed_this_card_correctly} players. Owner gains {num_who_guessed_this_card_correctly} points.", file=sys.stderr)

                     # Обновляем рейтинги всех активных игроков на основе набранных в раунде очков
                     for user_id, round_score in scores.items():
                          current_rating_db_row = db.execute("SELECT rating FROM users WHERE id = ?", (user_id,)).fetchone()
                          if current_rating_db_row:
                              current_rating_db = current_rating_db_row['rating']
                              new_rating = current_rating_db + round_score
                              new_rating = max(1, new_rating)
                              db.execute("UPDATE users SET rating = ? WHERE id = ?", (new_rating, user_id))
                              if round_score > 0:
                                  print(f"User {get_user_name(user_id)} gained {round_score} points. New rating: {new_rating}", file=sys.stderr)
                              elif round_score == 0:
                                  print(f"User {get_user_name(user_id)} rating remains {new_rating}.", file=sys.stderr)
             else:
                  # Случай, когда нет игроков, которые должны были угадывать (например, только Ведущий активен и выложил карту)
                  print("No players who should guess. Skipping guess-based scoring.", file=sys.stderr)

    else:
        print("Leader card not found on the table. Skipping guess-based scoring logic.", file=sys.stderr)

    # --- КОНЕЦ ЛОГИКИ ПОДСЧЕТА ОЧКОВ ---


    # --- Сброс состояния раунда после подсчета очков ---
    print("Resetting round state...", file=sys.stderr)
    # Удаляем карточки со стола
    db.execute("DELETE FROM images WHERE status LIKE 'На столе:%' AND subfolder = ?", (active_subfolder,))

    # Логика выдачи новых карт (если необходимо по правилам)
    # Сбрасываем статус всех карточек в активной колоде в 'Свободно' и удаляем владельцев
    db.execute("UPDATE images SET owner_id = NULL, status = 'Свободно', guesses = NULL WHERE subfolder = ?", (active_subfolder,))

    # Выдаем новые карты (пример: по 6 каждому активному игроку, если они могут получить карты)
    # Проверяем количество свободных карт
    num_free_cards = db.execute("SELECT COUNT(*) FROM images WHERE subfolder = ? AND status = 'Свободно'", (active_subfolder,)).fetchone()[0]
    active_users_list = db.execute("SELECT id FROM users WHERE status = 'active'").fetchall()
    cards_per_player = 6 # Количество карт на игрока
    total_cards_to_distribute = len(active_users_list) * cards_per_player

    if num_free_cards >= total_cards_to_distribute:
         print(f"Distributing {cards_per_player} new cards to {len(active_users_list)} active players.", file=sys.stderr)
         free_card_ids = [r['id'] for r in db.execute("SELECT id FROM images WHERE subfolder = ? AND status = 'Свободно'", (active_subfolder,)).fetchall()]
         random.shuffle(free_card_ids) # Перемешиваем

         card_index = 0
         for user in active_users_list:
             for i in range(cards_per_player):
                 if card_index < len(free_card_ids):
                     card_id = free_card_ids[card_index]
                     # Выдаем карту пользователю
                     db.execute("UPDATE images SET owner_id = ?, status = ? WHERE id = ?", (user['id'], f'Занято:{user["id"]}', card_id))
                     card_index += 1
                 else:
                     print(f"Not enough free cards during distribution.", file=sys.stderr)
                     break # Недостаточно карт
             if card_index == len(free_card_ids): # Если свободные карты закончились до выдачи всем
                 print("Ran out of free cards during distribution.", file=sys.stderr)
                 break
    else:
         print(f"Not enough free cards ({num_free_cards}) to distribute {cards_per_player} per player for {len(active_users_list)} players ({total_cards_to_distribute} needed). Skipping card distribution.", file=sys.stderr)
         # Возможно, здесь нужно реализовать логику завершения игры или сообщение об ошибке


    # Выбор следующего ведущего (пример: следующий по ID среди активных)
    active_user_ids = [u['id'] for u in db.execute("SELECT id FROM users WHERE status = 'active' ORDER BY id").fetchall()]
    next_leader_id = None
    current_leader_id = get_leading_user_id() # Получаем ID текущего ведущего еще раз
    if active_user_ids:
        if current_leader_id is not None and current_leader_id in active_user_ids:
            current_leader_index = active_user_ids.index(current_leader_id)
            next_leader_index = (current_leader_index + 1) % len(active_user_ids)
            next_leader_id = active_user_ids[next_leader_index]
            print(f"Current leader {get_user_name(current_leader_id)}, Next leader ID: {next_leader_id}", file=sys.stderr)
        elif active_user_ids: # Если текущий ведущий не активен или не найден
             next_leader_id = active_user_ids[0] # Выбираем первого активного по ID
             print(f"Current leader not found or not active. Selecting first active user ID {next_leader_id} as next leader.", file=sys.stderr)
        else:
             next_leader_id = None # Нет активных игроков
             print("No active users to select a next leader.", file=sys.stderr)
    else:
        next_leader_id = None # Нет активных игроков
        print("No active users to select a next leader.", file=syserr)


    set_setting('current_leader_id', str(next_leader_id) if next_leader_id is not None else None)
    set_setting('show_card_info', 'false') # Сбрасываем флаг показа карт


    # Проверяем условие завершения игры (например, кто-то достиг конца поля)
    # Если игра завершена, установить game_over = 'true'
    # Пример:
    winning_rating = _current_game_board_num_cells # Или другое условие для победы
    game_over_flag = False # Локальный флаг завершения игры в раунде
    for user in active_users:
        user_data_for_check = db.execute("SELECT id, rating, name FROM users WHERE id = ?", (user['id'],)).fetchone()
        if user_data_for_check and user_data_for_check['rating'] >= winning_rating:
            set_setting('game_over', 'true')
            set_setting('game_in_progress', 'false') # Игра окончена
            game_over_flag = True
            print(f"Game Over! Winner: {user_data_for_check['name']}", file=sys.stderr)
            flash(f"Игра окончена! Победитель: {user_data_for_check['name']}", "info")
            # Можно добавить логику для записи победителя и завершения игры полностью
            break # Игра окончена, выходим из цикла проверки

    print("Round state reset finished.", file=sys.stderr)
    # --- КОНЕЦ СБРОСА СОСТОЯНИЯ РАУНДА ---

# --- КОНЕЦ ДОБАВЛЕННОЙ ФУНКЦИИ ---


# --- Маршруты ---

@app.route("/")
def index():
    db = get_db()
    # Получаем список подпапок с карточками
    card_subfolders_base = os.path.join(app.static_folder, 'images')
    # Проверяем существование папки и фильтруем
    card_subfolders = []
    if os.path.exists(card_subfolders_base):
        card_subfolders = [name for name in os.listdir(card_subfolders_base)
                           if os.path.isdir(os.path.join(card_subfolders_base, name))
                           and name != GAME_BOARD_POLE_IMG_SUBFOLDER]

    active_subfolder = get_setting('active_subfolder')
    game_in_progress = is_game_in_progress()
    game_over = is_game_over()
    current_leader_id = get_leading_user_id()
    current_leader_name = get_user_name(current_leader_id) if current_leader_id is not None else "Не определен"

    # Получаем список активных игроков для отображения на главной
    active_users = db.execute("SELECT id, name, rating FROM users WHERE status = 'active' ORDER BY rating DESC").fetchall() # Изменено для получения id
    all_users_for_board = db.execute("SELECT id, name, rating FROM users").fetchall() # Получаем всех для отображения на поле главной

    # Получаем данные игрового поля для отображения на главной
    game_board_data = generate_game_board_data_for_display(all_users_for_board) # Передаем всех пользователей

    return render_template('index.html',
                           card_subfolders=card_subfolders,
                           active_subfolder=active_subfolder,
                           game_in_progress=game_in_progress,
                           game_over=game_over,
                           current_leader_name=current_leader_name,
                           active_users=active_users,
                           game_board=game_board_data, # Передаем данные поля
                           game_board_num_cells=_current_game_board_num_cells # Передаем количество клеток
                          )


@app.route("/create_user", methods=["POST"])
def create_user():
    db = get_db()
    user_name = request.form.get('user_name').strip()
    if not user_name:
        flash("Имя пользователя не может быть пустым.", "warning")
        return redirect(url_for('index'))

    # Проверяем, не занято ли имя
    existing_user = db.execute("SELECT id FROM users WHERE name = ?", (user_name,)).fetchone()
    if existing_user:
        flash(f"Имя пользователя '{user_name}' уже занято.", "warning")
        return redirect(url_for('index'))

    user_code = generate_user_code()
    # Устанавливаем статус 'pending' при создании, станет 'active' при начале игры
    db.execute("INSERT INTO users (name, code, status, rating) VALUES (?, ?, ?, ?)", (user_name, user_code, 'pending', 0))
    db.commit()

    # Автоматический логин пользователя после создания
    session['user_code'] = user_code

    flash(f"Пользователь '{user_name}' успешно создан! Ваш код: {user_code}", "success")
    return redirect(url_for('user', user_code=user_code))

@app.route("/user/<user_code>")
def user(user_code):
    db = get_db()
    user_data = db.execute("SELECT id, name, code, rating, status FROM users WHERE code = ?", (user_code,)).fetchone()

    if user_data:
        # Логика для запоминания пользователя в сессии, если он пришел по ссылке
        if session.get('user_code') != user_code:
             session['user_code'] = user_code
             flash(f"Вы вошли как {user_data['name']}.", "info")

        # Получаем начальное состояние игры для первой отрисовки
        # get_full_game_state_data включает все необходимые данные, включая статус пользователя и all_users_info
        initial_game_state = get_full_game_state_data(user_code_for_state=user_code)
        return render_template('user.html', user_data_for_init=user_data, initial_game_state_json=json.dumps(initial_game_state))
    else:
        flash("Пользователь с таким кодом не найден.", "danger")
        # Если пользователь не найден по коду в URL, и в сессии тоже нет кода, редирект на главную
        if not session.get('user_code'):
             return redirect(url_for('index'))
        else:
             # Если в сессии есть код, но пользователь по коду из URL не найден (ошибка в URL?),
             # попробовать редирект на страницу пользователя из сессии.
             print(f"User code in URL '{user_code}' not found, but session user_code '{session['user_code']}' exists. Redirecting to session user page.", file=sys.stderr)
             return redirect(url_for('user', user_code=session['user_code']))


@app.route("/user/<user_code>/place/<image_id>", methods=["POST"])
def place_card(user_code, image_id):
    db = get_db()
    user = db.execute("SELECT id, name, code, status FROM users WHERE code = ?", (user_code,)).fetchone()

    if not user:
        flash("Пользователь не найден.", "danger")
        return redirect(url_for('index'))

    if user['status'] != 'active':
        flash("Только активные игроки могут выкладывать карточки.", "warning")
        return redirect(url_for('user', user_code=user_code))

    if not is_game_in_progress() or is_game_over():
         flash("Сейчас нельзя выкладывать карточки.", "warning")
         return redirect(url_for('user', user_code=user_code))

    # Проверяем, является ли пользователь текущим ведущим
    current_leader_id = get_leading_user_id()
    is_current_user_leader = (user['id'] == current_leader_id)

    # Проверяем, выложил ли уже пользователь карточку в этом раунде
    existing_card_on_table = db.execute("SELECT id FROM images WHERE owner_id = ? AND status LIKE 'На столе:%'", (user['id'],)).fetchone()
    if existing_card_on_table:
        flash("Вы уже выложили карточку в этом раунде.", "warning")
        return redirect(url_for('user', user_code=user_code))

    # Проверяем, что карточка с данным image_id принадлежит пользователю и находится у него на руках
    card_to_place = db.execute("SELECT id, status FROM images WHERE id = ? AND owner_id = ? AND status LIKE 'Занято:%'", (image_id, user['id'])).fetchone()

    if not card_to_place:
        flash("Эта карточка не у вас на руках.", "warning")
        return redirect(url_for('user', user_code=user_code))

    # Получаем активную подпапку
    active_subfolder = get_setting('active_subfolder')
    if not active_subfolder:
         flash("Не выбрана активная колода карточек.", "danger")
         return redirect(url_for('user', user_code=user_code))


    db.execute("BEGIN") # Начинаем транзакцию

    try:
        # Если текущий пользователь - ведущий
        if is_current_user_leader:
            # Устанавливаем статус "На столе:Ведущий" для его карточки
            db.execute("UPDATE images SET status = 'На столе:Ведущий' WHERE id = ?", (card_to_place['id'],))
            flash("Вы выложили карточку ведущего.", "success")
        else:
            # Устанавливаем статус "На столе:Игрок" для карточек остальных игроков
            db.execute("UPDATE images SET status = 'На столе:Игрок' WHERE id = ?", (card_to_place['id'],))
            flash("Вы выложили свою карточку.", "success")

        db.commit() # Фиксируем изменения

        # Проверяем, все ли активные игроки выложили по одной карточке
        # Проверка: количество карт на столе == количество активных игроков
        num_active_players = get_active_players_count(db)
        num_cards_on_table = db.execute("SELECT COUNT(*) FROM images WHERE status LIKE 'На столе:%' AND subfolder = ?", (active_subfolder,)).fetchone()[0]

        # Если все активные игроки выложили карточки (и их больше одного для фазы угадывания)
        if num_active_players > 1 and num_cards_on_table == num_active_players:
             if not is_game_over():
                  print("All active players placed cards. Entering guessing phase.", file=sys.stderr)
                  # Фаза угадывания начинается автоматически после game_update
                  # Флаг show_card_info остается false на этом этапе
        elif num_active_players == 1 and num_cards_on_table == 1 and is_current_user_leader:
             # Если активен только ведущий и он выложил карту, это может быть игра для 1 игрока или ожидание других.
             # Здесь можно добавить логику, если игра с 1 игроком имеет особый флоу.
             pass


    except Exception as e:
        db.rollback() # Откатываем изменения при ошибке
        print(f"Error placing card for user {user['code']}: {e}", file=sys.stderr)
        traceback.print_exc()
        flash(f"Произошла ошибка при выкладывании карточки: {e}", "danger")


    # Отправляем обновление состояния игры всем подключенным клиентам
    socketio.emit('game_update', get_full_game_state_data(user_code_for_state=user_code), broadcast=True)

    return redirect(url_for('user', user_code=user_code))


@app.route('/user/<user_code>/guess/<image_id>', methods=['POST'])
def guess_card(user_code, image_id):
    db = get_db()
    user = db.execute("SELECT id, name, code, status FROM users WHERE code = ?", (user_code,)).fetchone()

    if not user:
        flash("Пользователь не найден.", "danger")
        return redirect(url_for('index'))

    if user['status'] != 'active':
        flash("Только активные игроки могут угадывать.", "warning")
        return redirect(url_for('user', user_code=user_code))

    # Разрешаем угадывать только если игра идет, не окончена, и карты ЕЩЕ не открыты
    if not is_game_in_progress() or is_game_over() or get_setting("show_card_info") == "true":
        flash("Сейчас нельзя угадывать карточки.", "warning")
        return redirect(url_for('user', user_code=user_code))

    guessed_user_id = request.form.get('guessed_user_id')
    if not guessed_user_id:
        flash("Выберите игрока, чью карточку вы угадываете.", "warning")
        return redirect(url_for('user', user_code=user_code))

    try:
        guessed_user_id = int(guessed_user_id)
    except ValueError:
        flash("Некорректный ID игрока.", "danger")
        return redirect(url_for('user', user_code=user_code))

    # Убедимся, что карточка существует и находится на столе
    active_subfolder = get_setting('active_subfolder')
    image_on_table = db.execute("SELECT id, owner_id, guesses FROM images WHERE status LIKE 'На столе:%' AND subfolder = ?", (image_id, active_subfolder)).fetchone()
    if not image_on_table:
        flash("Карточка не найдена на столе.", "danger")
        return redirect(url_for('user', user_code=user_code))

    # Убедимся, что пользователь не угадывает свою собственную карточку
    if image_on_table['owner_id'] == user['id']:
        flash("Вы не можете угадывать свою собственную карточку.", "warning")
        return redirect(url_for('user', user_code=user_code))

    db.execute("BEGIN") # Начинаем транзакцию

    try:
        # Обновляем угадывание для этой карточки в базе данных
        current_guesses = json.loads(image_on_table['guesses'] or '{}')
        current_guesses[str(user['id'])] = guessed_user_id # Сохраняем ID угадывающего и ID того, кого угадали

        db.execute("UPDATE images SET guesses = ? WHERE id = ?", (json.dumps(current_guesses), image_on_table['id']))
        # Commit будет позже, после проверки необходимости подсчета очков

        flash(f"Ваш выбор за карточку принят.", "success")

        # Проверяем, все ли необходимые угадывания сделаны
        num_active_players = get_active_players_count(db)
        num_cards_on_table = db.execute("SELECT COUNT(*) FROM images WHERE status LIKE 'На столе:%' AND subfolder = ?", (active_subfolder,)).fetchone()[0]
        all_table_images = db.execute("SELECT id, owner_id, guesses FROM images WHERE status LIKE 'На столе:%' AND subfolder = ?", (active_subfolder,)).fetchall()

        # Ищем ID активных игроков, у которых есть карточка на столе.
        # Только эти игроки должны делать угадывания на ВСЕХ чужих карточках.
        # Если Ведущий единственный активный игрок с картой на столе, фазы угадывания не должно быть.
        active_users_with_card_on_table_ids = [u['id'] for u in db.execute("SELECT id FROM users WHERE status = 'active'").fetchall() if db.execute("SELECT COUNT(*) FROM images WHERE owner_id = ? AND status LIKE 'На столе:%'", (u['id'],)).fetchone()[0] > 0]

        all_guesses_submitted = True
        # Проверяем, есть ли вообще игроки, которые должны угадывать (минимум 2 активных игрока, оба выложили карты)
        if len(active_users_with_card_on_table_ids) >= 2 and num_cards_on_table >= len(active_users_with_card_on_table_ids):
             for active_user_id in active_users_with_card_on_table_ids:
                 # Проверяем, сделал ли этот активный игрок угадывание для каждой карточки на столе, КРОМЕ СВОЕЙ
                 cards_to_guess_on = [img for img in all_table_images if img['owner_id'] != active_user_id]

                 # Условие для завершения угадывания: каждый из active_users_with_card_on_table_ids
                 # должен угадать каждую из cards_to_guess_on
                 # Убедимся, что количество карт для угадывания соответствует ожидаемому (все карты на столе - 1 своя)
                 expected_cards_to_guess = num_cards_on_table - (1 if active_user_id in [c['owner_id'] for c in all_table_images] else 0)
                 if len(cards_to_guess_on) != expected_cards_to_guess:
                     # Эта ситуация не должна происходить при нормальном ходе игры, если все выложили карты.
                     # Если происходит, возможно, игра в некорректном состоянии, и автоматический подсчет не должен запускаться.
                      all_guesses_submitted = False
                      print(f"User {get_user_name(active_user_id)} expected to guess on {expected_cards_to_guess} cards, but found {len(cards_to_guess_on)} cards to guess on.", file=sys.stderr)
                      break # Выходим, так как не все условия выполнены для этого игрока


                 for card_to_guess in cards_to_guess_on:
                     card_guesses = json.loads(card_to_guess['guesses'] or '{}')
                     if str(active_user_id) not in card_guesses:
                         all_guesses_submitted = False
                         break # Нет смысла проверять другие карточки этого игрока
                 if not all_guesses_submitted:
                     break # Нет смысла проверять других игроков
        else:
             # Если игроков с картами меньше 2, или карт меньше, чем игроков с картами,
             # фаза угадывания "всеми на все" не может быть завершена.
             all_guesses_submitted = False


        print(f"Guess submitted by {user['name']}. Active players with cards: {len(active_users_with_card_on_table_ids)}. Cards on table: {num_cards_on_table}. All guesses submitted (logic check): {all_guesses_submitted}", file=sys.stderr)


        # Если все необходимые угадывания сделаны, запускаем подсчет очков
        # Дополнительная проверка: если активен только ведущий и он выложил карту (игра 1 на 1 без угадывания?), не запускаем подсчет
        is_single_player_game = (num_active_players == 1 and num_cards_on_table == 1 and get_leading_user_id() == user['id'])

        if all_guesses_submitted and not is_single_player_game:
            print("All required guesses submitted. Triggering scoring...", file=sys.stderr)
            try:
                # Устанавливаем флаг, чтобы показать карточки
                set_setting("show_card_info", "true")
                # Фиксируем в базе данных установку флага и последнее сделанное угадывание
                # Commit будет после perform_round_scoring для включения результатов подсчета

                # --- ИЗМЕНЕНО: Вызов новой функции для подсчета очков ---
                perform_round_scoring(db) # Вызываем отдельную функцию для логики подсчета
                # --- КОНЕЦ ИЗМЕНЕННОГО ---

                # Фиксируем результаты подсчета очков, обновления поля и установку флага show_card_info
                db.commit()

                flash("Все угадали! Карточки открыты, очки подсчитаны.", "success")
            except Exception as e:
                db.rollback() # Откатываем изменения при ошибке
                print(f"Error during automatic scoring: {e}", file=sys.stderr)
                traceback.print_exc()
                flash(f"Произошла ошибка при автоматическом подсчете очков: {e}", "danger")
                # В случае ошибки откатываем и сбрасываем флаг show_card_info, чтобы игра не зависла
                try:
                     # ИСПРАВЛЕНО: Синтаксическая ошибка здесь
                     set_setting("show_card_info", "false")
                     db.commit() # Фиксируем сброс флага
                     socketio.emit('game_update', get_full_game_state_data(), broadcast=True)
                except Exception as inner_e:
                     print(f"Error during rollback and setting show_card_info to false after automatic scoring error: {inner_e}", file=sys.stderr)
        else:
            # Если не все угадывания сделаны, или это игра 1 на 1, просто фиксируем текущее угадывание
            db.commit()

    except Exception as e:
        db.rollback() # Откатываем всю транзакцию, если на любом этапе возникла ошибка
        print(f"Error processing guess for user {user['code']}: {e}", file=sys.stderr)
        traceback.print_exc()
        flash(f"Произошла ошибка при обработке угадывания: {e}", "danger")


    # Отправляем обновление состояния игры всем подключенным клиентам
    socketio.emit('game_update', get_full_game_state_data(user_code_for_state=user_code), broadcast=True)

    return redirect(url_for('user', user_code=user_code))


# --- Админские маршруты ---
@app.route("/admin")
# @admin_required # Закомментировано для удобства тестирования, раскомментируйте для продакшена
def admin():
    db = get_db()
    users = db.execute("SELECT id, name, code, status, rating, is_admin FROM users").fetchall()
    settings = db.execute("SELECT key, value FROM settings").fetchall()
    active_subfolder = get_setting('active_subfolder')

    # Получаем список всех подпапок с карточками
    card_subfolders_base = os.path.join(app.static_folder, 'images')
    card_subfolders = []
    if os.path.exists(card_subfolders_base):
         card_subfolders = [name for name in os.listdir(card_subfolders_base)
                            if os.path.isdir(os.path.join(card_subfolders_base, name))
                            and name != GAME_BOARD_POLE_IMG_SUBFOLDER]

    # Получаем данные игрового поля для отображения
    all_active_users_for_board = db.execute("SELECT id, name, rating FROM users WHERE status = 'active'").fetchall()
    game_board_data = generate_game_board_data_for_display(all_active_users_for_board)

    # Получаем текущего ведущего
    current_leader_id = get_leading_user_id()
    current_leader_name = get_user_name(current_leader_id) if current_leader_id is not None else "Не определен"


    return render_template("admin.html",
                           users=users,
                           settings=settings,
                           card_subfolders=card_subfolders,
                           active_subfolder=active_subfolder,
                           game_in_progress=is_game_in_progress(),
                           game_over=is_game_over(),
                           show_card_info=get_setting("show_card_info") == "true",
                           current_leader_name=current_leader_name,
                           game_board=game_board_data,
                           game_board_num_cells=_current_game_board_num_cells
                          )


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        admin_code = request.form.get("admin_code")
        db = get_db()
        # Проверяем, есть ли пользователь с таким кодом и является ли он админом
        admin_user = db.execute("SELECT id, name, is_admin FROM users WHERE code = ?", (admin_code,)).fetchone()
        if admin_user and admin_user['is_admin']:
            session['is_admin'] = True
            session['user_code'] = admin_code # Сохраняем код админа в сессии пользователя тоже
            flash(f"Вход как администратор ({admin_user['name']}) выполнен успешно.", "success")
            return redirect(url_for('admin'))
        else:
            flash("Неверный код администратора.", "danger")
            return render_template("admin_login.html") # Остаемся на странице логина при ошибке
    return render_template("admin_login.html")

@app.route("/admin/logout")
@admin_required
def admin_logout():
    session.pop('is_admin', None)
    # Опционально можно также очистить session['user_code'], если админ не должен быть залогинен как обычный пользователь после выхода из админки
    # session.pop('user_code', None)
    flash("Вы вышли из режима администратора.", "info")
    return redirect(url_for('index')) # Или redirect(url_for('admin_login'))


@app.route("/admin/set_active_subfolder", methods=["POST"])
@admin_required
def admin_set_active_subfolder():
    db = get_db()
    subfolder = request.form.get('active_subfolder')
    if subfolder:
        # Проверяем, существует ли такая папка
        subfolder_path = os.path.join(app.static_folder, 'images', subfolder)
        if os.path.isdir(subfolder_path):
            set_setting('active_subfolder', subfolder)
            db.commit()
            flash(f"Активная колода карточек установлена: {subfolder}", "success")
            socketio.emit('game_update', get_full_game_state_data(), broadcast=True) # Уведомляем клиентов
        else:
            flash(f"Папка с карточками '{subfolder}' не найдена.", "danger")
    else:
        flash("Необходимо выбрать колоду.", "warning")
    return redirect(url_for('admin'))


@app.route("/admin/start_game", methods=["POST"])
@admin_required
def admin_start_game():
    db = get_db()
    if is_game_in_progress():
        flash("Игра уже в процессе.", "warning")
        return redirect(url_for('admin'))
    if is_game_over():
         flash("Игра окончена. Сбросьте игру, чтобы начать новую.", "warning")
         return redirect(url_for('admin'))

    active_subfolder = get_setting('active_subfolder')
    if not active_subfolder:
         flash("Не выбрана активная колода карточек. Выберите колоду.", "warning")
         return redirect(url_for('admin'))

    # Проверяем, достаточно ли карточек в выбранной колоде (например, минимум по 6 карт на каждого активного игрока)
    num_active_players = get_active_players_count(db)
    if num_active_players < 2:
         flash("Необходимо минимум 2 активных игрока для начала игры.", "warning")
         return redirect(url_for('admin'))

    total_cards_in_subfolder = db.execute("SELECT COUNT(*) FROM images WHERE subfolder = ? AND status = 'Свободно'", (active_subfolder,)).fetchone()[0]
    cards_needed = num_active_players * 6 # Пример: по 6 карт на игрока

    if total_cards_in_subfolder < cards_needed:
        flash(f"В колоде '{active_subfolder}' недостаточно свободных карточек для {num_active_players} игроков ({total_cards_in_subfolder} из {cards_needed}).", "warning")
        return redirect(url_for('admin'))

    db.execute("BEGIN") # Начинаем транзакцию

    try:
        # Сбрасываем статус всех пользователей в 'active' и рейтинг в 0
        db.execute("UPDATE users SET status = 'active', rating = 0 WHERE status != 'admin'")
        # Удаляем все старые карточки из таблицы images (если они там остались с прошлых игр)
        # Это может быть опасно, если используются несколько колод. Лучше сбросить статус только для текущей колоды.
        db.execute("UPDATE images SET owner_id = NULL, status = 'Свободно', guesses = NULL WHERE subfolder = ?", (active_subfolder,))


        # Перемешиваем все карточки в активной колоде и выдаем по 6 каждому активному игроку
        all_card_ids_in_subfolder = [r['id'] for r in db.execute("SELECT id FROM images WHERE subfolder = ? AND status = 'Свободно'", (active_subfolder,)).fetchall()]
        random.shuffle(all_card_ids_in_subfolder)

        active_users = db.execute("SELECT id FROM users WHERE status = 'active'").fetchall()
        card_index = 0
        for user in active_users:
            for i in range(6): # Выдаем по 6 карт
                if card_index < len(all_card_ids_in_subfolder):
                    card_id = all_card_ids_in_subfolder[card_index]
                    # Обновляем статус и владельца карточки
                    db.execute("UPDATE images SET owner_id = ?, status = ? WHERE id = ?", (user['id'], f'Занято:{user["id"]}', card_id))
                    card_index += 1
                else:
                     print(f"Not enough cards in subfolder {active_subfolder} to give 6 cards to user {user['id']} during game start.", file=sys.stderr)
                     break # Недостаточно карт

        # Выбираем случайного ведущего из активных игроков
        if active_users:
            leader_user = random.choice(active_users)
            set_setting('current_leader_id', str(leader_user['id']))
            print(f"New leader selected: User ID {leader_user['id']}", file=sys.stderr)
        else:
             set_setting('current_leader_id', None)
             print("No active users to select a leader.", file=sys.stderr)


        set_setting('game_in_progress', 'true')
        set_setting('game_over', 'false')
        set_setting('show_card_info', 'false') # Убедимся, что карты не показаны в начале игры

        db.commit() # Фиксируем все изменения начала игры

        flash("Игра успешно начата! Карточки выданы.", "success")
        socketio.emit('game_update', get_full_game_state_data(), broadcast=True) # Уведомляем клиентов

    except Exception as e:
        db.rollback() # Откатываем все изменения, если что-то пошло не так
        print(f"Error starting game: {e}", file=sys.stderr)
        traceback.print_exc()
        flash(f"Произошла ошибка при запуске игры: {e}", "danger")

    return redirect(url_for('admin'))


@app.route("/admin/reset_game", methods=["POST"])
@admin_required
def admin_reset_game():
    db = get_db()
    db.execute("BEGIN") # Начинаем транзакцию

    try:
        # Сбрасываем настройки игры
        set_setting('game_in_progress', 'false')
        set_setting('game_over', 'false')
        set_setting('show_card_info', 'false')
        set_setting('current_leader_id', None)
        # active_subfolder оставляем, чтобы не выбирать заново каждый раз
        # Сбрасываем статус всех пользователей обратно в 'pending' и рейтинг в 0
        db.execute("UPDATE users SET status = 'pending', rating = 0 WHERE status != 'admin'")
        # Сбрасываем все карточки в статус 'Свободно' и удаляем владельцев и угадывания
        db.execute("UPDATE images SET owner_id = NULL, status = 'Свободно', guesses = NULL")

        db.commit() # Фиксируем изменения сброса

        flash("Игра успешно сброшена.", "success")
        socketio.emit('game_update', get_full_game_state_data(), broadcast=True) # Уведомляем клиентов

    except Exception as e:
        db.rollback() # Откатываем при ошибке
        print(f"Error resetting game: {e}", file=sys.stderr)
        traceback.print_exc()
        flash(f"Произошла ошибка при сбросе игры: {e}", "danger")

    return redirect(url_for('admin'))

@app.route("/admin/end_game", methods=["POST"])
@admin_required
def admin_end_game():
    db = get_db()
    if not is_game_in_progress():
        flash("Игра не в процессе, чтобы ее завершить.", "warning")
        return redirect(url_for('admin'))

    db.execute("BEGIN") # Начинаем транзакцию
    try:
        set_setting('game_over', 'true')
        set_setting('game_in_progress', 'false') # Игра окончена, значит не в процессе
        # Опционально: можно открыть карты в конце игры, если они не были открыты
        # if get_setting('show_card_info') != 'true':
        #      set_setting('show_card_info', 'true')
        #      # Если нужно посчитать очки за последний раунд перед завершением,
        #      # можно вызвать perform_round_scoring(db) здесь,
        #      # но обычно игра завершается ПОСЛЕ раунда.

        db.commit()
        flash("Игра успешно завершена.", "success")
        socketio.emit('game_update', get_full_game_state_data(), broadcast=True) # Уведомляем клиентов

    except Exception as e:
         db.rollback()
         print(f"Error ending game: {e}", file=sys.stderr)
         traceback.print_exc()
         flash(f"Произошла ошибка при завершении игры: {e}", "danger")

    return redirect(url_for('admin'))


# --- ИЗМЕНЕНО: Маршрут /admin/open_cards теперь вызывает perform_round_scoring ---
@app.route("/admin/open_cards", methods=["POST"])
@admin_required
def admin_open_cards():
    db = get_db()
    if not is_game_in_progress():
        flash("Игра не в процессе.", "warning")
        return redirect(url_for('admin'))
    if is_game_over():
        flash("Игра уже завершена.", "warning")
        return redirect(url_for('admin'))

    num_active_players = get_active_players_count(db)
    active_subfolder = get_setting('active_subfolder')
    num_cards_on_table = db.execute("SELECT COUNT(*) FROM images WHERE status LIKE 'На столе:%' AND subfolder = ?", (active_subfolder,)).fetchone()[0]


    # Условие для подсчета очков: достаточно ли карт на столе от активных игроков (минимум 2 игрока, выложившие карты)
    active_users_with_card_on_table_ids = [u['id'] for u in db.execute("SELECT id FROM users WHERE status = 'active'").fetchall() if db.execute("SELECT COUNT(*) FROM images WHERE owner_id = ? AND status LIKE 'На столе:%'", (u['id'],)).fetchone()[0] > 0]

    if len(active_users_with_card_on_table_ids) < 2 or num_cards_on_table < len(active_users_with_card_on_table_ids) :
        flash("Недостаточно карточек на столе или нет активных игроков, выложивших карты, для подсчета очков.", "warning")
        # Если флаг show_card_info был ошибочно установлен, сбрасываем его
        if get_setting("show_card_info") == "true":
             set_setting("show_card_info", "false")
             db.commit()
             socketio.emit('game_update', get_full_game_state_data(), broadcast=True)
        return redirect(url_for('admin'))


    db.execute("BEGIN") # Начинаем транзакцию

    try:
        # Устанавливаем флаг, чтобы показать карточки
        set_setting("show_card_info", "true") # ИСПРАВЛЕНО: Синтаксическая ошибка здесь

        # Фиксируем установку флага перед подсчетом, чтобы клиенты увидели его
        db.commit()
        print("Admin set show_card_info to true. Calling perform_round_scoring...", file=sys.stderr)

        # --- ИЗМЕНЕНО: Вызов новой функции для подсчета очков ---
        perform_round_scoring(db) # Вызываем отдельную функцию для логики подсчета
        # --- КОНЕЦ ИЗМЕНЕННОГО ---

        # Фиксируем результаты подсчета очков и обновления поля
        db.commit()
        flash("Карточки открыты, очки подсчитаны.", "success")
    except Exception as e:
        db.rollback() # Откатываем изменения при ошибке
        print(f"Error during scoring via admin: {e}", file=sys.stderr)
        traceback.print_exc()
        flash(f"Ошибка при подсчете очков: {e}", "danger")
        # В случае ошибки откатываем и сбрасываем флаг show_card_info
        try:
             set_setting("show_card_info", "false") # ИСПРАВЛЕНО: Синтаксическая ошибка здесь
             db.commit() # Фиксируем сброс флага
             socketio.emit('game_update', get_full_game_state_data(), broadcast=True)
        except Exception as inner_e:
             print(f"Error during rollback and setting show_card_info to false after admin scoring error: {inner_e}", file=sys.stderr)

    # Отправляем обновление состояния игры всем клиентам после подсчета очков
    socketio.emit('game_update', get_full_game_state_data(), broadcast=True)

    return redirect(url_for('admin'))

# ... (возможно, у вас есть другие админские маршруты для управления пользователями, карточками и т.д.) ...


# --- SocketIO события ---
@socketio.on('connect')
# ИСПРАВЛЕНО: Добавлены *args и **kwargs для корректной обработки аргументов, передаваемых SocketIO
def handle_connect(*args, **kwargs):
    sid = request.sid
    user_code = session.get('user_code')
    if user_code:
        db = get_db()
        try: # Оборачиваем обращение к БД в try/except на случай, если таблица users еще не создана
            user = db.execute("SELECT id, code FROM users WHERE code = ?", (user_code,)).fetchone()
            if user:
                connected_users_socketio[sid] = user_code
                print(f"SocketIO: Client connected: SID={sid}, User code: {user_code}", file=sys.stderr)
                # Отправляем начальное состояние игры только подключившемуся клиенту
                try:
                    initial_state = get_full_game_state_data(user_code_for_state=user_code)
                    emit('game_update', initial_state, room=sid)
                except Exception as e:
                    print(f"SocketIO: Error sending initial state to {sid}: {e}\n{traceback.format_exc()}", file=sys.stderr)
            else:
                print(f"SocketIO: Client connected with invalid user_code in session: SID={sid}, Code: {user_code}. Clearing session.", file=sys.stderr)
                # Очищаем некорректную сессию и, возможно, отправляем сообщение клиенту
                session.pop('user_code', None)
                # Отправляем базовое состояние гостя после очистки сессии
                try:
                     initial_state = get_full_game_state_data(user_code_for_state=None)
                     emit('game_update', initial_state, room=sid)
                except Exception as e:
                     print(f"SocketIO: Error sending initial state to guest after invalid session: {sid}: {e}\n{traceback.format_exc()}", file=sys.stderr)

        except sqlite3.OperationalError as e:
             print(f"SocketIO: OperationalError accessing DB during connect for SID {sid}: {e}. Database might not be initialized.", file=sys.stderr)
             traceback.print_exc()
             # В случае ошибки БД при подключении, возможно, стоит отправить клиенту сообщение об ошибке сервера
             emit('message', {'data': 'Ошибка сервера при подключении.', 'category': 'danger'}, room=sid)
        except Exception as e:
             print(f"SocketIO: Unexpected error during connect for SID {sid}: {e}", file=sys.stderr)
             traceback.print_exc()
             emit('message', {'data': 'Неизвестная ошибка сервера при подключении.', 'category': 'danger'}, room=sid)


    else:
        print(f"SocketIO: Client connected without user_code in session: SID={sid} (Guest)", file=sys.stderr)
        # Для гостей без кода отправляем базовое состояние
        try:
             initial_state = get_full_game_state_data(user_code_for_state=None) # Передаем None для гостя
             emit('game_update', initial_state, room=sid)
        except Exception as e:
             print(f"SocketIO: Error sending initial state to guest {sid}: {e}\n{traceback.format_exc()}", file=sys.stderr)


@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    user_code = connected_users_socketio.pop(sid, None)
    print(f"SocketIO: Client disconnected: SID={sid}, User code: {user_code or 'N/A'}", file=sys.stderr)


# --- Запуск приложения ---
if __name__ == "__main__":
    # Инициализация базы данных, если ее нет
    # ВАЖНО: Убедитесь, что ваша среда развертывания запускает этот блок кода
    # ПЕРЕД тем, как приложение начинает принимать соединения.
    print("Running init_db from __main__ block...", file=sys.stderr)
    try:
        init_db()
        print("init_db completed from __main__ block.", file=sys.stderr)
    except Exception as e:
         print(f"Error during init_db execution in __main__: {e}", file=sys.stderr)
         traceback.print_exc()


    # Инициализация визуализации игрового поля при старте
    print("Инициализация визуализации игрового поля...", file=sys.stderr)
    # Получаем список активных пользователей для инициализации поля
    users_for_board_init = []
    # Повторяем попытку подключения к БД здесь, на случай, если init_db только что создал файл
    try:
         conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row; cur = conn.cursor()
         cur.execute("SELECT id, name, rating FROM users WHERE status = 'active'")
         users_for_board_init = cur.fetchall(); conn.close(); conn = None # Закрываем и обнуляем
    except Exception as e: print(f"Ошибка чтения пользователей для поля при старте: {e}", file=sys.stderr)
    finally:
         if conn: conn.close()


    initialize_new_game_board_visuals(users_for_board_config=users_for_board_init)
    print("Game board visuals initialization finished.", file=sys.stderr)


    port = int(os.environ.get("PORT", 5000))
    # debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true" # Использовать переменную окружения FLASK_DEBUG
    # Используйте debug=True только в режиме разработки
    # allow_unsafe_werkzeug=True нужен для включения отладчика в Werkzeug, но может быть небезопасен на продакшене.
    print(f"Starting SocketIO server on port {port}...", file=sys.stderr)
    socketio.run(app, host="0.0.0.0", port=port, debug=True, allow_unsafe_werkzeug=True)
