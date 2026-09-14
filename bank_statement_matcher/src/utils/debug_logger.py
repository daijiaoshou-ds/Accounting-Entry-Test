"""Debug 日志工具：把核对全流程数据 dump 到 log/ 文件夹"""
import json
import os
from datetime import date, datetime
from typing import Any


class _DebugEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        if hasattr(obj, "amount"):
            return {
                "idx": getattr(obj, "idx", None),
                "date": getattr(obj, "entry_date", None) or getattr(obj, "tx_date", None),
                "amount": obj.amount,
                "abstract": getattr(obj, "abstract", "")[:50],
                "party": getattr(obj, "counter_party", "") or getattr(obj, "customer_name", ""),
            }
        return super().default(obj)


def _to_dict(obj):
    """把对象转成可序列化的 dict"""
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if hasattr(obj, "amount"):
        return {
            "idx": getattr(obj, "idx", None),
            "date": _to_dict(getattr(obj, "entry_date", None) or getattr(obj, "tx_date", None)),
            "amount": round(obj.amount, 2),
            "abstract": str(getattr(obj, "abstract", ""))[:60],
            "party": str(getattr(obj, "counter_party", "") or getattr(obj, "customer_name", ""))[:40],
            "voucher": str(getattr(obj, "voucher_no", ""))[:30],
        }
    return obj


def dump_debug(data: Any, filename: str):
    os.makedirs("log", exist_ok=True)
    path = os.path.join("log", filename)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, cls=_DebugEncoder)
    except Exception as e:
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"序列化失败: {e}\n")
            f.write(str(data)[:5000])
    print(f"[DEBUG] 已写入: {path}")
