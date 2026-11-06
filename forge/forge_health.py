"""Host and image scoped capability observations; installation is not validation."""
import datetime as dt
import json
import platform
import socket
import subprocess


def host_id():
    return dict(hostname=socket.gethostname(), architecture=platform.machine())


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def age(timestamp):
    try:
        return max(0, (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(timestamp)).total_seconds())
    except (ValueError, TypeError):
        return None


def cached(path, max_age=86400):
    try:
        data = json.loads(path.read_text())
        elapsed = age(data.get('observed_at'))
        data.update(age_seconds=elapsed, stale=elapsed is None or elapsed > max_age or data.get('host') != host_id())
        for row in data.get('toolchains', []):
            smoke = row.setdefault('smoke', {})
            elapsed = age(smoke.get('validated_at'))
            smoke.update(age_seconds=elapsed, stale=elapsed is None or elapsed > max_age)
        return data
    except (OSError, ValueError, TypeError, AttributeError):
        return dict(host=host_id(), stale=True, toolchains=[], age_seconds=None)


def command(args, timeout=30):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout, result.stderr
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, '', str(error)


def inspect(name, profile, run=command):
    image = profile['image']
    row = dict(name=name, image=image, installed=None, buildable=True, versions={}, image_id=None,
               dependency_cache={'state': 'unknown', 'completeness': 'unknown'},
               smoke=dict(status='unrun', validated_at=None))
    code, text, error = run(['docker', 'image', 'inspect', image])
    if code is None or code and 'no such' not in (text + error).lower():
        return {**row, 'status': 'unavailable'}
    if code:
        return {**row, 'status': 'not_installed', 'installed': False}
    try:
        row.update(image_id=json.loads(text)[0]['Id'], installed=True, status='installed')
    except (ValueError, KeyError, IndexError, TypeError):
        return {**row, 'status': 'unavailable'}
    code, text, _ = run(['docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges', '--user', '1000:1000', '--pids-limit', '64', '--memory', '256m',
        row['image_id'], 'python3', '-c', "from pathlib import Path;print(Path('/opt/forge-versions.json').read_text())"])
    if code == 0:
        try:
            row['versions'] = json.loads(text)
        except ValueError:
            pass
    return row


def report(path, profiles, smoke=None, run=command, caches=None):
    old = cached(path)
    previous = {row['name']: row for row in old['toolchains']} if old.get('host') == host_id() else {}
    rows = []
    for name, profile in sorted(profiles.items()):
        row = inspect(name, profile, run)
        prior = previous.get(name, {})
        if row['installed'] and row['image_id'] == prior.get('image_id'):
            row['smoke'] = prior['smoke']
        row['dependency_cache'] = [{'name': p.name, 'state': 'present' if p.is_dir() and any(p.iterdir()) else 'empty',
                                    'completeness': 'unknown'} for p in (caches or {}).get(name, [])]
        if smoke and row['installed']:
            status, diagnostic = smoke(name)
            row['smoke'] = dict(status=status, diagnostic=diagnostic[-2000:], validated_at=now(), stale=False)
        rows.append(row)
    return dict(schema_version=1, host=host_id(), observed_at=now(), stale=False, age_seconds=0, toolchains=rows)


def record_smoke(path, name, profile, status, diagnostic):
    data = cached(path)
    rows = {row['name']: row for row in data['toolchains']} if data.get('host') == host_id() else {}
    row = inspect(name, profile)
    row['smoke'] = dict(status=status, diagnostic=diagnostic[-2000:], validated_at=now(), stale=False)
    rows[name] = row
    return dict(schema_version=1, host=host_id(), observed_at=now(), stale=False, toolchains=list(rows.values()))
