# -*- coding: utf-8 -*-
"""Stability foundation shared by XingLi Assistant modules."""

import ast
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from typing import List, Tuple


_ROOT_LOGGER_NAME = "xingli_assistant"
_CONFIGURED = False
_LOG_PATH = ""


def configure_logging(organizer=None, plugin_dir: str = "") -> str:
    global _CONFIGURED, _LOG_PATH
    if _CONFIGURED:
        return _LOG_PATH

    root = logging.getLogger(_ROOT_LOGGER_NAME)
    root.setLevel(logging.DEBUG)
    root.propagate = False

    base = ""
    if organizer is not None:
        try:
            base = os.path.join(str(organizer.pluginDataPath()), "xingli_assistant", "logs")
        except Exception:
            base = ""
    if not base:
        base = os.path.join(plugin_dir or os.path.dirname(__file__), "logs")
    try:
        os.makedirs(base, exist_ok=True)
        _LOG_PATH = os.path.join(base, "XingliAssistant.log")
        handler = RotatingFileHandler(_LOG_PATH, maxBytes=1 * 1024 * 1024, backupCount=1, encoding="utf-8")
        handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"))
        root.addHandler(handler)
    except Exception:
        # Logging must never prevent MO2 from loading the plugin.
        _LOG_PATH = ""
        root.addHandler(logging.NullHandler())
    _CONFIGURED = True
    return _LOG_PATH


def get_logger(component: str = "core") -> logging.Logger:
    return logging.getLogger("{}.{}".format(_ROOT_LOGGER_NAME, component))


def log_exception(component: str, operation: str, exc: Exception) -> None:
    get_logger(component).exception("%s failed: %s", operation, exc)


class _CompatVisitor(ast.NodeVisitor):
    def __init__(self):
        self.findings = []

    @staticmethod
    def _attr_name(node):
        if isinstance(node, ast.Attribute):
            return node.attr
        return ""

    def visit_Call(self, node):
        name = self._attr_name(node.func)
        if name in {"pluginList", "waitForApplication", "onPluginStateChanged", "onModStateChanged", "onPluginMoved"}:
            self.findings.append((node.lineno, name))
        self.generic_visit(node)


def audit_mo244_compatibility(plugin_dir: str) -> List[Tuple[str, int, str]]:
    """Static source audit for APIs already proven hazardous in MO2 2.4.4."""
    findings = []
    for root, _dirs, files in os.walk(plugin_dir):
        for filename in files:
            if not filename.endswith(".py"):
                continue
            path = os.path.join(root, filename)
            try:
                with open(path, "r", encoding="utf-8-sig") as handle:
                    source = handle.read()
                tree = ast.parse(source, filename=path, feature_version=8)
                visitor = _CompatVisitor()
                visitor.visit(tree)
                for line, name in visitor.findings:
                    findings.append((os.path.relpath(path, plugin_dir), int(line), name))
            except SyntaxError as exc:
                findings.append((os.path.relpath(path, plugin_dir), int(exc.lineno or 0), "PY38_SYNTAX"))
            except Exception as exc:
                findings.append((os.path.relpath(path, plugin_dir), 0, "AUDIT_ERROR:{}".format(exc)))
    return findings


def log_runtime_compatibility(organizer, plugin_dir: str) -> List[Tuple[str, int, str]]:
    logger = get_logger("compat")
    logger.info("XingLi stability audit starting; Python=%s", sys.version.replace("\n", " "))
    try:
        import mobase
        enum_type = getattr(mobase, "ModState", None)
        logger.info("mobase.ModState=%r; ACTIVE=%r", enum_type, getattr(enum_type, "ACTIVE", None) if enum_type else None)
    except Exception as exc:
        logger.warning("mobase runtime inspection failed: %s", exc)
    try:
        logger.info("MO2 version=%s", organizer.version())
    except Exception:
        pass
    try:
        findings = audit_mo244_compatibility(plugin_dir)
    except Exception as exc:
        logger.exception("compatibility audit failed: %s", exc)
        findings = [("<audit>", 0, "AUDIT_ERROR")]
    if findings:
        for path, line, name in findings:
            logger.error("MO2 2.4.4 compatibility finding: %s:%s %s", path, line, name)
    else:
        logger.info("MO2 2.4.4 compatibility audit PASS: no forbidden state callbacks / blocking wait APIs")
    return findings
