import os
import random
import sqlite3
import string

from flask import Flask, render_template, request, redirect, url_for, g

app = Flask(__name__)
DATABASE = "users.db"

def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()

def init_db():
    if not os.path.exists(DATABASE):
        with app.app_context():
            db = get_db()
            cursor = db.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    code TEXT NOT NULL UNIQUE
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS images (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    image_path TEXT,
                    FOREIGN KEY (user_id) REFERENCES users (id)
                )
                """
            )
            db.commit()

def generate_code(length=6):
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        username = request.form["username"]
        code = generate_code()
        db = get_db()
        cursor = db.cursor()
        cursor.execute("INSERT INTO users (username, code) VALUES (?, ?)", (username, code))
        db.commit()
        return redirect(url_for("user_page", code=code))
    return render_template("index.html")

@app.route("/admin")
def admin():
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM users")
    users = cursor.fetchall()
    return render_template("admin.html", users=users)

@app.route("/user/<code>")
def user_page(code):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM users WHERE code = ?", (code,))
    user = cursor.fetchone()
    if user:
        cursor.execute("SELECT * FROM images WHERE user_id = ?", (user[0],))
        images = cursor.fetchall()
        return render_template("user.html", user=user, images=images)
    return "User not found", 404


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
