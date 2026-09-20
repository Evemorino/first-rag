"""坏例子：破坏幂等的 ID 生成方式。

来源依据：
CPython 开源标准库：
https://github.com/python/cpython/blob/main/Lib/uuid.py

其中 uuid4() 的文档明确写着：
Generate a random UUID.

下面的坏例子根据这个公开实现整理成教学反例，
用于说明为什么随机 ID 不适合本项目 FR-014 的幂等入库。
"""

# ===== 坏例子：常见错误 =====
#
# 这个例子故意展示下面这些问题：
# 1. 使用 uuid4()，每次运行都生成不同 ID；
# 2. 使用当前时间参与 ID，导致同一素材重跑后身份变化；
# 3. 没有把 source/date/text 全部纳入身份；
# 4. 在模块 import 时执行生成动作；
# 5. 没有“同样输入得到同样输出”的契约。

BAD_EXAMPLE = r'''
import time
import uuid

# 坏：uuid4() 是随机 UUID，CPython 文档明确说明 Generate a random UUID。
point_id = uuid.uuid4()

# 坏：时间参与 ID 后，同一条素材重跑会得到不同身份。
point_id = f"{time.time_ns()}-{point_id}"

print(point_id)  # 坏：模块顶层产生副作用。
'''
