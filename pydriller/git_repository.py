# Copyright 2018 Davide Spadini
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
This module includes 1 class, GitRepository, representing a repository in Git.
"""

import logging
import os
from pathlib import Path
from threading import Lock
from typing import List, Dict, Tuple, Set, Generator

from git import Git, Repo, GitCommandError, Commit as GitCommit

from pydriller.domain.commit import Commit, ModificationType, Modification
from pydriller.utils.hyperblame import GitHyperBlame

logger = logging.getLogger(__name__)


class GitRepository:
    """
    Class representing a repository in Git. It contains most of the logic of
    PyDriller: obtaining the list of commits, checkout, reset, etc.
    """

    # pylint: disable=too-many-instance-attributes
    def __init__(self, path: str, **kwargs):
        """
        Init the Git RepositoryMining.

        :param str path: path to the repository
        """
        self.path = Path(path)
        self.hyperblame = GitHyperBlame(path)
        self.project_name = self.path.name
        self.lock = Lock()
        self._hyper_blame_available = None
        self._git = None
        self._repo = None
        self._commit_options = {
            "path": self.path,
            "main_branch": None
        }

        if 'histogram' in kwargs:
            self._commit_options['histogram'] = True

    @property
    def git(self):
        """
        GitPython object Git.

        :return: Git
        """
        if self._git is None:
            self._open_git()
        return self._git

    @property
    def repo(self):
        """
        GitPython object Repo.

        :return: Repo
        """
        if self._repo is None:
            self._open_repository()
        return self._repo

    def _open_git(self):
        self._git = Git(str(self.path))

    def _open_repository(self):
        self._repo = Repo(str(self.path))
        if self._commit_options["main_branch"] is None:
            self._discover_main_branch(self._repo)

    def _discover_main_branch(self, repo):
        try:
            self._commit_options["main_branch"] = repo.active_branch.name
        except TypeError:
            logger.info("HEAD is a detached symbolic reference, setting "
                        "main branch to empty string")
            self._commit_options["main_branch"] = ''

    def get_head(self) -> Commit:
        """
        Get the head commit.

        :return: Commit of the head commit
        """
        head_commit = self.repo.head.commit
        return Commit(head_commit, **self._commit_options)

    def get_list_commits(self, branch: str = None,
                         reverse_order: bool = True) \
            -> Generator[Commit, None, None]:
        """
        Return a generator of commits of all the commits in the repo.

        :return: Generator[Commit], the generator of all the commits in the
            repo
        """
        for commit in self.repo.iter_commits(branch, reverse=reverse_order):
            yield self.get_commit_from_gitpython(commit)

    def get_commit(self, commit_id: str) -> Commit:
        """
        Get the specified commit.

        :param str commit_id: hash of the commit to analyze
        :return: Commit
        """
        gp_commit = self.repo.commit(commit_id)
        return Commit(gp_commit, **self._commit_options)

    def get_commit_from_gitpython(self, commit: GitCommit) -> Commit:
        """
        Build a PyDriller commit object from a GitPython commit object.
        This is internal of PyDriller, I don't think users generally will need
        it.

        :param GitCommit commit: GitPython commit
        :return: Commit commit: PyDriller commit
        """
        return Commit(commit, **self._commit_options)

    def checkout(self, _hash: str) -> None:
        """
        Checkout the repo at the speficied commit.
        BE CAREFUL: this will change the state of the repo, hence it should
        *not* be used with more than 1 thread.

        :param _hash: commit hash to checkout
        """
        with self.lock:
            self._delete_tmp_branch()
            self.git.checkout('-f', _hash, b='_PD')

    def _delete_tmp_branch(self) -> None:
        try:
            # we are already in _PD, so checkout the master branch before
            # deleting it
            if self.repo.active_branch.name == '_PD':
                self.git.checkout('-f', self._commit_options["main_branch"])
            self.repo.delete_head('_PD', force=True)
        except GitCommandError:
            logger.debug("Branch _PD not found")

    def files(self) -> List[str]:
        """
        Obtain the list of the files (excluding .git directory).

        :return: List[str], the list of the files
        """
        _all = []
        for path, _, files in os.walk(str(self.path)):
            if '.git' in path:
                continue
            for name in files:
                _all.append(os.path.join(path, name))
        return _all

    def reset(self) -> None:
        """
        Reset the state of the repo, checking out the main branch and
        discarding
        local changes (-f option).

        """
        with self.lock:
            self.git.checkout('-f', self._commit_options["main_branch"])
            self._delete_tmp_branch()

    def total_commits(self) -> int:
        """
        Calculate total number of commits.

        :return: the total number of commits
        """
        return len(list(self.get_list_commits()))

    def get_commit_from_tag(self, tag: str) -> Commit:
        """
        Obtain the tagged commit.

        :param str tag: the tag
        :return: Commit commit: the commit the tag referred to
        """
        try:
            selected_tag = self.repo.tags[tag]
            return self.get_commit(selected_tag.commit.hexsha)
        except (IndexError, AttributeError):
            logger.debug('Tag %s not found', tag)
            raise

    def get_tagged_commits(self):
        """
        Obtain the hash of all the tagged commits.

        :return: list of tagged commits (can be empty if there are no tags)
        """
        tags = []
        for tag in self.repo.tags:
            if tag.commit:
                tags.append(tag.commit.hexsha)
        return tags

    def parse_diff(self, diff: str) -> Dict[str, List[Tuple[int, str]]]:
        """
        Given a diff, returns a dictionary with the added and deleted lines.
        The dictionary has 2 keys: "added" and "deleted", each containing the
        corresponding added or deleted lines. For both keys, the value is a
        list of Tuple (int, str), corresponding to (number of line in the file,
        actual line).


        :param str diff: diff of the commit
        :return: Dictionary
        """
        lines = diff.split('\n')
        modified_lines = {'added': [], 'deleted': []}

        count_deletions = 0
        count_additions = 0

        for line in lines:
            line = line.rstrip()
            count_deletions += 1
            count_additions += 1

            if line.startswith('@@'):
                count_deletions, count_additions = self._get_line_numbers(line)

            if line.startswith('-'):
                modified_lines['deleted'].append((count_deletions, line[1:]))
                count_additions -= 1

            if line.startswith('+'):
                modified_lines['added'].append((count_additions, line[1:]))
                count_deletions -= 1

            if line == r'\ No newline at end of file':
                count_deletions -= 1
                count_additions -= 1

        return modified_lines

    @staticmethod
    def _get_line_numbers(line):
        token = line.split(" ")
        numbers_old_file = token[1]
        numbers_new_file = token[2]
        delete_line_number = int(numbers_old_file.split(",")[0]
                                 .replace("-", "")) - 1
        additions_line_number = int(numbers_new_file.split(",")[0]) - 1
        return delete_line_number, additions_line_number

    def get_commits_last_modified_lines(self, commit: Commit,
                                        modification: Modification = None,
                                        hyper_blame: bool = False,
                                        hashes_to_ignore_path: str = None) \
            -> Dict[str, Set[str]]:
        """
        Given the Commit object, returns the set of commits that last
        "touched" the lines that are modified in the files included in the
        commit. It applies SZZ.

        IMPORTANT: for better results, we suggest to install Google
        depot_tools first (see
        https://dev.chromium.org/developers/how-tos/install-depot-tools).
        This allows PyDriller to use "git hyper-blame" instead of the normal
        blame. If depot_tools are not installed, PyDriller will automatically
        switch to the normal blame.

        The algorithm works as follow: (for every file in the commit)

        1- obtain the diff

        2- obtain the list of deleted lines

        3- blame the file and obtain the commits were those lines were added

        Can also be passed as parameter a single Modification, in this case
        only this file will be analyzed.

        :param Commit commit: the commit to analyze
        :param Modification modification: single modification to analyze
        :param bool hyper_blame: whether to use git hyper blame or the
            normal blame (by default it uses the normal blame).
        :param str hashes_to_ignore_path: path to a file containing hashes of
               commits to ignore. (only works with git hyper blame)
        :return: the set containing all the bug inducing commits
        """
        hashes_to_ignore = []
        if hashes_to_ignore_path is not None:
            assert os.path.exists(hashes_to_ignore_path), \
                "The file with the commit hashes to ignore does not exist"
            hashes_to_ignore = open(hashes_to_ignore_path).readlines()

        if modification is not None:
            modifications = [modification]
        else:
            modifications = commit.modifications

        return self._calculate_last_commits(commit, modifications,
                                            hyper_blame,
                                            hashes_to_ignore)

    def _calculate_last_commits(self, commit: Commit,
                                modifications: List[Modification],
                                hyper_blame: bool = False,
                                hashes_to_ignore: List[str] = None) \
            -> Dict[str, Set[str]]:

        commits = {}

        for mod in modifications:
            path = mod.new_path
            if mod.change_type == ModificationType.RENAME or \
                    mod.change_type == ModificationType.DELETE:
                path = mod.old_path
            deleted_lines = self.parse_diff(mod.diff)['deleted']
            try:
                blame = self._get_blame(commit.hash, path, hyper_blame,
                                        hashes_to_ignore)
                for num_line, line in deleted_lines:
                    if not self._useless_line(line.strip()):
                        buggy_commit = blame[num_line - 1].split(' ')[
                            0].replace('^', '')

                        if mod.change_type == ModificationType.RENAME:
                            path = mod.new_path

                        commits.setdefault(path, set()).add(
                            self.get_commit(buggy_commit).hash)
            except GitCommandError:
                logger.debug(
                    "Could not found file %s in commit %s. Probably a double "
                    "rename!", mod.filename, commit.hash)

        return commits

    def _get_blame(self, commit_hash: str, path: str,
                   hyper_blame: bool = False,
                   hashes_to_ignore: List[str] = None):
        """
        If "git hyper-blame" is available, use it. Otherwise use normal blame.
        """
        if not hyper_blame or hashes_to_ignore is None:
            return self.git.blame('-w', commit_hash + '^',
                                  '--', path).split('\n')
        return self.hyperblame.hyper_blame(hashes_to_ignore, path,
                                           commit_hash + '^')

    @staticmethod
    def _useless_line(line: str):
        # this covers comments in Java and Python, as well as empty lines.
        # More have to be added!
        return not line or \
               line.startswith('//') or \
               line.startswith('#') or \
               line.startswith("/*") or \
               line.startswith("'''") or \
               line.startswith('"""') or \
               line.startswith("*")

    def get_commits_modified_file(self, filepath: str) -> List[str]:
        """
        Given a filepath, returns all the commits that modified this file
        (following renames).

        :param str filepath: path to the file
        :return: the list of commits' hash
        """
        path = str(Path(filepath))

        commits = []
        try:
            commits = self.git.log("--follow", "--format=%H", path).split('\n')
        except GitCommandError:
            logger.debug("Could not find information of file %s", path)

        return commits
