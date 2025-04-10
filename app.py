from flask import Flask, render_template, request, redirect, url_for
import sqlite3
import os
import string
import random

app = Flask(__name__)
DB_PATH = 'database.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            code TEXT UNIQUE NOT NULL,
            rating INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

def generate_unique_code(length=8):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

@app.route("/")
def index():
    return "<h1>Hello, world!</h1><p><a href='/admin'>Перейти в админку</a></p>"

@app.route("/admin/delete/<int:user_id>", methods=["POST"])
def delete_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("admin"))
    
@app.route("/admin", methods=["GET", "POST"])
def admin():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    message = ""
    if request.method == "POST":
        name = request.form.get("name")
        if name:
            code = generate_unique_code()
            try:
                c.execute("INSERT INTO users (name, code) VALUES (?, ?)", (name.strip(), code))
                conn.commit()
                message = f"Пользователь '{name}' добавлен."
            except sqlite3.IntegrityError:
                message = f"Имя '{name}' уже существует."
    c.execute("SELECT name, code, rating FROM users")
    users = c.fetchall()
    conn.close()
    return render_template("admin.html", users=users, message=message)

@app.route("/user/<code>")
def user(code):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT name, rating FROM users WHERE code = ?", (code,))
    row = c.fetchone()
    conn.close()
    if row:
        name, rating = row
        return render_template("user.html", name=name, rating=rating)
    else:
        return "<h1>Пользователь не найден</h1>", 404

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
