# -*- coding: utf-8 -*-
# vim:expandtab:autoindent:tabstop=4:shiftwidth=4:filetype=python:textwidth=0:
# License: GPL2 or later see COPYING
# Copyright (C) 2023 Stephen Gallagher <sgallagh@redhat.com>
# Copyright (C) 2023 Nils Philippsen <nils@redhat.com>
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Union

from rpmautospec_core import specfile_uses_rpmautospec

from mockbuild.exception import ConfigError
from mockbuild.trace_decorator import getLog, traceLog

requires_api_version = "1.1"


@traceLog()
def init(plugins, conf, buildroot):
    RpmautospecPlugin(plugins, conf, buildroot)


class RpmautospecPlugin:
    """Fill in release and changelog from git history using rpmautospec"""

    @traceLog()
    def __init__(self, plugins, conf, buildroot):
        self.buildroot = buildroot

        # Verify configuration
        requires = conf.get("requires")
        if not isinstance(requires, list) and len(requires):
            raise ConfigError(
                "The 'rpmautospec_opts.requires' key must be set to a non-empty list"
            )
        cmd_base = conf.get("cmd_base")
        if not isinstance(cmd_base, list) and len(cmd_base):
            raise ConfigError(
                "The 'rpmautospec_opts.cmd_base' key must be set to a non-empty list"
            )
        self.opts = conf

        self.log = getLog()
        plugins.add_hook("pre_srpm_build", self.attempt_process_distgit)
        self.log.info("rpmautospec: initialized")

    @contextmanager
    def as_root_user(self):
        try:
            self.buildroot.uid_manager.becomeUser(0, 0)
            yield
        finally:
            self.buildroot.uid_manager.restorePrivs()

    @traceLog()
    def attempt_process_distgit(
        self,
        host_chroot_spec: Union[Path, str],
        host_chroot_sources: Optional[Union[Path, str]],
    ) -> None:
        # Set up variables and check prerequisites.
        if not host_chroot_sources:
            self.log.debug("Sources not specified, skipping rpmautospec preprocessing.")
            return

        host_chroot_spec = Path(host_chroot_spec)
        host_chroot_sources = Path(host_chroot_sources)
        if not host_chroot_sources.is_dir():
            self.log.debug(
                "Sources not a directory, skipping rpmautospec preprocessing."
            )
            return

        distgit_git_dir = host_chroot_sources / ".git"
        if not distgit_git_dir.is_dir():
            self.log.debug(
                "Sources is not a git repository, skipping rpmautospec preprocessing."
            )
            return

        host_chroot_sources_spec = host_chroot_sources / host_chroot_spec.name
        if not host_chroot_sources_spec.is_file():
            self.log.debug(
                "Sources doesn’t contain spec file, skipping rpmautospec preprocessing."
            )
            return

        with host_chroot_spec.open("rb") as spec, host_chroot_sources_spec.open(
            "rb"
        ) as sources_spec:
            if spec.read() != sources_spec.read():
                self.log.warning(
                    "Spec file inside and outside sources are different, skipping rpmautospec"
                    " preprocessing."
                )
                return

        if not specfile_uses_rpmautospec(host_chroot_sources_spec):
            self.log_debug(
                "Spec file doesn’t use rpmautospec, skipping rpmautospec preprocessing."
            )
            return

        # Install the `rpmautospec` command line tool into the build root.
        if self.opts.get("requires", None):
            self.buildroot.pkg_manager.install_as_root(*self.opts["requires"], check=True)

        # Get paths inside the chroot by chopping off the leading paths
        chroot_dir = Path(self.buildroot.make_chroot_path())
        chroot_spec = Path("/") / host_chroot_spec.relative_to(chroot_dir)
        chroot_sources = Path("/") / host_chroot_sources.relative_to(chroot_dir)
        chroot_sources_spec = Path("/") / host_chroot_sources_spec.relative_to(chroot_dir)

        # Call subprocess to perform the specfile rewrite
        command = self.opts["cmd_base"]
        command += [chroot_sources_spec]  # <input-spec>
        command += [chroot_spec]  # <output-spec>

        self.buildroot.doChroot(
            command,
            shell=False,
            cwd=chroot_sources,
            logger=self.buildroot.build_log,
            uid=self.buildroot.chrootuid,
            gid=self.buildroot.chrootgid,
            user=self.buildroot.chrootuser,
            unshare_net=not self.config.get("rpmbuild_networking", False),
            nspawn_args=self.config.get("nspawn_args", []),
            printOutput=self.config.get("print_main_output", True),
        )
