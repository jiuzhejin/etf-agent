"""
ETF 投资研报 Agent
================
顶层是模式菜单（方向键）：
  单只深度研报 — 完整 11 维分析 + 可追问（原有能力）
  批量快筛     — 从自选池挑多只，只出评分 + 空仓/持仓建议（省 token）
  管理自选池   — 分组维护批量快筛的标的池
  退出
"""

from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None
if load_dotenv:
    load_dotenv(Path(__file__).parent / ".env")

try:
    import gnureadline  # 修复 macOS 终端中文删除显示问题
except ImportError:
    gnureadline = None
import batch
import ui
import watchlist
from etf_analyzer import analyze_etf, ask_followup, format_report
from etf_data import calculate_indicators, fetch_etf_history
from etf_resolver import resolve_etf_code, validate_code

# 单只模式的会话状态
current_code = None
current_name = None
current_indicators = None


def _name_lookup(code: str) -> str:
    """code → name（用于自选池补名/刷新），查不到返回空串。"""
    info = validate_code(code)
    return info["name"] if info else ""


# ============================================================
# 单只深度研报模式（保留原有能力）
# ============================================================


def run_analysis(code: str, name: str) -> dict | None:
    """完整分析流程：拉数据 → 算指标 → LLM 分析"""
    global current_code, current_name, current_indicators

    df = fetch_etf_history(code)
    if df is None:
        print("数据获取失败，请稍后重试")
        return None

    indicators = calculate_indicators(df)
    if indicators is None:
        return None

    current_code = code
    current_name = name
    current_indicators = indicators

    return analyze_etf(indicators, etf_name=name)


def _offer_add_to_watchlist(code: str, name: str):
    """研报看完后顺手收藏：问是否加入自选池、加到哪个组。"""
    data = watchlist.load()
    if watchlist.has(data, code):
        return  # 已在池里，不打扰
    if not ui.confirm(f"\n加入自选池? （{code} {name}）", default=False):
        return
    group = _pick_group(data, "加入哪个组?", allow_new=True)
    if group is None:
        return
    ok, msg = watchlist.add_item(data, code, name, group)
    watchlist.save(data)
    print(f"  {msg}")


def handle_input(user_input: str):
    """处理单只模式的输入：解析到新标的→出研报；否则→追问。
    返回刚出研报的 (code, name)，没出研报返回 None。"""
    global current_code, current_name, current_indicators

    user_input = user_input.strip()
    if not user_input:
        return None

    try:
        has_context = current_indicators is not None
        result = resolve_etf_code(user_input)

        if result:
            code, name = result["code"], result["name"]
            if code == current_code:
                # 匹配到当前标的，当作追问
                print(f"正在基于 {current_name}（{current_code}）回答...\n")
                ask_followup(current_indicators, etf_name=current_name, user_question=user_input)
                return None

            report = run_analysis(code, name)
            if report:
                dq = current_indicators.get("data_quality") if current_indicators else None
                print(format_report(report, etf_name=name, code=code, data_quality=dq))
                return (code, name)
            return None

        elif has_context:
            print(f"正在基于 {current_name}（{current_code}）回答...\n")
            ask_followup(current_indicators, etf_name=current_name, user_question=user_input)
            return None

        else:
            print("请先输入一个 ETF 代码或名称")
            return None

    except KeyboardInterrupt:
        print("\n已取消")
        return None
    except Exception as e:
        print(f"\n出错了: {e}\n请重试或输入其他 ETF 代码")
        return None


def single_mode():
    """单只深度研报模式循环。输入 back 返回主菜单。"""
    print("\n— 单只深度研报 —")
    print("  输入 ETF 代码或名称分析 / 追问 / refresh 刷新 / back 返回菜单")

    while True:
        try:
            user_input = input("\n[单只] > ").strip()
        except (KeyboardInterrupt, EOFError):
            return

        if not user_input:
            continue
        low = user_input.lower()
        if low in ("back", "exit", "quit", "返回"):
            return
        if low == "refresh":
            if current_code:
                print(f"刷新 {current_name}（{current_code}）...")
                report = run_analysis(current_code, current_name)
                if report:
                    print(format_report(report, etf_name=current_name, code=current_code))
            else:
                print("当前没有分析的标的，请先输入 ETF 代码或名称")
            continue

        shown = handle_input(user_input)
        if shown:
            _offer_add_to_watchlist(shown[0], shown[1])


# ============================================================
# 自选池辅助：选组
# ============================================================


def _pick_group(data: dict, title: str, allow_new: bool = True) -> str | None:
    """方向键选一个组；allow_new 时末尾给"新建组"。返回组名或 None。"""
    grps = watchlist.groups(data)
    options = list(grps)
    if allow_new:
        options.append("＋ 新建组")
    idx = ui.select(options, title)
    if idx is None:
        return None
    if allow_new and idx == len(grps):
        name = ui.ask("新组名: ")
        return name or None
    return grps[idx]


# ============================================================
# 批量快筛模式
# ============================================================


def _temp_input_items() -> list[dict]:
    """临时输入一串代码/名称，解析成 [{code,name}]。"""
    raw = ui.ask("输入代码/名称（空格或逗号分隔）: ")
    if not raw:
        return []
    tokens = [t.strip() for t in raw.replace("，", ",").replace(",", " ").split() if t.strip()]
    items, seen = [], set()
    for t in tokens:
        info = resolve_etf_code(t, allow_llm=False)
        if info and info["code"] not in seen:
            items.append({"code": info["code"], "name": info["name"]})
            seen.add(info["code"])
        elif not info:
            print(f"  跳过：无法识别 {t}")
    return items


def batch_mode():
    """批量快筛：选组 → 多选标的 → 串行跑 lite → rich 表格。"""
    data = watchlist.load()
    items = watchlist.all_items(data)

    # 空池引导
    if not items:
        print("\n自选池为空。")
        opt = ui.select(
            ["从历史缓存导入（你之前看过的）", "临时输入一串代码", "返回菜单"], "怎么开始?"
        )
        if opt == 0:
            n = watchlist.import_from_cache(data, name_lookup=_name_lookup)
            watchlist.save(data)
            print(f"  已从缓存导入 {n} 只到「{watchlist.DEFAULT_GROUP}」组")
            items = watchlist.all_items(data)
            if not items:
                return
        elif opt == 1:
            chosen = _temp_input_items()
            if chosen:
                _run_and_offer_save(chosen, data, title="批量快筛 · 临时")
            return
        else:
            return

    # 选扫描范围：全部 / 某个非空组 / 临时输入
    nonempty = [g for g in watchlist.groups(data) if watchlist.items_in_group(data, g)]
    group_opts = [f"全部（{len(items)} 只）"]
    group_opts += [f"{g}（{len(watchlist.items_in_group(data, g))} 只）" for g in nonempty]
    group_opts.append("临时输入一串代码")
    gi = ui.select(group_opts, "扫描哪个组?")
    if gi is None:
        return
    if gi == 0:
        scope = items
        title = "批量快筛 · 全部"
    elif gi == len(group_opts) - 1:
        chosen = _temp_input_items()
        if chosen:
            _run_and_offer_save(chosen, data, title="批量快筛 · 临时")
        return
    else:
        g = nonempty[gi - 1]
        scope = watchlist.items_in_group(data, g)
        title = f"批量快筛 · {g}"

    # 组内多选（默认全选）
    labels = [f"{it['code']} {it['name']}".rstrip() for it in scope]
    sel = ui.multiselect(labels, "勾选要扫描的（空格选/回车确认，默认全选）", preselect_all=True)
    if sel is None:
        return
    picked = [scope[i] for i in sel]
    if not picked:
        print("没有选择标的")
        return

    print(f"\n开始快筛 {len(picked)} 只（串行，防频控）...")
    results, stats = batch.run_batch(picked)
    batch.render(results, stats, title)


def _run_and_offer_save(chosen: list[dict], data: dict, title: str):
    """跑临时输入的标的，跑完问要不要存入池子。"""
    print(f"\n开始快筛 {len(chosen)} 只（串行，防频控）...")
    results, stats = batch.run_batch(chosen)
    batch.render(results, stats, title)
    # 把没在池里的临时标的存入
    fresh = [it for it in chosen if not watchlist.has(data, it["code"])]
    if fresh and ui.confirm(f"\n把这 {len(fresh)} 只存入自选池?", default=False):
        group = _pick_group(data, "存入哪个组?", allow_new=True)
        if group:
            for it in fresh:
                watchlist.add_item(data, it["code"], it["name"], group)
            watchlist.save(data)
            print(f"  已存入「{group}」{len(fresh)} 只")


# ============================================================
# 管理自选池模式
# ============================================================


def _print_pool(data: dict):
    items = watchlist.all_items(data)
    if not items:
        print("  （自选池为空）")
        return
    for g in watchlist.groups(data):
        members = watchlist.items_in_group(data, g)
        if not members:
            continue
        print(f"\n  ▸ {g}（{len(members)}）")
        for it in members:
            note = f"  — {it['note']}" if it.get("note") else ""
            print(f"      {it['code']} {it['name']}{note}")


def _manage_groups(data: dict):
    while True:
        idx = ui.select(["新建组", "重命名组", "删除组", "返回"], "组管理")
        if idx is None or idx == 3:
            return
        if idx == 0:
            name = ui.ask("新组名: ")
            if name:
                ok, msg = watchlist.add_group(data, name)
                watchlist.save(data)
                print(f"  {msg}")
        elif idx == 1:
            gi = ui.select(watchlist.groups(data), "重命名哪个组?")
            if gi is not None:
                old = watchlist.groups(data)[gi]
                new = ui.ask(f"「{old}」改成: ")
                if new:
                    ok, msg = watchlist.rename_group(data, old, new)
                    watchlist.save(data)
                    print(f"  {msg}")
        elif idx == 2:
            gi = ui.select(watchlist.groups(data), "删除哪个组?")
            if gi is None:
                continue
            g = watchlist.groups(data)[gi]
            members = watchlist.items_in_group(data, g)
            reassign = None
            if members:
                others = [x for x in watchlist.groups(data) if x != g]
                if not others:
                    print("  只剩这一个组，不能删")
                    continue
                print(f"  组「{g}」下有 {len(members)} 只，需迁移到别组")
                ri = ui.select(others, "迁移到哪个组?")
                if ri is None:
                    continue
                reassign = others[ri]
            ok, msg = watchlist.delete_group(data, g, reassign)
            watchlist.save(data)
            print(f"  {msg}")


def _manage_feeders(data: dict):
    """为自选池里的 ETF 维护场外联接基金(A/C)映射：干净的自动填，有歧义的人工选。"""
    import feeder

    items = watchlist.all_items(data)
    if not items:
        print("  自选池为空，先添加标的")
        return
    fmap = feeder.load_map()
    # 只补未映射的，避免每次重选；想整体重做可先删 cache/etf_feeder_map.json
    todo = [it for it in items if it["code"] not in fmap]
    if not todo:
        print(f"  自选池 {len(items)} 只都已映射（如需重做，删 cache/etf_feeder_map.json）")
        return
    print(f"  正在为 {len(todo)} 只未映射的 ETF 匹配场外联接（首次会下载基金名录，稍候）...")
    auto_n = pick_n = skip_n = 0
    for it in todo:
        code = it["code"]
        name = it["name"] or _name_lookup(code)
        try:
            auto, pairs = feeder.auto_or_candidates(name)
        except Exception as e:
            print(f"    {code} {name}: 匹配失败 {e}")
            continue
        if auto:
            fmap[code] = {"name": name, "a": auto["a"], "c": auto["c"]}
            auto_n += 1
            print(f"    ✓ {code} {name} → C {auto['c']['code']} / A {auto['a']['code']}（自动）")
        else:
            note = "有多个联接，选对应的那只:"
            if not pairs:
                # 没有自家联接 → 放宽到同指数(跨管理人)，如 国泰军工ETF→广发军工联接
                pairs = feeder.suggest_pairs(name, relax_manager=True)
                note = "无自家联接，下面是同指数(跨管理人)的:"
            if not pairs:
                skip_n += 1
                print(f"    — {code} {name}: 没找到联接，跳过（可用『手动指定联接』补）")
                continue
            opts = [f"{p['base']}  → C {p['c']['code']} / A {p['a']['code']}" for p in pairs]
            opts.append("跳过这只")
            sel = ui.select(opts, f"{code} {name} {note}")
            if sel is None or sel == len(pairs):
                skip_n += 1
                continue
            p = pairs[sel]
            fmap[code] = {"name": name, "a": p["a"], "c": p["c"]}
            pick_n += 1
    feeder.save_map(fmap)
    print(f"  完成：自动 {auto_n}、人工选 {pick_n}、跳过 {skip_n}；映射表共 {len(fmap)} 只")


def _set_feeder_manual(data: dict):
    """手动给某只 ETF 指定联接：先列同指数候选(放宽管理人)给你选，选不到再手输代码。"""
    import feeder

    items = watchlist.all_items(data)
    if not items:
        print("  自选池为空，先添加标的")
        return
    labels = [f"{it['code']} {it['name']}".rstrip() for it in items]
    si = ui.select(labels, "给哪只 ETF 手动指定联接?")
    if si is None:
        return
    code = items[si]["code"]
    name = items[si]["name"] or _name_lookup(code)

    # 放宽管理人按主题搜（能带出同指数、跨管理人的联接）
    try:
        pairs = feeder.suggest_pairs(name, relax_manager=True)
    except Exception as e:
        print(f"  搜索失败: {e}")
        pairs = []

    opts = [f"{p['base']}  → C {p['c']['code']} / A {p['a']['code']}" for p in pairs]
    opts += ["手动输入联接代码", "取消"]
    sel = ui.select(opts, f"{code} {name} 的联接(已放宽到同指数):")
    if sel is None or sel == len(opts) - 1:
        return

    if sel == len(pairs):  # 手动输入代码
        a = ui.ask("A 类代码（没有就回车）: ")
        c = ui.ask("C 类代码（没有就回车）: ")
        ok, msg = feeder.set_manual(code, name, a, c)
    else:
        p = pairs[sel]
        ok, msg = feeder.set_manual(code, name, p["a"]["code"], p["c"]["code"])
    print(f"  {msg}")


def manage_mode():
    """管理自选池：查看 / 增删 / 移动 / 组管理 / 刷新名 / 联接映射 / 导入。"""
    while True:
        data = watchlist.load()
        actions = [
            "查看",
            "添加标的",
            "删除标的",
            "移动标的到别组",
            "组管理",
            "刷新名称",
            "维护联接基金映射",
            "手动指定联接",
            "从历史缓存导入",
            "返回菜单",
        ]
        idx = ui.select(actions, f"管理自选池（共 {len(watchlist.all_items(data))} 只）")
        if idx is None:
            return
        action = actions[idx]
        if action == "返回菜单":
            return

        if action == "查看":
            _print_pool(data)

        elif action == "添加标的":
            raw = ui.ask("输入代码或名称: ")
            if not raw:
                continue
            info = resolve_etf_code(raw)
            if not info:
                print("  无法识别")
                continue
            group = _pick_group(data, "加入哪个组?", allow_new=True)
            if group is None:
                continue
            ok, msg = watchlist.add_item(data, info["code"], info["name"], group)
            watchlist.save(data)
            print(f"  {msg}")

        elif action == "删除标的":
            items = watchlist.all_items(data)
            if not items:
                print("  （空）")
                continue
            labels = [f"{it['code']} {it['name']} [{it['group']}]".rstrip() for it in items]
            sel = ui.multiselect(labels, "勾选要删除的（默认不选）", preselect_all=False)
            if not sel:
                continue
            codes = [items[i]["code"] for i in sel]
            if ui.confirm(f"确认删除 {len(codes)} 只?", default=False):
                cnt = watchlist.remove_codes(data, codes)
                watchlist.save(data)
                print(f"  已删除 {cnt} 只")

        elif action == "移动标的到别组":
            items = watchlist.all_items(data)
            if not items:
                print("  （空）")
                continue
            labels = [f"{it['code']} {it['name']} [{it['group']}]".rstrip() for it in items]
            sel = ui.multiselect(labels, "勾选要移动的", preselect_all=False)
            if not sel:
                continue
            target = _pick_group(data, "移动到哪个组?", allow_new=True)
            if target is None:
                continue
            codes = [items[i]["code"] for i in sel]
            cnt = watchlist.move_codes(data, codes, target)
            watchlist.save(data)
            print(f"  已移动 {cnt} 只到「{target}」")

        elif action == "组管理":
            _manage_groups(data)

        elif action == "刷新名称":
            print("  正在刷新名称...")
            cnt = watchlist.update_names(data, _name_lookup)
            watchlist.save(data)
            print(f"  更新了 {cnt} 个名称")

        elif action == "维护联接基金映射":
            _manage_feeders(data)

        elif action == "手动指定联接":
            _set_feeder_manual(data)

        elif action == "从历史缓存导入":
            n = watchlist.import_from_cache(data, name_lookup=_name_lookup)
            watchlist.save(data)
            print(f"  已从缓存导入 {n} 只到「{watchlist.DEFAULT_GROUP}」组")


# ============================================================
# 主菜单
# ============================================================


def main():
    print("=" * 50)
    print("  ETF 投资研报 Agent")
    print("=" * 50)

    while True:
        # 菜单 emoji 必须用增补平面（U+1F300+，库与终端都按 2 宽渲染）；
        # 老符号区的 ⚡⭐（U+26xx/2Bxx）库 wcswidth 返回 -1 会让菜单错位，禁用。
        idx = ui.select(
            [
                "📊 单只深度研报 — 完整分析、可追问",
                "🔍 批量快筛 — 多只评分+建议，省 token",
                "🌟 管理自选池 — 分组维护标的池",
                "🚪 退出",
            ],
            "选择模式（↑↓ 选择，Enter 确认）",
        )
        if idx is None or idx == 3:
            print("再见")
            break
        if idx == 0:
            single_mode()
        elif idx == 1:
            batch_mode()
        elif idx == 2:
            manage_mode()


if __name__ == "__main__":
    main()
