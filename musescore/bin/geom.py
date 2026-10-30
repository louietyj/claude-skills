import re
from svgelements import Path
def page_paths(src):
    for m in re.finditer(r'<path class="([A-Za-z]+)"([^>]*)/>',src):
        cls=m.group(1); attrs=m.group(2)
        d=re.search(r' d="([^"]*)"',attrs).group(1)
        yield cls, d, attrs
def page_factor(src):
    """factor to 360 dpi: newer exports are 360 dpi already, older ones 72 dpi (width in pt)."""
    w=float(re.search(r'<svg[^>]* width="([\d.]+)"',src).group(1))
    return 5.0 if w<1500 else 1.0
def subpaths(d, with_nseg=False, f=1.0):
    p=Path(d); out=[]
    if f!=1.0: p=Path(p*f'scale({f})'); p.reify()
    for sp in p.as_subpaths():
        sp=Path(sp)
        bb=sp.bbox()
        if not bb: continue
        if not with_nseg: out.append(bb); continue
        # Shape key: segment endpoints at fixed fractions of the segment list. Path.point(t) would be
        # arc-length based and costs minutes per page computing curve lengths.
        n=len(sp); pts=[sp[min(n-1,int(t*n))].end for t in (0.0,0.25,0.5,0.75)]
        out.append((bb[0],bb[1],bb[2],bb[3],(n,)+tuple(round(c) for p in pts for c in (p[0]-bb[0],p[1]-bb[1]))))
    return out
def glyphs(boxes, keep_parts=False):
    """merge subpaths contained in another's x-range (holes, dots) into glyphs.
    boxes may carry a 5th element (segment count)."""
    boxes=sorted(boxes,key=lambda b:(b[0], -(b[2]-b[0])))
    out=[]
    for b in boxes:
        for g in out:
            gb=g['bb']
            near=lambda a,c: max(a[1],c[1])-min(a[3],c[3]) < 0.35*max(a[3]-a[1],c[3]-c[1])
            if (b[0]>=gb[0]-1 and b[2]<=gb[2]+1 and near(b,gb)) or (gb[0]>=b[0]-1 and gb[2]<=b[2]+1 and near(b,gb)):
                g['bb']=[min(gb[0],b[0]),min(gb[1],b[1]),max(gb[2],b[2]),max(gb[3],b[3])]; g['parts'].append(b); break
        else: out.append({'bb':[b[0],b[1],b[2],b[3]],'parts':[b]})
    if keep_parts: return out
    return [g['bb'] for g in out]
def signature(g, baseline):
    """shape key for a glyph: parts' sizes, segment counts, offsets; plus vertical position vs baseline."""
    x0,y0=g['bb'][0],g['bb'][1]
    parts=sorted((round(p[2]-p[0]),round(p[3]-p[1]),p[4] if len(p)>4 else 0,round(p[0]-x0),round(p[1]-y0)) for p in g['parts'])
    return (tuple(parts), round((g['bb'][3]-baseline)/4))
