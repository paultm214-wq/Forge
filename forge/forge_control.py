"""Crash-aware FIFO GPU claims and cooperative operator checkpoints (Linux)."""
import contextlib
import fcntl
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    try:
        temporary.write_text(json.dumps(data, indent=2))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def identity(pid=None):
    try:
        pid = os.getpid() if pid is None else int(pid)
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return Path('/proc/sys/kernel/random/boot_id').read_text().strip() + ':' + fields[19]
    except (OSError, ValueError, IndexError):
        return None


def owner(what=''):
    return dict(pid=os.getpid(), identity=identity(), thread=threading.get_native_id(),
                what=what, since=time.time())


def alive(entry):
    try:
        os.kill(int(entry['pid']), 0)
    except PermissionError:
        return True  # cannot prove a different user's owner is dead
    except (OSError, KeyError, ValueError, TypeError):
        return False
    return (identity(entry['pid']) is not None and identity(entry['pid']) == entry.get('identity')
            and (not entry.get('thread') or Path(f"/proc/{entry['pid']}/task/{entry['thread']}").exists()))


@contextlib.contextmanager
def entries(root, name):
    if not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}', name) or '..' in name:
        raise ValueError('unsafe GPU resource name')
    root.mkdir(parents=True, exist_ok=True)
    with (root / ('.' + name + '.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = root / (name + '.json')
        try:
            data = json.loads(path.read_text())
        except FileNotFoundError:
            data = []
        if not isinstance(data, list):
            raise ValueError('invalid GPU queue: ' + name)
        data = [entry for entry in data if isinstance(entry, dict) and alive(entry)]
        yield data
        atomic(path, data)


class GpuLock:
    def __init__(self, root, name, waiting=None):
        self.root, self.name, self.waiting = root, name, waiting
        self.local = threading.Lock()
        self.token = self.thread = None

    def acquire(self, blocking=True, timeout=-1):
        started, token, success = time.monotonic(), uuid.uuid4().hex, False
        try:
            with entries(self.root, self.name) as queue:
                queue.append({**owner(), 'token': token})
            while True:
                with entries(self.root, self.name) as queue:
                    index = next(i for i, row in enumerate(queue) if row['token'] == token)
                    ahead = [row.get('what') or str(row['pid']) for row in queue[:index]]
                if index == 0 and self.local.acquire(False):
                    self.token, self.thread, success = token, threading.get_ident(), True
                    if self.waiting:
                        self.waiting(self.name, [])
                    return True
                if self.waiting:
                    self.waiting(self.name, ahead)
                if not blocking or timeout >= 0 and time.monotonic() - started >= timeout:
                    return False
                time.sleep(.05)
        finally:
            if not success:
                with entries(self.root, self.name) as queue:
                    queue[:] = [row for row in queue if row.get('token') != token]

    def release(self):
        with entries(self.root, self.name) as queue:
            queue[:] = [row for row in queue if row.get('token') != self.token]
        self.token = self.thread = None
        self.local.release()

    def locked(self):
        return self.local.locked()

    def owned(self):
        return self.thread == threading.get_ident()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()


def request(root, action):
    if action not in ('pause', 'cancel', 'resume'):
        raise ValueError('action must be pause, cancel or resume')
    if not (root / 'state.json').is_file():
        raise ValueError('not a Forge run')
    record = dict(action=action, requested_at=time.time())
    atomic(root / 'control.json', record)
    return record


def requested(root):
    try:
        return json.loads((root / 'control.json').read_text()).get('action')
    except FileNotFoundError:
        return None


def reconcile(root):
    result = {}
    for path in sorted(root.glob('*.json')):
        with entries(root, path.stem) as queue:
            result[path.stem] = list(queue)
    return result
