#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""为课程报告截图创建演示账号 demo 与丰富模拟数据（不影响现有用户）"""

import random
from datetime import date, timedelta
from models import (init_db, User, Account, Category, Record, Transfer, Tag,
                    Budget, RecurringTransaction, Reminder)

random.seed(42)

USERNAME = 'demo'
PASSWORD = 'demo123'

def main():
    if User.find_by_username(USERNAME):
        print('演示账号已存在，跳过创建数据')
        return

    User.create(USERNAME, PASSWORD)
    user_id = User.find_by_username(USERNAME)['id']
    print(f'演示账号创建成功 user_id={user_id}')

    cats = {c['name']: c for c in Category.query_all()}

    accs = [
        ('现金', 'cash', 1500, '💵', '#10b981'),
        ('工商银行卡', 'bank', 12800, '🏦', '#3b82f6'),
        ('支付宝', 'alipay', 2400, '🐜', '#6366f1'),
        ('微信钱包', 'wechat', 860, '💬', '#22c55e'),
        ('招商信用卡', 'credit_card', -3200, '💳', '#ef4444'),
        ('零钱通', 'virtual', 5000, '💰', '#f59e0b'),
    ]
    acc_ids = {}
    for name, t, bal, icon, color in accs:
        Account.create(user_id, name, t, bal, icon, color)
        acc_ids[name] = Account.get_by_user(user_id)[-1]['id']

    tag_names = ['日常', '出差', '生日', '紧急', '理财']
    tag_ids = {}
    for i, tn in enumerate(tag_names):
        Tag.create(user_id, tn, ['#6366f1', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6'][i])
    tag_ids = {t['name']: t['id'] for t in Tag.get_by_user(user_id)}

    today = date.today()
    year, month = today.year, today.month

    # 每月支出基础因子（模拟月份间波动）
    month_factor = {0: 1.0, 1: 0.85, 2: 1.2, 3: 0.9, 4: 1.05, 5: 1.1}

    def gen_month(ym):
        y, m = int(ym[:4]), int(ym[5:7])
        idx = (m - 3) % 6
        f = month_factor[idx]
        days = [d for d in range(1, 29)]
        # 固定大额支出
        fixed = [
            ('住房', 'expense', 2000, 1, '房租'),
            ('水电', 'expense', round(280 * f, 1), 8, '水电费'),
            ('通讯', 'expense', round(98 * f, 1), 12, '话费充值'),
            ('交通', 'expense', round(150 * f, 1), 15, '地铁卡充值'),
        ]
        for cat, typ, amt, d, desc in fixed:
            Record.create(user_id, typ, cats[cat]['id'], amt, desc,
                          f'{y}-{m:02d}-{d:02d}', acc_ids['支付宝' if cat == '水电' else '现金'])
        # 收入
        Record.create(user_id, 'income', cats['工资']['id'], 8000, '7月工资',
                      f'{y}-{m:02d}-05', acc_ids['工商银行卡'])
        Record.create(user_id, 'income', cats['兼职']['id'], round(random.uniform(800, 2000), 0),
                      '兼职收入', f'{y}-{m:02d}-15', acc_ids['支付宝'])
        Record.create(user_id, 'income', cats['投资收入']['id'], round(random.uniform(200, 900), 2),
                      '基金收益', f'{y}-{m:02d}-22', acc_ids['零钱通'])
        # 随机餐饮/交通/购物/娱乐
        n_meal = random.randint(12, 18)
        for _ in range(n_meal):
            d = random.choice(days)
            Record.create(user_id, 'expense', cats['餐饮']['id'], round(random.uniform(15, 80), 1),
                          random.choice(['早餐', '午餐', '晚餐', '聚餐', '奶茶', '外卖']),
                          f'{y}-{m:02d}-{d:02d}', acc_ids['微信钱包'])
        for _ in range(random.randint(4, 8)):
            d = random.choice(days)
            Record.create(user_id, 'expense', cats['交通']['id'], round(random.uniform(3, 25), 1),
                          random.choice(['地铁', '公交', '打车']),
                          f'{y}-{m:02d}-{d:02d}', acc_ids['微信钱包'])
        for _ in range(random.randint(2, 5)):
            d = random.choice(days)
            Record.create(user_id, 'expense', cats['购物']['id'], round(random.uniform(50, 400), 2),
                          random.choice(['超市采购', '网购', '日用品']),
                          f'{y}-{m:02d}-{d:02d}', acc_ids['支付宝'])
        for _ in range(random.randint(1, 4)):
            d = random.choice(days)
            Record.create(user_id, 'expense', cats['娱乐']['id'], round(random.uniform(30, 150), 1),
                          random.choice(['电影', 'KTV', '游戏充值', '演唱会']),
                          f'{y}-{m:02d}-{d:02d}', acc_ids['支付宝'])
        if random.random() < 0.7:
            Record.create(user_id, 'expense', cats['医疗']['id'], round(random.uniform(20, 120), 1),
                          '药店购药', f'{y}-{m:02d}-{random.choice(days):02d}', acc_ids['现金'])
        if random.random() < 0.6:
            Record.create(user_id, 'expense', cats['教育']['id'], round(random.uniform(30, 200), 1),
                          '课程/书籍', f'{y}-{m:02d}-{random.choice(days):02d}', acc_ids['支付宝'])
        if random.random() < 0.5:
            Record.create(user_id, 'expense', cats['服饰']['id'], round(random.uniform(100, 500), 2),
                          '服装鞋帽', f'{y}-{m:02d}-{random.choice(days):02d}', acc_ids['支付宝'])

    for m in range(3, month + 1):
        ym = f'{year}-{m:02d}'
        gen_month(ym)
        print(f'  生成 {ym} 数据')

    # 当月几条带标签的记录
    rid = Record.create(user_id, 'expense', cats['餐饮']['id'], 368, '部门聚餐',
                        f'{year}-{month:02d}-{min(6, 28):02d}', acc_ids['支付宝'])
    Record.set_tags(rid, [tag_ids['日常']])
    rid = Record.create(user_id, 'expense', cats['交通']['id'], 268, '出差高铁票',
                        f'{year}-{month:02d}-{min(3, 28):02d}', acc_ids['工商银行卡'])
    Record.set_tags(rid, [tag_ids['出差'], tag_ids['紧急']])
    rid = Record.create(user_id, 'expense', cats['人情']['id'], 500, '朋友生日红包',
                        f'{year}-{month:02d}-{min(10, 28):02d}', acc_ids['微信钱包'])
    Record.set_tags(rid, [tag_ids['生日']])

    # 转账
    Transfer.create(user_id, acc_ids['工商银行卡'], acc_ids['支付宝'], 1000,
                    f'{year}-{month:02d}-05', '转入支付宝')
    Transfer.create(user_id, acc_ids['工商银行卡'], acc_ids['零钱通'], 2000,
                    f'{year}-{month:02d}-12', '转入零钱通理财')

    # 预算（当月）
    for cat_name, amt in [('餐饮', 1500), ('交通', 500), ('购物', 1200), ('娱乐', 600), ('水电', 400)]:
        Budget.set_budget(user_id, cats[cat_name]['id'], f'{year}-{month:02d}', amt)

    # 周期账单（next_date 设在未来，避免自动入账）
    RecurringTransaction.create(user_id, acc_ids['工商银行卡'], 'expense', cats['住房']['id'], 2000,
                                '房租', 'monthly', 1, f'{year}-{month:02d}-28')
    RecurringTransaction.create(user_id, acc_ids['微信钱包'], 'expense', cats['娱乐']['id'], 199,
                                '视频会员', 'monthly', 1, f'{year}-{month:02d}-20')
    RecurringTransaction.create(user_id, acc_ids['工商银行卡'], 'income', cats['工资']['id'], 8000,
                                '每月工资', 'monthly', 1, f'{year}-{month:02d}-01')

    # 提醒
    Reminder.create(user_id, '信用卡还款', 3200, f'{year}-{month:02d}-15', 'monthly',
                    cats['其他']['id'], acc_ids['招商信用卡'], '招行账单日还款')
    Reminder.create(user_id, '房租缴费', 2000, f'{year}-{month:02d}-25', 'none',
                    cats['住房']['id'], acc_ids['现金'], '月底前交房租')
    Reminder.create(user_id, '话费充值', 100, f'{year}-{month:02d}-12', 'monthly',
                    cats['通讯']['id'], acc_ids['微信钱包'], '手机话费')

    # 设定最终余额（使资产汇总更直观）
    import sqlite3
    conn = sqlite3.connect('finance.db')
    for name, bal in [('现金', 1560), ('工商银行卡', 13800), ('支付宝', 2960),
                      ('微信钱包', 980), ('招商信用卡', -3200), ('零钱通', 6200)]:
        conn.execute('UPDATE account SET balance=? WHERE user_id=? AND name=?',
                     (bal, user_id, name))
    conn.commit()
    conn.close()

    print('演示数据生成完成')

if __name__ == '__main__':
    from app import app
    with app.app_context():
        main()
