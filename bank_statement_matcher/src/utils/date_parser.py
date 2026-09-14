"""强力日期解析器"""
import re
from datetime import date, datetime
from typing import Optional


def robust_parse_date(raw) -> Optional[date]:
    """
    强力识别各种格式的日期字符串
    支持:
      - 2024/12/31, 2024-12-31, 2024.12.31
      - 20241231
      - 2024/12/31 10:12:35
      - Excel 序列号 (如果传入的是数字)
    """
    if raw is None:
        return None

    # 已经是 date/datetime 类型
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw

    # Excel 序列号（粗略判断：1900-2100之间的数字）
    if isinstance(raw, (int, float)):
        if 1 <= raw <= 2958465:  # Excel 日期范围
            return _excel_serial_to_date(int(raw))
        # 也可能是 20241231 这种纯数字
        raw = str(int(raw))
    else:
        raw = str(raw).strip()

    if not raw or raw.lower() in ('nan', 'nat', 'none', ''):
        return None

    # 先清理常见分隔符为统一格式
    cleaned = raw

    # 1. 标准分隔符格式：2024/12/31, 2024-12-31, 2024.12.31
    pattern_std = re.compile(r'(\d{4})[/.\-](\d{1,2})[/.\-](\d{1,2})')
    m = pattern_std.search(cleaned)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(year, month, day)
        except ValueError:
            pass

    # 2. 紧凑格式：20241231 (8位数字)
    pattern_compact = re.compile(r'(\d{4})(\d{2})(\d{2})')
    m = pattern_compact.search(cleaned)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        # 简单校验：年份20xx，月份1-12，日期1-31
        if 2000 <= year <= 2099 and 1 <= month <= 12 and 1 <= day <= 31:
            try:
                return date(year, month, day)
            except ValueError:
                pass

    # 3. 尝试 dateutil 解析器兜底
    try:
        from dateutil import parser
        dt = parser.parse(cleaned, yearfirst=True, dayfirst=False)
        return dt.date()
    except Exception:
        pass

    return None


def _excel_serial_to_date(serial: int) -> date:
    """Excel 序列号转 date（Windows 默认 1900 日期系统）"""
    # Excel 的 1 对应 1900-01-01（有个 1900 闰年 bug，这里简单处理）
    base = date(1899, 12, 30)
    return base + __import__('datetime').timedelta(days=serial)
