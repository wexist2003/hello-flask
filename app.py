import eventlet # Импортируем eventlet
eventlet.monkey_patch() # Вызываем monkey_patch() СРАЗУ ЖЕ

import json
import sys
import sqlite3
import os
import string
import random
import time # Добавим импорт time для round_start_time
import traceback
from flask import Flask, render_template, request, redirect, url_for, g, flash, session
from flask_socketio import SocketIO, emit
import redis # Добавим импорт redis

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your_very_secret_fallback_key_for_dev_only_12345')
if app.config['SECRET_KEY'] == 'your_very_secret_fallback_key_for_dev_only_12345':
    print("ПРЕДУПРЕЖДЕНИЕ: Используется SECRET_KEY по умолчанию. Установите переменную окружения SECRET_KEY!", file=sys.stderr)

# --- Настройка Redis для SocketIO и состояния игры ---
REDIS_URL = os.environ.get('REDIS_URL')
if not REDIS_URL:
    print("ПРЕДУПРЕЖДЕНИЕ: REDIS_URL не найден. SocketIO будет работать только с одним воркером и состояние игры не будет общим.", file=sys.stderr)
    socketio = SocketIO(app)
    redis_game_state_client = None
else:
    socketio = SocketIO(app, message_queue=REDIS_URL)
    redis_game_state_client = redis.Redis.from_url(REDIS_URL)
    print(f"Redis: Используется Redis Message Queue и клиент для состояния игры: {REDIS_URL}", file=sys.stderr)

# --- Настройка SQLite (ВНИМАНИЕ: Не рекомендуется для мультипроцессного использования!) ---
DB_PATH = 'database.db'

# --- Глобальные переменные (для SocketIO connections) ---
connected_users_socketio = {}  # {sid: user_code} - Это остается на стороне каждого воркера.

# --- Класс для управления состоянием игры (через Redis) ---
class GameStateManager:
    # Ключ для хранения всего состояния игры в Redis
    GAME_STATE_KEY = "game_state"
    # Начальное состояние игры (для инициализации)
    DEFAULT_GAME_STATE = {
        'game_board_config': [],
        'num_cells': 0,
        'current_cards_on_table': [], # [{"card_id": 1, "card_text": "text", "image": "path.jpg", "chosen_by": null, "guessed_by": []}]
        'user_points': {}, # {user_code: points}
        'user_guessed_cards': {}, # {user_code: [card_id1, card_id2]}
        'game_round_state': 'waiting_for_players', # waiting_for_players, choosing_cards, voting_cards, score_display
        'deck_votes': [], # [{"subfolder": "deck_name", "votes": 0}]
        'round_start_time': None,
        'current_round_leader_code': None, # user_code лидера текущего раунда
        'current_round_card_chosen': None, # card_id выбранной лидером карты
        'current_round_card_description': '', # Описание от лидера
        'users_in_game': [], # Список user_code активных игроков
        'voted_for_card': {}, # {user_code: card_id} - кто за какую карту проголосовал
        'guesser_voted_for_card': {}, # {user_code: card_id} - для угадывающего
        'current_guesser_user_code': None # Код пользователя, который угадывает
    }

    def __init__(self, redis_client):
        self.redis_client = redis_client
        # Инициализируем состояние в Redis, если оно пусто
        if self.redis_client and not self.redis_client.exists(self.GAME_STATE_KEY):
            self.set_state(self.DEFAULT_GAME_STATE)

    def get_state(self):
        if self.redis_client:
            state_json = self.redis_client.get(self.GAME_STATE_KEY)
            if state_json:
                return json.loads(state_json)
        # Если Redis недоступен или пуст, возвращаем дефолтное состояние
        # В Production это должно быть ошибкой, если Redis_URL есть
        print("WARNING: Could not retrieve game state from Redis. Returning default.", file=sys.stderr)
        return self.DEFAULT_GAME_STATE.copy()

    def set_state(self, state_dict):
        if self.redis_client:
            self.redis_client.set(self.GAME_STATE_KEY, json.dumps(state_dict))
        else:
            print("ERROR: Redis client not initialized. Cannot set game state.", file=sys.stderr)

    def update_state(self, **kwargs):
        """Обновляет части состояния игры атомарно (или почти атомарно)."""
        if not self.redis_client:
            print("ERROR: Redis client not initialized. Cannot update game state.", file=sys.stderr)
            return

        # Для простых обновлений можно использовать GET/SET.
        # Для сложных или конкурентных обновлений рассмотрите Lua-скрипты Redis или транзакции WATCH/MULTI/EXEC.
        # В данном случае, так как основной инициатор изменений - сервер, это будет работать.
        current_state = self.get_state()
        for key, value in kwargs.items():
            current_state[key] = value
        self.set_state(current_state)

# Инициализация менеджера состояния игры
game_state_manager = GameStateManager(redis_game_state_client)

# --- Вспомогательные функции (могут остаться, но get_db() требует осторожности) ---
def get_db():
    if 'db' not in g:
        # ВНИМАНИЕ: SQLite не подходит для мультипроцессного использования.
        # Настоятельно рекомендуется перейти на PostgreSQL.
        g.db = sqlite3.connect(DB_PATH, timeout=10)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def hash_password(password): # Логика без изменений
    import hashlib
    return hashlib.sha256(password.encode()).hexdigest()

def generate_random_user_code(length=6): # Логика без изменений
    characters = string.ascii_letters + string.digits
    return ''.join(random.choice(characters) for i in range(length))

def generate_user_friendly_code(length=4): # Логика без изменений
    return ''.join(random.choice(string.digits) for i in range(length))

def get_user_by_code(user_code): # Логика без изменений
    db = get_db()
    user_data = db.execute('SELECT id, name, rating, user_code, role, status FROM users WHERE user_code = ?', (user_code,)).fetchone()
    return user_data

def get_user_by_name(name): # Логика без изменений
    db = get_db()
    user_data = db.execute('SELECT id, name, rating, user_code, role, status FROM users WHERE name = ?', (name,)).fetchone()
    return user_data

def get_all_active_users(): # Логика без изменений
    db = get_db()
    users = db.execute('SELECT id, name, rating, user_code, role, status FROM users WHERE status = \'active\'').fetchall()
    return users

def get_user_ratings(): # Логика без изменений
    db = get_db()
    users = db.execute('SELECT name, rating FROM users ORDER BY rating DESC').fetchall()
    return users

def update_user_rating(user_code, new_rating): # Логика без изменений
    db = get_db()
    db.execute('UPDATE users SET rating = ? WHERE user_code = ?', (new_rating, user_code))
    db.commit()

def create_db_schema(): # Логика без изменений
    if not os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                password TEXT NOT NULL,
                user_code TEXT NOT NULL UNIQUE,
                rating INTEGER DEFAULT 1000,
                role TEXT DEFAULT 'player',
                status TEXT DEFAULT 'active'
            )
        ''')
        # Создаем администратора по умолчанию, если БД новая
        default_admin_password = os.environ.get('ADMIN_PASSWORD', 'adminpass')
        admin_code = generate_random_user_code()
        cursor.execute("INSERT INTO users (name, password, user_code, role) VALUES (?, ?, ?, ?)",
                       ('admin', hash_password(default_admin_password), admin_code, 'admin'))
        conn.commit()
        conn.close()
        print(f"Database created and default admin user added with code: {admin_code}", file=sys.stderr)
    else:
        # Проверяем наличие колонки status, если ее нет - добавляем
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT status FROM users LIMIT 1")
        except sqlite3.OperationalError:
            cursor.execute("ALTER TABLE users ADD COLUMN status TEXT DEFAULT 'active'")
            conn.commit()
            print("Added 'status' column to users table.", file=sys.stderr)
        conn.close()

# --- Логика инициализации игрового поля (теперь берется из Redis) ---
GAME_BOARD_POLE_IMG_SUBFOLDER = "pole"
GAME_BOARD_POLE_IMAGES = [f"p{i}.jpg" for i in range(1, 8)]
DEFAULT_NUM_BOARD_CELLS = 40
GAME_BOARD_TOTAL_IMAGES = 100 # Количество картинок на игровом поле

# Инициализация состояния игры (обновленная)
def initialize_game_state(all_users_for_rating_check):
    # Логика генерации игрового поля (как у вас)
    pole_image_config = []
    # Вычисляем количество нужных изображений, чтобы было достаточно для 40 ячеек
    # и чтобы каждая картинка была представлена хотя бы один раз.
    num_unique_images = len(GAME_BOARD_POLE_IMAGES)
    if num_unique_images == 0:
        print("Error: No pole images defined.", file=sys.stderr)
        return

    # Гарантируем, что каждая уникальная картинка будет хотя бы один раз
    pole_image_config.extend(GAME_BOARD_POLE_IMAGES)

    # Добавляем остальные картинки случайным образом, чтобы довести до GAME_BOARD_TOTAL_IMAGES
    remaining_images_to_add = GAME_BOARD_TOTAL_IMAGES - num_unique_images
    if remaining_images_to_add > 0:
        for _ in range(remaining_images_to_add):
            pole_image_config.append(random.choice(GAME_BOARD_POLE_IMAGES))

    random.shuffle(pole_image_config) # Перемешиваем

    # --- Инициализация состояния игры в Redis ---
    current_game_state = game_state_manager.get_state() # Получаем текущее, чтобы не перезатереть полностью
    current_game_state['game_board_config'] = pole_image_config
    current_game_state['num_cells'] = DEFAULT_NUM_BOARD_CELLS
    current_game_state['game_round_state'] = 'waiting_for_players'
    current_game_state['current_cards_on_table'] = []
    current_game_state['user_points'] = {u['user_code']: u['rating'] for u in all_users_for_rating_check if u['status'] == 'active'}
    current_game_state['user_guessed_cards'] = {u['user_code']: [] for u in all_users_for_rating_check if u['status'] == 'active'}
    current_game_state['deck_votes'] = [] # Перенесем логику получения колод сюда или в отдельную функцию
    current_game_state['round_start_time'] = None
    current_game_state['current_round_leader_code'] = None
    current_game_state['current_round_card_chosen'] = None
    current_game_state['current_round_card_description'] = ''
    current_game_state['users_in_game'] = [u['user_code'] for u in all_users_for_rating_check if u['status'] == 'active']
    current_game_state['voted_for_card'] = {}
    current_game_state['guesser_voted_for_card'] = {}
    current_game_state['current_guesser_user_code'] = None

    game_state_manager.set_state(current_game_state)
    print("Игровое состояние инициализировано/обновлено в Redis.", file=sys.stderr)

# --- Роуты Flask (минимум изменений, так как они вызывают GameStateManager) ---
@app.route('/')
def index():
    user_code = session.get('user_code')
    user = get_user_by_code(user_code) if user_code else None

    # Получаем deck_votes из общего состояния игры
    game_state = game_state_manager.get_state()
    deck_votes = game_state.get('deck_votes', [])
    voted_for_deck = session.get('voted_for_deck') # Сохраняем в сессии, т.к. это предпочтение конкретного пользователя

    return render_template('index.html', user=user, deck_votes=deck_votes, voted_for_deck=voted_for_deck)

@app.route('/vote_deck', methods=['POST'])
def vote_deck():
    subfolder = request.form.get('subfolder')
    if not subfolder:
        flash("Не выбрана колода для голосования.", 'danger')
        return redirect(url_for('index'))

    user_code = session.get('user_code')
    if not user_code:
        flash("Вы не авторизованы.", 'danger')
        return redirect(url_for('login_player'))

    # Получаем текущее состояние голосования
    game_state = game_state_manager.get_state()
    deck_votes = game_state.get('deck_votes', [])
    current_vote_by_user = session.get('voted_for_deck_user_code') # Храним в сессии, чтобы привязка была к пользователю

    # Логика голосования:
    # 1. Если пользователь уже голосовал, отменяем предыдущий голос
    if current_vote_by_user:
        for deck in deck_votes:
            if deck['subfolder'] == current_vote_by_user:
                deck['votes'] = max(0, deck['votes'] - 1)
                break
    
    # 2. Добавляем новый голос
    found = False
    for deck in deck_votes:
        if deck['subfolder'] == subfolder:
            deck['votes'] += 1
            found = True
            break
    if not found:
        deck_votes.append({'subfolder': subfolder, 'votes': 1})

    # Сортируем колоды по количеству голосов
    deck_votes.sort(key=lambda x: x['votes'], reverse=True)

    # Сохраняем обновленные голоса в Redis
    game_state_manager.update_state(deck_votes=deck_votes)

    # Обновляем сессию пользователя
    session['voted_for_deck'] = subfolder
    session['voted_for_deck_user_code'] = subfolder # Используем это для отслеживания голоса пользователя

    flash(f"Вы проголосовали за колоду '{subfolder}'.", 'success')
    # Отправляем обновление всем клиентам через SocketIO
    # Теперь это будет работать через все воркеры
    socketio.emit('deck_votes_update', deck_votes, broadcast=True)

    return redirect(url_for('index'))

@app.route('/login_player', methods=['GET', 'POST'])
def login_player():
    if request.method == 'POST':
        user_code = request.form['user_code'].strip()
        user = get_user_by_code(user_code)
        if user:
            session['user_code'] = user_code
            session['user_role'] = user['role']
            session['user_name'] = user['name'] # Добавлено
            flash(f"Добро пожаловать, {user['name']}!", 'success')
            return redirect(url_for('game'))
        else:
            flash("Неверный код игрока.", 'danger')
    return render_template('login_player.html')

@app.route('/logout')
def logout():
    session.pop('user_code', None)
    session.pop('user_role', None)
    session.pop('user_name', None) # Добавлено
    session.pop('voted_for_deck', None)
    session.pop('voted_for_deck_user_code', None) # Удаляем и это
    flash("Вы вышли из системы.", 'info')
    return redirect(url_for('index'))

@app.route('/register_player', methods=['GET', 'POST'])
def register_player():
    if request.method == 'POST':
        name = request.form['name'].strip()
        password = request.form['password'].strip()
        if not name or not password:
            flash("Имя пользователя и пароль не могут быть пустыми.", 'danger')
            return redirect(url_for('register_player'))

        if get_user_by_name(name):
            flash("Пользователь с таким именем уже существует.", 'danger')
            return redirect(url_for('register_player'))

        try:
            db = get_db()
            user_code = generate_user_friendly_code() # Используем user_friendly_code для входа
            hashed_password = hash_password(password)
            db.execute("INSERT INTO users (name, password, user_code, role) VALUES (?, ?, ?, ?)",
                       (name, hashed_password, user_code, 'player'))
            db.commit()
            flash(f"Пользователь {name} зарегистрирован. Ваш код для входа: {user_code}", 'success')
            return redirect(url_for('login_player'))
        except Exception as e:
            flash(f"Ошибка при регистрации: {e}", 'danger')
            app.logger.error(f"Registration error: {e}", exc_info=True)
    return render_template('register_player.html')

@app.route('/game')
def game():
    user_code = session.get('user_code')
    user = get_user_by_code(user_code)

    if not user:
        flash("Пожалуйста, войдите, чтобы играть.", 'danger')
        return redirect(url_for('login_player'))

    # Передаем только нужную информацию в шаблон, состояние игры берется через SocketIO
    game_state = game_state_manager.get_state()
    deck_name = session.get('voted_for_deck') # Получаем выбранную колоду из сессии

    # Пример, как получить изображения для отображения (теперь из GameState)
    pole_images_config = game_state.get('game_board_config', [])
    num_cells = game_state.get('num_cells', DEFAULT_NUM_BOARD_CELLS)

    # Проверка, является ли пользователь лидером раунда
    is_leader = game_state.get('current_round_leader_code') == user_code

    # Проверка, является ли пользователь угадывающим
    is_guesser = game_state.get('current_guesser_user_code') == user_code

    return render_template('game.html', user=user,
                           pole_images_config=pole_images_config,
                           num_cells=num_cells,
                           deck_name=deck_name,
                           is_leader=is_leader,
                           is_guesser=is_guesser)

@app.route('/admin')
def admin():
    if session.get('user_role') != 'admin':
        flash("Доступ запрещен.", 'danger')
        return redirect(url_for('index'))
    users = get_all_active_users()
    return render_template('admin.html', users=users)

@app.route('/admin/toggle_user_status/<user_code>')
def toggle_user_status(user_code):
    if session.get('user_role') != 'admin':
        flash("Доступ запрещен.", 'danger')
        return redirect(url_for('index'))
    try:
        db = get_db()
        user = db.execute("SELECT status FROM users WHERE user_code = ?", (user_code,)).fetchone()
        if user:
            new_status = 'inactive' if user['status'] == 'active' else 'active'
            db.execute("UPDATE users SET status = ? WHERE user_code = ?", (new_status, user_code))
            db.commit()
            flash(f"Статус пользователя {user_code} изменен на {new_status}.", 'success')
            # Обновить список активных пользователей в игре, если они есть
            all_active_users = get_all_active_users()
            initialize_game_state(all_active_users) # Переинициализируем, чтобы обновить users_in_game
            socketio.emit('game_update', game_state_manager.get_state(), broadcast=True) # Обновить всем
        else:
            flash("Пользователь не найден.", 'danger')
    except Exception as e:
        flash(f"Ошибка при изменении статуса: {e}", 'danger')
    return redirect(url_for('admin'))

@app.route('/admin/reset_password/<user_code>', methods=['POST'])
def admin_reset_password(user_code):
    if session.get('user_role') != 'admin':
        flash("Доступ запрещен.", 'danger')
        return redirect(url_for('index'))
    new_password = request.form.get('new_password')
    if not new_password:
        flash("Новый пароль не может быть пустым.", 'danger')
        return redirect(url_for('admin'))

    try:
        db = get_db()
        db.execute("UPDATE users SET password = ? WHERE user_code = ?", (hash_password(new_password), user_code))
        db.commit()
        flash(f"Пароль пользователя {user_code} успешно сброшен.", 'success')
    except Exception as e:
        flash(f"Ошибка при сбросе пароля: {e}", 'danger')
    return redirect(url_for('admin'))

@app.route('/admin/add_card', methods=['POST'])
def admin_add_card():
    if session.get('user_role') != 'admin':
        flash("Доступ запрещен.", 'danger')
        return redirect(url_for('game'))

    card_text = request.form.get('card_text')
    card_image_url = request.form.get('card_image_url')

    if not card_text:
        flash("Текст карты не может быть пустым.", 'danger')
        return redirect(url_for('game'))

    current_game_state = game_state_manager.get_state()
    cards_on_table = current_game_state.get('current_cards_on_table', [])

    # Генерируем уникальный ID для карты
    card_id = len(cards_on_table) + 1 # Простой ID, можно улучшить до UUID

    new_card = {
        "card_id": card_id,
        "card_text": card_text,
        "image": card_image_url if card_image_url else None,
        "chosen_by": None,
        "guessed_by": []
    }
    cards_on_table.append(new_card)
    game_state_manager.update_state(current_cards_on_table=cards_on_table)

    flash("Карта добавлена на стол.", 'success')
    # Отправляем обновление всем клиентам
    socketio.emit('game_update', game_state_manager.get_state(), broadcast=True)
    return redirect(url_for('game'))

# --- SocketIO Events (обновлены для работы с GameStateManager) ---
@socketio.on('connect')
def handle_connect():
    sid = request.sid
    user_code = session.get('user_code')
    if user_code:
        connected_users_socketio[sid] = user_code
        print(f"SocketIO: Client connected: SID={sid}, User code: {user_code}", file=sys.stderr)
        try:
            # Отправляем начальное состояние игры пользователю
            initial_state = game_state_manager.get_state()
            emit('game_update', initial_state, room=sid)
        except Exception as e:
            print(f"SocketIO: Error sending initial state to {sid}: {e}\n{traceback.format_exc()}", file=sys.stderr)
    else:
        print(f"SocketIO: Anonymous client connected: SID={sid}", file=sys.stderr)
        # Если аноним, можно не отправлять игровое состояние или отправить ограниченное
        emit('game_update', {"game_round_state": "waiting_for_players"}, room=sid) # Ограниченное для анонима

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    user_code = connected_users_socketio.pop(sid, None)
    print(f"SocketIO: Client disconnected: SID={sid}, User code: {user_code or 'N/A'}", file=sys.stderr)

    # Обновить список активных игроков, если нужно
    game_state = game_state_manager.get_state()
    if user_code in game_state['users_in_game']:
        game_state['users_in_game'].remove(user_code)
        game_state_manager.update_state(users_in_game=game_state['users_in_game'])
        socketio.emit('game_update', game_state_manager.get_state(), broadcast=True)

# ... (Другие SocketIO события, например, выбор карты, угадывание, начало раунда) ...
# ВАЖНО: ВСЕ эти события теперь должны читать и записывать состояние ИСКЛЮЧИТЕЛЬНО через game_state_manager.
# И после любого изменения состояния - отправлять socketio.emit('game_update', game_state_manager.get_state(), broadcast=True)

@socketio.on('choose_card_leader')
def handle_choose_card_leader(data):
    sid = request.sid
    user_code = connected_users_socketio.get(sid)
    if not user_code: return

    card_id = data.get('card_id')
    card_description = data.get('description', '')

    game_state = game_state_manager.get_state()
    if game_state['game_round_state'] != 'choosing_cards' or game_state['current_round_leader_code'] != user_code:
        emit('error', 'Сейчас не время выбирать карты или вы не лидер раунда.')
        return

    chosen_card = next((c for c in game_state['current_cards_on_table'] if c['card_id'] == card_id), None)
    if chosen_card:
        # Убедитесь, что никто другой не выбрал эту карту ранее в текущем раунде
        if chosen_card['chosen_by'] is not None:
             emit('error', 'Эта карта уже выбрана.')
             return

        chosen_card['chosen_by'] = user_code
        game_state['current_round_card_chosen'] = card_id
        game_state['current_round_card_description'] = card_description
        game_state['game_round_state'] = 'voting_cards' # Переход к голосованию

        game_state_manager.set_state(game_state) # Сохраняем обновленное состояние
        socketio.emit('game_update', game_state_manager.get_state(), broadcast=True)
    else:
        emit('error', 'Карта не найдена.')

@socketio.on('vote_card_player')
def handle_vote_card_player(data):
    sid = request.sid
    user_code = connected_users_socketio.get(sid)
    if not user_code: return

    card_id = data.get('card_id')

    game_state = game_state_manager.get_state()
    if game_state['game_round_state'] != 'voting_cards':
        emit('error', 'Сейчас не время голосовать.')
        return

    # Игрок-лидер не голосует
    if game_state['current_round_leader_code'] == user_code:
        emit('error', 'Лидер раунда не может голосовать за карту.')
        return

    # Игрок-угадывающий не голосует здесь (у него отдельная логика)
    if game_state['current_guesser_user_code'] == user_code:
        emit('error', 'Угадывающий игрок не может голосовать за карты.')
        return

    # Проверяем, что пользователь еще не голосовал в этом раунде
    if game_state['voted_for_card'].get(user_code) is not None:
        emit('error', 'Вы уже проголосовали в этом раунде.')
        return

    voted_card = next((c for c in game_state['current_cards_on_table'] if c['card_id'] == card_id), None)
    if voted_card:
        game_state['voted_for_card'][user_code] = card_id
        game_state_manager.set_state(game_state)
        # Можно отправлять частичное обновление, или полное, если хотите показать кто проголосовал (для дебага)
        socketio.emit('game_update', game_state_manager.get_state(), broadcast=True) # Отправляем всем

        # Проверяем, все ли проголосовали (кроме лидера и угадывающего)
        players_to_vote = [uc for uc in game_state['users_in_game'] if uc != game_state['current_round_leader_code'] and uc != game_state['current_guesser_user_code']]
        if len(game_state['voted_for_card']) >= len(players_to_vote):
            # Все проголосовали, переходим к этапу угадывания
            game_state['game_round_state'] = 'guesser_voting' # Новый этап
            # Выбираем случайного угадывающего (или по очереди)
            # В данном примере просто случайный из нелидеров
            eligible_guessers = [uc for uc in game_state['users_in_game'] if uc != game_state['current_round_leader_code']]
            if eligible_guessers:
                game_state['current_guesser_user_code'] = random.choice(eligible_guessers)
            else:
                game_state['current_guesser_user_code'] = None # Никто не может угадывать

            game_state_manager.set_state(game_state)
            socketio.emit('game_update', game_state_manager.get_state(), broadcast=True)
    else:
        emit('error', 'Карта не найдена.')

@socketio.on('guess_card_guesser')
def handle_guess_card_guesser(data):
    sid = request.sid
    user_code = connected_users_socketio.get(sid)
    if not user_code: return

    card_id = data.get('card_id')

    game_state = game_state_manager.get_state()
    if game_state['game_round_state'] != 'guesser_voting' or game_state['current_guesser_user_code'] != user_code:
        emit('error', 'Сейчас не время угадывать или вы не угадывающий игрок.')
        return

    # Проверяем, что угадывающий еще не голосовал
    if game_state['guesser_voted_for_card'].get(user_code) is not None:
        emit('error', 'Вы уже угадали карту в этом раунде.')
        return

    guessed_card = next((c for c in game_state['current_cards_on_table'] if c['card_id'] == card_id), None)
    if guessed_card:
        game_state['guesser_voted_for_card'][user_code] = card_id
        game_state_manager.set_state(game_state)
        socketio.emit('game_update', game_state_manager.get_state(), broadcast=True)

        # После угадывания переходим к подсчету очков
        game_state['game_round_state'] = 'score_display'
        calculate_scores_and_update_state(game_state) # Функция для подсчета очков
        game_state_manager.set_state(game_state)
        socketio.emit('game_update', game_state_manager.get_state(), broadcast=True)
    else:
        emit('error', 'Карта не найдена.')

def calculate_scores_and_update_state(game_state):
    """
    Расчет очков в конце раунда.
    Логика на основе правил Диксита/Имаджинариума:
    1. Если все или никто не угадал карту лидера, лидер получает 0 очков, остальные по 2 очка.
    2. В противном случае, лидер получает 3 очка. Те, кто угадал карту лидера, получают 3 очка.
    3. Те, чьи карты угадали другие (кроме лидера), получают 1 очко за каждый голос.
    4. Угадывающий получает 3 очка, если угадал карту лидера.
    """
    leader_code = game_state['current_round_leader_code']
    leader_chosen_card_id = game_state['current_round_card_chosen']
    cards_on_table = game_state['current_cards_on_table']
    user_points = game_state['user_points'] # Обновляем эту структуру

    # Определяем, какие игроки проголосовали за карту лидера
    voters_for_leader_card = [
        uc for uc, card_id in game_state['voted_for_card'].items()
        if card_id == leader_chosen_card_id
    ]
    num_players_voted_for_leader_card = len(voters_for_leader_card)
    total_players_voting = len(game_state['users_in_game']) - 1 # Все, кроме лидера
    if game_state['current_guesser_user_code']:
        total_players_voting -= 1 # Исключаем угадывающего, если он есть

    # Получаем user_code угадывающего
    guesser_code = game_state['current_guesser_user_code']
    guesser_voted_for_card_id = game_state['guesser_voted_for_card'].get(guesser_code)

    # 1. Сценарий: все или никто не угадал карту лидера (для обычных игроков)
    all_voted_leader = num_players_voted_for_leader_card == total_players_voting
    no_one_voted_leader = num_players_voted_for_leader_card == 0

    if all_voted_leader or no_one_voted_leader:
        user_points[leader_code] = user_points.get(leader_code, 0) + 0 # Лидер 0 очков
        for player_code in game_state['users_in_game']:
            if player_code != leader_code and player_code != guesser_code:
                user_points[player_code] = user_points.get(player_code, 0) + 2 # Остальные по 2
    else:
        # 2. Сценарий: карта лидера угадана не всеми и не никем
        user_points[leader_code] = user_points.get(leader_code, 0) + 3 # Лидер 3 очка
        for player_code in voters_for_leader_card:
            user_points[player_code] = user_points.get(player_code, 0) + 3 # Кто угадал лидера - 3 очка

    # 3. Подсчет очков за угаданные карты игроков
    for card in cards_on_table:
        if card['chosen_by'] and card['chosen_by'] != leader_code: # Если это карта игрока, а не лидера
            votes_for_this_card = [uc for uc, card_id in game_state['voted_for_card'].items() if card_id == card['card_id']]
            if card['chosen_by'] in user_points:
                user_points[card['chosen_by']] += len(votes_for_this_card) # Очко за каждый голос

    # 4. Очки для угадывающего игрока
    if guesser_code and guesser_voted_for_card_id == leader_chosen_card_id:
        user_points[guesser_code] = user_points.get(guesser_code, 0) + 3

    # Обновляем user_points в состоянии игры
    game_state['user_points'] = user_points
    # Очищаем временные данные раунда для следующего
    game_state['current_cards_on_table'] = []
    game_state['voted_for_card'] = {}
    game_state['guesser_voted_for_card'] = {}
    game_state['current_round_leader_code'] = None
    game_state['current_round_card_chosen'] = None
    game_state['current_round_card_description'] = ''
    game_state['current_guesser_user_code'] = None
    game_state['round_start_time'] = None

    # Переход к следующему раунду или окончанию игры
    game_state['game_round_state'] = 'waiting_for_players' # Для начала нового раунда

@socketio.on('start_new_round')
def handle_start_new_round():
    sid = request.sid
    user_code = connected_users_socketio.get(sid)
    if not user_code: return

    game_state = game_state_manager.get_state()

    # Только если игра в ожидании игроков или после подсчета очков
    if game_state['game_round_state'] not in ['waiting_for_players', 'score_display']:
        emit('error', 'Игра уже идет или раунд не завершен.')
        return

    # Выбираем лидера раунда (простая логика: случайный активный игрок)
    active_players = [uc for uc in game_state['users_in_game'] if uc in game_state['user_points']]
    if not active_players:
        emit('error', 'Нет активных игроков для начала раунда.')
        return

    # TODO: Более сложная логика выбора лидера (по очереди)
    new_leader_code = random.choice(active_players)

    game_state['current_round_leader_code'] = new_leader_code
    game_state['game_round_state'] = 'choosing_cards' # Новый этап
    game_state['round_start_time'] = time.time() # Устанавливаем время начала раунда
    game_state['current_cards_on_table'] = [] # Очищаем карты предыдущего раунда
    game_state['voted_for_card'] = {} # Сброс голосов
    game_state['guesser_voted_for_card'] = {} # Сброс голосов угадывающего

    game_state_manager.set_state(game_state)
    socketio.emit('game_update', game_state_manager.get_state(), broadcast=True)
    emit('info', f'Начинается новый раунд. Лидер: {new_leader_code}')


@socketio.on('request_game_state')
def handle_request_game_state():
    sid = request.sid
    user_code = connected_users_socketio.get(sid)
    if user_code:
        game_state = game_state_manager.get_state()
        emit('game_update', game_state, room=sid)

# --- Запуск приложения ---
if __name__ == "__main__":
    create_db_schema() # Убедимся, что БД создана

    # Инициализация игрового поля при старте приложения
    # Это будет вызываться каждым воркером, но только первый реально инициализирует
    # состояние в Redis (потому что other воркеры увидят, что ключ уже существует)
    print("Инициализация игрового состояния при старте...", file=sys.stderr)
    users_at_start = []
    if os.path.exists(DB_PATH):
        try:
            conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row; cur = conn.cursor()
            cur.execute("SELECT id, name, rating, user_code, role, status FROM users WHERE status = 'active'")
            users_at_start = cur.fetchall(); conn.close()
        except Exception as e: print(f"Ошибка чтения пользователей для поля при старте: {e}", file=sys.stderr)
    initialize_game_state(all_users_for_rating_check=users_at_start)

    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "False") == "True" # Читаем FLASK_DEBUG
    
    # Для Gunicorn Eventlet, используем socketio.run
    # Обратите внимание, что socketio.run() будет использовать eventlet.wsgi.server
    # и не требует gunicorn напрямую в __main__.
    # Однако на Render вы будете запускать через Gunicorn.
    # Это секция для локальной разработки, а не для запуска Gunicorn.
    print(f"Запуск приложения на порту {port} (Debug: {debug})", file=sys.stderr)
    socketio.run(app, host='0.0.0.0', port=port, debug=debug)
