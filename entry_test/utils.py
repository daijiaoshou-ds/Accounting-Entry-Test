"""
工具函数模块
"""
import re
import pandas as pd
import numpy as np


def normalize_subject(subject):
    """
    标准化科目名称，去除空格和特殊字符
    """
    if pd.isna(subject):
        return ""
    return str(subject).strip().replace(" ", "").replace("\t", "")


def extract_first_level_subject(subject_name, subject_code=None):
    """
    从科目名称或科目编码提取一级科目
    
    规则：
    1. 如果科目编码是3位或以下，直接返回科目名称作为一级科目
    2. 如果科目名称包含"-"或"_"，取第一部分
    3. 否则返回原科目名称
    """
    if pd.isna(subject_name):
        return ""
    
    subject_name = normalize_subject(subject_name)
    
    # 如果有科目编码且是3位或以下，认为是一级科目
    if subject_code is not None and not pd.isna(subject_code):
        code_str = str(subject_code).strip()
        if len(code_str) <= 3 and code_str.isdigit():
            return subject_name
    
    # 尝试从科目名称拆分
    for sep in ['-', '_', '——', '—']:
        if sep in subject_name:
            return subject_name.split(sep)[0]
    
    return subject_name


def generate_unique_voucher_id(date_str, voucher_no):
    """
    生成凭证唯一值
    格式：2025年9月-记-0497
    
    参数:
        date_str: 日期字符串，支持多种格式
        voucher_no: 原凭证号（如"记-0497"）
    """
    try:
        # 尝试解析日期
        if pd.isna(date_str):
            return str(voucher_no) if not pd.isna(voucher_no) else ""
        
        # 如果是datetime对象
        if isinstance(date_str, pd.Timestamp):
            year = date_str.year
            month = date_str.month
            return f"{year}年{month}月-{voucher_no}"
        
        # 字符串解析
        date_str = str(date_str).strip()
        
        # 尝试匹配 2025/9/30 或 2025-9-30 格式
        patterns = [
            r'(\d{4})[/-](\d{1,2})[/-]\d{1,2}',  # 2025/9/30, 2025-9-30
            r'(\d{4})年(\d{1,2})月\d{1,2}日?',    # 2025年9月30日
        ]
        
        for pattern in patterns:
            match = re.match(pattern, date_str)
            if match:
                year = match.group(1)
                month = int(match.group(2))
                return f"{year}年{month}月-{voucher_no}"
        
        # 如果无法解析，返回原凭证号
        return str(voucher_no) if not pd.isna(voucher_no) else ""
        
    except Exception:
        return str(voucher_no) if not pd.isna(voucher_no) else ""


def format_amount(amount):
    """
    格式化金额，去除逗号等分隔符
    """
    if pd.isna(amount):
        return 0.0
    
    if isinstance(amount, (int, float)):
        return float(amount)
    
    # 处理字符串格式的金额，如 "4,650.00"
    amount_str = str(amount).replace(",", "").replace(" ", "")
    try:
        return float(amount_str)
    except ValueError:
        return 0.0


def get_accounting_direction(debit, credit):
    """
    判断会计方向
    返回: '借' 或 '贷'
    """
    debit_amt = format_amount(debit)
    credit_amt = format_amount(credit)
    
    if debit_amt > 0:
        return '借'
    elif credit_amt > 0:
        return '贷'
    else:
        return ''
