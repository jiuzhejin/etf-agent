"""
自选池数据层
================
批量快筛的输入集。维护一个分组的 ETF 池，持久化到 cache/watchlist.json。

设计：
- 存在 cache/ 下（已被 .gitignore 和 sync_to_agent.sh 排除）→ 不进 git、不被同步
  覆盖、每个部署各管各的池子。
- 池子是人工策展的状态、不可重建，所以每次写入前备份 .bak，并用临时文件原子替换，
  避免写到一半崩了把池子写坏。
- 分组（groups）是有序列表，item 带 group 字段。默认两组：持仓 / 观察。

数据结构：
  {
    "version": 1,
    "groups": ["持仓", "观察"],
    "items": [
      {"code": "512980", "name": "传媒ETF", "group": "观察",
       "added": "2026-06-24", "note": ""}
    ]
  }
"""

import json
import os
from datetime import date
from pathlib import Path

_DIR = Path(__file__).parent / "cache"
_FILE = _DIR / "watchlist.json"
_BAK = _DIR / "watchlist.json.bak"

DEFAULT_GROUPS = ["持仓", "观察"]
DEFAULT_GROUP = "观察"


# ============================================================
# 读写
# ============================================================

def _empty() -> dict:
    return {"version": 1, "groups": list(DEFAULT_GROUPS), "items": []}


def _normalize(data) -> dict:
    """容缺规整：保证结构合法、item.group 都在 groups 里、按 code 去重。"""
    if not isinstance(data, dict):
        return _empty()
    groups = list(data.get("groups") or DEFAULT_GROUPS)
    items = data.get("items") or []
    clean = []
    seen = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        code = str(it.get("code", "")).strip()
        if not code or code in seen:
            continue
        seen.add(code)
        group = it.get("group") or DEFAULT_GROUP
        if group not in groups:
            groups.append(group)
        clean.append({
            "code": code,
            "name": it.get("name", ""),
            "group": group,
            "added": it.get("added", ""),
            "note": it.get("note", ""),
        })
    if not groups:
        groups = list(DEFAULT_GROUPS)
    return {"version": 1, "groups": groups, "items": clean}


def load() -> dict:
    """读自选池；文件缺失/损坏时回退 .bak，再不行返回空池。"""
    if _FILE.exists():
        try:
            with open(_FILE, "r", encoding="utf-8") as f:
                return _normalize(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass  # 损坏，尝试 .bak
    if _BAK.exists():
        try:
            with open(_BAK, "r", encoding="utf-8") as f:
                return _normalize(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass
    return _empty()


def save(data: dict) -> None:
    """写自选池：先把现有文件备份成 .bak，再用临时文件原子替换。"""
    _DIR.mkdir(exist_ok=True)
    data = _normalize(data)
    if _FILE.exists():
        try:
            _BAK.write_text(_FILE.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            pass  # 备份失败不阻塞写入
    tmp = _FILE.with_name(_FILE.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _FILE)  # 原子替换，避免写一半损坏


# ============================================================
# 查询
# ============================================================

def has(data: dict, code: str) -> bool:
    code = str(code).strip()
    return any(it["code"] == code for it in data["items"])


def items_in_group(data: dict, group: str) -> list[dict]:
    return [it for it in data["items"] if it["group"] == group]


def all_items(data: dict) -> list[dict]:
    return list(data["items"])


def groups(data: dict) -> list[str]:
    return list(data["groups"])


# ============================================================
# 标的增删改
# ============================================================

def add_item(data: dict, code: str, name: str = "",
             group: str = DEFAULT_GROUP, note: str = "") -> tuple[bool, str]:
    """加入一只 ETF。按 code 去重；组不存在则自动创建。"""
    code = str(code).strip()
    if not code:
        return False, "代码为空"
    if has(data, code):
        cur = next(it for it in data["items"] if it["code"] == code)
        return False, f"{code} 已在自选池「{cur['group']}」"
    if group not in data["groups"]:
        data["groups"].append(group)
    data["items"].append({
        "code": code,
        "name": name,
        "group": group,
        "added": date.today().isoformat(),
        "note": note,
    })
    return True, f"已加入「{group}」: {code} {name}".rstrip()


def remove_codes(data: dict, codes) -> int:
    """按代码批量删除，返回删除条数。"""
    codes = {str(c).strip() for c in codes}
    before = len(data["items"])
    data["items"] = [it for it in data["items"] if it["code"] not in codes]
    return before - len(data["items"])


def move_codes(data: dict, codes, group: str) -> int:
    """把若干标的移动到另一个组（组不存在则创建），返回移动条数。"""
    codes = {str(c).strip() for c in codes}
    if group not in data["groups"]:
        data["groups"].append(group)
    moved = 0
    for it in data["items"]:
        if it["code"] in codes and it["group"] != group:
            it["group"] = group
            moved += 1
    return moved


def set_note(data: dict, code: str, note: str) -> bool:
    code = str(code).strip()
    for it in data["items"]:
        if it["code"] == code:
            it["note"] = note
            return True
    return False


def update_names(data: dict, name_lookup) -> int:
    """用 name_lookup(code)->name 刷新所有标的名称，返回更新条数。"""
    changed = 0
    for it in data["items"]:
        try:
            new = name_lookup(it["code"])
        except Exception:
            new = None
        if new and new != it.get("name"):
            it["name"] = new
            changed += 1
    return changed


# ============================================================
# 组增删改
# ============================================================

def add_group(data: dict, name: str) -> tuple[bool, str]:
    name = name.strip()
    if not name:
        return False, "组名为空"
    if name in data["groups"]:
        return False, f"组「{name}」已存在"
    data["groups"].append(name)
    return True, f"已新建组「{name}」"


def rename_group(data: dict, old: str, new: str) -> tuple[bool, str]:
    new = new.strip()
    if old not in data["groups"]:
        return False, f"组「{old}」不存在"
    if not new:
        return False, "新组名为空"
    if new in data["groups"] and new != old:
        return False, f"组「{new}」已存在"
    data["groups"] = [new if g == old else g for g in data["groups"]]
    for it in data["items"]:
        if it["group"] == old:
            it["group"] = new
    return True, f"组「{old}」→「{new}」"


def delete_group(data: dict, name: str, reassign_to: str | None = None) -> tuple[bool, str]:
    """删除一个组。组内有标的时必须给 reassign_to（迁到的目标组）。"""
    if name not in data["groups"]:
        return False, f"组「{name}」不存在"
    members = items_in_group(data, name)
    if members:
        if not reassign_to:
            return False, f"组「{name}」下还有 {len(members)} 只标的，请先指定迁移目标组"
        if reassign_to == name:
            return False, "迁移目标不能是被删除的组本身"
        move_codes(data, [it["code"] for it in members], reassign_to)
    data["groups"] = [g for g in data["groups"] if g != name]
    if not data["groups"]:
        data["groups"] = list(DEFAULT_GROUPS)
    return True, f"已删除组「{name}」" + (f"（{len(members)} 只迁到「{reassign_to}」）" if members else "")


# ============================================================
# 冷启动：从历史缓存导入
# ============================================================

def import_from_cache(data: dict, name_lookup=None, group: str = DEFAULT_GROUP) -> int:
    """
    扫描 cache/ 下的 {code}_hist.json（即用户之前看过的标的），把不在池里的导入。
    name_lookup(code)->name 用来补名称（可为 None）。返回新增条数。
    """
    if group not in data["groups"]:
        data["groups"].append(group)
    existing = {it["code"] for it in data["items"]}
    added = 0
    for p in sorted(_DIR.glob("*_hist.json")):
        code = p.name[: -len("_hist.json")]
        if not (len(code) == 6 and code.isdigit()):
            continue
        if code in existing:
            continue
        name = ""
        if name_lookup:
            try:
                name = name_lookup(code) or ""
            except Exception:
                name = ""
        data["items"].append({
            "code": code,
            "name": name,
            "group": group,
            "added": date.today().isoformat(),
            "note": "",
        })
        existing.add(code)
        added += 1
    return added
