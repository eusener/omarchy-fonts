#!/usr/bin/env python3
"""System-wide font management for the eusener.fonts Omarchy plugin.

Python 3 standard library only. Every command prints one JSON document on
stdout; failures print a message on stderr and exit non-zero.

  fonts.py state
  fonts.py set-role <sans|serif|monospace|emoji> <family|"">
  fonts.py set-metric <on|off>
  fonts.py set-rendering '<json>'          keys: antialias hinting hintstyle rgba
                                           lcdfilter autohint embeddedbitmap
  fonts.py preset <default|hidpi|lcd|sharp|smooth>
  fonts.py set-gtk <interface|document|monospace> <family|""> [size]
  fonts.py set-terminal <family|""> [size|""]
  fonts.py set-groupbar <family|""> [size|""]
  fonts.py set-menu <family|"">
  fonts.py reset
  fonts.py search <query> [--source S] [--category C] [--limit N]
  fonts.py catalog [--refresh] [--source S]
  fonts.py preview <source> <id>
  fonts.py install <source> <id>
  fonts.py uninstall <source> <id>

Nothing here needs root. Files written:
  ~/.config/fontconfig/conf.d/60-eusener-fonts.conf   aliases + rendering
  ~/.config/fontconfig/fonts.conf                     monospace, via omarchy-font-set
  ~/.config/hypr/eusener_fonts.lua (+ one require line in hypr/hyprland.lua)
  terminal configs (alacritty, kitty, ghostty, foot): family and size only
  GNOME interface gsettings (font names, antialiasing, hinting, rgba order)
  ~/.local/share/fonts/eusener-fonts/<source>/<id>/   downloaded fonts
Every file is backed up before its first change in a run, under
~/.local/state/eusener-fonts/backups/<timestamp>/.
"""

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

APP = "eusener-fonts"
VERSION = "0.1.0"
USER_AGENT = f"{APP}/{VERSION} (Omarchy font plugin)"


def _xdg(var, default):
  value = os.environ.get(var)
  return Path(value) if value else Path.home() / default


CONFIG = _xdg("XDG_CONFIG_HOME", ".config")
DATA = _xdg("XDG_DATA_HOME", ".local/share")
STATE = _xdg("XDG_STATE_HOME", ".local/state") / APP
CACHE = _xdg("XDG_CACHE_HOME", ".cache") / APP
FONT_DIR = DATA / "fonts" / APP
SETTINGS_FILE = CONFIG / "omarchy" / APP / "settings.json"
REGISTRY_FILE = STATE / "installed.json"
ORIGINAL_FILE = STATE / "original.json"
BACKUP_DIR = STATE / "backups"
FC_FILE = CONFIG / "fontconfig/conf.d/60-eusener-fonts.conf"
HYPR_FILE = CONFIG / "hypr/eusener_fonts.lua"
HYPR_MAIN = CONFIG / "hypr/hyprland.lua"
HYPR_REQUIRE = 'pcall(require, "hypr.eusener_fonts")'
SHELL_TOML = CONFIG / "omarchy/shell.toml"
OMARCHY_FC = Path("/etc/fonts/conf.d/50-omarchy.conf")

GSCHEMA = "org.gnome.desktop.interface"
GTK_KEYS = {"interface": "font-name", "document": "document-font-name", "monospace": "monospace-font-name"}
GTK_RENDER_KEYS = ["font-antialiasing", "font-hinting", "font-rgba-order", "font-rendering"]

ROLES = ("sans", "serif", "monospace", "emoji")
SOURCES = ("google", "fontsource", "nerd", "fontshare")
CATEGORIES = ("sans-serif", "serif", "monospace", "display", "handwriting", "other")

RENDER_CHOICES = {
  "antialias": (True, False),
  "hinting": (True, False),
  "autohint": (True, False),
  "embeddedbitmap": (True, False),
  "hintstyle": ("hintnone", "hintslight", "hintmedium", "hintfull"),
  "rgba": ("none", "rgb", "bgr", "vrgb", "vbgr"),
  "lcdfilter": ("lcdnone", "lcddefault", "lcdlight", "lcdlegacy"),
}

PRESETS = {
  "default": {k: None for k in RENDER_CHOICES},
  "hidpi": {"antialias": True, "hinting": True, "hintstyle": "hintslight", "rgba": "none",
            "lcdfilter": "lcddefault", "autohint": False, "embeddedbitmap": False},
  "lcd": {"antialias": True, "hinting": True, "hintstyle": "hintslight", "rgba": "rgb",
          "lcdfilter": "lcddefault", "autohint": False, "embeddedbitmap": False},
  "sharp": {"antialias": True, "hinting": True, "hintstyle": "hintfull", "rgba": "rgb",
            "lcdfilter": "lcddefault", "autohint": False, "embeddedbitmap": True},
  "smooth": {"antialias": True, "hinting": False, "hintstyle": "hintnone", "rgba": "none",
             "lcdfilter": "lcddefault", "autohint": False, "embeddedbitmap": False},
}

DEFAULT_SETTINGS = {
  "version": 1,
  "roles": {r: None for r in ROLES},
  "replaceMetricAliases": False,
  "rendering": {k: None for k in RENDER_CHOICES},
  "gtk": {"interface": None, "document": None, "monospace": None},
  "terminal": {"family": None, "size": None},
  "groupbar": {"family": None, "size": None},
  "menu": {"family": None},
}

# Families a site or document names when it means "a generic sans/serif".
SANS_GENERICS = ("sans-serif", "sans", "system-ui", "-apple-system", "BlinkMacSystemFont", "ui-sans-serif")
SERIF_GENERICS = ("serif", "ui-serif")
SANS_METRIC = ("Arial", "Helvetica", "Helvetica Neue", "Liberation Sans", "Arimo")
SERIF_METRIC = ("Times New Roman", "Times", "Liberation Serif", "Tinos")

FAMILY_RE = re.compile(r"^[\w][\w .,'()+&-]{0,95}$", re.UNICODE)
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.+-]{0,95}$")
FONT_EXT = (".ttf", ".otf", ".ttc")

ALLOWED_HOSTS = {
  "api.fontsource.org", "r2.fontsource.org", "cdn.jsdelivr.net",
  "fonts.google.com", "api.github.com", "raw.githubusercontent.com", "github.com",
  "objects.githubusercontent.com", "release-assets.githubusercontent.com",
  "api.fontshare.com", "cdn.fontshare.com",
}
CATALOG_TTL = 7 * 24 * 3600
MB = 1024 * 1024
LIMITS = {"catalog": 8 * MB, "font": 40 * MB, "archive": 200 * MB, "preview": 4 * MB, "small": 1 * MB}
MAX_FILES = 400
TIMEOUT = 30


class UserError(Exception):
  """A failure that is reported to the panel as is."""


# ------------------------------------------------------------------ utils

def emit(data):
  sys.stdout.write(json.dumps(data, ensure_ascii=False) + "\n")


def run(args, check=True, env=None, timeout=60):
  return subprocess.run(args, check=check, text=True, capture_output=True, env=env, timeout=timeout)


def have(cmd):
  return shutil.which(cmd) is not None


def atomic_write(path, text, mode=0o644):
  path = Path(path)
  if path.is_symlink():
    path = path.resolve()
  path.parent.mkdir(parents=True, exist_ok=True)
  fd, tmp = tempfile.mkstemp(prefix=f".{APP}-", dir=path.parent)
  try:
    with os.fdopen(fd, "w", encoding="utf-8") as f:
      f.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)
  finally:
    if os.path.exists(tmp):
      os.unlink(tmp)


def read_text(path):
  try:
    return Path(path).read_text(encoding="utf-8")
  except (FileNotFoundError, IsADirectoryError):
    return None


def load_json(path, default):
  try:
    with open(path, encoding="utf-8") as f:
      return json.load(f)
  except (FileNotFoundError, json.JSONDecodeError):
    return default


def save_json(path, data):
  atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


class Backup:
  """Copies each file once per run before it is first modified."""

  def __init__(self):
    self.dir = BACKUP_DIR / time.strftime("%Y%m%d-%H%M%S")
    self.done = set()

  def save(self, path):
    path = Path(path)
    key = str(path)
    if key in self.done:
      return
    self.done.add(key)
    if not path.exists():
      return
    target = self.dir / str(path.resolve()).lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path.resolve(), target)
    prune_backups()


def prune_backups(keep=20):
  if not BACKUP_DIR.is_dir():
    return
  runs = sorted(p for p in BACKUP_DIR.iterdir() if p.is_dir())
  for old in runs[:-keep]:
    shutil.rmtree(old, ignore_errors=True)


def merge_defaults(data, defaults):
  out = {}
  for key, value in defaults.items():
    if isinstance(value, dict):
      out[key] = merge_defaults(data.get(key) or {} if isinstance(data, dict) else {}, value)
    else:
      out[key] = data.get(key, value) if isinstance(data, dict) else value
  return out


def load_settings():
  return merge_defaults(load_json(SETTINGS_FILE, {}), DEFAULT_SETTINGS)


def save_settings(settings):
  save_json(SETTINGS_FILE, settings)


# ------------------------------------------------------------- validation

def clean_family(value, allow_empty=True):
  value = unicodedata.normalize("NFC", str(value or "")).strip()
  if not value:
    if allow_empty:
      return None
    raise UserError("A font family is required")
  if not FAMILY_RE.match(value) or any(c in value for c in '"\\<>/:\n\r\t'):
    raise UserError(f"Unsupported font family name: {value!r}")
  return value


def require_installed(family):
  if family is None:
    return None
  if family not in set(list_families()):
    raise UserError(f"Font family not installed: {family}")
  return family


def clean_size(value, low=4, high=72, allow_empty=True):
  text = str(value if value is not None else "").strip()
  if not text:
    if allow_empty:
      return None
    raise UserError("A size is required")
  try:
    number = float(text)
  except ValueError:
    raise UserError(f"Invalid size: {text!r}")
  if not (low <= number <= high):
    raise UserError(f"Size must be between {low} and {high}")
  number = round(number * 2) / 2
  return int(number) if number == int(number) else number


def clean_id(value):
  value = str(value or "").strip()
  if not ID_RE.match(value) or ".." in value:
    raise UserError(f"Invalid font id: {value!r}")
  return value


def clean_source(value):
  if value not in SOURCES:
    raise UserError(f"Unknown source: {value!r}")
  return value


def safe_name(name):
  base = os.path.basename(name.replace("\\", "/"))
  base = re.sub(r"[^A-Za-z0-9._\[\],@+-]", "_", base)
  if not base or base.startswith(".") or not base.lower().endswith(FONT_EXT):
    return None
  return base[:120]


def slug(value):
  return re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")[:80] or "font"


# ------------------------------------------------------------ fontconfig

def list_families(spacing=None):
  if not have("fc-list"):
    return []
  pattern = [f":spacing={spacing}"] if spacing is not None else [":"]
  out = run(["fc-list"] + pattern + ["-f", "%{family[0]}\n"], check=False).stdout
  names = set()
  for line in out.splitlines():
    name = line.strip()
    if name and not name.startswith(".") and FAMILY_RE.match(name):
      names.add(name)
  return sorted(names, key=str.casefold)


def fc_match(pattern):
  if not have("fc-match"):
    return ""
  return run(["fc-match", "-f", "%{family[0]}", pattern], check=False).stdout.strip()


def omarchy_defaults():
  """Families Omarchy's packaged fontconfig assigns to each generic."""
  defaults = {"sans": "Liberation Sans", "serif": "Liberation Serif",
              "monospace": "JetBrainsMono Nerd Font", "emoji": "Noto Color Emoji"}
  text = read_text(OMARCHY_FC) or ""
  rule = re.compile(r"<string>(sans-serif|serif|monospace)</string>\s*</test>\s*"
                    r"<edit name=\"family\" mode=\"assign\"[^>]*>\s*<string>([^<]+)</string>")
  names = {"sans-serif": "sans", "serif": "serif", "monospace": "monospace"}
  for generic, family in rule.findall(text):
    defaults[names[generic]] = family.strip()
  return defaults


def xml_escape(value):
  return (value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
          .replace('"', "&quot;").replace("'", "&apos;"))


def fc_prepend(generic, family, qual="first"):
  return (f'  <match target="pattern">\n'
          f'    <test name="family" qual="{qual}"><string>{xml_escape(generic)}</string></test>\n'
          f'    <edit name="family" mode="prepend_first" binding="strong"><string>{xml_escape(family)}</string></edit>\n'
          f'  </match>\n')


def fc_after_default(default, generic, family):
  """Omarchy's 50-omarchy.conf runs before user config and assigns its own
  family in place of the generic, which survives only as a weak fallback
  further down the list. Match that shape: its default first, the generic
  still present. A page asking for Arial keeps Arial first and is left alone."""
  return (f'  <match target="pattern">\n'
          f'    <test name="family" qual="first"><string>{xml_escape(default)}</string></test>\n'
          f'    <test name="family" qual="any"><string>{xml_escape(generic)}</string></test>\n'
          f'    <edit name="family" mode="prepend_first" binding="strong"><string>{xml_escape(family)}</string></edit>\n'
          f'  </match>\n')


def fc_replace(default, family):
  """Swap Omarchy's default wherever it appears: catches metric aliases
  (Arial -> Liberation Sans) and unknown families that fell back to it."""
  return (f'  <match target="pattern">\n'
          f'    <test name="family" qual="any"><string>{xml_escape(default)}</string></test>\n'
          f'    <edit name="family" mode="assign" binding="strong"><string>{xml_escape(family)}</string></edit>\n'
          f'  </match>\n')


def fc_role_rules(default, generics, metric, family, replace_metric):
  rules = []
  for generic in generics:
    rules.append(fc_after_default(default, generic, family))
    rules.append(fc_prepend(generic, family))
  if replace_metric:
    rules += [fc_prepend(name, family) for name in metric]
    rules.append(fc_replace(default, family))
  return rules


def render_fontconfig(settings, defaults=None):
  roles = settings["roles"]
  defaults = defaults or omarchy_defaults()
  parts = []
  if roles["sans"]:
    parts.append("  <!-- Interface and web sans-serif -->\n")
    parts += fc_role_rules(defaults["sans"], SANS_GENERICS, SANS_METRIC, roles["sans"], settings["replaceMetricAliases"])
  if roles["serif"]:
    parts.append("  <!-- Document serif -->\n")
    parts += fc_role_rules(defaults["serif"], SERIF_GENERICS, SERIF_METRIC, roles["serif"], settings["replaceMetricAliases"])
  if roles["emoji"]:
    parts.append("  <!-- Emoji -->\n")
    parts.append(fc_prepend("emoji", roles["emoji"], qual="any"))
  edits = []
  for key, value in settings["rendering"].items():
    if value is None:
      continue
    if isinstance(value, bool):
      edits.append(f'    <edit name="{key}" mode="assign"><bool>{"true" if value else "false"}</bool></edit>\n')
    else:
      edits.append(f'    <edit name="{key}" mode="assign"><const>{value}</const></edit>\n')
  if edits:
    parts.append("  <!-- Rendering -->\n  <match target=\"font\">\n" + "".join(edits) + "  </match>\n")
  if not parts:
    return None
  return ('<?xml version="1.0"?>\n<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n'
          "<!-- Managed by the eusener.fonts Omarchy plugin. Edits are overwritten. -->\n"
          "<fontconfig>\n" + "".join(parts) + "</fontconfig>\n")


def apply_fontconfig(settings, backup):
  text = render_fontconfig(settings)
  current = read_text(FC_FILE)
  if text == current:
    return False
  backup.save(FC_FILE)
  if text is None:
    FC_FILE.unlink(missing_ok=True)
  else:
    atomic_write(FC_FILE, text)
  return True


# ----------------------------------------------------------------- gtk

def gsettings_available():
  if not have("gsettings"):
    return False
  return run(["gsettings", "list-keys", GSCHEMA], check=False).returncode == 0


def gsettings_read():
  values = {}
  if not have("gsettings"):
    return values
  out = run(["gsettings", "list-recursively", GSCHEMA], check=False).stdout
  for line in out.splitlines():
    parts = line.split(" ", 2)
    if len(parts) == 3 and parts[0] == GSCHEMA:
      values[parts[1]] = parts[2]
  return values


def gvariant_string(raw):
  raw = (raw or "").strip()
  if len(raw) >= 2 and raw[0] in "'\"" and raw[-1] == raw[0]:
    return raw[1:-1].replace("\\'", "'").replace('\\"', '"').replace("\\\\", "\\")
  return raw


def split_font_name(value):
  match = re.match(r"^(.*?)[ ,]+(\d+(?:\.\d+)?)$", value or "")
  if not match:
    return {"family": value or "", "size": None}
  size = float(match.group(2))
  return {"family": match.group(1).strip(), "size": int(size) if size == int(size) else size}


def gset(key, value):
  run(["gsettings", "set", GSCHEMA, key, value], check=False)


def greset(key):
  run(["gsettings", "reset", GSCHEMA, key], check=False)


def snapshot_originals():
  """Remember the user's values once, so reset can put them back."""
  if ORIGINAL_FILE.exists() or not have("gsettings"):
    return
  values = gsettings_read()
  keep = {k: gvariant_string(values[k]) for k in list(GTK_KEYS.values()) + GTK_RENDER_KEYS if k in values}
  save_json(ORIGINAL_FILE, {"gsettings": keep, "savedAt": time.strftime("%Y-%m-%dT%H:%M:%S")})


def original_gsetting(key):
  return load_json(ORIGINAL_FILE, {}).get("gsettings", {}).get(key)


def apply_gtk(settings, slots=None, rendering=False):
  if not gsettings_available():
    return
  snapshot_originals()
  for slot in slots or ():
    key = GTK_KEYS[slot]
    value = settings["gtk"][slot]
    if value and value.get("family"):
      size = value.get("size") or 11
      gset(key, f"{value['family']} {size}")
    else:
      original = original_gsetting(key)
      gset(key, original) if original else greset(key)
  if rendering:
    r = settings["rendering"]
    if all(v is None for v in r.values()):
      for key in GTK_RENDER_KEYS:
        original = original_gsetting(key)
        gset(key, original) if original else greset(key)
      return
    if r["antialias"] is False:
      gset("font-antialiasing", "none")
    elif r["rgba"] and r["rgba"] != "none":
      gset("font-antialiasing", "rgba")
    else:
      gset("font-antialiasing", "grayscale")
    if r["rgba"] and r["rgba"] != "none":
      gset("font-rgba-order", r["rgba"])
    hint = "none" if r["hinting"] is False else {
      "hintnone": "none", "hintslight": "slight", "hintmedium": "medium", "hintfull": "full"
    }.get(r["hintstyle"] or "", "slight")
    gset("font-hinting", hint)
    # GTK 4 ignores hinting and antialiasing while font-rendering is automatic.
    gset("font-rendering", "manual")


# ------------------------------------------------------------ terminals

def toml_string(value):
  return json.dumps(value, ensure_ascii=False)


def foot_escape(value):
  return re.sub(r"([\\:,=-])", r"\\\1", value)


def number_text(size):
  return str(int(size)) if float(size) == int(size) else str(size)


def edit_alacritty(text, family, size):
  lines = text.splitlines(keepends=True)
  section = None
  font_header = None
  size_done = False
  out = []
  for line in lines:
    header = re.match(r"^\s*\[\[?([^\]]+)\]\]?\s*(#.*)?$", line)
    if header:
      if section == "font" and size is not None and not size_done:
        out.append(f"size = {number_text(size)}\n")
        size_done = True
      section = header.group(1).strip()
      if section == "font":
        font_header = len(out)
      out.append(line)
      continue
    in_font = section == "font" or (section or "").startswith("font.")
    if in_font and family is not None:
      line = re.sub(r'(\bfamily\s*=\s*)"(?:[^"\\]|\\.)*"', lambda m: m.group(1) + toml_string(family), line)
    if section == "font" and size is not None and re.match(r"^\s*size\s*=", line):
      line = f"size = {number_text(size)}\n"
      size_done = True
    out.append(line)
  if size is not None and not size_done:
    if section == "font":
      if out and not out[-1].endswith("\n"):
        out[-1] += "\n"
      out.append(f"size = {number_text(size)}\n")
    elif font_header is None:
      out.append(f"\n[font]\nsize = {number_text(size)}\n")
  if family is not None and font_header is None and not re.search(r"\bfamily\s*=", "".join(out)):
    out.append(f'\n[font.normal]\nfamily = {toml_string(family)}\n')
  return "".join(out)


def edit_line_config(text, pattern, replacement):
  regex = re.compile(pattern, re.M)
  if regex.search(text):
    return regex.sub(lambda m: replacement, text, count=1)
  if text and not text.endswith("\n"):
    text += "\n"
  return text + replacement + "\n"


def edit_kitty(text, family, size):
  if family is not None:
    text = edit_line_config(text, r"^[ \t]*font_family[ \t]+.*$", f"font_family {family}")
  if size is not None:
    text = edit_line_config(text, r"^[ \t]*font_size[ \t]+.*$", f"font_size {float(size):.1f}")
  return text


def edit_ghostty(text, family, size):
  if family is not None:
    text = edit_line_config(text, r"^[ \t]*font-family[ \t]*=.*$", f"font-family = {toml_string(family)}")
  if size is not None:
    text = edit_line_config(text, r"^[ \t]*font-size[ \t]*=.*$", f"font-size = {number_text(size)}")
  return text


def edit_foot(text, family, size):
  regex = re.compile(r"^([ \t]*font[ \t]*=[ \t]*)(.*)$", re.M)
  match = regex.search(text)
  if not match:
    if family is None:
      return text
    line = f"font={foot_escape(family)}:size={number_text(size or 9)}"
    if re.search(r"^\[main\]", text, re.M):
      return re.sub(r"^\[main\][^\n]*\n", lambda m: m.group(0) + line + "\n", text, count=1, flags=re.M)
    return line + "\n" + text
  value = match.group(2)
  primary, sep, rest = value.partition(",")
  pieces = re.split(r"(?<!\\):", primary)
  current_family, attrs = pieces[0], pieces[1:]
  if family is not None:
    current_family = foot_escape(family)
  if size is not None:
    attrs = [a for a in attrs if not re.match(r"^(size|pixelsize)=", a)]
    attrs.append(f"size={number_text(size)}")
  new_value = ":".join([current_family] + attrs) + (sep + rest if sep else "")
  return text[:match.start(2)] + new_value + text[match.end(2):]


def terminal_files():
  return {
    "alacritty": CONFIG / "alacritty/alacritty.toml",
    "kitty": CONFIG / "kitty/kitty.conf",
    "ghostty": CONFIG / "ghostty/config",
    "foot": CONFIG / "foot/foot.ini",
  }


EDITORS = {"alacritty": edit_alacritty, "kitty": edit_kitty, "ghostty": edit_ghostty, "foot": edit_foot}


def apply_terminals(family, size, backup):
  changed = []
  for name, path in terminal_files().items():
    text = read_text(path)
    if text is None:
      continue
    new = EDITORS[name](text, family, size)
    if new != text:
      backup.save(path)
      atomic_write(path, new)
      changed.append(name)
  if "kitty" in changed:
    run(["pkill", "-USR1", "-x", "kitty"], check=False)
  if "ghostty" in changed:
    run(["pkill", "-USR2", "-x", "ghostty"], check=False)
  return changed


def read_terminal():
  files = terminal_files()
  info = {"present": [n for n, p in files.items() if p.exists()], "family": None, "size": None}
  text = read_text(files["ghostty"])
  if text:
    m = re.search(r'^font-family\s*=\s*"?([^"\n]+)"?', text, re.M)
    s = re.search(r"^font-size\s*=\s*([0-9.]+)", text, re.M)
    info["family"] = m.group(1).strip() if m else None
    info["size"] = float(s.group(1)) if s else None
  for name in ("alacritty", "kitty", "foot"):
    if info["family"] and info["size"]:
      break
    text = read_text(files[name]) or ""
    if name == "alacritty":
      m = re.search(r'family\s*=\s*"((?:[^"\\]|\\.)*)"', text)
      s = re.search(r"^size\s*=\s*([0-9.]+)", text, re.M)
    elif name == "kitty":
      m = re.search(r"^[ \t]*font_family[ \t]+(.+)$", text, re.M)
      s = re.search(r"^[ \t]*font_size[ \t]+([0-9.]+)", text, re.M)
    else:
      m = re.search(r"^font\s*=\s*([^:,\n]+)", text, re.M)
      s = re.search(r"^font\s*=.*?:size=([0-9.]+)", text, re.M)
    if m and not info["family"]:
      info["family"] = m.group(1).strip().replace("\\", "")
    if s and not info["size"]:
      info["size"] = float(s.group(1))
  if info["size"] is not None and info["size"] == int(info["size"]):
    info["size"] = int(info["size"])
  return info


# ------------------------------------------------------------- hyprland

def lua_string(value):
  out = ['"']
  for ch in value:
    if ch in '"\\':
      out.append("\\" + ch)
    elif ch == "\n":
      out.append("\\n")
    elif ord(ch) < 32:
      out.append(f"\\{ord(ch):03d}")
    else:
      out.append(ch)
  out.append('"')
  return "".join(out)


def render_hypr(settings):
  groupbar = settings["groupbar"]
  menu = settings["menu"]["family"]
  lines = ["-- Managed by the eusener.fonts Omarchy plugin. Edits are overwritten.", ""]
  fields = []
  if groupbar["family"]:
    fields.append(f"font_family = {lua_string(groupbar['family'])}")
  if groupbar["size"]:
    fields.append(f"font_size = {int(groupbar['size'])}")
  if fields:
    lines.append("hl.config({ group = { groupbar = { " + ", ".join(fields) + " } } })")
  # The shell reads OMARCHY_MENU_FONT at startup; empty means "use the bar font".
  lines.append(f"hl.env(\"OMARCHY_MENU_FONT\", {lua_string(menu or '')})")
  return "\n".join(lines) + "\n"


def hypr_running():
  return have("hyprctl") and run(["hyprctl", "version"], check=False).returncode == 0


def hypr_errors():
  out = run(["hyprctl", "configerrors"], check=False).stdout.strip()
  if not out or out.lower().startswith("no errors"):
    return ""
  return out


def apply_hypr(settings, backup):
  text = render_hypr(settings)
  old_text = read_text(HYPR_FILE)
  main = read_text(HYPR_MAIN)
  if main is None:
    raise UserError(f"{HYPR_MAIN} not found")
  needs_require = HYPR_REQUIRE not in main
  if text == old_text and not needs_require:
    return False
  backup.save(HYPR_FILE)
  atomic_write(HYPR_FILE, text)
  if needs_require:
    backup.save(HYPR_MAIN)
    new_main = main + ("" if main.endswith("\n") else "\n") + \
      "\n-- eusener.fonts: font overrides (groupbar, menu font)\n" + HYPR_REQUIRE + "\n"
    atomic_write(HYPR_MAIN, new_main)
  if hypr_running():
    before = hypr_errors()
    run(["hyprctl", "reload"], check=False)
    time.sleep(0.3)
    after = hypr_errors()
    if after and after != before:
      if old_text is None:
        HYPR_FILE.unlink(missing_ok=True)
      else:
        atomic_write(HYPR_FILE, old_text)
      if needs_require:
        atomic_write(HYPR_MAIN, main)
      run(["hyprctl", "reload"], check=False)
      raise UserError("Hyprland rejected the font settings and they were rolled back: " + after.splitlines()[0])
  return True


def remove_hypr(backup):
  main = read_text(HYPR_MAIN)
  if main and HYPR_REQUIRE in main:
    backup.save(HYPR_MAIN)
    main = main.replace("\n-- eusener.fonts: font overrides (groupbar, menu font)\n", "\n")
    main = main.replace(HYPR_REQUIRE + "\n", "").replace(HYPR_REQUIRE, "")
    atomic_write(HYPR_MAIN, main)
  if HYPR_FILE.exists():
    backup.save(HYPR_FILE)
    # Leave an empty env assignment so the running session forgets the override.
    atomic_write(HYPR_FILE, render_hypr(merge_defaults({}, DEFAULT_SETTINGS)))
    if hypr_running():
      run(["hyprctl", "reload"], check=False)
    HYPR_FILE.unlink(missing_ok=True)
  elif hypr_running():
    run(["hyprctl", "reload"], check=False)


def hypr_option(name):
  if not hypr_running():
    return None
  try:
    data = json.loads(run(["hyprctl", "-j", "getoption", name], check=False).stdout or "{}")
  except json.JSONDecodeError:
    return None
  for key in ("str", "int", "float"):
    if key in data:
      return data[key]
  return None


def max_monitor_scale():
  if not hypr_running():
    return 1.0
  try:
    monitors = json.loads(run(["hyprctl", "-j", "monitors"], check=False).stdout or "[]")
    return max([float(m.get("scale", 1)) for m in monitors] or [1.0])
  except (json.JSONDecodeError, ValueError):
    return 1.0


def shell_base_size():
  text = read_text(SHELL_TOML) or ""
  in_font = False
  for line in text.splitlines():
    if re.match(r"^\s*\[", line):
      in_font = bool(re.match(r"^\s*\[font\]", line))
    elif in_font:
      m = re.match(r"^\s*base-size\s*=\s*(\d+)", line)
      if m:
        return int(m.group(1))
  return 12


# ------------------------------------------------------------- monospace

def set_monospace(family):
  """Delegate to Omarchy's own command: it updates fonts.conf, terminals,
  restarts the shell and fires the font-set hook."""
  target = family or omarchy_defaults()["monospace"]
  if have("omarchy-font-set"):
    result = run(["omarchy-font-set", target], check=False, timeout=180)
    if result.returncode != 0:
      raise UserError((result.stdout + result.stderr).strip() or "omarchy-font-set failed")
  else:
    raise UserError("omarchy-font-set not found")


# --------------------------------------------------------------- network

class _Redirects(urllib.request.HTTPRedirectHandler):
  def redirect_request(self, req, fp, code, msg, headers, newurl):
    check_url(newurl)
    return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_Redirects)


def check_url(url):
  parts = urllib.parse.urlsplit(url)
  if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
    raise UserError(f"Refusing to download from {parts.hostname or url}")
  return url


def fetch(url, limit, dest=None, accept=None):
  check_url(url)
  headers = {"User-Agent": USER_AGENT}
  if accept:
    headers["Accept"] = accept
  request = urllib.request.Request(url, headers=headers)
  try:
    response = _OPENER.open(request, timeout=TIMEOUT)
  except urllib.error.HTTPError as e:
    raise UserError(f"HTTP {e.code} from {urllib.parse.urlsplit(url).hostname}") from None
  except (urllib.error.URLError, TimeoutError, OSError) as e:
    raise UserError(f"Network error: {getattr(e, 'reason', e)}") from None
  with response:
    check_url(response.geturl())
    length = response.headers.get("Content-Length")
    if length and length.isdigit() and int(length) > limit:
      raise UserError("Download is larger than allowed")
    sink = open(dest, "wb") if dest else io.BytesIO()
    total = 0
    try:
      while True:
        chunk = response.read(256 * 1024)
        if not chunk:
          break
        total += len(chunk)
        if total > limit:
          raise UserError("Download is larger than allowed")
        sink.write(chunk)
      return None if dest else sink.getvalue()
    finally:
      if dest:
        sink.close()


def fetch_json(url, limit=LIMITS["catalog"], accept="application/json"):
  raw = fetch(url, limit, accept=accept).decode("utf-8", "replace")
  start = min([i for i in (raw.find("{"), raw.find("[")) if i >= 0] or [0])
  try:
    return json.loads(raw[start:])
  except json.JSONDecodeError:
    raise UserError(f"Invalid JSON from {urllib.parse.urlsplit(url).hostname}") from None


# --------------------------------------------------------------- catalog

def bounded(value, length=96):
  return str(value or "")[:length]


def norm_category(value):
  v = str(value or "").lower()
  if "mono" in v:
    return "monospace"
  if "hand" in v or "script" in v:
    return "handwriting"
  if "display" in v and not v.startswith(("sans", "serif")):
    return "display"
  if "sans" in v:
    return "sans-serif"
  if "serif" in v or "slab" in v:
    return "serif"
  if "display" in v:
    return "display"
  return "other"


def fetch_google():
  data = fetch_json("https://fonts.google.com/metadata/fonts")
  out = []
  for item in data.get("familyMetadataList", [])[:5000]:
    family = bounded(item.get("family"))
    if not family or not FAMILY_RE.match(family) or item.get("isOpenSource") is False:
      continue
    out.append({
      "source": "google", "id": family, "family": family,
      "category": norm_category(item.get("category")),
      "variable": bool(item.get("axes")), "styles": len(item.get("fonts") or {}),
      "license": "OFL / Apache", "rank": int(item.get("popularity") or 99999),
    })
  return out


def fetch_fontsource():
  data = fetch_json("https://api.fontsource.org/v1/fonts")
  out = []
  for item in data[:6000] if isinstance(data, list) else []:
    family = bounded(item.get("family"))
    fid = bounded(item.get("id"))
    if not family or not FAMILY_RE.match(family) or not ID_RE.match(fid):
      continue
    out.append({
      "source": "fontsource", "id": fid, "family": family,
      "category": norm_category(item.get("category")),
      "variable": bool(item.get("variable")),
      "styles": len(item.get("weights") or []) * max(1, len(item.get("styles") or [])),
      "license": bounded(item.get("license"), 32), "rank": None,
      "kind": "google" if item.get("type") == "google" else "other",
      "defSubset": bounded(item.get("defSubset"), 32) or "latin",
      "weights": [int(w) for w in (item.get("weights") or []) if str(w).isdigit()][:20],
      "fontStyles": [bounded(s, 16) for s in (item.get("styles") or [])][:4],
    })
  return out


def fetch_nerd():
  release = fetch_json("https://api.github.com/repos/ryanoasis/nerd-fonts/releases/latest",
                       accept="application/vnd.github+json")
  tag = bounded(release.get("tag_name"), 32)
  out = []
  for asset in release.get("assets", [])[:400]:
    name = str(asset.get("name") or "")
    if not name.endswith(".tar.xz"):
      continue
    base = name[:-len(".tar.xz")]
    if not ID_RE.match(base):
      continue
    family = "Symbols Nerd Font" if base == "NerdFontsSymbolsOnly" else f"{base} Nerd Font"
    out.append({
      "source": "nerd", "id": base, "family": family,
      "category": "monospace", "variable": False, "styles": None,
      "license": "OFL / MIT / Apache", "rank": None,
      "tag": tag, "size": int(asset.get("size") or 0),
    })
  return out


def fetch_fontshare():
  out = []
  offset = 0
  for _ in range(10):
    page = fetch_json(f"https://api.fontshare.com/v2/fonts?limit=100&offset={offset}")
    fonts = page.get("fonts") or []
    for item in fonts:
      family = bounded(item.get("name"))
      fid = bounded(item.get("slug"))
      if not family or not FAMILY_RE.match(family) or not ID_RE.match(fid):
        continue
      lic = item.get("license_type")
      out.append({
        "source": "fontshare", "id": fid, "family": family,
        "category": norm_category(item.get("category")),
        "variable": any(s.get("is_variable") for s in item.get("styles") or [] if isinstance(s, dict)),
        "styles": len(item.get("styles") or []),
        "license": "OFL-1.1" if lic == "sil_ofl" else "ITF Free Font License",
        "rank": None,
      })
    offset += len(fonts)
    if not page.get("has_more") or not fonts:
      break
  return out


FETCHERS = {"google": fetch_google, "fontsource": fetch_fontsource, "nerd": fetch_nerd, "fontshare": fetch_fontshare}


def catalog_path(source):
  return CACHE / f"catalog-{source}.json"


def load_catalog(source, refresh=False, allow_fetch=True):
  path = catalog_path(source)
  cached = load_json(path, None)
  fresh = cached and time.time() - cached.get("fetchedAt", 0) < CATALOG_TTL
  if cached and (fresh or not allow_fetch) and not refresh:
    return cached
  if not allow_fetch:
    return {"fetchedAt": 0, "items": []}
  try:
    items = FETCHERS[source]()
  except UserError:
    if cached and not refresh:
      return cached
    raise
  data = {"fetchedAt": int(time.time()), "items": items}
  save_json(path, data)
  return data


def registry():
  return load_json(REGISTRY_FILE, {})


def search(query, source="all", category="", limit=60):
  q = unicodedata.normalize("NFC", query or "").strip().casefold()[:64]
  sources = SOURCES if source == "all" else (clean_source(source),)
  errors = {}
  items = []
  for s in sources:
    try:
      items += load_catalog(s)["items"]
    except UserError as e:
      errors[s] = str(e)
  installed_keys = set(registry())
  families = set(f.casefold() for f in list_families())
  seen = {}
  priority = {"google": 0, "fontsource": 1, "fontshare": 2, "nerd": 3}
  for item in items:
    if category and item["category"] != category:
      continue
    name = item["family"].casefold()
    if q:
      if name == q:
        score = 0
      elif name.startswith(q):
        score = 1
      elif any(w.startswith(q) for w in name.split()):
        score = 2
      elif q in name:
        score = 3
      else:
        continue
    else:
      score = 0
    key = name if item["source"] != "nerd" else "nerd:" + name
    prev = seen.get(key)
    if prev and priority[prev[1]["source"]] <= priority[item["source"]]:
      if item["source"] == "fontsource" and prev[1]["source"] == "google":
        prev[1]["fontsourceId"] = item["id"]
      continue
    entry = dict(item)
    if prev and prev[1]["source"] == "fontsource":
      entry["fontsourceId"] = prev[1]["id"]
    entry["installed"] = f"{item['source']}:{item['id']}" in installed_keys or name in families
    entry["managed"] = f"{item['source']}:{item['id']}" in installed_keys
    seen[key] = (score, entry)
  ranked = sorted(seen.values(), key=lambda p: (p[0], p[1]["rank"] if p[1]["rank"] is not None else 50000,
                                                 p[1]["family"].casefold()))
  results = [e for _, e in ranked[:max(1, min(int(limit), 200))]]
  for entry in results:
    for drop in ("weights", "fontStyles", "defSubset", "tag", "kind"):
      entry.pop(drop, None)
  return {"query": query, "source": source, "results": results, "total": len(ranked), "errors": errors}


def fontsource_entry(family=None, fid=None):
  for item in load_catalog("fontsource")["items"]:
    if (fid and item["id"] == fid) or (family and item["family"].casefold() == family.casefold()):
      return item
  return None


# --------------------------------------------------------------- preview

def preview(source, fid):
  source, fid = clean_source(source), clean_id(fid)
  entry = None
  if source == "fontsource":
    entry = fontsource_entry(fid=fid)
  elif source == "google":
    entry = fontsource_entry(family=fid)
  if not entry:
    return {"path": None}
  weights = entry.get("weights") or [400]
  weight = 400 if 400 in weights else min(weights, key=lambda w: abs(w - 400))
  style = "normal" if "normal" in (entry.get("fontStyles") or ["normal"]) else entry["fontStyles"][0]
  subset = entry.get("defSubset") or "latin"
  if not re.match(r"^[a-z0-9-]+$", subset) or style not in ("normal", "italic"):
    return {"path": None}
  dest = CACHE / "previews" / f"{slug(entry['id'])}-{subset}-{weight}-{style}.ttf"
  if not dest.exists():
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://cdn.jsdelivr.net/fontsource/fonts/{entry['id']}@latest/{subset}-{weight}-{style}.ttf"
    tmp = dest.with_suffix(".part")
    try:
      fetch(url, LIMITS["preview"], dest=tmp)
      os.replace(tmp, dest)
    finally:
      tmp.unlink(missing_ok=True)
  prune_previews()
  return {"path": str(dest)}


def prune_previews(keep=80):
  folder = CACHE / "previews"
  files = sorted(folder.glob("*.ttf"), key=lambda p: p.stat().st_mtime)
  for old in files[:-keep]:
    old.unlink(missing_ok=True)


# --------------------------------------------------------------- install

def extract_zip(data, dest, choose=None):
  written = []
  with zipfile.ZipFile(io.BytesIO(data)) as archive:
    infos = [i for i in archive.infolist() if not i.is_dir()]
    if len(infos) > 5000:
      raise UserError("Archive has too many entries")
    names = [i.filename for i in infos]
    keep = set(choose(names)) if choose else set(names)
    for info in infos:
      if info.filename not in keep:
        continue
      name = safe_name(info.filename)
      if not name or info.file_size > LIMITS["font"]:
        continue
      with archive.open(info) as src:
        payload = src.read(LIMITS["font"] + 1)
      if len(payload) > LIMITS["font"]:
        continue
      (dest / name).write_bytes(payload)
      written.append(name)
      if len(written) >= MAX_FILES:
        break
  return written


def extract_tar(path, dest):
  written = []
  with tarfile.open(path, "r:*") as archive:
    for member in archive:
      if not member.isfile() or member.size > LIMITS["font"]:
        continue
      name = safe_name(member.name)
      if not name:
        continue
      src = archive.extractfile(member)
      if src is None:
        continue
      (dest / name).write_bytes(src.read(LIMITS["font"]))
      written.append(name)
      if len(written) >= MAX_FILES:
        break
  return written


def install_google(family, dest, fallback=True):
  folder = re.sub(r"[^a-z0-9]", "", family.lower())
  last = None
  for lic in ("ofl", "apache", "ufl"):
    try:
      listing = fetch_json(f"https://api.github.com/repos/google/fonts/contents/{lic}/{folder}",
                           LIMITS["small"], accept="application/vnd.github+json")
    except UserError as e:
      last = e
      if "HTTP 404" in str(e):
        continue
      break
    files = [f for f in listing if isinstance(f, dict) and f.get("type") == "file"
             and str(f.get("name", "")).lower().endswith(FONT_EXT)]
    written = []
    for f in files[:60]:
      name = safe_name(f["name"])
      url = str(f.get("download_url") or "")
      if not name or not url:
        continue
      fetch(url, LIMITS["font"], dest=dest / name)
      written.append(name)
    if written:
      return written
  entry = fontsource_entry(family=family) if fallback else None
  if entry:
    return fontsource_zip(entry, dest)
  raise last or UserError(f"{family} was not found in google/fonts")


def install_fontsource(fid, dest):
  entry = fontsource_entry(fid=fid)
  if not entry:
    raise UserError(f"Unknown Fontsource font: {fid}")
  # Fontsource serves Google families split by unicode range; prefer the
  # complete files from google/fonts and fall back to the subsets.
  if entry.get("kind") == "google":
    try:
      return install_google(entry["family"], dest, fallback=False)
    except UserError:
      pass
  return fontsource_zip(entry, dest)


def fontsource_zip(entry, dest):
  fid = entry["id"]
  data = fetch(f"https://r2.fontsource.org/fonts/{fid}@latest/download.zip", LIMITS["archive"])
  wanted = {"latin", "latin-ext", entry.get("defSubset") or "latin"}

  def choose(names):
    ttf = [n for n in names if n.lower().endswith(".ttf")]
    variable = [n for n in ttf if "/variable/" in "/" + n]
    pool = variable or ttf
    picked = [n for n in pool if any(f"{fid}-{s}-" in os.path.basename(n) for s in wanted)]
    return picked or pool

  return extract_zip(data, dest, choose)


def install_nerd(fid, dest):
  catalog = load_catalog("nerd")
  entry = next((i for i in catalog["items"] if i["id"] == fid), None)
  if not entry:
    raise UserError(f"Unknown Nerd Font: {fid}")
  tag = entry["tag"]
  if not re.match(r"^v[0-9.]+$", tag):
    raise UserError("Unexpected Nerd Fonts release tag")
  base = f"https://github.com/ryanoasis/nerd-fonts/releases/download/{tag}"
  sums = fetch(f"{base}/SHA-256.txt", LIMITS["small"]).decode("utf-8", "replace")
  expected = None
  for line in sums.splitlines():
    parts = line.split()
    if len(parts) == 2 and parts[1].lstrip("*") == f"{fid}.tar.xz":
      expected = parts[0].lower()
  if not expected or not re.match(r"^[0-9a-f]{64}$", expected):
    raise UserError("No published checksum for this archive")
  CACHE.mkdir(parents=True, exist_ok=True)
  tmp = Path(tempfile.mkstemp(prefix="nerd-", suffix=".tar.xz", dir=CACHE)[1])
  try:
    fetch(f"{base}/{fid}.tar.xz", LIMITS["archive"], dest=tmp)
    digest = hashlib.sha256()
    with open(tmp, "rb") as f:
      for chunk in iter(lambda: f.read(1024 * 1024), b""):
        digest.update(chunk)
    if digest.hexdigest() != expected:
      raise UserError("Checksum mismatch; the download was discarded")
    return extract_tar(tmp, dest)
  finally:
    tmp.unlink(missing_ok=True)


def install_fontshare(fid, dest):
  data = fetch(f"https://api.fontshare.com/v2/fonts/download/{urllib.parse.quote(fid)}", LIMITS["archive"])

  def choose(names):
    ttf = [n for n in names if "/TTF/" in "/" + n and n.lower().endswith(".ttf")]
    otf = [n for n in names if "/OTF/" in "/" + n and n.lower().endswith(".otf")]
    # Static OTFs carry the plain family name; the variable TTFs come as "<Name> Variable".
    return (otf + ttf) or [n for n in names if n.lower().endswith((".ttf", ".otf")) and "/WEB/" not in n]

  return extract_zip(data, dest, choose)


INSTALLERS = {"google": install_google, "fontsource": install_fontsource,
              "nerd": install_nerd, "fontshare": install_fontshare}


def scan_families(folder):
  families = set()
  for path in folder.iterdir():
    out = run(["fc-scan", "--format", "%{family[0]}\n", str(path)], check=False).stdout
    families.update(line.strip() for line in out.splitlines() if line.strip())
  return sorted(families, key=str.casefold)


def install(source, fid):
  source, fid = clean_source(source), clean_id(fid)
  key = f"{source}:{fid}"
  reg = registry()
  dest = FONT_DIR / source / slug(fid)
  staging = Path(tempfile.mkdtemp(prefix=".install-", dir=ensure_dir(FONT_DIR)))
  try:
    written = INSTALLERS[source](fid, staging)
    if not written:
      raise UserError("The download contained no installable font files")
    families = scan_families(staging)
    if not families:
      raise UserError("The downloaded files are not valid fonts")
    if dest.exists():
      shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging, dest)
  finally:
    if staging.exists():
      shutil.rmtree(staging, ignore_errors=True)
  run(["fc-cache", "-f", str(FONT_DIR)], check=False, timeout=180)
  reg[key] = {"source": source, "id": fid, "families": families, "dir": str(dest),
              "files": len(written), "installedAt": time.strftime("%Y-%m-%dT%H:%M:%S")}
  save_json(REGISTRY_FILE, reg)
  return {"installed": reg[key]}


def ensure_dir(path):
  path.mkdir(parents=True, exist_ok=True)
  return path


def uninstall(source, fid):
  source, fid = clean_source(source), clean_id(fid)
  key = f"{source}:{fid}"
  reg = registry()
  entry = reg.get(key)
  if not entry:
    raise UserError("This font was not installed by the plugin")
  folder = Path(entry["dir"]).resolve()
  if FONT_DIR.resolve() not in folder.parents:
    raise UserError("Refusing to delete outside the plugin font folder")
  shutil.rmtree(folder, ignore_errors=True)
  del reg[key]
  save_json(REGISTRY_FILE, reg)
  run(["fc-cache", "-f", str(FONT_DIR)], check=False, timeout=180)
  settings = load_settings()
  in_use = sorted({r for r, f in settings["roles"].items() if f in entry.get("families", [])})
  return {"removed": key, "inUse": in_use}


# ----------------------------------------------------------------- state

def state():
  settings = load_settings()
  g = gsettings_read()
  defaults = omarchy_defaults()
  scale = max_monitor_scale()
  gtk = {slot: split_font_name(gvariant_string(g.get(key))) for slot, key in GTK_KEYS.items()}
  reg = registry()
  return {
    "version": VERSION,
    "settings": settings,
    "defaults": defaults,
    "resolved": {
      "sans": fc_match("sans-serif"), "serif": fc_match("serif"),
      "monospace": fc_match("monospace"), "emoji": fc_match("emoji"),
    },
    "gtk": gtk,
    "gtkRendering": {k: gvariant_string(g.get(k)) for k in GTK_RENDER_KEYS},
    "textScaling": float(g.get("text-scaling-factor") or 1),
    "terminal": read_terminal(),
    "groupbar": {"family": hypr_option("group:groupbar:font_family"),
                 "size": hypr_option("group:groupbar:font_size")},
    "shellBaseSize": shell_base_size(),
    "monitorScale": scale,
    "hidpi": scale >= 1.5,
    "families": list_families(),
    "monoFamilies": list_families(spacing=100),
    "installed": sorted(reg.values(), key=lambda e: e.get("installedAt", ""), reverse=True),
    "catalogs": {s: int(load_json(catalog_path(s), {}).get("fetchedAt", 0)) for s in SOURCES},
    "gsettings": gsettings_available(),
  }


# -------------------------------------------------------------- commands

def cmd_set_role(role, family):
  if role not in ROLES:
    raise UserError(f"Unknown role: {role}")
  family = require_installed(clean_family(family))
  settings = load_settings()
  settings["roles"][role] = family
  backup = Backup()
  if role == "monospace":
    save_settings(settings)
    set_monospace(family)
    # omarchy-font-set rewrites terminal families; put a terminal override back.
    if settings["terminal"]["family"]:
      apply_terminals(settings["terminal"]["family"], None, backup)
    return {"ok": True, "restartedShell": True}
  apply_fontconfig(settings, backup)
  save_settings(settings)
  return {"ok": True}


def cmd_set_metric(flag):
  settings = load_settings()
  settings["replaceMetricAliases"] = flag in ("on", "true", "1", "yes")
  apply_fontconfig(settings, Backup())
  save_settings(settings)
  return {"ok": True}


def validate_rendering(values):
  out = {}
  for key, value in values.items():
    if key not in RENDER_CHOICES:
      raise UserError(f"Unknown rendering option: {key}")
    if value is not None and value not in RENDER_CHOICES[key]:
      raise UserError(f"Invalid value for {key}: {value!r}")
    out[key] = value
  return out


def cmd_set_rendering(payload):
  try:
    values = json.loads(payload)
  except json.JSONDecodeError:
    raise UserError("Rendering options must be JSON")
  if not isinstance(values, dict):
    raise UserError("Rendering options must be a JSON object")
  settings = load_settings()
  settings["rendering"].update(validate_rendering(values))
  apply_fontconfig(settings, Backup())
  apply_gtk(settings, rendering=True)
  save_settings(settings)
  return {"ok": True}


def cmd_preset(name):
  if name not in PRESETS:
    raise UserError(f"Unknown preset: {name}")
  return cmd_set_rendering(json.dumps(PRESETS[name]))


def cmd_set_gtk(slot, family, size=None):
  if slot not in GTK_KEYS:
    raise UserError(f"Unknown GTK font slot: {slot}")
  family = require_installed(clean_family(family))
  size = clean_size(size, 6, 48)
  settings = load_settings()
  settings["gtk"][slot] = {"family": family, "size": size or 11} if family else None
  apply_gtk(settings, slots=[slot])
  save_settings(settings)
  return {"ok": True}


def cmd_set_terminal(family, size=None):
  family = require_installed(clean_family(family))
  size = clean_size(size, 5, 40)
  settings = load_settings()
  settings["terminal"] = {"family": family, "size": size if size is not None else settings["terminal"]["size"]}
  target = family or fc_match("monospace") or omarchy_defaults()["monospace"]
  changed = apply_terminals(target, size, Backup())
  save_settings(settings)
  foot_running = run(["pgrep", "-x", "foot"], check=False).returncode == 0
  return {"ok": True, "changed": changed, "footNeedsRestart": "foot" in changed and foot_running}


def cmd_set_groupbar(family, size=None):
  family = require_installed(clean_family(family))
  size = clean_size(size, 6, 40)
  settings = load_settings()
  settings["groupbar"] = {"family": family, "size": size}
  apply_hypr(settings, Backup())
  save_settings(settings)
  return {"ok": True}


def cmd_set_menu(family):
  family = require_installed(clean_family(family))
  settings = load_settings()
  settings["menu"]["family"] = family
  apply_hypr(settings, Backup())
  save_settings(settings)
  return {"ok": True, "needsShellRestart": True}


def cmd_reset():
  backup = Backup()
  settings = load_settings()
  mono_was_set = settings["roles"]["monospace"] is not None
  clean = merge_defaults({}, DEFAULT_SETTINGS)
  apply_fontconfig(clean, backup)
  apply_gtk(clean, slots=list(GTK_KEYS), rendering=True)
  remove_hypr(backup)
  if settings["terminal"]["family"]:
    apply_terminals(fc_match("monospace") or omarchy_defaults()["monospace"], None, backup)
  save_settings(clean)
  if mono_was_set:
    set_monospace(None)
  return {"ok": True, "restartedShell": mono_was_set}


def parse_flags(args, names):
  flags, rest = {}, []
  i = 0
  while i < len(args):
    if args[i] in names and i + 1 < len(args):
      flags[args[i]] = args[i + 1]
      i += 2
    elif args[i] in names:
      flags[args[i]] = True
      i += 1
    else:
      rest.append(args[i])
      i += 1
  return flags, rest


def main(argv):
  if not argv or argv[0] in ("-h", "--help", "help"):
    print(__doc__.strip())
    return 0
  cmd, args = argv[0], argv[1:]

  def arg(i, default=None):
    return args[i] if len(args) > i else default

  if cmd == "state":
    result = state()
  elif cmd == "set-role":
    result = cmd_set_role(arg(0, ""), arg(1, ""))
  elif cmd == "set-metric":
    result = cmd_set_metric(arg(0, "off"))
  elif cmd == "set-rendering":
    result = cmd_set_rendering(arg(0, "{}"))
  elif cmd == "preset":
    result = cmd_preset(arg(0, ""))
  elif cmd == "set-gtk":
    result = cmd_set_gtk(arg(0, ""), arg(1, ""), arg(2))
  elif cmd == "set-terminal":
    result = cmd_set_terminal(arg(0, ""), arg(1))
  elif cmd == "set-groupbar":
    result = cmd_set_groupbar(arg(0, ""), arg(1))
  elif cmd == "set-menu":
    result = cmd_set_menu(arg(0, ""))
  elif cmd == "reset":
    result = cmd_reset()
  elif cmd == "search":
    flags, rest = parse_flags(args, ("--source", "--category", "--limit"))
    category = flags.get("--category", "")
    if category and category not in CATEGORIES:
      raise UserError(f"Unknown category: {category}")
    result = search(" ".join(rest), flags.get("--source", "all"), category, int(flags.get("--limit", 60)))
  elif cmd == "catalog":
    flags, _ = parse_flags(args, ("--refresh", "--source"))
    sources = SOURCES if flags.get("--source", "all") == "all" else (clean_source(flags["--source"]),)
    result, errors = {}, {}
    for s in sources:
      try:
        result[s] = len(load_catalog(s, refresh=bool(flags.get("--refresh")))["items"])
      except UserError as e:
        errors[s] = str(e)
    result = {"counts": result, "errors": errors}
  elif cmd == "preview":
    result = preview(arg(0, ""), arg(1, ""))
  elif cmd == "install":
    result = install(arg(0, ""), arg(1, ""))
  elif cmd == "uninstall":
    result = uninstall(arg(0, ""), arg(1, ""))
  else:
    raise UserError(f"Unknown command: {cmd}")
  emit(result)
  return 0


if __name__ == "__main__":
  try:
    sys.exit(main(sys.argv[1:]))
  except UserError as e:
    print(str(e), file=sys.stderr)
    sys.exit(1)
  except subprocess.TimeoutExpired as e:
    print(f"Timed out: {e.cmd[0] if e.cmd else 'command'}", file=sys.stderr)
    sys.exit(1)
