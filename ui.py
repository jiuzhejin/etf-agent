"""
终端 UI 封装
================
方向键菜单 / 多选框 / 确认框。底层用 simple-term-menu（纯 termios、零额外依赖、
延迟 import 不破坏秒开）。非 tty 环境（管道、测试）自动回退成编号文本输入，
保证任何场景都能用。
"""

import sys


def _is_tty() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


def _load_terminal_menu():
    """按需加载 simple-term-menu；未安装时返回 None 走文本回退。"""
    try:
        from simple_term_menu import TerminalMenu
        return TerminalMenu
    except ImportError:
        return None


def _safe_wcswidth(text: str) -> int:
    """未安装 simple-term-menu 时退化为长度判断，避免导入失败。"""
    try:
        from simple_term_menu import wcswidth
    except ImportError:
        return len(text)
    return wcswidth(text)


def _sanitize(text: str) -> str:
    """
    去掉会让 simple-term-menu 宽度算成 -1 的字符。

    老符号区 emoji（如 ⚡ U+26A1、⭐ U+2B50）在 simple-term-menu 的内置 wcswidth 里
    返回 -1（未知宽度），会让它重画菜单时的光标位移/右填充算错，导致整屏错位、
    帧一行行往下堆。这里把这类 width<0 的字符剔掉作为兜底。
    增补平面 emoji（📊🔍🌟🚪 等 U+1F300+）库返回 2、iTerm2 也按 2 渲染，对得上，
    可以正常用——所以这只兜底 -1 那类，不会误伤正常 emoji。
    """
    if _safe_wcswidth(text) >= 0 and all(_safe_wcswidth(ch) >= 0 for ch in text):
        return text
    cleaned = "".join(ch for ch in text if _safe_wcswidth(ch) >= 0)
    return cleaned.strip() or "?"


# ============================================================
# 单选
# ============================================================

def select(options: list[str], title: str = "", default: int = 0) -> int | None:
    """方向键单选。返回选中索引；ESC/q 取消返回 None。"""
    if not options:
        return None
    terminal_menu = _load_terminal_menu()
    if _is_tty() and terminal_menu:
        opts = [_sanitize(o) for o in options]  # 防 emoji 宽度炸菜单
        menu = terminal_menu(
            opts,
            title=_sanitize(title),
            cursor_index=max(0, min(default, len(options) - 1)),
            menu_cursor="❯ ",
            menu_cursor_style=("fg_cyan", "bold"),
            menu_highlight_style=("bold",),
            clear_screen=False,
        )
        return menu.show()
    return _text_select(options, title)


def _text_select(options: list[str], title: str) -> int | None:
    if title:
        print(title)
    for i, o in enumerate(options, 1):
        print(f"  {i}. {o}")
    while True:
        s = input("选择序号（回车取消）: ").strip()
        if not s:
            return None
        if s.isdigit() and 1 <= int(s) <= len(options):
            return int(s) - 1
        print("输入无效，请重试")


# ============================================================
# 多选
# ============================================================

def multiselect(options: list[str], title: str = "",
                preselect_all: bool = True) -> list[int] | None:
    """
    方向键多选（空格勾选 / 回车确认）。返回选中索引列表；
    ESC 取消返回 None；一个都没勾返回 []。
    """
    if not options:
        return []
    terminal_menu = _load_terminal_menu()
    if _is_tty() and terminal_menu:
        opts = [_sanitize(o) for o in options]  # 防 emoji 宽度炸菜单
        menu = terminal_menu(
            opts,
            title=_sanitize(title),
            multi_select=True,
            show_multi_select_hint=True,
            multi_select_select_on_accept=False,
            preselected_entries=list(opts) if preselect_all else None,
            menu_cursor="❯ ",
            menu_cursor_style=("fg_cyan", "bold"),
            clear_screen=False,
        )
        sel = menu.show()
        if sel is None:
            return None
        return list(sel)
    return _text_multiselect(options, title, preselect_all)


def _text_multiselect(options: list[str], title: str,
                      preselect_all: bool) -> list[int] | None:
    if title:
        print(title)
    for i, o in enumerate(options, 1):
        print(f"  {i}. {o}")
    hint = "多选序号（逗号分隔，'all' 全选，回车=" + ("全选" if preselect_all else "取消") + "）: "
    s = input(hint).strip()
    if not s:
        return list(range(len(options))) if preselect_all else None
    if s.lower() == "all":
        return list(range(len(options)))
    out = []
    for part in s.replace("，", ",").split(","):
        part = part.strip()
        if part.isdigit() and 1 <= int(part) <= len(options):
            out.append(int(part) - 1)
    return out


# ============================================================
# 确认 / 文本输入
# ============================================================

def confirm(prompt: str, default: bool = False) -> bool:
    """是/否确认。回车取默认值。"""
    suffix = " [Y/n] " if default else " [y/N] "
    try:
        ans = input(prompt + suffix).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return default
    if not ans:
        return default
    return ans in ("y", "yes", "是")


def ask(prompt: str) -> str:
    """单行文本输入，去空白；取消返回空串。"""
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return ""
