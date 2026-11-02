"""Colab GPU runtime で voiceBox-Pipeline を一度だけ取り込み、GPU と符号化器を確かめて、すぐに止める。
  COLAB_ADC_JSON（認証の JSON 文字列）を環境変数で渡す。値は表示しない。
  python tools/colab_sync_check.py
GPU を1台（L4）だけ使う。処理が終わったら必ず停止し、残りのランタイムが無いことを確かめる。"""
import json
import os
import sys
import time
import uuid

REPO = "https://github.com/nyu1791-collab/voiceBox-Pipeline"
EXPECTED_ACCOUNTS = {"n.yu17919191@gmail.com", "n.yu1791919191@gmail.com"}

CHECK_CODE = r"""
import subprocess
print(subprocess.run(['nvidia-smi', '-L'], capture_output=True, text=True).stdout.strip())
clone = subprocess.run(['git', 'clone', '--depth', '1', '%s', '/content/voicebox'], capture_output=True, text=True)
print('clone exit', clone.returncode)
print(subprocess.run(['git', '-C', '/content/voicebox', 'log', '-1', '--format=%%h %%s'], capture_output=True, text=True).stdout.strip())
enc = subprocess.run(['ffmpeg', '-hide_banner', '-encoders'], capture_output=True, text=True).stdout
print('h264_nvenc available:', 'h264_nvenc' in enc)
""" % REPO


def main():
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2.credentials import Credentials
    from colab_cli.client import Client, Prod, Accelerator, Variant, Shape, TooManyAssignmentsError
    from colab_cli.runtime import ColabRuntime

    raw = os.environ.get("COLAB_ADC_JSON", "")
    if not raw:
        raise SystemExit("COLAB_ADC_JSON が未設定です。何も起動していません")
    info = json.loads(raw)
    if info.get("type") != "authorized_user":
        raise SystemExit("認証の形式が合いません")
    session = AuthorizedSession(Credentials.from_authorized_user_info(info))
    identity = session.get("https://www.googleapis.com/oauth2/v2/userinfo", timeout=30).json()
    if identity.get("email", "").lower() not in EXPECTED_ACCOUNTS or not identity.get("verified_email"):
        raise SystemExit("許可していないアカウントです。何も起動していません")
    client = Client(Prod(), session)

    def units():
        u = client.get_consumption_user_info()
        return round(float(u.paid_compute_units_balance), 3), int(u.assignments_count)

    before = units()
    print("start: balance %.3f, active %d" % before, flush=True)
    notebook = uuid.uuid4()
    try:
        assignment = client.assign(notebook, variant=Variant.GPU, accelerator=Accelerator.L4, shape=Shape.STANDARD)
    except TooManyAssignmentsError:
        raise SystemExit("ランタイムの上限に達しています。何も起動していません")
    print("granted L4", flush=True)
    try:
        runtime = ColabRuntime(assignment.runtime_proxy_info.url, assignment.runtime_proxy_info.token)
        outputs = runtime.execute_code(CHECK_CODE, allow_stdin=False, timeout=900)
        for item in outputs:
            if item.get("output_type") == "stream":
                print(item.get("text", "").rstrip(), flush=True)
            elif item.get("output_type") == "error":
                print("kernel error:", item.get("ename", ""), flush=True)
    finally:
        client.unassign(assignment.endpoint)
        for attempt in range(4):
            if not any(item.endpoint == assignment.endpoint for item in client.list_assignments()):
                print("released", flush=True)
                break
            time.sleep(2 + attempt)
        else:
            raise SystemExit("停止を確認できませんでした。Colabの画面で確かめてください")
    after = units()
    print("end: balance %.3f, active %d" % after, flush=True)
    print("used %.3f units" % (before[0] - after[0]), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
