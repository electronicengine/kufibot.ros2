"""Trace persistent occupied-cell boundaries without a whole-room raster."""
import math


def boundary_paths(points):
    """Exact grid outlines, preserving gaps/holes and distant wall components.

    Work is proportional to measured cells rather than the map's bounding box;
    a distant point can never make all existing walls disappear. Grid corners
    use integer coordinates until the final conversion to metres.
    """
    cells = {(round(x/.1), round(y/.1)) for x, y in points
             if math.isfinite(x) and math.isfinite(y)}
    edges = set()
    for x, y in cells:
        if (x, y-1) not in cells:
            edges.add(((x, y), (x+1, y)))
        if (x+1, y) not in cells:
            edges.add(((x+1, y), (x+1, y+1)))
        if (x, y+1) not in cells:
            edges.add(((x+1, y+1), (x, y+1)))
        if (x-1, y) not in cells:
            edges.add(((x, y+1), (x, y)))
    outgoing = {}
    for a, b in edges:
        outgoing.setdefault(a, set()).add(b)
    paths = []
    directions = {(1, 0): 0, (0, 1): 1, (-1, 0): 2, (0, -1): 3}
    for first in sorted(edges):
        if first[1] not in outgoing[first[0]]:
            continue
        start, current = first
        vertices = [start]
        previous = start
        outgoing[start].remove(current)
        while current != start:
            vertices.append(current)
            candidates = outgoing[current]
            incoming = directions[(current[0]-previous[0], current[1]-previous[1])]
            # Prefer left turns at diagonally touching cells, so their outlines
            # stay separate instead of bridging across unknown space.
            def priority(p):
                direction = directions[(p[0]-current[0], p[1]-current[1])]
                return {1: 0, 0: 1, 3: 2, 2: 3}[(direction-incoming) % 4]
            following = min(candidates, key=priority)
            candidates.remove(following)
            previous, current = current, following
        twice_area = sum(a[0]*b[1]-b[0]*a[1]
                         for a, b in zip(vertices, vertices[1:]+vertices[:1]))
        if twice_area == 2:
            continue  # One isolated measurement does not establish a wall.
        corners = []
        for i, v in enumerate(vertices):
            a, b = vertices[i-1], vertices[(i+1) % len(vertices)]
            if (v[0]-a[0])*(b[1]-v[1]) != (v[1]-a[1])*(b[0]-v[0]):
                corners.append([round(v[0]*.1-.05, 3), round(v[1]*.1-.05, 3)])
        paths.append(corners+[corners[0]])
    return paths
