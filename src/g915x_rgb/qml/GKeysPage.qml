pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami

ColumnLayout {
    id: root
    required property var editor

    spacing: Kirigami.Units.largeSpacing

    QQC2.Label {
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        text: qsTr("Choose what each G key does. For now, a G key can switch the lighting profile.")
    }

    Kirigami.InlineMessage {
        Layout.fillWidth: true
        visible: root.editor.status.typing_available === false
        type: Kirigami.MessageType.Warning
        text: qsTr("The service cannot read this keyboard's key presses, so the G keys will not work. See /usr/share/doc/g915x-rgb for how to allow it.")
    }

    Repeater {
        model: root.editor.profile.gkeys

        delegate: RowLayout {
            required property var modelData
            required property int index

            Layout.fillWidth: true
            spacing: Kirigami.Units.largeSpacing

            QQC2.Label {
                Layout.preferredWidth: 50
                text: modelData.name
            }

            QQC2.ComboBox {
                id: actionBox
                Layout.preferredWidth: 180
                model: [
                    { text: qsTr("No action"), value: "" },
                    { text: qsTr("Switch profile"), value: "switch_profile" }
                ]
                textRole: "text"
                valueRole: "value"

                Component.onCompleted: {
                    currentIndex = Math.max(0, indexOfValue(modelData.type));
                }

                onActivated: {
                    const target = targetBox.currentText;
                    root.editor.setGKeyAction(
                        modelData.address,
                        currentValue,
                        target
                    );
                }
            }

            QQC2.ComboBox {
                id: targetBox
                Layout.fillWidth: true
                model: root.editor.profiles.map(profile => profile.name)
                enabled: actionBox.currentValue === "switch_profile"
                currentIndex: Math.max(0, model.indexOf(modelData.target))

                onActivated: {
                    root.editor.setGKeyAction(
                        modelData.address,
                        actionBox.currentValue,
                        currentText
                    );
                }
            }
        }
    }

    Kirigami.Separator { Layout.fillWidth: true }

    QQC2.Label {
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        textFormat: Text.StyledText
        text: qsTr("To have a G key open an application or run a command, use KDE's shortcut settings:"
                   + "<ol>"
                   + "<li>Click <b>Open KDE shortcuts</b> below.</li>"
                   + "<li>Click <b>Add New</b> at the top right and choose <b>Command or Script</b>.</li>"
                   + "<li>Enter the command, such as <i>konsole</i>, and a name, such as <i>Open Konsole</i>. Click <b>Add</b>.</li>"
                   + "<li>With the new entry selected under <b>Commands</b>, click <b>Add…</b> under <b>Custom shortcuts</b>, then press the G key.</li>"
                   + "<li>Click <b>Apply</b>.</li>"
                   + "</ol>"
                   + "KDE may show the G key under its own name, such as \"Tools\" for G1. That is expected, and the shortcut still works.")
    }

    QQC2.Button {
        text: qsTr("Open KDE shortcuts")
        icon.name: "configure-shortcuts"
        onClicked: root.editor.openShortcutSettings()
    }
}
