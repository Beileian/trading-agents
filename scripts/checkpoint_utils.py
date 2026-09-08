#!/usr/bin/env python3
"""
checkpoint_utils.py — Cron Pipeline 检查点工具
v1.0 | P1-2 产物 | 长程智能体综述 C2 能力补全

设计原则：
- 先写临时文件再原子重命名（防写入中断）
- 带 schema version（防格式变更后读旧数据崩溃）
- 带过期时间戳（防使用过期缓存）

用法：
  from checkpoint_utils import Checkpoint
  ck = Checkpoint("trade_signals", date_str, ttl_minutes=240)
  data = ck.load()  # 尝试加载检查点
  if data is None:
      data = expensive_computation()  # 重新计算
      ck.save(data)  # 存检查点
"""

import os
import json
import time
from datetime import datetime

class Checkpoint:
    def __init__(self, pipeline: str, date_str: str, stage: str = "default", ttl_minutes: int = 240):
        """
        pipeline: "trade_signals" | "closing_review" | "procurement"
        date_str: "20260726"
        stage:    "data_fetch" | "llm_gen" | "pre_push" — 一个 pipeline 可有多个检查点
        ttl_minutes: 检查点有效期（分钟），过期自动失效
        """
        self.pipeline = pipeline
        self.date_str = date_str
        self.stage = stage
        self.ttl_minutes = ttl_minutes
        self.checkpoint_dir = "/root/.openclaw/workspace/projects/trading-agents/checkpoints"
        self.filename = f"{self.checkpoint_dir}/{pipeline}_{date_str}_{stage}.json"
        self.tmp_filename = self.filename + ".tmp"
        self.version = 1

    def save(self, data: dict) -> bool:
        """存检查点（原子写入）"""
        os.makedirs(self.checkpoint_dir, exist_ok=True)
        checkpoint = {
            "version": self.version,
            "pipeline": self.pipeline,
            "date": self.date_str,
            "stage": self.stage,
            "saved_at": datetime.now().isoformat(),
            "ttl_minutes": self.ttl_minutes,
            "data": data,
        }
        try:
            with open(self.tmp_filename, "w") as f:
                json.dump(checkpoint, f, indent=2, ensure_ascii=False)
            os.rename(self.tmp_filename, self.filename)  # 原子重命名
            return True
        except Exception as e:
            print(f"[checkpoint] save failed: {e}", flush=True)
            return False

    def load(self) -> dict | None:
        """加载检查点。过期→删除→返回 None。损坏→删除→返回 None。"""
        if not os.path.exists(self.filename):
            return None

        try:
            with open(self.filename) as f:
                ck = json.load(f)
        except (json.JSONDecodeError, IOError):
            # 损坏文件 → 清理
            os.remove(self.filename)
            return None

        # 版本检查
        if ck.get("version") != self.version:
            os.remove(self.filename)
            return None

        # 过期检查
        saved_at = ck.get("saved_at", "")
        if saved_at:
            try:
                saved_time = datetime.fromisoformat(saved_at)
                age_minutes = (datetime.now() - saved_time).total_seconds() / 60
                if age_minutes > self.ttl_minutes:
                    os.remove(self.filename)
                    return None
            except (ValueError, TypeError):
                pass

        # 日期校验（不允许跨日复用）
        if ck.get("date") != self.date_str or ck.get("stage") != self.stage:
            os.remove(self.filename)
            return None

        return ck.get("data")

    def clear(self):
        """手动清除检查点"""
        if os.path.exists(self.filename):
            os.remove(self.filename)
