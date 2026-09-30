"""从 AutoMate（Onshape 公开装配，CC0）挖掘通用装配 workflow 的统计证据。

只做离线统计，不作为评测集，输出里不含任何装配 / 零件 id。

两部分：
  1. 配合级（mates.parquet + parts.parquet）：配合类型分布；按零件粗形状类
     （rod / disc / toothed_disc / plate / compact / block）统计「零件对 → 配合类型」。
  2. 装配级（assemblies.zip）：固定零件的选择、刚体组与 Grübler 自由度、
     机构闭环的配合类型签名、开链到地的配合序列、配合列表顺序是否「从地生长」、
     mateRelations（齿轮 / 齿条 / 丝杠等）及其所约束配合的类型。

输出 datasets/automate/stats.json。

用法:
    venv\\Scripts\\python.exe -X utf8 datasets\\automate\\mine.py
    venv\\Scripts\\python.exe -X utf8 datasets\\automate\\mine.py --limit 5000
"""

from __future__ import annotations

import argparse
import json
import random
import struct
import sys
import zipfile
import zlib
from collections import Counter, defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
RAW_DIR = HERE / "raw"
OUT_PATH = HERE / "stats.json"

# Onshape 各配合类型保留的相对自由度
MATE_DOF = {
    "FASTENED": 0, "REVOLUTE": 1, "SLIDER": 1, "CYLINDRICAL": 2,
    "PIN_SLOT": 2, "PLANAR": 3, "BALL": 3, "PARALLEL": 4,
}
MAX_LOOP_BODIES = 400


def shape_class(L: float, W: float, H: float, n_faces: int, n_cyl: int) -> str:
    """按非轴对齐包围盒三边（L ≥ W ≥ H）给零件一个与尺度无关的粗形状类。"""
    if not (L > 0 and W > 0 and H > 0):
        return "unknown"
    if L / W >= 3 and W / H <= 1.5:
        return "rod"
    if W / L >= 0.85 and H / L <= 0.5:
        return "toothed_disc" if n_faces >= 40 else "disc"
    if H / W <= 0.2:
        return "plate"
    if W / L >= 0.6 and H / L >= 0.6:
        return "compact"
    return "block"


def load_parts() -> pd.DataFrame:
    cols = ["part_id", "n_faces", "n_cyl", "mp_amount",
            "nabb_box_0", "nabb_box_1", "nabb_box_2", "nabb_box_3", "nabb_box_4", "nabb_box_5"]
    p = pd.read_parquet(RAW_DIR / "parts.parquet", columns=cols)
    dims = np.stack([p.nabb_box_3 - p.nabb_box_0, p.nabb_box_4 - p.nabb_box_1, p.nabb_box_5 - p.nabb_box_2], axis=1)
    dims = -np.sort(-np.nan_to_num(dims), axis=1)
    p["cls"] = [shape_class(l, w, h, int(f), int(c))
                for (l, w, h), f, c in zip(dims, p.n_faces.fillna(0), p.n_cyl.fillna(0))]
    p["L"] = dims[:, 0]
    return p.set_index("part_id")[["cls", "L", "mp_amount", "n_faces"]]


def pct(counter: Counter, top: int | None = None) -> dict:
    total = sum(counter.values()) or 1
    items = counter.most_common(top)
    return {str(k): {"n": v, "pct": round(100 * v / total, 2)} for k, v in items}


def mine_mates(parts: pd.DataFrame) -> dict:
    m = pd.read_parquet(RAW_DIR / "mates.parquet", columns=["mateType", "parts"])
    cls = parts["cls"].to_dict()
    size = parts["L"].to_dict()
    pair_types: dict[str, Counter] = defaultdict(Counter)
    size_ratio: dict[str, list] = defaultdict(list)
    for mtype, (a, b) in zip(m.mateType, m.parts):
        ca, cb = cls.get(a, "unknown"), cls.get(b, "unknown")
        key = "+".join(sorted((ca, cb)))
        pair_types[key][mtype] += 1
        la, lb = size.get(a, 0.0), size.get(b, 0.0)
        if la > 0 and lb > 0:
            size_ratio[mtype].append(min(la, lb) / max(la, lb))
    pairs = sorted(pair_types.items(), key=lambda kv: -sum(kv[1].values()))
    return {
        "n_mates_raw": int(len(m)),
        "mate_type": pct(Counter(m.mateType)),
        "part_class": pct(Counter(parts["cls"])),
        "pair_to_mate_type": {k: {"n": sum(c.values()), "types": pct(c, 5)} for k, c in pairs[:25]},
        "size_ratio_small_over_large_median": {t: round(float(np.median(v)), 3) for t, v in size_ratio.items()},
    }


class DSU:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        self.p[self.find(a)] = self.find(b)


def fundamental_cycles(n: int, edges: list[tuple[int, int, str]]) -> list[list[str]]:
    """生成树求基本回路；每条回路返回其配合类型列表。平行边（同一对刚体多条配合）也算回路。"""
    adj: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for i, (a, b, _) in enumerate(edges):
        adj[a].append((b, i))
        adj[b].append((a, i))
    parent: dict[int, tuple[int, int]] = {}
    depth: dict[int, int] = {}
    tree_edges: set[int] = set()
    for root in range(n):
        if root in depth:
            continue
        depth[root] = 0
        dq = deque([root])
        while dq:
            u = dq.popleft()
            for v, ei in adj[u]:
                if v not in depth:
                    depth[v] = depth[u] + 1
                    parent[v] = (u, ei)
                    tree_edges.add(ei)
                    dq.append(v)
    cycles = []
    for i, (a, b, t) in enumerate(edges):
        if i in tree_edges or a == b:
            continue
        types = [t]
        u, v = a, b
        while u != v:
            if depth[u] < depth[v]:
                u, v = v, u
            pu, ei = parent[u]
            types.append(edges[ei][2])
            u = pu
        cycles.append(types)
    return cycles


def order_growth(mate_occ: list[tuple[int, int]], seeds: set[int]) -> float:
    placed = set(seeds)
    if not placed and mate_occ:
        placed = {mate_occ[0][0]}
    hit = 0
    for a, b in mate_occ:
        hit += a in placed or b in placed
        placed.update((a, b))
    return hit / len(mate_occ)


def iter_zip_json(path: Path, rng: random.Random):
    """完整 zip 按随机顺序读；未下完的 .part 按本地文件头顺序流式读。"""
    if path.suffix != ".part":
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if n.endswith(".json")]
            rng.shuffle(names)
            for name in names:
                yield name, zf.read(name)
        return
    with path.open("rb") as fh:
        while True:
            head = fh.read(30)
            if len(head) < 30 or head[:4] != b"PK\x03\x04":
                return
            _, _, flag, method, _, _, _, csize, _, nlen, elen = struct.unpack("<IHHHHHIIIHH", head)
            name = fh.read(nlen).decode("utf-8", "replace")
            fh.read(elen)
            data = fh.read(csize)
            if flag & 8 or len(data) < csize:
                return
            if name.endswith(".json"):
                yield name, zlib.decompress(data, -15) if method == 8 else data


def mine_assemblies(parts: pd.DataFrame, zip_path: Path, limit: int, seed: int) -> dict:
    asm = pd.read_parquet(RAW_DIR / "assemblies.parquet",
                          columns=["assemblyId", "is_subassembly", "n_mates", "n_subassemblies"])
    top_df = asm.loc[(~asm.is_subassembly) & (asm.n_mates > 0)]
    top = set(top_df.assemblyId)
    cls = parts["cls"].to_dict()
    vol = parts["mp_amount"].to_dict()
    rng = random.Random(seed)

    seen_sig: set[int] = set()
    c = Counter()
    n_occ_hist, n_mate_hist = Counter(), Counter()
    grounding, fixed_rank_vol, fixed_rank_deg, fixed_cls = Counter(), Counter(), Counter(), Counter()
    pair_combo, mates_per_pair = Counter(), Counter()
    mobility, loops_per, loop_sig, loop_len = Counter(), Counter(), Counter(), Counter()
    chain_sig, first_joint, moving_cls, depth_hist = Counter(), Counter(), Counter(), Counter()
    rel_type, rel_mates, rel_per_asm = Counter(), Counter(), Counter()
    toothed_state, has_groups = Counter(), Counter()
    growth, growth_rand = [], []

    for name, blob in iter_zip_json(zip_path, rng):
        if Path(name).stem not in top:
            continue
        try:
            a = json.loads(blob)
        except json.JSONDecodeError:
            c["bad_json"] += 1
            continue
        occs, mates, plist = a.get("occurrences", []), a.get("mates", []), a.get("parts", [])
        if not occs or not mates:
            continue
        n = len(occs)
        mate2 = [(m["occurrences"][0], m["occurrences"][1], m["mateType"]) for m in mates
                 if len(m.get("occurrences", [])) == 2 and all(0 <= o < n for o in m["occurrences"])]
        mate1 = [(m["occurrences"][0], m["mateType"]) for m in mates
                 if len(m.get("occurrences", [])) == 1 and 0 <= m["occurrences"][0] < n]
        if not mate2:
            continue
        occ_part = [plist[o["part"]]["id"] if 0 <= o.get("part", -1) < len(plist) else "" for o in occs]
        sig = hash(tuple(sorted((t, *sorted((occ_part[x], occ_part[y]))) for x, y, t in mate2)))
        if sig in seen_sig:
            c["dup"] += 1
            continue
        seen_sig.add(sig)
        c["assemblies"] += 1
        if limit and c["assemblies"] > limit:
            break

        n_occ_hist[min(n, 50) // 5 * 5] += 1
        n_mate_hist[min(len(mate2), 50) // 5 * 5] += 1
        has_groups[bool(a.get("mateGroups"))] += 1
        fixed = {i for i, o in enumerate(occs) if o.get("fixed")}
        grounding[("fixed" if fixed else "") + ("+origin_mate" if mate1 else "") or "none"] += 1
        mated = {x for m in mate2 for x in m[:2]}
        inferred = not fixed and not mate1
        ground_occ = fixed or {max(mated, key=lambda i: vol.get(occ_part[i]) or 0)}
        deg = Counter()
        for x, y, _ in mate2:
            deg[x] += 1
            deg[y] += 1
        if len(fixed) == 1 and n >= 3:
            f = next(iter(fixed))
            by_vol = sorted(range(n), key=lambda i: -(vol.get(occ_part[i]) or 0))
            by_deg = sorted(range(n), key=lambda i: -deg[i])
            fixed_rank_vol[min(by_vol.index(f), 3)] += 1
            fixed_rank_deg[min(by_deg.index(f), 3)] += 1
            if occ_part[f] in cls:
                fixed_cls[cls[occ_part[f]]] += 1

        if len(mate2) >= 3:
            pairs = [(x, y) for x, y, _ in mate2]
            growth.append(order_growth(pairs, fixed))
            rng.shuffle(pairs)
            growth_rand.append(order_growth(pairs, fixed))

        # 刚体组：FASTENED 合并，固定件与 FASTENED 到原点的零件并为地；
        # 都没有时以体积最大的已配合零件为地（推断接地，单独计数）
        dsu = DSU(n + 1)
        ground = n
        for f in ground_occ:
            dsu.union(f, ground)
        for o, t in mate1:
            if t == "FASTENED":
                dsu.union(o, ground)
        for x, y, t in mate2:
            if t == "FASTENED":
                dsu.union(x, y)
        roots = sorted({dsu.find(i) for i in range(n + 1)})
        idx = {r: k for k, r in enumerate(roots)}
        g = idx[dsu.find(ground)]
        grp = [idx[dsu.find(i)] for i in range(n)]

        pair_n = Counter(tuple(sorted((x, y))) for x, y, _ in mate2)
        for v in pair_n.values():
            mates_per_pair[min(v, 4)] += 1

        joints: dict[tuple[int, int], list[str]] = defaultdict(list)
        for x, y, t in mate2:
            if t != "FASTENED" and grp[x] != grp[y]:
                joints[tuple(sorted((grp[x], grp[y])))].append(t)
        for o, t in mate1:
            if t != "FASTENED" and grp[o] != g:
                joints[tuple(sorted((grp[o], g)))].append(t)
        for types in joints.values():
            if len(types) >= 2:
                pair_combo["+".join(sorted(types))] += 1

        adj: dict[int, list[tuple[int, str]]] = defaultdict(list)
        for (x, y), types in joints.items():
            label = "+".join(sorted(types))
            adj[x].append((y, label))
            adj[y].append((x, label))
        path = {g: ()}
        dq = deque([g])
        while dq:
            u = dq.popleft()
            for v, label in adj[u]:
                if v not in path:
                    path[v] = path[u] + (label,)
                    dq.append(v)
        reached = set(path)
        if len(reached) == 1:
            c["static_only"] += 1
        else:
            c["has_ground_connected_motion"] += 1
            c["motion_with_inferred_ground"] += inferred
            sub = [(x, y, types) for (x, y), types in joints.items() if x in reached and y in reached]
            dof_removed = sum(6 - MATE_DOF.get(t, 0) for _, _, types in sub for t in types)
            rels = a.get("mateRelations", []) or []
            m_grubler = 6 * (len(reached) - 1) - dof_removed - len(rels)
            mobility[max(-3, min(m_grubler, 6))] += 1
            if len(reached) <= MAX_LOOP_BODIES:
                cyc = fundamental_cycles(len(roots), [(x, y, "+".join(sorted(t))) for x, y, t in sub])
                loops_per[min(len(cyc), 5)] += 1
                for labels in cyc:
                    loop_len[min(len(labels), 8)] += 1
                    if len(labels) <= 6:
                        loop_sig["|".join(sorted(labels))] += 1
            for v, p in path.items():
                if v == g:
                    continue
                depth_hist[min(len(p), 6)] += 1
                if len(p) <= 3:
                    chain_sig[" > ".join(p)] += 1
                if len(p) == 1:
                    first_joint[p[0]] += 1
            for i in range(n):
                if grp[i] in reached and grp[i] != g and occ_part[i] in cls:
                    moving_cls[cls[occ_part[i]]] += 1

        for i in range(n):
            if cls.get(occ_part[i]) == "toothed_disc" and deg[i]:
                toothed_state["moving" if grp[i] in reached and grp[i] != g else "rigid_or_floating"] += 1

        rels = a.get("mateRelations", []) or []
        rel_per_asm[min(len(rels), 5)] += 1
        for r in rels:
            rt = r.get("relationType", "?")
            rel_type[rt] += 1
            mt = [mates[i]["mateType"] for i in r.get("mates", []) if 0 <= i < len(mates)]
            rel_mates[f"{rt}:{'+'.join(sorted(mt))}"] += 1

    total = c["assemblies"] or 1
    return {
        "counts": dict(c),
        "ground_connected_motion_pct": round(100 * c["has_ground_connected_motion"] / total, 2),
        "n_occurrences_hist_bin5": dict(sorted(n_occ_hist.items())),
        "n_mates_hist_bin5": dict(sorted(n_mate_hist.items())),
        "n_subassemblies_top_level": pct(Counter(np.minimum(top_df.n_subassemblies, 5))),
        "uses_mate_groups": pct(has_groups),
        "grounding_method": pct(grounding),
        "single_fixed_rank_by_volume": pct(fixed_rank_vol),
        "single_fixed_rank_by_mate_degree": pct(fixed_rank_deg),
        "single_fixed_shape_class": pct(fixed_cls),
        "mates_per_occurrence_pair": pct(mates_per_pair),
        "pair_combo_top": pct(pair_combo, 20),
        "mate_order_growth_from_ground": {
            "mean": round(float(np.mean(growth)), 4) if growth else None,
            "random_order_mean": round(float(np.mean(growth_rand)), 4) if growth_rand else None,
            "n": len(growth),
        },
        "grubler_mobility_ground_component": pct(mobility),
        "independent_loops_per_assembly": pct(loops_per),
        "loop_length": pct(loop_len),
        "loop_signature_top": pct(loop_sig, 30),
        "joint_depth_from_ground": pct(depth_hist),
        "chain_to_ground_top": pct(chain_sig, 30),
        "first_joint_to_ground": pct(first_joint, 12),
        "moving_occurrence_shape_class": pct(moving_cls),
        "toothed_disc_state": pct(toothed_state),
        "relations_per_assembly": pct(rel_per_asm),
        "relation_type": pct(rel_type),
        "relation_on_mate_types_top": pct(rel_mates, 20),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=0, help="装配级只处理前 N 个去重后的顶层装配")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--skip-assemblies", action="store_true")
    parser.add_argument("--partial-zip", action="store_true", help="调试用：读尚未下完的 assemblies.zip.part")
    args = parser.parse_args()

    parts = load_parts()
    print(f"[automate] parts={len(parts)}")
    out = {"source": "AutoMate (Jones et al. 2021), Zenodo 7776208, CC0", "mates": mine_mates(parts)}
    print("[automate] mate-level done")
    zip_path = RAW_DIR / "assemblies.zip"
    if args.partial_zip:
        zip_path = RAW_DIR / "assemblies.zip.part"
    if not args.skip_assemblies and zip_path.exists():
        out["assemblies"] = mine_assemblies(parts, zip_path, args.limit, args.seed)
        print(f"[automate] assembly-level done: {out['assemblies']['counts']}")
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[automate] -> {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
