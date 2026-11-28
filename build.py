"""TRIGER 글로벌 유동성 대시보드 생성기.
매일 GitHub Actions가 실행 → FRED·야후 자료 수신 → site/index.html, site/contact.html 생성 → GitHub Pages 게시.
v5 (2026-11-28): 주간 변화 탭(구성별 기여 막대, 최근 12주 표)과 1주 변화 카드. 기본 탭은 6개월 그대로.
v4 (2026-11-27): 원자료 엑셀(site/liquidity_raw.xlsx) 생성, 중앙은행 자료가 새로 들어온 갱신에서만 Gmail로 엑셀 첨부 발송(시크릿 GMAIL_USER, GMAIL_APP_PASSWORD).
v3 (2026-11-24): 한국어/영어 전환(#en 또는 ?lang=en), 기간을 바꾸면 세로 눈금을 그 구간에 맞추고 왼쪽·오른쪽 0선을 항상 같은 높이로, 세로 확대 잠금, 문의 페이지.
글로벌 유동성 = 연준 자산 - TGA - 역레포 + ECB 자산 + BoJ 자산 (중국 제외, 조 달러)."""
import io, os, time, json, html as _html, datetime as dt
import numpy as np, pandas as pd, requests
import plotly.graph_objects as go

START = '2015-01-01'
DL_START = '2003-01-01'   # 사이클 분석용으로 길게(차트 표시는 START부터)
HORIZONS = {'3개월': 63, '6개월': 126, '12개월': 252}
ASSETS = {'S&P500': '^GSPC', '금': 'GC=F', '비트코인': 'BTC-USD', '코스피': '^KS11'}
FRED_API_KEY = os.environ.get('FRED_API_KEY', '')
PAGES_URL = os.environ.get('PAGES_URL', 'https://rukierukie.github.io/liquidity/')   # 직전 발송 상태(state.json)를 읽어 올 게시 주소
MAIL_KEYS = ('WALCL', 'WTREGEN', 'ECBASSETSW', 'JPNASSETS')   # 이 네 자료의 마지막 날짜가 바뀌면 '유동성 수치가 바뀐 갱신'으로 보고 메일을 보낸다(역레포·환율은 매일 바뀌어 제외)
CONTACT = ('cymbalion', 'gmail.com')   # 문의 페이지 메일 주소(자동 수집을 줄이려고 페이지에서 조립해 보여 준다)
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
_tg = al(RAW['WTREGEN']); P['TGA'] = -_tg / (1e6 if _tg.median() > 1e4 else 1e3)   # FRED WTREGEN 단위(백만 달러) 자동 판별
P['역레포'] = -al(RAW['RRPONTSYD']).fillna(0) / 1e3
if 'ECBASSETSW' in RAW and 'DEXUSEU' in RAW: P['ECB'] = al(RAW['ECBASSETSW']) / 1e6 * al(RAW['DEXUSEU'])
if 'JPNASSETS' in RAW and 'DEXJPUS' in RAW: P['BoJ'] = al(RAW['JPNASSETS']) * 1e8 / 1e12 / al(RAW['DEXJPUS'])
P = P.dropna(); GL = P.sum(axis=1)
CHG = pd.DataFrame({h: (np.log(GL) - np.log(GL.shift(n))) * 100 for h, n in HORIZONS.items()})   # 로그 변화율(%)
APX = pd.DataFrame({nm: s.reindex(GL.index.union(s.index)).ffill().reindex(GL.index) for nm, s in PX.items()})
ACHG = {h: (np.log(APX) - np.log(APX.shift(n))) * 100 for h, n in HORIZONS.items()}   # 로그 변화율(%)
show = GL.index >= pd.Timestamp(START); x = GL.index[show]

BG, GRID, TXT = '#1b1d22', '#30333a', '#c9cdd4'
AREA = {'3개월': '#9ad97a', '6개월': '#5fbf8f', '12개월': '#4a8fd1'}
LINE = {'S&P500': '#4fc3f7', '금': '#ffd54f', '비트코인': '#ff9f43', '코스피': '#f06292'}

NM_EN = {'S&P500': 'S&P 500', '금': 'Gold', '비트코인': 'Bitcoin', '코스피': 'KOSPI'}
H_EN = {'3개월': '3M', '6개월': '6M', '12개월': '12M'}
_REPL = [('주간 변화(10억 달러)', 'Weekly change ($bn)'), ('주간 합계', 'Weekly total'), ('역레포', 'RRP'), ('연준', 'Fed'), ('1년', '1Y'), ('유동성 사이클(2~6년 대역, 추세 대비 %)', 'Liquidity cycle (2-6y band, % vs trend)'), ('유동성 사이클(%)', 'Liquidity cycle (%)'),
         ('자산 사이클(%)', 'Asset cycle (%)'), ('표준화 점수(z)', 'Z-score'), ('유동성 변화율', 'Liquidity change'), ('자산 변화율', 'Asset change'),
         (' 변화율(%)', ' change (%)'), ('유동성', 'Liquidity'), ('사이클', 'cycle'), ('S&P500', 'S&P 500'), ('비트코인', 'Bitcoin'), ('코스피', 'KOSPI'),
         ('금', 'Gold'), ('12개월', '12M'), ('6개월', '6M'), ('3개월', '3M'), ('2년', '2Y'), ('5년', '5Y'), ('전체', 'All'), ('고점', 'Peak'), ('저점', 'Trough')]
def en(s_):
    for a, b in _REPL:
        s_ = s_.replace(a, b)
    return s_
I18N = {}
def _collect(fig):
    """차트 안의 한글 문자열(범례 이름, 축 제목, 버튼, 마우스오버 문구)을 영어 사전에 넣는다."""
    d = fig.to_dict()
    for t in d.get('data', []):
        for k in ('name', 'hovertemplate'):
            if isinstance(t.get(k), str): I18N[t[k]] = en(t[k])
    L = d.get('layout', {})
    for ax in ('yaxis', 'yaxis2', 'xaxis'):
        tt = (L.get(ax) or {}).get('title') or {}
        if isinstance(tt, dict) and isinstance(tt.get('text'), str): I18N[tt['text']] = en(tt['text'])
        for b in ((L.get(ax) or {}).get('rangeselector') or {}).get('buttons', []) or []:
            if isinstance(b.get('label'), str): I18N[b['label']] = en(b['label'])
    for um in L.get('updatemenus', []) or []:
        for b in um.get('buttons', []) or []:
            if isinstance(b.get('label'), str): I18N[b['label']] = en(b['label'])
def T(ko, en_):
    """화면 문구: 한국어를 보여 주고 영어는 data-en에 둔다(언어 전환 버튼이 바꾼다)."""
    return f'<span class="i18n" data-en="{_html.escape(str(en_), quote=True)}">{ko}</span>'

def hex2rgba(h, a): h = h.lstrip('#'); return f'rgba({int(h[0:2],16)},{int(h[2:4],16)},{int(h[4:6],16)},{a})'

# ---- 주간 변화 (v5): 금요일마다 마지막 값. 마지막 주는 실제 마지막 자료일로 표시
_wk_key = GL.index.to_period('W-FRI')
WK = P.groupby(_wk_key).tail(1); WK = WK[WK.index >= pd.Timestamp(START)]
WGL = WK.sum(axis=1)
WCH = WK.diff() * 1000          # 구성별 유동성 기여(10억 달러). TGA·역레포는 이미 음수로 들어 있어 차분이 곧 기여
WTOT = WGL.diff() * 1000
WPCT = WGL.pct_change() * 100
COMP_COL = {'연준': '#4a8fd1', 'TGA': '#9ad97a', '역레포': '#b0b4bb', 'ECB': '#e0a84f', 'BoJ': '#c77dd1'}
def wchart():
    f = go.Figure(); xs = WCH.index[1:]
    for c in WCH.columns:
        f.add_trace(go.Bar(x=xs, y=WCH[c].iloc[1:], name=c, marker_color=COMP_COL.get(c, '#888'), hovertemplate='%{y:+.1f}'))
    f.add_trace(go.Scatter(x=xs, y=WTOT.iloc[1:], name='주간 합계', mode='lines+markers', line=dict(color='#ffffff', width=1.6), marker=dict(size=5), hovertemplate='%{y:+.1f}'))
    _pos = WCH.iloc[1:].clip(lower=0).sum(axis=1); _neg = WCH.iloc[1:].clip(upper=0).sum(axis=1)   # 쌓인 막대 끝(세로 눈금 맞춤용, 보이지 않음)
    for _s in (_pos, _neg):
        f.add_trace(go.Scatter(x=xs, y=_s, mode='lines', line=dict(width=0), hoverinfo='skip', showlegend=False))
    _x0 = xs[max(0, len(xs) - 26)]
    f.update_layout(paper_bgcolor=BG, plot_bgcolor=BG, font=dict(family='Nanum Gothic, sans-serif', color=TXT, size=12), hovermode='x unified', barmode='relative',
                    height=490, margin=dict(l=60, r=40, t=40, b=110),
                    xaxis=dict(gridcolor=GRID, range=[_x0 - pd.Timedelta(days=4), xs[-1] + pd.Timedelta(days=4)],
                               rangeselector=dict(buttons=[dict(count=6, label='6개월', step='month', stepmode='backward'), dict(count=1, label='1년', step='year', stepmode='backward'),
                                                           dict(count=2, label='2년', step='year', stepmode='backward'), dict(step='all', label='전체')], bgcolor='#2a2d33', activecolor='#4a4f59', font=dict(color=TXT))),
                    yaxis=dict(gridcolor=GRID, zerolinecolor='#999', title='주간 변화(10억 달러)', fixedrange=True),
                    legend=dict(orientation='h', y=-0.24, x=0.5, xanchor='center', bgcolor='rgba(0,0,0,0)'))
    _collect(f)
    return f.to_html(full_html=False, include_plotlyjs=False, config={'displaylogo': False, 'responsive': True})
def wtable(n=12):
    hd = ''.join(f'<th>{T(k, e)}</th>' for k, e in (('주(마지막 자료일)', 'Week (last data date)'), ('글로벌 유동성', 'Global liquidity'), ('주간 변화', 'Weekly change'), ('주간 %', 'Weekly %'),
                                                  ('연준', 'Fed'), ('TGA', 'TGA'), ('역레포', 'RRP'), ('ECB', 'ECB'), ('BoJ', 'BoJ')))
    rows = ''
    for d_ in list(WCH.index[1:])[-n:][::-1]:
        _b = lambda v: T(f'{v * 10:+,.0f}억', f'{v:+,.1f}bn')
        rows += (f'<tr><td>{d_:%Y-%m-%d}</td><td>{T(f"{WGL[d_]:.3f}조 달러", f"${WGL[d_]:.3f}tn")}</td><td>{_b(WTOT[d_])}</td><td>{WPCT[d_]:+.2f}%</td>'
                 + ''.join(f'<td>{_b(WCH.at[d_, c])}</td>' for c in WCH.columns) + '</tr>')
    return f'<table><tr>{hd}</tr>{rows}</table>'
def wpanel():
    return (wchart() + f'<h2>{T("최근 12주", "Last 12 weeks")}</h2>' + wtable()
            + '<div class="note">' + T('막대는 한 주 동안 글로벌 유동성이 늘거나 줄어든 몫을 구성별로 나눈 것이다(10억 달러). 흰 선은 그 주의 합계다. TGA(재무부 계좌)와 역레포는 잔고가 줄면 시중 유동성이 늘어나므로 플러스로 나온다. ECB·BoJ는 달러 환산값이라 유로·엔 환율 변화가 섞인다. 연준은 수요일 기준 목요일(미국) 발표, ECB는 금요일 기준 다음 주 화요일 발표, BoJ는 월 1회라 최근 주는 일부 자료만 반영되어 있을 수 있다.',
                                       'Bars split each week\'s change in global liquidity by component ($bn); the white line is the weekly total. A falling TGA or RRP balance adds liquidity, so it shows as positive. ECB and BoJ are converted to dollars, so euro and yen moves are included. The Fed reports Wednesday levels on Thursday (US), the ECB reports Friday levels the following Tuesday, and the BoJ monthly, so the latest week may reflect only part of the data.') + '</div>')

def chart(h):
    f = go.Figure()
    f.add_trace(go.Scatter(x=x, y=CHG[h][show], name=f'유동성 {h} 변화율(%)', mode='lines', line=dict(color=AREA[h], width=1.2),
                           fill='tozeroy', fillcolor=hex2rgba(AREA[h], 0.35), hovertemplate='%{y:+.1f}%'))
    for nm in APX.columns:
        f.add_trace(go.Scatter(x=x, y=ACHG[h][nm][show], name=f'{nm} {h} %', yaxis='y2', mode='lines',
                               line=dict(color=LINE.get(nm, '#fff'), width=1.3), hovertemplate='%{y:+.1f}%',
                               visible=True if nm in ('S&P500',) else 'legendonly'))
    lim = float(np.nanmax(np.abs(CHG[h][show]))) * 1.15
    lim2 = float(np.nanpercentile(np.abs(ACHG[h][show][[c for c in APX.columns if c != '비트코인']].values), 99)) * 1.1   # 오른쪽 축도 0을 가운데에 맞춤
    f.update_layout(paper_bgcolor=BG, plot_bgcolor=BG, font=dict(family='Nanum Gothic, sans-serif', color=TXT, size=12),
                    hovermode='x unified', height=490, margin=dict(l=60, r=60, t=40, b=110),
                    xaxis=dict(gridcolor=GRID, showspikes=True, spikedash='dot', spikecolor='#888',
                               rangeselector=dict(buttons=[dict(count=2, label='2년', step='year', stepmode='backward'),
                                                           dict(count=5, label='5년', step='year', stepmode='backward'),
                                                           dict(step='all', label='전체')], bgcolor='#2a2d33', activecolor='#4a4f59', font=dict(color=TXT))),
                    yaxis=dict(gridcolor=GRID, zerolinecolor='#777', range=[-lim, lim], ticksuffix='%', title='유동성 변화율', fixedrange=True),
                    yaxis2=dict(overlaying='y', side='right', showgrid=False, zerolinecolor='#777', ticksuffix='%', title='자산 변화율', range=[-lim2, lim2], fixedrange=True),
                    legend=dict(orientation='h', y=-0.24, x=0.5, xanchor='center', bgcolor='rgba(0,0,0,0)'))
    _collect(f)
    return f.to_html(full_html=False, include_plotlyjs=False, config={'displaylogo': False, 'responsive': True})


def zchart():
    """유동성과 자산의 6·12개월 로그 변화율을 각자 표준화(z점수, 표시 기간 기준)해 같은 눈금에 겹친다. '같은 방향인가'를 보는 용도."""
    f = go.Figure(); traces_per = 1 + len(APX.columns); hs = ['6개월', '12개월']
    for k, h in enumerate(hs):
        z = lambda s_: (s_ - s_[show].mean()) / s_[show].std()
        f.add_trace(go.Scatter(x=x, y=z(CHG[h])[show], name=f'유동성 {h} (z)', mode='lines', line=dict(color=AREA[h], width=2.2),
                               fill='tozeroy', fillcolor=hex2rgba(AREA[h], 0.25), hovertemplate='%{y:+.2f}', visible=(k == 0)))
        for nm in APX.columns:
            f.add_trace(go.Scatter(x=x, y=z(ACHG[h][nm])[show], name=f'{nm} {h} (z)', mode='lines', line=dict(color=LINE.get(nm, '#fff'), width=1.3),
                                   hovertemplate='%{y:+.2f}', visible=(True if nm == 'S&P500' else 'legendonly') if k == 0 else False))
    btn = []
    for k, h in enumerate(hs):
        vis = []
        for kk in range(len(hs)):
            vis += [kk == k] + [((True if nm == 'S&P500' else 'legendonly') if kk == k else False) for nm in APX.columns]
        btn.append(dict(label=h, method='update', args=[{'visible': vis}]))
    f.update_layout(paper_bgcolor=BG, plot_bgcolor=BG, font=dict(family='Nanum Gothic, sans-serif', color=TXT, size=12), hovermode='x unified',
                    height=490, margin=dict(l=60, r=40, t=40, b=110),
                    updatemenus=[dict(type='buttons', direction='right', x=0, y=1.1, xanchor='left', buttons=btn, bgcolor='#2a2d33', font=dict(color='#222'))],
                    xaxis=dict(gridcolor=GRID, showspikes=True, spikedash='dot', spikecolor='#888'),
                    yaxis=dict(gridcolor=GRID, zerolinecolor='#777', range=[-3.5, 3.5], title='표준화 점수(z)', fixedrange=True),
                    legend=dict(orientation='h', y=-0.24, x=0.5, xanchor='center', bgcolor='rgba(0,0,0,0)'))
    _collect(f)
    return f.to_html(full_html=False, include_plotlyjs=False, config={'displaylogo': False, 'responsive': True})

# ---- 사이클: 로그 유동성(주간) → 선형 추세 제거 → 2~6년 대역 필터(버터워스, 앞뒤 양방향) → 사이클 선, 위상, 주된 주기
from scipy import signal
def _cycle(series, lo_y=2, hi_y=6):
    w = np.log(series.resample('W-FRI').last().dropna())
    t = np.arange(len(w)); dtr = w.values - np.polyval(np.polyfit(t, w.values, 1), t)
    sos = signal.butter(2, [1 / (hi_y * 52), 1 / (lo_y * 52)], btype='bandpass', output='sos', fs=1.0)
    cyc = signal.sosfiltfilt(sos, dtr) * 100
    return pd.Series(cyc, index=w.index), pd.Series(dtr * 100, index=w.index)
CYC, DTR = _cycle(GL)
fr, pw = signal.periodogram(DTR.values - DTR.values.mean(), fs=52, detrend='linear', window='hann')
_m = (fr > 0) & (1 / np.where(fr > 0, fr, np.nan) >= 1.5) & (1 / np.where(fr > 0, fr, np.nan) <= 8)
PERIOD = float(1 / fr[_m][np.argmax(pw[_m])]) if _m.any() else float('nan')
_slope = CYC.iloc[-1] - CYC.iloc[-5]
PHASE = ('상승(확장 국면)' if CYC.iloc[-1] > 0 and _slope > 0 else '고점 통과 후 하락' if CYC.iloc[-1] > 0 else
         '하락(수축 국면)' if _slope < 0 else '저점 통과 후 회복')
pk, _ = signal.find_peaks(CYC.values, distance=52); tr, _ = signal.find_peaks(-CYC.values, distance=52)
YEARS = (CYC.index[-1] - CYC.index[0]).days / 365.25
def cyc_chart():
    f = go.Figure(); cs = CYC[CYC.index >= pd.Timestamp(START)]
    f.add_trace(go.Scatter(x=cs.index, y=cs.clip(lower=0), mode='lines', line=dict(width=0), fill='tozeroy', fillcolor='rgba(95,191,143,0.35)', hoverinfo='skip', showlegend=False))
    f.add_trace(go.Scatter(x=cs.index, y=cs.clip(upper=0), mode='lines', line=dict(width=0), fill='tozeroy', fillcolor='rgba(217,95,95,0.35)', hoverinfo='skip', showlegend=False))
    f.add_trace(go.Scatter(x=cs.index, y=cs, name='유동성 사이클(2~6년 대역, 추세 대비 %)', mode='lines', line=dict(color='#e6e8eb', width=1.8), hovertemplate='%{y:+.1f}%'))
    for idx, nm, col, sym in ((pk, '고점', '#5fbf8f', 'triangle-down'), (tr, '저점', '#d95f5f', 'triangle-up')):
        d = CYC.iloc[idx]; d = d[d.index >= pd.Timestamp(START)]
        f.add_trace(go.Scatter(x=d.index, y=d, mode='markers', name=nm, marker=dict(color=col, size=10, symbol=sym), hovertemplate='%{x|%Y-%m} ' + nm))
    l1 = float(np.nanmax(np.abs(cs))) * 1.15; l2 = 0.0
    for nm in APX.columns:
        try:
            ac, _ = _cycle(APX[nm].dropna()); ac = ac[ac.index >= pd.Timestamp(START)]
            if nm != '비트코인': l2 = max(l2, float(np.nanmax(np.abs(ac))) * 1.15)
            f.add_trace(go.Scatter(x=ac.index, y=ac, name=f'{nm} 사이클', yaxis='y2', mode='lines', line=dict(color=LINE.get(nm, '#fff'), width=1.2, dash='dot'),
                                   hovertemplate='%{y:+.1f}%', visible='legendonly'))
        except Exception:
            pass
    f.update_layout(paper_bgcolor=BG, plot_bgcolor=BG, font=dict(family='Nanum Gothic, sans-serif', color=TXT, size=12), hovermode='x unified',
                    height=490, margin=dict(l=60, r=60, t=30, b=110),
                    xaxis=dict(gridcolor=GRID, showspikes=True, spikedash='dot', spikecolor='#888'),
                    yaxis=dict(gridcolor=GRID, zerolinecolor='#999', title='유동성 사이클(%)', ticksuffix='%', range=[-l1, l1], fixedrange=True),
                    yaxis2=dict(overlaying='y', side='right', showgrid=False, zeroline=False, title='자산 사이클(%)', ticksuffix='%', range=[-(l2 or 1), (l2 or 1)], fixedrange=True),
                    legend=dict(orientation='h', y=-0.24, x=0.5, xanchor='center', bgcolor='rgba(0,0,0,0)'))
    _collect(f)
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
    head = ''.join(f'<th>{T(f"{m}개월", f"{m}M")}</th>' for m in (0, 1, 2, 3, 6, 9, 12))
    body = ''.join(f'<tr><td>{T(nm, NM_EN.get(nm, nm))}</td>' + ''.join(f'<td>{c:+.2f}</td>' for c in cs) + f'<td>{T(f"{b[0]}개월 ({b[1]:+.2f})", f"{b[0]}M ({b[1]:+.2f})")}</td></tr>' for nm, cs, b in rows)
    return f'<table><tr><th>{T("자산", "Asset")}</th>{head}<th>{T("가장 높은 시차", "Best lag")}</th></tr>{body}</table>'

last = GL.index[-1]
reg = '확장' if CHG['12개월'].iloc[-1] > 0 else '수축'
mom = '개선' if CHG['3개월'].iloc[-1] * 2 > CHG['6개월'].iloc[-1] else '악화'
_usd = lambda v: f"{'+' if v >= 0 else '-'}${abs(v):.2f}tn"
PHASE_EN = {'상승(확장 국면)': 'Rising (expansion)', '고점 통과 후 하락': 'Falling after peak', '하락(수축 국면)': 'Falling (contraction)', '저점 통과 후 회복': 'Recovering after trough'}[PHASE]
cards = [(T('글로벌 유동성', 'Global liquidity'), T(f'{GL.iloc[-1]:.2f}조 달러', f'${GL.iloc[-1]:.2f}tn'), T(f'{last:%Y-%m-%d} 기준', f'as of {last:%Y-%m-%d}')),
         (T('1주 변화', '1W change'), T(f'{WTOT.iloc[-1] * 10:+,.0f}억 달러', f'{WTOT.iloc[-1]:+,.1f}bn'), T(f'{WPCT.iloc[-1]:+.2f}%, 직전 주 {WTOT.iloc[-2] * 10:+,.0f}억', f'{WPCT.iloc[-1]:+.2f}%, prior week {WTOT.iloc[-2]:+,.1f}bn')),
         (T('3개월 변화율', '3M change'), f'{CHG["3개월"].iloc[-1]:+.1f}%', T(f'{(GL.iloc[-1] - GL.iloc[-64]):+.2f}조 달러', _usd(GL.iloc[-1] - GL.iloc[-64]))),
         (T('6개월 변화율', '6M change'), f'{CHG["6개월"].iloc[-1]:+.1f}%', T(f'{(GL.iloc[-1] - GL.iloc[-127]):+.2f}조 달러', _usd(GL.iloc[-1] - GL.iloc[-127]))),
         (T('12개월 변화율', '12M change'), f'{CHG["12개월"].iloc[-1]:+.1f}%', T(f'국면: {reg}, 최근 속도 {mom}', f"Regime: {'expansion' if reg == '확장' else 'contraction'}, momentum {'improving' if mom == '개선' else 'weakening'}")),
         (T('사이클 위상', 'Cycle phase'), T(PHASE, PHASE_EN), T(f'사이클 {CYC.iloc[-1]:+.1f}% (추세 대비), 주된 주기 약 {PERIOD:.1f}년', f'Cycle {CYC.iloc[-1]:+.1f}% vs trend, dominant period about {PERIOD:.1f} years'))]
_CEN = {'연준': 'Fed', '역레포': 'RRP'}
comp = ' · '.join(f'{c} {P[c].iloc[-1]:+.2f}' for c in P.columns)
comp_en = ' · '.join(f'{_CEN.get(c, c)} {P[c].iloc[-1]:+.2f}' for c in P.columns)
assets_now = ''.join(f'<tr><td>{T(nm, NM_EN.get(nm, nm))}</td>' + ''.join(f'<td>{ACHG[h][nm].iloc[-1]:+.1f}%</td>' for h in HORIZONS) + '</tr>' for nm in APX.columns)
DEFAULT_TAB = 2   # 0 주간 변화, 1 3개월, 2 6개월
PANELS = [(T('주간 변화', 'Weekly change'), wpanel())] + [(T(h, H_EN[h]), chart(h)) for h in HORIZONS] + [(T('표준화 겹쳐보기', 'Z-score overlay'), zchart()), (T('사이클', 'Cycle'), cyc_chart())]
now_kst = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=9)
CSS = f"""
body{{margin:0;background:{BG};color:{TXT};font-family:'Nanum Gothic',sans-serif}}
.wrap{{max-width:1200px;margin:0 auto;padding:16px}}
.top{{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}}
h1{{font-size:20px;margin:8px 0 2px}} .sub{{color:#8b9099;font-size:12px;margin-bottom:14px}}
.nav{{display:flex;gap:8px;align-items:center}}
.lnk{{color:#c9cdd4;font-size:13px;text-decoration:none;border:1px solid {GRID};border-radius:4px;padding:6px 12px;background:#23262d}}
.lnk:hover,.lang:hover{{border-color:#5a606b;color:#fff}}
.lang{{font-family:inherit;color:#c9cdd4;font-size:13px;border:1px solid {GRID};border-radius:4px;padding:6px 12px;background:#23262d;cursor:pointer}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin-bottom:14px}}
.card{{background:#23262d;border:1px solid {GRID};border-radius:6px;padding:12px}}
.card .k{{font-size:12px;color:#8b9099}} .card .v{{font-size:22px;font-weight:700;margin:4px 0}} .card .n{{font-size:11px;color:#8b9099}}
.tabs{{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}} .tab{{padding:7px 16px;border:1px solid {GRID};border-radius:4px;cursor:pointer;background:#23262d;font-size:13px}}
.tab.on{{background:#3a3f48;color:#fff;border-color:#5a606b}}
.panel{{display:none}} .panel.on{{display:block}}
h2{{font-size:15px;margin:22px 0 6px}}
table{{border-collapse:collapse;width:100%;font-size:12px}} th,td{{border:1px solid {GRID};padding:6px 8px;text-align:right}}
th{{background:#2a2d33;color:#e6e8eb}} td:first-child,th:first-child{{text-align:left}}
.note{{font-size:11px;color:#8b9099;margin-top:10px;line-height:1.6}}
.foot{{font-size:11px;color:#8b9099;margin:26px 0 8px;padding-top:12px;border-top:1px solid {GRID}}}
.foot .lnk{{padding:2px 8px;font-size:11px}}
.box{{background:#23262d;border:1px solid {GRID};border-radius:6px;padding:20px;max-width:640px}}
.mail{{font-size:18px;font-weight:700;color:#fff;margin:10px 0 16px;word-break:break-all}}
.btn{{display:inline-block;font-family:inherit;font-size:13px;color:#fff;background:#3a3f48;border:1px solid #5a606b;border-radius:4px;padding:8px 16px;text-decoration:none;cursor:pointer}}
label{{display:block;font-size:12px;color:#8b9099;margin:12px 0 4px}}
input,textarea{{width:100%;box-sizing:border-box;font-family:inherit;font-size:13px;color:{TXT};background:{BG};border:1px solid {GRID};border-radius:4px;padding:8px}}
textarea{{min-height:140px;resize:vertical}}
@media (max-width:600px){{h1{{font-size:17px}} .card .v{{font-size:19px}}}}
"""
LANG_JS = r"""
function curLang(){try{if(/lang=en/.test(location.search)||location.hash==='#en')return 'en'}catch(e){}return 'ko'}
let LANG='ko';
function setText(lang){
  document.querySelectorAll('.i18n').forEach(el=>{if(el.dataset.ko===undefined)el.dataset.ko=el.textContent;el.textContent=lang==='en'?el.dataset.en:el.dataset.ko});
  document.documentElement.lang=lang;
  if(document.body.dataset.titleEn){if(!document.body.dataset.titleKo)document.body.dataset.titleKo=document.title;document.title=lang==='en'?document.body.dataset.titleEn:document.body.dataset.titleKo}
  const b=document.getElementById('lang');if(b)b.textContent=lang==='en'?'한국어':'EN';
  document.querySelectorAll('a[data-keep-lang]').forEach(a=>{const base=a.getAttribute('href').split('#')[0];a.setAttribute('href',base+(lang==='en'?'#en':''))});
}
function toggleLang(){LANG=LANG==='en'?'ko':'en';try{history.replaceState(null,'',location.pathname+(LANG==='en'?'#en':''))}catch(e){}applyLang()}
"""
DASH_JS = r"""
function tr(s,lang){if(typeof s!=='string')return s;if(lang!=='en')return s;return Object.prototype.hasOwnProperty.call(I18N,s)?I18N[s]:s}
function plots(){return Array.from(document.querySelectorAll('.js-plotly-plot'))}
function trFig(gd,lang){
  if(!gd._ko){const L=gd.layout;gd._ko={names:gd.data.map(t=>t.name),hts:gd.data.map(t=>t.hovertemplate),
    yt:L.yaxis&&L.yaxis.title?L.yaxis.title.text:undefined,y2t:L.yaxis2&&L.yaxis2.title?L.yaxis2.title.text:undefined,
    rs:(L.xaxis&&L.xaxis.rangeselector&&L.xaxis.rangeselector.buttons||[]).map(b=>b.label),
    um:(L.updatemenus||[]).map(u=>(u.buttons||[]).map(b=>b.label))}}
  const K=gd._ko;
  Plotly.restyle(gd,{name:K.names.map(n=>tr(n,lang)),hovertemplate:K.hts.map(h=>tr(h,lang))});
  const u={};if(K.yt!==undefined)u['yaxis.title.text']=tr(K.yt,lang);if(K.y2t!==undefined)u['yaxis2.title.text']=tr(K.y2t,lang);
  K.rs.forEach((l,i)=>{u['xaxis.rangeselector.buttons['+i+'].label']=tr(l,lang)});
  K.um.forEach((bs,j)=>bs.forEach((l,i)=>{u['updatemenus['+j+'].buttons['+i+'].label']=tr(l,lang)}));
  gd._fitting=true;return Plotly.relayout(gd,u).then(()=>{gd._fitting=false})
}
function dnum(v){return typeof v==='number'?v:Date.parse(String(v).slice(0,10))}
function fitY(gd){
  /* 보이는 기간의 값으로 세로 눈금을 다시 잡는다. 0을 가운데 두어 왼쪽·오른쪽 0선이 항상 같은 높이에 있게 한다. */
  const L=gd._fullLayout;if(!L||!L.xaxis||!L.xaxis.range)return;
  const x0=dnum(L.xaxis.range[0]),x1=dnum(L.xaxis.range[1]);const m={y:0,y2:0};
  (gd._fullData||[]).forEach(t=>{if(t.visible!==true||!t.x||!t.y)return;const ax=t.yaxis==='y2'?'y2':'y';
    if(!t._nx)t._nx=Array.from(t.x,dnum);const nx=t._nx,ys=t.y;
    for(let i=0;i<nx.length;i++){if(nx[i]<x0||nx[i]>x1)continue;const a=Math.abs(ys[i]);if(isFinite(a)&&a>m[ax])m[ax]=a}});
  const nice=v=>{const p=Math.pow(10,Math.floor(Math.log10(v))),f=v/p;return [1,1.2,1.5,2,2.5,3,4,5,6,8,10].find(k=>f<=k)*p};
  const u={};if(m.y>0){const r=nice(m.y*1.08);u['yaxis.range']=[-r,r]}if(L.yaxis2&&m.y2>0){const r=nice(m.y2*1.08);u['yaxis2.range']=[-r,r]}
  if(!Object.keys(u).length)return;gd._fitting=true;Plotly.relayout(gd,u).then(()=>{gd._fitting=false},()=>{gd._fitting=false})
}
function hook(gd){
  if(gd._hooked)return;gd._hooked=true;
  gd.on('plotly_relayout',ev=>{if(gd._fitting||!ev)return;if(Object.keys(ev).some(k=>k.indexOf('xaxis')===0))fitY(gd)});
  gd.on('plotly_restyle',()=>{if(!gd._fitting)fitY(gd)});
  gd.on('plotly_doubleclick',()=>{setTimeout(()=>fitY(gd),0)});
}
function applyLang(){setText(LANG);plots().forEach(gd=>trFig(gd,LANG))}
function sel(i){document.querySelectorAll('.tab').forEach((t,j)=>t.classList.toggle('on',i==j));document.querySelectorAll('.panel').forEach((p,j)=>p.classList.toggle('on',i==j));window.dispatchEvent(new Event('resize'));}
window.addEventListener('load',()=>{LANG=curLang();plots().forEach(gd=>{hook(gd);fitY(gd)});applyLang()});
"""
JS = LANG_JS + DASH_JS

def contact_page():
    u, d = CONTACT
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>문의 - TRIGER 글로벌 유동성</title>
<link href="https://fonts.googleapis.com/css2?family=Nanum+Gothic:wght@400;700&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body data-title-en="Contact - TRIGER Global Liquidity"><div class="wrap">
<div class="top"><h1>{T('문의', 'Contact')}</h1>
<div class="nav"><a class="lnk" href="./" data-keep-lang>{T('대시보드로 돌아가기', 'Back to dashboard')}</a><button class="lang" id="lang" type="button" onclick="toggleLang()">EN</button></div></div>
<div class="sub">{T('TRIGER 글로벌 유동성 대시보드', 'TRIGER Global Liquidity Dashboard')}</div>
<div class="box">
<div>{T('대시보드에 대한 의견, 협업 제안, 매크로와 자산배분에 관한 대화는 아래 메일로 보내 주세요.', 'For feedback on the dashboard, collaboration, or conversations on macro and asset allocation, please reach me by email.')}</div>
<div class="mail" id="mail" data-u="{u}" data-d="{d}">{u} [at] {d}</div>
<a class="btn" id="mailbtn" href="#">{T('메일 보내기', 'Send email')}</a>
<h2>{T('바로 작성하기', 'Write a message')}</h2>
<label for="nm">{T('이름 / 소속', 'Name / organization')}</label><input id="nm" type="text" autocomplete="name">
<label for="sj">{T('제목', 'Subject')}</label><input id="sj" type="text">
<label for="bd">{T('내용', 'Message')}</label><textarea id="bd"></textarea>
<div style="margin-top:14px"><button class="btn" type="button" onclick="compose()">{T('메일 앱에서 열기', 'Open in mail app')}</button></div>
<div class="note">{T('버튼을 누르면 사용 중인 메일 앱이 열리고 작성한 내용이 채워집니다. 이 페이지는 내용을 저장하거나 서버로 보내지 않습니다.', 'Your mail app opens with the message filled in. This page does not store or send anything to a server.')}</div>
</div>
<div class="foot">{T('개인 연구용 비상업 페이지이며 투자 권유가 아닙니다. 이용 책임은 이용자에게 있습니다.', 'A non-commercial personal research page. Not investment advice. Use at your own risk.')}</div>
</div><script>
{LANG_JS}
function addr(){{const m=document.getElementById('mail');return m.dataset.u+'@'+m.dataset.d}}
function applyLang(){{setText(LANG)}}
function compose(){{const n=document.getElementById('nm').value.trim(),s=document.getElementById('sj').value.trim(),b=document.getElementById('bd').value;
  const subj=s||(LANG==='en'?'Inquiry from TRIGER Liquidity':'TRIGER 유동성 문의');const body=(n?(n+'\\n\\n'):'')+b;
  location.href='mailto:'+addr()+'?subject='+encodeURIComponent(subj)+'&body='+encodeURIComponent(body)}}
window.addEventListener('load',()=>{{const m=document.getElementById('mail');m.textContent=addr();document.getElementById('mailbtn').setAttribute('href','mailto:'+addr());LANG=curLang();applyLang()}});
</script>
</body></html>"""

html = f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TRIGER 글로벌 유동성</title>
<meta name="description" content="Global liquidity (Fed - TGA - RRP + ECB + BoJ) vs. asset returns, updated daily. 글로벌 유동성과 자산 변화율, 매일 자동 갱신.">
<link href="https://fonts.googleapis.com/css2?family=Nanum+Gothic:wght@400;700&display=swap" rel="stylesheet">
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>{CSS}</style></head><body data-title-en="TRIGER Global Liquidity"><div class="wrap">
<div class="top"><h1>{T('TRIGER 글로벌 유동성 사이클', 'TRIGER Global Liquidity Cycle')}</h1>
<div class="nav"><a class="lnk" href="liquidity_raw.xlsx" download>{T('원자료 엑셀', 'Raw data (Excel)')}</a><a class="lnk" id="contact" href="contact.html" data-keep-lang>{T('문의', 'Contact')}</a><button class="lang" id="lang" type="button" onclick="toggleLang()">EN</button></div></div>
<div class="sub">{T(f'연준 - TGA - 역레포 + ECB + BoJ (중국 제외) · 매일 자동 갱신 · 마지막 갱신 {now_kst:%Y-%m-%d %H:%M} KST · 구성 {comp}', f'Fed - TGA - RRP + ECB + BoJ (ex-China) · updated daily · last update {now_kst:%Y-%m-%d %H:%M} KST · components {comp_en}')}</div>
<div class="cards">{''.join(f'<div class="card"><div class="k">{k}</div><div class="v">{v}</div><div class="n">{n}</div></div>' for k, v, n in cards)}</div>
<div class="tabs">{''.join(f'<div class="tab{" on" if i == DEFAULT_TAB else ""}" onclick="sel({i})">{lab}</div>' for i, (lab, _) in enumerate(PANELS))}</div>
{''.join(f'<div class="panel{" on" if i == DEFAULT_TAB else ""}" id="p{i}">{body}</div>' for i, (lab, body) in enumerate(PANELS))}
<div class="note">{T(f"3·6·12개월 탭: 면적은 글로벌 유동성의 로그 변화율(왼쪽 축, %), 선은 자산의 같은 기간 로그 변화율(오른쪽 축, %). 기간 버튼(2년·5년·전체)이나 확대로 구간을 바꾸면 세로 눈금이 그 구간에 맞춰 바뀌고, 왼쪽·오른쪽 0선은 항상 같은 높이에 있다. 표준화 탭: 유동성과 자산을 각자 평소 변동 크기로 나눈 점수(z)라 같은 눈금에서 방향을 비교한다. 사이클 탭: 로그 유동성(주간)에서 장기 추세를 빼고 2~6년 주기만 남긴 선이다. 앞뒤 양방향 필터라 최근 몇 달의 값과 위상 판정은 새 자료가 들어오면 바뀔 수 있고, 미래를 예측하는 선이 아니다. 주된 주기는 {YEARS:.0f}년 자료의 스펙트럼 추정이라 거칠다. 범례를 누르면 선을 켜고 끈다.", f"3M/6M/12M tabs: the shaded area is the log change in global liquidity (left axis, %); lines are the log change of each asset over the same period (right axis, %). When you change the period (2Y, 5Y, All) or zoom, the vertical scales refit to that window and the left and right zero lines always stay level. Z-score tab: liquidity and assets are each scaled by their own typical variability, so their direction can be compared on one scale. Cycle tab: weekly log liquidity with the long-run trend removed, keeping only 2-6 year cycles. The filter runs forward and backward, so recent values and the phase reading can change as new data arrive; it is not a forecast. The dominant period is a rough spectral estimate from {YEARS:.0f} years of data. Click a legend item to show or hide a line.")}</div>
<h2>{T('자산 변화율 (현재)', 'Asset changes (current)')}</h2>
<table><tr><th>{T('자산', 'Asset')}</th>{''.join(f'<th>{T(h, H_EN[h])}</th>' for h in HORIZONS)}</tr>{assets_now}</table>
<h2>{T('유동성 6개월 변화가 자산 6개월 변화율을 몇 달 앞서나 (상관)', 'Does the 6M liquidity change lead 6M asset returns, and by how many months? (correlation)')}</h2>
{corr_table()}
<div class="note">{T('상관은 인과가 아니며 표본 기간에 따라 바뀐다. 매매 판단 도구가 아니라 배경 지표다.', 'Correlation is not causation and varies with the sample period. This is a background indicator, not a trading tool.')}{(' ' + T('수신 실패: ', 'Data failures: ') + '; '.join(NOTE)) if NOTE else ''}</div>
<div class="foot">{T('개인 연구용 비상업 페이지이며 투자 권유가 아닙니다. 이용 책임은 이용자에게 있습니다.', 'A non-commercial personal research page. Not investment advice. Use at your own risk.')} · <a class="lnk" id="contact2" href="contact.html" data-keep-lang>{T('문의', 'Contact')}</a></div>
</div><script>
const I18N = {json.dumps(I18N, ensure_ascii=False)};
{JS}
</script>
</body></html>'''
os.makedirs('site', exist_ok=True)
open('site/index.html', 'w', encoding='utf-8').write(html)
open('site/contact.html', 'w', encoding='utf-8').write(contact_page())
print('site/index.html 생성', last.date(), '실패:', NOTE)

# ---- 원자료 엑셀과 메일 (v4) ----
SID_KO = {'WALCL': '연준 총자산(백만 달러, 주간 수요일)', 'WTREGEN': '재무부 일반계정 TGA(십억 달러, 주간)', 'RRPONTSYD': '역레포 잔액(십억 달러, 일간)',
          'ECBASSETSW': 'ECB 총자산(백만 유로, 주간 금요일)', 'DEXUSEU': '달러/유로 환율(일간)', 'JPNASSETS': 'BoJ 총자산(억 엔, 월간)', 'DEXJPUS': '엔/달러 환율(일간)'}
def write_raw_xlsx(path):
    liq = P.copy(); liq.columns = [f'{c}(조 달러)' for c in liq.columns]; liq['글로벌 유동성(조 달러)'] = GL
    for h in HORIZONS: liq[f'{h} 로그 변화율(%)'] = CHG[h]
    liq = liq[liq.index >= pd.Timestamp(START)]; liq.index.name = '날짜'
    ast = pd.DataFrame(index=APX.index)
    for nm in APX.columns:
        ast[f'{nm} 종가'] = APX[nm]
        for h in HORIZONS: ast[f'{nm} {h} 로그 변화율(%)'] = ACHG[h][nm]
    ast = ast[ast.index >= pd.Timestamp(START)]; ast.index.name = '날짜'
    cyc = pd.DataFrame({'사이클(2~6년 대역, 추세 대비 %)': CYC, '추세 제거 로그 유동성(%)': DTR}); cyc.index.name = '주(금요일)'
    raw = pd.concat([pd.DataFrame({'자료': sid, '설명': SID_KO.get(sid, ''), '날짜': s_.index, '값': s_.values}) for sid, s_ in RAW.items()], ignore_index=True)
    summ = pd.DataFrame([('기준일', f'{last:%Y-%m-%d}'), ('글로벌 유동성(조 달러)', f'{GL.iloc[-1]:.3f}')]
                        + [(f'{h} 로그 변화율(%)', f'{CHG[h].iloc[-1]:+.2f}') for h in HORIZONS]
                        + [('사이클 위상', PHASE), ('사이클(추세 대비 %)', f'{CYC.iloc[-1]:+.2f}'), ('주된 주기(년, 추정)', f'{PERIOD:.1f}')]
                        + [(f'구성: {c}(조 달러)', f'{P[c].iloc[-1]:+.3f}') for c in P.columns]
                        + [(f'마지막 자료일: {sid}', f'{RAW[sid].index[-1]:%Y-%m-%d}') for sid in RAW]
                        + [('생성 시각(KST)', f'{now_kst:%Y-%m-%d %H:%M}'), ('대시보드', PAGES_URL)], columns=['항목', '값'])
    note = pd.DataFrame({'설명': [
        '글로벌 유동성 = 연준 총자산 - TGA - 역레포 + ECB 총자산(달러 환산) + BoJ 총자산(달러 환산), 중국 제외, 단위 조 달러.',
        '각 자료는 영업일 기준으로 맞춘 뒤 직전 값으로 채웠다. 변화율은 로그 변화율(%)이며 3·6·12개월은 63·126·252영업일이다.',
        '사이클은 주간 로그 유동성에서 선형 추세를 빼고 2~6년 대역 필터를 앞뒤 양방향으로 적용한 값이라 최근 값은 새 자료가 들어오면 바뀔 수 있다.',
        '출처: FRED(세인트루이스 연준), 야후 파이낸스. 개인 연구용 비상업 자료이며 투자 권유가 아니다.']})
    with pd.ExcelWriter(path, engine='openpyxl') as w:
        summ.to_excel(w, sheet_name='요약', index=False); liq.to_excel(w, sheet_name='유동성_일별'); ast.to_excel(w, sheet_name='자산_일별')
        _wk = pd.concat([WGL.rename('글로벌 유동성(조 달러)'), WTOT.rename('주간 변화(10억 달러)'), WPCT.rename('주간 변화율(%)'), WCH.add_suffix(' 기여(10억 달러)')], axis=1); _wk.index.name = '주(마지막 자료일)'
        _wk.to_excel(w, sheet_name='유동성_주간'); cyc.to_excel(w, sheet_name='사이클_주간'); raw.to_excel(w, sheet_name='FRED_원자료', index=False); note.to_excel(w, sheet_name='설명', index=False)
        for ws in w.book.worksheets:
            ws.freeze_panes = 'B2'
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = min(max(len(str(col[0].value or '')) * 1.6, 12), 46)

def send_mail(subject, text, html_, attach):
    user, pw = os.environ.get('GMAIL_USER', '').strip(), os.environ.get('GMAIL_APP_PASSWORD', '').replace(' ', '').strip()
    if not (user and pw):
        print('메일 생략: 저장소 시크릿 GMAIL_USER, GMAIL_APP_PASSWORD가 없음', flush=True); return False
    import smtplib, ssl
    from email.message import EmailMessage
    m = EmailMessage(); m['Subject'] = subject; m['From'] = user; m['To'] = user
    m.set_content(text); m.add_alternative(html_, subtype='html')
    with open(attach, 'rb') as f:
        m.add_attachment(f.read(), maintype='application', subtype='vnd.openxmlformats-officedocument.spreadsheetml.sheet', filename=os.path.basename(attach))
    with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=ssl.create_default_context(), timeout=60) as sv:
        sv.login(user, pw); sv.send_message(m)
    print('메일 발송 완료:', subject, flush=True); return True

_xlsx = 'site/liquidity_raw.xlsx'
try:
    write_raw_xlsx(_xlsx); print('원자료 엑셀 생성', _xlsx, flush=True)
except Exception as e:
    _xlsx = None; print('원자료 엑셀 실패:', str(e)[:200], flush=True)
FP = {sid: f'{RAW[sid].index[-1]:%Y-%m-%d}' for sid in MAIL_KEYS if sid in RAW}
prev, prev_ok = {}, True
if not DEMO:
    try:
        r_ = requests.get(PAGES_URL.rstrip('/') + '/state.json', timeout=20, headers=UA)
        if r_.status_code == 200: prev = r_.json()
        elif r_.status_code != 404: prev_ok = False
    except Exception as e:
        prev_ok = False; print('직전 상태 읽기 실패(이번에는 메일 보내지 않음):', str(e)[:100], flush=True)
mailed = prev.get('mailed_fp')
changed = [sid for sid in FP if (mailed or {}).get(sid) != FP[sid]]
sent = False
if DEMO or not prev_ok or not _xlsx:
    pass
elif mailed is None or changed:
    _lab = {'WALCL': '연준', 'WTREGEN': 'TGA', 'ECBASSETSW': 'ECB', 'JPNASSETS': 'BoJ'}
    _what = ', '.join(f"{_lab[s_]} {FP[s_]}" for s_ in (changed or FP))
    subj = f"[유동성 원자료] {now_kst:%Y-%m-%d} 갱신 ({_what})"
    _rows = [('글로벌 유동성', f'{GL.iloc[-1]:.2f}조 달러 ({last:%Y-%m-%d} 기준)')] + [(f'{h} 변화율', f'{CHG[h].iloc[-1]:+.1f}%') for h in HORIZONS] + [('사이클 위상', f'{PHASE}, 추세 대비 {CYC.iloc[-1]:+.1f}%'), ('새로 들어온 자료', _what)]
    text = '\n'.join(f'{k}: {v}' for k, v in _rows) + f"\n\n대시보드: {PAGES_URL}\n첨부: 원자료 엑셀(요약, 유동성_일별, 자산_일별, 사이클_주간, FRED_원자료, 설명)\n\n개인 연구용 비상업 자료이며 투자 권유가 아닙니다."
    _th = 'style="background-color:#3a3a3a;color:#ffffff;font-weight:bold;border:1px solid #9a9a9a;padding:6px 8px;text-align:left" bgcolor="#3a3a3a"'
    _td = 'style="border:1px solid #9a9a9a;padding:6px 8px;color:#222222"'
    html_ = (f'<div style="font-family:\'Nanum Gothic\',\'NanumGothic\',sans-serif;font-size:13px;color:#222222">'
             f'<table border="1" cellpadding="6" cellspacing="0" width="100%" style="border-collapse:collapse;border:1px solid #9a9a9a;width:100%;font-family:\'Nanum Gothic\',\'NanumGothic\',sans-serif;font-size:13px">'
             f'<tr><th {_th}>항목</th><th {_th}>값</th></tr>' + ''.join(f'<tr><td {_td}>{k}</td><td {_td}>{v}</td></tr>' for k, v in _rows) + '</table>'
             f'<p>대시보드: <a href="{PAGES_URL}">{PAGES_URL}</a><br>첨부한 엑셀에 요약, 유동성 일별, 자산 일별, 사이클 주간, FRED 원자료, 설명 시트가 있습니다.</p>'
             '<p style="color:#666666;font-size:11px">개인 연구용 비상업 자료이며 투자 권유가 아닙니다.</p></div>')
    try:
        sent = send_mail(subj, text, html_, _xlsx)
    except Exception as e:
        print('메일 발송 실패(다음 갱신에서 다시 시도):', str(e)[:200], flush=True)
else:
    print('메일 생략: 중앙은행 자료가 지난 발송 이후 바뀌지 않음', FP, flush=True)
state = {'fp': FP, 'mailed_fp': (FP if sent else mailed), 'updated_kst': f'{now_kst:%Y-%m-%d %H:%M}'}
open('site/state.json', 'w', encoding='utf-8').write(json.dumps(state, ensure_ascii=False))

