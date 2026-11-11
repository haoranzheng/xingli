import typing
from typing import List, Sequence, Tuple

import mobase  # type: ignore
from PyQt5.QtCore import QCoreApplication
from PyQt5.QtWidgets import QMainWindow

from .page_file_manager import PageFileManager
from .utils import check_free_space, get_pagefiles_size, logger


class PageFileChecker(mobase.IPluginDiagnose):
    def __init__(self, pfm: PageFileManager) -> None:

        super().__init__()
        self._pfm: PageFileManager = pfm

    def init(self, organizer: mobase.IOrganizer) -> bool:

        # Set the internal organizer reference
        self._organizer: mobase.IOrganizer = organizer

        # Register the callback for the user interface initialization event
        self._organizer.onUserInterfaceInitialized(self.onUserInterfaceInitialized)

        # Log the initialization message
        logger.info("PageFileManagerChecker initialized")

        # Return True to indicate successful initialization
        return True

    def tr(self, txt: str) -> str:

        return QCoreApplication.translate("PageFileManager", txt)

    def author(self) -> str:

        return "MaskedRPGFan"

    def description(self) -> str:

        return self.tr("Automatically checks if the page file is set correctly. Displays notifications.")

    def name(self) -> str:
 
        return "PageFile Checker"

    def version(self) -> mobase.VersionInfo:

        return mobase.VersionInfo(0, 4, 0, mobase.ReleaseType.BETA)

    def onUserInterfaceInitialized(self, main_window: QMainWindow):

        # Check if there are any active problems.
        problems: list[int] = self.activeProblems()
        if len(problems) > 0:
            # Check if the "autostart" setting is enabled.
            if self._organizer.pluginSetting(self._pfm.name(), "autostart"):
                # If page file does not exists or is too small.
                if 0 <= problems[0] <= 2:
                    # Try to fix the problem.
                    self._pfm.display()

    def settings(self) -> Sequence[mobase.PluginSetting]:

        # Create a list of PluginSetting objects
        return []

    def activeProblems(self) -> typing.List[int]:

        if not self._organizer.pluginSetting(self._pfm.name(), "enable_notifications"):
            return []

        # Get the required pagefile size from the plugin settings
        required_mb: int = self._organizer.pluginSetting(self._pfm.name(), "pagefile_size")
        maximum_mb: int = self._organizer.pluginSetting(self._pfm.name(), "pagefile_size_max")

        # Get the current pagefile size
        pagefiles_size: List[Tuple[str, int, int]] = get_pagefiles_size()

        # Check if there is no pagefile
        if len(pagefiles_size) == 0:
            # Check if there is enough free space to create a pagefile
            if check_free_space(required_mb)[0]:
                # Return the key for the "pagefile does not exist" problem
                return [0]
            else:
                # Return the key for the "no free space for pagefile" problem
                return [2]

        # Get the maximum size of the pagefile
        initial_size_mb: int = sum([pagefile_size[1] for pagefile_size in pagefiles_size])
        max_size_mb: int = sum([pagefile_size[2] for pagefile_size in pagefiles_size])

        # Log the pagefile information
        for pagefile_size in pagefiles_size:
            logger.debug(self.tr("Pagefile found on drive {0} {1}-{2} MB.").format(pagefile_size[0].upper(), pagefile_size[1], pagefile_size[2]))

        # Check if the pagefile is too small
        if initial_size_mb < required_mb or max_size_mb < maximum_mb:
            # Check if there is enough free space to increase the pagefile
            if check_free_space(required_mb - max_size_mb)[0]:
                # Return the key for the "pagefile is too small" problem
                return [1]
            else:
                # Return the key for the "no free space for pagefile" problem
                return [2]

        # Check if the pc needs to be restarted
        if self._pfm._need_restart:
            return [3]

        # Return an empty list indicating no active problems
        return []

    def fullDescription(self, key: int) -> str:

        # Match the problem key and return the corresponding full description
        if key == 0:  # Pagefile does not exist
            # Return the localized description with the required pagefile size formatted in MB
            return self.tr("The pagefile does not exist. Required size {0}-{1}MB.").format(
                self._organizer.pluginSetting(self._pfm.name(), "pagefile_size"), self._organizer.pluginSetting(self._pfm.name(), "pagefile_size_max")
            )
        elif key == 1:  # Pagefile is too small
            # Return the localized description with the required pagefile size and current pagefile size formatted in MB
            current_min, current_max = get_pagefiles_size()
            return self.tr("The pagefile is too small. Required size {0}-{1}MB, current size {2}-{3}MB.").format(
                    self._organizer.pluginSetting(self._pfm.name(), "pagefile_size"),
                    self._organizer.pluginSetting(self._pfm.name(), "pagefile_size_max"),
                    current_min,
                    current_max,
                )
        elif key == 2:  # No free space is available
            return self.tr("No free space is available to create the pagefile.")
        elif key == 3:  # PC needs to be restarted
            return self.tr("The PC needs to be restarted to create the pagefile.")
        else:  # Unimplemented problem key
            raise NotImplementedError

    def hasGuidedFix(self, key: int) -> bool:

        # Match the problem key and return True if there is a guided fix
        if key == 0:  # Pagefile does not exist
            return True
        elif key == 1:  # Pagefile is too small
            return True
        elif key == 2:  # No free space is available
            return False
        elif key == 3:  # PC needs to be restarted
            return True
        else:  # Unimplemented problem key
                # Raise a NotImplementedError if the problem key is not implemented
                raise NotImplementedError

    def shortDescription(self, key: int) -> str:

        # Match the problem key and return the corresponding short description
        if key == 0:  # Pagefile does not exist
            return self.tr("The pagefile does not exist.")
        elif key == 1:  # Pagefile is too small
            return self.tr("The pagefile is too small.")
        elif key == 2:
            return self.tr("No free space for pagefile.")
        elif key == 3:  # PC needs to be restarted
            return self.tr("Need to restart the PC.")
        else:  # Unimplemented problem key
            # Raise a NotImplementedError if the problem key is not implemented
            raise NotImplementedError

    def startGuidedFix(self, key: int) -> None:

        # Match the problem key and call the corresponding function
        if key == 0:  # Pagefile does not exist
            # Call the display function to create the pagefile
            self._pfm.display()
        elif key == 1:  # Pagefile is too small
            # Call the display function to increase the pagefile size
            self._pfm.display()
        elif key == 3:  # PC needs to be restarted
            self._pfm.ask_for_restart()
        else:  # Unimplemented problem key
            # Raise a NotImplementedError if the problem key is not implemented
            raise NotImplementedError

    def master(self) -> str:

        # Return the name of the master plugin
        return self._pfm.name()
