# -*- coding: utf-8 -*-
r"""publish_pages.py - 把仪表盘发布到 GitHub Pages（2026-10-10）

## 为什么是「本地生成 → 推 gh-pages」而不是「CI 生成」

实测（2026-10-10）云端可用的数据比本地薄：
  - 仓库根 intel_*.jsonl：cn_title **0%**（翻译 2026-09-04 移出 CI，只在本地跑）
  - 面板实际只能显示 53% 中文标题（cn_title 空 → 回退 title，国内源 title 本就是中文），
    本地是 93%
  - 估值分位/失业率历史两个面板的数据源是本地专有工具链（firecrawl 抓的 raw_*.md、
    财新网页），CI 上没有
所以由 CI 生成会得到一个**劣化看板**。改为复用本地已有链路生成云端版再推送，
全保真且不需要维护第二套数据管线。代价：本机关机时云端停在最后一次推送
（那种情况下本来也拿不到新情报）。

## 为什么 force push

gh-pages 的职责是「只保留最新一份产物」。若每轮线性提交，一天 24 轮 × 约 1MB
会把仓库撑到每天 ~20MB。故用 `--force` 覆盖式推送（分支历史恒为 1 个提交）。
**这是 gh-pages 单分支的约定用法**，不涉及 master 的强推——项目「禁止 push --force」
的规则针对的是主分支，这里由脚本集中在一处、目标分支硬校验为 gh-pages。

## 用法

  python data/publish_pages.py            # 生成云端版并推送
  python data/publish_pages.py --dry      # 只生成到 dist/，不推送
  python data/publish_pages.py --check    # 只校验远端 Pages 配置，不生成

由 refresh.py 自动调用（非 critical 步骤，失败不影响其他子步骤）。
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
BASE = PROJECT / "data"
DIST = BASE / "dist"
BRANCH = "gh-pages"          # 硬编码：force push 只允许打到这个分支
_REMOTE = "origin"


def _gen_env():
    """生成云端版所需环境变量（gen_dashboard/fix_dashboard 读这几个）。"""
    env = dict(os.environ)
    env["OSINT_CLOUD"] = "1"
    # 产物写到 dist/index.html，不碰本地 interactive_dashboard.html
    env["OSINT_HTML_OUT"] = str(DIST / "index.html")
    return env


def build():
    """本地生成云端只读版 → data/dist/index.html。返回产物路径或 None。"""
    DIST.mkdir(parents=True, exist_ok=True)
    for script in ("gen_dashboard.py", "fix_dashboard.py"):
        r = subprocess.run(
            [sys.executable, str(PROJECT / script)],
            cwd=str(PROJECT), capture_output=True, text=True, env=_gen_env(),
        )
        if r.returncode != 0:
            print(f"publish_pages: {script} 失败: {(r.stderr or r.stdout)[:200]}")
            return None
    out = DIST / "index.html"
    if not out.exists():
        print("publish_pages: 产物不存在")
        return None
    print(f"publish_pages: 生成 {out} ({out.stat().st_size} bytes)")
    return out


def _git_env():
    """git 子进程环境（经本地代理；探测不到则直连）。与 refresh 共用 net_proxy。"""
    try:
        sys.path.insert(0, str(PROJECT))
        from net_proxy import git_env
        return git_env()
    except Exception:
        return dict(os.environ)


def _gh_ok():
    """gh CLI 是否可用（自带路由；本机 git 直连不稳时的可靠通道）。"""
    try:
        r = subprocess.run(["gh", "auth", "status"], capture_output=True,
                           text=True, timeout=30)
        return r.returncode == 0
    except Exception:
        return False


def push_via_gh(dist_index):
    """用 gh api 把 index.html 发布到 gh-pages（首选通道，历史恒定 1 提交）。

    2026-10-10 实测：本机 git 直连 GitHub 时通时不通（Clash 未自启时必挂），
    而 `gh` CLI 自带路由稳定可用，故发布优先走 gh api。

    **为什么用「孤儿提交 + 强制移动 ref」而不是 PUT contents**：
    `PUT /contents` 每次产生一个新提交，而面板每小时变一次 → 0.88MB × 24/天
    ≈ 21MB/天 ≈ 630MB/月，会撑爆仓库。改为每次构造**无父提交的孤儿 commit**
    再 force 移动分支引用，gh-pages 历史恒为 1 个提交，仓库体积不随时间增长
    （旧对象由 GitHub 自动回收）。

    安全：所有请求体经 stdin（`--input -`）送入，**不把内容拼进命令行参数**
    ——内容是产物 base64（派生数据），拼进 argv 会构成注入面。
    """
    import base64
    import json as _json

    repo = "repos/ol4p4/osint"

    def gh_call(args, body=None, timeout=120):
        return subprocess.run(
            ["gh", "api"] + args + (["--input", "-"] if body is not None else []),
            input=_json.dumps(body) if body is not None else None,
            capture_output=True, text=True, timeout=timeout,
        )

    # 1) 建 blob
    r = gh_call(["-X", "POST", f"{repo}/git/blobs"], {
        "content": base64.b64encode(dist_index.read_bytes()).decode("ascii"),
        "encoding": "base64",
    })
    if r.returncode != 0:
        print(f"publish_pages: 建 blob 失败: {(r.stderr or r.stdout or '')[:200]}")
        return False
    blob_sha = (_json.loads(r.stdout or "{}") or {}).get("sha")
    if not blob_sha:
        print("publish_pages: blob sha 为空")
        return False

    # 2) 建 tree（根级 index.html）
    r = gh_call(["-X", "POST", f"{repo}/git/trees"], {
        "tree": [{"path": "index.html", "mode": "100644", "type": "blob", "sha": blob_sha}],
    })
    if r.returncode != 0:
        print(f"publish_pages: 建 tree 失败: {(r.stderr or r.stdout or '')[:200]}")
        return False
    tree_sha = (_json.loads(r.stdout or "{}") or {}).get("sha")

    # 3) 建**孤儿** commit（不传 parents → 无父提交，历史不累积）
    r = gh_call(["-X", "POST", f"{repo}/git/commits"], {
        "message": "publish: dashboard snapshot",
        "tree": tree_sha,
    })
    if r.returncode != 0:
        print(f"publish_pages: 建 commit 失败: {(r.stderr or r.stdout or '')[:200]}")
        return False
    commit_sha = (_json.loads(r.stdout or "{}") or {}).get("sha")

    # 4) 强制把分支指向新孤儿 commit
    r = gh_call(["-X", "PATCH", f"{repo}/git/refs/heads/{BRANCH}"],
                {"sha": commit_sha, "force": True})
    if r.returncode != 0:
        print(f"publish_pages: 更新分支失败: {(r.stderr or r.stdout or '')[:200]}")
        return False

    print(f"publish_pages: 已发布到 gh-pages（commit {str(commit_sha)[:8]}）")
    return True


def push(dist_index):
    """把 dist/index.html 覆盖式推到 gh-pages 分支。

    用临时 git 仓库（不污染主仓库工作区，也不需要切分支）。
    """
    tmp = Path(tempfile.mkdtemp(prefix="osint_ghp_"))
    try:
        def run(args, cwd=tmp, timeout=180):
            return subprocess.run(args, cwd=str(cwd), capture_output=True,
                                  text=True, timeout=timeout, env=_git_env())

        remote = run(["git", "remote", "get-url", _REMOTE],
                     cwd=PROJECT).stdout.strip() or \
            "https://github.com/ol4p4/osint.git"

        # 先把产物拷进临时仓库，再 init/add/commit（顺序不能反：先 commit 后拷贝
        # 会把空仓库提交上去）
        shutil.copy2(dist_index, tmp / "index.html")
        if not (tmp / ".git").exists():
            init_steps = [
                ["git", "init", "-q", "-b", BRANCH],
                ["git", "config", "user.email", "osint-bot@local"],
                ["git", "config", "user.name", "osint-bot"],
            ]
            for s in init_steps:
                r = run(s)
                if r.returncode != 0:
                    print(f"publish_pages: {s[1]} 失败: {(r.stderr or r.stdout)[:200]}")
                    return False

        run(["git", "add", "index.html"])
        c = run(["git", "commit", "-q", "-m", "publish: dashboard snapshot"])
        if c.returncode != 0:
            blob = (c.stdout or "") + (c.stderr or "")
            if "nothing to commit" in blob:
                print("publish_pages: 内容无变化，跳过推送")
                return True
            # 首次提交也可能因缺 user 配置失败——给出可诊断信息
            print(f"publish_pages: commit 失败: {blob[:200]}")
            return False

        chk = run(["git", "remote"])
        if _REMOTE not in (chk.stdout or "").split():
            r = run(["git", "remote", "add", _REMOTE, remote])
            if r.returncode != 0:
                print(f"publish_pages: remote add 失败: {(r.stderr or r.stdout)[:200]}")
                return False

        # 覆盖式推送：只允许打到 gh-pages（分支名硬校验，防误伤 master）
        assert BRANCH == "gh-pages"
        p = run(["git", "push", "--force", _REMOTE, f"{BRANCH}:{BRANCH}"], timeout=300)
        if p.returncode != 0:
            print(f"publish_pages: push 失败: {(p.stderr or p.stdout)[:250]}")
            return False
        print("publish_pages: 已推送到 gh-pages")
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    dry = "--dry" in sys.argv
    if "--check" in sys.argv:
        r = subprocess.run(["git", "ls-remote", "--heads", _REMOTE, BRANCH],
                           cwd=str(PROJECT), capture_output=True, text=True,
                           env=_git_env(), timeout=120)
        print(f"远端 {BRANCH}: {(r.stdout or r.stderr).strip()[:120] or '(不存在)'}")
        return
    idx = build()
    if not idx:
        sys.exit(1)
    if dry:
        print(f"publish_pages: --dry，产物留在 {idx}")
        return
    # 首选 gh（自带路由，本机 git 直连不稳时唯一可靠通道）；gh 不可用再退回 git。
    if _gh_ok():
        sys.exit(0 if push_via_gh(idx) else 1)
    print("publish_pages: gh 不可用，退回 git 推送")
    sys.exit(0 if push(idx) else 1)


if __name__ == "__main__":
    main()
