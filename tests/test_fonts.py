#!/usr/bin/env python3
"""Unit tests for fonts.py. No network, no real config: every XDG directory
points into a temporary folder before the module is imported.

  python3 -m unittest discover -s tests
"""

import importlib
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
TMP = tempfile.mkdtemp(prefix="eusener-fonts-test-")
for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
  os.environ[var] = os.path.join(TMP, var.lower())
sys.path.insert(0, str(ROOT))
fonts = importlib.import_module("fonts")

DEFAULTS = {"sans": "Liberation Sans", "serif": "Liberation Serif",
            "monospace": "JetBrainsMono Nerd Font", "emoji": "Noto Color Emoji"}


def settings(**changes):
  s = fonts.merge_defaults({}, fonts.DEFAULT_SETTINGS)
  for key, value in changes.items():
    if isinstance(value, dict):
      s[key].update(value)
    else:
      s[key] = value
  return s


class Validation(unittest.TestCase):
  def test_family_accepts_real_names(self):
    for name in ("Inter", "JetBrainsMono Nerd Font", "IBM Plex Sans", "Noto Sans CJK JP", "M PLUS 1p", "Ação Sans"):
      self.assertEqual(fonts.clean_family(name), name)

  def test_family_rejects_markup_and_quotes(self):
    for name in ('<img src="x">', 'Evil"Font', "a\\b", "x/y", "a:b", "line\nbreak", "-rf"):
      with self.assertRaises(fonts.UserError):
        fonts.clean_family(name)

  def test_empty_family_means_default(self):
    self.assertIsNone(fonts.clean_family(""))
    self.assertIsNone(fonts.clean_family("   "))

  def test_size_bounds_and_halves(self):
    self.assertEqual(fonts.clean_size("11"), 11)
    self.assertEqual(fonts.clean_size("10.3"), 10.5)
    self.assertIsNone(fonts.clean_size(""))
    for bad in ("0", "500", "abc"):
      with self.assertRaises(fonts.UserError):
        fonts.clean_size(bad)

  def test_ids(self):
    self.assertEqual(fonts.clean_id("commit-mono"), "commit-mono")
    self.assertEqual(fonts.clean_id("Fira Code"), "Fira Code")
    for bad in ("../etc", "a/b", "", "x" * 200, ";rm"):
      with self.assertRaises(fonts.UserError):
        fonts.clean_id(bad)

  def test_safe_name_strips_paths(self):
    self.assertIsNone(fonts.safe_name("../../.bashrc.ttf"))
    self.assertIsNone(fonts.safe_name("../../.bashrc"))
    self.assertIsNone(fonts.safe_name("dir/.hidden.ttf"))
    self.assertEqual(fonts.safe_name("a/b/Inter[opsz,wght].ttf"), "Inter[opsz,wght].ttf")
    self.assertEqual(fonts.safe_name("C:\\evil\\x.otf"), "x.otf")
    self.assertIsNone(fonts.safe_name("readme.txt"))

  def test_url_allowlist(self):
    fonts.check_url("https://api.fontsource.org/v1/fonts")
    for bad in ("http://api.fontsource.org/v1/fonts", "https://evil.example/x.ttf", "file:///etc/passwd",
                "https://api.fontsource.org.evil.example/"):
      with self.assertRaises(fonts.UserError):
        fonts.check_url(bad)


class Fontconfig(unittest.TestCase):
  def test_nothing_set_writes_nothing(self):
    self.assertIsNone(fonts.render_fontconfig(settings(), DEFAULTS))

  def test_sans_rules_cover_omarchy_assignment_and_generics(self):
    xml = fonts.render_fontconfig(settings(roles={"sans": "Inter"}), DEFAULTS)
    self.assertIn('<test name="family" qual="first"><string>Liberation Sans</string></test>', xml)
    self.assertIn('<test name="family" qual="any"><string>sans-serif</string></test>', xml)
    self.assertIn("<string>system-ui</string>", xml)
    self.assertNotIn("Arial", xml)
    self.assertNotIn('mode="assign"', xml)

  def test_metric_replacement(self):
    xml = fonts.render_fontconfig(settings(roles={"sans": "Inter"}, replaceMetricAliases=True), DEFAULTS)
    self.assertIn("<string>Arial</string>", xml)
    self.assertIn('mode="assign"', xml)

  def test_family_is_xml_escaped(self):
    xml = fonts.render_fontconfig(settings(roles={"serif": "Tom & Jerry's"}), DEFAULTS)
    self.assertIn("Tom &amp; Jerry&apos;s", xml)
    self.assertNotIn("Tom & Jerry", xml)

  def test_rendering_edits(self):
    xml = fonts.render_fontconfig(settings(rendering={"antialias": True, "rgba": "bgr", "hintstyle": "hintslight"}), DEFAULTS)
    self.assertIn('<match target="font">', xml)
    self.assertIn('<edit name="antialias" mode="assign"><bool>true</bool></edit>', xml)
    self.assertIn("<const>bgr</const>", xml)
    self.assertNotIn("lcdfilter", xml)

  def test_rendering_validation(self):
    with self.assertRaises(fonts.UserError):
      fonts.validate_rendering({"rgba": "xyz"})
    with self.assertRaises(fonts.UserError):
      fonts.validate_rendering({"evil": True})
    self.assertEqual(fonts.validate_rendering({"rgba": None}), {"rgba": None})

  def test_presets_are_valid(self):
    for name, values in fonts.PRESETS.items():
      fonts.validate_rendering(values)
      self.assertEqual(set(values), set(fonts.RENDER_CHOICES), name)

  def test_omarchy_defaults_parse(self):
    sample = ('<match target="pattern"><test name="family" qual="any">\n<string>sans-serif</string>\n</test>\n'
              '<edit name="family" mode="assign" binding="strong">\n<string>Inter</string></edit></match>')
    path = Path(TMP) / "50-omarchy.conf"
    path.write_text(sample)
    with mock.patch.object(fonts, "OMARCHY_FC", path):
      self.assertEqual(fonts.omarchy_defaults()["sans"], "Inter")


class Terminals(unittest.TestCase):
  ALACRITTY = ('[env]\nTERM = "xterm-256color"\n\n[font]\n'
               'normal = { family = "JetBrainsMono Nerd Font", style = "Regular" }\n'
               'bold = { family = "JetBrainsMono Nerd Font", style = "Bold" }\n\n[window]\npadding.x = 14\n')

  def test_alacritty_family_and_new_size(self):
    out = fonts.edit_alacritty(self.ALACRITTY, 'Fira "Code"', 10.5)
    self.assertEqual(out.count('family = "Fira \\"Code\\""'), 2)
    self.assertIn("[font]\n", out)
    font_block = out.split("[font]")[1].split("[window]")[0]
    self.assertIn("size = 10.5", font_block)
    self.assertIn('TERM = "xterm-256color"', out)

  def test_alacritty_existing_size_replaced(self):
    text = self.ALACRITTY.replace("[font]\n", "[font]\nsize = 9\n")
    out = fonts.edit_alacritty(text, None, 12)
    self.assertEqual(out.count("size = "), 1)
    self.assertIn("size = 12", out)

  def test_alacritty_size_when_font_is_last(self):
    out = fonts.edit_alacritty("[font]\nnormal = { family = \"X\" }", None, 11)
    self.assertTrue(out.rstrip().endswith("size = 11"))

  def test_kitty(self):
    out = fonts.edit_kitty("# font_family old\nfont_size 10.0\n", "Iosevka", 12)
    self.assertIn("font_family Iosevka", out)
    self.assertIn("font_size 12.0", out)
    self.assertIn("# font_family old", out)

  def test_ghostty(self):
    out = fonts.edit_ghostty('font-family = "JetBrainsMono Nerd Font"\nfont-size = 9\n', "Inter", 11)
    self.assertIn('font-family = "Inter"', out)
    self.assertIn("font-size = 11", out)

  def test_foot_keeps_fallbacks_and_escapes(self):
    text = "[main]\nfont=JetBrainsMono Nerd Font:size=9,Noto Color Emoji:size=9\nshell=bash\n"
    out = fonts.edit_foot(text, "Some-Font", 11)
    self.assertIn("font=Some\\-Font:size=11,Noto Color Emoji:size=9", out)
    self.assertIn("shell=bash", out)

  def test_foot_size_only(self):
    out = fonts.edit_foot("font=JetBrainsMono Nerd Font:size=9\n", None, 13)
    self.assertEqual(out, "font=JetBrainsMono Nerd Font:size=13\n")

  def test_apply_terminals_backs_up_and_skips_missing(self):
    path = fonts.CONFIG / "ghostty/config"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('font-family = "Old"\n')
    backup = fonts.Backup()
    with mock.patch.object(fonts, "run"):
      changed = fonts.apply_terminals("New", None, backup)
    self.assertEqual(changed, ["ghostty"])
    self.assertIn('"New"', path.read_text())
    saved = list(backup.dir.rglob("config"))
    self.assertEqual(len(saved), 1)
    self.assertIn('"Old"', saved[0].read_text())


class Hyprland(unittest.TestCase):
  def test_render_escapes_lua(self):
    lua = fonts.render_hypr(settings(groupbar={"family": 'A"B\\C', "size": 13}, menu={"family": "Inter"}))
    self.assertIn('font_family = "A\\"B\\\\C"', lua)
    self.assertIn("font_size = 13", lua)
    self.assertIn('hl.env("OMARCHY_MENU_FONT", "Inter")', lua)

  def test_render_defaults_clear_menu_env(self):
    lua = fonts.render_hypr(settings())
    self.assertNotIn("groupbar", lua)
    self.assertIn('hl.env("OMARCHY_MENU_FONT", "")', lua)

  def test_apply_adds_require_once_and_remove_cleans_up(self):
    main = fonts.HYPR_MAIN
    main.parent.mkdir(parents=True, exist_ok=True)
    main.write_text('require("hypr.monitors")\n')
    with mock.patch.object(fonts, "hypr_running", return_value=False):
      fonts.apply_hypr(settings(menu={"family": "Inter"}), fonts.Backup())
      fonts.apply_hypr(settings(menu={"family": "Lato"}), fonts.Backup())
      self.assertEqual(main.read_text().count(fonts.HYPR_REQUIRE), 1)
      self.assertIn("Lato", fonts.HYPR_FILE.read_text())
      fonts.remove_hypr(fonts.Backup())
    self.assertNotIn(fonts.HYPR_REQUIRE, main.read_text())
    self.assertFalse(fonts.HYPR_FILE.exists())
    self.assertIn('require("hypr.monitors")', main.read_text())

  def test_rollback_on_config_error(self):
    main = fonts.HYPR_MAIN
    main.parent.mkdir(parents=True, exist_ok=True)
    main.write_text("-- user config\n")
    fonts.HYPR_FILE.unlink(missing_ok=True)
    errors = iter(["", "eusener_fonts.lua:3: bad option"])
    with mock.patch.object(fonts, "hypr_running", return_value=True), \
         mock.patch.object(fonts, "hypr_errors", side_effect=lambda: next(errors, "")), \
         mock.patch.object(fonts, "run"), mock.patch.object(fonts.time, "sleep"):
      with self.assertRaises(fonts.UserError):
        fonts.apply_hypr(settings(groupbar={"family": "Inter", "size": 12}), fonts.Backup())
    self.assertEqual(main.read_text(), "-- user config\n")
    self.assertFalse(fonts.HYPR_FILE.exists())


class Gtk(unittest.TestCase):
  def test_split_font_name(self):
    self.assertEqual(fonts.split_font_name("JetBrainsMono Nerd Font 9"), {"family": "JetBrainsMono Nerd Font", "size": 9})
    self.assertEqual(fonts.split_font_name("Inter, 10.5"), {"family": "Inter", "size": 10.5})
    self.assertEqual(fonts.split_font_name("Cantarell"), {"family": "Cantarell", "size": None})

  def test_gvariant_string(self):
    self.assertEqual(fonts.gvariant_string("'Adwaita Sans 11'"), "Adwaita Sans 11")
    self.assertEqual(fonts.gvariant_string("'It\\'s 11'"), "It's 11")

  def test_rendering_maps_to_gsettings(self):
    calls = []
    with mock.patch.object(fonts, "gsettings_available", return_value=True), \
         mock.patch.object(fonts, "snapshot_originals"), \
         mock.patch.object(fonts, "gset", side_effect=lambda k, v: calls.append((k, v))):
      fonts.apply_gtk(settings(rendering={"antialias": True, "rgba": "bgr", "hintstyle": "hintfull", "hinting": True}), rendering=True)
    self.assertIn(("font-antialiasing", "rgba"), calls)
    self.assertIn(("font-rgba-order", "bgr"), calls)
    self.assertIn(("font-hinting", "full"), calls)
    self.assertIn(("font-rendering", "manual"), calls)


class Archives(unittest.TestCase):
  def _zip(self, entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
      for name, data in entries.items():
        z.writestr(name, data)
    return buf.getvalue()

  def test_zip_traversal_and_non_fonts_are_ignored(self):
    data = self._zip({"../../evil.ttf": b"x", "fonts/Good-Regular.ttf": b"font", "run.sh": b"#!/bin/sh",
                      "/abs/Abs.otf": b"y", ".hidden.ttf": b"z"})
    dest = Path(tempfile.mkdtemp(dir=TMP))
    written = fonts.extract_zip(data, dest)
    self.assertEqual(sorted(written), ["Abs.otf", "Good-Regular.ttf", "evil.ttf"])
    self.assertEqual(sorted(p.name for p in dest.iterdir()), sorted(written))
    self.assertFalse((Path(TMP) / "evil.ttf").exists())

  def test_zip_choose_filters(self):
    data = self._zip({"static/x-latin-400-normal.ttf": b"a", "static/x-cyrillic-400-normal.ttf": b"b"})
    dest = Path(tempfile.mkdtemp(dir=TMP))
    written = fonts.extract_zip(data, dest, lambda names: [n for n in names if "-latin-" in n])
    self.assertEqual(written, ["x-latin-400-normal.ttf"])


class Catalog(unittest.TestCase):
  def setUp(self):
    items = {
      "google": [
        {"source": "google", "id": "Inter", "family": "Inter", "category": "sans-serif", "variable": True,
         "styles": 18, "license": "OFL", "rank": 5},
        {"source": "google", "id": "Inter Tight", "family": "Inter Tight", "category": "sans-serif",
         "variable": True, "styles": 18, "license": "OFL", "rank": 70},
        {"source": "google", "id": "Painter", "family": "Painterly", "category": "display",
         "variable": False, "styles": 1, "license": "OFL", "rank": 900},
      ],
      "fontsource": [
        {"source": "fontsource", "id": "inter", "family": "Inter", "category": "sans-serif", "variable": True,
         "styles": 18, "license": "OFL-1.1", "rank": None, "kind": "google"},
        {"source": "fontsource", "id": "commit-mono", "family": "Commit Mono", "category": "monospace",
         "variable": False, "styles": 12, "license": "OFL-1.1", "rank": None, "kind": "other"},
      ],
      "nerd": [{"source": "nerd", "id": "Inconsolata", "family": "Inconsolata Nerd Font", "category": "monospace",
                "variable": False, "styles": None, "license": "OFL", "rank": None, "tag": "v3.5.1", "size": 1}],
      "fontshare": [],
    }
    for source, rows in items.items():
      fonts.save_json(fonts.catalog_path(source), {"fetchedAt": 4102444800, "items": rows})

  def test_ranking_and_dedupe(self):
    with mock.patch.object(fonts, "list_families", return_value=[]):
      result = fonts.search("inter")
    families = [(r["source"], r["family"]) for r in result["results"]]
    self.assertEqual(families[0], ("google", "Inter"))
    self.assertEqual(families[1], ("google", "Inter Tight"))
    self.assertNotIn(("fontsource", "Inter"), families)
    self.assertEqual(result["results"][0]["fontsourceId"], "inter")
    self.assertIn(("google", "Painterly"), families)

  def test_category_and_source_filters(self):
    with mock.patch.object(fonts, "list_families", return_value=["Commit Mono"]):
      result = fonts.search("", source="fontsource", category="monospace")
    self.assertEqual([r["family"] for r in result["results"]], ["Commit Mono"])
    self.assertTrue(result["results"][0]["installed"])
    self.assertFalse(result["results"][0]["managed"])

  def test_normalized_categories(self):
    self.assertEqual(fonts.norm_category("Sans Serif"), "sans-serif")
    self.assertEqual(fonts.norm_category("Sans, Display"), "sans-serif")
    self.assertEqual(fonts.norm_category("Serif,  Blackletter, Display"), "serif")
    self.assertEqual(fonts.norm_category("Slab"), "serif")
    self.assertEqual(fonts.norm_category("Handwritten"), "handwriting")
    self.assertEqual(fonts.norm_category("Script"), "handwriting")
    self.assertEqual(fonts.norm_category("Monospace"), "monospace")
    self.assertEqual(fonts.norm_category("icons"), "other")


class Uninstall(unittest.TestCase):
  def test_refuses_paths_outside_plugin_folder(self):
    fonts.save_json(fonts.REGISTRY_FILE, {"google:X": {"source": "google", "id": "X", "dir": TMP, "families": ["X"]}})
    with self.assertRaises(fonts.UserError):
      fonts.uninstall("google", "X")
    self.assertTrue(Path(TMP).exists())

  def test_removes_and_reports_roles_in_use(self):
    folder = fonts.FONT_DIR / "google" / "x"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "X.ttf").write_bytes(b"x")
    fonts.save_json(fonts.REGISTRY_FILE, {"google:X": {"source": "google", "id": "X", "dir": str(folder), "families": ["X"]}})
    fonts.save_settings(settings(roles={"sans": "X"}))
    with mock.patch.object(fonts, "run"):
      result = fonts.uninstall("google", "X")
    self.assertEqual(result["inUse"], ["sans"])
    self.assertFalse(folder.exists())
    self.assertEqual(fonts.registry(), {})


class Settings(unittest.TestCase):
  def test_merge_defaults_drops_unknown_and_fills_missing(self):
    merged = fonts.merge_defaults({"roles": {"sans": "Inter", "evil": 1}, "junk": True}, fonts.DEFAULT_SETTINGS)
    self.assertEqual(merged["roles"]["sans"], "Inter")
    self.assertNotIn("evil", merged["roles"])
    self.assertNotIn("junk", merged)
    self.assertIsNone(merged["rendering"]["rgba"])

  def test_write_commands_need_consent(self):
    fonts.CONSENT_FILE.unlink(missing_ok=True)
    with self.assertRaises(fonts.UserError):
      fonts.main(["set-metric", "on"])
    self.assertFalse(fonts.SETTINGS_FILE.exists() and fonts.load_settings()["replaceMetricAliases"])
    with mock.patch.object(fonts, "apply_fontconfig"), mock.patch.object(fonts, "emit"):
      fonts.main(["set-metric", "on", "--consent"])
    self.assertTrue(fonts.CONSENT_FILE.exists())
    self.assertTrue(fonts.load_settings()["replaceMetricAliases"])

  def test_cli_reports_errors_on_stderr(self):
    import subprocess
    result = subprocess.run([sys.executable, str(ROOT / "fonts.py"), "set-role", "sans", "<b>x</b>", "--yes"],
                            capture_output=True, text=True, env=os.environ.copy())
    self.assertEqual(result.returncode, 1)
    self.assertEqual(result.stdout, "")
    self.assertIn("Unsupported font family name", result.stderr)


if __name__ == "__main__":
  unittest.main()
