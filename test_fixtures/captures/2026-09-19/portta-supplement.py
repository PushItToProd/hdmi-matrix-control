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
initial=request('/status'); save('supplement-initial-status',initial)
try:
    # Both commands change a linked input, and each repeat starts identically.
    for gap in (0.0, 0.1):
        for repeat in range(1,4):
            capture(f'06-setup-a-{gap}-{repeat}',['spoasi01'])
            capture(f'06-setup-b-{gap}-{repeat}',['spobsi04'])
            before=request('/status')
            assert before['outputs']['A']['input']==1 and before['outputs']['B']['input']==4,before
            name=f'06-linked-swap-{gap}-{repeat}'
            capture(name,['spoasi04','spobsi01'],gap=gap)
            after=request('/status');save(name+'-status',dict(before=before,after=after))
            print('APPLIED '+name+' '+json.dumps(after['outputs']),flush=True)
    # Repeat the minimum planned delay in each direction under single-input A.
    for repeat in range(1,5):
        before=request('/status'); target=5-before['outputs']['B']['input']
        assert target in (1,4)
        name=f'07-settle-100ms-{repeat}'
        capture(name,[f'spobsi0{target}','sta'],gap=.1)
        after=request('/status');save(name+'-status',dict(before=before,after=after,target=target))
        assert after['outputs']['B']['input']==target,after
finally:
    capture('supplement-restore-a',['spoasi01'])
    capture('supplement-restore-b',['spobsi04'])
    final=request('/status'); save('supplement-restored-status',final)
    assert final['outputs']==initial['outputs'],final
    print('RESTORED '+json.dumps(final),flush=True)
