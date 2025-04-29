import json
from flask import Flask, render_template, request, redirect, url_for
import sqlite3
import os
import string
import random
from collections import defaultdict

app = Flask(__name__)
DB_PATH = 'database.db'
app.secret_key = "super secret"  # Needed for flash messages

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Включаем WAL
    c.execute("PRAGMA journal_mode=WAL")

    #   Удаляем таблицы и создаем заново
    c.execute("DROP TABLE IF EXISTS users")
    c.execute("DROP TABLE IF EXISTS images")
    c.execute("DROP TABLE IF EXISTS settings")

    c.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            code TEXT UNIQUE NOT NULL,
            rating INTEGER DEFAULT 0
        )
    """)  #

    c.execute("""
        CREATE TABLE images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subfolder TEXT NOT NULL,
            image TEXT NOT NULL,
            status TEXT,
            owner_id INTEGER,  -- New column
            guesses TEXT       -- New column
        )
    """)  #

    c.execute("""
        CREATE TABLE settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)  #

    #   Загрузка изображений из static/images
    image_folders = ['koloda1', 'koloda2']
    for folder in image_folders:
        folder_path = os.path.join('static', 'images', folder)
        if os.path.exists(folder_path):
            for filename in os.listdir(folder_path):
                if filename.endswith('.jpg'):
                    c.execute("INSERT INTO images (subfolder, image, status, owner_id, guesses) VALUES (?, ?, 'Свободно', NULL, '{}')", (folder, filename))  # Initialize new columns

    #   Удаляем статусы "Занято" (при новом запуске)
    c.execute("UPDATE images SET status = 'Свободно'")  #

    conn.commit()
    conn.close()

def generate_unique_code(length=8):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))  #

def get_setting(key):
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = c.fetchone()
        return row[0] if row else None  #
    finally:
        conn.close()

def set_setting(key, value):
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
    finally:
        conn.close()  #

def get_leading_user_id():
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute("SELECT value FROM settings WHERE key = 'leading_user_id'")
        result = c.fetchone()
        if result:
            return int(result[0])
        return None  #
    finally:
        conn.close()

def set_leading_user_id(user_id):
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute("REPLACE INTO settings (key, value) VALUES ('leading_user_id', ?)", (user_id,))
        conn.commit()
    finally:
        conn.close()  #


def get_user_name(user_id):
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute("SELECT name FROM users WHERE id = ?", (user_id,))
        user_name = c.fetchone()
        conn.close()
        if user_name:
            return user_name[0]
        return None  #
    finally:
        conn.close()

app.jinja_env.globals.update(get_user_name=get_user_name, g=g, get_leading_user_id=get_leading_user_id) # Make the function globally available

@app.route("/")
def index():
    return "<h1>Hello, world!</h1><p><a href='/admin'>Перейти в админку</a></p>"  #

@app.route("/admin", methods=["GET", "POST"])
def admin():
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        message = ""

        if request.method == "POST":
            if "name" in request.form:
                name = request.form.get("name").strip()
                num_cards = int(request.form.get("num_cards", 3))
                code = generate_unique_code()  #

                try:
                    c.execute("INSERT INTO users (name, code) VALUES (?, ?)", (name, code))
                    user_id = c.lastrowid

                    #   Назначаем карточки пользователю из активной колоды в случайном порядке
                    active_subfolder = get_setting("active_subfolder")  #
                    if active_subfolder:
                        c.execute("""
                            SELECT id, subfolder, image
                            FROM images
                            WHERE subfolder = ?
                            AND status = 'Свободно'
                        """, (active_subfolder,))  #
                        available_cards = c.fetchall()

                        if len(available_cards) < num_cards:
                            message = f"Недостаточно свободных карточек в колоде {active_subfolder}."  #
                        else:
                            random.shuffle(available_cards)  #   Перемешиваем карточки
                            selected_cards = available_cards[:num_cards]  #   Выбираем нужное количество

                            for card in selected_cards:
                                c.execute("UPDATE images SET status = ? WHERE id = ?", (f"Занято:{user_id}", card[0]))  #

                    conn.commit()
                    message = f"Пользователь '{name}' добавлен."  #

                except sqlite3.IntegrityError:
                    message = f"Имя '{name}' уже существует."  #

            elif "active_subfolder" in request.form:
                selected = request.form.get("active_subfolder")
                set_setting("active_subfol
