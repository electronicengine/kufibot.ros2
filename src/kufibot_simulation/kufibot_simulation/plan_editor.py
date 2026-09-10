"""Interactive pygame floor-plan editor: draw rooms, walls, doors and
furniture with the mouse and save them to a FloorPlan-compatible JSON file.

Standalone (no rclpy) so it can run without any ROS graph up.
"""
import copy
import json
import math
import sys
from pathlib import Path

import pygame

from .floorplan import Door, FloorPlan, Obstacle, Room, Wall

WIDTH, HEIGHT = 1100, 750
PX_PER_M_DEFAULT = 60
GRID_M = 0.1
BACKGROUND = (20, 20, 24)
GRID_COLOR = (35, 35, 40)
TEXT_COLOR = (230, 230, 230)
PREVIEW_COLOR = (255, 255, 255)
START_POSE_COLOR = (240, 240, 60)
DOOR_COLOR = (220, 140, 40)

ROOM_PALETTE = [
    (150, 90, 90), (90, 140, 90), (90, 100, 150), (140, 130, 70),
    (120, 90, 140), (80, 140, 140),
]
FURNITURE_PALETTE = [
    (90, 60, 140), (60, 110, 160), (160, 90, 60), (60, 160, 90),
    (160, 160, 60), (160, 60, 120),
]
CATALOG = [
    ('chair', 'Sandalye', .5, .5),
    ('table', 'Masa', 1.2, .8),
    ('sofa', 'Koltuk', 1.8, .9),
    ('cabinet', 'Dolap', 1.2, .55),
    ('desk', 'Bilgisayar masasi', 1.3, .7),
    ('tv', 'TV ve unitesi', 1.2, .4),
    ('coffee_table', 'Sehpa', 1.0, .6),
]
TOOL_NAMES = {'room': 'Oda', 'wall': 'Duvar', 'door': 'Kapi',
              'furniture': 'Esya', 'pose': 'Baslangic konumu'}
TOOL_HELP = [
    '1 Oda   2 Duvar   3 Kapi (mevcut duvar uzerinde iki nokta)   4 Esya   5 Baslangic konumu',
    'N son ekleneni yeniden adlandir/renklendir   Z geri al   C hepsini temizle',
    'S kaydet   +/- yakinlastir   Ok tuslari kaydir   ALT basiliyken izgara kapali',
    'Baslangic konumunda: sol/sag ok yon, Enter onayla, ESC iptal',
    'H yardimi gizle/goster   ESC/Q cikis',
]


def _to_screen(x, y, origin_x, origin_y, px_per_m):
    return (int(WIDTH // 2 + (x - origin_x) * px_per_m),
            int(HEIGHT // 2 - (y - origin_y) * px_per_m))


def _to_world(px, py, origin_x, origin_y, px_per_m):
    return (origin_x + (px - WIDTH // 2) / px_per_m,
            origin_y - (py - HEIGHT // 2) / px_per_m)


def _snap(value, grid, enabled):
    return round(value / grid) * grid if enabled else value


class EditorState:
    def __init__(self, path):
        self.path = Path(path)
        if self.path.exists():
            plan = FloorPlan.load(str(self.path))
            self.rooms = list(plan.rooms.values())
            self.walls = list(plan.walls)
            self.doors = list(plan.doors)
            self.obstacles = list(plan.obstacles)
            self.start_pose = dict(plan.start_pose)
        else:
            self.rooms, self.walls, self.doors, self.obstacles = [], [], [], []
            self.start_pose = {'x': 0.0, 'y': 0.0, 'theta_deg': 0.0}
        self._room_n = len(self.rooms)
        self._furniture_n = len(self.obstacles)
        self._door_n = len(self.doors)
        self._undo_stack = []
        self.last_added = None  # (kind, object)
        self.selected = None
        self.catalog_index = 0
        self.furniture_yaw = 0

    def _snapshot(self):
        self._undo_stack.append(copy.deepcopy(
            (self.rooms, self.walls, self.doors, self.obstacles, self.start_pose)))
        del self._undo_stack[:-50]

    def undo(self):
        if not self._undo_stack:
            return False
        self.rooms, self.walls, self.doors, self.obstacles, self.start_pose = self._undo_stack.pop()
        self.selected = None
        self.last_added = None
        return True

    def clear(self):
        self._snapshot()
        self.rooms, self.walls, self.doors, self.obstacles = [], [], [], []

    def add_room(self, rect):
        self._snapshot()
        self._room_n += 1
        room = Room(id=f'room_{self._room_n}', label=f'Oda {self._room_n}',
                    color=ROOM_PALETTE[(self._room_n - 1) % len(ROOM_PALETTE)], rect=rect)
        self.rooms.append(room)
        xmin, ymin, xmax, ymax = rect
        for a, b in [((xmin, ymin), (xmax, ymin)), ((xmax, ymin), (xmax, ymax)),
                     ((xmax, ymax), (xmin, ymax)), ((xmin, ymax), (xmin, ymin))]:
            self.walls.append(Wall(a=a, b=b, room_id=room.id))
        self.last_added = ('room', room)

    def add_wall(self, a, b):
        self._snapshot()
        wall = Wall(a=a, b=b, room_id=None)
        self.walls.append(wall)
        self.last_added = ('wall', wall)

    def add_furniture(self, rect):
        self._snapshot()
        self._furniture_n += 1
        obstacle = Obstacle(id=f'furniture_{self._furniture_n}', label=f'Esya {self._furniture_n}',
                             color=FURNITURE_PALETTE[(self._furniture_n - 1) % len(FURNITURE_PALETTE)],
                             rect=rect)
        self.obstacles.append(obstacle)
        self.last_added = ('furniture', obstacle)

    def place_furniture(self, center):
        self._snapshot()
        kind, label, w, d = CATALOG[self.catalog_index]
        if self.furniture_yaw % 180:
            w, d = d, w
        x, y = center
        self._furniture_n += 1
        used = {o.id for o in self.obstacles}
        while f'{kind}_{self._furniture_n}' in used:
            self._furniture_n += 1
        item = Obstacle(f'{kind}_{self._furniture_n}', label, (115, 95, 75),
                        (x-w/2, y-d/2, x+w/2, y+d/2), kind, self.furniture_yaw)
        self.obstacles.append(item)
        self.selected = item
        self.last_added = ('furniture', item)

    def move_selected(self, center):
        if self.selected not in self.obstacles:
            return
        self._snapshot()
        x0,y0,x1,y1 = self.selected.rect
        w,d = x1-x0,y1-y0
        x,y = center
        self.selected.rect = (x-w/2,y-d/2,x+w/2,y+d/2)

    def rotate_furniture(self):
        self.furniture_yaw = (self.furniture_yaw + 90) % 360
        if self.selected not in self.obstacles:
            return
        self._snapshot()
        item = self.selected
        x0,y0,x1,y1 = item.rect
        x,y,w,d = (x0+x1)/2,(y0+y1)/2,x1-x0,y1-y0
        item.rect = (x-d/2,y-w/2,x+d/2,y+w/2)
        item.yaw_deg = (item.yaw_deg + 90) % 360

    def add_door(self, a, b):
        self._snapshot()
        connects = self._detect_rooms_for_door(a, b)
        self._door_n += 1
        door = Door(id=f'door_{self._door_n}', label=f'Kapi {self._door_n}', a=a, b=b, connects=connects)
        self.doors.append(door)
        self._cut_walls_for_door(a, b)
        self.last_added = ('door', door)

    def _detect_rooms_for_door(self, a, b):
        mx, my = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / length, dx / length
        lookup = FloorPlan(self.rooms, [], [])
        side_a = lookup.point_in_room(mx + nx * GRID_M, my + ny * GRID_M)
        side_b = lookup.point_in_room(mx - nx * GRID_M, my - ny * GRID_M)
        return (side_a, side_b)

    def _cut_walls_for_door(self, a, b):
        """Split any wall collinear with (a, b) so the door span becomes a gap."""
        vertical = math.isclose(a[0], b[0], abs_tol=1e-6)
        horizontal = math.isclose(a[1], b[1], abs_tol=1e-6)
        if not (vertical or horizontal):
            return  # only axis-aligned doors can auto-cut a wall
        d_lo, d_hi = sorted((a[1], b[1]) if vertical else (a[0], b[0]))
        fixed = a[0] if vertical else a[1]
        kept = []
        for wall in self.walls:
            wa, wb = wall.a, wall.b
            w_vertical = math.isclose(wa[0], wb[0], abs_tol=1e-6)
            same_line = (vertical == w_vertical) and math.isclose(
                wa[0] if vertical else wa[1], fixed, abs_tol=1e-6)
            if not same_line:
                kept.append(wall)
                continue
            w_lo, w_hi = sorted((wa[1], wb[1]) if vertical else (wa[0], wb[0]))
            lo, hi = max(w_lo, d_lo), min(w_hi, d_hi)
            if lo >= hi:
                kept.append(wall)  # no overlap with the door span
                continue
            if w_lo < lo:
                kept.append(Wall(a=(fixed, w_lo) if vertical else (w_lo, fixed),
                                  b=(fixed, lo) if vertical else (lo, fixed), room_id=wall.room_id))
            if hi < w_hi:
                kept.append(Wall(a=(fixed, hi) if vertical else (hi, fixed),
                                  b=(fixed, w_hi) if vertical else (w_hi, fixed), room_id=wall.room_id))
        self.walls = kept

    def rename_last(self):
        if self.last_added is None:
            print('Yeniden adlandirilacak bir sey yok.')
            return
        kind, obj = self.last_added
        label = input(f'Yeni etiket ({obj.label}): ').strip()
        if label:
            obj.label = label
        if kind in ('room', 'furniture'):
            color_raw = input('Yeni renk B,G,R (bos birak = degistirme): ').strip()
            if color_raw:
                try:
                    obj.color = tuple(int(c) for c in color_raw.split(','))
                except ValueError:
                    print('Gecersiz renk, degistirilmedi.')

    def save(self):
        data = {
            'start_pose': self.start_pose,
            'rooms': [{'id': r.id, 'label': r.label, 'color': list(r.color), 'rect': list(r.rect)}
                      for r in self.rooms],
            'walls': [{'a': list(w.a), 'b': list(w.b), 'room_id': w.room_id} for w in self.walls],
            'doors': [{'id': d.id, 'label': d.label, 'a': list(d.a), 'b': list(d.b),
                       'connects': list(d.connects)} for d in self.doors],
            'furniture': [{'id': o.id, 'label': o.label, 'color': list(o.color), 'rect': list(o.rect),
                           'kind': o.kind, 'yaw_deg': o.yaw_deg}
                          for o in self.obstacles],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
        print(f'Kaydedildi: {self.path}')


def _draw_grid(screen, origin, px_per_m):
    ox, oy = origin
    left, top = _to_world(0, 0, ox, oy, px_per_m)
    right, bottom = _to_world(WIDTH, HEIGHT, ox, oy, px_per_m)
    x = math.floor(left)
    while x <= right + 1:
        sx, _ = _to_screen(x, 0, ox, oy, px_per_m)
        pygame.draw.line(screen, GRID_COLOR, (sx, 0), (sx, HEIGHT), 1)
        x += 1.0
    y = math.floor(bottom)
    while y <= top + 1:
        _, sy = _to_screen(0, y, ox, oy, px_per_m)
        pygame.draw.line(screen, GRID_COLOR, (0, sy), (WIDTH, sy), 1)
        y += 1.0


def _draw_plan(screen, font, state, origin, px_per_m):
    ox, oy = origin
    for room in state.rooms:
        xmin, ymin, xmax, ymax = room.rect
        top_left = _to_screen(xmin, ymax, ox, oy, px_per_m)
        size = (int((xmax - xmin) * px_per_m), int((ymax - ymin) * px_per_m))
        pygame.draw.rect(screen, room.color, (*top_left, *size))
        screen.blit(font.render(room.label, True, (255, 255, 255)), (top_left[0] + 4, top_left[1] + 4))
    for wall in state.walls:
        pygame.draw.line(screen, (230, 230, 230), _to_screen(*wall.a, ox, oy, px_per_m),
                          _to_screen(*wall.b, ox, oy, px_per_m), 3)
    for obstacle in state.obstacles:
        xmin, ymin, xmax, ymax = obstacle.rect
        top_left = _to_screen(xmin, ymax, ox, oy, px_per_m)
        size = (int((xmax - xmin) * px_per_m), int((ymax - ymin) * px_per_m))
        pygame.draw.rect(screen, obstacle.color, (*top_left, *size))
        pygame.draw.rect(screen, (0, 0, 0), (*top_left, *size), 1)
        screen.blit(font.render(obstacle.label, True, (255, 255, 255)), (top_left[0] + 3, top_left[1] + 3))
        cx,cy = (xmin+xmax)/2, (ymin+ymax)/2
        rad = math.radians(obstacle.yaw_deg)
        pygame.draw.line(screen, (255,240,100), _to_screen(cx,cy,ox,oy,px_per_m),
                         _to_screen(cx-.25*math.sin(rad),cy-.25*math.cos(rad),ox,oy,px_per_m), 3)
    for door in state.doors:
        pygame.draw.line(screen, DOOR_COLOR, _to_screen(*door.a, ox, oy, px_per_m),
                          _to_screen(*door.b, ox, oy, px_per_m), 5)
    pose = state.start_pose
    pos_px = _to_screen(pose['x'], pose['y'], ox, oy, px_per_m)
    pygame.draw.circle(screen, START_POSE_COLOR, pos_px, 8)
    rad = math.radians(pose.get('theta_deg', 0.0))
    tip = (pos_px[0] + math.sin(rad) * 20, pos_px[1] - math.cos(rad) * 20)
    pygame.draw.line(screen, START_POSE_COLOR, pos_px, tip, 3)


def _draw_preview_rect(screen, a, b, origin, px_per_m, tool):
    xmin, xmax = sorted((a[0], b[0]))
    ymin, ymax = sorted((a[1], b[1]))
    top_left = _to_screen(xmin, ymax, origin[0], origin[1], px_per_m)
    size = (int((xmax - xmin) * px_per_m), int((ymax - ymin) * px_per_m))
    color = PREVIEW_COLOR if tool == 'room' else (255, 220, 120)
    pygame.draw.rect(screen, color, (*top_left, *size), 2)


def _draw_pose_preview(screen, pos, theta_deg, origin, px_per_m):
    pos_px = _to_screen(pos[0], pos[1], origin[0], origin[1], px_per_m)
    pygame.draw.circle(screen, START_POSE_COLOR, pos_px, 8, 2)
    rad = math.radians(theta_deg)
    tip = (pos_px[0] + math.sin(rad) * 24, pos_px[1] - math.cos(rad) * 24)
    pygame.draw.line(screen, START_POSE_COLOR, pos_px, tip, 3)


def _draw_hud(screen, font, big_font, tool, mouse_world, state, show_help):
    label = (f'Arac: {TOOL_NAMES.get(tool, tool)}   Konum: {mouse_world[0]:.2f}, {mouse_world[1]:.2f} m'
              f'   Dosya: {state.path.name}')
    screen.blit(big_font.render(label, True, TEXT_COLOR), (10, 8))
    if show_help:
        for i, line in enumerate(TOOL_HELP):
            screen.blit(font.render(line, True, TEXT_COLOR), (10, 34 + i * 18))


def main(args=None):
    argv = sys.argv[1:] if args is None else args
    if not argv:
        print('Kullanim: sim_plan_editor <plan.json>')
        sys.exit(1)
    state = EditorState(argv[0])

    pygame.init()
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    pygame.display.set_caption(f'Kufibot ev plani editoru - {state.path}')
    font = pygame.font.SysFont(None, 18)
    big_font = pygame.font.SysFont(None, 22)
    clock = pygame.time.Clock()

    tool = 'room'
    px_per_m = PX_PER_M_DEFAULT
    origin = [0.0, 0.0]
    show_help = True
    drag_start = None
    two_click_first = None
    pose_rotate_mode = False
    pose_start = (0.0, 0.0)
    pose_theta = 0.0

    running = True
    while running:
        mods = pygame.key.get_mods()
        snap_enabled = not (mods & pygame.KMOD_ALT)
        mouse_world_raw = _to_world(*pygame.mouse.get_pos(), origin[0], origin[1], px_per_m)
        mouse_world = (_snap(mouse_world_raw[0], GRID_M, snap_enabled),
                       _snap(mouse_world_raw[1], GRID_M, snap_enabled))

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if pose_rotate_mode:
                    if event.key == pygame.K_LEFT:
                        pose_theta = (pose_theta - 5.0) % 360.0
                    elif event.key == pygame.K_RIGHT:
                        pose_theta = (pose_theta + 5.0) % 360.0
                    elif event.key == pygame.K_RETURN:
                        state._snapshot()
                        state.start_pose = {'x': pose_start[0], 'y': pose_start[1], 'theta_deg': pose_theta}
                        pose_rotate_mode = False
                        print(f'Baslangic konumu ayarlandi: {state.start_pose}')
                    elif event.key == pygame.K_ESCAPE:
                        pose_rotate_mode = False
                    continue
                if event.key in (pygame.K_ESCAPE, pygame.K_q):
                    running = False
                elif event.key == pygame.K_1:
                    tool, two_click_first = 'room', None
                elif event.key == pygame.K_2:
                    tool, two_click_first = 'wall', None
                elif event.key == pygame.K_3:
                    tool, two_click_first = 'door', None
                elif event.key == pygame.K_4:
                    tool, two_click_first = 'furniture', None
                elif event.key == pygame.K_5:
                    tool, two_click_first = 'pose', None
                elif event.key == pygame.K_r and tool == 'furniture':
                    state.rotate_furniture()
                elif event.key == pygame.K_DELETE and tool == 'furniture':
                    if state.selected in state.obstacles:
                        state._snapshot()
                        state.obstacles.remove(state.selected)
                        state.selected = None
                elif event.key == pygame.K_s:
                    state.save()
                elif event.key == pygame.K_z:
                    print('Geri alindi.' if state.undo() else 'Geri alinacak bir sey yok.')
                elif event.key == pygame.K_n:
                    state.rename_last()
                elif event.key == pygame.K_c:
                    if input('Tum plani temizle? (e/h): ').strip().lower() == 'e':
                        state.clear()
                elif event.key == pygame.K_h:
                    show_help = not show_help
                elif event.key in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
                    px_per_m = min(200, px_per_m + 10)
                elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                    px_per_m = max(20, px_per_m - 10)
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if tool == 'furniture':
                    if event.pos[0] >= WIDTH-220 and 160 <= event.pos[1] < 160+len(CATALOG)*38:
                        state.catalog_index = (event.pos[1]-160)//38
                        state.selected = None
                    elif event.pos[0] >= WIDTH-220 and event.pos[1] >= 150:
                        continue
                    else:
                        hit = next((o for o in reversed(state.obstacles)
                                    if o.rect[0] <= mouse_world[0] <= o.rect[2]
                                    and o.rect[1] <= mouse_world[1] <= o.rect[3]), None)
                        if hit:
                            state.selected = hit
                        else:
                            state.place_furniture(mouse_world)
                        drag_start = mouse_world
                elif tool == 'room':
                    drag_start = mouse_world
                elif tool in ('wall', 'door'):
                    if two_click_first is None:
                        two_click_first = mouse_world
                    else:
                        (state.add_wall if tool == 'wall' else state.add_door)(two_click_first, mouse_world)
                        two_click_first = None
                elif tool == 'pose':
                    pose_start = mouse_world
                    pose_theta = state.start_pose.get('theta_deg', 0.0)
                    pose_rotate_mode = True
            elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                if tool == 'furniture' and drag_start is not None:
                    if math.dist(drag_start, mouse_world) > .01:
                        state.move_selected(mouse_world)
                    drag_start = None
                if tool == 'room' and drag_start is not None:
                    xmin, xmax = sorted((drag_start[0], mouse_world[0]))
                    ymin, ymax = sorted((drag_start[1], mouse_world[1]))
                    drag_start = None
                    if xmax - xmin >= GRID_M and ymax - ymin >= GRID_M:
                        rect = (xmin, ymin, xmax, ymax)
                        (state.add_room if tool == 'room' else state.add_furniture)(rect)

        if not pose_rotate_mode:
            keys = pygame.key.get_pressed()
            pan_speed = 0.2
            origin[0] += pan_speed * (keys[pygame.K_RIGHT] - keys[pygame.K_LEFT])
            origin[1] += pan_speed * (keys[pygame.K_UP] - keys[pygame.K_DOWN])

        screen.fill(BACKGROUND)
        _draw_grid(screen, origin, px_per_m)
        _draw_plan(screen, font, state, origin, px_per_m)
        if drag_start is not None and tool == 'room':
            _draw_preview_rect(screen, drag_start, mouse_world, origin, px_per_m, tool)
        if two_click_first is not None and tool in ('wall', 'door'):
            pygame.draw.line(screen, PREVIEW_COLOR, _to_screen(*two_click_first, *origin, px_per_m),
                              _to_screen(*mouse_world, *origin, px_per_m), 2)
        if pose_rotate_mode:
            _draw_pose_preview(screen, pose_start, pose_theta, origin, px_per_m)
        _draw_hud(screen, font, big_font, tool, mouse_world, state, show_help)
        if tool == 'furniture':
            pygame.draw.rect(screen, (25,30,40), (WIDTH-220,150,220,HEIGHT-150))
            for i, (_, label, w, d) in enumerate(CATALOG):
                rect = pygame.Rect(WIDTH-215, 160+i*38, 210, 34)
                pygame.draw.rect(screen, (65,90,140) if i == state.catalog_index else (45,50,60), rect)
                screen.blit(font.render(f'{label} ({w} x {d} m)', True, TEXT_COLOR), (rect.x+5, rect.y+8))
            for i, line in enumerate(['Sec > haritaya tikla', 'Esya: surukle ve tasi', 'R: 90 derece dondur', 'Delete: secileni sil', 'Z: geri al / S: kaydet']):
                screen.blit(font.render(line, True, TEXT_COLOR), (WIDTH-210, 445+i*22))
            if state.selected in state.obstacles:
                o = state.selected
                a = _to_screen(o.rect[0],o.rect[3],*origin,px_per_m)
                b = _to_screen(o.rect[2],o.rect[1],*origin,px_per_m)
                pygame.draw.rect(screen, (255,235,100), (a[0],a[1],b[0]-a[0],b[1]-a[1]), 3)
        pygame.display.flip()
        clock.tick(30)
    pygame.quit()


if __name__ == '__main__':
    main()
