"""
每日快照：讀取試算表目前的每一檔資料，存到 history/snapshots.csv。
由 GitHub Actions 在台股收盤後自動執行（見 .github/workflows/snapshot.yml）。

環境變數
  SHEET_URL      試算表 CSV 連結（測試時也可填本機檔案路徑）
  SNAPSHOT_DATE  （選填）指定日期 YYYY-MM-DD，預設為台北時間今天
"""
import datetime as dt
import io
import os
import sys

import pandas as pd
import requests

HIST = "history/snapshots.csv"


def today_tw():
    forced = os.environ.get("SNAPSHOT_DATE", "").strip()
    if forced:
        return forced
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=8)).strftime("%Y-%m-%d")


def num(s):
    return pd.to_numeric(s.astype(str).str.replace(",", "", regex=False), errors="coerce")


def read_sheet(src):
    if src.startswith("http"):
        r = requests.get(src, timeout=30)
        r.raise_for_status()
        text = r.content.decode("utf-8-sig")
    else:
        with open(src, encoding="utf-8-sig") as f:
            text = f.read()
    df = pd.read_csv(io.StringIO(text), dtype=str)
    if df.columns[0] in ("", "Unnamed: 0"):
        df = df.rename(columns={df.columns[0]: "備註"})
    keep = [c for c in df.columns
            if str(c).strip() and not str(c).startswith("Unnamed") and str(c).strip().upper() != "TRUE"]
    return df[keep]


def main():
    src = os.environ.get("SHEET_URL", "").strip()
    if not src:
        sys.exit("缺少環境變數 SHEET_URL")
    day = today_tw()

    new = read_sheet(src)
    for col in ("代號", "目前股價"):
        if col not in new.columns:
            sys.exit(f"試算表缺少欄位「{col}」，目前欄位：{list(new.columns)}")
    new = new[new["代號"].fillna("").astype(str).str.strip() != ""].copy()
    new["代號"] = new["代號"].astype(str).str.strip()
    px = num(new["目前股價"])
    if len(new) == 0 or px.notna().mean() < 0.5:
        sys.exit("目前股價大多是空白（可能是試算表的更新開關關閉或還沒重新計算），這次不存快照。")

    if os.path.exists(HIST):
        hist = pd.read_csv(HIST, dtype=str, encoding="utf-8-sig")
        hist = hist[hist["date"] != day]            # 同一天重跑 → 覆蓋
    else:
        hist = pd.DataFrame()

    # 假日／休市：價格幾乎和上一個交易日完全相同 → 不存
    if not hist.empty:
        last_day = hist["date"].max()
        prev = hist[hist["date"] == last_day].set_index("代號")["目前股價"]
        prev_px = num(prev)
        cur_px = pd.Series(px.values, index=new["代號"].values)
        common = [c for c in cur_px.index if c in prev_px.index]
        if len(common) >= 20:
            same = (cur_px[common].values == prev_px[common].values).mean()
            if same >= 0.95:
                print(f"價格與 {last_day} 幾乎完全相同（{same:.0%}），判斷為休市，不存 {day} 的快照。")
                return

    new.insert(0, "date", day)
    out = pd.concat([hist, new], ignore_index=True)
    out = out.sort_values(["date"], kind="stable")
    os.makedirs(os.path.dirname(HIST), exist_ok=True)
    out.to_csv(HIST, index=False, encoding="utf-8-sig")
    print(f"已存 {day} 的快照：{len(new)} 檔；累計 {out['date'].nunique()} 天。")


if __name__ == "__main__":
    main()
