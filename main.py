"""Дневники: кинодневник и читательский дневник на своём сервере."""
import base64
import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import (FileResponse, HTMLResponse, JSONResponse,
                                 RedirectResponse, Response)
from starlette.routing import Route

HERE = Path(__file__).parent
DB_PATH = os.environ.get("DB_PATH", str(HERE / "dnevnik.db"))
SECRET = os.environ.get("SECRET", "dev-secret").encode()
PASSWORD_HASH = os.environ.get("PASSWORD_HASH", "")  # salt$hex(scrypt)
COOKIE = "dn_auth"
COOKIE_AGE = 400 * 24 * 3600


# ---------- db ----------
def db():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS kv(name TEXT PRIMARY KEY, value TEXT NOT NULL, updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS books(id TEXT PRIMARY KEY, data TEXT NOT NULL, updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, value TEXT, ts REAL);
        """)


def kv_get(name):
    with db() as c:
        r = c.execute("SELECT value FROM kv WHERE name=?", (name,)).fetchone()
    return json.loads(r["value"]) if r else None


def kv_set(name, value):
    s = json.dumps(value, ensure_ascii=False)
    now = time.time()
    with db() as c:
        c.execute("INSERT INTO kv VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated=excluded.updated",
                  (name, s, now))
        # снимок раз в час — можно откатиться, если что-то пошло не так
        last = c.execute("SELECT ts FROM history WHERE name=? ORDER BY id DESC LIMIT 1", (name,)).fetchone()
        if not last or now - last["ts"] > 3600:
            c.execute("INSERT INTO history(name,value,ts) VALUES(?,?,?)", (name, s, now))
            c.execute("DELETE FROM history WHERE name=? AND id NOT IN (SELECT id FROM history WHERE name=? ORDER BY id DESC LIMIT 200)", (name, name))


# ---------- auth ----------
def check_password(pw: str) -> bool:
    if not PASSWORD_HASH or "$" not in PASSWORD_HASH:
        return False
    salt, want = PASSWORD_HASH.split("$", 1)
    got = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1).hex()
    return hmac.compare_digest(got, want)


def make_token():
    ts = str(int(time.time()))
    return ts + "." + hmac.new(SECRET, ts.encode(), hashlib.sha256).hexdigest()


def authed(req: Request) -> bool:
    t = req.cookies.get(COOKIE, "")
    if "." not in t:
        return False
    ts, sig = t.split(".", 1)
    good = hmac.new(SECRET, ts.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, good) and ts.isdigit() and time.time() - int(ts) < COOKIE_AGE


def guard(fn):
    async def w(req):
        if not authed(req):
            if req.url.path.startswith("/api/"):
                return JSONResponse({"error": "auth"}, 401)
            return RedirectResponse("/login?next=" + req.url.path, 303)
        return await fn(req)
    return w


HEAD = """<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<link rel="manifest" href="/manifest.webmanifest"><meta name="theme-color" content="#14110f">
<link rel="icon" href="/static/icon-192.png"><link rel="apple-touch-icon" href="/static/icon-192.png">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-title" content="Дневники">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">"""

PAGE_CSS = """<style>
:root{color-scheme:dark}*{box-sizing:border-box}
body{margin:0;min-height:100vh;background:#14110f;color:#f1ebe4;font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
display:flex;flex-direction:column;align-items:center;justify-content:center;gap:28px;padding:max(24px,env(safe-area-inset-top)) 20px 40px}
h1{font:600 34px/1.1 Georgia,serif;margin:0;letter-spacing:-.01em}
.sub{color:#a99d90;margin:-18px 0 0;font-size:15px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;width:100%;max-width:520px}
.tile{display:flex;flex-direction:column;gap:8px;padding:22px 18px;border-radius:16px;text-decoration:none;color:inherit;min-height:150px;justify-content:flex-end}
.tile .e{font-size:34px;line-height:1}.tile b{font:600 21px Georgia,serif}.tile span{font-size:13px;opacity:.8}
.kino{background:linear-gradient(160deg,#3a2418,#1f1511);border:1px solid #4d3224}
.books{background:linear-gradient(160deg,#16362a,#0f2a1f);border:1px solid #2a4d3e}
form{display:flex;flex-direction:column;gap:12px;width:100%;max-width:320px}
input{font:inherit;padding:12px 14px;border-radius:10px;border:1px solid #3a332d;background:#1d1916;color:inherit}
button{font:inherit;font-weight:600;padding:12px;border-radius:10px;border:0;background:#e8b04b;color:#1d1408;cursor:pointer}
.err{color:#f08a78;font-size:14px;margin:0}.foot{font-size:13px;color:#7d7268}.foot a{color:#a99d90}
</style>"""


async def login(req: Request):
    err = ""
    nxt = req.query_params.get("next", "/")
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = "/"
    if req.method == "POST":
        form = await req.form()
        if check_password(str(form.get("password", ""))):
            r = RedirectResponse(nxt, 303)
            r.set_cookie(COOKIE, make_token(), max_age=COOKIE_AGE, httponly=True, secure=req.url.scheme == "https", samesite="lax")
            return r
        time.sleep(1)
        err = '<p class="err">Неверный пароль</p>'
    return HTMLResponse(f"""<!doctype html><html lang="ru"><head>{HEAD}<title>Вход — Дневники</title>{PAGE_CSS}</head><body>
<h1>Дневники</h1><form method="post"><input type="password" name="password" placeholder="Пароль" autofocus autocomplete="current-password">
{err}<button>Войти</button></form></body></html>""")


async def logout(req):
    r = RedirectResponse("/login", 303)
    r.delete_cookie(COOKIE)
    return r


@guard
async def home(req):
    k = kv_get("kino") or {}
    with db() as c:
        nb = c.execute("SELECT COUNT(*) n FROM books").fetchone()["n"]
    nk = len(k.get("rated", []))
    empty = "" if (nk or nb) else '<p class="foot">Данных пока нет — <a href="/import">перенести из Claude</a></p>'
    return HTMLResponse(f"""<!doctype html><html lang="ru"><head>{HEAD}<title>Дневники</title>{PAGE_CSS}</head><body>
<h1>Дневники</h1><p class="sub">Иван · Ницца</p>
<div class="grid">
<a class="tile kino" href="/kino"><span class="e">🎬</span><b>Кино</b><span>{nk} оценок · {len(k.get('watch', []))} в планах</span></a>
<a class="tile books" href="/books"><span class="e">📚</span><b>Книги</b><span>{nb} книг</span></a>
</div>{empty}
<p class="foot"><a href="/export">Скачать копию</a> · <a href="/import">Импорт</a> · <a href="/logout">Выйти</a></p>
<script>if('serviceWorker' in navigator)navigator.serviceWorker.register('/sw.js');</script></body></html>""")


# ---------- shims: подменяют window.claude из артефактов ----------
HOME_LINK = """<a href="/" id="dn-home" aria-label="Все дневники" style="position:fixed;z-index:50;left:max(10px,env(safe-area-inset-left));bottom:calc(14px + env(safe-area-inset-bottom));width:40px;height:40px;border-radius:20px;background:rgba(0,0,0,.55);color:#fff;display:flex;align-items:center;justify-content:center;text-decoration:none;font:18px sans-serif;backdrop-filter:blur(6px)">⌂</a>"""

KINO_SHIM = """<script>
(function(){
  let ver = __VER__;
  function bar(msg){let b=document.getElementById('dn-bar');if(!b){b=document.createElement('div');b.id='dn-bar';
    b.style.cssText='position:fixed;left:0;right:0;top:0;z-index:60;padding:calc(10px + env(safe-area-inset-top)) 14px 10px;background:#b8432f;color:#fff;font:14px sans-serif;text-align:center;cursor:pointer';
    b.onclick=()=>location.reload();document.body.appendChild(b);}b.textContent=msg;}
  window.claude = {use: async (cap) => cap === 'artifact' ? {
    publish: async (html) => {
      const m = html.match(/<script id="kp-data" type="application\\/json">([\\s\\S]*?)<\\/script>/);
      if (!m) throw new Error('no data');
      const data = JSON.parse(m[1]);
      const r = await fetch('/api/kino', {method:'PUT', headers:{'Content-Type':'application/json','X-Base':String(ver)}, body: JSON.stringify(data)});
      if (r.status === 409) { bar('Список изменился на другом устройстве — нажмите, чтобы обновить'); const e=new Error('conflict'); e.code='conflict'; throw e; }
      if (r.status === 401) { location.href='/login?next=/kino'; return; }
      if (!r.ok) throw new Error('save failed');
      ver = (await r.json()).v;
    }} : null};
  // пришли с другого устройства — подтянуть свежие данные
  document.addEventListener('visibilitychange', async () => {
    if (document.visibilityState !== 'visible') return;
    try { const r = await fetch('/api/kino/ver'); const j = await r.json(); if (j.v > ver) location.reload(); } catch(e){}
  });
  try { localStorage.removeItem('kp-local'); } catch(e){}
})();
</script>"""

BOOKS_SHIM = """<script>
(function(){
  const subs = [];
  let cache = {};
  async function pull(){
    const r = await fetch('/api/books');
    if (r.status === 401) { location.href='/login?next=/books'; return; }
    if (!r.ok) throw new Error('load failed');
    cache = await r.json();
    const snap = {docs: Object.entries(cache).map(([id,d]) => ({id, data: () => d}))};
    subs.forEach(([ok]) => ok(snap));
  }
  async function send(method, id, body){
    const r = await fetch('/api/books/' + encodeURIComponent(id), {method, headers:{'Content-Type':'application/json'}, body: body && JSON.stringify(body)});
    if (!r.ok) throw new Error('Сервер не ответил (' + r.status + ')');
    pull().catch(()=>{});
  }
  window.claude = {use: async (cap) => cap === 'db' ? {
    collection: (name) => ({
      doc: (id) => ({ set: (d) => send('PUT', id, d), delete: () => send('DELETE', id) }),
      onSnapshot: (ok, bad) => { subs.push([ok, bad]); pull().catch(e => bad && bad(e)); }
    })
  } : null};
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') pull().catch(()=>{}); });
  setInterval(() => { if (document.visibilityState === 'visible') pull().catch(()=>{}); }, 30000);
})();
</script>"""

PWA_HEAD = """<link rel="manifest" href="/manifest.webmanifest"><link rel="apple-touch-icon" href="/static/icon-192.png">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">"""


def inject(html: str, shim: str, theme: str) -> str:
    head = PWA_HEAD + f'<meta name="theme-color" content="{theme}">'
    html = html.replace("</head>", head + "</head>", 1)
    # шим должен выполниться раньше кода артефакта
    i = html.find("<script", html.find("<body"))
    return html[:i] + shim + HOME_LINK + html[i:]


@guard
async def kino_page(req):
    tpl = (HERE / "kino.html").read_text()
    data = kv_get("kino") or {"v": 0, "rated": [], "watch": []}
    blob = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    html = tpl.replace("__KP_DATA__", blob, 1)
    html = inject(html, KINO_SHIM.replace("__VER__", str(int(data.get("v", 0)))), "#ece4f7")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@guard
async def books_page(req):
    html = inject((HERE / "books.html").read_text(), BOOKS_SHIM, "#0f2a1f")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


# ---------- api ----------
def valid_kino(d):
    return isinstance(d, dict) and isinstance(d.get("rated"), list) and isinstance(d.get("watch"), list)


@guard
async def api_kino(req):
    if req.method == "GET":
        return JSONResponse(kv_get("kino") or {"v": 0, "rated": [], "watch": []})
    d = await req.json()
    if not valid_kino(d):
        return JSONResponse({"error": "bad data"}, 400)
    cur = kv_get("kino") or {"v": 0}
    base = int(req.headers.get("X-Base", "-1"))
    if base != int(cur.get("v", 0)):
        return JSONResponse({"error": "conflict", "v": cur.get("v", 0)}, 409)
    d = {"v": int(cur.get("v", 0)) + 1, "rated": d["rated"], "watch": d["watch"]}
    kv_set("kino", d)
    return JSONResponse({"v": d["v"]})


@guard
async def api_kino_ver(req):
    return JSONResponse({"v": (kv_get("kino") or {}).get("v", 0)})


BOOK_FIELDS = {"title", "author", "status", "rating", "dateRead", "note", "added"}


def clean_book(d):
    if not isinstance(d, dict) or not str(d.get("title", "")).strip():
        return None
    return {k: d.get(k, "" if k != "rating" else 0) for k in BOOK_FIELDS}


@guard
async def api_books(req):
    with db() as c:
        rows = c.execute("SELECT id,data FROM books").fetchall()
    return JSONResponse({r["id"]: json.loads(r["data"]) for r in rows})


@guard
async def api_book(req):
    bid = req.path_params["id"]
    if not re.fullmatch(r"[\w-]{1,64}", bid):
        return JSONResponse({"error": "bad id"}, 400)
    with db() as c:
        if req.method == "DELETE":
            c.execute("DELETE FROM books WHERE id=?", (bid,))
        else:
            d = clean_book(await req.json())
            if not d:
                return JSONResponse({"error": "bad data"}, 400)
            c.execute("INSERT INTO books VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated=excluded.updated",
                      (bid, json.dumps(d, ensure_ascii=False), time.time()))
    return JSONResponse({"ok": True})


@guard
async def export(req):
    with db() as c:
        books = {r["id"]: json.loads(r["data"]) for r in c.execute("SELECT id,data FROM books")}
    blob = json.dumps({"format": "dnevnik-1", "kino": kv_get("kino"), "books": books}, ensure_ascii=False)
    name = time.strftime("dnevnik-%Y-%m-%d.json")
    return Response(blob, media_type="application/json", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@guard
async def import_page(req):
    msg = ""
    if req.method == "POST":
        form = await req.form()
        f = form.get("file")
        try:
            d = json.loads((await f.read()).decode("utf-8"))
            assert d.get("format") == "dnevnik-1"
            nk = nb = 0
            if valid_kino(d.get("kino")):
                cur = kv_get("kino") or {"v": 0}
                k = d["kino"]
                kv_set("kino", {"v": max(int(cur.get("v", 0)), int(k.get("v", 0))) + 1, "rated": k["rated"], "watch": k["watch"]})
                nk = len(k["rated"])
            with db() as c:
                for bid, b in (d.get("books") or {}).items():
                    b = clean_book(b)
                    if b and re.fullmatch(r"[\w-]{1,64}", bid):
                        c.execute("INSERT OR REPLACE INTO books VALUES(?,?,?)", (bid, json.dumps(b, ensure_ascii=False), time.time()))
                        nb += 1
            msg = f'<p class="sub" style="margin:0;color:#8fd19e">Готово: фильмов с оценкой {nk}, книг {nb}. <a href="/" style="color:inherit">На главную</a></p>'
        except Exception:
            msg = '<p class="err">Не похоже на файл дневников. Нужен dnevnik-import.json.</p>'
    return HTMLResponse(f"""<!doctype html><html lang="ru"><head>{HEAD}<title>Импорт — Дневники</title>{PAGE_CSS}</head><body>
<h1>Импорт</h1><p class="sub">Файл заменит фильмы целиком и добавит/обновит книги</p>
<form method="post" enctype="multipart/form-data"><input type="file" name="file" accept=".json,application/json" required>
<button>Загрузить</button>{msg}</form><p class="foot"><a href="/">← Назад</a></p></body></html>""")


# ---------- pwa ----------
MANIFEST = {
    "name": "Дневники", "short_name": "Дневники", "lang": "ru", "start_url": "/", "scope": "/",
    "display": "standalone", "background_color": "#14110f", "theme_color": "#14110f",
    "icons": [{"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
              {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
              {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}],
    "shortcuts": [{"name": "Кино", "url": "/kino"}, {"name": "Книги", "url": "/books"}],
}

SW = """const C='dn-v1';
self.addEventListener('install',e=>self.skipWaiting());
self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));
self.addEventListener('fetch',e=>{
  const u=new URL(e.request.url);
  if(e.request.method!=='GET'||u.origin!==location.origin||u.pathname.startsWith('/api/'))return;
  // сеть первой; без сети — последняя сохранённая версия страницы (только просмотр)
  e.respondWith(fetch(e.request).then(r=>{if(r.ok&&!r.redirected){const cp=r.clone();caches.open(C).then(c=>c.put(e.request,cp));}return r;})
    .catch(()=>caches.match(e.request)));
});"""


async def manifest(req):
    return JSONResponse(MANIFEST, media_type="application/manifest+json")


async def sw(req):
    return Response(SW, media_type="text/javascript", headers={"Cache-Control": "no-cache"})


ICONS = {
    "icon-192.png": base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAMAAAADACAIAAADdvvtQAAAC4ElEQVR4Xu3dzU0bYRRA0SHKHiFRBiVQABuKoAtKoAuKYEMBlEBBUWQpsZQ4DtwxfiOds/bPs7h+/jwsfHF9dbnAZ3377B3hJwGRCIhEQCQCIhEQiYBIBEQiIBIBkQiIREAkAiIREImASAREIiASAZEIiERAJAIiERCJgEgERCIgEgGRCIhEQCQCIhEQiYBIBEQiIBIBkQiIREAkAiIREImASAREIiASAZEIiERAJN/TvU/j/fn2NA98xM3D27Gb/Hb38vT/N17R6/3jio/WXYz6tZ5zpbPvaEbnSmffnIwGfYRNqGdZjowxoZ5lmTLGoF/rGVLPzqFhhtSzM2SYQRuILRoR0Kj1c2gJDXnH75sw0oiA2C4BkQiIREAkAiKZ+K+MnaNXhNdSvgN+2RXhCV+4/soGIhEQiYBIBEQy9xBdzrZfZuzZ9svYQCQCIhEQiYBI5h6i17oSfdLD+FpXord7GLeBSAREIiCSuWegk55d1rLds8tabCASAZEIiERAJAIiERCJgEgERCIgEgGRCIhEQCQCIhEQiYBIBEQiIBIBkQiIREAkAiIREImASAREIiASAZEIiERAJAIiERCJgEgERCIgEgGRCIhEQCQCIhEQiYBIBEQiIBIBkQiIREAkAiIREImASAREIiASAZEIiERAJAIiERCJgEgERCIgEgGRCIhEQCQCIhEQiYBIBEQiIBIBkQiIREAkAiIREImASAREMiKgm4e39CJO4M+RXu8fT/A8yYSRRgTEdk0JaNQSOjTMhHf8L0OGmRLQskxp6N8pD/mzDRljWZaL66vLafvz/fn2LCN9aAvevTydZcg56exMDIgNGfQRxhYJiERAJAIiERCJgEgERCIgEgGRCIhEQCQCIhEQiYBIBEQiIBIBkQiIREAkAiIREImASAREIiASAZEIiERAJAIiERCJgEgERCIgEgGRCIhEQCQCIhEQiYBIBEQiIBIBkQiIREAkPwDpYUq7GW6pZAAAAABJRU5ErkJggg=="),
    "icon-512.png": base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAgAAAAIACAIAAAB7GkOtAAAIx0lEQVR4Xu3czU3dQBSA0RBlj5AogxJSQDYUQReUkC5SBBsKoIQUFEXsgvLDs2fGzndOATz7Wrrfe0aaq9ub6w8A9Hzs3TIAPwkAQJQAAEQJAECUAABECQBAlAAARAkAQJQAAEQJAECUAABECQBAlAAARAkAQJQAAEQJAECUAABECQBAlAAARAkAQJQAAEQJAECUAABECQBAlAAARAkAQJQAAEQJAECUAABECQBAlAAARAkAQJQAAEQJAECUAABECQBAlAAARAkAQJQAAEQJAECUAABECQBAlAAARAkAQJQAAEQJAECUAABECQBAlAAARAkAQJQAAEQJAECUAABECQBAlAAARAkAQJQAAEQJAECUAABECQBAlAAARAkAQJQAAEQJAECUAABECQBAlAAARAkAQJQAAEQJAECUAABECQBAlAAARAkAQJQAAEQJAECUAABECQBAlAAARAkAQJQAAEQJAECUAABECQBAlAAARAkAQJQAAEQJAECUAABECQBAlAAARAkAQJQAAEQJAECUAABECQBAlAAARAkAQJQAAEQJAECUAABECQBAlAAARAkAQJQAAEQJAECUAABECQBAlAAARAkAQJQAAEQJAECUAABECQBAlAAARAkAQJQAAEQJAECUAABEfYre9yF9//b5kNd1aHcPL2uv78vT17UXcEbP949nvOz/z9XtzfX/d1fnYu/vYnIJ7P1dKMFaArCS1b+7CRmw+ncnA6v4H8Aytv8Io6dq+49gqqsIwBqj91TZuNnaU+OY7RICsMC4DcWrERO2oUYz4fkEYLYRu4m39p2z3TSHOU8mAFPtu5X4s72mbSvNZNozCcA8e+0j/t32mdtH85n5NAIwyfZNxGW2TN4mWsXk5xAAgCgBmGHLl1C2u2z+voSuZf4TCABAlAAARAnAcJe9f2Bf730K3j8cgacwmgAARAkAQJQAAEQJAECUAABECQBAlAAARAkAQJQAAEQJAECUAABECQBAlAAARAkAQJQAAER9it73Sdw9vJzkSid577H+SzzfPy753MNyrP9h+QUAECUAAFECABAlAABRAgAQJQAAUQIAECUAAFECABAlAABRAgAQJQAAUQ6DO7RTnH3GL5x9xln4BQAQJQAAUQIAECUAAFECABAlAABRAgAQJQAAUQIAECUAAFECABAlAABRDoM7tLuHl0Nf32/Ez7B7vn8841Nzhl2QXwAAUQIAECUAAFECABAlAABRAgAQJQAAUQIAECUAAFECABDlKIhDi5+pcFLOVOAs/AIAiBIAgCgBAIgSAIAoAQCIEgCAKAEAiBIAgCgBAIgSAIAoAQCIEgCAKAEAiBIAgCgBAIgSAIAoAQCIEgCAKAEAiBIAgCgBAIgSAIAoAQCIEgCAKAEAiBIAgCgBAIgSAIAoAQCIEgCAKAEAiBIAgCgBAIgSAIAoAQCIEgCAKAEAiBIAgCgBAIgSAIAoAQCIEgCAKAEAiBIAgCgBAIgSAIAoAQCIEgCAKAEAiBIAgCgBAIgSAIAoAQCIEgCAKAEAiBIAgCgBAIgSAIAoAQCIEgCAKAEAiBIAgCgBAIgSAIAoAQCIEgCAKAEAiBIAgCgBAIgSAIAoAQCIEgCAKAEAiBIAgCgBAIgSAIAoAQCIEgCAKAEAiBIAgCgBAIgSAIAoAQCIEgCAKAEAiBIAgCgBAIgSAIAoAQCIEgCAKAEAiBIAgCgBAIgSAIAoAQCIEgCAKAEAiBIAgCgBAIgSAIAoAQCIEgCAKAEAiBIAgCgBAIgSAIAoAQCIEgCAKAEAiBIAgCgBAIgSAIAoAQCIEgCAKAEAiBIAgCgBAIgSAIAoAQCIEgCAKAEAiBIAgCgBAIgSAIAoAQCIEgCAKAEAiBIAgCgBAIgSAIAoAQCIEgCAKAEAiBIAgCgBAIgSAIAoAQCIEgCAKAEAiBIAgCgBAIgSAIAoAQCIEgCAKAEAiBIAgCgBAIgSAIAoAQCIEgCAKAEAiBIAgCgBAIgSAIAoAQCIEgCAKAEAiBIAgCgBAIgSAIAoAQCIEgCAKAEAiBIAgCgBAIgSAIAoAQCIEgCAKAEAiBIAgCgBAIgSAIAoAQCIEgCAKAEAiBIAgCgBAIgSAIAoAQCIEgCAKAEAiBIAgCgBAIgSAIAoAQCIEgCAKAEAiBIAgCgBAIgSAIAoAQCIEoDh7h5ehn8Gf/Pep/B8//i3P8lwnsJoAgAQJQAAUQIww3vfP7Cvy+bv/cNa5j+BAABECcAkl30JZbstk/cldBWTn0MA5tmyibjM9pnbRPOZ+TQCMNX2fcS/22va9tFMpj2TAMy211biz/ads600hzlPJgAL7LubeGvEhO2m0Ux4PgFYY8SG4tW42dpQ45jtEgKwzLg9VTZ6qvbUCKa6ytXtzfWqz+bV92+fjWK70av/F1+evm6/Zqz+tQTgQJTgApP3/ltKcAF7/yAEACDK/wAAogQAIEoAAKIEACBKAACiBAAgSgAAogQAIEoAAKIEACBKAACiBAAgSgAAogQAIEoAAKIEACBKAACiBAAgSgAAogQAIEoAAKIEACBKAACiBAAgSgAAogQAIEoAAKIEACBKAACiBAAgSgAAogQAIEoAAKIEACBKAACiBAAgSgAAogQAIEoAAKIEACBKAACiBAAgSgAAogQAIEoAAKIEACBKAACiBAAgSgAAogQAIEoAAKIEACBKAACiBAAgSgAAogQAIEoAAKIEACBKAACiBAAgSgAAogQAIEoAAKIEACBKAACiBAAgSgAAogQAIEoAAKIEACBKAACiBAAgSgAAogQAIEoAAKIEACBKAACiBAAgSgAAogQAIEoAAKIEACBKAACiBAAgSgAAogQAIEoAAKIEACBKAACiBAAgSgAAogQAIEoAAKIEACBKAACiBAAgSgAAogQAIEoAAKIEACBKAACiBAAgSgAAogQAIEoAAKIEACBKAACiBAAgSgAAogQAIEoAAKIEACBKAACiBAAgSgAAogQAIEoAAKIEACDqB80QoaFBypUaAAAAAElFTkSuQmCC"),
}


async def static(req):
    data = ICONS.get(req.path_params["name"])
    if data is None:
        return Response(status_code=404)
    return Response(data, media_type="image/png", headers={"Cache-Control": "public, max-age=604800"})


init_db()
app = Starlette(routes=[
    Route("/", home), Route("/login", login, methods=["GET", "POST"]), Route("/logout", logout),
    Route("/kino", kino_page), Route("/books", books_page),
    Route("/api/kino", api_kino, methods=["GET", "PUT"]), Route("/api/kino/ver", api_kino_ver),
    Route("/api/books", api_books), Route("/api/books/{id}", api_book, methods=["PUT", "DELETE"]),
    Route("/export", export), Route("/import", import_page, methods=["GET", "POST"]),
    Route("/manifest.webmanifest", manifest), Route("/sw.js", sw), Route("/favicon.ico", lambda r: Response(ICONS["icon-192.png"], media_type="image/png")), Route("/static/{name}", static),
])
