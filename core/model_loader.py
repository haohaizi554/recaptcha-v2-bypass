"""
模型预加载器 — 在子线程中加载重型 ML 模块
=============================================
torch (~2GB)、transformers、ultralytics 的 import 耗时数秒，
必须在工作线程完成，避免阻塞 GUI 主线程。

架构:
  ModelLoader(QThread)
    ├─ progress 信号 → UI 显示加载进度
    └─ ready 信号 → 模型实例就绪，可被 runtime 复用
"""

from __future__ import annotations

import logging
import os
import sys
import time

from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger("ModelLoader")

# 项目根目录
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


class ModelLoader(QThread):
    """
    子线程预加载重型 ML 模块 + 模型实例。

    信号:
      progress(str)      — 加载阶段描述
      ready(dict)        — 加载完成的模块/模型字典
      error(str)         — 加载失败信息
    """

    progress = pyqtSignal(str)
    ready = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cache: dict = {}
        self._stop_flag = False

    def run(self):
        """线程入口: 按依赖顺序加载模块"""
        try:
            self._load_torch()
            if self._stop_flag:
                return
            self._load_transformers()
            if self._stop_flag:
                return
            self._load_ultralytics()
            if self._stop_flag:
                return

            # 预实例化 YOLO 模型 (可选, 不阻塞)
            self._preload_yolo_models()

            self.ready.emit(self._cache)
            logger.info("模型预加载完成")

        except Exception as e:
            logger.error(f"模型预加载失败: {e}", exc_info=True)
            self.error.emit(str(e))

    def _load_torch(self):
        """加载 torch (~7s)"""
        if "torch" in self._cache:
            return
        self.progress.emit("正在加载 torch...")
        t0 = time.perf_counter()
        import torch
        self._cache["torch"] = torch
        elapsed = time.perf_counter() - t0
        device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info(f"torch 加载完成 ({elapsed:.1f}s, device={device})")

    def _load_transformers(self):
        """加载 transformers (~6s)"""
        if "transformers" in self._cache:
            return
        self.progress.emit("正在加载 transformers...")
        t0 = time.perf_counter()
        import transformers
        self._cache["transformers"] = transformers
        elapsed = time.perf_counter() - t0
        logger.info(f"transformers 加载完成 ({elapsed:.1f}s)")

    def _load_ultralytics(self):
        """加载 ultralytics (~3s)"""
        if "ultralytics" in self._cache:
            return
        self.progress.emit("正在加载 ultralytics...")
        t0 = time.perf_counter()
        import ultralytics
        self._cache["ultralytics"] = ultralytics
        elapsed = time.perf_counter() - t0
        logger.info(f"ultralytics 加载完成 ({elapsed:.1f}s)")

    def _preload_yolo_models(self):
        """
        预实例化 YOLO 模型, 避免首次运行时的加载延迟.
        失败不阻塞, runtime 会在运行时按需加载.
        """
        try:
            import config

            cls_path = getattr(config, "YOLO_CLS_MODEL_PATH", "")
            if cls_path and os.path.exists(cls_path):
                self.progress.emit("正在加载 YOLOv8-cls 模型...")
                from ultralytics import YOLO
                self._cache["yolo_cls"] = YOLO(cls_path)
                logger.info("YOLOv8-cls 模型预加载完成")

            seg_name = getattr(config, "YOLO_SEG_MODEL_NAME", "yolov8n-seg.pt")
            seg_path = os.path.join(_PROJECT_ROOT, seg_name)
            if os.path.exists(seg_path):
                self.progress.emit("正在加载 YOLOv8-seg 模型...")
                from ultralytics import YOLO
                self._cache["yolo_seg"] = YOLO(seg_path)
                logger.info("YOLOv8-seg 模型预加载完成")

        except Exception as e:
            logger.warning(f"YOLO 模型预加载跳过 (将在运行时加载): {e}")

    def get(self, key: str):
        """获取已加载的模块/模型 (主线程安全读)"""
        return self._cache.get(key)

    def is_ready(self, key: str) -> bool:
        """检查指定模块是否已加载"""
        return key in self._cache

    def stop(self):
        """请求停止加载"""
        self._stop_flag = True
