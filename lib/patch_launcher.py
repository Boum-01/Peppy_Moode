#!/usr/bin/env python3
"""Modifie le script de lancement de moOde : remplace « python3 peppymeter.py » par
« python3 <dossier>/moode_peppymeter.py ».

Utilise par install.py et uninstall.py ; utilisable seul :
    patch_launcher.py apply|revert SCRIPT DOSSIER [--dry-run]
Affiche les lignes modifiees (format diff) puis, sur la derniere ligne, l'etat :
  PATCHED    la commande a ete remplacee
  UPDATED    le script etait deja modifie ; le chemin a ete mis a jour
  UNCHANGED  rien a faire
  REVERTED   l'original a ete restitue
  NOTHING    aucune commande reconnue dans le script (code de sortie 3)
"""
import difflib
import re
import sys

MARKER = "# peppy_moode_launcher : lance moode_peppymeter.py a la place de peppymeter.py"
OLD = re.compile(r"python3 peppymeter\.py")
NEW = re.compile(r"python3 \S*moode_peppymeter\.py")


def apply(text, install_dir):
    """Retourne (nouveau texte, etat) pour un script de lancement."""
    new_cmd = "python3 %s/moode_peppymeter.py" % install_dir.rstrip("/")
    if NEW.search(text):                      # deja modifie : on met seulement le chemin a jour
        out = NEW.sub(lambda m: new_cmd, text)
        return out, ("UPDATED" if out != text else "UNCHANGED")
    if not OLD.search(text):
        return text, "NOTHING"
    out = []
    for line in text.split("\n"):
        if OLD.search(line):
            indent = re.match(r"[ \t]*", line).group(0)
            out.append(indent + MARKER)
            line = OLD.sub(lambda m: new_cmd, line)
        out.append(line)
    return "\n".join(out), "PATCHED"


def revert(text):
    """Retourne (texte d'origine, etat)."""
    out = []
    for line in text.split("\n"):
        if line.strip() == MARKER:
            continue
        out.append(NEW.sub("python3 peppymeter.py", line))
    new = "\n".join(out)
    return new, ("REVERTED" if new != text else "UNCHANGED")


def diff_lines(old, new):
    """Lignes supprimees (-) et ajoutees (+), indentees pour l'affichage."""
    result = []
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), "avant", "apres", lineterm="", n=0):
        if not line.startswith(("---", "+++", "@@")):
            result.append("    " + line)
    return result


def read(path):
    with open(path, encoding="utf-8", errors="surrogateescape", newline="") as f:
        return f.read()


def write(path, text):
    with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:   # en place : droits conserves
        f.write(text)


def main(argv):
    args = [a for a in argv if a != "--dry-run"]
    dry = "--dry-run" in argv
    if len(args) < 3 or args[0] not in ("apply", "revert"):
        print(__doc__)
        return 2
    mode, path, install_dir = args[0], args[1], args[2]
    text = read(path)
    new, status = apply(text, install_dir) if mode == "apply" else revert(text)
    if new != text:
        print("\n".join(diff_lines(text, new)))
        if not dry:
            write(path, new)
    print(status)
    return 3 if status == "NOTHING" else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
