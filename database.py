import os, sqlite3, asyncio
DB_PATH=os.getenv("DB_PATH","stm_pro_bot.sqlite3")
def _conn():
    c=sqlite3.connect(DB_PATH)
    c.row_factory=sqlite3.Row
    return c
async def init_db():
    def work():
        with _conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
            c.execute("""CREATE TABLE IF NOT EXISTS signals (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, asset TEXT, direction TEXT, strategy TEXT, confidence INTEGER, reason TEXT, outcome TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    await asyncio.to_thread(work)
async def add_user(uid, username, first_name):
    def work():
        with _conn() as c:
            c.execute("INSERT INTO users(user_id,username,first_name) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,first_name=excluded.first_name",(uid,username,first_name))
    await asyncio.to_thread(work)
async def save_signal(uid, asset, direction, strategy, confidence, reason):
    def work():
        with _conn() as c:
            cur=c.execute("INSERT INTO signals(user_id,asset,direction,strategy,confidence,reason) VALUES(?,?,?,?,?,?)",(uid,asset,direction,strategy,confidence,reason))
            return cur.lastrowid
    return await asyncio.to_thread(work)
async def recent_signals(uid, limit=10):
    def work():
        with _conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM signals WHERE user_id=? ORDER BY id DESC LIMIT ?",(uid,limit)).fetchall()]
    return await asyncio.to_thread(work)
async def record_result(signal_id, outcome):
    if outcome not in ("WIN","LOSS","DRAW"): raise ValueError("invalid outcome")
    def work():
        with _conn() as c:
            c.execute("UPDATE signals SET outcome=? WHERE id=?",(outcome,signal_id))
    await asyncio.to_thread(work)
async def stats(uid):
    def work():
        with _conn() as c:
            r=c.execute("SELECT COUNT(*) total, SUM(outcome='WIN') wins, SUM(outcome='LOSS') losses, SUM(outcome IS NULL) pending FROM signals WHERE user_id=?",(uid,)).fetchone()
            total=int(r["total"] or 0); wins=int(r["wins"] or 0); losses=int(r["losses"] or 0); pending=int(r["pending"] or 0)
            settled=wins+losses
            return {"total":total,"wins":wins,"losses":losses,"pending":pending,"rate":(wins/settled*100 if settled else 0)}
    return await asyncio.to_thread(work)
