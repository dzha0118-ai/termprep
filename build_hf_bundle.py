#!/usr/bin/env python
"""把源码打包成可直接拖拽上传到 Hugging Face Space 的干净文件集。

用法：
    python build_hf_bundle.py            # 重新生成 hf_deploy/
    python build_hf_bundle.py --check    # 只校验现有 hf_deploy/ 是否与源码一致

设计约束（用户要求）：hf_deploy/ 里只能有运行必需的文件，
不能有 __pycache__、临时目录、.db、报告、测试脚本。
"""

from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "hf_deploy"

# 顶层文件（相对 ROOT）
TOP_FILES = [
    "Dockerfile",
    "web_entry.py",
    "pyproject.toml",
    "requirements.txt",
    "README.md",
]

# 需要整棵复制的目录
TREES = [
    "termprep",
    "web",
]

# dev 目录里只保留这些非 .py 资源
DEV_KEEP = {"data/.gitkeep", "data/medical_seed_terms.json", "reports/.gitkeep"}

# 任何层级都不带的垃圾
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git", "node_modules", ".venv", "venv"}
SKIP_SUFFIX = {".pyc", ".pyo", ".db", ".sqlite", ".sqlite3", ".log", ".zip"}
SKIP_NAMES = {".DS_Store", "Thumbs.db"}

# 包目录内的文档/残留副本，运行时不需要（文档只认顶层 README.md）
SKIP_REL = {
    "termprep/data",              # 旧版种子副本，实际从项目根 data/ 读取
}
SKIP_TOP_DOCS = {                 # termprep/ 下这些 md 属于开发文档，打包时剔除
    "AGENTS_README.md",
    "architecture-refactor-plan.md",
    "HF_DEPLOY.md",
    "README.md",
}


def _skip(path: Path, top_level_of_tree: bool = False) -> bool:
    if path.suffix in SKIP_SUFFIX or path.name in SKIP_NAMES:
        return True
    # 点文件（.env.example / .gitignore 等）不进打包
    if path.name.startswith("."):
        return True
    # 包目录顶层的 md 是开发文档
    if top_level_of_tree and path.suffix == ".md" and path.name in SKIP_TOP_DOCS:
        return True
    return False


def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _walk(src_dir: Path, dst_dir: Path, top: bool = True) -> int:
    n = 0
    for entry in sorted(src_dir.iterdir()):
        if entry.is_dir():
            if entry.name in SKIP_DIRS:
                continue
            rel = entry.relative_to(ROOT).as_posix()
            if rel in SKIP_REL:
                continue
            n += _walk(entry, dst_dir / entry.name, top=False)
            continue
        if _skip(entry, top_level_of_tree=top):
            continue
        _copy_file(entry, dst_dir / entry.name)
        n += 1
    return n


def build() -> tuple[int, int]:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    count = 0
    for name in TOP_FILES:
        src = ROOT / name
        if not src.exists():
            print(f"  ! 缺少顶层文件 {name}（已跳过）")
            continue
        _copy_file(src, OUT / name)
        count += 1

    for name in TREES:
        src = ROOT / name
        if not src.exists():
            print(f"  ! 缺少目录 {name}（已跳过）")
            continue
        count += _walk(src, OUT / name)

    # data/ 与 reports/ 只留占位，避免上传运行期产物
    for rel in DEV_KEEP:
        src = ROOT / rel
        if src.exists():
            _copy_file(src, OUT / rel)
            count += 1

    files = [p for p in OUT.rglob("*") if p.is_file()]
    size = sum(p.stat().st_size for p in files)
    return len(files), size


def check() -> int:
    """对比 hf_deploy/ 与源码，返回不一致的文件数。"""
    if not OUT.exists():
        print("hf_deploy/ 不存在，请先运行 python build_hf_bundle.py")
        return 1

    bad = 0
    for dst in sorted(OUT.rglob("*")):
        if not dst.is_file():
            continue
        rel = dst.relative_to(OUT)
        src = ROOT / rel
        if not src.exists():
            print(f"  - 多余文件：{rel}")
            bad += 1
        elif not filecmp.cmp(src, dst, shallow=False):
            print(f"  ~ 内容过期：{rel}")
            bad += 1

    for name in TOP_FILES + TREES:
        src = ROOT / name
        if not src.exists():
            continue
        targets = [src] if src.is_file() else [p for p in src.rglob("*") if p.is_file()]
        for f in targets:
            if any(part in SKIP_DIRS for part in f.parts):
                continue
            rel = f.relative_to(ROOT)
            if any(str(rel).replace("\\", "/").startswith(p) for p in SKIP_REL):
                continue
            if _skip(f, top_level_of_tree=(f.parent == src.parent / name)):
                continue
            if not (OUT / rel).exists():
                print(f"  + 缺失文件：{rel}")
                bad += 1

    if bad:
        print(f"\n[FAIL] hf_deploy/ 与源码有 {bad} 处不一致，请重新运行 python build_hf_bundle.py")
        return 1
    print("[OK] hf_deploy/ 与源码一致")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="只校验，不重建")
    args = ap.parse_args()

    if args.check:
        return check()

    n, size = build()
    print(f"[OK] hf_deploy/ 已重建：{n} 个文件，{size / 1024:.1f} KB")
    print("  打开 hf_deploy/ 全选拖到 Space 的 Files 页即可。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
