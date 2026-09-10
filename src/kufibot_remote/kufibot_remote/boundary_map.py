"""Extract measured obstacle outlines from a metric occupancy grid."""
import cv2
import numpy as np


def boundary_paths(points):
    """Trace occupied cells, preserving gaps and holes (no convex hull).

    Each 10 cm measurement cell is rasterized at 2.5 cm. Adjacent occupied
    cells form walls; disconnected measurements are never bridged across
    unknown space. Polygon simplification removes raster stair steps.
    """
    if not points:
        return []
    cells = np.rint(np.asarray(points, dtype=float) / .1).astype(int)
    low, high = cells.min(axis=0), cells.max(axis=0)
    shape = (high-low+1)*4+4
    if max(shape) > 4096:
        return []
    grid = np.zeros((shape[1], shape[0]), dtype=np.uint8)
    for x, y in (cells-low)*4+2:
        grid[y:y+4, x:x+4] = 255
    contours, _ = cv2.findContours(grid, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    paths = []
    for contour in contours:
        if cv2.contourArea(contour) < 20:
            continue  # isolated cells do not yet establish a boundary
        polygon = cv2.approxPolyDP(contour, 1., True).reshape(-1, 2)
        path = [[round(float(low[0]*.1-.05+(x-2)*.025), 3),
                 round(float(low[1]*.1-.05+(y-2)*.025), 3)] for x, y in polygon]
        paths.append(path + [path[0]])
    return paths
