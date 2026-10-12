# Via Stitching - legacy SWIG action plugin for KiCad 6/7/8
#
# Fills the overlap of a net's copper zones (e.g. the top + bottom GND pours)
# with a grid of vias, on whichever two copper layers you pick. Same feature
# as the IPC version (via_stitching_action.py, KiCad 10+), reimplemented on
# the legacy pcbnew SWIG API for KiCad versions the IPC build does not
# cover (6 through 9) -- see via-stitching/tests/legacy/ for the
# verification work behind every board-touching call in _geometry_legacy.py.
#
# Requires the zones to already be filled (press B in the PCB editor first) --
# this plugin only reads existing fill, same as the IPC version, and for the
# same reason: computing a fill from a plugin is a separate, heavier
# operation with its own failure modes.
#
# License: GPL-3.0-or-later

import json
import math
import os
import re
import sys
import traceback

import pcbnew
import wx
import wx.adv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _geometry_legacy as geo  # noqa: E402
from _i18n_legacy import _  # noqa: E402
from _kicad_config_legacy import kicad_config_dirs  # noqa: E402

VERSION = "1.2.2"

DEFAULT_NET = "GND"
DEFAULT_VIA_DIAMETER_MM = 0.6
DEFAULT_DRILL_MM = 0.3
DEFAULT_SPACING_MM = 2.0

DEFAULT_MICROVIA_DIAMETER_MM = 0.25
DEFAULT_MICROVIA_DRILL_MM = 0.1
DEFAULT_MICROVIA_SPACING_MM = DEFAULT_MICROVIA_DIAMETER_MM * 4

PATTERNS = ["Hexagonal", "Square", "Staggered"]
DEFAULT_PATTERN = "Square"

DEFAULT_AVOID_OTHER_ZONES = False
DEFAULT_AVOID_FOOTPRINTS = False
DEFAULT_AVOID_SAME_NET_PADS = False

EDGE_EPS_MM = 0.05
HOLE_MARGIN_MM = 0.25
VIA_COUNT_WARN = 5000
GROUP_PREFIX = "ViaStitching "


def _mm(nm):
    return pcbnew.ToMM(nm)


def _from_mm(mm):
    return pcbnew.FromMM(mm)


def _pcb_frame():
    """KiCad's PCB editor window, to parent dialogs and the fill progress on.
    "PcbFrame" is that frame's wx name -- KiCad's own bundled pyshell looks it
    up exactly like this. None when it isn't there (headless tests)."""
    return wx.FindWindowByName("PcbFrame")


def _refill_zones(board, parent):
    """Best-effort parting refill, same as the IPC version. Passing the PCB
    frame lets KiCad put up its own native fill-progress dialog."""
    try:
        pcbnew.ZONE_FILLER(board).Fill(board.Zones(), False, parent)
    except Exception:
        pass


# ---- remembered settings -------------------------------------------------
#
# A distinct filename from the IPC version's via_stitching_settings.json:
# kicad_config_dirs() picks the newest installed KiCad version's config dir
# regardless of which one is actually running, so on a machine with both
# KiCad 6 and KiCad 10 installed, the two plugins would otherwise silently
# read and overwrite each other's saved settings.

def _settings_path():
    dirs = kicad_config_dirs()
    base = dirs[0] if dirs else os.path.expanduser("~")
    return os.path.join(base, "via_stitching_settings_legacy.json")


def _load_settings():
    try:
        with open(_settings_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_settings(values):
    try:
        with open(_settings_path(), "w", encoding="utf-8") as fh:
            json.dump(values, fh, indent=2)
    except Exception:
        pass


def _clear_settings():
    try:
        os.remove(_settings_path())
    except Exception:
        pass


# ---- layer color swatches -------------------------------------------------

_DEFAULT_COPPER = {
    "f": (200, 52, 52), "in1": (127, 200, 127), "in2": (206, 125, 66),
    "in3": (79, 203, 203), "in4": (219, 98, 139), "in5": (167, 165, 198),
    "in6": (40, 204, 217), "b": (77, 127, 196),
}


def _copper_key_to_layer(key):
    if key == "f":
        return pcbnew.F_Cu
    if key == "b":
        return pcbnew.B_Cu
    m = re.match(r"in(\d+)$", key)
    if not m:
        return None
    return getattr(pcbnew, f"In{m.group(1)}_Cu", None)


def _layer_colors():
    """{layer_id: (r, g, b)} from the active KiCad color theme, with fallbacks."""
    copper = dict(_DEFAULT_COPPER)
    try:
        for config_dir in kicad_config_dirs():
            pcbnew_json = os.path.join(config_dir, "pcbnew.json")
            if not os.path.exists(pcbnew_json):
                continue
            with open(pcbnew_json, encoding="utf-8") as fh:
                theme = json.load(fh)["appearance"]["color_theme"]
            colors_path = os.path.join(config_dir, "colors", f"{theme}.json")
            with open(colors_path, encoding="utf-8") as fh:
                data = json.load(fh)
            for key, val in data.get("board", {}).get("copper", {}).items():
                m = re.match(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", val)
                if m:
                    copper[key.lower()] = tuple(map(int, m.groups()))
            break
    except Exception:
        pass
    out = {}
    for key, rgb in copper.items():
        layer = _copper_key_to_layer(key)
        if layer is not None:
            out[layer] = rgb
    return out


def _color_swatch(rgb, size=14):
    """A flat-colored square bitmap for a layer combo entry.

    Built from a wx.Image (a plain per-pixel RGB buffer) rather than the
    IPC version's wx.MemoryDC drawing -- the swatches did not render at all
    on a real KiCad 6.0 (blank/placeholder icon in the combo) with the
    MemoryDC approach, even though the same code works on the IPC side.
    An Image->Bitmap conversion has no DC/GC to select in and out of, so
    it does not depend on how a given wx build's device-context backend
    handles an off-screen MemoryDC."""
    img = wx.Image(size, size)
    img.SetRGB(wx.Rect(0, 0, size, size), *rgb)
    for x in range(size):
        img.SetRGB(wx.Rect(x, 0, 1, 1), 90, 90, 90)
        img.SetRGB(wx.Rect(x, size - 1, 1, 1), 90, 90, 90)
    for y in range(size):
        img.SetRGB(wx.Rect(0, y, 1, 1), 90, 90, 90)
        img.SetRGB(wx.Rect(size - 1, y, 1, 1), 90, 90, 90)
    return wx.Bitmap(img)


# ---- via-type / layer advisory --------------------------------------------

def _via_type_advisory(board, via_type, start_layer, end_layer):
    """A plain-language warning if this via type/layer combination isn't the
    standard shape for that type, or None if it looks normal. Never blocks
    anything -- shown as an inline label, same as the IPC version."""
    order = geo.copper_layer_order(board)
    outer = {order[0], order[-1]}
    starts_outer = start_layer in outer
    ends_outer = end_layer in outer

    if via_type == pcbnew.VIATYPE_MICROVIA:
        if not (starts_outer or ends_outer) or len(geo.span_layers(board, start_layer, end_layer)) != 2:
            return (
                _("This isn't a standard microvia: a microvia connects an "
                "outer layer (F.Cu or B.Cu) to the layer right next to it. "
                "KiCad's DRC will likely flag this via.")
            )
    elif via_type == pcbnew.VIATYPE_BLIND_BURIED:
        if not (starts_outer or ends_outer):
            return (
                "This isn't a standard blind/buried via with both layers "
                "inside the board: pick an outer layer (F.Cu or B.Cu) for a "
                "blind via, or two inner layers for a buried one."
            )
    return None


# ---- geometry / stitching --------------------------------------------------

def _grid_points(bounds, spacing_nm, pattern):
    """Candidate (x, y) nm points across `bounds` for the given pattern.
    Identical to the IPC version's _grid_points -- pure Python, no board
    API calls, shared as-is between backends."""
    minx, miny, maxx, maxy = bounds
    minx, miny = int(math.floor(minx)), int(math.floor(miny))
    maxx, maxy = int(math.ceil(maxx)), int(math.ceil(maxy))
    if pattern == "Hexagonal":
        row_pitch = round(spacing_nm * math.sqrt(3) / 2)
    else:
        row_pitch = spacing_nm
    if row_pitch <= 0 or spacing_nm <= 0:
        return
    row = 0
    y = miny
    while y <= maxy:
        if pattern in ("Hexagonal", "Staggered") and (row % 2) == 1:
            x = minx + spacing_nm // 2
        else:
            x = minx
        while x <= maxx:
            yield (x, y)
            x += spacing_nm
        y += row_pitch
        row += 1


def _nudge_radius(spacing_nm, drill_nm):
    room = (spacing_nm - drill_nm - _from_mm(HOLE_MARGIN_MM)) // 2
    return max(0, min(spacing_nm // 4, room))


def _nudged(x, y, nudge_r, allowed, blocked):
    if nudge_r <= 0:
        return None
    from shapely.geometry import Point

    for k in range(8):
        a = 2 * math.pi * k / 8
        nx = x + round(nudge_r * math.cos(a))
        ny = y + round(nudge_r * math.sin(a))
        if not blocked(nx, ny) and allowed(Point(nx, ny)):
            return (nx, ny)
    return None


def _blocked_predicate(shapes):
    if not shapes:
        return lambda x, y: False
    from shapely.geometry import Point
    from shapely.strtree import STRtree  # importable from shapely itself only on 2.x

    tree = STRtree(shapes)
    try:
        tree.query(Point(0, 0), predicate="intersects")
    except TypeError:
        # shapely 1.8, which is what Ubuntu 22.04 ships and 22.04 is a KiCad 6
        # distro. Its query() takes no predicate and answers with the geometries
        # whose bounding boxes overlap, so the real hit test has to happen here
        # or every via would be blocked by a neighbour's bounding box.
        return lambda x, y: any(s.intersects(Point(x, y)) for s in tree.query(Point(x, y)))
    return lambda x, y: len(tree.query(Point(x, y), predicate="intersects")) > 0


def stitch(board, via_type, start_layer, end_layer, net_name, via_dia_mm, drill_mm,
           spacing_mm, pattern, x_offset_mm, y_offset_mm,
           avoid_other_zones=False, avoid_footprints=False, avoid_same_net_pads=False,
           parent=None):
    """Run the stitching. Returns (vias placed, whether they were grouped)."""
    from shapely.geometry import Point
    from shapely.prepared import prep

    diameter_nm = _from_mm(via_dia_mm)
    drill_nm = _from_mm(drill_mm)
    spacing_nm = _from_mm(spacing_mm)
    via_radius_nm = diameter_nm // 2
    x_offset_nm = _from_mm(x_offset_mm)
    y_offset_nm = _from_mm(y_offset_mm)

    net = next((n for n in board.GetNetsByNetcode().values() if n.GetNetname() == net_name), None)
    if net is None:
        raise RuntimeError(
            _("Net '{net}' not found on the board.").format(net=net_name)
        )

    zones = list(board.Zones())
    span = geo.span_layers(board, start_layer, end_layer)
    span_set = set(span)

    region_by_layer = {}
    for layer in (start_layer, end_layer, *span):
        if layer in region_by_layer:
            continue
        r = geo.layer_region(zones, net_name, layer)
        if r is not None:
            region_by_layer[layer] = r

    # The via must land on the net's copper on the two layers it connects. A
    # through via is the exception: its barrel crosses every layer, so any two
    # poured layers in the span give it something to stitch, whether or not
    # they are the outer ones. One poured layer is not enough, that would only
    # place vias DRC reports as dangling.
    missing = [l for l in (start_layer, end_layer) if l not in region_by_layer]
    poured = [l for l in span if l in region_by_layer]
    if missing and not (via_type == pcbnew.VIATYPE_THROUGH and len(poured) >= 2):
        names = ", ".join(board.GetLayerName(l) for l in missing)
        raise RuntimeError(
            _(
                "Net '{net}' has no filled copper on: {layers}.\n"
                "Pick start/end layers where the net is poured, or fill the zones "
                "first (press B in the PCB editor)."
            ).format(net=net_name, layers=names)
        )

    # Keep the via on the net's copper on every poured layer it passes, since
    # the barrel connects all of them. Layers in the span where the net is NOT
    # poured are left to DRC: the barrel passing through another net's copper
    # there is not checked here.
    region = region_by_layer[poured[0]]
    for layer in poured[1:]:
        region = region.intersection(region_by_layer[layer])

    if region.is_empty:
        raise RuntimeError(_("The selected net's planes do not overlap anywhere on the selected layer span."))

    inset = via_radius_nm + _from_mm(EDGE_EPS_MM)
    region = region.buffer(-inset)
    if region.is_empty:
        raise RuntimeError(_("No room for vias after clearance inset. Try a smaller via diameter."))

    minx, miny, maxx, maxy = region.bounds
    shifted_bounds = (minx + x_offset_nm, miny + y_offset_nm, maxx + x_offset_nm, maxy + y_offset_nm)
    prepared = prep(region)
    allowed = lambda pt: prepared.contains(pt)  # noqa: E731
    candidates = [
        (x, y) for (x, y) in _grid_points(shifted_bounds, spacing_nm, pattern) if allowed(Point(x, y))
    ]
    if not candidates:
        raise RuntimeError(_("No via positions fit inside the overlap of the planes.\nTry a smaller spacing or via diameter."))

    clearances = geo.net_clearances(board, net_name)
    keepout = geo.via_keepout_shapes(board, via_radius_nm, span_set, _from_mm(HOLE_MARGIN_MM))
    keepout += geo.pad_drill_keepout_shapes(board, via_radius_nm, _from_mm(HOLE_MARGIN_MM))
    keepout += geo.pad_copper_keepout_shapes(board, net_name, via_radius_nm, clearances, span_set, avoid_same_net_pads)
    keepout += geo.rule_area_keepout_shapes(zones, via_radius_nm, span_set)
    keepout += geo.track_keepout_shapes(board, net_name, via_radius_nm, clearances, span_set)
    if avoid_other_zones:
        keepout += geo.zone_keepout_shapes(zones, net_name, via_radius_nm, clearances, span_set)
    if avoid_footprints:
        keepout += geo.footprint_keepout_shapes(board, net_name, via_radius_nm, clearances)
    blocked = _blocked_predicate(keepout)

    nudge_r = _nudge_radius(spacing_nm, drill_nm)
    points = []
    for (x, y) in candidates:
        if not blocked(x, y):
            points.append((x, y))
            continue
        moved = _nudged(x, y, nudge_r, allowed, blocked)
        if moved is not None:
            points.append(moved)

    if not points:
        raise RuntimeError(
            _(
                "All {count} candidate positions are blocked by {blockers}.\n"
                "If these zones are already stitched, delete the previous vias "
                "first, or try a smaller spacing."
            ).format(
                count=len(candidates),
                blockers=_("existing vias, pads, tracks or rule areas"),
            )
        )

    if len(points) > VIA_COUNT_WARN:
        msg = _(
            "This will place {count} vias, which may make KiCad slow.\n"
            "Increase the spacing for fewer vias.\n\nPlace them anyway?"
        ).format(count=len(points))
        if wx.MessageBox(msg, _("Many vias"), wx.YES_NO | wx.ICON_WARNING, parent) != wx.YES:
            return 0, False

    vias = [
        geo.make_via(board, via_type, start_layer, end_layer, diameter_nm, drill_nm, net.GetNetCode(), x, y)
        for (x, y) in points
    ]
    for via in vias:
        board.Add(via)

    group = geo.group_vias(
        board, vias,
        f"{GROUP_PREFIX}{net_name} "
        f"{board.GetLayerName(start_layer)}:{board.GetLayerName(end_layer)}",
    )

    _refill_zones(board, parent)

    return len(vias), group is not None


def _icon_path():
    """The toolbar icon: next to this file once packaged, one directory over in
    the source tree. Shared with the IPC build so the two cannot drift apart."""
    here = os.path.dirname(os.path.abspath(__file__))
    for candidate in (os.path.join(here, "icon.png"),
                      os.path.join(os.path.dirname(here), "plugins", "icon.png")):
        if os.path.exists(candidate):
            return candidate
    return ""  # KiCad draws its default placeholder


class ViaStitchingLegacy(pcbnew.ActionPlugin):
    def defaults(self):
        # The only name a user on KiCad 6 to 9 ever sees. They are not
        # running an old version of anything, just the build for their KiCad.
        self.name = "Via Stitching"
        self.category = "Modify PCB"
        self.description = "Stitch copper zones together with a grid of vias on a chosen net"
        self.show_toolbar_button = True
        self.icon_file_name = _icon_path()
        self.dark_icon_file_name = self.icon_file_name

    def Run(self):
        board = pcbnew.GetBoard()
        parent = _pcb_frame()
        try:
            import shapely  # noqa: F401
        except ImportError:
            wx.MessageBox(
                _("Via Stitching needs the shapely Python package, which KiCad "
                  "does not ship.\n\n"
                  "Linux: sudo apt install python3-shapely\n"
                  "Windows: \"C:/Program Files/KiCad/<version>/bin/python.exe\" "
                  "-m pip install shapely\n\n"
                  "Then restart KiCad."),
                _("Via Stitching"), wx.OK | wx.ICON_ERROR, parent)
            return
        try:
            dlg = ViaStitchingDialogLegacy(parent, board)
            try:
                if dlg.ShowModal() != wx.ID_OK:
                    return
                values = dlg.values()
                dlg.save_current_as_settings()
            finally:
                dlg.Destroy()
            placed, grouped = stitch(board, parent=parent, **values)
            if placed:
                pcbnew.Refresh()
                text = _("Placed {count} stitching vias on net '{net}'.").format(
                    count=placed, net=values["net_name"]
                )
                if not grouped:
                    text += "\n\n" + _(
                        "KiCad would not group them, so they delete individually "
                        "rather than as a set. The vias themselves are fine."
                    )
                wx.MessageBox(text, _("Via Stitching"), wx.OK | wx.ICON_INFORMATION, parent)
        except (RuntimeError, ValueError) as exc:
            wx.MessageBox(str(exc), _("Via Stitching"), wx.OK | wx.ICON_ERROR, parent)
        except Exception:
            _report(parent, _("Via Stitching hit an unexpected error."),
                    traceback.format_exc())


class ErrorDialog(wx.Dialog):
    """Unexpected-failure report. Selectable, so Ctrl+A and Ctrl+C paste into a
    bug report. Ported from the IPC build, which had it and this one did not:
    a traceback inside a MessageBox cannot be copied out of."""

    def __init__(self, parent, summary, details):
        super().__init__(
            parent,
            title=_("Via Stitching Error"),
            size=(660, 420),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.STAY_ON_TOP,
        )

        label = wx.StaticText(self, label=summary)
        label.Wrap(620)

        text = wx.TextCtrl(
            self,
            value=details,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP,
        )
        text.SetFont(wx.Font(wx.FontInfo(9).Family(wx.FONTFAMILY_TELETYPE)))

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 12)
        outer.Add(text, 1, wx.EXPAND | wx.ALL, 12)
        outer.Add(
            self.CreateButtonSizer(wx.OK), 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12
        )
        self.SetSizer(outer)


def _report(parent, summary, exc_text):
    """Show an unexpected failure in a dialog its text can be copied out of."""
    dlg = ErrorDialog(parent, summary, exc_text)
    try:
        dlg.ShowModal()
    finally:
        dlg.Destroy()


class ViaStitchingDialogLegacy(wx.Dialog):
    """'Via Stitching Parameters' input dialog, legacy SWIG backend.

    Laid out to match the IPC version's dialog: grouped sections (net name,
    then via type/layers/size, then pattern/placement/avoid-toggles),
    layer swatches, remembered settings, and the via-type/layer advisory.
    Not ported: clone-from-a-selected-via's exact size (legacy has no
    padstack to read a "standard" size off of the same way) -- but a
    selected via's net/layers/type ARE read, since board.IsSelected() makes
    that cheap and reliable without needing kipy-style selection queries.
    """

    VIA_TYPE_CHOICES = {
        "Through": pcbnew.VIATYPE_THROUGH,
        "Micro": pcbnew.VIATYPE_MICROVIA,
        "Blind/Buried": pcbnew.VIATYPE_BLIND_BURIED,
    }

    def __init__(self, parent, board):
        super().__init__(
            parent,
            title=_("Via Stitching Parameters"),
            style=wx.DEFAULT_DIALOG_STYLE | wx.STAY_ON_TOP,
        )
        self.board = board
        self.VIA_TYPE_NAMES = {v: k for k, v in self.VIA_TYPE_CHOICES.items()}

        layer_order = geo.copper_layer_order(board)
        self.layer_map = {board.GetLayerName(l): l for l in layer_order}
        self.layer_names = {v: k for k, v in self.layer_map.items()}
        self._all_layer_names = list(self.layer_map.keys())
        self._layer_colors = _layer_colors()

        net_names = sorted({n.GetNetname() for n in board.GetNetsByNetcode().values() if n.GetNetname()})

        sample_via = next(
            (t for t in board.GetTracks() if isinstance(t, pcbnew.PCB_VIA) and t.IsSelected()),
            None,
        )
        saved = {} if sample_via else _load_settings()

        # --- Via type ---
        via_type_names = list(self.VIA_TYPE_CHOICES.keys())
        self.via_type = wx.Choice(self, choices=via_type_names)
        if sample_via:
            self.via_type.SetStringSelection(self.VIA_TYPE_NAMES.get(sample_via.GetViaType(), via_type_names[0]))
        elif saved.get("via_type_name") in via_type_names:
            self.via_type.SetStringSelection(saved["via_type_name"])
        else:
            self.via_type.SetSelection(0)
        self.via_type.Bind(wx.EVT_CHOICE, lambda evt: self._on_via_type())

        # --- Layers, with color swatches ---
        def _layer_combo():
            combo = wx.adv.BitmapComboBox(self, style=wx.CB_READONLY)
            for i, name in enumerate(self._all_layer_names):
                rgb = self._layer_colors.get(self.layer_map[name], (128, 128, 128))
                combo.Append(f"L{i + 1} - {name}", _color_swatch(rgb))
            return combo

        self.start_layer = _layer_combo()
        self.end_layer = _layer_combo()
        if sample_via:
            self.start_layer.SetSelection(layer_order.index(sample_via.TopLayer()))
            self.end_layer.SetSelection(layer_order.index(sample_via.BottomLayer()))
        else:
            start_name = saved.get("start_layer_name")
            end_name = saved.get("end_layer_name")
            self.start_layer.SetSelection(
                layer_order.index(self.layer_map[start_name]) if start_name in self.layer_map else 0
            )
            self.end_layer.SetSelection(
                layer_order.index(self.layer_map[end_name]) if end_name in self.layer_map
                else len(layer_order) - 1
            )
        self.start_layer.Bind(wx.EVT_COMBOBOX, lambda evt: self._on_start_layer())
        self.end_layer.Bind(wx.EVT_COMBOBOX, lambda evt: self._update_advisory())

        # --- Size fields ---
        if sample_via:
            via_dia_str = str(_mm(sample_via.GetWidth()))
            drill_str = str(_mm(sample_via.GetDrillValue()))
            spacing_str = str(_mm(sample_via.GetWidth() * 4))
        elif saved:
            via_dia_str = str(saved.get("via_dia_mm", DEFAULT_VIA_DIAMETER_MM))
            drill_str = str(saved.get("drill_mm", DEFAULT_DRILL_MM))
            spacing_str = str(saved.get("spacing_mm", DEFAULT_SPACING_MM))
        else:
            via_dia_str = str(DEFAULT_VIA_DIAMETER_MM)
            drill_str = str(DEFAULT_DRILL_MM)
            spacing_str = str(DEFAULT_SPACING_MM)

        self.via_dia = wx.TextCtrl(self, value=via_dia_str)
        self.drill = wx.TextCtrl(self, value=drill_str)
        self.spacing = wx.TextCtrl(self, value=spacing_str)
        self._auto_values = set() if sample_via else {via_dia_str, drill_str, spacing_str}

        # --- Pattern / placement ---
        self.pattern = wx.Choice(self, choices=PATTERNS)
        self.pattern.SetSelection(PATTERNS.index(saved["pattern"]) if saved.get("pattern") in PATTERNS
                                   else PATTERNS.index(DEFAULT_PATTERN))

        x_offset_str = str(saved.get("x_offset_mm", 0.0)) if saved else "0.0"
        y_offset_str = str(saved.get("y_offset_mm", 0.0)) if saved else "0.0"
        self.x_offset = wx.TextCtrl(self, value=x_offset_str)
        self.y_offset = wx.TextCtrl(self, value=y_offset_str)
        offset_tip = (
            _("Shifts this run's grid, so a second pass (e.g. a back-side "
            "microvia stitch) doesn't land on top of the first.")
        )
        self.x_offset.SetToolTip(offset_tip)
        self.y_offset.SetToolTip(offset_tip)

        self.avoid_zones = wx.CheckBox(self, label=_("Avoid zones of other nets"))
        self.avoid_zones.SetValue(bool(saved.get("avoid_other_zones", DEFAULT_AVOID_OTHER_ZONES)))
        self.avoid_zones.SetToolTip(_(
            "Keep vias out of other nets' filled copper on every layer.\n\n"
            "Off by default: a via through another net's pour is not a DRC "
            "error, because KiCad clears the fill back around it when the "
            "zones are refilled.\n\n"
            "Tick this to leave an inner power plane unperforated. Expect far "
            "fewer vias, since such a plane often covers most of the board."
        ))

        self.avoid_footprints = wx.CheckBox(self, label=_("Avoid footprints"))
        self.avoid_footprints.SetValue(bool(saved.get("avoid_footprints", DEFAULT_AVOID_FOOTPRINTS)))
        self.avoid_footprints.SetToolTip(_(
            "Keep vias out from under every component's bounding box.\n\n"
            "This is about mechanical fit, not clearance, so it applies "
            "whatever net the footprint is on.\n\n"
            "Off by default: a thermal via array under a QFN or BGA ground pad "
            "is a normal use of via stitching, and this would block it."
        ))

        self.avoid_same_net_pads = wx.CheckBox(self, label=_("Avoid pads already on this net"))
        self.avoid_same_net_pads.SetValue(bool(saved.get("avoid_same_net_pads", DEFAULT_AVOID_SAME_NET_PADS)))
        self.avoid_same_net_pads.SetToolTip(_(
            "Keep vias off the copper of pads that are already on the net being "
            "stitched.\n\n"
            "Off by default: dropping a via array straight onto a QFN or BGA "
            "thermal pad is a normal use of stitching, and this would block it.\n\n"
            "Tick this to leave same-net pads alone, for example to keep vias out "
            "of a paste-critical pad. Pads on every other net are avoided either "
            "way, with their full clearance."
        ))

        # --- Net ---
        self.net = wx.ComboBox(self, choices=net_names, style=wx.CB_DROPDOWN)
        if sample_via:
            self.net.SetValue(sample_via.GetNetname())
        elif saved.get("net_name"):
            self.net.SetValue(saved["net_name"])
        elif DEFAULT_NET in net_names:
            self.net.SetValue(DEFAULT_NET)
        elif net_names:
            self.net.SetSelection(0)

        # --- Layout: matches the real IPC dialog's grouping and order
        # exactly (Net Name first, not last -- an earlier version of this
        # matched a reference screenshot instead of the current shipped
        # source, which put it last; the real source wins). ---
        self.main_sizer = wx.BoxSizer(wx.VERTICAL)

        # Safety net for the missing native undo: legacy ActionPlugins can't
        # register anything on KiCad's undo stack (PCB_EDIT_FRAME isn't
        # exposed to Python at all), and Ctrl+Z after a run has been seen to
        # leave ghost vias in the view -- so the plugin removes the vias it
        # grouped itself. Top of the dialog rather than its own toolbar
        # button: it belongs with the run it undoes.
        self.remove_run_btn = wx.Button(self, label=_("Reset last run"))
        self.remove_run_btn.Bind(wx.EVT_BUTTON, lambda evt: self._on_remove_last_run())
        self.remove_run_label = wx.StaticText(self, label="")
        top_row = wx.BoxSizer(wx.HORIZONTAL)
        top_row.Add(self.remove_run_btn, 0, wx.ALIGN_CENTER_VERTICAL)
        top_row.Add(self.remove_run_label, 1, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 8)
        self.main_sizer.Add(top_row, 0, wx.EXPAND | wx.ALL, 5)
        self._refresh_remove_run_btn()

        self._make_group(self.main_sizer, [
            (_("Net Name:"), self.net),
        ])

        self._make_group(self.main_sizer, [
            (_("Via Type:"), self.via_type),
            (_("Start Layer:"), self.start_layer),
            (_("End Layer:"), self.end_layer),
            (_("Via Diameter (mm):"), self.via_dia),
            (_("Drill (mm):"), self.drill),
        ])

        self.advisory_label = wx.StaticText(self, label="")
        self.advisory_label.SetForegroundColour(wx.Colour(180, 95, 0))
        self.advisory_label.Hide()
        self.main_sizer.Add(self.advisory_label, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)

        self._make_group(self.main_sizer, [
            (_("Via Pattern:"), self.pattern),
            (_("Spacing (mm):"), self.spacing),
            (_("X-Offset (mm):"), self.x_offset),
            (_("Y-Offset (mm):"), self.y_offset),
            (_("Zones:"), self.avoid_zones),
            (_("Footprints:"), self.avoid_footprints),
            (_("Same-net pads:"), self.avoid_same_net_pads),
        ])

        buttons = self.CreateButtonSizer(wx.OK | wx.CANCEL)
        self.reset_btn = wx.Button(self, label=_("Reset settings"))
        self.reset_btn.SetToolTip(_("Restore the built-in defaults and clear the settings saved from previous runs."))
        self.reset_btn.Bind(wx.EVT_BUTTON, lambda evt: self._on_reset())

        button_row = wx.BoxSizer(wx.HORIZONTAL)
        button_row.Add(self.reset_btn, 0, wx.ALIGN_CENTER_VERTICAL)
        button_row.AddStretchSpacer(1)
        button_row.Add(buttons, 0, wx.EXPAND)

        self.main_sizer.AddSpacer(10)
        self.main_sizer.Add(button_row, 0, wx.EXPAND)

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(self.main_sizer, 1, wx.EXPAND | wx.ALL, 8)
        self.SetSizerAndFit(outer)
        self.Layout()
        self.CenterOnParent()

        self._refresh_layer_controls()
        self._update_advisory()

    def _make_group(self, parent_sizer, rows):
        box = wx.StaticBoxSizer(wx.VERTICAL, self, "")
        grid = wx.FlexGridSizer(0, 2, 5, 5)
        grid.AddGrowableCol(1)
        for label, ctrl in rows:
            grid.Add(wx.StaticText(self, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
            grid.Add(ctrl, 1, wx.EXPAND)
        box.Add(grid, 1, wx.EXPAND | wx.ALL, 5)
        parent_sizer.Add(box, 0, wx.EXPAND | wx.ALL, 5)

    def _set_layer_combo_choices(self, combo, names, keep):
        """`names` may be a filtered subset (Through locks each combo to a
        single entry), but the "L{n}" label must always reflect the
        layer's real position in the full stackup, not its position in
        this possibly-shrunk list -- otherwise F.Cu and B.Cu both show as
        "L1" the moment Through's initial refresh overwrites the
        constructor's correctly-labelled population."""
        combo.Clear()
        for name in names:
            n = self._all_layer_names.index(name) + 1
            rgb = self._layer_colors.get(self.layer_map[name], (128, 128, 128))
            combo.Append(f"L{n} - {name}", _color_swatch(rgb))
        if names:
            combo.SetSelection(names.index(keep) if keep in names else 0)
        combo.Enable(len(names) > 1)

    def _combo_layer_name(self, combo):
        """The actual layer name behind a combo's current selection.

        Combo entries are labelled "L{n} - {name}", and n is that combo's
        OWN position, not an index into the full layer list -- Through mode
        shrinks each combo to a single entry, so both combos' GetSelection()
        return 0 at the same time. Indexing self._all_layer_names by that
        raw selection (an earlier version of this code did) silently
        resolved both start and end to the same layer. Parsing the visible
        label back out is what makes this correct regardless of how many
        entries a combo currently holds."""
        sel = combo.GetSelection()
        if sel == wx.NOT_FOUND:
            return None
        label = combo.GetString(sel)
        return label.split(" - ", 1)[1] if " - " in label else label

    def _refresh_layer_controls(self):
        """Lock Start/End Layer to F.Cu/B.Cu for Through -- always true by
        definition, so there's nothing to lose by fixing it. Every other via
        type is left free; _via_type_advisory catches a genuine mismatch
        instead of disabling fields."""
        names = self._all_layer_names

        def current(combo):
            name = self._combo_layer_name(combo)
            return name if name in names else (names[0] if names else "")

        if self.via_type.GetStringSelection() == "Through":
            self._set_layer_combo_choices(self.start_layer, [names[0]], names[0])
            self._set_layer_combo_choices(self.end_layer, [names[-1]], names[-1])
        else:
            self._set_layer_combo_choices(self.start_layer, names, current(self.start_layer))
            self._set_layer_combo_choices(self.end_layer, names, current(self.end_layer))

    def _on_via_type(self):
        self._refresh_layer_controls()
        self._apply_via_type_defaults()
        self._update_advisory()

    def _on_start_layer(self):
        self._refresh_layer_controls()
        self._update_advisory()

    def _apply_via_type_defaults(self):
        if self.via_type.GetStringSelection() == "Micro":
            wanted = (DEFAULT_MICROVIA_DIAMETER_MM, DEFAULT_MICROVIA_DRILL_MM, DEFAULT_MICROVIA_SPACING_MM)
        else:
            wanted = (DEFAULT_VIA_DIAMETER_MM, DEFAULT_DRILL_MM, DEFAULT_SPACING_MM)
        wanted = [str(v) for v in wanted]
        for ctrl, value in zip((self.via_dia, self.drill, self.spacing), wanted):
            if ctrl.GetValue() in self._auto_values:
                ctrl.SetValue(value)
        self._auto_values.update(wanted)

    def _update_advisory(self):
        via_type = self.VIA_TYPE_CHOICES[self.via_type.GetStringSelection()]
        start_name = self._combo_layer_name(self.start_layer)
        end_name = self._combo_layer_name(self.end_layer)
        advisory = None
        if start_name in self.layer_map and end_name in self.layer_map:
            start_layer = self.layer_map[start_name]
            end_layer = self.layer_map[end_name]
            if start_layer != end_layer:
                advisory = _via_type_advisory(self.board, via_type, start_layer, end_layer)
        if advisory:
            self.advisory_label.SetLabel(advisory)
            self.advisory_label.Wrap(380)
            self.advisory_label.Show()
        else:
            self.advisory_label.SetLabel("")
            self.advisory_label.Hide()
        self.main_sizer.Layout()
        self.Fit()

    def _refresh_remove_run_btn(self):
        runs = stitching_runs(self.board)
        self.remove_run_btn.Enable(bool(runs))
        if runs:
            group, vias = runs[-1]
            run = group.GetName()[len(GROUP_PREFIX):] or group.GetName()
            self.remove_run_label.SetLabel(
                _("{run}, {count} vias").format(run=run, count=len(vias))
            )
            self.remove_run_btn.SetToolTip(
                _("Delete the {count} vias placed by '{run}'.").format(
                    count=len(vias), run=run
                )
            )
        else:
            self.remove_run_label.SetLabel(_("No stitching run on this board"))
            self.remove_run_btn.SetToolTip(
                _("Only vias placed by this plugin, and still grouped, can be "
                "removed this way.")
            )
        self.remove_run_label.SetForegroundColour(
            wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT if not runs else wx.SYS_COLOUR_WINDOWTEXT)
        )
        self.Layout()

    def _on_remove_last_run(self):
        runs = stitching_runs(self.board)
        if not runs:
            self._refresh_remove_run_btn()
            return
        group, vias = runs[-1]
        confirm = wx.MessageBox(
            _("Remove the {count} vias placed by '{run}'?").format(
                count=len(vias), run=group.GetName()
            ),
            _("Reset last run"), wx.YES_NO | wx.ICON_WARNING, self,
        )
        if confirm != wx.YES:
            return
        try:
            geo.delete_grouped_vias(self.board, group, vias)
            _refill_zones(self.board, self)
            pcbnew.Refresh()
        except Exception:
            _report(self, _("Removing the stitching run hit an unexpected error."),
                    traceback.format_exc())
        self._refresh_remove_run_btn()

    def _on_reset(self):
        _clear_settings()
        self.via_type.SetSelection(0)
        self._refresh_layer_controls()
        self.via_dia.SetValue(str(DEFAULT_VIA_DIAMETER_MM))
        self.drill.SetValue(str(DEFAULT_DRILL_MM))
        self.spacing.SetValue(str(DEFAULT_SPACING_MM))
        self._auto_values = {str(DEFAULT_VIA_DIAMETER_MM), str(DEFAULT_DRILL_MM), str(DEFAULT_SPACING_MM)}
        self.pattern.SetSelection(PATTERNS.index(DEFAULT_PATTERN))
        self.x_offset.SetValue("0.0")
        self.y_offset.SetValue("0.0")
        net_choices = [self.net.GetString(i) for i in range(self.net.GetCount())]
        self.net.SetValue(DEFAULT_NET if DEFAULT_NET in net_choices else "")
        self.avoid_zones.SetValue(DEFAULT_AVOID_OTHER_ZONES)
        self.avoid_footprints.SetValue(DEFAULT_AVOID_FOOTPRINTS)
        self.avoid_same_net_pads.SetValue(DEFAULT_AVOID_SAME_NET_PADS)
        self._update_advisory()

    def values(self):
        """Validated dialog values. ValueError carries a user-facing message."""
        via_type_name = self.via_type.GetStringSelection()
        via_type = self.VIA_TYPE_CHOICES[via_type_name]

        start_name = self._combo_layer_name(self.start_layer)
        end_name = self._combo_layer_name(self.end_layer)
        if start_name not in self.layer_map or end_name not in self.layer_map:
            raise ValueError(_("Pick a start and end layer."))
        start_layer = self.layer_map[start_name]
        end_layer = self.layer_map[end_name]
        if start_layer == end_layer:
            raise ValueError(
                _("Start and end layers must not be the same ({layer})").format(layer=start_name)
            )

        try:
            via_dia_mm = float(self.via_dia.GetValue())
            drill_mm = float(self.drill.GetValue())
            spacing_mm = float(self.spacing.GetValue())
            x_offset_mm = float(self.x_offset.GetValue() or 0)
            y_offset_mm = float(self.y_offset.GetValue() or 0)
        except ValueError:
            raise ValueError(_("Via diameter, drill and spacing and offsets must be numbers (mm)."))

        if not all(math.isfinite(v) for v in (via_dia_mm, drill_mm, spacing_mm)):
            raise ValueError(_("Via diameter, drill and spacing must be real numbers (mm)."))

        # Ported from the IPC dialog's values() -- missing here until now,
        # so a zero/negative size or drill >= diameter reached stitch()
        # unvalidated instead of failing with a clear message in the dialog.
        if min(via_dia_mm, drill_mm, spacing_mm) <= 0:
            raise ValueError(_("Via diameter, drill and spacing must all be greater than zero."))
        if drill_mm >= via_dia_mm:
            raise ValueError(
                _(
                    "The drill ({drill} mm) must be smaller than the via diameter "
                    "({dia} mm)."
                ).format(drill=drill_mm, dia=via_dia_mm)
            )

        net_name = self.net.GetValue().strip()
        if not net_name:
            raise ValueError(_("Pick a net to stitch."))

        return dict(
            via_type=via_type, start_layer=start_layer, end_layer=end_layer, net_name=net_name,
            via_dia_mm=via_dia_mm, drill_mm=drill_mm, spacing_mm=spacing_mm,
            pattern=self.pattern.GetStringSelection(),
            x_offset_mm=x_offset_mm, y_offset_mm=y_offset_mm,
            avoid_other_zones=self.avoid_zones.GetValue(),
            avoid_footprints=self.avoid_footprints.GetValue(),
            avoid_same_net_pads=self.avoid_same_net_pads.GetValue(),
        )

    def save_current_as_settings(self):
        try:
            values = self.values()
        except ValueError:
            return
        _save_settings({
            "via_type_name": self.VIA_TYPE_NAMES.get(values["via_type"]),
            "start_layer_name": self.layer_names.get(values["start_layer"]),
            "end_layer_name": self.layer_names.get(values["end_layer"]),
            "via_dia_mm": values["via_dia_mm"],
            "drill_mm": values["drill_mm"],
            "spacing_mm": values["spacing_mm"],
            "pattern": values["pattern"],
            "net_name": values["net_name"],
            "x_offset_mm": values["x_offset_mm"],
            "y_offset_mm": values["y_offset_mm"],
            "avoid_other_zones": values["avoid_other_zones"],
            "avoid_footprints": values["avoid_footprints"],
            "avoid_same_net_pads": values["avoid_same_net_pads"],
        })


def stitching_runs(board):
    """Every stitching run still on the board, as (group, its vias). Runs whose
    vias are all gone are dropped -- an empty group is nothing to remove."""
    runs = [(g, geo.grouped_vias(board, g)) for g in board.Groups()
            if g.GetName().startswith(GROUP_PREFIX)]
    return [(g, vias) for (g, vias) in runs if vias]


ViaStitchingLegacy().register()
