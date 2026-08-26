# -*- coding: utf-8 -*-
"""Показывает персональные ссылки всех игроков.

Использование (локально):
    python show_links.py
    python show_links.py https://ваш-сайт.onrender.com

На Render можно запустить во вкладке Shell:
    python show_links.py https://ваш-сайт.onrender.com
"""
import sys
import sqlite3

from app import DB_PATH

base = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://localhost:5000"

db = sqlite3.connect(DB_PATH)
db.row_factory = sqlite3.Row

print("Персональные ссылки для редактирования:\n")
for r in db.execute("SELECT name, token FROM people ORDER BY id"):
    print(f"  {r['name']:10} {base}/edit/{r['token']}")
print("\nОбщая ссылка (только просмотр):")
print(f"  {base}/")
