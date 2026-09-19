import base64, datetime, json, pathlib, re, time, urllib.request
ROOT = pathlib.Path('/home/joe/Code/projects/portta-multiviewer-control/test_fixtures/captures/2026-09-19')
BASE = 'http://gmktec.zane.network:11344'
def request(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=40) as r: return json.load(r)
def save(name, data):
    (ROOT / (name + '.json')).write_text(json.dumps(data, indent=2) + '\n')
def capture(name, commands, gap=.1, read_seconds=2):
    body = dict(commands=commands, gap=gap, read_seconds=read_seconds)
    result = request('/debug/raw-command', body)
    save(name, dict(recorded_at=datetime.datetime.now(datetime.timezone.utc).isoformat(), request=body, **result))
    raw = b''.join(base64.b64decode(e['base64']) for e in result['events'] if e['kind']=='read')
    (ROOT / (name + '.bin')).write_bytes(raw)
    summary=[]
    for e in result['events']:
        if e['kind']=='write': summary.append(f"{e['at_ms']}ms write {e['command']}")
        else:
            text=base64.b64decode(e['base64']).decode('ascii',errors='replace')
            summary.append(f"{e['at_ms']}ms read {e['bytes']}B echoes={re.findall(r'<s>(.*?)</s>',text)} " + ' '.join(re.findall(r'-- Output [AB] Video Mode:[^\r\n]*',text)))
    print(name+': '+ ' | '.join(summary), flush=True)
    return raw
initial=request('/status'); save('initial-status',initial)
assert initial['outputs']['A']['mode']=='single' and initial['outputs']['A']['input']==1
assert initial['outputs']['B']['input']==4 and not initial['outputs']['B']['copy_a']
assert all(initial['inputs'][i]['linked'] for i in ('1','4'))
try:
    capture('01-swap-100ms',['spoasi02','spobsi04'])
    capture('02-swap-zero-gap',['spoasi01','spobsi02'],gap=0)
    capture('03-command-then-status',['spobsi01','sta'],read_seconds=3)
    capture('04-mode-and-b',['spoa2plr14','spobsi01'])
    capture('05-setup-b4',['spobsi04'])
    before=request('/status'); save('05-before-status',before)
    assert before['outputs']['B']['input']==4
    target=1
    for delay in (.10,.15,.25,.40,.60,1.00):
        before=request('/status')
        assert before['outputs']['B']['input']!=target, before
        assert all(before['inputs'][i]['linked'] for i in ('1','4')), before
        raw=capture(f'05-gap-{delay:.2f}',[f'spobsi0{target}','sta'],gap=delay)
        after=request('/status')
        save(f'05-gap-{delay:.2f}-status',dict(before=before,after=after,target=target))
        assert after['outputs']['B']['input']==target,after
        target=5-target
finally:
    capture('restore-a',['spoasi01'])
    capture('restore-b',['spobsi04'])
    final=request('/status'); save('restored-status',final)
    print('RESTORED '+json.dumps(final),flush=True)
    assert final['outputs']==initial['outputs'],final
