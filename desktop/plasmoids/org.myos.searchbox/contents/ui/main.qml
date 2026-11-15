/*
    "Type here to search" box for the taskbar, like Windows 10. Click it (or press Meta+S)
    and type: apps, settings, files and quick answers (sums, units) appear above it.
    Enter opens the first result, Esc closes.

    What you type is shown in the box on the taskbar itself; the actual text field sits,
    invisible, in the pop-up (a taskbar cannot take keyboard input itself).

    SPDX-License-Identifier: GPL-2.0-or-later
*/
import QtQuick
import QtQuick.Layouts
import QtQuick.Window
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.extras as PlasmaExtras
import org.kde.kirigami as Kirigami
import org.kde.milou as Milou

PlasmoidItem {
    id: root

    property string query: ""
    property int cursor: 0
    readonly property bool vertical: Plasmoid.formFactor === PlasmaCore.Types.Vertical
    readonly property string placeholder: i18n("Type here to search")

    preferredRepresentation: compactRepresentation
    Plasmoid.constraintHints: Plasmoid.CanFillArea
    Plasmoid.title: i18n("Search")
    toolTipMainText: ""
    toolTipSubText: ""

    onExpandedChanged: {
        if (expanded) {
            focusTimer.start();
        } else if (fullRepresentationItem) {
            fullRepresentationItem.input.text = "";
        }
    }

    // The pop-up is not always on screen yet when it opens; give it a moment.
    Timer {
        id: focusTimer
        interval: 100
        onTriggered: {
            if (root.fullRepresentationItem) {
                root.fullRepresentationItem.input.forceActiveFocus();
            }
        }
    }

    compactRepresentation: MouseArea {
        id: box

        readonly property int boxWidth: Kirigami.Units.gridUnit * (Screen.width < 1100 ? 11 : 18)

        Layout.minimumWidth: root.vertical ? -1 : boxWidth
        Layout.preferredWidth: Layout.minimumWidth
        Layout.maximumWidth: Layout.minimumWidth
        Layout.minimumHeight: root.vertical ? Math.round(Kirigami.Units.gridUnit * 2.3) : -1
        Layout.preferredHeight: Layout.minimumHeight
        Layout.maximumHeight: Layout.minimumHeight

        hoverEnabled: true
        activeFocusOnTab: true
        property bool wasExpanded
        onPressed: wasExpanded = root.expanded
        onClicked: root.expanded = !wasExpanded
        Keys.onPressed: event => {
            if ([Qt.Key_Space, Qt.Key_Enter, Qt.Key_Return, Qt.Key_Select].includes(event.key)) {
                root.expanded = !root.expanded;
            }
        }

        Accessible.name: root.placeholder
        Accessible.role: Accessible.Button

        // The light box (on a horizontal taskbar)
        Rectangle {
            anchors.fill: parent
            visible: !root.vertical
            color: root.expanded || box.containsMouse ? "#ffffff" : "#f2f2f2"

            Kirigami.Icon {
                id: glass
                anchors {
                    left: parent.left
                    leftMargin: Math.round(Kirigami.Units.gridUnit * 0.7)
                    verticalCenter: parent.verticalCenter
                }
                width: Kirigami.Units.iconSizes.small
                height: width
                source: Qt.resolvedUrl("../images/search.svg")
                isMask: true
                color: "#000000"
            }

            Text {
                id: label
                anchors {
                    left: glass.right
                    leftMargin: Math.round(Kirigami.Units.gridUnit * 0.6)
                    right: parent.right
                    rightMargin: Math.round(Kirigami.Units.gridUnit * 0.6)
                    verticalCenter: parent.verticalCenter
                }
                text: root.query.length > 0 ? root.query : root.placeholder
                textFormat: Text.PlainText
                color: root.query.length > 0 ? "#000000" : "#5d5d5d"
                elide: root.query.length > 0 ? Text.ElideLeft : Text.ElideRight
                font: Kirigami.Theme.defaultFont
                renderType: Text.NativeRendering
            }

            TextMetrics {
                id: beforeCursor
                font: label.font
                text: root.query.substring(0, root.cursor)
            }

            // blinking text cursor while the search is open
            Rectangle {
                x: label.x + Math.min(label.width, beforeCursor.advanceWidth)
                anchors.verticalCenter: parent.verticalCenter
                width: 1
                height: label.font.pixelSize > 0 ? label.font.pixelSize * 1.3 : Kirigami.Units.gridUnit
                color: "#000000"
                visible: root.expanded && blink.shown
            }

            Timer {
                id: blink
                property bool shown: true
                interval: 530
                repeat: true
                running: root.expanded
                onTriggered: shown = !shown
                onRunningChanged: shown = true
            }
        }

        // On a vertical taskbar: just the magnifying glass
        Kirigami.Icon {
            anchors.centerIn: parent
            visible: root.vertical
            width: Kirigami.Units.iconSizes.smallMedium
            height: width
            source: Qt.resolvedUrl("../images/search.svg")
            isMask: true
            color: Kirigami.Theme.textColor
        }
    }

    fullRepresentation: Item {
        id: popup

        property alias input: input

        Layout.preferredWidth: Kirigami.Units.gridUnit * 34
        Layout.preferredHeight: Kirigami.Units.gridUnit * 26
        Layout.minimumWidth: Kirigami.Units.gridUnit * 24
        Layout.minimumHeight: Kirigami.Units.gridUnit * 16

        PlasmaExtras.PlaceholderMessage {
            anchors.centerIn: parent
            width: parent.width - Kirigami.Units.gridUnit * 4
            visible: root.query.length === 0
            iconName: "search"
            text: i18n("Search apps, settings and files")
            explanation: i18n("Start typing. Enter opens the first result.")
        }

        Milou.ResultsView {
            id: results
            anchors.fill: parent
            visible: root.query.length > 0
            clip: true
            queryField: input
            reversed: Plasmoid.location === PlasmaCore.Types.BottomEdge

            onActivated: root.expanded = false
            onUpdateQueryString: (text, cursorPosition) => {
                input.text = text;
                input.cursorPosition = cursorPosition;
            }
        }

        // What you type goes here; the box on the taskbar shows it.
        TextInput {
            id: input
            width: 1
            height: 1
            opacity: 0
            focus: true
            Keys.forwardTo: [results]
            onTextChanged: {
                root.query = text;
                searchDelay.restart();
            }
            onCursorPositionChanged: root.cursor = cursorPosition
            Keys.onEscapePressed: {
                if (text.length > 0) {
                    text = "";
                } else {
                    root.expanded = false;
                }
            }
            Accessible.name: root.placeholder
            Accessible.role: Accessible.EditableText
        }

        // Fewer searches while typing quickly.
        Timer {
            id: searchDelay
            interval: 150
            onTriggered: results.setQueryString(input.text)
        }
    }
}
