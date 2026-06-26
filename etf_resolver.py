"""
ETF 代码解析模块
用户输入 → ETF 代码

降级链路：
1. 纯数字 → AKShare 验证存在
2. 短文本 → AKShare 模糊匹配
3. 匹配失败 → LLM 提取关键词 → 再模糊匹配
"""

import re
import json
import os
from datetime import date
from pathlib import Path
try:
    import gnureadline  # 修复 macOS 终端中文删除显示问题
except ImportError:
    gnureadline = None

def _get_llm_config():
    # DeepSeek 走 Anthropic 格式端点，复用 anthropic SDK，只换 base_url/key/model
    key = os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", "")
    url = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/anthropic")
    model = os.environ.get("LLM_MODEL_FAST", "deepseek-chat")
    return key, url, model


# ============================================================
# ETF 列表缓存（启动时加载一次）
# ============================================================

_etf_list_cache = None
_CACHE_DIR = Path(__file__).parent / "cache"
_CACHE_DIR.mkdir(exist_ok=True)
_CACHE_FILE = _CACHE_DIR / "etf_list_cache.json"


def load_etf_list():
    """加载 ETF 列表，优先读本地缓存，每天更新一次"""
    global _etf_list_cache
    if _etf_list_cache is not None:
        return _etf_list_cache

    today = date.today().isoformat()

    # 尝试读本地缓存
    if _CACHE_FILE.exists():
        with open(_CACHE_FILE, "r") as f:
            cache = json.load(f)
        if cache.get("date") == today:
            _etf_list_cache = cache["data"]
            print(f"已加载 {len(_etf_list_cache)} 只 ETF（本地缓存）")
            return _etf_list_cache

    # 缓存不存在或过期，从 AKShare 拉取
    import akshare as ak
    print("正在从 AKShare 加载 ETF 列表...")
    df = ak.fund_etf_spot_em()
    _etf_list_cache = df[["代码", "名称"]].to_dict("records")

    # 写入本地缓存
    with open(_CACHE_FILE, "w") as f:
        json.dump({"date": today, "data": _etf_list_cache}, f, ensure_ascii=False)

    print(f"已加载 {len(_etf_list_cache)} 只 ETF（已缓存到本地）")
    return _etf_list_cache


# ============================================================
# 第1步：纯数字 → 验证代码是否存在
# ============================================================

def is_pure_code(user_input: str) -> bool:
    """判断是否为纯数字 ETF 代码"""
    return bool(re.match(r"^\d{6}$", user_input.strip()))


def validate_code(code: str) -> dict | None:
    """验证代码在 ETF 列表中是否存在，返回 {code, name} 或 None"""
    etf_list = load_etf_list()
    for item in etf_list:
        if item["代码"] == code:
            return {"code": item["代码"], "name": item["名称"]}
    return None


# ============================================================
# 第2步：模糊匹配
# ============================================================

def fuzzy_match(keyword: str) -> list[dict]:
    """用关键词在 ETF 名称中模糊匹配，返回匹配列表"""
    etf_list = load_etf_list()
    keyword = keyword.strip().upper()
    results = []
    for item in etf_list:
        if keyword in item["名称"].upper():
            results.append({"code": item["代码"], "name": item["名称"]})
    return results


# ============================================================
# 第3步：LLM 提取关键词
# ============================================================

def extract_keyword_by_llm(user_input: str) -> list[str]:
    """调用 LLM 从自然语言中提取 ETF 相关关键词"""
    try:
        import anthropic
        api_key, base_url, model = _get_llm_config()
        client = anthropic.Anthropic(api_key=api_key, base_url=base_url)
        response = client.messages.create(
            model=model,
            max_tokens=100,
            system="你是一个关键词提取器。从用户输入中提取可能的 ETF 或基金名称关键词。只返回关键词，用逗号分隔，不要返回任何其他内容。例如输入'我想看看传媒和科技哪个好'，返回'传媒,科技'。",
            messages=[{"role": "user", "content": user_input}],
        )
        text = response.content[0].text.strip()
        keywords = [kw.strip() for kw in text.split(",") if kw.strip()]
        return keywords
    except Exception:
        return []


# ============================================================
# 用户选择
# ============================================================

def _has_cache(code: str) -> bool:
    """检查该 ETF 是否有本地历史缓存（即用户之前看过）"""
    cache_file = Path(__file__).parent / "cache" / f"{code}_hist.json"
    return cache_file.exists()


def let_user_choose(matches: list[dict]) -> dict | None:
    """多个匹配结果时，让用户选择。有缓存记录的优先展示。"""
    # 分成"看过的"和"其他"
    cached = [m for m in matches if _has_cache(m["code"])]
    rest = [m for m in matches if not _has_cache(m["code"])]

    # 排序后的完整列表：缓存的在前
    ordered = cached + rest

    print(f"\n找到 {len(ordered)} 个匹配结果：")

    if cached:
        print("  ── 猜你想看 ──")
        for i, item in enumerate(cached, 1):
            print(f"  {i}. {item['code']} {item['name']}")
        if rest:
            print(f"  ── 其他 ──")

    start = len(cached) + 1
    for i, item in enumerate(rest, start):
        print(f"  {i}. {item['code']} {item['name']}")

    print(f"  0. 都不是，重新输入")

    while True:
        choice = input("\n请选择序号或代码: ").strip()
        if choice == "0":
            return None
        if choice.isdigit():
            n = int(choice)
            # 先当序号试
            if 1 <= n <= len(ordered):
                return ordered[n - 1]
            # 再当6位代码试
            if len(choice) == 6:
                for item in ordered:
                    if item["code"] == choice:
                        return item
        print("输入无效，请重新选择")


# ============================================================
# 主流程：解析用户输入 → ETF 代码
# ============================================================

def resolve_etf_code(user_input: str, allow_llm: bool = True) -> dict | None:
    """
    解析用户输入，返回 {code, name} 或 None

    降级链路：
    1. 纯数字 → 验证存在
    2. 文本 → 模糊匹配
    3. 失败 → LLM 提取关键词 → 再匹配（allow_llm=False 时跳过）
    """
    user_input = user_input.strip()
    if not user_input:
        return None

    # 第1步：纯数字
    if is_pure_code(user_input):
        result = validate_code(user_input)
        if result:
            print(f"找到: {result['code']} {result['name']}")
            return result
        else:
            print(f"代码 {user_input} 不存在，请检查后重试")
            return None

    # 第1.5步：从文本中提取6位数字（如"510500呢"→"510500"）
    code_match = re.search(r"\d{6}", user_input)
    if code_match:
        result = validate_code(code_match.group())
        if result:
            print(f"找到: {result['code']} {result['name']}")
            return result

    # 第2步：直接模糊匹配
    matches = fuzzy_match(user_input)
    if len(matches) == 1:
        print(f"找到: {matches[0]['code']} {matches[0]['name']}")
        return matches[0]
    if len(matches) > 1:
        return let_user_choose(matches)

    # 第3步：LLM 提取关键词，再匹配
    if not allow_llm:
        return None

    print("正在智能识别...")
    keywords = extract_keyword_by_llm(user_input)
    if not keywords:
        print("无法识别 ETF 名称")
        return None

    # 过滤掉泛关键词
    _STOP_WORDS = {"ETF", "基金", "指数", "联接", "LOF"}
    keywords = [kw for kw in keywords if kw.upper() not in _STOP_WORDS]
    if not keywords:
        return None

    all_matches = []
    seen_codes = set()
    for kw in keywords:
        for m in fuzzy_match(kw):
            if m["code"] not in seen_codes:
                all_matches.append(m)
                seen_codes.add(m["code"])

    if len(all_matches) == 0:
        # 关键词未匹配到，静默返回 None（由上层处理追问）
        return None
    if len(all_matches) == 1:
        print(f"找到: {all_matches[0]['code']} {all_matches[0]['name']}")
        return all_matches[0]
    return let_user_choose(all_matches)


# ============================================================
# 单独测试
# ============================================================

if __name__ == "__main__":
    print("=== ETF 代码解析测试 ===\n")
    while True:
        user_input = input("\n输入 ETF 代码或名称 (exit 退出): ").strip()
        if user_input.lower() == "exit":
            break
        result = resolve_etf_code(user_input)
        if result:
            print(f"\n✓ 最终结果: {result['code']} {result['name']}")
        else:
            print("\n✗ 未能解析")
