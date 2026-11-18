# -*- coding: utf-8 -*-
"""功能：成本计算（cost）—— **占位，尚未接入**。

它属于 `cost` 域（`journal_io/contracts.py`）：那个域的输入是采购明细
（物料 / 数量 / 单价），与序时账完全独立。先把这个域和它的卡片立起来，是为了
让"分层"这件事从第一天就是真的：换一个域，新建任务的上传标的就跟着换。

`available=False` 的语义只有一条：**可以看，不能建任务**。建出来一份没有功能
能跑的数据，比暂时不让建更糟——它会以"0/1 功能可用"的形式永久留在任务列表里，
用户每次看到都要重新判断一次"这是不是出错了"。

接入时要做的事（写在这里，免得只在某人的聊天记录里）：
  1. `features/cost/stages.py`：读表 → 归集物料 → 分摊 → 复核 → 导出；
  2. `build()` 里填 stages / exports，并把 `available` 改成 True；
  3. 字段契约在 `build()` 的 `requires` 里填，就是下面这一行：
     `("item", "qty", "price")`，两个域共用同一份读表与映射表，不要再写一套。
"""

from __future__ import annotations

from jobflow.feature import Feature


def build() -> Feature:
    return Feature(
        id="cost",
        name="成本计算",
        description="采购明细 → 物料成本；与序时账完全独立，尚未接入",
        available=False,
        stages=(),
    )
