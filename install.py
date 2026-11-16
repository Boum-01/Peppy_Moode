#!/usr/bin/env python3
"""Installe la version moOde de PeppyMeter (pochette, infos du morceau, temps restant, spectre) et modifie
le script de lancement de moOde pour qu'il demarre moode_peppymeter.py au lieu de peppymeter.py.
Aucun service systemd n'est cree : c'est moOde qui lance et arrete l'affichage, comme d'habitude.

Prerequis : PeppyMeter installe dans /opt/peppymeter (PeppySpectrum dans /opt/peppyspectrum, facultatif).
Usage     : sudo python3 install.py [options]      (voir --help)
"""
import argparse
import datetime
import filecmp
import importlib.util
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC / "lib"))
import patch_launcher  # noqa: E402

ROOT = os.environ.get("ROOT", "")          # racine alternative, pour les tests ; vide = systeme reel
PEPPY_DIR = "/opt/peppymeter"
SPECTRUM_DIR = "/opt/peppyspectrum"
BACKUP_SUFFIX = ".bak-peppy-moode"
KNOWN_LAUNCHER = "/var/www/util/start-peppy.sh"      # script de lancement des moOde recents
FILES = ["moode_peppymeter.py", "moode_albumart.py", "moode_display.py", "moode_random.py",
         "moode_metadata.py", "moode_time_poller.py", "moode_spectrum.py", "moode_configfileparser.py"]
COLOR = sys.stdout.isatty()


def info(msg):
    print(("\033[1;34m==>\033[0m " if COLOR else "==> ") + msg, flush=True)


def ok(msg):
    print("    " + ("\033[1;32mOK\033[0m " if COLOR else "OK ") + msg, flush=True)


def warn(msg):
    print(("\033[1;33mAttention :\033[0m " if COLOR else "Attention : ") + msg, file=sys.stderr, flush=True)


def die(msg):
    print(("\033[1;31mErreur :\033[0m " if COLOR else "Erreur : ") + msg, file=sys.stderr, flush=True)
    sys.exit(1)


def rp(path):
    """Chemin reel d'un chemin du systeme (prefixe ROOT en test)."""
    return Path(ROOT + str(path))


def shown(path):
    """Chemin tel que le voit le systeme (sans le prefixe ROOT)."""
    s = str(path)
    return s[len(ROOT):] if ROOT and s.startswith(ROOT) else s


def parse_args():
    p = argparse.ArgumentParser(
        prog="install.py", allow_abbrev=False,
        description="Installation de PeppyMeter pour moOde Audio (sans service : le script de lancement de moOde est modifie)",
        epilog="Variable : ROOT=/chemin  installe dans une racine alternative (tests), sans apt.")
    p.add_argument("--launcher", metavar="CHEMIN",
                   help="script de lancement de moOde a modifier (celui qui contient « python3 peppymeter.py ») ; "
                        "detecte automatiquement s'il n'y en a qu'un")
    p.add_argument("--user", metavar="NOM", default=os.environ.get("SUDO_USER") or "pi",
                   help="proprietaire des fichiers installes (defaut : %(default)s)")
    p.add_argument("--dir", metavar="CHEMIN", help="dossier d'installation (defaut : /home/<utilisateur>/peppy_moode)")
    p.add_argument("--icons", metavar="DOSSIER", help="dossier d'icones SVG a copier (en plus de format-icons/ du paquet)")
    p.add_argument("--skip-deps", action="store_true",
                   help="n'installe pas les paquets Debian (pygame, PIL, requests, mpd, police DSEG)")
    p.add_argument("--dry-run", action="store_true", help="affiche ce qui serait fait, sans rien modifier")
    return p.parse_args()


# ------------------------------------------------------------------ recherche des scripts
def iter_files(base, maxdepth, maxsize):
    """Fichiers reguliers (hors sauvegardes et liens) de base, jusqu'a maxdepth niveaux, de moins de maxsize octets."""
    base = Path(base)
    if not base.is_dir():
        return
    base_depth = len(base.parts)
    for dirpath, dirnames, filenames in os.walk(base):
        if len(Path(dirpath).parts) - base_depth + 1 >= maxdepth:
            dirnames[:] = []
        for name in filenames:
            p = Path(dirpath) / name
            if name.endswith(BACKUP_SUFFIX) or p.is_symlink():
                continue
            try:
                if p.is_file() and p.stat().st_size < maxsize:
                    yield p
            except OSError:
                continue


def read_text(path):
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:4096]:
        return None
    return data.decode("utf-8", "replace")


def find_launchers(pattern):
    """Scripts qui contiennent PATTERN (expression reguliere) et /opt/peppymeter."""
    rx = re.compile(pattern)
    found = set()
    for base in ("/var/www", "/usr/local/bin", PEPPY_DIR):
        for p in iter_files(rp(base), 4, 64 * 1024):
            text = read_text(p)
            if text and rx.search(text) and "/opt/peppymeter" in text:
                found.add(shown(p))
    return sorted(found)


def choose_launcher(launcher):
    if launcher:
        if not rp(launcher).is_file():
            die("script de lancement introuvable : %s" % launcher)
        return launcher
    # le script de lancement connu de moOde est essaye en premier
    known = rp(KNOWN_LAUNCHER)
    if known.is_file() and re.search(r"python3 peppymeter\.py|moode_peppymeter\.py", read_text(known) or ""):
        return KNOWN_LAUNCHER
    # sinon on cherche : le script est reconnu qu'il soit encore d'origine ou deja modifie (reinstallation)
    found = find_launchers(r"python3 peppymeter\.py|moode_peppymeter\.py")
    if not found:
        die("aucun script de lancement trouve (recherche de « python3 peppymeter.py » dans /var/www, /usr/local/bin\n"
            "        et /opt/peppymeter). Indiquez-le avec --launcher /chemin/du/script")
    if len(found) > 1:
        for f in found:
            print("    " + f, file=sys.stderr)
        die("plusieurs scripts de lancement possibles (liste ci-dessus) : choisissez avec --launcher")
    return found[0]


# ------------------------------------------------------------------ principal
def main():
    args = parse_args()
    dry = args.dry_run

    def step(*cmd):
        """Execute une commande (ou l'affiche en simulation) ; retourne son code de sortie."""
        if dry:
            print("    + " + " ".join(str(c) for c in cmd))
            return 0
        return subprocess.call([str(c) for c in cmd])

    # ---- verifications
    info("Verifications")
    if not ROOT and os.geteuid() != 0:
        die("lancez l'installation avec sudo : sudo python3 install.py")
    if ROOT:
        home = "/opt"
    else:
        try:
            pw = pwd.getpwnam(args.user)
        except KeyError:
            die("l'utilisateur '%s' n'existe pas (voir --user)" % args.user)
        home = pw.pw_dir
    install_dir = (args.dir or home + "/peppy_moode").rstrip("/")

    if not (rp(PEPPY_DIR) / "peppymeter.py").is_file():
        die("PeppyMeter est introuvable dans %s (fichier peppymeter.py absent).\n"
            "        Installez-le d'abord ; ce paquet ajoute les fonctions moOde par-dessus." % PEPPY_DIR)
    if not (rp(PEPPY_DIR) / "configfileparser.py").is_file():
        warn("%s/configfileparser.py est absent : l'installation de PeppyMeter semble incomplete." % PEPPY_DIR)
    if not rp("/etc/peppymeter/config.txt").is_file() and not (rp(PEPPY_DIR) / "config.txt").is_file():
        warn("aucun config.txt trouve dans /etc/peppymeter ni dans %s." % PEPPY_DIR)
    if not (rp(SPECTRUM_DIR) / "spectrum.py").is_file() and not (rp(SPECTRUM_DIR) / "spectrum").is_dir():
        warn("PeppySpectrum est introuvable dans %s : l'affichage du spectre sera indisponible." % SPECTRUM_DIR)
    for name in FILES:
        if not (SRC / "files" / name).is_file():
            die("fichier manquant dans le paquet : files/%s" % name)
    if args.icons and not Path(args.icons).is_dir():
        die("dossier d'icones introuvable : %s" % args.icons)

    launcher = choose_launcher(args.launcher)
    launcher_real = rp(launcher)
    if not dry and not os.access(launcher_real, os.W_OK):
        die("le script %s n'est pas modifiable (droits insuffisants)" % launcher)
    ok("utilisateur %s, installation dans %s" % (args.user, install_dir))
    ok("script de lancement : %s" % launcher)

    # ---- dependances
    if not args.skip_deps and not ROOT:
        info("Paquets Debian")
        if shutil.which("apt-get"):
            if step("apt-get", "install", "-y", "--no-install-recommends",
                    "python3-pygame", "python3-pil", "python3-requests", "python3-mpd") != 0:
                warn("installation des paquets Python incomplete (voir le message ci-dessus)")
            if step("apt-get", "install", "-y", "--no-install-recommends", "fonts-dseg") != 0:
                warn("paquet fonts-dseg indisponible : le temps restant utilisera une police par defaut")
        else:
            warn("apt-get est absent : installez vous-meme python3-pygame, python3-pil, python3-requests et python3-mpd")
    if not ROOT and not dry:
        for mod in ("pygame", "PIL", "requests", "mpd"):
            if importlib.util.find_spec(mod) is not None:
                ok("module Python %s" % mod)
            elif mod == "mpd":
                warn("module Python 'mpd' absent (python3-mpd) : le temps restant sera desactive")
            else:
                warn("module Python '%s' absent : le programme ne demarrera pas" % mod)
        try:
            libs = subprocess.run(["ldconfig", "-p"], capture_output=True, text=True).stdout
        except OSError:
            libs = "libX11.so.6"            # ldconfig absent : on ne peut rien affirmer
        if "libX11.so.6" not in libs:
            warn("libX11.so.6 est introuvable (affichage X11 necessaire)")

    # ---- fichiers
    info("Copie des fichiers dans %s" % install_dir)
    dest = rp(install_dir)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = dest / "backup" / stamp
    for d in (dest, dest / "fonts", dest / "format-icons"):
        if dry:
            print("    + mkdir -p %s" % d)
        else:
            d.mkdir(parents=True, exist_ok=True)
    backed_up = False
    for name in FILES:
        src, target = SRC / "files" / name, dest / name
        if target.is_file() and not filecmp.cmp(src, target, shallow=False):
            if dry:
                print("    + sauvegarde %s -> %s" % (target, backup / name))
            else:
                backup.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup / name)
            backed_up = True
        if dry:
            print("    + install -m 0644 %s %s" % (src, target))
            continue
        shutil.copyfile(src, target)
        os.chmod(target, 0o644)
        try:
            compile(target.read_text(encoding="utf-8-sig"), str(target), "exec")
        except SyntaxError as e:
            die("erreur de syntaxe dans %s : %s" % (name, e))
    if backed_up:
        ok("anciennes versions sauvegardees dans %s" % backup)
    ok("%d fichiers copies" % len(FILES))

    # police a segments (temps restant)
    if not dry:
        for name in ("DSEG7Classic-Light.ttf", "DSEG7Classic-Italic.ttf"):
            link = dest / "fonts" / name
            found = next((p for p in rp("/usr/share/fonts").rglob(name)), None) if rp("/usr/share/fonts").is_dir() else None
            if found and not link.exists():
                if link.is_symlink():
                    link.unlink()                       # ancien lien casse
                link.symlink_to(shown(found))
                ok("police %s" % name)
        if not (dest / "fonts" / "DSEG7Classic-Light.ttf").exists() and not (dest / "fonts" / "DSEG7Classic-Italic.ttf").exists():
            warn("police DSEG7 absente : copiez DSEG7Classic-Light.ttf dans %s/fonts/ pour le temps restant" % install_dir)

    # icones de format SVG : copiees si elles existent, sans jamais ecraser un fichier deja present
    info("Icones de format (SVG)")
    sources = []
    if args.icons:
        sources.append(Path(args.icons))
    sources += [SRC / "format-icons", rp(PEPPY_DIR) / "format-icons",
                rp("/opt/peppy_meter/format-icons")]
    for d in sources:
        if not d.is_dir():
            continue
        svgs = sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() == ".svg")
        if not svgs:
            continue
        copied = 0
        for f in svgs:
            target = dest / "format-icons" / f.name.lower()    # le programme cherche flac.svg, en minuscules
            if not target.exists():
                if dry:
                    print("    + install -m 0644 %s %s" % (f, target))
                else:
                    shutil.copyfile(f, target)
                    os.chmod(target, 0o644)
                copied += 1
        ok("%d icone(s) copiee(s) depuis %s (%d deja presente(s))" % (copied, shown(d), len(svgs) - copied))
    icons_dir = dest / "format-icons"
    present = len([p for p in icons_dir.iterdir() if p.suffix.lower() == ".svg"]) if icons_dir.is_dir() else 0
    if present == 0 and not dry:
        print("    Aucune icone SVG trouvee : la zone du format restera vide (facultatif).")
        print("    Pour en ajouter : copiez flac.svg, mp3.svg, dsd.svg... dans %s/format-icons/" % install_dir)

    if not ROOT and not dry:
        gid = pwd.getpwnam(args.user).pw_gid
        uid = pwd.getpwnam(args.user).pw_uid
        for dirpath, dirnames, filenames in os.walk(dest):
            for name in dirnames + filenames:
                p = Path(dirpath) / name
                os.chown(p, uid, gid, follow_symlinks=False)
                if not p.is_symlink():
                    mode = stat.S_IMODE(p.stat().st_mode) | 0o444          # a+r
                    if p.is_dir() or mode & 0o111:
                        mode |= 0o111                                       # +X
                    os.chmod(p, mode)
        os.chown(dest, uid, gid)
        # le programme est lance par moOde, parfois avec un autre compte que l'utilisateur
        if Path(home).is_dir() and not os.stat(home).st_mode & stat.S_IXOTH and install_dir.startswith(home + "/"):
            warn("%s n'est pas accessible aux autres comptes ; si moOde lance l'affichage avec un autre\n"
                 "         utilisateur, reinstallez avec --dir /opt/peppy_moode" % home)

    # ---- script de lancement
    info("Modification du script de lancement de moOde")
    backup_file = Path(str(launcher_real) + BACKUP_SUFFIX)
    text = patch_launcher.read(launcher_real)
    new_text, status = patch_launcher.apply(text, install_dir)
    if status == "NOTHING":
        die("aucune commande « python3 peppymeter.py » dans %s" % launcher)
    # copie de l'original, une seule fois, et seulement s'il contient encore la commande d'origine
    if not dry and not backup_file.exists() and patch_launcher.OLD.search(text):
        shutil.copy2(launcher_real, backup_file)
        ok("original sauvegarde : %s" % shown(backup_file))
    if new_text != text and not dry:
        patch_launcher.write(launcher_real, new_text)
    if status == "PATCHED":
        ok("script modifie : moOde lancera moode_peppymeter.py")
    elif status == "UPDATED":
        ok("script deja modifie : chemin mis a jour")
    else:
        ok("script deja a jour")
    for line in patch_launcher.diff_lines(text, new_text):
        print(line)

    # ---- arret de l'affichage par moOde
    # moOde arrete l'affichage par une commande du type « pkill -f peppymeter.py » : elle doit reconnaitre
    # moode_peppymeter.py. On la cherche pour le verifier.
    info("Verification de l'arret de l'affichage")
    kill_rx = re.compile(r"(pkill|killall|pgrep|kill)[^#]*peppy")
    pkill_rx = re.compile(r"\b(pkill|killall)\b")
    hits = []
    for base in ("/var/www", "/usr/local/bin"):
        for p in iter_files(rp(base), 4, 1024 * 1024):
            text_p = read_text(p)
            if not text_p or "peppy" not in text_p:
                continue
            lines = text_p.splitlines()
            direct = [n for n, line in enumerate(lines, 1) if kill_rx.search(line)]
            if direct:
                numbers = direct
            elif pkill_rx.search(text_p) and "peppymeter.py" in text_p:
                # le nom du processus passe par une variable (PHP) : on montre ou il est defini et la commande d'arret
                numbers = [n for n, line in enumerate(lines, 1)
                           if "peppymeter.py" in line or (pkill_rx.search(line) and "$" in line)]
            else:
                continue
            for n in numbers:
                hits.append("%s:%d:%s" % (shown(p), n, lines[n - 1].strip()))
    hits = hits[:12]
    if not hits:
        print("    Aucune commande d'arret de PeppyMeter trouvee dans /var/www et /usr/local/bin.")
    else:
        for h in hits:
            print("    " + h)
        if any(re.search(r"python3 +peppymeter\.py", h) for h in hits):
            warn("une commande ci-dessus cherche « python3 peppymeter.py » : elle ne reconnaitra pas moode_peppymeter.py.\n"
                 "        Une commande « pkill -f peppymeter.py » fonctionne (moode_peppymeter.py contient peppymeter.py).")
        else:
            print("    Une commande « pkill -f peppymeter.py » reconnait moode_peppymeter.py ; verifiez les autres au besoin.")

    # ---- resume
    print()
    print("Installation terminee.")
    print()
    print("  Fichiers           : %s" % install_dir)
    print("  Script de lancement: %s" % launcher)
    if backup_file.exists():
        print("  Original sauvegarde: %s%s" % (launcher, BACKUP_SUFFIX))
    print("  Options            : voir %s/config-options.example.txt" % SRC)
    print()
    print("Arretez puis relancez l'affichage PeppyMeter depuis moOde pour utiliser cette version.")
    print("Pour verifier qu'elle tourne : pgrep -af moode_peppymeter")
    print("Apres une mise a jour de moOde, relancez install.py si le script de lancement a ete remplace.")


if __name__ == "__main__":
    main()
