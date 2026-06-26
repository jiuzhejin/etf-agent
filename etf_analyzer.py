"""
ETF 分析模块
接收本地计算好的指标 dict → 调用 LLM → 返回结构化研报
"""

import json
import os
import hashlib
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

def _get_llm_config():
    # DeepSeek 走 Anthropic 格式端点，复用 anthropic SDK，只换 base_url/key/model
    key = os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", "")
    url = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/anthropic")
    model = os.environ.get("LLM_MODEL", "deepseek-chat")
    return key, url, model


def _get_openai_base_url():
    # JSON Mode（response_format）是 OpenAI 格式特性，必须走 OpenAI 端点，
    # Anthropic 端点会静默忽略该参数。这里把 /anthropic 后缀去掉得到 OpenAI 端点。
    url = os.environ.get("LLM_BASE_URL_OPENAI")
    if url:
        return url
    base = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/anthropic")
    return base[: -len("/anthropic")] if base.endswith("/anthropic") else base

SYSTEM_PROMPT = """你是一位专业的 ETF 技术分析师。你只基于用户提供的技术指标数据进行分析，不编造任何基本面信息。

## 你会收到的数据字段说明

基本信息：
- price: 最新收盘价
- change_pct: 当日涨跌幅(%)
- date: 数据日期

均线系统：
- ma: 各周期均线值（ma5/ma10/ma20/ma60/ma120/ma250）
- ma_pattern: 均线形态（多头排列/空头排列/缠绕震荡）
- ma20_slope: MA20 斜率方向（上翘/走平/下探）

MACD：
- macd.dif/dea/histogram: MACD 三个数值
- macd.signal: 金叉/死叉/DIF在DEA上方/DIF在DEA下方
- macd.divergence: 背离情况

RSI：
- rsi_14: 14日RSI值
- rsi_status: 超买(>70)/超卖(<30)/正常

量价：
- volume_ratio: 量比（今日成交量/过去5日均量）
- volume_status: 放量/缩量/正常
- vol_price_relation: 量价关系判断

布林带：
- boll.upper/mid/lower: 上中下轨
- boll.position: 价格在布林带中的位置
- boll.trend: 带宽趋势（扩张/收窄/平稳）
- boll.bandwidth_pct: 带宽百分比

KDJ：
- kdj.k/d/j: KDJ三值
- kdj.status: 超买/超卖/正常

其他：
- atr_14: 14日平均真实波幅
- atr_pct: ATR占价格的百分比（衡量波动率）
- max_drawdown_60d: 近60天最大回撤(%)
- support: 近20日支撑位
- resistance: 近20日阻力位

## 操作纪律（风控优先，给建议时必须遵守）

你只做**短线技术面**分析，看不到估值、基本面、用户的持仓成本和投资期限。在这个前提下守住下面的纪律，不要当"赌跌够了就反弹"的赌徒：

1. **趋势优先于超卖**：只有当价格跌破全部均线、均线**空头排列**、MA20 向下时，才算已确认的下跌趋势。此时 RSI/KDJ 超卖本身**不构成持有或抄底的理由**——超卖可以钝化、可以更超卖。
2. **确认下跌趋势中优先保本**：在上述空头趋势成立、且持续下跌**无企稳信号**时，持仓建议应倾向**减仓**甚至**清仓**（保本优先）。企稳/反转信号包括：放量止跌、MACD 底背离或金叉、KDJ 金叉上穿、价格站回 MA20。出现这些信号前不要给"持有"。
3. **跌幅是沉没成本，不是决策依据**：已经跌掉的回撤属于过去，"跌这么多该止损"和"跌这么多舍不得割"都是锚定过去的错误。决策只看**前瞻信号**（趋势方向、动能、有无企稳）。回撤和 atr_pct 只用来衡量该标的**当前风险有多高**，不是买卖扳机——不同品种正常波动差别很大（宽基 -15% 已属严重，高波动行业 ETF 到 -20%~-25% 可能仍在常态），别用固定百分比一刀切。
4. **减仓也讲执行节奏**：确认下跌趋势里决定减仓时，若价格正贴着支撑位，不必在支撑上市价砸出；可在理由里提示"逢反弹到 MA5/MA10 附近分批减"，把"该减"和"别割在地板上"调和。但这是执行节奏，结论仍是减仓，不是持有。
5. **震荡/多头不适用保本减仓**：均线呈"缠绕震荡"或"多头排列"时，**不属于**已确认下跌趋势，不适用第2条，按指标综合常规判断（震荡可能是筑底，不要预设离场）。
6. **空仓不接飞刀**：确认下跌趋势中空仓应"观望"，不因为超卖就建仓。
7. **动作必须与风险提示自洽**：如果你的风险提示在警示下行风险，操作建议就不能装作没事给"持有"。风险段喊小心、动作段装没事，是自相矛盾。

## 综合评分口径

综合评分（score）是 1-10 的总分，**必须按以下四个维度各打 1-10 分后综合得出，不要只凭整体印象拍一个数**：
- **趋势**：均线排列（多头/空头/缠绕）、MA20 方向、价格与各均线的关系
- **动量**：MACD、RSI、KDJ —— 注意超买（RSI>70 / KDJ 高位）会压制动量分，不是越高越好
- **量价**：量比、量价配合（放量上涨健康，缩量上涨或放量滞涨打折）
- **风险**：最大回撤、波动率（atr_pct）、是否超买/突破布林上轨（回调风险）或破位/触及下轨

总分约等于四个维度的均值，可按当前**主导矛盾**适当微调（例如趋势很强但已严重超买，风险维度会把总分往下拉，不应给到 8+）。这套口径在「完整研报」和「批量快筛」里完全一致。

## 输出要求

你无法得知用户当前是否持有该 ETF、持仓成本是多少，因此操作建议必须分「空仓」和「已持有」两个分支分别给出。同一个技术面信号对两类用户含义往往相反（猛涨时空仓者纠结要不要追、持仓者纠结要不要落袋），不要用一个标签糊弄两类人。

你必须严格按照以下 JSON 格式返回，不要输出任何 JSON 以外的内容。

**重要：字符串值内部若要引用词语，一律用中文引号「」或单引号，绝不要用英文双引号 " —— 它会破坏 JSON 格式。** 例如写成「弱转强」而不是 "弱转强"。

```json
{
  "action": {
    "if_empty": "空仓时的建议：建仓/观望 中选一个",
    "if_holding": "已持有时的建议：加仓/持有/减仓/清仓 中选一个"
  },
  "reason": "一句话说明判断理由（技术面依据，两个分支共用）",
  "current": {
    "price": "当前价格",
    "change_pct": "今日涨跌幅",
    "summary": "一句话描述当前状态"
  },
  "ma_analysis": {
    "pattern": "均线形态",
    "detail": "均线分析要点，包括价格与各均线的关系"
  },
  "momentum": {
    "macd": "MACD信号及含义",
    "rsi": "RSI状态及含义",
    "kdj": "KDJ状态及含义"
  },
  "volume": {
    "ratio": "量比数值",
    "analysis": "量价关系分析"
  },
  "bollinger": {
    "position": "价格在布林带中的位置",
    "analysis": "布林带分析"
  },
  "support_resistance": {
    "support": "支撑位",
    "resistance": "阻力位",
    "analysis": "当前价格距支撑/阻力的距离和含义"
  },
  "risk": {
    "max_drawdown": "近60天最大回撤",
    "volatility": "波动率水平",
    "warnings": ["风险点1", "风险点2"]
  },
  "score": {
    "value": "1-10的综合评分，1最差10最好",
    "breakdown": "评分依据简述"
  }
}
```

## 示例

用户输入数据（部分）：
price: 2.15, ma_pattern: 多头排列, macd.signal: 金叉, rsi_14: 58, volume_ratio: 1.6, boll.position: 中轨附近

你的输出：
```json
{
  "action": {
    "if_empty": "建仓",
    "if_holding": "加仓"
  },
  "reason": "均线多头排列叠加MACD金叉，量能配合放大，短中期趋势向好",
  "current": {
    "price": "2.15",
    "change_pct": "+1.2%",
    "summary": "价格站上所有短期均线，处于上升趋势中"
  },
  "ma_analysis": {
    "pattern": "多头排列",
    "detail": "MA5>MA10>MA20>MA60，短中长期均线向上发散，价格在MA5上方运行，趋势健康"
  },
  "momentum": {
    "macd": "DIF上穿DEA形成金叉，红柱开始放大，上涨动量正在加速",
    "rsi": "RSI=58处于正常区间，距离超买还有空间",
    "kdj": "K=65 D=58 J=79，处于强势区间但未超买"
  },
  "volume": {
    "ratio": "1.6（温和放量）",
    "analysis": "上涨伴随放量，资金参与度提升，量价配合健康"
  },
  "bollinger": {
    "position": "中轨上方运行",
    "analysis": "价格沿中轨上方运行，布林带开口扩大，趋势性行情延续"
  },
  "support_resistance": {
    "support": "2.05（MA20）",
    "resistance": "2.25（近20日高点）",
    "analysis": "距支撑位4.7%，有一定安全边际；距阻力位4.7%，仍有上行空间"
  },
  "risk": {
    "max_drawdown": "-6.2%",
    "volatility": "中等（ATR占比2.1%）",
    "warnings": ["连续上涨后注意短期获利回吐", "关注MA5能否继续上翘"]
  },
  "score": {
    "value": "7.5",
    "breakdown": "趋势(8)+动量(7)+量价(8)+风险(7)，综合偏多但注意追高风险"
  }
}
```
"""


# ============================================================
# 批量快筛（lite）Prompt
# 复用研报 Prompt 的「字段说明 + 风控纪律」内核（从 SYSTEM_PROMPT 里切出来，
# 单一来源、不漂移），只把 11 项结构化输出换成 4 个结论字段。
# 风控纪律是「空仓/持仓建议」可信的根，删了 chat 模型会退化成"超卖就持有"
# 的赌徒，所以快筛也必须保留它。
# ============================================================
_LITE_CORE_START = SYSTEM_PROMPT.index("## 你会收到的数据字段说明")
_LITE_CORE_END = SYSTEM_PROMPT.index("## 输出要求")
_SHARED_CORE = SYSTEM_PROMPT[_LITE_CORE_START:_LITE_CORE_END].rstrip()

SYSTEM_PROMPT_LITE = (
    "你是一位专业的 ETF 技术分析师。你只基于用户提供的技术指标数据进行分析，"
    "不编造任何基本面信息。\n\n"
    "这是「批量快筛」模式：用户一次扫多只 ETF，只看结论。"
    "但你的分析逻辑必须和完整研报完全一致——在内部照样完整权衡趋势、"
    "动量（含 RSI/KDJ 超买超卖）、量价、布林带、风险各个维度，"
    "尤其是相互矛盾的信号（例如趋势强劲但已严重超买、突破布林上轨需防回调），"
    "评分要把这些风险如实折算进去，不能只看多头排列就给高分。"
    "只是最终不要逐项展开文字解读，只输出下面要求的几个汇总字段。\n\n"
    f"{_SHARED_CORE}\n\n"
    "## 输出要求\n\n"
    "你无法得知用户当前是否持有该 ETF，因此操作建议必须分「空仓」和「已持有」"
    "两个分支分别给出（同一技术面信号对两类用户含义往往相反）。\n\n"
    "严格按以下 JSON 格式返回，不要输出任何 JSON 以外的内容。"
    "字符串值内部若要引用词语，一律用中文引号「」，绝不用英文双引号 \" —— 它会破坏 JSON。\n\n"
    "```json\n"
    "{\n"
    '  "score": "1-10 的综合评分，按上面四维度口径综合得出，可带一位小数",\n'
    '  "if_empty": "空仓时的建议：建仓/观望 中选一个",\n'
    '  "if_holding": "已持有时的建议：加仓/持有/减仓/清仓 中选一个",\n'
    '  "reason": "一句话理由（25 字内，技术面依据；若有超买/突破上轨/破位等主要风险须一并点出，不能只报利好）"\n'
    "}\n"
    "```\n"
)


FOLLOWUP_SYSTEM_PROMPT = """你是一位专业的 ETF 技术分析师。用户已经看过一份完整的技术分析研报，现在有追问。

请基于提供的技术指标数据，直接回答用户的问题。要求：
- 简洁明了，直接回答问题，不要重复完整研报
- 结合具体数据说话，不要空泛
- 只基于提供的数据分析，不编造基本面信息
"""


def ask_followup(indicators: dict, etf_name: str, user_question: str):
    """
    追问模式：streaming 输出，直接回答用户问题

    直接打印到终端，不返回值
    """
    import anthropic
    api_key, base_url, model = _get_llm_config()
    client = anthropic.Anthropic(api_key=api_key, base_url=base_url)

    user_content = f"## {etf_name}（{indicators.get('date', '')}）技术指标数据\n\n"
    user_content += "```json\n"
    user_content += json.dumps(indicators, ensure_ascii=False, indent=2)
    user_content += "\n```\n"
    user_content += f"\n用户追问：{user_question}"

    try:
        with client.messages.stream(
            model=model,
            max_tokens=4000,  # reasoner 的思考 token 也占额度，给足避免回答截断
            system=FOLLOWUP_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        ) as stream:
            for text in stream.text_stream:
                print(text, end="", flush=True)
        print()  # 结尾换行
    except Exception as e:
        print(f"\nLLM 调用失败: {e}")


_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _strip_code_fence(text: str) -> str:
    """去掉可能的 markdown 代码块标记"""
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _repair_json_with_llm(broken: str) -> str | None:
    """
    用快模型（Haiku）把无效 JSON 修成合法 JSON。
    最常见的错误：字符串值内部有未转义的英文双引号（如 "信号仅为"弱转强"…"）。
    这是机械修复，用便宜的快模型即可。
    """
    try:
        import anthropic
        api_key, base_url, _ = _get_llm_config()
        fast_model = os.environ.get("LLM_MODEL_FAST", "deepseek-chat")
        client = anthropic.Anthropic(api_key=api_key, base_url=base_url)
        resp = client.messages.create(
            model=fast_model,
            max_tokens=2500,
            system="你是 JSON 修复器。输入是一段无效 JSON，最常见的错误是字符串值内部出现了未转义的英文双引号。请把它修成合法 JSON：保持所有内容文字不变，只修复格式（转义或替换内部引号等）。只输出修正后的 JSON 本身，不要加任何解释、不要加 markdown 代码块。",
            messages=[{"role": "user", "content": broken}],
        )
        return resp.content[0].text
    except Exception:
        return None


def analyze_etf(indicators: dict, etf_name: str = "") -> dict | None:
    """
    调用 LLM 分析 ETF 指标数据，返回结构化研报 dict
    使用 streaming 收集 JSON（支持 Ctrl+C 中断），同时显示旋转动画
    """
    # 研报需要结构化 JSON，启用 DeepSeek JSON Mode（response_format）硬保证输出是合法 JSON。
    # JSON Mode 是 OpenAI 格式特性，故这里用 OpenAI SDK + OpenAI 端点（不能用 anthropic 端点）。
    from openai import OpenAI
    api_key, _, model = _get_llm_config()
    client = OpenAI(api_key=api_key, base_url=_get_openai_base_url())

    # 组装 user message
    user_content = f"## {etf_name}（{indicators.get('date', '')}）技术指标数据\n\n"
    user_content += "```json\n"
    user_content += json.dumps(indicators, ensure_ascii=False, indent=2)
    user_content += "\n```\n"
    user_content += "\n请根据以上数据进行分析，严格按照要求的 JSON 格式输出。"

    import threading
    stop_event = threading.Event()
    status = ["正在生成研报"]
    spinner_thread = None

    def _spin():
        idx = 0
        while not stop_event.is_set():
            print(f"\r{status[0]} {_SPINNER[idx]}", end="", flush=True)
            idx = (idx + 1) % len(_SPINNER)
            stop_event.wait(0.1)

    try:
        collected = []
        spinner_thread = threading.Thread(target=_spin, daemon=True)
        spinner_thread.start()

        with client.chat.completions.create(
            model=model,
            max_tokens=8000,  # reasoner 的思考 token 也占额度，给足避免 JSON 截断
            stream=True,
            response_format={"type": "json_object"},  # JSON Mode：硬保证输出为合法 JSON
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        ) as stream:
            for ch in stream:
                delta = ch.choices[0].delta
                # 只收最终答案 content；reasoner 的思考过程在 reasoning_content，丢弃
                if delta.content:
                    collected.append(delta.content)

        stop_event.set()
        spinner_thread.join()
        print("\r正在生成研报 完成!  ")
    except KeyboardInterrupt:
        stop_event.set()
        if spinner_thread:
            spinner_thread.join()
        print("\r研报生成已取消        ")
        return None
    except Exception as e:
        stop_event.set()
        if spinner_thread:
            spinner_thread.join()
        print(f"\rLLM 调用失败: {e}        ")
        return None

    # 解析 LLM 返回的 JSON
    text = _strip_code_fence("".join(collected))

    # JSON Mode 已强制合法 JSON，但官方文档提示偶发返回空 content，单独提示便于排查
    if not text:
        print("LLM 返回了空内容（DeepSeek JSON Mode 偶发），请重试一次")
        return None

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # LLM 偶尔在字符串值里写未转义的英文引号，破坏 JSON。用快模型修一次再解析。
        repaired = _repair_json_with_llm(text)
        if repaired:
            try:
                report = json.loads(_strip_code_fence(repaired))
                print("（研报 JSON 已自动修复）")
                return report
            except json.JSONDecodeError as e:
                print(f"LLM 返回的 JSON 解析失败（修复后仍失败）: {e}")
        else:
            print("LLM 返回的 JSON 解析失败，自动修复也未成功")
        print(f"原始返回:\n{text}")
        return None


def lite_logic_version() -> str:
    """
    lite 分析逻辑的指纹（模型 + prompt 的短 hash）。

    batch 的缓存键带上它，模型或 prompt 一改指纹就变，旧缓存自动失效——
    避免"换了模型/改了 prompt，结果还命中上次的缓存"。用 hashlib（结果稳定），
    不用内置 hash()（每进程随机化）。
    """
    _, _, model = _get_llm_config()
    fingerprint = f"{model}\n{SYSTEM_PROMPT_LITE}"
    return hashlib.md5(fingerprint.encode("utf-8")).hexdigest()[:8]


def analyze_etf_lite(indicators: dict, etf_name: str = "") -> dict:
    """
    批量快筛：lite prompt + deepseek-chat + JSON Mode，返回精简结论。

    始终返回一个 dict（不返回 None），便于 batch 层永远有一行可渲染：
      成功 → {"score","if_empty","if_holding","reason","_usage"}
      失败 → {"score":"?","if_empty":"?","if_holding":"?","reason":"","_error": "..."}

    关键：用和深度研报**同一个模型**（LLM_MODEL，通常是 deepseek-reasoner）和同一套
    分析纪律，只把输出从 11 项裁成 4 个字段——保证批量和单只的判断逻辑一致，
    不会出现"快筛偏乐观、点进深度研报又变保守"的割裂。模型的思考过程是内部的、
    不算返回内容，所以"批量只返回那几个字段"依然成立。
    不打印、不显示动画（进度由 batch 层统一管理）；system prompt 在多只之间命中
    DeepSeek prompt 缓存。
    """
    from openai import OpenAI
    api_key, _, model = _get_llm_config()

    def _fail(msg: str) -> dict:
        return {"score": "?", "if_empty": "?", "if_holding": "?", "reason": "", "_error": msg}

    try:
        client = OpenAI(api_key=api_key, base_url=_get_openai_base_url())
        user_content = f"## {etf_name}（{indicators.get('date', '')}）技术指标数据\n\n"
        user_content += "```json\n"
        user_content += json.dumps(indicators, ensure_ascii=False, indent=2)
        user_content += "\n```\n"
        user_content += "\n请快筛：只输出评分和操作建议的 JSON，不要展开分析。"

        resp = client.chat.completions.create(
            model=model,
            max_tokens=8000,  # reasoner 思考 token 也占额度，给足避免 JSON 被截断
            response_format={"type": "json_object"},  # JSON Mode 硬保证合法 JSON
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT_LITE},
                {"role": "user", "content": user_content},
            ],
        )
    except Exception as e:
        return _fail(str(e))

    text = _strip_code_fence(resp.choices[0].message.content or "")
    if not text:
        return _fail("LLM 返回空内容")

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        repaired = _repair_json_with_llm(text)
        if not repaired:
            return _fail("JSON 解析失败")
        try:
            data = json.loads(_strip_code_fence(repaired))
        except json.JSONDecodeError:
            return _fail("JSON 解析失败（修复后仍失败）")

    result = {
        "score": data.get("score", "?"),
        "if_empty": data.get("if_empty", "?"),
        "if_holding": data.get("if_holding", "?"),
        "reason": data.get("reason", ""),
    }

    # 抽取 token 用量供 batch 统计（DeepSeek 在 usage 里附 cache 命中/未命中字段，
    # 标准 OpenAI 模型没有，用 model_dump 容错读取）。
    try:
        usage = resp.usage.model_dump() if resp.usage else {}
    except Exception:
        usage = {}
    result["_usage"] = {
        "prompt_tokens": usage.get("prompt_tokens", 0) or 0,
        "completion_tokens": usage.get("completion_tokens", 0) or 0,
        "cache_hit_tokens": usage.get("prompt_cache_hit_tokens", 0) or 0,
        "cache_miss_tokens": usage.get("prompt_cache_miss_tokens", 0) or 0,
    }
    return result


# 操作建议的合法枚举（LLM 越界时标出来，而不是静默改写）
VALID_ACTIONS_EMPTY = {"建仓", "观望"}
VALID_ACTIONS_HOLDING = {"加仓", "持有", "减仓", "清仓"}


def _mark_action(value, valid: set) -> str:
    """合法枚举原样返回；缺失返回 '?'；越界值附警示标记"""
    if not value or value == "?":
        return "?"
    if value in valid:
        return value
    return f"{value}  ⚠ 非标准值（不在预设建议内）"


def format_report(report: dict, etf_name: str = "", code: str = "",
                   data_quality: dict = None) -> str:
    """把结构化研报 dict 格式化成可读的文本"""
    if not report:
        return "分析失败"

    lines = []
    lines.append(f"{'='*50}")
    lines.append(f"  {etf_name}（{code}）技术分析研报")

    # 数据时效提示
    if data_quality:
        as_of = data_quality.get("data_as_of", "")
        if data_quality.get("stale"):
            lines.append(f"  !! 数据截至 {as_of}，非最新 !!")
        else:
            lines.append(f"  数据截至 {as_of}")

    lines.append(f"{'='*50}")

    # 操作建议（最重要，放最前面）
    # 没有持仓数据，所以按"空仓/已持有"两个分支给建议
    action = report.get("action", {})
    reason = report.get("reason", "")
    if isinstance(action, dict):
        if_empty = _mark_action(action.get("if_empty", "?"), VALID_ACTIONS_EMPTY)
        if_holding = _mark_action(action.get("if_holding", "?"), VALID_ACTIONS_HOLDING)
        lines.append(f"\n>>> 若空仓:   {if_empty}")
        lines.append(f">>> 若已持有: {if_holding}")
    else:
        # 兼容旧格式（单一 action 字符串）
        lines.append(f"\n>>> 操作建议: {action}")
    lines.append(f"    理由: {reason}")

    # 综合评分
    score = report.get("score", {})
    lines.append(f"\n>>> 综合评分: {score.get('value', '?')}/10")
    lines.append(f"    {score.get('breakdown', '')}")

    # 当前状态
    current = report.get("current", {})
    lines.append(f"\n--- 当前状态 ---")
    lines.append(f"  价格: {current.get('price', '?')}  涨跌: {current.get('change_pct', '?')}")
    lines.append(f"  {current.get('summary', '')}")

    # 均线分析
    ma = report.get("ma_analysis", {})
    lines.append(f"\n--- 均线分析 ---")
    lines.append(f"  形态: {ma.get('pattern', '?')}")
    lines.append(f"  {ma.get('detail', '')}")

    # 动量指标
    momentum = report.get("momentum", {})
    lines.append(f"\n--- 动量指标 ---")
    lines.append(f"  MACD: {momentum.get('macd', '?')}")
    lines.append(f"  RSI:  {momentum.get('rsi', '?')}")
    lines.append(f"  KDJ:  {momentum.get('kdj', '?')}")

    # 量价分析
    vol = report.get("volume", {})
    lines.append(f"\n--- 量价分析 ---")
    lines.append(f"  量比: {vol.get('ratio', '?')}")
    lines.append(f"  {vol.get('analysis', '')}")

    # 布林带
    boll = report.get("bollinger", {})
    lines.append(f"\n--- 布林带 ---")
    lines.append(f"  位置: {boll.get('position', '?')}")
    lines.append(f"  {boll.get('analysis', '')}")

    # 支撑阻力
    sr = report.get("support_resistance", {})
    lines.append(f"\n--- 支撑/阻力 ---")
    lines.append(f"  支撑: {sr.get('support', '?')}  阻力: {sr.get('resistance', '?')}")
    lines.append(f"  {sr.get('analysis', '')}")

    # 风险提示
    risk = report.get("risk", {})
    lines.append(f"\n--- 风险提示 ---")
    lines.append(f"  最大回撤: {risk.get('max_drawdown', '?')}")
    lines.append(f"  波动率: {risk.get('volatility', '?')}")
    warnings = risk.get("warnings", [])
    for w in warnings:
        lines.append(f"  ⚠ {w}")

    lines.append(f"\n{'='*50}")
    lines.append("  仅基于单只标的的短线技术指标，未考虑估值/基本面、你的持仓成本与")
    lines.append("  期限、仓位占比与集中度（多只同类高beta=押同一个风险）；不构成投资建议。")

    return "\n".join(lines)


# ============================================================
# 单独测试
# ============================================================

if __name__ == "__main__":
    # 用一份真实指标数据测试
    test_indicators = {
        "price": 1.964,
        "change_pct": -1.9,
        "date": "2026-05-28",
        "ma": {"ma5": 2.012, "ma10": 2.0236, "ma20": 2.0716, "ma60": 1.9561, "ma120": 1.905, "ma250": 1.6929},
        "ma_pattern": "缠绕震荡",
        "ma20_slope": "走平",
        "macd": {"dif": -0.0006, "dea": 0.0176, "histogram": -0.0365, "signal": "DIF在DEA下方", "divergence": "无明显背离"},
        "rsi_14": 23.1,
        "rsi_status": "超卖",
        "volume_ratio": 0.48,
        "volume_status": "缩量",
        "vol_price_relation": "跌+缩量（自然回调）",
        "boll": {"upper": 2.2008, "mid": 2.0716, "lower": 1.9425, "bandwidth_pct": 12.47, "position": "接近下轨", "trend": "扩张"},
        "kdj": {"k": 19.1, "d": 25.2, "j": 7.0, "status": "正常"},
        "atr_14": 0.0584,
        "atr_pct": 2.97,
        "max_drawdown_60d": -10.81,
        "support": 1.95,
        "resistance": 2.207,
        "data_quality": {"total_days": 250, "missing_volume_days": 0},
    }

    print("正在分析...\n")
    report = analyze_etf(test_indicators, etf_name="新能源ETF")
    if report:
        print(format_report(report, etf_name="新能源ETF", code="515030"))
