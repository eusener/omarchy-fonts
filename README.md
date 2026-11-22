# Omarchy Fonts (`eusener.fonts`)

System-wide font settings for [Omarchy](https://omarchy.org), from a bar widget:

- **System families**: the fonts every app gets when it asks for a generic
  family: sans-serif (also `system-ui`), serif, monospace and emoji. GTK, Qt,
  browsers, Electron and the Omarchy shell all resolve these through fontconfig.
  Optionally also replaces Arial/Helvetica/Times and fonts that are not installed.
- **Apps and sizes**: GTK interface, document and monospace fonts with sizes
  (Qt follows via the `gtk3` platform theme); terminal family and size
  (Alacritty, Kitty, Ghostty, Foot); Hyprland group bar font and size; the
  Omarchy menu/launcher font (`OMARCHY_MENU_FONT`).
- **Rendering**: antialiasing, hinting and hint style, autohinter, embedded
  bitmaps, subpixel order (RGB/BGR/V-RGB/V-BGR/none) and LCD filter, with
  presets (HiDPI, LCD, Sharp, Smooth). Applied to fontconfig and to GTK's
  own settings.
- **Install fonts** from Google Fonts, Fontsource, Nerd Fonts and Fontshare,
  with search, categories and a live preview before installing.

The global text scale (shell base size, GTK text scaling) is **not** changed
here; it belongs to `omarchy display text size` / the Monitor panel. Note that
command also rewrites terminal font sizes.

## Install

```bash
omarchy plugin add https://github.com/<you>/omarchy-fonts.git --enable
# or, from a checkout:
./install.sh && omarchy plugin enable eusener.fonts --section right
```

## Dependencies

All are present on a stock Omarchy install. Nothing is installed with pip and
nothing is compiled.

| Dependency | Used for |
|---|---|
| `python3` (standard library only) | `fonts.py`, the helper that does all writes |
| fontconfig (`fc-list`, `fc-match`, `fc-scan`, `fc-cache`) | listing, resolving and registering fonts |
| `gsettings` (glib2) | GTK font names and rendering |
| `hyprctl` | reload and validate the Hyprland override, read monitor scale |
| `omarchy-font-set` | monospace changes go through Omarchy's own command |
| `pkill`, `pgrep` | reload Kitty and Ghostty, detect a running Foot |

## Privileges

None. The plugin never uses `sudo` or `pkexec` and only writes inside your home.

## Files it changes

| Path | What |
|---|---|
| `~/.config/fontconfig/conf.d/60-eusener-fonts.conf` | family rules and rendering (owned by the plugin) |
| `~/.config/fontconfig/fonts.conf` | monospace, written by `omarchy-font-set` |
| `~/.config/hypr/eusener_fonts.lua` | group bar font, `OMARCHY_MENU_FONT` (owned by the plugin) |
| `~/.config/hypr/hyprland.lua` | one appended line: `pcall(require, "hypr.eusener_fonts")` |
| Alacritty, Kitty, Ghostty, Foot configs | font family and size lines only |
| `org.gnome.desktop.interface` gsettings | `font-name`, `document-font-name`, `monospace-font-name`, `font-antialiasing`, `font-hinting`, `font-rgba-order`, `font-rendering` |
| `~/.local/share/fonts/eusener-fonts/` | downloaded fonts |
| `~/.config/omarchy/eusener-fonts/settings.json` | the plugin's settings |
| `~/.local/state/eusener-fonts/` | installed-font registry, original gsettings values, backups |
| `~/.cache/eusener-fonts/` | catalogs (refreshed after 7 days) and preview files |

Every file is copied to `~/.local/state/eusener-fonts/backups/<timestamp>/`
before it is changed (the last 20 runs are kept). A Hyprland change that makes
`hyprctl configerrors` report a new error is rolled back.

## Network

Only when you search, preview or install, and only over HTTPS to these hosts
(redirects elsewhere are refused). Downloads have size limits and time-outs.

| Host | Why |
|---|---|
| `fonts.google.com` | Google Fonts catalog (`/metadata/fonts`) |
| `api.github.com`, `raw.githubusercontent.com` | complete Google font files from `google/fonts`; Nerd Fonts release list |
| `api.fontsource.org`, `r2.fontsource.org`, `cdn.jsdelivr.net` | Fontsource catalog, downloads and preview files |
| `github.com`, `objects.githubusercontent.com`, `release-assets.githubusercontent.com` | Nerd Fonts archives, verified against the release's `SHA-256.txt` |
| `api.fontshare.com`, `cdn.fontshare.com` | Fontshare catalog and downloads |

Downloaded archives are never executed: only `.ttf`, `.otf` and `.ttc` files
are extracted, under sanitized names, into the plugin's own font folder. The
unauthenticated GitHub API allows 60 requests an hour; when it is exhausted,
Google families fall back to Fontsource's files.

**Licenses.** Google Fonts, Fontsource and Nerd Fonts ship open licenses (OFL,
Apache, MIT). Fontshare fonts are either OFL or the ITF Free Font License,
which is free for personal and commercial use but not an OSI license; each
result shows its license.

## Removing

1. Panel → System → **Restore defaults** (removes the fontconfig and Hyprland
   overrides, puts back your previous GTK values and Omarchy's monospace).
2. `omarchy plugin remove eusener.fonts`
3. Optionally delete downloaded fonts and state:
   `rm -rf ~/.local/share/fonts/eusener-fonts ~/.local/state/eusener-fonts ~/.cache/eusener-fonts ~/.config/omarchy/eusener-fonts && fc-cache -f`

## CLI

The helper works without the panel:

```bash
./fonts.py state
./fonts.py set-role sans "Inter"
./fonts.py preset hidpi
./fonts.py set-terminal "" 11            # follow system monospace, 11 pt
./fonts.py search "plex" --source google --category sans-serif
./fonts.py install nerd JetBrainsMono
./fonts.py reset
```

## Keyboard

`1`–`4` or `←`/`→` switch tabs, `↑`/`↓` scroll, `R` reloads, `Esc` closes,
`Tab` moves to the neighbouring bar panel.

## Development

```bash
python3 -m unittest discover -s tests
omarchy plugin validate .
/usr/lib/qt6/bin/qmllint -I "$OMARCHY_PATH/shell" BarWidget.qml
./install.sh
qs log -p "$OMARCHY_PATH/shell" --tail 100
```

## License

MIT
