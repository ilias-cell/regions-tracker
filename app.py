"""
Family Russia-regions visit tracker.

Authentication model:
  - GET /              → read-only view (editor=None)
  - GET /edit/<token>  → personal edit link; only that player's column is editable
  - POST /toggle       → requires `token` + `region_code`; toggles visit for the token owner
  - POST /rename       → requires `token` + `name`; renames only the token owner
"""

import os
import sqlite3
import logging
from flask import Flask, g, render_template, request, redirect, url_for, jsonify

from regions_data import REGIONS_BY_DISTRICT

app = Flask(__name__)
app.logger.setLevel(logging.INFO)

DATABASE = os.environ.get("DATABASE_PATH", "visits.db")

# ---------------------------------------------------------------------------
# Fixed player list: (id, display_name, token)
# Tokens are intentionally opaque strings. Replace with long random values
# in production (e.g. from `python -c "import secrets; print(secrets.token_urlsafe(32))"`)
# ---------------------------------------------------------------------------
PEOPLE = [
    (1, "Илья",   "tok_Hv8kQw3mZpLxNbRqYeJdTfUsCgAiOvWn"),
    (2, "Катя",   "tok_Xr2aNcEdKsFjGhTyUiBvLmPwQoZxRnYp"),
    (3, "Кирилл", "tok_Dq7wEtYuIoPaSlKjHfGdSzXcVbNmQrTy"),
    (4, "Данил",  "tok_Mk5vBnCxZaQwErTyUiOpLkJhGfDsApRe"),
]

# Quick lookup dicts built at import time (never change at runtime)
_TOKEN_TO_PERSON = {tok: {"id": pid, "name": name, "token": tok}
                    for pid, name, tok in PEOPLE}
_ID_TO_TOKEN     = {pid: tok for pid, name, tok in PEOPLE}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_db() -> sqlite3.Connection:
    """Return a per-request SQLite connection with Row factory."""
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db


@app.teardown_appcontext
def close_db(exc):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()


def init_db():
    """
    Initialise (or migrate) the database.

    Strategy:
      1. Create tables if they don't exist (token column included from the start).
      2. If `people` already exists without a `token` column → ALTER TABLE to add it.
      3. INSERT OR IGNORE the canonical PEOPLE rows (preserves existing names/ids).
      4. UPDATE token for any row where token IS NULL (fills in after migration).
      5. Create `visits` table if absent.
    """
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row

    # -- ensure people table exists with token column -----------------------
    db.execute("""
        CREATE TABLE IF NOT EXISTS people (
            id    INTEGER PRIMARY KEY,
            name  TEXT    NOT NULL,
            token TEXT    UNIQUE
        )
    """)

    # -- migrate: add token column if missing (idempotent) ------------------
    existing_cols = {row["name"] for row in db.execute("PRAGMA table_info(people)")}
    if "token" not in existing_cols:
        app.logger.info("Migrating people table: adding token column")
        db.execute("ALTER TABLE people ADD COLUMN token TEXT")

    # -- ensure visits table exists -----------------------------------------
    db.execute("""
        CREATE TABLE IF NOT EXISTS visits (
            person_id   INTEGER NOT NULL,
            region_code TEXT    NOT NULL,
            PRIMARY KEY (person_id, region_code),
            FOREIGN KEY (person_id) REFERENCES people(id)
        )
    """)

    db.commit()

    # -- seed canonical people rows (preserve existing data) ----------------
    for pid, name, tok in PEOPLE:
        db.execute(
            "INSERT OR IGNORE INTO people (id, name, token) VALUES (?, ?, ?)",
            (pid, name, tok),
        )

    # -- backfill tokens for rows that were inserted before migration -------
    for pid, name, tok in PEOPLE:
        db.execute(
            "UPDATE people SET token = ? WHERE id = ? AND token IS NULL",
            (tok, pid),
        )

    db.commit()
    db.close()
    app.logger.info("Database initialised (or already up to date)")


# ---------------------------------------------------------------------------
# Shared query helpers
# ---------------------------------------------------------------------------

def _load_view_data(db: sqlite3.Connection) -> dict:
    """
    Return everything the index template needs:
      people        – list of Row objects from the DB
      by_district   – OrderedDict from regions_data
      visited       – set of (person_id, region_code) tuples
      totals        – {person_id: count}
      total_regions – total distinct regions in the data set
    """
    people       = db.execute("SELECT id, name FROM people ORDER BY id").fetchall()
    visit_rows   = db.execute("SELECT person_id, region_code FROM visits").fetchall()
    visited      = {(r["person_id"], r["region_code"]) for r in visit_rows}
    totals       = {p["id"]: sum(1 for v in visit_rows if v["person_id"] == p["id"])
                    for p in people}
    total_regions = sum(len(regions) for regions in REGIONS_BY_DISTRICT.values())

    return dict(
        people=people,
        by_district=REGIONS_BY_DISTRICT,
        visited=visited,
        totals=totals,
        total_regions=total_regions,
    )


def _person_by_token(db: sqlite3.Connection, token: str):
    """Return a sqlite3.Row for the person matching `token`, or None."""
    return db.execute(
        "SELECT id, name, token FROM people WHERE token = ?", (token,)
    ).fetchone()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Read-only view — no editor context."""
    db   = get_db()
    data = _load_view_data(db)
    return render_template("index.html", editor=None, edit_person_id=None, **data)


@app.route("/edit/<token>")
def edit(token: str):
    """
    Personal edit link.  Renders the same template but with `editor` set so
    the frontend can make exactly one column interactive.
    """
    db     = get_db()
    person = _person_by_token(db, token)

    if person is None:
        # Unknown token → fall back to read-only rather than 404
        app.logger.warning("Unknown token used for /edit — serving read-only")
        data = _load_view_data(db)
        return render_template("index.html", editor=None, edit_person_id=None, **data)

    editor = {"id": person["id"], "name": person["name"], "token": person["token"]}
    data   = _load_view_data(db)
    return render_template(
        "index.html",
        editor=editor,
        edit_person_id=editor["id"],
        **data,
    )


@app.route("/toggle", methods=["POST"])
def toggle():
    """
    Toggle a visit mark for the authenticated player.

    Expected POST body:  token=<str>  region_code=<str>
    Returns JSON:        {"visited": bool, "total": int}

    The person_id is derived exclusively from the token — any person_id
    submitted in the request body is ignored.
    """
    token       = request.form.get("token", "").strip()
    region_code = request.form.get("region_code", "").strip()

    if not token or not region_code:
        return jsonify({"error": "token and region_code are required"}), 400

    db     = get_db()
    person = _person_by_token(db, token)

    if person is None:
        return jsonify({"error": "invalid token"}), 403

    person_id = person["id"]

    existing = db.execute(
        "SELECT 1 FROM visits WHERE person_id = ? AND region_code = ?",
        (person_id, region_code),
    ).fetchone()

    if existing:
        db.execute(
            "DELETE FROM visits WHERE person_id = ? AND region_code = ?",
            (person_id, region_code),
        )
        now_visited = False
    else:
        db.execute(
            "INSERT INTO visits (person_id, region_code) VALUES (?, ?)",
            (person_id, region_code),
        )
        now_visited = True

    db.commit()

    total = db.execute(
        "SELECT COUNT(*) AS c FROM visits WHERE person_id = ?", (person_id,)
    ).fetchone()["c"]

    return jsonify({"visited": now_visited, "total": total})


@app.route("/rename", methods=["POST"])
def rename():
    """
    Rename a player.  Identity is established by token only.

    POST body:  token=<str>  name=<str>
    Returns JSON: {"ok": true, "name": "<new name>"}
    """
    token    = request.form.get("token", "").strip()
    new_name = request.form.get("name", "").strip()

    if not token or not new_name:
        return jsonify({"error": "token and name are required"}), 400

    db     = get_db()
    person = _person_by_token(db, token)

    if person is None:
        return jsonify({"error": "invalid token"}), 403

    db.execute("UPDATE people SET name = ? WHERE id = ?", (new_name, person["id"]))
    db.commit()

    return jsonify({"ok": True, "name": new_name})


# ---------------------------------------------------------------------------
# Application entry point
# ---------------------------------------------------------------------------

with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(debug=True)
