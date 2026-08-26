# -*- coding: utf-8 -*-
"""Семейный трекер посещённых регионов России.

Flask + SQLite. Готов к деплою на Render.com.
"""
import os
import sqlite3
from contextlib import closing

from flask import (
    Flask, g, render_template, request, redirect, url_for, jsonify
)

from regions_data import REGIONS, DISTRICT_ORDER

app = Flask(__name__)

# На Render постоянный диск монтируется в /var/data (см. render.yaml).
# Локально — файл рядом с приложением.
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "tracker.db"))

# Участники соревнования. Можно поменять имена здесь.
PEOPLE = [
    (1, "Игрок 1"),
    (2, "Игрок 2"),
    (3, "Игрок 3"),
    (4, "Игрок 4"),
]


# --------------------------------------------------------------------------- #
#  База данных
# --------------------------------------------------------------------------- #
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Создаёт таблицы и наполняет справочники, если БД пустая."""
    with closing(sqlite3.connect(DB_PATH)) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS people (
                id   INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS regions (
                code     TEXT PRIMARY KEY,
                name     TEXT NOT NULL,
                district TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS visits (
                person_id   INTEGER NOT NULL,
                region_code TEXT NOT NULL,
                PRIMARY KEY (person_id, region_code),
                FOREIGN KEY (person_id)   REFERENCES people(id),
                FOREIGN KEY (region_code) REFERENCES regions(code)
            );
            """
        )

        # Наполняем людей
        for pid, name in PEOPLE:
            db.execute(
                "INSERT OR IGNORE INTO people (id, name) VALUES (?, ?)",
                (pid, name),
            )

        # Наполняем регионы
        for code, name, district in REGIONS:
            db.execute(
                "INSERT OR IGNORE INTO regions (code, name, district) VALUES (?, ?, ?)",
                (code, name, district),
            )
        db.commit()


# --------------------------------------------------------------------------- #
#  Вспомогательные запросы
# --------------------------------------------------------------------------- #
def load_state():
    """Возвращает данные для отрисовки сводной таблицы."""
    db = get_db()

    people = [dict(r) for r in db.execute(
        "SELECT id, name FROM people ORDER BY id"
    )]

    regions = [dict(r) for r in db.execute(
        "SELECT code, name, district FROM regions"
    )]

    visited = set()
    for row in db.execute("SELECT person_id, region_code FROM visits"):
        visited.add((row["person_id"], row["region_code"]))

    # Группируем регионы по округам в правильном порядке
    by_district = {d: [] for d in DISTRICT_ORDER}
    for reg in regions:
        by_district.setdefault(reg["district"], []).append(reg)

    # Сохраняем исходный порядок регионов внутри округа (как в REGIONS)
    order = {code: i for i, (code, _, _) in enumerate(REGIONS)}
    for d in by_district:
        by_district[d].sort(key=lambda r: order.get(r["code"], 999))

    # Считаем итоги по каждому игроку
    totals = {p["id"]: 0 for p in people}
    for (pid, _code) in visited:
        if pid in totals:
            totals[pid] += 1

    total_regions = len(regions)

    return {
        "people": people,
        "by_district": by_district,
        "districts": DISTRICT_ORDER,
        "visited": visited,
        "totals": totals,
        "total_regions": total_regions,
    }


# --------------------------------------------------------------------------- #
#  Маршруты
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    state = load_state()
    return render_template("index.html", **state)


@app.route("/toggle", methods=["POST"])
def toggle():
    """Переключает статус посещения региона игроком (AJAX)."""
    person_id = int(request.form["person_id"])
    region_code = request.form["region_code"]
    db = get_db()

    exists = db.execute(
        "SELECT 1 FROM visits WHERE person_id=? AND region_code=?",
        (person_id, region_code),
    ).fetchone()

    if exists:
        db.execute(
            "DELETE FROM visits WHERE person_id=? AND region_code=?",
            (person_id, region_code),
        )
        visited = False
    else:
        db.execute(
            "INSERT INTO visits (person_id, region_code) VALUES (?, ?)",
            (person_id, region_code),
        )
        visited = True
    db.commit()

    total = db.execute(
        "SELECT COUNT(*) AS c FROM visits WHERE person_id=?",
        (person_id,),
    ).fetchone()["c"]

    return jsonify({"visited": visited, "total": total})


@app.route("/rename", methods=["POST"])
def rename():
    """Переименование игрока."""
    person_id = int(request.form["person_id"])
    name = request.form.get("name", "").strip() or f"Игрок {person_id}"
    db = get_db()
    db.execute("UPDATE people SET name=? WHERE id=?", (name[:40], person_id))
    db.commit()
    return redirect(url_for("index"))


# Инициализируем БД при импорте (важно для gunicorn на Render)
init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
