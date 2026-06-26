"""
ETF 数据获取模块
拉取历史行情 + 本地缓存（已收盘数据缓存，当日数据不缓存）
"""

import json
from datetime import date, datetime
from pathlib import Path
import pandas as pd

_CACHE_DIR = Path(__file__).parent / "cache"
_CACHE_DIR.mkdir(exist_ok=True)


def _cache_file(code: str) -> Path:
    """每只 ETF 单独一个缓存文件"""
    return _CACHE_DIR / f"{code}_hist.json"


def _exchange_prefix(code: str) -> str:
    """交易所前缀：15x 是深交所，5x/56x/58x 等均为上交所"""
    return "sz" if code.startswith("15") else "sh"


def _load_cache(code: str) -> tuple[pd.DataFrame | None, str | None]:
    """
    读取本地缓存，返回 (df, last_date)
    last_date 是缓存中最后一条数据的日期字符串，如 '2026-05-27'
    """
    cache_path = _cache_file(code)
    if not cache_path.exists():
        return None, None

    try:
        with open(cache_path, "r") as f:
            cache = json.load(f)
        df = pd.DataFrame(cache["data"])
        last_date = cache.get("last_date")
        return df, last_date
    except (json.JSONDecodeError, KeyError):
        print(f"缓存文件损坏，已删除: {cache_path.name}")
        cache_path.unlink()
        return None, None


def _save_cache(code: str, df: pd.DataFrame):
    """保存缓存（调用方负责只传入已确定的数据）"""
    if df.empty:
        return
    cache_df = df

    cache = {
        "last_date": cache_df["日期"].iloc[-1],
        "data": cache_df.to_dict("records"),
    }

    with open(_cache_file(code), "w") as f:
        json.dump(cache, f, ensure_ascii=False)


def _fetch_realtime_sina(code: str) -> dict | None:
    """从新浪获取实时行情"""
    import requests
    sina_code = f"{_exchange_prefix(code)}{code}"
    try:
        r = requests.get(
            f"https://hq.sinajs.cn/list={sina_code}",
            timeout=5,
            headers={"Referer": "https://finance.sina.com.cn"},
        )
        r.encoding = "gbk"
        data = r.text.strip().split('"')[1]
        if not data:
            return None
        f = data.split(",")
        return {
            "日期": f[30],
            "开盘": float(f[1]),
            "收盘": float(f[3]),
            "最高": float(f[4]),
            "最低": float(f[5]),
            "成交量_股": float(f[8]),
            "settled": f[32] == "00",  # 00=已收盘，数据已确定
        }
    except Exception:
        return None


def _fetch_realtime_tencent(code: str) -> dict | None:
    """从腾讯获取实时行情"""
    import requests
    qq_code = f"{_exchange_prefix(code)}{code}"
    try:
        r = requests.get(f"https://qt.gtimg.cn/q={qq_code}", timeout=5)
        r.encoding = "gbk"
        data = r.text.strip().split("~")
        if len(data) < 45:
            return None
        # 腾讯时间戳格式 20260602161442，提取 HH 判断是否过 15:00
        ts = data[30]
        hour = int(ts[8:10]) if len(ts) >= 10 else 0
        return {
            "日期": ts[:4] + "-" + ts[4:6] + "-" + ts[6:8],
            "开盘": float(data[5]),
            "收盘": float(data[3]),
            "最高": float(data[33]),
            "最低": float(data[34]),
            "成交量_股": float(data[6]) * 100,  # 腾讯实时返回手，转为股
            "settled": hour >= 15,
        }
    except Exception:
        return None


def _fetch_realtime(code: str) -> dict | None:
    """获取实时行情，新浪 → 腾讯"""
    return _fetch_realtime_sina(code) or _fetch_realtime_tencent(code)


def _need_realtime(df: pd.DataFrame) -> bool:
    """工作日且日K数据缺今天或今天数据可能未收盘 → 需要用实时补"""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    today_str = date.today().isoformat()
    last_date = str(df["日期"].iloc[-1]) if not df.empty else ""
    # <= 而非 <：缓存已有今天数据时仍用实时接口刷新，
    # 避免增量拉取的盘中快照价被当作收盘价
    return last_date <= today_str


def _append_realtime(df: pd.DataFrame, code: str) -> pd.DataFrame:
    """追加当日实时行情到历史数据末尾（工作日、日K缺今天时生效）
    若接口返回 settled=True（已收盘），同时写入缓存。
    """
    if not _need_realtime(df):
        return df

    rt = _fetch_realtime(code)
    if rt is None:
        return df

    settled = rt.get("settled", False)
    rt_vol = rt["成交量_股"]

    last_date = str(df["日期"].iloc[-1])
    rt_date = str(rt["日期"])

    if last_date == rt_date:
        # 今天已有记录，更新为最新价
        for col in ["开盘", "收盘", "最高", "最低"]:
            if col in df.columns:
                df.loc[df.index[-1], col] = rt[col]
        if "成交量" in df.columns:
            df.loc[df.index[-1], "成交量"] = rt_vol
    else:
        # 历史只到昨天，追加今天
        prev_close = float(df["收盘"].iloc[-1])
        row = {
            "日期": rt_date,
            "开盘": rt["开盘"],
            "收盘": rt["收盘"],
            "最高": rt["最高"],
            "最低": rt["最低"],
            "成交量": rt_vol,
            "成交额": 0.0,
            "涨跌额": rt["收盘"] - prev_close,
            "涨跌幅": (rt["收盘"] - prev_close) / prev_close * 100,
            "振幅": (rt["最高"] - rt["最低"]) / prev_close * 100,
            "换手率": 0.0,
        }
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)

    # 把收盘状态挂到 df 上，供 calculate_indicators 读取（concat 会丢 attrs，
    # 所以在拼接之后再写）。.tail() 切片会保留 attrs。
    df.attrs["settled"] = settled

    if settled:
        _save_cache(code, df)

    return df


def _sina_symbol(code: str) -> str:
    """转换为新浪格式"""
    return f"{_exchange_prefix(code)}{code}"


def _fetch_from_sina(code: str) -> pd.DataFrame | None:
    """从新浪拉取数据（主源），并统一列名格式"""
    try:
        import akshare as ak
        sina_code = _sina_symbol(code)
        df = ak.fund_etf_hist_sina(symbol=sina_code)
        if df.empty:
            return None

        # 统一列名为东方财富格式
        df = df.rename(columns={
            "date": "日期",
            "open": "开盘",
            "close": "收盘",
            "high": "最高",
            "low": "最低",
            "volume": "成交量",
            "amount": "成交额",
        })

        df["日期"] = df["日期"].astype(str)
        df["涨跌额"] = df["收盘"].astype(float).diff()
        df["涨跌幅"] = df["收盘"].astype(float).pct_change() * 100
        df["振幅"] = (df["最高"].astype(float) - df["最低"].astype(float)) / df["收盘"].astype(float).shift(1) * 100
        df["换手率"] = 0.0
        df = df.iloc[1:].reset_index(drop=True)

        return df
    except Exception:
        return None


def _fetch_from_eastmoney(code: str, start_date: str = None) -> pd.DataFrame | None:
    """从东方财富拉取数据"""
    try:
        import akshare as ak
        kwargs = {"symbol": code, "period": "daily", "adjust": "qfq"}
        if start_date:
            kwargs["start_date"] = start_date
            kwargs["end_date"] = "20500101"
        df = ak.fund_etf_hist_em(**kwargs)
        # 东方财富成交量单位是「手」，统一转为「股」（与新浪/腾讯/实时接口一致）
        if "成交量" in df.columns:
            df["成交量"] = df["成交量"].astype(float) * 100
        return df
    except Exception:
        return None


def _fetch_from_tencent(code: str) -> pd.DataFrame | None:
    """从腾讯拉取数据（备用源），并统一列名格式"""
    try:
        import akshare as ak
        # 腾讯源需要 sh/sz 前缀
        symbol = f"{_exchange_prefix(code)}{code}"
        df = ak.stock_zh_a_hist_tx(symbol=symbol, start_date="20240101", end_date="20500101")
        if df.empty:
            return None

        # 统一列名为东方财富格式
        df = df.rename(columns={
            "date": "日期",
            "open": "开盘",
            "close": "收盘",
            "high": "最高",
            "low": "最低",
            "amount": "成交量",
        })

        # 日期统一为字符串格式
        df["日期"] = df["日期"].astype(str)

        # 腾讯成交量是手，统一转为股
        df["成交量"] = df["成交量"].astype(float) * 100

        # 本地补算缺失字段
        df["成交额"] = 0.0  # 腾讯源没有成交额
        df["涨跌额"] = df["收盘"].diff()
        df["涨跌幅"] = df["收盘"].pct_change() * 100
        df["振幅"] = (df["最高"] - df["最低"]) / df["收盘"].shift(1) * 100
        df["换手率"] = 0.0  # 腾讯源没有换手率

        # 去掉第一行（diff 产生的 NaN）
        df = df.iloc[1:].reset_index(drop=True)

        return df
    except Exception:
        return None


def fetch_etf_history(code: str, days: int = 250) -> pd.DataFrame | None:
    """
    获取 ETF 历史日线数据（前复权）

    数据源降级：新浪 → 东方财富 → 腾讯
    缓存策略：
    - 有缓存且 last_date = 昨天或今天 → 只拉增量
    - 有缓存但过期 → 全量拉取后覆盖缓存
    - 无缓存 → 全量拉取

    返回 DataFrame，列: 日期,开盘,收盘,最高,最低,成交量,成交额,振幅,涨跌幅,涨跌额,换手率
    """
    cached_df, last_date = _load_cache(code)

    # 判断缓存是否足够新（昨天或今天的数据）
    if cached_df is not None and last_date is not None:
        from datetime import timedelta
        yesterday_str = (date.today() - timedelta(days=1)).isoformat()

        today_str = date.today().isoformat()

        if last_date >= today_str:
            # 缓存已包含今天，直接用
            print(f"读取缓存（已含 {last_date}）")
            cached_df = _append_realtime(cached_df, code)
            return cached_df.tail(days)

        if last_date >= yesterday_str:
            # 缓存到昨天，增量拉取今天
            print(f"读取缓存到 {last_date}，拉取增量数据...")
            new_rows = None
            sources = [
                ("东方财富", lambda: _fetch_from_eastmoney(code, start_date=last_date.replace("-", ""))),
                ("新浪", lambda: _fetch_from_sina(code)),
                ("腾讯", lambda: _fetch_from_tencent(code)),
            ]
            for src_name, fetch_fn in sources:
                src_df = fetch_fn()
                if src_df is not None:
                    candidate = src_df[src_df["日期"] > last_date]
                    if not candidate.empty:
                        new_rows = candidate
                        print(f"增量数据来源: {src_name}（+{len(new_rows)}条）")
                        break

            if new_rows is not None and not new_rows.empty:
                full_df = pd.concat([cached_df, new_rows], ignore_index=True)
            else:
                full_df = cached_df

            _save_cache(code, full_df)
            full_df = _append_realtime(full_df, code)
            return full_df.tail(days)

    # 缓存不存在或太旧，全量拉取
    print(f"正在拉取 {code} 历史数据...")

    # 主源：新浪
    df = _fetch_from_sina(code)
    if df is not None:
        print("数据源: 新浪")
        _save_cache(code, df)
        df = _append_realtime(df, code)
        return df.tail(days)

    # 备用源：东方财富
    print("新浪不可用，切换东方财富...")
    df = _fetch_from_eastmoney(code)
    if df is not None:
        print("数据源: 东方财富")
        _save_cache(code, df)
        df = _append_realtime(df, code)
        return df.tail(days)

    # 备用源：腾讯
    print("东方财富不可用，切换腾讯源...")
    df = _fetch_from_tencent(code)
    if df is not None:
        print("数据源: 腾讯（换手率不可用）")
        _save_cache(code, df)
        df = _append_realtime(df, code)
        return df.tail(days)

    # 都失败了，尝试用过期缓存
    print("所有数据源均不可用")
    if cached_df is not None:
        print("使用过期缓存数据")
        cached_df = _append_realtime(cached_df, code)
        return cached_df.tail(days)

    return None


# ============================================================
# 指标计算
# ============================================================

import numpy as np


def _ema(series: pd.Series, period: int) -> pd.Series:
    """指数移动平均"""
    return series.ewm(span=period, adjust=False).mean()


def calculate_indicators(df: pd.DataFrame, settled: bool = None) -> dict | None:
    """
    接收历史行情 DataFrame，返回计算好的指标 dict（直接给 LLM 用）

    settled: 最后一根日K是否已收盘。None 时从 df.attrs 读取（由
    _append_realtime 写入），缺省视为已收盘（纯历史数据）。
    未收盘时今日成交量只是盘中累计值，量比会被低估，需加警示。
    """
    if df is None or len(df) < 60:
        print(f"数据不足（{len(df) if df is not None else 0}条），至少需要60条")
        return None

    if settled is None:
        settled = df.attrs.get("settled", True)

    close = df["收盘"].astype(float)
    high = df["最高"].astype(float)
    low = df["最低"].astype(float)
    volume = df["成交量"].astype(float)
    change_pct = df["涨跌幅"].astype(float)

    latest = close.iloc[-1]
    latest_change = change_pct.iloc[-1]

    result = {
        "price": round(latest, 4),
        "change_pct": round(latest_change, 2),
        "date": df["日期"].iloc[-1],
    }

    # ---- P0: 均线系统 ----
    ma_periods = [5, 10, 20, 60, 120, 250]
    ma_values = {}
    for p in ma_periods:
        if len(close) >= p:
            ma_values[f"ma{p}"] = round(close.rolling(p).mean().iloc[-1], 4)
    result["ma"] = ma_values

    # 均线形态判断
    available_mas = [ma_values.get(f"ma{p}") for p in [5, 10, 20, 60] if f"ma{p}" in ma_values]
    if len(available_mas) >= 4:
        if all(available_mas[i] > available_mas[i+1] for i in range(len(available_mas)-1)):
            result["ma_pattern"] = "多头排列"
        elif all(available_mas[i] < available_mas[i+1] for i in range(len(available_mas)-1)):
            result["ma_pattern"] = "空头排列"
        else:
            result["ma_pattern"] = "缠绕震荡"
    else:
        result["ma_pattern"] = "数据不足"

    # 均线斜率（MA20 最近5天的方向）
    if len(close) >= 25:
        ma20_series = close.rolling(20).mean()
        ma20_now = ma20_series.iloc[-1]
        ma20_5ago = ma20_series.iloc[-5]
        slope = (ma20_now - ma20_5ago) / ma20_5ago * 100
        if slope > 0.5:
            result["ma20_slope"] = "上翘"
        elif slope < -0.5:
            result["ma20_slope"] = "下探"
        else:
            result["ma20_slope"] = "走平"
    else:
        result["ma20_slope"] = "数据不足"

    # ---- P0: MACD ----
    ema12 = _ema(close, 12)
    ema26 = _ema(close, 26)
    dif = ema12 - ema26
    dea = _ema(dif, 9)
    histogram = (dif - dea) * 2

    result["macd"] = {
        "dif": round(dif.iloc[-1], 4),
        "dea": round(dea.iloc[-1], 4),
        "histogram": round(histogram.iloc[-1], 4),
    }

    # 金叉/死叉判断（看最近两天 DIF 与 DEA 的关系变化）
    if len(dif) >= 2:
        prev_diff = dif.iloc[-2] - dea.iloc[-2]
        curr_diff = dif.iloc[-1] - dea.iloc[-1]
        if prev_diff <= 0 and curr_diff > 0:
            result["macd"]["signal"] = "金叉"
        elif prev_diff >= 0 and curr_diff < 0:
            result["macd"]["signal"] = "死叉"
        elif curr_diff > 0:
            result["macd"]["signal"] = "DIF在DEA上方"
        else:
            result["macd"]["signal"] = "DIF在DEA下方"

    # 顶背离/底背离（近30天）
    # 注意：DIF 会在零轴上下变号，不能用乘法阈值（如 max*0.8）——
    # 当 DIF 为负时乘法会让阈值反向，导致背离几乎恒为真。改用基于
    # 各自振幅的加法容差，符号安全。
    if len(close) >= 30:
        recent_close = close.iloc[-30:]
        recent_dif = dif.iloc[-30:]
        price_range = recent_close.max() - recent_close.min()
        dif_range = recent_dif.max() - recent_dif.min()

        # 价格在区间高/低点附近（容差为价格振幅的 2%）
        price_at_high = recent_close.iloc[-1] >= recent_close.max() - price_range * 0.02
        price_at_low = recent_close.iloc[-1] <= recent_close.min() + price_range * 0.02
        # DIF 明显未跟随创新高/新低（偏离自身高/低点超过振幅的 30%）
        dif_off_high = recent_dif.iloc[-1] < recent_dif.max() - dif_range * 0.3
        dif_off_low = recent_dif.iloc[-1] > recent_dif.min() + dif_range * 0.3

        if price_at_high and dif_off_high:
            result["macd"]["divergence"] = "疑似顶背离"
        elif price_at_low and dif_off_low:
            result["macd"]["divergence"] = "疑似底背离"
        else:
            result["macd"]["divergence"] = "无明显背离"

    # ---- P0: RSI ----
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    rsi_value = round(rsi.iloc[-1], 1)
    result["rsi_14"] = rsi_value
    if rsi_value > 70:
        result["rsi_status"] = "超买"
    elif rsi_value < 30:
        result["rsi_status"] = "超卖"
    else:
        result["rsi_status"] = "正常"

    # ---- P0: 量比 ----
    if len(volume) >= 6:
        avg_vol_5 = volume.iloc[-6:-1].mean()  # 过去5天均量（不含今天）
        vol_ratio = volume.iloc[-1] / avg_vol_5 if avg_vol_5 > 0 else 0
        result["volume_ratio"] = round(vol_ratio, 2)
        if vol_ratio > 2:
            result["volume_status"] = "明显放量"
        elif vol_ratio > 1.5:
            result["volume_status"] = "温和放量"
        elif vol_ratio > 0.8:
            result["volume_status"] = "正常"
        else:
            result["volume_status"] = "缩量"
    else:
        result["volume_ratio"] = None
        result["volume_status"] = "数据不足"

    # 量价关系
    if result["volume_ratio"] is not None:
        if latest_change > 0 and vol_ratio > 1.5:
            result["vol_price_relation"] = "涨+放量（健康）"
        elif latest_change > 0 and vol_ratio < 0.8:
            result["vol_price_relation"] = "涨+缩量（可能虚涨）"
        elif latest_change < 0 and vol_ratio > 1.5:
            result["vol_price_relation"] = "跌+放量（恐慌抛售）"
        elif latest_change < 0 and vol_ratio < 0.8:
            result["vol_price_relation"] = "跌+缩量（自然回调）"
        else:
            result["vol_price_relation"] = "无明显特征"

    # 盘中（未收盘）时，今日成交量只是累计到此刻的快照，拿去和过去5天
    # 的全天均量比，量比会被系统性低估，避免 LLM 误判为"缩量"
    if not settled and result.get("volume_status") not in (None, "数据不足"):
        result["volume_status"] += "（盘中累计，量比偏低仅供参考）"
        result["volume_ratio_note"] = "未收盘，今日成交量为盘中累计值，量比被低估"

    # ---- P0: 布林带 ----
    if len(close) >= 20:
        ma20 = close.rolling(20).mean().iloc[-1]
        std20 = close.rolling(20).std().iloc[-1]
        upper = ma20 + 2 * std20
        lower = ma20 - 2 * std20
        bandwidth = (upper - lower) / ma20 * 100  # 带宽百分比

        result["boll"] = {
            "upper": round(upper, 4),
            "mid": round(ma20, 4),
            "lower": round(lower, 4),
            "bandwidth_pct": round(bandwidth, 2),
        }

        if latest >= upper * 0.98:
            result["boll"]["position"] = "接近上轨"
        elif latest <= lower * 1.02:
            result["boll"]["position"] = "接近下轨"
        else:
            result["boll"]["position"] = "中轨附近"

        # 带宽趋势（最近5天标准差变化）
        if len(close) >= 25:
            std_5ago = close.iloc[:-5].tail(20).std()
            if std20 > std_5ago * 1.1:
                result["boll"]["trend"] = "扩张"
            elif std20 < std_5ago * 0.9:
                result["boll"]["trend"] = "收窄"
            else:
                result["boll"]["trend"] = "平稳"

    # ---- P1: KDJ ----
    if len(close) >= 9:
        low_9 = low.rolling(9).min()
        high_9 = high.rolling(9).max()
        rsv = (close - low_9) / (high_9 - low_9) * 100
        rsv = rsv.fillna(50)

        k = rsv.ewm(com=2, adjust=False).mean()
        d = k.ewm(com=2, adjust=False).mean()
        j = 3 * k - 2 * d

        result["kdj"] = {
            "k": round(k.iloc[-1], 1),
            "d": round(d.iloc[-1], 1),
            "j": round(j.iloc[-1], 1),
        }
        if j.iloc[-1] > 100:
            result["kdj"]["status"] = "超买"
        elif j.iloc[-1] < 0:
            result["kdj"]["status"] = "超卖"
        else:
            result["kdj"]["status"] = "正常"

    # ---- P1: ATR ----
    if len(close) >= 15:
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]
        result["atr_14"] = round(atr, 4)
        result["atr_pct"] = round(atr / latest * 100, 2)  # ATR 占价格的百分比

    # ---- P1: 最大回撤（近60天）----
    if len(close) >= 60:
        recent_60 = close.iloc[-60:]
        rolling_max = recent_60.cummax()
        drawdown = (recent_60 - rolling_max) / rolling_max * 100
        result["max_drawdown_60d"] = round(drawdown.min(), 2)

    # ---- 关键支撑/阻力位 ----
    if len(close) >= 20:
        recent_20_high = high.iloc[-20:].max()
        recent_20_low = low.iloc[-20:].min()
        result["support"] = round(recent_20_low, 4)
        result["resistance"] = round(recent_20_high, 4)

    # ---- 数据质量 ----
    total_days = len(df)
    missing_vol = (volume == 0).sum()
    last_date = df["日期"].iloc[-1]
    today_str = date.today().isoformat()

    result["data_quality"] = {
        "total_days": total_days,
        "missing_volume_days": int(missing_vol),
        "data_as_of": str(last_date),
        # settled：最后一根日K是否已收盘（区别于 stale="数据到没到今天"）。
        # 显式导出供批量 lite 缓存键和"盘中"角标使用，不再靠量比警示文案 substring。
        "settled": bool(settled),
    }
    if str(last_date) < today_str:
        result["data_quality"]["stale"] = True
        result["data_quality"]["warning"] = f"数据截至 {last_date}，非最新数据，分析结果可能与实时行情有偏差"
    else:
        result["data_quality"]["stale"] = False
    if missing_vol > 0:
        result["data_quality"]["note"] = f"{missing_vol}天成交量为0（可能停牌）"

    return result


# ============================================================
# 单独测试
# ============================================================

if __name__ == "__main__":
    import json as _json

    code = "512980"

    print(f"=== 拉取 {code} 历史数据 ===\n")
    df = fetch_etf_history(code)
    if df is None:
        print("数据拉取失败")
        exit(1)

    print(f"获取到 {len(df)} 条数据")
    print(f"日期范围: {df['日期'].iloc[0]} ~ {df['日期'].iloc[-1]}\n")

    print("=== 计算指标 ===\n")
    indicators = calculate_indicators(df)
    if indicators:
        print(_json.dumps(indicators, ensure_ascii=False, indent=2))
