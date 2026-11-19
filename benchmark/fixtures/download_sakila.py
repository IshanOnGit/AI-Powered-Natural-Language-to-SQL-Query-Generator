"""Download the Sakila SQLite test database into this folder.

Run once from the project root:
    python benchmark/fixtures/download_sakila.py

Sakila is originally a MySQL database; this is a community SQLite port, so the
script checks its row counts against the original before you rely on it.
If the download fails, put the file here by hand and name it sakila.sqlite.
"""
import sqlite3
import urllib.request
from pathlib import Path

URLS = [
    "https://github.com/jOOQ/sakila/raw/main/sqlite-sakila-db/sqlite-sakila.db",
    "https://raw.githubusercontent.com/jOOQ/sakila/main/sqlite-sakila-db/sqlite-sakila.db",
]
TARGET = Path(__file__).resolve().parent / "sakila.sqlite"

# Row counts of the original MySQL Sakila.
EXPECTED = {
    "actor": 200, "category": 16, "film": 1000, "film_actor": 5462,
    "film_category": 1000, "inventory": 4581, "customer": 599,
    "rental": 16044, "payment": 16049, "city": 600, "country": 109,
}


def download():
    for url in URLS:
        try:
            print(f"Trying {url}")
            urllib.request.urlretrieve(url, TARGET)
            sqlite3.connect(f"file:{TARGET}?mode=ro", uri=True).execute(
                "SELECT count(*) FROM sqlite_master").fetchone()
            return
        except Exception as e:
            print(f"  failed: {type(e).__name__}")
            TARGET.unlink(missing_ok=True)
    raise SystemExit("Could not download Sakila. Download a SQLite port by hand "
                     f"and save it as {TARGET}")


def main():
    if TARGET.exists():
        print(f"{TARGET.name} already exists, nothing to do.")
    else:
        download()

    con = sqlite3.connect(f"file:{TARGET}?mode=ro", uri=True)
    problems = 0
    for table, want in EXPECTED.items():
        try:
            got = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        except sqlite3.Error:
            got = None
        flag = "ok" if got == want else "MISMATCH"
        problems += got != want
        print(f"  {table}: {got} rows (expected {want}) {flag}")
    con.close()
    print("OK: matches the original Sakila." if not problems
          else f"{problems} table(s) differ. Check this port before relying on it.")


if __name__ == "__main__":
    main()
