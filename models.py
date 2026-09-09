import sqlite3
import hashlib
import os
from flask import g
from werkzeug.security import generate_password_hash, check_password_hash

DATABASE = 'finance.db'
UPLOAD_FOLDER = 'uploads'

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db

def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    conn = sqlite3.connect(DATABASE)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS user (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS category (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('income','expense'))
        );
        CREATE TABLE IF NOT EXISTS account (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('cash','bank','alipay','wechat','credit_card','virtual')),
            balance REAL NOT NULL DEFAULT 0,
            icon TEXT DEFAULT '💳',
            color TEXT DEFAULT '#6366f1',
            is_archived INTEGER DEFAULT 0,
            sort_order INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES user(id)
        );
        CREATE TABLE IF NOT EXISTS record (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            account_id INTEGER,
            category_id INTEGER NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('income','expense')),
            amount REAL NOT NULL,
            description TEXT DEFAULT '',
            date DATE NOT NULL,
            status TEXT DEFAULT 'uncleared' CHECK(status IN ('cleared','uncleared')),
            image_path TEXT DEFAULT '',
            deleted_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES user(id),
            FOREIGN KEY (account_id) REFERENCES account(id),
            FOREIGN KEY (category_id) REFERENCES category(id)
        );
        CREATE TABLE IF NOT EXISTS transfer (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            from_account_id INTEGER NOT NULL,
            to_account_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            date DATE NOT NULL,
            description TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES user(id),
            FOREIGN KEY (from_account_id) REFERENCES account(id),
            FOREIGN KEY (to_account_id) REFERENCES account(id)
        );
        CREATE TABLE IF NOT EXISTS tag (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            color TEXT DEFAULT '#6366f1',
            UNIQUE(user_id, name),
            FOREIGN KEY (user_id) REFERENCES user(id)
        );
        CREATE TABLE IF NOT EXISTS record_tag (
            record_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL,
            PRIMARY KEY (record_id, tag_id),
            FOREIGN KEY (record_id) REFERENCES record(id) ON DELETE CASCADE,
            FOREIGN KEY (tag_id) REFERENCES tag(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS recurring_transaction (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            account_id INTEGER,
            type TEXT NOT NULL CHECK(type IN ('income','expense')),
            category_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            description TEXT DEFAULT '',
            frequency TEXT NOT NULL CHECK(frequency IN ('daily','weekly','monthly','yearly')),
            interval_value INTEGER DEFAULT 1,
            next_date DATE NOT NULL,
            end_date DATE,
            active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES user(id),
            FOREIGN KEY (account_id) REFERENCES account(id),
            FOREIGN KEY (category_id) REFERENCES category(id)
        );
        CREATE TABLE IF NOT EXISTS reminder (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            amount REAL DEFAULT 0,
            due_date DATE,
            repeat_type TEXT DEFAULT 'none' CHECK(repeat_type IN ('none','daily','weekly','monthly','yearly')),
            category_id INTEGER,
            account_id INTEGER,
            paid INTEGER DEFAULT 0,
            note TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES user(id),
            FOREIGN KEY (category_id) REFERENCES category(id),
            FOREIGN KEY (account_id) REFERENCES account(id)
        );
        CREATE TABLE IF NOT EXISTS budget (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            category_id INTEGER NOT NULL,
            month TEXT NOT NULL,
            amount REAL NOT NULL,
            FOREIGN KEY (user_id) REFERENCES user(id),
            FOREIGN KEY (category_id) REFERENCES category(id),
            UNIQUE(user_id, category_id, month)
        );
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            currency_symbol TEXT DEFAULT '¥',
            default_account_id INTEGER,
            dark_mode INTEGER DEFAULT 0,
            page_size INTEGER DEFAULT 50,
            FOREIGN KEY (user_id) REFERENCES user(id)
        );
    ''')
    cur = conn.execute('SELECT COUNT(*) FROM category')
    if cur.fetchone()[0] == 0:
        defaults = [
            ('工资','income'),('兼职','income'),('投资收入','income'),
            ('奖金','income'),('红包','income'),('租金收入','income'),
            ('餐饮','expense'),('交通','expense'),('购物','expense'),
            ('娱乐','expense'),('住房','expense'),('水电','expense'),
            ('医疗','expense'),('教育','expense'),('通讯','expense'),
            ('服饰','expense'),('人情','expense'),('其他','expense')
        ]
        conn.executemany('INSERT INTO category (name, type) VALUES (?,?)', defaults)
    try:
        conn.execute('ALTER TABLE record ADD COLUMN deleted_at TIMESTAMP')
    except:
        pass
    conn.execute('CREATE INDEX IF NOT EXISTS idx_record_user_date ON record(user_id, date)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_record_user_type ON record(user_id, type)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_record_user_deleted ON record(user_id, deleted_at)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_record_tag_record ON record_tag(record_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_budget_user_month ON budget(user_id, month)')
    conn.commit()
    conn.close()

# ─── User ──────────────────────────────────────────────
class User:
    @staticmethod
    def create(username, password):
        pwd_hash = generate_password_hash(password)
        db = get_db()
        db.execute('INSERT INTO user (username, password_hash) VALUES (?, ?)', (username, pwd_hash))
        db.commit()
        uid = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        db.execute('INSERT INTO user_settings (user_id) VALUES (?)', (uid,))
        db.commit()

    @staticmethod
    def find_by_username(username):
        return get_db().execute('SELECT * FROM user WHERE username=?', (username,)).fetchone()

    @staticmethod
    def find_by_id(user_id):
        return get_db().execute('SELECT * FROM user WHERE id=?', (user_id,)).fetchone()

    @staticmethod
    def is_hashed(stored):
        return stored.startswith(('pbkdf2:', 'scrypt:'))

    @staticmethod
    def check_password(user_row, password):
        stored = user_row['password_hash']
        if User.is_hashed(stored):
            return check_password_hash(stored, password)
        return hashlib.sha256(password.encode()).hexdigest() == stored

    @staticmethod
    def update_password(user_id, new_password):
        pwd_hash = generate_password_hash(new_password)
        db = get_db()
        db.execute('UPDATE user SET password_hash=? WHERE id=?', (pwd_hash, user_id))
        db.commit()

# ─── Category ──────────────────────────────────────────
class Category:
    @staticmethod
    def query_all():
        return get_db().execute('SELECT * FROM category ORDER BY type, id').fetchall()

    @staticmethod
    def create(name, type_):
        db = get_db()
        db.execute('INSERT INTO category (name, type) VALUES (?,?)', (name, type_))
        db.commit()

    @staticmethod
    def get_by_name_type(name, type_):
        return get_db().execute('SELECT * FROM category WHERE name=? AND type=?', (name, type_)).fetchone()

    @staticmethod
    def rename(category_id, name, type_):
        db = get_db()
        db.execute('UPDATE category SET name=?, type=? WHERE id=?', (name, type_, category_id))
        db.commit()

    @staticmethod
    def is_used(category_id):
        db = get_db()
        return db.execute('SELECT COUNT(*) FROM record WHERE category_id=? AND deleted_at IS NULL', (category_id,)).fetchone()[0]

    @staticmethod
    def delete(category_id):
        db = get_db()
        db.execute('DELETE FROM category WHERE id=?', (category_id,))
        db.commit()

# ─── Account ──────────────────────────────────────────
class Account:
    @staticmethod
    def create(user_id, name, type_, balance=0, icon='💳', color='#6366f1'):
        db = get_db()
        db.execute('INSERT INTO account (user_id, name, type, balance, icon, color) VALUES (?,?,?,?,?,?)',
                   (user_id, name, type_, balance, icon, color))
        db.commit()

    @staticmethod
    def get_by_user(user_id):
        return get_db().execute('SELECT * FROM account WHERE user_id=? AND is_archived=0 ORDER BY sort_order, id', (user_id,)).fetchall()

    @staticmethod
    def get_archived(user_id):
        return get_db().execute('SELECT * FROM account WHERE user_id=? AND is_archived=1 ORDER BY id', (user_id,)).fetchall()

    @staticmethod
    def get_by_id(account_id, user_id):
        return get_db().execute('SELECT * FROM account WHERE id=? AND user_id=?', (account_id, user_id)).fetchone()

    @staticmethod
    def update(account_id, user_id, name, type_, icon, color):
        db = get_db()
        db.execute('UPDATE account SET name=?, type=?, icon=?, color=? WHERE id=? AND user_id=?',
                   (name, type_, icon, color, account_id, user_id))
        db.commit()

    @staticmethod
    def archive(account_id, user_id):
        db = get_db()
        db.execute('UPDATE account SET is_archived=1 WHERE id=? AND user_id=?', (account_id, user_id))
        db.commit()

    @staticmethod
    def restore(account_id, user_id):
        db = get_db()
        db.execute('UPDATE account SET is_archived=0 WHERE id=? AND user_id=?', (account_id, user_id))
        db.commit()

    @staticmethod
    def update_balance(account_id, user_id):
        db = get_db()
        income = db.execute(
            'SELECT COALESCE(SUM(amount),0) FROM record WHERE account_id=? AND user_id=? AND type="income" AND deleted_at IS NULL',
            (account_id, user_id)).fetchone()[0]
        expense = db.execute(
            'SELECT COALESCE(SUM(amount),0) FROM record WHERE account_id=? AND user_id=? AND type="expense" AND deleted_at IS NULL',
            (account_id, user_id)).fetchone()[0]
        transferred_in = db.execute(
            'SELECT COALESCE(SUM(amount),0) FROM transfer WHERE to_account_id=? AND user_id=?',
            (account_id, user_id)).fetchone()[0]
        transferred_out = db.execute(
            'SELECT COALESCE(SUM(amount),0) FROM transfer WHERE from_account_id=? AND user_id=?',
            (account_id, user_id)).fetchone()[0]
        balance = income + transferred_in - expense - transferred_out
        db.execute('UPDATE account SET balance=? WHERE id=? AND user_id=?', (balance, account_id, user_id))
        db.commit()

    @staticmethod
    def get_summary(user_id):
        db = get_db()
        total_assets = db.execute(
            'SELECT COALESCE(SUM(balance),0) FROM account WHERE user_id=? AND is_archived=0 AND type!="credit_card"',
            (user_id,)).fetchone()[0]
        total_debt = db.execute(
            'SELECT COALESCE(SUM(balance),0) FROM account WHERE user_id=? AND is_archived=0 AND type="credit_card"',
            (user_id,)).fetchone()[0]
        return {'total_assets': total_assets, 'total_debt': abs(total_debt), 'net_worth': total_assets + total_debt}

# ─── Record ──────────────────────────────────────────
class Record:
    @staticmethod
    def create(user_id, type_, category_id, amount, description, date, account_id=None, image_path='', status='uncleared'):
        db = get_db()
        db.execute('INSERT INTO record (user_id, type, category_id, amount, description, date, account_id, image_path, status) VALUES (?,?,?,?,?,?,?,?,?)',
                   (user_id, type_, category_id, amount, description, date, account_id, image_path, status))
        db.commit()
        rid = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        if account_id:
            Account.update_balance(account_id, user_id)
        return rid

    @staticmethod
    def get_by_user(user_id):
        return get_db().execute('''
            SELECT record.*, category.name as category_name, account.name as account_name, account.icon as account_icon
            FROM record
            JOIN category ON record.category_id = category.id
            LEFT JOIN account ON record.account_id = account.id
            WHERE record.user_id=? AND record.deleted_at IS NULL
            ORDER BY date DESC, id DESC
        ''', (user_id,)).fetchall()

    @staticmethod
    def get_by_id_and_user(record_id, user_id):
        return get_db().execute('''
            SELECT record.*, category.name as category_name, account.name as account_name
            FROM record
            JOIN category ON record.category_id = category.id
            LEFT JOIN account ON record.account_id = account.id
            WHERE record.id=? AND record.user_id=? AND record.deleted_at IS NULL
        ''', (record_id, user_id)).fetchone()

    @staticmethod
    def update(record_id, user_id, type_, category_id, amount, description, date, account_id=None, image_path=None, status=None):
        db = get_db()
        old = db.execute('SELECT * FROM record WHERE id=? AND user_id=?', (record_id, user_id)).fetchone()
        if not old:
            return
        old_account_id = old['account_id']
        fields = ['type=?','category_id=?','amount=?','description=?','date=?']
        params = [type_, category_id, amount, description, date]
        if account_id is not None:
            fields.append('account_id=?')
            params.append(account_id)
        if image_path is not None:
            fields.append('image_path=?')
            params.append(image_path)
        if status is not None:
            fields.append('status=?')
            params.append(status)
        params.extend([record_id, user_id])
        db.execute(f'UPDATE record SET {",".join(fields)} WHERE id=? AND user_id=?', params)
        db.commit()
        if old_account_id:
            Account.update_balance(old_account_id, user_id)
        if account_id and account_id != old_account_id:
            Account.update_balance(account_id, user_id)

    @staticmethod
    def delete(record_id, user_id):
        db = get_db()
        r = db.execute('SELECT * FROM record WHERE id=? AND user_id=?', (record_id, user_id)).fetchone()
        if not r:
            return
        db.execute("UPDATE record SET deleted_at=datetime('now') WHERE id=? AND user_id=?", (record_id, user_id))
        db.commit()
        if r['account_id']:
            Account.update_balance(r['account_id'], user_id)

    @staticmethod
    def get_trashed(user_id):
        return get_db().execute('''
            SELECT record.*, category.name as category_name,
                   account.name as account_name, account.icon as account_icon
            FROM record
            JOIN category ON record.category_id = category.id
            LEFT JOIN account ON record.account_id = account.id
            WHERE record.user_id=? AND record.deleted_at IS NOT NULL
            ORDER BY record.deleted_at DESC
        ''', (user_id,)).fetchall()

    @staticmethod
    def restore(record_id, user_id):
        db = get_db()
        db.execute("UPDATE record SET deleted_at=NULL WHERE id=? AND user_id=?", (record_id, user_id))
        db.commit()
        r = db.execute('SELECT * FROM record WHERE id=? AND user_id=?', (record_id, user_id)).fetchone()
        if r and r['account_id']:
            Account.update_balance(r['account_id'], user_id)

    @staticmethod
    def permanent_delete(record_id, user_id):
        db = get_db()
        r = db.execute('SELECT * FROM record WHERE id=? AND user_id=?', (record_id, user_id)).fetchone()
        if not r:
            return
        aid = r['account_id']
        img = r['image_path']
        db.execute('DELETE FROM record_tag WHERE record_id=?', (record_id,))
        db.execute('DELETE FROM record WHERE id=? AND user_id=?', (record_id, user_id))
        db.commit()
        if aid:
            Account.update_balance(aid, user_id)
        if img:
            try:
                os.remove(img)
            except:
                pass

    @staticmethod
    def cleanup_trash():
        db = get_db()
        expired = db.execute("SELECT * FROM record WHERE deleted_at IS NOT NULL AND deleted_at < datetime('now', '-30 days')").fetchall()
        for r in expired:
            img = r['image_path']
            db.execute('DELETE FROM record_tag WHERE record_id=?', (r['id'],))
            db.execute('DELETE FROM record WHERE id=?', (r['id'],))
            if img:
                try:
                    os.remove(img)
                except:
                    pass
        db.commit()
        return len(expired)

    @staticmethod
    def batch_delete(user_id, record_ids):
        if not record_ids:
            return
        db = get_db()
        placeholders = ','.join('?' for _ in record_ids)
        db.execute(f"UPDATE record SET deleted_at=datetime('now') WHERE id IN ({placeholders}) AND user_id=?",
                   record_ids + [user_id])
        db.commit()
        for rid in record_ids:
            r = db.execute('SELECT account_id FROM record WHERE id=? AND user_id=?', (rid, user_id)).fetchone()
            if r and r['account_id']:
                Account.update_balance(r['account_id'], user_id)

    @staticmethod
    def batch_update(user_id, record_ids, **fields):
        if not record_ids:
            return
        db = get_db()
        allowed = ['category_id', 'account_id', 'type', 'status', 'description']
        sets = []
        params = []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if v is None:
                sets.append(f'{k}=NULL')
            else:
                sets.append(f'{k}=?')
                params.append(v)
        if not sets:
            return
        placeholders = ','.join('?' for _ in record_ids)
        params.extend(record_ids + [user_id])
        old_accounts = {r['id']: r['account_id'] for r in
                        db.execute(f'SELECT id, account_id FROM record WHERE id IN ({placeholders}) AND user_id=?',
                                   record_ids + [user_id]).fetchall()}
        db.execute(f'UPDATE record SET {",".join(sets)} WHERE id IN ({placeholders}) AND user_id=?', params)
        db.commit()
        updated_accounts = set()
        if 'account_id' in fields:
            if fields['account_id'] is not None:
                updated_accounts.add(int(fields['account_id']))
        for aid in old_accounts.values():
            if aid:
                updated_accounts.add(aid)
        for aid in updated_accounts:
            Account.update_balance(aid, user_id)

    @staticmethod
    def _filter_clause(keyword=None, category_id=None, start_date=None, end_date=None, type_=None,
                       account_id=None, tag_id=None, status=None, amount_min=None, amount_max=None):
        clauses = []
        params = []
        if keyword:
            clauses.append('(record.description LIKE ? OR category.name LIKE ? OR record.amount LIKE ?)')
            kw = f'%{keyword}%'
            params.extend([kw, kw, kw])
        if category_id:
            clauses.append('record.category_id = ?')
            params.append(category_id)
        if start_date:
            clauses.append('record.date >= ?')
            params.append(start_date)
        if end_date:
            clauses.append('record.date <= ?')
            params.append(end_date)
        if type_:
            clauses.append('record.type = ?')
            params.append(type_)
        if account_id:
            clauses.append('record.account_id = ?')
            params.append(account_id)
        if amount_min:
            clauses.append('record.amount >= ?')
            params.append(amount_min)
        if amount_max:
            clauses.append('record.amount <= ?')
            params.append(amount_max)
        if tag_id:
            clauses.append('record.id IN (SELECT record_id FROM record_tag WHERE tag_id=?)')
            params.append(tag_id)
        if status:
            clauses.append('record.status = ?')
            params.append(status)
        return (' AND ' + ' AND '.join(clauses)) if clauses else '', params

    @staticmethod
    def search(user_id, keyword=None, category_id=None, start_date=None, end_date=None, type_=None,
               account_id=None, tag_id=None, status=None, amount_min=None, amount_max=None,
               order_by='date_desc', page=1, page_size=50):
        where, filter_params = Record._filter_clause(
            keyword=keyword, category_id=category_id, start_date=start_date, end_date=end_date,
            type_=type_, account_id=account_id, tag_id=tag_id, status=status,
            amount_min=amount_min, amount_max=amount_max)
        query = '''
            SELECT record.*, category.name as category_name,
                   account.name as account_name, account.icon as account_icon
            FROM record
            JOIN category ON record.category_id = category.id
            LEFT JOIN account ON record.account_id = account.id
            WHERE record.user_id = ? AND record.deleted_at IS NULL
        ''' + where
        count_query = 'SELECT COUNT(*) FROM record JOIN category ON record.category_id = category.id WHERE record.user_id=? AND record.deleted_at IS NULL' + where
        params = [user_id] + filter_params
        order_map = {
            'date_desc': 'ORDER BY record.date DESC, record.id DESC',
            'date_asc': 'ORDER BY record.date ASC, record.id ASC',
            'amount_desc': 'ORDER BY record.amount DESC, record.id DESC',
            'amount_asc': 'ORDER BY record.amount ASC, record.id ASC',
        }
        query += ' ' + order_map.get(order_by, order_map['date_desc'])
        db = get_db()
        total = db.execute(count_query, params).fetchone()[0]
        offset = (page - 1) * page_size
        query += ' LIMIT ? OFFSET ?'
        params.extend([page_size, offset])
        rows = db.execute(query, params).fetchall()
        return rows, total

    @staticmethod
    def search_totals(user_id, keyword=None, category_id=None, start_date=None, end_date=None, type_=None,
                      account_id=None, tag_id=None, status=None, amount_min=None, amount_max=None):
        where, filter_params = Record._filter_clause(
            keyword=keyword, category_id=category_id, start_date=start_date, end_date=end_date,
            type_=type_, account_id=account_id, tag_id=tag_id, status=status,
            amount_min=amount_min, amount_max=amount_max)
        params = [user_id] + filter_params
        row = get_db().execute('''
            SELECT COALESCE(SUM(CASE WHEN record.type='income' THEN record.amount ELSE 0 END),0) as income,
                   COALESCE(SUM(CASE WHEN record.type='expense' THEN record.amount ELSE 0 END),0) as expense
            FROM record JOIN category ON record.category_id = category.id
            WHERE record.user_id = ? AND record.deleted_at IS NULL
        ''' + where, params).fetchone()
        return row['income'], row['expense']

    @staticmethod
    def get_recent(user_id, limit=5):
        return get_db().execute('''
            SELECT record.*, category.name as category_name,
                   account.name as account_name, account.icon as account_icon
            FROM record
            JOIN category ON record.category_id = category.id
            LEFT JOIN account ON record.account_id = account.id
            WHERE record.user_id=? AND record.deleted_at IS NULL
            ORDER BY date DESC, id DESC LIMIT ?
        ''', (user_id, limit)).fetchall()

    @staticmethod
    def get_month_total(user_id, type_, month):
        cur = get_db().execute('SELECT COALESCE(SUM(amount),0) FROM record WHERE user_id=? AND type=? AND strftime("%Y-%m", date)=? AND deleted_at IS NULL',
                               (user_id, type_, month))
        return cur.fetchone()[0]

    @staticmethod
    def get_month_category_total(user_id, type_, category_id, month):
        cur = get_db().execute('SELECT COALESCE(SUM(amount),0) FROM record WHERE user_id=? AND type=? AND category_id=? AND strftime("%Y-%m", date)=? AND deleted_at IS NULL',
                               (user_id, type_, category_id, month))
        return cur.fetchone()[0]

    @staticmethod
    def get_day_total(user_id, type_, date_str):
        cur = get_db().execute('SELECT COALESCE(SUM(amount),0) FROM record WHERE user_id=? AND type=? AND date=? AND deleted_at IS NULL',
                               (user_id, type_, date_str))
        return cur.fetchone()[0]

    @staticmethod
    def get_tags_for_record(record_id):
        return get_db().execute('''
            SELECT tag.* FROM tag
            JOIN record_tag ON tag.id = record_tag.tag_id
            WHERE record_tag.record_id=?
        ''', (record_id,)).fetchall()

    @staticmethod
    def get_tags_batch(record_ids):
        if not record_ids:
            return {}
        db = get_db()
        placeholders = ','.join('?' for _ in record_ids)
        rows = db.execute(f'''
            SELECT rt.record_id, tag.* FROM tag
            JOIN record_tag rt ON tag.id = rt.tag_id
            WHERE rt.record_id IN ({placeholders})
        ''', list(record_ids)).fetchall()
        result = {}
        for r in rows:
            result.setdefault(r['record_id'], []).append(r)
        return result

    @staticmethod
    def set_tags(record_id, tag_ids):
        db = get_db()
        db.execute('DELETE FROM record_tag WHERE record_id=?', (record_id,))
        for tid in tag_ids:
            db.execute('INSERT OR IGNORE INTO record_tag (record_id, tag_id) VALUES (?,?)', (record_id, tid))
        db.commit()

    @staticmethod
    def get_year_summary(user_id, year):
        db = get_db()
        months = []
        for m in range(1, 13):
            month_str = f'{year}-{m:02d}'
            income = db.execute('SELECT COALESCE(SUM(amount),0) FROM record WHERE user_id=? AND type="income" AND strftime("%Y-%m", date)=? AND deleted_at IS NULL',
                                (user_id, month_str)).fetchone()[0]
            expense = db.execute('SELECT COALESCE(SUM(amount),0) FROM record WHERE user_id=? AND type="expense" AND strftime("%Y-%m", date)=? AND deleted_at IS NULL',
                                 (user_id, month_str)).fetchone()[0]
            months.append({'month': m, 'income': income, 'expense': expense})
        top_cats = db.execute('''
            SELECT category.name, COALESCE(SUM(record.amount),0) as total
            FROM record JOIN category ON record.category_id = category.id
            WHERE record.user_id=? AND record.type="expense" AND strftime("%Y", record.date)=? AND record.deleted_at IS NULL
            GROUP BY record.category_id ORDER BY total DESC LIMIT 10
        ''', (user_id, str(year))).fetchall()
        return {'months': months, 'top_categories': top_cats}

# ─── Transfer ──────────────────────────────────────────
class Transfer:
    @staticmethod
    def create(user_id, from_account_id, to_account_id, amount, date, description=''):
        db = get_db()
        db.execute('INSERT INTO transfer (user_id, from_account_id, to_account_id, amount, date, description) VALUES (?,?,?,?,?,?)',
                   (user_id, from_account_id, to_account_id, amount, date, description))
        db.commit()
        Account.update_balance(from_account_id, user_id)
        Account.update_balance(to_account_id, user_id)

    @staticmethod
    def get_by_user(user_id):
        return get_db().execute('''
            SELECT t.*,
                   a1.name as from_name, a1.icon as from_icon,
                   a2.name as to_name, a2.icon as to_icon
            FROM transfer t
            JOIN account a1 ON t.from_account_id = a1.id
            JOIN account a2 ON t.to_account_id = a2.id
            WHERE t.user_id=? ORDER BY t.date DESC, t.id DESC
        ''', (user_id,)).fetchall()

    @staticmethod
    def delete(transfer_id, user_id):
        db = get_db()
        t = db.execute('SELECT * FROM transfer WHERE id=? AND user_id=?', (transfer_id, user_id)).fetchone()
        if not t:
            return
        db.execute('DELETE FROM transfer WHERE id=? AND user_id=?', (transfer_id, user_id))
        db.commit()
        Account.update_balance(t['from_account_id'], user_id)
        Account.update_balance(t['to_account_id'], user_id)

# ─── Tag ──────────────────────────────────────────────
class Tag:
    @staticmethod
    def create(user_id, name, color='#6366f1'):
        db = get_db()
        db.execute('INSERT INTO tag (user_id, name, color) VALUES (?,?,?)', (user_id, name, color))
        db.commit()

    @staticmethod
    def get_by_user(user_id):
        return get_db().execute('SELECT * FROM tag WHERE user_id=? ORDER BY id', (user_id,)).fetchall()

    @staticmethod
    def get_by_id(tag_id, user_id):
        return get_db().execute('SELECT * FROM tag WHERE id=? AND user_id=?', (tag_id, user_id)).fetchone()

    @staticmethod
    def update(tag_id, user_id, name, color):
        db = get_db()
        db.execute('UPDATE tag SET name=?, color=? WHERE id=? AND user_id=?', (name, color, tag_id, user_id))
        db.commit()

    @staticmethod
    def delete(tag_id, user_id):
        db = get_db()
        db.execute('DELETE FROM record_tag WHERE tag_id=?', (tag_id,))
        db.execute('DELETE FROM tag WHERE id=? AND user_id=?', (tag_id, user_id))
        db.commit()

# ─── Recurring Transaction ────────────────────────────
class RecurringTransaction:
    @staticmethod
    def create(user_id, account_id, type_, category_id, amount, description, frequency, interval_value, next_date, end_date=None):
        db = get_db()
        db.execute('''INSERT INTO recurring_transaction
            (user_id, account_id, type, category_id, amount, description, frequency, interval_value, next_date, end_date)
            VALUES (?,?,?,?,?,?,?,?,?,?)''',
                   (user_id, account_id, type_, category_id, amount, description, frequency, interval_value, next_date, end_date))
        db.commit()

    @staticmethod
    def get_by_user(user_id):
        return get_db().execute('''
            SELECT rt.*, category.name as category_name,
                   account.name as account_name, account.icon as account_icon
            FROM recurring_transaction rt
            JOIN category ON rt.category_id = category.id
            LEFT JOIN account ON rt.account_id = account.id
            WHERE rt.user_id=? ORDER BY rt.next_date
        ''', (user_id,)).fetchall()

    @staticmethod
    def get_by_id(rt_id, user_id):
        return get_db().execute('SELECT * FROM recurring_transaction WHERE id=? AND user_id=?', (rt_id, user_id)).fetchone()

    @staticmethod
    def update(rt_id, user_id, account_id, type_, category_id, amount, description, frequency, interval_value, next_date, end_date, active):
        db = get_db()
        db.execute('''UPDATE recurring_transaction SET
            account_id=?, type=?, category_id=?, amount=?, description=?,
            frequency=?, interval_value=?, next_date=?, end_date=?, active=?
            WHERE id=? AND user_id=?''',
                   (account_id, type_, category_id, amount, description, frequency, interval_value, next_date, end_date, active, rt_id, user_id))
        db.commit()

    @staticmethod
    def delete(rt_id, user_id):
        db = get_db()
        db.execute('DELETE FROM recurring_transaction WHERE id=? AND user_id=?', (rt_id, user_id))
        db.commit()

    @staticmethod
    def process_due(user_id):
        db = get_db()
        today = db.execute("SELECT date('now')").fetchone()[0]
        due_ones = db.execute('''
            SELECT * FROM recurring_transaction
            WHERE user_id=? AND active=1 AND next_date<=?
        ''', (user_id, today)).fetchall()
        for rt in due_ones:
            db.execute('INSERT INTO record (user_id, type, category_id, amount, description, date, account_id) VALUES (?,?,?,?,?,?,?)',
                       (user_id, rt['type'], rt['category_id'], rt['amount'], rt['description'], today, rt['account_id']))
            import datetime
            from dateutil.relativedelta import relativedelta
            cur = datetime.datetime.strptime(rt['next_date'], '%Y-%m-%d').date()
            if rt['frequency'] == 'daily':
                nxt = cur + datetime.timedelta(days=rt['interval_value'])
            elif rt['frequency'] == 'weekly':
                nxt = cur + datetime.timedelta(weeks=rt['interval_value'])
            elif rt['frequency'] == 'monthly':
                nxt = cur + relativedelta(months=rt['interval_value'])
            elif rt['frequency'] == 'yearly':
                nxt = cur + relativedelta(years=rt['interval_value'])
            nxt_str = nxt.strftime('%Y-%m-%d')
            if rt['end_date'] and nxt_str > rt['end_date']:
                db.execute('UPDATE recurring_transaction SET active=0 WHERE id=?', (rt['id'],))
            else:
                db.execute('UPDATE recurring_transaction SET next_date=? WHERE id=?', (nxt_str, rt['id']))
            if rt['account_id']:
                Account.update_balance(rt['account_id'], user_id)
        db.commit()
        return len(due_ones)

# ─── Reminder ──────────────────────────────────────────
class Reminder:
    @staticmethod
    def create(user_id, title, amount=0, due_date=None, repeat_type='none', category_id=None, account_id=None, note=''):
        db = get_db()
        db.execute('INSERT INTO reminder (user_id, title, amount, due_date, repeat_type, category_id, account_id, note) VALUES (?,?,?,?,?,?,?,?)',
                   (user_id, title, amount, due_date, repeat_type, category_id, account_id, note))
        db.commit()

    @staticmethod
    def get_by_user(user_id):
        return get_db().execute('''
            SELECT r.*, category.name as category_name,
                   account.name as account_name, account.icon as account_icon
            FROM reminder r
            LEFT JOIN category ON r.category_id = category.id
            LEFT JOIN account ON r.account_id = account.id
            WHERE r.user_id=? ORDER BY r.paid, r.due_date
        ''', (user_id,)).fetchall()

    @staticmethod
    def get_pending(user_id):
        return get_db().execute('''
            SELECT r.*, category.name as category_name,
                   account.name as account_name, account.icon as account_icon
            FROM reminder r
            LEFT JOIN category ON r.category_id = category.id
            LEFT JOIN account ON r.account_id = account.id
            WHERE r.user_id=? AND r.paid=0 ORDER BY r.due_date
        ''', (user_id,)).fetchall()

    @staticmethod
    def mark_paid(reminder_id, user_id):
        db = get_db()
        db.execute('UPDATE reminder SET paid=1 WHERE id=? AND user_id=?', (reminder_id, user_id))
        db.commit()

    @staticmethod
    def update(reminder_id, user_id, title, amount, due_date, repeat_type, category_id, account_id, note):
        db = get_db()
        db.execute('''UPDATE reminder SET title=?, amount=?, due_date=?, repeat_type=?, category_id=?, account_id=?, note=?
            WHERE id=? AND user_id=?''',
                   (title, amount, due_date, repeat_type, category_id, account_id, note, reminder_id, user_id))
        db.commit()

    @staticmethod
    def delete(reminder_id, user_id):
        db = get_db()
        db.execute('DELETE FROM reminder WHERE id=? AND user_id=?', (reminder_id, user_id))
        db.commit()

# ─── User Settings ─────────────────────────────────────
class UserSettings:
    @staticmethod
    def get(user_id):
        s = get_db().execute('SELECT * FROM user_settings WHERE user_id=?', (user_id,)).fetchone()
        if not s:
            get_db().execute('INSERT INTO user_settings (user_id) VALUES (?)', (user_id,))
            get_db().commit()
            s = get_db().execute('SELECT * FROM user_settings WHERE user_id=?', (user_id,)).fetchone()
        return s

    @staticmethod
    def update(user_id, **kwargs):
        allowed = ['currency_symbol', 'default_account_id', 'dark_mode', 'page_size']
        sets = []
        vals = []
        for k, v in kwargs.items():
            if k in allowed:
                sets.append(f'{k}=?')
                vals.append(v)
        if sets:
            vals.append(user_id)
            db = get_db()
            db.execute(f'UPDATE user_settings SET {",".join(sets)} WHERE user_id=?', vals)
            db.commit()

# ─── Budget ──────────────────────────────────────────────
class Budget:
    @staticmethod
    def set_budget(user_id, category_id, month, amount):
        db = get_db()
        db.execute('INSERT INTO budget (user_id, category_id, month, amount) VALUES (?,?,?,?) ON CONFLICT(user_id, category_id, month) DO UPDATE SET amount=?',
                   (user_id, category_id, month, amount, amount))
        db.commit()

    @staticmethod
    def get_by_user_month(user_id, month):
        return get_db().execute('''
            SELECT budget.*, category.name as category_name
            FROM budget JOIN category ON budget.category_id = category.id
            WHERE budget.user_id=? AND budget.month=?
        ''', (user_id, month)).fetchall()
