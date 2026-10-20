"""One text-only generation through the official, locally hosted OpenCode CLI."""
from pathlib import Path
import base64
import json
import os
import re
import secrets
import select
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import urllib.request

FREE_MODEL = 'deepseek-v4.1-flash-free'
CLI_VERSION = '1.19.0'
ROOT = Path(__file__).resolve().parent


def _environment(directory, key, password):
    # Keep network trust/proxy configuration, but do not expose other providers'
    # credentials, GitHub tokens, local auth files or user plugins to this session.
    keep = {'PATH', 'LANG', 'LC_ALL', 'SYSTEMROOT', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
            'NODE_EXTRA_CA_CERTS', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE',
            'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
            'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy'}
    env = {k: v for k, v in os.environ.items() if k in keep}
    env.update(HOME=str(directory), OPENCODE_API_KEY=key,
               XDG_CONFIG_HOME=str(directory / 'config'), XDG_DATA_HOME=str(directory / 'data'),
               XDG_CACHE_HOME=str(directory / 'cache'), XDG_STATE_HOME=str(directory / 'state'),
               OPENCODE_SERVER_USERNAME='voicebox', OPENCODE_SERVER_PASSWORD=password,
               OPENCODE_DISABLE_AUTOUPDATE='true', OPENCODE_DISABLE_PROJECT_CONFIG='true')
    env['OPENCODE_CONFIG_CONTENT'] = json.dumps({
        'model': 'opencode/' + FREE_MODEL, 'small_model': 'opencode/' + FREE_MODEL,
        'enabled_providers': ['opencode'], 'share': 'disabled', 'snapshot': False,
        'autoupdate': False, 'compaction': {'auto': False, 'prune': False},
        # "ask" keeps the official tool catalog but authorizes no tool execution.
        # We never approve permissions, and terminate on any tool/permission event.
        'permission': 'ask', 'agent': {'title': {'disable': True},
                                     'summary': {'disable': True},
                                     'compaction': {'disable': True}}})
    return env


def _consume(events, session_id):
    texts, starts, finish, tokens, cost = {}, 0, None, None, None
    assistants = set()
    for raw in events:
        if not raw.startswith(b'data:'):
            continue
        event = json.loads(raw[5:])
        properties = event.get('properties', {})
        part = properties.get('part', {})
        info = properties.get('info', {})
        owner = properties.get('sessionID') or part.get('sessionID') or info.get('sessionID')
        if owner != session_id:
            continue
        kind = event.get('type')
        if kind == 'session.error':
            # Never echo server messages, response bodies, headers or credentials.
            raise RuntimeError('OpenCode generation failed; no retry or fallback')
        if kind == 'session.status':
            status = properties.get('status', {}).get('type')
            if status == 'retry':
                raise RuntimeError('OpenCode retry blocked; no second request was permitted')
            if status == 'idle':
                break
        if kind == 'permission.asked':
            raise RuntimeError('OpenCode requested a tool permission; generation stopped')
        if kind == 'message.updated' and info.get('role') == 'assistant':
            if info.get('providerID') != 'opencode' or info.get('modelID') != FREE_MODEL:
                raise RuntimeError('OpenCode returned an unexpected provider or model')
            assistants.add(info['id'])
        if kind != 'message.part.updated' or part.get('messageID') not in assistants:
            continue
        if part.get('type') == 'tool':
            raise RuntimeError('OpenCode attempted tool use; generation stopped')
        if part.get('type') == 'step-start':
            starts += 1
            if starts > 1:
                raise RuntimeError('OpenCode attempted another generation step')
        if part.get('type') == 'text':
            texts[part['id']] = part.get('text', '')
        if part.get('type') == 'step-finish':
            finish, tokens, cost = part.get('reason'), part.get('tokens'), part.get('cost')
            if finish != 'stop':
                raise ValueError('OpenCode output was incomplete')
            if cost is not None and cost != 0:
                raise RuntimeError('OpenCode reported a nonzero catalog cost')
    if starts != 1 or finish != 'stop':
        raise ValueError('OpenCode output was incomplete')
    try:
        script = json.loads(''.join(texts.values()))
    except (ValueError, TypeError):
        raise ValueError('OpenCode did not return valid JSON') from None
    if not isinstance(script, dict):
        raise ValueError('OpenCode must return a JSON object')
    return script, {'provider': 'opencode', 'model': FREE_MODEL, 'method': 'official-cli-server',
                    'cli_version': CLI_VERSION, 'request_count': starts,
                    'finish_reason': finish, 'tokens': tokens, 'catalog_cost': cost}


def generate_opencode_script(prompt, key, *, timeout=180):
    """Use native OpenCode sessions; never forge a client identity at the gateway."""
    if not key:
        raise ValueError('Selected script API credential is not configured')
    executable = shutil.which('opencode')
    if not executable:
        raise RuntimeError('Install the official opencode-ai@' + CLI_VERSION + ' CLI')
    with tempfile.TemporaryDirectory(prefix='voicebox-opencode-') as temporary:
        directory = Path(temporary)
        password = secrets.token_urlsafe(32)
        env = _environment(directory, key, password)
        try:
            version = subprocess.run([executable, '--version'], env=env, cwd=directory,
                                     capture_output=True, text=True, timeout=15, check=True)
        except (OSError, subprocess.SubprocessError):
            raise RuntimeError('Could not verify the official OpenCode CLI version') from None
        if version.stdout.strip() != CLI_VERSION:
            raise RuntimeError('Use the verified opencode-ai@' + CLI_VERSION + ' CLI')
        process = subprocess.Popen([executable, 'serve', '--pure', '--hostname', '127.0.0.1',
                                    '--port', '0'], cwd=directory, env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   start_new_session=True)

        def stop():
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        # This bound also interrupts a stalled event stream and covers startup.
        timer = threading.Timer(timeout, stop)
        timer.start()
        try:
            deadline = time.monotonic() + min(timeout, 45)
            base_url = None
            while time.monotonic() < deadline and process.poll() is None:
                if not select.select([process.stdout], [], [], .1)[0]:
                    continue
                line = process.stdout.readline()
                match = re.search(rb'http://127\.0\.0\.1:(\d+)', line)
                if match:
                    base_url = 'http://127.0.0.1:' + match[1].decode()
                    break
            if not base_url:
                raise RuntimeError('OpenCode local server did not start')
            # Only local IPC bypasses the outbound proxy. Provider traffic keeps
            # the inherited proxy and CA settings in the CLI process.
            local = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            authorization = 'Basic ' + base64.b64encode(('voicebox:' + password).encode()).decode()

            def request(path, data=None):
                return local.open(urllib.request.Request(base_url + path,
                    data=json.dumps(data).encode() if data is not None else None,
                    headers={'Authorization': authorization, 'Content-Type': 'application/json'}),
                    timeout=timeout)

            with request('/session', {'title': 'voiceBox script', 'permission': [
                    {'permission': '*', 'pattern': '*', 'action': 'ask'}]}) as response:
                session_id = json.load(response)['id']
            with request('/event') as events:
                rules = (ROOT / 'docs/RULES.md').read_text()
                with request('/session/' + session_id + '/prompt_async', {
                    'agent': 'build', 'model': {'providerID': 'opencode', 'modelID': FREE_MODEL},
                    'system': prompt['system'] + '\nNo tools. Return only the requested JSON object, '
                              'without Markdown fences or explanatory text.\n'
                              '制作ルール全文:\n' + rules,
                    'parts': [{'type': 'text', 'text': prompt['user'] +
                               '\n出力はJSONオブジェクトのみ。コードフェンス・説明文・ツール呼出しは禁止。'}]}):
                    pass
                return _consume(events, session_id)
        except (OSError, ValueError, KeyError) as error:
            # Preserve only our own validation messages; never propagate local
            # server bodies, input JSON, Authorization headers or API credentials.
            if isinstance(error, ValueError) and str(error).startswith('OpenCode '):
                raise
            raise RuntimeError('OpenCode session failed; no retry or fallback') from None
        finally:
            timer.cancel()
            stop()
            process.wait()
            process.stdout.close()
