"""특징 계산(검증과 사이트가 같은 코드를 쓴다). 입력: 날짜×종목 종가·고가·저가 DataFrame, SPY 종가 Series."""
import numpy as np, pandas as pd
FEATS = ['d200', 'd50', 'd20', 'slope20', 'rsi14', 'rsi2', 'r5', 'r20', 'momrank', 'hi52', 'atr', 'vol20', 'spy200', 'spy5', 'spy20', 'breadth']

def rsi(C, n):
    d = C.diff(); up = d.clip(lower=0); dn = (-d).clip(lower=0)
    au = up.ewm(alpha=1 / n, adjust=False, min_periods=n).mean(); ad = dn.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return (100 - 100 / (1 + au / ad.replace(0, np.nan))).where(ad != 0, 100.0)

def features(C, H, L, spy):
    C = C.astype('float32'); f = {}
    ma200 = C.rolling(200, min_periods=200).mean(); ma50 = C.rolling(50, min_periods=50).mean(); ma20 = C.rolling(20, min_periods=20).mean()
    f['d200'] = C / ma200 - 1; f['d50'] = C / ma50 - 1; f['d20'] = C / ma20 - 1; f['slope20'] = ma20 / ma20.shift(5) - 1
    f['rsi14'] = rsi(C, 14); f['rsi2'] = rsi(C, 2)
    f['r5'] = C / C.shift(5) - 1; f['r20'] = C / C.shift(20) - 1
    mom = C.shift(21) / C.shift(252) - 1; f['momrank'] = mom.rank(axis=1, pct=True)
    f['hi52'] = C / C.rolling(252, min_periods=200).max() - 1
    pc = C.shift(1); tr = np.maximum(H - L, np.maximum((H - pc).abs(), (L - pc).abs()))
    f['atr'] = tr.rolling(14, min_periods=14).mean() / C
    f['vol20'] = C.pct_change(fill_method=None).rolling(20, min_periods=20).std()
    s = spy.reindex(C.index).ffill()
    one = pd.DataFrame(1.0, index=C.index, columns=C.columns, dtype='float32')
    f['spy200'] = one.mul(s / s.rolling(200).mean() - 1, axis=0); f['spy5'] = one.mul(s / s.shift(5) - 1, axis=0); f['spy20'] = one.mul(s / s.shift(20) - 1, axis=0)
    above = (C > ma50).where(ma50.notna()); f['breadth'] = one.mul(above.mean(axis=1), axis=0)
    ok = (C >= 5) & (C.notna().rolling(252, min_periods=252).sum() >= 252)
    return {k: v.astype('float32') for k, v in f.items()}, ok

def cell_id(f):
    """방식 A 칸 번호(0~95). NaN 이면 -1"""
    t = (f['d200'] > 0).astype('int8')
    r = f['rsi2']; p = pd.DataFrame(np.select([r < 10, r < 30, r < 70], [0, 1, 2], 3), index=r.index, columns=r.columns)
    m = f['momrank']; q = pd.DataFrame(np.select([m <= 1 / 3, m <= 2 / 3], [0, 1], 2), index=m.index, columns=m.columns)
    s = (f['spy200'] > 0).astype('int8'); b = (f['breadth'] >= 0.5).astype('int8')
    cid = ((((t * 4 + p) * 3 + q) * 2 + s) * 2 + b)
    bad = f['d200'].isna() | r.isna() | m.isna() | f['spy200'].isna()
    return cid.where(~bad, -1).astype('int16')

def cell_label(cid):
    b = cid % 2; cid //= 2; s = cid % 2; cid //= 2; q = cid % 3; cid //= 3; p = cid % 4; t = cid // 4
    return ' · '.join([('200일선 위' if t else '200일선 아래'), ['단기 과매도(RSI2<10)', '눌림(RSI2 10~30)', '중립(RSI2 30~70)', '단기 과열(RSI2>70)'][p], ['1년 상승률 하위', '1년 상승률 중간', '1년 상승률 상위'][q],
                       ('시장 200일선 위' if s else '시장 200일선 아래'), ('시장폭 넓음' if b else '시장폭 좁음')])
