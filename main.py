"""
ETF 投资研报 Agent
主循环：输入解析 → 数据获取 → 指标计算 → LLM 分析 → 输出
"""

from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

import gnureadline  # 修复 macOS 终端中文删除显示问题
from etf_resolver import resolve_etf_code
from etf_data import fetch_etf_history, calculate_indicators
from etf_analyzer import analyze_etf, ask_followup, format_report


# 会话状态
current_code = None
current_name = None
current_indicators = None


def run_analysis(code: str, name: str) -> dict | None:
    """完整分析流程：拉数据 → 算指标 → LLM 分析"""
    global current_code, current_name, current_indicators

    # 拉数据
    df = fetch_etf_history(code)
    if df is None:
        print("数据获取失败，请稍后重试")
        return None

    # 算指标
    indicators = calculate_indicators(df)
    if indicators is None:
        return None

    # 更新会话状态
    current_code = code
    current_name = name
    current_indicators = indicators

    # LLM 分析
    report = analyze_etf(indicators, etf_name=name)
    return report


def handle_input(user_input: str):
    """处理用户输入"""
    global current_code, current_name, current_indicators

    user_input = user_input.strip()
    if not user_input:
        return

    try:
        # 始终走完整降级链路（含 LLM 提取关键词），匹配不到才当追问
        has_context = current_indicators is not None
        result = resolve_etf_code(user_input)

        if result:
            # 解析到了新标的
            code, name = result["code"], result["name"]

            if code == current_code:
                # 匹配到当前标的，当作追问
                print(f"正在基于 {current_name}（{current_code}）回答...\n")
                ask_followup(current_indicators, etf_name=current_name, user_question=user_input)
                return

            report = run_analysis(code, name)
            if report:
                dq = current_indicators.get("data_quality") if current_indicators else None
                print(format_report(report, etf_name=name, code=code, data_quality=dq))

        elif has_context:
            # 有当前标的但没匹配到新的 → 当追问（streaming 输出）
            print(f"正在基于 {current_name}（{current_code}）回答...\n")
            ask_followup(
                current_indicators,
                etf_name=current_name,
                user_question=user_input,
            )

        else:
            print("请先输入一个 ETF 代码或名称")

    except KeyboardInterrupt:
        print("\n已取消")
    except Exception as e:
        print(f"\n出错了: {e}\n请重试或输入其他 ETF 代码")


def main():
    print("=" * 50)
    print("  ETF 投资研报 Agent")
    print("  输入 ETF 代码或名称开始分析")
    print("  输入 refresh 刷新当前数据")
    print("  输入 exit 退出")
    print("=" * 50)

    while True:
        try:
            user_input = input("\n> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n再见")
            break

        if not user_input:
            continue

        if user_input.lower() == "exit":
            print("再见")
            break

        if user_input.lower() == "refresh":
            if current_code:
                print(f"刷新 {current_name}（{current_code}）...")
                report = run_analysis(current_code, current_name)
                if report:
                    print(format_report(report, etf_name=current_name, code=current_code))
            else:
                print("当前没有分析的标的，请先输入 ETF 代码或名称")
            continue

        handle_input(user_input)


if __name__ == "__main__":
    main()
