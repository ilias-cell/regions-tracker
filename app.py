# -*- coding: utf-8 -*-
"""Семейный трекер посещённых регионов России.

Flask + SQLite. Готов к деплою на Render.com.

Режимы доступа:
  /                — публичная страница, только чтение
  /edit/<token>    — персональная ссылка игрока, редактирование только своих регионов
"""
import os
import secrets
import sqlite3
from contextlib import closing

from flask import (
    Flask, g, render_template, request, redirect, url_for, jsonify, abort
)

from regions_data import REGIONS, DISTRICT_ORDER

app = Flask(__name__)

# На Render постоянный диск монтируется в /var/data (см. render.yaml).
# Локально — файл рядом с приложением.
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "tracker.db"))

# Участники соревнования. Можно поменять имена здесь.
PEOPLE = [
    (1, "Илья"),
    (2, "Катя"),
    (3, "Кирилл"),
    (4, "Данил"),
]


def make_token():
    """Случайный URL-безопасный токен для персональной ссылки."""
    return secrets.token_urlsafe(12)


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
    """Создаёт таблицы, наполняет справочники и делает миграции."""
    with closing(sqlite3.connect(DB_PATH)) as db:
        db.row_factory = sqlite3.Row
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS people (
                id    INTEGER PRIMARY KEY,
                name  TEXT NOT NULL,
                token TEXT
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

        # --- Миграция: если БД была создана старой версией без колонки token ---
        cols = [r["name"] for r in db.execute("PRAGMA table_info(people)")]
        if "token" not in cols:
            db.execute("ALTER TABLE people ADD COLUMN token TEXT")

        # Наполняем людей (имена не перезатираем, если игрок уже есть)
        for pid, name in PEOPLE:
            db.execute(
                "INSERT OR IGNORE INTO people (id, name) VALUES (?, ?)",
                (pid, name),
            )

        # Выдаём токен каждому, у кого его ещё нет
        for row in db.execute("SELECT id FROM people WHERE token IS NULL OR token = ''"):
            db.execute(
                "UPDATE people SET token=? WHERE id=?",
                (make_token(), row["id"]),
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


def person_by_token(token):
    """Возвращает игрока по токену или None."""
    if not token:
        return None
    row = get_db().execute(
        "SELECT id, name, token FROM people WHERE token=?", (token,)
    ).fetchone()
    return dict(row) if row else None


# --------------------------------------------------------------------------- #
#  Маршруты
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    """Публичная страница — только чтение."""
    state = load_state()
    return render_template(
        "index.html",
        editor=None,          # никто не редактирует
        edit_person_id=None,
        **state,
    )


@app.route("/edit/<token>")
def edit(token):
    """Персональная страница игрока — можно менять только свои регионы."""
    editor = person_by_token(token)
    if editor is None:
        abort(404)
    state = load_state()
    return render_template(
        "index.html",
        editor=editor,                 # объект игрока-редактора
        edit_person_id=editor["id"],   # чьи ячейки кликабельны
        **state,
    )


@app.route("/toggle", methods=["POST"])
def toggle():
    """Переключает статус посещения региона игроком (AJAX).

    Разрешено только владельцу токена и только для СВОИХ регионов.
    """
    token = request.form.get("token", "")
    region_code = request.form.get("region_code", "")

    editor = person_by_token(token)
    if editor is None:
        return jsonify({"error": "forbidden"}), 403

    person_id = editor["id"]  # берём id из токена, а НЕ из формы — защита от подмены

    db = get_db()

    # Проверяем, что регион существует
    if not db.execute("SELECT 1 FROM regions WHERE code=?", (region_code,)).fetchone():
        return jsonify({"error": "bad region"}), 400

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

    return jsonify({"visited": visited, "total": total, "person_id": person_id})


# Инициализируем БД при импорте (важно для gunicorn на Render)
init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
