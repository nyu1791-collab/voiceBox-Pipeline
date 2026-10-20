"""Check AgentRouter through its supported official OpenCode coding client."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from opencode_script import _run_session


def check_connection(model, *, allow_api=False):
    if not allow_api:
        raise ValueError('Explicit --allow-api is required')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', model):
        raise ValueError('Invalid AgentRouter model ID')
    key = os.environ.get('AGENTROUTER_API_KEY')
    if not key:
        raise ValueError('AGENTROUTER_API_KEY is not configured in Actions Secrets')
    prompt = {
        'system': 'You are checking a coding API connection. Review the tiny Python function. '
                  'Return only {"fixed_return":"a + b"} if addition is implemented by subtraction. '
                  'Do not use tools, read files, run commands, or add commentary.',
        'user': 'def add(a, b):\n    return a - b\n',
    }
    result, receipt = _run_session(prompt, key, timeout=120, provider='agentrouter',
                                   model=model, free_only=False)
    if result != {'fixed_return': 'a + b'}:
        raise ValueError('AgentRouter coding probe returned an unexpected result')
    return {**receipt, 'status': 'CONNECTED',
            'verified_utc': datetime.now(timezone.utc).isoformat(),
            'endpoint': 'https://agentrouter.org/v1', 'coding_probe_passed': True,
            'billing_verified': False, 'repository_modified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default='gpt-6.1-sol')
    parser.add_argument('--allow-api', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        receipt = check_connection(args.model, allow_api=args.allow_api)
    except Exception:
        # No provider exception, model output, prompt, Authorization or key is logged.
        receipt = {'status': 'FAILED', 'provider': 'agentrouter',
                   'verified_utc': datetime.now(timezone.utc).isoformat(),
                   'credential_configured': bool(os.environ.get('AGENTROUTER_API_KEY')),
                   'error': 'Connection not verified; check key, model, quota and CLI setup'}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
        print(receipt['error'], file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    print('AgentRouter coding connection verified; receipt saved')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
