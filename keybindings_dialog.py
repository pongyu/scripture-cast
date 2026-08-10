"""Popup dialog for recording custom keyboard shortcuts for common actions."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QHBoxLayout, QKeySequenceEdit, QLabel, QPushButton, QVBoxLayout,
)

from keybindings import ACTIONS, KeyBindings


class KeyBindingsDialog(QDialog):
    bindings_changed = Signal(KeyBindings)

    def __init__(self, bindings: KeyBindings, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Keyboard Shortcuts')

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Click a field, then press the key combination you want. Click "Clear" to unset.'))

        form = QFormLayout()
        self._edits: dict[str, QKeySequenceEdit] = {}
        for action, _default, label in ACTIONS:
            row = QHBoxLayout()
            edit = QKeySequenceEdit(QKeySequence(getattr(bindings, action)))
            self._edits[action] = edit
            row.addWidget(edit)
            clear_button = QPushButton('Clear')
            clear_button.clicked.connect(edit.clear)
            row.addWidget(clear_button)
            form.addRow(f'{label}:', row)
        layout.addLayout(form)

        button_row = QHBoxLayout()
        button_row.addStretch()
        save_button = QPushButton('Save')
        save_button.clicked.connect(self._on_save)
        button_row.addWidget(save_button)
        close_button = QPushButton('Close')
        close_button.clicked.connect(self.reject)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

    def _on_save(self):
        bindings = KeyBindings(**{
            action: self._edits[action].keySequence().toString()
            for action, _default, _label in ACTIONS
        })
        bindings.save()
        self.bindings_changed.emit(bindings)
        self.accept()
