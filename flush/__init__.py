# =================================================================
# lark-hls-v2/flush/__init__.py · 总导游图（改代码前必读，读完再动手）
# ▍这是什么
# ① 干什么：flush 子包的门面（facade）。re-export FlushController 和常量，
#    让外部 import 路径更短。
# ② 技术栈：纯 Python re-export。
# ③ 依赖：flush.controller。
# ④ 给谁看：新增节流常量的开发者（加了要在这里 re-export）。
# ▍修改铁律
# 1. 新增的公开常量【不】只在 controller.py 定义——必须在这里 re-export 并加到 __all__。
# 2. noqa: F401 不能删，否则 linter 会把 re-export 当无用 import 清掉。
# =================================================================



from .controller import (  # noqa: F401
    FlushController,
    CARDKIT_MS,
    LONG_GAP_MS,
    BATCH_AFTER_GAP_MS,
)

__all__ = [
    "FlushController",
    "CARDKIT_MS",
    "LONG_GAP_MS",
    "BATCH_AFTER_GAP_MS",
]
