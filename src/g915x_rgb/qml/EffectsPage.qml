pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami

GridLayout {
    id: root
    required property var editor
    // Which of the profile's two effects is being edited.
    property string slot: "ambient"
    readonly property var current: editor.profile[slot]

    columns: width > Kirigami.Units.gridUnit * 36 ? 2 : 1
    columnSpacing: Kirigami.Units.gridUnit * 2
    rowSpacing: Kirigami.Units.largeSpacing

    ColumnLayout {
        Layout.fillWidth: true
        Layout.alignment: Qt.AlignTop
        Layout.preferredWidth: 2
        RowLayout {
            QQC2.ButtonGroup { id: slots }
            QQC2.RadioButton { text: qsTr("Background effect"); checked: root.slot === "ambient"; QQC2.ButtonGroup.group: slots; onToggled: root.slot = "ambient" }
            QQC2.RadioButton { text: qsTr("Effect at login"); checked: root.slot === "startup"; QQC2.ButtonGroup.group: slots; onToggled: root.slot = "startup" }
        }
        GridLayout {
            columns: 4
            Layout.fillWidth: true
            uniformCellWidths: true
            Repeater {
                model: [{ id: "", label: qsTr("None"), detail: qsTr("Show the profile's colors") }].concat(root.editor.effects)
                delegate: QQC2.Button {
                    required property var modelData
                    Layout.fillWidth: true
                    text: modelData.label
                    id: choice
                    readonly property bool current: root.current.name === modelData.id
                    checkable: true
                    checked: current
                    icon.name: current ? "checkmark" : ""
                    onClicked: {
                        root.editor.setEffect(root.slot, "name", choice.modelData.id);
                        // Clicking toggles the button; keep it tied to the profile.
                        choice.checked = Qt.binding(() => choice.current);
                    }
                    QQC2.ToolTip.text: modelData.detail
                    QQC2.ToolTip.visible: hovered
                    QQC2.ToolTip.delay: Kirigami.Units.toolTipDelay
                }
            }
        }
    }

    Kirigami.FormLayout {
        Layout.fillWidth: true
        Layout.alignment: Qt.AlignTop
        Layout.preferredWidth: 1
        enabled: root.current.name !== ""
        ColorButton {
            Kirigami.FormData.label: qsTr("Primary:")
            description: qsTr("Primary color")
            enabled: root.current.name !== "rainbow"
            value: root.current.primary
            onPicked: color => root.editor.setEffect(root.slot, "primary", color)
        }
        ColorButton {
            Kirigami.FormData.label: qsTr("Secondary:")
            description: qsTr("Secondary color")
            enabled: root.current.name !== "rainbow"
            value: root.current.secondary
            onPicked: color => root.editor.setEffect(root.slot, "secondary", color)
        }
        QQC2.Slider {
            Kirigami.FormData.label: qsTr("Speed:")
            from: 0.2; to: 3; value: root.current.speed
            onMoved: root.editor.setEffect(root.slot, "speed", value)
        }
        QQC2.Slider {
            Kirigami.FormData.label: qsTr("Brightness:")
            from: 0.1; to: 1; value: root.current.intensity
            onMoved: root.editor.setEffect(root.slot, "intensity", value)
        }
        QQC2.Button {
            visible: root.slot === "ambient"
            text: root.editor.playing ? qsTr("Pause preview") : qsTr("Play preview")
            icon.name: root.editor.playing ? "media-playback-pause" : "media-playback-start"
            onClicked: root.editor.setPlaying(!root.editor.playing)
        }
    }
}
