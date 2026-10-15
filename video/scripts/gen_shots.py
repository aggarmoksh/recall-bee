"""AI shot generation (Phase V2.5). Run from video/ with video/.venv.

  python scripts/gen_shots.py --selftest            no key needed: budget guard + argument picking
  python scripts/gen_shots.py --plan                discover text-to-video models and listed prices; spends nothing
  python scripts/gen_shots.py --generate [--model ENDPOINT_ID] [--shots AI2,AI3]
  python scripts/gen_shots.py --reroll AI4 [--model ENDPOINT_ID]    one re-roll with a new seed

Rules baked in: FAL_KEY comes from video/.env and is never printed or logged; AI1 is never touched; every call prints
the model's listed price and the running total first, and is refused if it would push the total past the budget;
writes go only to videos/ai/ and videos/_review/; cost, model and seed are logged to ai_log.jsonl.
"""
import argparse, json, os, pathlib, random, re, subprocess, sys, time, urllib.error, urllib.parse, urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
VIDEOS = ROOT.parent / 'videos'
AI_DIR = VIDEOS / 'ai'
REVIEW_DIR = VIDEOS / '_review'
LOG = ROOT / 'ai_log.jsonl'
SHOTS = json.loads((ROOT / 'ai_shots.json').read_text())
BUDGET = float(SHOTS['budget_usd'])
PROTECTED = {'AI1'}  # exists already; never regenerate or overwrite
API = 'https://api.fal.ai/v1'


class BudgetExceeded(RuntimeError):
    pass


def load_key():
    """Reads FAL_KEY from video/.env into the environment. Never prints it."""
    env = ROOT / '.env'
    if not env.exists():
        sys.exit('video/.env not found: create it with a FAL_KEY=... line (it is gitignored).')
    for line in env.read_text(encoding='utf-8').splitlines():
        m = re.match(r'\s*FAL_KEY\s*=\s*(.+?)\s*$', line)
        if m:
            os.environ['FAL_KEY'] = m.group(1).strip('"\'')
            return
    sys.exit('FAL_KEY not found in video/.env')


def spent():
    total = 0.0
    if LOG.exists():
        for line in LOG.read_text(encoding='utf-8').splitlines():
            if line.strip():
                total += float(json.loads(line).get('cost_usd', 0))
    return round(total, 4)


def check_budget(total, cost, budget=BUDGET):
    if total + cost > budget + 1e-9:
        raise BudgetExceeded(f'refusing: ${total:.2f} spent + ${cost:.2f} for this call would pass the ${budget:.2f} budget')


def api_get(url, auth=True):
    req = urllib.request.Request(url)
    if auth:
        req.add_header('Authorization', f"Key {os.environ['FAL_KEY']}")
    for wait in (0, 6, 15, 30, 60):
        time.sleep(wait)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
    raise RuntimeError('fal API rate limit (429) persisted; try again in a minute')


def listed_price(endpoint_id):
    """(unit_price, unit) as listed by fal's pricing API, or None if it isn't listed."""
    q = urllib.parse.urlencode({'endpoint_id': endpoint_id})
    data = api_get(f'{API}/models/pricing?{q}')
    prices = data.get('prices') or []
    if not prices:
        return None
    p = prices[0]
    return float(p['unit_price']), p.get('unit', '')


def estimate(price, seconds):
    unit_price, unit = price
    u = unit.lower()
    if 'second' in u:
        return unit_price * seconds
    if 'video' in u or 'request' in u or 'generation' in u:
        return unit_price
    raise ValueError(f'unsupported price unit {unit!r}: cannot estimate the cost safely')


def discover():
    """Candidate text-to-video endpoints, Veo 3.1 Lite first, then LTX."""
    found = []
    for q in ('veo3.1', 'ltx'):
        url = f"{API}/models?{urllib.parse.urlencode({'q': q, 'category': 'text-to-video', 'limit': 50})}"
        try:
            for m in api_get(url).get('models', []):
                eid = m.get('endpoint_id', '')
                if eid and eid not in [f[0] for f in found]:
                    found.append((eid, m))
        except Exception as e:  # discovery is best-effort; --model overrides it
            print(f'  discovery for {q!r} failed: {type(e).__name__}', file=sys.stderr)
    def rank(item):
        e = item[0].lower()
        return (0 if 'veo3.1' in e and 'lite' in e else 1 if 'ltx' in e else 2, e)
    return sorted(found, key=rank)


def input_props(endpoint_id):
    """Input properties of an endpoint from its public OpenAPI schema (empty dict if unavailable)."""
    try:
        spec = api_get('https://fal.ai/api/openapi/queue/openapi.json?' + urllib.parse.urlencode({'endpoint_id': endpoint_id}), auth=False)
    except Exception:
        return {}
    for name, schema in spec.get('components', {}).get('schemas', {}).items():
        if name.endswith('Input') and 'properties' in schema:
            return schema['properties']
    return {}


def _nums(values):
    out = []
    for v in values:
        m = re.search(r'\d+(\.\d+)?', str(v))
        if m:
            out.append((float(m.group(0)), v))
    return out


def pick_duration(props, want):
    p = props.get('duration')
    if not p:
        return None
    options = _nums(p['enum']) if 'enum' in p else []
    if not options:
        return want if p.get('type') in ('integer', 'number') else f'{want}s'
    ge = sorted(o for o in options if o[0] >= want)
    return (ge[0] if ge else sorted(options)[-1])[1]


def pick_resolution(props):
    """Lowest tier that is still >= 1080p: the listed price is the base tier's, higher tiers may cost more."""
    p = props.get('resolution')
    if not p or 'enum' not in p:
        return None
    options = _nums(p['enum'])
    ok = sorted(o for o in options if o[0] >= 1080)
    return ok[0][1] if ok else (sorted(options)[-1][1] if options else p['enum'][-1])


def build_args(props, prompt, want_seconds, seed):
    args = {'prompt': prompt}
    d = pick_duration(props, want_seconds)
    if d is not None:
        args['duration'] = d
    if 'aspect_ratio' in props:
        args['aspect_ratio'] = '16:9'
    r = pick_resolution(props)
    if r is not None:
        args['resolution'] = r
    if 'fps' in props and 24 in (props['fps'].get('enum') or []):
        args['fps'] = 24
    if 'generate_audio' in props:
        args['generate_audio'] = False
    if 'seed' in props:
        args['seed'] = seed
    return args


def full_prompt(shot):
    return f"{shot['prompt']} {SHOTS['suffix']}"


def contact_sheet(clip, out):
    """3 frames (start, middle, end) as one JPEG."""
    dur = float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=nw=1:nk=1', str(clip)],
                               capture_output=True, text=True, check=True).stdout.strip())
    frames = []
    for i, t in enumerate((0.1, dur / 2, max(dur - 0.2, 0.1))):
        f = REVIEW_DIR / f'_tmp_{clip.stem}_{i}.jpg'
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-ss', f'{t:.2f}', '-i', str(clip), '-frames:v', '1', '-vf', 'scale=640:-1', str(f)], check=True)
        frames.append(f)
    subprocess.run(['ffmpeg', '-y', '-v', 'error'] + sum([['-i', str(f)] for f in frames], []) +
                   ['-filter_complex', 'hstack=inputs=3', '-frames:v', '1', str(out)], check=True)
    for f in frames:
        f.unlink()


def generate_one(shot, endpoint, price, props, attempt):
    import fal_client
    sid = shot['id']
    if sid in PROTECTED or shot.get('skip'):
        raise SystemExit(f'{sid} is protected and is never generated')
    seed = random.randint(1, 2**31 - 1)
    args = build_args(props, full_prompt(shot), SHOTS['draft_seconds'], seed)
    secs = int(_nums([args['duration']])[0][0]) if 'duration' in args else SHOTS['draft_seconds']  # what the model bills, not what we asked for
    cost = estimate(price, secs)
    total = spent()
    print(f'{sid}: model={endpoint} listed price=${price[0]}/{price[1]}  clip={secs}s  estimate=${cost:.2f}  running total=${total:.2f} / ${BUDGET:.2f}')
    check_budget(total, cost)
    result = fal_client.subscribe(endpoint, arguments=args)
    url = (result.get('video') or {}).get('url')
    if not url:
        raise RuntimeError(f'{sid}: no video url in the response')
    finalize(sid, url, endpoint, args, secs, cost, result.get('seed', seed if 'seed' in args else None), attempt)


def finalize(sid, url, endpoint, args, secs, cost, seed, attempt):
    """Log the spend FIRST (it is billed the moment the generation succeeds), then download and make the contact sheet."""
    import httpx
    entry = {'ts': time.strftime('%Y-%m-%dT%H:%M:%S'), 'shot': sid, 'attempt': attempt, 'model': endpoint, 'seed': seed,
             'seed_note': None if 'seed' in args else 'model has no seed input', 'cost_usd': round(cost, 4), 'seconds_billed': secs,
             'args': {k: v for k, v in args.items() if k != 'prompt'}, 'file': f'{sid}_draft.mp4', 'url': url}
    with LOG.open('a', encoding='utf-8') as f:
        f.write(json.dumps(entry) + '\n')
    AI_DIR.mkdir(exist_ok=True)
    REVIEW_DIR.mkdir(exist_ok=True)
    dest = AI_DIR / f'{sid}_draft.mp4'
    if dest.exists():  # keep the rejected one for the record, never overwrite silently
        dest.rename(AI_DIR / f'{sid}_draft_rejected{attempt - 1}.mp4')
    with httpx.stream('GET', url, timeout=120, follow_redirects=True) as r:
        r.raise_for_status()
        with dest.open('wb') as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    contact_sheet(dest, REVIEW_DIR / f'ai_{sid}_draft.jpg')
    print(f'  saved {dest.name}; contact sheet videos/_review/ai_{sid}_draft.jpg; total now ${spent():.2f}')


def suitable(props):
    res = _nums(props.get('resolution', {}).get('enum', []))
    return 'prompt' in props and 'duration' in props and 'aspect_ratio' in props and any(r[0] >= 1080 for r in res)


def choose_model(forced):
    if forced:
        pr = listed_price(forced)
        if not pr:
            sys.exit(f'{forced}: no listed price, refusing')
        return forced, pr
    rows = []
    for eid, _meta in discover():
        if 'text-to-video' not in eid:
            continue
        try:
            pr = listed_price(eid)
            props = input_props(eid)
            if not pr or not suitable(props):
                continue
            secs = int(_nums([pick_duration(props, SHOTS['draft_seconds'])])[0][0])
            rows.append((estimate(pr, secs), eid, pr, secs))
        except Exception as e:
            print(f'  skipped {eid}: {type(e).__name__}', file=sys.stderr)
    if not rows:
        sys.exit('no suitable priced text-to-video candidate found: pass --model ENDPOINT_ID')
    rows.sort()
    for est, eid, pr, secs in rows:
        print(f'  candidate {eid}: ${pr[0]}/{pr[1]} -> ${est:.2f} per {secs} s clip')
    return rows[0][1], rows[0][2]


def selftest():
    check_budget(7.0, 0.5)
    for total, cost in ((7.9, 0.2), (8.0, 0.01), (0.0, 8.5)):
        try:
            check_budget(total, cost)
        except BudgetExceeded:
            continue
        raise AssertionError(f'budget guard let ${total} + ${cost} through')
    assert estimate((0.05, 'second'), 5) == 0.25 and estimate((0.4, 'video'), 5) == 0.4
    try:
        estimate((1, 'megapixel'), 5)
        raise AssertionError('unknown unit accepted')
    except ValueError:
        pass
    props = {'duration': {'enum': ['4s', '6s', '8s']}, 'resolution': {'enum': ['720p', '1080p']}, 'aspect_ratio': {}, 'generate_audio': {}, 'seed': {}}
    a = build_args(props, 'p', 5, 7)
    assert a == {'prompt': 'p', 'duration': '6s', 'aspect_ratio': '16:9', 'resolution': '1080p', 'generate_audio': False, 'seed': 7}, a
    assert pick_resolution({'resolution': {'enum': ['1080p', '1440p', '2160p']}}) == '1080p'
    assert build_args({'duration': {'enum': [6, 8]}, 'fps': {'enum': [24, 25]}}, 'p', 5, 1)['fps'] == 24
    assert suitable({'prompt': {}, 'duration': {}, 'aspect_ratio': {}, 'resolution': {'enum': ['1080p']}}) and not suitable({'prompt': {}})
    assert pick_duration({'duration': {'type': 'integer'}}, 5) == 5
    assert full_prompt(SHOTS['shots'][1]).endswith('no screens facing the camera')
    assert [s['id'] for s in SHOTS['shots']] == ['AI1', 'AI2', 'AI3', 'AI4', 'AI5', 'AI6'] and SHOTS['shots'][0]['skip']
    print('selftest ok')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--plan', action='store_true')
    ap.add_argument('--generate', action='store_true')
    ap.add_argument('--reroll')
    ap.add_argument('--model')
    ap.add_argument('--shots')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    load_key()
    if a.plan:
        print(f'spent so far: ${spent():.2f} of ${BUDGET:.2f}')
        eid, pr = choose_model(a.model)
        print(f'chosen: {eid}  ${pr[0]}/{pr[1]}  input fields: {sorted(input_props(eid))}')
        return
    eid, pr = choose_model(a.model)
    props = input_props(eid)
    todo = [s for s in SHOTS['shots'] if not s.get('skip')]
    if a.reroll:
        generate_one(next(s for s in todo if s['id'] == a.reroll), eid, pr, props, attempt=2)
    elif a.generate:
        ids = set(a.shots.split(',')) if a.shots else {s['id'] for s in todo}
        for s in todo:
            if s['id'] in ids:
                generate_one(s, eid, pr, props, attempt=1)
    else:
        ap.print_help()


if __name__ == '__main__':
    main()
