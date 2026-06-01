import os
from datetime import datetime, date, timedelta
from functools import wraps

import psycopg as psycopg2
import psycopg.rows
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session, g

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'Church Savings')

DATABASE_URL = os.environ.get(
    'DATABASE_URL',
    'postgresql://postgres:postgres@localhost:5432/church_savings'
)


# ============ DATABASE CONNECTION ============

def get_db():
    """Get a request-scoped database connection."""
    if 'db' not in g:
        g.db = psycopg2.connect(DATABASE_URL, autocommit=False)
    return g.db


@app.teardown_appcontext
def close_db(exception):
    """Close the database connection at the end of the request."""
    db = g.pop('db', None)
    if db is not None:
        db.close()


def get_cursor():
    """Get a cursor that returns rows as dictionaries."""
    return get_db().cursor(row_factory=psycopg.rows.dict_row)


# ============ LOGIN DECORATORS ============
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            flash('Please login to access this page', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)

    return decorated_function


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            flash('Please login to access this page', 'warning')
            return redirect(url_for('login'))
        if session.get('role') != 'admin':
            flash('❌ Access denied! Admin privileges required.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)

    return decorated_function


def collector_or_admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            flash('Please login first', 'warning')
            return redirect(url_for('login'))
        if session.get('role') not in ['admin', 'collector']:
            flash('❌ Access denied! Collector or Admin privileges required.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)

    return decorated_function


# ============ HELPER FUNCTIONS ============
def can_edit_within_hour(added_at):
    if session.get('role') == 'admin':
        return True
    if session.get('role') == 'collector':
        if isinstance(added_at, str):
            added_at = datetime.strptime(added_at, '%Y-%m-%d %H:%M:%S')
        time_diff = datetime.now() - added_at
        return time_diff < timedelta(hours=1)
    return False


def log_action(username, action, entity_type, entity_id, details):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO audit_logs (username, action, entity_type, entity_id, details) VALUES (%s, %s, %s, %s, %s)',
            (username, action, entity_type, str(entity_id), details)
        )
        conn.commit()
        cur.close()
    except Exception:
        pass


# ============ DATABASE INITIALIZATION ============

def init_db():
    try:
        conn = get_db()
        cur = conn.cursor()

        cur.execute('''
            CREATE TABLE IF NOT EXISTS members (
                id SERIAL PRIMARY KEY,
                member_id VARCHAR(50) UNIQUE NOT NULL,
                name VARCHAR(255) NOT NULL,
                address TEXT NOT NULL,
                contact VARCHAR(20),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS savings (
                id SERIAL PRIMARY KEY,
                member_id VARCHAR(50),
                date DATE NOT NULL,
                amount NUMERIC(10, 2) NOT NULL,
                added_by VARCHAR(100),
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_edited_at TIMESTAMP,
                last_edited_by VARCHAR(100),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (member_id) REFERENCES members(member_id) ON DELETE CASCADE
            )
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS loans (
                id SERIAL PRIMARY KEY,
                member_id VARCHAR(50),
                date DATE NOT NULL,
                amount NUMERIC(10, 2) NOT NULL,
                interest_rate NUMERIC(5, 2) NOT NULL,
                interest_amount NUMERIC(10, 2) DEFAULT 0,
                added_by VARCHAR(100),
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_edited_at TIMESTAMP,
                last_edited_by VARCHAR(100),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (member_id) REFERENCES members(member_id) ON DELETE CASCADE
            )
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS loan_repayments (
                id SERIAL PRIMARY KEY,
                loan_id INT,
                date DATE NOT NULL,
                principal_paid NUMERIC(10, 2) DEFAULT 0,
                interest_paid NUMERIC(10, 2) DEFAULT 0,
                total_amount NUMERIC(10, 2) NOT NULL,
                added_by VARCHAR(100),
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (loan_id) REFERENCES loans(id) ON DELETE CASCADE
            )
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(100) UNIQUE NOT NULL,
                password VARCHAR(255) NOT NULL,
                is_admin BOOLEAN DEFAULT FALSE,
                role VARCHAR(20) DEFAULT 'member',
                member_id VARCHAR(50),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS audit_logs (
                id SERIAL PRIMARY KEY,
                username VARCHAR(100),
                action VARCHAR(50),
                entity_type VARCHAR(50),
                entity_id VARCHAR(50),
                details TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS user_preferences (
                id SERIAL PRIMARY KEY,
                username VARCHAR(100) UNIQUE NOT NULL,
                theme VARCHAR(20) DEFAULT 'light',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cur.execute("SELECT id FROM users WHERE username = 'Admin'")
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO users (username, password, is_admin, role) VALUES (%s, %s, %s, %s)",
                ['Admin', 'z', True, 'admin']
            )

        cur.execute("SELECT id FROM users WHERE username = 'collector'")
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO users (username, password, is_admin, role) VALUES (%s, %s, %s, %s)",
                ['collector', 'z', False, 'collector']
            )

        conn.commit()
        cur.close()
        print("Database tables created successfully!")
    except Exception as e:
        print(f"Error creating database tables: {e}")


# ============ LOGIN ROUTES ============
@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'logged_in' in session:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip()

        cur = get_cursor()

        # Check users table first (Admin / collector)
        cur.execute('SELECT * FROM users WHERE username = %s', [username])
        user = cur.fetchone()

        if user and password == user['password']:
            session['logged_in'] = True
            session['username'] = user['username']
            session['is_admin'] = user.get('is_admin', False)
            session['role'] = user.get('role', 'member')
            session['member_id'] = user.get('member_id')
            log_action(username, 'login', 'user', username, 'User logged in')
            if session['role'] == 'admin':
                flash('Welcome Admin! You have full access.', 'success')
            elif session['role'] == 'collector':
                flash('Welcome Collector! You can add/edit data (1-hour edit window).', 'success')
            else:
                flash('Welcome!', 'success')
            cur.close()
            return redirect(url_for('index'))

        # Login by phone number (contact) — fetch ALL members with this phone
        cur.execute('SELECT * FROM members WHERE contact = %s ORDER BY name', [username])
        family_members = cur.fetchall()

        if family_members:
            # Determine password to check: stored user account password, or phone number as default
            cur.execute('SELECT * FROM users WHERE username = %s', [username])
            user_account = cur.fetchone()

            correct_password = user_account['password'] if user_account else username

            if password == correct_password:
                # Auto-create user account if it doesn't exist yet
                if not user_account:
                    cur.execute(
                        'INSERT INTO users (username, password, is_admin, role, member_id) VALUES (%s, %s, %s, %s, %s)',
                        [username, username, False, 'member', family_members[0]['member_id']]
                    )
                    cur.connection.commit()

                names = ', '.join([m['name'] for m in family_members])
                member_ids = [m['member_id'] for m in family_members]

                session['logged_in'] = True
                session['username'] = names
                session['contact'] = username
                session['member_ids'] = member_ids
                session['member_id'] = family_members[0]['member_id']
                session['is_admin'] = False
                session['role'] = 'member'

                log_action(names, 'login', 'member', username, f'Member(s) logged in: {names}')
                flash(f'Welcome {names}!', 'success')
                cur.close()
                return redirect(url_for('index'))

        cur.close()
        flash('Invalid username or password', 'danger')

    return render_template('login.html')


@app.route('/logout')
def logout():
    username = session.get('username')
    log_action(username, 'logout', 'user', username, 'User logged out')
    session.clear()
    flash('Logged out successfully', 'info')
    return redirect(url_for('login'))


@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        if 'theme' in request.form:
            theme = request.form['theme']
            username = session.get('username')

            conn = get_db()
            cur = conn.cursor()
            cur.execute('SELECT id FROM user_preferences WHERE username = %s', [username])
            existing = cur.fetchone()

            if existing:
                cur.execute(
                    'UPDATE user_preferences SET theme = %s, updated_at = NOW() WHERE username = %s',
                    [theme, username]
                )
            else:
                cur.execute(
                    'INSERT INTO user_preferences (username, theme) VALUES (%s, %s)',
                    [username, theme]
                )

            conn.commit()
            cur.close()
            return jsonify({'status': 'success', 'message': 'Theme updated'})

        cur = get_cursor()
        current_pwd = request.form['current_password']
        new_pwd = request.form['new_password']
        confirm_pwd = request.form['confirm_password']

        if new_pwd != confirm_pwd:
            flash('Passwords do not match', 'danger')
            return redirect(url_for('settings'))

        # Determine the login username for password lookup
        role = session.get('role')
        if role == 'admin':
            login_username = 'Admin'
        elif role == 'collector':
            login_username = 'collector'
        else:
            login_username = session.get('contact', session.get('member_id'))

        cur.execute('SELECT * FROM users WHERE username = %s', [login_username])
        user = cur.fetchone()

        if user and current_pwd == user['password']:
            cur.execute('UPDATE users SET password = %s WHERE username = %s', [new_pwd, login_username])
            cur.connection.commit()
            flash('Password updated successfully!', 'success')
        else:
            flash('Current password incorrect', 'danger')
        cur.close()

    return render_template('settings.html')


# ============ END LOGIN ROUTES ============

@app.route('/')
@login_required
def index():
    cur = get_cursor()

    role = session.get('role')
    member_id = session.get('member_id')

    if role in ['admin', 'collector']:
        cur.execute('SELECT COUNT(*) as count FROM members')
        total_members = cur.fetchone()['count']
        cur.execute('SELECT COALESCE(SUM(amount), 0) as total FROM savings')
        total_savings = float(cur.fetchone()['total'])
        cur.execute('SELECT COALESCE(SUM(amount), 0) as total FROM loans')
        total_loans = float(cur.fetchone()['total'])
        cur.execute('SELECT COALESCE(SUM(interest_paid), 0) as total FROM loan_repayments')
        total_profit = float(cur.fetchone()['total'])

        search_query = request.args.get('search', '')
        if search_query:
            cur.execute('SELECT * FROM members WHERE member_id LIKE %s OR name LIKE %s ORDER BY name',
                        (f'%{search_query}%', f'%{search_query}%'))
        else:
            cur.execute('SELECT * FROM members ORDER BY name')
        members = cur.fetchall()

        for member in members:
            cur.execute('SELECT COALESCE(SUM(amount), 0) as total FROM savings WHERE member_id = %s',
                        [member['member_id']])
            member['total_savings'] = float(cur.fetchone()['total'])
            cur.execute('SELECT COALESCE(SUM(amount), 0) as total FROM loans WHERE member_id = %s',
                        [member['member_id']])
            member['total_loans'] = float(cur.fetchone()['total'])

    else:
        # MEMBER: show all family members sharing same phone number
        member_ids = session.get('member_ids', [])
        if not member_ids:
            member_ids = [member_id] if member_id else []

        if member_ids:
            placeholders = ','.join(['%s'] * len(member_ids))
            cur.execute(f'SELECT COALESCE(SUM(amount), 0) as total FROM savings WHERE member_id IN ({placeholders})', member_ids)
            total_savings = float(cur.fetchone()['total'])
            cur.execute(f'SELECT COALESCE(SUM(amount), 0) as total FROM loans WHERE member_id IN ({placeholders})', member_ids)
            total_loans = float(cur.fetchone()['total'])
            cur.execute(f'SELECT * FROM members WHERE member_id IN ({placeholders}) ORDER BY name', member_ids)
            members = cur.fetchall()
        else:
            total_savings = 0.0
            total_loans = 0.0
            members = []

        total_profit = 0
        total_members = len(members)

        for member in members:
            cur.execute('SELECT COALESCE(SUM(amount), 0) as total FROM savings WHERE member_id = %s', [member['member_id']])
            member['total_savings'] = float(cur.fetchone()['total'])
            cur.execute('SELECT COALESCE(SUM(amount), 0) as total FROM loans WHERE member_id = %s', [member['member_id']])
            member['total_loans'] = float(cur.fetchone()['total'])

        search_query = ''

    cur.close()
    return render_template('index.html', total_members=total_members, total_savings=total_savings,
                           total_loans=total_loans, total_profit=total_profit, members=members,
                           search_query=search_query, role=role)


@app.route('/member/<member_id>')
@login_required
def view_member(member_id):
    role = session.get('role')

    # Members can only view profiles belonging to their family (same phone)
    if role == 'member':
        allowed_ids = session.get('member_ids', [session.get('member_id')])
        if member_id not in allowed_ids:
            flash('Access denied! You can only view your own profile.', 'danger')
            return redirect(url_for('index'))

    cur = get_cursor()
    cur.execute('SELECT * FROM members WHERE member_id = %s', [member_id])
    member = cur.fetchone()
    if not member:
        flash('Member not found', 'danger')
        return redirect(url_for('index'))

    cur.execute('SELECT * FROM savings WHERE member_id = %s ORDER BY date DESC', [member_id])
    savings = cur.fetchall()
    total_savings = sum(float(s['amount']) for s in savings)

    cur.execute('SELECT * FROM loans WHERE member_id = %s ORDER BY date DESC', [member_id])
    loans = cur.fetchall()
    for loan in loans:
        loan['amount'] = float(loan['amount'])
        cur.execute('SELECT * FROM loan_repayments WHERE loan_id = %s ORDER BY date DESC', [loan['id']])
        loan['repayments'] = cur.fetchall()
        loan['principal_repaid'] = round(sum(float(r.get('principal_paid', 0) or 0) for r in loan['repayments']), 2)
        loan['interest_repaid'] = round(sum(float(r.get('interest_paid', 0) or 0) for r in loan['repayments']), 2)
        loan['total_repayments'] = round(loan['principal_repaid'] + loan['interest_repaid'], 2)
        loan['remaining'] = round(loan['amount'] - loan['principal_repaid'], 2)

    total_loans = sum(float(l['amount']) for l in loans)
    cur.close()
    return render_template('member_profile.html', member=member, savings=savings, total_savings=total_savings,
                           loans=loans, total_loans=total_loans, role=role)


@app.route('/add_member', methods=['GET', 'POST'])
@collector_or_admin_required
def add_member():
    if request.method == 'POST':
        member_id = request.form['member_id'].strip()
        name = request.form['name'].strip()
        address = request.form['address'].strip()
        contact = request.form.get('contact', '').strip()

        if not member_id or not name or not address or not contact:
            flash('Please fill in all required fields including phone number', 'danger')
            return redirect(url_for('add_member'))

        conn = get_db()
        cur = conn.cursor()
        cur.execute('SELECT member_id FROM members WHERE member_id = %s', [member_id])
        if cur.fetchone():
            flash('Member ID already exists!', 'danger')
            cur.close()
            return redirect(url_for('add_member'))

        try:
            added_by = session.get('username')
            cur.execute('INSERT INTO members (member_id, name, address, contact) VALUES (%s, %s, %s, %s)',
                        (member_id, name, address, contact))
            conn.commit()
            log_action(added_by, 'add', 'member', member_id, f'Added member: {name}')
            flash('Member added successfully!', 'success')
            cur.close()
            return redirect(url_for('index'))
        except Exception as e:
            flash(f'Error: {str(e)}', 'danger')
            cur.close()
            return redirect(url_for('add_member'))

    return render_template('add_member.html')


@app.route('/edit_member/<member_id>', methods=['GET', 'POST'])
@admin_required
def edit_member(member_id):
    conn = get_db()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    if request.method == 'POST':
        new_member_id = request.form['member_id'].strip()
        name = request.form['name'].strip()
        address = request.form['address'].strip()
        contact = request.form.get('contact', '').strip()
        if not new_member_id or not name or not address:
            flash('Please fill in all required fields', 'danger')
            return redirect(url_for('edit_member', member_id=member_id))
        try:
            if new_member_id != member_id:
                cur.execute('SELECT member_id FROM members WHERE member_id = %s', [new_member_id])
                if cur.fetchone():
                    flash('Member ID already exists!', 'danger')
                    cur.close()
                    return redirect(url_for('edit_member', member_id=member_id))
            cur.execute('UPDATE members SET member_id = %s, name = %s, address = %s, contact = %s WHERE member_id = %s',
                        (new_member_id, name, address, contact, member_id))
            conn.commit()
            flash('Member updated successfully!', 'success')
            cur.close()
            return redirect(url_for('view_member', member_id=new_member_id))
        except Exception as e:
            flash(f'Error: {str(e)}', 'danger')
            cur.close()
            return redirect(url_for('edit_member', member_id=member_id))
    cur.execute('SELECT * FROM members WHERE member_id = %s', [member_id])
    member = cur.fetchone()
    cur.close()
    if not member:
        flash('Member not found', 'danger')
        return redirect(url_for('index'))
    return render_template('edit_member.html', member=member)


@app.route('/delete_member/<member_id>')
@admin_required
def delete_member(member_id):
    conn = get_db()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    cur.execute('DELETE FROM members WHERE member_id = %s', [member_id])
    conn.commit()
    cur.close()
    flash('Member deleted successfully!', 'success')
    return redirect(url_for('index'))


@app.route('/add_savings/<member_id>', methods=['GET', 'POST'])
@collector_or_admin_required
def add_savings(member_id):
    if request.method == 'POST':
        try:
            date = request.form.get('date', '').strip()
            amount = request.form.get('amount', '').strip()

            if not date or not amount:
                flash('Please fill in all required fields', 'danger')
                return redirect(url_for('add_savings', member_id=member_id))

            try:
                amount_float = float(amount)
                if amount_float <= 0:
                    flash('Amount must be greater than zero', 'danger')
                    return redirect(url_for('add_savings', member_id=member_id))
            except ValueError:
                flash('Invalid amount format', 'danger')
                return redirect(url_for('add_savings', member_id=member_id))

            conn = get_db()
            cur = conn.cursor()
            added_by = session.get('username')
            cur.execute(
                'INSERT INTO savings (member_id, date, amount, added_by, added_at) VALUES (%s, %s, %s, %s, NOW())',
                (member_id, date, amount_float, added_by))
            conn.commit()
            log_action(added_by, 'add', 'savings', member_id, f'Added savings: ₹{amount_float}')
            cur.close()
            flash('✅ Savings added successfully!', 'success')
            return redirect(url_for('view_member', member_id=member_id))
        except Exception as e:
            flash(f'Error: {str(e)}', 'danger')
            return redirect(url_for('add_savings', member_id=member_id))

    cur = get_cursor()
    cur.execute('SELECT * FROM members WHERE member_id = %s', [member_id])
    member = cur.fetchone()
    cur.close()
    if not member:
        flash('Member not found', 'danger')
        return redirect(url_for('index'))
    return render_template('add_savings.html', member=member, today=datetime.now().strftime('%Y-%m-%d'))


@app.route('/add_loan/<member_id>', methods=['GET', 'POST'])
@collector_or_admin_required
def add_loan(member_id):
    if request.method == 'POST':
        try:
            date = request.form.get('date', '').strip()
            amount = request.form.get('amount', '').strip()
            if not date or not amount:
                flash('Please fill in all required fields', 'danger')
                return redirect(url_for('add_loan', member_id=member_id))

            try:
                amount_float = float(amount)
                if amount_float <= 0:
                    flash('Loan amount must be greater than zero', 'danger')
                    return redirect(url_for('add_loan', member_id=member_id))
            except ValueError:
                flash('Invalid number format', 'danger')
                return redirect(url_for('add_loan', member_id=member_id))

            conn = get_db()
            cur = conn.cursor()
            added_by = session.get('username')
            cur.execute(
                'INSERT INTO loans (member_id, date, amount, interest_rate, interest_amount, added_by, added_at) VALUES (%s, %s, %s, %s, 0, %s, NOW())',
                (member_id, date, amount_float, 0, added_by))
            conn.commit()
            log_action(added_by, 'add', 'loan', member_id, f'Added loan: ₹{amount_float}')
            cur.close()
            flash('✅ Loan added successfully!', 'success')
            return redirect(url_for('view_member', member_id=member_id))
        except Exception as e:
            flash(f'Error: {str(e)}', 'danger')
            return redirect(url_for('add_loan', member_id=member_id))

    cur = get_cursor()
    cur.execute('SELECT * FROM members WHERE member_id = %s', [member_id])
    member = cur.fetchone()
    cur.close()
    if not member:
        flash('Member not found', 'danger')
        return redirect(url_for('index'))
    return render_template('add_loan.html', member=member, today=datetime.now().strftime('%Y-%m-%d'))


@app.route('/edit_loan/<int:loan_id>', methods=['GET', 'POST'])
@collector_or_admin_required
def edit_loan(loan_id):
    cur = get_cursor()
    cur.execute('SELECT * FROM loans WHERE id = %s', [loan_id])
    loan = cur.fetchone()

    if not loan:
        flash('Loan not found', 'danger')
        cur.close()
        return redirect(url_for('index'))

    if not can_edit_within_hour(loan.get('added_at')):
        flash('Cannot edit! Collector can only edit within 1 hour of adding data.', 'warning')
        cur.close()
        return redirect(url_for('view_member', member_id=loan['member_id']))

    if request.method == 'POST':
        try:
            date = request.form.get('date', '').strip()
            amount = request.form.get('amount', '').strip()
            if not date or not amount:
                flash('Please fill in all required fields', 'danger')
                return redirect(url_for('edit_loan', loan_id=loan_id))

            try:
                amount_float = float(amount)
                if amount_float <= 0:
                    flash('Loan amount must be greater than zero', 'danger')
                    return redirect(url_for('edit_loan', loan_id=loan_id))
            except ValueError:
                flash('Invalid number format', 'danger')
                return redirect(url_for('edit_loan', loan_id=loan_id))

            member_id = loan['member_id']
            edited_by = session.get('username')
            cur.execute(
                'UPDATE loans SET date = %s, amount = %s, last_edited_at = NOW(), last_edited_by = %s WHERE id = %s',
                (date, amount_float, edited_by, loan_id))
            get_db().commit()
            log_action(edited_by, 'edit', 'loan', loan_id, f'Edited loan to: ₹{amount_float}')
            cur.close()
            flash('Loan updated successfully!', 'success')
            return redirect(url_for('view_member', member_id=member_id))
        except Exception as e:
            flash(f'Error: {str(e)}', 'danger')
            cur.close()
            return redirect(url_for('edit_loan', loan_id=loan_id))

    cur.close()
    return render_template('edit_loan.html', loan=loan)


@app.route('/delete_loan/<int:loan_id>')
@admin_required
def delete_loan(loan_id):
    conn = get_db()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    cur.execute('SELECT member_id FROM loans WHERE id = %s', [loan_id])
    result = cur.fetchone()
    if result:
        member_id = result['member_id']
        cur.execute('DELETE FROM loans WHERE id = %s', [loan_id])
        conn.commit()
        flash('Loan deleted successfully!', 'success')
        cur.close()
        return redirect(url_for('view_member', member_id=member_id))
    cur.close()
    flash('Loan not found', 'danger')
    return redirect(url_for('index'))


@app.route('/edit_savings/<int:savings_id>', methods=['GET', 'POST'])
@collector_or_admin_required
def edit_savings(savings_id):
    conn = get_db()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    cur.execute('SELECT * FROM savings WHERE id = %s', [savings_id])
    saving = cur.fetchone()

    if not saving:
        flash('Savings record not found', 'danger')
        cur.close()
        return redirect(url_for('index'))

    if not can_edit_within_hour(saving.get('added_at')):
        flash('Cannot edit! Collector can only edit within 1 hour of adding data.', 'warning')
        cur.close()
        return redirect(url_for('view_member', member_id=saving['member_id']))

    if request.method == 'POST':
        try:
            date = request.form.get('date', '').strip()
            amount = request.form.get('amount', '').strip()

            if not date or not amount:
                flash('Please fill in all required fields', 'danger')
                return redirect(url_for('edit_savings', savings_id=savings_id))

            try:
                amount_float = float(amount)
                if amount_float <= 0:
                    flash('Amount must be greater than zero', 'danger')
                    return redirect(url_for('edit_savings', savings_id=savings_id))
            except ValueError:
                flash('Invalid amount format', 'danger')
                return redirect(url_for('edit_savings', savings_id=savings_id))

            member_id = saving['member_id']
            edited_by = session.get('username')
            cur.execute(
                'UPDATE savings SET date = %s, amount = %s, last_edited_at = NOW(), last_edited_by = %s WHERE id = %s',
                (date, amount_float, edited_by, savings_id))
            conn.commit()
            log_action(edited_by, 'edit', 'savings', savings_id, f'Edited savings to: ₹{amount_float}')
            cur.close()
            flash('Savings updated successfully!', 'success')
            return redirect(url_for('view_member', member_id=member_id))
        except Exception as e:
            flash(f'Error: {str(e)}', 'danger')
            cur.close()
            return redirect(url_for('edit_savings', savings_id=savings_id))

    cur.close()
    return render_template('edit_savings.html', saving=saving)


@app.route('/delete_savings/<int:savings_id>')
@admin_required
def delete_savings(savings_id):
    conn = get_db()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    cur.execute('SELECT member_id FROM savings WHERE id = %s', [savings_id])
    result = cur.fetchone()
    if result:
        member_id = result['member_id']
        cur.execute('DELETE FROM savings WHERE id = %s', [savings_id])
        conn.commit()
        flash('Savings deleted successfully!', 'success')
        cur.close()
        return redirect(url_for('view_member', member_id=member_id))
    cur.close()
    flash('Savings record not found', 'danger')
    return redirect(url_for('index'))


@app.route('/add_repayment/<int:loan_id>', methods=['GET', 'POST'])
@collector_or_admin_required
def add_repayment(loan_id):
    if request.method == 'POST':
        try:
            date = request.form.get('date', '')
            principal_paid = request.form.get('principal_paid', '0').strip()
            interest_paid = request.form.get('interest_paid', '0').strip()

            principal_paid = round(float(principal_paid), 2) if principal_paid else 0
            interest_paid = round(float(interest_paid), 2) if interest_paid else 0
            total_amount = round(principal_paid + interest_paid, 2)

            if not date or total_amount <= 0:
                flash('Please enter a date and a payment amount', 'danger')
                return redirect(url_for('add_repayment', loan_id=loan_id))

            conn = get_db()
            cur = conn.cursor(row_factory=psycopg.rows.dict_row)
            cur.execute('SELECT member_id FROM loans WHERE id = %s', [loan_id])
            loan = cur.fetchone()

            if not loan:
                flash('Loan not found', 'danger')
                cur.close()
                return redirect(url_for('index'))

            added_by = session.get('username')
            cur.execute(
                'INSERT INTO loan_repayments (loan_id, date, principal_paid, interest_paid, total_amount, added_by, added_at) VALUES (%s, %s, %s, %s, %s, %s, NOW())',
                (loan_id, date, principal_paid, interest_paid, total_amount, added_by))
            conn.commit()
            log_action(added_by, 'add', 'repayment', loan_id, f'Added repayment: ₹{total_amount}')
            cur.close()
            flash('Payment recorded successfully!', 'success')
            return redirect(url_for('view_member', member_id=loan['member_id']))

        except ValueError:
            flash('Invalid number format. Please enter valid amounts.', 'danger')
            return redirect(url_for('add_repayment', loan_id=loan_id))
        except Exception as e:
            flash(f'Error: {str(e)}', 'danger')
            return redirect(url_for('add_repayment', loan_id=loan_id))

    cur = get_cursor()
    cur.execute(
        'SELECT l.*, m.name as member_name, m.member_id FROM loans l JOIN members m ON l.member_id = m.member_id WHERE l.id = %s',
        [loan_id])
    loan = cur.fetchone()

    if not loan:
        flash('Loan not found', 'danger')
        cur.close()
        return redirect(url_for('index'))

    loan['amount'] = float(loan['amount'])
    cur.execute('SELECT * FROM loan_repayments WHERE loan_id = %s', [loan_id])
    repayments = cur.fetchall()
    principal_paid = sum(float(r.get('principal_paid', 0)) for r in repayments)
    principal_remaining = round(loan['amount'] - principal_paid, 2)
    cur.close()
    return render_template('add_repayment.html', loan=loan, principal_remaining=principal_remaining,
                           today=datetime.now().strftime('%Y-%m-%d'))


@app.route('/savings_report')
@login_required
def savings_report():
    cur = get_cursor()
    cur.execute(
        'SELECT s.*, m.name as member_name, m.member_id FROM savings s JOIN members m ON s.member_id = m.member_id ORDER BY s.date DESC')
    all_savings = cur.fetchall()
    cur.execute('''SELECT m.member_id, m.name, COALESCE(SUM(s.amount), 0) as total_savings, COUNT(s.id) as transaction_count
                   FROM members m LEFT JOIN savings s ON m.member_id = s.member_id GROUP BY m.member_id, m.name
                   HAVING COALESCE(SUM(s.amount), 0) > 0 ORDER BY total_savings DESC''')
    member_summary = cur.fetchall()
    cur.execute('SELECT COALESCE(SUM(amount), 0) as total FROM savings')
    total_savings = float(cur.fetchone()['total'])
    cur.close()
    return render_template('savings_report.html', all_savings=all_savings, member_summary=member_summary,
                           total_savings=total_savings)


@app.route('/bulk_savings', methods=['GET', 'POST'])
@collector_or_admin_required
def bulk_savings():
    conn = get_db()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    if request.method == 'POST':
        date = request.form.get('date')
        if not date:
            flash('Please select a date', 'danger')
            return redirect(url_for('bulk_savings'))
        success_count = 0
        error_count = 0
        added_by = session.get('username')

        for key in request.form:
            if key.startswith('amount_'):
                member_id = key.replace('amount_', '')
                amount = request.form.get(key)
                if amount and float(amount) > 0:
                    try:
                        cur.execute(
                            'INSERT INTO savings (member_id, date, amount, added_by, added_at) VALUES (%s, %s, %s, %s, NOW())',
                            (member_id, date, amount, added_by))
                        success_count += 1
                    except Exception:
                        error_count += 1
        conn.commit()

        if success_count > 0:
            flash(f'✅ Successfully added savings for {success_count} member(s)!', 'success')
            log_action(added_by, 'bulk_add', 'savings', 'multiple', f'Bulk added {success_count} savings')
        if error_count > 0:
            flash(f'⚠️ Failed to add savings for {error_count} member(s)', 'danger')
        cur.close()
        return redirect(url_for('bulk_savings'))
    cur.execute('''SELECT m.member_id, m.name, COALESCE(SUM(s.amount), 0) as total_savings, MAX(s.date) as last_savings_date
                   FROM members m LEFT JOIN savings s ON m.member_id = s.member_id GROUP BY m.member_id, m.name ORDER BY m.member_id''')
    members = cur.fetchall()
    cur.close()
    return render_template('bulk_savings.html', members=members, today=datetime.now().strftime('%Y-%m-%d'))


@app.route('/ai_reports')
@login_required
def ai_reports():
    cur = get_cursor()
    role = session.get('role')

    if role in ['admin', 'collector']:
        cur.execute('SELECT COUNT(*) as count FROM members')
        total_members = cur.fetchone()['count']
        cur.execute('SELECT COALESCE(SUM(amount), 0) as total FROM savings')
        total_savings = float(cur.fetchone()['total'])
        cur.execute('SELECT COALESCE(SUM(amount), 0) as total FROM loans')
        total_loans = float(cur.fetchone()['total'])
        cur.execute('SELECT COALESCE(SUM(interest_paid), 0) as total FROM loan_repayments')
        total_profit = float(cur.fetchone()['total'])
    else:
        # Member: only their family stats
        member_ids = session.get('member_ids', [session.get('member_id')])
        placeholders = ','.join(['%s'] * len(member_ids))
        cur.execute(f'SELECT COUNT(*) as count FROM members WHERE member_id IN ({placeholders})', member_ids)
        total_members = cur.fetchone()['count']
        cur.execute(f'SELECT COALESCE(SUM(amount), 0) as total FROM savings WHERE member_id IN ({placeholders})', member_ids)
        total_savings = float(cur.fetchone()['total'])
        cur.execute(f'SELECT COALESCE(SUM(amount), 0) as total FROM loans WHERE member_id IN ({placeholders})', member_ids)
        total_loans = float(cur.fetchone()['total'])
        total_profit = 0

    cur.close()
    context = {
        'total_members': total_members,
        'total_savings': total_savings,
        'total_loans': total_loans,
        'total_profit': total_profit
    }
    return render_template('ai_reports.html', context=context)


@app.route('/ai_generate_report', methods=['POST'])
@login_required
def ai_generate_report():
    query = request.json.get('query', '').lower()
    cur = get_cursor()
    report_data = {}
    report_type = 'unknown'

    role = session.get('role')
    member_ids = session.get('member_ids', [session.get('member_id')]) if role == 'member' else None

    try:
        import re
        query = re.sub(r'[^\w\s]', ' ', query)
        query = ' '.join(query.split())

        def member_filter(alias='m'):
            if member_ids:
                ph = ','.join(['%s'] * len(member_ids))
                return f" AND {alias}.member_id IN ({ph})", member_ids
            return "", []

        if any(word in query for word in ['top', 'highest', 'best', 'most', 'maximum', 'max', 'biggest', 'largest']) and \
                any(word in query for word in ['saver', 'saving', 'savings', 'saved', 'deposit']):
            filt, params = member_filter()
            cur.execute(f'''SELECT m.member_id, m.name, COALESCE(SUM(s.amount), 0) as total FROM members m
                           LEFT JOIN savings s ON m.member_id = s.member_id
                           WHERE 1=1 {filt}
                           GROUP BY m.member_id, m.name
                           HAVING COALESCE(SUM(s.amount), 0) > 0 ORDER BY total DESC LIMIT 10''', params)
            report_data['members'] = cur.fetchall()
            report_type = 'top_savers'

        elif any(word in query for word in ['top', 'highest', 'best', 'most', 'maximum', 'max', 'biggest', 'largest']) and \
                any(word in query for word in ['loan', 'borrow', 'borrowed', 'borrower', 'debt', 'credit', 'taken', 'took']):
            filt, params = member_filter()
            cur.execute(f'''SELECT m.member_id, m.name, COALESCE(SUM(l.amount), 0) as total FROM members m
                           LEFT JOIN loans l ON m.member_id = l.member_id
                           WHERE 1=1 {filt}
                           GROUP BY m.member_id, m.name
                           HAVING COALESCE(SUM(l.amount), 0) > 0 ORDER BY total DESC LIMIT 10''', params)
            report_data['members'] = cur.fetchall()
            report_type = 'top_borrowers'

        elif any(word in query for word in ['lowest', 'minimum', 'min', 'least', 'smallest', 'bottom']) and \
                any(word in query for word in ['saver', 'saving', 'savings', 'saved', 'deposit']):
            filt, params = member_filter()
            cur.execute(f'''SELECT m.member_id, m.name, COALESCE(SUM(s.amount), 0) as total FROM members m
                           LEFT JOIN savings s ON m.member_id = s.member_id
                           WHERE 1=1 {filt}
                           GROUP BY m.member_id, m.name
                           HAVING COALESCE(SUM(s.amount), 0) > 0 ORDER BY total ASC LIMIT 10''', params)
            report_data['members'] = cur.fetchall()
            report_type = 'lowest_savers'

        elif any(phrase in query for phrase in
                 ['without saving', 'no saving', 'zero saving', 'not saved', 'havent saved',
                  'didnt save', 'never saved', 'no deposit', 'without deposit']):
            filt, params = member_filter()
            cur.execute(f'''SELECT m.member_id, m.name, m.contact FROM members m
                           LEFT JOIN savings s ON m.member_id = s.member_id
                           WHERE 1=1 {filt}
                           GROUP BY m.member_id, m.name, m.contact
                           HAVING COALESCE(SUM(s.amount), 0) = 0''', params)
            report_data['members'] = cur.fetchall()
            report_type = 'no_savings'

        elif any(word in query for word in ['outstanding', 'pending', 'due', 'unpaid', 'not paid', 'havent paid',
                                            'didnt pay', 'remaining', 'balance', 'owe', 'owes', 'owing']):
            filt, params = member_filter()
            cur.execute(f'''SELECT m.member_id, m.name, l.id as loan_id, l.amount, l.date,
                           COALESCE(SUM(lr.principal_paid), 0) as repaid
                           FROM members m JOIN loans l ON m.member_id = l.member_id
                           LEFT JOIN loan_repayments lr ON l.id = lr.loan_id
                           WHERE 1=1 {filt}
                           GROUP BY m.member_id, m.name, l.id, l.amount, l.date
                           HAVING l.amount > COALESCE(SUM(lr.principal_paid), 0)''', params)
            report_data['loans'] = cur.fetchall()
            report_type = 'outstanding_loans'

        elif any(word in query for word in ['paid', 'completed', 'finished', 'cleared', 'settled', 'closed']) and \
                any(word in query for word in ['loan', 'loans']):
            filt, params = member_filter()
            cur.execute(f'''SELECT m.member_id, m.name, l.id as loan_id, l.amount, l.date,
                           COALESCE(SUM(lr.principal_paid), 0) as repaid
                           FROM members m JOIN loans l ON m.member_id = l.member_id
                           LEFT JOIN loan_repayments lr ON l.id = lr.loan_id
                           WHERE 1=1 {filt}
                           GROUP BY m.member_id, m.name, l.id, l.amount, l.date
                           HAVING l.amount <= COALESCE(SUM(lr.principal_paid), 0)''', params)
            report_data['loans'] = cur.fetchall()
            report_type = 'paid_loans'

        elif any(word in query for word in ['monthly', 'month', 'months', 'per month']) and \
                any(word in query for word in ['saving', 'savings', 'saved', 'deposit']):
            if member_ids:
                ph = ','.join(['%s'] * len(member_ids))
                cur.execute(f'''SELECT TO_CHAR(date, 'YYYY-MM') as month, COUNT(*) as transactions,
                               SUM(amount) as total FROM savings
                               WHERE member_id IN ({ph})
                               GROUP BY month ORDER BY month DESC LIMIT 12''', member_ids)
            else:
                cur.execute('''SELECT TO_CHAR(date, 'YYYY-MM') as month, COUNT(*) as transactions,
                               SUM(amount) as total FROM savings
                               GROUP BY month ORDER BY month DESC LIMIT 12''')
            report_data['monthly'] = cur.fetchall()
            report_type = 'monthly_savings'

        elif any(word in query for word in ['monthly', 'month', 'months', 'per month']) and \
                any(word in query for word in ['loan', 'loans', 'borrowed', 'borrow']):
            if member_ids:
                ph = ','.join(['%s'] * len(member_ids))
                cur.execute(f'''SELECT TO_CHAR(date, 'YYYY-MM') as month, COUNT(*) as loans_given,
                               SUM(amount) as total FROM loans
                               WHERE member_id IN ({ph})
                               GROUP BY month ORDER BY month DESC LIMIT 12''', member_ids)
            else:
                cur.execute('''SELECT TO_CHAR(date, 'YYYY-MM') as month, COUNT(*) as loans_given,
                               SUM(amount) as total FROM loans
                               GROUP BY month ORDER BY month DESC LIMIT 12''')
            report_data['monthly'] = cur.fetchall()
            report_type = 'monthly_loans'

        elif any(word in query for word in ['recent', 'latest', 'last', 'new']) and \
                any(word in query for word in ['saving', 'savings', 'saved', 'deposit', 'transaction']):
            if member_ids:
                ph = ','.join(['%s'] * len(member_ids))
                cur.execute(f'''SELECT s.date, m.name, s.amount FROM savings s
                               JOIN members m ON s.member_id = m.member_id
                               WHERE s.member_id IN ({ph})
                               ORDER BY s.date DESC, s.created_at DESC LIMIT 20''', member_ids)
            else:
                cur.execute('''SELECT s.date, m.name, s.amount FROM savings s
                               JOIN members m ON s.member_id = m.member_id
                               ORDER BY s.date DESC, s.created_at DESC LIMIT 20''')
            report_data['transactions'] = cur.fetchall()
            report_type = 'recent_savings'

        elif any(word in query for word in ['recent', 'latest', 'last', 'new']) and \
                any(word in query for word in ['loan', 'loans', 'borrowed', 'borrow']):
            if member_ids:
                ph = ','.join(['%s'] * len(member_ids))
                cur.execute(f'''SELECT l.date, m.name, l.amount FROM loans l
                               JOIN members m ON l.member_id = m.member_id
                               WHERE l.member_id IN ({ph})
                               ORDER BY l.date DESC, l.created_at DESC LIMIT 20''', member_ids)
            else:
                cur.execute('''SELECT l.date, m.name, l.amount FROM loans l
                               JOIN members m ON l.member_id = m.member_id
                               ORDER BY l.date DESC, l.created_at DESC LIMIT 20''')
            report_data['loans_recent'] = cur.fetchall()
            report_type = 'recent_loans'

        elif any(word in query for word in ['profit', 'interest', 'income', 'earn', 'earned', 'earnings']):
            cur.execute('''SELECT TO_CHAR(lr.date, 'YYYY-MM') as month,
                           SUM(lr.interest_paid) as interest_earned
                           FROM loan_repayments lr
                           GROUP BY month ORDER BY month DESC LIMIT 12''')
            report_data['monthly'] = cur.fetchall()
            cur.execute('SELECT COALESCE(SUM(interest_paid), 0) as total FROM loan_repayments')
            report_data['total_profit'] = float(cur.fetchone()['total'])
            report_type = 'profit_report'

        elif any(word in query for word in ['all', 'list', 'show']) and \
                any(word in query for word in ['member', 'members', 'people', 'person']):
            filt, params = member_filter()
            cur.execute(f'''SELECT m.member_id, m.name, COALESCE(SUM(s.amount), 0) as total_savings,
                           COALESCE(SUM(l.amount), 0) as total_loans
                           FROM members m
                           LEFT JOIN savings s ON m.member_id = s.member_id
                           LEFT JOIN loans l ON m.member_id = l.member_id
                           WHERE 1=1 {filt}
                           GROUP BY m.member_id, m.name ORDER BY m.name''', params)
            report_data['members'] = cur.fetchall()
            report_type = 'all_members'

        elif any(word in query for word in ['summary', 'overview', 'dashboard', 'stats', 'statistics', 'report']):
            if member_ids:
                ph = ','.join(['%s'] * len(member_ids))
                cur.execute(f'SELECT COUNT(*) as count FROM members WHERE member_id IN ({ph})', member_ids)
                report_data['total_members'] = cur.fetchone()['count']
                cur.execute(f'SELECT COALESCE(SUM(amount), 0) as total FROM savings WHERE member_id IN ({ph})', member_ids)
                report_data['total_savings'] = float(cur.fetchone()['total'])
                cur.execute(f'SELECT COALESCE(SUM(amount), 0) as total FROM loans WHERE member_id IN ({ph})', member_ids)
                report_data['total_loans'] = float(cur.fetchone()['total'])
                report_data['total_profit'] = 0
            else:
                cur.execute('SELECT COUNT(*) as count FROM members')
                report_data['total_members'] = cur.fetchone()['count']
                cur.execute('SELECT COALESCE(SUM(amount), 0) as total FROM savings')
                report_data['total_savings'] = float(cur.fetchone()['total'])
                cur.execute('SELECT COALESCE(SUM(amount), 0) as total FROM loans')
                report_data['total_loans'] = float(cur.fetchone()['total'])
                cur.execute('SELECT COALESCE(SUM(interest_paid), 0) as total FROM loan_repayments')
                report_data['total_profit'] = float(cur.fetchone()['total'])
            report_type = 'summary'

        elif 'active' in query or 'inactive' in query:
            filt, params = member_filter()
            if 'inactive' in query:
                cur.execute(f'''SELECT m.member_id, m.name, m.contact FROM members m
                               LEFT JOIN savings s ON m.member_id = s.member_id
                               WHERE 1=1 {filt}
                               GROUP BY m.member_id, m.name, m.contact
                               HAVING MAX(s.date) IS NULL OR MAX(s.date) < CURRENT_DATE - INTERVAL '3 months' ''', params)
                report_data['members'] = cur.fetchall()
                report_type = 'inactive_members'
            else:
                cur.execute(f'''SELECT m.member_id, m.name, MAX(s.date) as last_transaction FROM members m
                               JOIN savings s ON m.member_id = s.member_id
                               WHERE 1=1 {filt}
                               GROUP BY m.member_id, m.name
                               HAVING MAX(s.date) >= CURRENT_DATE - INTERVAL '3 months'
                               ORDER BY last_transaction DESC''', params)
                report_data['members'] = cur.fetchall()
                report_type = 'active_members'

        else:
            report_type = 'help'

        cur.close()
        return jsonify({'success': True, 'report_type': report_type, 'data': report_data, 'query': query})

    except Exception as e:
        if cur:
            cur.close()
        return jsonify({'success': False, 'error': str(e)})


@app.route('/setup-db-x7k2')
def setup_db():
    init_db()
    return 'Database initialized!'


if __name__ == '__main__':
    with app.app_context():
        init_db()
    print("\n" + "=" * 60)
    print("Church Savings Management System Starting...")
    print("=" * 60)
    print("Open: http://localhost:5000")
    print("Login: Admin / z | collector / z | member via phone / phone")
    print("=" * 60 + "\n")
    app.run(host='0.0.0.0', port=5000)
