"""
sheets.py
Everything that talks to your Google Sheet lives here.
The connection is made lazily (on first use) so that if something in your setup
is missing, you get a friendly message instead of a crash on startup.
"""

import os
import json
import datetime
import gspread

import config

_sh = None


def _book():
    global _sh
    if _sh is None:
        if os.path.exists(config.GOOGLE_SERVICE_ACCOUNT_FILE):
            gc = gspread.service_account(filename=config.GOOGLE_SERVICE_ACCOUNT_FILE)
        elif config.GOOGLE_CREDENTIALS_JSON:
            gc = gspread.service_account_from_dict(json.loads(config.GOOGLE_CREDENTIALS_JSON))
        else:
            raise RuntimeError("No Google credentials found (file or GOOGLE_CREDENTIALS_JSON).")
        _sh = gc.open_by_key(config.GOOGLE_SHEET_ID)
    return _sh


def _ws(title):
    return _book().worksheet(title)


def _row_for(ws, col_index, value):
    """Return the 1-based row whose cell in `col_index` exactly equals `value`."""
    vals = ws.col_values(col_index)
    target = str(value).strip().lower()
    for i, v in enumerate(vals, start=1):
        if str(v).strip().lower() == target:
            return i
    return None


# ---------- Accounts ----------

def get_accounts():
    rows = _ws(config.SHEET_ACCOUNTS).get_all_records()
    accounts = []
    for r in rows:
        name = str(r.get("Account Name", "")).strip()
        if not name:
            continue
        cur = r.get("Current Balance", "")
        if cur in ("", None):
            cur = r.get("Starting Balance", 0)
        accounts.append({
            "name": name,
            "type": str(r.get("Account Type", "")).strip(),
            "currency": (str(r.get("Currency", "PHP")).strip().upper() or "PHP"),
            "balance": _to_float(cur),
        })
    return accounts


def get_account(name):
    for a in get_accounts():
        if a["name"].lower() == str(name).strip().lower():
            return a
    return None


def update_account_balance(name, new_balance):
    ws = _ws(config.SHEET_ACCOUNTS)
    header = ws.row_values(1)
    name_col = header.index("Account Name") + 1
    bal_col = header.index("Current Balance") + 1
    upd_col = header.index("Last Updated") + 1
    row = _row_for(ws, name_col, name)
    if not row:
        return
    ws.update_cell(row, bal_col, round(new_balance, 2))
    ws.update_cell(row, upd_col, _now())


# ---------- Transactions ----------

TX_HEADERS = [
    "Transaction ID", "Created Timestamp", "Transaction Date", "Type", "Amount",
    "Currency", "Exchange Rate", "PHP Equivalent", "Category", "Client",
    "Income Source", "Account", "Destination Account", "Description",
    "Telegram User ID", "Status",
]


def append_transaction(tx):
    row = [tx.get(h, "") for h in TX_HEADERS]
    _ws(config.SHEET_TRANSACTIONS).append_row(row, value_input_option="USER_ENTERED")
    return tx.get("Transaction ID", "")


def get_recent_transactions(n=10):
    rows = _ws(config.SHEET_TRANSACTIONS).get_all_records()
    return rows[-n:][::-1]


def get_month_transactions(year_month):
    rows = _ws(config.SHEET_TRANSACTIONS).get_all_records()
    out = []
    for r in rows:
        d = str(r.get("Transaction Date", ""))
        if d.startswith(year_month) and str(r.get("Status", "")).lower() != "reversed":
            out.append(r)
    return out


def mark_reversed(transaction_id):
    ws = _ws(config.SHEET_TRANSACTIONS)
    header = ws.row_values(1)
    id_col = header.index("Transaction ID") + 1
    status_col = header.index("Status") + 1
    row = _row_for(ws, id_col, transaction_id)
    if row:
        ws.update_cell(row, status_col, "Reversed")


# ---------- Settings ----------

def get_setting(key):
    rows = _ws(config.SHEET_SETTINGS).get_all_records()
    for r in rows:
        if str(r.get("Setting", "")).strip() == key:
            return str(r.get("Value", "")).strip()
    return None


def set_setting(key, value):
    ws = _ws(config.SHEET_SETTINGS)
    header = ws.row_values(1)
    key_col = header.index("Setting") + 1
    val_col = header.index("Value") + 1
    row = _row_for(ws, key_col, key)
    if row:
        ws.update_cell(row, val_col, str(value))
    else:
        ws.append_row([key, str(value)], value_input_option="USER_ENTERED")


# ---------- Lookup lists (for the AI) ----------

def get_categories():
    rows = _ws(config.SHEET_CATEGORIES).get_all_records()
    expense, income, sources = [], [], []
    for r in rows:
        name = str(r.get("Name", "")).strip()
        typ = str(r.get("Type", "")).strip().lower()
        if not name:
            continue
        if "income source" in typ:
            sources.append(name)
        elif "income" in typ:
            income.append(name)
        else:
            expense.append(name)
    return expense, income, sources


def get_clients():
    rows = _ws(config.SHEET_CLIENTS).get_all_records()
    return [str(r.get("Client Name", "")).strip()
            for r in rows if str(r.get("Client Name", "")).strip()]


# ---------- Audit log ----------

def append_audit(action, transaction_id, detail, user_id):
    _ws(config.SHEET_AUDIT).append_row(
        [_now(), action, transaction_id, detail, str(user_id)],
        value_input_option="USER_ENTERED",
    )


# ---------- helpers ----------

def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _to_float(v):
    try:
        return float(str(v).replace(",", "").replace("₱", "").replace("$", "").strip() or 0)
    except ValueError:
        return 0.0


def add_client(name, currency="USD", notes=""):
    """Add a new client row. Returns False if already exists."""
    existing = [c.lower() for c in get_clients()]
    if name.strip().lower() in existing:
        return False
    _ws(config.SHEET_CLIENTS).append_row(
        [name.strip(), currency.upper(), notes],
        value_input_option="USER_ENTERED",
    )
    return True


def add_income_source(name):
    """Add a new income source to the Categories tab. Returns False if already exists."""
    _, _, sources = get_categories()
    if name.strip().lower() in [s.lower() for s in sources]:
        return False
    _ws(config.SHEET_CATEGORIES).append_row(
        [name.strip(), "Income Source", ""],
        value_input_option="USER_ENTERED",
    )
    return True


def add_expense_category(name):
    """Add a new expense category to the Categories tab. Returns False if already exists."""
    expense, _, _ = get_categories()
    if name.strip().lower() in [e.lower() for e in expense]:
        return False
    _ws(config.SHEET_CATEGORIES).append_row(
        [name.strip(), "Expense Category", ""],
        value_input_option="USER_ENTERED",
    )
    return True


def add_account(name, account_type, currency, starting_balance):
    """Add a new account row. Returns False if already exists."""
    existing = [a["name"].lower() for a in get_accounts()]
    if name.strip().lower() in existing:
        return False
    _ws(config.SHEET_ACCOUNTS).append_row(
        [name.strip(), account_type.strip(), currency.upper(),
         round(float(starting_balance), 2), round(float(starting_balance), 2), _now()],
        value_input_option="USER_ENTERED",
    )
    return True
