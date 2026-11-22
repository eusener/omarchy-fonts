# Marketplace submission (draft)

Form: https://github.com/omacom/omarchy-plugin-marketplace/issues/new?template=submit-plugin.yml

- **Repository URL:** https://github.com/eusener/omarchy-fonts
- **Category:** Appearance
- **Tags:** System, Hyprland, Quickshell
- **Suggested tag:** Fonts

**Maintainer notes**

Bar widget plus a Python 3 helper (standard library only: no pip, no build
step). Sets the fontconfig sans/serif/monospace/emoji families, GTK/Qt,
terminal (Alacritty, Kitty, Ghostty, Foot) and Hyprland group bar fonts and
sizes, font rendering (antialiasing, hinting, subpixel order, LCD filter), and
installs fonts from Google Fonts, Fontsource, Nerd Fonts and Fontshare.

- No privileges: never uses sudo/pkexec; writes only inside $HOME.
- Asks for consent once before changing any configuration; every file is
  backed up before it changes; "Restore defaults" undoes the overrides.
- Monospace changes go through `omarchy-font-set`.
- Network only on search/preview/install, HTTPS to a fixed host allowlist,
  with size limits and time-outs; Nerd Fonts archives are verified against
  the release SHA-256 file; only .ttf/.otf/.ttc are extracted, never executed.
- Dependencies (all on stock Omarchy): python3, fontconfig tools, gsettings,
  hyprctl, omarchy-font-set, pkill/pgrep. Listed in the README with every
  file the plugin changes and every host it contacts.

**Checklist**

- [x] Public repository with installation and removal instructions (README)
- [x] License (MIT) and external dependencies documented
- [x] I own the plugin and the preview image (screenshot of the plugin)
- [x] Does not overwrite user configuration without explicit consent
- [x] Approval is for listing, not a security review
