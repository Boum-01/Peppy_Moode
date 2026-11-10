# Copyright 2024 PeppyMeter for Volumio by 2aCD
# (Copyright 2026 PeppyMeter for Moode by Boum)
#
# This file is part of PeppyMeter for Volumio
#
# Adaptation pour moOde Audio : la connexion socket.io (pushState) de Volumio
# est remplacee par la surveillance du fichier de metadonnees de moOde
# (voir moode_metadata.py). Le comportement reste identique :
#   - compteur "random" ou liste "a,b,c" : changement toutes les N secondes
#     (random.meter.interval), ou a chaque nouveau morceau si
#     random.change.title est active ;
#   - pas de redemarrage a l'initialisation.

import time
import ctypes
import traceback
from threading import Thread, Event, Lock

from configfileparser import METER, RANDOM_METER_INTERVAL
from moode_configfileparser import METER_BKP, RANDOM_TITLE, METER_VISIBLE
from moode_metadata import MoodeMetadata, DEFAULT_MOODE_METADATA_FILE


class RandomControl(Thread):
    """ Redemarre le compteur (random / liste) a intervalle fixe ou a chaque titre """

    def __init__(self, util, meter_config_volumio, meter, metadata_file=DEFAULT_MOODE_METADATA_FILE):
        """ Initializer

        :param util: utility class
        :param meter_config_volumio: configuration etendue
        :param meter: objet meter de PeppyMeter (methode restart())
        :param metadata_file: fichier de metadonnees moOde
        """
        Thread.__init__(self)

        self.meter = meter
        self.util = util
        self.meter_config = self.util.meter_config
        self.meter_config_volumio = meter_config_volumio
        self.metadata_file = metadata_file

        self.stop_event = Event()
        self.first_run = True
        self.position_mem = ""
        self.random_meter = False
        self.list_meter = False
        self.metadata_source = None

        # config values
        self.random_meter_interval = int(self.meter_config[RANDOM_METER_INTERVAL])
        meter_bkp = self.meter_config_volumio.get(METER_BKP) or ""
        if meter_bkp == "random":
            self.random_meter = True
        elif "," in meter_bkp:
            self.list_meter = True
        self.random_title = bool((self.random_meter or self.list_meter) and self.meter_config_volumio[RANDOM_TITLE])
        self.seconds = 0
        self._restart_lock = Lock()

    def _restart_meter(self):
        """ restart() protege : un seul a la fois, une erreur n'arrete pas la boucle """
        if not self._restart_lock.acquire(blocking=False):
            return  # un changement est deja en cours
        try:
            self.meter.restart()  # vumeter restart
        except Exception:
            traceback.print_exc()
        finally:
            self._restart_lock.release()

    def _on_metadata_change(self, meta):
        """ Appele par MoodeMetadata : redemarre le compteur quand le morceau change """
        try:
            if self.stop_event.is_set() or meta.get("state") != "play":
                return

            # equivalent de la "position" de Volumio : identifie le morceau en cours
            position = "|".join((meta.get("file") or "", meta.get("title") or "", meta.get("artist") or ""))
            if position != self.position_mem:
                self.position_mem = position

                # no restart on initialization
                if not self.first_run:
                    self._restart_meter()
                self.first_run = False
        except Exception:
            traceback.print_exc()

    def run(self):
        """ Thread method. Restart meters on random mode. """

        if self.random_meter or self.list_meter:

            # change on title: attendre les changements de morceau
            if self.random_title:
                self.metadata_source = MoodeMetadata(filepath=self.metadata_file, listener=self._on_metadata_change)
                self.metadata_source.start()
                self.stop_event.wait()
                self.metadata_source.stop()

            # change on interval
            else:
                while not self.stop_event.wait(1):
                    self.seconds += 1
                    # start random with random interval
                    if self.seconds >= self.random_meter_interval:
                        self.seconds = 0
                        self._restart_meter()

        # cleanup memory
        self.metadata_source = None
        del self.meter
        del self.util
        del self.meter_config
        del self.meter_config_volumio
        self.trim_memory()

    def stop_thread(self):
        """ Stop thread """
        self.stop_event.set()

    # cleanup memory on exit
    def trim_memory(self) -> int:
        libc = ctypes.CDLL("libc.so.6")
        return libc.malloc_trim(0)

# ===================================================================================================================
