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
This module includes 1 class, RepositoryMining, main class of PyDriller.
"""

import logging
import os
import tempfile
from datetime import datetime
from typing import List, Generator, Union

import pytz
from git import Repo

from pydriller.domain.commit import Commit
from pydriller.git_repository import GitRepository

logger = logging.getLogger(__name__)


class RepositoryMining:
    """
    This is the main class of PyDriller, responsible for running the study.
    """

    # pylint: disable=R0902,R0913,R0914
    def __init__(self, path_to_repo: Union[str, List[str]],
                 single: str = None,
                 since: datetime = None, to: datetime = None,
                 from_commit: str = None, to_commit: str = None,
                 from_tag: str = None, to_tag: str = None,
                 reversed_order: bool = False,
                 only_in_branch: str = None,
                 only_modifications_with_file_types: List[str] = None,
                 only_no_merge: bool = False,
                 only_authors: List[str] = None,
                 only_commits: List[str] = None,
                 only_releases: bool = False,
                 filepath: str = None,
                 histogram_diff: bool = False):
        """
        Init a repository mining. The only required parameter is
        "path_to_repo": to analyze a single repo, pass the absolute path to
        the repo; if you need to analyze more repos, pass a list of absolute
        paths.

        Furthermore, PyDriller supports local and remote repositories: if
        you pass a path to a repo, PyDriller will run the study on that
        repo; if you pass an URL, PyDriller will clone the repo in a
        temporary folder, run the study, and delete the temporary folder.

        :param Union[str,List[str]] path_to_repo: absolute path (or list of
            absolute paths) to the repository(ies) to analyze
        :param str single: hash of a single commit to analyze
        :param datetime since: starting date
        :param datetime to: ending date
        :param str from_commit: starting commit (only if `since` is None)
        :param str to_commit: ending commit (only if `to` is None)
        :param str from_tag: starting the analysis from specified tag (only
            if `since` and `from_commit` are None)
        :param str to_tag: ending the analysis from specified tag (only if
            `to` and `to_commit` are None)
        :param bool reversed_order: whether the commits should be analyzed
            in reversed order
        :param str only_in_branch: only commits in this branch will be analyzed
        :param List[str] only_modifications_with_file_types: only
            modifications with that file types will be analyzed
        :param bool only_no_merge: if True, merges will not be analyzed
        :param List[str] only_authors: only commits of these authors will be
            analyzed (the check is done on the username, NOT the email)
        :param List[str] only_commits: only these commits will be analyzed
        :param str filepath: only commits that modified this file will be
            analyzed
        """

        self._sanity_check_repos(path_to_repo)
        if isinstance(path_to_repo, str):
            self._path_to_repo = [path_to_repo]
        else:
            self._path_to_repo = path_to_repo

        self._from_commit = from_commit
        self._to_commit = to_commit
        self._from_tag = from_tag
        self._to_tag = to_tag
        self._single = single
        self._since = since
        self._to = to
        self._reversed_order = reversed_order
        self._only_in_branch = only_in_branch
        self._only_modifications_with_file_types = \
            only_modifications_with_file_types
        self._only_no_merge = only_no_merge
        self._only_authors = only_authors
        self._only_commits = only_commits
        self._only_releases = only_releases
        self._filepath = filepath
        self._filepath_commits = None
        self._tagged_commits = None
        self._histogram = histogram_diff

    @staticmethod
    def _sanity_check_repos(path_to_repo):
        if not isinstance(path_to_repo, str) and \
                not isinstance(path_to_repo, list):
            raise Exception("The path to the repo has to be of type "
                            "'string' or 'list of strings'!")

    def _sanity_check_filters(self, git_repo: GitRepository):
        if self._single is not None:
            if any([self._since,
                    self._to,
                    self._from_commit,
                    self._to_commit,
                    self._from_tag,
                    self._to_tag]):
                raise Exception('You can not specify a single commit with '
                                'other filters')

        self._check_starting_commit(git_repo)
        self._check_ending_commit(git_repo)

    def _check_ending_commit(self, git_repo):
        if not self.only_one_filter([self._to, self._to_commit,
                                     self._to_tag]):
            raise Exception('You can only specify one between since, '
                            'from_tag and from_commit')
        if self._to_commit is not None:
            self._to = git_repo.get_commit(self._to_commit).committer_date
        if self._to_tag is not None:
            self._to = git_repo.get_commit_from_tag(
                self._to_tag).committer_date

    def _check_starting_commit(self, git_repo):
        if not self.only_one_filter([self._since, self._from_tag,
                                     self._from_commit]):
            raise Exception('You can only specify one between since, '
                            'from_tag and from_commit')
        if self._from_commit is not None:
            self._since = git_repo.get_commit(self._from_commit).committer_date
        if self._from_tag is not None:
            self._since = git_repo.get_commit_from_tag(
                self._from_tag).committer_date

    @staticmethod
    def only_one_filter(arr):
        return len([x for x in arr if x is not None]) <= 1

    @staticmethod
    def _is_remote(repo: str) -> bool:
        return repo.startswith("git@") or repo.startswith("https://")

    def _clone_remote_repos(self, tmp_folder: str, repo: str) -> str:

        repo_folder = os.path.join(tmp_folder,
                                   self._get_repo_name_from_url(repo))
        logger.info("Cloning %s in temporary folder %s", repo, repo_folder)
        Repo.clone_from(url=repo, to_path=repo_folder)

        return repo_folder

    def traverse_commits(self) -> Generator[Commit, None, None]:
        """
        Analyze all the specified commits (all of them by default), returning
        a generator of commits.
        """
        for path_repo in self._path_to_repo:
            # if it is a remote repo, clone it first in a temporary folder!
            if self._is_remote(path_repo):
                tmp_folder = tempfile.TemporaryDirectory()
                path_repo = self._clone_remote_repos(tmp_folder.name,
                                                     path_repo)
            options = {}
            if self._histogram:
                options['histogram'] = True

            git_repo = GitRepository(path_repo, **options)

            self._sanity_check_filters(git_repo)
            self._check_timezones()

            logger.info('Analyzing git repository in %s', git_repo.path)

            if self._filepath is not None:
                self._filepath_commits = git_repo.get_commits_modified_file(
                    self._filepath)

            if self._only_releases:
                self._tagged_commits = git_repo.get_tagged_commits()

            for commit in git_repo.get_list_commits(self._only_in_branch,
                                                    not self._reversed_order):
                logger.info('Commit #%s in %s from %s', commit.hash,
                            commit.committer_date,
                            commit.author.name)

                if self._is_commit_filtered(commit):
                    logger.info('Commit #%s filtered', commit.hash)
                    continue

                yield commit

    def _is_commit_filtered(self, commit: Commit):  # pylint: disable=R0911
        if self._single is not None and commit.hash != self._single:
            logger.debug(
                'Commit filtered because is not the defined in single')
            return True
        if (self._since is not None and commit.committer_date < self._since) \
                or (self._to is not None and commit.committer_date > self._to):
            return True
        if self._only_modifications_with_file_types is not None:
            if not self._has_modification_with_file_type(commit):
                logger.debug('Commit filtered for modification types')
                return True
        if self._only_no_merge is True and commit.merge is True:
            logger.debug('Commit filtered for no merge')
            return True
        if self._only_authors is not None and commit.author.name not in \
                self._only_authors:
            logger.debug("Commit filtered for author")
            return True
        if self._only_commits is not None and commit.hash not in \
                self._only_commits:
            logger.debug("Commit filtered because it is not one of the "
                         "specified commits")
            return True
        if self._filepath_commits is not None and commit.hash not in \
                self._filepath_commits:
            logger.debug("Commit filtered because it did not modify the "
                         "specified file")
            return True
        if self._tagged_commits is not None and commit.hash not in \
                self._tagged_commits:
            logger.debug("Commit filtered because it is not tagged")
            return True

        return False

    def _has_modification_with_file_type(self, commit):
        for mod in commit.modifications:
            if mod.filename.endswith(
                    tuple(self._only_modifications_with_file_types)):
                return True
        return False

    def _check_timezones(self):
        if self._since is not None:
            self._since = self._replace_timezone(self._since)
        if self._to is not None:
            self._to = self._replace_timezone(self._to)

    @staticmethod
    def _replace_timezone(dt: datetime):
        if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
            dt = dt.replace(tzinfo=pytz.utc)
        return dt

    @staticmethod
    def _get_repo_name_from_url(url: str) -> str:
        last_slash_index = url.rfind("/")
        last_suffix_index = url.rfind(".git")
        if last_suffix_index < 0:
            last_suffix_index = len(url)

        if last_slash_index < 0 or last_suffix_index <= last_slash_index:
            raise Exception("Badly formatted url {}".format(url))

        return url[last_slash_index + 1:last_suffix_index]
