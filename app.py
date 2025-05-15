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
import click # Import click for command line interface

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
_current_game_board_num_cells = DEFAULT_NUM_BOARD_CELLS


connected_users_socketio = {}  # {sid: user_code}


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=10)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

# Modified init_db_command to include schema directly
@app.cli.command('init-db')
def init_db_command():
    """Clear existing data and create new tables."""
    db = get_db()
    cursor = db.cursor()

    # --- Database Schema Creation Directly in app.py ---
    cursor.executescript("""
        DROP TABLE IF EXISTS users;
        DROP TABLE IF EXISTS decks;
        DROP TABLE IF EXISTS images;
        DROP TABLE IF EXISTS game_state;
        DROP TABLE IF EXISTS guesses;
        DROP TABLE IF EXISTS game_board_visuals; -- Added table for board visuals

        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            is_admin BOOLEAN DEFAULT FALSE,
            rating INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending' -- 'pending', 'active', 'inactive'
        );

        CREATE TABLE decks (
            subfolder TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            votes INTEGER DEFAULT 0
        );

        CREATE TABLE images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subfolder TEXT NOT NULL,
            image TEXT NOT NULL,
            status TEXT DEFAULT 'Свободно', -- 'Свободно', 'Занято:user_id', 'На столе:user_id'
            FOREIGN KEY (subfolder) REFERENCES decks (subfolder)
        );

        CREATE TABLE game_state (
            id INTEGER PRIMARY KEY DEFAULT 1,
            game_in_progress BOOLEAN DEFAULT FALSE,
            game_over BOOLEAN DEFAULT FALSE,
            current_leader_id INTEGER,
            active_subfolder TEXT,
            on_table_status BOOLEAN DEFAULT FALSE, -- True when players place cards
            show_card_info BOOLEAN DEFAULT FALSE, -- True when cards are revealed
            next_leader_id INTEGER, -- <<< Added next_leader_id column
            leader_pole_image_path TEXT, -- Added to store path of leader's board image
            leader_pictogram_rating INTEGER, -- Added to store leader's rating for pictogram
            current_num_board_cells INTEGER DEFAULT 40, -- Store num cells
            FOREIGN KEY (current_leader_id) REFERENCES users (id),
            FOREIGN KEY (active_subfolder) REFERENCES decks (subfolder),
            FOREIGN KEY (next_leader_id) REFERENCES users (id)
        );

        CREATE TABLE guesses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, -- Who made the guess
            image_id INTEGER NOT NULL, -- The card the user voted on (image id from 'images' table)
            guessed_user_id INTEGER NOT NULL, -- The owner the user guessed (user id from 'users' table)
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (image_id) REFERENCES images (id),
            FOREIGN KEY (guessed_user_id) REFERENCES users (id)
        );

        -- Table to store game board visual configuration
        CREATE TABLE game_board_visuals (
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


    click.echo('Initialized the database.')

app.cli.add_command(init_db_command) # Register the command


def broadcast_game_update(user_code=None):
    """Sends the current game state to all connected users or a specific user."""
    # print(f"Broadcasting game update. Target user_code: {user_code}", file=sys.stderr)
    if user_code:
         # Send only to a specific user
         for sid, code in connected_users_socketio.items():
             if code == user_code:
                 try:
                    state = state_to_json(user_code_for_state=user_code)
                    emit('game_update', state, room=sid)
                    # print(f"Sent update to SID: {sid} ({user_code})", file=sys.stderr)
                 except Exception as e:
                     print(f"Error sending update to SID {sid} ({user_code}): {e}\n{traceback.format_exc()}", file=sys.stderr)

    else:
        # Broadcast to all connected users
        state_for_all = state_to_json() # Get state without user-specific data first

        # Now, iterate through connected users to get user-specific state
        for sid, code in connected_users_socketio.items():
             try:
                 user_specific_state = state_to_json(user_code_for_state=code)
                 # Merge user-specific data into the general state if needed, or send user-specific state directly
                 # Sending user-specific state directly is simpler if state_to_json handles it.
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
        # If game_state row doesn't exist (should be created by init-db), return default
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
             leader_card_placed = cursor.fetchone()[0] > 0

             if placed_cards_count_excluding_leader == active_players_count_excluding_leader and leader_card_placed:
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
         # Fallback if board visuals not in DB (should be initialized by init-db)
         print("WARNING: Game board visuals not found in DB!", file=sys.stderr)
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
             min_rating = 0 if cell_config['id'] == 1 else game_board_visual_config[cell_config['id']-2]['max_rating'] + 1 # Rating threshold from previous cell
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
        'current_num_board_cells': _current_game_board_num_cells, # Send the determined number of cells
        'leader_pole_pictogram_path': leader_pole_image_path, # Pass leader board image path
        'leader_pictogram_rating_display': leader_pictogram_rating_display, # Pass leader rating for pictogram
    }

# Helper to broadcast update
def broadcast_game_update(user_code=None):
    """Sends the current game state to all connected users or a specific user."""
    if user_code:
         # Send only to a specific user
         for sid, code in connected_users_socketio.items():
             if code == user_code:
                 try:
                    state = state_to_json(user_code_for_state=user_code)
                    emit('game_update', state, room=sid)
                 except Exception as e:
                     print(f"Error sending update to SID {sid} ({user_code}): {e}\n{traceback.format_exc()}", file=sys.stderr)

    else:
        # Broadcast to all connected users
        for sid, code in connected_users_socketio.items():
             try:
                 user_specific_state = state_to_json(user_code_for_state=code)
                 emit('game_update', user_specific_state, room=sid)
             except Exception as e:
                 print(f"Error sending update to SID {sid} ({code}): {e}\n{traceback.format_exc()}", file=sys.stderr)


# ... (rest of your routes and functions before SocketIO events and cli commands) ...

@app.route('/')
def index():
    # Your existing index route logic
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT subfolder, name, votes FROM decks ORDER BY votes DESC")
    decks = cursor.fetchall()
    return render_template('index.html', deck_votes=decks)

@app.route('/vote', methods=['POST'])
def vote_deck():
    # Your existing vote_deck route logic
    subfolder = request.form.get('subfolder')
    if not subfolder:
        flash("Не указана колода для голосования.", "warning")
        return redirect(url_for('index'))

    db = get_db()
    cursor = db.cursor()

    # Check if deck exists
    cursor.execute("SELECT * FROM decks WHERE subfolder = ?", (subfolder,))
    deck = cursor.fetchone()
    if not deck:
        flash("Выбранная колода не найдена.", "danger")
        return redirect(url_for('index'))

    # Get user's current vote from session
    current_vote = session.get('voted_for_deck')

    if current_vote == subfolder:
        # User voted for the same deck again, remove vote
        cursor.execute("UPDATE decks SET votes = votes - 1 WHERE subfolder = ?", (subfolder,))
        session.pop('voted_for_deck', None)
        flash(f"Голос за колоду '{deck['name']}' отозван.", "info")
    else:
        # User voted for a different deck or first vote
        if current_vote:
            # Remove previous vote
            cursor.execute("UPDATE decks SET votes = votes - 1 WHERE subfolder = ?", (current_vote,))
        # Add new vote
        cursor.execute("UPDATE decks SET votes = votes + 1 WHERE subfolder = ?", (subfolder,))
        session['voted_for_deck'] = subfolder
        flash(f"Голос за колоду '{deck['name']}' принят.", "success")

    db.commit()
    return redirect(url_for('index'))

# ... (rest of the routes like /admin, /login, /logout, /login_player, /user/<user_code>) ...

# Example route for user page (assuming it fetches initial state)
@app.route('/user/<user_code>')
def user(user_code):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM users WHERE code = ?", (user_code,))
    user_data = cursor.fetchone()

    if user_data:
         # Store user_code in session to link HTTP requests to user
        session['user_code'] = user_code

        # Initial state will be sent via SocketIO on connect
        return render_template('user.html', user_data_for_init=dict(user_data))
    else:
        flash("Неверный код пользователя.", "danger")
        return redirect(url_for('login_player'))

# Example route for placing a card (needs game logic updates and broadcasting)
@app.route('/user/<user_code>/place/<int:card_id>', methods=['POST'])
def place_card(user_code, card_id):
    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT * FROM users WHERE code = ? AND status = 'active'", (user_code,))
    user = cursor.fetchone()
    if not user:
        flash("Пользователь не активен.", "warning")
        return redirect(url_for('user', user_code=user_code)) # Redirect back to user page

    # Check game state
    cursor.execute("SELECT * FROM game_state WHERE id = 1")
    game_state = cursor.fetchone()
    if not game_state or not game_state['game_in_progress'] or game_state['game_over']:
         flash("Игра не в процессе или окончена.", "warning")
         return redirect(url_for('user', user_code=user_code))

    current_leader_id = game_state['current_leader_id']
    on_table_status = game_state['on_table_status']
    show_card_info = game_state['show_card_info']

    if on_table_status or show_card_info:
         flash("Карточки уже выложены или открыты. Ожидайте следующий раунд.", "warning")
         return redirect(url_for('user', user_code=user_code))


    # Check if the card belongs to the user and is available
    cursor.execute("SELECT * FROM images WHERE id = ? AND status = ?", (card_id, f'Занято: {user["id"]}',))
    card = cursor.fetchone()
    if not card:
        flash("У вас нет такой карточки или она уже не доступна.", "warning")
        return redirect(url_for('user', user_code=user_code))

    # Check if user is the current leader and if they have already placed a card
    if user['id'] == current_leader_id:
        cursor.execute("SELECT COUNT(*) FROM images WHERE status LIKE 'На столе:%' AND owner_id = ?", (user['id'],))
        if cursor.fetchone()[0] > 0:
            flash("Вы уже выложили карточку как Ведущий.", "warning")
            return redirect(url_for('user', user_code=user_code))

    # Check if user is a player (not leader) and if they have already placed a card
    if user['id'] != current_leader_id:
        cursor.execute("SELECT COUNT(*) FROM images WHERE status LIKE 'На столе:%' AND owner_id = ?", (user['id'],))
        if cursor.fetchone()[0] > 0:
            flash("Вы уже выложили карточку.", "warning")
            return redirect(url_for('user', user_code=user_code))

    # Place the card on the table
    new_status = f'На столе: {user["id"]}'
    cursor.execute("UPDATE images SET status = ? WHERE id = ?", (new_status, card_id))

    # Check if all players (excluding leader) have placed their cards OR if leader placed and no other players
    cursor.execute("SELECT COUNT(*) FROM users WHERE status = 'active' AND id != ?", (current_leader_id,))
    active_players_count_excluding_leader = cursor.fetchone()[0]

    all_placed = False
    if user['id'] == current_leader_id:
         # Leader placed card
         if active_players_count_excluding_leader == 0:
              # Only leader is active, immediately transition to scoring (or end round)
              all_placed = True
         else:
              # Leader placed, waiting for other players
              pass # Don't set on_table_status yet

    else: # Player placed card
         cursor.execute("SELECT COUNT(DISTINCT owner_id) FROM images WHERE status LIKE 'На столе:%' AND owner_id != ?", (current_leader_id,))
         placed_players_count_excluding_leader = cursor.fetchone()[0]

         cursor.execute("SELECT COUNT(*) FROM images WHERE status = ? AND owner_id = ?", (f'На столе: {current_leader_id}', current_leader_id))
         leader_card_is_on_table = cursor.fetchone()[0] > 0

         if placed_players_count_excluding_leader == active_players_count_excluding_leader and leader_card_is_on_table:
              all_placed = True


    if all_placed:
        # All cards are on the table, transition to guessing phase
        cursor.execute("UPDATE game_state SET on_table_status = 1, show_card_info = 0") # Set on_table_status = True
        flash("Все карточки на столе! Начинайте угадывать.", "info")
    else:
         # Leader placed or player placed, but waiting for others
         if user['id'] == current_leader_id:
              flash("Вы выложили карточку как Ведущий. Ожидайте карточки от игроков.", "info")
              # If leader placed, we should maybe indicate on_table_status just for leader?
              # Or wait until first player places? Let's set on_table_status=1 once ALL cards are placed.
         else:
             flash("Вы выложили карточку. Ожидайте остальных игроков.", "info")


    db.commit()

    # Broadcast game update to all connected users
    broadcast_game_update()

    return redirect(url_for('user', user_code=user_code))


# Example route for guessing a card (needs game logic updates and broadcasting)
@app.route('/user/<user_code>/guess/<int:card_id>', methods=['POST'])
def guess_card(user_code, card_id):
    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT * FROM users WHERE code = ? AND status = 'active'", (user_code,))
    user = cursor.fetchone()
    if not user:
        flash("Пользователь не активен.", "warning")
        return redirect(url_for('user', user_code=user_code))

    guessed_user_id = request.form.get('guessed_user_id')
    if not guessed_user_id:
        flash("Не выбран владелец карточки.", "warning")
        return redirect(url_for('user', user_code=user_code))

    try:
        guessed_user_id = int(guessed_user_id)
    except ValueError:
        flash("Неверный формат ID владельца карточки.", "danger")
        return redirect(url_for('user', user_code=user_code))


    # Check game state
    cursor.execute("SELECT * FROM game_state WHERE id = 1")
    game_state = cursor.fetchone()
    if not game_state or not game_state['game_in_progress'] or game_state['game_over'] or not game_state['on_table_status'] or game_state['show_card_info']:
         flash("Сейчас нельзя угадывать.", "warning")
         return redirect(url_for('user', user_code=user_code))

    # Check if the card exists on the table
    cursor.execute("SELECT id, owner_id FROM images WHERE id = ? AND status LIKE 'На столе:%'", (card_id,))
    card_on_table = cursor.fetchone()
    if not card_on_table:
        flash("Выбранная карточка не на столе.", "warning")
        return redirect(url_for('user', user_code=user_code))

    card_owner_id = card_on_table['owner_id']

    # User cannot guess their own card
    if card_owner_id == user['id']:
         flash("Вы не можете угадывать свою карточку.", "warning")
         return redirect(url_for('user', user_code=user_code))

    # User cannot guess the leader's card if they are the leader
    current_leader_id = game_state['current_leader_id']
    if user['id'] == current_leader_id:
         flash("Ведущий не угадывает карточки игроков на этом этапе.", "warning")
         return redirect(url_for('user', user_code=user_code))


    # Check if the guessed_user_id is a valid active user (and not the current guesser)
    cursor.execute("SELECT id FROM users WHERE id = ? AND status = 'active'", (guessed_user_id,))
    guessed_owner_user = cursor.fetchone()
    if not guessed_owner_user:
        flash("Выбран неверный владелец карточки.", "warning")
        return redirect(url_for('user', user_code=user_code))

    if guessed_user_id == user['id']:
         flash("Вы не можете угадывать себя как владельца.", "warning")
         return redirect(url_for('user', user_code=user_code))

    # Check if the chosen owner actually has a card on the table
    cursor.execute("SELECT COUNT(*) FROM images WHERE owner_id = ? AND status LIKE 'На столе:%'", (guessed_user_id,))
    if cursor.fetchone()[0] == 0:
         flash("У выбранного игрока нет карточки на столе.", "warning")
         return redirect(url_for('user', user_code=user_code))


    # Record the guess (or update if already exists)
    cursor.execute("SELECT COUNT(*) FROM guesses WHERE user_id = ? AND image_id = ?", (user['id'], card_id))
    guess_exists = cursor.fetchone()[0] > 0

    if guess_exists:
        cursor.execute("UPDATE guesses SET guessed_user_id = ? WHERE user_id = ? AND image_id = ?", (guessed_user_id, user['id'], card_id))
        flash("Ваш голос изменен.", "success")
    else:
        cursor.execute("INSERT INTO guesses (user_id, image_id, guessed_user_id) VALUES (?, ?, ?)", (user['id'], card_id, guessed_user_id))
        flash("Ваш голос принят.", "success")

    db.commit()

    # Check if all players (excluding leader) have made a guess for ALL cards on the table (excluding their own)
    # This logic might need refinement depending on exactly how guessing works
    # A simpler trigger for ending guessing might be a separate "End Guessing" button for the leader,
    # or after a certain time, or when all players have guessed for all cards they are allowed to guess on.
    # For now, we will assume the leader triggers the end of the guessing phase.

    # Broadcast game update to all connected users
    broadcast_game_update()

    return redirect(url_for('user', user_code=user_code))


# Route for leader to end the guessing phase and show results
@app.route('/user/<user_code>/show_results', methods=['POST'])
def show_results(user_code):
    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT id, status FROM users WHERE code = ? AND status = 'active'", (user_code,))
    user = cursor.fetchone()
    if not user:
        flash("Пользователь не активен.", "warning")
        return redirect(url_for('user', user_code=user_code))

    # Check game state
    cursor.execute("SELECT * FROM game_state WHERE id = 1")
    game_state = cursor.fetchone()
    if not game_state or not game_state['game_in_progress'] or game_state['game_over'] or not game_state['on_table_status'] or game_state['show_card_info']:
         flash("Сейчас нельзя показать результаты.", "warning")
         return redirect(url_for('user', user_code=user_code))

    current_leader_id = game_state['current_leader_id']

    # Only the current leader can show results
    if user['id'] != current_leader_id:
         flash("Только Ведущий может показать результаты.", "warning")
         return redirect(url_for('user', user_code=user_code))

    # Set show_card_info to True
    cursor.execute("UPDATE game_state SET show_card_info = 1")
    db.commit()

    flash("Результаты раунда показаны!", "info")

    # Broadcast game update to all connected users
    broadcast_game_update()

    return redirect(url_for('user', user_code=user_code))

# Route for leader to end the round and start scoring/next leader phase
@app.route('/user/<user_code>/end_round', methods=['POST'])
def end_round_route(user_code):
    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT id, status FROM users WHERE code = ? AND status = 'active'", (user_code,))
    user = cursor.fetchone()
    if not user:
        flash("Пользователь не активен.", "warning")
        return redirect(url_for('user', user_code=user_code))

    # Check game state
    cursor.execute("SELECT * FROM game_state WHERE id = 1")
    game_state = cursor.fetchone()
    # Allow ending the round only after cards are revealed (show_card_info = 1)
    if not game_state or not game_state['game_in_progress'] or game_state['game_over'] or not game_state['show_card_info']:
         flash("Сейчас нельзя завершить раунд. Результаты еще не показаны.", "warning")
         return redirect(url_for('user', user_code=user_code))

    current_leader_id = game_state['current_leader_id']

    # Only the current leader can end the round
    if user['id'] != current_leader_id:
         flash("Только Ведущий может завершить раунд.", "warning")
         return redirect(url_for('user', user_code=user_code))

    # Call the internal end_round logic
    end_round() # This function now handles scoring, next leader, and broadcasting

    return redirect(url_for('user', user_code=user_code))


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
    cursor.execute("SELECT COUNT(*) FROM users WHERE status = 'active' AND rating >= ?", (_current_game_board_num_cells,)) # Assuming _current_game_board_num_cells is the target rating
    players_at_end = cursor.fetchone()[0]
    if players_at_end > 0:
        game_over = True
        flash("Игра окончена! Игрок достиг конца игрового поля.", "success")
        cursor.execute("UPDATE game_state SET game_over = 1, game_in_progress = 0") # Set game_over and end game


    # Determine the next leader (user with the highest rating)
    # Only determine next leader if the game is NOT over
    next_leader_id = None
    if not game_over:
         cursor.execute("SELECT id FROM users WHERE status = 'active' ORDER BY rating DESC LIMIT 1")
         next_leader_row = cursor.fetchone()
         next_leader_id = next_leader_row['id'] if next_leader_row else None # Set to None if no active users


    # --- ADDITION START: Update game_state with next_leader_id and reset flags ---
    # Reset on_table_status and show_card_info for the next round
    # Store next_leader_id in the game_state table
    cursor.execute("UPDATE game_state SET on_table_status = 0, show_card_info = 0, current_leader_id = ?, next_leader_id = ?", (next_leader_id, next_leader_id)) # Set the new current leader and the next leader for the round AFTER next (or just the next one if this field is always the *next* leader)
    # Let's clarify the logic: after end_round, the *next* leader is determined. This player should be the leader for the *next* round (which starts after this round fully finishes, likely when a new_round or start_new_game is called).
    # So, the determined next_leader_id here should probably become the current_leader_id for the *next* round.
    # Let's set current_leader_id for the *next* round here, and clear next_leader_id until the end of *that* round.
    # Alternative: next_leader_id always stores the ID of the leader for the *upcoming* round.
    # Let's go with the simpler logic: Update current_leader_id to the next determined leader, and clear next_leader_id until the end of the round this new leader leads.
    cursor.execute("UPDATE game_state SET on_table_status = 0, show_card_info = 0, current_leader_id = ?, next_leader_id = NULL", (next_leader_id,)) # Set the new current leader, clear next_leader_id
    # Correction: The frontend wants to display the *next* leader while the current round's results are shown.
    # So end_round should determine the leader for the *next* round and store it in `next_leader_id`.
    # The `start_new_round` function should then move `next_leader_id` to `current_leader_id`.
    # Let's revert the update in end_round: it should ONLY determine and store `next_leader_id`. `current_leader_id` should remain the leader of the round just ended until the next round starts.

    # Reverting update in end_round - only set next_leader_id
    cursor.execute("UPDATE game_state SET on_table_status = 0, show_card_info = 1, next_leader_id = ?", (next_leader_id,)) # Keep showing results, set next_leader_id

    # --- ADDITION END ---

    # Reset image statuses from 'На столе' to 'Свободно'
    cursor.execute("UPDATE images SET status = 'Свободно' WHERE status LIKE 'На столе:%' AND subfolder = ?", (active_subfolder,))

    # Delete all guesses
    cursor.execute("DELETE FROM guesses")

    db.commit()

    if not game_over:
         flash("Раунд завершен! Подсчет очков выполнен. Определен следующий ведущий.", "success")
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

    # --- ADDITION START: Transition from next_leader_id to current_leader_id ---
    next_leader_id = game_state['next_leader_id'] if game_state and 'next_leader_id' in game_state else None
    if next_leader_id is None:
         # If no next leader is set (e.g., first round or after game over), determine initial leader
         cursor.execute("SELECT id FROM users WHERE status = 'active' ORDER BY RANDOM() LIMIT 1") # Example: random first leader
         initial_leader_row = cursor.fetchone()
         current_leader_id = initial_leader_row['id'] if initial_leader_row else None
         if current_leader_id is None:
              flash("Недостаточно активных игроков для начала раунда.", "warning")
              return redirect(url_for('index')) # Or handle appropriately
         # Clear next_leader_id as it's now the current leader
         cursor.execute("UPDATE game_state SET current_leader_id = ?, next_leader_id = NULL WHERE id = 1", (current_leader_id,))

    else:
         # Use the determined next leader from the previous round as the current leader
         current_leader_id = next_leader_id
         # Clear next_leader_id as it's now the current leader for this round
         cursor.execute("UPDATE game_state SET current_leader_id = ?, next_leader_id = NULL WHERE id = 1", (current_leader_id,))


    db.commit() # Commit leader update

    # --- ADDITION END ---


    # Reset image statuses
    cursor.execute("UPDATE images SET status = 'Свободно'")

    # Delete all guesses
    cursor.execute("DELETE FROM guesses")
    db.commit()

    # Deal cards to active players (each needs 6 cards - adjust as per rules)
    cursor.execute("SELECT id FROM users WHERE status = 'active'")
    active_users = [row['id'] for row in cursor.fetchall()]

    if not active_users:
        flash("Нет активных игроков для раздачи карточек.", "warning") # ИЗМЕНЕНИЕ: "нет карт" -> "нет карточек"
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
        return redirect(url_for('index'))


    cursor.execute("SELECT id FROM images WHERE status = 'Свободно' AND subfolder = ?", (active_subfolder,))
    available_image_ids = [row['id'] for row in cursor.fetchall()]

    num_cards_per_player = 6 # Adjust as per your game rules
    required_cards = len(active_users) * num_cards_per_player

    if len(available_image_ids) < required_cards:
        flash(f"Недостаточно свободных карточек ({len(available_image_ids)}) в активной колоде '{active_subfolder}' для раздачи {required_cards} карточек.", "danger") # ИЗМЕНЕНИЕ: "карт" -> "карточек"
        # Reset game state
        cursor.execute("UPDATE game_state SET game_in_progress = 0, on_table_status = 0, show_card_info = 0, current_leader_id = NULL, next_leader_id = NULL")
        db.commit()
        broadcast_game_update()
        return redirect(url_for('index'))


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

    flash("Новый раунд начат! Карточки розданы.", "success") # ИЗМЕНЕНИЕ: "Карты" -> "Карточки"

    # Broadcast game update to all connected users
    broadcast_game_update()

    return redirect(url_for('index')) # Redirect or return appropriate response


# Route for admin to start a new game (resets ratings, status, etc.)
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
    # Determine the very first leader randomly from users who are now 'pending' or will become 'active'
    # A common approach is to select from all users, or from those who will become active.
    # Let's assume we select from users who will be manually activated by admin later.
    # Or, select a random user with rating > 0 if any exist from previous games, or just a random one.
    # For simplicity, let's not set a leader here. The first round will determine the leader.
    # Or, select a random user to be the very first leader of the first round.
    cursor.execute("SELECT id FROM users ORDER BY RANDOM() LIMIT 1")
    first_leader_row = cursor.fetchone()
    initial_leader_id = first_leader_row['id'] if first_leader_row else None


    # Reset game_state row
    cursor.execute("""
        UPDATE game_state SET
        game_in_progress = FALSE,
        game_over = FALSE,
        current_leader_id = ?, -- Set the first leader
        active_subfolder = NULL, -- Active deck needs to be chosen again
        on_table_status = FALSE,
        show_card_info = FALSE,
        next_leader_id = NULL -- Ensure next_leader_id is NULL at the start of a new game
        WHERE id = 1
    """, (initial_leader_id,)) # Set the initial current leader

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


# Example SocketIO event for testing or specific actions (optional)
# @socketio.on('request_game_state')
# def handle_request_game_state(data):
#     sid = request.sid
#     user_code = connected_users_socketio.get(sid)
#     if user_code:
#         print(f"SocketIO: Received request_game_state from {user_code} (SID: {sid})", file=sys.stderr)
#         broadcast_game_update(user_code=user_code) # Send update only to the requester


# Command to initialize game board visuals from default config
@app.cli.command('init-board-visuals')
def init_board_visuals_command():
    """Initializes game_board_visuals table with default data."""
    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT COUNT(*) FROM game_board_visuals")
    if cursor.fetchone()[0] == 0:
        print("Initializing game board visuals from command...", file=sys.stderr)
        if _default_board_config:
             cursor.executemany("INSERT INTO game_board_visuals (id, image, max_rating) VALUES (?, ?, ?)",
                               [(cell['id'], cell['image'], cell['max_rating']) for cell in _default_board_config])
             db.commit()
             print(f"Inserted {len(_default_board_config)} game board visual entries.", file=sys.stderr)
        else:
             print("WARNING: _default_board_config is empty. Cannot initialize game board visuals.", file=sys.stderr)
    else:
        print("Game board visuals table is already initialized.", file=sys.stderr)


if __name__ == "__main__":
    # Moved initialization logic into init_db_command and init-board-visuals command
    # Ensure init-db is run at least once before starting the app the first time
    # Ensure init-board-visuals is run at least once if not part of init-db

    # If you need to run initial board visuals setup when the app starts *without*
    # running init-db command, you can uncomment and adjust the logic below.
    # However, it's cleaner to use the Flask commands.

    # if not _current_game_board_pole_image_config:
    #     print("Loading game board visuals from DB or using default...", file=sys.stderr)
    #     try:
    #          with app.app_context():
    #               db = get_db()
    #               cursor = db.cursor()
    #               cursor.execute("SELECT id, image, max_rating FROM game_board_visuals ORDER BY id")
    #               board_config_rows = cursor.fetchall()
    #               if board_config_rows:
    #                    global _current_game_board_pole_image_config
    #                    global _current_game_board_num_cells
    #                    _current_game_board_pole_image_config = [dict(row) for row in board_config_rows]
    #                    _current_game_board_num_cells = _current_game_board_pole_image_config[-1]['max_rating']
    #                    print("Loaded game board visuals from DB.", file=sys.stderr)
    #               else:
    #                    # If DB is empty, use default and maybe print warning to run init command
    #                    print("WARNING: Game board visuals table is empty. Using default config. Run 'flask init-board-visuals'.", file=sys.stderr)
    #                    _current_game_board_pole_image_config = _default_board_config
    #                    _current_game_board_num_cells = DEFAULT_NUM_BOARD_CELLS
    #     except Exception as e:
    #          print(f"Error loading game board visuals: {e}\n{traceback.format_exc()}", file=sys.stderr)
    #          # Fallback to default if error occurs
    #          _current_game_board_pole_image_config = _default_board_config
    #          _current_game_board_num_cells = DEFAULT_NUM_BOARD_CELLS


    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    socketio.run(app, host="0.0.0.0", port=port, debug=debug, allow_unsafe_werkzeug=True)
