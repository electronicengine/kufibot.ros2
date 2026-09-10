"""Human-operated tool console. Tool forms come from the registered schemas."""
import asyncio
import inspect
import json
import math

import cv2


class ManualControl:
    def __init__(self, demo, goal):
        self.demo, self.goal = demo, goal
        self.pending = set()
        self.error = ''
        self.automatic = False

    @property
    def busy(self):
        return self.automatic or bool(self.pending)

    def defaults(self, name):
        signature = inspect.signature(self.demo.session.handlers[name])
        values = {}
        for key in self.demo.session.metadata[name]['parameters']['properties']:
            default = signature.parameters[key].default
            values[key] = '' if default is inspect.Parameter.empty else str(default)
        return values

    def arguments(self, name, raw):
        schema = self.demo.session.metadata[name]['parameters']
        if set(raw) - set(schema['properties']):
            raise ValueError('Bu araca ait olmayan bir parametre var.')
        values = {}
        for key, spec in schema['properties'].items():
            value = str(raw.get(key, '')).strip()
            if not value:
                if key in schema['required']:
                    raise ValueError(f'{key}: bu alan zorunlu.')
                continue
            if spec['type'] == 'number':
                try:
                    value = float(value.replace(',', '.'))
                except ValueError:
                    raise ValueError(f'{key}: sayı gir (örnek: 0.5).') from None
                if not math.isfinite(value):
                    raise ValueError(f'{key}: sonlu bir sayı gir.')
                if value < spec.get('minimum', -math.inf) or value > spec.get('maximum', math.inf):
                    raise ValueError(f'{key}: {spec.get("minimum")} ile {spec.get("maximum")} arasında olmalı.')
            elif len(value) < spec.get('minLength', 0):
                raise ValueError(f'{key}: değer çok kısa.')
            values[key] = value
        return values

    def submit(self, name, raw):
        self.error = ''
        if self.busy:
            self.error = 'Çağrı sürüyor; sonucu bekle.'
            return False
        try:
            args = self.arguments(name, raw)
        except ValueError as error:
            self.error = str(error)
            return False
        task = asyncio.create_task(self.invoke(name, args))
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)
        return True

    async def invoke(self, name, args):
        self.demo.phase = f'{name} çalışıyor…'
        self.demo.event('call', tool=name, arguments=args)
        try:
            result = await self.demo.session.handlers[name](**args)
        except Exception as error:
            result = {'status': 'error', 'reason': str(error)}
        self.demo.event('result', tool=name, result=result)
        state = result.get('status', result.get('state', 'ok'))
        self.demo.phase = f'{name}: {state}'
        if result.get('reason'):
            self.demo.phase += ' • ' + result['reason']
        return result

    def reply(self, text):
        if not text.strip():
            self.error = 'Kaydetmeden önce yanıtını yaz.'
            return False
        self.demo.event('reply', role='assistant', text=text.strip())
        self.error = ''
        return True

    def new_session(self):
        if self.busy:
            self.error = 'Önce çalışan çağrıyı iptal et ve sonucunu bekle.'
            return
        self.demo.reset_session()
        self.error = ''

    async def close(self):
        tasks = list(self.pending)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class Field:
    def __init__(self, pg, rect, value='', multiline=False):
        self.rect = pg.Rect(rect)
        self.value = value
        self.cursor = len(value)
        self.focus = self.selected = False
        self.multiline = multiline

    def insert(self, text):
        if not self.multiline:
            text = text.replace('\n', ' ').replace('\r', '')
        if self.selected:
            self.value, self.cursor, self.selected = '', 0, False
        self.value = self.value[:self.cursor] + text + self.value[self.cursor:]
        self.cursor += len(text)

    def event(self, pg, event):
        if not self.focus:
            return
        if event.type == pg.TEXTINPUT:
            self.insert(event.text)
        elif event.type == pg.KEYDOWN:
            if event.mod & pg.KMOD_CTRL and event.key == pg.K_a:
                self.selected = True
            elif event.mod & pg.KMOD_CTRL and event.key == pg.K_v:
                try:
                    data = pg.scrap.get(pg.SCRAP_TEXT)
                    if data:
                        self.insert(data.decode('utf-8', errors='replace').rstrip('\x00'))
                except pg.error:
                    pass
            elif event.key in (pg.K_BACKSPACE, pg.K_DELETE):
                if self.selected:
                    self.value, self.cursor, self.selected = '', 0, False
                elif event.key == pg.K_BACKSPACE and self.cursor:
                    self.value = self.value[:self.cursor-1] + self.value[self.cursor:]
                    self.cursor -= 1
                elif event.key == pg.K_DELETE:
                    self.value = self.value[:self.cursor] + self.value[self.cursor+1:]
            elif event.key in (pg.K_LEFT, pg.K_RIGHT, pg.K_HOME, pg.K_END):
                self.selected = False
                self.cursor = {pg.K_LEFT: max(0, self.cursor-1), pg.K_RIGHT: min(len(self.value), self.cursor+1),
                               pg.K_HOME: 0, pg.K_END: len(self.value)}[event.key]
            elif event.key == pg.K_RETURN and self.multiline:
                self.insert('\n')

    def draw(self, window):
        pg, screen, font = window.pg, window.screen, window.font
        pg.draw.rect(screen, (27, 39, 53), self.rect, border_radius=4)
        pg.draw.rect(screen, (75, 161, 255) if self.focus else (72, 85, 99), self.rect, 1, border_radius=4)
        old_clip = screen.get_clip()
        screen.set_clip(self.rect.inflate(-12, -6))
        color = (125, 204, 255) if self.selected else (233, 237, 243)
        if self.multiline:
            lines = window.wrap(self.value, self.rect.width-16)
            visible = max(1, (self.rect.height-8)//23)
            for index, line in enumerate(lines[-visible:]):
                window.text(line, self.rect.x+8, self.rect.y+5+23*index, color)
        else:
            offset = max(0, font.size(self.value[:self.cursor])[0] - self.rect.width + 25)
            window.text(self.value, self.rect.x+8-offset, self.rect.y+7, color)
            if self.focus:
                x = self.rect.x+8-offset+font.size(self.value[:self.cursor])[0]
                pg.draw.line(screen, color, (x, self.rect.y+6), (x, self.rect.bottom-6))
        screen.set_clip(old_clip)


class Window:
    SIZE = (1440, 1000)

    def __init__(self, demo, plan, control):
        import pygame
        self.pg, self.demo, self.plan, self.control = pygame, demo, plan, control
        pygame.display.init()
        pygame.font.init()
        self.display = pygame.display.set_mode((1280, 900), pygame.RESIZABLE)
        self.screen = pygame.Surface(self.SIZE)
        pygame.display.set_caption('Kufibot • LLM rolü sende')
        self.font = pygame.font.SysFont('DejaVu Sans', 18)
        self.small = pygame.font.SysFont('DejaVu Sans Mono', 14)
        self.scale, self.offset = 1., (0, 0)
        self.buttons = {}
        self.menu_open = False
        self.error = ''
        self.selected = 'read_sensor_values'
        self.fields = {}
        self.reply_field = Field(pygame, (900, 821, 510, 76), multiline=True)
        self.select_tool(self.selected)
        self.inspected = None
        self.follow_results = True
        self.result_offset = self.history_offset = 0
        self.photo_index = None
        self.photo_cache = (None, None)
        self.photo_fullscreen = False
        self.delivered_rect = pygame.Rect(550, 373, 310, 199)
        self.history_rows = []
        self.history_rect = pygame.Rect(20, 669, 295, 279)
        self.result_rect = pygame.Rect(330, 669, 535, 279)
        pygame.key.start_text_input()
        try:
            pygame.scrap.init()
        except pygame.error:
            pass

    def select_tool(self, name):
        self.selected, self.menu_open = name, False
        defaults = self.control.defaults(name)
        self.fields = {key: Field(self.pg, (900, 284+i*61, 510, 36), value)
                       for i, (key, value) in enumerate(defaults.items())}

    def wrap(self, text, width, small=False):
        font = self.small if small else self.font
        lines = []
        for original in str(text).split('\n'):
            line = ''
            for char in original:
                if font.size(line + char)[0] > width:
                    lines.append(line)
                    line = ''
                line += char
            lines.append(line)
        return lines

    def text(self, text, x, y, color=(222, 232, 241), small=False):
        self.screen.blit((self.small if small else self.font).render(str(text), True, color), (x, y))

    def button(self, key, rect, label, enabled=True, danger=False):
        pg = self.pg
        rect = pg.Rect(rect)
        self.buttons[key] = (rect, enabled)
        pg.draw.rect(self.screen, (113, 51, 57) if danger else (37, 68, 88) if enabled else (35, 42, 49),
                     rect, border_radius=5)
        self.text(label, rect.x+10, rect.y+9, (232, 239, 244) if enabled else (111, 124, 135), small=True)

    def canvas_point(self, point):
        return ((point[0]-self.offset[0])/self.scale, (point[1]-self.offset[1])/self.scale)

    def display_point(self, point):
        return (round(point[0]*self.scale+self.offset[0]), round(point[1]*self.scale+self.offset[1]))

    def clicked(self, point):
        if self.photo_fullscreen:
            self.photo_fullscreen = False
            return
        if self.menu_open:
            for key, (rect, enabled) in self.buttons.items():
                if key.startswith('tool:') and rect.collidepoint(point):
                    self.select_tool(key[5:])
                    return
            self.menu_open = False
            return
        for field in [*self.fields.values(), self.reply_field]:
            field.focus = field.rect.collidepoint(point)
        for key, (rect, enabled) in self.buttons.items():
            if not enabled or not rect.collidepoint(point):
                continue
            if key == 'choose':
                self.menu_open = True
            elif key == 'call':
                if self.control.submit(self.selected, {k: v.value for k, v in self.fields.items()}):
                    self.follow_results = True
            elif key == 'session':
                self.control.new_session()
            elif key == 'reply':
                if self.control.reply(self.reply_field.value):
                    self.reply_field.value, self.reply_field.cursor = '', 0
                    self.follow_results = True
            elif key == 'latest':
                self.follow_results = True
                self.result_offset = self.history_offset = 0
            elif key == 'copy':
                try:
                    self.pg.scrap.put(self.pg.SCRAP_TEXT, self.result_text().encode('utf-8'))
                except self.pg.error:
                    self.control.error = 'Panoya erişilemedi; tam kayıt events.jsonl içinde.'
            elif key.startswith('photo'):
                current = self.photo_index or self.demo.image_count
                self.photo_index = (max(1, current-1) if key == 'photo-prev'
                                    else min(self.demo.image_count, current+1) if key == 'photo-next' else None)
            return
        if self.delivered_rect.collidepoint(point) and self.demo.observation is not None:
            self.photo_fullscreen = True
            return
        for rect, record in self.history_rows:
            if rect.collidepoint(point):
                self.inspected, self.follow_results, self.result_offset = record, False, 0

    def result_text(self):
        if not self.inspected:
            return 'Henüz çağrı yapılmadı.\nSağdan araç seç, değerleri gir ve Çağır düğmesine bas.'
        return json.dumps(self.inspected, ensure_ascii=False, indent=2)

    def events(self):
        pg = self.pg
        for event in pg.event.get():
            if event.type == pg.QUIT:
                return False
            if event.type == pg.KEYDOWN and event.key == pg.K_ESCAPE:
                if self.photo_fullscreen:
                    self.photo_fullscreen = False
                    continue
                return False
            if event.type == pg.MOUSEBUTTONDOWN and event.button == 1:
                self.clicked(self.canvas_point(event.pos))
            elif event.type == pg.MOUSEWHEEL:
                pos = self.canvas_point(pg.mouse.get_pos())
                if self.result_rect.collidepoint(pos):
                    self.result_offset = max(0, self.result_offset - event.y*3)
                elif self.history_rect.collidepoint(pos):
                    self.history_offset = max(0, self.history_offset - event.y*3)
            elif event.type == pg.KEYDOWN and event.key == pg.K_TAB:
                fields = [*self.fields.values(), self.reply_field]
                current = next((i for i, field in enumerate(fields) if field.focus), -1)
                for field in fields:
                    field.focus = False
                fields[(current + (-1 if event.mod & pg.KMOD_SHIFT else 1)) % len(fields)].focus = True
            else:
                for field in [*self.fields.values(), self.reply_field]:
                    field.event(pg, event)
        return True

    @staticmethod
    def point(x, y):
        return round(25 + (x+6)*48), round(435-y*48)

    def picture(self, frame, rect):
        if frame is None:
            self.text('Görüntü bekleniyor…', rect[0]+10, rect[1]+50, small=True)
            return
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        surface = self.pg.image.frombuffer(rgb.tobytes(), (rgb.shape[1], rgb.shape[0]), 'RGB')
        x, y, width, height = rect
        scale = min(width/rgb.shape[1], height/rgb.shape[0])
        size = (round(rgb.shape[1]*scale), round(rgb.shape[0]*scale))
        self.screen.blit(self.pg.transform.smoothscale(surface, size), (x+(width-size[0])//2, y))

    def visual(self):
        pg, d = self.pg, self.demo
        for room in self.plan.rooms.values():
            x1, y1, x2, y2 = room.rect
            a, b = self.point(x1, y2), self.point(x2, y1)
            pg.draw.rect(self.screen, (35, 48, 59), (*a, b[0]-a[0], b[1]-a[1]))
            self.text(room.label, a[0]+5, a[1]+5, small=True)
        for obstacle in self.plan.obstacles:
            x1, y1, x2, y2 = obstacle.rect
            a, b = self.point(x1, y2), self.point(x2, y1)
            pg.draw.rect(self.screen, (104, 81, 67), (*a, b[0]-a[0], b[1]-a[1]))
        for wall in self.plan.walls:
            pg.draw.line(self.screen, (186, 202, 214), self.point(*wall.a), self.point(*wall.b), 3)
        trail = list(d.trail)
        if len(trail) > 1:
            pg.draw.lines(self.screen, (77, 205, 159), False, [self.point(*p) for p in trail], 2)
        if d.world:
            pose = d.world['pose']
            x, y, heading = pose['x'], pose['y'], math.radians(pose['theta_deg'])
            pg.draw.circle(self.screen, (75, 161, 255), self.point(x, y), 10)
            pg.draw.line(self.screen, (255, 255, 255), self.point(x, y),
                         self.point(x+.4*math.sin(heading), y+.4*math.cos(heading)), 3)
            bearing, distance = math.radians(d.world['lidar_bearing_deg']), min(2., d.world['lidar_range_m'])
            pg.draw.line(self.screen, (255, 187, 77), self.point(x, y),
                         self.point(x+distance*math.sin(bearing), y+distance*math.cos(bearing)), 1)
            self.text(f'Konum: ({x:.2f}, {y:.2f}) m • Pusula: {pose["theta_deg"]:.1f}°', 20, 473)
            self.text(f'Lidar: {d.world["lidar_range_m"]:.2f} m • Kafa: {d.world["head_deg"]:.0f}°', 20, 504)
        self.text('Canlı kamera', 550, 104)
        self.picture(d.live, (550, 132, 310, 199))
        self.text('Teslim edilen fotoğraf', 550, 344)
        frame = d.observation
        if self.photo_index:
            path = d.output / f'observation-{self.photo_index:02d}.jpg'
            if path != self.photo_cache[0]:
                self.photo_cache = (path, cv2.imread(str(path)))
            frame = self.photo_cache[1]
        self.picture(frame, self.delivered_rect)
        if frame is not None:
            self.text('Büyütmek için fotoğrafa tıkla', 550, 556, (167, 185, 200), small=True)
        self.button('photo-prev', (550, 579, 44, 32), '<', d.image_count > 0)
        self.button('photo-next', (600, 579, 44, 32), '>', d.image_count > 0)
        self.button('photo-last', (650, 579, 68, 32), 'Son', d.image_count > 0)
        self.text(f'{self.photo_index or d.image_count}/{d.image_count}', 735, 587, small=True)
        state = d.tools.state
        self.text('Navigasyon: ' + state.get('state', 'bağlanıyor'), 20, 538)
        self.text('Gövde + mavi yön / turuncu lidar / yeşil yol', 20, 572, small=True)

    def console(self):
        pg, d = self.pg, self.demo
        records = list(d.events)
        if self.follow_results and records and self.inspected is not records[-1]:
            self.inspected, self.result_offset = records[-1], 0
        self.text('Çağrı geçmişi • seçerek incele', 20, 633)
        self.button('latest', (330, 627, 135, 32), 'Son kayda git')
        self.button('copy', (700, 627, 165, 32), 'JSON kopyala', bool(self.inspected))
        for rect in (self.history_rect, self.result_rect):
            pg.draw.rect(self.screen, (23, 32, 44), rect, border_radius=5)
        visible = 10
        self.history_offset = min(self.history_offset, max(0, len(records)-visible))
        end = len(records)-self.history_offset
        self.history_rows = []
        for index, record in enumerate(records[max(0, end-visible):end]):
            rect = pg.Rect(23, 672+index*27, 289, 26)
            self.history_rows.append((rect, record))
            if record is self.inspected:
                pg.draw.rect(self.screen, (46, 73, 89), rect)
            kind = {'call': '→', 'result': '←', 'image': 'Foto', 'reply': 'Yanıt'}.get(record['kind'], record['kind'])
            label = kind + ' ' + record.get('tool', record.get('file', record.get('text', '')))
            self.text(label[:32], rect.x+3, rect.y+5, small=True)
        lines = self.wrap(self.result_text(), self.result_rect.width-18, small=True)
        count = (self.result_rect.height-10)//19
        self.result_offset = min(self.result_offset, max(0, len(lines)-count))
        clip = self.screen.get_clip()
        self.screen.set_clip(self.result_rect)
        for index, line in enumerate(lines[self.result_offset:self.result_offset+count]):
            self.text(line, self.result_rect.x+9, self.result_rect.y+5+index*19, small=True)
        self.screen.set_clip(clip)
        self.text(f'JSON: {self.result_offset+1}–{min(len(lines), self.result_offset+count)}/{len(lines)} • Tekerlekle kaydır',
                  330, 954, small=True)

    def form(self):
        pg, c = self.pg, self.control
        pg.draw.rect(self.screen, (20, 29, 40), (885, 10, 540, 968), border_radius=8)
        self.text('OTOMATİK TEST' if c.automatic else 'LLM ROLÜ SENDE', 900, 25, (89, 222, 178))
        for i, line in enumerate(self.wrap('Hedef: ' + c.goal, 510)[:3]):
            self.text(line, 900, 57+i*22)
        self.button('choose', (900, 128, 510, 36), self.selected + '   ▾')
        description = self.demo.session.metadata[self.selected]['description']
        for i, line in enumerate(self.wrap(description, 510, small=True)[:4]):
            self.text(line, 900, 177+i*18, (167, 185, 200), small=True)
        schema = self.demo.session.metadata[self.selected]['parameters']
        for key, field in self.fields.items():
            spec = schema['properties'][key]
            label = key + (' *' if key in schema['required'] else '')
            if spec['type'] == 'number':
                label += f'  [{spec.get("minimum")} … {spec.get("maximum")}]'
                label += ' metre' if key == 'distance_m' else ' derece'
            self.text(label, field.rect.x, field.rect.y-22, small=True)
            field.draw(self)
        self.button('call', (900, 537, 510, 41), 'Çağır' if not c.busy else 'Çağrı çalışıyor…', not c.busy)
        for index, line in enumerate(self.wrap(c.error, 510, small=True)[:2]):
            self.text(line, 900, 586+index*19, (255, 156, 136), small=True)
        self.button('session', (900, 633, 510, 39), 'Yeni oturum / yetki aç', not c.busy)
        self.text('Yeni oturum mevcut iç görevi güvenle sonlandırır.', 900, 686, small=True)
        self.text('distance_m: ileri • angle_deg: + sağ / − sol', 900, 712, small=True)
        self.text('İstek kimliği ve iç görev kimliği otomatik yönetilir.', 900, 737, small=True)
        self.text('Senin yanıtın', 900, 789)
        self.reply_field.draw(self)
        self.button('reply', (900, 908, 510, 39), 'Yanıtı kaydet', not c.automatic)
        self.text('Yanıt kayda girer; hareket için ayrıca araç çağır.', 900, 957, small=True)
        if self.menu_open:
            for index, name in enumerate(self.demo.session.handlers):
                self.button('tool:'+name, (900, 166+index*37, 510, 36), name)

    def photo_overlay(self):
        if not self.photo_fullscreen:
            return
        frame = self.demo.observation
        if self.photo_index:
            path = self.demo.output / f'observation-{self.photo_index:02d}.jpg'
            if path != self.photo_cache[0]:
                self.photo_cache = (path, cv2.imread(str(path)))
            frame = self.photo_cache[1]
        if frame is None:
            self.photo_fullscreen = False
            return
        overlay = self.pg.Surface(self.SIZE, self.pg.SRCALPHA)
        overlay.fill((4, 8, 13, 238))
        self.screen.blit(overlay, (0, 0))
        self.text('Teslim edilen fotoğraf • büyük görünüm', 55, 34, (89, 222, 178))
        self.text('Kapatmak için fotoğrafa tıkla veya Esc', 55, 64, small=True)
        self.picture(frame, (45, 95, self.SIZE[0]-90, self.SIZE[1]-155))

    def draw(self):
        if not self.events():
            return False
        self.buttons = {}
        self.screen.fill((16, 23, 33))
        self.text('KUFIBOT • Araçları sen çağır, sonuçları incele, sonraki adıma karar ver', 20, 22)
        for i, line in enumerate(self.wrap(self.demo.phase, 840)[:2]):
            self.text(line, 20, 58+i*22, (89, 222, 178))
        self.visual()
        self.console()
        self.form()
        self.text('Tab: sonraki alan • Ctrl+A / Ctrl+V • Esc: kapat ve durdur', 20, 978, small=True)
        self.photo_overlay()
        width, height = self.display.get_size()
        self.scale = min(width/self.SIZE[0], height/self.SIZE[1])
        size = (round(self.SIZE[0]*self.scale), round(self.SIZE[1]*self.scale))
        self.offset = ((width-size[0])//2, (height-size[1])//2)
        self.display.fill((9, 14, 20))
        self.display.blit(self.pg.transform.smoothscale(self.screen, size), self.offset)
        self.pg.display.flip()
        return True
