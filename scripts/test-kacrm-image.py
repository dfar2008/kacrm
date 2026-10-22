#!/usr/bin/env python3
"""Exercise a disposable single-container installation before publishing an image."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import time
import urllib.request
import urllib.error
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    name = 'kacrm-release-check-' + uuid.uuid4().hex[:10]
    volume = name + '-data'
    def docker(*cmd):
        return subprocess.check_output(['docker', *cmd], stderr=subprocess.PIPE).decode()
    def start():
        docker('run', '-d', '--name', name, '--stop-timeout', '120',
               '-p', '127.0.0.1::8765', '-v', volume + ':/var/lib/kacrm', args.image)
    def ready():
        for _ in range(300):
            state = json.loads(docker('inspect', name))[0]
            if not state['State']['Running']:
                raise RuntimeError('Container exited during initialization')
            if state['State'].get('Health', {}).get('Status') == 'healthy':
                port = state['NetworkSettings']['Ports']['8765/tcp'][0]['HostPort']
                return 'http://127.0.0.1:' + port
            time.sleep(1)
        raise RuntimeError('Health check timed out')
    def settings():
        return json.loads(docker('exec', name, 'cat', '/var/lib/kacrm/kacrm-installation.json'))
    def request(path, data=None, token=None):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        body = None if data is None else json.dumps(data).encode()
        req = urllib.request.Request(base + path, data=body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            raise RuntimeError(f'HTTP {error.code} on {path}') from None
    def login(s):
        result = json.loads(request('/auth/login', {'email': s['admin_email'], 'password': s['admin_password']}))
        assert result['is_admin']
        return result['api_key']
    try:
        start()
        base = ready()
        s = settings()
        token = login(s)
        assert b'<html' in request('/app/').lower()
        created = json.loads(request('/crm/records/accounts', {'accounts_name': 'KaCRM release verification'}, token))
        assert created['success']
        rid = created['id']
        assert b'KaCRM release verification' in request(f'/crm/records/accounts/{rid}', token=token)
        # Use exactly the form's content/progress fields, not a different API-only shape.
        note = 'KaCRM release follow-up content'
        request('/api/touchpoints/note', {'module': 'accounts', 'record_id': rid,
                                        'content': note, 'progress': 'Materials prepared'}, token)
        interactions = json.loads(request(f'/api/interactions?module=accounts&record_id={rid}', token=token))
        assert any(i['content'].get('text') == note and i['content'].get('progress') == 'Materials prepared'
                   for i in interactions['data'])
        # Internal credentials never go to logs or report files.
        log = subprocess.check_output(['docker', 'logs', name], stderr=subprocess.STDOUT).decode()
        assert not any(v in log for k, v in s.items() if k != 'admin_email')
        docker('stop', '--timeout', '120', name)
        docker('rm', name)
        start()
        base = ready()
        assert settings() == s
        token = login(s)
        assert b'KaCRM release verification' in request(f'/crm/records/accounts/{rid}', token=token)
        data = json.loads(request(f'/api/interactions?module=accounts&record_id={rid}', token=token))
        assert any(i['content'].get('text') == note for i in data['data'])
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({'image': args.image, 'image_id': json.loads(docker('image', 'inspect', args.image))[0]['Id'],
            'fresh_install': True, 'admin_login': True, 'crm_write_read': True,
            'note_form_content_progress_persist': True, 'container_recreation_persistence': True,
            'no_generated_passwords_in_logs': True, 'model_key_supplied': False,
            'live_model_and_feishu_calls': 'not tested'}, indent=2) + '\n')
        print('PASS: fresh install, login, CRM, form follow-up content/progress, recreation persistence')
    finally:
        subprocess.run(['docker', 'stop', '--timeout', '120', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(['docker', 'volume', 'rm', volume], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == '__main__':
    main()
