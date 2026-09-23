"""Atomic, create-only publication without a two-hard-link crash window."""
from __future__ import annotations

import ctypes
import errno
import os
import stat


class PublishedFileDurabilityError(OSError):
    """The exclusive rename succeeded, but directory durability failed.

    The caller owns the destination and must preserve or roll back that exact
    file; treating this as a pre-publication failure would orphan it.
    """


def publish_file_no_replace(source: str, destination: str) -> None:
    """Move a complete regular file atomically; never clobber a destination.

    Source and destination must share a filesystem. Unsupported exclusive-rename
    primitives fail closed rather than degrading to an overwriting rename or a
    link/unlink sequence that leaves two names after a crash.
    """
    source, destination = os.path.abspath(source), os.path.abspath(destination)
    info = os.lstat(source)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('Publication source must be a single-link regular file')
    source_fd = os.open(source, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        opened = os.fstat(source_fd)
        if (opened.st_dev, opened.st_ino, opened.st_nlink) != (info.st_dev, info.st_ino, 1):
            raise ValueError('Publication source changed')
        os.fsync(source_fd)
    finally:
        os.close(source_fd)
    if os.name == 'nt':
        # Windows os.rename refuses existing destinations, including links.
        os.rename(source, destination)
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if hasattr(libc, 'renameat2'):
        rename, flag = libc.renameat2, 1  # Linux RENAME_NOREPLACE
    elif hasattr(libc, 'renameatx_np'):
        rename, flag = libc.renameatx_np, 0x4  # Darwin RENAME_EXCL
    else:
        raise OSError(errno.ENOTSUP, 'Atomic exclusive rename is unavailable')
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    source_directory = os.open(os.path.dirname(source), flags)
    try:
        destination_directory = os.open(os.path.dirname(destination), flags)
        try:
            result = rename(source_directory, os.fsencode(os.path.basename(source)),
                            destination_directory, os.fsencode(os.path.basename(destination)), flag)
            if result:
                code = ctypes.get_errno()
                raise OSError(code, os.strerror(code))
            try:
                os.fsync(destination_directory)
                os.fsync(source_directory)
            except OSError as exc:
                raise PublishedFileDurabilityError(exc.errno, "Published file directory sync failed") from exc
        finally:
            os.close(destination_directory)
    finally:
        os.close(source_directory)
