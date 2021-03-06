from datetime import datetime, timezone
import ephem
import math
import os

from qgis.PyQt import uic
from qgis.PyQt.QtCore import *
from qgis.PyQt.QtGui import *
from qgis.PyQt.QtWidgets import *
from qgis.core import *
from qgis.gui import *
from kadas.kadasgui import *
from kadas.kadasanalysis import *

from .ui_EphemToolWidget import Ui_EphemToolWidget

class EphemTool(QgsMapTool):

    def __init__(self, iface):
        QgsMapTool.__init__(self, iface.mapCanvas())

        self.iface = iface
        self.setCursor(Qt.ArrowCursor)
        self.widget = EphemToolWidget(self.iface)
        self.widget.close.connect(self.close)

    def activate(self):
        self.widget.setVisible(True)
        self.pin = KadasSymbolItem(self.iface.mapCanvas().mapSettings().destinationCrs())
        self.pin.setup( ":/kadas/icons/pin_blue", 0.5, 1.0 );
        self.pinAdded = False
        QgsMapTool.activate(self)

    def deactivate(self):
        self.widget.setVisible(False)
        if self.pinAdded:
            KadasMapCanvasItemManager.removeItem(self.pin)
            self.pinAdded = False
        self.pin = None
        QgsMapTool.deactivate(self)

    def close(self):
        self.iface.mapCanvas().unsetMapTool(self)

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.positionPicked(self.toMapCoordinates(event.pos()))
        elif event.button() == Qt.RightButton:
            self.iface.mapCanvas().unsetMapTool(self)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.iface.mapCanvas().unsetMapTool( self )

    def positionPicked(self, pos):
        self.pin.setPosition(KadasItemPos.fromPoint(pos))
        if not self.pinAdded:
            KadasMapCanvasItemManager.addItem(self.pin)
            self.pinAdded = True
        mapCrs = self.iface.mapCanvas().mapSettings().destinationCrs()
        wgsCrs = QgsCoordinateReferenceSystem("EPSG:4326")
        mrcCrs = QgsCoordinateReferenceSystem("EPSG:3857")
        wgsCrst = QgsCoordinateTransform(mapCrs, wgsCrs, QgsProject.instance())
        mrcCrst = QgsCoordinateTransform(mapCrs, mrcCrs, QgsProject.instance())
        self.widget.setPos(wgsCrst.transform(pos), mrcCrst.transform(pos))
        self.widget.recompute()

class EphemToolWidget(KadasBottomBar):

    close = pyqtSignal()

    def __init__(self, iface):
        KadasBottomBar.__init__(self, iface.mapCanvas())

        self.iface = iface

        self.setLayout(QHBoxLayout())
        self.layout().setSpacing(10)

        base = QWidget()
        self.ui = Ui_EphemToolWidget()
        self.ui.setupUi(base)
        self.layout().addWidget(base)

        closeButton = QPushButton()
        closeButton.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        closeButton.setIcon(QIcon(":/kadas/icons/close"))
        closeButton.setToolTip(self.tr("Close"))
        closeButton.clicked.connect(self.close)
        self.layout().addWidget(closeButton)
        self.layout().setAlignment(closeButton, Qt.AlignTop)

        self.ui.dateTimeEdit.setDateTime(QDateTime.currentDateTime())
        self.ui.dateTimeEdit.editingFinished.connect(self.recompute)
        self.ui.checkBoxRelief.toggled.connect(self.recompute)
        self.ui.tabWidgetOutput.setEnabled(False)

        self.busyOverlay = QLabel(self.tr("Calculating..."))
        self.busyOverlay.setStyleSheet("QLabel { background-color: white;}")
        self.busyOverlay.setAlignment(Qt.AlignHCenter|Qt.AlignVCenter)
        self.busyOverlay.setVisible(False)
        self.ui.gridLayout.addWidget(self.busyOverlay, 4, 0, 1, 5)

        self.wgsPos = None
        self.mrcPos = None

    def getTimestamp(self):
        return self.ui.dateTimeEdit.dateTime().toSecsSinceEpoch()

    def setPos(self, wgsPos, mrcPos):
        self.wgsPos = wgsPos
        self.mrcPos = mrcPos

    def recompute(self):
        if not self.wgsPos:
            return

        if self.ui.checkBoxRelief.isChecked():
            self.busyOverlay.setVisible(True)
            QApplication.instance().processEvents(QEventLoop.ExcludeUserInputEvents)

        self.ui.tabWidgetOutput.setEnabled(True)
        font = self.ui.labelPositionValue.font()
        font.setItalic(False)
        self.ui.labelPositionValue.setFont(font)
        x_str = QgsCoordinateFormatter.formatX(self.wgsPos.x(), QgsCoordinateFormatter.FormatDegreesMinutesSeconds, 1)
        y_str = QgsCoordinateFormatter.formatY(self.wgsPos.y(), QgsCoordinateFormatter.FormatDegreesMinutesSeconds, 1)
        self.ui.labelPositionValue.setText(y_str + " " + x_str)

        home = ephem.Observer()
        home.lat = str(self.wgsPos.y())
        home.lon = str(self.wgsPos.x())
        home.date = ephem.Date(datetime.fromtimestamp(self.getTimestamp(), timezone.utc))

        ## Sun ##

        sun = ephem.Sun()
        sun.compute(home)
        self.ui.labelAzimuthElevationValue.setText("%s %s" % (self.formatDMS(sun.az), self.formatDMS(sun.alt, True)))

        # Compute sunrise and sunset taking relief into account
        try:
            sunset = ephem.to_timezone(home.next_setting(sun), ephem.UTC).timestamp()
        except:
            sunset = None

        sun.compute(home)
        suntransit = ephem.to_timezone(home.next_transit(sun), ephem.UTC).timestamp()
        if sunset and suntransit > sunset:
            suntransit = ephem.to_timezone(home.previous_transit(sun), ephem.UTC).timestamp()

        sun.compute(home)
        if sun.alt >= 0:
            try:
                sunrise = ephem.to_timezone(home.previous_rising(sun), ephem.UTC).timestamp()
            except:
                sunrise = None
        else:
            try:
                sunrise = ephem.to_timezone(home.next_rising(sun), ephem.UTC).timestamp()
            except:
                sunrise = None
        if self.ui.checkBoxRelief.isChecked():
            sunset = self.search_body_relief_crossing(ephem.Sun(), sunset, suntransit) if sunset else None
            sunrise = self.search_body_relief_crossing(ephem.Sun(), sunrise, suntransit) if sunrise else None

        if sunrise and (not sunset or sunrise < sunset):
            self.ui.labelSunRiseValue.setText("%s" % self.timestampToHourString(sunrise))
        else:
            self.ui.labelSunRiseValue.setText("-")
        if sunset and (not sunrise or sunset > sunrise):
            self.ui.labelSunSetValue.setText("%s" % self.timestampToHourString(sunset))
        else:
            self.ui.labelSunSetValue.setText("-")
        self.ui.labelZenithValue.setText(self.timestampToHourString(suntransit))

        ## Moon ##

        moon = ephem.Moon()
        moon.compute(home)
        self.ui.labelMoonAzimuthElevationValue.setText("%s %s" % (self.formatDMS(moon.az), self.formatDMS(moon.alt, True)))

        # Compute moonrise and moonset taking relief into account
        try:
            moonset = ephem.to_timezone(home.next_setting(moon), ephem.UTC).timestamp()
        except:
            moonset = None

        moon.compute(home)
        moontransit = ephem.to_timezone(home.next_transit(moon), ephem.UTC).timestamp()
        if moonset and moontransit > moonset:
            moontransit = ephem.to_timezone(home.previous_transit(moon), ephem.UTC).timestamp()

        moon.compute(home)
        if moon.alt >= 0:
            try:
                moonrise = ephem.to_timezone(home.previous_rising(moon), ephem.UTC).timestamp()
            except:
                moonrise = None
        else:
            try:
                moonrise = ephem.to_timezone(home.next_rising(moon), ephem.UTC).timestamp()
            except:
                moonrise = None
        if self.ui.checkBoxRelief.isChecked():
            moonset = self.search_body_relief_crossing(ephem.Moon(), moonset, moontransit) if moonset else None
            moonrise = self.search_body_relief_crossing(ephem.Moon(), moonrise, moontransit) if moonrise else None

        if moonrise and (not moonset or moonrise < moonset):
            self.ui.labelMoonRiseValue.setText("%s" % self.timestampToHourString(moonrise))
        else:
            self.ui.labelMoonRiseValue.setText("-")
        if moonset and (not moonrise or moonset > moonrise):
            self.ui.labelMoonSetValue.setText("%s" % self.timestampToHourString(moonset))
        else:
            self.ui.labelMoonSetValue.setText("-")

        # Moon phase
        moon_image_suffix = math.floor(round(moon.phase/12.5)*12.5)
        self.ui.labelMoonPhaseIcon.setPixmap(QPixmap(":/plugins/Ephem/icons/moon_%d.svg" % moon_image_suffix))
        self.ui.labelMoonPhaseValue.setText("%.2f%%" % moon.phase)

        self.busyOverlay.setVisible(False)

    def timestampToHourString(self, timestamp):
        return QDateTime.fromSecsSinceEpoch(round(timestamp)).toString("hh:mm")

    def formatDMS(self, val, sign=False):
        strval = str(val)
        if sign and strval[0] != "-":
            strval = "+" + strval
        parts = strval.split(":")
        if len(parts) == 3:
            return parts[0] + "??" + parts[1] + "'" + parts[2] + "\"";
        else:
            return strval

    def search_body_relief_crossing(self, body, spheretime, zenithtime):
        # Binary search up to 1min precision
        mid = 0.5 * (spheretime + zenithtime)
        if abs(spheretime - zenithtime) < 60:
            return mid
        if self.body_is_visible(self.compute_body_position(mid, body)):
            return self.search_body_relief_crossing(body, spheretime, mid)
        else:
            return self.search_body_relief_crossing(body, mid, zenithtime)

    def compute_body_position(self, timestamp, body):
        home = ephem.Observer()
        home.lat = str(self.wgsPos.y())
        home.lon = str(self.wgsPos.x())
        home.date = ephem.Date(datetime.fromtimestamp(timestamp, timezone.utc))
        body.compute(home)
        azimuth_rad = float(body.az)
        alt_rad = float(body.alt)
        # Assume body 100km distant
        r = 100000
        body_x = self.mrcPos.x() + r * math.sin(azimuth_rad) * math.cos(alt_rad)
        body_y = self.mrcPos.y() + r * math.cos(azimuth_rad) * math.cos(alt_rad)
        body_z = r * math.sin(alt_rad)
        return QgsPoint(body_x, body_y, body_z)

    def body_is_visible(self, body_pos):
        # 100m resolution
        return KadasLineOfSight.computeTargetVisibility(QgsPoint(self.mrcPos.x(), self.mrcPos.y(), 0), body_pos, QgsCoordinateReferenceSystem("EPSG:3857"), 10000, False, True)
