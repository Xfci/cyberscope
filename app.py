#!/usr/bin/env python3
"""
CYBERSCOPE — Flask Web Interface
Run: python3 app.py
Open: http://localhost:8000
"""

import sys, re, os, io, threading, queue, time, base64, json
import urllib.request, urllib.parse
from typing import Optional
from urllib.parse import urlparse, urljoin
from html.parser import HTMLParser
from pathlib import Path
from flask import Flask, Response, render_template_string, request, stream_with_context

try:
    from PIL import Image
    import pytesseract
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

app = Flask(__name__)

PSL = {
    'com','com.tr','org.tr','net.tr','gov.tr','edu.tr','mil.tr','k12.tr','av.tr','dr.tr','tel.tr','info.tr','name.tr',
    'co.uk','org.uk','me.uk','ltd.uk','plc.uk','net.uk','sch.uk','gov.uk','nhs.uk','ac.uk','police.uk',
    'com.au','net.au','org.au','edu.au','gov.au','asn.au','id.au',
    'co.nz','org.nz','net.nz','govt.nz','ac.nz',
    'co.jp','ne.jp','or.jp','ac.jp','go.jp','ed.jp',
    'com.br','net.br','org.br','gov.br','edu.br',
    'co.in','net.in','org.in','gen.in','ac.in','edu.in','gov.in',
    'com.cn','net.cn','org.cn','gov.cn','edu.cn',
    'co.za','org.za','net.za','gov.za','ac.za',
    'com.sg','net.sg','org.sg','gov.sg','edu.sg',
    'com.hk','net.hk','org.hk','gov.hk','edu.hk',
    'co.kr','ne.kr','or.kr','ac.kr','go.kr',
    'com.my','net.my','org.my','gov.my',
    'co.id','net.id','or.id','ac.id','go.id',
    'on.ca','bc.ca','qc.ca','ab.ca','mb.ca','sk.ca','ns.ca','nb.ca',
    'com.mx','gob.mx','edu.mx',
    'com.ar','gob.ar','net.ar','org.ar',
    'com.co','gov.co','org.co',
    'com.pl','org.pl','net.pl',
    'com.es','gob.es',
    'com.pt','edu.pt','gov.pt',
    'com.ua','gov.ua','org.ua',
    'co.il','gov.il','ac.il',
    'com.ph','net.ph','org.ph','gov.ph',
    'com.pk','net.pk','org.pk','gov.pk',
    'com.eg','gov.eg','com.ng','gov.ng','com.sa','gov.sa','org.sa',
}

def base_domain(hostname: str) -> str:
    if not hostname: return ''
    hostname = hostname.lower().rstrip('.').split(':')[0]
    parts = hostname.split('.')
    if len(parts) <= 1: return hostname
    if len(parts) >= 3:
        two = '.'.join(parts[-2:])
        if two in PSL:
            return '.'.join(parts[-3:])
    return '.'.join(parts[-2:])

CDN_RE = [
    re.compile(r'\.cloudfront\.net$'), re.compile(r'\.akamai(hd|zed)?\.net$'),
    re.compile(r'\.fastly\.net$'),      re.compile(r'\.cloudflare\.com$'),
    re.compile(r'\.jsdelivr\.net$'),    re.compile(r'\.unpkg\.com$'),
    re.compile(r'cdnjs\.cloudflare\.com$'), re.compile(r'\.googleapis\.com$'),
    re.compile(r'\.gstatic\.com$'),     re.compile(r'\.amazonaws\.com$'),
    re.compile(r'\.azureedge\.net$'),   re.compile(r'\.twimg\.com$'),
    re.compile(r'\.fbcdn\.net$'),       re.compile(r'\.cloudinary\.com$'),
    re.compile(r'\.imgix\.net$'),       re.compile(r'\.wp\.com$'),
    re.compile(r'\.staticflickr\.com$'),re.compile(r'\.bunnycdn\.com$'),
]
TRK_RE = [
    re.compile(r'google-analytics\.com$'), re.compile(r'googletagmanager\.com$'),
    re.compile(r'doubleclick\.net$'),       re.compile(r'googlesyndication\.com$'),
    re.compile(r'segment\.com$'),           re.compile(r'mixpanel\.com$'),
    re.compile(r'hotjar\.com$'),            re.compile(r'clarity\.ms$'),
    re.compile(r'facebook\.com$'),          re.compile(r'connect\.facebook\.net$'),
]

def classify(host: str, target_base: str) -> str:
    hb = base_domain(host)
    if hb == target_base:
        return 'PRIMARY' if host == target_base else 'SUBDOMAIN'
    for r in CDN_RE:
        if r.search(host): return 'CDN'
    for r in TRK_RE:
        if r.search(host): return 'TRACKER'
    return 'EXTERNAL'

class PageParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.images = []
        self.hosts = set()
        self._seen = set()

    def _resolve(self, src: str) -> Optional[str]:
        if not src or src.startswith('javascript:'): return None
        if src.startswith('data:'): return src
        try: return urljoin(self.base_url, src)
        except: return None

    def _add_host(self, url: str):
        if not url or url.startswith('data:'): return
        try:
            h = urlparse(url).hostname
            if h: self.hosts.add(h)
        except: pass

    def _add_image(self, src: str, alt: str = '', extra: str = ''):
        if not src or src.startswith('data:'): return
        url = self._resolve(src)
        if not url or url in self._seen: return
        self._seen.add(url)
        try: host = urlparse(url).hostname or ''
        except: host = ''
        self.images.append({'url': url, 'alt': alt, 'host': host, 'extra': extra})
        self._add_host(url)

    def handle_starttag(self, tag: str, attrs):
        a = dict(attrs)
        for key in ('src','href','action','data-src','content'):
            if key in a: self._add_host(self._resolve(a[key]) or '')
        if tag == 'img':
            src = a.get('src') or a.get('data-src') or a.get('data-lazy-src') or a.get('data-original','')
            alt = a.get('alt','')
            self._add_image(src, alt)
            for ss in (a.get('srcset','') or '').split(','):
                part = ss.strip().split()[0] if ss.strip() else ''
                if part: self._add_image(part, alt, 'srcset')
        if tag == 'source':
            for ss in (a.get('srcset','') or '').split(','):
                part = ss.strip().split()[0] if ss.strip() else ''
                if part: self._add_image(part,'','srcset')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/122 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,*/*;q=0.9',
    'Accept-Language': 'en-US,en;q=0.9',
}

def fetch_bytes(url: str, timeout: int = 15) -> Optional[bytes]:
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except: return None

def fetch_image_pil(url: str) -> Optional[Image.Image]:
    data = fetch_bytes(url)
    if not data: return None
    try: return Image.open(io.BytesIO(data)).convert('RGB')
    except: return None

def pil_to_b64(img: Image.Image, max_w: int = 200) -> str:
    w, h = img.size
    if w > max_w:
        img = img.resize((max_w, int(h * max_w / w)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=75)
    return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()

DOMAIN_RE = re.compile(
    r'(?:https?://)?(?:www\.)?'
    r'(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)'
    r'+(?:com\.tr|org\.tr|net\.tr|gov\.tr|edu\.tr|'
    r'co\.uk|org\.uk|com\.au|co\.nz|co\.jp|com\.br|'
    r'co\.in|com\.cn|co\.za|co\.kr|com\.my|'
    r'com\.ar|com\.co|com\.mx|com\.sg|com\.hk|'
    r'[a-zA-Z]{2,})'
    r'(?:/[^\s,;\'\"<>()\[\]{}]*)?',
    re.IGNORECASE
)
IMG_EXT_RE = re.compile(r'\.(png|jpg|jpeg|gif|webp|svg|ico|bmp|pdf|zip|js|css|html|xml|json)$', re.IGNORECASE)
VALIDATE_RE = re.compile(
    r'^(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)'
    r'+(?:com\.tr|org\.tr|net\.tr|gov\.tr|[a-zA-Z]{2,})$',
    re.IGNORECASE
)

def extract_domains_from_text(text: str):
    found = {}
    for m in DOMAIN_RE.finditer(text):
        raw = m.group(0).strip()
        host = re.sub(r'^https?://', '', raw, flags=re.IGNORECASE)
        host = re.sub(r'^www\.', '', host, flags=re.IGNORECASE)
        host = host.split('/')[0].split('?')[0].rstrip('.,;:!?\'"')
        if len(host) > 253 or len(host) < 4: continue
        if IMG_EXT_RE.search(host): continue
        if host not in found:
            found[host] = raw
    return [{'host': h, 'raw': r} for h, r in found.items()]

def do_ocr(img: Image.Image) -> str:
    w, h = img.size
    if w < 800:
        scale = max(2, 800 // w)
        img = img.resize((w * scale, h * scale), Image.LANCZOS)
    best = ''
    for psm in (6, 11, 3):
        try:
            text = pytesseract.image_to_string(img, lang='eng', config=f'--oem 3 --psm {psm}')
            if len(text) > len(best): best = text
        except: pass
    return best

# ═══════════════════════════════════════════════════════════════
#  SSE SCAN — runs in thread, pushes JSON events into a queue
# ═══════════════════════════════════════════════════════════════

def run_scan(target: str, do_ocr_flag: bool, q: queue.Queue):
    """Full scan; sends structured events to queue for SSE streaming."""

    def emit(event_type: str, **kwargs):
        q.put({'type': event_type, **kwargs})

    emit('log', level='info', msg=f'Target: {target}')

    # ── Load HTML ──
    html = ''
    base_url = ''
    if target.startswith('http://') or target.startswith('https://'):
        emit('log', level='info', msg='Fetching URL …')
        data = fetch_bytes(target)
        if not data:
            emit('log', level='err', msg='Fatal: could not fetch URL.')
            emit('done')
            return
        html = data.decode('utf-8', errors='replace')
        base_url = target
        emit('log', level='ok', msg=f'Received {len(html)//1024} KB of HTML')
    elif os.path.exists(target):
        emit('log', level='info', msg=f'Reading local file: {target}')
        html = Path(target).read_text(errors='replace')
        base_url = 'https://x.invalid/'
        emit('log', level='ok', msg=f'Loaded {len(html)//1024} KB')
    else:
        emit('log', level='err', msg=f"'{target}' is not a reachable URL or local file.")
        emit('done'); return

    parsed = urlparse(base_url)
    t_host = parsed.hostname or ''
    t_base = base_domain(t_host)
    emit('log', level='ok', msg=f'Hostname: {t_host}  →  Base domain (PSL): {t_base}')

    # ── Parse HTML ──
    parser = PageParser(base_url)
    parser.feed(html)
    images    = parser.images
    all_hosts = parser.hosts
    if t_host: all_hosts.add(t_host)

    # ── Domain classification ──
    domain_map = {}
    if t_host: domain_map[t_host] = 'PRIMARY'
    for h in sorted(all_hosts):
        if h not in domain_map:
            domain_map[h] = classify(h, t_base)

    SORT = {'PRIMARY':0,'SUBDOMAIN':1,'CDN':2,'TRACKER':3,'EXTERNAL':4}
    sorted_domains = sorted(domain_map.items(), key=lambda x: SORT.get(x[1], 5))

    sub_count = sum(1 for _,v in sorted_domains if v == 'SUBDOMAIN')
    ext_count = sum(1 for _,v in sorted_domains if v not in ('PRIMARY','SUBDOMAIN'))

    emit('log', level='ok', msg=f'Discovered {len(sorted_domains)} domain(s), {len(images)} image(s).')

    no_alt  = [i for i in images if not i['alt']]
    ext_imgs= [i for i in images if i['host'] and base_domain(i['host']) != t_base]
    if no_alt:   emit('log', level='warn', msg=f'{len(no_alt)} image(s) missing alt text.')
    if ext_imgs: emit('log', level='warn', msg=f'{len(ext_imgs)} image(s) from external domains.')
    trackers = [h for h,c in sorted_domains if c == 'TRACKER']
    if trackers: emit('log', level='warn', msg=f'{len(trackers)} tracker/analytics domain(s) detected.')

    # ── Emit domains ──
    for host, cls in sorted_domains:
        emit('domain', host=host, cls=cls)

    # ── Emit images ──
    for img in images:
        emit('image',
             url=img['url'],
             alt=img['alt'],
             host=img['host'],
             extra=img['extra'],
             is_external=(img['host'] != '' and base_domain(img['host']) != t_base))

    # ── Stats ──
    emit('stats',
         images=len(images),
         domains=len(sorted_domains),
         subdomains=sub_count,
         third_party=ext_count,
         ocr=0)

    # ── OCR ──
    if not do_ocr_flag:
        emit('log', level='info', msg='OCR skipped.')
        emit('done'); return

    if not OCR_AVAILABLE:
        emit('log', level='err', msg='pytesseract/Pillow not installed — OCR unavailable.')
        emit('done'); return

    ocr_targets = images[:30]
    emit('log', level='ocr', msg=f'Starting OCR on {len(ocr_targets)} image(s) …')
    total_ocr = 0

    for idx, img_info in enumerate(ocr_targets):
        url = img_info['url']
        name = url.split('/')[-1][:50] or f'image-{idx}'
        emit('log', level='ocr', msg=f'[{idx+1}/{len(ocr_targets)}] OCR: {name}')
        emit('ocr_progress', idx=idx, total=len(ocr_targets), url=url)

        # Load image
        pil_img = None
        thumb_b64 = None

        if url.startswith('https://x.invalid/') or url.startswith('file://'):
            rel = url.replace('https://x.invalid/','').replace('file://','')
            for cand in [
                os.path.join(os.path.dirname(os.path.abspath(target)), rel),
                rel, os.path.join(os.getcwd(), rel)
            ]:
                if os.path.exists(cand):
                    try:
                        pil_img = Image.open(cand).convert('RGB')
                        break
                    except: pass

        if pil_img is None:
            pil_img = fetch_image_pil(url)

        if pil_img is None:
            emit('log', level='warn', msg=f'  Could not load image: {name}')
            continue

        thumb_b64 = pil_to_b64(pil_img)
        text = do_ocr(pil_img)

        if not text.strip():
            emit('log', level='info', msg=f'  No text detected in {name}')
            continue

        found = extract_domains_from_text(text)
        if not found:
            emit('log', level='info', msg=f'  No domains in {name}')
            continue

        emit('log', level='ok', msg=f'  {len(found)} domain(s) found in {name}')
        for d in found:
            total_ocr += 1
            cls = classify(d['host'], t_base) if t_base else 'EXTERNAL'
            emit('ocr_domain',
                 host=d['host'],
                 raw=d['raw'],
                 cls=cls,
                 thumb=thumb_b64,
                 source_url=url)

    emit('log', level='ocr', msg=f'OCR complete — {total_ocr} domain(s) found in images.')
    emit('stats_ocr', ocr=total_ocr)
    emit('done')

@app.route('/')
def index():
    return render_template_string(HTML_PAGE)

@app.route('/scan')
def scan_sse():
    target   = request.args.get('target', '').strip()
    do_ocr_f = request.args.get('ocr', '1') == '1'
    if not target:
        return Response('data: {"type":"error","msg":"No target"}\n\n',
                        mimetype='text/event-stream')

    q = queue.Queue()

    def background():
        try:
            run_scan(target, do_ocr_f, q)
        except Exception as e:
            q.put({'type':'log','level':'err','msg': str(e)})
            q.put({'type':'done'})

    t = threading.Thread(target=background, daemon=True)
    t.start()

    def generate():
        while True:
            try:
                item = q.get(timeout=120)
                yield f"data: {json.dumps(item)}\n\n"
                if item.get('type') == 'done':
                    break
            except queue.Empty:
                yield "data: {\"type\":\"ping\"}\n\n"

    return Response(stream_with_context(generate()),
                    mimetype='text/event-stream',
                    headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CYBERSCOPE</title>
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Orbitron:wght@400;700;900&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#030609;--surface:#080e14;--panel:#0b1520;--border:#0f2a3d;
  --accent:#00e5ff;--accent2:#ff3c6e;--accent3:#39ff14;
  --text:#8eb8cc;--bright:#cee9f5;--dim:#2a4a5e;--warn:#ffb700;
  --purple:#a78bfa;--orange:#ff6b00;
}
*{margin:0;padding:0;box-sizing:border-box;}
body{background:var(--bg);color:var(--text);font-family:'Share Tech Mono',monospace;min-height:100vh;}
body::before{content:'';position:fixed;inset:0;
  background:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,229,255,.012) 2px,rgba(0,229,255,.012) 4px);
  pointer-events:none;z-index:9999;}

/* HEADER */
header{border-bottom:1px solid var(--border);padding:16px 36px;
  display:flex;align-items:center;gap:20px;
  background:linear-gradient(90deg,rgba(0,229,255,.05),transparent);}
.logo{font-family:'Orbitron',sans-serif;font-weight:900;font-size:22px;
  color:var(--accent);letter-spacing:4px;text-shadow:0 0 24px rgba(0,229,255,.55);}
.logo span{color:var(--accent2);}
.tagline{font-size:10px;color:var(--dim);letter-spacing:3px;margin-top:2px;}
.hright{margin-left:auto;display:flex;align-items:center;gap:12px;font-size:11px;}
.dot{width:7px;height:7px;border-radius:50%;background:var(--accent3);
  box-shadow:0 0 8px var(--accent3);animation:pulse 2s infinite;}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.2}}

main{max-width:1400px;margin:0 auto;padding:28px 36px;}

/* SHELL */
.shell{background:var(--panel);border:1px solid var(--border);
  padding:24px 26px;margin-bottom:16px;position:relative;
  clip-path:polygon(0 0,calc(100% - 18px) 0,100% 18px,100% 100%,0 100%);}
.shell::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;
  background:linear-gradient(90deg,var(--accent),transparent);}
.cdeco{position:absolute;top:0;right:0;width:18px;height:18px;
  border-top:1px solid var(--accent);border-right:1px solid var(--accent);opacity:.5;}
.slabel{font-family:'Orbitron',sans-serif;font-size:10px;letter-spacing:4px;
  color:var(--accent);margin-bottom:14px;}

/* INPUT ROW */
.irow{display:flex;gap:10px;align-items:stretch;}
.url-in{flex:1;background:var(--surface);border:1px solid var(--border);
  color:var(--bright);font-family:'Share Tech Mono',monospace;font-size:14px;
  padding:13px 18px;outline:none;transition:border-color .2s,box-shadow .2s;}
.url-in::placeholder{color:var(--dim);}
.url-in:focus{border-color:var(--accent);box-shadow:0 0 14px rgba(0,229,255,.1);}
.scan-btn{background:transparent;border:1px solid var(--accent);color:var(--accent);
  font-family:'Orbitron',sans-serif;font-size:11px;font-weight:700;letter-spacing:3px;
  padding:13px 28px;cursor:pointer;transition:all .2s;position:relative;overflow:hidden;white-space:nowrap;}
.scan-btn::before{content:'';position:absolute;inset:0;background:var(--accent);
  transform:translateX(-100%);transition:transform .2s;}
.scan-btn:hover::before{transform:translateX(0);}
.scan-btn:hover{color:var(--bg);}
.scan-btn:disabled{opacity:.4;cursor:not-allowed;}
.scan-btn:disabled:hover::before{transform:translateX(-100%);}
.scan-btn:disabled:hover{color:var(--accent);}
.btn-txt{position:relative;z-index:1;}
.opts{display:flex;align-items:center;gap:16px;margin-top:12px;font-size:11px;flex-wrap:wrap;}
.toggle{display:flex;align-items:center;gap:7px;cursor:pointer;color:var(--text);}
.toggle input{accent-color:var(--purple);cursor:pointer;width:13px;height:13px;}
.hint{color:var(--dim);font-size:10px;}
.pbar{height:2px;background:linear-gradient(90deg,var(--accent),var(--accent2));width:0%;transition:width .3s;margin-top:10px;}

/* TERMINAL */
.terminal{background:#020508;border:1px solid var(--border);padding:14px 18px;
  font-size:12px;line-height:1.9;max-height:200px;overflow-y:auto;
  display:none;margin-bottom:16px;}
.terminal.on{display:block;}
.ll{opacity:0;animation:fi .2s forwards;}
.ll.ok{color:var(--accent3);}
.ll.info{color:var(--accent);}
.ll.warn{color:var(--warn);}
.ll.err{color:var(--accent2);}
.ll.ocr{color:var(--purple);}
@keyframes fi{to{opacity:1}}

/* STATS */
.stats{display:none;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:16px;}
.stats.on{display:grid;}
.sbox{background:var(--panel);border:1px solid var(--border);padding:14px;
  text-align:center;position:relative;overflow:hidden;}
.sbox::after{content:'';position:absolute;bottom:0;left:0;right:0;height:2px;
  background:var(--accent);transform:scaleX(0);transition:transform .5s;}
.sbox.go::after{transform:scaleX(1);}
.sbox.p::after{background:var(--purple);}
.snum{font-family:'Orbitron',sans-serif;font-size:22px;font-weight:900;
  color:var(--accent);text-shadow:0 0 14px rgba(0,229,255,.4);}
.sbox.p .snum{color:var(--purple);text-shadow:0 0 14px rgba(167,139,250,.4);}
.slbl{font-size:9px;letter-spacing:3px;color:var(--dim);margin-top:3px;}

/* RESULTS GRID */
.rgrid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;}
@media(max-width:1100px){.rgrid{grid-template-columns:1fr 1fr;}}
@media(max-width:680px){.rgrid{grid-template-columns:1fr;}}
.rpanel{background:var(--panel);border:1px solid var(--border);display:none;}
.rpanel.on{display:block;}
.rhead{padding:11px 15px;border-bottom:1px solid var(--border);
  display:flex;align-items:center;justify-content:space-between;
  background:rgba(0,229,255,.03);}
.rtitle{font-family:'Orbitron',sans-serif;font-size:10px;letter-spacing:3px;color:var(--accent);}
.rtitle.p{color:var(--purple);}
.rbadge{background:rgba(0,229,255,.08);border:1px solid rgba(0,229,255,.25);
  color:var(--accent);font-size:10px;padding:2px 9px;letter-spacing:2px;}
.rbadge.p{background:rgba(167,139,250,.08);border-color:rgba(167,139,250,.25);color:var(--purple);}
.rbody{padding:11px;max-height:480px;overflow-y:auto;}
.empty{color:var(--dim);font-size:11px;padding:10px;}

/* DOMAIN ROWS */
.drow{padding:7px 11px;margin-bottom:4px;background:var(--surface);
  border-left:3px solid var(--dim);display:flex;align-items:center;gap:9px;
  opacity:0;animation:si .28s forwards;transition:background .2s;}
.drow:hover{background:rgba(0,229,255,.04);}
.drow.PRIMARY{border-left-color:var(--accent3);}
.drow.SUBDOMAIN{border-left-color:var(--accent);}
.drow.EXTERNAL{border-left-color:var(--warn);}
.drow.CDN{border-left-color:var(--accent2);}
.drow.TRACKER{border-left-color:var(--orange);}
@keyframes si{from{opacity:0;transform:translateX(-7px)}to{opacity:1;transform:none}}
.dname{color:var(--bright);font-size:12px;flex:1;word-break:break-all;}
.dtag{font-size:9px;letter-spacing:2px;padding:1px 7px;border:1px solid;white-space:nowrap;}
.tPRIMARY{color:var(--accent3);border-color:rgba(57,255,20,.35);}
.tSUBDOMAIN{color:var(--accent);border-color:rgba(0,229,255,.35);}
.tEXTERNAL{color:var(--warn);border-color:rgba(255,183,0,.35);}
.tCDN{color:var(--accent2);border-color:rgba(255,60,110,.35);}
.tTRACKER{color:var(--orange);border-color:rgba(255,107,0,.35);}

/* IMAGE ROWS */
.irow2{padding:7px 11px;margin-bottom:5px;background:var(--surface);
  border-left:3px solid var(--dim);display:flex;gap:9px;
  opacity:0;animation:si .28s forwards;}
.irow2.has-ext{border-left-color:var(--warn);}
.ithumb{width:46px;height:46px;background:#0a1520;border:1px solid var(--border);
  flex-shrink:0;overflow:hidden;display:flex;align-items:center;justify-content:center;cursor:pointer;}
.ithumb img{width:100%;height:100%;object-fit:cover;}
.ithumb .ph{font-size:18px;color:var(--dim);}
.iinfo{flex:1;min-width:0;}
.iurl{color:var(--bright);font-size:11px;word-break:break-all;line-height:1.5;}
.imeta{margin-top:4px;display:flex;gap:5px;flex-wrap:wrap;}
.ib{font-size:9px;padding:1px 6px;border:1px solid var(--border);color:var(--dim);}
.ib.ha{color:var(--accent3);border-color:rgba(57,255,20,.35);}
.ib.na{color:var(--accent2);border-color:rgba(255,60,110,.35);}
.ib.ex{color:var(--warn);border-color:rgba(255,183,0,.35);}

/* OCR ROWS */
.orow{padding:8px 11px;margin-bottom:5px;background:var(--surface);
  border-left:3px solid var(--purple);display:flex;flex-direction:column;gap:4px;
  opacity:0;animation:si .28s forwards;}
.ohost{color:#c4b5fd;font-size:12px;word-break:break-all;}
.ometa{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:2px;}
.othumb{max-width:64px;max-height:34px;border:1px solid rgba(167,139,250,.35);
  object-fit:cover;cursor:pointer;}
.obadge{font-size:9px;padding:1px 6px;border:1px solid rgba(167,139,250,.35);color:var(--purple);}
.oraw{font-size:9px;color:#6b7280;font-style:italic;word-break:break-all;}

/* LIGHTBOX */
.lb{display:none;position:fixed;inset:0;background:rgba(0,0,0,.88);
  z-index:9998;align-items:center;justify-content:center;}
.lb.on{display:flex;}
.lb img{max-width:90vw;max-height:90vh;border:1px solid var(--accent);}
.lbx{position:absolute;top:18px;right:24px;color:var(--accent);
  font-size:26px;cursor:pointer;font-family:'Orbitron',sans-serif;}

::-webkit-scrollbar{width:4px;}
::-webkit-scrollbar-track{background:var(--surface);}
::-webkit-scrollbar-thumb{background:var(--border);}
</style>
</head>
<body>

<header>
  <div>
    <div class="logo">CYBER<span>SCOPE</span></div>
    <div class="tagline">Image & Domain Reconnaissance</div>
  </div>
  <div class="hright">
    <div class="dot"></div>
    <span id="sysStatus">READY</span>
  </div>
</header>

<main>
  <!-- INPUT -->
  <div class="shell">
    <div class="cdeco"></div>
    <div class="slabel">// TARGET ACQUISITION</div>
    <div class="irow">
      <input class="url-in" id="urlIn" type="text"
             placeholder="https://example.com  or  /path/to/file.html"
             onkeydown="if(event.key==='Enter')startScan()" />
      <button class="scan-btn" id="scanBtn" onclick="startScan()">
        <span class="btn-txt">◈ SCAN</span>
      </button>
    </div>
    <div class="opts">
      <label class="toggle">
        <input type="checkbox" id="ocrCheck" checked />
        <span>Enable Tesseract OCR (finds domains inside image text)</span>
      </label>
      <span class="hint">// Scans every image with OCR</span>
    </div>
    <div class="pbar" id="pbar"></div>
  </div>

  <!-- TERMINAL -->
  <div class="terminal" id="terminal"></div>

  <!-- STATS -->
  <div class="stats" id="stats">
    <div class="sbox" id="sb0"><div class="snum" id="nImg">0</div><div class="slbl">Images</div></div>
    <div class="sbox" id="sb1"><div class="snum" id="nDom">0</div><div class="slbl">Page Domains</div></div>
    <div class="sbox" id="sb2"><div class="snum" id="nSub">0</div><div class="slbl">Subdomains</div></div>
    <div class="sbox" id="sb3"><div class="snum" id="nExt">0</div><div class="slbl">3rd Party</div></div>
    <div class="sbox p" id="sb4"><div class="snum" id="nOcr">–</div><div class="slbl">OCR Domains</div></div>
  </div>

  <!-- RESULTS -->
  <div class="rgrid" id="rgrid">
    <div class="rpanel" id="pDom">
      <div class="rhead">
        <div class="rtitle">◈ PAGE DOMAINS</div>
        <div class="rbadge" id="cDom">0 HOSTS</div>
      </div>
      <div class="rbody" id="lDom"></div>
    </div>
    <div class="rpanel" id="pImg">
      <div class="rhead">
        <div class="rtitle">◈ IMAGE SOURCES</div>
        <div class="rbadge" id="cImg">0 IMAGES</div>
      </div>
      <div class="rbody" id="lImg"></div>
    </div>
    <div class="rpanel" id="pOcr">
      <div class="rhead">
        <div class="rtitle p">◈ OCR: DOMAINS IN IMAGES</div>
        <div class="rbadge p" id="cOcr">0 FOUND</div>
      </div>
      <div class="rbody" id="lOcr">
        <div class="empty" id="ocrWait">Waiting for OCR results …</div>
      </div>
    </div>
  </div>
</main>

<div class="lb" id="lb" onclick="closeLb()">
  <span class="lbx">✕</span>
  <img id="lbImg" src="" alt="">
</div>

<script>
const CLS_CSS = {PRIMARY:'PRIMARY',SUBDOMAIN:'SUBDOMAIN',CDN:'CDN',TRACKER:'TRACKER',EXTERNAL:'EXTERNAL'};
const CLS_LABEL = {PRIMARY:'PRIMARY',SUBDOMAIN:'SUBDOMAIN',CDN:'CDN',TRACKER:'TRACKER',EXTERNAL:'EXTERNAL'};

let domCount=0, imgCount=0, ocrCount=0;
let es = null;

function log(msg, level='info'){
  const t = document.getElementById('terminal');
  t.classList.add('on');
  const d = document.createElement('div');
  d.className = 'll ' + level;
  const ts = new Date().toISOString().slice(11,23);
  d.textContent = '['+ts+'] '+msg;
  t.appendChild(d);
  t.scrollTop = t.scrollHeight;
}

function setProgress(p){ document.getElementById('pbar').style.width = p+'%'; }

function openLb(src){ document.getElementById('lbImg').src=src; document.getElementById('lb').classList.add('on'); }
function closeLb(){ document.getElementById('lb').classList.remove('on'); }

function addDomain(host, cls){
  const list = document.getElementById('lDom');
  if(list.querySelector('.empty')) list.innerHTML='';
  domCount++;
  document.getElementById('cDom').textContent = domCount+' HOSTS';
  document.getElementById('nDom').textContent = domCount;

  const row = document.createElement('div');
  row.className = 'drow '+(CLS_CSS[cls]||'EXTERNAL');
  row.style.animationDelay = Math.min(domCount*30,600)+'ms';
  row.innerHTML =
    '<div class="dname">'+host+'</div>'+
    '<div class="dtag t'+(cls||'EXTERNAL')+'">'+( CLS_LABEL[cls]||'EXTERNAL' )+'</div>';
  list.appendChild(row);
}

function addImage(data){
  const list = document.getElementById('lImg');
  if(list.querySelector('.empty')) list.innerHTML='';
  imgCount++;
  document.getElementById('cImg').textContent = imgCount+' IMAGES';
  document.getElementById('nImg').textContent = imgCount;

  const hasAlt = data.alt && data.alt.trim().length>0;
  let badges = '<span class="ib '+(hasAlt?'ha':'na')+'">'+(hasAlt?'ALT ✓':'NO ALT')+'</span>';
  if(data.is_external) badges += '<span class="ib ex">EXTERNAL</span>';
  if(data.host && data.host !== '') badges += '<span class="ib">'+data.host+'</span>';
  if(data.extra) badges += '<span class="ib">'+data.extra.toUpperCase()+'</span>';

  const row = document.createElement('div');
  row.className = 'irow2'+(data.is_external?' has-ext':'');
  row.style.animationDelay = Math.min(imgCount*30,600)+'ms';
  const safeUrl = data.url.replace(/"/g,'&quot;');
  row.innerHTML =
    '<div class="ithumb" onclick="openLb(\''+safeUrl+'\')">'
    +'<img src="'+safeUrl+'" alt="" loading="lazy" '
    +'onerror="this.parentElement.innerHTML=\'<div class=ph>🖼</div>\'">'
    +'</div>'
    +'<div class="iinfo">'
    +'<div class="iurl">'+data.url+'</div>'
    +'<div class="imeta">'+badges+'</div>'
    +'</div>';
  list.appendChild(row);
}

function addOcrDomain(data){
  const list = document.getElementById('lOcr');
  const wait = document.getElementById('ocrWait');
  if(wait) wait.remove();
  ocrCount++;
  document.getElementById('cOcr').textContent = ocrCount+' FOUND';
  document.getElementById('nOcr').textContent = ocrCount;
  document.getElementById('sb4').classList.add('go');

  const cls = data.cls || 'EXTERNAL';
  const row = document.createElement('div');
  row.className = 'orow';
  row.innerHTML =
    '<div class="ohost">◆ '+data.host+'</div>'
    +'<div class="ometa">'
    +(data.thumb ? '<img class="othumb" src="'+data.thumb+'" alt="" onclick="openLb(\''+data.thumb+'\')">' : '')
    +'<span class="obadge">OCR EXTRACTED</span>'
    +'<span class="obadge t'+cls+'" style="color:inherit">'+cls+'</span>'
    +'<span class="oraw">"'+data.raw.slice(0,80)+'"</span>'
    +'</div>';
  list.appendChild(row);
}

function resetUI(){
  domCount=0; imgCount=0; ocrCount=0;
  ['pDom','pImg','pOcr'].forEach(id=>document.getElementById(id).classList.remove('on'));
  ['lDom','lImg'].forEach(id=>document.getElementById(id).innerHTML='');
  document.getElementById('lOcr').innerHTML='<div class="empty" id="ocrWait">Waiting for OCR results …</div>';
  document.getElementById('stats').classList.remove('on');
  document.querySelectorAll('.sbox').forEach(b=>b.classList.remove('go'));
  ['nImg','nDom','nSub','nExt'].forEach(id=>document.getElementById(id).textContent='0');
  document.getElementById('nOcr').textContent='–';
  ['cDom','cImg','cOcr'].forEach(id=>document.getElementById(id).textContent='0');
  document.getElementById('terminal').innerHTML='';
  document.getElementById('terminal').classList.remove('on');
  setProgress(0);
}

function startScan(){
  if(es){ es.close(); es=null; }
  const target = document.getElementById('urlIn').value.trim();
  if(!target){ alert('Enter a URL or file path.'); return; }
  const doOcr = document.getElementById('ocrCheck').checked ? '1' : '0';

  resetUI();
  document.getElementById('scanBtn').disabled = true;
  document.getElementById('sysStatus').textContent = 'SCANNING …';
  setProgress(5);

  es = new EventSource('/scan?target='+encodeURIComponent(target)+'&ocr='+doOcr);
  let domsDone=false, imgsDone=false;

  es.onmessage = function(e){
    const d = JSON.parse(e.data);

    if(d.type === 'ping') return;

    if(d.type === 'log'){
      log(d.msg, d.level||'info');
      return;
    }

    if(d.type === 'domain'){
      if(!domsDone){ domsDone=true; document.getElementById('pDom').classList.add('on'); }
      addDomain(d.host, d.cls);
      return;
    }

    if(d.type === 'image'){
      if(!imgsDone){ imgsDone=true; document.getElementById('pImg').classList.add('on'); }
      addImage(d);
      return;
    }

    if(d.type === 'stats'){
      document.getElementById('nImg').textContent = d.images;
      document.getElementById('nDom').textContent = d.domains;
      document.getElementById('nSub').textContent = d.subdomains;
      document.getElementById('nExt').textContent = d.third_party;
      document.getElementById('stats').classList.add('on');
      document.getElementById('pOcr').classList.add('on');
      ['sb0','sb1','sb2','sb3'].forEach((id,i)=>
        setTimeout(()=>document.getElementById(id).classList.add('go'),i*90));
      setProgress(70);
      return;
    }

    if(d.type === 'ocr_domain'){
      addOcrDomain(d);
      return;
    }

    if(d.type === 'stats_ocr'){
      document.getElementById('nOcr').textContent = d.ocr;
      return;
    }

    if(d.type === 'ocr_progress'){
      const pct = 70 + Math.round((d.idx / d.total) * 28);
      setProgress(pct);
      return;
    }

    if(d.type === 'done'){
      setProgress(100);
      document.getElementById('scanBtn').disabled = false;
      document.getElementById('sysStatus').textContent = 'DONE';
      es.close(); es=null;
      // If no OCR domains found
      const wait = document.getElementById('ocrWait');
      if(wait) wait.textContent = 'No domains found in image text.';
      return;
    }
  };

  es.onerror = function(){
    log('Connection error or scan finished.','warn');
    document.getElementById('scanBtn').disabled = false;
    document.getElementById('sysStatus').textContent = 'ERROR';
    if(es){ es.close(); es=null; }
  };
}
</script>
</body>
</html>
"""

if __name__ == '__main__':
    print("\n  CYBERSCOPE Flask Server")
    print("  ─────────────────────────────────────")
    print("  http://localhost:8000")
    print("  OCR available:", OCR_AVAILABLE)
    print("  Press Ctrl+C to quit\n")
    app.run(host='0.0.0.0', port=8000, debug=False, threaded=True)