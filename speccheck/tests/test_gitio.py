"""Real Git snapshot boundaries: never silently lose selected source."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from speccheck.gitio import GitError, load_files


class GitSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / 'repo'
        self.repo.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.email', 'test@example.com')
        self.git('config', 'user.name', 'Test')

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], check=True,
                              capture_output=True).stdout

    def write(self, path, content=b'source\n'):
        dest = self.repo / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        return dest

    def commit(self):
        self.git('add', '.')
        self.git('commit', '-qm', 'fixture')

    def test_literal_paths(self):
        paths = ['kypp/space name.py', 'kypp/tab\tname.py',
                 'kypp/new\nline.py', 'docs/café.md']
        for path in paths:
            self.write(path)
        self.commit()
        for rev in (None, 'HEAD'):
            with self.subTest(rev=rev):
                files, listed = load_files(self.repo, rev)
                self.assertEqual(set(listed), set(paths))
                self.assertEqual(files, dict.fromkeys(paths, 'source\n'))

    def test_invalid_utf8_is_failure(self):
        self.write('kypp/bad.py', b'\xff')
        self.commit()
        for rev in (None, 'HEAD'):
            with self.subTest(rev=rev), self.assertRaises(GitError):
                load_files(self.repo, rev)

    def test_deletion_removed_from_entire_snapshot(self):
        dest = self.write('kypp/deleted.py')
        other = self.write('outside.txt')
        self.commit()
        dest.unlink()
        other.unlink()
        self.assertEqual(load_files(self.repo, None), ({}, []))

    def test_symlink_rejected_in_both_modes(self):
        external = Path(self.tmp.name) / 'secret'
        external.write_bytes(b'external secret')
        (self.repo / 'kypp').mkdir()
        (self.repo / 'kypp/link.py').symlink_to(external)
        self.commit()
        for rev in (None, 'HEAD'):
            with self.subTest(rev=rev), patch('os.read', wraps=os.read) as read:
                with self.assertRaises(GitError):
                    load_files(self.repo, rev)
                # subprocess also uses os.read; source reads use large chunks.
                self.assertFalse(any(call.args[1] == 65536 for call in read.call_args_list))

    def test_symlink_parent_rejected(self):
        self.write('kypp/file.py')
        self.commit()
        (self.repo / 'kypp/file.py').unlink()
        (self.repo / 'kypp').rmdir()
        external = Path(self.tmp.name) / 'external'
        external.mkdir()
        (external / 'file.py').write_bytes(b'external')
        (self.repo / 'kypp').symlink_to(external, target_is_directory=True)
        with self.assertRaises(GitError):
            load_files(self.repo, None)

    def test_nonregular_selected_entry_is_failure(self):
        dest = self.write('kypp/file.py')
        self.commit()
        dest.unlink()
        os.mkfifo(dest)
        with self.assertRaises(GitError):
            load_files(self.repo, None)

    def test_read_error_is_failure(self):
        self.write('kypp/file.py')
        self.commit()
        with patch('speccheck.gitio.list_paths', return_value=['kypp/file.py']), \
                patch('os.read', side_effect=PermissionError('denied')):
            with self.assertRaises(GitError):
                load_files(self.repo, None)

    def test_git_blob_failure_is_explicit(self):
        self.write('kypp/file.py')
        self.commit()
        original = subprocess.run
        def fail_blob(args, **kwargs):
            if 'show' in args:
                return subprocess.CompletedProcess(args, 1, b'', b'cannot read blob')
            return original(args, **kwargs)
        with patch('subprocess.run', side_effect=fail_blob):
            with self.assertRaises(GitError):
                load_files(self.repo, 'HEAD')

    def test_stat_error_is_not_deletion(self):
        self.write('kypp/file.py')
        self.commit()
        with patch('os.stat', side_effect=PermissionError('denied')):
            with self.assertRaises(GitError):
                load_files(self.repo, None)

    def test_read_enoent_is_not_deletion(self):
        self.write('kypp/file.py')
        self.commit()
        with patch('speccheck.gitio.list_paths', return_value=['kypp/file.py']), \
                patch('os.read', side_effect=FileNotFoundError('read failed')):
            with self.assertRaises(GitError):
                load_files(self.repo, None)

    def test_staged_deletion_recreated_has_one_literal_path(self):
        self.write('kypp/recreated.py')
        self.commit()
        self.git('rm', 'kypp/recreated.py')
        self.write('kypp/recreated.py', b'recreated')
        self.assertEqual(load_files(self.repo, None),
                         ({'kypp/recreated.py': 'recreated'}, ['kypp/recreated.py']))

    def test_unselected_binary_and_symlink_are_not_read(self):
        self.write('outside.bin', b'\xff')
        (self.repo / 'link').symlink_to('/not/a/real/file')
        self.commit()
        for rev in (None, 'HEAD'):
            with self.subTest(rev=rev):
                files, paths = load_files(self.repo, rev)
                self.assertEqual(files, {})
                self.assertEqual(set(paths), {'outside.bin', 'link'})

    def test_committed_gitlink_is_not_source(self):
        self.write('outside.txt')
        self.commit()
        sha = self.git('rev-parse', 'HEAD').decode().strip()
        self.git('update-index', '--add', '--cacheinfo', f'160000,{sha},kypp/submodule')
        self.git('commit', '-qm', 'gitlink')
        with self.assertRaises(GitError):
            load_files(self.repo, 'HEAD')

    def test_git_execution_error_is_explicit(self):
        with patch('subprocess.run', side_effect=OSError('cannot launch Git')):
            for rev in (None, 'HEAD'):
                with self.subTest(rev=rev), self.assertRaises(GitError):
                    load_files(self.repo, rev)
