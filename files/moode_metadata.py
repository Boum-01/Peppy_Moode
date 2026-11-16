# (Copyright 2026 PeppyMeter for Moode by Boum)
#
# Source d'informations de lecture pour moOde Audio.
# Remplace la connexion socket.io (pushState) de Volumio : le fichier de
# metadonnees de moOde (/var/local/www/currentsong.txt, lignes "cle=valeur")
# est surveille, et un "listener" est appele a chaque changement.
#
# Le dictionnaire transmis au listener contient toutes les cles du fichier
# (en minuscules) plus :
#   state        : "play", "pause" ou "stop"
#   albumart_url : copie de "coverurl" (chemin relatif au site moOde ou URL)
#
# Verifiez les cles reelles de votre version avec :
#   cat /var/local/www/currentsong.txt
# puis ajustez KEY_MAP si besoin.

import os
import traceback
from threading import Thread, Event

DEFAULT_MOODE_METADATA_FILE = "/var/local/www/currentsong.txt"

# cle moOde -> cle interne utilisee par moode_albumart / moode_random
KEY_MAP = {
    "coverurl": "albumart_url",
    "albumart": "albumart_url",
}


def parse_metadata(text):
    """ Convertit le contenu du fichier (lignes cle=valeur) en dictionnaire """
    meta = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        key = key.strip().lower()
        if not sep or not key:
            continue
        value = value.strip()
        meta[key] = value
        if key in KEY_MAP:
            meta[KEY_MAP[key]] = value
    return meta


class MoodeMetadata(Thread):
    """ Surveille le fichier de metadonnees moOde et previent le listener """

    def __init__(self, filepath=DEFAULT_MOODE_METADATA_FILE, listener=None, poll_interval=0.5):
        """ Initializer

        :param filepath: fichier de metadonnees moOde
        :param listener: fonction listener(meta: dict), appelee au demarrage puis a chaque changement
        :param poll_interval: delai entre deux controles du fichier (secondes)
        """
        Thread.__init__(self, daemon=True)
        self.filepath = filepath
        self.listener = listener
        self.poll_interval = poll_interval
        self.stop_event = Event()

    def _read(self):
        """ Lit et analyse le fichier ; None s'il est absent, vide ou incomplet """
        try:
            with open(self.filepath, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            return None
        meta = parse_metadata(text)
        # fichier en cours d'ecriture ou vide : on attend la lecture suivante
        if "state" not in meta:
            return None
        meta["state"] = meta["state"].lower()
        return meta

    def _signature(self):
        try:
            st = os.stat(self.filepath)
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def run(self):
        last_meta = None
        last_sig = None
        while not self.stop_event.is_set():
            sig = self._signature()
            if sig is not None and sig != last_sig:
                last_sig = sig
                meta = self._read()
                if meta is not None and meta != last_meta:
                    last_meta = meta
                    if self.listener is not None:
                        try:
                            self.listener(meta)
                        except Exception:
                            traceback.print_exc()
            self.stop_event.wait(self.poll_interval)

    def stop(self):
        """ Stop thread """
        self.stop_event.set()
