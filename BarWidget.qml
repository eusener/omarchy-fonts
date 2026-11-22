import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui

// System-wide fonts for Omarchy: the fontconfig families every app resolves
// (sans, serif, monospace, emoji), GTK/Qt interface fonts, terminals, the
// Hyprland group bar and the Omarchy menu, rendering options, and fonts
// installed from Google Fonts, Fontsource, Nerd Fonts and Fontshare.
// Every write goes through fonts.py; this file only shows state and asks.
Panel {
  id: root
  moduleName: "eusener.fonts"
  ipcTarget: "eusener.fonts"
  manageIpc: false

  readonly property string pluginDir: decodeURIComponent(String(Qt.resolvedUrl(".")).replace(/^file:\/\//, "")).replace(/\/$/, "")
  readonly property string helperPath: pluginDir + "/fonts.py"

  readonly property color foreground: bar ? bar.foreground : Commons.Color.foreground
  readonly property color urgent: bar ? bar.urgent : Commons.Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property var tr: strings

  // ---- helper state
  property var st: null
  readonly property var cfg: st ? st.settings : null
  property bool busy: false
  property string message: ""
  property bool messageIsError: false
  property bool pendingReset: false
  property real touchpadScrollFactor: 1
  // Nothing under ~/.config changes until the user has agreed once.
  property var pendingConsent: null
  readonly property bool consented: st ? st.consented === true : false

  // ---- navigation
  property string tab: "system"
  readonly property var tabs: ["system", "apps", "render", "install"]

  // ---- catalog
  property string query: ""
  property string source: "all"
  property string category: ""
  property var results: []
  property int resultTotal: 0
  property bool searching: false
  property string searchError: ""
  property string previewKey: ""
  property string previewPath: ""
  property string installingKey: ""

  readonly property string sampleText: strings.t("sample")

  Commons.Translations { id: strings; directory: Qt.resolvedUrl("i18n") }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  // ------------------------------------------------------------ helper calls

  function lastJson(text) {
    var lines = String(text || "").trim().split("\n")
    return JSON.parse(lines[lines.length - 1] || "{}")
  }

  function refresh() {
    if (!stateProc.running) stateProc.running = true
  }

  // Every configuration write asks once, then remembers.
  function needsConsent(args, successText, detached, notice) {
    if (consented || args.indexOf("--consent") >= 0) return false
    pendingConsent = { args: args, successText: successText || "", detached: detached === true, notice: notice || "" }
    return true
  }

  function confirmConsent() {
    var p = pendingConsent
    pendingConsent = null
    if (!p) return
    var args = p.args.concat(["--consent"])
    if (p.detached) runDetached(args, p.notice)
    else runHelper(args, p.successText)
  }

  function runHelper(args, successText) {
    if (busy) return
    var writes = ["set-role", "set-metric", "set-rendering", "preset", "set-gtk", "set-terminal", "set-groupbar", "set-menu", "reset"]
    if (writes.indexOf(args[0]) >= 0 && needsConsent(args, successText, false)) return
    busy = true
    messageIsError = false
    message = strings.t("applying")
    applyProc.successText = successText || strings.t("applied")
    applyProc.lastArgs = args
    applyProc.command = ["python3", root.helperPath].concat(args)
    applyProc.running = true
  }

  // Changes that restart the shell must outlive it, so they run detached.
  function runDetached(args, notice) {
    if (needsConsent(args, "", true, notice)) return
    messageIsError = false
    message = notice
    Quickshell.execDetached(["python3", root.helperPath].concat(args))
  }

  function setRole(role, family) {
    if (!cfg || (cfg.roles[role] || "") === family) return
    if (role === "monospace") runDetached(["set-role", role, family], strings.t("restartingShell"))
    else runHelper(["set-role", role, family])
  }

  function setGtk(slot, family, size) {
    runHelper(["set-gtk", slot, family, family ? String(size) : ""])
  }

  function setTerminal(family, size) {
    runHelper(["set-terminal", family, size > 0 ? String(size) : ""])
  }

  function setGroupbar(family, size) {
    runHelper(["set-groupbar", family, size > 0 ? String(size) : ""])
  }

  function setMenu(family) {
    runHelper(["set-menu", family], strings.t("menuSaved"))
  }

  function setRendering(key, value) {
    var payload = {}
    payload[key] = value
    runHelper(["set-rendering", JSON.stringify(payload)])
  }

  function applyPreset(name) {
    runHelper(["preset", name], strings.t("presetApplied").arg(strings.t("preset." + name)))
  }

  function resetAll() {
    pendingReset = false
    var restarts = cfg && cfg.roles.monospace
    if (restarts) runDetached(["reset"], strings.t("restartingShell"))
    else runHelper(["reset"], strings.t("resetDone"))
  }

  function restartShell() {
    Quickshell.execDetached(["omarchy-restart-shell"])
  }

  function search() {
    if (searchProc.running) { searchDebounce.restart(); return }
    searching = true
    var args = ["python3", root.helperPath, "search", query, "--source", source, "--limit", "60"]
    if (category) args = args.concat(["--category", category])
    searchProc.command = args
    searchProc.running = true
  }

  function refreshCatalogs() {
    if (busy) return
    runHelper(["catalog", "--refresh"], strings.t("catalogsUpdated"))
  }

  function loadPreview(item) {
    var key = item.source + ":" + item.id
    if (previewKey === key) return
    previewKey = key
    previewPath = ""
    if (item.source !== "google" && item.source !== "fontsource") return
    if (previewProc.running) previewProc.running = false
    previewProc.key = key
    previewProc.command = ["python3", root.helperPath, "preview", item.source, item.id]
    previewProc.running = true
  }

  function install(item) {
    if (busy) return
    installingKey = item.source + ":" + item.id
    runHelper(["install", item.source, item.id], strings.t("installed").arg(item.family))
  }

  function uninstall(source, id, label) {
    if (busy) return
    runHelper(["uninstall", source, id], strings.t("removed").arg(label))
  }

  // ------------------------------------------------------------ view helpers

  // Built once per state refresh, not once per picker binding.
  readonly property var familyEntries: st ? st.families.map(function (n) { return { value: n, label: n } }) : []
  readonly property var monoEntries: st ? st.monoFamilies.map(function (n) { return { value: n, label: n } }) : []

  function familyOptions(defaultLabel, mono) {
    return [{ value: "", label: defaultLabel }].concat(mono ? monoEntries : familyEntries)
  }

  function roleLabel(role) {
    if (!st) return ""
    return strings.t("defaultFamily").arg(st.defaults[role] || "")
  }

  function renderValue(key) {
    if (!cfg) return "default"
    var v = cfg.rendering[key]
    if (v === null || v === undefined) return "default"
    if (v === true) return "on"
    if (v === false) return "off"
    return String(v)
  }

  function renderFromChoice(choice) {
    if (choice === "default") return null
    if (choice === "on") return true
    if (choice === "off") return false
    return choice
  }

  readonly property string activePreset: {
    if (!cfg) return ""
    var presets = {
      "default": { antialias: null, hinting: null, autohint: null, embeddedbitmap: null, hintstyle: null, rgba: null, lcdfilter: null },
      "hidpi": { antialias: true, hinting: true, autohint: false, embeddedbitmap: false, hintstyle: "hintslight", rgba: "none", lcdfilter: "lcddefault" },
      "lcd": { antialias: true, hinting: true, autohint: false, embeddedbitmap: false, hintstyle: "hintslight", rgba: "rgb", lcdfilter: "lcddefault" },
      "sharp": { antialias: true, hinting: true, autohint: false, embeddedbitmap: true, hintstyle: "hintfull", rgba: "rgb", lcdfilter: "lcddefault" },
      "smooth": { antialias: true, hinting: false, autohint: false, embeddedbitmap: false, hintstyle: "hintnone", rgba: "none", lcdfilter: "lcddefault" }
    }
    var r = cfg.rendering
    for (var name in presets) {
      var p = presets[name]
      var same = true
      for (var k in p) if ((r[k] === undefined ? null : r[k]) !== p[k]) { same = false; break }
      if (same) return name
    }
    return "custom"
  }

  function onOff() {
    return [
      { value: "default", label: strings.t("choice.default") },
      { value: "on", label: strings.t("choice.on") },
      { value: "off", label: strings.t("choice.off") }
    ]
  }

  function sourceLabel(s) { return strings.t("source." + s) }

  function resultMeta(item) {
    var parts = [sourceLabel(item.source), strings.t("category." + item.category)]
    if (item.styles) parts.push(strings.tn("styles", item.styles).arg(Commons.I18n.formatNumber(item.styles)))
    if (item.variable) parts.push(strings.t("variable"))
    if (item.license) parts.push(item.license)
    if (item.source === "nerd" && item.size) parts.push(Commons.I18n.formatNumber(item.size / 1048576, 1) + " MB")
    return parts.join(" · ")
  }

  onOpenedChanged: if (opened) {
    pendingReset = false
    pendingConsent = null
    refresh()
    if (tab === "install" && results.length === 0) search()
    Qt.callLater(function() { if (panel.loadedContent) panel.loadedContent.forceActiveFocus() })
  }

  onTabChanged: if (tab === "install" && results.length === 0 && opened) search()

  Component.onCompleted: {
    refresh()
    scrollFactorProc.running = true
  }

  Process {
    id: scrollFactorProc
    command: ["hyprctl", "-j", "getoption", "input:touchpad:scroll_factor"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var f = Number(JSON.parse(text || "{}").float)
          if (isFinite(f) && f > 0) root.touchpadScrollFactor = f
        } catch (e) {}
      }
    }
  }

  Timer {
    id: searchDebounce
    interval: 250
    onTriggered: root.search()
  }

  Process {
    id: stateProc
    command: ["python3", root.helperPath, "state"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try { root.st = root.lastJson(text) } catch (e) { console.warn("eusener.fonts: bad state", e) }
      }
    }
  }

  Process {
    id: applyProc
    property string successText: ""
    property var lastArgs: []
    property string out: ""
    property string err: ""
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: applyProc.out = text }
    stderr: StdioCollector { waitForEnd: true; onStreamFinished: applyProc.err = String(text || "").trim() }
    onExited: function(exitCode) {
      Qt.callLater(function() {
        root.busy = false
        root.installingKey = ""
        if (exitCode === 3) {
          // The helper wants consent the panel did not know was missing.
          root.message = ""
          root.pendingConsent = { args: applyProc.lastArgs, successText: applyProc.successText, detached: false, notice: "" }
          root.refresh()
          return
        }
        if (exitCode === 0) {
          root.messageIsError = false
          var extra = ""
          try {
            var data = root.lastJson(applyProc.out)
            if (data.footNeedsRestart) extra = " " + strings.t("footRestart")
            if (data.inUse && data.inUse.length) extra = " " + strings.t("stillInUse")
          } catch (e) {}
          root.message = applyProc.successText + extra
        } else {
          root.messageIsError = true
          root.message = strings.t("failed").arg(applyProc.err.split("\n").pop() || ("exit " + exitCode))
        }
        root.refresh()
        if (root.tab === "install") root.search()
      })
    }
  }

  Process {
    id: searchProc
    property string out: ""
    property string err: ""
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: searchProc.out = text }
    stderr: StdioCollector { waitForEnd: true; onStreamFinished: searchProc.err = String(text || "").trim() }
    onExited: function(exitCode) {
      Qt.callLater(function() {
        root.searching = false
        if (exitCode !== 0) {
          root.searchError = searchProc.err.split("\n").pop() || ("exit " + exitCode)
          return
        }
        try {
          var data = root.lastJson(searchProc.out)
          root.results = data.results || []
          root.resultTotal = data.total || 0
          var errs = Object.keys(data.errors || {})
          root.searchError = errs.length ? strings.t("sourceUnavailable").arg(errs.map(root.sourceLabel).join(", ")) : ""
        } catch (e) {
          root.searchError = String(e)
        }
      })
    }
  }

  Process {
    id: previewProc
    property string key: ""
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = root.lastJson(text)
          if (previewProc.key === root.previewKey && data.path) root.previewPath = data.path
        } catch (e) {}
      }
    }
  }

  // Exists only while a preview file is loaded, so it never loads "".
  Loader {
    id: previewLoader
    active: root.previewPath !== ""
    sourceComponent: FontLoader { source: "file://" + root.previewPath }
  }
  readonly property bool previewReady: previewLoader.item !== null && previewLoader.item.status === FontLoader.Ready
  readonly property string previewFamily: previewReady ? previewLoader.item.name : ""

  Commons.ShellIpc {
    target: root.ipcTarget
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function showTab(name: string): void { if (root.tabs.indexOf(name) >= 0) { root.tab = name; root.open() } }
    function status(): string {
      return root.st ? root.st.resolved.sans + " | " + root.st.resolved.serif + " | " + root.st.resolved.monospace : ""
    }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "󰛖"
    tooltipText: ""
    onPressed: function(b) {
      if (b === Qt.RightButton) root.refresh()
      else root.toggle()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: panel.loadedContent
    contentWidth: panel.fittedContentWidth(Style.space(480))
    contentHeight: panel.fittedContentHeight(panel.loadedContentHeight, Style.space(700))

    content: PanelKeyCatcher {
      property alias panelFlick: panelFlick
      implicitHeight: column.implicitHeight
      anchors.fill: parent
      onMoveRequested: function(dx, dy) {
        if (consentDialog.opened) { if (dx !== 0) consentDialog.selectedIndex = consentDialog.selectedIndex === 0 ? 1 : 0; return }
        if (resetDialog.opened) { if (dx !== 0) resetDialog.selectedIndex = resetDialog.selectedIndex === 0 ? 1 : 0; return }
        if (dx !== 0) {
          var i = root.tabs.indexOf(root.tab)
          root.tab = root.tabs[Math.max(0, Math.min(root.tabs.length - 1, i + dx))]
        } else if (dy !== 0) {
          panelFlick.scrollBy(dy * Style.space(60))
        }
      }
      onActivateRequested: {
        if (consentDialog.opened) {
          if (consentDialog.selectedIndex === 0) root.pendingConsent = null
          else root.confirmConsent()
          return
        }
        if (resetDialog.opened) {
          if (resetDialog.selectedIndex === 0) root.pendingReset = false
          else root.resetAll()
        }
      }
      onCloseRequested: {
        if (consentDialog.opened) root.pendingConsent = null
        else if (resetDialog.opened) root.pendingReset = false
        else root.close()
      }
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        if (t === "r" || t === "R") root.refresh()
        else if (t >= "1" && t <= "4") root.tab = root.tabs[Number(t) - 1]
      }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        pixelAligned: true
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        function scrollBy(dy) {
          contentY = Math.max(0, Math.min(Math.max(0, contentHeight - height), contentY + dy))
        }

        maximumFlickVelocity: Style.space(5000)
        flickDeceleration: Style.space(2400)

        // Touchpad deltas arrive scaled by Hyprland's scroll_factor (often
        // well below 1, tuned for browsers that add their own acceleration),
        // so the gain undoes it; lifting the fingers keeps the motion going.
        property real touchpadGain: Math.max(1, Math.min(4, 1 / Math.max(0.05, root.touchpadScrollFactor)))
        property real swipeVelocity: 0
        property real lastWheelAt: 0

        // Momentum after the fingers lift: constant deceleration from the
        // gesture's last velocity (Flickable.flick does not move when called
        // from a wheel gesture). Off when Omarchy animations are off.
        readonly property real coastDeceleration: 5000
        function coast(velocity) {
          var speed = Math.min(Math.abs(velocity), 6000)
          var distance = speed * speed / (2 * coastDeceleration)
          var target = Math.max(0, Math.min(Math.max(0, contentHeight - height), contentY + (velocity < 0 ? distance : -distance)))
          var duration = Style.duration(Math.round(speed / coastDeceleration * 1000))
          if (duration <= 0 || Math.abs(target - contentY) < 1) return
          coastAnim.to = target
          coastAnim.duration = duration
          coastAnim.restart()
        }

        NumberAnimation {
          id: coastAnim
          target: panelFlick
          property: "contentY"
          easing.type: Easing.OutQuad
        }

        onDraggingChanged: if (dragging) coastAnim.stop()

        WheelHandler {
          acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
          onWheel: function(event) {
            var now = Date.now()
            var touchpad = (event.device && event.device.type === PointerDevice.TouchPad)
              || event.phase !== Qt.NoScrollPhase || event.pixelDelta.y !== 0
            if (!touchpad) {
              coastAnim.stop()
              panelFlick.scrollBy(-event.angleDelta.y / 120 * Style.space(90))
              return
            }
            if (event.phase === Qt.ScrollEnd) {
              // The gesture ends with an empty event: hand the motion to a flick.
              var recent = now - panelFlick.lastWheelAt < 80
              if (recent && Math.abs(panelFlick.swipeVelocity) > Style.space(200))
                panelFlick.coast(panelFlick.swipeVelocity)
              panelFlick.swipeVelocity = 0
              return
            }
            // pixelDelta arrives rounded to whole pixels; angleDelta carries the
            // same motion at about 12x the resolution, so use it for smooth steps.
            var d = (event.angleDelta.y !== 0 ? event.angleDelta.y / 12 : event.pixelDelta.y) * panelFlick.touchpadGain
            coastAnim.stop()
            panelFlick.scrollBy(-d)
            var dt = now - panelFlick.lastWheelAt
            var v = (dt > 0 && dt < 80) ? d * 1000 / dt : 0
            panelFlick.swipeVelocity = dt >= 80 ? v : panelFlick.swipeVelocity * 0.7 + v * 0.3
            panelFlick.lastWheelAt = now
          }
        }

        Column {
          id: column
          width: panelFlick.width - Style.space(8)
          spacing: Style.space(12)

          PanelHero {
            width: parent.width
            title: strings.t("title")
            meta: root.st ? root.st.resolved.sans + " · " + root.st.resolved.serif + " · " + root.st.resolved.monospace : strings.t("loading")
            foreground: root.foreground
            fontFamily: root.fontFamily
            iconComponent: Component {
              PlainText {
                text: "󰛖"
                font.pixelSize: Style.font.display
              }
            }
          }

          ButtonGroup {
            width: parent.width
            options: root.tabs.map(function (t) { return { value: t, label: strings.t("tab." + t) } })
            value: root.tab
            foreground: root.foreground
            fontFamily: root.fontFamily
            fontSize: Style.font.bodySmall
            onChanged: function(v) { root.tab = v }
          }

          PlainText {
            visible: root.message !== ""
            width: parent.width
            text: root.message
            color: root.messageIsError ? root.urgent : root.dim
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
          }

          // ------------------------------------------------ system families
          Loader {
            width: parent.width
            active: root.tab === "system"
            visible: active
            sourceComponent: Component {
              Column {
                width: parent ? parent.width : 0
                spacing: Style.space(14)

                PlainText {
                  width: parent.width
                  text: strings.t("system.help")
                  color: root.dim
                  font.pixelSize: Style.font.caption
                  wrapMode: Text.WordWrap
                }

                Repeater {
                  model: ["sans", "serif", "monospace", "emoji"]
                  FontPicker {
                    required property string modelData
                    width: parent ? parent.width : 0
                    label: strings.t("role." + modelData)
                    hint: strings.t("role." + modelData + ".help")
                    value: root.cfg ? (root.cfg.roles[modelData] || "") : ""
                    options: root.familyOptions(root.roleLabel(modelData), modelData === "monospace")
                    previewFamily: root.st ? root.st.resolved[modelData] : ""
                    previewText: modelData === "emoji" ? "😀 🎉 🚀 ❤️ 👍" : root.sampleText
                    onPicked: function(v) { root.setRole(modelData, v) }
                  }
                }

                OptionRow {
                  width: parent.width
                  title: strings.t("metric")
                  subtitle: strings.t("metric.help")
                  checked: root.cfg ? root.cfg.replaceMetricAliases : false
                  onActivated: root.runHelper(["set-metric", checked ? "off" : "on"])
                }

                PanelSeparator { foreground: root.foreground }

                ActionRow {
                  width: parent.width
                  glyph: "󰑓"
                  title: strings.t("reset")
                  subtitle: strings.t("reset.help")
                  onActivated: root.pendingReset = true
                }
              }
            }
          }

          // ------------------------------------------------ apps and sizes
          Loader {
            width: parent.width
            active: root.tab === "apps"
            visible: active
            sourceComponent: Component {
              Column {
                width: parent ? parent.width : 0
                spacing: Style.space(14)

                PlainText {
                  width: parent.width
                  text: root.st ? strings.t("apps.scale").arg(root.st.shellBaseSize).arg(Commons.I18n.formatNumber(root.st.textScaling, 2)) : ""
                  color: root.dim
                  font.pixelSize: Style.font.caption
                  wrapMode: Text.WordWrap
                }

                PanelSectionHeader { text: strings.t("gtk"); foreground: root.foreground; fontFamily: root.fontFamily }

                Repeater {
                  model: ["interface", "document", "monospace"]
                  FontPicker {
                    id: gtkPicker
                    required property string modelData
                    readonly property var live: root.st ? root.st.gtk[modelData] : null
                    readonly property bool overridden: root.cfg ? root.cfg.gtk[modelData] !== null : false
                    width: parent ? parent.width : 0
                    label: strings.t("gtk." + modelData)
                    hint: strings.t("gtk." + modelData + ".help")
                    value: overridden && live ? live.family : ""
                    options: root.familyOptions(strings.t("systemValue").arg(live ? live.family : ""), modelData === "monospace")
                    previewFamily: live ? live.family : ""
                    previewText: root.sampleText
                    showSize: true
                    size: live && live.size ? Math.round(live.size) : 11
                    sizeFrom: 6
                    sizeTo: 48
                    onPicked: function(v) { root.setGtk(modelData, v, gtkPicker.size) }
                    onSizeEdited: function(s) { root.setGtk(modelData, gtkPicker.value || (live ? live.family : ""), s) }
                  }
                }

                PanelSeparator { foreground: root.foreground }
                PanelSectionHeader { text: strings.t("terminal"); foreground: root.foreground; fontFamily: root.fontFamily }

                FontPicker {
                  id: termPicker
                  width: parent.width
                  label: root.st ? strings.t("terminal.label").arg(root.st.terminal.present.join(", ") || "—") : strings.t("terminal")
                  hint: strings.t("terminal.help")
                  value: root.cfg ? (root.cfg.terminal.family || "") : ""
                  options: root.familyOptions(strings.t("followMono"), true)
                  previewFamily: root.st ? (root.st.terminal.family || root.st.resolved.monospace) : ""
                  previewText: "fn main() { println!(\"0O 1lI {}[]\"); } →"
                  showSize: true
                  size: root.st && root.st.terminal.size ? Math.round(root.st.terminal.size) : 9
                  sizeFrom: 5
                  sizeTo: 40
                  onPicked: function(v) { root.setTerminal(v, 0) }
                  onSizeEdited: function(s) { root.setTerminal(termPicker.value, s) }
                }

                PanelSeparator { foreground: root.foreground }
                PanelSectionHeader { text: strings.t("omarchy"); foreground: root.foreground; fontFamily: root.fontFamily }

                FontPicker {
                  id: groupPicker
                  width: parent.width
                  label: strings.t("groupbar")
                  hint: strings.t("groupbar.help")
                  value: root.cfg ? (root.cfg.groupbar.family || "") : ""
                  options: root.familyOptions(strings.t("omarchyDefault"), false)
                  previewFamily: root.st && root.st.groupbar.family ? root.st.groupbar.family : "monospace"
                  previewText: root.sampleText
                  showSize: true
                  size: root.st && root.st.groupbar.size ? root.st.groupbar.size : 12
                  sizeFrom: 6
                  sizeTo: 40
                  onPicked: function(v) { root.setGroupbar(v, groupPicker.size) }
                  onSizeEdited: function(s) { root.setGroupbar(groupPicker.value, s) }
                }

                FontPicker {
                  width: parent.width
                  label: strings.t("menu")
                  hint: strings.t("menu.help")
                  value: root.cfg ? (root.cfg.menu.family || "") : ""
                  options: root.familyOptions(strings.t("sameAsBar"), false)
                  previewFamily: Style.font.menuFamily
                  previewText: root.sampleText
                  onPicked: function(v) { root.setMenu(v) }
                }

                ActionRow {
                  width: parent.width
                  glyph: "󰜉"
                  title: strings.t("restartShell")
                  subtitle: strings.t("restartShell.help")
                  onActivated: root.restartShell()
                }
              }
            }
          }

          // ------------------------------------------------ rendering
          Loader {
            width: parent.width
            active: root.tab === "render"
            visible: active
            sourceComponent: Component {
              Column {
                width: parent ? parent.width : 0
                spacing: Style.space(14)

                PlainText {
                  visible: root.st !== null && root.st.hidpi
                  width: parent.width
                  text: root.st ? strings.t("hidpiHint").arg(Commons.I18n.formatNumber(root.st.monitorScale, 2)) : ""
                  color: root.dim
                  font.pixelSize: Style.font.caption
                  wrapMode: Text.WordWrap
                }

                Labeled {
                  width: parent.width
                  label: strings.t("presets")
                  hint: root.activePreset === "custom" ? strings.t("preset.custom") : strings.t("preset." + root.activePreset + ".help")
                  ButtonGroup {
                    width: parent.width
                    options: ["default", "hidpi", "lcd", "sharp", "smooth"].map(function (p) { return { value: p, label: strings.t("preset." + p) } })
                    value: root.activePreset
                    foreground: root.foreground
                    fontFamily: root.fontFamily
                    fontSize: Style.font.caption
                    onChanged: function(v) { root.applyPreset(v) }
                  }
                }

                PanelSeparator { foreground: root.foreground }

                Repeater {
                  model: ["antialias", "hinting", "autohint", "embeddedbitmap"]
                  Labeled {
                    required property string modelData
                    width: parent ? parent.width : 0
                    label: strings.t("render." + modelData)
                    hint: strings.t("render." + modelData + ".help")
                    ButtonGroup {
                      width: parent.width
                      options: root.onOff()
                      value: root.renderValue(modelData)
                      foreground: root.foreground
                      fontFamily: root.fontFamily
                      fontSize: Style.font.caption
                      onChanged: function(v) { root.setRendering(modelData, root.renderFromChoice(v)) }
                    }
                  }
                }

                Labeled {
                  width: parent.width
                  label: strings.t("render.hintstyle")
                  hint: strings.t("render.hintstyle.help")
                  ButtonGroup {
                    width: parent.width
                    options: ["default", "hintnone", "hintslight", "hintmedium", "hintfull"].map(function (v) { return { value: v, label: strings.t("hintstyle." + v) } })
                    value: root.renderValue("hintstyle")
                    foreground: root.foreground
                    fontFamily: root.fontFamily
                    fontSize: Style.font.caption
                    onChanged: function(v) { root.setRendering("hintstyle", root.renderFromChoice(v)) }
                  }
                }

                Labeled {
                  width: parent.width
                  label: strings.t("render.rgba")
                  hint: strings.t("render.rgba.help")
                  ButtonGroup {
                    width: parent.width
                    options: ["default", "none", "rgb", "bgr", "vrgb", "vbgr"].map(function (v) { return { value: v, label: strings.t("rgba." + v) } })
                    value: root.renderValue("rgba")
                    foreground: root.foreground
                    fontFamily: root.fontFamily
                    fontSize: Style.font.caption
                    onChanged: function(v) { root.setRendering("rgba", root.renderFromChoice(v)) }
                  }
                }

                Labeled {
                  width: parent.width
                  label: strings.t("render.lcdfilter")
                  hint: strings.t("render.lcdfilter.help")
                  ButtonGroup {
                    width: parent.width
                    options: ["default", "lcddefault", "lcdlight", "lcdlegacy", "lcdnone"].map(function (v) { return { value: v, label: strings.t("lcdfilter." + v) } })
                    value: root.renderValue("lcdfilter")
                    foreground: root.foreground
                    fontFamily: root.fontFamily
                    fontSize: Style.font.caption
                    onChanged: function(v) { root.setRendering("lcdfilter", root.renderFromChoice(v)) }
                  }
                }

                PlainText {
                  width: parent.width
                  text: strings.t("render.note")
                  color: root.dim
                  font.pixelSize: Style.font.caption
                  wrapMode: Text.WordWrap
                }
              }
            }
          }

          // ------------------------------------------------ install
          Loader {
            width: parent.width
            active: root.tab === "install"
            visible: active
            sourceComponent: Component {
              Column {
                width: parent ? parent.width : 0
                spacing: Style.space(10)

                TextField {
                  id: searchField
                  width: parent.width
                  text: root.query
                  placeholderText: strings.t("searchPlaceholder")
                  foreground: root.foreground
                  font.family: root.fontFamily
                  maximumLength: 64
                  onTextEdited: { root.query = text; searchDebounce.restart() }
                  Keys.onEscapePressed: function(event) {
                    if (text) { text = ""; root.query = ""; searchDebounce.restart(); event.accepted = true }
                  }
                }

                ButtonGroup {
                  width: parent.width
                  options: ["all", "google", "fontsource", "nerd", "fontshare"].map(function (s) { return { value: s, label: strings.t("source." + s) } })
                  value: root.source
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  fontSize: Style.font.caption
                  onChanged: function(v) { root.source = v; root.search() }
                }

                Dropdown {
                  width: parent.width
                  label: strings.t("category")
                  value: root.category
                  options: ["", "sans-serif", "serif", "monospace", "display", "handwriting", "other"].map(function (c) {
                    return { value: c, label: c ? strings.t("category." + c) : strings.t("category.all") }
                  })
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  onChanged: function(v) { root.category = v; root.search() }
                }

                PlainText {
                  width: parent.width
                  text: root.searching ? strings.t("searching")
                    : root.searchError !== "" ? root.searchError
                    : strings.tn("results", root.resultTotal).arg(Commons.I18n.formatNumber(root.resultTotal))
                  color: root.searchError !== "" ? root.urgent : root.dim
                  font.pixelSize: Style.font.caption
                  wrapMode: Text.WordWrap
                }

                Column {
                  width: parent.width
                  spacing: Style.space(4)
                  Repeater {
                    model: root.results
                    ResultRow {
                      required property var modelData
                      width: parent ? parent.width : 0
                      item: modelData
                    }
                  }
                }

                ActionRow {
                  width: parent.width
                  glyph: "󰑐"
                  title: strings.t("refreshCatalogs")
                  subtitle: strings.t("refreshCatalogs.help")
                  onActivated: root.refreshCatalogs()
                }

                PanelSeparator { foreground: root.foreground; visible: root.st !== null && root.st.installed.length > 0 }

                PanelSectionHeader {
                  visible: root.st !== null && root.st.installed.length > 0
                  text: strings.t("installedByPlugin")
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                }

                Column {
                  width: parent.width
                  spacing: Style.space(4)
                  Repeater {
                    model: root.st ? root.st.installed : []
                    ActionRow {
                      required property var modelData
                      width: parent ? parent.width : 0
                      glyph: "󰆴"
                      title: (modelData.families || []).join(", ")
                      subtitle: strings.t("installedMeta").arg(root.sourceLabel(modelData.source)).arg(modelData.files)
                      titleFamily: (modelData.families || [""])[0]
                      onActivated: root.uninstall(modelData.source, modelData.id, (modelData.families || [modelData.id])[0])
                    }
                  }
                }
              }
            }
          }

          PlainText {
            width: parent.width
            text: strings.t("tip")
            color: root.dim
            font.pixelSize: Style.font.caption
            wrapMode: Text.WordWrap
          }
        }
      }

      ConfirmDialog {
        id: consentDialog
        anchors.fill: parent
        z: 11
        opened: root.pendingConsent !== null
        message: strings.t("consent")
        confirmText: strings.t("consentConfirm")
        foreground: root.foreground
        fontFamily: root.fontFamily
        onCanceled: root.pendingConsent = null
        onConfirmed: root.confirmConsent()
      }

      ConfirmDialog {
        id: resetDialog
        anchors.fill: parent
        z: 10
        opened: root.pendingReset
        message: strings.t("reset.confirm")
        confirmText: strings.t("reset")
        foreground: root.foreground
        fontFamily: root.fontFamily
        onCanceled: root.pendingReset = false
        onConfirmed: root.resetAll()
      }
    }
  }

  // ---------------------------------------------------------- components

  component PlainText: Text {
    textFormat: Text.PlainText
    color: root.foreground
    font.family: root.fontFamily
    font.pixelSize: Style.font.body
  }

  // A caption, a control, and a one-line explanation under it.
  component Labeled: Column {
    id: labeled
    property string label: ""
    property string hint: ""
    default property alias control: holder.data
    spacing: Style.space(4)

    PlainText {
      width: parent.width
      text: labeled.label
      font.pixelSize: Style.font.bodySmall
    }
    Item {
      id: holder
      width: parent.width
      implicitHeight: childrenRect.height
      height: childrenRect.height
    }
    PlainText {
      visible: labeled.hint !== ""
      width: parent.width
      text: labeled.hint
      color: root.dim
      font.pixelSize: Style.font.caption
      wrapMode: Text.WordWrap
    }
  }

  // Family dropdown (installed families) with an optional size field and a
  // live sample drawn in the family that actually resolves.
  component FontPicker: Column {
    id: picker
    property string label: ""
    property string hint: ""
    property string value: ""
    property var options: []
    property string previewFamily: ""
    property string previewText: ""
    property bool showSize: false
    property int size: 11
    property int sizeFrom: 6
    property int sizeTo: 48
    signal picked(string value)
    signal sizeEdited(int value)
    spacing: Style.space(6)

    PlainText {
      width: parent.width
      text: picker.label
      font.pixelSize: Style.font.bodySmall
      elide: Text.ElideRight
    }

    RowLayout {
      width: parent.width
      spacing: Style.space(8)
      SearchableDropdown {
        Layout.fillWidth: true
        showLabel: false
        value: picker.value
        options: picker.options
        triggerLabel: picker.value || (picker.options.length ? picker.options[0].label : "")
        foreground: root.foreground
        fontFamily: root.fontFamily
        enabled: !root.busy
        onChanged: function(v) { picker.picked(v) }
      }
      NumberField {
        visible: picker.showSize
        value: picker.size
        from: picker.sizeFrom
        to: picker.sizeTo
        foreground: root.foreground
        fontFamily: root.fontFamily
        enabled: !root.busy
        onModified: function(v) { picker.size = v; sizeDebounce.restart() }
      }
    }

    Timer {
      id: sizeDebounce
      interval: 700
      onTriggered: picker.sizeEdited(picker.size)
    }

    Text {
      width: parent.width
      textFormat: Text.PlainText
      text: picker.previewText
      color: root.foreground
      font.family: picker.previewFamily || root.fontFamily
      font.pixelSize: Math.round(Style.font.body * 1.15)
      elide: Text.ElideRight
    }

    PlainText {
      visible: picker.hint !== ""
      width: parent.width
      text: picker.hint
      color: root.dim
      font.pixelSize: Style.font.caption
      wrapMode: Text.WordWrap
    }
  }

  component ActionRow: CursorSurface {
    id: action
    property string glyph: ""
    property string title: ""
    property string subtitle: ""
    property string titleFamily: ""
    signal activated()

    hasCursor: mouse.containsMouse
    foreground: root.foreground
    implicitHeight: actionLayout.implicitHeight + Style.spacing.rowPaddingX
    opacity: root.busy ? 0.6 : 1

    MouseArea {
      id: mouse
      anchors.fill: parent
      hoverEnabled: true
      enabled: !root.busy
      cursorShape: Qt.PointingHandCursor
      onClicked: action.activated()
    }

    RowLayout {
      id: actionLayout
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(10)
      spacing: Style.space(10)

      PlainText {
        text: action.glyph
        font.pixelSize: Style.font.icon
        horizontalAlignment: Text.AlignHCenter
        Layout.preferredWidth: Math.round(Style.font.icon * 1.4)
      }
      ColumnLayout {
        Layout.fillWidth: true
        spacing: Style.space(1)
        PlainText {
          Layout.fillWidth: true
          text: action.title
          font.family: action.titleFamily || root.fontFamily
          elide: Text.ElideRight
        }
        PlainText {
          Layout.fillWidth: true
          visible: action.subtitle !== ""
          text: action.subtitle
          color: root.dim
          font.pixelSize: Style.font.caption
          wrapMode: Text.WordWrap
        }
      }
    }
  }

  component OptionRow: CursorSurface {
    id: opt
    property string title: ""
    property string subtitle: ""
    property bool checked: false
    signal activated()

    hasCursor: optMouse.containsMouse
    foreground: root.foreground
    implicitHeight: optLayout.implicitHeight + Style.spacing.rowPaddingX

    MouseArea {
      id: optMouse
      anchors.fill: parent
      hoverEnabled: true
      enabled: !root.busy
      cursorShape: Qt.PointingHandCursor
      onClicked: opt.activated()
    }

    RowLayout {
      id: optLayout
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(4)
      spacing: Style.space(10)

      ColumnLayout {
        Layout.fillWidth: true
        spacing: Style.space(1)
        PlainText { Layout.fillWidth: true; text: opt.title; wrapMode: Text.WordWrap }
        PlainText {
          Layout.fillWidth: true
          text: opt.subtitle
          color: root.dim
          font.pixelSize: Style.font.caption
          wrapMode: Text.WordWrap
        }
      }
      ToggleSwitch {
        checked: opt.checked
        busy: root.busy
        cursorRing: false
        foreground: root.foreground
        onToggled: opt.activated()
      }
    }
  }

  // One catalog entry. Hovering fetches a small preview file so the name and
  // sample can be drawn in the font before it is installed.
  component ResultRow: CursorSurface {
    id: result
    property var item: ({})
    readonly property string key: item.source + ":" + item.id
    readonly property bool previewing: root.previewKey === key && root.previewReady
    readonly property bool installing: root.installingKey === key

    hasCursor: resultMouse.containsMouse
    current: root.previewKey === key
    foreground: root.foreground
    implicitHeight: resultLayout.implicitHeight + Style.spacing.rowPaddingX

    MouseArea {
      id: resultMouse
      anchors.fill: parent
      hoverEnabled: true
      onEntered: previewDelay.restart()
      onExited: previewDelay.stop()
      onClicked: root.loadPreview(result.item)
    }

    Timer {
      id: previewDelay
      interval: 300
      onTriggered: root.loadPreview(result.item)
    }

    RowLayout {
      id: resultLayout
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(6)
      spacing: Style.space(10)

      ColumnLayout {
        Layout.fillWidth: true
        spacing: Style.space(2)
        Text {
          Layout.fillWidth: true
          textFormat: Text.PlainText
          text: result.item.family || ""
          color: root.foreground
          font.family: result.previewing ? root.previewFamily : (result.item.installed ? result.item.family : root.fontFamily)
          font.pixelSize: Math.round(Style.font.body * 1.2)
          elide: Text.ElideRight
        }
        PlainText {
          Layout.fillWidth: true
          text: root.resultMeta(result.item)
          color: root.dim
          font.pixelSize: Style.font.caption
          wrapMode: Text.WordWrap
        }
        Text {
          Layout.fillWidth: true
          visible: result.previewing || (root.previewKey === result.key && result.item.installed)
          textFormat: Text.PlainText
          text: root.sampleText
          color: root.foreground
          font.family: result.previewing ? root.previewFamily : result.item.family
          font.pixelSize: Style.font.body
          wrapMode: Text.WordWrap
        }
        PlainText {
          Layout.fillWidth: true
          visible: root.previewKey === result.key && !result.previewing && !result.item.installed
          text: (result.item.source === "google" || result.item.source === "fontsource") ? strings.t("previewLoading") : strings.t("previewAfterInstall")
          color: root.dim
          font.pixelSize: Style.font.caption
        }
      }

      PanelActionButton {
        iconText: result.installing ? "󰔟" : (result.item.managed ? "󰆴" : (result.item.installed ? "󰄬" : "󰇚"))
        tooltipText: result.item.managed ? strings.t("remove") : (result.item.installed ? strings.t("alreadyInstalled") : strings.t("install"))
        foreground: root.foreground
        fontFamily: root.fontFamily
        enabled: !root.busy && !(result.item.installed && !result.item.managed)
        onClicked: {
          if (result.item.managed) root.uninstall(result.item.source, result.item.id, result.item.family)
          else root.install(result.item)
        }
      }
    }
  }
}
