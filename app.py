import json
from flask import Flask, render_template, request, redirect, url_for, g, flash, session
import sqlite3
import os
import string
import random

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')
if not app.config['SECRET_KEY']:
    raise ValueError("Не установлена переменная окружения SECRET_KEY!")
    
DB_PATH = 'database.db'

# --- Конфігурація для Ігрового Поля ---
GAME_BOARD_POLE_IMG_SUBFOLDER = "pole"
GAME_BOARD_POLE_IMAGES = [f"p{i}.jpg" for i in range(1, 8)] 
DEFAULT_NUM_BOARD_CELLS = 25 

_current_game_board_pole_image_config = []
_current_game_board_num_cells = 0
# --- Кінець Конфігурації ---

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

# --- Функції для ігрового поля (залишаються з попередньої версії) ---
def initialize_new_game_board_visuals(num_cells_for_board=None, all_users_for_rating_check=None):
    global _current_game_board_pole_image_config, _current_game_board_num_cells
    if num_cells_for_board is None:
        max_rating = 0
        if all_users_for_rating_check:
            for user_data in all_users_for_rating_check:
                user_rating = None
                if hasattr(user_data, 'rating'): 
                    user_rating = getattr(user_data, 'rating', 0)
                elif isinstance(user_data, dict) or hasattr(user_data, 'keys'): 
                    user_rating = user_data.get('rating', 0)
                if isinstance(user_rating, int) and user_rating > max_rating:
                    max_rating = user_rating
        _current_game_board_num_cells = max(DEFAULT_NUM_BOARD_CELLS, max_rating + 5)
    else:
        _current_game_board_num_cells = num_cells_for_board
    _current_game_board_pole_image_config = []
    if GAME_BOARD_POLE_IMAGES:
        for _ in range(_current_game_board_num_cells):
            random_pole_image_file = random.choice(GAME_BOARD_POLE_IMAGES)
            image_path = f"{GAME_BOARD_POLE_IMG_SUBFOLDER}/{random_pole_image_file}"
            _current_game_board_pole_image_config.append(image_path)
    else:
        print("ПОПЕРЕДЖЕННЯ: GAME_BOARD_POLE_IMAGES порожній. Використовуються placeholder'и.")
        _current_game_board_pole_image_config = [f"{GAME_BOARD_POLE_IMG_SUBFOLDER}/placeholder_pole.jpg"] * _current_game_board_num_cells
    print(f"Візуалізацію нового ігрового поля ініціалізовано для {_current_game_board_num_cells} клітинок.")

def generate_game_board_data_for_display(all_users_data):
    global _current_game_board_pole_image_config, _current_game_board_num_cells
    if not _current_game_board_pole_image_config or _current_game_board_num_cells == 0:
        print("ПОПЕРЕДЖЕННЯ: Візуалізація ігрового поля не ініціалізована! Спроба авто-ініціалізації.")
        initialize_new_game_board_visuals(num_cells_for_board=DEFAULT_NUM_BOARD_CELLS, all_users_for_rating_check=all_users_data)
        if not _current_game_board_pole_image_config or _current_game_board_num_cells == 0:
            print("ПОМИЛКА: Не вдалося ініціалізувати ігрове поле.")
            return []
    board_cells_data = []
    for i in range(_current_game_board_num_cells):
        cell_number = i + 1
        cell_image_path = ""
        if i < len(_current_game_board_pole_image_config):
            cell_image_path = _current_game_board_pole_image_config[i]
        else:
            print(f"ПОПЕРЕДЖЕННЯ: Немає конфігурації зображення для клітинки {cell_number}.")
            cell_image_path = f"{GAME_BOARD_POLE_IMG_SUBFOLDER}/placeholder_pole.jpg"
        users_in_this_cell = []
        for user_data in all_users_data:
            user_rating, user_name, user_id_for_name = None, None, 'N/A'
            if hasattr(user_data, 'rating'):
                user_rating = getattr(user_data, 'rating', None)
                user_name = getattr(user_data, 'name', None)
                user_id_for_name = getattr(user_data, 'id', 'N/A')
            elif isinstance(user_data, dict) or hasattr(user_data, 'keys'):
                user_rating = user_data.get('rating')
                user_name = user_data.get('name')
                user_id_for_name = user_data.get('id', 'N/A')
            if isinstance(user_rating, int) and user_rating == cell_number:
                display_name = user_name if user_name else f"ID {user_id_for_name}"
                users_in_this_cell.append({'name': display_name, 'rating': user_rating})
        board_cells_data.append({
            'cell_number': cell_number,
            'image_path': cell_image_path,
            'users_in_cell': users_in_this_cell
        })
    return board_cells_data

# --- Инициализация БД ---
def init_db():
    # ... (ваш код init_db залишається без змін) ...
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row 
    c = conn.cursor()
    print("init_db: Connection opened.") 

    try: 
        print("init_db: Dropping tables...")
        c.execute("DROP TABLE IF EXISTS users")
        c.execute("DROP TABLE IF EXISTS images")
        c.execute("DROP TABLE IF EXISTS settings")
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


# --- Вспомогательные функции (залишаються ваші) ---
def is_game_over():
    return get_setting('game_over') == 'true'

def set_game_over(state=True):
    return set_setting('game_over', 'true' if state else 'false')

def generate_unique_code(length=8):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def get_setting(key):
    try:
        db_conn = get_db()
        c = db_conn.cursor()
        c.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = c.fetchone()
        return row['value'] if row else None
    except sqlite3.Error as e:
        print(f"Database error in get_setting for key '{key}': {e}")
        return None

def set_setting(key, value):
    db_conn = get_db()
    try:
        c = db_conn.cursor()
        c.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        db_conn.commit()
        return True
    except sqlite3.Error as e:
        print(f"Database error in set_setting for key '{key}': {e}")
        db_conn.rollback()
        return False

def get_leading_user_id():
    value = get_setting('leading_user_id')
    if value:
        try:
            return int(value)
        except (ValueError, TypeError):
            print(f"Invalid leading_user_id value found in settings: {value}")
            return None
    return None

def set_leading_user_id(user_id):
    value_to_set = str(user_id) if user_id is not None else '' # Зберігаємо '' якщо user_id is None
    return set_setting('leading_user_id', value_to_set)

def get_user_name(user_id):
    if user_id is None:
        return None
    try:
        user_id_int = int(user_id) # Переконуємося, що це число
        db_conn = get_db()
        c = db_conn.cursor()
        c.execute("SELECT name FROM users WHERE id = ?", (user_id_int,))
        user_name_row = c.fetchone()
        return user_name_row['name'] if user_name_row else None
    except (ValueError, TypeError, sqlite3.Error) as e: # ValueError для int(), TypeError для None
        print(f"Error in get_user_name for ID '{user_id}': {e}")
        return None

# --- Глобальные переменные и функции для Jinja ---
app.jinja_env.globals.update(
    get_user_name=get_user_name,
    get_leading_user_id=get_leading_user_id)

# --- ВІДНОВЛЕНО/ДОДАНО: Маршрут для відкриття карт та підрахунку результатів ---
@app.route('/open_cards', methods=['POST'])
def open_cards(): 
    if not session.get('is_admin'):
        flash('Тільки адміністратор може виконувати цю дію.', 'danger')
        return redirect(url_for('admin'))

    db_conn = get_db()
    c = db_conn.cursor()
    print("--- Початок дії: Відкриття карт та підрахунок очок ---")
    new_leader_id_for_redirect = get_leading_user_id() # За замовчуванням

    try:
        set_setting('show_card_info', 'true')
        print("Налаштування 'show_card_info' встановлено на 'true'.")

        c.execute("SELECT id, owner_id, guesses FROM images WHERE status LIKE 'На столе:%'")
        table_cards = c.fetchall()

        if not table_cards:
            flash("На столі немає карт для підрахунку очок. Карти відкрито.", "info") 
            # Карти "відкрито" (show_card_info = true), але очок немає. Ведучий зміниться.
        else:
            c.execute("SELECT id, rating FROM users")
            users_ratings_list = c.fetchall()
            user_ratings = {row['id']: row['rating'] for row in users_ratings_list}
            points_awarded_info = {}

            for card in table_cards:
                actual_owner_id = card['owner_id']
                guesses_for_card = json.loads(card['guesses'] or '{}')

                if actual_owner_id is None:
                    print(f"Попередження: Карта ID {card['id']} на столі, але не має власника.")
                    continue

                card_guessed_correctly_by_someone = False
                for guesser_id_str, guessed_owner_id_str in guesses_for_card.items():
                    try:
                        guesser_id = int(guesser_id_str)
                        # guessed_owner_id - це ID користувача, якого вгадали як власника
                        # карти card['id']
                        guessed_owner_id = int(guessed_owner_id_str) 
                        
                        if guesser_id not in user_ratings:
                             print(f"Попередження: Гравець ID {guesser_id}, що зробив припущення, не знайдений.")
                             continue

                        if guessed_owner_id == actual_owner_id:
                            user_ratings[guesser_id] = user_ratings.get(guesser_id, 0) + 1
                            points_awarded_info[guesser_id] = points_awarded_info.get(guesser_id, 0) + 1
                            print(f"Гравець ID {guesser_id} вгадав карту ID {card['id']} (власник ID {actual_owner_id}). +1 бал.")
                            card_guessed_correctly_by_someone = True
                    except (ValueError, TypeError) as e_conv:
                        print(f"Помилка конвертації ID '{guesser_id_str}' або '{guessed_owner_id_str}' у припущеннях для карти ID {card['id']}: {e_conv}")
                
                if card_guessed_correctly_by_someone and actual_owner_id in user_ratings:
                    user_ratings[actual_owner_id] = user_ratings.get(actual_owner_id, 0) + 1
                    points_awarded_info[actual_owner_id] = points_awarded_info.get(actual_owner_id, 0) + 1
                    print(f"Власнику ID {actual_owner_id} карти ID {card['id']} +1 бал, оскільки його карту вгадали.")

            for user_id_update, new_rating_update in user_ratings.items():
                c.execute("UPDATE users SET rating = ? WHERE id = ?", (new_rating_update, user_id_update))
            
            if points_awarded_info:
                awarded_summary_parts = []
                for uid, pts in points_awarded_info.items():
                    user_display_name = get_user_name(uid) or f'ID {uid}'
                    awarded_summary_parts.append(f"{user_display_name}: +{pts}")
                flash(f"Очки нараховані: {', '.join(awarded_summary_parts)}. Карти відкрито.", "success")
            else:
                flash("Ніхто не вгадав правильно, очки не нараховані. Карти відкрито.", "info")
            print("Рейтинги оновлені в БД.")

        # Визначення наступного ведучого (логіка залишається)
        current_leader_id_val = get_leading_user_id()
        c.execute("SELECT id FROM users ORDER BY id")
        all_user_ids_rows = c.fetchall()
        all_user_ids = [row['id'] for row in all_user_ids_rows]
        
        new_leader_id_for_redirect = current_leader_id_val

        if not all_user_ids:
            print("Немає користувачів для визначення наступного ведучого.")
            set_leading_user_id(None)
            new_leader_id_for_redirect = None
        elif current_leader_id_val is None or current_leader_id_val not in all_user_ids:
            new_leader_id_for_redirect = all_user_ids[0]
            print(f"Поточного ведучого не було. Новий ведучий: ID {new_leader_id_for_redirect}")
        else:
            try:
                current_leader_index = all_user_ids.index(current_leader_id_val)
                new_leader_index = (current_leader_index + 1) % len(all_user_ids)
                new_leader_id_for_redirect = all_user_ids[new_leader_index]
                print(f"Поточний ведучий: ID {current_leader_id_val}. Наступний ведучий: ID {new_leader_id_for_redirect}")
            except ValueError: # Якщо current_leader_id_val не знайдений у списку all_user_ids
                if all_user_ids: # Перевіряємо, чи список не порожній
                    new_leader_id_for_redirect = all_user_ids[0]
                    print(f"Поточний ведучий ID {current_leader_id_val} не знайдений. Новий ведучий: ID {new_leader_id_for_redirect}")
                else: # Якщо список all_user_ids порожній
                    set_leading_user_id(None)
                    new_leader_id_for_redirect = None
                    print("Немає користувачів для призначення ведучого.")

        if set_leading_user_id(new_leader_id_for_redirect):
            if new_leader_id_for_redirect is not None:
                 flash(f"Наступний ведучий: {get_user_name(new_leader_id_for_redirect) or ('ID '+str(new_leader_id_for_redirect))}.", "info")
            else:
                 flash("Ведучий не призначений (немає гравців).", "warning")
        else:
            flash("Помилка встановлення нового ведучого.", "danger")
        
        # --- БЛОК ОЧИЩЕННЯ СТОЛУ ВИДАЛЕНО ---
        # Карти залишаються на столі зі статусом "На столе:%" та своїми припущеннями.
        # show_card_info тепер true, тому інформація буде видима.
        # Наступний хід/раунд має обробляти те, що відбувається з цими картами
        # (наприклад, вони можуть бути прибрані, коли новий ведучий викладає свою карту).

        db_conn.commit()
        print("Дія 'Відкриття карт' успішно завершена. Стіл НЕ очищено автоматично.")

    except sqlite3.Error as e:
        db_conn.rollback()
        flash(f"Помилка бази даних при відкритті карт: {e}", "danger")
        print(f"Database error during open_cards: {e}")
        # У разі помилки, намагаємося зберегти поточного ведучого для редиректу
        new_leader_id_for_redirect = get_leading_user_id() 
    except Exception as e:
        db_conn.rollback()
        flash(f"Непередбачена помилка при відкритті карт: {e}", "danger")
        print(f"Unexpected error during open_cards: {e}")
        new_leader_id_for_redirect = get_leading_user_id()

    return redirect(url_for('admin', displayed_leader_id=new_leader_id_for_redirect))
# --- Кінець маршруту open_cards ---



# --- ВІДНОВЛЕНО/ДОДАНО: Маршрути для дій користувача (place_card, guess_image) ---
@app.route('/place_card/<code>/<int:image_id>', methods=['POST'])
def place_card(code, image_id):
    # g.user_id встановлюється в before_request
    if not g.user_id:
        flash("Доступ заборонено. Необхідно увійти за кодом.", "danger")
        return redirect(url_for('index'))
    
    db_conn = get_db()
    c = db_conn.cursor()
    
    if is_game_over():
        flash("Гра закінчена, викладати карти не можна.", "warning")
        return redirect(url_for('user', code=code))

    # Перевірка, чи користувач вже виклав карту
    c.execute("SELECT 1 FROM images WHERE owner_id = ? AND status LIKE 'На столе:%'", (g.user_id,))
    if c.fetchone():
        flash("Ви вже виклали карту в цьому раунді.", "warning")
        return redirect(url_for('user', code=code))

    # Перевірка, чи це карта користувача і чи вона ще не на столі
    c.execute("SELECT status FROM images WHERE id = ? AND status = ?", (image_id, f"Занято:{g.user_id}"))
    card_to_place = c.fetchone()

    if not card_to_place:
        flash("Це не ваша карта або вона вже була використана/викладена.", "warning")
        return redirect(url_for('user', code=code))

    try:
        # Оновлюємо статус карти та встановлюємо її як власність гравця на столі
        # Статус "На столе:ID_користувача" показує, що це карта, викладена гравцем.
        c.execute("UPDATE images SET status = ?, owner_id = ? WHERE id = ?", 
                  (f"На столе:{g.user_id}", g.user_id, image_id))
        db_conn.commit()
        flash("Вашу карту викладено на стіл!", "success")
    except sqlite3.Error as e:
        db_conn.rollback()
        flash(f"Помилка бази даних при викладанні карти: {e}", "danger")
        print(f"DB error in place_card: {e}")
    except Exception as e_gen:
        db_conn.rollback()
        flash(f"Непередбачена помилка при викладанні карти: {e_gen}", "danger")
        print(f"Unexpected error in place_card: {e_gen}")


    return redirect(url_for('user', code=code))


@app.route('/guess_image/<code>/<int:image_id>', methods=['POST'])
def guess_image(code, image_id):
    if not g.user_id:
        flash("Доступ заборонено. Необхідно увійти за кодом.", "danger")
        return redirect(url_for('index'))

    guessed_user_id_str = request.form.get('guessed_user_id')
    if not guessed_user_id_str: # Пустий вибір
        flash("Ви не обрали користувача для припущення.", "warning")
        return redirect(url_for('user', code=code))

    try:
        guessed_user_id = int(guessed_user_id_str)
    except ValueError:
        flash("Невірний ID обраного користувача.", "danger")
        return redirect(url_for('user', code=code))

    db_conn = get_db()
    c = db_conn.cursor()

    if is_game_over():
        flash("Гра закінчена, робити припущення не можна.", "warning")
        return redirect(url_for('user', code=code))
    
    if get_setting('show_card_info') == 'true':
        flash("Карти вже відкрито, робити припущення запізно.", "warning")
        return redirect(url_for('user', code=code))

    c.execute("SELECT owner_id, guesses FROM images WHERE id = ? AND status LIKE 'На столе:%'", (image_id,))
    image_on_table = c.fetchone()

    if not image_on_table:
        flash("Карта не знайдена на столі або це не карта для припущень.", "danger")
        return redirect(url_for('user', code=code))
    
    # Заборона гадати власника своєї ж викладеної карти (якщо б це було можливо)
    # У поточній логіці шаблону це неможливо, бо форма для гадання не для своїх карт.
    # Але для безпеки можна залишити:
    # if image_on_table['owner_id'] == g.user_id:
    #     flash("Ви не можете робити припущення щодо своєї карти.", "warning")
    #     return redirect(url_for('user', code=code))
    
    # Заборона робити припущення щодо карти, яку сам гравець виклав (якщо це не ведучий)
    # Ця перевірка актуальна, якщо будь-хто може класти карти на стіл.
    # Якщо ж на стіл кладе тільки ведучий, то ця перевірка не потрібна,
    # бо g.user_id не буде власником table_image.owner_id для інших гравців.
    # if image_on_table['owner_id'] == g.user_id:
    #     flash("Вы не можете угадывать свою собственную карту, выложенную на стол.", "warning")
    #     return redirect(url_for('user', code=code))

    try:
        current_guesses = json.loads(image_on_table['guesses'] or '{}')
        current_guesses[str(g.user_id)] = guessed_user_id # Зберігаємо ID того, КОГО вгадали
        
        c.execute("UPDATE images SET guesses = ? WHERE id = ?", (json.dumps(current_guesses), image_id))
        db_conn.commit()
        guessed_user_name = get_user_name(guessed_user_id) or f"ID {guessed_user_id}"
        flash(f"Ваше припущення (що карта належить '{guessed_user_name}') прийнято!", "success")
        
    except sqlite3.Error as e:
        db_conn.rollback()
        flash(f"Помилка бази даних при збереженні припущення: {e}", "danger")
        print(f"DB error in guess_image: {e}")
    except json.JSONDecodeError:
        # Малоймовірно, якщо ми завжди зберігаємо валідний JSON, але для безпеки
        flash("Помилка обробки даних збережених припущень.", "danger")
    except Exception as e_gen:
        db_conn.rollback()
        flash(f"Непередбачена помилка при збереженні припущення: {e_gen}", "danger")
        print(f"Unexpected error in guess_image: {e_gen}")

    return redirect(url_for('user', code=code))
# --- Кінець маршрутів для дій користувача ---


# --- Обработчики запросов (залишаються ваші index, login, logout, admin, start_new_game) ---
@app.before_request
def before_request():
    # ... (ваш код before_request залишається без змін) ...
    db_conn = get_db()
    c = db_conn.cursor() 
    code = None
    if request.view_args and 'code' in request.view_args:
        code = request.view_args.get('code')
    elif request.args and 'code' in request.args:
        code = request.args.get('code')

    g.user_id = None 
    if code:
        try:
            c.execute("SELECT id FROM users WHERE code = ?", (code,))
            user_row = c.fetchone()
            if user_row:
                g.user_id = user_row['id']
        except sqlite3.Error as e:
            print(f"Database error in before_request checking code '{code}': {e}")
            g.user_id = None 

    show_card_info_setting = get_setting("show_card_info")
    g.show_card_info = show_card_info_setting == "true"
    g.game_over = is_game_over()

@app.route('/login', methods=['GET', 'POST'])
def login():
    # ... (ваш код login залишається без змін) ...
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
    # ... (ваш код logout залишається без змін) ...
    session.pop('is_admin', None)
    flash('Вы вышли из системы.', 'info')
    return redirect(url_for('login'))
    
@app.route("/")
def index():
    # ... (ваш код index залишається без змін) ...
    return render_template("index.html")
    

@app.route("/admin", methods=["GET", "POST"])
def admin():
    # ... (ваш код admin з попередньої відповіді з інтеграцією game_board) ...
    # Переконайтеся, що тут використовується generate_game_board_data_for_display(users_data_for_template)
    # і game_board передається в render_template
    if not session.get('is_admin'):
        flash('Для доступа к этой странице требуется авторизация администратора.', 'warning')
        return redirect(url_for('login', next=request.url))
    
    db_conn = get_db() 
    c = db_conn.cursor()
    leader_to_display = None
    current_active_subfolder = '' 
    show_card_info = False

    try:
        current_actual_leader_id = get_leading_user_id() 
        current_active_subfolder = get_setting('active_subfolder') or '' 
        show_card_info = get_setting('show_card_info') == "true" 

        displayed_leader_id_from_url_str = request.args.get('displayed_leader_id')
        if displayed_leader_id_from_url_str:
            try:
                leader_to_display = int(displayed_leader_id_from_url_str)
            except (ValueError, TypeError):
                leader_to_display = current_actual_leader_id 
        else:
            leader_to_display = current_actual_leader_id
    except Exception as e:
        print(f"CRITICAL Error reading initial settings in admin: {e}")
        flash(f"Критическая ошибка чтения начальных настроек: {e}", "danger")
        return render_template("admin.html", users=[], images=[], subfolders=['koloda1', 'koloda2'],
                               active_subfolder='', guess_counts_by_user={}, all_guesses={},
                               show_card_info=False, leader_to_display=None,
                               free_image_count=0, image_owners={}, user_has_duplicate_guesses={},
                               game_board=[] ) 

    if request.method == "POST":
        action_handled = False 
        leader_for_redirect = leader_to_display
        
        # Важливо: маршрут /open_cards тепер обробляє відкриття карт.
        # Маршрут /start_new_game обробляє повне скидання гри.
        # Тут залишаються інші POST-дії адміна.
        
        try:
            if "name" in request.form: 
                name = request.form.get("name", "").strip()
                user_created_success = False 
                if not name:
                    flash("Имя пользователя не может быть пустым.", "warning")
                else:
                    num_cards = int(request.form.get("num_cards", 3))
                    if num_cards < 1: num_cards = 1
                    code = generate_unique_code()
                    c.execute("SELECT 1 FROM users WHERE name = ?", (name,))
                    if c.fetchone():
                        flash(f"Имя пользователя '{name}' уже существует.", "danger")
                    else:
                        c.execute("INSERT INTO users (name, code) VALUES (?, ?)", (name, code))
                        user_id = c.lastrowid
                        flash(f"Пользователь '{name}' добавлен.", "success")
                        user_created_success = True 
                        if current_actual_leader_id is None:
                            if set_leading_user_id(user_id):
                                flash(f"Пользователь '{name}' назначен Ведущим.", "info")
                                current_actual_leader_id = user_id
                                if leader_to_display is None:
                                    leader_to_display = current_actual_leader_id
                            else:
                                flash("Ошибка назначения ведущего.", "warning")
                        if current_active_subfolder:
                            c.execute("SELECT id FROM images WHERE subfolder = ? AND status = 'Свободно'", (current_active_subfolder,))
                            available_cards_ids = [row['id'] for row in c.fetchall()]
                            if len(available_cards_ids) < num_cards:
                                flash(f"Недостаточно свободных карт ({len(available_cards_ids)}) в '{current_active_subfolder}' для {num_cards} шт.", "warning")
                                num_cards = len(available_cards_ids)
                            if num_cards > 0:
                                selected_cards_ids = random.sample(available_cards_ids, num_cards)
                                for card_id in selected_cards_ids:
                                    c.execute("UPDATE images SET status = ? WHERE id = ?", (f"Занято:{user_id}", card_id))
                                flash(f"'{name}' назначено {num_cards} карт.", "info")
                        else:
                            flash("Активная колода не выбрана, карты не назначены.", "warning")
                if user_created_success:
                    db_conn.commit() 
                    action_handled = True
                    leader_for_redirect = current_actual_leader_id

            elif "active_subfolder" in request.form: 
                selected = request.form.get("active_subfolder")
                if set_setting('active_subfolder', selected): 
                    try:
                        updated_inactive = c.execute("UPDATE images SET status = 'Занято:Админ' WHERE subfolder != ? AND status = 'Свободно'", (selected,)).rowcount
                        db_conn.commit()
                        flash_message_text = f"Выбрана активная колода: {selected}."
                        if updated_inactive > 0:
                            flash_message_text += f" Карты в других колодах ({updated_inactive} шт.) помечены как неактивные."
                        flash(flash_message_text, "success")
                        current_active_subfolder = selected 
                    except sqlite3.Error as e:
                        db_conn.rollback()
                        flash(f"Ошибка обновления статусов карт: {e}", "danger")
                else:
                    flash("Ошибка сохранения настройки активной колоды.", "danger")
                leader_for_redirect = leader_to_display
                action_handled = True

            elif "delete_user_id" in request.form: 
                user_id_to_delete = int(request.form.get("delete_user_id"))
                was_leader = (current_actual_leader_id == user_id_to_delete)
                c.execute("SELECT name FROM users WHERE id = ?", (user_id_to_delete,))
                user_to_delete = c.fetchone()
                if user_to_delete:
                    user_name_deleted = user_to_delete['name']
                    c.execute("DELETE FROM users WHERE id = ?", (user_id_to_delete,))
                    c.execute("UPDATE images SET status = 'Свободно' WHERE status = ?", (f"Занято:{user_id_to_delete}",))
                    c.execute("UPDATE images SET status = 'Свободно', owner_id = NULL, guesses = '{}' WHERE owner_id = ?", (user_id_to_delete,))
                    flash(f"Пользователь '{user_name_deleted}' удален.", "success")
                    new_leader_id_after_delete = current_actual_leader_id 
                    if was_leader:
                        c.execute("SELECT id FROM users ORDER BY id")
                        remaining_users = c.fetchall()
                        if remaining_users:
                            new_leader_id_after_delete = remaining_users[0]['id']
                            if set_leading_user_id(new_leader_id_after_delete):
                                new_leader_name = get_user_name(new_leader_id_after_delete) or f"ID {new_leader_id_after_delete}"
                                flash(f"Удаленный пользователь был Ведущим. Новый Ведущий: {new_leader_name}.", "info")
                            else:
                                flash("Ошибка назначения нового ведущего.", "warning")
                        else:
                            new_leader_id_after_delete = None
                            set_leading_user_id(None)
                            flash("Удаленный пользователь был Ведущим. Пользователей не осталось.", "warning")
                        leader_for_redirect = new_leader_id_after_delete
                    else:
                        leader_for_redirect = current_actual_leader_id
                    db_conn.commit() 
                else:
                    flash(f"Пользователь с ID {user_id_to_delete} не найден.", "danger")
                    leader_for_redirect = leader_to_display 
                action_handled = True
            
            if action_handled: 
                return redirect(url_for('admin', displayed_leader_id=leader_for_redirect))

        except sqlite3.IntegrityError as e: 
            if "UNIQUE constraint failed" not in str(e):
                flash(f"Ошибка целостности базы данных: {e}", "danger")
            db_conn.rollback()
        except (sqlite3.Error, ValueError, TypeError) as e: 
            flash(f"Ошибка при обработке запроса: {e}", "danger")
            db_conn.rollback()
        except Exception as e: 
            print(f"!!! UNEXPECTED ERROR during admin POST: {e}") 
            flash(f"Произошла непредвиденная ошибка: {e}", "danger")
            db_conn.rollback()

    users_data_for_template, images_data_for_template, subfolders_data = [], [], []
    guess_counts_data, all_guesses_data, image_owners_data, user_has_duplicate_guesses_data = {}, {}, {}, {}
    free_image_count_data = 0
    game_board_data_list = [] 

    try:
        c.execute("SELECT id, name, code, rating FROM users ORDER BY name ASC")
        users_data_for_template = c.fetchall() 
        print(f"Admin GET: Fetched {len(users_data_for_template)} users.")
        game_board_data_list = generate_game_board_data_for_display(users_data_for_template)
        c.execute("SELECT id, subfolder, image, status, owner_id, guesses FROM images ORDER BY subfolder, id")
        images_rows = c.fetchall()
        images_data_for_template = [] 
        all_guesses_data = {} 
        image_owners_data = {}
        print(f"Admin GET: Fetched {len(images_rows)} image rows. Active subfolder: '{current_active_subfolder}'")
        for img_row in images_rows:
            guesses_json_str = img_row['guesses'] or '{}'
            try:
                guesses_dict = json.loads(guesses_json_str)
            except json.JSONDecodeError as json_e:
                print(f"Warning: JSONDecodeError for image ID {img_row['id']} - guesses: '{guesses_json_str}'. Error: {json_e}. Using empty dict.")
                guesses_dict = {}
            img_dict_copy = dict(img_row) 
            img_dict_copy['guesses'] = guesses_dict
            images_data_for_template.append(img_dict_copy)
            if img_dict_copy['owner_id'] is not None:
                image_owners_data[img_dict_copy['id']] = img_dict_copy['owner_id']
            if img_dict_copy['status'] == 'Свободно' and img_dict_copy['subfolder'] == current_active_subfolder:
                free_image_count_data += 1
            if guesses_dict: 
                all_guesses_data[img_row['id']] = guesses_dict 
        print(f"Admin GET: Processed images. Free count in active folder: {free_image_count_data}")
        user_has_duplicate_guesses_data = {user_row['id']: False for user_row in users_data_for_template}
        if all_guesses_data:
            for user_row in users_data_for_template:
                user_id_str = str(user_row['id'])
                guesses_made_by_user = []
                for image_id, guesses_for_image in all_guesses_data.items():
                    if user_id_str in guesses_for_image:
                        guesses_made_by_user.append(guesses_for_image[user_id_str])
                if len(guesses_made_by_user) > len(set(guesses_made_by_user)):
                    user_has_duplicate_guesses_data[user_row['id']] = True
        guess_counts_data = {user_row['id']: 0 for user_row in users_data_for_template}
        for img_id, guesses_for_image in all_guesses_data.items():
            for guesser_id_str in guesses_for_image:
                try:
                    guesser_id_int = int(guesser_id_str)
                    if guesser_id_int in guess_counts_data:
                        guess_counts_data[guesser_id_int] += 1
                except (ValueError, TypeError): pass
        print(f"Admin GET: Calculated guess counts and duplicates.")
        c.execute("SELECT DISTINCT subfolder FROM images ORDER BY subfolder")
        subfolders_data = [row['subfolder'] for row in c.fetchall()] or ['koloda1', 'koloda2']
        print(f"Admin GET: Found subfolders: {subfolders_data}")
    except sqlite3.Error as e:
        print(f"!!! ERROR caught in admin GET data fetch: {e}")
        flash(f"Ошибка чтения данных для отображения: {e}", "danger")
        users_data_for_template, images_data_for_template, subfolders_data = [], [], []
        guess_counts_data, all_guesses_data, image_owners_data, user_has_duplicate_guesses_data = {}, {}, {}, {}
        free_image_count_data = 0
        game_board_data_list = [] 
    except Exception as e: 
        print(f"!!! UNEXPECTED ERROR caught in admin GET data fetch: {e}")
        flash(f"Непредвиденная ошибка при чтении данных: {e}", "danger")
        users_data_for_template, images_data_for_template, subfolders_data = [], [], []
        guess_counts_data, all_guesses_data, image_owners_data, user_has_duplicate_guesses_data = {}, {}, {}, {}
        free_image_count_data = 0
        game_board_data_list = []
    print(f"Admin GET: Rendering template. Users count: {len(users_data_for_template)}")
    return render_template("admin.html", users=users_data_for_template, images=images_data_for_template,
                           subfolders=subfolders_data, active_subfolder=current_active_subfolder,
                           guess_counts_by_user=guess_counts_data, all_guesses=all_guesses_data,
                           show_card_info=show_card_info,
                           leader_to_display=leader_to_display,
                           free_image_count=free_image_count_data,
                           image_owners=image_owners_data,
                           user_has_duplicate_guesses=user_has_duplicate_guesses_data,
                           game_board=game_board_data_list)

@app.route("/start_new_game", methods=["POST"])
def start_new_game():
    # ... (ваш код start_new_game з попередньої відповіді з викликом initialize_new_game_board_visuals) ...
    if not session.get('is_admin'): 
        flash('Тільки адміністратор може розпочати нову гру.', 'danger')
        return redirect(url_for('admin')) 

    db_conn = get_db() 
    c = db_conn.cursor()
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
    new_leader_id = None 
    try:
        print("Сброс рейтингов...")
        c.execute("UPDATE users SET rating = 0")
        print("Сброс состояния карт...")
        c.execute("UPDATE images SET owner_id = NULL, guesses = '{}', status = 'Занято:Админ'")
        c.execute("UPDATE images SET status = 'Свободно' WHERE subfolder = ?", (selected_deck,))
        print("Сброс настроек игры...")
        set_game_over(False) 
        set_setting("show_card_info", "false") # Карти приховані на початку нового раунду
        set_setting("active_subfolder", selected_deck)
        c.execute("SELECT id FROM users ORDER BY id LIMIT 1")
        first_user = c.fetchone()
        if first_user:
            new_leader_id = first_user['id']
            set_leading_user_id(new_leader_id)
            print(f"Назначен новый ведущий: {get_user_name(new_leader_id)} (ID: {new_leader_id})")
        else:
            set_leading_user_id(None) 
            print("Пользователи не найдены, ведущий не назначен.")
        
        c.execute("SELECT id, name, rating FROM users") 
        all_users_for_board_init = c.fetchall()
        initialize_new_game_board_visuals(all_users_for_rating_check=all_users_for_board_init)
        
        db_conn.commit() 

        c.execute("SELECT id FROM users ORDER BY id")
        user_ids = [row['id'] for row in c.fetchall()]
        num_users = len(user_ids)
        num_total_dealt = 0
        if not user_ids:
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
            for user_id in user_ids:
                cards_dealt_to_user = 0
                for _ in range(num_cards_per_player):
                    if card_index < num_available:
                        card_id = available_cards_ids[card_index]
                        c.execute("UPDATE images SET status = ? WHERE id = ?", (f"Занято:{user_id}", card_id))
                        card_index += 1
                        cards_dealt_to_user += 1
                    else:
                        break 
                if cards_dealt_to_user > 0:
                     num_total_dealt += cards_dealt_to_user
                     print(f"Пользователю ID {user_id} роздано {cards_dealt_to_user} карт.")
            db_conn.commit() 
            flash(f"Новая игра успешно начата! Колода: '{selected_deck}'. Роздано карт: {num_total_dealt}.", "success")
    except sqlite3.Error as e:
        db_conn.rollback()
        flash(f"Ошибка базы данных при старте новой игры: {e}", "danger")
        print(f"Database error during start_new_game: {e}")
    except Exception as e:
        db_conn.rollback()
        flash(f"Непредвиденная ошибка при старте новой игры: {e}", "danger")
        print(f"Unexpected error during start_new_game: {e}")
    return redirect(url_for('admin', displayed_leader_id=new_leader_id))

@app.route('/user/<code>', methods=['GET', 'POST'])
def user(code_from_url): 
    if not g.user_id: # g.user_id встановлюється в before_request
        flash("Неверный код доступа или сессия истекла.", "danger")
        return redirect(url_for('index'))

    db_conn = get_db()
    c = db_conn.cursor()
    current_user = None
    try:
        c.execute("SELECT id, name, rating, code FROM users WHERE id = ?", (g.user_id,))
        current_user = c.fetchone()
        if not current_user: 
            flash("Пользователь не найден.", "danger")
            return redirect(url_for('index'))
    except sqlite3.Error as e:
        flash(f"Ошибка при получении данных пользователя: {e}", "danger")
        return redirect(url_for('index'))

    user_cards = []
    table_images_list = []
    all_users_list_for_forms = [] # Для випадаючих списків у формах
    user_placed_card = False
    leader_id_for_display = get_leading_user_id() 
    game_board_data = [] # Ініціалізація

    try:
        c.execute("SELECT id, subfolder, image, status FROM images WHERE status = ?", (f"Занято:{g.user_id}",))
        user_cards = c.fetchall()

        c.execute("SELECT id, subfolder, image, owner_id, guesses FROM images WHERE status LIKE 'На столе:%' ORDER BY id")
        raw_table_images = c.fetchall()
        for img_row in raw_table_images:
            img_dict = dict(img_row)
            try:
                img_dict['guesses'] = json.loads(img_row['guesses'] or '{}')
            except json.JSONDecodeError:
                img_dict['guesses'] = {}
            table_images_list.append(img_dict)
        
        for img_on_table in table_images_list:
            if img_on_table['owner_id'] == g.user_id:
                user_placed_card = True # Прапор, що гравець вже виклав карту на стіл
                break
        
        c.execute("SELECT id, name FROM users ORDER BY name ASC") 
        all_users_list_for_forms = c.fetchall()

        c.execute("SELECT id, name, rating FROM users") # Потрібні всі користувачі з рейтингами для поля
        all_users_for_board_display = c.fetchall()
        game_board_data = generate_game_board_data_for_display(all_users_for_board_display)

    except sqlite3.Error as e:
        flash(f"Ошибка при загрузке данных для страницы пользователя: {e}", "danger")
        game_board_data = [] 

    return render_template(
        "user.html",
        name=current_user['name'],
        rating=current_user['rating'],
        code=current_user['code'], 
        cards=user_cards,
        table_images=table_images_list,
        on_table=user_placed_card, 
        all_users=all_users_list_for_forms, 
        leader_for_display=leader_id_for_display,
        game_board=game_board_data,
        # g, get_user_name, get_leading_user_id доступні глобально в Jinja
    )

# --- ВІДНОВЛЕНО/ДОДАНО: Маршрути для дій користувача (place_card, guess_image) ---
# Ці функції залишаються такими ж, як у повідомленні #13 (тобто моїй попередній відповіді)
@app.route('/place_card/<code>/<int:image_id>', methods=['POST'])
def place_card(code, image_id):
    # ... (код place_card з повідомлення #13) ...
    if not g.user_id:
        flash("Доступ заборонено. Необхідно увійти за кодом.", "danger")
        return redirect(url_for('index'))
    
    db_conn = get_db()
    c = db_conn.cursor()
    
    if is_game_over():
        flash("Гра закінчена, викладати карти не можна.", "warning")
        return redirect(url_for('user', code=code))

    c.execute("SELECT 1 FROM images WHERE owner_id = ? AND status LIKE 'На столе:%'", (g.user_id,))
    if c.fetchone():
        flash("Ви вже виклали карту в цьому раунді.", "warning")
        return redirect(url_for('user', code=code))

    c.execute("SELECT status FROM images WHERE id = ? AND status = ?", (image_id, f"Занято:{g.user_id}"))
    card_to_place = c.fetchone()

    if not card_to_place:
        flash("Це не ваша карта або вона вже була використана/викладена.", "warning")
        return redirect(url_for('user', code=code))

    try:
        c.execute("UPDATE images SET status = ?, owner_id = ? WHERE id = ?", 
                  (f"На столе:{g.user_id}", g.user_id, image_id))
        db_conn.commit()
        flash("Вашу карту викладено на стіл!", "success")
    except sqlite3.Error as e:
        db_conn.rollback()
        flash(f"Помилка бази даних при викладанні карти: {e}", "danger")
        print(f"DB error in place_card: {e}")
    except Exception as e_gen:
        db_conn.rollback()
        flash(f"Непередбачена помилка при викладанні карти: {e_gen}", "danger")
        print(f"Unexpected error in place_card: {e_gen}")
    return redirect(url_for('user', code=code))


@app.route('/guess_image/<code>/<int:image_id>', methods=['POST'])
def guess_image(code, image_id):
    # ... (код guess_image з повідомлення #13) ...
    if not g.user_id:
        flash("Доступ заборонено. Необхідно увійти за кодом.", "danger")
        return redirect(url_for('index'))

    guessed_user_id_str = request.form.get('guessed_user_id')
    if not guessed_user_id_str: 
        flash("Ви не обрали користувача для припущення.", "warning")
        return redirect(url_for('user', code=code))

    try:
        guessed_user_id = int(guessed_user_id_str)
    except ValueError:
        flash("Невірний ID обраного користувача.", "danger")
        return redirect(url_for('user', code=code))

    db_conn = get_db()
    c = db_conn.cursor()

    if is_game_over():
        flash("Гра закінчена, робити припущення не можна.", "warning")
        return redirect(url_for('user', code=code))
    
    if get_setting('show_card_info') == 'true':
        flash("Карти вже відкрито, робити припущення запізно.", "warning")
        return redirect(url_for('user', code=code))

    c.execute("SELECT owner_id, guesses FROM images WHERE id = ? AND status LIKE 'На столе:%'", (image_id,))
    image_on_table = c.fetchone()

    if not image_on_table:
        flash("Карта не знайдена на столі або це не карта для припущень.", "danger")
        return redirect(url_for('user', code=code))
    
    try:
        current_guesses = json.loads(image_on_table['guesses'] or '{}')
        current_guesses[str(g.user_id)] = guessed_user_id 
        
        c.execute("UPDATE images SET guesses = ? WHERE id = ?", (json.dumps(current_guesses), image_id))
        db_conn.commit()
        guessed_user_name = get_user_name(guessed_user_id) or f"ID {guessed_user_id}"
        flash(f"Ваше припущення (що карта належить '{guessed_user_name}') прийнято!", "success")
        
    except sqlite3.Error as e:
        db_conn.rollback()
        flash(f"Помилка бази даних при збереженні припущення: {e}", "danger")
        print(f"DB error in guess_image: {e}")
    except json.JSONDecodeError:
        flash("Помилка обробки даних збережених припущень.", "danger")
    except Exception as e_gen:
        db_conn.rollback()
        flash(f"Непередбачена помилка при збереженні припущення: {e_gen}", "danger")
        print(f"Unexpected error in guess_image: {e_gen}")
    return redirect(url_for('user', code=code))
    
if __name__ == "__main__":
    # init_db() # Розкоментуйте для першої ініціалізації
    if not _current_game_board_pole_image_config: # Ініціалізація поля при старті, якщо воно ще не було
         print("Первичная инициализация визуализации игрового поля при запуске приложения...")
         initialize_new_game_board_visuals(num_cells_for_board=DEFAULT_NUM_BOARD_CELLS)
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
