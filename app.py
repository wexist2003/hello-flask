from flask import Flask, render_template, request, redirect, url_for
import sqlite3
import os

app = Flask(__name__)
DB_PATH = 'database.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    rating INTEGER DEFAULT 0
                )""")
    conn.commit()
    conn.close()

@app.route("/")
def index():
    return "<h1>Hello, world!</h1><p><a href='/admin'>Перейти в админку</a></p>"

@app.route("/admin", methods=["GET", "POST"])
def admin():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    message = ""
    if request.method == "POST":
        name = request.form.get("name")
        if name:
            try:
                c.execute("INSERT INTO users (name) VALUES (?)", (name.strip(),))
                conn.commit()
                message = f"Пользователь '{name}' добавлен."
            except sqlite3.IntegrityError:
                message = f"Имя '{name}' уже существует."
    c.execute("SELECT name, rating FROM users")
    users = c.fetchall()
    conn.close()
    return render_template("admin.html", users=users, message=message)

@app.route("/user/<name>")
def user(name):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT rating FROM users WHERE name = ?", (name,))
    row = c.fetchone()
    conn.close()
    if row:
        return render_template("user.html", name=name, rating=row[0])
    else:
        return "<h1>Пользователь не найден</h1>", 404

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
