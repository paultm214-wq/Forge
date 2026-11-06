"""Restore only services proven active before a configured GPU handoff."""
import json
import fcntl
import re
import shlex
import subprocess
import urllib.request

import forge_control as control


def units(command, verb):
    try:
        words = shlex.split(command or '')
    except ValueError:
        return []
    if words[:3] != ['systemctl', '--user', verb] or not words[3:]:
        return []
    return words[3:] if all(re.fullmatch(r'[A-Za-z0-9_@.-]+', unit) and not unit.startswith('-') for unit in words[3:]) else []


def install(g):
    original_free, original_release = g['shape_free'], g['release_shape_server']
    def journal(change=None):
        path = g['FORGE_HOME'] / 'service-handoffs.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try: rows = json.loads(path.read_text())
            except FileNotFoundError: rows = []
            if change is not None:
                rows = change(rows)
                control.atomic(path, rows)
            return rows
    def key(srv):
        return srv['name'], srv.get('ssh', srv.get('machine', srv['name']))
    def configured(cfg):
        return cfg.get('shapes', {}).get('servers', [])
    def release_gpu(srv):
        request = urllib.request.Request(srv['url'].rstrip('/')+'/v1/release', data=b'{}', headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request, timeout=120) as response:
            if json.load(response).get('released') is not True:
                raise g['ForgeError']('shape server did not confirm GPU release')
    def restore(srv, row):
        with g['gpu_lock'](srv.get('machine', srv['name'])):
            release_gpu(srv)
            command = 'systemctl --user start ' + ' '.join(shlex.quote(unit) for unit in row['units'])
            if not g['_shape_ssh'](srv, command, 'restoring recorded services'):
                raise g['ForgeError']('service restoration failed; handoff journal retained')
            journal(lambda rows: [item for item in rows if item != row])
    def free(srv):
        stopped = units(srv.get('free_cmd'), 'stop')
        if not stopped or set(stopped) != set(units(srv.get('restore_cmd'), 'start')):
            return original_free(srv)
        if srv['name'] in g['_SHAPE_FREED']:
            return
        host, active = key(srv)[1], []
        for unit in stopped:
            result = subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8',host,
                'systemctl --user is-active --quiet '+shlex.quote(unit)], capture_output=True, text=True, timeout=20)
            if result.returncode == 0:
                active.append(unit)
            elif result.returncode != 3:
                raise g['ForgeError']('cannot establish service state before GPU handoff')
        if active:
            owner = control.owner('shape handoff'); owner.pop('thread', None)
            row = dict(name=srv['name'], host=host, units=active, owner=owner)
            journal(lambda rows: [*rows, row])  # record before stopping services
            if not g['_shape_ssh'](srv, 'systemctl --user stop '+' '.join(map(shlex.quote,active)), 'freeing GPU'):
                raise g['ForgeError']('service stop failed; handoff journal retained')
        g['_SHAPE_FREED'].add(srv['name'])
    def release(cfg, srv, wait=True):
        matches = [r for r in journal() if (r['name'],r['host']) == key(srv)]
        if not matches:
            return original_release(cfg, srv, wait)
        for row in matches:
            restore(srv, row)
        g['_SHAPE_FREED'].discard(srv['name'])
    def recover(cfg, apply=False):
        results = []
        for row in journal():
            srv = next((s for s in configured(cfg) if key(s) == (row['name'],row['host']) and
                        set(row['units']) <= set(units(s.get('restore_cmd'),'start')) and
                        set(row['units']) <= set(units(s.get('free_cmd'),'stop'))), None)
            reason = 'owner is alive' if control.alive(row['owner']) else 'configuration mismatch' if not srv else 'recoverable'
            result = dict(name=row['name'], units=row['units'], state=reason)
            if apply and reason == 'recoverable':
                try:
                    restore(srv, row)
                    result['state'] = 'restored'
                except (g['ForgeError'], OSError, ValueError) as error:
                    result.update(state='failed', error=str(error))
            results.append(result)
        return results
    g.update(shape_free=free, release_shape_server=release, recover_services=recover)
