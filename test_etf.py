"""
ETF 投资研报 Agent 测试
自动化测试（不调 LLM 的部分）+ 手动测试清单
"""

import json
from pathlib import Path

passed = 0
failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        print(f"  ✗ {name}  {detail}")


# ============================================================
# 1. ETF 代码解析（etf_resolver）
# ============================================================

print("\n=== 1. ETF 代码解析 ===\n")

from etf_resolver import fuzzy_match, is_pure_code, load_etf_list, validate_code

# 1.1 纯数字判断
check("纯6位数字", is_pure_code("512980") == True)
check("5位数字", is_pure_code("51298") == False)
check("7位数字", is_pure_code("5129801") == False)
check("带空格", is_pure_code(" 512980 ") == True)
check("带字母", is_pure_code("51298a") == False)
check("空字符串", is_pure_code("") == False)

# 1.2 ETF 列表加载
etf_list = load_etf_list()
check("ETF 列表非空", len(etf_list) > 0)
check("ETF 列表有代码和名称", "代码" in etf_list[0] and "名称" in etf_list[0])

# 1.3 代码验证
check("有效代码 512980", validate_code("512980") is not None)
check("有效代码返回名称", validate_code("512980")["name"] != "")
check("无效代码 999999", validate_code("999999") is None)
check("无效代码 000000", validate_code("000000") is None)

# 1.4 模糊匹配
matches = fuzzy_match("传媒")
check("模糊匹配'传媒'有结果", len(matches) > 0)
check("匹配结果包含512980", any(m["code"] == "512980" for m in matches))

matches2 = fuzzy_match("xyznotexist")
check("模糊匹配不存在的词", len(matches2) == 0)

matches3 = fuzzy_match("ETF")
check("模糊匹配'ETF'有多个结果", len(matches3) > 1)

# 1.5 从文本提取代码
import re

text = "帮我看看510500的走势"
code_match = re.search(r"\d{6}", text)
check("从文本提取6位数字", code_match is not None and code_match.group() == "510500")

text2 = "看短线"
code_match2 = re.search(r"\d{6}", text2)
check("无数字文本不提取", code_match2 is None)


# ============================================================
# 2. 数据获取与缓存（etf_data）
# ============================================================

print("\n=== 2. 数据获取与缓存 ===\n")

from etf_data import _CACHE_DIR, _cache_file, _exchange_prefix

# 2.1 缓存路径
check("缓存目录存在", _CACHE_DIR.exists())
check("缓存文件路径正确", str(_cache_file("512980")).endswith("512980_hist.json"))

# 2.2 交易所前缀判断（15x 深交所，5x/56x/58x 上交所）
check("159xxx → sz", _exchange_prefix("159915") == "sz")
check("512xxx → sh", _exchange_prefix("512980") == "sh")
check("562xxx → sh", _exchange_prefix("562800") == "sh")
check("588xxx → sh", _exchange_prefix("588000") == "sh")

# 2.3 已有缓存读取
cached_files = list(_CACHE_DIR.glob("*_hist.json"))
if cached_files:
    code = cached_files[0].stem.replace("_hist", "")
    with open(cached_files[0]) as f:
        cache = json.load(f)
    check(f"缓存文件 {code} 有 date 字段", "last_date" in cache)
    check(f"缓存文件 {code} 有 data 字段", "data" in cache)
    check(f"缓存文件 {code} data 非空", len(cache["data"]) > 0)
    # 检查数据字段完整性
    first_row = cache["data"][0]
    required_cols = ["日期", "开盘", "收盘", "最高", "最低", "成交量"]
    for col in required_cols:
        check(f"缓存数据包含 {col} 列", col in first_row, f"缺失: {list(first_row.keys())}")
else:
    print("  (跳过缓存测试，无缓存文件)")


# ============================================================
# 3. 指标计算（etf_data.calculate_indicators）
# ============================================================

print("\n=== 3. 指标计算 ===\n")

import pandas as pd

from etf_data import calculate_indicators

# 3.1 用已有缓存数据测试
if cached_files:
    code = cached_files[0].stem.replace("_hist", "")
    with open(cached_files[0]) as f:
        cache = json.load(f)
    df = pd.DataFrame(cache["data"])

    indicators = calculate_indicators(df)
    if indicators is None:
        print("  (数据不足，跳过指标测试)")
    else:
        # 基本信息
        check("有 price", "price" in indicators)
        check("有 change_pct", "change_pct" in indicators)
        check("有 date", "date" in indicators)
        check("price 是数字", isinstance(indicators["price"], (int, float)))

        # 均线
        check("有 ma", "ma" in indicators)
        check("有 ma5", "ma5" in indicators["ma"])
        check("有 ma20", "ma20" in indicators["ma"])
        check("有 ma60", "ma60" in indicators["ma"])
        check("ma5 是数字", isinstance(indicators["ma"]["ma5"], (int, float)))

        # 均线形态
        check("有 ma_pattern", "ma_pattern" in indicators)
        check(
            "ma_pattern 是有效值",
            indicators["ma_pattern"] in ["多头排列", "空头排列", "缠绕震荡", "数据不足"],
        )

        # MACD
        check("有 macd", "macd" in indicators)
        check("有 macd.dif", "dif" in indicators["macd"])
        check("有 macd.dea", "dea" in indicators["macd"])
        check("有 macd.signal", "signal" in indicators["macd"])
        check(
            "macd.signal 是有效值",
            indicators["macd"]["signal"] in ["金叉", "死叉", "DIF在DEA上方", "DIF在DEA下方"],
        )

        # RSI
        check("有 rsi_14", "rsi_14" in indicators)
        check("RSI 在 0-100", 0 <= indicators["rsi_14"] <= 100)
        check("有 rsi_status", "rsi_status" in indicators)

        # 量比
        check("有 volume_ratio", "volume_ratio" in indicators)
        check("有 volume_status", "volume_status" in indicators)

        # 布林带
        check("有 boll", "boll" in indicators)
        check("有 boll.upper", "upper" in indicators["boll"])
        check("有 boll.mid", "mid" in indicators["boll"])
        check("有 boll.lower", "lower" in indicators["boll"])
        check(
            "upper > mid > lower",
            indicators["boll"]["upper"] > indicators["boll"]["mid"] > indicators["boll"]["lower"],
        )

        # KDJ
        check("有 kdj", "kdj" in indicators)
        check("有 kdj.k", "k" in indicators["kdj"])

        # ATR
        check("有 atr_14", "atr_14" in indicators)
        check("ATR 为正数", indicators["atr_14"] > 0)

        # 支撑阻力
        check("有 support", "support" in indicators)
        check("有 resistance", "resistance" in indicators)
        check("resistance > support", indicators["resistance"] > indicators["support"])

        # 数据质量
        check("有 data_quality", "data_quality" in indicators)
        check("有 data_as_of", "data_as_of" in indicators["data_quality"])
        check("有 stale 标记", "stale" in indicators["data_quality"])

        # 3.2 指标数值合理性
        check("价格在均线合理范围", indicators["price"] > 0 and indicators["price"] < 10000)
        check("量比非负", indicators["volume_ratio"] is None or indicators["volume_ratio"] >= 0)

else:
    print("  (无缓存数据，跳过指标测试)")

# 3.3 数据不足时的处理
short_df = pd.DataFrame(
    {
        "日期": ["2026-01-01"] * 10,
        "开盘": [1.0] * 10,
        "收盘": [1.0] * 10,
        "最高": [1.0] * 10,
        "最低": [1.0] * 10,
        "成交量": [100] * 10,
        "涨跌幅": [0.0] * 10,
    }
)
result = calculate_indicators(short_df)
check("数据不足时返回 None", result is None)


# ============================================================
# 4. LLM 分析输出格式（etf_analyzer）
# ============================================================

print("\n=== 4. 研报格式化 ===\n")

from etf_analyzer import format_report

# 4.1 正常报告
mock_report = {
    "action": {"if_empty": "观望", "if_holding": "持有"},
    "reason": "测试理由",
    "current": {"price": "1.0", "change_pct": "+0.5%", "summary": "测试"},
    "ma_analysis": {"pattern": "缠绕震荡", "detail": "测试"},
    "momentum": {"macd": "测试", "rsi": "测试", "kdj": "测试"},
    "volume": {"ratio": "1.0", "analysis": "测试"},
    "bollinger": {"position": "中轨", "analysis": "测试"},
    "support_resistance": {"support": "1.0", "resistance": "2.0", "analysis": "测试"},
    "risk": {"max_drawdown": "-5%", "volatility": "中等", "warnings": ["风险1"]},
    "score": {"value": "5.0", "breakdown": "测试"},
}

output = format_report(mock_report, etf_name="测试ETF", code="000001")
check("format_report 返回字符串", isinstance(output, str))
check("包含 ETF 名称", "测试ETF" in output)
check("包含空仓建议", "观望" in output and "若空仓" in output)
check("包含持仓建议", "持有" in output and "若已持有" in output)
check("包含评分", "5.0" in output)
check("包含风险提示", "风险1" in output)

# 4.2 数据时效提示
dq_stale = {"data_as_of": "2026-05-28", "stale": True}
output_stale = format_report(mock_report, etf_name="测试", code="000001", data_quality=dq_stale)
check("过期数据有警告", "非最新" in output_stale)

dq_fresh = {"data_as_of": "2026-05-29", "stale": False}
output_fresh = format_report(mock_report, etf_name="测试", code="000001", data_quality=dq_fresh)
check("新鲜数据无警告", "非最新" not in output_fresh)

# 4.3 空报告
check("空报告返回失败提示", format_report(None) == "分析失败")

# 4.4 缺字段的报告（同时验证 action 为字符串时的旧格式兼容）
partial_report = {"action": "买入"}
output_partial = format_report(partial_report, etf_name="测试", code="000001")
check("缺字段不崩溃", isinstance(output_partial, str))
check("字符串 action 兼容旧格式", "操作建议: 买入" in output_partial)

# 4.5 操作建议枚举校验：合法值不标记，越界值标 ⚠
output_valid = format_report(mock_report, etf_name="测试", code="000001")
check("合法 action 不带警示标记", "非标准值" not in output_valid)
check("研报含免责脚注", "不构成投资建议" in output_valid)

offenum_report = dict(mock_report, action={"if_empty": "梭哈", "if_holding": "持有"})
output_offenum = format_report(offenum_report, etf_name="测试", code="000001")
check("越界 if_empty 被标记", "梭哈" in output_offenum and "非标准值" in output_offenum)
check(
    "同报告里合法的 if_holding 不被标记",
    "若已持有: 持有\n" in output_offenum or "若已持有: 持有" in output_offenum.split("理由")[0],
)

# 4.6 代码块剥离
from etf_analyzer import _strip_code_fence

check("剥离 ```json 包裹", _strip_code_fence('```json\n{"a":1}\n```') == '{"a":1}')
check("剥离 ``` 包裹", _strip_code_fence('```\n{"a":1}\n```') == '{"a":1}')
check("无包裹原样返回", _strip_code_fence('{"a":1}') == '{"a":1}')

# 4.7 字符串内未转义双引号确为非法 JSON（这是自动修复路径的触发条件）
bad_json = '{"macd": "信号仅为"弱转强"而非买入"}'
try:
    json.loads(bad_json)
    _bad_is_invalid = False
except json.JSONDecodeError:
    _bad_is_invalid = True
check("字符串内未转义双引号是非法JSON", _bad_is_invalid is True)


# ============================================================
# 5. 主流程逻辑（main.py）
# ============================================================

print("\n=== 5. 主流程逻辑 ===\n")

# 5.1 refresh 前无标的
import main as m

m.current_code = None
m.current_indicators = None
check("无标的时 current_code 为 None", m.current_code is None)

# 5.2 会话状态更新
m.current_code = "512980"
m.current_name = "传媒ETF"
m.current_indicators = {"price": 1.0, "data_quality": {"data_as_of": "2026-05-28", "stale": False}}
check("会话状态写入 code", m.current_code == "512980")
check("会话状态写入 name", m.current_name == "传媒ETF")
check("会话状态写入 indicators", m.current_indicators is not None)

# 重置
m.current_code = None
m.current_name = None
m.current_indicators = None


# ============================================================
# 6. 指标计算边界（背离符号修复 + 盘中量比）
# ============================================================

print("\n=== 6. 指标计算边界 ===\n")


def _mk_df(prices):
    """用收盘价序列造一个最简历史 DataFrame（≥60条供指标计算）"""
    n = len(prices)
    s = pd.Series(prices)
    return pd.DataFrame(
        {
            "日期": [f"2026-{(i // 28) % 12 + 1:02d}-{i % 28 + 1:02d}" for i in range(n)],
            "开盘": prices,
            "收盘": prices,
            "最高": [p * 1.005 for p in prices],
            "最低": [p * 0.995 for p in prices],
            "成交量": [1000 + i for i in range(n)],
            "涨跌幅": (s.pct_change().fillna(0) * 100).tolist(),
        }
    )


# 6.1 MACD 背离符号修复（回归测试）
# 深跌后缓慢爬升：价格回到近30日高点，但 DIF 仍为负。
# 旧代码用乘法阈值(max*0.8)在 DIF 为负时会误报"疑似顶背离"，
# 新代码用振幅加法容差，应判为"无明显背离"。
neg_dif_prices = [2.0 - 0.8 * i / 44 for i in range(45)]
_base = neg_dif_prices[-1]
neg_dif_prices += [_base + 0.06 * i / 29 for i in range(30)]
ind_neg = calculate_indicators(_mk_df(neg_dif_prices))
check(
    "DIF为负+价格近高点 不误报顶背离",
    ind_neg["macd"]["divergence"] == "无明显背离",
    f"实际: {ind_neg['macd']['divergence']}",
)

# 6.2 真顶背离仍能识别（价格新高，动能减弱）
top_prices = [1.5] * 30
top_prices += [1.5 + 0.5 * i / 14 for i in range(15)]
top_prices += [2.0 - 0.25 * i / 9 for i in range(10)]
top_prices += [1.75 + 0.30 * i / 14 for i in range(15)]
ind_top = calculate_indicators(_mk_df(top_prices))
check(
    "真顶背离能识别",
    ind_top["macd"]["divergence"] == "疑似顶背离",
    f"实际: {ind_top['macd']['divergence']}",
)

# 6.3 真底背离仍能识别（价格新低，动能抬升）
bot_prices = [2.0] * 30
bot_prices += [2.0 - 0.5 * i / 14 for i in range(15)]
bot_prices += [1.5 + 0.25 * i / 9 for i in range(10)]
bot_prices += [1.75 - 0.30 * i / 14 for i in range(15)]
ind_bot = calculate_indicators(_mk_df(bot_prices))
check(
    "真底背离能识别",
    ind_bot["macd"]["divergence"] == "疑似底背离",
    f"实际: {ind_bot['macd']['divergence']}",
)

# 6.4 背离标签始终是三个合法值之一
check("背离标签合法", ind_neg["macd"]["divergence"] in ["疑似顶背离", "疑似底背离", "无明显背离"])

# 6.5 盘中（未收盘）量比加警示
flat_prices = [1.5 + 0.001 * i for i in range(60)]
ind_intraday = calculate_indicators(_mk_df(flat_prices), settled=False)
check("盘中时有量比警示", "volume_ratio_note" in ind_intraday)
check("盘中时 volume_status 带盘中标注", "盘中" in ind_intraday.get("volume_status", ""))

# 6.6 已收盘（默认）不加警示
ind_settled = calculate_indicators(_mk_df(flat_prices), settled=True)
check("已收盘无量比警示", "volume_ratio_note" not in ind_settled)
check("已收盘 volume_status 无盘中标注", "盘中" not in ind_settled.get("volume_status", ""))

# 6.7 settled 默认从 df.attrs 读取，缺省视为已收盘
ind_default = calculate_indicators(_mk_df(flat_prices))
check("settled 缺省视为已收盘", "volume_ratio_note" not in ind_default)


# ============================================================
# 7. 自选池 watchlist
# ============================================================

print("\n=== 7. 自选池 watchlist ===\n")

import tempfile

import watchlist as wl

# 存储指到临时目录，不污染真实 cache/
_tmp = Path(tempfile.mkdtemp())
wl._DIR = _tmp
wl._FILE = _tmp / "watchlist.json"
wl._BAK = _tmp / "watchlist.json.bak"

d = wl._empty()
check("空池默认两组", wl.groups(d) == ["持仓", "观察"])
ok, _ = wl.add_item(d, "512980", "传媒ETF", "观察")
check("加入标的成功", ok and wl.has(d, "512980"))
ok2, _ = wl.add_item(d, "512980", "x", "持仓")
check("重复代码去重", ok2 is False and len(wl.all_items(d)) == 1)
wl.add_item(d, "510300", "沪深300", "持仓")
check("按组取标的", [i["code"] for i in wl.items_in_group(d, "持仓")] == ["510300"])
check(
    "移动标的", wl.move_codes(d, ["512980"], "持仓") == 1 and len(wl.items_in_group(d, "持仓")) == 2
)
ok3, _ = wl.add_group(d, "宽基")
check("新建组", ok3 and "宽基" in wl.groups(d))
ok4, _ = wl.rename_group(d, "宽基", "大盘")
check("重命名组", ok4 and "大盘" in wl.groups(d) and "宽基" not in wl.groups(d))
ok5, _ = wl.delete_group(d, "观察")
check("删空组", ok5)
ok6, _ = wl.delete_group(d, "持仓")
check("删非空组无目标→拒绝", ok6 is False)
ok7, _ = wl.delete_group(d, "持仓", "大盘")
check("删非空组迁移", ok7 and len(wl.items_in_group(d, "大盘")) == 2)
check("删除标的", wl.remove_codes(d, ["510300"]) == 1)

# save / load / bak
wl.save(d)
check("save 落盘", wl._FILE.exists())
d2 = wl.load()
check(
    "load 回读一致", [i["code"] for i in wl.all_items(d2)] == [i["code"] for i in wl.all_items(d)]
)
wl.add_item(d2, "159915", "创业板", "大盘")
wl.save(d2)
check("二次写生成 .bak", wl._BAK.exists())
wl._FILE.write_text("{ broken json", encoding="utf-8")
d3 = wl.load()
check("损坏主文件回退 .bak", any(i["code"] == "512980" for i in wl.all_items(d3)))

# normalize 容错
norm = wl._normalize(
    {"version": 1, "groups": ["A"], "items": [{"code": "512980", "name": "x", "group": "B"}]}
)
check("normalize 补未知组", "B" in norm["groups"])
dup = wl._normalize({"groups": ["A"], "items": [{"code": "1"}, {"code": "1"}]})
check("normalize 按 code 去重", len(dup["items"]) == 1)


# ============================================================
# 8. 批量快筛 batch
# ============================================================

print("\n=== 8. 批量快筛 batch ===\n")

import batch as bt

check(
    "score_num 解析",
    bt._score_num("7.5") == 7.5 and bt._score_num("?") == -1.0 and bt._score_num("5/10") == 5.0,
)
check("mark_short 合法值原样", bt._mark_short("观望", {"建仓", "观望"}) == "观望")
check("mark_short 越界标⚠", bt._mark_short("梭哈", {"持有"}) == "梭哈⚠")
check("mark_short 缺失→?", bt._mark_short("?", {"持有"}) == "?")

# lite 缓存 roundtrip
bt._LITE_CACHE = _tmp / "lite_cache.json"
bt._save_lite_cache({"k": {"score": "5"}})
check("lite 缓存 roundtrip", bt._load_lite_cache().get("k", {}).get("score") == "5")

# run_batch 编排：monkeypatch 掉数据/LLM，不联网不花 token
_lite_calls = {"n": 0}


def _fake_fetch(code):
    return "DF_" + code


def _fake_ind(df):
    code = df.split("_")[1]
    if code == "999999":
        return None  # 模拟数据不足
    settled = code != "111111"  # 111111 模拟盘中
    return {"data_quality": {"data_as_of": "2026-06-24", "settled": settled}}


def _fake_lite(ind, etf_name=""):
    _lite_calls["n"] += 1
    return {
        "score": "6",
        "if_empty": "观望",
        "if_holding": "持有",
        "reason": "x",
        "_usage": {
            "prompt_tokens": 100,
            "completion_tokens": 10,
            "cache_hit_tokens": 0,
            "cache_miss_tokens": 100,
        },
    }


bt.fetch_etf_history = _fake_fetch
bt.calculate_indicators = _fake_ind
bt.analyze_etf_lite = _fake_lite
bt._LITE_CACHE = _tmp / "lite_cache2.json"  # 干净缓存起步

_items = [
    {"code": "512980", "name": "传媒"},
    {"code": "111111", "name": "盘中"},
    {"code": "999999", "name": "坏"},
]
res, st = bt.run_batch(_items)
check("run_batch 行数=输入数", len(res) == 3)
check("失败标的有 error", any(r["error"] for r in res if r["code"] == "999999"))
check("盘中 settled=False", [r for r in res if r["code"] == "111111"][0]["settled"] is False)
check("成功标的有评分", [r for r in res if r["code"] == "512980"][0]["score"] == "6")
check("token 累计正确", st["completion_tokens"] == 20)  # 2 次成功调用 × 10

res2, st2 = bt.run_batch(_items)
check("settled 命中 lite 缓存", st2["cache_hits"] == 1)
check("盘中不缓存仍调 LLM", any(r["code"] == "111111" and not r["from_cache"] for r in res2))
check("缓存命中后 0 输出 token 那只", st2["llm_calls"] == 1)


# ============================================================
# 9. lite Prompt + settled 导出
# ============================================================

print("\n=== 9. lite Prompt + settled 导出 ===\n")

import etf_analyzer as ea

check("LITE 保留风控纪律", "操作纪律（风控优先" in ea.SYSTEM_PROMPT_LITE)
check("LITE 保留字段说明", "## 你会收到的数据字段说明" in ea.SYSTEM_PROMPT_LITE)
check("LITE 砍掉 11 项研报字段", "ma_analysis" not in ea.SYSTEM_PROMPT_LITE)
check(
    "LITE 含精简输出字段",
    "if_empty" in ea.SYSTEM_PROMPT_LITE and "if_holding" in ea.SYSTEM_PROMPT_LITE,
)
check("研报 Prompt 仍完整(对照)", "ma_analysis" in ea.SYSTEM_PROMPT)
check("LITE 比研报短", len(ea.SYSTEM_PROMPT_LITE) < len(ea.SYSTEM_PROMPT))

# settled 显式导出到 data_quality（用真实缓存 df 算一遍）
if cached_files:
    with open(cached_files[0]) as f:
        _c = json.load(f)
    _ind = calculate_indicators(pd.DataFrame(_c["data"]))
    if _ind is not None:
        check("data_quality 含 settled 字段", "settled" in _ind["data_quality"])
        check("settled 是布尔", isinstance(_ind["data_quality"]["settled"], bool))


# ============================================================
# 10. 场外联接基金映射 feeder
# ============================================================

print("\n=== 10. 联接基金映射 feeder ===\n")

import feeder as fd

# 合成全市场基金名录（不联网），覆盖"干净一对"和"多对歧义"两种
_fake_funds = [
    {"基金代码": "110026", "基金简称": "易方达创业板ETF联接A"},
    {"基金代码": "004744", "基金简称": "易方达创业板ETF联接C"},
    {"基金代码": "020732", "基金简称": "易方达创业板200ETF联接A"},
    {"基金代码": "020733", "基金简称": "易方达创业板200ETF联接C"},
    {"基金代码": "014110", "基金简称": "嘉实中证稀有金属主题ETF发起联接A"},
    {"基金代码": "014111", "基金简称": "嘉实中证稀有金属主题ETF发起联接C"},
    {"基金代码": "003017", "基金简称": "广发中证军工ETF联接A"},  # 跨管理人同指数
    {"基金代码": "005693", "基金简称": "广发中证军工ETF联接C"},
    {"基金代码": "159915", "基金简称": "创业板ETF易方达"},  # ETF 本身，无"联接"→应忽略
]
fd._load_fund_list = lambda: _fake_funds

# 多对歧义：创业板 匹配到 创业板/创业板200 两对，最短 base 排第一
pairs = fd.suggest_pairs("创业板ETF易方达")
check("联接匹配出 2 对", len(pairs) == 2)
check("最短 base 排第一(正解 004744)", pairs[0]["c"]["code"] == "004744")
auto, allp = fd.auto_or_candidates("创业板ETF易方达")
check("多对→不自动填", auto is None and len(allp) == 2)

# 干净一对：稀有金属 唯一 A/C → 自动
auto2, allp2 = fd.auto_or_candidates("稀有金属ETF嘉实")
check("唯一一对→自动填", auto2 is not None)
check("自动填 C 代码正确", auto2 and auto2["c"]["code"] == "014111")

# 不含"联接"的 ETF 本身不会被当候选
check(
    "ETF 本身不入候选",
    all("联接" in p["a"]["name"] for p in pairs) and all(p["a"]["code"] != "159915" for p in pairs),
)

# feeder_cell 显示（临时 map，不污染真实 cache）
fd._MAP_FILE = _tmp / "etf_feeder_map.json"
fd._map_cache = None
fd.save_map(
    {
        "562800": {
            "name": "x",
            "a": {"code": "014110", "name": "..A"},
            "c": {"code": "014111", "name": "..C"},
        }
    }
)
check("feeder_cell 优先 C", fd.feeder_cell("562800") == "014111 C")
check("未映射→—", fd.feeder_cell("999999") == "—")

# 放宽管理人：国泰军工无自家联接，严格=0，放宽后找到广发(同指数)
check("严格同管理人军工=0", len(fd.suggest_pairs("军工ETF国泰")) == 0)
relaxed = fd.suggest_pairs("军工ETF国泰", relax_manager=True)
check("放宽管理人找到同指数联接", len(relaxed) == 1 and relaxed[0]["c"]["code"] == "005693")

# 手动设置：代码按名录校验
ok_m, _ = fd.set_manual("512660", "军工ETF国泰", a_code="003017", c_code="005693")
check("手动设置有效代码成功", ok_m and fd.feeder_cell("512660") == "005693 C")
bad_m, _ = fd.set_manual("512660", "军工ETF国泰", c_code="000000")
check("手动设置假代码被拒", bad_m is False)


# ============================================================
# 总结
# ============================================================

print(f"\n{'=' * 50}")
print(f"  测试结果: {passed} 通过, {failed} 失败")
print(f"{'=' * 50}")

if failed > 0:
    print("\n需要修复以上失败的测试用例")
else:
    print("\n全部通过!")

print("""
============================================================
  手动测试清单（需要 LLM / 网络）
============================================================

  □ 输入 512980 → 生成完整研报
  □ 输入 "传媒" → 模糊匹配到 ETF
  □ 输入 "我想看看新能源" → LLM 提取关键词并匹配
  □ 追问 "看短线" → streaming 回答，不是完整研报
  □ 追问 "对比一下和科技ETF" → 应识别为追问
  □ 输入 "510500呢" → 提取代码，切换标的
  □ 输入 refresh → 刷新当前标的
  □ 输入 exit → 正常退出
  □ Ctrl+C 中断研报生成 → 显示"已取消"
  □ Ctrl+C 中断追问 → 回到输入
  □ 盘中运行 → 研报包含实时数据
  □ 非盘中运行 → 研报显示数据截至日期

  -- 操作纪律（风控）--
  □ 空头排列+大回撤(如512980/159869) → 持仓建议应为"减仓/清仓"，不是"持有"
  □ 缠绕震荡/多头排列(如588000) → 不应被强制减仓，按指标常规判断
  □ 操作建议与风险提示自洽（风险段警示下行时，动作不给"持有"）
  □ 每份研报底部都有"不构成投资建议"免责脚注
""")
