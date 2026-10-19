"""Run one coordinated real chat from each client VM; retain no answer text."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import ssl
import subprocess
import time
from uuid import uuid4

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--role', choices=('client1', 'client2'), required=True)
    parser.add_argument('--start-at', type=float, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    index = 0 if args.role == 'client1' else 1
    expected_ip = '192.168.50.' + ('11' if index == 0 else '12')
    adapters = json.loads(subprocess.check_output(['ip', '-j', '-4', 'addr'], text=True))
    assert any(a['local'] == expected_ip for d in adapters for a in d.get('addr_info', [])), 'Wrong client VM'
    account = json.loads((Path.home() / '.demo-credentials.json').read_text())[index]
    origin = 'https://smart-learning.test'
    report = {'role': args.role, 'hostname': socket.gethostname(), 'ip': expected_ip, 'passed': False}
    conversation_id = None
    headers = None
    with httpx.Client(base_url=origin, verify=ssl.create_default_context(), timeout=240, trust_env=False) as client:
        try:
            login = client.post('/api/login', headers={'Origin': origin}, json=account)
            login.raise_for_status()
            headers = {'Origin': origin, 'X-CSRF-Token': login.json()['csrf_token']}
            created = client.post('/api/conversations', headers=headers, json={'title': 'Two-VM concurrency ' + uuid4().hex[:8]})
            created.raise_for_status()
            conversation_id = created.json()['id']
            if args.start_at - time.time() > 60:
                raise RuntimeError('Start time is too far in the future')
            time.sleep(max(0, args.start_at - time.time()))
            report['started_at'] = datetime.now(timezone.utc).isoformat()
            request_id = str(uuid4())
            report['request_id'] = request_id
            started = time.monotonic()
            reply = client.post('/api/conversations/' + conversation_id + '/messages', headers=headers,
                                json={'request_id': request_id, 'content': 'Explain ' + ('TCP' if index == 0 else 'UDP') + ' in one short sentence.'})
            reply.raise_for_status()
            report['finished_at'] = datetime.now(timezone.utc).isoformat()
            report['seconds'] = round(time.monotonic() - started, 3)
            history = client.get('/api/conversations/' + conversation_id + '/messages')
            history.raise_for_status()
            matches = [m for m in history.json() if m['request_id'] == request_id and m['role'] == 'assistant']
            if len(matches) != 1 or matches[0]['status'] != 'completed' or not matches[0]['content'].strip():
                raise RuntimeError('The client did not receive its own completed saved reply')
            report['http_status'] = reply.status_code
            report['reply_routed_and_saved'] = True
            report['passed'] = True
        except Exception as exc:
            report['error'] = type(exc).__name__ + ': ' + str(exc)
        finally:
            if headers:
                if conversation_id:
                    client.delete('/api/conversations/' + conversation_id, headers=headers)
                client.post('/api/logout', headers=headers)
            args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
