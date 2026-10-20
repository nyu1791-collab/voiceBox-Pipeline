"""Explicit free-model connection check; no research, screenshots, speech or video."""
from pathlib import Path
import argparse
import json
import os
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from opencode_script import FREE_MODEL, generate_opencode_script
from research import normalize_script, script_prompt


def check(output, *, allow_api=False):
    if not allow_api:
        raise ValueError('Use --allow-api for the explicit OpenCode connection check')
    output = Path(output)
    if output.exists():
        raise FileExistsError('Use a new connection-check receipt path')
    started = time.monotonic()
    pack = {'topic': '接続確認', 'sources': [{'id': 'connection-check',
        'title': '接続確認用の架空資料', 'url': 'https://example.org/connection-check',
        'extraction_status': 'EXTRACTED',
        'text': 'これは接続確認用の架空資料です。ニュースや実在の出来事ではありません。'
                'ずんだもんと四国めたんが接続確認について一言ずつ話します。'}]}
    prompt = script_prompt(pack, 1)
    prompt['user'] = json.dumps({'instruction': '資料だけを使い、各話者1台詞、合計2台詞の短いJSONを返す。',
                                'source': pack['sources'][0]}, ensure_ascii=False)
    receipt = {'provider': 'opencode', 'model': FREE_MODEL, 'success': False}
    try:
        script, details = generate_opencode_script(prompt, os.environ.get('OPENCODE_API_KEY'))
        project = normalize_script(script, pack)
        receipt.update(details, success=True, json_valid=True, script_valid=True,
                       lines=len(project['lines']))
    except Exception as error:
        # No response body, prompts, script, auth material or exception text.
        receipt['error_type'] = type(error).__name__
        raise RuntimeError('OpenCode connection check failed; see the non-secret receipt') from None
    finally:
        receipt['seconds'] = round(time.monotonic() - started, 3)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-api', action='store_true')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(check(args.output, allow_api=args.allow_api), ensure_ascii=False))
