"""Align MuseScore page-SVG lyrics with score.mid notes.

usage: python3 align.py <score_dir> [--track N] [--ocr-dir DIR]
score_dir holds score_*.svg, score.mid, space.jsonp (from `msfetch unpack`).
Writes <score_dir>/aligned.json and prints a per-measure report.
"""
from collections import Counter
import re, sys, json, glob, os, subprocess, bisect, statistics
from collections import defaultdict
import mido
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geom import page_paths, page_factor, subpaths, glyphs

SCALE = 10 / 3  # space.jsonp units per SVG unit

# ---------------------------------------------------------------- MIDI
def load_midi(path):
    m = mido.MidiFile(path)
    tpb = m.ticks_per_beat
    tempos = []  # (tick, us_per_beat)
    for tr in m.tracks:
        t = 0
        for e in tr:
            t += e.time
            if e.type == 'set_tempo':
                tempos.append((t, e.tempo))
    tempos = sorted(set(tempos)) or [(0, 500000)]
    if tempos[0][0] != 0:
        tempos.insert(0, (0, 500000))
    # tick -> ms
    seg = []  # (tick, ms, us_per_beat)
    ms = 0.0
    for i, (t, us) in enumerate(tempos):
        if i:
            pt, pus = tempos[i - 1]
            ms += (t - pt) * pus / tpb / 1000
        seg.append((t, ms, us))
    ticks = [s[0] for s in seg]
    def t2ms(t):
        i = bisect.bisect_right(ticks, t) - 1
        st, sms, us = seg[i]
        return sms + (t - st) * us / tpb / 1000
    mss = [s[1] for s in seg]
    def ms2t(x):
        i = bisect.bisect_right(mss, x) - 1
        st, sms, us = seg[i]
        return st + (x - sms) * 1000 * tpb / us
    tracks = []
    meta = {'tpb': tpb, 'timesig': [], 'keysig': []}
    for tr in m.tracks:
        t = 0; on = {}; notes = []
        for e in tr:
            t += e.time
            if e.type == 'time_signature':
                meta['timesig'].append((t, e.numerator, e.denominator))
            if e.type == 'key_signature':
                meta['keysig'].append((t, e.key))
            if e.type == 'note_on' and e.velocity > 0:
                on.setdefault((e.channel, e.note), []).append(t)
            elif e.type in ('note_off', 'note_on'):
                k = (e.channel, e.note)
                if on.get(k):
                    s = on[k].pop(0)
                    notes.append({'tick': s, 'end': t, 'pitch': e.note, 'ch': e.channel})
        for ch in sorted(set(n['ch'] for n in notes)):
            ns = sorted([n for n in notes if n['ch'] == ch], key=lambda n: (n['tick'], n['pitch']))
            ons = defaultdict(int)
            for n in ns: ons[n['tick']] += 1
            poly = len(ns) / max(1, len(ons))
            tracks.append({'name': tr.name, 'ch': ch, 'notes': ns, 'poly': poly})
    tracks = [t for t in tracks if t['notes']]
    # a second channel inside one track whose notes are block chords is chord-symbol playback: drop it
    byname = defaultdict(list)
    for i, t in enumerate(tracks): byname[id(t) and t['name'] + str(i)]
    keep = []
    for t in tracks:
        same = [u for u in tracks if u['name'] == t['name'] and u is not t]
        if same and t['poly'] >= 2.5 and all(u['poly'] < t['poly'] for u in same):
            t['dropped'] = 'chord-symbol playback'
            continue
        keep.append(t)
    tracks = keep
    meta['timesig'] = sorted(set(meta['timesig']))
    meta['keysig'] = sorted(set(meta['keysig']))
    return tracks, meta, t2ms, ms2t

# ---------------------------------------------------------------- SVG
def staves_of(staffline_boxes):
    ys = sorted(set((round(b[1], 1), round(b[0]), round(b[2])) for b in staffline_boxes))
    lines = sorted(ys)
    staves = []; cur = []
    for y, x0, x1 in lines:
        if cur and (y - cur[-1][0] > 40 or len(cur) == 5 or abs(x0 - cur[-1][1]) > 5):
            staves.append(cur); cur = []
        cur.append((y, x0, x1))
    if cur: staves.append(cur)
    out = []
    for s in staves:
        sp = (s[-1][0] - s[0][0]) / max(1, len(s) - 1)
        out.append({'top': s[0][0], 'bot': s[-1][0], 'x0': s[0][1], 'x1': s[0][2], 'sp': sp, 'n': len(s)})
    return out

def subpaths_all(by, cls):
    return [b for d in by['_d_' + cls] for b in subpaths(d, with_nseg=True, f=by['_f'])]

def load_page(path):
    src = open(path).read()
    by = defaultdict(list)
    by['_f'] = page_factor(src)  # all geometry is normalised to 360 dpi, which SCALE and the thresholds assume
    for cls, d, attrs in page_paths(src):
        bxs = subpaths(d, f=by['_f'])
        if cls == 'LyricsLineSegment' or cls == 'StaffLines' or cls == 'TieSegment':
            by[cls].extend(bxs)
        else:
            by[cls].extend(bxs)
        by['_d_' + cls].append(d)
    return by, src

# ---------------------------------------------------------------- OCR
def ocr_syllables(svg_src, sylls, page_tag, cache_dir):
    """render each syllable box from the Lyrics layer and OCR it."""
    import cairosvg
    from PIL import Image
    head = re.match(r'<svg[^>]*>', svg_src).group(0)
    paths = ''.join(m.group(0) for m in re.finditer(r'<path class="Lyrics"[^>]*/>', svg_src))
    full = head + '<rect width="100%" height="100%" fill="#fff"/>' + paths + '</svg>'
    S = 2.0  # px per normalised (360 dpi) unit
    png = os.path.join(cache_dir, f'{page_tag}_lyr.png')
    if not os.path.exists(png):
        cairosvg.svg2png(bytestring=full.encode(), write_to=png, scale=S * page_factor(svg_src))
    im = Image.open(png).convert('L')
    # batch: paste syllables one per line into strips (<= ~8000 px tall), OCR each strip once
    pad = 30
    crops = []
    for s_ in sylls:
        x0, y0, x1, y1 = s_['box']
        crops.append(im.crop((int(x0 * S) - 6, int(y0 * S) - 6, int(x1 * S) + 6, int(y1 * S) + 6)))
    i0 = 0; part = 0
    while i0 < len(crops):
        rows_y = []; y = pad; i1 = i0
        while i1 < len(crops) and (y < 8000 or i1 == i0):
            rows_y.append((y, y + crops[i1].size[1])); y += crops[i1].size[1] + pad; i1 += 1
        W = max(c.size[0] for c in crops[i0:i1]) + 2 * pad
        strip = Image.new('L', (min(W, 30000), y), 255)
        for k in range(i0, i1):
            strip.paste(crops[k], (pad, rows_y[k - i0][0]))
        sp = os.path.join(cache_dir, f'{page_tag}_strip{part}.png'); strip.save(sp)
        tsv = subprocess.run(['tesseract', sp, '-', '--psm', '6', 'tsv'], capture_output=True, text=True).stdout
        found = defaultdict(list)
        for line in tsv.splitlines()[1:]:
            f = line.split('\t')
            if len(f) < 12 or f[0] != '5' or not f[11].strip():
                continue
            cy = int(f[7]) + int(f[9]) / 2
            for k, (ya, yb) in enumerate(rows_y):
                if ya - pad / 2 <= cy <= yb + pad / 2:
                    found[i0 + k].append((int(f[6]), f[11], float(f[10]))); break
        for k in range(i0, i1):
            ws = sorted(found.get(k, []))
            sylls[k]['text'] = ' '.join(w for _, w, _ in ws)
            sylls[k]['conf'] = min([c for _, _, c in ws], default=-1)
        i0 = i1; part += 1

# ---------------------------------------------------------------- main
def run(score_dir, track_hint=None, ocr=True):
    tracks, meta, t2ms, ms2t = load_midi(os.path.join(score_dir, 'score.mid'))
    space = json.load(open(os.path.join(score_dir, 'space.jsonp')))
    meas_box = {m['id']: m for m in space['space']}
    play = space['time']  # playback order: elid, ms
    pages = {}
    for f in sorted(glob.glob(os.path.join(score_dir, 'score_*.svg')), key=lambda p: int(re.search(r'_(\d+)\.svg', p).group(1))):
        pages[int(re.search(r'_(\d+)\.svg', f).group(1))] = load_page(f)

    # playback passes: list of (elid, start_ms, end_ms, pass_no)
    passes = []
    seen = defaultdict(int)
    total_ms = max(t2ms(max(n['end'] for n in t['notes'])) for t in tracks)
    for i, p in enumerate(play):
        end = play[i + 1]['position'] if i + 1 < len(play) else total_ms + 1
        passes.append({'elid': p['elid'], 'ms0': p['position'], 'ms1': end, 'pass': seen[p['elid']], 'last': i + 1 == len(play)})
        seen[p['elid']] += 1

    report = []
    events = []  # aligned output: per vocal note
    cache = os.path.join(score_dir, '_ocr'); os.makedirs(cache, exist_ok=True)

    # ---- per page geometry
    pg = {}
    for pno, (by, src) in pages.items():
        staves = staves_of(by['StaffLines'])
        heads = glyphs([b for b in by['Note']])
        lyr = glyphs(by['Lyrics'])
        pg[pno] = {'rests': glyphs(by['Rest']), 'staves': staves, 'heads': heads, 'lyr': lyr, 'lines': by['LyricsLineSegment'],
                   'ties': by['TieSegment'], 'src': src}

    # ---- systems: measures sharing page & y
    sysmap = defaultdict(list)
    for mid, b in meas_box.items():
        sysmap[(b['page'], round(b['y']))].append(mid)

    # ---- syllable segmentation per page (lines of text under staves)
    from geom import signature
    for pno, (by, src) in pages.items():
        g = pg[pno]
        gl = glyphs([b for b in subpaths_all(by, 'Lyrics')], keep_parts=True)
        heights = [x['bb'][3] - x['bb'][1] for x in gl] or [30]
        em = statistics.median(heights)
        # text rows: find baselines (most common glyph bottoms), then give each glyph to the baseline
        # whose x-height band [b - 0.7em, b] it overlaps most (robust to descenders/apostrophes/stacked verses)
        # baselines = bottoms shared by many full-size letters (skip small marks: apostrophes, commas, dots)
        letters = [x['bb'][3] for x in gl if x['bb'][3] - x['bb'][1] >= 0.6 * em]
        dens = sorted(((sum(1 for y2 in letters if abs(y2 - y) <= 2), y) for y in letters), reverse=True)
        peaks = []
        for c, y in dens:
            if c < 3: break
            if all(abs(y - p_) > em * 0.6 for p_ in peaks): peaks.append(y)
        peaks.sort()
        rows = [{'g': [], 'b': p_} for p_ in peaks]
        for x in gl:
            if not rows:
                rows.append({'g': [], 'b': x['bb'][3]})
            def ov(rw):
                lo, hi = rw['b'] - em * 0.7, rw['b']
                return min(hi, x['bb'][3]) - max(lo, x['bb'][1])
            best_r = max(rows, key=ov)
            if ov(best_r) <= 0:
                best_r = min(rows, key=lambda rw: abs(rw['b'] - x['bb'][3]))
            best_r['g'].append(x)
        rows = [rw for rw in rows if rw['g']]
        g['sylls'] = []
        for r in rows:
            gs = sorted(r['g'], key=lambda x: x['bb'][0])
            base = statistics.median([x['bb'][3] for x in gs])
            cur = [gs[0]]
            groups = []
            for a, b in zip(gs, gs[1:]):
                if b['bb'][0] - a['bb'][2] > em * 0.32:
                    groups.append(cur); cur = []
                cur.append(b)
            groups.append(cur)
            for grp in groups:
                box = (min(x['bb'][0] for x in grp), min(x['bb'][1] for x in grp), max(x['bb'][2] for x in grp), max(x['bb'][3] for x in grp))
                g['sylls'].append({'box': box, 'cx': (box[0] + box[2]) / 2, 'page': pno, 'base': base,
                                   'sigs': [signature(x, base) for x in grp], 'gb': [x['bb'] for x in grp]})
        if ocr:
            ocr_syllables(src, g['sylls'], f'p{pno}', cache)
        g['em'] = em
    # ---- glyph-signature voting: identical outlines are identical letters
    votes = defaultdict(lambda: defaultdict(int))
    for g in pg.values():
        for s in g['sylls']:
            t = s.get('text', '').replace(' ', '')
            if len(t) == len(s['sigs']):
                for sig, ch in zip(s['sigs'], t):
                    votes[sig][ch] += 1
    best = {sig: max(v.items(), key=lambda kv: kv[1])[0] for sig, v in votes.items()}
    nfix = 0; nunk = 0
    for g in pg.values():
        for s in g['sylls']:
            raw = s.get('text', '')
            if all(sig in best for sig in s['sigs']):
                s['text_ocr'] = raw
                s['text'] = ''.join(best[sig] for sig in s['sigs'])
                nfix += s['text'] != raw.replace(' ', '')
            else:
                nunk += 1
    r_best = best
    stats = {'sig_classes': len(best), 'syll_changed_by_vote': nfix, 'syll_unresolved': nunk}
    r_out = locals(); r_out['best'] = best
    return r_out

if __name__ == '__main__':
    r = run(sys.argv[1])


# ================================================================ alignment
STEP = {0: (0,), 1: (0, 1), 2: (1,), 3: (1, 2), 4: (2,), 5: (3,), 6: (3, 4), 7: (4,), 8: (4, 5), 9: (5,), 10: (5, 6), 11: (6,)}
def dia(pitch):
    """possible diatonic indices (C0=0) for a MIDI pitch (either enharmonic spelling)"""
    o = pitch // 12 - 1
    out = set(o * 7 + s for s in STEP[pitch % 12])
    if pitch % 12 == 0: out.add((o - 1) * 7 + 6)   # B#
    if pitch % 12 == 11: out.add((o + 1) * 7 + 0)  # Cb
    if pitch % 12 == 4: out.add(o * 7 + 3)         # Fb
    if pitch % 12 == 5: out.add(o * 7 + 2)         # E#
    return out
CLEFS = {'treble': 4 * 7 + 2, 'bass': 2 * 7 + 4, 'treble8vb': 3 * 7 + 2, 'alto': 3 * 7 + 3, 'tenor': 3 * 7 + 1, 'treble8va': 5 * 7 + 2}

def dp_align(A, B, cost_match, cost_skipA, cost_skipB):
    n, m = len(A), len(B)
    INF = 1e18
    D = [[INF] * (m + 1) for _ in range(n + 1)]; P = [[None] * (m + 1) for _ in range(n + 1)]
    D[0][0] = 0
    for i in range(n + 1):
        for j in range(m + 1):
            d = D[i][j]
            if d == INF: continue
            if i < n and j < m:
                c = d + cost_match(A[i], B[j])
                if c < D[i + 1][j + 1]: D[i + 1][j + 1] = c; P[i + 1][j + 1] = (i, j, 'm')
            if i < n:
                c = d + cost_skipA(A[i])
                if c < D[i + 1][j]: D[i + 1][j] = c; P[i + 1][j] = (i, j, 'a')
            if j < m:
                c = d + cost_skipB(B[j])
                if c < D[i][j + 1]: D[i][j + 1] = c; P[i][j + 1] = (i, j, 'b')
    pairs = []; i, j = n, m
    while (i, j) != (0, 0):
        pi, pj, op = P[i][j]
        if op == 'm': pairs.append((pi, pj))
        i, j = pi, pj
    return D[n][m], pairs[::-1]

def heads_for(g, mx0, mx1, my0, my1, st):
    sp0 = st[0]['sp'] if st else 20
    hs = [h for h in g['heads'] if mx0 - 5 <= (h[0] + h[2]) / 2 <= mx1 + 5 and my0 - sp0 * 7 <= (h[1] + h[3]) / 2 <= my1 + sp0 * 7]
    own = defaultdict(list)
    for h in hs:
        cy = (h[1] + h[3]) / 2
        k = min(range(len(st)), key=lambda i: 0 if st[i]['top'] <= cy <= st[i]['bot'] else min(abs(cy - st[i]['top']), abs(cy - st[i]['bot'])))
        s = st[k]
        pos = round((s['bot'] - cy) / (s['sp'] / 2))
        w = h[2] - h[0]
        own[k].append({'x': (h[0] + h[2]) / 2, 'x0': h[0], 'x1': h[2], 'y': cy, 'pos': pos, 'w': w})
    for k in own:
        own[k].sort(key=lambda h: (round(h['x'] / 4), -h['y']))
    return own

def mark_ties(g, heads, sp):
    for h in heads:
        h['cont'] = any(abs(t[2] - h['x0']) < sp * 2.2 and t[1] - sp * 1.5 < h['y'] < t[3] + sp * 1.5 and t[2] - t[0] > sp * 0.8 for t in g['ties'])

def align(r):
    pg, passes, meas_box, tracks, meta = r['pg'], r['passes'], r['meas_box'], r['tracks'], r['meta']
    ms2t = r['ms2t']
    allon = sorted(set(n['tick'] for t in tracks for n in t['notes']))
    def snap(t):
        i = bisect.bisect_left(allon, t)
        c = [allon[j] for j in (i - 1, i) if 0 <= j < len(allon)]
        b = min(c, key=lambda x: abs(x - t)) if c else t
        return b if abs(b - t) <= 3 else t
    for p in passes:
        p['t0'] = snap(round(ms2t(p['ms0']))); p['t1'] = snap(round(ms2t(p['ms1'])))
        if p.get('last'):
            ts = [x for x in meta['timesig'] if x[0] <= p['t0']] or [(0, 4, 4)]
            p['t1'] = p['t0'] + meta['tpb'] * 4 * ts[-1][1] // ts[-1][2]
    meas = []
    for mid, b in sorted(meas_box.items()):
        pno = b['page']
        if pno not in pg: continue
        g = pg[pno]
        mx0, my0 = b['x'] / SCALE, b['y'] / SCALE
        mx1, my1 = mx0 + b['sx'] / SCALE, my0 + b['sy'] / SCALE
        st = sorted([s for s in g['staves'] if s['top'] >= my0 - 5 and s['bot'] <= my1 + 5], key=lambda s: s['top'])
        own = heads_for(g, mx0, mx1, my0, my1, st)
        for k in own: mark_ties(g, own[k], st[k]['sp'])
        mp = [p for p in passes if p['elid'] == mid]
        meas.append({'id': mid, 'page': pno, 'box': (mx0, my0, mx1, my1), 'st': st, 'own': own, 'passes': mp})
    # staff k <-> stream k when every system shows all staves; else note it
    nst = Counter(len(m['st']) for m in meas)
    full = max(nst)
    # choose clef per staff index by minimum DP cost over the piece (first pass)
    def mnotes(t, p):
        return [n for n in t['notes'] if p['t0'] <= n['tick'] < p['t1']]
    def cost_for(k, ti, clef):
        tot = 0
        for m in meas:
            if len(m['st']) != full or not m['passes']: continue
            H = [h for h in m['own'].get(k, []) if not h['cont']]
            N = mnotes(tracks[ti], m['passes'][0])
            c, _ = dp_align(H, N, lambda h, n: 0 if CLEFS[clef] + h['pos'] in dia(n['pitch']) else 2, lambda h: 1, lambda n: 1)
            tot += c
        return tot
    staff_map = {}
    for k in range(full):
        best = min(((cost_for(k, ti, cl), ti, cl) for ti in range(len(tracks)) for cl in ('treble', 'bass', 'treble8vb')), key=lambda x: x[0])
        staff_map[k] = {'track': best[1], 'clef': best[2], 'cost': best[0]}
    return {'meas': meas, 'staff_map': staff_map, 'full': full}

def split_syl(s_, cols, best):
    """a 'syllable' box spanning >=2 note columns was two words set tight: split at the widest inner gap"""
    inside = [c for c in cols if s_['box'][0] + 2 < c['x'] < s_['box'][2] - 2]
    gb = s_['gb']
    if len(inside) < 2 or len(gb) < 2:
        return [s_]
    gaps = [(gb[i + 1][0] - gb[i][2], i) for i in range(len(gb) - 1)]
    g, i = max(gaps)
    if g <= 0:
        return [s_]
    txt = s_['text'].replace(' ', '')
    out = []
    for lo, hi in ((0, i + 1), (i + 1, len(gb))):
        sub = dict(s_)
        sub['gb'] = gb[lo:hi]; sub['sigs'] = s_['sigs'][lo:hi]
        sub['box'] = (min(b[0] for b in sub['gb']), min(b[1] for b in sub['gb']), max(b[2] for b in sub['gb']), max(b[3] for b in sub['gb']))
        sub['cx'] = (sub['box'][0] + sub['box'][2]) / 2
        if all(sg in best for sg in sub['sigs']):
            sub['text'] = ''.join(best[sg] for sg in sub['sigs'])
        elif len(txt) == len(gb):
            sub['text'] = txt[lo:hi]
        sub['split'] = True; sub['orig'] = s_.get('orig', id(s_))
        out += split_syl(sub, cols, best)
    return out

def lyrics_pass(r, a):
    pg, tracks = r['pg'], r['tracks']
    meas, smap = a['meas'], a['staff_map']
    events = defaultdict(dict)   # (track) -> tick -> {'syl':..., 'verse':...}
    rep = []
    used = set()
    for m in meas:
        g = pg[m['page']]
        mx0, my0, mx1, my1 = m['box']
        st = m['st']
        for k, s in enumerate(st):
            below_top = s['bot'] + s['sp'] * 1.5
            below_bot = st[k + 1]['top'] - s['sp'] * 0.5 if k + 1 < len(st) else s['bot'] + s['sp'] * 14
            sy = [x for x in g['sylls'] if below_top < x['base'] < below_bot and mx0 - 2 <= x['cx'] < mx1 - 2]
            if not sy: continue
            # verse rows within this staff region (whole system width, so rows are stable across measures)
            sysrows = sorted(set(round(x['base']) for x in g['sylls'] if below_top < x['base'] < below_bot))
            rowsets = []
            for b in sysrows:
                if rowsets and b - rowsets[-1][-1] < 8: rowsets[-1].append(b)
                else: rowsets.append([b])
            def vidx(x):
                return next(i for i, rs in enumerate(rowsets) if any(abs(round(x['base']) - b) <= 1 for b in rs))
            sm = smap.get(k) or {}
            if not sm: continue
            ti, clef = sm['track'], sm['clef']
            heads = [h for h in m['own'].get(k, []) if not h['cont']]
            for p in m['passes']:
                N = [n for n in tracks[ti]['notes'] if p['t0'] <= n['tick'] < p['t1']]
                _, hp = dp_align(heads, N, lambda h, n: 0 if CLEFS[clef] + h['pos'] in dia(n['pitch']) else 2, lambda h: 1, lambda n: 1)
                h2n = {i: N[j] for i, j in hp}
                for i, n in h2n.items():
                    hx = events.setdefault('_hx', {})
                    hx[(ti, n['tick'])] = {'page': m['page'], 'x': heads[i]['x'], 'top': s['top'], 'bot': s['bot'], 'sp': s['sp'], 'mx1': mx1}
                nverse = len(rowsets)
                npass = len(m['passes'])
                if npass > 1:   # repeats: verse k on pass k; surplus stacked verses ride on the last pass
                    vs = [p['pass']] if p['pass'] < npass - 1 else list(range(p['pass'], nverse))
                    vs = [v for v in vs if v < nverse] or ([0] if nverse == 1 else [])
                else:
                    vs = list(range(nverse))
                cols = []
                for i, h in enumerate(heads):
                    if i not in h2n: continue
                    if cols and abs(cols[-1]['x'] - h['x']) < h['w'] * 0.6:
                        cols[-1]['notes'].append(h2n[i]); continue
                    cols.append({'x': h['x'], 'w': h['w'], 'notes': [h2n[i]]})
                def c_match(sx, col):
                    left = sx['box'][0] + col['w'] / 2
                    return min(abs(sx['cx'] - col['x']), abs(left - col['x'])) / col['w']
                got = set(); far = 0; syl = []
                for v in vs:
                    sv = sorted([y for x in sy if vidx(x) == v for y in split_syl(x, cols, r['best'])], key=lambda x: x['cx'])
                    syl += sv
                    cost, sp_ = dp_align(sv, cols, c_match, lambda sx: 50, lambda col: 0)
                    for i, j in sp_:
                        n = max(cols[j]['notes'], key=lambda n: n['pitch'])
                        events[ti].setdefault(n['tick'], {})[v] = {'syl': sv[i], 'dist': c_match(sv[i], cols[j])}
                        used.add(sv[i].get('orig', id(sv[i]))); got.add(id(sv[i]))
                        far += c_match(sv[i], cols[j]) > 1.5
                rep.append({'m': m['id'], 'pass': p['pass'], 'staff': k, 'heads': len(heads), 'notes': len(N),
                            'matched': len(hp), 'syl': len(syl), 'syl_assigned': len(got), 'far': far, 'verses': len(vs)})
    allsyl = [x for g in pg.values() for x in g['sylls']]
    return events, rep, [x for x in allsyl if id(x) not in used]

# ================================================================ ABC output
from fractions import Fraction
KEYS = {'C': 0, 'G': 1, 'D': 2, 'A': 3, 'E': 4, 'B': 5, 'F#': 6, 'C#': 7, 'F': -1, 'Bb': -2, 'Eb': -3, 'Ab': -4, 'Db': -5, 'Gb': -6, 'Cb': -7}
SHARPS = 'FCGDAEB'
LET = 'CDEFGAB'; NAT = [0, 2, 4, 5, 7, 9, 11]
def keymap(key):
    minor = key.endswith('m'); k = key[:-1] if minor else key
    rel = {'Am': 'C', 'Em': 'G', 'Bm': 'D', 'F#m': 'A', 'C#m': 'E', 'G#m': 'B', 'Dm': 'F', 'Gm': 'Bb', 'Cm': 'Eb', 'Fm': 'Ab', 'Bbm': 'Db', 'Ebm': 'Gb'}
    n = KEYS[rel[key]] if minor else KEYS[k]
    acc = {l: 0 for l in LET}
    for l in (SHARPS[:n] if n > 0 else SHARPS[::-1][:-n]):
        acc[l] = 1 if n > 0 else -1
    return acc, n
def spell(pitch, kacc, nsharps):
    pc = pitch % 12
    for i, l in enumerate(LET):
        if (NAT[i] + kacc[l]) % 12 == pc:
            return l, kacc[l], pitch // 12 - 1 + (1 if NAT[i] + kacc[l] < 0 else 0) - (1 if NAT[i] + kacc[l] >= 12 else 0)
    # chromatic: sharps in sharp keys, flats otherwise
    if nsharps > 0:
        i = [j for j in range(7) if NAT[j] == (pc - 1) % 12]
        if i: return LET[i[0]], 1, (pitch - 1) // 12 - 1
    i = [j for j in range(7) if NAT[j] == (pc + 1) % 12]
    if i: return LET[i[0]], -1, (pitch + 1) // 12 - 1
    i = [j for j in range(7) if NAT[j] == pc]
    return LET[i[0]], 0, pitch // 12 - 1
def abcpitch(l, octv):
    if octv >= 5: return l.lower() + "'" * (octv - 5)
    return l + ',' * (4 - octv)
def abclen(fr):
    if fr == 1: return ''
    if fr.denominator == 1: return str(fr.numerator)
    if fr.numerator == 1: return '/' + (str(fr.denominator) if fr.denominator != 2 else '')
    return f'{fr.numerator}/{fr.denominator}'

REPR = sorted({Fraction(2) ** k for k in range(-4, 5)} | {Fraction(3, 2) * Fraction(2) ** k for k in range(-4, 4)}, reverse=True)
def pieces(fr):
    out = []
    while fr > 0:
        p = next((x for x in REPR if x <= fr), None)
        if p is None: out.append(fr); break
        out.append(p); fr -= p
    return out

def to_abc(r, a, ev, title='Score', ti=0, words_dashes=None):
    from wordfreq import zipf_frequency as zf
    tracks, meta, passes = r['tracks'], r['meta'], r['passes']
    tpb = meta['tpb']; unit = tpb // 2  # L:1/8
    notes = tracks[ti]['notes']
    lyrall = {t: d for t, d in ev.get(ti, {}).items()}
    nv = 1 + max([v for d in lyrall.values() for v in d] or [0])
    join = {}   # (verse, tick) -> 'dash' | 'dict'
    for v in range(nv):
        seq = [(t, lyrall[t][v]['syl']) for t in sorted(lyrall) if v in lyrall[t]]
        for (t1, s1), (t2, s2) in zip(seq, seq[1:]):
            a_, b_ = s1['text'], s2['text']
            if not a_ or not b_ or not a_[-1].isalpha() or not b_[0].isalpha():
                continue
            emx = r['pg'][s1['page']]['em']
            dash = s1['page'] == s2['page'] and any(
                abs(l[1] - l[3]) < 4 and s1['box'][2] - 2 <= l[0] and l[2] <= s2['box'][0] + 2
                and abs(l[1] - (s1['base'] - 0.45 * emx)) < 0.2 * emx for l in r['pg'][s1['page']]['lines'])
            wa, wb = a_.lower().strip('(),.!?'), b_.lower().strip('(),.!?')
            whole = zf(wa + wb, 'en')
            if dash or (whole >= 3.0 and whole >= min(zf(wa, 'en'), zf(wb, 'en'))):
                join[(v, t1)] = 'dash' if dash else 'dict'
    hx = ev.get('_hx', {})
    def rest_after(n, nn):
        a_ = hx.get((ti, n['tick']))
        if not a_: return n['end'] < (nn['tick'] if nn else 0) - unit  # unknown: keep long gaps
        b_x = hx.get((ti, nn['tick']), {}).get('x') if nn else None
        if b_x is None or (nn and hx[(ti, nn['tick'])]['page'] != a_['page']) or b_x < a_['x']:
            b_x = a_['mx1']
        for rb in r['pg'][a_['page']]['rests']:
            cx, cy = (rb[0] + rb[2]) / 2, (rb[1] + rb[3]) / 2
            if a_['x'] + 4 < cx < b_x - 4 and a_['top'] - a_['sp'] * 2 < cy < a_['bot'] + a_['sp'] * 2:
                return True
        return False
    ks = meta['keysig'] or [(0, 'C')]
    tss = meta['timesig'] or [(0, 4, 4)]
    def at(lst, t):
        x = lst[0]
        for y in lst:
            if y[0] <= t: x = y
        return x
    out = [f'X:1', f'T:{title}', 'L:1/8']
    ts0 = at(tss, 0); out.append(f'M:{ts0[1]}/{ts0[2]}')
    out.append(f'K:{at(ks, 0)[1]}')
    bars = []  # (abc string, [lyric tokens])
    cur_key, cur_ts = at(ks, 0)[1], (ts0[1], ts0[2])
    for p in passes:
        t0, t1 = p['t0'], p['t1']
        pre = ''
        k = at(ks, t0)[1]
        if k != cur_key: pre += f'[K:{k}] '; cur_key = k
        ts = at(tss, t0)[1:]
        if ts != cur_ts: pre += f'[M:{ts[0]}/{ts[1]}] '; cur_ts = ts
        kacc, nsh = keymap(cur_key)
        state = {}
        toks, ly = [], [[] for _ in range(nv)]
        t = t0
        # notes overlapping this bar (monophonic top line)
        ns = [n for n in notes if n['tick'] < t1 and n['end'] > t0]
        # keep highest note per onset
        by = {}
        for n in ns: by[n['tick']] = max(by.get(n['tick'], n), n, key=lambda q: q['pitch'])
        ons = sorted(by.values(), key=lambda n: n['tick'])
        for i, n in enumerate(ons):
            s = max(n['tick'], t0)
            nxt = ons[i + 1]['tick'] if i + 1 < len(ons) else t1
            e = min(n['end'], t1, nxt)
            if n['end'] < nxt and not rest_after(n, ons[i + 1] if i + 1 < len(ons) else None):
                e = nxt                                   # performance gate: no notated rest in between
            elif n['end'] < nxt:
                q = unit // 2                              # notated rest follows: round note up to a 16th grid
                e = min(nxt, -(-(n['end'] - s) // q) * q + s)
            if e <= s:
                continue
            if s > t:
                toks += ['z' + abclen(x) for x in pieces(Fraction(s - t, unit))]
            l, acc, octv = spell(n['pitch'], kacc, nsh)
            key = (l, octv)
            want = acc
            have = state.get(key, kacc[l])
            mark = '' if want == have else {1: '^', -1: '_', 0: '=', 2: '^^', -2: '__'}[want]
            state[key] = want
            tie = '-' if (n['end'] > t1 and e == t1) else ''
            ps = pieces(Fraction(e - s, unit))
            toks.append('-'.join(mark * (i == 0) + abcpitch(l, octv) + abclen(x) for i, x in enumerate(ps)) + tie)
            extra = len(ps) - 1
            if n['tick'] < t0:
                for v in range(nv): ly[v].append('_')   # tied over the barline (abcjs gives tied notes a slot)
            else:
                for L in range(nv):              # unrolled output: pass k sings verse k on line 1
                    v = L + p['pass']
                    d = lyrall.get(n['tick'], {})
                    if v in d:
                        tx = d[v]['syl']['text'].replace(' ', '~') or '?'
                        ly[L].append(tx + ('-' if (v, n['tick']) in join else ''))
                    else:
                        ly[L].append('*')
            for v in range(nv): ly[v].extend(['_'] * extra)
            t = e
        if t < t1:
            toks += ['z' + abclen(x) for x in pieces(Fraction(t1 - t, unit))]
        bars.append((pre + ' '.join(toks), ly, p['elid']))
    # 4 bars per line
    for i in range(0, len(bars), 4):
        chunk = bars[i:i + 4]
        out.append(f'%m{chunk[0][2] + 1}\n' + ' | '.join(b[0] for b in chunk) + ' |')
        for v in range(nv):
            if any(x not in ('*', '_') for b in chunk for x in b[1][v]):
                out.append('w: ' + ' | '.join(' '.join(b[1][v]) for b in chunk).replace('- ', '-'))
    return '\n'.join(out) + '\n'
