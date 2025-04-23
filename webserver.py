from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import subprocess
import os
import psutil
import sqlite3
from datetime import datetime
import time
import threading
from functools import wraps

app = Flask(__name__)
app.secret_key = 'afd123'  # Ändere das in der Produktion!

USERNAME = 'admin'
PASSWORD = 'minecraft1'

# SQLite-Datenbank-Setup
def init_db():
    conn = sqlite3.connect('minecraft_monitor.db')
    cursor = conn.cursor()
    
    # Tabelle für Systemmetriken
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS system_metrics (
            id INTEGER PRIMARY KEY,
            timestamp DATETIME NOT NULL,
            cpu_usage REAL NOT NULL,
            ram_usage REAL NOT NULL,
            disk_usage REAL NOT NULL
        )
    ''')
    
    # Tabelle für Server-Status
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS server_status (
            id INTEGER PRIMARY KEY,
            timestamp DATETIME NOT NULL,
            status TEXT NOT NULL
        )
    ''')
    
    conn.commit()
    conn.close()

def get_server_status():
    try:
        result = subprocess.run(
            ['sudo', 'systemctl', 'is-active', '--quiet', 'minecraft'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return "Läuft" if result.returncode == 0 else "Gestoppt"
    except Exception:
        return "Unbekannt"

# Funktion zum Aufzeichnen der Metriken
def record_metrics():
    conn = sqlite3.connect('minecraft_monitor.db')
    cursor = conn.cursor()
    
    # System-Metriken erfassen
    cpu = psutil.cpu_percent(interval=1)
    ram = psutil.virtual_memory().percent
    disk = psutil.disk_usage('/').percent
    
    # In Datenbank speichern
    cursor.execute(
        'INSERT INTO system_metrics (timestamp, cpu_usage, ram_usage, disk_usage) VALUES (?, ?, ?, ?)',
        (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), cpu, ram, disk)
    )
    
    # Server-Status erfassen und speichern
    status = get_server_status()
    cursor.execute(
        'INSERT INTO server_status (timestamp, status) VALUES (?, ?)',
        (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), status)
    )
    
    conn.commit()
    conn.close()

# Hintergrund-Task für regelmäßige Aufzeichnung
def background_metrics_recorder():
    while True:
        try:
            record_metrics()
        except Exception as e:
            print(f"Fehler bei der Metrikaufzeichnung: {e}")
        time.sleep(300)  # Alle 5 Minuten

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
@login_required
def index():
    server_status = get_server_status()
    return render_template('index.html', server_status=server_status)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form['username'] == USERNAME and request.form['password'] == PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('index'))
        return render_template('login.html', error='Falsche Zugangsdaten')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

@app.route('/start')
@login_required
def start():
    subprocess.run(['sudo', 'systemctl', 'start', 'minecraft'])
    record_metrics()  # Sofort den neuen Status aufzeichnen
    return redirect(url_for('index'))

@app.route('/stop')
@login_required
def stop():
    subprocess.run(['sudo', 'systemctl', 'stop', 'minecraft'])
    record_metrics()  # Sofort den neuen Status aufzeichnen
    return redirect(url_for('index'))

@app.route('/restart')
@login_required
def restart():
    subprocess.run(['sudo', 'systemctl', 'restart', 'minecraft'])
    record_metrics()  # Sofort den neuen Status aufzeichnen
    return redirect(url_for('index'))

@app.route('/systemdata')
@login_required
def systemdata():
    cpu = psutil.cpu_percent(interval=0.5)
    ram = psutil.virtual_memory().percent
    disk = psutil.disk_usage('/').percent
    return jsonify({
        'cpu': cpu,
        'ram': ram,
        'disk': disk
    })

@app.route('/history/metrics/<period>')
@login_required
def history_metrics(period):
    conn = sqlite3.connect('minecraft_monitor.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Zeitraum bestimmen
    if period == '24h':
        time_clause = "timestamp >= datetime('now', '-1 day')"
    elif period == '7d':
        time_clause = "timestamp >= datetime('now', '-7 days')"
    elif period == '30d':
        time_clause = "timestamp >= datetime('now', '-30 days')"
    else:
        time_clause = "timestamp >= datetime('now', '-1 day')"
    
    # Systemmetriken abfragen
    cursor.execute(f'''
        SELECT timestamp, cpu_usage, ram_usage, disk_usage
        FROM system_metrics
        WHERE {time_clause}
        ORDER BY timestamp
    ''')
    
    metrics = [dict(row) for row in cursor.fetchall()]
    
    # Server-Status-Änderungen abfragen
    cursor.execute(f'''
        SELECT timestamp, status
        FROM server_status
        WHERE {time_clause}
        ORDER BY timestamp
    ''')
    
    status_changes = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    
    return jsonify({
        'metrics': metrics,
        'status_changes': status_changes
    })

@app.route('/uptime')
@login_required
def get_uptime():
    conn = sqlite3.connect('minecraft_monitor.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Letzten Status ermitteln
    cursor.execute('SELECT status FROM server_status ORDER BY timestamp DESC LIMIT 1')
    last_status = cursor.fetchone()
    
    uptime_str = "Server offline"
    
    # Wenn der Server läuft, die letzte Start-Zeit ermitteln
    if last_status and last_status['status'] == 'Läuft':
        cursor.execute('''
            SELECT timestamp
            FROM server_status
            WHERE status = 'Läuft'
            AND id = (
                SELECT min(id)
                FROM server_status
                WHERE status = 'Läuft'
                AND id > (
                    SELECT max(id)
                    FROM server_status
                    WHERE status = 'Gestoppt'
                )
            )
        ''')
        
        start_time = cursor.fetchone()
        
        if start_time:
            start_timestamp = datetime.strptime(start_time['timestamp'], '%Y-%m-%d %H:%M:%S')
            uptime_seconds = (datetime.now() - start_timestamp).total_seconds()
            
            # Uptime formatieren
            hours, remainder = divmod(int(uptime_seconds), 3600)
            minutes, seconds = divmod(remainder, 60)
            
            uptime_str = f"{hours}h {minutes}m"
        else:
            uptime_str = "Unbekannt"
    
    conn.close()
    
    return jsonify({'uptime': uptime_str})

if __name__ == '__main__':
    # Initialisiere die Datenbank
    init_db()
    
    # Starte den Hintergrund-Thread für die Metrikerfassung
    metrics_thread = threading.Thread(target=background_metrics_recorder, daemon=True)
    metrics_thread.start()
    
    app.run(host='0.0.0.0', port=8080)
