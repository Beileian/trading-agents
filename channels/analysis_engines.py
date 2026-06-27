"""
analysis_engines.py — 技术分析引擎通道文件（方向3：分析技术栈多后端路由）

每个引擎 = 一种分析范式，按场景路由。
"""

ANALYSIS_BACKENDS = {
    "timesfm": {
        "priority": 1,
        "desc": "TimesFM (Google Research)",
        "type": "deep_learning",
        "probe": "python3 -c 'import timesfm; print(\"ok\")' 2>/dev/null",
        "output": "多步预测 + 置信区间",
        "best_for": ["短期趋势预测", "5/10/20日价格区间"],
        "failure_mode": "模型加载失败 / GPU 不可用 / huggingface 镜像不可达",
        "degrade_seconds": 7200,
        "note": "HF_ENDPOINT=https://hf-mirror.com",
        "requires": "transformers, numpy, pandas"
    },
    "bollinger": {
        "priority": 2,
        "desc": "布林带 (TA-Lib)",
        "type": "statistical",
        "probe": "python3 -c 'import talib; print(\"ok\")' 2>/dev/null",
        "output": "上/中/下轨 + 带宽",
        "best_for": ["超买超卖判断", "波动率回归"],
        "failure_mode": "TA-Lib 未安装",
        "note": "替代方案: numpy 手写布林带"
    },
    "macd": {
        "priority": 3,
        "desc": "MACD 趋势指标",
        "type": "statistical",
        "probe": "python3 -c 'import talib; print(\"ok\")' 2>/dev/null",
        "output": "DIF/DEA/柱状图",
        "best_for": ["趋势方向", "金叉死叉信号"],
        "failure_mode": "同上"
    },
    "rsi": {
        "priority": 4,
        "desc": "RSI 相对强弱",
        "type": "statistical",
        "probe": "python3 -c 'import talib; print(\"ok\")' 2>/dev/null",
        "output": "0-100 超买超卖",
        "best_for": ["短期动量", "超买(>70)/超卖(<30)"],
    },
    "sma_cross": {
        "priority": 5,
        "desc": "均线交叉 (MA5/MA10/MA20/MA60)",
        "type": "statistical",
        "probe": "python3 -c 'import numpy; print(\"ok\")' 2>/dev/null",
        "output": "多周期均线 + 金叉/死叉点",
        "best_for": ["中长线方向", "入场时机"],
        "note": "不需要 TA-Lib，numpy 手写"
    },
    "narrative": {
        "priority": 100,  # 始终最后，作为 fallback
        "desc": "LLM 叙述性分析 (V4-Pro)",
        "type": "language_model",
        "probe": "echo ok",  # 总是可用
        "output": "结构化分析报告",
        "best_for": ["综合研判", "多因子融合", "不确定性表达"],
        "note": "不是技术指标引擎，是自然语言综合。必须搭配至少一个数值引擎"
    }
}

# ── 场景路由表 ──
ANALYSIS_ROUTING = {
    "trend_prediction": {
        "desc": "趋势预测",
        "chain": ["timesfm", "sma_cross", "macd", "narrative"],
        "note": "TimesFM 为主，统计指标为辅"
    },
    "overbought_oversold": {
        "desc": "超买/超卖",
        "chain": ["bollinger", "rsi", "narrative"],
    },
    "full_report": {
        "desc": "完整技术分析报告",
        "chain": ["timesfm", "bollinger", "macd", "rsi", "sma_cross", "narrative"],
        "note": "依次运行可用引擎，综合输出"
    },
    "quick_scan": {
        "desc": "快速扫描（仅统计指标）",
        "chain": ["sma_cross", "rsi", "bollinger"],
        "note": "不需要 GPU / TimesFM"
    }
}
