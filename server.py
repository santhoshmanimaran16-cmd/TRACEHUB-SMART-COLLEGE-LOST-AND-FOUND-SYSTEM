#!/usr/bin/env python3
"""TraceHub - smart college lost & found. Python standard-library backend (http.server + sqlite3).
Run:  python3 server.py      then open  http://localhost:8000
"""
import base64, hashlib, hmac, json, math, os, re, secrets, sqlite3, sys, time
from collections import Counter
from datetime import date, datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, 'tracehub.db')
UPLOADS = os.path.join(ROOT, 'uploads')
STATIC = os.path.join(ROOT, 'static')
os.makedirs(UPLOADS, exist_ok=True)

PLACES = {  # name -> (lat, lon)
    'Main Block': (10.8412, 78.7621), 'Library': (10.8418, 78.7630), 'Canteen': (10.8405, 78.7634),
    'Hostel': (10.8398, 78.7612), 'Bus Bay': (10.8423, 78.7609), 'Sports Complex': (10.8430, 78.7640),
    'Seminar Hall': (10.8415, 78.7626), 'Auditorium': (10.8409, 78.7642), 'Admin Block': (10.8421, 78.7618),
    'Lab Block': (10.8402, 78.7624),
}
CATEGORIES = ['Electronics', 'Wallet & Cards', 'Keys', 'Bags', 'Books & Documents', 'Bottles & Lunch', 'Clothing & Accessories', 'Other']
POINTS = {'lost': 2, 'found': 10, 'return': 50}
STOP = set('a an the and or of in on at to is was it its my i me with for from by this that near some very'.split())
SYN = [{'phone', 'mobile', 'smartphone', 'cellphone', 'iphone', 'android'}, {'wallet', 'purse', 'billfold'},
       {'bag', 'backpack', 'rucksack', 'satchel'}, {'bottle', 'flask', 'tumbler'}, {'id', 'card', 'identity'},
       {'earphones', 'earbuds', 'airpods', 'headphones', 'headset'}, {'charger', 'adapter', 'cable'},
       {'glasses', 'spectacles', 'specs'}, {'key', 'keys', 'keychain'}, {'umbrella', 'brolly'}]
CANON = {w: sorted(s)[0] for s in SYN for w in s}

# ------------------------------------------------------------------ db
def db():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    c = db()
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT, email TEXT UNIQUE, dept TEXT, pw TEXT, salt TEXT, role TEXT DEFAULT 'student', points INTEGER DEFAULT 0, created REAL);
    CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER, created REAL);
    CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY, kind TEXT, title TEXT, description TEXT, category TEXT, location TEXT, event_date TEXT, image TEXT, secret TEXT, status TEXT DEFAULT 'open', user_id INTEGER, created REAL);
    CREATE TABLE IF NOT EXISTS claims(id INTEGER PRIMARY KEY, item_id INTEGER, claimant_id INTEGER, answer TEXT, score REAL, status TEXT, otp_hash TEXT, otp_salt TEXT, otp_exp REAL, tries INTEGER DEFAULT 0, created REAL);
    CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY, user_id INTEGER, text TEXT, item_id INTEGER, seen INTEGER DEFAULT 0, created REAL);
    ''')
    c.commit()
    if c.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0:
        seed(c)
    c.close()

def hash_pw(pw, salt):
    return hashlib.pbkdf2_hmac('sha256', pw.encode(), salt.encode(), 100_000).hex()

def add_user(c, name, email, pw, dept='', role='student'):
    salt = secrets.token_hex(8)
    cur = c.execute('INSERT INTO users(name,email,dept,pw,salt,role,created) VALUES(?,?,?,?,?,?,?)',
                    (name, email.lower(), dept, hash_pw(pw, salt), salt, role, time.time()))
    return cur.lastrowid

def seed(c):
    admin = add_user(c, 'Campus Admin', 'admin@tracehub.edu', 'admin123', 'Administration', 'admin')
    a = add_user(c, 'Aarav K', 'aarav@tracehub.edu', 'demo123', 'AI & DS')
    b = add_user(c, 'Meena R', 'meena@tracehub.edu', 'demo123', 'CSE')
    d = add_user(c, 'Karthik S', 'karthik@tracehub.edu', 'demo123', 'ECE')
    today = date.today()
    def D(n): return date.fromordinal(today.toordinal() - n).isoformat()
    rows = [
        ('lost', 'Black leather wallet', 'Black leather wallet with college ID card and some cash. Lost near the canteen.', 'Wallet & Cards', 'Canteen', D(2), None, a),
        ('found', 'Black wallet with ID card', 'Found a black wallet with an ID card inside near the canteen tables.', 'Wallet & Cards', 'Canteen', D(1), '500 rupees cash and name Meena on the ID', b),
        ('lost', 'Blue phone with cracked screen', 'Blue smartphone with a cracked screen guard and a transparent case.', 'Electronics', 'Library', D(3), None, b),
        ('found', 'Blue mobile phone', 'Blue mobile phone found on a reading table in the library, cracked screen guard.', 'Electronics', 'Library', D(2), 'mountain wallpaper', d),
        ('found', 'Set of keys with red keychain', 'Three keys on a red keychain found at the bus bay.', 'Keys', 'Bus Bay', D(1), 'three keys brass tag', d),
        ('lost', 'Grey backpack with laptop sleeve', 'Grey backpack containing a laptop sleeve and notebooks. Left in the seminar hall.', 'Bags', 'Seminar Hall', D(1), None, d),
        ('found', 'Scientific calculator Casio', 'Casio scientific calculator found in the lab block.', 'Electronics', 'Lab Block', D(2), 'initials RK scratched on the back', a),
        ('lost', 'Steel water bottle', 'Silver steel bottle with a sticker of a mountain, lost at the sports complex.', 'Bottles & Lunch', 'Sports Complex', D(4), None, a),
    ]
    for k, t, desc, cat, loc, dt, sec, uid in rows:
        c.execute('INSERT INTO items(kind,title,description,category,location,event_date,secret,user_id,created) VALUES(?,?,?,?,?,?,?,?,?)',
                  (k, t, desc, cat, loc, dt, sec, uid, time.time() - 3600 * (len(t) % 7 + 1)))
    c.execute('UPDATE users SET points=30 WHERE id=?', (a,)); c.execute('UPDATE users SET points=62 WHERE id=?', (b,)); c.execute('UPDATE users SET points=110 WHERE id=?', (d,))
    c.commit()

# ------------------------------------------------------------------ matching
def tokens(s):
    out = []
    for w in re.findall(r"[a-z0-9]+", (s or '').lower()):
        if w in STOP: continue
        if len(w) > 3 and w.endswith('s') and not w.endswith('ss'): w = w[:-1]
        out.append(CANON.get(w, w))
    return out

def cosine(a, b, idf):
    va = {k: v * idf.get(k, 1.0) for k, v in Counter(a).items()}
    vb = {k: v * idf.get(k, 1.0) for k, v in Counter(b).items()}
    dot = sum(va[k] * vb[k] for k in va if k in vb)
    na = math.sqrt(sum(v * v for v in va.values())); nb = math.sqrt(sum(v * v for v in vb.values()))
    return dot / (na * nb) if na and nb else 0.0

def haversine(p, q):
    R = 6371000.0
    la1, lo1, la2, lo2 = map(math.radians, (p[0], p[1], q[0], q[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))

def matches_for(c, item, limit=5):
    rows = c.execute("SELECT * FROM items WHERE status='open'").fetchall()
    docs = [tokens(r['title'] + ' ' + r['description']) for r in rows]
    df = Counter(w for d in docs for w in set(d))
    idf = {w: math.log((1 + len(docs)) / (1 + n)) + 1 for w, n in df.items()}
    me = tokens(item['title'] + ' ' + item['description'])
    out = []
    for r in rows:
        if r['kind'] == item['kind'] or r['id'] == item['id']: continue
        lost, found = (item, r) if item['kind'] == 'lost' else (r, item)
        if lost['category'] != found['category']: continue          # gate 1
        if found['event_date'] < lost['event_date']: continue       # gate 2
        t = cosine(me, tokens(r['title'] + ' ' + r['description']), idf)
        pa, pb = PLACES.get(item['location']), PLACES.get(r['location'])
        l = math.exp(-haversine(pa, pb) / 200.0) if pa and pb else 0.3
        days = (date.fromisoformat(found['event_date']) - date.fromisoformat(lost['event_date'])).days
        d = math.exp(-days / 5.0)
        s = 0.55 * t + 0.25 * l + 0.20 * d
        out.append({'id': r['id'], 'score': round(s, 3), 'text': round(t, 2), 'place': round(l, 2), 'time': round(d, 2)})
    out.sort(key=lambda x: -x['score'])
    return [m for m in out[:limit] if m['score'] > 0.25]

# ------------------------------------------------------------------ helpers
def item_json(c, r, me=None, full=False):
    u = c.execute('SELECT name FROM users WHERE id=?', (r['user_id'],)).fetchone()
    d = {k: r[k] for k in ('id', 'kind', 'title', 'description', 'category', 'location', 'event_date', 'status', 'created', 'user_id')}
    d['image'] = r['image']; d['owner'] = u['name'] if u else '?'
    d['has_question'] = bool(r['secret']) and r['kind'] == 'found'
    if r['kind'] == 'found' and r['secret']: d['question'] = 'The finder kept one detail hidden. What is something only the owner would know?'
    return d

def note(c, uid, text, item_id=None):
    c.execute('INSERT INTO notes(user_id,text,item_id,created) VALUES(?,?,?,?)', (uid, text, item_id, time.time()))

def award(c, uid, n):
    c.execute('UPDATE users SET points=points+? WHERE id=?', (n, uid))

def save_image(data_url):
    m = re.match(r'data:image/(png|jpeg|jpg|webp);base64,(.+)$', data_url or '', re.S)
    if not m: return None
    raw = base64.b64decode(m.group(2))
    if len(raw) > 3_000_000: return None
    name = secrets.token_hex(8) + '.' + ('jpg' if m.group(1) == 'jpeg' else m.group(1))
    open(os.path.join(UPLOADS, name), 'wb').write(raw)
    return '/uploads/' + name

class Err(Exception):
    def __init__(self, msg, code=400): self.msg, self.code = msg, code

# ------------------------------------------------------------------ http
class H(BaseHTTPRequestHandler):
    server_version = 'TraceHub/1.0'
    def log_message(self, *a): pass

    def send_json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)

    def user(self, c):
        t = self.headers.get('Authorization', '').replace('Bearer ', '')
        r = c.execute('SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?', (t,)).fetchone() if t else None
        return r

    def need(self, c):
        u = self.user(c)
        if not u: raise Err('Please log in', 401)
        return u

    def body(self):
        n = int(self.headers.get('Content-Length', 0))
        if n > 6_000_000: raise Err('Too large', 413)
        try: return json.loads(self.rfile.read(n) or b'{}')
        except ValueError: raise Err('Bad JSON')

    def do_GET(self): self.route('GET')
    def do_POST(self): self.route('POST')

    def route(self, method):
        p = urlparse(self.path); path = p.path; q = {k: v[0] for k, v in parse_qs(p.query).items()}
        if method == 'GET' and not path.startswith('/api/'):
            return self.static(path)
        c = db()
        try:
            res = self.api(c, method, path, q)
            c.commit()
            self.send_json(res)
        except Err as e:
            self.send_json({'error': e.msg}, e.code)
        except Exception as e:  # pragma: no cover
            self.send_json({'error': 'Server error: %s' % e}, 500)
        finally:
            c.close()

    def static(self, path):
        if path.startswith('/uploads/'):
            f = os.path.join(UPLOADS, os.path.basename(path))
        else:
            f = os.path.join(STATIC, 'index.html' if path in ('/', '') else os.path.normpath(path).lstrip('/\\'))
            if not os.path.abspath(f).startswith(STATIC): f = os.path.join(STATIC, 'index.html')
        if not os.path.isfile(f):
            f = os.path.join(STATIC, 'index.html')
        ext = os.path.splitext(f)[1]
        ct = {'.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript', '.png': 'image/png', '.jpg': 'image/jpeg', '.webp': 'image/webp', '.svg': 'image/svg+xml'}.get(ext, 'application/octet-stream')
        b = open(f, 'rb').read()
        self.send_response(200); self.send_header('Content-Type', ct); self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)

    # ---------------- API
    def api(self, c, m, path, q):
        if path == '/api/meta' and m == 'GET':
            return {'places': list(PLACES), 'categories': CATEGORIES, 'points': POINTS}
        if path == '/api/register' and m == 'POST':
            b = self.body()
            name, email, pw = (b.get('name') or '').strip(), (b.get('email') or '').strip().lower(), b.get('password') or ''
            if len(name) < 2 or '@' not in email or len(pw) < 6: raise Err('Enter your name, a valid email and a password of 6+ characters')
            if c.execute('SELECT 1 FROM users WHERE email=?', (email,)).fetchone(): raise Err('That email is already registered')
            uid = add_user(c, name, email, pw, (b.get('dept') or '').strip())
            return self.session(c, uid)
        if path == '/api/login' and m == 'POST':
            b = self.body()
            u = c.execute('SELECT * FROM users WHERE email=?', ((b.get('email') or '').lower(),)).fetchone()
            if not u or not hmac.compare_digest(u['pw'], hash_pw(b.get('password') or '', u['salt'])): raise Err('Wrong email or password', 401)
            return self.session(c, u['id'])
        if path == '/api/stats' and m == 'GET':
            g = lambda s: c.execute(s).fetchone()[0]
            return {'lost': g("SELECT COUNT(*) FROM items WHERE kind='lost' AND status='open'"), 'found': g("SELECT COUNT(*) FROM items WHERE kind='found' AND status='open'"),
                    'returned': g("SELECT COUNT(*) FROM items WHERE status='returned'"), 'users': g('SELECT COUNT(*) FROM users')}
        if path == '/api/leaderboard' and m == 'GET':
            return [dict(r) for r in c.execute("SELECT name,dept,points FROM users WHERE role!='admin' ORDER BY points DESC LIMIT 8")]
        if path == '/api/heat' and m == 'GET':
            cnt = Counter(r['location'] for r in c.execute('SELECT location FROM items'))
            return [{'place': k, 'count': cnt.get(k, 0), 'lat': v[0], 'lon': v[1]} for k, v in PLACES.items()]
        if path == '/api/items' and m == 'GET':
            sql, args = 'SELECT * FROM items WHERE 1=1', []
            if q.get('kind') in ('lost', 'found'): sql += ' AND kind=?'; args.append(q['kind'])
            if q.get('status') in ('open', 'returned'): sql += ' AND status=?'; args.append(q['status'])
            else: sql += " AND status='open'"
            if q.get('category'): sql += ' AND category=?'; args.append(q['category'])
            if q.get('mine'):
                u = self.need(c); sql += ' AND user_id=?'; args.append(u['id'])
            rows = c.execute(sql + ' ORDER BY created DESC', args).fetchall()
            qs = set(tokens(q.get('q', '')))
            out = []
            for r in rows:
                if qs and not (qs & set(tokens(r['title'] + ' ' + r['description'] + ' ' + r['location']))): continue
                out.append(item_json(c, r))
            return out
        if path == '/api/items' and m == 'POST':
            u = self.need(c); b = self.body()
            kind = b.get('kind'); title = (b.get('title') or '').strip(); desc = (b.get('description') or '').strip()
            if kind not in ('lost', 'found'): raise Err('Choose lost or found')
            if len(title) < 3 or len(desc) < 10: raise Err('Add a title and a description of at least 10 characters')
            if b.get('category') not in CATEGORIES: raise Err('Pick a category')
            if b.get('location') not in PLACES: raise Err('Pick where it happened')
            try: dt = date.fromisoformat(b.get('date') or '')
            except ValueError: raise Err('Pick a valid date')
            if dt > date.today(): raise Err('The date cannot be in the future')
            img = save_image(b.get('image'))
            cur = c.execute('INSERT INTO items(kind,title,description,category,location,event_date,image,secret,user_id,created) VALUES(?,?,?,?,?,?,?,?,?,?)',
                            (kind, title, desc, b['category'], b['location'], dt.isoformat(), img, (b.get('secret') or '').strip() if kind == 'found' else None, u['id'], time.time()))
            item = c.execute('SELECT * FROM items WHERE id=?', (cur.lastrowid,)).fetchone()
            award(c, u['id'], POINTS[kind])
            ms = matches_for(c, item)
            for mm in ms:
                if mm['score'] >= 0.5:
                    other = c.execute('SELECT * FROM items WHERE id=?', (mm['id'],)).fetchone()
                    note(c, other['user_id'], 'Possible match for your %s report "%s": "%s" (%d%% match)' % (other['kind'], other['title'], item['title'], mm['score'] * 100), item['id'])
                    note(c, u['id'], 'We found a likely match for "%s": "%s" (%d%% match)' % (item['title'], other['title'], mm['score'] * 100), other['id'])
            return {'id': item['id'], 'matches': len(ms), 'points': POINTS[kind]}
        m_ = re.match(r'^/api/items/(\d+)$', path)
        if m_ and m == 'GET':
            r = c.execute('SELECT * FROM items WHERE id=?', (m_.group(1),)).fetchone()
            if not r: raise Err('Not found', 404)
            d = item_json(c, r)
            d['matches'] = [dict(item_json(c, c.execute('SELECT * FROM items WHERE id=?', (x['id'],)).fetchone()), **{'score': x['score'], 'text': x['text'], 'place': x['place'], 'time': x['time']}) for x in matches_for(c, r)] if r['status'] == 'open' else []
            u = self.user(c)
            if u:
                cl = c.execute('SELECT id,status,score FROM claims WHERE item_id=? AND claimant_id=? ORDER BY id DESC', (r['id'], u['id'])).fetchone()
                d['my_claim'] = dict(cl) if cl else None
                if u['id'] == r['user_id'] or u['role'] == 'admin':
                    d['claims'] = [dict(x) for x in c.execute('SELECT c.id,c.status,c.score,u.name FROM claims c JOIN users u ON u.id=c.claimant_id WHERE item_id=?', (r['id'],))]
            return d
        m_ = re.match(r'^/api/items/(\d+)/claim$', path)
        if m_ and m == 'POST':
            u = self.need(c); r = c.execute('SELECT * FROM items WHERE id=?', (m_.group(1),)).fetchone()
            if not r or r['kind'] != 'found' or r['status'] != 'open': raise Err('This item cannot be claimed')
            if r['user_id'] == u['id']: raise Err('You reported this item yourself')
            ans = (self.body().get('answer') or '').strip()
            if len(ans) < 3: raise Err('Describe something only the owner would know')
            truth = set(tokens((r['secret'] or '') + ' ' + r['description'] + ' ' + r['title']))
            given = set(tokens(ans))
            secret = set(tokens(r['secret'] or ''))
            hit = len(given & secret) / len(secret) if secret else 0.0
            general = len(given & truth) / max(1, len(given))
            score = round(0.7 * hit + 0.3 * min(1.0, general * 1.5), 2)
            ok = score >= 0.6
            otp = None; h = salt = None; exp = None
            if ok:
                otp = '%06d' % secrets.randbelow(10 ** 6); salt = secrets.token_hex(6); h = hashlib.sha256((salt + otp).encode()).hexdigest(); exp = time.time() + 900
                note(c, r['user_id'], '%s passed verification for "%s". Ask them for the 6-digit code at handover.' % (u['name'], r['title']), r['id'])
            c.execute('INSERT INTO claims(item_id,claimant_id,answer,score,status,otp_hash,otp_salt,otp_exp,created) VALUES(?,?,?,?,?,?,?,?,?)',
                      (r['id'], u['id'], ans, score, 'approved' if ok else 'rejected', h, salt, exp, time.time()))
            return {'approved': ok, 'score': score, 'otp': otp, 'expires_in': 900 if ok else 0}
        m_ = re.match(r'^/api/items/(\d+)/handover$', path)
        if m_ and m == 'POST':
            u = self.need(c); r = c.execute('SELECT * FROM items WHERE id=?', (m_.group(1),)).fetchone()
            if not r or r['user_id'] != u['id'] or r['kind'] != 'found': raise Err('Only the finder can confirm a handover', 403)
            code = (self.body().get('code') or '').strip()
            for cl in c.execute("SELECT * FROM claims WHERE item_id=? AND status='approved'", (r['id'],)).fetchall():
                if cl['tries'] >= 3 or time.time() > (cl['otp_exp'] or 0): continue
                if hmac.compare_digest(cl['otp_hash'], hashlib.sha256((cl['otp_salt'] + code).encode()).hexdigest()):
                    c.execute("UPDATE claims SET status='done' WHERE id=?", (cl['id'],))
                    c.execute("UPDATE items SET status='returned' WHERE id=?", (r['id'],))
                    award(c, u['id'], POINTS['return'])
                    note(c, cl['claimant_id'], 'Handover confirmed for "%s". Glad you got it back!' % r['title'], r['id'])
                    return {'ok': True, 'points': POINTS['return']}
                c.execute('UPDATE claims SET tries=tries+1 WHERE id=?', (cl['id'],))
            raise Err('That code is wrong or has expired')
        m_ = re.match(r'^/api/items/(\d+)/close$', path)
        if m_ and m == 'POST':
            u = self.need(c); r = c.execute('SELECT * FROM items WHERE id=?', (m_.group(1),)).fetchone()
            if not r or (r['user_id'] != u['id'] and u['role'] != 'admin'): raise Err('Not allowed', 403)
            c.execute("UPDATE items SET status='returned' WHERE id=?", (r['id'],))
            return {'ok': True}
        if path == '/api/me' and m == 'GET':
            u = self.need(c)
            return self.me(c, u)
        if path == '/api/notes' and m == 'GET':
            u = self.need(c)
            rows = [dict(r) for r in c.execute('SELECT * FROM notes WHERE user_id=? ORDER BY created DESC LIMIT 30', (u['id'],))]
            return rows
        if path == '/api/notes/seen' and m == 'POST':
            u = self.need(c); c.execute('UPDATE notes SET seen=1 WHERE user_id=?', (u['id'],)); return {'ok': True}
        raise Err('Not found', 404)

    def me(self, c, u):
        return {'id': u['id'], 'name': u['name'], 'email': u['email'], 'dept': u['dept'], 'role': u['role'], 'points': u['points'],
                'unread': c.execute('SELECT COUNT(*) FROM notes WHERE user_id=? AND seen=0', (u['id'],)).fetchone()[0]}

    def session(self, c, uid):
        t = secrets.token_urlsafe(24)
        c.execute('INSERT INTO sessions VALUES(?,?,?)', (t, uid, time.time()))
        u = c.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
        return {'token': t, 'user': self.me(c, u)}

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', sys.argv[1] if len(sys.argv) > 1 else 8000))
    print('TraceHub running at http://localhost:%d' % port)
    ThreadingHTTPServer(('0.0.0.0', port), H).serve_forever()
