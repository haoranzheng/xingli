# coding: utf-8
"""Resolve the correct Qt owner when Xingli itself runs as a modal QDialog.

The MO2 main window (organizer._parentWidget) is not the parent of dialogs
opened *from the Xingli assistant*. Parenting those dialogs to MO2 can put
them behind Xingli's already-modal main dialog.
"""
try:
    from PyQt6 import QtWidgets
except ImportError:
    from PyQt5 import QtWidgets


def _is_live_widget(widget, require_visible=False):
    try:
        return (
            isinstance(widget, QtWidgets.QWidget)
            and (not require_visible or widget.isVisible())
        )
    except (RuntimeError, TypeError):
        # Qt C++ widget may have been deleted while its Python wrapper remains.
        return False


def owner(controller):
    """Prefer currently displayed Xingli dialog; fall back to MO2 parent."""
    # In display(), controller.window = QDialog() and display() calls exec().
    main = getattr(controller, "window", None)
    if _is_live_widget(main, require_visible=True):
        return main
    try:
        external = controller._parentWidget()
        if _is_live_widget(external):
            return external
    except (AttributeError, RuntimeError, TypeError):
        pass
    return None


def show_owned_dialog(dialog, controller):
    """Make a nonblocking .show() child a proper modal dialog above its owner.

    Does NOT set WindowStaysOnTopHint, which can trap a window above unrelated
    applications. This only blocks input until the user closes this dialog.
    """
    if not isinstance(dialog, QtWidgets.QDialog):
        raise TypeError("Expected a QDialog")
    expected = owner(controller)
    if expected is not None and dialog.parentWidget() is not expected:
        # Always set parent before modal/shown; reparenting after show is unsafe.
        dialog.setParent(expected)
    dialog.setModal(True)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog
