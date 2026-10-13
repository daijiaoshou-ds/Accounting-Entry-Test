# -*- coding: utf-8 -*-
"""验证：① 单个 Excel 三张 sheet 的内容正确性 ② 补丁往返（含与不含）。"""
import io
import contextlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "summary_cleaner_v3"
PY = ROOT / "venv" / "Scripts" / "python.exe"
WORK = ROOT / "_dev" / "smoke" / "补丁验证"
WORK.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(SKILL))

FAIL = []


def cli(*args):
    r = subprocess.run([PY, "-X", "utf8", str(SKILL / "cli.py"), *args],
                       cwd=str(SKILL), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    for line in (r.stdout or "").splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[7:])
    print("STDOUT:", (r.stdout or "")[:500])
    print("STDERR:", (r.stderr or "")[-800:])
    return None


# ============================================================================
# ① 检查样例结果文件的 sheet 内容
# ============================================================================
print("=" * 70)
print("① 样例结果文件（_dev/smoke/输出/样例序时账_结果.xlsx）")
print("=" * 70)
xlsx = ROOT / "_dev" / "smoke" / "输出" / "样例序时账_结果.xlsx"
book = pd.ExcelFile(xlsx)
print("sheets:", book.sheet_names)
if book.sheet_names != ["原始序时账", "业务分类结果", "货币资金凭证"]:
    FAIL.append(f"sheet 名/顺序不对: {book.sheet_names}")

raw = pd.read_excel(xlsx, sheet_name="原始序时账", dtype=object)
res = pd.read_excel(xlsx, sheet_name="业务分类结果", dtype=object)
money = pd.read_excel(xlsx, sheet_name="货币资金凭证", dtype=object)

print(f"\n原始序时账  {raw.shape}  列={list(raw.columns)}")
print(f"业务分类结果 {res.shape}  列={list(res.columns)}")

# 原始序时账必须"一个字不改"：列名与源文件一致
src = pd.read_excel(ROOT / "_dev" / "smoke" / "样例序时账.xlsx", dtype=object)
if list(raw.columns) != list(src.columns):
    FAIL.append(f"原始序时账列变了: {list(raw.columns)} vs {list(src.columns)}")
if raw.shape != src.shape:
    FAIL.append(f"原始序时账行数变了: {raw.shape} vs {src.shape}")
else:
    print("  ✅ 与源文件列名/行数完全一致（一字未改）")

# 业务分类结果 = 原始 + 新增列
extra = [c for c in res.columns if c not in raw.columns]
print(f"  新增列: {extra}")
for need in ("业务分类", "分类判定", "置信度", "现金流量项目", "现金流方向", "现金流判定"):
    if need not in res.columns:
        FAIL.append(f"业务分类结果缺列: {need}")

# 科目列必须叫回「一级科目」，不能漏出内部名
if "会计科目" in res.columns or "科目" in res.columns:
    print("  ✅ 科目列已还名为「一级科目」" if "一级科目" in res.columns
          else f"  ⚠️ 未还名，实际列: {[c for c in res.columns if '科目' in str(c)]}")

# 货币资金凭证：整张凭证，且只有货币资金行有现金流量项目
print(f"\n货币资金凭证 {money.shape}")
mv = money[money["现金流量项目"].notna() & (money["现金流量项目"].astype(str) != "")]
print(f"  有现金流量项目的行数: {len(mv)}")
money_subj = money["一级科目"].astype(str).str.strip().isin(
    ["库存现金", "银行存款", "其他货币资金", "其它货币资金"])
non_money_with_item = money[~money_subj &
                            money["现金流量项目"].notna() &
                            (money["现金流量项目"].astype(str) != "")]
if len(non_money_with_item):
    FAIL.append(f"有 {len(non_money_with_item)} 条非货币资金行被填了现金流量项目")
else:
    print("  ✅ 只有货币资金行填了现金流量项目")

# 整张凭证：应包含非货币资金行
if len(money) <= len(mv):
    FAIL.append("货币资金凭证表似乎只留了货币资金行（应保留整张凭证）")
else:
    print(f"  ✅ 保留了整张凭证（{len(money) - len(mv)} 行非货币资金行也在）")

# ============================================================================
# ② 补丁往返
# ============================================================================
print("\n" + "=" * 70)
print("② 补丁往返（造数据 → 无补丁跑 → 写补丁 → 带补丁跑）")
print("=" * 70)

rows = []
# 构造三类：① 会触发待复核的歧义桶（科目未登记在裁判表）
#           ② 方向与桶不符（日常费用 + 收现）→ 也是待复核，但**原因不同**
#           ③ 程序能直接判定的普通凭证（用来验证"非待复核的行补丁不许覆盖"）
cases = [
    # ① 投资本金 + 收现，科目「其他权益工具」没登记在裁判表里 → 待复核
    ("记-001", "银行存款", "收到某公司投资款", 600000, 0),
    ("记-001", "其他权益工具", "收到某公司投资款", 0, 600000),
    # ② 日常费用 + 收现：日常费用是**单向桶**（只对应"支付其他…"），
    #    所以这里不是"退款"，而是"方向与桶不符" → 待复核，得回去改业务桶
    ("记-002", "银行存款", "收到运费退款", 30000, 0),
    ("记-002", "管理费用", "收到运费退款", 0, 30000),
    # ③ 税费 + 付现 → 程序直接判定（桶+方向），不进待复核
    ("记-003", "应交税费", "支付2月税费", 12000, 0),
    ("记-003", "银行存款", "支付2月税费", 0, 12000),
]
for vid, subj, summ, d, c in cases:
    rows.append({"凭证号": vid, "科目": subj, "科目名称": subj + "-明细",
                 "摘要": summ, "借方金额": d, "贷方金额": c, "制单人": "测试"})
df = pd.DataFrame(rows)
ledger = WORK / "补丁样例.xlsx"
df.to_excel(ledger, index=False, sheet_name="序时账")
print(f"造了 {ledger.name}: {len(df)} 行")

# 补丁键 = 1-based 数据行号。从 DataFrame 现算，别写死（加行就会错位）
def key_of(vid, subject):
    hit = df.index[(df["凭证号"] == vid) & (df["科目"] == subject)].tolist()
    assert len(hit) == 1, f"定位不到 {vid}/{subject}: {hit}"
    return str(hit[0] + 1)


KEY_BANK_001 = key_of("记-001", "银行存款")     # 收现 + 待复核（歧义桶）
KEY_BANK_003 = key_of("记-003", "银行存款")     # 付现 + 程序已判定
print(f"补丁键：记-001银行存款={KEY_BANK_001}  记-003银行存款={KEY_BANK_003}")

out1 = WORK / "第1轮"
p1 = cli("run", str(ledger), "--out", str(out1))
if not p1 or not p1.get("ok"):
    FAIL.append("第1轮 run 失败")
else:
    rev = p1["cashflow_review"]
    print(f"第1轮 待复核 {rev['count']} 条（预期 2 条：歧义桶 1 + 方向与桶不符 1）")
    for it in rev["items"]:
        print(f"  行{it['行号']} {it['业务分类']} {it['现金流方向']}现 "
              f"科目={it['科目']} 候选={it['候选项目']}")
        print(f"       原因={it['原因']}")
    if rev["count"] != 2:
        FAIL.append(f"预期 2 条待复核，实得 {rev['count']}")
    if p1["cashflow"]["needs_review_rows"] != 2:
        FAIL.append("第1轮 needs_review_rows 计数不符")

    # 两条待复核的**原因**必须不一样：一条是"科目没登记"，一条是"方向与桶不符"
    reasons = {it["业务分类"]: it for it in rev["items"]}
    amb = reasons.get("投资本金")
    mismatch = reasons.get("日常费用")
    if amb is None or "裁判表" not in amb["原因"]:
        FAIL.append(f"歧义桶那条待复核原因不对: {amb}")
    if mismatch is None:
        FAIL.append("没有「日常费用 + 收现」那条待复核")
    else:
        if "方向与桶不符" not in mismatch["原因"]:
            FAIL.append(f"方向不符那条原因不对: {mismatch['原因']}")
        # 反方向候选不该出现在候选里（否则会诱导 AI 挑一个方向不符的项目，
        # 而 apply_patch 一定会驳回——那是给 AI 挖坑）
        if mismatch["候选项目"]:
            FAIL.append(f"方向不符的行不该给候选项目: {mismatch['候选项目']}")

    if rev["count"]:
        target = amb or rev["items"][0]
        # 补丁：补上正确结论；另塞两条坏数据验证校验
        patch = {
            "_comment": "测试补丁：把待复核条目补成 收回投资收到的现金",
            "rows": {
                str(target["行号"]): {
                    "item": "收回投资收到的现金",
                    "note": "摘要为收到投资款，按投资活动处理（测试）",
                },
                "9999": {"item": "收回投资收到的现金"},   # 行号不存在
                "2": {"item": "支付的各项税费"},           # 行2是收现，方向不符
            },
        }
        pf = WORK / "cashflow_patch.json"
        pf.write_text(json.dumps(patch, ensure_ascii=False, indent=2),
                      encoding="utf-8")

        out2 = WORK / "第2轮"
        p2 = cli("run", str(ledger), "--out", str(out2),
                 "--cashflow-patch", str(pf))
        if not p2 or not p2.get("ok"):
            FAIL.append("第2轮 run 失败")
        else:
            cf = p2["cashflow"]
            print(f"\n第2轮 补丁采纳={cf['patched_rows']} 仍需复核="
                  f"{cf['needs_review_rows']}")
            print(f"  驳回: {json.dumps(cf['patch_rejected'], ensure_ascii=False)}")
            print(f"  行号不存在: {cf['patch_unknown_rows']}")
            print(f"  非待复核被拒: {cf['patch_not_review']}")
            if cf["patched_rows"] != 1:
                FAIL.append(f"预期采纳 1 条，实得 {cf['patched_rows']}")
            if cf["needs_review_rows"] != 1:
                FAIL.append(f"补丁只该消掉 1 条，应还剩 1 条（记-002 方向不符），"
                            f"实得 {cf['needs_review_rows']}")
            # 补丁键是 0-based 内部行号 + 1，所以：
            #   键 "1" = 第 1 行（银行存款/记-001）→ 采纳
            #   键 "2" = 第 2 行（其他权益工具）不是货币资金行 → 未知行号
            #   键 "9999" → 未知行号
            if cf["patch_rejected"]:
                FAIL.append(f"不该有驳回，实得 {cf['patch_rejected']}")
            if sorted(cf["patch_unknown_rows"]) != ["2", "9999"]:
                FAIL.append(f"预期未知行号 [2, 9999]，实得 {cf['patch_unknown_rows']}")

            # ── 方向不符必须被驳回 ──
            pf2 = WORK / "cashflow_patch_baddir.json"
            pf2.write_text(json.dumps({
                "rows": {KEY_BANK_001: {"item": "支付的各项税费"}},  # 该行收现，项目付现
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            p3 = cli("run", str(ledger), "--out", str(WORK / "第3轮"),
                     "--cashflow-patch", str(pf2))
            rej = (p3 or {}).get("cashflow", {}).get("patch_rejected", [])
            print(f"\n第3轮（方向不符的补丁）驳回: "
                  f"{json.dumps(rej, ensure_ascii=False)}")
            if len(rej) != 1 or "方向" not in rej[0].get("原因", ""):
                FAIL.append(f"方向不符未被正确驳回: {rej}")
            remain = (p3 or {}).get("cashflow", {}).get("needs_review_rows")
            if remain != 2:
                FAIL.append(f"驳回后应仍有 2 条待复核，实得 {remain}")

            # ── 非待复核的行，补丁不该覆盖 ──
            # 记-003（税费 + 付现）是程序直接判定的，不是待复核
            pf3 = WORK / "cashflow_patch_notreview.json"
            pf3.write_text(json.dumps({
                "rows": {KEY_BANK_003: {"item": "支付的各项税费"}},
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            p4 = cli("run", str(ledger), "--out", str(WORK / "第4轮"),
                     "--cashflow-patch", str(pf3))
            nr = (p4 or {}).get("cashflow", {}).get("patch_not_review", [])
            print(f"第4轮（补一条已判定的行）被拒: "
                  f"{json.dumps(nr, ensure_ascii=False)}")
            if len(nr) != 1:
                FAIL.append(f"非待复核行未被拒绝: {nr}")

            x2 = out2 / "补丁样例_结果.xlsx"
            r2 = pd.read_excel(x2, sheet_name="业务分类结果", dtype=object)
            patched = r2[r2["现金流判定"].astype(str) == "补丁补充"]
            print(f"  结果文件中标记为「补丁补充」的行: {len(patched)}")
            if len(patched) != 1:
                FAIL.append(f"预期 1 行标「补丁补充」，实得 {len(patched)}")
            col = patched["现金流量项目"].astype(str).tolist()
            if col != ["收回投资收到的现金"]:
                FAIL.append(f"补丁补充的项目不对: {col}")
            # 第③条普通凭证应仍由程序判定（没被补丁碰到）
            # 非货币资金行本来就没有「现金流判定」，读出来是 NaN，先剔掉
            others = r2[r2["凭证号"].astype(str) == "记-003"]
            srcs = set(others["现金流判定"].astype(str)) - {"nan", ""}
            print(f"  记-003（未补丁）的判定来源: {srcs}")
            if srcs != {"桶+方向"}:
                FAIL.append(f"记-003 的判定来源不对: {srcs}")
            # 记-002（方向与桶不符）在第2轮补丁后应仍是待复核
            m2 = r2[r2["凭证号"].astype(str) == "记-002"]
            m2_src = set(m2["现金流判定"].astype(str)) - {"nan", ""}
            print(f"  记-002（方向与桶不符，未补丁）的判定来源: {m2_src}")
            if m2_src != {"待复核"}:
                FAIL.append(f"记-002 应仍是待复核: {m2_src}")

# ============================================================================
# ③ 业务分类补丁：改桶 → 现金流必须**由程序重算**（这是那条回边）
# ============================================================================
# 背景：流程是「序时账 →①切割成单元 →②模型给单元打分 →③AI 复核**单元** →
# ④写回具体凭证 →⑤程序按凭证编现金流」。复核结论是**写回的输入**，不是事后修正，
# 所以改完桶现金流必然跟着重算（实测样例 460 条现金流分录里 431 条由「桶+方向」
# 直接判定，桶一错项目就跟着错，而且候选唯一时程序不会报警）。
print("\n" + "=" * 70)
print("③ 单元链路（units → 复核 → run --unit → 写回 → 现金流）")
print("=" * 70)

if p1 and p1.get("ok"):
    base_item = p1["cashflow"]["amount_by_item"]
    print(f"  基线：记-002 = 日常费用 + 收现 → 待复核（方向与桶不符，无项目）")
    if "取得借款收到的现金" in base_item:
        FAIL.append("基线下不该出现「取得借款收到的现金」")

# ── ③a units：复核面就是单元，不产出文件 ──
u = cli("units", str(ledger))
if not u or not u.get("ok"):
    FAIL.append(f"units 失败: {(u or {}).get('error')}")
else:
    print(f"  units: 凭证 {u['vouchers']} 张 → 单元 {u['unit_count']} 个")
    if u["unit_count"] != 3:
        FAIL.append(f"预期 3 个单元，实得 {u['unit_count']}")
    for x in u["units"]:
        print(f"    {x['单元号']} {x['模型桶']:6} p={x['概率']} "
              f"覆盖{x['覆盖凭证数']}张  {x['单元键'][:56]}")
    # 找「收到运费退款」那个单元（记-002），按单元号改判
    unit_002 = next((x for x in u["units"] if "运费退款" in x["摘要"]), None)
    unit_001 = next((x for x in u["units"] if "投资款" in x["摘要"]), None)
    if unit_002 is None:
        FAIL.append("units 里没找到 记-002 对应的单元")
    else:
        # 单元键必须是「科目组合 :: 摘要」的规范文本（可跨月复用）
        if "::" not in unit_002["单元键"] or "银行存款[借]" not in unit_002["单元键"]:
            FAIL.append(f"单元键格式不对: {unit_002['单元键']}")

        # ── ③b 按单元号改判（AI 的主路径：一条命令，不写任何文件）──
        out5 = WORK / "第5轮"
        # 口径修正.json 会与已有文件**合并**（分两轮改判时不冲掉前一轮的结论），
        # 所以这里先清干净，保证断言确定。
        shutil.rmtree(out5, ignore_errors=True)
        p5 = cli("run", str(ledger), "--out", str(out5),
                 "--unit", f"{unit_002['单元号']}=借款筹资",
                 "--unit", "U999=单位往来",              # 单元号不存在
                 "--unit", f"{unit_002['单元号']}x=不存在桶")  # 桶名非法
        if not p5 or not p5.get("ok"):
            FAIL.append(f"第5轮（单元改判）run 失败: {(p5 or {}).get('error')}")
        else:
            up = p5["unit_patch"]
            print(f"  改判 采纳={up['applied']} 落到{up['changed_vouchers']}张  "
                  f"驳回={json.dumps(up['rejected'], ensure_ascii=False)}")
            print(f"       未知单元={up['unknown_units']}")
            if up["applied"] != 1:
                FAIL.append(f"预期采纳 1 个单元，实得 {up['applied']}")
            if up["unknown_units"] != ["U999"]:
                FAIL.append(f"预期未知单元 ['U999']，实得 {up['unknown_units']}")
            if len(up["rejected"]) != 1 or "不是 20 个业务桶之一" not in up["rejected"][0].get("原因", ""):
                FAIL.append(f"非法桶名未被驳回: {up['rejected']}")

            # 关键：现金流由程序按改后的桶**重算**
            it5 = p5["cashflow"]["amount_by_item"]
            sc5 = p5["cashflow"]["amount_by_section"]
            print(f"  改判后 section: {json.dumps(sc5, ensure_ascii=False)}")
            if "取得借款收到的现金" not in it5:
                FAIL.append(f"改判后现金流没重算，items={list(it5)}")
            elif abs(it5["取得借款收到的现金"] - 30000) > 0.01:
                FAIL.append(f"「取得借款收到的现金」金额不对: {it5['取得借款收到的现金']}")
            if sc5.get("筹资活动", 0) <= 0:
                FAIL.append(f"改判后应出现筹资活动流入，实得 {sc5}")

            x5 = out5 / "补丁样例_结果.xlsx"
            r5 = pd.read_excel(x5, sheet_name="业务分类结果", dtype=object)
            row5 = r5[(r5["凭证号"].astype(str) == "记-002") & r5["现金流量项目"].notna()]
            if len(row5) != 1:
                FAIL.append(f"记-002 现金流行数异常: {len(row5)}")
            else:
                rr = row5.iloc[0]
                print(f"  交付行：业务分类={rr['业务分类']}  分类判定={rr['分类判定']}  "
                      f"项目={rr['现金流量项目']}  判定={rr['现金流判定']}")
                if str(rr["业务分类"]) != "借款筹资":
                    FAIL.append(f"业务分类未改判: {rr['业务分类']}")
                if str(rr["分类判定"]) != "复核改判":
                    FAIL.append(f"分类判定应为「复核改判」，实得 {rr['分类判定']}")
                if str(rr["现金流量项目"]) != "取得借款收到的现金":
                    FAIL.append(f"现金流量项目未同步重算: {rr['现金流量项目']}")
                if str(rr["现金流判定"]) != "桶+方向":
                    FAIL.append(f"现金流判定应为「桶+方向」（程序算的），实得 {rr['现金流判定']}")

            # 可审计：新增列在，且原始序时账没被污染
            if "分类判定" not in r5.columns:
                FAIL.append("业务分类结果缺「分类判定」列")
            src5 = pd.read_excel(ledger, dtype=object)
            raw5 = pd.read_excel(x5, sheet_name="原始序时账", dtype=object)
            if list(raw5.columns) != list(src5.columns):
                FAIL.append(f"原始序时账被污染: {list(raw5.columns)} vs {list(src5.columns)}")

            # ── ③c 程序自己把口径修正落盘（AI 没写文件）──
            # 落盘的键必须是**单元键**而不是单元号：单元号只在本次文件里有意义，
            # 单元键才能跨月复用——那是这张表存在的理由。而且只记采纳的。
            pf = (p5.get("patch_files") or {}).get("口径修正")
            if not pf:
                FAIL.append("没落盘「口径修正.json」")
            else:
                saved = json.loads(Path(pf).read_text(encoding="utf-8-sig"))
                keys = list((saved.get("units") or {}))
                print(f"  留档 口径修正.json 键数 = {len(keys)}，首键 = {keys[0][:52] if keys else '(空)'}")
                if keys != [unit_002["单元键"]]:
                    FAIL.append(f"留档键应是单元键且只记采纳的: {keys}")
                if (saved.get("units") or {}).get(unit_002["单元键"]) != "借款筹资":
                    FAIL.append(f"留档的桶不对: {saved.get('units')}")

            # ── ③d 用「单元键原文」也能改判，且带 BOM 的文件也能读 ──
            # 单元键是跨月复用的形态；BOM 是因为 Windows 记事本/PowerShell
            # 写 JSON 默认带 BOM，以前用 encoding="utf-8" 读会直接 JSONDecodeError。
            up_file = WORK / "口径修正_bom.json"
            body = json.dumps({"units": {unit_002["单元键"]: "借款筹资"}},
                              ensure_ascii=False, indent=2).encode("utf-8")
            up_file.write_bytes(b"\xef\xbb\xbf" + body)
            p6 = cli("run", str(ledger), "--out", str(WORK / "第6轮"),
                     "--unit-patch", str(up_file))
            if not p6 or not p6.get("ok"):
                FAIL.append(f"带 BOM 的单元键修正表读不了: {(p6 or {}).get('error')}")
            elif (p6.get("unit_patch") or {}).get("applied") != 1:
                FAIL.append(f"带 BOM 的修正表未生效: {p6.get('unit_patch')}")
            else:
                print("  单元键原文 + UTF-8 BOM 的修正表都能正常用"
                      "（记事本默认就是带 BOM）")

        # ── ③e 一次交多条：单元号与单元键可以混用 ──
        # AI 的主路径就是「一条命令把结论全交完」，所以混用必须支持。
        out7 = WORK / "第7轮"
        shutil.rmtree(out7, ignore_errors=True)
        p7 = cli("run", str(ledger), "--out", str(out7),
                 "--unit", f"{unit_002['单元号']}=借款筹资",
                 "--unit", f"{unit_001['单元键']}=日常费用")
        if not p7 or not p7.get("ok"):
            FAIL.append(f"第7轮（单元号与单元键混用）失败: {(p7 or {}).get('error')}")
        elif (p7.get("unit_patch") or {}).get("applied") != 2:
            FAIL.append(f"预期采纳 2 个单元（混用两种键），实得 {p7.get('unit_patch')}")
        else:
            print(f"  单元号 + 单元键混用，一次交 2 条："
                  f"采纳 {p7['unit_patch']['applied']}，"
                  f"落到 {p7['unit_patch']['changed_vouchers']} 张凭证")

        # ── ③f units 是纯复核面：出参里不该有 output_files ──
        if "output_files" in u:
            FAIL.append("units 不该产生产出文件（出参里出现了 output_files）")
        else:
            print("  units 不产出任何文件（出参无 output_files）")

print("\n" + "=" * 70)
if FAIL:
    print(f"❌ {len(FAIL)} 项失败：")
    for f in FAIL:
        print("   -", f)
    sys.exit(1)
print("✅ 全部通过")
