#!/usr/bin/env python3
"""
Script per pulire i file:
1. Elimina TUTTI i file che contengono 'Zone.Identifier' nel nome
   (gestisce il carattere speciale che WSL usa al posto dei :)
2. Per ogni gruppo di file con stesso nome base, tiene quello col numero piu alto.
"""

import os
import re
import sys
import platform


def clean_zone_identifiers(directory):
    """
    Elimina tutti i file che contengono 'Zone.Identifier' nel nome.
    Su WSL, Windows mappa gli ADS con un carattere speciale al posto di ':'.
    """
    removed = []
    checked = 0

    for root, dirs, files in os.walk(directory):
        for filename in files:
            filepath = os.path.join(root, filename)
            checked += 1

            # ELIMINA a prescindere tutto cio' che contiene 'Zone.Identifier'
            # (su WSL il carattere prima di 'Zone.Identifier' non e' ':' ma un simbolo speciale)
            if 'Zone.Identifier' in filename:
                try:
                    os.remove(filepath)
                    removed.append(filepath)
                    print(f"    ✓ Eliminato: {repr(filename)}")
                except Exception as e:
                    print(f"    [ERRORE] Non riesco a eliminare: {filepath} - {e}")
                continue

            # Su Windows NTFS nativo: prova a rimuovere l'ADS nascosto
            ads_path = filepath + ':Zone.Identifier'
            try:
                if os.path.exists(ads_path):
                    os.remove(ads_path)
                    removed.append(ads_path)
                    print(f"    ✓ Eliminato ADS: {ads_path}")
            except Exception as e:
                print(f"    [ERRORE] Non riesco a eliminare ADS: {ads_path} - {e}")

        # Directory
        for dirname in dirs:
            dirpath = os.path.join(root, dirname)
            checked += 1
            ads_path = dirpath + ':Zone.Identifier'
            try:
                if os.path.exists(ads_path):
                    os.remove(ads_path)
                    removed.append(ads_path)
            except Exception as e:
                print(f"    [ERRORE] Non riesco a eliminare ADS dir: {ads_path} - {e}")

    return removed, checked


def parse_filename(filename):
    """
    Analizza il nome file.
    Ritorna (base_name, number, extension) dove number e' 0 se non c'e' (NUMERO).
    Supporta spazi opzionali prima/dopo le parentesi.
    """
    match = re.match(r'^(.*?)\s*\((\d+)\)\s*(\.[^.]+)?$', filename)
    if match:
        base_name = match.group(1).rstrip()
        number = int(match.group(2))
        extension = match.group(3) or ''
        return base_name, number, extension

    if '.' in filename and not filename.startswith('.'):
        base_name, extension = filename.rsplit('.', 1)
        extension = '.' + extension
    else:
        base_name = filename
        extension = ''
    return base_name, 0, extension


def rename_numbered_files(directory):
    """
    Per ogni gruppo di file con stesso (cartella, base, estensione),
    tiene quello col numero piu alto, cancella gli altri, e rinomina il vincitore.
    Salta i file che contengono 'Zone.Identifier' (gestiti dalla Fase 1).
    """
    renamed = []
    deleted = []
    skipped = []

    all_files = []
    for root, dirs, files in os.walk(directory):
        for filename in files:
            # SALTA i Zone.Identifier — sono gestiti dalla Fase 1
            if 'Zone.Identifier' in filename:
                continue

            filepath = os.path.join(root, filename)
            base_name, number, extension = parse_filename(filename)
            key = (root, base_name, extension)
            all_files.append((key, filepath, filename, number))

    # Raggruppa per (cartella, base, estensione)
    groups = {}
    for key, filepath, filename, number in all_files:
        groups.setdefault(key, []).append((filepath, filename, number))

    # Processa ogni gruppo
    for (root, base_name, extension), items in groups.items():
        if len(items) == 1:
            filepath, filename, number = items[0]
            if number > 0:
                new_filename = base_name + extension
                new_filepath = os.path.join(root, new_filename)
                if os.path.exists(new_filepath):
                    skipped.append(filepath)
                    continue
                try:
                    os.rename(filepath, new_filepath)
                    renamed.append((filepath, new_filepath))
                except Exception as e:
                    print(f"  [ERRORE] Non riesco a rinominare {filepath}: {e}")
                    skipped.append(filepath)
            continue

        # Piu' file nello stesso gruppo: ordina per numero decrescente
        items.sort(key=lambda x: x[2], reverse=True)

        winner_filepath, winner_filename, winner_number = items[0]
        new_filename = base_name + extension
        new_filepath = os.path.join(root, new_filename)

        # Cancella tutti i file che non sono il vincitore
        for filepath, filename, number in items[1:]:
            try:
                os.remove(filepath)
                deleted.append(filepath)
            except Exception as e:
                print(f"  [ERRORE] Non riesco a eliminare {filepath}: {e}")
                skipped.append(filepath)

        # Se il vincitore ha un numero, rinominalo togliendo il numero
        if winner_number > 0:
            if os.path.exists(new_filepath) and new_filepath != winner_filepath:
                try:
                    os.remove(new_filepath)
                    deleted.append(new_filepath)
                except Exception as e:
                    print(f"  [ERRORE] Non riesco a eliminare {new_filepath}: {e}")
                    skipped.append(winner_filepath)
                    continue
            try:
                os.rename(winner_filepath, new_filepath)
                renamed.append((winner_filepath, new_filepath))
            except Exception as e:
                print(f"  [ERRORE] Non riesco a rinominare {winner_filepath}: {e}")
                skipped.append(winner_filepath)

    return renamed, deleted, skipped


def main():
    if len(sys.argv) > 1:
        target_dir = os.path.abspath(sys.argv[1])
    else:
        target_dir = os.getcwd()

    print(f"\n{'='*60}")
    print(f"  PULIZIA FILE in: {target_dir}")
    print(f"  OS: {platform.system()} {platform.release()}")
    print(f"{'='*60}\n")

    # 1. Elimina i Zone.Identifier
    print("[FASE 1] Rimozione file Zone.Identifier...")
    print("  (cerca 'Zone.Identifier' ovunque nel nome, non solo alla fine)\n")
    removed, checked = clean_zone_identifiers(target_dir)
    if removed:
        print(f"\n  Totale eliminati: {len(removed)} Zone.Identifier (controllati {checked} elementi)")
    else:
        print(f"  Nessun file Zone.Identifier trovato (controllati {checked} elementi).")

    # 2. Gestisce i file con (NUMERO)
    print(f"\n[FASE 2] Gestione file duplicati con (NUMERO)...")
    print("  (tiene la versione col numero piu alto, cancella le altre)\n")
    renamed, deleted, skipped = rename_numbered_files(target_dir)

    if deleted:
        print(f"  Cancellati {len(deleted)} file obsoleti:")
        for f in deleted:
            print(f"    ✗ {f}")

    if renamed:
        print(f"\n  Rinominati {len(renamed)} file (versione piu recente):")
        for old, new in renamed:
            print(f"    ✓ {old}  →  {new}")

    if not deleted and not renamed:
        print("  Nessun file duplicato con (NUMERO) trovato.")

    if skipped:
        print(f"\n  ⚠️  {len(skipped)} operazioni saltate a causa di errori.")

    print(f"\n{'='*60}")
    print("  PULIZIA COMPLETATA!")
    print(f"{'='*60}\n")


if __name__ == '__main__':
    main()
