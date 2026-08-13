# =================================================================
# lark-hls-v2 · card/__init__.py · 总导游图（改代码前必读，读完再动手）
# ▍这是什么（固定四问，问完才算完）
# ① 干什么：card 子包的入口，把所有子模块的公开 API 汇聚到一个命名空间
# ② 技术栈：Python import
# ③ 依赖：i18n、md、elements、builder、special、styles 六个子模块
# ④ 给谁看：需要 import card 任何符号的外部调用者
# ▍文件从上到下的结构
# 导入区：re-export 全部子模块的 __all__（noqa 允许星号导入）
# ▍修改铁律（都是血泪教训，详见 hls-plugin-dev 技能）
# 1. 加新子模块时必须同时在这里加 from .xxx import *，否则外部 import 不到。
# 2. import 顺序按依赖层排：i18n（最底层）→ md → elements → builder → special → styles。
#    别乱序，否则循环导入。
# 3. noqa 标记不能删——F401/F403 是星号导入的静态分析警告，删了 lint 会报错。
# =================================================================
"""Card module — Feishu Card 2.0 rendering primitives.

把 card 子包的所有公开符号 re-export 到一个入口。
外部只需要 from lark_hls_v2.card import xxx 即可。
"""

from .i18n import *          # noqa: F401,F403
from .md import *            # noqa: F401,F403
from .elements import *      # noqa: F401,F403
from .builder import *       # noqa: F401,F403
from .special import *       # noqa: F401,F403
from .styles import *        # noqa: F401,F403
