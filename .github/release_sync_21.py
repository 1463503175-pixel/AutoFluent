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
           ".ico", ".csv", ".patch", ".ps1", ".toml", ".yaml", ".yml", ".css", ".js", ".mjs", ".ts", ".jpg", ".jpeg", ".webp", ".gif", ".woff", ".woff2", ".ttf", ".ini", ".cfg", ".bat", ".cmd", ".rst", ".xml", ".j2", ".sql"}
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
    binary_ext = {".png", ".ico", ".jpg", ".jpeg", ".webp", ".gif", ".woff", ".woff2", ".ttf"}

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
    import subprocess
    import shutil
    p=argparse.ArgumentParser()
    p.add_argument("exe",type=Path)
    p.add_argument("--repo",type=Path,default=Path.cwd())
    p.add_argument("--baseline",required=True)
    args=p.parse_args()
    repo=args.repo.resolve()
    tag="2.1"
    expected="30d822c0c5f5eda2cbebdf162581b920b219e9ee5438b08e1b232b5a47f30d7c"
    blob=args.exe.read_bytes()
    digest=hashlib.sha256(blob).hexdigest()
    if len(blob)!=32211968 or digest!=expected or blob[:2]!=b"MZ":
        raise SystemExit("Release asset size / SHA256 / PE mismatch")
    pe_offset=struct.unpack_from("<I",blob,0x3c)[0]
    if blob[pe_offset:pe_offset+4]!=b"PE\0\0":raise SystemExit("Missing PE signature")
    files,archives,conflicts,compiled,scopes=collect(blob)
    worker_ok,missing_imports=check_worker(files)
    if conflicts or not worker_ok:
        raise SystemExit("Unusable recovered source: "+json.dumps({"conflicts":conflicts,"missing_imports":missing_imports,"scopes":scopes},ensure_ascii=False))
    # Do not carry forward any old application files, even if they had valid names.
    # A project file is published only if obtained verbatim from this asset.
    required_resources={"assets/AutoFluent_icon_512.png","templates/report_v11/report_template.html"}
    if not required_resources<=set(files):raise SystemExit("Required branding/template not recovered: "+str(required_resources-set(files)))
    excluded_source={}
    # Preserve original embedded files even where they collide with maintained
    # repo documentation. These are separately namespaced rather than overwritten.
    reserved={"README.md","START_HERE.txt","SHA256SUMS.txt",".gitignore","docs/GETTING_STARTED.md","docs/release_inventory.json","docs/media/brand.svg","app/AutoFluent.exe"}
    for name in sorted(set(files)&reserved):
        excluded_source[name]=files.pop(name)
    for name,data in excluded_source.items():
        files["embedded_metadata/"+name]=data
    prior=json.loads((repo/"docs/release_inventory.json").read_text(encoding="utf-8"))
    old={x["path"]:x["sha256"] for x in prior["recovered"]}
    new={name:hashlib.sha256(data).hexdigest() for name,data in files.items()}
    differences={"added":sorted(new.keys()-old.keys()),"removed":sorted(old.keys()-new.keys()),
                 "changed":sorted(k for k in old.keys()&new.keys() if old[k]!=new[k]),
                 "unchanged":sorted(k for k in old.keys()&new.keys() if old[k]==new[k])}
    readme=(repo/"README.md").read_text(encoding="utf-8")
    guide=(repo/"docs/GETTING_STARTED.md").read_text(encoding="utf-8")
    start=(repo/"START_HERE.txt").read_text(encoding="utf-8")
    brand=(repo/"docs/media/brand.svg").read_bytes()
    ignore=(repo/".gitignore").read_bytes()
    def version(text):
        return text.replace("2.0","2.1")
    # All tracked files now replaced by exact new payload plus maintained docs.
    tracked=subprocess.check_output(["git","ls-files","-z"],cwd=repo).decode("utf-8").split("\0")
    for name in tracked:
        if not name:continue
        path=repo/name
        if path.is_file() or path.is_symlink():path.unlink()
    def write(name,data):
        p=repo/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
    def txt(name,data):write(name,data.encode("utf-8"))
    for name,data in files.items():write(name,data)
    write("app/AutoFluent.exe",blob)
    write(".gitignore",ignore)
    # Inspect report dependencies without executing recovered application code.
    v11_required=["templates/report_v11/report.css","templates/report_v11/report.js","templates/report_v11/report_template.html"]
    missing_resources=[name for name in v11_required if name not in files]
    go_recovered=all(k in files for k in ("go.mod","cmd/autofluent/main_windows.go"))
    worker_count=sum(k.startswith("worker/") and k.endswith(".py") for k in files)
    records=[{"path":k,"sha256":new[k],"size":len(files[k])} for k in sorted(files)]
    inventory={"release":tag,"published_date":"2026-11-22","asset":"app/AutoFluent.exe",
       "size":len(blob),"sha256":digest,"archives":archives,"archive_scopes":scopes,
       "recovered":records,"conflicts":conflicts,"missing_worker_imports":missing_imports,
       "missing_report_assets":missing_resources,"go_frontend_source_recovered":go_recovered,
       "comparison_to_previous_payload":differences,"baseline_commit":args.baseline,
       "checks":{"sha256":"pass","pe_signature":"pass","python_ast":"pass","json_parse":"pass","internal_worker_imports":"pass"},
       "source_note":"Static extraction only; original application files recovered verbatim. No EXE or recovered application code executed. Runtime dependencies and rebuildability not certified."}
    txt("docs/release_inventory.json",json.dumps(inventory,ensure_ascii=False,indent=2)+"\n")
    txt("SHA256SUMS.txt",digest+"  app/AutoFluent.exe\n"+"".join(r["sha256"]+"  "+r["path"]+"\n" for r in records))
    # Retain established homepage. Current release references and actual recovery
    # statistics are refreshed without inventing new product features.
    readme=version(readme)
    note=f"本次已从集成程序原样恢复 {len(files)} 个项目文件，其中 {worker_count} 个 Python 分析模块。"
    if missing_resources:note+=" V11 报告独立渲染资源缺失："+", ".join(missing_resources)+"。"
    readme=readme.replace("可恢复的 Python 分析模块、模板及图标来自发布程序，展示文档独立维护。","可恢复的 Python 分析模块、模板及图标来自发布程序，展示文档独立维护。"+note)
    if go_recovered:readme=readme.replace("集成 EXE 未包含可恢复的完整桌面前端源码及构建定义。","已找到桌面源文件及构建定义，但未验证完整可重建性。")
    txt("README.md",readme)
    txt("docs/GETTING_STARTED.md",version(guide))
    txt("START_HERE.txt",version(start))
    write("docs/media/brand.svg",brand.replace(b"AF / 02",b"AF / 2.1"))
    # Every project link in README must resolve to the new tree (or be external).
    links=re.findall(r'(?:href|src)="([^"]+)"|\]\(([^)]+)\)',readme)
    for a,b in links:
        link=a or b
        if link.startswith(("https://","http://","#")):continue
        if not (repo/link).exists():raise SystemExit("Broken README link: "+link)
    expected_paths=set(files)|{"app/AutoFluent.exe",".gitignore","docs/release_inventory.json","SHA256SUMS.txt","README.md","docs/GETTING_STARTED.md","START_HERE.txt","docs/media/brand.svg"}
    for k,h in new.items():
        if hashlib.sha256((repo/k).read_bytes()).hexdigest()!=h:raise SystemExit("File hash mismatch: "+k)
    if hashlib.sha256((repo/"app/AutoFluent.exe").read_bytes()).hexdigest()!=digest:raise SystemExit("EXE changed")
    actual={x.relative_to(repo).as_posix() for x in repo.rglob("*") if x.is_file() and ".git" not in x.relative_to(repo).parts}
    if actual!=expected_paths:raise SystemExit("Unexpected or missing repository files: "+str(actual^expected_paths))
    print(json.dumps({"release":tag,"recovered":len(files),"python":worker_count,"changes":differences,
        "missing_report_assets":missing_resources,"go_frontend_source_recovered":go_recovered,
        "tree_files":len(expected_paths)},ensure_ascii=False),flush=True)

if __name__=="__main__":
    main()
