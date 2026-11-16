"""TRIGER 글로벌 유동성 대시보드 생성기.
매일 GitHub Actions가 실행 → FRED·야후 자료 수신 → site/index.html 생성 → GitHub Pages 게시.
글로벌 유동성 = 연준 자산 - TGA - 역레포 + ECB 자산 + BoJ 자산 (중국 제외, 조 달러)."""
import io, os, time, json, datetime as dt
import numpy as np, pandas as pd, requests
import plotly.graph_objects as go

START = '2015-01-01'
DL_START = '2013-01-01'
HORIZONS = {'3개월': 63, '6개월': 126, '12개월': 252}
ASSETS = {'S&P500': '^GSPC', '금': 'GC=F', '비트코인': 'BTC-USD', '코스피': '^KS11'}
FRED_API_KEY = os.environ.get('FRED_API_KEY', '')
DEMO = os.environ.get('DEMO') == '1'
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36'}

def fred(sid):
    if DEMO:
        f = {'WALCL': 'W-WED', 'WTREGEN': 'W-WED', 'RRPONTSYD': 'B', 'ECBASSETSW': 'W-FRI', 'DEXUSEU': 'B', 'JPNASSETS': 'MS', 'DEXJPUS': 'B'}[sid]
        b = {'WALCL': 7e6, 'WTREGEN': 700, 'RRPONTSYD': 500, 'ECBASSETSW': 6.5e6, 'DEXUSEU': 1.1, 'JPNASSETS': 7e6, 'DEXJPUS': 145}[sid]
        i = pd.date_range(DL_START, pd.Timestamp.today(), freq=f); r = np.random.default_rng(abs(hash(sid)) % 999)
        return pd.Series(b * np.exp(np.cumsum(r.normal(0, 0.01, len(i)))), index=i)
    errs = []
    if FRED_API_KEY:
        try:
            j = requests.get('https://api.stlouisfed.org/fred/series/observations', timeout=30,
                             params={'series_id': sid, 'api_key': FRED_API_KEY, 'file_type': 'json', 'observation_start': DL_START}).json()
            d = pd.DataFrame(j['observations'])
            return pd.Series(pd.to_numeric(d['value'], errors='coerce').values, index=pd.to_datetime(d['date'])).dropna()
        except Exception as e:
            errs.append(f'API {e}')
    for k in range(2):
        try:
            r = requests.get(f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}', timeout=(10, 25), headers=UA)
            d = pd.read_csv(io.StringIO(r.text)); d = d.iloc[:, :2]; d.columns = ['date', 'v']
            s = pd.Series(pd.to_numeric(d['v'], errors='coerce').values, index=pd.to_datetime(d['date'])).dropna()
            return s[s.index >= DL_START]
        except Exception as e:
            errs.append(str(e)[:80]); print(f'  {sid} 재시도 {k+1}: {str(e)[:80]}', flush=True); time.sleep(3)
    raise RuntimeError(f'{sid}: {errs[-1]}')

def yclose(tk):
    if DEMO:
        i = pd.bdate_range(DL_START, pd.Timestamp.today()); r = np.random.default_rng(abs(hash(tk)) % 999)
        return pd.Series(100 * np.exp(np.cumsum(r.normal(0.0004, 0.015, len(i)))), index=i)
    import yfinance as yf
    for k in range(3):
        try:
            d = yf.download(tk, start=DL_START, progress=False, auto_adjust=True)
            c = d['Close']; c = c.iloc[:, 0] if hasattr(c, 'columns') else c
            c.index = pd.to_datetime(c.index).tz_localize(None).normalize()
            if len(c.dropna()) > 100: return c.dropna().astype(float)
        except Exception:
            pass
        time.sleep(3)
    raise RuntimeError(f'{tk} 수신 실패')

RAW, NOTE = {}, []
for sid in ('WALCL', 'WTREGEN', 'RRPONTSYD', 'ECBASSETSW', 'DEXUSEU', 'JPNASSETS', 'DEXJPUS'):
    try: RAW[sid] = fred(sid); print(f'{sid} {len(RAW[sid])}개, 마지막 {RAW[sid].index[-1].date()}', flush=True)
    except Exception as e: NOTE.append(f'{sid} 실패({str(e)[:60]})'); print(NOTE[-1], flush=True)
PX = {}
for nm, tk in ASSETS.items():
    try: PX[nm] = yclose(tk); print(f'{nm} {len(PX[nm])}개', flush=True)
    except Exception as e: NOTE.append(f'{nm} 실패'); print(NOTE[-1], flush=True)
if any(s not in RAW for s in ('WALCL', 'WTREGEN', 'RRPONTSYD')):
    raise SystemExit('필수 자료 실패: ' + '; '.join(NOTE))

END = min(RAW[s].index[-1] for s in ('WALCL', 'WTREGEN', 'RRPONTSYD'))
idx = pd.bdate_range(DL_START, END)
al = lambda s: s.reindex(idx.union(s.index)).ffill().reindex(idx)
P = pd.DataFrame(index=idx)
P['연준'] = al(RAW['WALCL']) / 1e6
P['TGA'] = -al(RAW['WTREGEN']) / 1e3
P['역레포'] = -al(RAW['RRPONTSYD']).fillna(0) / 1e3
if 'ECBASSETSW' in RAW and 'DEXUSEU' in RAW: P['ECB'] = al(RAW['ECBASSETSW']) / 1e6 * al(RAW['DEXUSEU'])
if 'JPNASSETS' in RAW and 'DEXJPUS' in RAW: P['BoJ'] = al(RAW['JPNASSETS']) * 1e8 / 1e12 / al(RAW['DEXJPUS'])
P = P.dropna(); GL = P.sum(axis=1)
CHG = pd.DataFrame({h: GL - GL.shift(n) for h, n in HORIZONS.items()})
APX = pd.DataFrame({nm: s.reindex(GL.index.union(s.index)).ffill().reindex(GL.index) for nm, s in PX.items()})
ACHG = {h: (APX / APX.shift(n) - 1) * 100 for h, n in HORIZONS.items()}
show = GL.index >= pd.Timestamp(START); x = GL.index[show]

BG, GRID, TXT = '#1b1d22', '#30333a', '#c9cdd4'
AREA = {'3개월': '#9ad97a', '6개월': '#5fbf8f', '12개월': '#4a8fd1'}
LINE = {'S&P500': '#4fc3f7', '금': '#ffd54f', '비트코인': '#ff9f43', '코스피': '#f06292'}

def hex2rgba(h, a): h = h.lstrip('#'); return f'rgba({int(h[0:2],16)},{int(h[2:4],16)},{int(h[4:6],16)},{a})'

def chart(h):
    f = go.Figure()
    f.add_trace(go.Scatter(x=x, y=CHG[h][show], name=f'유동성 {h} 변화(조 달러)', mode='lines', line=dict(color=AREA[h], width=1.2),
                           fill='tozeroy', fillcolor=hex2rgba(AREA[h], 0.35), hovertemplate='%{y:+.2f}조 달러'))
    for nm in APX.columns:
        f.add_trace(go.Scatter(x=x, y=ACHG[h][nm][show], name=f'{nm} {h} %', yaxis='y2', mode='lines',
                               line=dict(color=LINE.get(nm, '#fff'), width=1.3), hovertemplate='%{y:+.1f}%',
                               visible=True if nm in ('S&P500',) else 'legendonly'))
    lim = float(np.nanmax(np.abs(CHG[h][show]))) * 1.15
    lim2 = float(np.nanpercentile(np.abs(ACHG[h][show].values), 99)) * 1.1   # 오른쪽 축도 0을 가운데에 맞춤
    f.update_layout(paper_bgcolor=BG, plot_bgcolor=BG, font=dict(family='Nanum Gothic, sans-serif', color=TXT, size=12),
                    hovermode='x unified', height=470, margin=dict(l=60, r=60, t=40, b=70),
                    xaxis=dict(gridcolor=GRID, showspikes=True, spikedash='dot', spikecolor='#888',
                               rangeselector=dict(buttons=[dict(count=2, label='2년', step='year', stepmode='backward'),
                                                           dict(count=5, label='5년', step='year', stepmode='backward'),
                                                           dict(step='all', label='전체')], bgcolor='#2a2d33', activecolor='#4a4f59', font=dict(color=TXT))),
                    yaxis=dict(gridcolor=GRID, zerolinecolor='#777', range=[-lim, lim], ticksuffix='T', title='유동성 변화'),
                    yaxis2=dict(overlaying='y', side='right', showgrid=False, zerolinecolor='#777', ticksuffix='%', title='자산 변화율', range=[-lim2, lim2]),
                    legend=dict(orientation='h', y=-0.12, x=0.5, xanchor='center', bgcolor='rgba(0,0,0,0)'))
    return f.to_html(full_html=False, include_plotlyjs=False, config={'displaylogo': False, 'responsive': True})

def corr_table():
    rows = []
    for nm in APX.columns:
        best, cs = (None, -9), []
        for m in (0, 1, 2, 3, 6, 9, 12):
            j = pd.concat([CHG['6개월'], ACHG['6개월'][nm].shift(-21 * m)], axis=1).dropna()
            j = j[j.index >= pd.Timestamp(START)]
            c = float(j.iloc[:, 0].corr(j.iloc[:, 1])) if len(j) > 100 else np.nan
            cs.append(c)
            if c == c and c > best[1]: best = (m, c)
        rows.append((nm, cs, best))
    head = ''.join(f'<th>{m}개월</th>' for m in (0, 1, 2, 3, 6, 9, 12))
    body = ''.join(f'<tr><td>{nm}</td>' + ''.join(f'<td>{c:+.2f}</td>' for c in cs) + f'<td>{b[0]}개월 ({b[1]:+.2f})</td></tr>' for nm, cs, b in rows)
    return f'<table><tr><th>자산</th>{head}<th>가장 높은 시차</th></tr>{body}</table>'

last = GL.index[-1]
reg = '확장' if CHG['12개월'].iloc[-1] > 0 else '수축'
mom = '개선' if CHG['3개월'].iloc[-1] * 2 > CHG['6개월'].iloc[-1] else '악화'
cards = [('글로벌 유동성', f'{GL.iloc[-1]:.2f}조 달러', f'{last:%Y-%m-%d} 기준'),
         ('3개월 변화', f'{CHG["3개월"].iloc[-1]:+.2f}조', ''), ('6개월 변화', f'{CHG["6개월"].iloc[-1]:+.2f}조', ''),
         ('12개월 변화', f'{CHG["12개월"].iloc[-1]:+.2f}조', f'국면: {reg}, 최근 속도 {mom}')]
comp = ' · '.join(f'{c} {P[c].iloc[-1]:+.2f}' for c in P.columns)
assets_now = ''.join(f'<tr><td>{nm}</td>' + ''.join(f'<td>{ACHG[h][nm].iloc[-1]:+.1f}%</td>' for h in HORIZONS) + '</tr>' for nm in APX.columns)
now_kst = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=9)
html = f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TRIGER 글로벌 유동성</title>
<link href="https://fonts.googleapis.com/css2?family=Nanum+Gothic:wght@400;700&display=swap" rel="stylesheet">
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
body{{margin:0;background:{BG};color:{TXT};font-family:'Nanum Gothic',sans-serif}}
.wrap{{max-width:1200px;margin:0 auto;padding:16px}}
h1{{font-size:20px;margin:8px 0 2px}} .sub{{color:#8b9099;font-size:12px;margin-bottom:14px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin-bottom:14px}}
.card{{background:#23262d;border:1px solid {GRID};border-radius:6px;padding:12px}}
.card .k{{font-size:12px;color:#8b9099}} .card .v{{font-size:22px;font-weight:700;margin:4px 0}} .card .n{{font-size:11px;color:#8b9099}}
.tabs{{display:flex;gap:6px;margin:10px 0}} .tab{{padding:7px 16px;border:1px solid {GRID};border-radius:4px;cursor:pointer;background:#23262d;font-size:13px}}
.tab.on{{background:#3a3f48;color:#fff;border-color:#5a606b}}
.panel{{display:none}} .panel.on{{display:block}}
h2{{font-size:15px;margin:22px 0 6px}}
table{{border-collapse:collapse;width:100%;font-size:12px}} th,td{{border:1px solid {GRID};padding:6px 8px;text-align:right}}
th{{background:#2a2d33;color:#e6e8eb}} td:first-child,th:first-child{{text-align:left}}
.note{{font-size:11px;color:#8b9099;margin-top:10px;line-height:1.6}}
</style></head><body><div class="wrap">
<h1>TRIGER 글로벌 유동성 사이클</h1>
<div class="sub">연준 - TGA - 역레포 + ECB + BoJ (중국 제외) · 매일 자동 갱신 · 마지막 갱신 {now_kst:%Y-%m-%d %H:%M} KST · 구성 {comp}</div>
<div class="cards">{''.join(f'<div class="card"><div class="k">{k}</div><div class="v">{v}</div><div class="n">{n}</div></div>' for k, v, n in cards)}</div>
<div class="tabs">{''.join(f'<div class="tab{" on" if i == 1 else ""}" onclick="sel({i})">{h}</div>' for i, h in enumerate(HORIZONS))}</div>
{''.join(f'<div class="panel{" on" if i == 1 else ""}" id="p{i}">{chart(h)}</div>' for i, h in enumerate(HORIZONS))}
<div class="note">면적은 글로벌 유동성의 변화량(왼쪽 축, 조 달러), 선은 자산의 같은 기간 변화율(오른쪽 축, %). 범례를 누르면 자산 선을 켜고 끈다(처음에는 S&P500만 표시). 위쪽 탭으로 3·6·12개월을 바꾼다.</div>
<h2>자산 변화율 (현재)</h2>
<table><tr><th>자산</th>{''.join(f'<th>{h}</th>' for h in HORIZONS)}</tr>{assets_now}</table>
<h2>유동성 6개월 변화가 자산 6개월 변화율을 몇 달 앞서나 (상관)</h2>
{corr_table()}
<div class="note">상관은 인과가 아니며 표본 기간에 따라 바뀐다. 매매 판단 도구가 아니라 배경 지표다. {(' 수신 실패: ' + '; '.join(NOTE)) if NOTE else ''}</div>
</div><script>function sel(i){{document.querySelectorAll('.tab').forEach((t,j)=>t.classList.toggle('on',i==j));document.querySelectorAll('.panel').forEach((p,j)=>p.classList.toggle('on',i==j));window.dispatchEvent(new Event('resize'));}}</script>
</body></html>'''
os.makedirs('site', exist_ok=True)
open('site/index.html', 'w', encoding='utf-8').write(html)
print('site/index.html 생성', last.date(), '실패:', NOTE)
