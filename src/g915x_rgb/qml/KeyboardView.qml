pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Shapes
import org.kde.kirigami as Kirigami

Item {
    id: root

    required property var editor
    property bool showSelection: true
    // Where the layout origin sits inside the plate, in key units.
    readonly property real originX: 0.15
    readonly property real originY: 1.05
    // Symbols on the round keys, drawn on a 24 by 24 grid.
    readonly property var symbols: ({
        Prev: "M4 5h2v14h-2zM13 5v14l-7 -7zM20 5v14l-7 -7z",
        Play: "M3 5l9 7l-9 7zM14 5h2.5v14h-2.5zM18.5 5h2.5v14h-2.5z",
        Next: "M4 5l7 7l-7 7zM11 5l7 7l-7 7zM18 5h2v14h-2z",
        Backlight: "M12 8a4 4 0 1 0 0.01 0zM11 1h2v5h-2zM11 18h2v5h-2zM1 11h5v2h-5zM18 11h5v2h-5zM4.2 5.6l1.4 -1.4l3 3l-1.4 1.4zM15.4 16.8l1.4 -1.4l3 3l-1.4 1.4zM18.4 4.2l1.4 1.4l-3 3l-1.4 -1.4zM7.2 15.4l1.4 1.4l-3 3l-1.4 -1.4z",
        GameMode: "M10.5 2h3v11h-3zM2 15l7 -3.4v2.6l3 1.6l3 -1.6v-2.6l7 3.4v3l-10 5l-10 -5zM5 15.4l7 3.5l7 -3.5l-2 -1l-5 2.6l-5 -2.6z",
        Mute: "M3 9h4l5 -4v14l-5 -4h-4zM15.2 8l2.3 2.3l2.3 -2.3l1.2 1.2l-2.3 2.3l2.3 2.3l-1.2 1.2l-2.3 -2.3l-2.3 2.3l-1.2 -1.2l2.3 -2.3l-2.3 -2.3z"
    })
    readonly property real unit: Math.min(width / (editor.layoutWidth + 0.3), height / (editor.layoutHeight + 0.4))

    implicitHeight: width / (editor.layoutWidth + 0.3) * (editor.layoutHeight + 0.4)

    Rectangle {
        id: plate
        anchors.centerIn: parent
        width: root.unit * (root.editor.layoutWidth + 0.3)
        height: root.unit * (root.editor.layoutHeight + 0.4)
        radius: Kirigami.Units.cornerRadius
        // The hardware is dark whatever the desktop theme is.
        color: "#0c0d0e"
        border.color: "#33373b"

        // The volume roller has no light; it is here for orientation.
        Rectangle {
            x: (root.editor.layoutWidth - 3.4 + 0.15) * root.unit
            y: 0.3 * root.unit
            width: 2.6 * root.unit
            height: 0.36 * root.unit
            radius: 2
            border.color: "#33373b"
            gradient: Gradient {
                GradientStop { position: 0; color: "#2b2e31" }
                GradientStop { position: 0.5; color: "#5a5e62" }
                GradientStop { position: 1; color: "#222527" }
            }
        }

        // No light address is known for game mode, so it cannot be selected.
        Rectangle {
            x: (1.25 + 6.65 + root.originX) * root.unit
            y: (-0.85 + root.originY) * root.unit
            width: 0.62 * root.unit
            height: width
            radius: width / 2
            color: "#1b1d1f"
            border.color: "#33373b"
            Accessible.role: Accessible.Graphic
            Accessible.name: qsTr("Game mode, not controllable")

            Shape {
                anchors.centerIn: parent
                width: 24
                height: 24
                scale: parent.width * 0.6 / 24
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    strokeWidth: -1
                    fillColor: "#8d9399"
                    PathSvg { path: root.symbols.GameMode }
                }
            }
        }

        Repeater {
            model: root.editor.keys
            delegate: Rectangle {
                id: cap
                required property string name
                required property string label
                required property real keyX
                required property real keyY
                required property real keyW
                required property real keyH
                required property int address
                required property color lit
                required property bool chosen

                readonly property string symbol: root.symbols[name] ?? ""
                readonly property bool round: symbol !== "" || keyH === 0.7
                readonly property real inset: round && keyH === 1 ? 0.08 : 0
                // Blend dark lighting colors toward gray so the legend stays readable.
                readonly property real luma: 0.3 * lit.r + 0.59 * lit.g + 0.11 * lit.b
                readonly property color legend: Qt.tint(lit, Qt.rgba(0.77, 0.79, 0.81, Math.max(0, 1 - luma / 0.4)))

                x: (keyX + inset + root.originX) * root.unit
                y: (keyY + inset + root.originY) * root.unit
                width: (keyW - 0.08 - 2 * inset) * root.unit
                height: (keyH - 0.08 - 2 * inset) * root.unit
                radius: round ? width / 2 : Math.max(2, root.unit * 0.1)
                color: "#1b1d1f"
                border.width: chosen && root.showSelection ? 2 : 1
                border.color: chosen && root.showSelection ? Kirigami.Theme.highlightColor : round ? lit : "#33373b"
                Accessible.role: Accessible.Button
                Accessible.name: name
                Accessible.checked: chosen
                Accessible.onPressAction: root.editor.keyClicked(address)

                Shape {
                    visible: cap.symbol !== ""
                    anchors.centerIn: parent
                    width: 24
                    height: 24
                    scale: cap.width * 0.58 / 24
                    preferredRendererType: Shape.CurveRenderer
                    ShapePath {
                        strokeWidth: -1
                        fillColor: cap.legend
                        PathSvg { path: cap.symbol }
                    }
                }
                Rectangle {
                    visible: !cap.round
                    anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: 3 }
                    height: Math.max(2, root.unit * 0.07)
                    radius: 1
                    color: cap.lit
                }
                Text {
                    visible: cap.symbol === ""
                    anchors.fill: parent
                    anchors.margins: 2
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    text: cap.label
                    textFormat: Text.PlainText
                    color: cap.legend
                    font.bold: true
                    font.pixelSize: Math.max(6, root.unit * (cap.keyH < 1 ? 0.22 : cap.label.length > 3 ? 0.24 : 0.32))
                    renderType: Text.NativeRendering
                }
            }
        }

        MouseArea {
            id: mouse
            anchors.fill: parent
            property point from
            property bool dragging: false

            function keyAt(mx: real, my: real): int {
                for (let i = 0; i < plate.children.length; ++i) {
                    const c = plate.children[i];
                    if (c.address !== undefined && mx >= c.x && mx <= c.x + c.width && my >= c.y && my <= c.y + c.height)
                        return c.address;
                }
                return -1;
            }

            onPressed: event => {
                from = Qt.point(event.x, event.y);
                dragging = false;
            }
            onPositionChanged: event => {
                if (Math.hypot(event.x - from.x, event.y - from.y) > 8)
                    dragging = true;
                band.to = Qt.point(event.x, event.y);
            }
            onCanceled: dragging = false
            onReleased: event => {
                if (dragging) {
                    const u = root.unit;
                    root.editor.selectArea(
                        Math.min(from.x, event.x) / u - root.originX, Math.min(from.y, event.y) / u - root.originY,
                        Math.max(from.x, event.x) / u - root.originX, Math.max(from.y, event.y) / u - root.originY,
                        (event.modifiers & (Qt.ShiftModifier | Qt.ControlModifier)) !== 0);
                } else {
                    const address = keyAt(event.x, event.y);
                    if (address >= 0)
                        root.editor.keyClicked(address);
                }
                dragging = false;
            }
        }

        Rectangle {
            id: band
            property point to
            visible: mouse.dragging
            x: Math.min(mouse.from.x, to.x)
            y: Math.min(mouse.from.y, to.y)
            width: Math.abs(to.x - mouse.from.x)
            height: Math.abs(to.y - mouse.from.y)
            color: Qt.alpha(Kirigami.Theme.highlightColor, 0.2)
            border.color: Kirigami.Theme.highlightColor
        }
    }
}
