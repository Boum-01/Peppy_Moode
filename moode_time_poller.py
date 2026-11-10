# (Copyright 2026 PeppyMeter for Moode by Boum)
#
#
# Temps restant pour moOde Audio.
# Le fichier de metadonnees de moOde ne contient ni duree ni position de
# lecture : ce thread interroge donc MPD directement (python-mpd2) et
# appelle ImageTitleFactory.get_time_data() / render_time(), comme le
# faisait le minuteur alimente par pushState dans la version Volumio.
#
# Dependance : pip install python-mpd2  (paquet Debian : python3-mpd)

import sys
from threading import Thread, Event

try:
    from mpd import MPDClient
except ImportError:
    MPDClient = None


class MpdTimePoller(Thread):
    """ Interroge MPD une fois par seconde et met a jour le temps restant """

    def __init__(self, title_factory, host="localhost", port=6600, password=None, poll_interval=1.0):
        """ Initializer

        :param title_factory: ImageTitleFactory (get_time_data / render_time)
        :param host: hote MPD
        :param port: port MPD
        :param password: mot de passe MPD, si configure
        :param poll_interval: delai entre deux interrogations (secondes)
        """
        Thread.__init__(self, daemon=True)
        self.tf = title_factory
        self.host = host
        self.port = port
        self.password = password
        self.poll_interval = poll_interval
        self.stop_event = Event()

    def _connect(self):
        client = MPDClient()
        client.timeout = 5
        client.connect(self.host, self.port)
        if self.password:
            client.password(self.password)
        return client

    @staticmethod
    def _disconnect(client):
        if client is None:
            return
        try:
            client.close()
        except Exception:
            pass
        try:
            client.disconnect()
        except Exception:
            pass

    def _poll(self, client):
        status = client.status()
        if status.get("state") != "play":
            return

        elapsed = float(status.get("elapsed") or 0)
        duration = float(status.get("duration") or 0)
        if not duration and ":" in (status.get("time") or ""):
            duration = float(status["time"].split(":")[1])
        duration = int(duration)

        # sans duree (webradio) : pas de temps restant
        service = 'webradio' if duration == 0 else ''

        if not getattr(self.tf, "run_flag", True):
            return
        # [duree en s, position en ms, service] ; True = resynchronisation sur MPD
        self.tf.get_time_data([duration, int(elapsed * 1000), service], True)
        self.tf.render_time()

    def run(self):
        if MPDClient is None:
            print("moode_time_poller : python-mpd2 n'est pas installe, temps restant desactive", file=sys.stderr)
            return

        client = None
        while not self.stop_event.is_set():
            try:
                if client is None:
                    client = self._connect()
                self._poll(client)
            except Exception as e:
                pass  # MPD indisponible : nouvelle tentative au prochain cycle
                self._disconnect(client)
                client = None
            self.stop_event.wait(self.poll_interval)

        self._disconnect(client)

    def stop_thread(self):
        """ Stop thread """
        self.stop_event.set()
