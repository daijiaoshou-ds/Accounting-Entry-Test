# -*- coding: utf-8 -*-
"""
BGE 模型加载与下载 — V3.0

下载策略: ModelScope 优先（国内直连快）→ HuggingFace 回退 → 本地缓存兜底。
训练与推理统一走 resolve_model_dir()，永远先给 from_pretrained 本地路径，
避免运行时联网。
"""

from pathlib import Path
from typing import Optional

from summary_cleaner.v2.config import NN_MODEL_CACHE_DIR

# 可选模型（BAAI 智源出品，中文 Embedding 领域最强）
MODEL_CHOICES = {
    "BAAI/bge-large-zh-v1.5": {"hidden_size": 1024, "params": "326M"},
    "BAAI/bge-base-zh-v1.5": {"hidden_size": 768, "params": "109M"},
}


def get_model_cache_dir() -> Path:
    """nn/models/ 下载缓存目录（自动创建）。"""
    path = Path(NN_MODEL_CACHE_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _local_cache_dir(model_name: str) -> Path:
    """本地缓存目录: nn/models/{repo_id 的 / 替换为 __}。"""
    safe = model_name.replace("/", "__")
    return get_model_cache_dir() / safe


def _modelscope_cache_dir(model_name: str) -> Optional[Path]:
    """ModelScope 下载缓存: nn/models/models/{org}--{repo}/snapshots/{rev}。

    返回最新 revision 的快照目录（若存在）。
    """
    org, repo = model_name.split("/", 1)
    base = get_model_cache_dir() / "models" / f"{org}--{repo}" / "snapshots"
    if not base.exists():
        return None
    revisions = sorted(base.iterdir()) if base.is_dir() else []
    if not revisions:
        return None
    # 取最新的 revision（按 mtime）
    return max(revisions, key=lambda p: p.stat().st_mtime)


def is_model_complete(model_dir) -> bool:
    """判定模型下载完整: config.json + 任一权重文件存在。"""
    model_dir = Path(model_dir)
    if not model_dir.exists():
        return False
    if not (model_dir / "config.json").exists():
        return False
    for weight in ["model.safetensors", "pytorch_model.bin", "pytorch_model.bin.index.json"]:
        if (model_dir / weight).exists():
            return True
    # 某些仓库有多个分片 safetensors（pytorch_model-00001-of-00002.safetensors）
    if list(model_dir.glob("*.safetensors")):
        return True
    return False


def resolve_model_dir(model_name: str, download: bool = True) -> Path:
    """解析模型目录（本地缓存 → 下载），返回可直接给 from_pretrained 的本地路径。

    Args:
        model_name: 如 'BAAI/bge-large-zh-v1.5'
        download: True = 缓存缺失时自动下载；False = 离线模式，缺失即报错

    Returns:
        本地模型目录 Path

    Raises:
        FileNotFoundError: 下载失败或离线模式下缓存缺失
    """
    # ① 本地缓存（自定义目录 + ModelScope 目录）判完整
    for cand in (_local_cache_dir(model_name), _modelscope_cache_dir(model_name)):
        if cand is not None and is_model_complete(cand):
            print(f"[OK] 命中本地模型缓存: {cand}")
            return cand

    if not download:
        raise FileNotFoundError(
            f"模型未下载且处于离线模式: {model_name}（缓存: {_local_cache_dir(model_name)}）"
        )

    # org/repo 在此作用域定义（②的备用路径检查要用；旧代码在
    # _modelscope_cache_dir 内部定义，此处引用必 NameError）
    org, repo = model_name.split("/", 1) if "/" in model_name else ("", model_name)

    # ② ModelScope 优先
    try:
        from modelscope import snapshot_download
        print(f"[INF] 从 ModelScope 下载模型: {model_name}")
        local_path = snapshot_download(
            model_name, cache_dir=str(get_model_cache_dir()),
        )
        # snapshot_download 返回实际本地路径（可能带版本目录）
        model_dir = Path(local_path)
        if not is_model_complete(model_dir):
            # 尝试 org/repo 结构
            alt = get_model_cache_dir() / org / repo
            if is_model_complete(alt):
                model_dir = alt
        if is_model_complete(model_dir):
            print(f"[OK] 模型下载完成: {model_dir}")
            return model_dir
        raise FileNotFoundError(f"ModelScope 下载不完整: {model_dir}")
    except FileNotFoundError:
        raise
    except Exception as e:
        print(f"[WARN] ModelScope 下载失败: {e}")

    # ③ HuggingFace 回退
    try:
        from huggingface_hub import snapshot_download as hf_snapshot
        print(f"[INF] 从 HuggingFace 下载模型: {model_name}")
        local_path = hf_snapshot(
            repo_id=model_name, cache_dir=str(get_model_cache_dir()),
        )
        if is_model_complete(Path(local_path)):
            print(f"[OK] 模型下载完成: {local_path}")
            return Path(local_path)
        raise FileNotFoundError(f"HF 下载不完整: {local_path}")
    except FileNotFoundError:
        raise
    except Exception as e:
        raise FileNotFoundError(
            f"模型下载失败（ModelScope 与 HuggingFace 均失败）: {model_name}\n"
            f"  错误: {e}\n"
            f"  可手动下载后放到: {get_model_cache_dir()}"
        ) from e


def load_encoder_tokenizer(model_dir: Path, local_files_only: bool = True):
    """加载 BGE 编码器 + tokenizer（仅本地路径，不联网）。

    Returns:
        (encoder, tokenizer)
    """
    from transformers import AutoModel, AutoTokenizer

    model_dir = Path(model_dir)
    if not is_model_complete(model_dir):
        raise FileNotFoundError(f"模型目录不完整: {model_dir}")

    tokenizer = AutoTokenizer.from_pretrained(
        str(model_dir), local_files_only=local_files_only,
    )
    encoder = AutoModel.from_pretrained(
        str(model_dir), local_files_only=local_files_only,
    )
    return encoder, tokenizer
