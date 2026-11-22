#!/usr/bin/env python3
"""Inspect a pinned public Windows release without executing it.

Only recover original readable project files from validated embedded archives.
Compiled Go/Python code is not claimed as original source.
"""
import argparse
import ast
import hashlib
import io
import json
import re
import struct
import zipfile
import zlib
from pathlib import Path, PurePosixPath

MAX_TOTAL = 160_000_000
MAX_MEMBER = 30_000_000
MAX_MEMBERS = 6000
ALLOWED = {".py", ".go", ".json", ".md", ".txt", ".html", ".svg", ".png",
           ".ico", ".csv", ".patch", ".ps1", ".toml", ".yaml", ".yml"}
ROOTS = {"worker", "cmd", "templates", "assets", "tests", "docs", "examples", "tools"}
TOP = {"go.mod", "go.sum", "README.md", "START_HERE.txt", "requirements.txt", "SHA256SUMS.txt"}
CORE = {"worker/af_h5_offline.py", "worker/af_adaptive_native.py",
        "worker/af_adaptive_geometry.py", "cmd/autofluent/main_windows.go", "go.mod"}
WORKER = re.compile(r"^(?:af_[A-Za-z0-9_]+|am_workbench_bridge)\.py$")
COOKIE = b"MEI\x0c\x0b\x0a\x0b\x0e"


def safe_name(name):
    p = PurePosixPath(name.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts or not p.parts:
        return None
    if any(":" in part or part in {"", "."} for part in p.parts):
        return None
    return p


def project_name(name):
    p = safe_name(name)
    if not p or "__pycache__" in p.parts:
        return None
    for i, part in enumerate(p.parts):
        if part in ROOTS and i < len(p.parts) - 1:
            q = PurePosixPath(*p.parts[i:])
            if q.suffix.lower() in ALLOWED:
                if q.parts[0] == "worker" and not WORKER.fullmatch(q.name):
                    return None
                if q.parts[0] == "cmd" and (len(q.parts) < 3 or q.parts[1] != "autofluent"):
                    return None
                return q.as_posix()
    if len(p.parts) <= 2 and p.name in TOP:
        return p.name
    if WORKER.fullmatch(p.name):
        return "worker/" + p.name
    return None


def zip_candidates(blob):
    # EOCD may be embedded well before the end of the PE image, not just appended.
    cursor = 0
    count = 0
    while True:
        e = blob.find(b"PK\x05\x06", cursor)
        if e < 0:
            break
        cursor = e + 4
        count += 1
        if count > 20000:
            raise ValueError("too many ZIP end signatures")
        if e + 22 > len(blob):
            continue
        _, disk, cd_disk, n_disk, n, cd_size, cd_offset, comment = struct.unpack_from("<4s4H2LH", blob, e)
        start = e - cd_size - cd_offset
        end = e + 22 + comment
        if disk or cd_disk or n != n_disk or not 0 < n <= MAX_MEMBERS or start < 0 or end > len(blob):
            continue
        if blob[start:start + 4] != b"PK\x03\x04" or blob[e-cd_size:e-cd_size+4] != b"PK\x01\x02":
            continue
        try:
            with zipfile.ZipFile(io.BytesIO(blob[start:end])) as z:
                entries = z.infolist()
                if len(entries) > MAX_MEMBERS or sum(x.file_size for x in entries) > MAX_TOTAL:
                    continue
                if any(x.flag_bits & 1 or not safe_name(x.filename)
                       or ((x.external_attr >> 16) & 0o170000) == 0o120000 for x in entries):
                    continue
                if z.testzip() is not None:
                    continue
                yield ("zip@%d" % start, [(x.filename, z.read(x)) for x in entries
                         if not x.is_dir() and x.file_size <= MAX_MEMBER
                         and (project_name(x.filename) or x.filename.lower().endswith(".zip"))])
        except (ValueError, RuntimeError, OSError, zipfile.BadZipFile, NotImplementedError):
            continue


def inflate(data, size):
    if not 0 <= size <= MAX_MEMBER:
        raise ValueError("member exceeds limit")
    obj = zlib.decompressobj()
    value = obj.decompress(data, size + 1)
    if len(value) != size or not obj.eof or obj.unconsumed_tail:
        raise ValueError("invalid compressed member size")
    return value


def pyinstaller_candidates(blob):
    # PyInstaller CArchive: extract data/source only; never unmarshal or run PYZ.
    cursor = len(blob)
    while True:
        e = blob.rfind(COOKIE, 0, cursor)
        if e < 0:
            return
        cursor = e
        for length in (88, 24):
            if e + length > len(blob):
                continue
            try:
                _, package_size, toc_offset, toc_size, _ = struct.unpack_from("!8sIIII", blob, e)
                start = e + length - package_size
                pos = start + toc_offset
                stop = pos + toc_size
                if start < 0 or not start <= pos <= stop <= e or toc_size < 18:
                    continue
                found, inventory, total = [], [], 0
                while pos < stop:
                    if pos + 18 > stop:
                        raise ValueError("truncated TOC")
                    entry_size, offset, clen, usize, compressed, kind = struct.unpack_from("!IIIIBc", blob, pos)
                    if entry_size < 18 or pos + entry_size > stop or start+offset+clen > e:
                        raise ValueError("invalid TOC")
                    name = blob[pos+18:pos+entry_size].split(b"\0", 1)[0].decode("utf-8")
                    inventory.append({"name": name, "type": kind.decode("ascii", "replace"), "size": usize})
                    if len(inventory) > MAX_MEMBERS:
                        raise ValueError("too many members")
                    # PYZ/modules/scripts are compiled objects, not .py text.
                    if kind in (b"x", b"b", b"Z") and (project_name(name) or name.lower().endswith(".zip")):
                        if usize > MAX_MEMBER:
                            raise ValueError("member too large")
                        raw = blob[start+offset:start+offset+clen]
                        content = inflate(raw, usize) if compressed else raw
                        if len(content) != usize:
                            raise ValueError("bad member size")
                        total += usize
                        if total > MAX_TOTAL:
                            raise ValueError("payload too large")
                        found.append((name, content))
                    pos += entry_size
                if pos == stop:
                    yield ("pyinstaller@%d" % start, found, inventory)
                    return
            except (ValueError, UnicodeError, struct.error, zlib.error):
                continue


def scoped_pairs(pairs):
    """Only accept a project-root scope proved by both AF core worker names.

    Ignore other packages' tests, matplotlib/numpy/h5py resources, and nested
    interpreter archives; names such as tests/test_numeric.py alone are not
    evidence of AutoFluent ownership.
    """
    pairs = [(str(n).replace("\\", "/"), data) for n, data in pairs]
    by_path = {n for n, _ in pairs}
    prefixes = set()
    for n in by_path:
        if n.endswith("worker/af_h5_offline.py"):
            pre = n[:-len("worker/af_h5_offline.py")]
            if pre + "worker/af_adaptive_native.py" in by_path:
                prefixes.add((pre, "tree"))
        elif n.endswith("af_h5_offline.py"):
            pre = n[:-len("af_h5_offline.py")]
            if pre + "af_adaptive_native.py" in by_path:
                prefixes.add((pre, "flat"))
    for pre, kind in sorted(prefixes):
        for n, data in pairs:
            if not n.startswith(pre):
                continue
            tail = n[len(pre):]
            path = safe_name(tail)
            if not path:
                continue
            if kind == "flat":
                if len(path.parts) == 1 and WORKER.fullmatch(path.name):
                    yield "worker/" + path.name, data
                continue
            if path.parts[0] not in ROOTS and tail not in TOP:
                continue
            if any(x.lower() in {"site-packages", "lib", "python", "runtime", "__pycache__"} for x in path.parts):
                continue
            if path.parts[0] == "tests" and len(path.parts) > 1 and not (
                path.name.startswith("test_") or "fixtures" in path.parts):
                continue
            q = project_name(tail)
            if q:
                yield q, data


def collect(blob):
    files, evidence, conflicts, compiled_inventory, scope_inventory = {}, [], [], [], []
    binary_ext = {".png", ".ico"}

    def add(q, data):
        if PurePosixPath(q).suffix.lower() not in binary_ext:
            try:
                source = data.decode("utf-8-sig")
                if "\0" in source:
                    return
                if q.endswith(".py"):
                    ast.parse(source, filename=q)
                if q.endswith(".json"):
                    json.loads(source)
            except (ValueError, SyntaxError, UnicodeError):
                return
        if q in files and files[q] != data:
            conflicts.append(q)
        else:
            files[q] = data

    def consume(label, pairs, depth=0):
        scope = list(scoped_pairs(pairs))
        evidence.append(label)
        scope_inventory.append({"archive": label, "project_file_count": len(scope),
                                "candidate_paths": [q for q, _ in scope]})
        for q, data in scope:
            add(q, data)
        if depth == 0:
            for name, data in pairs:
                if not name.lower().endswith(".zip"):
                    continue
                # Only a nested archive independently containing AF core qualifies.
                for child, nested in zip_candidates(data):
                    consume(label + "/" + child, nested, depth + 1)

    for label, pairs in zip_candidates(blob):
        consume(label, pairs)
    for label, pairs, inventory in pyinstaller_candidates(blob):
        compiled_inventory.extend(inventory)
        consume(label, pairs)
    return files, evidence, sorted(set(conflicts)), compiled_inventory, scope_inventory


def check_worker(files):
    """A readable complete runtime worker tree, not proof of matching Go source."""
    needed = {"worker/af_h5_offline.py", "worker/af_adaptive_native.py",
              "worker/af_adaptive_geometry.py", "worker/af_worker.py"}
    names = {PurePosixPath(p).stem for p in files if p.startswith("worker/") and p.endswith(".py")}
    imports = set()
    for q, data in files.items():
        if not q.startswith("worker/") or not q.endswith(".py"):
            continue
        tree = ast.parse(data.decode("utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(x.name.split(".")[0] for x in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
    missing = sorted(x for x in imports if (x.startswith("af_") or x == "am_workbench_bridge") and x not in names)
    return needed <= set(files) and not missing, missing


def main():
    p = argparse.ArgumentParser()
    p.add_argument("exe", type=Path)
    p.add_argument("--sha256", required=True)
    p.add_argument("--repo", type=Path, default=Path.cwd())
    p.add_argument("--tag", required=True)
    args = p.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.tag):
        raise SystemExit("invalid release tag")
    blob = args.exe.read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    if digest != args.sha256.lower() or blob[:2] != b"MZ":
        raise SystemExit("release integrity / PE header check failed")
    repo = args.repo.resolve()
    files, evidence, conflicts, compiled, scopes = collect(blob)
    full = not conflicts and CORE <= set(files)
    # Never mix an unverified partial bundle with the legacy build tree.
    payload = repo / "release_payload" / args.tag
    payload.mkdir(parents=True, exist_ok=True)
    recovered = []
    if not conflicts:
        for name, data in sorted(files.items()):
            target = payload / "files" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            recovered.append({"path": name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
    # Promotion requires a coherent full project with an explicitly supplied
    # source hash manifest; check every candidate source against that manifest.
    verified = False
    manifest = files.get("SHA256SUMS.txt")
    if full and manifest:
        declared = {}
        for line in manifest.decode("utf-8-sig").splitlines():
            match = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+)$", line)
            if match:
                path = project_name(match.group(2).strip())
                if path:
                    declared[path] = match.group(1).lower()
        sources = {k for k in files if k.startswith(("cmd/", "worker/")) or k in {"go.mod", "go.sum"}}
        verified = all(declared.get(k) == hashlib.sha256(files[k]).hexdigest() for k in sources)
        verified = verified and all(k in files for k in declared if k.startswith(("cmd/", "worker/")))
    if verified:
        for name, data in sorted(files.items()):
            if name in {"README.md", "SHA256SUMS.txt"}:
                continue
            target = repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    worker_ok, missing_imports = check_worker(files) if not conflicts else (False, [])
    old_worker = {p.relative_to(repo).as_posix() for p in (repo / "worker").glob("*.py")}
    worker_missing_vs_baseline = sorted(old_worker - set(files))
    # Recover the exact readable Worker and its package resources. Do not delete
    # old project modules if the current payload cannot account for all of them.
    worker_synced = (not conflicts and worker_ok and not worker_missing_vs_baseline)
    if worker_synced:
        for name, data in sorted(files.items()):
            if not name.startswith(("worker/", "templates/", "assets/")):
                continue
            target = repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        # Bytecode cache is generated, not project source.
        import shutil
        cache = repo / "worker" / "__pycache__"
        if cache.is_dir():
            shutil.rmtree(cache)
    # The published artifact itself is verified independently of source recovery.
    # Preserve the original bytes; replace stale convenience binary.
    latest_exe = repo / "app" / "AutoFluent.exe"
    latest_exe.parent.mkdir(parents=True, exist_ok=True)
    latest_exe.write_bytes(blob)
    legacy_exe = repo / "AutoFluent-目录浏览版.exe"
    if legacy_exe.is_file():
        legacy_exe.unlink()
    status = ("verified_full_source_synced" if verified else
              "worker_source_synced_go_not_recovered" if worker_synced else
              "conflicting_payload" if conflicts else
              "partial_readable_payload" if recovered else "no_readable_project_source")
    result = {"tag": args.tag, "asset": "AutoFluent.exe", "size": len(blob), "sha256": digest,
              "status": status, "archives": evidence, "archive_scopes": scopes, "conflicts": conflicts,
              "missing_core_files": sorted(CORE - set(files)), "worker_synced": worker_synced,
              "missing_worker_imports": missing_imports,
              "worker_missing_vs_baseline": worker_missing_vs_baseline,
              "recovered": recovered, "compiled_archive_inventory": compiled,
              "source_note": "Original readable files only; compiled code is not original source."}
    (payload / "inventory.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Release " + args.tag + " — 静态拆包核验", "",
             "原始 EXE 未执行、未修改。SHA-256: " + digest, "",
             "状态：" + status, "",
             "识别归档：" + (", ".join(evidence) or "未识别 ZIP / PyInstaller CArchive"),
             "可恢复项目文件：" + str(len(recovered)), "",
             "Worker 主目录同步：" + ("已同步" if worker_synced else "未同步，先核对缺失/冲突"), "",
             ("已按内嵌校验清单同步完整源文件。" if verified else
              "尚未证明完整 Go 及测试源码与此 EXE 一致；未恢复部分保留为历史基线。"),
             "", "完整文件与缺失项见 [inventory.json](inventory.json)，原样恢复内容见 [files/](files/)（如有）。", "",
             "没有内嵌的 Go 源文件或仅有编译字节码时，需要原始构建源码才能完整同步。"]
    (payload / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in {"recovered", "compiled_archive_inventory"}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
