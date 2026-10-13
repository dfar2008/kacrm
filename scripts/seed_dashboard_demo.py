"""Seed a polished demo dataset so the dashboard charts look professional.

DEV-ONLY (guards on a *_dev database). Idempotent: re-running first removes
its own tagged potentials and re-applies account/user/department updates.

What it creates:
  · 4 named sales reps across 销售部 / 华东大区 / 华南大区
  · ~70 accounts repurposed with realistic names + industries + created_at
    spread over the last 9 months (fixes 客户行业分布 pie + 月度新增客户走势)
  · ~70 potentials spread over 9 months incl. current month, across the reps,
    departments, pipeline stages and amounts (fixes 商机漏斗 / 月度商机走势 /
    本月新增商机 / 销售员·部门 柱图)

Usage:  ALEMBIC_DATABASE_URL=...dev  .venv/bin/python scripts/seed_dashboard_demo.py
"""
from __future__ import annotations

import os
import random
import sys
from datetime import datetime, timedelta

import psycopg

random.seed(42)  # deterministic demo

URL = os.environ.get("ALEMBIC_DATABASE_URL") or os.environ.get("HERMES_CRM_DATABASE_URL") or ""
URL = URL.replace("postgresql+psycopg", "postgresql")
if not URL or not URL.rsplit("/", 1)[-1].split("?")[0].endswith("_dev"):
    sys.exit(f"refusing to run: not a *_dev database (got {URL.rsplit('/',1)[-1]!r})")

NOW = datetime(2026, 6, 3, 10, 0, 0)
TAG = "[dash-demo]"

# (user_id, realname, dept_id)
REPS = [(1453, "张可", 2), (1454, "王刚", 3), (1455, "李娜", 4), (1456, "赵强", 2)]
DEPTS = {2: "销售部", 3: "华东大区", 4: "华南大区"}

INDUSTRIES = [
    "制造业", "科技", "金融", "医疗健康", "教育培训", "物流供应链",
    "快消品", "能源化工", "建筑工程", "汽车", "互联网", "生物医药",
]
COMPANIES = [
    "鼎盛金融", "江南精密", "星辰科技", "东方软件", "瑞策智能", "华勤制造",
    "云图数据", "明远医疗", "博越能源", "卓尔教育", "顺达物流", "嘉合快消",
    "中科生物", "宏远汽车", "天工建筑", "联创网络", "锐进电子", "广济药业",
    "恒泰重工", "睿达半导体", "新元新材料", "海岳航运", "金桥地产", "优品消费",
    "智云互联", "格物科技", "盛海食品", "远景新能源", "康桥医械", "翔宇教育",
    "立信会计软件", "拓维信息", "安泰钢铁", "美程汽配", "环宇建工", "数擎云",
    "百川化工", "晨光文具集团", "仁和健康", "九州物流", "鑫源矿业", "维科电气",
    "同方智慧", "光合生物", "启明星辰安全", "鼎晖投资", "易联众", "正大食品",
    "三一重装", "比特视觉",
]
PROJECTS = [
    "CRM 系统采购", "数字化升级项目", "ERP 集成", "数据中台建设", "办公系统升级",
    "营销云平台", "智能客服项目", "BI 分析平台", "供应链系统", "私有化部署",
    "AI 助手试点", "年度续约 + 扩容", "3D 设计软件采购", "安全合规改造", "门户重构",
]
# milestone -> (weight, probability)
STAGES = [
    ("initial_contact", 30, 10),
    ("needs_analysis", 20, 25),
    ("proposal_sent", 18, 45),
    ("negotiation", 15, 65),
    ("closing", 9, 85),
    ("won", 8, 100),
]
STAGE_POOL = [s for s, w, _ in STAGES for _ in range(w)]
PROB = {s: p for s, _, p in STAGES}


def month_starts(n: int):
    """Return list of (year, month) for the last n months including current."""
    out = []
    y, m = NOW.year, NOW.month
    for _ in range(n):
        out.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def spread_dt(ym):
    y, m = ym
    day = random.randint(1, 28)
    if (y, m) == (NOW.year, NOW.month):
        day = random.randint(1, NOW.day)
    return datetime(y, m, day, random.randint(9, 18), random.randint(0, 59))


def main():
    conn = psycopg.connect(URL, autocommit=False)
    cur = conn.cursor()

    # 1) departments
    for did, name in DEPTS.items():
        cur.execute("UPDATE departments SET name=%s WHERE id=%s", (name, did))

    # 2) sales reps
    for uid, name, dept in REPS:
        cur.execute(
            "UPDATE users SET realname=%s, department_id=%s, deleted=false WHERE id=%s",
            (name, dept, uid),
        )

    # 3) accounts — repurpose test rows into realistic demo accounts
    cur.execute(
        """
        SELECT accounts_id, accounts_name FROM accounts
        WHERE deleted=false AND (
            description LIKE %s OR industry IS NULL OR industry=''
            OR accounts_name LIKE 'ToolQA%%' OR accounts_name LIKE '测试%%'
            OR accounts_name LIKE 'Test%%' OR accounts_name LIKE 'QA%%'
            OR accounts_name LIKE '%%-v2')
        ORDER BY accounts_id
        LIMIT 90
        """,
        (f"%{TAG}%",),
    )
    rows = cur.fetchall()
    months9 = month_starts(9)
    demo_accounts = []  # (id, name)

    def is_test_name(nm: str) -> bool:
        nm = nm or ""
        return (not nm.strip()) or any(
            nm.startswith(p) for p in ("ToolQA", "测试", "Test", "QA")
        ) or nm.endswith("-v2") or TAG in nm

    for i, (aid, cur_name) in enumerate(rows):
        if is_test_name(cur_name):
            name = COMPANIES[i % len(COMPANIES)]
            if i >= len(COMPANIES):
                name = f"{name}{i // len(COMPANIES) + 1}"
        else:
            name = cur_name  # keep real names
        ind = INDUSTRIES[i % len(INDUSTRIES)]  # even spread across industries
        ym = random.choices(months9, weights=[1, 1, 2, 2, 3, 3, 4, 4, 5])[0]
        created = spread_dt(ym)
        cur.execute(
            "UPDATE accounts SET accounts_name=%s, industry=%s, created_at=%s, description=%s WHERE accounts_id=%s",
            (name, ind, created, f"{TAG} demo account", aid),
        )
        demo_accounts.append((aid, name))

    if not demo_accounts:
        conn.rollback()
        sys.exit("no repurposable accounts found")

    # 4) potentials — remove our prior demo rows, reinsert a fresh spread
    cur.execute("DELETE FROM potentials WHERE description LIKE %s", (f"%{TAG}%",))

    # also re-home existing real potentials onto the named reps so the
    # salesperson/department charts read cleanly
    for idx, (uid, _, dept) in enumerate(REPS):
        cur.execute(
            "UPDATE potentials SET owner_by=%s, department_id=%s "
            "WHERE deleted=false AND (potentials_id %% %s)=%s AND description IS DISTINCT FROM %s",
            (uid, dept, len(REPS), idx, f"{TAG} demo"),
        )

    n = 72
    for i in range(n):
        aid, aname = random.choice(demo_accounts)
        uid, _, dept = random.choice(REPS)
        stage = random.choice(STAGE_POOL)
        # amount 80k .. 1.5M, rounded to 1k
        amount = random.randint(80, 1500) * 1000
        # spread, growth toward recent months; ensure current month has some
        ym = random.choices(months9, weights=[2, 2, 3, 3, 4, 5, 6, 7, 8])[0]
        created = spread_dt(ym)
        pname = f"{aname} {random.choice(PROJECTS)}"
        cur.execute(
            """
            INSERT INTO potentials
              (potentials_name, milestone, amount, probability, owner_by,
               department_id, created_by, updated_by, accounts_id,
               created_at, updated_at, deleted, description)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,false,%s)
            """,
            (pname, stage, amount, PROB[stage], uid, dept, uid, uid, aid,
             created, created, f"{TAG} demo"),
        )

    conn.commit()
    cur.execute("SELECT count(*) FROM potentials WHERE deleted=false")
    total = cur.fetchone()[0]
    print(f"OK: {len(demo_accounts)} demo accounts, {n} demo potentials inserted, {total} potentials total")
    conn.close()


if __name__ == "__main__":
    main()
