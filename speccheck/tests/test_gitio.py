"""Real Git snapshot boundaries: never silently lose selected source."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from speccheck.gitio import GitError, load_files

HIDDEN = 'kypp/nested/hidden.py'
BROKEN = 'doc://kypp/not-a-real-doc@0001#missing\n'
PROJECT_ROOT = Path(__file__).resolve().parents[2]


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

    def _tracked_tree(self):
        self.write('kypp/keep.py', b'keep\n')
        self.write(HIDDEN, BROKEN.encode())
        self.write('docs/note.md', b'note\n')
        self.write('outside.txt', b'out\n')
        self.commit()
        return self.git('rev-parse', 'HEAD').decode().strip()

    def _index_tag(self, path):
        listing = self.git('ls-files', '-v', '-z')
        for record in listing.split(b'\0'):
            if record[2:] == path.encode():
                return record[:2]
        self.fail(f'{path} missing from git ls-files -v: {listing!r}')

    def _remove_worktree_file(self, path):
        dest = self.repo / path
        dest.unlink()
        parent = dest.parent
        if parent != self.repo and not any(parent.iterdir()):
            parent.rmdir()

    def _expect_unmaterialized_load_failure(self, baseline, path):
        """A selected index path absent from the worktree must fail closed."""
        tag = self._index_tag(path)
        self.assertIn(tag, (b'S ', b's '), tag)
        self.assertFalse((self.repo / path).exists())
        self.assertIn(path.encode() + b'\0', self.git('ls-files', '-c', '-z') + b'\0')
        problems = []
        try:
            files, paths = load_files(self.repo, None)
        except GitError as exc:
            if path not in str(exc):
                problems.append(f'GitError did not name {path!r}: {exc}')
        else:
            problems.append(
                f'load_files treated {path!r} as absent rather than a load failure: '
                f'files={sorted(files)} paths={paths}'
            )
        out = Path(self.tmp.name) / 'report.md'
        report_json = Path(self.tmp.name) / 'report.json'
        cache = Path(self.tmp.name) / 'cache'
        env = dict(os.environ, PYTHONPATH=str(PROJECT_ROOT))
        bootstrap = 'from speccheck.cli import main; import sys; sys.exit(main(sys.argv[1:]))'
        result = subprocess.run(
            [sys.executable, '-c', bootstrap, 'check', '--repo', str(self.repo),
             '--baseline', baseline, '--out', str(out), '--json', str(report_json),
             '--cache', str(cache)],
            capture_output=True, text=True, env=env, timeout=60,
        )
        if result.returncode == 0:
            status = 'unparsed'
            if report_json.is_file():
                report = json.loads(report_json.read_text())
                refs = [v for v in report['verdicts'] if v['rule_id'] == 'doc-refs']
                status = ','.join(v['status'] for v in refs) or 'no-doc-refs'
            problems.append(
                'check exited 0 and produced a report over the incomplete worktree: '
                f'doc-refs={status} stdout={result.stdout[:500]!r}'
            )
        else:
            if 'speccheck:' not in result.stderr or path not in result.stderr:
                problems.append(f'nonzero exit did not report the path: {result.stderr!r}')
            if result.stdout.strip():
                problems.append(f'load failure printed a report: {result.stdout[:500]!r}')
            if out.exists() or report_json.exists():
                problems.append('load failure wrote report output')
            if cache.exists() and any(cache.rglob('*')):
                problems.append('load failure wrote a cache')
        self.assertFalse(problems, '\n'.join(problems))

    def test_skip_worktree_selected_file_is_load_failure(self):
        baseline = self._tracked_tree()
        self.git('update-index', '--skip-worktree', '--', HIDDEN)
        self._remove_worktree_file(HIDDEN)
        self._expect_unmaterialized_load_failure(baseline, HIDDEN)

    def test_skip_worktree_assume_unchanged_selected_file_is_load_failure(self):
        baseline = self._tracked_tree()
        self.git('update-index', '--skip-worktree', '--', HIDDEN)
        self.git('update-index', '--assume-unchanged', '--', HIDDEN)
        self._remove_worktree_file(HIDDEN)
        self.assertEqual(self._index_tag(HIDDEN), b's ')
        self._expect_unmaterialized_load_failure(baseline, HIDDEN)

    def test_sparse_checkout_selected_file_is_load_failure(self):
        baseline = self._tracked_tree()
        self.git('sparse-checkout', 'set', '--no-cone', '--',
                 '/kypp/keep.py', '/docs/note.md', '/outside.txt')
        self._expect_unmaterialized_load_failure(baseline, HIDDEN)

    def test_materialized_skip_worktree_file_is_read(self):
        self.write('kypp/hidden.py', b'present\n')
        self.commit()
        self.git('update-index', '--skip-worktree', '--', 'kypp/hidden.py')
        files, paths = load_files(self.repo, None)
        self.assertEqual(files, {'kypp/hidden.py': 'present\n'})
        self.assertEqual(paths, ['kypp/hidden.py'])

    def test_unselected_sparse_omission_is_not_a_load_failure(self):
        self.write('kypp/keep.py', b'keep\n')
        self.write('outside.txt', b'out\n')
        self.commit()
        self.git('sparse-checkout', 'set', '--no-cone', '--', '/kypp/keep.py')
        self.assertEqual(self._index_tag('outside.txt'), b'S ')
        self.assertFalse((self.repo / 'outside.txt').exists())
        self.assertEqual(load_files(self.repo, None), ({'kypp/keep.py': 'keep\n'}, ['kypp/keep.py']))

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

    def test_opened_file_type_is_checked_after_replacement(self):
        dest = self.write('kypp/file.py')
        self.commit()
        original = os.stat
        def replace_after_stat(*args, **kwargs):
            info = original(*args, **kwargs)
            if args[0] == 'file.py' and kwargs.get('dir_fd') is not None:
                dest.unlink()
                os.mkfifo(dest)
            return info
        with patch('os.stat', side_effect=replace_after_stat):
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

    def test_unmerged_index_has_one_worktree_path(self):
        path = 'kypp/conflicted.py'
        self.write(path)
        self.commit()
        blob = self.git('rev-parse', f'HEAD:{path}').decode().strip()
        records = f"0 {'0' * 40}\t{path}\n"
        records += ''.join(f'100644 {blob} {stage}\t{path}\n' for stage in (1, 2, 3))
        subprocess.run(['git', '-C', str(self.repo), 'update-index', '--index-info'],
                       input=records.encode(), check=True, capture_output=True)
        self.assertEqual(self.git('ls-files', '-c', '-z').decode().split('\0').count(path), 3)
        self.assertEqual(load_files(self.repo, None), ({path: 'source\n'}, [path]))

    def test_source_descriptors_close_on_success_and_decode_failure(self):
        for content in (b'source\n', b'\xff'):
            with self.subTest(content=content):
                self.write('kypp/file.py', content)
                opened = []
                original = os.open
                def record_open(*args, **kwargs):
                    fd = original(*args, **kwargs)
                    opened.append(fd)
                    return fd
                with patch('speccheck.gitio.list_paths', return_value=['kypp/file.py']), \
                        patch('os.open', side_effect=record_open):
                    if content == b'\xff':
                        with self.assertRaises(GitError):
                            load_files(self.repo, None)
                    else:
                        load_files(self.repo, None)
                self.assertTrue(opened)
                for fd in opened:
                    with self.assertRaises(OSError):
                        os.fstat(fd)

    def test_unselected_binary_and_symlink_are_not_read(self):
        self.write('outside.bin', b'\xff')
        (self.repo / 'link').symlink_to('/not/a/real/file')
        self.commit()
        for rev in (None, 'HEAD'):
            with self.subTest(rev=rev):
                files, paths = load_files(self.repo, rev)
                self.assertEqual(files, {})
                self.assertEqual(set(paths), {'outside.bin', 'link'})

    def test_unselected_symlinked_parent_preserves_existence_inventory(self):
        dest = self.write('assets/image.bin', b'\xff')
        self.commit()
        dest.unlink()
        dest.parent.rmdir()
        external = Path(self.tmp.name) / 'external-assets'
        external.mkdir()
        image = external / 'image.bin'
        image.write_bytes(b'\xff')
        dest.parent.symlink_to(external, target_is_directory=True)
        self.assertEqual(load_files(self.repo, None), ({}, ['assets', 'assets/image.bin']))
        image.unlink()
        self.assertEqual(load_files(self.repo, None), ({}, ['assets']))

    def test_unselected_directory_replaced_by_file_removes_descendants(self):
        dest = self.write('assets/image.bin', b'\xff')
        self.commit()
        dest.unlink()
        dest.parent.rmdir()
        dest.parent.write_bytes(b'\xff')
        self.assertEqual(load_files(self.repo, None), ({}, ['assets']))

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
