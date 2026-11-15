/*
    Task View button for the taskbar, like Windows 10: shows all open windows
    (KWin's Overview, also on Meta+Tab).

    SPDX-License-Identifier: GPL-2.0-or-later
*/
import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.kirigami as Kirigami
import org.kde.plasma.workspace.dbus as DBus

PlasmoidItem {
    id: root

    readonly property bool vertical: Plasmoid.formFactor === PlasmaCore.Types.Vertical
    // 48 x 40 px like the Windows 10 button (at the usual font size)
    readonly property int length: Math.round(Kirigami.Units.gridUnit * 2.7)

    preferredRepresentation: fullRepresentation
    Plasmoid.backgroundHints: PlasmaCore.Types.NoBackground
    Plasmoid.constraintHints: Plasmoid.CanFillArea
    Plasmoid.title: i18n("Task View")
    Plasmoid.onActivated: showOverview()

    Layout.minimumWidth: vertical ? -1 : length
    Layout.preferredWidth: Layout.minimumWidth
    Layout.maximumWidth: Layout.minimumWidth
    Layout.minimumHeight: vertical ? Math.round(length * 0.85) : -1
    Layout.preferredHeight: Layout.minimumHeight
    Layout.maximumHeight: Layout.minimumHeight

    // Same as pressing the Overview shortcut.
    property DBus.dbusMessage overview: {
        "service": "org.kde.kglobalaccel",
        "path": "/component/kwin",
        "iface": "org.kde.kglobalaccel.Component",
        "member": "invokeShortcut",
        "arguments": ["Overview"]
    }

    function showOverview() {
        const reply = DBus.SessionBus.asyncCall(root.overview);
        reply.finished.connect(() => reply.destroy());
    }

    MouseArea {
        id: mouseArea
        anchors.fill: parent
        hoverEnabled: true
        activeFocusOnTab: true
        onClicked: root.showOverview()
        Keys.onPressed: event => {
            if ([Qt.Key_Space, Qt.Key_Enter, Qt.Key_Return, Qt.Key_Select].includes(event.key)) {
                root.showOverview();
            }
        }

        Accessible.name: Plasmoid.title
        Accessible.description: i18n("Shows all open windows")
        Accessible.role: Accessible.Button

        Rectangle {
            anchors.fill: parent
            color: Kirigami.Theme.textColor
            opacity: mouseArea.pressed ? 0.16 : (mouseArea.containsMouse ? 0.10 : 0)
        }

        Kirigami.Icon {
            anchors.centerIn: parent
            width: Kirigami.Units.iconSizes.smallMedium
            height: width
            source: Qt.resolvedUrl("../images/taskview.svg")
            isMask: true
            color: Kirigami.Theme.textColor
        }

        PlasmaCore.ToolTipArea {
            anchors.fill: parent
            mainText: Plasmoid.title
            subText: i18n("See all open windows (Meta+Tab)")
            textFormat: Text.PlainText
        }
    }
}
