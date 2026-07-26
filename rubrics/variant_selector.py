#!/usr/bin/env python3
"""
variant_selector.py — Rubrics Variant 随机选择器

方案二核心组件：每次运行 rubrics 评估时，从 LLM judge 项的 variant_pool
中随机抽取一组等效但表述不同的 prompt，防止 LLM 学会"通过 rubrics"而非"产出高质量内容"。

设计原则：
  - 仅对 judge=llm 的项生效
  - 每个 variant 评估标准一致，仅描述方式不同
  - 随机种子 = 当日日期 + item_id，确保同一天同 item 使用同一个 variant
  - 如果 JSON 中没有 variant_pool，回退到原始 prompt（向后兼容）

用法：
  from variant_selector import select_variant
  variant_prompt, variant_index = select_variant(item, seed_date="2026-07-26")
"""

import json
import random
import hashlib
from datetime import date


def _seed_from_date(item_id: str, seed_date: str = None) -> int:
    """从日期生成确定性种子"""
    if seed_date is None:
        seed_date = date.today().isoformat()
    seed_str = f"{seed_date}:{item_id}"
    return int(hashlib.md5(seed_str.encode()).hexdigest()[:8], 16)


def select_variant(item: dict, seed_date: str = None) -> tuple:
    """
    从 item 的 variant_pool 中随机选择一个 variant prompt。
    
    返回: (variant_prompt, variant_index)
    - 如果没有 variant_pool，返回 (original_prompt, -1)
    """
    if "variant_pool" not in item or not item["variant_pool"]:
        return item.get("prompt", ""), -1

    pool = item["variant_pool"]
    if len(pool) == 0:
        return item.get("prompt", ""), -1

    seed = _seed_from_date(item.get("id", "unknown"), seed_date)
    rng = random.Random(seed)
    idx = rng.randint(0, len(pool) - 1)

    return pool[idx], idx


def has_variant_pool(item: dict) -> bool:
    """检查 item 是否有 variant_pool"""
    return "variant_pool" in item and len(item.get("variant_pool", [])) > 0


def variant_info(item: dict) -> dict:
    """获取 variant 元信息"""
    pool = item.get("variant_pool", [])
    return {
        "has_variant": len(pool) > 0,
        "variant_count": len(pool),
        "item_id": item.get("id", "unknown"),
    }
