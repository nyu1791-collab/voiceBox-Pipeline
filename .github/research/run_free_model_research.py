import json,os,pathlib,re,subprocess,sys
sys.path.insert(0,str(pathlib.Path.cwd()))
from opencode_script import _run_session
model='deepseek-v4.1-flash-free'
r=subprocess.run(['opencode','models','opencode','--refresh','--verbose'],capture_output=True,text=True,timeout=90,check=True)
decoder=json.JSONDecoder();selected=None
for match in re.finditer(r'^opencode/([^\n]+)\n',r.stdout,re.M):
 if match.group(1)==model:
  selected,_=decoder.raw_decode(r.stdout[match.end():].lstrip());break
if not selected:raise SystemExit('Selected DeepSeek free model missing; no fallback')
cost=selected.get('cost',{})
if cost.get('input')!=0 or cost.get('output')!=0 or any(v!=0 for v in cost.get('cache',{}).values()):
 raise SystemExit('Selected DeepSeek model is not free; stopped')
prompt=pathlib.Path('.github/research/free-model-brokers-request.txt').read_text()
result,receipt=_run_session({'system':'Analyze the supplied public source packet. Return only the requested JSON object. No tools or additional requests.','user':prompt},os.environ['OPENCODE_API_KEY'],timeout=300,provider='opencode',model=model,free_only=True)
out=pathlib.Path('deepseek-research');out.mkdir()
(out/'report.json').write_text(json.dumps({'result':result,'receipt':receipt},ensure_ascii=False,indent=2))
print('DeepSeek free-model research completed. Receipt saved without credentials.')
