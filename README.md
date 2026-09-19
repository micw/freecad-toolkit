# FreeCAD Toolkit

Werkzeuge und wiederverwendbare Bausteine für die programmatische Erzeugung
und den Export parametrischer FreeCAD-Modelle.

Ein Geometrieskript stellt eine Funktion `create_geometry(doc)` bereit. Das
Modell kann mit dem Live-Reload-Makro in der FreeCAD-GUI entwickelt und mit
dem Headless-Runner geprüft oder exportiert werden.

## Voraussetzungen

- FreeCAD mit Python-, Part-, Mesh- und optional TechDraw-Unterstützung
- ursprünglich mit FreeCAD 1.0.2 entwickelt
- Version 0.3.0 mit FreeCAD 1.1.3 getestet

## Verwendung als Library

Das öffentliche Python-Paket heißt `freecad_toolkit`:

```python
from freecad_toolkit import (
    ModuleResult,
    PreviewAssembly,
    Shape2D,
    merge_params,
)
```

Der bisherige Import über `lib` bleibt in Version 0.3.0 kompatibel, ist aber
nicht mehr die empfohlene öffentliche Schnittstelle.

Das Toolkit kann als Git-Submodul in ein CAD-Projekt eingebunden werden. Der
Headless-Runner fügt Toolkit, Projektwurzel und Verzeichnis des
Geometrieskripts automatisch zum Python-Suchpfad hinzu. Die Projektwurzel wird
an `.git` oder `pyproject.toml` erkannt und kann explizit gesetzt werden:

```bash
tools/freecad-toolkit/freecad_headless.py \
  test \
  --project-root "$PWD" \
  enclosure/cad/geometry_example.py
```

Dadurch können Geometrieskripte sowohl `freecad_toolkit` als auch eigene
Projektmodule importieren.

## Headless-Runner

```bash
./freecad_headless.py test geometry_example.py
./freecad_headless.py export-stl geometry_example.py output.stl
./freecad_headless.py export-3mf geometry_example.py output.3mf
./freecad_headless.py export-stp geometry_example.py output.stp
```

Der Testmodus zeigt Bounding Box, Volumen, Oberfläche, projizierte Fläche,
Anzahl der Vertices und Anzahl der Faces an. Import- und Geometriefehler führen
zu einem Exit-Code ungleich null, sodass der Runner in Makefiles und CI-Jobs
verwendet werden kann.

Der Integrationstest benötigt FreeCAD und prüft öffentliche sowie
projektinterne Imports und den Exit-Code bei Fehlern:

```bash
./tests/test_external_project.sh
```

Ein minimales Geometrieskript:

```python
import FreeCAD as App
import Part

from freecad_toolkit import PreviewAssembly


def create_geometry(doc):
    assembly = PreviewAssembly()
    assembly.group("Example").add(
        Part.makeBox(10, 10, 10, App.Vector(-5, -5, -5))
    )
    return assembly.build(doc)
```

### Export vorbereiten

Ein Modell kann optional `prepare_export(doc, command)` implementieren. Der
Runner ruft diese Funktion vor STL- und 3MF-Exporten auf. So können
Vorschauobjekte ersetzt, Teile angeordnet oder Exportobjekte zusammengebaut
werden, ohne die interaktive Ansicht zu verändern.

```python
def prepare_export(doc, command):
    # Exportdarstellung erzeugen oder anpassen
    pass
```

### Exportobjekte auswählen

Objekte mit der Bool-Property `_ExportEnabled = False` werden beim STL- und
3MF-Export ausgelassen. Objekte ohne diese Property werden exportiert.

### Mehrere Parts gruppieren

Objekte mit demselben Stringwert in `_PrintObject` werden im gruppierten
3MF-Export als Parts eines gemeinsamen Slicer-Objekts ausgegeben. Ohne diese
Property wird jedes FreeCAD-Objekt ein eigenes Slicer-Objekt.

Der 3MF-Writer erhält Objekt- und Partnamen und erzeugt eine Struktur, die von
BambuStudio und OrcaSlicer als Projekt mit getrennten Objekten gelesen werden
kann. Jedes Slicer-Objekt wird dabei als Einheit auf `Z = 0` gesetzt.

## Geometrie-Helfer

- `Shape2D`: verkettbare Linien, Bögen und runde Aussparungen in der XY-Ebene
- `ModuleResult`: semantisch gruppierte, transformierbare Modulgeometrie
- `PreviewAssembly`: Aufbau benannter FreeCAD-Objekte mit Farbe, Transparenz
  und Exportstatus
- `merge_params`: Zusammenführen von Defaults und Overrides

## Live-Reload in FreeCAD

`FreeCAD-Macros/LiveGeometryReloader.FCMacro.py` wird als FreeCAD-Makro
ausgeführt. Nach Auswahl eines Geometrieskripts wird dessen
`create_geometry(doc)` bei Dateiänderungen erneut ausgeführt.

Das Makro erkennt die Projektwurzel aus der ausgewählten Geometriedatei und
nimmt den Toolkit-Pfad in den Python-Suchpfad auf. Wird das Makro aus dem
Toolkit-Verzeichnis herauskopiert statt verlinkt, muss der Installationspfad
über `FREECAD_TOOLKIT_ROOT` gesetzt werden.

Das Makro beobachtet auch geladene Python-Module innerhalb des Projekts.
Weitere Dateien können vom Geometrieskript über `WATCH_FILES` angegeben werden:

```python
WATCH_FILES = ["components/support.py", "config.py"]
```
