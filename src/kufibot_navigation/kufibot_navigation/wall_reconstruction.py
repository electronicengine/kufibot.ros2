"""Robust line extraction from measured cells, without changing occupancy.

Spatial components bound the search; deterministic RANSAC rejects outliers,
orthogonal least squares removes grid stair steps, and observed free cells split
the fitted lines. No endpoint extrapolation or room-size raster is used.
"""
import math
import random

from .boundary_map import boundary_paths


def reconstruct_walls(cells, free, resolution=.1, max_gap=.35, tolerance=.065):
    cells, free = set(cells), set(free)
    # Solid objects contribute their surface, not spurious lines through their interior.
    surface = {p for p in cells if any((p[0]+dx, p[1]+dy) not in cells
                                      for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))}
    radius = math.ceil(max_gap/resolution)
    offsets = [(x, y) for x in range(-radius, radius+1) for y in range(-radius, radius+1)
               if 0 < math.hypot(x, y)*resolution <= max_gap]
    remaining = set(surface)
    components = []
    for seed in sorted(surface):
        if seed not in remaining:
            continue
        remaining.remove(seed)
        component, stack = [], [seed]
        while stack:
            p = stack.pop()
            component.append(p)
            for dx, dy in offsets:
                q = (p[0]+dx, p[1]+dy)
                if q in remaining:
                    remaining.remove(q)
                    stack.append(q)
        components.append(sorted(component))
    segments, covered = [], set()
    rng = random.Random(0)
    for component in components:
        # Bound pathological dense/complex components. Adjacent chunks still
        # retain their measured fallback outlines; nothing is silently dropped.
        for chunk_start in range(0, len(component), 2048):
            pool = component[chunk_start:chunk_start+2048]
            source_points = tuple(pool)
            for _ in range(16):
                if len(pool) < 4:
                    break
                best, best_score = [], (0, -math.inf)
                for _ in range(48):
                    a, b = rng.sample(pool, 2)
                    dx, dy = b[0]-a[0], b[1]-a[1]
                    length = math.hypot(dx, dy)
                    if length*resolution < .3:
                        continue
                    errors = [(abs((p[0]-a[0])*dy-(p[1]-a[1])*dx)/length*resolution, p) for p in pool]
                    inliers = [p for error, p in errors if error <= tolerance]
                    score = (len(inliers), -sum(error for error, _ in errors if error <= tolerance))
                    if score > best_score:
                        best, best_score = inliers, score
                if len(best) < 4:
                    break
                cx = sum(p[0] for p in best)/len(best)
                cy = sum(p[1] for p in best)/len(best)
                xx = sum((p[0]-cx)**2 for p in best)
                yy = sum((p[1]-cy)**2 for p in best)
                xy = sum((p[0]-cx)*(p[1]-cy) for p in best)
                angle = .5*math.atan2(2*xy, xx-yy)
                ux, uy = math.cos(angle), math.sin(angle)
                # A measured corner may support both intersecting walls even
                # after RANSAC consumed it for the first line. Reuse support,
                # never extrapolate an endpoint into unmeasured space.
                ordered = sorted(((p[0]-cx)*ux+(p[1]-cy)*uy, p) for p in source_points
                                 if abs((p[0]-cx)*uy-(p[1]-cy)*ux)*resolution <= tolerance)

                def point(t):
                    return [(cx+t*ux)*resolution, (cy+t*uy)*resolution]

                def observed_gap(a, b):
                    count = max(1, math.ceil((b-a)*3))
                    for i in range(count+1):
                        t = a+(b-a)*i/count
                        key = (round(cx+t*ux), round(cy+t*uy))
                        if key in free and key not in cells:
                            return True
                    return False

                groups = []
                for t, p in ordered:
                    if (not groups or (t-groups[-1][-1][0])*resolution > max_gap
                            or observed_gap(groups[-1][-1][0], t)):
                        groups.append([])
                    groups[-1].append((t, p))
                for group in groups:
                    if len(group) < 4 or (group[-1][0]-group[0][0])*resolution < .45:
                        continue
                    rms = math.sqrt(sum(((p[0]-cx)*uy-(p[1]-cy)*ux)**2 for _, p in group)/len(group))*resolution
                    path = [[round(v, 3) for v in point(t)] for t in (group[0][0], group[-1][0])]
                    segments.append(dict(points=path, support_count=len(group), rms_error_m=round(rms, 4),
                        max_sample_gap_m=round(max(b[0]-a[0] for a, b in zip(group, group[1:]))*resolution, 3),
                        source='inferred_from_measured_obstacles'))
                    covered.update(p for _, p in group)
                used = set(best)
                pool = [p for p in pool if p not in used]
    paths = [s['points'] for s in segments]
    paths += boundary_paths([[x*resolution, y*resolution] for x, y in surface-covered])
    return paths, segments
