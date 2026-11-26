pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami

ColumnLayout {
    id: root
    required property var editor
    readonly property var names: editor.profiles.map(p => p.name)

    spacing: Kirigami.Units.largeSpacing

    RowLayout {
        Kirigami.Heading { Layout.fillWidth: true; level: 3; text: qsTr("Switch profile by application") }
        QQC2.Switch {
            text: qsTr("Enabled")
            checked: root.editor.rules.enabled
            onToggled: root.editor.setRulesEnabled(checked)
        }
    }
    Kirigami.InlineMessage {
        Layout.fillWidth: true
        visible: root.editor.rules.enabled && root.editor.status.auto_switch === false
        type: Kirigami.MessageType.Information
        text: qsTr("Switching by application needs KDE Plasma.")
    }
    Repeater {
        model: root.editor.rules.items
        delegate: RowLayout {
            id: rule
            required property var modelData
            required property int index
            QQC2.TextField {
                Layout.fillWidth: true
                text: rule.modelData.pattern
                Accessible.name: qsTr("Application")
                onEditingFinished: if (text !== rule.modelData.pattern) root.editor.updateRule(rule.index, "class_regex", text)
            }
            QQC2.ComboBox {
                model: root.names
                currentIndex: root.names.indexOf(rule.modelData.profile)
                Accessible.name: qsTr("Profile")
                onActivated: root.editor.updateRule(rule.index, "profile", currentText)
            }
            QQC2.ComboBox {
                model: root.editor.transitions
                textRole: "label"
                valueRole: "id"
                currentIndex: root.editor.transitions.findIndex(t => t.id === rule.modelData.transition)
                Accessible.name: qsTr("Transition")
                onActivated: root.editor.updateRule(rule.index, "transition", currentValue)
            }
            QQC2.ToolButton {
                icon.name: "edit-delete-remove"
                Accessible.name: qsTr("Remove rule")
                onClicked: root.editor.removeRule(rule.index)
            }
        }
    }
    RowLayout {
        QQC2.TextField {
            id: pattern
            Layout.fillWidth: true
            placeholderText: qsTr("Application, such as konsole")
            Accessible.name: qsTr("New rule application")
            onAccepted: add.clicked()
        }
        QQC2.Button { text: qsTr("Pick a window"); icon.name: "crosshairs"; onClicked: root.editor.pickWindow() }
        QQC2.ComboBox { id: target; model: root.names; Accessible.name: qsTr("New rule profile") }
        QQC2.Button {
            id: add
            text: qsTr("Add rule")
            icon.name: "list-add"
            enabled: pattern.text.trim() !== ""
            onClicked: {
                root.editor.addRule(pattern.text, target.currentText);
                pattern.clear();
            }
        }
    }
    RowLayout {
        QQC2.Label { text: qsTr("Default fallback profile:") }
        QQC2.ComboBox {
            id: fallback
            Layout.fillWidth: true
            // The first entry keeps whichever profile was chosen last.
            model: [qsTr("None: keep the last chosen profile")].concat(root.names)
            currentIndex: root.names.indexOf(root.editor.rules.fallback) + 1
            Accessible.name: qsTr("Default fallback profile")
            onActivated: index => root.editor.setFallback(index > 0 ? currentText : "")
        }
    }
    QQC2.Label {
        Layout.fillWidth: true
        wrapMode: Text.Wrap
        color: Kirigami.Theme.disabledTextColor
        text: qsTr("The first matching rule wins. Applications no rule matches use the default fallback profile. Patterns can be names, wildcards such as *konsole*, or regular expressions.")
    }
    Connections {
        target: root.editor
        function onWindowPicked(name) {
            // Match this application exactly, whatever characters it contains.
            pattern.text = "^" + name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "$";
        }
    }
}
