#!/usr/bin/env python3
"""Переносит Дневники в Ryot: ищет фильмы в TMDB, книги в OpenLibrary и собирает
файл для импорта в Ryot (Import → Generic Json).

    python3 /opt/dnevnik/ryot_import.py

Результат: /root/ryot-import.json и список ненайденного /root/ryot-unmatched.txt
Повторный запуск быстрый: найденное кешируется в /root/ryot-match-cache.json
"""
import json
import re
import sqlite3
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

DB = "/var/lib/dnevnik/dnevnik.db"
ENV = "/opt/ryot/.env"
OUT = "/root/ryot-import.json"
MISS = "/root/ryot-unmatched.txt"
CACHE = "/root/ryot-match-cache.json"
UA = "dnevnik-ryot-import/1.0 (personal use)"


def load_token():
    for line in open(ENV, encoding="utf-8"):
        if line.startswith("TMDB="):
            return line.strip().split("=", 1)[1]
    sys.exit("Нет TMDB= в /opt/ryot/.env — запустите bash /opt/dnevnik/ryot.sh")


TOKEN = load_token()


def get(url, headers=None, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json", **(headers or {})})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                sys.exit("TMDB ответил 401: токен неверный. Нужен API Read Access Token (eyJ...)")
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(1.5 * (i + 1))
                continue
            return None
        except Exception:
            time.sleep(1.5 * (i + 1))
    return None


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).lower().replace("ё", "е")
    return re.sub(r"[^\w]+", "", s)


def tmdb(kind, q, year=None):
    p = {"query": q, "language": "ru-RU", "include_adult": "false"}
    if year:
        p["primary_release_year" if kind == "movie" else "first_air_date_year"] = year
    d = get(f"https://api.themoviedb.org/3/search/{kind}?" + urllib.parse.urlencode(p),
            {"Authorization": "Bearer " + TOKEN})
    return (d or {}).get("results") or []


def pick(results, kind, names, year):
    best, best_score = None, -1
    for r in results[:10]:
        date = r.get("release_date" if kind == "movie" else "first_air_date") or ""
        ry = int(date[:4]) if date[:4].isdigit() else None
        titles = {norm(r.get("title") or r.get("name")), norm(r.get("original_title") or r.get("original_name"))}
        score = 0
        if year and ry:
            dy = abs(ry - year)
            if dy > 1:
                continue
            score += 3 if dy == 0 else 1
        if titles & names:
            score += 4
        score += min(r.get("vote_count") or 0, 2000) / 2000
        if score > best_score:
            best, best_score = r, score
    return best


def match_film(row):
    title, orig, year = row[0], row[1], row[2]
    year = int(year) if str(year).isdigit() else None
    names = {norm(title), norm(orig)} - {""}
    queries = [q for q in (orig, title) if q]
    for kind, use_year in (("movie", True), ("tv", True), ("movie", False), ("tv", False)):
        if use_year and not year:
            continue
        for q in queries:
            r = pick(tmdb(kind, q, year if use_year else None), kind, names, year)
            if r:
                if not use_year and year:
                    # без года принимаем только точное совпадение названия
                    t = {norm(r.get("title") or r.get("name")), norm(r.get("original_title") or r.get("original_name"))}
                    if not (t & names):
                        continue
                return {"id": str(r["id"]), "lot": "movie" if kind == "movie" else "show",
                        "found": r.get("title") or r.get("name"),
                        "year": (r.get("release_date") or r.get("first_air_date") or "")[:4]}
    return None


def match_book(b):
    title, author = b.get("title", ""), b.get("author", "")
    first_author = re.split(r",| и ", author)[0].strip()
    tries = [{"title": title, "author": first_author}, {"q": f"{title} {first_author}"}, {"title": title}]
    for p in tries:
        p = {k: v for k, v in p.items() if v}
        p.update({"limit": "5", "fields": "key,title,author_name,first_publish_year"})
        d = get("https://openlibrary.org/search.json?" + urllib.parse.urlencode(p))
        for doc in (d or {}).get("docs") or []:
            if not doc.get("key", "").startswith("/works/"):
                continue
            if "q" in p or "author" in p or norm(doc.get("title")) == norm(title):
                return {"id": doc["key"].split("/")[-1], "lot": "book", "found": doc.get("title"),
                        "year": str(doc.get("first_publish_year") or "")}
        time.sleep(0.3)
    return None


def main():
    con = sqlite3.connect(DB)
    row = con.execute("SELECT value FROM kv WHERE name='kino'").fetchone()
    kino = json.loads(row[0]) if row else {"rated": [], "watch": []}
    books = {r[0]: json.loads(r[1]) for r in con.execute("SELECT id,data FROM books")}
    if not kino["rated"] and not books:
        sys.exit("В Дневниках пока нет данных — сначала загрузите dnevnik-import.json на dnevnik.ivansereda.fr/import")

    try:
        cache = json.load(open(CACHE, encoding="utf-8"))
    except Exception:
        cache = {}

    films = [("rated", r) for r in kino["rated"]] + [("watch", r) for r in kino["watch"]]
    todo = [(k, r) for k, r in films if f"f|{r[0]}|{r[1]}|{r[2]}" not in cache]
    print(f"Фильмов: {len(films)} (искать: {len(todo)}), книг: {len(books)}")

    done = 0

    def work(item):
        nonlocal done
        _, r = item
        res = match_film(r)
        cache[f"f|{r[0]}|{r[1]}|{r[2]}"] = res
        done += 1
        if done % 100 == 0:
            print(f"  фильмы: {done}/{len(todo)}", flush=True)
            json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)

    with ThreadPoolExecutor(8) as ex:
        list(ex.map(work, todo))
    json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)

    for bid, b in books.items():
        k = f"b|{b.get('title')}|{b.get('author')}"
        if k not in cache:
            cache[k] = match_book(b)
    json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)

    now = datetime.now(timezone.utc).isoformat()
    watch_coll = [{"collection_id": "", "collection_name": "Watchlist", "created_on": now, "creator_user_id": "",
                   "information": None, "last_updated_on": now}]
    items, missing = {}, []

    def add(key, lot, source, src_id, rating100=None, seen=None, watch=False, note=None):
        it = items.setdefault((source, key), {"identifier": key, "lot": lot, "source": source, "source_id": src_id,
                                              "collections": [], "reviews": [], "seen_history": []})
        if rating100 is not None or note:
            it["reviews"] = [{"rating": None if rating100 is None else str(rating100),
                              "review": {"text": note, "date": None, "spoiler": False, "visibility": "private"} if note else None}]
        if seen is not None and lot != "show":
            it["seen_history"] = [seen]
        if watch and not it["collections"]:
            it["collections"] = watch_coll

    for kind, r in films:
        m = cache.get(f"f|{r[0]}|{r[1]}|{r[2]}")
        label = f"{r[0]}" + (f" / {r[1]}" if r[1] else "") + f" ({r[2]})"
        if not m:
            missing.append(("Фильм к просмотру" if kind == "watch" else "Фильм") + ": " + label)
            continue
        if kind == "rated":
            rating = r[4] if len(r) > 4 else None
            add(m["id"], m["lot"], "tmdb", label, None if rating is None else int(rating) * 10,
                {"state": "completed", "ended_on": None})
        else:
            add(m["id"], m["lot"], "tmdb", label, watch=True)

    for bid, b in books.items():
        m = cache.get(f"b|{b.get('title')}|{b.get('author')}")
        label = f"{b.get('title')} — {b.get('author')}"
        if not m:
            missing.append("Книга: " + label)
            continue
        st = b.get("status")
        rating = b.get("rating") or 0
        if st == "read":
            date = (b.get("dateRead") or "") or None
            add(m["id"], "book", "openlibrary", label, int(round(rating * 20)) if rating else None,
                {"state": "completed", "ended_on": date}, note=b.get("note") or None)
        elif st == "reading":
            add(m["id"], "book", "openlibrary", label, seen={"state": "in_progress", "progress": "0"},
                note=b.get("note") or None)
        else:
            add(m["id"], "book", "openlibrary", label, watch=True, note=b.get("note") or None)

    export = {"metadata": list(items.values()), "collections": None, "exercises": None, "measurements": None,
              "metadata_groups": None, "people": None, "workout_templates": None, "workouts": None}
    json.dump(export, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    open(MISS, "w", encoding="utf-8").write("\n".join(missing) + "\n")

    nf = sum(1 for k, r in films if cache.get(f"f|{r[0]}|{r[1]}|{r[2]}"))
    nb = sum(1 for b in books.values() if cache.get(f"b|{b.get('title')}|{b.get('author')}"))
    print()
    print(f"Найдено фильмов: {nf} из {len(films)}, книг: {nb} из {len(books)}")
    print(f"Файл для Ryot: {OUT}")
    print(f"Не найдено ({len(missing)}): {MISS}")


if __name__ == "__main__":
    main()
