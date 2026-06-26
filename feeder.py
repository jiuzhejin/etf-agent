"""
场外 ETF 联接基金映射
================
用户买的是场外联接基金（按净值申赎，A/C 两类），不是场内 ETF。这个模块维护
一张「ETF 代码 → 联接基金(A/C)」的**核对过的**映射，给批量快筛表补一列。

为什么是核对过的静态表、不是实时猜：联接代码买错=真金白银买错标的，绝不能让
启发式或 LLM 猜。流程是「akshare 拉候选 → 干净的(唯一 A/C 对)自动填、有歧义的
人工选 → 落 cache/etf_feeder_map.json」。批量渲染只读这张表，离线、零成本。

映射表结构：
  {
    "562800": {"name":"稀有金属ETF嘉实",
               "a":{"code":"014110","name":"嘉实中证稀有金属主题ETF发起联接A"},
               "c":{"code":"014111","name":"...C"}}
  }
"""

import json
import os
import re
from datetime import date
from pathlib import Path

_DIR = Path(__file__).parent / "cache"
_MAP_FILE = _DIR / "etf_feeder_map.json"
_FUND_LIST_CACHE = _DIR / "fund_name_cache.json"

_CLASS_LETTERS = "ABCEIJOY"  # 基金份额类别后缀


# ============================================================
# 映射表读写
# ============================================================

_map_cache = None


def load_map() -> dict:
    global _map_cache
    if _map_cache is not None:
        return _map_cache
    if _MAP_FILE.exists():
        try:
            with open(_MAP_FILE, "r", encoding="utf-8") as f:
                _map_cache = json.load(f)
                return _map_cache
        except (json.JSONDecodeError, OSError):
            pass
    _map_cache = {}
    return _map_cache


def save_map(m: dict) -> None:
    global _map_cache
    _DIR.mkdir(exist_ok=True)
    tmp = _MAP_FILE.with_name(_MAP_FILE.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _MAP_FILE)
    _map_cache = m


def feeder_cell(etf_code: str, prefer: str = "c") -> str:
    """
    批量表里的"联接基金"列内容：优先返回 C 类代码（短线无申购费），
    缺 C 用 A，都没有返回 "—"。
    """
    entry = load_map().get(str(etf_code))
    if not entry:
        return "—"
    first = entry.get(prefer) or entry.get("a") or entry.get("c")
    if not first:
        return "—"
    cls = "C" if first is entry.get("c") else "A"
    return f"{first['code']} {cls}"


# ============================================================
# 候选匹配（需要 akshare，仅维护映射时调用）
# ============================================================

def _load_fund_list():
    """全市场基金名录，按天缓存（27000+ 条，避免反复下载）。"""
    today = date.today().isoformat()
    if _FUND_LIST_CACHE.exists():
        try:
            with open(_FUND_LIST_CACHE, "r", encoding="utf-8") as f:
                c = json.load(f)
            if c.get("date") == today:
                return c["data"]
        except (json.JSONDecodeError, OSError):
            pass
    import akshare as ak
    df = ak.fund_name_em()
    data = df[["基金代码", "基金简称"]].to_dict("records")
    _DIR.mkdir(exist_ok=True)
    with open(_FUND_LIST_CACHE, "w", encoding="utf-8") as f:
        json.dump({"date": today, "data": data}, f, ensure_ascii=False)
    return data


def _class_of(name: str) -> str | None:
    """基金简称末尾的份额类别字母（含联接才算）。"""
    if "联接" not in name:
        return None
    last = name[-1]
    return last if last in _CLASS_LETTERS else None


def suggest_pairs(etf_name: str, relax_manager: bool = False) -> list[dict]:
    """
    给一个 ETF 名（如"创业板ETF易方达"）或主题词（如"军工"），返回候选联接
    「A/C 对」列表：
      [{"base": "易方达创业板ETF联接", "a": {code,name}, "c": {code,name}}, ...]
    按 base 名长度升序（最短的通常是最朴素的正解，排最前）。

    匹配口径：联接基金名同时含 ETF 的「主题」和「管理人」。主题/管理人由
    ETF 名以 "ETF" 切分得到（"创业板ETF易方达" → 主题=创业板, 管理人=易方达）。
    relax_manager=True 时放宽管理人，只按主题匹配——用来找"同指数、跨管理人"的
    联接（例如 国泰军工ETF 没有自家联接，但同指数有广发中证军工ETF联接）。
    """
    parts = etf_name.split("ETF")
    theme = parts[0]
    mgr = "" if relax_manager else (parts[1] if len(parts) > 1 else "")

    groups: dict[str, dict] = {}
    for row in _load_fund_list():
        name = row["基金简称"]
        if "联接" not in name or theme not in name:
            continue
        if mgr and mgr not in name:
            continue
        cls = _class_of(name)
        if cls is None:
            continue
        base = name[:-1]  # 去掉末尾类别字母
        groups.setdefault(base, {})[cls] = {"code": row["基金代码"], "name": name}

    pairs = []
    for base, classes in groups.items():
        if "A" in classes and "C" in classes:
            pairs.append({"base": base, "a": classes["A"], "c": classes["C"]})
    pairs.sort(key=lambda p: len(p["base"]))
    return pairs


def auto_or_candidates(etf_name: str):
    """
    返回 (auto_pair, all_pairs)：
      - 只有一对 A/C → auto_pair 为那一对，可自动填
      - 0 或 ≥2 对 → auto_pair 为 None，需要人工在 all_pairs 里选（或跳过）
    """
    pairs = suggest_pairs(etf_name)
    auto = pairs[0] if len(pairs) == 1 else None
    return auto, pairs


def lookup_fund(code: str) -> str | None:
    """按基金代码查名称（校验手动输入的联接代码确实存在），查不到返回 None。"""
    code = str(code).strip()
    for r in _load_fund_list():
        if r["基金代码"] == code:
            return r["基金简称"]
    return None


def set_manual(etf_code: str, etf_name: str,
               a_code: str = "", c_code: str = "") -> tuple[bool, str]:
    """手动给某只 ETF 设置联接(A/C)。代码会按基金名录校验，防止填错。"""
    a_code = (a_code or "").strip()
    c_code = (c_code or "").strip()
    if not a_code and not c_code:
        return False, "至少给一个 A 或 C 代码"
    entry = {"name": etf_name}
    for cls, code in (("a", a_code), ("c", c_code)):
        if not code:
            continue
        fund_name = lookup_fund(code)
        if not fund_name:
            return False, f"代码 {code} 不在基金名录，请核对"
        entry[cls] = {"code": code, "name": fund_name}
    m = load_map()
    m[str(etf_code)] = entry
    save_map(m)
    return True, f"已设置 {etf_code} → " + " / ".join(
        f"{c.upper()} {entry[c]['code']}" for c in ("c", "a") if c in entry)
