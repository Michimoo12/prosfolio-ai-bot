"""
sheets.py
Everything that talks to your Google Sheet lives here.
The connection is made lazily (on first use) so that if something in your setup
is missing, you get a friendly message instead of a crash on startup.
"""

import os
import json
import time
import threading
import gspread

import config

_sh = None

# ---------- every API call: one lock + quota retry ----------
# Serialized: audit rows and the daily FX refresh run on background threads,
# and the HTTP session underneath gspread is not thread-safe.
# Retried: Google's free Sheets API allows 60 reads and 60 writes per minute
# per user. A burst — like "set all my accounts to 0" confirming 9 balance
# corrections at once — can trip that and come back as APIError 429. Backing
# off inside the same minute-window turns "⚠️ failed" into "took a few
# seconds longer."
_api_lock = threading.RLock()
_RETRY_DELAYS = [3, 8, 20]  # seconds between retries; quota window is 60s


def _api(call):
    with _api_lock:
        for attempt, delay in enumerate([0] + _RETRY_DELAYS):
            if delay:
                time.sleep(delay)
            try:
                return call()
            except gspread.exceptions.APIError as e:
                if getattr(e, "code", None) != 429 or attempt == len(_RETRY_DELAYS):
                    raise

# ---------- tiny read cache ----------
# Every incoming message used to trigger several full-tab reads (accounts,
# categories, clients, settings — some more than once). Each is a separate
# HTTP round trip to Google, which made replies sluggish and eats into the
# Sheets API's 60-reads-per-minute quota. Reads are cached for a few seconds;
# every write in this module drops the affected key, so nothing the bot
# changes is ever served stale.
_cache = {}
_CACHE_TTL = 20  # seconds


def _cached(key, fetch, ttl=None):
    ttl = _CACHE_TTL if ttl is None else ttl
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fetch()
    _cache[key] = (time.time(), val)
    return val


def _drop_cache(*keys):
    for k in keys:
        _cache.pop(k, None)


# The four small tabs the bot needs on almost every message. When any one of
# them is cold, ONE values_batch_get fetches all four in a single HTTP round
# trip (instead of four sequential get_all_records calls) — this is the main
# reason cold messages got noticeably faster.

def _records(vals):
    """Raw batch values -> list of dicts, like get_all_records does."""
    if not vals:
        return []
    headers = [str(h).strip() for h in vals[0]]
    out = []
    for row in vals[1:]:
        row = list(row) + [""] * (len(headers) - len(row))
        out.append(dict(zip(headers, row[:len(headers)])))
    return out


def _load_core():
    keys = ["accounts", "categories", "clients", "settings"]
    ranges = [config.SHEET_ACCOUNTS, config.SHEET_CATEGORIES,
              config.SHEET_CLIENTS, config.SHEET_SETTINGS]
    resp = _api(lambda: _book().values_batch_get(ranges))
    now = time.time()
    for key, vr in zip(keys, resp.get("valueRanges", [])):
        _cache[key] = (now, _records(vr.get("values", [])))


def _core(key):
    """Cached records for one of the four core tabs; one batch fetch if cold."""
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1]
    _load_core()
    return _cache[key][1]


def _load_credentials_dict():
    """
    Parse GOOGLE_CREDENTIALS_JSON into a usable service-account dict.

    Railway env vars are pasted as a single line, so the private_key field
    inside the JSON needs its \\n sequences to be REAL newlines for Google's
    crypto library to accept it as a valid PEM key. json.loads() already turns
    a correctly-escaped "\\n" into a real newline character on its own — so in
    the normal case no extra work is needed. But if the value ever gets
    double-escaped along the way (copy/paste, an extra layer of quoting in the
    Railway UI, etc.) the private_key can end up with literal backslash-n
    characters instead of real newlines, which breaks the key. This
    normalizes that field defensively, and is a no-op if it's already correct.
    """
    creds_dict = json.loads(config.GOOGLE_CREDENTIALS_JSON)
    pk = creds_dict.get("private_key")
    if isinstance(pk, str) and "\\n" in pk:
        creds_dict["private_key"] = pk.replace("\\n", "\n")
    return creds_dict


def _book():
    global _sh
    if _sh is None:
        if os.path.exists(config.GOOGLE_SERVICE_ACCOUNT_FILE):
            gc = gspread.service_account(filename=config.GOOGLE_SERVICE_ACCOUNT_FILE)
        elif config.GOOGLE_CREDENTIALS_JSON:
            gc = gspread.service_account_from_dict(_load_credentials_dict())
        else:
            raise RuntimeError("No Google credentials found (file or GOOGLE_CREDENTIALS_JSON).")
        _sh = gc.open_by_key(config.GOOGLE_SHEET_ID)
    return _sh


def _ws(title):
    return _book().worksheet(title)


def _header(ws):
    """Header row of a tab, cached — headers don't change while the bot runs."""
    return _cached("hdr:" + ws.title, lambda: _api(lambda: ws.row_values(1)), ttl=600)


def _row_for(ws, col_index, value):
    """Return the 1-based row whose cell in `col_index` exactly equals `value`."""
    vals = _api(lambda: ws.col_values(col_index))
    target = str(value).strip().lower()
    for i, v in enumerate(vals, start=1):
        if str(v).strip().lower() == target:
            return i
    return None

# ---------- Accounts ----------

def get_accounts():
    rows = _core("accounts")
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


def _squash(s):
    return "".join(ch for ch in str(s).lower() if ch.isalnum())


def get_account(name):
    """
    Find an account forgivingly: exact match first, then ignoring spaces and
    punctuation ("union bank" -> UnionBank), then unique containment
    ("UnionBank Account" -> UnionBank). Ambiguous queries ("rcbc" when both
    RCBC PHP and RCBC USD exist) return None so the bot asks instead of
    guessing with money.
    """
    accounts = get_accounts()
    q = str(name).strip().lower()
    if not q:
        return None
    for a in accounts:
        if a["name"].lower() == q:
            return a
    qs = _squash(q)
    if not qs:
        return None
    for a in accounts:
        if _squash(a["name"]) == qs:
            return a
    hits = [a for a in accounts if _squash(a["name"]) in qs or qs in _squash(a["name"])]
    # Drop hits whose name sits inside another hit's name (e.g. "Cash" inside
    # "GCash") so they don't create false ambiguity.
    squashes = [_squash(a["name"]) for a in hits]
    hits = [a for a in hits
            if not any(_squash(a["name"]) != s and _squash(a["name"]) in s for s in squashes)]
    return hits[0] if len(hits) == 1 else None


def update_account_balance(name, new_balance):
    ws = _ws(config.SHEET_ACCOUNTS)
    header = _header(ws)
    name_col = header.index("Account Name") + 1
    bal_col = header.index("Current Balance") + 1
    upd_col = header.index("Last Updated") + 1
    row = _row_for(ws, name_col, name)
    if not row:
        return
    # One batched write instead of two update_cell round trips. USER_ENTERED
    # matches what update_cell did, so values parse the same as before.
    from gspread.utils import rowcol_to_a1
    _api(lambda: ws.batch_update(
        [{"range": rowcol_to_a1(row, bal_col), "values": [[round(new_balance, 2)]]},
         {"range": rowcol_to_a1(row, upd_col), "values": [[_now()]]}],
        value_input_option="USER_ENTERED",
    ))
    # Patch the cached record in place instead of dropping the cache. A
    # multi-account Confirm ("set all my accounts to 0") used to force a full
    # re-read for every account, which is exactly what blew through Google's
    # 60-reads-per-minute quota and produced APIError 429.
    hit = _cache.get("accounts")
    if hit:
        target = str(name).strip().lower()
        for r in hit[1]:
            if str(r.get("Account Name", "")).strip().lower() == target:
                r["Current Balance"] = round(new_balance, 2)

# ---------- Transactions ----------

TX_HEADERS = [
    "Transaction ID", "Created Timestamp", "Transaction Date", "Type", "Amount",
    "Currency", "Exchange Rate", "PHP Equivalent", "Category", "Client",
    "Income Source", "Account", "Destination Account", "Description",
    "Telegram User ID", "Status",
]


def append_transaction(tx):
    row = [tx.get(h, "") for h in TX_HEADERS]
    _api(lambda: _ws(config.SHEET_TRANSACTIONS).append_row(row, value_input_option="USER_ENTERED"))
    return tx.get("Transaction ID", "")


def _transactions():
    """
    All transaction rows as dicts. Uses raw values + _records() instead of
    gspread's get_all_records(), which refuses to read the tab at all if any
    row has stray content beyond the 16 header columns ("header row contains
    duplicates: ['']"). _records() just ignores anything beyond the headers,
    so one stray cell can't take down /history, /report, /undo and chat.
    """
    return _records(_api(lambda: _ws(config.SHEET_TRANSACTIONS).get_values()))


def get_recent_transactions(n=10):
    rows = _transactions()
    return rows[-n:][::-1]


def get_month_transactions(year_month):
    rows = _transactions()
    out = []
    for r in rows:
        d = str(r.get("Transaction Date", ""))
        if d.startswith(year_month) and str(r.get("Status", "")).lower() != "reversed":
            out.append(r)
    return out


def mark_reversed(transaction_id):
    ws = _ws(config.SHEET_TRANSACTIONS)
    header = _header(ws)
    id_col = header.index("Transaction ID") + 1
    status_col = header.index("Status") + 1
    row = _row_for(ws, id_col, transaction_id)
    if row:
        _api(lambda: ws.update_cell(row, status_col, "Reversed"))

# ---------- Settings ----------

def get_setting(key):
    rows = _core("settings")
    for r in rows:
        if str(r.get("Setting", "")).strip() == key:
            return str(r.get("Value", "")).strip()
    return None


def set_setting(key, value):
    ws = _ws(config.SHEET_SETTINGS)
    header = _header(ws)
    key_col = header.index("Setting") + 1
    val_col = header.index("Value") + 1
    row = _row_for(ws, key_col, key)
    if row:
        _api(lambda: ws.update_cell(row, val_col, str(value)))
    else:
        _api(lambda: ws.append_row([key, str(value)], value_input_option="USER_ENTERED"))
    _drop_cache("settings")

# ---------- Lookup lists (for the AI) ----------

def get_categories():
    rows = _core("categories")
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
    rows = _core("clients")
    return [str(r.get("Client Name", "")).strip()
            for r in rows if str(r.get("Client Name", "")).strip()]

# ---------- Audit log ----------

def append_audit(action, transaction_id, detail, user_id):
    _api(lambda: _ws(config.SHEET_AUDIT).append_row(
        [_now(), action, transaction_id, detail, str(user_id)],
        value_input_option="USER_ENTERED",
    ))

# ---------- helpers ----------

def _now():
    # Manila wall-clock time, not the server's UTC (see config.now()).
    return config.now().strftime("%Y-%m-%d %H:%M:%S")


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
    _api(lambda: _ws(config.SHEET_CLIENTS).append_row(
        [name.strip(), currency.upper(), notes],
        value_input_option="USER_ENTERED",
    ))
    _drop_cache("clients")
    return True


def add_income_source(name):
    """Add a new income source to the Categories tab. Returns False if already exists."""
    _, _, sources = get_categories()
    if name.strip().lower() in [s.lower() for s in sources]:
        return False
    _api(lambda: _ws(config.SHEET_CATEGORIES).append_row(
        [name.strip(), "Income Source", ""],
        value_input_option="USER_ENTERED",
    ))
    _drop_cache("categories")
    return True


def add_expense_category(name):
    """Add a new expense category to the Categories tab. Returns False if already exists."""
    expense, _, _ = get_categories()
    if name.strip().lower() in [e.lower() for e in expense]:
        return False
    _api(lambda: _ws(config.SHEET_CATEGORIES).append_row(
        [name.strip(), "Expense Category", ""],
        value_input_option="USER_ENTERED",
    ))
    _drop_cache("categories")
    return True


def add_account(name, account_type, currency, starting_balance):
    """Add a new account row. Returns False if already exists."""
    existing = [a["name"].lower() for a in get_accounts()]
    if name.strip().lower() in existing:
        return False
    _api(lambda: _ws(config.SHEET_ACCOUNTS).append_row(
        [name.strip(), account_type.strip(), currency.upper(),
         round(float(starting_balance), 2), round(float(starting_balance), 2), _now()],
        value_input_option="USER_ENTERED",
    ))
    _drop_cache("accounts")
    return True


def remove_client(name):
    """Remove a client by name. Returns False if not found."""
    ws = _ws(config.SHEET_CLIENTS)
    header = _header(ws)
    name_col = header.index("Client Name") + 1
    row = _row_for(ws, name_col, name)
    if not row:
        return False
    _api(lambda: ws.delete_rows(row))
    _drop_cache("clients")
    return True


def remove_category(name):
    """Remove a category or income source by name. Returns False if not found."""
    ws = _ws(config.SHEET_CATEGORIES)
    header = _header(ws)
    name_col = header.index("Name") + 1
    row = _row_for(ws, name_col, name)
    if not row:
        return False
    _api(lambda: ws.delete_rows(row))
    _drop_cache("categories")
    return True


def remove_account(name):
    """Remove an account by name. Returns False if not found."""
    ws = _ws(config.SHEET_ACCOUNTS)
    header = _header(ws)
    name_col = header.index("Account Name") + 1
    row = _row_for(ws, name_col, name)
    if not row:
        return False
    _api(lambda: ws.delete_rows(row))
    _drop_cache("accounts")
    return True
