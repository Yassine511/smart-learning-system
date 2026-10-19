"""Verify the installed guest Firefox login with its real trusted CA profile."""
import argparse
import base64
import json
from pathlib import Path
import subprocess
import time

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--student', type=int, choices=(1, 2), required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--expect-unavailable', action='store_true')
    args = parser.parse_args()
    profiles = list((Path.home() / 'snap/firefox/common/.mozilla/firefox').glob('*/cert9.db'))
    if not profiles:
        raise SystemExit('Initialize Firefox and install the public lab CA first')
    profile = next((p.parent for p in profiles if 'default' in p.parent.name), profiles[0].parent)
    account = json.loads((Path.home() / '.demo-credentials.json').read_text())[args.student - 1]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {'browser': 'Firefox installed in client VM', 'student': args.student,
              'accept_insecure_certs': False, 'passed': False}
    driver = None
    session = None
    with args.output.with_suffix('.driver.log').open('w') as log:
        try:
            driver = subprocess.Popen(['/snap/bin/firefox.geckodriver', '--host', '127.0.0.1', '--port', '4444'], stdout=log, stderr=log)
            with httpx.Client(base_url='http://127.0.0.1:4444', timeout=90, trust_env=False) as client:
                def call(method, path, data=None):
                    response = client.request(method, path, json=data)
                    response.raise_for_status()
                    value = response.json()['value']
                    if isinstance(value, dict) and value.get('error'):
                        raise RuntimeError('WebDriver: ' + value['error'])
                    return value

                for _ in range(60):
                    try:
                        if call('GET', '/status')['ready']:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(1)
                else:
                    raise RuntimeError('Guest geckodriver did not start')
                created = call('POST', '/session', {'capabilities': {'alwaysMatch': {
                    'browserName': 'firefox', 'acceptInsecureCerts': False,
                    'moz:firefoxOptions': {'args': ['-headless', '-profile', str(profile)]}}}})
                session = created['sessionId']
                prefix = '/session/' + session
                call('POST', prefix + '/window/rect', {'width': 1280, 'height': 1600})
                call('POST', prefix + '/url', {'url': 'https://smart-learning.test'})
                # Clear only this lab origin's browser cookies so repeat runs
                # verify login rather than reusing the last test session.
                call('DELETE', prefix + '/cookie')
                call('POST', prefix + '/url', {'url': 'https://smart-learning.test'})

                def find(selector):
                    elements = call('POST', prefix + '/elements', {'using': 'css selector', 'value': selector})
                    return elements[0]['element-6066-11e4-a52e-4f735466cecf'] if elements else None

                for _ in range(60):
                    if find('input[name="email"]'):
                        break
                    time.sleep(1)
                else:
                    raise RuntimeError('Login form did not appear')
                for name in ('email', 'password'):
                    element = find(f'input[name="{name}"]')
                    call('POST', prefix + '/element/' + element + '/value', {'text': account[name]})
                button = find('form button[type="submit"]')
                if not button:
                    raise RuntimeError('Login submit button absent')
                call('POST', prefix + '/element/' + button + '/click', {})
                for _ in range(60):
                    if find('input[name="title"]'):
                        break
                    time.sleep(1)
                else:
                    raise RuntimeError('Authenticated workspace did not appear')
                if args.expect_unavailable:
                    for _ in range(60):
                        text = call('POST', prefix + '/execute/sync', {'script': 'return document.body.innerText', 'args': []})
                        if '503' in text and 'unavailable' in text.lower():
                            report['outage_ui_503'] = True
                            break
                        time.sleep(1)
                    else:
                        raise RuntimeError('Expected a visible 503 unavailable notice')
                    call('POST', prefix + '/execute/sync', {'script': "document.querySelector('.status-panel').scrollIntoView({block:'center'}); return true", 'args': []})
                else:
                    for _ in range(60):
                        text = call('POST', prefix + '/execute/sync', {'script': 'return document.body.innerText', 'args': []})
                        if 'All systems ready' in text:
                            report['ready_ui'] = True
                            break
                        time.sleep(1)
                    else:
                        raise RuntimeError('Ready status did not appear in the workspace')
                args.output.with_suffix('.png').write_bytes(base64.b64decode(call('GET', prefix + '/screenshot')))
                report['passed'] = True
                call('DELETE', prefix)
                session = None
        except Exception as exc:
            report['error'] = type(exc).__name__ + ': ' + str(exc)
        finally:
            if driver:
                driver.terminate()
                try:
                    driver.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    driver.kill()
            args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
