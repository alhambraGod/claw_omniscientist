#!/usr/bin/env python3
"""
fix-openclaw-config.py — openclaw.json 配置自动校验与修复

解决问题：
  openclaw doctor --fix 会把 feishu 的 dmPolicy / allowFrom / groupPolicy
  从顶层迁移到 accounts.default，但 OpenClaw 只读 accounts.main，
  导致所有飞书用户都触发配对流程。

本脚本确保这三个策略字段始终在 accounts.main 中，
让任何用户添加机器人后直接可对话，无需人工审批。

用法：
  python3 claw/scripts/fix-openclaw-config.py [openclaw.json路径]
"""

import json
import sys
import shutil
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "config" / "openclaw.json"
if len(sys.argv) > 1:
    CONFIG_PATH = Path(sys.argv[1])

REQUIRED_POLICIES = {
    "dmPolicy": "open",       # 任何人可私聊，无需配对
    "allowFrom": ["*"],       # 不限制用户白名单
    "groupPolicy": "open",    # 群组中 @ 机器人即可响应
}

def fix_config(path: Path) -> bool:
    """校验并修复 openclaw.json，返回 True 表示有变更"""
    if not path.exists():
        print(f"[fix-config] ✗ 找不到配置文件: {path}", file=sys.stderr)
        sys.exit(1)

    with open(path, encoding="utf-8") as f:
        config = json.load(f)

    feishu = config.get("channels", {}).get("feishu", {})
    if not feishu:
        print("[fix-config] ✓ 无 Feishu 配置，无需修复")
        return False

    accounts = feishu.setdefault("accounts", {})
    main_acc = accounts.setdefault("main", {})

    changed = False

    # 1. 若 doctor --fix 又把策略放到 accounts.default，合并回 main
    default_acc = accounts.pop("default", {})
    if default_acc:
        print(f"[fix-config] ⚠ 检测到 accounts.default，合并到 accounts.main: {list(default_acc.keys())}")
        main_acc.update(default_acc)
        changed = True

    # 2. 确保 accounts.main 里有正确的策略值
    for key, val in REQUIRED_POLICIES.items():
        if main_acc.get(key) != val:
            print(f"[fix-config] ⚠ 修正 accounts.main.{key}: {main_acc.get(key)!r} → {val!r}")
            main_acc[key] = val
            changed = True

    # 3. 清理顶层的策略字段（防止和 accounts.main 的值冲突）
    for key in REQUIRED_POLICIES:
        if key in feishu:
            print(f"[fix-config] ⚠ 移除顶层 channels.feishu.{key}（已整合到 accounts.main）")
            del feishu[key]
            changed = True

    if not changed:
        print("[fix-config] ✓ 配置正确，无需修改")
        return False

    # 备份并写入
    backup = path.with_suffix(".json.bak")
    shutil.copy2(path, backup)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    print(f"[fix-config] ✓ 配置已修复（备份: {backup.name}）")
    return True


if __name__ == "__main__":
    fix_config(CONFIG_PATH)
