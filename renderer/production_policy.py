"""Production defaults: one final review, advisory style rules, immutable inputs."""
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import urlparse


def batch_mode():
    return os.environ.get('VIDEO_REVIEW_MODE', 'strict') == 'batch'


def advisory(message):
    return any(term in message for term in (
        '表情は2種類', '明示的な表情計画が2種類', '主題場面には両話者',
        '主題場面に両話者', '献立は15文字', '日本語訳は1〜30文字',
        '訳が30字', '同じ切抜き対象', '表示用英語引用が合計25語',
    ))


def split_findings(messages):
    if not batch_mode():
        return messages, []
    return ([m for m in messages if not advisory(m)],
            [m for m in messages if advisory(m)])


def short_source_label(url, diagram=False):
    host = urlparse(url).hostname or '出典'
    names = {'deepmind.google': 'Google DeepMind', 'www.mddi.gov.sg': 'MDDI'}
    name = names.get(host, host.removeprefix('www.'))
    # The full name and URL stay in source_credit/source_url. Keep the overlay bounded.
    if len(name) > 28:
        name = name[:25] + '…'
    return '出典：' + name + ('／原文引用図' if diagram else '') + '／内部確認用'


def source_layout(items, font):
    from PIL import ImageFont
    f = ImageFont.truetype(str(font), 15)
    errors = []
    for item in items:
        label = short_source_label(item['source_url'], item.get('kind') == 'explain_diagram')
        if f.getlength(label) > 610:
            errors.append(item['id'] + ': 出典ラベルが表示幅を超える')
    return errors


def validate_delivery(task, version, destination):
    task, version, destination = map(lambda p:Path(p).resolve(),(task,version,destination))
    if destination.is_relative_to(version) or destination in version.parents:
        raise ValueError('受渡し先が版の保存先・親と重なる')
    if destination == task or any(destination.is_relative_to(task/n) for n in ('work','sources','capture','pipeline','checks','voice_pack')):
        raise ValueError('受渡し先がtaskの入力と重なる')
    return destination


def readiness(task, python, repo, shell, delivery_dir=None, capture=False):
    """Small operational checks before expensive work; no content review here."""
    import subprocess
    task, repo = Path(task), Path(repo)
    problems = []
    for binary in ('ffmpeg', 'ffprobe'):
        if not shutil.which(binary):
            problems.append(binary + ' がない')
    modules = ['PIL'] + (['playwright.async_api'] if capture else [])
    code = ';'.join('__import__(' + repr(m) + ')' for m in modules)
    result = subprocess.run([str(python), '-c', code], capture_output=True, text=True)
    if result.returncode:
        problems.append('指定した制作用Pythonに必要なモジュールがない: ' + ', '.join(modules))
    if capture and not Path('/usr/bin/google-chrome').is_file():
        problems.append('Chromeがない')
    if not Path(shell).is_dir():
        problems.append('人物素材の保存先がない')
    if problems:
        raise ValueError('; '.join(problems))
    result = subprocess.run([str(python), str(repo/'tools/caption_font.py'),
                             str(repo/'config/landscape_profile_v18.json')],
                            capture_output=True, text=True, check=True)
    font = Path(result.stdout.strip())
    items = json.loads((task/'material-sheet-v1.json').read_text())['items']
    problems.extend(source_layout(items, font))
    if delivery_dir is not None:
        dest = Path(delivery_dir)
        dest.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix='.delivery-check-', dir=dest):
            pass
    if problems:
        raise ValueError('; '.join(problems))
    return {'state': 'OK', 'font': str(font), 'review_mode': 'batch' if batch_mode() else 'strict',
            'delivery': 'LOCAL_DIRECTORY' if delivery_dir else 'NOT_REQUESTED'}


def needs_capture(task):
    """A stale/corrupt receipt cannot establish that browser preparation can be skipped."""
    from material_prepare import current_receipt
    try:
        return current_receipt(task) is None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return True


def summarize_rules(rules):
    """Keep genuine input/media errors blocking; present style findings together."""
    if not batch_mode() or rules.get('error'):
        return rules
    style = {'R40_shu_at_visual_change', 'R41_translation_for_overseas',
             'R42_marker_for_important', 'R45_peek_on_switch', 'R47_agenda_style',
             'R53_translation_length', 'R55_table_se_not_in_card', 'R56_marker_limit'}
    failed = rules.get('failures', [])
    blocking = [key for key in failed if key not in style]
    # An unexplained nonzero exit is a crashed checker, not a cosmetic finding.
    exit_code = rules.get('exit_code', 0)
    unexplained = exit_code not in (0, 1) or (exit_code != 0 and not failed)
    return {**rules, 'state': 'FAIL' if blocking or unexplained else 'OK',
            'failures': blocking,
            'warnings': sorted(set(rules.get('pending', []) + [key for key in failed if key in style])),
            'pending': [], 'original_failures': failed}
