"""Authored screenshot holds, anchored to measured speech rather than speaker turns."""
import math


def validate_shots(project):
    shots = project.get('evidence_shots', [])
    if not isinstance(shots, list):
        raise ValueError('evidence_shots must be an ordered list')
    visuals = {v['id']: v for v in project['visuals']}
    ids = {r['id']: i for i,r in enumerate(project['lines'])}
    previous = None
    for shot in shots:
        if not isinstance(shot,dict) or shot.get('visual') not in visuals or shot.get('start_line_id') not in ids:
            raise ValueError('Each evidence shot needs a known visual and start_line_id')
        fraction = shot.get('start_fraction',0)
        if type(fraction) not in (int,float) or not math.isfinite(fraction) or not 0 <= fraction < 1:
            raise ValueError('Shot start_fraction must be in [0,1)')
        anchor = (ids[shot['start_line_id']],fraction)
        if previous is None and anchor != (0,0):
            raise ValueError('Evidence shots must begin with the first dialogue')
        if previous is not None and anchor <= previous:
            raise ValueError('Evidence shots must follow measured dialogue order')
        if shot.get('role','evidence') not in ('evidence','context'):
            raise ValueError('Shot role must be evidence or context')
        if not visuals[shot['visual']].get('source_url'):
            raise ValueError('Evidence shots require a source URL')
        previous = anchor
    return shots


def compile_shots(project, records, hidden=()):
    shots = validate_shots(project)
    if not shots:
        return []
    if not records or any(type(r.get(k)) not in (int,float) or not math.isfinite(r[k])
                          for r in records for k in ('start','end')):
        raise ValueError('Evidence shots need finite measured times')
    if any(not 0 <= r['start'] < r['end'] for r in records) or any(a['end']>b['start'] for a,b in zip(records,records[1:])):
        raise ValueError('Evidence shot dialogue must be ordered and non-overlapping')
    by_id = {r['id']:r for r in records}
    if set(by_id) != {r['id'] for r in project['lines']}:
        raise ValueError('Evidence shots require matching measured dialogue')
    visuals = {v['id']:v for v in project['visuals']}
    authored = {r['id']:r for r in project['lines']}
    timeline = []
    for shot in shots:
        row = by_id[shot['start_line_id']]
        start = row['start']+(row['end']-row['start'])*shot.get('start_fraction',0)
        timeline.append({**shot, 'start':start})
    for i,shot in enumerate(timeline):
        shot['end'] = timeline[i+1]['start'] if i+1<len(timeline) else records[-1]['end']
        minimum = 6 if shot.get('role') == 'context' else 8
        # Union hidden windows; an agenda/diagram does not count as screenshot reading time.
        visible = [(shot['start'],shot['end'])]
        for a,b in hidden:
            visible = [(lo,hi) for x,y in visible for lo,hi in ((x,min(y,a)),(max(x,b),y)) if lo<hi]
        longest = max((b-a for a,b in visible),default=0)
        if longest+1e-6 < minimum:
            raise ValueError(f"Screenshot {shot['visual']} needs {minimum}s of continuous visible time; got {longest:.2f}s")
        for row in records:
            if row['end']<=shot['start'] or row['start']>=shot['end']:
                continue
            line = authored[row['id']]
            if line.get('section') in ('intro','closing'):
                continue
            reference = visuals[line['visual']]
            if reference.get('source_url') != visuals[shot['visual']]['source_url']:
                raise ValueError('A held screenshot cannot cross into a different article')
        shot.update(visible_seconds=longest, minimum_seconds=minimum)
    return timeline


def shot_at(timeline, t):
    return next((s for s in timeline if s['start']<=t<s['end']),None)
