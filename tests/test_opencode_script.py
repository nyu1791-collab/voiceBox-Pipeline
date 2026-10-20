"""Free-only selection, native session boundaries and fail-closed generation."""
from pathlib import Path
import json
import os
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import opencode_script
from opencode_script import FREE_MODEL, _consume, generate_opencode_script
from research import generate_script
from tools.check_opencode import check


def event(kind, **properties):
    return {'type': kind, 'properties': properties}


def response_events(text='{"ok":true}', finish='stop'):
    sid = 'session-test'
    return [
        # User input can also look like JSON: it must never enter the response.
        event('message.part.updated', part={'id': 'user-text', 'messageID': 'user',
              'sessionID': sid, 'type': 'text', 'text': 'not assistant JSON'}),
        event('message.updated', info={'id': 'assistant', 'sessionID': sid,
              'role': 'assistant', 'providerID': 'opencode', 'modelID': FREE_MODEL}),
        event('message.part.updated', part={'id': 'start', 'messageID': 'assistant',
              'sessionID': sid, 'type': 'step-start'}),
        event('message.part.updated', part={'id': 'answer', 'messageID': 'assistant',
              'sessionID': sid, 'type': 'text', 'text': text}),
        event('message.part.updated', part={'id': 'finish', 'messageID': 'assistant',
              'sessionID': sid, 'type': 'step-finish', 'reason': finish, 'cost': 0,
              'tokens': {'input': 20, 'output': 4}}),
        event('session.status', sessionID=sid, status={'type': 'idle'})]


def stream(events):
    return iter([b'data: ' + json.dumps(e).encode() + b'\n' for e in events])


def test_response_requires_one_complete_assistant_json_object():
    script, receipt = _consume(stream(response_events()), 'session-test')
    assert script == {'ok': True}
    assert receipt['request_count'] == 1 and receipt['catalog_cost'] == 0
    assert receipt['model'] == FREE_MODEL and receipt['finish_reason'] == 'stop'


@pytest.mark.parametrize('failure', ['error', 'retry', 'tool', 'permission', 'model',
                                    'second_step', 'cost', 'truncated', 'invalid', 'array'])
def test_rejected_outputs_never_become_success(failure):
    events = response_events()
    if failure == 'error':
        events.insert(3, event('session.error', sessionID='session-test',
                              error={'message': 'Authorization: secret-marker'}))
    elif failure == 'retry':
        events.insert(3, event('session.status', sessionID='session-test', status={'type': 'retry'}))
    elif failure == 'tool':
        events.insert(3, event('message.part.updated', part={'type': 'tool', 'sessionID': 'session-test',
                              'messageID': 'assistant'}))
    elif failure == 'permission':
        events.insert(3, event('permission.asked', sessionID='session-test'))
    elif failure == 'model':
        events[1]['properties']['info']['modelID'] = 'deepseek-v4.1-flash'
    elif failure == 'second_step':
        events.insert(3, events[2])
    elif failure == 'cost':
        events[4]['properties']['part']['cost'] = .1
    elif failure == 'truncated':
        events[4]['properties']['part']['reason'] = 'length'
    elif failure == 'invalid':
        events[3]['properties']['part']['text'] = '```json\n{}\n```'
    elif failure == 'array':
        events[3]['properties']['part']['text'] = '[]'
    with pytest.raises((RuntimeError, ValueError)) as raised:
        _consume(stream(events), 'session-test')
    assert 'secret-marker' not in str(raised.value)


def test_provider_gate_rejects_missing_permission_key_and_paid_ids(monkeypatch):
    monkeypatch.delenv('OPENCODE_API_KEY', raising=False)
    calls = []
    monkeypatch.setattr(opencode_script.shutil, 'which', lambda *_: calls.append('lookup'))
    with pytest.raises(ValueError, match='--allow-api'):
        generate_script({}, 'opencode', FREE_MODEL)
    for model in ('deepseek-v4.1-flash', 'deepseek-v4.1-flash-1017', 'opencode/' + FREE_MODEL):
        with pytest.raises(ValueError, match='free model'):
            generate_script({}, 'opencode', model, allow_api=True)
    with pytest.raises(ValueError, match='credential'):
        generate_script({}, 'opencode', FREE_MODEL, allow_api=True)
    assert calls == []


@pytest.fixture
def native_cli(tmp_path, monkeypatch):
    """A real local IPC process, not a provider call or an installed fake CLI."""
    def install(events):
        record, pid, retried = (tmp_path / name for name in ('request.json', 'pid', 'retried'))
        executable = tmp_path / 'opencode'
        executable.write_text('#!' + sys.executable + '\nCONFIG = ' + repr({
            'events': events, 'record': str(record), 'pid': str(pid), 'retried': str(retried)}) + '\n' + '''
import base64, json, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
if '--version' in sys.argv:
    print('1.19.0')
    raise SystemExit
Path(CONFIG['pid']).write_text(str(os.getpid()))
ready = threading.Event()
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.send_response(200)
        self.end_headers()
        if self.path == '/session':
            assert data['title'] == 'voiceBox script'
            assert data['permission'] == [{'permission': '*', 'pattern': '*', 'action': 'ask'}]
            self.wfile.write(b'{"id":"session-test"}')
        elif self.path.endswith('/prompt_async'):
            Path(CONFIG['record']).write_text(json.dumps({'payload': data,
                'env_keys': sorted(os.environ), 'home': os.environ['HOME']}))
            ready.set()
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        self.wfile.write(b'data: {"type":"server.connected"}\\n\\n')
        self.wfile.flush()
        ready.wait(5)
        for e in CONFIG['events']:
            self.wfile.write(b'data: ' + json.dumps(e).encode() + b'\\n\\n')
            self.wfile.flush()
            if e['type'] == 'session.status' and e['properties']['status']['type'] == 'retry':
                time.sleep(2)
                Path(CONFIG['retried']).write_text('second attempt')
server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
print('opencode server listening on http://127.0.0.1:' + str(server.server_port), flush=True)
server.serve_forever()
''')
        executable.chmod(0o755)
        monkeypatch.setenv('PATH', str(tmp_path) + os.pathsep + os.environ['PATH'])
        return record, pid, retried
    return install


def test_native_session_is_isolated_and_stopped(native_cli, monkeypatch):
    record, pid, _ = native_cli(response_events())
    monkeypatch.setenv('GITHUB_TOKEN', 'unrelated-secret')
    monkeypatch.setenv('GROQ_API_KEY', 'unrelated-secret')
    monkeypatch.setenv('OPENCODE_API_KEY', 'selected-test-key')
    script = generate_script({'system': 'JSON only', 'user': 'test'}, 'opencode', FREE_MODEL, allow_api=True)
    assert script == {'ok': True}
    data = json.loads(record.read_text())
    assert data['payload']['model'] == {'providerID': 'opencode', 'modelID': FREE_MODEL}
    assert (opencode_script.ROOT / 'docs/RULES.md').read_text() in data['payload']['system']
    assert 'GITHUB_TOKEN' not in data['env_keys'] and 'GROQ_API_KEY' not in data['env_keys']
    assert not Path(data['home']).exists()
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid.read_text()), 0)


def test_retry_terminates_native_process_before_next_attempt(native_cli):
    events = response_events()[:3] + [event('session.status', sessionID='session-test', status={'type': 'retry'})]
    _, pid, retried = native_cli(events)
    with pytest.raises(RuntimeError, match='retry blocked'):
        generate_opencode_script({'system': 'JSON only', 'user': 'test'}, 'selected-test-key')
    assert not retried.exists()
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid.read_text()), 0)


def test_connection_check_needs_explicit_permission_and_keeps_no_raw_error(tmp_path, monkeypatch):
    path = tmp_path / 'receipt.json'
    with pytest.raises(ValueError, match='--allow-api'):
        check(path)
    assert not path.exists()
    def fail(*args):
        raise RuntimeError('secret-marker')
    monkeypatch.setattr('tools.check_opencode.generate_opencode_script', fail)
    with pytest.raises(RuntimeError, match='connection check failed'):
        check(path, allow_api=True)
    receipt = json.loads(path.read_text())
    assert receipt['success'] is False and receipt['error_type'] == 'RuntimeError'
    assert 'secret-marker' not in path.read_text()


@pytest.mark.parametrize('selected,provider', [('request', 'opencode'), ('opencode', 'groq')])
def test_video_requires_matching_explicit_opencode_selection(tmp_path, monkeypatch, selected, provider):
    from tools import actions_run
    project = tmp_path / 'research-request.json'
    project.write_text(json.dumps({'provider': provider}))
    monkeypatch.setenv('SCRIPT_PROVIDER', selected)
    monkeypatch.setenv('PROJECT_ZIP_URL', 'https://example.org/input.zip')
    monkeypatch.setattr(sys, 'argv', ['actions_run', '--mode', 'production',
        '--engine-dir', str(tmp_path / 'engine'), '--output', str(tmp_path / 'out')])
    monkeypatch.setattr(actions_run, 'install_font', lambda *_: tmp_path / 'font.ttf')
    monkeypatch.setattr(actions_run, 'download', lambda *_: None)
    monkeypatch.setattr(actions_run, 'extract_project', lambda *_: project)
    with pytest.raises(ValueError, match='matching script_provider'):
        actions_run.main()


def test_provided_script_and_research_only_never_call_opencode(tmp_path, monkeypatch):
    from tools import prepare_project
    pack = {'topic': 'fixture', 'sources': [{'id': 'fixture', 'extraction_status': 'EXTRACTED',
                                            'text': 'Provided source body', 'url': 'https://example.org'}]}
    monkeypatch.setattr(prepare_project, 'research', lambda *_: pack)
    def forbidden(*args, **kwargs):
        pytest.fail('Provided scripts and provider:none must not call a model')
    monkeypatch.setattr(prepare_project, 'generate_script', forbidden)
    result = prepare_project.prepare({'provider': 'none'}, tmp_path / 'research')
    assert result['status'] == 'RESEARCH_READY'
    # Stop at the next independent subsystem: reaching capture proves that the
    # supplied script bypassed the provider gate, even with an invalid model ID.
    class ReachedCapture(Exception): pass
    async def capture(*args, **kwargs):
        raise ReachedCapture
    monkeypatch.setattr(prepare_project, 'capture_targets', capture)
    script = {'title': 'fixture', 'lines': [
        {'speaker': 'ずんだもん', 'text': '提供台本なのだ。', 'source_id': 'fixture'},
        {'speaker': '四国めたん', 'text': 'モデルは呼ばないわ。', 'source_id': 'fixture'}]}
    with pytest.raises(ReachedCapture):
        prepare_project.prepare({'provider': 'opencode', 'model': 'paid-model'},
                                tmp_path / 'provided', script, allow_api=True)


def test_agentrouter_native_session_uses_only_selected_provider(native_cli, monkeypatch):
    from tools.check_agentrouter import check_connection
    events = response_events('{"fixed_return":"a + b"}')
    events[1]['properties']['info'].update(providerID='agentrouter', modelID='gpt-6.1-sol')
    events[4]['properties']['part']['cost'] = .1
    record, pid, _ = native_cli(events)
    monkeypatch.setenv('AGENTROUTER_API_KEY', 'selected-agentrouter-key')
    monkeypatch.setenv('GITHUB_TOKEN', 'unrelated-secret')
    monkeypatch.setenv('OPENCODE_API_KEY', 'unrelated-secret')
    receipt = check_connection('gpt-6.1-sol', allow_api=True)
    assert receipt['status'] == 'CONNECTED' and receipt['billing_verified'] is False
    assert receipt['catalog_cost'] == .1  # Routing is not a claim of free usage.
    data = json.loads(record.read_text())
    assert data['payload']['model'] == {'providerID': 'agentrouter', 'modelID': 'gpt-6.1-sol'}
    assert 'AGENTROUTER_API_KEY' in data['env_keys']
    assert 'OPENCODE_API_KEY' not in data['env_keys'] and 'GITHUB_TOKEN' not in data['env_keys']
    assert not Path(data['home']).exists()
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid.read_text()), 0)


def test_agentrouter_configuration_has_fixed_endpoint_and_no_inline_secret(tmp_path):
    env = opencode_script._environment(tmp_path, 'secret-marker', 'local-password',
                                      provider='agentrouter', model='gpt-6.1-sol')
    config = json.loads(env['OPENCODE_CONFIG_CONTENT'])
    assert config['enabled_providers'] == ['agentrouter']
    assert config['model'] == config['small_model'] == 'agentrouter/gpt-6.1-sol'
    options = config['provider']['agentrouter']['options']
    assert options['baseURL'] == 'https://agentrouter.org/v1'
    assert options['apiKey'] == '{env:AGENTROUTER_API_KEY}' and options['maxRetries'] == 0
    assert 'secret-marker' not in env['OPENCODE_CONFIG_CONTENT']


@pytest.mark.parametrize('model', ['../other', 'gpt-6.1-sol\nother', '', 'x'*129])
def test_agentrouter_invalid_ids_never_start_client(monkeypatch, model):
    from tools import check_agentrouter
    monkeypatch.setattr(check_agentrouter, '_run_session', lambda *a, **k: pytest.fail('No call expected'))
    with pytest.raises(ValueError, match='model ID'):
        check_agentrouter.check_connection(model, allow_api=True)


def test_agentrouter_needs_own_key_and_explicit_call(monkeypatch):
    from tools import check_agentrouter
    monkeypatch.delenv('AGENTROUTER_API_KEY', raising=False)
    monkeypatch.setenv('OPENCODE_API_KEY', 'not-an-agentrouter-key')
    with pytest.raises(ValueError, match='--allow-api'):
        check_agentrouter.check_connection('gpt-6.1-sol')
    with pytest.raises(ValueError, match='AGENTROUTER_API_KEY'):
        check_agentrouter.check_connection('gpt-6.1-sol', allow_api=True)


def test_agentrouter_failure_receipt_excludes_raw_provider_error(tmp_path, monkeypatch, capsys):
    from tools import check_agentrouter
    path = tmp_path / 'receipt.json'
    monkeypatch.setenv('AGENTROUTER_API_KEY', 'secret-marker')
    def fail(*a, **k):
        raise RuntimeError('Authorization: Bearer secret-marker')
    monkeypatch.setattr(check_agentrouter, '_run_session', fail)
    monkeypatch.setattr(sys, 'argv', ['check_agentrouter', '--allow-api', '--output', str(path)])
    assert check_agentrouter.main() == 1
    receipt = json.loads(path.read_text())
    assert receipt['status'] == 'FAILED' and receipt['credential_configured'] is True
    captured = capsys.readouterr()
    assert 'secret-marker' not in path.read_text() + captured.out + captured.err
