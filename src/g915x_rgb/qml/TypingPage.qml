pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami

ColumnLayout {
    id: root
    required property var editor
    readonly property var reactive: editor.profile.reactive

    spacing: Kirigami.Units.largeSpacing

    Kirigami.InlineMessage {
        Layout.fillWidth: true
        visible: root.reactive.effect !== "" && root.editor.status.typing_available === false
        type: Kirigami.MessageType.Warning
        text: qsTr("The service cannot read this keyboard's key presses, so typing effects will not appear. See /usr/share/doc/g915x-rgb for how to allow it.")
    }
    Kirigami.Heading { level: 3; text: qsTr("When a key is pressed") }
    Flow {
        Layout.fillWidth: true
        spacing: Kirigami.Units.smallSpacing
        Repeater {
            model: root.editor.typingEffects
            delegate: QQC2.Button {
                required property var modelData
                text: modelData.label
                id: choice
                readonly property bool current: root.reactive.effect === modelData.id
                checkable: true
                checked: current
                icon.name: current ? "checkmark" : ""
                onClicked: {
                        root.editor.setEffect("reactive", "effect", choice.modelData.id);
                        // Clicking toggles the button; keep it tied to the profile.
                        choice.checked = Qt.binding(() => choice.current);
                    }
            }
        }
    }
    Kirigami.FormLayout {
        Layout.fillWidth: true
        enabled: root.reactive.effect !== ""
        ColorButton {
            Kirigami.FormData.label: qsTr("Color:")
            description: qsTr("Typing effect color")
            value: root.reactive.color
            onPicked: color => root.editor.setEffect("reactive", "color", color)
        }
        QQC2.Slider {
            Kirigami.FormData.label: qsTr("Strength:")
            from: 0.1; to: 1.5; value: root.reactive.intensity
            onMoved: root.editor.setEffect("reactive", "intensity", value)
        }
    }
    QQC2.Label {
        Layout.fillWidth: true
        wrapMode: Text.Wrap
        color: Kirigami.Theme.disabledTextColor
        text: qsTr("Click keys above to try the effect. While an effect is on, the service reads key presses from this keyboard only. Nothing is stored.")
    }
}
