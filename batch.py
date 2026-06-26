"""
批量快筛编排
================
对一批 ETF 串行跑：拉数据 → 算指标 → LLM lite，输出一张按评分排序的表。

设计要点：
- 串行（不并发）：数据源（东方财富等）有频控，并发拉数据等于自找 429；
  LLM 串行还能吃满 DeepSeek 的 prompt 缓存（system prompt 在多只之间命中）。
- 双层缓存：数据照旧走 fetch_etf_history 的缓存；lite 结论再缓存一层
  （键 code:date:settled），盘后同日反复跑零 token。盘中（settled=False）不缓存，
  因为半天的量能快照下次还是错的。
- 失败标 ?：某只挂了仍在表里占一行，不静默消失（和研报"标? 不瞎编"一致）。
"""

import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path

from etf_analyzer import (
    VALID_ACTIONS_EMPTY,
    VALID_ACTIONS_HOLDING,
    analyze_etf_lite,
    lite_logic_version,
)
from etf_data import calculate_indicators, fetch_etf_history

_CACHE_DIR = Path(__file__).parent / "cache"
_LITE_CACHE = _CACHE_DIR / "lite_cache.json"

# 估算单价（¥/百万 token），按 deepseek-reasoner 档（快筛与深度研报同模型，
# 输出含思考 token，按 reasoner 价计才不低估）。价格会变，仅作"约"估，可用环境变量覆盖。
_PRICE_IN_HIT = float(os.environ.get("LITE_PRICE_IN_HIT", "1.0"))
_PRICE_IN_MISS = float(os.environ.get("LITE_PRICE_IN_MISS", "4.0"))
_PRICE_OUT = float(os.environ.get("LITE_PRICE_OUT", "16.0"))


# ============================================================
# lite 结论缓存
# ============================================================


def _load_lite_cache() -> dict:
    if _LITE_CACHE.exists():
        try:
            with open(_LITE_CACHE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_lite_cache(cache: dict) -> None:
    _CACHE_DIR.mkdir(exist_ok=True)
    tmp = _LITE_CACHE.with_name(_LITE_CACHE.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)
    os.replace(tmp, _LITE_CACHE)


# ============================================================
# 跑批
# ============================================================


def _new_stats() -> dict:
    return {
        "n": 0,
        "llm_calls": 0,
        "cache_hits": 0,
        "fails": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cache_hit_tokens": 0,
        "cache_miss_tokens": 0,
        "cost": 0.0,
    }


def run_batch(items: list[dict]) -> tuple[list[dict], dict]:
    """
    items: [{code, name}, ...]
    串行处理，返回 (results, stats)。

    results 每行：
      {code, name, score, if_empty, if_holding, reason, settled, date, from_cache, error}
    """
    lite_cache = _load_lite_cache()
    ver = lite_logic_version()  # 模型/prompt 指纹，进缓存键 → 逻辑变则旧缓存自动失效
    results = []
    stats = _new_stats()
    stats["n"] = len(items)
    dirty = False
    n = len(items)

    for idx, it in enumerate(items, 1):
        code = str(it.get("code", "")).strip()
        name = it.get("name", "") or ""
        print(f"\r  分析中 [{idx}/{n}] {code} {name}".ljust(48), end="", flush=True)

        row = {
            "code": code,
            "name": name,
            "score": "?",
            "if_empty": "?",
            "if_holding": "?",
            "reason": "",
            "settled": True,
            "date": "",
            "from_cache": False,
            "error": None,
        }

        # 拉数据 + 算指标（抑制 fetch 的来源/缓存打印，保持批量输出干净）
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                df = fetch_etf_history(code)
                indicators = calculate_indicators(df) if df is not None else None
        except Exception as e:
            row["error"] = f"数据异常: {e}"
            stats["fails"] += 1
            results.append(row)
            continue

        if indicators is None:
            row["error"] = "数据不足/获取失败"
            stats["fails"] += 1
            results.append(row)
            continue

        dq = indicators.get("data_quality", {})
        date_s = dq.get("data_as_of", "")
        settled = bool(dq.get("settled", True))
        row["date"] = date_s
        row["settled"] = settled
        key = f"{code}:{date_s}:{int(settled)}:{ver}"

        # lite 缓存命中（仅 settled 才缓存）→ 零 token
        if settled and key in lite_cache:
            cached = lite_cache[key]
            for k in ("score", "if_empty", "if_holding", "reason"):
                row[k] = cached.get(k, "?")
            row["from_cache"] = True
            stats["cache_hits"] += 1
            results.append(row)
            continue

        # 调 LLM lite
        out = analyze_etf_lite(indicators, etf_name=name)
        stats["llm_calls"] += 1
        for k in ("score", "if_empty", "if_holding", "reason"):
            row[k] = out.get(k, "?")
        if out.get("_error"):
            row["error"] = out["_error"]
        usage = out.get("_usage")
        if usage:
            for k in (
                "prompt_tokens",
                "completion_tokens",
                "cache_hit_tokens",
                "cache_miss_tokens",
            ):
                stats[k] += usage.get(k, 0)

        # 写 lite 缓存（仅 settled 且成功）
        if settled and not out.get("_error"):
            lite_cache[key] = {k: out.get(k) for k in ("score", "if_empty", "if_holding", "reason")}
            dirty = True

        results.append(row)

    print("\r".ljust(50) + "\r", end="", flush=True)  # 清掉进度行
    if dirty:
        _save_lite_cache(lite_cache)

    # 成本估算：DeepSeek 用量带缓存命中/未命中拆分；若拿不到拆分则全按未命中计
    hit = stats["cache_hit_tokens"]
    miss = stats["cache_miss_tokens"]
    if hit + miss == 0:
        miss = stats["prompt_tokens"]
    stats["cost"] = (
        hit * _PRICE_IN_HIT + miss * _PRICE_IN_MISS + stats["completion_tokens"] * _PRICE_OUT
    ) / 1_000_000
    return results, stats


# ============================================================
# 渲染
# ============================================================


def _score_num(s) -> float:
    """把评分转成可排序的数；'?'/非法 → -1（排末尾）。"""
    try:
        return float(str(s).strip().split("/")[0])
    except (ValueError, AttributeError):
        return -1.0


def _mark_short(value, valid: set) -> str:
    """合法枚举原样；'?' 原样；越界值附短标记 ⚠。"""
    if not value or value == "?":
        return "?"
    return value if value in valid else f"{value}⚠"


def render(results: list[dict], stats: dict, title: str = "批量快筛") -> None:
    """打印 rich 表格 + token/花费统计。"""
    from rich import box
    from rich.console import Console
    from rich.table import Table

    console = Console()
    rows = sorted(results, key=lambda r: _score_num(r["score"]), reverse=True)

    has_intraday = any(not r["settled"] for r in results)

    table = Table(title=f"{title}  共 {stats['n']} 只", box=box.SIMPLE_HEAVY, header_style="bold")
    table.add_column("代码", style="cyan", no_wrap=True)
    table.add_column("名称", no_wrap=True)
    table.add_column("评分", justify="right", no_wrap=True)
    table.add_column("空仓", no_wrap=True)
    table.add_column("持有", no_wrap=True)
    table.add_column("一句话理由")
    table.add_column("联接基金(C)", style="cyan", no_wrap=True)  # 用户实际买的场外联接

    import feeder

    for r in rows:
        feeder_cell = feeder.feeder_cell(r["code"])
        if r["error"]:
            # 失败行：标 ?，理由处红字显示错误
            table.add_row(
                r["code"],
                r["name"] or "?",
                "[dim]?[/dim]",
                "?",
                "?",
                f"[red]✗ {r['error']}[/red]",
                feeder_cell,
            )
            continue

        sv = _score_num(r["score"])
        score_txt = str(r["score"])
        if not r["settled"]:
            score_txt += "*"
        if sv >= 7:
            score_cell = f"[bold green]{score_txt}[/bold green]"
        elif 0 <= sv <= 4:
            score_cell = f"[bold red]{score_txt}[/bold red]"
        else:
            score_cell = score_txt

        empty_cell = _mark_short(r["if_empty"], VALID_ACTIONS_EMPTY)
        hold_cell = _mark_short(r["if_holding"], VALID_ACTIONS_HOLDING)
        # 持仓建议偏空时染红，偏多染绿，便于扫
        if r["if_holding"] in ("减仓", "清仓"):
            hold_cell = f"[red]{hold_cell}[/red]"
        elif r["if_holding"] in ("加仓",):
            hold_cell = f"[green]{hold_cell}[/green]"

        reason = r["reason"]
        if r["from_cache"]:
            reason += "  [dim](缓存)[/dim]"
        table.add_row(
            r["code"], r["name"] or "?", score_cell, empty_cell, hold_cell, reason, feeder_cell
        )

    console.print()
    console.print(table)
    if has_intraday:
        console.print("[dim]* 盘中数据，量能为半天快照，评分仅供参考[/dim]")
    if not feeder.load_map():
        console.print("[dim]  联接基金列为空 → 「管理自选池 → 维护联接基金映射」可一键生成[/dim]")

    # token / 花费统计
    in_tokens = stats["prompt_tokens"]
    out_tokens = stats["completion_tokens"]
    hit_tokens = stats["cache_hit_tokens"]
    parts = [
        f"LLM 调用 {stats['llm_calls']} 只",
        f"命中缓存 {stats['cache_hits']} 只(0 token)",
    ]
    if stats["fails"]:
        parts.append(f"失败 {stats['fails']} 只")
    summary = "  本次快筛：" + " | ".join(parts)
    detail = (
        f"  输入 {in_tokens / 1000:.1f}k token"
        f"（其中缓存命中 {hit_tokens / 1000:.1f}k）"
        f" | 输出 {out_tokens / 1000:.1f}k token"
        f" | 约 ¥{stats['cost']:.4f}（估算，价随 DeepSeek 调整）"
    )
    console.print(f"[dim]{summary}[/dim]")
    console.print(f"[dim]{detail}[/dim]")
    console.print("[dim]  快筛仅出结论；要细看请回主菜单对单只跑深度研报。不构成投资建议。[/dim]")
