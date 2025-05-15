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
GAME_BOARD_POLE_IMAGES = [f"p{i}.jpg" for i in range(1, 8)]
# Assuming you have a configuration for board visuals like this:
_current_game_board_pole_image_config = [] # This should be loaded from somewhere or defined. Example structure: [{'id': 1, 'image': 'p1.jpg', 'max_rating': 5}, ...]
# Let's define a simple default if not loaded elsewhere:
_default_board_config = [
    {'id': 1, 'image': 'p1.jpg', 'max_rating': 5},
    {'id': 2, 'image': 'p2.jpg', 'max_rating': 10},
    {'id': 3, 'image': 'p3.jpg', 'max_rating': 15},
    {'id': 4, 'image': 'p4.jpg', 'max_rating': 20},
    {'id': 5, 'image': 'p5.jpg', 'max_rating': 25},
    {'id': 6, 'image': 'p6.jpg', 'max_rating': 30},
    {'id': 7, 'image': 'p7.jpg', 'max_rating': 35},
    # Add more as needed, up to DEFAULT_NUM_BOARD_CELLS logic
    {'id': 8, 'image': 'p8.jpg', 'max_rating': 40}, # Assuming 40 is the end
]
_current_game_board_pole_image_config = _default_board_config # Use default if not initialized otherwise

DEFAULT_NUM_BOARD_CELLS = 40 # This should ideally match the max_rating of the last board visual cell
_current_game_board_num_cells = DEFAULT_NUM_BOARD_CELLS # Initial global declaration


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
            FOREIGN KEY (subfolder) REFERENCES decks (subfolder)
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
            leader_pole_image_path TEXT, -- Added to store path of leader's board image
            leader_pictogram_rating INTEGER, -- Added to store leader's rating for pictogram
            current_num_board_cells INTEGER DEFAULT 40, -- Store num cells
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
        # Assuming _default_board_config is defined globally or loaded
        if _default_board_config:
             cursor.executemany("INSERT INTO game_board_visuals (id, image, max_rating) VALUES (?, ?, ?)",
                               [(cell['id'], cell['image'], cell['max_rating']) for cell in _default_board_config])
             db.commit()
             print(f"Inserted {len(_default_board_config)} game board visual entries.", file=sys.stderr)
        else:
             print("WARNING: _default_board_config is empty. Cannot initialize game board visuals.", file=sys.stderr)


    # Initialize the single game_state row if it doesn't exist
    cursor.execute("SELECT COUNT(*) FROM game_state WHERE id = 1")
    if cursor.fetchone()[0] == 0:
         cursor.execute("INSERT INTO game_state DEFAULT VALUES")
         db.commit()


    print('Database initialized or already exists.', file=sys.stderr)

# Removed the click command decorator
# @app.cli.command('init-db')
# def init_db_command():
#     """Clear existing data and create new tables."""
#     init_database() # Now calls the internal function
#     click.echo('Initialized the database.')


# --- Automatic Database Initialization and Board Visuals Loading on App Load ---
# This code runs when the app module is imported by Gunicorn or run directly
# It needs to be within app_context to perform DB operations
with app.app_context():
    init_database()

    # Load game board visuals into global variable _current_game_board_pole_image_config
    # This should happen after init_database has potentially created the table
    db = get_db()
    cursor = db.cursor()
    try:
         global _current_game_board_num_cells # <<< ИСПРАВЛЕНИЕ: Перемещено сюда

         cursor.execute("SELECT id, image, max_rating FROM game_board_visuals ORDER BY id")
         board_config_rows = cursor.fetchall()
         if board_config_rows:
              _current_game_board_pole_image_config = [dict(row) for row in board_config_rows]
              _current_game_board_num_cells = board_config_rows[-1]['max_rating']
              print("Loaded game board visuals from DB.", file=sys.stderr)
         else:
              # Should not happen if init_database ran and added defaults, but as a fallback
              print("WARNING: Game board visuals table is empty after initialization. Using default config.", file=sys.stderr)
              _current_game_board_pole_image_config = _default_board_config
              _current_game_board_num_cells = DEFAULT_NUM_BOARD_CELLS # Assignment here is fine after global


    except sqlite3.OperationalError as e:
         print(f"WARNING: Could not load game board visuals from DB on startup: {e}. Table might be missing despite init_database attempt.", file=sys.stderr)
         # Assign using default config if DB error
         _current_game_board_pole_image_config = _default_board_config
         _current_game_board_num_cells = DEFAULT_NUM_BOARD_CELLS
    except Exception as e:
         print(f"Error loading game board visuals on startup: {e}\n{traceback.format_exc()}", file=sys.stderr)
         # Assign using default config on other errors
         _current_game_board_pole_image_config = _default_board_config
         _current_game_board_num_cells = DEFAULT_NUM_BOARD_CELLS
# --- End of Automatic Initialization Block ---


def broadcast_game_update(user_code=None):
    """Sends the current game state to all connected users or a specific user."""
    # print(f"Broadcasting game update. Target user_code: {user_code}", file=sys.stderr)
    if user_code:
         # Send only to a specific user
         for sid, code in list(connected_users_socketio.items()): # Use list to avoid issues if dict changes during iteration
             if code == user_code:
                 try:
                    state = state_to_json(user_code_for_state=user_code)
                    emit('game_update', state, room=sid)
                    # print(f"Sent update to SID: {sid} ({user_code})", file=sys.stderr)
                 except Exception as e:
                     print(f"Error sending update to SID {sid} ({user_code}): {e}\n{traceback.format_exc()}", file=sys.stderr)

    else:
        # Broadcast to all connected users
        # Iterate through connected users to get user-specific state
        for sid, code in list(connected_users_socketio.items()): # Use list for safe iteration
             try:
                 user_specific_state = state_to_json(user_code_for_state=code)
                 emit('game_update', user_specific_state, room=sid)
                 # print(f"Sent update to SID: {sid} ({code})", file=sys.stderr)
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
        # If game_state row doesn't exist (should be created by init_database), return default
        # This fallback might not be needed if init_database runs on startup
         print("WARNING: game_state row (id=1) not found in DB!", file=sys.stderr)
         return {
            'game_in_progress': False,
            'game_over': False,
            'current_leader_name': None,
            'next_leader_name': None, # Include next_leader_name
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
            'current_num_board_cells': _current_game_board_num_cells, # Fallback value
            'leader_pole_pictogram_path': None,
            'leader_pictogram_rating_display': None,
        }


    game_in_progress = bool(game_state['game_in_progress'])
    game_over = bool(game_state['game_over'])
    current_leader_id = game_state['current_leader_id']
    on_table_status = bool(game_state['on_table_status'])
    show_card_info = bool(game_state['show_card_info'])
    # Fetch next_leader_id from game_state
    next_leader_id = game_state['next_leader_id']

    # Fetch current_num_board_cells from game_state if available, fallback to global
    current_num_board_cells = game_state['current_num_board_cells'] if 'current_num_board_cells' in game_state and game_state['current_num_board_cells'] is not None else _current_game_board_num_cells


    current_leader_name = get_user_name_by_id(current_leader_id) if current_leader_id else None
    next_leader_name = get_user_name_by_id(next_leader_id) if next_leader_id else None


    user_cards = []
    table_images = []
    all_users_for_guessing = []
    current_user_data = None
    flashed_messages_list = get_flashed_messages(with_categories=true) if user_code_for_state else []
    flashed_messages = [dict(msg) for msg in flashed_messages_list] # Convert to list of dicts


    if game_in_progress or game_over:
        # Fetch user cards for the specific user if code is provided and user is active
        if user_code_for_state:
            cursor.execute("SELECT id, name, rating, status FROM users WHERE code = ?", (user_code_for_state,))
            current_user = cursor.fetchone()
            if current_user:
                current_user_data = dict(current_user)
                if current_user_data['status'] == 'active':
                     cursor.execute("SELECT id, subfolder, image FROM images WHERE status = ?", (f'Занято: {current_user_data["id"]}',))
                     user_cards = [dict(row) for row in cursor.fetchall()]


        # Fetch images on the table
        cursor.execute("SELECT id, subfolder, image, status FROM images WHERE status LIKE 'На столе:%'")
        table_images_raw = cursor.fetchall()

        # Fetch all active users for guessing phase and other user info
        cursor.execute("SELECT id, name, rating FROM users WHERE status = 'active'")
        all_active_users = {row['id']: dict(row) for row in cursor.fetchall()}
        all_users_for_guessing = list(all_active_users.values()) # Provide list of users for frontend

        # Fetch guesses
        # Adjusted query to select only guesses related to cards currently on the table
        if table_images_raw:
             cursor.execute("SELECT user_id, guessed_user_id, image_id FROM guesses WHERE image_id IN ({})".format(','.join('?' * len(table_images_raw))), tuple(img['id'] for img in table_images_raw))
             all_guesses_raw = cursor.fetchall()
             # Group guesses by the card that was guessed ABOUT (image_id)
             all_guesses_by_card = {} # {image_id: [(user_id, guessed_user_id)]}
             for guess in all_guesses_raw:
                 card_guessed_about_id = guess['image_id']
                 if card_guessed_about_id not in all_guesses_by_card:
                     all_guesses_by_card[card_guessed_about_id] = []
                 all_guesses_by_card[card_guessed_about_id].append((guess['user_id'], guess['guessed_user_id']))

        else:
             all_guesses_raw = []
             all_guesses_by_card = {}


        # Augment table images with owner info and guesses if show_card_info or guessing phase
        if show_card_info or on_table_status: # on_table_status implies guessing phase might be starting/active
             for img in table_images_raw:
                owner_id_match = re.match(r'На столе: (\d+)', img['status'])
                if owner_id_match:
                    owner_id = int(owner_id_match.group(1))
                    owner_name = all_active_users.get(owner_id, {}).get('name', f'Игрок ID {owner_id}')
                    img_dict = dict(img)
                    img_dict['owner_id'] = owner_id
                    img_dict['owner_name'] = owner_name
                    # Include guesses related to this specific card
                    img_dict['guesses'] = {user_id: guessed_user_id for user_id, guessed_user_id in all_guesses_by_card.get(img_dict['id'], [])}


                    # Add the current user's guess for this specific card if available and user is active
                    if current_user_data and current_user_data['status'] == 'active':
                         current_user_id = current_user_data['id']
                         # Find the guess made by the current user ABOUT this card
                         for guess_entry in all_guesses_raw:
                             if guess_entry['user_id'] == current_user_id and guess_entry['image_id'] == img_dict['id']:
                                 img_dict['my_guess_for_this_card_value'] = guess_entry['guessed_user_id']
                                 break # Found the guess for this user for this card
                             else:
                                 img_dict['my_guess_for_this_card_value'] = None # Ensure it's set if no guess by user

                    table_images.append(img_dict)


    # Determine if all active players have placed a card for the guessing phase
    all_cards_placed_for_guessing_phase = False
    if game_in_progress and not game_over and current_leader_id is not None and on_table_status:
         cursor.execute("SELECT COUNT(*) FROM users WHERE status = 'active' AND id != ?", (current_leader_id,))
         active_players_count_excluding_leader = cursor.fetchone()[0]
         if active_players_count_excluding_leader > 0:
             # Count distinct owner_ids on the table that are not the leader's ID
             cursor.execute("SELECT COUNT(DISTINCT owner_id) FROM images WHERE status LIKE 'На столе:%' AND owner_id != ?", (current_leader_id,))
             placed_cards_count_excluding_leader = cursor.fetchone()[0]

             # Also check if the leader has placed their card
             cursor.execute("SELECT COUNT(*) FROM images WHERE status = ? AND owner_id = ?", (f'На столе: {current_leader_id}', current_leader_id))
             leader_card_is_on_table = cursor.fetchone()[0] > 0

             if placed_cards_count_excluding_leader == active_players_count_excluding_leader and leader_card_is_on_table:
                 all_cards_placed_for_guessing_phase = True
         elif active_players_count_excluding_leader == 0 and current_leader_id is not None:
             # Case: Only leader is active. Leader placing a card transitions to guessing/scoring implicitly
             cursor.execute("SELECT COUNT(*) FROM images WHERE status = ? AND owner_id = ?", (f'На столе: {current_leader_id}', current_leader_id))
             if cursor.fetchone()[0] > 0:
                  all_cards_placed_for_guessing_phase = True


    # Determine leader's board visual state based on rating
    leader_pole_image_path = None
    leader_pictogram_rating_display = None
    game_board_visual_config = [] # Fetch board visual config
    cursor.execute("SELECT id, image, max_rating FROM game_board_visuals ORDER BY id")
    board_config_rows = cursor.fetchall()
    if board_config_rows:
         game_board_visual_config = [dict(row) for row in board_config_rows]
         # Ensure global is updated, but be careful if this runs multiple times
         # global _current_game_board_num_cells # Already declared global at the top of this block

         _current_game_board_num_cells = game_board_visual_config[-1]['max_rating'] # Update global based on DB

         if current_leader_id is not None and (game_in_progress or game_over):
             cursor.execute("SELECT rating FROM users WHERE id = ?", (current_leader_id,))
             leader_rating_row = cursor.fetchone()
             if leader_rating_row:
                 leader_rating = leader_rating_row['rating']
                 leader_pictogram_rating_display = leader_rating
                 # Find the correct pictogram based on rating
                 for i in range(len(game_board_visual_config)):
                      if leader_rating <= game_board_visual_config[i]['max_rating']:
                          leader_pole_image_path = os.path.join(GAME_BOARD_POLE_IMG_SUBFOLDER, game_board_visual_config[i]['image'])
                          break
                 # If rating is higher than max, use the last pictogram
                 if leader_pole_image_path is None and game_board_visual_config:
                      leader_pole_image_path = os.path.join(GAME_BOARD_POLE_IMG_SUBFOLDER, game_board_visual_config[-1]['image'])
    else:
         # Fallback if board visuals not in DB (should be initialized by init_database)
         print("WARNING: Game board visuals table is empty in DB!", file=sys.stderr)
         _current_game_board_num_cells = DEFAULT_NUM_BOARD_CELLS # Use default fallback


    # Fetch game board state (users on cells)
    game_board_state = []
    if (game_in_progress or game_over) and game_board_visual_config:
         # Fetch active users with their ratings
         cursor.execute("SELECT id, name, rating FROM users WHERE status = 'active'")
         active_users_for_board = {row['id']: dict(row) for row in cursor.fetchall()}
         active_users_list = list(active_users_for_board.values()) # List of active users

         # Sort users by rating for easier board placement determination
         active_users_list.sort(key=lambda x: x['rating'])


         for cell_config in game_board_visual_config:
             cell_data = {
                 'cell_number': cell_config['id'],
                 'image_path': os.path.join(GAME_BOARD_POLE_IMG_SUBFOLDER, cell_config['image']),
                 'max_rating': cell_config['max_rating'],
                 'users_in_cell': []
             }
             # Find users in this cell based on rating range for this cell
             # Ensure indices are correct when using board_config_rows
             min_rating = 0 if cell_config['id'] == 1 else board_config_rows[cell_config['id']-2]['max_rating'] + 1 # Rating threshold from previous cell
             max_rating = cell_config['max_rating']

             users_in_this_cell = [user for user in active_users_list if user['rating'] >= min_rating and user['rating'] <= max_rating]
             cell_data['users_in_cell'] = users_in_this_cell

             game_board_state.append(cell_data)


    return {
        'game_in_progress': game_in_progress,
        'game_over': game_over,
        'current_leader_name': current_leader_name,
        'next_leader_name': next_leader_name, # Include next_leader_name in the state
        'on_table_status': on_table_status, # Indicates if cards are on the table for guessing
        'show_card_info': show_card_info, # Indicates if cards are revealed with owners/guesses
        'all_cards_placed_for_guessing_phase_to_template': all_cards_placed_for_guessing_phase,
        'user_cards': user_cards,
        'table_images': table_images,
        'all_users_for_guessing': all_users_for_guessing, # Active users for guessing
        'db_current_leader_id': current_leader_id,
        'current_user_data': current_user_data, # Data for the specific connected user
        'flashed_messages': flashed_messages,
        'game_board': game_board_state,
        'current_num_board_cells': current_num_board_cells, # Send the determined number of cells
        'leader_pole_pictogram_path': leader_pole_image_path, # Pass leader board image path
        'leader_pictogram_rating_display': leader_pictogram_rating_display, # Pass leader rating for pictogram
    }


# Helper function for internal end round logic (now called from route)
def end_round():
    """Calculates scores, updates ratings, determines next leader, and updates game state."""
    db = get_db()
    cursor = db.cursor()

    # Fetch game state
    cursor.execute("SELECT * FROM game_state WHERE id = 1")
    game_state = cursor.fetchone()
    # Check show_card_info status here as the route handler should ensure it's 1
    if not game_state or not game_state['game_in_progress'] or game_state['game_over'] or not game_state['show_card_info']:
         # This check is mostly redundant if called from end_round_route, but kept for safety
         print("Error: end_round called in invalid state.", file=sys.stderr)
         return


    current_leader_id = game_state['current_leader_id']
    active_subfolder = game_state['active_subfolder']


    # Fetch images on the table with their owners
    cursor.execute("SELECT id, owner_id FROM images WHERE status LIKE 'На столе:%' AND subfolder = ?", (active_subfolder,))
    table_images_raw = cursor.fetchall()
    table_image_owners = {img['id']: img['owner_id'] for img in table_images_raw}

    if not table_image_owners:
         flash("На столе нет карточек из активной колоды для подсчета очков.", "warning")
         # Reset table state if no cards found (shouldn't happen if show_card_info was true)
         cursor.execute("UPDATE images SET status = 'Свободно' WHERE status LIKE 'На столе:%' AND subfolder = ?", (active_subfolder,))
         cursor.execute("DELETE FROM guesses")
         cursor.execute("UPDATE game_state SET on_table_status = 0, show_card_info = 0, leader_pole_image_path = NULL, leader_pictogram_rating = NULL, current_num_board_cells = NULL, current_leader_id = NULL, next_leader_id = NULL")
         db.commit()
         broadcast_game_update() # Send update after reset
         return

    leader_card_id = None
    # Find the leader's card on the table
    for img_id, owner_id in table_image_owners.items():
        if owner_id == current_leader_id:
            leader_card_id = img_id
            break

    if leader_card_id is None:
        # This should not happen if leader placed a card and show_card_info is True
         flash("Ведущий не выложил карточку для подсчета очков.", "danger")
         # Reset table state
         cursor.execute("UPDATE images SET status = 'Свободно' WHERE status LIKE 'На столе:%' AND subfolder = ?", (active_subfolder,))
         cursor.execute("DELETE FROM guesses")
         cursor.execute("UPDATE game_state SET on_table_status = 0, show_card_info = 0, leader_pole_image_path = NULL, leader_pictogram_rating = NULL, current_num_board_cells = NULL, current_leader_id = NULL, next_leader_id = NULL")
         db.commit()
         broadcast_game_update() # Send update after reset
         return


    # Fetch all active players excluding the leader
    cursor.execute("SELECT id, rating FROM users WHERE status = 'active' AND id != ?", (current_leader_id,))
    players = cursor.fetchall()
    player_ids = [p['id'] for p in players]
    player_ratings = {p['id']: p['rating'] for p in players}

    # Fetch all guesses for cards currently on the table
    if table_image_owners:
         cursor.execute("SELECT user_id, guessed_user_id, image_id FROM guesses WHERE image_id IN ({})".format(','.join('?' * len(table_image_owners))), tuple(table_image_owners.keys()))
         all_guesses = cursor.fetchall()
    else:
         all_guesses = []


    # Process guesses and calculate scores
    leader_score_change = 0
    player_scores_change = {player_id: 0 for player_id in player_ids} # {player_id: score_change}
    correct_leader_guessers = [] # List of user_ids who correctly guessed the leader's card

    # Group guesses by the card that was guessed ABOUT (image_id)
    guesses_by_card_guessed_about = {} # {card_id: [(user_id, guessed_owner_id)]}
    for guess in all_guesses:
        card_guessed_about_id = guess['image_id']
        if card_guessed_about_id not in guesses_by_card_guessed_about:
            guesses_by_card_guessed_about[card_guessed_about_id] = []
        guesses_by_card_guessed_about[card_guessed_about_id].append((guess['user_id'], guess['guessed_user_id']))


    # Check guesses for the leader's card
    leader_card_guesses = guesses_by_card_guessed_about.get(leader_card_id, [])
    num_correct_leader_guesses = 0
    for guesser_id, guessed_owner_id in leader_card_guesses:
        if guessed_owner_id == current_leader_id:
            num_correct_leader_guesses += 1
            correct_leader_guessers.append(guesser_id)


    num_players_excluding_leader = len(player_ids)

    if num_players_excluding_leader > 0 and num_correct_leader_guesses == num_players_excluding_leader:
        # Case 1: All players guessed leader's card correctly (only if there are other players)
        leader_score_change = -3 # Move back 3 spaces
        flash(f"Все игроки угадали карточку Ведущего. Ведущий перемещается на 3 хода назад.", "info")
    elif num_correct_leader_guesses == 0:
        # Case 2: No players guessed leader's card correctly
        leader_score_change = -2 # Move back 2 spaces
        flash(f"Ни один игрок не угадал карточку Ведущего. Ведущий перемещается на 2 хода назад.", "info")
        # Players whose cards were guessed correctly by others still get points
        for card_id, owner_id in table_image_owners.items():
             if owner_id != current_leader_id: # Only consider player cards
                 guesses_about_this_card = guesses_by_card_guessed_about.get(card_id, [])
                 correct_guesses_for_this_player_card = [guesser_id for guesser_id, guessed_owner_id in guesses_about_this_card if guessed_owner_id == owner_id]
                 num_correct_guesses_for_this_player_card = len(correct_guesses_for_this_player_card)
                 if owner_id in player_scores_change:
                     player_scores_change[owner_id] += num_correct_guesses_for_this_player_card # 1 point per correct guess for their card
                     if num_correct_guesses_for_this_player_card > 0:
                         player_name = get_user_name_by_id(owner_id) or f'Игрок ID {owner_id}'
                         flash(f"Карточку игрока {player_name} угадали {num_correct_guesses_for_this_player_card} игрок(а).", "info")

    else:
        # Case 3: Some players guessed leader's card correctly (but not all)
        leader_score_change = 3 + num_correct_leader_guesses # 3 points + 1 per correct guesser
        flash(f"{num_correct_leader_guesses} игрок(а) угадали карточку Ведущего.", "info")
        for guesser_id in correct_leader_guessers:
             if guesser_id in player_scores_change:
                 player_scores_change[guesser_id] += 3 # 3 points for correctly guessing leader's card

        # Players whose cards were guessed correctly by others still get points
        for card_id, owner_id in table_image_owners.items():
             if owner_id != current_leader_id: # Only consider player cards
                 guesses_about_this_card = guesses_by_card_guessed_about.get(card_id, [])
                 correct_guesses_for_this_player_card = [guesser_id for guesser_id, guessed_owner_id in guesses_about_this_card if guessed_owner_id == owner_id]
                 num_correct_guesses_for_this_player_card = len(correct_guesses_for_this_player_card)
                 if owner_id in player_scores_change:
                     player_scores_change[owner_id] += num_correct_guesses_for_this_player_card # 1 point per correct guess for their card
                     if num_correct_guesses_for_this_player_card > 0:
                         player_name = get_user_name_by_id(owner_id) or f'Игрок ID {owner_id}'
                         flash(f"Карточку игрока {player_name} угадали {num_correct_guesses_for_this_player_card} игрок(а).", "info")


    # Apply score changes and update ratings
    cursor.execute("UPDATE users SET rating = MAX(0, rating + ?) WHERE id = ?", (leader_score_change, current_leader_id))
    db.commit() # Commit leader's score change immediately

    for player_id, score_change in player_scores_change.items():
        if score_change > 0: # Only update if score changed
             cursor.execute("UPDATE users SET rating = MAX(0, rating + ?) WHERE id = ?", (score_change, player_id))
             db.commit() # Commit each player's score change

    # Check for game over condition (if a player reached the end of the board)
    game_over = False
    # Fetch current_num_board_cells from game_state for accurate check
    cursor.execute("SELECT current_num_board_cells FROM game_state WHERE id = 1")
    game_state_cells_row = cursor.fetchone()
    current_num_board_cells = game_state_cells_row['current_num_board_cells'] if game_state_cells_row else DEFAULT_NUM_BOARD_CELLS

    cursor.execute("SELECT COUNT(*) FROM users WHERE status = 'active' AND rating >= ?", (current_num_board_cells,)) # Use fetched cell count
    players_at_end = cursor.fetchone()[0]
    if players_at_end > 0:
        game_over = True
        flash("Игра окончена! Игрок достиг конца игрового поля.", "success")
        # Ensure game state is updated correctly for game over
        cursor.execute("UPDATE game_state SET game_over = 1, game_in_progress = 0, on_table_status = 0, show_card_info = 0, current_leader_id = NULL, next_leader_id = NULL") # Reset leaders on game over
        db.commit()


    # Determine the next leader (user with the highest rating)
    # Only determine next leader if the game is NOT over
    next_leader_id = None
    if not game_over:
         cursor.execute("SELECT id FROM users WHERE status = 'active' ORDER BY rating DESC LIMIT 1")
         next_leader_row = cursor.fetchone()
         next_leader_id = next_leader_row['id'] if next_leader_row else None # Set to None if no active users


    # --- UPDATE game_state with next_leader_id and reset flags ---
    # Keep show_card_info = 1 here so frontend shows results until next round starts
    if not game_over: # Only update flags if game is not over
         cursor.execute("UPDATE game_state SET on_table_status = 0, show_card_info = 1, next_leader_id = ? WHERE id = 1", (next_leader_id,))
         db.commit()
    # else: game_state is already updated for game_over above


    # Reset image statuses from 'На столе' to 'Свободно'
    cursor.execute("UPDATE images SET status = 'Свободно' WHERE status LIKE 'На столе:%' AND subfolder = ?", (active_subfolder,))

    # Delete all guesses
    cursor.execute("DELETE FROM guesses")

    db.commit() # Commit remaining changes


    if not game_over:
         # Flash message set earlier
         pass
    # If game_over, the game over flash message was already set.
    broadcast_game_update()


# Example function to start a new round (needs logic to select images, assign to players, set leader, etc.)
# This function should likely be triggered by the next leader or an admin after end_round
@app.route('/start_new_round', methods=['POST'])
def start_new_round():
    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT * FROM game_state WHERE id = 1")
    game_state = cursor.fetchone()

    if game_state and game_state['game_in_progress'] and not game_state['game_over']:
        flash("Игра уже в процессе.", "warning")
        # Ideally, check if the current leader or admin is triggering this
        return redirect(url_for('index')) # Redirect or return appropriate response

    if game_state and game_state['game_over']:
         flash("Игра окончена. Запустите новую игру через админ панель.", "warning")
         return redirect(url_for('index')) # Or redirect to admin panel

    # --- Transition from next_leader_id to current_leader_id ---
    # Fetch the determined next leader from the previous round
    next_leader_id = game_state['next_leader_id'] if game_state and 'next_leader_id' in game_state else None
    if next_leader_id is None:
         # If no next leader is set (e.g., first round or after game over), determine initial leader randomly from active players
         cursor.execute("SELECT id FROM users WHERE status = 'active' ORDER BY RANDOM() LIMIT 1")
         initial_leader_row = cursor.fetchone()
         current_leader_id = initial_leader_row['id'] if initial_leader_row else None
         if current_leader_id is None:
              flash("Недостаточно активных игроков для начала раунда.", "warning")
              # Reset game state to ensure it's not stuck in a 'starting' state
              cursor.execute("UPDATE game_state SET game_in_progress = 0, on_table_status = 0, show_card_info = 0, current_leader_id = NULL, next_leader_id = NULL")
              db.commit()
              broadcast_game_update()
              return redirect(url_for('index')) # Or handle appropriately
         # Clear next_leader_id as the initial leader is now the current one
         cursor.execute("UPDATE game_state SET current_leader_id = ?, next_leader_id = NULL WHERE id = 1", (current_leader_id,))

    else:
         # Use the determined next leader from the previous round as the current leader
         current_leader_id = next_leader_id
         # Clear next_leader_id as it's now the current leader for this round
         cursor.execute("UPDATE game_state SET current_leader_id = ?, next_leader_id = NULL WHERE id = 1", (current_leader_id,))


    db.commit() # Commit leader update

    # --- END Transition ---


    # Reset image statuses
    cursor.execute("UPDATE images SET status = 'Свободно'")

    # Delete all guesses
    cursor.execute("DELETE FROM guesses")
    db.commit()

    # Deal cards to active players (each needs 6 cards - adjust as per rules)
    cursor.execute("SELECT id FROM users WHERE status = 'active'")
    active_users = [row['id'] for row in cursor.fetchall()]

    if not active_users:
        flash("Нет активных игроков для раздачи карточек.", "warning")
        # Reset game state if no active players
        cursor.execute("UPDATE game_state SET game_in_progress = 0, on_table_status = 0, show_card_info = 0, current_leader_id = NULL, next_leader_id = NULL")
        db.commit()
        broadcast_game_update()
        return redirect(url_for('index'))


    # Select available images from the active subfolder (fetch active_subfolder from game_state)
    cursor.execute("SELECT active_subfolder FROM game_state WHERE id = 1")
    game_state_for_subfolder = cursor.fetchone()
    active_subfolder = game_state_for_subfolder['active_subfolder'] if game_state_for_subfolder else None

    if not active_subfolder:
        flash("Не выбрана активная колода. Запустите новую игру через админ панель.", "danger")
        # Reset game state
        cursor.execute("UPDATE game_state SET game_in_progress = 0, on_table_status = 0, show_card_info = 0, current_leader_id = NULL, next_leader_id = NULL")
        db.commit()
        broadcast_game_update()
        return redirect(url_for('admin')) # Redirect to admin to choose deck

    # Check if there are enough free cards in the active subfolder
    cursor.execute("SELECT id FROM images WHERE status = 'Свободно' AND subfolder = ?", (active_subfolder,))
    available_image_ids = [row['id'] for row in cursor.fetchall()]

    num_cards_per_player = 6 # Adjust as per your game rules
    required_cards = len(active_users) * num_cards_per_player

    if len(available_image_ids) < required_cards:
        flash(f"Недостаточно свободных карточек ({len(available_image_ids)}) в активной колоде '{active_subfolder}' для раздачи {required_cards} карточек. Выберите другую колоду или загрузите больше карточек.", "danger")
        # Reset game state (or just prevent round start)
        # For now, just prevent start and keep existing state
        # cursor.execute("UPDATE game_state SET game_in_progress = 0, on_table_status = 0, show_card_info = 0, current_leader_id = NULL, next_leader_id = NULL")
        # db.commit()
        broadcast_game_update() # Update clients that round didn't start
        return redirect(url_for('admin')) # Redirect to admin to fix deck issue


    # Shuffle available image IDs
    random.shuffle(available_image_ids)

    # Deal cards
    deal_count = 0
    for user_id in active_users:
        for _ in range(num_cards_per_player):
            if deal_count < len(available_image_ids):
                image_id_to_deal = available_image_ids[deal_count]
                cursor.execute("UPDATE images SET status = ? WHERE id = ?", (f'Занято: {user_id}', image_id_to_deal))
                deal_count += 1
            else:
                print(f"Warning: Ran out of available images while dealing cards. Dealt {deal_count} out of {required_cards}.", file=sys.stderr)
                break # Should not happen if check above is correct
        if deal_count >= len(available_image_ids):
             break # Stop dealing if images run out

    db.commit()

    # Update game state to indicate game is in progress and reset flags
    cursor.execute("UPDATE game_state SET game_in_progress = 1, game_over = 0, on_table_status = 0, show_card_info = 0") # current_leader_id and next_leader_id handled above
    db.commit()

    flash("Новый раунд начат! Карточки розданы.", "success")

    # Broadcast game update to all connected users
    broadcast_game_update()

    return redirect(url_for('index')) # Redirect or return appropriate response


# Route for admin to start a new game (resets ratings, status, etc.)
# This should reset the game state to a fresh start, ready for admin setup
@app.route('/admin/start_new_game', methods=['POST'])
def start_new_game():
    db = get_db()
    cursor = db.cursor()

    # Reset user ratings and status (set all to pending)
    cursor.execute("UPDATE users SET rating = 0, status = 'pending'")

    # Reset image statuses
    cursor.execute("UPDATE images SET status = 'Свободно'")

    # Delete all guesses
    cursor.execute("DELETE FROM guesses")

    # Reset game state to initial state
    # No leader set at the very beginning, deck needs to be chosen
    cursor.execute("""
        UPDATE game_state SET
        game_in_progress = FALSE,
        game_over = FALSE,
        current_leader_id = NULL, -- No leader at the very start
        active_subfolder = NULL, -- Active deck needs to be chosen again
        on_table_status = FALSE,
        show_card_info = FALSE,
        next_leader_id = NULL, -- Ensure next_leader_id is NULL at the start of a new game
        leader_pole_image_path = NULL,
        leader_pictogram_rating = NULL
        WHERE id = 1
    """)

    db.commit()

    flash("Новая игра начата! Все рейтинги сброшены, статус игроков 'ожидание'. Выберите колоду и активируйте игроков.", "success")

    # Broadcast game update to all connected users (state will be reset)
    broadcast_game_update()

    return redirect(url_for('admin')) # Redirect back to admin panel


# SocketIO event handlers

@socketio.on('connect')
def handle_connect():
    sid = request.sid
    user_code = request.args.get('user_code') # Get user_code from connection arguments

    if not user_code:
        print(f"SocketIO: Connection attempt without user_code: SID={sid}", file=sys.stderr)
        emit('message', {'data': 'Ошибка подключения: не указан код пользователя.', 'category': 'danger'})
        return

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT id, status FROM users WHERE code = ?", (user_code,))
    user = cursor.fetchone()

    if not user:
        print(f"SocketIO: Connection attempt with invalid user_code: {user_code}, SID={sid}", file=sys.stderr)
        emit('message', {'data': 'Ошибка подключения: неверный код пользователя.', 'category': 'danger'})
        return

    connected_users_socketio[sid] = user_code
    print(f"SocketIO: Client connected: SID={sid}, User code: {user_code}", file=sys.stderr)

    try:
        # state_to_json now includes next_leader_name if it's stored in game_state
        initial_state = state_to_json(user_code_for_state=user_code)

        emit('game_update', initial_state, room=sid)
    except Exception as e:
        print(f"SocketIO: Error sending initial state to {sid}: {e}\n{traceback.format_exc()}", file=sys.stderr)


@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    user_code = connected_users_socketio.pop(sid, None)
    print(f"SocketIO: Client disconnected: SID={sid}, User code: {user_code or 'N/A'}", file=sys.stderr)


# SocketIO event for admin to activate/deactivate user (needs user code and new status)
@socketio.on('set_user_status')
def handle_set_user_status(data):
    sid = request.sid
    admin_user_code = connected_users_socketio.get(sid)
    if not admin_user_code:
         print(f"SocketIO: Unauthorized status change attempt from SID: {sid}", file=sys.stderr)
         emit('message', {'data': 'Вы не авторизованы как администратор.', 'category': 'danger'}, room=sid)
         return

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE code = ?", (admin_user_code,))
    admin_user = cursor.fetchone()

    if not admin_user or not admin_user['is_admin']:
         print(f"SocketIO: Unauthorized status change attempt from non-admin user: {admin_user_code} (SID: {sid})", file=sys.stderr)
         emit('message', {'data': 'У вас нет прав администратора.', 'category': 'danger'}, room=sid)
         return

    target_user_code = data.get('user_code')
    new_status = data.get('status') # 'pending' or 'active'

    if not target_user_code or new_status not in ['pending', 'active', 'inactive']:
         print(f"SocketIO: Invalid data for set_user_status: {data}", file=sys.stderr)
         emit('message', {'data': 'Неверные данные для изменения статуса пользователя.', 'category': 'warning'}, room=sid)
         return

    cursor.execute("SELECT id, status FROM users WHERE code = ?", (target_user_code,))
    target_user = cursor.fetchone()

    if not target_user:
         print(f"SocketIO: Attempt to set status for non-existent user: {target_user_code}", file=sys.stderr)
         emit('message', {'data': f'Пользователь с кодом "{target_user_code}" не найден.', 'category': 'warning'}, room=sid)
         return

    old_status = target_user['status']

    if old_status == new_status:
         # No change needed
         emit('message', {'data': f'Статус пользователя "{target_user_code}" уже "{new_status}".', 'category': 'info'}, room=sid)
         return

    cursor.execute("UPDATE users SET status = ? WHERE code = ?", (new_status, target_user_code))
    db.commit()

    print(f"SocketIO: Admin {admin_user_code} changed status of {target_user_code} from {old_status} to {new_status}", file=sys.stderr)
    emit('message', {'data': f'Статус пользователя "{target_user_code}" изменен на "{new_status}".', 'category': 'success'}, room=sid)

    # Broadcast game update to all connected users as user status affects game state (e.g., active players count)
    broadcast_game_update()


# SocketIO event for admin to select active deck
@socketio.on('select_active_deck')
def handle_select_active_deck(data):
    sid = request.sid
    admin_user_code = connected_users_socketio.get(sid)
    if not admin_user_code:
         emit('message', {'data': 'Вы не авторизованы как администратор.', 'category': 'danger'}, room=sid)
         return

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE code = ?", (admin_user_code,))
    admin_user = cursor.fetchone()

    if not admin_user or not admin_user['is_admin']:
         emit('message', {'data': 'У вас нет прав администратора.', 'category': 'danger'}, room=sid)
         return

    selected_subfolder = data.get('subfolder')

    if not selected_subfolder:
         emit('message', {'data': 'Не выбрана колода.', 'category': 'warning'}, room=sid)
         return

    # Check if the deck exists
    cursor.execute("SELECT COUNT(*) FROM decks WHERE subfolder = ?", (selected_subfolder,))
    if cursor.fetchone()[0] == 0:
         emit('message', {'data': 'Выбранная колода не найдена.', 'category': 'danger'}, room=sid)
         return

    # Check if game is in progress
    cursor.execute("SELECT game_in_progress FROM game_state WHERE id = 1")
    game_state = cursor.fetchone()
    if game_state and game_state['game_in_progress']:
         emit('message', {'data': 'Нельзя сменить активную колоду во время игры.', 'category': 'warning'}, room=sid)
         return

    cursor.execute("UPDATE game_state SET active_subfolder = ? WHERE id = 1", (selected_subfolder,))
    db.commit()

    print(f"SocketIO: Admin {admin_user_code} selected active deck: {selected_subfolder}", file=sys.stderr)
    emit('message', {'data': f'Активная колода изменена на "{selected_subfolder}".', 'category': 'success'}, room=sid)

    # Broadcast game update (active subfolder is part of the state)
    broadcast_game_update()

# ... Other admin SocketIO events like load_deck_images etc. ...


# This block ensures the Flask app runs when the script is executed directly
# Gunicorn typically ignores this block and loads the 'app' object directly.
if __name__ == "__main__":
    # Initialization is now handled outside this block when the module is loaded.
    # This block is primarily for running the development server directly.
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    # Ensure allow_unsafe_werkzeug=True is used only in development
    socketio.run(app, host="0.0.0.0", port=port, debug=debug, allow_unsafe_werkzeug=True)
