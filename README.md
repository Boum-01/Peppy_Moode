# Peppy_Moode (PeppyMeter pour moOde Audio / PeppyMeter for moOde Audio)
Adapted the "Peppymeter programs for Volumio" so that they work with "Moode Audio"

(Fr)
Ajoute à PeppyMeter, sur moOde Audio, l'affichage de la **pochette**, des **informations du morceau**
(titre, artiste, album, format, fréquence d'échantillonnage), du **temps restant** et du **spectre**,
avec changement de Vu-metre aléatoire (`meter = random`) et fondu entre deux Vu-metres.

(En)
Adds to PeppyMeter, on moOde Audio, the display of **album art**, **track information** (title, artist, album, format, sampling rate), **remaining time** and **spectrum**,
with random VU meter changes (`meter = random`) and crossfading between two VU meters.


# PeppyMeter pour moOde Audio : installation par modification du script de lancement / PeppyMeter for moOde Audio: installation by modifying the launch script

(Fr)
**Aucun service n'est créé.** L'installation modifie le script de lancement de moOde (celui qui reçoit
`meter` ou `spectrum` en argument) : la commande `python3 peppymeter.py` devient
`python3 <dossier>/moode_peppymeter.py`. moOde lance et arrête l'affichage comme d'habitude.

    avant :   cd /opt/peppymeter && python3 peppymeter.py >/dev/null &
    après :   # peppy_moode_launcher : lance moode_peppymeter.py a la place de peppymeter.py
              cd /opt/peppymeter && python3 /home/pi/peppy_moode/moode_peppymeter.py >/dev/null &

La branche `spectrum` n'est pas modifiée. L'original est sauvegardé à côté du script
(`<script>.bak-peppy-moode`).

(En)
**No service is created.** The installation modifies the moOde launch script (the one that receives
`meter` or `spectrum` as an argument): the command `python3 peppymeter.py` becomes
`python3 <folder>/moode_peppymeter.py`. moOde starts and stops displaying the reading as usual.

Before: `cd /opt/peppymeter && python3 peppymeter.py >/dev/null &`
After: `# peppy_moode_launcher`: launches moode_peppymeter.py instead of peppymeter.py
`cd /opt/peppymeter && python3 /home/pi/peppy_moode/moode_peppymeter.py >/dev/null &`

The `spectrum` branch is not modified. The original is saved alongside the script
(`<script>.bak-peppy-moode`).

## Prérequis / Prerequisites

(Fr)
- moOde Audio avec écran local (serveur X).
- PeppyMeter installé dans `/opt/peppymeter` (obligatoire), PeppySpectrum dans `/opt/peppyspectrum` (facultatif).
- Internet pendant l'installation, pour les paquets Debian (`python3-pygame`, `python3-pil`,
  `python3-requests`, `python3-mpd`, `fonts-dseg`). Sans Internet : `--skip-deps`.

(En)
- moOde Audio with a local display (X server).
- PeppyMeter installed in `/opt/peppymeter` (required); PeppySpectrum installed in `/opt/peppyspectrum` (optional).
- Internet access required during installation for Debian packages (`python3-pygame`, `python3-pil`,
`python3-requests`, `python3-mpd`, `fonts-dseg`). Without Internet access, use: `--skip-deps`.

## Installation / setup

(Fr)
Copiez `peppy_moode_launcher.tar.gz` sur le Raspberry Pi, puis :

    tar xzf peppy_moode_launcher.tar.gz
    cd peppy_moode_launcher
    sudo python3 install.py --dry-run        # montre ce qui serait fait, sans rien modifier
    sudo python3 install.py

Le script de lancement des moOde récents, `/var/www/util/start-peppy.sh`, est essayé en premier. Sinon il est
**détecté automatiquement** (recherche de `python3 peppymeter.py` dans `/var/www`,
`/usr/local/bin` et `/opt/peppymeter`). S'il y en a zéro ou plusieurs, l'installation s'arrête et vous
l'indiquez : `sudo python3 install.py --launcher /chemin/du/script`.

Les installateurs sont des programmes **Python** : on les lance avec `python3`, quel que soit le shell de la machine
(`sh`, `dash` ou `bash`), et ils ne sont pas gênés par les fins de ligne ou l'octet d'ordre (BOM) qu'un éditeur Windows
peut ajouter. Lancez-les toujours avec `sudo python3 install.py`, pas avec `sh`.

Arrêtez puis relancez ensuite l'affichage PeppyMeter depuis moOde. Pour vérifier : `pgrep -af moode_peppymeter`.

| Option | Effet |
|---|---|
| `--launcher CHEMIN` | script de lancement de moOde à modifier |
| `--user NOM` | propriétaire des fichiers installés (défaut : l'utilisateur de `sudo`, sinon `pi`) |
| `--dir CHEMIN` | dossier d'installation (défaut : `/opt/peppy_moode`) |
| `--icons DOSSIER` | dossier d'icônes SVG à copier en plus |
| `--skip-deps` | n'installe pas les paquets Debian |
| `--dry-run` | affiche ce qui serait fait, sans rien modifier |

L'installation est répétable : relancez-la pour mettre à jour les fichiers (les versions modifiées sont
sauvegardées dans `<dossier>/backup/<date>/`). **Après une mise à jour de moOde** qui aurait remplacé le script de
lancement, relancez `install.py` pour le modifier de nouveau.


(En)
Copy `peppy_moode_launcher.tar.gz` to the Raspberry Pi, then run:

tar xzf peppy_moode_launcher.tar.gz
cd peppy_moode_launcher
sudo python3 install.py --dry-run        # shows what would be done, without making any changes
sudo python3 install.py

The launch script `/var/www/util/start-peppy.sh. If zero or multiple instances are found, the installation will stop; in that case,
specify the path manually using: `sudo python3 install.py --launcher /path/to/script`.

The installers are **Python** programs; they should be executed using `python3`, regardless of the machine's shell
(`sh`, `dash`, or `bash`). They are also unaffected by line endings or a Byte Order Mark (BOM) that might be added
by a Windows-based text editor. Always run them using `sudo python3 install.py`, not `sh`.

Afterward, stop and then restart the PeppyMeter display from within moOde. To verify, run: `pgrep -af moode_peppymeter`.

| Option | Effect |
|---|---|
| `--launcher PATH` | moOde launch script to be modified |
| `--user NAME` | owner of the installed files (default: the user running `sudo`, or `pi` if not specified) |
| `--dir PATH` | installation directory (default: `/opt/peppy_moode`) |
| `--icons FOLDER` | additional folder containing SVG icons to be copied |
| `--skip-deps` | skips the installation of Debian packages |
| `--dry-run` | displays the actions that would be performed, without making any changes |

The installation process can be repeated: run it again to update the files (modified versions are
saved in `<directory>/backup/<date>/`). **After a moOde update** that may have replaced the launch
script, run `install.py` again to re-apply the modifications.

## Icônes de format (SVG) Song Format icons (SVG)

(Fr)
Déposez vos icônes (`flac.svg`, `mp3.svg`, `dsd.svg`, `wav.svg`...) dans le dossier `format-icons/` de ce paquet
**avant** de lancer `install.py`, ou indiquez un dossier avec `--icons`. Elles sont copiées dans
`<dossier d'installation>/format-icons/` si elles existent :

- seuls les fichiers `.svg` sont copiés, et leur nom est mis en minuscules (le programme cherche `flac.svg`) ;
- un fichier déjà présent n'est jamais écrasé : vos icônes personnalisées sont conservées ;
- `/opt/peppymeter/format-icons` est aussi examiné s'il existe.

Sans icône, la zone du format reste vide : c'est facultatif.

(En)
Place your icons (`flac.svg`, `mp3.svg`, `dsd.svg`, `wav.svg`, etc.) in the `format-icons/` folder of this package
**before** running `install.py`, or specify a folder using the `--icons` option. If present, they will be copied to
`<installation directory>/format-icons/`:

- Only `.svg` files are copied, and their filenames are converted to lowercase (e.g., the program looks for `flac.svg`).
- Existing files are never overwritten; your custom icons will be preserved.
- The directory `/opt/peppymeter/format-icons` is also checked, if it exists.

If no icon is provided, the format area will remain empty—this is entirely optional.

## Arrêt de l'affichage par moOde / moOde isplay stop

(Fr)
Sur moOde, le retour à l'interface web arrête l'affichage par `pkill -f peppymeter.py` (ou `pkill -f spectrum.py`
pour le spectre), dans `/usr/local/bin/moodeutl`. Cette commande reconnaît `moode_peppymeter.py`, dont la ligne de
commande contient `peppymeter.py`, et ne vise pas le spectre. L'arrêt continue donc de fonctionner sans rien changer.

(En)
In moOde, returning to the web interface triggers the termination of the display via `pkill -f peppymeter.py` (or `pkill -f spectrum.py` for the spectrum) within the `/usr/local/bin/moodeutl` script. This command identifies `moode_peppymeter.py`—since its command line contains the string `peppymeter.py`—without affecting the spectrum display. Consequently, the shutdown mechanism continues to function as intended without requiring any modifications.


## Configuration / Configuration
(Fr)
Les options propres à cette version (`meter.fade`, `random.change.title`, position et dimension de la pochette,
position des textes, temps restant, spectre...) sont décrites dans `config-options.example.txt`.
Le programme lit les informations du morceau dans `/var/local/www/currentsong.txt` et le temps restant auprès de
MPD (`localhost:6600`).

(En)
The options specific to this version (`meter.fade`, `random.change.title`, album art position and dimensions,
text positioning, remaining time, spectrum, etc.) are described in `config-options.example.txt`.
The program reads track information from `/var/local/www/currentsong.txt` and the remaining time from
MPD (`localhost:6600`).

## Désinstallation / Uninstalling

(Fr)
    cd peppy_moode_launcher
    sudo python3 uninstall.py              restitue le script de lancement d'origine, conserve les fichiers
    sudo python3 uninstall.py --purge      supprime aussi le dossier d'installation (confirmation, ou --yes)

Le script est restitué à l'identique (le commentaire ajouté est retiré). Si moOde a mis à jour le script depuis
l'installation, la sauvegarde périmée est conservée avec un avertissement.

(En)
cd peppy_moode_launcher
sudo python3 uninstall.py              restores the original launch script, keeps the files
sudo python3 uninstall.py --purge      also removes the installation directory (requires confirmation, or use --yes)

The script is restored to its original state (the added comment is removed). If moOde has updated the script since
the installation, the outdated backup is kept, accompanied by a warning.

## Dépannage / Troubleshooting

(Fr)
- **Rien ne s'affiche** : lancez le script de moOde à la main au premier plan pour voir les erreurs, par exemple
  `cd /opt/peppymeter && DISPLAY=:0 python3 /home/pi/peppy_moode/moode_peppymeter.py`.
  Causes fréquentes : module Python manquant, `config.txt` ou `meters.txt` introuvables.
- **Avertissement sur le dossier personnel** : si `/home/<utilisateur>` n'est pas accessible aux autres comptes et
  que moOde lance l'affichage avec un autre utilisateur, réinstallez avec `--dir /opt/peppy_moode`.
- **Pas de temps restant** : installez `python3-mpd`.
- **Arrêt manuel d'un programme lancé à la main** : supprimez `/tmp/peppyrunning`.

(En)
- **Nothing is displayed**: run the moOde script manually in the foreground to check for errors—for example:
`cd /opt/peppymeter && DISPLAY=:0 python3 /home/pi/peppy_moode/moode_peppymeter.py`. 
Common causes: a missing Python module, or `config.txt` or `meters.txt` files that cannot be found.
- **Home directory warning**: if `/home/<user>` is not accessible to other accounts and moOde launches the display using a different user, reinstall using the `--dir /opt/peppy_moode` option.
- **No "time remaining" displayed**: install `python3-mpd`.
- **Manually stopping a program that was launched by hand**: delete the file `/tmp/peppyrunning`.
