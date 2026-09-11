"""Startup-fixed measured grid shared by planning, execution and viewers."""
import math
import time
import uuid
from collections import OrderedDict

from .boundary_map import boundary_paths
from .wall_reconstruction import reconstruct_walls


class MetricMap:
    resolution = .1
    max_cells = 16384

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.id = uuid.uuid4().hex
        self.revision = 0
        self.occupied = OrderedDict()
        self.free = OrderedDict()
        self.transient = set()
        self.clear_evidence = {}
        self.measured_at = None
        self._boundary_signature = None
        self.capacity_reached = False
        self.rejected_rays = 0
        self._boundaries = []
        self._wall_signature = None
        self._wall_paths, self._wall_segments = [], []
        self._wall_executor = None
        self._wall_job = None

    def enable_background_reconstruction(self):
        """Keep fitting work off the ROS motor/sensor callback thread."""
        from concurrent.futures import ThreadPoolExecutor
        self._wall_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='wall-fit')

    def close(self):
        if self._wall_executor:
            self._wall_executor.shutdown(wait=False, cancel_futures=True)

    def _reconstruct(self, signature):
        if self._wall_executor is None:
            if self._wall_signature != signature:
                self._wall_paths, self._wall_segments = reconstruct_walls(*signature, self.resolution)
                self._wall_signature = signature
            return
        if self._wall_job and self._wall_job[1].done():
            source, job = self._wall_job
            self._wall_job = None
            if source == signature:
                self._wall_paths, self._wall_segments = job.result()
                self._wall_signature = source
        if self._wall_signature != signature and self._wall_job is None:
            self._wall_job = (signature, self._wall_executor.submit(
                reconstruct_walls, *signature, self.resolution))

    def key(self, x, y):
        return round(x / self.resolution), round(y / self.resolution)

    def point(self, key):
        return [round(v * self.resolution, 3) for v in key]

    def ray(self, origin, bearing_deg, distance, hit=True, measured_at=None, dynamic=False):
        """Accumulate geometry with conservative, observed clearing evidence.

        Conflicting rays stop adding free cells until clearing is confirmed.
        Occupied capacity is explicit: retain old geometry and reject new cells
        rather than silently deleting established walls from an LRU cache.
        """
        if (not isinstance(origin, (list, tuple)) or len(origin) != 2
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                       for v in (*origin, bearing_deg, distance)) or distance <= 0):
            self.rejected_rays += 1
            return False
        if measured_at is not None and (not math.isfinite(measured_at) or
                (self.measured_at is not None and measured_at < self.measured_at)):
            self.rejected_rays += 1
            return False
        hit = bool(hit) and distance <= 8.
        distance = min(distance, 8.)
        angle = math.radians(bearing_deg)
        dx, dy = math.sin(angle)*distance, math.cos(angle)*distance
        endpoint = [origin[0]+dx, origin[1]+dy]
        end = self.key(*endpoint)
        # Snap only a near-boundary repeat to existing geometry. This small
        # deadband prevents centimetre range noise thickening a wall by a cell.
        if hit and end not in self.occupied:
            nearby = [(math.hypot(endpoint[0]-self.point((x, y))[0],
                                  endpoint[1]-self.point((x, y))[1]), (x, y))
                      for x in range(end[0]-1, end[0]+2) for y in range(end[1]-1, end[1]+2)
                      if (x, y) in self.occupied]
            if nearby:
                separation, nearest = min(nearby)
                if separation <= self.resolution/2 + .02:
                    end = nearest
        count = max(1, math.ceil(distance / (self.resolution / 3)))
        cells = dict.fromkeys(self.key(origin[0]+dx*i/count, origin[1]+dy*i/count)
                              for i in range(count+1))
        changed = False
        now = self.clock() if measured_at is None else measured_at
        # A new return inside measured free space is a temporary obstruction.
        temporary = dynamic or end in self.free or end in self.transient
        for key in cells:
            if hit and key == end:
                break
            if key in self.occupied:
                # Require the beam to pass well beyond the cell, repeatedly.
                # No clock-only decay: absence of observations never clears space.
                if math.dist(self.point(key), endpoint) < .25:
                    break
                first, last, count = self.clear_evidence.get(key, (now, -math.inf, 0))
                if now-last > 1.:
                    first, count = now, 0
                if now > last:
                    count += 1
                self.clear_evidence[key] = (first, now, count)
                minimum, duration = (3, .3) if key in self.transient else (20, 3.)
                if count < minimum or now-first < duration:
                    break
                del self.occupied[key]
                self.transient.discard(key)
                self.clear_evidence.pop(key, None)
                changed = True
            changed |= key not in self.free
            self.free[key] = self.point(key)
            self.free.move_to_end(key)
        if hit:
            self.clear_evidence.pop(end, None)
            changed |= end in self.free
            self.free.pop(end, None)
            if end in self.occupied:
                if dynamic:
                    changed |= end not in self.transient
                    self.transient.add(end)  # a later matched camera result can classify an existing hit
            elif len(self.occupied) < self.max_cells:
                self.occupied[end] = self.point(end)
                if temporary:
                    self.transient.add(end)
                changed = True
            else:
                self.capacity_reached = True
        while len(self.free) > self.max_cells:
            self.free.popitem(last=False)
            changed = True
        if changed:
            self.revision += 1
        self.measured_at = self.clock() if measured_at is None else measured_at
        return True

    @staticmethod
    def compact(snapshot):
        """Lossless row runs keep numerical observations small for the model."""
        data = dict(snapshot)
        resolution = data['resolution_m']
        cells = sorted((round(p[1]/resolution), round(p[0]/resolution))
                       for p in data.pop('free_cells', []))
        runs = []
        for y, x in cells:
            if runs and runs[-1][0] == y and runs[-1][2] + 1 == x:
                runs[-1][2] = x
            else:
                runs.append([y, x, x])
        data['free_cell_runs'] = runs
        data['free_cell_format'] = '[y_index, x_first, x_last_inclusive]; cell centre = index * resolution_m'
        data['unknown_space'] = 'cells outside free_cell_runs and obstacle_points; never assumed free'
        return data

    def snapshot(self, pose, heading, source):
        signature = frozenset(self.occupied.keys() - self.transient)
        if self._boundary_signature != signature:
            self._boundaries = boundary_paths([self.occupied[k] for k in signature])
            self._boundary_signature = signature
        wall_signature = (signature, frozenset(self.free))
        self._reconstruct(wall_signature)
        fitted = self._wall_signature == wall_signature
        points = list(self.free.values()) + list(self.occupied.values())
        bounds = ([min(p[0] for p in points), min(p[1] for p in points),
                   max(p[0] for p in points), max(p[1] for p in points)] if points else None)
        return dict(map_id=self.id, revision=self.revision, frame='startup_robot_pose', units='m',
                    origin=[0., 0.], axes='+x=startup right, +y=startup forward; heading clockwise',
                    resolution_m=self.resolution, measured_at_monotonic_sec=self.measured_at,
                    measurement_age_sec=None if self.measured_at is None else round(self.clock()-self.measured_at, 3),
                    robot_pose=[round(v, 3) for v in pose], robot_heading_deg=round(heading % 360, 1),
                    wall_policy='observed_clearing_only', dynamic_obstacle_points=[self.occupied[k] for k in self.transient if k in self.occupied], occupied_capacity_reached=self.capacity_reached,
                    rejected_rays=self.rejected_rays, pose_source=source, obstacle_points=list(self.occupied.values()),
                    free_cells=list(self.free.values()), boundary_paths=self._boundaries,
                    wall_paths=self._wall_paths if fitted else self._boundaries,
                    wall_segments=self._wall_segments if fitted else [],
                    wall_reconstruction=dict(method='spatial_components_ransac_orthogonal_fit',
                        status='ready' if fitted else 'updating',
                        max_gap_m=.35, residual_tolerance_m=.065, min_support=4,
                        meaning='inferred wall lines; not additional measured occupancy or free space'),
                    measured_bounds_m=bounds, boundary_meaning='measured obstacle outlines, not complete room walls',
                    unknown_space='all cells absent from free_cells and obstacle_points')

    def segment_clear(self, start, end, radius, stopping, initial_pose=None, body_radius=0.):
        """Check swept cells, including lateral footprint and forward braking room.

        The robot's current occupied footprint is known physically, even when
        the forward-only sensor cannot see beneath/behind its own chassis.
        No other unknown cells are exempted.
        """
        if self.capacity_reached:
            return False  # New obstacles can no longer be retained; no route admission.
        dx, dy = end[0]-start[0], end[1]-start[1]
        length = math.hypot(dx, dy)
        if length > 32:  # bound work and reject out-of-map model coordinates
            return False
        ux, uy = ((dx/length, dy/length) if length > 1e-9 else (0., 1.))
        target = [end[0] + ux*stopping, end[1] + uy*stopping]
        low = self.key(min(start[0], target[0])-radius, min(start[1], target[1])-radius)
        high = self.key(max(start[0], target[0])+radius, max(start[1], target[1])+radius)
        pad = self.resolution * math.sqrt(2)/2
        for x in range(low[0], high[0]+1):
            for y in range(low[1], high[1]+1):
                px, py = self.point((x, y))
                along = (px-start[0])*ux + (py-start[1])*uy
                lateral = abs((px-start[0])*uy - (py-start[1])*ux)
                # Forward swept corridor, with a footprint disk at each end.
                near = max(0., -along, along-(length+stopping))
                if math.hypot(near, lateral) > radius + pad:
                    continue
                key = (x, y)
                if key in self.occupied:
                    return False
                if key in self.free:
                    continue
                if initial_pose is not None and math.hypot(px-initial_pose[0], py-initial_pose[1]) <= body_radius + pad:
                    continue
                return False
        return True
