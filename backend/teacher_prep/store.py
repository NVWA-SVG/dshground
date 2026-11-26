from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


def now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(dump(value).encode("utf-8")).hexdigest()


def identifier(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def assert_process_exists(pid):
    if os.name != "nt":
        os.kill(pid, 0)
        return
    # On Windows os.kill(pid, 0) can terminate a process. Query, never signal it.
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == 5:
            raise PermissionError("进程查询权限不足")
        raise ProcessLookupError(pid)
    try:
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise PermissionError("无法读取进程状态")
        if code.value != 259:
            raise ProcessLookupError(pid)
    finally:
        kernel.CloseHandle(handle)


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.data = self.root / "data"
        self.data.mkdir(parents=True, exist_ok=True)
        self.path = self.data / "teacher-prep.sqlite3"
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS classes(id TEXT PRIMARY KEY, payload TEXT NOT NULL, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS lessons(id TEXT PRIMARY KEY, payload TEXT NOT NULL, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS versions(id TEXT PRIMARY KEY, lesson_id TEXT NOT NULL REFERENCES lessons(id),
                  number INTEGER NOT NULL, parent_id TEXT REFERENCES versions(id), decision_id TEXT,
                  design TEXT NOT NULL, changes TEXT NOT NULL, summary TEXT NOT NULL,
                  status TEXT NOT NULL, manifest TEXT, error TEXT, created TEXT NOT NULL, UNIQUE(lesson_id,number));
                CREATE TABLE IF NOT EXISTS feedback(id TEXT PRIMARY KEY, lesson_id TEXT NOT NULL REFERENCES lessons(id),
                  version_id TEXT NOT NULL REFERENCES versions(id), payload TEXT NOT NULL, analysis TEXT NOT NULL, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS decisions(id TEXT PRIMARY KEY, feedback_id TEXT NOT NULL REFERENCES feedback(id),
                  payload TEXT NOT NULL, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS ledger(operation TEXT NOT NULL, key TEXT NOT NULL, payload_hash TEXT NOT NULL,
                  result TEXT NOT NULL, PRIMARY KEY(operation,key));
                CREATE TABLE IF NOT EXISTS export_jobs(id TEXT PRIMARY KEY, version_id TEXT NOT NULL REFERENCES versions(id),
                  status TEXT NOT NULL, error TEXT, created TEXT NOT NULL, finished TEXT);
                CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, name TEXT NOT NULL, sha256 TEXT NOT NULL,
                  text_path TEXT NOT NULL, original_path TEXT NOT NULL, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS knowledge(id TEXT PRIMARY KEY, payload TEXT NOT NULL, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS asset_descriptions(
                  owner_id TEXT NOT NULL REFERENCES knowledge(id) ON DELETE CASCADE,
                  sha256 TEXT NOT NULL, description TEXT NOT NULL, model TEXT NOT NULL,
                  cache_key TEXT NOT NULL, updated TEXT NOT NULL, PRIMARY KEY(owner_id,sha256));
            """)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def read(self, table, entity_id):
        if table not in {"classes", "lessons", "versions", "feedback", "decisions", "sources", "knowledge"}:
            raise ValueError("未知数据对象")
        with self.connect() as db:
            row = db.execute(f"SELECT * FROM {table} WHERE id=?", (entity_id,)).fetchone()
        if row is None:
            raise ValueError(f"对象不存在：{entity_id}")
        return dict(row)

    def idempotent(self, operation, key, payload, action):
        if not key or len(key) > 150:
            raise ValueError("需要不超过150字符的幂等键；同一次操作重试时复用")
        fingerprint = digest(payload)
        with self.transaction() as db:
            old = db.execute("SELECT * FROM ledger WHERE operation=? AND key=?", (operation, key)).fetchone()
            if old:
                if old["payload_hash"] != fingerprint:
                    raise ValueError("同一幂等键不能用于不同输入")
                return json.loads(old["result"])
            result = action(db)
            db.execute("INSERT INTO ledger VALUES(?,?,?,?)", (operation, key, fingerprint, dump(result)))
            return result

    @contextmanager
    def export_lock(self, version_id):
        directory = self.data / "locks"
        directory.mkdir(exist_ok=True)
        path = directory / f"{version_id}.lock"
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                owner = json.loads(path.read_text(encoding="utf-8"))
                assert_process_exists(int(owner["pid"]))
            except ProcessLookupError:
                path.unlink()
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except (ValueError, KeyError, json.JSONDecodeError):
                raise ValueError("导出锁记录异常，请先核对 data/locks 中的对应文件") from None
            except PermissionError:
                raise ValueError("导出进程仍存在或无权限检查，请稍后查询状态") from None
            else:
                raise ValueError("该版本正在导出，请等待完成，避免重复写入")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"pid": os.getpid(), "created": now()}, handle)
            yield
        finally:
            path.unlink(missing_ok=True)
