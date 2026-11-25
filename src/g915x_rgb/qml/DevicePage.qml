import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami

Kirigami.FormLayout {
    id: root
    required property var editor
    readonly property var status: editor.status
    readonly property bool hasBattery: (status.battery_percent ?? -1) >= 0
    readonly property var minutes: [0, 1, 5, 15, 30]

    Kirigami.Separator { Kirigami.FormData.isSection: true; Kirigami.FormData.label: qsTr("Keyboard") }
    QQC2.Label {
        Kirigami.FormData.label: qsTr("Connection:")
        text: root.status.error ? qsTr("The lighting service is not running")
            : root.status.access_denied ? qsTr("Access denied. Reconnect the keyboard.")
            : root.status.connected ? qsTr("Connected") : qsTr("Not connected")
    }
    QQC2.Label {
        Kirigami.FormData.label: qsTr("Battery:")
        visible: root.hasBattery
        text: (root.status.battery_percent ?? -1) < 0 ? qsTr("Unknown")
            : root.status.battery_charging ? qsTr("%1%, charging").arg(root.status.battery_percent)
            : qsTr("%1%").arg(root.status.battery_percent)
    }
    Kirigami.Separator { Kirigami.FormData.isSection: true; Kirigami.FormData.label: qsTr("Behavior") }
    QQC2.CheckBox {
        text: qsTr("Start lighting at login")
        checked: root.editor.login
        onToggled: root.editor.setLogin(checked)
    }
    QQC2.CheckBox {
        text: qsTr("Turn off when the screen locks")
        checked: root.editor.options.off_when_locked
        onToggled: root.editor.setOption("off_when_locked", checked)
    }
    QQC2.CheckBox {
        visible: root.hasBattery
        text: qsTr("Show low battery on the G keys")
        checked: root.editor.options.battery_gkeys
        onToggled: root.editor.setOption("battery_gkeys", checked)
    }
    QQC2.ComboBox {
        Kirigami.FormData.label: qsTr("Dim when idle:")
        enabled: root.status.idle_available !== false
        model: root.minutes.map(m => m === 0 ? qsTr("Never") : m === 1 ? qsTr("After 1 minute") : qsTr("After %1 minutes").arg(m))
        currentIndex: root.minutes.indexOf(root.editor.options.idle_minutes)
        onActivated: index => root.editor.setOption("idle_minutes", root.minutes[index])
    }
}
