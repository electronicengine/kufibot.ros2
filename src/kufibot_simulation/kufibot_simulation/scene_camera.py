"""Robot-eye camera using the viewer's shared metre-scale Panda3D scene."""
import math
import numpy as np
from panda3d.core import loadPrcFileData, AmbientLight, DirectionalLight
from direct.showbase.ShowBase import ShowBase
from .scene import build_home


class SceneCamera:
    def __init__(self, plan, width, height, fov):
        loadPrcFileData('', f'window-type offscreen\nwin-size {width} {height}\naudio-library-name null')
        self.base = ShowBase(windowType='offscreen')
        self.base.disableMouse()
        build_home(self.base.render, plan)
        self.base.setBackgroundColor(.055, .075, .10)
        ambient = AmbientLight('ambient')
        ambient.setColor((.48,.48,.52,1))
        self.base.render.setLight(self.base.render.attachNewNode(ambient))
        sun = DirectionalLight('sun')
        sun.setColor((.85,.82,.75,1))
        light = self.base.render.attachNewNode(sun)
        light.setHpr(-35,-55,0)
        self.base.render.setLight(light)
        self.base.camLens.setFov(fov, math.degrees(2*math.atan(math.tan(math.radians(fov/2))*height/width)))
        self.base.camLens.setNearFar(.01, 100)

    def render(self, x, y, z, bearing, pitch, direction=None, up=None):
        self.base.camera.setPos(x, y, z)
        if direction is None:
            self.base.camera.setHpr(-bearing, pitch, 0)
        else:
            from panda3d.core import Point3, Vec3
            self.base.camera.lookAt(Point3(x+direction[0], y+direction[1], z+direction[2]),
                                    Vec3(*up))
        self.base.graphicsEngine.renderFrame()
        shot = self.base.win.getScreenshot()
        return np.frombuffer(shot.getRamImageAs('BGR'), np.uint8).reshape(
            shot.getYSize(), shot.getXSize(), 3)[::-1].copy()

    def close(self):
        self.base.destroy()
