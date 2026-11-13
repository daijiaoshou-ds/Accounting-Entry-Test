# -*- coding: utf-8 -*-
"""Job：一个任务 = 磁盘上一个目录 + 一份 state.json。

run/next/submit 的语义：

    advance()   从第一个"没做完"的阶段往后跑，遇到 ai 阶段就停（返回 next_action）
    submit()    把 decision 收进去，然后 advance()
    status()    当前状态（给前端轮询）

**幂等**：同一份输入 + 同一批 decision ⇒ 同一份产物。阶段靠哈希判断要不要重跑，
所以 `submit` 之后只有下游会重算——清洗那一步不会因为改了一条现金流补丁而重跑。
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import JOBS_DIR, SCHEMA_VERSION
from .feature import Feature
from .stage import Ctx, ReviewRequest, Stage, StageResult

PROGRAM_DONE = ("done", "skipped")


# ============================================================================
# 小工具
# ============================================================================

def _sha(*parts: Any) -> str:
    raw = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _write_json(path: Path, obj: Any) -> None:
    """**原子写**：先写 .tmp 再 os.replace。

    不这么做的话，后台在写 state.json 的同时前端正好轮询 `/next`，会读到被截断
    的空文件（`JSONDecodeError` → 400）——第一次跑接口测试就撞上了。

    Windows 的坑：`os.replace` 在目标文件正被读取时会抛 `WinError 5 拒绝访问`
    （Python 打开文件不带 FILE_SHARE_DELETE）。读的人只握几微秒，重试几次即可。
    """
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str),
                   encoding="utf-8")
    for attempt in range(25):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 24:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.02)


def read_json(path: Path) -> Any:
    """读 state / decision：撞上"正好在替换"就重试，别把 400 抛给用户。"""
    last: Exception | None = None
    for _ in range(25):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, PermissionError) as exc:
            last = exc
            time.sleep(0.02)
    raise last  # type: ignore[misc]


def _new_id() -> str:
    return f"{time.strftime('%Y%m%d_%H%M%S')}_{random.randrange(16 ** 6):06x}"


# ============================================================================
# Job
# ============================================================================

class Job:
    def __init__(self, path: Path, state: Dict[str, Any]):
        self.path = path
        self.state = state
        self._feature: Optional[Feature] = None
        self._journal = None

    # ── 基本属性 ──
    @property
    def id(self) -> str:
        return self.state["job_id"]

    @property
    def dir(self) -> Path:
        return self.path

    @property
    def status(self) -> str:
        return self.state.get("status", "created")

    @property
    def feature(self) -> Feature:
        if self._feature is None:
            from features import get as get_feature
            self._feature = get_feature(self.state["feature"])
        return self._feature

    def stage(self, stage_id: str) -> Stage:
        return self.feature.stage(stage_id)

    @property
    def source(self) -> Path:
        return self.path / self.state["input"]["file"]

    # ── 建立 / 打开 ──
    @classmethod
    def create(cls, feature_id: str, source: str | Path, *,
               options: Optional[Dict[str, Any]] = None,
               root: Optional[str | Path] = None,
               job_id: Optional[str] = None) -> "Job":
        from features import get as get_feature

        feature = get_feature(feature_id)
        src = Path(source)
        if not src.exists():
            raise FileNotFoundError(f"文件不存在：{src}")

        jid = job_id or _new_id()
        path = Path(root or JOBS_DIR) / jid
        path.mkdir(parents=True, exist_ok=False)
        (path / "source").mkdir()
        (path / "out").mkdir()
        (path / "reviews").mkdir()
        (path / "decisions").mkdir()

        target = path / "source" / src.name
        shutil.copy2(src, target)

        state = {
            "job_id": jid,
            "schema_version": SCHEMA_VERSION,
            "feature": feature.id,
            # 冗余存一份中文名：列任务清单时不用为了一个名字把 torch 拉起来
            "feature_name": feature.name,
            "created_at": _now(),
            "updated_at": _now(),
            "input": {"file": f"source/{src.name}", "filename": src.name,
                      "sha256": _sha_file(target), "bytes": target.stat().st_size},
            "options": dict(options or {}),
            "stages": {},
            "status": "created",
            "error": None,
        }
        job = cls(path, state)
        job.save()
        return job

    @classmethod
    def open(cls, ref: str | Path) -> "Job":
        """按 job_id 或目录打开。"""
        p = Path(ref)
        if not (p / "state.json").exists():
            p = Path(JOBS_DIR) / str(ref)
        state_path = p / "state.json"
        if not state_path.exists():
            raise FileNotFoundError(f"找不到任务：{ref}（既不是目录也不是 job_id）")
        return cls(p, read_json(state_path))

    def save(self) -> None:
        self.state["updated_at"] = _now()
        _write_json(self.path / "state.json", self.state)

    # ── 阶段状态 ──
    def stage_state(self, stage_id: str) -> Dict[str, Any]:
        return (self.state.get("stages") or {}).get(stage_id) or {}

    def stage_facts(self, stage_id: str) -> Dict[str, Any]:
        return self.stage_state(stage_id).get("facts") or {}

    # 这些选项只影响"复核面"（取多少条）或者只是归属标记，不影响业务产物 →
    # 不进哈希，否则改一下 max_review、换一个任务建同一份账，都会把清洗重跑一遍。
    _HASH_IGNORED_OPTIONS = ("max_review", "task")

    def _hashed_options(self) -> Dict[str, Any]:
        return {k: v for k, v in (self.state.get("options") or {}).items()
                if k not in self._HASH_IGNORED_OPTIONS}

    def stage_hash(self, stage_id: str) -> str:
        st = self.stage(stage_id)
        if st.actor != "program":
            return self._upstream(st)
        parts = [self.state["input"]["sha256"], self._hashed_options(),
                 [self._upstream_hash(d) for d in st.depends_on]]
        if st.hash_extra is not None:
            # 只在声明了才追加：否则会一次性作废所有历史任务的哈希（全部重跑）
            parts.append(st.hash_extra(Ctx(self, st, lambda *_a, **_k: None)))
        return _sha(*parts)

    def _upstream(self, stage_id: str) -> str:
        st = self.stage(stage_id)
        vals = []
        for dep in st.depends_on:
            d = self.stage(dep)
            vals.append(self.decision_hash(dep) if d.actor == "ai"
                        else self.stage_state(dep).get("hash") or "missing")
        return _sha(vals)

    def _upstream_hash(self, dep: str) -> str:
        d = self.stage(dep)
        if d.actor == "ai":
            return self.decision_hash(dep)
        return self.stage_state(dep).get("hash") or "missing"

    # ── 决策 ──
    def _decision_file(self, stage_id: str) -> Path:
        return self.path / "decisions" / f"{stage_id}.json"

    def decision_doc(self, stage_id: str) -> Dict[str, Any]:
        p = self._decision_file(stage_id)
        if not p.exists():
            return {}
        return read_json(p)

    def decision_hash(self, stage_id: str) -> str:
        p = self._decision_file(stage_id)
        if not p.exists():
            return ""
        return _sha_file(p)

    def decisions(self, stage_id: str) -> Dict[str, Any]:
        """{key: value} —— 只取值，note/by 不进业务代码。"""
        doc = self.decision_doc(stage_id)
        return {k: v.get("value") for k, v in (doc.get("items") or {}).items()
                if isinstance(v, dict) and "value" in v}

    # ── 账 ──
    @property
    def journal(self):
        if self._journal is None:
            from journal_io import Journal, JournalSchema
            import pandas as pd

            d = self.path / "out" / "read"
            raw, sch = d / "raw.parquet", d / "schema.json"
            if not raw.exists():
                raise FileNotFoundError("read 阶段还没跑：先 journal run/resume")
            schema = JournalSchema(**json.loads(sch.read_text(encoding="utf-8")))
            self._journal = Journal(pd.read_parquet(raw), schema,
                                    source=self.state["input"]["filename"])
        return self._journal

    def drop_journal_cache(self) -> None:
        """作废内存里的 Journal（产物被替换过时用，比如从上游任务搬了一份）。"""
        self._journal = None

    def save_journal(self, journal) -> None:
        """read 阶段的产物：表 + 映射（重跑时不用再解析原件）。"""
        from .prepare import write_journal

        write_journal(journal, self.path / "out" / "read")
        self._journal = journal

    # ── 执行 ──
    def advance(self, log=None) -> Dict[str, Any]:
        """跑到下一个 AI 阶段或跑完。返回 next_action。"""
        log = log or (lambda *_a, **_k: None)
        self.state["status"] = "running"
        self.save()

        for st in self.feature.stages:
            cur = self.stage_state(st.id)
            upstream = self._upstream(st.id)

            # 这条链路已经被上游任务做过了（如从清洗任务起跑现金流）→ 整段跳过。
            # 跳过也要落一条状态：前端时间线要显示"为什么这步没跑"。
            if not st.active(Ctx(self, st, log)):
                if cur.get("status") != "skipped":
                    note = st.skip_note or "本任务不需要这一步"
                    self.state.setdefault("stages", {})[st.id] = {
                        "status": "skipped", "title": st.title,
                        "note": note, "finished_at": _now()}
                    self.save()
                    log(f"  ↷ {st.title}（跳过：{note}）")
                continue

            if st.actor == "program":
                want = self.stage_hash(st.id)
                if cur.get("status") in PROGRAM_DONE and cur.get("hash") == want:
                    log(f"  ↷ {st.title}（未变，跳过）")
                    continue
                t0 = time.perf_counter()
                log(f"▶ {st.title}")
                ctx = Ctx(self, st, log)
                try:
                    res = st.run(ctx) or StageResult()
                except Exception as exc:  # noqa: BLE001
                    self.state["status"] = "failed"
                    self.state["error"] = f"{st.id}: {type(exc).__name__}: {exc}"
                    self.state["stages"][st.id] = {
                        **cur, "status": "failed", "error": self.state["error"],
                        "finished_at": _now()}
                    self.save()
                    raise
                self.state.setdefault("stages", {})[st.id] = {
                    "status": "done",
                    "hash": want,
                    "title": st.title,
                    "elapsed": round(time.perf_counter() - t0, 2),
                    "facts": res.facts,
                    "artifacts": res.artifacts,
                    "finished_at": _now(),
                }
                log(f"  ✓ {st.title}（{self.state['stages'][st.id]['elapsed']}s）")
                self.save()
                continue

            # ── ai 阶段 ──
            dec_hash = self.decision_hash(st.id)
            if (cur.get("status") == "done"
                    and cur.get("upstream") == upstream
                    and cur.get("hash") == dec_hash):
                log(f"  ↷ {st.title}（已交结论）")
                continue

            ctx = Ctx(self, st, log)
            try:
                req: ReviewRequest = st.review(ctx)
            except Exception as exc:  # noqa: BLE001
                # 程序阶段炸了会落 failed；AI 阶段以前没人接住，进程退出后 state
                # 还停在 running——页面上就是"永远正在跑"，点几次开始处理就多几个
                # 假运行。这里必须和程序阶段一样落 failed。
                self.state["status"] = "failed"
                self.state["error"] = f"{st.id}: {type(exc).__name__}: {exc}"
                self.state.setdefault("stages", {})[st.id] = {
                    **cur, "status": "failed", "title": st.title,
                    "error": self.state["error"], "finished_at": _now()}
                self.save()
                raise
            if not req or not req.items:
                self.state.setdefault("stages", {})[st.id] = {
                    "status": "done", "hash": dec_hash, "upstream": upstream,
                    "title": st.title, "facts": {"count": 0},
                    "note": "无需复核", "finished_at": _now()}
                self.save()
                log(f"  ✓ {st.title}（无需复核）")
                continue

            payload = req.payload(st.id)
            _write_json(self.path / "reviews" / f"{st.id}.request.json", payload)
            self.state.setdefault("stages", {})[st.id] = {
                "status": "awaiting", "upstream": upstream, "title": st.title,
                "facts": {"count": len(req.items), "truncated": req.truncated},
                "request": f"reviews/{st.id}.request.json",
                "started_at": _now()}
            self.state["status"] = "awaiting_review"
            self.save()
            log(f"⏸ {st.title}：{len(req.items)} 条待确认"
                + ("（已截断）" if req.truncated else ""))
            return self.next_action()

        self.state["status"] = "done"
        self.save()
        return self.next_action()

    def submit(self, doc: Dict[str, Any], log=None,
               advance: bool = True) -> Dict[str, Any]:
        """收一批 decision（或 {} 表示"这批我确认无误"），然后继续往下跑。

        Args:
            advance: False 表示**只落盘不往下跑**。给 HTTP/前端用——校验结论
                （采纳了几条、驳回几条）要同步返回，而往下跑可能要几十秒，
                得丢给后台线程。CLI 用不到这个参数。
        """
        log = log or (lambda *_a, **_k: None)
        sid = str(doc.get("stage") or "").strip()
        if not sid:
            raise ValueError("decision 文件缺 \"stage\" 字段")
        st = self.stage(sid)
        if st.actor != "ai":
            raise ValueError(f"阶段 {sid} 不是 AI 阶段，不接受 decision")

        raw = doc.get("decisions") or []
        if not isinstance(raw, list):
            raise ValueError("\"decisions\" 必须是数组")

        # 合法取值范围 + 当前待办里的 key（防止改到不存在的行/凭证）
        req_path = self.path / "reviews" / f"{sid}.request.json"
        req = (json.loads(req_path.read_text(encoding="utf-8"))
               if req_path.exists() else {})
        enum = set(req.get("value_enum") or [])
        keys = {str(it["key"]) for it in (req.get("items") or [])
                if it.get("key")}

        existing = (self.decision_doc(sid).get("items") or {})
        rejected: List[Dict[str, Any]] = []
        accepted = 0
        for d in raw:
            key = str(d.get("key") or "").strip()
            value = str(d.get("value") or "").strip()
            if not key:
                rejected.append({"key": key, "reason": "缺 key"})
                continue
            if enum and value not in enum:
                rejected.append({"key": key, "reason": f"取值不合法：{value or '(空)'}"})
                continue
            if keys and key not in keys:
                rejected.append({"key": key,
                                 "reason": "不在本轮待办里（key 写错，或已在上一轮处理过）"})
                continue
            existing[key] = {"value": value, "note": str(d.get("note") or ""),
                             "by": str(d.get("by") or "ai"), "at": _now()}
            accepted += 1

        # 空结论 = "这批没有要改的"：把它当成**弃权**，别让同一条待办反复拦路。
        # 语义写在 decisions 文件里（waived: true），阶段直接结束；
        # 上游变了（重跑清洗）才会重新要求复核。
        waive = bool(doc.get("waive")) or not raw

        # 归档旧结论再写新的（可审计：谁在什么时候改了什么）
        if self._decision_file(sid).exists():
            arc = self.path / "decisions" / "_archive"
            arc.mkdir(exist_ok=True)
            self._decision_file(sid).replace(
                arc / f"{sid}_{time.strftime('%Y%m%d_%H%M%S')}.json")

        _write_json(self._decision_file(sid), {
            "schema_version": SCHEMA_VERSION, "stage": sid,
            "submitted_at": _now(), "accepted": accepted, "rejected": rejected,
            "waived": bool(waive),
            "items": existing,
        })

        if waive:
            self.state.setdefault("stages", {})[sid] = {
                "status": "done", "upstream": self._upstream(sid),
                "hash": self.decision_hash(sid), "title": st.title,
                "facts": {"count": len(existing)},
                "note": "本批复核无改动（弃权）", "finished_at": _now()}
            self.save()
            log(f"✔ {st.title}：这批没有要改的（{len(existing)} 条已有结论）")
        else:
            log(f"✔ {st.title}：采纳 {accepted} 条"
                + (f"，驳回 {len(rejected)} 条" if rejected else ""))

        # 回执要跟着往下跑的结果一起返回：交完立刻能看到"哪几条被驳回"，
        # 否则得再去翻 decisions/<stage>.json 才知道。
        receipt = {"ok": True, "stage": sid, "accepted": accepted,
                   "rejected": rejected, "waived": bool(waive),
                   "by": str(doc.get("by") or "ai")}
        if not advance:
            return {**receipt, "status": self.state.get("status")}
        return {**self.advance(log=log), **receipt}

    # ── 中止 ──
    def abort(self, reason: str = "已中止") -> Dict[str, Any]:
        """中止（页面上的「■ 中止」）。

        只改状态，不杀进程：真正的 python 进程由调用方（插件 runner）杀掉，
        停在这一侧的只是"这本账不再往下算"。停在复核位的任务没有进程，也能中止。
        之后再 `resume` 会从没做完的阶段继续——已完成的阶段按哈希跳过，不重算。
        """
        if self.state.get("status") == "done":
            return {"ok": False, "job_id": self.id, "status": "done",
                    "error": "任务已经跑完了，不用中止"}
        self.state["status"] = "aborted"
        self.state["error"] = f"{reason}（{_now()}）"
        for cur in (self.state.get("stages") or {}).values():
            if isinstance(cur, dict) and cur.get("status") in ("awaiting", "running"):
                cur["status"] = "aborted"
                cur["note"] = "已中止"
        self.save()
        return {"ok": True, "job_id": self.id, "status": "aborted",
                "error": self.state["error"]}

    # ── 报告 ──
    def request_doc(self, stage_id: str) -> Dict[str, Any]:
        """某个 AI 阶段落盘的待办原文（`reviews/<stage>.request.json`）。

        没有就返回 {}——阶段还没跑到、或者已经被提交过（待办文件仍在，
        只是 items 里已扣掉有结论的）。
        """
        p = self.path / "reviews" / f"{stage_id}.request.json"
        if not p.exists():
            return {}
        try:
            return read_json(p)
        except (OSError, ValueError):
            return {}

    def next_action(self, with_items: bool = True,
                    item_limit: int = 30) -> Dict[str, Any]:
        """给 AI / 前端 / 人看的"下一步"。只有这一个入口，谁都不用猜。

        Args:
            with_items: 把待办条目**内联**在返回值里。CLI/agent 用得到——它不该
                为了看 35 条待办再跑一次 `cat reviews/*.json`。
            item_limit: 内联多少条。剩下的用 `journal request --offset` 分页取；
                大账一次可能上千条，塞进一个 stdout 会把上下文撑爆。
        """
        base = {
            "ok": self.state.get("status") != "failed",
            "job_id": self.id,
            "feature": self.state["feature"],
            "feature_name": self.feature.name,
            "status": self.state.get("status"),
            "dir": str(self.path),
        }
        if self.state.get("status") == "failed":
            return {**base, "error": self.state.get("error"),
                    "next": {"actor": "operator", "stage": None,
                             "action": "看 error 修好后 `journal resume <job_id>`"}}

        if self.state.get("status") == "aborted":
            return {**base, "error": self.state.get("error"),
                    "next": {"actor": "operator", "stage": None,
                             "action": "已中止；`journal resume <job_id>` 从断点继续"}}

        # 进程正在算（HTTP 里是后台线程，CLI 是当前进程）：别让调用方以为该它动手。
        # 正常情况下 advance() 结束一定会改成 awaiting_review/done，所以这里看到
        # running 就是"还没算完"（或者上个进程崩了，那就 resume）。
        if self.state.get("status") == "running":
            return {**base, "next": {"actor": "program", "stage": None,
                                     "running": True,
                                     "action": "正在运行…（等它跑完，或 resume 重试）"}}

        for st in self.feature.stages:
            cur = self.stage_state(st.id)
            if st.actor == "ai" and cur.get("status") == "awaiting":
                doc = self.request_doc(st.id)
                all_items = doc.get("items") or []
                shown = all_items[:item_limit] if with_items else []
                return {**base, "stage": st.id, "next": {
                    "actor": "ai",
                    "stage": st.id,
                    "title": st.title,
                    "action": st.help or f"复核并提交《{st.title}》的结论",
                    "count": len(doc.get("items") or []),
                    "truncated": bool(doc.get("truncated")),
                    "items_returned": len(shown),
                    "items": shown if with_items else None,
                    "more": (f"journal request {self.id} "
                             f"--stage {st.id} --offset {len(shown)}"
                             if with_items and len(doc.get("items") or []) > len(shown)
                             else None),
                    "read": cur.get("request"),
                    "submit": f"journal submit {self.id} --file decisions.json",
                    "decision_format": {
                        "schema_version": SCHEMA_VERSION,
                        "stage": st.id,
                        "decisions": [{"key": "<request 里的 key>",
                                       "value": "<value_enum 里的一个>",
                                       "note": "依据（可选）"}],
                    },
                }}

        done = self.state.get("status") == "done"
        return {**base, "next": {
            "actor": "none" if done else "program",
            "stage": None,
            "action": "已完成，产物在 outputs" if done else "继续 `journal resume <job_id>`",
            "outputs": self.outputs() if done else {},
        }}

    def outputs(self) -> Dict[str, str]:
        """可下载的产物（按 feature.exports 声明的 key）。"""
        out: Dict[str, str] = {}
        for spec in self.feature.exports:
            st = self.stage_state(spec.get("stage") or "")
            for key, path in (st.get("artifacts") or {}).items():
                if key == spec.get("key"):
                    out[spec.get("key")] = path
        return out

    def summary(self) -> Dict[str, Any]:
        """状态摘要（前端头部、CLI status）。"""
        return {
            "job_id": self.id,
            "feature": self.state["feature"],
            "feature_name": self.feature.name,
            "status": self.state.get("status"),
            "created_at": self.state.get("created_at"),
            "updated_at": self.state.get("updated_at"),
            "input": {k: v for k, v in self.state["input"].items()
                      if k in ("filename", "rows", "sha256")},
            "options": self.state.get("options") or {},
            "stages": [
                # 没跑过的阶段也要有一行（前端画时间线要用），状态给 pending
                {"id": s.id, "title": s.title, "actor": s.actor,
                 "status": self.stage_state(s.id).get("status", "pending"),
                 **{k: v for k, v in self.stage_state(s.id).items()
                    if k in ("elapsed", "facts", "note", "error", "finished_at")}}
                for s in self.feature.stages
            ],
            "outputs": self.outputs(),
            "error": self.state.get("error"),
        }
