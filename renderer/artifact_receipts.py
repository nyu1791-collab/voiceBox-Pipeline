"""同じ入力・実装・出力の工程だけを再利用する、小さな共通受領処理。"""
from pathlib import Path
import json
from pipeline_common import atomic_json, sha256


def file_hashes(paths):
    return {str(Path(p).resolve()): sha256(p) for p in sorted(set(map(Path, paths)))}


def reusable(path, inputs, context):
    try:
        receipt = json.loads(Path(path).read_text())
        if (not isinstance(receipt,dict) or receipt.get('schema') != 'artifact-receipt-v1' or receipt.get('context') != context
                or receipt.get('inputs') != inputs or not isinstance(receipt.get('outputs'),dict)
                or not receipt.get('outputs')):
            return False
        return all(sha256(p) == digest for p, digest in {**inputs, **receipt['outputs']}.items())
    except (OSError, ValueError, TypeError, KeyError):
        return False


def record(path, inputs, outputs, context):
    # 工程中に入力が変化していれば成功受領を作らない。
    if not inputs or any(sha256(p) != digest for p, digest in inputs.items()):
        raise ValueError('工程中に入力が変化したため再利用受領を作らない')
    atomic_json(path, {'schema': 'artifact-receipt-v1', 'inputs': inputs,
                       'outputs': file_hashes(outputs), 'context': context})
