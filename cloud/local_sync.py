# -*- coding: utf-8 -*-
r"""local_sync.py - 本地数据同步模块
职责：仓库(D:\osint, CI 产物) → 本地产物目录(D:\osint\data) 的 intel jsonl 合并、
可选本地翻译、运行日志。由产物目录的 refresh.py 调用（计划任务 OsintRefresh 每小时）。

合并策略：同 id 条目优先保留带 cn_title 的版本（直接复用 CI 的翻译成果）；
本地新条目保留；仓库新条目并入。
"""
import json
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

PROJECT = Path(r"D:\osint").resolve()
BASE = Path(r"D:\osint\data").resolve()
LOG_DIR = BASE / "logs"
ALLOWED_DIRS = (PROJECT, BASE)

# 文件名白名单：只允许按日期命名的最终情报文件和日志文件（YYYYMMDD 共 8 位数字），
# 显式排除 intel_raw_*/intel_final_* 及任何其他名字（防路径穿越、防脏数据混入）
INTEL_NAME_RE = re.compile(r"^intel_2\d{7}\.jsonl$")
LOG_NAME_RE = re.compile(r"^refresh_2\d{7}\.log$")


def check_inside(p):
    """规范化路径并校验必须位于 PROJECT 或 BASE 目录树内（防路径穿越）"""
    resolved = Path(p).resolve()
    if not any(resolved == d or d in resolved.parents for d in ALLOWED_DIRS):
        raise ValueError(f"path outside allowed dirs: {resolved}")
    return resolved


def safe_path(base, name, pattern):
    """文件名白名单校验 + 必须落在 base 目录内，返回规范化绝对路径"""
    if not pattern.match(name):
        raise ValueError(f"filename not allowed: {name!r}")
    p = (base / name).resolve()
    if p.parent != base:
        raise ValueError(f"path escapes base dir: {name!r}")
    return p


@contextmanager
def run_logging():
    """把 stdout 同时写入 logs/refresh_YYYYMMDD.log，保证计划任务运行留痕"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = safe_path(LOG_DIR, f"refresh_{datetime.now().strftime('%Y%m%d')}.log", LOG_NAME_RE)

    class Tee:
        def __init__(self, fh):
            self.fh = fh

        def write(self, s):
            self.fh.write(s)
            try:
                sys.__stdout__.write(s)
            except Exception:
                pass

        def flush(self):
            try:
                self.fh.flush()
                sys.__stdout__.flush()
            except Exception:
                pass

    fh = log_file.open("a", encoding="utf-8")
    old = sys.stdout
    sys.stdout = Tee(fh)
    try:
        yield log_file
    finally:
        sys.stdout = old
        fh.close()


def git_pull():
    if not (PROJECT / ".git").exists():
        print(f"[WARN] {PROJECT} 不是 git 仓库，跳过 pull")
        return False
    try:
        r = subprocess.run(["git", "pull", "origin", "master"], cwd=str(PROJECT),
                           capture_output=True, text=True, timeout=120)
        print(f"Git pull: {(r.stdout or r.stderr).strip()[:120]}")
        return r.returncode == 0
    except Exception as e:
        print(f"[WARN] Git pull failed: {e}")
        return False


def load_jsonl(path):
    """读取 jsonl 文件。路径必须位于 PROJECT/BASE 目录树内"""
    p = check_inside(path)
    items = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except Exception:
            pass
    return items


def write_jsonl(path, items):
    """写 jsonl 文件。路径必须位于 PROJECT/BASE 目录树内"""
    p = check_inside(path)
    payload = "\n".join(json.dumps(it, ensure_ascii=False) for it in items) + "\n"
    p.write_text(payload, encoding="utf-8")


def repo_intel_files():
    """仓库根的 intel_YYYYMMDD.jsonl 绝对路径列表（白名单正则过滤）"""
    out = []
    for entry in sorted(os.scandir(PROJECT), key=lambda e: e.name):
        if entry.is_file() and INTEL_NAME_RE.match(entry.name):
            out.append(str(safe_path(PROJECT, entry.name, INTEL_NAME_RE)))
    return out


def sync_repo_intel():
    """合并仓库根的 intel_YYYYMMDD.jsonl（CI 产物）进产物目录同名文件。
    同 id：优先保留带 cn_title 的版本；其余保留本地版。产物目录没有的文件直接新建。"""
    changed = 0
    for rf in repo_intel_files():
        name = os.path.basename(rf)
        bf = safe_path(BASE, name, INTEL_NAME_RE)
        repo_items = load_jsonl(rf)
        if not repo_items:
            continue
        local_items = load_jsonl(bf) if bf.exists() else []
        if not local_items:
            write_jsonl(bf, repo_items)
            print(f"Sync {name}: {len(repo_items)} items (new file)")
            changed += len(repo_items)
            continue
        local_map = {it.get("id"): it for it in local_items if it.get("id")}
        added = replaced = 0
        for it in repo_items:
            iid = it.get("id")
            if not iid:
                continue
            cur = local_map.get(iid)
            if cur is None:
                local_map[iid] = it
                added += 1
            elif not cur.get("cn_title") and it.get("cn_title"):
                local_map[iid] = it
                replaced += 1
        if added or replaced:
            write_jsonl(bf, list(local_map.values()))
            print(f"Merged {name}: +{added} new, {replaced} got CN translation")
            changed += added + replaced
        else:
            print(f"Merged {name}: up to date")
    print(f"Sync total: {changed} items changed")
    return changed


def translate_local():
    """本地有 NVIDIA_API_KEY 时对产物目录最新文件增量翻译（直接 import 调用，无子进程）；
    没有就跳过（由 CI 翻译后经 sync_repo_intel 合并回来）"""
    if not os.environ.get("NVIDIA_API_KEY"):
        print("Translate: no NVIDIA_API_KEY, skip (CI will translate)")
        return
    try:
        if str(PROJECT) not in sys.path:
            sys.path.insert(0, str(PROJECT))
        from cloud.translate import run as translate_run
        translate_run(str(BASE))
        print("Translate: done")
    except SystemExit:
        print("Translate: skipped by translator")
    except Exception as e:
        print(f"[WARN] Translate failed: {e}")
