import os
import calendar
import csv
import io
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, flash, Response, send_file, abort
from models import (init_db, close_db, User, Category, Account, Record, Transfer, Tag,
                    RecurringTransaction, Reminder, UserSettings, Budget)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24).hex())
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
app.teardown_appcontext(close_db)

with app.app_context():
    init_db()

def get_settings():
    if 'user_id' in session:
        return UserSettings.get(session['user_id'])
    return {'currency_symbol': '¥', 'dark_mode': 0, 'page_size': 50}

def _last_n_months(n, ref=None):
    ref = ref or datetime.now().replace(day=1)
    months = []
    y, m = ref.year, ref.month
    for _ in range(n):
        months.append(f'{y}-{m:02d}')
        m -= 1
        if m == 0:
            y -= 1
            m = 12
    return list(reversed(months))

@app.context_processor
def inject_globals():
    ctx = {'now': datetime.now(), 'dark_mode': 0, 'settings': {'currency_symbol': '¥', 'page_size': 50}}
    if 'user_id' in session:
        s = get_settings()
        ctx['settings'] = s
        ctx['dark_mode'] = s['dark_mode']
    return ctx

@app.template_filter('currency')
def currency_filter(value, symbol=None):
    if symbol is None:
        symbol = get_settings().get('currency_symbol', '¥')
    return f'{symbol}{value:,.2f}'

@app.template_filter('human_amount')
def human_amount(value, symbol='¥'):
    if value is None:
        value = 0
    if abs(value) >= 10000:
        return f'{symbol}{value/10000:.2f}万'
    if abs(value) >= 1:
        return f'{symbol}{value:,.2f}'
    return f'{symbol}{value:.2f}'

# ─── 首页仪表盘 ──────────────────────────────────────
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    current_month = datetime.now().strftime('%Y-%m')
    income_total = Record.get_month_total(user_id, 'income', current_month)
    expense_total = Record.get_month_total(user_id, 'expense', current_month)
    budgets = Budget.get_by_user_month(user_id, current_month)
    budget_details = []
    for b in budgets:
        spent = Record.get_month_category_total(user_id, 'expense', b['category_id'], current_month)
        percent = (spent / b['amount'] * 100) if b['amount'] > 0 else 0
        budget_details.append({
            'category_name': b['category_name'],
            'amount': b['amount'],
            'spent': spent,
            'percent': percent,
            'over': spent > b['amount']
        })
    recent_records = Record.get_recent(user_id, 5)
    accounts = Account.get_by_user(user_id)
    summary = Account.get_summary(user_id)
    pending_reminders = Reminder.get_pending(user_id)
    RecurringTransaction.process_due(user_id)
    Record.cleanup_trash()
    return render_template('index.html',
                           income_total=income_total,
                           expense_total=expense_total,
                           budget_details=budget_details,
                           recent_records=recent_records,
                           accounts=accounts,
                           summary=summary,
                           pending_reminders=pending_reminders)

# ─── 用户相关 ──────────────────────────────────────────
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if User.find_by_username(username):
            flash('用户名已存在')
            return redirect(url_for('register'))
        User.create(username, password)
        flash('注册成功，请登录')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.find_by_username(username)
        if user and User.check_password(user, password):
            if not User.is_hashed(user['password_hash']):
                User.update_password(user['id'], password)
            session['user_id'] = user['id']
            session['username'] = user['username']
            return redirect(url_for('index'))
        flash('用户名或密码错误')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/theme', methods=['POST'])
def toggle_theme():
    if 'user_id' not in session:
        return {'ok': False}, 401
    dark = int(request.form.get('dark_mode', 0))
    UserSettings.update(session['user_id'], dark_mode=dark)
    return {'ok': True}

# ─── 个人中心 ──────────────────────────────────────────
@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        old_pw = request.form.get('old_password', '')
        new_pw = request.form.get('new_password', '')
        confirm_pw = request.form.get('confirm_password', '')
        user = User.find_by_id(session['user_id'])
        if not user or not User.check_password(user, old_pw):
            flash('原密码错误')
        elif new_pw != confirm_pw:
            flash('两次输入的新密码不一致')
        elif len(new_pw) < 6:
            flash('新密码长度不能少于6位')
        else:
            User.update_password(session['user_id'], new_pw)
            flash('密码修改成功')
        return redirect(url_for('profile'))
    return render_template('profile.html')

# ─── 账户管理 ──────────────────────────────────────────
@app.route('/accounts')
def accounts():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    accounts_list = Account.get_by_user(user_id)
    archived = Account.get_archived(user_id)
    summary = Account.get_summary(user_id)
    return render_template('accounts.html', accounts=accounts_list, archived=archived, summary=summary)

@app.route('/account/add', methods=['POST'])
def add_account():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    Account.create(session['user_id'],
                   name=request.form['name'],
                   type_=request.form['type'],
                   balance=float(request.form.get('balance', 0)),
                   icon=request.form.get('icon', '💳'),
                   color=request.form.get('color', '#6366f1'))
    flash('账户添加成功')
    return redirect(url_for('accounts'))

@app.route('/account/edit/<int:account_id>', methods=['POST'])
def edit_account(account_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    Account.update(account_id, session['user_id'],
                   name=request.form['name'],
                   type_=request.form['type'],
                   icon=request.form.get('icon', '💳'),
                   color=request.form.get('color', '#6366f1'))
    Account.update_balance(account_id, session['user_id'])
    flash('账户已更新')
    return redirect(url_for('accounts'))

@app.route('/account/archive/<int:account_id>')
def archive_account(account_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    Account.archive(account_id, session['user_id'])
    flash('账户已归档')
    return redirect(url_for('accounts'))

@app.route('/account/restore/<int:account_id>')
def restore_account(account_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    Account.restore(account_id, session['user_id'])
    flash('账户已恢复')
    return redirect(url_for('accounts'))

# ─── 转账 ─────────────────────────────────────────────
@app.route('/transfer', methods=['GET', 'POST'])
def transfer():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    if request.method == 'POST':
        from_aid = int(request.form['from_account'])
        to_aid = int(request.form['to_account'])
        if from_aid == to_aid:
            flash('转出和转入账户不能相同')
            return redirect(url_for('transfer'))
        amount = float(request.form['amount'])
        if amount <= 0:
            flash('金额必须大于0')
            return redirect(url_for('transfer'))
        date = request.form['date']
        desc = request.form.get('description', '')
        Transfer.create(user_id, from_aid, to_aid, amount, date, desc)
        flash('转账成功')
        return redirect(url_for('transfer'))
    accounts_list = Account.get_by_user(user_id)
    transfers = Transfer.get_by_user(user_id)
    return render_template('transfer.html', accounts=accounts_list, transfers=transfers)

@app.route('/transfer/delete/<int:transfer_id>')
def delete_transfer(transfer_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    Transfer.delete(transfer_id, session['user_id'])
    flash('转账记录已删除')
    return redirect(url_for('transfer'))

# ─── 标签管理 ──────────────────────────────────────────
@app.route('/tags', methods=['GET', 'POST'])
def manage_tags():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    if request.method == 'POST':
        name = request.form['name'].strip()
        color = request.form.get('color', '#6366f1')
        if name:
            Tag.create(user_id, name, color)
            flash('标签添加成功')
        return redirect(url_for('manage_tags'))
    tags = Tag.get_by_user(user_id)
    return render_template('tags.html', tags=tags)

@app.route('/tag/edit/<int:tag_id>', methods=['POST'])
def edit_tag(tag_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    name = request.form['name'].strip()
    color = request.form.get('color', '#6366f1')
    if name:
        Tag.update(tag_id, session['user_id'], name, color)
        flash('标签已更新')
    return redirect(url_for('manage_tags'))

@app.route('/tag/delete/<int:tag_id>')
def delete_tag(tag_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    Tag.delete(tag_id, session['user_id'])
    flash('标签已删除')
    return redirect(url_for('manage_tags'))

# ─── 周期性账单 ──────────────────────────────────────
@app.route('/recurring', methods=['GET', 'POST'])
def recurring():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    if request.method == 'POST':
        RecurringTransaction.create(
            user_id=user_id,
            account_id=int(request.form['account_id']) if request.form.get('account_id') else None,
            type_=request.form['type'],
            category_id=int(request.form['category_id']),
            amount=float(request.form['amount']),
            description=request.form.get('description', ''),
            frequency=request.form['frequency'],
            interval_value=int(request.form.get('interval_value', 1)),
            next_date=request.form['next_date'],
            end_date=request.form.get('end_date') or None
        )
        flash('周期性账单已添加')
        return redirect(url_for('recurring'))
    categories = Category.query_all()
    accounts_list = Account.get_by_user(user_id)
    recurrings = RecurringTransaction.get_by_user(user_id)
    return render_template('recurring.html', categories=categories, accounts=accounts_list, recurrings=recurrings)

@app.route('/recurring/edit/<int:rt_id>', methods=['POST'])
def edit_recurring(rt_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    RecurringTransaction.update(
        rt_id, session['user_id'],
        account_id=int(request.form['account_id']) if request.form.get('account_id') else None,
        type_=request.form['type'],
        category_id=int(request.form['category_id']),
        amount=float(request.form['amount']),
        description=request.form.get('description', ''),
        frequency=request.form['frequency'],
        interval_value=int(request.form.get('interval_value', 1)),
        next_date=request.form['next_date'],
        end_date=request.form.get('end_date') or None,
        active=int(request.form.get('active', 1))
    )
    flash('周期性账单已更新')
    return redirect(url_for('recurring'))

@app.route('/recurring/<int:rt_id>/data')
def get_recurring_data(rt_id):
    if 'user_id' not in session:
        return {'error': 'unauthorized'}, 401
    data = RecurringTransaction.get_by_id(rt_id, session['user_id'])
    if not data:
        return {'error': 'not found'}, 404
    return dict(data)

@app.route('/recurring/delete/<int:rt_id>')
def delete_recurring(rt_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    RecurringTransaction.delete(rt_id, session['user_id'])
    flash('周期性账单已删除')
    return redirect(url_for('recurring'))

# ─── 提醒中心 ──────────────────────────────────────────
@app.route('/reminders', methods=['GET', 'POST'])
def reminders():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    if request.method == 'POST':
        Reminder.create(
            user_id=user_id,
            title=request.form['title'],
            amount=float(request.form.get('amount', 0)),
            due_date=request.form.get('due_date') or None,
            repeat_type=request.form.get('repeat_type', 'none'),
            category_id=int(request.form['category_id']) if request.form.get('category_id') else None,
            account_id=int(request.form['account_id']) if request.form.get('account_id') else None,
            note=request.form.get('note', '')
        )
        flash('提醒已添加')
        return redirect(url_for('reminders'))
    categories = Category.query_all()
    accounts_list = Account.get_by_user(user_id)
    reminders_list = Reminder.get_by_user(user_id)
    return render_template('reminders.html', categories=categories, accounts=accounts_list, reminders=reminders_list)

@app.route('/reminder/paid/<int:reminder_id>')
def mark_paid(reminder_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    Reminder.mark_paid(reminder_id, session['user_id'])
    flash('已标记为已处理')
    return redirect(url_for('reminders'))

@app.route('/reminder/edit/<int:reminder_id>', methods=['POST'])
def edit_reminder(reminder_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    Reminder.update(reminder_id, user_id,
                    title=request.form['title'],
                    amount=float(request.form.get('amount', 0)),
                    due_date=request.form.get('due_date') or None,
                    repeat_type=request.form.get('repeat_type', 'none'),
                    category_id=int(request.form['category_id']) if request.form.get('category_id') else None,
                    account_id=int(request.form['account_id']) if request.form.get('account_id') else None,
                    note=request.form.get('note', ''))
    flash('提醒已更新')
    return redirect(url_for('reminders'))

@app.route('/reminder/delete/<int:reminder_id>')
def delete_reminder(reminder_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    Reminder.delete(reminder_id, session['user_id'])
    flash('提醒已删除')
    return redirect(url_for('reminders'))

# ─── 记录管理 ──────────────────────────────────────────
@app.route('/record/add', methods=['GET', 'POST'])
def add_record():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        user_id = session['user_id']
        type_ = request.form['type']
        category_id = int(request.form['category_id'])
        amount = float(request.form['amount'])
        description = request.form.get('description', '')
        date = request.form['date']
        account_id = int(request.form['account_id']) if request.form.get('account_id') else None
        image_path = ''
        if 'image' in request.files and request.files['image'].filename:
            f = request.files['image']
            ext = f.filename.rsplit('.', 1)[-1].lower()
            filename = f'{datetime.now().strftime("%Y%m%d%H%M%S")}_{user_id}.{ext}'
            path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            f.save(path)
            image_path = path
        rid = Record.create(user_id, type_, category_id, amount, description, date, account_id, image_path)
        tag_ids = request.form.getlist('tags')
        if tag_ids:
            Record.set_tags(rid, [int(t) for t in tag_ids])
        flash('记录已添加')
        return redirect(url_for('records'))
    categories = Category.query_all()
    accounts_list = Account.get_by_user(session['user_id'])
    tags = Tag.get_by_user(session['user_id'])
    settings_data = get_settings()
    return render_template('add_record.html', categories=categories, accounts=accounts_list, tags=tags,
                           default_account_id=settings_data['default_account_id'])

@app.route('/records')
def records():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    page = int(request.args.get('page', 1))
    ps = get_settings()['page_size']
    keyword = request.args.get('keyword', '').strip()
    category_id = request.args.get('category_id', '')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    type_ = request.args.get('type', '')
    account_id = request.args.get('account_id', '')
    tag_id = request.args.get('tag_id', '')
    status_filter = request.args.get('status', '')
    amount_min = request.args.get('amount_min', '').strip()
    amount_max = request.args.get('amount_max', '').strip()
    order_by = request.args.get('order_by', 'date_desc')
    records_list, total = Record.search(user_id,
                                        keyword=keyword if keyword else None,
                                        category_id=int(category_id) if category_id else None,
                                        start_date=start_date if start_date else None,
                                        end_date=end_date if end_date else None,
                                        type_=type_ if type_ else None,
                                        account_id=int(account_id) if account_id else None,
                                        tag_id=int(tag_id) if tag_id else None,
                                        status=status_filter if status_filter else None,
                                        amount_min=float(amount_min) if amount_min else None,
                                        amount_max=float(amount_max) if amount_max else None,
                                        order_by=order_by if order_by else 'date_desc',
                                        page=page, page_size=ps)
    total_pages = max(1, (total + ps - 1) // ps)
    categories = Category.query_all()
    accounts_list = Account.get_by_user(user_id)
    tags = Tag.get_by_user(user_id)
    records_list = [dict(r) for r in records_list]
    tag_map = Record.get_tags_batch([r['id'] for r in records_list])
    for r in records_list:
        r['tags'] = tag_map.get(r['id'], [])
    filter_income, filter_expense = Record.search_totals(
        user_id,
        keyword=keyword if keyword else None,
        category_id=int(category_id) if category_id else None,
        start_date=start_date if start_date else None,
        end_date=end_date if end_date else None,
        type_=type_ if type_ else None,
        account_id=int(account_id) if account_id else None,
        tag_id=int(tag_id) if tag_id else None,
        status=status_filter if status_filter else None,
        amount_min=float(amount_min) if amount_min else None,
        amount_max=float(amount_max) if amount_max else None)
    return render_template('records.html',
                           records=records_list,
                           categories=categories,
                           accounts=accounts_list,
                           tags=tags,
                           keyword=keyword,
                           category_id=category_id,
                           start_date=start_date,
                           end_date=end_date,
                           type_=type_,
                           account_id=account_id,
                           tag_id=tag_id,
                           status=status_filter,
                           amount_min=amount_min,
                           amount_max=amount_max,
                           order_by=order_by,
                           page=page,
                           total_pages=total_pages,
                           total=total,
                           filter_income=filter_income,
                           filter_expense=filter_expense)

@app.route('/record/edit/<int:record_id>', methods=['POST'])
def edit_record(record_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    record = Record.get_by_id_and_user(record_id, user_id)
    if not record:
        flash('记录不存在')
        return redirect(url_for('records'))
    type_ = request.form['type']
    category_id = int(request.form['category_id'])
    amount = float(request.form['amount'])
    description = request.form.get('description', '')
    date = request.form['date']
    account_id = int(request.form['account_id']) if request.form.get('account_id') else None
    status = request.form.get('status', record['status'])
    image_path = record['image_path']
    if 'image' in request.files and request.files['image'].filename:
        f = request.files['image']
        ext = f.filename.rsplit('.', 1)[-1].lower()
        filename = f'{datetime.now().strftime("%Y%m%d%H%M%S")}_{user_id}.{ext}'
        path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        f.save(path)
        image_path = path
        if record['image_path'] and os.path.exists(record['image_path']):
            try:
                os.remove(record['image_path'])
            except:
                pass
    Record.update(record_id, user_id, type_, category_id, amount, description, date, account_id, image_path, status)
    tag_ids = request.form.getlist('tags')
    Record.set_tags(record_id, [int(t) for t in tag_ids] if tag_ids else [])
    flash('记录已更新')
    return redirect(url_for('records'))

@app.route('/record/delete/<int:record_id>')
def delete_record(record_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    Record.delete(record_id, session['user_id'])
    flash('记录已移入回收站')
    return redirect(url_for('records'))

@app.route('/record/image/<int:record_id>')
def record_image(record_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    record = Record.get_by_id_and_user(record_id, session['user_id'])
    if not record or not record['image_path'] or not os.path.exists(record['image_path']):
        abort(404)
    return send_file(record['image_path'])

# ─── 回收站 ──────────────────────────────────────────
@app.route('/trash')
def trash():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    trashed = Record.get_trashed(user_id)
    trashed = [dict(r) for r in trashed]
    tag_map = Record.get_tags_batch([r['id'] for r in trashed])
    for r in trashed:
        r['tags'] = tag_map.get(r['id'], [])
    return render_template('trash.html', records=trashed)

@app.route('/record/restore/<int:record_id>')
def restore_record(record_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    Record.restore(record_id, session['user_id'])
    flash('记录已恢复')
    return redirect(url_for('trash'))

@app.route('/record/permanent-delete/<int:record_id>')
def permanent_delete_record(record_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    Record.permanent_delete(record_id, session['user_id'])
    flash('记录已永久删除')
    return redirect(url_for('trash'))

@app.route('/trash/empty')
def empty_trash():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    count = Record.cleanup_trash()
    flash(f'已清空回收站（{count} 条）')
    return redirect(url_for('trash'))

# ─── 批量操作 ──────────────────────────────────────────
@app.route('/records/batch-delete', methods=['POST'])
def batch_delete_records():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    record_ids = request.form.getlist('record_ids[]')
    record_ids = [int(i) for i in record_ids if i.isdigit()]
    if record_ids:
        Record.batch_delete(session['user_id'], record_ids)
        flash(f'已删除 {len(record_ids)} 条记录')
    return redirect(url_for('records'))

@app.route('/records/batch-edit', methods=['POST'])
def batch_edit_records():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    record_ids = request.form.getlist('record_ids[]')
    record_ids = [int(i) for i in record_ids if i.isdigit()]
    if not record_ids:
        flash('请选择要修改的记录')
        return redirect(url_for('records'))
    kwargs = {}
    if request.form.get('category_id'):
        kwargs['category_id'] = int(request.form['category_id'])
    if request.form.get('account_id'):
        acc_id = int(request.form['account_id'])
        kwargs['account_id'] = acc_id if acc_id > 0 else None
    if request.form.get('type'):
        kwargs['type'] = request.form['type']
    if request.form.get('status'):
        kwargs['status'] = request.form['status']
    if kwargs:
        Record.batch_update(session['user_id'], record_ids, **kwargs)
        flash(f'已更新 {len(record_ids)} 条记录')
    return redirect(url_for('records'))

@app.route('/records/export')
def export_records():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    keyword = request.args.get('keyword', '').strip()
    category_id = request.args.get('category_id', '')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    type_ = request.args.get('type', '')
    account_id = request.args.get('account_id', '')
    amount_min = request.args.get('amount_min', '').strip()
    amount_max = request.args.get('amount_max', '').strip()
    order_by = request.args.get('order_by', 'date_desc')
    records_list, _ = Record.search(user_id,
                                    keyword=keyword if keyword else None,
                                    category_id=int(category_id) if category_id else None,
                                    start_date=start_date if start_date else None,
                                    end_date=end_date if end_date else None,
                                    type_=type_ if type_ else None,
                                    account_id=int(account_id) if account_id else None,
                                    amount_min=float(amount_min) if amount_min else None,
                                    amount_max=float(amount_max) if amount_max else None,
                                    order_by=order_by if order_by else 'date_desc',
                                    page=1, page_size=999999)
    records_list = [dict(r) for r in records_list]
    tag_map = Record.get_tags_batch([r['id'] for r in records_list])
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['日期', '类型', '分类', '账户', '金额', '备注', '标签', '状态'])
    for r in records_list:
        tag_names = '、'.join(t['name'] for t in tag_map.get(r['id'], []))
        writer.writerow([r['date'],
                         '收入' if r['type'] == 'income' else '支出',
                         r['category_name'],
                         r.get('account_name', ''),
                         r['amount'],
                         r['description'],
                         tag_names,
                         '已对账' if r['status'] == 'cleared' else '未对账'])
    output.seek(0)
    data = '\ufeff' + output.getvalue()
    return Response(data,
                    mimetype='text/csv; charset=utf-8',
                    headers={'Content-Disposition': 'attachment;filename=records.csv'})

# ─── 分类管理 ──────────────────────────────────────────
@app.route('/categories', methods=['GET', 'POST'])
def manage_categories():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        name = request.form['name'].strip()
        type_ = request.form['type']
        if name:
            if Category.get_by_name_type(name, type_):
                flash('该分类已存在')
            else:
                Category.create(name, type_)
                flash('分类添加成功')
        else:
            flash('分类名称不能为空')
        return redirect(url_for('manage_categories'))
    categories = Category.query_all()
    return render_template('categories.html', categories=categories)

@app.route('/category/edit/<int:category_id>', methods=['POST'])
def edit_category(category_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    name = request.form['name'].strip()
    type_ = request.form['type']
    if not name:
        flash('分类名称不能为空')
    else:
        dup = Category.get_by_name_type(name, type_)
        if dup and dup['id'] != category_id:
            flash('该分类已存在')
        else:
            Category.rename(category_id, name, type_)
            flash('分类已更新')
    return redirect(url_for('manage_categories'))

@app.route('/category/delete/<int:category_id>')
def delete_category(category_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    if Category.is_used(category_id):
        flash('该分类已被使用，无法删除（可先修改相关记录）')
    else:
        Category.delete(category_id)
        flash('分类已删除')
    return redirect(url_for('manage_categories'))

# ─── 预算 ──────────────────────────────────────────────
@app.route('/budget', methods=['GET', 'POST'])
def budget():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    month = request.form.get('month') or request.args.get('month') or datetime.now().strftime('%Y-%m')
    if request.method == 'POST':
        category_id = int(request.form['category_id'])
        amount = float(request.form['amount'])
        Budget.set_budget(user_id, category_id, month, amount)
        flash('预算已更新')
        return redirect(url_for('budget', month=month))
    expense_categories = [c for c in Category.query_all() if c['type'] == 'expense']
    budgets = {b['category_id']: b['amount'] for b in Budget.get_by_user_month(user_id, month)}
    spent_dict = {}
    total_budget = 0
    total_spent = 0
    for cat in expense_categories:
        spent = Record.get_month_category_total(user_id, 'expense', cat['id'], month)
        spent_dict[cat['id']] = spent
        if budgets.get(cat['id'], 0) > 0:
            total_budget += budgets[cat['id']]
            total_spent += spent
    try:
        ym = datetime.strptime(month, '%Y-%m')
    except ValueError:
        ym = datetime.now().replace(day=1)
    prev_month = (ym.replace(day=1) - timedelta(days=1)).strftime('%Y-%m')
    next_month = (ym.replace(day=28) + timedelta(days=7)).strftime('%Y-%m')
    return render_template('budget.html',
                           categories=expense_categories,
                           budgets=budgets,
                           month=month,
                           prev_month=prev_month,
                           next_month=next_month,
                           total_budget=total_budget,
                           total_spent=total_spent,
                           spent_dict=spent_dict)

# ─── 统计 ──────────────────────────────────────────────
@app.route('/stats')
def stats():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    current_month = datetime.now().strftime('%Y-%m')
    expense_cats = [c for c in Category.query_all() if c['type'] == 'expense']

    labels = []; data = []; colors = []
    palette = ['#10b981','#ef4444','#f59e0b','#6366f1','#ec4899','#06b6d4','#f97316','#14b8a6','#8b5cf6','#e11d48']
    idx = 0
    for cat in expense_cats:
        total = Record.get_month_category_total(user_id, 'expense', cat['id'], current_month)
        if total > 0:
            labels.append(cat['name'])
            data.append(total)
            colors.append(palette[idx % len(palette)])
            idx += 1

    now = datetime.now()
    months = _last_n_months(6)
    income_trend = [Record.get_month_total(user_id, 'income', m) for m in months]
    expense_trend = [Record.get_month_total(user_id, 'expense', m) for m in months]

    year = now.year; month = now.month
    cal = calendar.monthcalendar(year, month)
    daily_expenses = {}
    for day in range(1, calendar.monthrange(year, month)[1] + 1):
        date_str = f"{year}-{month:02d}-{day:02d}"
        amount = Record.get_day_total(user_id, 'expense', date_str)
        if amount > 0:
            opacity = 0.3 + min(amount / 200, 0.7)
        else:
            opacity = 0
        daily_expenses[day] = {'amount': amount, 'opacity': round(opacity, 2)}

    net_worth_trend = [income_trend[i] - expense_trend[i] for i in range(len(months))]

    return render_template('stats.html',
                           labels=labels, data=data, colors=colors,
                           months=months, income_trend=income_trend, expense_trend=expense_trend,
                           net_worth_trend=net_worth_trend,
                           year=year, month=month, cal=cal,
                           daily_expenses=daily_expenses,
                           month_name=now.strftime('%Y年%m月'))

# ─── 年度报告 ──────────────────────────────────────────
@app.route('/annual_report')
def annual_report():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    year = request.args.get('year', datetime.now().year, type=int)
    summary = Record.get_year_summary(user_id, year)
    total_income = sum(m['income'] for m in summary['months'])
    total_expense = sum(m['expense'] for m in summary['months'])
    return render_template('annual_report.html',
                           year=year,
                           months=summary['months'],
                           top_categories=summary['top_categories'],
                           total_income=total_income,
                           total_expense=total_expense,
                           balance=total_income - total_expense)

# ─── 用户设置 ──────────────────────────────────────────
@app.route('/settings', methods=['GET', 'POST'])
def settings():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    if request.method == 'POST':
        UserSettings.update(user_id,
                            currency_symbol=request.form.get('currency_symbol', '¥'),
                            default_account_id=int(request.form['default_account_id']) if request.form.get('default_account_id') else None,
                            dark_mode=int(request.form.get('dark_mode', 0)),
                            page_size=int(request.form.get('page_size', 50)))
        flash('设置已保存')
        return redirect(url_for('settings'))
    settings_data = UserSettings.get(user_id)
    accounts_list = Account.get_by_user(user_id)
    return render_template('settings.html', settings=settings_data, accounts=accounts_list)

if __name__ == '__main__':
    app.run(debug=True)
