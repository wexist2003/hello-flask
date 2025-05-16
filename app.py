import json
import sys
import sqlite3
import os
import string
import random
import traceback
import re # Import re for regex operations
from flask import Flask, render_template, request, redirect, url_for, g, flash, session
from flask_socketio import SocketIO, emit
# import click # No longer needed if init command is removed

app = Flask(__name__)
# ВАЖНО: Убедитесь, что этот ключ ИДЕНТИЧЕН тому, что был в работающей версии
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your_very_secret_fallback_key_for_dev_only_12345')
if app.config['SECRET_KEY'] == 'your_very_secret_fallback_key_for_dev_only_12345':
    print("ПРЕДУПРЕЖДЕНИЕ: Используется SECRET_KEY по умолчанию. Установите переменную окружения SECRET_KEY!", file=sys.stderr)

socketio = SocketIO(app)
DB_PATH = 'database.db'

GAME_BOARD_POLE_IMG_SUBFOLDER = "pole"
# GAME_BOARD_POLE_IMAGES is not strictly needed as filenames come from DB/config
# GAME_BOARD_POLE_IMAGES = [f"p{i}.jpg" for i in range(1, 8)]

# Default config constant (used if DB is empty or on error)
_DEFAULT_BOARD_CONFIG_CONSTANT = [
    {'id': 1, 'image': 'p1.jpg', 'max_rating': 5},
    {'id': 2, 'image': 'p2.jpg', 'max_rating': 10},
    {'id': 3, 'image': 'p3.jpg', 'max_rating': 15},
    {'id': 4, 'image': 'p4.jpg', 'max_rating': 20},
    {'id': 5, 'image': 'p5.jpg', 'max_rating': 25},
    {'id': 6, 'image': 'p6.jpg', 'max_rating': 30},
    {'id': 7, 'image': 'p7.jpg', 'max_rating': 35},
    {'id': 8, 'image': 'p8.jpg', 'max_rating': 40}, # Assuming 40 is the end
]
DEFAULT_NUM_BOARD_CELLS = 40 # This should ideally match the max_rating of the last board visual cell

# Global variables for connection tracking etc. remain
connected_users_socketio = {}  # {sid: user_code}


def get_db():
    if 'db' not in g:
        # Check if DB file exists, if not, it will be created on first connection
        # For simplicity, ensure directory exists if using subdirectories for DB
        # os.makedirs(os.path.dirname(DB_PATH), exist_ok=True) # If DB_PATH includes directories
        g.db = sqlite3.connect(DB_PATH, timeout=10)
        g.db.row_factory = sqlite3.Row
        # Enable foreign key support (important for integrity)
        g.db.execute("PRAGMA foreign_keys = ON;")
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

# Renamed and modified for automatic initialization
def init_database():
    """Create database tables if they do not exist."""
    db = get_db()
    cursor = db.cursor()

    # --- Database Schema Creation Directly in app.py with IF NOT EXISTS ---
    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            is_admin BOOLEAN DEFAULT FALSE,
            rating INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending' -- 'pending', 'active', 'inactive'
        );

        CREATE TABLE IF NOT EXISTS decks (
            subfolder TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            votes INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subfolder TEXT NOT NULL,
            image TEXT NOT NULL,
            status TEXT DEFAULT 'Свободно', -- 'Свободно', 'Занято:user_id', 'На столе:user_id'
            owner_id INTEGER, -- Added owner_id for easier lookup of placed cards
            FOREIGN KEY (subfolder) REFERENCES decks (subfolder),
            FOREIGN KEY (owner_id) REFERENCES users (id) -- Link owner_id to users
        );

        CREATE TABLE IF NOT EXISTS game_state (
            id INTEGER PRIMARY KEY DEFAULT 1,
            game_in_progress BOOLEAN DEFAULT FALSE,
            game_over BOOLEAN DEFAULT FALSE,
            current_leader_id INTEGER,
            active_subfolder TEXT,
            on_table_status BOOLEAN DEFAULT FALSE, -- True when players place cards
            show_card_info BOOLEAN DEFAULT FALSE, -- True when cards are revealed
            next_leader_id INTEGER, -- Added next_leader_id column
            leader_pole_image_path TEXT, -- Added to store path of leader's board image (redundant? can calculate from rating)
            leader_pictogram_rating INTEGER, -- Added to store leader's rating for pictogram (redundant? can get from user table)
            current_num_board_cells INTEGER DEFAULT 40, -- Store num cells (should match max_rating from game_board_visuals)
            FOREIGN KEY (current_leader_id) REFERENCES users (id),
            FOREIGN KEY (active_subfolder) REFERENCES decks (subfolder),
            FOREIGN KEY (next_leader_id) REFERENCES users (id)
        );

        CREATE TABLE IF NOT EXISTS guesses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, -- Who made the guess
            image_id INTEGER NOT NULL, -- The card the user voted on (image id from 'images' table)
            guessed_user_id INTEGER NOT NULL, -- The owner the user guessed (user id from 'users' table)
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (image_id) REFERENCES images (id),
            FOREIGN KEY (guessed_user_id) REFERENCES users (id)
        );

        -- Table to store game board visual configuration
        CREATE TABLE IF NOT EXISTS game_board_visuals (
             id INTEGER PRIMARY KEY, -- Corresponds to cell number/rating segment
             image TEXT NOT NULL, -- Image file name
             max_rating INTEGER NOT NULL -- Max rating for this segment
        );

    """)
    db.commit()

    # --- Initialize default game board visuals if the table is empty ---
    cursor.execute("SELECT COUNT(*) FROM game_board_visuals")
    if cursor.fetchone()[0] == 0:
        print("Initializing game board visuals...", file=sys.stderr)
        if _DEFAULT_BOARD_CONFIG_CONSTANT:
             cursor.executescript("""
                 INSERT INTO game_board_visuals (id, image, max_rating) VALUES
                 (1, 'p1.jpg', 5),
                 (2, 'p2.jpg', 10),
                 (3, 'p3.jpg', 15),
                 (4, 'p4.jpg', 20),
                 (5, 'p5.jpg', 25),
                 (6, 'p6.jpg', 30),
                 (7, 'p7.jpg', 35),
                 (8, 'p8.jpg', 40);
             """)
             db.commit()
             print("Inserted default game board visual entries.", file=sys.stderr)
        else:
             print("WARNING: _DEFAULT_BOARD_CONFIG_CONSTANT is empty. Cannot initialize game board visuals.", file=sys.stderr)


    # Initialize the single game_state row if it doesn't exist
    cursor.execute("SELECT COUNT(*) FROM game_state WHERE id = 1")
    if cursor.fetchone()[0] == 0:
         cursor.execute("INSERT INTO game_state DEFAULT VALUES")
         db.commit()


    print('Database initialized or already exists.', file=sys.stderr)


# --- Automatic Database Initialization and Board Visuals Loading into app.config on App Load ---
with app.app_context():
    init_database()

    # Load game board visuals into app.config
    db = get_db()
    cursor = db.cursor()
    try:
         cursor.execute("SELECT id, image, max_rating FROM game_board_visuals ORDER BY id")
         board_config_rows = cursor.fetchall()
         if board_config_rows:
              app.config['BOARD_VISUAL_CONFIG'] = [dict(row) for row in board_config_rows]
              app.config['NUM_BOARD_CELLS'] = board_config_rows[-1]['max_rating']
              print("Loaded game board visuals into app.config from DB.", file=sys.stderr)
         else:
              print("WARNING: Game board visuals table is empty after initialization. Using default config constant.", file=sys.stderr)
              app.config['BOARD_VISUAL_CONFIG'] = _DEFAULT_BOARD_CONFIG_CONSTANT
              app.config['NUM_BOARD_CELLS'] = DEFAULT_NUM_BOARD_CELLS

    except sqlite3.OperationalError as e:
         print(f"WARNING: Could not load game board visuals from DB on startup: {e}. Table might be missing despite init_database attempt. Using default config constant.", file=sys.stderr)
         app.config['BOARD_VISUAL_CONFIG'] = _DEFAULT_BOARD_CONFIG_CONSTANT
         app.config['NUM_BOARD_CELLS'] = DEFAULT_NUM_BOARD_CELLS
    except Exception as e:
         print(f"Error loading game board visuals into app.config on startup: {e}\n{traceback.format_exc()}", file=sys.stderr)
         app.config['BOARD_VISUAL_CONFIG'] = _DEFAULT_BOARD_CONFIG_CONSTANT
         app.config['NUM_BOARD_CELLS'] = DEFAULT_NUM_BOARD_CELLS
# --- End of Automatic Initialization Block ---


def broadcast_game_update(user_code_trigger=None):
    """Sends the current game state to all connected users or a specific user."""
    for sid, code in list(connected_users_socketio.items()): # Use list for safe iteration
         try:
             user_specific_state = state_to_json(user_code_for_state=code)
             emit('game_update', user_specific_state, room=sid)
         except Exception as e:
             print(f"Error sending update to SID {sid} ({code}): {e}\n{traceback.format_exc()}", file=sys.stderr)


def get_user_name_by_id(user_id):
    """Helper function to get user name by ID."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT name FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    return user['name'] if user else None


def state_to_json(user_code_for_state=None):
    """Pulls current game state from DB and formats it for frontend."""
    db = get_db()
    cursor = db.cursor()

    # Fetch current game state
    cursor.execute("SELECT * FROM game_state WHERE id = 1")
    game_state = cursor.fetchone()

    if not game_state:
         print("WARNING: game_state row (id=1) not found in DB!", file=sys.stderr)
         # Fallback state - should be prevented by init_database
         return {
            'game_in_progress': False,
            'game_over': False,
            'current_leader_name': None,
            'next_leader_name': None,
            'on_table_status': False,
            'show_card_info': False,
            'all_cards_placed_for_guessing_phase_to_template': False,
            'user_cards': [],
            'table_images': [],
            'all_users_for_guessing': [],
            'db_current_leader_id': None,
            'current_user_data': None,
            'flashed_messages': [],
            'game_board': [],
            # Use values from app.config or default constants
            'current_num_board_cells': app.config.get('NUM_BOARD_CELLS', DEFAULT_NUM_BOARD_CELLS),
            'leader_pole_pictogram_path': None,
            'leader_pictogram_rating_display': None,
        }


    game_in_progress = bool(game_state['game_in_progress'])
    game_over = bool(game_state['game_over'])
    current_leader_id = game_state['current_leader_id']
    on_table_status = bool(game_state['on_table_status'])
    show_card_info = bool(game_state['show_card_info'])
    next_leader_id = game_state['next_leader_id']

    # Get number of board cells from app.config, fallback to default
    current_num_board_cells = app.config.get('NUM_BOARD_CELLS', DEFAULT_NUM_BOARD_CELLS)


    current_leader_name = get_user_name_by_id(current_leader_id) if current_leader_id else None
    next_leader_name = get_user_name_by_id(next_leader_id) if next_leader_id else None

    flashed_messages_list = get_flashed_messages(with_categories=True) if user_code_for_state else []
    flashed_messages = [dict(msg) for msg in flashed_messages_list]


    user_cards = []
    table_images = []
    all_users_for_guessing = []
    current_user_data = None


    # Fetch current user data if code is provided
    if user_code_for_state:
        cursor.execute("SELECT id, code, name, rating, status FROM users WHERE code = ?", (user_code_for_state,))
        current_user = cursor.fetchone()
        if current_user:
            current_user_data = dict(current_user)
            # Fetch user cards if user is active and game is in progress
            if current_user_data['status'] == 'active' and (game_in_progress or game_over):
                 cursor.execute("SELECT id, subfolder, image FROM images WHERE status = ? AND owner_id = ?", (f'Занято: {current_user_data["id"]}', current_user_data['id']))
                 user_cards = [dict(row) for row in cursor.fetchall()]


    if game_in_progress or game_over:
        # Fetch images on the table
        cursor.execute("SELECT id, subfolder, image, status, owner_id FROM images WHERE status LIKE 'На столе:%'")
        table_images_raw = cursor.fetchall()

        # Fetch all active users for guessing phase and other user info
        cursor.execute("SELECT id, name, rating FROM users WHERE status = 'active'")
        all_active_users = {row['id']: dict(row) for row in cursor.fetchall()}
        all_users_for_guessing = list(all_active_users.values())

        # Fetch guesses related to cards currently on the table
        all_guesses_raw = []
        all_guesses_by_card = {}
        if table_images_raw:
             table_image_ids = tuple(img['id'] for img in table_images_raw)
             if table_image_ids:
                 cursor.execute("SELECT user_id, guessed_user_id, image_id FROM guesses WHERE image_id IN ({})".format(','.join('?' * len(table_image_ids))), table_image_ids)
                 all_guesses_raw = cursor.fetchall()
                 for guess in all_guesses_raw:
                     card_guessed_about_id = guess['image_id']
                     if card_guessed_about_id not in all_guesses_by_card:
                         all_guesses_by_card[card_guessed_about_id] = []
                     all_guesses_by_card[card_guessed_about_id].append((guess['user_id'], guess['guessed_user_id']))


        # Augment table images with owner info and guesses
        if show_card_info or on_table_status:
             for img in table_images_raw:
                owner_id = img['owner_id']
                owner_name = all_active_users.get(owner_id, {}).get('name', f'Игрок ID {owner_id}')
                img_dict = dict(img)
                img_dict['owner_id'] = owner_id
                img_dict['owner_name'] = owner_name
                img_dict['guesses'] = {user_id: guessed_user_id for user_id, guessed_user_id in all_guesses_by_card.get(img_dict['id'], [])}

                if current_user_data and current_user_data['status'] == 'active':
                     current_user_id = current_user_data['id']
                     user_guess_for_this_card = None
                     for guess_entry in all_guesses_raw:
                         if guess_entry['user_id'] == current_user_id and guess_entry['image_id'] == img_dict['id']:
                             user_guess_for_this_card = guess_entry['guessed_user_id']
                             break
                     img_dict['my_guess_for_this_card_value'] = user_guess_for_this_card

                table_images.append(img_dict)


    # Determine if all active players have placed a card for the guessing phase
    all_cards_placed_for_guessing_phase = False
    if game_in_progress and not game_over and current_leader_id is not None and on_table_status:
         cursor.execute("SELECT COUNT(*) FROM users WHERE status = 'active'")
         active_players_count = cursor.fetchone()[0]
         cursor.execute("SELECT COUNT(DISTINCT owner_id) FROM images WHERE status LIKE 'На столе:%'")
         placed_cards_distinct_owners_count = cursor.fetchone()[0]

         if active_players_count > 0 and placed_cards_distinct_owners_count == active_players_count:
             all_cards_placed_for_guessing_phase = True
         elif active_players_count == 0:
              pass


    # Determine leader's board visual state based on rating
    leader_pole_image_path = None
    leader_pictogram_rating_display = None
    # Get board config from app.config
    game_board_visual_config_local = app.config.get('BOARD_VISUAL_CONFIG', [])

    if game_board_visual_config_local:
         if current_leader_id is not None and (game_in_progress or game_over):
             cursor.execute("SELECT rating FROM users WHERE id = ?", (current_leader_id,))
             leader_rating_row = cursor.fetchone()
             if leader_rating_row:
                 leader_rating = leader_rating_row['rating']
                 leader_pictogram_rating_display = leader_rating
                 if game_board_visual_config_local:
                     for i in range(len(game_board_visual_config_local)):
                          if leader_rating <= game_board_visual_config_local[i]['max_rating']:
                              leader_pole_image_path = os.path.join(GAME_BOARD_POLE_IMG_SUBFOLDER, game_board_visual_config_local[i]['image'])
                              break
                     if leader_pole_image_path is None:
                          leader_pole_image_path = os.path.join(GAME_BOARD_POLE_IMG_SUBFOLDER, game_board_visual_config_local[-1]['image'])


    # Fetch game board state (users on cells)
    game_board_state = []
    if (game_in_progress or game_over) and game_board_visual_config_local:
        cursor.execute("SELECT id, name, rating FROM users WHERE status = 'active'")
        active_users_for_board = {row['id']: dict(row) for row in cursor.fetchall()}
        active_users_list = list(active_users_for_board.values())
        active_users_list.sort(key=lambda x: x['rating'])

        # Determine the number of board cells based on the loaded config from app.config
        num_board_cells_display = app.config.get('NUM_BOARD_CELLS', DEFAULT_NUM_BOARD_CELLS)

        # We need the full board config from DB results again to calculate min_rating correctly
        cursor.execute("SELECT id, image, max_rating FROM game_board_visuals ORDER BY id")
        board_config_rows_for_min_rating = cursor.fetchall()

        for cell_config in game_board_visual_config_local:
            cell_data = {
                'cell_number': cell_config['id'],
                'image_path': os.path.join(GAME_BOARD_POLE_IMG_SUBFOLDER, cell_config['image']),
                'max_rating': cell_config['max_rating'],
                'users_in_cell': []
            }
            # Find users in this cell based on rating range using board_config_rows_for_min_rating
            min_rating = 0 if cell_config['id'] == 1 else board_config_rows_for_min_rating[cell_config['id']-2]['max_rating'] + 1
            max_rating = cell_config['max_rating']

            users_in_this_cell = [user for user in active_users_list if user['rating'] >= min_rating and user['rating'] <= max_rating]
            cell_data['users_in_cell'] = users_in_this_cell

            game_board_state.append(cell_data)


    return {
        'game_in_progress': game_in_progress,
        'game_over': game_over,
        'current_leader_name': current_leader_name,
        'next_leader_name': next_leader_name,
        'on_table_status': on_table_status,
        'show_card_info': show_card_info,
        'all_cards_placed_for_guessing_phase_to_template': all_cards_placed_for_guessing_phase,
        'user_cards': user_cards,
        'table_images': table_images,
        'all_users_for_guessing': all_users_for_guessing,
        'db_current_leader_id': current_leader_id,
        'current_user_data': current_user_data,
        'flashed_messages': flashed_messages,
        'game_board': game_board_state,
        # Use the determined number of cells from app.config or default
        'current_num_board_cells': app.config.get('NUM_BOARD_CELLS', DEFAULT_NUM_BOARD_CELLS),
        'leader_pole_pictogram_path': leader_pole_image_path,
        'leader_pictogram_rating_display': leader_pictogram_rating_display,
    }


# Helper function for internal end round logic
def end_round():
    """Calculates scores, updates ratings, determines next leader, and updates game state."""
    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT * FROM game_state WHERE id = 1")
    game_state = cursor.fetchone()
    if not game_state or not game_state['game_in_progress'] or game_state['game_over'] or not game_state['show_card_info']:
         print("Error: end_round called in invalid state (show_card_info is not True or game state invalid).", file=sys.stderr)
         if game_state and game_state['game_in_progress'] and not game_state['game_over']:
             cursor.execute("UPDATE game_state SET on_table_status = 0, show_card_info = 0, leader_pole_image_path = NULL, leader_pictogram_rating = NULL")
             cursor.execute("UPDATE images SET status = 'Свободно', owner_id = NULL WHERE status LIKE 'На столе:%'")
             cursor.execute("DELETE FROM guesses")
             db.commit()
             flash("Раунд сброшен из-за внутренней ошибки.", "danger")
             broadcast_game_update()
         return


    current_leader_id = game_state['current_leader_id']
    active_subfolder = game_state['active_subfolder']

    cursor.execute("SELECT id, owner_id FROM images WHERE status LIKE 'На столе:%' AND subfolder = ?", (active_subfolder,))
    table_images_raw = cursor.fetchall()
    table_image_owners = {img['id']: img['owner_id'] for img in table_images_raw}
    table_image_ids = tuple(table_image_owners.keys()) if table_image_owners else tuple()


    if not table_image_owners:
         flash("На столе нет карточек из активной колоды для подсчета очков.", "warning")
         cursor.execute("UPDATE images SET status = 'Свободно', owner_id = NULL WHERE status LIKE 'На столе:%' AND subfolder = ?", (active_subfolder,))
         cursor.execute("DELETE FROM guesses")
         cursor.execute("UPDATE game_state SET on_table_status = 0, show_card_info = 0, leader_pole_image_path = NULL, leader_pictogram_rating = NULL, current_num_board_cells = NULL, current_leader_id = NULL, next_leader_id = NULL")
         db.commit()
         broadcast_game_update()
         return

    leader_card_id = None
    for img_id, owner_id in table_image_owners.items():
        if owner_id == current_leader_id:
            leader_card_id = img_id
            break

    if current_leader_id is not None:
        if leader_card_id is None and len(table_image_owners) > 0:
             flash("Ведущий не выложил карточку для подсчета очков.", "danger")
             cursor.execute("UPDATE images SET status = 'Свободно', owner_id = NULL WHERE status LIKE 'На столе:%' AND subfolder = ?", (active_subfolder,))
             cursor.execute("DELETE FROM guesses")
             cursor.execute("UPDATE game_state SET on_table_status = 0, show_card_info = 0, leader_pole_image_path = NULL, leader_pictogram_rating = NULL, current_num_board_cells = NULL, current_leader_id = NULL, next_leader_id = NULL")
             db.commit()
             broadcast_game_update()
             return


    cursor.execute("SELECT id, rating FROM users WHERE status = 'active'")
    active_players = cursor.fetchall()
    active_player_ids = [p['id'] for p in active_players]
    player_ratings = {p['id']: p['rating'] for p in active_players}

    all_guesses = []
    if table_image_ids:
        cursor.execute("SELECT user_id, guessed_user_id, image_id FROM guesses WHERE image_id IN ({})".format(','.join('?' * len(table_image_ids))), table_image_ids)
        all_guesses = cursor.fetchall()


    score_changes = {player_id: 0 for player_id in active_player_ids}
    correct_leader_guessers = []


    guesses_by_card_guessed_about = {}
    for guess in all_guesses:
        card_guessed_about_id = guess['image_id']
        if card_guessed_about_id not in guesses_by_card_guessed_about:
            guesses_by_card_guessed_about[card_guessed_about_id] = []
        guesses_by_card_guessed_about[card_guessed_about_id].append((guess['user_id'], guess['guessed_user_id']))


    # --- Scoring Logic based on Guesses ---

    non_leader_players_ids = [pid for pid in active_player_ids if pid != current_leader_id]
    num_non_leader_players = len(non_leader_players_ids)

    leader_score_from_guesses_on_his_card = 0
    if leader_card_id is not None:
        leader_card_guesses = guesses_by_card_guessed_about.get(leader_card_id, [])
        leader_card_guesses_by_others = [guess for guess in leader_card_guesses if guess[0] != current_leader_id]
        num_correct_leader_guesses_by_others = 0
        correct_leader_guessers = []
        for guesser_id, guessed_owner_id in leader_card_guesses_by_others:
            if guessed_owner_id == current_leader_id:
                num_correct_leader_guesses_by_others += 1
                correct_leader_guessers.append(guesser_id)

        if num_non_leader_players > 0:
            if num_correct_leader_guesses_by_others == num_non_leader_players:
                 leader_score_from_guesses_on_his_card = -3
                 flash(f"Все игроки угадали карточку Ведущего. Ведущий перемещается на 3 хода назад.", "info")
            elif num_correct_leader_guesses_by_others == 0:
                 leader_score_from_guesses_on_his_card = -2
                 flash(f"Ни один игрок не угадал карточку Ведущего. Ведущий перемещается на 2 хода назад.", "info")
            else:
                 leader_score_from_guesses_on_his_card = 3 + num_correct_leader_guesses_by_others
                 flash(f"{num_correct_leader_guesses_by_others} игрок(а) угадали карточку Ведущего.", "info")


        if num_non_leader_players > 0 and not (num_correct_leader_guesses_by_others == num_non_leader_players or num_correct_leader_guesses_by_others == 0):
            for guesser_id in correct_leader_guessers:
                if guesser_id in score_changes:
                     score_changes[guesser_id] += 3

    for card_id, owner_id in table_image_owners.items():
        if owner_id != current_leader_id:
            guesses_about_this_player_card = guesses_by_card_guessed_about.get(card_id, [])
            correct_guessers_for_this_player_card = [guesser_id for guesser_id, guessed_owner_id in guesses_about_this_player_card if guessed_owner_id == owner_id and guesser_id != owner_id]
            num_correct_guesses_for_this_player_card = len(correct_guessers_for_this_player_card)

            if owner_id in score_changes:
                 score_changes[owner_id] += num_correct_guesses_for_this_player_card
                 if num_correct_guesses_for_this_player_card > 0:
                      player_name = get_user_name_by_id(owner_id) or f'Игрок ID {owner_id}'
                      flash(f"Карточку игрока {player_name} угадали {num_correct_guesses_for_this_player_card} игрок(а).", "info")

    if current_leader_id is not None and current_leader_id in score_changes:
         score_changes[current_leader_id] += leader_score_from_guesses_on_his_card


    for player_id, score_change in score_changes.items():
        if score_change != 0:
             player_name = get_user_name_by_id(player_id) or f'Игрок ID {player_id}'
             cursor.execute("UPDATE users SET rating = MAX(0, rating + ?) WHERE id = ?", (score_change, player_id))
             db.commit()


    game_over = False
    # Get number of board cells from app.config, fallback to default
    current_num_board_cells = app.config.get('NUM_BOARD_CELLS', DEFAULT_NUM_BOARD_CELLS)

    cursor.execute("SELECT COUNT(*) FROM users WHERE status = 'active' AND rating >= ?", (current_num_board_cells,))
    players_at_end = cursor.fetchone()[0]
    if players_at_end > 0:
        game_over = True
        flash("Игра окончена! Игрок достиг конца игрового поля.", "success")
        cursor.execute("UPDATE game_state SET game_over = 1, game_in_progress = 0, on_table_status = 0, show_card_info = 0, current_leader_id = NULL, next_leader_id = NULL")
        db.commit()


    next_leader_id = None
    if not game_over:
         cursor.execute("SELECT id FROM users WHERE status = 'active' ORDER BY rating DESC LIMIT 1")
         next_leader_row = cursor.fetchone()
         if next_leader_row:
             next_leader_id = next_leader_row['id']
         else:
             next_leader_id = None

    if not game_over:
         cursor.execute("UPDATE game_state SET on_table_status = 0, show_card_info = 0, current_leader_id = NULL, next_leader_id = ? WHERE id = 1", (next_leader_id,))
         db.commit()


    if active_subfolder:
        cursor.execute("UPDATE images SET status = 'Свободно', owner_id = NULL WHERE status LIKE 'На столе:%' AND subfolder = ?", (active_subfolder,))

    cursor.execute("DELETE FROM guesses")

    db.commit()

    broadcast_game_update()


@app.route('/admin/end_round_manual', methods=['POST'])
@app.route('/end_round', methods=['POST'])
def admin_end_round_manual():
    """Admin or auto trigger to reveal cards, calculate scores, and end round."""
    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT * FROM game_state WHERE id = 1")
    game_state = cursor.fetchone()

    if not game_state or not game_state['game_in_progress'] or game_state['game_over']:
         flash("Игра не в процессе.", "warning")
         return redirect(url_for('index'))

    cursor.execute("UPDATE game_state SET show_card_info = 1 WHERE id = 1")
    db.commit()

    broadcast_game_update()

    end_round()

    flash("Раунд завершен, очки подсчитаны.", "success")

    return redirect(url_for('index'))


@app.route('/start_new_round', methods=['POST'])
def start_new_round():
    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT * FROM game_state WHERE id = 1")
    game_state = cursor.fetchone()

    if game_state and game_state['game_in_progress'] and not game_state['game_over']:
        flash("Игра уже в процессе.", "warning")
        return redirect(url_for('index'))

    if game_state and game_state['game_over']:
         flash("Игра окончена. Запустите новую игру через админ панель.", "warning")
         return redirect(url_for('index'))

    next_leader_id = game_state['next_leader_id'] if game_state and 'next_leader_id' in game_state else None
    if next_leader_id is None:
         cursor.execute("SELECT id FROM users WHERE status = 'active'")
         active_users_for_leader_selection = cursor.fetchall()
         if not active_users_for_leader_selection:
              flash("Недостаточно активных игроков для начала раунда.", "warning")
              cursor.execute("UPDATE game_state SET game_in_progress = 0, on_table_status = 0, show_card_info = 0, current_leader_id = NULL, next_leader_id = NULL")
              db.commit()
              broadcast_game_update()
              return redirect(url_for('index'))

         cursor.execute("SELECT id FROM users WHERE status = 'active' ORDER BY rating DESC LIMIT 1")
         initial_leader_row = cursor.fetchone()
         current_leader_id = initial_leader_row['id'] if initial_leader_row else None

         if current_leader_id is None:
              flash("Ошибка при выборе ведущего.", "danger")
              cursor.execute("UPDATE game_state SET game_in_progress = 0, on_table_status = 0, show_card_info = 0, current_leader_id = NULL, next_leader_id = NULL")
              db.commit()
              broadcast_game_update()
              return redirect(url_for('index'))

         cursor.execute("UPDATE game_state SET current_leader_id = ?, next_leader_id = NULL WHERE id = 1", (current_leader_id,))

    else:
         current_leader_id = next_leader_id
         cursor.execute("UPDATE game_state SET current_leader_id = ?, next_leader_id = NULL WHERE id = 1", (current_leader_id,))


    db.commit()


    cursor.execute("UPDATE images SET status = 'Свободно', owner_id = NULL")

    cursor.execute("DELETE FROM guesses")
    db.commit()

    cursor.execute("SELECT id FROM users WHERE status = 'active'")
    active_users = [row['id'] for row in cursor.fetchall()]

    if not active_users:
        flash("Нет активных игроков для раздачи карточек.", "warning")
        cursor.execute("UPDATE game_state SET game_in_progress = 0, on_table_status = 0, show_card_info = 0, current_leader_id = NULL, next_leader_id = NULL")
        db.commit()
        broadcast_game_update()
        return redirect(url_for('index'))

    cursor.execute("SELECT active_subfolder FROM game_state WHERE id = 1")
    game_state_for_subfolder = cursor.fetchone()
    active_subfolder = game_state_for_subfolder['active_subfolder'] if game_state_for_subfolder else None

    if not active_subfolder:
        flash("Не выбрана активная колода. Запустите новую игру через админ панель.", "danger")
        cursor.execute("UPDATE game_state SET game_in_progress = 0, on_table_status = 0, show_card_info = 0, current_leader_id = NULL, next_leader_id = NULL")
        db.commit()
        broadcast_game_update()
        return redirect(url_for('admin'))

    cursor.execute("SELECT id FROM images WHERE status = 'Свободно' AND subfolder = ?", (active_subfolder,))
    available_image_ids = [row['id'] for row in cursor.fetchall()]

    num_cards_per_player = 6
    required_cards = len(active_users) * num_cards_per_player

    if len(available_image_ids) < required_cards:
        flash(f"Недостаточно свободных карточек ({len(available_image_ids)}) в активной колоде '{active_subfolder}' для раздачи {required_cards} карточек. Выберите другую колоду или загрузите больше карточек.", "danger")
        broadcast_game_update()
        return redirect(url_for('admin'))


    random.shuffle(available_image_ids)

    deal_count = 0
    for user_id in active_users:
        for _ in range(num_cards_per_player):
            if deal_count < len(available_image_ids):
                image_id_to_deal = available_image_ids[deal_count]
                cursor.execute("UPDATE images SET status = ?, owner_id = ? WHERE id = ?", (f'Занято: {user_id}', user_id, image_id_to_deal))
                deal_count += 1
            else:
                print(f"Warning: Ran out of available images while dealing cards. Dealt {deal_count} out of {required_cards}.", file=sys.stderr)
                break
        if deal_count >= len(available_image_ids):
             break

    db.commit()

    cursor.execute("UPDATE game_state SET game_in_progress = 1, game_over = 0, on_table_status = 0, show_card_info = 0")
    db.commit()

    flash("Новый раунд начат! Карточки розданы.", "success")

    broadcast_game_update()

    return redirect(url_for('index'))


@app.route('/user/<code>/place/<int:image_id>', methods=['POST'])
def place_card(code, image_id):
    """Handle a player placing a card on the table."""
    db = get_db()
    c = db.cursor()

    c.execute("SELECT id, code, status FROM users WHERE code = ?", (code,))
    g.user = c.fetchone()
    if not g.user or g.user['status'] != 'active':
        flash("Неверный код пользователя или ваш статус не 'Активен'.", "danger")
        return redirect(url_for('index'))

    c.execute("SELECT game_in_progress, game_over, current_leader_id, active_subfolder, on_table_status, show_card_info FROM game_state WHERE id = 1")
    game_state = c.fetchone()

    if not game_state or not game_state['game_in_progress'] or game_state['game_over']:
        flash("Игра не в процессе.", "warning")
        broadcast_game_update(user_code_trigger=code)
        return redirect(url_for('index'))

    if game_state['show_card_info'] or game_state['on_table_status']:
        flash("Сейчас не фаза выкладывания карточек.", "warning")
        broadcast_game_update(user_code_trigger=code)
        return redirect(url_for('index'))


    current_leader_id = game_state['current_leader_id']
    active_subfolder = game_state['active_subfolder']

    if not active_subfolder:
         flash("Активная колода не выбрана. Свяжитесь с администратором.", "danger")
         broadcast_game_update(user_code_trigger=code)
         return redirect(url_for('index'))

    c.execute("SELECT id, subfolder, image, status, owner_id FROM images WHERE id = ? AND status = ? AND owner_id = ?", (image_id, f"Занято:{g.user['id']}", g.user['id']))
    card_to_place = c.fetchone()

    if not card_to_place:
        flash("Эта карточка не у вас в руке или уже на столе.", "warning")
        broadcast_game_update(user_code_trigger=code)
        return redirect(url_for('index'))

    c.execute("SELECT id FROM images WHERE owner_id = ? AND status LIKE 'На столе:%' AND subfolder = ?", (g.user['id'], active_subfolder))
    card_of_this_user_on_table = c.fetchone()

    if card_of_this_user_on_table:
        if card_of_this_user_on_table['id'] == image_id:
            flash("Эта карточка уже у вас на столе.", "info")
            broadcast_game_update(user_code_trigger=code)
            return redirect(url_for('index'))
        else:
            c.execute("UPDATE images SET status = ?, owner_id = ? WHERE id = ?", (f"Занято:{g.user['id']}", g.user['id'], card_of_this_user_on_table['id']))
            c.execute("DELETE FROM guesses WHERE image_id = ?", (card_of_this_user_on_table['id'],))
            flash(f"Предыдущая карточка возвращена в руку.", "info")


    c.execute("UPDATE images SET status = ?, owner_id = ? WHERE id = ?", (f"На столе:{g.user['id']}", g.user['id'], image_id))
    c.execute("DELETE FROM guesses WHERE image_id = ?", (image_id,))
    db.commit()

    flash(f"Ваша карточка '{card_to_place['image']}' выложена на стол.", "success")

    c.execute("SELECT id FROM users WHERE status = 'active'")
    active_player_ids = [row['id'] for row in c.fetchall()]
    active_players_count = len(active_player_ids)


    c.execute("SELECT COUNT(DISTINCT owner_id) FROM images WHERE status LIKE 'На столе:%'")
    placed_cards_distinct_owners_count = c.fetchone()[0]

    all_players_placed_cards = False
    if active_players_count > 0 and placed_cards_distinct_owners_count == active_players_count:
        all_players_placed_cards = True
    elif active_players_count == 1 and placed_cards_distinct_owners_count == 1:
         all_players_placed_cards = True


    if all_players_placed_cards and not game_state['on_table_status'] and not game_state['show_card_info']:
        c.execute("UPDATE game_state SET on_table_status = 1 WHERE id = 1")
        c.execute("DELETE FROM guesses")
        db.commit()
        flash("Все игроки выложили карточки! Начинается фаза угадывания.", "info")


    broadcast_game_update(user_code_trigger=code)

    return redirect(url_for('index'))


@app.route('/user/<code>/guess/<int:card_id>', methods=['POST'])
def guess_card(code, card_id):
    """Handle a player submitting a guess for a card on the table."""
    db = get_db()
    c = db.cursor()

    c.execute("SELECT id, code, status FROM users WHERE code = ?", (code,))
    g.user = c.fetchone()
    if not g.user or g.user['status'] != 'active':
        flash("Неверный код пользователя или ваш статус не 'Активен'.", "danger")
        return redirect(url_for('index'))

    c.execute("SELECT game_in_progress, game_over, current_leader_id, active_subfolder, on_table_status, show_card_info FROM game_state WHERE id = 1")
    game_state = c.fetchone()

    if not game_state or not game_state['game_in_progress'] or game_state['game_over'] or not game_state['on_table_status'] or game_state['show_card_info']:
        flash("Сейчас не фаза угадывания.", "warning")
        broadcast_game_update(user_code_trigger=code)
        return redirect(url_for('index'))

    guessed_user_id = request.form.get('guessed_user_id')
    if not guessed_user_id:
         flash("Выберите игрока, чью карточку, по вашему мнению, угадываете.", "warning")
         broadcast_game_update(user_code_trigger=code)
         return redirect(url_for('index'))

    try:
        guessed_user_id = int(guessed_user_id)
    except ValueError:
        flash("Неверный формат ID игрока.", "danger")
        broadcast_game_update(user_code_trigger=code)
        return redirect(url_for('index'))

    c.execute("SELECT id, owner_id FROM images WHERE id = ? AND status LIKE 'На столе:%' AND subfolder = ?", (card_id, game_state['active_subfolder']))
    card_on_table = c.fetchone()

    if not card_on_table:
        flash("Эта карточка больше не на столе или не из активной колоды.", "warning")
        broadcast_game_update(user_code_trigger=code)
        return redirect(url_for('index'))

    if card_on_table['owner_id'] == g.user['id']:
        flash("Вы не можете угадывать собственную карточку.", "warning")
        broadcast_game_update(user_code_trigger=code)
        return redirect(url_for('index'))

    c.execute("SELECT id, status FROM users WHERE id = ? AND status = 'active'", (guessed_user_id,))
    guessed_user = c.fetchone()

    c.execute("SELECT COUNT(*) FROM images WHERE owner_id = ? AND status LIKE 'На столе:%'", (guessed_user_id,))
    guessed_user_has_card_on_table = c.fetchone()[0] > 0


    if not guessed_user or not guessed_user_has_card_on_table:
         flash("Выбранный игрок не активен или не выложил карточку на стол.", "warning")
         broadcast_game_update(user_code_trigger=code)
         return redirect(url_for('index'))

    c.execute("""
        SELECT g.image_id, i.owner_id
        FROM guesses g
        JOIN images i ON g.image_id = i.id
        WHERE g.user_id = ?
          AND g.image_id != ?
          AND g.guessed_user_id = ?
          AND i.status LIKE 'На столе:%'
    """, (g.user['id'], card_id, guessed_user_id))
    existing_guess_for_other_card_with_same_owner = c.fetchone()

    if existing_guess_for_other_card_with_same_owner:
        conflicting_image_id = existing_guess_for_other_card_with_same_owner['image_id']
        c.execute("SELECT image FROM images WHERE id = ?", (conflicting_image_id,))
        conflicting_image_row = c.fetchone()
        conflicting_image_name = conflicting_image_row['image'] if conflicting_image_row else f"ID {conflicting_image_id}"

        flash(f"Вы уже предположили, что карточка '{conflicting_image_name}' принадлежит этому игроку. Выберите другого игрока для текущей карточки или измените то предположение.", "warning")
        broadcast_game_update(user_code_trigger=code)
        return redirect(url_for('index'))

    c.execute("SELECT id FROM guesses WHERE user_id = ? AND image_id = ?", (g.user['id'], card_id))
    existing_guess = c.fetchone()

    if existing_guess:
        c.execute("UPDATE guesses SET guessed_user_id = ? WHERE id = ?", (guessed_user_id, existing_guess['id']))
        flash(f"Ваше предположение для карточки изменено.", "success")
    else:
        c.execute("INSERT INTO guesses (user_id, image_id, guessed_user_id) VALUES (?, ?, ?)", (g.user['id'], card_id, guessed_user_id))
        flash(f"Ваше предположение для карточки сохранено.", "success")

    db.commit()

    c.execute("SELECT id FROM users WHERE status = 'active'")
    active_player_ids = [row['id'] for row in c.fetchall()]
    num_active_players = len(active_player_ids)

    c.execute("SELECT id, owner_id FROM images WHERE status LIKE 'На столе:%'")
    table_cards_with_owners = c.fetchall()
    table_card_ids = [card['id'] for card in table_cards_with_owners]
    num_cards_on_table = len(table_card_ids)

    total_required_guesses = 0
    for player_id in active_player_ids:
        player_required_guesses = sum(1 for card in table_cards_with_owners if card['owner_id'] != player_id)
        total_required_guesses += player_required_guesses

    actual_guesses_count = 0
    if table_card_ids:
        c.execute("SELECT COUNT(*) FROM guesses WHERE image_id IN ({})".format(','.join('?' * len(table_card_ids))), table_card_ids)
        actual_guesses_count = c.fetchone()[0]

    all_guesses_for_trigger_check = []
    if table_card_ids:
         c.execute("SELECT user_id, guessed_user_id, image_id FROM guesses WHERE image_id IN ({})".format(','.join('?' * len(table_card_ids))), table_card_ids)
         all_guesses_for_trigger_check = c.fetchall()

    guesses_grouped_by_user = {}
    for guess in all_guesses_for_trigger_check:
        user_id = guess['user_id']
        if user_id not in guesses_grouped_by_user:
            guesses_grouped_by_user[user_id] = []
        guesses_grouped_by_user[user_id].append(guess['guessed_user_id'])

    uniqueness_check_passed = True
    for user_id, guessed_owners in guesses_grouped_by_user.items():
        if len(guessed_owners) > 1:
             if len(guessed_owners) != len(set(guessed_owners)):
                 uniqueness_check_passed = False
                 break


    print("--- Проверка автоперехода ---", file=sys.stderr)
    print(f"Активных игроков: {num_active_players}", file=sys.stderr)
    print(f"Карточек на столе: {num_cards_on_table}", file=sys.stderr)
    print(f"Всего требуется предположений: {total_required_guesses}", file=sys.stderr)
    print(f"Фактически сделано предположений: {actual_guesses_count}", file=sys.stderr)
    print(f"Проверка уникальности пройдена (для игроков с >1 предположением): {uniqueness_check_passed}", file=sys.stderr)
    print(f"Состояние игры: on_table_status={game_state['on_table_status']}, show_card_info={game_state['show_card_info']}", file=sys.stderr)
    if num_active_players == 1 and active_player_ids:
         print(f"Единственный активный игрок ID: {active_player_ids[0]}, Ведущий ID: {game_state['current_leader_id']}", file=sys.stderr)
    print("-----------------------------", file=sys.stderr)


    should_auto_trigger = False

    if game_state['on_table_status'] and not game_state['show_card_info']:
        if num_active_players > 1:
            if actual_guesses_count == total_required_guesses and uniqueness_check_passed:
                 should_auto_trigger = True
                 flash("Все игроки сделали предположения! Карточки открываются и подсчитываются очки.", "info")
                 print("Автоматический переход к подсчету очков: Все игроки сделали необходимые и уникальные предположения.", file=sys.stderr)

        elif num_active_players == 1:
             if active_player_ids and active_player_ids[0] == game_state['current_leader_id']:
                  should_auto_trigger = True
                  flash("Нет других игроков для угадывания. Переход к подсчету.", "info")
                  print("Автоматический переход к подсчету очков: Нет других игроков.", file=sys.stderr)


    if should_auto_trigger:
        c.execute("UPDATE game_state SET show_card_info = 1 WHERE id = 1")
        db.commit()
        broadcast_game_update()
        end_round()


    broadcast_game_update(user_code_trigger=code)

    return redirect(url_for('index'))


@app.route('/user/<code>')
def user(code):
    db = get_db()
    c = db.cursor()
    c.execute("SELECT id, code, name, is_admin, rating, status FROM users WHERE code = ?", (code,))
    g.user = c.fetchone()

    if g.user:
        return render_template('user.html', user_data_for_init=dict(g.user))
    else:
        flash(f"Неверный код пользователя: {code}", "danger")
        return redirect(url_for('index'))


@app.route('/admin')
def admin():
    if not session.get('is_admin'):
         flash("Доступ к админ панели ограничен.", "danger")
         return redirect(url_for('index'))

    db = get_db()
    c = db.cursor()
    c.execute("SELECT id, code, name, is_admin, rating, status FROM users")
    all_users = c.fetchall()
    c.execute("SELECT subfolder, name FROM decks")
    all_decks = c.fetchall()
    c.execute("SELECT active_subfolder FROM game_state WHERE id = 1")
    game_state = c.fetchone()
    active_subfolder = game_state['active_subfolder'] if game_state else None


    deck_images = []
    if active_subfolder:
         c.execute("SELECT id, image, status, owner_id FROM images WHERE subfolder = ?", (active_subfolder,))
         deck_images = c.fetchall()

    current_leader_name = None
    if game_state and game_state['current_leader_id']:
         current_leader_name = get_user_name_by_id(game_state['current_leader_id'])


    game_board_data = []
    # Get board config from app.config, fallback to default constant
    board_config_admin = app.config.get('BOARD_VISUAL_CONFIG', _DEFAULT_BOARD_CONFIG_CONSTANT)
    current_num_board_cells_admin = app.config.get('NUM_BOARD_CELLS', DEFAULT_NUM_BOARD_CELLS)

    try:
         c.execute("SELECT id, name, rating FROM users WHERE status = 'active'")
         active_users_for_board_admin = {row['id']: dict(row) for row in c.fetchall()}
         active_users_list_admin = list(active_users_for_board_admin.values())
         active_users_list_admin.sort(key=lambda x: x['rating'])

         # We need the full board config from DB results again to calculate min_rating correctly,
         # or reuse the one from app.config if it was loaded. Let's fetch again for clarity/safety here.
         cursor.execute("SELECT id, image, max_rating FROM game_board_visuals ORDER BY id")
         board_config_rows_admin = cursor.fetchall() # Use this for calculating min_rating

         if board_config_admin: # Use board_config_admin (from app.config or default) for iteration
            for cell_config in board_config_admin:
                cell_data = {
                    'cell_number': cell_config['id'],
                    'image_path': os.path.join(GAME_BOARD_POLE_IMG_SUBFOLDER, cell_config['image']),
                    'max_rating': cell_config['max_rating'],
                    'users_in_cell': []
                }
                # Find users in this cell based on rating range using board_config_rows_admin (from DB)
                min_rating = 0 if cell_config['id'] == 1 else board_config_rows_admin[cell_config['id']-2]['max_rating'] + 1 if cell_config['id']-2 >= 0 and cell_config['id']-2 < len(board_config_rows_admin) else board_config_admin[cell_config['id']-2]['max_rating'] + 1 # Safer indexing
                max_rating = cell_config['max_rating']

                users_in_this_cell = [user for user in active_users_list_admin if user['rating'] >= min_rating and user['rating'] <= max_rating]
                cell_data['users_in_cell'] = users_in_this_cell

                game_board_data.append(cell_data)

    except Exception as e:
         print(f"Error loading board data for admin panel: {e}", file=sys.stderr)
         game_board_data = []


    return render_template('admin.html',
                           all_users=all_users,
                           all_decks=all_decks,
                           active_subfolder=active_subfolder,
                           deck_images=deck_images,
                           current_leader_name=current_leader_name,
                           game_state=game_state,
                           game_board=game_board_data,
                           current_num_board_cells=current_num_board_cells_admin
                           )


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        admin_code = request.form.get('admin_code')
        db = get_db()
        c = db.cursor()
        c.execute("SELECT id, code, name FROM users WHERE code = ? AND is_admin = TRUE", (admin_code,))
        admin_user = c.fetchone()

        if admin_user:
            session['is_admin'] = True
            session['user_code'] = admin_user['code']
            flash(f"Добро пожаловать, {admin_user['name']} (Администратор)!", "success")
            return redirect(url_for('admin'))
        else:
            flash("Неверный код администратора.", "danger")

    return render_template('admin_login.html')


@app.route('/admin/logout')
def admin_logout():
    session.pop('user_code', None)
    session.pop('is_admin', None)
    flash("Вы вышли из аккаунта.", "info")
    return redirect(url_for('index'))


@app.route('/create_user', methods=['POST'])
def create_user():
    user_name = request.form.get('user_name')
    is_admin = request.form.get('is_admin') == 'on'

    if not user_name:
        flash("Имя пользователя не может быть пустым.", "warning")
        return redirect(url_for('admin'))

    user_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

    db = get_db()
    c = db.cursor()

    try:
        c.execute("INSERT INTO users (code, name, is_admin) VALUES (?, ?, ?)", (user_code, user_name, is_admin))
        db.commit()
        flash(f"Пользователь '{user_name}' создан с кодом: {user_code}.{' (Админ)' if is_admin else ''}", "success")
    except sqlite3.IntegrityError:
        flash("Ошибка при создании пользователя. Возможно, такой код уже существует (попробуйте снова).", "danger")
    except Exception as e:
        flash(f"Произошла ошибка при создании пользователя: {e}", "danger")
        print(f"Error creating user: {e}", file=sys.stderr)

    return redirect(url_for('admin'))


@app.route('/admin/set_user_status/<int:user_id>/<status>', methods=['POST'])
def admin_set_user_status(user_id, status):
     if not session.get('is_admin'):
         flash("Недостаточно прав.", "danger")
         return redirect(url_for('index'))

    if status not in ['pending', 'active', 'inactive']:
        flash("Неверный статус.", "warning")
        return redirect(url_for('admin'))

    db = get_db()
    c = db.cursor()
    c.execute("UPDATE users SET status = ? WHERE id = ?", (status, user_id))
    db.commit()
    flash(f"Статус пользователя ID {user_id} изменен на '{status}'.", "success")

    broadcast_game_update()

    return redirect(url_for('admin'))


@app.route('/admin/delete_user/<int:user_id>', methods=['POST'])
def admin_delete_user(user_id):
     if not session.get('is_admin'):
         flash("Недостаточно прав.", "danger")
         return redirect(url_for('index'))

    db = get_db()
    c = db.cursor()
    try:
        c.execute("DELETE FROM guesses WHERE user_id = ?", (user_id,))
        c.execute("UPDATE images SET status = 'Свободно', owner_id = NULL WHERE status LIKE 'На столе:%' AND owner_id = ?", (user_id,))
        c.execute("UPDATE game_state SET current_leader_id = NULL WHERE current_leader_id = ?", (user_id,))
        c.execute("UPDATE game_state SET next_leader_id = NULL WHERE next_leader_id = ?", (user_id,))

        c.execute("DELETE FROM users WHERE id = ?", (user_id,))
        db.commit()
        flash(f"Пользователь ID {user_id} удален.", "success")

        broadcast_game_update()

    except Exception as e:
        flash(f"Ошибка при удалении пользователя ID {user_id}: {e}", "danger")
        print(f"Error deleting user: {e}", file=sys.stderr)


    return redirect(url_for('admin'))


@app.route('/admin/create_deck', methods=['POST'])
def admin_create_deck():
     if not session.get('is_admin'):
         flash("Недостаточно прав.", "danger")
         return redirect(url_for('index'))

    deck_name = request.form.get('deck_name')
    subfolder_name = request.form.get('subfolder_name')

    if not deck_name or not subfolder_name:
        flash("Название колоды и папка не могут быть пустыми.", "warning")
        return redirect(url_for('admin'))

    if not re.match(r'^[a-zA-Z0-9_-]+$', subfolder_name):
         flash("Название папки может содержать только латинские буквы, цифры, дефисы и подчеркивания.", "warning")
         return redirect(url_for('admin'))


    deck_dir = os.path.join(app.static_folder, 'images', subfolder_name)
    try:
        os.makedirs(deck_dir, exist_ok=True)
        db = get_db()
        c = db.cursor()
        c.execute("SELECT COUNT(*) FROM decks WHERE subfolder = ?", (subfolder_name,))
        if c.fetchone()[0] > 0:
             flash(f"Колода с папкой '{subfolder_name}' уже существует.", "warning")
        else:
             c.execute("INSERT INTO decks (subfolder, name) VALUES (?, ?)", (subfolder_name, deck_name))
             db.commit()
             flash(f"Колода '{deck_name}' ({subfolder_name}) создана.", "success")

    except OSError as e:
        flash(f"Ошибка при создании папки колоды: {e}", "danger")
    except sqlite3.IntegrityError:
         flash(f"Ошибка при создании колоды. Папка '{subfolder_name}' уже зарегистрирована в базе данных.", "danger")
    except Exception as e:
        flash(f"Произошла ошибка при создании колоды: {e}", "danger")
        print(f"Error creating deck: {e}", file=sys.stderr)

    return redirect(url_for('admin'))


@app.route('/admin/delete_deck/<subfolder>', methods=['POST'])
def admin_delete_deck(subfolder):
     if not session.get('is_admin'):
         flash("Недостаточно прав.", "danger")
         return redirect(url_for('index'))

    db = get_db()
    c = db.cursor()

    c.execute("SELECT active_subfolder FROM game_state WHERE id = 1")
    game_state = c.fetchone()
    if game_state and game_state['active_subfolder'] == subfolder:
        flash(f"Нельзя удалить активную колоду ('{subfolder}').", "warning")
        return redirect(url_for('admin'))


    try:
        c.execute("DELETE FROM images WHERE subfolder = ?", (subfolder,))
        c.execute("DELETE FROM decks WHERE subfolder = ?", (subfolder,))
        db.commit()

        deck_dir = os.path.join(app.static_folder, 'images', subfolder)
        if os.path.exists(deck_dir):
             try:
                 os.rmdir(deck_dir)
                 print(f"Directory {deck_dir} removed.", file=sys.stderr)
             except OSError as e:
                  print(f"Warning: Could not remove directory {deck_dir}. It might not be empty: {e}", file=sys.stderr)
                  flash(f"Колода удалена из базы данных, но папка '{subfolder}' не была пустой и не удалена на сервере.", "warning")


        flash(f"Колода '{subfolder}' удалена (если папка была пустой).", "success")

    except Exception as e:
        flash(f"Ошибка при удалении колоды '{subfolder}': {e}", "danger")
        print(f"Error deleting deck: {e}", file=sys.stderr)

    return redirect(url_for('admin'))


@app.route('/admin/upload_images/<subfolder>', methods=['POST'])
def admin_upload_images(subfolder):
     if not session.get('is_admin'):
         flash("Недостаточно прав.", "danger")
         return redirect(url_for('index'))

    db = get_db()
    c = db.cursor()

    c.execute("SELECT COUNT(*) FROM decks WHERE subfolder = ?", (subfolder,))
    if c.fetchone()[0] == 0:
        flash(f"Колода '{subfolder}' не найдена.", "danger")
        return redirect(url_for('admin'))

    files = request.files.getlist('images')
    uploaded_count = 0
    skipped_count = 0
    errors = []

    deck_dir = os.path.join(app.static_folder, 'images', subfolder)
    os.makedirs(deck_dir, exist_ok=True)


    for file in files:
        if file and file.filename:
            allowed_extensions = {'png', 'jpg', 'jpeg', 'gif'}
            if '.' in file.filename and file.filename.rsplit('.', 1)[1].lower() in allowed_extensions:
                filename = file.filename
                filepath = os.path.join(deck_dir, filename)

                c.execute("SELECT COUNT(*) FROM images WHERE subfolder = ? AND image = ?", (subfolder, filename))
                if c.fetchone()[0] > 0:
                    skipped_count += 1
                    errors.append(f"Изображение '{filename}' уже существует в этой колоде.")
                    continue

                try:
                    file.save(filepath)
                    c.execute("INSERT INTO images (subfolder, image, status, owner_id) VALUES (?, ?, 'Свободно', NULL)", (subfolder, filename))
                    db.commit()
                    uploaded_count += 1
                except Exception as e:
                    errors.append(f"Ошибка при загрузке файла '{filename}': {e}")
                    print(f"Error saving or inserting image {filename}: {e}", file=sys.stderr)
                    if os.path.exists(filepath):
                        try: os.remove(filepath)
                        except: pass

            else:
                skipped_count += 1
                errors.append(f"Файл '{file.filename}' имеет недопустимое расширение.")

    db.commit()


    if uploaded_count > 0:
        flash(f"Успешно загружено {uploaded_count} изображени(е/й) в колоду '{subfolder}'.", "success")
    if skipped_count > 0:
        flash(f"Пропущено {skipped_count} файлов.", "warning")
    if errors:
        for error in errors:
            flash(error, "danger")

    return redirect(url_for('admin'))


@app.route('/admin/delete_image/<int:image_id>', methods=['POST'])
def admin_delete_image(image_id):
     if not session.get('is_admin'):
         flash("Недостаточно прав.", "danger")
         return redirect(url_for('index'))

    db = get_db()
    c = db.cursor()

    try:
        c.execute("SELECT subfolder, image FROM images WHERE id = ?", (image_id,))
        image_info = c.fetchone()

        if not image_info:
            flash("Изображение не найдено.", "warning")
            return redirect(url_for('admin'))

        subfolder = image_info['subfolder']
        filename = image_info['image']
        filepath = os.path.join(app.static_folder, 'images', subfolder, filename)

        c.execute("DELETE FROM images WHERE id = ?", (image_id,))
        c.execute("DELETE FROM guesses WHERE image_id = ?", (image_id,))
        db.commit()

        if os.path.exists(filepath):
            try:
                os.remove(filepath)
                print(f"File {filepath} removed.", file=sys.stderr)
            except Exception as e:
                print(f"Warning: Could not remove file {filepath}: {e}", file=sys.stderr)
                flash(f"Изображение удалено из базы данных, но файл '{filename}' не удален на сервере.", "warning")


        flash(f"Изображение '{filename}' удалено из колоды '{subfolder}'.", "success")

    except Exception as e:
        flash(f"Ошибка при удалении изображения ID {image_id}: {e}", "danger")
        print(f"Error deleting image: {e}", file=sys.stderr)

    return redirect(url_for('admin'))


@app.route('/')
def index():
    db = get_db()
    c = db.cursor()
    c.execute("SELECT subfolder, name FROM decks")
    all_decks = c.fetchall()
    c.execute("SELECT active_subfolder FROM game_state WHERE id = 1")
    game_state = c.fetchone()
    active_subfolder = game_state['active_subfolder'] if game_state else None

    user_code = session.get('user_code')
    user_data = None
    if user_code:
         c.execute("SELECT id, code, name, is_admin, rating, status FROM users WHERE code = ?", (user_code,))
         user_data = c.fetchone()
         if not user_data:
              session.pop('user_code', None)
              session.pop('is_admin', None)
              flash("Ваша сессия устарела, пожалуйста, войдите снова.", "warning")


    return render_template('index.html', all_decks=all_decks, active_subfolder=active_subfolder, user_data=user_data)


@app.route('/user_login', methods=['POST'])
def user_login():
    user_code = request.form.get('user_code')
    db = get_db()
    c = db.cursor()
    c.execute("SELECT id, code, name, is_admin, rating, status FROM users WHERE code = ?", (user_code,))
    user_data = c.fetchone()

    if user_data:
        session['user_code'] = user_data['code']
        if user_data['is_admin']:
             session['is_admin'] = True
        flash(f"Добро пожаловать, {user_data['name']}!", "success")
        return redirect(url_for('user', code=user_data['code']))
    else:
        flash("Неверный код пользователя.", "danger")
        return redirect(url_for('index'))

@app.route('/user_logout', methods=['POST'])
def user_logout():
    session.pop('user_code', None)
    session.pop('is_admin', None)
    flash("Вы вышли из аккаунта.", "info")
    return redirect(url_for('index'))


@app.route('/admin/set_active_deck/<subfolder>', methods=['POST'])
def admin_set_active_deck_route(subfolder):
     if not session.get('is_admin'):
         flash("Недостаточно прав.", "danger")
         return redirect(url_for('index'))

     db = get_db()
     c = db.cursor()

     c.execute("SELECT COUNT(*) FROM decks WHERE subfolder = ?", (subfolder,))
     if c.fetchone()[0] == 0:
          flash(f"Колода '{subfolder}' не найдена.", "danger")
          return redirect(url_for('admin'))

     c.execute("SELECT game_in_progress FROM game_state WHERE id = 1")
     game_state = c.fetchone()
     if game_state and game_state['game_in_progress']:
          flash("Нельзя сменить активную колоду во время игры.", "warning")
          return redirect(url_for('admin'))

     c.execute("UPDATE game_state SET active_subfolder = ? WHERE id = 1", (subfolder,))
     db.commit()
     flash(f"Активная колода изменена на '{subfolder}'.", "success")

     broadcast_game_update()

     return redirect(url_for('admin'))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    socketio.run(app, host="0.0.0.0", port=port, debug=debug, allow_unsafe_werkzeug=True)
