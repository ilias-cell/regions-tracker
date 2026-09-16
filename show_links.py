"""
show_links.py — local helper to print personal edit links.

Run this locally (never on Render) to share links with players:
    python show_links.py [base_url]

Default base_url: http://localhost:5000
"""

import sys

BASE_URL = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://localhost:5000"

# Must stay in sync with PEOPLE in app.py
PEOPLE = [
    (1, "Илья",   "tok_Hv8kQw3mZpLxNbRqYeJdTfUsCgAiOvWn"),
    (2, "Катя",   "tok_Xr2aNcEdKsFjGhTyUiBvLmPwQoZxRnYp"),
    (3, "Кирилл", "tok_Dq7wEtYuIoPaSlKjHfGdSzXcVbNmQrTy"),
    (4, "Данил",  "tok_Mk5vBnCxZaQwErTyUiOpLkJhGfDsApRe"),
]

print(f"\nПерсональные ссылки редактирования ({BASE_URL}):\n")
for pid, name, tok in PEOPLE:
    print(f"  {name:<10}  {BASE_URL}/edit/{tok}")
print()
