// KWin script — pushes active-window changes to the g915x-rgb daemon.
//
// Loaded by g915x_rgb.auto_switch.KWinSource via the KWin scripting DBus
// interface. KWin runs this in its built-in JS engine; we just want it
// to call our DBus method whenever the focused window changes so the
// daemon can match rules and switch profiles.
//
// Compatible with KDE Plasma 5 and 6 (uses workspace.activeWindow on 6,
// workspace.activeClient on 5).

function pickWindow() {
    if (typeof workspace.activeWindow !== "undefined") return workspace.activeWindow;
    if (typeof workspace.activeClient !== "undefined") return workspace.activeClient;
    return null;
}

function send() {
    var w = pickWindow();
    var cls = "";
    var title = "";
    if (w) {
        cls = (w.resourceClass || "").toString();
        title = (w.caption || "").toString();
    }
    callDBus(
        "org.g915xrgb.Controller",
        "/org/g915xrgb/Controller",
        "org.g915xrgb.Controller",
        "OnActiveWindowChanged",
        cls,
        title
    );
}

// Plasma 6 names the signal windowActivated; Plasma 5 clientActivated.
if (typeof workspace.windowActivated !== "undefined") {
    workspace.windowActivated.connect(send);
} else if (typeof workspace.clientActivated !== "undefined") {
    workspace.clientActivated.connect(send);
}
send();
