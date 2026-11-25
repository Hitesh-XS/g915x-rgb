pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

Item {
    id: root

    required property var editor
    property bool showSelection: true
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

                x: (keyX + 0.15) * root.unit
                y: (keyY + 0.85) * root.unit
                width: (keyW - 0.08) * root.unit
                height: (keyH - 0.08) * root.unit
                radius: Math.max(2, root.unit * 0.1)
                color: "#1b1d1f"
                border.width: chosen && root.showSelection ? 2 : 1
                border.color: chosen && root.showSelection ? Kirigami.Theme.highlightColor : "#33373b"
                Accessible.role: Accessible.Button
                Accessible.name: name
                Accessible.checked: chosen
                Accessible.onPressAction: root.editor.keyClicked(address)

                Rectangle {
                    anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: 3 }
                    height: Math.max(2, root.unit * 0.07)
                    radius: 1
                    color: cap.lit
                }
                Text {
                    anchors.fill: parent
                    anchors.margins: 2
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    text: cap.label
                    textFormat: Text.PlainText
                    // Dark lighting colors would make the legend unreadable.
                    color: 0.3 * cap.lit.r + 0.59 * cap.lit.g + 0.11 * cap.lit.b < 0.45 ? "#c3c8cd" : cap.lit
                    font.bold: true
                    font.pixelSize: Math.max(6, root.unit * (cap.keyH < 0.6 ? 0.2 : cap.label.length > 3 ? 0.24 : 0.32))
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
                        Math.min(from.x, event.x) / u - 0.15, Math.min(from.y, event.y) / u - 0.85,
                        Math.max(from.x, event.x) / u - 0.15, Math.max(from.y, event.y) / u - 0.85,
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
