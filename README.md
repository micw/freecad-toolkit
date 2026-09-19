# FreeCAD Toolkit

Werkzeuge und wiederverwendbare Bausteine für die programmatische Erzeugung
und den Export parametrischer FreeCAD-Modelle.

Ein Geometrieskript stellt eine Funktion `create_geometry(doc)` bereit. Das
Modell kann mit dem Live-Reload-Makro in der FreeCAD-GUI entwickelt und mit
dem Headless-Runner geprüft oder exportiert werden.

## Voraussetzungen

- FreeCAD mit Python-, Part-, Mesh- und optional TechDraw-Unterstützung
- ursprünglich mit FreeCAD 1.0.2 entwickelt
- Version 0.2.0 mit FreeCAD 1.1.3 getestet

## Headless-Runner

```bash
./freecad_headless.py test geometry_example.py
./freecad_headless.py export-stl geometry_example.py output.stl
./freecad_headless.py export-3mf geometry_example.py output.3mf
./freecad_headless.py export-stp geometry_example.py output.stp
```

Der Testmodus zeigt Bounding Box, Volumen, Oberfläche, projizierte Fläche,
Anzahl der Vertices und Anzahl der Faces an.

Ein minimales Geometrieskript:

```python
import FreeCAD as App
import Part


def create_geometry(doc):
    shape = Part.makeBox(10, 10, 10, App.Vector(-5, -5, -5))
    obj = doc.addObject("Part::Feature", "Example")
    obj.Shape = shape
    return [obj]
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

Das Paket `lib` stellt gemeinsame Bausteine bereit:

```python
from lib.freecad_common import (
    ModuleResult,
    PreviewAssembly,
    Shape2D,
    merge_params,
)
```

- `Shape2D`: verkettbare Linien, Bögen und runde Aussparungen in der XY-Ebene
- `ModuleResult`: semantisch gruppierte, transformierbare Modulgeometrie
- `PreviewAssembly`: Aufbau benannter FreeCAD-Objekte mit Farbe, Transparenz
  und Exportstatus
- `merge_params`: Zusammenführen von Defaults und Overrides

Das Repository muss dafür im Python-Suchpfad liegen. Der Headless-Runner und
das Live-Reload-Makro sorgen bei normaler Verwendung dafür, dass Projekt- und
Modulpfade importierbar sind.

## Live-Reload in FreeCAD

`FreeCAD-Macros/LiveGeometryReloader.FCMacro.py` wird als FreeCAD-Makro
ausgeführt. Nach Auswahl eines Geometrieskripts wird dessen
`create_geometry(doc)` bei Dateiänderungen erneut ausgeführt.

Ab Version 0.2.0 beobachtet das Makro auch geladene Python-Module innerhalb
des Projekts. Weitere Dateien können vom Geometrieskript über `WATCH_FILES`
angegeben werden:

```python
WATCH_FILES = ["components/support.py", "config.py"]
```
