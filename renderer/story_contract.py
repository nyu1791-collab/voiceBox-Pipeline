"""台本準備で共有する取得・日付・根拠の契約。意味の全面監査ではない。"""
import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from html.parser import HTMLParser
from urllib.parse import urlparse

CONTRACT = 'story-authoring-v2'
EXCERPT_LIMIT = 26000
MIN_FACTS = 4

def norm(text):
    return re.sub(r'\s+', ' ', text.translate(str.maketrans({'’':"'",'‘':"'",'“':'"','”':'"'}))).strip()

def original_quote(quote, source):
    if not isinstance(quote,str) or not quote.strip(): return None
    parts=[]
    for char in norm(quote):
        parts.append(r"['’‘]" if char=="'" else r'["“”]' if char=='"' else r'\s+' if char==' ' else re.escape(char))
    pattern=''.join(parts)
    if quote[0].isascii() and quote[0].isalnum():pattern=r'(?<!\w)'+pattern
    if quote[-1].isascii() and quote[-1].isalnum():pattern+=r'(?!\w)'
    match=re.search(pattern,source)
    return match.group() if match else None

def iso_date(value):
    if not isinstance(value,str) or not re.fullmatch(r'20\d{2}-\d{2}-\d{2}',value):return None
    try:return datetime.date.fromisoformat(value).isoformat()
    except ValueError:return None

MONTH = r'Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?'
MONTHS = {name:i for i,name in enumerate('jan feb mar apr may jun jul aug sep oct nov dec'.split(),1)}

def publication_date(text):
    for match in re.finditer(r'\b('+MONTH+r')\.?\s+(\d{1,2}),?\s+(20\d{2})\b',text[:8000],re.I):
        before=text[max(0,match.start()-70):match.start()]
        if re.search(r'\bbilling\s+(?:starts?|begins?)\s+(?:on\s+)?$',before,re.I):continue
        return iso_date(f'{int(match[3]):04d}-{MONTHS[match[1][:3].lower()]:02d}-{int(match[2]):02d}')
    # 公開日のラベルが付いたフランス語の日付だけを採用。本文の開催日/更新日は拾わない。
    french = {'janvier':1,'janv':1,'février':2,'févr':2,'fevrier':2,'fevr':2,'mars':3,
              'avril':4,'avr':4,'mai':5,'juin':6,'juillet':7,'juil':7,'août':8,'aout':8,
              'septembre':9,'sept':9,'octobre':10,'oct':10,'novembre':11,'nov':11,'décembre':12,'déc':12,'decembre':12,'dec':12}
    for match in re.finditer(r'\bPubli(?:é|ée)\s+le\s+(\d{1,2})\s+([a-zéûîôàè]+)\.?\s+(20\d{2})\b', text[:8000], re.I):
        month = french.get(match[2].lower())
        if month:
            date = iso_date(f'{int(match[3]):04d}-{month:02d}-{int(match[1]):02d}')
            if date:return date
    return None


def separate_source_annotations(fact, source):
    """Move only corroborated machine labels; preserve factual caveats and raw inputs."""
    fact=dict(fact)
    if not isinstance(fact.get('statement'),str):return fact
    checks=source.get('date_checks')
    expected=checks.get('state') if isinstance(checks,dict) else None
    pattern=r'[（(]published_atは(NOT_FOUND|UNVERIFIED)(?:[、,](?:取得時点の一覧|取得時点のガイド|取得時点の価格))*[）)]'
    moved=[]
    def remove(match):
        if match[1]!=expected:return match[0]
        moved.append({'field':'published_at','state':expected,'original_annotation':match[0]})
        return ''
    value=re.sub(pattern,remove,fact['statement'])
    if moved:
        previous=fact.get('source_annotations')
        if previous is None:previous=[]
        elif not isinstance(previous,list):previous=[previous]
        fact.update(statement=value,source_annotations=previous+moved)
    return fact

class PublishedMetadata(HTMLParser):
    def __init__(self):
        super().__init__();self.candidates=[];self.ld=False;self.buffer=[]
    def add(self,value):
        date=iso_date(value[:10]) if isinstance(value,str) else None
        if date:self.candidates.append(date)
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='meta' and (attrs.get('property') or attrs.get('name') or attrs.get('itemprop','')).lower() in {'article:published_time','datepublished','pubdate','publishdate'}:self.add(attrs.get('content'))
        if tag=='time' and attrs.get('itemprop','').lower()=='datepublished':self.add(attrs.get('datetime'))
        if tag=='script' and attrs.get('type','').lower()=='application/ld+json':self.ld=True;self.buffer=[]
    def handle_data(self,text):
        if self.ld:self.buffer.append(text)
    def handle_endtag(self,tag):
        if tag!='script' or not self.ld:return
        self.ld=False
        try:value=json.loads(''.join(self.buffer))
        except (ValueError,RecursionError):return
        def walk(node,depth=0):
            if depth>20:return
            if isinstance(node,dict):
                self.add(node.get('datePublished'))
                for key in ('@graph','mainEntity','mainEntityOfPage'):
                    if key in node:walk(node[key],depth+1)
            elif isinstance(node,list):
                for item in node:walk(item,depth+1)
        walk(value)

def date_receipt(html,text,requested=None):
    if requested is not None and not iso_date(requested):raise ValueError('published_atはISO日付文字列またはnullが必要')
    parser=PublishedMetadata();parser.feed(html);metadata=sorted(set(parser.candidates));visible=publication_date(text)
    candidates={'request':requested,'metadata':metadata,'visible':visible}
    if len(metadata)>1 or (metadata and requested and metadata[0]!=requested):
        return {'published_at':None,'state':'CONFLICT','method':'metadata','candidates':candidates}
    if metadata:return {'published_at':metadata[0],'state':'CONFIRMED_METADATA','method':'published_metadata','candidates':candidates}
    if requested and visible==requested:return {'published_at':requested,'state':'CORROBORATED_VISIBLE','method':'request_and_visible','candidates':candidates}
    if requested and visible and requested!=visible:return {'published_at':None,'state':'CONFLICT','method':'visible','candidates':candidates}
    return {'published_at':None,'state':'UNVERIFIED' if requested or visible else 'NOT_FOUND','method':'request_only' if requested else 'visible_only' if visible else 'none','candidates':candidates}

def usable_date(source):
    checks=source.get('date_checks')
    if checks and checks.get('state') not in ('CONFIRMED_METADATA','CORROBORATED_VISIBLE'):return None
    return iso_date(source.get('published_at'))

def document_key(source):
    sha=source.get('text_sha256')
    if isinstance(sha,str) and re.fullmatch('[a-f0-9]{64}',sha):return sha
    return source.get('url') or source['source_id']

def substantive_quote(quote):
    """機能語や一文字で件数を満たさない。含意の検査とは区別する。"""
    if len(re.sub(r'[\W_]+','',quote))<12:return False
    if re.search(r'[\u3040-\u30ff\u3400-\u9fff]',quote):return True
    words=re.findall(r"[A-Za-z]+(?:['’-][A-Za-z]+)*",quote)
    return len(words)>=3 and any(w.lower() not in {'a','an','the','of','to','in','on','at','by','and','or','is','are','was','were','it','as','for','with'} for w in words)

def numeric_tokens(text,source=None):
    numbers=set(re.findall(r'\d+(?:[.,]\d+)*',text))
    # フランス語の空白/NBSP区切りの千位表記を、整数の同値表現として追加する。
    for match in re.finditer(r'(?<![\d.,])\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?!\d)', text):
        numbers.add(re.sub(r'[ \u00a0\u202f]', '', match.group()))
    date=usable_date(source or {})
    if date:numbers.update(str(int(n)) for n in date.split('-'))
    aliases=[('one','first'),('two','second'),('three','third'),('four','fourth'),('five','fifth'),('six','sixth'),('seven','seventh'),('eight','eighth'),('nine','ninth'),('ten','tenth'),('eleven','eleventh'),('twelve','twelfth')]
    for i,words in enumerate(aliases,1):
        if re.search(r'\b(?:'+'|'.join(words)+r')\b',text,re.I):numbers.add(str(i))
    # 月名だけでは数値化しない。may/march等の一般語を月と誤認しない。
    for pattern in [r'\b('+MONTH+r')\.?\s+(?:\d{1,2}(?:,?\s+20\d{2})?|20\d{2})\b',r'\b\d{1,2}\s+('+MONTH+r')\b']:
        for match in re.finditer(pattern,text,re.I):numbers.add(str(MONTHS[match[1][:3].lower()]))
    # 文中の大文字の月名（例：our August Risk Report）は月として数える。May は一般語と紛らわしいため除外。
    for match in re.finditer(r'(?<=[a-z] )(January|February|March|April|June|July|August|September|October|November|December)\b',text):numbers.add(str(MONTHS[match[1][:3].lower()]))
    # フランス語の月名は具体的な日付の中だけで数値化。mai等の単独語は拾わない。
    french_months = dict(zip(('janvier','février','mars','avril','mai','juin','juillet','août','septembre','octobre','novembre','décembre'), range(1,13)))
    for match in re.finditer(r'\b(\d{1,2})\s+('+'|'.join(french_months)+r')\b', text, re.I):
        if 1 <= int(match[1]) <= 31:numbers.add(str(french_months[match[2].lower()]))
    return numbers

def unsupported_number_values(text, numbers):
    """表示の万/億/兆と原文の整数を正確に換算する。根拠のない値は追加しない。"""
    def decimal(value):
        if re.fullmatch(r'\d{1,3}(?:,\d{3})+(?:\.\d+)?', value):
            value=value.replace(',', '')
        try:return Decimal(value)
        except InvalidOperation:return None
    errors=[]
    supported={decimal(n) for n in numbers}
    supported.discard(None)
    for match in re.finditer(r'(\d+(?:[.,]\d+)*)(万|億|兆)?', text):
        value, unit=match.groups()
        if value in numbers:continue  # 既存のトークン照合を維持
        actual=decimal(value)
        if actual is not None:
            actual *= {'万':10000,'億':100000000,'兆':1000000000000}.get(unit,1)
            if actual in supported:continue
        errors.append(value)
    return errors

def unsupported_fact_numbers(fact,source,text):
    numbers=numeric_tokens(text,source)
    return unsupported_number_values(fact.get('statement',''), numbers)

def support_flags(fact,source,quote,text=""):
    """引用外の数値・明示的英字名は要確認にする。日本語の意味含意は未検証。"""
    flags=[]
    if unsupported_fact_numbers(fact,source,quote):flags.append('要約の数値が支持引用・発表日にない')
    host=(urlparse(source.get('url','')).hostname or '').lower().split('.')
    names=re.findall(r'(?<![A-Za-z0-9])[A-Z][A-Za-z0-9]*(?:[-.][A-Za-z0-9]+)*(?![A-Za-z0-9])',fact.get('statement',''))
    for name in names:
        known=(quote.lower(),' '.join(host),str(source.get('title','')).lower(),str(text).lower())
        if not any(name.lower() in k for k in known):flags.append('引用外の英字名:'+name)
    return flags

def measurement_scope(request=None):
    selection=(request or {}).get('selection_seconds')
    if selection is not None and (isinstance(selection,bool) or not isinstance(selection,(int,float)) or not 0<=selection<86400):raise ValueError('selection_secondsは0以上86400未満')
    return {'measured_from':'CLI開始（request読込・指定URL取得・調査・執筆・修正・G0）','excluded':['題材発見','URL探索・選定','接続復旧','実装'],'selection_seconds':selection if selection is not None else 'NOT_MEASURED','selection_measurement':'external_report' if selection is not None else 'NOT_MEASURED','url_discovery':'NOT_IMPLEMENTED'}

def question_pairing_errors(lines):
    """z（質問側）の疑問文の直後には、m（答え側）の台詞が必要。章見出しや末尾で答えが欠けるのを止める。"""
    errs=[]
    for i,(speaker,text) in enumerate(lines):
        if speaker=='z' and str(text).rstrip().endswith(('？','?')):
            if i+1>=len(lines) or lines[i+1][0]!='m':errs.append(f'台詞{i}: 質問に答えの台詞（m）がない')
    return errs
