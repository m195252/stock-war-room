"""
股票戰情室 dashboard.py
啟動：.venv\\Scripts\\python.exe -X utf8 -m streamlit run dashboard.py
"""
import datetime as dt
import html
import io
import os
import re

import pandas as pd
import requests
import streamlit as st
import yfinance as yf
from lightweight_charts.widgets import StreamlitChart

st.set_page_config(page_title="股票戰情室", layout="wide")

# ------------------------------------------------------------------
# 欄位自動對應
# ------------------------------------------------------------------
COL_CANDIDATES = {
    "note": ["備註", "A欄備註", "Unnamed: 0"],
    "code": ["代號", "B欄代號", "股票代號", "code", "ticker"],
    "name": ["股票名稱", "C欄股票名稱", "名稱", "name"],
    "cat": ["分類", "D欄分類", "類別"],
    "support": ["絕對支撐", "支撐價格", "E欄支撐價格", "支撐"],
    "resistance": ["波段壓力", "壓力價格", "F欄壓力價格", "壓力"],
    "price": ["目前股價", "G欄目前股價", "現價"],
    "status": ["狀態判斷", "H欄狀態判斷", "狀態"],
    "brk": ["突破狀態/日期", "突破狀態", "I欄突破狀態/日期"],
    "dev": ["乖離率(距離壓力%)", "乖離率", "J欄乖離率(距離壓力%)"],
    "kbar": ["當天強弱", "K欄當天強弱"],
    "chg": ["當天漲跌", "L欄當天漲跌"],
    "op": ["操作建議", "M欄操作建議"],
    "vol": ["量價", "N欄量價"],
    "season": ["季線", "O欄季線"],
    "month": ["月線", "P欄月線"],
    "upd": ["上次更新日期", "更新日期", "更新時間", "最後更新", "更新"],
    "lvl": ["本期改線日", "改線日", "最近改線日"],
}
PREFIX_FALLBACK = {"brk": "突破狀態", "dev": "乖離率", "support": "支撐", "resistance": "壓力"}


def pick_col(df, key):
    for c in COL_CANDIDATES[key]:
        if c in df.columns:
            return c
    pre = PREFIX_FALLBACK.get(key)
    if pre:
        for c in df.columns:
            if str(c).startswith(pre):
                return c
    return None


def norm_lvl(x):
    """把「10/21」「2026/10/21」「2026-10-21」都整理成 MM/DD；看不懂回傳空字串。"""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return ""
    nums = re.findall(r"\d+", str(x))
    if len(nums) >= 2:
        m_, d_ = int(nums[-2]), int(nums[-1])
        if 1 <= m_ <= 12 and 1 <= d_ <= 31:
            return f"{m_:02d}/{d_:02d}"
    return ""


def to_num(s):
    return pd.to_numeric(s.astype(str).str.replace(",", "", regex=False), errors="coerce")


def _now_tw():
    """台北時間（UTC+8）的 月/日 時:分。"""
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=8)).strftime("%m/%d %H:%M")


FETCHED_AT = ""
LATEST_LVL = ""


@st.cache_data(ttl=300, show_spinner=False)
def load_csv(source):
    for enc in ("utf-8-sig", "utf-8", "cp950"):
        try:
            if hasattr(source, "seek"):
                source.seek(0)
            return pd.read_csv(source, encoding=enc, dtype=str), _now_tw()
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise ValueError("無法解讀 CSV 編碼")


# ------------------------------------------------------------------
# 歷史股價（yfinance → FinMind 備援）
# ------------------------------------------------------------------
def _normalize(hist):
    hist = hist.reset_index()
    dcol = "Date" if "Date" in hist.columns else hist.columns[0]
    hist[dcol] = pd.to_datetime(hist[dcol]).dt.tz_localize(None)
    return hist.rename(columns={
        dcol: "time", "Open": "open", "High": "high",
        "Low": "low", "Close": "close", "Volume": "volume",
    })[["time", "open", "high", "low", "close", "volume"]]


@st.cache_data(ttl=900, show_spinner="抓取歷史資料中…")
def fetch_history(code: str, period: str = "6mo", override: str = ""):
    tickers = [override] if override else [f"{code}.TW", f"{code}.TWO"]
    errors = []
    for ticker in tickers:
        try:
            hist = yf.Ticker(ticker).history(period=period, auto_adjust=False)
            if hist is not None and not hist.empty:
                return _normalize(hist), ticker
            errors.append(f"{ticker} history(): 回傳空資料")
        except Exception as e:
            errors.append(f"{ticker} history(): {type(e).__name__}: {e}")
        try:
            dl = yf.download(ticker, period=period, auto_adjust=False,
                             progress=False, multi_level_index=False)
            if dl is not None and not dl.empty:
                return _normalize(dl), ticker
            errors.append(f"{ticker} download(): 回傳空資料")
        except Exception as e:
            errors.append(f"{ticker} download(): {type(e).__name__}: {e}")
    if not override:
        try:
            start = (dt.date.today() - dt.timedelta(days=190)).isoformat()
            r = requests.get(
                "https://api.finmindtrade.com/api/v4/data",
                params={"dataset": "TaiwanStockPrice", "data_id": code, "start_date": start},
                timeout=15,
            )
            r.raise_for_status()
            rows = r.json().get("data", [])
            if rows:
                fm = pd.DataFrame(rows)
                out = pd.DataFrame({
                    "time": pd.to_datetime(fm["date"]),
                    "open": fm["open"], "high": fm["max"], "low": fm["min"],
                    "close": fm["close"], "volume": fm["Trading_Volume"],
                })
                out = out[out["close"] > 0].reset_index(drop=True)
                if not out.empty:
                    return out, f"{code} (FinMind)"
            errors.append("FinMind: 回傳空資料")
        except Exception as e:
            errors.append(f"FinMind: {type(e).__name__}: {e}")
    raise RuntimeError("\n".join(errors))


# ------------------------------------------------------------------
# 卡片樣式
# ------------------------------------------------------------------
CSS = """
<style>
.card{background:#1e2119;border:1px solid #33362a;border-radius:14px;padding:16px 20px;color:#eeeee2;}
.card.hot{border-color:rgba(229,83,60,.55);}
.card.bad{border-color:rgba(76,154,106,.6);}
.c-code{font-size:12px;color:#8a8d7c;}
.c-name{font-size:21px;font-weight:600;margin:2px 0 8px;}
.pill{display:inline-block;font-size:12px;border:1px solid #3a3d30;border-radius:14px;padding:2px 10px;margin-right:6px;color:#b5b8a6;}
.pill.note{background:#3f3520;border-color:#4d4125;color:#d1a13a;}
.lbl{display:flex;justify-content:space-between;align-items:baseline;font-size:12px;color:#8a8d7c;margin:14px 0 6px;}
.lbl b{color:#b5b8a6;}
.lbl .now{color:#fff;font-size:17px;font-weight:600;}
.bar{position:relative;height:8px;border-radius:5px;background:#2a2c22;}
.bar .mid{position:absolute;top:0;bottom:0;background:#242719;border-left:1px solid #3a3d30;border-right:1px solid #3a3d30;}
.bar .dot{position:absolute;top:50%;width:14px;height:14px;border-radius:50%;transform:translate(-50%,-50%);border:2px solid #12130f;}
.dot.hot{background:#e5533c;}.dot.ok{background:#4c9a6a;}.dot.mid{background:#d1a13a;}
.dev{display:flex;justify-content:space-between;font-size:12px;color:#8a8d7c;margin-top:8px;}
.dev b{color:#b5b8a6;}
.tags{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px;}
.tag{font-size:12px;padding:3px 10px;border-radius:6px;}
.t-hot,.t-bad{background:#5a3327;color:#ff9b83;}
.t-warn{background:#3f3520;color:#e8c877;}
.t-ok{background:#233a2c;color:#8fd6a7;}
.t-info{background:#233746;color:#8fc7e8;}
.t-mild{background:#2a2c22;color:#b5b8a6;}
.t-neutral{background:#2a2c22;color:#8a8d7c;}
.t-op{background:#233746;color:#8fc7e8;font-weight:600;}
.judge{font-size:15px;font-weight:600;margin-top:12px;}
.judge.hot{color:#e5533c;}.judge.bad{color:#4c9a6a;}.judge.neutral{color:#b5b8a6;}

.heats{display:flex;flex-wrap:wrap;gap:5px;margin:2px 0 8px;}
.heat{flex:1 1 76px;max-width:104px;border-radius:7px;padding:4px 8px;color:#ffffff;border:1px solid #33362a;border-left-width:4px;line-height:1.25;}
.h-name{font-size:11.5px;font-weight:600;color:#ffffff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.h-val{font-size:15px;font-weight:700;margin:0;color:#ffffff;}
.h-sub{font-size:10px;color:#e6e8da;}
.card.pass{border-color:#2f7a3f;box-shadow:inset 4px 0 0 #2f7a3f;}
.gates{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:12px;padding-top:12px;border-top:1px solid #33362a;}
.gcell{font-size:12.5px;color:#eeeee2;line-height:1.5;}
.gt{font-size:11px;color:#8a8d7c;margin-bottom:4px;}
.gb{display:inline-block;font-size:12px;border-radius:12px;padding:2px 10px;margin-top:4px;}
.gb.ok{background:#233a2c;color:#8fd6a7;}
.gb.no{background:#5a3327;color:#ff9b83;}
.gwhy{color:#8a8d7c;font-size:11.5px;margin-top:3px;}
.gna{color:#6c6f5f;}
.kb{display:inline-flex;align-items:center;gap:8px;}
.gtag{font-size:12px;border-radius:12px;padding:2px 10px;margin-left:10px;}
.gtag.pass{background:#233a2c;color:#8fd6a7;border:1px solid #2f5a3f;}
.gtag.stop{background:#2a2c22;color:#b5b8a6;}
.tip{background:#3f3520;color:#e8c877;border-radius:8px;padding:8px 12px;font-size:12.5px;margin-top:10px;}
.block-container [data-testid="stVerticalBlock"]{gap:0.35rem;}
.rh,.rw{display:grid;grid-template-columns:2.8fr .6fr .8fr 1.5fr 2.3fr;gap:10px;align-items:center;}
.rh{padding:6px 14px;color:#6b7280;font-size:13px;border-bottom:1px solid #e5e7eb;}
.rw{padding:7px 14px 3px;color:#1f2933;font-size:14px;line-height:1.35;}
.rw .cd{color:#8b909a;margin-right:8px;font-size:14px;}
.rw .nm{font-size:16px;font-weight:600;color:#1f2933;}
.rw .l1{display:flex;align-items:center;flex-wrap:wrap;gap:6px;}
.rw .sub{color:#8b909a;font-size:12.5px;margin-top:1px;}
.rw .gtag{font-size:12px;border-radius:12px;padding:1px 9px;margin-left:4px;}
.rw .gtag.pass{background:#d3e9d0;color:#1f6b21;border:1px solid #b9dcb5;}
.rw .gtag.stop{background:#eef0ea;color:#6b7280;}
.rw .gtag.note{background:#fdf0cf;color:#8a6410;}
.rw .pb{display:inline-block;background:#d3e9d0;color:#2a7a2f;border-radius:12px;padding:1px 9px;font-size:12.5px;}
.rw .fb{display:inline-block;background:#fde8e8;color:#c0392b;border-radius:12px;padding:1px 9px;font-size:12.5px;}
.rw .why{color:#8b909a;font-size:11.5px;margin-top:2px;}
.rw .gna{color:#b3b7bf;}
.rw .kb{display:inline-flex;align-items:center;gap:8px;}
.rw2{background:#ffffff;border-bottom:1px solid #e9ece4;border-left:4px solid transparent;color:#1f2933;}
.rw2.pass{background:#e4efe1;border-left-color:#1f6b21;}
.rx{display:flex;align-items:center;gap:18px;padding:2px 14px 9px;}
.pbw{width:100%;max-width:560px;}
.pbw .bn,.pbw .bl{position:relative;height:16px;font-size:12px;color:#8b909a;}
.pbw .bn span,.pbw .bl span{position:absolute;transform:translateX(-50%);white-space:nowrap;}
.pbw .bn{margin-bottom:3px;}
.pbw .mk{position:absolute;top:-3px;bottom:-3px;width:2px;margin-left:-1px;background:#7c8c6b;border-radius:1px;}
.pbw .bn b{color:#374151;font-weight:600;}
.pbw .bar{position:relative;height:7px;border-radius:4px;background:#d3d9cb;}
.pbw .bar .mid{position:absolute;top:0;bottom:0;background:#aebf9d;border:0;border-radius:4px;}
.pbw .bar .dot{position:absolute;top:50%;width:13px;height:13px;border-radius:50%;transform:translate(-50%,-50%);border:2px solid #ffffff;box-shadow:0 0 0 1px rgba(0,0,0,.18);}
.pbw .bar .dot.hot{background:#e04a4a;}.pbw .bar .dot.ok{background:#1f8a3c;}.pbw .bar .dot.mid{background:#d19a2a;}
.pbw .bl{margin-top:5px;font-size:11.5px;}
.pbw .bl b{color:#4b5563;font-weight:600;}
.rx .tags{display:flex;flex-wrap:wrap;gap:6px;margin-top:0;}
.rx .tag{font-size:12px;padding:2px 9px;border-radius:6px;}
.rx .t-hot,.rx .t-bad{background:#fde4df;color:#b83a24;}
.rx .t-warn{background:#fdf0cf;color:#8a6410;}
.rx .t-ok{background:#dff0dc;color:#2a7a2f;}
.rx .t-info,.rx .t-op{background:#dcebf7;color:#1f5f94;}
.rx .t-op{font-weight:600;}
.rx .t-mild{background:#eef0ea;color:#5c6270;}
.rx .t-neutral{background:#eef0ea;color:#7a808c;}
.c1{display:flex;gap:12px;align-items:flex-start;}
.cdb{min-width:86px;}
.rw .cdb .cd{margin-right:0;line-height:1.3;}
.rw .cdb .up{font-size:11px;color:#9aa0aa;white-space:nowrap;line-height:1.3;margin-top:1px;}
.nmb{min-width:0;}
.mini-title{display:inline-flex;align-items:center;background:#ffffff;border:1px solid #d9dcd2;border-radius:10px;padding:8px 14px;font-size:15px;font-weight:600;color:#1b2a1b;margin:6px 0 4px;}
.stg{background:#fbfcf8;border:1px solid #dde3d3;border-radius:10px;padding:10px 12px;color:#1f2933;}
.stg-h{font-weight:600;font-size:14px;margin-bottom:8px;}
.stg-st{background:#e8efe0;color:#3b5a2a;border-radius:10px;padding:1px 9px;font-size:12.5px;font-weight:500;}
.zns{display:flex;gap:10px;flex-wrap:wrap;}
.zn{flex:1 1 260px;border:1px solid #e1e5d8;border-radius:8px;padding:8px 10px;background:#ffffff;opacity:.72;}
.zn.on{border-color:#2f7a3f;background:#eef6ea;opacity:1;}
.zt{font-size:12.5px;color:#6b7280;}
.zr{font-size:14px;margin-top:2px;}
.zh{font-size:12px;color:#8b909a;margin-top:1px;}
.zc{font-size:12.5px;margin-top:5px;color:#374151;}
.calc{display:flex;flex-wrap:wrap;gap:16px;align-items:center;font-size:14px;color:#1f2933;padding-top:6px;}
.pos{color:#d9363e;}.neg{color:#1a7f37;}.muted{color:#8b909a;font-size:12.5px;}
.ml{display:none;}
@media (max-width:640px){
  .rh{display:none;}
  .rw{grid-template-columns:1fr 1fr;grid-template-areas:"c1 c1" "trig price" "vol k";gap:6px 10px;padding:9px 10px 2px;align-items:stretch;}
  .rw .c1{grid-area:c1;flex-direction:column;gap:2px;}
  .rw .cdb{display:flex;gap:10px;align-items:baseline;min-width:0;}
  .rw .nm{font-size:17px;}
  .g-trig{grid-area:trig;} .g-price{grid-area:price;}
  .g-price div{display:inline-block;margin-left:8px;}
  .g-vol{grid-area:vol;} .g-k{grid-area:k;}
  .g-trig,.g-price,.g-vol,.g-k{background:rgba(255,255,255,.55);border-radius:8px;padding:6px 8px;}
  .ml{display:block;font-size:11px;color:#8b909a;margin-bottom:2px;}
  .g-price b{font-size:17px;}
  .rx{padding:2px 10px 10px;}
  .pbw{max-width:100%;}
  .zn{flex:1 1 100%;}
  div[data-testid="stColumn"]:has([data-testid="stLinkButton"]) > div[data-testid="stVerticalBlock"],
  div[data-testid="column"]:has([data-testid="stLinkButton"]) > div[data-testid="stVerticalBlock"]{flex-direction:row;gap:8px;}
  div[data-testid="stColumn"]:has([data-testid="stLinkButton"]) [data-testid="stElementContainer"],
  div[data-testid="column"]:has([data-testid="stLinkButton"]) [data-testid="element-container"]{flex:1;}
}
.refwrap{opacity:.72;}
.reftag{font-size:11px;color:#8b909a;border:1px dashed #c3c7cf;border-radius:8px;padding:0 6px;margin-right:6px;white-space:nowrap;}
.rw .cdb .lv{font-size:11px;color:#8b909a;white-space:nowrap;line-height:1.3;}
.rw .cdb .lv.hot{color:#d9363e;font-weight:600;}
</style>
"""


def tag_class(text: str) -> str:
    t = text or ""
    for key, cls in (("❌", "t-bad"), ("🔥", "t-hot"), ("⚠️", "t-warn"), ("✅", "t-ok"),
                     ("🎯", "t-info"), ("⬛", "t-bad"), ("🟥", "t-hot"), ("🔨", "t-ok"),
                     ("🦶", "t-mild"), ("☁️", "t-mild"), ("💧", "t-neutral"),
                     ("↗️", "t-ok"), ("↘️", "t-bad"), ("➡️", "t-neutral")):
        if key in t:
            return cls
    return "t-neutral"


def g(row, cols, key):
    c = cols.get(key)
    if not c or c not in row.index or pd.isna(row[c]):
        return ""
    return str(row[c]).strip()


def fmt(v):
    return "-" if v is None or pd.isna(v) else f"{v:,.2f}".rstrip("0").rstrip(".")



def eval_gates(row, cols, b_relax_month=False, b_relax_season=False):
    """四道關卡（CSV 模式，依序過濾）。回傳各關結果與未通過原因。"""
    brk, op = g(row, cols, "brk"), g(row, cols, "op")
    season, month = g(row, cols, "season"), g(row, cols, "month")
    vol, kbar = g(row, cols, "vol"), g(row, cols, "kbar")
    r = {"trigger": None, "g2": False, "g3": (False, ""), "g4": (False, ""),
         "g3_text": vol, "g4_text": kbar, "passed": False, "g2_reason": "",
         "season_above": None}

    # 季線之上／之下：現價 >= 季線價 即為之上（沒有季線價時，以「跌破季線」字樣判斷）
    price_v = row[cols["price"]] if cols.get("price") else None
    m_s = re.search(r"季線價[:：]\s*([\d,\.]+)", season)
    season_px = float(m_s.group(1).replace(",", "")) if m_s else None
    if season_px is not None and price_v is not None and pd.notna(price_v):
        r["season_above"] = bool(price_v >= season_px)
    elif season:
        r["season_above"] = "跌破季線" not in season
    else:
        r["season_above"] = False

    # 第一關：啟動條件（B 回測優先）
    if "回測支撐" in brk or (("已突破" in brk or "昨日突破" in brk) and "回測買點" in op):
        r["trigger"] = "B"
    elif "今日突破" in brk:
        r["trigger"] = "A"
    else:
        return r

    # 第二關：大環境（季線之上 且 月線之上／守穩回測）
    season_ok = bool(r["season_above"])
    month_ok = ("月線之上" in month) or ("守穩回測" in month)
    if r["trigger"] == "B":          # 回測：可在側邊欄放寬第二關
        season_ok = season_ok or b_relax_season
        month_ok = month_ok or b_relax_month
    r["g2"] = season_ok and month_ok
    if not r["g2"]:
        why = []
        if not season_ok:
            why.append("季線之下")
        if not month_ok:
            why.append("月線不在上方／未守穩")
        r["g2_reason"] = "、".join(why)
        # 不 return：第三、四關仍照樣評估，供畫面參考；計分與是否通過仍依序判斷

    # 第三關：量價
    if r["trigger"] == "A":
        if "量縮" in vol or "窒息量" in vol:
            r["g3"] = (False, "突破不可量縮／窒息量")
        elif "溫和放量" in vol or "爆量警示" in vol:
            r["g3"] = (True, "")
        else:
            r["g3"] = (False, "突破需溫和放量或爆量警示")
    else:
        if "爆量警示" in vol:
            r["g3"] = (False, "回測不可爆量")
        elif any(k in vol for k in ("量縮", "窒息量", "溫和放量", "正常量")):
            r["g3"] = (True, "")
        else:
            r["g3"] = (False, "回測需量縮、窒息量、溫和放量或正常量")

    # 第四關：K 棒
    if r["trigger"] == "A":
        if "實體殺盤" in kbar:
            r["g4"] = (False, "實體殺盤，剔除")
        elif "實體紅K" in kbar:
            r["g4"] = (True, "")
        else:
            r["g4"] = (False, "突破需實體紅K")
    else:
        if "實體殺盤" in kbar:
            r["g4"] = (False, "實體殺盤，剔除")
        elif "強力收腳" in kbar or "略有支撐" in kbar:
            r["g4"] = (True, "")
        else:
            r["g4"] = (False, "回測需強力收腳或略有支撐")

    r["passed"] = bool(r["g2"] and r["g3"][0] and r["g4"][0])
    return r


def candle_svg(kbar, chg):
    """依試算表的 K 棒分類、下影線 % 與漲跌方向，畫出示意 K 棒。
    漲＝紅色實心；跌＝綠框空心。"""
    RED, GRN = "#e04a4a", "#1a7f37"
    m = re.search(r"\((\d+)%\)", kbar)
    p = int(m.group(1)) / 100 if m else None          # 下影線佔整根 K 的比例

    if "實體紅K" in kbar:
        up, (top, body, low) = True, (0.06, 0.80, 0.14)
    elif "實體殺盤" in kbar:
        up, (top, body, low) = False, (0.06, 0.80, 0.14)
    else:
        up = chg >= 0
        if "強力收腳" in kbar:
            low = p if p is not None else 0.6
            rest = 1 - low
            body = max(0.05, rest * 0.45)
            top = max(0.0, rest - body)
        elif "略有支撐" in kbar:
            low = p if p is not None else 0.4
            rest = 1 - low
            body = rest * 0.55
            top = rest - body
        else:                                          # 震盪整理 / 其他
            top, body, low = 0.33, 0.30, 0.37

    Y0, H = 3, 40                                      # 畫布高 46，上下各留 3px
    y_top = Y0 + top * H
    y_bot = y_top + max(3.0, body * H)
    y_end = Y0 + H
    col = RED if up else GRN
    fill = col if up else "#ffffff"
    svg = []
    if top > 0.001:
        svg.append(f'<line x1="10" y1="{Y0}" x2="10" y2="{y_top:.1f}" stroke="{col}" stroke-width="2"/>')
    if low > 0.001:
        svg.append(f'<line x1="10" y1="{y_bot:.1f}" x2="10" y2="{y_end}" stroke="{col}" stroke-width="2"/>')
    svg.append(
        f'<rect x="4" y="{y_top:.1f}" width="12" height="{y_bot - y_top:.1f}" rx="2" '
        f'fill="{fill}" stroke="{col}" stroke-width="2"/>'
    )
    return f'<svg width="20" height="46" viewBox="0 0 20 46">{"".join(svg)}</svg>'


def chg_num(row, cols):
    try:
        return float(g(row, cols, "chg").replace("%", ""))
    except ValueError:
        return 0.0


def chg_span(v):
    color = "#d9363e" if v > 0 else ("#1a7f37" if v < 0 else "#6b7a6b")
    return f'<span style="color:{color}">{v:+.1f}%</span>'


def vol_ratio(text):
    m_ = re.search(r"\(([\d.]+)x\)", text or "")
    return float(m_.group(1)) if m_ else 0.0


def gate_score(r):
    """依序通過幾道關卡（0～4）。"""
    if not r["trigger"]:
        return 0
    if not r["g2"]:
        return 1
    if not r["g3"][0]:
        return 2
    if not r["g4"][0]:
        return 3
    return 4


def _plain(t):
    t = re.sub(r"[\(（].*?[\)）]", "", t or "")
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", t)


def clean_vol(t):
    m_ = re.search(r"\(([\d.]+)x\)", t or "")
    label = _plain(t)
    return f"{m_.group(1)}x {label}" if m_ else label


def clean_kbar(t):
    return _plain(t)


def short_env(text, kind):
    t = text or ""
    pat = r"季線(?:上揚|下彎|走平)" if kind == "season" else r"月線之上|跌破月線|守穩回測"
    m = re.search(pat, t)
    return m.group(0) if m else t.split("|")[0].strip()


def stop_reason(r):
    if not r["trigger"]:
        return "無啟動條件（突破狀態／操作建議不符）"
    if not r["g2"]:
        return r["g2_reason"]
    if not r["g3"][0]:
        return r["g3"][1]
    return r["g4"][1]


HEADER_HTML = (
    '<div class="rh"><div>股票</div><div>觸發</div><div>現價</div>'
    '<div>第三關 量價</div><div>第四關 K 棒</div></div>'
)


def row_html(row, cols, gate):
    e = html.escape
    price = row[cols["price"]] if cols["price"] else None
    sup = row[cols["support"]] if cols["support"] else None
    res = row[cols["resistance"]] if cols["resistance"] else None
    cv = chg_num(row, cols)
    upd = g(row, cols, "upd") or FETCHED_AT
    lv = g(row, cols, "lvl")
    lv_html = (f'<div class="lv{" hot" if lv and lv == LATEST_LVL else ""}">改線 {e(lv)}</div>' if lv else "")

    # 名稱下方：分類・季線之上/之下・月線
    sa = gate.get("season_above")
    season_s = ("季線之上" if sa else "季線之下") if sa is not None else short_env(g(row, cols, "season"), "season")
    month_s = short_env(g(row, cols, "month"), "month")
    env = []
    for txt, bad in ((season_s, "之下" in season_s or "下彎" in season_s), (month_s, "跌破" in month_s)):
        if txt:
            env.append(f'<span style="color:#b45309">{e(txt)}</span>' if bad else e(txt))
    note = g(row, cols, "note")
    if note:
        env.append(f'<span style="color:#8a6410">📝{e(note)}</span>')
    sub = "・".join([e(g(row, cols, "cat") or "未分類")] + env)

    sc = gate_score(gate)
    if gate["passed"]:
        cls = "pass"
        kind = "突破・追強勢" if gate["trigger"] == "A" else "回測・買防守"
        tag = f'<span class="gtag pass">✅ 通過四關｜{kind}</span>'
    else:
        cls = ""
        tag = f'<span class="gtag stop" title="{e(stop_reason(gate))}">止於第 {sc + 1} 關</span>'

    def badge(ok, why=""):
        if ok:
            return '<span class="pb">✓ 通過</span>'
        extra = f'<div class="why">{e(why)}</div>' if why else ""
        return f'<span class="fb">✗ 未通過</span>{extra}'

    na = '<span class="gna">—</span>'
    trig = gate["trigger"]
    if trig:
        dot_c = "#2f6fdc" if trig == "A" else "#e8672c"
        c2 = f'<span style="color:{dot_c}">●</span> {"突破" if trig == "A" else "回測"}'
    else:
        c2 = na
    price_txt = fmt(price) if price is not None else "-"
    c3 = f'<b>{price_txt}</b><div>{chg_span(cv)}</div>'
    vol_txt = e(clean_vol(gate["g3_text"]) or "-")
    kb_inner = (f'<span class="kb">{candle_svg(gate["g4_text"], cv)}'
                f'<span>{e(clean_kbar(gate["g4_text"]) or "-")}</span></span>')
    if not trig:                      # 沒有啟動條件：只列出原始標籤，不判定
        c4 = f'<span class="gna">{vol_txt}</span>'
        c5 = kb_inner
    elif gate["g2"]:
        c4 = f'<span>{vol_txt}</span> {badge(*gate["g3"])}'
        c5 = f'{kb_inner} {badge(*gate["g4"])}'
    else:                             # 第二關已淘汰：第三、四關結果僅供參考
        tag_ref = '<span class="reftag" title="第二關未通過，以下僅供參考，不計入關卡">參考</span>'
        c4 = f'<span class="refwrap">{tag_ref}<span>{vol_txt}</span> {badge(*gate["g3"])}</span>'
        c5 = f'<span class="refwrap">{tag_ref}{kb_inner} {badge(*gate["g4"])}</span>'

    # ---- 支撐壓力價格條 + 與支撐／壓力的乖離率 ----
    ok = all(v is not None and pd.notna(v) for v in (sup, res, price)) and res > sup
    if ok:
        span = res - sup
        lo, hi = sup - span * 0.18, res + span * 0.18
        pos = min(98, max(2, (price - lo) / (hi - lo) * 100))
        sp, rp = (sup - lo) / (hi - lo) * 100, (res - lo) / (hi - lo) * 100
        dot = "hot" if price >= res else ("ok" if price <= sup else "mid")
    else:
        pos, sp, rp, dot = 50, 35, 65, "mid"

    def pct_txt(a, b):
        if a is None or b is None or pd.isna(a) or pd.isna(b) or b == 0:
            return "-"
        return f"{(a - b) / b * 100:+.1f}%"

    bar = (
        '<div class="pbw">'
        f'<div class="bn"><span style="left:{sp:.1f}%">支撐 <b>{fmt(sup)}</b></span>'
        f'<span style="left:{rp:.1f}%">壓力 <b>{fmt(res)}</b></span></div>'
        '<div class="bar">'
        f'<div class="mid" style="left:{sp:.1f}%;width:{max(0, rp - sp):.1f}%"></div>'
        f'<div class="mk" style="left:{sp:.1f}%"></div><div class="mk" style="left:{rp:.1f}%"></div>'
        f'<div class="dot {dot}" style="left:{pos:.1f}%"></div></div>'
        f'<div class="bl"><span style="left:{sp:.1f}%">距支撐 <b>{pct_txt(price, sup)}</b></span>'
        f'<span style="left:{rp:.1f}%">距壓力 <b>{pct_txt(price, res)}</b></span></div></div>'
    )

    return (
        f'<div class="rw2 {cls}"><div class="rw">'
        f'<div class="c1"><div class="cdb"><div class="cd">{e(g(row, cols, "code"))}</div>'
        f'<div class="up">更新 {e(upd)}</div>{lv_html}</div>'
        f'<div class="nmb"><div class="l1"><b class="nm">{e(g(row, cols, "name"))}</b>{tag}</div>'
        f'<div class="sub">{sub}</div></div></div>'
        f'<div class="g-trig"><span class="ml">觸發</span>{c2}</div>'
        f'<div class="g-price"><span class="ml">現價</span>{c3}</div>'
        f'<div class="g-vol"><span class="ml">第三關 量價</span>{c4}</div>'
        f'<div class="g-k"><span class="ml">第四關 K 棒</span>{c5}</div></div>'
        f'<div class="rx">{bar}</div></div>'
    )


# ------------------------------------------------------------------
# 交易策略 1：突破後回測（條件取自試算表欄位）
# ------------------------------------------------------------------
def tick_size(p):
    """台股跳動單位。"""
    if p < 10:
        return 0.01
    if p < 50:
        return 0.05
    if p < 100:
        return 0.1
    if p < 500:
        return 0.5
    if p < 1000:
        return 1.0
    return 5.0


def tick_round(p):
    t = tick_size(p)
    return round(round(p / t) * t, 2)


def strategy_info(row, cols, band):
    """符合條件才回傳策略資料，否則 None。band 為進場區間（%）。"""
    brk, op = g(row, cols, "brk"), g(row, cols, "op")
    broke = any(k in brk for k in ("今日突破", "昨日突破", "已突破"))
    retest = ("回測支撐" in brk) or ("回測買點" in op)
    if not (broke or retest):
        return None
    sup = row[cols["support"]] if cols["support"] else None
    res = row[cols["resistance"]] if cols["resistance"] else None
    price = row[cols["price"]] if cols["price"] else None
    if any(v is None or pd.isna(v) for v in (sup, res, price)) or res <= sup:
        return None
    b = band / 100
    if price >= res:
        state, focus = "已突破・站穩壓力價", "res"
    elif price <= sup * (1 + b):
        state, focus = "跌回支撐價", "sup"
    else:
        state, focus = "突破後回測中", "both"
    zones = {
        "res": (tick_round(res * (1 - b)), tick_round(res), tick_round(res * (1 + b))),
        "sup": (tick_round(sup * (1 - b)), tick_round(sup), tick_round(sup * (1 + b))),
    }
    return {"state": state, "focus": focus, "zones": zones, "price": float(price)}


def zone_hint(p, lo, hi):
    if lo <= p <= hi:
        return "現價在進場區內"
    if p > hi:
        return f"現價高於進場區 {(p - hi) / hi * 100:.1f}%"
    return f"現價低於進場區 {(lo - p) / lo * 100:.1f}%"


def zone_label(info):
    z = info["zones"]
    if info["focus"] == "res":
        return f"壓力區 {fmt(z['res'][0])}～{fmt(z['res'][2])}"
    if info["focus"] == "sup":
        return f"支撐區 {fmt(z['sup'][0])}～{fmt(z['sup'][2])}"
    return f"壓力區 {fmt(z['res'][0])}～{fmt(z['res'][2])}｜支撐區 {fmt(z['sup'][0])}～{fmt(z['sup'][2])}"


def ticks_down(p, n):
    """價格往下移 n 檔（逐檔依台股跳動單位，跨價位級距也正確）。"""
    for _ in range(int(n)):
        p = round(p - tick_size(p - 1e-9), 2)
    return p


def find_breakout_bar(data, level, big_mult):
    """找出「向上突破 level」的日 K：收盤站上 level、前一日收盤在 level 之下。
    優先取量大者（成交量 ≥ big_mult × 前 20 日均量）中最近的一根；沒有大量的就取最近一根。"""
    d = data.reset_index(drop=True).copy()
    d["vol_ma"] = d["volume"].rolling(20, min_periods=5).mean().shift(1)
    cross = d[(d["close"] > level) & (d["close"].shift(1) <= level)]
    if cross.empty:
        return None
    big = cross[(cross["vol_ma"] > 0) & (cross["volume"] >= big_mult * cross["vol_ma"])]
    bar = (big if not big.empty else cross).iloc[-1]
    ratio = float(bar["volume"] / bar["vol_ma"]) if pd.notna(bar["vol_ma"]) and bar["vol_ma"] > 0 else None
    return {
        "date": pd.to_datetime(bar["time"]).strftime("%m/%d"),
        "low": float(bar["low"]), "high": float(bar["high"]),
        "ratio": ratio, "big": not big.empty,
    }


def compute_stops(bars, n_ticks):
    """bars 為 None（未啟用自動）或 {'res': bar|None, 'sup': bar|None}。"""
    out = {"res": None, "sup": None}
    if bars:
        for k in ("res", "sup"):
            if bars.get(k):
                out[k] = ticks_down(bars[k]["low"], n_ticks)
    return out


def strategy_html(info, band, tp, bars, n_ticks):
    """tp 為小數（0.10）。bars 同 compute_stops。"""
    stops = compute_stops(bars, n_ticks)

    def block(title, key):
        lo, mid, hi = info["zones"][key]
        on = "on" if info["focus"] in (key, "both") else ""
        tpp = tick_round(mid * (1 + tp))
        if bars is None:
            stop_line = '<span class="muted">停損：勾選下方「自動抓取」，或手動輸入</span>'
        elif not bars.get(key):
            stop_line = '<span class="muted">近半年找不到突破這個價位的 K 棒 → 請手動輸入停損</span>'
        else:
            b = bars[key]
            ratio = f"｜量 {b['ratio']:.1f}× 均量" if b["ratio"] else ""
            kind = "大量突破K" if b["big"] else "突破K（無大量，取最近一根）"
            stp = stops[key]
            if stp >= mid:
                pct_txt = '<span class="muted">（高於進場價，不適用，請手動輸入）</span>'
            else:
                pct_txt = f'<b class="neg">{(stp - mid) / mid * 100:+.1f}%</b>'
            stop_line = (f'{kind} {b["date"]}｜低 {fmt(b["low"])}{ratio} → 停損 '
                         f'<b class="neg">{fmt(stp)}</b>（低點下 {n_ticks} 檔，距進場價 {pct_txt}）')
        return (
            f'<div class="zn {on}"><div class="zt">{title} {fmt(mid)} ± {band:g}%</div>'
            f'<div class="zr">進場區 <b>{fmt(lo)} ～ {fmt(hi)}</b></div>'
            f'<div class="zh">{zone_hint(info["price"], lo, hi)}</div>'
            f'<div class="zc">{stop_line}</div>'
            f'<div class="zc">以 {fmt(mid)} 進場 → 1/3 停利 <b class="pos">{fmt(tpp)}</b></div></div>'
        )
    return (
        '<div class="stg"><div class="stg-h">策略 1｜突破後回測　'
        f'<span class="stg-st">{html.escape(info["state"])}</span></div>'
        f'<div class="zns">{block("壓力價", "res")}{block("支撐價", "sup")}</div></div>'
    )


def calc_html(entry, stop, tp):
    if entry <= 0:
        return '<div class="calc">請輸入進場價</div>'
    tpp = tick_round(entry * (1 + tp))
    tp_part = f'<span>1/3 停利價 <b class="pos">{fmt(tpp)}</b>（+{tp * 100:g}%）</span>'
    if stop <= 0:
        return f'<div class="calc">{tp_part}<span class="muted">請輸入停損價（突破 K 棒低點下數檔）</span></div>'
    if stop >= entry:
        return f'<div class="calc"><span class="neg">停損價需低於進場價</span>{tp_part}</div>'
    risk = entry - stop
    return (
        '<div class="calc">'
        f'<span>停損價 <b class="neg">{fmt(stop)}</b></span>{tp_part}'
        f'<span>每股風險 <b>{fmt(risk)}</b>（{risk / entry * 100:.1f}%）</span>'
        f'<span>每股目標獲利 <b>{fmt(tpp - entry)}</b></span>'
        f'<span>報酬風險比 <b>{(tpp - entry) / risk:.1f} : 1</b></span></div>'
    )


# ------------------------------------------------------------------
# 策略績效追蹤：每日快照 → 以當日收盤買進，到現在的報酬與勝率
# ------------------------------------------------------------------
DEFAULT_HISTORY_URL = "https://raw.githubusercontent.com/m195252/stock-war-room/main/history/snapshots.csv"


@st.cache_data(ttl=600, show_spinner="讀取每日快照…")
def load_history(src, b_relax_month=False, b_relax_season=False):
    """讀取每日快照並用「目前的關卡規則」評分。沒有快照檔回傳 None。"""
    if src.startswith("http"):
        r = requests.get(src, timeout=20)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        text = r.content.decode("utf-8-sig")
    else:
        if not os.path.exists(src):
            return None
        with open(src, encoding="utf-8-sig") as f:
            text = f.read()
    raw = pd.read_csv(io.StringIO(text), dtype=str)
    hc = {k: pick_col(raw, k) for k in COL_CANDIDATES}
    if "date" not in raw.columns or not (hc["code"] and hc["price"]):
        return pd.DataFrame(columns=["date", "code", "name", "cat", "score", "trig", "entry", "sup", "res"])
    for k in ("support", "resistance", "price"):
        if hc[k]:
            raw[hc[k]] = to_num(raw[hc[k]])
    out = []
    for _, row_ in raw.iterrows():
        entry = row_[hc["price"]]
        if pd.isna(entry) or entry <= 0:
            continue
        g_ = eval_gates(row_, hc, b_relax_month, b_relax_season)
        sc = gate_score(g_)
        if sc < 1:
            continue
        out.append({
            "date": str(row_["date"]), "code": str(row_[hc["code"]]).strip(),
            "name": g(row_, hc, "name"), "cat": g(row_, hc, "cat"),
            "score": sc, "trig": g_["trigger"], "entry": float(entry),
            "sup": row_[hc["support"]] if hc["support"] else float("nan"),
            "res": row_[hc["resistance"]] if hc["resistance"] else float("nan"),
        })
    return pd.DataFrame(out, columns=["date", "code", "name", "cat", "score", "trig", "entry", "sup", "res"])


LEVEL_NAME = {4: "通過四關", 3: "通過三關", 2: "通過兩關", 1: "通過一關"}
LEVEL_COLOR = {4: "#1f6b21", 3: "#4c8f3a", 2: "#a38a1c", 1: "#8b909a"}


def render_perf(live, cols, relax_m=False, relax_s=False):
    with st.expander("📈 策略績效追蹤：每日快照 → 收盤買進後的報酬與勝率", expanded=False):
        url = _secret("HISTORY_URL", "") or DEFAULT_HISTORY_URL
        try:
            hist = load_history(url, relax_m, relax_s)
        except Exception as e:
            st.warning(f"讀取每日快照失敗（{type(e).__name__}），稍後再試。")
            return
        if hist is None or hist.empty:
            st.info("目前還沒有每日快照。完成 GitHub Actions 設定後，每個交易日收盤後會自動存一份，"
                    "有了第一份快照，這裡就會開始統計。")
            return

        live_px = dict(zip(live[cols["code"]], live[cols["price"]]))
        h = hist.copy()
        h["now"] = h["code"].map(live_px)
        h = h.dropna(subset=["now"])
        h["ret"] = (h["now"] / h["entry"] - 1) * 100
        today = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=8)).date()
        h["days"] = h["date"].map(lambda d_: (today - pd.to_datetime(d_).date()).days)

        n_days = hist["date"].nunique()
        st.caption(f"快照 {n_days} 天（{hist['date'].min()} ～ {hist['date'].max()}）｜"
                   f"買進價＝快照當天的「目前股價」（收盤後存＝收盤價）｜現價取自目前試算表｜"
                   f"勝率＝目前報酬 > 0 的比例｜未計手續費與證交稅")

        c1, c2 = st.columns(2)
        mode = c1.radio("統計方式", ["各關卡獨立（剛好通過 N 關）", "累計（至少通過 N 關）"], horizontal=True)
        first_only = c2.checkbox("同一檔、同一關卡只算第一次出現", value=False,
                                 help="連續好幾天都出現的股票，預設每天都算一筆；勾選後只算第一次。")
        if first_only:
            h = h.sort_values("date").drop_duplicates(["code", "score"], keep="first")
        exact = mode.startswith("各關卡")

        rows_ = []
        for lv in (4, 3, 2, 1):
            sub = h[h["score"] == lv] if exact else h[h["score"] >= lv]
            label = LEVEL_NAME[lv] if exact else f"至少通過 {lv} 關"
            if sub.empty:
                rows_.append({"關卡": label, "訊號數": 0, "勝率": "-", "平均報酬": "-", "中位數報酬": "-"})
            else:
                rows_.append({
                    "關卡": label, "訊號數": len(sub),
                    "勝率": f"{(sub['ret'] > 0).mean() * 100:.0f}%",
                    "平均報酬": f"{sub['ret'].mean():+.2f}%",
                    "中位數報酬": f"{sub['ret'].median():+.2f}%",
                })
        st.table(pd.DataFrame(rows_))

        st.markdown("---")
        backtest_section(hist)
        st.markdown("---")
        dates = sorted(h["date"].unique(), reverse=True)
        pick = st.selectbox("看哪一天的名單", ["全部日期"] + dates)
        sub = h if pick == "全部日期" else h[h["date"] == pick]
        sub = sub.sort_values(["date", "score", "ret"], ascending=[False, False, False]).head(300)
        if sub.empty:
            st.info("這天沒有符合的標的。")
            return
        th = "text-align:left;padding:6px 8px;color:#6b7280;font-weight:500;border-bottom:1px solid #e3e5dc;"
        body = []
        for r_ in sub.itertuples():
            lc = LEVEL_COLOR[r_.score]
            body.append(
                '<tr style="border-bottom:1px solid #eceee8">'
                f'<td style="padding:6px 8px">{html.escape(r_.date)}</td>'
                f'<td style="padding:6px 8px"><span style="background:{lc};color:#fff;border-radius:10px;'
                f'padding:1px 9px;font-size:12px">{LEVEL_NAME[r_.score]}</span></td>'
                f'<td style="padding:6px 8px"><b>{html.escape(r_.name)}</b> '
                f'<span style="color:#8b909a">{html.escape(r_.code)}</span></td>'
                f'<td style="padding:6px 8px;color:#6b7280">{html.escape(r_.cat)}</td>'
                f'<td style="padding:6px 8px">{"突破" if r_.trig == "A" else "回測"}</td>'
                f'<td style="padding:6px 8px">{fmt(r_.entry)}</td>'
                f'<td style="padding:6px 8px">{fmt(r_.now)}</td>'
                f'<td style="padding:6px 8px;font-weight:600">{chg_span(r_.ret)}</td>'
                f'<td style="padding:6px 8px;color:#6b7280">{r_.days} 天</td></tr>'
            )
        st.markdown(
            '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:14px;'
            'background:#fff;color:#1f2933">'
            f'<thead><tr><th style="{th}">快照日</th><th style="{th}">關卡</th><th style="{th}">股票</th>'
            f'<th style="{th}">分類</th><th style="{th}">觸發</th><th style="{th}">買進價</th>'
            f'<th style="{th}">現價</th><th style="{th}">報酬</th><th style="{th}">持有</th></tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>',
            unsafe_allow_html=True,
        )


# ------------------------------------------------------------------
# 策略回測：突破後回測買入，+10% 賣 1/3、破 5 日線賣 1/3、破 20 日線賣 1/3、
#           跌破「突破 K 棒低點下 N 檔」全數停損
# ------------------------------------------------------------------
def simulate_trade(data, entry_date, entry, stop, tp, ma_fast=5, ma_slow=20):
    """以日 K 模擬一筆交易。找不到進場當天的 K 線回傳 None。
    規則：每個出場條件各占 1/3；同一天先檢查停損（保守）；
    破均線＝先站上均線後，收盤再跌破（進場時已在均線下，需先站回均線）。"""
    d = data.sort_values("time").reset_index(drop=True).copy()
    d["time"] = pd.to_datetime(d["time"])
    d["ma_f"] = d["close"].rolling(ma_fast).mean()
    d["ma_s"] = d["close"].rolling(ma_slow).mean()
    hit = d.index[d["time"].dt.date == entry_date]
    if len(hit) == 0:
        return None
    i0 = int(hit[0])
    target = entry * (1 + tp)
    exits, why = [None, None, None], ["", "", ""]
    armed_f = bool(pd.notna(d.at[i0, "ma_f"]) and d.at[i0, "close"] >= d.at[i0, "ma_f"])
    armed_s = bool(pd.notna(d.at[i0, "ma_s"]) and d.at[i0, "close"] >= d.at[i0, "ma_s"])
    status = "持有中"
    for j in range(i0 + 1, len(d)):
        o, h_, l_, c_ = (float(d.at[j, k]) for k in ("open", "high", "low", "close"))
        if stop and l_ <= stop:
            fill = min(o, stop)
            for k in range(3):
                if exits[k] is None:
                    exits[k], why[k] = fill, "停損"
            status = "停損出場"
            break
        if exits[0] is None and h_ >= target:
            exits[0], why[0] = max(o, target), "停利"
        mf, ms = d.at[j, "ma_f"], d.at[j, "ma_s"]
        if pd.notna(mf):
            if exits[1] is None and armed_f and c_ < mf:
                exits[1], why[1] = c_, "破5日線"
            armed_f = c_ >= mf
        if pd.notna(ms):
            if exits[2] is None and armed_s and c_ < ms:
                exits[2], why[2] = c_, "破20日線"
            armed_s = c_ >= ms
        if all(e is not None for e in exits):
            status = "全部出場"
            break
    last = float(d["close"].iloc[-1])
    n_open = sum(e is None for e in exits)
    rets, parts = [], []
    for k in range(3):
        px = exits[k] if exits[k] is not None else last
        r_ = (px / entry - 1) * 100
        rets.append(r_)
        parts.append(f"{why[k] or '持有'} {r_:+.1f}%")
    if status == "持有中" and n_open < 3:
        status = "部分出場，持有中"
    return {"ret": sum(rets) / 3, "status": status, "detail": "｜".join(parts)}


def run_backtest(sigs, n_ticks_, big_mult_, tp, prog):
    rows_ = []
    codes = list(sigs["code"].unique())
    data_map = {}
    for i, code_ in enumerate(codes):
        prog.progress((i + 1) / max(len(codes), 1), text=f"抓取歷史 K 線 {i + 1}/{len(codes)}：{code_}")
        try:
            data_map[code_] = fetch_history(code_)[0]
        except Exception:
            data_map[code_] = None
    for r_ in sigs.itertuples():
        base = {"date": r_.date, "name": r_.name, "code": r_.code, "score": r_.score,
                "trig": r_.trig, "entry": r_.entry, "stop": None}
        data = data_map.get(r_.code)
        if data is None:
            rows_.append({**base, "skip": "抓不到歷史 K 線"})
            continue
        edate = pd.to_datetime(r_.date).date()
        upto = data[pd.to_datetime(data["time"]).dt.date <= edate]
        stop = None
        if pd.notna(r_.res) and pd.notna(r_.sup) and not upto.empty:
            if r_.entry >= r_.res:
                level = r_.res
            elif r_.entry <= r_.sup * 1.01:
                level = r_.sup
            else:
                level = r_.res
            bar = find_breakout_bar(upto, level, big_mult_)
            if bar:
                s_ = ticks_down(bar["low"], n_ticks_)
                if s_ < r_.entry:
                    stop = s_
        sim = simulate_trade(data, edate, r_.entry, stop, tp)
        if sim is None:
            rows_.append({**base, "skip": "找不到進場當天的 K 線"})
            continue
        rows_.append({**base, "stop": stop, "skip": "", **sim})
    return pd.DataFrame(rows_)


def backtest_section(hist):
    st.markdown("#### 🧪 策略回測：突破後回測買入＋分批出場")
    st.caption("買進＝快照當天收盤價｜出場：+停利% 賣 1/3、收盤跌破 5 日線賣 1/3、收盤跌破 20 日線賣 1/3｜"
               "跌破「突破 K 棒低點下 N 檔」（量大優先，找不到就不設停損）全數停損出場｜"
               "同一天先檢查停損；用日 K 模擬，未計手續費與證交稅；未出場的部分以最新收盤價計。")
    c1, c2 = st.columns(2)
    kind = c1.selectbox("進場訊號", ["回測（突破後回測買入）", "突破", "全部"])
    min_lv = c2.selectbox("至少通過關卡", [1, 2, 3, 4], index=0)
    sigs = hist[hist["score"] >= min_lv]
    if kind.startswith("回測"):
        sigs = sigs[sigs["trig"] == "B"]
    elif kind == "突破":
        sigs = sigs[sigs["trig"] == "A"]
    sigs = sigs.sort_values("date")
    st.write(f"符合的訊號：{len(sigs)} 筆（{sigs['code'].nunique()} 檔）")

    if st.button("▶ 執行策略回測（會下載各股歷史 K 線）", key="run_bt"):
        if sigs.empty:
            st.info("沒有符合的訊號可回測。")
        else:
            prog = st.progress(0.0, text="準備中…")
            st.session_state["bt_rows"] = run_backtest(sigs, int(n_ticks), float(big_mult), tp_pct / 100, prog)
            st.session_state["bt_meta"] = f"{kind}｜至少 {min_lv} 關｜停利 {tp_pct:g}%｜停損下 {int(n_ticks)} 檔"
            prog.empty()

    res_ = st.session_state.get("bt_rows")
    if res_ is None or res_.empty:
        return
    st.caption(f"上次回測設定：{st.session_state.get('bt_meta', '')}")
    ok = res_[res_["skip"] == ""]
    skipped = res_[res_["skip"] != ""]
    if ok.empty:
        st.info("沒有可計算的交易（可能是歷史 K 線還沒更新到進場日）。")
    else:
        stops = (ok["status"] == "停損出場").sum()
        holding = ok["status"].str.contains("持有").sum()
        st.table(pd.DataFrame([{
            "有效筆數": len(ok), "勝率": f"{(ok['ret'] > 0).mean() * 100:.0f}%",
            "平均報酬": f"{ok['ret'].mean():+.2f}%", "中位數報酬": f"{ok['ret'].median():+.2f}%",
            "停損出場": int(stops), "仍持有": int(holding), "無停損價": int(ok["stop"].isna().sum()),
        }]))
    if not skipped.empty:
        st.caption(f"略過 {len(skipped)} 筆：" + "、".join(sorted(set(skipped["skip"]))))
    th = "text-align:left;padding:6px 8px;color:#6b7280;font-weight:500;border-bottom:1px solid #e3e5dc;"
    body = []
    for r_ in ok.sort_values(["date", "ret"], ascending=[False, False]).head(300).itertuples():
        stop_txt = fmt(r_.stop) if pd.notna(r_.stop) else "—"
        body.append(
            '<tr style="border-bottom:1px solid #eceee8">'
            f'<td style="padding:6px 8px">{html.escape(r_.date)}</td>'
            f'<td style="padding:6px 8px"><b>{html.escape(r_.name)}</b> '
            f'<span style="color:#8b909a">{html.escape(r_.code)}</span></td>'
            f'<td style="padding:6px 8px">{"回測" if r_.trig == "B" else "突破"}・{r_.score}關</td>'
            f'<td style="padding:6px 8px">{fmt(r_.entry)}</td><td style="padding:6px 8px">{stop_txt}</td>'
            f'<td style="padding:6px 8px">{html.escape(r_.status)}</td>'
            f'<td style="padding:6px 8px;color:#6b7280;font-size:12.5px">{html.escape(r_.detail)}</td>'
            f'<td style="padding:6px 8px;font-weight:600">{chg_span(r_.ret)}</td></tr>'
        )
    if body:
        st.markdown(
            '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:14px;'
            'background:#fff;color:#1f2933">'
            f'<thead><tr><th style="{th}">進場日</th><th style="{th}">股票</th><th style="{th}">訊號</th>'
            f'<th style="{th}">買進價</th><th style="{th}">停損價</th><th style="{th}">狀態</th>'
            f'<th style="{th}">三份出場明細（停利／破5日／破20日）</th><th style="{th}">總報酬</th></tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>', unsafe_allow_html=True)


# ------------------------------------------------------------------
# 第 1～4 關：條件說明 + 目前各關卡被擋的原因統計
# ------------------------------------------------------------------
def render_gate_panel(results, relax_m, relax_s):
    from collections import Counter

    with st.expander("📋 第 1～4 關篩選條件與目前結果", expanded=False):
        note_s = "（回測已設定：不要求）" if relax_s else ""
        note_m = "（回測已設定：不要求）" if relax_m else ""
        st.markdown(
            "**第一關：啟動條件**（讀「突破狀態/日期」「操作建議」）\n"
            "- **B 回測**（優先）：突破狀態含「回測支撐」；或含「已突破／昨日突破」且操作建議含「回測買點」\n"
            "- **A 突破**：突破狀態含「今日突破」\n"
            "- 都不符合 → 剔除，後面不再判斷\n\n"
            f"**第二關：大環境**（突破、回測相同，兩項都要成立）\n"
            f"- 季線之上：現價 ≥ 季線價{note_s}\n"
            f"- 月線：含「月線之上」或「守穩回測」{note_m}\n\n"
            "**第三關：量價**（讀「量價」欄）\n"
            "- A 突破：含「溫和放量」或「爆量警示」，且不含「量縮」「窒息量」\n"
            "- B 回測：含「量縮／窒息量／溫和放量／正常量」其中之一，且不含「爆量警示」\n\n"
            "**第四關：K 棒**（讀「當天強弱」欄）\n"
            "- A 突破：必須「實體紅K」；含「實體殺盤」剔除\n"
            "- B 回測：必須「強力收腳」或「略有支撐」；含「實體殺盤」剔除\n\n"
            "依序過濾：前一關沒過就停在那一關。回測的第二關可在左側欄「關卡設定（回測）」放寬。"
        )
        if not results:
            return
        rows_, notes = [], []
        for label, key in (("回測 B", "B"), ("突破 A", "A")):
            sub = [(row_, r_) for row_, r_ in results if r_["trigger"] == key]
            n1 = len(sub)
            n2 = sum(1 for _, r_ in sub if r_["g2"])
            n3 = sum(1 for _, r_ in sub if r_["g2"] and r_["g3"][0])
            n4 = sum(1 for _, r_ in sub if r_["passed"])
            rows_.append({"類型": label, "第一關（觸發）": n1, "通過第二關": n2, "通過第三關": n3, "通過第四關": n4})
            c2_ = Counter(x for _, r_ in sub if not r_["g2"] for x in r_["g2_reason"].split("、") if x)
            c3_ = Counter(r_["g3"][1] for _, r_ in sub if r_["g2"] and not r_["g3"][0])
            c4_ = Counter(r_["g4"][1] for _, r_ in sub if r_["g2"] and r_["g3"][0] and not r_["g4"][0])
            for gname, cnt in (("第二關", c2_), ("第三關", c3_), ("第四關", c4_)):
                if cnt:
                    notes.append(f"- {label}・{gname}被擋：" + "、".join(f"{k} {v} 檔" for k, v in cnt.most_common()))
        st.markdown("**目前各關通過檔數**")
        st.table(pd.DataFrame(rows_))
        if notes:
            st.markdown("**被擋原因統計**\n" + "\n".join(notes))
        stuck = []
        for row_, r_ in results:
            if r_["trigger"] == "B" and not r_["passed"]:
                sc = gate_score(r_)
                stuck.append(f"{g(row_, cols, 'name')}（第{sc + 1}關：{stop_reason(r_)}）")
        if stuck:
            st.markdown("**回測類尚未通過的個股**\n\n" + "、".join(stuck[:40]))


st.markdown(CSS, unsafe_allow_html=True)

# ------------------------------------------------------------------
# 側邊欄：資料來源
# ------------------------------------------------------------------
def _secret(name, default=""):
    """從 Streamlit Secrets 讀取設定；本機沒有 secrets 檔時回傳預設值。"""
    try:
        return str(st.secrets.get(name, default))
    except Exception:
        return default


# 試算表連結放在 Secrets（SHEET_URL），程式碼裡不寫死
DEFAULT_SHEET_URL = _secret("SHEET_URL", "").strip()

st.sidebar.title("📋 觀察清單")
uploaded = st.sidebar.file_uploader("上傳 CSV（最優先）", type=["csv"])
sheet_url_input = st.sidebar.text_input(
    "Google Sheet CSV 連結（留空＝使用預設）",
    value="",
    help="留空會使用 Secrets 裡設定的 SHEET_URL；填入連結則改用你填的。",
).strip()
sheet_url = sheet_url_input or DEFAULT_SHEET_URL
path = st.sidebar.text_input("本機 CSV 路徑（最後備用）", value="watchlist.csv")
if st.sidebar.button("🔄 重新載入清單"):
    st.cache_data.clear()
    st.rerun()

try:
    if uploaded:
        df, FETCHED_AT = load_csv(uploaded)
    elif sheet_url:
        try:
            df, FETCHED_AT = load_csv(sheet_url)
        except Exception as e1:
            st.sidebar.warning(
                f"Google Sheet 讀取失敗（{type(e1).__name__}），已改用本機檔案。"
                "請確認試算表已設為「知道連結的任何人都能檢視」。"
            )
            df, FETCHED_AT = load_csv(path)
    else:
        df, FETCHED_AT = load_csv(path)
except Exception as e:
    st.sidebar.error(f"讀取失敗：{e}")
    st.info("請上傳 CSV、貼上 Google Sheet 連結，或填入正確的本機路徑。")
    st.stop()

if df.columns[0] in ("", "Unnamed: 0"):
    df = df.rename(columns={df.columns[0]: "備註"})
cols = {k: pick_col(df, k) for k in COL_CANDIDATES}
if not cols["code"]:
    st.error(f"找不到代號欄位。請確認 CSV 含有：{COL_CANDIDATES['code']}")
    st.stop()

df = df.dropna(subset=[cols["code"]]).copy()
df[cols["code"]] = df[cols["code"]].astype(str).str.strip()
df = df[df[cols["code"]] != ""].drop_duplicates(subset=[cols["code"]]).reset_index(drop=True)
for k in ("support", "resistance", "price"):
    if cols[k]:
        df[cols[k]] = to_num(df[cols[k]])
df["_dev"] = to_num(df[cols["dev"]].astype(str).str.replace("%", "", regex=False)) if cols["dev"] else float("nan")
df["_chg"] = to_num(df[cols["chg"]].astype(str).str.replace("%", "", regex=False)) if cols["chg"] else float("nan")
if cols["lvl"]:
    df[cols["lvl"]] = df[cols["lvl"]].map(norm_lvl)
    _lv = [x for x in df[cols["lvl"]] if x]
    LATEST_LVL = max(_lv, key=lambda x: (int(x[:2]), int(x[3:]))) if _lv else ""
    df["_lvkey"] = df[cols["lvl"]].map(lambda x: int(x[:2]) * 100 + int(x[3:]) if x else -1)
else:
    df["_lvkey"] = -1


def has(colkey, text):
    c = cols[colkey]
    if not c:
        return pd.Series(False, index=df.index)
    return df[c].fillna("").astype(str).str.contains(text, regex=False)


# ------------------------------------------------------------------
# 頂部：分類熱度 + 篩選欄
# ------------------------------------------------------------------
mask = pd.Series(True, index=df.index)

# 1) 分類「當天漲跌」平均熱度（以全部標的計算，不受篩選影響）
if cols["cat"] and cols["chg"]:
    grp = df.groupby(cols["cat"])
    heat = pd.DataFrame({
        "avg": grp["_chg"].mean(),
        "n": grp[cols["code"]].count(),
        "up": grp["_chg"].apply(lambda s: int((s > 0).sum())),
    }).sort_values("avg", ascending=False, na_position="last")
    tiles = []
    for cat_name, r in heat.iterrows():
        a = r["avg"]
        if pd.isna(a):
            bg, txt, edge = "#2a2c22", "-", "#6c6f5f"
        else:
            alpha = min(0.70, 0.18 + abs(a) / 8 * 0.5)
            rgb = "229,83,60" if a > 0 else ("76,154,106" if a < 0 else "122,126,112")
            tint = f"rgba({rgb},{alpha:.2f})"
            bg, txt, edge = f"linear-gradient({tint},{tint}),#1e2119", f"{a:+.2f}%", f"rgb({rgb})"
        tiles.append(
            f'<div class="heat" style="background:{bg};border-left-color:{edge}"><div class="h-name">{html.escape(str(cat_name))}</div>'
            f'<div class="h-val">{txt}</div><div class="h-sub">{int(r["n"])} 檔｜上漲 {int(r["up"])}</div></div>'
        )
    st.markdown('<div style="font-size:14px;font-weight:600;margin:4px 0 2px">🌡️ 分類當日漲跌熱度（平均，紅漲綠跌）</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="heats">{"".join(tiles)}</div>', unsafe_allow_html=True)

# ------------------------------------------------------------------
# 關卡篩選器：四道關卡（依序過濾）
# ------------------------------------------------------------------
if all(cols[k] for k in ("brk", "op", "season", "month", "vol", "kbar")):
    st.sidebar.markdown("**關卡設定（回測）**")
    relax_m = st.sidebar.checkbox("回測：第二關不要求月線之上", value=False,
                                  help="回測常常會跌到月線附近，勾選後回測類不再因月線被第二關淘汰。")
    relax_s = st.sidebar.checkbox("回測：第二關不要求季線之上", value=False,
                                  help="勾選後回測類不再因季線之下被第二關淘汰。")
    results = [(row_, eval_gates(row_, cols, relax_m, relax_s)) for _, row_ in df.iterrows()]
    n_all = len(results)
    n1 = sum(1 for _, r_ in results if r_["trigger"])
    n2 = sum(1 for _, r_ in results if r_["g2"])
    n3 = sum(1 for _, r_ in results if r_["g3"][0])
    passed = [(row_, r_) for row_, r_ in results if r_["passed"]]
    passed.sort(key=lambda x: x[1]["trigger"] != "B")   # 回測在前、突破在後（穩定排序）
    passed_codes = {g(row_, cols, "code") for row_, _ in passed}
    n_a = sum(1 for _, r_ in passed if r_["trigger"] == "A")
    n_b = len(passed) - n_a

    D = "color:#1b2a1b;"
    st.markdown(f"### {len(passed)} 檔通過四道關卡")
    st.markdown(
        f'<div style="color:#555;font-size:14px;margin-bottom:10px">{n_a} 檔突破、{n_b} 檔回測，從 {n_all} 檔觀察清單篩出。'
        f'　<span style="color:#888">第一關 {n1} → 第二關 {n2} → 第三關 {n3} → 第四關 {len(passed)}</span></div>',
        unsafe_allow_html=True,
    )
    if passed:
        _lab = {}
        for row_, r_ in passed:
            c_ = g(row_, cols, "code")
            dot_ = "🔵" if r_["trigger"] == "A" else "🟠"
            pr_ = fmt(row_[cols["price"]]) if cols["price"] else "-"
            _lab[c_] = f"{dot_} {g(row_, cols, 'name')} {c_}　{pr_} {chg_num(row_, cols):+.1f}%"
        if hasattr(st, "pills"):
            def _open_pick():
                c_ = st.session_state.get("pass_pick")
                if c_:
                    st.session_state["dialog_code"] = c_
                    st.session_state["pass_pick"] = None

            st.pills(
                "點選個股，彈出明細", options=list(_lab), format_func=lambda c: _lab[c],
                selection_mode="single", key="pass_pick", on_change=_open_pick,
                label_visibility="collapsed",
            )
            st.caption("🔵 突破・追強勢　🟠 回測・買防守　｜　點個股名稱，彈出明細視窗")
        else:
            pills = []
            for row_, r_ in passed:
                dot = "#2f6fdc" if r_["trigger"] == "A" else "#e8672c"
                kind = "突破・追強勢" if r_["trigger"] == "A" else "回測・買防守"
                pills.append(
                    f'<span title="{kind}" style="display:inline-flex;align-items:center;gap:8px;background:#fff;'
                    f'border:1px solid #d9dcd2;border-radius:10px;padding:8px 14px;margin:0 8px 8px 0;font-size:15px;{D}">'
                    f'<span style="width:10px;height:10px;border-radius:50%;background:{dot};display:inline-block"></span>'
                    f'<b>{html.escape(g(row_, cols, "name"))}</b>'
                    f'<span style="color:#8a8f80">{html.escape(g(row_, cols, "code"))}</span>'
                    f'<span>{fmt(row_[cols["price"]]) if cols["price"] else "-"}</span>'
                    f'{chg_span(chg_num(row_, cols))}</span>'
                )
            st.markdown("".join(pills), unsafe_allow_html=True)
    else:
        st.info("目前沒有標的通過四道關卡。")

    gate_map = {g(row_, cols, "code"): r_ for row_, r_ in results}
    df["_score"] = df[cols["code"]].map(lambda c_: gate_score(gate_map[c_]))
    df["_trigB"] = df[cols["code"]].map(lambda c_: 1 if gate_map[c_]["trigger"] == "B" else 0)
    df["_ratio"] = df[cols["code"]].map(lambda c_: vol_ratio(gate_map[c_]["g3_text"]))
else:
    results, relax_m, relax_s = [], False, False
    passed_codes, gate_map = set(), {}
    df["_score"], df["_trigB"], df["_ratio"] = 0, 0, 0.0
    st.info("缺少 突破狀態／操作建議／季線／月線／量價／當天強弱 其中某些欄位，無法執行關卡篩選。")

# 2) 搜尋
st.markdown("##### 🔎 篩選")
q = st.text_input(
    "🔍 搜尋代號或股票名稱",
    placeholder="直接輸入，例：2330、鈦昇（可輸入多個，用空白或逗號分隔）",
)
terms = [t for t in re.split(r"[,\s，、]+", q.strip()) if t]
if terms:
    hit = pd.Series(False, index=df.index)
    for t in terms:
        h_ = df[cols["code"]].str.contains(t, case=False, regex=False)
        if cols["name"]:
            h_ |= df[cols["name"]].fillna("").str.contains(t, case=False, regex=False)
        hit |= h_
    mask &= hit


def _open_detail():
    v_ = st.session_state.get("pick_detail") or ""
    if v_:
        st.session_state["dialog_code"] = v_.split(" ")[0]
        st.session_state["pick_detail"] = ""


_nm = df[cols["name"]].fillna("") if cols["name"] else df[cols["code"]]
_opts = [""] + [f"{c_} {n_}" for c_, n_ in zip(df[cols["code"]], _nm)]
st.selectbox("📂 開啟個股明細（輸入代號或名稱搜尋，不受下方篩選影響）", _opts,
             key="pick_detail", on_change=_open_detail)

# 3) 訊號類型與關卡按鈕
# 訊號類型篩選：依「第一關」判定的類型（B 回測／A 突破），和下面的關卡按鈕可以疊加
if "trig_filter" not in st.session_state:
    st.session_state.trig_filter = None
if "gate_level" not in st.session_state:
    st.session_state.gate_level = None


def _set_trig(k):
    st.session_state.trig_filter = None if st.session_state.trig_filter == k else k


def _set_level(k):
    st.session_state.gate_level = None if st.session_state.gate_level == k else k


tf = st.session_state.trig_filter
lvl = st.session_state.gate_level
n_b = int((df["_trigB"] == 1).sum())
n_a = int(((df["_score"] >= 1) & (df["_trigB"] == 0)).sum())
bcs = st.columns(6)
bcs[0].button(
    f"✔ 回測訊號（{n_b}）" if tf == "B" else f"🎯 回測訊號（{n_b}）",
    on_click=_set_trig, args=("B",), type="primary" if tf == "B" else "secondary",
    key="btn_trig_b", use_container_width=True,
    help="第一關判定為「回測」的個股（突破狀態含回測支撐，或已突破／昨日突破且操作建議含回測買點）。",
)
bcs[1].button(
    f"✔ 突破訊號（{n_a}）" if tf == "A" else f"🔥 突破訊號（{n_a}）",
    on_click=_set_trig, args=("A",), type="primary" if tf == "A" else "secondary",
    key="btn_trig_a", use_container_width=True,
    help="第一關判定為「突破」的個股（突破狀態含今日突破）。",
)
for k_, label_ in ((1, "通過第一關"), (2, "通過第二關"), (3, "通過第三關"), (4, "✅ 通過四關")):
    n_k = int((df["_score"] >= k_).sum())
    bcs[k_ + 1].button(
        f"✔ {label_}（{n_k}）" if lvl == k_ else f"{label_}（{n_k}）",
        on_click=_set_level, args=(k_,), type="primary" if lvl == k_ else "secondary",
        key=f"btn_gate{k_}", use_container_width=True,
    )
if tf == "B":
    mask &= df["_trigB"] == 1
elif tf == "A":
    mask &= (df["_score"] >= 1) & (df["_trigB"] == 0)
if lvl:
    mask &= df["_score"] >= lvl

# ------------------------------------------------------------------
# 側邊欄：其他篩選
# ------------------------------------------------------------------
st.sidebar.divider()
st.sidebar.subheader("其他篩選")

if cols["op"]:
    NONE = "（無建議）"
    opts = sorted(x for x in df[cols["op"]].dropna().unique() if x.strip()) + [NONE]
    sel = st.sidebar.multiselect("操作建議", opts)
    if sel:
        m_ = df[cols["op"]].isin([x for x in sel if x != NONE])
        if NONE in sel:
            m_ |= df[cols["op"]].fillna("").str.strip() == ""
        mask &= m_

if cols["cat"]:
    sel = st.sidebar.multiselect("分類", sorted(df[cols["cat"]].dropna().unique()))
    if sel:
        mask &= df[cols["cat"]].isin(sel)

if cols["note"]:
    NO_NOTE = "（無備註）"
    opts = sorted(str(x) for x in df[cols["note"]].dropna().unique() if str(x).strip()) + [NO_NOTE]
    sel = st.sidebar.multiselect("備註", opts)
    if sel:
        m_ = df[cols["note"]].isin([x for x in sel if x != NO_NOTE])
        if NO_NOTE in sel:
            m_ |= df[cols["note"]].fillna("").str.strip() == ""
        mask &= m_

if cols["status"]:
    opts = sorted(x for x in df[cols["status"]].dropna().unique() if x.strip())
    sel = st.sidebar.multiselect("狀態判斷", opts)
    if sel:
        mask &= df[cols["status"]].isin(sel)

if cols["brk"]:
    opts = sorted(x for x in df[cols["brk"]].dropna().unique() if x.strip())
    sel = st.sidebar.multiselect("突破狀態 / 日期", opts)
    if sel:
        mask &= df[cols["brk"]].isin(sel)

if cols["lvl"]:
    dts = sorted({x for x in df[cols["lvl"]] if x}, key=lambda x: (int(x[:2]), int(x[3:])), reverse=True)
    NO_LVL = "（本期未改線）"
    sel = st.sidebar.multiselect("本期改線日", dts + [NO_LVL])
    if sel:
        m_ = df[cols["lvl"]].isin([x for x in sel if x != NO_LVL])
        if NO_LVL in sel:
            m_ |= df[cols["lvl"]] == ""
        mask &= m_

if st.sidebar.checkbox("✅ 套用 30 秒 SOP 快篩", help="季線安全 → 量價 → K 棒，三關全過才顯示"):
    gate1 = (has("season", "上揚") | has("season", "走平")) & has("season", "位階安全")
    brk_ok = ((has("brk", "今日突破") | has("brk", "昨日突破"))
              & (has("vol", "溫和放量") | has("vol", "爆量")) & has("kbar", "紅K"))
    retest_ok = ((has("brk", "回測支撐") | has("op", "回測買點"))
                 & (has("vol", "窒息量") | has("vol", "量縮"))
                 & (has("kbar", "強力收腳") | has("kbar", "略有支撐")))
    mask &= gate1 & (brk_ok | retest_ok)

show_strat = st.sidebar.checkbox("清單中展開交易策略（較慢）", value=False,
                                help="預設關閉：交易策略只在「個股明細」視窗裡看，清單會快很多。")
sort_by = st.sidebar.selectbox("排序", ["關卡進度（高→低，回測→突破）", "本期改線日（新→舊）", "清單原順序", "乖離率（接近壓力優先）", "當日漲跌（高→低）", "代號"])
view = df[mask].copy()
if sort_by.startswith("關卡"):
    view = view.sort_values(["_score", "_trigB", "_ratio"], ascending=False, kind="stable")
elif sort_by.startswith("本期改線日"):
    view = view.sort_values(["_lvkey", "_score", "_trigB"], ascending=False, kind="stable")
elif sort_by.startswith("乖離率"):
    view = view.sort_values("_dev", ascending=False, na_position="last")
elif sort_by.startswith("當日"):
    view = view.sort_values("_chg", ascending=False, na_position="last")
elif sort_by == "代號":
    view = view.sort_values(cols["code"])
view = view.reset_index(drop=True)

# ------------------------------------------------------------------
# 主畫面：總覽 + 狀態卡片
# ------------------------------------------------------------------


def count(colkey, text):
    c = cols[colkey]
    return int(view[c].fillna("").str.contains(text, regex=False).sum()) if c else 0


mm = st.columns(4)
mm[0].metric("符合條件", f"{len(view)} / {len(df)}")
mm[1].metric("強勢突破", count("status", "強勢突破"))
mm[2].metric("回測買點", count("op", "回測買點"))
mm[3].metric("跌破支撐", count("status", "跌破支撐"))
st.markdown('<div class="mini-title">股票戰情室</div>', unsafe_allow_html=True)

with st.expander("📐 交易策略：條件與參數", expanded=False):
    p1, p2, p3, p4 = st.columns(4)
    band_pct = p1.number_input("進場區間 ±（%）", min_value=0.1, max_value=10.0, value=1.0, step=0.1)
    tp_pct = p2.number_input("1/3 停利 +（%）", min_value=1.0, max_value=100.0, value=10.0, step=1.0)
    n_ticks = p3.number_input("停損：K 棒低點下移（檔）", min_value=0, max_value=10, value=3, step=1)
    big_mult = p4.number_input("大量門檻（× 前 20 日均量）", min_value=1.0, max_value=5.0, value=1.5, step=0.1)
    st.markdown(
        "**符合條件才會在個股下方出現「交易策略」（依試算表欄位判斷）**\n\n"
        "1. 突破狀態／日期含「今日突破、昨日突破、已突破」或「回測支撐」，或操作建議含「回測買點」。\n"
        "2. **策略 1｜突破後回測**：現價 ≥ 壓力價 → 已突破・站穩壓力價；"
        "現價 ≤ 支撐價 × (1 + 進場區間) → 跌回支撐價；其餘 → 突破後回測中。\n"
        "3. **進場區**＝支撐價或壓力價的上下 ±進場區間 %，自動算出價位（依台股跳動單位取整）。\n"
        "4. **停損價**＝突破該價位的那根日 K（優先選成交量 ≥ 前 20 日均量 × 大量門檻者）的低點，往下移 N 檔。"
        "在策略面板勾選「自動抓取」才會下載歷史 K 線；找不到時請手動輸入。\n"
        "5. **1/3 停利價**＝進場價 × (1 + 停利 %)。"
    )

render_gate_panel(results, relax_m, relax_s)
render_perf(df, cols, relax_m, relax_s)

if "open_code" not in st.session_state:
    st.session_state.open_code = None


def toggle(code):
    st.session_state.open_code = None if st.session_state.open_code == code else code


def render_chart(row, prefix="", width=1100):
    code = g(row, cols, "code")
    sup = row[cols["support"]] if cols["support"] else None
    res = row[cols["resistance"]] if cols["resistance"] else None
    try:
        data, ticker = fetch_history(code)
    except Exception as e:
        st.error(f"抓不到 {code} 的歷史資料")
        st.code(str(e))
        if st.button("清除快取並重試", key=f"{prefix}retry_{code}"):
            st.cache_data.clear()
            st.rerun()
        return
    px = row[cols["price"]] if cols["price"] else None

    def seg(label, lvl):
        if lvl is None or pd.isna(lvl):
            return f"{label} -"
        dev = f"（現價乖離 {(px - lvl) / lvl * 100:+.1f}%）" if px is not None and pd.notna(px) and lvl else ""
        return f"{label} {lvl:g}{dev}"

    st.caption(f"資料來源：{ticker}（近 6 個月日 K）｜最近收盤 {float(data['close'].iloc[-1]):g}"
               f"｜{seg('支撐', sup)}｜{seg('壓力', res)}")
    data = data.copy()
    data["time"] = pd.to_datetime(data["time"]).dt.normalize().astype("datetime64[ns]")
    chart = StreamlitChart(width=width, height=560)
    chart.time_scale(time_visible=False)
    chart.set(data)
    if pd.notna(sup):
        chart.horizontal_line(float(sup), color="#26a69a", width=2, style="dashed",
                              text=f"支撐 {sup:g}", axis_label_visible=True)
    if pd.notna(res):
        chart.horizontal_line(float(res), color="#ef5350", width=2, style="dashed",
                              text=f"壓力 {res:g}", axis_label_visible=True)
    chart.load()


def _cols2():
    try:
        return st.columns([9.4, 1.3], vertical_alignment="center")
    except TypeError:
        return st.columns([9.4, 1.3])


def render_strategy(code, info, prefix=""):
    price = info["price"]
    use_auto = st.checkbox(
        "🔍 自動抓取歷史 K 線，找突破 K 棒（量大優先）算停損", value=False, key=f"{prefix}auto_{code}",
        help="會下載該股近半年日 K，可能多花幾秒；抓不到時請手動輸入停損價。",
    )
    bars = None
    if use_auto:
        try:
            data, _ticker = fetch_history(code)
            bars = {
                "res": find_breakout_bar(data, info["zones"]["res"][1], big_mult),
                "sup": find_breakout_bar(data, info["zones"]["sup"][1], big_mult),
            }
        except Exception:
            st.warning("抓不到這檔的歷史 K 線，請手動輸入停損價。")
            bars = {"res": None, "sup": None}
    stops = compute_stops(bars, int(n_ticks))
    st.markdown(strategy_html(info, band_pct, tp_pct / 100, bars, int(n_ticks)), unsafe_allow_html=True)

    default_stop = stops["res"] if info["focus"] in ("res", "both") else stops["sup"]
    default_stop = default_stop or stops["res"] or stops["sup"] or 0.0
    step = float(tick_size(price))
    c_in, c_stop, c_out = st.columns([1, 1, 2.6])
    entry = c_in.number_input("手動輸入進場價", min_value=0.0, value=float(price), step=step,
                              format="%.2f", key=f"{prefix}man_{code}")
    stop_in = c_stop.number_input("停損價（可改）", min_value=0.0, value=float(default_stop), step=step,
                                  format="%.2f", key=f"{prefix}stop_{code}_{default_stop}")
    c_out.markdown(calc_html(float(entry), float(stop_in), tp_pct / 100), unsafe_allow_html=True)


def stock_detail(code_):
    sel_ = df[df[cols["code"]] == code_]
    if sel_.empty:
        st.info("找不到這檔的資料。")
        return
    row_ = sel_.iloc[0]
    st.markdown(row_html(row_, cols, gate_map.get(code_)), unsafe_allow_html=True)
    st.link_button("TradingView 開啟", f"https://www.tradingview.com/chart/?symbol=TWSE%3A{code_}")
    info_ = strategy_info(row_, cols, band_pct)
    if info_:
        st.markdown("##### 📌 交易策略")
        render_strategy(code_, info_, prefix="dlg_")
    st.markdown("##### 📈 K 線圖")
    render_chart(row_, prefix="dlg_", width=700)


if hasattr(st, "dialog"):
    @st.dialog("個股明細", width="large")
    def _stock_dialog(code_):
        stock_detail(code_)
else:
    _stock_dialog = None

_pick = st.session_state.get("dialog_code")
if _pick:
    st.session_state["dialog_code"] = None
    if _stock_dialog:
        _stock_dialog(_pick)
    else:
        with st.container(border=True):
            stock_detail(_pick)

if view.empty:
    st.warning("沒有符合條件的標的，請放寬左側篩選。")
    st.stop()

c1, c2 = st.columns([1, 1])
page_size = c1.selectbox("每頁顯示", ["全部", 20, 50, 100], index=2)
if page_size == "全部" and len(view) > 120:
    c2.caption("⚠️ 一次列出超過 120 檔會比較慢，可改選 50 或 100。")
if page_size == "全部":
    pages, page = 1, 1
    chunk = view
else:
    pages = max(1, -(-len(view) // page_size))
    page = c2.number_input("頁碼", min_value=1, max_value=pages, value=1, step=1)
    chunk = view.iloc[(page - 1) * page_size: page * page_size]

hl, _h1 = _cols2()
hl.markdown(HEADER_HTML, unsafe_allow_html=True)

for i, row in chunk.iterrows():
    code = g(row, cols, "code")
    left, right = _cols2()
    left.markdown(row_html(row, cols, gate_map[code]), unsafe_allow_html=True)
    with right:
        st.link_button("TradingView", f"https://www.tradingview.com/chart/?symbol=TWSE%3A{code}",
                       use_container_width=True)
        is_open = st.session_state.open_code == code
        st.button("▲ 收起線圖" if is_open else "📈 內嵌線圖", key=f"b_{i}_{code}",
                  on_click=toggle, args=(code,), use_container_width=True)
    info = strategy_info(row, cols, band_pct)
    if info and show_strat:
        with st.expander(f"📌 交易策略｜{info['state']}｜{zone_label(info)}"):
            render_strategy(code, info)
    if st.session_state.open_code == code:
        with st.container(border=True):
            render_chart(row)
