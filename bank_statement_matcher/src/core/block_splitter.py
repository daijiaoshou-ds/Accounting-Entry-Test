"""最小平扫区域分块（Block Splitter）"""
from typing import List, Tuple, Dict


def split_into_blocks(
    gl_entries: List,
    bank_entries: List,
    tol: float = 0.01
) -> Tuple[List[Tuple[List, List]], Dict]:
    """
    使用累计额哈希交集法，将序时账和银行流水分割为多个最小平扫区域 Block

    核心思路（来自 theory.md）：
    1. 分别计算 GL 和 Bank 的逐行累计额数组
    2. 把两个累计额数组变成哈希集合，取交集
    3. 交集有多少个，就代表中途它们平了几次
    4. 每个交集点，就分割为一个区域

    修复了旧版双指针算法的 bug：切分后没有重新对齐累计额，导致后续匹配点错位。

    返回: (blocks, log_info)
    blocks: [(gl_block, bank_block), ...] 按原始顺序
    log_info: 分块过程的诊断信息
    """
    log_info = {
        "gl_count": len(gl_entries),
        "bank_count": len(bank_entries),
        "gl_total": 0.0,
        "bank_total": 0.0,
        "cut_points": [],
        "blocks": [],
    }

    if not gl_entries or not bank_entries:
        log_info["note"] = "GL或Bank为空，无法分块"
        return [(gl_entries, bank_entries)], log_info

    # 计算累计额
    gl_cumsum = _cumsum(gl_entries)
    bank_cumsum = _cumsum(bank_entries)

    total_gl = gl_cumsum[-1] if gl_cumsum else 0.0
    total_bank = bank_cumsum[-1] if bank_cumsum else 0.0
    log_info["gl_total"] = round(total_gl, 2)
    log_info["bank_total"] = round(total_bank, 2)

    # 总额校验：如果总额差异太大，说明数据不平，直接返回一个大 block
    # total_diff = abs(total_gl - total_bank)
    # if total_diff > max(tol, abs(total_gl) * 0.001):
    #     log_info["note"] = f"总额不平，差异 {total_diff:.2f}，无法分块"
    #     return [(gl_entries, bank_entries)], log_info
    
    # 总额不平，继续切block
    total_diff = abs(total_gl - total_bank)
    if total_diff > max(tol, abs(total_gl) * 0.001):
        log_info["warning"] = f"总额不平，差异 {total_diff:.2f}，但继续尝试分块"

    # 构建 Bank 累计额字典: {rounded_value: index}
    # 由于累计额单调（同方向），每个值只保留第一次出现的位置
    bank_dict: Dict[float, int] = {}
    for i, v in enumerate(bank_cumsum):
        key = round(v, 2)
        if key not in bank_dict:
            bank_dict[key] = i

    # 找所有匹配点（GL 累计额在 Bank 累计额中出现的位置）
    # 始终包含起点 (0, 0)
    cut_points = [(0, 0)]  # (gl_end_index, bank_end_index)

    for i, v in enumerate(gl_cumsum):
        key = round(v, 2)
        if key in bank_dict:
            j = bank_dict[key]
            gl_end = i + 1
            bank_end = j + 1
            # 确保切分点比之前的大（避免重复或倒退）
            last_gl, last_bank = cut_points[-1]
            if gl_end > last_gl and bank_end > last_bank:
                cut_points.append((gl_end, bank_end))

    # 确保终点被包含
    final_gl = len(gl_entries)
    final_bank = len(bank_entries)
    if cut_points[-1] != (final_gl, final_bank):
        cut_points.append((final_gl, final_bank))

    # 合并会拆散同客商 GL 的相邻 block
    _merge_split_groups(gl_entries, cut_points)

    # 处理总额不平导致的末尾空 block：将空 GL / 空 Bank 的剩余项合并到最后一个有效 block
    while len(cut_points) >= 2:
        last_gl, last_bank = cut_points[-2]
        end_gl, end_bank = cut_points[-1]
        if end_gl == last_gl and end_bank > last_bank:
            # GL 已用完，Bank 有剩余 → 合并 Bank 到前一个 block
            cut_points[-2] = (last_gl, end_bank)
            cut_points.pop()
        elif end_gl > last_gl and end_bank == last_bank:
            # Bank 已用完，GL 有剩余 → 合并 GL 到前一个 block
            cut_points[-2] = (end_gl, last_bank)
            cut_points.pop()
        else:
            break

    log_info["cut_points"] = cut_points

    # 按切分点生成 blocks
    blocks = []
    for k in range(1, len(cut_points)):
        gl_start, bank_start = cut_points[k - 1]
        gl_end, bank_end = cut_points[k]

        gl_block = gl_entries[gl_start:gl_end]
        bank_block = bank_entries[bank_start:bank_end]

        block_gl_sum = sum(e.amount for e in gl_block)
        block_bank_sum = sum(e.amount for e in bank_block)

        block_info = {
            "gl_start": gl_start,
            "gl_end": gl_end,
            "gl_count": len(gl_block),
            "bank_start": bank_start,
            "bank_end": bank_end,
            "bank_count": len(bank_block),
            "gl_sum": round(block_gl_sum, 2),
            "bank_sum": round(block_bank_sum, 2),
            "diff": round(block_gl_sum - block_bank_sum, 2),
        }
        log_info["blocks"].append(block_info)

        blocks.append((gl_block, bank_block))

    return blocks, log_info


def _get_gl_party(gl_entry) -> str:
    """从 GL 条目提取客商名称（用于判断是否同客商多笔）"""
    parties = getattr(gl_entry, 'counterparties', None)
    if parties:
        return parties[0] if parties else ''
    return getattr(gl_entry, 'customer_name', '') or ''


def _merge_split_groups(gl_entries: List, cut_points: List[Tuple[int, int]]) -> None:
    """
    合并那些会拆散同客商 GL 的切分点。
    如果相邻两个 block 的 GL 中有相同客商，删除中间的切分点将它们合并。
    """
    if len(cut_points) <= 2:
        return  # 只有起点和终点，无需合并

    # 先计算每个 block 区间的 GL 客商集合
    block_parties = []
    for k in range(1, len(cut_points)):
        gl_start = cut_points[k - 1][0]
        gl_end = cut_points[k][0]
        parties = set()
        for i in range(gl_start, gl_end):
            p = _get_gl_party(gl_entries[i])
            if p:
                parties.add(p)
        block_parties.append(parties)

    # 从后往前合并，避免索引偏移
    i = len(block_parties) - 1
    while i > 0:
        if block_parties[i] & block_parties[i - 1]:
            # 有交集 → 合并：删除 cut_points[i]
            del cut_points[i]
            # 更新 block_parties：合并两个集合
            merged = block_parties[i] | block_parties[i - 1]
            block_parties[i - 1] = merged
            del block_parties[i]
        i -= 1


def _cumsum(entries: List) -> List[float]:
    """计算累计额数组"""
    result = []
    s = 0.0
    for e in entries:
        s += e.amount
        result.append(s)
    return result
