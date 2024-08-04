import sqlite3

def connect_sqlite():
      conn = sqlite3.connect('statics/data.db')
      cursor = conn.cursor()
      return conn, cursor
    
def create_table(cursor):
    cursor.execute('''CREATE TABLE IF NOT EXISTS transmisiones
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plataforma TEXT NOT NULL,
                    key TEXT NOT NULL)''')

def get_transmisiones(cursor):
    cursor.execute("SELECT * FROM transmisiones")
    return cursor.fetchall()      
def insert_transmision(cursor, plataforma, key):
    cursor.execute("DELETE FROM transmisiones WHERE plataforma = ?", (plataforma,))
    cursor.execute("INSERT INTO transmisiones (plataforma, key) VALUES (?, ?)", (plataforma, key))
    