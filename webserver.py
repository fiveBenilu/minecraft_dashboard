import os
import re
import shutil
import signal
import sqlite3
import subprocess
import threading
import time
from datetime import datetime
from functools import wraps
import atexit

import psutil
import secrets
import tarfile
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash

backup_restore_progress = {"step": "", "in_progress": False, "error": None}

# Globale Variablen für Backup-Einstellungen
BACKUP_INTERVAL_HOURS = 24
BACKUP_KEEP_COUNT = 3

minecraft_process = None

# Globale Variablen für Spieleraktivität
active_players = {}
total_online_players = 0

def update_player_activity():
    global active_players, total_online_players
    log_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../minecraft/logs/latest.log'))
    if not os.path.exists(log_path):
        active_players = {}
        total_online_players = 0
        return
    join_pattern = re.compile(r'\[\d{2}:\d{2}:\d{2}\] \[.*?\]: \[\+\] (.+?) joined the server!')
    leave_pattern = re.compile(r'\[\d{2}:\d{2}:\d{2}\] \[.*?\]: \[-\] (.+?) left the server!')

    last_seen = {}
    joined = {}

    with open(log_path, 'r') as f:
        for line in f:
            join_match = join_pattern.search(line)
            if join_match:
                timestamp = datetime.strptime(line[1:9], '%H:%M:%S')
                player = join_match.group(1)
                joined[player] = timestamp
                last_seen[player] = timestamp
                continue

            leave_match = leave_pattern.search(line)
            if leave_match:
                timestamp = datetime.strptime(line[1:9], '%H:%M:%S')
                player = leave_match.group(1)
                last_seen[player] = timestamp
                if player in joined:
                    del joined[player]
    active_players = {
        'online': {p: (datetime.now() - joined[p]).seconds for p in joined},
        'offline': {p: (datetime.now() - last_seen[p]).seconds for p in last_seen if p not in joined}
    }
    total_online_players = len(joined)
 
app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', secrets.token_hex(32))

USERNAME = 'admin'
PASSWORD = 'minecraft1'


def init_db():
    conn = sqlite3.connect('minecraft_monitor.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS system_metrics (
            id INTEGER PRIMARY KEY,
            timestamp DATETIME NOT NULL,
            cpu_usage REAL NOT NULL,
            ram_usage REAL NOT NULL,
            disk_usage REAL NOT NULL
        )
    ''')
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
    for proc in psutil.process_iter(['name', 'cmdline']):
        try:
            if 'java' in proc.info['name'] and any('server.jar' in str(arg) for arg in proc.info['cmdline']):
                return "Läuft"
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return "Gestoppt"


def record_metrics():
    conn = sqlite3.connect('minecraft_monitor.db')
    cursor = conn.cursor()
    cpu = psutil.cpu_percent(interval=1)
    ram = psutil.virtual_memory().percent
    disk = psutil.disk_usage('/').percent
    cursor.execute(
        'INSERT INTO system_metrics (timestamp, cpu_usage, ram_usage, disk_usage) VALUES (?, ?, ?, ?)',
        (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), cpu, ram, disk)
    )
    status = get_server_status()
    cursor.execute(
        'INSERT INTO server_status (timestamp, status) VALUES (?, ?)',
        (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), status)
    )
    conn.commit()
    conn.close()


def background_metrics_recorder():
    while True:
        try:
            record_metrics()
        except Exception as e:
            print(f"Fehler bei der Metrikaufzeichnung: {e}")
        time.sleep(300)


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
    global minecraft_process
    if get_server_status() == "Gestoppt":
        minecraft_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../minecraft'))
        jar_path = os.path.join(minecraft_dir, 'server.jar')
        if os.path.exists(jar_path):
            minecraft_process = subprocess.Popen(
                ['java', '-Xmx1024M', '-Xms1024M', '-jar', 'server.jar', 'nogui'],
                cwd=minecraft_dir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
    record_metrics()
    return redirect(url_for('index'))


@app.route('/stop')
@login_required
def stop():
    global minecraft_process
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if 'java' in proc.info['name'] and any('server.jar' in str(arg) for arg in proc.info['cmdline']):
                proc.terminate()
                proc.wait(timeout=10)
                break
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
            continue
    minecraft_process = None
    record_metrics()
    return redirect(url_for('index'))


@app.route('/restart')
@login_required
def restart():
    global minecraft_process
    stop()
    time.sleep(2)
    start()
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

    if period == '24h':
        time_clause = "timestamp >= datetime('now', '-1 day')"
    elif period == '7d':
        time_clause = "timestamp >= datetime('now', '-7 days')"
    elif period == '30d':
        time_clause = "timestamp >= datetime('now', '-30 days')"
    else:
        time_clause = "timestamp >= datetime('now', '-1 day')"

    cursor.execute(f'''
        SELECT timestamp, cpu_usage, ram_usage, disk_usage
        FROM system_metrics
        WHERE {time_clause}
        ORDER BY timestamp
    ''')
    metrics = [dict(row) for row in cursor.fetchall()]

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

    cursor.execute('SELECT status FROM server_status ORDER BY timestamp DESC LIMIT 1')
    last_status = cursor.fetchone()
    uptime_str = "Server offline"
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
            hours, remainder = divmod(int(uptime_seconds), 3600)
            minutes, seconds = divmod(remainder, 60)
            uptime_str = f"{hours}h {minutes}m"
        else:
            uptime_str = "Unbekannt"
    conn.close()
    return jsonify({'uptime': uptime_str})


@app.route('/logs')
@login_required
def logs():
    update_player_activity()
    log_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../minecraft/logs/latest.log'))
    if not os.path.exists(log_path):
        logs = ["Logdatei nicht gefunden. Stelle sicher, dass dein Server läuft und Logs erzeugt."]
    else:
        with open(log_path, 'r') as f:
            logs = f.readlines()[-200:]
    return render_template('logs.html', logs=logs, players=active_players, total_players=total_online_players)


@app.route('/logs/players')
@login_required
def logs_players():
    update_player_activity()
    return jsonify({
        'online': active_players['online'],
        'offline': active_players['offline'],
        'total_online': total_online_players
    })


BACKUP_DIR = os.path.join(os.path.dirname(__file__), 'backups')
MINECRAFT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../minecraft'))


def extract_backup_structure(backup_path):
    print(f"[DEBUG] Starte extract_backup_structure mit Pfad: {backup_path}")
    structure = []
    try:
        with tarfile.open(backup_path, "r:gz") as tar:
            print(f"[DEBUG] Archiv geöffnet: {backup_path}, {len(tar.getmembers())} Einträge gefunden.")
            for member in tar.getmembers():
                print(f"[DEBUG] {member.name} | Typ: {'Ordner' if member.isdir() else 'Datei'} | Größe: {member.size}")
                if not member.name or member.name.startswith("../"):
                    continue
                structure.append({
                    "name": os.path.relpath(member.name),
                    "size": member.size,
                    "type": "Ordner" if member.isdir() else "Datei"
                })
    except Exception as e:
        structure.append({"name": f"Fehler beim Lesen des Archivs: {str(e)}", "size": 0, "type": "Fehler"})
    return structure


def list_backups():
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)
    backups = []
    for file in sorted(os.listdir(BACKUP_DIR), reverse=True):
        path = os.path.join(BACKUP_DIR, file)
        size = os.path.getsize(path)
        created = datetime.fromtimestamp(os.path.getmtime(path)).strftime('%d.%m.%Y %H:%M')
        backups.append({
            'name': file,
            'size': size,
            'created': created,
            'delete_filename': file
        })
    return backups


def create_backup():
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)
    # Ensure server is stopped before backup
    if get_server_status() == "Läuft":
        stop()
        time.sleep(2)
    date_str = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    backup_path = os.path.join(BACKUP_DIR, f'backup_{date_str}.tar.gz')
    subprocess.run(
        ['tar', '--exclude=./backups', '-czf', backup_path, '.'],
        cwd=os.path.abspath(MINECRAFT_DIR),
        check=True
    )
    return backup_path


def cleanup_old_backups(days_to_keep=3):
    backups = list_backups()
    to_delete = backups[3:]
    for b in to_delete:
        os.remove(os.path.join(BACKUP_DIR, b['name']))


@app.route('/backups')
@login_required
def backup_page():
    backups = list_backups()
    print("Backup funktion aufgerunfn")
    detailed_backups = backups
    return render_template('backup.html', backups=detailed_backups)


@app.route('/backup/create', methods=['POST'])
@login_required
def backup_create():
    create_backup()
    cleanup_old_backups()
    flash('Backup wurde erfolgreich erstellt.')
    return redirect(url_for('backup_page'))


@app.route('/backup/restore/<backup_file>', methods=['POST'])
@login_required
def backup_restore(backup_file):
    def restore_task():
        global backup_restore_progress
        backup_restore_progress = {"step": "Backup-Vorgang gestartet...", "in_progress": True, "error": None}
        try:
            backup_restore_progress["step"] = "Aktuellen Zustand sichern"
            create_backup()
            minecraft_dir = MINECRAFT_DIR
            for item in os.listdir(minecraft_dir):
                item_path = os.path.join(minecraft_dir, item)
                if item == 'backups':
                    continue
                if os.path.isfile(item_path) or os.path.islink(item_path):
                    os.unlink(item_path)
                elif os.path.isdir(item_path):
                    shutil.rmtree(item_path)
            backup_path = os.path.join(BACKUP_DIR, backup_file)
            backup_restore_progress["step"] = "Backup wird wiederhergestellt"
            subprocess.run(['tar', '--strip-components=1', '-xzf', backup_path], cwd=minecraft_dir, check=True)
            backup_restore_progress["step"] = "Backup erfolgreich eingespielt"
        except subprocess.CalledProcessError as e:
            backup_restore_progress["error"] = str(e)
            backup_restore_progress["step"] = "Fehler beim Wiederherstellen"
        finally:
            time.sleep(1)
            backup_restore_progress["in_progress"] = False
    threading.Thread(target=restore_task).start()
    return jsonify({"message": "Wiederherstellung gestartet"})


@app.route('/backup/restore/status')
@login_required
def backup_restore_status():
    response = {
        "step": backup_restore_progress["step"],
        "in_progress": backup_restore_progress["in_progress"],
        "error": backup_restore_progress["error"]
    }
    return jsonify(response)


def stop_minecraft_on_exit():
    global minecraft_process
    if minecraft_process and minecraft_process.poll() is None:
        print("Minecraft-Server wird beendet...")
        minecraft_process.send_signal(signal.SIGINT)
        try:
            minecraft_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            minecraft_process.kill()


atexit.register(stop_minecraft_on_exit)
signal.signal(signal.SIGTERM, lambda signum, frame: (stop_minecraft_on_exit(), os._exit(0)))
signal.signal(signal.SIGINT, lambda signum, frame: (stop_minecraft_on_exit(), os._exit(0)))


@app.route('/logs/data')
@login_required
def logs_data():
    log_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../minecraft/logs/latest.log'))
    if not os.path.exists(log_path):
        return "Logdatei nicht gefunden."
    with open(log_path, 'r') as f:
        return f.read()[-15000:]


@app.route('/backup/delete/<backup_file>', methods=['POST'])
@login_required
def backup_delete(backup_file):
    backup_path = os.path.join(BACKUP_DIR, backup_file)
    if os.path.exists(backup_path):
        os.remove(backup_path)
        flash(f'Backup {backup_file} wurde gelöscht.')
    else:
        flash(f'Datei {backup_file} nicht gefunden.')
    return redirect(url_for('backup_page'))


@app.route('/backup/details/<backup_file>')
@login_required
def backup_details(backup_file):
    print(f"[DEBUG] backup_details für: {backup_file}")
    backup_path = os.path.join(BACKUP_DIR, backup_file)
    if not os.path.exists(backup_path):
        return jsonify({"error": "Datei nicht gefunden"}), 404
    structure = extract_backup_structure(backup_path)
    return jsonify(structure)


def start_backup_scheduler():
    def scheduled_backup():
        while True:
            try:
                print("[Backup-Scheduler] Starte automatisches Backup...")
                create_backup()
                cleanup_old_backups(BACKUP_KEEP_COUNT)
                print("[Backup-Scheduler] Backup erfolgreich erstellt.")
            except Exception as e:
                print(f"[Backup-Scheduler] Fehler beim Erstellen des automatischen Backups: {e}")
            time.sleep(BACKUP_INTERVAL_HOURS * 3600)
    thread = threading.Thread(target=scheduled_backup, daemon=True)
    thread.start()


@app.route('/backup/settings', methods=['GET', 'POST'])
@login_required
def backup_settings():
    global BACKUP_INTERVAL_HOURS, BACKUP_KEEP_COUNT
    if request.method == 'POST':
        BACKUP_INTERVAL_HOURS = int(request.form.get('interval', 24))
        BACKUP_KEEP_COUNT = int(request.form.get('keep', 3))
        flash(f'Einstellungen gespeichert: Intervall {BACKUP_INTERVAL_HOURS}h, Behalte {BACKUP_KEEP_COUNT} Backups')
        return redirect(url_for('backup_settings'))
    return render_template('backup_settings.html', interval=BACKUP_INTERVAL_HOURS, keep=BACKUP_KEEP_COUNT)


if __name__ == '__main__':
    init_db()
    metrics_thread = threading.Thread(target=background_metrics_recorder, daemon=True)
    metrics_thread.start()
    start_backup_scheduler()
    app.run(host='0.0.0.0', port=8080, debug=False, threaded=True)
