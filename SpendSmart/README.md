# SpendSmart — Personal Expense & Budget Manager

A beginner-friendly personal finance dashboard built with Python, Flask, SQLite, HTML, CSS, and vanilla JavaScript. Transactions and the monthly budget are stored locally in SQLite. The app creates its database and tables on startup and inserts a small sample dataset the first time it runs.

## Project structure

```text
SpendSmart/
├── app.py
├── requirements.txt
├── README.md
├── templates/
│   ├── dashboard.html
│   ├── index.html
│   ├── login.html
│   └── register.html
├── static/
│   ├── spendsmart.css
│   ├── script.js
│   └── style.css
├── Procfile
└── instance/                 # Local SQLite data; ignored by Git
```

## Run on Windows

Open the `SpendSmart` folder in VS Code, then run these commands in the integrated PowerShell terminal:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

If PowerShell blocks virtual environment activation, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` once in that terminal, then activate again. Open [http://127.0.0.1:5000](http://127.0.0.1:5000). Stop the local server with `Ctrl+C`.

## Features

- Dashboard totals are calculated from transaction rows in SQLite.
- Add, edit, and delete income and expense transactions.
- Filter transaction history by search text, category, type, and date range.
- Set a monthly spending budget with near-limit and exceeded warnings.
- View current-month expenses grouped by category.
- Validate form input in the browser and again in Flask before writing to the database.
- Sample transactions are inserted only when the transaction table is empty.

## Database

The app uses Python's built-in `sqlite3` module, so no database server is needed. It creates `transactions` and `settings` tables in `instance/spendsmart.db`. Every user-provided SQL value is passed as a parameter. To start over, stop the server and delete that database file; the app will recreate it with sample data on the next start.

## Deploy to Render

1. Push this project to a Git repository. The project `.gitignore` excludes the virtual environment, environment files, SQLite databases, and migration backups; do not force-add those files.
2. Create a Render Web Service from the repository. Set its root directory to `SpendSmart` if this project is inside a larger repository.
3. Use `pip install -r requirements.txt` as the build command and `gunicorn app:app --bind 0.0.0.0:$PORT` as the start command. `Procfile` contains the same start command.
4. In Render's environment settings, add `SECRET_KEY` with a generated random value. For example, run `python -c "import secrets; print(secrets.token_hex(32))"` locally and paste the output into the Render dashboard. Never commit that value.
5. SQLite files on Render's default filesystem are temporary. For data that must survive deploys/restarts, attach a persistent disk mounted at `/var/data` and set `DATABASE_PATH` to `/var/data/spendsmart.db`. A persistent disk may incur Render charges. Without it, use the app only as a disposable demo.

The app reads Render's `PORT` when run directly, and Gunicorn binds to that port in the provided start command. It refuses to start on Render if `SECRET_KEY` is missing. Demo account and transactions are initialized idempotently under a SQLite write lock, so restarts and simultaneous worker startup do not duplicate the sample set.

## Quick test checklist

1. Start the app and confirm the dashboard displays seeded income, expenses, balance, and budget.
2. Add an expense and an income; confirm totals, history, and category breakdown change.
3. Edit a transaction, then delete one and confirm the prompt and updated totals.
4. Try an empty title, a zero amount, and an invalid amount; confirm validation blocks them.
5. Change the monthly budget to a small amount; confirm the progress bar and warning update.
6. Search for a title and filter by category, income/expense, and date range; clear filters.
7. Resize the browser to a phone width and confirm the dashboard and transaction table remain usable.
8. Stop and restart Flask; confirm saved transactions and budget persist.

## Interview explanation

Flask maps browser requests to routes. The home route reads transactions and uses SQL aggregate queries for dashboard totals, while POST routes validate and execute parameterized INSERT, UPDATE, and DELETE statements. SQLite stores transaction records and the budget in a local file. Jinja renders the HTML with the returned data, CSS provides responsive layout, and vanilla JavaScript handles small interactions such as delete confirmation and showing the budget editor.