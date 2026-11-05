# -*- coding: utf-8 -*-
"""把训练集里的长尾桶补到目标条数（默认 30）。

只补「样本太少导致模型学不会」的桶，合成样本遵循真实科目组合与摘要句式，
并打 `synthetic: true` 标记，便于日后用真实数据替换。

不改已有样本，只追加。运行：python summary_cleaner/nn/augment_training_data.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

_THIS = Path(__file__).resolve()
ROOT = _THIS.parents[2]
SRC = _THIS.parent / "_storage" / "training_data.json"
TARGET = 30

# 股东 / 对手方名，用于句式轮换（避免摘要机械重复）
NAMES = [
    "深圳品誉", "汇金实业", "华创投资", "恒信集团", "瑞丰资本",
    "启明创投", "东方资产", "中科控股", "南方实业", "鼎晖投资",
]
BANKS = ["招商银行", "中国银行", "工商银行", "建设银行", "浦发银行", "兴业银行"]
PARTIES = ["宏远科技", "嘉信物流", "天成建设", "恒达贸易", "正泰电子"]

SPECS = {
    "权益筹资": {
        "templates": [
            ("收到{name}投资款", ["实收资本[贷]", "银行存款[借]"]),
            ("{name}增资款到账", ["实收资本[贷]", "银行存款[借]"]),
            ("收到{name}实收资本", ["实收资本[贷]", "银行存款[借]"]),
            ("收到注册资本金（第{n}期）", ["实收资本[贷]", "银行存款[借]"]),
            ("{name}出资款到账", ["实收资本[贷]", "银行存款[借]"]),
            ("收到{name}资本溢价", ["资本公积[贷]", "实收资本[贷]", "银行存款[借]"]),
            ("验资款到账（{name}）", ["实收资本[贷]", "银行存款[借]"]),
            ("股东追加出资-{name}", ["实收资本[贷]", "银行存款[借]"]),
            ("收到新股东{name}入股款", ["股本[贷]", "银行存款[借]"]),
            ("增资扩股款到账-{name}", ["股本[贷]", "资本公积[贷]", "银行存款[借]"]),
        ],
    },
    "筹资费用": {
        "templates": [
            ("支付{bank}借款手续费", ["财务费用[借]", "银行存款[贷]"]),
            ("支付融资顾问费-{name}", ["财务费用[借]", "银行存款[贷]"]),
            ("支付债券发行费用", ["财务费用[借]", "银行存款[贷]"]),
            ("融资租赁租金（第{n}期）", ["长期应付款[借]", "银行存款[贷]"]),
            ("支付担保费-{name}", ["财务费用[借]", "银行存款[贷]"]),
            ("支付{bank}银团贷款安排费", ["财务费用[借]", "银行存款[贷]"]),
            ("支付融资服务费-{name}", ["财务费用[借]", "银行存款[贷]"]),
            ("支付保理手续费", ["财务费用[借]", "银行存款[贷]"]),
        ],
    },
    "其他经营收支": {
        "templates": [
            ("收到{party}违约金", ["营业外收入[贷]", "银行存款[借]"]),
            ("收到保险赔款-{party}", ["营业外收入[贷]", "银行存款[借]"]),
            ("收到{party}诉讼赔款", ["营业外收入[贷]", "银行存款[借]"]),
            ("对外捐赠支出-{party}", ["营业外支出[借]", "银行存款[贷]"]),
            ("支付{party}违约赔偿款", ["营业外支出[借]", "银行存款[贷]"]),
            ("存货盘亏损失（管理原因查明）", ["营业外支出[借]", "待处理财产损溢[贷]"]),
            ("收到{party}质量索赔款", ["营业外收入[贷]", "银行存款[借]"]),
            ("支付行政罚款", ["营业外支出[借]", "银行存款[贷]"]),
        ],
    },
}


def count_bucket(buckets, name):
    return sum(len(g["records"]) for g in buckets.get(name, []))


def main():
    data = json.loads(SRC.read_text(encoding="utf-8"))
    buckets = data["buckets"]

    added = defaultdict(int)
    for name, spec in SPECS.items():
        have = count_bucket(buckets, name)
        need = TARGET - have
        if need <= 0:
            print(f"{name}: 已有 {have} 条，无需补充")
            continue

        groups = buckets.setdefault(name, [])
        by_subj = {}
        for g in groups:
            by_subj[tuple(g["subjects"])] = g

        existing = {r["summary"] for g in groups for r in g["records"]}
        made = 0
        i = 0
        tpls = spec["templates"]
        while made < need and i < need * 6:
            tpl, subj = tpls[i % len(tpls)]
            name_i = NAMES[(i // len(tpls)) % len(NAMES)]
            bank_i = BANKS[(i // len(tpls)) % len(BANKS)]
            party_i = PARTIES[(i // len(tpls)) % len(PARTIES)]
            n_i = i // (len(tpls) * 3) + 1
            summary = tpl.format(name=name_i, bank=bank_i, party=party_i, n=n_i)
            i += 1
            if summary in existing:
                continue
            key = tuple(subj)
            if key not in by_subj:
                by_subj[key] = {"subjects": list(subj), "records": []}
                groups.append(by_subj[key])
            by_subj[key]["records"].append({
                "summary": summary, "count": 1, "synthetic": True,
            })
            existing.add(summary)
            made += 1

        added[name] = made
        print(f"{name}: {have} → {have + made}（补 {made} 条）")

    data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data.setdefault("augment", {})["at"] = data["updated_at"]
    data["augment"]["target"] = TARGET
    data["augment"]["added"] = dict(added)

    stat = {n: count_bucket(buckets, n) for n in buckets}
    data["stats"]["total_records"] = sum(stat.values())

    SRC.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n桶数 {len(buckets)}，总摘要 {sum(stat.values()):,}")
    low = sorted(((v, k) for k, v in stat.items() if v < TARGET))
    print("仍不足 %d 条的桶：%s" % (TARGET, low or "无"))


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
