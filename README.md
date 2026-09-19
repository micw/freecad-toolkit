# FreeCAD Toolkit

Werkzeuge für die programmatische Erzeugung und den Export parametrischer
FreeCAD-Modelle.

Ein Geometrieskript stellt eine Funktion `create_geometry(doc)` bereit. Das
Modell kann mit dem Live-Reload-Makro in der FreeCAD-GUI entwickelt und mit
dem Headless-Runner geprüft oder exportiert werden.

## Voraussetzungen

- FreeCAD mit Python- und Part-Unterstützung
- getestet mit FreeCAD 1.0.2

## Headless-Runner

```bash
./freecad_headless.py test geometry_example.py
./freecad_headless.py export-stl geometry_example.py output.stl
./freecad_headless.py export-3mf geometry_example.py output.3mf
```

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

## Live-Reload in FreeCAD

`FreeCAD-Macros/LiveGeometryReloader.FCMacro.py` wird als FreeCAD-Makro
ausgeführt. Nach Auswahl eines Geometrieskripts wird dessen
`create_geometry(doc)` bei Dateiänderungen erneut ausgeführt.
