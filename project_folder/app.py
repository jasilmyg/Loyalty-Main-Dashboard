from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
import os
import sqlite3
import hashlib
import io
import csv
import logging

logging.basicConfig(filename='app_error.log', level=logging.DEBUG, 
                    format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'dev-secret-key-123'

# ─── Simple SQLite user store (no SQLAlchemy) ─────────────────────────────────
USERS_DB = os.path.join(os.path.dirname(__file__), 'instance', 'users.db')

def _get_users_conn():
    os.makedirs(os.path.dirname(USERS_DB), exist_ok=True)
    return sqlite3.connect(USERS_DB)

def _init_users_db():
    con = _get_users_conn()
    con.execute('''CREATE TABLE IF NOT EXISTS users
                   (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT, role TEXT)''')
    if not con.execute("SELECT 1 FROM users WHERE username='admin'").fetchone():
        pwd = hashlib.sha256(b'admin123').hexdigest()
        con.execute("INSERT INTO users(username,password,role) VALUES(?,?,?)",
                    ('admin', pwd, 'Admin'))
        con.commit()
    con.close()

def _check_login(username, password):
    con = _get_users_conn()
    pwd = hashlib.sha256(password.encode()).hexdigest()
    row = con.execute("SELECT id, role FROM users WHERE username=? AND password=?",
                      (username, pwd)).fetchone()
    con.close()
    return row

# ─── Lazy-loaded analytics ────────────────────────────────────────────────────
_analytics = None
def get_analytics():
    global _analytics
    if _analytics is None:
        from services.analytics import AnalyticsService
        _analytics = AnalyticsService('combined_data.db')
    return _analytics

# ─── Template context: inject current_user for all templates ─────────────────
@app.context_processor
def inject_current_user():
    """Make `current_user` available in every Jinja2 template.
    Mirrors the Flask-Login interface (current_user.role, current_user.username,
    current_user.is_authenticated) so layout.html works without Flask-Login."""
    class _User:
        def __init__(self, user_id, username, role):
            self.id = user_id
            self.username = username
            self.role = role
            self.is_authenticated = user_id is not None

    uid = session.get('user_id')
    role = session.get('role', '')
    username = session.get('username', '')

    # Lazy-fetch username from DB if not cached in session yet
    if uid and not username:
        try:
            con = _get_users_conn()
            row = con.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
            con.close()
            if row:
                username = row[0]
                session['username'] = username  # cache for subsequent requests
        except Exception:
            pass

    return dict(current_user=_User(uid, username, role))

# ─── Auth helpers ─────────────────────────────────────────────────────────────
def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def _get_filters():
    return {
        'start_date': request.args.get('start_date'),
        'end_date': request.args.get('end_date'),
        'branch': request.args.get('branch'),
        'rbm': request.args.get('rbm'),
        'bdm': request.args.get('bdm'),
        'staff': request.args.get('staff'),
        'customer_type': request.args.get('customer_type')
    }

# ─── Routes ───────────────────────────────────────────────────────────────────
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        row = _check_login(username, password)
        if row:
            session['user_id'] = row[0]
            session['role'] = row[1]
            session['username'] = username   # cache for context_processor
            return redirect(url_for('dashboard'))
        flash('Invalid username or password')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def dashboard():
    filters = _get_filters()
    analytics = get_analytics()
    sales_data = analytics.get_sales_overview(filters)
    customer_data = analytics.get_customer_analytics(filters)
    return render_template('index.html', sales=sales_data, customers=customer_data)

@app.route('/rfm')
@login_required
def rfm():
    analytics = get_analytics()
    data = analytics.perform_rfm_analysis(_get_filters())
    return render_template('rfm.html', rfm=data)

@app.route('/payments')
@login_required
def payments():
    analytics = get_analytics()
    data = analytics.get_payment_analytics(_get_filters())
    return render_template('payments.html', payments=data)

@app.route('/discounts')
@login_required
def discounts():
    analytics = get_analytics()
    data = analytics.get_discount_analysis(_get_filters())
    return render_template('discounts.html', discounts=data)

@app.route('/staff')
@login_required
def staff():
    analytics = get_analytics()
    data = analytics.get_staff_performance(_get_filters())
    return render_template('staff.html', staff=data)

@app.route('/branches')
@login_required
def branches():
    analytics = get_analytics()
    data = analytics.get_branch_performance(_get_filters())
    return render_template('branches.html', branches=data)

@app.route('/export/<format>')
@login_required
def export_data(format):
    if format == 'csv':
        conn = sqlite3.connect('combined_data.db')
        cur = conn.execute("SELECT * FROM sales_data LIMIT 50000")
        cols = [d[0] for d in cur.description]
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(cols)
        writer.writerows(cur.fetchall())
        conn.close()
        return output.getvalue(), 200, {
            'Content-Type': 'text/csv',
            'Content-Disposition': 'attachment; filename=sales_data.csv'
        }
    return "Invalid format", 400

# ─── Startup ──────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print("Initializing user database...")
    _init_users_db()
    print("Starting Flask server on http://0.0.0.0:5000 ...")
    app.run(debug=False, port=5000, host='0.0.0.0')
