pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami

GridLayout {
    id: root
    required property var editor
    property color second: "#da4453"

    columns: width > Kirigami.Units.gridUnit * 36 ? 2 : 1
    columnSpacing: Kirigami.Units.gridUnit * 2
    rowSpacing: Kirigami.Units.largeSpacing

    ColumnLayout {
        Layout.fillWidth: true
        Layout.alignment: Qt.AlignTop
        Layout.preferredWidth: 2
        Kirigami.Heading { level: 3; text: qsTr("Select keys") }
        GridLayout {
            columns: 2
            Layout.fillWidth: true
            uniformCellWidths: true
            Repeater {
                model: root.editor.profile.groups
                delegate: QQC2.Button {
                    id: groupButton
                    required property var modelData
                    Layout.fillWidth: true
                    Accessible.name: qsTr("Select %1").arg(modelData.label)
                    onClicked: root.editor.selectGroup(modelData.id)
                    leftPadding: Kirigami.Units.largeSpacing
                    rightPadding: Kirigami.Units.largeSpacing
                    contentItem: RowLayout {
                        Rectangle {
                            Layout.preferredWidth: Kirigami.Units.iconSizes.small
                            Layout.preferredHeight: Kirigami.Units.iconSizes.small
                            radius: 3
                            color: groupButton.modelData.color
                            border.color: Kirigami.Theme.disabledTextColor
                        }
                        QQC2.Label { Layout.fillWidth: true; text: groupButton.modelData.label; elide: Text.ElideRight }
                        QQC2.Label { text: groupButton.modelData.count; color: Kirigami.Theme.disabledTextColor }
                    }
                }
            }
        }
    }

    ColumnLayout {
        Layout.fillWidth: true
        Layout.alignment: Qt.AlignTop
        Layout.preferredWidth: 1
        enabled: root.editor.selection.count > 0
        Kirigami.Heading {
            level: 3
            text: root.editor.selection.count === 0 ? qsTr("No keys selected")
                : root.editor.selection.count === 1 ? qsTr("1 key selected")
                : qsTr("%1 keys selected").arg(root.editor.selection.count)
        }
        Kirigami.FormLayout {
            Layout.fillWidth: true
            ColorButton {
                id: first
                Kirigami.FormData.label: qsTr("Color:")
                description: qsTr("Color")
                value: root.editor.selection.color
                onPicked: color => {
                    value = color;
                    root.editor.fill(color);
                }
                Connections {
                    target: root.editor
                    function onSelectionChanged() { first.value = root.editor.selection.color; }
                }
            }
            ColorButton {
                Kirigami.FormData.label: qsTr("Blend to:")
                description: qsTr("Blend to")
                value: root.second
                onPicked: color => root.second = color
            }
        }
        Flow {
            Layout.fillWidth: true
            spacing: Kirigami.Units.smallSpacing
            QQC2.Button { text: qsTr("Blend across"); icon.name: "color-gradient"; onClicked: root.editor.blend(first.value.toString(), root.second.toString()) }
            QQC2.Button { text: qsTr("Use group color"); icon.name: "edit-reset"; onClicked: root.editor.resetToGroup() }
            QQC2.Button { text: qsTr("Deselect"); icon.name: "edit-select-none"; onClicked: root.editor.clearSelection() }
        }
    }
}
