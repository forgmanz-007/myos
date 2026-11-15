/*
    The thin "Show desktop" strip at the very end of the taskbar, like Windows 10:
    click it to see the desktop, click again to get the windows back.

    SPDX-License-Identifier: GPL-2.0-or-later
*/
import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.kirigami as Kirigami
import org.kde.plasma.private.showdesktop

PlasmoidItem {
    id: root

    readonly property bool vertical: Plasmoid.formFactor === PlasmaCore.Types.Vertical
    readonly property int thickness: 6

    preferredRepresentation: fullRepresentation
    Plasmoid.backgroundHints: PlasmaCore.Types.NoBackground
    Plasmoid.constraintHints: Plasmoid.CanFillArea
    Plasmoid.title: i18n("Show desktop")
    Plasmoid.onActivated: showdesktop.toggleDesktop()

    Layout.minimumWidth: vertical ? -1 : thickness
    Layout.preferredWidth: Layout.minimumWidth
    Layout.maximumWidth: Layout.minimumWidth
    Layout.minimumHeight: vertical ? thickness : -1
    Layout.preferredHeight: Layout.minimumHeight
    Layout.maximumHeight: Layout.minimumHeight

    ShowDesktop {
        id: showdesktop
    }

    MouseArea {
        id: mouseArea
        anchors.fill: parent
        // Also the last few pixels up to the screen corner, so a quick push of the mouse
        // into the corner hits it.
        anchors.rightMargin: root.vertical ? 0 : -Kirigami.Units.smallSpacing
        anchors.bottomMargin: root.vertical ? -Kirigami.Units.smallSpacing : 0
        hoverEnabled: true
        activeFocusOnTab: true
        onClicked: showdesktop.toggleDesktop()
        Keys.onPressed: event => {
            if ([Qt.Key_Space, Qt.Key_Enter, Qt.Key_Return, Qt.Key_Select].includes(event.key)) {
                showdesktop.toggleDesktop();
            }
        }

        Accessible.name: Plasmoid.title
        Accessible.role: Accessible.Button

        Rectangle {
            anchors.fill: parent
            color: Kirigami.Theme.textColor
            opacity: mouseArea.pressed ? 0.20 : (mouseArea.containsMouse || showdesktop.showingDesktop ? 0.12 : 0)
        }

        // the dividing line
        Rectangle {
            width: root.vertical ? parent.width : 1
            height: root.vertical ? 1 : parent.height
            color: Kirigami.Theme.textColor
            opacity: 0.35
        }

        PlasmaCore.ToolTipArea {
            anchors.fill: parent
            mainText: showdesktop.showingDesktop ? i18n("Show windows") : i18n("Show desktop")
            textFormat: Text.PlainText
        }
    }
}
