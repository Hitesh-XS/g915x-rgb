import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Dialogs
import org.kde.kirigami as Kirigami

QQC2.Button {
    id: root

    property color value: "white"
    property string description
    signal picked(string color)

    implicitWidth: Kirigami.Units.gridUnit * 4
    Accessible.name: description
    onClicked: {
        dialog.selectedColor = value;
        dialog.open();
    }
    contentItem: Rectangle {
        implicitHeight: Kirigami.Units.iconSizes.small
        radius: Kirigami.Units.cornerRadius
        color: root.value
        border.color: Kirigami.Theme.textColor
        border.width: 1
    }

    ColorDialog {
        id: dialog
        title: root.description
        onAccepted: root.picked(selectedColor.toString())
    }
}
