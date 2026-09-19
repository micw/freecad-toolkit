# LiveGeometryReloader.FCMacro
"""
Dieses Makro überwacht eine Python-Geometriedatei und aktualisiert die
Ansicht in der FreeCAD GUI live, sobald die Datei gespeichert wird.

Workflow:
1. Führe das Makro aus.
2. Wähle die Python-Datei aus, die du bearbeiten möchtest
   (z.B. 'geometry_sphere.py').
3. Die Datei muss eine Funktion 'create_geometry(doc)' enthalten.
4. Bearbeite und speichere die Python-Datei in einem externen Editor.
5. Die Geometrie im aktiven FreeCAD-Dokument wird automatisch aktualisiert.
"""

import sys
import os
import importlib
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFileDialog, QMessageBox
import FreeCAD as App
import FreeCADGui as Gui

# --- Globale Variablen zur Zustandsspeicherung ---
monitored_file = None
last_mtimes = {}
timer = None
doc_name = None  # Name des Dokuments, in das geladen wird
watch_files = []
project_root = None
toolkit_root = None
instance_token = None

INSTANCE_STATE_ATTR = "_live_geometry_reloader_state"


def _normalize_path(path: str) -> str:
    """Normalisiert Dateipfade für Vergleiche."""
    return os.path.abspath(os.path.realpath(path))


def _find_project_root(path: str) -> str:
    """Findet die nächste Projektwurzel oberhalb der Geometriedatei."""
    current = _normalize_path(path if os.path.isdir(path) else os.path.dirname(path))
    start = current
    while True:
        if os.path.exists(os.path.join(current, ".git")) or os.path.exists(os.path.join(current, "pyproject.toml")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return start
        current = parent


if "__file__" in globals():
    configured_toolkit_root = os.environ.get("FREECAD_TOOLKIT_ROOT")
    toolkit_root = _normalize_path(
        configured_toolkit_root or os.path.dirname(os.path.dirname(__file__))
    )


def _get_shared_state():
    """Liefert einen prozessweiten Zustand, damit alte Makroinstanzen auffindbar bleiben."""
    state = getattr(App, INSTANCE_STATE_ATTR, None)
    if not isinstance(state, dict):
        state = {}
        setattr(App, INSTANCE_STATE_ATTR, state)
    return state


def _clear_shared_state(expected_token=None):
    """Entfernt den gemeinsamen Zustand optional nur für die erwartete Instanz."""
    state = _get_shared_state()
    if expected_token is not None and state.get("instance_token") is not expected_token:
        return
    setattr(App, INSTANCE_STATE_ATTR, {})


def _stop_timer(timer_obj, callback=None):
    """Stoppt einen Timer defensiv und löst seine Callback-Verbindung."""
    if timer_obj is None:
        return

    try:
        if timer_obj.isActive():
            timer_obj.stop()
    except Exception:
        pass

    if callback is not None:
        try:
            timer_obj.timeout.disconnect(callback)
        except Exception:
            pass


def _publish_instance_state():
    """Registriert die aktuelle Makroinstanz prozessweit."""
    if instance_token is None:
        return

    state = _get_shared_state()
    state.clear()
    state.update({
        "instance_token": instance_token,
        "timer": timer,
        "callback": check_for_update,
        "stop": stop_monitoring,
        "monitored_file": monitored_file,
        "doc_name": doc_name,
    })


def _is_current_instance():
    """Prüft, ob diese Skriptinstanz noch die aktive Reload-Instanz ist."""
    return instance_token is not None and _get_shared_state().get("instance_token") is instance_token


def _stop_previous_instance():
    """Beendet eine vorherige Makroinstanz, auch wenn das Skript neu geladen wurde."""
    state = _get_shared_state()
    previous_token = state.get("instance_token")
    if previous_token is None:
        return

    previous_stop = state.get("stop")
    previous_timer = state.get("timer")
    previous_callback = state.get("callback")
    previous_file = state.get("monitored_file")
    previous_name = os.path.basename(previous_file) if previous_file else "unbekannt"

    App.Console.PrintMessage(f"Beende vorherige Live-Reload-Instanz für '{previous_name}'.\n")

    try:
        if callable(previous_stop):
            previous_stop("Vorherige Live-Reload-Instanz wurde ersetzt.\n")
        else:
            _stop_timer(previous_timer, previous_callback)
    except TypeError:
        try:
            previous_stop()
        except Exception:
            _stop_timer(previous_timer, previous_callback)
    except Exception:
        _stop_timer(previous_timer, previous_callback)
    finally:
        if _get_shared_state().get("instance_token") is previous_token:
            _clear_shared_state(previous_token)


def _module_file_path(module):
    """Ermittelt den .py-Pfad eines geladenen Moduls, sofern vorhanden."""
    path = getattr(module, "__file__", None)
    if not path:
        return None
    if path.endswith(".pyc"):
        path = path[:-1]
    return _normalize_path(path)


def _is_under_project_root(path: str) -> bool:
    """Prüft, ob ein Pfad innerhalb der Projektwurzel liegt."""
    if not path or not project_root:
        return False
    try:
        return os.path.commonpath([project_root, path]) == project_root
    except ValueError:
        return False


def _collect_loaded_module_watch_files():
    """Sammelt alle geladenen Python-Dateien unterhalb der Projektwurzel."""
    loaded = []
    seen = set()
    for module in sys.modules.values():
        if module is None:
            continue
        path = _module_file_path(module)
        if not path or not path.endswith(".py"):
            continue
        if not _is_under_project_root(path):
            continue
        if path in seen:
            continue
        loaded.append(path)
        seen.add(path)
    return loaded


def _ensure_import_paths(module_path: str):
    """Stellt sicher, dass Projektwurzel und Modulordner importierbar sind."""
    module_dir = _normalize_path(os.path.dirname(module_path))
    search_paths = []
    if project_root:
        search_paths.append(project_root)
    search_paths.append(module_dir)
    if toolkit_root:
        search_paths.append(toolkit_root)

    for path in reversed(search_paths):
        if path not in sys.path:
            sys.path.insert(0, path)


def _resolve_watch_files(module):
    """Ermittelt Watch-Dateien aus geladenen Projektmodulen und optionalen WATCH_FILES."""
    base_dir = os.path.dirname(monitored_file)
    resolved = [_normalize_path(monitored_file)]
    resolved.extend(_collect_loaded_module_watch_files())
    for entry in getattr(module, "WATCH_FILES", []) or []:
        if not entry:
            continue
        path = entry if os.path.isabs(entry) else os.path.join(base_dir, entry)
        resolved.append(_normalize_path(path))
    # Reihenfolge behalten, Duplikate entfernen.
    unique = []
    seen = set()
    for path in resolved:
        if path not in seen:
            unique.append(path)
            seen.add(path)
    return unique


def _refresh_last_mtimes():
    """Aktualisiert die gespeicherten Änderungszeiten aller beobachteten Dateien."""
    global last_mtimes
    last_mtimes = {}
    for path in watch_files or [_normalize_path(monitored_file)]:
        try:
            last_mtimes[path] = os.path.getmtime(path)
        except OSError:
            last_mtimes[path] = 0


def _remove_module_from_parent_packages(module_name: str):
    """Entfernt gecachte Submodul-Attribute aus Parent-Packages."""
    parts = module_name.split(".")
    for index in range(1, len(parts)):
        parent_name = ".".join(parts[:index])
        child_name = parts[index]
        parent_module = sys.modules.get(parent_name)
        if parent_module is None or not hasattr(parent_module, child_name):
            continue
        try:
            delattr(parent_module, child_name)
        except Exception:
            pass


def _remove_cached_bytecode(path: str):
    """Entfernt vorhandene .pyc-Dateien des Moduls, damit garantiert Source geladen wird."""
    cache_dir = os.path.join(os.path.dirname(path), "__pycache__")
    if not os.path.isdir(cache_dir):
        return

    module_stem = os.path.splitext(os.path.basename(path))[0] + "."
    for entry in os.listdir(cache_dir):
        if not entry.startswith(module_stem) or not entry.endswith(".pyc"):
            continue
        pyc_path = os.path.join(cache_dir, entry)
        try:
            os.remove(pyc_path)
        except OSError:
            pass


def _invalidate_watch_modules():
    """Entfernt Watch-Module inkl. Parent-Cache und Bytecode-Cache."""
    watch_set = set(watch_files)
    removed = []
    for path in watch_set:
        _remove_cached_bytecode(path)
    for name, module in list(sys.modules.items()):
        path = _module_file_path(module)
        if path and path in watch_set and path != _normalize_path(monitored_file):
            try:
                del sys.modules[name]
                _remove_module_from_parent_packages(name)
                removed.append(name)
            except KeyError:
                pass
    if removed:
        App.Console.PrintMessage("Invalidierte Module: " + ", ".join(sorted(removed)) + "\n")

def _is_doc_open(name: str) -> bool:
    """Prüft, ob ein Dokument mit dem angegebenen Namen noch geöffnet ist."""
    try:
        return bool(name) and name in App.listDocuments()
    except Exception:
        return False


def check_for_update():
    """
    Wird vom QTimer periodisch aufgerufen. Prüft, ob die Datei geändert wurde
    und löst bei Bedarf die Aktualisierung aus.
    """
    global monitored_file, doc_name

    if not monitored_file:
        stop_monitoring()
        return

    if not _is_current_instance():
        stop_monitoring("Veraltete Live-Reload-Instanz erkannt – beende alten Watcher.\n")
        return

    try:
        # Beende, wenn das zugehörige Dokument geschlossen wurde
        if not _is_doc_open(doc_name):
            App.Console.PrintMessage("Überwachtes Dokument wurde geschlossen – Live-Reloading wird beendet.\n")
            stop_monitoring()
            return

        if not os.path.exists(monitored_file):
            App.Console.PrintError(f"✗ Fehler: Überwachte Datei '{monitored_file}' nicht mehr gefunden.\n")
            stop_monitoring()
            return

        changed_files = []
        for path in watch_files or [_normalize_path(monitored_file)]:
            if not os.path.exists(path):
                App.Console.PrintWarning(f"⚠ Beobachtete Datei fehlt: '{path}'\n")
                continue
            current_mtime = os.path.getmtime(path)
            if current_mtime > last_mtimes.get(path, 0):
                changed_files.append(path)

        if changed_files:
            names = ", ".join(os.path.basename(path) for path in changed_files)
            App.Console.PrintMessage(f"→ Änderung in {names} erkannt. Lade neu...\n")
            reload_geometry()
    except Exception as e:
        App.Console.PrintError(f"✗ Fehler beim Überwachen der Datei: {e}\n")
        stop_monitoring()

def reload_geometry():
    """
    Löscht alle Objekte im Dokument, lädt das Geometrie-Modul neu
    und lässt es die Szene neu aufbauen.
    """
    global monitored_file, doc_name, watch_files

    if not _is_current_instance():
        stop_monitoring("Veraltete Live-Reload-Instanz erkannt – Reload wird beendet.\n")
        return

    # Greife auf das beim Start ermittelte Dokument zu
    if not _is_doc_open(doc_name):
        App.Console.PrintMessage("Zieldokument ist nicht mehr geöffnet – Live-Reloading wird beendet.\n")
        stop_monitoring()
        return

    doc = App.getDocument(doc_name)

    try:
        # 1. Alle vorhandenen Objekte im Dokument radikal löschen
        App.Console.PrintMessage("Lösche alte Objekte...\n")
        # Wichtig: Wir erstellen eine Kopie der Liste, da wir sie während der Iteration verändern
        for obj in doc.Objects[:]:
            try:
                if obj and hasattr(obj, 'Name') and obj.Name:
                    doc.removeObject(obj.Name)
            except Exception as e:
                obj_name = getattr(obj, 'Name', 'Unknown') if obj else 'None'
                App.Console.PrintWarning(f"Konnte Objekt '{obj_name}' nicht entfernen: {e}\n")

        # 2. Module aus WATCH_FILES invalidieren und Geometrie-Modul neu laden
        _ensure_import_paths(monitored_file)

        module_name = os.path.splitext(os.path.basename(monitored_file))[0]
        importlib.invalidate_caches()
        _invalidate_watch_modules()

        if module_name in sys.modules:
            geometry_module = importlib.reload(sys.modules[module_name])
        else:
            geometry_module = importlib.import_module(module_name)

        watch_files = _resolve_watch_files(geometry_module)
        _refresh_last_mtimes()
        App.Console.PrintMessage("Beobachtete Dateien:\n" + "\n".join(f"  - {path}" for path in watch_files) + "\n")

        # 3. Geometrie-Funktion aufrufen
        if hasattr(geometry_module, 'create_geometry'):
            App.Console.PrintMessage("Führe 'create_geometry' aus...\n")
            geometry_module.create_geometry(doc)
            doc.recompute()
            try:
                # Versuche, die Ansicht des betroffenen Dokuments zu fitten
                if Gui.ActiveDocument and Gui.ActiveDocument.Document.Name == doc_name:
                    Gui.SendMsgToActiveView("ViewFit")
            except Exception:
                pass
            App.Console.PrintMessage("✓ Geometrie erfolgreich aktualisiert.\n")
        else:
            App.Console.PrintError(f"✗ Fehler: Funktion 'create_geometry(doc)' nicht in '{os.path.basename(monitored_file)}' gefunden.\n")

    except Exception as e:
        App.Console.PrintError(f"✗ Fehler beim Neuladen der Geometrie: {e}\n")
        import traceback
        traceback.print_exc(file=sys.stderr)


def stop_monitoring(reason=None):
    """Stoppt den Timer und räumt den Zustand auf."""
    global timer, monitored_file, doc_name, watch_files, last_mtimes, instance_token
    current_timer = timer
    current_token = instance_token

    try:
        _stop_timer(current_timer, check_for_update)
    finally:
        timer = None
        monitored_file = None
        doc_name = None
        watch_files = []
        last_mtimes = {}
        instance_token = None
        if current_token is not None:
            _clear_shared_state(current_token)
        if reason:
            App.Console.PrintMessage(reason)
        App.Console.PrintMessage("Live-Reloading gestoppt.\n")

def run():
    """Hauptfunktion des Makros."""
    global monitored_file, timer, doc_name, watch_files, instance_token, project_root

    # Prüfen, ob ein Dokument geöffnet ist
    if not App.ActiveDocument:
        msg_box = QMessageBox()
        msg_box.setText("Bitte erstelle oder öffne ein Dokument, bevor du das Makro startest.")
        msg_box.setWindowTitle("Kein Dokument")
        msg_box.exec_()
        return

    # Vorherige Überwachung zuverlässig beenden, falls Makro erneut gestartet wird
    if timer is not None:
        stop_monitoring()

    # Dateiauswahldialog
    path, _ = QFileDialog.getOpenFileName(
        None,
        "Wähle eine Geometrie-Python-Datei zum Überwachen",
        "", # Startverzeichnis
        "Python Files (*.py)"
    )

    if not path:
        App.Console.PrintMessage("Keine Datei ausgewählt. Makro wird beendet.\n")
        return

    _stop_previous_instance()

    monitored_file = path
    monitored_file = _normalize_path(monitored_file)
    project_root = _find_project_root(monitored_file)
    watch_files = [monitored_file]
    doc_name = App.ActiveDocument.Name  # Dokument fixieren, in das geladen wird
    instance_token = object()

    if timer is None:
        timer = QTimer()
        timer.setInterval(2000)  # Millisekunden
        timer.timeout.connect(check_for_update)

    _publish_instance_state()

    # Ersten Ladevorgang sofort ausführen
    reload_geometry()

    # Timer starten, der alle 2 Sekunden prüft
    if timer is not None:
        timer.start()
        _publish_instance_state()

    App.Console.PrintMessage(f"Überwache '{os.path.basename(monitored_file)}' auf Änderungen...\n")


# --- Makro starten ---
if __name__ == "__main__":
    run()
