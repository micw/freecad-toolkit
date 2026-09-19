#!/usr/bin/FreeCADCmd
# freecad_headless.py
import sys
import os
import re
import traceback
import importlib.util
import FreeCAD as App
import Mesh
import Part

AREA_UNIT_SWITCH_THRESHOLD = 1000.0
VOLUME_UNIT_SWITCH_THRESHOLD = 10000.0
PROJECTED_FACE_AREA_TOLERANCE = 1e-4
PROJECTED_AREA_DEFLECTION = 0.2
TOOLKIT_ROOT = os.path.abspath(os.path.dirname(__file__))
PROJECT_ROOT_MARKERS = (".git", "pyproject.toml")


def find_project_root(start_path):
    """Find the nearest project root above a geometry script."""
    current = os.path.abspath(start_path)
    if not os.path.isdir(current):
        current = os.path.dirname(current)

    while True:
        if any(os.path.exists(os.path.join(current, marker)) for marker in PROJECT_ROOT_MARKERS):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return os.path.abspath(start_path if os.path.isdir(start_path) else os.path.dirname(start_path))
        current = parent


def configure_import_paths(geometry_script_path, project_root=None):
    """Make toolkit, geometry directory and project root importable."""
    geometry_dir = os.path.dirname(os.path.abspath(geometry_script_path))
    resolved_project_root = os.path.abspath(project_root) if project_root else find_project_root(geometry_dir)
    if not os.path.isdir(resolved_project_root):
        raise ValueError(f"Projektwurzel existiert nicht: {resolved_project_root}")

    # Insert in reverse priority order. The project takes precedence over the
    # geometry directory, while the toolkit remains available as a fallback.
    for path in (TOOLKIT_ROOT, geometry_dir, resolved_project_root):
        if path in sys.path:
            sys.path.remove(path)
        sys.path.insert(0, path)
    return resolved_project_root


def format_area(area_mm2):
    if abs(area_mm2) >= AREA_UNIT_SWITCH_THRESHOLD:
        return f"{area_mm2 / 100.0:.2f} cm²"
    return f"{area_mm2:.2f} mm²"


def format_volume(volume_mm3):
    if abs(volume_mm3) >= VOLUME_UNIT_SWITCH_THRESHOLD:
        return f"{volume_mm3 / 1000.0:.2f} cm³"
    return f"{volume_mm3:.2f} mm³"


def areas_match(left_area, right_area):
    tolerance = max(PROJECTED_FACE_AREA_TOLERANCE, max(abs(left_area), abs(right_area)) * 1e-6)
    return abs(left_area - right_area) <= tolerance


def is_contained_face(inner_face, outer_face):
    if outer_face.Area <= inner_face.Area:
        return False

    try:
        common_shape = outer_face.common(inner_face)
    except Exception:
        return False

    return areas_match(common_shape.Area, inner_face.Area)


def get_tessellated_top_projected_area(shape):
    try:
        points, facets = shape.tessellate(PROJECTED_AREA_DEFLECTION)
    except Exception:
        return None

    total_area = 0.0
    for first_index, second_index, third_index in facets:
        first_point = points[first_index]
        second_point = points[second_index]
        third_point = points[third_index]

        projected_cross_z = (
            (second_point.x - first_point.x) * (third_point.y - first_point.y)
            - (second_point.y - first_point.y) * (third_point.x - first_point.x)
        )
        if projected_cross_z > 0.0:
            total_area += 0.5 * projected_cross_z

    return total_area


def get_top_projected_area(shape):
    try:
        import TechDraw
    except ImportError:
        return None

    try:
        projected_edges = TechDraw.projectEx(shape, App.Vector(0, 0, 1))
    except Exception:
        return None

    edge_walker = getattr(TechDraw, "EdgeWalker", None) or getattr(TechDraw, "edgeWalker", None)
    if edge_walker is None:
        return None

    flat_projected_edges = []
    for projected_shape in projected_edges:
        flat_projected_edges.extend(projected_shape.Edges)

    if not flat_projected_edges:
        return get_tessellated_top_projected_area(shape)

    projected_wires = edge_walker(flat_projected_edges, True)
    if projected_wires is None:
        return get_tessellated_top_projected_area(shape)

    projected_faces = []
    for wire in projected_wires:
        candidate_wire = wire
        if not hasattr(candidate_wire, "isClosed"):
            try:
                candidate_wire = Part.Wire(wire)
            except Exception:
                continue

        if not candidate_wire.isClosed():
            continue

        try:
            face = Part.Face(candidate_wire)
        except Exception:
            try:
                face = Part.Face([candidate_wire])
            except Exception:
                continue

        if face.Area <= PROJECTED_FACE_AREA_TOLERANCE:
            continue

        if any(
            areas_match(existing_face.Area, face.Area)
            and areas_match(existing_face.common(face).Area, face.Area)
            for existing_face in projected_faces
        ):
            continue

        projected_faces.append(face)

    if not projected_faces:
        return get_tessellated_top_projected_area(shape)

    total_area = 0.0
    sorted_faces = sorted(projected_faces, key=lambda face: face.Area, reverse=True)
    for index, face in enumerate(sorted_faces):
        containing_faces = sum(
            1
            for outer_face in sorted_faces[:index]
            if is_contained_face(face, outer_face)
        )
        if containing_faces % 2 == 0:
            total_area += face.Area
        else:
            total_area -= face.Area

    total_area = max(total_area, 0.0)
    if total_area <= PROJECTED_FACE_AREA_TOLERANCE:
        return get_tessellated_top_projected_area(shape)
    return total_area

# ---------------------------------------------------------------------------
# 3MF-Export mit Objektgruppierung und Namen (Bambu/Orca-kompatibel)
# ---------------------------------------------------------------------------
#
# FreeCADs Mesh.export erzeugt ein "flaches" 3MF: anonyme <object>-Tags ohne
# Namen und ohne Gruppierungs-Metadaten. Im Slicer führt das zu generischen
# Namen ("Object_1/2/3") und zur Rückfrage "ein Objekt oder mehrere?".
#
# write_grouped_3mf() baut stattdessen die von Bambu/Orca erwartete Struktur:
#   - je Part eine eigene /3D/Objects/Object_N.model (Production Extension)
#   - im Root <components>-Wrapper je Slicer-Objekt
#   - Metadata/model_settings.config mit den Objekt-/Part-Namen
#
# Gruppierung: Objekte mit gleichem _PrintObject-String werden zu EINEM
# Slicer-Objekt (Name = _PrintObject) zusammengefasst, ihre Bestandteile
# werden Parts (Part-Name = Label). Ohne _PrintObject ist jedes Objekt ein
# eigenständiges Slicer-Objekt (Name = Label, ein Part).
#
# Alle Container-Transforms bleiben Identity: die Objekt-Platzierung wird in
# die Vertices "gebacken". Das vermeidet jede Mehrdeutigkeit, wie ein Slicer
# Component-/Item-/Part-Matrizen kombiniert.

_IDENT12 = "1 0 0 0 1 0 0 0 1 0 0 0"
_IDENT16 = "1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"
_VERTEX_RE = re.compile(
    r'<vertex\s+x="(?P<x>[^"]+)"\s+y="(?P<y>[^"]+)"\s+z="(?P<z>[^"]+)"\s*/>'
)


def _xml_attr(value):
    """Escaped einen Text für die Verwendung in einem XML-Attributwert."""
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _bake_vertices(mesh_xml, transform):
    """Wendet eine 3MF-Transform (12 Zahlen, 4x3) auf alle <vertex> an.

    Liefert das Mesh in Weltkoordinaten zurück, sodass alle Container-
    Transforms Identity bleiben können.
    """
    try:
        nums = [float(v) for v in transform.split()]
    except ValueError:
        return mesh_xml
    if len(nums) != 12:
        return mesh_xml
    a, b, c, d, e, f, g, h, i, tx, ty, tz = nums
    if nums == [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0]:
        return mesh_xml

    def repl(m):
        x = float(m.group("x"))
        y = float(m.group("y"))
        z = float(m.group("z"))
        nx = a * x + d * y + g * z + tx
        ny = b * x + e * y + h * z + ty
        nz = c * x + f * y + i * z + tz
        return '<vertex x="%.9g" y="%.9g" z="%.9g" />' % (nx, ny, nz)

    return _VERTEX_RE.sub(repl, mesh_xml)


def _min_vertex_z(mesh_xml):
    """Kleinste Z-Koordinate aller <vertex> in mesh_xml (oder None)."""
    zmin = None
    for m in _VERTEX_RE.finditer(mesh_xml):
        z = float(m.group("z"))
        if zmin is None or z < zmin:
            zmin = z
    return zmin


def _translate_z(mesh_xml, dz):
    """Verschiebt alle <vertex> um dz in Z."""
    def repl(m):
        return '<vertex x="%s" y="%s" z="%.9g" />' % (
            m.group("x"), m.group("y"), float(m.group("z")) + dz
        )
    return _VERTEX_RE.sub(repl, mesh_xml)


def write_grouped_3mf(export_objects, output_path):
    """Schreibt export_objects als gruppiertes, benanntes 3MF (Bambu/Orca)."""
    import zipfile
    import tempfile
    import uuid

    def new_uuid():
        return str(uuid.uuid4())

    # 1. Meshes je Objekt einsammeln und nach _PrintObject gruppieren
    groups = []        # geordnete Liste: {"name": str, "parts": [{"name", "mesh"}]}
    group_index = {}   # Gruppen-Key -> Position in groups
    with tempfile.TemporaryDirectory() as tmp:
        tmp_3mf = os.path.join(tmp, "part.3mf")
        for obj in export_objects:
            Mesh.export([obj], tmp_3mf)
            with zipfile.ZipFile(tmp_3mf) as zf:
                model_xml = zf.read("3D/3dmodel.model").decode("utf-8")
            m_start = model_xml.find("<mesh>")
            m_end = model_xml.find("</mesh>")
            if m_start == -1 or m_end == -1:
                App.Console.PrintWarning(
                    f"Objekt '{getattr(obj, 'Label', obj.Name)}' liefert kein Mesh und wird übersprungen.\n"
                )
                continue
            mesh_xml = model_xml[m_start:m_end + len("</mesh>")]
            item = re.search(r'<item\b[^>]*\btransform="([^"]*)"', model_xml)
            transform = item.group(1) if item else _IDENT12
            mesh_xml = _bake_vertices(mesh_xml, transform)

            label = getattr(obj, "Label", None) or obj.Name
            group_name = (getattr(obj, "_PrintObject", "") or "").strip()
            if group_name:
                key = ("group", group_name)
                display = group_name
            else:
                key = ("solo", id(obj))
                display = label
            if key not in group_index:
                group_index[key] = len(groups)
                groups.append({"name": display, "parts": []})
            groups[group_index[key]]["parts"].append({"name": label, "mesh": mesh_xml})

    if not groups:
        raise RuntimeError("Keine Mesh-Objekte zum Exportieren gefunden.")

    # 1b. Jedes Slicer-Objekt auf die Druckplatte absetzen (zmin -> 0).
    # Andernfalls erkennt Orcas Model::looks_like_multipart_object() mehrere
    # Einzelobjekte mit unterschiedlicher Z-Unterkante als zusammengehörig und
    # zeigt beim Import die "ein/mehrere Objekte?"-Rückfrage. Gruppen werden als
    # Einheit verschoben, damit die Parts ihre Relativlage behalten.
    for grp in groups:
        zmins = [_min_vertex_z(p["mesh"]) for p in grp["parts"]]
        zmins = [z for z in zmins if z is not None]
        if not zmins:
            continue
        dz = -min(zmins)
        if abs(dz) > 1e-9:
            for part in grp["parts"]:
                part["mesh"] = _translate_z(part["mesh"], dz)

    # 2. IDs vergeben (paketweit eindeutig) und Dateistruktur planen
    next_id = 1
    part_files = []   # (path, inner_id, mesh_xml)
    objects_meta = []  # (wrapper_id, display_name, [(inner_id, part_name, path)])
    for grp in groups:
        part_refs = []
        for part in grp["parts"]:
            inner_id = next_id
            next_id += 1
            path = "/3D/Objects/Object_%d.model" % len(part_files)
            part_files.append((path, inner_id, part["mesh"]))
            part_refs.append((inner_id, part["name"], path))
        wrapper_id = next_id
        next_id += 1
        objects_meta.append((wrapper_id, grp["name"], part_refs))

    # 3. Wurzel-Modell (Production Extension, Component-Wrapper)
    ns = (
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
        'xmlns:BambuStudio="http://schemas.bambulab.com/package/2021" '
        'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" '
        'requiredextensions="p"'
    )
    # Bambu/Orca laden ein 3MF nur dann ohne "ein/mehrere Objekte?"-Dialog als
    # Projekt, wenn die Application-Metadaten mit "BambuStudio-" beginnen
    # (bbs_3mf.cpp: m_is_bbl_3mf wird sonst nicht gesetzt). Die echte Herkunft
    # halten wir separat in der Designer-Metadaten fest.
    root = ['<?xml version="1.0" encoding="UTF-8"?>',
            '<model unit="millimeter" xml:lang="en-US" %s>' % ns,
            ' <metadata name="Application">BambuStudio-1.3.9.4</metadata>',
            ' <metadata name="BambuStudio:3mfVersion">1</metadata>',
            ' <metadata name="Designer">FreeCAD headless (freecad-toolkit export)</metadata>',
            ' <resources>']
    for wrapper_id, _name, part_refs in objects_meta:
        root.append('  <object id="%d" p:UUID="%s" type="model">' % (wrapper_id, new_uuid()))
        root.append('   <components>')
        for inner_id, _pn, path in part_refs:
            root.append(
                '    <component p:path="%s" objectid="%d" p:UUID="%s" transform="%s"/>'
                % (path, inner_id, new_uuid(), _IDENT12)
            )
        root.append('   </components>')
        root.append('  </object>')
    root.append(' </resources>')
    root.append(' <build p:UUID="%s">' % new_uuid())
    for wrapper_id, _name, _part_refs in objects_meta:
        root.append(
            '  <item objectid="%d" p:UUID="%s" transform="%s" printable="1"/>'
            % (wrapper_id, new_uuid(), _IDENT12)
        )
    root.append(' </build>')
    root.append('</model>')

    def object_file(inner_id, mesh_xml):
        return "\n".join([
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<model unit="millimeter" xml:lang="en-US" %s>' % ns,
            ' <metadata name="BambuStudio:3mfVersion">1</metadata>',
            ' <resources>',
            '  <object id="%d" p:UUID="%s" type="model">' % (inner_id, new_uuid()),
            '   %s' % mesh_xml,
            '  </object>',
            ' </resources>',
            ' <build/>',
            '</model>',
        ])

    # 4. Relationships für die Part-Dateien
    rels = ['<?xml version="1.0" encoding="UTF-8"?>',
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
    for n, (path, _inner_id, _mesh) in enumerate(part_files, 1):
        rels.append(
            ' <Relationship Target="%s" Id="rel-%d" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>' % (path, n)
        )
    rels.append('</Relationships>')

    # 5. model_settings.config (Namen + Plate)
    cfg = ['<?xml version="1.0" encoding="UTF-8"?>', '<config>']
    for wrapper_id, name, part_refs in objects_meta:
        cfg.append('  <object id="%d">' % wrapper_id)
        cfg.append('    <metadata key="name" value="%s"/>' % _xml_attr(name))
        cfg.append('    <metadata key="extruder" value="1"/>')
        for inner_id, part_name, _path in part_refs:
            cfg.append('    <part id="%d" subtype="normal_part">' % inner_id)
            cfg.append('      <metadata key="name" value="%s"/>' % _xml_attr(part_name))
            cfg.append('      <metadata key="matrix" value="%s"/>' % _IDENT16)
            cfg.append('    </part>')
        cfg.append('  </object>')
    cfg.append('  <plate>')
    cfg.append('    <metadata key="plater_id" value="1"/>')
    cfg.append('    <metadata key="plater_name" value=""/>')
    cfg.append('    <metadata key="locked" value="false"/>')
    for wrapper_id, _name, _part_refs in objects_meta:
        cfg.append('    <model_instance>')
        cfg.append('      <metadata key="object_id" value="%d"/>' % wrapper_id)
        cfg.append('      <metadata key="instance_id" value="0"/>')
        cfg.append('    </model_instance>')
    cfg.append('  </plate>')
    cfg.append('</config>')

    content_types = "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
        ' <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
        ' <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>',
        '</Types>',
    ])
    root_rels = "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
        ' <Relationship Target="/3D/3dmodel.model" Id="rel-1" '
        'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>',
        '</Relationships>',
    ])

    # 6. Container schreiben
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("3D/3dmodel.model", "\n".join(root))
        for path, inner_id, mesh_xml in part_files:
            zf.writestr(path.lstrip("/"), object_file(inner_id, mesh_xml))
        zf.writestr("3D/_rels/3dmodel.model.rels", "\n".join(rels))
        zf.writestr("Metadata/model_settings.config", "\n".join(cfg))

    return len(objects_meta), len(part_files)


def process_geometry(geometry_script_path, command, output_filename=None, project_root=None):
    """
    Lädt ein Geometrie-Skript und führt verschiedene Operationen aus.
    
    Args:
        geometry_script_path: Pfad zum Geometrie-Skript
        command: 'test', 'export-stl', 'export-3mf' oder 'export-stp'
        output_filename: Name der Ausgabedatei (für export)
        project_root: Optionale Projektwurzel für projektinterne Imports
    """
    geometry_script_path = os.path.abspath(geometry_script_path)
    if not os.path.exists(geometry_script_path):
        App.Console.PrintError(f"Fehler: Geometrie-Skript nicht gefunden unter: {geometry_script_path}\n")
        sys.exit(1)

    configure_import_paths(geometry_script_path, project_root)

    # Dokument erstellen
    doc = App.newDocument("ProcessDoc")

    try:
        # Lade das Geometrie-Modul dynamisch
        spec = importlib.util.spec_from_file_location("geometry_module", geometry_script_path)
        geometry_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(geometry_module)

        # Prüfen, ob die create_geometry Funktion existiert
        if not hasattr(geometry_module, 'create_geometry'):
            App.Console.PrintError(f"Fehler: Die Funktion 'create_geometry(doc)' ist im Skript '{geometry_script_path}' nicht definiert.\n")
            sys.exit(1)

        # Geometrie erstellen lassen
        App.Console.PrintMessage(f"Erzeuge Geometrie aus '{os.path.basename(geometry_script_path)}'...\n")
        geometry_module.create_geometry(doc)

        # Dokument neu berechnen, um die Geometrie zu finalisieren
        doc.recompute()

        if command in {'export-stl', 'export-3mf'} and hasattr(geometry_module, 'prepare_export'):
            geometry_module.prepare_export(doc, command)
            doc.recompute()

        # Sicherstellen, dass Objekte vorhanden sind
        objects = doc.Objects
        if not objects:
            App.Console.PrintWarning("Warnung: Das Geometrie-Skript hat keine Objekte zum Dokument hinzugefügt.\n")
            return

        # Führe Befehl aus
        if command == 'test':
            # Test-Modus: Zeige nur Informationen
            App.Console.PrintMessage(f"\n=== TEST MODE ===\n")
            App.Console.PrintMessage(f"Anzahl Objekte: {len(objects)}\n")
            for obj in objects:
                App.Console.PrintMessage(f"  - {obj.Label} ({obj.TypeId})\n")
                if hasattr(obj, 'Shape') and obj.Shape:
                    shape = obj.Shape
                    bbox = shape.BoundBox
                    top_projected_area = get_top_projected_area(shape)
                    App.Console.PrintMessage(f"    Bounding Box: {bbox.XLength:.2f} x {bbox.YLength:.2f} x {bbox.ZLength:.2f} mm\n")
                    App.Console.PrintMessage(f"    Volume: {format_volume(shape.Volume)}\n")
                    App.Console.PrintMessage(f"    Surface Area: {format_area(shape.Area)}\n")
                    if top_projected_area is None:
                        App.Console.PrintMessage("    Top Projected Area: n/a\n")
                    else:
                        App.Console.PrintMessage(f"    Top Projected Area: {format_area(top_projected_area)}\n")
                    App.Console.PrintMessage(f"    Vertices: {len(shape.Vertexes)}, Faces: {len(shape.Faces)}\n")
            App.Console.PrintMessage(f"Test erfolgreich.\n")
            
        elif command == 'export-stl':
            # STL Export - nur Objekte mit _ExportEnabled=True
            export_objects = [
                obj for obj in objects
                if getattr(obj, "_ExportEnabled", True)
            ]
            if not export_objects:
                App.Console.PrintWarning("Keine exportierbaren Objekte gefunden.\n")
                return
            output_path = os.path.abspath(output_filename)
            App.Console.PrintMessage(f"Exportiere {len(export_objects)} Objekt(e) als STL nach '{output_path}'...\n")
            Mesh.export(export_objects, output_path)
            App.Console.PrintMessage("STL Export erfolgreich abgeschlossen.\n")
            
        elif command == 'export-3mf':
            # 3MF Export - nur Objekte mit _ExportEnabled=True
            export_objects = [
                obj for obj in objects
                if getattr(obj, "_ExportEnabled", True)
            ]
            if not export_objects:
                App.Console.PrintWarning("Keine exportierbaren Objekte gefunden.\n")
                return
            output_path = os.path.abspath(output_filename or "export.3mf")
            if not output_path.lower().endswith(".3mf"):
                output_path = output_path + ".3mf"

            App.Console.PrintMessage(f"Exportiere {len(export_objects)} Objekt(e) als 3MF nach '{output_path}'...\n")

            # Labels eindeutig machen (einige Exporter verwenden diese für Bauteilnamen)
            seen_labels = set()
            for obj in export_objects:
                base = obj.Label if getattr(obj, "Label", None) else obj.Name
                name = base
                i = 1
                while name in seen_labels:
                    i += 1
                    name = f"{base}_{i}"
                try:
                    if obj.Label != name:
                        obj.Label = name
                except Exception:
                    pass
                seen_labels.add(name)

            # Export versuchen: gruppiertes 3MF mit Namen (Bambu/Orca-kompatibel)
            try:
                n_obj, n_parts = write_grouped_3mf(export_objects, output_path)
                App.Console.PrintMessage(
                    f"3MF Export erfolgreich: {n_obj} Slicer-Objekt(e), {n_parts} Part(s).\n"
                )
            except Exception as e_primary:
                App.Console.PrintWarning(
                    f"Gruppierter 3MF-Export fehlgeschlagen ({e_primary}). Fallback auf flachen Mesh.export...\n"
                )
                try:
                    Mesh.export(export_objects, output_path)
                    App.Console.PrintMessage("3MF Export (flach) erfolgreich abgeschlossen.\n")
                except Exception as e_fallback:
                    App.Console.PrintError(
                        "3MF Export fehlgeschlagen. Prüfe, ob FreeCAD mit 3MF-Unterstützung (lib3mf) gebaut ist oder installiere den 3MF-Exporter.\n"
                    )
                    App.Console.PrintError(f"Fehler gruppiert: {e_primary}\n")
                    App.Console.PrintError(f"Fehler Mesh.export: {e_fallback}\n")
                    sys.exit(2)

        elif command == 'export-stp':
            # STEP Export - alle Objekte (auch Dummy/Referenz)
            output_path = os.path.abspath(output_filename or "export.stp")
            if not output_path.lower().endswith((".stp", ".step")):
                output_path = output_path + ".stp"

            App.Console.PrintMessage(f"Exportiere {len(objects)} Objekt(e) als STEP nach '{output_path}'...\n")

            try:
                import Import  # type: ignore
                Import.export(objects, output_path)
                App.Console.PrintMessage("STEP Export erfolgreich abgeschlossen.\n")
            except Exception as error:
                App.Console.PrintError(f"STEP Export fehlgeschlagen: {error}\n")
                sys.exit(2)
            

    finally:
        # Dokument schließen
        App.closeDocument(doc.Name)

import argparse

def main():
    parser = argparse.ArgumentParser(
        description="FreeCAD Headless Tool - Lädt ein Geometrie-Skript und führt verschiedene Operationen aus."
    )
    parser.add_argument(
        "command",
        choices=["test", "export-stl", "export-3mf", "export-stp"],
        help="Aktion: test (nur prüfen), export-stl (als STL exportieren), export-3mf (als 3MF exportieren), export-stp (als STEP exportieren)"
    )
    parser.add_argument(
        "--project-root",
        help="Optionale Projektwurzel für projektinterne Python-Imports. Standard: automatische Erkennung."
    )
    parser.add_argument(
        "geometry_script",
        help="Pfad zum Python-Skript, das eine 'create_geometry(doc)'-Funktion definiert."
    )
    parser.add_argument(
        "output_file",
        nargs="?",
        help="Name der Ausgabedatei (erforderlich für export-stl, export-3mf und export-stp)"
    )

    # Da FreeCADCmd das Skript ausführt, sind die ersten beiden Argumente
    # der Interpreter und der Skriptname. Wir parsen nur die restlichen.
    args = parser.parse_args(sys.argv[2:])

    # Sicherheitsprüfung: Verhindern, dass das Skript sich selbst importiert.
    if os.path.abspath(args.geometry_script) == os.path.abspath(__file__):
        App.Console.PrintError("Fehler: Das Skript kann sich nicht selbst als Geometrie-Modul laden.\n")
        sys.exit(1)

    # Prüfe ob output_file für export benötigt wird
    if args.command in ['export-stl', 'export-3mf', 'export-stp'] and not args.output_file:
        App.Console.PrintError(f"Fehler: {args.command} benötigt einen output_file Parameter.\n")
        sys.exit(1)

    try:
        process_geometry(
            args.geometry_script,
            args.command,
            args.output_file,
            project_root=args.project_root,
        )
    except SystemExit:
        raise
    except Exception as error:
        App.Console.PrintError(f"Fehler beim Verarbeiten der Geometrie: {error}\n")
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)


main()



