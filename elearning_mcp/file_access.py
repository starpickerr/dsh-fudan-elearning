"""Confine file transfers to directories opened without following links."""
import os
from pathlib import Path, PureWindowsPath
import re
import stat


_DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0)
_READ_FLAGS = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
_CREATE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
_WINDOWS = os.name == 'nt'


def valid_name(name):
    if not isinstance(name, str) or name in ('', '.', '..') or '/' in name or '\\' in name:
        return False
    if any(ord(char) < 32 for char in name):
        return False
    return not _WINDOWS or (
        not re.search(r'[<>:"|?*]', name) and name == name.rstrip(' .')
        and not PureWindowsPath(name).is_reserved()
    )


def _windows_handle(path, *, directory):
    import ntsecuritycon
    import win32con
    import win32file

    access = ntsecuritycon.FILE_READ_ATTRIBUTES if directory else win32con.GENERIC_READ
    share = win32con.FILE_SHARE_READ
    flags = win32file.FILE_FLAG_OPEN_REPARSE_POINT | win32con.FILE_FLAG_BACKUP_SEMANTICS
    try:
        handle = win32file.CreateFile(str(path), access, share, None, win32con.OPEN_EXISTING, flags, None)
    except win32file.error as error:
        if error.winerror in (2, 3):
            raise FileNotFoundError(str(path)) from error
        raise OSError(str(path)) from error
    try:
        attrs = win32file.GetFileInformationByHandle(handle)[0]
        if attrs & win32con.FILE_ATTRIBUTE_REPARSE_POINT or bool(attrs & win32con.FILE_ATTRIBUTE_DIRECTORY) != directory:
            raise OSError('Expected a directory or regular file without reparse points')
        return handle
    except BaseException:
        handle.Close()
        raise


class _WindowsDirectory:
    def __init__(self, path):
        self.path = path
        self.handles = []
        try:
            # Denying delete sharing pins every ancestor against renames and junction swaps.
            current = Path(path.anchor)
            self.handles.append(_windows_handle(current, directory=True))
            for name in path.parts[1:]:
                if not valid_name(name):
                    raise OSError('Invalid directory component')
                current /= name
                self.handles.append(_windows_handle(current, directory=True))
        except BaseException:
            self.close()
            raise

    def file(self, name):
        if not valid_name(name):
            raise OSError('Invalid file name')
        return self.path / name

    def close(self):
        for handle in reversed(self.handles):
            handle.Close()


def open_directory(path):
    if _WINDOWS:
        return _WindowsDirectory(path)
    descriptor = os.open(path.anchor, _DIRECTORY_FLAGS)
    try:
        for name in path.parts[1:]:
            child = os.open(name, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def same_directory(first, second):
    if _WINDOWS:
        return first.path == second.path
    actual, expected = os.fstat(first), os.fstat(second)
    return (actual.st_dev, actual.st_ino) == (expected.st_dev, expected.st_ino)


def close(directory):
    if _WINDOWS:
        directory.close()
    else:
        os.close(directory)


def open_child(directory, name):
    try:
        if _WINDOWS:
            directory.file(name).mkdir()
        else:
            os.mkdir(name, 0o700, dir_fd=directory)
    except FileExistsError:
        pass
    if _WINDOWS:
        return open_directory(directory.file(name))
    return os.open(name, _DIRECTORY_FLAGS, dir_fd=directory)


def inspect(directory, name):
    if _WINDOWS:
        info = directory.file(name).lstat()
        if info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise OSError('File is a reparse point')
        return info
    return os.stat(name, dir_fd=directory, follow_symlinks=False)


def open_read(directory, name):
    if _WINDOWS:
        import msvcrt

        handle = _windows_handle(directory.file(name), directory=False)
        try:
            descriptor = msvcrt.open_osfhandle(int(handle), os.O_RDONLY | os.O_BINARY)
            handle.Detach()
            return descriptor
        finally:
            handle.Close()
    return os.open(name, _READ_FLAGS, dir_fd=directory)


def create(directory, name):
    if _WINDOWS:
        return os.open(directory.file(name), _CREATE_FLAGS, 0o600)
    return os.open(name, _CREATE_FLAGS, 0o600, dir_fd=directory)


def link(source_directory, source, target_directory, target):
    if _WINDOWS:
        old = source_directory.file(source)
        handle = _windows_handle(old, directory=False)
        try:
            # CreateHardLink refuses existing destinations.
            os.link(old, target_directory.file(target))
        finally:
            handle.Close()
    else:
        os.link(source, target, src_dir_fd=source_directory, dst_dir_fd=target_directory, follow_symlinks=False)


def replace(directory, source, target):
    if _WINDOWS:
        inspect(directory, source)
        os.replace(directory.file(source), directory.file(target))
    else:
        os.replace(source, target, src_dir_fd=directory, dst_dir_fd=directory)


def unlink(directory, name):
    if _WINDOWS:
        directory.file(name).unlink()
    else:
        os.unlink(name, dir_fd=directory)
