from flask import Flask, render_template, flash, request, redirect, url_for, session, jsonify
import sqlite3
import os
from contextlib import contextmanager
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime, date, timedelta
import json

app = Flask(__name__)
app.secret_key = "StudyFlow_2025_secret_key"

# Veritabanı bağlantı ayarları
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR,"studyflow.db")

# Veritabanı bağlantısı için context manager
@contextmanager
def get_db_connection():
    connection = None
    try:
        connection = sqlite3.connect(DB_PATH)
        connection.row_factory = sqlite3.Row
        yield connection
    except sqlite3.Error as e:
        print(f"Veritabanı bağlantı hatası: {e}")
        raise
    finally:
        if connection:
            connection.close()

# Giriş yapmamış kullanıcıları korumak için dekoratör
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# ---------------------- ROUTES ----------------------

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')

        if not username or not email or not password:
            return "Lütfen tüm alanları doldurun.", 400

        password_hash = generate_password_hash(password)

        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                sql = "INSERT INTO users (username, email, password_hash) VALUES (?,?,?)"
                cursor.execute(sql, (username, email, password_hash))
                conn.commit()
            flash("Hesabınız başarıyla oluşturuldu! Lütfen giriş yapın.", "success")
            return redirect(url_for('login'))

        except sqlite3.IntegrityError :
            if e.args[0] == 1062:
                return "Bu kullanıcı adı veya e-posta zaten kullanımda.", 409
            return f"Veritabanı bütünlük hatası: {e}", 500
        except sqlite3.Error as e:
            return f"Veritabanı hatası: {e}", 500
        except Exception as e:
            return f"Beklenmedik hata: {e}", 500

    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = None
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                sql = "SELECT id, username, password_hash FROM users WHERE username = ?"
                cursor.execute(sql, (username,))
                user = cursor.fetchone()
        except sqlite3.Error as e:
            flash(f"Veritabanı hatası oluştu: {e}","danger")
            return render_template("login.html")

        if user and check_password_hash(user['password_hash'], password):
            session['logged_in'] = True
            session['user_id'] = user['id']
            session['username'] = user['username']
            flash("Giriş başarıyla tamamlandı!", "success")
            return redirect(url_for('dashboard'))
        else:
            flash("Kullanıcı adı veya parola hatalı!","danger")
            return render_template("login.html")

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    user_id = session['user_id']
    
    today = date.today()
    week_start_date = today - timedelta(days=today.weekday())
    next_monday = week_start_date + timedelta(weeks=1)

    # Değişkenleri başlat
    study_sessions_list = []
    active_plans = []
    grafik_verisi = []
    total_completed_tasks = 0
    kanban_dates = []
    kanban_counts = []
    weekly_total_minutes = 0
    weekly_total_hours = 0
    weekly_remaining_minutes = 0
    current_plan = None

    # Rütbe varsayılanları
    user_rank = "Yeni Başlayan"
    rank_color = "#B0B0C4" 

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # Otomatik Temizlik
            sql_clean_plans = "DELETE FROM weekly_plans WHERE user_id = ? AND week_start_date < ?"
            cursor.execute(sql_clean_plans, (user_id, week_start_date))

            # ESKİ KANBAN GÖREVLERİNİ SİL (30 Günlük Arşiv):
            sql_clean_tasks = "DELETE FROM kanban_tasks WHERE user_id = ? AND status = 'DONE' AND completed_at < DATE_SUB(NOW(), INTERVAL 30 DAY)"
            cursor.execute(sql_clean_tasks, (user_id,))

            # ÇALIŞMA SEANSLARINI SİL (60 Gün)
            sql_clean_sessions = "DELETE FROM study_sessions WHERE user_id = ? AND start_time < DATE_SUB(NOW(), INTERVAL 60 DAY)"
            cursor.execute(sql_clean_sessions, (user_id,))
            conn.commit()
            
            # 1. Haftalık toplam süre
            sql_weekly_sum = "SELECT SUM(duration_minutes) AS total_minutes FROM study_sessions WHERE user_id = ? AND start_time >= ?"
            cursor.execute(sql_weekly_sum, (user_id, week_start_date))
            result = cursor.fetchone()
            if result and result['total_minutes']:
                weekly_total_minutes = result['total_minutes']
                weekly_total_hours = weekly_total_minutes // 60
                weekly_remaining_minutes = weekly_total_minutes % 60

            # 2. Güncel plan
            sql_plans = "SELECT * FROM weekly_plans WHERE user_id = ? AND week_start_date >= ? ORDER BY week_start_date DESC"
            cursor.execute(sql_plans, (user_id, week_start_date))
            active_plans = cursor.fetchall()

            # 3. Tüm seanslar
            sql_all = "SELECT id, task_name, start_time, end_time, duration_minutes, notes FROM study_sessions WHERE user_id = ? ORDER BY start_time DESC"
            cursor.execute(sql_all, (user_id,))
            study_sessions_list = cursor.fetchall()

            # 4. Grafik Verisi
            sql_chart_data = "SELECT task_name AS kategori, SUM(duration_minutes) AS sure FROM study_sessions WHERE user_id = ? GROUP BY task_name ORDER BY sure DESC"
            cursor.execute(sql_chart_data, (user_id,))
            chart_data_from_db = cursor.fetchall()
            grafik_verisi = [{"kategori": row["kategori"], "süre": int(row["sure"])} for row in chart_data_from_db if row["sure"] > 0]

            # 5. Toplam Tamamlanan Görev
            sql_kanban_count = "SELECT COUNT(*) as completed_count FROM kanban_tasks WHERE user_id = ? AND status = 'DONE'"
            cursor.execute(sql_kanban_count, (user_id,))
            kanban_result = cursor.fetchone()
            total_completed_tasks = kanban_result['completed_count'] if kanban_result else 0

            # 6. Kanban Grafiği
            sql_kanban_chart = """
                SELECT DATE(completed_at) as gun, COUNT(*) as sayi 
                FROM kanban_tasks 
                WHERE user_id = ? AND status = 'DONE' AND completed_at >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
                GROUP BY DATE(completed_at)
                ORDER BY gun ASC
            """
            cursor.execute(sql_kanban_chart, (user_id,))
            kanban_chart_rows = cursor.fetchall()
            kanban_dates = [row['gun'].strftime('%d %b') for row in kanban_chart_rows]
            kanban_counts = [row['sayi'] for row in kanban_chart_rows]

            # --- 7. RÜTBE HESAPLAMA ---
            cursor.execute("SELECT SUM(duration_minutes) as total FROM study_sessions WHERE user_id = ?", (user_id,))
            res_total = cursor.fetchone()
            total_hours_worked = 0
            if res_total and res_total['total']:
                total_hours_worked = int(res_total['total']) // 60
            
            if total_hours_worked >= 100:
                user_rank = "StudyFlow Efsanesi 🏆"
                rank_color = "#d4af37" # Altın
            elif total_hours_worked >= 50:
                user_rank = "Usta Öğrenci 🥇"
                rank_color = "#FFD700" # Sarı
            elif total_hours_worked >= 20:
                user_rank = "Azimli Çalışkan 🥈"
                rank_color = "#C0C0C0" # Gümüş
            elif total_hours_worked >= 5:
                user_rank = "Yola Çıkan 🥉"
                rank_color = "#CD7F32" # Bronz

    except sqlite3.Error as e:
        flash(f"Veritabanı hatası: {e}", "error")
    
    DEFAULT_WEEKLY_GOAL_MINUTES = 15 * 60
    progress_percentage = min(100, round((weekly_total_minutes / DEFAULT_WEEKLY_GOAL_MINUTES) * 100)) if DEFAULT_WEEKLY_GOAL_MINUTES > 0 else 0

    return render_template('dashboard.html',
                           sessions=study_sessions_list,
                           weekly_total_minutes=weekly_total_minutes,
                           weekly_total_hours=weekly_total_hours,
                           weekly_remaining_minutes=weekly_remaining_minutes,
                           week_start_date=week_start_date,
                           current_plan=current_plan,
                           active_plans=active_plans,
                           next_monday=next_monday,
                           progress_percentage=progress_percentage,
                           default_goal_hours=DEFAULT_WEEKLY_GOAL_MINUTES / 60,
                           grafik_datasi=grafik_verisi,
                           total_completed_tasks=total_completed_tasks,
                           kanban_dates=kanban_dates,
                           kanban_counts=kanban_counts,
                           user_rank=user_rank,
                           rank_color=rank_color)

@app.route('/add_session', methods=['GET', 'POST'])
@login_required
def add_session():
    if request.method == 'POST':
        task_name = request.form.get('task_name')
        start_time_str = request.form.get('start_time')
        end_time_str = request.form.get('end_time')
        notes = request.form.get('notes')

        try:
            start_time = datetime.fromisoformat(start_time_str)
            end_time = datetime.fromisoformat(end_time_str)
            duration = end_time - start_time
            duration_minutes = int(duration.total_seconds() / 60)
        except ValueError:
            flash("Bitiş saati, başlangıç saatinden sonra olmalıdır.", "error")
            return "Hata: Geçersiz tarih/saat formatı.", 400

        user_id = session['user_id']
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                sql = "INSERT INTO study_sessions (user_id, task_name, start_time, end_time, duration_minutes, notes) VALUES (?,?,?,?,?,?)"
                cursor.execute(sql, (user_id, task_name, start_time, end_time, duration_minutes, notes))
                conn.commit()
            flash("Çalışma seansı başarıyla kaydedildi!", "success")
            return redirect(url_for('dashboard'))
        except sqlite3.Error as e:
            return f"Veritabanı hatası: {e}", 500
        except Exception as e:
            return f"Beklenmedik hata: {e}", 500

    return render_template('add_session.html')


@app.route('/plan_week', methods=['GET', 'POST'])
@login_required
def plan_week():
    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    next_monday = this_monday + timedelta(weeks=1)

    if request.method == 'POST':
        goals = request.form.get('goals')
        user_id = session['user_id']
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                sql = "INSERT INTO weekly_plans (user_id, week_start_date, goals, is_completed) VALUES (?,?,?,0)"
                cursor.execute(sql, (user_id, next_monday, goals))
                conn.commit()
            return redirect(url_for('dashboard'))
        except sqlite3.Error as e:
            return f"Veritabanı hatası: {e}", 500
        except Exception as e:
            return f"Beklenmedik hata: {e}", 500

    return render_template('plan_week.html', next_monday=next_monday)


# ---------------- Kanban ve Pomodoro ----------------

@app.route('/pomodoro')
@login_required
def pomodoro_page():
    return render_template('pomodoro.html')

@app.route('/kanban')
@login_required
def kanban_board():
    user_id = session.get('user_id')
    tasks_list = []
    
    if not user_id:
        return redirect(url_for('login'))

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Mantık: Durumu 'DONE' olmayanları getir VEYA Durumu 'DONE' olup bitiş saati son 24 saat içinde olanları getir.
            sql = """
                SELECT * FROM kanban_tasks 
                WHERE user_id = ?
                AND (
                    status != 'DONE' 
                    OR (status = 'DONE' AND completed_at > datetiem('now2, '-1 day'))
                )
                ORDER BY created_at DESC
            """
            cursor.execute(sql, (user_id,))
            tasks_list = cursor.fetchall()
    except Exception as e:
        print(f"Hata: {e}")
    
    return render_template('kanban.html', tasks=tasks_list)

@app.route('/api/tasks/add', methods=['POST'])
@login_required
def add_task():
    user_id = session['user_id']
    data = request.get_json()
    
    title = data.get('title')
    description = data.get('description', '')
    priority = data.get('priority', 'MEDIUM') 
    status = 'TODO'
    
    if not title:
        return jsonify({'success': False, 'message': 'Başlık zorunludur'}), 400

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            sql = "INSERT INTO kanban_tasks (user_id, title, description, status, priority) VALUES (?, ?, ?, ?, ?)"
            cursor.execute(sql, (user_id, title, description, status, priority))
            conn.commit()
            
            new_task_id = cursor.lastrowid
            
            new_task = {
                'id': new_task_id,
                'title': title,
                'description': description,
                'status': status,
                'priority': priority
            }
            return jsonify({'success': True, 'message': 'Görev eklendi', 'task': new_task}), 201
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/task/status', methods=['POST'])
@login_required
def update_task_status():
    user_id = session['user_id']
    data = request.get_json()
    
    try:
        task_id = int(data.get('task_id'))
    except (ValueError, TypeError):
        return jsonify({'success': False, 'message': 'Geçersiz ID'}), 400
        
    new_status = data.get('status')
    valid_statuses = ['TODO', 'IN_PROGRESS', 'DONE']
    if new_status not in valid_statuses:
        return jsonify({'success': False, 'message': 'Geçersiz durum'}), 400

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            if new_status == 'DONE':
                sql = "UPDATE kanban_tasks SET status = ?, completed_at = NOW() WHERE id = ? AND user_id = ?"
            else:
                sql = "UPDATE kanban_tasks SET status = ?, completed_at = NULL WHERE id = ? AND user_id = ?"
            
            cursor.execute(sql, (new_status, task_id, user_id))
            conn.commit()
            
            if cursor.rowcount > 0:
                return jsonify({'success': True, 'message': 'Durum güncellendi'}), 200
            else:
                return jsonify({'success': False, 'message': 'Görev bulunamadı'}), 404
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# -------------------- Seans Düzenleme ve Silme --------------------

@app.route('/edit_session/<int:session_id>', methods=['GET','POST'])
@login_required
def edit_session(session_id):
    with get_db_connection() as conn:
        cursor = conn.cursor(sqlite3.cursors.DictCursor)
        cursor.execute("SELECT * FROM study_sessions WHERE id = ? AND user_id = ?", (session_id, session['user_id']))
        session_data = cursor.fetchone()
        if not session_data:
            flash("Seans bulunamadı veya yetkiniz yok.", "error")
            return redirect(url_for('dashboard'))

        if request.method == 'POST':
            try:
                task_name = request.form['task_name']
                start_time = datetime.strptime(request.form['start_time'], '%Y-%m-%dT%H:%M')
                end_time = datetime.strptime(request.form['end_time'], '%Y-%m-%dT%H:%M')
                duration_minutes = int((end_time - start_time).total_seconds() / 60)
                if duration_minutes <= 0:
                    flash("Bitiş saati, başlangıç saatinden sonra olmalıdır.", "error")
                    session_data.update(request.form)
                    return render_template('edit_session.html', study_session=session_data)
                sql = "UPDATE study_sessions SET task_name=?, start_time=?, end_time=?, duration_minutes=?, notes=? WHERE id=? AND user_id=?"
                cursor.execute(sql, (task_name, start_time, end_time, duration_minutes, request.form.get('notes',''), session_id, session['user_id']))
                conn.commit()
                flash("Çalışma seansı başarıyla güncellendi!", "success")
                return redirect(url_for('dashboard'))
            except Exception as e:
                conn.rollback()
                flash(f"Güncelleme hatası: {e}", "error")
                session_data.update(request.form)
                return render_template('edit_session.html', study_session=session_data)

        session_data['start_time_formatted'] = session_data['start_time'].strftime('%Y-%m-%dT%H:%M')
        session_data['end_time_formatted'] = session_data['end_time'].strftime('%Y-%m-%dT%H:%M')
        return render_template('edit_session.html', study_session=session_data)


@app.route('/delete_session/<int:session_id>', methods=['POST'])
@login_required
def delete_session(session_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM study_sessions WHERE id = ? AND user_id = ?", (session_id, session['user_id']))
        conn.commit()
        flash("Seans silindi." if cursor.rowcount>0 else "Seans bulunamadı veya yetkiniz yok.", "success" if cursor.rowcount>0 else "error")
    return redirect(url_for('dashboard'))

@app.route('/completed_tasks')
@login_required
def completed_tasks():
    user_id = session['user_id']
    completed_list = []

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # 1. TEMİZLİK
            sql_cleanup = "DELETE FROM kanban_tasks WHERE user_id = ? AND status = 'DONE' AND completed_at < DATE_SUB(NOW(), INTERVAL 30 DAY)"
            cursor.execute(sql_cleanup, (user_id,))
            conn.commit()

            # 2. LİSTELEME
            sql_history = """
                SELECT * FROM kanban_tasks 
                WHERE user_id = ?
                AND status = 'DONE' 
                AND completed_at >= DATE_SUB(NOW(), INTERVAL 14 DAY)
                ORDER BY completed_at DESC
            """
            cursor.execute(sql_history, (user_id,))
            completed_list = cursor.fetchall()
            
    except Exception as e:
        print(f"Hata: {e}")

    return render_template('completed_tasks.html', tasks=completed_list)

if __name__ == '__main__':
    app.run(debug=True)