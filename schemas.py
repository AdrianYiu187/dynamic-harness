"""
schemas.py — Dynamic Harness 統一資料模型
============================================

設計原則（Rule 2 最小代碼、Rule 3 精準修改）：
- 不動 4 套現有 router 的內部資料結構
- 只在頂層包一個 envelope，把異質結果標準化
- raw_result 保留原始輸出，下游可選擇深入解析

日期：2026-06-05
"""
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable
from enum import Enum


class Domain(Enum):
    """支援的領域"""
    FT = "ft"             # 足球博彩
    STOCK = "stock"       # 股票分析
    CODING = "coding"     # 編碼開發
    HERMES = "hermes"     # 通用 HermesTeamAgent
    GENERAL = "general"   # 通用 fallback


@dataclass
class CapabilityRef:
    """單個能力引用（跨域統一 ID）"""
    id: str               # 原 ID（E50, S5, C3, task_type）
    name: str             # 顯示名稱
    domain: str           # 所屬 domain
    confidence: float = 1.0  # 匹配信心度


@dataclass
class WorkflowStep:
    """統一 workflow 步驟格式"""
    step: int             # 步驟序號
    action: str           # 動作名稱
    role: str             # 執行角色
    domain: str           # 所屬 domain
    raw: Any = None       # 原始步驟物件（保留向下相容）


@dataclass
class RouteEnvelope:
    """統一路由結果 — 4 套 router 的共同出口"""
    task_text: str
    detected_domain: str            # Domain.value
    domain_confidence: float        # 0-1
    capabilities: List[CapabilityRef]
    workflow: List[WorkflowStep]
    raw_result: Any                 # 原始 router 輸出（dict 或物件）
    adapter_used: str               # adapter class 名稱
    error: Optional[str] = None     # 失敗時填入
    
    def to_dict(self) -> Dict[str, Any]:
        """序列化為 dict（JSON-safe）"""
        d = asdict(self)
        # raw_result 與 workflow[].raw 可能非 JSON-safe
        d['raw_result'] = _safe_repr(self.raw_result)
        for step in d['workflow']:
            step['raw'] = _safe_repr(step.get('raw'))
        return d


def _safe_repr(obj: Any, max_len: int = 500) -> Any:
    """對未知物件做安全序列化"""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_safe_repr(x, max_len) for x in obj[:20]]
    if isinstance(obj, dict):
        return {str(k): _safe_repr(v, max_len) for k, v in list(obj.items())[:30]}
    # dataclass / 一般物件
    s = repr(obj)
    return s[:max_len] + "..." if len(s) > max_len else s


@runtime_checkable
class DomainAdapter(Protocol):
    """所有 adapter 必須實作的介面"""
    domain: Domain
    
    def can_handle(self, task_text: str) -> float:
        """回傳 0-1 信心度"""
        ...
    
    def route(self, task_text: str) -> RouteEnvelope:
        """實際路由，回傳統一 envelope"""
        ...


# === 領域關鍵字（給 can_handle fallback 用）===
DOMAIN_KEYWORDS: Dict[str, List[str]] = {
    Domain.FT.value: [
        # 中文
        "足球", "赔率", "盤口", "讓球", "大小球", "英超", "西甲", "意甲", "德甲",
        "歐霸", "歐冠", "亞冠", "主場", "客場", "讓分", "角球", "比分",
        # 英文
        "football", "soccer", "premier league", "la liga", "match", "betting odds",
        "handicap", "over/under", "btts", "1x2",
    ],
    Domain.STOCK.value: [
        # 中文
        "股票", "股價", "投資", "市值", "目標價", "市盈率", "基本面", "技術面",
        "財報", "營收", "淨利", "毛利率", "ROE", "PE", "PB",
        # 港股代碼
        "01810", "00700", "09988", "03690", "01024",
        # 英文
        "stock", "equity", "HK$", "A股", "美股", "earnings", "P/E ratio",
    ],
    Domain.CODING.value: [
        # 中文
        "網頁", "網站", "前端", "後端", "API", "數據庫", "代碼", "代碼審計",
        "代碼審查", "重構", "debug", "bug", "修復", "重構", "測試",
        "React", "Vue", "Django", "Flask", "FastAPI",
        # 英文
        "code", "web app", "frontend", "backend", "refactor", "test",
        "javascript", "typescript", "python", "rust", "go",
    ],
    Domain.HERMES.value: [
        # 通用研究
        "論文", "學術", "研究", "數據分析", "機器學習", "統計",
        "paper", "arxiv", "research", "ML", "AI", "model",
        # 通用
        "分析", "報告", "深度", "review",
    ],
}
