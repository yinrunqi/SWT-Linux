"""用 Fusion 360 Gallery Assembly 交叉验证 AutoMate 的装配统计，并补两类 AutoMate 没有的信号。

只做离线统计，不作为评测集，输出里不含任何 uuid 或设计名。许可仅限非商业研究。

与 AutoMate 同口径的统计：关节类型、接地、刚体组 Grübler 活动度、闭环签名、到地开链、首个关节。
Fusion 独有：
  1. 顺序：关节与零件都带 timeline_index，可以检验「从地生长」「先刚性后运动」「闭环最后」；
  2. 接口：每个关节引用的几何（边 / 面、圆 / 直线 / 柱面 / 平面、关键点类型）；
  3. 运动限位：每类关节启用上下限的比例；
  4. 接触面类型与孔特征类型分布；
  5. 行业 / 类别标签。

近似：关节一端为空（连到根组件）视为接地；嵌套组件若本身不参与任何关节，视为与父组件刚性连接。

输出 datasets/fusion360_assembly/stats.json。

用法:
    venv\\Scripts\\python.exe -X utf8 datasets\\fusion360_assembly\\mine.py
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
RAW_DIR = HERE / "raw"
OUT_PATH = HERE / "stats.json"
sys.path.insert(0, str(HERE.parent))

from automate.mine import DSU, MATE_DOF, MAX_LOOP_BODIES, fundamental_cycles, order_growth, pct  # noqa: E402

TO_ONSHAPE = {
    "RigidJointType": "FASTENED", "RevoluteJointType": "REVOLUTE", "SliderJointType": "SLIDER",
    "CylindricalJointType": "CYLINDRICAL", "PinSlotJointType": "PIN_SLOT", "PlanarJointType": "PLANAR",
    "BallJointType": "BALL",
}
LIMIT_KEYS = ("rotation_limits", "slide_limits", "pitch_limits", "yaw_limits", "roll_limits",
              "primary_slide_limits", "secondary_slide_limits")


def walk_tree(tree: dict, parent: str | None, out: dict) -> None:
    for k, v in tree.items():
        out[k] = parent
        walk_tree(v, k, out)


def geom_sig(g: dict | None) -> str:
    if not isinstance(g, dict):
        return "origin"
    if g.get("type") == "JointOrigin":
        return "joint_origin"
    ent = g.get("entity_one") or {}
    kind = ent.get("curve_type") or ent.get("surface_type") or ent.get("type") or "?"
    kind = kind.replace("3DCurveType", "").replace("SurfaceType", "").replace("CurveType", "")
    key = (g.get("key_point_type") or "").replace("KeyPoint", "")
    return f"{kind}@{key}" if key else kind


def rank01(values: list[int], x: int) -> float:
    return 0.0 if len(values) <= 1 else sorted(values).index(x) / (len(values) - 1)


def mine(files: list[Path], seed: int) -> dict:
    rng = random.Random(seed)
    c = Counter()
    jtype, jtype_ab, interface, limits, limit_total = Counter(), Counter(), defaultdict(Counter), Counter(), Counter()
    contact_types, hole_types, industries, categories = Counter(), Counter(), Counter(), Counter()
    grounding, g_rank_vol, g_rank_tl = Counter(), Counter(), Counter()
    mobility, loops_per, loop_sig, loop_len = Counter(), Counter(), Counter(), Counter()
    chain_sig, first_joint, depth_hist, pair_combo = Counter(), Counter(), Counter(), Counter()
    type_rank = defaultdict(list)
    growth, growth_rand, close_rank, tree_rank = [], [], [], []
    ground_first_occ = Counter()

    for f in files:
        try:
            a = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            c["bad_json"] += 1
            continue
        c["assemblies"] += 1
        props = a.get("properties") or {}
        for h in a.get("holes") or []:
            hole_types[h.get("type", "?")] += 1
        for x in a.get("contacts") or []:
            s1 = x["entity_one"].get("surface_type", "?").replace("SurfaceType", "")
            s2 = x["entity_two"].get("surface_type", "?").replace("SurfaceType", "")
            contact_types["+".join(sorted((s1, s2)))] += 1

        joints = [(j, False) for j in (a.get("joints") or {}).values()]
        joints += [(j, True) for j in (a.get("as_built_joints") or {}).values()]
        if not joints:
            continue
        c["with_joints"] += 1
        for tag in props.get("industries") or []:
            industries[tag] += 1
        for tag in props.get("categories") or []:
            categories[tag] += 1

        occs = a.get("occurrences") or {}
        ids = list(occs)
        index = {k: i for i, k in enumerate(ids)}
        n = len(ids)
        ground = n
        parent: dict[str, str | None] = {}
        walk_tree((a.get("tree") or {}).get("root") or {}, None, parent)

        edges = []
        for j, as_built in joints:
            t = TO_ONSHAPE.get((j.get("joint_motion") or {}).get("joint_type"), "?")
            (jtype_ab if as_built else jtype)[t] += 1
            jm = j.get("joint_motion") or {}
            for k in LIMIT_KEYS:
                if isinstance(jm.get(k), dict):
                    limit_total[t] += 1
                    limits[t] += bool(jm[k].get("is_maximum_value_enabled") or jm[k].get("is_minimum_value_enabled"))
            if not as_built:
                sig = "|".join(sorted((geom_sig(j.get("geometry_or_origin_one")), geom_sig(j.get("geometry_or_origin_two")))))
                interface[t][sig] += 1
            o1, o2 = j.get("occurrence_one"), j.get("occurrence_two")
            x = index.get(o1, ground) if o1 is not None else ground
            y = index.get(o2, ground) if o2 is not None else ground
            if (o1 is not None and o1 not in index) or (o2 is not None and o2 not in index):
                c["joint_unknown_occ"] += 1
                continue
            edges.append((x, y, t, j.get("timeline_index")))

        jointed = {e[0] for e in edges} | {e[1] for e in edges}
        grounded = {index[k] for k, o in occs.items() if o.get("is_grounded")}
        to_root = any(ground in (x, y) for x, y, _, _ in edges)
        grounding[("grounded" if grounded else "") + ("+root_joint" if to_root else "") or "none"] += 1
        vol = [((occs[k].get("physical_properties") or {}).get("volume") or 0.0) for k in ids]
        inferred = not grounded and not to_root
        if inferred and jointed - {ground}:
            grounded = {max(jointed - {ground}, key=lambda i: vol[i])}

        occ_tl = {i: occs[k]["timeline_index"] for k, i in index.items() if "timeline_index" in occs[k]}
        if len(grounded) == 1 and n >= 3:
            g0 = next(iter(grounded))
            if not inferred:
                g_rank_vol[min(sorted(range(n), key=lambda i: -vol[i]).index(g0), 3)] += 1
                if g0 in occ_tl and len(occ_tl) >= 3:
                    g_rank_tl[min(sorted(occ_tl, key=occ_tl.get).index(g0), 3)] += 1
        if occ_tl and len(occ_tl) >= 3 and grounded and not inferred:
            ground_first_occ[min(occ_tl, key=occ_tl.get) in grounded] += 1

        dsu = DSU(n + 1)
        for gi in grounded:
            dsu.union(gi, ground)
        for k, p in parent.items():
            if k in index and p in index and index[k] not in jointed:
                dsu.union(index[k], index[p])
        for x, y, t, _ in edges:
            if t == "FASTENED":
                dsu.union(x, y)
        roots = sorted({dsu.find(i) for i in range(n + 1)})
        rid = {r: k for k, r in enumerate(roots)}
        grp = [rid[dsu.find(i)] for i in range(n + 1)]
        g = grp[ground]

        timed = sorted([e for e in edges if e[3] is not None], key=lambda e: e[3])
        if len(timed) >= 3:
            tls = [e[3] for e in timed]
            for x, y, t, tl in timed:
                type_rank[t].append(rank01(tls, tl))
            pairs = [(x, y) for x, y, _, _ in timed]
            growth.append(order_growth(pairs, grounded | ({ground} if to_root else set())))
            shuffled = pairs[:]
            rng.shuffle(shuffled)
            growth_rand.append(order_growth(shuffled, grounded | ({ground} if to_root else set())))
            kin_timed = [(grp[x], grp[y], tl) for x, y, t, tl in timed if t != "FASTENED" and grp[x] != grp[y]]
            if len(kin_timed) >= 3:
                d2 = DSU(len(roots))
                tl_k = [e[2] for e in kin_timed]
                for x, y, tl in kin_timed:
                    if d2.find(x) == d2.find(y):
                        close_rank.append(rank01(tl_k, tl))
                    else:
                        tree_rank.append(rank01(tl_k, tl))
                        d2.union(x, y)

        joint_map: dict[tuple[int, int], list[str]] = defaultdict(list)
        for x, y, t, _ in edges:
            if t != "FASTENED" and grp[x] != grp[y]:
                joint_map[tuple(sorted((grp[x], grp[y])))].append(t)
        for types in joint_map.values():
            if len(types) >= 2:
                pair_combo["+".join(sorted(types))] += 1
        adj: dict[int, list[tuple[int, str]]] = defaultdict(list)
        for (x, y), types in joint_map.items():
            lab = "+".join(sorted(types))
            adj[x].append((y, lab))
            adj[y].append((x, lab))
        path = {g: ()}
        dq = deque([g])
        while dq:
            u = dq.popleft()
            for v, lab in adj[u]:
                if v not in path:
                    path[v] = path[u] + (lab,)
                    dq.append(v)
        if len(path) == 1:
            c["static_only"] += 1
            continue
        c["has_ground_connected_motion"] += 1
        c["motion_with_inferred_ground"] += inferred
        sub = [(x, y, t) for (x, y), t in joint_map.items() if x in path and y in path]
        removed = sum(6 - MATE_DOF.get(t, 0) for _, _, ts in sub for t in ts)
        mobility[max(-3, min(6 * (len(path) - 1) - removed, 6))] += 1
        if len(path) <= MAX_LOOP_BODIES:
            cyc = fundamental_cycles(len(roots), [(x, y, "+".join(sorted(t))) for x, y, t in sub])
            loops_per[min(len(cyc), 5)] += 1
            for labs in cyc:
                loop_len[min(len(labs), 8)] += 1
                if len(labs) <= 6:
                    loop_sig["|".join(sorted(labs))] += 1
        for v, p in path.items():
            if v == g:
                continue
            depth_hist[min(len(p), 6)] += 1
            if len(p) <= 3:
                chain_sig[" > ".join(p)] += 1
            if len(p) == 1:
                first_joint[p[0]] += 1

    mean = lambda v: round(float(np.mean(v)), 4) if v else None  # noqa: E731
    return {
        "counts": dict(c),
        "joint_type": pct(jtype),
        "as_built_joint_type": pct(jtype_ab),
        "joint_interface_top": {t: pct(cnt, 8) for t, cnt in sorted(interface.items(), key=lambda kv: -sum(kv[1].values()))},
        "limit_enabled_pct": {t: round(100 * limits[t] / limit_total[t], 1) for t in limit_total},
        "grounding_method": pct(grounding),
        "grounded_rank_by_volume": pct(g_rank_vol),
        "grounded_rank_by_insert_order": pct(g_rank_tl),
        "first_inserted_is_grounded": pct(ground_first_occ),
        "order": {
            "growth_from_ground_timeline": mean(growth),
            "growth_from_ground_random": mean(growth_rand),
            "n_assemblies": len(growth),
            "joint_type_mean_rank_0first_1last": {t: {"n": len(v), "mean": mean(v)} for t, v in type_rank.items()},
            "loop_closing_joint_mean_rank": mean(close_rank),
            "tree_joint_mean_rank": mean(tree_rank),
            "n_loop_closing": len(close_rank),
        },
        "pair_combo_top": pct(pair_combo, 15),
        "grubler_mobility_ground_component": pct(mobility),
        "independent_loops_per_assembly": pct(loops_per),
        "loop_length": pct(loop_len),
        "loop_signature_top": pct(loop_sig, 20),
        "joint_depth_from_ground": pct(depth_hist),
        "chain_to_ground_top": pct(chain_sig, 20),
        "first_joint_to_ground": pct(first_joint, 10),
        "contact_surface_pairs_top": pct(contact_types, 10),
        "hole_types_top": pct(hole_types, 12),
        "industries_with_joints": pct(industries, 12),
        "categories_with_joints": pct(categories, 15),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    files = sorted(RAW_DIR.rglob("assembly.json"))
    print(f"[fusion] {len(files)} assembly.json")
    out = {"source": "Fusion 360 Gallery Assembly Dataset a1.0.0 (Autodesk), non-commercial research license",
           **mine(files, args.seed)}
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[fusion] -> {OUT_PATH}  {out['counts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
