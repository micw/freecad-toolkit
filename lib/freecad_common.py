"""Shared FreeCAD helpers for reusable geometry modules."""

import math

import FreeCAD as App
import Part


def merge_params(defaults, overrides=None):
    """Return defaults with non-None overrides applied."""
    params = defaults.copy()
    if overrides:
        params.update({key: value for key, value in overrides.items() if value is not None})
    return params


class Shape2D:
    """2D path builder in the XY plane (Z=0)."""

    def __init__(self, x, y):
        self.start = App.Vector(x, y, 0)
        self.current = self.start
        self.edges = []
        self.holes = []
        self._offset = App.Vector(0, 0, 0)

    def relLineTo(self, dx, dy):
        """Add a straight line segment relative to current position."""
        point = App.Vector(self.current.x + dx, self.current.y + dy, 0)
        self.edges.append(Part.makeLine(self.current, point))
        self.current = point
        return self

    def translate(self, dx, dy):
        """Shift the entire shape drawn so far by (dx, dy)."""
        offset = App.Vector(dx, dy, 0)
        self._offset = self._offset.add(offset)
        self.edges = [edge.translated(offset) for edge in self.edges]
        self.holes = [
            {
                "center": hole["center"].add(offset),
                "diameter": hole["diameter"],
            }
            for hole in self.holes
        ]
        self.start = self.start.add(offset)
        self.current = self.current.add(offset)
        return self

    def roundHoleAt(self, x, y, diameter):
        """Add a circular hole at absolute XY coordinates using its diameter."""
        self.holes.append(
            {
                "center": App.Vector(x, y, 0),
                "diameter": diameter,
            }
        )
        return self

    def holeAt(self, x, y, diameter):
        """Backward-compatible alias for roundHoleAt()."""
        return self.roundHoleAt(x, y, diameter)

    def relArcTo(self, dx, dy, deg=90):
        """Add a circular arc to (current+dx, current+dy)."""
        p1 = self.current
        p2 = App.Vector(p1.x + dx, p1.y + dy, 0)
        chord = math.hypot(dx, dy)
        if chord < 1e-12 or abs(deg) < 1e-12:
            return self

        angle_rad = math.radians(deg)
        half_angle = abs(angle_rad) / 2
        radius = chord / (2 * math.sin(half_angle))
        dist_from_mid = radius * math.cos(half_angle)

        perp_x = -dy / chord
        perp_y = dx / chord

        mid = App.Vector((p1.x + p2.x) / 2, (p1.y + p2.y) / 2, 0)
        if deg > 0:
            center = App.Vector(
                mid.x + perp_x * dist_from_mid,
                mid.y + perp_y * dist_from_mid,
                0,
            )
        else:
            center = App.Vector(
                mid.x - perp_x * dist_from_mid,
                mid.y - perp_y * dist_from_mid,
                0,
            )

        vector = p1 - center
        cos_a = math.cos(half_angle)
        sin_a = math.sin(half_angle) * (1 if deg > 0 else -1)
        arc_mid = App.Vector(
            center.x + vector.x * cos_a - vector.y * sin_a,
            center.y + vector.x * sin_a + vector.y * cos_a,
            0,
        )

        self.edges.append(Part.Edge(Part.Arc(p1, arc_mid, p2)))
        self.current = p2
        return self

    def close(self):
        """Close the wire back to the start point and return a Part.Face."""
        if (self.current - self.start).Length > 1e-12:
            self.edges.append(Part.makeLine(self.current, self.start))
        outer_wire = Part.Wire(self.edges)
        if not self.holes:
            return Part.Face(outer_wire)

        wires = [outer_wire]
        for hole in self.holes:
            radius = hole["diameter"] / 2
            hole_edge = Part.makeCircle(radius, hole["center"], App.Vector(0, 0, 1))
            wires.append(Part.Wire([hole_edge]))
        return Part.Face(wires, "Part::FaceMakerBullseye")


class PreviewAssembly:
    """Small preview/document assembly grouped by fused export objects."""

    def __init__(self):
        self._groups = {}

    def group(self, name, color=None, transparency=None, label=None, export=True):
        """Create or fetch a preview group builder."""
        if name not in self._groups:
            self._groups[name] = {
                "shapes": [],
                "color": color,
                "transparency": transparency,
                "label": label or name,
                "export": export,
            }
        return _PreviewGroupBuilder(self._groups[name])

    def build(self, doc):
        """Fuse all groups and create FreeCAD objects in the document."""
        objects = []
        for name, group in self._groups.items():
            if not group["shapes"]:
                continue
            solid = group["shapes"][0]
            for shape in group["shapes"][1:]:
                solid = solid.fuse(shape)
            obj = doc.addObject("Part::Feature", name)
            obj.Label = group["label"]
            obj.Shape = solid
            obj.addProperty("App::PropertyBool", "_ExportEnabled", "Export", "Include in mesh export")
            obj._ExportEnabled = group["export"]
            view = obj.ViewObject
            if view:
                if group["color"] is not None:
                    view.ShapeColor = group["color"]
                if group["transparency"] is not None:
                    view.Transparency = group["transparency"]
            objects.append(obj)
        return objects


class _PreviewGroupBuilder:
    def __init__(self, group):
        self._group = group

    def add(self, shape):
        self._group["shapes"].append(shape)
        return self


class ModuleGroup:
    """A semantic module group with one or more shapes."""

    def __init__(self, name, color=None, transparency=None, label=None, export=True):
        self.name = name
        self.color = color
        self.transparency = transparency
        self.label = label or name
        self.export = export
        self.parts = {}
        self._fused_cache = None

    def add(self, shape, part_name=None):
        """Add a shape to the group."""
        if part_name is None:
            part_name = f"part_{len(self.parts) + 1}"
        self.parts[part_name] = shape
        self._fused_cache = None
        return self

    def fused(self):
        """Return a fused shape of all parts in the group."""
        if not self.parts:
            return None
        if self._fused_cache is not None:
            return self._fused_cache
        shapes = list(self.parts.values())
        solid = shapes[0]
        for shape in shapes[1:]:
            solid = solid.fuse(shape)
        self._fused_cache = solid
        return solid


class ModuleResult:
    """Common result container for reusable module geometry."""

    def __init__(self, groups=None, refs=None, params=None, info=None):
        self.groups = groups or {}
        self.refs = refs or {}
        self.params = params or {}
        self.info = info or {}

    @property
    def shapes(self):
        """Return fused shapes keyed by semantic group name."""
        fused_shapes = {}
        for name, group in self.groups.items():
            fused_shape = group.fused()
            if fused_shape is not None:
                fused_shapes[name] = fused_shape
        return fused_shapes

    def group(self, name, color=None, transparency=None, label=None, export=True):
        """Create or fetch a semantic module group."""
        if name not in self.groups:
            self.groups[name] = ModuleGroup(
                name,
                color=color,
                transparency=transparency,
                label=label,
                export=export,
            )
        return self.groups[name]

    def transformed(self, x=0.0, y=0.0, z=0.0, rotation_deg=0.0):
        """Return a transformed copy of the full module result."""
        # TODO: Add a fast path for transforming already-fused group solids so
        # callers that only need semantic output shapes can avoid rebuilding
        # transformed ModuleGroup parts and fusing them again afterwards.
        matrix = App.Matrix()
        has_rotation = abs(rotation_deg) > 1e-9
        if has_rotation:
            matrix.rotateZ(math.radians(rotation_deg))

        transformed_groups = {}
        for name, group in self.groups.items():
            transformed_group = ModuleGroup(
                name,
                color=group.color,
                transparency=group.transparency,
                label=group.label,
                export=group.export,
            )
            for part_name, shape in group.parts.items():
                transformed_shape = shape.copy()
                if has_rotation:
                    transformed_shape = transformed_shape.transformGeometry(matrix)
                transformed_shape.translate(App.Vector(x, y, z))
                transformed_group.add(transformed_shape, part_name)
            transformed_groups[name] = transformed_group

        return ModuleResult(
            groups=transformed_groups,
            refs=_transform_value(self.refs, x, y, z, matrix if has_rotation else None),
            params=self.params.copy(),
            info=_transform_value(self.info, x, y, z, matrix if has_rotation else None),
        )

    def add_to_preview_assembly(self, assembly=None):
        """Add fused group solids to a preview assembly."""
        if assembly is None:
            assembly = PreviewAssembly()
        for name, group in self.groups.items():
            fused = group.fused()
            if fused is None:
                continue
            assembly.group(
                name,
                color=group.color,
                transparency=group.transparency,
                label=group.label,
                export=group.export,
            ).add(fused)
        return assembly

    def add_to_document(self, doc):
        """Create one document object per fused semantic group."""
        return self.add_to_preview_assembly().build(doc)


def _transform_value(value, x, y, z, matrix):
    if isinstance(value, App.Vector):
        result = App.Vector(value.x, value.y, value.z)
        if matrix is not None:
            result = matrix.multiply(result)
        return result.add(App.Vector(x, y, z))
    if isinstance(value, list):
        return [_transform_value(item, x, y, z, matrix) for item in value]
    if isinstance(value, tuple):
        return tuple(_transform_value(item, x, y, z, matrix) for item in value)
    if isinstance(value, dict):
        return {key: _transform_value(item, x, y, z, matrix) for key, item in value.items()}
    return value