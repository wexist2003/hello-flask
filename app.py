from flask import Flask, render_template
import sqlite3

app = Flask(__name__)

# Инициализация БД
def init_db():
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS visits (count INTEGER)')
    c.execute('INSERT INTO visits (count) SELECT 0 WHERE NOT EXISTS (SELECT 1 FROM visits)')
    conn.commit()
    conn.close()

@app.route("/")
def index():
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute('UPDATE visits SET count = count + 1')
    conn.commit()
    c.execute('SELECT count FROM visits')
    visit_count = c.fetchone()[0]
    conn.close()
    return render_template("index.html", visits=visit_count)

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
