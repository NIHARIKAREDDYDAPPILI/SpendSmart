from calendar import monthrange
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from functools import wraps
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


app = Flask(__name__)
secret_key = os.environ.get("SECRET_KEY") or os.environ.get("SPENDSMART_SECRET_KEY")
if not secret_key:
    if os.environ.get("RENDER", "").lower() == "true":
        raise RuntimeError("Set the SECRET_KEY environment variable in Render before starting SpendSmart.")
    secret_key = secrets.token_hex(32)
app.secret_key = secret_key
app.config["DATABASE"] = Path(os.environ.get("DATABASE_PATH", Path(app.instance_path) / "spendsmart.db"))
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

CATEGORIES = ("Food", "Travel", "Education", "Shopping", "Bills", "Other")
TRANSACTION_TYPES = ("income", "expense")
DEMO_USERNAME = "niharika"
DEMO_EMAIL = "niharika@spendsmart.local"
DEFAULT_BUDGET_PAISE = 1_500_000


@contextmanager
def get_db():
    database_path = Path(app.config["DATABASE"])
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _table_columns(db, table):
    return {row["name"]: row for row in db.execute(f"PRAGMA table_info({table})")}


def _backup_before_migration():
    database_path = Path(app.config["DATABASE"])
    if not database_path.exists() or database_path.stat().st_size == 0:
        return None

    source = sqlite3.connect(database_path)
    try:
        tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        transaction_columns = {row[1] for row in source.execute("PRAGMA table_info(transactions)")} if "transactions" in tables else set()
        settings_columns = {row[1] for row in source.execute("PRAGMA table_info(settings)")} if "settings" in tables else set()
        needs_migration = (
            ("transactions" in tables and not {"user_id", "amount_paise"}.issubset(transaction_columns))
            or ("settings" in tables and "user_id" not in settings_columns)
        )
        if not needs_migration:
            return None

        backup_path = database_path.with_name(f"{database_path.name}.pre-auth.bak")
        if not backup_path.exists():
            destination = sqlite3.connect(backup_path)
            try:
                source.backup(destination)
            finally:
                destination.close()
        return backup_path
    finally:
        source.close()


def _create_transactions_table(db, table_name="transactions"):
    db.execute(
        f"""CREATE TABLE {table_name} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            amount_paise INTEGER NOT NULL CHECK (amount_paise > 0),
            type TEXT NOT NULL CHECK (type IN ('income', 'expense')),
            category TEXT NOT NULL,
            date TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT ''
        )"""
    )


def _create_settings_table(db, table_name="settings"):
    db.execute(
        f"""CREATE TABLE {table_name} (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (user_id, key)
        )"""
    )


def _amount_to_paise(value):
    return int((Decimal(str(value)) * 100).quantize(Decimal("1")))


def _ensure_demo_user(db):
    db.execute(
        "INSERT OR IGNORE INTO users (username, email, password_hash) VALUES (?, ?, ?)",
        (DEMO_USERNAME, DEMO_EMAIL, generate_password_hash("niharika")),
    )
    row = db.execute("SELECT id FROM users WHERE username = ? COLLATE NOCASE", (DEMO_USERNAME,)).fetchone()
    if row is None:
        raise RuntimeError("Unable to create the SpendSmart demo account.")
    return row["id"]


def _seed_demo_transactions(db, demo_user_id):
    marker = "demo_transactions_seeded_v1"
    if db.execute("SELECT 1 FROM settings WHERE user_id = ? AND key = ?", (demo_user_id, marker)).fetchone():
        return

    transactions = [
        ("August salary", "52000.00", "income", "Other", "2026-08-01", "Monthly salary"),
        ("Freelance design work", "4500.00", "income", "Other", "2026-08-18", "Small project payment"),
        ("August rent", "14500.00", "expense", "Bills", "2026-08-02", "Apartment rent"),
        ("August groceries", "4250.00", "expense", "Food", "2026-08-06", "Monthly groceries and produce"),
        ("Metro and bus card", "1650.00", "expense", "Travel", "2026-08-08", "Commute pass"),
        ("Cloud course", "2499.00", "expense", "Education", "2026-08-12", "Online course"),
        ("Electricity and internet", "2380.00", "expense", "Bills", "2026-08-17", "Utilities"),
        ("Clothing", "3200.00", "expense", "Shopping", "2026-08-22", "Seasonal clothes"),
        ("Coffee and books", "740.00", "expense", "Other", "2026-08-27", "Cafe and a reference book"),
        ("September salary", "52000.00", "income", "Other", "2026-09-01", "Monthly salary"),
        ("Tutoring income", "3000.00", "income", "Other", "2026-09-14", "Weekend tutoring"),
        ("September rent", "14500.00", "expense", "Bills", "2026-09-02", "Apartment rent"),
        ("Market and groceries", "3980.00", "expense", "Food", "2026-09-05", "Groceries"),
        ("Auto and metro", "1420.00", "expense", "Travel", "2026-09-09", "Local travel"),
        ("Certification exam", "1800.00", "expense", "Education", "2026-09-13", "Course assessment"),
        ("Power and broadband", "2210.00", "expense", "Bills", "2026-09-17", "Monthly utilities"),
        ("Headphones", "2799.00", "expense", "Shopping", "2026-09-21", "Study headphones"),
        ("Medical supplies", "560.00", "expense", "Other", "2026-09-26", "Pharmacy"),
        ("October salary", "52000.00", "income", "Other", "2026-10-01", "Monthly salary"),
        ("Project stipend", "2500.00", "income", "Other", "2026-10-10", "College project stipend"),
        ("October rent", "14500.00", "expense", "Bills", "2026-10-02", "Apartment rent"),
        ("Groceries and lunch", "4620.00", "expense", "Food", "2026-10-07", "Groceries and meals"),
        ("Cab and metro", "1780.00", "expense", "Travel", "2026-10-11", "Commute and one late cab"),
        ("Interview preparation course", "3200.00", "expense", "Education", "2026-10-14", "Placement preparation"),
        ("Electricity and phone", "2470.00", "expense", "Bills", "2026-10-17", "Monthly utilities"),
        ("Backpack", "2350.00", "expense", "Shopping", "2026-10-20", "College backpack"),
        ("Festival snacks", "1120.00", "expense", "Other", "2026-10-24", "Family gathering"),
    ]
    db.executemany(
        "INSERT INTO transactions (user_id, title, amount_paise, type, category, date, description) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(demo_user_id, title, _amount_to_paise(amount), transaction_type, category, transaction_date, description)
         for title, amount, transaction_type, category, transaction_date, description in transactions],
    )
    db.execute("INSERT OR IGNORE INTO settings (user_id, key, value) VALUES (?, ?, ?)", (demo_user_id, marker, "1"))


def _upgrade_legacy_budget_units(db, demo_user_id):
    marker = "budget_units_paise_v1"
    if db.execute("SELECT 1 FROM settings WHERE user_id = ? AND key = ?", (demo_user_id, marker)).fetchone():
        return

    database_path = Path(app.config["DATABASE"])
    backup_path = database_path.with_name(f"{database_path.name}.pre-auth.bak")
    if backup_path.exists():
        backup = sqlite3.connect(backup_path)
        try:
            old_budget = backup.execute("SELECT value FROM settings WHERE key = ?", ("monthly_budget",)).fetchone()
        except sqlite3.OperationalError:
            old_budget = None
        finally:
            backup.close()
        current_budget = db.execute(
            "SELECT value FROM settings WHERE user_id = ? AND key = ?", (demo_user_id, "monthly_budget")
        ).fetchone()
        if old_budget and current_budget and current_budget["value"] == old_budget[0]:
            db.execute(
                "UPDATE settings SET value = ? WHERE user_id = ? AND key = ?",
                (str(_amount_to_paise(old_budget[0])), demo_user_id, "monthly_budget"),
            )
    db.execute("INSERT OR IGNORE INTO settings (user_id, key, value) VALUES (?, ?, ?)", (demo_user_id, marker, "1"))


def init_db():
    _backup_before_migration()
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            """CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL
            )"""
        )
        demo_user_id = _ensure_demo_user(db)

        transaction_columns = _table_columns(db, "transactions")
        if not transaction_columns:
            _create_transactions_table(db)
        elif not {"user_id", "amount_paise"}.issubset(transaction_columns):
            db.execute("ALTER TABLE transactions RENAME TO transactions_legacy_migration")
            _create_transactions_table(db)
            legacy_rows = db.execute("SELECT * FROM transactions_legacy_migration ORDER BY id").fetchall()
            for row in legacy_rows:
                owner_id = row["user_id"] if "user_id" in row.keys() and row["user_id"] else demo_user_id
                if not db.execute("SELECT 1 FROM users WHERE id = ?", (owner_id,)).fetchone():
                    owner_id = demo_user_id
                amount_paise = row["amount_paise"] if "amount_paise" in row.keys() else _amount_to_paise(row["amount"])
                db.execute(
                    """INSERT INTO transactions (id, user_id, title, amount_paise, type, category, date, description)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (row["id"], owner_id, row["title"], amount_paise, row["type"], row["category"], row["date"], row["description"] or ""),
                )
            db.execute("DROP TABLE transactions_legacy_migration")

        settings_columns = _table_columns(db, "settings")
        settings_info = db.execute("PRAGMA table_info(settings)").fetchall() if settings_columns else []
        settings_primary_key = tuple(row["name"] for row in sorted(settings_info, key=lambda item: item["pk"]) if row["pk"])
        if not settings_columns:
            _create_settings_table(db)
        elif "user_id" not in settings_columns or settings_primary_key != ("user_id", "key"):
            db.execute("ALTER TABLE settings RENAME TO settings_legacy_migration")
            _create_settings_table(db)
            legacy_rows = db.execute("SELECT * FROM settings_legacy_migration").fetchall()
            for row in legacy_rows:
                owner_id = row["user_id"] if "user_id" in row.keys() and row["user_id"] else demo_user_id
                if not db.execute("SELECT 1 FROM users WHERE id = ?", (owner_id,)).fetchone():
                    owner_id = demo_user_id
                value = row["value"]
                if row["key"] == "monthly_budget" and "user_id" not in row.keys():
                    value = str(_amount_to_paise(value))
                db.execute(
                    "INSERT OR REPLACE INTO settings (user_id, key, value) VALUES (?, ?, ?)",
                    (owner_id, row["key"], value),
                )
            db.execute("DROP TABLE settings_legacy_migration")

        db.execute("CREATE INDEX IF NOT EXISTS idx_transactions_owner_date ON transactions(user_id, date)")
        db.execute(
            "INSERT OR IGNORE INTO settings (user_id, key, value) VALUES (?, ?, ?)",
            (demo_user_id, "monthly_budget", str(DEFAULT_BUDGET_PAISE)),
        )
        _upgrade_legacy_budget_units(db, demo_user_id)
        _seed_demo_transactions(db, demo_user_id)


def current_month():
    return date.today().strftime("%Y-%m")


def month_bounds(month):
    year, month_number = (int(part) for part in month.split("-"))
    start = date(year, month_number, 1)
    if month_number == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month_number + 1, 1)
    previous = date(year - 1, 12, 1) if month_number == 1 else date(year, month_number - 1, 1)
    return start.isoformat(), end.isoformat(), previous.strftime("%Y-%m")


def parse_amount_paise(value):
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError):
        raise ValueError("Enter a valid amount.") from None
    if not amount.is_finite() or amount <= 0 or amount > Decimal("999999999"):
        raise ValueError("Amount must be greater than zero and no more than 999,999,999.")
    if amount != amount.quantize(Decimal("0.01")):
        raise ValueError("Amount can have no more than two decimal places.")
    return int(amount * 100)


def parse_transaction(form):
    title = form.get("title", "").strip()
    transaction_type = form.get("type", "")
    category = form.get("category", "")
    transaction_date = form.get("date", "")
    description = form.get("description", "").strip()

    if not title or len(title) > 80:
        raise ValueError("Title is required and must be 80 characters or fewer.")
    amount_paise = parse_amount_paise(form.get("amount", "").strip())
    if transaction_type not in TRANSACTION_TYPES:
        raise ValueError("Choose income or expense.")
    if category not in CATEGORIES:
        raise ValueError("Choose a valid category.")
    try:
        datetime.strptime(transaction_date, "%Y-%m-%d")
    except ValueError:
        raise ValueError("Choose a valid transaction date.") from None
    if len(description) > 240:
        raise ValueError("Description must be 240 characters or fewer.")
    return (title, amount_paise, transaction_type, category, transaction_date, description)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = get_current_user()
        if user is None:
            return redirect(url_for("login", next=request.full_path if request.query_string else request.path))
        return view(*args, **kwargs)
    return wrapped


def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    with get_db() as db:
        row = db.execute("SELECT id, username, email FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        session.clear()
        return None
    return dict(row)


def dashboard_data(user_id, selected_month):
    month_start, next_month, previous_month = month_bounds(selected_month)
    previous_start, previous_end, _ = month_bounds(previous_month)
    with get_db() as db:
        all_totals = db.execute(
            """SELECT
                COALESCE(SUM(CASE WHEN type = 'income' THEN amount_paise ELSE 0 END), 0) AS income,
                COALESCE(SUM(CASE WHEN type = 'expense' THEN amount_paise ELSE 0 END), 0) AS expenses
            FROM transactions WHERE user_id = ?""",
            (user_id,),
        ).fetchone()
        monthly_totals = db.execute(
            """SELECT
                COALESCE(SUM(CASE WHEN type = 'income' THEN amount_paise ELSE 0 END), 0) AS income,
                COALESCE(SUM(CASE WHEN type = 'expense' THEN amount_paise ELSE 0 END), 0) AS expenses
            FROM transactions WHERE user_id = ? AND date >= ? AND date < ?""",
            (user_id, month_start, next_month),
        ).fetchone()
        previous_expenses = db.execute(
            "SELECT COALESCE(SUM(amount_paise), 0) FROM transactions WHERE user_id = ? AND type = 'expense' AND date >= ? AND date < ?",
            (user_id, previous_start, previous_end),
        ).fetchone()[0]
        budget_row = db.execute(
            "SELECT value FROM settings WHERE user_id = ? AND key = ?",
            (user_id, "monthly_budget"),
        ).fetchone()
        breakdown = db.execute(
            """SELECT category, SUM(amount_paise) AS total
            FROM transactions WHERE user_id = ? AND type = 'expense' AND date >= ? AND date < ?
            GROUP BY category ORDER BY total DESC""",
            (user_id, month_start, next_month),
        ).fetchall()

    monthly_expenses = monthly_totals["expenses"]
    if previous_expenses:
        spending_change = (Decimal(monthly_expenses - previous_expenses) * 100 / Decimal(previous_expenses)).quantize(Decimal("0.1"))
    else:
        spending_change = Decimal("0.0") if not monthly_expenses else None
    budget = int(budget_row["value"]) if budget_row else DEFAULT_BUDGET_PAISE
    return {
        "income": all_totals["income"],
        "expenses": all_totals["expenses"],
        "balance": all_totals["income"] - all_totals["expenses"],
        "month_income": monthly_totals["income"],
        "month_expenses": monthly_expenses,
        "net_savings": monthly_totals["income"] - monthly_expenses,
        "previous_expenses": previous_expenses,
        "spending_change": spending_change,
        "budget": budget,
        "budget_percent": (Decimal(monthly_expenses) * 100 / Decimal(budget)).quantize(Decimal("0.1")) if budget else Decimal("0"),
        "breakdown": [dict(row) for row in breakdown],
    }


def render_dashboard(user, selected_month, edit_transaction=None, filters=None):
    month_start, next_month, previous_month = month_bounds(selected_month)
    filters = filters or {"search": "", "category": "", "type": "", "date_from": "", "date_to": ""}
    clauses = ["user_id = ?", "date >= ?", "date < ?"]
    parameters = [user["id"], month_start, next_month]
    search = filters["search"]
    if search:
        clauses.append("(title LIKE ? OR description LIKE ?)")
        parameters.extend((f"%{search}%", f"%{search}%"))
    if filters["category"] in CATEGORIES:
        clauses.append("category = ?")
        parameters.append(filters["category"])
    if filters["type"] in TRANSACTION_TYPES:
        clauses.append("type = ?")
        parameters.append(filters["type"])
    if filters["date_from"]:
        clauses.append("date >= ?")
        parameters.append(filters["date_from"])
    if filters["date_to"]:
        clauses.append("date <= ?")
        parameters.append(filters["date_to"])

    with get_db() as db:
        transactions = db.execute(
            "SELECT * FROM transactions WHERE " + " AND ".join(clauses) + " ORDER BY date DESC, id DESC",
            parameters,
        ).fetchall()

    option_months = ["2026-08", "2026-09", "2026-10"]
    if selected_month not in option_months:
        option_months.append(selected_month)
        option_months.sort()
    start_date, _, _ = month_bounds(selected_month)
    year, month_number = (int(part) for part in selected_month.split("-"))
    month_last_day = monthrange(year, month_number)[1]
    return render_template(
        "dashboard.html",
        user=user,
        stats=dashboard_data(user["id"], selected_month),
        transactions=transactions,
        categories=CATEGORIES,
        filters=filters,
        selected_month=selected_month,
        month_label=date.fromisoformat(start_date).strftime("%B %Y"),
        month_options=option_months,
        previous_month=previous_month,
        next_month=date(year + (month_number == 12), 1 if month_number == 12 else month_number + 1, 1).strftime("%Y-%m"),
        month_first=start_date,
        month_last=f"{year:04d}-{month_number:02d}-{month_last_day:02d}",
        today=date.today().isoformat(),
        default_date=date.today().isoformat() if current_month() == selected_month else start_date,
        edit_transaction=edit_transaction,
    )


def requested_month():
    month = request.values.get("month", current_month())
    try:
        datetime.strptime(month, "%Y-%m")
        return month
    except ValueError:
        flash("Choose a valid month.", "error")
        return current_month()


def _safe_next(target):
    return target if target and target.startswith("/") and not target.startswith("//") else url_for("index")


@app.route("/login", methods=["GET", "POST"])
def login():
    if get_current_user():
        return redirect(url_for("index"))
    if request.method == "POST":
        identity = request.form.get("identity", "").strip()
        password = request.form.get("password", "")
        with get_db() as db:
            user = db.execute(
                "SELECT id, username, email, password_hash FROM users WHERE username = ? COLLATE NOCASE OR email = ? COLLATE NOCASE",
                (identity, identity),
            ).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            flash(f"Welcome back, {user['username']}.", "success")
            return redirect(_safe_next(request.args.get("next")))
        flash("Email/username or password is incorrect.", "error")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if get_current_user():
        return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(email) > 254:
            flash("Enter a valid email address.", "error")
        elif not re.fullmatch(r"[A-Za-z0-9_.-]{3,30}", username):
            flash("Username must be 3–30 characters using letters, numbers, dots, underscores, or hyphens.", "error")
        elif len(password) < 8 or len(password) > 128:
            flash("Password must be between 8 and 128 characters.", "error")
        elif password != confirm_password:
            flash("Passwords do not match.", "error")
        else:
            try:
                with get_db() as db:
                    cursor = db.execute(
                        "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
                        (username, email, generate_password_hash(password)),
                    )
                    db.execute(
                        "INSERT INTO settings (user_id, key, value) VALUES (?, ?, ?)",
                        (cursor.lastrowid, "monthly_budget", str(DEFAULT_BUDGET_PAISE)),
                    )
                flash("Account created. Sign in to continue.", "success")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("That email address or username is already registered.", "error")
    return render_template("register.html")


@app.post("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


@app.route("/", methods=["GET", "POST"])
@login_required
def index():
    user = get_current_user()
    month = requested_month()
    if request.method == "POST":
        try:
            transaction = parse_transaction(request.form)
            with get_db() as db:
                db.execute(
                    "INSERT INTO transactions (user_id, title, amount_paise, type, category, date, description) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (user["id"], *transaction),
                )
            flash("Transaction added.", "success")
        except ValueError as error:
            flash(str(error), "error")
        return redirect(url_for("index", month=month))

    filters = {
        "search": request.args.get("search", "").strip()[:80],
        "category": request.args.get("category", ""),
        "type": request.args.get("type", ""),
        "date_from": request.args.get("date_from", ""),
        "date_to": request.args.get("date_to", ""),
    }
    for key in ("date_from", "date_to"):
        if filters[key]:
            try:
                datetime.strptime(filters[key], "%Y-%m-%d")
            except ValueError:
                filters[key] = ""
    return render_dashboard(user, month, filters=filters)


@app.route("/transaction/<int:transaction_id>/edit", methods=["GET", "POST"])
@login_required
def edit_transaction(transaction_id):
    user = get_current_user()
    month = requested_month()
    with get_db() as db:
        existing = db.execute(
            "SELECT * FROM transactions WHERE id = ? AND user_id = ?",
            (transaction_id, user["id"]),
        ).fetchone()
    if existing is None:
        abort(404)

    if request.method == "POST":
        try:
            transaction = parse_transaction(request.form)
            with get_db() as db:
                result = db.execute(
                    "UPDATE transactions SET title = ?, amount_paise = ?, type = ?, category = ?, date = ?, description = ? WHERE id = ? AND user_id = ?",
                    (*transaction, transaction_id, user["id"]),
                )
            if not result.rowcount:
                abort(404)
            flash("Transaction updated.", "success")
            return redirect(url_for("index", month=month))
        except ValueError as error:
            flash(str(error), "error")

    return render_dashboard(user, month, edit_transaction=existing)


@app.post("/transaction/<int:transaction_id>/delete")
@login_required
def delete_transaction(transaction_id):
    user = get_current_user()
    month = requested_month()
    with get_db() as db:
        result = db.execute("DELETE FROM transactions WHERE id = ? AND user_id = ?", (transaction_id, user["id"]))
    flash("Transaction deleted." if result.rowcount else "Transaction not found.", "success" if result.rowcount else "error")
    return redirect(url_for("index", month=month))


@app.post("/budget")
@login_required
def update_budget():
    user = get_current_user()
    month = requested_month()
    try:
        budget_paise = parse_amount_paise(request.form.get("budget", ""))
    except ValueError:
        flash("Budget must be a valid amount greater than zero.", "error")
        return redirect(url_for("index", month=month))

    with get_db() as db:
        db.execute(
            "INSERT INTO settings (user_id, key, value) VALUES (?, ?, ?) ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value",
            (user["id"], "monthly_budget", str(budget_paise)),
        )
    flash("Monthly budget updated.", "success")
    return redirect(url_for("index", month=month))


@app.template_filter("money")
def format_money(amount_paise):
    return f"₹{(Decimal(int(amount_paise)) / 100):,.2f}"


init_db()

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )