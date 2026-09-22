import sqlite3
from datetime import datetime,timezone
from .config import DB_PATH
def now(): return datetime.now(timezone.utc).isoformat()
def connect():
 c=sqlite3.connect(DB_PATH,check_same_thread=False); c.row_factory=sqlite3.Row; return c
def init_db():
 c=connect(); c.executescript('''
 CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY AUTOINCREMENT,source TEXT,title TEXT,status TEXT DEFAULT 'queued',progress INTEGER DEFAULT 0,message TEXT,created_at TEXT,updated_at TEXT);
 CREATE TABLE IF NOT EXISTS clips(id INTEGER PRIMARY KEY AUTOINCREMENT,job_id INTEGER,path TEXT,title TEXT,duration REAL,transcript TEXT,status TEXT DEFAULT 'ready',created_at TEXT);
 CREATE TABLE IF NOT EXISTS schedules(id INTEGER PRIMARY KEY AUTOINCREMENT,clip_id INTEGER,run_at TEXT,title TEXT,description TEXT,hashtags TEXT,platforms TEXT,status TEXT DEFAULT 'scheduled',attempts INTEGER DEFAULT 0,last_error TEXT,created_at TEXT,updated_at TEXT);
 CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
 CREATE TABLE IF NOT EXISTS accounts(id INTEGER PRIMARY KEY AUTOINCREMENT,platform TEXT UNIQUE,status TEXT,account_name TEXT,details TEXT,updated_at TEXT);
 '''); c.commit()
 defaults={'automation_mode':'review','daily_slots':'["10:00","14:00","19:30"]','timezone':'Asia/Kolkata','auto_schedule_enabled':'0'}
 for k,v in defaults.items(): c.execute('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)',(k,v))
 c.commit(); c.close()
def execute(sql,args=(),fetch=False):
 c=connect(); cur=c.execute(sql,args); rows=cur.fetchall() if fetch else None; c.commit(); c.close(); return rows
def one(sql,args=()):
 r=execute(sql,args,True); return dict(r[0]) if r else None
