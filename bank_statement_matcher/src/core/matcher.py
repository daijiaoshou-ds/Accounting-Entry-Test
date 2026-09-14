"""主匹配引擎：编排整个核对流程"""
import pandas as pd
from typing import List, Tuple, Dict
from collections import defaultdict

from bank_statement_matcher.src.config.field_mapper import FieldMapper
from bank_statement_matcher.src.core.cleaner import clean_journal, clean_bank, split_by_detail_account, split_by_month_and_direction
from bank_statement_matcher.src.core.large_block import solve_large_block as solve_block
from bank_statement_matcher.src.utils.performance_logger import perf_logger


class ReconciliationResult:
    """核对结果容器"""
    def __init__(self):
        self.matches: List[Tuple] = []          # (gl, [banks], match_type)
        self.unmatched_gl: List = []
        self.unmatched_bank: List = []
        self.summary: Dict = {}


class ReconciliationEngine:
    """序时账与银行流水核对引擎"""

    def __init__(self, tol: float = 0.01, date_window_days: int = 31, dynamic_greedy: bool = True):
        self.tol = tol
        self.date_window_days = date_window_days
        self.dynamic_greedy = dynamic_greedy
        self.field_mapper = FieldMapper()

    def load_journal(self, df: pd.DataFrame, manual_map: Dict[str, str] = None) -> List[str]:
        """加载序时账，返回未识别到的必填字段列表（由调用方决定如何处理）"""
        self.field_mapper.auto_map_journal(df)
        if manual_map:
            for k, v in manual_map.items():
                self.field_mapper.set_journal_field(k, v)
        self.journal_df = df
        return self.field_mapper.validate_journal()

    def load_bank(self, filename: str, df: pd.DataFrame, manual_map: Dict[str, str] = None) -> List[str]:
        """加载指定银行流水文件，返回未识别到的必填字段列表"""
        self.field_mapper.auto_map_bank(filename, df)
        if manual_map:
            for k, v in manual_map.items():
                self.field_mapper.set_bank_field(filename, k, v)
        if not hasattr(self, 'bank_dfs'):
            self.bank_dfs = {}
        self.bank_dfs[filename] = df
        return self.field_mapper.validate_bank(filename)

    def check_ready(self, bank_filenames: List[str] = None) -> List[str]:
        """检查是否所有必填字段已配置，返回错误信息列表"""
        errors = []
        journal_missing = self.field_mapper.validate_journal()
        if journal_missing:
            errors.append(f"序时账缺少必填字段: {journal_missing}")

        if bank_filenames:
            for filename in bank_filenames:
                bank_missing = self.field_mapper.validate_bank(filename)
                if bank_missing:
                    errors.append(f"银行流水[{filename}]缺少必填字段: {bank_missing}")
        else:
            all_missing = self.field_mapper.validate_all_banks()
            for filename, missing in all_missing:
                errors.append(f"银行流水[{filename}]缺少必填字段: {missing}")
        return errors

    def run(self, detail_account: str = None) -> Dict[str, ReconciliationResult]:
        """
        执行核对流程
        
        如果指定 detail_account，只核对该明细科目；否则全部核对
        返回: {detail_account: ReconciliationResult}
        """
        results, _ = self._run_internal(detail_account)
        return results

    def run_with_summary(self, detail_account: str = None):
        """
        执行核对流程，同时返回月度汇总表和诊断信息
        
        返回: (results, monthly_summary, diagnostics)
        """
        results, diagnostics = self._run_internal(detail_account)
        monthly_summary = self._build_monthly_summary(diagnostics)
        return results, monthly_summary, diagnostics

    def _run_internal(self, detail_account: str = None):
        """内部执行方法，返回 results 和 diagnostics"""
        import os
        from bank_statement_matcher.src.core.journal_opponent_analysis import transform_journal_df
        from bank_statement_matcher.src.utils.name_vectorizer import NameVectorizer

        # 初始化性能日志
        log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "log")
        perf_logger.set_log_dir(log_dir)
        perf_logger.info(f"========== 核对开始 ==========")
        perf_logger.counter("金额容差(tol)", self.tol)
        perf_logger.counter("日期容差(天)", self.date_window_days)
        perf_logger.counter("动态贪心", self.dynamic_greedy)

        # 1. 对方科目分析（在清洗之前，需要完整序时账）
        with perf_logger.stage("1-对方科目分析"):
            transformed_journal_df, counterparty_stats = transform_journal_df(
                self.journal_df, self.field_mapper.journal_map
            )
            perf_logger.counter("序时账原始行数", len(self.journal_df))
            perf_logger.counter("变形后行数", len(transformed_journal_df))
            perf_logger.counter("简单拆分凭证数", counterparty_stats.get("simple_count", 0))
            perf_logger.counter("复杂聚合凭证数", counterparty_stats.get("complex_count", 0))
            perf_logger.counter("多银行账户凭证数", counterparty_stats.get("multi_bank_count", 0))
            perf_logger.counter("无对方科目凭证数", counterparty_stats.get("no_counterparty_count", 0))

        # 2. 清洗变形后的序时账
        with perf_logger.stage("2-清洗序时账"):
            journal_entries, journal_errors = clean_journal(
                transformed_journal_df, self.field_mapper.journal_map
            )
            perf_logger.counter("序时账清洗成功", len(journal_entries))
            perf_logger.counter("序时账清洗失败", len(journal_errors))

        # 按文件分别清洗银行流水，然后合并
        with perf_logger.stage("3-清洗银行流水"):
            bank_entries = []
            bank_errors = []
            for filename, df in getattr(self, 'bank_dfs', {}).items():
                b_entries, b_errors = clean_bank(
                    df, self.field_mapper.bank_maps.get(filename, {})
                )
                bank_entries.extend(b_entries)
                bank_errors.extend(b_errors)
            perf_logger.counter("银行流水清洗成功", len(bank_entries))
            perf_logger.counter("银行流水清洗失败", len(bank_errors))

        diagnostics = {
            "journal_total": len(journal_entries),
            "journal_errors": len(journal_errors),
            "bank_total": len(bank_entries),
            "bank_errors": len(bank_errors),
            "counterparty_analysis": counterparty_stats,
            "journal_by_account": {},
            "bank_monthly": [],
            "matching_process": [],
        }

        # bank_monthly 展平为列表
        bank_month_dict = {}
        for e in bank_entries:
            k = (e.tx_date.year, e.tx_date.month, 'income' if e.is_income else 'expense')
            bank_month_dict.setdefault(k, {"count": 0, "sum": 0.0})
            bank_month_dict[k]["count"] += 1
            bank_month_dict[k]["sum"] += e.amount
        for (year, month, direction), info in bank_month_dict.items():
            diagnostics["bank_monthly"].append({
                "year": year, "month": month, "direction": direction,
                "count": info["count"], "sum": round(info["sum"], 2),
            })

        # 2. 按明细科目分账
        journal_by_account = split_by_detail_account(journal_entries)

        for acc, entries in journal_by_account.items():
            gl_by_month_dir = split_by_month_and_direction(entries)
            diag_rows = []
            for key, vals in gl_by_month_dir.items():
                (year, month), direction = key
                diag_rows.append({
                    "year": year, "month": month, "direction": direction,
                    "count": len(vals), "sum": round(sum(v.amount for v in vals), 2),
                })
            diagnostics["journal_by_account"][acc] = diag_rows

        results = {}

        accounts = [detail_account] if detail_account else list(journal_by_account.keys())

        # 训练公司名称向量化器（需要所有 GL 和 Bank 的公司名称）
        with perf_logger.stage("4-训练名称向量化器"):
            all_names = []
            for e in journal_entries:
                if e.counterparties:
                    all_names.extend(e.counterparties)
                elif e.customer_name:
                    all_names.append(e.customer_name)
            for b in bank_entries:
                if b.counter_party:
                    all_names.append(b.counter_party)
            perf_logger.counter("收集到的名称总数", len(all_names))

            vectorizer = NameVectorizer()
            vectorizer.fit(all_names)
            perf_logger.counter("去重后名称数", len(vectorizer.vectors))
            perf_logger.counter("Bigram词汇表大小", len(vectorizer.vocab))

        for acc in accounts:
            gl_list = journal_by_account.get(acc, [])
            if not gl_list:
                continue

            perf_logger.info(f"=== 开始核对科目: {acc} (GL {len(gl_list)}条) ===")

            # 3. 按月份+收支方向拆分
            gl_by_month_dir = split_by_month_and_direction(gl_list)
            bank_by_month_dir = split_by_month_and_direction(bank_entries)

            result = ReconciliationResult()
            account_diag = {"account": acc, "months": []}

            month_keys = sorted(gl_by_month_dir.keys(), key=lambda k: (k[0][0], k[0][1], k[1]))
            for key_idx, key in enumerate(month_keys):
                gl_month_entries = gl_by_month_dir[key]
                (year, month), direction = key
                month_info = {
                    "year": year, "month": month, "direction": direction,
                    "gl_count": len(gl_month_entries),
                    "gl_sum": round(sum(e.amount for e in gl_month_entries), 2),
                }
                bank_month_entries = bank_by_month_dir.get(key, [])
                month_info["bank_count"] = len(bank_month_entries)
                month_info["bank_sum"] = round(sum(e.amount for e in bank_month_entries), 2) if bank_month_entries else 0.0

                month_label = f"{year}-{month:02d}/{direction}"
                perf_logger.info(f"--- {month_label}: GL {len(gl_month_entries)}条, Bank {len(bank_month_entries)}条 ---")

                if not bank_month_entries:
                    perf_logger.info("  无 Bank 数据，全部未匹配")
                    result.unmatched_gl.extend(gl_month_entries)
                    month_info["matches"] = 0
                    account_diag["months"].append(month_info)
                    continue

                # 4. 统一匹配（5阶段：1v1 → Bank→GL聚合 → GL→Bank聚合 → 一对一回溯 → DP兜底）
                month_label = f"{year}-{month:02d}/{direction}"
                with perf_logger.stage(f"4-匹配/{month_label}"):
                    m, ug, ub = solve_block(
                        gl_month_entries, bank_month_entries, self.tol,
                        self.date_window_days, dynamic_greedy=self.dynamic_greedy,
                        vectorizer=vectorizer
                    )
                    perf_logger.counter("匹配数", len(m))
                    perf_logger.counter("未匹配GL", len(ug))
                    perf_logger.counter("未匹配Bank", len(ub))

                result.matches.extend(m)
                result.unmatched_gl.extend(ug)
                result.unmatched_bank.extend(ub)
                month_info["matches"] = len(m)
                account_diag["months"].append(month_info)

            diagnostics["matching_process"].append(account_diag)

            # 汇总统计
            result.summary = {
                "detail_account": acc,
                "total_matches": len(result.matches),
                "total_unmatched_gl": len(result.unmatched_gl),
                "total_unmatched_bank": len(result.unmatched_bank),
            }
            results[acc] = result

        return results, diagnostics

    def _build_monthly_summary(self, diagnostics):
        """构建月度总体差异表"""
        rows = []
        for proc in diagnostics.get("matching_process", []):
            acc = proc["account"]
            for m in proc["months"]:
                direction_label = "收入" if m["direction"] == "income" else "支出"
                gl_sum = m["gl_sum"]
                bank_sum = m["bank_sum"]
                diff = round(gl_sum - bank_sum, 2)
                rows.append({
                    "明细科目": acc,
                    "年份": m["year"],
                    "月份": m["month"],
                    "收支": direction_label,
                    "序时账笔数": m["gl_count"],
                    "银行流水笔数": m["bank_count"],
                    "序时账金额": gl_sum,
                    "银行流水金额": bank_sum,
                    "差异": diff,
                    "匹配成功": m["matches"],
                })
        return pd.DataFrame(rows)


MATCH_TYPE_MAP = {
    # Phase 1: 一对一
    "1v1": "一对一",
    "csr_1v1": "一对一",
    # Phase 2: GL→Bank 聚合
    "gl_agg1": "GL聚合Bank-1级",
    "gl_agg2": "GL聚合Bank-2级",
    "gl_agg3": "GL聚合Bank-3级",
    "gl_agg4": "GL聚合Bank-4级",
    # Phase 3: Bank→GL 聚合
    "agg1": "Bank聚合GL-1级",
    "agg2": "Bank聚合GL-2级",
    "agg3": "Bank聚合GL-3级",
    "agg4": "Bank聚合GL-4级",
    "csr_agg1": "Bank聚合GL-1级",
    "csr_agg2": "Bank聚合GL-2级",
    "csr_agg3": "Bank聚合GL-3级",
    "csr_agg4": "Bank聚合GL-4级",
    # Phase 4: 一对一回溯
    "1v1_backtrack": "一对一回溯",
    "csr_1v1_backtrack": "一对一回溯",
    # Phase 5: DP
    "dp_subset": "DP凑GL",
    "backtrack_subset": "DP凑GL(回溯)",
    "gl_dp_subset": "GL-DP凑Bank",
    "gl_backtrack_subset": "GL-DP凑Bank(回溯)",
    # Phase 6: 时序聚合
    "wild_bank_sameday": "时序聚合Bank-同日",
    "wild_bank_2d": "时序聚合Bank-±2天",
    "wild_bank_7d": "时序聚合Bank-±7天",
    "wild_bank_month": "时序聚合Bank-同月",
    "wild_gl_sameday": "时序聚合GL-同日",
    "wild_gl_2d": "时序聚合GL-±2天",
    "wild_gl_7d": "时序聚合GL-±7天",
    "wild_gl_month": "时序聚合GL-同月",
}


def export_results(results: Dict[str, ReconciliationResult], output_path: str):
    """导出核对结果到 Excel
    
    输出格式：
    - 一对一匹配：1 行（GL + 1 个 Bank）
    - 一对多匹配：多行（GL 信息重复，每行 1 个 Bank）
    - 未匹配：各 1 行
    """
    rows = []
    for acc, res in results.items():
        # 追踪已显示过金额的 Bank（避免 Bank 一对多时重复显示）
        shown_bank_ids = set()

        # 匹配结果（展开为多行）
        for gl, banks, mtype in res.matches:
            mtype_cn = MATCH_TYPE_MAP.get(mtype, mtype)
            if len(banks) == 1:
                # 一对一：一行
                b = banks[0]
                bid = id(b)
                show_bank_amount = bid not in shown_bank_ids
                shown_bank_ids.add(bid)
                rows.append({
                    "明细科目": acc,
                    "类型": "匹配",
                    "匹配方式": mtype_cn,
                    "GL日期": getattr(gl, 'entry_date', None),
                    "GL凭证号": getattr(gl, 'voucher_no', ''),
                    "GL摘要": getattr(gl, 'abstract', ''),
                    "GL金额": gl.amount,
                    "GL客商": _fmt_counterparties(gl),
                    "Bank日期": getattr(b, 'tx_date', ''),
                    "Bank交易方": getattr(b, 'counter_party', ''),
                    "Bank金额": b.amount if show_bank_amount else None,
                    "Bank摘要": getattr(b, 'abstract', ''),
                    "Bank流水号": getattr(b, 'serial_no', ''),
                })
            else:
                # 一对多：每行一个 Bank，GL 金额只在第一行显示
                for i, b in enumerate(banks):
                    bid = id(b)
                    show_bank_amount = bid not in shown_bank_ids
                    shown_bank_ids.add(bid)
                    rows.append({
                        "明细科目": acc,
                        "类型": "匹配",
                        "匹配方式": mtype_cn,
                        "GL日期": getattr(gl, 'entry_date', None),
                        "GL凭证号": getattr(gl, 'voucher_no', ''),
                        "GL摘要": getattr(gl, 'abstract', ''),
                        "GL金额": gl.amount if i == 0 else None,
                        "GL客商": _fmt_counterparties(gl) if i == 0 else "",
                        "Bank日期": getattr(b, 'tx_date', ''),
                        "Bank交易方": getattr(b, 'counter_party', ''),
                        "Bank金额": b.amount if show_bank_amount else None,
                        "Bank摘要": getattr(b, 'abstract', ''),
                        "Bank流水号": getattr(b, 'serial_no', ''),
                    })
        # 未匹配 GL
        for gl in res.unmatched_gl:
            rows.append({
                "明细科目": acc,
                "类型": "未匹配-序时账",
                "匹配方式": "",
                "GL日期": getattr(gl, 'entry_date', None),
                "GL凭证号": getattr(gl, 'voucher_no', ''),
                "GL摘要": getattr(gl, 'abstract', ''),
                "GL金额": gl.amount,
                "GL客商": _fmt_counterparties(gl),
                "Bank日期": None,
                "Bank交易方": "",
                "Bank金额": None,
            })
        # 未匹配 Bank
        for b in res.unmatched_bank:
            rows.append({
                "明细科目": acc,
                "类型": "未匹配-银行流水",
                "匹配方式": "",
                "GL日期": None,
                "GL凭证号": "",
                "GL摘要": "",
                "GL金额": None,
                "GL客商": "",
                "Bank日期": getattr(b, 'tx_date', ''),
                "Bank交易方": getattr(b, 'counter_party', ''),
                "Bank金额": b.amount,
                "Bank摘要": getattr(b, 'abstract', ''),
                "Bank流水号": getattr(b, 'serial_no', ''),
            })

    df = pd.DataFrame(rows)
    # 调整列顺序
    cols = ["明细科目", "类型", "匹配方式", "GL日期", "GL凭证号", "GL摘要", "GL金额", "GL客商",
            "Bank日期", "Bank交易方", "Bank金额", "Bank摘要", "Bank流水号"]
    df = df[[c for c in cols if c in df.columns]]
    df.to_excel(output_path, index=False)
    print(f"结果已导出: {output_path}")


def _fmt_counterparties(gl) -> str:
    """格式化GL客商列表为展示字符串"""
    parties = getattr(gl, 'counterparties', None)
    if parties:
        return "，".join(parties)
    name = getattr(gl, 'customer_name', '')
    return str(name) if name else ""
