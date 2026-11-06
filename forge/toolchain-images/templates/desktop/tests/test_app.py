from PySide6.QtWidgets import QApplication,QPushButton,QLabel
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
from app import make_window
def test_keyboard():
 app=QApplication.instance() or QApplication([]);w=make_window();w.show();b=w.findChild(QPushButton);b.setFocus();QTest.keyClick(b,Qt.Key_Space);app.processEvents();assert w.findChild(QLabel).text()=='42';assert b.accessibleName()=='Increment';w.close()
