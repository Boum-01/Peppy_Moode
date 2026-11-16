# (Copyright 2026 PeppyMeter for Moode by Boum)
#
#
# Mise a jour de l'ecran pour moOde Audio.
#
# La pochette, les textes, le defilement et le fondu entre compteurs dessinent depuis des threads
# secondaires. Sous SDL2/X11, une mise a jour de la fenetre (pygame.display.update) demandee depuis
# un autre thread que celui de la boucle d'affichage de PeppyMeter n'arrive pas a l'ecran : on voit
# les aiguilles et le fond (mis a jour par la boucle) mais pas ce qui est dessine par les threads.
#
# Les threads dessinent donc sur la surface puis appellent request_update() ; la zone est mise en file
# et presentee par flush(), appelee a chaque image depuis le thread d'affichage (pm.dependent).
# Tant que flush() n'a jamais ete appelee, request_update() met a jour directement, comme avant.

import threading
import pygame as pg

_lock = threading.Lock()
_rects = {}              # (x, y, largeur, hauteur) -> zone a mettre a jour
_full = False            # True : fenetre entiere
_active = False          # True des que le thread d'affichage appelle flush()
_display_thread = None   # identifiant du thread d'affichage


def request_update(rect=None):
    """ Demande la mise a jour d'une zone de l'ecran (toute la fenetre si rect est None). """
    global _full
    if not _active or threading.get_ident() == _display_thread:
        # pas de thread d'affichage connu, ou on y est deja : mise a jour directe
        if rect is None:
            pg.display.update()
        else:
            pg.display.update(rect)
        return
    with _lock:
        if rect is None:
            _full = True
        else:
            _rects[(rect.x, rect.y, rect.width, rect.height)] = rect


def flush():
    """ A appeler a chaque image depuis le thread d'affichage : presente les zones demandees. """
    global _full, _active, _display_thread
    with _lock:
        if not _active:
            _active = True
            _display_thread = threading.get_ident()
            _full = True    # premiere image : tout presenter, y compris ce qui a ete dessine avant
        if not _full and not _rects:
            return
        full = _full
        rects = list(_rects.values())
        _full = False
        _rects.clear()
    if full:
        pg.display.update()
    else:
        pg.display.update(rects)
