"""Download the Chinook SQLite test database into this folder.

Run once from the project root:
    python benchmark/fixtures/download_chinook.py
"""
import sqlite3
import urllib.request
from pathlib import Path

URL = (
    "https://raw.githubusercontent.com/lerocha/chinook-database/"
    "master/ChinookDatabase/DataSources/Chinook_Sqlite.sqlite"
)
TARGET = Path(__file__).resolve().parent / "chinook.sqlite"


def main():
    if TARGET.exists():
        print(f"{TARGET.name} already exists, nothing to do.")
    else:
        print("Downloading Chinook...")
        urllib.request.urlretrieve(URL, TARGET)

    # Quick check: open read-only and count rows per table.
    con = sqlite3.connect(f"file:{TARGET}?mode=ro", uri=True)
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    for t in tables:
        n = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        print(f"  {t}: {n} rows")
    con.close()
    print(f"OK: {len(tables)} tables (expected 11).")


if __name__ == "__main__":
    main()
