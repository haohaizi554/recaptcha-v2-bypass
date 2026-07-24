"""
方案 3: AI 图像识别 (YOLOv8 微调分类 + 分割 + CLIP 三引擎, 免费) — ETH 方案
=====================================================
核心架构:
  三引擎策略 (参考 ETH Zurich "Breaking reCAPTCHAv2", 100% 通过率):
    - YOLOv8-cls 微调模型: 3x3 (9宫格) 挑战, 12 个 reCAPTCHA 专用类别
      → 每个 tile 独立分类, 概率 > 0.2 即选中 (ETH 论文参数)
    - YOLOv8-seg 基础模型: 4x4 (16宫格) 挑战, COCO 类别分割
      → 分割掩码 → 网格重叠检测 (conf=0.5, imgsz=320, 任意像素重叠)
    - CLIP 零样本: 非 12 类别回退 (如 "mountain", "stairs" 等长尾类别)

参考: ETH Zurich "Breaking reCAPTCHAv2" (2024) — arXiv:2409.08831
模型: https://github.com/aplesner/Breaking-reCAPTCHAv2
"""

# CLIP 离线模式: 必须在 import transformers/huggingface_hub 之前设置
# 否则 huggingface_hub 在 import 时读取环境变量, 后续设置无效
# 模型已缓存在 ~/.cache/huggingface/hub/, 无需在线检查更新
import os
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import asyncio
import logging
import re
import tempfile
from typing import Optional

import config
from core.base_runtime import BaseBypassRuntime

logger = logging.getLogger(__name__)

# ============================================================
# 依赖检测 (延迟导入)
# ============================================================
_has_pillow = False
try:
    from PIL import Image
    _has_pillow = True
except ImportError:
    pass


# ============================================================
# reCAPTCHA 常见挑战类别 → 多提示文本映射
# CLIP 对具体描述比对泛化描述更敏感, 每个类别提供多条候选文本
# ============================================================
_CATEGORY_PROMPTS: dict[str, list[str]] = {
    "traffic light": [
        "a photo of a traffic light",
        "a photo of traffic lights",
        "a photo of a street light signal",
        "a photo of a stoplight",
    ],
    "traffic lights": [
        "a photo of traffic lights",
        "a photo of a traffic light",
        "a photo of a street light signal",
        "a photo of a stoplight",
    ],
    "crosswalk": [
        "a photo of a crosswalk",
        "a photo of a pedestrian crossing",
        "a photo of a zebra crossing",
        "a photo of a crosswalk on a road",
    ],
    "crosswalks": [
        "a photo of a crosswalk",
        "a photo of a pedestrian crossing",
        "a photo of a zebra crossing",
        "a photo of white painted lines on a road",
        "a photo of a crosswalk on a street",
    ],
    "bus": [
        "a photo of a bus",
        "a photo of a city bus",
        "a photo of a transit bus",
        "a photo of a school bus",
        "a photo of a coach bus",
    ],
    "buses": [
        "a photo of a bus",
        "a photo of a city bus",
        "a photo of a transit bus",
        "a photo of a school bus",
        "a photo of a coach bus",
    ],
    "car": [
        "a photo of a car",
        "a photo of an automobile",
        "a photo of a vehicle",
        "a photo of a sedan",
    ],
    "cars": [
        "a photo of a car",
        "a photo of an automobile",
        "a photo of a vehicle",
        "a photo of a sedan",
    ],
    "fire hydrant": [
        "a photo of a fire hydrant",
        "a photo of a red fire hydrant",
        "a photo of a fire plug",
    ],
    "fire hydrants": [
        "a photo of a fire hydrant",
        "a photo of a red fire hydrant",
        "a photo of a fire plug",
    ],
    "bicycle": [
        "a photo of a bicycle",
        "a photo of a bike",
        "a photo of a cycle",
    ],
    "bicycles": [
        "a photo of a bicycle",
        "a photo of a bike",
        "a photo of a cycle",
    ],
    "motorcycle": [
        "a photo of a motorcycle",
        "a photo of a motorbike",
        "a photo of a motor scooter",
    ],
    "motorcycles": [
        "a photo of a motorcycle",
        "a photo of a motorbike",
        "a photo of a motor scooter",
    ],
    "boat": [
        "a photo of a boat",
        "a photo of a ship",
        "a photo of a watercraft",
    ],
    "boats": [
        "a photo of a boat",
        "a photo of a ship",
        "a photo of a watercraft",
    ],
    "parking meter": [
        "a photo of a parking meter",
        "a photo of a parking machine",
    ],
    "parking meters": [
        "a photo of a parking meter",
        "a photo of a parking machine",
    ],
    "stair": [
        "a photo of stairs",
        "a photo of a staircase",
        "a photo of steps",
    ],
    "stairs": [
        "a photo of stairs",
        "a photo of a staircase",
        "a photo of steps",
    ],
    "street sign": [
        "a photo of a street sign",
        "a photo of a road sign",
        "a photo of a traffic sign",
    ],
    "bridge": [
        "a photo of a bridge",
        "a photo of an overpass",
    ],
    "bridges": [
        "a photo of a bridge",
        "a photo of an overpass",
    ],
    "taxi": [
        "a photo of a taxi",
        "a photo of a cab",
        "a photo of a taxicab",
        "a photo of a yellow taxi",
    ],
    "taxis": [
        "a photo of a taxi",
        "a photo of a cab",
        "a photo of a taxicab",
        "a photo of a yellow taxi",
    ],
    "tractor": [
        "a photo of a tractor",
        "a photo of a farm tractor",
        "a photo of an agricultural vehicle",
        "a photo of a farming machine",
    ],
    "tractors": [
        "a photo of a tractor",
        "a photo of a farm tractor",
        "a photo of an agricultural vehicle",
        "a photo of a farming machine",
    ],
    "mountain": [
        "a photo of a mountain",
        "a photo of mountains",
        "a photo of a mountain range",
    ],
    "mountains": [
        "a photo of a mountain",
        "a photo of mountains",
        "a photo of a mountain range",
    ],
    "tree": [
        "a photo of a tree",
        "a photo of trees",
    ],
    "trees": [
        "a photo of a tree",
        "a photo of trees",
    ],
    "chimney": [
        "a photo of a chimney",
        "a photo of a smokestack",
    ],
    "chimneys": [
        "a photo of a chimney",
        "a photo of a smokestack",
    ],
    "pole": [
        "a photo of a utility pole",
        "a photo of a telephone pole",
        "a photo of a lamp post",
    ],
    "hydrant": [
        "a photo of a fire hydrant",
        "a photo of a red fire hydrant",
        "a photo of a fire plug",
    ],
    "hydrants": [
        "a photo of a fire hydrant",
        "a photo of a red fire hydrant",
        "a photo of a fire plug",
    ],
}

# 通用负样本 (不匹配任何特定类别)
_GENERIC_NEGATIVES = [
    "a photo of empty space",
    "a photo of a blank surface",
    "a photo of nothing in particular",
]


# ============================================================
# reCAPTCHA 13 专用类别 (ETH 微调分类模型的实际类别)
# 模型 names: {0:'Bicycle', 1:'Bridge', 2:'Bus', 3:'Car', 4:'Chimney',
#   5:'Crosswalk', 6:'Hydrant', 7:'Motorcycle', 8:'Mountain', 9:'Other',
#   10:'Palm', 11:'Stairs', 12:'Traffic Light'}
# ============================================================
_RECAPTCHA_CLASSES = [
    "bicycle", "bridge", "bus", "car", "chimney", "crosswalk",
    "hydrant", "motorcycle", "mountain", "other", "palm", "stairs",
    "traffic",
]

# reCAPTCHA 提示文本 → 13 类索引的映射 (支持模糊匹配)
_RECAPTCHA_PROMPT_TO_CLASS = {
    "bicycle": 0, "bicycles": 0, "bike": 0, "bikes": 0,
    "bridge": 1, "bridges": 1,
    "bus": 2, "buses": 2,
    "car": 3, "cars": 3,
    "chimney": 4, "chimneys": 4,
    "crosswalk": 5, "crosswalks": 5, "pedestrian crossing": 5,
    "hydrant": 6, "fire hydrant": 6, "fire hydrants": 6, "hydrants": 6,
    "motorcycle": 7, "motorcycles": 7, "motorbike": 7, "motorbikes": 7,
    "mountain": 8, "mountains": 8,
    "palm": 10, "palms": 10, "palm tree": 10, "palm trees": 10,
    "stairs": 11, "stair": 11, "staircase": 11, "step": 11, "steps": 11,
    "traffic": 12, "traffic light": 12, "traffic lights": 12,
    "stoplight": 12, "stoplights": 12,
}


def _get_recaptcha_class_index(target: str) -> int | None:
    """将 reCAPTCHA 提示文本映射到 12 类索引, 无映射返回 None"""
    target_lower = target.lower().strip()
    # 精确匹配
    if target_lower in _RECAPTCHA_PROMPT_TO_CLASS:
        return _RECAPTCHA_PROMPT_TO_CLASS[target_lower]
    # 模糊匹配 (目标长度 >= 3, 避免 "a" 误匹配)
    if len(target_lower) >= 3:
        for key, cls_idx in _RECAPTCHA_PROMPT_TO_CLASS.items():
            if key in target_lower or target_lower in key:
                return cls_idx
    return None


# ============================================================
# COCO → reCAPTCHA 类别映射 (YOLOv8-seg 基础模型, 仅用于 4x4 分割)
# 参考: ETH Zurich MAPPING, 已根据实际模型类别索引修正
# COCO ID → reCAPTCHA class index (基于实际模型 names)
# ============================================================
_COCO_TO_RECAPTCHA = {1: 0, 5: 2, 2: 3, 10: 6, 3: 7, 9: 12}
# 反向映射: reCAPTCHA class index → COCO ID
_RECAPTCHA_TO_COCO: dict[int, int] = {v: k for k, v in _COCO_TO_RECAPTCHA.items()}


def _get_coco_class_id(target: str) -> int | None:
    """将 reCAPTCHA 提示文本映射到 COCO 类别 ID (仅用于 4x4 分割模型)"""
    cls_idx = _get_recaptcha_class_index(target)
    if cls_idx is not None:
        return _RECAPTCHA_TO_COCO.get(cls_idx)
    return None


def _build_prompts(target: str) -> tuple[list[str], list[str]]:
    """根据目标文本构建正/负样本提示列表"""
    target_lower = target.lower().strip()

    # 精确匹配
    if target_lower in _CATEGORY_PROMPTS:
        positives = _CATEGORY_PROMPTS[target_lower]
    else:
        # 模糊匹配
        matched = False
        for key, prompts in _CATEGORY_PROMPTS.items():
            if key in target_lower or target_lower in key:
                positives = prompts
                matched = True
                break
        if not matched:
            # 通用回退: 用原始文本构造
            singular = target_lower.rstrip("s")
            positives = [
                f"a photo of a {target_lower}",
                f"a photo of {target_lower}",
                f"a photo of a {singular}",
            ]

    return positives, _GENERIC_NEGATIVES


# ============================================================
# CLIP 分类器 (单例)
# ============================================================
class _CLIPClassifier:
    """
    CLIP 零样本图像分类器 (单例, 优化版)
    
    优化点:
    - 批量推理: 所有 tile + 所有文本提示一次前向传播
    - GPU 自动检测: CUDA > MPS > CPU
    - 异步加载: 线程池加载模型, 不阻塞事件循环
    - 多提示集成: 每个类别多条文本取最高分
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        self._model = None
        self._processor = None
        self._device = "cpu"
        self._loaded = False
        self._initialized = True

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    async def load_async(self) -> bool:
        """异步加载模型 (线程池, 不阻塞事件循环)"""
        if self._loaded:
            return True

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._load_sync)

    def _load_sync(self) -> bool:
        """
        同步加载模型 (在线程池中执行)

        离线模式优化:
          - 模型首次下载后已缓存在 ~/.cache/huggingface/hub/
          - 设置 HF_HUB_OFFLINE=1 避免在线检查更新 (防止 WinError 10054 网络中断)
          - 如果离线模式失败, 回退到在线模式重试 (首次下载场景)
        """
        try:
            import torch
            from transformers import CLIPProcessor, CLIPModel

            # GPU 自动检测
            if torch.cuda.is_available():
                self._device = "cuda"
                logger.info("[Image] 检测到 CUDA GPU, 使用 GPU 加速")
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                self._device = "mps"
                logger.info("[Image] 检测到 Apple MPS, 使用 MPS 加速")
            else:
                self._device = "cpu"
                logger.info("[Image] 无 GPU, 使用 CPU 推理")

            model_name = config.IMAGE_CLASSIFIER_MODEL
            logger.info(f"[Image] 加载 CLIP 模型: {model_name} (device={self._device})...")

            # 离线模式: 优先使用本地缓存 (避免网络中断导致加载失败)
            # 模型已在之前的运行中下载并缓存 (pytorch_model.bin ~577MB)
            original_hf_offline = os.environ.get("HF_HUB_OFFLINE")
            original_tf_offline = os.environ.get("TRANSFORMERS_OFFLINE")
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"

            try:
                self._processor = CLIPProcessor.from_pretrained(model_name)
                self._model = CLIPModel.from_pretrained(model_name)
            except Exception as offline_err:
                # 离线模式失败: 可能是首次运行尚未缓存, 回退到在线模式
                logger.warning(f"[Image] 离线模式加载失败, 回退到在线模式: {offline_err}")
                os.environ.pop("HF_HUB_OFFLINE", None)
                os.environ.pop("TRANSFORMERS_OFFLINE", None)
                self._processor = CLIPProcessor.from_pretrained(model_name)
                self._model = CLIPModel.from_pretrained(model_name)

            # 恢复原始环境变量
            if original_hf_offline is not None:
                os.environ["HF_HUB_OFFLINE"] = original_hf_offline
            else:
                os.environ.pop("HF_HUB_OFFLINE", None)
            if original_tf_offline is not None:
                os.environ["TRANSFORMERS_OFFLINE"] = original_tf_offline
            else:
                os.environ.pop("TRANSFORMERS_OFFLINE", None)

            self._model.to(self._device)
            self._model.eval()
            self._loaded = True

            logger.info("[Image] CLIP 模型加载完成")
            return True

        except ImportError:
            logger.error(
                "[Image] torch/transformers 未安装. "
                "请运行: pip install torch transformers pillow"
            )
            return False
        except Exception as e:
            logger.error(f"[Image] CLIP 模型加载失败: {e}", exc_info=True)
            return False

    def classify_tiles(
        self, tile_images: list, target: str
    ) -> list[tuple[bool, float]]:
        """
        批量零样本分类 (优化版)
        
        所有 tile 和所有文本提示在单次前向传播中完成,
        取每个 tile 在所有正提示中的最大概率作为匹配分数.
        
        参数:
            tile_images: PIL.Image 列表
            target: 挑战目标文本 (如 "traffic light")
        
        返回: [(is_match, confidence), ...] 列表, 按原始顺序
        """
        if not self._loaded or not tile_images:
            return [(False, 0.0)] * len(tile_images)

        import torch

        positives, negatives = _build_prompts(target)
        all_texts = positives + negatives
        n_pos = len(positives)

        # --- 批量推理: 所有 tile × 所有文本 一次前向 ---
        try:
            # 确保所有图片是 RGB
            rgb_images = []
            for img in tile_images:
                if img.mode != "RGB":
                    img = img.convert("RGB")
                rgb_images.append(img)

            inputs = self._processor(
                text=all_texts,
                images=rgb_images,
                return_tensors="pt",
                padding=True,
            )
            # 移到对应设备
            inputs = {
                k: v.to(self._device) if isinstance(v, torch.Tensor) else v
                for k, v in inputs.items()
            }

            with torch.no_grad():
                outputs = self._model(**inputs)

            # logits_per_image: [n_tiles, n_texts]
            logits = outputs.logits_per_image
            probs = logits.softmax(dim=-1)  # [n_tiles, n_texts]

            # 每个 tile 的匹配分数 = 所有正提示概率之和
            pos_probs = probs[:, :n_pos]  # [n_tiles, n_pos]
            match_scores = pos_probs.sum(dim=1)  # [n_tiles]
            # 归一化到 0-1 (正提示概率总和 / 所有提示概率总和 已自动归一)
            # 由于 softmax 分母包含负样本, sum(pos_probs) 已经是合理的匹配概率

            results = []
            threshold = config.IMAGE_MATCH_THRESHOLD
            for i in range(len(tile_images)):
                score = match_scores[i].item()
                is_match = score > threshold
                results.append((is_match, score))
                logger.info(
                    f"[Image] Tile [{i}] score={score:.4f} "
                    f"{'✓ MATCH' if is_match else '✗ skip'}"
                )

            return results

        except Exception as e:
            logger.error(f"[Image] 批量分类失败: {e}", exc_info=True)
            return [(False, 0.0)] * len(tile_images)

    def classify_tiles_ranked(
        self, tile_images: list, target: str, top_k: int = 3
    ) -> list[tuple[bool, float]]:
        """
        排序模式: 选 top-K 个 tile (按置信度降序)
        
        策略:
        1. 批量推理得到所有 tile 的分数
        2. 按分数降序排列
        3. 选 top_k 个, 但每个必须 >= IMAGE_MIN_CONFIDENCE
        4. 如果 top_k 中有低于阈值的, 不选它 (宁缺勿滥)
        
        参数:
            tile_images: PIL.Image 列表
            target: 挑战目标文本
            top_k: 选前 K 个 tile
        
        返回: [(is_match, confidence), ...]
        """
        results = self.classify_tiles(tile_images, target)
        if not results:
            return results

        # 按分数降序排列
        indexed = list(enumerate(results))
        indexed.sort(key=lambda x: x[1][1], reverse=True)

        # 标记 top_k 为匹配: 同时满足最低分和与最高分的差距约束
        min_conf = config.IMAGE_MIN_CONFIDENCE
        score_gap = getattr(config, "IMAGE_RANK_SCORE_GAP", 0.45)
        top_score = indexed[0][1][1]
        adaptive_floor = max(min_conf, top_score - score_gap)

        # 自然间隔检测: 在 top_k 范围内寻找最大分数间隔
        # 如果相邻两个 tile 的分数差 > 0.25, 说明存在自然分界线,
        # 高分组为匹配, 低分组不选 (比固定 gap 更准确地适应 CLIP 分数分布)
        natural_gap_boundary = None
        check_range = min(top_k + 2, len(indexed))  # 多看 2 个以发现间隔
        max_gap = 0.0
        for i in range(1, check_range):
            gap = indexed[i - 1][1][1] - indexed[i][1][1]
            if gap > max_gap and gap > 0.25:
                max_gap = gap
                natural_gap_boundary = indexed[i - 1][1][1]  # 间隔上方的分数

        # 如果自然间隔存在且比 adaptive_floor 更宽松, 使用自然间隔
        if natural_gap_boundary is not None and natural_gap_boundary < adaptive_floor:
            adaptive_floor = max(min_conf, natural_gap_boundary)
            logger.info(
                f"[Image] CLIP 自然间隔检测: boundary={natural_gap_boundary:.4f} "
                f"(gap={max_gap:.4f}), 使用自然间隔替代 adaptive_floor"
            )

        matched_indices = set()
        for i in range(min(top_k, len(indexed))):
            idx = indexed[i][0]
            score = indexed[i][1][1]
            if score >= adaptive_floor:
                matched_indices.add(idx)
            else:
                # 分数不够, 后面的更不够, 直接停止
                break

        # 尽力而为回退: 如果 top_score < min_conf, 所有 tile 都被过滤
        # 此时选至少 1 个最高分 tile (比什么都不选好, reCAPTCHA 允许试错)
        if not matched_indices and indexed:
            best_idx = indexed[0][0]
            best_score = indexed[0][1][1]
            # 放宽到 top_score * 0.5, 至少选 1 个, 最多选 2 个
            relaxed_floor = best_score * 0.5
            for i in range(min(2, len(indexed))):
                idx = indexed[i][0]
                score = indexed[i][1][1]
                if score >= relaxed_floor:
                    matched_indices.add(idx)
            logger.warning(
                f"[Image] CLIP ranked 尽力而为: top_score={top_score:.4f} < min_conf={min_conf}, "
                f"relaxed_floor={relaxed_floor:.4f}, selected={len(matched_indices)}"
            )

        logger.info(
            f"[Image] CLIP ranked: top={top_score:.4f}, "
            f"floor={adaptive_floor:.4f}, selected={len(matched_indices)}/{top_k}"
        )

        final_results = []
        for i in range(len(results)):
            score = results[i][1]  # results[i] = (is_match, score), 取 score
            is_match = i in matched_indices
            final_results.append((is_match, score))

        return final_results


# ============================================================
# YOLOv8 三引擎检测器 (单例): 分类 (3x3) + 分割 (4x4)
# ============================================================
class _YOLODetector:
    """
    YOLOv8 三引擎检测器 (单例)

    基于 ETH Zurich "Breaking reCAPTCHAv2" 方案:
    - YOLOv8-cls 微调模型: 3x3 (9宫格) 挑战
      → 每个 tile 独立分类, 12 个 reCAPTCHA 专用类别, 概率 > 0.2 即选中
    - YOLOv8-seg 基础模型: 4x4 (16宫格) 挑战
      → COCO 分割掩码 → 网格重叠检测 (conf=0.5, imgsz=320, 任意像素重叠)
    - 非 12 类别由 CLIP 回退处理
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        self._cls_model = None    # 微调分类模型 (3x3)
        self._seg_model = None    # 基础分割模型 (4x4)
        self._device = "cpu"
        self._cls_loaded = False
        self._seg_loaded = False
        self._cls_class_names = None  # 模型的类别名 {0: 'Bicycle', ...}
        self._initialized = True

    @property
    def is_loaded(self) -> bool:
        return self._cls_loaded or self._seg_loaded

    @property
    def cls_loaded(self) -> bool:
        return self._cls_loaded

    @property
    def seg_loaded(self) -> bool:
        return self._seg_loaded

    @staticmethod
    def can_handle_cls(target: str) -> bool:
        """检查分类模型是否能处理此类别 (在 12 类中)"""
        return _get_recaptcha_class_index(target) is not None

    @staticmethod
    def can_handle_seg(target: str) -> bool:
        """检查分割模型是否能处理此类别 (在 COCO 映射中)"""
        return _get_coco_class_id(target) is not None

    async def load_async(self) -> bool:
        """异步加载模型 (线程池, 不阻塞事件循环)"""
        if self._cls_loaded and self._seg_loaded:
            return True
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._load_sync)

    def _load_sync(self) -> bool:
        """同步加载模型 (在线程池中执行)"""
        try:
            from ultralytics import YOLO

            # GPU 自动检测
            try:
                import torch
                if torch.cuda.is_available():
                    self._device = "cuda"
                    logger.info("[Image] YOLO 检测到 CUDA GPU, 使用 GPU 加速")
                else:
                    self._device = "cpu"
                    logger.info("[Image] YOLO 使用 CPU 推理")
            except ImportError:
                self._device = "cpu"

            # 加载微调分类模型 (3x3 挑战)
            cls_path = getattr(config, "YOLO_CLS_MODEL_PATH", "")
            if cls_path and os.path.exists(cls_path):
                logger.info(f"[Image] 加载 YOLOv8-cls 微调模型: {cls_path}")
                self._cls_model = YOLO(cls_path)
                self._cls_class_names = self._cls_model.names
                self._cls_loaded = True
                logger.info(
                    f"[Image] 分类模型加载完成, 类别: {self._cls_class_names}"
                )
            else:
                logger.warning(f"[Image] 分类模型文件不存在: {cls_path}")

            # 加载基础分割模型 (4x4 挑战)
            seg_name = getattr(config, "YOLO_SEG_MODEL_NAME", "yolov8n-seg.pt")
            logger.info(f"[Image] 加载 YOLOv8-seg 模型: {seg_name}")
            self._seg_model = YOLO(seg_name)
            self._seg_loaded = True
            logger.info("[Image] 分割模型加载完成")

            return self._cls_loaded or self._seg_loaded

        except ImportError:
            logger.error("[Image] ultralytics 未安装. 请运行: pip install ultralytics")
            return False
        except Exception as e:
            logger.error(f"[Image] YOLO 模型加载失败: {e}", exc_info=True)
            return False

    def classify_tiles(
        self, tile_images: list, target: str
    ) -> list[tuple[bool, float]] | None:
        """
        用微调分类模型对每个 tile 独立分类 (3x3 挑战)

        参数:
            tile_images: PIL.Image 列表
            target: 挑战目标文本

        返回: [(is_match, confidence), ...] 或 None (无法处理)
        """
        if not self._cls_loaded:
            return None

        cls_idx = _get_recaptcha_class_index(target)
        if cls_idx is None:
            return None

        try:
            import numpy as np

            threshold = getattr(config, "YOLO_CLS_THRESHOLD", 0.2)
            imgsz = getattr(config, "YOLO_CLS_IMGSZ", 128)

            results = []
            for i, tile_img in enumerate(tile_images):
                # 确保 RGB
                if tile_img.mode != "RGB":
                    tile_img = tile_img.convert("RGB")

                # 运行分类推理
                preds = self._cls_model.predict(
                    tile_img, imgsz=imgsz, verbose=False
                )

                if not preds or len(preds) == 0:
                    results.append((False, 0.0))
                    continue

                pred = preds[0]
                if pred.probs is None:
                    results.append((False, 0.0))
                    continue

                probs = pred.probs.data.cpu().numpy()
                target_prob = float(probs[cls_idx])

                # 获取预测的类别名
                top1_idx = int(pred.probs.top1)
                top1_prob = float(probs[top1_idx])
                top1_name = self._cls_class_names.get(top1_idx, "?") if self._cls_class_names else "?"
                target_name = self._cls_class_names.get(cls_idx, "?") if self._cls_class_names else "?"
                top1_margin = getattr(config, "YOLO_CLS_TOP1_MARGIN", 0.08)
                is_match = (
                    target_prob > threshold
                    and target_prob >= top1_prob - top1_margin
                )

                results.append((is_match, target_prob))
                logger.info(
                    f"[Image] CLS Tile [{i}]: pred='{top1_name}' "
                    f"target='{target_name}' prob={target_prob:.4f} "
                    f"top1={top1_prob:.4f} "
                    f"{'✓ MATCH' if is_match else '✗ skip'}"
                )

            match_count = sum(1 for m, _ in results if m)
            logger.info(f"[Image] CLS 结果: {match_count}/{len(tile_images)} 个匹配")
            return results

        except Exception as e:
            logger.error(f"[Image] CLS 分类失败: {e}", exc_info=True)
            return None

    def classify_tiles_ranked(
        self, tile_images: list, target: str, top_k: int = 4
    ) -> list[tuple[bool, float]] | None:
        """
        用分类模型对 tile 做 ranking 选择 (用于 4x4 网格回退)

        与 classify_tiles 的区别:
        - classify_tiles 使用绝对阈值 (适合 3x3, 模型训练数据匹配)
        - classify_tiles_ranked 使用 top-k 排序 (适合 4x4, 模型未训练但概率排序仍有参考价值)

        策略:
          1. 获取每个 tile 对目标类别的概率
          2. 按概率降序排序
          3. 自适应选择: top-k 内且与最高分差距不超过 YOLO_CLS_RANK_GAP
          4. 最低概率不得低于 YOLO_CLS_RANK_MIN (避免选完全不相关的 tile)
        """
        if not self._cls_loaded:
            return None

        cls_idx = _get_recaptcha_class_index(target)
        if cls_idx is None:
            return None

        try:
            threshold = getattr(config, "YOLO_CLS_THRESHOLD", 0.15)
            imgsz = getattr(config, "YOLO_CLS_IMGSZ", 224)
            rank_gap = getattr(config, "YOLO_CLS_RANK_GAP", 0.15)
            rank_min = getattr(config, "YOLO_CLS_RANK_MIN", 0.03)

            # 收集每个 tile 的目标概率
            tile_probs = []
            for i, tile_img in enumerate(tile_images):
                if tile_img.mode != "RGB":
                    tile_img = tile_img.convert("RGB")

                preds = self._cls_model.predict(
                    tile_img, imgsz=imgsz, verbose=False
                )

                if not preds or len(preds) == 0 or preds[0].probs is None:
                    tile_probs.append((i, 0.0))
                    continue

                probs = preds[0].probs.data.cpu().numpy()
                target_prob = float(probs[cls_idx])
                top1_idx = int(preds[0].probs.top1)
                top1_prob = float(probs[top1_idx])
                top1_name = self._cls_class_names.get(top1_idx, "?") if self._cls_class_names else "?"
                target_name = self._cls_class_names.get(cls_idx, "?") if self._cls_class_names else "?"

                tile_probs.append((i, target_prob))
                logger.info(
                    f"[Image] CLS-Rank Tile [{i}]: pred='{top1_name}' "
                    f"target='{target_name}' prob={target_prob:.4f} "
                    f"top1={top1_prob:.4f}"
                )

            # 按概率降序排序
            tile_probs.sort(key=lambda x: x[1], reverse=True)

            # 自适应选择: top-k 内且与最高分差距不超过 rank_gap
            top_score = tile_probs[0][1] if tile_probs else 0.0
            floor = max(top_score - rank_gap, rank_min)
            selected_indices = set()
            for idx, prob in tile_probs[:top_k]:
                if prob >= floor and prob >= rank_min:
                    selected_indices.add(idx)

            # 尽力而为回退: 如果所有 tile 都低于 rank_min, 选最高分的 1-2 个
            # 但仅当 top_score 有一定信号时才启用 (避免随机猜测浪费点击机会)
            best_effort_threshold = 0.05  # 低于此值视为模型无信号, 不选任何 tile
            if not selected_indices and tile_probs and top_score >= best_effort_threshold:
                relaxed_floor = top_score * 0.5
                for idx, prob in tile_probs[:min(2, top_k)]:
                    if prob >= relaxed_floor:
                        selected_indices.add(idx)
                logger.warning(
                    f"[Image] CLS-Rank 尽力而为: top_score={top_score:.4f} < rank_min={rank_min}, "
                    f"relaxed_floor={relaxed_floor:.4f}"
                )
            elif not selected_indices and tile_probs and top_score < best_effort_threshold:
                logger.warning(
                    f"[Image] CLS-Rank 放弃选择: top_score={top_score:.4f} < {best_effort_threshold} "
                    f"(模型无信号, 不浪费点击机会)"
                )

            results = [
                (i in selected_indices, prob)
                for i, (_, prob) in enumerate(
                    sorted(tile_probs, key=lambda x: x[0])
                )
            ]

            match_count = sum(1 for m, _ in results if m)
            logger.info(
                f"[Image] CLS-Rank 结果: {match_count}/{len(tile_images)} 个匹配 "
                f"(top={top_score:.4f}, floor={floor:.4f}, k={top_k})"
            )
            return results

        except Exception as e:
            logger.error(f"[Image] CLS-Rank 分类失败: {e}", exc_info=True)
            return None

    def detect_grid_cells(
        self,
        full_image_path: str,
        target: str,
        grid_rows: int,
        grid_cols: int,
    ) -> list[tuple[bool, float]] | None:
        """
        在完整网格图上运行 YOLOv8-seg, 返回哪些网格应被选中 (4x4 挑战)

        多尺度检测策略:
          1. 主尺度: imgsz=YOLO_SEG_IMGSZ (默认 320, ETH 参数)
          2. 高分辨率尺度: imgsz=YOLO_SEG_IMGSZ_HIGH (默认 640, 检测小目标)
          3. 低置信度回退: 若主尺度 0 匹配, 用更低阈值重试

        ETH 参数: conf=0.5, imgsz=320, 任意像素重叠即选中
        """
        if not self._seg_loaded:
            return None

        coco_id = _get_coco_class_id(target)
        if coco_id is None:
            return None

        try:
            import numpy as np
            import cv2

            # 多尺度检测参数
            conf_threshold = getattr(config, "YOLO_SEG_CONFIDENCE", 0.25)
            imgsz = getattr(config, "YOLO_SEG_IMGSZ", 320)
            imgsz_high = getattr(config, "YOLO_SEG_IMGSZ_HIGH", 640)
            conf_fallback = getattr(config, "YOLO_SEG_CONFIDENCE_FALLBACK", 0.10)
            overlap_mode = getattr(config, "YOLO_SEG_OVERLAP_MODE", "ratio")
            overlap_threshold = getattr(config, "YOLO_SEG_OVERLAP_THRESHOLD", 0.05)

            # ---- 多尺度检测: 先主尺度, 再高分辨率 ----
            all_results = []
            for scale_imgsz in [imgsz, imgsz_high]:
                preds = self._seg_model.predict(
                    full_image_path,
                    conf=conf_threshold,
                    imgsz=scale_imgsz,
                    verbose=False,
                )
                if preds and len(preds) > 0:
                    all_results.append((scale_imgsz, preds[0]))

            # 若两种尺度均 0 目标, 用低置信度回退
            target_found = False
            for _, r in all_results:
                if r.boxes is not None and len(r.boxes) > 0:
                    boxes_cls = r.boxes.cls.cpu().numpy()
                    if int(coco_id) in boxes_cls.astype(int):
                        target_found = True
                        break

            if not target_found:
                logger.info(
                    f"[Image] SEG 多尺度 (imgsz={imgsz},{imgsz_high}) 未检测到目标类别 (COCO ID={coco_id}), "
                    f"使用低置信度回退 (conf={conf_fallback})"
                )
                for scale_imgsz in [imgsz_high, imgsz]:
                    preds = self._seg_model.predict(
                        full_image_path,
                        conf=conf_fallback,
                        imgsz=scale_imgsz,
                        verbose=False,
                    )
                    if preds and len(preds) > 0:
                        r = preds[0]
                        if r.boxes is not None and len(r.boxes) > 0:
                            boxes_cls = r.boxes.cls.cpu().numpy()
                            if int(coco_id) in boxes_cls.astype(int):
                                all_results = [(scale_imgsz, r)]
                                target_found = True
                                break

            if not all_results or not target_found:
                logger.info("[Image] SEG 所有尺度+回退均未检测到目标类别")
                return [(False, 0.0)] * (grid_rows * grid_cols)

            n_cells = grid_rows * grid_cols
            cell_selected = [False] * n_cells
            cell_confidence = [0.0] * n_cells
            edge_margin = int(getattr(config, "YOLO_SEG_EDGE_MARGIN_PX", 2))

            # ---- 合并多尺度检测结果 ----
            total_target_detections = 0
            for scale_imgsz, result in all_results:
                img_h, img_w = result.orig_shape
                cell_h = img_h / grid_rows
                cell_w = img_w / grid_cols

                if result.boxes is None or len(result.boxes) == 0:
                    continue

                boxes_cls = result.boxes.cls.cpu().numpy()
                boxes_conf = result.boxes.conf.cpu().numpy()
                has_masks = result.masks is not None and len(result.masks) > 0

                # 收集目标类别的检测
                target_detections = []
                for i, cls_id in enumerate(boxes_cls):
                    if int(cls_id) == coco_id:
                        target_detections.append({
                            "conf": float(boxes_conf[i]),
                            "box_idx": i,
                            "mask_idx": i if has_masks else None,
                        })

                if not target_detections:
                    continue

                total_target_detections += len(target_detections)
                logger.info(
                    f"[Image] SEG imgsz={scale_imgsz}: 检测到 {len(target_detections)} 个目标 "
                    f"(COCO ID={coco_id})"
                )

                # ETH 方案: 用掩码多边形填充, 检查任意像素重叠
                for det in target_detections:
                    conf = det["conf"]

                    if has_masks and det["mask_idx"] is not None:
                        # === ETH 方案: 多边形掩码 → 任意像素重叠 ===
                        try:
                            mask_tensor = result.masks.data[det["mask_idx"]].cpu().numpy()
                            # mask_tensor 可能与原图尺寸不同 (imgsz 缩放), 需要调整
                            if mask_tensor.shape[0] != img_h or mask_tensor.shape[1] != img_w:
                                mask_full = cv2.resize(
                                    mask_tensor.astype(np.uint8),
                                    (img_w, img_h),
                                    interpolation=cv2.INTER_NEAREST,
                                )
                            else:
                                mask_full = mask_tensor.astype(np.uint8)

                            for row in range(grid_rows):
                                for col in range(grid_cols):
                                    cell_idx = row * grid_cols + col
                                    y_start = int(row * cell_h)
                                    y_end = int((row + 1) * cell_h)
                                    x_start = int(col * cell_w)
                                    x_end = int((col + 1) * cell_w)

                                    cell_mask = mask_full[y_start:y_end, x_start:x_end]
                                    overlap = np.count_nonzero(cell_mask) / (cell_mask.size + 1e-6)

                                    if overlap_mode == "any":
                                        # 忽略 cell 边缘的少量像素, 避免边界轻触导致误选
                                        if (
                                            edge_margin > 0
                                            and cell_mask.shape[0] > edge_margin * 2
                                            and cell_mask.shape[1] > edge_margin * 2
                                        ):
                                            check_mask = cell_mask[
                                                edge_margin:-edge_margin,
                                                edge_margin:-edge_margin,
                                            ]
                                        else:
                                            check_mask = cell_mask
                                        if np.any(check_mask > 0):
                                            cell_selected[cell_idx] = True
                                            cell_confidence[cell_idx] = max(
                                                cell_confidence[cell_idx], conf
                                            )
                                            logger.info(
                                                f"[Image] SEG 掩码选中 cell[{row},{col}] "
                                                f"(any overlap={overlap:.2%}, conf={conf:.2f})"
                                            )
                                    else:
                                        # 按比例阈值 (ratio 模式)
                                        if (
                                            edge_margin > 0
                                            and cell_mask.shape[0] > edge_margin * 2
                                            and cell_mask.shape[1] > edge_margin * 2
                                        ):
                                            check_mask = cell_mask[
                                                edge_margin:-edge_margin,
                                                edge_margin:-edge_margin,
                                            ]
                                            check_total = check_mask.size
                                        else:
                                            check_mask = cell_mask
                                            check_total = cell_mask.size
                                        overlap = np.count_nonzero(check_mask > 0) / (check_total + 1e-6)
                                        if overlap > overlap_threshold:
                                            cell_selected[cell_idx] = True
                                            cell_confidence[cell_idx] = max(
                                                cell_confidence[cell_idx], conf
                                            )
                                            logger.info(
                                                f"[Image] SEG 掩码选中 cell[{row},{col}] "
                                                f"(overlap={overlap:.2%}, conf={conf:.2f})"
                                            )
                        except Exception as e:
                            logger.warning(f"[Image] 掩码处理异常: {e}")
                    else:
                        # === 边界框回退 ===
                        boxes_xyxy = result.boxes.xyxy.cpu().numpy()
                        box = boxes_xyxy[det["box_idx"]]
                        x1, y1, x2, y2 = box

                        for row in range(grid_rows):
                            for col in range(grid_cols):
                                cell_idx = row * grid_cols + col
                                cx1, cy1 = col * cell_w, row * cell_h
                                cx2, cy2 = (col + 1) * cell_w, (row + 1) * cell_h

                                ox1, oy1 = max(x1, cx1), max(y1, cy1)
                                ox2, oy2 = min(x2, cx2), min(y2, cy2)

                                if ox1 < ox2 and oy1 < oy2:
                                    cell_selected[cell_idx] = True
                                    cell_confidence[cell_idx] = max(
                                        cell_confidence[cell_idx], conf
                                    )

            if total_target_detections == 0:
                logger.info("[Image] SEG 多尺度合并后仍无目标检测")
                return [(False, 0.0)] * n_cells

            results_list = [
                (cell_selected[i], cell_confidence[i])
                for i in range(n_cells)
            ]
            selected_count = sum(1 for s, _ in results_list if s)
            logger.info(
                f"[Image] SEG 结果: {selected_count}/{n_cells} 个网格被选中 "
                f"(总检测数={total_target_detections})"
            )
            return results_list

        except Exception as e:
            logger.error(f"[Image] SEG 检测失败: {e}", exc_info=True)
            return None


# ============================================================
# ImageRuntime
# ============================================================
class ImageRuntime(BaseBypassRuntime):
    """AI 图像识别方案运行时 (YOLOv8 分类+分割 + CLIP 三引擎, ETH 方案)"""

    method_name = "image"
    method_desc = "AI 图像识别 (YOLOv8 + CLIP 双引擎, 免费)"

    def __init__(self):
        super().__init__()
        self._classifier = _CLIPClassifier()
        self._yolo_detector = _YOLODetector()
        self._temp_dir = tempfile.mkdtemp(prefix="recaptcha_img_")
        self._capture_seq = 0
        self._bframe_original_style = None  # bframe 截图前的原始 CSS (用于恢复)
        self._last_table_hash = None  # 上一轮截图哈希 (检测陈旧截图)
        self._last_table_array = None  # 上一轮截图像素数据 (精确陈旧检测)
        self._stale_count = 0  # 连续陈旧截图次数
        self._last_screenshot_method = None  # 上一轮截图方法 (检测方法切换)

    async def solve_recaptcha(self, sitekey: str, page_url: str) -> str | None:
        """主求解流程"""
        logger.info("[Image] 使用 AI 图像识别方案求解 reCAPTCHA v2...")

        if not _has_pillow:
            raise RuntimeError("[Image] Pillow 未安装, 请运行: pip install Pillow")

        # 异步加载 YOLO 模型 (分类+分割)
        yolo_ok = await self._yolo_detector.load_async()
        if yolo_ok:
            engines = []
            if self._yolo_detector.cls_loaded:
                engines.append("CLS(3x3)")
            if self._yolo_detector.seg_loaded:
                engines.append("SEG(4x4)")
            logger.info(f"[Image] YOLOv8 引擎就绪: {', '.join(engines)}")
        else:
            logger.warning("[Image] YOLO 加载失败, 仅使用 CLIP 引擎")

        # 异步加载 CLIP 模型 (回退引擎)
        clip_ok = await self._classifier.load_async()
        if clip_ok:
            logger.info("[Image] CLIP 引擎就绪 (非 12 类别回退使用)")
        else:
            logger.warning("[Image] CLIP 加载失败")
            if not yolo_ok:
                raise RuntimeError(
                    "[Image] YOLO 和 CLIP 均不可用, 请运行: "
                    "pip install ultralytics torch transformers pillow"
                )

        max_retries = config.RECAPTCHA_MAX_RETRIES
        for attempt in range(1, max_retries + 1):
            logger.info(f"[Image] 第 {attempt}/{max_retries} 次尝试...")

            try:
                if await self._attempt_solve():
                    logger.info("[Image] reCAPTCHA 图像求解成功!")
                    return None
            except Exception as e:
                logger.warning(f"[Image] 第 {attempt} 次尝试异常: {e}", exc_info=True)

            # 重置挑战
            await self._reset_challenge()
            await asyncio.sleep(config.RECAPTCHA_RETRY_DELAY)

        raise RuntimeError("[Image] 图像识别方案求解失败, 已达最大重试次数")

    # ========================================================
    # 单次求解
    # ========================================================
    async def _attempt_solve(self) -> bool:
        """单次求解尝试"""
        # Step 1: 点击 checkbox
        if not await self._click_checkbox():
            return False
        await asyncio.sleep(3)

        # Step 2: 可能直接通过
        if await self._is_checked():
            logger.info("[Image] checkbox 直接通过, 无需图像挑战")
            return True

        # Step 3: 等待图像挑战加载
        await asyncio.sleep(2)
        bframe = await self._get_recaptcha_frame("bframe")
        if not bframe:
            logger.warning("[Image] 未找到挑战 iframe (bframe)")
            return False

        # Step 4: 提取提示文本
        prompt_text = await self._extract_prompt(bframe)
        if not prompt_text:
            logger.warning("[Image] 无法提取挑战提示文本")
            return False
        logger.info(f"[Image] 挑战目标: '{prompt_text}'")

        # Step 5: 截取 tiles (同时返回完整表格截图路径, 供 YOLO 使用)
        tile_images, grid_size, table_path = await self._capture_tiles(bframe)
        if not tile_images:
            # 页面关闭可能意味着验证已通过 (reCAPTCHA 验证成功后页面会导航走)
            if self.page is None or self.page.is_closed():
                logger.info("[Image] 截图失败且页面已关闭, 可能验证已通过")
                return True
            logger.warning("[Image] 无法截取图像 tiles")
            return False
        logger.info(
            f"[Image] 截取 {len(tile_images)} 个 tiles (网格: {grid_size[0]}x{grid_size[1]})"
        )

        # Step 6: 三引擎分类 (CLS for 3x3/32-tile, SEG for 4x4, CLIP fallback)
        match_results = None
        n_tiles = len(tile_images)
        is_4x4 = (n_tiles == 16) or (grid_size[0] == 4 and grid_size[1] == 4)
        # CLS 适用于所有非 4x4 挑战, 以及 4x4 中 SEG 失败后的回退
        cls_available = self._yolo_detector.cls_loaded and _YOLODetector.can_handle_cls(prompt_text)
        use_cls = (not is_4x4) and cls_available

        # 引擎 1: YOLOv8-seg 分割 (仅 4x4 挑战, COCO 类别)
        if is_4x4 and self._yolo_detector.seg_loaded and _YOLODetector.can_handle_seg(prompt_text) and table_path:
            logger.info(f"[Image] 使用 YOLOv8-seg 引擎处理 '{prompt_text}' (4x4 分割)")
            match_results = await asyncio.get_event_loop().run_in_executor(
                None,
                self._yolo_detector.detect_grid_cells,
                table_path,
                prompt_text,
                grid_size[0],
                grid_size[1],
            )
            if match_results:
                match_count = sum(1 for m, _ in match_results if m)
                if match_count == 0:
                    logger.warning("[Image] SEG 0 匹配, 回退")
                    match_results = None
                else:
                    logger.info(f"[Image] SEG 完成: {match_count}/{n_tiles} 个匹配")

        # 引擎 2: YOLOv8-cls 分类 (非 4x4 挑战用绝对阈值, 4x4 用 ranking 模式)
        if match_results is None and cls_available:
            grid_desc = f"{grid_size[0]}x{grid_size[1]}" if grid_size else "?"
            if is_4x4:
                # 4x4 网格: CLS 模型未在此尺寸训练, 使用 ranking 模式 (top-k 选择)
                cls_top_k = getattr(config, "IMAGE_TOP_K_4X4", 4)
                logger.info(f"[Image] 使用 YOLOv8-cls 引擎处理 '{prompt_text}' ({grid_desc} ranking模式)")
                match_results = await asyncio.get_event_loop().run_in_executor(
                    None,
                    self._yolo_detector.classify_tiles_ranked,
                    tile_images,
                    prompt_text,
                    cls_top_k,
                )
            else:
                # 3x3 网格: 模型训练数据匹配, 使用绝对阈值模式
                logger.info(f"[Image] 使用 YOLOv8-cls 引擎处理 '{prompt_text}' ({grid_desc} 分类)")
                match_results = await asyncio.get_event_loop().run_in_executor(
                    None,
                    self._yolo_detector.classify_tiles,
                    tile_images,
                    prompt_text,
                )
            if match_results:
                match_count = sum(1 for m, _ in match_results if m)
                if match_count == 0:
                    logger.warning("[Image] CLS 0 匹配, 回退到 CLIP")
                    match_results = None
                else:
                    logger.info(f"[Image] CLS 完成: {match_count}/{n_tiles} 个匹配")

        # 引擎 3: CLIP 回退 (非 13 类别或 YOLO 失败)
        if match_results is None:
            logger.info(f"[Image] 使用 CLIP 引擎处理 '{prompt_text}'")
            if n_tiles >= 16:
                expected_matches = getattr(config, "IMAGE_TOP_K_4X4", 4)
            elif n_tiles >= 9:
                expected_matches = getattr(config, "IMAGE_TOP_K_3X3", 3)
            else:
                expected_matches = max(2, n_tiles // 3)
            match_results = self._classifier.classify_tiles_ranked(
                tile_images, prompt_text, top_k=expected_matches
            )

            match_count = sum(1 for m, _ in match_results if m)
            if match_count == 0:
                # 回退: 用固定阈值模式 (但仍限制不超过 expected_matches)
                logger.warning("[Image] CLIP 排序模式 0 匹配, 尝试固定阈值模式...")
                raw_results = self._classifier.classify_tiles(tile_images, prompt_text)
                # 硬上限: 固定阈值模式下也不超过 expected_matches
                matched_indices = [i for i, (m, _) in enumerate(raw_results) if m]
                if len(matched_indices) > expected_matches:
                    # 只保留分数最高的 expected_matches 个
                    scored = [(i, raw_results[i][1]) for i in matched_indices]
                    scored.sort(key=lambda x: x[1], reverse=True)
                    keep = set(idx for idx, _ in scored[:expected_matches])
                    match_results = [
                        (i in keep, raw_results[i][1]) for i in range(len(raw_results))
                    ]
                else:
                    match_results = raw_results
                match_count = sum(1 for m, _ in match_results if m)

            if match_count == 0:
                logger.warning("[Image] 三引擎均 0 匹配, 刷新挑战")
                return False

            logger.info(f"[Image] CLIP 分类完成: {match_count}/{len(tile_images)} 个匹配")

        # Step 7: 点击匹配的 tiles
        clicked_count = await self._click_matching_tiles(bframe, match_results)
        if clicked_count == 0:
            logger.warning("[Image] 0 个 tile 被点击, 跳过 Verify/Skip 并刷新挑战")
            return False
        await asyncio.sleep(1)

        # Step 8: 点击验证
        if not await self._click_verify(bframe, clicked_count=clicked_count):
            return False
        await asyncio.sleep(3)

        # Step 9: 检查是否通过
        if await self._is_checked():
            logger.info("[Image] reCAPTCHA 图像验证通过!")
            await self._restore_bframe_style()
            return True

        # Step 10: 可能出现新的挑战 (多轮)
        logger.info("[Image] 可能需要多轮挑战, 检查是否有新挑战...")
        await asyncio.sleep(2)
        new_bframe = await self._get_recaptcha_frame("bframe")
        if new_bframe:
            # 尝试处理第二轮
            if await self._handle_additional_challenge(new_bframe, prompt_text):
                await self._restore_bframe_style()
                return True

        logger.warning("[Image] 图像验证未通过")
        await self._restore_bframe_style()
        return False

    async def _handle_additional_challenge(
        self, bframe, original_prompt: str
    ) -> bool:
        """处理多轮挑战 (reCAPTCHA 有时要求连续完成 2-3 轮)"""
        for round_num in range(3):
            # 防御: 页面已关闭 (验证通过后页面可能已导航走)
            if self.page is None or self.page.is_closed():
                logger.info("[Image] 页面已关闭, 可能验证已通过")
                return True  # 页面关闭通常意味着验证通过, 页面已跳转

            # 检查是否已通过
            if await self._is_checked():
                return True

            # 每轮重新获取 bframe (reCAPTCHA 刷新挑战时可能重建 frame)
            fresh_bframe = await self._get_recaptcha_frame("bframe")
            if fresh_bframe:
                bframe = fresh_bframe
            else:
                # bframe 不存在, 可能挑战已关闭
                logger.info("[Image] 未找到 bframe, 挑战可能已通过")
                return await self._is_checked()

            # 检查是否还有挑战弹窗
            target = bframe.locator(".rc-imageselect-target")
            if await target.count() == 0:
                logger.info("[Image] 挑战弹窗已关闭, 可能已通过")
                return await self._is_checked()

            # 提取新的提示 (可能和上一轮不同)
            new_prompt = await self._extract_prompt(bframe)
            if new_prompt:
                prompt_text = new_prompt
                logger.info(f"[Image] 第 {round_num + 1} 轮挑战目标: '{prompt_text}'")
            else:
                prompt_text = original_prompt

            # 截取 + 分类 + 点击 (三引擎)
            tile_images, grid_sz, tbl_path = await self._capture_tiles(bframe)
            if not tile_images:
                # 页面关闭可能意味着验证已通过
                if self.page is None or self.page.is_closed():
                    logger.info("[Image] 多轮挑战截图失败且页面已关闭, 可能验证已通过")
                    return True
                return False

            n_tiles = len(tile_images)
            is_4x4 = (n_tiles == 16) or (grid_sz[0] == 4 and grid_sz[1] == 4)
            cls_available = self._yolo_detector.cls_loaded and _YOLODetector.can_handle_cls(prompt_text)

            # 三引擎: SEG(4x4) → CLS(非4x4 或 SEG失败回退) → CLIP
            results = None

            # 引擎 1: SEG (仅 4x4)
            if is_4x4 and self._yolo_detector.seg_loaded and _YOLODetector.can_handle_seg(prompt_text) and tbl_path:
                logger.info(f"[Image] 第 {round_num + 1} 轮: SEG 引擎 (4x4)")
                results = await asyncio.get_event_loop().run_in_executor(
                    None,
                    self._yolo_detector.detect_grid_cells,
                    tbl_path,
                    prompt_text,
                    grid_sz[0],
                    grid_sz[1],
                )
                if results and sum(1 for m, _ in results if m) == 0:
                    results = None

            # 引擎 2: CLS (非 4x4 用绝对阈值, 4x4 用 ranking 模式)
            if results is None and cls_available:
                grid_desc = f"{grid_sz[0]}x{grid_sz[1]}" if grid_sz else "?"
                if is_4x4:
                    cls_top_k = getattr(config, "IMAGE_TOP_K_4X4", 4)
                    logger.info(f"[Image] 第 {round_num + 1} 轮: CLS 引擎 ({grid_desc} ranking模式)")
                    results = await asyncio.get_event_loop().run_in_executor(
                        None,
                        self._yolo_detector.classify_tiles_ranked,
                        tile_images,
                        prompt_text,
                        cls_top_k,
                    )
                else:
                    logger.info(f"[Image] 第 {round_num + 1} 轮: CLS 引擎 ({grid_desc})")
                    results = await asyncio.get_event_loop().run_in_executor(
                        None,
                        self._yolo_detector.classify_tiles,
                        tile_images,
                        prompt_text,
                    )
                if results and sum(1 for m, _ in results if m) == 0:
                    results = None

            # 引擎 3: CLIP 回退 (带硬上限)
            if results is None:
                logger.info(f"[Image] 第 {round_num + 1} 轮: CLIP 引擎")
                expected = (
                    getattr(config, "IMAGE_TOP_K_4X4", 4)
                    if n_tiles >= 16
                    else getattr(config, "IMAGE_TOP_K_3X3", 3)
                )
                results = self._classifier.classify_tiles_ranked(
                    tile_images, prompt_text, top_k=expected
                )
                if sum(1 for m, _ in results if m) == 0:
                    raw = self._classifier.classify_tiles(tile_images, prompt_text)
                    matched_idx = [i for i, (m, _) in enumerate(raw) if m]
                    if len(matched_idx) > expected:
                        scored = [(i, raw[i][1]) for i in matched_idx]
                        scored.sort(key=lambda x: x[1], reverse=True)
                        keep = set(idx for idx, _ in scored[:expected])
                        results = [(i in keep, raw[i][1]) for i in range(len(raw))]
                    else:
                        results = raw

            match_count = sum(1 for m, _ in results if m) if results else 0
            if match_count == 0:
                logger.warning("[Image] 多轮挑战 0 匹配, 跳过 Verify/Skip 并刷新挑战")
                return False

            clicked_count = await self._click_matching_tiles(bframe, results)
            if clicked_count == 0:
                logger.warning("[Image] 多轮挑战 0 个 tile 被点击, 跳过 Verify/Skip")
                return False
            await asyncio.sleep(1)
            if not await self._click_verify(bframe, clicked_count=clicked_count):
                return False
            await asyncio.sleep(3)

        return await self._is_checked()

    # ========================================================
    # 页面操作
    # ========================================================
    async def _click_checkbox(self) -> bool:
        """点击 reCAPTCHA checkbox"""
        try:
            # 先在主页面滚动到 reCAPTCHA 容器, 确保 iframe 在视口内
            try:
                await self.page.evaluate(
                    """() => {
                        const el = document.querySelector('#g-recaptcha') ||
                                   document.querySelector('.g-recaptcha') ||
                                   document.querySelector('[data-sitekey]');
                        if (el) {
                            el.scrollIntoView({behavior: 'instant', block: 'center'});
                        }
                        // 也滚动到 reCAPTCHA 标签
                        const labels = document.querySelectorAll('label');
                        for (const label of labels) {
                            if (label.textContent && label.textContent.includes('robot')) {
                                label.scrollIntoView({behavior: 'instant', block: 'center'});
                                break;
                            }
                        }
                    }"""
                )
                await asyncio.sleep(1)
            except Exception:
                pass

            frame = await self._get_recaptcha_frame("anchor")
            if not frame:
                logger.warning("[Image] 未找到 reCAPTCHA anchor iframe")
                return False

            checkbox = frame.locator(".recaptcha-checkbox-border")

            # 尝试多种点击方式
            try:
                await checkbox.click(force=True, timeout=5000)
            except Exception:
                # 回退: 用 JavaScript 点击
                logger.info("[Image] 常规点击失败, 尝试 JavaScript 点击...")
                await frame.evaluate(
                    """() => {
                        const cb = document.querySelector('.recaptcha-checkbox-border');
                        if (cb) cb.click();
                    }"""
                )

            logger.info("[Image] 已点击 reCAPTCHA checkbox")
            return True
        except Exception as e:
            logger.warning(f"[Image] 点击 checkbox 失败: {e}")
            return False

    async def _is_checked(self) -> bool:
        """检查 reCAPTCHA 是否已通过"""
        try:
            frame = await self._get_recaptcha_frame("anchor")
            if not frame:
                return False
            checkbox = frame.locator(".recaptcha-checkbox")
            aria_checked = await checkbox.get_attribute("aria-checked")
            return aria_checked == "true"
        except Exception:
            return False

    async def _extract_prompt(self, bframe) -> str:
        """从挑战弹窗提取提示文本"""
        try:
            prompt_el = bframe.locator(".rc-imageselect-instructions")
            if await prompt_el.count() > 0:
                raw_text = await prompt_el.first.inner_text()
                
                # 规范化: 将换行和多余空格合并为单个空格
                # reCAPTCHA 常将 "with a\ncar" 分行显示, 导致前缀匹配失败
                normalized = re.sub(r"\s+", " ", raw_text).strip().lower()

                # 去掉常见前缀, 提取目标对象
                # 注意: 前缀顺序很重要, 长前缀优先
                prefixes = [
                    "select all images with a ",
                    "select all squares with a ",
                    "select all images with an ",
                    "select all squares with an ",
                    "select all images with the ",
                    "select all squares with the ",
                    "select all images with ",
                    "select all squares with ",
                    "select all images ",
                    "select all squares ",
                ]
                for prefix in prefixes:
                    if normalized.startswith(prefix):
                        # 取前缀之后的文本 (用规范化后的文本提取)
                        target = normalized[len(prefix):].strip()
                        # 去掉 "if there are none, click skip" 后缀 (reCAPTCHA 常见指令)
                        target = re.sub(
                            r"\s*if there are none,?\s*click skip.*$",
                            "",
                            target,
                            flags=re.I,
                        ).strip()
                        # 去掉可能的 "tap on tiles" / "then click verify" 等后缀
                        target = re.sub(
                            r"\s+(?:tap on|then click|click verify).*$",
                            "",
                            target,
                            flags=re.I,
                        ).strip()
                        if target:
                            return target

                # 回退: 返回非 "select"/"tap" 开头的行
                for line in raw_text.strip().split("\n"):
                    line = line.strip()
                    if line and not line.lower().startswith("select") and not line.lower().startswith("tap"):
                        return line

                return raw_text.strip()
        except Exception as e:
            logger.warning(f"[Image] 提取提示文本失败: {e}")
        return ""

    async def _capture_tiles(self, bframe) -> tuple[list, tuple[int, int], str | None]:
        """
        截取挑战图片并切分为 tiles (优化版)
        
        优化: 只截一次全图, 用 PIL 裁剪各 tile
        - 消除逐 tile screenshot 导致的多次闪屏
        - 避免 element not stable 超时
        - 速度更快 (1 次 screenshot vs N 次)
        - 同时返回完整表格截图路径 (供 YOLO 使用)
        
        返回: (tile_images, (rows, cols), table_path)
        """
        tiles = []
        grid_size = (3, 3)
        table_path = None

        # 防御: 页面或 bframe 已关闭时直接返回 (验证通过后页面可能已导航)
        if self.page is None or self.page.is_closed():
            logger.warning("[Image] 页面已关闭, 跳过截图")
            return [], grid_size, None
        try:
            if bframe is None or bframe.is_closed():
                logger.warning("[Image] bframe 已关闭, 跳过截图")
                return [], grid_size, None
        except Exception:
            pass

        try:
            # 等待图像加载 (挑战刷新时需要更长等待)
            await asyncio.sleep(3)

            target = bframe.locator(".rc-imageselect-target")
            if await target.count() == 0:
                logger.warning("[Image] 未找到图像挑战网格")
                return [], grid_size, None

            # 获取所有可点击 tile (<td> 元素) 数量
            tile_els = bframe.locator(".rc-imageselect-target td")
            tile_count = await tile_els.count()
            logger.info(f"[Image] 找到 {tile_count} 个 tile 元素")

            if tile_count == 0:
                # 回退: 截取整个表格并按 3x3 切分
                challenge_path = os.path.join(self._temp_dir, "challenge_full.png")
                await target.first.screenshot(path=challenge_path, timeout=10000)
                return self._split_image(challenge_path, 3, 3), (3, 3), challenge_path

            # 检测网格大小
            if tile_count == 16:
                grid_size = (4, 4)
            elif tile_count == 9:
                grid_size = (3, 3)
            elif tile_count == 12:
                grid_size = (3, 4)
            elif tile_count == 32:
                grid_size = (4, 8)
            elif tile_count == 28:
                grid_size = (4, 7)
            else:
                side = int(tile_count ** 0.5)
                if side * side == tile_count:
                    grid_size = (side, side)
                elif tile_count % 4 == 0:
                    grid_size = (4, tile_count // 4)
                elif tile_count % 3 == 0:
                    grid_size = (3, tile_count // 3)
                else:
                    grid_size = (3, tile_count // 3)

            # === 统一截图策略: frame 内 table 元素截图优先 ===
            # 核心改进:
            # 1. 不再对 bframe iframe 施加 position:fixed (导致 Playwright "element not visible" 超时 30s)
            # 2. 不再对 table 施加 CSS transform (导致 "element not stable" 超时)
            # 3. 优先使用 frame 内 table 元素截图 (不依赖父页面 iframe 可见性)
            # 4. 回退使用 page.screenshot(clip) + iframe 偏移坐标
            # 5. 最终兜底: 全页截图 + PIL 裁剪
            table_path = os.path.join(self._temp_dir, "table_full.png")
            screenshot_ok = False
            table_box = None
            tile_boxes = []
            screenshot_method = "none"  # 当前截图方法 (用于陈旧检测和方法切换跟踪)
            screenshot_scale = 1.0  # 截图像素缩放比 (1.0=CSS像素, dpi=物理像素)

            # Step 1: 强制 bframe 到可见固定位置 (解决负 Y 坐标问题)
            # 核心修复: bframe iframe 可能被 reCAPTCHA 定位到视口外 (负 Y),
            # 导致 page.screenshot(clip) 无法捕获和 locator.screenshot() 超时.
            # 强制 position:fixed + 正坐标确保 iframe 在可见区域内.
            self._bframe_original_style = await self.page.evaluate(
                """() => {
                    const iframe = document.querySelector('iframe[src*="bframe"]');
                    if (!iframe) return null;
                    const orig = iframe.style.cssText;
                    iframe.style.cssText = 'position: fixed !important; '
                        + 'top: 30px !important; left: 50px !important; '
                        + 'width: 400px !important; height: 600px !important; '
                        + 'z-index: 99999 !important;';
                    return orig;
                }"""
            )
            await asyncio.sleep(0.5)

            # Step 2: 在 bframe 内部, 仅做 scrollIntoView (不做 CSS transform)
            try:
                await bframe.evaluate(
                    """() => {
                        window.scrollTo(0, 0);
                        document.documentElement.scrollTop = 0;
                        document.body.scrollTop = 0;
                        const target = document.querySelector('.rc-imageselect-target');
                        if (target) {
                            // 清除可能残留的 transform
                            target.style.transform = '';
                            target.scrollIntoView({block: 'start', behavior: 'instant'});
                        }
                    }"""
                )
                await asyncio.sleep(0.3)
            except Exception as e:
                logger.warning(f"[Image] bframe 内部滚动调整异常: {e}")

            # Step 3: 获取表格和 tile 的 bframe 相对坐标
            try:
                boxes_data = await bframe.evaluate(
                    """() => {
                        const target = document.querySelector('.rc-imageselect-target');
                        if (!target) return null;
                        const tableBox = target.getBoundingClientRect();
                        const tds = target.querySelectorAll('td');
                        const tileBoxes = Array.from(tds).map(td => {
                            const rect = td.getBoundingClientRect();
                            return {x: rect.x, y: rect.y, width: rect.width, height: rect.height};
                        });
                        return {tableBox: {x: tableBox.x, y: tableBox.y, width: tableBox.width, height: tableBox.height}, tileBoxes};
                    }"""
                )
            except Exception as e:
                logger.warning(f"[Image] 获取 bframe 坐标失败: {e}")
                boxes_data = None

            # Step 4: 截图 — 三级策略
            if boxes_data and boxes_data["tableBox"] and boxes_data["tableBox"]["width"] > 10:
                tbl = boxes_data["tableBox"]
                tile_boxes = boxes_data["tileBoxes"]

                # 4a: frame 内 table 元素截图 (最可靠, 不依赖父页面 iframe 可见性)
                # 双重超时保护: Playwright timeout=2000 + asyncio.wait_for(3s) 硬超时
                # 即使 Playwright 内部 CDP 调用无法取消, asyncio.wait_for 也会在 3s 后
                # 抛出 TimeoutError, 让 fallback 立即执行 (避免 30s 卡死)
                try:
                    target_el = bframe.locator('.rc-imageselect-target')
                    try:
                        await asyncio.wait_for(
                            target_el.screenshot(
                                path=table_path,
                                timeout=2000,
                                animations="disabled",
                            ),
                            timeout=3.0,
                        )
                    except asyncio.TimeoutError:
                        raise Exception("asyncio.wait_for 超时 (3s), Playwright screenshot 可能仍在后台运行")
                    screenshot_ok = True
                    screenshot_method = "frame_screenshot"
                    screenshot_scale = 1.0  # Playwright frame 截图 = CSS 像素
                    # frame 内截图原点 = table 左上角
                    # tile 坐标是 bframe 相对坐标, 需转为 table 相对坐标
                    table_box = {
                        "x": tbl["x"],
                        "y": tbl["y"],
                        "width": tbl["width"],
                        "height": tbl["height"],
                    }
                    tile_boxes = [
                        {
                            "x": tb["x"],
                            "y": tb["y"],
                            "width": tb["width"],
                            "height": tb["height"],
                        }
                        for tb in tile_boxes
                    ]
                    logger.info(
                        f"[Image] frame 内 table 截图成功: "
                        f"table=({tbl['width']:.0f}x{tbl['height']:.0f}), "
                        f"tiles={len(tile_boxes)}"
                    )
                except Exception as e_frame:
                    logger.warning(f"[Image] frame 内 table 截图失败: {e_frame}")

                # 4b: page.screenshot(clip) (使用 iframe 偏移 + 表格坐标)
                # 页面状态检查 (frame 截图超时期间页面可能已关闭)
                if not screenshot_ok and (self.page is None or self.page.is_closed()):
                    logger.info("[Image] page.screenshot(clip) 前发现页面已关闭, 可能验证已通过")
                    return [], grid_size, None
                if not screenshot_ok:
                    try:
                        iframe_offset = await self.page.evaluate(
                            """() => {
                                const iframe = document.querySelector('iframe[src*="bframe"]');
                                if (!iframe) return null;
                                const rect = iframe.getBoundingClientRect();
                                return {x: rect.x, y: rect.y, width: rect.width, height: rect.height};
                            }"""
                        )
                        if iframe_offset:
                            page_x = iframe_offset["x"] + tbl["x"]
                            page_y = iframe_offset["y"] + tbl["y"]
                            logger.info(
                                f"[Image] page.screenshot(clip) 坐标: "
                                f"iframe=({iframe_offset['x']:.0f},{iframe_offset['y']:.0f}), "
                                f"table=({tbl['x']:.0f},{tbl['y']:.0f}), "
                                f"page=({page_x:.0f},{page_y:.0f}), "
                                f"size=({tbl['width']:.0f}x{tbl['height']:.0f})"
                            )
                            if page_x >= 0 and page_y >= 0:
                                try:
                                    await asyncio.wait_for(
                                        self.page.screenshot(
                                            path=table_path, timeout=2000,
                                            animations="disabled", caret="hide",
                                            clip={
                                                "x": page_x, "y": page_y,
                                                "width": tbl["width"], "height": tbl["height"],
                                            },
                                        ),
                                        timeout=3.0,
                                    )
                                except asyncio.TimeoutError:
                                    raise Exception("asyncio.wait_for 超时 (3s), page.screenshot(clip)")
                                screenshot_ok = True
                                screenshot_method = "page_clip"
                                screenshot_scale = 1.0  # Playwright page 截图 = CSS 像素
                                table_box = tbl
                                logger.info(
                                    f"[Image] page.screenshot(clip) 成功: "
                                    f"table=({tbl['width']:.0f}x{tbl['height']:.0f})"
                                )
                            else:
                                logger.warning(
                                    f"[Image] page.screenshot(clip) 跳过: "
                                    f"坐标为负 ({page_x:.0f},{page_y:.0f})"
                                )
                    except Exception as e_clip:
                        logger.warning(f"[Image] page.screenshot(clip) 失败: {e_clip}")

                # 4c: 兜底 — 全页截图 + PIL 裁剪
                if not screenshot_ok:
                    # 页面状态检查 (验证通过后页面可能已关闭)
                    if self.page is None or self.page.is_closed():
                        logger.info("[Image] 全页截图前发现页面已关闭, 可能验证已通过")
                        return [], grid_size, None
                    try:
                        full_page_path = os.path.join(self._temp_dir, "full_page_fallback.png")
                        # 使用 asyncio.wait_for 包装, 防止 Playwright 内部超时不生效
                        try:
                            await asyncio.wait_for(
                                self.page.screenshot(
                                    path=full_page_path, timeout=2000, full_page=False,
                                    animations="disabled", caret="hide",
                                ),
                                timeout=3.0,
                            )
                        except asyncio.TimeoutError:
                            raise Exception("asyncio.wait_for 超时 (3s), 全页截图")
                        # 使用 PIL 从全页截图中裁剪表格区域
                        iframe_offset = await self.page.evaluate(
                            """() => {
                                const iframe = document.querySelector('iframe[src*="bframe"]');
                                if (!iframe) return null;
                                const rect = iframe.getBoundingClientRect();
                                return {x: rect.x, y: rect.y};
                            }"""
                        )
                        if iframe_offset:
                            crop_x = int(iframe_offset["x"] + tbl["x"])
                            crop_y = int(iframe_offset["y"] + tbl["y"])
                            crop_w = int(tbl["width"])
                            crop_h = int(tbl["height"])
                            full_img = Image.open(full_page_path)
                            # 确保 crop 区域在图片范围内
                            img_w, img_h = full_img.size
                            crop_x = max(0, min(crop_x, img_w - 10))
                            crop_y = max(0, min(crop_y, img_h - 10))
                            crop_w = min(crop_w, img_w - crop_x)
                            crop_h = min(crop_h, img_h - crop_y)
                            cropped = full_img.crop((crop_x, crop_y, crop_x + crop_w, crop_y + crop_h))
                            if cropped.mode != "RGB":
                                cropped = cropped.convert("RGB")
                            cropped.save(table_path)
                            screenshot_ok = True
                            screenshot_method = "full_page_crop"
                            screenshot_scale = 1.0  # Playwright 全页截图 = CSS 像素
                            table_box = tbl
                            logger.warning(
                                f"[Image] 全页截图裁剪成功 (兜底): "
                                f"crop=({crop_x},{crop_y},{crop_w}x{crop_h})"
                            )
                    except Exception as e3:
                        logger.warning(f"[Image] 全页截图也失败: {e3}")

            # Step 5: OS 级屏幕捕获 (绕过 Playwright/CDP, 直接从屏幕缓冲区截取)
            # 当 Playwright 在跨域 iframe 上截图持续超时时, 这是最终可靠方案
            # 原理: PIL.ImageGrab.grab() 使用 Win32 BitBlt, 不依赖 CDP 协议
            if not screenshot_ok and boxes_data and boxes_data["tableBox"]:
                # 页面状态检查 (验证通过后页面可能已关闭)
                if self.page is None or self.page.is_closed():
                    logger.info("[Image] OS 截图前发现页面已关闭, 可能验证已通过")
                    return [], grid_size, None
                try:
                    tbl_os = boxes_data["tableBox"]
                    # 获取 iframe 在页面中的偏移 + 窗口信息
                    win_info = await self.page.evaluate(
                        """() => ({
                            outerW: window.outerWidth,
                            outerH: window.outerHeight,
                            innerW: window.innerWidth,
                            innerH: window.innerHeight,
                            screenX: window.screenX,
                            screenY: window.screenY,
                            devicePixelRatio: window.devicePixelRatio
                        })"""
                    )
                    iframe_offset_os = await self.page.evaluate(
                        """() => {
                            const iframe = document.querySelector('iframe[src*="bframe"]');
                            if (!iframe) return null;
                            const rect = iframe.getBoundingClientRect();
                            return {x: rect.x, y: rect.y, width: rect.width, height: rect.height};
                        }"""
                    )
                    if win_info and iframe_offset_os:
                        # 计算 table 在页面中的 CSS 坐标
                        table_css_x = iframe_offset_os["x"] + tbl_os["x"]
                        table_css_y = iframe_offset_os["y"] + tbl_os["y"]
                        table_css_w = tbl_os["width"]
                        table_css_h = tbl_os["height"]

                        # 计算 DPI 缩放比
                        # devicePixelRatio 在某些 Chrome profile 下可能返回 1.0 (不准确)
                        # 使用 Win32 API 获取真实 DPI 作为校准
                        dpi = win_info.get("devicePixelRatio", 1.5)
                        try:
                            import ctypes
                            # GetDpiForSystem() 返回系统 DPI (96=100%, 144=150%)
                            # 在 Windows 10 1607+ 可用
                            user32 = ctypes.windll.user32
                            user32.SetProcessDPIAware()
                            system_dpi = user32.GetDpiForSystem()
                            if system_dpi > 0:
                                win_dpi = system_dpi / 96.0
                                if abs(win_dpi - dpi) > 0.1:
                                    logger.info(
                                        f"[Image] DPI 校准: devicePixelRatio={dpi:.2f} → "
                                        f"Win32 系统DPI={win_dpi:.2f} ({system_dpi})"
                                    )
                                    dpi = win_dpi
                        except Exception:
                            pass

                        if win_info["outerW"] > 0 and win_info["outerH"] > 0:
                            pass  # dpi 已通过上述校准
                        else:
                            dpi = 1.5

                        # Chrome UI 高度 (物理像素)
                        # 修正: 旧方法 (outerH - innerH) * dpi 包含窗口边框,
                        # 但 screenY 已包含边框位置, 导致 Y 轴偏移
                        # 新方法: 使用 Win32 GetClientRect 获取无边框客户端高度,
                        # 减去视口物理高度得到纯 Chrome UI 高度
                        try:
                            import win32gui
                            # 查找 Chrome 主窗口 (与 Native runtime 相同的过滤逻辑)
                            chrome_hwnd = None
                            def _enum_chrome(hwnd, _):
                                nonlocal chrome_hwnd
                                try:
                                    import win32process
                                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                                    cls = win32gui.GetClassName(hwnd)
                                    if cls == "Chrome_WidgetWin_1":
                                        import subprocess
                                        try:
                                            p = subprocess.run(
                                                ["wmic", "process", "where", f"ProcessId={pid}",
                                                 "get", "Name"],
                                                capture_output=True, text=True, timeout=3,
                                            )
                                            if "chrome.exe" in p.stdout:
                                                chrome_hwnd = hwnd
                                        except Exception:
                                            pass
                                except Exception:
                                    pass
                            win32gui.EnumWindows(_enum_chrome, None)
                            if chrome_hwnd:
                                _, _, cw, ch = win32gui.GetClientRect(chrome_hwnd)
                                viewport_h_phys = win_info["innerH"] * dpi
                                chrome_ui_h = ch - viewport_h_phys
                                logger.info(
                                    f"[Image] OS 截图 Chrome UI 校准: "
                                    f"client_h={ch}, viewport_h_phys={viewport_h_phys:.0f}, "
                                    f"chrome_ui_h={chrome_ui_h:.1f} (Win32 客户端区域法)"
                                )
                            else:
                                raise Exception("未找到 Chrome 窗口")
                        except Exception:
                            # 回退: 使用旧方法但减去估计的边框高度
                            # 最大化窗口边框约 24px (12 top + 12 bottom), 取上边框约 11px
                            border_est = 11
                            chrome_ui_h = (win_info["outerH"] - win_info["innerH"]) * dpi - border_est
                            logger.info(
                                f"[Image] OS 截图 Chrome UI 回退: "
                                f"chrome_ui_h={chrome_ui_h:.1f} (估计边框 {border_est}px)"
                            )

                        # table 物理屏幕坐标
                        # 使用 Win32 ClientToScreen 获取客户端区域原点 (排除窗口边框),
                        # 再加上 Chrome UI 高度得到视口原点
                        # 这避免了 screenY (含边框) 与 chrome_ui_h (已排除边框) 的混用问题
                        try:
                            if chrome_hwnd:
                                client_origin = win32gui.ClientToScreen(chrome_hwnd, (0, 0))
                                viewport_origin_x = float(client_origin[0])
                                viewport_origin_y = float(client_origin[1]) + chrome_ui_h
                            else:
                                raise Exception("无 chrome_hwnd")
                        except Exception:
                            # 回退: screenY * dpi + chrome_ui_h (可能含边框误差)
                            viewport_origin_x = win_info["screenX"] * dpi
                            viewport_origin_y = win_info["screenY"] * dpi + chrome_ui_h

                        phys_x = int(viewport_origin_x + table_css_x * dpi)
                        phys_y = int(viewport_origin_y + table_css_y * dpi)
                        phys_w = int(table_css_w * dpi)
                        phys_h = int(table_css_h * dpi)

                        logger.info(
                            f"[Image] OS 截图: CSS=({table_css_x:.0f},{table_css_y:.0f}), "
                            f"DPI={dpi:.2f}, ChromeUI={chrome_ui_h:.0f}, "
                            f"Phys=({phys_x},{phys_y},{phys_w}x{phys_h})"
                        )

                        # 使用 PIL.ImageGrab 直接从屏幕捕获
                        from PIL import ImageGrab
                        bbox = (phys_x, phys_y, phys_x + phys_w, phys_y + phys_h)
                        os_img = ImageGrab.grab(bbox=bbox)

                        if os_img.mode != "RGB":
                            os_img = os_img.convert("RGB")
                        os_img.save(table_path)
                        screenshot_ok = True
                        screenshot_method = "os_capture"
                        screenshot_scale = dpi  # OS 截图 = 物理像素, 需按 DPI 缩放 tile 坐标
                        table_box = tbl_os
                        # OS 截图原点 = table 左上角 (与 frame 内截图一致)
                        # tile_boxes 已是 bframe 相对坐标, 需转为 table 相对坐标
                        # 注意: OS 截图像素为物理像素, tile 坐标为 CSS 像素,
                        # 裁剪时需乘 screenshot_scale (=dpi) 进行坐标转换
                        logger.info(
                            f"[Image] ✓ OS 级屏幕捕获成功: "
                            f"size={os_img.size[0]}x{os_img.size[1]}, "
                            f"scale={screenshot_scale:.2f}"
                        )
                except Exception as e_os:
                    logger.warning(f"[Image] OS 级屏幕捕获失败: {e_os}")

            # Step 6: 最终回退 — 页面截图 (坐标可能不准)
            if not screenshot_ok:
                # 页面状态检查 (验证通过后页面可能已关闭)
                if self.page is None or self.page.is_closed():
                    logger.info("[Image] 最终回退前发现页面已关闭, 可能验证已通过")
                    return [], grid_size, None
                try:
                    await self.page.screenshot(
                        path=table_path, timeout=3000, full_page=False,
                        caret="hide",
                    )
                    screenshot_ok = True
                    screenshot_method = "page_fallback"
                    screenshot_scale = 1.0
                    logger.warning("[Image] 使用页面截图回退 (坐标可能不准)")
                    table_box = None
                    tile_boxes = []
                except Exception as e3:
                    logger.warning(f"[Image] 页面截图也失败: {e3}")
                    return [], grid_size, None

            table_img = Image.open(table_path)
            if table_img.mode != "RGB":
                table_img = table_img.convert("RGB")
            table_w, table_h = table_img.size

            # 截图内容验证: 检查图像是否有足够方差 (排除空白/stale 截图)
            import numpy as np
            img_array = np.array(table_img)
            pixel_std = float(img_array.std())
            if pixel_std < 5.0:
                logger.warning(
                    f"[Image] 截图内容疑似空白 (std={pixel_std:.2f} < 5.0), "
                    f"可能捕获到错误区域, 放弃本轮"
                )
                return [], grid_size, None
            logger.info(f"[Image] 截图内容验证通过 (std={pixel_std:.2f})")

            # 陈旧截图检测: 对比当前截图与上一轮的像素数据
            # 注意: MD5 哈希对比对 fallback crop 不可靠 (裁剪边缘可能有亚像素差异),
            # 改用像素均值差异判断 (mean_diff < 1.0 视为同一张图)
            #
            # 方法切换检测: 不同截图方法 (Playwright vs OS 级) 产生不同分辨率/渲染的图像,
            # 直接比较会导致误判。切换时重置 stale_count 和上一轮数组。
            if self._last_screenshot_method is not None and self._last_screenshot_method != screenshot_method:
                logger.info(
                    f"[Image] 截图方法切换: {self._last_screenshot_method} → {screenshot_method}, "
                    f"重置陈旧检测基准"
                )
                self._stale_count = 0
                self._last_table_array = None  # 不同方法/分辨率的截图不可比较
            self._last_screenshot_method = screenshot_method

            if self._last_table_array is not None and self._last_table_array.shape == img_array.shape:
                diff = np.abs(img_array.astype(np.int16) - self._last_table_array.astype(np.int16))
                mean_diff = float(diff.mean())
                max_diff = float(diff.max())
                if mean_diff < 1.0:
                    self._stale_count += 1
                    logger.warning(
                        f"[Image] 截图与上一轮几乎相同 (mean_diff={mean_diff:.4f}, max_diff={max_diff:.0f}), "
                        f"连续陈旧 {self._stale_count} 次 — 挑战可能未刷新"
                    )
                    if self._stale_count >= 3:
                        logger.error("[Image] 连续 3 次陈旧截图, 放弃求解")
                        return [], grid_size, None
                else:
                    self._stale_count = 0
            else:
                self._stale_count = 0
            self._last_table_array = img_array.copy()

            rows, cols = grid_size
            tile_w = table_w / cols
            tile_h = table_h / rows

            # 裁剪各 tile (纯 PIL 操作, 无浏览器交互, 不闪屏)
            # table_box 和 tile_boxes 已在截图阶段获取 (iframe 相对坐标, CSS 像素)
            # 注意: OS 级截图的像素为物理像素, 需乘 screenshot_scale 转换坐标
            for i in range(tile_count):
                box = tile_boxes[i] if i < len(tile_boxes) else None
                if box and table_box:
                    # 截图原点 = 表格左上角, tile 用相对坐标 (减去表格偏移)
                    # 乘 screenshot_scale 将 CSS 像素坐标转为截图像素坐标
                    crop_x = (box["x"] - table_box["x"]) * screenshot_scale
                    crop_y = (box["y"] - table_box["y"]) * screenshot_scale
                    crop_w = box["width"] * screenshot_scale
                    crop_h = box["height"] * screenshot_scale
                    crop_box = (
                        max(0, int(crop_x)),
                        max(0, int(crop_y)),
                        int(crop_x + crop_w),
                        int(crop_y + crop_h),
                    )
                else:
                    # 回退: 按网格均分
                    row = i // cols
                    col = i % cols
                    crop_box = (
                        int(col * tile_w),
                        int(row * tile_h),
                        int((col + 1) * tile_w),
                        int((row + 1) * tile_h),
                    )

                # 裁剪并添加内缩 (去掉 tile 边框线)
                # OS 级截图下边框线宽度随 DPI 放大, 需相应增加内缩量
                inset = max(1, int(round(screenshot_scale)))
                crop_box = (
                    crop_box[0] + inset,
                    crop_box[1] + inset,
                    crop_box[2] - inset,
                    crop_box[3] - inset,
                )
                tile_img = table_img.crop(crop_box)
                tiles.append(tile_img)

            logger.info(f"[Image] 裁剪完成: {len(tiles)} tiles (单次截图 + PIL 裁剪, 无闪屏)")

            # 保存调试截图 (复用已有截图, 不额外截图)
            if config.SAVE_SCREENSHOTS:
                import shutil
                self._capture_seq += 1
                debug_path = os.path.join(
                    self.screenshot_dir, f"image_challenge_{tile_count}.png"
                )
                shutil.copy2(table_path, debug_path)
                seq_debug_path = os.path.join(
                    self.screenshot_dir,
                    f"image_challenge_{self._capture_seq:02d}_{tile_count}.png",
                )
                shutil.copy2(table_path, seq_debug_path)
                logger.info(f"[Image] 调试截图已保存: {seq_debug_path}")

        except Exception as e:
            logger.warning(f"[Image] 截取 tiles 失败: {e}", exc_info=True)

        return tiles, grid_size, table_path

    def _split_image(self, image_path: str, rows: int, cols: int) -> list:
        """将完整截图按 rows x cols 切分 (回退方案)"""
        try:
            img = Image.open(image_path)
            if img.mode != "RGB":
                img = img.convert("RGB")
            w, h = img.size
            tw, th = w / cols, h / rows
            tiles = []
            for row in range(rows):
                for col in range(cols):
                    tile = img.crop(
                        (int(col * tw), int(row * th), int((col + 1) * tw), int((row + 1) * th))
                    )
                    tiles.append(tile)
            return tiles
        except Exception as e:
            logger.warning(f"[Image] 图片切分失败: {e}")
            return []

    async def _click_matching_tiles(self, bframe, match_results: list[tuple[bool, float]]) -> int:
        """
        点击匹配的 tiles

        策略优化: bframe iframe 被 CSS 强制固定后, Playwright 常规 click() 会超时
        (元素 visible 但 Playwright 内部状态认为不可交互)
        因此优先使用 JS click() (bframe.evaluate), 避免超时等待
        """
        tile_els = bframe.locator(".rc-imageselect-target td")
        tile_count = await tile_els.count()
        clicked_count = 0

        for i, (is_match, score) in enumerate(match_results):
            if is_match and i < tile_count:
                # 优先 JS 点击 (bframe 被 CSS 固定后, 常规 click 必定超时)
                try:
                    await bframe.evaluate(
                        """(idx) => {
                            const tds = document.querySelectorAll('.rc-imageselect-target td');
                            if (tds[idx]) tds[idx].click();
                        }""",
                        i,
                    )
                    clicked_count += 1
                    logger.info(f"[Image] JS 点击 tile [{i}] 成功 (score={score:.4f})")
                except Exception as e_js:
                    # JS 失败时尝试常规点击 (极少触发, 仅在 bframe 未被固定时)
                    logger.warning(f"[Image] JS 点击 tile [{i}] 失败, 尝试常规点击: {e_js}")
                    try:
                        await tile_els.nth(i).click(timeout=3000)
                        clicked_count += 1
                        logger.info(f"[Image] 常规点击 tile [{i}] 成功 (score={score:.4f})")
                    except Exception as e2:
                        logger.warning(f"[Image] 常规点击 tile [{i}] 也失败: {e2}")
                await asyncio.sleep(0.3)
        return clicked_count

    async def _click_verify(self, bframe, clicked_count: int = 0) -> bool:
        """点击验证按钮 (带 JS 回退)"""
        try:
            verify_btn = bframe.locator("#recaptcha-verify-button")
            button_text = (await verify_btn.inner_text(timeout=2000)).strip()
            logger.info(
                f"[Image] 验证按钮文本: '{button_text}', clicked={clicked_count}"
            )
            if button_text.lower() == "skip" and clicked_count <= 0:
                logger.warning("[Image] 按钮为 Skip 且未选择 tile, 不点击")
                return False
            await verify_btn.click(force=True, timeout=5000)
            logger.info("[Image] 已点击验证按钮")
            return True
        except Exception as e:
            logger.warning(f"[Image] 常规点击验证失败, 尝试 JS: {e}")
            try:
                clicked = await bframe.evaluate(
                    """(clickedCount) => {
                        const btn = document.getElementById('recaptcha-verify-button');
                        if (!btn) return {ok: false, text: ''};
                        const text = (btn.innerText || btn.textContent || '').trim();
                        if (text.toLowerCase() === 'skip' && clickedCount <= 0) {
                            return {ok: false, text};
                        }
                        btn.click();
                        return {ok: true, text};
                    }""",
                    clicked_count,
                )
                logger.info(
                    f"[Image] JS 验证按钮结果: ok={clicked.get('ok')} "
                    f"text='{clicked.get('text')}'"
                )
                return bool(clicked.get("ok"))
            except Exception as e2:
                logger.warning(f"[Image] JS 点击验证也失败: {e2}")
                return False

    async def _reset_challenge(self):
        """重置挑战"""
        try:
            bframe = await self._get_recaptcha_frame("bframe")
            if bframe:
                reset_btn = bframe.locator("#recaptcha-reload-button")
                await reset_btn.click(force=True)
                await asyncio.sleep(2)
        except Exception:
            pass

    async def _restore_bframe_style(self):
        """恢复 bframe iframe 的原始 CSS 样式 (截图时被强制修改)"""
        try:
            orig_style = getattr(self, "_bframe_original_style", None)
            if orig_style is not None:
                await self.page.evaluate(
                    """(orig) => {
                        const iframe = document.querySelector('iframe[src*="bframe"]');
                        if (iframe) iframe.style.cssText = orig;
                    }""",
                    orig_style,
                )
                self._bframe_original_style = None
        except Exception:
            # 确保即使恢复失败也清除标记
            self._bframe_original_style = None
