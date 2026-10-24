#!/usr/bin/env python
"""把 hf_deploy/ 干净地部署到 Hugging Face Space（git 方式，会同步删除多余文件）。

为什么需要它：HF 网页的「拖拽上传」只会新增/覆盖文件，**永远不会删除**。
本项目的 Space 因此累积了 266 个残留文件（backend/、frontend/、docs/、
根目录重复的 *.py、甚至一个被误提交的 .env）。git 推送才会真正同步删除。

用法：
    python build_hf_bundle.py          # 1. 先由源码生成干净文件集
    python deploy_hf.py --dry-run      # 2. 预览会改什么（不推送）
    python deploy_hf.py                # 3. 真正部署（Space 自动重建）

    python deploy_hf.py --repo dzha0118/termprep --message "fix: xxx"
    python deploy_hf.py --proxy http://127.0.0.1:7892   # 系统代理时 git 需要显式指定

依赖：本机 git，以及对 Space 有写权限的凭据（HF Access Token 当密码）。
"""

from __future__ import annotations

import argparse
import filecmp
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "hf_deploy"


def _git(*args: str, cwd: Path, proxy: str = "") -> tuple[int, str]:
    cmd = ["git", "-c", "http.sslBackend=openssl"]
    if proxy:
        cmd += ["-c", f"http.proxy={proxy}"]
    cmd += list(args)
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never")
    p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _detect_proxy() -> str:
    """Windows 系统代理 git 不会自动使用，从注册表读出来。"""
    if os.name != "nt":
        return ""
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        )
        enabled = winreg.QueryValueEx(key, "ProxyEnable")[0]
        server = winreg.QueryValueEx(key, "ProxyServer")[0]
        if enabled and server:
            return server if "://" in server else f"http://{server}"
    except Exception:
        pass
    return ""


def _sync(src: Path, dst: Path) -> tuple[int, list[str]]:
    """把 src 全量同步到 dst（dst 里除 .git 外全部清空）。返回 (文件数, 被删条目)。"""
    removed = []
    for name in os.listdir(dst):
        if name == ".git":
            continue
        p = dst / name
        removed.append(name + ("/" if p.is_dir() else ""))
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
        else:
            p.unlink()

    count = 0
    for base, _dirs, files in os.walk(src):
        rel = Path(base).relative_to(src)
        target = dst if rel == Path(".") else dst / rel
        target.mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copy2(Path(base) / f, target / f)
            count += 1
    return count, sorted(removed)


def _verify(src: Path, dst: Path) -> list[str]:
    problems = []

    def listing(base: Path) -> set[str]:
        out = set()
        for root, dirs, files in os.walk(base):
            if ".git" in Path(root).parts:
                continue
            for f in files:
                out.add(Path(root, f).relative_to(base).as_posix())
        return out

    a, b = listing(src), listing(dst)
    problems += [f"缺少: {x}" for x in sorted(a - b)]
    problems += [f"多余: {x}" for x in sorted(b - a)]
    for rel in sorted(a & b):
        if not filecmp.cmp(src / rel, dst / rel, shallow=False):
            problems.append(f"内容不一致: {rel}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default="dzha0118/termprep", help="Space 仓库 id")
    ap.add_argument("--branch", default="main", help="Space 分支（默认 main）")
    ap.add_argument("--message", default="", help="提交信息")
    ap.add_argument("--proxy", default="", help="HTTP 代理，如 http://127.0.0.1:7892")
    ap.add_argument("--dry-run", action="store_true", help="只预览，不提交不推送")
    ap.add_argument("--keep", action="store_true", help="保留临时克隆目录便于排查")
    args = ap.parse_args()

    if not SRC.is_dir():
        print(f"找不到 {SRC}，请先运行：python build_hf_bundle.py")
        return 1

    proxy = args.proxy or _detect_proxy()
    if proxy:
        print(f"使用代理: {proxy}")
    else:
        print("未检测到系统代理（若推送超时，试试 --proxy http://127.0.0.1:7892）")

    url = f"https://huggingface.co/spaces/{args.repo}"
    tmp = Path(tempfile.mkdtemp(prefix="termprep-hf-"))
    work = tmp / "space"
    try:
        print(f"克隆 {url} …")
        code, out = _git("clone", "--quiet", url, str(work), cwd=tmp, proxy=proxy)
        if code != 0:
            print("克隆失败：\n" + out.strip()[-800:])
            return 1

        file_count, removed = _sync(SRC, work)
        print(f"已同步 {file_count} 个文件（先清空 {len(removed)} 个顶层条目再重建，"
              f"所以 Space 上任何残留都会被清掉）")
        if removed:
            print("  涉及顶层条目: " + ", ".join(removed[:20]))

        problems = _verify(SRC, work)
        if problems:
            print("同步后校验失败：")
            for p in problems[:20]:
                print("  " + p)
            return 1
        print("同步校验通过（内容与 hf_deploy/ 完全一致）")

        code, out = _git("add", "-A", cwd=work, proxy=proxy)
        if code != 0:
            print("git add 失败：\n" + out.strip()[-500:])
            return 1
        code, status = _git("status", "--porcelain", cwd=work, proxy=proxy)
        if not status.strip():
            print("Space 内容已是最新，无需部署。")
            return 0
        changed = len(status.strip().splitlines())
        print(f"待提交变更: {changed} 项")

        if args.dry_run:
            print("\n[dry-run] 未提交、未推送。上面就是会发生的改动。")
            return 0

        # 身份兜底：HF Space 未必配置了 user.name/email
        name = _git("config", "--get", "user.name", cwd=ROOT)[1].strip() or "termprep-deploy"
        mail = _git("config", "--get", "user.email", cwd=ROOT)[1].strip() or "deploy@example.com"
        _git("config", "user.name", name, cwd=work)
        _git("config", "user.email", mail, cwd=work)

        msg = args.message or (
            f"deploy: 同步 hf_deploy（{file_count} 个文件，清理 {len(removed)} 个残留）"
        )
        code, out = _git("commit", "-m", msg, cwd=work, proxy=proxy)
        if code != 0:
            print("提交失败：\n" + out.strip()[-800:])
            return 1
        print("已提交：" + msg)

        print(f"推送到 {args.branch} …")
        code, out = _git("push", "origin", args.branch, cwd=work, proxy=proxy)
        if code != 0:
            print("推送失败：\n" + out.strip()[-800:])
            print("\n提示：HF 用 Access Token 当密码；需要写权限。")
            return 1
        print("推送成功，Space 会自动重建。用 /api/health 确认版本。")
        return 0
    finally:
        if args.keep:
            print(f"临时目录保留在: {work}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
