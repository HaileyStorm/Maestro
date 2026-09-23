from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from services.atomic_file_publish import publish_file_no_replace, PublishedFileDurabilityError


class AtomicPublicationTests(unittest.TestCase):
    def test_moves_complete_file_without_overwrite_or_extra_link(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); staging = root/'stage'; staging.mkdir()
            source = staging/'video'; source.write_bytes(b'complete')
            target = root/'video'
            publish_file_no_replace(str(source),str(target))
            self.assertFalse(source.exists())
            self.assertEqual(target.read_bytes(),b'complete')
            self.assertEqual(target.stat().st_nlink,1)
            source.write_bytes(b'other')
            with self.assertRaises(FileExistsError): publish_file_no_replace(str(source),str(target))
            self.assertEqual(source.read_bytes(),b'other')
            self.assertEqual(target.read_bytes(),b'complete')

    @unittest.skipIf(os.name == 'nt', 'Directory fsync is POSIX-only')
    def test_post_rename_sync_failure_reports_destination_ownership(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)/'source'; source.write_bytes(b'complete')
            target = Path(folder)/'target'
            with patch('services.atomic_file_publish.os.fsync', side_effect=[None, OSError(5, 'io')]):
                with self.assertRaises(PublishedFileDurabilityError):
                    publish_file_no_replace(str(source), str(target))
            self.assertFalse(source.exists())
            self.assertEqual(target.read_bytes(), b'complete')

    def test_existing_symlink_or_directory_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source=root/'source'; source.write_bytes(b'complete')
            target=root/'target'; target.mkdir()
            with self.assertRaises(OSError): publish_file_no_replace(str(source),str(target))
            self.assertTrue(target.is_dir()); target.rmdir()
            try: target.symlink_to(root/'missing')
            except OSError: self.skipTest('symlinks unavailable')
            with self.assertRaises(FileExistsError): publish_file_no_replace(str(source),str(target))
            self.assertTrue(target.is_symlink()); self.assertTrue(source.exists())

    def test_source_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); real=root/'real'; real.write_bytes(b'original')
            alias=root/'alias'
            try: alias.symlink_to(real)
            except OSError: self.skipTest('symlinks unavailable')
            with self.assertRaises(ValueError): publish_file_no_replace(str(alias),str(root/'out'))
            self.assertEqual(real.read_bytes(),b'original')

if __name__ == '__main__': unittest.main()
