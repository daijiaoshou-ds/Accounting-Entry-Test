# -*- coding: utf-8 -*-
"""批次进度回调：encoder 每批一次，最后一次的 done 必须等于 total。

注意：摘要会先做归一化（数字被抹掉），所以 40 条"编号 001、002…"会被并成一条，
必须用真正不同的词，才测得到多批。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.classify.classifier import get_classifier

WORDS = [
    "办公用品采购报销", "差旅费住宿餐饮", "销售商品收到货款", "采购原材料入库",
    "支付银行手续费", "计提本月工资薪酬", "缴纳增值税及附加", "购买生产设备一台",
    "收到客户预付款项", "退还供应商押金", "支付水电物业费用", "结转本期销售成本",
    "确认咨询服务收入", "租赁办公场地租金", "报销员工交通补贴", "支付广告宣传费用",
    "购入无形资产软件", "计提固定资产折旧", "收到政府补助款项", "偿付短期借款利息",
    "支付审计服务费用", "销售退回冲减收入", "预付材料采购订金", "收回应收账款款项",
    "支付员工社会保险", "购入低值易耗品", "支付快递物流运费", "收到投资收益分红",
    "缴纳企业所得税费", "支付法律顾问费用", "采购包装材料一批", "支付网络通信费用",
    "确认工程完工收入", "摊销长期待摊费用", "支付银行承兑汇票", "收到出口退税款额",
    "计提存货跌价准备", "支付设备维修费用", "确认租赁收入款项", "支付商标注册费用",
]

clf = get_classifier()
clf._load_nn_if_available()
assert clf._nn_available, "模型不可用，先确认 core/nn/_storage 四件交付物"
# 关掉摘要向量缓存：同一串摘要第二次跑会全部命中缓存，那时没有编码工作，
# 也就没有进度回调（这是对的），但测不到多批。
clf._nn._cache = None

items = [{"summary": w, "subjects": ["银行存款"]} for w in WORDS]
calls = []
clf._nn.predict_batch_full(items, chunk_size=16, on_progress=lambda done, total: calls.append((done, total)))

print("回调次数", len(calls), "序列", calls)
assert calls, "一次回调都没有"
assert calls[-1][0] == calls[-1][1], f"最后一批没有跑满：{calls[-1]}"
assert all(b[0] > a[0] for a, b in zip(calls, calls[1:])), f"进度没有单调递增：{calls}"
print("✅ 分批进度回调正常")
