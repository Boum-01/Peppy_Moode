# Adaptation pour moOde audio, dérivée de volumio_albumart.py
# (Copyright 2021 PeppyMeter for Volumio by 2aCD)
# Copyright 2026 PeppyMeter for Moode by Boum-01
#
# Ce module remplace le thread AlbumartAnimator basé sur socket.io par un
# thread qui surveille le fichier de métadonnées de moOde (LCD.TXT, ou
# currentsong.txt), et alimente le même pipeline de rendu 
# (ImageTitleFactory / TextAnimator) que la version Volumio d'origine.
#
# Le "temps restant" (TIME_REMAINING_POS) n'est pas dans LCD.TXT (pas de
# seek/duration) : il est géré séparément par MpdTimePoller (voir
# moode_time_poller.py), qui interroge MPD directement et n'est démarré
# que si le skin utilise TIME_REMAINING_POS. Dépendance : python-mpd2.
#

import io
import os
import re
import time
import ctypes
import traceback
from threading import Thread, Timer, Lock, RLock
from urllib.parse import quote

import requests
import pygame as pg
from PIL import Image, ImageOps

try:
    import cairosvg  # uniquement utile avec pygame 1.9 (pas de SVG natif)
except Exception:
    cairosvg = None

from configfileparser import METER, BASE_PATH, SCREEN_INFO, METER_FOLDER, UI_REFRESH_PERIOD
from moode_configfileparser import *

from moode_metadata import MoodeMetadata, DEFAULT_MOODE_METADATA_FILE
from moode_display import request_update
from moode_time_poller import MpdTimePoller


SAMPLE_RATE_RE = re.compile(r'([\d.]+)\s*kHz', re.IGNORECASE)
BIT_DEPTH_RE = re.compile(r'(\d+)\s*bit', re.IGNORECASE)

# --- Réglages propres à moOde (à adapter si besoin) -----------------------------
DEFAULT_BASE_URL = "http://localhost"                   # serveur web de moOde (port 80)
DEFAULT_ALBUMART_PATH = "/images/default-album-cover.png"  # pochette si aucune n'est fournie
URL_SAFE_CHARS = "/:%?&=+,;@!$'()*~#[]"                 # '%' conservé : pas de double encodage
HTTP_TIMEOUT = (3, 10)                                  # (connexion, lecture) en secondes
DEFAULT_FONT_DIR = ''
LEGACY_VOLUMIO_ICON_DIR = '/opt/peppy_moode/fonts/format-icons'
DIGI_FONT_NAMES = ('DSEG7Classic-Light.ttf', 'DSEG7Classic-Italic.ttf')
DIGI_FONT_DIRS = ('/opt/peppy_moode/fonts',)        # en plus du dossier "fonts" du module


def pil_to_surface(img):
    """ Convertit une image PIL en surface pygame (compatible pygame 1.9 / 2.x) """
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")
    data = img.tobytes()
    if hasattr(pg.image, "frombytes"):
        return pg.image.frombytes(data, img.size, img.mode)
    return pg.image.fromstring(data, img.size, img.mode)


class AlbumartAnimator(Thread):
    """ Provides show albumart in a separate thread - version moOde """

    def __init__(self, util, meter_config_volumio, metadata_file=DEFAULT_MOODE_METADATA_FILE,
                 mpd_host="localhost", mpd_port=6600, base_url=DEFAULT_BASE_URL, meter_offset=None):
        """ Initializer

        :param util: utility class
        :param meter_config_volumio: config de mise en page du meter (VolumioConfig), réutilisée telle quelle
        :param metadata_file: chemin vers le fichier de métadonnées moOde (LCD.TXT / currentsong.txt)
        :param mpd_host: hôte MPD, utilisé uniquement si TIME_REMAINING_POS est configuré
        :param mpd_port: port MPD, utilisé uniquement si TIME_REMAINING_POS est configuré
        :param base_url: URL du serveur web de moOde, pour télécharger les pochettes
        :param meter_offset: (meter_x, meter_y) : décalage du compteur dans l'écran, lu sur le compteur actif
        """
        Thread.__init__(self)

        self.screen = util.PYGAME_SCREEN
        self.run_flag = True
        self.util = util
        self.meter_config = self.util.meter_config
        self.meter_config_volumio = meter_config_volumio
        self.meter_section = self.meter_config_volumio[self.meter_config[METER]]

        meter_bkp = self.meter_config_volumio.get(METER_BKP) or ""
        self.random_title = bool((meter_bkp == "random" or "," in meter_bkp)
                                 and self.meter_config_volumio.get(RANDOM_TITLE))

        self.metadata_file = metadata_file
        self.mpd_host = mpd_host
        self.mpd_port = mpd_port
        self.base_url = base_url
        self.meter_offset = meter_offset
        self.title_factory = None
        self.metadata_source = None
        self.time_poller = None

        self.albumart_mem = ''
        self.position_mem = ''
        self.first_run = True
        self.AA_Thread = None
        self.TI_Thread = None

    # ------------------------------------------------------------------
    def _create_title_factory(self):
        """ Crée le moteur de rendu (mode étendu uniquement). En cas d'échec,
        on continue sans infos de titre plutôt que de tuer le thread. """
        if self.meter_section[EXTENDED_CONF] != True:
            return None
        try:
            tf = ImageTitleFactory(self.util, self.meter_config_volumio, self.base_url, self.meter_offset)
            tf.load_fonts()      # charge les polices pour les infos titre
            tf.init_surfaces()   # copie les surfaces propres en backup
            return tf
        except Exception:
            traceback.print_exc()
            return None

    def run(self):
        """ Thread method. show all title infos and albumart. """

        self.albumart_mem = ''
        self.position_mem = ''
        self.first_run = True

        self.title_factory = self._create_title_factory()

        # thread séparé pour le compte à rebours, seulement si le skin en a besoin
        if self.title_factory is not None and self.meter_section[TIME_REMAINING_POS]:
            try:
                self.time_poller = MpdTimePoller(self.title_factory, host=self.mpd_host, port=self.mpd_port)
                self.time_poller.start()
            except Exception:
                traceback.print_exc()
                self.time_poller = None

        self.metadata_source = MoodeMetadata(filepath=self.metadata_file, listener=self._on_metadata_change)
        self.metadata_source.start()

        # attendre jusqu'à l'arrêt du thread
        while self.run_flag:
            time.sleep(0.1)

        # ----> on exit
        self.metadata_source.stop()
        if self.time_poller is not None:
            self.time_poller.stop_thread()

        if self.title_factory is not None:
            self.title_factory.stop_AA_FadeIn()          # interrompt un fondu en cours
            for t in (self.AA_Thread, self.TI_Thread):   # laisse finir les rendus lancés
                if t is not None and t.is_alive():
                    t.join(timeout=2)
            self.title_factory.stop_text_animator()

        time.sleep(0.1)

        # nettoyage mémoire
        self.AA_Thread = None
        self.TI_Thread = None
        self.title_factory = None
        self.screen = None
        self.util = None
        self.meter_config = None
        self.meter_config_volumio = None
        self.metadata_source = None
        self.time_poller = None
        self.trim_memory()
        # <---- exit <----

    def stop_thread(self):
        """ Stop thread """
        self.run_flag = False
        # Coupe tout de suite les dessins en cours (fondu de pochette, texte defilant) :
        # lors d'un changement de compteur, l'ecran va etre redessine et l'ancien
        # thread ne doit plus y ecrire pendant ce temps.
        tf = self.title_factory
        if tf is not None:
            try:
                tf.stop_AA_FadeIn()
                tf.stop_text_animator()
            except Exception:
                traceback.print_exc()

    # ------------------------------------------------------------------
    def _albumart_worker(self, tf, albumart_value):
        """ render albumart as thread """
        try:
            with tf.aa_lock:   # un seul rendu de pochette à la fois
                tf.get_albumart_data(albumart_value)
                tf.render_aa()
        except Exception:
            traceback.print_exc()

    def _titleinfo_worker(self, tf, play_info):
        """ render titleinfo as thread """
        try:
            with tf.text_lock:  # un seul rendu de texte à la fois
                tf.get_title_data(play_info)
                tf.render_text()
        except Exception:
            traceback.print_exc()

    def _on_metadata_change(self, meta):
        """ Appelé par MoodeMetadata à chaque changement détecté dans LCD.TXT """
        try:
            self._handle_metadata(meta)
        except Exception:
            # une exception ici ne doit pas tuer le thread de surveillance
            traceback.print_exc()

    def _handle_metadata(self, meta):
        if not self.run_flag or self.title_factory is None:
            return

        if meta.get("state", "stop") != "play":
            return

        if self.meter_section[EXTENDED_CONF] != True:
            return

        # pour le mode random/liste avec 'change on title', ne démarrer
        # qu'à l'initialisation (comme dans la version Volumio)
        if self.random_title and not self.first_run:
            return

        # pochette
        albumart_value = meta.get("albumart_url") or ""
        if albumart_value != self.albumart_mem:
            self.albumart_mem = albumart_value
            self.title_factory.init_aa()
            self.AA_Thread = Thread(target=self._albumart_worker,
                                    args=(self.title_factory, albumart_value), daemon=True)
            self.AA_Thread.start()

        # titre / artiste
        title_key = (meta.get("title") or "") + (meta.get("artist") or "")
        if title_key != self.position_mem:
            self.position_mem = title_key
            play_info = self._build_play_info(meta)
            self.TI_Thread = Thread(target=self._titleinfo_worker,
                                    args=(self.title_factory, play_info), daemon=True)
            self.TI_Thread.start()

        self.first_run = False

        # Le temps restant n'est pas géré ici : il n'est pas dans LCD.TXT.
        # Voir MpdTimePoller, démarré séparément dans run().

    def _build_play_info(self, meta):
        """ Convertit les champs LCD.TXT de moOde vers le format de dict
        attendu par ImageTitleFactory.get_title_data(), initialement écrit
        pour le payload pushState de Volumio.

        Champs attendus en sortie : title, artist, album, trackType,
        samplerate (optionnel), bitdepth (optionnel), bitrate (optionnel).
        """

        outrate = meta.get("outrate") or ""
        sample_match = SAMPLE_RATE_RE.search(outrate)
        depth_match = BIT_DEPTH_RE.search(outrate)

        samplerate = (sample_match.group(1) + " kHz") if sample_match else ""
        bitdepth = (depth_match.group(1) + " bit") if depth_match else ""

        track_type = ""
        file_field = (meta.get("file") or "").split("?", 1)[0]
        last_segment = file_field.rsplit("/", 1)[-1]
        if "." in last_segment:
            track_type = last_segment.rsplit(".", 1)[-1].lower()

        return {
            "title": meta.get("title") or "",
            "artist": meta.get("artist") or "",
            "album": meta.get("album") or "",
            "trackType": track_type,
            "samplerate": samplerate,
            "bitdepth": bitdepth,
            "bitrate": meta.get("bitrate") or "",
        }

    # nettoyage mémoire à la sortie
    def trim_memory(self) -> int:
        libc = ctypes.CDLL("libc.so.6")
        return libc.malloc_trim(0)


# ===================================================================================================================
class ImageTitleFactory():
    """ Dessine la pochette, les infos de titre et le temps restant """

    def __init__(self, util, meter_config_volumio, base_url=DEFAULT_BASE_URL, meter_offset=None):
        """ Initializer

        :param util: utility class
        :param meter_config_volumio: configuration étendue (pochette, textes, polices...)
        :param base_url: URL du serveur web de moOde
        :param meter_offset: (meter_x, meter_y) lu sur le compteur actif ; None : valeurs de meters.txt
        """

        self.run_flag = True
        self.screen = util.PYGAME_SCREEN
        self.util = util
        self.meter_config = self.util.meter_config
        self.meter_config_volumio = meter_config_volumio
        self.meter_section_meter = self.meter_config[self.meter_config[METER]]
        self.ui_refresh = self.meter_section_meter[UI_REFRESH_PERIOD]
        self.meter_section = self._relative_section(self.meter_config_volumio[self.meter_config[METER]], meter_offset)
        self.base_url = base_url.rstrip('/')
        self.titleMem = ""
        self.file_path = os.path.dirname(os.path.realpath(__file__))

        # pochette
        self.aa_rect = None
        self.AABackup = None
        self.aa_img = None

        # temps restant
        self.time_rect = None
        self.imgTimeBackup = None
        self.seek_new = 0
        self.NoTime = False
        self.remain = '00:00'
        self.timecolor = self.meter_section[TIMECOLOR]

        # verrous : draw_lock protège l'écran pygame (blit + update),
        # aa_lock / text_lock sérialisent les rendus de pochette / de texte.
        # Le thread de spectre (volumio_spectrum) ne les utilise pas.
        self.draw_lock = RLock()
        self.aa_lock = Lock()
        self.text_lock = Lock()

    @staticmethod
    def _relative_section(section, meter_offset=None):
        """ Les positions de la pochette, des textes et du temps restant sont relatives au compteur : on y ajoute
        son decalage dans l'ecran. Ce decalage est celui que PeppyMeter applique a ses composants (attributs
        meter_x et meter_y du compteur) ; a defaut, meter.x et meter.y de meters.txt. Sans decalage, rien ne
        change. overlay.relative = False dans meters.txt garde des positions absolues dans l'ecran. """
        if meter_offset is not None:
            dx, dy = meter_offset
        else:
            dx, dy = section.get(METER_X) or 0, section.get(METER_Y) or 0
        if not (dx or dy) or not section.get(OVERLAY_RELATIVE, True):
            return section
        shifted = dict(section)
        for key in (ALBUMART_POS, PLAY_TITLE_POS, PLAY_ARTIST_POS, PLAY_ALBUM_POS, PLAY_TYPE_POS,
                    PLAY_SAMPLE_POS, TIME_REMAINING_POS):
            pos = section.get(key)
            if pos:
                shifted[key] = (pos[0] + dx, pos[1] + dy)
        return shifted

    def init_surfaces(self):
        """ create clean surfaces as backup """
        #self.init_aa()
        self.init_text()
        self.init_time()

    # fonts
    # ----------------------------------
    @staticmethod
    def _find_font(font_dir, name):
        """ Retourne le chemin d'un fichier de police, ou None """
        if not name:
            return None
        candidate = os.path.join(font_dir, name.lstrip('/'))
        if os.path.isfile(candidate):
            return candidate
        if os.path.isabs(name) and os.path.isfile(name):
            return name
        return None

    @staticmethod
    def _load_font(path, size, fallback_size, bold=False):
        if path:
            try:
                return pg.font.Font(path, size)
            except Exception:
                pass
        return pg.font.SysFont(None, fallback_size, bold=bold)

    def load_fonts(self):
        """ load fonts for titleinfo """
        if not pg.font.get_init():
            pg.font.init()

        font_dir = self.meter_config_volumio.get(FONT_PATH)
        if not font_dir or not os.path.isdir(font_dir):
            font_dir = DEFAULT_FONT_DIR

        ms = self.meter_section
        cfg = self.meter_config_volumio

        self.fontL = self._load_font(self._find_font(font_dir, cfg.get(FONT_LIGHT)), ms[FONTSIZE_LIGHT], 50)
        self.fontR = self._load_font(self._find_font(font_dir, cfg.get(FONT_REGULAR)), ms[FONTSIZE_REGULAR], 50)
        self.fontB = self._load_font(self._find_font(font_dir, cfg.get(FONT_BOLD)), ms[FONTSIZE_BOLD], 70, bold=True)

        # digital font for remaining time
        digi_path = None
        for d in (os.path.join(self.file_path, 'fonts'),) + DIGI_FONT_DIRS:
            for name in DIGI_FONT_NAMES:
                p = os.path.join(d, name)
                if os.path.isfile(p):
                    digi_path = p
                    break
            if digi_path:
                break
        self.fontDigi = self._load_font(digi_path, ms[FONTSIZE_DIGI] or 40, 40)

        self.fontcolor = ms[FONTCOLOR]
        #green = (84, 198, 136)

    # get data functions
    # ----------------------------------
    def get_title_data(self, play_info):
        """ get title infos from argument """
        #print(play_info)
        if hasattr(self, 'playinfo_title'):
            self.titleMem = self.playinfo_title
        self.playinfo_title = play_info['title'] if play_info['title'] is not None else ''
        self.playinfo_artist = play_info['artist'] if play_info['artist'] is not None else ''
        self.playinfo_album = play_info['album'] if play_info['album'] is not None else ''
        self.playinfo_trackT = play_info['trackType'] if play_info['trackType'] is not None else ''
        self.playinfo_sample = play_info['samplerate'] if 'samplerate' in play_info and play_info['samplerate'] is not None else ''
        self.playinfo_depth = play_info['bitdepth'] if 'bitdepth' in play_info and play_info['bitdepth'] is not None else ''
        playinfo_rate = play_info['bitrate'] if 'bitrate' in play_info and play_info['bitrate'] is not None else ''

        self.playinfo_sample = str(self.playinfo_sample)  # some tidal samples are float
        if self.playinfo_sample == '':
            self.playinfo_sample = str(playinfo_rate)  # for webradio
        if not self.meter_section[PLAY_ALBUM_POS] and self.playinfo_album != '':
            self.playinfo_artist = self.playinfo_artist + " - " + self.playinfo_album
        if self.playinfo_trackT in ('dsf', 'dff'):
            self.playinfo_trackT = 'dsd'

    def has_albumart(self):
        """ La pochette n'est affichable que si position ET dimension sont configurées """
        return bool(self.meter_section[ALBUMART_POS] and self.meter_section[ALBUMART_DIM])

    def _albumart_url(self, albumart_value):
        """ Construit l'URL complète de la pochette à partir de la valeur moOde """
        value = (albumart_value or "").strip()
        if not value:
            value = DEFAULT_ALBUMART_PATH
        if value.startswith(("http://", "https://")):
            return value
        if not value.startswith("/"):
            value = "/" + value
        return self.base_url + quote(value, safe=URL_SAFE_CHARS)

    def get_albumart_data(self, albumart_value):
        """ télécharge et prépare la pochette (self.aa_img reste None en cas d'échec) """

        self.aa_img = None
        if not self.has_albumart():
            return

        url = self._albumart_url(albumart_value)
        try:
            resp = requests.get(url, timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            data = resp.content
        except Exception as e:
            pass
            return

        dim = self.meter_section[ALBUMART_DIM]
        try:
            # if png mask available use PIL for image
            if self.meter_section[ALBUMART_MSK]:
                img = Image.open(io.BytesIO(data)).convert("RGBA")
                img = img.resize(dim)
            # otherwise surface
            else:
                try:
                    img = pg.image.load(io.BytesIO(data))
                except pg.error:
                    # format non géré par SDL_image (ex. webp) : repli sur PIL
                    img = pil_to_surface(Image.open(io.BytesIO(data)).convert("RGBA"))
                try:
                    img = pg.transform.smoothscale(img, dim)
                except ValueError:
                    img = pg.transform.scale(img, dim)
        except Exception as e:
            pass
            return

        self.aa_img = img

    def get_time_data(self, time_args, timer_init):
        """ get time data """

        self.NoTime = False
        seek_current = int(float(time_args[1]) / 1000)
        # set initial to current and then count automatcally
        self.seek_new = seek_current if timer_init else self.seek_new + 1

        # webradio has no time info
        if time_args[2] == 'webradio':
            self.remain = time_args[0]
            if time_args[0] == 0:
                self.NoTime = True
        else:
            self.remain = 0 if time_args[0] - self.seek_new <= 0 else time_args[0] - self.seek_new
        self.timecolor = self.meter_section[TIMECOLOR] if self.remain > 10 else (242, 0, 0)  # red for last 10 seconds
        self.remain = '{:02d}:{:02d}'.format(self.remain // 60, self.remain % 60)

    # render data functions
    # ----------------------------------

    def init_aa(self):
        """ create clean album art surface as backup """

        if not self.has_albumart():
            self.aa_rect = None
            self.AABackup = None
            return
        try:
            pos = self.meter_section[ALBUMART_POS]
            dim = self.meter_section[ALBUMART_DIM]
            self.aa_rect = pg.Rect(pos[0], pos[1], dim[0], dim[1])
            self.AABackup = self.screen.subsurface(self.aa_rect).copy()
        except ValueError:
            pass
            self.aa_rect = None
            self.AABackup = None

    def _fade_step(self, img, alpha):
        img.set_alpha(alpha)
        with self.draw_lock:
            if not self.run_flag:
                return
            self.screen.blit(img, self.aa_rect)
            request_update(self.aa_rect)

    def render_aa(self):
        """ render albumart """

        if self.aa_rect is None or self.AABackup is None or self.aa_img is None:
            return

        img = self.aa_img

        # mask image if png mask set
        if self.meter_section[ALBUMART_MSK]:
            try:
                path = os.path.join(self.meter_config[BASE_PATH], self.meter_config[SCREEN_INFO][METER_FOLDER],
                                    self.meter_section[ALBUMART_MSK])
                mask_image = Image.open(path).convert('L')
                # scale mask if needed
                if mask_image.size != img.size:
                    mask_image = mask_image.resize(img.size)
                # image composite (on travaille sur une copie : self.aa_img reste intact)
                img = img.copy()
                img.putalpha(ImageOps.invert(mask_image))
                # put albumart image to surface
                img = pil_to_surface(img)
            except Exception as e:
                pass
                return

        # copy clean surface from backup
        with self.draw_lock:
            self.screen.blit(self.AABackup, self.aa_rect)

        # fadeIn album art
        # alpha = 20
        alpha = 5
        while alpha < 120 and self.run_flag:
            self._fade_step(img, alpha)
            time.sleep(self.ui_refresh)
            alpha += 4

        if self.run_flag:
            self._fade_step(img, 255)

        # draw border
        if self.meter_section[ALBUMBORDER] and self.run_flag:
            with self.draw_lock:
                pg.draw.rect(self.screen, self.fontcolor, self.aa_rect, self.meter_section[ALBUMBORDER])
                request_update(self.aa_rect)

    def stop_AA_FadeIn(self):
        self.run_flag = False

    def init_time(self):
        """ create clean remaining time surface as backup """

        if self.meter_section[TIME_REMAINING_POS]:
            txt_size = self.fontDigi.size('00:00')
            self.time_rect = pg.Rect(self.meter_section[TIME_REMAINING_POS], txt_size)
            self.imgTimeBackup = self.screen.subsurface(self.time_rect).copy()

    def render_time(self):
        """ render remaining time (appelée par MpdTimePoller) """

        if self.time_rect is None or self.imgTimeBackup is None:
            return
        with self.draw_lock:
            # copy clean surface from backup
            self.screen.blit(self.imgTimeBackup, self.time_rect)
            if not self.NoTime:
                imgTime = self.fontDigi.render(self.remain, True, self.timecolor)
                self.screen.blit(imgTime, self.time_rect)
            request_update(self.time_rect)

    def init_text(self):
        """ create clean text surfaces as backup """

        def set_txt_max(txtmax):
            if (txtmax):
                return txtmax
            else:
                return self.meter_section[PLAY_MAX]

        def size_txt(rendertxt, fontstyle):
            if fontstyle == FONT_STYLE_L:
                ret = self.fontL.size(rendertxt)
            elif fontstyle == FONT_STYLE_R:
                ret = self.fontR.size(rendertxt)
            else:
                ret = self.fontB.size(rendertxt)
            return ret

        # track type
        if self.meter_section[PLAY_TYPE_POS] and self.meter_section[PLAY_TYPE_DIM]:
            self.type_rect = pg.Rect(self.meter_section[PLAY_TYPE_POS], self.meter_section[PLAY_TYPE_DIM])
            self.imgFormatBackup = None
            self.imgFormatBackup = self.screen.subsurface(self.type_rect).copy()
        # title info
        if self.meter_section[PLAY_TITLE_POS] and (self.meter_section[PLAY_MAX] or self.meter_section[PLAY_TITLE_MAX]):
            txt_size = size_txt('A', self.meter_section[PLAY_TITLE_STYLE])  # for height
            self.title_rect = pg.Rect(self.meter_section[PLAY_TITLE_POS], (set_txt_max(self.meter_section[PLAY_TITLE_MAX]), txt_size[1]))
            self.imgTitleBackup = None
            self.imgTitleBackup = self.screen.subsurface(self.title_rect).copy()
        # artist info
        if self.meter_section[PLAY_ARTIST_POS] and (self.meter_section[PLAY_MAX] or self.meter_section[PLAY_ARTIST_MAX]):
            txt_size = size_txt('A', self.meter_section[PLAY_ARTIST_STYLE])  # for height
            self.artist_rect = pg.Rect(self.meter_section[PLAY_ARTIST_POS], (set_txt_max(self.meter_section[PLAY_ARTIST_MAX]), txt_size[1]))
            self.imgArtistBackup = None
            self.imgArtistBackup = self.screen.subsurface(self.artist_rect).copy()
        # album info
        if self.meter_section[PLAY_ALBUM_POS] and (self.meter_section[PLAY_MAX] or self.meter_section[PLAY_ALBUM_MAX]):
            txt_size = size_txt('A', self.meter_section[PLAY_ALBUM_STYLE])  # for height
            self.album_rect = pg.Rect(self.meter_section[PLAY_ALBUM_POS], (set_txt_max(self.meter_section[PLAY_ALBUM_MAX]), txt_size[1]))
            self.imgAlbumBackup = None
            self.imgAlbumBackup = self.screen.subsurface(self.album_rect).copy()
        # frame rate info
        if self.meter_section[PLAY_SAMPLE_POS] and (self.meter_section[PLAY_MAX] or self.meter_section[PLAY_SAMPLE_MAX]):
            txt_size = size_txt('-44.1 kHz 24 bit-', self.meter_section[PLAY_SAMPLE_STYLE])  # max widht to create rectangle for clear area
            if self.meter_section[PLAY_SAMPLE_MAX]:
                self.sample_rect = pg.Rect(self.meter_section[PLAY_SAMPLE_POS], (set_txt_max(self.meter_section[PLAY_SAMPLE_MAX]), txt_size[1]))
            else:
                sample_pos_x = self.meter_section[PLAY_SAMPLE_POS][0]
                # center sample position
                if self.meter_section[PLAY_CENTER] == True:
                    sample_pos_x += int((self.meter_section[PLAY_MAX] - txt_size[0]) / 2)
                self.sample_rect = pg.Rect((sample_pos_x, self.meter_section[PLAY_SAMPLE_POS][1]), txt_size)

            self.imgSampleBackup = None
            self.imgSampleBackup = self.screen.subsurface(self.sample_rect).copy()

    # --- helpers de rendu du texte ---
    def _txt_enabled(self, pos_key, max_key):
        ms = self.meter_section
        return bool(ms[pos_key] and (ms[PLAY_MAX] or ms[max_key]))

    def _render_txt(self, rendertxt, fontstyle, color):
        if fontstyle == FONT_STYLE_L:
            font = self.fontL
        elif fontstyle == FONT_STYLE_R:
            font = self.fontR
        else:
            font = self.fontB
        return font.render(rendertxt, True, color)

    def _set_txt_color(self, txtcolor):
        return txtcolor if txtcolor else self.fontcolor

    def _update_txt(self, imgtxt, rect):
        center = self.meter_section[PLAY_TXT_CENTER]
        if center is None:
            center = self.meter_section[PLAY_CENTER]
        if center == True:  # center title position
            self.screen.blit(imgtxt, (rect.centerx - int(imgtxt.get_width() / 2), rect.y))
        else:
            self.screen.blit(imgtxt, rect)
        request_update(rect)

    def _draw_text_block(self, backup, rect, img_long, animator_attr):
        """ efface la zone, puis affiche le texte ou démarre un ticker s'il est trop long """
        self.screen.blit(backup, rect)  # copy clean surface from backup
        if img_long.get_width() - 5 <= rect.width:
            self._update_txt(img_long, rect)
        else:  # start ticker daemon
            setattr(self, animator_attr, self.start_text_animator(backup, img_long, rect))

    @staticmethod
    def _set_color(img, color):
        """ Recolore une surface en conservant son canal alpha (sans boucle pixel par pixel).
        La surface doit avoir un canal alpha (convert_alpha). """
        img.fill((0, 0, 0, 255), special_flags=pg.BLEND_RGBA_MULT)
        img.fill((color[0], color[1], color[2], 0), special_flags=pg.BLEND_RGBA_ADD)

    def _format_icon_path(self, track_type):
        """ cherche l'icône SVG du format : dossier local d'abord, puis ancien dossier Volumio """
        if not track_type:
            return None
        name = track_type + '.svg'
        for d in (os.path.join(self.file_path, 'format-icons'), LEGACY_VOLUMIO_ICON_DIR):
            p = os.path.join(d, name)
            if os.path.isfile(p):
                return p
        return None

    def _load_format_icon(self, icon_path):
        """ charge l'icône, mise à l'échelle et recolorée ; None en cas d'échec """
        rect = self.type_rect
        try:
            if pg.version.ver.startswith("2"):
                # for pygame > 2.0 use internal svg functions
                img = pg.image.load(icon_path)
                h = img.get_height()
                w = img.get_width()
                # scale aspect ratio to dimension
                sc = rect.height / h if h >= w else rect.width / w
                img = pg.transform.smoothscale_by(img, sc)
            else:
                # pygame 1.9 have no svg, use cairosvg
                png = cairosvg.svg2png(url=icon_path, output_width=rect.width, output_height=rect.height)
                img = pil_to_surface(Image.open(io.BytesIO(png)))
            img = img.convert_alpha()
            c = self.meter_section[PLAY_TYPE_COLOR]
            self._set_color(img, c)
            return img
        except Exception as e:
            pass
            return None

    def _draw_type_text(self):
        """ repli : affiche le type de fichier en texte. Retourne le rect dessiné ou None """
        ms = self.meter_section
        if not ms[PLAY_SAMPLE_POS]:
            return None
        if ms[PLAY_CENTER] == True:
            type_y = ms[PLAY_TYPE_POS][1]
            type_str = self.playinfo_trackT
        else:
            type_y = ms[PLAY_SAMPLE_POS][1]
            type_str = self.playinfo_trackT[:4]
        img = self._render_txt(type_str, ms[PLAY_SAMPLE_STYLE], ms[PLAY_TYPE_COLOR])
        rect = pg.Rect((ms[PLAY_TYPE_POS][0], type_y), img.get_size())
        self.screen.blit(img, rect)
        return rect

    def _draw_format(self, icon_path):
        """ dessine l'icône (ou le texte) du format de fichier """
        ms = self.meter_section
        if not (ms[PLAY_TYPE_POS] and ms[PLAY_TYPE_DIM]):
            return

        # copy clean surface from backup
        self.screen.blit(self.imgFormatBackup, self.type_rect)
        update_rect = self.type_rect

        # sans icône (webradio...) : la zone reste simplement effacée
        if icon_path:
            img = self._load_format_icon(icon_path)
            if img is not None:
                if img.get_height() >= img.get_width():
                    pos = ms[PLAY_TYPE_POS]
                else:  # center vertically
                    pos = (ms[PLAY_TYPE_POS][0], int(ms[PLAY_TYPE_POS][1] + ms[PLAY_TYPE_DIM][1] / 2 - img.get_height() / 2))
                self.screen.blit(img, pos)
            else:
                # icône non chargeable : texte à la place
                text_rect = self._draw_type_text()
                if text_rect is not None:
                    update_rect = self.type_rect.union(text_rect)

        # update tracktype rectangle
        request_update(update_rect)

    def render_text(self):
        """ render text objects """

        ms = self.meter_section
        icon_path = self._format_icon_path(self.playinfo_trackT)

        # title, artist, album
        imgTitle_long = self._render_txt(self.playinfo_title, ms[PLAY_TITLE_STYLE], self._set_txt_color(ms[PLAY_TITLE_COLOR]))
        imgArtist_long = self._render_txt(self.playinfo_artist, ms[PLAY_ARTIST_STYLE], self._set_txt_color(ms[PLAY_ARTIST_COLOR]))
        imgAlbum_long = self._render_txt(self.playinfo_album, ms[PLAY_ALBUM_STYLE], self._set_txt_color(ms[PLAY_ALBUM_COLOR]))
        # samplerate + bitdepth
        imgSample_long = self._render_txt((self.playinfo_sample + " " + self.playinfo_depth).rstrip(), ms[PLAY_SAMPLE_STYLE], ms[PLAY_TYPE_COLOR])

        if self.titleMem == self.playinfo_title:  # only if title changed
            return

        # stop all ticker if title info changed (hors verrou : on attend la fin des tickers)
        self.stop_text_animator()

        if not self.run_flag:
            return

        with self.draw_lock:
            if not self.run_flag:  # arret demande entre-temps
                return

            # trackType
            self._draw_format(icon_path)

            # title info
            if self._txt_enabled(PLAY_TITLE_POS, PLAY_TITLE_MAX):
                self._draw_text_block(self.imgTitleBackup, self.title_rect, imgTitle_long, 'text_animator_title')

            # artist info
            if self._txt_enabled(PLAY_ARTIST_POS, PLAY_ARTIST_MAX):
                self._draw_text_block(self.imgArtistBackup, self.artist_rect, imgArtist_long, 'text_animator_artist')

            # album info
            if self._txt_enabled(PLAY_ALBUM_POS, PLAY_ALBUM_MAX):
                self._draw_text_block(self.imgAlbumBackup, self.album_rect, imgAlbum_long, 'text_animator_album')

            # frame rate info
            if self._txt_enabled(PLAY_SAMPLE_POS, PLAY_SAMPLE_MAX):
                # copy clean surface from backup
                self.screen.blit(self.imgSampleBackup, self.sample_rect)

                sample_pos_x = self.sample_rect.x
                if ms[PLAY_CENTER] == True and ms[PLAY_SAMPLE_MAX]:
                    # center sample position
                    sample_pos_x = self.sample_rect.centerx - int(imgSample_long.get_width() / 2)

                # sample rate
                self.screen.blit(imgSample_long, (sample_pos_x, self.sample_rect.y))
                # update sample rectangle
                request_update(self.sample_rect)

    # text animator functions
    # ----------------------------------
    def start_text_animator(self, imgBackup, imgTxt, imgRect):
        """ start daemon for text animation"""

        a = TextAnimator(self.util, self.ui_refresh, imgBackup, imgTxt, imgRect, self.draw_lock)
        a.start()
        return a

    def stop_text_animator(self):
        """ stop daemons for text animation """

        for attr in ('text_animator_title', 'text_animator_artist', 'text_animator_album'):
            anim = getattr(self, attr, None)
            if anim is not None:
                anim.stop_thread()
                anim.join(timeout=2)  # évite qu'un ticker repasse par-dessus le nouveau texte
                setattr(self, attr, None)

        self.trim_memory()

    # cleanup memory on exit
    def trim_memory(self) -> int:
        libc = ctypes.CDLL("libc.so.6")
        return libc.malloc_trim(0)


# ===================================================================================================================
class TextAnimator(Thread):
    """ Provides show ticker in a separate thread """

    def __init__(self, util, ui_refresh, imgBackup, imgTxt, imgRect, lock=None):
        """ Initializer

        :param util: utility class
        :param ui_refresh_period
        :param imgBackup: backup surface for clean
        :param imTxt: txt surface
        :param imgRect: rectangle for update
        :param lock: verrou de dessin partagé avec ImageTitleFactory
        """
        Thread.__init__(self, daemon=True)

        self.util = util
        self.ui_refresh = ui_refresh
        self.screen = self.util.PYGAME_SCREEN
        self.backup = imgBackup
        self.txt = imgTxt
        self.rct = imgRect
        self.lock = lock if lock is not None else Lock()
        self.run_flag = True

    def run(self):
        """ Thread method. draw ticker """

        x = 0
        xd = 1
        while self.run_flag:

            with self.lock:
                if not self.run_flag:
                    break
                self.screen.blit(self.backup, self.rct)
                self.screen.blit(self.txt, (self.rct.x, self.rct.y), ((x, 0), self.rct.size))
                request_update(self.rct)

            if self.rct.width + x >= self.txt.get_width():
                xd = -1  # backward
            elif x <= 0:
                xd = 1  # forward
            x += xd

            time.sleep(self.ui_refresh)

        # cleanup memory
        del self.screen
        del self.backup
        del self.txt
        del self.rct
        self.trim_memory()

    def stop_thread(self):
        """ Stop thread """

        self.run_flag = False

    # cleanup memory on exit
    def trim_memory(self) -> int:
        libc = ctypes.CDLL("libc.so.6")
        return libc.malloc_trim(0)


# RepeatTimer for remaining time
class RepeatTimer(Timer):
    def run(self):
        self.function(*self.args, **self.kwargs)
        while not self.finished.wait(self.interval):
            self.function(*self.args, **self.kwargs)
