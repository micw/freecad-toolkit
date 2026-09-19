# Changelog

## 0.3.0

- Öffentliches Python-Paket `freecad_toolkit` ergänzt; bestehende `lib`-Imports
  bleiben kompatibel.
- Packaging-Metadaten in `pyproject.toml` ergänzt.
- Projektwurzel und Verzeichnis des Geometrieskripts werden vom
  Headless-Runner in den Python-Suchpfad aufgenommen.
- Option `--project-root` für explizite Projektpfade ergänzt.
- Unerwartete Import- und Geometriefehler liefern jetzt einen Exit-Code ungleich
  null.
- Integrationstest für externe CAD-Projekte ergänzt.
- Live-Reload erkennt die Projektwurzel anhand der ausgewählten Geometriedatei
  und unterstützt `FREECAD_TOOLKIT_ROOT` für externe Installationen.

## 0.2.0

- Wiederverwendbare Geometriebausteine mit `Shape2D`, `ModuleResult` und
  `PreviewAssembly` ergänzt.
- STEP-Export ergänzt.
- Gruppierten, benannten 3MF-Export ergänzt.
- `_ExportEnabled` und `_PrintObject` zur Exportsteuerung ergänzt.
- Optionalen `prepare_export()`-Hook ergänzt.
- Testausgabe um Oberfläche und projizierte Fläche erweitert.
- Live-Reload auf importierte Projektmodule und `WATCH_FILES` erweitert.
- Verwaltung paralleler bzw. veralteter Live-Reload-Instanzen verbessert.

## 0.1.0

- Headless-Tests für FreeCAD-Geometrieskripte.
- STL- und 3MF-Export.
- Live-Reload-Makro für die FreeCAD-GUI.
