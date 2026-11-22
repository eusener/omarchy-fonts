#!/bin/bash
# Copy the plugin into Omarchy's user plugin folder (no symlinks: the plugin
# validator forbids them). The shell reloads plugin code on save.
set -euo pipefail
src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
dest="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins/eusener.fonts"
mkdir -p "$dest"
for item in manifest.json BarWidget.qml fonts.py i18n README.md LICENSE; do
  [[ -e $src/$item ]] && cp -r "$src/$item" "$dest/"
done
chmod +x "$dest/fonts.py"
omarchy plugin validate "$dest"
echo "Installed to $dest"
