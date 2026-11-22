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
WORKER = re.compile(r"^(?:af_[A-Za-z0-9_.-]+|am_workbench_bridge)\.(?:py|js|css|json|html|svg|png|ico|csv|txt|mjs|toml|yaml|yml|j2)$")
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
    prior=json.loads("{\n  \"release\": \"2.0\",\n  \"published_date\": \"2026-11-22\",\n  \"asset\": \"app/AutoFluent.exe\",\n  \"size\": 32208896,\n  \"sha256\": \"d3b374cf79413c248d7d1ff0178cda4ef8b2d0fd8e273cd08b6e41b3f589aed1\",\n  \"archives\": [\n    \"zip@3522368\",\n    \"zip@3522368/zip@0\"\n  ],\n  \"recovered\": [\n    {\n      \"path\": \"assets/AutoFluent.ico\",\n      \"sha256\": \"1539d5b311f11a8cc96a3659f053edd4340b054a052be8a2c36745f0ae0b308d\",\n      \"size\": 135992\n    },\n    {\n      \"path\": \"assets/AutoFluent_icon_512.png\",\n      \"sha256\": \"d4d7e2e5c6d1694a643ca41467251d4fd68c73b20a6ca849691b9d91e1fafb4c\",\n      \"size\": 287433\n    },\n    {\n      \"path\": \"assets/AutoFluent_source.png\",\n      \"sha256\": \"03b2b28fc30cc5680fe001e8bea55cbb9ddcf47fe41f2b5ff42d8ae2295455bd\",\n      \"size\": 1126664\n    },\n    {\n      \"path\": \"templates/ALD-N2-100Pa-DRAFT.json\",\n      \"sha256\": \"c756ba491533eda14795924b7ef8d70e65accb0b6eb9484994ba8fe83a13698a\",\n      \"size\": 862\n    },\n    {\n      \"path\": \"templates/geometry_config_example_staggered.json\",\n      \"sha256\": \"3f48f1628bcb72b049793911c28a506dbc5b5e677afd52411be055bb17014d17\",\n      \"size\": 1097\n    },\n    {\n      \"path\": \"templates/report_v11/report_template.html\",\n      \"sha256\": \"79f87efef3b79aab38c67931c0e7a3f02b23fd78be14d9aaba8e7d2c6ec02401\",\n      \"size\": 9094\n    },\n    {\n      \"path\": \"worker/af_adaptive_compare.py\",\n      \"sha256\": \"f3d066f7cce481785af173765dc8e8e34c7570a414764341d7e9be2fdaa67b3c\",\n      \"size\": 12115\n    },\n    {\n      \"path\": \"worker/af_adaptive_geometry.py\",\n      \"sha256\": \"8e6a30b9e93419803f28550068d7b8a0d0ba9a13c3087ea58cde67eae2e1c0db\",\n      \"size\": 37042\n    },\n    {\n      \"path\": \"worker/af_adaptive_native.py\",\n      \"sha256\": \"342c6862ca243fe2ae0f528117264d55e4a318da915683ec9a7bf9ceeeef0184\",\n      \"size\": 88357\n    },\n    {\n      \"path\": \"worker/af_baffle_openings.py\",\n      \"sha256\": \"b68c8f0c0801d0e346adcce9b18f7d755f020d4a896faaed2f1f9082708052dc\",\n      \"size\": 7727\n    },\n    {\n      \"path\": \"worker/af_bundle_diagnostics_selftest.py\",\n      \"sha256\": \"e1991277ffbd681727d257aed7e1121e3449fc6ff2545f025b873b42cfdf3afc\",\n      \"size\": 4466\n    },\n    {\n      \"path\": \"worker/af_bundle_selftest.py\",\n      \"sha256\": \"c1993e67611e1b5993ab6c2f5a4bce3e39fbb4fa535754a75b070c5ee13f9a40\",\n      \"size\": 8208\n    },\n    {\n      \"path\": \"worker/af_bundle_species_selftest.py\",\n      \"sha256\": \"a09b7b6af8a862fc3e048dfe46c77cf66610dc602e0f1cc26de3b1fbf1a5d0c6\",\n      \"size\": 6224\n    },\n    {\n      \"path\": \"worker/af_cff_rt0.py\",\n      \"sha256\": \"33b645924aac2424672ad51dbe60ec53beaa01ff23485c03a1118f8776b35b12\",\n      \"size\": 8924\n    },\n    {\n      \"path\": \"worker/af_cff_topology.py\",\n      \"sha256\": \"89a85392e61cb492fcd208adac7be5129b1e4834bf5374226a2c4aa9ea815a35\",\n      \"size\": 10377\n    },\n    {\n      \"path\": \"worker/af_codex_import.py\",\n      \"sha256\": \"5bd76abaaaf4306b4d2814cd59e234d906551edd5fcf0f8dced176de105a2774\",\n      \"size\": 7308\n    },\n    {\n      \"path\": \"worker/af_common_cut_compare.py\",\n      \"sha256\": \"cb4316b97ec072c247c1e3bff949977eebfea38962a7880c206fbfeed1ec2234\",\n      \"size\": 8856\n    },\n    {\n      \"path\": \"worker/af_compare.py\",\n      \"sha256\": \"3f7d550a18760b73ec545fed4e400eb91951d2583a25dbf306799d5b2b3277d1\",\n      \"size\": 5655\n    },\n    {\n      \"path\": \"worker/af_core.py\",\n      \"sha256\": \"6c322c65c902f60998639f68b2c5bae8943e8e6a2d4be54019ee43701e14dad4\",\n      \"size\": 18791\n    },\n    {\n      \"path\": \"worker/af_dual_baffles.py\",\n      \"sha256\": \"6b9910c1afe526dc93ec93e9b6c0af6dd25755039a094e3e61b50f5fb2cb3c66\",\n      \"size\": 4087\n    },\n    {\n      \"path\": \"worker/af_flow_diagnostics.py\",\n      \"sha256\": \"5b0285c6302a9bf5520605045e9f29c0cdd58bda577ba634cf51e85ce64b8d2c\",\n      \"size\": 18777\n    },\n    {\n      \"path\": \"worker/af_geometry_precheck.py\",\n      \"sha256\": \"fcd7cfe2f89a36312dcc09680de47314aea8f72701c3fcf55a36228c43ca1122\",\n      \"size\": 9862\n    },\n    {\n      \"path\": \"worker/af_geometry_scout.py\",\n      \"sha256\": \"ed0b4ce0870f9e7e53458456143798d63f9b22f1673d7e2add8e38975fcc971b\",\n      \"size\": 8315\n    },\n    {\n      \"path\": \"worker/af_h5_batch.py\",\n      \"sha256\": \"87e61353935205e985bdfb15e8565b64a478fa4213d69071cf01f903d046d688\",\n      \"size\": 6293\n    },\n    {\n      \"path\": \"worker/af_h5_offline.py\",\n      \"sha256\": \"826455256c87b60cdf2e2786f998d6f4681302d7eb11439be6c3316ee725363c\",\n      \"size\": 36492\n    },\n    {\n      \"path\": \"worker/af_h5_schema.py\",\n      \"sha256\": \"819e6a9f01ba220f6e8a8471dccffdfa28e0231cb0651d7ef1a8b2060617ba7e\",\n      \"size\": 4153\n    },\n    {\n      \"path\": \"worker/af_live_capture.py\",\n      \"sha256\": \"28e9e45eb06f622aaf82cbb204bef8180f8bba68481ef8f774203a8f73a35d53\",\n      \"size\": 6880\n    },\n    {\n      \"path\": \"worker/af_metrics.py\",\n      \"sha256\": \"34fd54d63c65d7872e011de9cb731fe1387cb2e9c1a1d4bcd2ba91a5bba24a04\",\n      \"size\": 4289\n    },\n    {\n      \"path\": \"worker/af_model_config.py\",\n      \"sha256\": \"8f01874f4b7047bdbe8ee52a326b8205f1527874c9046ae8de565d982af048d5\",\n      \"size\": 8525\n    },\n    {\n      \"path\": \"worker/af_native_11_9_reference.py\",\n      \"sha256\": \"fa1c14b128b2c8730a408e3153dc7c201de16c299897709e3187cee4704345b1\",\n      \"size\": 18071\n    },\n    {\n      \"path\": \"worker/af_native_flux_map.py\",\n      \"sha256\": \"a99029b846910f8a251fe2e71cf89c28048231462f374f17e80ffdf3516f5288\",\n      \"size\": 6917\n    },\n    {\n      \"path\": \"worker/af_native_profile.py\",\n      \"sha256\": \"0f6ec07b92364e59dc75fc2163a6676df4428c0ba4311a4979e3ff02a6b30a4f\",\n      \"size\": 17734\n    },\n    {\n      \"path\": \"worker/af_pairing.py\",\n      \"sha256\": \"b2218b9a3c4c2d774fcaa83eb4f090a9a27c217641e309af84046b5a774d21be\",\n      \"size\": 3993\n    },\n    {\n      \"path\": \"worker/af_port_only.py\",\n      \"sha256\": \"2f7e2fd57d18d48fb6517d350a96721915d1eff8d6fdef1a236359c5575f6fd7\",\n      \"size\": 9222\n    },\n    {\n      \"path\": \"worker/af_progress.py\",\n      \"sha256\": \"b9b80044e441e11712763640c0419eda2c8715622f8ca95aedc1baccb00f2ae6\",\n      \"size\": 2831\n    },\n    {\n      \"path\": \"worker/af_render_saved.py\",\n      \"sha256\": \"3a3e5d1e4d6b8bf338a9b4f3df40edb1e52e7421554dab096635f280243a0b69\",\n      \"size\": 2239\n    },\n    {\n      \"path\": \"worker/af_report_library.py\",\n      \"sha256\": \"9616d72b134efa7658912070e1116bc699b6a056f5266fb970c44a18cf97eaf1\",\n      \"size\": 7243\n    },\n    {\n      \"path\": \"worker/af_report_summary.py\",\n      \"sha256\": \"a1bf0dd5f11d23b7dcb6270ad4c4f3f9a39b5b74c1fa6bedb66b6c055671c9c0\",\n      \"size\": 12841\n    },\n    {\n      \"path\": \"worker/af_report_v11.py\",\n      \"sha256\": \"8f80684dbce1be181a5d7762f8c735762680765b5faadd5d178c335738de1652\",\n      \"size\": 20853\n    },\n    {\n      \"path\": \"worker/af_report_v3.py\",\n      \"sha256\": \"0e9675bdf3b26b54c4fa1f1931fd59e289e5baff26bc57fdd171af340beff8dd\",\n      \"size\": 56163\n    },\n    {\n      \"path\": \"worker/af_rt0.py\",\n      \"sha256\": \"d4f08ca4cb358ae25f297aab0c776aab71e1d214eb7666d810b9bd4e7bedc9ad\",\n      \"size\": 5117\n    },\n    {\n      \"path\": \"worker/af_saved_analysis.py\",\n      \"sha256\": \"6cb254ff91445376f840001567394dec040dd0b793ef20bbb31ccc25d5d83bf3\",\n      \"size\": 13050\n    },\n    {\n      \"path\": \"worker/af_saved_compare.py\",\n      \"sha256\": \"3a000b9973398dfff543607eb7f0350965ec0c61d770fc176a0aae974d5260bb\",\n      \"size\": 2388\n    },\n    {\n      \"path\": \"worker/af_set_compare.py\",\n      \"sha256\": \"b8e2631cf95f28702cde95f26aa31efcdba33de749c3208ad9d1f5e3896e42c2\",\n      \"size\": 7169\n    },\n    {\n      \"path\": \"worker/af_species_exposure.py\",\n      \"sha256\": \"986050100ea9a7d95952d0044216d09e559cbc7ee38870805eca208948245214\",\n      \"size\": 15200\n    },\n    {\n      \"path\": \"worker/af_species_report.py\",\n      \"sha256\": \"e907148b8b1c146a1a1b79194349ba0cfc5260644ba006ba43eaeae790433a7b\",\n      \"size\": 18784\n    },\n    {\n      \"path\": \"worker/af_target_lock.py\",\n      \"sha256\": \"ce649823c1d3df42a0bc4e92ccd642fd09b615a0c40eefa10c043aa8d40f768e\",\n      \"size\": 1872\n    },\n    {\n      \"path\": \"worker/af_wafer_patches.py\",\n      \"sha256\": \"ef7230d275dba8686fe2bc6a367f52661c153d6b35e90ca1eddd4426b73a92e7\",\n      \"size\": 26628\n    },\n    {\n      \"path\": \"worker/af_wall_reaction.py\",\n      \"sha256\": \"4376a565a4422abc82159b41e5f24015684bcdd5cc5f84fbe8ed0dc9ce46bf58\",\n      \"size\": 29768\n    },\n    {\n      \"path\": \"worker/af_wb_bootstrap.py\",\n      \"sha256\": \"d2bdb113794ecba33542a90afac6ae2770107eb812f8933aa1e059c774af720e\",\n      \"size\": 1904\n    },\n    {\n      \"path\": \"worker/af_wb_bridge.py\",\n      \"sha256\": \"15d5e6cf4188c5f02f5ddd57d8dca92cd903188678e5583b2975af9032467e15\",\n      \"size\": 27069\n    },\n    {\n      \"path\": \"worker/af_wb_conditions.py\",\n      \"sha256\": \"4a64900a9b36ad96de82f0f91e53d77c915f2e26f9bf4d9751b498edbbbcfbc6\",\n      \"size\": 13304\n    },\n    {\n      \"path\": \"worker/af_wb_files.py\",\n      \"sha256\": \"e29e442829790f13af28d81a2b5d40023c445c2152fa8caaac12aae252a79b0f\",\n      \"size\": 2227\n    },\n    {\n      \"path\": \"worker/af_wb_solver.py\",\n      \"sha256\": \"5e72994bbd28ec4f17489e964ce10d1c8fb42dead028af6dc9effb3b0e6bea10\",\n      \"size\": 13611\n    },\n    {\n      \"path\": \"worker/af_worker.py\",\n      \"sha256\": \"5f9b0e5666ccd7f9d9bd99cff3c03399a2a546165ba504e85c05b582315e91e8\",\n      \"size\": 3293\n    },\n    {\n      \"path\": \"worker/am_workbench_bridge.py\",\n      \"sha256\": \"5c405403ccd70684e8f7d92e9abad1b5b8fd7629c1b73e01e6f4cd5480af3c44\",\n      \"size\": 5562\n    }\n  ],\n  \"conflicts\": [],\n  \"missing_core_files\": [\n    \"cmd/autofluent/main_windows.go\",\n    \"go.mod\"\n  ],\n  \"missing_worker_imports\": [],\n  \"source_note\": \"56 original readable embedded project files; complete Go front-end/build sources not recovered. No executable was run. Static extraction is not a functional validation.\"\n}\n")
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
    literal_worker_refs=set()
    for name,data in files.items():
        if not(name.startswith("worker/") and name.endswith(".py")):continue
        tree=ast.parse(data.decode("utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=="with_name" and len(node.args)==1:
                arg=node.args[0]
                if isinstance(arg,ast.Constant) and isinstance(arg.value,str) and WORKER.fullmatch(arg.value):
                    literal_worker_refs.add("worker/"+arg.value)
    missing_worker_resources=sorted(literal_worker_refs-set(files))
    go_recovered=all(k in files for k in ("go.mod","cmd/autofluent/main_windows.go"))
    worker_count=sum(k.startswith("worker/") and k.endswith(".py") for k in files)
    records=[{"path":k,"sha256":new[k],"size":len(files[k])} for k in sorted(files)]
    inventory={"release":tag,"published_date":"2026-11-22","asset":"app/AutoFluent.exe",
       "size":len(blob),"sha256":digest,"archives":archives,"archive_scopes":scopes,
       "recovered":records,"conflicts":conflicts,"missing_worker_imports":missing_imports,
       "missing_report_assets":missing_resources,"worker_literal_resource_references":sorted(literal_worker_refs),"missing_worker_resources":missing_worker_resources,"go_frontend_source_recovered":go_recovered,
       "comparison_to_previous_payload":differences,"baseline_commit":args.baseline,
       "checks":{"sha256":"pass","pe_signature":"pass","python_ast":"pass","json_parse":"pass","internal_worker_imports":"pass"},
       "source_note":"Static extraction only; original application files recovered verbatim. No EXE or recovered application code executed. Runtime dependencies and rebuildability not certified."}
    txt("docs/release_inventory.json",json.dumps(inventory,ensure_ascii=False,indent=2)+"\n")
    txt("SHA256SUMS.txt",digest+"  app/AutoFluent.exe\n"+"".join(r["sha256"]+"  "+r["path"]+"\n" for r in records))
    # Retain established homepage. Current release references and actual recovery
    # statistics are refreshed without inventing new product features.
    readme=version(readme)
    readme=re.sub(r"本次已从集成程序原样恢复 \d+ 个项目文件，其中 \d+ 个 Python 分析模块。","",readme)
    if "## What’s new in 2.1" not in readme:
        readme=readme.replace("## What you can do","## What’s new in 2.1\n\n- **独立物种库存统计**：将气相物种库存计算提前，即使晶圆几何未成功识别，也可在相关字段有效时保留已取得的库存数据。\n- **ALD 阶段分析适配**：有物种分析数据时即可进入阶段报告逻辑，不再要求入口同时含四种指定物种；阶段快照携带库存统计。\n- **完整报告资源同步**：从当前发布程序恢复 HTML、CSS、JavaScript 及可取得的分析资源。\n\n## What you can do")
    note=f"本次已从集成程序原样恢复 {len(files)} 个项目文件，其中 {worker_count} 个 Python 分析模块。"
    if missing_resources:note+=" V11 报告独立渲染资源缺失："+", ".join(missing_resources)+"。"
    if missing_worker_resources:note+=" Worker 引用的独立资源未恢复："+", ".join(missing_worker_resources)+"。"
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
