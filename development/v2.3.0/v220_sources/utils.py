import typing
import json
import logging
import os
import re
import shutil
import subprocess
import winreg
from pathlib import Path
from typing import Any, Dict, List, Tuple

logger: logging.Logger = logging.getLogger("PageFileManager")


def get_base_path() -> str:
    """
    Returns the base path of the script.
    """
    return os.path.dirname(os.path.abspath(__file__))


def create_logger() -> None:
    """
    Creates a logger with a file handler and sets it to the DEBUG level.
    Removes all existing handlers from the logger.
    """
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    script_dir: str = get_base_path()
    logs_dir: str = os.path.abspath(os.path.join(script_dir, "..", "..", "logs"))
    Path(logs_dir).mkdir(parents=True, exist_ok=True)
    log_path: str = os.path.abspath(os.path.join(logs_dir, "PageFileManager.log"))
    with open(log_path, "w") as _:
        pass

    # Create a file handler that logs messages to "logs\PageFileManager.log"
    file_handler = logging.FileHandler(log_path, encoding="utf-8", mode="w")

    # Set the format of the log messages
    formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s")
    file_handler.setFormatter(formatter)

    # Add the file handler to the logger
    logger.addHandler(file_handler)

    # Set the logger to the DEBUG level
    logger.setLevel(logging.DEBUG)


def get_free_space_mb(drive_letter: typing.Optional[str] = None) -> float:
    """
    Returns the free disk space in megabytes available on the drive.

    Args:
        drive_letter (str | None): The drive letter of the drive to query. If None, the drive letter of the drive
            from which this script is run will be used.

    Returns:
        float: The free disk space in megabytes.
    """
    # Get the drive letter of the drive from which the script is run
    if drive_letter is None or not os.path.exists(drive_letter):
        drive_letter = os.path.splitdrive(os.path.abspath(__file__))[0]

    # Get the total, used, and free disk space using shutil.disk_usage
    # This function returns a named tuple with the attributes total, used, and free
    disk_usage: shutil._ntuple_diskusage = shutil.disk_usage(drive_letter)

    # Convert the free disk space from bytes to megabytes
    free_mb: float = disk_usage.free / (1024**2)

    return free_mb


def check_free_space(required_mb: int) -> typing.Tuple[bool, float]:
    """
    Checks if there is enough free space to allocate the pagefile.

    Args:
        required_mb (int): The required free disk space in megabytes.

    Returns:
        tuple[bool, float]: A tuple with a boolean indicating if there is enough free space available,
            and the available free space in megabytes.
    """
    free_mb: float = get_free_space_mb()
    if free_mb < required_mb:
        # If there is not enough free space return False
        return False, free_mb
    else:
        return True, free_mb


def get_pagefiles_size() -> List[Tuple[str, int, int]]:
    """
    Returns the pagefile sizes for all drives.

    This function reads the PagingFiles registry value and returns a list of tuples
    containing the drive letter, initial size, and maximum size of each pagefile.

    Returns:
        List[Tuple[str, int, int]]: A list of tuples with the drive letter, initial size, and maximum size in MB.
                                    If no pagefiles are found, the list is empty.
    """
    pagefile_sizes: List[Tuple[str, int, int]] = []

    try:
        # Open the registry key containing the PagingFiles value
        reg_key: winreg.HKEYType = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management")

        # Query the PagingFiles value from the registry
        paging_files, reg_type = winreg.QueryValueEx(reg_key, "PagingFiles")
        winreg.CloseKey(reg_key)

        # Iterate over the pagefiles
        for pagefile in paging_files:
            parts = pagefile.split(" ")
            path = parts[0].lower()
            drive_letter = os.path.splitdrive(path)[0]
            if len(parts) >= 3:
                # Extract the initial and maximum pagefile size from the parts
                initial_size = int(parts[1])
                max_size = int(parts[2])
                pagefile_sizes.append((drive_letter, initial_size, max_size))

        return pagefile_sizes

    except FileNotFoundError:
        # If the PagingFiles value is not found, log a warning and return an empty list
        logger.warning("PagingFiles value not found in registry.")
        return []


def get_current_pagefile_settings() -> typing.Optional[list]:
    """
    Returns the current pagefile settings from the registry.

    Returns:
        typing.Optional[list]: A list of pagefile settings, where each setting is a string
            containing the path to the pagefile and its initial and maximum size in
            megabytes, separated by spaces. If no pagefile is found, returns None.
    """
    try:
        # Open the registry key that contains the pagefile settings
        reg_key: winreg.HKEYType = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management")

        # Query the PagingFiles value from the registry
        paging_files, reg_type = winreg.QueryValueEx(reg_key, "PagingFiles")

        # Close the registry key
        winreg.CloseKey(reg_key)

        return paging_files

    except FileNotFoundError:
        # If the PagingFiles value is not found, return None
        logger.warning("PagingFiles value not found in registry.")
        return None


def set_pagefile_size(initial_size_mb: int, max_size_mb: int, total_initial_size_mb: int = 0, total_max_size_mb: int = 0) -> None:
    """
    Sets the pagefile size on the current drive to the specified initial and maximum sizes.

    Args:
        initial_size_mb (int): The initial size of the pagefile in megabytes.
        max_size_mb (int): The maximum size of the pagefile in megabytes.

    Returns:
        None
    """
    # Get the current drive letter
    drive_letter: str = os.path.splitdrive(os.path.abspath(__file__))[0].lower()

    if initial_size_mb > total_initial_size_mb:
        initial_size_mb -= total_initial_size_mb
    else:
        initial_size_mb = 0

    if max_size_mb > total_max_size_mb:
        max_size_mb -= total_max_size_mb
    else:
        max_size_mb = 0

    if initial_size_mb < 0:
        initial_size_mb = 0

    if initial_size_mb > max_size_mb:
        max_size_mb = initial_size_mb

    try:
        # Get the current pagefile settings
        paging_files = get_current_pagefile_settings()
        new_paging_files: list = []

        # Create a new pagefile entry
        pagefile_entry: str = f"{drive_letter}\\pagefile.sys {initial_size_mb} {max_size_mb}"
        updated = False

        # Iterate over the current pagefile settings
        for pagefile in paging_files:
            if pagefile.lower().startswith(drive_letter):
                # If the current drive is found, update the pagefile entry
                logger.info(f"Found, updating: {pagefile_entry}")
                new_paging_files.append(pagefile_entry)
                updated = True
            else:
                # If the current drive is not found, keep the existing pagefile entry
                new_paging_files.append(pagefile)

        # If the current drive is not found in the pagefile settings, add a new pagefile entry
        if not updated:
            new_paging_files.append(pagefile_entry)

        # Create a string of the new pagefile entries, separated by null characters
        new_paging_files_str: str = "\\0".join([f"{entry}" for entry in new_paging_files])

        # Set the new pagefile entries in the registry
        subKey = "SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Memory Management"
        args: str = '\'add "{}\\{}" /v "{}" /t REG_MULTI_SZ /d "{}" /f\''.format("HKLM", subKey, "PagingFiles", new_paging_files_str)
        cmd: list[str] = ["powershell", "Start-Process", "-Verb", "runAs", "reg", "-ArgumentList", args]
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        subprocess.check_call(cmd, startupinfo=si)

        # Output a message indicating the pagefile has been set
        logger.info(f"The paging file has been set on drive {drive_letter} with an initial size of {initial_size_mb} MB and a maximum size of {max_size_mb} MB.")

    except subprocess.CalledProcessError as e:
        # Output an error message if the pagefile could not be set
        logger.error(f"Unable to change pagefile: {e}")
    except Exception as e:
        # Output an error message for any other exception
        logger.error(f"Unknown error: {e}")


def reset_pagefile_to_system_managed() -> None:
    """
    Resets the pagefile to system managed size on all drives.
    """
    try:
        # Command to set pagefile to system managed on all drives
        # This command clears the PagingFiles registry value, which effectively
        # tells Windows to manage pagefile sizes automatically.
        subKey = "SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Memory Management"
        args: str = '\'remove "{}\\{}" /v "{}" /f\''.format("HKLM", subKey, "PagingFiles")
        cmd: list[str] = ["powershell", "Start-Process", "-Verb", "runAs", "reg", "-ArgumentList", args]
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        subprocess.check_call(cmd, startupinfo=si)
        logger.info("Pagefile has been reset to system managed.")
    except subprocess.CalledProcessError as e:
        logger.error(f"Unable to reset pagefile to system managed: {e}")
    except Exception as e:
        logger.error(f"Unknown error when resetting pagefile: {e}")

def open_virtual_memory_settings() -> None:
    """
    Opens the system's virtual memory settings dialog.
    """
    try:
        # Command to open the System Properties -> Advanced -> Performance Options -> Virtual Memory dialog
        subprocess.run(["control", "sysdm.cpl,,3"], check=True, shell=True)
        logger.info("Opened system virtual memory settings.")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to open system virtual memory settings: {e}")
    except Exception as e:
        logger.error(f"Unknown error when opening system settings: {e}")

def run_powershell_command(command: str) -> str:
    """
    Runs a PowerShell command and returns the output.

    Args:
        command (str): The PowerShell command to run.

    Returns:
        str: The output of the PowerShell command.
    """
    # Run the PowerShell command and capture the output
    result: subprocess.CompletedProcess[str] = subprocess.run(["powershell", "-Command", command], capture_output=True, text=True)

    # Return the output of the PowerShell command
    return result.stdout


def extract_disk_and_partition(input_str: str) -> typing.Optional[typing.Tuple[int, int]]:
    """
    Extracts the disk and partition numbers from a string.

    Args:
        input_str (str): The string to extract the disk and partition numbers from.

    Returns:
        tuple[int, int] | tuple[None, None]: The extracted disk and partition numbers, or None if the string does not match the pattern.
    """
    # The pattern to match to extract the disk and partition numbers
    pattern: str = r"Disk #(\d+), Partition #(\d+)"
    # Search for the pattern in the input string
    match: re.Match[str] | None = re.search(pattern, input_str)

    if match:
        # Extract the disk and partition numbers from the match
        disk_number: str = match.group(1)
        partition_number: str = match.group(2)
        # Return the extracted numbers as integers
        return int(disk_number), int(partition_number)
    else:
        # Return None if the string does not match the pattern
        return None, None


class Disk(object):
    """
    Represents a disk.
    """

    def __init__(self, device_id: int, model: str, media_type: str, bus_type: str, size: int):
        """
        Initializes a new instance of the Disk class.

        Args:
            device_id (int): The device ID of the disk.
            model (str): The model of the disk.
            media_type (str): The media type of the disk (Unspecified, HDD, SSD, SCM).
            bus_type (str): The bus type of the disk (e.g. "SATA", "USB", etc.).
            size (int): The size of the disk in bytes.
        """
        self.DeviceID: int = device_id
        """The device ID of the disk."""
        self.Model: str = model
        """The model of the disk."""
        self.MediaType: str = media_type
        """The media type of the disk (Unspecified, HDD, SSD, SCM)."""
        self.BusType: str = bus_type
        """The bus type of the disk (e.g. "SATA", "USB", etc.)."""
        self.Size: int = size
        """The size of the disk in bytes."""


class Partition(object):
    """
    Represents a partition on a disk.

    Attributes:
        DeviceID (int): The device ID of the partition.
        Model (str): The model of the partition.
        Partition (str): The partition number.
        Volume (str): The volume letter of the partition.
        FreeSize (int): The free space on the partition in megabytes.
        PageFileSizes (List[Tuple[str, int, int]]): The initial and maximum pagefile size on the partitions in megabytes.
    """

    def __init__(self, device_id: int, model: str, partition: str, volume: str):
        """
        Initializes a new instance of the Partition class.

        Args:
            device_id (int): The device ID of the partition.
            model (str): The model of the partition.
            partition (str): The partition number.
            volume (str): The volume letter of the partition.
        """
        self.DeviceID: int = device_id
        """The device ID of the partition."""
        self.Model: str = model
        """The model of the partition."""
        self.Partition: str = partition
        """The partition number."""
        self.Volume: str = volume
        """The volume letter of the partition."""
        self.FreeSize: int = get_free_space_mb(self.Volume)
        """The free space on the partition in megabytes."""
        self.PageFileSize: List[Tuple[str, int, int]] = get_pagefiles_size()
        """The initial and maximum pagefile size on the partition in megabytes."""


def get_disks_data() -> Dict[int, Disk]:
    ps_command = "Get-PhysicalDisk | Select-Object DeviceId, Model, MediaType, BusType, Size | ConvertTo-Json"

    output: str = run_powershell_command(ps_command)
    disk_data: Any = json.loads(output)

    disks: Dict[int, Disk] = {}

    for disk in disk_data:
        disks[int(disk["DeviceId"])] = Disk(int(disk["DeviceId"]), disk["Model"], disk["MediaType"], disk["BusType"], int(disk["Size"]))

    return disks


def get_partitions_data() -> Dict[str, Partition]:
    ps_command = """
    Get-WmiObject -Query "SELECT * FROM Win32_DiskDrive" | ForEach-Object {
        $disk = $_
        $partitions = Get-WmiObject -Query "ASSOCIATORS OF {Win32_DiskDrive.DeviceID='$($disk.DeviceID)'} WHERE AssocClass=Win32_DiskDriveToDiskPartition"
        foreach ($partition in $partitions) {
            $volumes = Get-WmiObject -Query "ASSOCIATORS OF {Win32_DiskPartition.DeviceID='$($partition.DeviceID)'} WHERE AssocClass=Win32_LogicalDiskToPartition"
            foreach ($volume in $volumes) {
                [PSCustomObject]@{
                    DiskModel = $disk.Model
                    Partition = $partition.DeviceID
                    Volume = $volume.DeviceID
                }
            }
        }
    } | ConvertTo-Json
    """

    output: str = run_powershell_command(ps_command)
    disk_data = json.loads(output)

    partitions: Dict[str, Partition] = {}

    for entry in disk_data:
        disk, partition = extract_disk_and_partition(entry["Partition"])
        partitions[entry["Volume"]] = Partition(disk, entry["DiskModel"], partition, entry["Volume"])

    return partitions
