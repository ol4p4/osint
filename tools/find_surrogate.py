"""Find lone surrogate in gen_dashboard.py source"""
import re
src = open(r'D:\osint\gen_dashboard.py', 'r', encoding='utf-8').read()
# 找形如 \uD8xx 或 \uD9xx 或 \uDAxx 或 \uDBxx 后面不跟 \uDCxx-\uDFxx
# 但 \u 在 Python 字符串里也是 escape
# 找原始字面 \uD8xx
# 实际: 找 chr(0xD800)-chr(0xDFFF) 在源中的位置
for i, line in enumerate(src.splitlines(), 1):
    for j, ch in enumerate(line):
        if 0xD800 <= ord(ch) <= 0xDFFF:
            # 是 lone surrogate
            ctx = line[max(0, j-20):j+20]
            print(f'L{i}:{j} ord=U+{ord(ch):04X}  ctx: {ctx!r}')
