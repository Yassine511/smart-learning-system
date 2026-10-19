"""Live restart and Ollama-outage checks using only synthetic lab resources."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import ssl
import time
from uuid import uuid4

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('prepare', 'rag', 'restarted', 'outage', 'recovered', 'cleanup'), required=True)
    parser.add_argument('--state', type=Path, required=True)
    args = parser.parse_args()
    origin = 'https://smart-learning.test'
    account = json.loads((Path.home() / '.demo-credentials.json').read_text())[0]
    fixture = Path('/opt/smart-learning-local-client/networking_notes.pdf').read_bytes()
    state = json.loads(args.state.read_text()) if args.state.exists() else {'checks': []}
    phase = {'phase': args.phase, 'started_at': datetime.now(timezone.utc).isoformat(), 'passed': False}
    state['checks'].append(phase)
    try:
        with httpx.Client(base_url=origin, verify=ssl.create_default_context(), timeout=240, trust_env=False) as client:
            login = client.post('/api/login', headers={'Origin': origin}, json=account)
            login.raise_for_status()
            headers = {'Origin': origin, 'X-CSRF-Token': login.json()['csrf_token']}

            def require(response, status):
                if response.status_code != status:
                    raise RuntimeError(f'{response.request.method} {response.request.url.path}: expected {status}, got {response.status_code}')
                return response

            def chat(expect_failure=False):
                request_id = str(uuid4())
                started = time.monotonic()
                response = require(client.post('/api/conversations/' + state['conversation_id'] + '/messages', headers=headers,
                                               json={'request_id': request_id, 'content': 'Explain TCP reliability in one short sentence.'}), 200)
                events = []
                for block in response.text.split('\n\n'):
                    name = next((line[7:] for line in block.splitlines() if line.startswith('event: ')), '')
                    data = next((line[6:] for line in block.splitlines() if line.startswith('data: ')), '{}')
                    if name:
                        events.append((name, json.loads(data)))
                failures = [data.get('error_code') for name, data in events if name == 'failed']
                if expect_failure:
                    if failures != ['ollama_unavailable']:
                        raise RuntimeError('Expected a structured model-unavailable failure')
                elif failures or not any(name == 'complete' for name, _ in events):
                    raise RuntimeError('Real chat failed: ' + ','.join(str(code) for code in failures))
                return {'request_id': request_id, 'seconds': round(time.monotonic() - started, 3), 'failure_codes': failures}

            if args.phase == 'prepare':
                if 'conversation_id' in state or 'document_id' in state:
                    raise RuntimeError('Recovery test state already has resources; clean them before preparing again')
                created = require(client.post('/api/conversations', headers=headers, json={'title': 'VM persistence and recovery ' + uuid4().hex[:8]}), 201)
                state['conversation_id'] = created.json()['id']
                phase['chat'] = chat()
                history = require(client.get('/api/conversations/' + state['conversation_id'] + '/messages'), 200).json()
                state['message_ids'] = [message['id'] for message in history]
                uploaded = require(client.post('/api/documents', headers=headers,
                                               files={'file': ('networking_notes.pdf', fixture, 'application/pdf')}), 202)
                state['document_id'] = uploaded.json()['id']
                for _ in range(150):
                    document = require(client.get('/api/documents/' + state['document_id']), 200).json()
                    if document['status'] == 'indexed':
                        break
                    if document['status'] == 'failed':
                        raise RuntimeError('PDF indexing failed: ' + str(document.get('error_code')))
                    time.sleep(2)
                else:
                    raise RuntimeError('PDF indexing timed out')
                state['pdf_sha256'] = hashlib.sha256(fixture).hexdigest()
            elif args.phase in ('restarted', 'recovered'):
                require(client.get('/api/ready'), 200)
                history = require(client.get('/api/conversations/' + state['conversation_id'] + '/messages'), 200).json()
                if not set(state['message_ids']).issubset({message['id'] for message in history}):
                    raise RuntimeError('Saved messages missing after restart')
                document = require(client.get('/api/documents/' + state['document_id']), 200).json()
                if document['status'] != 'indexed':
                    raise RuntimeError('Indexed PDF state was not preserved')
                downloaded = require(client.get('/api/documents/' + state['document_id'] + '/file'), 200)
                if hashlib.sha256(downloaded.content).hexdigest() != state['pdf_sha256']:
                    raise RuntimeError('PDF content changed after restart')
                phase['messages_preserved'] = True
                phase['indexed_pdf_preserved'] = True
                if args.phase == 'recovered':
                    phase['chat'] = chat()
            elif args.phase == 'outage':
                response = require(client.get('/api/ready'), 503)
                phase['readiness_http'] = response.status_code
                phase['chat'] = chat(expect_failure=True)
            elif args.phase == 'rag':
                require(client.put('/api/conversations/' + state['conversation_id'] + '/documents/' + state['document_id'], headers=headers), 204)
                try:
                    question = 'How does TCP deliver data reliably?'
                    response = require(client.post('/api/conversations/' + state['conversation_id'] + '/messages', headers=headers,
                                                   json={'request_id': str(uuid4()), 'content': question}), 200)
                    history = require(client.get('/api/conversations/' + state['conversation_id'] + '/messages'), 200).json()
                    answer = next(message for message in reversed(history) if message['role'] == 'assistant')
                    phase['answer_status'] = answer['status']
                    phase['error_code'] = answer['error_code']
                    phase['no_evidence'] = answer['content'] == "I couldn't find relevant evidence in the attached documents."
                    phase['answer_characters'] = len(answer['content'])
                    phase['source_count'] = len(answer['sources'])
                    if answer['status'] != 'completed' or not answer['sources']:
                        raise RuntimeError('PDF answer has no verified citation; see no_evidence and source_count')
                finally:
                    require(client.delete('/api/conversations/' + state['conversation_id'] + '/documents/' + state['document_id'], headers=headers), 204)
            elif args.phase == 'cleanup':
                if state.get('document_id'):
                    require(client.delete('/api/documents/' + state['document_id'], headers=headers), 204)
                if state.get('conversation_id'):
                    require(client.delete('/api/conversations/' + state['conversation_id'], headers=headers), 204)
                phase['resources_deleted'] = True
            require(client.post('/api/logout', headers=headers), 204)
            phase['passed'] = True
    except Exception as exc:
        phase['error'] = type(exc).__name__ + ': ' + str(exc)
    finally:
        phase['finished_at'] = datetime.now(timezone.utc).isoformat()
        args.state.parent.mkdir(parents=True, exist_ok=True)
        args.state.write_text(json.dumps(state, indent=2) + '\n')
    print(json.dumps(phase))
    return 0 if phase['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
