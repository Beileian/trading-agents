#!/usr/bin/env python3
"""
金桥虚拟盘 v1.1 — 基于每日交易推荐的模拟交易引擎
项目: 金桥量化 v2.5.0

v1.1: 收盘价从 closing_review 快照读取（避免新浪阻断导致净值偏移）
v1: 基础版
==============================================
- 初始资金: 10万人民币
- 仅做多，不支持融券卖空
- 佣金: 万2.5 双向（买+卖）
- 印花税: 千1（仅卖出）
- 单票仓位上限: 30%
- 总仓位上限: 80%
- 成交价: 信号触发时实时价（盘中）或收盘价（收盘复盘时）

用法:
  python3 paper_trading.py status          # 查看当前持仓+净值
  python3 paper_trading.py execute <date>  # 基于当日交易推荐执行买卖
  python3 paper_trading.py close <date>    # 收盘更新持仓市值
  python3 paper_trading.py report <date>   # 生成完整日终报告
  python3 paper_trading.py history         # 查看全部交易记录
"""

import os, json, sys, re
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ── 配置 ──────────────────────────────────────────────
TZ = timezone(timedelta(hours=8))
PROJECT_DIR = "/root/.openclaw/workspace/projects/trading-agents"
STATE_FILE = f"{PROJECT_DIR}/reports/paper_state.json"
TRADE_LOG = f"{PROJECT_DIR}/reports/paper_trades.jsonl"

# 初始参数
INITIAL_CASH = 100_000.0
COMMISSION_RATE = 0.00025   # 万2.5
STAMP_DUTY_RATE = 0.001     # 千1（仅卖出）
MAX_POSITION_RATIO = 0.30   # 单票 30%
MAX_TOTAL_RATIO = 0.80      # 总仓位 80%

# 价格获取
def _fetch_close_price(code: str, date_str: str) -> Optional[float]:
    """从 closing_review 收盘快照获取收盘价（唯一数据源）"""
    date_tag = date_str.replace("-", "")
    snapshot_file = f"{PROJECT_DIR}/reports/close_snapshot_{date_tag}.json"
    if not os.path.exists(snapshot_file):
        return None
    with open(snapshot_file) as f:
        snapshot = json.load(f)
    name = _CODE_NAME.get(code, _CODE_NAME.get("sh" + code, _CODE_NAME.get("sz" + code)))
    if name and name in snapshot:
        return snapshot[name]
    # 备选: 模糊匹配
    clean = code.replace("sh", "").replace("sz", "")
    for sname, sprice in snapshot.items():
        if clean in sname or sname in clean:
            return sprice
    return None

# 名称映射
_CODE_NAME = {
    "sh000016": "上证50", "sh000300": "沪深300", "sh588000": "科创50",
    "sh601288": "农业银行", "sh601988": "中国银行", "sh600036": "招商银行",
    "sh600795": "国电电力", "sz000066": "中国长城", "sh600562": "国睿科技",
    "sh562500": "中证机器人",
}


# ── 数据模型 ──────────────────────────────────────────
@dataclass
class Position:
    code: str
    name: str
    shares: int          # 股数
    avg_cost: float      # 均价
    current_price: float = 0.0
    market_value: float = 0.0
    unrealized_pnl: float = 0.0
    sina_code: str = ""  # 新浪行情代码

    def update(self, price: float):
        self.current_price = price
        self.market_value = round(self.shares * price, 2)
        self.unrealized_pnl = round(self.market_value - self.shares * self.avg_cost, 2)


@dataclass
class Trade:
    date: str
    code: str
    name: str
    action: str          # buy / sell
    shares: int
    price: float
    amount: float        # 成交金额
    commission: float    # 佣金
    stamp_duty: float    # 印花税
    reason: str          # 触发原因


@dataclass
class PaperState:
    cash: float
    positions: Dict[str, Position] = field(default_factory=dict)
    trades: List[Trade] = field(default_factory=list)
    net_values: List[Tuple[str, float]] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    @property
    def total_assets(self) -> float:
        pos_val = sum(p.market_value for p in self.positions.values())
        return round(self.cash + pos_val, 2)

    @property
    def net_value(self) -> float:
        # 净值 = 总资产 / 持仓成本总额 (initial_equity)
        base = getattr(self, 'initial_equity', None)
        if base is None:
            # 回退：用持仓成本+初始现金
            pos_cost = sum(p.shares * p.avg_cost for p in self.positions.values())
            base = pos_cost + INITIAL_CASH
        if base == 0:
            base = INITIAL_CASH
        return round(self.total_assets / base, 4)

    def position_ratio(self, code: str) -> float:
        if code in self.positions:
            return round(self.positions[code].market_value / self.total_assets, 4)
        return 0.0

    def total_position_ratio(self) -> float:
        pos_val = sum(p.market_value for p in self.positions.values())
        return round(pos_val / self.total_assets, 4)


# ── 状态持久化 ──────────────────────────────────────
def load_state() -> PaperState:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            data = json.load(f)
        state = PaperState(
            cash=data["cash"],
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )
        state.initial_equity = data.get("initial_equity", INITIAL_CASH)
        for pd in data.get("positions", []):
            pos = Position(**pd)
            state.positions[pos.code] = pos
        for td in data.get("trades", []):
            state.trades.append(Trade(**td))
        for nd in data.get("net_values", []):
            state.net_values.append(tuple(nd))
        return state
    return PaperState(
        cash=INITIAL_CASH,
        created_at=datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
    )


def save_state(state: PaperState):
    state.updated_at = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    data = {
        "cash": state.cash,
        "created_at": state.created_at,
        "updated_at": state.updated_at,
        "net_value": state.net_value,
        "total_assets": state.total_assets,
        "positions": [
            {
                "code": p.code, "name": p.name, "shares": p.shares,
                "avg_cost": p.avg_cost, "current_price": p.current_price,
                "market_value": p.market_value, "unrealized_pnl": p.unrealized_pnl,
            }
            for p in state.positions.values()
        ],
        "net_values": [list(nv) for nv in state.net_values],
    }
    with open(STATE_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    # 追加交易日志
    if state.trades:
        with open(TRADE_LOG, "a") as f:
            for t in state.trades[-len(state.trades):]:
                f.write(json.dumps({
                    "date": t.date, "code": t.code, "name": t.name,
                    "action": t.action, "shares": t.shares, "price": t.price,
                    "amount": t.amount, "commission": t.commission,
                    "stamp_duty": t.stamp_duty, "reason": t.reason,
                }, ensure_ascii=False) + "\n")


# ── 交易信号解析 ──────────────────────────────────────
def parse_signal_line(line: str) -> Optional[dict]:
    """
    解析交易推荐中的个股行，提取操作信号。
    格式: 🟡 农业银行  6.80  乖离+4.9% ↑  持有
           触发: 乖离7.20扩大+接近阻力，减仓规避
           支撑6.67 / 阻力7.20
    """
    # 匹配名称+价格行
    m = re.match(r'[🔴🟡🟢]\s+([^\s]+)\s+([\d.]+)\s+乖离.+?\s+(\S+)', line)
    if not m:
        return None
    name = m.group(1).strip()
    try:
        price = float(m.group(2))
    except ValueError:
        return None
    action_word = m.group(3).strip()

    # 从名称映射到code
    code = None
    for k, v in _CODE_NAME.items():
        if v == name:
            code = k
            break
    if not code:
        return None

    # 判断操作方向
    if action_word == "卖出":
        signal = "sell"
    elif action_word == "持有":
        signal = "hold"
    else:
        signal = "hold"

    return {"code": code, "name": name, "price": price, "signal": signal}


def parse_trigger_line(line: str) -> Optional[str]:
    """解析触发条件"""
    m = re.match(r'^\s*触发:\s*(.+)', line)
    return m.group(1).strip() if m else None


def get_code_for_name(name: str) -> Optional[str]:
    for k, v in _CODE_NAME.items():
        if v == name:
            return k
    return None


def get_shares_for_amount(amount: float, price: float) -> int:
    """按A股规则：100的整数倍"""
    raw = amount / price
    return max(100, int(raw // 100) * 100)


def execute_order(state: PaperState, code: str, name: str, action: str, price: float, reason: str, date: str) -> Optional[Trade]:
    """
    执行单笔交易，返回 Trade 或 None
    """
    total = state.total_assets

    if action == "buy":
        # 检查持仓上限
        max_buy_amount = total * MAX_POSITION_RATIO
        existing_val = state.positions[code].market_value if code in state.positions else 0
        available = max_buy_amount - existing_val
        if available <= 0:
            print(f"  ⛔ {name}: 已达单票仓位上限 30%")
            return None
        # 总仓位上限
        max_total_buy = total * MAX_TOTAL_RATIO
        current_pos_val = sum(p.market_value for p in state.positions.values())
        cash_limit = min(available, max_total_buy - current_pos_val, state.cash)
        if cash_limit < price * 100:
            print(f"  ⛔ {name}: 资金不足（可用 {cash_limit:.0f}）")
            return None
        shares = get_shares_for_amount(cash_limit, price)
        amount = round(shares * price, 2)
        commission = round(amount * COMMISSION_RATE, 2)
        total_cost = amount + commission
        if total_cost > state.cash:
            shares = get_shares_for_amount(state.cash - commission, price)
            amount = round(shares * price, 2)
            commission = round(amount * COMMISSION_RATE, 2)
            total_cost = amount + commission
        state.cash = round(state.cash - total_cost, 2)
        trade = Trade(date, code, name, "buy", shares, price, amount, commission, 0, reason)
        state.trades.append(trade)
        # 更新持仓
        if code in state.positions:
            p = state.positions[code]
            new_cost = round((p.shares * p.avg_cost + amount) / (p.shares + shares), 4)
            p.shares += shares
            p.avg_cost = new_cost
        else:
            state.positions[code] = Position(code, name, shares, price, price, amount, 0)
        print(f"  ✅ 买入 {name} {shares}股 @{price:.2f} 金额{amount:.2f} 费用{commission:.2f}")
        return trade

    elif action == "sell":
        if code not in state.positions:
            print(f"  ⚠️ {name}: 无持仓，跳过卖出")
            return None
        p = state.positions[code]
        amount = round(p.shares * price, 2)
        commission = round(amount * COMMISSION_RATE, 2)
        stamp_duty = round(amount * STAMP_DUTY_RATE, 2)
        net_received = round(amount - commission - stamp_duty, 2)
        state.cash = round(state.cash + net_received, 2)
        trade = Trade(date, code, name, "sell", p.shares, price, amount, commission, stamp_duty, reason)
        state.trades.append(trade)
        print(f"  ✅ 卖出 {name} {p.shares}股 @{price:.2f} 金额{amount:.2f} 费用{commission+stamp_duty:.2f}")
        del state.positions[code]
        return trade

    return None


# ── 命令入口 ──────────────────────────────────────────
def cmd_status():
    state = load_state()
    if not state.positions and not state.trades:
        print("# 金桥虚拟盘")
        print(f"初始资金: ¥{INITIAL_CASH:,.0f}")
        print("持仓: 空仓")
        print(f"创建时间: {state.created_at}")
        return

    print(f"# 金桥虚拟盘 · {datetime.now(TZ).strftime('%Y-%m-%d %H:%M')}")
    print()
    print(f"净值: {state.net_value:.4f} | 总资产: ¥{state.total_assets:,.2f}")
    print(f"现金: ¥{state.cash:,.2f} | 仓位: {state.total_position_ratio()*100:.1f}%")
    print()
    print("## 当前持仓")
    print("| 标的 | 股数 | 均价 | 现价 | 市值 | 浮盈 |")
    print("|------|------|------|------|------|------|")
    for p in state.positions.values():
        if p.shares > 0:
            print(f"| {p.name} | {p.shares} | {p.avg_cost:.2f} | {p.current_price:.2f} | {p.market_value:.0f} | {p.unrealized_pnl:+.0f} |")
    print()
    if state.net_values:
        print("## 净值曲线")
        for d, nv in state.net_values:
            arrow = "↑" if nv >= 1.0 else "↓"
            print(f"  {d}: {nv:.4f} {arrow}")
    if state.trades:
        print(f"\n总交易: {len(state.trades)} 笔")


def cmd_execute(date_str: str):
    """基于当日交易推荐信号执行买卖"""
    signal_file = f"{PROJECT_DIR}/reports/trade_signals_{date_str.replace('-', '')}.md"
    if not os.path.exists(signal_file):
        print(f"❌ 信号文件不存在: {signal_file}")
        # 尝试从带日期的文件名找
        alt = f"{PROJECT_DIR}/reports/trade_signals_{date_str.replace('-', '')}.md"
        # 只用YYYYMMDD格式
        print("   请确认交易推荐已生成 (run_premarket_push.sh)")
        sys.exit(1)

    state = load_state()

    # 更新持仓市价
    for p in state.positions.values():
        price = _fetch_close_price(p.code, date_str)
        if price:
            p.update(price)
        else:
            # 保底：用前一天的价格
            print(f"  ⚠️ {p.name}: 无实时价格，保留原估值")

    with open(signal_file) as f:
        lines = f.readlines()

    # 解析信号
    signals = []
    triggers = {}
    for i, line in enumerate(lines):
        sig = parse_signal_line(line)
        if sig:
            signals.append(sig)
        # 找触发条件
        trig = parse_trigger_line(line)
        if trig and signals:
            triggers[signals[-1]["code"]] = trig

    print(f"# 虚拟盘执行 · {date_str}")
    print(f"执行前: 净值 {state.net_value:.4f} | 现金 ¥{state.cash:,.2f}")
    print()

    # 指数ETF列表（首次建仓不选ETF）
    INDEX_ETFS = {"sh000016", "sh000300", "sh000688"}

    # 执行交易
    buys, sells = 0, 0
    for sig in signals:
        code = sig["code"]
        name = sig["name"]
        price = sig["price"]
        signal = sig["signal"]
        reason = triggers.get(code, "交易推荐信号")

        # 卖出：信号为"卖出"且有持仓
        if signal == "sell":
            if code in state.positions:
                trade = execute_order(state, code, name, "sell", price, reason, date_str)
                if trade:
                    sells += 1
            continue

        # 买入逻辑：
        # - 已有持仓 + 触发含"加仓" → 加仓
        # - 首次建仓（空仓/无此标的）→ 只买个股（非ETF），触发含"加仓" or "突破" or "企稳"
        if code in INDEX_ETFS:
            # ETF只在已有持仓时加仓，不首次建仓
            if code in state.positions and reason and ("加仓" in reason or "突破" in reason):
                trade = execute_order(state, code, name, "buy", price, reason, date_str)
                if trade:
                    buys += 1
        else:
            # 个股
            is_buy_trigger = reason and ("加仓" in reason or "突破" in reason or "企稳" in reason or "买入" in reason)
            if code in state.positions and is_buy_trigger:
                trade = execute_order(state, code, name, "buy", price, reason, date_str)
                if trade:
                    buys += 1
            elif code not in state.positions and is_buy_trigger:
                # 首次建仓个股
                trade = execute_order(state, code, name, "buy", price, reason, date_str)
                if trade:
                    buys += 1

    print()
    print(f"买入 {buys} 笔 | 卖出 {sells} 笔")
    print(f"执行后: 净值 {state.net_value:.4f} | 现金 ¥{state.cash:,.2f} | 仓位 {state.total_position_ratio()*100:.1f}%")

    # 记录净值
    state.net_values.append((date_str, state.net_value))
    save_state(state)


def cmd_close(date_str: str):
    """收盘更新：刷新所有持仓市价"""
    state = load_state()
    if not state.positions:
        print("空仓，无需更新")
        return

    print(f"# 收盘更新 · {date_str}")
    for p in state.positions.values():
        price = _fetch_close_price(p.code, date_str)
        if price:
            old = p.current_price
            p.update(price)
            change = (price - old) / old * 100 if old else 0
            print(f"  {p.name}: {old:.2f} → {price:.2f} ({change:+.2f}%) | 浮盈 ¥{p.unrealized_pnl:+.0f}")
        else:
            print(f"  ⚠️ {p.name}: 无收盘数据")

    state.net_values.append((date_str, state.net_value))
    save_state(state)
    print(f"\n日终净值: {state.net_value:.4f} | 总资产: ¥{state.total_assets:,.2f}")


def cmd_report(date_str: str):
    """生成完整日终报告"""
    state = load_state()
    print(f"# 金桥虚拟盘日终报告 · {date_str}")
    print()
    print(f"## 账户概览")
    print(f"- 净值: {state.net_value:.4f} ({'盈利' if state.net_value >= 1.0 else '亏损'} {(state.net_value-1)*100:+.2f}%)")
    print(f"- 总资产: ¥{state.total_assets:,.2f}")
    print(f"- 现金: ¥{state.cash:,.2f}")
    print(f"- 仓位: {state.total_position_ratio()*100:.1f}%")
    print()

    if state.positions:
        print("## 持仓明细")
        print("| 标的 | 股数 | 均价 | 现价 | 市值 | 浮盈 | 占比 |")
        print("|------|------|------|------|------|------|------|")
        for p in state.positions.values():
            ratio = state.position_ratio(p.code) * 100
            print(f"| {p.name} | {p.shares} | {p.avg_cost:.2f} | {p.current_price:.2f} | {p.market_value:.0f} | {p.unrealized_pnl:+.0f} | {ratio:.1f}% |")
        print()
    else:
        print("## 持仓明细")
        print("空仓")
        print()

    today_trades = [t for t in state.trades if t.date == date_str]
    if today_trades:
        print("## 今日交易")
        total_cost = 0
        total_amount = 0
        for t in today_trades:
            direction = "买入" if t.action == "buy" else "卖出"
            fee = t.commission + t.stamp_duty
            print(f"- {direction} {t.name} {t.shares}股 @{t.price:.2f} 金额¥{t.amount:.2f} 费用¥{fee:.2f} | {t.reason}")
            total_cost += fee
            total_amount += t.amount
        print(f"\n总成交: ¥{total_amount:,.2f} | 总费用: ¥{total_cost:,.2f}")
        print()

    if state.net_values:
        print("## 净值曲线")
        for d, nv in state.net_values:
            arrow = "+" if nv >= 1.0 else ""
            print(f"  {d}: {nv:.4f} ({arrow}{(nv-1)*100:+.2f}%)")

    # 日末对比
    print(f"\n> *初始资金 ¥{INITIAL_CASH:,.0f} · 运行 {len(state.net_values)} 天 · AI模拟，不构成投资建议*")


def cmd_history():
    state = load_state()
    if not state.trades:
        print("暂无交易记录")
        return
    print("# 交易历史")
    print("| 日期 | 方向 | 标的 | 数量 | 价格 | 金额 | 费用 | 原因 |")
    print("|------|------|------|------|------|------|------|------|")
    for t in state.trades:
        direction = "买入" if t.action == "buy" else "卖出"
        fee = t.commission + t.stamp_duty
        print(f"| {t.date} | {direction} | {t.name} | {t.shares} | {t.price:.2f} | {t.amount:.0f} | {fee:.2f} | {t.reason[:20]} |")


# ── main ─────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: paper_trading.py [status|execute <date>|close <date>|report <date>|history]")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "status":
        cmd_status()
    elif cmd == "execute":
        date_str = sys.argv[2] if len(sys.argv) > 2 else datetime.now(TZ).strftime("%Y-%m-%d")
        cmd_execute(date_str)
    elif cmd == "close":
        date_str = sys.argv[2] if len(sys.argv) > 2 else datetime.now(TZ).strftime("%Y-%m-%d")
        cmd_close(date_str)
    elif cmd == "report":
        date_str = sys.argv[2] if len(sys.argv) > 2 else datetime.now(TZ).strftime("%Y-%m-%d")
        cmd_report(date_str)
    elif cmd == "history":
        cmd_history()
    else:
        print(f"未知命令: {cmd}")
        sys.exit(1)
