import sys
from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout,QLabel,QPushButton
from PySide6.QtCore import QTimer
def make_window():
 w=QWidget();w.setWindowTitle('Forge Counter');layout=QVBoxLayout(w);label=QLabel('41');label.setAccessibleName('Count');button=QPushButton('Increment');button.setAccessibleName('Increment');button.clicked.connect(lambda:label.setText(str(int(label.text())+1)));layout.addWidget(label);layout.addWidget(button);return w
if __name__=='__main__':
 app=QApplication(sys.argv);window=make_window();window.show()
 if '--selftest' in sys.argv:QTimer.singleShot(100,app.quit)
 sys.exit(app.exec())
