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
    # <<< ИСПРАВЛЕНИЕ: Перемещение global в начало блока
    global _current_game_board_pole_image_config
    global _current_game_board_num_cells
    # >>>

    init_database()

    # Load game board visuals into global variable _current_game_board_pole_image_config
    # This should happen after init_database has potentially created the table
    db = get_db()
    cursor = db.cursor()
    try:
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
              _current_game_board_num_cells = DEFAULT_NUM_BOARD_CELLS


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


def broadcast_game_update(user_code_trigger=None):
    """Sends the current game state to all connected users or a specific user."""
    # print(f"Broadcasting game update. Target user_code: {user_code}", file=sys.stderr)
    # If a user_code_trigger is provided, it means this broadcast is in response to their action
    # We might add logic here later if needed to differentiate updates.
    # For now, we just broadcast to all.
    for sid, code in list(connected_users_socketio.items()): # Use list for safe iteration
         try:
             user_specific_state = state_to_json(user_code_for_state=code)
             # Include flashed messages only for the user who triggered the update, or if it's a general update
             # For automatic updates (like after scoring), send messages to all relevant users
             # However, Flask flash messages are session-based. They are fetched once per request.
             # We are handling flash messages via SocketIO 'message' event separately for now.
             # Let's ensure flashed messages are handled in state_to_json if user_code is provided.
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
            'flashed_messages': [], # Keep flashed messages here as they are fetched per request context
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

    # Fetch flashed messages only if user_code is provided (i.e., called within a request context for a specific user)
    flashed_messages_list = get_flashed_messages(with_categories=True) if user_code_for_state else []
    flashed_messages = [dict(msg) for msg in flashed_messages_list] # Convert to list of dicts


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
                 cursor.execute("SELECT id, subfolder, image FROM images WHERE status = ? AND owner_id = ?", (f'Занято: {current_user_data["id"]}', current_user_data['id'])) # Use owner_id
                 user_cards = [dict(row) for row in cursor.fetchall()]


    if game_in_progress or game_over:
        # Fetch images on the table
        cursor.execute("SELECT id, subfolder, image, status, owner_id FROM images WHERE status LIKE 'На столе:%'") # Fetch owner_id here
        table_images_raw = cursor.fetchall()

        # Fetch all active users for guessing phase and other user info
        cursor.execute("SELECT id, name, rating FROM users WHERE status = 'active'")
        all_active_users = {row['id']: dict(row) for row in cursor.fetchall()}
        all_users_for_guessing = list(all_active_users.values()) # Provide list of users for frontend

        # Fetch guesses related to cards currently on the table
        all_guesses_raw = []
        all_guesses_by_card = {} # {image_id: [(user_id, guessed_user_id)]}
        if table_images_raw:
             table_image_ids = tuple(img['id'] for img in table_images_raw)
             if table_image_ids: # Ensure tuple is not empty for SQL IN clause
                 cursor.execute("SELECT user_id, guessed_user_id, image_id FROM guesses WHERE image_id IN ({})".format(','.join('?' * len(table_image_ids))), table_image_ids)
                 all_guesses_raw = cursor.fetchall()
                 # Group guesses by the card that was guessed ABOUT (image_id)
                 for guess in all_guesses_raw:
                     card_guessed_about_id = guess['image_id']
                     if card_guessed_about_id not in all_guesses_by_card:
                         all_guesses_by_card[card_guessed_about_id] = []
                     all_guesses_by_card[card_guessed_about_id].append((guess['user_id'], guess['guessed_user_id']))


        # Augment table images with owner info and guesses if show_card_info or guessing phase
        if show_card_info or on_table_status: # on_table_status implies guessing phase might be starting/active
             for img in table_images_raw:
                # Owner_id is already fetched in the main query for table_images_raw
                owner_id = img['owner_id'] # Use the fetched owner_id
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
                     user_guess_for_this_card = None
                     for guess_entry in all_guesses_raw:
                         if guess_entry['user_id'] == current_user_id and guess_entry['image_id'] == img_dict['id']:
                             user_guess_for_this_card = guess_entry['guessed_user_id']
                             break # Found the guess for this user for this card
                     img_dict['my_guess_for_this_card_value'] = user_guess_for_this_card


                table_images.append(img_dict)


    # Determine if all active players have placed a card for the guessing phase
    all_cards_placed_for_guessing_phase = False
    if game_in_progress and not game_over and current_leader_id is not None and on_table_status:
         # Count active players
         cursor.execute("SELECT COUNT(*) FROM users WHERE status = 'active'")
         active_players_count = cursor.fetchone()[0]
         # Count distinct owner_ids on the table
         cursor.execute("SELECT COUNT(DISTINCT owner_id) FROM images WHERE status LIKE 'На столе:%'")
         placed_cards_distinct_owners_count = cursor.fetchone()[0]

         # Condition to transition to guessing phase: All active players have placed a card
         if active_players_count > 0 and placed_cards_distinct_owners_count == active_players_count:
             all_cards_placed_for_guessing_phase = True
         elif active_players_count == 0: # No active players, shouldn't be in progress
              pass # Stay False

    # In the edge case of only one active player (the leader), placing their card transitions to guessing phase
    # and then immediately to scoring phase as no one else needs to guess.
    # This is handled below in the guess_card trigger logic for the single active player case.


    # Determine leader's board visual state based on rating
    leader_pole_image_path = None
    leader_pictogram_rating_display = None
    game_board_visual_config = [] # Fetch board visual config
    cursor.execute("SELECT id, image, max_rating FROM game_board_visuals ORDER BY id")
    board_config_rows = cursor.fetchall()
    if board_config_rows:
         game_board_visual_config = [dict(row) for row in board_config_rows]
         # Ensure global is updated, but be careful if this runs multiple times
         # global _current_game_board_num_cells # Already declared global at the top of the file

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
        'flashed_messages': flashed_messages, # Flashed messages for this user/request
        'game_board': game_board_state,
        'current_num_board_cells': current_num_board_cells, # Send the determined number of cells
        'leader_pole_pictogram_path': leader_pole_image_path, # Pass leader board image path
        'leader_pictogram_rating_display': leader_pictogram_rating_display, # Pass leader rating for pictogram
    }


# Helper function for internal end round logic (now called from route)
def end_round():
    """Calculates scores, updates ratings, determines next leader, and updates game state.
       This function is triggered when show_card_info becomes True."""
    db = get_db()
    cursor = db.cursor()

    # Fetch game state
    cursor.execute("SELECT * FROM game_state WHERE id = 1")
    game_state = cursor.fetchone()
    # Check show_card_info status here as the route handler or auto-trigger should ensure it's 1
    if not game_state or not game_state['game_in_progress'] or game_state['game_over'] or not game_state['show_card_info']:
         print("Error: end_round called in invalid state (show_card_info is not True or game state invalid).", file=sys.stderr)
         # If called incorrectly, try to reset state partially to prevent deadlock
         if game_state and game_state['game_in_progress'] and not game_state['game_over']:
             cursor.execute("UPDATE game_state SET on_table_status = 0, show_card_info = 0, leader_pole_image_path = NULL, leader_pictogram_rating = NULL")
             cursor.execute("UPDATE images SET status = 'Свободно', owner_id = NULL WHERE status LIKE 'На столе:%'") # Reset owner_id
             cursor.execute("DELETE FROM guesses")
             db.commit()
             flash("Раунд сброшен из-за внутренней ошибки.", "danger")
             broadcast_game_update()
         return


    current_leader_id = game_state['current_leader_id']
    active_subfolder = game_state['active_subfolder']

    # Fetch images on the table with their owners
    cursor.execute("SELECT id, owner_id FROM images WHERE status LIKE 'На столе:%' AND subfolder = ?", (active_subfolder,))
    table_images_raw = cursor.fetchall()
    table_image_owners = {img['id']: img['owner_id'] for img in table_images_raw}
    table_image_ids = tuple(table_image_owners.keys()) if table_image_owners else tuple()


    if not table_image_owners:
         flash("На столе нет карточек из активной колоды для подсчета очков.", "warning")
         # Reset table state if no cards found (shouldn't happen if show_card_info was true)
         cursor.execute("UPDATE images SET status = 'Свободно', owner_id = NULL WHERE status LIKE 'На столе:%' AND subfolder = ?", (active_subfolder,)) # Reset owner_id
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

    if current_leader_id is not None: # Leader must be defined if game is in progress
        if leader_card_id is None and len(table_image_owners) > 0: # Leader's card must be on table unless no cards were placed at all (shouldn't happen if show_card_info=1)
            # This should not happen if leader placed a card to start the round UNLESS they are the only player and no cards were dealt/placed
             flash("Ведущий не выложил карточку для подсчета очков.", "danger")
             # Reset table state
             cursor.execute("UPDATE images SET status = 'Свободно', owner_id = NULL WHERE status LIKE 'На столе:%' AND subfolder = ?", (active_subfolder,)) # Reset owner_id
             cursor.execute("DELETE FROM guesses")
             cursor.execute("UPDATE game_state SET on_table_status = 0, show_card_info = 0, leader_pole_image_path = NULL, leader_pictogram_rating = NULL, current_num_board_cells = NULL, current_leader_id = NULL, next_leader_id = NULL")
             db.commit()
             broadcast_game_update() # Send update after reset
             return


    # Fetch all active players
    cursor.execute("SELECT id, rating FROM users WHERE status = 'active'")
    active_players = cursor.fetchall()
    active_player_ids = [p['id'] for p in active_players]
    player_ratings = {p['id']: p['rating'] for p in active_players} # Includes leader if active

    # Fetch all guesses for cards currently on the table
    all_guesses = []
    if table_image_ids:
        cursor.execute("SELECT user_id, guessed_user_id, image_id FROM guesses WHERE image_id IN ({})".format(','.join('?' * len(table_image_ids))), table_image_ids)
        all_guesses = cursor.fetchall()


    # Process guesses and calculate scores
    score_changes = {player_id: 0 for player_id in active_player_ids} # {player_id: score_change}
    correct_leader_guessers = [] # List of user_ids who correctly guessed the leader's card


    # Group guesses by the card that was guessed ABOUT (image_id)
    guesses_by_card_guessed_about = {} # {card_id: [(user_id, guessed_owner_id)]}
    for guess in all_guesses:
        card_guessed_about_id = guess['image_id']
        if card_guessed_about_id not in guesses_by_card_guessed_about:
            guesses_by_card_guessed_about[card_guessed_about_id] = []
        guesses_by_card_guessed_about[card_guessed_about_id].append((guess['user_id'], guess['guessed_user_id']))


    # --- Scoring Logic based on Guesses ---

    # 1. Process non-leader players' guesses about the leader's card
    non_leader_players_ids = [pid for pid in active_player_ids if pid != current_leader_id]
    num_non_leader_players = len(non_leader_players_ids)

    leader_score_from_guesses_on_his_card = 0
    if leader_card_id is not None: # Ensure leader's card was on the table
        leader_card_guesses = guesses_by_card_guessed_about.get(leader_card_id, [])
        leader_card_guesses_by_others = [guess for guess in leader_card_guesses if guess[0] != current_leader_id] # Guesses on leader's card by others
        num_correct_leader_guesses_by_others = 0
        correct_leader_guessers = []
        for guesser_id, guessed_owner_id in leader_card_guesses_by_others:
            if guessed_owner_id == current_leader_id:
                num_correct_leader_guesses_by_others += 1
                correct_leader_guessers.append(guesser_id)

        if num_non_leader_players > 0: # Only apply these rules if there are other players
            if num_correct_leader_guesses_by_others == num_non_leader_players:
                 leader_score_from_guesses_on_his_card = -3
                 flash(f"Все игроки угадали карточку Ведущего. Ведущий перемещается на 3 хода назад.", "info")
            elif num_correct_leader_guesses_by_others == 0:
                 leader_score_from_guesses_on_his_card = -2
                 flash(f"Ни один игрок не угадал карточку Ведущего. Ведущий перемещается на 2 хода назад.", "info")
            else:
                 leader_score_from_guesses_on_his_card = 3 + num_correct_leader_guesses_by_others
                 flash(f"{num_correct_leader_guesses_by_others} игрок(а) угадали карточку Ведущего.", "info")
        # else: if num_non_leader_players == 0, leader_score_from_guesses_on_his_card remains 0 from this part (leader gets points only if others guess him)

        # Add points for players who correctly guessed the leader's card (Case 3 logic)
        if num_non_leader_players > 0 and not (num_correct_leader_guesses_by_others == num_non_leader_players or num_correct_leader_guesses_by_others == 0):
            for guesser_id in correct_leader_guessers:
                if guesser_id in score_changes: # Ensure it's an active player
                     score_changes[guesser_id] += 3 # 3 points for correctly guessing leader's card

    # 2. Process guesses about other players' cards
    # Both leader and non-leader players can guess other players' cards
    for card_id, owner_id in table_image_owners.items():
        if owner_id != current_leader_id: # Only consider player cards (not leader's own card for this scoring rule)
            guesses_about_this_player_card = guesses_by_card_guessed_about.get(card_id, [])
            # Count correct guesses for this player's card made by *other* players (excluding the card owner)
            correct_guessers_for_this_player_card = [guesser_id for guesser_id, guessed_owner_id in guesses_about_this_player_card if guessed_owner_id == owner_id and guesser_id != owner_id]
            num_correct_guesses_for_this_player_card = len(correct_guessers_for_this_player_card)

            # Add points to the owner of the card (+1 per correct guesser, excluding self)
            if owner_id in score_changes: # Ensure owner is an active player
                 score_changes[owner_id] += num_correct_guesses_for_this_player_card
                 if num_correct_guesses_for_this_player_card > 0:
                      player_name = get_user_name_by_id(owner_id) or f'Игрок ID {owner_id}'
                      flash(f"Карточку игрока {player_name} угадали {num_correct_guesses_for_this_player_card} игрок(а).", "info")

            # Check if the LEADER correctly guessed this player's card
            # Based on user.html rules, leader's points only come from players guessing HIS card.
            # If the rule was added that leader gets points for guessing others, logic would be here.
            # For now, following the provided rules, leader doesn't get points for guessing players' cards.


    # Add the leader's score change from guesses on HIS card
    if current_leader_id is not None and current_leader_id in score_changes:
         score_changes[current_leader_id] += leader_score_from_guesses_on_his_card


    # Apply total score changes and update ratings
    for player_id, score_change in score_changes.items():
        if score_change != 0: # Only update if score changed
             player_name = get_user_name_by_id(player_id) or f'Игрок ID {player_id}'
             # Removed flashing score change per player to reduce spam, relies on score change in total flash
             # flash(f"Игрок {player_name} получает {score_change} очк(а/ов).", "info")
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


    # Determine the next leader (user with the highest rating among active players)
    # Only determine next leader if the game is NOT over
    next_leader_id = None
    if not game_over:
         cursor.execute("SELECT id FROM users WHERE status = 'active' ORDER BY rating DESC LIMIT 1")
         next_leader_row = cursor.fetchone()
         if next_leader_row:
             next_leader_id = next_leader_row['id']
         else:
             next_leader_id = None # No active players left

    # --- UPDATE game_state with next_leader_id and reset flags for next round ---
    # After scoring is done, reset state for the next round.
    # show_card_info is set back to 0.
    if not game_over:
         # Set current_leader_id to NULL temporarily and set next_leader_id to the determined next leader.
         # The start_new_round logic will move next_leader_id to current_leader_id.
         cursor.execute("UPDATE game_state SET on_table_status = 0, show_card_info = 0, current_leader_id = NULL, next_leader_id = ? WHERE id = 1", (next_leader_id,))
         db.commit()
    # else: game_state is already updated for game_over above


    # Reset image statuses from 'На столе' to 'Свободно' for cards from the active subfolder that were on the table
    if active_subfolder:
        cursor.execute("UPDATE images SET status = 'Свободно', owner_id = NULL WHERE status LIKE 'На столе:%' AND subfolder = ?", (active_subfolder,)) # Reset owner_id

    # Delete all guesses
    cursor.execute("DELETE FROM guesses")

    db.commit() # Commit remaining changes

    # Broadcast game update to reflect scores, new leader, and state reset
    # This broadcast happens *after* scores are committed and state is reset for the next round.
    # The frontend will show the score changes, then transition based on new state.
    broadcast_game_update()

    # Flash messages are already handled by state_to_json/SocketIO 'message' event
    # No explicit redirect or render needed here as this is likely called internally after an action.


# Route for admin to trigger end round manually (kept for fallback/admin control)
# This route will now just set show_card_info and call end_round
@app.route('/admin/end_round_manual', methods=['POST'])
@app.route('/end_round', methods=['POST']) # Keep old route for compatibility if used elsewhere
def admin_end_round_manual():
    """Admin or auto trigger to reveal cards, calculate scores, and end round."""
    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT * FROM game_state WHERE id = 1")
    game_state = cursor.fetchone()

    if not game_state or not game_state['game_in_progress'] or game_state['game_over']:
         flash("Игра не в процессе.", "warning")
         return redirect(url_for('index')) # Or redirect to admin panel

    # Ensure show_card_info is true before calling end_round
    cursor.execute("UPDATE game_state SET show_card_info = 1 WHERE id = 1")
    db.commit()

    # Broadcast to show revealed cards before scoring (optional, could be done inside end_round)
    broadcast_game_update()

    # Call the internal end_round logic
    end_round()

    # Redirect to index or admin panel, state update is handled by SocketIO
    # If triggered automatically by last guess, this redirect is not used.
    # If triggered by admin, redirect back to admin panel might be better.
    # Let's assume admin triggered if this route is called directly.
    flash("Раунд завершен, очки подсчитаны.", "success") # Reiterate success flash

    # Determine if the user who triggered this is an admin (if needed)
    # For simplicity, always redirect to index after manual trigger
    # return redirect(url_for('admin')) # Redirect back to admin for admin trigger
    return redirect(url_for('index')) # Redirect to user page


# Example function to start a new round (needs logic to select images, assign to players, set leader, etc.)
# This function should likely be triggered by the next leader or an admin after end_round
# The button in user.html/admin.html should post to this route.
@app.route('/start_new_round', methods=['POST'])
def start_new_round():
    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT * FROM game_state WHERE id = 1")
    game_state = cursor.fetchone()

    if game_state and game_state['game_in_progress'] and not game_state['game_over']:
        flash("Игра уже в процессе.", "warning")
        # Ideally, check if the current leader or admin is triggering this
        # Determine who is triggering this (e.g., current leader from state or admin)
        # For now, any POST to this route attempts to start.
        # If triggered by SocketIO event from specific user, check user ID vs game_state['next_leader_id'] or admin status.
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


    # Reset image statuses and owner_id for all images
    cursor.execute("UPDATE images SET status = 'Свободно', owner_id = NULL")

    # Delete all guesses - IMPORTANT: Clear guesses when starting a NEW round
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

    # Ensure the leader gets their card dealt too, so they have a hand at the start of the round.
    # The leader is included in the active_users list, so they will get cards.

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
                # When dealing, set status to 'Занято:user_id' and owner_id to user_id
                cursor.execute("UPDATE images SET status = ?, owner_id = ? WHERE id = ?", (f'Занято: {user_id}', user_id, image_id_to_deal))
                deal_count += 1
            else:
                print(f"Warning: Ran out of available images while dealing cards. Dealt {deal_count} out of {required_cards}.", file=sys.stderr)
                break # Should not happen if check above is correct
        if deal_count >= len(available_image_ids):
             break # Stop dealing if images run out

    db.commit()

    # Update game state to indicate game is in progress, set on_table_status to 0 (placement phase begins)
    cursor.execute("UPDATE game_state SET game_in_progress = 1, game_over = 0, on_table_status = 0, show_card_info = 0") # current_leader_id and next_leader_id handled above
    db.commit()

    flash("Новый раунд начат! Карточки розданы.", "success")

    # Broadcast game update to all connected users
    broadcast_game_update()

    # If triggered from admin, redirect there. If triggered from user page (e.g., by next leader), redirect there.
    # Let's redirect to index for simplicity, which will then redirect to user page if logged in.
    return redirect(url_for('index'))


# Route for handling card placement by a player (including leader)
@app.route('/user/<code>/place/<int:image_id>', methods=['POST'])
def place_card(code, image_id):
    """Handle a player placing a card on the table."""
    db = get_db()
    c = db.cursor()

    # 1. Authenticate user and get user data
    c.execute("SELECT id, code, status FROM users WHERE code = ?", (code,))
    g.user = c.fetchone()
    if not g.user or g.user['status'] != 'active':
        flash("Неверный код пользователя или ваш статус не 'Активен'.", "danger")
        return redirect(url_for('index')) # Redirect to index if not active or invalid code

    # 2. Check game state
    c.execute("SELECT game_in_progress, game_over, current_leader_id, active_subfolder, on_table_status, show_card_info FROM game_state WHERE id = 1")
    game_state = c.fetchone()

    if not game_state or not game_state['game_in_progress'] or game_state['game_over']:
        flash("Игра не в процессе.", "warning")
        # Broadcast update as state might be stale on client
        broadcast_game_update(user_code_trigger=code)
        return redirect(url_for('index'))

    if game_state['show_card_info'] or game_state['on_table_status']:
        flash("Сейчас не фаза выкладывания карточек.", "warning")
         # Broadcast update as state might be stale on client
        broadcast_game_update(user_code_trigger=code)
        return redirect(url_for('index')) # Cannot place if already in guessing/revealing phase


    current_leader_id = game_state['current_leader_id']
    active_subfolder = game_state['active_subfolder']

    if not active_subfolder:
         flash("Активная колода не выбрана. Свяжитесь с администратором.", "danger")
         # Broadcast update as state might be stale on client
         broadcast_game_update(user_code_trigger=code)
         return redirect(url_for('index')) # Redirect to index

    # 3. Check if the card belongs to the user and is in their hand ('Занято')
    # Use owner_id check as well for robustness
    c.execute("SELECT id, subfolder, image, status, owner_id FROM images WHERE id = ? AND status = ? AND owner_id = ?", (image_id, f"Занято:{g.user['id']}", g.user['id']))
    card_to_place = c.fetchone()

    if not card_to_place:
        flash("Эта карточка не у вас в руке или уже на столе.", "warning")
         # Broadcast update as hand might be stale on client
        broadcast_game_update(user_code_trigger=code)
        return redirect(url_for('index')) # Redirect back to user page


    # 4. Check if the user has already placed a card and handle replacement
    # Find if the current user already has a card on the table in this round (status starts with 'На столе:')
    c.execute("SELECT id FROM images WHERE owner_id = ? AND status LIKE 'На столе:%' AND subfolder = ?", (g.user['id'], active_subfolder))
    card_of_this_user_on_table = c.fetchone()

    if card_of_this_user_on_table:
        if card_of_this_user_on_table['id'] == image_id:
            # Trying to place the same card that is already on the table - no change needed
            flash("Эта карточка уже у вас на столе.", "info")
             # Broadcast update (even if no change, ensures state is fresh)
            broadcast_game_update(user_code_trigger=code)
            return redirect(url_for('index'))
        else:
            # User is placing a DIFFERENT card while one is already on the table.
            # This means they are replacing their card. Return the old one to hand.
             c.execute("UPDATE images SET status = ?, owner_id = ? WHERE id = ?", (f"Занято:{g.user['id']}", g.user['id'], card_of_this_user_on_table['id'])) # Reset owner_id to themselves
             # Also remove any guesses associated with the card being returned to hand (shouldn't exist if guesses cleared properly, but safety)
             c.execute("DELETE FROM guesses WHERE image_id = ?", (card_of_this_user_on_table['id'],))
             flash(f"Предыдущая карточка возвращена в руку.", "info")


    # 5. Place the selected card on the table
    # Set status to 'На столе:user_id' and keep owner_id set to user_id
    c.execute("UPDATE images SET status = ?, owner_id = ? WHERE id = ?", (f"На столе:{g.user['id']}", g.user['id'], image_id))
    # When a card is placed, ensure any old guesses *about this specific card* from *previous rounds* are cleared.
    # Although guesses are cleared at the end of round, this is a safeguard.
    c.execute("DELETE FROM guesses WHERE image_id = ?", (image_id,))
    db.commit()

    flash(f"Ваша карточка '{card_to_place['image']}' выложена на стол.", "success")

    # 6. Check if all active players have placed their cards.
    c.execute("SELECT id FROM users WHERE status = 'active'")
    active_player_ids = [row['id'] for row in c.fetchall()]
    active_players_count = len(active_player_ids)


    c.execute("SELECT COUNT(DISTINCT owner_id) FROM images WHERE status LIKE 'На столе:%'")
    placed_cards_distinct_owners_count = c.fetchone()[0]

    all_players_placed_cards = False
    # Condition to transition to guessing phase:
    # - There are active players AND the number of distinct owners with cards on the table equals the number of active players.
    if active_players_count > 0 and placed_cards_distinct_owners_count == active_players_count:
        all_players_placed_cards = True
    # Edge case: 0 active players - should not be able to start round. 1 active player (leader) transitions after placing.
    elif active_players_count == 1 and placed_cards_distinct_owners_count == 1:
         all_players_placed_cards = True # Leader placed their card, they are the only player


    if all_players_placed_cards and not game_state['on_table_status'] and not game_state['show_card_info']:
        # Transition to guessing phase
        c.execute("UPDATE game_state SET on_table_status = 1 WHERE id = 1")
        # --- НОВОЕ ИЗМЕНЕНИЕ: Очистить предположения при переходе в фазу угадывания ---
        c.execute("DELETE FROM guesses")
        # --- КОНЕЦ НОВОГО ИЗМЕНЕНИЯ ---
        db.commit()
        flash("Все игроки выложили карточки! Начинается фаза угадывания.", "info")


    # 7. Broadcast game update
    broadcast_game_update(user_code_trigger=code)

    return redirect(url_for('index')) # Redirect back to user page


# Route for handling player guesses
@app.route('/user/<code>/guess/<int:card_id>', methods=['POST'])
def guess_card(code, card_id):
    """Handle a player submitting a guess for a card on the table."""
    db = get_db()
    c = db.cursor()

    # 1. Authenticate user and get user data
    c.execute("SELECT id, code, status FROM users WHERE code = ?", (code,))
    g.user = c.fetchone()
    if not g.user or g.user['status'] != 'active':
        flash("Неверный код пользователя или ваш статус не 'Активен'.", "danger")
        return redirect(url_for('index')) # Redirect to index if not active or invalid code

    # 2. Check game state - must be in guessing phase
    c.execute("SELECT game_in_progress, game_over, current_leader_id, active_subfolder, on_table_status, show_card_info FROM game_state WHERE id = 1")
    game_state = c.fetchone()

    if not game_state or not game_state['game_in_progress'] or game_state['game_over'] or not game_state['on_table_status'] or game_state['show_card_info']:
        flash("Сейчас не фаза угадывания.", "warning")
        broadcast_game_update(user_code_trigger=code)
        return redirect(url_for('index'))

    # Leader *does* guess in this updated logic, so no exclusion here.


    # 3. Get the guessed user ID from the form
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

    # 4. Validate the guess: card must be on the table, guessed user must be active and have a card on table
    c.execute("SELECT id, owner_id FROM images WHERE id = ? AND status LIKE 'На столе:%' AND subfolder = ?", (card_id, game_state['active_subfolder']))
    card_on_table = c.fetchone()

    if not card_on_table:
        flash("Эта карточка больше не на столе или не из активной колоды.", "warning")
        broadcast_game_update(user_code_trigger=code)
        return redirect(url_for('index'))

    # Prevent a user from guessing their *own* card
    if card_on_table['owner_id'] == g.user['id']:
        flash("Вы не можете угадывать собственную карточку.", "warning")
        broadcast_game_update(user_code_trigger=code)
        return redirect(url_for('index'))


    # Guessed user must be an active player on the table (leader or another player)
    c.execute("SELECT id, status FROM users WHERE id = ? AND status = 'active'", (guessed_user_id,))
    guessed_user = c.fetchone()

    # Also, the guessed user must have a card currently on the table for this round
    c.execute("SELECT COUNT(*) FROM images WHERE owner_id = ? AND status LIKE 'На столе:%'", (guessed_user_id,))
    guessed_user_has_card_on_table = c.fetchone()[0] > 0


    if not guessed_user or not guessed_user_has_card_on_table:
         flash("Выбранный игрок не активен или не выложил карточку на стол.", "warning")
         broadcast_game_update(user_code_trigger=code)
         return redirect(url_for('index'))

    # --- НОВАЯ ВАЛИДАЦИЯ: Игрок не может указывать одно и то же имя пользователя для разных карточек ---
    # Check if the current user has already made a guess for a *different* card
    # that points to the *same* guessed_user_id in this round.
    # We need to check against all cards on the table that are NOT the one being guessed right now,
    # and that belong to someone other than the current user (as user cannot guess own card).
    c.execute("""
        SELECT g.image_id, i.owner_id
        FROM guesses g
        JOIN images i ON g.image_id = i.id
        WHERE g.user_id = ?
          AND g.image_id != ? -- Exclude the card being guessed now
          AND g.guessed_user_id = ?
          AND i.status LIKE 'На столе:%' -- Ensure the card is still on the table
    """, (g.user['id'], card_id, guessed_user_id))
    existing_guess_for_other_card_with_same_owner = c.fetchone()

    if existing_guess_for_other_card_with_same_owner:
        # Find the image name for the conflicting card for better message
        conflicting_image_id = existing_guess_for_other_card_with_same_owner['image_id']
        c.execute("SELECT image FROM images WHERE id = ?", (conflicting_image_id,))
        conflicting_image_row = c.fetchone()
        conflicting_image_name = conflicting_image_row['image'] if conflicting_image_row else f"ID {conflicting_image_id}"

        flash(f"Вы уже предположили, что карточка '{conflicting_image_name}' принадлежит этому игроку. Выберите другого игрока для текущей карточки или измените то предположение.", "warning")
        broadcast_game_update(user_code_trigger=code)
        return redirect(url_for('index'))
    # --- КОНЕЦ НОВОЙ ВАЛИДАЦИИ ---


    # 5. Save the guess (or update if already exists)
    # Check if this user already guessed for this card in this round
    c.execute("SELECT id FROM guesses WHERE user_id = ? AND image_id = ?", (g.user['id'], card_id))
    existing_guess = c.fetchone()

    if existing_guess:
        c.execute("UPDATE guesses SET guessed_user_id = ? WHERE id = ?", (guessed_user_id, existing_guess['id']))
        flash(f"Ваше предположение для карточки изменено.", "success")
    else:
        c.execute("INSERT INTO guesses (user_id, image_id, guessed_user_id) VALUES (?, ?, ?)", (g.user['id'], card_id, guessed_user_id))
        flash(f"Ваше предположение для карточки сохранено.", "success")

    db.commit()

    # --- НОВАЯ ЛОГИКА: Проверка, все ли игроки сделали все необходимые предположения и они валидны ---
    # Determine the total number of guesses required in this round:
    # Each active player must guess every card on the table that is not their own.

    c.execute("SELECT id FROM users WHERE status = 'active'")
    active_player_ids = [row['id'] for row in c.fetchall()]
    num_active_players = len(active_player_ids)

    # Fetch cards on the table with owners
    c.execute("SELECT id, owner_id FROM images WHERE status LIKE 'На столе:%'")
    table_cards_with_owners = c.fetchall()
    table_card_ids = [card['id'] for card in table_cards_with_owners]
    num_cards_on_table = len(table_card_ids)

    total_required_guesses = 0
    for player_id in active_player_ids:
        for card in table_cards_with_owners:
            if card['owner_id'] != player_id:
                total_required_guesses += 1

    # Count the actual number of guesses made for cards currently on the table
    actual_guesses_count = 0
    if table_card_ids:
        c.execute("SELECT COUNT(*) FROM guesses WHERE image_id IN ({})".format(','.join('?' * len(table_card_ids))), table_card_ids)
        actual_guesses_count = c.fetchone()[0]

    # Check uniqueness constraint for *each* player who has made guesses
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
    # We only need to check uniqueness for players who have made more than one guess.
    for user_id, guessed_owners in guesses_grouped_by_user.items():
        if len(guessed_owners) > 1: # Only check if user guessed more than one card
             if len(guessed_owners) != len(set(guessed_owners)):
                 uniqueness_check_passed = False
                 # Optional: Identify which player failed the check for logging/debugging
                 # print(f"Uniqueness check failed for user ID {user_id}", file=sys.stderr)
                 break # No need to check other users if one failed


    # Trigger the reveal and scoring if:
    # 1. There are active players (more than 0)
    # 2. The number of actual guesses equals the total required guesses.
    # 3. The uniqueness check passed for all players who made guesses.
    # 4. The game is in the guessing phase and not already in the reveal phase.
    # 5. Handle the edge case of 1 active player (leader) separately - they don't guess others' cards.
    # The transition for a leader-only game happens after they place their card (in place_card).
    # So this auto-trigger logic primarily applies to games with > 1 active player.

    should_auto_trigger = False

    if num_active_players > 1: # Only check for auto-trigger if there's more than just the leader
         if actual_guesses_count == total_required_guesses and uniqueness_check_passed and game_state['on_table_status'] and not game_state['show_card_info']:
              should_auto_trigger = True
              flash("Все игроки сделали предположения! Карточки открываются и подсчитываются очки.", "info")
              print("Автоматический переход к подсчету очков: Все игроки сделали необходимые и уникальные предположения.", file=sys.stderr)

    elif num_active_players == 1 and game_state['on_table_status'] and not game_state['show_card_info']:
         # Edge case: Only one active player (the leader).
         # The check in place_card for num_active_players == 1 and placed_cards_distinct_owners_count == 1
         # should set on_table_status to true.
         # If we reach here in guessing phase with 1 active player, it must be the leader.
         # No guesses are required from non-existent players. The round should end.
         # We can trigger immediately if in guessing phase with 1 active player.
         # Ensure that the single active player *is* the leader to be safe.
         if active_player_ids and active_player_ids[0] == game_state['current_leader_id']:
              should_auto_trigger = True
              flash("Нет других игроков для угадывания. Переход к подсчету.", "info")
              print("Автоматический переход к подсчету очков: Нет других игроков.", file=sys.stderr)


    if should_auto_trigger:
        c.execute("UPDATE game_state SET show_card_info = 1 WHERE id = 1")
        db.commit()
        # Broadcast game update to show revealed cards
        broadcast_game_update()
        # Call the end_round logic to calculate scores and transition
        end_round()


    else:
        # Not all required guesses have been made yet OR the uniqueness check failed for at least one player.
        pass # Do nothing, wait for more guesses/corrections


    # 7. Broadcast game update (already handled inside the auto-trigger block if it fires)
    # If auto-trigger didn't fire, we still need to broadcast to show the user's guess.
    auto_triggered = (num_active_players > 1 and actual_guesses_count == total_required_guesses and uniqueness_check_passed and game_state['on_table_status'] and not game_state['show_card_info']) or \
                     (num_active_players == 1 and game_state['on_table_status'] and not game_state['show_card_info'] and active_player_ids and active_player_ids[0] == game_state['current_leader_id']) # Check the specific leader-only case that triggers
    if not auto_triggered:
        broadcast_game_update(user_code_trigger=code)


    return redirect(url_for('index')) # Redirect back to user page


# Default route for user page - requires user code
@app.route('/user/<code>')
def user(code):
    db = get_db()
    c = db.cursor()
    c.execute("SELECT id, code, name, is_admin, rating, status FROM users WHERE code = ?", (code,))
    g.user = c.fetchone()

    if g.user:
        # If user exists, render user page with initial user data
        # state_to_json will fetch full game state based on current DB state
        # user_data_for_init is passed to populate basic info on load
        return render_template('user.html', user_data_for_init=dict(g.user))
    else:
        # If user code is invalid, redirect to index with an error
        flash(f"Неверный код пользователя: {code}", "danger")
        return redirect(url_for('index'))


# Route for admin panel - requires admin status (session based)
@app.route('/admin')
def admin():
    # Check if user is logged in and is admin (assuming admin login sets session['is_admin'] = True)
    # For this example, let's just check if there is a user code and they are marked as admin in DB
    # A proper admin login flow would be needed in a real app.
    # For now, let's allow access if session has admin flag set.
    if not session.get('is_admin'):
         # Redirect to a login page or deny access if not admin
         flash("Доступ к админ панели ограничен.", "danger")
         return redirect(url_for('index')) # Redirect to index or login

    db = get_db()
    c = db.cursor()
    c.execute("SELECT id, code, name, is_admin, rating, status FROM users")
    all_users = c.fetchall()
    c.execute("SELECT subfolder, name FROM decks")
    all_decks = c.fetchall()
    c.execute("SELECT active_subfolder FROM game_state WHERE id = 1")
    game_state = c.fetchone()
    active_subfolder = game_state['active_subfolder'] if game_state else None


    # Fetch cards in the active deck and their status
    deck_images = []
    if active_subfolder:
         c.execute("SELECT id, image, status, owner_id FROM images WHERE subfolder = ?", (active_subfolder,)) # Fetch owner_id
         deck_images = c.fetchall()


    # Fetch game state for admin panel display (optional, can reuse state_to_json)
    # admin_game_state = state_to_json() # Use state_to_json to get current game info

    # Fetch current leader name for display
    current_leader_name = None
    if game_state and game_state['current_leader_id']:
         current_leader_name = get_user_name_by_id(game_state['current_leader_id'])


    # Get game board visuals and user positions for display in admin panel
    game_board_data = []
    current_num_board_cells_admin = DEFAULT_NUM_BOARD_CELLS # Fallback
    board_config_admin = []
    try:
         c.execute("SELECT id, image, max_rating FROM game_board_visuals ORDER BY id")
         board_config_rows_admin = c.fetchall()
         if board_config_rows_admin:
              board_config_admin = [dict(row) for row in board_config_rows_admin]
              current_num_board_cells_admin = board_config_admin[-1]['max_rating']
         c.execute("SELECT id, name, rating FROM users WHERE status = 'active'")
         active_users_for_board_admin = {row['id']: dict(row) for row in c.fetchall()}
         active_users_list_admin = list(active_users_for_board_admin.values())
         active_users_list_admin.sort(key=lambda x: x['rating'])

         for cell_config in board_config_admin:
              min_rating = 0 if cell_config['id'] == 1 else board_config_rows_admin[cell_config['id']-2]['max_rating'] + 1
              max_rating = cell_config['max_rating']
              users_in_this_cell = [user for user in active_users_list_admin if user['rating'] >= min_rating and user['rating'] <= max_rating]
              game_board_data.append({
                  'cell_number': cell_config['id'],
                  'image_path': os.path.join(GAME_BOARD_POLE_IMG_SUBFOLDER, cell_config['image']),
                  'max_rating': cell_config['max_rating'],
                  'users_in_cell': users_in_this_cell
              })

    except Exception as e:
         print(f"Error loading board data for admin panel: {e}", file=sys.stderr)
         game_board_data = [] # Clear board data on error


    return render_template('admin.html',
                           all_users=all_users,
                           all_decks=all_decks,
                           active_subfolder=active_subfolder,
                           deck_images=deck_images, # Cards in the active deck
                           current_leader_name=current_leader_name,
                           game_state=game_state, # Pass the game_state object
                           game_board=game_board_data, # Pass game board data
                           current_num_board_cells=current_num_board_cells_admin # Pass board cell count
                           )

# Route for admin login (simple example)
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        admin_code = request.form.get('admin_code')
        # Simple check: find a user with this code and is_admin=True
        db = get_db()
        c = db.cursor()
        c.execute("SELECT id, code, name FROM users WHERE code = ? AND is_admin = TRUE", (admin_code,))
        admin_user = c.fetchone()

        if admin_user:
            session['is_admin'] = True # Set admin flag in session
            session['user_code'] = admin_user['code'] # Store admin user code in session
            flash(f"Добро пожаловать, {admin_user['name']} (Администратор)!", "success")
            return redirect(url_for('admin'))
        else:
            flash("Неверный код администратора.", "danger")

    # If GET request or login failed, show login form
    return render_template('admin_login.html') # Assuming you have an admin_login.html template


# Route for admin logout
@app.route('/admin/logout')
def admin_logout():
    session.pop('user_code', None)
    session.pop('is_admin', None) # Also clear admin flag on user logout
    flash("Вы вышли из аккаунта.", "info")
    return redirect(url_for('index'))


# Route to handle creating a new user
@app.route('/create_user', methods=['POST'])
def create_user():
    user_name = request.form.get('user_name')
    is_admin = request.form.get('is_admin') == 'on' # Checkbox value 'on' if checked

    if not user_name:
        flash("Имя пользователя не может быть пустым.", "warning")
        return redirect(url_for('admin')) # Redirect back to admin panel

    # Generate a unique user code (simple example: random string)
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

    return redirect(url_for('admin')) # Redirect back to admin panel


# Route to handle activating/deactivating a user (from admin panel)
@app.route('/admin/set_user_status/<int:user_id>/<status>', methods=['POST'])
def admin_set_user_status(user_id, status):
     # Check if admin is logged in (basic check)
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

    # Broadcast game update as user status affects active player count etc.
    broadcast_game_update()

    return redirect(url_for('admin'))


# Route to handle deleting a user (from admin panel)
@app.route('/admin/delete_user/<int:user_id>', methods=['POST'])
def admin_delete_user(user_id):
     # Check if admin is logged in (basic check)
    if not session.get('is_admin'):
         flash("Недостаточно прав.", "danger")
         return redirect(url_for('index'))

    db = get_db()
    c = db.cursor()
    try:
        # Before deleting user, need to handle dependent records (guesses, potentially images if they owned any on table)
        # Delete guesses made by this user
        c.execute("DELETE FROM guesses WHERE user_id = ?", (user_id,))
        # If user owned a card on the table, reset its status? Or clear owner?
        # Let's reset status for cards owned by this user that are on the table
        c.execute("UPDATE images SET status = 'Свободно', owner_id = NULL WHERE status LIKE 'На столе:%' AND owner_id = ?", (user_id,))
        # If this user is the current or next leader, reset that in game_state
        c.execute("UPDATE game_state SET current_leader_id = NULL WHERE current_leader_id = ?", (user_id,))
        c.execute("UPDATE game_state SET next_leader_id = NULL WHERE next_leader_id = ?", (user_id,))

        # Now delete the user
        c.execute("DELETE FROM users WHERE id = ?", (user_id,))
        db.commit()
        flash(f"Пользователь ID {user_id} удален.", "success")

        # Broadcast game update as user removal affects game state
        broadcast_game_update()

    except Exception as e:
        flash(f"Ошибка при удалении пользователя ID {user_id}: {e}", "danger")
        print(f"Error deleting user: {e}", file=sys.stderr)


    return redirect(url_for('admin'))


# Route to handle creating a new deck folder
@app.route('/admin/create_deck', methods=['POST'])
def admin_create_deck():
     # Check if admin is logged in (basic check)
    if not session.get('is_admin'):
         flash("Недостаточно прав.", "danger")
         return redirect(url_for('index'))

    deck_name = request.form.get('deck_name')
    subfolder_name = request.form.get('subfolder_name') # User-provided subfolder name

    if not deck_name or not subfolder_name:
        flash("Название колоды и папка не могут быть пустыми.", "warning")
        return redirect(url_for('admin'))

    # Basic validation for subfolder name (preventing directory traversal etc.)
    if not re.match(r'^[a-zA-Z0-9_-]+$', subfolder_name):
         flash("Название папки может содержать только латинские буквы, цифры, дефисы и подчеркивания.", "warning")
         return redirect(url_for('admin'))


    # Create the directory for the deck images
    deck_dir = os.path.join(app.static_folder, 'images', subfolder_name)
    try:
        os.makedirs(deck_dir, exist_ok=True)
        db = get_db()
        c = db.cursor()
        # Check if subfolder already exists in DB
        c.execute("SELECT COUNT(*) FROM decks WHERE subfolder = ?", (subfolder_name,))
        if c.fetchone()[0] > 0:
             flash(f"Колода с папкой '{subfolder_name}' уже существует.", "warning")
             # Clean up the created directory if DB entry exists
             # os.rmdir(deck_dir) # Only if it was just created and is empty
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


# Route to handle deleting a deck
@app.route('/admin/delete_deck/<subfolder>', methods=['POST'])
def admin_delete_deck(subfolder):
     # Check if admin is logged in (basic check)
    if not session.get('is_admin'):
         flash("Недостаточно прав.", "danger")
         return redirect(url_for('index'))

    db = get_db()
    c = db.cursor()

    # Prevent deleting the currently active deck
    c.execute("SELECT active_subfolder FROM game_state WHERE id = 1")
    game_state = c.fetchone()
    if game_state and game_state['active_subfolder'] == subfolder:
        flash(f"Нельзя удалить активную колоду ('{subfolder}').", "warning")
        return redirect(url_for('admin'))


    try:
        # Delete images associated with this deck from DB (also clears owner_id and status)
        c.execute("DELETE FROM images WHERE subfolder = ?", (subfolder,))
        # Delete the deck from DB
        c.execute("DELETE FROM decks WHERE subfolder = ?", (subfolder,))
        db.commit()

        # Remove the physical directory and files
        deck_dir = os.path.join(app.static_folder, 'images', subfolder)
        if os.path.exists(deck_dir):
            # Use shutil.rmtree for non-empty directories, require import shutil
            # import shutil
            # shutil.rmtree(deck_dir)
            # For simplicity, let's just warn if dir is not empty and only remove if empty
             try:
                 os.rmdir(deck_dir) # This will fail if dir is not empty
                 print(f"Directory {deck_dir} removed.", file=sys.stderr)
             except OSError as e:
                  print(f"Warning: Could not remove directory {deck_dir}. It might not be empty: {e}", file=sys.stderr)
                  flash(f"Колода удалена из базы данных, но папка '{subfolder}' не была пустой и не удалена на сервере.", "warning")


        flash(f"Колода '{subfolder}' удалена (если папка была пустой).", "success")

    except Exception as e:
        flash(f"Ошибка при удалении колоды '{subfolder}': {e}", "danger")
        print(f"Error deleting deck: {e}", file=sys.stderr)

    return redirect(url_for('admin'))


# Route to handle uploading images for a deck
@app.route('/admin/upload_images/<subfolder>', methods=['POST'])
def admin_upload_images(subfolder):
     # Check if admin is logged in (basic check)
    if not session.get('is_admin'):
         flash("Недостаточно прав.", "danger")
         return redirect(url_for('index'))

    db = get_db()
    c = db.cursor()

    # Check if the deck exists
    c.execute("SELECT COUNT(*) FROM decks WHERE subfolder = ?", (subfolder,))
    if c.fetchone()[0] == 0:
        flash(f"Колода '{subfolder}' не найдена.", "danger")
        return redirect(url_for('admin'))

    files = request.files.getlist('images')
    uploaded_count = 0
    skipped_count = 0
    errors = []

    deck_dir = os.path.join(app.static_folder, 'images', subfolder)
    # Ensure directory exists (should exist if deck was created, but double-check)
    os.makedirs(deck_dir, exist_ok=True)


    for file in files:
        if file and file.filename:
            # Basic validation for image file types (you might need more robust checks)
            allowed_extensions = {'png', 'jpg', 'jpeg', 'gif'}
            if '.' in file.filename and file.filename.rsplit('.', 1)[1].lower() in allowed_extensions:
                filename = file.filename
                filepath = os.path.join(deck_dir, filename)

                # Check if image with the same filename already exists for this deck
                c.execute("SELECT COUNT(*) FROM images WHERE subfolder = ? AND image = ?", (subfolder, filename))
                if c.fetchone()[0] > 0:
                    skipped_count += 1
                    errors.append(f"Изображение '{filename}' уже существует в этой колоде.")
                    continue # Skip to next file

                try:
                    file.save(filepath)
                    # Insert image info into DB
                    # When uploading, status is 'Свободно' and owner_id is NULL
                    c.execute("INSERT INTO images (subfolder, image, status, owner_id) VALUES (?, ?, 'Свободно', NULL)", (subfolder, filename))
                    db.commit()
                    uploaded_count += 1
                except Exception as e:
                    errors.append(f"Ошибка при загрузке файла '{filename}': {e}")
                    print(f"Error saving or inserting image {filename}: {e}", file=sys.stderr)
                    # Clean up partially saved file if DB insert failed
                    if os.path.exists(filepath):
                        try: os.remove(filepath)
                        except: pass # Ignore clean up errors

            else:
                skipped_count += 1
                errors.append(f"Файл '{file.filename}' имеет недопустимое расширение.")

    db.commit() # Ensure any successful inserts are committed if loop broke early


    if uploaded_count > 0:
        flash(f"Успешно загружено {uploaded_count} изображени(е/й) в колоду '{subfolder}'.", "success")
    if skipped_count > 0:
        flash(f"Пропущено {skipped_count} файлов.", "warning")
    if errors:
        for error in errors:
            flash(error, "danger")

    return redirect(url_for('admin'))


# Route to handle deleting an image from a deck
@app.route('/admin/delete_image/<int:image_id>', methods=['POST'])
def admin_delete_image(image_id):
     # Check if admin is logged in (basic check)
    if not session.get('is_admin'):
         flash("Недостаточно прав.", "danger")
         return redirect(url_for('index'))

    db = get_db()
    c = db.cursor()

    try:
        # Get image info before deleting from DB
        c.execute("SELECT subfolder, image FROM images WHERE id = ?", (image_id,))
        image_info = c.fetchone()

        if not image_info:
            flash("Изображение не найдено.", "warning")
            return redirect(url_for('admin'))

        subfolder = image_info['subfolder']
        filename = image_info['image']
        filepath = os.path.join(app.static_folder, 'images', subfolder, filename)

        # Check if the image is currently on the table or in someone's hand (optional, but good practice)
        # Depending on game state, might want to prevent deletion mid-round.
        # For simplicity now, let's just delete and potentially break ongoing game state.
        # In a real app, would need to handle this gracefully (e.g., reset round).

        # Delete from DB first (also clears owner_id and status)
        c.execute("DELETE FROM images WHERE id = ?", (image_id,))
        # Delete related guesses if any (should be cleared per round, but belt-and-suspenders)
        c.execute("DELETE FROM guesses WHERE image_id = ?", (image_id,))
        db.commit()

        # Delete the physical file
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


# Index route - shows decks and login form (if not logged in)
@app.route('/')
def index():
    db = get_db()
    c = db.cursor()
    c.execute("SELECT subfolder, name FROM decks")
    all_decks = c.fetchall()
    # Fetch current active deck for display
    c.execute("SELECT active_subfolder FROM game_state WHERE id = 1")
    game_state = c.fetchone()
    active_subfolder = game_state['active_subfolder'] if game_state else None

    # Check if user is logged in (by checking session user_code)
    user_code = session.get('user_code')
    user_data = None
    if user_code:
         c.execute("SELECT id, code, name, is_admin, rating, status FROM users WHERE code = ?", (user_code,))
         user_data = c.fetchone()
         # If user data not found for code in session, clear session
         if not user_data:
              session.pop('user_code', None)
              session.pop('is_admin', None)
              flash("Ваша сессия устарела, пожалуйста, войдите снова.", "warning")


    return render_template('index.html', all_decks=all_decks, active_subfolder=active_subfolder, user_data=user_data)


# Route to handle user login (setting session user_code)
@app.route('/user_login', methods=['POST'])
def user_login():
    user_code = request.form.get('user_code')
    db = get_db()
    c = db.cursor()
    c.execute("SELECT id, code, name, is_admin, rating, status FROM users WHERE code = ?", (user_code,))
    user_data = c.fetchone()

    if user_data:
        session['user_code'] = user_data['code'] # Set user code in session
        if user_data['is_admin']:
             session['is_admin'] = True # Set admin flag if user is admin
        flash(f"Добро пожаловать, {user_data['name']}!", "success")
        # Redirect to user page
        return redirect(url_for('user', code=user_data['code']))
    else:
        flash("Неверный код пользователя.", "danger")
        # Redirect back to index page with login form
        return redirect(url_for('index'))

# Route for user logout
@app.route('/user_logout', methods=['POST'])
def user_logout():
    session.pop('user_code', None)
    session.pop('is_admin', None) # Also clear admin flag on user logout
    flash("Вы вышли из аккаунта.", "info")
    return redirect(url_for('index'))


# Route to handle admin selecting active deck
@app.route('/admin/set_active_deck/<subfolder>', methods=['POST'])
def admin_set_active_deck_route(subfolder):
     # Check if admin is logged in
     if not session.get('is_admin'):
         flash("Недостаточно прав.", "danger")
         return redirect(url_for('index'))

     db = get_db()
     c = db.cursor()

     # Check if the deck exists
     c.execute("SELECT COUNT(*) FROM decks WHERE subfolder = ?", (subfolder,))
     if c.fetchone()[0] == 0:
          flash(f"Колода '{subfolder}' не найдена.", "danger")
          return redirect(url_for('admin'))

     # Check if game is in progress (optional, but good practice)
     c.execute("SELECT game_in_progress FROM game_state WHERE id = 1")
     game_state = c.fetchone()
     if game_state and game_state['game_in_progress']:
          flash("Нельзя сменить активную колоду во время игры.", "warning")
          return redirect(url_for('admin'))

     c.execute("UPDATE game_state SET active_subfolder = ? WHERE id = 1", (subfolder,))
     db.commit()
     flash(f"Активная колода изменена на '{subfolder}'.", "success")

     # Broadcast game update
     broadcast_game_update()

     return redirect(url_for('admin'))


# If running directly, start development server
if __name__ == "__main__":
    # Initialization is now handled outside this block when the module is loaded.
    # This block is primarily for running the development server directly.
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    # Ensure allow_unsafe_werkzeug=True is used only in development
    socketio.run(app, host="0.0.0.0", port=port, debug=debug, allow_unsafe_werkzeug=True)
