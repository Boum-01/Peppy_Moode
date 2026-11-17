# Copyright 2024 PeppyMeter for Volumio by 2aCD
# (Copyright 2026 PeppyMeter for Moode by Boum)
# 
# This file is part of PeppyMeter for Volumio
#
# Adaptation pour moOde Audio : chemins, imports et modules moode_*.
#

import os, sys
import time
import threading
import traceback
import ctypes
import resource
import pygame as pg
from pygame.time import Clock

# for pygame2 with sdl2 fadeIn and position is available
use_sdl2 = False
if pg.version.ver.startswith("2"):
    try:
        import pyscreenshot
        from pygame._sdl2 import Window
        use_sdl2 = True
    except:
        pass

from pathlib import Path
from threading import Thread

# modules PeppyMeter / PeppySpectrum installes sur moOde + dossier de ce script
HERE = os.path.dirname(os.path.abspath(__file__))
for _p in ('/opt/peppymeter', '/opt/peppyspectrum', HERE):
    if _p not in sys.path:
        sys.path.append(_p)

try:
    from peppymeter import Peppymeter                  # /opt/peppymeter/peppymeter.py
except ImportError:
    from peppymeter.peppymeter import Peppymeter       # arborescence en paquet
from configfileparser import *

from moode_albumart import AlbumartAnimator
from moode_spectrum import SpectrumOutput
from moode_random import RandomControl
import moode_display
from moode_configfileparser import Volumio_ConfigFileParser, EXTENDED_CONF, METER_VISIBLE, SPECTRUM_VISIBLE, COLOR_DEPTH, POSITION_TYPE, POS_X, POS_Y, POSITION_APPLY, START_ANIMATION, METER_FADE

# cles du decalage du compteur dans la configuration de PeppyMeter (meter.x / meter.y de meters.txt)
METER_X_KEY = globals().get('METER_X', 'meter.x')
METER_Y_KEY = globals().get('METER_Y', 'meter.y')

# duree maximale du fondu entre deux compteurs (secondes) ; au-dela il est abrege
FADE_MAX_SECONDS = 3.0

PeppyRunning = '/tmp/peppyrunning'
CurDir = os.getcwd()
PeppyPath = '/opt/peppymeter'


def report_error(message):
    """ Affiche sur stderr l'exception en cours (erreurs inattendues seulement) """
    print('moode_peppymeter : ' + message, file=sys.stderr)
    traceback.print_exc()


class CallBack:
    """ Implements CallBack functions to start and stop albumart/spectrum animator """
    
    def __init__(self, util, meter_config_volumio, sdl2):
        """ Initializer

        :param util: peppymeter utility class
        :param meter_config_volumio: volumio meter configuration
        """

        self.util = util
        self.meter_config = self.util.meter_config
        self.meter_config_volumio = meter_config_volumio        
        self.first_run = True
        self.use_sdl2 = sdl2
        self._update_errors = 0     # erreurs deja affichees dans peppy_meter_update
        self.position_offset = (0, 0)   # decalage position.x / position.y injecte dans la configuration de PeppyMeter
        self._offset_warned = False
        # fondu entre compteurs : option meter.fade de config.txt (True par defaut)
        self.fade_enabled = bool(meter_config_volumio.get(METER_FADE, True))
        
    def vol_FadeIn_thread(self, arg):
        meter = arg
        for i in range(0, 100, 10): 
            meter.set_volume(i)
            time.sleep(0.07)
        meter.set_volume(100)

    def update_display(self):
        """ Demande la mise a jour de l'ecran. Appele depuis le thread de changement de compteur : la
        mise a jour est presentee par le thread d'affichage (voir moode_display). """
        moode_display.request_update(self.util.meter_config.get(SCREEN_RECT) or None)

    def img_FadeIn(self, img, a):
        img.set_alpha(a)
        self.util.screen_copy.blit(img, (0,0))
        self.update_display()

    def restore_screens(self, meter):
        """ Rend l'ecran affiche aux composants : fin de la redirection vers la surface temporaire.
        Tant qu'elle dure, le compteur dessine hors ecran : l'affichage parait fige. """
        meter.util.PYGAME_SCREEN = meter.util.screen_copy
        for comp in meter.components:
            comp.screen = meter.util.screen_copy

    def show_new_screen(self, meter):
        """ Affiche d'un coup le nouvel ecran (celui qu'a dessine le nouveau compteur) sur l'ecran
        sauvegarde. C'est la derniere etape d'un fondu ; sans fondu, elle est indispensable : sinon
        le nouveau fond reste sur la surface temporaire et l'ancien fond reste affiche. """
        try:
            meter.util.screen_copy.blit(meter.util.PYGAME_SCREEN, (0, 0))
            self.update_display()
        except Exception:
            report_error("affichage du nouvel ecran impossible")

    def safe_fade(self, meter, clock):
        """ Fondu enchaine de l'ancien vers le nouveau compteur, sans jamais bloquer la suite :
        erreur -> fondu desactive ; trop lent -> abrege. """
        t0 = time.time()
        try:
            screen_new = meter.util.PYGAME_SCREEN.copy()
            for i in range(0, 100, 2):
                self.img_FadeIn(screen_new, i)
                clock.tick(self.meter_config[FRAME_RATE])
                if time.time() - t0 > FADE_MAX_SECONDS:
                    break
            self.img_FadeIn(screen_new, 255)
        except Exception:
            report_error("fondu en erreur, desactive pour la suite")
            self.fade_enabled = False
            self.show_new_screen(meter)

    def check_position_offset(self, meter):
        """ Verifie que PeppyMeter a bien repris position.x / position.y dans meter_x / meter_y du compteur ;
        sinon le fond et le premier plan ne sont pas decales : message unique sur stderr. """
        if self._offset_warned or self.position_offset == (0, 0):
            return
        expected = self.position_offset
        section = self.meter_config.get(self.meter_config[METER])
        if isinstance(section, dict):
            expected = (section.get(METER_X_KEY), section.get(METER_Y_KEY))
        if self.meter_offset(meter) != expected:
            self._offset_warned = True
            print("moode_peppymeter : PeppyMeter n'a pas repris position.x / position.y dans meter_x / meter_y "
                  "(compteur : %s, attendu : %s) : fond et premier plan non decales"
                  % (self.meter_offset(meter), expected), file=sys.stderr)

    @staticmethod
    def meter_offset(meter):
        """ Decalage (meter_x, meter_y) que PeppyMeter applique aux composants du compteur ; None si indisponible. """
        try:
            return int(meter.meter_x), int(meter.meter_y)
        except (AttributeError, TypeError, ValueError):
            return None

    def peppy_meter_start(self, meter):
        # print('start')
        try:
            meter_section_volumio = self.meter_config_volumio.get(self.meter_config[METER])
            if meter_section_volumio is None:
                # section absente de meters.txt : pas d'options etendues
                meter_section_volumio = {EXTENDED_CONF: False}
            animation = self.meter_config_volumio[START_ANIMATION]
            self.check_position_offset(meter)

            # fadeIn new screen to old
            clock = Clock()
            if ((self.use_sdl2 and (animation or (not animation and not self.first_run))) or not self.first_run) \
                    and self.fade_enabled:
                self.safe_fade(meter, clock)
            elif meter.util.PYGAME_SCREEN is not meter.util.screen_copy:
                # sortie redirigee vers une surface temporaire (changement de compteur) mais pas de fondu
                self.show_new_screen(meter)
            self.first_run = False

            # restore default screen to pygame screen for all meter components
            self.restore_screens(meter)

            if meter_section_volumio[EXTENDED_CONF] == True:
                # stop meters when they are not visible
                if meter_section_volumio[METER_VISIBLE] == False:
                    meter.stop()

                # start spectrum thread if visible
                if meter_section_volumio[SPECTRUM_VISIBLE] == True:
                    self.spectrum_output = None
                    try:
                        self.spectrum_output = SpectrumOutput(self.util, self.meter_config_volumio, CurDir)
                        self.spectrum_output.start()
                    except Exception:
                        report_error("demarrage du spectre impossible")
                        self.spectrum_output = None

                # print (self.get_memory() / 1024)

            # start albumart animator
            self.album_animator = None
            try:
                # pochette et textes suivent le decalage du compteur (meter_x, meter_y), comme ses composants
                self.album_animator = AlbumartAnimator(self.util, self.meter_config_volumio,
                                                       meter_offset=self.meter_offset(meter))
                self.album_animator.start()
            except Exception:
                report_error("demarrage de la pochette impossible")
                self.album_animator = None

            # start volume fadeIn thread 
            meter.set_volume(0.0)
            if hasattr(self, 'FadeIn'):
                del self.FadeIn
            self.FadeIn = Thread(target = self.vol_FadeIn_thread, args=(meter, ))
            self.FadeIn.start()
        except Exception:
            try:
                self.restore_screens(meter)   # ne jamais laisser l'affichage redirige hors ecran
            except Exception:
                pass
            raise
        
    def peppy_meter_stop(self, meter):
        # print('stop')

        # save the current screen and reroute display output to a temporary surface        
        meter.util.screen_copy = meter.util.PYGAME_SCREEN
        meter.util.PYGAME_SCREEN = meter.util.PYGAME_SCREEN.copy()
            
        # stop spectrum, if running
        if hasattr(self, 'spectrum_output') and self.spectrum_output is not None:
            self.spectrum_output.stop_thread()
            if hasattr(self, 'spectrum_output'):
                del self.spectrum_output
        
        # stop albumart animator
        if hasattr(self, 'album_animator') and self.album_animator is not None:
            self.album_animator.stop_thread()
            if hasattr(self, 'album_animator'):
                del self.album_animator
                
        # delete fadeIn
        if hasattr(self, 'FadeIn'):
            del self.FadeIn
            
    def peppy_meter_update(self):
        # Appelee a chaque image par la boucle principale de PeppyMeter, dans le thread d'affichage :
        # une exception ici arreterait l'affichage.
        try:
            # presente ce que la pochette, les textes et le fondu ont dessine depuis d'autres threads
            moode_display.flush()
        except Exception:
            self._update_errors += 1
            if self._update_errors <= 3:
                report_error("mise a jour de l'ecran en erreur")

        # on lit l'attribut une seule fois (peppy_meter_stop le supprime depuis un autre thread)
        so = getattr(self, 'spectrum_output', None)
        if so is not None:
            try:
                so.update()
            except Exception:
                self._update_errors += 1
                if self._update_errors <= 3:
                    report_error("mise a jour du spectre en erreur")
    
    def get_memory(self):
        with open('/proc/meminfo', 'r') as mem:
            free_memory = 0
            for i in mem:
                sline = i.split()
                #if str(sline[0]) in ('MemFree:', 'Buffers:', 'Cached:'):
                if str(sline[0]) in ('MemAvailable:'):
                    free_memory += int(sline[1])
        return free_memory
    
    # cleanup memory called on stop
    def trim_memory(self) -> int:
        libc = ctypes.CDLL("libc.so.6")
        return libc.malloc_trim(0)

    # cleanup memory called on exit
    def exit_trim_memory(self):
            
        # cleanup    
        if os.path.exists(PeppyRunning):
            os.remove(PeppyRunning)
                
        if hasattr(self, 'album_animator'):
            del self.album_animator
        del self.util
        self.trim_memory()

# end class callback ----------------------------------------------

def meter_thread(arg, meter_config_volumio):
    """ thread methode to start peppymeter initial"""

    pm = arg # peppymeter object
    screen_w = pm.util.meter_config[SCREEN_INFO][WIDTH]
    screen_h = pm.util.meter_config[SCREEN_INFO][HEIGHT]
    clock = Clock()
    
    if use_sdl2:
        # grab the current x11 screen to use as source for fadin peppymeter
        screenshot_img = pyscreenshot.grab()
        screenshot = pg.image.fromstring(screenshot_img.tobytes(), screenshot_img.size, screenshot_img.mode)
        # define white surface
        white = pg.Surface((screen_w,screen_h))
        white.fill(pg.Color(255,255,255))
        #step for screen sizing
        step = int((screen_w * 0.04)+0.5)
                 
        screen_x, screen_y = window_position(meter_config_volumio, screenshot.get_width(), screenshot.get_height(),
                                             screen_w, screen_h)
        aspect = screen_h / screen_w
        
        # use own isplay init with option HIDDEN to prevent black blizzard
        screen = init_display(screen_w, screen_h, hide = True) 
        win = Window.from_display_module()
        
        if meter_config_volumio[START_ANIMATION]:
            # use screenshot as start image
            screen.blit(screenshot,(-screen_x, -screen_y))
            pg.display.update()

            # draw transparent white to screen
            white.set_alpha(70)
            screen.blit(white,(0, 0))

            # show hidden screen on position
            win.position = (screen_x, screen_y)
            Window.show(win)

            # update screen for resizing
            for i in range(screen_w - 5, 0, -step):             
                pg.display.update(0,0, screen_w-i, int(screen_h-i*aspect))
                clock.tick(50) 
            pg.display.update()

            # set the current screen as source for fadeIn
            pm.util.screen_copy = screen
            # redirect the follow meter output to temporary screen 
            pm.util.PYGAME_SCREEN = screen.copy()
        
        else:
            # show hidden screen on position
            win.position = (screen_x, screen_y)
            Window.show(win)
            
            pm.util.PYGAME_SCREEN = screen
            pm.util.screen_copy = screen
            
    else:
        # use own init_display without option HIDDEN
        pm.util.PYGAME_SCREEN = init_display(screen_w, screen_h)
        pm.util.screen_copy = pm.util.PYGAME_SCREEN
    
    # start peppymeter display output
    pm.meter.current_volume = 0.0
    pm.start_display_output()
    # wait for exit on main loop <------

    # after exit main loop
    if use_sdl2 and meter_config_volumio[START_ANIMATION]:
        pm.util.PYGAME_SCREEN = pm.util.screen_copy
        pg.event.set_blocked(None)
        win.position = (screen_x, screen_y)

        # fade to transparent white
        for i in range(0, 75, 10): 
            white.set_alpha(i)
            pm.util.PYGAME_SCREEN.blit(white, (0,0))
            pg.display.update()
            clock.tick(pm.util.meter_config[FRAME_RATE])

        # reduce the window size
        for i in range(0, screen_w - 5, step):
            win.size = (screen_w-i, int(screen_h-i*aspect))
            clock.tick(50)

    pm.exit()

    
def window_position(meter_config_volumio, desktop_w, desktop_h, screen_w, screen_h):
    """ Position de la fenetre sur le bureau (SDL2) : centree, ou aux coordonnees position.x / position.y avec
    position.apply = window. Avec position.apply = content (defaut), position.x / position.y decalent les elements
    dans la fenetre (voir apply_position_offset) : la fenetre reste en (0, 0), sinon le decalage serait double. """
    if meter_config_volumio[POSITION_TYPE] == "center":
        return (desktop_w - screen_w) / 2, (desktop_h - screen_h) / 2
    if meter_config_volumio.get(POSITION_APPLY, "content") == "window":
        return meter_config_volumio[POS_X], meter_config_volumio[POS_Y]
    return 0, 0


_position_applied = False


def apply_position_offset(meter_config, meter_config_volumio):
    """ position.x / position.y (config.txt, position.type = custom) decalent TOUS les elements : fond, premier plan,
    aiguilles, pochette et textes. On les ajoute au decalage meter.x / meter.y de chaque compteur dans la configuration
    de PeppyMeter, avant la creation des compteurs : PeppyMeter les applique alors a tous ses composants, comme il le
    fait pour meter_x et meter_y, et la pochette et les textes suivent (voir CallBack.meter_offset).
    Retourne le decalage applique. """
    global _position_applied
    if _position_applied or meter_config_volumio[POSITION_TYPE] == "center" \
            or meter_config_volumio.get(POSITION_APPLY, "content") != "content":
        return (0, 0)
    dx, dy = meter_config_volumio[POS_X], meter_config_volumio[POS_Y]
    if not (dx or dy):
        return (0, 0)
    _position_applied = True
    for name in meter_config.get(METER_NAMES) or []:
        section = meter_config.get(name)
        if isinstance(section, dict):
            section[METER_X_KEY] = int(section.get(METER_X_KEY) or 0) + dx
            section[METER_Y_KEY] = int(section.get(METER_Y_KEY) or 0) + dy
    return (dx, dy)


def protect_meter_run(vumeter):
    """ Vumeter.run() lit self.meter puis l'utilise. Si Vumeter.stop() (thread de changement de
    compteur) supprime self.meter entre les deux, une AttributeError sort dans la boucle principale :
    elle s'arrete et l'ecran reste fige. On ignore alors simplement cette image. """
    original = vumeter.run

    def run():
        try:
            return original()
        except AttributeError:
            if getattr(vumeter, 'meter', None) is None:
                return None
            raise
    vumeter.run = run


def init_display(screen_w, screen_h, hide = False):
    """
    alternative methode for display initialization
    for usage with SDL2 with option HIDDEN
    """

    depth = meter_config_volumio[COLOR_DEPTH]
    pm.util.meter_config[SCREEN_INFO][DEPTH] = depth

    os.environ["SDL_FBDEV"] = pm.util.meter_config[SDL_ENV][FRAMEBUFFER_DEVICE]

    if pm.util.meter_config[SDL_ENV][MOUSE_ENABLED]:
        os.environ["SDL_MOUSEDEV"] = pm.util.meter_config[SDL_ENV][MOUSE_DEVICE]
        os.environ["SDL_MOUSEDRV"] = pm.util.meter_config[SDL_ENV][MOUSE_DRIVER]
    else:
        os.environ["SDL_NOMOUSE"] = "1"
                
    if not pm.util.meter_config[SDL_ENV][VIDEO_DRIVER] == "dummy":
        os.environ["SDL_VIDEODRIVER"] = pm.util.meter_config[SDL_ENV][VIDEO_DRIVER]
        os.environ["DISPLAY"] = pm.util.meter_config[SDL_ENV][VIDEO_DISPLAY]
    pg.display.init()
    pg.mouse.set_visible(False)
    pg.font.init()

    if pm.util.meter_config[SDL_ENV][DOUBLE_BUFFER]:
        flags = pg.DOUBLEBUF | pg.HIDDEN | pg.NOFRAME if hide else pg.DOUBLEBUF | pg.NOFRAME
        screen = pg.display.set_mode((screen_w, screen_h), flags, depth)
    
    else:
        flags = pg.HIDDEN | pg.NOFRAME if hide else pg.NOFRAME
        screen = pg.display.set_mode((screen_w, screen_h), flags)
                                                         
    pm.util.meter_config[SCREEN_RECT] = pg.Rect(0, 0, screen_w, screen_h)
    return screen
        
def memory_limit():
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    free_memory = get_memory() * 1024
    resource.setrlimit(resource.RLIMIT_AS, (free_memory + 90000000, hard))
    # print(free_memory / 1024 /1024)


def reduce_memory_footprint():
    """ Reduit la memoire virtuelle des threads, qui compte dans RLIMIT_AS : 8 Mo de pile par defaut,
    et une zone malloc par thread. """
    threading.stack_size(2 * 1024 * 1024)
    try:
        ctypes.CDLL('libc.so.6').mallopt(-8, 2)   # M_ARENA_MAX = 2
    except Exception:
        pass


def get_memory():
    with open('/proc/meminfo', 'r') as mem:
        free_memory = 0
        for i in mem:
            sline = i.split()
            #if str(sline[0]) in ('MemFree:', 'Buffers:', 'Cached:'):
            if str(sline[0]) in ('MemAvailable:'):
                free_memory += int(sline[1])
    return free_memory

# cleanup memory on exit
def trim_memory() -> int:
    libc = ctypes.CDLL("libc.so.6")
    return libc.malloc_trim(0)
        
if __name__ == "__main__":
    """ Lance par moOde (service / script de demarrage) """

    reduce_memory_footprint()

    #enable threading!! 
    ctypes.CDLL('libX11.so.6').XInitThreads()
    
    # get the peppy meter object
    os.chdir(PeppyPath) # to find the config file
    pm = Peppymeter(standalone=True, timer_controlled_random_meter=False, quit_pygame_on_stop=False)
    
    # parse additional volumio configuration values  
    parser = Volumio_ConfigFileParser(pm.util)   
    meter_config_volumio = parser.meter_config_volumio
    
    # define the callback functions
    callback = CallBack(pm.util, meter_config_volumio, use_sdl2)
    pm.meter.callback_start = callback.peppy_meter_start
    pm.meter.callback_stop = callback.peppy_meter_stop
    pm.dependent = callback.peppy_meter_update
    pm.meter.malloc_trim = callback.trim_memory
    protect_meter_run(pm.meter)
    callback.position_offset = apply_position_offset(pm.util.meter_config, meter_config_volumio)
    pm.malloc_trim = callback.exit_trim_memory
            
    # start display output until break       
    memory_limit() # Limitates maximun memory usage
    try:
        Path(PeppyRunning).touch()
        Path(PeppyRunning).chmod(0o0777)
     
        # start random control in separate thread
        Random_Control = RandomControl(pm.util, meter_config_volumio, pm.meter)
        # Random_Control.callback_start = callback.peppy_meter_start
        Random_Control.start()
        
        # start meter output in separate thread
        meter_output = Thread(target = meter_thread, args=(pm, meter_config_volumio, ))
        meter_output.start()
                
        # stop if PeppyRunning deleted from external script
        while os.path.exists(PeppyRunning):
            time.sleep(1)
        
        Random_Control.stop_thread()
        pg.event.post(pg.event.Event(pg.MOUSEBUTTONUP))
        
    except MemoryError:
        print('ERROR: Memory Exception')
        callback.exit_trim_memory()
        del pm
        del callback
        trim_memory()            
        if os.path.exists(PeppyRunning):
            os.remove(PeppyRunning)
        os._exit(1)
